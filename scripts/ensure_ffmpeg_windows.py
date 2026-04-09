#!/usr/bin/env python3
"""下载 Windows x64 便携 ffmpeg.exe 到 deps/ffmpeg/ffmpeg.exe（打 Windows 包前在开发机执行，需联网）。

使用 curl 拉取 zip（避免部分环境 urllib SSL 失败）。失败即退出，不静默跳过。
若已存在且体积合理则跳过。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEST_EXE = BASE_DIR / "deps" / "ffmpeg" / "ffmpeg.exe"
ZIP_URL = os.environ.get(
    "LOBSTER_FFMPEG_WIN64_ZIP",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
)
MIN_BYTES = 500_000


def _find_ffmpeg_in_zip(z: zipfile.ZipFile) -> str:
    names = z.namelist()
    for n in names:
        n_norm = n.replace("\\", "/")
        if n_norm.endswith("/bin/ffmpeg.exe"):
            return n
    for n in names:
        if n.endswith("ffmpeg.exe") and "ffprobe" not in n.lower():
            return n
    raise RuntimeError("zip 内未找到 ffmpeg.exe")


def _download_zip(path: Path) -> None:
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("未找到 curl 可执行文件，无法下载 ffmpeg zip")
    print(f"==> 下载: {ZIP_URL}")
    r = subprocess.run(
        [curl, "-fsSL", "-o", str(path), ZIP_URL],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        raise RuntimeError(f"curl 下载失败 (exit {r.returncode}): {err or 'no output'}")


def main() -> None:
    DEST_EXE.parent.mkdir(parents=True, exist_ok=True)
    if DEST_EXE.is_file() and DEST_EXE.stat().st_size >= MIN_BYTES:
        print(f"==> ffmpeg 已存在，跳过下载: {DEST_EXE} ({DEST_EXE.stat().st_size} bytes)")
        return

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tf:
        tmp_zip = Path(tf.name)
    try:
        _download_zip(tmp_zip)
        with zipfile.ZipFile(tmp_zip, "r") as z:
            inner = _find_ffmpeg_in_zip(z)
            data = z.read(inner)
        if len(data) < MIN_BYTES:
            raise RuntimeError(f"解压出的 ffmpeg 异常小: {len(data)} bytes")
        DEST_EXE.write_bytes(data)
        try:
            os.chmod(DEST_EXE, 0o755)
        except OSError:
            pass
        print(f"==> 已写入: {DEST_EXE} ({DEST_EXE.stat().st_size} bytes)")
    finally:
        tmp_zip.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
