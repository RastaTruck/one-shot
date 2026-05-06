"""素材 Agent：把一个 Shot 展开成 6 层 slot 视图，供素材页渲染。

图层模型：
  L0 背景        —— 从素材库背景匹配（Shot.background_id）
  L1 IP 表情     —— 按 scene_tags 从 expressions 库筛选（Shot.scene_tags）
  L2 场景元素    —— Shot.element_insets 里手工命中的那些（id 不以 MISSING 开头）
  L3 AI 生成     —— Shot.element_insets 里 MISSING 的 + Shot.background MISSING
  L4 配音 (MOCK) —— 按 Shot.estimated_duration 估一段字幕 → 配音占位
  L5 字幕 (MOCK) —— 复用 Shot.text

每层 slot 包含：
  {layer, label, source: "manual"|"ai"|"mock", asset_id|ai_prompt, thumbnail,
   desc, status, meta}
"""
from __future__ import annotations

from typing import Any

from pipeline.asset_library import AssetLibrary
from pipeline.shot_table import ElementInset, Shot, ShotTable


def _asset_view(asset) -> dict[str, Any]:
    """把 Asset 序列化为前端用的缩略结构。"""
    return {
        "id": asset.id,
        "kind": asset.kind,
        "file": asset.file,
        "tags": asset.tags,
        "description": asset.description,
        "style": asset.style,
        "meta": asset.meta,
    }


def _layer_stub(layer: str, label: str, status: str = "empty", **extra) -> dict[str, Any]:
    return {"layer": layer, "label": label, "source": "manual", "status": status, **extra}


def build_shot_view(
    shot: Shot,
    library: AssetLibrary,
    bible: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """展开单个 Shot 为 6 层视图。"""
    view: dict[str, Any] = {
        "shot_id": shot.shot_id,
        "section": shot.section,
        "text": shot.text,
        "scene_tags": list(shot.scene_tags),
        "estimated_duration": shot.estimated_duration,
        "status": shot.status,
        "layers": [],
    }

    # --- L0 背景 ---
    l0: dict[str, Any] = {"layer": "L0", "label": "背景"}
    bg_id = shot.background_id or ""
    if bg_id and not bg_id.startswith("MISSING:"):
        a = library.get(bg_id)
        if a is not None:
            l0.update({"source": "manual", "status": "ready",
                       "asset_id": a.id, "thumbnail": a.file,
                       "desc": a.description})
        else:
            l0.update({"source": "manual", "status": "missing",
                       "desc": f"库内未找到 {bg_id}"})
    elif bg_id.startswith("MISSING:"):
        l0.update({"source": "ai", "status": "pending",
                   "ai_prompt": bg_id.replace("MISSING:", "").strip(),
                   "desc": bg_id})
    else:
        l0.update({"source": "manual", "status": "empty",
                   "desc": "未选背景"})
    view["layers"].append(l0)

    # --- L1 IP 表情 ---
    l1: dict[str, Any] = {"layer": "L1", "label": "IP 表情"}
    expr_id = shot.expression_id or ""
    if expr_id and not expr_id.startswith("MISSING:"):
        a = library.get(expr_id)
        if a is not None:
            l1.update({"source": "manual", "status": "ready",
                       "asset_id": a.id, "thumbnail": a.file,
                       "desc": a.description, "meta": a.meta})
        else:
            l1.update({"source": "manual", "status": "missing",
                       "desc": f"库内未找到 {expr_id}"})
    else:
        # 用 scene_tags 推荐候选（同库里可多选）
        # 只用已审过的表情（only_reviewed=True）—— 用户审核存档作为唯一真相源
        candidates = library.search_expressions(shot.scene_tags, only_reviewed=True)
        if candidates:
            top = candidates[0]
            l1.update({
                "source": "manual", "status": "ready",
                "asset_id": top.id, "thumbnail": top.file,
                "desc": top.description, "meta": top.meta,
                "candidates": [_asset_view(c) for c in candidates[:4]],
            })
        else:
            l1.update({"source": "manual", "status": "empty",
                       "desc": "库内无匹配 scene_tags 的表情"})
    view["layers"].append(l1)

    # --- L2 场景元素（手工命中）+ L3 AI 生成（MISSING 的）---
    manual_elements: list[dict[str, Any]] = []
    ai_insets: list[dict[str, Any]] = []
    for idx, ins in enumerate(shot.element_insets):
        inset_payload = {
            "index": idx,
            "frame_style": ins.frame_style,
            "position": ins.position,
            "enter_offset": ins.enter_offset,
            "duration": ins.duration,
        }
        if ins.id.startswith("MISSING:"):
            desc = ins.id.replace("MISSING:", "").strip()
            # 根据已有产物判定状态
            if ins.video_url:
                status = "video_ready"
            elif ins.image_url:
                status = "image_ready"
            else:
                status = "pending"
            ai_insets.append({
                **inset_payload,
                "source": "ai",
                "status": status,
                "ai_prompt": desc,
                "desc": desc,
                "image_url": ins.image_url,
                "image_prompt": ins.image_prompt,
                "video_url": ins.video_url,
                "video_task_id": ins.video_task_id,
                "cost": (ins.image_cost or 0) + (ins.video_cost or 0),
                "provider": "ark" if ins.image_url else None,
            })
        else:
            a = library.get(ins.id)
            if a is not None:
                manual_elements.append({
                    **inset_payload,
                    "source": "manual",
                    "status": "ready",
                    "asset_id": a.id,
                    "thumbnail": a.file,
                    "desc": a.description,
                })
            else:
                manual_elements.append({
                    **inset_payload,
                    "source": "manual",
                    "status": "missing",
                    "desc": f"库内未找到 {ins.id}",
                })

    view["layers"].append({
        "layer": "L2",
        "label": "场景元素",
        "source": "manual",
        "items": manual_elements,
        "status": "ready" if manual_elements else "empty",
    })
    view["layers"].append({
        "layer": "L3",
        "label": "AI 生成兜底",
        "source": "ai",
        "items": ai_insets,
        "status": "pending" if ai_insets else "empty",
    })

    # --- L4 配音 (MOCK) ---
    view["layers"].append({
        "layer": "L4",
        "label": "配音",
        "source": "mock",
        "status": "mock",
        "desc": f"预估 {shot.estimated_duration:.1f}s · MiniMax 女声（MOCK）",
        "duration": shot.estimated_duration,
    })

    # --- L5 字幕 (MOCK) ---
    view["layers"].append({
        "layer": "L5",
        "label": "字幕",
        "source": "mock",
        "status": "mock",
        "desc": shot.text[:40] + ("…" if len(shot.text) > 40 else ""),
        "subtitle_style": shot.subtitle_style,
    })

    # 就绪度
    ready_count = sum(
        1 for l in view["layers"]
        if (l.get("status") == "ready"
            or (l["layer"] == "L3" and not l["items"])
            or l.get("source") == "mock")
    )
    view["ready_count"] = ready_count
    view["layer_count"] = 6
    l3_pending = any(x["status"] == "pending" for x in view["layers"][3]["items"])
    view["fully_ready"] = (
        view["layers"][0]["status"] == "ready"
        and view["layers"][1]["status"] == "ready"
        and not l3_pending
    )
    return view


def build_table_view(
    table: ShotTable,
    library: AssetLibrary,
    bible: dict[str, Any] | None = None,
) -> dict[str, Any]:
    shots_view = [build_shot_view(s, library, bible) for s in table.shots]
    return {
        "video_id": table.video_id,
        "title": table.title,
        "topic": table.topic,
        "status": table.status,
        "total_duration_estimated": table.total_duration_estimated,
        "shots": shots_view,
        "shots_ready": sum(1 for s in shots_view if s["fully_ready"]),
        "shots_total": len(shots_view),
    }


# ============================================================
# 素材页任务队列（批 3c）—— 不再段×层矩阵，而是"AI 该干的活"清单
# ============================================================

def auto_label_expressions(
    asset_ids: list[str],
    *,
    library: AssetLibrary,
    on_progress=None,
) -> list[dict[str, Any]]:
    """AI 打标。表情类用情绪闭集；元素类用元素类别闭集。

    写到 meta.scene_tags_ai / meta.categories_ai，不覆盖最终消费字段。
    返回每条 {asset_id, ok, kind, predicted: [...], rationale, cost, error}.
    """
    from pathlib import Path as _Path
    from pipeline.shot_table import SCENE_TAGS, ELEMENT_CATEGORIES
    from pipeline.tools.ark_client import classify_image

    emotion_closed = sorted(list(SCENE_TAGS))
    element_closed = sorted(list(ELEMENT_CATEGORIES))
    repo_root = _Path(__file__).resolve().parents[2]
    out: list[dict[str, Any]] = []
    for i, aid in enumerate(asset_ids, start=1):
        a = library.get(aid)
        if a is None:
            r = {"asset_id": aid, "ok": False, "error": "not found"}
            out.append(r)
            if on_progress:
                on_progress(i, len(asset_ids), r)
            continue
        if a.file.startswith("http"):
            r = {"asset_id": aid, "ok": False, "error": "remote URL skipped"}
            out.append(r)
            if on_progress:
                on_progress(i, len(asset_ids), r)
            continue

        is_element = a.kind == "elements"
        closed = element_closed if is_element else emotion_closed
        full = repo_root / a.file
        res = classify_image(
            full,
            closed_set=closed,
            description_hint=a.description or "",
        )
        # 写到 catalog（按 kind 分字段）
        if res["ok"] and res["scene_tags"]:
            patch = ({
                "categories_ai": res["scene_tags"],
                "categories_ai_rationale": res["rationale"],
            } if is_element else {
                "scene_tags_ai": res["scene_tags"],
                "scene_tags_ai_rationale": res["rationale"],
            })
            library.update(aid, meta_patch=patch, save=False)
        r = {
            "asset_id": aid, "kind": a.kind, "ok": res["ok"],
            "predicted": res["scene_tags"],
            "rationale": res["rationale"],
            "cost": res["cost"], "error": res.get("error"),
        }
        out.append(r)
        if on_progress:
            on_progress(i, len(asset_ids), r)
    library.save()
    return out


def build_task_queue(
    table: ShotTable,
    library: AssetLibrary,
    bible: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从 ShotTable 派生 3 个 AI 任务队列：配音 / 生图 / 生视频。

    - 配音：每段一个任务（MVP mock）
    - 生图：每段 element_insets 里 id 以 "MISSING:" 开头的项 → 一任务
            + 每段 background_id 以 "MISSING:" 开头 → 一任务（本批暂不实装背景 AI 生）
    - 生视频：已生过图（image_url 存在）且 video_task_id 为空 → 可升级
              已提交视频（video_task_id 存在）→ 队列里显示状态
    """
    bible = bible or {}
    audio_tasks: list[dict[str, Any]] = []
    image_tasks: list[dict[str, Any]] = []
    video_tasks: list[dict[str, Any]] = []

    for shot in table.shots:
        # --- 配音 ---
        audio_url = None
        if shot.audio_clip_path:
            # audio_clip_path 现在是"data/audio/<vid>/<sid>.mp3"（相对仓库根）
            # 前端通过 /data 挂载访问
            if shot.audio_clip_path.startswith("data/"):
                audio_url = "/" + shot.audio_clip_path
            elif shot.audio_clip_path.startswith("http"):
                audio_url = shot.audio_clip_path
            elif shot.audio_clip_path.startswith("mock://"):
                audio_url = None  # 旧 mock 数据不暴露给播放器
            else:
                audio_url = shot.audio_clip_path
        audio_tasks.append({
            "shot_id": shot.shot_id,
            "section": shot.section,
            "text": shot.text,
            "duration_estimated": shot.estimated_duration,
            "voice_id": bible.get("voice_id"),
            "audio_url": audio_url,
            "audio_clip_path": shot.audio_clip_path,
            "status": "ready" if (shot.audio_clip_path and not shot.audio_clip_path.startswith("mock://")) else "pending",
            "source": "edge_tts" if shot.audio_clip_path and not shot.audio_clip_path.startswith("mock://") else "pending",
        })

        # --- 生图（每个 MISSING inset 一任务）---
        for idx, ins in enumerate(shot.element_insets):
            if not ins.id.startswith("MISSING:"):
                continue
            desc = ins.id.replace("MISSING:", "").strip()
            # 图片状态
            if ins.image_url:
                img_status = "image_ready"
            else:
                img_status = "pending"
            image_tasks.append({
                "shot_id": shot.shot_id,
                "section": shot.section,
                "inset_index": idx,
                "frame_style": ins.frame_style,
                "position": ins.position,
                "intent": desc,
                "image_url": ins.image_url,
                "image_prompt": ins.image_prompt,
                "cost": ins.image_cost or 0.0,
                "status": img_status,
                "provider": "ark" if ins.image_url else None,
            })

            # --- 视频（只要已生过图就出现在视频队列，状态为 not_requested / running / ready）---
            if ins.image_url:
                if ins.video_url:
                    v_status = "video_ready"
                elif ins.video_task_id:
                    v_status = "running"
                else:
                    v_status = "not_requested"
                video_tasks.append({
                    "shot_id": shot.shot_id,
                    "section": shot.section,
                    "inset_index": idx,
                    "intent": desc,
                    "image_url": ins.image_url,     # 上游
                    "video_url": ins.video_url,
                    "video_task_id": ins.video_task_id,
                    "cost": ins.video_cost or 0.0,
                    "status": v_status,
                })

    def _count_ready(tasks, ready_values):
        return sum(1 for t in tasks if t["status"] in ready_values)

    audio_ready = _count_ready(audio_tasks, {"ready"})
    image_ready = _count_ready(image_tasks, {"image_ready"})
    video_ready = _count_ready(video_tasks, {"video_ready"})
    total_cost = sum(t.get("cost", 0.0) for t in image_tasks + video_tasks)

    return {
        "video_id": table.video_id,
        "title": table.title,
        "shots_total": len(table.shots),
        "audio": {
            "tasks": audio_tasks,
            "ready": audio_ready, "total": len(audio_tasks),
        },
        "image": {
            "tasks": image_tasks,
            "ready": image_ready, "total": len(image_tasks),
        },
        "video": {
            "tasks": video_tasks,
            "ready": video_ready, "total": len(video_tasks),
        },
        "total_cost_cny": round(total_cost, 2),
        "bible_voice_id": bible.get("voice_id"),
        "bible_subtitle_style": bible.get("subtitle_style"),
    }
