"""replica 传输层（wechatauto-replica，Apache-2.0，vendor 在 backend/app/vendor/wechatauto）。

2026-11-19 关键结论：replica 和 wxauto4 **不能在同一进程里同时驱动同一个微信窗口**，
实测会互相锁死（wxauto4 抛 I/O operation on closed file / last_step=scan:client_cached 卡 45s）。
所以 replica 跑在**独立子进程**（replica_worker.py），主进程只发 JSON 行请求：
两边各自持有 COM/UIA，互不干扰；sidecar 崩了只重启它。

只接管三类操作：会话列表（带未读）、读消息、发消息。
通讯录/群成员/发文件/朋友圈/诊断仍然走 wxauto4，不受影响。

开关：LOBSTER_WECHAT_TRANSPORT=replica|wxauto4（默认 replica；启动失败自动回落 wxauto4）。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
_PROC: Optional[subprocess.Popen] = None
_SEQ = 0
_LAST_START_AT = 0.0
_START_FAILURES = 0

_DISABLE = {"0", "off", "false", "no", "wxauto4", "legacy", "disabled"}


def enabled() -> bool:
    flag = (os.environ.get("LOBSTER_WECHAT_TRANSPORT") or "replica").strip().lower()
    return flag not in _DISABLE


def _worker_script() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "replica_worker.py")


def _ensure_proc() -> subprocess.Popen:
    global _PROC, _LAST_START_AT, _START_FAILURES
    if _PROC is not None and _PROC.poll() is None:
        return _PROC
    now = time.time()
    if _START_FAILURES >= 3 and now - _LAST_START_AT < 120:
        raise RuntimeError("replica sidecar 连续启动失败，暂停 120s")
    _LAST_START_AT = now
    creation = 0
    if os.name == "nt":
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        [sys.executable, _worker_script()],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=os.path.dirname(_worker_script()),
        creationflags=creation,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    _PROC = proc
    return proc


def _call(op: str, timeout: float = 120.0, **payload: Any) -> Dict[str, Any]:
    global _SEQ, _PROC, _START_FAILURES
    with _LOCK:
        proc = _ensure_proc()
        _SEQ += 1
        rid = _SEQ
        req = {"id": rid, "op": op, **payload}
        try:
            assert proc.stdin and proc.stdout
            proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except Exception:
            _PROC = None
            raise
        # readline() 会一直阻塞，不能靠循环里的 deadline 判断；放到线程里 join(timeout)。
        box: Dict[str, Any] = {}

        def _reader() -> None:
            try:
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        res = json.loads(line)
                    except Exception:
                        continue
                    if int(res.get("id") or 0) == rid:
                        box.update(res)
                        break
            except Exception:
                pass

        waiter = threading.Thread(target=_reader, daemon=True)
        waiter.start()
        waiter.join(max(1.0, float(timeout)))
        if not box:
            reset()
            raise TimeoutError("replica sidecar 超时（已重置，下一轮重启）")
        _START_FAILURES = 0
        return box


def reset() -> None:
    global _PROC
    with _LOCK:
        if _PROC is not None and _PROC.poll() is None:
            try:
                _PROC.kill()
            except Exception:
                pass
        _PROC = None


_MY_NICKNAME = ""


def my_nickname() -> str:
    global _MY_NICKNAME
    if _MY_NICKNAME:
        return _MY_NICKNAME
    res = _call("me", timeout=30.0)
    _MY_NICKNAME = str(res.get("nickname") or "").strip()
    return _MY_NICKNAME


def list_sessions() -> List[Dict[str, Any]]:
    res = _call("sessions", timeout=60.0)
    if not res.get("ok"):
        raise RuntimeError(str(res.get("error") or "sessions failed")[:300])
    return list(res.get("sessions") or [])


def groups() -> Dict[str, Any]:
    res = _call("groups", timeout=60.0)
    if not res.get("ok"):
        raise RuntimeError(str(res.get("error") or "groups failed")[:300])
    return res


def group_members(chatroom: str) -> Dict[str, Any]:
    res = _call("group_members", timeout=60.0, chatroom=str(chatroom))
    if not res.get("ok"):
        raise RuntimeError(str(res.get("error") or "group_members failed")[:300])
    return res


def drafts(limit: int = 50) -> Dict[str, Any]:
    res = _call("drafts", timeout=20.0, max=int(limit))
    if not res.get("ok"):
        raise RuntimeError(str(res.get("error") or "drafts failed")[:300])
    return res


def contacts(limit: int = 5000) -> Dict[str, Any]:
    res = _call("contacts", timeout=90.0, max=int(limit))
    if not res.get("ok"):
        raise RuntimeError(str(res.get("error") or "contacts failed")[:300])
    return res


def friend_requests(limit: int = 50) -> Dict[str, Any]:
    res = _call("friend_requests", timeout=60.0, max=int(limit))
    if not res.get("ok"):
        raise RuntimeError(str(res.get("error") or "friend_requests failed")[:300])
    return res


def sessions_db(limit: int = 200) -> Dict[str, Any]:
    res = _call("sessions_db", timeout=60.0, max=int(limit))
    if not res.get("ok"):
        raise RuntimeError(str(res.get("error") or "sessions_db failed")[:300])
    return res


def history(peer_username: str, *, limit: int = 20, force: bool = False) -> Dict[str, Any]:
    """从本地消息库读最近 N 条（跨分片、按时间倒序、带发送者），不走界面。"""
    res = _call("history", timeout=90.0, peer=str(peer_username), max=int(limit), force=bool(force))
    if not res.get("ok"):
        raise RuntimeError(str(res.get("error") or "history failed")[:300])
    return res


def read_messages(peer: str, *, max_messages: int = 50) -> Dict[str, Any]:
    res = _call("read", timeout=30.0, peer=str(peer), max=int(max_messages))
    if not res.get("ok"):
        raise RuntimeError(str(res.get("error") or "read failed")[:300])
    return res


def send_text(peer: str, text: str) -> Dict[str, Any]:
    res = _call("send", timeout=180.0, peer=str(peer), text=str(text))
    return {
        "ok": bool(res.get("ok")),
        "status": str(res.get("status") or ""),
        "driver": "wechatauto_replica",
        "peer_id": str(peer),
        "raw": str(res.get("raw") or res.get("error") or "")[:300],
    }
