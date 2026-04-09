"""
在无已安装 pip 时，仅从 deps/wheels/pip-*.whl 引导安装 pip / setuptools / wheel（--no-index）。
由 install.bat 调用；失败返回非 0。不访问网络。
"""
from __future__ import annotations

import glob
import os
import runpy
import sys


def main() -> int:
    root = os.environ.get("LOBSTER_ROOT") or os.getcwd()
    root = os.path.abspath(root)
    wheels = os.path.join(root, "deps", "wheels")
    if not os.path.isdir(wheels):
        print("[pip_bootstrap] ERR: missing deps\\wheels directory", file=sys.stderr)
        return 1
    pat = os.path.join(wheels, "pip-*.whl")
    matches = sorted(glob.glob(pat))
    if not matches:
        print("[pip_bootstrap] ERR: no pip-*.whl under deps\\wheels", file=sys.stderr)
        return 1
    whl = matches[-1]
    sys.path.insert(0, whl)
    sys.argv = [
        "pip",
        "install",
        "--no-index",
        "--find-links",
        wheels,
        "pip",
        "setuptools",
        "wheel",
    ]
    try:
        runpy.run_module("pip", run_name="__main__", alter_sys=True)
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
