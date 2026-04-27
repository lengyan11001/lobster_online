"""按 xSkill 公开「模型文档」接口拉取 pricing，估算与预扣一致的算力参考（见 model-pricing-guide）。"""
from __future__ import annotations

import logging
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

_PRICING_CACHE: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}
_MCP_MODELS_PRICING_CACHE: Dict[str, Tuple[float, Dict[str, Dict[str, Any]]]] = {}


def _cache_ttl_success() -> float:
    return max(60.0, float(getattr(settings, "xskill_model_docs_cache_ttl_seconds", 3600) or 3600))


def _cache_ttl_miss() -> float:
    return 300.0


def _cache_get_valid(model_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """返回 (是否命中未过期缓存, pricing)；命中且 pricing 为 None 表示已缓存的「无定价」。"""
    now = time.time()
    ent = _PRICING_CACHE.get(model_id)
    if not ent:
        return False, None
    exp, pricing = ent
    if now > exp:
        _PRICING_CACHE.pop(model_id, None)
        return False, None
    return True, pricing


def _cache_set(model_id: str, pricing: Optional[Dict[str, Any]], *, success: bool) -> None:
    ttl = _cache_ttl_success() if success and pricing is not None else _cache_ttl_miss()
    _PRICING_CACHE[model_id] = (time.time() + ttl, pricing)


def _num_images_from_params(params: Dict[str, Any]) -> int:
    n = params.get("num_images", params.get("n", 1))
    try:
        if isinstance(n, (int, float)):
            return max(1, int(n))
        if isinstance(n, str) and n.strip().isdigit():
            return max(1, int(n.strip()))
    except (TypeError, ValueError):
        pass
    return 1


def _duration_seconds_from_params(params: Dict[str, Any]) -> Optional[float]:
    for key in ("duration", "duration_sec", "duration_seconds", "video_duration", "video_length", "audio_length", "length", "seconds"):
        v = params.get(key)
        if v is None:
            continue
        try:
            d = float(v)
            if d > 0:
                return d
        except (TypeError, ValueError):
            continue
    return None


def _audio_duration_seconds(params: Dict[str, Any]) -> Optional[float]:
    for key in ("duration", "duration_sec", "audio_duration", "length"):
        v = params.get(key)
        if v is None:
            continue
        try:
            d = float(v)
            if d > 0:
                return d
        except (TypeError, ValueError):
            continue
    return None


def _first_example_price(pricing: Dict[str, Any]) -> Optional[int]:
    ex: List[Any] = pricing.get("examples") or []
    if not isinstance(ex, list) or not ex:
        return None
    first = ex[0]
    if isinstance(first, dict) and first.get("price") is not None:
        try:
            return int(round(float(first["price"])))
        except (TypeError, ValueError):
            return None
    return None


def _pricing_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def _pricing_base_amount(pricing: Dict[str, Any]) -> Optional[float]:
    for key in ("base_price", "amount", "price", "credits", "credit_cost"):
        if key not in pricing:
            continue
        x = _pricing_number(pricing.get(key))
        if x is not None:
            return x
    return None


def _num_outputs_from_params(params: Dict[str, Any]) -> int:
    n = params.get("num_images") or params.get("n") or params.get("batch_size") or params.get("num_outputs") or 1
    try:
        return max(1, int(n))
    except (TypeError, ValueError):
        return 1


def _duration_from_example(ex: Dict[str, Any]) -> float:
    for key in ("duration", "duration_sec", "duration_seconds", "seconds", "length"):
        x = _pricing_number(ex.get(key))
        if x and x > 0:
            return x
    desc = str(ex.get("description") or ex.get("label") or ex.get("name") or "")
    digits = "".join(c for c in desc if c.isdigit() or c == ".")
    x = _pricing_number(digits)
    return x if x and x > 0 else 0.0


def _duration_example_price(pricing: Dict[str, Any], params: Dict[str, Any]) -> Optional[int]:
    examples = pricing.get("examples") or pricing.get("duration_prices") or pricing.get("prices") or []
    if not isinstance(examples, list):
        return None
    rows: List[Tuple[float, float]] = []
    for ex in examples:
        if not isinstance(ex, dict):
            continue
        p = _pricing_base_amount(ex)
        if p is None:
            continue
        rows.append((_duration_from_example(ex), p))
    if not rows:
        return None
    d = _duration_seconds_from_params(params)
    if d is not None:
        timed = sorted((dur, price) for dur, price in rows if dur > 0)
        for dur, price in timed:
            if d <= dur:
                return int(round(price))
        if timed:
            return int(round(timed[-1][1]))
    return int(round(max(price for _, price in rows)))


def _matrix_price_candidates(node: Any) -> List[float]:
    n = _pricing_number(node)
    if n is not None:
        return [n]
    if isinstance(node, dict):
        direct = _pricing_base_amount(node)
        out: List[float] = [direct] if direct is not None else []
        for v in node.values():
            out.extend(_matrix_price_candidates(v))
        return out
    if isinstance(node, list):
        out: List[float] = []
        for v in node:
            out.extend(_matrix_price_candidates(v))
        return out
    return []


def _price_from_quality_size_matrix(pricing: Dict[str, Any], params: Dict[str, Any]) -> Optional[int]:
    matrix = pricing.get("quality_size_matrix") or pricing.get("matrix") or pricing.get("size_quality_matrix")
    if not isinstance(matrix, dict):
        return None
    qualities = [
        str(params.get(k) or "").strip()
        for k in ("quality", "image_quality", "resolution_quality", "mode")
        if str(params.get(k) or "").strip()
    ]
    sizes = [
        str(params.get(k) or "").strip()
        for k in ("size", "image_size", "resolution", "aspect_ratio", "ratio")
        if str(params.get(k) or "").strip()
    ]
    for q in qualities:
        sub = matrix.get(q)
        if isinstance(sub, dict):
            for s in sizes:
                values = _matrix_price_candidates(sub.get(s))
                if values:
                    return int(round(max(values)))
            values = _matrix_price_candidates(sub)
            if values:
                return int(round(max(values)))
    for s in sizes:
        values = _matrix_price_candidates(matrix.get(s))
        if values:
            return int(round(max(values)))
    values = _matrix_price_candidates(matrix)
    return int(round(max(values))) if values else None


def estimate_credits_from_pricing(pricing: Dict[str, Any], params: Dict[str, Any]) -> Tuple[Optional[int], str]:
    """根据 docs 中 pricing + 本次 params 估算算力；无法精确时返回说明文案。"""
    ptype = (pricing.get("price_type") or "").strip().lower()
    desc = (pricing.get("price_description") or "").strip()
    base_f = _pricing_base_amount(pricing)

    if ptype == "fixed":
        if base_f is None:
            p = _first_example_price(pricing)
            if p is not None:
                return p, desc
            return None, desc or "固定计价但缺少 base_price"
        return int(round(base_f)), desc

    if ptype == "quantity_based":
        if base_f is None:
            p = _first_example_price(pricing)
            return (p, desc) if p is not None else (None, desc or "按量计价但缺少单价")
        n = _num_images_from_params(params)
        return int(round(base_f * n)), f"{desc}（按本次约 {n} 张估算）" if n != 1 else desc

    if ptype == "quality_size_matrix":
        p = _price_from_quality_size_matrix(pricing, params)
        if p is not None:
            return int(round(p * _num_outputs_from_params(params))), desc or "按质量/尺寸矩阵估算"
        if base_f is not None:
            return int(round(base_f * _num_outputs_from_params(params))), desc
        return None, desc or "质量/尺寸矩阵计价但缺少可用价格"

    if ptype in ("duration_based", "dynamic_per_second"):
        if base_f is None:
            p = _first_example_price(pricing)
            return (p, desc) if p is not None else (None, desc or "按时长计价但缺少单价")
        d = _duration_seconds_from_params(params)
        if d is None:
            ex_p = _first_example_price(pricing)
            if ex_p is not None:
                return ex_p, f"{desc}（未传 duration，按文档示例价参考）"
            return None, desc or "按时长计价，请在参数中提供 duration（秒）以便估算"
        return int(round(base_f * d)), f"{desc}（按本次约 {d:g} 秒估算）"

    if ptype == "per_minute":
        rate = _pricing_number(pricing.get("per_minute"))
        if rate is None:
            rate = base_f
        if rate is None:
            p = _first_example_price(pricing)
            return (p, desc) if p is not None else (None, desc or "按分钟计价但缺少单价")
        d = _duration_seconds_from_params(params) or 60.0
        minutes = max(1, math.ceil(d / 60.0))
        return int(round(rate * minutes)), f"{desc}（按本次约 {minutes:g} 分钟估算）"

    if ptype == "audio_duration_based":
        if base_f is None:
            p = _first_example_price(pricing)
            return (p, desc) if p is not None else (None, desc or "按音频时长计价但缺少单价")
        d = _audio_duration_seconds(params)
        if d is None:
            ex_p = _first_example_price(pricing)
            if ex_p is not None:
                return ex_p, f"{desc}（未传时长，按文档示例价参考）"
            return None, desc or "按音频时长计价，请提供 duration 以便估算"
        return int(round(base_f * d)), f"{desc}（按本次约 {d:g} 秒估算）"

    if ptype in ("duration_map", "duration_price"):
        if ptype == "duration_price" and base_f is None:
            p = _duration_example_price(pricing, params)
            return (p, desc) if p is not None else (None, desc or "按时长价格表计价但缺少可用价格")
        d = _duration_seconds_from_params(params)
        examples: List[Any] = pricing.get("examples") or []
        if d is not None and examples:
            for ex in examples:
                if not isinstance(ex, dict):
                    continue
                ex_desc = str(ex.get("description") or "")
                try:
                    ex_dur = float("".join(c for c in ex_desc if c.isdigit() or c == "."))
                except (ValueError, TypeError):
                    continue
                if ex_dur > 0 and d <= ex_dur:
                    return int(ex.get("price", 0)), f"{desc}（按 {d:g} 秒匹配档位）"
            if examples:
                return int(examples[-1].get("price", 0)), f"{desc}（按最长档位估算）"
        ex_p_dm = _first_example_price(pricing)
        if ex_p_dm is not None:
            return ex_p_dm, f"{desc}（按最短档位估算）" if desc else "按最短档位估算"
        if base_f is not None:
            return int(round(base_f)), desc
        return None, desc or "按时长分档计价，无法自动估算"

    if ptype == "token_based":
        return None, desc or "按 token 计费，确认前无法精确估算"

    ex_p = _first_example_price(pricing)
    if ex_p is not None:
        return ex_p, f"{desc}（按文档示例价参考）" if desc else "按文档示例价参考"
    if base_f is not None:
        return int(round(base_f)), desc or f"计价方式 {ptype or '未知'}，仅展示基础单价"
    return None, desc or f"计价方式 {ptype or '未知'}，无法自动估算"


async def fetch_model_pricing(model_id: str) -> Optional[Dict[str, Any]]:
    mid = (model_id or "").strip()
    if not mid:
        return None
    hit, cached = _cache_get_valid(mid)
    if hit:
        return cached

    base = (getattr(settings, "sutui_api_base", None) or "https://api.xskill.ai").strip().rstrip("/")
    lang = (getattr(settings, "xskill_model_docs_lang", None) or "zh").strip() or "zh"
    safe_mid = quote(mid, safe="")
    url = f"{base}/api/v3/models/{safe_mid}/docs"
    try:
        async with httpx.AsyncClient(timeout=12.0, trust_env=False) as client:
            r = await client.get(url, params={"lang": lang})
    except Exception as e:
        logger.warning("[xskill pricing] fetch failed model_id=%s err=%s", mid[:80], e)
        pricing = await _fetch_mcp_models_pricing(mid)
        _cache_set(mid, pricing, success=pricing is not None)
        return pricing

    if r.status_code != 200:
        logger.info("[xskill pricing] model_id=%s http=%s", mid[:80], r.status_code)
        pricing = await _fetch_mcp_models_pricing(mid)
        _cache_set(mid, pricing, success=pricing is not None)
        return pricing
    try:
        j = r.json()
    except Exception:
        pricing = await _fetch_mcp_models_pricing(mid)
        _cache_set(mid, pricing, success=pricing is not None)
        return pricing
    data = j.get("data") if isinstance(j, dict) else None
    pricing = None
    if isinstance(data, dict):
        pr = data.get("pricing")
        pricing = pr if isinstance(pr, dict) else None
    if pricing is None:
        pricing = await _fetch_mcp_models_pricing(mid)
    _cache_set(mid, pricing, success=pricing is not None)
    return pricing


def _sutui_auth_headers() -> Dict[str, str]:
    token = (
        getattr(settings, "sutui_server_token", None)
        or os.environ.get("SUTUI_SERVER_TOKEN")
        or os.environ.get("APIZ_API_KEY")
        or os.environ.get("XSKILL_API_KEY")
        or ""
    ).strip()
    if not token:
        return {}
    if token.lower().startswith("bearer "):
        return {"Authorization": token}
    return {"Authorization": f"Bearer {token}"}


def _mcp_models_urls() -> List[str]:
    base = (getattr(settings, "sutui_api_base", None) or "https://api.xskill.ai").strip().rstrip("/")
    urls = [f"{base}/api/v3/mcp/models?lang=zh-CN"]
    if "api.apiz.ai" not in base:
        urls.append("https://api.apiz.ai/api/v3/mcp/models?lang=zh-CN")
    return urls


async def _fetch_mcp_models_pricing(mid: str) -> Optional[Dict[str, Any]]:
    now = time.time()
    ent = _MCP_MODELS_PRICING_CACHE.get("models")
    if ent and now < ent[0]:
        p = ent[1].get(mid)
        return p if isinstance(p, dict) else None

    headers = _sutui_auth_headers()
    pricing_map: Dict[str, Dict[str, Any]] = {}
    for url in _mcp_models_urls():
        try:
            async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
                r = await client.get(url, headers=headers)
            if r.status_code >= 400:
                continue
            j = r.json()
            models = j.get("data", {}).get("models", []) if isinstance(j, dict) else []
            if not isinstance(models, list):
                continue
            for m in models:
                if not isinstance(m, dict):
                    continue
                model_id = str(m.get("id") or "").strip()
                pricing = m.get("pricing")
                if model_id and isinstance(pricing, dict):
                    pricing_map[model_id] = pricing
            if pricing_map:
                break
        except Exception as e:
            logger.debug("[xskill pricing] mcp models fetch failed url=%s err=%s", url, e)
            continue

    _MCP_MODELS_PRICING_CACHE["models"] = (now + _cache_ttl_success(), pricing_map)
    p = pricing_map.get(mid)
    return p if isinstance(p, dict) else None
