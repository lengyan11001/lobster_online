#!/usr/bin/env python3
"""把 episode.json（拍表）+ 旁白时间（timings.json）+ 素材表（assets.json）合成 Remotion 用的 timeline.json。

用法：
  python3 build_timeline.py <episode.json> <timings.json> <engine/public> [--assets assets.json] [--host host.json]
                            [--bgm music.mp3] [--from c01 --to c10] [--badge "演示片"]

- timings.json：narrate_mimo.py 的输出，或 align_recording.py（主播自录音频）的输出；格式相同：
  beats[id] = {start, end, words:[{text,start,end,i0,i1}], cues:{name: 绝对秒}}，audio/<id>.wav
- 每拍时长 = 该拍音频时长 + gap；章节卡（kind=chapter）用自己的 duration。
- 叠加卡片里的 "at": "cue名" 换算成整片时间 t；"start" = 拍开头。
- 素材 id（gen:xxx / stock:xxx）在 assets.json 里查 {src, kind}；查不到就在画面上画「素材待生成」占位。
- host.json：{"src": "host/anchor.jpg", "kind": "image", "face": [0.5, 0.3], "clips": {"c02": "host/c02.mp4", ...}, "offset": 0.23}
  clips 是每拍的口型视频（Wan 输出），offset 是 Wan 输出里音频相对画面的延迟（实测 0.23 秒）。
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

PUNCT = "，。！？；：、,.!?;:"
GAP = 0.18          # 拍与拍之间留白（秒）
CHAPTER_SFX = "sfx/impact.wav"


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def dur(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout
    return float(out.strip() or 0)


def sub_lines(tb, t0, rel0, maxlen=13):
    """原文 + 逐字开始时间 → 字幕行：按原文标点断句，超长再对半断开（字幕里不显示标点）。"""
    text, chars = tb["text"], tb["chars"]
    segs, cur = [], []
    for i, ch in enumerate(text):
        if ch in PUNCT:
            if cur: segs.append(cur); cur = []
            continue
        cur.append(i)
    if cur: segs.append(cur)
    lines = []
    for sg in segs:
        sg = [i for i in sg if text[i].strip()]
        if not sg: continue
        def ok_cut(j):  # 不在英文单词/数字中间断开
            a, b = text[sg[j - 1]], text[sg[j]]
            return not (a.isascii() and a.isalnum() and b.isascii() and b.isalnum())
        def split(seq_idx):
            if len(seq_idx) <= maxlen:
                return [seq_idx]
            mid = len(seq_idx) // 2
            for d in range(0, mid):
                for j in (mid - d, mid + d):
                    if 0 < j < len(seq_idx) and ok_cut(sg.index(seq_idx[j])):
                        return split(seq_idx[:j]) + split(seq_idx[j:])
            return [seq_idx[:mid], seq_idx[mid:]]
        parts = split(sg)
        for x in parts:
            lines.append(["".join(text[i] for i in x).replace("  ", " "), chars[x[0]], chars[min(x[-1] + 1, len(chars) - 1)] if x[-1] + 1 < len(chars) else tb["end"]])
    out = []
    for i, (s_, a, b) in enumerate(lines):
        nb = lines[i + 1][1] if i + 1 < len(lines) else b + 0.3
        out.append({"text": s_, "start": round(t0 + a - rel0, 3), "end": round(t0 + max(b, nb) - rel0 if nb - b < 0.6 else t0 + b + 0.3 - rel0, 3)})
    return out


def main():
    ep_p, tim_p, pub = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    ep = json.loads(ep_p.read_text())
    tim = json.loads(tim_p.read_text())
    assets = json.loads(Path(arg("--assets")).read_text()) if arg("--assets") and Path(arg("--assets")).exists() else {}
    host = json.loads(Path(arg("--host")).read_text()) if arg("--host") and Path(arg("--host")).exists() else {"src": "host/anchor.jpg", "kind": "image", "face": [0.5, 0.3]}
    bfrom, bto = arg("--from"), arg("--to")
    ids = [b["id"] for b in ep["beats"]]
    lo = ids.index(bfrom) if bfrom else 0
    hi = ids.index(bto) if bto else len(ids) - 1
    beats_in = ep["beats"][lo:hi + 1]

    voice_dir = pub / "voice"; voice_dir.mkdir(parents=True, exist_ok=True)
    tim_dir = tim_p.parent
    t = 0.0
    beats, subs, audio, sfx, host_clips = [], [], [], [], []
    prev_host = None
    loop_i = [0]
    for b in beats_in:
        nb = {k: v for k, v in b.items() if k not in ("narration", "overlays", "stage")}
        nb.setdefault("host", "off" if b.get("kind") == "chapter" else "full")
        cues = {}
        if b.get("kind") == "chapter":
            d = float(b.get("duration", 2.6))
            sfx.append({"src": CHAPTER_SFX, "t": round(t, 3), "volume": 0.55})
        else:
            tb = tim["beats"][b["id"]]
            src = tim_dir / "audio" / f"{b['id']}.wav"
            if not src.exists():
                src = tim_dir / tb["file"]
            dst = voice_dir / f"{b['id']}{src.suffix}"
            shutil.copyfile(src, dst)
            adur = dur(dst)
            rel0 = tb["start"] - 0.0           # timings 里该拍的绝对起点（对应 audio 文件 0 秒）
            audio.append({"src": f"voice/{dst.name}", "start": round(t, 3), "dur": round(adur, 3)})
            cues = {k: round(t + (v - rel0), 3) for k, v in tb.get("cues", {}).items()}
            if tb.get("chars"):
                subs += sub_lines(tb, t, rel0)
            d = adur + GAP
            clip = host.get("clips", {}).get(b["id"])
            if clip:
                csrc, coff = (clip["src"], clip.get("offset", 0.0)) if isinstance(clip, dict) else (clip, host.get("offset", 0.0))
                host_clips.append({"src": csrc, "start": round(t, 3), "end": round(t + d, 3), "offset": coff})
            elif host.get("loops") and nb["host"] != "off":
                # 没有专属口型片段：用通用说话循环顶上，长拍拆段轮换（每段 ≤ 循环长度 − 0.5 秒）
                L = float(host.get("loop_len", 10.0)) - 0.5
                k = 0.0
                while k < d - 0.05:
                    seg = min(L, d - k)
                    host_clips.append({"src": host["loops"][loop_i[0] % len(host["loops"])], "start": round(t + k, 3), "end": round(t + k + seg, 3),
                                       "offset": host.get("offset", 0.0)})
                    loop_i[0] += 1; k += seg
        nb["start"], nb["end"] = round(t, 3), round(t + d, 3)
        cues["start"] = nb["start"]
        nb["cues"] = cues

        def at(x):
            return cues.get(x, nb["start"]) if isinstance(x, str) else nb["start"]

        ovs = []
        for o in b.get("overlays", []):
            o = json.loads(json.dumps(o))
            o["t"] = at(o.get("at", "start"))
            o["t2"] = at(o["until"]) if o.get("until") else o.get("t2", nb["end"])
            for key in ("items", "bubble"):
                for it in o.get(key, []) or []:
                    if isinstance(it, dict):
                        it["t"] = at(it.get("at", o.get("at", "start")))
            ovs.append(o)
            if o["type"] in ("comment", "term", "list", "datestack", "sticker", "questions"):
                sfx.append({"src": "sfx/pop.wav", "t": o["t"], "volume": 0.35})
            elif o["type"] == "kinetic" and str(o.get("style", "")).startswith("stamp"):
                sfx.append({"src": "sfx/slam-1.wav", "t": o["t"], "volume": 0.3})
            elif o["type"] == "counter":
                sfx.append({"src": "sfx/ding.wav", "t": o["t"] + 1.1, "volume": 0.3})
        nb["overlays"] = ovs
        if b.get("stage"):
            s = json.loads(json.dumps(b["stage"]))
            for key in ("bars", "items", "lines"):
                for it in s.get(key, []) or []:
                    it["t"] = at(it.get("at", "start"))
            if "reveal" in s and isinstance(s["reveal"], list):
                s["reveal"] = [at(x) for x in s["reveal"]]
            if s.get("callout"):
                s["callout"]["t"] = at(s["callout"].get("at", "start"))
            if s.get("type") == "quiz":
                s["revealT"] = at(s.get("reveal", "start"))
            nb["stage"] = s
        if prev_host is not None and nb["host"] != prev_host and b.get("kind") != "chapter":
            sfx.append({"src": "sfx/swish-1.wav", "t": nb["start"], "volume": 0.22})
        prev_host = nb["host"]
        beats.append(nb)
        t += d

    used = {}
    for b in beats:
        ids_ = [b.get("bg")] + [o.get("src") for o in b["overlays"]]
        for i in ids_:
            if i and isinstance(i, str) and ":" in i:
                k = i.split(":", 1)[1]
                used[k] = assets.get(k, {"src": "", "kind": "image", "label": "待生成"})

    meta = dict(ep["meta"])
    meta.update({"duration": round(t + 0.6, 3), "host": host})
    if arg("--bgm"):
        meta["bgm"] = arg("--bgm"); meta["bgmVolume"] = float(arg("--bgm-volume", "0.13"))
    if arg("--badge"):
        meta["badge"] = arg("--badge")
    tl = {"meta": meta, "beats": beats, "subs": subs, "audio": audio, "hostClips": host_clips, "sfx": sfx, "assets": used}
    (pub / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1))
    print(f"{len(beats)} 拍  {t:.1f}s  字幕 {len(subs)} 行  音效 {len(sfx)}  口型片段 {len(host_clips)}  素材 {len(used)}（缺 {sum(1 for v in used.values() if not v.get('src'))}）")


if __name__ == "__main__":
    main()
