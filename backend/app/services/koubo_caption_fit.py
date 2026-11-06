# -*- coding: utf-8 -*-
"""数字人 3.0：按模板排版约束切字幕、提关键词。

切分复用「模板定制」的字幕流程（cutcli_templates_local），
保证中英文、标点、保护词规则一致；这里只补时间对齐和关键词。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..api.cutcli_templates_local import _caption_visual_units, _split_caption_text

_EMPHASIS = (
    "免费", "限时", "最后", "第一", "唯一", "不要", "千万", "一定", "必须",
    "核心", "关键", "重点", "真相", "秘密", "错误", "正确", "便宜", "划算",
    "涨价", "降价", "避坑", "推荐", "实测", "对比", "反转", "居然", "没想到",
)

_STOP = {
    "我们", "你们", "他们", "这个", "那个", "然后", "因为", "所以", "但是",
    "如果", "还是", "可以", "一个", "什么", "怎么", "这样", "那样", "自己",
    "现在", "时候", "已经", "就是", "不是", "没有", "知道", "觉得", "其实",
    "真的", "非常", "特别", "比较", "很多", "一下", "这些", "那些", "大家",
    "今天", "明天", "昨天", "东西", "事情", "问题", "地方", "感觉",
}


def caption_capacity(width: int, height: int, caption_size: float, max_chars: int) -> float:
    """一屏大约能放多少个中文字（visual units）。"""
    font_px = max(10.0, float(height or 480) * float(caption_size or 0.05))
    usable_px = max(60.0, float(width or 270) * 0.94 - 36.0)
    units = usable_px / font_px
    return max(6.0, min(float(max_chars or 22), units))


def _match_chars(chars: List[List[Any]], target: List[str], cursor: int
                 ) -> Optional[Tuple[List[List[Any]], int]]:
    matched: List[List[Any]] = []
    j = cursor
    for ch in target:
        while j < len(chars) and str(chars[j][0]) != ch:
            j += 1
        if j >= len(chars):
            return None
        matched.append([ch, float(chars[j][1])])
        j += 1
    return matched, j


def _word_span(seg: Dict[str, Any], word: str) -> Optional[Tuple[float, float]]:
    chars = [[str(c), float(t)] for c, t in (seg.get("chars") or []) if str(c).strip()]
    target = [c for c in word if c.strip()]
    if not target:
        return None
    hit = _match_chars(chars, target, 0)
    if not hit:
        return None
    matched = hit[0]
    start = max(float(seg.get("start") or 0.0), matched[0][1])
    end = min(float(seg.get("end") or start + 1.0), matched[-1][1] + max(0.7, 0.22 * len(target)))
    if end <= start:
        end = start + 0.6
    return round(start, 3), round(end, 3)


def fit_segments(segs: List[Dict[str, Any]], *, width: int, height: int,
                 caption_size: float = 0.05, max_chars: int = 22) -> List[Dict[str, Any]]:
    capacity = caption_capacity(width, height, caption_size, max_chars)
    out: List[Dict[str, Any]] = []
    for seg in segs or []:
        text = str(seg.get("text") or "").strip()
        start = float(seg.get("start") or 0.0)
        end = max(start + 0.4, float(seg.get("end") or start + 0.4))
        if not text:
            continue
        chunks = [c for c in _split_caption_text(
            text, max_chars=int(max_chars or 22), max_visual_units=capacity) if c]
        if not chunks:
            chunks = [text]
        if len(chunks) == 1:
            out.append({**seg, "start": round(start, 3), "end": round(end, 3), "text": chunks[0]})
            continue
        chars = [[str(c), float(t)] for c, t in (seg.get("chars") or []) if str(c).strip()]
        cursor = 0
        prev_end = start
        built: List[Dict[str, Any]] = []
        for idx, chunk in enumerate(chunks):
            target = [ch for ch in chunk if ch.strip()]
            hit = _match_chars(chars, target, cursor)
            if hit:
                matched, cursor = hit
                cstart = max(prev_end, matched[0][1])
                cend = matched[-1][1] + 0.22
                cchars = [[c, round(t, 3)] for c, t in matched]
            else:
                cstart = prev_end
                cend = prev_end + max(0.45, (end - start) / max(1, len(chunks)))
                step = max(0.03, (cend - cstart) / max(1, len(target)))
                cchars = [[ch, round(cstart + i * step, 3)] for i, ch in enumerate(target)]
            cstart = max(prev_end, min(cstart, end - 0.25))
            cend = max(cstart + 0.34, min(cend, end))
            if idx == len(chunks) - 1:
                cend = max(cend, end)
            built.append({"start": round(cstart, 3), "end": round(cend, 3),
                          "text": chunk, "chars": cchars})
            prev_end = cend
        out.extend(built)
    if out:
        out[0]["start"] = 0.0 if out[0]["start"] < 0.2 else out[0]["start"]
    return out


def extract_keywords(segs: List[Dict[str, Any]], limit: int = 8) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for seg in segs or []:
        text = str(seg.get("text") or "")
        if not text:
            continue
        cands: List[Tuple[str, float]] = []
        for m in re.finditer(r"[¥$]?\d[\d,.]*\s*(?:%|元|块|万|亿|折|岁|年|天|小时|分钟|秒)?", text):
            tok = m.group(0).strip()
            if tok and any(ch.isdigit() for ch in tok):
                cands.append((tok, 12.0))
        for m in re.finditer(r"[零一二两三四五六七八九十百千万亿]{2,}(?:元|块|万|亿|折|岁|年|天|小时|分钟|秒|%|％)?", text):
            tok = m.group(0).strip()
            if len(tok) >= 2:
                cands.append((tok, 11.0))
        for m in re.finditer(r"[「“\"]([^」”\"]{2,8})[」”\"]", text):
            cands.append((m.group(1).strip(), 9.0))
        for word in _EMPHASIS:
            if word in text:
                cands.append((word, 7.0))
        for m in re.finditer(r"[A-Za-z]{2,12}", text):
            cands.append((m.group(0), 6.0))
        if len(cands) < 2:
            for m in re.finditer(r"[\u4e00-\u9fff]{2,4}", text):
                w = m.group(0)
                if w not in _STOP:
                    cands.append((w, 3.0 + len(w)))
        cands.sort(key=lambda x: -x[1])
        picked = 0
        for word, _score in cands:
            if word in seen or len(word) < 2:
                continue
            span = _word_span(seg, word)
            if not span:
                continue
            seen.add(word)
            out.append({"text": word, "start": span[0], "end": span[1], "pop": 0.2})
            picked += 1
            if picked >= 2:
                break
        if len(out) >= limit:
            break
    return out[:limit]
