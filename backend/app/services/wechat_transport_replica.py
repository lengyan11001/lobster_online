"""wechatauto-replica 传输层（Apache-2.0，vendor 在 backend/app/vendor/wechatauto）。

为什么需要它（2026-11-19 本机实测，微信 4.1.7.59）：
- wxauto4 的发送在 4.1.7+ 上无效：点发送后微信里没有这条消息（引擎报"未确认发出"）；
  replica 的 SendMsg 走真实输入 + 回车 + 数据库回读，同会话 2.1s、跨会话 13s，成功。
- wxauto4 的 ChatBox 在 UIA 点开会话后会脱离：ChatInfo 正常、GetAllMessage() 恒 0；
  replica 的 ChatWith + GetAllMessage 能读（50 条 0.4s）。
- replica 的 GetSession 0.1s 返回全部会话并带 unread 未读数，可替代"挨个点开"的扫描。

开关：LOBSTER_WECHAT_TRANSPORT=replica|wxauto4（默认 replica；import 失败自动回退）。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
_CLIENT: Any = None
_LOAD_ERROR = ""

_TRUTHY_DISABLE = {"0", "off", "false", "no", "wxauto4", "legacy", "disabled"}


def _vendor_path() -> str:
    # backend/app/services/wechat_transport_replica.py -> backend/app/vendor
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor")


def enabled() -> bool:
    flag = (os.environ.get("LOBSTER_WECHAT_TRANSPORT") or "replica").strip().lower()
    return flag not in _TRUTHY_DISABLE


def load_error() -> str:
    return _LOAD_ERROR


def _import_wechat():
    global _LOAD_ERROR
    vendor = _vendor_path()
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    try:
        from wechatauto import WeChat  # type: ignore
    except Exception as exc:  # pragma: no cover - 环境相关
        _LOAD_ERROR = str(exc)[:300]
        raise
    return WeChat


def client():
    global _CLIENT, _LOAD_ERROR
    with _LOCK:
        if _CLIENT is not None:
            return _CLIENT
        WeChat = _import_wechat()
        _CLIENT = WeChat(resize=False)
        return _CLIENT


def reset() -> None:
    global _CLIENT
    with _LOCK:
        _CLIENT = None


def _resp_field(resp: Any, key: str) -> str:
    if isinstance(resp, dict):
        return str(resp.get(key) or "")
    try:
        return str(getattr(resp, key, "") or "")
    except Exception:
        return ""


def send_text(peer: str, text: str) -> Dict[str, Any]:
    """发一条文本。返回 {ok, status, raw}；不吞异常（调用方决定是否重试）。"""
    with _LOCK:
        wx = client()
        resp = wx.SendMsg(str(text), who=str(peer), exact=True)
    status = _resp_field(resp, "status")
    ok = status == "成功" or status.lower() in {"ok", "success"} or bool(_resp_field(resp, "ok") == "True")
    return {
        "ok": bool(ok),
        "status": status,
        "driver": "wechatauto_replica",
        "peer_id": str(peer),
        "raw": str(resp)[:300],
    }


def list_sessions() -> List[Dict[str, Any]]:
    """全部会话 + 未读数（实测 50 条 0.1s）。"""
    with _LOCK:
        wx = client()
        items = wx.GetSession() or []
    out: List[Dict[str, Any]] = []
    for item in items:
        try:
            name = str(getattr(item, "name", "") or "").strip()
            unread = int(getattr(item, "unread", 0) or 0)
        except Exception:
            continue
        if name:
            out.append({"name": name, "unread": unread})
    return out


def read_messages(peer: str, *, max_messages: int = 50) -> Dict[str, Any]:
    """打开会话并读最近消息（返回纯 dict，可直接喂给引擎的 _persist_message_obj）。"""
    with _LOCK:
        wx = client()
        wx.ChatWith(str(peer), exact=True)
        msgs = list(wx.GetAllMessage() or [])
        info: Dict[str, Any] = {}
        try:
            info = dict(wx.ChatInfo() or {})
        except Exception:
            pass
    raw: List[Dict[str, Any]] = []
    for msg in msgs[-max(1, int(max_messages)):]:
        item: Dict[str, Any] = {}
        for attr in ("type", "content", "sender", "id", "hash", "hash_text", "attr", "time"):
            try:
                item[attr] = getattr(msg, attr)
            except Exception:
                continue
        if item:
            raw.append(item)
    return {"ok": True, "chat_info": info, "messages": raw}
