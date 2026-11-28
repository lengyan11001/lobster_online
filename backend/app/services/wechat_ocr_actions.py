"""OCR 动作层：UIA 失效（微信 4.2）时，用截图 + 点击完成三件日常操作。

覆盖：
    send_text(chat, text)                     回消息（搜索会话 → 输入框 → 回车）
    add_friend(keyword, hello=..., submit=..) 加好友（+ → 添加朋友 → 搜索 → 申请）
    post_moments(images, text, ...)           发朋友圈图文（发现 → 朋友圈 → 相机 → 选图 → 文案 → 发表）

原则：
- 每一步都做状态校验（OCR 确认界面真的变了），失败重试 3 次再报错，不盲点；
- 点击统一走 wechat_ocr_driver.click_checked（遮挡/置顶校验）；
- publish/提交类动作有 submit 开关，默认 False 的只走到确认页，供干跑验证。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import win32api
import win32clipboard
import win32con
import win32gui

from backend.app.services import wechat_ocr_driver as drv

MAX_RETRY = 3

# 相对窗口左上角的固定像素锚点：微信侧栏/会话列宽度不随窗口缩放，
# 用比例会在窗口从 1009 拉到 1133 时点偏（真机实测）。
RAIL_X = 27
RAIL_CHAT_Y = 107
RAIL_DISCOVER_Y = 257
SEARCH_DX, SEARCH_DY = 159, 56      # 会话列表顶部搜索框
PLUS_DX, PLUS_DY = 195, 56          # 会话列表顶部 + 号
CAMERA_DR, CAMERA_DY = 87, 62       # 朋友圈相机：距右缘 / 距顶


def _rail(hwnd: int, rect: Tuple[int, int, int, int], dy: int) -> None:
    l, t, _, _ = rect
    drv.click_checked(hwnd, l + RAIL_X, t + dy)


def _search_box(hwnd: int, rect: Tuple[int, int, int, int]) -> None:
    l, t, _, _ = rect
    drv.click_checked(hwnd, l + SEARCH_DX, t + SEARCH_DY)


def _plus_button(hwnd: int, rect: Tuple[int, int, int, int]) -> None:
    l, t, _, _ = rect
    drv.click_checked(hwnd, l + PLUS_DX, t + PLUS_DY)


def _input_box(hwnd: int, rect: Tuple[int, int, int, int]) -> None:
    l, _, r, b = rect
    drv.click_checked(hwnd, l + int((r - l) * 0.60), b - 95)


def _camera_button(hwnd: int, rect: Tuple[int, int, int, int]) -> None:
    _, t, r, _ = rect
    drv.click_checked(hwnd, r - CAMERA_DR, t + CAMERA_DY)


# ------------------------------------------------------------------ 基础

def _set_clipboard(text: str) -> bool:
    for _ in range(8):
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            win32clipboard.CloseClipboard()
            return True
        except Exception:
            time.sleep(0.1)
    return False


def _set_clipboard_files(paths: Sequence[str]) -> bool:
    import struct

    files = "\0".join(os.path.abspath(p) for p in paths) + "\0\0"
    try:
        data = struct.pack("I", 20) + struct.pack("i", 0) * 5 + files.encode("utf-16le")
        for _ in range(8):
            try:
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
                win32clipboard.CloseClipboard()
                return True
            except Exception:
                time.sleep(0.1)
    except Exception:
        pass
    return False


def _tap(vk: int) -> None:
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)


def _ctrl(key: int) -> None:
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    time.sleep(0.03)
    _tap(key)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)


_KEYEVENTF_UNICODE = 0x0004
_KEYEVENTF_KEYUP = 0x0002


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_void_p)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_void_p)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


def _type_text(text: str) -> bool:
    """Unicode 逐字注入（不依赖剪贴板；微信子窗口不接 Ctrl+V）。

    SendInput 的 sizeof(INPUT) 必须和系统一致（x64 = 40），union 里带上
    MOUSEINPUT 才不会算小；失败时对 ASCII 退化成 keybd_event。
    """
    if not text:
        return True
    ok = True
    for ch in text:
        code = ord(ch)
        try:
            down = _INPUT(type=1, u=_INPUTUNION(ki=_KEYBDINPUT(0, code, _KEYEVENTF_UNICODE, 0, None)))
            up = _INPUT(type=1, u=_INPUTUNION(ki=_KEYBDINPUT(0, code, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP, 0, None)))
            arr = (_INPUT * 2)(down, up)
            sent = ctypes.windll.user32.SendInput(2, arr, ctypes.sizeof(_INPUT))
        except Exception:
            sent = 0
        if sent != 2:
            ok = False
            if "0" <= ch <= "9":
                vk = 0x30 + int(ch)
                _tap(vk)
            else:
                break
        time.sleep(0.02)
    return ok


def _paste_text(text: str) -> None:
    _set_clipboard(text)
    time.sleep(0.15)
    _ctrl(0x56)


def _focus(hwnd: int) -> Tuple[int, int, int, int]:
    """切前台并压过普通窗口（Chrome/资源管理器等）。整段操作只需调一次，
    之后不要再提升层级——会把微信自己的弹出菜单关掉。"""
    drv.ensure_visible(hwnd)
    drv.bring_to_front(hwnd)
    drv.make_topmost(hwnd)
    return drv.window_rect(hwnd)


def _ocr(hwnd: int, region=None) -> List[Dict[str, Any]]:
    try:
        return drv.read_window(hwnd, region=region)
    except Exception:
        return []


def wechat_page(main_hwnd: int):
    """当前该操作的微信窗：前台若是微信页面窗就用它，否则用非主窗子页面，最后才主窗。"""
    import win32process
    try:
        fg = int(win32gui.GetForegroundWindow() or 0)
        if fg:
            pid_main = win32process.GetWindowThreadProcessId(int(main_hwnd))[1]
            if fg != int(main_hwnd) and \
                    win32process.GetWindowThreadProcessId(fg)[1] == pid_main and \
                    win32gui.GetClassName(fg) == "Qt51514QWindowIcon":
                return fg, drv.window_rect(fg)
    except Exception:
        pass
    pages = [x for x in drv.wechat_pages() if (x["title"] or "") != "微信"]
    if pages:
        pages.sort(key=lambda x: x["area"])
        return pages[0]["hwnd"], drv.window_rect(pages[0]["hwnd"])
    return int(main_hwnd), drv.window_rect(int(main_hwnd))


def _norm(text: str) -> str:
    """去空白比较：OCR 会把 'OCR 自测 21:05' 读成 'OCR自测21:05'。"""
    return re.sub(r"\s+", "", text or "")


def _has(items: Sequence[Dict[str, Any]], needle: str) -> bool:
    if not needle:
        return False
    n = _norm(needle)
    return any(n in _norm(it["text"]) for it in items)


def _find_exact(items: Sequence[Dict[str, Any]], needle: str) -> Optional[Dict[str, Any]]:
    return next((it for it in items if it["text"].strip() == needle), None)


def _wait(pred, timeout: float = 6.0, interval: float = 0.5):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = pred()
        if last:
            return last
        time.sleep(interval)
    return last


def _click_at(hwnd: int, rect: Tuple[int, int, int, int], fx: float, fy: float) -> None:
    l, t, r, b = rect
    drv.click_checked(hwnd, int(l + (r - l) * fx), int(t + (b - t) * fy))


def _click_item(hwnd: int, it: Dict[str, Any]) -> None:
    drv.click_checked(hwnd, it["cx"], it["cy"])


def _ensure_chat_page(hwnd: int, rect: Tuple[int, int, int, int]) -> bool:
    """回到「聊天」主页面（4.2 聊天页没有「搜索」占位文字，只有图标）。"""
    def _on_chat(items) -> bool:
        texts = [it["text"] for it in items]
        if any("搜索" in t for t in texts):
            return True
        return any(t.strip() in ("服务号", "公众号", "订阅号", "文件传输助手") for t in texts)

    for _ in range(3):
        if _on_chat(_ocr(hwnd)):
            return True
        _rail(hwnd, rect, RAIL_CHAT_Y)
        time.sleep(1.0)
    return _on_chat(_ocr(hwnd))


def _ocr(hwnd: int, region=None) -> List[Dict[str, Any]]:
    try:
        return drv.read_window(hwnd, region=region)
    except Exception:
        return []


def wechat_page(main_hwnd: int):
    """当前该操作的微信窗：前台若是微信页面窗就用它，否则用非主窗子页面，最后才主窗。"""
    import win32process
    try:
        fg = int(win32gui.GetForegroundWindow() or 0)
        if fg:
            pid_main = win32process.GetWindowThreadProcessId(int(main_hwnd))[1]
            if fg != int(main_hwnd) and \
                    win32process.GetWindowThreadProcessId(fg)[1] == pid_main and \
                    win32gui.GetClassName(fg) == "Qt51514QWindowIcon":
                return fg, drv.window_rect(fg)
    except Exception:
        pass
    pages = [x for x in drv.wechat_pages() if (x["title"] or "") != "微信"]
    if pages:
        pages.sort(key=lambda x: x["area"])
        return pages[0]["hwnd"], drv.window_rect(pages[0]["hwnd"])
    return int(main_hwnd), drv.window_rect(int(main_hwnd))


def _norm(text: str) -> str:
    """去空白比较：OCR 会把 'OCR 自测 21:05' 读成 'OCR自测21:05'。"""
    return re.sub(r"\s+", "", text or "")


def _has(items: Sequence[Dict[str, Any]], needle: str) -> bool:
    if not needle:
        return False
    n = _norm(needle)
    return any(n in _norm(it["text"]) for it in items)


def _find_exact(items: Sequence[Dict[str, Any]], needle: str) -> Optional[Dict[str, Any]]:
    return next((it for it in items if it["text"].strip() == needle), None)


def _wait(pred, timeout: float = 6.0, interval: float = 0.5):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = pred()
        if last:
            return last
        time.sleep(interval)
    return last


def _click_at(hwnd: int, rect: Tuple[int, int, int, int], fx: float, fy: float) -> None:
    l, t, r, b = rect
    drv.click_checked(hwnd, int(l + (r - l) * fx), int(t + (b - t) * fy))


def _click_item(hwnd: int, it: Dict[str, Any]) -> None:
    drv.click_checked(hwnd, it["cx"], it["cy"])


def _ensure_chat_page(hwnd: int, rect: Tuple[int, int, int, int]) -> bool:
    """回到「聊天」主页面（侧栏第一个图标）。"""
    l, t, r, b = rect
    for _ in range(3):
        items = _ocr(hwnd)
        # 聊天页有搜索框占位；朋友圈/视频号页没有
        if any("搜索" in it["text"] for it in items):
            return True
        _rail(hwnd, rect, RAIL_CHAT_Y)
        time.sleep(1.0)
    return any("搜索" in it["text"] for it in _ocr(hwnd))


# ------------------------------------------------------------------ 回消息

def send_text(chat: str, text: str, *, submit: bool = True) -> Dict[str, Any]:
    """给会话 chat 发一条文字。返回 {ok, chat, verified, error}。

    路径：点搜索框 → 粘会话名 → 回车 → 校验标题 → 点输入框 → 粘文本 → 回车 →
    校验文本出现在消息区（最多重试 3 次）。
    """
    hwnd = drv.find_wechat_hwnd()
    if not hwnd:
        return {"ok": False, "error": "没找到微信窗口"}
    rect = _focus(hwnd)
    drv.close_other_pages(hwnd)
    l, t, r, b = rect
    w, hh = r - l, b - t
    notes: List[str] = []

    _rail(hwnd, rect, RAIL_CHAT_Y)
    time.sleep(0.6)

    for attempt in range(MAX_RETRY):
        _search_box(hwnd, rect)
        time.sleep(0.7)
        if not _has(_ocr(hwnd), "搜索"):
            notes.append(f"try{attempt}: 搜索框没打开")
            continue
        _paste_text(chat)
        time.sleep(1.3)
        _tap(win32con.VK_RETURN)
        time.sleep(1.8)
        title_box = (l + int(w * 0.20), t + int(hh * 0.02), l + int(w * 0.70), t + int(hh * 0.10))
        title = " ".join(it["text"] for it in _ocr(hwnd, region=title_box))
        if chat.strip() and chat.strip() not in title:
            notes.append(f"try{attempt}: 标题={title!r}，不是目标会话")
            _tap(win32con.VK_ESCAPE)
            time.sleep(0.5)
            continue
        notes.append(f"try{attempt}: 已进入 {chat!r}")
        break
    else:
        return {"ok": False, "chat": chat, "error": "没能切到目标会话", "notes": notes}

    if not submit:
        return {"ok": True, "chat": chat, "submitted": False, "notes": notes}

    for attempt in range(MAX_RETRY):
        _input_box(hwnd, rect)
        time.sleep(0.5)
        _ctrl(0x41)          # 清掉输入框里残留的草稿
        time.sleep(0.15)
        _paste_text(text)
        time.sleep(0.7)
        if not _has(_ocr(hwnd), text[:8]):
            notes.append(f"send{attempt}: 输入框没出现文本")
            continue
        _tap(win32con.VK_RETURN)
        time.sleep(1.4)
        tail = _ocr(hwnd, region=(l + int(w * 0.20), t + int(hh * 0.55), r - 10, b - 10))
        if _has(tail, text[:8]):
            return {"ok": True, "chat": chat, "verified": True, "notes": notes}
        notes.append(f"send{attempt}: 消息区没看到刚发的文本")
    return {"ok": False, "chat": chat, "verified": False, "error": "发送后未校验到消息", "notes": notes}


# ------------------------------------------------------------------ 加好友

def _click_menu_item(hwnd: int, rect: Tuple[int, int, int, int], text: str) -> bool:
    """点 + 菜单里的项（菜单是临时层，点了只关闭菜单是正常现象——
    4.2 会把「添加朋友」开成独立窗口，是否成功由调用方找窗口判断）。"""
    import win32api

    items = [it for it in _ocr(hwnd) if it["text"].strip() == text]
    if not items:
        return False
    hit = items[0]
    win32api.SetCursorPos((hit["cx"], hit["cy"]))
    time.sleep(0.35)
    drv.click_plain(hit["cx"], hit["cy"])
    return True


def _await_page(title_part: str, timeout: float = 5.0) -> Optional[Tuple[int, Tuple[int, int, int, int]]]:
    """等某个微信子页面窗出现，并把它提到最前（4.2 常常开在主窗后面）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        page = drv.page_by_title(title_part)
        if page:
            rect = drv.bring_page_front(page["hwnd"])
            return page["hwnd"], rect
        time.sleep(0.35)
    return None


def open_add_friend_page(hwnd: int, rect: Tuple[int, int, int, int]) -> Optional[Tuple[int, Tuple[int, int, int, int]]]:
    """主窗 → + → 添加朋友；返回「添加朋友」窗口及其 rect。"""
    for _ in range(3):
        page = _await_page("添加朋友", timeout=0.01)
        if page:
            return page
        _plus_button(hwnd, rect)
        time.sleep(0.9)
        if _click_menu_item(hwnd, rect, "添加朋友"):
            page = _await_page("添加朋友", timeout=4.0)
            if page:
                return page
    return None


def _alive(hwnd: int) -> bool:
    try:
        return bool(win32gui.IsWindow(int(hwnd)))
    except Exception:
        return False


def _page_or_refind(hwnd: int, title: str,
                    rect: Tuple[int, int, int, int]) -> Tuple[int, Tuple[int, int, int, int]]:
    if _alive(hwnd):
        return hwnd, drv.window_rect(hwnd)
    page = drv.page_by_title(title)
    if page:
        return page["hwnd"], drv.bring_page_front(page["hwnd"])
    return 0, rect


def search_in_add_page(page_hwnd: int, page_rect: Tuple[int, int, int, int],
                       keyword: str) -> str:
    """在「添加朋友」窗里搜关键词 → 返回 not_found / card / already / unknown。"""
    page_hwnd, page_rect = _page_or_refind(page_hwnd, "添加朋友", page_rect)
    if not page_hwnd:
        return "unknown"
    for _ in range(2):
        items = _ocr(page_hwnd)
        box = next((it for it in items if "搜索微信号" in it["text"]), None)
        if box:
            _click_item(page_hwnd, box)
            time.sleep(0.4)
            break
        time.sleep(0.5)
    page_hwnd, page_rect = _page_or_refind(page_hwnd, "添加朋友", page_rect)
    if not page_hwnd:
        return "unknown"
    _ctrl(0x41)
    _paste_text(keyword)
    time.sleep(0.6)
    btn = _find_exact(_ocr(page_hwnd), "搜索")
    if btn:
        _click_item(page_hwnd, btn)
    else:
        _tap(win32con.VK_RETURN)
    time.sleep(2.8)

    page_hwnd, page_rect = _page_or_refind(page_hwnd, "添加朋友", page_rect)
    if not page_hwnd:
        return "unknown"
    texts = [it["text"] for it in _ocr(page_hwnd)]
    if any("找不到" in t or "无法找到" in t or "不存在" in t for t in texts):
        return "not_found"
    if any(("添加到通讯录" in t) or ("申请添加朋友" in t) or ("加为朋友" in t) for t in texts):
        return "card"
    if any(t.strip() in ("发消息", "语音聊天", "视频聊天") for t in texts):
        return "already"
    return "unknown"


def add_friend(
    keyword: str,
    *,
    hello: str = "",
    remark: str = "",
    submit: bool = False,
) -> Dict[str, Any]:
    """按 微信号/手机号 加好友（脚本段）。

    4.2 的子页面（添加朋友/申请添加朋友/资料卡）都是独立顶层窗，而且会随操作
    反复重建：所以每一步都重新找"当前微信页面窗"，不长期持有句柄。
    """
    main = drv.find_wechat_hwnd()
    if not main:
        return {"ok": False, "status": "failed", "error": "没找到微信窗口"}
    rect = _focus(main)
    _ensure_chat_page(main, rect)
    notes: List[str] = []

    page = None
    for _ in range(3):
        _plus_button(main, rect)
        time.sleep(0.9)
        if _click_menu_item(main, rect, "添加朋友"):
            drv.unset_topmost(main)          # 主窗不放开置顶，子页面压不上去
            page = _await_page("添加朋友", timeout=4.0)
        if page:
            break
    if not page:
        return {"ok": False, "status": "failed", "error": "没能打开「添加朋友」窗口", "notes": notes}

    ph, prect = drv.current_page(main)
    notes.append(f"page={win32gui.GetWindowText(ph)!r} rect={prect}")

    items = _ocr(ph)
    box = next((it for it in items if "搜索微信号" in it["text"]), None)
    typed = False
    for _ in range(2):
        if box:
            _click_item(ph, box)
            time.sleep(0.25)
            _click_item(ph, box)      # 第一次点击只是激活窗口，第二次才聚焦输入框
            time.sleep(0.35)
        _ctrl(0x41)
        if not _type_text(keyword):
            _paste_text(keyword)
        time.sleep(0.8)
        if any(keyword[:6] in it["text"].replace(" ", "") for it in _ocr(ph)):
            typed = True
            break
        items = _ocr(ph)
        box = next((it for it in items if "搜索微信号" in it["text"]), None)
    if not typed:
        return {"ok": False, "status": "failed", "error": "关键词没能输入搜索框",
                "keyword": keyword, "notes": notes}
    btn = _find_exact(_ocr(ph), "搜索")
    if btn:
        _click_item(ph, btn)
    else:
        _tap(win32con.VK_RETURN)
    time.sleep(3.0)

    ph, prect = drv.current_page(main)
    texts = [it["text"] for it in _ocr(ph)]
    notes.append("result=" + "|".join(t[:12] for t in texts[:8]))
    if any("找不到" in t or "无法找到" in t or "不存在" in t for t in texts):
        return {"ok": False, "status": "not_found", "keyword": keyword, "notes": notes}
    if any(t.strip() in ("发消息", "语音聊天", "视频聊天") for t in texts):
        return {"ok": True, "status": "already_friend", "keyword": keyword, "notes": notes}
    btn = next((it for it in _ocr(ph) if "添加到通讯录" in it["text"]), None)
    if not btn:
        return {"ok": False, "status": "failed", "error": "结果页无法识别", "keyword": keyword, "notes": notes}

    _click_item(ph, btn)
    time.sleep(1.8)
    page = _await_page("申请添加朋友", timeout=3.0)
    if page:
        ah, arect = page
    else:
        ah, arect = drv.current_page(main)
    notes.append(f"apply_page={win32gui.GetWindowText(ah)!r} rect={arect}")
    if not submit:
        return {"ok": True, "status": "pending_submit", "keyword": keyword, "notes": notes}

    # 申请页布局（真机标定）：打招呼语输入框 ≈ 页面上 23% 处，按钮在最底部
    if hello:
        cx = int((arect[0] + arect[2]) / 2)
        cy = int(arect[1] + (arect[3] - arect[1]) * 0.23)
        drv.click_checked(ah, cx, cy)
        time.sleep(0.4)
        _ctrl(0x41)
        if not _type_text(hello):
            _paste_text(hello)
        time.sleep(0.6)
        notes.append("hello=" + (hello[:12]))
    page = drv.page_by_title("申请添加朋友")
    if page:
        ah, arect = page["hwnd"], drv.bring_page_front(page["hwnd"])
    btn = _find_exact(_ocr(ah), "确定") or _find_exact(_ocr(ah), "发送")
    if btn:
        _click_item(ah, btn)
    else:
        # 兜底：申请页底部左起约 1/3 处就是绿色「确定」
        cx = int(arect[0] + (arect[2] - arect[0]) * 0.32)
        cy = int(arect[3] - 26)
        drv.click_checked(ah, cx, cy)
        notes.append(f"fallback_click=({cx},{cy})")
    time.sleep(2.5)
    closed = not win32gui.IsWindow(ah)
    texts = [it["text"] for it in _ocr(ah)] if not closed else []
    ok = closed or any(("已发送" in t) or ("等待验证" in t) for t in texts)
    drv.unset_topmost(main)          # 置顶只是点击兜底，别把用户窗口钉在最前
    return {"ok": True, "status": "submitted", "verified": ok, "keyword": keyword,
            "notes": notes + [f"apply_closed={closed}"]}


# ------------------------------------------------------------------ 发朋友圈

def _open_moments(hwnd: int, rect: Tuple[int, int, int, int]) -> bool:
    l, t, r, b = rect
    for _ in range(2):
        _rail(hwnd, rect, RAIL_DISCOVER_Y)
        time.sleep(1.0)
        items = _ocr(hwnd)
        hit = next((it for it in items if it["text"].rstrip().endswith("朋友圈")), None)
        if not hit:
            continue
        _click_item(hwnd, hit)
        time.sleep(2.4)
        # 判据放宽：标题「朋友圈」常被 OCR 读错（真机读成「良试」），
        # 只要右侧内容区出现文字就认为朋友圈页已打开（发现面板本身只在左侧 x<0.25w）
        items = _ocr(hwnd)
        if any((it["left"] - l) > (r - l) * 0.55 for it in items):
            return True
    return False


def _pick_files(dialog_timeout: float = 6.0) -> Optional[int]:
    deadline = time.time() + dialog_timeout
    while time.time() < deadline:
        found: List[int] = []

        def _cb(h, _):
            try:
                if win32gui.IsWindowVisible(h) and win32gui.GetClassName(h) == "#32770" \
                        and "选择文件" in win32gui.GetWindowText(h):
                    found.append(h)
            except Exception:
                pass
            return True

        win32gui.EnumWindows(_cb, None)
        if found:
            return found[0]
        time.sleep(0.4)
    return None


def post_moments(
    images: Sequence[str],
    text: str = "",
    *,
    visibility: str = "public",
    submit: bool = True,
) -> Dict[str, Any]:
    """发朋友圈图文。

    images: 本地图片绝对路径（1..9 张）；visibility: public | private。
    submit=False 只走到编辑框（图片已插入、文案已填），不点发表。
    """
    hwnd = drv.find_wechat_hwnd()
    if not hwnd:
        return {"ok": False, "error": "没找到微信窗口"}
    paths = [os.path.abspath(p) for p in images]
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        return {"ok": False, "error": f"图片不存在: {missing[0]}"}
    rect = _focus(hwnd)
    drv.close_other_pages(hwnd)
    l, t, r, b = rect
    notes: List[str] = []

    if not _open_moments(hwnd, rect):
        return {"ok": False, "error": "没能打开朋友圈", "notes": notes}

    _ensure_chat_page(hwnd, rect)
    if not _open_moments(hwnd, rect):
        return {"ok": False, "error": "没能打开朋友圈(2)", "notes": notes}
    _camera_button(hwnd, rect)  # 相机
    dlg = _pick_files()
    if not dlg:
        return {"ok": False, "error": "选图对话框没出现", "notes": notes}
    try:
        win32gui.SetForegroundWindow(dlg)
    except Exception:
        pass
    time.sleep(0.4)
    _set_clipboard('" "'.join(paths).join(['"', '"']))
    _ctrl(0x41)
    _ctrl(0x56)
    time.sleep(0.4)
    _tap(win32con.VK_RETURN)
    time.sleep(3.0)
    if win32gui.IsWindow(dlg):
        return {"ok": False, "error": "选图对话框没关闭（路径没被接受）", "notes": notes}

    items = _ocr(hwnd)
    tip = next((it for it in items if "这一刻的想法" in it["text"]), None)
    if not tip:
        return {"ok": False, "error": "没进入朋友圈编辑页", "notes": notes}
    _click_item(hwnd, tip)
    time.sleep(0.4)
    if text:
        _paste_text(text)
        time.sleep(0.6)

    if visibility != "public":
        row = next((it for it in items if it["text"].strip() in ("公开", "谁可以看")), None)
        if row:
            _click_item(hwnd, row)
            time.sleep(0.9)
            priv = next((it for it in _ocr(hwnd) if it["text"].strip() == "私密"), None)
            if priv:
                _click_item(hwnd, priv)
                time.sleep(0.5)
                ok = _find_exact(_ocr(hwnd), "确定")
                if ok:
                    _click_item(hwnd, ok)
                    time.sleep(0.8)
                    notes.append("可见范围: 私密")
    if not submit:
        return {"ok": True, "submitted": False, "images": len(paths), "notes": notes}

    # 发表按钮在编辑窗里（4.2 的编辑窗是独立顶层窗，标题就叫「朋友圈」）
    comp = drv.page_by_title("朋友圈")
    ch = comp["hwnd"] if comp and comp["hwnd"] != hwnd else hwnd
    crect = drv.bring_page_front(ch) if ch != hwnd else rect
    pub = _find_exact(_ocr(ch), "发表")
    if not pub:
        return {"ok": False, "error": "找不到「发表」按钮", "notes": notes}
    drv.click_checked(ch, pub["cx"], pub["cy"])
    # 判据：编辑窗消失 = 已提交（真机验证：点发表后 composer alive=0）
    gone = _wait(lambda: not win32gui.IsWindow(ch), timeout=8.0, interval=0.5)
    notes.append(f"composer_closed={gone}")
    if not gone:
        return {"ok": False, "error": "点了发表但编辑窗没关闭", "notes": notes}
    return {"ok": True, "submitted": True, "verified": True, "images": len(paths), "notes": notes}


# ------------------------------------------------------------------ CLI

def _main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="微信 OCR 动作层（UIA 失效时的兜底）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("send", help="回消息")
    p1.add_argument("--chat", required=True)
    p1.add_argument("--text", required=True)
    p1.add_argument("--dry", action="store_true", help="只切会话不发")

    p2 = sub.add_parser("add", help="加好友")
    p2.add_argument("--keyword", required=True, help="微信号 / 手机号")
    p2.add_argument("--hello", default="")
    p2.add_argument("--remark", default="")
    p2.add_argument("--submit", action="store_true", help="真的发送好友申请")

    p3 = sub.add_parser("moments", help="发朋友圈图文")
    p3.add_argument("--image", action="append", required=True)
    p3.add_argument("--text", default="")
    p3.add_argument("--private", action="store_true")
    p3.add_argument("--submit", action="store_true", help="真的点发表")

    args = ap.parse_args(list(argv) if argv is not None else None)
    if args.cmd == "send":
        res = send_text(args.chat, args.text, submit=not args.dry)
    elif args.cmd == "add":
        res = add_friend(args.keyword, hello=args.hello, remark=args.remark, submit=args.submit)
    else:
        res = post_moments(args.image, args.text,
                           visibility="private" if args.private else "public",
                           submit=args.submit)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_main())