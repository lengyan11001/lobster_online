"""Standalone diagnostics exporter — 不依赖后端进程。

后端卡死 / 接口卡住时，直接跑这个脚本也能把日志打包出来：

    D:\lobster_online\python\python.exe D:\lobster_online\scripts\export_diagnostics.py

产物默认落在 <安装目录>/logs/pending_diagnostics/，并打印绝对路径。
"""
from __future__ import annotations

import json
import os
import platform
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
OUT_DIR = LOG_DIR / "pending_diagnostics"

# 与 backend/app/api/logs_api.py 保持同样的预算，避免又生成几十 MB 的包
TAIL_BYTES = 2 * 1024 * 1024
JSONL_TAIL_BYTES = 1024 * 1024
TOTAL_BYTES = 12 * 1024 * 1024

CANDIDATES = [
    "logs/app.log",
    "logs/app.log.1",
    "logs/native_wechat_diagnostics.jsonl",
    "logs/native_wechat_auto_reply.jsonl",
    "logs/native_wechat_auto_reply.jsonl.1",
    "logs/native_wechat_send.jsonl",
    "logs/native_wechat_contact_sync.jsonl",
    "logs/client_ui_trace.jsonl",
    "logs/wechat_read_fail.png",
    "logs/native_whatsapp.jsonl",
    "backend.log",
    "mcp.log",
    "desktop_launcher.log",
    "desktop_launcher.log.1",
    "CLIENT_CODE_VERSION.json",
    "static/client_version.json",
]


def _tail(path: Path, limit: int) -> tuple[bytes, int, bool]:
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > limit:
            fh.seek(-limit, os.SEEK_END)
            return fh.read(), size, True
        return fh.read(), size, False


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / f"lobster-diagnostics-{stamp}.zip"
    budget = TOTAL_BYTES
    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(ROOT),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "files": [],
    }
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in CANDIDATES:
            path = ROOT / rel
            if not path.is_file():
                continue
            if budget <= 0:
                summary["files"].append({"path": rel, "skipped": "budget_exhausted"})
                continue
            limit = JSONL_TAIL_BYTES if rel.endswith(".jsonl") else TAIL_BYTES
            limit = min(limit, budget)
            try:
                data, size, truncated = _tail(path, limit)
            except OSError as exc:
                summary["files"].append({"path": rel, "error": str(exc)[:120]})
                continue
            if truncated:
                data = (f"[tail only: last {limit} bytes of {size}]\n").encode() + data
            zf.writestr(rel.replace("\\", "/"), data)
            budget -= len(data)
            summary["files"].append({"path": rel, "size": size, "truncated": truncated})
        zf.writestr("diagnostic_summary.json",
                    json.dumps(summary, ensure_ascii=False, indent=2))
    print("OK")
    print("path:", target)
    print("size:", target.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
