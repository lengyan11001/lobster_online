"""MCP Server entry: run HTTP server on configured port.
全日志：默认 LOG_LEVEL=debug；.env 或环境变量 LOG_LEVEL=info 可仅打关键信息。"""
import logging
import os
import sys
from pathlib import Path

# 须先于 http_server 导入：与 backend/run.py 子进程注入一致，单独 run_mcp.bat 启动时也能读到 LOBSTER_EDITION / AUTH_SERVER_BASE
_root = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv

    load_dotenv(_root / ".env")
except Exception:
    pass

import uvicorn
from . import http_server

if __name__ == "__main__":
    port = 8001
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            try:
                port = int(sys.argv[i + 1])
            except ValueError:
                pass
            break
    log_level_name = os.environ.get("LOG_LEVEL", "debug").strip().lower()
    log_level = getattr(logging, log_level_name.upper(), logging.DEBUG)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("mcp")
    _ed = (os.environ.get("LOBSTER_EDITION") or "").strip()
    _asb = (os.environ.get("AUTH_SERVER_BASE") or "").strip()
    _sutui_upstream = (
        f"{_asb.rstrip('/')}/mcp-gateway" if _ed.lower() == "online" and _asb else "(非 online 或未配 AUTH_SERVER_BASE，见 upstream_urls / CAPABILITY_SUTUI_MCP_URL)"
    )
    logger.info(
        "[启动] MCP 服务监听 127.0.0.1:%s LOG_LEVEL=%s LOBSTER_EDITION=%s AUTH_SERVER_BASE=%s → 在线版速推上游=%s",
        port,
        log_level_name,
        _ed or "(未设置)",
        _asb or "(未设置)",
        _sutui_upstream,
    )
    uvicorn.run(
        http_server.app,
        host="127.0.0.1",
        port=port,
        log_level=log_level_name,
    )
