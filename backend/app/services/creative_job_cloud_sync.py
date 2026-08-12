"""Best-effort cloud sync for local creative generation job history."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from ..core.config import get_settings
from .oem_brand_context import with_oem_brand_header

logger = logging.getLogger(__name__)
_OMIT_CLOUD_PAYLOAD_KEYS = {
    "b64_json",
    "base64",
    "base64_json",
    "data_url",
    "dataUrl",
    "raw_response",
    "rawResponse",
    "raw_payload",
    "rawPayload",
    "upstream_raw",
    "upstreamRaw",
}


def normalized_auth_header(auth_header: str) -> str:
    raw = (auth_header or "").strip()
    if not raw:
        return ""
    return raw if raw.lower().startswith("bearer ") else f"Bearer {raw}"


def creative_asset_ids_from_saved(saved_assets: Any) -> List[str]:
    result: List[str] = []
    rows = saved_assets if isinstance(saved_assets, list) else []
    for item in rows:
        if not isinstance(item, dict):
            continue
        candidates = [
            item.get("asset_id"),
            (item.get("cloud_asset") or {}).get("asset_id") if isinstance(item.get("cloud_asset"), dict) else None,
            (item.get("asset") or {}).get("asset_id") if isinstance(item.get("asset"), dict) else None,
        ]
        for value in candidates:
            aid = str(value or "").strip()
            if aid and aid not in result:
                result.append(aid)
    return result


def json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except Exception:
        pass
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return str(value)


def compact_cloud_job_payload(value: Any, *, string_limit: int = 12000, max_items: int = 80, _depth: int = 0) -> Any:
    if _depth > 8:
        return {"omitted": True, "reason": "max_depth"}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("data:"):
            return {"omitted": True, "kind": "data_url", "length": len(value)}
        if len(value) > string_limit:
            return value[:string_limit] + f"...[truncated {len(value) - string_limit} chars]"
        return value
    if isinstance(value, (list, tuple)):
        result = [compact_cloud_job_payload(item, string_limit=string_limit, max_items=max_items, _depth=_depth + 1) for item in list(value)[:max_items]]
        if len(value) > max_items:
            result.append({"omitted": True, "reason": "too_many_items", "count": len(value) - max_items})
        return result
    if isinstance(value, dict):
        compacted: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in _OMIT_CLOUD_PAYLOAD_KEYS:
                low = key_text.lower()
                if low in {"b64_json", "base64", "base64_json"}:
                    kind = "base64"
                elif "data" in low:
                    kind = "data_url"
                else:
                    kind = "raw_payload"
                compacted[key_text] = {"omitted": True, "kind": kind, "length": len(str(item or ""))}
                continue
            compacted[key_text] = compact_cloud_job_payload(
                item,
                string_limit=string_limit,
                max_items=max_items,
                _depth=_depth + 1,
            )
        return compacted
    return json_safe(value)


async def sync_creative_job_to_cloud(
    *,
    auth_header: str,
    installation_id: str = "",
    job_id: str,
    feature_type: str,
    provider: str,
    status: str,
    stage: str = "",
    progress: Optional[int] = None,
    title: str = "",
    prompt: str = "",
    request_payload: Optional[Dict[str, Any]] = None,
    result_payload: Optional[Dict[str, Any]] = None,
    saved_assets: Optional[List[Any]] = None,
    asset_ids: Optional[List[str]] = None,
    error: str = "",
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    server_base = (get_settings().auth_server_base or "").strip().rstrip("/")
    auth = normalized_auth_header(auth_header)
    if not server_base or not auth or not job_id:
        logger.info(
            "[creative-job-sync] skip cloud sync: server_base=%s auth=%s job_id=%s",
            bool(server_base),
            bool(auth),
            bool(job_id),
        )
        return

    saved = saved_assets or []
    merged_asset_ids = []
    for aid in asset_ids or []:
        clean = str(aid or "").strip()
        if clean and clean not in merged_asset_ids:
            merged_asset_ids.append(clean)
    for aid in creative_asset_ids_from_saved(saved):
        if aid not in merged_asset_ids:
            merged_asset_ids.append(aid)

    payload: Dict[str, Any] = {
        "job_id": str(job_id).strip().lower(),
        "feature_type": feature_type,
        "provider": provider,
        "status": status,
        "stage": stage or None,
        "progress": progress,
        "title": title or None,
        "prompt": prompt or None,
        "request_payload": compact_cloud_job_payload(json_safe(request_payload or {})),
        "result_payload": compact_cloud_job_payload(json_safe(result_payload or {})),
        "saved_assets": compact_cloud_job_payload(json_safe(saved)),
        "asset_ids": merged_asset_ids,
        "error": (error or "")[:4000] or None,
        "meta": compact_cloud_job_payload(json_safe(meta or {})),
    }
    headers = with_oem_brand_header({"Authorization": auth, "Content-Type": "application/json"})
    if installation_id:
        headers["X-Installation-Id"] = installation_id

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, trust_env=False) as client:
            resp = await client.post(f"{server_base}/api/creative-jobs", json=payload, headers=headers)
        if resp.status_code >= 400:
            logger.warning(
                "[creative-job-sync] cloud sync failed feature=%s job_id=%s status=%s http=%s body=%s",
                feature_type,
                job_id,
                status,
                resp.status_code,
                (resp.text or "")[:500],
            )
            return
        logger.info("[creative-job-sync] synced feature=%s job_id=%s status=%s", feature_type, job_id, status)
    except Exception as exc:
        logger.warning(
            "[creative-job-sync] cloud sync error feature=%s job_id=%s status=%s err=%s",
            feature_type,
            job_id,
            status,
            exc,
            exc_info=True,
        )
