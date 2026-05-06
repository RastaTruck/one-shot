"""朗读切分 Agent · 把段落切成"可朗读单元"。

输出落进 script_full.paragraphs[i].units（list[str]）。下游：
- shot_table_agent 优先按 units 装段，绕过正则切分
- TTS 与字幕都顺着 shot.text 走 → 自然按 units 边界停顿/换行

接口：
    run(video_id) -> {"ok": bool, "paragraphs": [...], "provider", "cost", "error"?}
    persist(video_id, paragraphs_with_units) -> bool       # 用户编辑后保存
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline import state as _state
from pipeline.tools import llm_client


_PROMPT_PATH = Path(__file__).resolve().parents[2] / "config" / "prompts" / "segmenter.txt"


def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _build_user_prompt(paragraphs: list[dict]) -> str:
    lines = ["## 文稿段落（请按下标 p_idx 一一对应输出）"]
    for i, p in enumerate(paragraphs):
        text = (p.get("text") or "").strip()
        role = (p.get("role") or "").strip()
        lines.append(f"\n[p_idx={i}, role={role or '-'}]")
        lines.append(text)
    return "\n".join(lines)


def _validate_unit(unit: str, source: str) -> bool:
    """单元必须是源段落的子串、长度 4-30（宽容点，硬约束 8-22 但允许例外）。"""
    if not unit:
        return False
    if not (4 <= len(unit) <= 30):
        return False
    return unit in source


def _normalize_response(
    raw: dict, source_paragraphs: list[dict]
) -> tuple[list[dict], list[str]]:
    """把 LLM 输出对齐到源段落，缺漏的段落 fallback 用整段做单一单元。
    返回 (paragraphs_with_units, warnings)。"""
    by_idx: dict[int, list[str]] = {}
    for item in (raw.get("paragraphs") or []):
        try:
            idx = int(item.get("p_idx"))
        except (TypeError, ValueError):
            continue
        units = [str(u or "").strip() for u in (item.get("units") or [])]
        units = [u for u in units if u]
        if units:
            by_idx[idx] = units

    out: list[dict] = []
    warnings: list[str] = []
    for i, p in enumerate(source_paragraphs):
        text = (p.get("text") or "").strip()
        units = by_idx.get(i, [])
        # 校验 + 漏字检测
        valid_units = [u for u in units if _validate_unit(u, text)]
        # 字数和必须 ≈ 原段（±2 字容差，标点差异）
        joined = "".join(valid_units)
        if not valid_units or abs(len(joined) - len(text)) > max(2, int(len(text) * 0.05)):
            warnings.append(f"段 {i+1}: LLM 切分异常，回退到整段单元")
            valid_units = [text] if text else []
        out.append({**p, "units": valid_units})
    return out, warnings


def run(video_id: str, duration_sec: int | None = None) -> dict[str, Any]:
    """对当前 script_full 做切分，落到 script_full.paragraphs[i].units。"""
    full = _state.load_script_full(video_id, duration_sec=duration_sec)
    if not full:
        return {"ok": False, "error": "项目还没有完整文稿"}

    paragraphs = full.get("paragraphs") or []
    paragraphs = [p for p in paragraphs if (p.get("text") or "").strip()]
    if not paragraphs:
        return {"ok": False, "error": "文稿段落为空"}

    sys_prompt = _load_system_prompt()
    user = _build_user_prompt(paragraphs)
    result = llm_client.call_json_with_fallback(sys_prompt, user, timeout=120)
    if result is None:
        return {"ok": False, "error": "LLM 不可用（无密钥或网络错误）"}
    provider, content_str, cost = result

    try:
        data = json.loads(content_str)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"LLM 输出非 JSON: {e}", "raw": content_str[:300]}

    paragraphs_with_units, warnings = _normalize_response(data, paragraphs)

    # 写回 full_json（保留其余字段）
    new_full = {**full, "paragraphs": paragraphs_with_units}
    new_full.pop("full_id", None)
    new_full.pop("updated_at", None)
    new_full.pop("duration_sec", None)
    new_full.pop("outline_id", None)
    _state.save_script_full(
        video_id,
        full.get("outline_id", ""),
        full.get("duration_sec", 0),
        new_full,
    )

    return {
        "ok": True,
        "paragraphs": paragraphs_with_units,
        "provider": provider,
        "cost": cost,
        "warnings": warnings,
    }


def persist(video_id: str, paragraphs_with_units: list[dict]) -> bool:
    """用户在 UI 编辑了 units → 保存。
    text 字段以前端传来的为准（用户可能已在段落编辑器里修改过正文）。
    """
    full = _state.load_script_full(video_id)
    if not full:
        return False
    for i, p in enumerate(paragraphs_with_units):
        # 只过滤空字符串，不强制回写原始 text（允许用户的正文编辑生效）
        units = [str(u or "").strip() for u in (p.get("units") or [])]
        p["units"] = [u for u in units if u]
    new_full = {**full, "paragraphs": paragraphs_with_units}
    for k in ("full_id", "updated_at", "duration_sec", "outline_id"):
        new_full.pop(k, None)
    _state.save_script_full(
        video_id,
        full.get("outline_id", ""),
        full.get("duration_sec", 0),
        new_full,
    )
    return True
