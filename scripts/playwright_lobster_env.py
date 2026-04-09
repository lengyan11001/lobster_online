"""
让独立运行的探测脚本与 start.bat / install.bat 使用同一套 Playwright 浏览器路径。

pip 里的 playwright 包不含浏览器本体；install.bat 可能把浏览器放在项目 browser_chromium，
并期望通过环境变量 PLAYWRIGHT_BROWSERS_PATH 指向该目录。在 Cursor 里直接 python 跑脚本时
若未设置该变量，Playwright 只会查用户目录 ms-playwright，容易误判「还要再 install」。
"""
from __future__ import annotations

import os
from pathlib import Path


def ensure_playwright_browsers_path(lobster_root: Path) -> None:
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip():
        return
    local = lobster_root / "browser_chromium"
    if not local.is_dir():
        return
    try:
        if not any(local.iterdir()):
            return
    except OSError:
        return
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(local.resolve())
