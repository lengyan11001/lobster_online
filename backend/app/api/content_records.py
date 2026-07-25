from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from .assets import ASSETS_DIR, _normalize_auth_server_base
from .auth import _ServerUser, get_current_user_media_edit
from ..core.config import settings
from ..db import get_db
from ..models import Asset


logger = logging.getLogger(__name__)
router = APIRouter()


def _auth_headers(
    request: Optional[Request] = None,
    *,
    token: str = "",
    installation_id: str = "",
) -> dict[str, str]:
    auth = (request.headers.get("Authorization") or "").strip() if request else ""
    if not auth and token:
        auth = f"Bearer {token.strip()}"
    install = installation_id.strip()
    if request and not install:
        install = (
            request.headers.get("X-Installation-Id")
            or request.headers.get("x-installation-id")
            or ""
        ).strip()
    headers: dict[str, str] = {}
    if auth.lower().startswith("bearer "):
        headers["Authorization"] = auth
    if install:
        headers["X-Installation-Id"] = install
    return headers


def _server_base() -> str:
    return _normalize_auth_server_base((settings.auth_server_base or "").strip().rstrip("/"))


async def _post_records(
    records: list[dict[str, Any]],
    *,
    request: Optional[Request] = None,
    token: str = "",
    installation_id: str = "",
) -> dict[str, Any]:
    base = _server_base()
    headers = _auth_headers(request, token=token, installation_id=installation_id)
    if not base:
        return {"ok": False, "error": "AUTH_SERVER_BASE missing"}
    if "Authorization" not in headers:
        return {"ok": False, "error": "Authorization Bearer missing"}
    try:
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
            response = await client.post(
                f"{base}/api/content-records/sync",
                json={"records": records},
                headers=headers,
                follow_redirects=True,
            )
        if response.status_code >= 400:
            return {
                "ok": False,
                "status_code": response.status_code,
                "error": (response.text or "")[:800],
            }
        body = response.json()
        return body if isinstance(body, dict) else {"ok": False, "error": "invalid server response"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def _upload_document(
    path: Path,
    *,
    content_type: str,
    request: Optional[Request] = None,
    token: str = "",
    installation_id: str = "",
) -> tuple[str, dict[str, Any]]:
    base = _server_base()
    headers = _auth_headers(request, token=token, installation_id=installation_id)
    if not base:
        return "", {"error": "AUTH_SERVER_BASE missing"}
    if "Authorization" not in headers:
        return "", {"error": "Authorization Bearer missing"}
    if not path.is_file():
        return "", {"error": f"local file missing: {path.name}"}
    try:
        async with httpx.AsyncClient(timeout=180.0, trust_env=False) as client:
            response = await client.post(
                f"{base}/api/assets/upload-temp",
                files={"file": (path.name, path.read_bytes(), content_type)},
                headers=headers,
                follow_redirects=True,
            )
        if response.status_code >= 400:
            return "", {"status_code": response.status_code, "error": (response.text or "")[:800]}
        body = response.json()
        url = str(body.get("public_url") or "").strip() if isinstance(body, dict) else ""
        return url, {
            "status_code": response.status_code,
            "storage": body.get("storage") if isinstance(body, dict) else "",
            "error": "" if url else "public_url missing",
        }
    except Exception as exc:
        return "", {"error": f"{type(exc).__name__}: {exc}"}


def _ppt_record(asset: Asset, *, title: str = "", slide_count: int = 0) -> dict[str, Any]:
    meta = dict(asset.meta) if isinstance(asset.meta, dict) else {}
    resolved_title = str(title or meta.get("title") or asset.prompt or asset.filename or "PPT").strip()
    if slide_count:
        meta["slide_count"] = int(slide_count)
    return {
        "source": "online_ppt",
        "source_id": asset.asset_id,
        "kind": "ppt",
        "title": resolved_title,
        "summary": asset.prompt or "",
        "content": "",
        "cover_url": str(meta.get("cover_url") or ""),
        "file_url": asset.source_url or "",
        "filename": asset.filename or "",
        "status": "completed",
        "source_created_at": asset.created_at.isoformat() if asset.created_at else datetime.utcnow().isoformat(),
        "meta": {
            **meta,
            "model": asset.model or "",
            "file_size": int(asset.file_size or 0),
        },
    }


def _wechat_record(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "online_wechat_article",
        "source_id": str(item.get("id") or item.get("media_id") or "").strip(),
        "kind": "wechat_article",
        "title": str(item.get("title") or "公众号文章").strip(),
        "summary": str(item.get("digest") or "").strip(),
        "content": str(item.get("markdown") or "").strip(),
        "cover_url": str(item.get("cover_image_url") or "").strip(),
        "file_url": "",
        "filename": "",
        "status": str(item.get("push_status") or item.get("status") or "local_saved").strip(),
        "source_created_at": str(item.get("created_at") or item.get("local_saved_at") or "").strip(),
        "meta": {
            "author": str(item.get("author") or ""),
            "theme": str(item.get("theme") or ""),
            "media_id": str(item.get("media_id") or ""),
            "has_cover": bool(item.get("has_cover")),
            "image_uploads": int(item.get("image_uploads") or 0),
            "push_error": str(item.get("push_error") or "")[:1000],
        },
    }


async def sync_ppt_asset_to_server(
    asset_id: str,
    *,
    title: str = "",
    slide_count: int = 0,
    request: Optional[Request] = None,
    token: str = "",
    installation_id: str = "",
) -> dict[str, Any]:
    from ..db import SessionLocal

    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.asset_id == asset_id).first()
        if asset is None:
            return {"ok": False, "error": "PPT asset missing"}
        local_path = ASSETS_DIR / (asset.filename or "")
        file_url = str(asset.source_url or "").strip()
        upload_diag: dict[str, Any] = {}
        if not file_url or "/api/assets/temp/" in file_url:
            file_url, upload_diag = await _upload_document(
                local_path,
                content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                request=request,
                token=token,
                installation_id=installation_id,
            )
            if file_url:
                asset.source_url = file_url
                db.commit()
                db.refresh(asset)
        record = _ppt_record(asset, title=title, slide_count=slide_count)
        result = await _post_records(
            [record],
            request=request,
            token=token,
            installation_id=installation_id,
        )
        result["upload"] = upload_diag
        return result
    finally:
        db.close()


async def sync_wechat_article_to_server(item: dict[str, Any], request: Request) -> dict[str, Any]:
    record = _wechat_record(item)
    if not record["source_id"]:
        return {"ok": False, "error": "article id missing"}
    return await _post_records([record], request=request)


@router.post("/api/content-records/sync-local", summary="Backfill local PPT and WeChat article records")
async def sync_local_content_records(
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
    db: Session = Depends(get_db),
):
    records: list[dict[str, Any]] = []
    upload_failures: list[dict[str, str]] = []
    ppt_rows = (
        db.query(Asset)
        .filter(Asset.user_id == int(current_user.id), Asset.media_type == "document")
        .order_by(Asset.created_at.asc(), Asset.id.asc())
        .all()
    )
    for asset in ppt_rows:
        tags = str(asset.tags or "").lower()
        filename = str(asset.filename or "").lower()
        if "ppt" not in tags and not filename.endswith((".ppt", ".pptx")):
            continue
        file_url = str(asset.source_url or "").strip()
        if not file_url or "/api/assets/temp/" in file_url:
            file_url, diag = await _upload_document(
                ASSETS_DIR / (asset.filename or ""),
                content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                request=request,
            )
            if file_url:
                asset.source_url = file_url
                db.commit()
            else:
                upload_failures.append({"asset_id": asset.asset_id, "error": str(diag.get("error") or "上传失败")})
        records.append(_ppt_record(asset))

    from .wechat_article import _load_doc

    for item in _load_doc(int(current_user.id)).get("drafts", []):
        if not isinstance(item, dict):
            continue
        record = _wechat_record(item)
        if record["source_id"]:
            records.append(record)

    synced = 0
    created = 0
    updated = 0
    errors: list[str] = []
    for start in range(0, len(records), 100):
        result = await _post_records(records[start:start + 100], request=request)
        if result.get("ok"):
            synced += len(records[start:start + 100])
            created += int(result.get("created") or 0)
            updated += int(result.get("updated") or 0)
        else:
            errors.append(str(result.get("error") or "同步失败"))
    return {
        "ok": not errors and not upload_failures,
        "total": len(records),
        "synced": synced,
        "created": created,
        "updated": updated,
        "upload_failures": upload_failures,
        "errors": errors,
    }
