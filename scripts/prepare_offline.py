"""Download wheels into deps/wheels for offline pip install (no PyPI at install time).

在开发机执行（需联网），将 deps/wheels 填满后再打完整包或拷贝到无网环境；失败即退出。

Targets:
  windows  — Windows 完整包：win_amd64 + CPython 3.12，与 install.bat 离线安装一致（默认）
  current  — 当前机器的 Python 版本与平台（macOS/Linux/Windows 本机开发机填 deps/wheels）

Usage:
    python scripts/prepare_offline.py
    python scripts/prepare_offline.py --target current
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEPS_DIR = BASE_DIR / "deps"
WHEELS_DIR = DEPS_DIR / "wheels"
REQUIREMENTS = BASE_DIR / "requirements.txt"

PYTHON = sys.executable
PLATFORM = "win_amd64"
PYTHON_VERSION = "312"


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def _download_pip_toolchain_windows() -> None:
    print("[2/4] pip / setuptools / wheel (win_amd64 cp312) ...")
    _run(
        [
            PYTHON,
            "-m",
            "pip",
            "download",
            "pip",
            "setuptools",
            "wheel",
            "--dest",
            str(WHEELS_DIR),
            "--only-binary",
            ":all:",
            "--platform",
            PLATFORM,
            "--python-version",
            PYTHON_VERSION,
        ]
    )


def _download_pip_toolchain_current() -> None:
    print("[2/4] pip / setuptools / wheel (当前解释器平台) ...")
    _run(
        [
            PYTHON,
            "-m",
            "pip",
            "download",
            "pip",
            "setuptools",
            "wheel",
            "--dest",
            str(WHEELS_DIR),
        ]
    )


def _requirements_lines_no_tos() -> Path:
    lines = [
        ln
        for ln in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#") and "tos" not in ln.lower()
    ]
    tf = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    tf.write("\n".join(lines) + "\n")
    tf.close()
    return Path(tf.name)


def _download_requirements_windows(req_no_tos: Path) -> None:
    print("[3/4] requirements.txt → wheels (binary only, win_amd64) ...")
    _run(
        [
            PYTHON,
            "-m",
            "pip",
            "download",
            "-r",
            str(req_no_tos),
            "--dest",
            str(WHEELS_DIR),
            "--only-binary",
            ":all:",
            "--platform",
            PLATFORM,
            "--python-version",
            PYTHON_VERSION,
        ]
    )


def _download_requirements_current(req_no_tos: Path) -> None:
    print("[3/4] requirements.txt → wheels（当前平台，允许 sdist）...")
    _run(
        [
            PYTHON,
            "-m",
            "pip",
            "download",
            "-r",
            str(req_no_tos),
            "--dest",
            str(WHEELS_DIR),
        ]
    )


def _remove_wheels_unusable_on_windows_embed() -> None:
    """在 macOS 制包时可能混入非 Windows wheel；嵌入式包仅保留 Windows 可用的标签。"""
    bad = ("macosx", "manylinux", "musllinux", "linux_x86_64", "linux_aarch64", "linux_i686")
    for p in list(WHEELS_DIR.glob("*.whl")):
        n = p.name.lower()
        if any(b in n for b in bad):
            print(f"  remove (not Windows x64): {p.name}")
            p.unlink()


def _download_tos_for_windows_embed() -> None:
    """tos 常为 sdist；pip 在带 --platform 时要么 --no-deps 要么 --only-binary :all:（与 sdist 冲突）。

    做法：先只拉 tos 包；再单独拉 win_amd64+cp312 的传递依赖 wheel；crcmod 通常仅 sdist。
    """
    print("  [4a] tos 包本体（sdist，--no-deps + platform）...")
    _run(
        [
            PYTHON,
            "-m",
            "pip",
            "download",
            "tos>=2.9.0",
            "--no-deps",
            "--platform",
            PLATFORM,
            "--python-version",
            PYTHON_VERSION,
            "--dest",
            str(WHEELS_DIR),
        ]
    )
    print("  [4b] tos 传递依赖（仅 wheel，win_amd64 cp312；wrapt 版本以 tos setup.py 为准）...")
    _run(
        [
            PYTHON,
            "-m",
            "pip",
            "download",
            "Deprecated>=1.2.13,<2.0.0",
            "wrapt==1.16.0",
            "requests>=2.19.1,==2.*",
            "pytz",
            "six",
            "--dest",
            str(WHEELS_DIR),
            "--platform",
            PLATFORM,
            "--python-version",
            PYTHON_VERSION,
            "--only-binary",
            ":all:",
        ]
    )
    print("  [4c] crcmod（通常无 Windows wheel，仅 sdist）...")
    _run(
        [
            PYTHON,
            "-m",
            "pip",
            "download",
            "crcmod>=1.7",
            "--no-deps",
            "--dest",
            str(WHEELS_DIR),
        ]
    )
    _remove_wheels_unusable_on_windows_embed()


def main() -> None:
    ap = argparse.ArgumentParser(description="Fill deps/wheels for offline install")
    ap.add_argument(
        "--target",
        choices=("windows", "current"),
        default="windows",
        help="windows=完整包(win_amd64+cp312)；current=本机 Python 平台（macOS/Linux 开发填库）",
    )
    args = ap.parse_args()

    WHEELS_DIR.mkdir(parents=True, exist_ok=True)
    DEPS_DIR.mkdir(parents=True, exist_ok=True)

    get_pip = DEPS_DIR / "get-pip.py"
    if get_pip.is_file() and get_pip.stat().st_size > 1000:
        print(f"[1/4] get-pip.py 已存在，跳过下载: {get_pip}")
    else:
        print("[1/4] Downloading get-pip.py ...")
        urllib.request.urlretrieve(
            "https://bootstrap.pypa.io/get-pip.py",
            str(get_pip),
        )
        print(f"  OK: {get_pip}")

    if not REQUIREMENTS.exists():
        raise FileNotFoundError("requirements.txt not found")

    if args.target == "windows":
        _download_pip_toolchain_windows()
    else:
        _download_pip_toolchain_current()

    req_no_tos = _requirements_lines_no_tos()
    try:
        if args.target == "windows":
            _download_requirements_windows(req_no_tos)
        else:
            _download_requirements_current(req_no_tos)
    finally:
        req_no_tos.unlink(missing_ok=True)

    print("[4/4] tos（及传递依赖）...")
    if args.target == "windows":
        _download_tos_for_windows_embed()
    else:
        _run(
            [
                PYTHON,
                "-m",
                "pip",
                "download",
                "tos>=2.9.0",
                "--dest",
                str(WHEELS_DIR),
            ]
        )

    whl_count = len(list(WHEELS_DIR.glob("*.whl")))
    tar_count = len(list(WHEELS_DIR.glob("*.tar.gz")))
    print(f"\nDone: {whl_count} .whl + {tar_count} .tar.gz in {WHEELS_DIR}")

    print("\n[校验] verify_offline_wheels.py ...")
    verify_py = BASE_DIR / "scripts" / "verify_offline_wheels.py"
    subprocess.run([PYTHON, str(verify_py)], check=True)


if __name__ == "__main__":
    main()
