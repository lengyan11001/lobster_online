"""登录后持久化 JWT + 安装槽位，供 OpenClaw 微信等渠道调用速推代理 / MCP 时使用（无网页 chat 会话时）。"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from fastapi import Request
from sqlalchemy.orm import Session

from ..models import User

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent


def _channel_file() -> Path:
    return _project_root() / "openclaw" / ".channel_fallback.json"


def channel_fallback_path_for_logs() -> str:
    """供日志打印：本机 OpenClaw 读此路径（须与写入 persist 一致）。"""
    try:
        return str(_channel_file().resolve())
    except Exception:
        return str(_channel_file())


def read_channel_fallback() -> Tuple[Optional[str], Optional[str]]:
    """读取上次登录写入的凭证。返回 (jwt, installation_id)。"""
    path = _channel_file()
    if not path.is_file():
        return None, None
    try:
        with _lock:
            raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        jwt = (data.get("jwt") or "").strip()
        xi = (data.get("installation_id") or "").strip() or None
        if not jwt:
            return None, None
        return jwt, xi
    except Exception as e:
        logger.warning("read_channel_fallback failed: %s", e)
        return None, None


def persist_channel_fallback_for_login(
    *,
    jwt_token: str,
    request: Optional[Request] = None,
    installation_id: Optional[str] = None,
    user_id: Optional[int] = None,
    db: Optional[Session] = None,
) -> None:
    """登录/注册成功后调用：写入 openclaw/.channel_fallback.json（原子替换）。

    安装槽位：优先请求头 X-Installation-Id；若无则使用该用户上次保存在 users.client_installation_id 的值
    （服务号登录等无浏览器头时可复用曾用网页登录过的槽位）。不要求用户改 .env。
    """
    from ..core.config import get_settings

    if not getattr(get_settings(), "openclaw_persist_channel_token_on_login", True):
        return
    token = (jwt_token or "").strip()
    if not token:
        return
    xi = (installation_id or "").strip() or None
    if request is not None:
        hdr = (request.headers.get("X-Installation-Id") or request.headers.get("x-installation-id") or "").strip()
        if hdr:
            xi = hdr

    if user_id is not None and db is not None:
        user = db.query(User).filter(User.id == user_id).first()
        if user is not None:
            if xi:
                prev = (getattr(user, "client_installation_id", None) or "").strip()
                if prev != xi:
                    try:
                        user.client_installation_id = xi
                        db.add(user)
                        db.commit()
                    except Exception as e:
                        logger.warning("persist client_installation_id commit failed: %s", e)
                        db.rollback()
            else:
                saved = (getattr(user, "client_installation_id", None) or "").strip()
                if saved:
                    xi = saved
    path = _channel_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "jwt": token,
        "installation_id": xi,
        "user_id": user_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(".json.tmp")
    try:
        with _lock:
            tmp.write_text(text, encoding="utf-8")
            os.replace(str(tmp), str(path))
        logger.info(
            "openclaw channel fallback persisted user_id=%s has_installation_id=%s",
            user_id,
            bool(xi),
        )
    except OSError as e:
        logger.warning("persist_channel_fallback_for_login failed: %s", e)
        try:
            if tmp.is_file():
                tmp.unlink(missing_ok=True)
        except OSError:
            pass


def weixin_openclaw_peers_path_for_logs() -> str:
    try:
        return str((_project_root() / "openclaw" / ".weixin_openclaw_peers.json").resolve())
    except Exception:
        return str(_project_root() / "openclaw" / ".weixin_openclaw_peers.json")


def _weixin_peers_file() -> Path:
    return _project_root() / "openclaw" / ".weixin_openclaw_peers.json"


def read_weixin_peer_auth(weixin_user_id: str) -> Tuple[Optional[str], Optional[str]]:
    """微信渠道（OpenClaw 插件）好友 ID → (jwt, installation_id)。"""
    wid = (weixin_user_id or "").strip()
    if not wid:
        return None, None
    path = _weixin_peers_file()
    if not path.is_file():
        return None, None
    try:
        with _lock:
            raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        peers = data.get("peers")
        if not isinstance(peers, dict):
            return None, None
        entry = peers.get(wid)
        if not isinstance(entry, dict):
            return None, None
        jwt = (entry.get("jwt") or "").strip()
        xi = (entry.get("installation_id") or "").strip() or None
        if not jwt:
            return None, None
        return jwt, xi
    except Exception as e:
        logger.warning("read_weixin_peer_auth failed: %s", e)
        return None, None


def persist_weixin_openclaw_peer_for_user(
    *,
    weixin_user_id: str,
    jwt_token: str,
    request: Optional[Request] = None,
    installation_id: Optional[str] = None,
    user_id: Optional[int] = None,
    db: Optional[Session] = None,
) -> None:
    """将当前登录用户的 JWT 绑定到微信助手侧的好友 ID（from_user_id），供 mcp-gateway / 速推代理按人扣费。"""
    from ..core.config import get_settings

    if not getattr(get_settings(), "openclaw_persist_channel_token_on_login", True):
        return
    wid = (weixin_user_id or "").strip()
    token = (jwt_token or "").strip()
    if not wid or not token:
        return
    xi = (installation_id or "").strip() or None
    if request is not None:
        hdr = (request.headers.get("X-Installation-Id") or request.headers.get("x-installation-id") or "").strip()
        if hdr:
            xi = hdr
    if user_id is not None and db is not None:
        user = db.query(User).filter(User.id == user_id).first()
        if user is not None:
            if xi:
                prev = (getattr(user, "client_installation_id", None) or "").strip()
                if prev != xi:
                    try:
                        user.client_installation_id = xi
                        db.add(user)
                        db.commit()
                    except Exception as e:
                        logger.warning("persist weixin peer: client_installation_id commit failed: %s", e)
                        db.rollback()
            else:
                saved = (getattr(user, "client_installation_id", None) or "").strip()
                if saved:
                    xi = saved
    path = _weixin_peers_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    peers: dict = {}
    if path.is_file():
        try:
            with _lock:
                prev = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(prev, dict) and isinstance(prev.get("peers"), dict):
                peers = dict(prev["peers"])
        except Exception:
            peers = {}
    peers[wid] = {
        "jwt": token,
        "installation_id": xi,
        "user_id": user_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    payload = {"peers": peers, "updated_at": datetime.now(timezone.utc).isoformat()}
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(".json.tmp")
    try:
        with _lock:
            tmp.write_text(text, encoding="utf-8")
            os.replace(str(tmp), str(path))
        logger.info("weixin openclaw peer persisted weixin_user_id=%s user_id=%s", wid, user_id)
    except OSError as e:
        logger.warning("persist_weixin_openclaw_peer_for_user failed: %s", e)
        try:
            if tmp.is_file():
                tmp.unlink(missing_ok=True)
        except OSError:
            pass
