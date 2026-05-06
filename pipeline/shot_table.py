"""分镜表 — 整条管线的脊柱。

每条视频对应一个 ShotTable。分镜 Agent 初始化它、后续各 Agent 增量填写字段、
剪辑 Agent 按表装配最终 mp4。

设计原则:
- 视频 = 背景图层 + 表情图层（预制动图，带 alpha）+ 画框内元素图层 + 字幕图层
- 表情不再临时生成: 从表情库按 scene_tags 检索现成条目
- 元素图按需生成: 只有文案里提到具体叙事元素（K 线/手机通知/图标）才跑
- 可增量填写: 新字段默认 None，填什么字段用什么
- 可序列化: 落盘 JSON 供人工 cat 查看 + 机器读写
- 自带校验: 装配前检查必要字段是否齐备
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


SHOT_TABLES_DIR = Path(__file__).resolve().parents[1] / "data" / "shot_tables"


# ---------- 数据模型 ----------

# 合法的段落标签（对应文案 5 段结构）
SECTIONS = {"钩子", "共鸣", "知识点", "金句", "CTA"}

# 合法的字幕样式
SUBTITLE_STYLES = {"hook_big", "正文", "金句大字居中", "CTA"}

# 场景标签闭集合 — 10个精简标签，双向对齐文案信号
# 情绪标签层次分组（金融科普视频版 · 审核 UI 按这个布局）
#
# 标签 → 文案信号对照表（供人工审核参考）：
#   震惊 → 竟然/想不到/不是吧/居然/没想到/史上最…
#   兴奋 → 涨/翻倍/赚/盈利/暴富/牛市/庆祝/数钱
#   担忧 → 跌/亏/风险/暴跌/危机/警告/焦虑/害怕
#   崩溃 → 爆仓/强平/清零/割肉/套牢/惨/全没了
#   期待 → 抄底/看涨/布局/入场/机会/准备好了
#   调侃 → 韭菜/散户/FOMO/段子/哈哈/这操作/无语
#   困惑 → 为什么/搞不懂/难道/真的吗/怎么会
#   沉思 → 分析/逻辑/原因/思考/数据/其实是这样
#   得意 → 看吧/说对了/这才是/诀窍/技巧/知道了吧
#   平静 → 总结/今天分享/关注/点赞/记住这句话

SCENE_TAG_GROUPS: list[tuple[str, list[str]]] = [
    ("正向情绪", ["兴奋", "期待", "得意"]),
    ("负向情绪", ["担忧", "崩溃"]),
    ("认知状态", ["震惊", "困惑", "沉思"]),
    ("风格调性", ["调侃", "平静"]),
]

SCENE_TAGS = {t for _, ts in SCENE_TAG_GROUPS for t in ts}

# 元素场景标签体系 · 两个维度：叙事时机 + 视觉情绪
# 设计原则：标签描述"这个元素在哪种叙事时机+情绪下出现"，而非"它是什么物件"
# 每条 element 贴 1-2 个场景标签 + 1 个情绪标签，共 2-3 个，不超 3 个。

ELEMENT_CATEGORY_GROUPS: list[tuple[str, list[str]]] = [
    # ── 叙事时机（12 个）──────────────────────────────────────────
    ("行情走势", [
        "行情上涨",   # 飙升/翻倍/暴涨/拉升/涨停
        "行情下跌",   # 暴跌/崩盘/下跌/熔断/闪崩
    ]),
    ("交易操作", [
        "做多入场",   # 买入/看涨/抄底/布局
        "做空操作",   # 做空/借券/看跌/沽空
        "爆仓清盘",   # 爆仓/强平/保证金不足/穿仓
    ]),
    ("财富变动", [
        "财富盈利",   # 数钱/赚到/回报/盈利兑现
        "财富亏损",   # 亏损/赔钱/血亏/割肉/清零
        "存款储蓄",   # 存款/定存/储蓄/利息收入
        "借贷成本",   # 借钱/利率/利息/债务滚雪球
    ]),
    ("宏观背景", [
        "央行政策",   # 美联储/央行/加息/降息/印钞
        "通胀购买力", # 通胀/购买力/物价/CPI/货币贬值
    ]),
    ("叙事辅助", [
        "风险陷阱",   # 风险/陷阱/警告/危机/暴雷
        "数据图表",   # K线/走势图/统计/百分比/胜率
        "散户心理",   # 散户/韭菜/跟风/FOMO/追涨杀跌
    ]),
    # ── 视觉情绪（4 个）──────────────────────────────────────────
    ("视觉情绪", [
        "积极",       # 绿色/上箭头/金币飞散/喜悦富足
        "消极",       # 红色/下箭头/破碎/亏损悲伤
        "紧张",       # 炸弹/警报/崩塌/危机感
        "调侃",       # 卡通夸张/幽默/反差/轻松
    ]),
]

ELEMENT_CATEGORIES = {
    t for _, ts in ELEMENT_CATEGORY_GROUPS for t in ts
}

# 画框样式枚举
FRAME_STYLES = {"polaroid", "tv_screen", "sticky_note", "circle_window", "no_frame"}

# 画框位置
INSET_POSITIONS = {"top_left", "top_right", "bottom_left", "bottom_right", "center", "left", "right"}


@dataclass
class ElementInset:
    """画框内演绎的叙事元素 (K线图/手机通知/股票代码牌等)。

    与旧 Element 的差异: 固定用"画框"形式呈现，不再是自由贴纸。
    """
    id: str                          # 素材库 ID (EL_KLINE_DOWN_01) 或 "MISSING: 描述"
    enter_offset: float = 0.0        # 相对本镜头起点的入场时刻(秒)
    duration: float | None = None    # 持续时长，None = 跟随镜头到底
    frame_style: str = "polaroid"    # polaroid / tv_screen / sticky_note / circle_window / no_frame
    position: str = "top_right"      # top_right / bottom_left / center / ...

    # L3 AI 兜底产物（id 仍保持 "MISSING: 描述" 不动，生成结果挂这里）
    image_url: str | None = None
    image_prompt: str | None = None
    image_cost: float = 0.0
    video_url: str | None = None
    video_task_id: str | None = None
    video_cost: float = 0.0

    def absolute_enter(self, shot_start: float) -> float:
        return shot_start + self.enter_offset


# ============== 双素材并列 Beat 模型（v2 节奏改造） ==============
# 设计动机：单 expression 占满整个 shot 时长（3-6s）画面节奏太慢。改成每 shot
# 内部 N=2 个 beat（时长平分），每 beat 是【左槽 + 右槽】并列布局——
# 左可放 IP 表情，右可放场景元素，一帧两素材内容更密。
#
# 旧 shot.expression_id / background_id 字段保留，用作降级/迁移：
#   shot.beats 为空 → 渲染时自动用 expression_id 当 1 个 beat 的 left
@dataclass
class BeatSlot:
    """Beat 内一个槽位（左 or 右）的素材引用。"""
    asset_id: str = ""               # EXPR_xxx / EL_xxx / "" 留空
    asset_kind: str = ""             # "expression" | "element" | ""
    # MISSING 占位符走 AI 生图兜底（与 ElementInset 同样的 L3 流程）
    image_url: str | None = None
    image_prompt: str | None = None


@dataclass
class Beat:
    """Shot 内一个时间分段的画面（左右两素材并列）。
    duration 比例由 duration_ratio 决定（与同一 shot 内其他 beat 总和 = 1）。
    """
    duration_ratio: float = 0.5      # 占该 shot 总时长的比例
    layout: str = "left_right"        # "left_right" | "center" (单素材居中)
    left: BeatSlot = field(default_factory=BeatSlot)
    right: BeatSlot = field(default_factory=BeatSlot)


@dataclass
class Shot:
    """一个镜头 = 分镜表的一行。

    字段由不同 Agent 分阶段填写:
      分镜 Agent:  shot_id / section / text / estimated_duration /
                   scene_tags / beats / background_id / subtitle_style
      图片 Agent:  把 MISSING 的 slot.asset_id 替换为生图 URL
      配音 Agent:  actual_start / actual_end / audio_clip_path
      剪辑 Agent:  只读（按 beats 拆段渲染 + 字幕）
    """
    shot_id: str                          # S01, S02, ...
    section: str                          # 钩子 / 共鸣 / 知识点 / 金句 / CTA
    text: str                             # 本镜头台词

    estimated_duration: float             # 分镜师预估（秒）
    actual_start: float | None = None     # 配音后回填
    actual_end: float | None = None

    # 背景层（整 shot 统一一张 L0 BG）
    background_id: str = ""               # BG_xxx 或 "MISSING: 描述"
    # 节奏层：N 个 beat 时间分段。空 = 走旧 expression_id 单一 beat 兜底
    beats: list[Beat] = field(default_factory=list)
    # 分镜 Agent 识别结果（人工审阅用）
    scene_tags: list[str] = field(default_factory=list)

    # 旧字段（兼容老数据 + 旧 picker UI），新逻辑优先看 beats
    expression_id: str = ""               # EXPR_xxx
    element_insets: list[ElementInset] = field(default_factory=list)

    subtitle_style: str = "正文"

    audio_clip_path: str | None = None    # 本镜头配音 mp3
    status: str = "pending"               # pending / assets_ready / audio_ready / ready

    # 字幕层：语义切分后属于本 shot 的朗读单元（按序逐行显示）
    # 空 = 退回旧的 split_text_to_cues 正则切分
    subtitle_units: list[str] = field(default_factory=list)

    # 视觉形态标签（分镜 Agent 推荐 · 用户可在分镜页覆盖）
    # stock     = L0 + L1 + L2 全部来自手工库存（element_insets 无 MISSING）
    # ai_image  = 至少一个 element_insets 需 AI 生图兜底
    # ai_video  = 某个 AI 生图后需升级为 5s 视频
    visual_form: str = "stock"

    @property
    def actual_duration(self) -> float | None:
        if self.actual_start is None or self.actual_end is None:
            return None
        return self.actual_end - self.actual_start

    def is_missing(self, field_name: str) -> bool:
        value = getattr(self, field_name, "")
        return isinstance(value, str) and value.startswith("MISSING:")


@dataclass
class ShotTable:
    video_id: str
    title: str
    topic: str | None = None
    status: str = "draft"
    total_duration_estimated: float = 0.0
    total_duration_actual: float | None = None

    shots: list[Shot] = field(default_factory=list)

    # 全局产物（非镜头级）
    final_audio_path: str | None = None
    final_subtitle_path: str | None = None
    final_video_path: str | None = None
    cover_path: str | None = None
    publish_title: str | None = None
    publish_description: str | None = None
    publish_tags: list[str] = field(default_factory=list)

    # 发布信息备选池（一次生成 N 条减少 LLM 调用，客户端循环切换）
    publish_meta_pool: list[dict[str, Any]] = field(default_factory=list)
    publish_meta_index: int = 0

    # 来源文案的 full_id（用于检测"文案已更新但分镜未重生"的 stale 状态）
    source_full_id: int | None = None
    source_outline_id: str | None = None

    # ---------- 序列化 ----------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, pretty: bool = True) -> str:
        indent = 2 if pretty else None
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ShotTable":
        shots_data = data.pop("shots", [])
        # 旧 ShotTable 可能没的字段，给兜底
        data.setdefault("publish_meta_pool", [])
        data.setdefault("publish_meta_index", 0)
        shots: list[Shot] = []
        for sd in shots_data:
            insets_data = sd.pop("element_insets", [])
            sd["element_insets"] = [ElementInset(**e) for e in insets_data]
            # 反序列化 beats（旧 shot 可能没此字段）
            beats_raw = sd.pop("beats", []) or []
            beats: list[Beat] = []
            for bd in beats_raw:
                left_raw = bd.pop("left", None) or {}
                right_raw = bd.pop("right", None) or {}
                bd["left"] = BeatSlot(**left_raw)
                bd["right"] = BeatSlot(**right_raw)
                beats.append(Beat(**bd))
            sd["beats"] = beats
            sd.setdefault("subtitle_units", [])
            shots.append(Shot(**sd))
        return cls(shots=shots, **data)


def ensure_beats(shot: "Shot", scene_tags_pool: list[str] | None = None) -> "Shot":
    """**兼容兜底**：shot.beats 为空时，用 expression_id + element_insets 自动构造 1 个 center beat。
    供渲染 / UI 不至于看到 beats 空。"""
    if shot.beats:
        return shot
    # 从旧字段构造单 beat
    left = BeatSlot()
    right = BeatSlot()
    if shot.expression_id and not shot.expression_id.startswith("MISSING:"):
        left = BeatSlot(asset_id=shot.expression_id, asset_kind="expression")
    # element_insets 第一个有效 element 放右槽
    for ins in shot.element_insets:
        if ins.id and not ins.id.startswith("MISSING:"):
            right = BeatSlot(asset_id=ins.id, asset_kind="element",
                            image_url=ins.image_url, image_prompt=ins.image_prompt)
            break
    layout = "left_right" if right.asset_id else "center"
    shot.beats = [Beat(duration_ratio=1.0, layout=layout, left=left, right=right)]
    return shot

    # ---------- 落盘 ----------

    def save(self, path: str | Path | None = None) -> Path:
        if path is None:
            SHOT_TABLES_DIR.mkdir(parents=True, exist_ok=True)
            path = SHOT_TABLES_DIR / f"{self.video_id}.json"
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    @classmethod
    def load(cls, video_id_or_path: str | Path) -> "ShotTable":
        p = Path(video_id_or_path)
        if not p.exists():
            p = SHOT_TABLES_DIR / f"{video_id_or_path}.json"
        if not p.exists():
            raise FileNotFoundError(f"shot table not found: {video_id_or_path}")
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls.from_dict(data)

    # ---------- 查询 ----------

    def missing_assets(self) -> dict[str, list[tuple[str, str]]]:
        """收集所有 MISSING 的素材。
        返回: {"backgrounds": [(shot_id, desc), ...], "elements": [...], "expressions": [...]}

        expressions 的 MISSING = 分镜 Agent 没能在库里找到合适的表情
        （通常意味着需要补录素材，不是生成）。
        """
        missing: dict[str, list[tuple[str, str]]] = {
            "backgrounds": [],
            "elements": [],
            "expressions": [],
        }
        for shot in self.shots:
            if shot.is_missing("background_id"):
                desc = shot.background_id.replace("MISSING:", "").strip()
                missing["backgrounds"].append((shot.shot_id, desc))
            if shot.is_missing("expression_id") or not shot.expression_id:
                tag_desc = "/".join(shot.scene_tags) if shot.scene_tags else "未打标"
                missing["expressions"].append((shot.shot_id, tag_desc))
            for ins in shot.element_insets:
                if ins.id.startswith("MISSING:"):
                    desc = ins.id.replace("MISSING:", "").strip()
                    missing["elements"].append((shot.shot_id, desc))
        return missing

    def get_shot(self, shot_id: str) -> Shot | None:
        for s in self.shots:
            if s.shot_id == shot_id:
                return s
        return None

    # ---------- 校验 ----------

    def validate_for_assembly(self) -> list[str]:
        """剪辑前的完整性校验。返回错误列表，空表示可装配。"""
        errors: list[str] = []
        if not self.shots:
            errors.append("no shots")
            return errors

        for shot in self.shots:
            ctx = f"[{shot.shot_id}]"

            if shot.actual_start is None or shot.actual_end is None:
                errors.append(f"{ctx} missing actual_start/end (配音 Agent 还没跑？)")
            if shot.is_missing("background_id") or not shot.background_id:
                errors.append(f"{ctx} background_id 未补齐 (图片 Agent 还没跑？)")
            if shot.is_missing("expression_id") or not shot.expression_id:
                errors.append(f"{ctx} expression_id 未命中 (表情库需补录)")
            for ins in shot.element_insets:
                if ins.id.startswith("MISSING:") or not ins.id:
                    errors.append(f"{ctx} element_inset 未生成: {ins.id}")
                if ins.frame_style not in FRAME_STYLES:
                    errors.append(f"{ctx} 画框样式非法: {ins.frame_style}")
            if not shot.audio_clip_path:
                errors.append(f"{ctx} audio_clip_path 缺失")
            if shot.section not in SECTIONS:
                errors.append(f"{ctx} section 非法: {shot.section}")
            for t in shot.scene_tags:
                if t not in SCENE_TAGS:
                    errors.append(f"{ctx} scene_tag 非法: {t}")

        if not self.final_audio_path:
            errors.append("final_audio_path 缺失")
        if not self.final_subtitle_path:
            errors.append("final_subtitle_path 缺失")

        return errors


# ---------- 便捷工厂 ----------

def create_empty(video_id: str, title: str, topic: str | None = None) -> ShotTable:
    return ShotTable(video_id=video_id, title=title, topic=topic, status="draft")


if __name__ == "__main__":
    st = create_empty("V_TEST", "做空是什么", topic="做空")
    st.shots = [
        Shot(
            shot_id="S01",
            section="钩子",
            text="你知道股票跌了也能赚钱吗？",
            estimated_duration=5.0,
            background_id="BG_OFFICE_01",
            expression_id="EXPR_SHOCK_01",
            scene_tags=["震惊"],
            subtitle_style="hook_big",
        ),
        Shot(
            shot_id="S02",
            section="共鸣",
            text="去年大盘跌的时候，很多人账户都缩水了...",
            estimated_duration=13.0,
            background_id="MISSING: 股市大屏+红色K线",
            expression_id="EXPR_WORRIED_02",
            scene_tags=["担忧", "失落"],
            element_insets=[
                ElementInset(
                    id="MISSING: K 线下跌示意图",
                    enter_offset=1.0,
                    duration=3.0,
                    frame_style="tv_screen",
                    position="top_right",
                ),
            ],
        ),
    ]
    st.total_duration_estimated = sum(s.estimated_duration for s in st.shots)

    path = st.save()
    print(f"[shot_table] saved to {path}")

    loaded = ShotTable.load("V_TEST")
    print(f"[shot_table] loaded: {loaded.title} | {len(loaded.shots)} shots")
    print(f"[shot_table] missing assets: {loaded.missing_assets()}")
    print(f"[shot_table] validate errors:")
    for e in loaded.validate_for_assembly():
        print(f"  - {e}")
