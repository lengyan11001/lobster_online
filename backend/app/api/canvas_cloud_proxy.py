"""灵感画布（canvas_web）：本机 → 服务器 /canvas-api 转发。

画布页面由本机后端托管（/static/canvas-web/），页面里的接口根地址是 /canvas-api，
所以模型 / 任务 / 上传请求都先到本机；本机用**当前云端登录态**
（openclaw/.channel_fallback.json，取法与 services/cloud_session_renew 一致）转发到
服务器 /canvas-api/...，服务器只做 apiz key 中转与算力计费 —— 本机不接触 apiz key，
浏览器更不接触。

- 转发统一带 Authorization: Bearer <cloud token> 与 X-Installation-Id；
- 上游返回什么就原样返回什么（状态码 / 内容类型一致），画布自己判断错误；
- 本机没有登录态时直接 401，不去打服务器（省一次无谓请求）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional, Tuple

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..core.config import settings
from ..services.cloud_session_renew import decode_jwt_payload, read_cloud_token, read_installation_id
from ..services.oem_brand_context import with_oem_brand_header

logger = logging.getLogger(__name__)
router = APIRouter()

_DEFAULT_TIMEOUT = 120.0
_UPLOAD_TIMEOUT = 600.0
_UPLOAD_HINTS = ("upload",)


def auth_context() -> Tuple[str, str]:
    """当前云端登录态：(jwt, installation_id)，与 h5_chat_channel 同一套取法。"""
    token = read_cloud_token()
    installation_id = read_installation_id()
    if token and not installation_id:
        sub = str(decode_jwt_payload(token).get("sub") or "").strip()
        installation_id = f"canvas-local-{sub}" if sub else "canvas-local"
    return token, installation_id


def cloud_base() -> str:
    base = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="未配置服务器地址（AUTH_SERVER_BASE），无法转发画布请求")
    return base


_SESSION_PATHS = {
    "api/user_info",
    "api/get_user_money",
    "api/check_admin_permission",
}

_PROFILE_TTL_SECONDS = 60.0
_profile_cache: Dict[str, Any] = {"at": 0.0, "data": {}}


_FALLBACK_IMAGE_MODEL = "openai/gpt-image-2"
_FALLBACK_VIDEO_MODEL = "minimax/h3"
_VIDEO_HINTS = ("video", "sora", "kling", "seedance", "minimax", "h3", "veo", "runway", "luma")


def _looks_video_model(model: str, params) -> bool:
    m = str(model or "").lower()
    if any(k in m for k in _VIDEO_HINTS):
        return True
    if isinstance(params, dict):
        if params.get("duration") or params.get("video_url") or params.get("first_frame"):
            return True
        content = params.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") in ("video_url", "reference_video"):
                    return True
    return False


_IMAGE_FALLBACK = "openai/gpt-image-2"
_VIDEO_FALLBACK = "minimax/h3"
_IMG_HINTS = ("jimeng", "seedream", "dreamina", "gpt-image", "nano-banana", "flux", "sdxl", "ideogram", "kolors", "wanx")
_VID_HINTS = ("seedance", "sora", "kling", "veo", "runway", "luma", "minimax", "hailuo", "h3", "pika", "video")


def _fallback_model_name(value: str) -> str:
    v = str(value or "").strip()
    low = v.lower()
    if not low or "/" not in low:
        return v
    if any(k in low for k in _VID_HINTS):
        return _VIDEO_FALLBACK
    if any(k in low for k in _IMG_HINTS):
        return _IMAGE_FALLBACK
    return v


def _rewrite_model_ids(obj, depth: int = 0):
    """把画布快照里的旧模型 id 换成我们现在支持的（图片→gpt-image-2，视频→minimax/h3）。"""
    if depth > 12:
        return obj
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, str) and k.lower() in ("model", "model_id", "modelid", "app_name", "appname", "engine", "modelname", "model_name"):
                out[k] = _fallback_model_name(v)
            else:
                out[k] = _rewrite_model_ids(v, depth + 1)
        return out
    if isinstance(obj, list):
        return [_rewrite_model_ids(v, depth + 1) for v in obj]
    return obj


async def cloud_profile(force: bool = False) -> Dict[str, Any]:
    """当前登录态的用户资料（认证中心 /auth/me，带 60s 缓存）。

    拿不到就不阻塞画布：退回 JWT 里的 claims（id/邮箱等），credits 留空。
    """
    token, installation_id = auth_context()
    if not token:
        return {}
    claims = decode_jwt_payload(token)
    profile: Dict[str, Any] = {
        "id": claims.get("sub"),
        "email": claims.get("email") or "",
        "phone": claims.get("phone") or claims.get("mobile") or "",
        "name": claims.get("name") or claims.get("nickname") or "",
    }
    now = time.monotonic()
    if not force and _profile_cache["data"] and (now - float(_profile_cache["at"])) < _PROFILE_TTL_SECONDS:
        return dict(_profile_cache["data"])
    base = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    if base:
        headers = with_oem_brand_header({"Authorization": f"Bearer {token}", "Accept": "application/json"})
        if installation_id:
            headers["X-Installation-Id"] = installation_id
        try:
            async with httpx.AsyncClient(timeout=8.0, trust_env=False, follow_redirects=True) as client:
                resp = await client.get(f"{base}/auth/me", headers=headers)
            if resp.status_code < 400:
                data = resp.json()
                if isinstance(data, dict):
                    for key in ("id", "name", "nickname", "phone", "email", "credits", "balance", "points_balance"):
                        value = data.get(key)
                        if value not in (None, ""):
                            profile[key] = value
                    if "credits" not in profile:
                        for key in ("points_balance", "balance"):
                            if data.get(key) not in (None, ""):
                                profile["credits"] = data.get(key)
                                break
        except Exception as exc:  # noqa: BLE001 认证中心不可达时不阻塞画布
            logger.info("[canvas] 取用户资料失败，用登录态兜底: %s", exc)
    if not profile.get("name"):
        profile["name"] = "用户 %s" % (profile.get("id") or "")
    _profile_cache.update({"at": now, "data": dict(profile)})
    return profile


def canvas_session_payload(profile: Dict[str, Any]) -> Dict[str, Any]:
    """画布页面要的会话：token 是本机占位值（真 token 由本机代理注入，不下发浏览器）。"""
    credits = profile.get("credits")
    return {
        "token": "lobster-canvas",
        "id": profile.get("id"),
        "name": profile.get("name") or "",
        "phone": profile.get("phone") or "",
        "email": profile.get("email") or "",
        "points_balance": credits,
        "credits": credits,
    }


def _session_answer(normalized: str, profile: Dict[str, Any]) -> Optional[Response]:
    """画布问「我是谁 / 我的余额 / 我是不是管理员」：本机直接回答，不打上游。"""
    session = canvas_session_payload(profile)
    if normalized == "api/user_info":
        return JSONResponse({"code": 200, **session})
    if normalized == "api/get_user_money":
        credits = profile.get("credits")
        return JSONResponse({"code": 200, "points_balance": credits, "money": credits, "data": {"points_balance": credits}})
    if normalized == "api/check_admin_permission":
        return JSONResponse({"code": 200, "data": {"is_admin": False}})
    return None


@router.api_route(
    "/canvas-api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def canvas_cloud_proxy(path: str, request: Request) -> Response:
    normalized = str(path or "").strip().lstrip("/")
    if not normalized:
        raise HTTPException(status_code=404, detail="缺少接口路径")

    token, installation_id = auth_context()
    if not token:
        # 偶发读到空（续签中/文件短暂不可读）时，画布那边收到 401 会清会话并跳自己的登录页。
        # 这里再读一次，仍没有才 401，避免一次抖动把用户踢回登录。
        await asyncio.sleep(0.15)
        token, installation_id = auth_context()
    if not token:
        raise HTTPException(status_code=401, detail="画布需要登录：本机还没有云端登录态，请先在客户端登录")

    # 会话类接口本机回答：画布按这套判断「已登录」，不该再去问上游（上游只认服务器 key）
    if normalized in _SESSION_PATHS:
        return _session_answer(normalized, await cloud_profile())

    headers: Dict[str, str] = with_oem_brand_header(
        {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    )
    if installation_id:
        headers["X-Installation-Id"] = installation_id
    for name in ("content-type", "accept", "accept-language"):
        value = request.headers.get(name)
        if value:
            headers[name] = value

    body = await request.body()
    # 画布前端把 token 放在 JSON body 里（且是占位值 "lobster-canvas"），
    # 只替换 Authorization 头不够：上游按 body.token 鉴权会直接 401（用户看到「登录已过期」+扫码弹窗）。
    # 这里把 body.token 换成真 token；其它字段原样透传。
    if body and b'"token"' in body:
        try:
            import json as _json
            _data = _json.loads(body.decode("utf-8"))
            if isinstance(_data, dict) and "token" in _data:
                _data["token"] = token
                body = _json.dumps(_data, ensure_ascii=False).encode("utf-8")
                headers["content-type"] = "application/json"
                logger.info("[canvas] 注入 body.token -> %s %s", request.method, normalized)
        except Exception:
            logger.debug("[canvas] body.token 注入跳过 %s", normalized, exc_info=True)

    # 画布编辑器里的「生成」还用旧版 fal 接口（服务器已不认它的 token），
    # 统一翻译成我们服务器的 v3 接口：模型=app_name、参数=input_params。
    # 这样画布所有生成都走我们服务器，不再直连任何上游。
    if normalized in ("api/fal/tasks/create", "api/fal/tasks/info"):
        try:
            import json as _json2
            _raw = _json2.loads(body.decode("utf-8")) if body else {}
        except Exception:
            _raw = {}
        if isinstance(_raw, dict):
            if normalized == "api/fal/tasks/create":
                _payload = {
                    "model": str(_raw.get("app_name") or _raw.get("model") or "").strip(),
                    "params": _raw.get("input_params") if isinstance(_raw.get("input_params"), dict) else {},
                }
                _target = "api/v3/tasks/create"
            else:
                _tid = str(_raw.get("task_id") or _raw.get("taskId") or _raw.get("id") or "").strip()
                _payload = {"task_id": _tid, "taskId": _tid}
                _target = "api/v3/tasks/query"
            body = _json2.dumps(_payload, ensure_ascii=False).encode("utf-8")
            headers["content-type"] = "application/json"
            logger.info("[canvas] 旧 fal 接口转 v3：%s -> %s model=%s", normalized, _target, _payload.get("model", ""))
            normalized = _target

    timeout = _UPLOAD_TIMEOUT if any(hint in normalized.lower() for hint in _UPLOAD_HINTS) else _DEFAULT_TIMEOUT
    url = f"{cloud_base()}/canvas-api/{normalized}"
    # 2026-10-05：画布轮询断一次就被前端判失败（用户看到「暂时无法读取任务状态」），
    # 这里对「网络错误 / 上游 5xx」做两次静默重试（只重试幂等的读接口 + 查询类），
    # 生成类 POST 不重试（避免重复下单）。
    retryable = (request.method.upper() in {"GET", "HEAD"}
                 or "tasks/query" in normalized or "tasks/info" in normalized)
    attempts = 3 if retryable else 1
    last_error: Optional[Exception] = None
    upstream = None
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
                upstream = await client.request(
                    request.method,
                    url,
                    content=body or None,
                    headers=headers,
                    params=dict(request.query_params),
                )
        except httpx.TimeoutException as exc:
            last_error = exc
        except httpx.TransportError as exc:
            last_error = exc
        else:
            if upstream.status_code < 500 or attempt == attempts - 1:
                break
            last_error = None
            logger.warning("[canvas] %s %s 上游 %s，重试 %s/%s",
                           request.method, normalized, upstream.status_code, attempt + 2, attempts)
        if attempt < attempts - 1:
            await asyncio.sleep(0.6 * (attempt + 1))
    if upstream is None:
        if isinstance(last_error, httpx.TimeoutException):
            raise HTTPException(status_code=504, detail=f"服务器画布接口超时：{last_error}") from last_error
        raise HTTPException(status_code=502, detail=f"连不上服务器画布接口：{last_error}") from last_error

    # 上游 401（token 过期）会让画布清会话并弹自己的微信扫码登录页。
    # 这里静默续签一次再重发，用户无感；仍失败才把 401 透给画布。
    if upstream.status_code == 401:
        try:
            from ..services.cloud_session_renew import renew_cloud_token

            renewed = await renew_cloud_token(force=True)
            if renewed.get("refreshed") or renewed.get("ok"):
                token, installation_id = auth_context()
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                    if installation_id:
                        headers["X-Installation-Id"] = installation_id
                    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
                        upstream = await client.request(
                            request.method,
                            url,
                            content=body or None,
                            headers=headers,
                            params=dict(request.query_params),
                        )
                    logger.info("[canvas] %s %s 续签后重试 -> %s", request.method, normalized, upstream.status_code)
        except Exception:
            logger.warning("[canvas] 401 续签重试失败", exc_info=True)

    # 画布里还有不少旧模型（即梦/Seedream 等），服务器没配对应上游账号会 400「不支持的模型」。
    # 按用户规则兜底：图片 → gpt-image-2，视频 → minimax/h3（只替换模型名，参数原样）。
    if upstream.status_code == 400 and normalized == "api/v3/tasks/create":
        try:
            import json as _json5
            _payload = _json5.loads(body.decode("utf-8")) if body else {}
        except Exception:
            _payload = {}
        if isinstance(_payload, dict):
            _text = (upstream.content or b"").decode("utf-8", "ignore")
            if "不支持的模型" in _text:
                _model = str(_payload.get("model") or "")
                _params = _payload.get("params") if isinstance(_payload.get("params"), dict) else {}
                _fallback = _FALLBACK_VIDEO_MODEL if _looks_video_model(_model, _params) else _FALLBACK_IMAGE_MODEL
                if _fallback and _fallback != _model:
                    _payload["model"] = _fallback
                    _retry_body = _json5.dumps(_payload, ensure_ascii=False).encode("utf-8")
                    logger.warning("[canvas] 模型不受支持，兜底替换：%s -> %s", _model, _fallback)
                    try:
                        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
                            upstream = await client.request(
                                request.method, url, content=_retry_body, headers=headers,
                                params=dict(request.query_params),
                            )
                    except Exception:
                        logger.warning("[canvas] 兜底重试失败", exc_info=True)

    if upstream.status_code == 404 and normalized == "api/v3/tasks/query":
        try:
            import json as _json7
            _q = _json7.loads(body.decode("utf-8")) if body else {}
            _tid2 = str(_q.get("task_id") or _q.get("taskId") or "").strip()
            if _tid2:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
                    _up2 = await client.post(
                        f"{cloud_base()}/canvas-api/api/v2/tasks/info",
                        json={"task_id": _tid2, "taskId": _tid2},
                        headers=headers,
                    )
                if _up2.status_code == 200:
                    logger.info("[canvas] v3 查不到，回落 v2 命中：%s", _tid2)
                    upstream = _up2
        except Exception:
            logger.debug("[canvas] v2 回落失败", exc_info=True)

    logger.info("[canvas] %s %s -> %s", request.method, normalized, upstream.status_code)
    media_type = upstream.headers.get("content-type") or "application/json"
    content = upstream.content

    # 灵感模板里的旧模型（即梦4.0 / Seedream / Sora…）在快照里写死，复制后必然不可用。
    # 载入/克隆/详情返回时统一替换成我们现在支持的模型，用户复制即用。
    if upstream.status_code == 200 and "json" in media_type and any(
        key in normalized for key in ("canvas/load", "/clone", "projects/public", "projects/my")
    ):
        try:
            import json as _json6
            data = _json6.loads(content.decode("utf-8"))
            fixed = _rewrite_model_ids(data)
            if fixed != data:
                content = _json6.dumps(fixed, ensure_ascii=False).encode("utf-8")
                logger.info("[canvas] 模板模型已替换为当前支持：%s", normalized)
        except Exception:
            logger.debug("[canvas] 模板模型替换跳过 %s", normalized, exc_info=True)

    return Response(content=content, status_code=upstream.status_code, media_type=media_type)
