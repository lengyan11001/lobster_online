from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..core.config import settings
from .auth import _ServerUser, get_current_user_for_local
from .chat import get_customer_service_reply


router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
_STATE_PATH = _BASE_DIR / "data" / "msghelper_wechat_state.json"
_TEMP_DIR = _BASE_DIR / "temp_assets" / "msghelper_wechat"
_TEMP_DIR.mkdir(parents=True, exist_ok=True)

_TOKEN_CACHE: Dict[str, Any] = {"token": "", "expires_at": 0.0}
_UI_AUTH_CACHE: Dict[str, Any] = {"auth": {}, "expires_at": 0.0}
_SECRET_KEYS = {
    "authorization",
    "accesstoken",
    "access_token",
    "refreshtoken",
    "refresh_token",
}


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _read_state() -> Dict[str, Any]:
    if not _STATE_PATH.exists():
        return {}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(state: Dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            norm_key = str(key).replace("-", "").replace("_", "").lower()
            out[key] = "***" if norm_key in _SECRET_KEYS else _redact_secrets(item)
        return out
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value


def _user_key(user_id: int) -> str:
    return str(int(user_id or 0))


def _next_id(state: Dict[str, Any], key: str) -> int:
    value = int(state.get(key) or 0) + 1
    state[key] = value
    return value


def _append_log(
    user_id: int,
    *,
    config_id: Optional[int],
    action: str,
    upstream_path: str,
    request_payload: Optional[Dict[str, Any]],
    response_payload: Optional[Dict[str, Any]],
    success: bool,
    http_status: Optional[int] = None,
    latency_ms: Optional[int] = None,
    error_message: str = "",
) -> None:
    state = _read_state()
    key = _user_key(user_id)
    logs = state.setdefault("logs", {}).setdefault(key, [])
    logs.insert(
        0,
        {
            "id": _next_id(state, "next_log_id"),
            "config_id": config_id,
            "action": action,
            "upstream_path": upstream_path,
            "success": bool(success),
            "http_status": http_status,
            "latency_ms": latency_ms,
            "request_payload": _redact_secrets(request_payload or {}),
            "response_payload": _redact_secrets(response_payload or {}),
            "error_message": (error_message or "")[:1200],
            "created_at": _now_iso(),
        },
    )
    del logs[300:]
    _write_state(state)


def _contact_cache_key(config_id: int) -> str:
    return str(int(config_id or 0))


def _load_contact_cache(user_id: int, config_id: int) -> Dict[str, Any]:
    state = _read_state()
    user_cache = (state.get("contact_cache") or {}).get(_user_key(user_id), {})
    cached = user_cache.get(_contact_cache_key(config_id), {})
    return cached if isinstance(cached, dict) else {}


def _save_contact_cache(
    user_id: int,
    config_id: int,
    *,
    contacts: Optional[List[Dict[str, Any]]] = None,
    groups: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    state = _read_state()
    user_cache = state.setdefault("contact_cache", {}).setdefault(_user_key(user_id), {})
    cached = user_cache.setdefault(_contact_cache_key(config_id), {})
    if contacts is not None:
        cached["contacts"] = contacts
    if groups is not None:
        cached["groups"] = groups
    cached["updated_at"] = _now_iso()
    _write_state(state)
    return cached


def _msghelper_base() -> str:
    return (getattr(settings, "msghelper_api_base", None) or "http://127.0.0.1:6003").strip().rstrip("/")


def _msghelper_local_root() -> Path:
    configured = os.environ.get("MSGHELPER_DATA_DIR") or ""
    if configured.strip():
        return Path(configured).expanduser()
    local_appdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local_appdata) / "MsgHelper" / "MsgHelper"


def _msghelper_db_path() -> Path:
    return _msghelper_local_root() / "db" / "msghelper.db"


def _msghelper_leveldb_dir() -> Path:
    return _msghelper_local_root() / "web_profile" / "Local Storage" / "leveldb"


def _extract_leveldb_value(blob: bytes, key: str, *, token: bool = False) -> str:
    key_bytes = key.encode("utf-8")
    pattern = rb"[A-Za-z0-9_\-\.]{16,}" if token else rb"\d{1,20}"
    values: List[str] = []
    start = 0
    while True:
        idx = blob.find(key_bytes, start)
        if idx < 0:
            break
        chunk = blob[idx + len(key_bytes) : idx + len(key_bytes) + 256]
        match = re.search(pattern, chunk)
        if match:
            values.append(match.group(0).decode("utf-8", errors="ignore"))
        start = idx + len(key_bytes)
    return values[-1] if values else ""


def _read_msghelper_ui_auth(force: bool = False) -> Dict[str, str]:
    now = time.time()
    cached = _UI_AUTH_CACHE.get("auth") or {}
    if cached and not force and float(_UI_AUTH_CACHE.get("expires_at") or 0) > now:
        return dict(cached)
    auth: Dict[str, str] = {
        "access_token": "",
        "refresh_token": "",
        "tenant_id": "",
        "user_id": "",
        "expires_time": "",
    }
    leveldb = _msghelper_leveldb_dir()
    if leveldb.exists():
        try:
            files = sorted(
                [p for p in leveldb.glob("*") if p.suffix.lower() in {".log", ".ldb"}],
                key=lambda p: p.stat().st_mtime if p.exists() else 0,
            )
        except Exception:
            files = []
        for path in files:
            try:
                blob = path.read_bytes()
            except Exception:
                continue
            auth["access_token"] = _extract_leveldb_value(blob, "ACCESS_TOKEN", token=True) or auth["access_token"]
            auth["refresh_token"] = _extract_leveldb_value(blob, "REFRESH_TOKEN", token=True) or auth["refresh_token"]
            auth["tenant_id"] = _extract_leveldb_value(blob, "TENANT_ID") or auth["tenant_id"]
            auth["user_id"] = _extract_leveldb_value(blob, "USER_ID") or auth["user_id"]
            auth["expires_time"] = _extract_leveldb_value(blob, "EXPIRES_TIME") or auth["expires_time"]
    if auth.get("tenant_id") and auth.get("user_id") and (auth.get("access_token") or auth.get("refresh_token")):
        _UI_AUTH_CACHE["auth"] = dict(auth)
        _UI_AUTH_CACHE["expires_at"] = now + 15
        return auth
    return {}


def _msghelper_credentials(*, require_client: bool = False) -> Dict[str, str]:
    tenant_id = (getattr(settings, "msghelper_tenant_id", None) or "").strip()
    client_id = (getattr(settings, "msghelper_client_id", None) or "").strip()
    client_secret = (getattr(settings, "msghelper_client_secret", None) or "").strip()
    user_id = (getattr(settings, "msghelper_user_id", None) or "0").strip() or "0"
    ui_auth = _read_msghelper_ui_auth()
    if ui_auth:
        tenant_id = tenant_id or ui_auth.get("tenant_id", "")
        if not user_id or user_id == "0":
            user_id = ui_auth.get("user_id", "") or "0"
    if require_client and (not tenant_id or not client_id or not client_secret):
        raise HTTPException(
            status_code=400,
            detail="未配置 MSGHELPER_TENANT_ID / MSGHELPER_CLIENT_ID / MSGHELPER_CLIENT_SECRET",
        )
    if not tenant_id or not user_id or user_id == "0":
        raise HTTPException(status_code=400, detail="请先打开 MsgHelper 并登录账号")
    return {
        "tenant_id": tenant_id,
        "client_id": client_id,
        "client_secret": client_secret,
        "user_id": user_id,
        "access_token": ui_auth.get("access_token", "") if ui_auth else "",
        "refresh_token": ui_auth.get("refresh_token", "") if ui_auth else "",
    }


def _msghelper_identity_payload() -> Dict[str, Any]:
    creds = _msghelper_credentials()
    payload: Dict[str, Any] = {
        "userId": creds["user_id"],
        "tenantId": creds["tenant_id"],
    }
    if creds.get("access_token"):
        payload["accessToken"] = creds["access_token"]
    if creds.get("refresh_token"):
        payload["refreshToken"] = creds["refresh_token"]
    return payload


def _unwrap_data(body: Any) -> Any:
    if isinstance(body, dict) and "data" in body:
        return body.get("data")
    return body


def _is_success(body: Any, http_status: int = 200) -> bool:
    if http_status < 200 or http_status >= 300:
        return False
    if not isinstance(body, dict):
        return True
    code = body.get("code")
    if code is not None:
        try:
            if int(code) not in (0, 200):
                return False
        except Exception:
            return False
    inner = body.get("data")
    if isinstance(inner, dict) and "code" in inner:
        try:
            return int(inner.get("code") or 0) == 0
        except Exception:
            return False
    return True


def _error_text(body: Any, fallback: str = "MsgHelper request failed") -> str:
    if isinstance(body, dict):
        for key in ("msg", "message", "error", "detail"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        inner = body.get("data")
        if isinstance(inner, dict):
            for key in ("msg", "message", "error", "detail"):
                value = inner.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return fallback


def _list_from_response(body: Any) -> List[Any]:
    data = _unwrap_data(body)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("list", "items", "records", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    if isinstance(body, list):
        return body
    return []


async def _login_msghelper(force: bool = False) -> str:
    creds = _msghelper_credentials(require_client=True)
    now = time.time()
    cached = str(_TOKEN_CACHE.get("token") or "")
    if cached and not force and float(_TOKEN_CACHE.get("expires_at") or 0) > now + 30:
        return cached
    payload = {
        "tenantId": creds["tenant_id"],
        "clientId": creds["client_id"],
        "clientSecret": creds["client_secret"],
        "loginMode": "client_credentials",
        "scope": "msghelper.api",
        "forceRelogin": False,
    }
    async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
        try:
            resp = await client.post(_msghelper_base() + "/api/auth/login", json=payload)
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"MsgHelper 本机服务不可用：{exc}") from exc
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:1000]}
    if not _is_success(body, resp.status_code):
        raise HTTPException(status_code=502, detail=_error_text(body, "MsgHelper 登录失败"))
    data = body.get("data") if isinstance(body, dict) else {}
    token_obj = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
    token = ""
    if isinstance(token_obj, dict):
        token = str(token_obj.get("access_token") or token_obj.get("accessToken") or "").strip()
        expires_in = int(token_obj.get("expires_in") or token_obj.get("expiresIn") or 7200)
    else:
        expires_in = 7200
    if not token:
        raise HTTPException(status_code=502, detail="MsgHelper 登录成功但没有返回 access_token")
    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expires_at"] = now + max(60, expires_in - 60)
    return token


async def _msghelper_request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Any] = None,
    timeout: float = 60.0,
    auth: bool = True,
    retry_on_401: bool = True,
) -> tuple[Any, int, int]:
    creds = _msghelper_credentials()
    token = ""
    if auth:
        token = creds.get("access_token") or await _login_msghelper()
    headers: Dict[str, str] = {
        "tenant-id": creds["tenant_id"],
        "tenantId": creds["tenant_id"],
        "user-id": creds["user_id"],
        "userId": creds["user_id"],
    }
    if creds.get("refresh_token"):
        headers["refresh-token"] = creds["refresh_token"]
        headers["refreshToken"] = creds["refresh_token"]
    if auth:
        headers["Authorization"] = f"Bearer {token}"
    if path.startswith("/api/wx-bindings/"):
        if creds.get("client_id"):
            headers["client-id"] = creds["client_id"]
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        try:
            resp = await client.request(
                method.upper(),
                _msghelper_base() + path,
                params=params,
                json=json_body,
                headers=headers,
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"MsgHelper 请求失败：{exc}") from exc
    latency_ms = int((time.perf_counter() - started) * 1000)
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:2000]}
    if resp.status_code == 401 and auth and retry_on_401:
        _read_msghelper_ui_auth(force=True)
        if creds.get("client_id") and creds.get("client_secret"):
            await _login_msghelper(force=True)
        return await _msghelper_request(
            method,
            path,
            params=params,
            json_body=json_body,
            timeout=timeout,
            auth=auth,
            retry_on_401=False,
        )
    return body, resp.status_code, latency_ms


async def _call_msghelper(
    user_id: int,
    *,
    config_id: Optional[int],
    action: str,
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Any] = None,
    timeout: float = 60.0,
    raise_on_fail: bool = True,
) -> Dict[str, Any]:
    body: Any = None
    status: Optional[int] = None
    latency: Optional[int] = None
    success = False
    try:
        body, status, latency = await _msghelper_request(
            method,
            path,
            params=params,
            json_body=json_body,
            timeout=timeout,
        )
        success = _is_success(body, status)
        _append_log(
            user_id,
            config_id=config_id,
            action=action,
            upstream_path=path,
            request_payload=json_body if isinstance(json_body, dict) else {"payload": json_body},
            response_payload=body if isinstance(body, dict) else {"payload": body},
            success=success,
            http_status=status,
            latency_ms=latency,
            error_message="" if success else _error_text(body),
        )
        if raise_on_fail and not success:
            raise HTTPException(status_code=502, detail=_error_text(body))
        return {"ok": success, "upstream": body, "http_status": status, "latency_ms": latency}
    except HTTPException as exc:
        if body is None:
            _append_log(
                user_id,
                config_id=config_id,
                action=action,
                upstream_path=path,
                request_payload=json_body if isinstance(json_body, dict) else {"payload": json_body},
                response_payload=None,
                success=False,
                http_status=status,
                latency_ms=latency,
                error_message=str(exc.detail),
            )
        raise


async def _opened_wxs() -> List[Dict[str, Any]]:
    body, status, _ = await _msghelper_request("GET", "/api/get_opened_wxs", timeout=20)
    if not _is_success(body, status):
        raise HTTPException(status_code=502, detail=_error_text(body, "获取已登录微信失败"))
    return [x for x in _list_from_response(body) if isinstance(x, dict)]


async def _bindings() -> List[Dict[str, Any]]:
    creds = _msghelper_credentials()
    if not creds.get("client_id"):
        return []
    params = {"tenantId": creds["tenant_id"], "clientId": creds["client_id"], "userId": creds["user_id"]}
    body, status, _ = await _msghelper_request("GET", "/api/wx-bindings/list", params=params, timeout=20)
    if status == 404:
        return []
    if not _is_success(body, status):
        raise HTTPException(status_code=502, detail=_error_text(body, "查询微信绑定失败"))
    return [x for x in _list_from_response(body) if isinstance(x, dict)]


def _same_wx(binding: Dict[str, Any], wx: Dict[str, Any]) -> bool:
    b_no = str(binding.get("wxNo") or "").strip()
    w_no = str(wx.get("wxNo") or "").strip()
    if b_no and w_no and b_no == w_no:
        return True
    b_name = str(binding.get("wxName") or "").strip()
    w_name = str(wx.get("nickName") or wx.get("wxName") or "").strip()
    return bool(b_name and w_name and b_name == w_name)


def _db_fetch_all(sql: str, params: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    db_path = _msghelper_db_path()
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


def _query_contacts_from_db(config_id: int, *, limit: int = 2000) -> List[Dict[str, Any]]:
    rows = _db_fetch_all(
        """
        select
            id, wx_instance_id, user_id, tenant_id, name, nick_name, remark, call_name,
            wx_no, signature, tags, type, variables, source, status, update_time, created_time
        from wx_contact
        where wx_instance_id = ?
        order by coalesce(update_time, created_time) desc
        limit ?
        """,
        (str(config_id), int(limit)),
    )
    items: List[Dict[str, Any]] = []
    for row in rows:
        item = {
            "id": row.get("id"),
            "wxInstanceId": row.get("wx_instance_id"),
            "userId": row.get("user_id"),
            "tenantId": row.get("tenant_id"),
            "name": row.get("name"),
            "nickName": row.get("nick_name"),
            "remark": row.get("remark"),
            "callName": row.get("call_name"),
            "wxNo": row.get("wx_no"),
            "signature": row.get("signature"),
            "tags": row.get("tags"),
            "type": row.get("type"),
            "variables": row.get("variables"),
            "source": row.get("source") or "msghelper_db",
            "status": row.get("status"),
            "updatedTime": row.get("update_time"),
            "createdTime": row.get("created_time"),
        }
        items.append(_normal_contact(item))
    return items


def _query_groups_from_db(config_id: int, *, limit: int = 1000) -> List[Dict[str, Any]]:
    rows = _db_fetch_all(
        """
        select
            id, wx_instance_id, user_id, tenant_id, group_key, group_name, member_count,
            remark, tags, updated_time, created_time
        from wx_group
        where wx_instance_id = ?
        order by coalesce(updated_time, created_time) desc
        limit ?
        """,
        (str(config_id), int(limit)),
    )
    items: List[Dict[str, Any]] = []
    for row in rows:
        item = {
            "id": row.get("id"),
            "wxInstanceId": row.get("wx_instance_id"),
            "userId": row.get("user_id"),
            "tenantId": row.get("tenant_id"),
            "groupKey": row.get("group_key"),
            "groupName": row.get("group_name"),
            "memberCount": row.get("member_count"),
            "remark": row.get("remark"),
            "tags": row.get("tags"),
            "source": "msghelper_db",
            "updatedTime": row.get("updated_time"),
            "createdTime": row.get("created_time"),
        }
        items.append(_normal_group(item))
    return items


def _local_wx_instance_id(wx: Dict[str, Any]) -> int:
    explicit = wx.get("wxInstanceId") or wx.get("wx_instance_id") or wx.get("id")
    try:
        if explicit and int(explicit) > 0:
            return int(explicit)
    except Exception:
        pass
    wx_no = str(wx.get("wxNo") or "").strip()
    wx_name = str(wx.get("nickName") or wx.get("wxName") or "").strip()
    rows: List[Dict[str, Any]] = []
    if wx_no:
        rows = _db_fetch_all(
            "select wx_instance_id from wx_contact where wx_no = ? order by update_time desc limit 1",
            (wx_no,),
        )
    if not rows and wx_name:
        rows = _db_fetch_all(
            "select wx_instance_id from wx_contact where name = ? or nick_name = ? order by update_time desc limit 1",
            (wx_name, wx_name),
        )
    if not rows:
        rows = _db_fetch_all(
            "select wx_instance_id from wx_import_meta order by coalesce(update_time, created_time) desc limit 1"
        )
    try:
        return int((rows[0] if rows else {}).get("wx_instance_id") or 0)
    except Exception:
        return 0


async def _ensure_configs(user_id: int) -> List[Dict[str, Any]]:
    creds = _msghelper_credentials()
    opened = await _opened_wxs()
    bindings = await _bindings()
    if not creds.get("client_id"):
        out: List[Dict[str, Any]] = []
        for wx in opened:
            instance_id = _local_wx_instance_id(wx)
            if instance_id <= 0:
                continue
            binding = {
                "wxInstanceId": instance_id,
                "wxName": wx.get("nickName") or wx.get("wxName") or "",
                "wxNo": wx.get("wxNo") or "",
                "userId": creds["user_id"],
                "tenantId": creds["tenant_id"],
                "source": "msghelper_local_db",
            }
            out.append(_config_out(binding, wx))
        return out
    for wx in opened:
        if any(_same_wx(b, wx) for b in bindings):
            continue
        payload = {
            "tenantId": creds["tenant_id"],
            "clientId": creds["client_id"],
            "userId": creds["user_id"],
            "wxName": wx.get("nickName") or wx.get("wxName") or "",
            "wxNo": wx.get("wxNo") or "",
        }
        result = await _call_msghelper(
            user_id,
            config_id=None,
            action="bind_wx",
            method="POST",
            path="/api/wx-bindings/bind",
            json_body=payload,
            raise_on_fail=False,
        )
        data = _unwrap_data(result.get("upstream"))
        if isinstance(data, dict) and data.get("wxInstanceId"):
            bindings.append(data)
    if opened:
        latest = await _bindings()
        if latest:
            bindings = latest
    out: List[Dict[str, Any]] = []
    for binding in bindings:
        match = next((wx for wx in opened if _same_wx(binding, wx)), None)
        out.append(_config_out(binding, match))
    return out


def _config_id(raw: Any) -> int:
    try:
        value = int(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="config_id 无效")
    if value <= 0:
        raise HTTPException(status_code=400, detail="config_id 无效")
    return value


def _config_out(binding: Dict[str, Any], opened: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    config_id = int(binding.get("wxInstanceId") or binding.get("id") or 0)
    wx_name = str(binding.get("wxName") or (opened or {}).get("nickName") or "").strip()
    wx_no = str(binding.get("wxNo") or (opened or {}).get("wxNo") or "").strip()
    label = wx_name or wx_no or f"微信实例 {config_id}"
    return {
        "id": config_id,
        "label": label,
        "guid": str(config_id),
        "status": "active",
        "last_status": 2 if opened else 0,
        "last_status_at": _now_iso(),
        "auto_reply_enabled": False,
        "auto_reply_memory_doc_ids": [],
        "auto_reply_prompt": "",
        "auto_reply_handoff_keywords": "",
        "auto_reply_cooldown_seconds": 8,
        "auto_reply_max_context": 12,
        "has_auto_reply_knowledge": False,
        "created_at": binding.get("createdTime") or None,
        "updated_at": binding.get("updatedTime") or None,
        "meta": {"binding": binding, "opened": opened or {}, "wxName": wx_name, "wxNo": wx_no},
    }


async def _context(user_id: int, config_id: int) -> Dict[str, Any]:
    configs = await _ensure_configs(user_id)
    cfg = next((x for x in configs if int(x.get("id") or 0) == int(config_id)), None)
    if not cfg:
        raise HTTPException(status_code=404, detail="微信实例不存在，请确认 MsgHelper 已绑定微信")
    meta = cfg.get("meta") if isinstance(cfg.get("meta"), dict) else {}
    opened = meta.get("opened") if isinstance(meta.get("opened"), dict) else {}
    binding = meta.get("binding") if isinstance(meta.get("binding"), dict) else {}
    wx_name = str(opened.get("nickName") or binding.get("wxName") or cfg.get("label") or "").strip()
    wx_no = str(opened.get("wxNo") or binding.get("wxNo") or "").strip()
    return {
        "id": int(cfg["id"]),
        "config": cfg,
        "binding": binding,
        "opened": opened,
        "wx_name": wx_name,
        "wx_no": wx_no,
        "version": str(opened.get("version") or opened.get("wxVersion") or "4"),
        "hwnd": int(opened.get("hwnd") or 0),
    }


def _normal_contact(item: Dict[str, Any]) -> Dict[str, Any]:
    contact_id = str(item.get("id") or item.get("contactId") or item.get("wxNo") or item.get("wxid") or "").strip()
    wx_no = str(item.get("wxNo") or item.get("wxid") or item.get("username") or "").strip()
    name = str(item.get("name") or item.get("remark") or item.get("nickName") or item.get("nickname") or wx_no or contact_id).strip()
    return {
        "id": contact_id or wx_no,
        "username": contact_id or wx_no,
        "contact_key": contact_id or wx_no,
        "wxNo": wx_no or contact_id,
        "nickname": name,
        "display_name": name,
        "remark": str(item.get("remark") or ""),
        "callName": str(item.get("callName") or ""),
        "source": str(item.get("source") or "msghelper"),
        "status": str(item.get("status") if item.get("status") is not None else "synced"),
        "raw": item,
    }


def _normal_group(item: Dict[str, Any]) -> Dict[str, Any]:
    group_id = str(item.get("id") or item.get("groupId") or item.get("wxNo") or "").strip()
    wx_no = str(item.get("wxNo") or item.get("groupWxNo") or group_id).strip()
    name = str(item.get("groupName") or item.get("name") or item.get("remark") or wx_no or group_id).strip()
    return {
        "id": group_id or wx_no,
        "username": group_id or wx_no,
        "contact_key": group_id or wx_no,
        "wxNo": wx_no,
        "nickname": name,
        "display_name": name,
        "groupName": name,
        "memberCount": item.get("memberCount"),
        "remark": str(item.get("remark") or ""),
        "source": str(item.get("source") or "msghelper"),
        "status": str(item.get("status") if item.get("status") is not None else "synced"),
        "raw": item,
    }


async def _query_contacts(config_id: int, *, limit: int = 2000) -> List[Dict[str, Any]]:
    try:
        body, status, _ = await _msghelper_request(
            "GET",
            "/api/contacts",
            params={"wxInstanceId": config_id, "limit": limit, "offset": 0},
            timeout=30,
        )
        if _is_success(body, status):
            items = [_normal_contact(x) for x in _list_from_response(body) if isinstance(x, dict)]
            return items or _query_contacts_from_db(config_id, limit=limit)
    except Exception:
        pass
    items = _query_contacts_from_db(config_id, limit=limit)
    if items:
        return items
    raise HTTPException(status_code=502, detail="查询联系人失败")


async def _query_groups(config_id: int, *, limit: int = 1000) -> List[Dict[str, Any]]:
    try:
        creds = _msghelper_credentials()
        body, status, _ = await _msghelper_request(
            "GET",
            "/api/groups",
            params={
                "wxInstanceId": config_id,
                "userId": creds["user_id"],
                "tenantId": creds["tenant_id"],
                "limit": limit,
                "offset": 0,
            },
            timeout=30,
        )
        if _is_success(body, status):
            items = [_normal_group(x) for x in _list_from_response(body) if isinstance(x, dict)]
            return items or _query_groups_from_db(config_id, limit=limit)
    except Exception:
        pass
    items = _query_groups_from_db(config_id, limit=limit)
    if items:
        return items
    raise HTTPException(status_code=502, detail="查询群失败")


def _matches_target(item: Dict[str, Any], key: str) -> bool:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    values = [
        item.get("id"),
        item.get("username"),
        item.get("contact_key"),
        item.get("wxNo"),
        item.get("nickname"),
        item.get("display_name"),
        raw.get("id"),
        raw.get("wxNo"),
        raw.get("name"),
        raw.get("nickName"),
        raw.get("groupName"),
    ]
    key = str(key or "").strip()
    return any(str(v or "").strip() == key for v in values)


async def _selected_contacts(config_id: int, keys: List[str]) -> List[Dict[str, Any]]:
    contacts = await _query_contacts(config_id)
    out: List[Dict[str, Any]] = []
    for key in keys:
        item = next((x for x in contacts if _matches_target(x, key)), None)
        if item:
            raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
            out.append(
                {
                    "id": str(raw.get("id") or item.get("id") or item.get("wxNo") or key),
                    "name": str(raw.get("name") or item.get("nickname") or key),
                    "wxNo": str(raw.get("wxNo") or item.get("wxNo") or key),
                    "nickName": str(raw.get("nickName") or item.get("nickname") or key),
                    "variables": str(raw.get("variables") or ""),
                    "callName": str(raw.get("callName") or item.get("callName") or ""),
                    "remark": str(raw.get("remark") or item.get("remark") or ""),
                    "type": 0,
                }
            )
        else:
            out.append({"id": key, "name": key, "wxNo": key, "nickName": key, "variables": "", "callName": "", "remark": "", "type": 0})
    return out


async def _selected_groups(config_id: int, keys: List[str]) -> List[Dict[str, Any]]:
    groups = await _query_groups(config_id)
    out: List[Dict[str, Any]] = []
    missing: List[str] = []
    for key in keys:
        item = next((x for x in groups if _matches_target(x, key)), None)
        if not item:
            missing.append(key)
            continue
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        group_id = str(raw.get("id") or item.get("id") or key)
        group_name = str(raw.get("name") or raw.get("groupName") or item.get("groupName") or item.get("nickname") or key)
        out.append({"id": group_id, "name": group_name})
    if missing:
        raise HTTPException(status_code=404, detail="Group not found. Refresh group list first: " + ", ".join(missing[:5]))
    return out


def _local_temp_url(temp_id: str) -> str:
    port = int(getattr(settings, "port", 8000) or 8000)
    return f"http://127.0.0.1:{port}/api/juhe-wechat/media/temp/{quote(temp_id)}"


class ConfigOnlyBody(BaseModel):
    config_id: int


class ContactDetailBody(ConfigOnlyBody):
    username: str = Field(min_length=1, max_length=256)


class ContactRemarkBody(ContactDetailBody):
    remark: str = Field(default="", max_length=120)


class SendMessageBody(ConfigOnlyBody):
    to_usernames: List[str] = Field(default_factory=list, max_length=100)
    target_type: Literal["contacts", "groups", "auto"] = "contacts"
    message_type: Literal["text", "image", "file"] = "text"
    content: Optional[str] = Field(default=None, max_length=4000)
    upload: Optional[Dict[str, Any]] = None


class FriendImportItem(BaseModel):
    contact: str = Field(min_length=1, max_length=128)
    remark: Optional[str] = Field(default=None, max_length=120)


class FriendRequestsBody(ConfigOnlyBody):
    verify_content: str = Field(default="", max_length=240)
    contacts: List[FriendImportItem] = Field(default_factory=list, max_length=100)


class RoomDetailBody(ConfigOnlyBody):
    room_username: str = Field(min_length=1, max_length=256)


class RoomCreateBody(ConfigOnlyBody):
    username_list: List[str] = Field(default_factory=list, max_length=80)


class RoomMembersBody(RoomDetailBody):
    username_list: List[str] = Field(default_factory=list, max_length=80)


class RoomRenameBody(RoomDetailBody):
    name: str = Field(default="", max_length=120)


class RoomAnnouncementBody(RoomDetailBody):
    announcement: str = Field(default="", max_length=2000)


class RoomDisplayNameBody(RoomDetailBody):
    display_name: str = Field(default="", max_length=120)


class AiReplyConfigBody(ConfigOnlyBody):
    enabled: bool = False
    memory_doc_ids: List[str] = Field(default_factory=list, max_length=20)
    knowledge: Optional[str] = Field(default=None, max_length=20000)
    prompt: Optional[str] = Field(default=None, max_length=4000)
    handoff_keywords: Optional[str] = Field(default=None, max_length=2000)
    cooldown_seconds: int = Field(default=8, ge=0, le=300)
    max_context: int = Field(default=12, ge=2, le=40)


class AiIncomingMessageBody(ConfigOnlyBody):
    contact_key: str = Field(min_length=1, max_length=256)
    contact_name: Optional[str] = Field(default=None, max_length=160)
    content: str = Field(min_length=1, max_length=8000)
    msg_type: str = Field(default="text", max_length=32)
    provider_msg_id: Optional[str] = Field(default=None, max_length=160)
    raw_payload: Optional[Dict[str, Any]] = None
    dry_run: bool = False


@router.get("/api/juhe-wechat/configs")
async def list_configs(current_user: _ServerUser = Depends(get_current_user_for_local)):
    try:
        configs = await _ensure_configs(current_user.id)
        return {"configs": configs, "server_default_ready": bool(configs), "upstream": "msghelper"}
    except HTTPException as exc:
        return {
            "configs": [],
            "server_default_ready": False,
            "upstream": "msghelper",
            "error": str(exc.detail),
        }


@router.post("/api/juhe-wechat/configs/{config_id}/status")
async def check_status(config_id: int, current_user: _ServerUser = Depends(get_current_user_for_local)):
    cid = _config_id(config_id)
    ctx = await _context(current_user.id, cid)
    online = bool(ctx.get("opened"))
    _append_log(
        current_user.id,
        config_id=cid,
        action="status",
        upstream_path="/api/get_opened_wxs",
        request_payload={"config_id": cid},
        response_payload={"opened": ctx.get("opened") or {}},
        success=online,
        error_message="" if online else "微信窗口未在线或未被 MsgHelper 识别",
    )
    return {
        "ok": online,
        "status": 2 if online else 0,
        "status_label": "在线" if online else "离线",
        "upstream": ctx,
    }


@router.post("/api/juhe-wechat/contacts/refresh")
async def refresh_contacts(body: ConfigOnlyBody, current_user: _ServerUser = Depends(get_current_user_for_local)):
    cid = _config_id(body.config_id)
    ctx = await _context(current_user.id, cid)
    creds = _msghelper_credentials()
    payload = {
        "userWxName": ctx["wx_name"],
        "userWxId": str(cid),
        "wxInstanceId": str(cid),
        "wxVersion": ctx["version"],
        "hwnd": ctx["hwnd"],
        "userId": creds["user_id"],
        "tenantId": creds["tenant_id"],
        "sleepStart": 0,
        "sleepEnd": 0.5,
        "specificName": "",
        "maxScrollTimes": 3,
        "syncType": "full",
        "strategy": "merge",
        "updateOptions": {"remark": True, "tags": True},
    }
    payload.update(_msghelper_identity_payload())
    result = await _call_msghelper(
        current_user.id,
        config_id=cid,
        action="contacts_refresh",
        method="POST",
        path="/api/get_contacts_v2",
        json_body=payload,
        timeout=120,
        raise_on_fail=False,
    )
    await asyncio.sleep(0.3)
    contacts = await _query_contacts(cid)
    groups: List[Dict[str, Any]] = []
    try:
        groups = await _query_groups(cid)
    except HTTPException:
        groups = []
    _save_contact_cache(current_user.id, cid, contacts=contacts, groups=groups)
    return {
        "ok": bool(result.get("ok")),
        "items": contacts + groups,
        "contacts": contacts,
        "groups": groups,
        "contact_count": len(contacts),
        "group_count": len(groups),
        "count": len(contacts) + len(groups),
        "upstream": result.get("upstream"),
    }


@router.get("/api/juhe-wechat/contacts/cache")
async def contact_cache(
    config_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    cid = _config_id(config_id)
    await _context(current_user.id, cid)
    cached = _load_contact_cache(current_user.id, cid)
    contacts = cached.get("contacts") if isinstance(cached.get("contacts"), list) else None
    groups = cached.get("groups") if isinstance(cached.get("groups"), list) else None
    if contacts is None or groups is None:
        if contacts is None:
            contacts = await _query_contacts(cid)
        if groups is None:
            try:
                groups = await _query_groups(cid)
            except HTTPException:
                groups = []
        _save_contact_cache(current_user.id, cid, contacts=contacts, groups=groups)
    return {
        "ok": True,
        "contacts": contacts or [],
        "groups": groups or [],
        "items": (contacts or []) + (groups or []),
        "contact_count": len(contacts or []),
        "group_count": len(groups or []),
        "count": len(contacts or []) + len(groups or []),
        "cached_at": cached.get("updated_at") or "",
    }


@router.post("/api/juhe-wechat/contacts/detail")
async def contact_detail(body: ContactDetailBody, current_user: _ServerUser = Depends(get_current_user_for_local)):
    cid = _config_id(body.config_id)
    contacts = await _query_contacts(cid)
    item = next((x for x in contacts if _matches_target(x, body.username)), None)
    if not item:
        raise HTTPException(status_code=404, detail="联系人不存在，请先刷新通讯录")
    _append_log(
        current_user.id,
        config_id=cid,
        action="contact_detail",
        upstream_path="/api/contacts",
        request_payload={"username": body.username},
        response_payload={"item": item},
        success=True,
    )
    return {"ok": True, "detail": item, "upstream": item}


@router.post("/api/juhe-wechat/contacts/remark")
async def modify_remark(body: ContactRemarkBody, current_user: _ServerUser = Depends(get_current_user_for_local)):
    cid = _config_id(body.config_id)
    ctx = await _context(current_user.id, cid)
    selected = await _selected_contacts(cid, [body.username])
    target = selected[0]
    payload = {
        "wxName": ctx["wx_name"],
        "wxVersion": ctx["version"],
        "hwnd": ctx["hwnd"],
        "updateRemark": True,
        "updateTags": False,
        "syncType": 0,
        "contacts": [{**target, "remark": body.remark, "updateRemark": True}],
        "optSleepTimeStart": 0.5,
        "optSleepTimeEnd": 1,
    }
    payload.update(_msghelper_identity_payload())
    result = await _call_msghelper(
        current_user.id,
        config_id=cid,
        action="modify_remark",
        method="POST",
        path="/api/update_contacts_v2",
        json_body=payload,
        timeout=90,
        raise_on_fail=False,
    )
    return {"ok": bool(result.get("ok")), "upstream": result.get("upstream")}


@router.post("/api/juhe-wechat/media/upload-file")
async def upload_file(
    config_id: int = Query(...),
    file_type: int = Query(default=2),
    file: UploadFile = File(...),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    cid = _config_id(config_id)
    await _context(current_user.id, cid)
    suffix = Path(file.filename or "file").suffix[:16]
    temp_id = f"{uuid.uuid4().hex}{suffix}"
    path = _TEMP_DIR / temp_id
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    path.write_bytes(data)
    url = _local_temp_url(temp_id)
    upload = {"url": url, "fileName": file.filename or temp_id}
    if int(file_type or 2) == 2:
        upload["msgImgUrl"] = url
    else:
        upload["msgFileUrl"] = url
    _append_log(
        current_user.id,
        config_id=cid,
        action="media_upload",
        upstream_path="/api/juhe-wechat/media/temp",
        request_payload={"filename": file.filename, "file_type": file_type},
        response_payload={"url": url},
        success=True,
    )
    return {"ok": True, "source": {"url": url, "filename": file.filename}, "upload": upload, "upstream": upload}


@router.get("/api/juhe-wechat/media/temp/{temp_id}", include_in_schema=False)
@router.head("/api/juhe-wechat/media/temp/{temp_id}", include_in_schema=False)
async def media_temp(temp_id: str):
    safe = Path(temp_id).name
    path = _TEMP_DIR / safe
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在或已过期")
    return FileResponse(path, filename=safe)


def _msghelper_send_record(
    log_id: str = "",
    *,
    task_name: str = "",
    created_after_ts: float = 0.0,
) -> Optional[Dict[str, Any]]:
    tasks: List[Dict[str, Any]] = []
    if log_id:
        tasks = _db_fetch_all("select * from wx_msg_send_task where id = ? limit 1", (log_id,))
    if not tasks and task_name:
        created_after = datetime.utcfromtimestamp(max(0.0, created_after_ts - 5)).strftime("%Y-%m-%d %H:%M:%S")
        tasks = _db_fetch_all(
            "select * from wx_msg_send_task where name = ? and created_time >= ? order by created_time desc limit 1",
            (task_name, created_after),
        )
    if not tasks:
        return None
    task = tasks[0]
    task_id = str(task.get("id") or log_id or "")
    details = _db_fetch_all("select * from wx_msg_send_detail where log_id = ? order by updated_at desc", (task_id,))
    planned = int(task.get("planned_total") or len(details) or 0)
    success_count = 0
    failed_count = 0
    for row in details:
        code = int(row.get("send_result_code") or 0)
        status = int(row.get("status") or 0)
        if code == 1 or status == 1:
            success_count += 1
        else:
            failed_count += 1
    if not details:
        status_text = str(task.get("status") or "").lower()
        if status_text == "success":
            success_count = planned
            failed_count = 0
        elif status_text == "failed":
            success_count = 0
            failed_count = planned
    return {
        "task": task,
        "details": details,
        "status": str(task.get("status") or ""),
        "planned_total": planned,
        "success_count": success_count,
        "failed_count": failed_count,
    }


def _wait_msghelper_send_record(
    log_id: str = "",
    *,
    task_name: str = "",
    created_after_ts: float = 0.0,
    timeout_seconds: float = 45.0,
) -> Optional[Dict[str, Any]]:
    deadline = time.time() + max(1.0, timeout_seconds)
    latest: Optional[Dict[str, Any]] = None
    while time.time() < deadline:
        latest = _msghelper_send_record(log_id, task_name=task_name, created_after_ts=created_after_ts)
        if latest and latest.get("status", "").lower() in {"success", "failed", "completed", "finish", "finished"}:
            return latest
        time.sleep(0.5)
    return latest


@router.post("/api/juhe-wechat/messages/send")
async def send_message(body: SendMessageBody, current_user: _ServerUser = Depends(get_current_user_for_local)):
    cid = _config_id(body.config_id)
    ctx = await _context(current_user.id, cid)
    names = [str(x).strip() for x in body.to_usernames if str(x).strip()]
    if not names:
        raise HTTPException(status_code=400, detail="请选择联系人")
    message: Dict[str, Any] = {"msg": "", "msgImgUrl": "", "msgFileUrl": "", "sendOrder": 0}
    if body.message_type == "text":
        content = (body.content or "").strip()
        if not content:
            raise HTTPException(status_code=400, detail="请输入发送内容")
        message["msg"] = content
    else:
        upload = body.upload or {}
        url = str(upload.get("msgImgUrl") or upload.get("msgFileUrl") or upload.get("url") or upload.get("source_url") or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="文件上传结果缺少 URL")
        if body.message_type == "image":
            message["msgImgUrl"] = url
        else:
            message["msgFileUrl"] = url
    target_type = body.target_type or "contacts"
    selected_contacts: List[Dict[str, Any]] = []
    selected_groups: List[Dict[str, Any]] = []
    select_mode = "contacts"
    if target_type == "groups":
        selected_groups = await _selected_groups(cid, names)
        select_mode = "groups"
    elif target_type == "auto":
        try:
            selected_groups = await _selected_groups(cid, names)
            select_mode = "groups" if len(selected_groups) == len(names) else "contacts"
        except HTTPException:
            selected_groups = []
            select_mode = "contacts"
        if select_mode == "contacts":
            selected_contacts = await _selected_contacts(cid, names)
    else:
        selected_contacts = await _selected_contacts(cid, names)
    targets = selected_groups if select_mode == "groups" else selected_contacts
    started_ts = time.time()
    operation_id = f"sendmsg-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    task_name = f"msghelper-{select_mode}-{body.message_type}-{uuid.uuid4().hex[:8]}"
    payload = {
        "name": task_name,
        "taskName": task_name,
        "userWxName": ctx["wx_name"],
        "wxInstanceId": str(cid),
        "wxVersion": ctx["version"],
        "hwnd": ctx["hwnd"],
        "selectMode": select_mode,
        "receiverMode": "include",
        "selectedContacts": selected_contacts,
        "messages": [message],
        "selectedTags": [],
        "selectedGroups": selected_groups,
        "logInfo": 1,
        "sendSleepTimeStart": 0.5,
        "sendSleepTimeEnd": 1.2,
        "optSleepTimeStart": 0.2,
        "optSleepTimeEnd": 0.8,
        "batchSize": 50,
        "batchSleep": 5,
        "contentType": 0,
        "storage": "localDB",
        "plannedUsage": len(targets),
        "operationId": operation_id,
        "confirmEvery": 500,
        "enableConfirm": 1,
        "checkDelete": 0,
        "relaxedMode": False,
    }
    payload.update(_msghelper_identity_payload())
    result = await _call_msghelper(
        current_user.id,
        config_id=cid,
        action=f"send_{select_mode}_{body.message_type}",
        method="POST",
        path="/api/sendmsg_v2",
        json_body=payload,
        timeout=180,
        raise_on_fail=False,
    )
    ok = bool(result.get("ok"))
    data = _unwrap_data(result.get("upstream")) if isinstance(result.get("upstream"), dict) else {}
    record = (
        await asyncio.to_thread(
            _wait_msghelper_send_record,
            "",
            task_name=task_name,
            created_after_ts=started_ts,
            timeout_seconds=45.0,
        )
        if ok
        else None
    )
    if record:
        status = str(record.get("status") or "").lower()
        ok = status == "success" and int(record.get("failed_count") or 0) == 0
        success_count = int(record.get("success_count") or 0)
        failed_count = int(record.get("failed_count") or 0)
    else:
        success_count = int((data or {}).get("successCount") or (len(targets) if ok else 0))
        failed_count = int((data or {}).get("failedCount") or (0 if ok else len(targets)))
    return {
        "ok": ok,
        "items": [{"ok": ok, "to_username": name, "upstream": result.get("upstream"), "record": record} for name in names],
        "success_count": success_count,
        "failed_count": failed_count,
        "log_id": str(((record or {}).get("task") or {}).get("id") or ""),
        "operation_id": operation_id,
        "record": record,
        "upstream": result.get("upstream"),
    }


@router.post("/api/juhe-wechat/friend-requests")
async def friend_requests(body: FriendRequestsBody, current_user: _ServerUser = Depends(get_current_user_for_local)):
    cid = _config_id(body.config_id)
    ctx = await _context(current_user.id, cid)
    creds = _msghelper_credentials()
    resources = []
    for idx, item in enumerate(body.contacts):
        contact = item.contact.strip()
        resources.append(
            {
                "id": hashlib.md5(contact.encode("utf-8")).hexdigest()[:16] or str(idx + 1),
                "keyword": contact,
                "nickname": item.remark or contact,
            }
        )
    if not resources:
        raise HTTPException(status_code=400, detail="请填写要添加的联系人")
    payload = {
        "taskName": "批量加好友",
        "userWxName": ctx["wx_name"],
        "wxInstanceId": str(cid),
        "userId": creds["user_id"],
        "tenantId": creds["tenant_id"],
        "wxVersion": ctx["version"],
        "hwnd": ctx["hwnd"],
        "selectedResources": resources,
        "applyMessage": body.verify_content or "你好",
        "tags": [],
        "permission": "朋友圈",
        "sleepTimeMin": 5,
        "sleepTimeMax": 8,
        "optSleepTimeStart": 0.5,
        "optSleepTimeEnd": 1,
        "stopIfTooManyRequests": True,
        "autoStart": True,
    }
    payload.update(_msghelper_identity_payload())
    result = await _call_msghelper(
        current_user.id,
        config_id=cid,
        action="friend_requests",
        method="POST",
        path="/api/friend_apply/create",
        json_body=payload,
        timeout=120,
        raise_on_fail=False,
    )
    ok = bool(result.get("ok"))
    return {
        "ok": ok,
        "items": [{"ok": ok, "to_username": r["keyword"], "error": "" if ok else _error_text(result.get("upstream"))} for r in resources],
        "success_count": len(resources) if ok else 0,
        "failed_count": 0 if ok else len(resources),
        "upstream": result.get("upstream"),
    }


@router.post("/api/juhe-wechat/rooms/list")
async def rooms_list(body: ConfigOnlyBody, current_user: _ServerUser = Depends(get_current_user_for_local)):
    cid = _config_id(body.config_id)
    ctx = await _context(current_user.id, cid)
    creds = _msghelper_credentials()
    payload = {
        "userWxName": ctx["wx_name"],
        "wxVersion": ctx["version"],
        "hwnd": ctx["hwnd"],
        "wxInstanceId": str(cid),
        "userId": creds["user_id"],
        "tenantId": creds["tenant_id"],
        "optSleepTimeStart": 0,
        "optSleepTimeEnd": 0.5,
    }
    payload.update(_msghelper_identity_payload())
    await _call_msghelper(
        current_user.id,
        config_id=cid,
        action="rooms_refresh",
        method="POST",
        path="/api/get_groups",
        json_body=payload,
        timeout=120,
        raise_on_fail=False,
    )
    await asyncio.sleep(0.2)
    groups = await _query_groups(cid)
    _save_contact_cache(current_user.id, cid, groups=groups)
    return {"ok": True, "items": groups, "count": len(groups)}


@router.post("/api/juhe-wechat/rooms/detail")
async def room_detail(body: RoomDetailBody, current_user: _ServerUser = Depends(get_current_user_for_local)):
    cid = _config_id(body.config_id)
    groups = await _query_groups(cid)
    item = next((x for x in groups if _matches_target(x, body.room_username)), None)
    if not item:
        raise HTTPException(status_code=404, detail="群不存在，请先刷新群列表")
    _append_log(
        current_user.id,
        config_id=cid,
        action="room_detail",
        upstream_path="/api/groups",
        request_payload={"room_username": body.room_username},
        response_payload={"item": item},
        success=True,
    )
    return {"ok": True, "detail": item, "upstream": item}


def _unsupported_room_operation(user_id: int, config_id: int, action: str) -> Dict[str, Any]:
    msg = "MsgHelper OpenAPI 未提供该群协议操作接口"
    _append_log(
        user_id,
        config_id=config_id,
        action=action,
        upstream_path="unsupported",
        request_payload={},
        response_payload={"message": msg},
        success=False,
        error_message=msg,
    )
    return {"ok": False, "upstream": {"message": msg}, "error": msg}


@router.post("/api/juhe-wechat/rooms/members")
async def room_members(body: RoomDetailBody, current_user: _ServerUser = Depends(get_current_user_for_local)):
    return _unsupported_room_operation(current_user.id, _config_id(body.config_id), "room_members")


@router.post("/api/juhe-wechat/rooms/create")
async def room_create(body: RoomCreateBody, current_user: _ServerUser = Depends(get_current_user_for_local)):
    return _unsupported_room_operation(current_user.id, _config_id(body.config_id), "room_create")


@router.post("/api/juhe-wechat/rooms/add-members")
async def room_add_members(body: RoomMembersBody, current_user: _ServerUser = Depends(get_current_user_for_local)):
    return _unsupported_room_operation(current_user.id, _config_id(body.config_id), "room_add_members")


@router.post("/api/juhe-wechat/rooms/invite-members")
async def room_invite_members(body: RoomMembersBody, current_user: _ServerUser = Depends(get_current_user_for_local)):
    return _unsupported_room_operation(current_user.id, _config_id(body.config_id), "room_invite_members")


@router.post("/api/juhe-wechat/rooms/rename")
async def room_rename(body: RoomRenameBody, current_user: _ServerUser = Depends(get_current_user_for_local)):
    return _unsupported_room_operation(current_user.id, _config_id(body.config_id), "room_rename")


@router.post("/api/juhe-wechat/rooms/announcement")
async def room_announcement(body: RoomAnnouncementBody, current_user: _ServerUser = Depends(get_current_user_for_local)):
    return _unsupported_room_operation(current_user.id, _config_id(body.config_id), "room_announcement")


@router.post("/api/juhe-wechat/rooms/display-name")
async def room_display_name(body: RoomDisplayNameBody, current_user: _ServerUser = Depends(get_current_user_for_local)):
    return _unsupported_room_operation(current_user.id, _config_id(body.config_id), "room_display_name")


def _ai_config(user_id: int, config_id: int) -> Dict[str, Any]:
    state = _read_state()
    key = _user_key(user_id)
    cfgs = state.setdefault("ai_config", {}).setdefault(key, {})
    cfg = cfgs.setdefault(
        str(config_id),
        {
            "auto_reply_enabled": False,
            "auto_reply_memory_doc_ids": [],
            "auto_reply_knowledge": "",
            "auto_reply_prompt": "",
            "auto_reply_handoff_keywords": "",
            "auto_reply_cooldown_seconds": 8,
            "auto_reply_max_context": 12,
        },
    )
    _write_state(state)
    return cfg


def _ai_messages(user_id: int) -> List[Dict[str, Any]]:
    state = _read_state()
    return state.setdefault("ai_messages", {}).setdefault(_user_key(user_id), [])


def _save_ai_message(user_id: int, item: Dict[str, Any]) -> Dict[str, Any]:
    state = _read_state()
    item = dict(item)
    item["id"] = _next_id(state, "next_ai_message_id")
    item.setdefault("created_at", _now_iso())
    item.setdefault("updated_at", item["created_at"])
    state.setdefault("ai_messages", {}).setdefault(_user_key(user_id), []).append(item)
    state["ai_messages"][_user_key(user_id)] = state["ai_messages"][_user_key(user_id)][-1000:]
    _write_state(state)
    return item


def _update_ai_message(user_id: int, message_id: int, patch: Dict[str, Any]) -> None:
    state = _read_state()
    rows = state.setdefault("ai_messages", {}).setdefault(_user_key(user_id), [])
    for row in rows:
        if int(row.get("id") or 0) == int(message_id):
            row.update(patch)
            row["updated_at"] = _now_iso()
            break
    _write_state(state)


@router.get("/api/juhe-wechat/ai-reply/config")
async def ai_reply_config(
    config_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    cid = _config_id(config_id)
    await _context(current_user.id, cid)
    cfg = _ai_config(current_user.id, cid)
    return {"ok": True, "config": {"id": cid, "knowledge": cfg.get("auto_reply_knowledge") or "", **cfg}}


@router.get("/api/juhe-wechat/ai-reply/memory-docs")
async def ai_reply_memory_docs(
    config_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    cid = _config_id(config_id)
    await _context(current_user.id, cid)
    selected = _ai_config(current_user.id, cid).get("auto_reply_memory_doc_ids") or []
    return {"ok": True, "selected_doc_ids": selected, "items": [], "count": 0}


@router.post("/api/juhe-wechat/ai-reply/config")
async def save_ai_reply_config(
    body: AiReplyConfigBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    cid = _config_id(body.config_id)
    await _context(current_user.id, cid)
    state = _read_state()
    key = _user_key(current_user.id)
    cfgs = state.setdefault("ai_config", {}).setdefault(key, {})
    cfgs[str(cid)] = {
        "auto_reply_enabled": bool(body.enabled),
        "auto_reply_memory_doc_ids": [str(x) for x in body.memory_doc_ids],
        "auto_reply_knowledge": (body.knowledge or "").strip(),
        "auto_reply_prompt": (body.prompt or "").strip(),
        "auto_reply_handoff_keywords": (body.handoff_keywords or "").strip(),
        "auto_reply_cooldown_seconds": int(body.cooldown_seconds or 8),
        "auto_reply_max_context": int(body.max_context or 12),
    }
    _write_state(state)
    return {"ok": True, "config": {"id": cid, "knowledge": cfgs[str(cid)]["auto_reply_knowledge"], **cfgs[str(cid)]}}


@router.get("/api/juhe-wechat/ai-reply/sessions")
async def ai_reply_sessions(
    config_id: int,
    limit: int = Query(default=80, ge=1, le=200),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    cid = _config_id(config_id)
    latest: Dict[str, Dict[str, Any]] = {}
    for msg in reversed(_ai_messages(current_user.id)):
        if int(msg.get("config_id") or 0) != cid:
            continue
        key = str(msg.get("contact_key") or "")
        if not key:
            continue
        if key in latest:
            latest[key]["message_count"] += 1
            continue
        latest[key] = {
            "contact_key": key,
            "contact_name": msg.get("contact_name") or "",
            "last_message": msg.get("content") or "",
            "last_direction": msg.get("direction") or "",
            "last_status": msg.get("status") or "",
            "last_at": msg.get("created_at") or "",
            "message_count": 1,
        }
        if len(latest) >= limit:
            break
    return {"ok": True, "items": list(latest.values()), "count": len(latest)}


@router.get("/api/juhe-wechat/ai-reply/messages")
async def ai_reply_messages(
    config_id: int,
    contact_key: str,
    limit: int = Query(default=80, ge=1, le=200),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    cid = _config_id(config_id)
    rows = [
        msg
        for msg in _ai_messages(current_user.id)
        if int(msg.get("config_id") or 0) == cid and str(msg.get("contact_key") or "") == contact_key
    ][-limit:]
    return {"ok": True, "items": rows, "count": len(rows)}


@router.post("/api/juhe-wechat/ai-reply/incoming")
async def ai_reply_incoming(
    body: AiIncomingMessageBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    cid = _config_id(body.config_id)
    await _context(current_user.id, cid)
    inbound = _save_ai_message(
        current_user.id,
        {
            "config_id": cid,
            "contact_key": body.contact_key.strip(),
            "contact_name": (body.contact_name or "").strip(),
            "provider_msg_id": body.provider_msg_id or "",
            "direction": "in",
            "msg_type": body.msg_type or "text",
            "content": body.content.strip(),
            "status": "received",
            "raw_payload": body.raw_payload or {},
        },
    )
    cfg = _ai_config(current_user.id, cid)
    reply = await get_customer_service_reply(
        body.content.strip(),
        company_info=cfg.get("auto_reply_knowledge") or "",
        common_phrases=cfg.get("auto_reply_prompt") or "",
    )
    out = _save_ai_message(
        current_user.id,
        {
            "config_id": cid,
            "contact_key": body.contact_key.strip(),
            "contact_name": (body.contact_name or "").strip(),
            "direction": "out",
            "msg_type": "text",
            "content": reply,
            "status": "dry_run" if body.dry_run else "pending_send",
            "reply_to_message_id": inbound["id"],
        },
    )
    send_result: Dict[str, Any] = {}
    if not body.dry_run:
        try:
            send_result = await send_message(
                SendMessageBody(config_id=cid, to_usernames=[body.contact_key], message_type="text", content=reply),
                current_user=current_user,
            )
            out["status"] = "sent" if send_result.get("ok") else "reply_send_failed"
            out["sent_payload"] = send_result
        except Exception as exc:
            out["status"] = "reply_send_failed"
            out["error_message"] = str(exc)
        _update_ai_message(current_user.id, int(out["id"]), out)
    return {"ok": out.get("status") in {"sent", "dry_run", "pending_send"}, "inbound": inbound, "outbound": out, "send": send_result}


@router.post("/api/juhe-wechat/ai-reply/messages/{message_id:int}/retry")
async def ai_reply_retry(message_id: int, current_user: _ServerUser = Depends(get_current_user_for_local)):
    target = next((msg for msg in _ai_messages(current_user.id) if int(msg.get("id") or 0) == int(message_id)), None)
    if not target:
        raise HTTPException(status_code=404, detail="消息不存在")
    result = await send_message(
        SendMessageBody(
            config_id=int(target.get("config_id") or 0),
            to_usernames=[str(target.get("contact_key") or "")],
            message_type="text",
            content=str(target.get("content") or ""),
        ),
        current_user=current_user,
    )
    _update_ai_message(
        current_user.id,
        int(message_id),
        {"status": "sent" if result.get("ok") else "reply_send_failed", "sent_payload": result},
    )
    return {"ok": bool(result.get("ok")), "send": result}


@router.get("/api/juhe-wechat/call-logs")
async def call_logs(
    config_id: Optional[int] = None,
    limit: int = Query(default=80, ge=1, le=200),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    logs = list((_read_state().get("logs") or {}).get(_user_key(current_user.id), []))
    if config_id:
        logs = [x for x in logs if int(x.get("config_id") or 0) == int(config_id)]
    return {"ok": True, "items": logs[:limit], "count": min(len(logs), limit)}
