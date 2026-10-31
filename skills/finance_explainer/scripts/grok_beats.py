#!/usr/bin/env python3
"""Grok 原生说话的数字人片段 → 每拍音频 + timings.json + host.json（画面与声音同步裁切，口型不会错位）。

用法：
  python3 grok_beats.py <episode.json> <clips.json> <输出目录> [--host-out host.json] [--anchor host/anchor.jpg] [--public engine/public]

clips.json：{"c03": "gen/grok_c03.mp4", "c02": {"src": "gen/grok_15s.mp4", "from": 0, "to": 8.9}, ...}
  - from/to 可选：一段长片里含几拍台词时，按秒切窗口复用。
做法：
  1. 取片段（或窗口）的音轨，用 align_recording 的逐段 whisper 对齐剧本（不剪中间、不压停顿）。
  2. 只去掉开头/结尾的静音：音频从首字前 0.06 秒到末字后 0.18 秒；画面用同样的起点（host.json 里每拍的 offset）。
  3. 写 audio/<id>.wav、timings.json（与 narrate_mimo / align_recording 同格式）、host.json（clips: {id: {src, offset}}）。
  4. 复制片段到 <public>/host/，并打印每拍对齐率与字/秒；对齐 < 80% 标 ⚠（Grok 偶尔会改词或漏读）。
换音色：整条 narration 交给「保留节奏的换声」（如 Seed Audio 1.0 的 re-voicing），出来的音频逐拍按 timings 切回 audio/<id>.wav 即可，时间轴不用重算。
"""
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import align_recording as ar  # noqa: E402

SR = ar.SR


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main():
    ep = json.loads(Path(sys.argv[1]).read_text())
    clips = json.loads(Path(sys.argv[2]).read_text())
    out = Path(sys.argv[3]); (out / "audio").mkdir(parents=True, exist_ok=True)
    public = Path(arg("--public", "")) if arg("--public") else None
    model = Path(arg("--model", str(ar.MODEL)))
    timings = {"beats": {}, "gap": 0.0, "lead": 0.0}
    host = {"src": arg("--anchor", "host/anchor.jpg"), "kind": "image", "face": [0.5, 0.33], "offset": 0.0, "clips": {}}
    t_cursor = 0.0
    for b in ep["beats"]:
        if not b.get("narration") or b["id"] not in clips:
            continue
        c = clips[b["id"]]; c = {"src": c} if isinstance(c, str) else c
        src = Path(c["src"]); w0 = float(c.get("from", 0.0)); w1 = c.get("to")
        tmp = out / f"_{b['id']}.wav"; w16 = out / f"_{b['id']}16.wav"
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{w0:.3f}"] + (["-to", f"{float(w1):.3f}"] if w1 else []) + ["-i", str(src), "-vn", "-ac", "1", "-ar", str(SR), "-af", "highpass=f=70", str(tmp)]
        subprocess.run(cmd, check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-ar", "16000", "-ac", "1", str(w16)], check=True)
        pcm = ar.load_pcm(tmp)
        clean, cues = ar.strip_cues(b["narration"])
        rc = ar.whisper_chars(w16, clean, model, pcm)
        stream = "".join(ar.norm(ch) for ch, _, _ in rc)
        sk = [(i, ar.norm(ch)) for i, ch in enumerate(clean) if ch not in ar.PUNCT and not ch.isspace()]
        import difflib
        sm = difflib.SequenceMatcher(None, "".join(x for _, x in sk), stream, autojunk=False)
        times = [None] * len(clean); matched = 0
        for m in sm.get_matching_blocks():
            for k in range(m.size):
                times[sk[m.a + k][0]] = rc[m.b + k][1]; matched += 1
        ratio = matched / max(1, len(sk))
        known = [(i, v) for i, v in enumerate(times) if v is not None] or [(0, 0.1), (len(clean) - 1, len(pcm) / SR - 0.2)]
        for i in range(len(clean)):
            if times[i] is None:
                prev = max([k for k in known if k[0] < i], default=known[0], key=lambda k: k[0])
                nxt = min([k for k in known if k[0] > i], default=known[-1], key=lambda k: k[0])
                times[i] = prev[1] if nxt[0] == prev[0] else prev[1] + (nxt[1] - prev[1]) * (i - prev[0]) / (nxt[0] - prev[0])
        s0 = max(0.0, (rc[0][1] if rc else 0.0) - 0.06)
        e1 = min(len(pcm) / SR, (rc[-1][2] if rc else len(pcm) / SR) + 0.18)
        seg = pcm[int(s0 * SR):int(e1 * SR)]
        with wave.open(str(out / "audio" / f"{b['id']}.wav"), "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SR); wf.writeframes(np.clip(seg, -32768, 32767).astype(np.int16).tobytes())
        dur = len(seg) / SR
        chars = [round(t_cursor + max(0.0, v - s0), 3) for v in times]
        words = [{"text": ch, "start": chars[i], "end": chars[i + 1] if i + 1 < len(chars) else round(t_cursor + dur, 3), "i0": i, "i1": i + 1}
                 for i, ch in enumerate(clean) if ch not in ar.PUNCT and not ch.isspace()]
        timings["beats"][b["id"]] = {"file": f"audio/{b['id']}.wav", "text": clean, "start": round(t_cursor, 3), "end": round(t_cursor + dur, 3),
                                     "duration": round(dur, 3), "words": words, "chars": chars,
                                     "cues": {k: chars[min(v, len(chars) - 1)] for k, v in cues.items()}, "align": round(ratio, 3)}
        name = f"host/grok_{src.stem}.mp4"
        if public:
            (public / "host").mkdir(parents=True, exist_ok=True)
            if not (public / name).exists():
                shutil.copyfile(src, public / name)
        host["clips"][b["id"]] = {"src": name, "offset": round(w0 + s0, 3)}
        n = len(sk)
        print(f"{'⚠ ' if ratio < 0.8 else ''}{b['id']:5s} {dur:5.2f}s  {n / dur:4.1f} 字/秒  对齐 {ratio:.0%}  画面起点 {w0 + s0:.2f}s")
        t_cursor += dur
        tmp.unlink(missing_ok=True); w16.unlink(missing_ok=True)
    timings["total"] = round(t_cursor, 3)
    (out / "timings.json").write_text(json.dumps(timings, ensure_ascii=False, indent=1))
    Path(arg("--host-out", str(out / "host.json"))).write_text(json.dumps(host, ensure_ascii=False, indent=1))
    print(f"→ {out / 'timings.json'}  {arg('--host-out', str(out / 'host.json'))}")


if __name__ == "__main__":
    main()
