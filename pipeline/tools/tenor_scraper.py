"""Tenor GIF 搜索抓取器（无需 API key，靠抓 HTML）。

注意：Tenor GIF 多为用户上传，**不一定有商用授权**。仅用于学习/内部 demo。

模块职责：
- `extract_from_search(query)` 抓搜索页 → 返回 [{url, alt, slug_tags, format}]
- `download(item, out_dir)` 单条下载，返回本地路径
- `infer_scene_tags(alt, slug_tags, query)` 把英文 alt + slug → 闭集 SCENE_TAGS 子集
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


SEARCH_URL_TEMPLATE = "https://tenor.com/{lang}/search/{query}-gifs"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


# 英文 alt / slug 到闭集 scene_tag 的关键词映射（v2 · 扩词表）
SCENE_TAG_KEYWORDS: list[tuple[list[str], str]] = [
    # 震惊
    (["shocked", "shock", "surprised", "surprise", "wow", "omg",
      "amazed", "stunned", "astonished", "gasp", "wat"], "震惊"),
    # 困惑
    (["confused", "confuse", "huh", "what", "questioning", "puzzled",
      "wondering", "unsure", "perplexed"], "困惑"),
    # 沉思
    (["thinking", "ponder", "think", "thoughtful", "contemplating",
      "hmm", "reflect", "meditating"], "沉思"),
    # 讲解
    (["explaining", "telling", "talk", "talking", "presenting",
      "teaching", "lecture", "speech", "speak"], "讲解"),
    # 严肃
    (["serious", "stern", "concentrate", "focus", "focused",
      "determined", "resolute"], "严肃"),
    # 失落
    (["sad", "crying", "cry", "tear", "tears", "depressed",
      "sigh", "disappointed", "down", "blue", "upset"], "失落"),
    # 得意
    (["proud", "smug", "boast", "victory", "win", "winning",
      "satisfied", "confident", "cocky"], "得意"),
    # 恐慌
    (["panic", "panicking", "freaking", "freakout",
      "anxious", "nervous", "scream", "screaming"], "恐慌"),
    # 担忧
    (["worried", "worry", "concerned", "concern", "uneasy",
      "stress", "stressed", "afraid"], "担忧"),
    # 兴奋
    (["happy", "smile", "smiling", "joy", "joyful", "excited",
      "celebrate", "yay", "cheer", "clap", "party",
      "love", "heart", "kiss", "hug", "laugh", "laughing", "haha"], "兴奋"),
    # 无语
    (["speechless", "blank", "no comment", "facepalm", "deadpan",
      "really", "side eye", "side-eye", "judging"], "无语"),
    # 愤怒
    (["angry", "mad", "rage", "fury", "annoyed", "irritated",
      "yell", "yelling", "scold"], "愤怒"),
    # 调侃
    (["funny", "joke", "haha", "lol", "tease", "teasing",
      "mock", "sarcastic", "wink", "smirk"], "调侃"),
    # 害怕
    (["scared", "fear", "afraid", "terrified", "horror",
      "scary", "frightened"], "害怕"),
    # 平静
    (["calm", "peaceful", "chill", "relax", "relaxed",
      "neutral", "sit", "sitting", "rest", "resting"], "平静"),
]

# 内置常用搜索词 → 中文 scene_tag （额外加分）
QUERY_TAG_HINTS: dict[str, list[str]] = {
    "震惊": ["震惊"], "shock": ["震惊"], "shocked": ["震惊"], "wow": ["震惊"],
    "困惑": ["困惑"], "confused": ["困惑"], "huh": ["困惑"],
    "沉思": ["沉思"], "思考": ["沉思"], "thinking": ["沉思"], "ponder": ["沉思"],
    "讲解": ["讲解"], "explaining": ["讲解"], "talking": ["讲解"],
    "严肃": ["严肃"], "serious": ["严肃"], "focused": ["严肃"],
    "失落": ["失落"], "难过": ["失落"], "sad": ["失落"], "crying": ["失落"],
    "得意": ["得意"], "proud": ["得意"], "smug": ["得意"], "victory": ["得意"],
    "恐慌": ["恐慌"], "panic": ["恐慌"], "anxious": ["恐慌"],
    "担忧": ["担忧"], "worried": ["担忧"], "worry": ["担忧"],
    "兴奋": ["兴奋"], "开心": ["兴奋"], "happy": ["兴奋"], "excited": ["兴奋"],
    "无语": ["无语"], "speechless": ["无语"], "facepalm": ["无语"],
    "愤怒": ["愤怒"], "生气": ["愤怒"], "angry": ["愤怒"], "mad": ["愤怒"],
    "调侃": ["调侃"], "tease": ["调侃"], "joke": ["调侃"], "wink": ["调侃"],
    "害怕": ["害怕"], "scared": ["害怕"], "afraid": ["害怕"], "fear": ["害怕"],
    "平静": ["平静"], "calm": ["平静"], "chill": ["平静"], "neutral": ["平静"],
    "钱": [], "money": [], "金币": [], "coin": [],
    "股票": [], "stock": [], "走势": [],
}


def fetch_html(url: str, timeout: int = 30) -> str:
    """带 UA 抓 URL，返回解码后的 HTML。"""
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    # Tenor 返 utf-8
    return raw.decode("utf-8", "replace")


def extract_from_search(query: str, *, lang: str = "zh-CN", limit: int = 60) -> list[dict[str, Any]]:
    """抓搜索页，返回该页所有 GIF 条目。

    每条：{url: 媒体 URL, alt: 英文描述, slug_tags: [...], format: 'webp'/'gif'/'mp4'}.
    """
    url = SEARCH_URL_TEMPLATE.format(lang=lang, query=urllib.parse.quote(query))
    html = fetch_html(url)
    # 匹配 <img ... src="https://media.tenor.com/..." alt="..." ...>
    img_pat = re.compile(
        r'<img[^>]*\bsrc="(https://media\.tenor\.com/[^"]+)"[^>]*\balt="([^"]*)"',
        re.S,
    )
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in img_pat.finditer(html):
        media_url, alt = m.group(1), m.group(2)
        if media_url in seen:
            continue
        seen.add(media_url)
        # slug 从 url 末段取：<id>/<slug>.<ext>
        try:
            tail = media_url.rsplit("/", 1)[-1]
            slug, ext = tail.rsplit(".", 1)
            slug = urllib.parse.unquote(slug)
        except ValueError:
            slug, ext = "", "webp"
        slug_tags = [s for s in re.split(r"[-_\s]+", slug) if s and not s.startswith("AAAA")]
        items.append({
            "url": media_url,
            "alt": alt,
            "slug_tags": slug_tags,
            "format": ext.lower(),
        })
        if len(items) >= limit:
            break
    return items


def infer_scene_tags(alt: str, slug_tags: list[str], query: str) -> list[str]:
    """从 alt + slug + query 推闭集 scene_tags（最多 3 个）."""
    blob = (alt + " " + " ".join(slug_tags) + " " + query).lower()
    out: list[str] = []
    for kws, tag in SCENE_TAG_KEYWORDS:
        if any(kw in blob for kw in kws) and tag not in out:
            out.append(tag)
        if len(out) >= 3:
            break
    # 用户搜索词中文直命中
    for kw, tags in QUERY_TAG_HINTS.items():
        if kw in query.lower():
            for t in tags:
                if t not in out:
                    out.append(t)
            break
    return out[:3]


def download(item: dict, out_dir: str | Path, *,
             filename: str | None = None) -> Path:
    """把单条 item 下载到 out_dir。返回本地绝对路径。

    文件名 = `<tenor_id>__<slug>.<ext>`，避免不同 URL 同 slug 互相覆盖。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not filename:
        # url 形如 https://media.tenor.com/<TENOR_ID>/<slug>.<ext>
        parts = item["url"].rsplit("/", 2)  # ['...media.tenor.com', '<TENOR_ID>', '<slug>.<ext>']
        tenor_id = parts[-2] if len(parts) >= 2 else "x"
        tail = urllib.parse.unquote(parts[-1])
        # 把非法字符洗掉
        tenor_id = re.sub(r"[^A-Za-z0-9_\-]+", "", tenor_id)
        slug_clean = re.sub(r"[^A-Za-z0-9._\-]+", "_", tail)
        filename = f"{tenor_id}__{slug_clean}"
    target = out_dir / filename
    if target.exists() and target.stat().st_size > 0:
        return target
    req = urllib.request.Request(item["url"], headers=DEFAULT_HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp, open(target, "wb") as f:
        f.write(resp.read())
    return target


def harvest_search(query: str, out_dir: str | Path, *,
                    lang: str = "zh-CN", limit: int = 60,
                    on_progress=None) -> list[dict]:
    """一次性把 query 的搜索结果下载到 out_dir，返回登记元数据 list."""
    items = extract_from_search(query, lang=lang, limit=limit)
    out: list[dict] = []
    for i, it in enumerate(items, start=1):
        try:
            local = download(it, out_dir)
            it["local_path"] = str(local)
            it["scene_tags"] = infer_scene_tags(it["alt"], it["slug_tags"], query)
            out.append(it)
            if on_progress:
                on_progress(i, len(items), it)
        except Exception as e:
            it["error"] = str(e)[:200]
            if on_progress:
                on_progress(i, len(items), it)
    return out
