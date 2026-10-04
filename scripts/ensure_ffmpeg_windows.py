#!/usr/bin/env python3
"""下载 Windows x64 便携 ffmpeg.exe + ffprobe.exe 到 deps/ffmpeg/（打 Windows 包前 / 修复依赖时执行，需联网）。

使用 curl 拉取 zip（避免部分环境 urllib SSL 失败）。失败即退出，不静默跳过。
已经存在且体积合理的二进制会跳过，缺哪个补哪个。

背景（2026-10-04 diag_20261004083756_12ea0605）：以前这里只解压 ffmpeg.exe，客户机
deps/ffmpeg 里只有 ffmpeg 没有 ffprobe，闪剪素材分辨率校验一直报
「本机缺少 ffprobe，无法校验素材分辨率」，数字人口播视频的素材准备全被跳过。
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
DEST_DIR = BASE_DIR / "deps" / "ffmpeg"
TARGET_BINARIES: tuple[str, ...] = ("ffmpeg.exe", "ffprobe.exe")
ZIP_URL = os.environ.get(
    "LOBSTER_FFMPEG_WIN64_ZIP",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
)
MIN_BYTES = 500_000


def _missing_binaries() -> list[str]:
    """deps/ffmpeg 下缺哪些二进制（体积太小视为没下全）。"""
    missing: list[str] = []
    for name in TARGET_BINARIES:
        path = DEST_DIR / name
        if path.is_file() and path.stat().st_size >= MIN_BYTES:
            continue
        missing.append(name)
    return missing


def _find_binary_in_zip(z: zipfile.ZipFile, name: str) -> str:
    names = [str(n).replace("\\", "/") for n in z.namelist()]
    for n in names:
        if n.endswith(f"/bin/{name}"):
            return n
    for n in names:
        if n.rsplit("/", 1)[-1] == name:
            return n
    raise RuntimeError(f"zip 内未找到 {name}")


def _find_ffmpeg_in_zip(z: zipfile.ZipFile) -> str:
    """兼容旧调用：只找 ffmpeg.exe。"""
    return _find_binary_in_zip(z, "ffmpeg.exe")


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


def _extract_binaries(zip_path: Path, wanted: list[str]) -> list[Path]:
    written: list[Path] = []
    with zipfile.ZipFile(zip_path, "r") as z:
        for name in wanted:
            inner = _find_binary_in_zip(z, name)
            data = z.read(inner)
            if len(data) < MIN_BYTES:
                raise RuntimeError(f"解压出的 {name} 异常小: {len(data)} bytes")
            dest = DEST_DIR / name
            dest.write_bytes(data)
            try:
                os.chmod(dest, 0o755)
            except OSError:
                pass
            written.append(dest)
    return written


def main() -> None:
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    missing = _missing_binaries()
    if not missing:
        print(f"==> ffmpeg/ffprobe 已存在，跳过下载: {DEST_DIR}")
        return
    print(f"==> 需要补齐: {', '.join(missing)}")

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tf:
        tmp_zip = Path(tf.name)
    try:
        _download_zip(tmp_zip)
        for path in _extract_binaries(tmp_zip, missing):
            print(f"==> 已写入: {path} ({path.stat().st_size} bytes)")
    finally:
        tmp_zip.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
