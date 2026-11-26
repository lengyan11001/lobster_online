"""代理池：单条/批量导入、连通性检测、被账号使用计数。"""
from __future__ import annotations

import asyncio
import socket
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
from ..models import ProxyEndpoint, ProxyIpProfile, PublishAccount

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


_HOSTING_KEYWORDS = (
    "leaseweb", "m247", "ntt", "ovh", "hetzner", "contabo", "vultr", "linode",
    "digitalocean", "amazon", "aws", "azure", "google cloud", "alibaba", "tencent",
    "web2objects", "b2 net", "zenlayer", "cogent", "gtt", "hosting", "datacenter",
    "data center", "server", "cloud",
)
_MOBILE_KEYWORDS = ("mobile", "cellular", "wireless", "4g", "5g", "lte")
_ISP_KEYWORDS = ("broadband", "telecom", "communications", "isp", "cable", "fiber", "dsl")


def _host_ip(server: str) -> str:
    host = (urlparse(_norm(server)).hostname or "").strip()
    if not host:
        return ""
    try:
        socket.inet_aton(host)
        return host
    except OSError:
        pass
    try:
        return socket.gethostbyname(host)
    except Exception:
        return ""


def _classify_ip(org: str, is_datacenter: bool, is_proxy: bool, is_vpn: bool) -> str:
    low = (org or "").lower()
    if is_datacenter or any(k in low for k in _HOSTING_KEYWORDS):
        return "datacenter"
    if any(k in low for k in _MOBILE_KEYWORDS):
        return "mobile"
    if is_proxy or is_vpn:
        return "proxy"
    if any(k in low for k in _ISP_KEYWORDS):
        return "residential"
    return "unknown"


async def _lookup_ip_profile(server: str) -> Dict[str, Any]:
    ip = _host_ip(server)
    out = {
        "ip": ip,
        "ip_type": "unknown",
        "asn": "",
        "org": "",
        "country": "",
        "is_proxy": False,
        "is_vpn": False,
    }
    if not ip:
        return out
    try:
        async with httpx.AsyncClient(timeout=8.0, trust_env=False) as c:
            r = await c.get("https://api.ipapi.is/", params={"q": ip})
        if r.status_code < 400:
            d = r.json() or {}
            asn = d.get("asn") or {}
            org = str((d.get("company") or {}).get("name") or asn.get("org") or "")
            out.update({
                "asn": str(asn.get("asn") or ""),
                "org": org[:255],
                "country": str((d.get("location") or {}).get("country") or ""),
                "is_proxy": bool(d.get("is_proxy")),
                "is_vpn": bool(d.get("is_vpn")),
            })
            out["ip_type"] = _classify_ip(
                org, bool(d.get("is_datacenter")), out["is_proxy"], out["is_vpn"]
            )
            return out
    except Exception:
        pass
    try:
        async with httpx.AsyncClient(timeout=8.0, trust_env=False) as c:
            r = await c.get(f"https://ipinfo.io/{ip}/json")
        if r.status_code < 400:
            d = r.json() or {}
            org = str(d.get("org") or "")
            out.update({"asn": org.split()[0] if org else "", "org": org[:255], "country": str(d.get("country") or "")})
            out["ip_type"] = _classify_ip(org, False, False, False)
    except Exception:
        pass
    return out


def _save_ip_profile(db: Session, server: str, prof: Dict[str, Any]) -> None:
    row = db.get(ProxyIpProfile, server)
    if row is None:
        row = ProxyIpProfile(server=server)
        db.add(row)
    row.ip = str(prof.get("ip") or "")
    row.ip_type = str(prof.get("ip_type") or "unknown")
    row.asn = str(prof.get("asn") or "")[:32]
    row.org = str(prof.get("org") or "")[:255]
    row.country = str(prof.get("country") or "")[:8]
    row.is_proxy = bool(prof.get("is_proxy"))
    row.is_vpn = bool(prof.get("is_vpn"))
    row.checked_at = datetime.utcnow()


def _profile_map(db: Session, servers: List[str]) -> Dict[str, ProxyIpProfile]:
    if not servers:
        return {}
    rows = db.query(ProxyIpProfile).filter(ProxyIpProfile.server.in_(servers)).all()
    return {r.server: r for r in rows}


def _score_proxy(row: ProxyEndpoint, used_by_count: int, ip_type: str = "unknown") -> Dict[str, Any]:
    """代理评分：状态 + 延迟 + 检测新鲜度 + 账号集中度。"""
    status = str(row.status or "unknown")
    used = max(0, int(used_by_count or 0))
    if status == "fail":
        return {
            "score": 0,
            "grade": "D",
            "risk_level": "high",
            "recommendation": "停用",
            "score_reasons": ["最近一次检测不通"],
        }
    if status != "ok":
        return {
            "score": 40,
            "grade": "D",
            "risk_level": "medium",
            "recommendation": "待检测",
            "score_reasons": ["尚未检测"],
        }
    else:
        base = 100
        reasons = []
        ms = row.latency_ms
        if ms is None:
            base -= 15
            reasons.append("缺少延迟数据")
        elif ms <= 600:
            pass
        elif ms <= 1200:
            base -= 5
            reasons.append("延迟略高")
        elif ms <= 2000:
            base -= 10
            reasons.append("延迟偏高")
        elif ms <= 3000:
            base -= 18
            reasons.append("延迟高")
        else:
            base -= 28
            reasons.append("延迟很高")

        checked = row.last_checked_at
        if not checked:
            base -= 25
            reasons.append("从未检测")
        else:
            age_h = (datetime.utcnow() - checked).total_seconds() / 3600.0
            if age_h > 24 * 7:
                base -= 20
                reasons.append("超过 7 天未检测")
            elif age_h > 24:
                base -= 10
                reasons.append("超过 24 小时未检测")
            elif age_h > 6:
                base -= 4
                reasons.append("超过 6 小时未检测")

    if used <= 1:
        pass
    elif used <= 3:
        base -= 5
        reasons.append(f"已有 {used} 个账号共用")
    elif used <= 6:
        base -= 15
        reasons.append(f"已有 {used} 个账号共用，集中度偏高")
    else:
        base -= 30
        reasons.append(f"已有 {used} 个账号共用，风险高")
    if ip_type == "datacenter":
        base -= 20
        reasons.append("机房 IP")
    elif ip_type in ("proxy", "vpn"):
        base -= 10
        reasons.append("代理/VPN IP")
    elif ip_type in ("residential", "mobile"):
        pass
    else:
        base -= 5
        reasons.append("IP 类型未知")

    score = max(0, min(100, base))
    if score >= 85:
        grade, risk, rec = "A", "low", "推荐"
    elif score >= 70:
        grade, risk, rec = "B", "low", "可用"
    elif score >= 50:
        grade, risk, rec = "C", "medium", "慎用"
    else:
        grade, risk, rec = "D", "high", "停用"
    return {
        "score": score,
        "grade": grade,
        "risk_level": risk,
        "recommendation": rec,
        "score_reasons": reasons,
    }


def _to_out(row: ProxyEndpoint, counts: Dict[str, int], profile: Optional[ProxyIpProfile] = None) -> Dict[str, Any]:
    used = counts.get(_norm(row.server), 0)
    ip_type = str(getattr(profile, "ip_type", "") or "unknown") if profile is not None else "unknown"
    scored = _score_proxy(row, used, ip_type)
    out = {
        "id": row.id,
        "label": row.label,
        "server": row.server,
        "username": row.username,
        "password": row.password,
        "status": row.status,
        "latency_ms": row.latency_ms,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else "",
        "last_error": row.last_error or "",
        "used_by_count": used,
        "ip_type": ip_type,
        "asn": str(getattr(profile, "asn", "") or "") if profile is not None else "",
        "org": str(getattr(profile, "org", "") or "") if profile is not None else "",
        "country": str(getattr(profile, "country", "") or "") if profile is not None else "",
        "is_proxy": bool(getattr(profile, "is_proxy", False)) if profile is not None else False,
        "is_vpn": bool(getattr(profile, "is_vpn", False)) if profile is not None else False,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }
    out.update(scored)
    return out


@router.get("/api/proxies", summary="代理池列表")
def list_proxies(
    healthy: bool = False,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ProxyEndpoint)
        .filter(ProxyEndpoint.user_id == current_user.id)
        .order_by(ProxyEndpoint.id.desc())
        .all()
    )
    counts = _usage_counts(db, current_user.id)
    profiles = _profile_map(db, [r.server for r in rows])
    items = [_to_out(r, counts, profiles.get(r.server)) for r in rows]
    if healthy:
        items = [x for x in items if x.get("status") == "ok" and int(x.get("score") or 0) >= 60]
    items.sort(key=lambda x: (int(x.get("score") or 0), -(x.get("latency_ms") or 99999)), reverse=True)
    return {"items": items}


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
    prof = await _lookup_ip_profile(row.server)
    _save_ip_profile(db, row.server, prof)
    db.commit()
    db.refresh(row)
    return _to_out(row, _usage_counts(db, current_user.id), db.get(ProxyIpProfile, row.server))


@router.get("/api/proxies/system", summary="检测系统代理")
def system_proxy(current_user: _ServerUser = Depends(get_current_user_for_local)):
    from ..services.youtube_api_upload import resolve_windows_system_proxy_url

    server = resolve_windows_system_proxy_url(_TEST_URL) or ""
    return {"server": server, "detected": bool(server)}


@router.post("/api/proxies/test-all", summary="批量测试全部代理")
async def test_all_proxies(
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    rows = db.query(ProxyEndpoint).filter(ProxyEndpoint.user_id == current_user.id).all()
    sem = asyncio.Semaphore(8)
    results: Dict[int, tuple] = {}

    async def run_one(row: ProxyEndpoint) -> None:
        async with sem:
            res = await _probe(_proxy_url(row.server, row.username, row.password))
            prof = await _lookup_ip_profile(row.server)
        results[row.id] = (res, prof)

    if rows:
        await asyncio.gather(*(run_one(r) for r in rows))
        for row in rows:
            res, prof = results.get(row.id, ({}, {}))
            row.status = "ok" if res.get("ok") else "fail"
            row.latency_ms = res.get("latency_ms")
            row.last_checked_at = datetime.utcnow()
            row.last_error = (res.get("error") or "")[:500] or None
            if prof:
                _save_ip_profile(db, row.server, prof)
        db.commit()
    counts = _usage_counts(db, current_user.id)
    profiles = _profile_map(db, [r.server for r in rows])
    items = [_to_out(r, counts, profiles.get(r.server)) for r in rows]
    items.sort(key=lambda x: (int(x.get("score") or 0), -(x.get("latency_ms") or 99999)), reverse=True)
    return {"items": items}


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
