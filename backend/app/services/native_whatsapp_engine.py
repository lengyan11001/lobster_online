from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
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
# 桌面版 WhatsApp 有多种打包方式，窗口类名/进程名会随版本和安装渠道变：
#   * 新版 WinUI3：进程 WhatsApp.exe，窗口类 WinUIDesktopWin32WindowClass
#   * Store/UWP 外壳：宿主 ApplicationFrameWindow，真实窗口是它的子窗口
#   * 打包成 Electron/WebView 的渠道版：Chrome_WidgetWin_1
WHATSAPP_WINDOW_CLASSES = {
    "WinUIDesktopWin32WindowClass",
    "ApplicationFrameWindow",
    "Chrome_WidgetWin_1",
    "Windows.UI.Core.CoreWindow",
}
WHATSAPP_WINDOW_CLASS = "WinUIDesktopWin32WindowClass"   # 兼容旧引用
WHATSAPP_PROCESS_NAMES = {"whatsapp.root.exe", "whatsapp.exe"}
# 只要进程名里带 whatsapp 就算候选（覆盖 WhatsAppBeta / WhatsApp Business / whatsapp.root 等）
WHATSAPP_PROCESS_HINTS = ("whatsapp",)
WHATSAPP_PROCESS_IGNORE = {"whatsappcrashhandler.exe", "whatsappupdate.exe", "whatsappupdater.exe"}

# WhatsApp 进程里还挂着一堆辅助窗口（输入法、托盘图标、GDI+ 钩子、广播事件窗口…）。
# 它们类名各异、但都不是聊天主窗口；不过滤掉会把候选列表刷成一片噪音，
# 用户看到的就是"识别出一堆看不懂的窗口"。
_AUX_WINDOW_CLASS_EXACT = {"IME", "MSCTFIME UI", "MSCTFIME UI Window"}
_AUX_WINDOW_CLASS_PREFIXES = ("h.notifyicon", ".net-broadcasteventwindow", "gdi+ hook window class")
_AUX_WINDOW_TITLE_PREFIXES = ("default ime", "msctfime", "gdi+ window", "h.notifyicon")


def is_auxiliary_window(*, class_name: str, title: str) -> bool:
    """辅助窗口（输入法/托盘/钩子）不能当成 WhatsApp 主窗口候选。"""
    window_class = str(class_name or "").strip().lower()
    window_title = str(title or "").strip().lower()
    if any(window_class.startswith(prefix) for prefix in _AUX_WINDOW_CLASS_PREFIXES):
        return True
    if any(window_class == name.lower() for name in _AUX_WINDOW_CLASS_EXACT):
        return True
    return any(window_title.startswith(prefix) for prefix in _AUX_WINDOW_TITLE_PREFIXES)


def whatsapp_window_match(*, process_name: str, class_name: str, title: str) -> bool:
    """判断一个窗口是不是桌面版 WhatsApp 主窗口（跨版本尽量宽松）。"""
    process = str(process_name or "").strip().lower()
    window_class = str(class_name or "").strip()
    window_title = str(title or "").strip().lower()
    if not process:
        return False
    if process in WHATSAPP_PROCESS_IGNORE:
        return False
    if process not in WHATSAPP_PROCESS_NAMES and not any(hint in process for hint in WHATSAPP_PROCESS_HINTS):
        return False
    if window_class in WHATSAPP_WINDOW_CLASSES:
        return True
    return "whatsapp" in window_title


def is_whatsapp_process(process_name: str, process_path: str = "") -> bool:
    """判断一个进程是不是 WhatsApp（进程名优先，安装路径兜底，兼容 Store/MSIX 版）。"""
    name = str(process_name or "").strip().lower()
    path = str(process_path or "").strip().lower()
    if name in WHATSAPP_PROCESS_IGNORE:
        return False
    if name in WHATSAPP_PROCESS_NAMES or any(hint in name for hint in WHATSAPP_PROCESS_HINTS):
        return True
    if not path:
        return False
    # 浏览器也可能在参数里带 whatsapp；只看可执行文件路径本身
    if any(token in path for token in ("chrome.exe", "msedge.exe", "firefox.exe", "iexplore.exe")):
        return False
    return "whatsapp" in path


def _window_rank(row: Dict[str, Any]) -> tuple:
    title = str(row.get("title") or "").lower()
    class_name = str(row.get("class_name") or "")
    noisy = any(token in title for token in ("hidden", "tray", "shadow", "default ime", "msctfime"))
    return (
        1 if row.get("match_by") in {"class", "title"} else 0,
        1 if (row.get("is_visible") and not row.get("is_iconic")) else 0,
        1 if title == "whatsapp" else 0,
        0 if "business" in title else 1,
        1 if row.get("match_by") == "child" else 0,
        1 if class_name in WHATSAPP_WINDOW_CLASSES else 0,
        1 if not noisy else 0,
        int(row.get("hwnd") or 0),
    )

_UI_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="lobster-whatsapp")
_ACTIVE_LOCK = threading.Lock()
_ACTIVE = False
_ACTIVE_ACTION = ""
_STOP_REQUESTED = threading.Event()
_DB_LOCK = threading.RLock()
_LAST_STATUS_LOG_AT = 0.0

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

        create table if not exists whatsapp_tasks (
            id text primary key,
            account_id text not null,
            task_type text not null default 'add_friend',
            targets text not null default '[]',
            payload text not null default '{}',
            status text not null default 'queued',
            processed integer not null default 0,
            success integer not null default 0,
            failed integer not null default 0,
            error_message text,
            client_request_id text,
            created_at text not null,
            updated_at text not null
        );
        create index if not exists idx_whatsapp_tasks_queue
        on whatsapp_tasks(account_id, task_type, status, created_at);
        create unique index if not exists uq_whatsapp_tasks_client_request
        on whatsapp_tasks(client_request_id) where client_request_id <> '';

        create table if not exists whatsapp_friend_add_control (
            account_id text primary key,
            enabled integer not null default 0,
            interval_seconds integer not null default 45,
            daily_limit integer not null default 30,
            running integer not null default 0,
            last_started_at text,
            last_stopped_at text,
            updated_at text not null
        );
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
        "memory_doc_ids": [],
        "group_invite_enabled": False,
        "group_invite_memory_doc_id": "",
        "group_invite_keywords": "",
        "group_invite_contacts": [],
        "group_invite_group_name": "",
        "group_invite_welcome_message": "",
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
    result["memory_doc_ids"] = [
        str(item or "")[:64] for item in (result.get("memory_doc_ids") or [])
        if str(item or "").strip()
    ][:20]
    result["group_invite_enabled"] = bool(result.get("group_invite_enabled"))
    result["group_invite_memory_doc_id"] = str(result.get("group_invite_memory_doc_id") or "").strip()[:64]
    result["group_invite_keywords"] = str(result.get("group_invite_keywords") or "").strip()[:500]
    result["group_invite_contacts"] = [
        str(item or "").strip()[:120] for item in (result.get("group_invite_contacts") or [])
        if str(item or "").strip()
    ][:30]
    result["group_invite_group_name"] = str(result.get("group_invite_group_name") or "").strip()[:60]
    result["group_invite_welcome_message"] = str(result.get("group_invite_welcome_message") or "").strip()[:1000]
    if not isinstance(result.get("last_run"), dict):
        result["last_run"] = {}
    return result


def save_config(
    *,
    interval_seconds: Optional[int] = None,
    takeover_session_minutes: Optional[int] = None,
    max_unread_per_round: Optional[int] = None,
    reply_instruction: Optional[str] = None,
    memory_doc_ids: Optional[List[str]] = None,
    group_invite_enabled: Optional[bool] = None,
    group_invite_memory_doc_id: Optional[str] = None,
    group_invite_keywords: Optional[str] = None,
    group_invite_contacts: Optional[List[str]] = None,
    group_invite_group_name: Optional[str] = None,
    group_invite_welcome_message: Optional[str] = None,
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
    if memory_doc_ids is not None:
        cfg["memory_doc_ids"] = [
            str(item or "")[:64] for item in (memory_doc_ids or []) if str(item or "").strip()
        ][:20]
    if group_invite_enabled is not None:
        cfg["group_invite_enabled"] = bool(group_invite_enabled)
    if group_invite_memory_doc_id is not None:
        cfg["group_invite_memory_doc_id"] = str(group_invite_memory_doc_id or "").strip()[:64]
    if group_invite_keywords is not None:
        cfg["group_invite_keywords"] = str(group_invite_keywords or "").strip()[:500]
    if group_invite_contacts is not None:
        cfg["group_invite_contacts"] = [
            str(item or "").strip()[:120] for item in (group_invite_contacts or []) if str(item or "").strip()
        ][:30]
    if group_invite_group_name is not None:
        cfg["group_invite_group_name"] = str(group_invite_group_name or "").strip()[:60]
    if group_invite_welcome_message is not None:
        cfg["group_invite_welcome_message"] = str(group_invite_welcome_message or "").strip()[:1000]
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


# ---------------------------------------------------------------------------
# 运行依赖：优先用系统安装的包；装不上/装坏了就退回随客户端代码包下发的内置副本。
#
# 背景（diag_20260921073448_831a0dad，2026-09-21）：客户机上 uiautomation / pyperclip /
# psutil 三个包都不在（status.dependencies 全 false），于是
#   * 登录状态永远“未登录”（没有 UIA 就读不到会话控件）→ 接管根本起不来；
#   * 进程名/路径为空 → 候选窗口全是「设置」「Edge」之类的系统窗口。
# 解决方式：uiautomation / comtypes 走内置副本，进程枚举与剪贴板改成不依赖第三方包。
# ---------------------------------------------------------------------------
VENDOR_DIR = Path(__file__).resolve().parents[1] / "vendor"
_COMTYPES_GEN_DIR = ROOT_DIR / ".cache" / "comtypes_gen"
_UIA_LOCK = threading.Lock()
_UIA_CACHE: Dict[str, Any] = {"module": None, "error": "", "source": "", "at": 0.0}
_UIA_ERROR_CACHE_SECONDS = 300.0


def _activate_vendor_path(*, prefer: bool = False) -> bool:
    """把随包下发的内置依赖目录挂进 sys.path（幂等）。

    默认追加到末尾：系统已安装的包照旧优先，缺失的（uiautomation / comtypes /
    psutil / pyperclip）从内置副本补上。
    prefer=True 时挪到最前：系统安装版导入失败（装坏了）时强制走内置副本。
    """
    if not VENDOR_DIR.is_dir():
        return False
    resolved = str(VENDOR_DIR)
    if resolved in sys.path:
        if prefer and sys.path.index(resolved) != 0:
            sys.path.remove(resolved)
            sys.path.insert(0, resolved)
        return True
    if prefer:
        sys.path.insert(0, resolved)
    else:
        sys.path.append(resolved)
    return True


def _prepare_comtypes_cache() -> None:
    """comtypes 生成类型库会写包目录；改写到可写的 .cache，避免污染 OTA 下发目录。"""
    try:
        import comtypes.client  # type: ignore

        _COMTYPES_GEN_DIR.mkdir(parents=True, exist_ok=True)
        comtypes.client.gen_dir = str(_COMTYPES_GEN_DIR)
    except Exception:
        pass


def _drop_partial_modules() -> None:
    """清掉上一次失败导入留下的半成品模块，否则内置副本会被它挡住。"""
    for name in ("uiautomation", "comtypes", "comtypes.client", "comtypes.gen"):
        module = sys.modules.get(name)
        if module is None:
            continue
        origin = str(getattr(module, "__file__", "") or "")
        if not origin or "vendor" not in origin.replace("\\", "/"):
            sys.modules.pop(name, None)


# 模块加载即挂上内置依赖目录：psutil / pyperclip 缺失时也能从随包副本补上，
# uiautomation 导入失败时 load_uia() 会再把它提到 sys.path 最前。
_activate_vendor_path()


def load_uia(*, refresh: bool = False) -> tuple[Any, str, str]:
    """加载 uiautomation，返回 (模块或 None, 错误, 来源)。

    来源：installed = 系统 site-packages；vendor = 随包内置副本；missing = 都不可用。
    """
    now = time.monotonic()
    with _UIA_LOCK:
        cached_module = _UIA_CACHE.get("module")
        cached_error = str(_UIA_CACHE.get("error") or "")
        cached_at = float(_UIA_CACHE.get("at") or 0.0)
        if not refresh:
            if cached_module is not None:
                return cached_module, "", str(_UIA_CACHE.get("source") or "installed")
            if cached_error and now - cached_at < _UIA_ERROR_CACHE_SECONDS:
                return None, cached_error, "missing"

    error = ""
    module: Any = None
    source = ""
    try:
        import uiautomation as auto  # type: ignore

        module, source = auto, _module_source(auto)
    except Exception as exc:
        error = f"系统安装版：{type(exc).__name__}: {exc}"

    if module is None and _activate_vendor_path(prefer=True):
        _drop_partial_modules()
        _prepare_comtypes_cache()
        try:
            import uiautomation as auto  # type: ignore

            module, source, error = auto, _module_source(auto), ""
        except Exception as exc:
            error = f"{error}；内置副本：{type(exc).__name__}: {exc}" if error else f"内置副本：{type(exc).__name__}: {exc}"

    with _UIA_LOCK:
        _UIA_CACHE.update(
            {"module": module, "error": "" if module is not None else error,
             "source": source, "at": time.monotonic()}
        )
    if module is None:
        return None, error, "missing"
    return module, "", source


_CLIPBOARD_BACKEND = {"name": ""}


def _clipboard_via_ctypes(text: str) -> None:
    """纯 ctypes 写剪贴板（不依赖 pyperclip / pywin32）。"""
    import ctypes
    from ctypes import wintypes

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    payload = str(text or "").encode("utf-16-le") + b"\x00\x00"
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
    if not handle:
        raise RuntimeError("剪贴板内存分配失败")
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        raise RuntimeError("剪贴板内存锁定失败")
    try:
        ctypes.memmove(pointer, payload, len(payload))
    finally:
        kernel32.GlobalUnlock(handle)
    last_error = "剪贴板被其他程序占用"
    for _attempt in range(12):
        if user32.OpenClipboard(None):
            try:
                user32.EmptyClipboard()
                if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                    last_error = "剪贴板写入失败"
                    continue
                return
            finally:
                user32.CloseClipboard()
        time.sleep(0.12)
    raise RuntimeError(last_error)


def set_clipboard_text(text: str) -> str:
    """写系统剪贴板，返回实际使用的后端名（诊断用）。"""
    value = str(text or "")
    errors: List[str] = []
    try:
        import pyperclip  # type: ignore

        pyperclip.copy(value)
        _CLIPBOARD_BACKEND["name"] = "pyperclip"
        return "pyperclip"
    except Exception as exc:
        errors.append(f"pyperclip: {type(exc).__name__}: {exc}")
    if os.name == "nt":
        try:
            import win32clipboard  # type: ignore
            import win32con  # type: ignore

            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, value)
            finally:
                win32clipboard.CloseClipboard()
            _CLIPBOARD_BACKEND["name"] = "win32clipboard"
            return "win32clipboard"
        except Exception as exc:
            errors.append(f"win32clipboard: {type(exc).__name__}: {exc}")
        try:
            _clipboard_via_ctypes(value)
            _CLIPBOARD_BACKEND["name"] = "ctypes"
            return "ctypes"
        except Exception as exc:
            errors.append(f"ctypes: {type(exc).__name__}: {exc}")
    raise RuntimeError("剪贴板不可用（" + "；".join(errors) + "）")


def _module_probe(name: str) -> tuple[bool, str]:
    try:
        __import__(name)
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _module_source(module: Any) -> str:
    """区分模块是从随包内置副本来的，还是系统 site-packages 来的。"""
    origin = str(getattr(module, "__file__", "") or "").replace("\\", "/")
    if not origin:
        return "builtin"
    if origin.startswith(str(VENDOR_DIR).replace("\\", "/")):
        return "vendor"
    return "installed"


def _process_meta(pid: int) -> Dict[str, Any]:
    try:
        from .win_process_scan import process_image

        name, exe = process_image(int(pid or 0))
        return {"name": name, "exe": exe}
    except Exception:
        return {"name": "", "exe": ""}


def _scan_windows() -> List[Dict[str, Any]]:
    try:
        import win32gui  # type: ignore
        import win32process  # type: ignore
    except Exception:
        return []
    items: List[Dict[str, Any]] = []
    seen: set = set()

    def window_row(hwnd: int, *, match_by: str, parent_hwnd: int = 0) -> Optional[Dict[str, Any]]:
        try:
            title = str(win32gui.GetWindowText(hwnd) or "").strip()
            class_name = str(win32gui.GetClassName(hwnd) or "").strip()
            _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
            meta = _process_meta(int(pid or 0))
            rect = tuple(int(value) for value in win32gui.GetWindowRect(hwnd))
            return {
                "account_id": DEFAULT_ACCOUNT_ID,
                "hwnd": int(hwnd),
                "parent_hwnd": int(parent_hwnd or 0),
                "pid": int(pid or 0),
                "title": title or "WhatsApp",
                "class_name": class_name,
                "process_name": meta.get("name") or "",
                "process_path": meta.get("exe") or "",
                "is_visible": bool(win32gui.IsWindowVisible(hwnd)),
                "is_iconic": bool(win32gui.IsIconic(hwnd)),
                "rect": list(rect),
                "match_by": match_by,
            }
        except Exception:
            return None

    def children_of(hwnd: int) -> List[int]:
        out: List[int] = []
        try:
            def visit(child: int, _extra: Any) -> None:
                out.append(int(child))
            win32gui.EnumChildWindows(hwnd, visit, None)
        except Exception:
            pass
        return out

    def collect(hwnd: int, _extra: Any) -> None:
        try:
            title = str(win32gui.GetWindowText(hwnd) or "").strip()
            class_name = str(win32gui.GetClassName(hwnd) or "").strip()
            _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
            meta = _process_meta(int(pid or 0))
            process_name = str(meta.get("name") or "").lower()
            process_path = str(meta.get("exe") or "")
            if is_auxiliary_window(class_name=class_name, title=title):
                return
            # 进程优先：只要是 WhatsApp 进程的顶层窗口就收进来（后面再按"类名/标题命中"排序），
            # 这样即使新版换了窗口类名也不会"完全识别不到"。
            if process_name or process_path:
                if not is_whatsapp_process(process_name, process_path):
                    return
                if class_name in WHATSAPP_WINDOW_CLASSES:
                    match_by = "class"
                elif "whatsapp" in title.lower():
                    match_by = "title"
                else:
                    match_by = "process"
            else:
                # 进程信息完全拿不到时才退回"只看窗口"，而且只有 WhatsApp 专有类名、
                # 或标题里明确写着 WhatsApp 才算：ApplicationFrameWindow /
                # Windows.UI.Core.CoreWindow / Chrome_WidgetWin_1 是系统共用类名，
                # 不加这个门槛就会把「设置」「Windows 输入体验」「Edge」也收成候选。
                if "whatsapp" in title.lower():
                    match_by = "title"
                elif class_name == WHATSAPP_WINDOW_CLASS:
                    match_by = "class"
                else:
                    return
            row = window_row(int(hwnd), match_by=match_by)
            if row and int(row["hwnd"]) not in seen:
                seen.add(int(row["hwnd"]))
                items.append(row)
            # UWP/打包壳：真实窗口是子窗口，优先用子窗口句柄（点击/取控件都在子窗口上）
            if class_name in {"ApplicationFrameWindow", "Windows.UI.Core.CoreWindow"}:
                for child in children_of(int(hwnd)):
                    child_class = ""
                    try:
                        child_class = str(win32gui.GetClassName(child) or "").strip()
                    except Exception:
                        child_class = ""
                    if child_class not in WHATSAPP_WINDOW_CLASSES:
                        continue
                    child_row = window_row(child, match_by="child", parent_hwnd=int(hwnd))
                    if child_row and int(child_row["hwnd"]) not in seen:
                        seen.add(int(child_row["hwnd"]))
                        items.append(child_row)
        except Exception:
            return

    win32gui.EnumWindows(collect, None)

    return sorted(items, key=_window_rank, reverse=True)


def whatsapp_processes() -> List[Dict[str, Any]]:
    """列出机器上所有 WhatsApp 进程（即使没有窗口），用于诊断"到底是没起还是没识别到"。"""
    out: List[Dict[str, Any]] = []
    try:
        from .win_process_scan import snapshot_processes
    except Exception:
        return out
    for proc in snapshot_processes():
        try:
            name = str(proc.get("name") or "")
            exe = str(proc.get("exe") or "")
            if not is_whatsapp_process(name, exe):
                continue
            out.append({"pid": int(proc.get("pid") or 0), "name": name, "exe": exe})
        except Exception:
            continue
    return out


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
    auto, error, _source = load_uia()
    if auto is None:
        raise RuntimeError(f"UIA 控件不可用（{error}）")

    root = auto.ControlFromHandle(int(hwnd))
    candidates = [node for node, _depth in _iter_nodes(root, max_depth=14, max_nodes=400) if _node_aid(node) == "RootWebArea" and _rect(node)]
    return candidates[-1] if candidates else root


def _foreground_hwnd() -> int:
    try:
        import ctypes

        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def _same_process_window(candidate: int, target: int) -> bool:
    """两个窗口句柄是否属于同一进程（同一个 WhatsApp 的壳/子窗口都算）。"""
    if not candidate or not target:
        return False
    if candidate == target:
        return True
    try:
        import win32process  # type: ignore

        _thread_a, pid_a = win32process.GetWindowThreadProcessId(candidate)
        _thread_b, pid_b = win32process.GetWindowThreadProcessId(target)
        return bool(pid_a) and pid_a == pid_b
    except Exception:
        return False


def _node_hwnd(node: Any) -> int:
    """控件所属顶层窗口句柄。"""
    try:
        hwnd = int(getattr(node, "NativeWindowHandle", 0) or 0)
        if hwnd:
            return hwnd
        return int(getattr(node.GetTopLevelControl(), "NativeWindowHandle", 0) or 0)
    except Exception:
        return 0


def _force_foreground(hwnd: int, *, wait_seconds: float = 2.5) -> bool:
    """强行把窗口抢成前台。

    实测（本机 18:1x）：向日葵的 `OrayUI` 一直占着前台，`SetForegroundWindow` 被系统拒绝。
    这里用经典的 `AttachThreadInput` 把我们的线程附到前台窗口线程上，再调 SetForegroundWindow，
    就能拿到前台（抢到后立刻做输入，趁它没抢回去）。
    """
    try:
        import win32api  # type: ignore
        import win32gui  # type: ignore
        import win32process  # type: ignore
    except Exception:
        return _activate_window(hwnd, wait_seconds=wait_seconds)

    target = int(hwnd or 0)
    if not target:
        return False
    deadline = time.monotonic() + max(0.3, float(wait_seconds))
    while True:
        _activate_window(target, wait_seconds=0.05)
        if _same_process_window(_foreground_hwnd(), target):
            return True
        foreground = _foreground_hwnd()
        if foreground:
            try:
                fg_thread, _pid = win32process.GetWindowThreadProcessId(foreground)
                current_thread = int(win32api.GetCurrentThreadId())
                if fg_thread and int(fg_thread) != current_thread:
                    win32process.AttachThreadInput(int(fg_thread), current_thread, True)
                    try:
                        win32gui.BringWindowToTop(target)
                        win32gui.SetForegroundWindow(target)
                    finally:
                        win32process.AttachThreadInput(int(fg_thread), current_thread, False)
                else:
                    win32gui.SetForegroundWindow(target)
            except Exception:
                pass
        if _same_process_window(_foreground_hwnd(), target):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


def _ensure_foreground(hwnd: int, *, attempts: int = 3, pause: float = 0.25) -> bool:
    """确保目标窗口是当前前台窗口。

    按键（^a/^v/Enter）是发给"当前前台窗口"的：如果焦点在别的程序（例如微信）上，
    自动化就会操作到那个程序上。所以任何键盘/鼠标动作之前都必须先过这一关。
    """
    target = int(hwnd or 0)
    if not target:
        return False
    if _same_process_window(_foreground_hwnd(), target):
        return True
    if _force_foreground(target, wait_seconds=max(0.5, pause * max(1, attempts))):
        return True
    return _same_process_window(_foreground_hwnd(), target)


def _read_clipboard_text() -> str:
    """读回用户剪贴板里的文本，用于用完还原（不污染用户剪贴板/微信）。"""
    try:
        import win32clipboard  # type: ignore
        import win32con  # type: ignore

        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return str(win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) or "")
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass
    return ""


_PRIMARY_WINDOW_CACHE: Dict[str, Any] = {"at": 0.0, "hwnd": 0}


def _primary_window_hwnd() -> int:
    """WhatsApp 主窗口句柄（5 秒缓存，避免每次都扫窗口）。"""
    now = time.time()
    cached = int(_PRIMARY_WINDOW_CACHE.get("hwnd") or 0)
    if cached and now - float(_PRIMARY_WINDOW_CACHE.get("at") or 0.0) < 5:
        return cached
    windows = _scan_windows()
    hwnd = int(windows[0]["hwnd"]) if windows else 0
    _PRIMARY_WINDOW_CACHE.update({"at": now, "hwnd": hwnd})
    return hwnd


def _point_window_hwnd(x: int, y: int) -> int:
    try:
        import win32gui  # type: ignore

        return int(win32gui.WindowFromPoint((int(x), int(y))) or 0)
    except Exception:
        return 0


def _focus_by_mouse(node: Any, hwnd: int = 0) -> None:
    """用真实鼠标点击聚焦输入框（Web 控件的 SetFocus/Invoke 都不可靠）。

    点击前把 WhatsApp 临时置顶，并校验"这个坐标下确实是 WhatsApp 窗口"，
    所以不会像旧实现那样把点击落到被遮挡的其它程序（例如微信）上。
    """
    rect = _rect(node)
    if not rect:
        # 节点可能已过期（UI 刚重建）：退化为 UIA 原生聚焦，实在不行才报错
        try:
            node.SetFocus()
            time.sleep(0.12)
            return
        except Exception:
            raise RuntimeError("WhatsApp 输入框不可点击")
    import win32api  # type: ignore
    import win32con  # type: ignore
    import win32gui  # type: ignore

    target = int(hwnd or 0) or _primary_window_hwnd()
    if target:
        _activate_window(target)
    # 点输入框左侧 1/4：搜索框右侧通常有个清除按钮（×），点中心可能点到它上
    x = int(rect[0] + (rect[2] - rect[0]) * 0.25)
    y = int((rect[1] + rect[3]) / 2)
    pinned = False
    if target:
        try:
            win32gui.SetWindowPos(
                target, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
            )
            pinned = True
            time.sleep(0.2)
        except Exception:
            pinned = False
    try:
        if target and not _same_process_window(_point_window_hwnd(x, y), target):
            raise RuntimeError("输入框位置不在 WhatsApp 窗口上，已中止输入（避免误操作到其它程序）")
        win32api.SetCursorPos((x, y))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.12)
    finally:
        if pinned:
            try:
                win32gui.SetWindowPos(
                    target, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE,
                )
            except Exception:
                pass


def _node_value(node: Any) -> str:
    try:
        return str(node.GetValuePattern().Value)
    except Exception:
        return ""


def _activate_window(hwnd: int, *, wait_seconds: float = 3.0) -> bool:
    """把 WhatsApp 激活到最前面（照微信那套做法：恢复隐藏窗口 → 提到最前 → 轮询确认）。

    后台进程调 SetForegroundWindow 常被系统拒绝，所以这里按顺序做多件事，并在
    wait_seconds 内反复重试，只有真的到最前（前台是本进程窗口）才返回 True：
      1) ShowWindow(SW_RESTORE / SW_SHOW)：窗口被最小化/隐藏时先显示出来
      2) SetWindowPos(HWND_TOP + SWP_SHOWWINDOW) + BringWindowToTop：提到最前
      3) SetForegroundWindow：尝试拿前台
    """
    import win32con  # type: ignore
    import win32gui  # type: ignore

    target = int(hwnd or 0)
    if not target:
        return False
    deadline = time.monotonic() + max(0.3, float(wait_seconds))
    while True:
        try:
            if win32gui.IsWindow(target):
                if win32gui.IsIconic(target):
                    win32gui.ShowWindow(target, win32con.SW_RESTORE)
                else:
                    win32gui.ShowWindow(target, win32con.SW_SHOW)
                win32gui.SetWindowPos(
                    target, win32con.HWND_TOP, 0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
                )
                win32gui.BringWindowToTop(target)
        except Exception:
            pass
        try:
            win32gui.SetForegroundWindow(target)
        except Exception:
            pass
        if _same_process_window(_foreground_hwnd(), target):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.3)


def _click(node: Any, *, force_mouse: bool = False) -> None:
    """点击控件。

    按钮类优先用 UIA 原生调用（不移动鼠标）；列表行/成员行这类 Web 元素
    Invoke/DoDefaultAction 会"调用成功但没反应"，用 force_mouse=True 走鼠标点击。
    """
    if force_mouse:
        _click_with_mouse(node)
        return
    # 按钮/菜单项优先用 UIA 原生调用（不移动鼠标）；
    # 列表行、输入框这类 Web 元素对 Invoke/Select 往往"调用成功但没反应"，
    # 必须走鼠标点击（下面带置顶 + 坐标守卫，不会点到别的程序上）。
    if _node_type(node) in {"ButtonControl", "SplitButtonControl", "MenuItemControl", "TabItemControl"}:
        for accessor, action in (
            ("GetInvokePattern", "Invoke"),
            ("GetLegacyIAccessiblePattern", "DoDefaultAction"),
        ):
            try:
                pattern = getattr(node, accessor)()
                getattr(pattern, action)()
                time.sleep(0.08)
                return
            except Exception:
                continue
    _click_with_mouse(node)


_IME_WINDOW_HINTS = (
    "ime", "msctfime", "candwnd", "pinyin", "unikey", "inputtip", "textinputhost", "sogou", "qqpinyin",
)


def _is_ime_like_window(hwnd: int) -> bool:
    """输入法候选窗 / TSF 浮层。

    实测（本机日志 17:58）：接管一轮报「点击位置不在 WhatsApp 窗口上」，
    当时候选窗口里就有 `QQPinyinImageCandWndTSF` —— 输入法浮层盖在 WhatsApp 上，
    但它不属于别的程序，点它最多无效、不会误操作。
    """
    if not hwnd:
        return False
    try:
        import win32gui  # type: ignore
        import win32process  # type: ignore

        class_name = str(win32gui.GetClassName(hwnd) or "").lower()
        title = str(win32gui.GetWindowText(hwnd) or "").lower()
        _thread, pid = win32process.GetWindowThreadProcessId(hwnd)
        process_name = ""
        if pid:
            try:
                import psutil  # type: ignore

                process_name = str(psutil.Process(int(pid)).name() or "").lower()
            except Exception:
                process_name = ""
        haystack = " ".join((class_name, title, process_name))
        return any(hint in haystack for hint in _IME_WINDOW_HINTS)
    except Exception:
        return False


_REMOTE_CONTROL_HINTS = (
    "oray", "awesun", "sunlogin", "teamviewer", "anydesk", "rustdesk", "todesk", "vnc", "mstsc",
)


def _window_haystack(hwnd: int) -> str:
    """窗口的 类名 + 标题 + 进程名（小写），用于识别输入法/远程控制软件。"""
    if not hwnd:
        return ""
    try:
        import win32gui  # type: ignore
        import win32process  # type: ignore

        parts = [str(win32gui.GetClassName(int(hwnd)) or ""), str(win32gui.GetWindowText(int(hwnd)) or "")]
        _thread, pid = win32process.GetWindowThreadProcessId(int(hwnd))
        if pid:
            try:
                import psutil  # type: ignore

                parts.append(str(psutil.Process(int(pid)).name() or ""))
            except Exception:
                pass
        return " ".join(parts).lower()
    except Exception:
        return ""


def _is_remote_control_window(hwnd: int) -> bool:
    """向日葵 / ToDesk / AnyDesk 等远程控制窗口。

    实测（本机 2026-09-21 18:0x）：前台被 `AweSun.exe`（向日葵）的 `OrayUI` 窗口占着，
    它会持续抢焦点，导致 WhatsApp 的点击守卫一直拒绝操作。
    """
    haystack = _window_haystack(hwnd)
    return bool(haystack) and any(hint in haystack for hint in _REMOTE_CONTROL_HINTS)


def _point_clickable(x: int, y: int, target: int) -> bool:
    """这个屏幕坐标是否"可以点"：WhatsApp 自己的窗口，或者输入法浮层。

    其它程序（例如被挡住的微信/浏览器）一律返回 False —— 宁可不点，也不误操作。
    """
    hwnd = _point_window_hwnd(x, y)
    if not hwnd:
        return True
    # WhatsApp 是 WinUI3 + WebView2：控件属于 msedgewebview2 进程，窗口壳属于 WhatsApp.Root.exe，
    # 两个 pid 都要认。
    if _same_process_window(hwnd, target) or _same_process_window(hwnd, _primary_window_hwnd()):
        return True
    return _is_ime_like_window(hwnd)


def _click_with_mouse(node: Any) -> None:
    rect = _rect(node)
    if not rect:
        raise RuntimeError("WhatsApp 控件不可点击")
    import win32api  # type: ignore
    import win32con  # type: ignore

    # 点控件左侧 1/4 处（避开行尾的勾选/箭头图标）
    x = int(rect[0] + (rect[2] - rect[0]) * 0.25)
    y = int((rect[1] + rect[3]) / 2)
    # 鼠标点击前确认"这个坐标下就是 WhatsApp 窗口"，避免点到别的程序（例如微信）上
    target = _node_hwnd(node) or _primary_window_hwnd()
    if target:
        # 先把 WhatsApp 提到最前（后台进程抢不到前台，用置顶兜底），再等坐标可用
        _activate_window(target)
        try:
            win32gui.SetWindowPos(
                target, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
            )
        except Exception:
            pass
        deadline = time.monotonic() + 8.0
        while not _point_clickable(x, y, target):
            if time.monotonic() >= deadline:
                blocker = _point_window_hwnd(x, y)
                if _is_remote_control_window(blocker):
                    raise RuntimeError(
                        "检测到远程控制软件（%s）占着前台，它会持续抢焦点：请把它最小化后再运行接管"
                        % (_window_haystack(blocker)[:60] or "远程控制")
                    )
                raise RuntimeError("WhatsApp 窗口被其它窗口挡住或不可见，请把它移到前面后重试")
            try:
                win32gui.SetWindowPos(
                    target, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
                )
            except Exception:
                pass
            time.sleep(0.25)
    win32api.SetCursorPos((x, y))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.08)


def _set_edit_text(node: Any, value: str, *, hwnd: Optional[int] = None) -> None:
    """往 WhatsApp 输入框写值。

    实测（本机 WinUI3，2026-09-21）：WebView2 的输入框不支持 ValuePattern /
    LegacyIAccessible 写值，只能"聚焦 + 粘贴"。因此这里必须：
      1) 先确认前台是本窗口 —— 否则 ^a/^v 会打到别的程序上（用户反馈"控制了我的微信"就是这个）；
      2) 用完把用户剪贴板还原，避免污染。
    """
    text_value = str(value or "")
    if node is None or not _rect(node):
        raise RuntimeError("WhatsApp 输入框不可用")
    target = int(hwnd or 0) or _primary_window_hwnd() or _node_hwnd(node)
    if not _ensure_foreground(target):
        raise RuntimeError("WhatsApp 窗口没有拿到前台焦点，已中止输入（避免误操作到其它程序）")
    _focus_by_mouse(node, target)
    backup = _read_clipboard_text()
    try:
        set_clipboard_text(text_value)
        send_keys_simple("^a")
        time.sleep(0.03)
        send_keys_simple("^v")
        time.sleep(0.18)
    finally:
        if backup:
            try:
                set_clipboard_text(backup)
            except Exception:
                pass


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
        processes = whatsapp_processes()
        if processes:
            names = "、".join(sorted({str(item.get("name") or "?") for item in processes}))
            raise RuntimeError(
                "检测到 WhatsApp 进程（%s），但没有可操作的主窗口：请把 WhatsApp 主窗口打开并还原"
                "（不要只留托盘），然后重试。" % names
            )
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


_AUTO_REPAIR_LOCK = threading.Lock()
_AUTO_REPAIR_COOLDOWN_SECONDS = 3 * 60 * 60
_AUTO_REPAIR_STATE: Dict[str, Any] = {"running": False, "started_at": 0.0, "finished_at": 0.0,
                                      "ok": None, "message": ""}


def _dependency_report() -> Dict[str, Any]:
    """依赖体检：uiautomation 走"已装/内置副本"两级加载，其余按普通导入探测并带错误原因。"""
    uia_module, uia_error, uia_source = load_uia()
    deps: Dict[str, bool] = {"uiautomation": uia_module is not None}
    sources: Dict[str, str] = {"uiautomation": uia_source or ("installed" if uia_module else "missing")}
    errors: Dict[str, str] = {}
    if uia_module is None:
        errors["uiautomation"] = uia_error
    for name in ("win32gui", "win32process", "pyperclip", "psutil"):
        ok, error = _module_probe(name)
        deps[name] = ok
        sources[name] = _module_source(sys.modules.get(name)) if ok else "missing"
        if not ok:
            errors[name] = error
    return {"deps": deps, "errors": errors, "sources": sources, "uia_source": uia_source}


def _clipboard_backend_probe() -> str:
    if _CLIPBOARD_BACKEND.get("name"):
        return str(_CLIPBOARD_BACKEND["name"])
    for name, label in (("pyperclip", "pyperclip"), ("win32clipboard", "win32clipboard")):
        ok, _error = _module_probe(name)
        if ok:
            return label
    return "ctypes" if os.name == "nt" else "none"


def _capabilities(report: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    try:
        from .win_process_scan import process_scan_backend

        process_backend = process_scan_backend()
    except Exception:
        process_backend = "none"
    return {
        "process_scan": process_backend,
        "clipboard": _clipboard_backend_probe(),
        "uia": str((report or _dependency_report())["uia_source"] or "missing"),
    }


def _auto_repair_snapshot() -> Dict[str, Any]:
    with _AUTO_REPAIR_LOCK:
        return {
            "running": bool(_AUTO_REPAIR_STATE.get("running")),
            "ok": _AUTO_REPAIR_STATE.get("ok"),
            "message": str(_AUTO_REPAIR_STATE.get("message") or ""),
            "started_at": float(_AUTO_REPAIR_STATE.get("started_at") or 0.0),
            "finished_at": float(_AUTO_REPAIR_STATE.get("finished_at") or 0.0),
        }


def _auto_repair_worker() -> None:
    try:
        from .runtime_dependency_repair import repair_runtime_dependencies

        result = repair_runtime_dependencies()
        ok = bool(result.get("ok"))
        message = str(result.get("message") or "")
    except Exception as exc:  # noqa: BLE001
        ok = False
        message = f"{type(exc).__name__}: {exc}"
    with _AUTO_REPAIR_LOCK:
        _AUTO_REPAIR_STATE.update({"running": False, "finished_at": time.time(), "ok": ok, "message": message})
    try:
        _append_log("dependency_auto_repair", ok=ok, message=message)
    except Exception:
        pass


# 这几项缺任何一个都会让加好友/发消息直接报错（例如客户机缺 pywinauto 报 No module named 'pywinauto'）
WHATSAPP_REQUIRED_MODULES = ("uiautomation", "win32gui", "win32process", "pywinauto")


def _maybe_schedule_auto_repair(report: Dict[str, Any]) -> None:
    """必需依赖缺任意一个就自动补一次（后台线程，3 小时最多一次，不阻塞状态查询）。"""
    missing = [name for name in WHATSAPP_REQUIRED_MODULES if not report["deps"].get(name)]
    if not missing:
        return
    with _AUTO_REPAIR_LOCK:
        if _AUTO_REPAIR_STATE.get("running"):
            return
        last = max(float(_AUTO_REPAIR_STATE.get("started_at") or 0.0),
                   float(_AUTO_REPAIR_STATE.get("finished_at") or 0.0))
        if last and time.time() - last < _AUTO_REPAIR_COOLDOWN_SECONDS:
            return
        _AUTO_REPAIR_STATE.update({"running": True, "started_at": time.time(), "ok": None, "message": ""})
    threading.Thread(target=_auto_repair_worker, name="lobster-whatsapp-deps", daemon=True).start()


def status() -> Dict[str, Any]:
    report = _dependency_report()
    deps = report["deps"]
    _maybe_schedule_auto_repair(report)
    windows = _scan_windows()
    processes = whatsapp_processes()
    probed = _probe_window(windows[0]) if windows and deps["uiautomation"] else (windows[0] if windows else {})
    capabilities = _capabilities(report)
    with _ACTIVE_LOCK:
        running = _ACTIVE
        active_action = _ACTIVE_ACTION
    missing_modules = [name for name in WHATSAPP_REQUIRED_MODULES if not deps.get(name)]
    if missing_modules:
        reason = "缺少依赖：%s（点「修复运行依赖」）" % "、".join(missing_modules)
    elif not windows and processes:
        reason = (
            "检测到 WhatsApp 进程（%s），但没识别到主窗口：请把 WhatsApp 主窗口打开并还原（不要只留托盘/最小化）后重试。"
            % "、".join(sorted({str(item.get("name") or "?") for item in processes}))
        )
    else:
        reason = probed.get("reason") or ("未检测到 Windows 桌面版 WhatsApp" if not windows else "")
    # 记录一次扫描结果（20 秒节流），下次诊断包就能直接看到"进程/窗口到底长什么样"
    global _LAST_STATUS_LOG_AT
    now_ts = time.time()
    if now_ts - _LAST_STATUS_LOG_AT >= 20:
        _LAST_STATUS_LOG_AT = now_ts
        try:
            _append_log(
                "status_scan",
                ok=bool(probed.get("uia_ready")),
                desktop_found=bool(windows),
                reason=reason,
                deps=deps,
                dependency_sources=report["sources"],
                dependency_errors=report["errors"],
                capabilities=capabilities,
                auto_repair=_auto_repair_snapshot(),
                processes=[{"name": item.get("name"), "pid": item.get("pid")} for item in processes[:8]],
                candidates=[
                    {"title": row.get("title"), "class_name": row.get("class_name"),
                     "process_name": row.get("process_name"), "is_iconic": row.get("is_iconic"),
                     "match_by": row.get("match_by")}
                    for row in windows[:8]
                ],
            )
        except Exception:
            pass
    return {
        "ok": bool(probed.get("uia_ready")),
        "desktop_found": bool(windows),
        "logged_in": bool(probed.get("logged_in")),
        "running": running,
        "active_action": active_action,
        "unread_count": int(probed.get("unread_count") or 0),
        "window": probed,
        "processes": processes[:8],
        "candidates": [
            {key: row.get(key) for key in ("hwnd", "title", "class_name", "process_name",
                                           "is_visible", "is_iconic", "match_by")}
            for row in windows[:8]
        ],
        "dependencies": deps,
        "dependency_sources": report["sources"],
        "dependency_errors": report["errors"],
        "capabilities": capabilities,
        "auto_repair": _auto_repair_snapshot(),
        "reason": reason,
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
    if not _ensure_foreground(int(hwnd or 0) or _primary_window_hwnd()):
        raise RuntimeError("WhatsApp 窗口没有拿到前台焦点，已中止发送（避免误操作到其它程序）")
    set_clipboard_text(str(text or ""))
    send_keys_simple("^v")
    time.sleep(0.15)
    send_keys_simple("{ENTER}")
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


_NEW_CHAT_SEARCH_MARKERS = ("搜索姓名", "search name", "电话号码", "phone number", "@账号", "username")


def _find_new_chat_search(root: Any) -> Optional[Any]:
    """在新聊天页里找搜索框。

    先按「面板锚点」找：新聊天面板一定有「添加联系人 / 新建群组」按钮，
    搜索框就在这些按钮上方且横向重叠（不依赖会被清空的占位文案）。
    找不到再退回按占位文案匹配。
    """
    edits: List[tuple[Any, tuple[float, float, float, float]]] = []
    anchors: List[tuple[float, float, float, float]] = []
    for node, _depth in _iter_nodes(root, max_depth=CONTACT_TREE_DEPTH, max_nodes=CONTACT_TREE_NODES):
        rect = _rect(node)
        if not rect:
            continue
        if _node_type(node) == "EditControl":
            edits.append((node, rect))
        elif _node_type(node) == "ButtonControl" and _node_text(node).strip().casefold() in {
            "添加联系人", "add contact", "新建群组", "new group",
        }:
            anchors.append(rect)
    if edits and anchors:
        ax0, ay0, ax1, _ay1 = anchors[0]
        above = [
            (node, rect)
            for node, rect in edits
            if rect[1] < ay0 and rect[0] < ax1 and rect[2] > ax0 and (rect[3] - rect[1]) < 60
        ]
        if above:
            return max(above, key=lambda item: item[1][1])[0]
    for node, _rect_ in edits:
        if any(marker in _node_text(node).casefold() for marker in _NEW_CHAT_SEARCH_MARKERS):
            return node
    return None


def _open_new_chat_page(hwnd: int) -> Any:
    root = _root_for_hwnd(hwnd)
    _click_named(root, ("对话", "Chats"), required=False)
    time.sleep(0.3)
    # 上一轮失败可能在窗口里留下表单/弹层：先按 Esc 收掉，否则新聊天面板点不出来
    try:
        auto, _error, _source = load_uia()
        if auto is not None:
            auto.SendKeys("{Escape}")
            time.sleep(0.25)
    except Exception:
        pass
    search = _find_new_chat_search(_root_for_hwnd(hwnd))
    if search is not None:
        # 已经停在新聊天页（上次没关掉）：直接用，别再点「新聊天」把它 toggle 掉
        return search
    for attempt in range(3):
        root = _root_for_hwnd(hwnd)
        _click_named(root, ("新聊天", "New chat"), required=attempt == 0)
        time.sleep(0.7 + 0.4 * attempt)
        search = _find_new_chat_search(_root_for_hwnd(hwnd))
        if search is not None:
            return search
        # 点成 toggle 关掉的情况：再点一次
        _click_named(_root_for_hwnd(hwnd), ("新聊天", "New chat"), required=False)
        time.sleep(0.5)
        search = _find_new_chat_search(_root_for_hwnd(hwnd))
        if search is not None:
            return search
    raise RuntimeError("WhatsApp 新聊天页没有出现联系人搜索框")


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


_CONTACT_FORM_HINT_MARKERS = (
    "没有注册 whatsapp",
    "未注册 whatsapp",
    "not on whatsapp",
    "找不到",
    "未找到",
    "无法找到",
    "couldn't find",
    "no results",
    "无效",
    "invalid",
    "邀请对方",
    "invite",
)


def _dismiss_contact_form(hwnd: int) -> None:
    """收掉「添加联系人」表单：先点返回，再按 Esc（失败不影响主流程）。"""
    try:
        root = _root_for_hwnd(hwnd)
        if _click_named(root, ("返回", "Back"), required=False):
            time.sleep(0.35)
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        auto, _error, _source = load_uia()
        if auto is not None:
            auto.SendKeys("{Escape}")
            time.sleep(0.35)
    except Exception:  # noqa: BLE001
        pass


def _contact_save_blocked_message(hint: str = "") -> str:
    """WhatsApp 不给「保存」按钮时的可执行提示。

    实测：号码没注册 WhatsApp 时，表单只会显示一句提示并且**不出现保存按钮**，
    而这句提示在当前 WebView2 版里读不到（UIA 树里没有），所以文案必须自己能说明问题。
    """
    message = "WhatsApp 没给出「保存」按钮：这个号码可能没注册 WhatsApp，或已经是你的联系人"
    return message + ("；表单提示：" + hint if hint else "")


def _contact_form_hint(root: Any) -> str:
    """读 WhatsApp「添加联系人」表单自己的提示文案。

    实测：号码没注册 WhatsApp、或用户名找不到时，表单不会出现「保存」按钮，
    而是显示一句提示（例如「此电话号码没有注册 WhatsApp。请在主要设备上邀请对方。」）。
    把这句原文带进错误里，用户才能立刻知道该改号码还是改用户名。
    """
    hits: List[str] = []
    for node, _depth in _iter_nodes(root, max_depth=CONTACT_TREE_DEPTH, max_nodes=CONTACT_TREE_NODES):
        text = re.sub(r"\s+", " ", _node_text(node)).strip()
        if not text or len(text) > 200:
            continue
        folded = text.casefold()
        if any(marker in folded for marker in _CONTACT_FORM_HINT_MARKERS) and text not in hits:
            hits.append(text)
        if len(hits) >= 3:
            break
    return " / ".join(hits)


# 每次 UIA 遍历的规模上限。
# 旧实现的问题是"遍历整棵树再挑最后一个"，客户机树大时一次加好友要跑好几分钟；
# 现在关键查找都改成"命中即返回"，所以上限保留大一点（保证找得到）也不会慢。
CONTACT_TREE_NODES = 12000
CONTACT_TREE_DEPTH = 22
# 单个联系人从打开表单到点保存的软超时；超了就明确失败，不留「执行中」
ADD_CONTACT_DEADLINE_SECONDS = 60.0


_COUNTRY_NAME_HINTS = {
    "86": ("中国", "china"),
    "852": ("中国香港", "hong kong", "香港"),
    "853": ("中国澳门", "macau", "澳门"),
    "886": ("中国台湾", "taiwan", "台湾"),
}


def _country_item_matches(text: str, digits: str) -> bool:
    """国家列表项是否匹配目标码。

    实测（本机 WhatsApp WinUI3）：列表项文本形如 `🇮🇹 意大利 Italia +39`；
    但「中国」那条不带 +86，所以再按国家名兜底。
    """
    value = str(text or "")
    want = re.sub(r"[^0-9]", "", str(digits or ""))
    if not want:
        return False
    if re.search(r"\+%s(?![0-9])" % re.escape(want), value):
        return True
    lowered = value.lower()
    for hint in _COUNTRY_NAME_HINTS.get(want, ()):
        if hint and hint in lowered:
            return True
    return False


def _find_contact_country_button(root: Any) -> Optional[Any]:
    for node, _depth in _iter_nodes(root, max_depth=CONTACT_TREE_DEPTH, max_nodes=CONTACT_TREE_NODES):
        if _node_type(node) != "ButtonControl" or not _rect(node):
            continue
        if "国家/地区" in _node_text(node):
            return node
    return None


def _find_contact_country_search(root: Any, list_top: Optional[int] = None) -> Optional[Any]:
    best: Optional[tuple] = None
    skip = {"搜索或开始新聊天", "名字", "姓氏", "用户名", "电话号码"}
    for node, depth in _iter_nodes(root, max_depth=CONTACT_TREE_DEPTH, max_nodes=CONTACT_TREE_NODES):
        if _node_type(node) != "EditControl":
            continue
        rect = _rect(node)
        if not rect or _node_text(node).strip() in skip:
            continue
        if list_top is not None and rect[1] > list_top:
            continue
        if best is None or depth < best[0]:
            best = (depth, node)
    return best[1] if best else None


def _find_contact_country_list(root: Any) -> Optional[Any]:
    for node, _depth in _iter_nodes(root, max_depth=CONTACT_TREE_DEPTH, max_nodes=CONTACT_TREE_NODES):
        if _node_type(node) == "ListControl" and _rect(node):
            return node
    return None


def _normalize_country_digits(country_code: str) -> str:
    """把 +86 / 86 / +8618124655127（脏数据）统一成国家码数字 "86"。"""
    raw = re.sub(r"[^0-9]", "", str(country_code or ""))
    if not raw:
        return "86"
    for size in (3, 2, 1):
        if len(raw) >= size and raw[:size] in _COMMON_COUNTRY_CODES:
            return raw[:size]
    return "86"


def select_contact_country(hwnd: int, country_code: str) -> str:
    """把「添加联系人」表单的国家/地区切到目标码，返回切换后按钮文本（如「国家/地区：意大利 +39」）。"""
    digits = _normalize_country_digits(country_code)
    root = _root_for_hwnd(hwnd)
    button = _find_contact_country_button(root)
    if button is None:
        raise RuntimeError("WhatsApp 添加联系人表单没有出现国家/地区选择")
    current = _node_text(button)
    if _country_item_matches(current, digits):
        return current
    _click(button)
    time.sleep(0.9)
    root = _root_for_hwnd(hwnd)
    list_node = _find_contact_country_list(root)
    search = _find_contact_country_search(root, _rect(list_node)[1] if list_node is not None else None)
    if search is None:
        raise RuntimeError("WhatsApp 国家/地区列表没有出现搜索框")
    _set_edit_text(search, digits)
    time.sleep(1.0)
    root = _root_for_hwnd(hwnd)
    target = None
    for node, _depth in _iter_nodes(root, max_depth=CONTACT_TREE_DEPTH, max_nodes=CONTACT_TREE_NODES):
        if _node_type(node) != "ButtonControl" or not _rect(node):
            continue
        text = _node_text(node).strip()
        if not text or text.startswith("所选国家/地区"):
            continue
        if _country_item_matches(text, digits):
            target = node
            break
    if target is None:
        raise RuntimeError(f"WhatsApp 国家/地区列表里没有 +{digits}，请在 WhatsApp 表单里手动选择国家后重试")
    _click(target)
    time.sleep(0.9)
    root = _root_for_hwnd(hwnd)
    confirm_button = _find_contact_country_button(root)
    confirmed = _node_text(confirm_button) if confirm_button is not None else ""
    if not _country_item_matches(confirmed, digits):
        raise RuntimeError(f"WhatsApp 国家/地区没有切换成功（当前显示：{confirmed or '未知'}）")
    return confirmed


def verify_contact_added(
    hwnd: int,
    *,
    first_name: str,
    phone: str = "",
    username: str = "",
    country_code: str = "+86",
) -> Dict[str, Any]:
    """点完保存后再确认一次：在新聊天页搜这个目标，看是否真的进了联系人。

    实测（本机 WinUI3，2026-09-21）：号码已经是联系人时，搜索结果里会先出现一张
    带姓名的联系人卡片，随后才是「联系人」分组标题；没加上的目标搜不出这张卡片。
    """
    name = str(first_name or "").strip()
    digits = re.sub(r"[^0-9]", "", str(phone or ""))
    user = str(username or "").lstrip("@").strip()
    probe = user or digits
    if not probe:
        return {"checked": False, "reason": "没有可搜索的目标"}
    not_in_contacts_markers = ("不在你的联系人中", "不是你的联系人", "not in your contacts")
    search = _open_new_chat_page(hwnd)
    _set_edit_text(search, probe, hwnd=hwnd)
    time.sleep(1.2)
    root = _root_for_hwnd(hwnd)
    search_rect = _rect(search) or (0, 0, 0, 0)
    search_bottom = int(search_rect[3] or 0)
    group_y = None
    not_in_contacts = False
    matched: List[str] = []
    for node, _depth in _iter_nodes(root, max_depth=CONTACT_TREE_DEPTH, max_nodes=CONTACT_TREE_NODES):
        rect = _rect(node)
        if not rect or rect[0] < 1060:
            continue
        value = _node_text(node).strip()
        if not value:
            continue
        folded_value = value.casefold()
        if any(marker in folded_value for marker in not_in_contacts_markers):
            not_in_contacts = True
            continue
        if group_y is None and value in {"联系人", "Contacts"}:
            group_y = int(rect[1])
            continue
        if search_bottom and rect[1] < search_bottom:
            continue
        if group_y is not None and rect[1] > group_y:
            continue
        folded = value.casefold()
        if name and folded == name.casefold():
            matched.append(value)
        elif user and folded == user.casefold():
            matched.append(value)
    try:
        _set_edit_text(search, "", hwnd=hwnd)
        time.sleep(0.3)
    except Exception:
        pass
    found = bool(matched)
    if found:
        state = "in_contacts"
        note = ""
    elif not_in_contacts:
        state = "not_in_contacts"
        note = "WhatsApp 显示「不在你的联系人中」"
    else:
        state = "not_found"
        note = "搜索里找不到这个号码（可能没注册 WhatsApp）"
    return {
        "checked": True,
        "found": found,
        "state": state,
        "not_in_contacts": not_in_contacts,
        "probe": probe,
        "group_seen": group_y is not None,
        "matched": matched[:3],
        "note": note,
    }


def _collect_contact_form_fields(root: Any) -> Dict[str, Any]:
    """收集「添加联系人」表单的输入框（名字/姓氏/用户名/电话号码）。"""
    aliases = {
        "first_name": {"名字", "first name"},
        "last_name": {"姓氏", "last name"},
        "username": {"用户名", "username"},
        "phone": {"电话号码", "phone number"},
    }
    fields: Dict[str, Any] = {}
    for node, _depth in _iter_nodes(root, max_depth=CONTACT_TREE_DEPTH, max_nodes=CONTACT_TREE_NODES):
        if _node_type(node) != "EditControl" or not _rect(node):
            continue
        label = _node_text(node).strip().casefold()
        for key, names in aliases.items():
            if label in names and key not in fields:
                fields[key] = node
        if len(fields) == len(aliases):
            break  # 四个字段都拿到了，不用再往下遍历
    return fields


def _click_button_matching(
    root: Any,
    needles: Iterable[str],
    *,
    max_depth: int = CONTACT_TREE_DEPTH,
    max_nodes: int = CONTACT_TREE_NODES,
    dry_run: bool = False,
) -> bool:
    """按「包含」匹配点按钮。

    实测（本机 WinUI3，2026-09-21）：号码有效时表单底部的按钮叫「保存联系人」，
    而旧实现是精确匹配「保存 / Save」→ 永远找不到 → 报「没给出保存按钮」，
    这正是客户机加好友一直失败的原因。

    命中即返回（旧写法遍历完整棵树，客户机上慢到几分钟）；dry_run 只探测不点击。
    """
    wanted = tuple(str(item).casefold() for item in needles if str(item or "").strip())
    if not wanted:
        return False
    for node, _depth in _iter_nodes(root, max_depth=max_depth, max_nodes=max_nodes):
        if _node_type(node) not in {"ButtonControl", "SplitButtonControl"} or not _rect(node):
            continue
        text = _node_text(node).strip().casefold()
        if not text:
            continue
        if any(needle in text for needle in wanted):
            if dry_run:
                return True
            _click(node)
            return True
    return False


def add_contact(*, first_name: str, last_name: str = "", username: str = "", phone: str = "", country_code: str = "+86") -> Dict[str, Any]:
    first = str(first_name or "").strip()[:200]
    last = str(last_name or "").strip()[:200]
    user = str(username or "").strip()[:240]
    digits = _normalize_country_digits(country_code)
    country = f"+{digits}"
    number = re.sub(r"[^0-9]", "", str(phone or "").strip())[:40]
    if number.startswith(digits):
        number = number[len(digits):]
    # target 先算出来：下面任何一步失败时，except 里记录错误都要用到它
    target = user or f"{country}{number}"
    if not first:
        raise RuntimeError("请填写联系人名字")
    if not user and not number:
        raise RuntimeError("请填写 WhatsApp 用户名或电话号码")
    _claim_action("添加 WhatsApp 联系人")
    clear_friend_add_cancel(DEFAULT_ACCOUNT_ID)
    hwnd: Optional[int] = None
    deadline = time.monotonic() + ADD_CONTACT_DEADLINE_SECONDS
    steps: List[str] = []

    def mark(step: str, started_at: float) -> None:
        steps.append("%s=%.1fs" % (step, time.monotonic() - started_at))

    try:
        started = time.monotonic()
        hwnd, _window = _window_or_raise()
        _open_new_chat_page(hwnd)
        mark("open_new_chat", started)
        if friend_add_cancelled(DEFAULT_ACCOUNT_ID):
            raise FriendAddCancelled("已手动停止")
        root = _root_for_hwnd(hwnd)
        add_buttons = [
            node for node, _depth in _iter_nodes(root, max_depth=20, max_nodes=CONTACT_TREE_NODES)
            if _node_type(node) == "ButtonControl" and _node_text(node).strip().casefold() in {"添加联系人", "add contact"} and _rect(node)
        ]
        if not add_buttons:
            raise RuntimeError("WhatsApp 新聊天页没有出现添加联系人入口")
        _click(add_buttons[-1])
        time.sleep(0.6)
        started = time.monotonic()
        root = _root_for_hwnd(hwnd)
        fields = _collect_contact_form_fields(root)
        mark("open_form", started)
        if "first_name" not in fields:
            raise RuntimeError("WhatsApp 添加联系人表单没有出现")
        # 先切国家/地区（切换会让表单重建），再填其余字段，避免刚填的内容被清掉
        if number:
            started = time.monotonic()
            select_contact_country(hwnd, digits)
            time.sleep(0.3)
            mark("select_country", started)
            root = _root_for_hwnd(hwnd)
            fields = _collect_contact_form_fields(root)
            if "first_name" not in fields:
                raise RuntimeError("切换国家/地区后 WhatsApp 联系人表单没有回来")
        started = time.monotonic()
        _set_edit_text(fields["first_name"], first)
        if last and fields.get("last_name") is not None:
            _set_edit_text(fields["last_name"], last)
        if user and fields.get("username") is not None:
            _set_edit_text(fields["username"], user.lstrip("@"))
        if number and fields.get("phone") is not None:
            _set_edit_text(fields["phone"], number)
        mark("fill_fields", started)
        if friend_add_cancelled(DEFAULT_ACCOUNT_ID):
            raise FriendAddCancelled("已手动停止")
        if time.monotonic() > deadline:
            raise RuntimeError("添加联系人超时：WhatsApp 界面响应太慢，请重试")
        # 实测：号码填完后 WhatsApp 要异步校验，按钮（叫「保存联系人」）不是立刻出现，
        # 所以轮询等待最多 8 秒；旧实现是精确匹配「保存 / Save」→ 永远点不到。
        started = time.monotonic()
        if friend_add_cancelled(DEFAULT_ACCOUNT_ID):
            raise FriendAddCancelled("已手动停止")
        saved = False
        for _wait in range(8):
            if _click_button_matching(_root_for_hwnd(hwnd), ("保存", "save")):
                saved = True
                break
            time.sleep(0.6)
        mark("click_save", started)
        if not saved:
            raise RuntimeError(_contact_save_blocked_message(_contact_form_hint(_root_for_hwnd(hwnd))))
        time.sleep(0.8)
        started = time.monotonic()
        root = _root_for_hwnd(hwnd)
        still_editing = "first_name" in _collect_contact_form_fields(root)
        mark("verify_saved", started)
        if still_editing:
            hint = _contact_form_hint(root)
            raise RuntimeError("WhatsApp 仍停留在联系人表单，未确认保存成功" + ("；表单提示：" + hint if hint else ""))
        contact = _persist_contact({
            "first_name": first, "last_name": last, "username": user.lstrip("@"),
            "phone": f"{country}{number}" if number else "",
            "country_code": country, "display_name": " ".join(part for part in (first, last) if part),
            "source": "desktop_add_contact",
        })
        result = {"ok": True, "contact": contact, "message": "WhatsApp 联系人已保存", "steps": steps}
        # 加完再自己搜一次确认：成功失败都不猜，给用户一个明确结论
        try:
            verify = verify_contact_added(
                hwnd, first_name=first, phone=number, username=user.lstrip("@"), country_code=country,
            )
        except Exception as exc:  # noqa: BLE001
            verify = {"checked": False, "reason": "确认失败：%s" % exc}
        result["verify"] = verify
        if verify.get("checked") and not verify.get("found"):
            if verify.get("state") == "not_in_contacts":
                result["message"] = "已点保存，但 WhatsApp 仍显示「不在你的联系人中」"
            else:
                result["message"] = "已点保存，但搜索不到这个号码"
        _record_operation("add_contact", target, "success", result["message"], result)
        _append_log("add_contact_done", target=target, steps=steps, verify=verify)
        return result
    except Exception as exc:
        _record_operation("add_contact", target, "failed", str(exc))
        _append_log("add_contact_failed", target=target, error=str(exc)[:500], steps=steps)
        if hwnd:
            # 失败时把表单收掉：残留的表单会让下一轮找不到「新聊天」搜索框
            _dismiss_contact_form(hwnd)
        raise
    finally:
        _release_action()


def _server_proxy_base() -> str:
    value = str(getattr(settings, "lobster_server_url", None) or "").strip().rstrip("/")
    return value or "https://h5.bhzn.top"


def _load_auto_reply_memory_context(
    user_id: Optional[int],
    *,
    max_chars: int = 12000,
    max_docs: int = 5,
    selected_doc_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """把选中的「记忆文件」内容读出来，供回复时注入（与个微同一份实现思路）。"""
    empty = {"text": "", "document_count": 0, "titles": []}
    if not user_id:
        return empty
    try:
        from ..api.openclaw_memory import _load_index, _read_canonical_memory_content  # type: ignore
    except Exception:
        return empty
    try:
        docs = _load_index(int(user_id))
    except Exception:
        return empty
    selected = list(dict.fromkeys(
        str(item or "").strip() for item in (selected_doc_ids or []) if str(item or "").strip()
    ))
    selected_set = set(selected)
    picked: List[tuple] = []
    for index, doc in enumerate(docs or []):
        if not isinstance(doc, dict):
            continue
        doc_id = str(doc.get("id") or doc.get("doc_id") or "").strip()
        if selected_set and doc_id not in selected_set:
            continue
        status = str(doc.get("status") or "active").strip().lower()
        if status not in {"", "active", "enabled", "ready"}:
            continue
        rank = (len(selected) - selected.index(doc_id)) if (selected_set and doc_id in selected_set) else 0
        picked.append((rank, -index, doc))
    picked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    parts: List[str] = []
    titles: List[str] = []
    used = 0
    for _rank, _index, doc in picked[: max(1, int(max_docs or 1))]:
        title = str(doc.get("title") or doc.get("filename") or "记忆").strip()
        remaining = max_chars - used
        if remaining <= 0:
            break
        content = _read_canonical_memory_content(doc, max_chars=min(5000, remaining))
        if not content:
            continue
        block = "## %s\n%s" % (title, content.strip())
        parts.append(block)
        titles.append(title[:120])
        used += len(block)
        if used >= max_chars:
            break
    return {
        "text": "\n\n---\n\n".join(parts).strip()[:max_chars],
        "document_count": len(titles),
        "titles": titles,
    }


def _reset_to_chat_list(hwnd: int, *, attempts: int = 3) -> bool:
    """把 WhatsApp 收回聊天列表（收掉残留的表单/建群面板）。"""
    auto = None
    try:
        auto, _error, _source = load_uia()
    except Exception:
        auto = None
    for _attempt in range(max(1, attempts)):
        root = _root_for_hwnd(hwnd)
        if _find_button(root, ("新建群组", "新 group")) is not None:
            return True
        back = _find_button(root, ("返回", "Back"))
        if back is not None:
            try:
                _click(back)
                _void = None
                time.sleep(0.6)
                continue
            except Exception:
                pass
        if auto is not None:
            try:
                auto.SendKeys("{Escape}")
                time.sleep(0.5)
            except Exception:
                pass
    root = _root_for_hwnd(hwnd)
    return _find_button(root, ("新建群组", "new group")) is not None


async def _report_intelligence_observation(
    payload: Dict[str, Any],
    *,
    auth_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """把这一轮接管结果回写给接管中枢（与个微同一套 observe，channel=whatsapp）。"""
    context = auth_context or {}
    token = str(context.get("token") or "").strip()
    if not token:
        return {"ok": False, "reason": "missing_token"}
    body = {"channel": "whatsapp"}
    body.update({key: value for key, value in (payload or {}).items() if value is not None})
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0), trust_env=False) as client:
            response = await client.post(
                f"{_server_proxy_base()}/api/wechat-intelligence/observe",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "X-Installation-Id": str(context.get("installation_id") or "native-whatsapp")[:160],
                },
                json=body,
            )
        if response.status_code >= 400:
            raise RuntimeError("HTTP %s: %s" % (response.status_code, (response.text or "")[:200]))
        _append_log("intelligence_reported", event=body.get("event_type"), status=body.get("status"))
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        _append_log("intelligence_report_failed", event=body.get("event_type"), error=str(exc)[:300])
        return {"ok": False, "error": str(exc)[:300]}


def _key_event(vk: int, *, up: bool = False) -> None:
    import win32api  # type: ignore
    import win32con  # type: ignore

    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP if up else 0, 0)


def send_keys_simple(keys: str) -> None:
    """只发 Ctrl+字母 / Enter（本项目就用这几个），不再依赖 pywinauto。

    客户机上没装 pywinauto 时会直接报 "No module named 'pywinauto'"；
    这里换成 pywin32（客户端必装）的 keybd_event，少一个依赖。
    """
    import win32con  # type: ignore

    lowered = str(keys or "").strip().lower()
    if lowered.startswith("^") and len(lowered) == 2:
        vk = ord(lowered[1].upper())
        _key_event(win32con.VK_CONTROL)
        _key_event(vk)
        time.sleep(0.02)
        _key_event(vk, up=True)
        _key_event(win32con.VK_CONTROL, up=True)
        return
    if lowered in {"{enter}", "enter", "{return}"}:
        _key_event(win32con.VK_RETURN)
        time.sleep(0.02)
        _key_event(win32con.VK_RETURN, up=True)
        return
    raise RuntimeError("不支持的按键：%s" % keys)


def _find_button(
    root: Any,
    names: Iterable[str],
    *,
    max_depth: int = CONTACT_TREE_DEPTH,
    max_nodes: int = CONTACT_TREE_NODES,
) -> Optional[Any]:
    """只找不点：先精确匹配，再按"包含"匹配（WhatsApp 的按钮文案经常带后缀）。"""
    wanted = tuple(str(item).casefold() for item in names if str(item or "").strip())
    if not wanted:
        return None
    exact: Optional[Any] = None
    loose: Optional[Any] = None
    for node, _depth in _iter_nodes(root, max_depth=max_depth, max_nodes=max_nodes):
        if _node_type(node) not in {"ButtonControl", "SplitButtonControl"} or not _rect(node):
            continue
        label = _node_text(node).strip().casefold()
        if not label:
            continue
        if label in wanted:
            if exact is None:
                exact = node
        elif loose is None and any(item in label for item in wanted):
            loose = node
    return exact or loose


def _group_member_rows(root: Any) -> List[Dict[str, Any]]:
    """群成员选择页里的成员行（实测：接近整行宽的 ButtonControl，文本就是联系人名）。"""
    rows: List[Dict[str, Any]] = []
    for node, depth in _iter_nodes(root, max_depth=CONTACT_TREE_DEPTH, max_nodes=CONTACT_TREE_NODES):
        if _node_type(node) != "ButtonControl" or not _rect(node):
            continue
        rect = _rect(node)
        label = _node_text(node).strip()
        if not label or len(label) > 60:
            continue
        if (rect[2] - rect[0]) < 180 or rect[1] < 360:
            continue
        rows.append({"node": node, "name": label, "rect": rect, "depth": depth})
    return rows


def _find_group_name_field(root: Any) -> Optional[Any]:
    """群名页的输入框（实测在面板中部，右侧是「打开表情符号面板」按钮）。"""
    for node, _depth in _iter_nodes(root, max_depth=CONTACT_TREE_DEPTH, max_nodes=CONTACT_TREE_NODES):
        if _node_type(node) != "EditControl" or not _rect(node):
            continue
        rect = _rect(node)
        if rect[0] < 1060:
            continue
        if 430 <= rect[1] <= 700 and (rect[2] - rect[0]) >= 80:
            return node
    return None


def group_invite_hit(message: str, keywords: str) -> bool:
    """这条消息是否命中拉群关键词（逗号/分号/空格分隔）。"""
    body = str(message or "").casefold()
    wanted = [item.casefold() for item in re.split(r"[,，;；\s]+", str(keywords or "")) if item.strip()]
    if not body or not wanted:
        return False
    return any(item in body for item in wanted)


def create_group(*, name: str, members: Iterable[str], welcome_message: str = "", dry_run: bool = False) -> Dict[str, Any]:
    """新建 WhatsApp 群组。

    实测流程（2026-09-21 本机 WinUI3）：
      新聊天页「新建群组」→「添加群组成员」选成员（成员行必须鼠标点击）→「下一步」
      → 群名页填群名 →「创建群组」。
    """
    group_name = str(name or "").strip()[:60]
    member_names = [str(item or "").strip()[:120] for item in (members or []) if str(item or "").strip()]
    if not group_name:
        raise RuntimeError("请填写群名")
    if not member_names:
        raise RuntimeError("请至少指定一个群成员")
    _claim_action("新建 WhatsApp 群组")
    hwnd: Optional[int] = None
    steps: List[str] = []
    started = time.monotonic()
    try:
        hwnd, _window = _window_or_raise()
        _reset_to_chat_list(hwnd)
        _open_new_chat_page(hwnd)
        root = _root_for_hwnd(hwnd)
        entry = _find_button(root, ("新建群组", "new group"))
        if entry is None:
            raise RuntimeError("新聊天页没有出现「新建群组」入口")
        _click(entry)
        time.sleep(1.2)
        steps.append("open=%.1fs" % (time.monotonic() - started))

        for member in member_names:
            root = _root_for_hwnd(hwnd)
            box = _find_new_chat_search(root)
            if box is not None:
                _set_edit_text(box, member, hwnd=hwnd)
                time.sleep(0.8)
            root = _root_for_hwnd(hwnd)
            row = next(
                (item for item in _group_member_rows(root) if item["name"].casefold() == member.casefold()),
                None,
            )
            if row is None:
                raise RuntimeError("群成员列表里找不到「%s」（必须已经是联系人）" % member)
            _click(row["node"], force_mouse=True)  # 实测：成员行 UIA Invoke 无效，只能鼠标点
            time.sleep(0.6)
            steps.append("member:%s" % member)

        root = _root_for_hwnd(hwnd)
        nxt = _find_button(root, ("下一步", "next"))
        if nxt is None:
            raise RuntimeError("选完成员后没有出现「下一步」")
        _click(nxt, force_mouse=True)
        time.sleep(1.3)

        root = _root_for_hwnd(hwnd)
        name_field = _find_group_name_field(root)
        if name_field is None:
            raise RuntimeError("群名输入框没有出现")
        _set_edit_text(name_field, group_name, hwnd=hwnd)
        time.sleep(0.5)
        if str(_node_value(name_field) or "").strip() != group_name:
            raise RuntimeError("群名没有填进去")
        steps.append("name")

        root = _root_for_hwnd(hwnd)
        create_button = _find_button(root, ("创建群组", "create group"))
        if create_button is None:
            raise RuntimeError("没有出现「创建群组」按钮")
        if dry_run:
            steps.append("dry_run_stop")
            return {
                "ok": True,
                "dry_run": True,
                "name": group_name,
                "members": member_names,
                "steps": steps,
                "message": "已到群名页（演练模式，未创建）",
            }
        _click(create_button, force_mouse=True)
        time.sleep(1.8)
        steps.append("created")

        result: Dict[str, Any] = {
            "ok": True,
            "name": group_name,
            "members": member_names,
            "steps": steps,
            "message": "已创建群组「%s」（%d 人）" % (group_name, len(member_names)),
        }
        if welcome_message:
            try:
                send_message(group_name, str(welcome_message)[:1000])
                result["welcome_sent"] = True
            except Exception as exc:  # noqa: BLE001
                result["welcome_sent"] = False
                result["welcome_error"] = str(exc)[:200]
        _record_operation("create_group", group_name, "success", result["message"], result)
        _append_log("group_created", name=group_name, members=member_names, steps=steps)
        return result
    except Exception as exc:
        _record_operation("create_group", group_name, "failed", str(exc)[:300], {"steps": steps})
        _append_log("group_create_failed", name=group_name, error=str(exc)[:300], steps=steps)
        if hwnd:
            try:
                _dismiss_contact_form(hwnd)
            except Exception:
                pass
        raise
    finally:
        _release_action()


async def _generate_reply(
    latest_message: str,
    recent_context: str,
    *,
    auth_context: Optional[Dict[str, Any]],
    instruction: str,
    memory_text: str = "",
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
    if memory_text:
        system_prompt += "\n参考资料（优先遵守，不要照抄原文）：\n" + str(memory_text)[:12000]
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
    memory_context = _load_auto_reply_memory_context(
        int((auth_context or {}).get("user_id") or 0) or None,
        max_chars=12000,
        max_docs=5,
        selected_doc_ids=cfg.get("memory_doc_ids") if isinstance(cfg.get("memory_doc_ids"), list) else [],
    )
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
                        memory_text=str(memory_context.get("text") or ""),
                    )
                    if _STOP_REQUESTED.is_set():
                        result["stop_reason"] = "cancelled"
                        break
                    sent = await loop.run_in_executor(_UI_EXECUTOR, _send_current_message, hwnd, reply)
                    try:
                        report = await _report_intelligence_observation(
                            {
                                "account_id": DEFAULT_ACCOUNT_ID,
                                "contact_key": "wa:" + str(snapshot.get("peer_key") or snapshot.get("peer_name") or "")[:200],
                                "contact_name": str(snapshot.get("peer_name") or "")[:240],
                                "event_type": "auto_reply",
                                "status": "completed" if sent.get("sent") else "failed",
                                "inbound_text": text[:4000],
                                "reply_text": reply[:4000],
                                "error_message": "" if sent.get("sent") else "发送未确认",
                            },
                            auth_context=auth_context,
                        )
                        item["intelligence_report"] = "ok" if report.get("ok") else "failed"
                    except Exception as exc:  # noqa: BLE001
                        item["intelligence_report"] = "failed"
                        _append_log("intelligence_report_error", error=str(exc)[:200])
                    # 命中拉群关键词：把配置里的成员（加上当前对话人）拉成一个群
                    if bool(cfg.get("group_invite_enabled")) and group_invite_hit(
                        text, str(cfg.get("group_invite_keywords") or "")
                    ):
                        try:
                            invite_members = list(cfg.get("group_invite_contacts") or [])
                            peer_label = str(snapshot.get("peer_name") or "").strip()
                            if peer_label and peer_label not in invite_members:
                                invite_members.append(peer_label)
                            invite_name = str(cfg.get("group_invite_group_name") or "").strip() or (
                                "群-" + (peer_label[:20] or "客户")
                            )
                            invite_result = await loop.run_in_executor(
                                _UI_EXECUTOR,
                                lambda: create_group(
                                    name=invite_name,
                                    members=invite_members,
                                    welcome_message=str(cfg.get("group_invite_welcome_message") or ""),
                                ),
                            )
                            result["group_created"] = invite_result.get("name")
                            item["group_created"] = invite_result.get("name")
                            try:
                                await _report_intelligence_observation(
                                    {
                                        "account_id": DEFAULT_ACCOUNT_ID,
                                        "contact_key": "wa:" + str(snapshot.get("peer_key") or peer_label)[:200],
                                        "contact_name": peer_label[:240],
                                        "event_type": "group_invite",
                                        "status": "completed",
                                        "payload": {"group": invite_result.get("name"),
                                                    "members": invite_result.get("members") or []},
                                    },
                                    auth_context=auth_context,
                                )
                            except Exception:  # noqa: BLE001
                                pass
                        except Exception as exc:  # noqa: BLE001
                            result["group_invite_failed"] = str(exc)[:200]
                            item["group_invite_failed"] = str(exc)[:200]
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


# ── 批量加好友队列 + 常驻接管（对齐微信协议助手，2026-09-21）──────────────────
# 目的：把「个人 WhatsApp 助手」的加好友与接管能力对齐微信协议助手：
#   * 批量加好友：一行一个目标（电话 / 名字,电话 / @用户名），逐条入队，
#     按间隔慢慢加、按日额度封顶、逐条留记录，可随时启停；
#   * 接管：除单轮执行外，支持常驻轮次（按间隔自动跑下一轮）与诊断摘要。
DEFAULT_FRIEND_ADD_INTERVAL_SECONDS = 45
DEFAULT_FRIEND_ADD_DAILY_LIMIT = 30
# 实测：找不到「保存联系人」这类确定性失败重试也不会成功，反而让用户看着「执行中」等几分钟。
DEFAULT_FRIEND_ADD_RETRY_MAX = 0
DEFAULT_FRIEND_ADD_RETRY_SLEEP = 3.0

_FRIEND_ADD_SCHEDULERS: Dict[str, Any] = {}
_FRIEND_ADD_WAKE_EVENTS: Dict[str, Any] = {}
_AUTO_REPLY_LOOPS: Dict[str, Any] = {}


class FriendAddDailyLimitReached(RuntimeError):
    """今日加好友额度已用完：任务留在队列里等额度窗口重置，而不是标记失败。"""


def _friend_add_quota_reset_seconds() -> float:
    """距离下一次日额度重置的秒数（本地时间次日 0 点 + 2 分钟缓冲）。"""
    now = datetime.now()
    tomorrow = datetime(now.year, now.month, now.day) + timedelta(days=1)
    return max(60.0, (tomorrow - now).total_seconds() + 120.0)


_COMMON_COUNTRY_CODES = frozenset(
    """1 7 20 27 30 31 32 33 34 36 39 40 41 43 44 45 46 47 48 49 51 52 53 54 55 56 57 58 60 61 62 63 64 65 66
    81 82 84 86 90 91 92 93 94 95 98 211 212 213 216 218 220 221 222 223 224 225 226 227 228 229 230 231 232 233
    234 235 236 237 238 239 240 241 242 243 244 245 246 247 248 249 250 251 252 253 254 255 256 257 258 260 261
    262 263 264 265 266 267 268 269 290 291 297 298 299 350 351 352 353 354 355 356 357 358 359 370 371 372 373
    374 375 376 377 378 379 380 381 382 383 385 386 387 389 420 421 423 500 501 502 503 504 505 506 507 508 509
    590 591 592 593 594 595 596 597 598 599 670 672 673 674 675 676 677 678 679 680 681 682 683 685 686 687 688
    689 690 691 692 850 852 853 855 856 880 886 960 961 962 963 964 965 966 967 968 970 971 972 973 974 975 976
    977 979 992 993 994 995 996 998""".split()
)


def _split_country_code(digits: str) -> "tuple[str, str]":
    """把 `+8613800138001` 拆成 ("+86", "13800138001")；识别不出国家码时返回原号码。"""
    body = str(digits or "").lstrip("+")
    for size in (3, 2, 1):
        if len(body) > size + 5 and body[:size] in _COMMON_COUNTRY_CODES:
            return "+" + body[:size], body[size:]
    return "", body


def _parse_target_line(line: str) -> Optional[Dict[str, str]]:
    """解析一行目标，字段对齐 WhatsApp「添加联系人」表单（名字/姓氏/@用户名/电话）。

    支持：`姓名,电话`、`姓名,姓氏,电话`、`姓名,@用户名`、`姓名,姓氏,@用户名`；
    只写一个号码时不带姓名，由 create_add_contact_task 拒绝入队（WhatsApp 表单必须有名字）。
    """
    text = str(line or "").strip()
    if not text:
        return None
    parts = [part.strip() for part in re.split(r"[,，\t]+", text) if part.strip()]
    if not parts:
        return None
    if len(parts) >= 3:
        first_name, last_name, contact = parts[0], parts[1], parts[-1]
    elif len(parts) == 2:
        first_name, last_name, contact = parts[0], "", parts[1]
    else:
        first_name, last_name, contact = "", "", parts[0]
    raw_contact = contact.lstrip("@").strip()
    if not raw_contact:
        return None
    if contact.startswith("@"):
        return {"first_name": first_name, "last_name": last_name, "username": raw_contact, "phone": "", "country_code": ""}
    digits = re.sub(r"[^0-9+]", "", contact)
    if not digits:
        return {"first_name": first_name, "last_name": last_name, "username": raw_contact, "phone": "", "country_code": ""}
    country_code = ""
    number = digits
    if digits.startswith("+"):
        country_code, number = _split_country_code(digits)
    return {
        "first_name": first_name,
        "last_name": last_name,
        "username": "",
        "phone": number,
        "country_code": country_code or "+86",
    }


def _missing_name_targets(targets: Iterable[Dict[str, Any]]) -> List[str]:
    """找出「只写了号码、没写姓名」的目标：WhatsApp 添加联系人表单必须有名字。"""
    missing: List[str] = []
    for target in targets or []:
        if str(target.get("first_name") or "").strip():
            continue
        missing.append(_target_label(target))
    return missing


def normalize_friend_targets(raw_targets: Iterable[Any]) -> List[Dict[str, str]]:
    """把"一行一个目标"的批量文本解析成结构化目标（自动去重）。"""
    out: List[Dict[str, str]] = []
    seen: set = set()
    for raw in raw_targets or []:
        for line in re.split(r"[\r\n;；]+", str(raw or "")):
            target = _parse_target_line(line)
            if not target:
                continue
            if not str(target.get("first_name") or "").strip() and target.get("username"):
                # 只写 @用户名 时用用户名当名字（表单必填项不能空）
                target["first_name"] = str(target["username"])
            key = str(target.get("username") or target.get("phone") or target.get("first_name") or "").casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(target)
    return out


def _target_label(target: Dict[str, Any]) -> str:
    username = str(target.get("username") or "").strip()
    if username:
        return "@" + username.lstrip("@")
    country = str(target.get("country_code") or "+86").strip()
    phone = str(target.get("phone") or "").strip()
    if phone:
        return country + phone
    return str(target.get("first_name") or "").strip()


def _row_to_task(row: sqlite3.Row) -> Dict[str, Any]:
    data = _row_public(row)
    for key in ("targets", "payload"):
        raw = data.get(key)
        if isinstance(raw, str):
            try:
                data[key] = json.loads(raw)
            except (TypeError, ValueError):
                data[key] = [] if key == "targets" else {}
    return data


def _persist_task_row(
    *,
    account_id: str,
    task_type: str,
    targets: List[Dict[str, Any]],
    payload: Dict[str, Any],
    status: str = "queued",
    client_request_id: str = "",
) -> Dict[str, Any]:
    now = _now_iso()
    task_id = uuid.uuid4().hex
    with _DB_LOCK, _connect() as conn:
        conn.execute(
            "insert into whatsapp_tasks(id, account_id, task_type, targets, payload, status, "
            "processed, success, failed, error_message, client_request_id, created_at, updated_at) "
            "values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                account_id,
                task_type,
                _json_text(targets),
                _json_text(payload),
                status,
                0,
                0,
                0,
                "",
                str(client_request_id or "")[:200],
                now,
                now,
            ),
        )
    return _task_row_by_id(task_id) or {}


def _task_row_by_id(task_id: str) -> Optional[Dict[str, Any]]:
    if not task_id:
        return None
    with _connect() as conn:
        row = conn.execute("select * from whatsapp_tasks where id=? limit 1", (task_id,)).fetchone()
    return _row_to_task(row) if row else None


def _existing_task_by_client_request_id(account_id: str, client_request_id: str) -> Optional[Dict[str, Any]]:
    key = str(client_request_id or "").strip()
    if not key:
        return None
    with _connect() as conn:
        row = conn.execute(
            "select * from whatsapp_tasks where account_id=? and client_request_id=? limit 1",
            (account_id, key),
        ).fetchone()
    return _row_to_task(row) if row else None


def _update_task_payload(task_id: str, patch: Dict[str, Any]) -> None:
    task = _task_row_by_id(task_id)
    if not task:
        return
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    payload.update(patch or {})
    with _DB_LOCK, _connect() as conn:
        conn.execute(
            "update whatsapp_tasks set payload=?, updated_at=? where id=?",
            (_json_text(payload), _now_iso(), task_id),
        )


def _update_task_progress(task_id: str, processed: int, success: int, failed: int, error: str = "") -> None:
    with _DB_LOCK, _connect() as conn:
        conn.execute(
            "update whatsapp_tasks set processed=?, success=?, failed=?, error_message=?, updated_at=? where id=?",
            (int(processed), int(success), int(failed), str(error or "")[:2000], _now_iso(), task_id),
        )


def _finish_task(task_id: str, status: str, processed: int, success: int, failed: int, error: str = "") -> None:
    _update_task_progress(task_id, processed, success, failed, error)
    with _DB_LOCK, _connect() as conn:
        conn.execute("update whatsapp_tasks set status=?, updated_at=? where id=?", (status, _now_iso(), task_id))


def _friend_add_count_today(account_id: str) -> int:
    """今天已经"尝试过"的加好友条数（一条任务 = 一个目标）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    with _connect() as conn:
        row = conn.execute(
            "select count(*) as n from whatsapp_tasks where account_id=? and task_type='add_friend' "
            "and processed > 0 and substr(updated_at, 1, 10) = ?",
            (account_id, today),
        ).fetchone()
    return int(row["n"] if row else 0)


def _normalize_friend_add_control(row: Optional[sqlite3.Row], account_id: str) -> Dict[str, Any]:
    data = dict(row) if row else {}
    try:
        interval = int(data.get("interval_seconds") or DEFAULT_FRIEND_ADD_INTERVAL_SECONDS)
    except (TypeError, ValueError):
        interval = DEFAULT_FRIEND_ADD_INTERVAL_SECONDS
    raw_limit = data.get("daily_limit")
    if raw_limit is None or raw_limit == "":
        raw_limit = DEFAULT_FRIEND_ADD_DAILY_LIMIT
    try:
        # 显式 0 = 不限制：不能因为 `0 or 默认值` 被悄悄改回默认 30
        daily_limit = int(raw_limit)
    except (TypeError, ValueError):
        daily_limit = DEFAULT_FRIEND_ADD_DAILY_LIMIT
    key = str(account_id or "").strip() or DEFAULT_ACCOUNT_ID
    scheduler = _FRIEND_ADD_SCHEDULERS.get(key)
    return {
        "account_id": key,
        "enabled": bool(int(data.get("enabled") or 0)),
        "interval_seconds": max(1, min(interval, 86400)),
        "daily_limit": max(0, min(daily_limit, 1000)),
        "running": bool(scheduler is not None and not scheduler.done()),
        "added_today": _friend_add_count_today(key),
        "last_started_at": str(data.get("last_started_at") or ""),
        "last_stopped_at": str(data.get("last_stopped_at") or ""),
        "updated_at": str(data.get("updated_at") or ""),
    }


def get_friend_add_control(account_id: str = "") -> Dict[str, Any]:
    key = str(account_id or "").strip() or DEFAULT_ACCOUNT_ID
    with _connect() as conn:
        row = conn.execute(
            "select * from whatsapp_friend_add_control where account_id=? limit 1", (key,)
        ).fetchone()
    return _normalize_friend_add_control(row, key)


def save_friend_add_control(
    account_id: str = "",
    *,
    interval_seconds: Optional[int] = None,
    daily_limit: Optional[int] = None,
) -> Dict[str, Any]:
    key = str(account_id or "").strip() or DEFAULT_ACCOUNT_ID
    current = get_friend_add_control(key)
    interval = current["interval_seconds"] if interval_seconds is None else max(1, min(int(interval_seconds), 86400))
    limit = current["daily_limit"] if daily_limit is None else max(0, min(int(daily_limit), 1000))
    now = _now_iso()
    with _DB_LOCK, _connect() as conn:
        conn.execute(
            "insert into whatsapp_friend_add_control(account_id, enabled, interval_seconds, daily_limit, running, updated_at) "
            "values(?,?,?,?,?,?) on conflict(account_id) do update set interval_seconds=excluded.interval_seconds, "
            "daily_limit=excluded.daily_limit, updated_at=excluded.updated_at",
            (key, 1 if current["enabled"] else 0, int(interval), int(limit), 0, now),
        )
    _notify_friend_add_scheduler(key)
    return get_friend_add_control(key)


def _set_friend_add_control_enabled(account_id: str, enabled: bool) -> None:
    key = str(account_id or "").strip() or DEFAULT_ACCOUNT_ID
    now = _now_iso()
    with _DB_LOCK, _connect() as conn:
        conn.execute(
            "insert into whatsapp_friend_add_control(account_id, enabled, interval_seconds, daily_limit, running, "
            "last_started_at, last_stopped_at, updated_at) values(?,?,?,?,?,?,?,?) "
            "on conflict(account_id) do update set enabled=excluded.enabled, "
            "last_started_at=case when excluded.enabled=1 then excluded.last_started_at else whatsapp_friend_add_control.last_started_at end, "
            "last_stopped_at=case when excluded.enabled=0 then excluded.last_stopped_at else whatsapp_friend_add_control.last_stopped_at end, "
            "updated_at=excluded.updated_at",
            (
                key,
                1 if enabled else 0,
                DEFAULT_FRIEND_ADD_INTERVAL_SECONDS,
                DEFAULT_FRIEND_ADD_DAILY_LIMIT,
                0,
                now if enabled else None,
                now if not enabled else None,
                now,
            ),
        )
        conn.execute(
            "update whatsapp_friend_add_control set running=? where account_id=?", (1 if enabled else 0, key)
        )


def _notify_friend_add_scheduler(account_id: str) -> None:
    event = _FRIEND_ADD_WAKE_EVENTS.get(str(account_id or "").strip() or DEFAULT_ACCOUNT_ID)
    if event is not None:
        event.set()


def _claim_next_queued_task(account_id: str, task_type: str = "add_friend") -> Optional[Dict[str, Any]]:
    key = str(account_id or "").strip() or DEFAULT_ACCOUNT_ID
    with _DB_LOCK, _connect() as conn:
        row = conn.execute(
            "select * from whatsapp_tasks where account_id=? and task_type=? and status='queued' "
            "order by created_at asc, id asc limit 1",
            (key, task_type),
        ).fetchone()
        if not row:
            return None
        task_id = str(row["id"])
        changed = conn.execute(
            "update whatsapp_tasks set status='running', updated_at=? where id=? and status='queued'",
            (_now_iso(), task_id),
        ).rowcount
        if not changed:
            return None
        fresh = conn.execute("select * from whatsapp_tasks where id=? limit 1", (task_id,)).fetchone()
    return _row_to_task(fresh) if fresh else None


def _enforce_friend_add_rate(account_id: str) -> None:
    control = get_friend_add_control(account_id)
    limit = int(control.get("daily_limit") or 0)
    if limit > 0 and _friend_add_count_today(account_id) >= limit:
        raise FriendAddDailyLimitReached("今日加好友额度已用完（上限 %d 条）" % limit)


def create_add_contact_task(
    targets: Iterable[Any],
    *,
    apply_message: str = "",
    remark: str = "",
    bulk_import: bool = False,
    queue_only: bool = True,
    client_request_id: str = "",
    interval_seconds: Optional[int] = None,
    daily_limit: Optional[int] = None,
) -> Dict[str, Any]:
    """把批量目标逐条入队（一条任务 = 一个目标），由调度器按间隔慢慢加。"""
    account_id = DEFAULT_ACCOUNT_ID
    normalized = normalize_friend_targets(targets)
    if not normalized:
        raise RuntimeError("没有可用的加好友目标（支持：姓名,电话 / 姓名,姓氏,电话 / 姓名,@用户名）")
    missing_names = _missing_name_targets(normalized)
    if missing_names:
        raise RuntimeError(
            "WhatsApp 添加联系人必须填姓名：%d 个目标只写了号码（%s）。请改成「姓名,电话」或「姓名,姓氏,电话」"
            % (len(missing_names), "、".join(missing_names[:3]))
        )
    if interval_seconds is not None or daily_limit is not None:
        save_friend_add_control(account_id, interval_seconds=interval_seconds, daily_limit=daily_limit)
    if not queue_only:
        if not _scan_windows():
            raise RuntimeError("未检测到 Windows 桌面版 WhatsApp，请先启动并登录")
    batch_id = str(client_request_id or uuid.uuid4().hex)[:120]
    if client_request_id:
        first_existing = _existing_task_by_client_request_id(account_id, batch_id + ":0")
        if first_existing:
            with _connect() as conn:
                rows = conn.execute(
                    "select * from whatsapp_tasks where account_id=? and task_type='add_friend' "
                    "and client_request_id like ? order by created_at asc",
                    (account_id, batch_id + ":%"),
                ).fetchall()
            queued = [_row_to_task(row) for row in rows]
            return {
                "id": batch_id,
                "task_type": "add_friend",
                "targets": [_target_label(item) for item in normalized],
                "tasks": queued,
                "status": "queued" if any(str(item.get("status")) in {"queued", "running"} for item in queued) else "success",
                "planned_total": len(queued),
                "queued_total": sum(1 for item in queued if str(item.get("status")) == "queued"),
                "deduped": True,
            }
    tasks: List[Dict[str, Any]] = []
    for index, target in enumerate(normalized):
        tasks.append(
            _persist_task_row(
                account_id=account_id,
                task_type="add_friend",
                targets=[target],
                payload={
                    "apply_message": str(apply_message or "").strip()[:1000],
                    "remark": str(remark or "").strip()[:200],
                    "bulk_import": bool(bulk_import),
                    "queue_only": True,
                    "batch_request_id": batch_id,
                    "label": _target_label(target),
                },
                status="queued",
                client_request_id=(batch_id + ":" + str(index)) if client_request_id else "",
            )
        )
    _record_operation(
        "friend_add_enqueue",
        batch_id,
        "queued",
        "批量加好友入队 %d 条" % len(tasks),
        {"batch_id": batch_id, "targets": [_target_label(item) for item in normalized]},
    )
    _notify_friend_add_scheduler(account_id)
    return {
        "id": batch_id,
        "task_type": "add_friend",
        "targets": [_target_label(item) for item in normalized],
        "tasks": tasks,
        "status": "queued",
        "planned_total": len(tasks),
        "queued_total": len(tasks),
    }


async def _process_add_contact_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """执行一条加好友任务（一条任务 = 一个目标；带重试与日额度保护）。"""
    task_id = str(task.get("id") or "")
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    pending = payload.get("pending_targets")
    resuming = isinstance(pending, list) and bool(pending)
    targets = [item for item in (list(pending) if resuming else list(task.get("targets") or [])) if isinstance(item, dict)]
    if not targets:
        _finish_task(task_id, "failed", 0, 0, 1, "任务里没有目标")
        return {"status": "failed", "error": "任务里没有目标"}
    base_processed = int(task.get("processed") or 0) if resuming else 0
    base_success = int(task.get("success") or 0) if resuming else 0
    base_failed = int(task.get("failed") or 0) if resuming else 0
    apply_message = str(payload.get("apply_message") or "").strip()
    processed = success = failed = consumed = 0
    last_error = ""
    deferred = ""
    cancelled = False
    for index, target in enumerate(targets):
        try:
            _enforce_friend_add_rate(DEFAULT_ACCOUNT_ID)
        except FriendAddDailyLimitReached as exc:
            deferred = str(exc)
            break
        processed += 1
        consumed = index + 1
        ok = False
        error_text = ""
        verify_note = ""
        for attempt in range(DEFAULT_FRIEND_ADD_RETRY_MAX + 1):
            try:
                task_result = await asyncio.to_thread(
                    add_contact,
                    first_name=str(target.get("first_name") or "").strip() or _target_label(target),
                    last_name=str(target.get("last_name") or "").strip(),
                    username=str(target.get("username") or ""),
                    phone=str(target.get("phone") or ""),
                    country_code=str(target.get("country_code") or "+86"),
                )
                verify = (task_result or {}).get("verify") if isinstance(task_result, dict) else {}
                if isinstance(verify, dict) and verify.get("checked") and not verify.get("found"):
                    verify_note = "已点保存，但搜索里没确认到联系人（可能还在同步）"
                if apply_message:
                    await asyncio.to_thread(send_message, _target_label(target), apply_message)
                ok = True
                break
            except FriendAddCancelled as exc:
                cancelled = True
                error_text = str(exc)
                break
            except FriendAddDailyLimitReached as exc:
                deferred = str(exc)
                error_text = deferred
                break
            except Exception as exc:  # noqa: BLE001
                error_text = str(exc)
                if attempt < DEFAULT_FRIEND_ADD_RETRY_MAX:
                    await asyncio.sleep(DEFAULT_FRIEND_ADD_RETRY_SLEEP)
        if deferred:
            processed -= 1
            consumed = index
            break
        if ok:
            success += 1
        else:
            failed += 1
            last_error = error_text
        _update_task_progress(
            task_id,
            base_processed + processed,
            base_success + success,
            base_failed + failed,
            last_error or (verify_note if ok else ""),
        )
    total_processed = base_processed + processed
    total_success = base_success + success
    total_failed = base_failed + failed
    if cancelled:
        _finish_task(task_id, "cancelled", total_processed, total_success, total_failed, last_error or "已手动停止")
        clear_friend_add_cancel(DEFAULT_ACCOUNT_ID)
        return {"cancelled": True, "processed": total_processed, "success": total_success, "failed": total_failed}
    if deferred:
        remaining = targets[consumed:]
        _update_task_payload(task_id, {"pending_targets": remaining, "deferred_reason": deferred})
        _finish_task(task_id, "queued", total_processed, total_success, total_failed, deferred)
        return {"deferred": True, "reason": deferred, "remaining": len(remaining)}
    _update_task_payload(task_id, {"pending_targets": [], "deferred_reason": ""})
    status = "success" if failed == 0 else ("partial_failed" if success else "failed")
    _finish_task(task_id, status, total_processed, total_success, total_failed, last_error)
    return {"deferred": False, "status": status, "processed": total_processed, "success": total_success, "failed": total_failed}


async def _run_friend_add_scheduler(account_id: str) -> None:
    key = str(account_id or "").strip() or DEFAULT_ACCOUNT_ID
    event = _FRIEND_ADD_WAKE_EVENTS.setdefault(key, asyncio.Event())
    try:
        while get_friend_add_control(key).get("enabled"):
            task = _claim_next_queued_task(key)
            if task:
                defer_seconds = 0.0
                try:
                    outcome = await _process_add_contact_task(task)
                    if isinstance(outcome, dict) and outcome.get("deferred"):
                        defer_seconds = _friend_add_quota_reset_seconds()
                except Exception as exc:  # noqa: BLE001
                    _finish_task(
                        str(task.get("id") or ""),
                        "failed",
                        int(task.get("processed") or 0),
                        int(task.get("success") or 0),
                        max(1, int(task.get("failed") or 0)),
                        str(exc),
                    )
                if not get_friend_add_control(key).get("enabled"):
                    break
                interval = float(get_friend_add_control(key).get("interval_seconds") or DEFAULT_FRIEND_ADD_INTERVAL_SECONDS)
                jitter = 0.85 + 0.3 * ((int(hashlib.sha1(str(task.get("id") or "").encode()).hexdigest()[:6], 16) % 100) / 100.0)
                wait_seconds = defer_seconds or interval * jitter
                event.clear()
                try:
                    await asyncio.wait_for(event.wait(), timeout=max(1.0, wait_seconds))
                except asyncio.TimeoutError:
                    pass
                continue
            event.clear()
            try:
                await asyncio.wait_for(event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
    except asyncio.CancelledError:
        raise
    finally:
        current = _FRIEND_ADD_SCHEDULERS.get(key)
        if current is asyncio.current_task():
            _FRIEND_ADD_SCHEDULERS.pop(key, None)
            _FRIEND_ADD_WAKE_EVENTS.pop(key, None)


async def start_friend_add_queue(account_id: str = "") -> Dict[str, Any]:
    key = str(account_id or "").strip() or DEFAULT_ACCOUNT_ID
    _set_friend_add_control_enabled(key, True)
    current = _FRIEND_ADD_SCHEDULERS.get(key)
    if current is None or current.done():
        _FRIEND_ADD_SCHEDULERS[key] = asyncio.create_task(
            _run_friend_add_scheduler(key), name="whatsapp-friend-add-" + key[-8:]
        )
    _notify_friend_add_scheduler(key)
    _append_log("friend_add_queue_started", account_id=key)
    return get_friend_add_control(key)


async def stop_friend_add_queue(account_id: str = "") -> Dict[str, Any]:
    key = str(account_id or "").strip() or DEFAULT_ACCOUNT_ID
    _set_friend_add_control_enabled(key, False)
    _notify_friend_add_scheduler(key)
    _append_log("friend_add_queue_stopped", account_id=key)
    return get_friend_add_control(key)


def list_friend_records(
    account_id: str = "",
    *,
    limit: int = 50,
    offset: int = 0,
    status: str = "",
    keyword: str = "",
) -> Dict[str, Any]:
    """逐条记录（一条任务 = 一个目标），给前端"加好友记录"表用。"""
    key = str(account_id or "").strip() or DEFAULT_ACCOUNT_ID
    reap_stale_friend_tasks(key)
    where = "where account_id=? and task_type='add_friend'"
    params: List[Any] = [key]
    if status:
        where += " and status=?"
        params.append(str(status))
    with _connect() as conn:
        rows = conn.execute(
            "select * from whatsapp_tasks " + where + " order by created_at desc, id desc",
            tuple(params),
        ).fetchall()
    records: List[Dict[str, Any]] = []
    needle = str(keyword or "").strip().casefold()
    for row in rows:
        task = _row_to_task(row)
        payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
        for target in task.get("targets") or []:
            if not isinstance(target, dict):
                continue
            label = str(payload.get("label") or _target_label(target))
            if needle and needle not in label.casefold() and needle not in str(payload.get("apply_message") or "").casefold():
                continue
            records.append(
                {
                    "id": str(task.get("id") or ""),
                    "task_id": str(task.get("id") or ""),
                    "account_id": str(task.get("account_id") or ""),
                    "target": label,
                    "keyword": label,
                    "first_name": str(target.get("first_name") or ""),
                    "last_name": str(target.get("last_name") or ""),
                    "phone": str(target.get("country_code") or "") + str(target.get("phone") or ""),
                    "username": str(target.get("username") or ""),
                    "apply_message": str(payload.get("apply_message") or ""),
                    "remark": str(payload.get("remark") or ""),
                    "status": str(task.get("status") or ""),
                    "error_message": str(task.get("error_message") or ""),
                    "processed": int(task.get("processed") or 0),
                    "success": int(task.get("success") or 0),
                    "failed": int(task.get("failed") or 0),
                    "created_at": str(task.get("created_at") or ""),
                    "updated_at": str(task.get("updated_at") or ""),
                }
            )
    total = len(records)
    start = max(0, int(offset))
    end = start + max(1, int(limit))
    # total 与客户端其它分页接口口径一致；count 保留给旧前端
    return {"items": records[start:end], "count": total, "total": total, "limit": int(limit), "offset": start}


class FriendAddCancelled(RuntimeError):
    """用户手动停止加好友时抛出，用于尽快中断正在执行的 UIA 动作。"""


_FRIEND_ADD_CANCEL_LOCK = threading.Lock()
_FRIEND_ADD_CANCEL_EVENTS: Dict[str, threading.Event] = {}


def _friend_cancel_event(account_id: str = "") -> threading.Event:
    key = str(account_id or "").strip() or DEFAULT_ACCOUNT_ID
    with _FRIEND_ADD_CANCEL_LOCK:
        event = _FRIEND_ADD_CANCEL_EVENTS.get(key)
        if event is None:
            event = threading.Event()
            _FRIEND_ADD_CANCEL_EVENTS[key] = event
        return event


def friend_add_cancelled(account_id: str = "") -> bool:
    return _friend_cancel_event(account_id).is_set()


def clear_friend_add_cancel(account_id: str = "") -> None:
    _friend_cancel_event(account_id).clear()


def request_friend_add_cancel(account_id: str = "") -> None:
    _friend_cancel_event(account_id).set()


STALE_RUNNING_FRIEND_SECONDS = 15 * 60
_LAST_REAP_AT = 0.0


def reap_stale_friend_tasks(account_id: str = "", *, max_age_seconds: int = STALE_RUNNING_FRIEND_SECONDS) -> int:
    """把卡在「执行中」但早就没有实际动作的任务标成失败。

    客户机实测（2026-09-21）：加好友点不到「保存联系人」时会一直卡在执行中，
    界面上既不能重试也不能删除。这里按「没有活动动作 + updated_at 太旧」判定为僵尸记录。
    """
    global _LAST_REAP_AT
    key = str(account_id or "").strip() or DEFAULT_ACCOUNT_ID
    now = time.time()
    if now - _LAST_REAP_AT < 60:
        return 0
    _LAST_REAP_AT = now
    with _ACTIVE_LOCK:
        active = bool(_ACTIVE)
    if active:
        return 0
    cutoff = (datetime.now().astimezone() - timedelta(seconds=max(60, int(max_age_seconds)))).isoformat()
    with _DB_LOCK, _connect() as conn:
        cur = conn.execute(
            "update whatsapp_tasks set status='failed', error_message=?, updated_at=? "
            "where account_id=? and task_type='add_friend' and status='running' and updated_at < ?",
            (
                "执行中断",
                _now_iso(),
                key,
                cutoff,
            ),
        )
        return int(cur.rowcount or 0)


def cancel_friend_record(task_id: str, account_id: str = "") -> Dict[str, Any]:
    """停止一条排队中/执行中的加好友记录。"""
    key = str(account_id or "").strip() or DEFAULT_ACCOUNT_ID
    tid = str(task_id or "").strip()
    if not tid:
        raise RuntimeError("缺少记录 ID")
    task = _task_row_by_id(tid)
    if not task or str(task.get("account_id") or "") != key or str(task.get("task_type") or "") != "add_friend":
        raise RuntimeError("找不到这条加好友记录")
    status = str(task.get("status") or "")
    if status not in {"queued", "pending", "running"}:
        raise RuntimeError("这条记录已经结束（%s），不需要停止" % (status or "未知"))
    if status == "running":
        request_friend_add_cancel(key)
        _finish_task(tid, "cancelled", int(task.get("processed") or 0),
                     int(task.get("success") or 0), int(task.get("failed") or 0), "已手动停止")
        _append_log("friend_add_cancel", task_id=tid)
        return {"ok": True, "status": "cancelled", "message": "已请求停止，正在执行的这条会在几秒内中断"}
    _finish_task(tid, "cancelled", 0, 0, 0, "已手动停止")
    return {"ok": True, "status": "cancelled", "message": "已从队列移除"}


def retry_friend_record(task_id: str, account_id: str = "") -> Dict[str, Any]:
    """手动重试一条加好友记录：清掉失败计数与错误，重新排队（保留原目标与申请语）。"""
    key = str(account_id or "").strip() or DEFAULT_ACCOUNT_ID
    tid = str(task_id or "").strip()
    if not tid:
        raise RuntimeError("缺少记录 ID")
    task = _task_row_by_id(tid)
    if not task or str(task.get("account_id") or "") != key or str(task.get("task_type") or "") != "add_friend":
        raise RuntimeError("找不到这条加好友记录")
    if str(task.get("status") or "") == "running":
        raise RuntimeError("这条记录正在执行中，先点「停止」再重试")
    with _DB_LOCK, _connect() as conn:
        conn.execute(
            "update whatsapp_tasks set status='queued', processed=0, success=0, failed=0, "
            "error_message='', updated_at=? where id=?",
            (_now_iso(), tid),
        )
    # 不碰 created_at：调度器按 created_at 升序取任务，重试的记录会排到最前
    _update_task_payload(tid, {"pending_targets": [], "deferred_reason": ""})
    _record_operation("friend_add_retry", tid, "queued", "手动重试加好友记录", {"task_id": tid})
    _notify_friend_add_scheduler(key)
    fresh = _task_row_by_id(tid) or {}
    return {
        "ok": True,
        "task": fresh,
        "status": str(fresh.get("status") or "queued"),
        "message": "已重新排队，队列会按设置间隔处理",
    }


def delete_friend_record(task_id: str, account_id: str = "") -> Dict[str, Any]:
    """删除一条加好友记录（执行中的必须先停止）。"""
    key = str(account_id or "").strip() or DEFAULT_ACCOUNT_ID
    tid = str(task_id or "").strip()
    if not tid:
        raise RuntimeError("缺少记录 ID")
    task = _task_row_by_id(tid)
    if not task or str(task.get("account_id") or "") != key or str(task.get("task_type") or "") != "add_friend":
        raise RuntimeError("找不到这条加好友记录")
    if str(task.get("status") or "") == "running":
        raise RuntimeError("这条记录正在执行中，先点「停止」再删除")
    with _DB_LOCK, _connect() as conn:
        conn.execute("delete from whatsapp_tasks where id=?", (tid,))
    _record_operation("friend_add_delete", tid, "success", "删除加好友记录", {"task_id": tid})
    return {"ok": True, "deleted": tid, "message": "记录已删除"}


def friend_add_queue_summary(account_id: str = "") -> Dict[str, Any]:
    """队列概览：排队/执行中/累计与今日成功失败，给 UI 顶部状态用。"""
    key = str(account_id or "").strip() or DEFAULT_ACCOUNT_ID
    with _connect() as conn:
        rows = conn.execute(
            "select status, count(*) as n from whatsapp_tasks where account_id=? and task_type='add_friend' group by status",
            (key,),
        ).fetchall()
    by_status = {str(row["status"]): int(row["n"] or 0) for row in rows}
    today = datetime.now().strftime("%Y-%m-%d")
    with _connect() as conn:
        today_row = conn.execute(
            "select sum(success) as s, sum(failed) as f from whatsapp_tasks where account_id=? "
            "and task_type='add_friend' and substr(updated_at, 1, 10) = ?",
            (key, today),
        ).fetchone()
    control = get_friend_add_control(key)
    return {
        "queued": by_status.get("queued", 0),
        "running": by_status.get("running", 0),
        "success": by_status.get("success", 0) + by_status.get("partial_failed", 0),
        "failed": by_status.get("failed", 0),
        "today_success": int((today_row["s"] if today_row else 0) or 0),
        "today_failed": int((today_row["f"] if today_row else 0) or 0),
        "added_today": control.get("added_today", 0),
        "daily_limit": control.get("daily_limit", 0),
        "interval_seconds": control.get("interval_seconds"),
        "enabled": control.get("enabled"),
        "running_now": control.get("running"),
    }


# ── 接管：常驻轮次 + 诊断（对齐微信 auto-reply worker / diagnostics）──────────

_AUTO_REPLY_STATE: Dict[str, Any] = {"auth_context": {}, "interval_seconds": 0}
_AUTO_REPLY_LOOPS: Dict[str, Any] = {}


def auto_reply_state() -> Dict[str, Any]:
    task = _AUTO_REPLY_LOOPS.get(DEFAULT_ACCOUNT_ID)
    running = bool(task is not None and not task.done())
    return {
        "running": running,
        "interval_seconds": int(_AUTO_REPLY_STATE.get("interval_seconds") or 0),
        "last_run": (get_config().get("last_run") or {}),
    }


async def _auto_reply_loop(interval_seconds: int) -> None:
    try:
        while True:
            try:
                await run_once(auth_context=dict(_AUTO_REPLY_STATE.get("auth_context") or {}))
            except Exception as exc:  # noqa: BLE001
                _append_log("auto_reply_loop_round_failed", error=str(exc)[:500])
            await asyncio.sleep(max(5, int(interval_seconds)))
    except asyncio.CancelledError:
        raise
    finally:
        current = _AUTO_REPLY_LOOPS.get(DEFAULT_ACCOUNT_ID)
        if current is asyncio.current_task():
            _AUTO_REPLY_LOOPS.pop(DEFAULT_ACCOUNT_ID, None)


async def start_auto_reply_loop(
    *,
    interval_seconds: Optional[int] = None,
    auth_context: Optional[Dict[str, Any]] = None,
    config_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """常驻接管：按间隔自动执行下一轮（对齐微信的自动回复 worker）。"""
    cfg = get_config()
    interval = int(interval_seconds or cfg.get("interval_seconds") or 15)
    if config_override:
        save_config(
            interval_seconds=interval,
            max_unread_per_round=config_override.get("max_unread_per_round"),
            reply_instruction=config_override.get("reply_instruction"),
        )
    else:
        save_config(interval_seconds=interval)
    if auth_context:
        _AUTO_REPLY_STATE["auth_context"] = dict(auth_context)
    _AUTO_REPLY_STATE["interval_seconds"] = interval
    current = _AUTO_REPLY_LOOPS.get(DEFAULT_ACCOUNT_ID)
    if current is None or current.done():
        _AUTO_REPLY_LOOPS[DEFAULT_ACCOUNT_ID] = asyncio.create_task(
            _auto_reply_loop(interval), name="whatsapp-auto-reply-loop"
        )
    _append_log("auto_reply_loop_started", interval_seconds=interval)
    return auto_reply_state()


def stop_auto_reply_loop() -> Dict[str, Any]:
    task = _AUTO_REPLY_LOOPS.pop(DEFAULT_ACCOUNT_ID, None)
    if task is not None and not task.done():
        task.cancel()
    request_stop()
    _append_log("auto_reply_loop_stopped")
    return auto_reply_state()


def auto_reply_diagnostics(limit: int = 20) -> Dict[str, Any]:
    """最近一轮接管的结构化结果 + 日志尾部，给 UI 的诊断面板用。"""
    cfg = get_config()
    last_run = cfg.get("last_run") if isinstance(cfg.get("last_run"), dict) else {}
    events: List[Dict[str, Any]] = []
    try:
        if LOG_PATH.exists():
            with LOG_PATH.open("r", encoding="utf-8") as handle:
                tail = handle.readlines()[-max(1, min(int(limit or 20), 200)):]
            for line in tail:
                try:
                    events.append(json.loads(line))
                except (TypeError, ValueError):
                    continue
    except OSError:
        pass
    return {"ok": True, "state": auto_reply_state(), "last_run": last_run, "events": events}
