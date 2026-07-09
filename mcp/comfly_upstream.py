"""Comfly 中转平台上游调用模块。

与速推(xSkill)并行的生成能力上游。当模型在 comfly_pricing.json 中配置了定价时，
invoke_capability 将路由到 Comfly 而非速推。
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import httpx

from collections import OrderedDict

logger = logging.getLogger("lobster.mcp.comfly")

_PRICING_PATH = Path(__file__).resolve().parent.parent / "comfly_pricing.json"
_pricing_cache: Optional[Dict[str, Any]] = None
_pricing_mtime: float = 0

_MAX_COMFLY_TASK_TRACK = 5000
_comfly_task_ids: "OrderedDict[str, Tuple[str, str]]" = OrderedDict()
_COMFLY_UPLOAD_ID_KEYS = (
    "file_id",
    "fileId",
    "asset_id",
    "assetId",
    "material_id",
    "materialId",
    "media_id",
    "mediaId",
    "id",
)
_COMFLY_UPLOAD_URL_KEYS = ("url", "image_url", "imageUrl", "file_url", "fileUrl", "download_url", "downloadUrl")
_COMFLY_VIDEO_TASK_ID_KEYS = (
    "task_id",
    "taskId",
    "taskID",
    "id",
    "job_id",
    "jobId",
    "request_id",
    "requestId",
    "generation_id",
    "generationId",
    "run_id",
    "runId",
)


def _coerce_grok_video_resolution(raw: Any) -> str:
    s = str(raw or "").strip().lower().replace(" ", "")
    if "480" in s:
        return "480p"
    return "720p"


_COMFLY_VIDEO_URL_KEYS = (
    "video_url",
    "videoUrl",
    "mp4url",
    "mp4Url",
    "url",
    "output",
    "output_url",
    "outputUrl",
    "download_url",
    "downloadUrl",
)
_COMFLY_RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
_COMFLY_HTTP_RETRY_ATTEMPTS = 3
_COMFLY_HTTP_RETRY_DELAY_SECONDS = 3.0


class _ComflyRetryableError(RuntimeError):
    pass


def register_comfly_task(task_id: str, token_group: str = "", api_format: str = "") -> None:
    """记录由 Comfly 创建的 task_id 及其 token_group 和 api_format，用于 task.get_result 路由。"""
    tid = (task_id or "").strip()
    if not tid:
        return
    _comfly_task_ids[tid] = (token_group or "", api_format or "")
    while len(_comfly_task_ids) > _MAX_COMFLY_TASK_TRACK:
        _comfly_task_ids.popitem(last=False)


def is_comfly_task(task_id: str) -> bool:
    """判断 task_id 是否属于 Comfly。"""
    return (task_id or "").strip() in _comfly_task_ids


def get_comfly_task_token_group(task_id: str) -> str:
    """获取 Comfly task 对应的 token_group。"""
    entry = _comfly_task_ids.get((task_id or "").strip())
    if entry is None:
        return ""
    return entry[0] if isinstance(entry, tuple) else entry


def get_comfly_task_api_format(task_id: str) -> str:
    """获取 Comfly task 对应的 api_format。"""
    entry = _comfly_task_ids.get((task_id or "").strip())
    if entry is None:
        return ""
    return entry[1] if isinstance(entry, tuple) else ""


def _load_pricing() -> Dict[str, Any]:
    """热加载 comfly_pricing.json（文件修改后自动刷新）。"""
    global _pricing_cache, _pricing_mtime
    try:
        mt = _PRICING_PATH.stat().st_mtime
    except FileNotFoundError:
        _pricing_cache = {"models": {}}
        return _pricing_cache
    if _pricing_cache is not None and mt == _pricing_mtime:
        return _pricing_cache
    try:
        raw = _PRICING_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        _pricing_cache = data if isinstance(data, dict) else {"models": {}}
        _pricing_mtime = mt
    except Exception as e:
        logger.warning("[Comfly] 加载 comfly_pricing.json 失败: %s", e)
        if _pricing_cache is None:
            _pricing_cache = {"models": {}}
    return _pricing_cache


def get_comfly_config(token_group: str = "") -> Tuple[str, str]:
    """返回 (base_url, api_key)。

    token_group 对应环境变量 COMFLY_API_BASE_<GROUP> / COMFLY_API_KEY_<GROUP>（大写），
    未设置时回退到默认 COMFLY_API_BASE / COMFLY_API_KEY。
    base_url 会去除尾部 /v1 以避免与端点路径拼接时出现 /v1/v1 重复。
    """
    base = (os.environ.get("COMFLY_API_BASE") or "").strip().rstrip("/")
    key = ""
    if token_group:
        group = token_group.upper()
        base_env_name = f"COMFLY_API_BASE_{group}"
        key_env_name = f"COMFLY_API_KEY_{group}"
        grouped_base = (os.environ.get(base_env_name) or "").strip().rstrip("/")
        if not grouped_base and group == "YUNWU":
            grouped_base = (os.environ.get("YUNWU_API_BASE") or "").strip().rstrip("/")
        if not grouped_base and group in {"OPENMIND", "OPENMINDAPI"}:
            grouped_base = (os.environ.get("OPENMIND_API_BASE") or "").strip().rstrip("/")
        if grouped_base:
            base = grouped_base
            logger.debug("[Comfly] 使用 token_group=%s base_env=%s", token_group, base_env_name)
        key = (os.environ.get(key_env_name) or "").strip()
        if not key and group == "YUNWU":
            key = (os.environ.get("YUNWU_API_KEY") or "").strip()
        if not key and group in {"OPENMIND", "OPENMINDAPI"}:
            key = (os.environ.get("OPENMIND_API_KEY") or "").strip()
        if key:
            logger.debug("[Comfly] 使用 token_group=%s key_env=%s", token_group, key_env_name)
    if not key:
        key = (os.environ.get("COMFLY_API_KEY") or "").strip()
    if base.endswith("/v1"):
        base = base[:-3]
    return base, key


def _get_model_token_group(model_id: str) -> str:
    """从 comfly_pricing.json 中查找模型的 token_group。"""
    entry = lookup_comfly_model(model_id)
    if entry and isinstance(entry, dict):
        return (entry.get("token_group") or "").strip()
    return ""


def is_comfly_configured() -> bool:
    base, key = get_comfly_config()
    return bool(base and key)


def _comfly_retry_attempts() -> int:
    return _COMFLY_HTTP_RETRY_ATTEMPTS


def _comfly_retry_delay_seconds() -> float:
    return _COMFLY_HTTP_RETRY_DELAY_SECONDS


def _is_retryable_comfly_exception(exc: Exception) -> bool:
    return isinstance(exc, (_ComflyRetryableError, httpx.TimeoutException, httpx.TransportError))


async def _request_comfly_json(
    client: httpx.AsyncClient,
    action: str,
    method: str,
    url: str,
    **kwargs: Any,
) -> Tuple[int, Dict[str, Any], int]:
    attempts = _comfly_retry_attempts()
    delay = _comfly_retry_delay_seconds()
    last: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            response = await client.request(method, url, **kwargs)
            retryable_status = response.status_code in _COMFLY_RETRYABLE_STATUS_CODES
            try:
                payload = response.json() if response.content else {}
            except Exception as exc:
                if response.status_code >= 400 and not retryable_status:
                    payload = {"raw_text": response.text}
                else:
                    raise _ComflyRetryableError(f"invalid JSON response: {exc}") from exc
            if not response.content and (response.status_code < 400 or retryable_status):
                raise _ComflyRetryableError("empty response")
            if retryable_status:
                raise _ComflyRetryableError(f"HTTP {response.status_code}: {str(payload)[:500]}")
            if not isinstance(payload, dict):
                if response.status_code >= 400:
                    payload = {"raw": payload}
                else:
                    raise _ComflyRetryableError(f"non-object JSON response: {payload}")
            if attempt > 1:
                payload["_comfly_request_attempts"] = attempt
            return response.status_code, payload, attempt
        except Exception as exc:
            last = exc
            if attempt >= attempts or not _is_retryable_comfly_exception(exc):
                raise
            logger.warning(
                "[Comfly] request retry action=%s attempt=%s/%s error=%s",
                action,
                attempt,
                attempts,
                exc,
            )
            if delay > 0:
                await asyncio.sleep(delay * attempt)
    raise _ComflyRetryableError(f"{action} failed after {attempts} attempt(s): {last}")


def _pricing_entry_enabled(entry: Dict[str, Any]) -> bool:
    return entry.get("enabled") is not False and entry.get("disabled") is not True


def _as_media_ref(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("url", "image_url", "path", "file_path", "filePath", "local_path", *_COMFLY_UPLOAD_ID_KEYS):
            ref = value.get(key)
            if isinstance(ref, str) and ref.strip():
                return ref.strip()
    return ""


def _first_media_ref(*values: Any) -> str:
    for value in values:
        if isinstance(value, (list, tuple)):
            for item in value:
                ref = _as_media_ref(item)
                if ref:
                    return ref
            continue
        ref = _as_media_ref(value)
        if ref:
            return ref
    return ""


def _find_nested_string(value: Any, keys: Tuple[str, ...]) -> str:
    if isinstance(value, dict):
        for key in keys:
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
        for child_key in ("data", "file", "payload", "result", "task", "job", "generation", "video", "content", "output"):
            found = _find_nested_string(value.get(child_key), keys)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_nested_string(item, keys)
            if found:
                return found
    return ""


def _filename_from_media_ref(ref: str) -> str:
    raw = (ref or "").strip()
    if raw.startswith(("http://", "https://")):
        parsed = urlparse(raw)
        name = unquote(Path(parsed.path).name or "")
        return name or "image.bin"
    try:
        name = Path(raw).name
    except Exception:
        name = ""
    return name or "image.bin"


async def _upload_image_to_comfly_for_veo(
    client: httpx.AsyncClient,
    base: str,
    auth_headers: Dict[str, str],
    image_ref: str,
) -> str:
    ref = (image_ref or "").strip()
    if not ref:
        return ""

    content_type = ""
    filename = _filename_from_media_ref(ref)
    if ref.startswith(("http://", "https://")):
        download = await client.get(ref, timeout=120.0)
        download.raise_for_status()
        content = download.content
        content_type = (download.headers.get("content-type") or "").split(";")[0].strip()
    else:
        path = Path(ref)
        if not path.exists():
            # A non-path, non-URL value is assumed to already be a Comfly file/material id.
            return ref
        content = path.read_bytes()

    guessed_type = mimetypes.guess_type(filename)[0]
    media_type = content_type or guessed_type or "application/octet-stream"
    upload_url = f"{base}/v1/files"
    files = {"file": (filename, content, media_type)}
    status_code, payload, upload_attempts = await _request_comfly_json(
        client,
        "veo_file_upload",
        "POST",
        upload_url,
        files=files,
        headers=auth_headers,
        timeout=120.0,
    )
    if status_code >= 400:
        raise RuntimeError(f"Comfly file upload HTTP {status_code}: {str(payload)[:500]}")
    if not isinstance(payload, dict):
        raise RuntimeError(f"Comfly file upload returned invalid payload: {payload}")

    file_id = _find_nested_string(payload, _COMFLY_UPLOAD_ID_KEYS)
    file_url = _find_nested_string(payload, _COMFLY_UPLOAD_URL_KEYS)
    upload_ref = file_url or file_id
    if not upload_ref:
        raise RuntimeError(f"Comfly file upload returned no id/url: {payload}")
    logger.info(
        "[Comfly] Veo image uploaded for images[] source_type=%s has_file_url=%s has_file_id=%s upload_ref=%s attempts=%s",
        "url" if ref.startswith(("http://", "https://")) else "file",
        bool(file_url),
        bool(file_id),
        upload_ref,
        upload_attempts,
    )
    return upload_ref


def _coerce_sora2_size(payload: Dict[str, Any], model_id: str) -> str:
    allowed = {"1280x720", "720x1280"}
    if model_id == "sora-2-pro":
        allowed.update({"1792x1024", "1024x1792"})

    raw_size = str(payload.get("size") or payload.get("image_size") or "").strip().lower()
    raw_size = raw_size.replace("×", "x").replace("*", "x")
    if raw_size in allowed:
        return raw_size

    width = payload.get("width")
    height = payload.get("height")
    try:
        if width and height:
            wh = f"{int(width)}x{int(height)}"
            if wh in allowed:
                return wh
    except (TypeError, ValueError):
        pass

    aspect_ratio = str(payload.get("aspect_ratio") or payload.get("ratio") or "").strip().lower()
    resolution = str(payload.get("resolution") or payload.get("quality") or "").strip().lower()
    wants_1080p = "1080" in resolution or "1792" in raw_size or "1024" in raw_size
    if model_id == "sora-2-pro" and wants_1080p:
        return "1792x1024" if aspect_ratio in ("16:9", "landscape", "horizontal") else "1024x1792"
    if aspect_ratio in ("16:9", "landscape", "horizontal"):
        return "1280x720"
    return "720x1280"


def _coerce_sora2_seconds(duration: Any) -> str:
    try:
        raw = str(duration).strip().lower()
        if raw.endswith("s"):
            raw = raw[:-1]
        seconds = int(float(raw))
    except (TypeError, ValueError):
        seconds = 5
    if seconds < 4:
        seconds = 4
    return str(seconds)


def _build_sora2_multipart(
    payload: Dict[str, Any],
    *,
    model_id: str,
    prompt: str,
    duration: Any,
    first_image: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    data: Dict[str, Any] = {
        "model": model_id,
        "prompt": prompt,
        "size": _coerce_sora2_size(payload, model_id),
        "seconds": _coerce_sora2_seconds(duration),
        "watermark": "true" if payload.get("watermark") else "false",
    }
    files: Dict[str, Any] = {k: (None, str(v)) for k, v in data.items() if v is not None}

    if first_image:
        path = Path(first_image)
        if path.exists() and path.is_file():
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            files["input_reference"] = (path.name, path.read_bytes(), content_type)
        else:
            data["input_reference"] = first_image
            files["input_reference"] = (None, first_image)

    return data, files


_FORCE_SUTUI_MODEL_IDS = {
    "xai/grok-imagine-video/text-to-video",
    "xai/grok-imagine-video/image-to-video",
}


def _is_force_sutui_model_id(model_id: str) -> bool:
    return (model_id or "").strip().lower() in _FORCE_SUTUI_MODEL_IDS


def lookup_comfly_model(model_id: str) -> Optional[Dict[str, Any]]:
    """查找模型是否在 Comfly 定价表中。返回定价条目或 None。
    支持直接按 Comfly 模型名查找，也支持通过 sutui_equivalent 反查。
    当精确匹配失败时，尝试前缀匹配（如 fal-ai/veo3.1/xxx 匹配 fal-ai/veo3.1）。
    """
    if not model_id:
        return None
    if _is_force_sutui_model_id(model_id):
        return None
    pricing = _load_pricing()
    models = pricing.get("models") or {}
    entry = models.get(model_id)
    if entry and isinstance(entry, dict) and _pricing_entry_enabled(entry):
        return entry
    low = model_id.lower()
    for k, v in models.items():
        if isinstance(v, dict) and not _pricing_entry_enabled(v):
            continue
        if k.lower() == low:
            return v
    for _k, v in models.items():
        if not isinstance(v, dict):
            continue
        if not _pricing_entry_enabled(v):
            continue
        eq = v.get("sutui_equivalent")
        if isinstance(eq, list) and any(e.lower() == low for e in eq if isinstance(e, str)):
            return v
        if isinstance(eq, str) and eq.lower() == low:
            return v
    # Prefix fallback: fal-ai/veo3.1/lite → try fal-ai/veo3.1, then fal-ai
    parts = low.rsplit("/", 1)
    while len(parts) == 2 and parts[0]:
        prefix = parts[0]
        for k, v in models.items():
            if isinstance(v, dict) and not _pricing_entry_enabled(v):
                continue
            if k.lower() == prefix:
                return v
        for _k, v in models.items():
            if not isinstance(v, dict):
                continue
            if not _pricing_entry_enabled(v):
                continue
            eq = v.get("sutui_equivalent")
            _eqs = eq if isinstance(eq, list) else ([eq] if isinstance(eq, str) else [])
            if any(e.lower() == prefix for e in _eqs if isinstance(e, str)):
                return v
        parts = prefix.rsplit("/", 1)
    return None


_SUTUI_MODEL_ID_PREFIXES = (
    "fal-ai/",
    "st-ai/",
    "wan/",
    "xai/",
    "ark/",
    "openai/",
    "openrouter/",
    "sora2pub/",
)


def _is_sutui_model_id(model_id: str) -> bool:
    """速推真实模型 ID 不应被 Comfly 价格表的 sutui_equivalent 自动劫持。"""
    low = (model_id or "").strip().lower()
    return any(low.startswith(prefix) for prefix in _SUTUI_MODEL_ID_PREFIXES) or low.startswith("jimeng-")


def should_route_to_comfly(capability_id: str, model_id: str, *, sutui_price: Optional[float] = None) -> bool:
    """判断是否应将请求路由到 Comfly。

    当模型在 Comfly 定价表中且满足以下条件之一时返回 True：
    1. 速推无此模型（sutui_price_per_unit 未配置且 sutui_price 未传入）
    2. Comfly 采购价 <= 速推采购价（比价路由）
    """
    if capability_id not in ("image.generate", "video.generate"):
        return False
    if not is_comfly_configured():
        return False
    if _is_force_sutui_model_id(model_id):
        logger.info("[Comfly] 跳过路由：model=%s 固定走速推真实定价", model_id)
        return False
    if _is_sutui_model_id(model_id):
        logger.info("[Comfly] 跳过路由：model=%s 是速推模型 ID，保持走速推", model_id)
        return False
    entry = lookup_comfly_model(model_id)
    if not entry:
        return False
    comfly_price = entry.get("price_per_unit")
    if comfly_price is None:
        return True
    comfly_price = float(comfly_price)
    st_price = sutui_price if sutui_price is not None else entry.get("sutui_price_per_unit")
    if st_price is None:
        return True
    st_price = float(st_price)
    use_comfly = comfly_price <= st_price
    logger.info("[Comfly] 比价 model=%s comfly=%.1f sutui=%.1f → %s", model_id, comfly_price, st_price, "comfly" if use_comfly else "sutui")
    return use_comfly


def _user_price_multiplier() -> float:
    """用户实际消耗 = 采购价 × 倍率。优先取全局倍率，其次 Comfly 专用倍率，最后 JSON 配置。"""
    for env_name in ("USER_PRICE_MULTIPLIER", "COMFLY_USER_PRICE_MULTIPLIER"):
        env_val = os.environ.get(env_name, "").strip()
        if env_val:
            try:
                return float(env_val)
            except ValueError:
                pass
    pricing = _load_pricing()
    return float(pricing.get("user_price_multiplier_default", 3))


def estimate_comfly_credits(model_id: str, params: Dict[str, Any], *, for_user: bool = False) -> Optional[int]:
    """按 Comfly 定价表估算算力消耗。for_user=True 时返回用户价（采购价 × 倍率）。

    支持的 price_type：
    - per_call：base = price_per_unit
    - per_second：base = price_per_unit × duration
    - per_token：base = (input_tokens/1000 × input_price + output_tokens/1000 × output_price)
                 estimate 时若 params 未提供实际 token，则按 prompt_tokens=5000, completion_tokens=1000 粗估。
    """
    entry = lookup_comfly_model(model_id)
    if not entry:
        return None
    price_type = (entry.get("price_type") or "per_call").strip()
    multiplier = float(entry.get("user_price_multiplier", _user_price_multiplier())) if for_user else 1.0

    if price_type == "per_token":
        input_unit = entry.get("input_price_per_1k_tokens")
        output_unit = entry.get("output_price_per_1k_tokens")
        if input_unit is None and output_unit is None:
            return None
        input_unit = float(input_unit or 0)
        output_unit = float(output_unit or 0)
        # 实际计费传 usage 时优先用真实 token 数；估算时给典型值
        usage = params.get("usage") if isinstance(params.get("usage"), dict) else None
        if usage:
            in_tok = float(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            out_tok = float(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        else:
            in_tok = float(params.get("estimate_prompt_tokens") or 5000)
            out_tok = float(params.get("estimate_completion_tokens") or 1000)
        base = (in_tok / 1000.0) * input_unit + (out_tok / 1000.0) * output_unit
        # per_token 价格非常小，避免 round 后变成 0；至少计 1 积分（按 multiplier 后）
        return max(1, int(round(base * multiplier))) if base > 0 else 0

    unit_price = entry.get("price_per_unit")
    if unit_price is None:
        return None
    unit_price = float(unit_price)
    if price_type == "per_second":
        duration = params.get("duration") or params.get("seconds") or 5
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = 5
        base = unit_price * duration
    else:
        base = unit_price
    return int(round(base * multiplier))


def get_all_comfly_pricing() -> Dict[str, Any]:
    """返回完整定价表（供 API 端点暴露给 lobster_online）。"""
    return _load_pricing()


# ---------------------------------------------------------------------------
# Comfly API 调用
# ---------------------------------------------------------------------------

async def call_comfly_image_generate(
    model_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """调用 Comfly 图片生成 API (DALL-E 格式)。"""
    tg = _get_model_token_group(model_id)
    base, key = get_comfly_config(tg)
    entry = lookup_comfly_model(model_id) or {}
    comfly_model = entry.get("comfly_model") or model_id

    prompt = (payload.get("prompt") or "").strip()
    body: Dict[str, Any] = {
        "model": comfly_model,
        "prompt": prompt,
    }
    if payload.get("n") is not None:
        body["n"] = payload.get("n") or 1
    size = payload.get("image_size") or payload.get("size")
    if size:
        if str(comfly_model).strip().lower().startswith("nano-banana-2"):
            body["image_size"] = size
        else:
            body["size"] = size
    aspect_ratio = payload.get("aspect_ratio") or payload.get("ratio")
    if aspect_ratio:
        body["aspect_ratio"] = aspect_ratio
    response_format = payload.get("response_format")
    if response_format:
        body["response_format"] = response_format

    image_url = payload.get("image_url") or ""
    image_urls = payload.get("image_urls") or []
    if image_url:
        body["image"] = image_url
    elif image_urls and isinstance(image_urls, list) and image_urls:
        body["image"] = image_urls[0]

    api_format = (entry.get("api_format") or "dalle").strip()

    if api_format == "dalle":
        url = f"{base}/v1/images/generations"
        if body.get("image"):
            url = f"{base}/v1/images/edits"
    else:
        url = f"{base}/v1/images/generations"

    auth_headers = {
        "Authorization": f"Bearer {key}",
    }
    json_headers = {
        **auth_headers,
        "Content-Type": "application/json",
    }

    logger.info("[Comfly] 图片生成请求 model=%s url=%s", comfly_model, url)
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            status_code, resp, attempts = await _request_comfly_json(
                client,
                "image_generate",
                "POST",
                url,
                json=body,
                headers=json_headers,
            )
        if status_code >= 400:
            err = resp.get("error", {})
            msg = err.get("message", str(resp)) if isinstance(err, dict) else str(err)
            return {"error": {"message": f"Comfly 返回 HTTP {status_code}: {msg}"}}
        logger.info("[Comfly] 图片生成响应 HTTP=%s attempts=%s", status_code, attempts)
        return resp
    except Exception as e:
        logger.exception("[Comfly] 图片生成请求异常")
        return {"error": {"message": f"Comfly 请求失败: {e}"}}


async def call_comfly_video_generate(
    model_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """调用 Comfly 视频生成 API (统一格式)。"""
    tg = _get_model_token_group(model_id)
    base, key = get_comfly_config(tg)
    entry = lookup_comfly_model(model_id) or {}
    comfly_model = entry.get("comfly_model") or model_id
    api_format = (entry.get("api_format") or "unified_video").strip()

    prompt = (payload.get("prompt") or "").strip()
    _raw_dur = payload.get("duration") or payload.get("seconds") or 5
    try:
        if isinstance(_raw_dur, str) and _raw_dur.strip().lower().endswith("s"):
            duration = int(_raw_dur.strip().lower().rstrip("s"))
        else:
            duration = int(_raw_dur)
    except (ValueError, TypeError):
        duration = 5

    auth_headers = {
        "Authorization": f"Bearer {key}",
    }
    json_headers = {
        **auth_headers,
        "Content-Type": "application/json",
    }

    image_url = payload.get("image_url") or ""
    file_paths = payload.get("filePaths") or []
    media_files = payload.get("media_files") or []
    images = payload.get("images") or []
    image_files = payload.get("image_files") or []
    first_image = _first_media_ref(image_url, file_paths, media_files, images, image_files)
    request_mode = "json"
    multipart_files: Optional[Dict[str, Any]] = None

    if api_format == "veo":
        url = f"{base}/v2/videos/generations"
        body: Dict[str, Any] = {
            "model": comfly_model,
            "prompt": prompt,
            "enhance_prompt": True,
        }
        aspect_ratio = payload.get("aspect_ratio") or "16:9"
        body["aspect_ratio"] = aspect_ratio
    elif api_format == "grok":
        url = f"{base}/v2/videos/generations"
        grok_duration = 10 if duration == 10 else 6
        body = {
            "model": comfly_model,
            "prompt": prompt,
            "ratio": payload.get("ratio") or payload.get("aspect_ratio") or "9:16",
            "resolution": _coerce_grok_video_resolution(payload.get("resolution")),
            "duration": grok_duration,
        }
        if first_image:
            body["images"] = [first_image]
    elif api_format == "unified_video":
        if first_image:
            url = f"{base}/task/submit/i2v"
            body = {
                "model": comfly_model,
                "prompt": prompt,
                "image_url": first_image,
            }
        else:
            url = f"{base}/task/submit/t2v"
            body = {
                "model": comfly_model,
                "prompt": prompt,
        }
        if duration:
            body["duration"] = str(int(duration))
    elif api_format == "seedance_legacy":
        url = f"{base}/seedance/v3/contents/generations/tasks"
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        if first_image:
            content.append({"type": "image_url", "image_url": {"url": first_image}, "role": "reference_image"})
        body = {
            "model": comfly_model,
            "content": content,
            "ratio": payload.get("ratio") or payload.get("aspect_ratio") or "9:16",
            "duration": 10 if duration >= 10 else 5,
            "generate_audio": bool(payload.get("generate_audio", False)),
            "watermark": bool(payload.get("watermark", False)),
        }
        if payload.get("resolution"):
            body["resolution"] = payload.get("resolution")
        if payload.get("seed") is not None:
            body["seed"] = payload.get("seed")
    elif api_format == "sora2":
        url = f"{base}/v1/videos"
        request_mode = "multipart"
        body, multipart_files = _build_sora2_multipart(
            payload,
            model_id=comfly_model,
            prompt=prompt,
            duration=duration,
            first_image=first_image,
        )
    else:
        url = f"{base}/task/submit/t2v"
        body = {
            "model": comfly_model,
            "prompt": prompt,
        }
        if first_image:
            body["image_url"] = first_image

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            if api_format == "veo" and first_image:
                uploaded_image_ref = await _upload_image_to_comfly_for_veo(client, base, auth_headers, first_image)
                if uploaded_image_ref:
                    body["images"] = [uploaded_image_ref]
            logger.info("[Comfly] 视频生成请求 model=%s url=%s api_format=%s body=%s", comfly_model, url, api_format, json.dumps(body, ensure_ascii=False)[:500])
            if request_mode == "multipart":
                status_code, resp, attempts = await _request_comfly_json(
                    client,
                    "video_generate",
                    "POST",
                    url,
                    files=multipart_files or {},
                    headers=auth_headers,
                )
            else:
                status_code, resp, attempts = await _request_comfly_json(
                    client,
                    "video_generate",
                    "POST",
                    url,
                    json=body,
                    headers=json_headers,
                )
        logger.info(
            "[Comfly] 视频生成响应 HTTP=%s keys=%s preview=%s",
            status_code,
            list(resp.keys()) if isinstance(resp, dict) else type(resp).__name__,
            str(resp)[:500],
        )
        if status_code >= 400:
            err = resp.get("error", {})
            msg = err.get("message", str(resp)) if isinstance(err, dict) else str(err)
            return {"error": {"message": f"Comfly 返回 HTTP {status_code}: {msg}"}}
        if isinstance(resp, dict):
            resp["_api_format"] = api_format
            resp["_comfly_request_attempts"] = attempts
        return resp
    except Exception as e:
        logger.exception("[Comfly] 视频生成请求异常")
        return {"error": {"message": f"Comfly 请求失败: {e}"}}


async def call_comfly_task_query(task_id: str, token_group: str = "", api_format: str = "") -> Dict[str, Any]:
    """查询 Comfly 任务状态。api_format=veo 时用 /v2/videos/generations/{task_id}。"""
    base, key = get_comfly_config(token_group)
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if api_format in ("veo", "grok"):
        url = f"{base}/v2/videos/generations/{task_id}"
    elif api_format == "sora2":
        url = f"{base}/v1/videos/{task_id}"
    elif api_format == "seedance_legacy":
        url = f"{base}/seedance/v3/contents/generations/tasks/{task_id}"
    else:
        url = f"{base}/task/query/{task_id}"
    logger.info("[Comfly] 任务查询 task_id=%s url=%s api_format=%s", task_id, url, api_format or "(default)")
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            status_code, resp, attempts = await _request_comfly_json(
                client,
                "task_query",
                "GET",
                url,
                headers=headers,
            )
        logger.info(
            "[Comfly] 任务查询响应 HTTP=%s attempts=%s keys=%s preview=%s",
            status_code,
            attempts,
            list(resp.keys()) if isinstance(resp, dict) else type(resp).__name__,
            str(resp)[:500],
        )
        if isinstance(resp, dict) and attempts > 1:
            resp["_comfly_request_attempts"] = attempts
        return resp
    except Exception as e:
        logger.exception("[Comfly] 任务查询异常 task_id=%s", task_id)
        return {"error": {"message": f"Comfly 查询失败: {e}"}}


def format_comfly_image_response_as_sutui(resp: Dict[str, Any]) -> Dict[str, Any]:
    """将 Comfly DALL-E 格式响应转换为速推兼容格式（方便下游统一处理）。"""
    data_list = resp.get("data") or []
    if not data_list:
        return resp

    urls = []
    for item in data_list:
        u = item.get("url") or item.get("b64_json") or ""
        if u:
            urls.append(u)

    if not urls:
        return resp

    return {
        "task_id": resp.get("id") or f"comfly-img-{int(time.time())}",
        "status": "completed",
        "output": {
            "images": [{"url": u} for u in urls],
        },
        "url": urls[0],
        "_comfly": True,
    }


async def call_comfly_chat_completions(
    model_id: str,
    messages: List[Dict[str, Any]],
    *,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    stream: bool = False,
) -> Dict[str, Any]:
    """调用 Comfly /v1/chat/completions (OpenAI 兼容格式)。"""
    tg = _get_model_token_group(model_id)
    base, key = get_comfly_config(tg)
    if not base or not key:
        return {"error": {"message": "Comfly 未配置 (COMFLY_API_BASE / COMFLY_API_KEY)"}}

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    body: Dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        body["max_tokens"] = max_tokens
    if stream:
        body["stream"] = True

    url = f"{base}/v1/chat/completions"
    logger.info("[Comfly] chat/completions 请求 model=%s url=%s", model_id, url)
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            status_code, resp, attempts = await _request_comfly_json(
                client,
                "chat_completions",
                "POST",
                url,
                json=body,
                headers=headers,
            )
        if status_code >= 400:
            err = resp.get("error", {})
            msg = err.get("message", str(resp)) if isinstance(err, dict) else str(err)
            return {"error": {"message": f"Comfly chat 返回 HTTP {status_code}: {msg}"}}
        if isinstance(resp, dict) and attempts > 1:
            resp["_comfly_request_attempts"] = attempts
        return resp
    except Exception as e:
        logger.exception("[Comfly] chat/completions 请求异常")
        return {"error": {"message": f"Comfly chat 请求失败: {e}"}}


def format_comfly_video_response_as_sutui(resp: Dict[str, Any], *, fallback_task_id: str = "") -> Dict[str, Any]:
    """将 Comfly 视频响应转换为速推兼容格式。"""
    if not isinstance(resp, dict):
        return {"error": {"message": f"视频任务返回格式异常：{type(resp).__name__}"}}
    if isinstance(resp.get("error"), dict):
        return resp

    data = resp.get("data") or {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = {}
    task_id = (
        (fallback_task_id or "").strip()
        or _find_nested_string(resp, _COMFLY_VIDEO_TASK_ID_KEYS)
        or ""
    )
    raw_status = resp.get("status") or (data.get("status") if isinstance(data, dict) else "") or "pending"
    _VEO_STATUS_MAP = {
        "NOT_START": "pending",
        "IN_PROGRESS": "pending",
        "QUEUED": "pending",
        "SUBMITTED": "pending",
        "PROCESSING": "pending",
        "SUCCESS": "completed",
        "SUCCEEDED": "completed",
        "COMPLETED": "completed",
        "FAILURE": "failed",
        "FAILED": "failed",
        "ERROR": "failed",
    }
    status_key = str(raw_status).upper()
    status = _VEO_STATUS_MAP.get(status_key, raw_status)

    if not task_id:
        logger.warning(
            "[Comfly] format_video: task_id 为空! resp_keys=%s data_keys=%s resp_preview=%s",
            list(resp.keys()),
            list(data.keys()) if isinstance(data, dict) else type(data).__name__,
            str(resp)[:500],
        )

    result: Dict[str, Any] = {
        "task_id": task_id,
        "status": status,
        "_comfly": True,
    }

    video_url = _find_nested_string(resp, _COMFLY_VIDEO_URL_KEYS)
    if video_url and not video_url.startswith(("http://", "https://")):
        video_url = ""

    if not task_id and not video_url:
        logger.error(
            "[Comfly] format_video: missing task_id and video_url; resp_keys=%s data_keys=%s resp_preview=%s",
            list(resp.keys()),
            list(data.keys()) if isinstance(data, dict) else type(data).__name__,
            str(resp)[:500],
        )
        return {
            "error": {
                "message": "视频任务创建失败：上游没有返回任务ID，无法查询结果，请稍后重试。"
            },
            "status": "failed",
            "_comfly": True,
        }

    if video_url:
        result["output"] = {"video_url": video_url}
        result["url"] = video_url
        result["status"] = "completed"

    if status_key in {"FAILURE", "FAILED", "ERROR"}:
        result["status"] = "failed"
        fail_reason = resp.get("fail_reason") or resp.get("error") or ""
        if fail_reason:
            result["output"] = {"error": fail_reason}

    return result
