"""灵感画布独立 origin 服务（127.0.0.1:8003）的启动器。

随主服务（backend/run.py）一起拉起，和主服务在同一个进程里：
登录态、运行时目录、日志、看门狗、退出清理全都复用，不会留下孤儿进程占端口。

端口可用 LOBSTER_CANVAS_PORT 覆盖；画布服务起不来不影响主站启动，
前端 static/views/canvas-studio.html 会自动回退到主站内嵌入口。
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

CANVAS_HOST = "127.0.0.1"
DEFAULT_CANVAS_PORT = 8003
_CLIENT_ROOT = str(Path(__file__).resolve().parents[1])


def canvas_port() -> int:
    raw = (os.environ.get("LOBSTER_CANVAS_PORT") or "").strip()
    try:
        port = int(raw) if raw else DEFAULT_CANVAS_PORT
    except ValueError:
        port = DEFAULT_CANVAS_PORT
    return port if 1024 <= port <= 65535 else DEFAULT_CANVAS_PORT


def _port_open(port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((CANVAS_HOST, port), timeout=timeout):
            return True
    except OSError:
        return False


def _health_payload(port: int, timeout: float = 1.5):
    try:
        with urllib.request.urlopen("http://%s:%s/healthz" % (CANVAS_HOST, port), timeout=timeout) as resp:
            if not (200 <= int(resp.status) < 300):
                return None
            raw = resp.read(512)
            if b"lobster-canvas" not in raw:
                return None
            return json.loads(raw.decode("utf-8", "replace") or "{}")
    except Exception:
        return None


def _health_ok(port: int, timeout: float = 1.5) -> bool:
    """已在跑的画布服务只有「同客户端目录」才复用；别的客户端实例占用时不动它。"""
    payload = _health_payload(port, timeout)
    if not payload:
        return False
    root = str(payload.get("client_root") or "")
    if root and os.path.normcase(os.path.abspath(root)) != os.path.normcase(_CLIENT_ROOT):
        return False
    return True


def _wait_ready(port: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _health_ok(port):
            return True
        time.sleep(0.4)
    return False


def start_canvas_server() -> bool:
    """拉起画布服务；已经在跑就直接复用（热重启不会重复占端口）。"""
    port = canvas_port()
    if _health_ok(port):
        logger.info("[启动] 灵感画布已在端口 %s 监听，跳过自启", port)
        return True
    if _port_open(port):
        logger.warning("[启动] 端口 %s 被其他进程占用，灵感画布未启动（页面会回退到主站内嵌入口）", port)
        return False

    def _serve() -> None:
        try:
            import uvicorn

            from backend.canvas_app import app as canvas_app

            logger.info("[启动] 灵感画布服务启动 host=%s port=%s", CANVAS_HOST, port)
            uvicorn.run(
                canvas_app,
                host=CANVAS_HOST,
                port=port,
                log_level="warning",
                loop="asyncio",
                http="h11",
                ws="none",
            )
        except Exception:
            logger.exception("[启动] 灵感画布服务异常退出")

    threading.Thread(target=_serve, name="lobster-canvas-server", daemon=True).start()
    ok = _wait_ready(port, 20.0)
    logger.info("[启动] 灵感画布就绪=%s port=%s", ok, port)
    return ok
