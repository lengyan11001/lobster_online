"""Trace writer: local audit trail for every recovery attempt."""
from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2] / "publisher" / "agent_traces"
# 2026-11-17：trace 会写 jsonl + 截图，之前没有保留策略，长期会无限增长
#（也会被打进客户端代码包/诊断包）。只保留最近 7 天、总量不超过 200MB。
_TRACE_KEEP_DAYS = 7
_TRACE_MAX_BYTES = 200 * 1024 * 1024


def _prune_traces() -> None:
    try:
        if not _ROOT.is_dir():
            return
        cutoff = datetime.now().timestamp() - _TRACE_KEEP_DAYS * 86400
        days = [d for d in _ROOT.iterdir() if d.is_dir()]
        for d in days:
            try:
                if d.stat().st_mtime < cutoff:
                    for f in d.glob("*"):
                        try:
                            f.unlink()
                        except OSError:
                            pass
                    d.rmdir()
            except OSError:
                continue
        # 总量兜底：从最旧的开始删
        days = sorted([d for d in _ROOT.iterdir() if d.is_dir()], key=lambda d: d.name)
        total = 0
        for d in days:
            try:
                total += sum(f.stat().st_size for f in d.glob("*") if f.is_file())
            except OSError:
                continue
        for d in days:
            if total <= _TRACE_MAX_BYTES:
                break
            try:
                size = sum(f.stat().st_size for f in d.glob("*") if f.is_file())
                for f in d.glob("*"):
                    try:
                        f.unlink()
                    except OSError:
                        pass
                d.rmdir()
                total -= size
            except OSError:
                continue
    except Exception:
        pass


class TraceWriter:
    def __init__(self, *, platform: str, stage: str, env: str = "browser") -> None:
        self.trace_id = uuid.uuid4().hex[:16]
        self.platform = platform
        self.stage = stage
        self.env = env
        self.started_at = datetime.now()
        self.dir = _ROOT / self.started_at.strftime("%Y%m%d")
        self.path = self.dir / f"{self.trace_id}.jsonl"
        try:
            _prune_traces()
            self.dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            self.path = Path("")

    def _write(self, payload: Dict[str, Any]) -> None:
        if not self.path:
            return
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug("[agent-trace] write failed: %s", exc)

    def event(self, kind: str, **fields: Any) -> None:
        self._write({
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "trace_id": self.trace_id,
            "platform": self.platform,
            "stage": self.stage,
            "env": self.env,
            "kind": kind,
            **fields,
        })

    def save_screenshot(self, data_b64: str, name: str = "shot") -> str:
        if not data_b64 or not self.path:
            return ""
        try:
            raw = base64.b64decode(data_b64)
            target = self.dir / f"{self.trace_id}_{name}.jpg"
            target.write_bytes(raw)
            return str(target)
        except Exception:
            return ""

    def finish(self, ok: bool, steps: int, error: str = "") -> None:
        self.event("finish", ok=bool(ok), steps=int(steps), error=str(error)[:500],
                   duration_s=round((datetime.now() - self.started_at).total_seconds(), 2))
