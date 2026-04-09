"""校验 deps/wheels 中文件能否满足 requirements.txt（不访问 PyPI）。"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename
from packaging.version import Version

BASE = Path(__file__).resolve().parent.parent
WHEELS = BASE / "deps" / "wheels"
REQ = BASE / "requirements.txt"


def _runtime_requirements_no_tos() -> Path:
    """与 install.bat 一致：主步骤不含 tos，由 2b 单独安装。"""
    lines = [
        ln
        for ln in REQ.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#") and "tos" not in ln.lower()
    ]
    tf = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    tf.write("\n".join(lines) + "\n")
    tf.close()
    return Path(tf.name)


def _pip_offline_resolve_check() -> None:
    """用 pip 在纯离线模式下试解析整棵依赖树（含传递依赖）。仅 Windows + 3.12 与完整包一致。"""
    if sys.platform != "win32":
        print(
            "INFO: 传递依赖 pip dry-run 仅在 Windows 上执行；"
            "当前平台已用顶层包名校验 deps/wheels。"
        )
        return
    if sys.version_info[:2] != (3, 12):
        print(
            f"WARN: 传递依赖 pip dry-run 跳过（需 Python 3.12，当前 {sys.version_info.major}.{sys.version_info.minor}）。"
        )
        return
    help_r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--help"],
        capture_output=True,
        text=True,
    )
    if help_r.returncode != 0 or "--dry-run" not in (help_r.stdout or ""):
        print("WARN: pip 无 --dry-run，跳过传递依赖校验。")
        return
    path = _runtime_requirements_no_tos()
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--dry-run",
                "--no-index",
                "--find-links",
                str(WHEELS),
                "-r",
                str(path),
            ],
            check=True,
        )
    finally:
        path.unlink(missing_ok=True)
    print("OK: pip 离线解析通过（含传递依赖，与 install.bat 主步骤一致）。")


def _collect_dists() -> dict[str, list[Version]]:
    """canonical_name -> [versions]"""
    out: dict[str, list[Version]] = {}
    if not WHEELS.is_dir():
        return out
    for p in WHEELS.iterdir():
        if p.suffix == ".whl":
            try:
                name, ver, _build, _tags = parse_wheel_filename(p.name)
            except Exception:
                continue
            cn = canonicalize_name(name)
            out.setdefault(cn, []).append(ver)
        elif p.name.endswith(".tar.gz"):
            try:
                name, ver = parse_sdist_filename(p.name)
            except Exception:
                continue
            cn = canonicalize_name(name)
            out.setdefault(cn, []).append(ver)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="校验 deps/wheels 与 requirements.txt（不访问 PyPI）")
    ap.add_argument(
        "--skip-transitive",
        action="store_true",
        help="跳过 Windows 上 pip install --dry-run 传递依赖校验",
    )
    args = ap.parse_args()

    if not REQ.is_file():
        print("ERROR: requirements.txt 不存在", file=sys.stderr)
        sys.exit(1)
    if not WHEELS.is_dir():
        print("ERROR: deps/wheels 不存在", file=sys.stderr)
        sys.exit(1)

    dists = _collect_dists()
    missing: list[str] = []
    for raw in REQ.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        req = Requirement(line)
        cn = canonicalize_name(req.name)
        vers = dists.get(cn)
        if not vers:
            missing.append(f"{req.name}: 无 wheel/sdist")
            continue
        ok = any(req.specifier.contains(v, prereleases=True) for v in vers)
        if not ok:
            missing.append(f"{req.name}: 版本不满足 {req.specifier}，已有 {vers}")

    if missing:
        print("ERROR: deps/wheels 与 requirements.txt 不一致：", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        sys.exit(1)
    print("OK: deps/wheels 覆盖 requirements.txt（含剪辑依赖 httpx / SQLAlchemy / Pillow 等）")

    if not args.skip_transitive:
        _pip_offline_resolve_check()


if __name__ == "__main__":
    main()
