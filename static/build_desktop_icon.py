#!/usr/bin/env python3
"""将多份 PNG 合并为 Windows .ico（无第三方依赖）。"""
import struct
from pathlib import Path


def _png_dimensions(png: bytes) -> tuple[int, int]:
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


def pngs_to_ico(png_paths: list[Path], out_ico: Path) -> None:
    chunks: list[bytes] = []
    for p in png_paths:
        data = p.read_bytes()
        chunks.append(data)

    # ICONDIR: reserved(2)=0, type(2)=1 (icon), count(2)
    count = len(chunks)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + count * 16
    entries = b""
    blob = b""
    for data in chunks:
        w, h = _png_dimensions(data)
        # ICO: width/height byte; 0 means 256
        wb = 0 if w >= 256 else w
        hb = 0 if h >= 256 else h
        size = len(data)
        entries += struct.pack("<BBBBHHII", wb, hb, 0, 0, 1, 32, size, offset)
        blob += data
        offset += size

    out_ico.write_bytes(header + entries + blob)


def main() -> None:
    base = Path(__file__).resolve().parent
    sizes = [16, 32, 48, 64, 128, 256]
    pngs = []
    for s in sizes:
        p = base / f"bihu_{s}.png"
        if not p.exists():
            raise SystemExit(f"missing {p}")
        pngs.append(p)
    out = base / "bihu_box.ico"
    pngs_to_ico(pngs, out)
    print(f"Wrote {out} ({len(sizes)} sizes)")


if __name__ == "__main__":
    main()
