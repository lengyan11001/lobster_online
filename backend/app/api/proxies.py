"""代理池：单条/批量导入、连通性检测、被账号使用计数。"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .auth import _ServerUser, get_current_user_for_local
from ..db import get_db
from ..models import ProxyEndpoint, PublishAccount

router = APIRouter()
_TEST_URL = "https://www.google.com/generate_204"


class ProxyIn(BaseModel):
    label: str = ""
    server: str
    username: str = ""
    password: str = ""


class ProxyImportIn(BaseModel):
    text: str


class ProxyTestIn(BaseModel):
    server: str = ""
    username: str = ""
    password: str = ""
    use_system: bool = False


def _norm(server: str) -> str:
    s = (server or "").strip().rstrip("/")
    if not s:
        return ""
    if "://" not in s:
        s = "http://" + s
    try:
        u = urlparse(s)
        if u.scheme not in ("http", "https") or not u.hostname:
            return ""
        port = u.port or (443 if u.scheme == "https" else 8080)
        return f"{u.scheme}://{u.hostname.lower()}:{port}"
    except Exception:
        return ""


def _proxy_url(server: str, username: str, password: str) -> Optional[str]:
    base = _norm(server)
    if not base:
        return None
    u = urlparse(base)
    auth = ""
    if username or password:
        auth = f"{quote(unquote(username or ''), safe='')}:{quote(unquote(password or ''), safe='')}@"
    return f"{u.scheme}://{auth}{u.hostname}:{u.port or (443 if u.scheme == 'https' else 8080)}"


async def _probe(proxy_url: Optional[str]) -> Dict[str, Any]:
    started = time.monotonic()
    try:
        kwargs: Dict[str, Any] = {"timeout": 8.0, "trust_env": False, "follow_redirects": True}
        if proxy_url:
            kwargs["proxy"] = proxy_url
        async with httpx.AsyncClient(**kwargs) as c:
            r = await c.get(_TEST_URL)
        ms = int((time.monotonic() - started) * 1000)
        if r.status_code in (200, 204):
            return {"ok": True, "latency_ms": ms, "error": ""}
        return {"ok": False, "latency_ms": ms, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "latency_ms": int((time.monotonic() - started) * 1000), "error": f"{type(e).__name__}: {e}"[:300]}


def _usage_counts(db: Session, user_id: int) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for a in db.query(PublishAccount).filter(PublishAccount.user_id == user_id).all():
        if (a.platform or "") == "youtube":
            continue
        meta = a.meta if isinstance(a.meta, dict) else {}
        px = ((meta.get("browser") or {}).get("proxy") or {}) if isinstance(meta.get("browser"), dict) else {}
        s = _norm(str(px.get("server") or ""))
        if s:
            counts[s] = counts.get(s, 0) + 1
    try:
        from .youtube_publish import list_youtube_account_entries

        for ent in list_youtube_account_entries(user_id).values():
            s = _norm(str(ent.get("proxy_server") or ""))
            if s:
                counts[s] = counts.get(s, 0) + 1
    except Exception:
        pass
    return counts


def _to_out(row: ProxyEndpoint, counts: Dict[str, int]) -> Dict[str, Any]:
    return {
        "id": row.id,
        "label": row.label,
        "server": row.server,
        "username": row.username,
        "password": row.password,
        "status": row.status,
        "latency_ms": row.latency_ms,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else "",
        "last_error": row.last_error or "",
        "used_by_count": counts.get(_norm(row.server), 0),
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


@router.get("/api/proxies", summary="代理池列表")
def list_proxies(
    healthy: bool = False,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    q = db.query(ProxyEndpoint).filter(ProxyEndpoint.user_id == current_user.id)
    if healthy:
        q = q.filter(ProxyEndpoint.status == "ok")
    rows = q.order_by(ProxyEndpoint.id.desc()).all()
    counts = _usage_counts(db, current_user.id)
    return {"items": [_to_out(r, counts) for r in rows]}


@router.post("/api/proxies", summary="新增代理")
def create_proxy(
    body: ProxyIn,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    server = _norm(body.server)
    if not server:
        raise HTTPException(400, detail="代理地址无效，需为 http://host:port 或 https://host:port")
    row = (
        db.query(ProxyEndpoint)
        .filter(
            ProxyEndpoint.user_id == current_user.id,
            ProxyEndpoint.server == server,
            ProxyEndpoint.username == (body.username or "").strip(),
        )
        .first()
    )
    if row is None:
        row = ProxyEndpoint(
            user_id=current_user.id,
            label=(body.label or "").strip()[:128],
            server=server,
            username=(body.username or "").strip(),
            password=body.password or "",
            status="unknown",
        )
        db.add(row)
    else:
        row.label = (body.label or row.label or "")[:128]
        row.password = body.password or row.password
    db.commit()
    db.refresh(row)
    return _to_out(row, _usage_counts(db, current_user.id))


def _parse_import_line(line: str) -> Optional[Dict[str, str]]:
    s = (line or "").strip()
    if not s or s.startswith("#"):
        return None
    label = ""
    if "|" in s:
        label, s = s.split("|", 1)
        label = label.strip()
        s = s.strip()
    if "://" in s:
        u = urlparse(s)
        if not u.hostname:
            return None
        port = u.port or (443 if u.scheme == "https" else 8080)
        return {
            "label": label,
            "server": f"{u.scheme}://{u.hostname}:{port}",
            "username": unquote(u.username or ""),
            "password": unquote(u.password or ""),
        }
    parts = s.split(":")
    if len(parts) == 4:
        host, port, user, pw = parts
    elif len(parts) == 2:
        host, port = parts
        user = pw = ""
    else:
        return None
    try:
        port_i = int(port)
    except ValueError:
        return None
    return {"label": label, "server": f"http://{host}:{port_i}", "username": user, "password": pw}


@router.post("/api/proxies/import", summary="批量导入代理")
def import_proxies(
    body: ProxyImportIn,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    imported = 0
    skipped = 0
    for line in (body.text or "").splitlines():
        item = _parse_import_line(line)
        if not item:
            if (line or "").strip() and not (line or "").strip().startswith("#"):
                skipped += 1
            continue
        server = _norm(item["server"])
        if not server:
            skipped += 1
            continue
        exists = (
            db.query(ProxyEndpoint)
            .filter(
                ProxyEndpoint.user_id == current_user.id,
                ProxyEndpoint.server == server,
                ProxyEndpoint.username == item["username"],
            )
            .first()
        )
        if exists:
            skipped += 1
            continue
        db.add(
            ProxyEndpoint(
                user_id=current_user.id,
                label=(item["label"] or "")[:128],
                server=server,
                username=item["username"],
                password=item["password"],
                status="unknown",
            )
        )
        imported += 1
    db.commit()
    return {"imported": imported, "skipped": skipped}


@router.post("/api/proxies/test", summary="测试任意代理")
async def test_any_proxy(body: ProxyTestIn, current_user: _ServerUser = Depends(get_current_user_for_local)):
    if body.use_system:
        from ..services.youtube_api_upload import resolve_windows_system_proxy_url

        server = resolve_windows_system_proxy_url(_TEST_URL) or ""
        if not server:
            return {"ok": False, "latency_ms": None, "error": "未检测到 Windows 系统代理/PAC", "server": ""}
        res = await _probe(server)
        res["server"] = server
        return res
    proxy_url = _proxy_url(body.server, body.username, body.password)
    if not proxy_url:
        return {"ok": False, "latency_ms": None, "error": "代理地址无效", "server": body.server}
    res = await _probe(proxy_url)
    res["server"] = _norm(body.server)
    return res


@router.post("/api/proxies/{proxy_id}/test", summary="测试代理池条目")
async def test_proxy(
    proxy_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    row = (
        db.query(ProxyEndpoint)
        .filter(ProxyEndpoint.id == proxy_id, ProxyEndpoint.user_id == current_user.id)
        .first()
    )
    if not row:
        raise HTTPException(404, detail="代理不存在")
    res = await _probe(_proxy_url(row.server, row.username, row.password))
    row.status = "ok" if res.get("ok") else "fail"
    row.latency_ms = res.get("latency_ms")
    row.last_checked_at = datetime.utcnow()
    row.last_error = (res.get("error") or "")[:500] or None
    db.commit()
    db.refresh(row)
    return _to_out(row, _usage_counts(db, current_user.id))


@router.get("/api/proxies/system", summary="检测系统代理")
def system_proxy(current_user: _ServerUser = Depends(get_current_user_for_local)):
    from ..services.youtube_api_upload import resolve_windows_system_proxy_url

    server = resolve_windows_system_proxy_url(_TEST_URL) or ""
    return {"server": server, "detected": bool(server)}


@router.delete("/api/proxies/{proxy_id}", summary="删除代理")
def delete_proxy(
    proxy_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    row = (
        db.query(ProxyEndpoint)
        .filter(ProxyEndpoint.id == proxy_id, ProxyEndpoint.user_id == current_user.id)
        .first()
    )
    if not row:
        raise HTTPException(404, detail="代理不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}
