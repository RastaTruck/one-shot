"""News 字幕对齐器 · 语义切分 + librosa 静音吸附。

核心问题：纯按字数比例分配字幕时长会"漂"——TTS 在标点处会真停顿（。？！≈350ms，，；≈180ms），
这些停顿"字数=0 占用时间"。100s 音频累积下来字幕能漂 2-3s。

解决思路（参考一二布布按段独立 audio 的天然对齐，但 news 只有一条整段 mp3 → 改走声学吸附）：

1. 语义切分：按标点切 + 短段合并 + 长段硬切，每条 cue 不超 max_chars
2. librosa RMS 静音检测：找出 ≥180ms 的静音段（TTS 在 。？！；，处的真实停顿）
3. 边界吸附：N 条 cue 需要 N-1 个边界，从静音段里挑最匹配的 N-1 个作为切点
4. cue 时间窗：start = 上一段静音结束，end = 这一段静音开始 → 字幕在 TTS 停顿时"闪掉"
5. fallback：静音数 < N-1 时降级到"标点加权比例"分配（标点按等效字符数加进权重）
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

# 标点 → 对应"等效字符数"作为停顿权重（用于 fallback 模式）
# 数字基于经验：单字符耗时 ~170-200ms；逗号停顿 ~180ms ≈ 1 字；句号 ~350ms ≈ 2 字
_PAUSE_WEIGHT = {
    "。": 2.0, ".": 2.0, "！": 2.5, "!": 2.5, "？": 2.5, "?": 2.5,
    "，": 1.0, ",": 1.0, "；": 1.5, ";": 1.5,
    "：": 1.2, ":": 1.2,
    "、": 0.6,
}
_TERMINATORS = "，。！？!?；;,、：:"
# 单条 cue 去除的标点（user 要求"没有标点符号"）
_CUE_PUNCT_RE = re.compile(r"[，。！？!?；;,、：:\"'“”‘’「」『』（）()【】《》〈〉…—\-·]")


def _semantic_split(text: str, *, max_chars: int = 14, min_chars: int = 4) -> list[tuple[str, str]]:
    """语义切分：按标点切 + 短段合并 + 长段硬切。
    返回 [(clean_text, trailing_punct), ...]，clean_text 已去标点。
    用于驱动后续的时间分配。"""
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return []

    # 1. 按标点切，记录每段的尾标点
    raw_pieces: list[tuple[str, str]] = []   # [(text_with_no_trailing_punct, trailing_punct)]
    buf = ""
    for ch in text:
        if ch in "\n\r\t":
            ch = ""  # 把换行当 0 宽（不计入字数，不当标点）
        if ch in _TERMINATORS:
            clean = buf.strip()
            if clean:
                raw_pieces.append((clean, ch))
            buf = ""
        else:
            buf += ch
    if buf.strip():
        raw_pieces.append((buf.strip(), ""))

    if not raw_pieces:
        return []

    # 2. 合并过短段（< min_chars）到相邻段
    merged: list[tuple[str, str]] = []
    for txt, punct in raw_pieces:
        if not merged:
            merged.append((txt, punct))
            continue
        prev_txt, prev_punct = merged[-1]
        # 当前过短 OR 上一段过短，且合并后 ≤ max_chars+4 → 合并（上一段尾标点丢弃）
        if (len(txt) < min_chars or len(prev_txt) < min_chars) and len(prev_txt) + len(txt) <= max_chars + 4:
            merged[-1] = (prev_txt + txt, punct)
        else:
            merged.append((txt, punct))

    # 3. 长段硬切（> max_chars * 1.5 才切）
    HARD_CAP = int(max_chars * 1.5)
    final: list[tuple[str, str]] = []
    for txt, punct in merged:
        if len(txt) <= HARD_CAP:
            final.append((txt, punct))
            continue
        # 在 max_chars 附近找断点（优先标点 / 数字字母词边界）
        remaining = txt
        while len(remaining) > HARD_CAP:
            cut = max_chars
            best = None
            for i in range(min(len(remaining), max_chars + 4), max(min_chars, max_chars - 4), -1):
                if 0 < i < len(remaining):
                    prev_ascii = remaining[i - 1].isascii() and remaining[i - 1].isalnum()
                    next_ascii = remaining[i].isascii() and remaining[i].isalnum()
                    if not (prev_ascii and next_ascii):
                        best = i
                        break
            if best is not None:
                cut = best
            final.append((remaining[:cut].strip(), ""))
            remaining = remaining[cut:].strip()
        if remaining:
            final.append((remaining, punct))

    # 4. 短尾巴 < min_chars 吞回前一条
    while len(final) >= 2 and len(final[-1][0]) < min_chars:
        prev_txt, _ = final[-2]
        last_txt, last_punct = final[-1]
        final[-2] = (prev_txt + last_txt, last_punct)
        final.pop()

    return final


def _detect_silences(
    audio_path: str | Path,
    *,
    min_silence_sec: float = 0.18,
    rms_threshold: float = 0.06,
    samples_per_sec: int = 100,
) -> tuple[list[tuple[float, float]], float]:
    """检测 ≥min_silence_sec 的静音段。
    返回 ([(silence_start_sec, silence_end_sec), ...], audio_duration_sec)。
    threshold 是 95 分位归一化后的相对值，0.06 大致对应 -24dBFS。"""
    try:
        import librosa  # 延迟导入：失败时调用方走 fallback
    except ImportError:
        return [], 0.0

    y, sr = librosa.load(str(audio_path), sr=None, mono=True)
    duration = len(y) / sr
    if duration <= 0:
        return [], 0.0

    hop = max(1, sr // samples_per_sec)
    rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop)[0]
    norm = max(float(np.percentile(rms, 95)), 1e-6)
    rms_n = rms / norm

    intervals: list[tuple[float, float]] = []
    in_sil = False
    sil_start = 0.0
    for i, val in enumerate(rms_n):
        t = i * hop / sr
        if val < rms_threshold:
            if not in_sil:
                sil_start = t
                in_sil = True
        else:
            if in_sil:
                if t - sil_start >= min_silence_sec:
                    intervals.append((sil_start, t))
                in_sil = False
    if in_sil:
        if duration - sil_start >= min_silence_sec:
            intervals.append((sil_start, duration))

    return intervals, duration


def _split_long_cue(text: str, t_start: float, t_end: float, max_chars: int) -> list[dict]:
    """单条 cue 超 max_chars 时按 CJK 标点 + ASCII 词边界切多条 sub-cue。
    text 必须保留原标点（切完才去），否则没标点锚点会从汉字中间切。"""
    if len(text) <= max_chars + 4 or t_end <= t_start:
        return [{"start": t_start, "end": t_end, "text": text}]

    # 优先按 CJK 标点切（标点本身是停顿信号）
    # 用滑窗找最合理的切点
    HARD = int(max_chars * 1.5)
    chunks: list[str] = []
    s = text
    while len(s) > HARD:
        # 在 [max_chars-3, max_chars+5] 窗口找最佳切点
        lo = max(4, max_chars - 3)
        hi = min(len(s), max_chars + 6)
        best = None
        # 优先 1：找 CJK 强标点 (。！？；)
        for i in range(lo, hi + 1):
            if i < len(s) and s[i - 1] in "。！？；.!?;":
                best = i
                break
        # 优先 2：找 CJK 弱标点 (，、：)
        if best is None:
            for i in range(lo, hi + 1):
                if i < len(s) and s[i - 1] in "，、：,:":
                    best = i
                    break
        # 优先 3：找 ASCII 词边界（避免切英文/数字）
        if best is None:
            for i in range(hi, lo - 1, -1):
                if 0 < i < len(s):
                    prev_ascii = s[i - 1].isascii() and s[i - 1].isalnum()
                    next_ascii = s[i].isascii() and s[i].isalnum()
                    if not (prev_ascii and next_ascii):
                        best = i
                        break
        cut = best if best is not None else max_chars
        chunks.append(s[:cut].strip())
        s = s[cut:].strip()
    if s:
        chunks.append(s)
    if not chunks:
        return [{"start": t_start, "end": t_end, "text": text}]
    # 按"含标点"字数比例分配 [t_start, t_end] 窗口（标点占字数 → 时长里也算）
    total = sum(len(c) for c in chunks) or 1
    out: list[dict] = []
    cur = t_start
    for i, ch in enumerate(chunks):
        if i == len(chunks) - 1:
            seg_end = t_end
        else:
            seg_end = cur + (t_end - t_start) * len(ch) / total
        out.append({"start": cur, "end": seg_end, "text": ch})
        cur = seg_end
    return out


def _assign_silence_driven(
    text_clean: str,
    silences: list[tuple[float, float]],
    audio_dur: float,
    *,
    max_chars: int = 14,
) -> list[dict]:
    """静音驱动对齐 · cue 数由音频里的真停顿决定，不由文本切分决定。

    算法：
    1. char_rate = 总字数 / 总说话时间（音频总长 - 静音总长）
    2. 顺序遍历每个静音：
       - 期望此刻 char_cursor 应推进到的位置 = round(elapsed_speak_time × char_rate)
       - 在 target ±5 字范围找最近的标点位置，作为这一段 cue 的文本结束位
       - cue.start = 上一段静音 end，cue.end = 当前静音 start
    3. 末段 cue 取所有剩余文字，覆盖到 audio_dur
    4. 单条 cue 字数若超 max_chars，在 cue 时间窗内按字数比例切多条 sub-cue

    这样保证 cue 边界 = 真静音中心，字幕在 TTS 换气时闪掉，跟着呼吸节奏走。"""
    if not text_clean:
        return []
    if not silences:
        # 没静音 → 单 cue 覆盖全段
        return _split_long_cue(text_clean, 0.0, audio_dur, max_chars)

    total_chars = len(text_clean)
    total_silence = sum(e - s for s, e in silences)
    total_speak = max(0.1, audio_dur - total_silence)
    char_rate = total_chars / total_speak  # chars / 说话秒

    cues_raw: list[tuple[str, float, float]] = []   # [(text, t_start, t_end)]
    char_cursor = 0
    speak_t_cursor = 0.0  # 当前说话段起点（前一段静音 end，或 0）

    for sil_start, sil_end in silences:
        speak_dur_so_far = sil_start - speak_t_cursor
        if speak_dur_so_far <= 0.05:
            speak_t_cursor = sil_end
            continue
        chars_expected = int(round(speak_dur_so_far * char_rate))
        target_end = min(total_chars, char_cursor + chars_expected)
        if target_end <= char_cursor:
            speak_t_cursor = sil_end
            continue
        # 向标点吸附（target ±6 字范围内找最近标点）
        snap_window = 6
        snap_pos = target_end
        best_dist = snap_window + 1
        lo = max(char_cursor + 1, target_end - snap_window)
        hi = min(total_chars, target_end + snap_window + 1)
        for pos in range(lo, hi):
            if pos - 1 < total_chars and text_clean[pos - 1] in _TERMINATORS:
                d = abs(pos - target_end)
                if d < best_dist:
                    best_dist = d
                    snap_pos = pos
        chunk_text = text_clean[char_cursor:snap_pos].strip()
        if chunk_text:
            cues_raw.append((chunk_text, speak_t_cursor, sil_start))
        char_cursor = snap_pos
        speak_t_cursor = sil_end

    # 末段：剩余文字 → 最后一条 cue
    if char_cursor < total_chars:
        tail = text_clean[char_cursor:].strip()
        if tail:
            cues_raw.append((tail, speak_t_cursor, audio_dur))

    # 长 cue 内部再切：必须用"含标点"的原文切（标点是天然停顿锚点），切完每条再去标点
    out: list[dict] = []
    for txt, ts, te in cues_raw:
        if len(txt) > max_chars + 4:
            sub_cues = _split_long_cue(txt, ts, te, max_chars)
        else:
            sub_cues = [{"start": ts, "end": te, "text": txt}]
        for sub in sub_cues:
            cleaned = _CUE_PUNCT_RE.sub("", sub["text"]).strip()
            if cleaned:
                out.append({"start": sub["start"], "end": sub["end"], "text": cleaned})

    # 卫生检查
    return [c for c in out if c["text"].strip() and c["end"] > c["start"]]


def _assign_by_proportional_weights(
    pieces: list[tuple[str, str]],
    audio_dur: float,
) -> list[dict]:
    """fallback：按"字数 + 标点等效字符"加权比例分配时间。
    比纯字数版准——至少标点停顿能算进去。"""
    if not pieces:
        return []
    weights: list[float] = []
    for txt, punct in pieces:
        w = float(len(txt)) + _PAUSE_WEIGHT.get(punct, 0.0)
        weights.append(max(w, 1.0))
    total_w = sum(weights)
    out: list[dict] = []
    cursor = 0.0
    for i, ((txt, _punct), w) in enumerate(zip(pieces, weights)):
        if i == len(pieces) - 1:
            seg_end = audio_dur
        else:
            seg_end = cursor + w / total_w * audio_dur
        out.append({"start": cursor, "end": seg_end, "text": txt})
        cursor = seg_end
    return out


def align_units_to_audio(
    units: list[str],
    audio_path: str | Path | None,
    audio_dur: float,
) -> tuple[list[dict], str]:
    """unit 驱动对齐 · 用户已审核过的 units 作为 cue 边界基准。

    流程：
    1. 每个 unit 直接 = 一条字幕（无需再切分）
    2. librosa 检测静音；理想情况 N 个 unit 对 N-1 个静音（TTS 在 unit 边界 "。" 处停顿）
    3. 静音数 ≥ N-1 → 1:1 吸附；不足 → 按 unit 字数等比例分配（fallback）
    4. cue.text 是去标点后的 unit 文本（字幕"无标点"）
    """
    if not units:
        return [], "empty"
    # 清洗：去 None / 空，去尾标点
    cleaned_units: list[str] = []
    for u in units:
        if not u or not str(u).strip():
            continue
        u_clean = _CUE_PUNCT_RE.sub("", str(u)).strip()
        if u_clean:
            cleaned_units.append(u_clean)
    if not cleaned_units:
        return [], "empty"
    n = len(cleaned_units)

    # 单 unit：全程一条
    if n == 1:
        return [{"start": 0.0, "end": audio_dur, "text": cleaned_units[0]}], "single_unit"

    # 尝试 librosa 1:1 吸附
    if audio_path and Path(audio_path).exists():
        try:
            silences, dur = _detect_silences(audio_path)
            use_dur = audio_dur if abs(audio_dur - dur) < 1.0 else dur
            if len(silences) >= n - 1:
                # 选 N-1 个静音作为边界。优先选最长的（停得最实）+ 按时间顺序排
                # 但避免选太早或太晚的孤立静音 → 用"按时间期望位置选最近"策略
                # 期望位置：按 unit 字数比例
                weights = [max(1, len(u)) for u in cleaned_units]
                total_w = sum(weights)
                expected = []
                cur = 0.0
                for w in weights[:-1]:
                    cur += w / total_w * use_dur
                    expected.append(cur)
                # 对每个 expected 找最近未用静音
                silence_centers = [(s + e) / 2.0 for s, e in silences]
                used: set[int] = set()
                snapped: list[tuple[float, float]] = []  # [(sil_start, sil_end), ...]
                for exp in expected:
                    best_i = None
                    best_d = float("inf")
                    for i, c in enumerate(silence_centers):
                        if i in used:
                            continue
                        d = abs(c - exp)
                        if d < best_d:
                            best_d = d
                            best_i = i
                    if best_i is None:
                        snapped.append((exp, exp))
                    else:
                        used.add(best_i)
                        snapped.append(silences[best_i])

                # 构造 cues
                out: list[dict] = []
                prev_end = 0.0  # 上段静音的 end（= 本段说话开始）
                for i, txt in enumerate(cleaned_units):
                    if i < n - 1:
                        sil_start, sil_end = snapped[i]
                        cue_start = prev_end
                        cue_end = max(cue_start + 0.3, sil_start)
                        out.append({"start": cue_start, "end": cue_end, "text": txt})
                        prev_end = sil_end
                    else:
                        out.append({"start": prev_end, "end": use_dur, "text": txt})
                cleaned_out = [c for c in out if c["text"] and c["end"] > c["start"]]
                if cleaned_out:
                    return cleaned_out, "units_silence_snap"
        except Exception:
            pass

    # fallback：按字数等比例（无 librosa 或静音不足）
    weights = [max(1, len(u)) for u in cleaned_units]
    total_w = sum(weights)
    out: list[dict] = []
    cur = 0.0
    for i, (u, w) in enumerate(zip(cleaned_units, weights)):
        seg_end = audio_dur if i == n - 1 else cur + w / total_w * audio_dur
        out.append({"start": cur, "end": seg_end, "text": u})
        cur = seg_end
    return out, "units_weighted"


def align_cues_to_audio(
    text: str,
    audio_path: str | Path | None,
    audio_dur: float,
    *,
    max_chars: int = 14,
    min_chars: int = 4,
) -> tuple[list[dict], str]:
    """主入口 · 返回 (cues, mode)。

    mode ∈ {"silence_driven", "weighted_prop", "empty"} —— 写进装配日志方便排查。

    优先 silence_driven（cue 边界 = TTS 真停顿）；librosa 不可用 / 静音段过少 → weighted_prop fallback。
    """
    # 1. 文本归一化：去掉所有换行/制表（不当字数，也不当标点）
    text_norm = (text or "").replace("\r\n", "\n").translate(
        {ord(c): "" for c in "\n\r\t"}
    ).strip()
    if not text_norm or audio_dur <= 0.5:
        return [], "empty"

    # 2. 尝试 silence-driven（最准）
    if audio_path and Path(audio_path).exists():
        try:
            silences, dur = _detect_silences(audio_path)
            use_dur = audio_dur if abs(audio_dur - dur) < 1.0 else dur
            # 至少要有 1 个静音才有意义（否则等于单 cue）
            if silences:
                cues = _assign_silence_driven(text_norm, silences, use_dur, max_chars=max_chars)
                if cues:
                    return cues, "silence_driven"
        except Exception:
            pass

    # 3. fallback：语义切 + 加权比例
    pieces = _semantic_split(text_norm, max_chars=max_chars, min_chars=min_chars)
    pieces = [(_CUE_PUNCT_RE.sub("", t).strip() or t, p) for t, p in pieces]
    pieces = [p for p in pieces if p[0]]
    if not pieces:
        return [], "empty"
    return _assign_by_proportional_weights(pieces, audio_dur), "weighted_prop"
