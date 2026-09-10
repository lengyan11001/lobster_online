from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx

from ..core.config import settings


ROOT_DIR = Path(__file__).resolve().parents[3]
STATE_DIR = ROOT_DIR / "data" / "native_whatsapp"
CONFIG_PATH = STATE_DIR / "config.json"
DB_PATH = STATE_DIR / "state.db"
LOG_PATH = ROOT_DIR / "logs" / "native_whatsapp.jsonl"
DEFAULT_ACCOUNT_ID = "desktop-whatsapp-default"
WHATSAPP_WINDOW_CLASS = "WinUIDesktopWin32WindowClass"
WHATSAPP_PROCESS_NAMES = {"whatsapp.root.exe", "whatsapp.exe"}

_UI_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="lobster-whatsapp")
_ACTIVE_LOCK = threading.Lock()
_ACTIVE = False
_ACTIVE_ACTION = ""
_STOP_REQUESTED = threading.Event()
_DB_LOCK = threading.RLock()

_SKIP_TEXT = {
    "WhatsApp",
    "聊天",
    "对话",
    "通话",
    "动态",
    "频道",
    "社群",
    "所有",
    "未读",
    "特别关注",
    "菜单",
    "搜索或开始新聊天",
    "发送文档",
    "添加联系人",
}
_SYSTEM_MESSAGE_MARKERS = (
    "消息和通话均受端到端加密保护",
    "端到端加密",
    "security code",
    "messages and calls are end-to-end encrypted",
    "created group",
    "加入了群组",
    "退出了群组",
    "changed the subject",
    "未接来电",
    "missed voice call",
    "missed video call",
)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _append_log(event: str, **fields: Any) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"at": _now_iso(), "event": event, **fields}
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception:
        logging.getLogger(__name__).debug("failed to write native WhatsApp log", exc_info=True)


def _connect() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma journal_mode=wal")
    conn.execute("pragma busy_timeout=10000")
    conn.executescript(
        """
        create table if not exists whatsapp_sessions (
            id text primary key,
            account_id text not null,
            peer_key text not null,
            display_name text not null,
            chat_type text not null default 'direct',
            unread_count integer not null default 0,
            last_message text,
            last_direction text,
            raw_json text,
            created_at text not null,
            updated_at text not null,
            unique(account_id, peer_key)
        );
        create index if not exists idx_whatsapp_sessions_account_updated
        on whatsapp_sessions(account_id, updated_at desc);

        create table if not exists whatsapp_messages (
            id text primary key,
            account_id text not null,
            peer_key text not null,
            direction text not null,
            content text not null,
            sequence_no integer not null default 0,
            observed_at text not null,
            raw_json text
        );
        create index if not exists idx_whatsapp_messages_peer
        on whatsapp_messages(account_id, peer_key, sequence_no asc);

        create table if not exists whatsapp_contacts (
            id text primary key,
            account_id text not null,
            contact_key text not null,
            display_name text not null,
            first_name text,
            last_name text,
            username text,
            phone text,
            country_code text,
            source text not null default 'desktop',
            raw_json text,
            created_at text not null,
            updated_at text not null,
            unique(account_id, contact_key)
        );
        create index if not exists idx_whatsapp_contacts_account_updated
        on whatsapp_contacts(account_id, updated_at desc);

        create table if not exists whatsapp_operations (
            id integer primary key autoincrement,
            account_id text not null,
            action text not null,
            target text,
            status text not null,
            message text,
            raw_json text,
            created_at text not null
        );
        create index if not exists idx_whatsapp_operations_account_time
        on whatsapp_operations(account_id, created_at desc);
        """
    )
    return conn


def _stable_key(prefix: str, value: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
    return f"{prefix}_{hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:24]}"


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _row_public(row: sqlite3.Row) -> Dict[str, Any]:
    result = dict(row)
    raw = result.pop("raw_json", None)
    if raw:
        try:
            result["raw"] = json.loads(raw)
        except (TypeError, ValueError):
            pass
    return result


def _record_operation(action: str, target: str, status_value: str, message: str = "", raw: Any = None) -> None:
    with _DB_LOCK, _connect() as conn:
        conn.execute(
            "insert into whatsapp_operations(account_id, action, target, status, message, raw_json, created_at) values(?,?,?,?,?,?,?)",
            (DEFAULT_ACCOUNT_ID, action, str(target or "")[:500], status_value, str(message or "")[:2000], _json_text(raw or {}), _now_iso()),
        )


def _query_page(table: str, *, limit: int, offset: int, keyword: str = "", where: str = "", params: tuple[Any, ...] = ()) -> Dict[str, Any]:
    safe_limit = max(1, min(int(limit or 50), 200))
    safe_offset = max(0, int(offset or 0))
    clauses = ["account_id=?"]
    values: List[Any] = [DEFAULT_ACCOUNT_ID]
    if where:
        clauses.append(where)
        values.extend(params)
    search = str(keyword or "").strip()
    if search:
        if table == "whatsapp_sessions":
            clauses.append("(display_name like ? or last_message like ?)")
            values.extend([f"%{search}%", f"%{search}%"])
        elif table == "whatsapp_contacts":
            clauses.append("(display_name like ? or username like ? or phone like ?)")
            values.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
        else:
            clauses.append("(target like ? or message like ? or action like ?)")
            values.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
    where_sql = " and ".join(clauses)
    order_sql = "updated_at desc" if table != "whatsapp_operations" else "created_at desc"
    with _DB_LOCK, _connect() as conn:
        total = int(conn.execute(f"select count(*) from {table} where {where_sql}", tuple(values)).fetchone()[0])
        rows = conn.execute(
            f"select * from {table} where {where_sql} order by {order_sql} limit ? offset ?",
            tuple([*values, safe_limit, safe_offset]),
        ).fetchall()
    return {"items": [_row_public(row) for row in rows], "total": total, "limit": safe_limit, "offset": safe_offset}


def _default_config() -> Dict[str, Any]:
    return {
        "account_id": DEFAULT_ACCOUNT_ID,
        "interval_seconds": 15,
        "takeover_session_minutes": 30,
        "max_unread_per_round": 50,
        "reply_instruction": "",
        "last_run": {},
    }


def get_config() -> Dict[str, Any]:
    result = _default_config()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            result.update(raw)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    result["account_id"] = DEFAULT_ACCOUNT_ID
    result["interval_seconds"] = max(1, min(int(result.get("interval_seconds") or 15), 300))
    result["takeover_session_minutes"] = max(1, min(int(result.get("takeover_session_minutes") or 30), 1440))
    result["max_unread_per_round"] = max(1, min(int(result.get("max_unread_per_round") or 50), 100))
    result["reply_instruction"] = str(result.get("reply_instruction") or "").strip()[:4000]
    if not isinstance(result.get("last_run"), dict):
        result["last_run"] = {}
    return result


def save_config(
    *,
    interval_seconds: Optional[int] = None,
    takeover_session_minutes: Optional[int] = None,
    max_unread_per_round: Optional[int] = None,
    reply_instruction: Optional[str] = None,
) -> Dict[str, Any]:
    cfg = get_config()
    if interval_seconds is not None:
        cfg["interval_seconds"] = max(1, min(int(interval_seconds), 300))
    if takeover_session_minutes is not None:
        cfg["takeover_session_minutes"] = max(1, min(int(takeover_session_minutes), 1440))
    if max_unread_per_round is not None:
        cfg["max_unread_per_round"] = max(1, min(int(max_unread_per_round), 100))
    if reply_instruction is not None:
        cfg["reply_instruction"] = str(reply_instruction or "").strip()[:4000]
    _write_config(cfg)
    return cfg


def _write_config(cfg: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temp = CONFIG_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(CONFIG_PATH)


def _module_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _process_meta(pid: int) -> Dict[str, Any]:
    try:
        import psutil  # type: ignore

        proc = psutil.Process(int(pid))
        return {"name": proc.name(), "exe": proc.exe()}
    except Exception:
        return {"name": "", "exe": ""}


def _scan_windows() -> List[Dict[str, Any]]:
    try:
        import win32gui  # type: ignore
        import win32process  # type: ignore
    except Exception:
        return []
    items: List[Dict[str, Any]] = []

    def collect(hwnd: int, _extra: Any) -> None:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = str(win32gui.GetWindowText(hwnd) or "").strip()
            class_name = str(win32gui.GetClassName(hwnd) or "").strip()
            _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
            meta = _process_meta(int(pid or 0))
            process_name = str(meta.get("name") or "").lower()
            if process_name not in WHATSAPP_PROCESS_NAMES and "whatsapp" not in process_name:
                return
            if class_name != WHATSAPP_WINDOW_CLASS and title.lower() != "whatsapp":
                return
            rect = tuple(int(value) for value in win32gui.GetWindowRect(hwnd))
            items.append(
                {
                    "account_id": DEFAULT_ACCOUNT_ID,
                    "hwnd": int(hwnd),
                    "pid": int(pid or 0),
                    "title": title or "WhatsApp",
                    "class_name": class_name,
                    "process_name": meta.get("name") or "",
                    "process_path": meta.get("exe") or "",
                    "is_iconic": bool(win32gui.IsIconic(hwnd)),
                    "rect": list(rect),
                }
            )
        except Exception:
            return

    win32gui.EnumWindows(collect, None)
    return sorted(items, key=lambda row: (row.get("title") == "WhatsApp", int(row.get("hwnd") or 0)), reverse=True)


def _iter_nodes(root: Any, *, max_depth: int = 24, max_nodes: int = 2400) -> Iterable[tuple[Any, int]]:
    queue: List[tuple[Any, int]] = [(root, 0)]
    seen = 0
    while queue and seen < max_nodes:
        node, depth = queue.pop(0)
        seen += 1
        yield node, depth
        if depth >= max_depth:
            continue
        try:
            queue.extend((child, depth + 1) for child in node.GetChildren())
        except Exception:
            pass


def _node_text(node: Any) -> str:
    try:
        return str(getattr(node, "Name", "") or "").strip()
    except Exception:
        return ""


def _node_aid(node: Any) -> str:
    try:
        return str(getattr(node, "AutomationId", "") or "").strip()
    except Exception:
        return ""


def _node_type(node: Any) -> str:
    try:
        return str(getattr(node, "ControlTypeName", "") or "")
    except Exception:
        return ""


def _rect(node: Any) -> Optional[tuple[float, float, float, float]]:
    try:
        raw = node.BoundingRectangle
        result = (float(raw.left), float(raw.top), float(raw.right), float(raw.bottom))
        if result[2] <= result[0] or result[3] <= result[1]:
            return None
        return result
    except Exception:
        return None


def _find_by_aid(root: Any, automation_id: str) -> Optional[Any]:
    expected = str(automation_id or "").strip().lower()
    matches: List[Any] = []
    for node, _depth in _iter_nodes(root):
        if _node_aid(node).lower() == expected and _rect(node):
            matches.append(node)
    return matches[-1] if matches else None


def _find_by_name(root: Any, names: Iterable[str], *, control_type: str = "") -> Optional[Any]:
    expected = {str(name or "").strip().lower() for name in names if str(name or "").strip()}
    for node, _depth in _iter_nodes(root):
        if _node_text(node).lower() not in expected:
            continue
        if control_type and _node_type(node) != control_type:
            continue
        if _rect(node):
            return node
    return None


def _root_for_hwnd(hwnd: int) -> Any:
    import uiautomation as auto  # type: ignore

    root = auto.ControlFromHandle(int(hwnd))
    candidates = [node for node, _depth in _iter_nodes(root, max_depth=14, max_nodes=400) if _node_aid(node) == "RootWebArea" and _rect(node)]
    return candidates[-1] if candidates else root


def _activate_window(hwnd: int) -> None:
    import win32con  # type: ignore
    import win32gui  # type: ignore

    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _click(node: Any) -> None:
    rect = _rect(node)
    if not rect:
        raise RuntimeError("WhatsApp 控件不可点击")
    import win32api  # type: ignore
    import win32con  # type: ignore

    x = int((rect[0] + rect[2]) / 2)
    y = int((rect[1] + rect[3]) / 2)
    win32api.SetCursorPos((x, y))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.08)


def _set_edit_text(node: Any, value: str) -> None:
    if node is None or not _rect(node):
        raise RuntimeError("WhatsApp 输入框不可用")
    _click(node)
    import pyperclip  # type: ignore
    from pywinauto.keyboard import send_keys  # type: ignore

    pyperclip.copy(str(value or ""))
    send_keys("^a", pause=0.02)
    send_keys("^v", pause=0.03)
    time.sleep(0.18)


def _claim_action(action: str) -> None:
    global _ACTIVE, _ACTIVE_ACTION
    with _ACTIVE_LOCK:
        if _ACTIVE:
            label = _ACTIVE_ACTION or "WhatsApp 操作"
            raise RuntimeError(f"{label}正在执行，请完成或停止后重试")
        _ACTIVE = True
        _ACTIVE_ACTION = str(action or "WhatsApp 操作")


def _release_action() -> None:
    global _ACTIVE, _ACTIVE_ACTION
    with _ACTIVE_LOCK:
        _ACTIVE = False
        _ACTIVE_ACTION = ""


def _window_or_raise() -> tuple[int, Dict[str, Any]]:
    windows = _scan_windows()
    if not windows:
        raise RuntimeError("未检测到 Windows 桌面版 WhatsApp，请先启动并登录")
    window = windows[0]
    hwnd = int(window.get("hwnd") or 0)
    if not hwnd:
        raise RuntimeError("WhatsApp 窗口句柄不可用")
    _activate_window(hwnd)
    return hwnd, window


def _click_named(root: Any, names: Iterable[str], *, control_type: str = "ButtonControl", required: bool = True) -> bool:
    expected = {str(name or "").strip().casefold() for name in names if str(name or "").strip()}
    candidates: List[Any] = []
    for node, _depth in _iter_nodes(root, max_depth=24, max_nodes=8000):
        if _node_text(node).strip().casefold() not in expected:
            continue
        if control_type and _node_type(node) != control_type:
            continue
        if _rect(node):
            candidates.append(node)
    if not candidates:
        if required:
            raise RuntimeError(f"未找到 WhatsApp 控件：{'/'.join(names)}")
        return False
    _click(candidates[-1])
    return True


def _probe_window(window: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(window)
    result.update({"uia_ready": False, "logged_in": False, "unread_count": 0, "reason": ""})
    try:
        root = _root_for_hwnd(int(window.get("hwnd") or 0))
        pane = _find_by_aid(root, "pane-side")
        unread = _find_by_aid(root, "label_item_1")
        result["uia_ready"] = bool(pane is not None and unread is not None)
        result["logged_in"] = result["uia_ready"]
        label = _node_text(unread) if unread is not None else ""
        numbers = re.findall(r"\d+", label)
        result["unread_count"] = int(numbers[-1]) if numbers else 0
        if not result["uia_ready"]:
            result["reason"] = "桌面 WhatsApp 已启动，但没有读取到已登录的会话控件"
    except Exception as exc:
        result["reason"] = f"WhatsApp UIA 探测失败：{exc}"
    return result


def status() -> Dict[str, Any]:
    deps = {name: _module_available(name) for name in ("uiautomation", "win32gui", "win32process", "pyperclip")}
    windows = _scan_windows()
    probed = _probe_window(windows[0]) if windows and deps["uiautomation"] else (windows[0] if windows else {})
    with _ACTIVE_LOCK:
        running = _ACTIVE
        active_action = _ACTIVE_ACTION
    return {
        "ok": bool(probed.get("uia_ready")),
        "desktop_found": bool(windows),
        "logged_in": bool(probed.get("logged_in")),
        "running": running,
        "active_action": active_action,
        "unread_count": int(probed.get("unread_count") or 0),
        "window": probed,
        "dependencies": deps,
        "reason": probed.get("reason") or ("未检测到 Windows 桌面版 WhatsApp" if not windows else ""),
        "config": get_config(),
    }


def _chat_rows(pane: Any) -> List[Dict[str, Any]]:
    pane_rect = _rect(pane)
    if not pane_rect:
        return []
    candidates: Dict[int, Dict[str, Any]] = {}
    for node, depth in _iter_nodes(pane, max_depth=12, max_nodes=1000):
        text = _node_text(node)
        rect = _rect(node)
        if not text or not rect or text in _SKIP_TEXT:
            continue
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        if height < 42 or height > 112 or width < (pane_rect[2] - pane_rect[0]) * 0.62:
            continue
        if rect[1] < pane_rect[1] - 2 or rect[3] > pane_rect[3] + 2:
            continue
        lowered = text.lower()
        if lowered in {"无对话", "no chats", "no unread chats"}:
            continue
        score = (20 if _node_type(node) in {"ListItemControl", "DataItemControl"} else 0) + int(width) + depth
        key = int(round(rect[1]))
        if key not in candidates or score > candidates[key]["score"]:
            candidates[key] = {"node": node, "name": text[:500], "rect": rect, "score": score}
    return [candidates[key] for key in sorted(candidates)]


def _visible_chat_rows(hwnd: int, *, unread_only: bool = False) -> List[Dict[str, Any]]:
    _activate_window(hwnd)
    root = _root_for_hwnd(hwnd)
    _click_named(root, ("对话", "Chats"), required=False)
    time.sleep(0.35)
    root = _root_for_hwnd(hwnd)
    tab = _find_by_aid(root, "label_item_1" if unread_only else "all-filter")
    if tab is not None:
        _click(tab)
        time.sleep(0.45)
    root = _root_for_hwnd(hwnd)
    pane = _find_by_aid(root, "pane-side")
    if pane is None:
        raise RuntimeError("未找到 WhatsApp 会话列表")
    rows = _chat_rows(pane)
    if rows:
        return rows
    # Some WhatsApp builds expose only text children for short/empty lists.
    pane_rect = _rect(pane)
    fallback: Dict[int, Dict[str, Any]] = {}
    if pane_rect:
        for node, _depth in _iter_nodes(pane, max_depth=14, max_nodes=1800):
            text = _node_text(node).strip()
            rect = _rect(node)
            if not text or not rect or text in _SKIP_TEXT or _is_system_message(text):
                continue
            if rect[0] < pane_rect[0] - 3 or rect[2] > pane_rect[2] + 3 or rect[1] < pane_rect[1] - 4:
                continue
            if text.casefold() in {"无对话", "no chats", "no unread chats"}:
                continue
            key = int(max(pane_rect[1], rect[1]) // 54)
            parent = node
            for _ in range(6):
                try:
                    candidate = parent.GetParentControl()
                except Exception:
                    break
                candidate_rect = _rect(candidate)
                if candidate_rect and 44 <= candidate_rect[3] - candidate_rect[1] <= 120 and candidate_rect[0] <= pane_rect[0] + 12:
                    parent = candidate
                else:
                    break
            fallback.setdefault(key, {"node": parent, "name": text[:500], "rect": _rect(parent) or rect, "score": 0})
    return [fallback[key] for key in sorted(fallback)]


def _select_unread_and_rows(hwnd: int) -> List[Dict[str, Any]]:
    return _visible_chat_rows(hwnd, unread_only=True)


def _is_system_message(text: str) -> bool:
    value = str(text or "").strip().lower()
    if not value or value in {item.lower() for item in _SKIP_TEXT}:
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}(?:\s*[ap]m)?", value, flags=re.I):
        return True
    return any(marker.lower() in value for marker in _SYSTEM_MESSAGE_MARKERS)


def _nearest_message_rect(node: Any, main_rect: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    current = node
    best = _rect(node) or main_rect
    for _ in range(7):
        try:
            current = current.GetParentControl()
        except Exception:
            break
        rect = _rect(current)
        if not rect:
            continue
        width = rect[2] - rect[0]
        height = rect[3] - rect[1]
        if 20 <= width <= (main_rect[2] - main_rect[0]) * 0.82 and 14 <= height <= 220:
            best = rect
    return best


def _conversation_snapshot(hwnd: int) -> Dict[str, Any]:
    root = _root_for_hwnd(hwnd)
    main = _find_by_aid(root, "main")
    main_rect = _rect(main) if main is not None else None
    if not main_rect:
        pane = _find_by_aid(root, "pane-side")
        pane_rect = _rect(pane) if pane is not None else None
        root_rect = _rect(root)
        if not root_rect or not pane_rect:
            raise RuntimeError("未读取到 WhatsApp 当前会话区域")
        main_rect = (pane_rect[2], root_rect[1], root_rect[2], root_rect[3])
        main = root

    edit_nodes = [
        node
        for node, _depth in _iter_nodes(main, max_depth=16, max_nodes=1800)
        if _node_type(node) in {"EditControl", "DocumentControl"}
        and _rect(node)
        and _rect(node)[1] > main_rect[1] + (main_rect[3] - main_rect[1]) * 0.55
    ]
    composer = max(edit_nodes, key=lambda node: _rect(node)[1], default=None)
    composer_rect = _rect(composer) if composer is not None else None
    message_bottom = (composer_rect[1] - 4) if composer_rect else (main_rect[3] - 55)

    header_texts: List[str] = []
    messages: List[Dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for node, _depth in _iter_nodes(main, max_depth=18, max_nodes=2200):
        text = _node_text(node)
        rect = _rect(node)
        if not text or not rect:
            continue
        if rect[3] <= main_rect[1] + 78 and text not in _SKIP_TEXT:
            header_texts.append(text[:240])
        if rect[1] <= main_rect[1] + 72 or rect[3] >= message_bottom:
            continue
        if _node_type(node) not in {"TextControl", "DocumentControl", "GroupControl"} or _is_system_message(text):
            continue
        if len(text) > 2000:
            continue
        bubble_rect = _nearest_message_rect(node, main_rect)
        center = (bubble_rect[0] + bubble_rect[2]) / 2
        main_center = (main_rect[0] + main_rect[2]) / 2
        if bubble_rect[2] >= main_rect[2] - 28 and bubble_rect[0] > main_rect[0] + 70:
            direction = "outbound"
        elif bubble_rect[0] <= main_rect[0] + 42 and bubble_rect[2] < main_rect[2] - 70:
            direction = "inbound"
        elif center > main_center + 38:
            direction = "outbound"
        elif center < main_center - 38:
            direction = "inbound"
        else:
            direction = "unknown"
        key = (text, int(rect[1]), int(rect[0]))
        if key in seen:
            continue
        seen.add(key)
        messages.append({"text": text[:2000], "direction": direction, "rect": list(rect), "bottom": rect[3]})
    messages.sort(key=lambda item: (item["bottom"], item["rect"][0]))

    explicit_group = any(
        marker in " ".join(_node_text(node) for node, _depth in _iter_nodes(main, max_depth=10, max_nodes=500)).lower()
        for marker in ("群组信息", "group info", "participants", "位参与者")
    )
    compact_header = list(dict.fromkeys(text for text in header_texts if text not in _SKIP_TEXT))
    subtitle = " ".join(compact_header[1:3])
    if re.search(r"[,，].+[,，]?", subtitle) and not re.search(r"最后上线|last seen|online|在线", subtitle, re.I):
        explicit_group = True
    peer_name = compact_header[0] if compact_header else ""
    return {
        "peer_name": peer_name,
        "is_group": explicit_group,
        "header": compact_header[:6],
        "messages": messages[-30:],
        "last_message": messages[-1] if messages else None,
        "composer_found": composer is not None,
        "composer": composer,
    }


def _open_first_unread(hwnd: int) -> Optional[Dict[str, Any]]:
    rows = _select_unread_and_rows(hwnd)
    if not rows:
        return None
    row = rows[0]
    _click(row["node"])
    time.sleep(0.65)
    snapshot = _conversation_snapshot(hwnd)
    snapshot["row_name"] = row["name"]
    return snapshot


def _send_current_message(hwnd: int, text: str) -> Dict[str, Any]:
    snapshot = _conversation_snapshot(hwnd)
    composer = snapshot.pop("composer", None)
    if composer is None:
        raise RuntimeError("未找到 WhatsApp 消息输入框")
    _click(composer)
    import pyperclip  # type: ignore
    from pywinauto.keyboard import send_keys  # type: ignore

    pyperclip.copy(str(text or ""))
    send_keys("^v", pause=0.03)
    time.sleep(0.15)
    send_keys("{ENTER}", pause=0.05)
    time.sleep(0.8)
    confirmed = _conversation_snapshot(hwnd)
    confirmed.pop("composer", None)
    latest = confirmed.get("last_message") if isinstance(confirmed.get("last_message"), dict) else {}
    sent = str(latest.get("direction") or "") == "outbound" and str(text or "").strip() in str(latest.get("text") or "")
    return {"sent": sent, "last_message": latest}


def _persist_session_snapshot(snapshot: Dict[str, Any], *, row_name: str = "", unread_count: int = 0) -> Dict[str, Any]:
    peer_name = str(snapshot.get("peer_name") or row_name or "未命名会话").strip()[:500]
    peer_key = _stable_key("peer", peer_name)
    now = _now_iso()
    messages = [item for item in (snapshot.get("messages") or []) if isinstance(item, dict)]
    last = snapshot.get("last_message") if isinstance(snapshot.get("last_message"), dict) else (messages[-1] if messages else {})
    chat_type = "group" if snapshot.get("is_group") else "direct"
    with _DB_LOCK, _connect() as conn:
        conn.execute(
            """
            insert into whatsapp_sessions(id, account_id, peer_key, display_name, chat_type, unread_count, last_message, last_direction, raw_json, created_at, updated_at)
            values(?,?,?,?,?,?,?,?,?,?,?)
            on conflict(account_id, peer_key) do update set
              display_name=excluded.display_name, chat_type=excluded.chat_type, unread_count=excluded.unread_count,
              last_message=excluded.last_message, last_direction=excluded.last_direction, raw_json=excluded.raw_json, updated_at=excluded.updated_at
            """,
            (
                peer_key, DEFAULT_ACCOUNT_ID, peer_key, peer_name, chat_type, max(0, int(unread_count or 0)),
                str(last.get("text") or "")[:2000], str(last.get("direction") or "")[:40],
                _json_text({key: value for key, value in snapshot.items() if key != "composer"}), now, now,
            ),
        )
        if messages:
            conn.execute("delete from whatsapp_messages where account_id=? and peer_key=?", (DEFAULT_ACCOUNT_ID, peer_key))
            for index, message in enumerate(messages[-100:]):
                content = str(message.get("text") or "").strip()
                if not content:
                    continue
                message_id = _stable_key("msg", f"{peer_key}|{index}|{message.get('direction')}|{content}")
                conn.execute(
                    "insert or replace into whatsapp_messages(id, account_id, peer_key, direction, content, sequence_no, observed_at, raw_json) values(?,?,?,?,?,?,?,?)",
                    (message_id, DEFAULT_ACCOUNT_ID, peer_key, str(message.get("direction") or "unknown"), content[:4000], index, now, _json_text(message)),
                )
    return {
        "peer_key": peer_key, "display_name": peer_name, "chat_type": chat_type,
        "unread_count": max(0, int(unread_count or 0)), "last_message": str(last.get("text") or "")[:2000],
        "last_direction": str(last.get("direction") or ""), "message_count": len(messages), "updated_at": now,
    }


def _persist_session_row(name: str, *, unread_count: int = 0) -> Dict[str, Any]:
    display_name = str(name or "").strip()[:500]
    if not display_name:
        return {}
    peer_key = _stable_key("peer", display_name)
    now = _now_iso()
    with _DB_LOCK, _connect() as conn:
        conn.execute(
            """
            insert into whatsapp_sessions(id, account_id, peer_key, display_name, unread_count, created_at, updated_at) values(?,?,?,?,?,?,?)
            on conflict(account_id, peer_key) do update set display_name=excluded.display_name, unread_count=excluded.unread_count, updated_at=excluded.updated_at
            """,
            (peer_key, DEFAULT_ACCOUNT_ID, peer_key, display_name, max(0, int(unread_count or 0)), now, now),
        )
    return {"peer_key": peer_key, "display_name": display_name, "unread_count": max(0, int(unread_count or 0)), "updated_at": now}


def _persist_contact(contact: Dict[str, Any]) -> Dict[str, Any]:
    first_name = str(contact.get("first_name") or "").strip()[:200]
    last_name = str(contact.get("last_name") or "").strip()[:200]
    username = str(contact.get("username") or "").strip()[:240]
    phone = str(contact.get("phone") or "").strip()[:80]
    display_name = str(contact.get("display_name") or " ".join(part for part in (first_name, last_name) if part) or username or phone).strip()[:500]
    identity = username or phone or display_name
    if not identity:
        return {}
    contact_key = _stable_key("contact", identity)
    now = _now_iso()
    with _DB_LOCK, _connect() as conn:
        conn.execute(
            """
            insert into whatsapp_contacts(id, account_id, contact_key, display_name, first_name, last_name, username, phone, country_code, source, raw_json, created_at, updated_at)
            values(?,?,?,?,?,?,?,?,?,?,?,?,?)
            on conflict(account_id, contact_key) do update set
              display_name=excluded.display_name, first_name=excluded.first_name, last_name=excluded.last_name, username=excluded.username,
              phone=excluded.phone, country_code=excluded.country_code, source=excluded.source, raw_json=excluded.raw_json, updated_at=excluded.updated_at
            """,
            (contact_key, DEFAULT_ACCOUNT_ID, contact_key, display_name, first_name, last_name, username, phone, str(contact.get("country_code") or "")[:20], str(contact.get("source") or "desktop"), _json_text(contact), now, now),
        )
    return {**contact, "contact_key": contact_key, "display_name": display_name, "updated_at": now}


def list_sessions(*, limit: int = 50, offset: int = 0, keyword: str = "", chat_type: str = "") -> Dict[str, Any]:
    kind = str(chat_type or "").strip().lower()
    return _query_page("whatsapp_sessions", limit=limit, offset=offset, keyword=keyword, where="chat_type=?" if kind in {"direct", "group"} else "", params=(kind,) if kind else ())


def list_messages(peer_key: str, *, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    key = str(peer_key or "").strip()
    safe_limit = max(1, min(int(limit or 100), 300))
    safe_offset = max(0, int(offset or 0))
    with _DB_LOCK, _connect() as conn:
        peer = conn.execute("select * from whatsapp_sessions where account_id=? and peer_key=?", (DEFAULT_ACCOUNT_ID, key)).fetchone()
        total = int(conn.execute("select count(*) from whatsapp_messages where account_id=? and peer_key=?", (DEFAULT_ACCOUNT_ID, key)).fetchone()[0])
        rows = conn.execute("select * from whatsapp_messages where account_id=? and peer_key=? order by sequence_no asc limit ? offset ?", (DEFAULT_ACCOUNT_ID, key, safe_limit, safe_offset)).fetchall()
    return {"peer": _row_public(peer) if peer else None, "items": [_row_public(row) for row in rows], "total": total, "limit": safe_limit, "offset": safe_offset}


def list_contacts(*, limit: int = 50, offset: int = 0, keyword: str = "") -> Dict[str, Any]:
    return _query_page("whatsapp_contacts", limit=limit, offset=offset, keyword=keyword)


def list_operations(*, limit: int = 50, offset: int = 0, keyword: str = "") -> Dict[str, Any]:
    return _query_page("whatsapp_operations", limit=limit, offset=offset, keyword=keyword)


def _scroll_node(node: Any, direction: int = -1) -> None:
    rect = _rect(node)
    if not rect:
        return
    import win32api  # type: ignore
    import win32con  # type: ignore

    win32api.SetCursorPos((int((rect[0] + rect[2]) / 2), int((rect[1] + rect[3]) / 2)))
    win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, 720 * (-1 if direction < 0 else 1), 0)
    time.sleep(0.45)


def _sync_sessions_ui(*, limit: int = 500, max_scrolls: int = 20) -> Dict[str, Any]:
    _claim_action("同步 WhatsApp 会话")
    try:
        hwnd, _window = _window_or_raise()
        clean_limit = max(1, min(int(limit or 500), 2000))
        pages = max(1, min(int(max_scrolls or 20), 100))
        found: Dict[str, Dict[str, Any]] = {}
        empty_rounds = 0
        first_rows = _visible_chat_rows(hwnd, unread_only=False)
        for _page in range(pages):
            root = _root_for_hwnd(hwnd)
            pane = _find_by_aid(root, "pane-side")
            if pane is None:
                break
            rows = first_rows if _page == 0 else _chat_rows(pane)
            added = 0
            for row in rows:
                name = str(row.get("name") or "").strip()
                if not name or name.casefold() in {"无对话", "no chats"}:
                    continue
                key = re.sub(r"\s+", " ", name).casefold()
                if key in found:
                    continue
                persisted = _persist_session_row(name)
                if persisted:
                    found[key] = persisted
                    added += 1
                if len(found) >= clean_limit:
                    break
            if len(found) >= clean_limit:
                break
            empty_rounds = empty_rounds + 1 if added == 0 else 0
            if empty_rounds >= 2:
                break
            _scroll_node(pane, -1)
        message = f"已同步 {len(found)} 个 WhatsApp 会话" if found else "同步完成，但当前 WhatsApp 会话列表没有暴露可读取的会话"
        result = {"ok": True, "count": len(found), "items": list(found.values()), "empty": not found, "message": message}
        _record_operation("sync_sessions", "", "success", result["message"], result)
        _append_log("sessions_synced", count=len(found))
        return result
    except Exception as exc:
        _record_operation("sync_sessions", "", "failed", str(exc))
        raise
    finally:
        _release_action()


def sync_sessions(*, limit: int = 500, max_scrolls: int = 20) -> Dict[str, Any]:
    return _sync_sessions_ui(limit=limit, max_scrolls=max_scrolls)


def _open_new_chat_page(hwnd: int) -> Any:
    root = _root_for_hwnd(hwnd)
    _click_named(root, ("对话", "Chats"), required=False)
    time.sleep(0.3)
    root = _root_for_hwnd(hwnd)
    _click_named(root, ("新聊天", "New chat"))
    time.sleep(0.65)
    root = _root_for_hwnd(hwnd)
    search = next(
        (
            node
            for node, _depth in _iter_nodes(root, max_depth=24, max_nodes=8000)
            if _node_type(node) == "EditControl"
            and any(marker in _node_text(node).casefold() for marker in ("搜索姓名", "search name", "电话号码", "phone number", "@账号", "username"))
            and _rect(node)
        ),
        None,
    )
    if search is None:
        raise RuntimeError("WhatsApp 新聊天页没有出现联系人搜索框")
    return search


def _contact_rows_from_new_chat(root: Any) -> List[Dict[str, Any]]:
    skip = {
        "新聊天", "new chat", "返回", "back", "电话号码", "phone number", "新建群组", "new group",
        "添加联系人", "add contact", "新建社群", "new community", "搜索姓名、电话号码或 @账号",
    }
    candidates: Dict[str, Dict[str, Any]] = {}
    for node, _depth in _iter_nodes(root, max_depth=26, max_nodes=12000):
        text = re.sub(r"\s+", " ", _node_text(node)).strip()
        rect = _rect(node)
        if not text or not rect or text.casefold() in skip:
            continue
        if _node_type(node) not in {"ButtonControl", "ListItemControl", "DataItemControl"}:
            continue
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        if width < 180 or height < 38 or height > 130:
            continue
        key = text.casefold()
        candidates.setdefault(key, {"node": node, "display_name": text[:500], "rect": rect})
    return list(candidates.values())


def sync_contacts(*, limit: int = 1000, max_scrolls: int = 30) -> Dict[str, Any]:
    _claim_action("同步 WhatsApp 通讯录")
    try:
        hwnd, _window = _window_or_raise()
        _open_new_chat_page(hwnd)
        clean_limit = max(1, min(int(limit or 1000), 5000))
        pages = max(1, min(int(max_scrolls or 30), 150))
        found: Dict[str, Dict[str, Any]] = {}
        empty_rounds = 0
        for _page in range(pages):
            root = _root_for_hwnd(hwnd)
            rows = _contact_rows_from_new_chat(root)
            added = 0
            for row in rows:
                display_name = str(row.get("display_name") or "").strip()
                key = display_name.casefold()
                if not key or key in found:
                    continue
                contact = _persist_contact({"display_name": display_name, "source": "desktop_new_chat"})
                if contact:
                    found[key] = contact
                    added += 1
                if len(found) >= clean_limit:
                    break
            if len(found) >= clean_limit:
                break
            empty_rounds = empty_rounds + 1 if added == 0 else 0
            if empty_rounds >= 2:
                break
            search = next(
                (
                    node for node, _depth in _iter_nodes(root, max_depth=24, max_nodes=8000)
                    if _node_type(node) == "EditControl"
                    and any(marker in _node_text(node).casefold() for marker in ("搜索姓名", "search name", "电话号码", "phone number", "@账号", "username"))
                    and _rect(node)
                ),
                None,
            )
            if search is None:
                break
            try:
                parent = search.GetParentControl()
            except Exception:
                parent = root
            _scroll_node(parent, -1)
        message = f"已同步 {len(found)} 个 WhatsApp 联系人" if found else "同步完成，但当前 WhatsApp 新聊天页没有暴露可读取的联系人"
        result = {"ok": True, "count": len(found), "items": list(found.values()), "empty": not found, "message": message}
        _record_operation("sync_contacts", "", "success", result["message"], result)
        _append_log("contacts_synced", count=len(found))
        return result
    except Exception as exc:
        _record_operation("sync_contacts", "", "failed", str(exc))
        raise
    finally:
        _release_action()


def _search_and_open_conversation(hwnd: int, target: str) -> Dict[str, Any]:
    name = str(target or "").strip()
    if not name:
        raise RuntimeError("缺少 WhatsApp 会话目标")
    root = _root_for_hwnd(hwnd)
    _click_named(root, ("对话", "Chats"), required=False)
    time.sleep(0.3)
    root = _root_for_hwnd(hwnd)
    search = next(
        (
            node
            for node, _depth in _iter_nodes(root, max_depth=20, max_nodes=6000)
            if _node_type(node) == "EditControl"
            and any(marker in _node_text(node).casefold() for marker in ("搜索或开始", "search or start"))
            and _rect(node)
        ),
        None,
    )
    if search is None:
        raise RuntimeError("未找到 WhatsApp 会话搜索框")
    _set_edit_text(search, name)
    time.sleep(0.8)
    root = _root_for_hwnd(hwnd)
    pane = _find_by_aid(root, "pane-side")
    rows = _chat_rows(pane) if pane is not None else []
    exact = [row for row in rows if name.casefold() in str(row.get("name") or "").casefold()]
    if not exact and pane is not None:
        for node, _depth in _iter_nodes(pane, max_depth=16, max_nodes=2400):
            text = _node_text(node).strip()
            if name.casefold() not in text.casefold() or not _rect(node):
                continue
            parent = node
            for _ in range(5):
                try:
                    candidate = parent.GetParentControl()
                except Exception:
                    break
                rect = _rect(candidate)
                if rect and 40 <= rect[3] - rect[1] <= 130:
                    parent = candidate
                else:
                    break
            exact.append({"node": parent, "name": text, "rect": _rect(parent) or _rect(node)})
            break
    if not exact:
        raise RuntimeError(f"没有找到 WhatsApp 会话：{name}")
    _click(exact[0]["node"])
    time.sleep(0.75)
    snapshot = _conversation_snapshot(hwnd)
    snapshot.pop("composer", None)
    opened_name = str(snapshot.get("peer_name") or exact[0].get("name") or "").strip()
    if name.casefold() not in opened_name.casefold() and opened_name.casefold() not in name.casefold():
        raise RuntimeError(f"WhatsApp 打开的会话与目标不一致：{opened_name or '未知'}")
    return snapshot


def open_conversation(target: str) -> Dict[str, Any]:
    _claim_action("打开 WhatsApp 会话")
    try:
        hwnd, _window = _window_or_raise()
        snapshot = _search_and_open_conversation(hwnd, target)
        persisted = _persist_session_snapshot(snapshot, row_name=target)
        _record_operation("open_conversation", target, "success", "会话已打开并同步", persisted)
        return {"ok": True, "peer": persisted, "messages": snapshot.get("messages") or []}
    except Exception as exc:
        _record_operation("open_conversation", target, "failed", str(exc))
        raise
    finally:
        _release_action()


def send_message(target: str, content: str) -> Dict[str, Any]:
    message = str(content or "").strip()
    if not message:
        raise RuntimeError("发送内容不能为空")
    _claim_action("发送 WhatsApp 消息")
    try:
        hwnd, _window = _window_or_raise()
        _search_and_open_conversation(hwnd, target)
        sent = _send_current_message(hwnd, message[:4000])
        if not sent.get("sent"):
            raise RuntimeError("消息已提交，但没有检测到新的出站气泡")
        snapshot = _conversation_snapshot(hwnd)
        snapshot.pop("composer", None)
        peer = _persist_session_snapshot(snapshot, row_name=target)
        result = {"ok": True, "sent": True, "target": target, "content": message[:4000], "peer": peer}
        _record_operation("send_message", target, "success", "消息发送成功", result)
        return result
    except Exception as exc:
        _record_operation("send_message", target, "failed", str(exc))
        raise
    finally:
        _release_action()


def add_contact(*, first_name: str, last_name: str = "", username: str = "", phone: str = "", country_code: str = "+86") -> Dict[str, Any]:
    first = str(first_name or "").strip()[:200]
    last = str(last_name or "").strip()[:200]
    user = str(username or "").strip()[:240]
    number = re.sub(r"[^0-9+]", "", str(phone or "").strip())[:80]
    country = str(country_code or "+86").strip()[:20]
    if not first:
        raise RuntimeError("请填写联系人名字")
    if not user and not number:
        raise RuntimeError("请填写 WhatsApp 用户名或电话号码")
    if number.startswith(country):
        number = number[len(country):]
    if country not in {"+86", "86"}:
        raise RuntimeError("当前桌面自动化仅确认支持中国 +86；其他国家请先在 WhatsApp 表单手动切换国家")
    target = user or f"+86{number}"
    _claim_action("添加 WhatsApp 联系人")
    try:
        hwnd, _window = _window_or_raise()
        _open_new_chat_page(hwnd)
        root = _root_for_hwnd(hwnd)
        add_buttons = [
            node for node, _depth in _iter_nodes(root, max_depth=25, max_nodes=10000)
            if _node_type(node) == "ButtonControl" and _node_text(node).strip().casefold() in {"添加联系人", "add contact"} and _rect(node)
        ]
        if not add_buttons:
            raise RuntimeError("WhatsApp 新聊天页没有出现添加联系人入口")
        _click(add_buttons[-1])
        time.sleep(0.65)
        root = _root_for_hwnd(hwnd)
        fields: Dict[str, Any] = {}
        aliases = {
            "first_name": {"名字", "first name"}, "last_name": {"姓氏", "last name"},
            "username": {"用户名", "username"}, "phone": {"电话号码", "phone number"},
        }
        for node, _depth in _iter_nodes(root, max_depth=28, max_nodes=12000):
            if _node_type(node) != "EditControl" or not _rect(node):
                continue
            label = _node_text(node).strip().casefold()
            for key, names in aliases.items():
                if label in names and key not in fields:
                    fields[key] = node
        if "first_name" not in fields:
            raise RuntimeError("WhatsApp 添加联系人表单没有出现")
        _set_edit_text(fields["first_name"], first)
        if last and fields.get("last_name") is not None:
            _set_edit_text(fields["last_name"], last)
        if user and fields.get("username") is not None:
            _set_edit_text(fields["username"], user.lstrip("@"))
        if number and fields.get("phone") is not None:
            _set_edit_text(fields["phone"], number)
        time.sleep(0.35)
        root = _root_for_hwnd(hwnd)
        saved = _click_named(root, ("保存", "Save"), required=False)
        if not saved:
            raise RuntimeError("联系人资料已填写，但 WhatsApp 没有提供可点击的保存按钮")
        time.sleep(0.9)
        root = _root_for_hwnd(hwnd)
        still_editing = any(
            _node_type(node) == "EditControl" and _node_text(node).strip().casefold() in aliases["first_name"] and _rect(node)
            for node, _depth in _iter_nodes(root, max_depth=25, max_nodes=9000)
        )
        if still_editing:
            raise RuntimeError("WhatsApp 仍停留在联系人表单，未确认保存成功")
        contact = _persist_contact({
            "first_name": first, "last_name": last, "username": user.lstrip("@"), "phone": f"+86{number}" if number else "",
            "country_code": "+86", "display_name": " ".join(part for part in (first, last) if part), "source": "desktop_add_contact",
        })
        result = {"ok": True, "contact": contact, "message": "WhatsApp 联系人已保存"}
        _record_operation("add_contact", target, "success", result["message"], result)
        return result
    except Exception as exc:
        _record_operation("add_contact", target, "failed", str(exc))
        raise
    finally:
        _release_action()


def _server_proxy_base() -> str:
    value = str(getattr(settings, "lobster_server_url", None) or "").strip().rstrip("/")
    return value or "https://h5.bhzn.top"


async def _generate_reply(
    latest_message: str,
    recent_context: str,
    *,
    auth_context: Optional[Dict[str, Any]],
    instruction: str,
) -> str:
    context = auth_context or {}
    token = str(context.get("token") or getattr(settings, "openclaw_sutui_fallback_jwt", None) or "").strip()
    if not token:
        raise RuntimeError("缺少登录凭证，无法生成 WhatsApp 回复")
    model = str(
        getattr(settings, "lobster_orchestration_sutui_chat_model", None)
        or getattr(settings, "lobster_default_sutui_chat_model", None)
        or "deepseek-chat"
    ).strip()
    system_prompt = (
        "你是桌面 WhatsApp 的一对一私聊回复助手。只输出要发送给客户的正文，不输出 JSON、解释、称呼标签或引号。"
        "根据对方最后一条消息的语言，用相同语言简短自然地回复；没有资料时不要编造，说明需要确认即可。"
    )
    if instruction:
        system_prompt += f"\n用户补充要求：{instruction[:4000]}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"最近对话：\n{recent_context[-6000:]}\n\n对方最后一条消息：\n{latest_message[:2000]}"},
        ],
        "stream": False,
        "temperature": 0.35,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Installation-Id": str(context.get("installation_id") or "native-whatsapp")[:160],
    }
    async with httpx.AsyncClient(timeout=90.0, trust_env=False) as client:
        response = await client.post(f"{_server_proxy_base()}/api/sutui-chat/completions", json=payload, headers=headers)
    if response.status_code >= 400:
        raise RuntimeError(f"WhatsApp 回复生成失败 HTTP {response.status_code}: {(response.text or '')[:300]}")
    data = response.json() if response.content else {}
    try:
        content = str(data["choices"][0]["message"]["content"] or "").strip()
    except Exception as exc:
        raise RuntimeError("WhatsApp 回复模型没有返回正文") from exc
    content = re.sub(r"^```(?:text)?\s*|\s*```$", "", content, flags=re.I).strip()
    if not content:
        raise RuntimeError("WhatsApp 回复模型返回空内容")
    return content[:1200]


def request_stop() -> Dict[str, Any]:
    _STOP_REQUESTED.set()
    with _ACTIVE_LOCK:
        running = _ACTIVE
    _append_log("stop_requested", running=running)
    return {"ok": True, "requested": True, "running": running}


async def run_once(
    *,
    auth_context: Optional[Dict[str, Any]] = None,
    config_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        _claim_action("WhatsApp 消息接管")
    except RuntimeError:
        return {"ok": True, "skipped": True, "reason": "running", "message": "WhatsApp 已有桌面操作正在执行"}
    _STOP_REQUESTED.clear()
    cfg = get_config()
    if isinstance(config_override, dict):
        if "max_unread_per_round" in config_override:
            cfg["max_unread_per_round"] = max(1, min(int(config_override.get("max_unread_per_round") or 50), 100))
        if "reply_instruction" in config_override:
            cfg["reply_instruction"] = str(config_override.get("reply_instruction") or "").strip()[:4000]
    started = _now_iso()
    result: Dict[str, Any] = {
        "ok": True,
        "action": "native_whatsapp_poll",
        "started_at": started,
        "checked": 0,
        "replied": 0,
        "skipped": 0,
        "failed": 0,
        "items": [],
    }
    loop = asyncio.get_running_loop()
    try:
        windows = await loop.run_in_executor(_UI_EXECUTOR, _scan_windows)
        if not windows:
            raise RuntimeError("未检测到 Windows 桌面版 WhatsApp，请先启动并登录")
        hwnd = int(windows[0].get("hwnd") or 0)
        processed: set[str] = set()
        for _index in range(int(cfg["max_unread_per_round"])):
            if _STOP_REQUESTED.is_set():
                result["stop_reason"] = "cancelled"
                break
            snapshot = await loop.run_in_executor(_UI_EXECUTOR, _open_first_unread, hwnd)
            if not snapshot:
                break
            snapshot.pop("composer", None)
            result["checked"] += 1
            peer_name = str(snapshot.get("peer_name") or snapshot.get("row_name") or "未命名会话").strip()
            _persist_session_snapshot(snapshot, row_name=peer_name, unread_count=1)
            latest = snapshot.get("last_message") if isinstance(snapshot.get("last_message"), dict) else {}
            text = str(latest.get("text") or "").strip()
            work_key = f"{peer_name}|{text}"
            item: Dict[str, Any] = {"peer_name": peer_name, "last_message": text[:500]}
            if work_key in processed:
                item.update({"status": "skipped", "reason": "duplicate_in_round"})
                result["skipped"] += 1
                result["items"].append(item)
                break
            processed.add(work_key)
            if snapshot.get("is_group"):
                item.update({"status": "skipped", "reason": "group_chat"})
                result["skipped"] += 1
            elif not text or _is_system_message(text):
                item.update({"status": "skipped", "reason": "no_replyable_text"})
                result["skipped"] += 1
            elif str(latest.get("direction") or "") != "inbound":
                item.update({"status": "skipped", "reason": "last_message_not_inbound"})
                result["skipped"] += 1
            else:
                context_lines = [
                    ("客户" if message.get("direction") == "inbound" else "我") + "：" + str(message.get("text") or "")
                    for message in (snapshot.get("messages") or [])[-12:]
                    if isinstance(message, dict) and str(message.get("direction") or "") in {"inbound", "outbound"}
                ]
                try:
                    reply = await _generate_reply(
                        text,
                        "\n".join(context_lines),
                        auth_context=auth_context,
                        instruction=str(cfg.get("reply_instruction") or ""),
                    )
                    if _STOP_REQUESTED.is_set():
                        result["stop_reason"] = "cancelled"
                        break
                    sent = await loop.run_in_executor(_UI_EXECUTOR, _send_current_message, hwnd, reply)
                    if not sent.get("sent"):
                        raise RuntimeError("消息已提交，但没有检测到新的出站气泡")
                    confirmed = await loop.run_in_executor(_UI_EXECUTOR, _conversation_snapshot, hwnd)
                    confirmed.pop("composer", None)
                    _persist_session_snapshot(confirmed, row_name=peer_name, unread_count=0)
                    item.update({"status": "replied", "reply": reply})
                    result["replied"] += 1
                except Exception as exc:
                    item.update({"status": "failed", "error": str(exc)[:500]})
                    result["failed"] += 1
            result["items"].append(item)
            _append_log("conversation_processed", **item)
        result["finished_at"] = _now_iso()
        result["summary_text"] = (
            f"个人whatapp助手本轮检查 {result['checked']} 个未读会话，"
            f"回复 {result['replied']} 个，跳过 {result['skipped']} 个，失败 {result['failed']} 个。"
        )
        result["ok"] = result["failed"] == 0
        cfg["last_run"] = {key: value for key, value in result.items() if key != "items"}
        cfg["last_run"]["items"] = result.get("items", [])[:100]
        _write_config(cfg)
        _append_log("round_finished", result=cfg["last_run"])
        return result
    except Exception as exc:
        _append_log("round_failed", error=str(exc))
        raise
    finally:
        _release_action()
        _STOP_REQUESTED.clear()
