"""Best-effort cloud sync for local creative generation job history."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from ..core.config import get_settings

logger = logging.getLogger(__name__)


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
        "request_payload": json_safe(request_payload or {}),
        "result_payload": json_safe(result_payload or {}),
        "saved_assets": json_safe(saved),
        "asset_ids": merged_asset_ids,
        "error": (error or "")[:4000] or None,
        "meta": json_safe(meta or {}),
    }
    headers = {"Authorization": auth, "Content-Type": "application/json"}
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
