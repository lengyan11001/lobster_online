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


@spec(name="wechat.moments_publish", platform="wechat", surface=S.WECHAT_MOMENTS,
      summary="打开发表入口（dry_run 只验证入口可达并安全退出）",
      precondition="朋友圈窗口已打开", postcondition="入口可达 + dry_run 后动态未变",
      destructive=True, params=["text", "dry_run"])
def moments_publish(text: str = "", dry_run: bool = True, window: Any = None) -> Result:
    """微信 4.x 真机行为：点「发表」直接弹系统「选择文件」框（先选图，再写文案）。
    所以这里先只做“入口可达 + 安全退出”的验证，不做真实发布。"""
    res = Result(op="wechat.moments_publish")
    win = window or find_sns_window()
    if win is None:
        res.layer, res.error = "precondition", "moments_window_not_found"
        return res
    activate_window(win)
    before = _ensure_surface(win, S.WECHAT_MOMENTS, res)
    if before is None:
        return res
    btn = _find_control(win, "发表")
    if btn is None:
        res.layer, res.error = "precondition", "publish_entry_not_found"
        return res
    try:
        btn.click_input()
    except Exception as exc:
        res.layer, res.error = "action", f"click_failed:{type(exc).__name__}"
        return res
    dialog = _wait_file_dialog(timeout=5.0)
    res.evidence["file_dialog"] = bool(dialog)
    if dialog:
        try:
            import win32gui
            from pywinauto import keyboard

            win32gui.SetForegroundWindow(dialog)
            keyboard.send_keys("{ESC}")
        except Exception:
            pass
        time.sleep(0.8)
        res.evidence["dialog_closed"] = _wait_file_dialog(timeout=2.0) is None
    after = S.snapshot_desktop(win)
    res.after = after.to_dict()
    unchanged = after.digest == before.digest
    res.evidence["feed_unchanged"] = unchanged
    if not dry_run:
        res.layer, res.error = "unsupported", "real_publish_not_implemented"
        return res
    res.ok = bool(res.evidence.get("dialog_closed") or not res.evidence["file_dialog"]) and unchanged
    if not res.ok:
        res.layer, res.error = "verify", "composer_not_clean"
    return res


def _wait_file_dialog(timeout: float = 5.0) -> Any:
    """等系统「选择文件」框出现，返回 hwnd；没有返回 None。"""
    import win32gui

    deadline = time.monotonic() + max(0.5, float(timeout))
    while time.monotonic() < deadline:
        found = []
        def _cb(hwnd: int, _p) -> bool:
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                cls = win32gui.GetClassName(hwnd)
                title = win32gui.GetWindowText(hwnd)
                if cls == "#32770" or "选择文件" in title:
                    found.append(hwnd)
            except Exception:
                pass
            return True

        win32gui.EnumWindows(_cb, None)
        if found:
            return found[0]
        time.sleep(0.25)
    return None


def _run_engine_task(coro_factory: Any, *, wait_s: float = 180.0) -> Any:
    """跑引擎里已经验证过的朋友圈任务流程（create_task + 轮询 get_task）。"""
    import asyncio

    from backend.app.services import native_wechat_engine as eng

    async def _go() -> Any:
        task = await coro_factory()
        task_id = str((task or {}).get("id") or "")
        if not task_id:
            return task, task
        deadline = time.monotonic() + max(10.0, float(wait_s))
        latest = task
        while time.monotonic() < deadline:
            await asyncio.sleep(1.5)
            latest = eng.get_task(task_id) or latest
            if str(latest.get("status") or "").lower() in {"success", "succeeded", "failed", "error", "cancelled", "canceled"}:
                break
        return task, latest

    return asyncio.run(_go())


@spec(name="wechat.moments_like", platform="wechat", surface=S.WECHAT_MOMENTS,
      summary="给指定联系人的朋友圈点赞（复用引擎已验证的 task 流程）",
      precondition="微信窗口可见；目标是白名单里的小号/指定联系人",
      postcondition="任务终态为 succeeded 且 finding/liked 计数可读",
      destructive=True, params=["targets", "dry_run"])
def moments_like(targets: List[str], dry_run: bool = True, max_scrolls: int = 6,
                 account_id: str = "pc-wechat-default", wait_s: float = 180.0) -> Result:
    from backend.app.services import native_wechat_engine as eng

    res = Result(op="wechat.moments_like")
    win = find_window()
    if win is None:
        res.layer, res.error = "precondition", "wechat_window_not_found"
        return res
    activate_window(win)
    snap = S.snapshot_desktop(win)
    res.surface = snap.surface
    if snap.surface not in {S.WECHAT_CHAT, S.WECHAT_MOMENTS}:
        res.layer, res.error = "surface", "wrong_surface"
        res.evidence["required_surface"] = f"{S.WECHAT_CHAT}|{S.WECHAT_MOMENTS}"
        return res
    names = [str(t).strip() for t in (targets or []) if str(t).strip()]
    if not names:
        res.layer, res.error = "precondition", "empty_targets"
        return res
    try:
        created, latest = _run_engine_task(
            lambda: eng.create_moments_like_task(account_id, names, dry_run=dry_run, max_scrolls=max_scrolls),
            wait_s=wait_s,
        )
    except Exception as exc:
        res.layer, res.error = "action", f"task_failed:{type(exc).__name__}:{exc}"[:160]
        return res
    payload = (latest or {}).get("payload") or {}
    res.after = {"status": str((latest or {}).get("status") or ""), "payload": payload}
    res.evidence["dry_run"] = bool(dry_run)
    status = str((latest or {}).get("status") or "").lower()
    if status not in {"success", "succeeded"}:
        res.layer, res.error = "verify", "task_not_success"
        res.evidence["error_message"] = str((latest or {}).get("error_message") or "")[:160]
        return res
    # 结果证据：payload 里的 found/liked/already/skipped（引擎任务自己统计的）
    res.evidence["steps"] = len(payload.get("steps") or [])
    res.evidence["error_message"] = str((latest or {}).get("error_message") or "")[:160]
    res.ok = True
    return res


@spec(name="wechat.moments_comment", platform="wechat", surface=S.WECHAT_MOMENTS,
      summary="给指定联系人的朋友圈评论（复用引擎已验证的 task 流程）",
      precondition="微信窗口可见；目标是白名单里的小号/指定联系人",
      postcondition="任务终态 succeeded 且评论计数可读",
      destructive=True, params=["targets", "dry_run"])
def moments_comment(targets: List[str], dry_run: bool = True, max_scrolls: int = 6,
                    account_id: str = "pc-wechat-default", wait_s: float = 180.0) -> Result:
    from backend.app.services import native_wechat_engine as eng

    res = Result(op="wechat.moments_comment")
    win = find_window()
    if win is None:
        res.layer, res.error = "precondition", "wechat_window_not_found"
        return res
    activate_window(win)
    snap = S.snapshot_desktop(win)
    res.surface = snap.surface
    if snap.surface not in {S.WECHAT_CHAT, S.WECHAT_MOMENTS}:
        res.layer, res.error = "surface", "wrong_surface"
        return res
    names = [str(t).strip() for t in (targets or []) if str(t).strip()]
    if not names:
        res.layer, res.error = "precondition", "empty_targets"
        return res
    # 评论文案是 LLM 生成的，引擎需要登录 token（和 API 里一样的用法）
    token, uid = "", 0
    try:
        from backend.app.services.creator_metrics_daily_runner import read_login_context

        token, uid = read_login_context()
    except Exception:
        token, uid = "", 0
    if not token:
        res.layer, res.error = "precondition", "missing_login_token"
        return res
    try:
        created, latest = _run_engine_task(
            lambda: eng.create_moments_comment_task(
                account_id, names, dry_run=dry_run, max_scrolls=max_scrolls,
                user_id=(int(uid) if str(uid).isdigit() else 0),
                auth_context={"token": token, "user_id": (int(uid) if str(uid).isdigit() else 0)},
            ),
            wait_s=wait_s,
        )
    except Exception as exc:
        res.layer, res.error = "action", f"task_failed:{type(exc).__name__}:{exc}"[:160]
        return res
    payload = (latest or {}).get("payload") or {}
    status = str((latest or {}).get("status") or "").lower()
    res.after = {"status": status, "payload": payload}
    res.evidence["dry_run"] = bool(dry_run)
    if status not in {"success", "succeeded"}:
        res.layer, res.error = "verify", "task_not_success"
        res.evidence["error_message"] = str((latest or {}).get("error_message") or "")[:160]
        return res
    res.evidence["steps"] = len(payload.get("steps") or [])
    res.evidence["error_message"] = str((latest or {}).get("error_message") or "")[:160]
    res.ok = True
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
