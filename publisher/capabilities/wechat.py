"""微信个人号能力（可组合、自带校验）。

现状对照：引擎里是 `_send_text_local_slow` 这种"一条龙剧本"，读不回输入框内容就整条失败。
这里拆成：
  - open_chat(name)      只负责把会话切对，postcondition 是当前会话名 == name
  - send_message(text)   只负责在**已确认正确的会话**里发一条，postcondition 是聊天记录里出现该文本
调用方（Agent 或引擎）可以先用 open_chat 摆正现场，再决定要不要发。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from publisher.agent.desktop import (
    _chat_left,
    _current_chat_name,
    _iter_controls,
    _rect,
    _region,
    _visible,
    _window_visible,
    activate_window,
    find_window,
    verify_desktop_stage,
)
from .base import CapResult, Capability

logger = logging.getLogger(__name__)

OPEN_CHAT = Capability(
    name="wechat.open_chat",
    platform="wechat",
    summary="把微信主窗口切到指定联系人的会话",
    precondition="微信主窗口存在且会话列表可见",
    postcondition="当前聊天输入框所属会话名 == 目标名",
    params=["name"],
)

SEND_MESSAGE = Capability(
    name="wechat.send_message",
    platform="wechat",
    summary="在**当前已确认的会话**里发送一条文本消息",
    precondition="当前会话名 == 目标名（防止发错人）",
    postcondition="该文本真的出现在聊天记录里",
    destructive=True,
    params=["text", "to"],
)


def _session_rows(window: Any) -> List[Any]:
    """只取左侧会话列的行。

    注意：不能用固定比例切区（_region 的 28%）——聊天消息从 x≈1217 起，
    宽度 700+，会被算成 sessions，open_chat 就会去点一条聊天记录。
    这里用 session_list 的真实右边界 + 行宽上限双重过滤。
    """
    rect = _rect(window) or (0, 0, 0, 0)
    left, width = rect[0], rect[2] - rect[0]
    chat_left = _chat_left(window, left, width)
    rows = []
    for c, info in _iter_controls(window):
        try:
            if str(info.control_type or "") != "ListItem":
                continue
            rc = c.rectangle()
            if int(rc.left) >= chat_left:
                continue
            if (rc.right - rc.left) > max(260, int(width * 0.32)):
                continue
            if _visible(c):
                rows.append(c)
        except Exception:
            continue
    return rows


def _row_name(control: Any) -> str:
    try:
        return " ".join(str(control.element_info.name or "").split())
    except Exception:
        return ""


def _pick_row(rows: List[Any], target: str) -> Any:
    for row in rows:
        if _row_name(row).startswith(target):
            return row
    return None


def _open_via_search(window: Any, target: str, *, timeout: float = 8.0) -> bool:
    """搜索框输入联系人名后回车 —— 真机验证这是唯一可靠路径：
    点搜索结果行只会关掉搜索、不切会话；回车才真的进对话。"""
    box = _find_control(window, "搜索", region="sessions") or _find_control(window, "搜索")
    if box is None:
        return False
    try:
        from pywinauto import keyboard

        box.click_input()
        keyboard.send_keys("^a")
        keyboard.send_keys("{DELETE}")
        keyboard.send_keys(target, with_spaces=True)
        time.sleep(1.5)
        keyboard.send_keys("{ENTER}")
    except Exception:
        return False
    deadline = time.monotonic() + max(2.0, float(timeout))
    while time.monotonic() < deadline:
        time.sleep(0.3)
        if _current_chat_name(window) == target:
            return True
    return False


def open_chat(name: str, *, window: Any = None, timeout: float = 8.0) -> CapResult:
    """把会话切到 name。顺序：已经在 → 可视行 → 搜索。每一步都回读当前会话名。"""
    res = CapResult(name=OPEN_CHAT.name)
    target = str(name or "").strip()
    if not target:
        res.error = "empty_target"
        return res
    win = window or find_window()
    if win is None:
        res.error = "wechat_window_not_found"
        return res
    activate_window(win)
    res.precondition = _window_visible(win)
    res.evidence["session_rows"] = len(_session_rows(win))
    if not res.precondition:
        res.error = "window_not_visible"
        return res
    if _current_chat_name(win) == target:
        res.action_ok = res.verified = res.ok = True
        res.evidence["already_open"] = True
        return res

    # 1) 可视会话行（置顶/最近几条）
    picked = _pick_row(_session_rows(win), target)
    if picked is not None:
        try:
            picked.click_input()
            res.action_ok = True
        except Exception as exc:
            res.evidence["click_error"] = type(exc).__name__
        deadline = time.monotonic() + max(1.5, float(timeout) * 0.5)
        while time.monotonic() < deadline:
            time.sleep(0.3)
            if _current_chat_name(win) == target:
                res.verified = res.ok = True
                res.evidence.update({"via": "session_row", "chat": target})
                return res
    # 2) 搜索 + 回车（会话列只有置顶那几条，目标常在下面）
    if _open_via_search(win, target, timeout=timeout):
        res.action_ok = res.verified = res.ok = True
        res.evidence.update({"via": "search_enter", "chat": target})
        return res
    res.action_ok = res.action_ok or False
    res.error = "chat_name_mismatch" if res.action_ok else "session_not_found"
    res.evidence["current_chat"] = _current_chat_name(win)
    res.evidence["visible_sessions"] = [_row_name(r)[:14] for r in _session_rows(win)[:8]]
    return res


def send_message(text: str, to: str = "", *, window: Any = None, timeout: float = 8.0) -> CapResult:
    res = CapResult(name=SEND_MESSAGE.name)
    body = str(text or "")
    if not body.strip():
        res.error = "empty_text"
        return res
    win = window or find_window()
    if win is None:
        res.error = "wechat_window_not_found"
        return res
    activate_window(win)
    current = _current_chat_name(win)
    target = str(to or "").strip()
    res.evidence["current_chat"] = current
    # 前置条件：必须在目标会话里。不做“自动切过去再发”，切会话是 open_chat 的职责。
    res.precondition = bool(current) and (not target or current == target or current.startswith(target))
    if not res.precondition:
        res.error = "wrong_chat"
        res.evidence["expected"] = target
        return res
    from publisher.agent.desktop import _chat_input

    control = _chat_input(win)
    if control is None:
        res.error = "chat_input_not_found"
        return res
    try:
        from pywinauto import keyboard

        control.click_input()
        keyboard.send_keys("^a")
        keyboard.send_keys("{DELETE}")
        keyboard.send_keys(body, with_spaces=True)
        keyboard.send_keys("{ENTER}")
    except Exception as exc:
        res.error = f"type_failed: {type(exc).__name__}: {exc}"[:160]
        return res
    res.action_ok = True
    expect = body.strip()[:16]
    deadline = time.monotonic() + max(2.0, float(timeout))
    while time.monotonic() < deadline:
        time.sleep(0.4)
        if verify_desktop_stage(win, "wechat_send", expect_text=expect, guard_name=current):
            res.verified = True
            res.ok = True
            res.evidence["matched_text"] = expect
            return res
    res.error = "sent_not_verified"
    return res
