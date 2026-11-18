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
    if _window_hung(win):
        res.layer, res.error = "precondition", "wechat_window_hung"
        _trace("robot_open_failed", target=target, error="wechat_window_hung")
        return res
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
def _trace(cap: str, **fields: Any) -> None:
    """robot 每一步都落一行，避免再出现"日志停在 reply_send_started"的黑盒。"""
    try:
        import json as _json
        from datetime import datetime as _dt
        from pathlib import Path as _Path

        log = _Path(__file__).resolve().parents[2] / "logs" / "native_wechat_send.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": _dt.now().strftime("%Y-%m-%dT%H:%M:%S"), "stage": cap, "driver": "robot"}
        for k, v in fields.items():
            if isinstance(v, (int, float, bool, type(None))):
                row[k] = v
            else:
                row[k] = str(v)[:200]
        with log.open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass

def _window_hung(window: Any) -> bool:
    """微信主窗口无响应时，任何 UIA/COM 调用都会长时间阻塞。

    客户机上反复出现的就是这个：本机能发（窗口正常响应），
    客户机卡在一个 COM 调用上几分钟不动。这里先判一次，避免把整轮拖死。
    """
    try:
        import win32gui

        hwnd = int(getattr(window, "handle", 0) or 0)
        if not hwnd:
            return False
        return bool(win32gui.IsHungAppWindow(hwnd))
    except Exception:
        return False

def _click_contact_group_result(window: Any, expect_name: str, *, tries: int = 3) -> bool:
    """只点"联系人"分组里的结果行（对齐引擎里 selection_scope=联系人:first_matching_result）。

    搜索结果里同名群聊一堆（军争、单鹏程 / 单鹏程、军争…），按名字点很容易落到群里。
    这里按 UIA 里的顺序找"联系人"分组标题，然后只在该分组内的前几行里点，并核对会话名。
    """
    from publisher.agent.desktop import _chat_left, _iter_controls, _rect, _visible

    rect = _rect(window) or (0, 0, 0, 0)
    chat_left = _chat_left(window, rect[0], rect[2] - rect[0])
    rows = []
    for c, info in _iter_controls(window):
        try:
            if str(info.control_type or "") != "ListItem" or not _visible(c):
                continue
            if int(c.rectangle().left) >= chat_left:
                continue
            rows.append((c, " ".join(str(info.name or "").split())))
        except Exception:
            continue
    start = None
    for idx, (_c, name) in enumerate(rows):
        if name == "联系人":
            start = idx + 1
            break
    if start is None:
        return False
    tried = 0
    for c, name in rows[start:]:
        if name in {"群聊", "会话", "公众号", "服务号", "聊天记录"}:
            break  # 已经走出"联系人"分组
        if tried >= tries:
            break
        tried += 1
        try:
            c.click_input()
            time.sleep(1.0)
            if _current_chat_name(window) == expect_name:
                return True
        except Exception:
            continue
    return False

def open_chat(name: str, window: Any = None, timeout: float = 8.0, search_key: str = "") -> Result:
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
    if _window_hung(win):
        res.layer, res.error = "precondition", "wechat_window_hung"
        _trace("robot_open_failed", target=target, error="wechat_window_hung")
        return res
    snap = _ensure_surface(win, S.WECHAT_CHAT, res)
    if snap is None:
        return res
    if snap.anchors.get("chat") == target:
        res.after = snap.to_dict()
        res.ok = True
        res.evidence["already_open"] = True
        _trace("robot_open_already", target=target)
        return res
    _trace("robot_open_start", target=target, current=snap.anchors.get("chat"), rows=snap.count)
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
    # 轮询用便宜的"当前会话名"检查；整页快照只在命中后取一次
    # （整页快照要扫 800 个控件 + 消息列表，真机上单次就要 1 秒多，
    #   放在轮询里会把 2 秒的切换拖成 14 秒）
    from publisher.agent.desktop import _current_chat_name

    deadline = time.monotonic() + max(3.0, float(timeout))
    while time.monotonic() < deadline:
        if _current_chat_name(win) == target:
            res.after = S.snapshot_desktop(win).to_dict()
            res.ok = True
            res.evidence["via"] = "session_row"
            _trace("robot_open_ok", target=target, via="session_row")
            return res
        time.sleep(0.2)
    # 搜索 + 回车（2026-11-18 加固）
    # 真机实测：微信搜索索引慢时，按一次回车 + 固定等 1.5s 会直接超时判失败
    # （表现为 chat_name_mismatch，客户侧就是"没回复"）。现在：
    #   剪贴板输入（名字里有空格/表情不会被打歪）→ 最多按 3 次回车 →
    #   轮询窗口放到 timeout*1.5（默认 12s）→ 仍不行就点第一条匹配的搜索结果。
    box = _find_control(win, "搜索", region="sessions") or _find_control(win, "搜索")
    if box is not None:
        # 2026-11-18：微信不在前台时，剪贴板粘贴/回车会打到别的窗口上——
        # 本机能发、客户机发不出去，最常见的就是这一条。
        try:
            active = bool(win.is_active())
        except Exception:
            active = False
        if not active:
            activate_window(win)
            try:
                active = bool(win.is_active())
            except Exception:
                active = False
        _trace("robot_search_focus", target=target, active=active)
        if not active:
            res.layer, res.error = "precondition", "window_not_foreground"
            _trace("robot_open_failed", target=target, error="window_not_foreground")
            return res
        typed = False
        # 2026-11-18：按显示名搜会撞到同名群聊（现场 军争 / 军争、单鹏程 / 单鹏程、军争）。
        # 有 wxid 就用 wxid 搜，点结果后再按显示名核对。
        query = str(search_key or "").strip() or target
        try:
            from pywinauto import keyboard
            from publisher.agent.desktop import type_text

            box.click_input()
            time.sleep(0.15)
            keyboard.send_keys("^a")
            keyboard.send_keys("{DELETE}")
            typed = bool(type_text(query))
        except Exception:
            typed = False
        if not typed:
            try:
                from pywinauto import keyboard

                keyboard.send_keys(target, with_spaces=True)
            except Exception:
                pass
        _trace("robot_search_typed", target=target, typed=typed)
        deadline = time.monotonic() + max(6.0, float(timeout) * 1.5)
        next_enter = time.monotonic() + 1.0
        pressed = 0
        while time.monotonic() < deadline:
            if pressed < 3 and time.monotonic() >= next_enter:
                try:
                    from pywinauto import keyboard as _kb

                    _kb.send_keys("{ENTER}")
                except Exception:
                    pass
                pressed += 1
                next_enter = time.monotonic() + 2.0
            time.sleep(0.2)
            if _current_chat_name(win) == target:
                res.after = S.snapshot_desktop(win).to_dict()
                res.ok = True
                res.evidence["via"] = "search_enter"
                res.evidence["enter_presses"] = pressed
                _trace("robot_open_ok", target=target, via="search_enter", presses=pressed)
                return res
        # 兜底 1：只在"联系人"分组里点（这是以前那个流程）
        try:
            if _click_contact_group_result(win, target):
                res.ok = True
                res.evidence["via"] = "contact_group_click"
                _trace("robot_open_ok", target=target, via="contact_group_click")
                return res
        except Exception:
            pass
        # 兜底 2：点搜索结果里第一条匹配的行
        try:
            from publisher.agent.desktop import _chat_left, _iter_controls, _visible

            rect = _rect(win) or (0, 0, 0, 0)
            chat_left = _chat_left(win, rect[0], rect[2] - rect[0])
            rows = []
            for c, info in _iter_controls(win):
                try:
                    if str(info.control_type or "") != "ListItem" or not _visible(c):
                        continue
                    if int(c.rectangle().left) >= chat_left:
                        continue
                    rows.append((c, str(info.name or "").strip()))
                except Exception:
                    continue
            # 先点名字匹配的，再逐个试前几行（wxid 搜索时行里显示昵称，不能靠名字过滤）
            ordered = [c for c, nm in rows if nm.startswith(target)] + [c for c, nm in rows if not nm.startswith(target)]
            for c in ordered[:4]:
                try:
                    c.click_input()
                    time.sleep(1.0)
                    if S.snapshot_desktop(win).anchors.get("chat") == target:
                        res.ok = True
                        res.evidence["via"] = "search_result_click"
                        _trace("robot_open_ok", target=target, via="search_result_click")
                        return res
                except Exception:
                    continue
        except Exception:
            pass
    after = S.snapshot_desktop(win)
    res.after = after.to_dict()
    res.layer, res.error = "verify", "chat_name_mismatch"
    _trace("robot_open_failed", target=target, current=S.snapshot_desktop(win).anchors.get("chat"), error="chat_name_mismatch")
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
    if _window_hung(win):
        res.layer, res.error = "precondition", "wechat_window_hung"
        _trace("robot_open_failed", target=target, error="wechat_window_hung")
        return res
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
    if _window_hung(win):
        res.layer, res.error = "precondition", "wechat_window_hung"
        _trace("robot_open_failed", target=target, error="wechat_window_hung")
        return res
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
    from publisher.agent.desktop import list_friend_requests

    # 用统一的解析（会过滤「新的朋友/群聊」这类页头行），别直接吃 snapshot 的原始 items
    rows = list_friend_requests(win)
    pending = [r for r in rows if r["status"] == "pending"]
    res.after = snap.to_dict()
    res.ok = True
    res.evidence.update({"total": len(rows), "pending": len(pending),
                         "items": [{"text": r["name"][:60], "status": r["status"]}
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


@spec(name="wechat.accept_friend_request", platform="wechat", surface=S.WECHAT_FRIEND_REQUESTS,
      summary="按名字通过一条好友申请（默认 dry_run，只认精确名字）",
      precondition="停在「新的朋友」页；目标名必须在待处理列表里",
      postcondition="该行状态变为 accepted", destructive=True, params=["name", "dry_run"])
def accept_friend_request(name: str, dry_run: bool = True, window: Any = None,
                          account_id: str = "pc-wechat-default") -> Result:
    from publisher.agent.desktop import list_friend_requests

    res = Result(op="wechat.accept_friend_request")
    want = str(name or "").strip()
    if not want:
        res.layer, res.error = "precondition", "empty_name"
        return res
    win = window or find_window()
    if win is None:
        res.layer, res.error = "precondition", "wechat_window_not_found"
        return res
    activate_window(win)
    snap = _ensure_surface(win, S.WECHAT_FRIEND_REQUESTS, res)
    if snap is None:
        return res
    rows = list_friend_requests(win)
    pending = [r for r in rows if r["status"] == "pending"]
    res.evidence["pending"] = len(pending)
    match = [r for r in pending if r["name"].startswith(want)]
    if not match:
        res.layer, res.error = "precondition", "request_not_found"
        res.evidence["names"] = [r["name"][:24] for r in pending[:8]]
        return res
    res.evidence["target"] = match[0]["name"][:60]
    if dry_run:
        res.ok = True
        res.evidence["dry_run"] = True
        return res
    # 真通过：复用引擎里那条“按可见项通过”的实现，只传被选中的那一个
    try:
        from backend.app.services import native_wechat_engine as eng

        hwnd = int(getattr(win, "handle", 0) or 0)
        visible = eng._visible_pending_friend_requests(win.wrapper_object() if hasattr(win, "wrapper_object") else win)
        target_item = None
        for item in visible or []:
            nm = str(item.get("name") or item.get("display_name") or "")
            if nm.startswith(want):
                target_item = item
                break
        if target_item is None:
            res.layer, res.error = "action", "engine_item_not_found"
            return res
        steps = eng._accept_visible_friend_request(hwnd, target_item)
        res.evidence["steps"] = [str(s)[:40] for s in (steps or [])][:6]
    except Exception as exc:
        res.layer, res.error = "action", f"accept_failed:{type(exc).__name__}:{exc}"[:160]
        return res
    after = S.snapshot_desktop(win)
    res.after = after.to_dict()
    accepted = [r for r in list_friend_requests(win)
                if r["name"].startswith(want) and r["status"] == "accepted"]
    res.ok = bool(accepted)
    res.evidence["accepted_rows"] = len(accepted)
    if not res.ok:
        res.layer, res.error = "verify", "not_accepted"
    return res
