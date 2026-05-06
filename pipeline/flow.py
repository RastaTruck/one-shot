"""六步流水线 · 单一真相源（导航 / 完成判定 / 下一步路由）。

历史背景：2026-04-27 之前 web 层有 4 个互不引用的"步骤真相"——
  ❶ web/app.py:STEPS
  ❷ web/deps.py:STEPS（孤儿、和 ❶ 冲突）
  ❸ web/app.py:_NEXT_STEP_BY_DONE 字典
  ❹ 各 step_*.html 模板里 hard-code 的下一步 URL

互相不知道对方存在，谁改谁错位 → 用户看到"下一步"乱跳、stepper 高亮失配。
本文件是它们的唯一替代。其他地方不准再定义"步骤"。

判定原则：is_done_* 看**真字段**（内容有效），不只是"行存在"。
stale 状态（脚本变了分镜没重生）= not done，按钮自动禁用。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from pipeline import state as _state


# ---------- 数据结构 ----------

@dataclass(frozen=True)
class Step:
    key: str
    label: str           # 完整标签（含编号）："① 选题"
    label_short: str     # 紧凑标签："选题"
    icon: str            # lucide 图标名
    route_template: str  # URL 模板，含 {video_id}

    def url_for(self, video_id: str) -> str:
        return self.route_template.format(video_id=video_id)


@dataclass
class VideoState:
    """一次 DB 读取，所有 is_done_* 共享。避免 6 次重复查询。"""
    video_id: str
    topic: Optional[dict[str, Any]]
    script_full: Optional[dict[str, Any]]
    style_bible: Optional[dict[str, Any]]
    shot_table: Optional[dict[str, Any]]
    output_mp4_exists: bool

    @classmethod
    def load(cls, video_id: str, outputs_dir: Path) -> "VideoState":
        mp4 = outputs_dir / f"{video_id}.mp4"
        return cls(
            video_id=video_id,
            topic=_state.load_project_topic(video_id),
            script_full=_state.load_script_full(video_id),
            style_bible=_state.load_project_style_bible(video_id),
            shot_table=_state.load_project_shot_table(video_id),
            output_mp4_exists=mp4.exists() and mp4.stat().st_size > 0,
        )


# ---------- 完成判定 ----------
# 每个函数返回 (done, blocker_reason)。blocker 非空 = UI 显示"为什么不能进下一步"。

def is_done_topic(s: VideoState) -> tuple[bool, str]:
    if not s.topic:
        return False, "尚未选定题材"
    if not (s.topic.get("title") or s.topic.get("id")):
        return False, "题材数据不完整"
    return True, ""


def is_done_script(s: VideoState) -> tuple[bool, str]:
    if not s.script_full:
        return False, "尚未生成完整文案"
    paragraphs = s.script_full.get("paragraphs") or []
    if not paragraphs:
        return False, "完整文案为空（paragraphs 列表空）"
    return True, ""


_STYLE_CONFIRM_KEYS = (
    "ip_library",
    "element_library",
    "background",
    "voice_subtitle",
    "bgm",
)


def is_done_style(s: VideoState) -> tuple[bool, str]:
    if not s.style_bible:
        return False, "尚未保存画风设定"
    if s.style_bible.get("locked"):
        return True, ""
    confirmed = (s.style_bible.get("bible") or {}).get("confirmed") or {}
    missing = [k for k in _STYLE_CONFIRM_KEYS if not confirmed.get(k)]
    if missing:
        return False, f"画风未完成（剩 {len(missing)} 块未确认）"
    return True, ""


def is_done_shots(s: VideoState) -> tuple[bool, str]:
    if not s.shot_table:
        return False, "尚未生成分镜表"
    shots = s.shot_table.get("shots") or []
    if not shots:
        return False, "分镜表为空"
    # stale = 文案改了但分镜没重生
    if s.script_full:
        current_full_id = s.script_full.get("full_id")
        bound_full_id = s.shot_table.get("source_full_id")
        if current_full_id is not None:
            if bound_full_id is None:
                return False, "分镜表未绑定文案版本（建议重新生成）"
            if bound_full_id != current_full_id:
                return False, f"文案已更新（#{current_full_id}），分镜基于旧版（#{bound_full_id}）"
    return True, ""


def is_done_assets(s: VideoState) -> tuple[bool, str]:
    """素材完成 = 每个镜头都有配音 + 分镜非空。

    视觉素材（背景/表情/元素 L3）**不要求 100% 齐**——
    video_editor_agent.compose_final 对缺失视觉自动用 L0 背景或橙色 placeholder 兜底。
    判定门槛与剪辑页 `get ready()` 保持一致（step_edit.html: audioReady && shotsCount > 0）。
    """
    if not s.shot_table:
        return False, "尚无分镜表"
    shots = s.shot_table.get("shots") or []
    if not shots:
        return False, "分镜为空"
    missing_audio = sum(1 for sh in shots if not sh.get("audio_clip_path"))
    if missing_audio:
        return False, f"{missing_audio} 个镜头未生成配音"
    return True, ""


def is_done_edit(s: VideoState) -> tuple[bool, str]:
    if not s.output_mp4_exists:
        return False, "尚未导出最终 mp4"
    return True, ""


# ---------- 步骤定义（唯一） ----------

STEPS: list[Step] = [
    Step("topic",  "① 选题", "选题", "lightbulb",   "/project/{video_id}/topic"),
    Step("script", "② 文案", "文案", "file-text",   "/project/{video_id}/script"),
    Step("style",  "③ 画风", "画风", "palette",     "/project/{video_id}/style"),
    Step("shots",  "④ 分镜", "分镜", "layout-grid", "/project/{video_id}/shots"),
    Step("assets", "⑤ 素材", "素材", "image",       "/project/{video_id}/assets"),
    Step("edit",   "⑥ 剪辑", "剪辑", "film",        "/project/{video_id}/edit"),
]

_IS_DONE: dict[str, Callable[[VideoState], tuple[bool, str]]] = {
    "topic":  is_done_topic,
    "script": is_done_script,
    "style":  is_done_style,
    "shots":  is_done_shots,
    "assets": is_done_assets,
    "edit":   is_done_edit,
}

STEP_KEYS = [s.key for s in STEPS]


def get_step(key: str) -> Optional[Step]:
    for s in STEPS:
        if s.key == key:
            return s
    return None


# ---------- Progress（UI 直接消费） ----------

@dataclass
class StepView:
    """stepper / 下一步按钮直接展开就能渲染。"""
    step: Step
    done: bool
    blocker: str         # 未完成时的人话原因
    is_current: bool     # 当前应该停留在哪一步（第一个未完成的；全完成时 = 最后一步）
    url: str             # 已展开 video_id 的完整 URL


@dataclass
class Progress:
    video_id: str
    state: VideoState
    steps_view: list[StepView]
    current: StepView
    next_step: Optional[Step]   # current 之后那一步；全完成 = None
    can_advance: bool           # current.done — 决定"下一步"按钮是否可点

    def url_for(self, step_key: str) -> Optional[str]:
        for sv in self.steps_view:
            if sv.step.key == step_key:
                return sv.url
        return None

    def is_done(self, step_key: str) -> bool:
        for sv in self.steps_view:
            if sv.step.key == step_key:
                return sv.done
        return False

    def blocker_for(self, step_key: str) -> str:
        for sv in self.steps_view:
            if sv.step.key == step_key:
                return sv.blocker
        return ""

    def step_index(self, step_key: str) -> int:
        for i, sv in enumerate(self.steps_view):
            if sv.step.key == step_key:
                return i
        return -1

    @property
    def current_index(self) -> int:
        return self.step_index(self.current.step.key)


def compute_progress(video_id: str, outputs_dir: Path) -> Progress:
    s = VideoState.load(video_id, outputs_dir)
    views: list[StepView] = []
    first_undone: Optional[int] = None

    for idx, step in enumerate(STEPS):
        done, blocker = _IS_DONE[step.key](s)
        views.append(StepView(
            step=step, done=done, blocker=blocker,
            is_current=False, url=step.url_for(video_id),
        ))
        if not done and first_undone is None:
            first_undone = idx

    current_idx = first_undone if first_undone is not None else len(STEPS) - 1
    views[current_idx].is_current = True
    current = views[current_idx]
    next_step = STEPS[current_idx + 1] if current_idx + 1 < len(STEPS) else None

    return Progress(
        video_id=video_id, state=s, steps_view=views,
        current=current, next_step=next_step, can_advance=current.done,
    )


# ---------- Smoke test ----------

if __name__ == "__main__":
    """跑当前 DB 里所有视频，打印每个的 progress，看判定有没有错。"""
    outputs = Path(__file__).resolve().parents[1] / "data" / "outputs"
    videos = _state.list_videos()
    if not videos:
        print("[flow] 当前 DB 没有视频项目")
    for v in videos:
        p = compute_progress(v.video_id, outputs)
        print(f"\n=== {v.video_id} | {v.title} ===")
        print(f"DB status: {v.status}")
        print(f"Current step: {p.current.step.key}  (can_advance={p.can_advance})")
        if p.next_step:
            print(f"Next step:    {p.next_step.key}")
        else:
            print(f"Next step:    — (全完成)")
        for sv in p.steps_view:
            mark = "✓" if sv.done else ("→" if sv.is_current else " ")
            line = f"  [{mark}] {sv.step.label}"
            if sv.blocker:
                line += f"  ← {sv.blocker}"
            print(line)
