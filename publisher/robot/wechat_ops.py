"""微信操作：每个 Op 都先过 surface 闸门，再用快照差异验证。"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from publisher.agent.desktop import (
    _chat_input,
    _find_control,
    activate_window,
    find_sns_window,
    find_window,
)
from . import surface as S
from .ops import Result, _ensure_surface, spec


def _win(window: Any = None) -> Any:
    return window or find_window()


@spec(name="wechat.list_sessions", platform="wechat", surface=S.WECHAT_CHAT,
      summary="列出左侧会话名与预览", precondition="当前在聊天页",
      postcondition="条目来自真实会话列 ListItem", readonly=True)
def list_sessions(window: Any = None, limit: int = 20) -> Result:
    res = Result(op="wechat.list_sessions")
    win = _win(window)
    if win is None:
        res.layer, res.error = "precondition", "wechat_window_not_found"
        return res
    activate_window(win)
    snap = _ensure_surface(win, S.WECHAT_CHAT, res)
    if snap is None:
        return res
    if not snap.items:
        res.layer, res.error = "precondition", "session_list_empty"
        return res
    res.after = snap.to_dict()
    res.ok = True
    res.evidence["items"] = [{"name": i.split(" ")[0], "preview": i[:70]} for i in snap.items[:limit]]
    return res


@spec(name="wechat.open_chat", platform="wechat", surface=S.WECHAT_CHAT,
      summary="切到指定会话", precondition="当前在聊天页",
      postcondition="快照 anchors.chat == 目标名", params=["name"])
def open_chat(name: str, window: Any = None, timeout: float = 8.0) -> Result:
    res = Result(op="wechat.open_chat")
    target = str(name or "").strip()
    if not target:
        res.layer, res.error = "precondition", "empty_target"
        return res
    win = _win(window)
    if win is None:
        res.layer, res.error = "precondition", "wechat_window_not_found"
        return res
    activate_window(win)
    snap = _ensure_surface(win, S.WECHAT_CHAT, res)
    if snap is None:
        return res
    if snap.anchors.get("chat") == target:
        res.after = snap.to_dict()
        res.ok = True
        res.evidence["already_open"] = True
        return res
    # 可视行
    for item in snap.items:
        if item.startswith(target):
            try:
                from publisher.agent.desktop import _iter_controls, _visible

                for c, info in _iter_controls(win):
                    try:
                        if str(info.control_type or "") != "ListItem" or not _visible(c):
                            continue
                        if " ".join(str(info.name or "").split()) == item:
                            c.click_input()
                            break
                    except Exception:
                        continue
            except Exception:
                pass
            break
    deadline = time.monotonic() + max(3.0, float(timeout))
    while time.monotonic() < deadline:
        after = S.snapshot_desktop(win)
        if after.anchors.get("chat") == target:
            res.after = after.to_dict()
            res.ok = True
            res.evidence["via"] = "session_row" if snap.items else "unknown"
            return res
        time.sleep(0.3)
    # 搜索 + 回车
    box = _find_control(win, "搜索", region="sessions") or _find_control(win, "搜索")
    if box is not None:
        try:
            from pywinauto import keyboard

            box.click_input()
            keyboard.send_keys("^a")
            keyboard.send_keys("{DELETE}")
            keyboard.send_keys(target, with_spaces=True)
            time.sleep(1.5)
            keyboard.send_keys("{ENTER}")
        except Exception:
            pass
        deadline = time.monotonic() + max(3.0, float(timeout))
        while time.monotonic() < deadline:
            after = S.snapshot_desktop(win)
            if after.anchors.get("chat") == target:
                res.after = after.to_dict()
                res.ok = True
                res.evidence["via"] = "search_enter"
                return res
            time.sleep(0.3)
    after = S.snapshot_desktop(win)
    res.after = after.to_dict()
    res.layer, res.error = "verify", "chat_name_mismatch"
    return res


@spec(name="wechat.read_recent", platform="wechat", surface=S.WECHAT_CHAT,
      summary="读当前会话最近消息", precondition="当前在聊天页且已打开会话",
      postcondition="消息来自聊天区 ListItem", readonly=True, params=["limit"])
def read_recent(limit: int = 8, window: Any = None) -> Result:
    res = Result(op="wechat.read_recent")
    win = _win(window)
    if win is None:
        res.layer, res.error = "precondition", "wechat_window_not_found"
        return res
    activate_window(win)
    snap = _ensure_surface(win, S.WECHAT_CHAT, res)
    if snap is None:
        return res
    if not snap.anchors.get("chat"):
        res.layer, res.error = "precondition", "no_chat_open"
        return res
    msgs = list(snap.anchors.get("messages") or [])
    if not msgs:
        res.layer, res.error = "precondition", "no_messages_visible"
        return res
    res.after = snap.to_dict()
    res.ok = True
    res.evidence.update({"chat": snap.anchors.get("chat"), "messages": msgs[-limit:]})
    return res


@spec(name="wechat.send_message", platform="wechat", surface=S.WECHAT_CHAT,
      summary="在已确认的会话里发一条文本", precondition="当前会话 == 目标名",
      postcondition="快照 messages 新增该文本", destructive=True, params=["text", "to"])
def send_message(text: str, to: str, window: Any = None, timeout: float = 8.0) -> Result:
    res = Result(op="wechat.send_message")
    body = str(text or "")
    if not body.strip():
        res.layer, res.error = "precondition", "empty_text"
        return res
    win = _win(window)
    if win is None:
        res.layer, res.error = "precondition", "wechat_window_not_found"
        return res
    activate_window(win)
    snap = _ensure_surface(win, S.WECHAT_CHAT, res)
    if snap is None:
        return res
    chat = str(snap.anchors.get("chat") or "")
    target = str(to or "").strip()
    if not chat or (target and not chat.startswith(target)):
        res.layer, res.error = "precondition", "wrong_chat"
        res.evidence.update({"current_chat": chat, "expected": target})
        return res
    control = _chat_input(win)
    if control is None:
        res.layer, res.error = "precondition", "chat_input_not_found"
        return res
    before_msgs = set(snap.anchors.get("messages") or [])
    try:
        from pywinauto import keyboard

        control.click_input()
        keyboard.send_keys("^a")
        keyboard.send_keys("{DELETE}")
        from publisher.agent.desktop import type_text

        if not type_text(body):
            res.layer, res.error = "action", "paste_failed"
            return res
        keyboard.send_keys("{ENTER}")
    except Exception as exc:
        res.layer, res.error = "action", f"type_failed:{type(exc).__name__}"
        return res
    probe = body.strip()[:12]
    item_probe = body.strip()[:10]
    deadline = time.monotonic() + max(6.0, float(timeout))
    while time.monotonic() < deadline:
        time.sleep(0.4)
        after = S.snapshot_desktop(win)
        msgs = after.anchors.get("messages") or []
        new_msg = [m for m in msgs if m not in before_msgs and probe in m]
        # 兜底证据：会话列那一行的预览也会变成刚发的文本（真机 22:13 那条就是这样）
        row_hit = [it for it in after.items if it.startswith(chat[:6]) and item_probe in it]
        if new_msg or row_hit:
            res.after = after.to_dict()
            res.ok = True
            res.evidence["matched"] = (new_msg[-1] if new_msg else row_hit[0])[:90]
            res.evidence["matched_via"] = "chat_message" if new_msg else "session_preview"
            return res
    res.after = S.snapshot_desktop(win).to_dict()
    res.layer, res.error = "verify", "message_not_found"
    return res


@spec(name="wechat.pending_friend_requests", platform="wechat", surface=S.WECHAT_FRIEND_REQUESTS,
      summary="只读枚举好友申请", precondition="当前停在「新的朋友」页",
      postcondition="条目来自该页 ListItem", readonly=True)
def pending_friend_requests(window: Any = None) -> Result:
    res = Result(op="wechat.pending_friend_requests")
    win = _win(window)
    if win is None:
        res.layer, res.error = "precondition", "wechat_window_not_found"
        return res
    activate_window(win)
    snap = _ensure_surface(win, S.WECHAT_FRIEND_REQUESTS, res)
    if snap is None:
        return res
    rows = snap.items
    pending = [r for r in rows if "已添加" not in r]
    res.after = snap.to_dict()
    res.ok = True
    res.evidence.update({"total": len(rows), "pending": len(pending),
                         "items": [{"text": r[:60], "status": "accepted" if "已添加" in r else "pending"}
                                   for r in rows[:20]]})
    return res


@spec(name="wechat.moments_feed", platform="wechat", surface=S.WECHAT_MOMENTS,
      summary="只读列朋友圈动态", precondition="朋友圈窗口已打开",
      postcondition="条目来自 SNS 窗口 ListItem", readonly=True, params=["limit"])
def moments_feed(limit: int = 10, window: Any = None) -> Result:
    res = Result(op="wechat.moments_feed")
    win = window or find_sns_window()
    if win is None:
        res.layer, res.error = "precondition", "moments_window_not_found"
        return res
    activate_window(win)
    snap = _ensure_surface(win, S.WECHAT_MOMENTS, res)
    if snap is None:
        return res
    if not snap.items:
        res.layer, res.error = "precondition", "no_posts_visible"
        return res
    res.after = snap.to_dict()
    res.ok = True
    res.evidence.update({"count": len(snap.items), "posts": snap.items[:limit]})
    return res
