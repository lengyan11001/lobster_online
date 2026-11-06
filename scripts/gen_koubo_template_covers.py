# -*- coding: utf-8 -*-
"""Generate 320x180 SVG covers for digital-human-koubo template specs."""
from __future__ import annotations

import argparse
import json
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPECS = ROOT / "skills" / "digital_human_koubo" / "templates" / "specs"
OUT = ROOT / "static" / "koubo" / "templates"
FONT = "'Noto Sans SC','Microsoft YaHei',sans-serif"


def build(spec: dict) -> str:
    style = spec.get("style") or {}
    copy = spec.get("copy") or {}
    a1 = escape(str(style.get("accent") or "#FFD84D"))
    a2 = escape(str(style.get("accent2") or "#FF4D8D"))
    a3 = escape(str(style.get("accent3") or "#4DE1FF"))
    badge = escape(str(copy.get("badge") or spec.get("name") or "AI 口播"))
    title = escape(str(spec.get("cover_title") or spec.get("name") or ""))
    sub = escape(str(spec.get("cover_sub") or spec.get("description") or "")[:16])
    bw = max(98, 26 + len(str(copy.get("badge") or "")) * 11)
    size = 24 if len(str(spec.get("cover_title") or "")) <= 10 else 21
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">\n'
        '  <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{a1}"/><stop offset="0.55" stop-color="{a2}"/><stop offset="1" stop-color="{a3}"/>'
        '</linearGradient><linearGradient id="d" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#050812" stop-opacity="0.18"/><stop offset="1" stop-color="#050812" stop-opacity="0.80"/>'
        '</linearGradient></defs>\n'
        '  <rect width="320" height="180" fill="url(#g)"/><rect width="320" height="180" fill="url(#d)"/>\n'
        f'  <circle cx="278" cy="28" r="42" fill="{a1}" opacity="0.16"/><circle cx="302" cy="148" r="52" fill="{a3}" opacity="0.12"/>\n'
        f'  <rect x="16" y="14" rx="10" width="{bw}" height="22" fill="#050812" opacity="0.48" stroke="#fff" stroke-opacity="0.18"/>'
        f'<circle cx="27" cy="25" r="3" fill="{a1}"/>'
        f'<text x="35" y="29" fill="#fff" font-size="10" font-family="{FONT}">{badge}</text>\n'
        f'  <rect x="16" y="52" width="4" height="44" rx="2" fill="{a1}"/>'
        f'<text x="29" y="72" fill="#fff" font-size="{size}" font-weight="700" font-family="{FONT}">{title}</text>'
        f'<text x="29" y="96" fill="#fff" font-size="11" font-family="{FONT}" opacity="0.78">{sub}</text>\n'
        f'  <rect x="16" y="128" rx="10" width="188" height="34" fill="#050812" opacity="0.42" stroke="#fff" stroke-opacity="0.12"/>'
        f'<text x="28" y="149" fill="#fff" font-size="12" font-weight="700" font-family="{FONT}">一张照片 · 一段口播</text>'
        f'<text x="224" y="149" fill="{a1}" font-size="11" font-weight="700" font-family="{FONT}">AI 成片</text>\n'
        '</svg>\n'
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    made = 0
    for path in sorted(SPECS.glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        target = OUT / (str(spec.get("id") or path.stem) + ".svg")
        if target.is_file() and not args.force:
            continue
        target.write_text(build(spec), encoding="utf-8")
        made += 1
        print("cover", target.name)
    print("generated", made)


if __name__ == "__main__":
    main()
