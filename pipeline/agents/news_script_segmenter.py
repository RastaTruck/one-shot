"""News 文案 → 字幕单元 切分器（B2 规则版）。

设计原则：
1. 每个 unit = 一条字幕 cue + TTS 在其末尾会真停顿
2. 强标点 `。！？；` 和换行 `\n` 是首选边界（TTS 一定停）
3. 长句（>max_chars*1.4）在 `，：` 处二切（TTS 大概率停）
4. 短段（<min_chars）和邻居合并（避免字幕一闪而过）
5. 输出 units 的末尾**保留原标点**（驱动 TTS 停顿用），UI 显示时由前端隐去

为什么不切 `、`：顿号 TTS 几乎不停（< 100ms），切了字幕会跟不上语音
"""
from __future__ import annotations

import re

# 强终止符：必切（TTS 确定停顿）。包含 \n 因为 TTS 在换行处也换气
_STRONG = "。！？；?!;\n"
# 弱终止符：长句才在这切（TTS 大概率停顿）
_WEAK = "，,：:"
# 不切：顿号 / 引号 / 括号 内的标点
_SKIP = "、""''「」『』（）()【】《》〈〉"


def segment_script(text: str, *, max_chars: int = 14, min_chars: int = 4) -> list[str]:
    """B2 切分：强标点优先 + 长句弱标点二切 + 短段合并。
    输出每个 unit 末尾**保留**强标点（如 "。"），供 TTS 阶段做停顿信号。

    举例输入：
      "欢迎收看ZooNews。\\n特朗普访华，中美都说聊得不错。"
    输出：
      ["欢迎收看ZooNews。", "特朗普访华，中美都说聊得不错。"]
    """
    if not text or not text.strip():
        return []

    # 1. 归一化：\r\n → \n；制表替换为空格
    s = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")

    # 2. 一遍扫描按强标点切（含 \n）
    pieces: list[str] = []
    buf = ""
    for ch in s:
        buf += ch
        if ch in _STRONG:
            cleaned = _trim_unit(buf)
            if cleaned:
                pieces.append(cleaned)
            buf = ""
    tail = _trim_unit(buf)
    if tail:
        pieces.append(tail)

    if not pieces:
        return []

    # 3. 短段合并：< min_chars 的吞到邻居（向前合并优先，避免破坏开头）
    merged: list[str] = []
    for p in pieces:
        clean_len = len(_strip_trailing_punct(p))
        if not merged:
            merged.append(p)
            continue
        prev_clean_len = len(_strip_trailing_punct(merged[-1]))
        # 短段 + 合并后不超 max_chars*1.6 → 合并（前段尾标点丢弃）
        if (clean_len < min_chars or prev_clean_len < min_chars) and \
           (prev_clean_len + clean_len <= int(max_chars * 1.6)):
            merged[-1] = _strip_trailing_punct(merged[-1]) + p
        else:
            merged.append(p)

    # 4. 长段在弱标点处二切（> max_chars*1.4）
    HARD = int(max_chars * 1.4)  # 20 字（max=14 时）以上就切
    final: list[str] = []
    for p in merged:
        if len(_strip_trailing_punct(p)) <= HARD:
            final.append(p)
            continue
        # 在弱标点处切
        sub: list[str] = []
        buf = ""
        for ch in p:
            buf += ch
            # 弱标点切，并且累计 ≥min_chars 才切（避免切出 "也急" 这种独立小段）
            if ch in _WEAK and len(_strip_trailing_punct(buf)) >= min_chars:
                sub.append(buf)
                buf = ""
        if buf.strip():
            sub.append(buf)
        # sub 内还可能有超 HARD 的（弱标点也少）→ 不再硬切，留给 ASS WrapStyle=2 时由前端预警
        # 但短尾巴往前合并避免独立小段
        cleaned_sub: list[str] = []
        for s2 in sub:
            cs = _trim_unit(s2)
            if not cs:
                continue
            if cleaned_sub and len(_strip_trailing_punct(cleaned_sub[-1])) < min_chars:
                cleaned_sub[-1] = _strip_trailing_punct(cleaned_sub[-1]) + cs
            else:
                cleaned_sub.append(cs)
        final.extend(cleaned_sub or [p])

    # 5. 末尾短尾巴吞回前段
    while len(final) >= 2 and len(_strip_trailing_punct(final[-1])) < min_chars:
        prev = _strip_trailing_punct(final[-2])
        final[-2] = prev + final[-1]
        final.pop()

    return final


def _trim_unit(s: str) -> str:
    """裁剪 unit：去前后空白，去开头标点（保留尾标点供 TTS 用）。"""
    s = s.strip()
    while s and s[0] in (_STRONG + _WEAK + " "):
        s = s[1:].strip()
    return s


def _strip_trailing_punct(s: str) -> str:
    """剥掉末尾所有标点（用于长度计算）。"""
    return re.sub(r"[" + re.escape(_STRONG + _WEAK + _SKIP) + r"]+$", "", s).strip()


# 用于 UI 显示：把 unit 末尾的强标点去掉显示更干净
# 但 TTS / 对齐时用原文（含尾标点）
def display_text(unit: str) -> str:
    return _strip_trailing_punct(unit)


def tts_text(units: list[str]) -> str:
    """把 units 拼成喂 TTS 的字符串。
    末尾没强标点的 unit 补一个 "。" 确保 TTS 在 unit 边界真停顿。"""
    if not units:
        return ""
    out: list[str] = []
    for u in units:
        u = u.strip()
        if not u:
            continue
        # \n 翻译为 "。" — TTS 不识别 \n 当停顿但识别 "。"
        u = u.replace("\n", "。").strip()
        if u and u[-1] not in _STRONG:
            u += "。"
        out.append(u)
    return "".join(out)
