"""文案 Agent：一次生成 3 套完整文案 + 单字段 refine。

- 有 LLM 密钥：走真 API + 任务日志
- 无密钥：返回一套基于输入主题的 stub，让前端 UI 仍可演示
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline import state
from pipeline.tools import llm_client

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "config" / "prompts"


def _read_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _fill(template: str, **kw: Any) -> str:
    """安全替换：仅把 {key} 换成值，不影响模板里的 JSON 花括号。"""
    out = template
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _ensure_video_row(video_id: str, title: str) -> None:
    """API 可能传进来 mock video_id（没进过 DB），补一条占位 row 让 tasks 外键通过。"""
    if state.get_video(video_id) is None:
        state.create_video(video_id, title=title, topic=title)


def _stub_drafts(topic: str, style_name: str) -> list[dict[str, Any]]:
    """无密钥时的演示数据 —— 3 套差异化 stub。"""
    return [
        {
            "draft_id": "d1", "label": "反常识型", "tone": "犀利",
            "hook": f"关于「{topic}」，99% 的人理解反了。",
            "body": [
                {"step": "S1", "role": "共鸣", "text": f"你以为「{topic}」只是一个小众话题？打开手机，每天都有人在为它付真金白银。"},
                {"step": "S2", "role": "反转", "text": "但真相是，大多数人看到的只是表面——底层逻辑和你想的完全相反，这一点专业人士从不愿意说破。"},
                {"step": "S3", "role": "论证", "text": "回到一个最朴素的问题：钱是怎么流动的？一旦想明白这个，你就会发现自己长期站在了天平的错误一侧。"},
            ],
            "golden": "看懂规则的人，才不是赌徒。", "cta": "点关注，下期扒更硬的内幕。",
            "est_chars": 140, "est_duration_sec": 35,
        },
        {
            "draft_id": "d2", "label": "故事化", "tone": "叙事",
            "hook": f"2019 年，一个 27 岁的年轻人因为「{topic}」翻身。",
            "body": [
                {"step": "S1", "role": "案例", "text": "他不是富二代，也没学过金融，只是在一个深夜刷到了一张不起眼的图，把自己存了 5 年的 8 万块全押了进去。"},
                {"step": "S2", "role": "原理", "text": f"他赌的，就是今天要讲的「{topic}」——它的规则并不复杂，但敢落地的人不到万分之一，因为它和直觉完全对冲。"},
                {"step": "S3", "role": "反转", "text": "两年后他翻了 6 倍，但真正改变他的不是钱，是他终于理解了：市场奖励的从来不是努力，是结构性的认知差。"},
            ],
            "golden": "大多数人输的，是自己的常识。", "cta": "想听完整故事？下集继续。",
            "est_chars": 170, "est_duration_sec": 42,
        },
        {
            "draft_id": "d3", "label": "数据驱动", "tone": "理性",
            "hook": f"「{topic}」相关的钱，一年规模 1.2 万亿。",
            "body": [
                {"step": "S1", "role": "数据", "text": "这个数字什么概念？相当于中国三个一线城市全年社会零售总额加起来。但 80% 的普通人，对它的认知停留在一句话。"},
                {"step": "S2", "role": "原理", "text": f"「{topic}」的本质是一套定价机制——三个变量、两条曲线就能拆开它。今天用最短的 30 秒讲清楚。"},
                {"step": "S3", "role": "风险", "text": "注意：看懂机制不等于能赚到钱。真正决定盈亏的，是你在什么位置进场、以什么纪律退场，这才是大多数人栽的地方。"},
            ],
            "golden": "数字不会说谎，但会被误读。", "cta": "收藏，回头慢慢看。",
            "est_chars": 170, "est_duration_sec": 42,
        },
    ]


def generate_three_drafts(video_id: str, topic: str, style: dict[str, Any]) -> dict[str, Any]:
    """返回 {"drafts": [...], "provider": str, "cost": float}"""
    _ensure_video_row(video_id, topic)
    system = _fill(
        _read_prompt("script_gen.txt"),
        style_name=style.get("name", "默认"),
        style_desc=style.get("desc", ""),
        style_sample=style.get("sample", ""),
    )
    user = f"选题：{topic}"
    task_id = state.start_task(video_id, agent="script_writer", input_data={"topic": topic})

    result = llm_client.call_json_with_fallback(system, user)
    if result is None:
        drafts = _stub_drafts(topic, style.get("name", "默认"))
        state.finish_task(task_id, success=True, output={"mode": "stub", "n": len(drafts)}, cost_cny=0.0)
        return {"drafts": drafts, "provider": "stub", "cost": 0.0}

    provider, content, cost = result
    try:
        parsed = json.loads(content)
        drafts = parsed.get("drafts") or []
        assert len(drafts) == 3
    except (json.JSONDecodeError, AssertionError, TypeError) as e:
        state.finish_task(task_id, success=False, error=f"parse error: {e}", cost_cny=cost)
        drafts = _stub_drafts(topic, style.get("name", "默认"))
        return {"drafts": drafts, "provider": "stub", "cost": 0.0}

    state.finish_task(task_id, success=True, output={"n": 3}, cost_cny=cost)
    # 写回 provider 字段（start_task 创建时还不知道）
    import sqlite3  # noqa
    with state.get_conn() as conn:
        conn.execute("UPDATE tasks SET provider = ? WHERE task_id = ?", (provider, task_id))
    return {"drafts": drafts, "provider": provider, "cost": cost}


def refine_field(
    video_id: str, field_label: str, current_text: str, instruction: str,
    style: dict[str, Any], context: dict[str, Any],
) -> dict[str, Any]:
    """单字段 AI 改写。返回 {"text": str, "provider": str, "cost": float}"""
    _ensure_video_row(video_id, context.get("topic", "(untitled)"))
    system = _fill(
        _read_prompt("script_refine.txt"),
        field_label=field_label,
        current_text=current_text,
        instruction=instruction or "换一个更有张力的版本",
        style_name=style.get("name", "默认"),
        ctx_hook=context.get("hook", ""),
        ctx_body=" / ".join(context.get("body", [])),
        ctx_golden=context.get("golden", ""),
        ctx_cta=context.get("cta", ""),
    )
    task_id = state.start_task(video_id, agent="script_refine", input_data={"field": field_label})

    result = llm_client.call_json_with_fallback(system, "请按上述要求改写。")
    if result is None:
        # 无密钥：做个简单变体
        variants = [
            f"{current_text}（换个说法）",
            f"再说一遍：{current_text}",
            current_text.rstrip("。.!？?") + "——这次说透。",
        ]
        import hashlib
        pick = variants[int(hashlib.md5(current_text.encode()).hexdigest(), 16) % len(variants)]
        state.finish_task(task_id, success=True, output={"mode": "stub"}, cost_cny=0.0)
        return {"text": pick, "provider": "stub", "cost": 0.0}

    provider, content, cost = result
    try:
        parsed = json.loads(content)
        text = parsed.get("text", "").strip()
        assert text
    except (json.JSONDecodeError, AssertionError, TypeError) as e:
        state.finish_task(task_id, success=False, error=f"parse error: {e}", cost_cny=cost)
        return {"text": current_text, "provider": provider, "cost": cost}

    state.finish_task(task_id, success=True, output={"len": len(text)}, cost_cny=cost)
    with state.get_conn() as conn:
        conn.execute("UPDATE tasks SET provider = ? WHERE task_id = ?", (provider, task_id))
    return {"text": text, "provider": provider, "cost": cost}


def generate_full_script(
    video_id: str, topic: str, style: dict[str, Any],
    outline: dict[str, Any], duration_sec: int,
    pace_chars_per_sec: float = 5.5,
) -> dict[str, Any]:
    """根据已确认的大纲扩写成完整文稿。
    默认 5.5 字/秒（科普正常语速）；小林/回形针式密度可调 6；央视式严肃可调 4.5。
    返回 {full, provider, cost}
    """
    _ensure_video_row(video_id, topic)
    body = outline.get("body") or [{}, {}, {}]
    target_chars = int(duration_sec * pace_chars_per_sec)
    # ±15% 容忍区间
    min_chars = int(target_chars * 0.85)
    max_chars = int(target_chars * 1.15)
    system = _fill(
        _read_prompt("script_full.txt"),
        style_name=style.get("name", "默认"),
        style_desc=style.get("desc", ""),
        style_sample=style.get("sample", ""),
        topic=topic,
        outline_hook=outline.get("hook", ""),
        s1_role=body[0].get("role", "S1"), s1_text=body[0].get("text", ""),
        s2_role=body[1].get("role", "S2"), s2_text=body[1].get("text", ""),
        s3_role=body[2].get("role", "S3"), s3_text=body[2].get("text", ""),
        outline_golden=outline.get("golden", ""),
        outline_cta=outline.get("cta", ""),
        duration_sec=duration_sec,
        pace=pace_chars_per_sec,
        min_chars=min_chars,
        max_chars=max_chars,
    )
    user = "请按上述大纲生成完整口播文稿。"
    task_id = state.start_task(
        video_id, agent="script_full",
        input_data={"outline_id": outline.get("draft_id"), "duration_sec": duration_sec},
    )
    # 超时随时长线性放大：1min≈120s，3min≈300s
    gen_timeout = max(120, 60 + duration_sec * 2)
    result = llm_client.call_json_with_fallback(system, user, timeout=gen_timeout)
    if result is None:
        # stub
        full = {
            "title": topic,
            "duration_sec": duration_sec,
            "pace_chars_per_sec": pace_chars_per_sec,
            "total_chars": target_chars,
            "framework": {
                "one_liner": f"[stub] {topic}",
                "logical_arc": ["认知前提", "暴露反例", "引入机制", "落到行动"],
                "info_points": ["[stub] 具体数字", "[stub] 真实人物"],
            },
            "paragraphs": [
                {"id": f"p{i+1}", "role": ["钩子","共鸣","原理","金句","CTA"][i % 5],
                 "purpose": "stub 占位",
                 "text": f"[stub 段 {i+1}] " + (body[i % 3].get("text", "") if i < 3 else outline.get("golden", "")),
                 "duration_sec": 8.0,
                 "info_density": "低"}
                for i in range(max(4, duration_sec // 15))
            ],
            "summary": "无 API key 时的 stub 演示版本",
        }
        state.finish_task(task_id, success=True, output={"mode": "stub"}, cost_cny=0.0)
        return {"full": full, "provider": "stub", "cost": 0.0}

    provider, content, cost = result
    try:
        parsed = json.loads(content)
        assert "paragraphs" in parsed and isinstance(parsed["paragraphs"], list)
        for i, p in enumerate(parsed["paragraphs"]):
            p.setdefault("id", f"p{i+1}")
            p.setdefault("role", "段落")
            p.setdefault("purpose", "")
            p.setdefault("text", "")
            p.setdefault("info_density", "中")
            # 永远重算 duration（不相信 AI 报的）
            p["duration_sec"] = round(len(p["text"]) / pace_chars_per_sec, 1)
        parsed.setdefault("title", topic)
        parsed.setdefault("duration_sec", duration_sec)
        parsed.setdefault("pace_chars_per_sec", pace_chars_per_sec)
        # 永远重算总字数（AI 经常虚报）
        actual_chars = sum(len(p["text"]) for p in parsed["paragraphs"])
        parsed["total_chars"] = actual_chars
        parsed.setdefault("framework", {"one_liner": "", "logical_arc": [], "info_points": []})
        parsed.setdefault("summary", "")
    except (json.JSONDecodeError, AssertionError, KeyError, TypeError) as e:
        state.finish_task(task_id, success=False, error=f"parse: {e}", cost_cny=cost)
        return {"full": None, "provider": provider, "cost": cost, "error": str(e)}

    # 字数不达标 → 自动补写一次（给 AI 看实际短了多少，让它扩）
    shortfall = min_chars - actual_chars
    retried = False
    if shortfall > 20:
        retried = True
        expand_user = (
            f"你上一版总字数只有 {actual_chars} 字，但目标是 {min_chars}-{max_chars} 字（时长 {duration_sec}s）。"
            f"缺 {shortfall} 字。请保留结构/段数/顺序不变，往每段里**补具体细节**（数字、案例细节、机构名、场景动作），"
            f"直到总字数落进目标区间。返回完整的同样 JSON 结构，不要只返回新增部分。当前文稿 JSON：\n"
            + json.dumps(parsed, ensure_ascii=False)
        )
        r2 = llm_client.call_json_with_fallback(system, expand_user, timeout=gen_timeout)
        if r2 is not None:
            p2, c2, cost2 = r2
            cost += cost2
            try:
                parsed2 = json.loads(c2)
                assert "paragraphs" in parsed2 and isinstance(parsed2["paragraphs"], list)
                for i, p in enumerate(parsed2["paragraphs"]):
                    p.setdefault("id", f"p{i+1}")
                    p.setdefault("role", parsed["paragraphs"][i]["role"] if i < len(parsed["paragraphs"]) else "段落")
                    p.setdefault("purpose", "")
                    p.setdefault("text", "")
                    p.setdefault("info_density", "中")
                    p["duration_sec"] = round(len(p["text"]) / pace_chars_per_sec, 1)
                parsed2.setdefault("title", parsed.get("title", topic))
                parsed2["duration_sec"] = duration_sec
                parsed2["pace_chars_per_sec"] = pace_chars_per_sec
                new_chars = sum(len(p["text"]) for p in parsed2["paragraphs"])
                parsed2["total_chars"] = new_chars
                parsed2.setdefault("framework", parsed.get("framework", {}))
                parsed2.setdefault("summary", parsed.get("summary", ""))
                # 采纳条件：变长 且 没超上限（超上限宁可保持原版，让用户自己删）
                if new_chars > actual_chars and new_chars <= max_chars:
                    parsed = parsed2
                    actual_chars = new_chars
                elif new_chars > max_chars:
                    # 扩过头了 — 截断到 max_chars 附近：从尾部往前累加字数，保留前 N 段
                    trimmed = []
                    cum = 0
                    for p in parsed2["paragraphs"]:
                        if cum + len(p["text"]) <= max_chars:
                            trimmed.append(p); cum += len(p["text"])
                        else:
                            break
                    # 必须保留 CTA — 如果最后一段是 CTA role，从被删的段里找回
                    original_last = parsed2["paragraphs"][-1]
                    if trimmed and trimmed[-1].get("role") != "CTA" and original_last.get("role") == "CTA":
                        trimmed.append(original_last); cum += len(original_last["text"])
                    if trimmed and min_chars <= cum <= max_chars:
                        parsed2["paragraphs"] = trimmed
                        parsed2["total_chars"] = cum
                        parsed = parsed2
                        actual_chars = cum
            except Exception:
                pass  # 补写失败就用原版

    state.finish_task(task_id, success=True,
                      output={"chars": actual_chars, "target_min": min_chars, "target_max": max_chars,
                              "retried_expand": retried},
                      cost_cny=cost)
    with state.get_conn() as conn:
        conn.execute("UPDATE tasks SET provider = ? WHERE task_id = ?", (provider, task_id))
    # 把字数达标警告带回给前端
    if actual_chars < min_chars:
        parsed["char_warning"] = f"实际 {actual_chars} 字，低于目标 {min_chars} 字下限（已尝试补写）"
    return {"full": parsed, "provider": provider, "cost": cost}


def refine_paragraph(
    video_id: str, full: dict[str, Any], paragraph_idx: int, instruction: str,
    style: dict[str, Any],
) -> dict[str, Any]:
    """单段重写。返回 {text, duration_sec, provider, cost}"""
    _ensure_video_row(video_id, full.get("title", "(untitled)"))
    paras = full.get("paragraphs") or []
    if paragraph_idx < 0 or paragraph_idx >= len(paras):
        return {"error": "paragraph_idx out of range"}
    p = paras[paragraph_idx]
    cur = p.get("text", "")
    cur_len = len(cur)
    min_c, max_c = max(20, int(cur_len * 0.7)), int(cur_len * 1.4) + 20
    full_context = "\n".join(
        f"[{i+1}/{p2.get('role','')}] {p2.get('text','')[:60]}{'...' if len(p2.get('text',''))>60 else ''}"
        for i, p2 in enumerate(paras)
    )
    system = _fill(
        _read_prompt("paragraph_refine.txt"),
        full_context=full_context,
        idx=paragraph_idx + 1,
        role=p.get("role", ""),
        current_text=cur,
        instruction=instruction or "换一个更有张力的版本",
        style_name=style.get("name", "默认"),
        min_chars=min_c, max_chars=max_c,
    )
    task_id = state.start_task(video_id, agent="paragraph_refine", input_data={"idx": paragraph_idx})
    result = llm_client.call_json_with_fallback(system, "请按上述要求重写这一段。")
    if result is None:
        state.finish_task(task_id, success=True, output={"mode": "stub"}, cost_cny=0.0)
        return {"text": cur + "（stub 改写）", "duration_sec": p.get("duration_sec", 8), "provider": "stub", "cost": 0.0}
    provider, content, cost = result
    try:
        parsed = json.loads(content)
        text = parsed.get("text", "").strip()
        assert text
        dur = parsed.get("duration_sec") or round(len(text) / 4.0, 1)
    except Exception as e:
        state.finish_task(task_id, success=False, error=f"parse: {e}", cost_cny=cost)
        return {"text": cur, "duration_sec": p.get("duration_sec", 8), "provider": provider, "cost": cost, "error": str(e)}
    state.finish_task(task_id, success=True, output={"len": len(text)}, cost_cny=cost)
    with state.get_conn() as conn:
        conn.execute("UPDATE tasks SET provider = ? WHERE task_id = ?", (provider, task_id))
    return {"text": text, "duration_sec": dur, "provider": provider, "cost": cost}


def build_working_from_draft(draft: dict[str, Any]) -> dict[str, Any]:
    """把一整套 draft 展开成 working_draft 初始结构。"""
    did = draft["draft_id"]
    return {
        "hook": {"source_draft": did, "text": draft["hook"], "edited": False},
        "body": [
            {"source_draft": did, "step": b["step"], "role": b.get("role", ""), "text": b["text"], "edited": False}
            for b in draft["body"]
        ],
        "golden": {"source_draft": did, "text": draft["golden"], "edited": False},
        "cta": {"source_draft": did, "text": draft["cta"], "edited": False},
    }
