"""replica sidecar：在独立进程里跑 wechatauto-replica，避免和主进程的 wxauto4 抢 UIA/COM。

协议：stdin/stdout 各一行 JSON。
  请求  {"id":1,"op":"sessions"}
        {"id":2,"op":"read","peer":"张三","max":50}
        {"id":3,"op":"send","peer":"张三","text":"..."}
  响应  {"id":1,"ok":true,...} / {"id":1,"ok":false,"error":"..."}
日志只写 stderr，绝不污染 stdout。
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor"))

_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        from wechatauto import WeChat  # type: ignore

        _CLIENT = WeChat(resize=False)
    return _CLIENT



def _sessions_db(limit: int = 200):
    db = _db()
    info = db.get_self_info() or {}
    self_name = str(info.get("username") or "")
    out = []
    for row in db.get_sessions(int(limit)):
        u = str(row.get("username") or "").strip()
        if not u:
            continue
        name = ""
        try:
            name = str(db.get_nickname(u) or "").strip()
        except Exception:
            name = ""
        out.append({
            "username": u,
            "name": name or u,
            "unread": int(row.get("unread") or 0),
            "summary": str(row.get("summary") or ""),
            "last_time": row.get("last_time"),
            "last_sender": str(row.get("last_sender") or ""),
        })
    return {"ok": True, "self": self_name, "sessions": out}

def _me():
    wx = _client()
    name = ""
    try:
        name = str(getattr(wx, "nickname", "") or "").strip()
    except Exception:
        name = ""
    return {"ok": True, "nickname": name, "username": str((_db().get_self_info() or {}).get("username") or "")}


def _sessions():
    wx = _client()
    out = []
    for item in wx.GetSession() or []:
        try:
            name = str(getattr(item, "name", "") or "").strip()
            unread = int(getattr(item, "unread", 0) or 0)
        except Exception:
            continue
        if name:
            out.append(
                {
                    "name": name,
                    "unread": unread,
                    "username": str(getattr(item, "username", "") or "").strip(),
                    "summary": str(getattr(item, "summary", "") or "").strip(),
                    "last_time": str(getattr(item, "last_time", "") or "").strip(),
                }
            )
    return {"ok": True, "sessions": out}


def _scroll_chat_to_bottom(times: int = 6) -> None:
    """把聊天记录滚到最底部：replica 的 GetAllMessage 只读当前渲染出来的那一屏，"""
    """不滚到底就会一直读到靠上的旧消息（新消息永远看不到）。"""
    try:
        import time as _time
        import win32api
        import win32con
        import win32gui
        wx = _client()
        hwnd = int(getattr(wx, "HWND", 0) or 0)
        if not hwnd:
            ctrl = getattr(wx, "control", None)
            hwnd = int(getattr(ctrl, "NativeWindowHandle", 0) or 0)
        if not hwnd:
            return
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        x = left + int((right - left) * 0.62)
        y = top + int((bottom - top) * 0.55)
        win32api.SetCursorPos((x, y))
        _time.sleep(0.15)
        for _ in range(max(1, int(times))):
            win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, -1200, 0)
            _time.sleep(0.18)
    except Exception:
        return


_DB = None
_TYPE_MAP = {"文本": "text", "图片": "image", "语音": "voice", "视频": "video", "文件": "file", "链接": "link"}


def _db():
    global _DB
    if _DB is None:
        from wechatauto.db import WeChatDB
        _DB = WeChatDB()
    return _DB


def _history(peer_username: str, limit: int = 20):
    db = _db()
    self_name = str((db.get_self_info() or {}).get("username") or "")
    rows = db.get_messages(str(peer_username), limit=max(1, int(limit)))
    out = []
    for m in rows:
        sender = str(m.get("sender_username") or "")
        out.append({
            "type": _TYPE_MAP.get(str(m.get("type") or ""), "text"),
            "content": str(m.get("content") or ""),
            "attr": "self" if sender and sender == self_name else "friend",
            "sender": sender,
            "id": "%s:%s" % (m.get("shard") or "", m.get("local_id")),
            "hash": "%s:%s" % (m.get("shard") or "", m.get("local_id")),
            "time": "",
            "create_time": m.get("create_time"),
        })
    return {"ok": True, "self": self_name, "messages": out}

def _read(peer: str, max_messages: int = 50):
    wx = _client()
    current = ""
    try:
        current = str((wx.ChatInfo() or {}).get("chat_name") or "").strip()
    except Exception:
        current = ""
    # 当前已经在这个会话上时不要再搜一次（ChatWith 一次约 11s）。
    if current != str(peer or "").strip():
        wx.ChatWith(peer, exact=True)
    msgs = list(wx.GetAllMessage() or [])
    info = {}
    try:
        info = dict(wx.ChatInfo() or {})
    except Exception:
        pass
    raw = []
    for msg in msgs[-max(1, int(max_messages)):]:
        item = {}
        for attr in ("type", "content", "sender", "id", "hash", "hash_text", "attr", "time"):
            try:
                item[attr] = getattr(msg, attr)
            except Exception:
                continue
        if item:
            raw.append(item)
    return {"ok": True, "chat_info": info, "messages": raw}


def _send(peer: str, text: str):
    wx = _client()
    resp = wx.SendMsg(str(text), who=str(peer), exact=True)
    status = ""
    if isinstance(resp, dict):
        status = str(resp.get("status") or "")
    else:
        status = str(getattr(resp, "status", "") or "")
    ok = status == "成功" or status.lower() in {"ok", "success"}
    return {"ok": ok, "status": status, "raw": str(resp)[:300]}


def main() -> int:
    # 2026-11-19：Windows 下管道默认用 GBK，UTF-8 的中文名字会被解成"鍐涗簤"这种乱码，
    # 然后拿乱码去搜索会话。这里强制 stdin/stdout 都用 UTF-8。
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        rid = req.get("id")
        op = str(req.get("op") or "")
        try:
            if op == "ping":
                res = {"ok": True}
            elif op == "sessions_db":
                res = _sessions_db(int(req.get("max") or 200))
            elif op == "me":
                res = _me()
            elif op == "sessions":
                res = _sessions()
            elif op == "history":
                res = _history(str(req.get("peer") or ""), int(req.get("max") or 20))
            elif op == "read":
                res = _read(str(req.get("peer") or ""), int(req.get("max") or 50))
            elif op == "send":
                res = _send(str(req.get("peer") or ""), str(req.get("text") or ""))
            else:
                res = {"ok": False, "error": "unknown op: " + op}
        except Exception as exc:
            res = {"ok": False, "error": str(exc)[:300], "trace": traceback.format_exc()[-800:]}
        res["id"] = rid
        sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
