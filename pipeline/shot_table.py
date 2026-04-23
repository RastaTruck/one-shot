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

# 场景标签闭集合（粗粒度 15 个）— 分镜 Agent 只能从中选
SCENE_TAGS = {
    "震惊", "困惑", "沉思", "讲解", "严肃",
    "失落", "得意", "恐慌", "担忧", "兴奋",
    "无语", "愤怒", "调侃", "害怕", "平静",
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

    def absolute_enter(self, shot_start: float) -> float:
        return shot_start + self.enter_offset


@dataclass
class Shot:
    """一个镜头 = 分镜表的一行。

    字段由不同 Agent 分阶段填写:
      分镜 Agent:  shot_id / section / text / estimated_duration /
                   scene_tags / expression_id / background_id /
                   element_insets / subtitle_style
      图片 Agent:  把 MISSING 的 background_id / element_insets[].id 替换为新 ID
      配音 Agent:  actual_start / actual_end / audio_clip_path
      剪辑 Agent:  只读（机械装配 = 背景 + 表情动图 + 画框元素 + 字幕）
    """
    shot_id: str                          # S01, S02, ...
    section: str                          # 钩子 / 共鸣 / 知识点 / 金句 / CTA
    text: str                             # 本镜头台词

    estimated_duration: float             # 分镜师预估（秒）
    actual_start: float | None = None     # 配音后回填
    actual_end: float | None = None

    # 三层拼装
    background_id: str = ""               # BG_xxx 或 "MISSING: 描述"
    expression_id: str = ""               # EXPR_xxx，指向表情库中预制动图
    scene_tags: list[str] = field(default_factory=list)  # 分镜 Agent 识别结果（人工审阅用）
    element_insets: list[ElementInset] = field(default_factory=list)

    subtitle_style: str = "正文"

    audio_clip_path: str | None = None    # 本镜头配音 mp3
    status: str = "pending"               # pending / assets_ready / audio_ready / ready

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

    # ---------- 序列化 ----------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, pretty: bool = True) -> str:
        indent = 2 if pretty else None
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ShotTable":
        shots_data = data.pop("shots", [])
        shots: list[Shot] = []
        for sd in shots_data:
            insets_data = sd.pop("element_insets", [])
            sd["element_insets"] = [ElementInset(**e) for e in insets_data]
            shots.append(Shot(**sd))
        return cls(shots=shots, **data)

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
