"""将库中 naive UTC 时间序列化为带 Z 的 ISO-8601，便于前端按 UTC 解析后再转北京时间。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def isoformat_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")
