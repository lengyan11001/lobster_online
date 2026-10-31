#!/usr/bin/env python3
"""主播自录口播 → 按剧本切拍 + 跳切去停顿 → 与 narrate_mimo.py 同格式的 timings.json（下游 build_timeline 不用改）。

用法：
  python3 align_recording.py <录音.m4a|wav|mp3> <episode.json> <输出目录> [--max-pause 0.28] [--keep-pause 0.16] [--model ggml.bin] [--no-cut]

  --no-cut：只对齐、不剪不压（拍表只能有 1 拍，整段音频原样保留）。用于 Grok 原生说话的数字人片段——
            它的声音和口型是一体的，剪音频会让口型错位。

做了什么：
1. whisper.cpp 逐字转写（--prompt 给全文剧本，简体 + 原文写法，对齐率高）。
2. 每一拍在转写流里找匹配窗口；同一句录了好几遍（口误重来），取「下一拍开始之前的最后一遍」。
3. 拍内超过 --max-pause 的停顿压到 --keep-pause（原频道的跳切节奏：成片约 6 字/秒）。
4. 输出 audio/<拍id>.wav、narration.wav、timings.json（beats[id] = {start,end,text,chars,words,cues}）。
   chars[i] 是剧本第 i 个字（含标点位置）在 narration.wav 里的开始秒数；对不上的字按前后插值。
5. 报告：每拍对齐率、删掉的重录次数、语速（字/秒）；对齐率 < 80% 的拍标 ⚠，要人工听一下。
"""
import os, codecs
import difflib
import json
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

CUE_RE = re.compile(r"\[\[([a-z][a-z0-9_-]*)\]\]")
PUNCT = set("，。！？；：、,.!?;:（）()「」『』“”\"'《》…—- 　")
MODEL = Path(os.environ["WHISPER_MODEL"]) if os.environ.get("WHISPER_MODEL") else next((q for q in [Path.home() / "Library/Application Support/Murmur/models/ggml-medium-q5_0.bin", Path.home() / "models/ggml-medium-q5_0.bin", Path.home() / ".cache/whisper/ggml-medium-q5_0.bin"] if q.exists()), Path.home() / "models/ggml-medium-q5_0.bin")   # 或用环境变量 WHISPER_MODEL 指定
SR = 24000


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def strip_cues(text):
    out, cues = [], {}
    pos = 0
    for m in CUE_RE.finditer(text):
        out.append(text[pos:m.start()]); cues[m.group(1)] = sum(len(p) for p in out); pos = m.end()
    out.append(text[pos:])
    return "".join(out), cues


def norm(ch):
    tbl = {"〇": "零", "两": "二"}
    return tbl.get(ch, ch).lower()


def _whisper(w16, prompt, model):
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["whisper-cli", "-m", str(model), "-l", "zh", "-f", str(w16), "-ojf", "-of", str(Path(td) / "o"), "-np",
                        "--prompt", prompt], capture_output=True, check=True)
        d = json.loads((Path(td) / "o.json").read_bytes().decode("utf-8", "surrogateescape"))
    dec = codecs.getincrementaldecoder("utf-8")(errors="ignore")
    out = []
    for seg in d["transcription"]:
        for tk in seg.get("tokens", []):
            t = tk["text"]
            if t.startswith("[_") or t.startswith("<|"):
                continue
            for ch in dec.decode(t.encode("utf-8", "surrogateescape")):
                if not ch.isspace() and ch not in PUNCT:
                    out.append((ch, tk["offsets"]["from"] / 1000, tk["offsets"]["to"] / 1000))
    return out


def whisper_chars(pcm16k_path, script, model, pcm=None):
    """长录音先按 ≥0.45 秒的停顿切成句子级小段（≤25 秒），逐段转写再拼回绝对时间。
    整段 + 长提示词会让 whisper 漏句、编句（实测 34 秒只转出 30 字），逐段 + 短提示稳定得多。"""
    terms = sorted(set(re.findall(r"[A-Za-z][A-Za-z0-9.\-]+", script)), key=len, reverse=True)[:30]
    prompt = "以下是普通话的财经科普口播，简体中文。" + ("专有名词：" + "、".join(terms) if terms else "")
    total = len(pcm) / SR
    qs = pauses(pcm, 0, total, min_len=0.45)
    cuts = [0.0] + [(a + b) / 2 for a, b in qs] + [total]
    chunks, cur = [], 0.0
    for c in cuts[1:]:
        if c - cur >= 25 or c == total:
            chunks.append((cur, c)); cur = c
        elif c - cur >= 1.2 and (len(chunks) == 0 or True):
            chunks.append((cur, c)); cur = c
    out = []
    with tempfile.TemporaryDirectory() as td:
        for k, (a, b) in enumerate(chunks):
            if b - a < 0.3:
                continue
            f = Path(td) / f"c{k}.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(pcm16k_path), "-ar", "16000", "-ac", "1", str(f)], check=True)
            out += [(ch, a + t0, a + t1) for ch, t0, t1 in _whisper(f, prompt, model)]
    return out


def load_pcm(path):
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)


def pauses(x, start, end, thr_db=-38, min_len=0.12):
    """[start,end] 里低于阈值的静音段（秒，绝对时间）。"""
    a, b = int(start * SR), int(end * SR)
    seg = x[a:b]; hop = int(0.01 * SR)
    if len(seg) < hop:
        return []
    n = len(seg) // hop
    rms = np.sqrt((seg[:n * hop].reshape(n, hop) ** 2).mean(1) + 1e-9)
    db = 20 * np.log10(rms / 32768 + 1e-12)
    quiet = db < thr_db
    out, i = [], 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if (j - i) * 0.01 >= min_len:
                out.append((start + i * 0.01, start + j * 0.01))
            i = j
        else:
            i += 1
    return out


def main():
    rec, ep_p, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    max_pause, keep_pause = float(arg("--max-pause", "0.28")), float(arg("--keep-pause", "0.16"))
    model = Path(arg("--model", str(MODEL)))
    ep = json.loads(ep_p.read_text())
    beats = [b for b in ep["beats"] if b.get("narration")]
    (out / "audio").mkdir(parents=True, exist_ok=True)
    tmp = out / "_rec24.wav"; w16 = out / "_rec16.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(rec), "-ac", "1", "-ar", str(SR), "-af", "highpass=f=70" if "--no-cut" in sys.argv else "highpass=f=70,loudnorm=I=-18:TP=-1.5:LRA=11", str(tmp)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-ac", "1", "-ar", "16000", str(w16)], check=True)
    pcm = load_pcm(tmp)

    scripts = []
    for b in beats:
        clean, cues = strip_cues(b["narration"])
        scripts.append((b["id"], clean, cues))
    full = "".join(c for _, c, _ in scripts)
    print("转写中（whisper.cpp）…", file=sys.stderr)
    rec_chars = whisper_chars(w16, full, model, pcm)
    stream = "".join(norm(c) for c, _, _ in rec_chars)

    # —— 每拍在转写流里找窗口；有重录时取下一拍之前的最后一遍 ——
    def best_window(key, lo, hi):
        """在 stream[lo:hi] 里找与 key 最像的窗口，返回所有 ratio≥0.6 的候选 (start, end, ratio)。"""
        L = len(key); cands = []
        if L == 0:
            return cands
        step = max(1, L // 6)
        for s in range(lo, max(lo + 1, hi - int(L * 0.6)), step):
            sm = difflib.SequenceMatcher(None, key, stream[s:s + int(L * 1.3)], autojunk=False)
            blocks = [m for m in sm.get_matching_blocks() if m.size]
            if not blocks:
                continue
            r = sum(m.size for m in blocks) / L
            if r >= 0.6:
                cands.append((s + blocks[0].b, s + blocks[-1].b + blocks[-1].size, r))
        # 合并重叠候选，保留每簇里最好的
        cands.sort(); merged = []
        for c in cands:
            if merged and c[0] < merged[-1][1]:
                if c[2] > merged[-1][2]:
                    merged[-1] = c
            else:
                merged.append(c)
        return merged

    keys = ["".join(norm(ch) for ch in c if ch not in PUNCT and not ch.isspace()) for _, c, _ in scripts]
    windows, pos = [], 0
    for i, key in enumerate(keys):
        nxt_key = keys[i + 1] if i + 1 < len(keys) else ""
        hi = len(stream)
        if nxt_key:
            nc = best_window(nxt_key[:12], pos, len(stream))
            if nc:
                hi = nc[-1][0] + 1 if len(nc) > 1 else nc[0][0] + 1
        cands = [c for c in best_window(key, pos, hi)] or best_window(key, pos, len(stream))
        if not cands:
            windows.append(None); continue
        top = max(x[2] for x in cands)
        good = [c for c in cands if c[2] >= (top - 0.1 if top >= 0.75 else top)]
        w = good[-1]                       # 最后一遍
        windows.append((w[0], w[1], w[2], len(cands) - 1))
        pos = w[1]

    # —— 逐拍切出音频 + 压停顿 + 逐字时间 ——
    timings = {"beats": {}, "gap": 0.22, "lead": 0.3}
    lead = 0.0 if "--no-cut" in sys.argv else 0.3
    report, t_out, all_audio = [], lead, [np.zeros(int(lead * SR), np.float32)]
    for (bid, clean, cues), w in zip(scripts, windows):
        if w is None:
            report.append(f"⚠ {bid}: 录音里没找到这一拍"); continue
        s_i, e_i, ratio, retakes = w
        a = max(0.0, rec_chars[s_i][1] - 0.08); bnd = min(len(pcm) / SR, rec_chars[e_i - 1][2] + 0.15)
        if "--no-cut" in sys.argv:
            a, bnd = 0.0, len(pcm) / SR
        # 保留区间：去掉拍内长停顿的中段
        keep, cur = [], a
        for p0, p1 in ([] if "--no-cut" in sys.argv else pauses(pcm, a, bnd)):
            if p1 - p0 > max_pause:
                keep.append((cur, p0 + keep_pause / 2)); cur = p1 - keep_pause / 2
        keep.append((cur, bnd))

        def tmap(x):
            acc = 0.0
            for k0, k1 in keep:
                if x <= k1:
                    return acc + max(0.0, x - k0)
                acc += k1 - k0
            return acc
        seg = np.concatenate([pcm[int(k0 * SR):int(k1 * SR)] for k0, k1 in keep])
        fade = int(0.004 * SR)
        for k in range(1, len(keep)):        # 接缝处微淡入淡出，避免咔哒声
            j = int(sum(k1 - k0 for k0, k1 in keep[:k]) * SR)
            seg[max(0, j - fade):j] *= np.linspace(1, 0, min(fade, j))
            seg[j:j + fade] *= np.linspace(0, 1, len(seg[j:j + fade]))
        bwav = out / "audio" / f"{bid}.wav"
        with wave.open(str(bwav), "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SR); wf.writeframes(np.clip(seg, -32768, 32767).astype(np.int16).tobytes())
        dur = len(seg) / SR
        # 剧本字 ↔ 转写字 对齐 → 每个剧本字的开始时间（拍内相对，压停顿后）
        sk = [(i, norm(ch)) for i, ch in enumerate(clean) if ch not in PUNCT and not ch.isspace()]
        sm = difflib.SequenceMatcher(None, "".join(c for _, c in sk), stream[s_i:e_i], autojunk=False)
        times = [None] * len(clean)
        for m in sm.get_matching_blocks():
            for k in range(m.size):
                times[sk[m.a + k][0]] = tmap(rec_chars[s_i + m.b + k][1])
        known = [(i, v) for i, v in enumerate(times) if v is not None]
        if not known:
            known = [(0, 0.05), (len(clean) - 1, dur - 0.1)]
        for i in range(len(clean)):           # 插值补齐（含标点位置）
            if times[i] is None:
                prev = max([k for k in known if k[0] < i], default=known[0], key=lambda k: k[0])
                nxt = min([k for k in known if k[0] > i], default=known[-1], key=lambda k: k[0])
                times[i] = prev[1] if nxt[0] == prev[0] else prev[1] + (nxt[1] - prev[1]) * (i - prev[0]) / (nxt[0] - prev[0])
        chars = [round(t_out + v, 3) for v in times]
        words = [{"text": ch, "start": chars[i], "end": chars[i + 1] if i + 1 < len(chars) else round(t_out + dur, 3), "i0": i, "i1": i + 1}
                 for i, ch in enumerate(clean) if ch not in PUNCT and not ch.isspace()]
        cue_t = {k: chars[min(v, len(chars) - 1)] for k, v in cues.items()}
        timings["beats"][bid] = {"file": f"audio/{bid}.wav", "text": clean, "start": round(t_out, 3), "end": round(t_out + dur, 3),
                                 "duration": round(dur, 3), "words": words, "chars": chars, "cues": cue_t, "align": round(ratio, 3)}
        all_audio += [seg, np.zeros(int(0.22 * SR), np.float32)]
        n_chars = len([c for c in clean if c not in PUNCT and not c.isspace()])
        flag = "⚠ " if ratio < 0.8 else ""
        report.append(f"{flag}{bid:5s} {dur:6.2f}s  {n_chars / dur:4.1f} 字/秒  对齐 {ratio:.0%}" + (f"  删掉重录 {retakes} 遍" if retakes else ""))
        t_out += dur + 0.22
    timings["total"] = round(t_out, 3)
    full_audio = np.concatenate(all_audio)
    with wave.open(str(out / "narration.wav"), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SR); wf.writeframes(np.clip(full_audio, -32768, 32767).astype(np.int16).tobytes())
    (out / "timings.json").write_text(json.dumps(timings, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.unlink(missing_ok=True); w16.unlink(missing_ok=True)
    print("\n".join(report))
    print(f"合计 {t_out:.1f}s → {out / 'timings.json'}")


if __name__ == "__main__":
    main()
