"""新闻口播文案生成器 + 选题生成器 · 比 IP 文案简单得多。

输入：题目 + 受众 + 模式（单人/对话）+ 目标时长（秒）
输出：单段连续口播文案（不用分段，因为是一镜到底的口播）+ 标题

prompt 直接内联，不依赖 prompts/ 目录（轻量）。

也提供 generate_topics() —— 不绑定金融，按用户给的关键词自由发挥。
"""
from __future__ import annotations

import json
from typing import Any

from pipeline.tools import llm_client


_PACE = 5.5  # 字/秒，与 IP 管线一致


_SYSTEM_TEMPLATE_MONO = """你是中文短视频的口播文案编辑。给定主题，写一段单人对镜口播稿。

【创作约束】
- 主题：{topic}
- 目标受众：{audience}
- 目标时长：{duration_sec} 秒（{min_chars}-{max_chars} 字）
- 风格：{style_hint}

【写作铁律】
1. 一气呵成 · 不分段 · 这是单人对镜头一口气念完的稿
2. 开场 3 秒必须抓人：用反问/反差/具体数字/冲突
3. 中间论点要"短句快节奏"，禁止超长复合句
4. 标点节奏：每句以 。！？ 收尾，避免"长串短逗号"机关枪
5. 结尾留一句金句或反问，留白让观众消化
6. 禁止：书面语连接词（综上所述/由此可见）/ 空话填充（其实大家都知道）

【返回严格 JSON】
{{
  "title": "10-18字视频标题",
  "script": "口播全文（整段，无换行无段落分割）",
  "char_count": 0
}}
char_count = script 实际字符数（含标点）。
"""


_SYSTEM_TEMPLATE_DIALOG = """你是中文短视频的双人对话口播文案编辑。给定主题，写一段两人轮替对话稿。

【创作约束】
- 主题：{topic}
- 目标受众：{audience}
- 目标时长：{duration_sec} 秒（{min_chars}-{max_chars} 字）

【写作铁律】
1. A/B 两个人物轮流说，每次 1-3 句话
2. A 主问 / 略不懂；B 主答 / 略权威
3. 开场 A 抛出问题或好奇，B 用反差或数字钩住
4. 收尾 B 给金句，A 顺势带 CTA

【返回严格 JSON】
{{
  "title": "10-18字视频标题",
  "script": "A: 你听说了吗...\\nB: 对，今天数据出来...\\n（用 A: / B: 前缀和换行分隔轮次）",
  "char_count": 0
}}
char_count = 不含 A:/B: 前缀的字符数。
"""


_TOPIC_PROMPT = """你是中文短视频的选题策划。根据用户给的关键词，生成 {n_topics} 个候选选题。
**注意：这是通用口播视频的选题策划——不限领域、不锁死金融——用户的关键词是什么领域就做什么。**

【创作约束】
- 关键词 / 主题方向：{keywords}
- 创作意图：{intent}（搞懂概念 / 探讨影响 / 学会操作 / 引发共鸣）
- 目标受众：{audience}（完全小白 / 有点基础 / 进阶用户）
- 目标时长：{duration_sec} 秒短视频

【意图规则】
- 搞懂概念：让人秒懂"它是什么"
- 探讨影响：聚焦"XX 改变了 YY"
- 学会操作：给具体步骤或决策框架
- 引发共鸣：人物故事 + 情绪载体

【受众规则】
- 完全小白：零基础，全部生活类比
- 有点基础：可用简单术语
- 进阶用户：可挑战误区/讲机制

【写作铁律】
1. 标题 12-22 字，开头要有钩子（反问 / 反差 / 数字 / 冲突）
2. 标题必须围绕用户给的"{keywords}"，不要漂移到其他领域
3. {n_topics} 条选题之间要有差异化角度（不要内容雷同）
4. 每条配一句"hook"（一句话讲清楚这个题目的核心反差或洞察）

【返回严格 JSON】
{{
  "topics": [
    {{
      "id": "F01",
      "title": "12-22 字标题",
      "hook": "20 字以内的一句话钩子",
      "angle": "差异化角度（4-6 字标签）"
    }}
  ]
}}
"""


def generate_topics(
    keywords: str,
    intent: str = "搞懂概念",
    audience: str = "完全小白",
    duration_sec: int = 60,
    n_topics: int = 6,
) -> dict[str, Any]:
    """口播专用候选选题生成器——不绑定金融领域。"""
    if not keywords.strip():
        return {"ok": False, "error": "keywords 为空"}
    system = _TOPIC_PROMPT.format(
        n_topics=n_topics,
        keywords=keywords,
        intent=intent,
        audience=audience,
        duration_sec=duration_sec,
    )
    user = "请生成候选选题列表。"
    result = llm_client.call_json_with_fallback(system, user, timeout=90)
    if result is None:
        return {"ok": False, "error": "LLM 不可用 · 请检查 config/.env"}
    provider, content, cost = result
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"LLM 输出非 JSON: {e}", "raw": content[:300]}
    topics = parsed.get("topics") or []
    for i, t in enumerate(topics):
        t.setdefault("id", f"F{i+1:02d}")
        t.setdefault("hook", "")
        t.setdefault("angle", "")
        t["intent"] = intent
        t["audience"] = audience
    if not topics:
        return {"ok": False, "error": "LLM 没返回任何选题", "raw": content[:200]}
    return {"ok": True, "topics": topics, "provider": provider, "cost": cost}


def generate_news_script(
    topic: str,
    audience: str = "完全小白",
    duration_sec: int = 60,
    mode: str = "single",
    style_hint: str = "口语化、有梗、节奏快",
) -> dict[str, Any]:
    """返回 {ok, title, script, char_count, duration_estimated, provider, cost, error?}"""
    target_chars = int(duration_sec * _PACE)
    min_chars = int(target_chars * 0.85)
    max_chars = int(target_chars * 1.15)

    template = _SYSTEM_TEMPLATE_DIALOG if mode == "dialog" else _SYSTEM_TEMPLATE_MONO
    system = template.format(
        topic=topic,
        audience=audience,
        duration_sec=duration_sec,
        min_chars=min_chars,
        max_chars=max_chars,
        style_hint=style_hint,
    )
    user = "请生成口播文案。"

    timeout = max(60, 30 + duration_sec)
    result = llm_client.call_json_with_fallback(system, user, timeout=timeout)
    if result is None:
        return {
            "ok": False,
            "error": "LLM 不可用（无密钥或网络错误）· 请检查 config/.env",
        }

    provider, content, cost = result
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"LLM 输出非 JSON: {e}", "raw": content[:300]}

    title = str(parsed.get("title") or topic)[:30]
    script = str(parsed.get("script") or "").strip()
    if not script:
        return {"ok": False, "error": "LLM 返回的 script 为空", "raw": content[:300]}

    # 字数：剔除 A:/B: 前缀和空白
    plain = script
    if mode == "dialog":
        plain = "\n".join(
            line.split(":", 1)[-1].strip() if ":" in line else line
            for line in script.split("\n")
        )
    char_count = len([c for c in plain if not c.isspace()])
    duration_est = round(char_count / _PACE, 1)

    return {
        "ok": True,
        "title": title,
        "script": script,
        "char_count": char_count,
        "duration_estimated": duration_est,
        "provider": provider,
        "cost": cost,
    }
