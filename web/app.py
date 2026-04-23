"""一二布布 · AI 视频工作台 · FastAPI 入口。

当前: 项目看板 + 6 个工作台板块 + 素材库，全 Mock 数据。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import sys as _sys
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

from pipeline import state as _state  # noqa: E402
from pipeline.agents import script_writer as _script_writer  # noqa: E402
from pipeline.agents import topic_generator as _topic_generator  # noqa: E402

_state.init_db()

BASE_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="一二布布 · AI 视频工作台", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ==================== 步骤元数据 ====================
STEPS = [
    {"key": "topic",  "label_short": "选题", "icon": "lightbulb"},
    {"key": "script", "label_short": "文案", "icon": "file-text"},
    {"key": "shots",  "label_short": "分镜", "icon": "layout-grid"},
    {"key": "assets", "label_short": "素材", "icon": "image"},
    {"key": "audio",  "label_short": "配音", "icon": "mic"},
    {"key": "edit",   "label_short": "剪辑", "icon": "film"},
]
STEP_KEYS = [s["key"] for s in STEPS]


def step_index_of(key: str) -> int:
    try:
        return STEP_KEYS.index(key)
    except ValueError:
        return -1


def next_step_of(key: str) -> str:
    i = step_index_of(key)
    if i < 0 or i >= len(STEP_KEYS) - 1:
        return "edit"
    return STEP_KEYS[i + 1]


# ==================== Mock 项目数据 ====================
MOCK_HERO_STATS = [
    {"label": "本月产出",  "value": "12",  "delta": "+3 vs 上月"},
    {"label": "进行中",    "value": "4",   "delta": None},
    {"label": "素材库",    "value": "127", "delta": "+8 本周"},
    {"label": "平均成本",  "value": "¥24", "delta": None},
]

MOCK_QUICK_ACTIONS = [
    {"title": "开始新选题", "desc": "输入关键词或粘贴灵感，AI 生成 20 个候选选题",
     "icon": "lightbulb", "bg": "#FFF8EC", "fg": "#D06E05", "href": "/project/V0001/topic"},
    {"title": "逛素材库", "desc": "查看背景 / 立绘 / 元素 / 表情视频四大仓库",
     "icon": "images", "bg": "#EEF4FF", "fg": "#1E40AF", "href": "/library"},
    {"title": "复盘数据", "desc": "从已发布作品的播放完播率里找下一条爆款",
     "icon": "bar-chart-3", "bg": "#ECFDF5", "fg": "#047857", "href": "#"},
]

MOCK_PROJECTS = [
    {"video_id": "V0001", "title": "做空是什么？3 分钟看懂熊市赚钱逻辑",
     "hook": "你知道股票跌了也能赚钱吗？", "category": "金融概念",
     "cover_emoji": "📉", "cover_bg": "linear-gradient(135deg, #F48A0C 0%, #D06E05 60%, #2E2C27 100%)",
     "duration": "01:30", "status_label": "分镜中", "status_badge_class": "badge-brand",
     "step_done": 2, "current_step_icon": "layout-grid",
     "current_step_label": "正在分镜 · 已拆 5 个镜头", "updated_ago": "12 分钟前",
     "next_step": "shots"},
    {"video_id": "V0002", "title": "通货膨胀：为什么你的钱越来越不值钱",
     "hook": "十年前一碗面 5 块，现在 20 块。", "category": "宏观经济",
     "cover_emoji": "🪙", "cover_bg": "linear-gradient(135deg, #1E40AF 0%, #1E3A8A 60%, #0D0D0B 100%)",
     "duration": "02:10", "status_label": "素材生产中", "status_badge_class": "badge-blue",
     "step_done": 3, "current_step_icon": "image",
     "current_step_label": "正在生图 · 2/6 完成", "updated_ago": "1 小时前",
     "next_step": "assets"},
    {"video_id": "V0003", "title": "复利的力量：爱因斯坦称它为世界第八大奇迹",
     "hook": "每天存 10 块，20 年能变多少？", "category": "投资入门",
     "cover_emoji": "🌱", "cover_bg": "linear-gradient(135deg, #047857 0%, #064E3B 60%, #0D0D0B 100%)",
     "duration": "01:45", "status_label": "待终审", "status_badge_class": "badge-green",
     "step_done": 5, "current_step_icon": "film",
     "current_step_label": "成片终审 · 等你确认", "updated_ago": "3 小时前",
     "next_step": "edit"},
    {"video_id": "V0004", "title": "房贷利率下调，现在是上车好时机吗？",
     "hook": "LPR 降了 25 个基点，每月省多少？", "category": "时事热点",
     "cover_emoji": "🏠", "cover_bg": "linear-gradient(135deg, #7C3AED 0%, #5B21B6 60%, #0D0D0B 100%)",
     "duration": "--", "status_label": "文案中", "status_badge_class": "badge-brand",
     "step_done": 1, "current_step_icon": "file-text",
     "current_step_label": "AI 正在撰写 3 版文案", "updated_ago": "刚刚",
     "next_step": "script"},
    {"video_id": "V0005", "title": "为什么 A 股是散户坟场？3 个结构性原因",
     "hook": "一个从未变过的残酷真相。", "category": "市场解读",
     "cover_emoji": "📊", "cover_bg": "linear-gradient(135deg, #DC2626 0%, #991B1B 60%, #0D0D0B 100%)",
     "duration": "02:30", "status_label": "草稿", "status_badge_class": "badge-gray",
     "step_done": 0, "current_step_icon": "lightbulb",
     "current_step_label": "刚建项目 · 待确认选题", "updated_ago": "昨天",
     "next_step": "topic"},
]


# ==================== 各板块 Mock 数据 ====================

# 选题 - 20 个候选
MOCK_TOPIC_POOL = [
    {"id": "T01", "title": "做空是什么？3 分钟看懂熊市赚钱逻辑",
     "category": "概念", "pain": 5, "surprise": 4, "hook": 5, "difficulty": 3, "score": 14,
     "tags": ["做空", "熊市", "金融工具"]},
    {"id": "T02", "title": "通货膨胀：为什么你的钱越来越不值钱",
     "category": "宏观", "pain": 5, "surprise": 3, "hook": 4, "difficulty": 2, "score": 14,
     "tags": ["通胀", "货币"]},
    {"id": "T03", "title": "复利的力量：每天存 10 块 20 年后能变多少",
     "category": "理财", "pain": 3, "surprise": 4, "hook": 5, "difficulty": 2, "score": 12,
     "tags": ["复利", "储蓄"]},
    {"id": "T04", "title": "房贷等额本息 vs 等额本金，谁更省钱",
     "category": "房贷", "pain": 5, "surprise": 4, "hook": 4, "difficulty": 4, "score": 13,
     "tags": ["房贷", "贷款"]},
    {"id": "T05", "title": "为什么 A 股散户 7 赔 2 平 1 赚",
     "category": "市场", "pain": 5, "surprise": 5, "hook": 5, "difficulty": 3, "score": 15,
     "tags": ["A股", "散户"]},
    {"id": "T06", "title": "定投基金真的稳赚不赔吗？一个反常识真相",
     "category": "基金", "pain": 4, "surprise": 5, "hook": 5, "difficulty": 3, "score": 14,
     "tags": ["基金", "定投"]},
    {"id": "T07", "title": "可转债打新：几乎零风险的套利机会",
     "category": "债券", "pain": 3, "surprise": 5, "hook": 5, "difficulty": 4, "score": 13,
     "tags": ["可转债", "打新"]},
    {"id": "T08", "title": "保险里的那些文字游戏你看懂了吗",
     "category": "保险", "pain": 5, "surprise": 4, "hook": 4, "difficulty": 3, "score": 13,
     "tags": ["保险", "条款"]},
    {"id": "T09", "title": "美联储加息为什么影响你的工资",
     "category": "宏观", "pain": 4, "surprise": 5, "hook": 4, "difficulty": 4, "score": 13,
     "tags": ["美联储", "加息"]},
    {"id": "T10", "title": "ETF 是什么？小白也能看懂的指数投资",
     "category": "基金", "pain": 4, "surprise": 3, "hook": 4, "difficulty": 2, "score": 11,
     "tags": ["ETF", "指数"]},
    {"id": "T11", "title": "黄金为什么是避险资产？古今逻辑不变",
     "category": "资产", "pain": 3, "surprise": 4, "hook": 4, "difficulty": 3, "score": 11,
     "tags": ["黄金", "避险"]},
    {"id": "T12", "title": "市盈率 PE 到底看哪个版本",
     "category": "指标", "pain": 4, "surprise": 3, "hook": 3, "difficulty": 4, "score": 10,
     "tags": ["PE", "估值"]},
]

# 文案 - 3 版
MOCK_SCRIPT_VERSIONS = [
    {"id": "v1", "style": "严肃科普",
     "tags": ["专业", "数据驱动", "中立"],
     "hook": "你知道股票跌了也能赚钱吗？这个操作叫「做空」。",
     "body": "做空的逻辑不复杂：向券商借入股票卖出，等股价下跌后再买回来还给券商，差价就是利润。2008 年次贷危机中，迈克尔·贝瑞靠做空 MBS 赚了超过 1 亿美元。但做空有三大风险：①股价理论上可以无限上涨，亏损无上限；②融券成本与利率挂钩；③监管层可能临时禁止做空。",
     "golden": "做空不是看空，是一种风险对冲工具。",
     "cta": "点关注，下期讲讲普通人如何做空 A 股。"},
    {"id": "v2", "style": "网感幽默",
     "tags": ["口语化", "段子感", "共情"],
     "hook": "股票跌的时候，你哭的死去活来，别人却在偷着乐？",
     "body": "对，他们在做空。说白了就是：从券商那借一堆股票，趁现在高价先卖掉，等跌了再买回来还给券商，差价白赚。就像你借邻居一件 2000 的羽绒服，拿去卖了 2000，等到夏天 500 收回来，1500 装进自己口袋。当然啊，万一股票不跌反涨，那你就要花更多钱买回去，亏得你妈都不认识你。",
     "golden": "牛市靠胆子，熊市靠脑子。",
     "cta": "下期讲普通人怎么做空，想看的扣 1。"},
    {"id": "v3", "style": "悬疑引钩",
     "tags": ["故事化", "反常识", "悬念"],
     "hook": "2008 年，有一个男人因为这个操作，净赚了 1 亿美元。",
     "body": "他叫迈克尔·贝瑞。在所有人都以为楼市会涨的时候，他做了一件反人类的事——押注美国房地产会崩盘。这个操作就是今天要说的「做空」。它的核心是「先卖后买」：向券商借股票卖掉，跌了之后再买回来还，差价归你。听起来很神奇？但这也是全世界监管最严的一个操作，因为稍有不慎就会血本无归。",
     "golden": "做空，是资本市场最锋利的一把双刃剑。",
     "cta": "关注我，下期聊聊 A 股做空的具体玩法。"},
]

# ============== 文案页 · 拓扑器渐进展开 ==============

# 第 1 步：3 个钩子方向（只给"点"，不给长文）
MOCK_HOOK_DIRECTIONS = [
    {
        "id": "h_counter", "emoji": "🎭", "label": "反常识提问",
        "color": "#DC2626", "bg": "#FEE2E2",
        "tagline": "用一个颠覆认知的问题开场",
        "preview": "你知道股票跌了也能赚钱吗？",
        "traits": ["立刻勾起好奇", "适合小白受众", "反转感强"],
        "match": 92,  # 和当前风格 DNA 的匹配度
    },
    {
        "id": "h_story", "emoji": "🎬", "label": "故事开场",
        "color": "#1E40AF", "bg": "#EEF4FF",
        "tagline": "从一个真实人物的戏剧性瞬间切入",
        "preview": "2008 年，有一个男人因为这个操作，净赚了 1 亿美元。",
        "traits": ["代入感强", "满足真实案例约束", "观众追完"],
        "match": 88,
    },
    {
        "id": "h_number", "emoji": "💥", "label": "数字震撼",
        "color": "#F48A0C", "bg": "#FFF8EC",
        "tagline": "用一个令人震惊的数字抓住眼球",
        "preview": "150 亿美金 —— 华尔街的大空头是怎么赚到的？",
        "traits": ["视觉冲击力", "金融题材友好", "精准定位"],
        "match": 75,
    },
]

# 第 2 步：3 条叙事骨架（基于选中钩子 h_counter 的分支）
MOCK_SKELETON_PATHS = [
    {
        "id": "sk_analogy", "emoji": "🔄", "label": "类比型",
        "color": "#047857", "bg": "#ECFDF5",
        "tagline": "用生活化类比讲金融原理",
        "nodes": [
            {"step": "A", "label": "钩子", "meta": "反常识提问"},
            {"step": "B", "label": "类比", "meta": "借球鞋卖掉再买回来"},
            {"step": "C", "label": "映射", "meta": "球鞋→股票，赚差价"},
            {"step": "D", "label": "风险", "meta": "鞋子涨价你要亏"},
            {"step": "E", "label": "金句", "meta": "..."},
            {"step": "F", "label": "CTA", "meta": "..."},
        ],
        "pros": "小白零门槛理解",
        "cons": "信息密度偏低",
    },
    {
        "id": "sk_story", "emoji": "📖", "label": "案例型",
        "color": "#7C3AED", "bg": "#F3E8FF",
        "tagline": "用贝瑞的真实故事贯穿",
        "nodes": [
            {"step": "A", "label": "钩子", "meta": "反常识提问"},
            {"step": "B", "label": "引入", "meta": "迈克尔·贝瑞 2008"},
            {"step": "C", "label": "操作", "meta": "做空次贷 MBS"},
            {"step": "D", "label": "结果", "meta": "净赚 1 亿美金"},
            {"step": "E", "label": "原理", "meta": "先借后卖再低价买"},
            {"step": "F", "label": "金句+CTA", "meta": "..."},
        ],
        "pros": "代入感强 · 满足真实人物约束",
        "cons": "需要更多铺垫",
    },
    {
        "id": "sk_logic", "emoji": "🧠", "label": "推理型",
        "color": "#0D0D0B", "bg": "#F4F3EE",
        "tagline": "从'为什么能赚'拆到'怎么做'",
        "nodes": [
            {"step": "A", "label": "钩子", "meta": "反常识提问"},
            {"step": "B", "label": "为什么", "meta": "金融市场允许借卖"},
            {"step": "C", "label": "机制", "meta": "融券/保证金/回补"},
            {"step": "D", "label": "怎么做", "meta": "普通散户的门路"},
            {"step": "E", "label": "风险", "meta": "亏损无上限"},
            {"step": "F", "label": "金句+CTA", "meta": "..."},
        ],
        "pros": "逻辑严密 · 专业感强",
        "cons": "对小白略烧脑",
    },
]

# 第 3 步：段落展开（基于选中 sk_analogy 类比型骨架）
# 每个节点只给 2 个变体，不堆量
MOCK_PARAGRAPH_NODES = [
    {
        "node_id": "N01", "step": "A", "label": "钩子",
        "locked": True,
        "selected_text": "你知道股票跌了也能赚钱吗？",
        "variants": [
            {"id": "v1", "text": "你知道股票跌了也能赚钱吗？", "tone": "提问", "len": 13, "active": True},
            {"id": "v2", "text": "股票跌到地板上，有人却赚到飞起。", "tone": "反差", "len": 15, "active": False},
        ],
    },
    {
        "node_id": "N02", "step": "B", "label": "类比-引入",
        "locked": False,
        "selected_text": "就像你借朋友一件限量球鞋——",
        "variants": [
            {"id": "v1", "text": "就像你借朋友一件限量球鞋——", "tone": "日常", "len": 13, "active": True},
            {"id": "v2", "text": "打个比方。你有个朋友，买了双限量联名。", "tone": "对话", "len": 18, "active": False},
        ],
    },
    {
        "node_id": "N03", "step": "C", "label": "类比-操作",
        "locked": False,
        "selected_text": "趁它热的时候，3000 借来卖了，等降价到 2000 再买回来还他，你净赚 1000。",
        "variants": [
            {"id": "v1", "text": "趁它热的时候，3000 借来卖了，等降价到 2000 再买回来还他，你净赚 1000。", "tone": "解释", "len": 38, "active": True},
            {"id": "v2", "text": "高价卖给球鞋贩子，低价再从二手市场收回来，中间差价揣兜里。", "tone": "口语", "len": 26, "active": False},
        ],
    },
    {
        "node_id": "N04", "step": "D", "label": "风险提醒",
        "locked": False,
        "selected_text": "但如果鞋价涨了呢？你要花更多钱买回去——这就是做空的致命风险。",
        "variants": [
            {"id": "v1", "text": "但如果鞋价涨了呢？你要花更多钱买回去——这就是做空的致命风险。", "tone": "警示", "len": 28, "active": True},
            {"id": "v2", "text": "当然，万一那双鞋断货炒到 5000，你得自己掏 2000 补差价。", "tone": "段子", "len": 25, "active": False},
        ],
    },
]

# 第 4 步：3 个金句候选
MOCK_GOLDEN_QUOTES = [
    {"id": "g1", "text": "牛市靠胆子，熊市靠脑子。", "vibe": "对仗工整", "len": 12, "match": 95},
    {"id": "g2", "text": "做空不是赌跌，是和市场下棋。", "vibe": "意境感", "len": 14, "match": 82},
    {"id": "g3", "text": "股票跌的时候，聪明钱已经在偷着乐。", "vibe": "冲击力", "len": 17, "match": 78},
]

# 第 5 步：3 个 CTA 候选
MOCK_CTA_OPTIONS = [
    {"id": "c1", "text": "点关注，下期讲普通人怎么做空 A 股。", "style": "下期预告", "match": 90},
    {"id": "c2", "text": "想看 A 股做空的具体玩法？扣 1。", "style": "互动扣字", "match": 85},
    {"id": "c3", "text": "金融冷知识每周更新，记得关注。", "style": "常规关注", "match": 70},
]

# 渐进式状态：用户当前走到哪一步
MOCK_SCRIPT_JOURNEY = {
    "current_stage": 3,      # 已经在段落展开阶段
    "total_stages": 5,
    "stages": [
        {"idx": 1, "key": "hook",     "label": "钩子方向", "icon": "sparkles",   "status": "done",    "selected": "反常识提问"},
        {"idx": 2, "key": "skeleton", "label": "叙事骨架", "icon": "git-branch", "status": "done",    "selected": "类比型"},
        {"idx": 3, "key": "paragraph","label": "段落展开", "icon": "file-text",  "status": "current", "selected": ""},
        {"idx": 4, "key": "golden",   "label": "金句",     "icon": "quote",      "status": "pending", "selected": ""},
        {"idx": 5, "key": "cta",      "label": "CTA",      "icon": "megaphone",  "status": "pending", "selected": ""},
    ],
}

# 最终合成预览（mock：假设用户都选完了）
MOCK_FINAL_PREVIEW = {
    "hook": "你知道股票跌了也能赚钱吗？",
    "body_segments": [
        "就像你借朋友一件限量球鞋——",
        "趁它热的时候，3000 借来卖了，等降价到 2000 再买回来还他，你净赚 1000。",
        "但如果鞋价涨了呢？你要花更多钱买回去——这就是做空的致命风险。",
    ],
    "golden": "牛市靠胆子，熊市靠脑子。",
    "cta": "点关注，下期讲普通人怎么做空 A 股。",
    "total_chars": 142,
    "estimated_duration": 58.0,
}


MOCK_SCRIPT_CHECKLIST = [
    {"id": 1, "label": "钩子吸引力", "desc": "前 3 秒能不能让自己想看下去", "ok": True},
    {"id": 2, "label": "术语通俗化", "desc": "有没有金融术语没解释", "ok": True},
    {"id": 3, "label": "知识点单一性", "desc": "是不是只讲 1 个知识点", "ok": True},
    {"id": 4, "label": "金句存在感", "desc": "有没有能被截图的金句", "ok": True},
    {"id": 5, "label": "事实准确性", "desc": "有没有金融常识错误", "ok": True},
    {"id": 6, "label": "合规安全", "desc": "有没有荐股/承诺收益", "ok": True},
    {"id": 7, "label": "朗读流畅度", "desc": "大声读一遍，卡不卡口", "ok": False},
]

# 本项目的素材来源配置 — 进入分镜前选好主 IP、启用哪些元素库
# 画风偏好不在这里 —— 已移到「素材」页做规范提示词 + 示例图确认 + 批量生成流程
MOCK_ASSET_CONFIG = {
    "primary_ip": {"id": "ip_buubuu", "name": "一二布布", "emoji": "🐰", "count": 48},
    "element_libs_enabled": ["finance_icons", "office_props", "life_scene", "ui_symbols"],
}

# 可选表情 IP 池（未来可扩展更多 IP）
MOCK_IP_OPTIONS = [
    {"id": "ip_buubuu", "name": "一二布布", "emoji": "🐰", "count": 48, "desc": "默认 IP · 通用财经/生活"},
    {"id": "ip_apple", "name": "苹果 Memoji", "emoji": "🍎", "count": 120, "desc": "iOS 风格 · 情绪丰富"},
    {"id": "ip_retro_cat", "name": "复古小猫", "emoji": "🐱", "count": 36, "desc": "像素怀旧风"},
    {"id": "ip_custom_01", "name": "我的自建 IP", "emoji": "✨", "count": 8, "desc": "自制 8 个 · 持续录入中"},
]

# 元素素材库（通用，按类别分组；每个库里可能同元素有多画风）
MOCK_ELEMENT_LIBS = [
    {"id": "finance_icons", "name": "金融图标", "emoji": "📊", "count": 32,
     "tags_sample": ["K线","箭头","数字牌","钱袋","牛熊","股票板"]},
    {"id": "office_props", "name": "办公道具", "emoji": "🏢", "count": 24,
     "tags_sample": ["便签","图钉","重点章","订阅钮","键盘"]},
    {"id": "life_scene", "name": "生活场景", "emoji": "🏠", "count": 68,
     "tags_sample": ["球鞋","咖啡","手机","购物车","汽车"]},
    {"id": "pets_anim", "name": "宠物动物", "emoji": "🐾", "count": 18,
     "tags_sample": ["狗","猫","鸟","兔子"]},
    {"id": "ui_symbols", "name": "UI 符号", "emoji": "🔔", "count": 16,
     "tags_sample": ["关注按钮","点赞","评论","分享"]},
]

# 分镜 - 7 个镜头
# 五段流水线: 文案 → 场景识别 → 表情库匹配(IP) → 元素库匹配 → 画框新生成(兜底)
MOCK_SHOT_TABLE = [
    {"id": "S01", "section": "钩子", "section_color": "#F48A0C",
     "time": "0.0 - 5.3s", "duration": 5.3,
     "text": "你知道股票跌了也能赚钱吗？",
     "detected_tags": [{"tag": "震惊", "intensity": 3, "reason": "反常识设问，制造悬念"}],
     "candidates": [
         {"id": "EXPR_SHOCK_01", "emoji": "😲", "desc": "布布惊讶捂嘴", "scene_tags": ["震惊"], "intensity": 3, "duration": 2.4, "selected": True},
         {"id": "EXPR_SHOCK_02", "emoji": "😮", "desc": "布布张嘴瞪眼", "scene_tags": ["震惊"], "intensity": 2, "duration": 2.0, "selected": False},
         {"id": "EXPR_STARTLED_01", "emoji": "🫨", "desc": "布布受惊摇头", "scene_tags": ["震惊", "害怕"], "intensity": 3, "duration": 2.6, "selected": False},
     ],
     "matched_elements": [],
     "custom_insets": [],
     "ready": True},
    {"id": "S02", "section": "共鸣", "section_color": "#1E40AF",
     "time": "5.3 - 18.9s", "duration": 13.6,
     "text": "去年大盘跌的时候，身边人都在哭，但有人反而赚了 30%。",
     "detected_tags": [
         {"tag": "担忧", "intensity": 2, "reason": "描述熊市亏损情绪"},
         {"tag": "失落", "intensity": 2, "reason": "身边人哭，情绪低落"},
     ],
     "candidates": [
         {"id": "EXPR_WORRIED_02", "emoji": "😟", "desc": "布布皱眉忧虑", "scene_tags": ["担忧", "失落"], "intensity": 2, "duration": 3.0, "selected": True},
         {"id": "EXPR_SAD_01", "emoji": "😔", "desc": "布布低头失落", "scene_tags": ["失落"], "intensity": 2, "duration": 2.8, "selected": False},
     ],
     "matched_elements": [
         {"keyword": "K 线下跌", "source_phrase": "大盘跌的时候", "lib": "金融图标",
          "at": 1.0, "duration": 3.0, "frame": "tv_screen", "pos": "top_right",
          "styles": [
              {"id": "EL_KLINE_DOWN_MBE", "emoji": "📉", "style": "MBE 扁平", "picked": True},
              {"id": "EL_KLINE_DOWN_PIXEL", "emoji": "📊", "style": "像素复古", "picked": False},
              {"id": "EL_KLINE_DOWN_HAND", "emoji": "📈", "style": "手绘插画", "picked": False},
          ]},
     ],
     "custom_insets": [],
     "ready": False},
    {"id": "S03", "section": "知识点", "section_color": "#047857",
     "time": "18.9 - 35.2s", "duration": 16.3,
     "text": "这件事叫\"做空\"。简单说：你先借来股票卖掉，跌了之后再买回来还。",
     "detected_tags": [{"tag": "讲解", "intensity": 2, "reason": "定义概念并拆步骤"}],
     "candidates": [
         {"id": "EXPR_EXPLAIN_01", "emoji": "🧑‍🏫", "desc": "布布指点讲解", "scene_tags": ["讲解"], "intensity": 2, "duration": 4.0, "selected": True},
         {"id": "EXPR_EXPLAIN_02", "emoji": "☝️", "desc": "布布食指上举", "scene_tags": ["讲解"], "intensity": 1, "duration": 3.2, "selected": False},
     ],
     "matched_elements": [
         {"keyword": "下跌箭头", "source_phrase": "跌了之后", "lib": "金融图标",
          "at": 3.6, "duration": 2.0, "frame": "sticky_note", "pos": "top_right",
          "styles": [
              {"id": "EL_ARROW_DOWN_MBE", "emoji": "⬇️", "style": "MBE 扁平", "picked": True},
              {"id": "EL_ARROW_DOWN_HAND", "emoji": "↘️", "style": "手绘插画", "picked": False},
          ]},
         {"keyword": "重点标签", "source_phrase": "再买回来还", "lib": "办公道具",
          "at": 9.9, "duration": 2.5, "frame": "no_frame", "pos": "bottom_left",
          "styles": [
              {"id": "EL_LABEL_IMPORTANT_MBE", "emoji": "⚠️", "style": "MBE 扁平", "picked": True},
          ]},
     ],
     "custom_insets": [],
     "ready": True},
    {"id": "S04", "section": "知识点", "section_color": "#047857",
     "time": "35.2 - 55.4s", "duration": 20.2,
     "text": "就像你借朋友一件限量球鞋，3000 卖掉，降价到 2000 再买回来还他，净赚 1000。",
     "detected_tags": [
         {"tag": "讲解", "intensity": 2, "reason": "打类比拆解过程"},
         {"tag": "得意", "intensity": 2, "reason": "净赚 1000 的愉悦"},
     ],
     "candidates": [
         {"id": "EXPR_PROUD_01", "emoji": "😏", "desc": "布布得意挑眉", "scene_tags": ["得意"], "intensity": 2, "duration": 2.8, "selected": True},
         {"id": "EXPR_EXPLAIN_01", "emoji": "🧑‍🏫", "desc": "布布指点讲解", "scene_tags": ["讲解"], "intensity": 2, "duration": 4.0, "selected": False},
     ],
     "matched_elements": [
         {"keyword": "球鞋", "source_phrase": "限量球鞋", "lib": "生活场景",
          "at": 3.3, "duration": 4.0, "frame": "polaroid", "pos": "top_right",
          "styles": [
              {"id": "EL_SNEAKER_MBE", "emoji": "👟", "style": "MBE 扁平", "picked": True},
              {"id": "EL_SNEAKER_PIXEL", "emoji": "🥿", "style": "像素复古", "picked": False},
          ]},
         {"keyword": "钱袋", "source_phrase": "净赚 1000", "lib": "金融图标",
          "at": 13.4, "duration": 3.0, "frame": "circle_window", "pos": "bottom_left",
          "styles": [
              {"id": "EL_MONEY_BAG_MBE", "emoji": "💰", "style": "MBE 扁平", "picked": True},
          ]},
     ],
     "custom_insets": [
         {"label": "价格牌 3000→2000", "emoji": "🏷️", "at": 7.5, "duration": 2.5, "frame": "polaroid", "pos": "top_right", "source_phrase": "3000 卖掉，降价到 2000", "reason": "元素库无「阶梯价格」现成款，需针对本视频生成", "status": "pending"},
     ],
     "ready": False},
    {"id": "S05", "section": "知识点", "section_color": "#047857",
     "time": "55.4 - 75.1s", "duration": 19.7,
     "text": "华尔街大空头迈克尔·贝瑞，2008 年做空次贷，一战赚了 150 亿美金。",
     "detected_tags": [{"tag": "严肃", "intensity": 2, "reason": "讲真实案例，语气沉稳"}],
     "candidates": [
         {"id": "EXPR_SERIOUS_01", "emoji": "😐", "desc": "布布严肃点头", "scene_tags": ["严肃"], "intensity": 2, "duration": 3.2, "selected": True},
     ],
     "matched_elements": [],
     "custom_insets": [
         {"label": "150 亿美金数字牌", "emoji": "💵", "at": 5.4, "duration": 3.5, "frame": "tv_screen", "pos": "top_right", "source_phrase": "赚了 150 亿美金", "reason": "具体数字 + 美金标识，元素库无现成组合", "status": "generating"},
     ],
     "ready": False},
    {"id": "S06", "section": "金句", "section_color": "#DC2626",
     "time": "75.1 - 85.5s", "duration": 10.4,
     "text": "牛市赚钱靠勇气，熊市赚钱靠做空。",
     "detected_tags": [{"tag": "得意", "intensity": 3, "reason": "金句点题，自信收束"}],
     "candidates": [
         {"id": "EXPR_CONFIDENT_01", "emoji": "😎", "desc": "布布自信指镜头", "scene_tags": ["得意"], "intensity": 3, "duration": 2.6, "selected": True},
         {"id": "EXPR_PROUD_01", "emoji": "😏", "desc": "布布得意挑眉", "scene_tags": ["得意"], "intensity": 2, "duration": 2.8, "selected": False},
     ],
     "matched_elements": [
         {"keyword": "牛熊 icon", "source_phrase": "牛市 / 熊市", "lib": "金融图标",
          "at": 0.5, "duration": 4.5, "frame": "no_frame", "pos": "center",
          "styles": [
              {"id": "EL_BULL_BEAR_MBE", "emoji": "🐂", "style": "MBE 扁平", "picked": True},
              {"id": "EL_BULL_BEAR_HAND", "emoji": "🐻", "style": "手绘插画", "picked": False},
          ]},
     ],
     "custom_insets": [],
     "ready": True},
    {"id": "S07", "section": "CTA", "section_color": "#7C3AED",
     "time": "85.5 - 90.2s", "duration": 4.7,
     "text": "想听更多金融冷知识，点个关注。",
     "detected_tags": [{"tag": "兴奋", "intensity": 2, "reason": "召唤关注的号召"}],
     "candidates": [],
     "matched_elements": [
         {"keyword": "关注按钮", "source_phrase": "点个关注", "lib": "UI 符号",
          "at": 0.8, "duration": 3.0, "frame": "sticky_note", "pos": "center",
          "styles": [
              {"id": "EL_SUBSCRIBE_MBE", "emoji": "🔔", "style": "MBE 扁平", "picked": True},
              {"id": "EL_SUBSCRIBE_3D", "emoji": "📣", "style": "3D 卡通", "picked": False},
          ]},
     ],
     "custom_insets": [],
     "ready": False},
]

# 素材生产 - 三阶段数据
# 阶段 1: 画风校准（规范提示词 + 示例图预览确认）
# 阶段 2: 画框内元素批量生成（standard + scene prompt per inset）
# 阶段 3: 配音 TTS
MOCK_ASSETS = {
    # 阶段 1: 画风校准
    "style_calibration": {
        "standard_prompt": (
            "MBE 扁平插画风，粗黑描边(2-3px)，饱和暖色块，白底，16:9 中心构图，"
            "无阴影渐变，简洁几何图形，童趣可爱，与人物立绘同款画风"
        ),
        "negative_prompt": "真实照片, 3D渲染, 复杂纹理, 写实阴影, 水印文字",
        "style_hint": "MBE 扁平",       # 来自用户选择
        "samples": [
            # 用「规范提示词 + 默认测试题材」生成的示例图，给用户确认画风
            {"id": "SAMPLE_01", "prompt_extra": "一只咖啡杯", "emoji": "☕",
             "thumb_bg": "linear-gradient(135deg,#FDE68A,#F59E0B)", "status": "done", "picked": True},
            {"id": "SAMPLE_02", "prompt_extra": "一本翻开的书", "emoji": "📖",
             "thumb_bg": "linear-gradient(135deg,#E0F2FE,#0EA5E9)", "status": "done", "picked": False},
            {"id": "SAMPLE_03", "prompt_extra": "一把雨伞", "emoji": "☂️",
             "thumb_bg": "linear-gradient(135deg,#FCE7F3,#EC4899)", "status": "done", "picked": False},
        ],
        "confirmed": True,   # 用户已确认此画风 → 才能进入阶段 2 批量生成
    },
    # 阶段 2: 画框内元素批量生成（只生成 shot.custom_insets 里的，其余都从库里拉）
    "insets": [
        {"id": "I01", "shot": "S04", "label": "价格牌 3000→2000", "desc": "红色价格牌阶梯式下跌",
         "source_phrase": "3000 卖掉，降价到 2000",
         "scene_prompt": "一块木质复古价格牌，上面写着大字 3000 带删除线，旁边箭头指向新价 2000，红色强调",
         "frame": "polaroid", "status": "pending", "emoji": "🏷️",
         "thumb_bg": "linear-gradient(135deg,#FECACA,#DC2626)"},
        {"id": "I02", "shot": "S05", "label": "150 亿美金数字牌", "desc": "金色立体数字 $15B",
         "source_phrase": "赚了 150 亿美金",
         "scene_prompt": "巨幅金色数字 150 亿美金（$15B），金光闪耀，配美元符号装饰",
         "frame": "tv_screen", "status": "generating", "emoji": "💵",
         "thumb_bg": "linear-gradient(135deg,#FEF3C7,#F59E0B)"},
    ],
    # 阶段 1 依赖：只有全局背景缺件时才需要跑（通常 0 个）
    "backgrounds_missing": [],
    "expression_hits": {
        "matched": 6, "total": 7,
        "unmatched_shots": [{"shot": "S07", "scene_tags": ["兴奋"]}],
    },
    "element_hits": {
        "matched": 7, "total_needed": 9,   # 7 个命中元素库，2 个要生成 (= insets)
    },
    "audios": [
        {"id": "A01", "shot": "S01", "label": "V0001_S01.mp3", "text": "你知道股票跌了也能赚钱吗？", "status": "done", "duration": "5.3s"},
        {"id": "A02", "shot": "S02", "label": "V0001_S02.mp3", "text": "去年大盘跌的时候，身边人都在哭...", "status": "done", "duration": "13.6s"},
        {"id": "A03", "shot": "S03", "label": "V0001_S03.mp3", "text": "这件事叫\"做空\"。简单说...", "status": "done", "duration": "16.3s"},
        {"id": "A04", "shot": "S04", "label": "V0001_S04.mp3", "text": "就像你借朋友一件限量球鞋...", "status": "generating", "duration": "--"},
        {"id": "A05", "shot": "S05", "label": "V0001_S05.mp3", "text": "华尔街大空头迈克尔·贝瑞...", "status": "pending", "duration": "--"},
        {"id": "A06", "shot": "S06", "label": "V0001_S06.mp3", "text": "牛市赚钱靠勇气，熊市赚钱靠做空。", "status": "pending", "duration": "--"},
        {"id": "A07", "shot": "S07", "label": "V0001_S07.mp3", "text": "想听更多金融冷知识，点个关注。", "status": "pending", "duration": "--"},
    ]
}

# 配音&字幕
MOCK_SUBTITLES = [
    {"idx": 1, "start": "00:00:00,000", "end": "00:00:05,300", "text": "你知道股票跌了也能赚钱吗？"},
    {"idx": 2, "start": "00:00:05,300", "end": "00:00:12,100", "text": "去年大盘跌的时候，身边人都在哭，"},
    {"idx": 3, "start": "00:00:12,100", "end": "00:00:18,900", "text": "但有人反而赚了 30%。"},
    {"idx": 4, "start": "00:00:18,900", "end": "00:00:26,500", "text": "这件事叫\"做空\"。"},
    {"idx": 5, "start": "00:00:26,500", "end": "00:00:35,200", "text": "简单说：你先借来股票卖掉，跌了之后再买回来还。"},
    {"idx": 6, "start": "00:00:35,200", "end": "00:00:45,800", "text": "就像你借朋友一件限量球鞋，3000 卖掉，"},
    {"idx": 7, "start": "00:00:45,800", "end": "00:00:55,400", "text": "降价到 2000 再买回来还他，净赚 1000。"},
    {"idx": 8, "start": "00:00:55,400", "end": "00:01:05,000", "text": "华尔街大空头迈克尔·贝瑞，"},
    {"idx": 9, "start": "00:01:05,000", "end": "00:01:15,100", "text": "2008 年做空次贷，一战赚了 150 亿美金。"},
    {"idx": 10, "start": "00:01:15,100", "end": "00:01:25,500", "text": "牛市赚钱靠勇气，熊市赚钱靠做空。"},
    {"idx": 11, "start": "00:01:25,500", "end": "00:01:30,200", "text": "想听更多金融冷知识，点个关注。"},
]

# 素材库 - 演示
MOCK_LIBRARY = {
    "backgrounds": [
        {"id": "BG_OFFICE_01", "tags": ["办公室","书桌","白天"], "emoji": "💼", "bg": "linear-gradient(135deg,#FED7AA,#F48A0C)"},
        {"id": "BG_STOCK_SCREEN_01", "tags": ["股市","大屏","K线"], "emoji": "📈", "bg": "linear-gradient(135deg,#FECACA,#DC2626)"},
        {"id": "BG_BOARD_01", "tags": ["白板","教学"], "emoji": "📋", "bg": "linear-gradient(135deg,#E0F2FE,#0EA5E9)"},
        {"id": "BG_BANK_01", "tags": ["银行","大厅"], "emoji": "🏦", "bg": "linear-gradient(135deg,#D1FAE5,#047857)"},
        {"id": "BG_HOME_01", "tags": ["家","客厅"], "emoji": "🏠", "bg": "linear-gradient(135deg,#F3E8FF,#7C3AED)"},
        {"id": "BG_CITY_NIGHT_01", "tags": ["城市","夜景"], "emoji": "🌃", "bg": "linear-gradient(135deg,#1E3A8A,#0D0D0B)"},
        {"id": "BG_COFFEE_01", "tags": ["咖啡厅","轻松"], "emoji": "☕", "bg": "linear-gradient(135deg,#FDE68A,#92400E)"},
        {"id": "BG_GRADIENT_01", "tags": ["纯色","金句"], "emoji": "🎨", "bg": "linear-gradient(135deg,#F48A0C,#DC2626)"},
    ],
    "characters": [
        {"id": "CHAR_SURPRISED", "tags": ["惊讶","情绪"], "emoji": "😲", "bg": "linear-gradient(135deg,#FEF3C7,#F59E0B)"},
        {"id": "CHAR_THINKING", "tags": ["思考"], "emoji": "🤔", "bg": "linear-gradient(135deg,#DBEAFE,#3B82F6)"},
        {"id": "CHAR_EXPLAIN", "tags": ["讲解","标准"], "emoji": "🧑‍🏫", "bg": "linear-gradient(135deg,#D1FAE5,#059669)"},
        {"id": "CHAR_HAPPY", "tags": ["开心","得意"], "emoji": "😄", "bg": "linear-gradient(135deg,#FCE7F3,#EC4899)"},
        {"id": "CHAR_CONFIDENT", "tags": ["自信"], "emoji": "😎", "bg": "linear-gradient(135deg,#E0E7FF,#4F46E5)"},
        {"id": "CHAR_WAVE", "tags": ["挥手","告别"], "emoji": "👋", "bg": "linear-gradient(135deg,#FEF3C7,#D97706)"},
    ],
    "elements": [
        {"id": "EL_KLINE_DOWN", "tags": ["K线","红色"], "emoji": "📉", "bg": "#FEE2E2"},
        {"id": "EL_ARROW_DOWN", "tags": ["箭头"], "emoji": "⬇️", "bg": "#FEE2E2"},
        {"id": "EL_LABEL_IMPORTANT", "tags": ["标签","重点"], "emoji": "⚠️", "bg": "#FEF3C7"},
        {"id": "EL_MONEY_BAG", "tags": ["钱袋","收益"], "emoji": "💰", "bg": "#FEF3C7"},
        {"id": "EL_SHOE_ICON", "tags": ["球鞋","类比"], "emoji": "👟", "bg": "#E0E7FF"},
        {"id": "EL_SUBSCRIBE_BTN", "tags": ["订阅","CTA"], "emoji": "🔔", "bg": "#FEE2E2"},
        {"id": "EL_QUOTE_FRAME", "tags": ["金句框"], "emoji": "❝", "bg": "#FEF3C7"},
        {"id": "EL_NUMBER_150B", "tags": ["数字","150亿"], "emoji": "💵", "bg": "#D1FAE5"},
    ],
    "expressions": [
        {"id": "EXPR_SHOCK_01", "scene_tags": ["震惊"], "intensity": 3, "duration": 2.4, "character": "一二布布", "emoji": "😲", "bg": "linear-gradient(135deg,#FED7AA,#F48A0C)"},
        {"id": "EXPR_WORRIED_02", "scene_tags": ["担忧", "失落"], "intensity": 2, "duration": 3.0, "character": "一二布布", "emoji": "😟", "bg": "linear-gradient(135deg,#DBEAFE,#1E40AF)"},
        {"id": "EXPR_EXPLAIN_01", "scene_tags": ["讲解"], "intensity": 2, "duration": 4.0, "character": "一二布布", "emoji": "🧑‍🏫", "bg": "linear-gradient(135deg,#D1FAE5,#047857)"},
        {"id": "EXPR_PROUD_01", "scene_tags": ["得意"], "intensity": 2, "duration": 2.8, "character": "一二布布", "emoji": "😏", "bg": "linear-gradient(135deg,#FEF3C7,#F59E0B)"},
        {"id": "EXPR_SERIOUS_01", "scene_tags": ["严肃"], "intensity": 2, "duration": 3.2, "character": "一二布布", "emoji": "😐", "bg": "linear-gradient(135deg,#E5E7EB,#6B7280)"},
        {"id": "EXPR_CONFIDENT_01", "scene_tags": ["得意"], "intensity": 3, "duration": 2.6, "character": "一二布布", "emoji": "😎", "bg": "linear-gradient(135deg,#FEE2E2,#DC2626)"},
        {"id": "EXPR_CONFUSED_01", "scene_tags": ["困惑"], "intensity": 2, "duration": 2.5, "character": "一二布布", "emoji": "🤨", "bg": "linear-gradient(135deg,#F3E8FF,#7C3AED)"},
        {"id": "EXPR_THINKING_01", "scene_tags": ["沉思"], "intensity": 1, "duration": 3.5, "character": "一二布布", "emoji": "🤔", "bg": "linear-gradient(135deg,#E0F2FE,#0EA5E9)"},
        {"id": "EXPR_PANIC_01", "scene_tags": ["恐慌"], "intensity": 3, "duration": 2.0, "character": "一二布布", "emoji": "😱", "bg": "linear-gradient(135deg,#FEE2E2,#B91C1C)"},
        {"id": "EXPR_SPEECHLESS_01", "scene_tags": ["无语"], "intensity": 2, "duration": 2.4, "character": "一二布布", "emoji": "😑", "bg": "linear-gradient(135deg,#F1F5F9,#64748B)"},
    ],
    "scene_tags": ["震惊","困惑","沉思","讲解","严肃","失落","得意","恐慌","担忧","兴奋","无语","愤怒","调侃","害怕","平静"],
    "frame_styles": ["polaroid","tv_screen","sticky_note","circle_window","no_frame"],
}


# ==================== 通用上下文 ====================
def get_project(video_id: str) -> dict | None:
    """先取 mock 项目骨架，再用 DB 里最新的 title / topic 覆盖；用户在选题页 pick 后下游全部页面自动跟上。"""
    proj = next((p for p in MOCK_PROJECTS if p["video_id"] == video_id), None)
    if proj is None:
        return None
    proj = dict(proj)  # 拷一份避免改 mock
    db_video = _state.get_video(video_id)
    if db_video and db_video.title:
        proj["title"] = db_video.title
    picked = _state.load_project_topic(video_id)
    if picked and picked.get("title"):
        proj["title"] = picked["title"]
        proj["topic_id"] = picked.get("id")
        proj["topic_score"] = picked.get("score")
        proj["topic_category"] = picked.get("category")
    style = _state.load_project_style(video_id)
    if style:
        proj["style_preset"] = style
    return proj


def build_ctx(request: Request, active_nav: str, video_id: str | None = None, **extra) -> dict[str, Any]:
    ctx = {
        "request": request,
        "active_nav": active_nav,
        "steps": STEPS,
        "current_video": None,
        "current_step_index": -1,
    }
    if video_id:
        proj = get_project(video_id)
        if proj:
            ctx["current_video"] = proj
            ctx["current_step_index"] = proj["step_done"] - 1
    ctx.update(extra)
    return ctx


# ==================== 路由 ====================
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return TEMPLATES.TemplateResponse("projects.html", build_ctx(
        request, "projects",
        hero_stats=MOCK_HERO_STATS,
        quick_actions=MOCK_QUICK_ACTIONS,
        projects=MOCK_PROJECTS,
    ))


@app.get("/project/new")
async def project_new():
    return RedirectResponse(url="/project/V0001/topic", status_code=303)


@app.get("/project/{video_id}", response_class=HTMLResponse)
async def project_redirect(video_id: str):
    proj = get_project(video_id)
    if not proj:
        return RedirectResponse(url="/", status_code=303)
    return RedirectResponse(url=f"/project/{video_id}/{proj['next_step']}", status_code=303)


# ==================== ① 选题相关 Mock 数据 ====================

# 5 个风格预设
STYLE_PRESETS = [
    {"id": "serious", "emoji": "🎓", "name": "严肃科普",
     "desc": "央视纪录片风 · 准确严谨",
     "color": "#1E40AF", "bg": "#EEF4FF",
     "dims": {"formal": 85, "humor": 10, "pace": 30, "density": 75, "emotion": 25, "speak": 20},
     "sample": "根据国家统计局 2024 年数据，居民消费价格指数同比上涨 0.2%，这意味着……",
     "voice": "青年男声 · 沉稳",
     "visual": "冷色调 · 数据图表为主"},
    {"id": "humor", "emoji": "😎", "name": "幽默段子",
     "desc": "网感金融 · 自带段子",
     "color": "#F48A0C", "bg": "#FFF8EC",
     "dims": {"formal": 25, "humor": 90, "pace": 80, "density": 45, "emotion": 85, "speak": 90},
     "sample": "股票跌的时候，你哭的死去活来，别人却在偷着乐？",
     "voice": "青年男声 · 活泼",
     "visual": "暖色调 · 表情包+梗图"},
    {"id": "healing", "emoji": "🌿", "name": "治愈温柔",
     "desc": "陪伴式讲解 · 减焦虑",
     "color": "#047857", "bg": "#ECFDF5",
     "dims": {"formal": 45, "humor": 35, "pace": 35, "density": 40, "emotion": 70, "speak": 65},
     "sample": "别急，钱的事情慢慢来。今天我们只聊一件小事——为什么你的存款好像越来越不耐用？",
     "voice": "女声 · 温柔",
     "visual": "柔和渐变 · 手绘风"},
    {"id": "analyst", "emoji": "📊", "name": "华尔街派",
     "desc": "分析师视角 · 数据说话",
     "color": "#0D0D0B", "bg": "#F4F3EE",
     "dims": {"formal": 75, "humor": 20, "pace": 55, "density": 85, "emotion": 40, "speak": 35},
     "sample": "从 risk-reward ratio 的角度看，当前做空 TSLA 的赔率并不理想——原因有三个。",
     "voice": "青年男声 · 理性",
     "visual": "深色背景 · 金融图表"},
    {"id": "counter", "emoji": "🎭", "name": "反常识",
     "desc": "认知颠覆 · 钩子炸裂",
     "color": "#DC2626", "bg": "#FEE2E2",
     "dims": {"formal": 40, "humor": 55, "pace": 75, "density": 60, "emotion": 80, "speak": 70},
     "sample": "你以为你的钱是你的？错。银行存款的法律所有权其实属于银行。",
     "voice": "青年男声 · 悬疑",
     "visual": "高对比 · 戏剧化镜头"},
]

# 风格 DNA 的 6 个维度定义
STYLE_DIMS = [
    {"key": "formal",  "label": "人设口吻", "left": "幽默段子", "right": "严肃学术", "icon": "user-round"},
    {"key": "humor",   "label": "幽默感",   "left": "克制",     "right": "段子密集", "icon": "smile"},
    {"key": "pace",    "label": "节奏",     "left": "平稳",     "right": "炸裂快切", "icon": "zap"},
    {"key": "density", "label": "信息密度", "left": "浅科普",   "right": "烧脑深度", "icon": "brain"},
    {"key": "emotion", "label": "情绪强度", "left": "冷静",     "right": "激昂",     "icon": "flame"},
    {"key": "speak",   "label": "口语化",   "left": "书面",     "right": "大白话",   "icon": "mic"},
]

# Tab 2: 参考模仿 · 模拟粘贴了一个 B 站 URL 的解析结果
MOCK_REFERENCE = {
    "url": "https://www.bilibili.com/video/BV1xxx",
    "platform": "bilibili", "platform_label": "B 站",
    "title": "3分钟讲清做空，熊市也能赚大钱！",
    "author": "财经小白脱盲",
    "duration": "3:24",
    "views": "128.3 万",
    "likes": "4.2 万",
    "cover_emoji": "📈",
    "cover_bg": "linear-gradient(135deg,#DC2626,#0D0D0B)",
    "style_extracted": {
        "tone": "幽默口语",
        "hook_type": "反常识提问",
        "structure": "钩子→类比→案例→金句→CTA",
        "pace": "快节奏·短句",
        "density": "中等",
    },
    "imitation_topics": [
        {"title": "期权是什么？看懂之后你会觉得股票太弱了", "score": 15, "why": "继承原片'反常识认知'钩子"},
        {"title": "融资融券：散户的财富放大器还是绞肉机？", "score": 14, "why": "沿用'利弊双面'结构"},
        {"title": "量化交易为什么能稳赚？一个被误解的真相", "score": 14, "why": "保留'一个真相'标题句式"},
        {"title": "私募基金门槛 100 万，凭什么赚钱比公募多", "score": 13, "why": "沿用数字+反差的钩子"},
        {"title": "VIX 恐慌指数：华尔街的情绪温度计", "score": 12, "why": "学习'专业术语→生活化类比'"},
    ],
}

# Tab 3: 大纲导入 · 模拟用户粘贴了一段笔记
MOCK_OUTLINE = {
    "raw": """最近研究了一下期权。发现大家对期权有几个误解：
1. 期权不是赌博，是对冲工具
2. Delta/Gamma/Vega/Theta 四个希腊字母决定一切
3. 卖方赚 80% 的钱，但一旦崩就是灾难（那个交易员的故事）
4. 散户只玩看涨看跌期权，但其实可以做 covered call 稳赚
还有一些零散的点：时间价值衰减、隐含波动率、波动率微笑……""",
    "extracted_topics": [
        {"title": "期权的4个希腊字母，90% 的人只看懂一个", "from": "笔记第2点", "score": 14},
        {"title": "Covered Call 策略：散户忽视的稳赚方法", "from": "笔记第4点", "score": 13},
        {"title": "期权卖方真的稳赚不赔？那个赔掉 10 亿的交易员", "from": "笔记第3点", "score": 15},
        {"title": "为什么说期权是对冲工具而非赌博", "from": "笔记第1点", "score": 12},
        {"title": "时间价值衰减：期权买方的隐形杀手", "from": "笔记零散点", "score": 11},
    ],
}

# Tab 4: 系列规划 · 模拟规划了 10 集
MOCK_SERIES = {
    "name": "金融小白脱盲 10 讲",
    "subject": "金融入门科普",
    "total_episodes": 10,
    "target_duration": "2-3 分钟",
    "avg_difficulty": 2.6,
    "style_id": "humor",  # 继承风格
    "episodes": [
        {"ep": 1,  "title": "什么是股票？一张纸凭什么值钱",                  "difficulty": 1, "mood": "轻快", "status": "locked"},
        {"ep": 2,  "title": "做空是什么？3 分钟看懂熊市赚钱逻辑",            "difficulty": 2, "mood": "惊讶", "status": "locked"},
        {"ep": 3,  "title": "通货膨胀：为什么你的钱越来越不值钱",              "difficulty": 2, "mood": "焦虑", "status": "locked"},
        {"ep": 4,  "title": "复利的力量：每天存 10 块 20 年后能变多少",        "difficulty": 2, "mood": "惊喜", "status": "draft"},
        {"ep": 5,  "title": "基金 vs 股票：到底该买哪个",                    "difficulty": 3, "mood": "理性", "status": "draft"},
        {"ep": 6,  "title": "ETF 是什么？指数投资的正确打开方式",             "difficulty": 3, "mood": "轻快", "status": "draft"},
        {"ep": 7,  "title": "为什么 A 股散户 7 赔 2 平 1 赚",                "difficulty": 3, "mood": "沉重", "status": "draft"},
        {"ep": 8,  "title": "期权：被误解的金融工具",                        "difficulty": 4, "mood": "好奇", "status": "draft"},
        {"ep": 9,  "title": "期货与期权的区别，一张图讲清",                   "difficulty": 4, "mood": "专注", "status": "draft"},
        {"ep": 10, "title": "从小白到入门：如何搭建你的第一个投资组合",         "difficulty": 3, "mood": "激励", "status": "draft"},
    ],
    "coverage": [
        {"topic": "基础概念", "eps": [1, 3]},
        {"topic": "交易工具", "eps": [2, 5, 6, 8, 9]},
        {"topic": "市场现象", "eps": [7]},
        {"topic": "投资理财", "eps": [4, 10]},
    ],
}

# ============== 灵感 Lab 混搭模式 ==============

# 预置的 5 个灵感源（用户一进页面就看到有内容，便于演示）
MOCK_SOURCES = [
    {
        "sid": "src_kw",
        "kind": "keywords",
        "icon": "hash",
        "color": "#F48A0C", "bg": "#FFF8EC",
        "title": "关键词",
        "summary": "做空 · 熊市 · 金融工具",
        "tags": ["做空", "熊市", "金融工具"],
        "weight": 60,
        "extracted": ["卖空", "空头", "做空机构", "融券", "裸卖空", "VIX", "反指标"],
    },
    {
        "sid": "src_ref1",
        "kind": "reference",
        "icon": "link",
        "color": "#1E40AF", "bg": "#EEF4FF",
        "title": "参考视频 · B站",
        "summary": "3分钟讲清做空，熊市也能赚大钱！",
        "meta": {"platform": "B站", "author": "财经小白脱盲", "duration": "3:24", "views": "128.3万"},
        "weight": 80,
        "extracted": ["反常识提问钩子", "生活化类比", "快节奏短句", "5 段式结构", "段子感收尾"],
        # 真实处理管线 (4 步) — 暂用 mock 但结构完全对应未来真实 API
        "pipeline": [
            {"step": 1, "tool": "yt-dlp",    "label": "抓取元数据+下载视频", "icon": "cloud-download", "status": "done",       "detail": "下载 8.2MB · 原生字幕: 无", "pct": 100, "dur_ms": 3200},
            {"step": 2, "tool": "ffmpeg",    "label": "提取音轨",         "icon": "music-2",        "status": "done",       "detail": "输出 mp3 · 16kHz · 单声道",  "pct": 100, "dur_ms": 850},
            {"step": 3, "tool": "Whisper",   "label": "ASR 转录字幕",     "icon": "mic",            "status": "done",       "detail": "识别 826 字 · 置信度 94%",    "pct": 100, "dur_ms": 14200},
            {"step": 4, "tool": "DeepSeek",  "label": "结构化风格抽取",    "icon": "sparkles",       "status": "done",       "detail": "产出 6 个风格维度 JSON",       "pct": 100, "dur_ms": 2100},
        ],
        # 用户"继承哪些维度"的复选框
        "dimensions": [
            {"key": "hook_type",     "label": "钩子类型",   "value": "反常识提问",  "checked": True},
            {"key": "structure",     "label": "叙事结构",   "value": "钩→类→案→句→CTA", "checked": True},
            {"key": "pace",          "label": "节奏",       "value": "快节奏·短句", "checked": True},
            {"key": "topic",         "label": "题材话题",   "value": "做空/熊市",    "checked": False},
            {"key": "golden",        "label": "具体金句",   "value": "\"牛市靠胆熊市靠脑\"", "checked": False},
            {"key": "cover",         "label": "封面风格",   "value": "红色数字强调",  "checked": True},
        ],
    },
    {
        "sid": "src_note",
        "kind": "note",
        "icon": "file-text",
        "color": "#047857", "bg": "#ECFDF5",
        "title": "我的笔记 · 期权研究",
        "summary": "4 个希腊字母 / Covered Call / 卖方赔掉 10 亿...",
        "raw_excerpt": "期权不是赌博而是对冲工具。Delta/Gamma/Vega/Theta 四个希腊字母决定一切。卖方赚 80% 的钱...",
        "weight": 45,
        "extracted": ["希腊字母定价体系", "Covered Call 策略", "期权卖方风险", "时间价值衰减", "隐含波动率"],
    },
    {
        "sid": "src_prompt",
        "kind": "prompt",
        "icon": "sliders",
        "color": "#6B7280", "bg": "#F4F3EE",
        "title": "额外指令",
        "summary": "避免荐股用语 · 带一个真实人物故事",
        "raw": "避免任何可能被视为荐股的表述；每个选题如果涉及案例，必须带一个真实人物或机构的故事。",
        "weight": 70,
    },
]

# 灵感源类型的元数据（添加栏用） — 规划已提升到顶层模式切换，不再是 Source
SOURCE_TYPES = [
    {"kind": "keywords",  "icon": "hash",       "label": "关键词",   "desc": "3-5 个核心词",     "color": "#F48A0C"},
    {"kind": "reference", "icon": "link",       "label": "参考",    "desc": "URL / 账号 / 文稿", "color": "#1E40AF"},
    {"kind": "note",      "icon": "file-text",  "label": "笔记",    "desc": "粘贴你的大纲/想法", "color": "#047857"},
    {"kind": "prompt",    "icon": "sliders",    "label": "指令",    "desc": "附加 Prompt 约束",  "color": "#6B7280"},
]

# 创作模式切换（顶层）
CREATION_MODES = [
    {"key": "single",   "label": "单集创作",    "desc": "出一条视频",          "icon": "film",   "episodes": 1},
    {"key": "mini",     "label": "3 集系列",    "desc": "短系列·探索",         "icon": "layers-2", "episodes": 3},
    {"key": "series",   "label": "10 集系列",   "desc": "完整系列·课程感",     "icon": "layers-3", "episodes": 10},
]

# AI Fusion 的"识别与融合"可视化 (阶段 A：题材探索，还未锁风格)
MOCK_FUSION_INSIGHTS = {
    "recognized_intents": [
        {"icon": "📌", "label": "单集创作 · 90 秒左右的金融概念科普"},
        {"icon": "🎯", "label": "核心方向：做空 → 可延伸到期权、融券、做空机制等"},
        {"icon": "🎭", "label": "参考片提取维度：反常识钩子 / 结构 / 节奏（题材+金句未勾选）"},
        {"icon": "📚", "label": "笔记中的期权希腊字母有潜力做单独选题"},
        {"icon": "⚠️", "label": "硬约束：不荐股 · 必带真实案例"},
    ],
    "source_influence": [
        {"sid": "src_kw",     "name": "关键词",       "pct": 28, "color": "#F48A0C"},
        {"sid": "src_ref1",   "name": "参考视频",     "pct": 38, "color": "#1E40AF"},
        {"sid": "src_note",   "name": "笔记",         "pct": 20, "color": "#047857"},
        {"sid": "src_prompt", "name": "指令",         "pct": 14, "color": "#6B7280"},
    ],
    "model": "DeepSeek-V3",
    "latency_ms": 3420,
    "tokens": 6824,
    "cost_cny": 0.18,
    # 阶段说明：当前只生成了"题材候选"，还没定风格
    "stage_note": "仅探索题材 · 风格在下一步定向",
}

# 融合后的题材候选池（阶段 A：纯题材，附"风格光谱"— 告诉用户此题材在各风格下的适配度）
# 每个候选的 style_spectrum 对应 STYLE_PRESETS 的 5 个预设（严肃/幽默/治愈/华尔街派/反常识）
MOCK_FUSED_TOPICS = [
    {"id": "F01", "title": "做空是什么？3 分钟看懂熊市赚钱逻辑",
     "category": "概念", "pain": 5, "surprise": 4, "hook": 5, "difficulty": 3, "score": 14,
     "origins": [
        {"sid": "src_kw",   "label": "关键词: 做空"},
        {"sid": "src_ref1", "label": "参考片·反常识钩子"},
     ],
     "why": "关键词主方向 + 继承参考片的钩子结构",
     "style_spectrum": {"serious": 75, "humor": 92, "healing": 45, "analyst": 68, "counter": 88},
     "best_style": "humor"},
    {"id": "F02", "title": "做空大师贝瑞：2008 年他凭什么赚了 100 亿",
     "category": "人物", "pain": 4, "surprise": 5, "hook": 5, "difficulty": 3, "score": 15,
     "origins": [
        {"sid": "src_kw",     "label": "关键词: 做空"},
        {"sid": "src_ref1",   "label": "参考片·段子感结构"},
        {"sid": "src_prompt", "label": "指令·带真实人物"},
     ],
     "why": "满足指令的真实案例要求 + 钩子炸裂",
     "style_spectrum": {"serious": 88, "humor": 72, "healing": 30, "analyst": 95, "counter": 82},
     "best_style": "analyst"},
    {"id": "F03", "title": "期权的 4 个希腊字母，90% 的人只看懂一个",
     "category": "进阶", "pain": 4, "surprise": 5, "hook": 5, "difficulty": 4, "score": 14,
     "origins": [
        {"sid": "src_note",   "label": "笔记·希腊字母"},
        {"sid": "src_ref1",   "label": "参考片·反常识钩子"},
     ],
     "why": "直接复用你笔记中的核心概念",
     "style_spectrum": {"serious": 82, "humor": 62, "healing": 40, "analyst": 88, "counter": 90},
     "best_style": "counter"},
    {"id": "F04", "title": "Covered Call：散户也能稳赚的期权策略",
     "category": "策略", "pain": 3, "surprise": 5, "hook": 4, "difficulty": 4, "score": 13,
     "origins": [
        {"sid": "src_note",   "label": "笔记·Covered Call"},
        {"sid": "src_prompt", "label": "指令·不荐股"},
     ],
     "why": "笔记中的策略 + 中性表述",
     "style_spectrum": {"serious": 78, "humor": 55, "healing": 72, "analyst": 85, "counter": 60},
     "best_style": "analyst"},
    {"id": "F05", "title": "融券做空被禁，A 股散户为什么做空最难",
     "category": "市场", "pain": 5, "surprise": 4, "hook": 4, "difficulty": 3, "score": 13,
     "origins": [
        {"sid": "src_kw",     "label": "关键词: 做空/金融工具"},
     ],
     "why": "切入 A 股本地化痛感",
     "style_spectrum": {"serious": 70, "humor": 65, "healing": 80, "analyst": 60, "counter": 78},
     "best_style": "healing"},
    {"id": "F06", "title": "期权卖方赚 80% 的钱，那个 10 亿赔光的交易员",
     "category": "人物", "pain": 5, "surprise": 5, "hook": 5, "difficulty": 4, "score": 15,
     "origins": [
        {"sid": "src_note",   "label": "笔记·卖方崩盘"},
        {"sid": "src_ref1",   "label": "参考片·段子感"},
        {"sid": "src_prompt", "label": "指令·真实人物"},
     ],
     "why": "所有 source 最佳融合 · 强烈推荐",
     "style_spectrum": {"serious": 82, "humor": 88, "healing": 35, "analyst": 92, "counter": 95},
     "best_style": "counter"},
]

# 阶段 B：风格定向 · 双栏对比
# 给"做空是什么？"在 2 种不同风格下的预览
MOCK_STYLE_PREVIEW = {
    "topic_title": "做空是什么？3 分钟看懂熊市赚钱逻辑",
    "previews": {
        "humor": {
            "preset_id": "humor",
            "hook": "股票跌的时候，你哭的死去活来，别人却在偷着乐？",
            "opening": "对，他们在做空。说白了就是：高价先借来卖了，等跌了再买回来还，差价白赚。",
            "tone_sample": "就像你借邻居一件 2000 的羽绒服，拿去卖 2000，等到夏天 500 收回来，1500 装进自己口袋。",
            "golden": "牛市靠胆子，熊市靠脑子。",
            "visual_hint": "暖橙色调 · 表情包+梗图",
            "voice_hint": "青年男声 · 活泼",
            "estimated_viral": 85,
        },
        "serious": {
            "preset_id": "serious",
            "hook": "根据证监会融券业务数据，2023 年散户做空参与率不足 3%。为什么？",
            "opening": "做空是投资者预期股价下跌时，向券商借入股票卖出，待股价下跌后买回还券、赚取差价的交易行为。",
            "tone_sample": "以 2008 年次贷危机为例，对冲基金经理迈克尔·贝瑞通过做空次级抵押贷款证券，获利约 1 亿美元。",
            "golden": "做空不是看空，是一种风险对冲工具。",
            "visual_hint": "冷蓝色调 · 数据图表为主",
            "voice_hint": "青年男声 · 沉稳",
            "estimated_viral": 62,
        },
    },
}

# 10 集规划画布 (当创作模式为 series 时)
MOCK_SERIES_CANVAS = {
    "series_name": "金融小白脱盲 10 讲",
    "episodes": [
        {"ep": 1,  "title": "什么是股票？一张纸凭什么值钱",       "difficulty": 1, "mood": {"label": "轻快", "emoji": "🎉", "color": "#F48A0C"}, "dominant": "src_kw",     "topic_area": "基础"},
        {"ep": 2,  "title": "做空是什么？熊市赚钱逻辑",           "difficulty": 2, "mood": {"label": "惊讶", "emoji": "😲", "color": "#DC2626"}, "dominant": "src_kw+ref", "topic_area": "工具"},
        {"ep": 3,  "title": "通胀：为什么你的钱越来越不值钱",      "difficulty": 2, "mood": {"label": "焦虑", "emoji": "😨", "color": "#7C3AED"}, "dominant": "src_kw",     "topic_area": "宏观"},
        {"ep": 4,  "title": "复利的力量：每天 10 块 20 年后",     "difficulty": 2, "mood": {"label": "惊喜", "emoji": "💡", "color": "#059669"}, "dominant": "src_kw",     "topic_area": "投资"},
        {"ep": 5,  "title": "基金 vs 股票，该买哪个",             "difficulty": 3, "mood": {"label": "理性", "emoji": "🧐", "color": "#1E40AF"}, "dominant": "src_kw",     "topic_area": "工具"},
        {"ep": 6,  "title": "ETF 指数投资入门",                  "difficulty": 3, "mood": {"label": "轻快", "emoji": "🎉", "color": "#F48A0C"}, "dominant": "src_kw",     "topic_area": "工具"},
        {"ep": 7,  "title": "A 股散户 7 赔 2 平 1 赚",          "difficulty": 3, "mood": {"label": "沉重", "emoji": "😓", "color": "#6B7280"}, "dominant": "src_ref1",   "topic_area": "市场"},
        {"ep": 8,  "title": "期权的 4 个希腊字母",                "difficulty": 4, "mood": {"label": "好奇", "emoji": "🤔", "color": "#047857"}, "dominant": "src_note",   "topic_area": "进阶"},
        {"ep": 9,  "title": "期权 vs 期货，一图看懂",             "difficulty": 4, "mood": {"label": "专注", "emoji": "🎯", "color": "#1E40AF"}, "dominant": "src_note",   "topic_area": "进阶"},
        {"ep": 10, "title": "搭建你的第一个投资组合",             "difficulty": 3, "mood": {"label": "激励", "emoji": "🔥", "color": "#DC2626"}, "dominant": "src_prompt", "topic_area": "实操"},
    ],
    "coverage": [
        {"area": "基础",  "count": 1, "eps": [1]},
        {"area": "工具",  "count": 3, "eps": [2, 5, 6]},
        {"area": "宏观",  "count": 1, "eps": [3]},
        {"area": "投资",  "count": 1, "eps": [4]},
        {"area": "市场",  "count": 1, "eps": [7]},
        {"area": "进阶",  "count": 2, "eps": [8, 9]},
        {"area": "实操",  "count": 1, "eps": [10]},
    ],
    "callbacks": [
        {"from_ep": 2,  "to_ep": 7,  "note": "贝瑞案例 → A 股散户对比"},
        {"from_ep": 4,  "to_ep": 10, "note": "复利概念 → 投资组合实操"},
        {"from_ep": 8,  "to_ep": 9,  "note": "希腊字母 → 期权 vs 期货"},
    ],
    "difficulty_curve": [1, 2, 2, 2, 3, 3, 3, 4, 4, 3],
}

# Tab 5: Prompt 工程 · 默认模板
MOCK_PROMPT = {
    "system": """你是一二布布金融科普视频的选题 AI。
根据用户输入的关键词和节目风格，生成 20 个候选选题。
每个选题需要：
1. 包含一个"钩子词"（反常识/提问/数字）
2. 符合节目风格设定（{style_name}）
3. 难度适合目标受众（{audience}）
4. 给出 4 维评分（痛感/反常识/钩子/难度）

输出格式：JSON 数组，每项 {id, title, tags, pain, surprise, hook, difficulty, score}""",
    "user_template": "关键词: {keywords}\n风格: {style_name}\n受众: {audience}\n请生成 20 个候选选题",
    "temperature": 0.85,
    "max_candidates": 20,
    "model": "DeepSeek-V3",
    "variables": [
        {"name": "keywords",  "desc": "用户输入的 3-5 个关键词"},
        {"name": "style_name", "desc": "当前锁定的节目风格名称"},
        {"name": "audience",  "desc": "目标受众画像（从风格面板读取）"},
    ],
}


# ==================== ① 选题 ====================
# phase: explore(阶段A·题材探索)  |  direct(阶段B·风格定向)
# mode:  single / mini / series
@app.get("/project/{video_id}/topic", response_class=HTMLResponse)
async def page_topic(request: Request, video_id: str,
                     phase: str = "explore", mode: str = "single"):
    # 真实数据：从 DB 读出之前保存的 sources / 候选池 / 已选题
    real_sources = _state.load_project_sources(video_id) or DEFAULT_SOURCES.copy()
    real_pool = _state.load_project_topic_pool(video_id) or []
    real_picked = _state.load_project_topic(video_id)
    return TEMPLATES.TemplateResponse("step_topic.html", build_ctx(
        request, "topic", video_id,
        topic_pool=MOCK_TOPIC_POOL,
        selected_topic_id="F01",
        active_tab="keywords",
        style_presets=STYLE_PRESETS,
        style_dims=STYLE_DIMS,
        active_style_id="humor",
        reference=MOCK_REFERENCE,
        outline=MOCK_OUTLINE,
        series=MOCK_SERIES,
        prompt_config=MOCK_PROMPT,
        # 灵感 Lab
        sources=MOCK_SOURCES,
        source_types=SOURCE_TYPES,
        fusion=MOCK_FUSION_INSIGHTS,
        fused_topics=MOCK_FUSED_TOPICS,
        # 新增：两阶段 + 创作模式
        phase=phase,
        creation_modes=CREATION_MODES,
        active_mode=mode,
        style_preview=MOCK_STYLE_PREVIEW,
        series_canvas=MOCK_SERIES_CANVAS,
        # 真实持久化数据（覆盖 mock）
        real_sources=real_sources,
        real_pool=real_pool,
        real_picked=real_picked,
    ))


# ==================== ① 选题 · 真实输入 + AI 生成 ====================

DEFAULT_SOURCES = {
    "keywords": "",
    "notes": "",
    "extra": "",
    "references": [],  # 后续接 yt-dlp / Whisper 真实管线
}


@app.get("/api/source/load")
async def api_source_load(video_id: str):
    s = _state.load_project_sources(video_id) or DEFAULT_SOURCES.copy()
    return JSONResponse({"sources": s})


@app.post("/api/source/save")
async def api_source_save(payload: dict):
    video_id = payload.get("video_id")
    sources = payload.get("sources")
    if not video_id or sources is None:
        raise HTTPException(400, "video_id and sources required")
    _state.save_project_sources(video_id, sources)
    return JSONResponse({"ok": True})


@app.post("/api/topic/generate")
async def api_topic_generate(payload: dict):
    video_id = payload.get("video_id")
    keywords = (payload.get("keywords") or "").strip()
    extra = (payload.get("extra") or "").strip()
    mode = payload.get("mode") or "single"
    n_topics = int(payload.get("n_topics") or 6)
    if not video_id or not keywords:
        raise HTTPException(400, "video_id and keywords required")
    # 把当前输入也持久化（这样刷新页面不丢）
    notes = (payload.get("notes") or "").strip()
    _state.save_project_sources(video_id, {
        "keywords": keywords, "notes": notes, "extra": extra, "references": []
    })
    # 把 notes 也拼进 extra 给 AI
    full_extra = extra
    if notes:
        full_extra = (extra + "\n参考笔记: " + notes).strip()
    result = _topic_generator.generate_fused_topics(video_id, keywords, full_extra, mode, n_topics)
    if result.get("topics"):
        _state.save_project_topic_pool(video_id, result["topics"])
    return JSONResponse(result)


@app.get("/api/topic/pool")
async def api_topic_pool(video_id: str):
    pool = _state.load_project_topic_pool(video_id) or []
    picked = _state.load_project_topic(video_id)
    return JSONResponse({"pool": pool, "picked": picked})


@app.post("/api/topic/pick")
async def api_topic_pick(payload: dict):
    video_id = payload.get("video_id")
    topic = payload.get("topic")
    if not video_id or not topic:
        raise HTTPException(400, "video_id and topic required")
    _state.save_project_topic(video_id, topic)
    # 同时更新 videos 表的 title，让其他页面能继承
    title = topic.get("title", "")
    if title:
        # 确保 videos 表有这条记录
        if _state.get_video(video_id) is None:
            _state.create_video(video_id, title=title, topic=title)
        else:
            _state.update_video_fields(video_id, title=title, topic=title)
    # 同步把 topic.best_style 作为风格的初值（如果用户还没主动选风格）
    if not _state.load_project_style(video_id):
        best = topic.get("best_style", "humor")
        preset = next((p for p in STYLE_PRESETS if p["id"] == best), STYLE_PRESETS[1])
        _state.save_project_style(video_id, preset)
    # 题材一变，旧文案稿就过期 → 清掉，避免下一页看到陈旧稿
    with _state.get_conn() as conn:
        conn.execute("DELETE FROM script_drafts WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM script_working WHERE video_id = ?", (video_id,))
    return JSONResponse({"ok": True, "next": f"/project/{video_id}/topic?phase=direct"})


@app.post("/api/style/pick")
async def api_style_pick(payload: dict):
    video_id = payload.get("video_id")
    style_id = payload.get("style_id")
    if not video_id or not style_id:
        raise HTTPException(400, "video_id and style_id required")
    preset = next((p for p in STYLE_PRESETS if p["id"] == style_id), None)
    if not preset:
        raise HTTPException(400, f"unknown style_id: {style_id}")
    _state.save_project_style(video_id, preset)
    # 风格变化 → 同样清旧稿
    with _state.get_conn() as conn:
        conn.execute("DELETE FROM script_drafts WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM script_working WHERE video_id = ?", (video_id,))
    return JSONResponse({"ok": True, "style": preset, "next": f"/project/{video_id}/script"})


@app.get("/api/style/load")
async def api_style_load(video_id: str):
    style = _state.load_project_style(video_id)
    return JSONResponse({"style": style})


# ==================== ② 文案（三稿并排 + 工作稿） ====================

def _script_style_for(video_id: str) -> dict:
    """从 DB 读用户选定的风格预设；没选过就默认幽默段子。"""
    style = _state.load_project_style(video_id)
    return style if style else STYLE_PRESETS[1]


def _script_topic_for(video_id: str) -> str:
    """从 DB 读用户选定的题材标题；没选过就用 mock 项目的 title。"""
    picked = _state.load_project_topic(video_id)
    if picked and picked.get("title"):
        return picked["title"]
    proj = get_project(video_id)
    return proj["title"] if proj else video_id


@app.get("/project/{video_id}/script", response_class=HTMLResponse)
async def page_script(request: Request, video_id: str):
    style = _script_style_for(video_id)
    drafts = _state.load_script_drafts(video_id) or []
    working = _state.load_script_working(video_id)
    archives = _state.list_script_archives(video_id)
    return TEMPLATES.TemplateResponse("step_script.html", build_ctx(
        request, "script", video_id,
        style_preset=style,
        initial_drafts=drafts,
        initial_working=working,
        archives=archives,
        topic_title=_script_topic_for(video_id),
    ))


@app.post("/api/script/generate")
async def api_script_generate(payload: dict):
    video_id = payload.get("video_id")
    if not video_id:
        raise HTTPException(400, "video_id required")
    style = _script_style_for(video_id)
    topic = _script_topic_for(video_id)
    result = _script_writer.generate_three_drafts(video_id, topic, style)
    _state.save_script_drafts(video_id, result["drafts"])
    # 首次生成：若没有工作稿，默认用 d1 初始化
    if _state.load_script_working(video_id) is None and result["drafts"]:
        working = _script_writer.build_working_from_draft(result["drafts"][0])
        _state.save_script_working(video_id, working)
    return JSONResponse({
        "drafts": result["drafts"],
        "provider": result["provider"],
        "cost": result["cost"],
        "working": _state.load_script_working(video_id),
    })


@app.post("/api/script/refine")
async def api_script_refine(payload: dict):
    video_id = payload.get("video_id")
    field_label = payload.get("field_label") or payload.get("field") or "字段"
    current_text = payload.get("current_text", "")
    instruction = payload.get("instruction", "")
    if not video_id or not current_text:
        raise HTTPException(400, "video_id and current_text required")
    style = _script_style_for(video_id)
    context = payload.get("context", {"topic": _script_topic_for(video_id)})
    result = _script_writer.refine_field(
        video_id, field_label, current_text, instruction, style, context
    )
    return JSONResponse(result)


@app.get("/api/script/working")
async def api_script_working_get(video_id: str):
    w = _state.load_script_working(video_id)
    return JSONResponse({"working": w})


@app.post("/api/script/working")
async def api_script_working_post(payload: dict):
    video_id = payload.get("video_id")
    working = payload.get("working")
    if not video_id or working is None:
        raise HTTPException(400, "video_id and working required")
    _state.save_script_working(video_id, working)
    return JSONResponse({"ok": True})


@app.post("/api/script/archive")
async def api_script_archive(payload: dict):
    video_id = payload.get("video_id")
    name = payload.get("name") or "未命名存档"
    if not video_id:
        raise HTTPException(400, "video_id required")
    drafts = _state.load_script_drafts(video_id) or []
    working = _state.load_script_working(video_id) or {}
    archive_id = _state.archive_script(video_id, name, drafts, working)
    return JSONResponse({"archive_id": archive_id, "name": name})


@app.post("/api/script/full/generate")
async def api_script_full_generate(payload: dict):
    video_id = payload.get("video_id")
    outline = payload.get("outline")
    duration_sec = int(payload.get("duration_sec") or 60)
    pace = float(payload.get("pace_chars_per_sec") or 5.5)
    if not video_id or not outline:
        raise HTTPException(400, "video_id and outline required")
    style = _script_style_for(video_id)
    topic = _script_topic_for(video_id)
    result = _script_writer.generate_full_script(video_id, topic, style, outline, duration_sec, pace)
    if result.get("full"):
        # 给每段加 marked 字段（前端勾选/标记用）
        for p in result["full"].get("paragraphs", []):
            p.setdefault("marked", None)  # null | 'delete' | 'rewrite'
            p.setdefault("edited", False)
        _state.save_script_full(video_id, outline.get("draft_id", "?"), duration_sec, result["full"])
    return JSONResponse(result)


@app.get("/api/script/full/load")
async def api_script_full_load(video_id: str, duration_sec: Optional[int] = None):
    full = _state.load_script_full(video_id, duration_sec)
    fulls = _state.list_script_fulls(video_id)
    return JSONResponse({"full": full, "available": fulls})


@app.post("/api/script/full/save")
async def api_script_full_save(payload: dict):
    video_id = payload.get("video_id")
    full = payload.get("full")
    if not video_id or not full:
        raise HTTPException(400, "video_id and full required")
    duration_sec = int(full.get("duration_sec") or 60)
    outline_id = full.get("outline_id") or "?"
    _state.save_script_full(video_id, outline_id, duration_sec, full)
    return JSONResponse({"ok": True})


@app.post("/api/script/full/refine")
async def api_script_full_refine(payload: dict):
    video_id = payload.get("video_id")
    full = payload.get("full")
    idx = int(payload.get("paragraph_idx", -1))
    instruction = payload.get("instruction", "")
    if not video_id or not full or idx < 0:
        raise HTTPException(400, "video_id, full, paragraph_idx required")
    style = _script_style_for(video_id)
    result = _script_writer.refine_paragraph(video_id, full, idx, instruction, style)
    return JSONResponse(result)


# ---------- 灵感片段库 ----------

@app.post("/api/snippet/save")
async def api_snippet_save(payload: dict):
    video_id = payload.get("video_id")
    kind = payload.get("kind")  # hook / golden / cta / case / paragraph
    text = (payload.get("text") or "").strip()
    source_note = payload.get("source_note") or ""
    if not video_id or not kind or not text:
        raise HTTPException(400, "video_id, kind, text required")
    snippet_id = _state.save_snippet(video_id, kind, text, source_note)
    return JSONResponse({"ok": True, "snippet_id": snippet_id})


@app.get("/api/snippet/list")
async def api_snippet_list(video_id: str, kind: Optional[str] = None):
    snippets = _state.list_snippets(video_id, kind)
    return JSONResponse({"snippets": snippets})


@app.delete("/api/snippet/{snippet_id}")
async def api_snippet_delete(snippet_id: int):
    _state.delete_snippet(snippet_id)
    return JSONResponse({"ok": True})


@app.post("/api/script/restore")
async def api_script_restore(payload: dict):
    archive_id = payload.get("archive_id")
    if not archive_id:
        raise HTTPException(400, "archive_id required")
    snap = _state.load_script_archive(int(archive_id))
    if not snap:
        raise HTTPException(404, "archive not found")
    _state.save_script_drafts(snap["video_id"], snap["drafts"])
    _state.save_script_working(snap["video_id"], snap["working"])
    return JSONResponse({"ok": True, "drafts": snap["drafts"], "working": snap["working"]})


@app.get("/project/{video_id}/script/full", response_class=HTMLResponse)
async def page_script_full(request: Request, video_id: str, duration: int = 60):
    style = _script_style_for(video_id)
    full = _state.load_script_full(video_id, duration)
    available = _state.list_script_fulls(video_id)
    snippets = _state.list_snippets(video_id)
    return TEMPLATES.TemplateResponse("step_script_full.html", build_ctx(
        request, "script", video_id,
        style_preset=style,
        topic_title=_script_topic_for(video_id),
        full=full,
        available=available,
        snippets=snippets,
        duration=duration,
    ))


# ==================== ③ 分镜 ====================
@app.get("/project/{video_id}/shots", response_class=HTMLResponse)
async def page_shots(request: Request, video_id: str):
    return TEMPLATES.TemplateResponse("step_shots.html", build_ctx(
        request, "shots", video_id,
        shots=MOCK_SHOT_TABLE,
        asset_config=MOCK_ASSET_CONFIG,
        ip_options=MOCK_IP_OPTIONS,
        element_libs=MOCK_ELEMENT_LIBS,
    ))


# ==================== ④ 素材 ====================
@app.get("/project/{video_id}/assets", response_class=HTMLResponse)
async def page_assets(request: Request, video_id: str):
    return TEMPLATES.TemplateResponse("step_assets.html", build_ctx(
        request, "assets", video_id,
        assets=MOCK_ASSETS,
    ))


# ==================== ⑤ 配音字幕 ====================
@app.get("/project/{video_id}/audio", response_class=HTMLResponse)
async def page_audio(request: Request, video_id: str):
    return TEMPLATES.TemplateResponse("step_audio.html", build_ctx(
        request, "audio", video_id,
        subtitles=MOCK_SUBTITLES,
        audio_duration=90.2,
    ))


# ==================== ⑥ 剪辑 ====================
@app.get("/project/{video_id}/edit", response_class=HTMLResponse)
async def page_edit(request: Request, video_id: str):
    return TEMPLATES.TemplateResponse("step_edit.html", build_ctx(
        request, "edit", video_id,
        shots=MOCK_SHOT_TABLE,
    ))


# ==================== 素材库 ====================
@app.get("/library", response_class=HTMLResponse)
async def library(request: Request):
    return TEMPLATES.TemplateResponse("library.html", build_ctx(
        request, "library",
        library=MOCK_LIBRARY,
    ))


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "version": "0.1.0"}
