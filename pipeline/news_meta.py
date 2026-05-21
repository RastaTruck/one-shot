"""新闻口播管线的项目专属字段（存在 videos.meta_json["news"] 子树）。

设计原则（仿 outhan_meta）：不改 schema，所有 News 特有字段塞 meta_json["news"]。

schema 形态：
  meta_json = {
    "news": {
      "topic_keywords": str,           # 选题关键词
      "topic_intent": str,             # 意图（搞懂/影响/操作/共鸣）
      "topic_audience": str,           # 受众
      "topic_picked": dict | null,     # 已锁定的选题（来自候选）
      "script_full": dict | null,      # 完整文案 + 节拍标注
      "presenter_id": str | null,      # 选定的主播
      "audio_path": str | null,        # 配音 mp3 路径（项目相对）
      "audio_duration_sec": float,     # 配音时长（lipsync 主时钟）
      "audio_alignment": list,         # whisper 反向对齐 [{word, start, end}]
      "shot_plan": list,               # 切镜清单 [{start, end, framing, take_id}]
      "draft_video_path": str | null,  # 切镜后未 lipsync 的视频骨架
      "final_video_path": str | null,  # lipsync 后的成片
      "current_step": int,             # 1-6
    }
  }
"""
from __future__ import annotations

import json
import time
from typing import Any

from pipeline import state as _state


DEFAULT_NEWS = {
    # 步骤 1 · 选题
    "mode": "single",                # single / dialog（v0 只做 single）
    "topic_keywords": "",
    "topic_intent": "搞懂概念",
    "topic_audience": "完全小白",
    "duration_sec": 60,              # 目标时长，秒
    "topic_pool": [],                # 选题候选池
    "topic_picked": None,            # 选定的选题对象
    # 步骤 2 · 文案
    "script_title": "",
    "script_text": "",               # 完整口播文案（一气呵成）
    "script_char_count": 0,
    "script_units": [],              # 字幕单元 list[str]，每条 = 一条字幕 cue · 由用户审核后落库
    #                                  TTS 用 units join "。" 喂；字幕用 units 1:1 对齐 librosa 静音
    # 步骤 3 · 镜头模板
    "shot_template_id": None,        # 选定的镜头系列 id
    # 步骤 4 · 成片要素（bible 形态）
    "voice_provider": "edge_tts",    # edge_tts / fish_audio
    "voice_id": "",                  # voice_id（Edge）或 reference_id（Fish）
    "subtitle_burn": True,           # 是否烧录字幕（默认开，短视频标配）
    "subtitle_style": "classic_white",  # classic_white / modern_orange
    # 新闻栏目条（底部装饰层，央视/凤凰风格）
    "chyron_enabled": False,         # 是否叠加新闻栏目条
    "chyron_style": "znn_intl",      # znn_intl (funny_zoo) / znn_finance (black_gold) — 默认走 hyperframes
    "chyron_label": "新闻速递",       # 左侧栏目名小色块文字（≤6 字）
    "chyron_title": "",              # 中部大字标题（空则用 script_title）
    "chyron_ticker": "",             # 底部滚动 ticker 文字（仅 ffmpeg_drawbox 引擎用 · 已废）
    "chyron_live_dot": True,         # LIVE 红点呼吸（仅 ffmpeg_drawbox 引擎用 · 已废）
    "chyron_title_sub": "",          # 副条中文副标题（hyperframes/pil_overlay 用 · 空则用 script_title）
    "chyron_station": "ZNN",         # 右上台标（hyperframes/pil_overlay 用）
    "chyron_time_str": "",           # 右下时间码（空则当前 HH:MM）
    # 背景音乐（BGM）
    "bgm_id": None,                  # 选定的 BGM 文件名（不含路径，挂在 ./口播bgm/ 下）
    "bgm_volume_db": -16.0,          # BGM 相对音量（dB），人声不变，BGM 衰减
    "audio_path": None,              # 项目相对路径，如 data/news_audio/V0042/voice.mp3
    "audio_duration_sec": 0.0,
    # 步骤 5 · 视频合成
    "final_video_path": None,        # 切镜+原音轨成片路径
    "switch_every": 6.0,             # 切镜节奏（秒）
    # 步骤 6 · 导出 / Lipsync
    "lipsync_video_path": None,      # VideoRetalk 替换嘴型后的成片
    "lipsync_task_id": None,         # 阿里云任务 id（debug 用）
    # 进度
    "current_step": 1,
    # 热点工坊传过来的完整立意（含找补 refs）— 让 topic/文案步骤能看到 brief 全貌
    "forge_brief": None,        # {title, core_argument, audience_baseline, info_support, key_data, emotion_hook, direction_label, direction_key, track}
    "forge_supplements": None,  # {pool_refs, web_refs} — AI 找补的相关报道
    "forge_signal": None,       # {signal_id, source, title, url} — 工坊选定的源信号
}


# 步骤号 ↔ 路径 slug
_STEP_SLUGS = {
    1: "topic",
    2: "script",
    3: "presenter",   # 沿用 slug 不变（指"镜头模板"），未来不动 URL
    4: "audio",
    5: "video",
    6: "export",      # v0 = 成片预览/下载；v1 = lipsync 替换
}
_STEP_LABELS = {
    1: "选题",
    2: "文案",
    3: "镜头模板",
    4: "成片要素",     # 配音 + 栏目条 + BGM + 字幕样式（一站式装饰）
    5: "视频合成",
    6: "导出",
}


def _read_meta(project_id: str) -> dict[str, Any]:
    rec = _state.get_video(project_id)
    if rec is None:
        return {}
    return dict(rec.meta) if rec.meta else {}


def _write_meta(project_id: str, meta: dict[str, Any]) -> None:
    with _state.get_conn() as conn:
        conn.execute(
            "UPDATE videos SET meta_json = ?, updated_at = ? WHERE video_id = ?",
            (json.dumps(meta, ensure_ascii=False), time.time(), project_id),
        )


def get_news_data(project_id: str) -> dict[str, Any]:
    meta = _read_meta(project_id)
    out = dict(DEFAULT_NEWS)
    out.update(meta.get("news", {}))
    return out


def update_news_data(project_id: str, **fields: Any) -> dict[str, Any]:
    meta = _read_meta(project_id)
    cur = dict(DEFAULT_NEWS)
    cur.update(meta.get("news", {}))
    for k, v in fields.items():
        if k in DEFAULT_NEWS:
            cur[k] = v
    meta["news"] = cur
    _write_meta(project_id, meta)
    return cur


def set_current_step(project_id: str, step: int) -> None:
    update_news_data(project_id, current_step=max(1, min(6, step)))


def step_path(project_id: str, step: int) -> str:
    return _STEP_SLUGS.get(step, "topic")


def step_label(step: int) -> str:
    return _STEP_LABELS.get(step, "")


def all_steps() -> list[tuple[int, str, str]]:
    """[(step_num, slug, label), ...]，用于 stepper 渲染。"""
    return [(n, _STEP_SLUGS[n], _STEP_LABELS[n]) for n in sorted(_STEP_SLUGS)]
