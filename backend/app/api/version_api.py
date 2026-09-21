"""前后端版本自检：界面（static）和后端（backend）必须来自同一个包。

用途：客户端一升级就会出现"界面是新的、后端还是旧的"这种半包情况（典型现象是前端在调
一个后端没有的接口 → 一路 404）。这里给出一个权威口径：

* static_version：static/client_version.json（界面侧自己读的也是这个文件）
* backend_version：CLIENT_CODE_VERSION.json + 后端进程启动时间
* expected_routes_missing：关键接口是否真的注册上了（后端代码不完整会当场暴露）
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Request

router = APIRouter()

ROOT_DIR = Path(__file__).resolve().parents[3]
_BACKEND_STARTED_AT = time.time()

# 关键接口清单：任何一条缺失，就说明后端代码不是当前包（半包/旧包）
EXPECTED_ROUTES: tuple = (
    "/api/health",
    "/api/version",
    "/api/native-whatsapp/status",
    "/api/native-whatsapp/sessions/sync",
    "/api/twilio-whatsapp/config",
    "/api/alibaba-inquiries/accounts",
    "/api/alibaba-inquiries/accounts/{account_id}/dashboard",
    "/api/alibaba-inquiries/accounts/{account_id}/store/sync",
    "/api/alibaba-inquiries/accounts/{account_id}/reception/run",
)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _iso(value: Any) -> str:
    text = str(value or "").strip()
    return text


def version_payload(request: Request) -> Dict[str, Any]:
    root_version = _read_json(ROOT_DIR / "CLIENT_CODE_VERSION.json")
    static_version = _read_json(ROOT_DIR / "static" / "client_version.json")
    routes = {getattr(route, "path", "") for route in getattr(request.app, "routes", [])}
    missing = [path for path in EXPECTED_ROUTES if path not in routes]
    return {
        "ok": not missing,
        "client_version": _iso(root_version.get("version")),
        "client_build": root_version.get("build"),
        "applied_at": _iso(root_version.get("applied_at") or static_version.get("applied_at")),
        "bundle_sha256": _iso(root_version.get("bundle_sha256")),
        "static_version": _iso(static_version.get("version")),
        "static_build": static_version.get("build"),
        "backend_started_at": datetime.fromtimestamp(_BACKEND_STARTED_AT, tz=timezone.utc).isoformat(),
        "backend_uptime_seconds": int(max(0, time.time() - _BACKEND_STARTED_AT)),
        "python": sys.version.split()[0],
        "pid": os.getpid(),
        "root_dir": str(ROOT_DIR),
        "registered_routes": len(routes),
        "expected_routes_missing": missing,
    }


@router.get("/api/version", summary="前后端版本与关键接口自检")
def get_version(request: Request) -> Dict[str, Any]:
    return version_payload(request)
