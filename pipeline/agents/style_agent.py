"""画风锁定 Agent。

**关键修正**：画风 DNA 不描述 IP 外观（IP 是手工素材，不由 AI 生成）。
DNA 只约束"当 AI 要生一张库里没有的元素时，它应该长什么样"——
视觉语言、色彩、材质、构图的共同词汇。

提供两个能力：
- `compose_image_prompt(intent, bible)` —— 所有 L3（AI 生图兜底）统一走此函数注入画风约束
- `generate_test_element(bible)` —— 按 bible.test_intent 生一张 L3 测试图，
  让用户肉眼检验"这套约束生的东西是否和手工素材画风扣得上"

`generate_reference_images` 保留以兼容老 API（暂不用）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline import state
from pipeline.tools import ark_client

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
STYLE_BIBLE_PATH = CONFIG_DIR / "style_bible.json"


def load_style_bible() -> dict[str, Any]:
    return json.loads(STYLE_BIBLE_PATH.read_text(encoding="utf-8"))


def compose_image_prompt(intent: str, bible: dict[str, Any], library=None) -> str:
    """把"我要一个什么东西"(intent) + 画风 DNA 拼成最终 prompt。

    L3 所有生图（test_element + material_agent 兜底生成）都走此函数。
    不含 IP 外观（IP 是手工素材，AI 不生 IP）。

    如果传入 library 且 bible.element_library_ids 非空，会合并这些库的 style_tags。
    """
    parts: list[str] = [intent.strip().rstrip("。.")]

    tag_set: list[str] = []
    seen: set[str] = set()

    # 1. 全局 visual_style_tags
    for t in (bible.get("visual_style_tags") or []):
        if t not in seen:
            tag_set.append(t); seen.add(t)

    # 2. 已选 element_library 的 style_tags
    if library is not None:
        for lid in (bible.get("element_library_ids") or []):
            lmeta = library.element_library_meta(lid) or {}
            for t in (lmeta.get("style_tags") or []):
                if t not in seen:
                    tag_set.append(t); seen.add(t)

    if tag_set:
        parts.append(f"画风：{'、'.join(tag_set)}")

    palette_items = bible.get("palette") or []
    if palette_items:
        palette_words = "、".join(f"{p['name']}（{p['hex']}）" for p in palette_items[:4])
        parts.append(f"色调：{palette_words}")

    if bible.get("composition"):
        parts.append(f"构图：{bible['composition']}")

    parts.append(f"画幅比 {bible.get('aspect_ratio', '9:16')}")

    neg = bible.get("negative_prompt_global")
    if neg:
        parts.append(f"负向：{neg}")

    return "。".join(parts) + "。"


def generate_test_element(
    video_id: str,
    bible: dict[str, Any] | None = None,
    override_intent: str | None = None,
    library=None,
) -> dict[str, Any]:
    """跑 1 张 L3 测试图，检验画风是否到位。

    intent 来源优先级：override_intent > bible.test_intent > 默认占位。
    返回 {"ok", "url", "intent", "prompt", "cost", "provider", "error"}.
    """
    bible = bible or load_style_bible()
    intent = (override_intent or bible.get("test_intent") or "一个通用场景元素").strip()
    prompt = compose_image_prompt(intent, bible, library=library)

    task_id = state.start_task(
        video_id, agent="style_test",
        input_data={"intent": intent, "style_version": bible.get("style_pack_version")},
    )
    r = ark_client.generate_image(prompt, size=bible.get("size", "2K"))
    state.finish_task(
        task_id, success=r["ok"],
        output={"ok": r["ok"], "provider": r["provider"]},
        cost_cny=r["cost"],
        error=(r.get("error") or "")[:500] if not r["ok"] else None,
    )
    return {
        "ok": r["ok"],
        "url": r.get("url"),
        "intent": intent,
        "prompt": prompt,
        "cost": r["cost"],
        "provider": r["provider"],
        "error": r.get("error"),
    }


def generate_reference_images(
    video_id: str,
    bible: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """（保留兼容）老版本 3 张参考图生成。当前页面已不再调用。"""
    bible = bible or load_style_bible()
    refs = bible.get("reference_prompts") or []
    if not refs:
        return {"images": [], "total_cost": 0.0, "bible": bible, "all_ok": True, "errors": []}
    task_id = state.start_task(
        video_id, agent="style_ref",
        input_data={"n": len(refs), "bible_version": bible.get("style_pack_version")},
    )
    images: list[dict[str, Any]] = []
    total_cost = 0.0
    errors: list[str] = []
    for ref in refs[:3]:
        prompt = compose_image_prompt(ref["intent"], bible)
        r = ark_client.generate_image(prompt, size=bible.get("size", "2K"))
        images.append({
            "slot": ref.get("slot", ""),
            "intent": ref.get("intent", ""),
            "prompt": prompt,
            "url": r["url"],
            "cost": r["cost"],
            "provider": r["provider"],
            "ok": r["ok"],
            "error": r.get("error"),
        })
        total_cost += r["cost"]
        if not r["ok"] and r.get("error"):
            errors.append(r["error"][:120])
    all_ok = not errors
    state.finish_task(
        task_id, success=all_ok,
        output={"n": len(images), "errors": errors[:3]},
        cost_cny=total_cost,
    )
    return {
        "images": images, "total_cost": round(total_cost, 2),
        "bible": bible, "all_ok": all_ok, "errors": errors,
    }
