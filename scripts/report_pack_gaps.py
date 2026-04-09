#!/usr/bin/env python3
"""
制包前检查：列出 lobster_online 内依赖是否齐全（只读，不下载、不改任何文件）。

不检查/不修改：install.bat、start.bat、run_*.bat（由人工维护）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def _has_glob(dirpath: Path, pattern: str) -> bool:
    if not dirpath.is_dir():
        return False
    return any(dirpath.glob(pattern))


def main() -> int:
    print("")
    print("=== lobster_online 制包依赖检查（只读，不改 install/start） ===")
    print("")

    rows: list[tuple[str, bool, str]] = []

    for bat in ("install.bat", "start.bat", "run_backend.bat", "run_mcp.bat"):
        p = BASE / bat
        rows.append((bat, p.is_file(), "应保留在仓库内，制包脚本不会改写"))

    vr = subprocess.run(
        [sys.executable, str(BASE / "scripts" / "verify_offline_wheels.py")],
        cwd=str(BASE),
        capture_output=True,
        text=True,
    )
    wheels_ok = vr.returncode == 0
    whint = "verify_offline_wheels.py 通过" if wheels_ok else (vr.stderr or vr.stdout or "校验失败").strip()[:200]
    rows.append(("deps/wheels 覆盖 requirements.txt", wheels_ok, whint))

    rows.append(("deps/wheels/pip-*.whl", _has_glob(BASE / "deps" / "wheels", "pip-*.whl"), ""))
    rows.append(("deps/get-pip.py", (BASE / "deps" / "get-pip.py").is_file(), ""))
    rows.append(("python/python.exe", (BASE / "python" / "python.exe").is_file(), "嵌入 Python，离线后端"))
    rows.append(("nodejs/node.exe", (BASE / "nodejs" / "node.exe").is_file(), "Windows 便携 Node，OpenClaw Gateway"))
    rows.append(("nodejs/package-lock.json", (BASE / "nodejs" / "package-lock.json").is_file(), "建议纳入仓库，npm ci 可复现"))
    rows.append(
        (
            "nodejs/node_modules/openclaw",
            (BASE / "nodejs" / "node_modules" / "openclaw").is_dir(),
            "完整包须预装，见 build_package.sh",
        )
    )
    rows.append(
        (
            "nodejs/node_modules/@tencent-weixin/openclaw-weixin",
            (BASE / "nodejs" / "node_modules" / "@tencent-weixin" / "openclaw-weixin").is_dir(),
            "微信通道插件，与 openclaw.json plugins 对应",
        )
    )
    rows.append(("deps/wheels/pycryptodome*.whl", _has_glob(BASE / "deps" / "wheels", "pycryptodome*.whl"), ""))
    rows.append(("deps/wheels/tos-*", _has_glob(BASE / "deps" / "wheels", "tos-*"), ""))
    rows.append(
        (
            "deps/vc_redist.x64.exe",
            (BASE / "deps" / "vc_redist.x64.exe").is_file(),
            "一键结果包会下载；无则部分原生模块可能需本机 VC++",
        )
    )
    rows.append(("deps/ffmpeg/ffmpeg.exe", (BASE / "deps" / "ffmpeg" / "ffmpeg.exe").is_file(), "剪辑"))
    rows.append(
        (
            "browser_chromium/",
            BASE.joinpath("browser_chromium").is_dir() and any(BASE.joinpath("browser_chromium").iterdir()),
            "发布/Playwright 离线",
        )
    )
    rows.append(("scripts/pip_bootstrap_from_wheel.py", (BASE / "scripts" / "pip_bootstrap_from_wheel.py").is_file(), ""))

    wname = max(len(r[0]) for r in rows)
    for name, ok, hint in rows:
        st = "OK" if ok else "MISSING"
        extra = f"  # {hint}" if hint and (not ok or len(hint) < 70) else ""
        print(f"  {name:<{wname}}  [{st}]{extra}")

    print("")

    oc = BASE / "nodejs" / "node_modules" / "openclaw"
    wx = BASE / "nodejs" / "node_modules" / "@tencent-weixin" / "openclaw-weixin"
    critical_fail = not wheels_ok or not _has_glob(BASE / "deps" / "wheels", "pip-*.whl")
    critical_fail = critical_fail or not (BASE / "scripts" / "pip_bootstrap_from_wheel.py").is_file()
    # 与 install.bat 离线 OpenClaw + 微信插件一致；缺则另一台机器解压后网关/插件不可用
    critical_fail = critical_fail or not (BASE / "python" / "python.exe").is_file()
    critical_fail = critical_fail or not (BASE / "nodejs" / "node.exe").is_file()
    critical_fail = critical_fail or not oc.is_dir() or not wx.is_dir()

    if critical_fail:
        # 全部走 stdout，避免终端先显示 stderr
        print("")
        print(
            "ERROR: 标为 MISSING 的核心项须先补齐（build_package.sh → nodejs npm ci、"
            "scripts/ensure_full_pack_deps.sh）后再打包。"
        )
        return 1

    print("结论: 核心依赖已齐（含 Python/Node 嵌入、OpenClaw、微信插件、离线 wheels）。")
    print(
        "      大体积常见来源: nodejs/node_modules(OpenClaw 树)、browser_chromium(Playwright)、"
        "python(嵌入+已装 site-packages)、deps/wheels、deps/ffmpeg；"
        "其中 Chromium/ffmpeg 仅部分功能必需，见下方 MISSING 项。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
