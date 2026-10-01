"""灵感画布（canvas_web）独立 origin 的 ASGI 应用。

背景：画布是第三方打包的 hash 路由 SPA（webpack publicPath=/static/canvas-web/）。
挂在客户端主站子路径 http://127.0.0.1:8000/static/canvas-web/ 下时，画布内部的
pushState('/#/canvas-editor/<uuid>') 会被解析成主站根地址（'/#/...' -> 8000/#/...），
把 iframe 顶成客户端整页（未登录态 = 登录页 + 套娃），复制/打开作品永远回不到画布。

把画布搬到独立 origin（127.0.0.1:8003）挂在自身根下后，'/#/...' 始终留在画布这边，
从根上消除 hash 路由 base 冲突，也顺带隔离 WebView 缓存命名空间（改了立刻生效）。

本模块只做「静态托管 + 复用本机既有画布路由」，不新增、不改动任何接口：
  /canvas-api/{path}          -> canvas_cloud_proxy（注入本机登录态，转发云端画布接口）
  /api/canvas-local/*         -> canvas_local（本机会话注入 / ffmpeg / 产物读取）
  /static/canvas-web/{path}   -> webpack publicPath，资源按原样提供
  /{path}                     -> 画布页面（html=True；'/' 与 '/index.html' 永远不缓存）
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.canvas_cloud_proxy import router as canvas_cloud_proxy_router
from backend.app.api.canvas_local import router as canvas_local_router

logger = logging.getLogger(__name__)

CANVAS_WEB_DIR = Path(__file__).resolve().parents[1] / "static" / "canvas-web"
CANVAS_BUILD = "20261001-canvas-origin"
CLIENT_ROOT = str(Path(__file__).resolve().parents[1])
_NOSTORE = {"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache"}

app = FastAPI(
    title="Lobster Canvas",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# 主站（http://127.0.0.1:8000）嵌入画布前要探测 /healthz；只放行本机来源。
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(canvas_local_router, prefix="")
app.include_router(canvas_cloud_proxy_router, prefix="")


@app.get("/healthz", include_in_schema=False)
def canvas_healthz() -> JSONResponse:
    """画布服务就绪探针。

    刻意不叫 /api/health：主站启动时会用 /api/health 判定「是不是本机另一个后端实例」
    并清理，这里不能让自己被误当成重复后端。
    """
    return JSONResponse({"ok": True, "service": "lobster-canvas", "build": CANVAS_BUILD, "client_root": CLIENT_ROOT})


def _canvas_index() -> FileResponse:
    return FileResponse(
        str(CANVAS_WEB_DIR / "index.html"),
        media_type="text/html",
        headers=dict(_NOSTORE),
    )


@app.get("/", include_in_schema=False)
def canvas_root() -> FileResponse:
    return _canvas_index()


@app.get("/index.html", include_in_schema=False)
def canvas_index() -> FileResponse:
    return _canvas_index()


if CANVAS_WEB_DIR.is_dir():
    # webpack 里写死的 publicPath，画布按这个绝对路径取 js/css/字体
    app.mount("/static/canvas-web", StaticFiles(directory=str(CANVAS_WEB_DIR)), name="canvas-web-assets")
    # 独立 origin 的根：画布页面本体（html=True -> / 返回 index.html）
    app.mount("/", StaticFiles(directory=str(CANVAS_WEB_DIR), html=True), name="canvas-web-root")
else:  # pragma: no cover - 只在安装不完整时出现
    logger.warning("[canvas] 画布静态目录不存在：%s", CANVAS_WEB_DIR)
