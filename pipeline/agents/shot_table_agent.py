"""分镜 Agent · MVP · 启发式切段器。

切段策略（核心改动 · 修 4）：
1. paragraph → 多个 sub-shot（按句号/逗号断句 + 每段 ≤ MAX_SHOT_DUR 秒）
2. 同段内 sub-shots 表情轮转：从 library 的 top-K 候选里按 i % K 取
3. 背景轮转：sub-shots 之间也轮换，避免静态停顿

输入：full_script.paragraphs / style_bible / asset_library
输出：ShotTable（含 visual_form 推荐）

LLM 升级后替换此文件（保留函数签名）。
"""
from __future__ import annotations

import random
import re
from typing import Any

from pipeline import shot_table as _st
from pipeline.asset_library import AssetLibrary

# 单个 sub-shot 时长上限（秒）。超过自动切分到下一段。
# 上限调到 8s（≈44字），给"句末标点优先"装段策略留出 buffer：
# 字数到软阈值后，宁可多装几字等到句末再切，也不在逗号处截断。
MAX_SHOT_DUR = 8.0
MIN_SHOT_DUR = 2.0
PACE = 5.5  # 字/秒

# ── 装 shot 的标点感知阈值 ──
# 软阈值：到此字数即开始寻找"句末标点收尾"，命中就立刻切
# 硬阈值：超过则不管末尾是什么，强制切（防止超长 shot 把 cap_chars 撑爆）
# 之间的"等待区" = 给一两个 unit 的余地走到下一个 。！？
PACK_SOFT_CAP = 24      # ~4.4s
PACK_HARD_CAP = 40      # ~7.3s
PACK_SEMI_BONUS = 8     # 没等到 。！？，但 ; 也算次优收尾，这里允许的额外字数

# 标点权重分级（决定 shot 边界的合法性）
_PUNCT_SENT_END = "。！？!?"      # 一级停顿：句末
_PUNCT_SEMI = "；;"               # 二级停顿：分号
_PUNCT_COMMA = "，,"              # 三级停顿：逗号（最差选项）


# --------- 映射表 ---------

# paragraph.role 清洗到 ShotTable.SECTIONS 闭集
ROLE_TO_SECTION = {
    "钩子": "钩子", "开场": "钩子", "hook": "钩子",
    "共鸣": "共鸣", "案例": "共鸣", "场景": "共鸣",
    "原理": "知识点", "知识点": "知识点", "论证": "知识点",
    "反转": "知识点", "数据": "知识点", "风险": "知识点",
    "解释": "知识点", "机制": "知识点", "案例解析": "知识点",
    "金句": "金句", "结论": "金句", "点题": "金句",
    "CTA": "CTA", "cta": "CTA", "致谢": "CTA", "订阅": "CTA",
}

# section 默认 scene_tags — 多维语义，不锁死单一情绪
# 逻辑：同一个叙事段落在情绪上往往是复合的，多 tag 让 round-robin
# 能从多个情绪池里轮转选取，视觉多样性大幅提升。
SECTION_DEFAULT_TAGS = {
    "钩子":   ["震惊", "兴奋", "调侃"],   # 开场：惊喜/热闹/戏谑，调动注意力
    "共鸣":   ["困惑", "担忧", "崩溃"],   # 共鸣：疑惑/忧虑/崩了，拉近距离
    "知识点": ["沉思", "困惑", "得意"],   # 知识：思考→提问→揭晓，讲解三段式
    "金句":   ["得意", "兴奋", "平静"],   # 金句：自信/兴奋/沉稳，视场景而定
    "CTA":    ["平静", "期待", "得意"],   # CTA：收尾/期待互动/满足感
}

# 文本关键词 → 多维 scene_tags（与 ELEMENT_SCENARIO_MAP 同一思路）
#
# 设计原则：一个情境往往同时涵盖多种情绪维度。
# 例："爆仓"= 崩溃（核心）+ 震惊（意外）+ 担忧（余震）
#     "美联储加息"= 沉思（分析）+ 担忧（隐忧）
#     "赚钱"= 兴奋（喜悦）+ 得意（自信）
# 多 tag → search_expressions round-robin 覆盖更多情绪池 → 视觉更多样
KEYWORD_TO_TAGS: list[tuple[list[str], list[str]]] = [
    # ── 强惊喜/意外 ────────────────────────────────────────
    (["竟然", "想不到", "不是吧", "居然", "没想到", "史上最", "真的假的", "颠覆"],
     ["震惊", "兴奋"]),               # 意外 + 激动

    # ── 行情上涨 / 赚钱 ────────────────────────────────────
    (["涨", "翻倍", "暴涨", "牛市", "拉升", "涨停"],
     ["兴奋", "得意"]),               # 涨了→开心→自信

    (["赚", "盈利", "暴富", "数钱", "致富", "利润", "收益"],
     ["兴奋", "得意", "平静"]),       # 赚钱→兴奋→得意→从容

    # ── 行情下跌 / 亏损 ────────────────────────────────────
    (["跌", "暴跌", "崩盘", "熊市", "大跌", "阴跌"],
     ["担忧", "崩溃"]),               # 跌了→担心→崩溃

    (["亏", "亏损", "赔", "血亏", "套牢", "浮亏", "输"],
     ["崩溃", "担忧", "调侃"]),       # 亏了→崩→忧→自嘲

    # ── 爆仓 / 极端损失 ────────────────────────────────────
    (["爆仓", "强平", "清零", "割肉", "穿仓", "全没了", "崩了"],
     ["崩溃", "震惊", "担忧"]),       # 爆仓三连：崩溃+惊吓+余悸

    # ── 风险 / 危机 ────────────────────────────────────────
    (["风险", "危机", "暴雷", "警告", "陷阱", "踩雷", "爆雷", "崩"],
     ["担忧", "震惊"]),               # 风险→警觉+震惊

    # ── 宏观政策 / 央行 ────────────────────────────────────
    (["美联储", "央行", "加息", "降息", "印钞", "货币政策", "缩表", "利率"],
     ["沉思", "担忧"]),               # 宏观→分析+隐忧

    # ── 机会 / 期待 ────────────────────────────────────────
    (["抄底", "看涨", "布局", "入场", "机会", "买入", "准备好了", "等待", "加仓"],
     ["期待", "兴奋"]),               # 机会来了→期待+跃跃欲试

    # ── 散户 / 韭菜 ────────────────────────────────────────
    (["韭菜", "散户", "FOMO", "追涨", "杀跌", "跟风", "人性"],
     ["调侃", "崩溃"]),               # 韭菜→戏谑+认栽

    # ── 疑问 / 困惑 ────────────────────────────────────────
    (["为什么", "搞不懂", "难道", "怎么会", "谁能告诉我", "不理解", "真的吗"],
     ["困惑", "沉思"]),               # 问题→困惑+思考

    # ── 分析 / 揭秘 ────────────────────────────────────────
    (["分析", "逻辑", "原因", "其实", "背后", "规律", "本质", "真相"],
     ["沉思", "得意"]),               # 分析→思考→恍然大悟

    # ── 金句 / 技巧 ────────────────────────────────────────
    (["诀窍", "技巧", "这才是", "看吧", "说对了", "知道了吧", "没错吧", "记住"],
     ["得意", "平静"]),               # 金句→自信+沉稳

    # ── CTA / 总结 ─────────────────────────────────────────
    (["总结", "关注", "点赞", "记住", "方法就是", "最后", "今天分享"],
     ["平静", "期待"]),               # 收尾→从容+期待互动
]

# 兼容旧代码引用（shot_table_agent 内部已全面改用 KEYWORD_TO_TAGS）
KEYWORD_TO_TAG = [(kws, tags[0]) for kws, tags in KEYWORD_TO_TAGS]

# shot 文本关键词 → 元素 category 列表（多维语义，不锁死单一标签）
#
# 设计原则：
#   一个 shot 的语义可以同时命中多个 category。
#   例如"美联储加息"不只是"央行政策"，同时也是"数据图表"（走势/数字）
#   和"风险陷阱"（加息带来的压力）。多 category → 更大的元素池 → 更少重复。
#
#   排在前面的 category 优先级更高（_pick_element_pool 按命中数打分排序，
#   同时命中越多 category 的元素排越前，体现"最贴切"逻辑）。
#
# category 池大小参考（截至当前素材库）：
#   积极160  财富盈利99  调侃71  消极68  行情上涨61  风险陷阱59
#   紧张52   行情下跌43  数据图表40  散户心理35  财富亏损19
#   爆仓清盘8  做多入场6  央行政策5  存款储蓄3  做空操作2
ELEMENT_SCENARIO_MAP: list[tuple[list[str], list[str]]] = [

    # ── 宏观政策 ──────────────────────────────────────────
    # "加息"="央行政策"是显性语义，但它同时意味着数据/图表（利率曲线）
    # 和风险（资产重估压力），后两者池子大得多
    (["美联储", "央行", "加息", "降息", "印钞", "货币政策", "缩表", "利率"],
     ["央行政策", "数据图表", "风险陷阱"]),

    # 通胀 = 购买力下降 + 财富亏损 + 风险，加数据图表体现量化感
    (["通胀", "购买力", "物价", "CPI", "货币贬值", "通缩"],
     ["财富亏损", "消极", "风险陷阱", "数据图表"]),

    # ── 行情走势 ──────────────────────────────────────────
    # 涨 = 行情上涨 + 财富盈利（赚钱了）+ 积极情绪
    (["涨", "飙升", "翻倍", "暴涨", "拉升", "涨停", "上涨", "牛市", "反弹"],
     ["行情上涨", "财富盈利", "积极"]),

    # 跌 = 行情下跌 + 消极 + 风险陷阱（跌代表风险）
    (["跌", "暴跌", "崩盘", "下跌", "熔断", "闪崩", "熊市", "大跌", "阴跌"],
     ["行情下跌", "消极", "风险陷阱"]),

    # ── 交易操作 ──────────────────────────────────────────
    # 做空 = 做空操作 + 行情下跌（看空代表预期下跌）+ 消极
    (["做空", "借券", "看跌", "沽空", "空头", "放空"],
     ["做空操作", "行情下跌", "消极"]),

    # 做多/买入 = 做多入场 + 行情上涨（看多代表预期上涨）+ 积极
    (["买入", "看涨", "做多", "抄底", "布局", "多头", "加仓", "入场"],
     ["做多入场", "行情上涨", "积极"]),

    # 爆仓 = 爆仓清盘（核心）+ 财富亏损 + 风险陷阱 + 紧张
    (["爆仓", "强平", "保证金", "穿仓", "清盘", "爆了", "爆仓了"],
     ["爆仓清盘", "财富亏损", "风险陷阱", "紧张"]),

    # ── 财富变动 ──────────────────────────────────────────
    # 赚钱 = 财富盈利（核心）+ 行情上涨（涨才赚）+ 积极
    (["赚", "盈利", "收益", "回报", "暴富", "数钱", "财富自由", "挣"],
     ["财富盈利", "行情上涨", "积极"]),

    # 亏损 = 财富亏损（核心）+ 行情下跌 + 消极 + 风险陷阱
    (["亏", "亏损", "赔", "血亏", "割肉", "套牢", "清零", "输", "浮亏"],
     ["财富亏损", "行情下跌", "消极", "风险陷阱"]),

    # 存款/储蓄 = 存款储蓄（核心）+ 财富盈利（有利息算盈利）+ 积极
    (["存款", "定存", "储蓄", "存钱", "利息收入", "年化", "理财"],
     ["存款储蓄", "财富盈利", "积极"]),

    # 借贷/成本 = 风险陷阱（负债是风险）+ 消极 + 紧张
    (["借款", "借贷", "债务", "杠杆", "利息成本", "融资成本"],
     ["风险陷阱", "消极", "紧张"]),

    # ── 风险叙事 ──────────────────────────────────────────
    # 风险/危机 = 风险陷阱（核心）+ 紧张 + 消极
    (["风险", "陷阱", "危机", "暴雷", "警告", "雷", "崩", "踩雷", "爆雷"],
     ["风险陷阱", "紧张", "消极"]),

    # ── 数据/分析 ──────────────────────────────────────────
    # 数据/图表 = 数据图表（核心）+ 积极（分析是正向的）
    (["K线", "图表", "走势", "统计", "胜率", "百分", "概率", "数据", "指标", "均线"],
     ["数据图表", "积极"]),

    # ── 散户心理 ──────────────────────────────────────────
    # 散户/韭菜 = 散户心理（核心）+ 调侃（戏谑口吻）+ 紧张（FOMO情绪）
    (["散户", "韭菜", "跟风", "FOMO", "追涨", "杀跌", "恐慌", "割", "人性"],
     ["散户心理", "调侃", "紧张"]),
]


def _search_expr_with_fallback(
    tags: list[str],
    library: "AssetLibrary",
    character: str | None,
) -> list:
    """带情绪近邻链的表情搜索 · 主动扩池。
    主路径：主 tags 池 ∪ 近邻 tags 池（合并去重）→ 池大幅扩宽，避免反复撞车
    兜底：角色内任何带情绪标注的 / 角色全集
    """
    # 1. 主 tag 精确搜
    main_pool = library.search_expressions(tags, character=character, only_reviewed=False)

    # 2. 主动展开近邻（即使主池非空也合并，扩宽候选）
    expanded: list[str] = []
    seen: set[str] = set(tags)
    for t in tags:
        for nb in EMOTION_PROXIMITY.get(t, []):
            if nb not in seen:
                expanded.append(nb)
                seen.add(nb)
    nb_pool = library.search_expressions(expanded, character=character, only_reviewed=False) if expanded else []

    # 合并主池 + 近邻池（保序去重，主池靠前 → 语义优先级保持）
    if main_pool or nb_pool:
        merged: list = []
        seen_ids: set[str] = set()
        for a in list(main_pool) + list(nb_pool):
            if a.id in seen_ids:
                continue
            seen_ids.add(a.id)
            merged.append(a)
        return merged

    # 3. 角色内有情绪标注的表情（兜底但情绪不乱）
    if character:
        with_tags = [
            a for a in library.all("expressions")
            if a.meta.get("character") == character
            and any(t in _st.SCENE_TAGS for t in (a.meta.get("scene_tags") or []))
        ]
        if with_tags:
            return with_tags

    # 4. 最终兜底：角色全集（宁可没情绪也要有表情，总比空槽好）
    if character:
        return [a for a in library.all("expressions") if a.meta.get("character") == character]

    return library.search_expressions(tags, only_reviewed=False)


def _pick_least_used(
    pool: list,
    usage: dict[str, int],
    recent: list[str] | None = None,
) -> str:
    """从候选池里选使用次数最少的表情 id，并记录一次用量。

    规则（优先级递减）：
    1. 跳过最近 recent 窗口内已用过的（避免连续重复）
    2. 在剩余候选里选 usage 最少的
    3. 若全部都在 recent 里（池极小），退化到不限 recent，仍取 usage 最少的
    同使用次数时取原始排名靠前的（pool 已按语义打过分）。
    """
    recent_set = set(recent or [])
    # 先从 recent 之外选
    outside = [a for a in pool if a.id not in recent_set]
    candidates = outside if outside else pool  # 池太小就不管 recent
    # 找 usage 最小值，再在所有并列最小的里随机选一个 → 打散同分头部偏置
    min_u = min(usage.get(a.id, 0) for a in candidates)
    tied = [a for a in candidates if usage.get(a.id, 0) == min_u]
    best = random.choice(tied)
    usage[best.id] = usage.get(best.id, 0) + 1
    return best.id


def _pick_element_pool(text: str, library: "AssetLibrary", k: int = 4) -> list:
    """按 shot 文本命中 scenario_tags，返回 ranked 候选 (top-k)。

    优先路径：shot文本 → ELEMENT_SCENARIO_MAP → meta.scenario_tags 精确匹配
    降级路径：scenario_tags 为空的元素，回退到旧英文关键词模糊匹配（过渡期兼容）
    """
    # 1. 从 shot 文本推出目标 scenario_tags
    target_tags: list[str] = []
    for cn_list, tag_list in ELEMENT_SCENARIO_MAP:
        if any(cn in text for cn in cn_list):
            target_tags.extend(tag_list)
    target_tags = list(dict.fromkeys(target_tags))  # 去重保序

    # 2. 优先：精确匹配 meta.scenario_tags（新标签体系）
    scored: list[tuple[int, "object"]] = []
    fallback_pool: list["object"] = []

    for a in library.all("elements"):
        meta = a.meta or {}
        stags = set(meta.get("categories") or [])
        if not stags:
            # 尚未打 scenario_tags 的素材，保留给降级路径
            fallback_pool.append(a)
            continue
        hits = sum(1 for t in target_tags if t in stags)
        if hits > 0:
            scored.append((hits, a))

    scored.sort(key=lambda x: -x[0])
    result = [a for _, a in scored[:k]]

    # 2b. 关键词未命中时（target_tags 为空）→ 从所有元素里随机兜底，保证元素不空
    if not target_tags:
        all_elems = library.all("elements")
        if all_elems:
            # 优先返回有 categories 标注的元素（质量更高），不足再补无标注
            tagged = [a for a in all_elems if (a.meta or {}).get("categories")]
            untagged = [a for a in all_elems if not (a.meta or {}).get("categories")]
            pool_fallback = (tagged + untagged)[:k]
            return pool_fallback

    # 3. 降级：新标签结果不足时，用英文关键词在无标签素材里补充
    if len(result) < k and fallback_pool and target_tags:
        # 把 scenario_tags 中文转回英文关键词做模糊匹配（过渡期 mapping）
        _TAG_TO_EN: dict[str, list[str]] = {
            "行情上涨": ["bull", "rise", "up", "rocket", "profit"],
            "行情下跌": ["bear", "down", "fall", "crash", "loss"],
            "做空操作": ["bear", "short", "down", "fall"],
            "做多入场": ["bull", "up", "buy", "rise"],
            "爆仓清盘": ["explosion", "bomb", "warning", "danger"],
            "财富盈利": ["money", "cash", "rich", "gold", "profit"],
            "财富亏损": ["loss", "cry", "poor", "broke"],
            "存款储蓄": ["piggy", "bank", "save", "money"],
            "借贷成本": ["debt", "interest", "loan", "cost"],
            "央行政策": ["bank", "dollar", "money", "government"],
            "通胀购买力": ["money", "price", "inflation"],
            "风险陷阱": ["warning", "danger", "bomb", "alert"],
            "数据图表": ["chart", "graph", "candle", "stock"],
            "散户心理": ["crowd", "panic", "people", "run"],
            "积极": ["happy", "win", "rich", "gold"],
            "消极": ["sad", "loss", "cry", "fall"],
            "紧张": ["warning", "danger", "bomb"],
            "调侃": ["funny", "cute", "cartoon"],
        }
        en_kws: list[str] = []
        for t in target_tags:
            en_kws.extend(_TAG_TO_EN.get(t, []))
        en_kws = list(dict.fromkeys(en_kws))

        fb_scored: list[tuple[int, "object"]] = []
        existing_ids = {a.id for a in result}
        for a in fallback_pool:
            if a.id in existing_ids:
                continue
            meta = a.meta or {}
            blob = " ".join([
                (meta.get("keyword") or "").lower(),
                (a.description or "").lower(),
                " ".join(a.tags or []).lower(),
            ])
            hits = sum(1 for kw in en_kws if kw in blob)
            if hits > 0:
                fb_scored.append((hits, a))
        fb_scored.sort(key=lambda x: -x[0])
        result += [a for _, a in fb_scored[: k - len(result)]]

    return result[:k]

# ---------- 情绪近邻表 ----------
# 主 tag 在角色库里找不到时，按这个顺序尝试语义相近的 tag，
# 保证 fallback 选的表情和内容情绪沾边，绝不随机乱选。
# 近邻映射 — 10个新标签互为近邻，同时保留旧标签作为入口（兼容历史数据）
EMOTION_PROXIMITY: dict[str, list[str]] = {
    # === 10个主标签的近邻 ===
    "震惊": ["担忧", "困惑", "兴奋"],
    "兴奋": ["得意", "期待", "调侃"],
    "担忧": ["崩溃", "困惑", "沉思"],
    "崩溃": ["担忧", "调侃", "震惊"],
    "期待": ["兴奋", "得意", "平静"],
    "调侃": ["平静", "兴奋", "得意"],
    "困惑": ["沉思", "担忧", "平静"],
    "沉思": ["困惑", "平静", "得意"],
    "得意": ["兴奋", "调侃", "期待"],
    "平静": ["沉思", "调侃", "期待"],
    # === 旧标签兼容 → 映射到新标签 ===
    "失落":      ["崩溃", "担忧"],
    "委屈":      ["崩溃", "担忧"],
    "哭泣":      ["崩溃", "担忧"],
    "后悔":      ["崩溃", "担忧"],
    "心累":      ["崩溃", "担忧"],
    "无奈":      ["调侃", "平静"],
    "焦虑":      ["担忧", "震惊"],
    "恐慌":      ["担忧", "震惊"],
    "害怕":      ["担忧", "震惊"],
    "焦急":      ["担忧", "震惊"],
    "颤抖":      ["担忧", "崩溃"],
    "愤怒":      ["崩溃", "调侃"],
    "暴怒":      ["崩溃", "调侃"],
    "不耐烦":    ["调侃", "崩溃"],
    "翻脸":      ["崩溃", "调侃"],
    "无语":      ["调侃", "平静"],
    "摊手":      ["调侃", "困惑"],
    "嫌弃":      ["调侃", "崩溃"],
    "满足":      ["得意", "兴奋"],
    "自信":      ["得意", "兴奋"],
    "大笑":      ["兴奋", "调侃"],
    "庆祝":      ["兴奋", "得意"],
    "心动":      ["期待", "兴奋"],
    "严肃":      ["沉思", "平静"],
    "怀疑":      ["困惑", "沉思"],
    "犹豫":      ["困惑", "沉思"],
    "讲解":      ["沉思", "得意"],
    "醒悟":      ["沉思", "震惊"],
    # 金融专属旧标签
    "看涨大笑":  ["兴奋", "得意"],
    "看跌哭脸":  ["崩溃", "担忧"],
    "暴亏崩溃":  ["崩溃", "担忧"],
    "暴富狂喜":  ["兴奋", "得意"],
    "韭菜哭":    ["崩溃", "调侃"],
    "FOMO 焦躁": ["担忧", "调侃"],
    "抄底窃喜":  ["期待", "得意"],
    "套牢苦":    ["崩溃", "担忧"],
    "割肉痛":    ["崩溃", "担忧"],
    "数钱满足":  ["得意", "兴奋"],
}

# 文本触发词 → visual_form 推荐
AI_IMAGE_TRIGGERS = [
    "价格牌", "K 线", "K线", "走势图", "报表", "屏幕", "界面", "图表",
    "柱状图", "饼图", "曲线", "数字", "百分比", "票据", "账单", "文件",
]
AI_VIDEO_TRIGGERS = [
    "动画", "流程演示", "流水", "穿越", "冲击", "爆炸", "翻转",
]


def _infer_section(role: str) -> str:
    role = (role or "").strip()
    return ROLE_TO_SECTION.get(role, "知识点")


def _infer_scene_tags(text: str, section: str) -> list[str]:
    """推断 shot 文本对应的情绪 tag 列表（多维）。

    改版逻辑：
    1. section 默认已是多 tag（钩子→震惊/兴奋/调侃，知识点→沉思/困惑/得意 …）
    2. KEYWORD_TO_TAGS 也是多 tag，命中关键词后把整组 tags 合并进来
    3. 去重保序，上限 5 个（让 round-robin 覆盖更多情绪池，视觉更多样）
    """
    seen: set[str] = set()
    result: list[str] = []

    def _add(ts: list[str]) -> None:
        for t in ts:
            if t not in seen:
                seen.add(t)
                result.append(t)

    # 1. section 默认（多维）
    _add(SECTION_DEFAULT_TAGS.get(section, ["平静", "沉思"]))

    # 2. 关键词命中 → 追加整组 tags
    for kws, tags in KEYWORD_TO_TAGS:
        if len(result) >= 5:
            break
        if any(kw in text for kw in kws):
            _add(tags)

    return result[:5]


def _infer_visual_form(text: str, has_expression_hit: bool) -> str:
    if any(t in text for t in AI_VIDEO_TRIGGERS):
        return "ai_video"
    if any(t in text for t in AI_IMAGE_TRIGGERS):
        return "ai_image"
    return "stock"


def _infer_element_intent(text: str) -> str:
    """从文案里抽一句作为 L3 MISSING 的描述意图 · 限 40 字。"""
    t = text.strip().rstrip("。.!！？?").replace("\n", " ")
    return (t[:40] + ("…" if len(t) > 40 else ""))


# --------- 句子切分 ---------

# 按强标点（。！？）切句；保留逗号作为弱断点供二次拆
_STRONG_PUNCT = "。！？!?"
_WEAK_PUNCT = "，、；,;"


def _split_sentences(text: str) -> list[str]:
    """把一段文本切成短句列表，保留标点。"""
    text = text.strip()
    if not text:
        return []
    # 强切：在 [。！？] 后断
    parts = re.split(rf"(?<=[{_STRONG_PUNCT}])\s*", text)
    out = [p.strip() for p in parts if p.strip()]
    # 二次：单句字数太多（> MAX_SHOT_DUR * PACE）就在逗号处再切
    final: list[str] = []
    char_cap = int(MAX_SHOT_DUR * PACE)  # 33 字
    for s in out:
        if len(s) <= char_cap:
            final.append(s)
            continue
        # 在逗号处切
        sub = re.split(rf"(?<=[{_WEAK_PUNCT}])\s*", s)
        sub = [x.strip() for x in sub if x.strip()]
        # 若拆出的零碎过短（<6 字），合回
        merged: list[str] = []
        for piece in sub:
            if merged and len(merged[-1]) + len(piece) <= char_cap and len(merged[-1]) < 12:
                merged[-1] = merged[-1] + piece
            else:
                merged.append(piece)
        final.extend(merged)
    return final


def _pack_sentences_into_shots(sentences: list[str]) -> list[str]:
    """把短句贪心装到 shot：累加直到时长 > MAX_SHOT_DUR 就开新 shot。
    确保每条 shot 字数足够撑到 MIN_SHOT_DUR。
    """
    cap_chars = int(MAX_SHOT_DUR * PACE)   # 33
    min_chars = int(MIN_SHOT_DUR * PACE)   # 11
    shots: list[str] = []
    cur = ""
    for s in sentences:
        # 单句已超 cap → 强行作为一个 shot（已在 _split_sentences 里二次拆过，这里基本不会触发）
        if len(s) >= cap_chars:
            if cur:
                shots.append(cur); cur = ""
            shots.append(s)
            continue
        # 装入当前 shot
        if not cur:
            cur = s
        elif len(cur) + len(s) <= cap_chars:
            cur = cur + s
        else:
            shots.append(cur)
            cur = s
    if cur:
        # 末尾若太短，并入上一段
        if shots and len(cur) < min_chars and len(shots[-1]) + len(cur) <= cap_chars + 6:
            shots[-1] = shots[-1] + cur
        else:
            shots.append(cur)
    return shots


def _ends_with_any(s: str, charset: str) -> bool:
    return bool(s) and s[-1] in charset


def _pack_units_with_tracking(units: list[str]) -> list[tuple[str, list[str]]]:
    """装 shot 的核心算法 · 标点感知版（治"配音不自然"第一层）。

    策略（关键改动）：
    - 旧版：纯字数贪心，到 cap=33 就强制切 → shot 末尾标点几乎随机，
            导致跨 mp3 拼接时"逗号尾"被拉成"假长停顿"。
    - 新版：分级阈值 + 标点权重
        * 字数 < SOFT_CAP(24)：无脑装
        * 字数 ∈ [SOFT_CAP, HARD_CAP]：遇到 。！？ 立即切；
                                      末段允许 ; 收尾（次优）
        * 字数 ≥ HARD_CAP(40)：不管末尾是什么强制切（兜底）
    - 后果：≥90% 的 shot 末尾是句末标点 → TTS 收尾自然 →
            跨 shot mp3 拼接时不再出现"该停的不停、不该停的停"

    返回 [(packed_text, [unit1, unit2, ...]), ...]
    """
    min_chars = int(MIN_SHOT_DUR * PACE)   # 11
    shots: list[tuple[str, list[str]]] = []
    cur_text = ""
    cur_units: list[str] = []

    def _flush() -> None:
        nonlocal cur_text, cur_units
        if cur_text:
            shots.append((cur_text, cur_units))
            cur_text = ""
            cur_units = []

    for u in units:
        # 单个 unit 自身就超 hard_cap：单独成 shot（不破坏字幕单元完整性）
        if len(u) >= PACK_HARD_CAP:
            _flush()
            shots.append((u, [u]))
            continue

        # 装入
        if not cur_text:
            cur_text = u
            cur_units = [u]
        else:
            cur_text += u
            cur_units.append(u)

        cur_len = len(cur_text)
        ends_sent = _ends_with_any(cur_text, _PUNCT_SENT_END)
        ends_semi = _ends_with_any(cur_text, _PUNCT_SEMI)

        # 决策：此刻该不该切？
        if cur_len >= PACK_HARD_CAP:
            # 超硬上限，不管末尾是什么强制切
            _flush()
        elif cur_len >= PACK_SOFT_CAP and ends_sent:
            # 进入软区且末尾是 。！？ —— 黄金切点
            _flush()
        elif cur_len >= PACK_SOFT_CAP + PACK_SEMI_BONUS and ends_semi:
            # 软区 + 额外 buffer 还没等到句末，分号也接受
            _flush()
        # else：还没到软区，或在软区但末尾是逗号 → 继续装等下一个 unit

    # 末尾兜底：太短的尾段并入上一 shot；否则直接成段
    if cur_text:
        if (
            shots
            and len(cur_text) < min_chars
            and len(shots[-1][0]) + len(cur_text) <= PACK_HARD_CAP + 6
        ):
            prev_text, prev_units = shots[-1]
            shots[-1] = (prev_text + cur_text, prev_units + cur_units)
        else:
            shots.append((cur_text, cur_units))

    return shots


# --------- 主入口 ---------

def generate_shot_table(
    video_id: str,
    title: str,
    topic: str | None,
    full_script: dict[str, Any],
    bible: dict[str, Any],
    library: AssetLibrary,
) -> _st.ShotTable:
    """把 full_script.paragraphs 切成 ShotTable。"""
    paragraphs = full_script.get("paragraphs") or []
    if not paragraphs:
        raise ValueError("full_script.paragraphs 为空 · 先到文案页生成完整文稿")

    ip_char = None
    raw_ip_id = (bible.get("ip_library_id") or "").strip()
    ip_libs = (library.meta or {}).get("ip_libraries", {})
    # 1. 直接 key 命中
    ip_lib_meta = ip_libs.get(raw_ip_id)
    if ip_lib_meta:
        ip_char = ip_lib_meta.get("character_name")
    # 2. 没命中 → 反查：当作 character_name 直接用
    if not ip_char and raw_ip_id:
        # 检查是否是哪个库的 character_name
        for lid, lmeta in ip_libs.items():
            if lmeta.get("character_name") == raw_ip_id:
                ip_char = raw_ip_id
                break
        # 仍没命中 → 直接当 character 名用
        if not ip_char:
            ip_char = raw_ip_id

    # 整期视频只用一张背景 —— 由画风步骤的 bible.bg_id 锁定
    # bible 没选时 fallback 到第一张已审过的；分镜不再轮换、UI 不再让用户改
    backgrounds = library.approved("backgrounds") or library.all("backgrounds")
    locked_bg_id = (bible.get("bg_id") or "").strip()
    if not locked_bg_id and backgrounds:
        locked_bg_id = backgrounds[0].id

    shots: list[_st.Shot] = []
    sid_counter = 0
    # 跨 shot 全局使用计数——防止同一表情频繁出现
    expr_usage: dict[str, int] = {}
    elem_usage: dict[str, int] = {}
    # 近期使用窗口：记录最近 RECENT_WINDOW 个表情/元素 id，避免连续重复
    # 14 = 典型视频 15-20 shot 里，几乎全程都不让同一个素材复现
    RECENT_WINDOW = 14
    recent_exprs: list[str] = []
    recent_elems: list[str] = []
    # 布局均匀交替计数：每 LAYOUT_CYCLE 个 shot 中，最后 1 个放 element（left_right）
    # 其余用 center（只有表情），保证约 1:4 节奏（4 center → 1 element）
    # 改为 5 是因为：典型 topic 元素池只有 5-8 个，CYCLE=3 时一个 15-shot 视频
    # 需要 5 个 element slot，池耗尽后必然重复；CYCLE=5 降到 3 个 slot 更安全。
    # 布局节奏：每 ELEM_CYCLE 个 shot 里，ELEM_POSITIONS 位置用「表情+元素」左右布局
    # 其余位置用「表情居中」。目标 40% 有元素（2/5）。
    ELEM_CYCLE = 5
    ELEM_POSITIONS = {1, 3}   # 0-indexed，2/5 = 40%
    layout_counter = 0
    # 元素最多重复次数：池没耗尽前每个元素最多 1 次；池真耗尽再考虑放元素与否
    ELEM_MAX_REUSE = 1

    for para in paragraphs:
        section = _infer_section(para.get("role", ""))
        para_text = (para.get("text") or "").strip()
        if not para_text:
            continue

        # 1. 拆短句 → 装进 sub-shot，同时追踪每个 shot 的 subtitle_units
        llm_units = [u.strip() for u in (para.get("units") or []) if str(u).strip()]
        if llm_units:
            packed = _pack_units_with_tracking(llm_units)
        else:
            sentences = _split_sentences(para_text)
            packed = _pack_units_with_tracking(sentences)
        if not packed:
            packed = [(para_text, [])]
        sub_texts    = [t for t, _ in packed]
        sub_units_map = [us for _, us in packed]

        # 2. 段落级 scene_tags（用整段文本推断，所有 sub-shot 共享作 fallback）
        para_tags = _infer_scene_tags(para_text, section)

        for i, sub in enumerate(sub_texts):
            sid_counter += 1
            shot_id = f"S{sid_counter:02d}"

            # sub-shot 预估时长：纯按字数算，不再卡 MAX_SHOT_DUR 上限——
            # 标点感知装段后，shot 末尾必须收在句号，字数会自然落在 22-44 区间，
            # 估时跟着实际字数走才准（剪辑层最终用 ffprobe 真实音频，估时只是 UI 显示）
            dur = max(MIN_SHOT_DUR, len(sub) / PACE)

            # ---- 语境驱动表情匹配 ----
            # 1. 用 sub-shot 自身文本推 scene_tags（比整段更精确）
            sub_tags = _infer_scene_tags(sub, section)
            if not sub_tags:
                sub_tags = list(para_tags)

            # 2. 带情绪近邻链搜索表情池（绝不随机乱 fallback）
            expr_pool_shot = _search_expr_with_fallback(sub_tags, library, ip_char)

            # 3. 从池里选使用次数最少、且不在近期窗口内的表情（语义驱动）
            expr_id_1 = ""
            if expr_pool_shot:
                expr_id_1 = _pick_least_used(expr_pool_shot, expr_usage, recent_exprs)
                recent_exprs.append(expr_id_1)
                if len(recent_exprs) > RECENT_WINDOW:
                    recent_exprs.pop(0)

            # 4. 元素节奏：40% shot 用「表情+元素」左右布局，60% 用「表情居中」
            #    含数据关键词的 shot 强制触发元素（不受周期限制）
            ELEMENT_FORCE_KWS = ["数据", "图表", "K线", "走势", "%", "万", "亿", "倍", "胜率", "概率"]
            force_element = any(kw in sub for kw in ELEMENT_FORCE_KWS)
            want_element = force_element or ((layout_counter % ELEM_CYCLE) in ELEM_POSITIONS)
            layout_counter += 1

            # 5. 元素：只在 want_element 时才取；允许复用（ELEM_MAX_REUSE=3）
            elem_id_1 = ""
            if want_element:
                elem_pool_shot = _pick_element_pool(sub, library, k=12)
                if elem_pool_shot:
                    min_usage_in_pool = min(elem_usage.get(a.id, 0) for a in elem_pool_shot)
                    # force_element 不限复用；普通周期触发也放宽到 ELEM_MAX_REUSE
                    if not force_element and min_usage_in_pool >= ELEM_MAX_REUSE:
                        pass  # 只有全部元素超限时才跳过
                    else:
                        elem_id_1 = _pick_least_used(elem_pool_shot, elem_usage, recent_elems)
                        recent_elems.append(elem_id_1)
                        if len(recent_elems) > RECENT_WINDOW:
                            recent_elems.pop(0)

            # 背景：所有 shot 强制用 bible 锁定的同一张
            bg_id = locked_bg_id

            # 形态：基于 sub-shot 文本判断
            form = _infer_visual_form(sub, bool(expr_id_1))

            insets: list[_st.ElementInset] = []
            if form in ("ai_image", "ai_video"):
                insets.append(_st.ElementInset(
                    id=f"MISSING: {_infer_element_intent(sub)}",
                    frame_style="polaroid",
                    position="top_right",
                    enter_offset=0.3,
                ))

            # ---- beats 布局规则 ----
            # center     → 单表情居中（约 60% 的 shot）
            # left_right → 表情（左）+ 场景元素（右）（约 40%）
            beats: list[_st.Beat] = []
            if expr_id_1:
                if elem_id_1:
                    # 表情 + 场景元素：左右并列
                    beats = [_st.Beat(
                        duration_ratio=1.0, layout="left_right",
                        left=_st.BeatSlot(asset_id=expr_id_1, asset_kind="expression"),
                        right=_st.BeatSlot(asset_id=elem_id_1, asset_kind="element"),
                    )]
                else:
                    # 单表情居中
                    beats = [_st.Beat(
                        duration_ratio=1.0, layout="center",
                        left=_st.BeatSlot(asset_id=expr_id_1, asset_kind="expression"),
                        right=_st.BeatSlot(),
                    )]

            shots.append(_st.Shot(
                shot_id=shot_id,
                section=section,
                text=sub,
                estimated_duration=round(dur, 1),
                scene_tags=sub_tags,                # 用更精细的 sub_tags
                background_id=bg_id,
                expression_id=expr_id_1,            # 兼容字段
                beats=beats,                         # **主路径**
                element_insets=insets,
                visual_form=form,
                subtitle_units=sub_units_map[i],    # 字幕逐句单元
            ))

    total_dur = sum(s.estimated_duration for s in shots)
    return _st.ShotTable(
        video_id=video_id,
        title=title,
        topic=topic,
        total_duration_estimated=round(total_dur, 1),
        shots=shots,
        source_full_id=full_script.get("full_id"),
        source_outline_id=full_script.get("outline_id"),
    )


# --------- 形态切换工具 · 用户改 visual_form 时调整 element_insets ---------

def apply_visual_form(shot: _st.Shot, new_form: str) -> None:
    """把 shot 的 element_insets 调整到与 new_form 一致。不触动已生成的 image/video 产物。"""
    if new_form not in ("stock", "ai_image", "ai_video"):
        raise ValueError(f"unknown visual_form: {new_form}")
    shot.visual_form = new_form
    # 找到第一个 L3 inset（id 以 MISSING 开头 or 已有 image_url/video_url）
    l3_insets = [ins for ins in shot.element_insets
                 if ins.id.startswith("MISSING:") or ins.image_url or ins.video_url]
    if new_form == "stock":
        # 清掉未产出过的 MISSING；保留已产出的（历史结果 user 可能想留）
        shot.element_insets = [
            ins for ins in shot.element_insets
            if not (ins.id.startswith("MISSING:") and not ins.image_url and not ins.video_url)
        ]
    elif new_form in ("ai_image", "ai_video"):
        if not l3_insets:
            # 没有 L3 inset → 加一条 MISSING
            intent = _infer_element_intent(shot.text)
            shot.element_insets.append(_st.ElementInset(
                id=f"MISSING: {intent}",
                frame_style="polaroid",
                position="top_right",
                enter_offset=0.3,
            ))
