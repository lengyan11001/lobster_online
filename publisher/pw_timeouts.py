"""发布自动化 Playwright 超时：慢网/弱机用环境变量放大，无需改业务代码。

- PUBLISH_PLAYWRIGHT_TIMEOUT_SCALE：全局倍数（1～6），作用于默认导航/点击等毫秒值。
- PUBLISH_NAVIGATION_TIMEOUT_MS：若设置正整数，则 **覆盖** page.goto 的 timeout（毫秒），优先级高于 SCALE。
- PUBLISH_FILE_INPUT_WAIT_MS：发布页加载后，轮询等待 ``input[type=file]`` / ``input.upload-input`` 的总时长（毫秒）。
"""
from __future__ import annotations

import os


def publish_timeout_scale() -> float:
    raw = (os.environ.get("PUBLISH_PLAYWRIGHT_TIMEOUT_SCALE") or "1.0").strip()
    try:
        v = float(raw)
    except ValueError:
        v = 1.0
    return max(1.0, min(6.0, v))


def ms(base_ms: int) -> int:
    b = int(base_ms)
    if b < 1:
        b = 1000
    return int(max(1000, min(600_000, round(b * publish_timeout_scale()))))


def navigation_timeout_ms(default_ms: int = 30000) -> int:
    raw = (os.environ.get("PUBLISH_NAVIGATION_TIMEOUT_MS") or "").strip()
    if raw.isdigit():
        v = int(raw)
        return max(5000, min(600_000, v))
    return ms(default_ms)


def file_input_wait_ms(default_ms: int = 45000) -> int:
    raw = (os.environ.get("PUBLISH_FILE_INPUT_WAIT_MS") or "").strip()
    if raw.isdigit():
        v = int(raw)
        return max(3000, min(300_000, v))
    return ms(default_ms)
