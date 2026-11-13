"""本地微信视频号 UI 自动化驱动。

只做只读采集：搜索视频、视频评论、账号搜索结果、作者主页。
不点赞、不关注、不发评论、不主动私信。
所有点击优先 UIA 控件；只有搜索子 tab（全部/视频/账号/直播）
没有暴露 UIA 时，才使用窗口内相对坐标，并在点击前后校验页面状态。
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import uiautomation as auto
import win32api
import win32con
import win32gui
import win32process

from backend.app.services import native_wechat_engine as eng

from .runtime import CHANNELS_UI_DUMP_DIR


class ChannelsUiError(RuntimeError):
    pass


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _rect(node: Any) -> Optional[Tuple[int, int, int, int]]:
    r = eng._uia_rect(node)
    if not r:
        return None
    return tuple(int(v) for v in r)  # type: ignore[return-value]


def _text(node: Any) -> str:
    return str(eng._uia_control_text(node) or "").strip()


def _cls(node: Any) -> str:
    return str(eng._uia_control_class(node) or "")


def _type(node: Any) -> str:
    return str(getattr(node, "ControlTypeName", "") or "")


def _desc_text(node: Any, max_depth: int = 6, max_nodes: int = 80) -> str:
    parts: List[str] = []
    for child in eng._uia_walk(node, max_depth=max_depth, max_nodes=max_nodes):
        value = _text(child)
        if value and value not in parts:
            parts.append(value)
    return " ".join(parts)


def _wait(predicate: Callable[[], Any], timeout: float = 6.0, interval: float = 0.25) -> Any:
    deadline = time.time() + max(0.2, float(timeout))
    last_exc: Optional[Exception] = None
    while time.time() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except Exception as exc:
            last_exc = exc
        time.sleep(interval)
    if last_exc:
        raise ChannelsUiError(str(last_exc)[:300])
    return None


def _dump_on_error(tag: str) -> str:
    folder = CHANNELS_UI_DUMP_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{tag}"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import ImageGrab

        hwnd = channels_hwnd(required=False)
        if hwnd:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            ImageGrab.grab(bbox=(left, top, right, bottom)).save(folder / "window.png")
            root = auto.ControlFromHandle(hwnd)
            rows = []
            for node in eng._uia_walk(root, max_depth=36, max_nodes=20000):
                name = _text(node)
                rect = _rect(node)
                if name or rect:
                    rows.append({"name": name[:300], "class": _cls(node), "type": _type(node), "rect": rect})
            (folder / "uia.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:
        (folder / "error.txt").write_text(str(exc), encoding="utf-8")
    return str(folder)


def _activate_hwnd(hwnd: int) -> bool:
    if not hwnd or not win32gui.IsWindow(hwnd):
        return False
    try:
        eng._focus_local_wechat(int(hwnd))
    except Exception:
        pass
    if win32gui.GetForegroundWindow() == int(hwnd):
        return True
    try:
        win32gui.ShowWindow(int(hwnd), win32con.SW_RESTORE)
        win32gui.BringWindowToTop(int(hwnd))
        win32gui.SetForegroundWindow(int(hwnd))
    except Exception:
        pass
    return win32gui.GetForegroundWindow() == int(hwnd)


def _doc_names(hwnd: int) -> List[str]:
    try:
        root = auto.ControlFromHandle(hwnd)
    except Exception:
        return []
    names: List[str] = []
    for node in eng._uia_walk(root, max_depth=12, max_nodes=4000):
        if _type(node) == "DocumentControl":
            name = _text(node)
            if name and name not in names:
                names.append(name)
    return names


def main_window() -> Dict[str, Any]:
    wins = eng._scan_local_wechat_windows()
    if not wins:
        raise ChannelsUiError("微信主窗口未找到，请先登录微信 PC 端")
    wins.sort(key=lambda row: int(row.get("area") or 0), reverse=True)
    return wins[0]


def channels_hwnd(*, required: bool = True) -> Optional[int]:
    candidates: List[Tuple[int, List[str], int]] = []
    main_hwnd = int(main_window().get("hwnd") or 0)

    def callback(win: int, _extra: Any) -> None:
        if not win32gui.IsWindowVisible(win):
            return
        if win == main_hwnd:
            return
        try:
            cls = str(win32gui.GetClassName(win) or "")
            title = str(win32gui.GetWindowText(win) or "")
        except Exception:
            return
        if cls != "Chrome_WidgetWin_0":
            return
        if title and title not in ("微信", "WeChat", "Weixin"):
            return
        names = _doc_names(win)
        if not any(("视频号" in name) or ("搜一搜" in name) or ("赞和收藏" in name) for name in names):
            return
        score = 0
        if any(name == "视频号" or name.startswith("视频号") for name in names):
            score += 3
        if any("搜一搜" in name for name in names):
            score += 2
        if any("赞和收藏" in name for name in names):
            score += 1
        candidates.append((win, names, score))

    win32gui.EnumWindows(callback, None)
    if not candidates:
        if required:
            raise ChannelsUiError("视频号窗口未打开，请先在微信左侧点『视频号』")
        return None
    candidates.sort(key=lambda item: (item[2], len(item[1])), reverse=True)
    return int(candidates[0][0])


def active_doc_name(hwnd: Optional[int] = None) -> str:
    target = hwnd or channels_hwnd()
    names = _doc_names(int(target))
    return names[0] if names else ""


def open_channels() -> Dict[str, Any]:
    main = main_window()
    main_hwnd = int(main["hwnd"])
    _activate_hwnd(main_hwnd)
    root = eng._uia_main_root(main_hwnd)
    tab = eng._find_tab_item(root, ["视频号"])
    if not tab:
        raise ChannelsUiError("微信左侧栏未找到『视频号』入口")
    eng._click_uia_node(tab)
    hwnd = _wait(lambda: channels_hwnd(required=False), timeout=6.0)
    if not hwnd:
        raise ChannelsUiError("点击视频号后未出现视频号窗口")
    _activate_hwnd(int(hwnd))
    ensure_feed_tab(int(hwnd))
    return {"ok": True, "hwnd": int(hwnd), "doc": active_doc_name(int(hwnd))}


def _tabs(hwnd: int) -> List[Any]:
    root = auto.ControlFromHandle(hwnd)
    tabs = [node for node in eng._uia_walk(root, max_depth=18, max_nodes=5000) if _cls(node) == "Tab"]
    tabs.sort(key=lambda node: (_rect(node) or (0, 0, 0, 0))[0])
    return tabs


def ensure_feed_tab(hwnd: Optional[int] = None) -> bool:
    target = int(hwnd or channels_hwnd())
    _activate_hwnd(target)
    doc = active_doc_name(target)
    if doc == "视频号" or doc.startswith("视频号"):
        return True
    for tab in _tabs(target):
        try:
            eng._click_uia_node(tab)
            time.sleep(0.7)
            doc = active_doc_name(target)
            if doc == "视频号" or doc.startswith("视频号"):
                return True
        except Exception:
            continue
    raise ChannelsUiError("未能切回视频号推荐流")


def bring_to_front() -> Dict[str, Any]:
    hwnd = channels_hwnd()
    ok = _activate_hwnd(int(hwnd))
    return {"ok": bool(ok), "hwnd": int(hwnd), "doc": active_doc_name(int(hwnd))}

def _find_all(root: Any, *, class_contains: str = "", name: str = "", type_contains: str = "", max_depth: int = 34, max_nodes: int = 20000) -> List[Any]:
    out = []
    for node in eng._uia_walk(root, max_depth=max_depth, max_nodes=max_nodes):
        if class_contains and class_contains not in _cls(node):
            continue
        if type_contains and type_contains not in _type(node):
            continue
        if name and _text(node) != name:
            continue
        out.append(node)
    return out


def _find_first(root: Any, *, class_contains: str = "", name: str = "", type_contains: str = "", max_depth: int = 34, max_nodes: int = 20000) -> Optional[Any]:
    for node in _find_all(root, class_contains=class_contains, name=name, type_contains=type_contains, max_depth=max_depth, max_nodes=max_nodes):
        return node
    return None


def _click(node: Any, *, label: str = "") -> None:
    if node is None:
        raise ChannelsUiError(f"控件不存在：{label}")
    try:
        eng._click_uia_node(node)
    except Exception as exc:
        raise ChannelsUiError(f"点击失败：{label} {exc}") from exc


def _foreground_channels(hwnd: int) -> None:
    if not _activate_hwnd(hwnd):
        raise ChannelsUiError("无法把视频号窗口切到前台")
    if win32gui.GetForegroundWindow() != hwnd:
        raise ChannelsUiError("前台窗口不是视频号，停止操作")


def _wheel(x: int, y: int, notches: int = 6) -> None:
    win32api.SetCursorPos((int(x), int(y)))
    for _ in range(max(1, abs(int(notches)))):
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, 120 if notches > 0 else -120, 0)
        time.sleep(0.06)


def _search_icon(root: Any) -> Optional[Any]:
    for node in _find_all(root, name="搜索", type_contains="Button", max_depth=30, max_nodes=12000):
        rect = _rect(node)
        if rect and rect[1] < 120:
            return node
    return None


def _feed_search_edit(root: Any) -> Optional[Any]:
    for node in _find_all(root, name="搜索", type_contains="Edit", max_depth=30, max_nodes=12000):
        if _rect(node):
            return node
    return None


def _card_key(card: Dict[str, Any]) -> str:
    return "|".join(str(card.get(k) or "") for k in ("title", "author", "duration", "date"))


def _extract_cards(root: Any) -> List[Dict[str, Any]]:
    cards = []
    for node in _find_all(root, class_contains="rich-media", type_contains="ListItem", max_depth=34, max_nodes=20000):
        rect = _rect(node)
        if not rect or rect[1] < 120:
            continue
        row: Dict[str, Any] = {"rect": rect, "raw": _text(node)}
        for child in eng._uia_walk(node, max_depth=8, max_nodes=120):
            cls = _cls(child)
            value = _desc_text(child, max_depth=4, max_nodes=30)
            if "rich-media__title" in cls and value:
                row["title"] = value
            elif "rich-media__source__title" in cls and value:
                row["author"] = value
            elif "rich-media__source__dateTime" in cls and value:
                row["date"] = value
            elif "video-player-mask__text" in cls and re.fullmatch(r"\d{1,2}:\d{2}", value):
                row["duration"] = value
        like = ""
        for child in eng._uia_walk(node, max_depth=8, max_nodes=120):
            value = _text(child)
            r = _rect(child)
            if value and r and r[0] > rect[2] - 50 and rect[3] - 125 <= r[1] <= rect[3] - 70:
                like = value
        row["like"] = like
        row["key"] = _card_key(row)
        if row.get("title") or row.get("raw"):
            cards.append(row)
    seen = set()
    unique = []
    for row in cards:
        dedupe = (row.get("key"), tuple(row.get("rect") or ()))
        if dedupe in seen:
            continue
        seen.add(dedupe)
        unique.append(row)
    return unique


def _merge_cards(target: Dict[str, Dict[str, Any]], rows: List[Dict[str, Any]]) -> int:
    added = 0
    for row in rows:
        key = row.get("key") or row.get("raw") or ""
        if not key or key in target:
            continue
        target[key] = row
        added += 1
    return added


def search_videos(keyword: str, *, max_results: int = 20, max_scroll_rounds: int = 6) -> Dict[str, Any]:
    keyword = str(keyword or "").strip()
    if not keyword:
        raise ChannelsUiError("搜索关键词不能为空")
    open_channels()
    hwnd = channels_hwnd()
    _foreground_channels(hwnd)
    root = auto.ControlFromHandle(hwnd)
    icon = _search_icon(root)
    if not icon:
        raise ChannelsUiError("视频号推荐流未找到搜索按钮")
    _click(icon, label="搜索按钮")
    edit = _wait(lambda: _feed_search_edit(root), timeout=3.0)
    if not edit:
        raise ChannelsUiError("视频号搜索输入框未出现")
    _click(edit, label="搜索输入框")
    eng._paste_text(keyword)
    time.sleep(0.25)
    auto.SendKeys("{Enter}", waitTime=0.05)
    _wait(lambda: "搜一搜" in active_doc_name(hwnd), timeout=8.0)

    root = auto.ControlFromHandle(hwnd)
    collected: Dict[str, Dict[str, Any]] = {}
    _merge_cards(collected, _extract_cards(root))
    stale_rounds = 0
    for _ in range(max(1, max_scroll_rounds)):
        if len(collected) >= max_results:
            break
        cards = _extract_cards(root)
        if cards:
            rect = cards[0]["rect"]
            _wheel((rect[0] + rect[2]) // 2, min(700, rect[1] + 300), 6)
        else:
            _wheel(700, 600, 6)
        time.sleep(1.2)
        root = auto.ControlFromHandle(hwnd)
        added = _merge_cards(collected, _extract_cards(root))
        stale_rounds = stale_rounds + 1 if added == 0 else 0
        if stale_rounds >= 3:
            break
    items = list(collected.values())[:max(1, int(max_results))]
    return {"keyword": keyword, "items": items, "count": len(items), "doc": active_doc_name(hwnd)}

def _feed_group(root: Any) -> Optional[Any]:
    best = None
    for node in _find_all(root, class_contains="flow-feed-", max_depth=30, max_nodes=16000):
        rect = _rect(node)
        if not rect:
            continue
        if rect[1] <= 120 and rect[3] >= 900:
            if best is None or rect[3] - rect[1] > (_rect(best)[3] - _rect(best)[1]):
                best = node
    return best


def _video_detail(root: Any) -> Dict[str, Any]:
    detail: Dict[str, Any] = {}
    feed = _feed_group(root)
    if feed:
        for child in eng._uia_walk(feed, max_depth=12, max_nodes=1200):
            cls = _cls(child)
            name = _text(child)
            rect = _rect(child)
            if not rect:
                continue
            if "mr-3 h-10 w-10" in cls and name:
                detail["author"] = name
            elif name.startswith("喜欢，") or name.startswith("推荐，") or name.startswith("评论，") or name.startswith("分享，"):
                key = name.split("，", 1)[0]
                detail[key] = name.split("，", 1)[1]
            elif rect[1] > 900 and rect[0] < 1000 and len(name) > 8:
                detail.setdefault("title", name)
    return detail


def open_first_video() -> Dict[str, Any]:
    hwnd = channels_hwnd()
    _foreground_channels(hwnd)
    root = auto.ControlFromHandle(hwnd)
    cards = _extract_cards(root)
    if not cards:
        raise ChannelsUiError("搜索结果为空，无法打开视频")
    cards_nodes = _find_all(root, class_contains="rich-media", type_contains="ListItem", max_depth=34, max_nodes=20000)
    _click(cards_nodes[0], label="第一条视频")
    _wait(lambda: _feed_group(auto.ControlFromHandle(hwnd)), timeout=6.0)
    root = auto.ControlFromHandle(hwnd)
    return {"ok": True, "detail": _video_detail(root)}


def _comment_button(root: Any) -> Optional[Any]:
    for node in _find_all(root, type_contains="Button", max_depth=34, max_nodes=20000):
        if _text(node).startswith("评论，"):
            return node
    return None


def _comment_panel(root: Any) -> Optional[Any]:
    return _find_first(root, class_contains="comment-panel", max_depth=30, max_nodes=16000)


def _extract_comments(root: Any) -> List[Dict[str, Any]]:
    names: List[Tuple[int, str]] = []
    contents: List[Tuple[int, int, str]] = []
    likes: Dict[int, str] = {}
    for node in eng._uia_walk(root, max_depth=36, max_nodes=22000):
        cls = _cls(node)
        rect = _rect(node)
        if not rect or rect[0] < 1250 or rect[1] >= 990:
            continue
        if "comment-user-name" in cls:
            value = _desc_text(node, max_depth=4, max_nodes=20)
            if value:
                names.append((rect[1], value))
        elif cls == "comment-content":
            value = _desc_text(node, max_depth=4, max_nodes=20)
            if value:
                contents.append((rect[1], rect[0], value))
        elif "like-num" in cls:
            value = _desc_text(node, max_depth=3, max_nodes=10)
            if value:
                likes[rect[1]] = value
    rows = []
    for cy, cx, content in contents:
        candidates = [(ny, nv) for ny, nv in names if ny <= cy and cy - ny <= 90]
        if not candidates:
            continue
        ny, nickname = max(candidates, key=lambda item: item[0])
        like = ""
        for ly, lv in likes.items():
            if abs(ly - cy) <= 35:
                like = lv
                break
        rows.append({"nickname": nickname, "content": content, "like": like, "y": cy})
    return rows


def collect_comments(*, max_comments: int = 100, max_scroll_rounds: int = 8) -> Dict[str, Any]:
    hwnd = channels_hwnd()
    _foreground_channels(hwnd)
    root = auto.ControlFromHandle(hwnd)
    if not _comment_panel(root):
        button = _comment_button(root)
        if not button:
            open_first_video()
            root = auto.ControlFromHandle(hwnd)
            button = _comment_button(root)
        if not button:
            raise ChannelsUiError("视频详情未找到评论按钮")
        _click(button, label="评论按钮")
    _wait(lambda: _comment_panel(auto.ControlFromHandle(hwnd)), timeout=5.0)
    root = auto.ControlFromHandle(hwnd)
    collected: Dict[str, Dict[str, Any]] = {}
    for row in _extract_comments(root):
        key = f"{row.get('nickname')}|{row.get('content')}"
        collected[key] = {**row, "key": key}
    stale = 0
    for _ in range(max(0, max_scroll_rounds)):
        if len(collected) >= max_comments:
            break
        panel = _comment_panel(root)
        rect = _rect(panel) if panel else None
        if rect:
            _wheel((rect[0] + rect[2]) // 2, max(rect[1] + 250, 500), 6)
        else:
            _wheel(1420, 650, 6)
        time.sleep(1.5)
        root = auto.ControlFromHandle(hwnd)
        added = 0
        for row in _extract_comments(root):
            key = f"{row.get('nickname')}|{row.get('content')}"
            if key not in collected:
                collected[key] = {**row, "key": key}
                added += 1
        stale = stale + 1 if added == 0 else 0
        if stale >= 2:
            break
    items = list(collected.values())[:max(1, int(max_comments))]
    detail = _video_detail(root)
    return {"items": items, "count": len(items), "detail": detail}

def _search_page_input(root: Any) -> Optional[Any]:
    for node in eng._uia_walk(root, max_depth=28, max_nodes=16000):
        if _type(node) == "EditControl" and (str(getattr(node, "AutomationId", "") or "") == "weixin-search-input" or _text(node) == "搜索"):
            if _rect(node):
                return node
    return None


def _scroll_top(x: int = 700, y: int = 400, notches: int = 40) -> None:
    win32api.SetCursorPos((int(x), int(y)))
    for _ in range(max(1, int(notches))):
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, 120, 0)
        time.sleep(0.03)


def _click_accounts_tab(hwnd: int) -> None:
    _foreground_channels(hwnd)
    root = auto.ControlFromHandle(hwnd)
    edit = _search_page_input(root)
    if not edit:
        raise ChannelsUiError("搜索结果页未找到搜索框，无法切换到账号 tab")
    rect = _rect(edit)
    if not rect:
        raise ChannelsUiError("搜索框没有坐标")
    _scroll_top(rect[0] + 120, rect[1] + 60)
    time.sleep(0.5)
    auto.SendKeys("{Esc}", waitTime=0.05)
    time.sleep(0.3)
    # 搜索页子 tab 没有 UIA：全部/视频/账号/直播 相对搜索框固定偏移
    x = rect[0] + 142
    y = rect[3] + 22
    win32api.SetCursorPos((int(x), int(y)))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    def _accounts_visible() -> bool:
        current = auto.ControlFromHandle(hwnd)
        return bool(_find_all(current, class_contains="video-account__container", type_contains="ListItem", max_depth=34, max_nodes=18000))
    _wait(_accounts_visible, timeout=5.0)


def extract_contacts(text: str) -> Dict[str, List[str]]:
    value = str(text or "")
    phones = []
    for match in re.findall(r"(?<!\d)(1[3-9]\d{9})(?!\d)", value):
        if match not in phones:
            phones.append(match)
    wechats = []
    patterns = (
        r"(?:微信|V信|v信|VX|vx|WX|wx|加V|加v|同V|同v|V|v)\s*[:：]?\s*([A-Za-z][-_A-Za-z0-9]{5,19})",
        r"[:：]\s*([A-Za-z][-_A-Za-z0-9]{5,19})",
    )
    for pattern in patterns:
        for match in re.findall(pattern, value):
            if match not in wechats and not match.lower().startswith(("http", "www")):
                wechats.append(match)
    qqs = []
    for match in re.findall(r"(?:QQ|qq)\s*[:：]?\s*([1-9]\d{4,11})", value):
        if match not in qqs:
            qqs.append(match)
    return {"phones": phones, "wechats": wechats, "qqs": qqs}


def _extract_accounts(root: Any) -> List[Dict[str, Any]]:
    rows = []
    for node in _find_all(root, class_contains="video-account__container", type_contains="ListItem", max_depth=36, max_nodes=24000):
        rect = _rect(node)
        if not rect or rect[1] < 120:
            continue
        row: Dict[str, Any] = {"rect": rect, "raw": _text(node)}
        for child in eng._uia_walk(node, max_depth=8, max_nodes=120):
            cls = _cls(child)
            value = _desc_text(child, max_depth=4, max_nodes=30)
            if "video-account__name__text" in cls and value:
                row["name"] = value
            elif "video-account__des" in cls and value:
                row["bio"] = value
            elif "video-account__cert" in cls and value:
                row["cert"] = value
            elif value and re.fullmatch(r"\d+个朋友关注", value):
                row["friends"] = value
            elif value and re.fullmatch(r"\d+条视频", value):
                row["video_count"] = value
            elif value and ("更新" in value or "开播" in value):
                row["updated"] = value
        bio = str(row.get("bio") or "")
        row["contacts"] = extract_contacts(bio)
        row["key"] = f"{row.get('name') or ''}|{bio[:80]}"
        if row.get("name") or bio:
            rows.append(row)
    seen = set()
    unique = []
    for row in rows:
        key = row.get("key")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def search_accounts(keyword: str, *, max_results: int = 20, max_scroll_rounds: int = 6) -> Dict[str, Any]:
    keyword = str(keyword or "").strip()
    if not keyword:
        raise ChannelsUiError("搜索关键词不能为空")
    search_videos(keyword, max_results=1, max_scroll_rounds=0)
    hwnd = channels_hwnd()
    _click_accounts_tab(hwnd)
    root = auto.ControlFromHandle(hwnd)
    collected: Dict[str, Dict[str, Any]] = {}
    for row in _extract_accounts(root):
        collected[row["key"]] = row
    stale = 0
    for _ in range(max(1, max_scroll_rounds)):
        if len(collected) >= max_results:
            break
        cards = _extract_accounts(root)
        if cards:
            rect = cards[0]["rect"]
            _wheel((rect[0] + rect[2]) // 2, min(700, rect[1] + 250), 6)
        else:
            _wheel(700, 600, 6)
        time.sleep(1.2)
        root = auto.ControlFromHandle(hwnd)
        before = len(collected)
        for row in _extract_accounts(root):
            collected[row["key"]] = row
        added = len(collected) - before
        stale = stale + 1 if added == 0 else 0
        if stale >= 3:
            break
    items = list(collected.values())[:max(1, int(max_results))]
    return {"keyword": keyword, "items": items, "count": len(items), "doc": active_doc_name(hwnd)}


def status() -> Dict[str, Any]:
    result: Dict[str, Any] = {"ok": False, "main_window": False, "channels_window": False, "doc": "", "error": ""}
    try:
        main = main_window()
        result["main_window"] = True
        result["main_hwnd"] = int(main.get("hwnd") or 0)
        result["version"] = str(main.get("version") or "")
    except Exception as exc:
        result["error"] = str(exc)[:300]
        return result
    try:
        hwnd = channels_hwnd(required=False)
        if hwnd:
            result["channels_window"] = True
            result["hwnd"] = int(hwnd)
            result["doc"] = active_doc_name(int(hwnd))
        result["ok"] = True
    except Exception as exc:
        result["error"] = str(exc)[:300]
    return result

def _find_card_node(root: Any, card_key: str) -> Optional[Any]:
    rows = _extract_cards(root)
    target = next((row for row in rows if row.get("key") == card_key), None)
    if not target:
        parts = str(card_key or "").split("|")
        title = parts[0] if parts else ""
        author = parts[1] if len(parts) > 1 else ""
        duration = parts[2] if len(parts) > 2 else ""
        candidates = []
        for row in rows:
            row_title = str(row.get("title") or "")
            row_author = str(row.get("author") or "")
            row_duration = str(row.get("duration") or "")
            score = 0
            if title and (row_title == title or row_title.startswith(title[:18]) or title.startswith(row_title[:18])):
                score += 3
            if author and row_author == author:
                score += 2
            if duration and row_duration == duration:
                score += 1
            if score >= 3:
                candidates.append((score, row))
        if candidates:
            target = max(candidates, key=lambda item: item[0])[1]
    if not target:
        return None
    target_rect = tuple(target.get("rect") or ())
    for node in _find_all(root, class_contains="rich-media", type_contains="ListItem", max_depth=34, max_nodes=20000):
        if tuple(_rect(node) or ()) == target_rect:
            return node
    return None


def collect_comments_for_card(keyword: str, card_key: str, *, max_comments: int = 100) -> Dict[str, Any]:
    """重新搜索关键词，定位指定卡片，打开并采集评论。"""
    search_videos(keyword, max_results=30, max_scroll_rounds=5)
    hwnd = channels_hwnd()
    _foreground_channels(hwnd)
    root = auto.ControlFromHandle(hwnd)
    node = _find_card_node(root, card_key)
    if node is None:
        raise ChannelsUiError("搜索结果里找不到目标视频，可能已下架或排序变化")
    _click(node, label="目标视频卡片")
    _wait(lambda: _feed_group(auto.ControlFromHandle(hwnd)), timeout=6.0)
    return collect_comments(max_comments=max_comments)
