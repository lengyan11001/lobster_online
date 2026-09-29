"""跑回归测试但跳过微信相关测试。

微信那套测试会去连微信窗口（wxauto4），在用户机器上不能随便跑，
所以默认跑测试请用这个入口；确实要测微信时再单独跑 scripts/round_test_wechat.py。

用法：.\python\python.exe scripts\run_tests_no_wechat.py
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS = ROOT / "backend" / "tests"

ignore_args: list[str] = []
for pattern in ("test_native_wechat_*.py", "test_wechat_*.py", "*wechat*.py"):
    for path in sorted(TESTS.glob(pattern)):
        if path.is_file():
            ignore_args.extend(["--ignore", str(path)])
skip = sorted({ignore_args[i + 1] for i in range(0, len(ignore_args), 2)})
for path in skip:
    print("skip:", pathlib.Path(path).name)

command = [sys.executable, "-m", "pytest", str(TESTS), "-q", *ignore_args]
raise SystemExit(subprocess.call(command))
