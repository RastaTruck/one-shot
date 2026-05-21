"""热点工坊 v2 · 信号 → 方向库 → AI 完整立意 → 三模式创建项目。

工作流：
  1. 左栏：信号流（按层 timely/ammo/longtail 切 tab）
  2. 中栏：方向库 14 个（金融科普 8 + 经济分析 6）+ AI 推荐 2-3 个加 ⭐
  3. 右栏：AI 完整立意（用户只审不填）+ 三模式（一二布布/Outhan/新闻口播）选一 + 一键创建项目

vs v1 的区别：
- 旧：AI 一次性出 4 个候选角度 → 用户选一个 → 用户填空 5 个字段 → 送入
- 新：用户选方向 → AI 找补真实数据 → AI 出完整立意 → 用户选模式 → 创建对应管线项目
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

import json as _json
import threading

from pipeline import state as _state
from pipeline.hotspot_forge.brief_generator import (
    find_supplements,
    generate_brief,
    recommend_directions,
)
from pipeline.hotspot_forge.direction_library import DIRECTIONS, by_key
from pipeline.hotspot_forge.fetcher import fetch_all
from pipeline.hotspot_forge.schema import SOURCE_LAYERS
from pipeline.hotspot_forge.script_translator import translate as _translate_brief
from pipeline.hotspot_forge.storage import (
    create_script_job,
    fetch_log_summary,
    get_script_job,
    list_recent_signals,
    update_script_job,
)


router = APIRouter()

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


# ---------- 元数据 ----------

LAYER_META: dict[str, dict[str, Any]] = {
    "timely":   {"label": "时效热搜", "desc": "微博/知乎/百度 当日热度",
                 "icon": "flame",   "color": "rose"},
    "ammo":     {"label": "专业弹药", "desc": "财经媒体 RSS（FT/36氪/WSJ 等）",
                 "icon": "library", "color": "teal"},
    "longtail": {"label": "长尾雷达", "desc": "HN + Google Trends 英文圈早期信号",
                 "icon": "radar",   "color": "violet"},
}

TRACK_META: dict[str, dict[str, Any]] = {
    "finance_kp":     {"label": "金融科普", "icon": "graduation-cap", "tone": "blue"},
    "macro_analysis": {"label": "经济分析", "icon": "trending-up",    "tone": "emerald"},
}

# 三种制作模式（对应已有三条管线）
MODE_META: dict[str, dict[str, Any]] = {
    "yierbubu": {
        "label": "一二布布 IP",
        "desc":  "金融科普卡通 IP · 选题→文案→分镜→装配→剪辑",
        "icon":  "puzzle",
        "tone":  "amber",
        "redirect_template": "/project/{video_id}/topic",
    },
    "outhan": {
        "label": "Outhan 通用",
        "desc":  "单条创意视频 · 选题→首帧→台本→分段→配音→打包",
        "icon":  "film",
        "tone":  "blue",
        "redirect_template": "/project/{video_id}/outhan",
    },
    "news": {
        "label": "新闻口播",
        "desc":  "数字主播 lipsync · 选题→文案→主播→成片要素→合成→导出",
        "icon":  "mic",
        "tone":  "emerald",
        "redirect_template": "/project/{video_id}/news/topic",
    },
}


def _humanize_ago(ts: float | None) -> str:
    if not ts:
        return ""
    delta = time.time() - ts
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta/60)} 分钟前"
    if delta < 86400:
        return f"{int(delta/3600)} 小时前"
    return f"{int(delta/86400)} 天前"


def _layer_counts() -> dict[str, int]:
    out: dict[str, int] = {layer: 0 for layer in SOURCE_LAYERS}
    for layer in SOURCE_LAYERS:
        rows = list_recent_signals(layer=layer, limit=999)
        out[layer] = len(rows)
    return out


# ---------- 页面 ----------

@router.get("/forge", response_class=HTMLResponse)
async def page_forge(request: Request):
    counts = _layer_counts()
    last_fetch = fetch_log_summary(limit=1)
    last_fetch_at = _humanize_ago(last_fetch[0]["started_at"]) if last_fetch else "从未"
    # 按 track 分组方向库
    directions_by_track = {
        "finance_kp": [d for d in DIRECTIONS if d["track"] == "finance_kp"],
        "macro_analysis": [d for d in DIRECTIONS if d["track"] == "macro_analysis"],
    }
    return _TEMPLATES.TemplateResponse("forge.html", {
        "request": request,
        "active_nav": "forge",
        "layer_meta": LAYER_META,
        "track_meta": TRACK_META,
        "mode_meta": MODE_META,
        "directions_by_track": directions_by_track,
        "layer_counts": counts,
        "layer_order": list(SOURCE_LAYERS),
        "last_fetch_at": last_fetch_at,
        "current_video": None,
        "steps_view": [],
        "progress": None,
    })


# ---------- API · 信号列表 ----------

@router.get("/api/forge/signals")
async def api_signals(layer: Optional[str] = None, source: Optional[str] = None,
                      limit: int = 50) -> JSONResponse:
    rows = list_recent_signals(layer=layer, source=source, limit=limit)
    out: list[dict] = []
    for r in rows:
        out.append({
            "signal_id": r["signal_id"],
            "source": r["source"],
            "source_layer": r["source_layer"],
            "title": r["title"],
            "url": r["url"],
            "summary": r["summary"],
            "category_hint": r["category_hint"],
            "lang": r["lang"],
            "published_at": r["published_at"],
            "fetched_at": r["fetched_at"],
            "ago": _humanize_ago(r["published_at"] or r["fetched_at"]),
        })
    return JSONResponse({"signals": out, "total": len(out)})


@router.post("/api/forge/fetch")
async def api_fetch() -> JSONResponse:
    result = fetch_all(verbose=False)
    return JSONResponse({
        "ok": True,
        "total_fetched": result["total_fetched"],
        "total_new": result["total_new"],
        "by_source": result["by_source"],
    })


# ---------- API · 方向推荐（选信号后，AI 推 2-3 个 ⭐ 方向） ----------

@router.get("/api/forge/recommend-directions")
async def api_recommend(signal_id: str) -> JSONResponse:
    """AI 看信号推 2-3 个最匹配的方向。返回 [{key, reason}, ...]"""
    rows = list_recent_signals(limit=500)
    target = next((r for r in rows if r["signal_id"] == signal_id), None)
    if not target:
        raise HTTPException(404, "signal not found")
    recs = recommend_directions(dict(target), limit=3)
    if recs is None:
        # LLM 不可用 fallback：默认推前 2 个金融科普
        return JSONResponse({
            "signal": dict(target),
            "recommendations": [
                {"key": "concept_unpack", "reason": "通用首选（LLM 不可用降级）"},
                {"key": "policy_interpret", "reason": "通用首选（LLM 不可用降级）"},
            ],
            "stub": True,
        })
    return JSONResponse({
        "signal": dict(target),
        "recommendations": recs,
        "stub": False,
    })


# ---------- API · 生成完整立意（信号 + 方向 → AI 找补 + 立意） ----------

@router.post("/api/forge/brief")
async def api_brief(payload: dict[str, Any]) -> JSONResponse:
    """信号 + 方向 → 找补内容 + 完整立意文档。"""
    signal_id = (payload.get("signal_id") or "").strip()
    direction_key = (payload.get("direction_key") or "").strip()
    if not signal_id or not direction_key:
        raise HTTPException(400, "signal_id + direction_key required")

    direction = by_key(direction_key)
    if not direction:
        raise HTTPException(400, f"unknown direction_key: {direction_key}")

    rows = list_recent_signals(limit=500)
    target = next((r for r in rows if r["signal_id"] == signal_id), None)
    if not target:
        raise HTTPException(404, "signal not found")
    signal = dict(target)

    # Step 1: 找补（池内 + 联网 stub）
    supplements = find_supplements(signal)

    # Step 2: AI 生成完整立意
    result = generate_brief(signal, direction_key, supplements)
    if result is None:
        return JSONResponse({
            "ok": False,
            "error": "LLM 不可用或返回格式异常",
            "supplements": supplements,
        })

    return JSONResponse({
        "ok": True,
        "signal": signal,
        "direction": direction,
        "supplements": supplements,
        "brief": result["brief"],
        "provider": result["provider"],
        "cost": result["cost"],
    })


# ---------- API · 创建项目（按模式分发到三条管线） ----------

def _brief_to_notes(brief: dict, signal: dict, direction: dict, supplements: dict) -> str:
    """把立意转成 sources.notes 用的可读结构化文本。"""
    lines = [
        f"【立意标题】{brief.get('title','')}",
        f"【赛道】{brief.get('direction_label','')} / {brief.get('direction_key','')}",
        f"【信号来源】{signal.get('source','')} · {signal.get('title','')}",
        "",
        f"【核心论点】",
        brief.get("core_argument", ""),
        "",
        f"【受众认知起点】",
        brief.get("audience_baseline", ""),
        "",
        "【信息支撑】",
    ]
    for s in brief.get("info_support", []) or []:
        ref = f" [{s.get('ref_source','')} · {s.get('ref_title','')}]" if s.get("ref_source") else ""
        lines.append(f"- {s.get('point','')}{ref}")
    lines.append("")
    lines.append("【关键数据/案例】")
    for k in brief.get("key_data", []) or []:
        src = f" ({k.get('source','')})" if k.get("source") else ""
        lines.append(f"- {k.get('label','')}: {k.get('value','')}{src}")
    lines.append("")
    lines.append("【情绪钩子】")
    lines.append(brief.get("emotion_hook", ""))
    lines.append("")
    if supplements.get("pool_refs"):
        lines.append("【找补 · 信号池相关】")
        for r in supplements["pool_refs"][:5]:
            lines.append(f"- [{r.get('source','')}] {r.get('title','')}")
        lines.append("")
    return "\n".join(lines).strip()


def _next_video_id() -> str:
    videos = _state.list_videos()
    max_n = 0
    for v in videos:
        vid = v.video_id or ""
        if vid.startswith("V") and vid[1:].isdigit():
            max_n = max(max_n, int(vid[1:]))
    return f"V{max_n + 1:04d}"


# ---------- 后台 · 立意 → 文案 异步任务 ----------

def _write_script_to_downstream(video_id: str, mode: str, result: dict) -> None:
    """把生成出的文案落到对应管线的下游存储。"""
    title = result.get("title", "")
    script = result.get("script", "")
    units = result.get("units", []) or []
    char_count = len("".join(c for c in script if not c.isspace()))

    if mode == "news":
        try:
            from pipeline import news_meta as _news_meta
            _news_meta.update_news_data(
                video_id,
                script_title=title,
                script_text=script,
                script_char_count=char_count,
                script_units=units,
                current_step=3,  # 文案 step 2 完成，进入 step 3 镜头模板
            )
        except Exception as e:
            print(f"[forge] news_meta script write failed: {e}")
    elif mode in ("yierbubu", "outhan"):
        # MVP：把文案追加到 sources.notes 头部，让下游 script 步骤能看到
        try:
            cur = _state.load_project_sources(video_id) or {}
            existing_notes = cur.get("notes") or ""
            forge_script_block = (
                f"【AI 生成文案（来自工坊立意）】\n"
                f"标题: {title}\n\n{script}\n\n"
                f"——以下是原立意——\n\n"
            )
            cur["notes"] = forge_script_block + existing_notes
            cur["forge_script"] = {
                "title": title,
                "script": script,
                "units": units,
                "references_used": result.get("references_used", []),
            }
            _state.save_project_sources(video_id, cur)
        except Exception as e:
            print(f"[forge] sources script write failed ({mode}): {e}")


def _run_script_job_async(video_id: str, mode: str,
                          brief: dict, supplements: dict, signal: dict) -> None:
    """后台线程跑文案转译。失败不抛出（写到 job.error）。"""
    import time as _t
    update_script_job(video_id, status="running")
    try:
        result = _translate_brief(brief, supplements, signal, mode)
        if result is None:
            update_script_job(video_id, status="failed",
                              error="LLM 不可用或返回格式异常",
                              completed_at=_t.time())
            return
        update_script_job(
            video_id,
            status="completed",
            result_json=_json.dumps(result, ensure_ascii=False),
            provider=result.get("provider"),
            cost=result.get("cost", 0),
            completed_at=_t.time(),
        )
        _write_script_to_downstream(video_id, mode, result)
    except Exception as e:
        update_script_job(video_id, status="failed",
                          error=f"{type(e).__name__}: {e}",
                          completed_at=_t.time())


@router.get("/api/forge/script-job/{video_id}")
async def api_script_job(video_id: str) -> JSONResponse:
    """前端轮询查文案生成进度。返回 status + result（completed 时）。"""
    job = get_script_job(video_id)
    if not job:
        return JSONResponse({"ok": False, "error": "no job for this video"})
    result = None
    if job.get("result_json"):
        try:
            result = _json.loads(job["result_json"])
        except Exception:
            result = None
    return JSONResponse({
        "ok": True,
        "video_id": video_id,
        "mode": job["mode"],
        "status": job["status"],
        "error": job.get("error"),
        "provider": job.get("provider"),
        "cost": job.get("cost"),
        "created_at": job.get("created_at"),
        "completed_at": job.get("completed_at"),
        "result": result,
    })


@router.post("/api/forge/create-project")
async def api_create_project(payload: dict[str, Any]) -> JSONResponse:
    """按选定模式（yierbubu/outhan/news）创建项目 + 预填立意。"""
    mode = (payload.get("mode") or "").strip()
    if mode not in MODE_META:
        raise HTTPException(400, f"mode must be one of {list(MODE_META)}")

    signal_id = (payload.get("signal_id") or "").strip()
    direction_key = (payload.get("direction_key") or "").strip()
    brief = payload.get("brief") or {}
    if not brief.get("title"):
        raise HTTPException(400, "brief.title required")

    # 重新拉信号 / 方向 / 找补（防止前端篡改）
    rows = list_recent_signals(limit=500)
    target = next((r for r in rows if r["signal_id"] == signal_id), None)
    signal = dict(target) if target else {"title": "", "source": "manual"}
    direction = by_key(direction_key) or {"label": "", "key": direction_key, "track": brief.get("track", "")}
    supplements = find_supplements(signal) if target else {"pool_refs": [], "web_refs": []}

    video_id = _next_video_id()
    title = (brief.get("title") or f"未命名 {video_id}")[:60]

    # 创建 video（按 mode 设 project_type）
    _state.create_video(video_id=video_id, title=title, project_type=mode)

    # 通用：把完整立意 + 找补 + 信号都存 sources（不仅是 notes 文本，还有结构化 forge_brief）
    notes = _brief_to_notes(brief, signal, direction, supplements)
    forge_brief_payload = {
        "title": brief.get("title", ""),
        "core_argument": brief.get("core_argument", ""),
        "audience_baseline": brief.get("audience_baseline", ""),
        "info_support": brief.get("info_support", []) or [],
        "key_data": brief.get("key_data", []) or [],
        "emotion_hook": brief.get("emotion_hook", ""),
        "direction_label": brief.get("direction_label", "") or direction.get("label", ""),
        "direction_key": brief.get("direction_key", "") or direction_key,
        "track": brief.get("track", "") or direction.get("track", ""),
    }
    forge_supplements_payload = {
        "pool_refs": [
            {k: r.get(k) for k in ("signal_id", "source", "title", "url", "summary", "published_at")}
            for r in (supplements.get("pool_refs") or [])[:5]
        ],
        "web_refs": supplements.get("web_refs", []) or [],
    }
    forge_signal_payload = {
        "signal_id": signal.get("signal_id", ""),
        "source": signal.get("source", ""),
        "title": signal.get("title", ""),
        "url": signal.get("url", ""),
    }
    _state.save_project_sources(video_id, {
        "keywords": "",
        "notes": notes,
        "extra": "",
        "references": [{
            "type": "hotspot_forge",
            "signal_id": signal_id,
            "direction_key": direction_key,
            "track": direction.get("track", ""),
        }],
        # 完整立意 + 找补：让下游 topic/script 模板能渲染 forge_brief banner
        "forge_brief": forge_brief_payload,
        "forge_supplements": forge_supplements_payload,
        "forge_signal": forge_signal_payload,
    })

    # mode 专属预填
    if mode == "news":
        # 把立意 + brief 完整结构存 news_meta，让 step_news_topic / step_news_script 渲染 banner
        try:
            from pipeline import news_meta as _news_meta
            _news_meta.update_news_data(
                video_id,
                topic_picked={
                    "title": brief["title"],
                    "summary": brief.get("core_argument", ""),
                    "angle": direction.get("label", ""),
                },
                topic_audience=brief.get("audience_baseline", "")[:120],
                duration_sec=60,
                current_step=2,  # 跳到文案步骤
                forge_brief=forge_brief_payload,
                forge_supplements=forge_supplements_payload,
                forge_signal=forge_signal_payload,
            )
        except Exception as e:
            print(f"[forge] news_meta update failed: {e}")

    # 创建立意 → 文案 异步 job + 后台跑（立刻返回 redirect，不阻塞 30-60s）
    # 跳转目标改为 step 2 (script) 而非 step 1 (topic)——立意已决定选题，下游应直接进文案
    create_script_job(video_id, mode, forge_brief_payload,
                      forge_supplements_payload, forge_signal_payload)
    threading.Thread(
        target=_run_script_job_async,
        args=(video_id, mode, forge_brief_payload, forge_supplements_payload, forge_signal_payload),
        daemon=True,
    ).start()

    # script 步骤路由表（按 mode 跳到对应文案步骤而非 topic）
    script_redirect_template = {
        "yierbubu": "/project/{video_id}/script",
        "outhan":   "/project/{video_id}/outhan/script",
        "news":     "/project/{video_id}/news/script",
    }.get(mode, MODE_META[mode]["redirect_template"])
    redirect_url = script_redirect_template.format(video_id=video_id)
    return JSONResponse({
        "ok": True,
        "video_id": video_id,
        "title": title,
        "mode": mode,
        "redirect_url": redirect_url,
    })
