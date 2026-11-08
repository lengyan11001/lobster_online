#!/usr/bin/env python3
"""从数字人3.0模板样片重新生成封面帧（static/koubo/templates/covers/*.jpg）。

样片替换后跑一次即可：python scripts/koubo_regen_covers.py
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "static" / "koubo" / "templates" / "samples"
COVERS = ROOT / "static" / "koubo" / "templates" / "covers"


def find_ffmpeg() -> str:
    for rel in ("deps/ffmpeg/ffmpeg.exe", "deps/ffmpeg/ffmpeg"):
        p = ROOT / rel
        if p.is_file():
            return str(p)
    return "ffmpeg"


def main() -> int:
    ff = find_ffmpeg()
    COVERS.mkdir(parents=True, exist_ok=True)
    made = 0
    files = sorted(SAMPLES.glob("*.mp4"))
    for mp4 in files:
        out = COVERS / (mp4.stem + ".jpg")
        cmd = [ff, "-y", "-loglevel", "error", "-ss", "0.8", "-i", str(mp4), "-frames:v", "1",
               "-vf", "scale=540:960:force_original_aspect_ratio=increase,crop=540:960", "-q:v", "3", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and out.is_file() and out.stat().st_size > 1000:
            made += 1
        else:
            print("FAIL", mp4.stem, (r.stderr or "")[-200:])
    print("covers", made, "of", len(files))
    return 0 if made == len(files) else 1


if __name__ == "__main__":
    raise SystemExit(main())
