from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import importlib.util
import json
import mimetypes
import os
import random
import re
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

from ..core.config import settings


ROOT_DIR = Path(__file__).resolve().parents[3]
STATE_DIR = ROOT_DIR / "openclaw" / "openclaw-weixin"
ACCOUNTS_DIR = STATE_DIR / "accounts"
DB_PATH = ROOT_DIR / "data" / "native_wechat_engine.db"
LOG_DIR = ROOT_DIR / "logs"
NATIVE_WECHAT_DIAGNOSTIC_LOG = LOG_DIR / "native_wechat_diagnostics.jsonl"
NATIVE_WECHAT_UPLOAD_DIR = ROOT_DIR / "temp_assets" / "native_wechat"
NATIVE_WECHAT_DOWNLOAD_DIR = ROOT_DIR / "assets" / "native_wechat"
NATIVE_WECHAT_MAX_UPLOAD_BYTES = 500 * 1024 * 1024

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
CHANNEL_VERSION = "2.1.6"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (1 << 8) | 6
DEFAULT_BOT_TYPE = "3"
LOGIN_TTL_SECONDS = 5 * 60
LOCAL_ACCOUNT_PREFIX = "pc-wechat-"
LOCAL_DEFAULT_ACCOUNT_ID = f"{LOCAL_ACCOUNT_PREFIX}default"
WXAUTO4_MAX_FREE_VERSION = (4, 1, 8, 107)
WXAUTO4_MAX_PLUS_VERSION = (4, 1, 9, 35)
WECHAT_PROCESS_NAMES = {"weixin.exe", "wechat.exe"}
WECHAT_WINDOW_TITLES = {"微信", "Weixin", "WeChat"}
WECHAT_WINDOW_CLASSES = {
    "WeChatMainWndForPC",
    "mmui::MainWindow",
}


DEFAULT_STRATEGY: Dict[str, Any] = {
    "send_sleep_min": 12.0,
    "send_sleep_max": 28.0,
    "local_min_send_gap": 10.0,
    "batch_size": 3,
    "batch_sleep": 120,
    "max_targets_per_task": 30,
    "daily_send_limit": 80,
    "friend_add_sleep_min": 60.0,
    "friend_add_sleep_max": 180.0,
    "friend_add_min_gap": 60.0,
    "friend_add_batch_size": 2,
    "friend_add_batch_sleep": 600,
    "daily_friend_add_limit": 20,
    "moments_like_sleep_min": 20.0,
    "moments_like_sleep_max": 60.0,
    "moments_scroll_sleep_min": 3.0,
    "moments_scroll_sleep_max": 8.0,
    "daily_moments_like_limit": 50,
    "daily_moments_comment_limit": 30,
    "daily_moments_publish_limit": 20,
    "moments_publish_min_gap": 180.0,
    "moments_publish_sleep_min": 20.0,
    "moments_publish_sleep_max": 60.0,
    "ui_action_sleep_min": 0.7,
    "ui_action_sleep_max": 1.8,
    "ui_input_sleep_min": 0.35,
    "ui_input_sleep_max": 1.2,
    "retry_max": 1,
    "retry_sleep": 8,
    "session_poll_interval": 5,
    "poll_timeout_ms": 35000,
    "api_timeout_ms": 15000,
    "consecutive_failure_limit": 3,
    "backoff_seconds": 30,
    "one_active_task_per_account": True,
    "auto_reply_interval_seconds": 1800,
    "auto_reply_session_sleep_min": 0.0,
    "auto_reply_session_sleep_max": 0.0,
    "auto_reply_char_sleep_min": 0.08,
    "auto_reply_char_sleep_max": 0.22,
    "auto_reply_punctuation_sleep_min": 0.35,
    "auto_reply_punctuation_sleep_max": 0.9,
    "auto_reply_max_text_chars": 600,
}


_ACTIVE_LOGINS: Dict[str, Dict[str, Any]] = {}
_LOCAL_WINDOWS_CACHE: Dict[str, Any] = {"items": [], "at": 0.0}
_TASK_WORKERS: Dict[str, asyncio.Task[Any]] = {}
_TASK_AUTH_CONTEXT: Dict[str, Dict[str, Any]] = {}
_AUTO_REPLY_WORKERS: Dict[str, asyncio.Task[Any]] = {}
_AUTO_REPLY_AUTH_CONTEXT: Dict[str, Dict[str, Any]] = {}
_AUTO_REPLY_ACTIVE_RUNS: set[str] = set()
_AUTO_REPLY_ACTIVE_RUNS_LOCK = threading.Lock()
_AUTO_REPLY_STOP_REQUESTS: set[str] = set()
_AUTO_REPLY_STARTUP_RECONCILED = False
_AUTO_REPLY_STARTUP_RECONCILE_LOCK = threading.Lock()
_WECHAT_INTELLIGENCE_CIRCUIT_UNTIL = 0.0
_LOCAL_WECHAT_UI_LOCK = threading.RLock()
_LOCAL_WECHAT_THREAD_STATE = threading.local()
_LOCAL_WECHAT_AUTOMATION_OWNER_THREAD_ID = 0
_LOCAL_WECHAT_DRIVER_RECOVERY: Dict[str, Dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_json_loads(raw: str | bytes | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _normalize_account_id(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    return value.replace("@", "-").replace(".", "-").replace("/", "-").replace("\\", "-")


def _read_json_file(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json_file(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _json_safe_value(value: Any, *, max_text: int = 1200) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe_value(v, max_text=max_text) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(v, max_text=max_text) for v in list(value)]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and len(value) > max_text:
            return value[:max_text] + "...<truncated>"
        return value
    return str(value)[:max_text]


def _runtime_build_info() -> Dict[str, Any]:
    data = _read_json_file(ROOT_DIR / "CLIENT_CODE_VERSION.json", {})
    if not isinstance(data, dict):
        data = {}
    out: Dict[str, Any] = {
        "version": data.get("version") or "",
        "build": data.get("build") or "",
        "updated_at": data.get("updated_at") or data.get("created_at") or "",
    }
    try:
        out["root"] = str(ROOT_DIR)
    except Exception:
        pass
    return out


def _raw_wechat_window_candidates(*, max_items: int = 120) -> Dict[str, Any]:
    result: Dict[str, Any] = {"ok": False, "items": [], "count": 0, "error": ""}
    try:
        import win32gui  # type: ignore
        import win32process  # type: ignore
    except Exception as exc:
        result["error"] = f"missing win32 window dependency: {exc}"
        return result

    try:
        process_items = _wechat_process_candidates()
        process_pids = {int(item.get("pid") or 0) for item in process_items}
    except Exception:
        process_items = []
        process_pids = set()

    items: List[Dict[str, Any]] = []

    def _enum(hwnd: int, _extra: Any) -> None:
        if len(items) >= max_items:
            return
        try:
            title = win32gui.GetWindowText(hwnd) or ""
            class_name = win32gui.GetClassName(hwnd) or ""
            _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
            pid = int(pid or 0)
            meta = _process_meta(pid)
            class_l = class_name.lower()
            title_l = title.lower()
            process_ok = pid in process_pids or _looks_like_wechat_process(meta)
            title_hint = any(token in title_l for token in ("微信", "wechat", "weixin", "朋友圈"))
            class_hint = any(token in class_l for token in ("wechat", "weixin", "mmui", "qwindow", "qwidget"))
            if not (process_ok or title_hint or class_hint):
                return
            try:
                rect = tuple(int(x) for x in win32gui.GetWindowRect(hwnd))
            except Exception:
                rect = (0, 0, 0, 0)
            width = max(0, int(rect[2]) - int(rect[0]))
            height = max(0, int(rect[3]) - int(rect[1]))
            items.append(
                {
                    "hwnd": int(hwnd),
                    "pid": pid,
                    "title": title,
                    "class_name": class_name,
                    "is_visible": bool(win32gui.IsWindowVisible(hwnd)),
                    "is_iconic": bool(win32gui.IsIconic(hwnd)),
                    "rect": list(rect),
                    "width": width,
                    "height": height,
                    "area": width * height,
                    "process_name": meta.get("name") or "",
                    "process_path": meta.get("exe") or "",
                    "version": meta.get("version") or "",
                    "looks_like_wechat_process": bool(process_ok),
                    "title_hint": bool(title_hint),
                    "class_hint": bool(class_hint),
                }
            )
        except Exception:
            return

    try:
        win32gui.EnumWindows(_enum, None)
        result.update({"ok": True, "items": items, "count": len(items)})
    except Exception as exc:
        result["error"] = str(exc)
    return result


def create_native_wechat_diagnostic(
    operation: str,
    *,
    error: str = "",
    account_id: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    code = "NWX-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3).upper()
    entry: Dict[str, Any] = {
        "code": code,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "operation": str(operation or "native_wechat"),
        "account_id": str(account_id or ""),
        "error": str(error or ""),
        "runtime": _runtime_build_info(),
        "process": {
            "pid": os.getpid(),
            "integrity": _process_integrity(os.getpid()),
        },
        "dependencies": {},
        "wechat_processes": [],
        "recognized_windows": [],
        "raw_windows": {},
        "ensure_without_launch": {},
        "extra": _json_safe_value(extra or {}),
    }
    try:
        for name in (
            "win32gui",
            "win32process",
            "win32api",
            "win32con",
            "psutil",
            "uiautomation",
            "pywinauto",
            "pyperclip",
            "wxauto4",
        ):
            entry["dependencies"][name] = _module_available(name)
    except Exception as exc:
        entry["dependencies_error"] = str(exc)
    try:
        entry["wechat_processes"] = _wechat_process_brief()
    except Exception as exc:
        entry["wechat_processes_error"] = str(exc)
    try:
        entry["recognized_windows"] = _scan_local_wechat_windows(max_age_seconds=0)
        entry["scan_cache_error"] = str(_LOCAL_WINDOWS_CACHE.get("error") or "")
    except Exception as exc:
        entry["recognized_windows_error"] = str(exc)
    try:
        entry["raw_windows"] = _raw_wechat_window_candidates()
    except Exception as exc:
        entry["raw_windows_error"] = str(exc)
    try:
        entry["ensure_without_launch"] = _ensure_local_wechat_window_visible(wait_seconds=1.0, allow_launch=False)
    except Exception as exc:
        entry["ensure_without_launch_error"] = str(exc)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with NATIVE_WECHAT_DIAGNOSTIC_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_json_safe_value(entry), ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception as exc:
        entry["write_error"] = str(exc)

    windows = entry.get("recognized_windows") or []
    raw_windows = (entry.get("raw_windows") or {}).get("items") or []
    processes = entry.get("wechat_processes") or []
    return {
        "code": code,
        "operation": entry["operation"],
        "created_at": entry["created_at"],
        "log_path": str(NATIVE_WECHAT_DIAGNOSTIC_LOG),
        "error": entry["error"],
        "account_id": entry["account_id"],
        "window_count": len(windows) if isinstance(windows, list) else 0,
        "raw_window_count": len(raw_windows) if isinstance(raw_windows, list) else 0,
        "wechat_process_count": len(processes) if isinstance(processes, list) else 0,
        "backend_integrity": (entry.get("process") or {}).get("integrity") or {},
        "action": (entry.get("ensure_without_launch") or {}).get("action") or "",
    }


def _account_path(account_id: str) -> Path:
    return ACCOUNTS_DIR / f"{account_id}.json"


def _sync_path(account_id: str) -> Path:
    return ACCOUNTS_DIR / f"{account_id}.sync.json"


def _context_path(account_id: str) -> Path:
    return ACCOUNTS_DIR / f"{account_id}.context-tokens.json"


def _load_account(account_id: str) -> Dict[str, Any]:
    return _read_json_file(_account_path(account_id), {})


def _save_account(account_id: str, data: Dict[str, Any]) -> None:
    existing = _load_account(account_id)
    merged = {**existing, **data, "savedAt": _now_iso()}
    _write_json_file(_account_path(account_id), merged)
    ids = _read_json_file(STATE_DIR / "accounts.json", [])
    if not isinstance(ids, list):
        ids = []
    if account_id not in ids:
        ids.append(account_id)
        _write_json_file(STATE_DIR / "accounts.json", ids)


def _load_context_tokens(account_id: str) -> Dict[str, str]:
    data = _read_json_file(_context_path(account_id), {})
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _save_context_token(account_id: str, peer_id: str, token: str) -> None:
    if not peer_id or not token:
        return
    data = _load_context_tokens(account_id)
    data[str(peer_id)] = str(token)
    _write_json_file(_context_path(account_id), data)


def _load_sync_buf(account_id: str) -> str:
    data = _read_json_file(_sync_path(account_id), {})
    if isinstance(data, dict):
        return str(data.get("get_updates_buf") or data.get("sync_buf") or "")
    if isinstance(data, str):
        return data
    return ""


def _save_sync_buf(account_id: str, buf: str) -> None:
    _write_json_file(_sync_path(account_id), {"get_updates_buf": buf, "updated_at": _now_iso()})


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    global _AUTO_REPLY_STARTUP_RECONCILED
    with _connect() as conn:
        conn.executescript(
            """
            create table if not exists wechat_peers (
                id text primary key,
                account_id text not null,
                peer_id text not null,
                display_name text,
                chat_type text not null default 'direct',
                context_token text,
                last_inbound_at text,
                last_outbound_at text,
                raw_json text,
                created_at text not null,
                updated_at text not null,
                unique(account_id, peer_id)
            );
            create index if not exists idx_wechat_peers_account_updated
            on wechat_peers(account_id, updated_at desc);

            create table if not exists wechat_contacts (
                id text primary key,
                account_id text not null,
                contact_key text not null,
                display_name text,
                remark text,
                wx_no text,
                source text not null default 'local',
                raw_json text,
                created_at text not null,
                updated_at text not null,
                unique(account_id, contact_key)
            );
            create index if not exists idx_wechat_contacts_account_updated
            on wechat_contacts(account_id, updated_at desc);
            create index if not exists idx_wechat_contacts_account_name
            on wechat_contacts(account_id, display_name);

            create table if not exists wechat_groups (
                id text primary key,
                account_id text not null,
                group_key text not null,
                display_name text,
                member_count integer,
                remark text,
                source text not null default 'local',
                raw_json text,
                created_at text not null,
                updated_at text not null,
                unique(account_id, group_key)
            );
            create index if not exists idx_wechat_groups_account_updated
            on wechat_groups(account_id, updated_at desc);

            create table if not exists wechat_group_members (
                id text primary key,
                account_id text not null,
                group_key text not null,
                member_key text not null,
                display_name text,
                raw_json text,
                created_at text not null,
                updated_at text not null,
                unique(account_id, group_key, member_key)
            );
            create index if not exists idx_wechat_group_members_group
            on wechat_group_members(account_id, group_key, updated_at desc);

            create table if not exists wechat_friend_requests (
                id text primary key,
                account_id text not null,
                keyword text not null,
                apply_message text,
                remark text,
                tags text,
                permission text,
                status text not null,
                error_message text,
                raw_json text,
                created_at text not null,
                updated_at text not null
            );
            create index if not exists idx_wechat_friend_requests_account_time
            on wechat_friend_requests(account_id, created_at desc);

            create table if not exists wechat_messages (
                id text primary key,
                account_id text not null,
                peer_id text not null,
                direction text not null,
                msg_type text not null default 'text',
                content text,
                provider_message_id text,
                client_id text,
                status text not null,
                error_message text,
                raw_json text,
                created_at text not null
            );
            create index if not exists idx_wechat_messages_account_peer_time
            on wechat_messages(account_id, peer_id, created_at desc);

            create table if not exists wechat_session_state (
                id text primary key,
                account_id text not null,
                peer_id text not null,
                display_name text,
                chat_type text not null default 'unknown',
                last_content text,
                session_time text,
                unread_count integer not null default 0,
                is_new integer not null default 0,
                is_muted integer not null default 0,
                raw_json text,
                first_seen_at text not null,
                updated_at text not null,
                unique(account_id, peer_id)
            );
            create index if not exists idx_wechat_session_state_account_updated
            on wechat_session_state(account_id, updated_at desc);

            create table if not exists wechat_tasks (
                id text primary key,
                account_id text not null,
                task_type text not null,
                target_type text not null,
                targets text not null,
                payload text not null,
                strategy text not null,
                status text not null,
                planned_total integer not null default 0,
                processed integer not null default 0,
                success integer not null default 0,
                failed integer not null default 0,
                error_message text,
                created_at text not null,
                updated_at text not null
            );
            create index if not exists idx_wechat_tasks_account_time
            on wechat_tasks(account_id, created_at desc);

            create table if not exists wechat_auto_reply_config (
                account_id text primary key,
                enabled integer not null default 0,
                interval_seconds integer not null default 1800,
                group_invite_enabled integer not null default 0,
                user_id integer,
                memory_doc_ids text not null default '[]',
                group_invite_memory_doc_id text not null default '',
                group_invite_keywords text not null default '',
                group_invite_contacts text not null default '[]',
                group_invite_primary_contact text not null default '',
                group_invite_primary_contact_name text not null default '',
                group_invite_welcome_message text not null default '',
                running integer not null default 0,
                last_started_at text,
                last_checked_at text,
                last_finished_at text,
                last_error text,
                last_result text,
                updated_at text not null
            );

            create table if not exists wechat_auto_reply_history (
                id text primary key,
                account_id text not null,
                peer_id text not null,
                inbound_message_id text not null,
                inbound_content text,
                reply_content text,
                category text,
                status text not null,
                error_message text,
                created_at text not null,
                updated_at text not null,
                unique(account_id, peer_id, inbound_message_id)
            );
            create index if not exists idx_wechat_auto_reply_history_account_time
            on wechat_auto_reply_history(account_id, created_at desc);

            create table if not exists wechat_intelligence_outbox (
                id text primary key,
                dedup_key text not null unique,
                payload text not null,
                attempts integer not null default 0,
                last_error text,
                created_at text not null,
                updated_at text not null
            );
            create index if not exists idx_wechat_intelligence_outbox_time
            on wechat_intelligence_outbox(created_at asc);

            create table if not exists wechat_moments_comments (
                id text primary key,
                account_id text not null,
                target text not null,
                post_key text not null,
                reply text not null,
                post_text text,
                media_summary text,
                status text not null,
                error_message text,
                raw_json text,
                created_at text not null,
                updated_at text not null,
                unique(account_id, target, post_key)
            );
            create index if not exists idx_wechat_moments_comments_account_time
            on wechat_moments_comments(account_id, created_at desc);
            """
        )
        config_columns = {str(row[1]) for row in conn.execute("pragma table_info(wechat_auto_reply_config)").fetchall()}
        added_config_columns = []
        for column_name, column_sql in (
            ("group_invite_enabled", "integer not null default 0"),
            ("memory_doc_ids", "text not null default '[]'"),
            ("group_invite_memory_doc_id", "text not null default ''"),
            ("group_invite_keywords", "text not null default ''"),
            ("group_invite_contacts", "text not null default '[]'"),
            ("group_invite_primary_contact", "text not null default ''"),
            ("group_invite_primary_contact_name", "text not null default ''"),
            ("group_invite_welcome_message", "text not null default ''"),
        ):
            if column_name not in config_columns:
                conn.execute(f"alter table wechat_auto_reply_config add column {column_name} {column_sql}")
                added_config_columns.append(column_name)
        if "group_invite_enabled" in added_config_columns:
            conn.execute(
                """
                update wechat_auto_reply_config
                   set group_invite_enabled=1
                 where coalesce(group_invite_memory_doc_id, '') <> ''
                    or coalesce(group_invite_keywords, '') <> ''
                    or coalesce(group_invite_contacts, '[]') <> '[]'
                    or coalesce(group_invite_primary_contact, '') <> ''
                """
                )
        task_columns = {str(row[1]) for row in conn.execute("pragma table_info(wechat_tasks)").fetchall()}
        if "client_request_id" not in task_columns:
            conn.execute("alter table wechat_tasks add column client_request_id text")
        conn.execute(
            "create unique index if not exists idx_wechat_tasks_client_request "
            "on wechat_tasks(account_id, client_request_id) where client_request_id is not null and client_request_id <> ''"
        )
        # A local backend restart cannot resume a half-completed UI round.
        # Clear only the persisted running marker once per process so a stale
        # client state never blocks the next explicit run.
        with _AUTO_REPLY_STARTUP_RECONCILE_LOCK:
            if not _AUTO_REPLY_STARTUP_RECONCILED:
                conn.execute(
                    """
                    update wechat_auto_reply_config
                       set running=0,
                           last_error=case
                               when coalesce(last_error, '') = ''
                               then '本机服务已重启，上一次接管未完成'
                               else last_error
                           end,
                           updated_at=?
                     where running=1
                    """,
                    (_now_iso(),),
                )
                _AUTO_REPLY_STARTUP_RECONCILED = True


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    for key in ("raw_json", "targets", "payload", "strategy", "last_result", "memory_doc_ids", "group_invite_contacts"):
        if key in item and isinstance(item[key], str) and item[key]:
            item[key] = _safe_json_loads(item[key], item[key])
    return item


def _is_local_account_id(account_id: str) -> bool:
    return str(account_id or "").startswith(LOCAL_ACCOUNT_PREFIX)


def _local_account_id(hwnd: int) -> str:
    return LOCAL_DEFAULT_ACCOUNT_ID


def _local_hwnd_from_account_id(account_id: str) -> int:
    value = str(account_id or "").strip()
    if not value.startswith(LOCAL_ACCOUNT_PREFIX):
        return 0
    if value == LOCAL_DEFAULT_ACCOUNT_ID:
        windows = _scan_local_wechat_windows(max_age_seconds=0)
        if not windows:
            windows = _ensure_local_wechat_window_visible().get("windows") or []
        return int((windows[0] if windows else {}).get("hwnd") or 0)
    try:
        return int(value[len(LOCAL_ACCOUNT_PREFIX) :])
    except Exception:
        return 0


def _process_meta(pid: int) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"pid": int(pid or 0), "name": "", "exe": "", "version": ""}
    try:
        import psutil  # type: ignore

        proc = psutil.Process(pid)
        meta["name"] = proc.name() or ""
        meta["exe"] = proc.exe() or ""
    except Exception:
        return meta
    exe = str(meta.get("exe") or "")
    if exe:
        try:
            import win32api  # type: ignore

            info = win32api.GetFileVersionInfo(exe, "\\")
            ms = info.get("FileVersionMS", 0)
            ls = info.get("FileVersionLS", 0)
            meta["version"] = ".".join(
                str(x)
                for x in (
                    win32api.HIWORD(ms),
                    win32api.LOWORD(ms),
                    win32api.HIWORD(ls),
                    win32api.LOWORD(ls),
                )
            )
        except Exception:
            parent = Path(exe).parent.name
            if parent and parent[0].isdigit():
                meta["version"] = parent
    return meta


def _integrity_label(rid: int) -> str:
    if rid >= 0x4000:
        return "system"
    if rid >= 0x3000:
        return "high"
    if rid >= 0x2100:
        return "medium_plus"
    if rid >= 0x2000:
        return "medium"
    if rid >= 0x1000:
        return "low"
    return "unknown"


def _process_integrity(pid: int) -> Dict[str, Any]:
    pid = int(pid or 0)
    if not pid:
        return {"pid": 0, "rid": 0, "label": "unknown", "error": "missing pid"}
    try:
        import win32api  # type: ignore
        import win32con  # type: ignore
        import win32security  # type: ignore

        hproc = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            htok = win32security.OpenProcessToken(hproc, win32con.TOKEN_QUERY)
            try:
                sid, _attrs = win32security.GetTokenInformation(htok, win32security.TokenIntegrityLevel)
                try:
                    count = win32security.GetSidSubAuthorityCount(sid)
                    rid = int(win32security.GetSidSubAuthority(sid, count - 1))
                except AttributeError:
                    count = sid.GetSubAuthorityCount()
                    rid = int(sid.GetSubAuthority(count - 1))
                return {"pid": pid, "rid": rid, "label": _integrity_label(rid)}
            finally:
                try:
                    htok.Close()
                except Exception:
                    pass
        finally:
            try:
                hproc.Close()
            except Exception:
                pass
    except Exception as exc:
        return {"pid": pid, "rid": 0, "label": "unknown", "error": str(exc)}


def _looks_like_wechat_process(meta: Dict[str, Any]) -> bool:
    name = str(meta.get("name") or "").lower()
    exe = str(meta.get("exe") or "").lower().replace("/", "\\")
    return name in WECHAT_PROCESS_NAMES or "\\tencent\\weixin\\" in exe or "\\program files\\tencent\\weixin" in exe


def _clear_local_windows_cache() -> None:
    _LOCAL_WINDOWS_CACHE.update({"items": [], "at": 0.0, "error": ""})


def _wechat_process_brief(items: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    source = items if items is not None else _wechat_process_candidates()
    for meta in source:
        out.append(
            {
                "pid": int(meta.get("pid") or 0),
                "name": str(meta.get("name") or ""),
                "exe": str(meta.get("exe") or ""),
                "version": str(meta.get("version") or ""),
                "integrity": meta.get("integrity") or {},
            }
        )
    return out


def _wechat_window_rank(item: Dict[str, Any]) -> int:
    """Prefer the real chat window over QR/login helper windows."""
    rank = 0
    area = int(item.get("area") or 0)
    if area >= 420_000:
        rank += 40
    elif area >= 260_000:
        rank += 24
    elif area >= 150_000:
        rank += 8
    else:
        rank -= 12
    if item.get("full_driver_ready"):
        rank += 60
    if item.get("is_iconic"):
        rank -= 4
    if str(item.get("class_name") or "") in WECHAT_WINDOW_CLASSES:
        rank += 8
    if str(item.get("title") or "").strip() in WECHAT_WINDOW_TITLES:
        rank += 4
    return rank


def _wechat_process_candidates() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        import psutil  # type: ignore

        for proc in psutil.process_iter(["pid", "name", "exe"]):
            try:
                info = proc.info or {}
                meta = {
                    "pid": int(info.get("pid") or 0),
                    "name": str(info.get("name") or ""),
                    "exe": str(info.get("exe") or ""),
                }
                if _looks_like_wechat_process(meta):
                    full = _process_meta(int(meta["pid"]))
                    integrity = _process_integrity(int(meta["pid"]))
                    out.append({**meta, **full, "integrity": integrity})
            except Exception:
                continue
    except Exception:
        return []
    return out


def _known_wechat_exe_paths() -> List[str]:
    paths: List[str] = []
    for meta in _wechat_process_candidates():
        exe = str(meta.get("exe") or "").strip()
        if (
            exe
            and Path(exe).name.lower() in WECHAT_PROCESS_NAMES
            and Path(exe).is_file()
            and exe not in paths
        ):
            paths.append(exe)
    for raw in (
        os.environ.get("WECHAT_EXE", ""),
        r"C:\Program Files\Tencent\Weixin\Weixin.exe",
        r"C:\Program Files (x86)\Tencent\Weixin\Weixin.exe",
        r"C:\Program Files\Tencent\WeChat\WeChat.exe",
        r"C:\Program Files (x86)\Tencent\WeChat\WeChat.exe",
    ):
        text = str(raw or "").strip()
        if text and Path(text).is_file() and text not in paths:
            paths.append(text)
    return paths


def _restore_hidden_wechat_windows() -> Dict[str, Any]:
    result: Dict[str, Any] = {"restored": False, "found": 0, "hwnds": [], "errors": []}
    try:
        import win32con  # type: ignore
        import win32gui  # type: ignore
        import win32process  # type: ignore
    except Exception as exc:
        result["errors"].append(f"missing win32 window dependency: {exc}")
        return result

    candidates: List[int] = []

    def _enum(hwnd: int, _extra: Any) -> None:
        try:
            title = win32gui.GetWindowText(hwnd) or ""
            class_name = win32gui.GetClassName(hwnd) or ""
            if "TrayIcon" in class_name:
                return
            _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
            meta = _process_meta(int(pid or 0))
            class_l = class_name.lower()
            looks_like_restore_window = (
                _looks_like_wechat_window(title, class_name, meta)
                or (_looks_like_wechat_process(meta) and "qwindowicon" in class_l)
            )
            if not looks_like_restore_window:
                return
            candidates.append(int(hwnd))
        except Exception:
            return

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception as exc:
        result["errors"].append(str(exc))
        return result

    result["found"] = len(candidates)
    for hwnd in candidates:
        try:
            if not win32gui.IsWindow(hwnd):
                continue
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            try:
                win32gui.SetWindowPos(
                    hwnd,
                    win32con.HWND_TOP,
                    0,
                    0,
                    0,
                    0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
                )
            except Exception:
                pass
            result["hwnds"].append(hwnd)
        except Exception as exc:
            result["errors"].append(f"{hwnd}: {exc}")
    time.sleep(0.6)
    for hwnd in candidates:
        try:
            if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
                result["restored"] = True
                break
        except Exception:
            continue
    if result["restored"]:
        _clear_local_windows_cache()
    return result


def _launch_wechat_single_instance() -> Dict[str, Any]:
    result: Dict[str, Any] = {"launched": False, "path": "", "errors": []}
    for exe in _known_wechat_exe_paths():
        try:
            os.startfile(exe)  # type: ignore[attr-defined]
            result.update({"launched": True, "path": exe})
            time.sleep(1.2)
            _clear_local_windows_cache()
            return result
        except Exception as exc:
            result["errors"].append(f"os.startfile {exe}: {exc}")
            try:
                subprocess.Popen([exe], cwd=str(Path(exe).parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                result.update({"launched": True, "path": exe})
                time.sleep(1.2)
                _clear_local_windows_cache()
                return result
            except Exception as sub_exc:
                result["errors"].append(f"popen {exe}: {sub_exc}")
    return result


def _ensure_local_wechat_window_visible(*, wait_seconds: float = 4.0, allow_launch: bool = False) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ok": False,
        "action": "none",
        "windows": [],
        "restore": {},
        "launch": {},
        "processes": [],
    }
    windows = _scan_local_wechat_windows(max_age_seconds=0)
    initial_windows = windows
    if windows and _wechat_window_rank(windows[0]) >= 32:
        result.update({"ok": True, "windows": windows})
        return result

    restore = _restore_hidden_wechat_windows()
    result["restore"] = restore
    if restore.get("restored"):
        deadline = time.time() + max(0.5, float(wait_seconds))
        while time.time() < deadline:
            windows = _scan_local_wechat_windows(max_age_seconds=0)
            if windows:
                result.update({"ok": True, "action": "restore_hidden_window", "windows": windows})
                return result
            time.sleep(0.3)
    if initial_windows:
        result.update({"ok": True, "action": "visible_window_low_confidence", "windows": initial_windows})
        return result

    processes = _wechat_process_candidates()
    if processes:
        result["processes"] = _wechat_process_brief(processes)
        result["action"] = "wechat_process_running_without_readable_window"
        result["launch"] = {
            "launched": False,
            "skipped": True,
            "reason": "微信进程已存在，但没有找到可复用主窗口；为避免打开一个新的未登录微信，本次不再启动 Weixin.exe。",
        }
        return result

    result["processes"] = []
    if not allow_launch:
        result["action"] = "no_wechat_window_launch_disabled"
        result["launch"] = {
            "launched": False,
            "skipped": True,
            "reason": "launch disabled for detection/sync to avoid opening a second login window",
        }
        return result

    launch = _launch_wechat_single_instance()
    result["launch"] = launch
    if launch.get("launched"):
        deadline = time.time() + max(1.0, float(wait_seconds))
        while time.time() < deadline:
            windows = _scan_local_wechat_windows(max_age_seconds=0)
            if windows:
                result.update({"ok": True, "action": "launch_single_instance", "windows": windows})
                return result
            # Some WeChat builds first create a hidden main window, then show it
            # on the second single-instance activation.
            _restore_hidden_wechat_windows()
            time.sleep(0.4)
    return result


def _looks_like_wechat_window(title: str, class_name: str, process_meta: Dict[str, Any]) -> bool:
    title = str(title or "").strip()
    class_name = str(class_name or "").strip()
    process_ok = _looks_like_wechat_process(process_meta)
    class_l = class_name.lower()
    if class_name in WECHAT_WINDOW_CLASSES:
        return True
    if title in WECHAT_WINDOW_TITLES and process_ok:
        return True
    if process_ok and any(token in title for token in ("微信", "WeChat", "Weixin")):
        return True
    if process_ok and class_l.startswith("qt") and ("qwindow" in class_l or "qwidget" in class_l):
        return True
    return False


def _parse_version_tuple(value: str) -> tuple[int, int, int, int]:
    parts = [int(x) for x in re.findall(r"\d+", str(value or ""))[:4]]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])  # type: ignore[return-value]


def _version_lte(value: str, upper: tuple[int, int, int, int]) -> bool:
    parsed = _parse_version_tuple(value)
    return parsed != (0, 0, 0, 0) and parsed <= upper


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _probe_wechat_uia_unlocked(hwnd: int) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "available": False,
        "node_count": 0,
        "reason": "",
        "has_mmui_controls": False,
    }
    try:
        import uiautomation as auto  # type: ignore

        root = auto.ControlFromHandle(int(hwnd))
        queue = [(root, 0)]
        seen = 0
        has_mmui = False
        while queue and seen < 80:
            node, depth = queue.pop(0)
            seen += 1
            class_name = str(getattr(node, "ClassName", "") or "")
            name = str(getattr(node, "Name", "") or "")
            if class_name.startswith("mmui::") or name.startswith("mmui::"):
                has_mmui = True
            if depth < 4:
                try:
                    for child in node.GetChildren():
                        queue.append((child, depth + 1))
                except Exception:
                    pass
        result.update(
            {
                "available": has_mmui,
                "node_count": seen,
                "has_mmui_controls": has_mmui,
                "reason": "" if has_mmui else "当前微信窗口只暴露渲染壳，读取不到通讯录/消息控件树",
            }
        )
    except Exception as exc:
        result["reason"] = f"UIA 探测失败：{exc}"
    return result


def _probe_wechat_uia(hwnd: int) -> Dict[str, Any]:
    with _LOCAL_WECHAT_UI_LOCK:
        _prepare_local_automation_thread()
        return _probe_wechat_uia_unlocked(hwnd)


def _probe_wxauto4_unlocked(item: Dict[str, Any]) -> Dict[str, Any]:
    version = str(item.get("version") or "")
    out: Dict[str, Any] = {
        "installed": _module_available("wxauto4"),
        "usable": False,
        "version": version,
        "supported_free": _version_lte(version, WXAUTO4_MAX_FREE_VERSION),
        "supported_plus": _version_lte(version, WXAUTO4_MAX_PLUS_VERSION),
        "reason": "",
    }
    if not out["installed"]:
        out["reason"] = "缺少 wxauto4"
        return out
    try:
        import wxauto4  # type: ignore

        _ensure_local_chat_tab(str(item.get("account_id") or ""))
        wx = wxauto4.WeChat(debug=False, resize=False, ads=False)
        out["usable"] = bool(wx.IsOnline())
        out["reason"] = "" if out["usable"] else "wxauto4 未识别到已登录微信主窗口"
    except Exception as exc:
        out["reason"] = str(exc)
    if not out["usable"] and version and not out["supported_plus"]:
        out["reason"] = (
            f"当前微信版本 {version} 高于 wxauto4 已知适配版本，完整通讯录/群能力不可用；"
            "请使用适配版本微信或接入自研本机驱动"
        )
    return out


def _probe_wxauto4(item: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCAL_WECHAT_UI_LOCK:
        _prepare_local_automation_thread()
        return _probe_wxauto4_unlocked(item)


def _scan_local_wechat_windows(*, max_age_seconds: float = 1.5) -> List[Dict[str, Any]]:
    now = time.time()
    cached = _LOCAL_WINDOWS_CACHE.get("items") or []
    if cached and now - float(_LOCAL_WINDOWS_CACHE.get("at") or 0) < max_age_seconds:
        return [dict(x) for x in cached]
    items: List[Dict[str, Any]] = []
    try:
        import win32gui  # type: ignore
        import win32process  # type: ignore
    except Exception as exc:
        _LOCAL_WINDOWS_CACHE.update(
            {
                "items": [],
                "at": now,
                "error": f"缺少 Windows 窗口依赖：{exc}",
            }
        )
        return []

    def _enum(hwnd: int, _extra: Any) -> None:
        try:
            title = win32gui.GetWindowText(hwnd) or ""
            class_name = win32gui.GetClassName(hwnd) or ""
            visible = bool(win32gui.IsWindowVisible(hwnd))
            iconic = bool(win32gui.IsIconic(hwnd))
            try:
                rect = tuple(int(x) for x in win32gui.GetWindowRect(hwnd))
            except Exception:
                rect = (0, 0, 0, 0)
            width = max(0, int(rect[2]) - int(rect[0]))
            height = max(0, int(rect[3]) - int(rect[1]))
            _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
            meta = _process_meta(int(pid or 0))
            if not _looks_like_wechat_window(title, class_name, meta):
                return
            if not visible and "TrayIcon" not in class_name:
                return
            if "TrayIcon" in class_name:
                return
            items.append(
                {
                    "account_id": _local_account_id(hwnd),
                    "id": _local_account_id(hwnd),
                    "name": title or "微信",
                    "source": "pc_wechat",
                    "configured": True,
                    "driver_ready": _local_action_driver_ready(),
                    "driver": "pywin32-ui",
                    "full_driver_ready": False,
                    "hwnd": int(hwnd),
                    "pid": int(pid or 0),
                    "title": title,
                    "class_name": class_name,
                    "is_visible": visible,
                    "is_iconic": iconic,
                    "rect": list(rect),
                    "width": width,
                    "height": height,
                    "area": width * height,
                    "process_name": meta.get("name") or "",
                    "process_path": meta.get("exe") or "",
                    "version": meta.get("version") or "",
                    "user_id": "",
                    "saved_at": "",
                }
            )
        except Exception:
            return

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception:
        items = []
    dedup: Dict[str, Dict[str, Any]] = {}
    for item in items:
        uia_status = _probe_wechat_uia(int(item.get("hwnd") or 0))
        item["uia"] = uia_status
        item["full_driver_ready"] = bool(uia_status.get("available"))
        key = str(item["account_id"])
        previous = dedup.get(key)
        if previous is None or _wechat_window_rank(item) > _wechat_window_rank(previous):
            dedup[key] = item
    out = sorted(dedup.values(), key=_wechat_window_rank, reverse=True)
    _LOCAL_WINDOWS_CACHE.update({"items": out, "at": now, "error": ""})
    return [dict(x) for x in out]


def _find_visible_local_moments_hwnd() -> int:
    try:
        import win32gui  # type: ignore
        import win32process  # type: ignore
    except Exception:
        return 0
    found = 0

    def _enum(hwnd: int, _extra: Any) -> None:
        nonlocal found
        if found:
            return
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd) or ""
            class_name = win32gui.GetClassName(hwnd) or ""
            if title != "朋友圈":
                return
            _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
            if not _looks_like_wechat_process(_process_meta(int(pid or 0))):
                return
            if "QWindowIcon" not in class_name and "SNS" not in class_name:
                return
            found = int(hwnd)
        except Exception:
            return

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception:
        return 0
    return found


def _local_moments_or_main_hwnd(account_id: str) -> int:
    try:
        hwnd = _local_wechat_hwnd(account_id)
        if hwnd:
            return hwnd
    except Exception:
        pass
    return _find_visible_local_moments_hwnd()


def _local_action_driver_ready() -> bool:
    try:
        import win32api  # noqa: F401
        import win32clipboard  # noqa: F401
        import win32con  # noqa: F401
        import win32gui  # noqa: F401

        return True
    except Exception:
        return False


def _local_driver_status(*, passive: bool = False) -> Dict[str, Any]:
    deps: Dict[str, bool] = {}
    for name in (
        "win32gui",
        "win32process",
        "win32api",
        "win32con",
        "psutil",
        "uiautomation",
        "pywinauto",
        "pyperclip",
        "wxauto4",
    ):
        deps[name] = _module_available(name)
    restore_info: Dict[str, Any] = {}
    windows = _scan_local_wechat_windows(max_age_seconds=0)
    if not windows and not passive:
        restore_info = _ensure_local_wechat_window_visible()
        windows = restore_info.get("windows") or []
    if passive:
        full_probe = {
            "installed": deps.get("wxauto4", False),
            "usable": False,
            "passive": True,
            "reason": "未主动检测",
        }
    else:
        full_probe = _probe_wxauto4(windows[0]) if windows else {"installed": deps.get("wxauto4", False), "usable": False}
    return {
        "ok": bool(windows),
        "driver_ready": all(deps.get(name, False) for name in ("win32gui", "win32process", "win32api", "win32con")),
        "full_driver_ready": bool(full_probe.get("usable")),
        "full_driver": full_probe,
        "dependencies": deps,
        "windows": windows,
        "count": len(windows),
        "restore": restore_info,
    }


def _legacy_local_account_ids(conn: sqlite3.Connection) -> List[str]:
    tables = (
        "wechat_peers",
        "wechat_contacts",
        "wechat_groups",
        "wechat_group_members",
        "wechat_friend_requests",
        "wechat_messages",
        "wechat_session_state",
        "wechat_tasks",
    )
    ids: set[str] = set()
    for table in tables:
        for row in conn.execute(
            f"select distinct account_id from {table} where account_id like ?",
            (f"{LOCAL_ACCOUNT_PREFIX}%",),
        ).fetchall():
            account_id = str(row["account_id"] or "").strip()
            if account_id and account_id != LOCAL_DEFAULT_ACCOUNT_ID:
                ids.add(account_id)
    return sorted(ids)


def _has_local_account_data(account_id: str) -> bool:
    init_db()
    tables = (
        "wechat_peers",
        "wechat_contacts",
        "wechat_groups",
        "wechat_group_members",
        "wechat_friend_requests",
        "wechat_messages",
        "wechat_session_state",
        "wechat_tasks",
    )
    with _connect() as conn:
        for table in tables:
            row = conn.execute(f"select 1 from {table} where account_id=? limit 1", (account_id,)).fetchone()
            if row:
                return True
    return False


def _migrate_legacy_local_account_data() -> None:
    init_db()
    target = LOCAL_DEFAULT_ACCOUNT_ID
    with _connect() as conn:
        legacy_ids = _legacy_local_account_ids(conn)
        for old_id in legacy_ids:
            for row in conn.execute("select * from wechat_peers where account_id=?", (old_id,)).fetchall():
                conn.execute(
                    """
                    insert into wechat_peers(
                        id, account_id, peer_id, display_name, chat_type, context_token,
                        last_inbound_at, last_outbound_at, raw_json, created_at, updated_at
                    )
                    values(?,?,?,?,?,?,?,?,?,?,?)
                    on conflict(account_id, peer_id) do update set
                      display_name=coalesce(excluded.display_name, wechat_peers.display_name),
                      chat_type=case when excluded.chat_type != 'unknown' then excluded.chat_type else wechat_peers.chat_type end,
                      context_token=coalesce(excluded.context_token, wechat_peers.context_token),
                      last_inbound_at=coalesce(excluded.last_inbound_at, wechat_peers.last_inbound_at),
                      last_outbound_at=coalesce(excluded.last_outbound_at, wechat_peers.last_outbound_at),
                      raw_json=coalesce(excluded.raw_json, wechat_peers.raw_json),
                      updated_at=excluded.updated_at
                    """,
                    (
                        _stable_key(target, row["peer_id"]),
                        target,
                        row["peer_id"],
                        row["display_name"],
                        row["chat_type"],
                        row["context_token"],
                        row["last_inbound_at"],
                        row["last_outbound_at"],
                        row["raw_json"],
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
            conn.execute("delete from wechat_peers where account_id=?", (old_id,))

            for row in conn.execute("select * from wechat_contacts where account_id=?", (old_id,)).fetchall():
                conn.execute(
                    """
                    insert into wechat_contacts(id, account_id, contact_key, display_name, remark, wx_no, source, raw_json, created_at, updated_at)
                    values(?,?,?,?,?,?,?,?,?,?)
                    on conflict(account_id, contact_key) do update set
                      display_name=excluded.display_name,
                      remark=excluded.remark,
                      wx_no=excluded.wx_no,
                      source=excluded.source,
                      raw_json=excluded.raw_json,
                      updated_at=excluded.updated_at
                    """,
                    (
                        _stable_key(target, row["contact_key"]),
                        target,
                        row["contact_key"],
                        row["display_name"],
                        row["remark"],
                        row["wx_no"],
                        row["source"],
                        row["raw_json"],
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
            conn.execute("delete from wechat_contacts where account_id=?", (old_id,))

            for row in conn.execute("select * from wechat_groups where account_id=?", (old_id,)).fetchall():
                conn.execute(
                    """
                    insert into wechat_groups(id, account_id, group_key, display_name, member_count, remark, source, raw_json, created_at, updated_at)
                    values(?,?,?,?,?,?,?,?,?,?)
                    on conflict(account_id, group_key) do update set
                      display_name=excluded.display_name,
                      member_count=excluded.member_count,
                      remark=excluded.remark,
                      source=excluded.source,
                      raw_json=excluded.raw_json,
                      updated_at=excluded.updated_at
                    """,
                    (
                        _stable_key(target, row["group_key"]),
                        target,
                        row["group_key"],
                        row["display_name"],
                        row["member_count"],
                        row["remark"],
                        row["source"],
                        row["raw_json"],
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
            conn.execute("delete from wechat_groups where account_id=?", (old_id,))

            for row in conn.execute("select * from wechat_group_members where account_id=?", (old_id,)).fetchall():
                conn.execute(
                    """
                    insert into wechat_group_members(id, account_id, group_key, member_key, display_name, raw_json, created_at, updated_at)
                    values(?,?,?,?,?,?,?,?)
                    on conflict(account_id, group_key, member_key) do update set
                      display_name=excluded.display_name,
                      raw_json=excluded.raw_json,
                      updated_at=excluded.updated_at
                    """,
                    (
                        _stable_key(target, row["group_key"], row["member_key"]),
                        target,
                        row["group_key"],
                        row["member_key"],
                        row["display_name"],
                        row["raw_json"],
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
            conn.execute("delete from wechat_group_members where account_id=?", (old_id,))

            for row in conn.execute("select * from wechat_session_state where account_id=?", (old_id,)).fetchall():
                conn.execute(
                    """
                    insert into wechat_session_state(
                        id, account_id, peer_id, display_name, chat_type, last_content, session_time,
                        unread_count, is_new, is_muted, raw_json, first_seen_at, updated_at
                    )
                    values(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    on conflict(account_id, peer_id) do update set
                      display_name=excluded.display_name,
                      chat_type=case when excluded.chat_type != 'unknown' then excluded.chat_type else wechat_session_state.chat_type end,
                      last_content=excluded.last_content,
                      session_time=excluded.session_time,
                      unread_count=excluded.unread_count,
                      is_new=excluded.is_new,
                      is_muted=excluded.is_muted,
                      raw_json=excluded.raw_json,
                      updated_at=excluded.updated_at
                    """,
                    (
                        _stable_key(target, row["peer_id"]),
                        target,
                        row["peer_id"],
                        row["display_name"],
                        row["chat_type"],
                        row["last_content"],
                        row["session_time"],
                        row["unread_count"],
                        row["is_new"],
                        row["is_muted"],
                        row["raw_json"],
                        row["first_seen_at"],
                        row["updated_at"],
                    ),
                )
            conn.execute("delete from wechat_session_state where account_id=?", (old_id,))

            for table in ("wechat_friend_requests", "wechat_messages", "wechat_tasks"):
                conn.execute(f"update {table} set account_id=? where account_id=?", (target, old_id))


def list_accounts() -> List[Dict[str, Any]]:
    init_db()
    _migrate_legacy_local_account_data()
    ensured = _ensure_local_wechat_window_visible()
    out: List[Dict[str, Any]] = ensured.get("windows") or []
    if not out:
        out = _scan_local_wechat_windows(max_age_seconds=0)
    if out:
        return out
    if _has_local_account_data(LOCAL_DEFAULT_ACCOUNT_ID):
        return [
            {
                "account_id": LOCAL_DEFAULT_ACCOUNT_ID,
                "id": LOCAL_DEFAULT_ACCOUNT_ID,
                "name": "本机微信",
                "source": "pc_wechat",
                "configured": True,
                "driver_ready": False,
                "driver": "pywin32-ui",
                "full_driver_ready": False,
                "hwnd": 0,
                "pid": 0,
                "title": "本机微信",
                "class_name": "",
                "process_name": "",
                "process_path": "",
                "version": "",
                "user_id": "",
                "saved_at": "",
                "offline": True,
            }
        ]
    ids = _read_json_file(STATE_DIR / "accounts.json", [])
    if not isinstance(ids, list):
        ids = []
    for raw_id in ids:
        account_id = str(raw_id or "").strip()
        if not account_id:
            continue
        data = _load_account(account_id)
        out.append(
            {
                "account_id": account_id,
                "id": account_id,
                "name": data.get("userId") or account_id,
                "source": "ilink",
                "user_id": data.get("userId") or "",
                "base_url": data.get("baseUrl") or DEFAULT_BASE_URL,
                "configured": bool(data.get("token")),
                "driver_ready": bool(data.get("token")),
                "driver": "ilink",
                "saved_at": data.get("savedAt") or "",
            }
        )
    return out


def local_driver_status(*, passive: bool = False) -> Dict[str, Any]:
    init_db()
    return _local_driver_status(passive=passive)


def _uia_node_debug(node: Any, depth: int) -> Dict[str, Any]:
    text = _uia_control_text(node)
    rect = _uia_rect(node)
    try:
        children_count = len(node.GetChildren())
    except Exception:
        children_count = 0
    return {
        "depth": depth,
        "class_name": _uia_control_class(node),
        "control_type": str(getattr(node, "ControlTypeName", "") or ""),
        "name_lines": len([line for line in text.splitlines() if line.strip()]),
        "name_sample": text[:180],
        "rect": rect,
        "children": children_count,
    }


def _uia_debug_tree(root: Any, *, max_depth: int = 10, max_nodes: int = 900) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    queue = [(root, 0)]
    while queue and len(out) < max_nodes:
        node, depth = queue.pop(0)
        out.append(_uia_node_debug(node, depth))
        if depth >= max_depth:
            continue
        try:
            children = node.GetChildren()
        except Exception:
            children = []
        queue.extend((child, depth + 1) for child in children)
    return out


def _diagnose_uia_page(root: Any, *, include_tree: bool = True) -> Dict[str, Any]:
    nodes = _uia_walk(root, max_depth=20, max_nodes=5000)
    root_rect = _uia_rect(root)
    exact_sessions = [node for node in nodes if _uia_control_class(node) == "mmui::ChatSessionCell"]
    generic_sessions = [node for node in nodes if _looks_like_uia_session_candidate(node, root_rect)]
    contact_lists = _uia_contact_recycler_lists(root)
    contact_cells = [node for node in nodes if _uia_control_class(node) == "mmui::ContactsCellItemView"]
    class_counts: Dict[str, int] = {}
    type_counts: Dict[str, int] = {}
    for node in nodes:
        cls = _uia_control_class(node) or "(empty)"
        typ = str(getattr(node, "ControlTypeName", "") or "(empty)")
        class_counts[cls] = class_counts.get(cls, 0) + 1
        type_counts[typ] = type_counts.get(typ, 0) + 1
    top_classes = sorted(class_counts.items(), key=lambda item: item[1], reverse=True)[:30]
    top_types = sorted(type_counts.items(), key=lambda item: item[1], reverse=True)[:20]
    return {
        "node_count": len(nodes),
        "root_rect": root_rect,
        "exact_session_count": len(exact_sessions),
        "generic_session_count": len(generic_sessions),
        "session_samples": [_uia_node_debug(node, 0) for node in (exact_sessions or generic_sessions)[:12]],
        "contact_list_count": len(contact_lists),
        "contact_cell_count": len(contact_cells),
        "contact_list_samples": [_uia_node_debug(node, 0) for node in contact_lists[:8]],
        "contact_cell_samples": [_uia_node_debug(node, 0) for node in contact_cells[:12]],
        "top_classes": top_classes,
        "top_types": top_types,
        "tree": _uia_debug_tree(root) if include_tree else [],
    }


def diagnose_local_wechat_ui(account_id: str) -> Dict[str, Any]:
    init_db()
    item = _find_local_account(account_id)
    hwnd = int(item.get("hwnd") or 0)
    result: Dict[str, Any] = {
        "ok": True,
        "account_id": account_id,
        "window": item,
        "integrity": {
            "backend": _process_integrity(os.getpid()),
            "wechat": _process_integrity(int(item.get("pid") or 0)),
        },
        "driver": local_driver_status(passive=True),
        "chat_page": {},
        "contacts_page": {},
        "wxauto4": {},
    }
    if not _module_available("uiautomation"):
        result["ok"] = False
        result["error"] = "uiautomation is not installed"
        return result
    import uiautomation as auto  # type: ignore

    try:
        _ensure_local_tab(hwnd, "\u5fae\u4fe1", strict=False)
        chat_root = auto.ControlFromHandle(int(hwnd))
        result["chat_page"] = _diagnose_uia_page(chat_root, include_tree=True)
    except Exception as exc:
        result["chat_page"] = {"ok": False, "error": str(exc)}
    try:
        _ensure_local_tab(hwnd, "\u901a\u8baf\u5f55", strict=False)
        contacts_root = auto.ControlFromHandle(int(hwnd))
        result["contacts_page"] = _diagnose_uia_page(contacts_root, include_tree=True)
    except Exception as exc:
        result["contacts_page"] = {"ok": False, "error": str(exc)}
    try:
        wx = _get_wxauto4_client(account_id)
        methods = [name for name in dir(wx) if "Session" in name or "Friend" in name or "Contact" in name or "Chat" in name]
        session_count = None
        session_error = ""
        try:
            sessions = wx.GetSession()
            session_count = len(sessions or [])
        except Exception as sess_exc:
            session_error = str(sess_exc)
        result["wxauto4"] = {
            "ok": True,
            "methods": methods,
            "session_count": session_count,
            "session_error": session_error,
        }
    except Exception as exc:
        result["wxauto4"] = {"ok": False, "error": str(exc)}
    return result


def get_strategy() -> Dict[str, Any]:
    return dict(DEFAULT_STRATEGY)


def _strategy_float(name: str, default: float = 0.0) -> float:
    try:
        return float(get_strategy().get(name, default) or default)
    except Exception:
        return float(default)


def _human_pause(min_key: str = "ui_action_sleep_min", max_key: str = "ui_action_sleep_max", *, floor: float = 0.0) -> None:
    low = max(float(floor), _strategy_float(min_key, floor))
    high = max(low, _strategy_float(max_key, low))
    if high > 0:
        time.sleep(random.uniform(low, high))


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _server_proxy_base() -> str:
    return (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/") or "https://bhzn.top"


def _wechat_intelligence_headers(auth_context: Optional[Dict[str, Any]]) -> Dict[str, str]:
    context = auth_context or {}
    token = str(context.get("token") or getattr(settings, "openclaw_sutui_fallback_jwt", None) or "").strip()
    if not token:
        return {}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    installation_id = str(
        context.get("installation_id")
        or getattr(settings, "openclaw_sutui_fallback_installation_id", None)
        or ""
    ).strip()
    if installation_id:
        headers["X-Installation-Id"] = installation_id
    brand = str(context.get("brand") or "").strip()
    if brand:
        headers["X-Lobster-Brand"] = brand
    return headers


async def _load_wechat_intelligence_context(
    auth_context: Optional[Dict[str, Any]],
    *,
    account_id: str,
    contact_key: str,
    contact_name: str,
    latest_message: str,
) -> Dict[str, Any]:
    global _WECHAT_INTELLIGENCE_CIRCUIT_UNTIL
    headers = _wechat_intelligence_headers(auth_context)
    if not headers:
        return {"available": False, "contact": None, "rules": [], "error": "missing_token"}
    if time.monotonic() < _WECHAT_INTELLIGENCE_CIRCUIT_UNTIL:
        return {"available": False, "contact": None, "rules": [], "error": "server_circuit_open"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=3.0), trust_env=False) as client:
            response = await client.post(
                f"{_server_proxy_base()}/api/wechat-intelligence/context",
                headers=headers,
                json={
                    "account_id": str(account_id or "")[:160],
                    "contact_key": str(contact_key or "")[:240],
                    "contact_name": str(contact_name or "")[:240],
                    "latest_message": str(latest_message or "")[:4000],
                },
            )
        if response.status_code >= 400:
            _WECHAT_INTELLIGENCE_CIRCUIT_UNTIL = time.monotonic() + (300.0 if response.status_code in {401, 403} else 60.0)
            return {
                "available": False,
                "contact": None,
                "rules": [],
                "error": f"HTTP {response.status_code}: {(response.text or '')[:300]}",
            }
        data = response.json() if response.content else {}
        _WECHAT_INTELLIGENCE_CIRCUIT_UNTIL = 0.0
        return {
            "available": True,
            "contact": data.get("contact") if isinstance(data.get("contact"), dict) else None,
            "rules": data.get("rules") if isinstance(data.get("rules"), list) else [],
            "limits": data.get("limits") if isinstance(data.get("limits"), dict) else {},
        }
    except Exception as exc:
        _WECHAT_INTELLIGENCE_CIRCUIT_UNTIL = time.monotonic() + 60.0
        return {"available": False, "contact": None, "rules": [], "error": str(exc)[:300]}


def _wechat_intelligence_outbox_key(payload: Dict[str, Any]) -> str:
    base = "|".join(
        str(payload.get(key) or "").strip()
        for key in ("account_id", "contact_key", "inbound_message_id", "event_type")
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _enqueue_wechat_intelligence_observation(payload: Dict[str, Any]) -> str:
    init_db()
    dedup_key = _wechat_intelligence_outbox_key(payload)
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            """
            insert into wechat_intelligence_outbox(id,dedup_key,payload,attempts,last_error,created_at,updated_at)
            values(?,?,?,?,?,?,?)
            on conflict(dedup_key) do update set payload=excluded.payload,updated_at=excluded.updated_at
            """,
            (uuid.uuid4().hex, dedup_key, _json_dumps(payload), 0, "", now, now),
        )
        conn.execute(
            """
            delete from wechat_intelligence_outbox
            where id in (
              select id from wechat_intelligence_outbox order by created_at desc limit -1 offset 1000
            )
            """
        )
    return dedup_key


def _delete_wechat_intelligence_outbox(dedup_key: str) -> None:
    with _connect() as conn:
        conn.execute("delete from wechat_intelligence_outbox where dedup_key=?", (dedup_key,))


def _mark_wechat_intelligence_outbox_failed(dedup_key: str, error: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            update wechat_intelligence_outbox
            set attempts=attempts+1,last_error=?,updated_at=? where dedup_key=?
            """,
            (str(error or "")[:1000], _now_iso(), dedup_key),
        )
        conn.execute("delete from wechat_intelligence_outbox where attempts>=8")


async def _post_wechat_intelligence_observation(
    auth_context: Optional[Dict[str, Any]],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    global _WECHAT_INTELLIGENCE_CIRCUIT_UNTIL
    headers = _wechat_intelligence_headers(auth_context)
    if not headers:
        return {"ok": False, "skipped": True, "reason": "missing_token"}
    if time.monotonic() < _WECHAT_INTELLIGENCE_CIRCUIT_UNTIL:
        return {"ok": False, "skipped": True, "reason": "server_circuit_open"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=3.0), trust_env=False) as client:
            response = await client.post(
                f"{_server_proxy_base()}/api/wechat-intelligence/observe",
                headers=headers,
                json=payload,
            )
        if response.status_code >= 400:
            _WECHAT_INTELLIGENCE_CIRCUIT_UNTIL = time.monotonic() + (300.0 if response.status_code in {401, 403} else 60.0)
            return {"ok": False, "error": f"HTTP {response.status_code}: {(response.text or '')[:300]}"}
        data = response.json() if response.content else {}
        _WECHAT_INTELLIGENCE_CIRCUIT_UNTIL = 0.0
        return {"ok": bool(data.get("ok", True)), **data}
    except Exception as exc:
        _WECHAT_INTELLIGENCE_CIRCUIT_UNTIL = time.monotonic() + 60.0
        return {"ok": False, "error": str(exc)[:300]}


async def _observe_wechat_intelligence(
    auth_context: Optional[Dict[str, Any]],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    dedup_key = _enqueue_wechat_intelligence_observation(payload)
    result = await _post_wechat_intelligence_observation(auth_context, payload)
    if result.get("ok"):
        _delete_wechat_intelligence_outbox(dedup_key)
    elif not result.get("skipped"):
        _mark_wechat_intelligence_outbox_failed(dedup_key, str(result.get("error") or "upload_failed"))
    return {**result, "queued": not bool(result.get("ok"))}


async def _flush_wechat_intelligence_outbox(
    auth_context: Optional[Dict[str, Any]],
    *,
    limit: int = 20,
) -> Dict[str, int]:
    init_db()
    headers = _wechat_intelligence_headers(auth_context)
    if not headers or time.monotonic() < _WECHAT_INTELLIGENCE_CIRCUIT_UNTIL:
        return {"processed": 0, "remaining": 0}
    with _connect() as conn:
        rows = conn.execute(
            "select dedup_key,payload from wechat_intelligence_outbox order by created_at asc limit ?",
            (max(1, min(50, int(limit or 20))),),
        ).fetchall()
    processed = 0
    for row in rows:
        payload = _safe_json_loads(row["payload"], {})
        if not isinstance(payload, dict):
            _delete_wechat_intelligence_outbox(str(row["dedup_key"] or ""))
            continue
        result = await _post_wechat_intelligence_observation(auth_context, payload)
        if result.get("ok"):
            _delete_wechat_intelligence_outbox(str(row["dedup_key"] or ""))
            processed += 1
            continue
        if not result.get("skipped"):
            _mark_wechat_intelligence_outbox_failed(str(row["dedup_key"] or ""), str(result.get("error") or "upload_failed"))
        break
    with _connect() as conn:
        remaining = int(conn.execute("select count(1) from wechat_intelligence_outbox").fetchone()[0] or 0)
    return {"processed": processed, "remaining": remaining}


def _wechat_intelligence_prompt_context(context: Optional[Dict[str, Any]]) -> tuple[str, str]:
    data = context or {}
    contact = data.get("contact") if isinstance(data.get("contact"), dict) else {}
    rules = data.get("rules") if isinstance(data.get("rules"), list) else []
    contact_parts: List[str] = []
    if contact:
        profile = contact.get("profile") if isinstance(contact.get("profile"), dict) else {}
        contact_parts.extend(
            [
                f"客户阶段：{str(contact.get('stage') or 'unknown')[:48]}",
                f"意向等级：{str(contact.get('intent_level') or 'none')[:16]}",
                f"历史摘要：{str(contact.get('rolling_summary') or '')[:2000]}",
                f"客户资料：{json.dumps(profile, ensure_ascii=False)[:3000]}",
                f"建议跟进：{str(contact.get('next_followup') or '')[:1200]}",
            ]
        )
    rule_parts: List[str] = []
    used = 0
    for index, rule in enumerate(rules[:12], start=1):
        if not isinstance(rule, dict):
            continue
        content = str(rule.get("content") or "").strip()[:1600]
        if not content or used + len(content) > 7000:
            continue
        rule_parts.append(
            f"{index}. [{str(rule.get('category') or 'general')[:32]}] "
            f"{str(rule.get('title') or '长期规则')[:160]}：{content}"
        )
        used += len(content)
    return "\n".join(contact_parts).strip()[:7000], "\n".join(rule_parts).strip()[:7000]


def _json_from_text(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I).strip()
    raw = re.sub(r"\s*```$", "", raw).strip()
    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for item in candidates:
        try:
            data = json.loads(item)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


_AUTO_REPLY_CONTROL_KEYS = frozenset(
    {
        "should_reply",
        "category",
        "intent_level",
        "topic",
        "conversation_summary",
        "should_invite_group",
        "matched_group_keywords",
        "group_invite_reason",
    }
)


def _bool_value(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _looks_like_auto_reply_control_payload(value: Any) -> bool:
    parsed = _json_from_text(str(value or ""))
    return bool(parsed and _AUTO_REPLY_CONTROL_KEYS.intersection(parsed))


def _reply_claims_existing_group(value: Any) -> bool:
    text = re.sub(r"\s+", "", str(value or "")).strip()
    if not text:
        return False
    return bool(
        re.search(
            r"(?:您|你|客户)?(?:已经|已)(?:被)?拉(?:进|到)?群|"
            r"(?:您|你|客户)?(?:已经|已)(?:在|进)(?:群|群里)|"
            r"群里(?:同事|老师|负责人)(?:已经|会|马上)",
            text,
        )
    )


def _strip_unverified_group_claims(value: Any) -> Any:
    replacements = (
        "已经在群里",
        "已在群里",
        "已经进群",
        "已进群",
        "已经拉群",
        "已拉群",
        "已进入群聊",
        "确认已进入群聊",
        "已拉群对接",
        "拉入群聊",
        "拉进群聊",
        "进入群聊",
        "在群内",
        "群内对接",
    )
    if isinstance(value, str):
        text = value
        for phrase in replacements:
            text = text.replace(phrase, "群状态未核验")
        return text
    if isinstance(value, list):
        return [_strip_unverified_group_claims(item) for item in value]
    if isinstance(value, dict):
        return {key: _strip_unverified_group_claims(item) for key, item in value.items()}
    return value


def _is_affirmative_group_confirmation(latest_message: str, recent_context: str) -> bool:
    latest = re.sub(r"[\s\[\]【】()（）,.，。!！?？~～]+", "", str(latest_message or "")).lower()
    if not latest or len(latest) > 16:
        return False
    if not re.fullmatch(r"(?:行|可以|可以的|好|好的|好呀|好啊|没问题|同意|拉吧|建吧|安排|ok|okay|yes)", latest):
        return False
    return bool(
        re.search(
            r"(?:^|\n)我[:：][^\n]{0,240}(?:拉.{0,6}群|建.{0,6}群|进群|群里|服务群|对接群)",
            str(recent_context or ""),
            flags=re.I,
        )
    )


def _session_preview_matches_inbound(session: Dict[str, Any], inbound: Dict[str, Any]) -> bool:
    """No-badge scans must prove the changed preview is the inbound being handled."""
    try:
        if int(session.get("unread_count") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    preview = re.sub(r"\s+", "", str(session.get("last_content") or "")).strip()
    content = re.sub(r"\s+", "", str(inbound.get("content") or "")).strip()
    if not preview or not content:
        return False
    return preview in content or content in preview


def _looks_like_non_replyable_wechat_event(content: Any) -> bool:
    text = re.sub(r"\s+", "", str(content or "")).strip()
    if not text:
        return True
    call_markers = (
        "已在其它设备接听",
        "已在其他设备接听",
        "通话中断",
        "视频通话",
        "语音通话",
        "已取消",
        "已拒绝",
        "未接通",
        "对方忙线",
        "发起了语音通话",
        "发起了视频通话",
        "通话邀请",
        "对方已取消",
        "对方已拒绝",
        "对方无应答",
        "[语音通话]",
        "[视频通话]",
        "[通话]",
        "[语音]",
        "[视频]",
    )
    return any(marker in text for marker in call_markers)


def _auto_reply_default_config(account_id: str) -> Dict[str, Any]:
    return {
        "account_id": account_id,
        "enabled": False,
        "interval_seconds": int(DEFAULT_STRATEGY["auto_reply_interval_seconds"]),
        "group_invite_enabled": False,
        "user_id": None,
        "memory_doc_ids": [],
        "group_invite_memory_doc_id": "",
        "group_invite_keywords": "",
        "group_invite_contacts": [],
        "group_invite_primary_contact": "",
        "group_invite_primary_contact_name": "",
        "group_invite_welcome_message": "",
        "running": False,
        "last_started_at": "",
        "last_checked_at": "",
        "last_finished_at": "",
        "last_error": "",
        "last_result": {},
        "updated_at": "",
    }


def _normalize_auto_reply_config(row: Optional[sqlite3.Row], account_id: str) -> Dict[str, Any]:
    cfg = _auto_reply_default_config(account_id)
    if row:
        cfg.update(_row_to_dict(row))
    cfg["enabled"] = bool(int(cfg.get("enabled") or 0))
    cfg["group_invite_enabled"] = bool(int(cfg.get("group_invite_enabled") or 0))
    cfg["running"] = bool(int(cfg.get("running") or 0))
    try:
        cfg["interval_seconds"] = max(1, int(cfg.get("interval_seconds") or DEFAULT_STRATEGY["auto_reply_interval_seconds"]))
    except Exception:
        cfg["interval_seconds"] = int(DEFAULT_STRATEGY["auto_reply_interval_seconds"])
    if not isinstance(cfg.get("last_result"), dict):
        cfg["last_result"] = _safe_json_loads(str(cfg.get("last_result") or ""), {})
    for key in ("memory_doc_ids", "group_invite_contacts"):
        value = cfg.get(key)
        if not isinstance(value, list):
            value = _safe_json_loads(str(value or ""), [])
        cfg[key] = list(dict.fromkeys(str(item or "").strip() for item in value if str(item or "").strip()))[:50]
    cfg["group_invite_keywords"] = str(cfg.get("group_invite_keywords") or "").strip()[:2000]
    cfg["group_invite_memory_doc_id"] = str(cfg.get("group_invite_memory_doc_id") or "").strip()[:64]
    cfg["group_invite_primary_contact"] = str(cfg.get("group_invite_primary_contact") or "").strip()[:240]
    cfg["group_invite_primary_contact_name"] = str(cfg.get("group_invite_primary_contact_name") or "").strip()[:240]
    cfg["group_invite_welcome_message"] = str(cfg.get("group_invite_welcome_message") or "").strip()[:4000]
    return cfg


def get_auto_reply_config(account_id: str) -> Dict[str, Any]:
    init_db()
    account_id = str(account_id or "").strip()
    if not account_id:
        raise RuntimeError("missing account_id")
    with _connect() as conn:
        row = conn.execute(
            "select * from wechat_auto_reply_config where account_id=? limit 1",
            (account_id,),
        ).fetchone()
    return _normalize_auto_reply_config(row, account_id)


def save_auto_reply_config(
    account_id: str,
    *,
    enabled: bool,
    interval_seconds: int = 1800,
    group_invite_enabled: Optional[bool] = None,
    user_id: Optional[int] = None,
    memory_doc_ids: Optional[List[str]] = None,
    group_invite_memory_doc_id: Optional[str] = None,
    group_invite_keywords: Optional[str] = None,
    group_invite_contacts: Optional[List[str]] = None,
    group_invite_primary_contact: Optional[str] = None,
    group_invite_primary_contact_name: Optional[str] = None,
    group_invite_welcome_message: Optional[str] = None,
    auth_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    init_db()
    account_id = str(account_id or "").strip()
    if not account_id:
        raise RuntimeError("missing account_id")
    if not _is_local_account_id(account_id):
        raise RuntimeError("auto reply only supports local PC WeChat accounts")
    _find_local_account(account_id)
    interval_seconds = max(1, int(interval_seconds or DEFAULT_STRATEGY["auto_reply_interval_seconds"]))
    current = get_auto_reply_config(account_id)
    invite_enabled = current.get("group_invite_enabled") if group_invite_enabled is None else bool(group_invite_enabled)
    selected_memory_ids = current.get("memory_doc_ids") if memory_doc_ids is None else memory_doc_ids
    selected_memory_ids = list(
        dict.fromkeys(str(item or "").strip()[:64] for item in (selected_memory_ids or []) if str(item or "").strip())
    )[:20]
    invite_memory_doc_id = (
        current.get("group_invite_memory_doc_id")
        if group_invite_memory_doc_id is None
        else group_invite_memory_doc_id
    )
    invite_memory_doc_id = str(invite_memory_doc_id or "").strip()[:64]
    invite_keywords = current.get("group_invite_keywords") if group_invite_keywords is None else group_invite_keywords
    invite_keywords = str(invite_keywords or "").strip()[:2000]
    invite_contacts = current.get("group_invite_contacts") if group_invite_contacts is None else group_invite_contacts
    invite_contacts = list(
        dict.fromkeys(str(item or "").strip()[:240] for item in (invite_contacts or []) if str(item or "").strip())
    )[:20]
    primary_contact = (
        current.get("group_invite_primary_contact")
        if group_invite_primary_contact is None
        else group_invite_primary_contact
    )
    primary_contact = str(primary_contact or "").strip()[:240]
    primary_contact_name = (
        current.get("group_invite_primary_contact_name")
        if group_invite_primary_contact_name is None
        else group_invite_primary_contact_name
    )
    primary_contact_name = str(primary_contact_name or "").strip()[:240]
    welcome_message = (
        current.get("group_invite_welcome_message")
        if group_invite_welcome_message is None
        else group_invite_welcome_message
    )
    welcome_message = str(welcome_message or "").strip()[:4000]
    if primary_contact:
        invite_contacts = [primary_contact]
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            """
            insert into wechat_auto_reply_config(
                account_id, enabled, interval_seconds, group_invite_enabled, user_id, memory_doc_ids,
                group_invite_memory_doc_id, group_invite_keywords, group_invite_contacts,
                group_invite_primary_contact, group_invite_primary_contact_name,
                group_invite_welcome_message, running, updated_at
            )
            values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            on conflict(account_id) do update set
              enabled=excluded.enabled,
              interval_seconds=excluded.interval_seconds,
              group_invite_enabled=excluded.group_invite_enabled,
              user_id=coalesce(excluded.user_id, wechat_auto_reply_config.user_id),
              memory_doc_ids=excluded.memory_doc_ids,
              group_invite_memory_doc_id=excluded.group_invite_memory_doc_id,
              group_invite_keywords=excluded.group_invite_keywords,
              group_invite_contacts=excluded.group_invite_contacts,
              group_invite_primary_contact=excluded.group_invite_primary_contact,
              group_invite_primary_contact_name=excluded.group_invite_primary_contact_name,
              group_invite_welcome_message=excluded.group_invite_welcome_message,
              updated_at=excluded.updated_at
            """,
            (
                account_id,
                1 if enabled else 0,
                interval_seconds,
                1 if invite_enabled else 0,
                user_id,
                _json_dumps(selected_memory_ids),
                invite_memory_doc_id,
                invite_keywords,
                _json_dumps(invite_contacts),
                primary_contact,
                primary_contact_name,
                welcome_message,
                0,
                now,
            ),
        )
    if auth_context:
        _AUTO_REPLY_AUTH_CONTEXT[account_id] = dict(auth_context)
    if enabled:
        ensure_auto_reply_worker(account_id, auth_context=auth_context)
    else:
        worker = _AUTO_REPLY_WORKERS.get(account_id)
        if worker and not worker.done():
            worker.cancel()
    return get_auto_reply_config(account_id)


def _auto_reply_due(cfg: Dict[str, Any], *, force: bool = False) -> bool:
    if force:
        return True
    last = _parse_iso_datetime(cfg.get("last_checked_at") or cfg.get("last_finished_at"))
    if not last:
        return True
    interval = max(1, int(cfg.get("interval_seconds") or DEFAULT_STRATEGY["auto_reply_interval_seconds"]))
    return (datetime.utcnow() - last).total_seconds() >= interval


def _claim_auto_reply_run(account_id: str) -> bool:
    account_id = str(account_id or "").strip()
    with _AUTO_REPLY_ACTIVE_RUNS_LOCK:
        if account_id in _AUTO_REPLY_ACTIVE_RUNS:
            return False
        _AUTO_REPLY_ACTIVE_RUNS.add(account_id)
        _AUTO_REPLY_STOP_REQUESTS.discard(account_id)
    now = _now_iso()
    try:
        with _connect() as conn:
            row = conn.execute(
                "select account_id from wechat_auto_reply_config where account_id=? limit 1",
                (account_id,),
            ).fetchone()
            if not row:
                conn.execute(
                    """
                    insert into wechat_auto_reply_config(account_id, enabled, interval_seconds, running, last_started_at, updated_at)
                    values(?,?,?,?,?,?)
                    """,
                    (account_id, 0, int(DEFAULT_STRATEGY["auto_reply_interval_seconds"]), 1, now, now),
                )
            else:
                # The database flag survives a client restart. The in-process set
                # is the authoritative lock; an old persisted flag must not block
                # the first real takeover after restart.
                conn.execute(
                    """
                    update wechat_auto_reply_config
                    set running=1, last_started_at=?, last_error='', updated_at=?
                    where account_id=?
                    """,
                    (now, now, account_id),
                )
        return True
    except Exception:
        with _AUTO_REPLY_ACTIVE_RUNS_LOCK:
            _AUTO_REPLY_ACTIVE_RUNS.discard(account_id)
        raise


def _release_auto_reply_run(account_id: str) -> None:
    with _AUTO_REPLY_ACTIVE_RUNS_LOCK:
        _AUTO_REPLY_ACTIVE_RUNS.discard(str(account_id or "").strip())


def request_auto_reply_stop(account_id: str) -> Dict[str, Any]:
    """Ask the current local round to stop after its active UI call returns."""
    normalized = str(account_id or "").strip()
    if not normalized:
        raise RuntimeError("missing account_id")
    with _AUTO_REPLY_ACTIVE_RUNS_LOCK:
        active = normalized in _AUTO_REPLY_ACTIVE_RUNS
        if active:
            _AUTO_REPLY_STOP_REQUESTS.add(normalized)
    return {"ok": True, "account_id": normalized, "requested": active}


def _auto_reply_stop_requested(account_id: str) -> bool:
    with _AUTO_REPLY_ACTIVE_RUNS_LOCK:
        return str(account_id or "").strip() in _AUTO_REPLY_STOP_REQUESTS


def _clear_auto_reply_stop(account_id: str) -> None:
    with _AUTO_REPLY_ACTIVE_RUNS_LOCK:
        _AUTO_REPLY_STOP_REQUESTS.discard(str(account_id or "").strip())


def _finish_auto_reply_run(account_id: str, result: Dict[str, Any], error: str = "") -> None:
    now = _now_iso()
    try:
        with _connect() as conn:
            conn.execute(
                """
                update wechat_auto_reply_config
                set running=0, last_checked_at=?, last_finished_at=?, last_error=?, last_result=?, updated_at=?
                where account_id=?
                """,
                (now, now, str(error or "")[:2000], _json_dumps(result or {}), now, account_id),
            )
    finally:
        _clear_auto_reply_stop(account_id)
        _release_auto_reply_run(account_id)


def ensure_auto_reply_worker(account_id: str, *, auth_context: Optional[Dict[str, Any]] = None) -> None:
    account_id = str(account_id or "").strip()
    if not account_id:
        return
    if auth_context:
        _AUTO_REPLY_AUTH_CONTEXT[account_id] = dict(auth_context)
    existing = _AUTO_REPLY_WORKERS.get(account_id)
    if existing is not None and not existing.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _AUTO_REPLY_WORKERS[account_id] = loop.create_task(_run_auto_reply_worker(account_id))


async def _run_auto_reply_worker(account_id: str) -> None:
    try:
        while True:
            cfg = get_auto_reply_config(account_id)
            if not cfg.get("enabled"):
                return
            if _auto_reply_due(cfg):
                try:
                    await run_auto_reply_once(
                        account_id,
                        auth_context=_AUTO_REPLY_AUTH_CONTEXT.get(account_id),
                        force=False,
                        trigger="schedule",
                    )
                except Exception:
                    pass
            interval = max(1, int(cfg.get("interval_seconds") or DEFAULT_STRATEGY["auto_reply_interval_seconds"]))
            await asyncio.sleep(min(60, max(10, interval / 10)))
    finally:
        current = _AUTO_REPLY_WORKERS.get(account_id)
        if current is asyncio.current_task():
            _AUTO_REPLY_WORKERS.pop(account_id, None)


def _looks_like_group_session(item: Dict[str, Any]) -> bool:
    peer_id = str(item.get("peer_id") or item.get("display_name") or "").strip()
    chat_type = str(item.get("chat_type") or "").strip().lower()
    if chat_type in {"group", "chatroom"}:
        return True
    if chat_type in {"official", "subscription"}:
        return True
    if peer_id in {"公众号", "服务号", "订阅号", "文件传输助手"}:
        return True
    if "@chatroom" in peer_id.lower():
        return True
    if "群" in peer_id:
        return True
    preview = str(item.get("last_content") or "").strip()
    first_line = next((line.strip() for line in preview.splitlines() if line.strip()), "")
    if re.match(r"^[^:：]{1,40}[:：]", first_line):
        return True
    return False


def _auto_reply_content_key(content: Any) -> str:
    return re.sub(r"\s+", "", str(content or "")).strip().lower()


def _latest_auto_reply_candidate(account_id: str, peer_id: str) -> Optional[Dict[str, Any]]:
    latest = _latest_message_record(account_id, peer_id, include_system=True)
    if not latest or latest.get("direction") != "in":
        return None
    raw = latest.get("raw_json") if isinstance(latest.get("raw_json"), dict) else {}
    raw_type = str(raw.get("type") or "").strip().lower()
    raw_sender = str(raw.get("sender") or raw.get("sender_remark") or "").strip().lower()
    if (
        latest.get("is_system")
        or latest.get("msg_type") == "time"
        or raw_sender == "system"
        or raw_type in {"system", "sys", "status", "notification", "notify", "time", "voip", "call"}
        or _looks_like_non_replyable_wechat_event(latest.get("content"))
    ):
        return None
    content = str(latest.get("content") or "").strip()
    if not content:
        return None
    inbound_id = str(latest.get("provider_message_id") or latest.get("id") or _stable_key(peer_id, content, latest.get("created_at")))
    latest["auto_reply_inbound_id"] = inbound_id
    return latest


def _record_auto_reply_history(
    account_id: str,
    peer_id: str,
    inbound: Dict[str, Any],
    *,
    reply: str = "",
    category: str = "",
    status: str,
    error: str = "",
) -> bool:
    inbound_id = str(inbound.get("auto_reply_inbound_id") or inbound.get("provider_message_id") or inbound.get("id") or "")
    if not inbound_id:
        inbound_id = _stable_key(peer_id, inbound.get("content"), inbound.get("created_at"))
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            """
            insert into wechat_auto_reply_history(
                id, account_id, peer_id, inbound_message_id, inbound_content, reply_content,
                category, status, error_message, created_at, updated_at
            )
            values(?,?,?,?,?,?,?,?,?,?,?)
            on conflict(account_id, peer_id, inbound_message_id) do update set
                inbound_content=excluded.inbound_content,
                reply_content=excluded.reply_content,
                category=excluded.category,
                status=excluded.status,
                error_message=excluded.error_message,
                updated_at=excluded.updated_at
            """,
            (
                uuid.uuid4().hex,
                account_id,
                peer_id,
                inbound_id,
                str(inbound.get("content") or "")[:4000],
                str(reply or "")[:4000],
                str(category or "")[:80],
                status,
                str(error or "")[:2000],
                now,
                now,
            ),
        )
        return True


def _update_auto_reply_history(
    account_id: str,
    peer_id: str,
    inbound: Dict[str, Any],
    *,
    reply: str = "",
    category: str = "",
    status: str,
    error: str = "",
) -> None:
    inbound_id = str(inbound.get("auto_reply_inbound_id") or inbound.get("provider_message_id") or inbound.get("id") or "")
    if not inbound_id:
        inbound_id = _stable_key(peer_id, inbound.get("content"), inbound.get("created_at"))
    with _connect() as conn:
        conn.execute(
            """
            update wechat_auto_reply_history
            set reply_content=?, category=?, status=?, error_message=?, updated_at=?
            where account_id=? and peer_id=? and inbound_message_id=?
            """,
            (
                str(reply or "")[:4000],
                str(category or "")[:80],
                status,
                str(error or "")[:2000],
                _now_iso(),
                account_id,
                peer_id,
                inbound_id,
            ),
        )


def _mark_auto_reply_group_invite_failed(account_id: str, peer_id: str, inbound_id: str, error: str) -> None:
    inbound_id = str(inbound_id or "").strip()
    peer_id = str(peer_id or "").strip()
    if not inbound_id or not peer_id:
        return
    with _connect() as conn:
        conn.execute(
            """
            update wechat_auto_reply_history
            set status='failed', error_message=?, updated_at=?
            where account_id=? and peer_id=? and inbound_message_id=?
            """,
            (f"拉群失败：{str(error or '')[:1800]}", _now_iso(), account_id, peer_id, inbound_id),
        )


def _latest_failed_group_invite_candidate(
    account_id: str,
    peer_id: str,
    primary_contact: str,
    *,
    retry_cooldown_seconds: int = 300,
) -> Optional[Dict[str, Any]]:
    primary_contact = str(primary_contact or "").strip()
    peer_id = str(peer_id or "").strip()
    if not primary_contact or not peer_id or _has_verified_group_invite(account_id, peer_id, primary_contact):
        return None
    peer_key = _compact_for_contains(peer_id).lower()
    with _connect() as conn:
        rows = conn.execute(
            """
            select id, targets, payload, error_message, updated_at
            from wechat_tasks
            where account_id=? and task_type='create_group' and status='failed'
            order by updated_at desc
            limit 20
            """,
            (account_id,),
        ).fetchall()
        for row in rows:
            payload = _safe_json_loads(str(row["payload"] or ""), {})
            source_peer_id = str((payload or {}).get("source_peer_id") or "").strip()
            if not isinstance(payload, dict) or _compact_for_contains(source_peer_id).lower() != peer_key:
                continue
            inbound_id = str(payload.get("source_inbound_message_id") or "").strip()
            if not inbound_id:
                continue
            targets = [
                str(item or "").strip()
                for item in _safe_json_loads(str(row["targets"] or ""), [])
                if str(item or "").strip()
            ]
            task_primary = next((item for item in targets if item != peer_id), "")
            updated_at = _parse_iso_datetime(row["updated_at"])
            if task_primary == primary_contact and updated_at:
                if (datetime.utcnow() - updated_at).total_seconds() < retry_cooldown_seconds:
                    return None
            hist = conn.execute(
                """
                select inbound_content, created_at
                from wechat_auto_reply_history
                where account_id=? and peer_id=? and inbound_message_id=?
                limit 1
                """,
                (account_id, source_peer_id or peer_id, inbound_id),
            ).fetchone()
            content = str(hist["inbound_content"] if hist else "").strip()
            if not content:
                continue
            return {
                "peer_id": source_peer_id or peer_id,
                "direction": "in",
                "msg_type": "text",
                "content": content,
                "provider_message_id": inbound_id,
                "auto_reply_inbound_id": inbound_id,
                "created_at": str(hist["created_at"] if hist else row["updated_at"]),
                "group_invite_retry": True,
                "previous_group_invite_error": str(row["error_message"] or "")[:500],
                "previous_group_invite_task_id": str(row["id"] or ""),
                "previous_group_invite_primary_contact": task_primary,
            }
    return None


def _recent_conversation_text(account_id: str, peer_id: str, *, limit: int = 8) -> str:
    rows = list_messages(account_id, peer_id, limit=limit, offset=0).get("items") or []
    lines: List[str] = []
    for item in reversed(rows):
        if item.get("is_system") or item.get("msg_type") == "time":
            continue
        who = "我" if item.get("direction") == "out" else "对方"
        text = str(item.get("content") or "").strip()
        if text:
            lines.append(f"{who}: {text}")
    return "\n".join(lines[-limit:])


_AUTO_REPLY_MEMORY_PRIORITY_TERMS: tuple[tuple[str, int], ...] = (
    ("product_service_faq", 20),
    ("百问百答", 18),
    ("faq", 16),
    ("问答", 14),
    ("产品", 10),
    ("服务", 10),
    ("业务", 9),
    ("公司", 8),
    ("品牌", 8),
    ("价格", 8),
    ("报价", 8),
    ("售后", 8),
    ("客服", 8),
    ("话术", 8),
    ("人设", 7),
    ("规则", 7),
    ("policy", 7),
    ("profile", 6),
)


def _auto_reply_memory_score(doc: Dict[str, Any]) -> int:
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    haystack = " ".join(
        str(value or "")
        for value in (
            doc.get("title"),
            doc.get("filename"),
            doc.get("notes"),
            doc.get("origin"),
            meta.get("document_type"),
            meta.get("document_label"),
            meta.get("memory_layer"),
        )
    ).lower()
    return 1 + sum(weight for term, weight in _AUTO_REPLY_MEMORY_PRIORITY_TERMS if term.lower() in haystack)


def _load_auto_reply_memory_context(
    user_id: Optional[int],
    *,
    max_chars: int = 18000,
    max_docs: int = 8,
    selected_doc_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    empty = {"text": "", "document_count": 0, "titles": []}
    if not user_id:
        return empty
    try:
        from ..api.openclaw_memory import _load_index, _read_canonical_memory_content  # type: ignore
    except Exception:
        return empty
    docs = _load_index(int(user_id))
    selected = list(dict.fromkeys(str(item or "").strip() for item in (selected_doc_ids or []) if str(item or "").strip()))
    selected_set = set(selected)
    scored: List[tuple[int, int, Dict[str, Any]]] = []
    for index, doc in enumerate(docs):
        if not isinstance(doc, dict):
            continue
        doc_id = str(doc.get("id") or doc.get("doc_id") or "").strip()
        if selected_set and doc_id not in selected_set:
            continue
        status = str(doc.get("status") or "active").strip().lower()
        if status not in {"", "active", "enabled", "ready"}:
            continue
        selected_rank = len(selected) - selected.index(doc_id) if selected_set and doc_id in selected_set else 0
        scored.append((selected_rank * 1000 + _auto_reply_memory_score(doc), -index, doc))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    parts: List[str] = []
    titles: List[str] = []
    used = 0
    for _score, _index, doc in scored[: max(1, int(max_docs or 1))]:
        title = str(doc.get("title") or doc.get("filename") or "个人记忆").strip()
        remaining = max_chars - used
        if remaining <= 0:
            break
        text = _read_canonical_memory_content(doc, max_chars=min(5000, remaining))
        if not text:
            continue
        block = f"## {title}\n{text.strip()}"
        parts.append(block)
        titles.append(title[:120])
        used += len(block)
        if used >= max_chars:
            break
    context = "\n\n---\n\n".join(parts).strip()[:max_chars]
    return {"text": context, "document_count": len(titles), "titles": titles}


def _load_faq_memory_context(user_id: Optional[int], *, max_chars: int = 12000) -> str:
    """Backward-compatible text accessor for callers that only need memory text."""
    return str(_load_auto_reply_memory_context(user_id, max_chars=max_chars).get("text") or "")


async def _call_auto_reply_llm(
    *,
    auth_context: Optional[Dict[str, Any]],
    user_id: Optional[int],
    peer_name: str,
    latest_message: str,
    recent_context: str,
    memory_context: str = "",
    group_invite_rule_context: str = "",
    group_invite_keywords: str = "",
    group_invite_enabled: bool = True,
    group_invite_already_verified: bool = False,
    contact_intelligence: str = "",
    strategy_context: str = "",
) -> Dict[str, Any]:
    auth_context = auth_context or {}
    token = str(auth_context.get("token") or getattr(settings, "openclaw_sutui_fallback_jwt", None) or "").strip()
    if not token:
        raise RuntimeError("missing login token for AI auto reply")
    installation_id = str(
        auth_context.get("installation_id")
        or getattr(settings, "openclaw_sutui_fallback_installation_id", None)
        or f"native-wechat-auto-reply-{int(user_id or 0)}"
    )
    model = (
        getattr(settings, "lobster_orchestration_sutui_chat_model", None)
        or getattr(settings, "lobster_default_sutui_chat_model", None)
        or "deepseek-chat"
    )
    invite_keywords = list(
        dict.fromkeys(
            item.strip()
            for item in re.split(r"[,，;；\n]+", str(group_invite_keywords or ""))
            if item.strip()
        )
    )[:50]
    invite_rule_context = str(group_invite_rule_context or "").strip()[:8000]
    has_invite_rule = bool(group_invite_enabled and (invite_rule_context or invite_keywords))
    system_prompt = (
        "你是个人微信私聊代回复助手，只处理一对一私聊，不回复群聊。"
        "回复要像真人微信聊天：短、自然、有边界，不营销、不硬广、不夸大。"
        "如果对方只是闲聊，就自然接话；如果对方问业务、产品、价格、流程、合作或售后等专业问题，必须优先依据提供的个人记忆资料。"
        "有效文字消息必须回复；如果资料里没有答案，不能编造，要自然说明需要确认，或请对方补充必要信息。"
        "如果提供了拉群判断规则文件，你还要结合最新消息、最近上下文和规则文件做语义判断；"
        "只有客户真实诉求明确符合文件规则时 should_invite_group 才能为 true，不能只因出现相近字词就判定。没有提供规则时必须为 false。"
        "群聊状态只能以系统提供的核验结果为准；历史摘要、历史回复或客户画像中的‘已拉群/已在群里’都不能作为事实。"
        "如果系统标记为未核验，回复中禁止声称客户已经在群里；符合拉群规则时必须把 should_invite_group 设为 true。"
        "如果最近上下文中我方已经明确提出帮客户对接负责人、拉群或进入服务群，而客户回复可以、好的、同意、没问题等肯定答复，"
        "应结合此前已表达的业务兴趣判定为明确同意对接；符合规则时 should_invite_group 必须为 true，回复中不要再重复追问已经问过的问题。"
        "要结合客户历史画像延续上下文，不能重复问已经确认的信息，也不能在对方没有继续回复时自说自话。"
        "conversation_summary 必须在旧摘要基础上更新为累计摘要，保留仍有效的需求、已确认事实、异议、承诺和下一步，不能只总结最后一句。"
        "同时从本轮聊天提取该客户自己的稳定事实、需求、预算、时间、异议、偏好和关系阶段；不要把推测写成事实。"
        "只有发现可跨客户复用、且有明确聊天证据的改进方法时才提出 learning_candidates；客户单方面声称的价格、产品事实或承诺不能学成全局规则。"
        "必须返回 JSON：{\"should_reply\":true,\"category\":\"casual|product|price|service|cooperation|complaint|other\","
        "\"intent_level\":\"high|medium|low|none\",\"topic\":\"简短话题\","
        "\"conversation_summary\":\"结合历史画像更新后的累计摘要\",\"reply\":\"实际微信回复\","
        "\"stage\":\"unknown|new|warming|qualified|proposal|won|lost|service\","
        "\"next_followup\":\"下一步建议，没有则空\","
        "\"customer_profile\":{\"company\":\"\",\"role\":\"\",\"industry\":\"\",\"region\":\"\",\"needs\":[],\"budget\":\"\",\"timeline\":\"\",\"objections\":[],\"preferences\":[],\"interests\":[],\"products\":[],\"relationship\":\"\",\"notes\":\"\",\"tags\":[]},"
        "\"learning_candidates\":[{\"category\":\"tone|followup|service|general\",\"title\":\"\",\"content\":\"\",\"evidence\":\"\",\"confidence\":0,\"risk_level\":\"low|medium|high\"}],"
        "\"should_invite_group\":false,\"matched_group_keywords\":[],\"group_invite_reason\":\"判断依据\"}。"
    )
    user_prompt = (
        f"会话对象：{peer_name or '未命名'}\n\n"
        f"最近聊天记录：\n{recent_context or '(暂无)'}\n\n"
        f"对方最新消息：\n{latest_message}\n\n"
        f"该客户历史画像与摘要：\n{contact_intelligence or '(首次接触，暂无历史画像)'}\n\n"
        f"用户已审核生效的长期接管规则：\n{strategy_context or '(暂无额外长期规则)'}\n\n"
        f"已同步的个人记忆资料：\n{memory_context or '(没有读取到个人记忆，专业问题不要编造，需回复为待确认)'}\n\n"
        f"自动拉群开关：{'已开启' if group_invite_enabled else '已关闭'}\n\n"
        f"系统核验群状态：{'已核验客户在本系统创建的群聊中' if group_invite_already_verified else '未核验，禁止声称客户已在群里'}\n\n"
        f"拉群判断规则文件：\n{invite_rule_context or '(未配置，本轮不得判定拉群)'}\n\n"
        f"历史拉群关键词（仅兼容旧设置）：\n{('、'.join(invite_keywords)) if invite_keywords else '(无)'}"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "temperature": 0.35,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "X-Installation-Id": installation_id,
    }
    async with httpx.AsyncClient(timeout=90.0, trust_env=False) as client:
        resp = await client.post(f"{_server_proxy_base()}/api/sutui-chat/completions", json=payload, headers=headers)
    if resp.status_code >= 400:
        raise RuntimeError(f"sutui-chat HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    data = resp.json() if resp.content else {}
    try:
        content = str(data["choices"][0]["message"]["content"] or "")
    except Exception:
        content = json.dumps(data, ensure_ascii=False)
    parsed = _json_from_text(content)
    is_structured = bool(parsed)
    reply = str(parsed.get("reply") or parsed.get("content") or "").strip() if is_structured else ""
    should_reply = _bool_value(parsed.get("should_reply"), default=bool(reply)) if is_structured else True
    if not is_structured and content.strip():
        reply = re.sub(r"^```.*?```$", "", content.strip(), flags=re.S).strip()
    max_chars = int(DEFAULT_STRATEGY["auto_reply_max_text_chars"])
    reply = reply[:max_chars].strip()
    if _looks_like_auto_reply_control_payload(reply):
        raise RuntimeError("AI返回了内部判断数据，已阻止发送")
    if should_reply and not reply:
        raise RuntimeError("AI未生成可发送的回复")
    if not should_reply:
        reply = ""
    category = str(parsed.get("category") or "other").strip().lower()
    if category not in {"casual", "product", "price", "service", "cooperation", "complaint", "other"}:
        category = "other"
    intent_level = str(parsed.get("intent_level") or "none").strip().lower()
    if intent_level not in {"high", "medium", "low", "none"}:
        intent_level = "none"
    should_invite_group = _bool_value(parsed.get("should_invite_group"))
    if not group_invite_enabled or not has_invite_rule:
        should_invite_group = False
    elif group_invite_already_verified:
        should_invite_group = False
    elif _is_affirmative_group_confirmation(latest_message, recent_context):
        should_invite_group = True
    elif _reply_claims_existing_group(reply):
        should_invite_group = True
    matched_raw = parsed.get("matched_group_keywords")
    if isinstance(matched_raw, str):
        matched_values = re.split(r"[,，;；\n]+", matched_raw)
    elif isinstance(matched_raw, list):
        matched_values = matched_raw
    else:
        matched_values = []
    matched_group_keywords = list(
        dict.fromkeys(str(item or "").strip() for item in matched_values if str(item or "").strip())
    )[:20]
    group_invite_reason = str(parsed.get("group_invite_reason") or "").strip()[:300]
    if should_invite_group and _reply_claims_existing_group(reply) and not group_invite_already_verified:
        group_invite_reason = group_invite_reason or "AI 回复涉及已入群状态，但系统尚未核验，必须先实际执行拉群"
    if should_invite_group and _is_affirmative_group_confirmation(latest_message, recent_context):
        if "明确同意拉群" not in matched_group_keywords:
            matched_group_keywords.append("明确同意拉群")
        group_invite_reason = group_invite_reason or "客户对我方此前提出的建群或拉群邀请作出明确肯定答复"
    profile_raw = parsed.get("customer_profile") if isinstance(parsed.get("customer_profile"), dict) else {}
    profile_updates: Dict[str, Any] = {}
    allowed_profile_fields = {
        "company", "role", "industry", "region", "needs", "budget", "timeline", "objections",
        "preferences", "interests", "products", "relationship", "notes", "tags",
    }
    for key, value in profile_raw.items():
        if key not in allowed_profile_fields:
            continue
        if isinstance(value, list):
            cleaned = list(dict.fromkeys(str(item or "").strip()[:160] for item in value if str(item or "").strip()))[:20]
            if cleaned:
                profile_updates[key] = cleaned
        elif isinstance(value, (str, int, float, bool)) and str(value or "").strip():
            profile_updates[key] = str(value).strip()[:1000]
    stage = str(parsed.get("stage") or "").strip().lower()
    if stage not in {"unknown", "new", "warming", "qualified", "proposal", "won", "lost", "service"}:
        stage = ""
    candidates_raw = parsed.get("learning_candidates") if isinstance(parsed.get("learning_candidates"), list) else []
    learning_candidates: List[Dict[str, Any]] = []
    for item in candidates_raw[:8]:
        if not isinstance(item, dict):
            continue
        candidate_content = str(item.get("content") or "").strip()[:4000]
        if not candidate_content:
            continue
        candidate_category = str(item.get("category") or "general").strip().lower()
        if candidate_category not in {"general", "fact", "tone", "product", "price", "service", "commitment", "forbidden", "group_rule", "followup"}:
            candidate_category = "general"
        candidate_risk = str(item.get("risk_level") or "medium").strip().lower()
        if candidate_risk not in {"low", "medium", "high"}:
            candidate_risk = "medium"
        try:
            confidence = max(0, min(100, int(item.get("confidence") or 0)))
        except (TypeError, ValueError):
            confidence = 0
        learning_candidates.append(
            {
                "category": candidate_category,
                "title": str(item.get("title") or "聊天中发现的新规则").strip()[:200],
                "content": candidate_content,
                "evidence": str(item.get("evidence") or "").strip()[:4000],
                "confidence": confidence,
                "risk_level": candidate_risk,
            }
        )
    return {
        "should_reply": should_reply,
        "category": category,
        "intent_level": intent_level,
        "topic": str(parsed.get("topic") or "").strip()[:80],
        "conversation_summary": str(parsed.get("conversation_summary") or parsed.get("summary") or "").strip()[:200],
        "reply": reply,
        "should_invite_group": should_invite_group,
        "matched_group_keywords": matched_group_keywords,
        "group_invite_reason": group_invite_reason,
        "stage": stage,
        "next_followup": str(parsed.get("next_followup") or "").strip()[:2000],
        "profile_updates": profile_updates,
        "learning_candidates": learning_candidates,
        "raw": content[:2000],
    }


def _auto_reply_report_line(value: Any, max_chars: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_chars]


async def _queue_auto_reply_group_invite(
    account_id: str,
    peer_id: str,
    inbound: Dict[str, Any],
    llm_reply: Dict[str, Any],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    if not llm_reply.get("should_invite_group"):
        return {"ok": True, "skipped": True, "reason": "not_matched"}
    configured_primary_contact = str(
        cfg.get("group_invite_primary_contact")
        or next((item for item in (cfg.get("group_invite_contacts") or []) if str(item or "").strip()), "")
    ).strip()
    if not configured_primary_contact:
        return {"ok": True, "skipped": True, "reason": "missing_primary_contact"}
    if configured_primary_contact == str(peer_id or "").strip():
        return {"ok": True, "skipped": True, "reason": "primary_contact_is_customer"}
    primary_contact = _resolve_local_contact_wx_no(account_id, configured_primary_contact)
    if not primary_contact:
        return {
            "ok": True,
            "skipped": True,
            "reason": "primary_contact_wx_no_missing",
            "configured_primary_contact": configured_primary_contact,
        }
    inbound_id = str(
        inbound.get("auto_reply_inbound_id")
        or inbound.get("provider_message_id")
        or inbound.get("id")
        or _stable_key(peer_id, inbound.get("content"), inbound.get("created_at"))
    ).strip()
    # The open private chat already identifies the customer. Only the configured
    # companion's WeChat ID is searched in the add-member picker.
    dedup_key = "auto-invite-v4-" + _stable_key(account_id, peer_id, primary_contact)
    task = await create_group_task(
        account_id,
        [peer_id, primary_contact],
        welcome_message=str(cfg.get("group_invite_welcome_message") or "").strip(),
        dedup_key=dedup_key,
        source_peer_id=peer_id,
        source_inbound_message_id=inbound_id,
        group_invite_reason=str(llm_reply.get("group_invite_reason") or "").strip(),
        matched_group_keywords=list(llm_reply.get("matched_group_keywords") or []),
        customer_wx_no="",
        use_current_chat=True,
        execute_now=True,
    )
    status = str(task.get("status") or "").strip().lower()
    task_error = str(task.get("error_message") or "").strip()
    completed = status in {"success", "partial_failed"} and bool(
        (task.get("payload") or {}).get("group_verified") if isinstance(task.get("payload"), dict) else False
    )
    return {
        "ok": status != "failed",
        "queued": status in {"pending", "running"},
        "completed": completed,
        "deduped": bool(task.get("deduped")),
        "dedup_key": dedup_key,
        "primary_contact": primary_contact,
        "primary_contact_name": str(cfg.get("group_invite_primary_contact_name") or configured_primary_contact).strip(),
        "task_id": str(task.get("id") or ""),
        "task_status": status,
        "error": task_error,
        "already_grouped": bool(task.get("deduped") and status in {"success", "partial_failed"}),
        "customer_wx_no": "",
    }


def _build_auto_reply_report(result: Dict[str, Any], memory: Dict[str, Any]) -> Dict[str, Any]:
    items = [item for item in (result.get("items") or []) if isinstance(item, dict)]
    category_labels = {
        "casual": "闲聊",
        "product": "产品咨询",
        "price": "价格咨询",
        "service": "服务/售后",
        "cooperation": "合作意向",
        "complaint": "投诉/风险",
        "other": "其他",
    }
    intent_labels = {"high": "高意向", "medium": "中意向", "low": "低意向", "none": "未判定"}
    status_labels = {
        "sent": "已回复",
        "llm_skipped": "未回复",
        "no_unreplied_message": "无待回复消息",
        "duplicate": "已处理过",
        "group_invite_queued": "拉群已排队",
        "group_invite_completed": "拉群已完成",
        "group_invite_deduped": "拉群已去重",
        "group_invite_not_executable": "拉群配置需调整",
        "failed": "回复失败",
        "skipped_group": "群聊已跳过",
        "skipped": "已跳过",
    }
    category_counts: Dict[str, int] = {}
    topic_counts: Dict[str, int] = {}
    high_intent_count = 0
    group_invite_match_count = 0
    for item in items:
        category = str(item.get("category") or "").strip().lower()
        if category:
            category_counts[category] = category_counts.get(category, 0) + 1
        topic = _auto_reply_report_line(item.get("topic"), 80)
        if topic:
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
        if str(item.get("intent_level") or "").strip().lower() == "high":
            high_intent_count += 1
        if item.get("should_invite_group"):
            group_invite_match_count += 1

    memory_titles = [_auto_reply_report_line(title, 80) for title in (memory.get("titles") or []) if _auto_reply_report_line(title, 80)]
    report = {
        "started_at": result.get("started_at") or result.get("checked_at"),
        "finished_at": result.get("finished_at"),
        "duration_seconds": result.get("duration_seconds") or 0,
        "session_count": int(result.get("session_count") or 0),
        "private_session_count": int(result.get("unread_private_count") or 0),
        "unread_message_count": int(result.get("unread_message_count") or 0),
        "replied": int(result.get("replied") or 0),
        "skipped": int(result.get("skipped") or 0),
        "failed": int(result.get("failed") or 0),
        "skipped_groups": int(result.get("skipped_groups") or 0),
        "friend_requests_checked": int(result.get("friend_requests_checked") or 0),
        "friend_requests_accepted": int(result.get("friend_requests_accepted") or 0),
        "friend_requests_failed": int(result.get("friend_requests_failed") or 0),
        "high_intent_count": high_intent_count,
        "group_invite_match_count": group_invite_match_count,
        "category_counts": category_counts,
        "topic_counts": topic_counts,
        "memory_document_count": int(memory.get("document_count") or 0),
        "memory_titles": memory_titles,
        "conversations": items,
    }
    lines = [
        "个微私信自动接管汇总",
        f"统计时间：{report['started_at'] or '-'} 至 {report['finished_at'] or '-'}",
        "",
        f"- 扫描会话：{report['session_count']} 个",
        f"- 有新消息的个人会话：{report['private_session_count']} 个",
        f"- 未读消息：{report['unread_message_count']} 条",
        f"- 已自动回复：{report['replied']} 个会话",
        f"- 新好友申请：检查 {report['friend_requests_checked']} 条，已同意 {report['friend_requests_accepted']} 条，失败 {report['friend_requests_failed']} 条",
        f"- 高意向会话：{report['high_intent_count']} 个",
        f"- 命中拉群条件：{report['group_invite_match_count']} 个",
        f"- 跳过：{report['skipped']} 个；群聊/公众号排除：{report['skipped_groups']} 个；失败：{report['failed']} 个",
    ]
    if memory_titles:
        lines.append(f"- 回复依据：个人记忆 {report['memory_document_count']} 份（{'、'.join(memory_titles[:5])}）")
    else:
        lines.append("- 回复依据：未读取到个人记忆；专业问题已按不编造、待确认处理")
    if category_counts:
        category_text = "、".join(
            f"{category_labels.get(key, key)} {count} 个"
            for key, count in sorted(category_counts.items(), key=lambda pair: pair[1], reverse=True)
        )
        lines.append(f"- 咨询类型：{category_text}")
    if topic_counts:
        topic_text = "、".join(
            f"{topic} {count} 个"
            for topic, count in sorted(topic_counts.items(), key=lambda pair: pair[1], reverse=True)[:6]
        )
        lines.append(f"- 主要话题：{topic_text}")
    if items:
        lines.extend(["", "会话处理明细"])
        for index, item in enumerate(items, start=1):
            name = _auto_reply_report_line(item.get("display_name") or item.get("peer_id") or "未命名会话", 80)
            status = status_labels.get(str(item.get("status") or ""), str(item.get("status") or "已处理"))
            category = category_labels.get(str(item.get("category") or "other"), "其他")
            intent = intent_labels.get(str(item.get("intent_level") or "none"), "未判定")
            topic = _auto_reply_report_line(item.get("topic"), 80)
            lines.append(f"{index}. {name}｜{status}｜{category}｜{intent}" + (f"｜{topic}" if topic else ""))
            conversation_summary = _auto_reply_report_line(item.get("conversation_summary"), 200)
            inbound = _auto_reply_report_line(item.get("inbound_preview"), 240)
            reply = _auto_reply_report_line(item.get("reply_preview"), 240)
            if conversation_summary:
                lines.append(f"   诉求：{conversation_summary}")
            if inbound:
                lines.append(f"   收到：{inbound}")
            if reply:
                lines.append(f"   回复：{reply}")
            elif item.get("error"):
                lines.append(f"   原因：{_auto_reply_report_line(item.get('error'), 200)}")
            if item.get("should_invite_group"):
                matched = "、".join(str(value) for value in (item.get("matched_group_keywords") or []) if str(value).strip())
                reason = _auto_reply_report_line(item.get("group_invite_reason"), 200)
                lines.append(f"   拉群判断：符合{f'（{matched}）' if matched else ''}{f'；{reason}' if reason else ''}")
                welcome = _auto_reply_report_line(item.get("group_invite_welcome_message"), 200)
                if welcome:
                    lines.append(f"   建群欢迎话术：{welcome}")
    report["summary_text"] = "\n".join(lines)
    return report


def _session_needs_auto_reply_check(item: Dict[str, Any]) -> bool:
    try:
        if int(item.get("unread_count") or 0) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    return not _looks_like_non_replyable_wechat_event(item.get("last_content"))


def _session_is_scan_candidate(item: Dict[str, Any]) -> bool:
    """A takeover scan opens the chat and checks its actual last message."""
    if not str(item.get("peer_id") or "").strip():
        return False
    if item.get("is_system") or str(item.get("msg_type") or "").lower() == "time":
        return False
    return not _looks_like_non_replyable_wechat_event(item.get("last_content"))


async def run_auto_reply_once(
    account_id: str,
    *,
    auth_context: Optional[Dict[str, Any]] = None,
    force: bool = True,
    trigger: str = "manual",
    check_friend_requests: bool = True,
    config_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    init_db()
    account_id = str(account_id or "").strip()
    if not account_id:
        raise RuntimeError("missing account_id")
    if auth_context:
        _AUTO_REPLY_AUTH_CONTEXT[account_id] = dict(auth_context)
    cfg = get_auto_reply_config(account_id)
    if isinstance(config_override, dict) and config_override:
        cfg = {**cfg, **config_override}
        cfg["group_invite_enabled"] = bool(cfg.get("group_invite_enabled"))
        cfg["group_invite_contacts"] = [
            str(item or "").strip() for item in (cfg.get("group_invite_contacts") or []) if str(item or "").strip()
        ][:20]
        if "group_invite_enabled" in config_override:
            cfg["group_invite_enabled"] = bool(config_override.get("group_invite_enabled"))
    effective_user_id = int(cfg.get("user_id") or (auth_context or {}).get("user_id") or 0) or None
    if not force and not cfg.get("enabled"):
        return {"ok": True, "skipped": True, "reason": "disabled", "config": cfg}
    if not _auto_reply_due(cfg, force=force):
        return {"ok": True, "skipped": True, "reason": "not_due", "config": cfg}
    if not _claim_auto_reply_run(account_id):
        return {"ok": True, "skipped": True, "reason": "running", "config": get_auto_reply_config(account_id)}
    started_monotonic = time.monotonic()
    started_at = _now_iso()
    memory = _load_auto_reply_memory_context(
        effective_user_id,
        max_chars=12000,
        max_docs=5,
        selected_doc_ids=cfg.get("memory_doc_ids") if isinstance(cfg.get("memory_doc_ids"), list) else [],
    )
    invite_rule_doc_id = str(cfg.get("group_invite_memory_doc_id") or "").strip()
    invite_rule_memory = (
        _load_auto_reply_memory_context(
            effective_user_id,
            max_chars=8000,
            max_docs=1,
            selected_doc_ids=[invite_rule_doc_id],
        )
        if invite_rule_doc_id
        else {"text": "", "document_count": 0, "titles": []}
    )
    group_invite_welcome_message = str(cfg.get("group_invite_welcome_message") or "").strip()
    result: Dict[str, Any] = {
        "ok": True,
        "trigger": trigger,
        "checked_at": started_at,
        "started_at": started_at,
        "session_count": 0,
        "unread_private_count": 0,
        "unread_message_count": 0,
        "processed": 0,
        "replied": 0,
        "skipped": 0,
        "skipped_groups": 0,
        "failed": 0,
        "items": [],
        "friend_requests_checked": 0,
        "friend_requests_accepted": 0,
        "friend_requests_failed": 0,
        "friend_requests": {
            "ok": True,
            "skipped": not check_friend_requests,
            "reason": "session_initial_check_completed" if not check_friend_requests else "",
        },
        "friend_requests_checked_this_run": bool(check_friend_requests),
        "group_invite_queued": 0,
        "group_invite_completed": 0,
        "group_invite_deduped": 0,
        "group_invite_skipped": 0,
        "group_invite_failed": 0,
        "driver_recovered": False,
        "driver_retry_count": 0,
        "driver_recoveries": [],
        "stop_reason": "",
        "memory": {
            "document_count": int(memory.get("document_count") or 0),
            "titles": list(memory.get("titles") or []),
        },
        "intelligence_outbox": {"processed": 0, "remaining": 0},
    }
    try:
        result["intelligence_outbox"] = await _flush_wechat_intelligence_outbox(
            _AUTO_REPLY_AUTH_CONTEXT.get(account_id) or auth_context,
            limit=5,
        )
        if check_friend_requests:
            try:
                friend_requests = await asyncio.to_thread(
                    accept_local_friend_requests,
                    account_id,
                    max_accepts=10,
                    max_scrolls=6,
                )
                result["friend_requests"] = friend_requests
                _collect_local_driver_recovery(result, friend_requests)
                result["friend_requests_checked"] = int(friend_requests.get("checked") or 0)
                result["friend_requests_accepted"] = int(friend_requests.get("accepted") or 0)
                result["friend_requests_failed"] = int(friend_requests.get("failed") or 0)
            except Exception as friend_exc:
                result["friend_requests"] = {"ok": False, "error": str(friend_exc)[:500]}
                result["friend_requests_failed"] = 1
        if _auto_reply_stop_requested(account_id):
            result["stop_reason"] = "cancelled"
        if result.get("stop_reason"):
            result["finished_at"] = _now_iso()
            result["duration_seconds"] = round(max(0.0, time.monotonic() - started_monotonic), 2)
            report = _build_auto_reply_report(result, memory)
            result["report"] = report
            result["summary_text"] = report.get("summary_text") or ""
            _finish_auto_reply_run(account_id, result)
            return {**result, "config": get_auto_reply_config(account_id)}
        session_data = await asyncio.to_thread(
            sync_local_sessions,
            account_id,
            passive=False,
            recent_only=True,
        )
        _collect_local_driver_recovery(result, session_data)
        sessions = _enrich_sessions_with_message_counts(account_id, list(session_data.get("items") or []))
        result["session_count"] = len(sessions)
        result["session_scan_rounds"] = int(session_data.get("scroll_rounds") or 0)
        result["session_scan_limit"] = 20
        unread = [item for item in sessions if _session_is_scan_candidate(item)]
        private_unread = []
        for item in unread:
            if _looks_like_group_session(item):
                result["skipped_groups"] += 1
                continue
            private_unread.append(item)
        result["unread_private_count"] = len(private_unread)
        result["unread_message_count"] = sum(
            max(0, int(item.get("unread_count") or 0))
            for item in private_unread
        )
        # The scan is intentionally bounded by 20 UIA scroll rounds, not by an
        # unrelated session-count cap. Process every eligible private session
        # collected by those rounds so later pages cannot be silently skipped.
        for idx, session in enumerate(private_unread):
            if _auto_reply_stop_requested(account_id):
                result["stop_reason"] = "cancelled"
                break
            peer_id = str(session.get("peer_id") or "").strip()
            display_name = str(session.get("display_name") or peer_id).strip()
            if not peer_id:
                result["skipped"] += 1
                continue
            item_result: Dict[str, Any] = {"peer_id": peer_id, "display_name": display_name, "status": "skipped"}
            current_inbound: Optional[Dict[str, Any]] = None
            current_reply = ""
            current_category = ""
            current_peer = peer_id
            llm_reply: Dict[str, Any] = {}
            intelligence_context: Dict[str, Any] = {}
            try:
                sync_result = await asyncio.to_thread(
                    sync_local_messages,
                    account_id,
                    peer_id,
                    load_more_pages=0,
                    select_via_uia=True,
                )
                _collect_local_driver_recovery(result, sync_result)
                chat_info = sync_result.get("chat_info") if isinstance(sync_result.get("chat_info"), dict) else {}
                if str((chat_info or {}).get("chat_type") or "").lower() in {"group", "chatroom", "official"}:
                    result["skipped_groups"] += 1
                    item_result.update({"status": "skipped_group", "chat_type": (chat_info or {}).get("chat_type")})
                    result["items"].append(item_result)
                    continue
                actual_peer = str(sync_result.get("peer_id") or peer_id)
                current_peer = actual_peer
                configured_primary_contact = str(
                    cfg.get("group_invite_primary_contact")
                    or next(
                        (value for value in (cfg.get("group_invite_contacts") or []) if str(value or "").strip()),
                        "",
                    )
                ).strip()
                primary_contact = _resolve_local_contact_wx_no(account_id, configured_primary_contact) or configured_primary_contact
                inbound = _latest_auto_reply_candidate(account_id, actual_peer)
                if not inbound and cfg.get("group_invite_enabled"):
                    inbound = _latest_failed_group_invite_candidate(account_id, actual_peer, primary_contact)
                    if inbound:
                        item_result.update(
                            {
                                "group_invite_retry": True,
                                "previous_group_invite_error": str(inbound.get("previous_group_invite_error") or "")[:240],
                                "previous_group_invite_task_id": str(inbound.get("previous_group_invite_task_id") or ""),
                                "previous_group_invite_primary_contact": str(
                                    inbound.get("previous_group_invite_primary_contact") or ""
                                ),
                            }
                        )
                if not inbound:
                    result["skipped"] += 1
                    item_result.update({"status": "no_unreplied_message"})
                    result["items"].append(item_result)
                    continue
                current_inbound = inbound
                item_result.update(
                    {
                        "inbound_message_id": str(
                            inbound.get("auto_reply_inbound_id")
                            or inbound.get("provider_message_id")
                            or inbound.get("id")
                            or ""
                        ),
                        "inbound_preview": str(inbound.get("content") or "").strip()[:240],
                        "message_time": str(inbound.get("created_at") or ""),
                    }
                )
                recent = _recent_conversation_text(account_id, actual_peer, limit=8)
                intelligence_context = await _load_wechat_intelligence_context(
                    _AUTO_REPLY_AUTH_CONTEXT.get(account_id) or auth_context,
                    account_id=account_id,
                    contact_key=actual_peer,
                    contact_name=display_name,
                    latest_message=str(inbound.get("content") or ""),
                )
                contact_intelligence, strategy_context = _wechat_intelligence_prompt_context(intelligence_context)
                local_group_invite_verified = _has_verified_group_invite(
                    account_id,
                    actual_peer,
                    primary_contact,
                )
                server_contact = intelligence_context.get("contact") if isinstance(intelligence_context, dict) else {}
                group_invite_already_verified = bool(
                    local_group_invite_verified
                    or (isinstance(server_contact, dict) and server_contact.get("group_invite_verified"))
                )
                if not group_invite_already_verified:
                    contact_intelligence = str(_strip_unverified_group_claims(contact_intelligence) or "")
                llm_reply = await _call_auto_reply_llm(
                    auth_context=_AUTO_REPLY_AUTH_CONTEXT.get(account_id) or auth_context,
                    user_id=effective_user_id,
                    peer_name=display_name,
                    latest_message=str(inbound.get("content") or ""),
                    recent_context=recent,
                    memory_context=str(memory.get("text") or ""),
                    group_invite_rule_context=str(invite_rule_memory.get("text") or ""),
                    group_invite_keywords=str(cfg.get("group_invite_keywords") or ""),
                    group_invite_enabled=bool(cfg.get("group_invite_enabled")),
                    group_invite_already_verified=group_invite_already_verified,
                    contact_intelligence=contact_intelligence,
                    strategy_context=strategy_context,
                )
                if not group_invite_already_verified:
                    llm_reply["conversation_summary"] = _strip_unverified_group_claims(
                        llm_reply.get("conversation_summary") or ""
                    )
                    llm_reply["profile_updates"] = _strip_unverified_group_claims(
                        llm_reply.get("profile_updates") or {}
                    )
                item_result.update(
                    {
                        "category": llm_reply.get("category"),
                        "intent_level": llm_reply.get("intent_level"),
                        "topic": llm_reply.get("topic"),
                        "conversation_summary": llm_reply.get("conversation_summary"),
                        "should_invite_group": bool(llm_reply.get("should_invite_group")),
                        "matched_group_keywords": llm_reply.get("matched_group_keywords") or [],
                        "group_invite_reason": llm_reply.get("group_invite_reason") or "",
                        "stage": llm_reply.get("stage") or "",
                        "next_followup": llm_reply.get("next_followup") or "",
                        "profile_update_fields": sorted((llm_reply.get("profile_updates") or {}).keys()),
                        "learning_candidate_count": len(llm_reply.get("learning_candidates") or []),
                        "intelligence_context_available": bool(intelligence_context.get("available")),
                    }
                )
                group_invite: Optional[Dict[str, Any]] = None
                if llm_reply.get("should_invite_group"):
                    try:
                        group_invite = await _queue_auto_reply_group_invite(
                            account_id,
                            actual_peer,
                            inbound,
                            llm_reply,
                            cfg,
                        )
                        item_result["group_invite"] = group_invite
                        item_result["group_invite_dedup_key"] = str(group_invite.get("dedup_key") or "")
                        item_result["customer_wx_no"] = str(group_invite.get("customer_wx_no") or "")
                        if group_invite.get("queued"):
                            result["group_invite_queued"] += 1
                        elif group_invite.get("completed"):
                            result["group_invite_completed"] += 1
                        elif group_invite.get("deduped"):
                            result["group_invite_deduped"] += 1
                        elif group_invite.get("skipped"):
                            result["group_invite_skipped"] += 1
                    except Exception as group_exc:
                        result["group_invite_failed"] += 1
                        group_invite = {"ok": False, "error": str(group_exc)[:500]}
                        item_result["group_invite"] = group_invite

                if llm_reply.get("should_invite_group") and group_invite and group_invite.get("already_grouped"):
                    llm_reply["should_invite_group"] = False
                    item_result["should_invite_group"] = False
                    item_result["group_invite_already_verified"] = True

                if llm_reply.get("should_invite_group"):
                    invite_ok = bool(
                        group_invite
                        and (group_invite.get("queued") or group_invite.get("completed") or group_invite.get("deduped"))
                    )
                    if not invite_ok:
                        invite_reason = str((group_invite or {}).get("reason") or (group_invite or {}).get("error") or "unknown")
                        invite_error_labels = {
                            "missing_primary_contact": "未设置拉群主联系人",
                            "primary_contact_wx_no_missing": "预设拉群联系人没有微信号，请先同步通讯录",
                            "primary_contact_is_customer": "拉群主联系人不能与当前客户相同",
                        }
                        invite_error = invite_error_labels.get(invite_reason, f"拉群任务未能创建：{invite_reason}")
                        _record_auto_reply_history(
                            account_id,
                            actual_peer,
                            inbound,
                            reply="",
                            category=str(llm_reply.get("category") or ""),
                            status="skipped",
                            error=invite_error,
                        )
                        result["skipped"] += 1
                        item_result.update(
                            {
                                "status": "group_invite_not_executable",
                                "reply_suppressed": True,
                                "error": invite_error,
                            }
                        )
                        result["items"].append(item_result)
                        continue
                    _record_auto_reply_history(
                        account_id,
                        actual_peer,
                        inbound,
                        reply="",
                        category=str(llm_reply.get("category") or ""),
                        status="skipped",
                        error="已进入拉群流程，抑制普通回复",
                    )
                    item_result.update(
                        {
                            "status": (
                                "group_invite_completed"
                                if group_invite and group_invite.get("completed")
                                else "group_invite_queued"
                                if group_invite and group_invite.get("queued")
                                else "group_invite_deduped"
                            ),
                            "reply_suppressed": True,
                            "group_invite_initiated": True,
                            "group_invite_welcome_message": group_invite_welcome_message,
                        }
                    )
                    result["items"].append(item_result)
                    continue
                if not llm_reply.get("should_reply"):
                    _record_auto_reply_history(
                        account_id,
                        actual_peer,
                        inbound,
                        reply="",
                        category=str(llm_reply.get("category") or ""),
                        status="skipped",
                    )
                    result["skipped"] += 1
                    item_result.update({"status": "llm_skipped"})
                    result["items"].append(item_result)
                    continue
                reply_text = str(llm_reply.get("reply") or "").strip()
                if _looks_like_auto_reply_control_payload(reply_text):
                    raise RuntimeError("内部判断数据禁止作为微信回复发送")
                current_reply = reply_text
                current_category = str(llm_reply.get("category") or "")
                if not _record_auto_reply_history(
                    account_id,
                    actual_peer,
                    inbound,
                    reply=reply_text,
                    category=current_category,
                    status="sending",
                ):
                    result["skipped"] += 1
                    item_result.update({"status": "duplicate"})
                    result["items"].append(item_result)
                    continue
                await asyncio.to_thread(
                    _send_text_local_slow,
                    account_id,
                    actual_peer,
                    reply_text,
                    {"driver": "native_wechat_auto_reply", "trigger": trigger, "category": llm_reply.get("category")},
                    True,
                )
                _update_auto_reply_history(
                    account_id,
                    actual_peer,
                    inbound,
                    reply=reply_text,
                    category=current_category,
                    status="sent",
                )
                result["processed"] += 1
                result["replied"] += 1
                item_result.update({"status": "sent", "reply_preview": reply_text[:240]})
                result["items"].append(item_result)
            except Exception as exc:
                if current_inbound is not None:
                    _update_auto_reply_history(
                        account_id,
                        str(current_inbound.get("peer_id") or peer_id),
                        current_inbound,
                        reply=current_reply,
                        category=current_category,
                        status="failed",
                        error=str(exc),
                    )
                result["failed"] += 1
                item_result.update({"status": "failed", "error": str(exc)[:300]})
                result["items"].append(item_result)
            finally:
                if current_inbound is not None:
                    item_status = str(item_result.get("status") or "skipped")
                    event_type = "reply_sent" if item_status == "sent" else "reply_skipped"
                    outcome_status = "completed"
                    if item_status in {"failed", "group_invite_not_executable"}:
                        event_type = "failed"
                        outcome_status = "failed"
                    inbound_id = str(
                        current_inbound.get("auto_reply_inbound_id")
                        or current_inbound.get("provider_message_id")
                        or current_inbound.get("id")
                        or ""
                    )
                    sync_result = await _observe_wechat_intelligence(
                        _AUTO_REPLY_AUTH_CONTEXT.get(account_id) or auth_context,
                        {
                            "account_id": account_id,
                            "contact_key": current_peer,
                            "contact_name": display_name,
                            "event_type": event_type,
                            "status": outcome_status,
                            "inbound_message_id": inbound_id,
                            "inbound_text": str(current_inbound.get("content") or "")[:4000],
                            "reply_text": current_reply[:4000] if item_status == "sent" else "",
                            "category": str(llm_reply.get("category") or current_category)[:32],
                            "intent_level": str(llm_reply.get("intent_level") or "")[:16],
                            "topic": str(llm_reply.get("topic") or "")[:160],
                            "conversation_summary": str(llm_reply.get("conversation_summary") or "")[:2000],
                            "stage": str(llm_reply.get("stage") or "")[:48],
                            "next_followup": str(llm_reply.get("next_followup") or "")[:2000],
                            "profile_updates": llm_reply.get("profile_updates") if isinstance(llm_reply.get("profile_updates"), dict) else {},
                            "learning_candidates": llm_reply.get("learning_candidates") if isinstance(llm_reply.get("learning_candidates"), list) else [],
                            "payload": {
                                "trigger": trigger,
                                "should_invite_group": bool(llm_reply.get("should_invite_group")),
                                "group_invite": item_result.get("group_invite") if isinstance(item_result.get("group_invite"), dict) else {},
                            },
                            "error_message": str(item_result.get("error") or "")[:2000],
                        },
                    )
                    item_result["intelligence_sync"] = {
                        "ok": bool(sync_result.get("ok")),
                        "deduplicated": bool(sync_result.get("deduplicated")),
                        "candidate_count": len(sync_result.get("candidates") or []),
                        "error": str(sync_result.get("error") or "")[:200],
                    }
                    group_invite = item_result.get("group_invite") if isinstance(item_result.get("group_invite"), dict) else {}
                    if group_invite.get("queued"):
                        await _observe_wechat_intelligence(
                            _AUTO_REPLY_AUTH_CONTEXT.get(account_id) or auth_context,
                            {
                                "account_id": account_id,
                                "contact_key": current_peer,
                                "contact_name": display_name,
                                "event_type": "group_queued",
                                "status": "completed",
                                "inbound_message_id": inbound_id,
                                "category": str(llm_reply.get("category") or "")[:32],
                                "intent_level": str(llm_reply.get("intent_level") or "none")[:16],
                                "payload": {
                                    "dedup_key": str(group_invite.get("dedup_key") or "")[:255],
                                    "reason": str(llm_reply.get("group_invite_reason") or "")[:300],
                                },
                            },
                        )
            if idx < len(private_unread) - 1:
                low = float(DEFAULT_STRATEGY["auto_reply_session_sleep_min"])
                high = max(low, float(DEFAULT_STRATEGY["auto_reply_session_sleep_max"]))
                await asyncio.sleep(random.uniform(low, high))
        result["finished_at"] = _now_iso()
        result["duration_seconds"] = round(max(0.0, time.monotonic() - started_monotonic), 2)
        report = _build_auto_reply_report(result, memory)
        result["report"] = report
        result["summary_text"] = report.get("summary_text") or ""
        _finish_auto_reply_run(account_id, result)
        return {**result, "config": get_auto_reply_config(account_id)}
    except asyncio.CancelledError:
        result["ok"] = False
        result["stop_reason"] = "cancelled"
        result["error"] = "本轮接管已取消"
        result["finished_at"] = _now_iso()
        result["duration_seconds"] = round(max(0.0, time.monotonic() - started_monotonic), 2)
        try:
            _finish_auto_reply_run(account_id, result, error=result["error"])
        finally:
            raise
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)
        _finish_auto_reply_run(account_id, result, error=str(exc))
        raise


def _common_headers(*, token: str = "", body: str = "") -> Dict[str, str]:
    uin = base64.b64encode(str(secrets.randbits(32)).encode("utf-8")).decode("ascii")
    headers = {
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
        "X-WECHAT-UIN": uin,
    }
    if body:
        headers.update(
            {
                "Content-Type": "application/json",
                "AuthorizationType": "ilink_bot_token",
                "Content-Length": str(len(body.encode("utf-8"))),
            }
        )
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _api_get(base_url: str, endpoint: str, *, timeout_ms: int = 15000) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    timeout = max(1.0, timeout_ms / 1000)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await client.get(url, headers=_common_headers())
    text = resp.text
    if resp.status_code < 200 or resp.status_code >= 300:
        raise RuntimeError(f"GET {endpoint} {resp.status_code}: {text[:500]}")
    return _safe_json_loads(text, {"raw": text})


async def _api_post(
    base_url: str,
    endpoint: str,
    payload: Dict[str, Any],
    *,
    token: str = "",
    timeout_ms: int = 15000,
) -> Dict[str, Any]:
    body = _json_dumps(payload)
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    timeout = max(1.0, timeout_ms / 1000)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await client.post(url, content=body.encode("utf-8"), headers=_common_headers(token=token, body=body))
    text = resp.text
    if resp.status_code < 200 or resp.status_code >= 300:
        raise RuntimeError(f"POST {endpoint} {resp.status_code}: {text[:500]}")
    return _safe_json_loads(text, {"raw": text})


async def start_login(*, force: bool = False, session_key: str = "") -> Dict[str, Any]:
    session = session_key.strip() or str(uuid.uuid4())
    existing = _ACTIVE_LOGINS.get(session)
    if existing and not force and time.time() - float(existing.get("started_at") or 0) < LOGIN_TTL_SECONDS:
        return {"ok": True, "session_key": session, "qrcode_url": existing.get("qrcode_url"), "message": "二维码已生成"}

    data = await _api_get(
        DEFAULT_BASE_URL,
        f"ilink/bot/get_bot_qrcode?bot_type={DEFAULT_BOT_TYPE}",
        timeout_ms=int(DEFAULT_STRATEGY["api_timeout_ms"]),
    )
    qrcode = str(data.get("qrcode") or "")
    qrcode_url = str(data.get("qrcode_img_content") or "")
    if not qrcode or not qrcode_url:
        return {"ok": False, "session_key": session, "message": "获取微信二维码失败", "upstream": data}
    _ACTIVE_LOGINS[session] = {
        "session_key": session,
        "qrcode": qrcode,
        "qrcode_url": qrcode_url,
        "started_at": time.time(),
        "base_url": DEFAULT_BASE_URL,
    }
    return {"ok": True, "session_key": session, "qrcode_url": qrcode_url, "message": "请使用微信扫码连接"}


async def wait_login(*, session_key: str, timeout_seconds: int = 480) -> Dict[str, Any]:
    login = _ACTIVE_LOGINS.get(session_key)
    if not login:
        return {"ok": False, "connected": False, "message": "登录会话不存在，请重新生成二维码"}
    if time.time() - float(login.get("started_at") or 0) > LOGIN_TTL_SECONDS:
        _ACTIVE_LOGINS.pop(session_key, None)
        return {"ok": False, "connected": False, "message": "二维码已过期，请重新生成"}

    deadline = time.time() + max(1, min(timeout_seconds, 480))
    current_base = str(login.get("base_url") or DEFAULT_BASE_URL)
    while time.time() < deadline:
        try:
            data = await _api_get(
                current_base,
                f"ilink/bot/get_qrcode_status?qrcode={login['qrcode']}",
                timeout_ms=int(DEFAULT_STRATEGY["poll_timeout_ms"]),
            )
        except Exception:
            await _sleep(1.5)
            continue
        status = str(data.get("status") or "")
        if status == "scaned_but_redirect" and data.get("redirect_host"):
            current_base = str(data.get("redirect_host")).strip() or current_base
            login["base_url"] = current_base
            continue
        if status == "confirmed" and data.get("bot_token") and data.get("ilink_bot_id"):
            account_id = _normalize_account_id(str(data.get("ilink_bot_id") or ""))
            base_url = str(data.get("baseurl") or current_base or DEFAULT_BASE_URL)
            _save_account(
                account_id,
                {
                    "token": str(data.get("bot_token") or ""),
                    "baseUrl": base_url,
                    "userId": str(data.get("ilink_user_id") or ""),
                },
            )
            _ACTIVE_LOGINS.pop(session_key, None)
            return {
                "ok": True,
                "connected": True,
                "account_id": account_id,
                "user_id": str(data.get("ilink_user_id") or ""),
                "message": "微信连接成功",
            }
        if status == "expired":
            _ACTIVE_LOGINS.pop(session_key, None)
            return {"ok": False, "connected": False, "message": "二维码已过期，请重新生成"}
    return {"ok": False, "connected": False, "message": "等待扫码超时"}


def _message_counts_by_peer(account_id: str, peer_ids: Optional[List[str]] = None) -> Dict[str, Dict[str, int]]:
    init_db()
    params: List[Any] = [account_id]
    where = "where account_id=?"
    peer_ids = [str(x).strip() for x in (peer_ids or []) if str(x).strip()]
    if peer_ids:
        where += " and peer_id in (" + ",".join("?" for _ in peer_ids) + ")"
        params.extend(peer_ids)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            select peer_id,
                   count(*) as message_count,
                   sum(case when direction='in' then 1 else 0 end) as inbound_message_count,
                   sum(case when direction='out' then 1 else 0 end) as outbound_message_count
            from wechat_messages
            {where}
              and direction != 'system'
              and msg_type != 'time'
            group by peer_id
            """,
            tuple(params),
        ).fetchall()
    return {
        str(row["peer_id"]): {
            "message_count": int(row["message_count"] or 0),
            "inbound_message_count": int(row["inbound_message_count"] or 0),
            "outbound_message_count": int(row["outbound_message_count"] or 0),
        }
        for row in rows
    }


def _enrich_sessions_with_message_counts(account_id: str, sessions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    peer_ids = [str(item.get("peer_id") or "").strip() for item in sessions]
    counts = _message_counts_by_peer(account_id, peer_ids)
    out: List[Dict[str, Any]] = []
    for item in sessions:
        copied = dict(item)
        peer_id = str(copied.get("peer_id") or "").strip()
        copied.update(counts.get(peer_id, {"message_count": 0, "inbound_message_count": 0, "outbound_message_count": 0}))
        try:
            copied["unread_count"] = int(copied.get("unread_count") or 0)
        except Exception:
            copied["unread_count"] = 0
        out.append(copied)
    return out


def _receive_summary(account_id: str, sessions: List[Dict[str, Any]], *, received_count: int = 0) -> Dict[str, int]:
    total_unread = sum(int(item.get("unread_count") or 0) for item in sessions)
    total_messages = sum(int(item.get("message_count") or 0) for item in sessions)
    return {
        "session_count": len(sessions),
        "message_count": int(received_count),
        "received_message_count": int(received_count),
        "stored_message_count": int(total_messages),
        "unread_message_count": int(total_unread),
    }


async def poll_updates(account_id: str, *, timeout_ms: Optional[int] = None) -> Dict[str, Any]:
    init_db()
    if _is_local_account_id(account_id):
        _find_local_account(account_id)
        try:
            session_data = await asyncio.to_thread(
                sync_local_sessions,
                account_id,
                passive=False,
                recent_only=True,
            )
            sessions = _enrich_sessions_with_message_counts(account_id, list(session_data.get("items") or []))
            unread_sessions = [item for item in sessions if int(item.get("unread_count") or 0) > 0]
            session_by_peer = {str(item.get("peer_id") or ""): item for item in sessions}
            target_session = unread_sessions[0] if unread_sessions else None
            peer_id = str((target_session or {}).get("peer_id") or "")
            data = {"new_message_count": 0, "has_new_message": False, "previous_latest_message": None, "latest_message": None}
            message_data: Dict[str, Any] = {"items": [], "real_message_count": 0}
            messages: List[Dict[str, Any]] = []
            current_left_session = target_session
            current_left_unread_count = int((target_session or {}).get("unread_count") or 0)
            session = None
            if peer_id:
                data = await asyncio.to_thread(sync_local_messages, account_id, peer_id, load_more_pages=0)
                peer_id = str(data.get("peer_id") or peer_id)
                message_data = list_messages(account_id, peer_id, limit=50, offset=0) if peer_id else {"items": [], "real_message_count": 0}
                messages = message_data.get("items") or []
                current_left_session = session_by_peer.get(peer_id) or target_session
                current_left_unread_count = int((current_left_session or {}).get("unread_count") or 0)
                if peer_id:
                    with _connect() as conn:
                        row = conn.execute(
                            "select * from wechat_session_state where account_id=? and peer_id=? limit 1",
                            (account_id, peer_id),
                        ).fetchone()
                    session = _row_to_dict(row) if row else {"account_id": account_id, "peer_id": peer_id, "display_name": peer_id}
                    sessions = _enrich_sessions_with_message_counts(account_id, [session])
                    session = sessions[0] if sessions else session
            has_new_message = bool(data.get("has_new_message"))
            changed = [session] if (session and has_new_message) else []
            group_sync = {
                "ok": True,
                "skipped": True,
                "count": 0,
                "items": [],
                "message": "接管轮询只处理未读消息，群列表同步已跳过",
            }
            summary = _receive_summary(account_id, sessions, received_count=int(data.get("new_message_count") or 0))
            recovery_events = [
                dict(item.get("driver_recovery") or {})
                for item in (session_data, data)
                if isinstance(item, dict) and item.get("driver_recovered")
            ]
            return {
                "ok": True,
                "items": unread_sessions,
                "count": len(unread_sessions),
                "sessions": sessions,
                "unread_sessions": unread_sessions,
                "unread_session_count": len(unread_sessions),
                "changed_sessions": changed,
                "changed_count": len(changed),
                "current_left_session": current_left_session,
                "current_left_unread_count": current_left_unread_count,
                "current_session": session,
                "current_messages": messages or [],
                "current_message_count": int(message_data.get("real_message_count") or 0),
                "new_message_count": int(data.get("new_message_count") or 0),
                "has_new_message": has_new_message,
                "current_session_has_unread": current_left_unread_count > 0 or has_new_message,
                "previous_latest_message": data.get("previous_latest_message"),
                "latest_message": data.get("latest_message"),
                "left_session_sync": session_data,
                "group_sync": group_sync,
                "sync_result": data,
                "unread_preserved_count": 0,
                "driver_recovered": bool(recovery_events),
                "driver_retry_count": len(recovery_events),
                "driver_recoveries": recovery_events,
                **summary,
                "message": "收消息完成：已读取左侧会话未读数，并读取当前默认选中会话做最新消息对比",
            }
        except Exception as exc:
            status = local_driver_status()
            recovery = _latest_local_driver_recovery(account_id)
            return {
                "ok": False,
                "items": [],
                "count": 0,
                "message": f"本机微信已连接；消息读取驱动不可用：{exc}",
                "error": str(exc),
                "driver_recovered": False,
                "driver_retry_count": 1 if recovery.get("attempted") else 0,
                "driver_recovery": recovery,
                "driver": status.get("full_driver") or {},
            }
    account = _load_account(account_id)
    token = str(account.get("token") or "")
    if not token:
        raise RuntimeError("账号未连接，请先扫码")
    base_url = str(account.get("baseUrl") or DEFAULT_BASE_URL)
    sync_buf = _load_sync_buf(account_id)
    payload = {"get_updates_buf": sync_buf, "base_info": {"channel_version": CHANNEL_VERSION}}
    data = await _api_post(
        base_url,
        "ilink/bot/getupdates",
        payload,
        token=token,
        timeout_ms=timeout_ms or int(DEFAULT_STRATEGY["poll_timeout_ms"]),
    )
    ret = int(data.get("ret") or data.get("errcode") or 0)
    if ret not in (0,):
        return {"ok": False, "ret": ret, "message": data.get("errmsg") or "获取消息失败", "upstream": data}
    next_buf = str(data.get("get_updates_buf") or "")
    if next_buf:
        _save_sync_buf(account_id, next_buf)
    messages = [x for x in data.get("msgs") or [] if isinstance(x, dict)]
    persisted = [_persist_inbound(account_id, msg) for msg in messages]
    sessions = _enrich_sessions_with_message_counts(account_id, list_peers(account_id, limit=200, offset=0).get("items") or [])
    changed_peer_ids = {str(item.get("peer_id") or "") for item in persisted}
    changed = [item for item in sessions if str(item.get("peer_id") or "") in changed_peer_ids]
    summary = _receive_summary(account_id, sessions, received_count=len(persisted))
    return {
        "ok": True,
        "items": persisted,
        "count": len(persisted),
        "upstream_count": len(messages),
        "sessions": sessions,
        "changed_sessions": changed,
        "changed_count": len(changed),
        **summary,
    }


def _message_text(msg: Dict[str, Any]) -> str:
    for item in msg.get("item_list") or []:
        if not isinstance(item, dict):
            continue
        if item.get("text_item") and isinstance(item.get("text_item"), dict):
            text = item["text_item"].get("text")
            if text is not None:
                return str(text)
        voice = item.get("voice_item")
        if isinstance(voice, dict) and voice.get("text"):
            return str(voice.get("text"))
    return ""


def _persist_inbound(account_id: str, msg: Dict[str, Any]) -> Dict[str, Any]:
    peer_id = str(msg.get("from_user_id") or "").strip()
    if not peer_id:
        peer_id = str(msg.get("session_id") or msg.get("group_id") or "unknown")
    context_token = str(msg.get("context_token") or "")
    if context_token:
        _save_context_token(account_id, peer_id, context_token)
    now = _now_iso()
    content = _message_text(msg)
    msg_id = str(msg.get("message_id") or msg.get("client_id") or uuid.uuid4().hex)
    chat_type = "group" if msg.get("group_id") else "direct"
    with _connect() as conn:
        conn.execute(
            """
            insert into wechat_peers(id, account_id, peer_id, display_name, chat_type, context_token, last_inbound_at, raw_json, created_at, updated_at)
            values(?,?,?,?,?,?,?,?,?,?)
            on conflict(account_id, peer_id) do update set
              chat_type=excluded.chat_type,
              context_token=coalesce(excluded.context_token, wechat_peers.context_token),
              last_inbound_at=excluded.last_inbound_at,
              raw_json=excluded.raw_json,
              updated_at=excluded.updated_at
            """,
            (
                hashlib.sha1(f"{account_id}:{peer_id}".encode("utf-8")).hexdigest(),
                account_id,
                peer_id,
                peer_id,
                chat_type,
                context_token,
                now,
                _json_dumps(msg),
                now,
                now,
            ),
        )
        conn.execute(
            """
            insert into wechat_messages(id, account_id, peer_id, direction, msg_type, content, provider_message_id, status, raw_json, created_at)
            values(?,?,?,?,?,?,?,?,?,?)
            """,
            (uuid.uuid4().hex, account_id, peer_id, "in", "text", content, msg_id, "received", _json_dumps(msg), now),
        )
    session = _persist_session(
        account_id,
        {
            "peer_id": peer_id,
            "display_name": peer_id,
            "last_content": content,
            "session_time": now,
            "unread_count": 1,
            "is_new": True,
            "raw": msg,
        },
        chat_type=chat_type,
    )
    return {"account_id": account_id, "peer_id": peer_id, "chat_type": chat_type, "content": content, "created_at": now, "session": session}


def _stable_key(*parts: str) -> str:
    return hashlib.sha1(":".join(str(x or "") for x in parts).encode("utf-8")).hexdigest()


def _local_wechat_hwnd(account_id: str = "") -> int:
    if account_id:
        item = _find_local_account(account_id)
        return int(item.get("hwnd") or 0)
    windows = _scan_local_wechat_windows(max_age_seconds=0)
    if not windows:
        windows = _ensure_local_wechat_window_visible().get("windows") or []
    return int((windows[0] if windows else {}).get("hwnd") or 0)


def _uia_control_text(node: Any, default: str = "") -> str:
    try:
        return str(getattr(node, "Name", "") or "").strip()
    except Exception:
        return default


def _uia_control_class(node: Any, default: str = "") -> str:
    try:
        return str(getattr(node, "ClassName", "") or "").strip()
    except Exception:
        return default


def _uia_rect(node: Any) -> Optional[tuple[float, float, float, float]]:
    try:
        rect = getattr(node, "BoundingRectangle", None)
        if rect is None:
            return None
        left = float(getattr(rect, "left", getattr(rect, "Left", 0)) or 0)
        top = float(getattr(rect, "top", getattr(rect, "Top", 0)) or 0)
        right = float(getattr(rect, "right", getattr(rect, "Right", 0)) or 0)
        bottom = float(getattr(rect, "bottom", getattr(rect, "Bottom", 0)) or 0)
        if right <= left or bottom <= top:
            return None
        return left, top, right, bottom
    except Exception:
        return None


def _ensure_local_tab(hwnd: int, tab_name: str, *, strict: bool = False) -> None:
    if not hwnd or not _module_available("uiautomation"):
        return
    try:
        import uiautomation as auto  # type: ignore

        _focus_local_wechat(hwnd)
        root = auto.ControlFromHandle(int(hwnd))
        try:
            root.SetActive()
            root.SetFocus()
        except Exception:
            pass
        clicked = False
        btn = root.ButtonControl(Name=tab_name)
        if btn.Exists(1.5):
            try:
                # Qt/WeChat tab items sometimes ignore UIA invoke-style clicks.
                # A real pointer click is slower but switches tabs more reliably.
                btn.Click(simulateMove=True)
            except Exception:
                btn.Click(simulateMove=False)
            clicked = True
            time.sleep(1.2)
        if not clicked:
            # Newer WeChat builds expose the left navigation item as an
            # XTabBarItem rather than a ButtonControl.
            nav = _uia_find_by_names(root, [tab_name], contains=False, max_depth=10)
            if nav is not None:
                try:
                    nav.Click(simulateMove=True)
                except Exception:
                    nav.Click(simulateMove=False)
                clicked = True
                time.sleep(1.2)
        if not clicked:
            shortcut = {"\u5fae\u4fe1": "1", "\u901a\u8baf\u5f55": "2"}.get(tab_name)
            if shortcut:
                _send_hotkey(shortcut, ctrl=True, pause=0.8)
                clicked = True
                time.sleep(1.0)
        if strict and not clicked:
            raise RuntimeError(f"local WeChat tab not found: {tab_name}")
    except Exception as exc:
        if strict:
            raise RuntimeError(f"切换本机微信页面失败：{exc}") from exc
        return


def _ensure_local_chat_tab(account_id: str = "") -> None:
    hwnd = _local_wechat_hwnd(account_id)
    if hwnd:
        _ensure_local_tab(hwnd, "\u5fae\u4fe1")


def _ensure_local_contacts_tab(account_id: str) -> int:
    hwnd = _local_wechat_hwnd(account_id)
    if hwnd:
        _ensure_local_tab(hwnd, "\u901a\u8baf\u5f55", strict=True)
    return hwnd


def _session_from_obj(sess: Any) -> Dict[str, Any]:
    raw = _obj_dict(sess)
    name = _obj_value(sess, "name", "nickname", "realname", "display_name") or str(raw.get("name") or "")
    try:
        unread_count = int(raw.get("new_count") or 0)
    except Exception:
        unread_count = 0
    return {
        "peer_id": str(name or "").strip(),
        "display_name": str(name or "").strip(),
        "last_content": str(raw.get("content") or ""),
        "session_time": str(raw.get("time") or ""),
        "unread_count": unread_count,
        "is_new": bool(raw.get("isnew")),
        "is_muted": bool(raw.get("ismute")),
        "raw": raw,
    }


def _persist_session(account_id: str, session: Dict[str, Any], *, chat_type: str = "unknown") -> Dict[str, Any]:
    now = _now_iso()
    peer_id = str(session.get("peer_id") or session.get("name") or session.get("display_name") or "").strip()
    if not peer_id:
        return {}
    display_name = str(session.get("display_name") or peer_id).strip()
    last_content = str(session.get("last_content") or "")
    session_time = str(session.get("session_time") or "")
    unread_count = int(session.get("unread_count") or 0)
    is_new = 1 if bool(session.get("is_new")) else 0
    is_muted = 1 if bool(session.get("is_muted")) else 0
    raw = dict(session.get("raw") or session)
    changed = True
    message_preview_changed = False
    unread_preserved = False
    with _connect() as conn:
        old = conn.execute(
            """
            select last_content, session_time, unread_count, is_new, is_muted
            from wechat_session_state where account_id=? and peer_id=? limit 1
            """,
            (account_id, peer_id),
        ).fetchone()
        if old:
            message_preview_changed = (
                str(old["last_content"] or "") != last_content
                or str(old["session_time"] or "") != session_time
            )
            changed = (
                message_preview_changed
                or int(old["unread_count"] or 0) != unread_count
                or int(old["is_new"] or 0) != is_new
                or int(old["is_muted"] or 0) != is_muted
            )
            if (
                unread_count == 0
                and int(old["unread_count"] or 0) > 0
                and str(old["last_content"] or "") == last_content
                and str(old["session_time"] or "") == session_time
                and not bool(session.get("clear_unread"))
            ):
                unread_count = int(old["unread_count"] or 0)
                is_new = int(old["is_new"] or 0)
                unread_preserved = True
                changed = False
        conn.execute(
            """
            insert into wechat_session_state(
                id, account_id, peer_id, display_name, chat_type, last_content, session_time,
                unread_count, is_new, is_muted, raw_json, first_seen_at, updated_at
            )
            values(?,?,?,?,?,?,?,?,?,?,?,?,?)
            on conflict(account_id, peer_id) do update set
              display_name=excluded.display_name,
              chat_type=case when excluded.chat_type != 'unknown' then excluded.chat_type else wechat_session_state.chat_type end,
              last_content=excluded.last_content,
              session_time=excluded.session_time,
              unread_count=excluded.unread_count,
              is_new=excluded.is_new,
              is_muted=excluded.is_muted,
              raw_json=excluded.raw_json,
              updated_at=excluded.updated_at
            """,
            (
                _stable_key(account_id, peer_id),
                account_id,
                peer_id,
                display_name,
                chat_type,
                last_content,
                session_time,
                unread_count,
                is_new,
                is_muted,
                _json_dumps(raw),
                now,
                now,
            ),
        )
        conn.execute(
            """
            insert into wechat_peers(id, account_id, peer_id, display_name, chat_type, raw_json, created_at, updated_at)
            values(?,?,?,?,?,?,?,?)
            on conflict(account_id, peer_id) do update set
              display_name=excluded.display_name,
              chat_type=case when excluded.chat_type != 'unknown' then excluded.chat_type else wechat_peers.chat_type end,
              raw_json=excluded.raw_json,
              updated_at=excluded.updated_at
            """,
            (
                _stable_key(account_id, peer_id),
                account_id,
                peer_id,
                display_name,
                chat_type,
                _json_dumps(raw),
                now,
                now,
            ),
        )
    return {
        "account_id": account_id,
        "peer_id": peer_id,
        "display_name": display_name,
        "chat_type": chat_type,
        "last_content": last_content,
        "session_time": session_time,
        "unread_count": unread_count,
        "is_new": bool(is_new),
        "is_muted": bool(is_muted),
        "changed": bool(changed),
        "message_preview_changed": bool(message_preview_changed),
        "unread_preserved": unread_preserved,
        "updated_at": now,
    }


def _persist_peer_chat_info(account_id: str, peer_id: str, info: Dict[str, Any]) -> None:
    chat_type = str(info.get("chat_type") or "unknown").strip() or "unknown"
    display_name = str(info.get("chat_name") or peer_id).strip() or peer_id
    if chat_type == "group":
        _persist_group(
            account_id,
            {
                "group_key": peer_id,
                "display_name": display_name,
                "member_count": info.get("group_member_count") or 0,
                "source": "wxauto4_chat_info",
                "raw": info,
            },
        )
    elif chat_type in {"friend", "official"}:
        _persist_session(account_id, {"peer_id": peer_id, "display_name": display_name, "raw": info}, chat_type="direct" if chat_type == "friend" else chat_type)
    else:
        _persist_session(account_id, {"peer_id": peer_id, "display_name": display_name, "raw": info}, chat_type=chat_type)


def _obj_value(obj: Any, *names: str) -> str:
    if isinstance(obj, dict):
        for name in names:
            value = obj.get(name)
            if value is not None and str(value).strip():
                return str(value).strip()
    for name in names:
        try:
            value = getattr(obj, name)
            if value is not None and str(value).strip():
                return str(value).strip()
        except Exception:
            pass
    return ""


def _obj_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return dict(obj)
    data: Dict[str, Any] = {}
    for name in (
        "name",
        "display_name",
        "nickname",
        "nickName",
        "remark",
        "wxNo",
        "wxid",
        "username",
        "memberCount",
        "member_count",
        "content",
        "time",
        "isnew",
        "new_count",
        "ismute",
        "sender",
        "sender_remark",
        "type",
        "attr",
        "id",
        "hash",
        "hash_text",
        "path",
        "filename",
        "file_name",
        "filepath",
        "file_path",
        "url",
        "size",
        "file_size",
    ):
        try:
            value = getattr(obj, name)
        except Exception:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            data[name] = value
    try:
        info = getattr(obj, "info")
        if isinstance(info, dict):
            data.update(info)
    except Exception:
        pass
    return data


def _build_contact_record(account_id: str, contact: Dict[str, Any], *, now: Optional[str] = None) -> Dict[str, Any]:
    now = now or _now_iso()
    contact_key = (
        str(contact.get("contact_key") or contact.get("wxNo") or contact.get("wx_no") or contact.get("username") or contact.get("id") or "").strip()
    )
    display_name = str(
        contact.get("display_name")
        or contact.get("nickname")
        or contact.get("nickName")
        or contact.get("remark")
        or contact.get("name")
        or contact_key
    ).strip()
    if not contact_key:
        contact_key = display_name
    if not contact_key:
        return {}
    source = str(contact.get("source") or "local").strip() or "local"
    wx_no = str(contact.get("wxNo") or contact.get("wx_no") or contact.get("username") or "").strip()
    remark = str(contact.get("remark") or "").strip()
    raw = dict(contact)
    return {
        "id": _stable_key(account_id, contact_key),
        "account_id": account_id,
        "contact_key": contact_key,
        "display_name": display_name,
        "remark": remark,
        "wx_no": wx_no,
        "source": source,
        "raw_json": _json_dumps(raw),
        "updated_at": now,
    }


def _write_contact_record(conn: sqlite3.Connection, record: Dict[str, Any], *, chat_type: str = "direct") -> None:
    conn.execute(
        """
        insert into wechat_contacts(id, account_id, contact_key, display_name, remark, wx_no, source, raw_json, created_at, updated_at)
        values(?,?,?,?,?,?,?,?,?,?)
        on conflict(account_id, contact_key) do update set
          display_name=excluded.display_name,
          remark=excluded.remark,
          wx_no=coalesce(nullif(excluded.wx_no, ''), wechat_contacts.wx_no),
          source=excluded.source,
          raw_json=excluded.raw_json,
          updated_at=excluded.updated_at
        """,
        (
            record["id"],
            record["account_id"],
            record["contact_key"],
            record["display_name"],
            record["remark"],
            record["wx_no"],
            record["source"],
            record["raw_json"],
            record["updated_at"],
            record["updated_at"],
        ),
    )


def _contact_record_public(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "account_id": record["account_id"],
        "contact_key": record["contact_key"],
        "display_name": record["display_name"],
        "wx_no": record["wx_no"],
        "remark": record["remark"],
        "source": record["source"],
        "updated_at": record["updated_at"],
    }


def _persist_contact(account_id: str, contact: Dict[str, Any], *, chat_type: str = "direct") -> Dict[str, Any]:
    record = _build_contact_record(account_id, contact)
    if not record:
        return {}
    with _connect() as conn:
        _write_contact_record(conn, record, chat_type=chat_type)
    return _contact_record_public(record)


def _replace_contacts_snapshot(account_id: str, contacts: List[Dict[str, Any]], *, chat_type: str = "direct") -> List[Dict[str, Any]]:
    now = _now_iso()
    records = [x for x in (_build_contact_record(account_id, item, now=now) for item in contacts) if x]
    with _connect() as conn:
        conn.execute("delete from wechat_contacts where account_id=?", (account_id,))
        for record in records:
            _write_contact_record(conn, record, chat_type=chat_type)
    return [_contact_record_public(record) for record in records]


def _merge_contacts_snapshot(account_id: str, contacts: List[Dict[str, Any]], *, chat_type: str = "direct") -> List[Dict[str, Any]]:
    now = _now_iso()
    records = [x for x in (_build_contact_record(account_id, item, now=now) for item in contacts) if x]
    with _connect() as conn:
        for record in records:
            _write_contact_record(conn, record, chat_type=chat_type)
    return [_contact_record_public(record) for record in records]


def _contact_count(account_id: str) -> int:
    with _connect() as conn:
        row = conn.execute("select count(*) from wechat_contacts where account_id=?", (account_id,)).fetchone()
    return int(row[0] if row else 0)


def _contact_sync_max_rounds(limit: int) -> int:
    clean_limit = max(1, min(int(limit or 10000), 10000))
    return max(120, min(900, int(clean_limit / 4) + 120))


def _wechat_contact_skip_names() -> set[str]:
    return {
        "新的朋友",
        "群聊",
        "标签",
        "公众号",
        "企业微信联系人",
        "仅聊天的朋友",
    } | set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _persist_group(account_id: str, group: Dict[str, Any]) -> Dict[str, Any]:
    now = _now_iso()
    group_key = str(group.get("group_key") or group.get("wxNo") or group.get("username") or group.get("id") or group.get("name") or "").strip()
    display_name = str(group.get("display_name") or group.get("groupName") or group.get("name") or group.get("remark") or group_key).strip()
    if not group_key:
        group_key = display_name
    if not group_key:
        return {}
    try:
        member_count = int(group.get("memberCount") or group.get("member_count") or 0)
    except Exception:
        member_count = 0
    source = str(group.get("source") or "local").strip() or "local"
    raw = dict(group)
    with _connect() as conn:
        conn.execute(
            """
            insert into wechat_groups(id, account_id, group_key, display_name, member_count, remark, source, raw_json, created_at, updated_at)
            values(?,?,?,?,?,?,?,?,?,?)
            on conflict(account_id, group_key) do update set
              display_name=excluded.display_name,
              member_count=excluded.member_count,
              remark=excluded.remark,
              source=excluded.source,
              raw_json=excluded.raw_json,
              updated_at=excluded.updated_at
            """,
            (
                _stable_key(account_id, group_key),
                account_id,
                group_key,
                display_name,
                member_count,
                str(group.get("remark") or ""),
                source,
                _json_dumps(raw),
                now,
                now,
            ),
        )
        conn.execute(
            """
            insert into wechat_peers(id, account_id, peer_id, display_name, chat_type, raw_json, created_at, updated_at)
            values(?,?,?,?,?,?,?,?)
            on conflict(account_id, peer_id) do update set
              display_name=excluded.display_name,
              chat_type=excluded.chat_type,
              raw_json=excluded.raw_json,
              updated_at=excluded.updated_at
            """,
            (
                _stable_key(account_id, group_key),
                account_id,
                group_key,
                display_name,
                "group",
                _json_dumps(raw),
                now,
                now,
            ),
        )
    return {
        "account_id": account_id,
        "group_key": group_key,
        "display_name": display_name,
        "member_count": member_count,
        "source": source,
        "updated_at": now,
    }


def _replace_groups_snapshot(account_id: str, groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    with _connect() as conn:
        conn.execute("delete from wechat_groups where account_id=?", (account_id,))
    items: List[Dict[str, Any]] = []
    for group in groups:
        saved = _persist_group(account_id, group)
        if saved:
            items.append(saved)
    return items


def _persist_message_obj_legacy(account_id: str, peer_id: str, msg: Any) -> Dict[str, Any]:
    raw = _obj_dict(msg)
    content = str(raw.get("content") or raw.get("text") or "")
    msg_type = str(raw.get("type") or "text")
    direction = "out" if str(raw.get("attr") or "").lower() in {"self", "out", "me"} else "in"
    created_at = _now_iso()
    message_id = str(raw.get("id") or uuid.uuid4().hex)
    with _connect() as conn:
        conn.execute(
            """
            insert into wechat_messages(id, account_id, peer_id, direction, msg_type, content, provider_message_id, status, raw_json, created_at)
            values(?,?,?,?,?,?,?,?,?,?)
            """,
            (uuid.uuid4().hex, account_id, peer_id, direction, msg_type, content, message_id, "received", _json_dumps(raw), created_at),
        )
    return {"account_id": account_id, "peer_id": peer_id, "content": content, "msg_type": msg_type, "created_at": created_at}


_MEDIA_MESSAGE_TYPES = {"image", "video", "file"}


def _message_download_dir(account_id: str, peer_id: str) -> Path:
    account_part = _safe_upload_filename(account_id or "account")
    peer_part = _safe_upload_filename(peer_id or "peer")
    path = NATIVE_WECHAT_DOWNLOAD_DIR / account_part / peer_part
    path.mkdir(parents=True, exist_ok=True)
    return path


def _media_public_url(path: Path) -> str:
    try:
        rel = path.resolve().relative_to((ROOT_DIR / "assets").resolve())
        return "/media/" + "/".join(rel.parts)
    except Exception:
        return ""


def _copy_downloaded_media(src: Path, target_dir: Path, *, fallback_name: str) -> Path:
    src = Path(src).expanduser().resolve()
    suffix = src.suffix or Path(fallback_name).suffix or ".bin"
    stem = _safe_upload_filename(src.stem or Path(fallback_name).stem or "wechat-media")
    target = target_dir / f"{uuid.uuid4().hex}_{stem}{suffix}"
    if src.exists() and src.is_file():
        if src.resolve() != target.resolve():
            shutil.copy2(str(src), str(target))
        return target
    return src


def _download_message_attachment(account_id: str, peer_id: str, msg: Any, raw: Dict[str, Any], msg_type: str) -> Optional[Dict[str, Any]]:
    if msg_type not in _MEDIA_MESSAGE_TYPES:
        return None
    target_dir = _message_download_dir(account_id, peer_id)
    filename = str(raw.get("filename") or raw.get("file_name") or raw.get("name") or raw.get("content") or msg_type or "wechat-media")
    try:
        result: Any = None
        if msg_type == "file" and hasattr(msg, "download"):
            result = msg.download(dir_path=target_dir)
        elif msg_type in {"image", "video"} and hasattr(msg, "download"):
            result = msg.download()
        raw_path = result
        if isinstance(result, dict):
            raw_path = result.get("path") or result.get("file") or result.get("filepath") or result.get("savepath")
        if not raw_path:
            try:
                raw_path = getattr(msg, "path")
            except Exception:
                raw_path = None
        if raw_path:
            local_path = _copy_downloaded_media(Path(str(raw_path)), target_dir, fallback_name=filename)
            if local_path.exists() and local_path.is_file():
                return {
                    "kind": native_wechat_file_kind(local_path),
                    "filename": local_path.name,
                    "local_path": str(local_path),
                    "url": _media_public_url(local_path),
                    "size": int(local_path.stat().st_size),
                    "content_type": mimetypes.guess_type(str(local_path))[0] or "application/octet-stream",
                }
        existing_path = raw.get("path") or raw.get("file_path") or raw.get("filepath")
        if existing_path:
            local_path = _copy_downloaded_media(Path(str(existing_path)), target_dir, fallback_name=filename)
            if local_path.exists() and local_path.is_file():
                return {
                    "kind": native_wechat_file_kind(local_path),
                    "filename": local_path.name,
                    "local_path": str(local_path),
                    "url": _media_public_url(local_path),
                    "size": int(local_path.stat().st_size),
                    "content_type": mimetypes.guess_type(str(local_path))[0] or "application/octet-stream",
                }
    except Exception as exc:
        return {"kind": msg_type, "filename": filename, "download_error": str(exc)}
    source_url = str(raw.get("url") or "").strip()
    if source_url.startswith(("http://", "https://")):
        return {"kind": msg_type, "filename": filename, "source_url": source_url, "url": source_url}
    return {"kind": msg_type, "filename": filename, "download_error": "未获取到本地文件路径"}


def _normalize_message_public(item: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(item)
    raw = out.get("raw_json") if isinstance(out.get("raw_json"), dict) else {}
    msg_type = str(out.get("msg_type") or raw.get("type") or "text").lower()
    sender = str(raw.get("sender") or raw.get("sender_remark") or "").lower()
    attr = str(raw.get("attr") or "").lower()
    if msg_type == "time" or sender == "system" or attr == "system":
        out["direction"] = "system"
        out["msg_type"] = "time" if msg_type == "time" else (out.get("msg_type") or "system")
    elif attr in {"self", "out", "me"}:
        out["direction"] = "out"
    attachments = raw.get("attachments") if isinstance(raw, dict) else None
    out["attachments"] = attachments if isinstance(attachments, list) else []
    if out["attachments"]:
        out["attachment"] = out["attachments"][0]
    out["is_system"] = out.get("direction") == "system" or out.get("msg_type") == "time"
    return out


def _persist_message_obj(account_id: str, peer_id: str, msg: Any) -> Dict[str, Any]:
    raw = _obj_dict(msg)
    content = str(raw.get("content") or raw.get("text") or "")
    msg_type = str(raw.get("type") or "text").lower()
    attr = str(raw.get("attr") or "").lower()
    sender = str(raw.get("sender") or raw.get("sender_remark") or "")
    if msg_type == "time" or attr == "system" or sender.lower() == "system":
        direction = "system"
    elif attr in {"self", "out", "me"}:
        direction = "out"
    else:
        direction = "in"
    created_at = _now_iso()
    message_id = str(raw.get("id") or raw.get("hash") or _stable_key(peer_id, msg_type, content, str(raw.get("time") or "")))
    attachment = _download_message_attachment(account_id, peer_id, msg, raw, msg_type)
    if attachment:
        raw["attachments"] = [attachment]
    with _connect() as conn:
        existing = conn.execute(
            """
            select * from wechat_messages
            where account_id=? and peer_id=? and provider_message_id=?
            limit 1
            """,
            (account_id, peer_id, message_id),
        ).fetchone()
        if existing:
            existing_item = _normalize_message_public(_row_to_dict(existing))
            if attachment and not existing_item.get("attachments"):
                merged_raw = dict(existing_item.get("raw_json") or {})
                merged_raw["attachments"] = [attachment]
                conn.execute(
                    "update wechat_messages set raw_json=? where id=?",
                    (_json_dumps(merged_raw), existing["id"]),
                )
                existing_item["raw_json"] = merged_raw
                existing_item["attachments"] = [attachment]
                existing_item["attachment"] = attachment
            return {
                "account_id": account_id,
                "peer_id": peer_id,
                "content": existing_item.get("content") or content,
                "msg_type": existing_item.get("msg_type") or msg_type,
                "direction": existing_item.get("direction") or direction,
                "sender": sender,
                "created_at": existing_item.get("created_at") or created_at,
                "attachments": existing_item.get("attachments") or [],
                "deduped": True,
            }
        conn.execute(
            """
            insert into wechat_messages(id, account_id, peer_id, direction, msg_type, content, provider_message_id, status, raw_json, created_at)
            values(?,?,?,?,?,?,?,?,?,?)
            """,
            (uuid.uuid4().hex, account_id, peer_id, direction, msg_type, content, message_id, "received", _json_dumps(raw), created_at),
        )
        if direction == "out":
            conn.execute(
                "update wechat_peers set last_outbound_at=?, updated_at=? where account_id=? and peer_id=?",
                (created_at, created_at, account_id, peer_id),
            )
        elif direction == "in":
            conn.execute(
                "update wechat_peers set last_inbound_at=?, updated_at=? where account_id=? and peer_id=?",
                (created_at, created_at, account_id, peer_id),
            )
    return {
        "account_id": account_id,
        "peer_id": peer_id,
        "content": content,
        "msg_type": msg_type,
        "direction": direction,
        "sender": sender,
        "created_at": created_at,
        "attachments": [attachment] if attachment else [],
        "deduped": False,
    }


def _latest_message_record(
    account_id: str,
    peer_id: str,
    *,
    include_system: bool = False,
) -> Optional[Dict[str, Any]]:
    filters = ["account_id=?", "peer_id=?"]
    if not include_system:
        filters.extend(["direction != 'system'", "msg_type != 'time'"])
    with _connect() as conn:
        row = conn.execute(
            f"""
            select * from wechat_messages
            where {' and '.join(filters)}
            order by created_at desc
            limit 1
            """,
            (account_id, peer_id),
        ).fetchone()
    return _normalize_message_public(_row_to_dict(row)) if row else None


def _message_compare_key(message: Optional[Dict[str, Any]]) -> str:
    if not message:
        return ""
    return "|".join(
        str(message.get(key) or "")
        for key in ("provider_message_id", "direction", "msg_type", "content", "status", "created_at")
    )


def _local_driver_recovery_key(account_id: str) -> str:
    return str(account_id or "").strip() or LOCAL_DEFAULT_ACCOUNT_ID


def _store_local_driver_recovery(account_id: str, value: Dict[str, Any]) -> Dict[str, Any]:
    saved = dict(value)
    _LOCAL_WECHAT_DRIVER_RECOVERY[_local_driver_recovery_key(account_id)] = saved
    return saved


def _latest_local_driver_recovery(account_id: str) -> Dict[str, Any]:
    return dict(_LOCAL_WECHAT_DRIVER_RECOVERY.get(_local_driver_recovery_key(account_id)) or {})


def _ensure_local_wechat_com_thread() -> None:
    if bool(getattr(_LOCAL_WECHAT_THREAD_STATE, "com_initialized", False)):
        return
    try:
        import pythoncom  # type: ignore

        pythoncom.CoInitialize()
    except Exception:
        pass
    _LOCAL_WECHAT_THREAD_STATE.com_initialized = True


def _reset_local_automation_clients() -> Dict[str, Any]:
    global _LOCAL_WECHAT_AUTOMATION_OWNER_THREAD_ID

    reset: List[str] = []
    errors: List[str] = []
    for module_name in ("uiautomation.uiautomation", "wxauto4.uia.uiautomation"):
        try:
            module = importlib.import_module(module_name)
            client_type = getattr(module, "_AutomationClient", None)
            if client_type is not None and getattr(client_type, "_instance", None) is not None:
                client_type._instance = None
                reset.append(module_name)
        except Exception as exc:
            errors.append(f"{module_name}: {exc}")
    _LOCAL_WECHAT_AUTOMATION_OWNER_THREAD_ID = threading.get_ident()
    return {"reset": reset, "errors": errors}


def _prepare_local_automation_thread() -> Dict[str, Any]:
    global _LOCAL_WECHAT_AUTOMATION_OWNER_THREAD_ID

    _ensure_local_wechat_com_thread()
    current_thread_id = threading.get_ident()
    if _LOCAL_WECHAT_AUTOMATION_OWNER_THREAD_ID in (0, current_thread_id):
        _LOCAL_WECHAT_AUTOMATION_OWNER_THREAD_ID = current_thread_id
        return {"thread_changed": False, "thread_id": current_thread_id, "reset": [], "errors": []}
    reset = _reset_local_automation_clients()
    return {"thread_changed": True, "thread_id": current_thread_id, **reset}


def _recover_local_wechat_driver(
    account_id: str,
    *,
    operation: str,
    error: str,
    ensure_chat_tab: bool = True,
) -> Dict[str, Any]:
    recovery: Dict[str, Any] = {
        "attempted": True,
        "ok": False,
        "recovered": False,
        "operation": str(operation or "native_wechat"),
        "initial_error": str(error or "")[:1000],
        "attempted_at": _now_iso(),
        "thread_id": threading.get_ident(),
        "automation_reset": {},
        "window": {},
        "uia": {},
        "error": "",
    }
    try:
        _clear_local_windows_cache()
        recovery["automation_reset"] = _reset_local_automation_clients()
        visible = _ensure_local_wechat_window_visible(wait_seconds=2.5, allow_launch=False)
        windows = list(visible.get("windows") or [])
        if not windows:
            windows = _scan_local_wechat_windows(max_age_seconds=0)
        if not windows:
            raise RuntimeError("没有检测到已登录的微信主窗口")
        window = dict(windows[0])
        hwnd = int(window.get("hwnd") or 0)
        if not hwnd:
            raise RuntimeError("微信主窗口句柄无效")
        recovery["window"] = {
            "hwnd": hwnd,
            "pid": int(window.get("pid") or 0),
            "title": str(window.get("title") or ""),
            "version": str(window.get("version") or ""),
        }
        _focus_local_wechat(hwnd)
        if ensure_chat_tab:
            _ensure_local_tab(hwnd, "\u5fae\u4fe1", strict=True)
        time.sleep(0.8)
        _clear_local_windows_cache()
        recovery["uia"] = _probe_wechat_uia(hwnd)
        recovery["ok"] = True
    except Exception as exc:
        recovery["error"] = str(exc)[:1000]
    return _store_local_driver_recovery(account_id, recovery)


def _mark_local_driver_recovery(
    account_id: str,
    recovery: Dict[str, Any],
    *,
    recovered: bool,
    retry_error: str = "",
) -> Dict[str, Any]:
    updated = {
        **dict(recovery),
        "ok": bool(recovered),
        "recovered": bool(recovered),
        "retry_error": str(retry_error or "")[:1000],
        "finished_at": _now_iso(),
    }
    if recovered:
        updated["error"] = ""
    elif retry_error:
        updated["error"] = str(retry_error)[:1000]
    return _store_local_driver_recovery(account_id, updated)


def _annotate_local_driver_recovery(result: Any, recovery: Dict[str, Any]) -> Any:
    if isinstance(result, dict):
        result["driver_recovered"] = True
        result["driver_retry_count"] = max(1, int(recovery.get("retry_count") or 1))
        result["driver_recovery"] = dict(recovery)
    return result


def _collect_local_driver_recovery(target: Dict[str, Any], source: Any) -> None:
    if not isinstance(source, dict) or not source.get("driver_recovered"):
        return
    target["driver_recovered"] = True
    target["driver_retry_count"] = int(target.get("driver_retry_count") or 0) + int(
        source.get("driver_retry_count") or 1
    )
    events = target.setdefault("driver_recoveries", [])
    recovery = source.get("driver_recovery")
    if isinstance(events, list) and isinstance(recovery, dict):
        events.append(dict(recovery))


def _run_local_driver_operation(
    account_id: str,
    operation: str,
    callback: Callable[[], Any],
    *,
    retry_on_failure: bool = True,
) -> Any:
    # Do not let stale UI requests queue behind a stuck WeChat control. A
    # queued request could execute against a different chat after the first
    # operation finishes and click the wrong control.
    if not _LOCAL_WECHAT_UI_LOCK.acquire(timeout=0.75):
        raise RuntimeError("\u5fae\u4fe1\u6b63\u5728\u5904\u7406\u4e0a\u4e00\u9879\u64cd\u4f5c\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5")
    try:
        _prepare_local_automation_thread()
        recovery_before = _latest_local_driver_recovery(account_id)

        def invoke() -> Any:
            previous = bool(getattr(_LOCAL_WECHAT_THREAD_STATE, "operation_handles_recovery", False))
            _LOCAL_WECHAT_THREAD_STATE.operation_handles_recovery = retry_on_failure
            try:
                return callback()
            finally:
                _LOCAL_WECHAT_THREAD_STATE.operation_handles_recovery = previous

        try:
            result = invoke()
            recovery_after = _latest_local_driver_recovery(account_id)
            if (
                recovery_after.get("recovered")
                and recovery_after.get("finished_at")
                and recovery_after.get("finished_at") != recovery_before.get("finished_at")
            ):
                return _annotate_local_driver_recovery(result, recovery_after)
            return result
        except Exception as initial_exc:
            if not retry_on_failure:
                raise
            recovery = _recover_local_wechat_driver(
                account_id,
                operation=operation,
                error=str(initial_exc),
            )
            if not recovery.get("ok"):
                raise RuntimeError(
                    f"{operation}\u5931\u8d25\uff0c\u81ea\u52a8\u6062\u590d\u9a71\u52a8\u672a\u6210\u529f\uff1a"
                    f"{recovery.get('error') or initial_exc}"
                ) from initial_exc
            try:
                result = invoke()
            except Exception as retry_exc:
                recovery = _mark_local_driver_recovery(
                    account_id,
                    recovery,
                    recovered=False,
                    retry_error=str(retry_exc),
                )
                raise RuntimeError(
                    f"{operation}\u5931\u8d25\uff0c\u81ea\u52a8\u6062\u590d\u540e\u91cd\u8bd5\u4ecd\u5931\u8d25\uff1a{retry_exc}\uff1b"
                    "\u8bf7\u624b\u52a8\u91cd\u542f\u5fae\u4fe1\u5e76\u91cd\u65b0\u767b\u5f55\u540e\u518d\u8bd5"
                ) from retry_exc
            recovery = _mark_local_driver_recovery(account_id, recovery, recovered=True)
            return _annotate_local_driver_recovery(result, recovery)
    finally:
        _LOCAL_WECHAT_UI_LOCK.release()


def _new_wxauto4_client(account_id: str = "", *, ensure_chat_tab: bool = True) -> Any:
    try:
        import wxauto4  # type: ignore
    except Exception as exc:
        raise RuntimeError("\u7f3a\u5c11 wxauto4\uff0c\u65e0\u6cd5\u8bfb\u53d6\u901a\u8baf\u5f55/\u7fa4\u6d88\u606f") from exc
    if ensure_chat_tab:
        _ensure_local_chat_tab(account_id)
    wx = wxauto4.WeChat(debug=False, resize=False, ads=False)
    online_check = getattr(wx, "IsOnline", None)
    if callable(online_check) and not bool(online_check()):
        raise RuntimeError("wxauto4 \u672a\u8bc6\u522b\u5230\u5df2\u767b\u5f55\u7684\u5fae\u4fe1\u4e3b\u7a97\u53e3")
    return wx


def _get_wxauto4_client(account_id: str = "", *, ensure_chat_tab: bool = True) -> Any:
    with _LOCAL_WECHAT_UI_LOCK:
        _prepare_local_automation_thread()
        if bool(getattr(_LOCAL_WECHAT_THREAD_STATE, "operation_handles_recovery", False)):
            return _new_wxauto4_client(account_id, ensure_chat_tab=ensure_chat_tab)
        try:
            return _new_wxauto4_client(account_id, ensure_chat_tab=ensure_chat_tab)
        except Exception as initial_exc:
            recovery = _recover_local_wechat_driver(
                account_id,
                operation="wxauto4_connect",
                error=str(initial_exc),
                ensure_chat_tab=ensure_chat_tab,
            )
            if recovery.get("ok"):
                try:
                    wx = _new_wxauto4_client(account_id, ensure_chat_tab=ensure_chat_tab)
                    _mark_local_driver_recovery(account_id, recovery, recovered=True)
                    return wx
                except Exception as retry_exc:
                    _mark_local_driver_recovery(
                        account_id,
                        recovery,
                        recovered=False,
                        retry_error=str(retry_exc),
                    )
                    initial_exc = retry_exc
            windows = _scan_local_wechat_windows(max_age_seconds=0)
            version = str((windows[0] if windows else {}).get("version") or "")
            if version and not _version_lte(version, WXAUTO4_MAX_PLUS_VERSION):
                raise RuntimeError(
                    f"\u5f53\u524d\u5fae\u4fe1\u7248\u672c {version} \u9ad8\u4e8e wxauto4 \u5df2\u77e5\u9002\u914d\u7248\u672c\uff0c\u5b8c\u6574\u901a\u8baf\u5f55/\u7fa4\u80fd\u529b\u4e0d\u53ef\u7528"
                ) from initial_exc
            raise RuntimeError(str(initial_exc)) from initial_exc


def sync_local_sessions_legacy(account_id: str) -> Dict[str, Any]:
    init_db()
    _find_local_account(account_id)
    wx = _get_wxauto4_client(account_id)
    sessions = wx.GetSession()
    items: List[Dict[str, Any]] = []
    for sess in sessions or []:
        raw = _obj_dict(sess)
        name = _obj_value(sess, "name", "nickname", "realname", "display_name") or str(sess)
        item = {
            "contact_key": name,
            "display_name": name,
            "name": name,
            "source": "wxauto4_session",
            "raw": raw,
        }
        if name.endswith("群") or raw.get("is_group") or raw.get("type") == "group":
            saved = _persist_group(account_id, item)
        else:
            saved = _persist_contact(account_id, item)
        if saved:
            items.append(saved)
    return {"ok": True, "items": items, "count": len(items)}


def _sync_local_sessions_from_wxauto4(account_id: str) -> Dict[str, Any]:
    wx = _get_wxauto4_client(account_id)
    sessions = wx.GetSession()
    items: List[Dict[str, Any]] = []
    groups = 0
    for sess in sessions or []:
        session = _session_from_obj(sess)
        peer_id = str(session.get("peer_id") or "").strip()
        if not peer_id:
            continue
        raw = session.get("raw") if isinstance(session.get("raw"), dict) else {}
        chat_type = "unknown"
        raw_type = str(raw.get("type") or raw.get("chat_type") or "").lower()
        if raw.get("is_group") or raw_type in {"group", "chatroom"} or "@chatroom" in peer_id.lower():
            chat_type = "group"
            groups += 1
        saved = _persist_session(account_id, session, chat_type=chat_type)
        if saved:
            items.append(saved)
        if chat_type == "group":
            _persist_group(
                account_id,
                {
                    "group_key": peer_id,
                    "display_name": str(session.get("display_name") or peer_id),
                    "source": "wxauto4_session",
                    "raw": raw,
                },
            )
    if not items:
        raise RuntimeError("wxauto4 GetSession did not return local WeChat sessions")
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "changed": [x for x in items if x.get("changed")],
        "changed_count": sum(1 for x in items if x.get("changed")),
        "mode": "merge",
        "source": "wxauto4_sessions",
        "fallback": True,
        "group_count": groups,
        "scroll_rounds": 0,
        "scroll_completed": False,
    }


def _session_from_uia_cell(cell: Any) -> Dict[str, Any]:
    raw_text = _uia_control_text(cell)
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    peer_id = lines[0] if lines else ""
    is_muted = bool(lines and lines[-1] == "消息免打扰")
    time_index = len(lines) - (2 if is_muted and len(lines) >= 2 else 1)
    session_time = lines[time_index] if len(lines) >= 2 and time_index >= 1 else ""
    content_lines = lines[1:time_index] if len(lines) >= 3 and time_index >= 1 else []
    pinned = bool(content_lines and content_lines[0] == "已置顶")
    if pinned:
        content_lines = content_lines[1:]
    last_content = "\n".join(content_lines).strip()
    unread_count = 0
    if content_lines:
        m = re.match(r"^\[(\d+)条\]\s*(.*)$", content_lines[0])
        if m:
            unread_count = int(m.group(1))
            first_content = str(m.group(2) or "").strip()
            last_content = "\n".join([x for x in [first_content, *content_lines[1:]] if x]).strip()
    return {
        "peer_id": peer_id,
        "display_name": peer_id,
        "last_content": last_content,
        "session_time": session_time,
        "unread_count": unread_count,
        "is_new": unread_count > 0,
        "is_muted": is_muted,
        "raw": {"name": raw_text, "source": "pc_wechat_uia_sessions", "pinned": pinned},
    }


def _looks_like_uia_session_candidate(node: Any, root_rect: Optional[tuple[float, float, float, float]]) -> bool:
    text = _uia_control_text(node)
    if not text or len(text) > 500:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or len(lines) > 8:
        return False
    title = lines[0]
    if title in {"微信", "通讯录", "收藏", "聊天文件", "朋友圈", "视频号", "搜一搜", "看一看"}:
        return False
    class_name = _uia_control_class(node)
    control_type = str(getattr(node, "ControlTypeName", "") or "")
    score = 0
    lowered_class = class_name.lower()
    lowered_type = control_type.lower()
    if any(part in lowered_class for part in ("session", "chat", "cell", "item")):
        score += 2
    if any(part in lowered_type for part in ("listitem", "dataitem", "button")):
        score += 1
    last = lines[-1]
    if re.search(r"(\d{1,2}:\d{2}|昨天|前天|星期|周[一二三四五六日天]?|上午|下午|晚上|刚刚|\d+月\d+日|\d{4}/\d{1,2}/\d{1,2})", last):
        score += 2
    if len(lines) >= 3:
        score += 1
    rect = _uia_rect(node)
    if rect and root_rect:
        root_left, _root_top, root_right, _root_bottom = root_rect
        width = max(1.0, root_right - root_left)
        left, _top, right, _bottom = rect
        center_x = (left + right) / 2
        # The session list is normally in the left half of the main window.
        if center_x <= root_left + width * 0.48:
            score += 2
    return score >= 4


def _uia_session_cells(root: Any) -> List[Any]:
    nodes = _uia_walk(root, max_depth=20, max_nodes=5000)
    exact = [node for node in nodes if _uia_control_class(node) == "mmui::ChatSessionCell"]
    if exact:
        return exact
    root_rect = _uia_rect(root)
    seen: set[str] = set()
    generic: List[Any] = []
    for node in nodes:
        if not _looks_like_uia_session_candidate(node, root_rect):
            continue
        text = _uia_control_text(node)
        key = re.sub(r"\s+", "\n", text.strip())
        if key in seen:
            continue
        seen.add(key)
        generic.append(node)
    return generic


def _uia_scroll_target_from_cells(cells: List[Any], fallback: Any) -> Any:
    for cell in cells[:1]:
        node = cell
        for _idx in range(6):
            try:
                parent = node.GetParentControl()
            except Exception:
                parent = None
            if parent is None:
                break
            class_name = _uia_control_class(parent).lower()
            if any(part in class_name for part in ("list", "recycler", "session", "scroll")):
                return parent
            node = parent
    return cells[0] if cells else fallback


def _uia_collect_visible_sessions(root: Any) -> List[Dict[str, Any]]:
    return [item for item in (_session_from_uia_cell(cell) for cell in _uia_session_cells(root)) if item.get("peer_id")]


def _uia_collect_recent_sessions(hwnd: int, *, max_rounds: int = 20) -> Dict[str, Any]:
    import uiautomation as auto  # type: ignore

    root = auto.ControlFromHandle(int(hwnd))
    cells = _uia_session_cells(root)
    if not cells:
        return {"items": [], "rounds": 0, "completed": False}
    scroll_target = _uia_scroll_target_from_cells(cells, root)
    try:
        scroll_target.WheelUp(wheelTimes=30)
        time.sleep(0.35)
    except Exception:
        pass

    seen: Dict[str, Dict[str, Any]] = {}
    rounds = 0
    for idx in range(max(1, min(int(max_rounds or 20), 20))):
        rounds = idx + 1
        root = auto.ControlFromHandle(int(hwnd))
        cells = _uia_session_cells(root)
        visible = [_session_from_uia_cell(cell) for cell in cells]
        visible = [item for item in visible if item.get("peer_id")]
        before = len(seen)
        for item in visible:
            peer_id = str(item.get("peer_id") or "")
            if peer_id:
                seen[peer_id] = item
        if len(seen) == before or idx + 1 >= max_rounds or not cells:
            break
        scroll_target = _uia_scroll_target_from_cells(cells, root)
        try:
            scroll_target.WheelDown(wheelTimes=4)
            time.sleep(0.22)
        except Exception:
            break
    try:
        scroll_target.WheelUp(wheelTimes=30)
    except Exception:
        pass
    return {"items": list(seen.values()), "rounds": rounds, "completed": False}


def _find_uia_session_cell(root: Any, peer_id: str) -> Optional[Any]:
    """Find a session row by its first-line peer name, never by child controls."""
    wanted = str(peer_id or "").strip()
    if not wanted:
        return None
    for cell in _uia_session_cells(root):
        item = _session_from_uia_cell(cell)
        if str(item.get("peer_id") or "").strip() == wanted:
            return cell
    return None


def _open_local_session_by_uia(
    account_id: str,
    peer_id: str,
    *,
    max_rounds: int = 20,
) -> Dict[str, Any]:
    """Open one conversation by clicking its session row, without ChatWith search."""
    if not _module_available("uiautomation"):
        raise RuntimeError("uiautomation is required to open a local WeChat session")
    import uiautomation as auto  # type: ignore

    hwnd = _local_wechat_hwnd(account_id)
    if not hwnd:
        raise RuntimeError("local WeChat window not found")
    root = auto.ControlFromHandle(int(hwnd))
    cells = _uia_session_cells(root)
    if not cells:
        raise RuntimeError("未读取到微信会话列表")
    scroll_target = _uia_scroll_target_from_cells(cells, root)
    target = str(peer_id or "").strip()
    rounds = 0
    # run_auto_reply processes sessions in the same order as this scan, so the
    # cursor can keep moving down instead of resetting and reopening the list.
    for index in range(max(1, min(int(max_rounds or 20), 20))):
        rounds = index + 1
        root = auto.ControlFromHandle(int(hwnd))
        cell = _find_uia_session_cell(root, target)
        if cell is not None:
            _uia_click(cell)
            time.sleep(random.uniform(0.35, 0.65))
            return {
                "ok": True,
                "peer_id": target,
                "rounds": rounds,
                "selection_method": "pc_wechat_uia_session_cell",
            }
        cells = _uia_session_cells(root)
        if not cells:
            break
        scroll_target = _uia_scroll_target_from_cells(cells, root)
        try:
            scroll_target.WheelDown(wheelTimes=4)
            time.sleep(0.22)
        except Exception:
            break
    raise RuntimeError(f"未在微信会话列表找到联系人：{target}")


def _uia_collect_all_sessions(hwnd: int) -> Dict[str, Any]:
    import uiautomation as auto  # type: ignore

    root = auto.ControlFromHandle(int(hwnd))
    cells = _uia_session_cells(root)
    if not cells:
        return {"items": [], "rounds": 0, "completed": False}
    scroll_target = _uia_scroll_target_from_cells(cells, root)
    try:
        scroll_target.WheelUp(wheelTimes=30)
        time.sleep(0.45)
    except Exception:
        pass

    seen: Dict[str, Dict[str, Any]] = {}
    stable_rounds = 0
    last_signature = ""
    rounds = 0
    for idx in range(120):
        rounds = idx + 1
        root = auto.ControlFromHandle(int(hwnd))
        cells = _uia_session_cells(root)
        visible = [_session_from_uia_cell(cell) for cell in cells]
        visible = [item for item in visible if item.get("peer_id")]
        before = len(seen)
        for item in visible:
            peer_id = str(item.get("peer_id") or "")
            if peer_id:
                seen[peer_id] = item
        signature = "|".join(
            f"{item.get('peer_id') or ''}:{item.get('session_time') or ''}:{item.get('last_content') or ''}"
            for item in visible[-12:]
        )
        if len(seen) == before and signature == last_signature:
            stable_rounds += 1
        else:
            stable_rounds = 0
        last_signature = signature
        if stable_rounds >= 3:
            return {"items": list(seen.values()), "rounds": rounds, "completed": True}
        if not cells:
            break
        scroll_target = _uia_scroll_target_from_cells(cells, root)
        try:
            scroll_target.WheelDown(wheelTimes=4)
            time.sleep(0.28)
        except Exception:
            break
    return {"items": list(seen.values()), "rounds": rounds, "completed": False}


def _sync_local_sessions_from_uia(
    account_id: str,
    *,
    passive: bool = False,
    recent_only: bool = False,
) -> Dict[str, Any]:
    if not _module_available("uiautomation"):
        raise RuntimeError("uiautomation is required to sync local sessions")
    import uiautomation as auto  # type: ignore

    hwnd = _local_wechat_hwnd(account_id)
    if not hwnd:
        raise RuntimeError("local WeChat window not found")
    if not passive:
        _ensure_local_tab(hwnd, "\u5fae\u4fe1", strict=True)
    root = auto.ControlFromHandle(int(hwnd))
    scan = {"rounds": 1, "completed": True}
    if passive:
        sessions = _uia_collect_visible_sessions(root)
    elif recent_only:
        scan = _uia_collect_recent_sessions(hwnd, max_rounds=20)
        sessions = list(scan.get("items") or [])
    else:
        scan = _uia_collect_all_sessions(hwnd)
        sessions = list(scan.get("items") or [])
    if not sessions:
        if passive:
            raise RuntimeError("被动收消息未读取到左侧会话，请确认微信当前停留在聊天页；本次不会自动激活或切换微信")
        raise RuntimeError("未读取到微信左侧会话，请确认微信主窗口在聊天页")
    items: List[Dict[str, Any]] = []
    for session in sessions:
        saved = _persist_session(account_id, session, chat_type="unknown")
        if saved:
            items.append(saved)
    peer_ids = [str(item.get("peer_id") or "") for item in items if item.get("peer_id")]
    replace_mode = (not passive) and bool(scan.get("completed"))
    if peer_ids and replace_mode:
        placeholders = ",".join("?" for _ in peer_ids)
        with _connect() as conn:
            conn.execute(
                f"delete from wechat_session_state where account_id=? and peer_id not in ({placeholders})",
                tuple([account_id] + peer_ids),
            )
    changed = [x for x in items if x.get("changed")]
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "changed": changed,
        "changed_count": len(changed),
        "mode": "replace" if replace_mode else "merge",
        "source": (
            "pc_wechat_uia_sessions_passive"
            if passive
            else "pc_wechat_uia_sessions_recent"
            if recent_only
            else "pc_wechat_uia_sessions"
        ),
        "passive": passive,
        "recent_only": recent_only,
        "scroll_rounds": int(scan.get("rounds") or 0),
        "scroll_completed": bool(scan.get("completed")),
    }


def _sync_local_sessions_once(
    account_id: str,
    *,
    passive: bool = False,
    recent_only: bool = False,
) -> Dict[str, Any]:
    init_db()
    _find_local_account(account_id)
    try:
        return _sync_local_sessions_from_uia(account_id, passive=passive, recent_only=recent_only)
    except Exception as uia_exc:
        if passive:
            raise
        try:
            data = _sync_local_sessions_from_wxauto4(account_id)
            data["uia_error"] = str(uia_exc)
            return data
        except Exception as wx_exc:
            raise RuntimeError(
                f"未读取到微信左侧会话；UIA={uia_exc}；wxauto4 fallback={wx_exc}"
            ) from wx_exc


def sync_local_sessions(
    account_id: str,
    *,
    passive: bool = False,
    recent_only: bool = False,
) -> Dict[str, Any]:
    return _run_local_driver_operation(
        account_id,
        "读取微信会话",
        lambda: _sync_local_sessions_once(account_id, passive=passive, recent_only=recent_only),
        retry_on_failure=not passive,
    )


def _sync_local_contacts_from_uia(account_id: str, *, limit: int = 10000) -> Dict[str, Any]:
    if not _module_available("uiautomation"):
        raise RuntimeError("uiautomation is required to sync local contacts")
    import uiautomation as auto  # type: ignore

    clean_limit = max(1, min(int(limit or 10000), 10000))
    hwnd = _ensure_local_contacts_tab(account_id)
    if not hwnd:
        raise RuntimeError("local WeChat window not found")
    root = auto.ControlFromHandle(int(hwnd))
    contact_list = None
    queue = [(root, 0)]
    while queue:
        node, depth = queue.pop(0)
        if _uia_control_class(node) == "mmui::StickyHeaderRecyclerListView" and _uia_control_text(node) == "\u901a\u8baf\u5f55":
            contact_list = node
            break
        if depth < 14:
            try:
                queue.extend((child, depth + 1) for child in node.GetChildren())
            except Exception:
                pass
    if contact_list is None:
        contact_list = _uia_guess_contact_list(root)
    if contact_list is None:
        contact_list = _uia_primary_contact_list(root)
    if contact_list is None:
        raise RuntimeError("local WeChat contact list not found")

    try:
        contact_list.WheelUp(wheelTimes=40)
        time.sleep(0.35)
    except Exception:
        pass

    seen: Dict[str, Dict[str, Any]] = {}
    # Do not initialize wxauto4 from this path. On unsupported WeChat builds
    # its constructor switches the window back to the chat tab before raising,
    # invalidating the Contacts UIA tree. The UIA profile card is the reliable
    # source for wx ids when the full driver API is unavailable.
    wx_no_index: Dict[str, str] = {}
    stable_rounds = 0
    last_signature = ""
    completed = False
    hit_limit = False
    max_rounds = _contact_sync_max_rounds(clean_limit)
    skip_names = _wechat_contact_skip_names()
    profile_fallback_attempts = 0
    profile_fallback_successes = 0
    profile_fallback_failures = 0
    profile_attempted: set[str] = set()
    rounds = 0
    for _idx in range(max_rounds):
        rounds = _idx + 1
        try:
            root = auto.ControlFromHandle(int(hwnd))
            refreshed = _uia_guess_contact_list(root) or _uia_primary_contact_list(root)
            if refreshed is not None:
                contact_list = refreshed
        except Exception:
            pass
        before = len(seen)
        names = _uia_visible_contact_cell_names(contact_list)
        for name in names:
            clean = str(name or "").strip()
            if not clean or clean in skip_names:
                continue
            wx_no = wx_no_index.get(_normalize_contact_lookup_key(clean), "")
            normalized_name = _normalize_contact_lookup_key(clean)
            if not wx_no and normalized_name and normalized_name not in profile_attempted:
                profile_attempted.add(normalized_name)
                profile_fallback_attempts += 1
                try:
                    wx_no = _read_visible_contact_profile_wx_no(hwnd, account_id, clean)
                    if wx_no:
                        profile_fallback_successes += 1
                        wx_no_index[normalized_name] = wx_no
                    else:
                        profile_fallback_failures += 1
                except Exception:
                    profile_fallback_failures += 1
            seen[clean] = {
                "contact_key": clean,
                "display_name": clean,
                "name": clean,
                "wxNo": wx_no,
                "source": "pc_wechat_uia_contacts",
                "raw": {
                    "source": "pc_wechat_uia_contacts",
                    "wxNo": wx_no,
                },
            }
            if len(seen) >= clean_limit:
                hit_limit = True
                break
        if hit_limit:
            break
        signature = "|".join(names[-16:])
        stable_rounds = stable_rounds + 1 if len(seen) == before and signature == last_signature else 0
        last_signature = signature
        if stable_rounds >= 6:
            completed = True
            break
        try:
            contact_list.WheelDown(wheelTimes=5)
            time.sleep(0.2)
        except Exception:
            break

    if not seen:
        raise RuntimeError("未读取到通讯录联系人，请确认微信已切到通讯录页后重试")
    existing_before = _contact_count(account_id)
    items = _merge_contacts_snapshot(account_id, list(seen.values()), chat_type="direct")
    total_after = _contact_count(account_id)
    return {
        "ok": True,
        "items": items[:200],
        "count": len(items),
        "total_after": total_after,
        "existing_before": existing_before,
        "source": "pc_wechat_uia_contacts",
        "mode": "merge",
        "scroll_completed": completed,
        "limit_reached": hit_limit,
        "rounds": rounds,
        "max_rounds": max_rounds,
        "partial": (not completed and not hit_limit),
        "wx_no_source": "uia_contact_profile",
        "profile_fallback_attempts": profile_fallback_attempts,
        "profile_fallback_successes": profile_fallback_successes,
        "profile_fallback_failures": profile_fallback_failures,
    }


def sync_local_contacts_legacy(account_id: str, *, limit: int = 10000) -> Dict[str, Any]:
    init_db()
    _find_local_account(account_id)
    try:
        wx = _get_wxauto4_client(account_id, ensure_chat_tab=False)
    except Exception as exc:
        raise RuntimeError(f"完整通讯录驱动不可用：{exc}") from exc
    if hasattr(wx, "GetFriendDetails"):
        contacts = wx.GetFriendDetails(n=limit, timeout=max(30, min(int(limit), 600)))
        existing_before = _contact_count(account_id)
        items = _merge_contacts_snapshot(
            account_id,
            [{**_obj_dict(x), "source": "wx_driver_contacts"} for x in contacts or []],
            chat_type="direct",
        )
        return {
            "ok": True,
            "items": items[:200],
            "count": len(items),
            "total_after": _contact_count(account_id),
            "existing_before": existing_before,
            "mode": "merge",
        }
    raise RuntimeError("当前驱动不支持完整通讯录同步")


def sync_local_contacts(account_id: str, *, limit: int = 10000) -> Dict[str, Any]:
    init_db()
    _find_local_account(account_id)
    # Contact sync is intentionally UIA-only. wxauto4's constructor can move
    # unsupported WeChat builds to the chat tab before reporting that its full
    # contacts API is unavailable, which corrupts this workflow's UI state.
    return _run_local_driver_operation(
        account_id,
        "同步通讯录",
        lambda: _sync_local_contacts_from_uia(account_id, limit=limit),
        retry_on_failure=False,
    )


def _normalize_contact_lookup_key(value: Any) -> str:
    return _compact_for_contains(str(value or "")).lower()


def _build_local_contact_wx_no_index(limit: int, *, account_id: str = "") -> Dict[str, str]:
    try:
        wx = _get_wxauto4_client(account_id, ensure_chat_tab=False)
    except Exception:
        return {}
    if not hasattr(wx, "GetFriendDetails"):
        return {}
    try:
        contacts = wx.GetFriendDetails(n=limit, timeout=max(30, min(int(limit), 600)))
    except Exception:
        return {}
    index: Dict[str, str] = {}
    for raw in contacts or []:
        item = _obj_dict(raw)
        wx_no = str(item.get("wxNo") or item.get("wx_no") or item.get("username") or item.get("contact_key") or "").strip()
        if not wx_no:
            continue
        for alias in (
            item.get("display_name"),
            item.get("nickname"),
            item.get("nickName"),
            item.get("remark"),
            item.get("name"),
            item.get("contact_key"),
            item.get("wxNo"),
            item.get("wx_no"),
            item.get("username"),
            item.get("id"),
        ):
            key = _normalize_contact_lookup_key(alias)
            if key and key not in index:
                index[key] = wx_no
    return index


def _extract_contact_profile_wx_no(root: Any) -> str:
    entries: List[tuple[str, str]] = []
    for node in _uia_walk(root, max_depth=20, max_nodes=5000):
        text = _uia_control_text(node)
        if text:
            entries.append((text, _uia_control_class(node)))
    texts = [text for text, _class_name in entries]
    label_seen = False
    profile_started = False
    for idx, text in enumerate(texts):
        class_name = entries[idx][1]
        if class_name.endswith("ContactHeadView"):
            profile_started = True
            continue
        compact = _compact_for_contains(text)
        match = re.search(r"微信号[:：]\s*([^\s]+)", compact)
        if match:
            return str(match.group(1) or "").strip()
        if compact in {"微信号", "微信號"} or compact.startswith("微信号") or compact.startswith("微信號"):
            label_seen = True
            continue
        if label_seen:
            candidate = str(text or "").strip()
            if any(marker in candidate for marker in ("地区", "地區", "备注", "朋友圈", "视频号", "共同群聊")):
                label_seen = False
                continue
            if candidate and class_name.endswith("ContactProfileTextView"):
                return candidate
        if profile_started and class_name.endswith("ContactProfileTextView"):
            candidate = str(text or "").strip()
            if candidate and not any("\u4e00" <= char <= "\u9fff" for char in candidate):
                return candidate
    return ""


def _persist_contact_wx_no(account_id: str, target: str, wx_no: str, *, source: str = "pc_wechat_uia_contact_profile") -> Dict[str, Any]:
    account_id = str(account_id or "").strip()
    target = str(target or "").strip()
    wx_no = str(wx_no or "").strip()
    if not account_id or not target or not wx_no:
        return {}
    with _connect() as conn:
        row = conn.execute(
            """
            select * from wechat_contacts
            where account_id=? and (contact_key=? or display_name=? or remark=? or wx_no=?)
            order by updated_at desc, id desc
            limit 1
            """,
            (account_id, target, target, target, target),
        ).fetchone()
    base = _row_to_dict(row) if row else {}
    contact = dict(base)
    contact["contact_key"] = str(contact.get("contact_key") or target).strip()
    contact["display_name"] = str(contact.get("display_name") or target).strip()
    contact["remark"] = str(contact.get("remark") or "").strip()
    contact["wxNo"] = wx_no
    contact["wx_no"] = wx_no
    contact["source"] = source or str(contact.get("source") or "local")
    return _persist_contact(account_id, contact)


def _resolve_local_contact_aliases(account_id: str, target: str) -> List[str]:
    target = str(target or "").strip()
    if not target:
        return []
    aliases: List[str] = [target]
    try:
        with _connect() as conn:
            row = conn.execute(
                """
                select contact_key, display_name, remark, wx_no
                from wechat_contacts
                where account_id=? and (contact_key=? or display_name=? or remark=? or wx_no=?)
                order by updated_at desc, id desc
                limit 1
                """,
                (account_id, target, target, target, target),
            ).fetchone()
    except Exception:
        row = None
    if row:
        for value in (row["wx_no"], row["contact_key"], row["display_name"], row["remark"]):
            text = str(value or "").strip()
            if text and text not in aliases:
                aliases.append(text)
    return aliases


def _looks_like_wechat_id(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{5,}", text))


def _resolve_local_contact_wx_no(account_id: str, target: str) -> str:
    """Resolve a contact's stable WeChat ID; never turn a nickname into an ID."""
    account_id = str(account_id or "").strip()
    target = str(target or "").strip()
    if not account_id or not target:
        return ""
    try:
        with _connect() as conn:
            row = conn.execute(
                """
                select wx_no
                from wechat_contacts
                where account_id=? and (contact_key=? or display_name=? or remark=? or wx_no=?)
                order by case when wx_no=? then 0 else 1 end, updated_at desc, id desc
                limit 1
                """,
                (account_id, target, target, target, target, target),
            ).fetchone()
    except Exception:
        row = None
    wx_no = str(row["wx_no"] if row else "").strip()
    if wx_no:
        return wx_no
    # A value already shaped like a WeChat ID is safe to pass through even if
    # this contact has not been synced into the local contact table yet.
    return target if _looks_like_wechat_id(target) else ""


def _uia_contact_recycler_lists(root: Any) -> List[Any]:
    return [
        node
        for node in _uia_walk(root, max_depth=16, max_nodes=1800)
        if _uia_control_class(node) == "mmui::StickyHeaderRecyclerListView"
    ]


def _uia_contact_list_score(node: Any) -> int:
    if _uia_rect(node) is None:
        return -1000
    score = 0
    text = _uia_control_text(node)
    if "通讯录" in text:
        score += 200
    if "联系人" in text:
        score += 120
    try:
        children = node.GetChildren()
    except Exception:
        children = []
    contact_cells = 0
    queue = list(children[:80])
    seen_ids: set[int] = set()
    while queue and contact_cells < 24:
        child = queue.pop(0)
        child_id = id(child)
        if child_id in seen_ids:
            continue
        seen_ids.add(child_id)
        class_name = _uia_control_class(child)
        if class_name == "mmui::ContactsCellItemView" or class_name.endswith("ContactsCellItemView"):
            contact_cells += 1
        try:
            grand_children = child.GetChildren()
        except Exception:
            grand_children = []
        if grand_children:
            queue.extend(grand_children[:20])
    score += min(contact_cells, 20) * 10
    return score


def _uia_guess_contact_list(root: Any) -> Optional[Any]:
    candidates: List[tuple[int, Any]] = []
    for node in _uia_contact_recycler_lists(root):
        score = _uia_contact_list_score(node)
        if score > 0:
            candidates.append((score, node))
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    for node in _uia_walk(root, max_depth=16, max_nodes=1800):
        class_name = _uia_control_class(node)
        if class_name.endswith("RecyclerView") or class_name.endswith("ListView"):
            score = _uia_contact_list_score(node)
            if score > 0:
                candidates.append((score, node))
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    return None


def _uia_parent_contact_cell(node: Any) -> Optional[Any]:
    cur = node
    for _idx in range(8):
        if cur is None:
            break
        if _uia_control_class(cur) == "mmui::ContactsCellItemView":
            return cur
        try:
            cur = cur.GetParentControl()
        except Exception:
            break
    return None


def _uia_find_contact_entry(root: Any, names: List[str]) -> Optional[Any]:
    wanted = [str(name or "").strip() for name in names if str(name or "").strip()]
    if not wanted:
        return None
    fallback: Optional[Any] = None
    for node in _uia_walk(root, max_depth=18, max_nodes=1800):
        text = _uia_control_text(node)
        if not text or not any(name in text for name in wanted):
            continue
        cell = _uia_parent_contact_cell(node)
        if cell is not None:
            return cell
        if fallback is None:
            fallback = node
    return fallback


def _uia_primary_contact_list(root: Any) -> Optional[Any]:
    lists = _uia_contact_recycler_lists(root)
    if not lists:
        return None
    return next((item for item in lists if "\u901a\u8baf\u5f55" in _uia_control_text(item)), lists[0])


def _uia_open_contact_entry_by_scroll(hwnd: int, names: List[str], *, max_scrolls: int = 12) -> Any:
    import uiautomation as auto  # type: ignore

    root = auto.ControlFromHandle(int(hwnd))
    contact_list = _uia_primary_contact_list(root)
    if contact_list is not None:
        try:
            contact_list.WheelUp(wheelTimes=30)
            time.sleep(0.35)
        except Exception:
            pass
    last_signature = ""
    stable_rounds = 0
    for _idx in range(max(1, int(max_scrolls))):
        root = auto.ControlFromHandle(int(hwnd))
        entry = _uia_find_contact_entry(root, names)
        if entry is not None:
            _uia_click(entry)
            time.sleep(0.8)
            return entry
        contact_list = _uia_primary_contact_list(root) or contact_list
        names_visible = _uia_visible_contact_cell_names(contact_list) if contact_list is not None else []
        signature = "|".join(names_visible[-12:])
        stable_rounds = stable_rounds + 1 if signature == last_signature else 0
        last_signature = signature
        if stable_rounds >= 3:
            break
        if contact_list is None:
            break
        try:
            contact_list.WheelDown(wheelTimes=4)
            time.sleep(0.25)
        except Exception:
            break
    raise RuntimeError("local WeChat contact entry not found: " + ",".join(names))


def _uia_visible_contact_cell_names(list_node: Any) -> List[str]:
    names: List[str] = []
    try:
        children = list_node.GetChildren()
    except Exception:
        children = []
    queue = list(children)
    seen_ids: set[int] = set()
    while queue and len(names) < 120:
        child = queue.pop(0)
        child_id = id(child)
        if child_id in seen_ids:
            continue
        seen_ids.add(child_id)
        if _uia_control_class(child) == "mmui::ContactsCellItemView":
            name = _uia_control_text(child)
            if name:
                names.append(name)
        try:
            grand_children = child.GetChildren()
        except Exception:
            grand_children = []
        if grand_children:
            queue.extend(grand_children[:20])
    return names


def _sync_local_groups_from_uia(account_id: str, *, limit: int = 2000) -> Dict[str, Any]:
    if not _module_available("uiautomation"):
        raise RuntimeError("uiautomation is required to sync local groups")
    import uiautomation as auto  # type: ignore

    hwnd = _ensure_local_contacts_tab(account_id)
    if not hwnd:
        raise RuntimeError("local WeChat window not found")
    try:
        _uia_open_contact_entry_by_scroll(hwnd, ["\u7fa4\u804a"], max_scrolls=12)
    except Exception as entry_exc:
        return _sync_local_groups_from_all_sessions(account_id, limit=limit, reason=str(entry_exc))

    root = auto.ControlFromHandle(int(hwnd))
    lists = _uia_contact_recycler_lists(root)
    if not lists:
        raise RuntimeError("local WeChat group list not found")
    group_list = next((item for item in lists if "\u7fa4\u804a" in _uia_control_text(item)), lists[0])
    try:
        group_list.WheelUp(wheelTimes=30)
        time.sleep(0.35)
    except Exception:
        pass

    skip_names = {
        "\u65b0\u7684\u670b\u53cb",
        "\u7fa4\u804a",
        "\u6807\u7b7e",
        "\u516c\u4f17\u53f7",
        "\u4f01\u4e1a\u5fae\u4fe1\u8054\u7cfb\u4eba",
        "\u4ec5\u804a\u5929\u7684\u670b\u53cb",
    }
    seen: Dict[str, Dict[str, Any]] = {}
    stable_rounds = 0
    last_signature = ""
    max_rounds = max(8, min(100, int(limit / 8) + 8))
    for _idx in range(max_rounds):
        root = auto.ControlFromHandle(int(hwnd))
        lists = _uia_contact_recycler_lists(root)
        if lists:
            group_list = next((item for item in lists if "\u7fa4\u804a" in _uia_control_text(item)), lists[0])
        names = _uia_visible_contact_cell_names(group_list)
        before = len(seen)
        for name in names:
            clean = str(name or "").strip()
            if not clean or clean in skip_names:
                continue
            seen[clean] = {
                "group_key": clean,
                "display_name": clean,
                "source": "pc_wechat_uia_groups",
                "raw": {"name": clean, "source": "pc_wechat_uia_groups"},
            }
            if len(seen) >= limit:
                break
        if len(seen) >= limit:
            break
        signature = "|".join(names[-12:])
        if len(seen) == before and signature == last_signature:
            stable_rounds += 1
        else:
            stable_rounds = 0
        last_signature = signature
        if stable_rounds >= 3:
            break
        try:
            group_list.WheelDown(wheelTimes=4)
            time.sleep(0.25)
        except Exception:
            break

    if not seen:
        raise RuntimeError("local WeChat group list is empty or unreadable")
    items = _replace_groups_snapshot(account_id, list(seen.values()))
    _ensure_local_chat_tab(account_id)
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "source": "pc_wechat_uia_groups",
        "mode": "replace",
    }


def _sync_local_groups_from_all_sessions(account_id: str, *, limit: int = 2000, reason: str = "") -> Dict[str, Any]:
    hwnd = _local_wechat_hwnd(account_id)
    if not hwnd:
        raise RuntimeError("local WeChat window not found")
    _ensure_local_chat_tab(account_id)
    scan = _uia_collect_all_sessions(hwnd)
    sessions = list(scan.get("items") or [])
    if not sessions:
        raise RuntimeError("local WeChat session list is empty or unreadable")
    wx = _get_wxauto4_client(account_id)
    groups: List[Dict[str, Any]] = []
    skipped_unread: List[str] = []
    checked = 0
    for session in sessions:
        if checked >= max(1, int(limit)):
            break
        peer_id = str(session.get("peer_id") or "").strip()
        if not peer_id:
            continue
        _persist_session(account_id, session, chat_type="unknown")
        if int(session.get("unread_count") or 0) > 0 or bool(session.get("is_new")):
            skipped_unread.append(peer_id)
            continue
        checked += 1
        try:
            wx.ChatWith(peer_id, exact=True, force=False)
            time.sleep(random.uniform(0.35, 0.7))
            info = wx.ChatInfo() if hasattr(wx, "ChatInfo") else {}
            chat_type = str((info or {}).get("chat_type") or "unknown")
            _persist_session(account_id, session, chat_type=chat_type)
            if chat_type == "group":
                saved = _persist_group(
                    account_id,
                    {
                        "group_key": peer_id,
                        "display_name": str((info or {}).get("chat_name") or peer_id),
                        "member_count": int((info or {}).get("group_member_count") or 0),
                        "source": "pc_wechat_session_scan_groups",
                        "raw": info or {},
                    },
                )
                if saved:
                    groups.append(saved)
        except Exception:
            continue
    return {
        "ok": True,
        "items": groups,
        "count": len(groups),
        "source": "pc_wechat_session_scan_groups",
        "mode": "merge",
        "session_count": len(sessions),
        "checked_count": checked,
        "skipped_unread": skipped_unread,
        "scroll_rounds": int(scan.get("rounds") or 0),
        "scroll_completed": bool(scan.get("completed")),
        "fallback_reason": reason,
        "message": "synced groups by scanning session list",
    }


def sync_local_groups_legacy(account_id: str) -> Dict[str, Any]:
    init_db()
    _find_local_account(account_id)
    wx = _get_wxauto4_client(account_id)
    if hasattr(wx, "GetContactGroups"):
        groups = wx.GetContactGroups()
        items = [_persist_group(account_id, {"group_key": str(x), "display_name": str(x), "source": "wx_driver_groups"}) for x in groups or []]
        return {"ok": True, "items": [x for x in items if x], "count": len([x for x in items if x])}
    if hasattr(wx, "GetAllRecentGroups"):
        groups = wx.GetAllRecentGroups()
        if isinstance(groups, dict) and not bool(groups):
            raise RuntimeError(str(groups))
        items = [_persist_group(account_id, {"group_key": str(x), "display_name": str(x), "source": "wx_driver_recent_groups"}) for x in groups or []]
        return {"ok": True, "items": [x for x in items if x], "count": len([x for x in items if x])}
    raise RuntimeError("当前驱动不支持群列表同步")


def sync_local_groups(account_id: str, *, limit: int = 2000) -> Dict[str, Any]:
    init_db()
    _find_local_account(account_id)
    return _sync_local_groups_from_all_sessions(account_id, limit=limit, reason="session_list")


def _normalize_local_group_members(members: List[Any], *, source: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for member in members or []:
        raw = _obj_dict(member)
        name = str(_obj_value(member, "name", "nickname", "remark", "display_name") or member or "").strip()
        if not name:
            continue
        member_key = str(
            _obj_value(member, "wxid", "user_id", "username", "id", "member_key") or name
        ).strip()
        if not member_key or member_key in seen:
            continue
        seen.add(member_key)
        items.append(
            {
                "member_key": member_key,
                "display_name": name,
                "raw": {**raw, "source": source},
            }
        )
    return items


def _persist_local_group_members(
    account_id: str,
    group_key: str,
    items: List[Dict[str, Any]],
    *,
    replace: bool,
) -> List[Dict[str, Any]]:
    now = _now_iso()
    saved: List[Dict[str, Any]] = []
    with _connect() as conn:
        if replace:
            conn.execute(
                "delete from wechat_group_members where account_id=? and group_key=?",
                (account_id, group_key),
            )
        for item in items:
            member_key = str(item.get("member_key") or "").strip()
            display_name = str(item.get("display_name") or member_key).strip()
            if not member_key or not display_name:
                continue
            raw = dict(item.get("raw") or {})
            conn.execute(
                """
                insert into wechat_group_members(id, account_id, group_key, member_key, display_name, raw_json, created_at, updated_at)
                values(?,?,?,?,?,?,?,?)
                on conflict(account_id, group_key, member_key) do update set
                  display_name=excluded.display_name,
                  raw_json=excluded.raw_json,
                  updated_at=excluded.updated_at
                """,
                (
                    _stable_key(account_id, group_key, member_key),
                    account_id,
                    group_key,
                    member_key,
                    display_name,
                    _json_dumps(raw),
                    now,
                    now,
                ),
            )
            saved.append({"member_key": member_key, "display_name": display_name, "raw": raw})
    return saved


def _uia_group_member_grid(root: Any) -> Optional[Any]:
    fallback: Optional[Any] = None
    for node in _uia_walk(root, max_depth=24, max_nodes=3000):
        if _uia_control_class(node) != "QFReuseGridWidget":
            continue
        if _uia_control_text(node) == "\u804a\u5929\u6210\u5458":
            return node
        fallback = fallback or node
    return fallback


def _uia_visible_group_member_items(root: Any) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for node in _uia_walk(root, max_depth=24, max_nodes=3000):
        if _uia_control_class(node) != "mmui::ChatMemberCell":
            continue
        name = _uia_control_text(node)
        if not name or name in seen:
            continue
        seen.add(name)
        items.append(
            {
                "member_key": name,
                "display_name": name,
                "raw": {
                    "source": "pc_wechat_uia_group_members",
                    "class_name": _uia_control_class(node),
                },
            }
        )
    return items


def _uia_click_control_center(node: Any) -> None:
    rect = _uia_rect_tuple(node)
    if rect is None:
        raise RuntimeError("local WeChat control has no clickable bounds")
    left, top, right, bottom = rect
    _uia_click_screen_point((left + right) // 2, (top + bottom) // 2)


def _sync_local_group_members_from_uia(
    account_id: str,
    group_key: str,
    *,
    wx: Any,
) -> Dict[str, Any]:
    if not _module_available("uiautomation"):
        raise RuntimeError("uiautomation is required to read local WeChat group members")
    import uiautomation as auto  # type: ignore

    hwnd = _local_wechat_hwnd(account_id)
    if not hwnd:
        raise RuntimeError("local WeChat window not found")
    _focus_local_wechat(hwnd)
    time.sleep(0.35)
    root = auto.ControlFromHandle(int(hwnd))
    opened_panel = False
    grid = _uia_group_member_grid(root)
    if grid is None:
        button = root.ButtonControl(Name="\u804a\u5929\u4fe1\u606f")
        if not button.Exists(2.0):
            raise RuntimeError("local WeChat chat info button not found")
        _uia_click_control_center(button)
        opened_panel = True
        deadline = time.time() + 6.0
        while time.time() < deadline:
            root = auto.ControlFromHandle(int(hwnd))
            grid = _uia_group_member_grid(root)
            if grid is not None:
                break
            time.sleep(0.25)
    if grid is None:
        raise RuntimeError("local WeChat group member panel did not open")

    info = wx.ChatInfo() if hasattr(wx, "ChatInfo") else {}
    try:
        expected_count = max(0, int((info or {}).get("group_member_count") or 0))
    except (TypeError, ValueError):
        expected_count = 0

    try:
        grid.WheelUp(wheelTimes=40)
        time.sleep(0.35)
    except Exception:
        pass

    ordered: Dict[str, Dict[str, Any]] = {}
    stable_rounds = 0
    last_signature = ""
    rounds = 0
    completed = False
    max_rounds = max(12, min(240, (expected_count // 6) + 24 if expected_count else 80))
    for index in range(max_rounds):
        rounds = index + 1
        root = auto.ControlFromHandle(int(hwnd))
        visible = _uia_visible_group_member_items(root)
        before = len(ordered)
        for item in visible:
            ordered.setdefault(str(item.get("member_key") or ""), item)
        signature = "|".join(str(item.get("member_key") or "") for item in visible)
        if expected_count and len(ordered) >= expected_count:
            completed = True
            break
        if len(ordered) == before and signature == last_signature:
            stable_rounds += 1
        else:
            stable_rounds = 0
        last_signature = signature
        if stable_rounds >= 3:
            completed = True
            break
        grid = _uia_group_member_grid(root) or grid
        try:
            grid.WheelDown(wheelTimes=4)
            time.sleep(0.25)
        except Exception:
            break

    items = [item for key, item in ordered.items() if key]
    if not items:
        raise RuntimeError("no group members were readable from local WeChat")

    if opened_panel:
        try:
            root = auto.ControlFromHandle(int(hwnd))
            button = root.ButtonControl(Name="\u804a\u5929\u4fe1\u606f")
            if button.Exists(1.0):
                _uia_click_control_center(button)
        except Exception:
            pass

    return {
        "items": items,
        "count": len(items),
        "expected_count": expected_count,
        "scroll_rounds": rounds,
        "scroll_completed": completed,
        "source": "pc_wechat_uia_group_members",
    }


def sync_local_group_members(account_id: str, group_key: str) -> Dict[str, Any]:
    init_db()
    _find_local_account(account_id)
    group_key = str(group_key or "").strip()
    if not group_key:
        raise RuntimeError("缺少群名称")
    wx = _get_wxauto4_client(account_id)
    wx.ChatWith(group_key, exact=True, force=False)
    time.sleep(random.uniform(0.45, 0.8))
    if hasattr(wx, "GetGroupMembers"):
        items = _normalize_local_group_members(
            list(wx.GetGroupMembers() or []),
            source="wx_driver_group_members",
        )
        saved = _persist_local_group_members(account_id, group_key, items, replace=True)
        return {
            "ok": True,
            "items": saved,
            "count": len(saved),
            "source": "wx_driver_group_members",
            "scroll_completed": True,
        }

    scan = _sync_local_group_members_from_uia(account_id, group_key, wx=wx)
    saved = _persist_local_group_members(
        account_id,
        group_key,
        list(scan.get("items") or []),
        replace=bool(scan.get("scroll_completed")),
    )
    return {"ok": True, **scan, "items": saved, "count": len(saved)}


def sync_local_messages_legacy(account_id: str) -> Dict[str, Any]:
    init_db()
    _find_local_account(account_id)
    wx = _get_wxauto4_client(account_id)
    items: List[Dict[str, Any]] = []
    if hasattr(wx, "GetNextNewMessage"):
        data = wx.GetNextNewMessage(filter_mute=False) or {}
        if isinstance(data, dict):
            for peer_id, messages in data.items():
                _persist_contact(account_id, {"contact_key": str(peer_id), "display_name": str(peer_id), "source": "wx_driver_message"})
                for msg in messages or []:
                    items.append(_persist_message_obj(account_id, str(peer_id), msg))
    elif hasattr(wx, "GetAllMessage"):
        info = wx.ChatInfo() if hasattr(wx, "ChatInfo") else {}
        peer_id = str((info or {}).get("name") or (info or {}).get("nickname") or "current")
        for msg in wx.GetAllMessage() or []:
            items.append(_persist_message_obj(account_id, peer_id, msg))
    else:
        raise RuntimeError("当前驱动不支持消息读取")
    return {"ok": True, "items": items, "count": len(items)}


def _sync_local_messages_once(
    account_id: str,
    peer_id: str = "",
    *,
    load_more_pages: int = 0,
    select_via_uia: bool = False,
) -> Dict[str, Any]:
    init_db()
    _find_local_account(account_id)
    wx = _get_wxauto4_client(account_id)
    target = str(peer_id or "").strip()
    if target:
        if select_via_uia:
            _open_local_session_by_uia(account_id, target)
        else:
            wx.ChatWith(target, exact=True, force=False)
            time.sleep(random.uniform(0.45, 0.9))
    info = wx.ChatInfo() if hasattr(wx, "ChatInfo") else {}
    actual_peer = str((info or {}).get("chat_name") or target or "").strip() or "current"
    _persist_peer_chat_info(account_id, actual_peer, info or {"chat_name": actual_peer, "chat_type": "unknown"})
    previous_latest = _latest_message_record(account_id, actual_peer)
    pages = max(0, min(int(load_more_pages or 0), 3))
    for _idx in range(pages):
        if hasattr(wx, "LoadMoreCache"):
            try:
                wx.LoadMoreCache()
                time.sleep(random.uniform(0.5, 0.9))
            except Exception:
                break
    messages = wx.GetAllMessage() if hasattr(wx, "GetAllMessage") else []
    items = [_persist_message_obj(account_id, actual_peer, msg) for msg in (messages or [])]
    inserted = [
        x for x in items
        if not x.get("deduped") and x.get("direction") != "system" and x.get("msg_type") != "time"
    ]
    latest = _latest_message_record(account_id, actual_peer)
    if latest:
        _persist_session(
            account_id,
            {
                "peer_id": actual_peer,
                "display_name": str((info or {}).get("chat_name") or actual_peer),
                "last_content": latest.get("content") or "",
                "session_time": latest.get("created_at") or "",
                "unread_count": 0,
                "is_new": False,
                "clear_unread": True,
                "raw": {"source": "current_selected_message_sync", "chat_info": info or {}},
            },
            chat_type=str((info or {}).get("chat_type") or "unknown"),
        )
    has_new_message = _message_compare_key(previous_latest) != _message_compare_key(latest)
    if previous_latest is None and latest is None:
        has_new_message = False
    return {
        "ok": True,
        "peer_id": actual_peer,
        "chat_info": info or {},
        "items": inserted,
        "count": len(inserted),
        "new_message_count": len(inserted),
        "has_new_message": bool(has_new_message),
        "previous_latest_message": previous_latest,
        "latest_message": latest,
        "current_selected": not bool(target),
        "seen_count": len(items),
        "deduped_count": len(items) - len(inserted),
    }


def sync_local_messages(
    account_id: str,
    peer_id: str = "",
    *,
    load_more_pages: int = 0,
    select_via_uia: bool = False,
) -> Dict[str, Any]:
    return _run_local_driver_operation(
        account_id,
        "读取微信消息",
        lambda: _sync_local_messages_once(
            account_id,
            peer_id,
            load_more_pages=load_more_pages,
            select_via_uia=select_via_uia,
        ),
    )


def _uia_walk(root: Any, *, max_depth: int = 16, max_nodes: int = 600) -> List[Any]:
    nodes: List[Any] = []
    queue = [(root, 0)]
    while queue and len(nodes) < max_nodes:
        node, depth = queue.pop(0)
        nodes.append(node)
        if depth >= max_depth:
            continue
        try:
            children = node.GetChildren()
        except Exception:
            children = []
        queue.extend((child, depth + 1) for child in children)
    return nodes


def _uia_find_by_names(root: Any, names: List[str], *, contains: bool = False, max_depth: int = 16) -> Optional[Any]:
    wanted = [str(x or "").strip() for x in names if str(x or "").strip()]
    if not wanted:
        return None
    for node in _uia_walk(root, max_depth=max_depth):
        name = _uia_control_text(node)
        if not name:
            continue
        if any((target in name if contains else name == target) for target in wanted):
            return node
    return None


def _uia_click(node: Any) -> None:
    try:
        node.Click(simulateMove=True)
    except Exception:
        node.Click(simulateMove=False)
    _human_pause(floor=0.45)


def _uia_foreground_or_main_root(hwnd: int) -> Any:
    import uiautomation as auto  # type: ignore

    try:
        import win32gui  # type: ignore
        import win32process  # type: ignore

        fg = int(win32gui.GetForegroundWindow() or 0)
        target = int(hwnd or 0)
        if fg and target:
            target_pid = int(win32process.GetWindowThreadProcessId(target)[1] or 0)
            foreground_pid = int(win32process.GetWindowThreadProcessId(fg)[1] or 0)
            if target_pid and foreground_pid and target_pid != foreground_pid:
                fg = 0
    except Exception:
        fg = 0
    return auto.ControlFromHandle(fg or int(hwnd))


def _uia_main_root(hwnd: int) -> Any:
    import uiautomation as auto  # type: ignore

    return auto.ControlFromHandle(int(hwnd or 0))


def _uia_wait_for_names(hwnd: int, names: List[str], *, timeout: float = 8.0, contains: bool = False) -> Optional[Any]:
    deadline = time.time() + max(0.5, timeout)
    while time.time() < deadline:
        for root in (_uia_foreground_or_main_root(hwnd),):
            node = _uia_find_by_names(root, names, contains=contains, max_depth=18)
            if node is not None:
                return node
        time.sleep(0.35)
    return None


def _uia_edit_controls(root: Any) -> List[Any]:
    edits: List[Any] = []
    for node in _uia_walk(root, max_depth=18):
        class_name = _uia_control_class(node)
        control_type = str(getattr(node, "ControlTypeName", "") or "")
        if "Edit" in class_name or "Edit" in control_type:
            edits.append(node)
    return edits


def _uia_set_text(node: Any, text: str) -> None:
    try:
        node.SetFocus()
    except Exception:
        pass
    try:
        node.Click(simulateMove=True)
    except Exception:
        pass
    _human_pause("ui_input_sleep_min", "ui_input_sleep_max", floor=0.12)
    value = str(text or "")
    if _uia_try_set_value(node, value):
        return
    _send_hotkey("a", ctrl=True, pause=0.08)
    _paste_text(value)


def _uia_try_set_value(node: Any, text: str) -> bool:
    for getter in ("GetValuePattern", "ValuePattern"):
        try:
            pattern = getattr(node, getter)
            pattern = pattern() if callable(pattern) else pattern
            if pattern is None:
                continue
            setter = getattr(pattern, "SetValue", None)
            if callable(setter):
                setter(str(text or ""))
                time.sleep(0.15)
                return True
        except Exception:
            pass
    for setter_name in ("SetValue", "SetText"):
        try:
            setter = getattr(node, setter_name, None)
            if callable(setter):
                setter(str(text or ""))
                time.sleep(0.15)
                return True
        except Exception:
            pass
    return False


def _uia_visible_edit_controls(root: Any) -> List[Any]:
    visible: List[Any] = []
    edits = _uia_edit_controls(root)
    for node in edits:
        try:
            if bool(getattr(node, "IsOffscreen", False)):
                continue
        except Exception:
            pass
        visible.append(node)
    return visible or edits


def _uia_control_rect_score(node: Any) -> int:
    try:
        rect = getattr(node, "BoundingRectangle", None)
        if rect is None:
            return 0
        left = int(getattr(rect, "left", getattr(rect, "Left", 0)) or 0)
        right = int(getattr(rect, "right", getattr(rect, "Right", 0)) or 0)
        width = max(0, right - left)
    except Exception:
        return 0
    score = 0
    if left >= 220:
        score += 20
    if width >= 160:
        score += 8
    return score


def _uia_new_friend_search_edit_score(node: Any) -> int:
    name = _uia_control_text(node)
    strong_hints = ("微信号", "手机号", "QQ号", "邮箱", "账号")
    score = _uia_control_rect_score(node)
    if name and any(hint in name for hint in strong_hints):
        score += 100
    elif name and "搜索" in name:
        score += 5
    return score


def _uia_wait_for_edit(hwnd: int, *, timeout: float = 6.0) -> Optional[Any]:
    deadline = time.time() + max(0.5, timeout)
    fallback: Optional[Any] = None
    fallback_score = -1
    while time.time() < deadline:
        root = _uia_foreground_or_main_root(hwnd)
        edits = _uia_visible_edit_controls(root)
        for edit in edits:
            score = _uia_new_friend_search_edit_score(edit)
            if score >= 100:
                return edit
            if score > fallback_score:
                fallback = edit
                fallback_score = score
        time.sleep(0.25)
    return fallback


def _uia_click_first_named(
    hwnd: int,
    names: List[str],
    *,
    timeout: float = 5.0,
    contains: bool = False,
) -> Optional[Any]:
    deadline = time.time() + max(0.5, timeout)
    while time.time() < deadline:
        root = _uia_foreground_or_main_root(hwnd)
        node = _uia_find_by_names(root, names, contains=contains, max_depth=18)
        if node is not None:
            _uia_click(node)
            return node
        time.sleep(0.25)
    return None


def _uia_rect_tuple(node: Any) -> Optional[tuple[int, int, int, int]]:
    try:
        rect = getattr(node, "BoundingRectangle", None)
        if rect is None:
            return None
        left = int(getattr(rect, "left", getattr(rect, "Left", 0)) or 0)
        top = int(getattr(rect, "top", getattr(rect, "Top", 0)) or 0)
        right = int(getattr(rect, "right", getattr(rect, "Right", 0)) or 0)
        bottom = int(getattr(rect, "bottom", getattr(rect, "Bottom", 0)) or 0)
    except Exception:
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _uia_click_screen_point(x: int, y: int) -> None:
    try:
        import win32api  # type: ignore
        import win32con  # type: ignore
    except Exception as exc:
        raise RuntimeError("本机微信控制组件不可用：缺少 pywin32 鼠标模块") from exc
    win32api.SetCursorPos((int(x), int(y)))
    time.sleep(0.08)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, int(x), int(y), 0, 0)
    time.sleep(0.04)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, int(x), int(y), 0, 0)
    _human_pause(floor=0.45)


_FRIEND_REQUEST_ACCEPT_SUFFIXES = ("\u63a5\u53d7", "\u540c\u610f", "\u7b49\u5f85\u9a8c\u8bc1", "accept")
_FRIEND_REQUEST_FINISHED_SUFFIXES = (
    "\u5df2\u6dfb\u52a0",
    "\u5df2\u63a5\u53d7",
    "\u5df2\u540c\u610f",
    "\u5df2\u62d2\u7edd",
    "\u5df2\u8fc7\u671f",
    "added",
    "accepted",
    "declined",
    "expired",
)


def _friend_request_action_label(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "")).strip()
    lowered = text.lower()
    if not text or any(lowered.endswith(suffix.lower()) for suffix in _FRIEND_REQUEST_FINISHED_SUFFIXES):
        return ""
    for suffix in _FRIEND_REQUEST_ACCEPT_SUFFIXES:
        if lowered.endswith(suffix.lower()):
            return text[-len(suffix) :]
    return ""


def _friend_request_key(value: Any, action: str = "") -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if action and text.lower().endswith(action.lower()):
        text = text[: -len(action)].rstrip()
    return hashlib.sha1(text.encode("utf-8")).hexdigest() if text else ""


def _visible_pending_friend_requests(root: Any) -> List[Dict[str, Any]]:
    request_list = _uia_primary_contact_list(root)
    if request_list is None:
        return []
    try:
        children = request_list.GetChildren()
    except Exception:
        children = []
    out: List[Dict[str, Any]] = []
    for child in children:
        if _uia_control_class(child) != "mmui::XTableCell":
            continue
        text = _uia_control_text(child)
        action = _friend_request_action_label(text)
        rect = _uia_rect_tuple(child)
        if not action or rect is None:
            continue
        try:
            if bool(getattr(child, "IsOffscreen", False)):
                continue
        except Exception:
            pass
        key = _friend_request_key(text, action)
        if not key:
            continue
        out.append(
            {
                "node": child,
                "key": key,
                "action": action,
                "text": text,
                "preview": text[:120],
                "rect": rect,
            }
        )
    return out


def _open_friend_requests_list(hwnd: int) -> Any:
    import uiautomation as auto  # type: ignore

    root = auto.ControlFromHandle(int(hwnd))
    contact_list = _uia_primary_contact_list(root)
    if contact_list is None:
        raise RuntimeError("\u672a\u627e\u5230\u5fae\u4fe1\u901a\u8baf\u5f55\u5217\u8868")
    try:
        contact_list.WheelUp(wheelTimes=30)
        time.sleep(0.35)
    except Exception:
        pass

    root = auto.ControlFromHandle(int(hwnd))
    contact_list = _uia_primary_contact_list(root)
    entry = None
    if contact_list is not None:
        try:
            children = contact_list.GetChildren()
        except Exception:
            children = []
        entry = next(
            (
                node
                for node in children
                if _uia_control_text(node).replace(" ", "").startswith("\u65b0\u7684\u670b\u53cb")
            ),
            None,
        )
    if entry is None:
        entry = _uia_find_by_names(root, ["\u65b0\u7684\u670b\u53cb"], contains=True, max_depth=18)
    if entry is None:
        raise RuntimeError("\u672a\u627e\u5230\u5fae\u4fe1\u201c\u65b0\u7684\u670b\u53cb\u201d\u5165\u53e3")

    deadline = time.time() + 5.0
    next_retry_at = 0.0
    while time.time() < deadline:
        now = time.time()
        if now >= next_retry_at:
            _uia_click(entry)
            next_retry_at = now + 1.2
        time.sleep(0.3)
        root = auto.ControlFromHandle(int(hwnd))
        request_list = _uia_primary_contact_list(root)
        if request_list is None:
            continue
        try:
            children = request_list.GetChildren()
        except Exception:
            children = []
        if any(_uia_control_class(node) == "mmui::XTableCell" for node in children):
            return request_list
        refreshed_entry = next(
            (
                node
                for node in children
                if _uia_control_text(node).replace(" ", "").startswith("\u65b0\u7684\u670b\u53cb")
            ),
            None,
        )
        if refreshed_entry is not None:
            entry = refreshed_entry
    raise RuntimeError("\u6253\u5f00\u5fae\u4fe1\u201c\u65b0\u7684\u670b\u53cb\u201d\u540e\u672a\u8bfb\u53d6\u5230\u597d\u53cb\u7533\u8bf7\u5217\u8868")


def _find_pending_friend_request(hwnd: int, request_key: str) -> Optional[Dict[str, Any]]:
    import uiautomation as auto  # type: ignore

    root = auto.ControlFromHandle(int(hwnd))
    return next((item for item in _visible_pending_friend_requests(root) if item.get("key") == request_key), None)


def _find_visible_action_button(root: Any, names: List[str]) -> Optional[Any]:
    wanted = {str(name or "").strip().lower() for name in names if str(name or "").strip()}
    for node in _uia_walk(root, max_depth=18, max_nodes=1800):
        if _uia_control_text(node).lower() not in wanted:
            continue
        class_name = _uia_control_class(node)
        control_type = str(getattr(node, "ControlTypeName", "") or "")
        if "Button" not in class_name and "Button" not in control_type:
            continue
        try:
            if bool(getattr(node, "IsOffscreen", False)):
                continue
        except Exception:
            pass
        if _uia_rect_tuple(node) is not None:
            return node
    return None


def _complete_friend_request_dialog(hwnd: int, steps: List[str]) -> None:
    time.sleep(0.55)
    root = _uia_foreground_or_main_root(hwnd)
    action = _find_visible_action_button(
        root,
        ["\u524d\u5f80\u9a8c\u8bc1", "\u901a\u8fc7\u9a8c\u8bc1", "\u63a5\u53d7", "\u540c\u610f", "Accept"],
    )
    if action is not None:
        action_name = _uia_control_text(action)
        _uia_click(action)
        steps.append("open_verification" if action_name == "\u524d\u5f80\u9a8c\u8bc1" else "dialog_accept")
        time.sleep(0.45)
        root = _uia_foreground_or_main_root(hwnd)
    confirm = _find_visible_action_button(root, ["\u786e\u5b9a", "\u5b8c\u6210", "Confirm", "Done"])
    if confirm is not None:
        _uia_click(confirm)
        steps.append("dialog_confirm")
        time.sleep(0.6)


def _accept_visible_friend_request(hwnd: int, item: Dict[str, Any]) -> List[str]:
    rect = item.get("rect")
    if not isinstance(rect, tuple) or len(rect) != 4:
        raise RuntimeError("\u597d\u53cb\u7533\u8bf7\u63a5\u53d7\u6309\u94ae\u4e0d\u53ef\u89c1")
    left, top, right, bottom = rect
    width = right - left
    if str(item.get("action") or "") == "\u7b49\u5f85\u9a8c\u8bc1":
        steps = ["open_request_detail"]
        _uia_click(item["node"])
        _complete_friend_request_dialog(hwnd, steps)
        time.sleep(0.7)
        if _find_pending_friend_request(hwnd, str(item.get("key") or "")) is not None:
            raise RuntimeError("\u5df2\u70b9\u51fb\u540c\u610f\uff0c\u4f46\u5fae\u4fe1\u4ecd\u663e\u793a\u8be5\u7533\u8bf7\u672a\u5904\u7406")
        return steps

    steps = ["inline_accept"]
    _uia_click_screen_point(right - max(26, min(42, int(width * 0.16))), int((top + bottom) / 2))
    _complete_friend_request_dialog(hwnd, steps)
    time.sleep(0.7)
    current = _find_pending_friend_request(hwnd, str(item.get("key") or ""))
    if current is None:
        return steps

    # Some WeChat builds select the row instead of invoking its inline button.
    _uia_click(current["node"])
    steps.append("open_request_detail")
    _complete_friend_request_dialog(hwnd, steps)
    time.sleep(0.7)
    if _find_pending_friend_request(hwnd, str(item.get("key") or "")) is not None:
        raise RuntimeError("\u5df2\u70b9\u51fb\u63a5\u53d7\uff0c\u4f46\u5fae\u4fe1\u4ecd\u663e\u793a\u8be5\u7533\u8bf7\u672a\u5904\u7406")
    return steps


def _accept_local_friend_requests_once(
    account_id: str,
    *,
    max_accepts: int = 20,
    max_scrolls: int = 24,
) -> Dict[str, Any]:
    init_db()
    _find_local_account(account_id)
    if not _module_available("uiautomation"):
        raise RuntimeError("\u7f3a\u5c11 uiautomation\uff0c\u65e0\u6cd5\u68c0\u67e5\u65b0\u597d\u53cb\u7533\u8bf7")
    import uiautomation as auto  # type: ignore

    hwnd = _ensure_local_contacts_tab(account_id)
    if not hwnd:
        raise RuntimeError("\u6ca1\u6709\u68c0\u6d4b\u5230\u672c\u673a\u5fae\u4fe1\u7a97\u53e3")
    accepted_limit = max(1, min(int(max_accepts or 20), 50))
    scroll_limit = max(1, min(int(max_scrolls or 24), 60))
    accepted = 0
    failed = 0
    scanned_keys: set[str] = set()
    attempted_keys: set[str] = set()
    items: List[Dict[str, Any]] = []
    last_signature = ""
    stable_rounds = 0
    scroll_rounds = 0
    try:
        request_list = _open_friend_requests_list(hwnd)
        try:
            request_list.WheelUp(wheelTimes=30)
            time.sleep(0.4)
        except Exception:
            pass

        while scroll_rounds < scroll_limit and accepted < accepted_limit:
            root = auto.ControlFromHandle(int(hwnd))
            request_list = _uia_primary_contact_list(root)
            if request_list is None:
                break
            try:
                visible_rows = [
                    _uia_control_text(node)
                    for node in request_list.GetChildren()
                    if _uia_control_class(node) == "mmui::XTableCell" and _uia_control_text(node)
                ]
            except Exception:
                visible_rows = []
            for row_text in visible_rows:
                action = _friend_request_action_label(row_text)
                key = _friend_request_key(row_text, action)
                if key:
                    scanned_keys.add(key)
            pending = [
                item
                for item in _visible_pending_friend_requests(root)
                if str(item.get("key") or "") not in attempted_keys
            ]
            if pending:
                item = pending[0]
                request_key = str(item.get("key") or "")
                attempted_keys.add(request_key)
                try:
                    steps = _accept_visible_friend_request(hwnd, item)
                    accepted += 1
                    items.append(
                        {
                            "key": request_key,
                            "status": "accepted",
                            "preview": str(item.get("preview") or "")[:120],
                            "steps": steps,
                        }
                    )
                except Exception as exc:
                    failed += 1
                    items.append(
                        {
                            "key": request_key,
                            "status": "failed",
                            "preview": str(item.get("preview") or "")[:120],
                            "error": str(exc)[:300],
                        }
                    )
                stable_rounds = 0
                last_signature = ""
                continue

            signature = "|".join(visible_rows[-12:])
            stable_rounds = stable_rounds + 1 if signature == last_signature else 0
            last_signature = signature
            if stable_rounds >= 2:
                break
            try:
                request_list.WheelDown(wheelTimes=4)
                scroll_rounds += 1
                time.sleep(0.28)
            except Exception:
                break
    finally:
        _ensure_local_chat_tab(account_id)

    return {
        "ok": failed == 0,
        "checked": len(attempted_keys),
        "scanned": len(scanned_keys),
        "accepted": accepted,
        "failed": failed,
        "limit_reached": accepted >= accepted_limit,
        "scroll_rounds": scroll_rounds,
        "items": items,
        "source": "pc_wechat_uia_friend_requests",
    }


def accept_local_friend_requests(
    account_id: str,
    *,
    max_accepts: int = 20,
    max_scrolls: int = 24,
) -> Dict[str, Any]:
    return _run_local_driver_operation(
        account_id,
        "检查微信好友申请",
        lambda: _accept_local_friend_requests_once(
            account_id,
            max_accepts=max_accepts,
            max_scrolls=max_scrolls,
        ),
    )


def _uia_find_add_friend_plus_button(root: Any) -> Optional[Any]:
    root_rect = _uia_rect_tuple(root)
    if root_rect is None:
        return None
    root_left, root_top, root_right, _ = root_rect
    best_node: Optional[Any] = None
    best_score = -1
    for node in _uia_walk(root, max_depth=12, max_nodes=900):
        rect = _uia_rect_tuple(node)
        if rect is None:
            continue
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top
        rel_left = left - root_left
        rel_top = top - root_top
        if not (150 <= rel_left <= 330 and 15 <= rel_top <= 90):
            continue
        if not (16 <= width <= 60 and 16 <= height <= 60):
            continue
        if left > root_right - 180:
            continue
        name = _uia_control_text(node)
        class_name = _uia_control_class(node)
        control_type = str(getattr(node, "ControlTypeName", "") or "")
        score = 0
        if name in {"+", "添加", "添加朋友", "更多"}:
            score += 80
        if "Button" in class_name or "Button" in control_type:
            score += 30
        if 205 <= rel_left <= 275:
            score += 15
        if 20 <= rel_top <= 60:
            score += 10
        if score > best_score:
            best_score = score
            best_node = node
    return best_node if best_score >= 30 else None


def _open_local_add_friend_entry(hwnd: int, steps: List[Dict[str, Any]]) -> None:
    _ensure_local_tab(hwnd, "微信", strict=True)
    steps.append({"step": "switch_chat_tab", "ok": True})

    root = _uia_foreground_or_main_root(hwnd)
    plus_button = _uia_find_add_friend_plus_button(root)
    if plus_button is not None:
        _uia_click(plus_button)
        steps.append({"step": "open_add_menu", "ok": True, "method": "uia", "button": _uia_control_text(plus_button)})
    else:
        root_rect = _uia_rect_tuple(root)
        if root_rect is None:
            raise RuntimeError("未找到微信窗口位置，无法点击添加好友入口")
        left, top, _, _ = root_rect
        # PC 微信的添加菜单固定在左上搜索框右侧，UIA 拿不到按钮时用相对坐标兜底。
        _uia_click_screen_point(left + 238, top + 40)
        steps.append({"step": "open_add_menu", "ok": True, "method": "coordinate"})

    add_friend = _uia_click_first_named(hwnd, ["添加朋友"], timeout=4.0, contains=False)
    if add_friend is None:
        add_friend = _uia_click_first_named(hwnd, ["添加朋友"], timeout=2.0, contains=True)
    if add_friend is None:
        raise RuntimeError("未在加号菜单中找到“添加朋友”入口")
    steps.append({"step": "open_add_friend_entry", "ok": True})


def _manual_open_local_add_friend_form(hwnd: int, keyword: str, steps: List[Dict[str, Any]]) -> None:
    search_edit = _uia_wait_for_edit(hwnd, timeout=6.0)
    if search_edit is None:
        add_entry = _uia_click_first_named(hwnd, ["添加朋友"], timeout=2.0, contains=False)
        if add_entry is not None:
            steps.append({"step": "manual_open_add_friend_search", "ok": True})
            search_edit = _uia_wait_for_edit(hwnd, timeout=4.0)
    if search_edit is None:
        raise RuntimeError("未找到新朋友搜索输入框")

    _uia_set_text(search_edit, keyword)
    steps.append({"step": "manual_search_friend_input", "ok": True, "keyword": keyword})

    _send_hotkey("enter", pause=0.35)
    steps.append({"step": "manual_trigger_search", "ok": True, "method": "enter"})
    time.sleep(0.8)
    root_after_enter = _uia_foreground_or_main_root(hwnd)
    has_apply = _uia_find_by_names(root_after_enter, ["发送添加朋友申请"], contains=False, max_depth=18) is not None
    has_result_button = _uia_find_by_names(root_after_enter, ["添加到通讯录", "申请添加朋友", "加为朋友"], contains=True, max_depth=18) is not None
    if not has_apply and not has_result_button:
        search_button = _uia_click_first_named(hwnd, ["搜索"], timeout=1.0, contains=False)
        if search_button is not None:
            steps.append({"step": "manual_trigger_search", "ok": True, "method": "button"})
            time.sleep(0.8)

    deadline = time.time() + 8.0
    apply_names = ["添加到通讯录", "申请添加朋友", "加为朋友"]
    while time.time() < deadline:
        root = _uia_foreground_or_main_root(hwnd)
        if _uia_find_by_names(root, ["发送添加朋友申请"], contains=False, max_depth=18) is not None:
            steps.append({"step": "manual_apply_form_ready", "ok": True})
            return

        apply_node = _uia_find_by_names(root, apply_names, contains=False, max_depth=18)
        if apply_node is None:
            apply_node = _uia_find_by_names(root, apply_names, contains=True, max_depth=18)
        if apply_node is None:
            apply_node = _uia_find_by_names(root, ["添加"], contains=False, max_depth=18)
        if apply_node is not None:
            _uia_click(apply_node)
            steps.append({"step": "manual_open_apply_form", "ok": True, "button": _uia_control_text(apply_node)})
            time.sleep(0.8)
            continue
        time.sleep(0.3)

    raise RuntimeError("未找到搜索结果里的添加入口，可能未搜到用户、已是好友或微信限制")


def _find_local_add_friend_submit_button(hwnd: int, *, timeout: float = 5.0) -> Optional[Any]:
    primary_names = ["发送添加朋友申请"]
    fallback_names = ["确定", "发送", "完成"]
    deadline = time.time() + max(0.5, timeout)
    while time.time() < deadline:
        root = _uia_foreground_or_main_root(hwnd)
        for names in (primary_names, fallback_names):
            for node in _uia_walk(root, max_depth=18, max_nodes=900):
                name = _uia_control_text(node)
                if name not in names:
                    continue
                class_name = _uia_control_class(node)
                control_type = str(getattr(node, "ControlTypeName", "") or "")
                if "Button" not in class_name and "Button" not in control_type:
                    continue
                try:
                    if hasattr(node, "IsEnabled") and not bool(getattr(node, "IsEnabled")):
                        continue
                except Exception:
                    pass
                return node
        time.sleep(0.25)
    return None


def _submit_local_add_friend_request(hwnd: int, steps: List[Dict[str, Any]]) -> None:
    submit_button = _find_local_add_friend_submit_button(hwnd, timeout=6.0)
    if submit_button is None:
        raise RuntimeError("未找到最终确认按钮，好友申请未提交")
    first_name = _uia_control_text(submit_button)
    _uia_click(submit_button)
    steps.append({"step": "submit_final", "ok": True, "button": first_name})

    # Some WeChat builds show a second confirmation button after the submit page.
    time.sleep(0.7)
    confirm_button = _find_local_add_friend_submit_button(hwnd, timeout=1.5)
    if confirm_button is not None:
        confirm_name = _uia_control_text(confirm_button)
        if confirm_name in {"确定", "完成"}:
            _uia_click(confirm_button)
            steps.append({"step": "submit_final_confirm", "ok": True, "button": confirm_name})


def _prepare_local_add_friend_form(
    account_id: str,
    keyword: str,
    *,
    apply_message: str = "",
    remark: str = "",
    tags: Optional[List[str]] = None,
    permission: str = "朋友圈",
    submit_final: bool = False,
) -> Dict[str, Any]:
    if not _module_available("uiautomation"):
        raise RuntimeError("缺少 uiautomation，无法打开加好友申请界面")
    steps: List[Dict[str, Any]] = []
    hwnd = _local_wechat_hwnd(account_id)
    if not hwnd:
        raise RuntimeError("没有检测到本机微信窗口")
    _focus_local_wechat(hwnd)
    _open_local_add_friend_entry(hwnd, steps)

    try:
        from wxauto4.ui.component import SearchNewFriendWnd  # type: ignore

        wnd = SearchNewFriendWnd()
        wnd.init()
        missing_controls = [name for name in ("search_edit", "search_btn") if not hasattr(wnd, name)]
        if missing_controls:
            raise RuntimeError(f"SearchNewFriendWnd controls not initialized: {', '.join(missing_controls)}")
        wnd.search(keyword)
        steps.append({"step": "search_friend", "ok": True, "keyword": keyword})
        time.sleep(1.0)
        wnd.apply()
        steps.append({"step": "open_apply_form", "ok": True})
    except Exception as exc:
        steps.append({"step": "wxauto_open_apply_form", "ok": False, "error": str(exc)})
        try:
            _manual_open_local_add_friend_form(hwnd, keyword, steps)
        except Exception as fallback_exc:
            raise RuntimeError(f"打开好友申请界面失败：{fallback_exc}") from fallback_exc

    send_button = _find_local_add_friend_submit_button(hwnd, timeout=8.0)
    if send_button is None:
        raise RuntimeError("未进入发送添加朋友申请界面，可能未搜到用户或已是好友")
    form_root = _uia_foreground_or_main_root(hwnd)

    edits = _uia_edit_controls(form_root)
    filled: Dict[str, Any] = {"apply_message": False, "remark": False, "permission": False, "tags": False}
    if apply_message and edits:
        _uia_set_text(edits[0], apply_message)
        filled["apply_message"] = True
    if remark and len(edits) >= 2:
        _uia_set_text(edits[1], remark)
        filled["remark"] = True

    permission = str(permission or "朋友圈").strip()
    if permission:
        perm_node = _uia_find_by_names(form_root, [permission], contains=False, max_depth=18)
        if perm_node is None and permission == "朋友圈":
            perm_node = _uia_find_by_names(form_root, ["聊天、朋友圈、微信运动等"], contains=False, max_depth=18)
        if perm_node is not None:
            _uia_click(perm_node)
            filled["permission"] = True

    # 标签窗口需要额外确认，容易改变用户现有标签选择；这里保留给用户在最终页手动确认。
    tag_list = [str(x).strip() for x in (tags or []) if str(x).strip()]
    if tag_list:
        steps.append({"step": "tags_pending_manual_confirm", "ok": True, "tags": tag_list})

    submitted = False
    if submit_final:
        _submit_local_add_friend_request(hwnd, steps)
        submitted = True

    return {
        "ok": True,
        "prepared": True,
        "submitted": submitted,
        "message": "好友申请已提交" if submitted else "已打开好友申请确认界面，未点击“发送添加朋友申请”",
        "filled": filled,
        "steps": steps,
    }


def add_local_friend(
    account_id: str,
    keyword: str,
    *,
    apply_message: str = "",
    remark: str = "",
    tags: Optional[List[str]] = None,
    permission: str = "朋友圈",
    prepare_only: bool = False,
) -> Dict[str, Any]:
    init_db()
    _find_local_account(account_id)
    keyword = str(keyword or "").strip()
    if not keyword:
        raise RuntimeError("缺少好友关键词")
    now = _now_iso()
    req_id = uuid.uuid4().hex
    status = "prepared" if prepare_only else "submitted"
    error = ""
    raw: Dict[str, Any] = {}
    try:
        raw = _prepare_local_add_friend_form(
            account_id,
            keyword,
            apply_message=apply_message,
            remark=remark,
            tags=tags,
            permission=permission,
            submit_final=not prepare_only,
        )
        status = "prepared" if prepare_only else "submitted"
    except Exception as exc:
        status = "failed"
        error = str(exc)
    with _connect() as conn:
        conn.execute(
            """
            insert into wechat_friend_requests(id, account_id, keyword, apply_message, remark, tags, permission, status, error_message, raw_json, created_at, updated_at)
            values(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                req_id,
                account_id,
                keyword,
                apply_message,
                remark,
                _json_dumps(tags or []),
                permission,
                status,
                error,
                _json_dumps(raw),
                now,
                _now_iso(),
            ),
        )
    if status not in {"prepared", "submitted"}:
        raise RuntimeError(error or "打开好友申请界面失败")
    return {
        "ok": True,
        "id": req_id,
        "status": status,
        "submitted": status == "submitted",
        "raw": raw,
        "message": raw.get("message") or ("好友申请已提交" if status == "submitted" else "已打开好友申请确认界面"),
    }


def _local_friend_request_count_today(account_id: str) -> int:
    today = datetime.utcnow().date().isoformat()
    with _connect() as conn:
        row = conn.execute(
            """
            select count(*) from wechat_friend_requests
            where account_id=? and status in ('submitted','prepared') and created_at >= ?
            """,
            (account_id, today),
        ).fetchone()
    return int((row[0] if row else 0) or 0)


def _local_moments_like_count_today(account_id: str) -> int:
    today = datetime.utcnow().date().isoformat()
    with _connect() as conn:
        row = conn.execute(
            """
            select coalesce(sum(success), 0) from wechat_tasks
            where account_id=? and task_type='moments_like' and created_at >= ?
            """,
            (account_id, today),
        ).fetchone()
    return int((row[0] if row else 0) or 0)


def _local_moments_comment_count_today(account_id: str) -> int:
    today = datetime.utcnow().date().isoformat()
    with _connect() as conn:
        row = conn.execute(
            """
            select count(*) from wechat_moments_comments
            where account_id=? and status='submitted' and created_at >= ?
            """,
            (account_id, today),
        ).fetchone()
    return int((row[0] if row else 0) or 0)


def _local_moments_publish_count_today(account_id: str) -> int:
    today = datetime.utcnow().date().isoformat()
    with _connect() as conn:
        row = conn.execute(
            """
            select coalesce(sum(success), 0) from wechat_tasks
            where account_id=? and task_type='moments_publish' and created_at >= ?
            """,
            (account_id, today),
        ).fetchone()
    return int((row[0] if row else 0) or 0)


def _moments_comment_record_exists(account_id: str, target: str, post_key: str) -> bool:
    if not post_key:
        return False
    with _connect() as conn:
        row = conn.execute(
            """
            select 1 from wechat_moments_comments
            where account_id=? and target=? and post_key=? and status='submitted'
            limit 1
            """,
            (account_id, target, post_key),
        ).fetchone()
    return bool(row)


def _record_moments_comment(
    account_id: str,
    target: str,
    post_key: str,
    reply: str,
    *,
    post_text: str = "",
    media_summary: str = "",
    status: str = "submitted",
    error_message: str = "",
    raw: Optional[Dict[str, Any]] = None,
) -> None:
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            """
            insert into wechat_moments_comments(
                id, account_id, target, post_key, reply, post_text, media_summary,
                status, error_message, raw_json, created_at, updated_at
            )
            values(?,?,?,?,?,?,?,?,?,?,?,?)
            on conflict(account_id, target, post_key) do update set
              reply=excluded.reply,
              post_text=excluded.post_text,
              media_summary=excluded.media_summary,
              status=excluded.status,
              error_message=excluded.error_message,
              raw_json=excluded.raw_json,
              updated_at=excluded.updated_at
            """,
            (
                uuid.uuid4().hex,
                account_id,
                target,
                post_key,
                reply,
                post_text[:4000],
                media_summary[:4000],
                status,
                error_message[:1000],
                _json_dumps(raw or {}),
                now,
                now,
            ),
        )


def _enforce_local_friend_add_rate(account_id: str) -> None:
    strategy = get_strategy()
    daily_limit = int(strategy.get("daily_friend_add_limit") or 0)
    if daily_limit > 0 and _local_friend_request_count_today(account_id) >= daily_limit:
        raise RuntimeError(f"daily friend add limit reached: {daily_limit}")
    min_gap = float(strategy.get("friend_add_min_gap") or 0)
    if min_gap <= 0:
        return
    with _connect() as conn:
        row = conn.execute(
            """
            select created_at from wechat_friend_requests
            where account_id=? and status in ('submitted','prepared')
            order by created_at desc limit 1
            """,
            (account_id,),
        ).fetchone()
    if not row:
        return
    try:
        last = datetime.fromisoformat(str(row["created_at"]))
        elapsed = (datetime.utcnow() - last).total_seconds()
    except Exception:
        return
    if elapsed < min_gap:
        time.sleep(min_gap - elapsed)


def _open_local_moments(hwnd: int, steps: List[Dict[str, Any]]) -> None:
    _focus_local_wechat(hwnd)
    root = _uia_foreground_or_main_root(hwnd)
    nodes = _uia_walk(root, max_depth=8, max_nodes=240)
    is_global_timeline = any(_uia_control_class(node) == "mmui::TimeLineListView" for node in nodes)
    is_contact_album = any(
        _uia_control_class(node) in {"mmui::AlbumBaseCell", "mmui::AlbumContentCell"} for node in nodes
    )
    if is_global_timeline and not is_contact_album:
        steps.append({"step": "open_moments", "ok": True, "entry": "already_open"})
        refresh = _uia_find_by_names(root, ["刷新"], contains=False, max_depth=10)
        if refresh is not None:
            _uia_click(refresh)
            steps.append({"step": "refresh_moments", "ok": True})
            time.sleep(1.5)
        return
    node = _uia_find_by_names(root, ["朋友圈"], contains=False, max_depth=18)
    if node is None:
        node = _uia_find_by_names(root, ["朋友圈"], contains=True, max_depth=18)
    if node is None:
        raise RuntimeError("未找到朋友圈入口，请确认 PC 微信左侧有朋友圈入口且当前账号支持")
    _uia_click(node)
    steps.append({"step": "open_moments", "ok": True, "entry": _uia_control_text(node)})
    time.sleep(1.5)
    root = _uia_foreground_or_main_root(hwnd)
    refresh = _uia_find_by_names(root, ["刷新"], contains=False, max_depth=10)
    if refresh is not None:
        _uia_click(refresh)
        steps.append({"step": "refresh_moments", "ok": True})
        time.sleep(1.5)


def _find_local_contact_list(root: Any) -> Optional[Any]:
    for node in _uia_walk(root, max_depth=14, max_nodes=1200):
        if _uia_control_class(node) == "mmui::StickyHeaderRecyclerListView" and _uia_control_text(node) == "???":
            return node
    return _uia_guess_contact_list(root)


def _find_visible_contact_cell(root: Any, target: str) -> Optional[Any]:
    wanted = str(target or "").strip()
    if not wanted:
        return None
    fallback: Optional[Any] = None
    for node in _uia_walk(root, max_depth=18, max_nodes=1800):
        if _uia_control_class(node) != "mmui::ContactsCellItemView":
            continue
        name = _uia_control_text(node)
        if name == wanted:
            return node
        if fallback is None and wanted in name:
            fallback = node
    return fallback


def _read_visible_contact_profile_wx_no(
    hwnd: int,
    account_id: str,
    target: str,
    steps: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Read one contact's wx id without leaving the Contacts page workflow."""
    import uiautomation as auto  # type: ignore

    root = auto.ControlFromHandle(int(hwnd))
    cell = _find_visible_contact_cell(root, target)
    if cell is None:
        return ""
    if steps is None:
        steps = []
    _uia_click(cell)
    steps.append({"step": "sync_contact_open_profile", "ok": True, "target": target})
    wx_no = ""
    try:
        time.sleep(0.8)
        profile_root = auto.ControlFromHandle(int(hwnd))
        wx_no = _extract_contact_profile_wx_no(profile_root)
        if wx_no:
            persisted = _persist_contact_wx_no(account_id, target, wx_no)
            steps.append(
                {
                    "step": "sync_contact_capture_wx_no",
                    "ok": True,
                    "target": target,
                    "wx_no": wx_no,
                    "persisted": bool(persisted),
                }
            )
        else:
            steps.append({"step": "sync_contact_capture_wx_no", "ok": False, "target": target, "error": "资料页未显示微信号"})
    finally:
        # PC WeChat keeps the Contacts list on the left while the profile is
        # rendered on the right. Leave that list in place for the next item;
        # switching to the chat tab would break the sync workflow.
        pass
    return wx_no


def _open_local_contact_profile(hwnd: int, account_id: str, target: str, steps: List[Dict[str, Any]]) -> None:
    _ensure_local_contacts_tab(account_id)
    root = _uia_foreground_or_main_root(hwnd)
    contact_list = _find_local_contact_list(root)
    if contact_list is None:
        raise RuntimeError("local WeChat contact list not found")
    aliases = _resolve_local_contact_aliases(account_id, target)
    search_terms = aliases or [str(target or "").strip()]
    try:
        contact_list.WheelUp(wheelTimes=24)
        time.sleep(0.35)
    except Exception:
        pass

    stable_rounds = 0
    last_visible = ""
    found_node: Optional[Any] = None
    for _idx in range(80):
        root = _uia_foreground_or_main_root(hwnd)
        for candidate in search_terms:
            found_node = _find_visible_contact_cell(root, candidate)
            if found_node is not None:
                target = candidate
                break
        if found_node is not None:
            break
        visible_names: List[str] = []
        for node in _uia_walk(root, max_depth=18, max_nodes=1200):
            if _uia_control_class(node) == "mmui::ContactsCellItemView":
                name = _uia_control_text(node)
                if name:
                    visible_names.append(name)
        signature = "|".join(visible_names[-8:])
        stable_rounds = stable_rounds + 1 if signature == last_visible else 0
        last_visible = signature
        if stable_rounds >= 4:
            break
        try:
            contact_list = _find_local_contact_list(root) or contact_list
            contact_list.WheelDown(wheelTimes=4)
            time.sleep(0.25)
        except Exception:
            break

    if found_node is None:
        raise RuntimeError(f"联系人未找到：{target}")
    _uia_click(found_node)
    steps.append({"step": "open_contact_profile", "ok": True, "target": target, "aliases": aliases[:5]})
    time.sleep(0.8)
    try:
        root = _uia_foreground_or_main_root(hwnd)
        wx_no = _extract_contact_profile_wx_no(root)
        if wx_no:
            persisted = _persist_contact_wx_no(account_id, target, wx_no)
            steps.append({"step": "capture_contact_wx_no", "ok": True, "target": target, "wx_no": wx_no, "persisted": bool(persisted)})
    except Exception as exc:
        steps.append({"step": "capture_contact_wx_no", "ok": False, "target": target, "error": str(exc)})


def _open_local_contact_moments(account_id: str, target: str, steps: List[Dict[str, Any]]) -> int:
    hwnd = _local_wechat_hwnd(account_id)
    if not hwnd:
        raise RuntimeError("没有检测到本机微信窗口")
    _open_local_contact_profile(hwnd, account_id, target, steps)
    root = _uia_foreground_or_main_root(hwnd)
    candidates: List[tuple[int, Any]] = []
    for node in _uia_walk(root, max_depth=18, max_nodes=1400):
        if _uia_control_text(node) != "朋友圈":
            continue
        rect = _uia_rect_tuple(node)
        class_name = _uia_control_class(node)
        width = (rect[2] - rect[0]) if rect else 0
        score = width
        if class_name == "mmui::XMouseEventView":
            score += 1000
        elif class_name == "mmui::XTextView":
            score += 200
        elif class_name == "mmui::XTabBarItem":
            score -= 500
        candidates.append((score, node))
    if not candidates:
        raise RuntimeError(f"联系人没有可进入的朋友圈入口：{target}")
    candidates.sort(key=lambda item: item[0], reverse=True)
    _uia_click(candidates[0][1])
    steps.append({"step": "open_contact_moments", "ok": True, "target": target})
    time.sleep(1.5)
    return hwnd


def _close_foreground_sns_window(main_hwnd: int, steps: List[Dict[str, Any]], *, reason: str = "") -> None:
    try:
        import win32con  # type: ignore
        import win32gui  # type: ignore

        fg = int(win32gui.GetForegroundWindow() or 0)
        if not fg or fg == int(main_hwnd):
            return
        root = _uia_foreground_or_main_root(main_hwnd)
        if _uia_control_class(root) != "mmui::SNSWindow":
            return
        win32gui.PostMessage(fg, win32con.WM_CLOSE, 0, 0)
        steps.append({"step": "close_contact_moments", "ok": True, "reason": reason})
        time.sleep(0.8)
    except Exception as exc:
        steps.append({"step": "close_contact_moments", "ok": False, "error": str(exc), "reason": reason})


def _scroll_local_moments(hwnd: int, amount: int = -5) -> None:
    root = _uia_foreground_or_main_root(hwnd)
    rect = _uia_rect_tuple(root)
    if rect is None:
        return
    left, top, right, bottom = rect
    x = int((left + right) / 2)
    y = int((top + bottom) / 2)
    try:
        import win32api  # type: ignore
        import win32con  # type: ignore
    except Exception as exc:
        raise RuntimeError("本机微信控制组件不可用：缺少 pywin32 滚轮模块") from exc
    win32api.SetCursorPos((x, y))
    for _ in range(abs(int(amount))):
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, x, y, -120 if amount < 0 else 120, 0)
        time.sleep(random.uniform(0.18, 0.45))
    _human_pause("moments_scroll_sleep_min", "moments_scroll_sleep_max", floor=1.0)


def _node_center(node: Any) -> tuple[int, int] | None:
    rect = _uia_rect_tuple(node)
    if rect is None:
        return None
    left, top, right, bottom = rect
    return int((left + right) / 2), int((top + bottom) / 2)


def _moments_time_label(text: str) -> str:
    value = str(text or "")
    matches: List[tuple[int, str]] = []
    for pattern in (
        r"刚刚",
        r"今天",
        r"\d+\s*秒前",
        r"\d+\s*分钟前",
        r"\d+\s*小时前",
        r"\d+\s*天前",
        r"\d{1,2}:\d{2}",
        r"昨天",
        r"前天",
        r"\d{1,2}\s*月\s*\d{1,2}\s*日",
        r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日",
    ):
        for match in re.finditer(pattern, value):
            matches.append((match.start(), match.group(0)))
    if not matches:
        return ""
    return sorted(matches, key=lambda item: item[0])[-1][1].replace(" ", "")


def _moments_time_within_24h(label: str) -> bool:
    value = str(label or "").strip()
    if not value:
        return False
    if value in {"刚刚", "刚才", "今天"}:
        return True
    if re.fullmatch(r"\d+秒前", value):
        return True
    if re.fullmatch(r"\d+分钟前", value):
        return True
    match = re.fullmatch(r"(\d+)小时前", value)
    if match:
        return int(match.group(1)) < 24
    if re.fullmatch(r"\d{1,2}:\d{2}", value):
        return True
    return False


def _moments_time_outside_24h(label: str) -> bool:
    value = str(label or "").strip()
    if not value or _moments_time_within_24h(value):
        return False
    if value in {"昨天", "前天"}:
        return True
    if re.fullmatch(r"\d+天前", value):
        return True
    if re.fullmatch(r"\d{1,2}月\d{1,2}日", value):
        return True
    if re.fullmatch(r"\d{4}年\d{1,2}月\d{1,2}日", value):
        return True
    return False


def _moments_post_author_matches(text: str, target: str) -> bool:
    value = str(text or "").strip()
    wanted = str(target or "").strip()
    if not value or not wanted:
        return False
    first_line = value.splitlines()[0].strip()
    if first_line == wanted:
        return True
    if first_line.startswith(wanted):
        tail = first_line[len(wanted): len(wanted) + 1]
        return not tail or tail.isspace() or tail in ":：，,。.!！?？@-—"
    return wanted in first_line[:64]


def _moments_action_point_for_post(root: Any, post_node: Any) -> Optional[tuple[int, int]]:
    root_rect = _uia_rect_tuple(root)
    post_rect = _uia_rect_tuple(post_node)
    if root_rect is None or post_rect is None:
        return None
    root_left, root_top, root_right, root_bottom = root_rect
    left, _top, right, bottom = post_rect
    x = int(right - 34)
    y = int(min(bottom - 8, root_bottom - 14))
    if x < root_left + 20 or x > root_right - 12:
        return None
    if y < root_top + 70 or y > root_bottom - 4:
        return None
    if right - left < 120:
        return None
    return x, y


def _contact_album_time_label(text: str) -> str:
    value = str(text or "")
    if "今天" in value:
        return "今天"
    return _moments_time_label(value)


def _visible_contact_album_cells(root: Any) -> List[Dict[str, Any]]:
    root_rect = _uia_rect_tuple(root)
    root_top = root_rect[1] if root_rect else -10**6
    root_bottom = root_rect[3] if root_rect else 10**6
    posts: List[Dict[str, Any]] = []
    for node in _uia_walk(root, max_depth=18, max_nodes=1600):
        if _uia_control_class(node) != "mmui::AlbumContentCell":
            continue
        rect = _uia_rect_tuple(node)
        text = _uia_control_text(node)
        if rect is None:
            continue
        _left, top, _right, bottom = rect
        if bottom < root_top + 70 or top > root_bottom - 4:
            continue
        label = _contact_album_time_label(text)
        posts.append(
            {
                "node": node,
                "rect": rect,
                "text": text,
                "time_label": label,
                "within_24h": _moments_time_within_24h(label),
                "outside_24h": _moments_time_outside_24h(label),
                "top": top,
            }
        )
    return sorted(posts, key=lambda item: int(item.get("top") or 0))


def _return_contact_album_list(hwnd: int, steps: List[Dict[str, Any]], target: str) -> None:
    root = _uia_foreground_or_main_root(hwnd)
    has_detail = any(_uia_control_class(node) == "mmui::TimelineContentCell" for node in _uia_walk(root, max_depth=12, max_nodes=600))
    if not has_detail:
        return
    back = _uia_find_by_names(root, ["返回"], contains=False, max_depth=8)
    if back is not None:
        _uia_click(back)
        steps.append({"step": "contact_album_back", "ok": True, "target": target})
        time.sleep(0.8)
    else:
        _send_hotkey("esc", pause=0.3)
        steps.append({"step": "contact_album_back", "ok": True, "target": target, "method": "esc"})


def _scan_and_like_contact_album_page(
    account_id: str,
    target: str,
    *,
    dry_run: bool,
    seen: set[str],
    steps: List[Dict[str, Any]],
) -> Dict[str, Any]:
    hwnd = _local_wechat_hwnd(account_id)
    if not hwnd:
        raise RuntimeError("没有检测到本机微信窗口")
    root = _uia_foreground_or_main_root(hwnd)
    found = 0
    liked = 0
    already_liked = 0
    skipped = 0
    stop_after_24h = False
    cells = _visible_contact_album_cells(root)
    if not cells:
        detail_result = _scan_and_like_visible_moments(account_id, [target], dry_run=dry_run, seen=seen, steps=steps)
        _return_contact_album_list(hwnd, steps, target)
        return detail_result

    for cell in cells:
        time_label = str(cell.get("time_label") or "")
        text = str(cell.get("text") or "")
        rect = cell.get("rect")
        key = f"contact_album:{target}:{rect}:{time_label}:{hashlib.sha1(text.encode('utf-8', errors='ignore')).hexdigest()[:12]}"
        if key in seen:
            continue
        seen.add(key)
        if cell.get("outside_24h"):
            stop_after_24h = True
            steps.append({"step": "contact_album_stop_after_24h", "target": target, "time": time_label or "unknown"})
            break
        if not cell.get("within_24h"):
            skipped += 1
            steps.append({"step": "contact_album_skip", "target": target, "reason": "time_unknown", "time": time_label or "unknown"})
            continue
        _uia_click(cell.get("node"))
        steps.append({"step": "contact_album_open_post", "target": target, "time": time_label})
        time.sleep(1.0)
        detail = _scan_and_like_visible_moments(account_id, [target], dry_run=dry_run, seen=seen, steps=steps)
        found += int(detail.get("found") or 0)
        liked += int(detail.get("liked") or 0)
        already_liked += int(detail.get("already_liked") or 0)
        skipped += int(detail.get("skipped") or 0)
        stop_after_24h = stop_after_24h or bool(detail.get("stop_after_24h"))
        _return_contact_album_list(hwnd, steps, target)
        if stop_after_24h:
            break
    return {
        "found": found,
        "liked": liked,
        "already_liked": already_liked,
        "skipped": skipped,
        "stop_after_24h": stop_after_24h,
    }


def _process_contact_moments_like_target(
    account_id: str,
    target: str,
    *,
    dry_run: bool,
    max_scrolls: int,
    seen: set[str],
    steps: List[Dict[str, Any]],
) -> Dict[str, Any]:
    hwnd = _open_local_contact_moments(account_id, target, steps)
    found_total = 0
    liked_total = 0
    already_total = 0
    skipped_total = 0
    processed_steps = 0
    stopped_after_24h = False
    try:
        for idx in range(max_scrolls):
            result = _scan_and_like_contact_album_page(account_id, target, dry_run=dry_run, seen=seen, steps=steps)
            found_total += int(result.get("found") or 0)
            liked_total += int(result.get("liked") or 0)
            already_total += int(result.get("already_liked") or 0)
            skipped_total += int(result.get("skipped") or 0)
            processed_steps = idx + 1
            stopped_after_24h = bool(result.get("stop_after_24h"))
            if stopped_after_24h:
                break
            _scroll_local_moments(hwnd, -4)
        return {
            "found": found_total,
            "liked": liked_total,
            "already_liked": already_total,
            "skipped": skipped_total,
            "processed_steps": processed_steps,
            "stop_after_24h": stopped_after_24h,
        }
    finally:
        _close_foreground_sns_window(hwnd, steps, reason="contact_target_done")


def _moments_nearby_social_text(root: Any, post_node: Any) -> str:
    post_rect = _uia_rect_tuple(post_node)
    if post_rect is None:
        return ""
    _left, _top, _right, post_bottom = post_rect
    chunks: List[str] = []
    for node in _uia_walk(root, max_depth=18, max_nodes=1600):
        class_name = _uia_control_class(node)
        if class_name not in {"mmui::TimelineCommentCell", "mmui::TimelineCell"}:
            continue
        rect = _uia_rect_tuple(node)
        if rect is None:
            continue
        _l, top, _r, bottom = rect
        if top < post_bottom - 4 or top > post_bottom + 180:
            continue
        text = _uia_control_text(node)
        if text:
            chunks.append(text[:120])
    return " | ".join(chunks)


def _find_visible_target_moments_posts(root: Any, targets: List[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    wanted = [str(x or "").strip() for x in targets if str(x or "").strip()]
    if not wanted:
        return out
    for node in _uia_walk(root, max_depth=18, max_nodes=1800):
        if _uia_control_class(node) != "mmui::TimelineContentCell":
            continue
        rect = _uia_rect_tuple(node)
        text = _uia_control_text(node)
        if rect is None or not text:
            continue
        for target in wanted:
            if not _moments_post_author_matches(text, target):
                continue
            label = _moments_time_label(text)
            out.append(
                {
                    "target": target,
                    "node": node,
                    "rect": rect,
                    "text": text,
                    "time_label": label,
                    "within_24h": _moments_time_within_24h(label),
                    "social_text": _moments_nearby_social_text(root, node),
                }
            )
            break
    return out


def _first_visible_moments_post_outside_24h(root: Any) -> Optional[Dict[str, Any]]:
    root_rect = _uia_rect_tuple(root)
    root_top = root_rect[1] if root_rect else -10**6
    root_bottom = root_rect[3] if root_rect else 10**6
    posts: List[Dict[str, Any]] = []
    for node in _uia_walk(root, max_depth=18, max_nodes=1800):
        if _uia_control_class(node) != "mmui::TimelineContentCell":
            continue
        rect = _uia_rect_tuple(node)
        text = _uia_control_text(node)
        if rect is None or not text:
            continue
        _left, top, _right, bottom = rect
        if bottom < root_top + 70 or top > root_bottom - 4:
            continue
        label = _moments_time_label(text)
        if not _moments_time_outside_24h(label):
            continue
        posts.append({"text": text, "time_label": label, "rect": rect, "top": top})
    if not posts:
        return None
    return sorted(posts, key=lambda item: int(item.get("top") or 0))[0]


def _find_nearby_moments_action(root: Any, author_node: Any) -> Optional[Any]:
    author_rect = _uia_rect_tuple(author_node)
    if author_rect is None:
        return None
    a_left, a_top, a_right, _ = author_rect
    best: tuple[int, Any] | None = None
    for node in _uia_walk(root, max_depth=18, max_nodes=1200):
        name = _uia_control_text(node)
        if name not in {"评论", "赞"}:
            continue
        rect = _uia_rect_tuple(node)
        if rect is None:
            continue
        left, top, right, bottom = rect
        if top < a_top - 20 or top > a_top + 520:
            continue
        if right < a_right:
            continue
        dist = abs(top - a_top) + max(0, left - a_left)
        if best is None or dist < best[0]:
            best = (dist, node)
    return best[1] if best else None


def _find_visible_target_author_nodes(root: Any, targets: List[str]) -> List[tuple[str, Any]]:
    out: List[tuple[str, Any]] = []
    wanted = [str(x or "").strip() for x in targets if str(x or "").strip()]
    if not wanted:
        return out
    for node in _uia_walk(root, max_depth=18, max_nodes=1200):
        name = _uia_control_text(node)
        if not name:
            continue
        for target in wanted:
            if name == target or target in name:
                out.append((target, node))
                break
    return out


def _open_moments_action_menu(hwnd: int, action_node: Any) -> Dict[str, Any]:
    _uia_click(action_node)
    time.sleep(0.6)
    root = _uia_foreground_or_main_root(hwnd)
    like_node = _uia_find_by_names(root, ["赞"], contains=False, max_depth=18)
    comment_node = _uia_find_by_names(root, ["评论"], contains=False, max_depth=18)
    cancel_node = _uia_find_by_names(root, ["取消", "取消赞"], contains=False, max_depth=18)
    return {"root": root, "like": like_node, "comment": comment_node, "cancel": cancel_node}


def _open_moments_action_menu_at_point(hwnd: int, x: int, y: int) -> Dict[str, Any]:
    _uia_click_screen_point(x, y)
    time.sleep(0.6)
    root = _uia_foreground_or_main_root(hwnd)
    like_node = _uia_find_by_names(root, ["赞"], contains=False, max_depth=18)
    comment_node = _uia_find_by_names(root, ["评论"], contains=False, max_depth=18)
    cancel_node = _uia_find_by_names(root, ["取消", "取消赞"], contains=False, max_depth=18)
    return {"root": root, "like": like_node, "comment": comment_node, "cancel": cancel_node}


def _scan_and_like_visible_moments(
    account_id: str,
    targets: List[str],
    *,
    dry_run: bool,
    seen: set[str],
    steps: List[Dict[str, Any]],
) -> Dict[str, Any]:
    hwnd = _local_moments_or_main_hwnd(account_id)
    if not hwnd:
        raise RuntimeError("没有检测到本机微信窗口")
    root = _uia_foreground_or_main_root(hwnd)
    found = 0
    liked = 0
    already_liked = 0
    skipped = 0
    for post in _find_visible_target_moments_posts(root, targets):
        target = str(post.get("target") or "")
        rect = post.get("rect")
        time_label = str(post.get("time_label") or "")
        text_key = hashlib.sha1(str(post.get("text") or "").encode("utf-8", errors="ignore")).hexdigest()[:12]
        key = f"{target}:{rect}:{time_label}:{text_key}"
        if key in seen:
            continue
        seen.add(key)
        if not post.get("within_24h"):
            skipped += 1
            steps.append(
                {
                    "step": "moments_post_skip",
                    "target": target,
                    "reason": "outside_24h",
                    "time": time_label or "unknown",
                }
            )
            continue
        found += 1
        point = _moments_action_point_for_post(root, post.get("node"))
        if point is None:
            skipped += 1
            steps.append(
                {
                    "step": "moments_post_skip",
                    "target": target,
                    "reason": "action_not_visible",
                    "time": time_label,
                }
            )
            continue
        menu = _open_moments_action_menu_at_point(hwnd, point[0], point[1])
        if menu.get("cancel") is not None:
            already_liked += 1
            _send_hotkey("esc", pause=0.2)
            steps.append(
                {
                    "step": "moments_post_already_liked",
                    "target": target,
                    "time": time_label,
                    "social": str(post.get("social_text") or ""),
                }
            )
            continue
        like_node = menu.get("like")
        if like_node is None:
            skipped += 1
            _send_hotkey("esc", pause=0.2)
            steps.append(
                {
                    "step": "moments_post_skip",
                    "target": target,
                    "reason": "like_button_not_found",
                    "time": time_label,
                }
            )
            continue
        if dry_run:
            _send_hotkey("esc", pause=0.2)
            steps.append(
                {
                    "step": "moments_post_like_ready",
                    "target": target,
                    "dry_run": True,
                    "time": time_label,
                    "social": str(post.get("social_text") or ""),
                }
            )
        else:
            daily_limit = int(get_strategy().get("daily_moments_like_limit") or 0)
            if daily_limit > 0 and _local_moments_like_count_today(account_id) + liked >= daily_limit:
                skipped += 1
                _send_hotkey("esc", pause=0.2)
                steps.append({"step": "moments_post_skip", "target": target, "reason": "daily_limit", "time": time_label})
                continue
            _uia_click(like_node)
            liked += 1
            steps.append({"step": "moments_post_liked", "target": target, "time": time_label})
            _human_pause("moments_like_sleep_min", "moments_like_sleep_max", floor=5.0)
    stop_post = _first_visible_moments_post_outside_24h(root)
    if stop_post is not None:
        text = str(stop_post.get("text") or "")
        steps.append(
            {
                "step": "moments_stop_after_24h",
                "time": str(stop_post.get("time_label") or ""),
                "post": text.splitlines()[0] if text else "",
            }
        )
    return {
        "found": found,
        "liked": liked,
        "already_liked": already_liked,
        "skipped": skipped,
        "stop_after_24h": bool(stop_post),
    }


def _moments_post_key(target: str, post_text: str, time_label: str = "", fallback: str = "") -> str:
    body = re.sub(r"\s+", " ", str(post_text or fallback or "")).strip()
    seed = f"{target}|{time_label}|{body[:2000]}"
    return hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()


def _find_first_visible_timeline_post(root: Any, target: str) -> Optional[Dict[str, Any]]:
    posts = _find_visible_target_moments_posts(root, [target])
    if posts:
        return posts[0]
    candidates: List[Dict[str, Any]] = []
    for node in _uia_walk(root, max_depth=18, max_nodes=1800):
        if _uia_control_class(node) != "mmui::TimelineContentCell":
            continue
        rect = _uia_rect_tuple(node)
        text = _uia_control_text(node)
        if rect is None or not text:
            continue
        label = _moments_time_label(text)
        candidates.append(
            {
                "target": target,
                "node": node,
                "rect": rect,
                "text": text,
                "time_label": label,
                "within_24h": _moments_time_within_24h(label),
                "social_text": _moments_nearby_social_text(root, node),
            }
        )
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: int((item.get("rect") or (0, 0, 0, 0))[1]))[0]


def _moments_comment_text(root: Any, post_node: Any) -> str:
    post_rect = _uia_rect_tuple(post_node)
    if post_rect is None:
        return ""
    _left, _top, _right, post_bottom = post_rect
    chunks: List[str] = []
    for node in _uia_walk(root, max_depth=18, max_nodes=1800):
        class_name = _uia_control_class(node)
        if class_name not in {"mmui::TimelineCommentCell", "mmui::TimelineCell"}:
            continue
        rect = _uia_rect_tuple(node)
        if rect is None:
            continue
        _l, top, _r, _bottom = rect
        if top < post_bottom - 8 or top > post_bottom + 520:
            continue
        text = _uia_control_text(node)
        if text:
            chunks.append(text[:500])
    return "\n".join(chunks)


def _local_my_names(account_id: str) -> List[str]:
    names: List[str] = ["我"]
    try:
        import wxauto4  # type: ignore

        wx = wxauto4.WeChat(debug=False, resize=False)
        info = wx.GetMyInfo() or {}
        for key in ("nickname", "name", "wxid", "微信名", "昵称"):
            value = str(info.get(key) or "").strip()
            if value:
                names.append(value)
    except Exception:
        pass
    try:
        item = _find_local_account(account_id)
        title = str(item.get("title") or item.get("name") or "").strip()
        if title and title not in WECHAT_WINDOW_TITLES:
            names.append(title)
    except Exception:
        pass
    out: List[str] = []
    seen: set[str] = set()
    for name in names:
        clean = re.sub(r"\s+", " ", str(name or "")).strip()
        if not clean or clean.lower() in seen:
            continue
        seen.add(clean.lower())
        out.append(clean)
    return out


def _moments_already_commented_by_self(root: Any, post_node: Any, self_names: List[str]) -> bool:
    text = _moments_comment_text(root, post_node)
    if not text:
        return False
    lines = [line.strip() for line in re.split(r"[\n|]+", text) if line.strip()]
    for line in lines:
        for name in self_names:
            escaped = re.escape(name)
            if re.match(rf"^{escaped}\s*[:：]", line):
                return True
    return False


def _capture_post_snapshot_data_url(root: Any, post_node: Any) -> str:
    post_rect = _uia_rect_tuple(post_node)
    root_rect = _uia_rect_tuple(root)
    if post_rect is None or root_rect is None:
        return ""
    left, top, right, bottom = post_rect
    r_left, r_top, r_right, r_bottom = root_rect
    bbox = (
        max(int(r_left), int(left) - 18),
        max(int(r_top), int(top) - 12),
        min(int(r_right), int(right) + 18),
        min(int(r_bottom), int(bottom) + 260),
    )
    if bbox[2] - bbox[0] < 120 or bbox[3] - bbox[1] < 80:
        return ""
    try:
        from PIL import ImageGrab  # type: ignore

        img = ImageGrab.grab(bbox=bbox)
        if max(img.size) > 1280:
            ratio = 1280 / float(max(img.size))
            img = img.resize((max(1, int(img.size[0] * ratio)), max(1, int(img.size[1] * ratio))))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return ""


def _find_detail_comment_cell(root: Any) -> Optional[Any]:
    for node in _uia_walk(root, max_depth=18, max_nodes=1800):
        if _uia_control_class(node) == "mmui::DetailCommentCell":
            return node
    return None


def _server_proxy_base_for_native() -> str:
    return (getattr(settings, "auth_server_base", None) or "https://bhzn.top").strip().rstrip("/")


async def _call_sutui_chat_for_native_task(
    auth_context: Dict[str, Any],
    *,
    messages: List[Dict[str, Any]],
    temperature: float = 0.35,
    timeout: float = 160.0,
) -> str:
    token = str(auth_context.get("token") or "").strip()
    if not token:
        raise RuntimeError("缺少登录 Token，不能生成朋友圈评论")
    model = (
        getattr(settings, "lobster_orchestration_sutui_chat_model", "")
        or getattr(settings, "lobster_default_sutui_chat_model", "")
        or "gpt-4o-mini"
    )
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    installation_id = str(auth_context.get("installation_id") or "").strip()
    if installation_id:
        headers["X-Installation-Id"] = installation_id
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
    }
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await client.post(f"{_server_proxy_base_for_native()}/api/sutui-chat/completions", json=payload, headers=headers)
    if resp.status_code >= 400:
        raise RuntimeError(f"AI 生成朋友圈评论失败 HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    data = resp.json() if resp.content else {}
    try:
        return str(data["choices"][0]["message"]["content"] or "")
    except Exception:
        return json.dumps(data, ensure_ascii=False)


def _load_user_memory_context(user_id: int, *, max_docs: int = 8, max_chars: int = 12000) -> str:
    if not user_id:
        return ""
    try:
        from ..api.openclaw_memory import _load_index, _read_canonical_memory_content
    except Exception:
        return ""
    parts: List[str] = []
    used = 0
    try:
        docs = _load_index(int(user_id))
    except Exception:
        return ""
    for doc in docs[:max_docs]:
        title = str(doc.get("title") or doc.get("filename") or doc.get("id") or "记忆").strip()
        try:
            content = _read_canonical_memory_content(doc, max_chars=min(3000, max_chars))
        except Exception:
            content = ""
        content = re.sub(r"\s+\n", "\n", str(content or "")).strip()
        if not content:
            continue
        block = f"## {title}\n{content[:3000]}"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts).strip()


async def _understand_moments_post_snapshot(
    auth_context: Dict[str, Any],
    *,
    target: str,
    post_text: str,
    snapshot_data_url: str,
) -> str:
    if not snapshot_data_url:
        return ""
    prompt = (
        "请理解这条微信朋友圈帖子截图。结合可见图片、视频封面和文案，概括它实际分享了什么。"
        "只基于截图可见信息，不要猜测视频未展示的内容，不要写营销话术。"
    )
    reply = await _call_sutui_chat_for_native_task(
        auth_context,
        messages=[
            {"role": "system", "content": "你是社交内容理解助手，只做事实理解，不做广告扩写。"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"{prompt}\n\n联系人：{target}\n朋友圈可读文本：\n{post_text[:3000]}"},
                    {"type": "image_url", "image_url": {"url": snapshot_data_url}},
                ],
            },
        ],
        temperature=0.2,
        timeout=160.0,
    )
    return re.sub(r"\s+", " ", reply).strip()[:1200]


def _clean_moments_reply(raw: str) -> str:
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I).strip()
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            text = str(data.get("reply") or data.get("comment") or data.get("content") or text)
    except Exception:
        pass
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip(" \"'“”‘’")
    text = re.sub(r"^(回复|评论)[:：]\s*", "", text).strip()
    if len(text) > 80:
        text = text[:80].rstrip("，,。.!！?？ ") + "。"
    forbidden = ("加我", "私信", "下单", "购买", "优惠", "活动", "引流", "广告", "推广", "咨询我")
    if any(word in text for word in forbidden):
        raise RuntimeError("AI 回复含营销/导流词，已拦截")
    if not text:
        raise RuntimeError("AI 未生成可用评论")
    return text


async def _generate_moments_comment_reply(
    auth_context: Dict[str, Any],
    *,
    user_id: int,
    target: str,
    post_text: str,
    media_summary: str,
) -> str:
    memory_context = _load_user_memory_context(user_id)
    user_prompt = (
        f"联系人：{target}\n\n"
        "朋友圈文案/可读文本：\n"
        f"{(post_text or '（没有读取到文字）')[:3000]}\n\n"
        "图片/视频封面理解：\n"
        f"{(media_summary or '（没有可用媒体理解结果）')[:1500]}\n\n"
        "我的个人记忆资料（用于判断我的口吻和背景，不要变成广告）：\n"
        f"{(memory_context or '（未配置个人记忆）')[:12000]}\n\n"
        "请写一句适合发在这条朋友圈下面的中文评论。"
    )
    reply = await _call_sutui_chat_for_native_task(
        auth_context,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是微信朋友圈自然互动回复助手。只输出一句评论，不要解释。"
                    "要求：真诚、轻松、像熟人互动；不营销、不广告、不导流、不销售、不夸大；"
                    "不要出现品牌宣传、私信、加我、下单、优惠等表达；不要太长，优先 8-28 个中文字符。"
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.55,
        timeout=160.0,
    )
    return _clean_moments_reply(reply)


def _find_moments_comment_edit(hwnd: int, *, timeout: float = 6.0) -> Optional[Any]:
    deadline = time.time() + max(1.0, timeout)
    while time.time() < deadline:
        root = _uia_foreground_or_main_root(hwnd)
        edits = _uia_visible_edit_controls(root)
        if edits:
            return sorted(edits, key=_uia_control_rect_score, reverse=True)[0]
        time.sleep(0.25)
    return None


def _focus_moments_detail_comment_box(hwnd: int) -> tuple[int, int, int, int]:
    root = _uia_foreground_or_main_root(hwnd)
    cell = _find_detail_comment_cell(root)
    rect = _uia_rect_tuple(cell) if cell is not None else None
    if rect is None:
        raise RuntimeError("未找到朋友圈评论区域")
    left, top, right, bottom = rect
    # PC 微信 4.x 的朋友圈详情评论框是自绘控件，不暴露 Edit。
    # 点开“评论”后，真实输入区在 DetailCommentCell 底部，不在已有评论列表中部。
    x = int(min(max(left + 116, left + 28), right - 120))
    y = int(min(max(bottom - 86, top + 92), bottom - 30))
    _uia_click_screen_point(x, y)
    time.sleep(0.35)
    return rect


def _click_moments_comment_send(hwnd: int, rect: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = rect
    x = int(max(left + 80, right - 48))
    y = int(max(top + 28, bottom - 28))
    _uia_click_screen_point(x, y)
    time.sleep(0.8)


def _submit_moments_comment_at_point(hwnd: int, point: tuple[int, int], reply: str) -> None:
    menu = _open_moments_action_menu_at_point(hwnd, point[0], point[1])
    # 朋友圈菜单是自绘弹层，UIA Invoke 偶尔不触发；取“评论”按钮矩形后用真实鼠标点中心。
    comment_rect = _uia_rect_tuple(menu.get("comment")) if menu.get("comment") is not None else None
    if comment_rect is not None:
        left, top, right, bottom = comment_rect
        _uia_click_screen_point(int((left + right) / 2), int((top + bottom) / 2))
    else:
        _uia_click_screen_point(max(0, int(point[0]) - 60), int(point[1]))
    time.sleep(0.5)
    edit = _find_moments_comment_edit(hwnd, timeout=6.0)
    if edit is not None:
        _uia_set_text(edit, reply)
        root = _uia_foreground_or_main_root(hwnd)
        send_node = _uia_find_by_names(root, ["发送"], contains=False, max_depth=18)
        if send_node is not None:
            _uia_click(send_node)
        else:
            _send_hotkey("enter", pause=0.3)
        time.sleep(0.8)
        return
    rect = _focus_moments_detail_comment_box(hwnd)
    _paste_text(reply)
    time.sleep(random.uniform(0.4, 1.0))
    _click_moments_comment_send(hwnd, rect)


async def _comment_first_visible_moments_post(
    account_id: str,
    target: str,
    *,
    dry_run: bool,
    auth_context: Dict[str, Any],
    user_id: int,
    album_text: str,
    steps: List[Dict[str, Any]],
    self_names: List[str],
) -> Dict[str, Any]:
    hwnd = _local_wechat_hwnd(account_id)
    if not hwnd:
        raise RuntimeError("没有检测到本机微信窗口")
    root = _uia_foreground_or_main_root(hwnd)
    post = _find_first_visible_timeline_post(root, target)
    if post is None:
        steps.append({"step": "moments_comment_skip", "target": target, "reason": "post_not_found"})
        return {"found": 0, "commented": 0, "already_commented": 0, "skipped": 1, "result": {"target": target, "status": "skipped", "reason": "post_not_found"}}
    post_node = post.get("node")
    post_text = str(post.get("text") or album_text or "")
    time_label = str(post.get("time_label") or "")
    post_key = _moments_post_key(target, post_text, time_label, fallback=album_text)
    steps.append({"step": "moments_comment_post_found", "target": target, "time": time_label, "post_text": post_text[:500]})
    if _moments_comment_record_exists(account_id, target, post_key):
        steps.append({"step": "moments_comment_skip", "target": target, "reason": "already_recorded", "time": time_label})
        return {"found": 1, "commented": 0, "already_commented": 1, "skipped": 0, "result": {"target": target, "status": "already_commented", "reason": "already_recorded"}}
    if post_node is not None and _moments_already_commented_by_self(root, post_node, self_names):
        steps.append({"step": "moments_comment_skip", "target": target, "reason": "already_commented_in_ui", "time": time_label})
        return {"found": 1, "commented": 0, "already_commented": 1, "skipped": 0, "result": {"target": target, "status": "already_commented", "reason": "ui_detected"}}
    point = _moments_action_point_for_post(root, post_node)
    if point is None:
        steps.append({"step": "moments_comment_skip", "target": target, "reason": "action_not_visible", "time": time_label})
        return {"found": 1, "commented": 0, "already_commented": 0, "skipped": 1, "result": {"target": target, "status": "skipped", "reason": "action_not_visible"}}
    snapshot = _capture_post_snapshot_data_url(root, post_node)
    steps.append({"step": "moments_comment_snapshot", "target": target, "ok": bool(snapshot), "time": time_label})
    media_summary = ""
    if snapshot:
        try:
            media_summary = await _understand_moments_post_snapshot(
                auth_context,
                target=target,
                post_text=post_text,
                snapshot_data_url=snapshot,
            )
            steps.append({"step": "moments_comment_media_understood", "target": target, "summary": media_summary[:500]})
        except Exception as exc:
            media_summary = f"视觉理解失败：{exc}"
            steps.append({"step": "moments_comment_media_understand_failed", "target": target, "error": str(exc)})
    reply = await _generate_moments_comment_reply(
        auth_context,
        user_id=user_id,
        target=target,
        post_text=post_text,
        media_summary=media_summary,
    )
    steps.append({"step": "moments_comment_reply_generated", "target": target, "reply": reply})
    if dry_run:
        steps.append({"step": "moments_comment_ready", "target": target, "reply": reply, "time": time_label, "dry_run": True})
        return {
            "found": 1,
            "commented": 0,
            "already_commented": 0,
            "skipped": 0,
            "result": {"target": target, "status": "ready", "reply": reply, "media_summary": media_summary, "post_text": post_text[:800]},
        }
    daily_limit = int(get_strategy().get("daily_moments_comment_limit") or 0)
    if daily_limit > 0 and _local_moments_comment_count_today(account_id) >= daily_limit:
        steps.append({"step": "moments_comment_skip", "target": target, "reason": "daily_limit", "time": time_label})
        return {"found": 1, "commented": 0, "already_commented": 0, "skipped": 1, "result": {"target": target, "status": "skipped", "reason": "daily_limit"}}
    try:
        _submit_moments_comment_at_point(hwnd, point, reply)
    except Exception as exc:
        err = str(exc)
        _record_moments_comment(
            account_id,
            target,
            post_key,
            reply,
            post_text=post_text,
            media_summary=media_summary,
            status="failed",
            error_message=err,
            raw={"time_label": time_label, "self_names": self_names},
        )
        steps.append({"step": "moments_comment_submit_failed", "target": target, "reply": reply, "error": err, "time": time_label})
        return {
            "found": 1,
            "commented": 0,
            "already_commented": 0,
            "skipped": 1,
            "result": {"target": target, "status": "failed", "error": err, "reply": reply, "media_summary": media_summary, "post_text": post_text[:800]},
        }
    _record_moments_comment(
        account_id,
        target,
        post_key,
        reply,
        post_text=post_text,
        media_summary=media_summary,
        status="submitted",
        raw={"time_label": time_label, "self_names": self_names},
    )
    steps.append({"step": "moments_comment_submitted", "target": target, "reply": reply, "time": time_label})
    return {
        "found": 1,
        "commented": 1,
        "already_commented": 0,
        "skipped": 0,
        "result": {"target": target, "status": "submitted", "reply": reply, "media_summary": media_summary, "post_text": post_text[:800]},
    }


async def _scan_and_comment_contact_album_page(
    account_id: str,
    target: str,
    *,
    dry_run: bool,
    auth_context: Dict[str, Any],
    user_id: int,
    steps: List[Dict[str, Any]],
    self_names: List[str],
) -> Dict[str, Any]:
    hwnd = _local_wechat_hwnd(account_id)
    if not hwnd:
        raise RuntimeError("没有检测到本机微信窗口")
    root = _uia_foreground_or_main_root(hwnd)
    cells = _visible_contact_album_cells(root)
    if not cells:
        result = await _comment_first_visible_moments_post(
            account_id,
            target,
            dry_run=dry_run,
            auth_context=auth_context,
            user_id=user_id,
            album_text="",
            steps=steps,
            self_names=self_names,
        )
        _return_contact_album_list(hwnd, steps, target)
        return {**result, "stop_after_24h": False}
    first = cells[0]
    time_label = str(first.get("time_label") or "")
    album_text = str(first.get("text") or "")
    if first.get("outside_24h"):
        steps.append({"step": "moments_comment_stop_after_24h", "target": target, "time": time_label or "unknown"})
        return {"found": 0, "commented": 0, "already_commented": 0, "skipped": 0, "stop_after_24h": True, "result": {"target": target, "status": "skipped", "reason": "outside_24h"}}
    if not first.get("within_24h"):
        steps.append({"step": "moments_comment_skip", "target": target, "reason": "time_unknown", "time": time_label or "unknown"})
        return {"found": 0, "commented": 0, "already_commented": 0, "skipped": 1, "stop_after_24h": False, "result": {"target": target, "status": "skipped", "reason": "time_unknown"}}
    _uia_click(first.get("node"))
    steps.append({"step": "moments_comment_open_post", "target": target, "time": time_label})
    time.sleep(1.0)
    try:
        result = await _comment_first_visible_moments_post(
            account_id,
            target,
            dry_run=dry_run,
            auth_context=auth_context,
            user_id=user_id,
            album_text=album_text,
            steps=steps,
            self_names=self_names,
        )
        return {**result, "stop_after_24h": False}
    finally:
        _return_contact_album_list(hwnd, steps, target)


async def _process_contact_moments_comment_target(
    account_id: str,
    target: str,
    *,
    dry_run: bool,
    max_scrolls: int,
    auth_context: Dict[str, Any],
    user_id: int,
    steps: List[Dict[str, Any]],
    self_names: List[str],
) -> Dict[str, Any]:
    hwnd = _open_local_contact_moments(account_id, target, steps)
    processed_steps = 0
    total = {"found": 0, "commented": 0, "already_commented": 0, "skipped": 0}
    last_result: Dict[str, Any] = {"target": target, "status": "skipped", "reason": "not_processed"}
    stopped_after_24h = False
    try:
        for idx in range(max_scrolls):
            result = await _scan_and_comment_contact_album_page(
                account_id,
                target,
                dry_run=dry_run,
                auth_context=auth_context,
                user_id=user_id,
                steps=steps,
                self_names=self_names,
            )
            processed_steps = idx + 1
            for key in total:
                total[key] += int(result.get(key) or 0)
            last_result = result.get("result") if isinstance(result.get("result"), dict) else last_result
            stopped_after_24h = bool(result.get("stop_after_24h"))
            if result.get("found") or result.get("commented") or result.get("already_commented") or result.get("skipped") or stopped_after_24h:
                break
            _scroll_local_moments(hwnd, -4)
        return {
            **total,
            "processed_steps": processed_steps,
            "stop_after_24h": stopped_after_24h,
            "result": last_result,
        }
    finally:
        _close_foreground_sns_window(hwnd, steps, reason="contact_comment_done")


async def create_add_friend_task(
    account_id: str,
    keywords: List[str],
    *,
    apply_message: str = "",
    remark: str = "",
    tags: Optional[List[str]] = None,
    permission: str = "朋友圈",
    prepare_only: bool = False,
    client_request_id: str = "",
) -> Dict[str, Any]:
    init_db()
    existing = _existing_task_by_client_request_id(account_id, client_request_id)
    if existing:
        return existing
    _find_local_account(account_id)
    strategy = get_strategy()
    max_targets = int(strategy.get("max_targets_per_task") or 0)
    targets = _normalize_task_targets(keywords, max_targets=max_targets)
    if not targets:
        raise RuntimeError("缺少好友关键词")
    daily_limit = int(strategy.get("daily_friend_add_limit") or 0)
    added_today = _local_friend_request_count_today(account_id)
    if daily_limit > 0 and added_today + len(targets) > daily_limit:
        raise RuntimeError(f"daily friend add limit would be exceeded: {added_today}/{daily_limit}")
    task = _create_wechat_task(
        account_id=account_id,
        task_type="add_friend",
        target_type="friend_keyword",
        targets=targets,
        payload={
            "apply_message": str(apply_message or ""),
            "remark": str(remark or ""),
            "tags": tags or [],
            "permission": str(permission or "朋友圈"),
            "prepare_only": bool(prepare_only),
        },
        strategy=strategy,
        client_request_id=client_request_id,
    )
    return task


async def _process_add_friend_task(task: Dict[str, Any]) -> None:
    task_id = str(task.get("id") or "")
    account_id = str(task.get("account_id") or "")
    targets = _normalize_task_targets(list(task.get("targets") or []))
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    strategy = task.get("strategy") if isinstance(task.get("strategy"), dict) else get_strategy()
    success = 0
    failed = 0
    processed = 0
    last_error = ""
    for idx, target in enumerate(targets):
        processed += 1
        ok = False
        err = ""
        for attempt in range(int(strategy.get("retry_max") or 0) + 1):
            try:
                _enforce_local_friend_add_rate(account_id)
                await asyncio.to_thread(
                    add_local_friend,
                    account_id,
                    target,
                    apply_message=str(payload.get("apply_message") or ""),
                    remark=str(payload.get("remark") or ""),
                    tags=list(payload.get("tags") or []),
                    permission=str(payload.get("permission") or "朋友圈"),
                    prepare_only=bool(payload.get("prepare_only")),
                )
                ok = True
                break
            except Exception as exc:
                err = str(exc)
                if attempt < int(strategy.get("retry_max") or 0):
                    await _sleep(float(strategy.get("retry_sleep") or 0))
        if ok:
            success += 1
        else:
            failed += 1
            last_error = err
        _update_task_progress(task_id, processed, success, failed, last_error)
        await _sleep_between_targets(strategy, idx, len(targets), kind="add_friend")
    status = "success" if failed == 0 else ("partial_failed" if success else "failed")
    _finish_task(task_id, status, processed, success, failed, last_error)


async def create_moments_publish_task(
    account_id: str,
    content: str = "",
    *,
    attachments: Optional[List[Dict[str, Any]]] = None,
    media_type: str = "image_text",
    visibility: str = "public",
    client_request_id: str = "",
) -> Dict[str, Any]:
    init_db()
    existing = _existing_task_by_client_request_id(account_id, client_request_id)
    if existing:
        return existing
    if not _local_moments_or_main_hwnd(account_id):
        raise RuntimeError("没有检测到本机微信窗口")
    text = str(content or "").strip()
    files = _normalize_attachments(attachments)
    if not text and not files:
        raise RuntimeError("朋友圈发布缺少正文或素材")
    strategy = get_strategy()
    daily_limit = int(strategy.get("daily_moments_publish_limit") or 0)
    published_today = _local_moments_publish_count_today(account_id)
    if daily_limit > 0 and published_today + 1 > daily_limit:
        raise RuntimeError(f"daily moments publish limit would be exceeded: {published_today}/{daily_limit}")
    return _create_wechat_task(
        account_id=account_id,
        task_type="moments_publish",
        target_type="moments",
        targets=["朋友圈"],
        payload={
            "content": text,
            "attachments": files,
            "media_type": str(media_type or "image_text").strip() or "image_text",
            "visibility": str(visibility or "public").strip() or "public",
        },
        strategy=strategy,
        planned_total=1,
        client_request_id=client_request_id,
    )


async def _process_moments_publish_task(task: Dict[str, Any]) -> None:
    task_id = str(task.get("id") or "")
    account_id = str(task.get("account_id") or "")
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    text = str(payload.get("content") or "").strip()
    files = _normalize_attachments(payload.get("attachments") if isinstance(payload.get("attachments"), list) else [])
    try:
        result = await asyncio.to_thread(
            publish_moments_local,
            account_id,
            text,
            attachments=files,
            media_type=str(payload.get("media_type") or "image_text"),
            visibility=str(payload.get("visibility") or "public"),
        )
        _merge_task_payload(task_id, {"publish_result": result})
        _finish_task(task_id, "success", 1, 1, 0, "")
    except Exception as exc:
        _merge_task_payload(task_id, {"publish_result": {"ok": False, "error": str(exc)}})
        _finish_task(task_id, "failed", 1, 0, 1, str(exc))


async def create_moments_like_task(
    account_id: str,
    targets: List[str],
    *,
    dry_run: bool = False,
    max_scrolls: int = 20,
    client_request_id: str = "",
) -> Dict[str, Any]:
    init_db()
    existing = _existing_task_by_client_request_id(account_id, client_request_id)
    if existing:
        return existing
    if not _local_moments_or_main_hwnd(account_id):
        raise RuntimeError("没有检测到本机微信窗口")
    target_list = _normalize_task_targets(targets, max_targets=100)
    if not target_list:
        raise RuntimeError("缺少朋友圈目标联系人")
    max_scrolls = max(1, min(int(max_scrolls or 20), 120))
    strategy = get_strategy()
    return _create_wechat_task(
        account_id=account_id,
        task_type="moments_like",
        target_type="moments_author",
        targets=target_list,
        payload={"dry_run": bool(dry_run), "max_scrolls": max_scrolls},
        strategy=strategy,
        planned_total=max_scrolls,
        client_request_id=client_request_id,
    )


async def _process_moments_like_task(task: Dict[str, Any]) -> None:
    task_id = str(task.get("id") or "")
    account_id = str(task.get("account_id") or "")
    targets = _normalize_task_targets(list(task.get("targets") or []))
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    strategy = task.get("strategy") if isinstance(task.get("strategy"), dict) else get_strategy()
    dry_run = bool(payload.get("dry_run", True))
    max_scrolls = max(1, min(int(payload.get("max_scrolls") or 20), 120))
    steps: List[Dict[str, Any]] = []
    found_total = 0
    liked_total = 0
    already_total = 0
    skipped_total = 0
    processed_steps = 0
    stopped_after_24h = False
    seen: set[str] = set()
    try:
        hwnd = _local_moments_or_main_hwnd(account_id)
        if not hwnd:
            raise RuntimeError("没有检测到本机微信窗口")
        fallback_targets: List[str] = []
        for target_idx, target in enumerate(targets):
            try:
                result = await asyncio.to_thread(
                    _process_contact_moments_like_target,
                    account_id,
                    target,
                    dry_run=dry_run,
                    max_scrolls=max_scrolls,
                    seen=seen,
                    steps=steps,
                )
            except Exception as exc:
                fallback_targets.append(target)
                steps.append({"step": "contact_moments_fallback", "target": target, "error": str(exc)})
                await _sleep_between_moments_targets(strategy, target_idx, len(targets))
                continue
            found_total += int(result.get("found") or 0)
            liked_total += int(result.get("liked") or 0)
            already_total += int(result.get("already_liked") or 0)
            skipped_total += int(result.get("skipped") or 0)
            processed_steps += int(result.get("processed_steps") or 1)
            success_count = found_total if dry_run else liked_total
            stopped_after_24h = bool(result.get("stop_after_24h"))
            _update_task_progress(
                task_id,
                processed_steps,
                success_count,
                0,
                f"found={found_total}, liked={liked_total}, already={already_total}, skipped={skipped_total}",
            )
            await _sleep_between_moments_targets(strategy, target_idx, len(targets))
        if fallback_targets:
            await asyncio.to_thread(_open_local_moments, hwnd, steps)
            for idx in range(max_scrolls):
                result = await asyncio.to_thread(
                    _scan_and_like_visible_moments,
                    account_id,
                    fallback_targets,
                    dry_run=dry_run,
                    seen=seen,
                    steps=steps,
                )
                found_total += int(result.get("found") or 0)
                liked_total += int(result.get("liked") or 0)
                already_total += int(result.get("already_liked") or 0)
                skipped_total += int(result.get("skipped") or 0)
                processed_steps += 1
                success_count = found_total if dry_run else liked_total
                stopped_after_24h = bool(result.get("stop_after_24h"))
                _update_task_progress(
                    task_id,
                    processed_steps,
                    success_count,
                    0,
                    f"found={found_total}, liked={liked_total}, already={already_total}, skipped={skipped_total}",
                )
                if stopped_after_24h:
                    break
                await asyncio.to_thread(_scroll_local_moments, hwnd, -4)
        status = "success"
        stop_note = ", stop_after_24h=true" if stopped_after_24h else ""
        _finish_task(
            task_id,
            status,
            processed_steps,
            found_total if dry_run else liked_total,
            0,
            f"dry_run={dry_run}, found={found_total}, liked={liked_total}, already={already_total}, skipped={skipped_total}{stop_note}",
        )
    except Exception as exc:
        _finish_task(
            task_id,
            "failed",
            int(task.get("processed") or 0),
            found_total if dry_run else liked_total,
            skipped_total,
            str(exc),
        )


async def create_moments_comment_task(
    account_id: str,
    targets: List[str],
    *,
    dry_run: bool = False,
    max_scrolls: int = 6,
    user_id: int = 0,
    auth_context: Optional[Dict[str, Any]] = None,
    client_request_id: str = "",
) -> Dict[str, Any]:
    init_db()
    existing = _existing_task_by_client_request_id(account_id, client_request_id)
    if existing:
        return existing
    if not _local_moments_or_main_hwnd(account_id):
        raise RuntimeError("没有检测到本机微信窗口")
    target_list = _normalize_task_targets(targets, max_targets=100)
    if not target_list:
        raise RuntimeError("缺少朋友圈评论目标联系人")
    max_scrolls = max(1, min(int(max_scrolls or 6), 30))
    strategy = get_strategy()
    task = _create_wechat_task(
        account_id=account_id,
        task_type="moments_comment",
        target_type="moments_author",
        targets=target_list,
        payload={"dry_run": bool(dry_run), "max_scrolls": max_scrolls, "user_id": int(user_id or 0), "comment_results": []},
        strategy=strategy,
        planned_total=len(target_list),
        auth_context=auth_context or {},
        client_request_id=client_request_id,
    )
    return task


async def _process_moments_comment_task(task: Dict[str, Any]) -> None:
    task_id = str(task.get("id") or "")
    account_id = str(task.get("account_id") or "")
    targets = _normalize_task_targets(list(task.get("targets") or []))
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    strategy = task.get("strategy") if isinstance(task.get("strategy"), dict) else get_strategy()
    dry_run = bool(payload.get("dry_run", False))
    max_scrolls = max(1, min(int(payload.get("max_scrolls") or 6), 30))
    user_id = int(payload.get("user_id") or 0)
    auth_context = dict(_TASK_AUTH_CONTEXT.get(task_id) or {})
    if user_id and not auth_context.get("user_id"):
        auth_context["user_id"] = user_id
    steps: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    found_total = 0
    commented_total = 0
    already_total = 0
    skipped_total = 0
    processed = 0
    last_error = ""
    try:
        if not _local_moments_or_main_hwnd(account_id):
            raise RuntimeError("没有检测到本机微信窗口")
        if not str(auth_context.get("token") or "").strip():
            raise RuntimeError("缺少登录 Token，不能生成朋友圈评论")
        self_names = _local_my_names(account_id)
        for idx, target in enumerate(targets):
            processed += 1
            try:
                result = await _process_contact_moments_comment_target(
                    account_id,
                    target,
                    dry_run=dry_run,
                    max_scrolls=max_scrolls,
                    auth_context=auth_context,
                    user_id=user_id,
                    steps=steps,
                    self_names=self_names,
                )
                found_total += int(result.get("found") or 0)
                commented_total += int(result.get("commented") or 0)
                already_total += int(result.get("already_commented") or 0)
                skipped_total += int(result.get("skipped") or 0)
                item = result.get("result") if isinstance(result.get("result"), dict) else {"target": target, "status": "unknown"}
                results.append(item)
            except Exception as exc:
                last_error = str(exc)
                skipped_total += 1
                results.append({"target": target, "status": "failed", "error": last_error})
            _merge_task_payload(task_id, {"comment_results": results[-50:], "steps": steps[-80:]})
            _update_task_progress(
                task_id,
                processed,
                found_total if dry_run else commented_total,
                skipped_total,
                f"found={found_total}, commented={commented_total}, already={already_total}, skipped={skipped_total}" + (f", last_error={last_error}" if last_error else ""),
            )
            await _sleep_between_moments_targets(strategy, idx, len(targets))
        status = "success" if skipped_total == 0 else ("partial_failed" if commented_total or already_total or (dry_run and found_total) else "failed")
        if dry_run:
            status = "success" if found_total or already_total else status
        _finish_task(
            task_id,
            status,
            processed,
            found_total if dry_run else commented_total,
            skipped_total,
            f"dry_run={dry_run}, found={found_total}, commented={commented_total}, already={already_total}, skipped={skipped_total}" + (f", last_error={last_error}" if last_error else ""),
        )
    except Exception as exc:
        _finish_task(
            task_id,
            "failed",
            processed,
            found_total if dry_run else commented_total,
            skipped_total or int(task.get("planned_total") or 0),
            str(exc),
        )
    finally:
        _TASK_AUTH_CONTEXT.pop(task_id, None)


async def send_text(account_id: str, peer_id: str, text: str, *, context_token: str = "") -> Dict[str, Any]:
    init_db()
    if _is_local_account_id(account_id):
        return _send_text_local(account_id, peer_id, text)
    account = _load_account(account_id)
    token = str(account.get("token") or "")
    if not token:
        raise RuntimeError("账号未连接，请先扫码")
    base_url = str(account.get("baseUrl") or DEFAULT_BASE_URL)
    peer_id = str(peer_id or "").strip()
    text = str(text or "").strip()
    if not peer_id:
        raise RuntimeError("缺少接收人")
    if not text:
        raise RuntimeError("缺少发送内容")
    if not context_token:
        context_token = _load_context_tokens(account_id).get(peer_id, "")
    client_id = f"lobster-wechat-{uuid.uuid4().hex}"
    payload = {
        "msg": {
            "from_user_id": "",
            "to_user_id": peer_id,
            "client_id": client_id,
            "message_type": 2,
            "message_state": 2,
            "item_list": [{"type": 1, "text_item": {"text": text}}],
            **({"context_token": context_token} if context_token else {}),
        },
        "base_info": {"channel_version": CHANNEL_VERSION},
    }
    data = await _api_post(
        base_url,
        "ilink/bot/sendmessage",
        payload,
        token=token,
        timeout_ms=int(DEFAULT_STRATEGY["api_timeout_ms"]),
    )
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            """
            insert into wechat_messages(id, account_id, peer_id, direction, msg_type, content, client_id, status, raw_json, created_at)
            values(?,?,?,?,?,?,?,?,?,?)
            """,
            (uuid.uuid4().hex, account_id, peer_id, "out", "text", text, client_id, "sent", _json_dumps(data), now),
        )
        conn.execute(
            """
            insert into wechat_peers(id, account_id, peer_id, display_name, chat_type, last_outbound_at, created_at, updated_at)
            values(?,?,?,?,?,?,?,?)
            on conflict(account_id, peer_id) do update set
              last_outbound_at=excluded.last_outbound_at,
              updated_at=excluded.updated_at
            """,
            (
                hashlib.sha1(f"{account_id}:{peer_id}".encode("utf-8")).hexdigest(),
                account_id,
                peer_id,
                peer_id,
                "direct",
                now,
                now,
                now,
            ),
        )
    return {"ok": True, "client_id": client_id, "peer_id": peer_id}


def _find_local_account(account_id: str) -> Dict[str, Any]:
    hwnd = _local_hwnd_from_account_id(account_id)
    if not hwnd:
        restored = _ensure_local_wechat_window_visible()
        hwnd = int(((restored.get("windows") or [{}])[0]).get("hwnd") or 0)
    if not hwnd:
        raise RuntimeError("本机微信账号标识无效")
    for item in _scan_local_wechat_windows(max_age_seconds=0):
        if int(item.get("hwnd") or 0) == hwnd:
            return item
    raise RuntimeError("没有检测到对应的本机微信窗口，请打开已登录的 PC 微信主窗口后重试")


def _focus_local_wechat(hwnd: int) -> None:
    try:
        import win32api  # type: ignore
        import win32con  # type: ignore
        import win32gui  # type: ignore
        import win32process  # type: ignore

        if not hwnd or not win32gui.IsWindow(hwnd):
            raise RuntimeError("微信窗口句柄无效")
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.15)
        try:
            flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
        except Exception:
            pass
        current_thread = win32api.GetCurrentThreadId()
        target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
        foreground = win32gui.GetForegroundWindow()
        foreground_thread = win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
        attached: List[int] = []
        for thread_id in {target_thread, foreground_thread}:
            if thread_id and thread_id != current_thread:
                try:
                    win32process.AttachThreadInput(current_thread, thread_id, True)
                    attached.append(thread_id)
                except Exception:
                    pass
        # Foreground locking on Windows is picky; a short Alt pulse is the
        # least invasive way to let SetForegroundWindow succeed.
        try:
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            win32gui.BringWindowToTop(hwnd)
            try:
                win32gui.SetActiveWindow(hwnd)
            except Exception:
                pass
            win32gui.SetForegroundWindow(hwnd)
        finally:
            for thread_id in attached:
                try:
                    win32process.AttachThreadInput(current_thread, thread_id, False)
                except Exception:
                    pass
        time.sleep(0.45)
        if win32gui.GetForegroundWindow() != hwnd:
            try:
                from pywinauto.application import Application  # type: ignore

                Application(backend="uia").connect(handle=hwnd).window(handle=hwnd).set_focus()
                time.sleep(0.35)
            except Exception:
                pass
        if win32gui.GetForegroundWindow() != hwnd:
            raise RuntimeError("微信窗口没有切到前台")
    except Exception as exc:
        raise RuntimeError(f"无法激活本机微信窗口：{exc}") from exc


def _paste_text(text: str) -> None:
    value = str(text or "")
    last_error = ""
    for _idx in range(5):
        try:
            import win32clipboard  # type: ignore
            import win32con  # type: ignore

            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, value)
            finally:
                win32clipboard.CloseClipboard()
            last_error = ""
            break
        except Exception as exc:
            last_error = str(exc)
            try:
                win32clipboard.CloseClipboard()  # type: ignore[name-defined]
            except Exception:
                pass
            time.sleep(0.18)
    if last_error:
        raise RuntimeError(f"本机微信控制组件不可用：剪贴板写入失败：{last_error}")
    time.sleep(0.08)
    _send_hotkey("v", ctrl=True, pause=0.12)


def _clipboard_text(value: str) -> None:
    last_error = ""
    for _idx in range(5):
        try:
            import win32clipboard  # type: ignore
            import win32con  # type: ignore

            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, str(value or ""))
            finally:
                win32clipboard.CloseClipboard()
            return
        except Exception as exc:
            last_error = str(exc)
            try:
                win32clipboard.CloseClipboard()  # type: ignore[name-defined]
            except Exception:
                pass
            time.sleep(0.12)
    raise RuntimeError(f"clipboard write failed: {last_error}")


def _send_hotkey_quick(key: str, *, ctrl: bool = False) -> None:
    try:
        import win32api  # type: ignore
        import win32con  # type: ignore
    except Exception as exc:
        raise RuntimeError("pywin32 keyboard module is required") from exc
    vk_map = {
        "enter": win32con.VK_RETURN,
        "backspace": win32con.VK_BACK,
    }
    vk = ord(key.upper()) if len(key) == 1 else int(vk_map.get(key.lower()) or 0)
    if not vk:
        raise RuntimeError(f"unsupported hotkey: {key}")
    if ctrl:
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(vk, 0, 0, 0)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    if ctrl:
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)


def _paste_text_quick(text: str) -> None:
    _clipboard_text(str(text or ""))
    time.sleep(0.03)
    _send_hotkey_quick("v", ctrl=True)


def _send_hotkey(key: str, *, ctrl: bool = False, pause: float = 0.2) -> None:
    try:
        import win32api  # type: ignore
        import win32con  # type: ignore
    except Exception as exc:
        raise RuntimeError("本机微信控制组件不可用：缺少 pywin32 键盘模块") from exc
    vk_map = {
        "enter": win32con.VK_RETURN,
        "esc": win32con.VK_ESCAPE,
        "tab": win32con.VK_TAB,
        "backspace": win32con.VK_BACK,
    }
    if len(key) == 1:
        vk = ord(key.upper())
    else:
        vk = int(vk_map.get(key.lower()) or 0)
    if not vk:
        raise RuntimeError(f"不支持的快捷键：{key}")
    if ctrl:
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(vk, 0, 0, 0)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    if ctrl:
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
    _human_pause("ui_input_sleep_min", "ui_input_sleep_max", floor=max(0.0, pause))


def _send_keys(keys: str, *, pause: float = 0.2) -> None:
    value = str(keys or "").strip().lower()
    if value == "^f":
        return _send_hotkey("f", ctrl=True, pause=pause)
    if value == "^a":
        return _send_hotkey("a", ctrl=True, pause=pause)
    if value == "{enter}":
        return _send_hotkey("enter", pause=pause)
    if value == "{esc}":
        return _send_hotkey("esc", pause=pause)
    if value == "{tab}":
        return _send_hotkey("tab", pause=pause)
    raise RuntimeError(f"不支持的快捷键：{keys}")


def _local_outbound_count_today(account_id: str) -> int:
    today = datetime.utcnow().date().isoformat()
    with _connect() as conn:
        row = conn.execute(
            """
            select count(*) from wechat_messages
            where account_id=? and direction='out' and status='sent' and created_at >= ?
            """,
            (account_id, today),
        ).fetchone()
    return int((row[0] if row else 0) or 0)


def _enforce_local_send_rate(account_id: str) -> None:
    strategy = get_strategy()
    daily_limit = int(strategy.get("daily_send_limit") or 0)
    if daily_limit > 0 and _local_outbound_count_today(account_id) >= daily_limit:
        raise RuntimeError(f"daily send limit reached: {daily_limit}")
    min_gap = float(strategy.get("local_min_send_gap") or 0)
    if min_gap <= 0:
        return
    with _connect() as conn:
        row = conn.execute(
            """
            select created_at from wechat_messages
            where account_id=? and direction='out' and status='sent'
            order by created_at desc limit 1
            """,
            (account_id,),
        ).fetchone()
    if not row:
        return
    try:
        last = datetime.fromisoformat(str(row["created_at"]))
        elapsed = (datetime.utcnow() - last).total_seconds()
    except Exception:
        return
    if elapsed < min_gap:
        time.sleep(min_gap - elapsed)


def _enforce_local_moments_publish_rate(account_id: str) -> None:
    strategy = get_strategy()
    daily_limit = int(strategy.get("daily_moments_publish_limit") or 0)
    if daily_limit > 0 and _local_moments_publish_count_today(account_id) >= daily_limit:
        raise RuntimeError(f"daily moments publish limit reached: {daily_limit}")
    min_gap = float(strategy.get("moments_publish_min_gap") or 0)
    if min_gap <= 0:
        return
    with _connect() as conn:
        row = conn.execute(
            """
            select updated_at from wechat_tasks
            where account_id=? and task_type='moments_publish' and status='success'
            order by updated_at desc limit 1
            """,
            (account_id,),
        ).fetchone()
    if not row:
        return
    try:
        last = datetime.fromisoformat(str(row["updated_at"]))
        elapsed = (datetime.utcnow() - last).total_seconds()
    except Exception:
        return
    if elapsed < min_gap:
        time.sleep(min_gap - elapsed)


def _send_text_local_legacy(account_id: str, peer_id: str, text: str) -> Dict[str, Any]:
    item = _find_local_account(account_id)
    peer_id = str(peer_id or "").strip()
    text = str(text or "").strip()
    if not peer_id:
        raise RuntimeError("缺少接收人")
    if not text:
        raise RuntimeError("缺少发送内容")
    if not _local_action_driver_ready():
        raise RuntimeError("本机微信已检测到，但本机控制组件不可用，请检查 pywin32")

    hwnd = int(item.get("hwnd") or 0)
    _focus_local_wechat(hwnd)

    # WeChat 4.x exposes little UIA structure, so this follows the same
    # high-level path as wxauto-style drivers: search contact, open chat, paste.
    _send_keys("^f", pause=0.3)
    _send_keys("^a", pause=0.1)
    _paste_text(peer_id)
    time.sleep(0.8)
    _send_keys("{ENTER}", pause=1.0)
    _paste_text(text)
    time.sleep(random.uniform(0.25, 0.55))
    _send_keys("{ENTER}", pause=0.2)

    now = _now_iso()
    client_id = f"lobster-local-wechat-{uuid.uuid4().hex}"
    with _connect() as conn:
        conn.execute(
            """
            insert into wechat_messages(id, account_id, peer_id, direction, msg_type, content, client_id, status, raw_json, created_at)
            values(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                uuid.uuid4().hex,
                account_id,
                peer_id,
                "out",
                "text",
                text,
                client_id,
                "sent",
                _json_dumps({"driver": "pc_wechat_hotkeys", "hwnd": hwnd}),
                now,
            ),
        )
        conn.execute(
            """
            insert into wechat_peers(id, account_id, peer_id, display_name, chat_type, last_outbound_at, created_at, updated_at)
            values(?,?,?,?,?,?,?,?)
            on conflict(account_id, peer_id) do update set
              display_name=excluded.display_name,
              last_outbound_at=excluded.last_outbound_at,
              updated_at=excluded.updated_at
            """,
            (
                hashlib.sha1(f"{account_id}:{peer_id}".encode("utf-8")).hexdigest(),
                account_id,
                peer_id,
                peer_id,
                "direct",
                now,
                now,
                now,
            ),
        )
    return {"ok": True, "client_id": client_id, "peer_id": peer_id, "driver": "pc_wechat_hotkeys"}


def _send_text_local(account_id: str, peer_id: str, text: str) -> Dict[str, Any]:
    # A successful wxauto4 call only means the API accepted the request. The
    # visible chat record is the source of truth, so every local send now uses
    # the same click-and-verify path as automatic replies.
    return _send_text_local_slow(
        account_id,
        peer_id,
        text,
        {"driver": "native_wechat_verified_send", "source": "send_text"},
    )


def _wxauto_visible_message_snapshot(wx: Any) -> Dict[str, Any]:
    if not hasattr(wx, "GetAllMessage"):
        return {"available": False, "count": 0, "items": []}
    try:
        messages = list(wx.GetAllMessage() or [])
    except Exception as exc:
        return {"available": False, "count": 0, "items": [], "error": str(exc)[:500]}
    items: List[Dict[str, Any]] = []
    for index, message in enumerate(messages[-80:]):
        raw = _obj_dict(message)
        msg_type = str(raw.get("type") or "text").strip().lower()
        if msg_type == "time":
            continue
        content = str(raw.get("content") or raw.get("text") or "").strip()
        attr = str(raw.get("attr") or "").strip().lower()
        source_id = str(raw.get("id") or raw.get("hash") or raw.get("hash_text") or "").strip()
        identity = source_id or "|".join(
            (str(index), attr, msg_type, content, str(raw.get("time") or "").strip())
        )
        items.append({"identity": identity, "content": content, "attr": attr, "type": msg_type})
    return {"available": True, "count": len(messages), "items": items}


def _wxauto_snapshot_has_new_outbound(before: Dict[str, Any], after: Dict[str, Any], text: str) -> bool:
    if not after.get("available"):
        return False
    expected = str(text or "").strip()
    if not expected:
        return False
    before_ids = {
        str(item.get("identity") or "")
        for item in (before.get("items") or [])
        if isinstance(item, dict)
    }
    for item in reversed(list(after.get("items") or [])):
        if not isinstance(item, dict):
            continue
        if str(item.get("attr") or "").lower() not in {"self", "out", "me"}:
            continue
        if str(item.get("content") or "").strip() != expected:
            continue
        if str(item.get("identity") or "") not in before_ids:
            return True
    return False


def _local_wechat_draft_text(hwnd: int) -> str:
    try:
        import win32gui  # type: ignore

        window_left, window_top, window_right, window_bottom = win32gui.GetWindowRect(int(hwnd))
        root = _uia_foreground_or_main_root(hwnd)
        candidates: List[tuple[int, Any]] = []
        for edit in _uia_visible_edit_controls(root):
            rect = _uia_rect_tuple(edit)
            if rect is None:
                continue
            left, top, right, bottom = rect
            center_x = (left + right) // 2
            center_y = (top + bottom) // 2
            if center_x < window_left + (window_right - window_left) * 0.35:
                continue
            if center_y < window_top + (window_bottom - window_top) * 0.55:
                continue
            candidates.append((bottom * 1_000_000 + max(0, right - left) * max(0, bottom - top), edit))
        if not candidates:
            return ""
        edit = max(candidates, key=lambda item: item[0])[1]
        return str(_uia_value_text(edit) or "").strip()
    except Exception:
        return ""


def _click_local_wechat_send_button(hwnd: int) -> str:
    _focus_local_wechat(hwnd)
    try:
        root = _uia_foreground_or_main_root(hwnd)
        candidates: List[tuple[int, Any]] = []
        for node in _uia_walk(root, max_depth=18, max_nodes=1200):
            name = _uia_control_text(node)
            if name not in {"发送", "发送(S)", "Send", "Send(S)"}:
                continue
            rect = _uia_rect_tuple(node)
            if rect is None:
                continue
            left, top, right, bottom = rect
            candidates.append((bottom * 1_000_000 + right + max(0, right - left), node))
        if candidates:
            _uia_click(max(candidates, key=lambda item: item[0])[1])
            return "uia_send_button"
    except Exception:
        pass
    try:
        import win32gui  # type: ignore

        left, top, right, bottom = win32gui.GetWindowRect(int(hwnd))
        width = max(1, right - left)
        height = max(1, bottom - top)
        x = right - max(55, min(80, int(width * 0.065)))
        y = bottom - max(50, min(72, int(height * 0.075)))
        _uia_click_screen_point(x, y)
        return "coordinate_send_button"
    except Exception as exc:
        raise RuntimeError(f"未找到微信发送按钮：{exc}") from exc


def _clear_local_wechat_draft(hwnd: int) -> None:
    """Remove only the draft owned by the failed automatic send attempt."""
    try:
        _focus_local_wechat(hwnd)
        _send_hotkey("a", ctrl=True, pause=0.05)
        _send_hotkey_quick("backspace")
    except Exception:
        pass


def _submit_local_wechat_typed_message(
    wx: Any,
    hwnd: int,
    text: str,
    *,
    verify_timeout: float = 5.0,
) -> Dict[str, Any]:
    expected = str(text or "").strip()
    before = _wxauto_visible_message_snapshot(wx)
    methods: List[str] = []
    last_snapshot: Dict[str, Any] = before
    for attempt in range(2):
        methods.append(_click_local_wechat_send_button(hwnd))
        deadline = time.monotonic() + max(0.05, float(verify_timeout))
        while time.monotonic() < deadline:
            time.sleep(0.25)
            last_snapshot = _wxauto_visible_message_snapshot(wx)
            if _wxauto_snapshot_has_new_outbound(before, last_snapshot, expected):
                return {"ok": True, "verified": True, "send_method": methods[-1], "attempts": attempt + 1}
        draft = _local_wechat_draft_text(hwnd)
        if expected and expected not in draft:
            break
        if attempt == 0:
            _focus_local_wechat(hwnd)
    draft = _local_wechat_draft_text(hwnd)
    if expected and expected in draft:
        _clear_local_wechat_draft(hwnd)
        raise RuntimeError("点击微信发送按钮后消息仍停留在输入框，未确认发送成功")
    snapshot_error = str(last_snapshot.get("error") or "").strip()
    if snapshot_error:
        _clear_local_wechat_draft(hwnd)
        raise RuntimeError(f"微信消息发送后无法校验结果：{snapshot_error}")
    _clear_local_wechat_draft(hwnd)
    raise RuntimeError("微信消息发送后未在聊天记录中出现，未确认发送成功")


def _send_text_local_slow_once(
    account_id: str,
    peer_id: str,
    text: str,
    raw_meta: Optional[Dict[str, Any]] = None,
    use_current_chat: bool = False,
) -> Dict[str, Any]:
    item = _find_local_account(account_id)
    peer_id = str(peer_id or "").strip()
    text = str(text or "").strip()
    if not peer_id:
        raise RuntimeError("missing recipient")
    if not text:
        raise RuntimeError("missing text")
    if _looks_like_auto_reply_control_payload(text):
        raise RuntimeError("内部判断数据禁止作为微信回复发送")
    _enforce_local_send_rate(account_id)
    wx = _get_wxauto4_client(account_id)
    hwnd = int(item.get("hwnd") or 0)
    _focus_local_wechat(hwnd)
    if not use_current_chat:
        try:
            wx.ChatWith(peer_id, exact=True, force=False)
        except Exception as exc:
            raise RuntimeError(f"open local WeChat chat failed: {exc}") from exc
        time.sleep(random.uniform(0.55, 1.1))
    _focus_local_wechat(hwnd)

    # ChatWith usually focuses the input box. Clear only the input area, then
    # paste one character at a time so the visible operation is paced like a person.
    _send_hotkey("a", ctrl=True, pause=0.08)
    _send_hotkey_quick("backspace")
    time.sleep(random.uniform(0.18, 0.42))
    char_low = float(DEFAULT_STRATEGY["auto_reply_char_sleep_min"])
    char_high = max(char_low, float(DEFAULT_STRATEGY["auto_reply_char_sleep_max"]))
    punc_low = float(DEFAULT_STRATEGY["auto_reply_punctuation_sleep_min"])
    punc_high = max(punc_low, float(DEFAULT_STRATEGY["auto_reply_punctuation_sleep_max"]))
    for ch in text:
        _paste_text_quick(ch)
        if ch in "。！？!?，,；;：:\n":
            time.sleep(random.uniform(punc_low, punc_high))
        else:
            time.sleep(random.uniform(char_low, char_high))
    time.sleep(random.uniform(0.35, 0.9))
    submit_result = _submit_local_wechat_typed_message(wx, hwnd, text)

    now = _now_iso()
    client_id = f"lobster-local-wechat-auto-{uuid.uuid4().hex}"
    raw = {
        "driver": "pc_wechat_slow_typing",
        "hwnd": hwnd,
        "chat_selection_method": "current_session" if use_current_chat else "wxauto4_chat_with",
        "send_method": submit_result.get("send_method"),
        "send_verified": bool(submit_result.get("verified")),
        "send_attempts": int(submit_result.get("attempts") or 1),
        **(raw_meta or {}),
    }
    with _connect() as conn:
        conn.execute(
            """
            insert into wechat_messages(id, account_id, peer_id, direction, msg_type, content, client_id, status, raw_json, created_at)
            values(?,?,?,?,?,?,?,?,?,?)
            """,
            (uuid.uuid4().hex, account_id, peer_id, "out", "text", text, client_id, "sent", _json_dumps(raw), now),
        )
        conn.execute(
            """
            insert into wechat_peers(id, account_id, peer_id, display_name, chat_type, last_outbound_at, raw_json, created_at, updated_at)
            values(?,?,?,?,?,?,?,?,?)
            on conflict(account_id, peer_id) do update set
              display_name=excluded.display_name,
              chat_type=case when excluded.chat_type != 'unknown' then excluded.chat_type else wechat_peers.chat_type end,
              last_outbound_at=excluded.last_outbound_at,
              raw_json=excluded.raw_json,
              updated_at=excluded.updated_at
            """,
            (_stable_key(account_id, peer_id), account_id, peer_id, peer_id, "direct", now, _json_dumps(raw), now, now),
        )
    return {"ok": True, "client_id": client_id, "peer_id": peer_id, "driver": "pc_wechat_slow_typing"}


def _send_text_local_slow(
    account_id: str,
    peer_id: str,
    text: str,
    raw_meta: Optional[Dict[str, Any]] = None,
    use_current_chat: bool = False,
) -> Dict[str, Any]:
    return _run_local_driver_operation(
        account_id,
        "发送微信自动回复",
        lambda: _send_text_local_slow_once(account_id, peer_id, text, raw_meta, use_current_chat),
        # Sending is verified from the visible chat record. If the UI driver
        # loses its COM/UIA handle mid-send, rebuild the driver and retry the
        # same operation instead of leaving the task stuck until the user
        # restarts WeChat.
        retry_on_failure=True,
    )


def _send_files_local(account_id: str, peer_id: str, attachments: List[Dict[str, Any]]) -> Dict[str, Any]:
    _find_local_account(account_id)
    peer_id = str(peer_id or "").strip()
    if not peer_id:
        raise RuntimeError("缺少接收人")
    files = _normalize_attachments(attachments)
    if not files:
        raise RuntimeError("缺少附件")
    _enforce_local_send_rate(account_id)
    wx = _get_wxauto4_client(account_id)
    try:
        resp = wx.SendFiles([item["local_path"] for item in files], who=peer_id, exact=True)
    except Exception as exc:
        raise RuntimeError(f"local WeChat SendFiles failed: {exc}") from exc
    raw = _obj_dict(resp)
    if not bool(resp):
        raise RuntimeError(f"local WeChat SendFiles failed: {resp}")
    now = _now_iso()
    client_id = f"lobster-local-wechat-file-{uuid.uuid4().hex}"
    with _connect() as conn:
        for item in files:
            conn.execute(
                """
                insert into wechat_messages(id, account_id, peer_id, direction, msg_type, content, client_id, status, raw_json, created_at)
                values(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    uuid.uuid4().hex,
                    account_id,
                    peer_id,
                    "out",
                    item.get("kind") or "file",
                    item.get("filename") or Path(item["local_path"]).name,
                    client_id,
                    "sent",
                    _json_dumps({"driver": "wxauto4.SendFiles", "file": item, "raw": raw}),
                    now,
                ),
            )
        conn.execute(
            """
            insert into wechat_peers(id, account_id, peer_id, display_name, chat_type, last_outbound_at, raw_json, created_at, updated_at)
            values(?,?,?,?,?,?,?,?,?)
            on conflict(account_id, peer_id) do update set
              display_name=excluded.display_name,
              last_outbound_at=excluded.last_outbound_at,
              raw_json=excluded.raw_json,
              updated_at=excluded.updated_at
            """,
            (_stable_key(account_id, peer_id), account_id, peer_id, peer_id, "direct", now, _json_dumps({"files": files}), now, now),
        )
    return {"ok": True, "client_id": client_id, "peer_id": peer_id, "driver": "wxauto4.SendFiles", "files": files, "raw": raw}


def _moments_publish_hwnd(main_hwnd: int) -> int:
    return _find_visible_local_moments_hwnd() or int(main_hwnd or 0)


def _moments_publish_dialog_ready(root: Any) -> bool:
    if root is None:
        return False
    text = "\n".join(_uia_control_text(node) for node in _uia_walk(root, max_depth=12, max_nodes=900) if _uia_control_text(node))
    return ("这一刻的想法" in text or "谁可以看" in text) and ("发表" in text or "取消" in text)


def _click_moments_publish_entry(hwnd: int, steps: List[Dict[str, Any]]) -> int:
    publish_hwnd = _moments_publish_hwnd(hwnd)
    if not publish_hwnd:
        raise RuntimeError("未找到朋友圈窗口")
    _focus_local_wechat(publish_hwnd)
    root = _uia_foreground_or_main_root(publish_hwnd)
    if _moments_publish_dialog_ready(root):
        steps.append({"step": "open_moments_publish", "ok": True, "entry": "already_open"})
        return publish_hwnd

    entry = _uia_find_by_names(root, ["发布朋友圈", "发朋友圈", "相机", "拍照分享"], contains=True, max_depth=12)
    if entry is not None:
        _uia_click(entry)
        steps.append({"step": "open_moments_publish", "ok": True, "method": "uia", "entry": _uia_control_text(entry)})
    else:
        rect = _uia_rect_tuple(root)
        if rect is None:
            raise RuntimeError("未找到朋友圈窗口位置，无法打开发布入口")
        left, top, _right, _bottom = rect
        _uia_click_screen_point(left + 75, top + 23)
        steps.append({"step": "open_moments_publish", "ok": True, "method": "coordinate"})

    deadline = time.time() + 8.0
    while time.time() < deadline:
        root = _uia_foreground_or_main_root(publish_hwnd)
        if _moments_publish_dialog_ready(root):
            steps.append({"step": "moments_publish_dialog_ready", "ok": True})
            return publish_hwnd
        time.sleep(0.25)
    raise RuntimeError("朋友圈发布窗口未打开")


def _find_moments_publish_text_edit(root: Any) -> Optional[Any]:
    edits = _uia_visible_edit_controls(root)
    if not edits:
        return None
    scored: List[tuple[int, Any]] = []
    for edit in edits:
        rect = _uia_rect_tuple(edit)
        if rect is None:
            continue
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top
        name = _uia_control_text(edit)
        score = 0
        if "这一刻" in name or "想法" in name:
            score += 120
        if width >= 200:
            score += 40
        if height >= 40:
            score += 20
        if top > 0:
            score -= min(top // 20, 40)
        scored.append((score, edit))
    if not scored:
        return sorted(edits, key=_uia_control_rect_score, reverse=True)[0]
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _focus_moments_publish_text(hwnd: int, steps: List[Dict[str, Any]]) -> None:
    root = _uia_foreground_or_main_root(hwnd)
    if not _moments_publish_dialog_ready(root):
        raise RuntimeError("朋友圈发布窗口已失去焦点，已阻止正文写入其他聊天窗口")
    edit = _find_moments_publish_text_edit(root)
    if edit is not None:
        try:
            edit.SetFocus()
        except Exception:
            pass
        try:
            edit.Click(simulateMove=True)
        except Exception:
            pass
        steps.append({"step": "focus_moments_text", "ok": True, "method": "uia"})
        _human_pause("ui_input_sleep_min", "ui_input_sleep_max", floor=0.15)
        return
    rect = _uia_rect_tuple(root)
    if rect is None:
        raise RuntimeError("未找到朋友圈发布输入框")
    left, top, _right, _bottom = rect
    _uia_click_screen_point(left + 185, top + 68)
    steps.append({"step": "focus_moments_text", "ok": True, "method": "coordinate"})


def _paste_moments_text_human(text: str) -> None:
    value = str(text or "").strip()
    if not value:
        return
    chunks = re.findall(r"[\s\S]{1,120}", value)
    for idx, chunk in enumerate(chunks):
        _paste_text_quick(chunk)
        if idx < len(chunks) - 1:
            time.sleep(random.uniform(0.35, 0.85))
    time.sleep(random.uniform(0.4, 1.0))


def _uia_value_text(node: Any) -> str:
    if node is None:
        return ""
    for getter in ("GetValuePattern", "ValuePattern"):
        try:
            pattern = getattr(node, getter)
            pattern = pattern() if callable(pattern) else pattern
            if pattern is None:
                continue
            for attr in ("Value", "CurrentValue"):
                value = getattr(pattern, attr, None)
                if value is not None:
                    return str(value or "").strip()
        except Exception:
            pass
    for attr in ("Value", "CurrentValue"):
        try:
            value = getattr(node, attr, None)
            if value is not None:
                return str(value or "").strip()
        except Exception:
            pass
    return ""


def _compact_for_contains(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _moments_publish_text_present(root: Any, edit: Any, expected: str) -> bool:
    wanted = _compact_for_contains(expected)
    if not wanted:
        return True
    edit_value = _compact_for_contains(_uia_value_text(edit) or _uia_control_text(edit))
    if wanted in edit_value:
        return True
    root_text = _compact_for_contains("\n".join(_uia_control_text(node) for node in _uia_walk(root, max_depth=14, max_nodes=1200) if _uia_control_text(node)))
    return wanted in root_text


def _click_uia_center(node: Any) -> bool:
    rect = _uia_rect_tuple(node)
    if rect is None:
        return False
    left, top, right, bottom = rect
    _uia_click_screen_point((left + right) // 2, (top + bottom) // 2)
    return True


def _fill_moments_publish_text(hwnd: int, text: str, steps: List[Dict[str, Any]]) -> None:
    value = str(text or "").strip()
    if not value:
        return
    last_error = ""
    for attempt in range(1, 4):
        root = _uia_foreground_or_main_root(hwnd)
        if not _moments_publish_dialog_ready(root):
            raise RuntimeError("朋友圈发布窗口已失去焦点，已阻止正文写入其他聊天窗口")
        edit = _find_moments_publish_text_edit(root)
        if edit is None:
            last_error = "未找到朋友圈发布输入框"
            time.sleep(0.35)
            continue
        try:
            edit.SetFocus()
        except Exception:
            pass
        clicked = False
        try:
            edit.Click(simulateMove=True)
            clicked = True
        except Exception:
            clicked = _click_uia_center(edit)
        _human_pause("ui_input_sleep_min", "ui_input_sleep_max", floor=0.12)

        method = "value_pattern"
        if not _uia_try_set_value(edit, value):
            method = "clipboard"
            if clicked:
                try:
                    _send_hotkey("a", ctrl=True, pause=0.08)
                    _send_hotkey_quick("backspace")
                except Exception:
                    pass
            _paste_text(value)

        time.sleep(0.5)
        root = _uia_foreground_or_main_root(hwnd)
        if _moments_publish_text_present(root, edit, value):
            steps.append({"step": "fill_moments_text", "ok": True, "chars": len(value), "method": method, "attempt": attempt, "clicked": clicked})
            return
        last_error = "正文输入后未在朋友圈发布框中检测到"
        steps.append({"step": "fill_moments_text_retry", "ok": False, "attempt": attempt, "method": method, "reason": last_error})
        time.sleep(0.4)
    raise RuntimeError(f"朋友圈正文未输入成功：{last_error or 'unknown'}")


def _find_moments_publish_plus(root: Any) -> Optional[Any]:
    root_rect = _uia_rect_tuple(root)
    if root_rect is None:
        return None
    root_left, root_top, root_right, root_bottom = root_rect
    best_node: Optional[Any] = None
    best_score = -1
    for node in _uia_walk(root, max_depth=16, max_nodes=1400):
        rect = _uia_rect_tuple(node)
        if rect is None:
            continue
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top
        rel_left = left - root_left
        rel_top = top - root_top
        if not (80 <= rel_left <= 260 and 120 <= rel_top <= 320):
            continue
        if not (35 <= width <= 130 and 35 <= height <= 130):
            continue
        name = _uia_control_text(node)
        class_name = _uia_control_class(node)
        score = 0
        if name in {"+", "添加", "添加照片", "添加图片", "添加视频"}:
            score += 90
        if "Button" in class_name or "Image" in class_name:
            score += 20
        if abs(width - height) <= 25:
            score += 20
        if 120 <= rel_left <= 220 and 160 <= rel_top <= 270:
            score += 20
        if right <= root_right and bottom <= root_bottom and score > best_score:
            best_score = score
            best_node = node
    return best_node if best_score >= 20 else None


def _find_moments_publish_submit(root: Any) -> Optional[Any]:
    root_rect = _uia_rect_tuple(root)
    if root_rect is None:
        return None
    root_left, root_top, root_right, root_bottom = root_rect
    root_width = max(1, root_right - root_left)
    root_height = max(1, root_bottom - root_top)
    best_node: Optional[Any] = None
    best_score = -1
    for node in _uia_walk(root, max_depth=20, max_nodes=1800):
        name = _uia_control_text(node)
        if name not in {"发表", "发布"}:
            continue
        rect = _uia_rect_tuple(node)
        if rect is None:
            continue
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top
        rel_left = left - root_left
        rel_top = top - root_top
        if not (45 <= width <= 180 and 24 <= height <= 70):
            continue
        if rel_top < root_height * 0.68:
            continue
        try:
            if bool(getattr(node, "IsOffscreen", False)):
                continue
        except Exception:
            pass
        try:
            if hasattr(node, "IsEnabled") and not bool(getattr(node, "IsEnabled")):
                continue
        except Exception:
            pass
        class_name = _uia_control_class(node)
        control_type = str(getattr(node, "ControlTypeName", "") or "")
        score = 100
        if "Button" in class_name or "Button" in control_type:
            score += 50
        if rel_left < root_width * 0.55:
            score += 25
        if bottom <= root_bottom and top >= root_top:
            score += 15
        if score > best_score:
            best_score = score
            best_node = node
    return best_node if best_score >= 100 else None


def _click_moments_publish_submit_node(node: Any, steps: List[Dict[str, Any]], attempt: int) -> bool:
    rect = _uia_rect_tuple(node)
    if rect is not None:
        left, top, right, bottom = rect
        _uia_click_screen_point((left + right) // 2, (top + bottom) // 2)
        steps.append({"step": "submit_moments_publish", "ok": True, "method": "button_center", "attempt": attempt})
        return True
    try:
        _uia_click(node)
        steps.append({"step": "submit_moments_publish", "ok": True, "method": "uia", "attempt": attempt})
        return True
    except Exception as exc:
        steps.append({"step": "submit_moments_publish", "ok": False, "method": "uia", "attempt": attempt, "error": str(exc)})
        return False


def _file_dialog_filename_edit(root: Any) -> Optional[Any]:
    edits = _uia_visible_edit_controls(root)
    if not edits:
        return None
    scored: List[tuple[int, Any]] = []
    for edit in edits:
        rect = _uia_rect_tuple(edit)
        if rect is None:
            continue
        left, top, right, bottom = rect
        width = right - left
        name = _uia_control_text(edit)
        score = bottom + min(width, 500)
        if "文件名" in name or "File name" in name:
            score += 1000
        scored.append((score, edit))
    if not scored:
        return sorted(edits, key=_uia_control_rect_score, reverse=True)[0]
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _select_files_in_open_dialog(hwnd: int, files: List[Dict[str, Any]], steps: List[Dict[str, Any]]) -> None:
    paths = [str(item.get("local_path") or "").strip() for item in files if str(item.get("local_path") or "").strip()]
    if not paths:
        return
    file_spec = " ".join(f'"{path}"' for path in paths)
    deadline = time.time() + 10.0
    edit = None
    root = None
    while time.time() < deadline:
        root = _uia_foreground_or_main_root(hwnd)
        edit = _file_dialog_filename_edit(root)
        if edit is not None:
            break
        time.sleep(0.25)
    if edit is None or root is None:
        raise RuntimeError("未找到系统文件选择框")
    _uia_set_text(edit, file_spec)
    steps.append({"step": "select_moments_files", "ok": True, "count": len(paths)})
    open_btn = _uia_find_by_names(root, ["打开(&O)", "打开", "Open"], contains=True, max_depth=16)
    if open_btn is not None:
        _uia_click(open_btn)
    else:
        _send_hotkey("enter", pause=0.25)
    time.sleep(1.5)


def _add_moments_publish_files(hwnd: int, files: List[Dict[str, Any]], steps: List[Dict[str, Any]]) -> None:
    if not files:
        return
    root = _uia_foreground_or_main_root(hwnd)
    plus = _find_moments_publish_plus(root)
    if plus is not None:
        _uia_click(plus)
        steps.append({"step": "open_moments_file_picker", "ok": True, "method": "uia"})
    else:
        rect = _uia_rect_tuple(root)
        if rect is None:
            raise RuntimeError("未找到朋友圈发布窗口位置，无法添加素材")
        left, top, _right, _bottom = rect
        _uia_click_screen_point(left + 175, top + 215)
        steps.append({"step": "open_moments_file_picker", "ok": True, "method": "coordinate"})
    _select_files_in_open_dialog(hwnd, files, steps)
    time.sleep(random.uniform(1.0, 2.0))


def _submit_moments_publish(hwnd: int, steps: List[Dict[str, Any]]) -> None:
    last_error = ""
    for attempt in range(1, 4):
        deadline = time.time() + 8.0
        submit = None
        root = None
        while time.time() < deadline:
            root = _uia_foreground_or_main_root(hwnd)
            submit = _find_moments_publish_submit(root) or _uia_find_by_names(root, ["发表", "发布"], contains=False, max_depth=20)
            if submit is not None:
                break
            time.sleep(0.25)
        if submit is not None:
            if not _click_moments_publish_submit_node(submit, steps, attempt):
                last_error = "发表按钮点击失败"
                continue
        else:
            if root is None:
                root = _uia_foreground_or_main_root(hwnd)
            rect = _uia_rect_tuple(root)
            if rect is None:
                last_error = "未找到朋友圈发表按钮"
                continue
            left, _top, right, bottom = rect
            width = max(1, right - left)
            x = left + max(70, min(int(width * 0.34), width - 70))
            y = bottom - 45
            _uia_click_screen_point(x, y)
            steps.append({"step": "submit_moments_publish", "ok": True, "method": "coordinate", "attempt": attempt, "point": [x, y]})

        confirm_deadline = time.time() + 10.0
        while time.time() < confirm_deadline:
            root = _uia_foreground_or_main_root(hwnd)
            if not _moments_publish_dialog_ready(root):
                time.sleep(0.8)
                steps.append({"step": "moments_publish_closed", "ok": True, "attempt": attempt})
                return
            time.sleep(0.35)
        last_error = "发表后窗口仍未关闭"
        steps.append({"step": "submit_moments_publish_retry", "ok": False, "attempt": attempt, "reason": last_error})
    raise RuntimeError(f"朋友圈发表后窗口仍未关闭，请检查是否未真正提交：{last_error or 'unknown'}")


def _publish_moments_local_once(
    account_id: str,
    content: str = "",
    *,
    attachments: Optional[List[Dict[str, Any]]] = None,
    media_type: str = "image_text",
    visibility: str = "public",
) -> Dict[str, Any]:
    item = _find_local_account(account_id)
    files = _normalize_attachments(attachments)
    text = str(content or "").strip()
    if not text and not files:
        raise RuntimeError("朋友圈发布缺少正文或素材")
    image_count = sum(1 for file in files if file.get("kind") == "image")
    video_count = sum(1 for file in files if file.get("kind") == "video")
    if image_count and video_count:
        raise RuntimeError("朋友圈一次发布暂不混合图片和视频")
    if image_count > 9:
        raise RuntimeError("朋友圈图文一次最多选择9张图片")
    if video_count > 1:
        raise RuntimeError("朋友圈视频一次只支持1个视频")
    _enforce_local_moments_publish_rate(account_id)
    steps: List[Dict[str, Any]] = []
    hwnd = int(item.get("hwnd") or 0)
    _open_local_moments(hwnd, steps)
    publish_hwnd = _click_moments_publish_entry(hwnd, steps)
    if files:
        _add_moments_publish_files(publish_hwnd, files, steps)
    if text:
        _focus_moments_publish_text(publish_hwnd, steps)
        _fill_moments_publish_text(publish_hwnd, text, steps)
    _submit_moments_publish(publish_hwnd, steps)
    return {
        "ok": True,
        "account_id": account_id,
        "content_length": len(text),
        "media_type": media_type or ("video" if video_count else "image_text"),
        "visibility": visibility or "public",
        "attachments": [
            {
                "filename": item.get("filename"),
                "kind": item.get("kind"),
                "size": item.get("size"),
            }
            for item in files
        ],
        "steps": steps,
        "driver": "pc_wechat_moments_uia",
    }


def publish_moments_local(
    account_id: str,
    content: str = "",
    *,
    attachments: Optional[List[Dict[str, Any]]] = None,
    media_type: str = "image_text",
    visibility: str = "public",
) -> Dict[str, Any]:
    return _run_local_driver_operation(
        account_id,
        "发布朋友圈",
        lambda: _publish_moments_local_once(
            account_id,
            content,
            attachments=attachments,
            media_type=media_type,
            visibility=visibility,
        ),
        retry_on_failure=False,
    )


def _group_picker_root(hwnd: int) -> Any:
    root = _uia_foreground_or_main_root(hwnd)
    class_name = _uia_control_class(root)
    if class_name == "mmui::SessionPickerWindow" or class_name.endswith("SessionPickerWindow"):
        return root
    # The picker can be exposed as a child of the registered main window when
    # its transient top-level handle is not foreground. Never treat a normal
    # chat window as a picker merely because it contains a text named "完成";
    # that allowed a WeChat ID to be typed into the conversation composer.
    try:
        main_root = _uia_main_root(hwnd)
    except Exception:
        return None
    for node in _uia_walk(main_root, max_depth=12, max_nodes=2600):
        node_class = _uia_control_class(node)
        if node_class == "mmui::SessionPickerWindow" or node_class.endswith("SessionPickerWindow"):
            return node
    return None


def _open_local_create_group_picker(account_id: str, steps: List[Dict[str, Any]]) -> int:
    hwnd = _local_wechat_hwnd(account_id)
    if not hwnd:
        raise RuntimeError("没有检测到本机微信窗口")
    _ensure_local_tab(hwnd, "微信", strict=True)
    root = _uia_foreground_or_main_root(hwnd)
    plus_button = _uia_find_add_friend_plus_button(root)
    if plus_button is not None:
        _uia_click(plus_button)
        steps.append({"step": "open_quick_menu", "ok": True, "method": "uia"})
    else:
        root_rect = _uia_rect_tuple(root)
        if root_rect is None:
            raise RuntimeError("未找到微信窗口位置，无法打开快捷操作")
        left, top, _, _ = root_rect
        _uia_click_screen_point(left + 238, top + 40)
        steps.append({"step": "open_quick_menu", "ok": True, "method": "coordinate"})

    entry = _uia_wait_for_names(hwnd, ["发起群聊"], timeout=4.0, contains=False)
    if entry is None:
        raise RuntimeError("未找到发起群聊入口")
    _uia_click(entry)
    steps.append({"step": "open_group_picker", "ok": True})
    deadline = time.time() + 8.0
    while time.time() < deadline:
        root = _uia_foreground_or_main_root(hwnd)
        if _uia_control_class(root) == "mmui::SessionPickerWindow" or _uia_find_by_names(root, ["完成"], max_depth=12):
            return hwnd
        time.sleep(0.25)
    raise RuntimeError("发起群聊选择窗口未打开")


def _find_current_chat_more_button(root: Any) -> Optional[Any]:
    root_rect = _uia_rect_tuple(root)
    if root_rect is None:
        return None
    left, top, right, bottom = root_rect
    width = max(1, right - left)
    candidates: List[tuple[int, Any]] = []
    for node in _uia_walk(root, max_depth=18, max_nodes=2400):
        name = _uia_control_text(node)
        if name not in {"更多", "...", "…"}:
            continue
        rect = _uia_rect_tuple(node)
        if rect is None:
            continue
        n_left, n_top, n_right, n_bottom = rect
        center_x = (n_left + n_right) / 2
        center_y = (n_top + n_bottom) / 2
        if center_x < left + width * 0.62 or center_y < top or center_y > top + 120:
            continue
        score = int(center_x * 10)
        class_name = _uia_control_class(node).lower()
        if "button" in class_name:
            score += 1000
        candidates.append((score, node))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _current_chat_header_rect(root: Any) -> Optional[Tuple[int, int, int, int]]:
    for node in _uia_walk(root, max_depth=12, max_nodes=1200):
        if _uia_control_class(node) == "mmui::ChatTitleBarChatSingleView":
            rect = _uia_rect_tuple(node)
            if rect is not None:
                return rect
    return _uia_rect_tuple(root)


def _current_chat_icon_point(root: Any, *, kind: str) -> Optional[Tuple[int, int]]:
    rect = _current_chat_header_rect(root)
    if rect is None:
        return None
    _left, top, right, _bottom = rect
    # Weixin 4.1 renders these controls as icon-only views with no UIA name.
    # Their positions are stable relative to the single-chat title bar.
    if kind == "more":
        return right - 32, top + 49
    if kind == "add":
        return right - 158, top + 102
    return None


def _find_current_chat_add_button(root: Any) -> Optional[Any]:
    root_rect = _uia_rect_tuple(root)
    if root_rect is None:
        return None
    left, top, right, _bottom = root_rect
    width = max(1, right - left)
    candidates: List[tuple[int, Any]] = []
    for node in _uia_walk(root, max_depth=20, max_nodes=2600):
        if _uia_control_text(node) != "添加":
            continue
        rect = _uia_rect_tuple(node)
        if rect is None:
            continue
        n_left, n_top, n_right, n_bottom = rect
        center_x = (n_left + n_right) / 2
        center_y = (n_top + n_bottom) / 2
        if center_x < left + width * 0.55 or center_y < top + 35:
            continue
        target = node
        class_name = _uia_control_class(node).lower()
        # Weixin exposes the visible label as XTextView, but the clickable
        # action is its ChatMemberActionView parent. Clicking the text child
        # leaves the member panel open and makes the run appear to stall.
        for _idx in range(5):
            if "chatmemberactionview" in class_name:
                break
            try:
                parent = target.GetParentControl()
            except Exception:
                parent = None
            if parent is None:
                break
            target = parent
            class_name = _uia_control_class(target).lower()
        target_rect = _uia_rect_tuple(target) or rect
        target_left, target_top, target_right, target_bottom = target_rect
        target_center_x = (target_left + target_right) / 2
        target_center_y = (target_top + target_bottom) / 2
        score = int(target_center_x * 10 + target_center_y)
        if "chatmemberactionview" in class_name:
            score += 2000
        elif "button" in class_name or "view" in class_name:
            score += 100
        candidates.append((score, target))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _open_current_chat_add_contact_picker(account_id: str, steps: List[Dict[str, Any]]) -> int:
    """Open the add-member picker from the already selected private chat."""
    hwnd = _local_wechat_hwnd(account_id)
    if not hwnd:
        raise RuntimeError("没有检测到本机微信窗口")
    _focus_local_wechat(hwnd)
    time.sleep(0.3)
    # The group action must start from the registered WeChat main window. A
    # small auxiliary WebView can temporarily become foreground after a click,
    # but it does not contain the current chat toolbar or picker controls.
    main_root = _uia_main_root(hwnd)
    root = main_root
    # Do not toggle an already-open member panel closed. This is common after
    # a previous picker timeout and was the reason the next run lost "添加".
    add = _find_current_chat_add_button(root)
    if add is None:
        more = _find_current_chat_more_button(root)
        if more is None:
            root = _uia_foreground_or_main_root(hwnd)
            more = _find_current_chat_more_button(root)
        if more is not None:
            _uia_click(more)
            steps.append({"step": "open_current_chat_more", "ok": True, "method": "uia"})
        else:
            point = _current_chat_icon_point(main_root, kind="more")
            if point is None:
                raise RuntimeError("当前会话未找到右上角更多按钮")
            _uia_click_screen_point(*point)
            steps.append({"step": "open_current_chat_more", "ok": True, "method": "icon_coordinate", "point": point})
    deadline = time.monotonic() + 5.0
    add = None
    while time.monotonic() < deadline:
        root = main_root
        add = _find_current_chat_add_button(root)
        if add is None:
            foreground_root = _uia_foreground_or_main_root(hwnd)
            add = _find_current_chat_add_button(foreground_root)
            if add is not None:
                root = foreground_root
        if add is not None:
            break
        time.sleep(0.2)
    if add is not None:
        _uia_click(add)
        steps.append({"step": "open_current_chat_add_contact_picker", "ok": True, "method": "uia"})
    else:
        point = _current_chat_icon_point(main_root, kind="add")
        if point is None:
            raise RuntimeError("当前会话更多面板未找到添加联系人入口")
        _uia_click_screen_point(*point)
        steps.append({"step": "open_current_chat_add_contact_picker", "ok": True, "method": "icon_coordinate", "point": point})
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        root = _group_picker_root(hwnd)
        if _find_group_picker_search_edit(root) is not None:
            return hwnd
        time.sleep(0.25)
    raise RuntimeError("当前会话添加联系人选择窗口未打开")


def _find_group_picker_search_edit(root: Any) -> Optional[Any]:
    if root is None:
        return None
    edits = _uia_visible_edit_controls(root)
    if not edits:
        return None
    return sorted(edits, key=_uia_control_rect_score, reverse=True)[0]


def _uia_optional_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if callable(value):
        try:
            value = value()
        except Exception:
            return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"true", "1", "on", "selected", "checked"}:
        return True
    if text in {"false", "0", "off", "unselected", "unchecked"}:
        return False
    return None


def _uia_group_picker_selection_state(node: Any) -> Optional[bool]:
    """Read the selection state without relying on a screenshot or OCR."""
    if node is None:
        return None
    sources: List[Any] = [node]
    for getter in ("GetSelectionItemPattern", "GetTogglePattern", "SelectionItemPattern", "TogglePattern"):
        try:
            pattern = getattr(node, getter, None)
            pattern = pattern() if callable(pattern) else pattern
        except Exception:
            pattern = None
        if pattern is not None:
            sources.append(pattern)
    for source in sources:
        for attr in ("IsSelected", "CurrentIsSelected", "Selected", "CurrentToggleState", "ToggleState"):
            try:
                state = _uia_optional_bool(getattr(source, attr, None))
            except Exception:
                state = None
            if state is not None:
                return state
    return None


def _group_picker_selected_count(root: Any) -> Optional[int]:
    if root is None:
        return None
    for node in _uia_walk(root, max_depth=14, max_nodes=1200):
        text = _uia_control_text(node)
        if not text:
            continue
        match = re.search(r"已选择\s*(\d+)\s*个联系人", text)
        if match:
            return int(match.group(1))
    return None


def _group_picker_selection_target(cell: Any) -> Any:
    """Prefer the row checkbox; clicking the text child is not sufficient in WeChat."""
    cell_rect = _uia_rect_tuple(cell)
    candidates: List[tuple[int, Any]] = []
    for node in _uia_walk(cell, max_depth=6, max_nodes=120):
        if node is cell:
            continue
        class_name = _uia_control_class(node).lower()
        control_type = str(getattr(node, "ControlTypeName", "") or "").lower()
        if not any(token in class_name or token in control_type for token in ("checkbox", "radiobutton", "check", "toggle")):
            continue
        try:
            if bool(getattr(node, "IsOffscreen", False)):
                continue
            if hasattr(node, "IsEnabled") and not bool(getattr(node, "IsEnabled")):
                continue
        except Exception:
            pass
        score = 100
        if "checkbox" in class_name or "checkbox" in control_type:
            score += 40
        if cell_rect and _uia_rect_tuple(node):
            left, top, right, bottom = _uia_rect_tuple(node) or (0, 0, 0, 0)
            c_left, c_top, c_right, c_bottom = cell_rect
            if c_left <= left <= right <= c_right and c_top <= top <= bottom <= c_bottom:
                score += 20
        candidates.append((score, node))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    return cell


def _find_group_picker_contact_node(root: Any, target: str) -> Optional[Any]:
    if root is None:
        return None
    wanted = str(target or "").strip()
    if not wanted:
        return None
    wanted_compact = _compact_for_contains(wanted).lower()
    exact: List[Any] = []
    fallback: List[Any] = []
    for node in _uia_walk(root, max_depth=20, max_nodes=2200):
        name = _uia_control_text(node)
        if not name:
            continue
        class_name = _uia_control_class(node)
        is_contact_like = (
            class_name == "mmui::ContactsCellItemView"
            or class_name.endswith("ContactsCellItemView")
            or class_name == "mmui::SearchContactCellView"
            or class_name.endswith("SearchContactCellView")
        )
        if not is_contact_like:
            continue
        compact_name = _compact_for_contains(name).lower()
        if compact_name == wanted_compact:
            exact.append(node)
        elif wanted_compact and wanted_compact in compact_name:
            fallback.append(node)
    return (exact or fallback or [None])[0]


def _find_first_group_picker_search_result(root: Any) -> Optional[Any]:
    """Return the unique result produced by an exact WeChat-ID search."""
    if root is None:
        return None
    candidates: List[Any] = []
    for node in _uia_walk(root, max_depth=20, max_nodes=2200):
        class_name = _uia_control_class(node)
        if class_name != "mmui::SearchContactCellView" and not class_name.endswith("SearchContactCellView"):
            continue
        if not _uia_control_text(node):
            continue
        try:
            if bool(getattr(node, "IsOffscreen", False)):
                continue
        except Exception:
            pass
        candidates.append(node)
    return candidates[0] if len(candidates) == 1 else None


def _group_picker_search_terms(target: str) -> List[str]:
    value = str(target or "").strip()
    if not value:
        return []
    if _looks_like_wechat_id(value):
        return [value]
    terms = [value]
    prefix = re.split(r"[-_－—]", value, maxsplit=1)[0].strip()
    if prefix and prefix != value:
        terms.append(prefix)
    return list(dict.fromkeys(terms))


def _select_group_picker_contact(hwnd: int, account_id: str, target: str, steps: List[Dict[str, Any]]) -> None:
    root = _group_picker_root(hwnd)
    edit = _find_group_picker_search_edit(root)
    if edit is None:
        raise RuntimeError("未找到发起群聊搜索框")
    resolved_wx_no = _resolve_local_contact_wx_no(account_id, target)
    aliases = _resolve_local_contact_aliases(account_id, target) if not resolved_wx_no else [resolved_wx_no]
    search_terms: List[str] = []
    for value in aliases or [str(target or "").strip()]:
        for term in _group_picker_search_terms(value):
            if term and term not in search_terms:
                search_terms.append(term)
    node: Optional[Any] = None
    search_term = ""
    for search_term in search_terms:
        _uia_set_text(edit, search_term)
        # ValuePattern updates the field but some Weixin builds do not refresh
        # the result list until the edit receives an Enter event.
        _send_hotkey("enter", pause=0.08)
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            time.sleep(0.25)
            root = _group_picker_root(hwnd)
            for candidate in (target, *aliases, search_term):
                candidate = str(candidate or "").strip()
                if not candidate:
                    continue
                node = _find_group_picker_contact_node(root, candidate)
                if node is not None:
                    break
            if node is None and resolved_wx_no:
                node = _find_first_group_picker_search_result(root)
            if node is not None:
                break
        if node is not None:
            break
    if node is None:
        raise RuntimeError(f"未找到联系人：{target}")
    before_count = _group_picker_selected_count(root)
    selection_target = _group_picker_selection_target(node)
    before_state = _uia_group_picker_selection_state(selection_target)
    if before_state is not True:
        _uia_click(selection_target)

    deadline = time.monotonic() + 3.0
    verified = False
    after_count: Optional[int] = before_count
    after_state: Optional[bool] = before_state
    while time.monotonic() < deadline:
        time.sleep(0.2)
        latest_root = _group_picker_root(hwnd)
        latest_node = _find_group_picker_contact_node(latest_root, target)
        if latest_node is None and resolved_wx_no:
            latest_node = _find_first_group_picker_search_result(latest_root)
        latest_target = _group_picker_selection_target(latest_node) if latest_node is not None else selection_target
        after_state = _uia_group_picker_selection_state(latest_target)
        after_count = _group_picker_selected_count(latest_root)
        if after_state is True or (
            before_count is not None and after_count is not None and after_count > before_count
        ):
            verified = True
            break
    if not verified and before_state is not True:
        raise RuntimeError(f"联系人已显示但未确认勾选成功：{target}")
    steps.append(
        {
            "step": "select_group_contact",
            "ok": True,
            "target": target,
            "resolved_wx_no": resolved_wx_no,
            "search_term": search_term,
            "selection_method": _uia_control_class(selection_target) or "contact_cell",
            "verified": True,
            "selected_count_before": before_count,
            "selected_count_after": after_count,
        }
    )


def _finish_local_create_group(hwnd: int, steps: List[Dict[str, Any]]) -> None:
    root = _group_picker_root(hwnd)
    done = _uia_find_by_names(root, ["完成"], contains=False, max_depth=18)
    if done is None:
        raise RuntimeError("未找到创建群完成按钮")
    _uia_click(done)
    steps.append({"step": "finish_create_group", "ok": True})
    time.sleep(1.5)
    _confirm_create_new_group_if_needed(hwnd, steps)


def _confirm_create_new_group_if_needed(hwnd: int, steps: List[Dict[str, Any]]) -> None:
    deadline = time.time() + 6.0
    while time.time() < deadline:
        root = _uia_foreground_or_main_root(hwnd)
        create_new = _uia_find_by_names(root, ["创建新群"], contains=False, max_depth=18)
        if create_new is not None:
            _uia_click(create_new)
            steps.append({"step": "create_new_group_confirm", "ok": True})
            time.sleep(1.5)
            return
        if _uia_find_by_names(root, ["选择或创建群聊"], contains=False, max_depth=18) is not None:
            create_new = _uia_find_by_names(root, ["创建新群"], contains=True, max_depth=18)
            if create_new is not None:
                _uia_click(create_new)
                steps.append({"step": "create_new_group_confirm", "ok": True, "method": "contains"})
                time.sleep(1.5)
                return
        time.sleep(0.25)
    steps.append({"step": "create_new_group_confirm", "ok": True, "skipped": "not_needed"})


def _verify_created_local_group(
    account_id: str,
    targets: List[str],
    *,
    timeout_seconds: float = 8.0,
) -> Dict[str, Any]:
    expected_count = len(targets) + 1
    wx = _get_wxauto4_client(account_id)
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    last_info: Dict[str, Any] = {}
    while time.monotonic() < deadline:
        info = wx.ChatInfo() if hasattr(wx, "ChatInfo") else {}
        if not isinstance(info, dict):
            info = _obj_dict(info)
        last_info = dict(info or {})
        chat_type = str(info.get("chat_type") or "").strip().lower()
        group_key = str(info.get("chat_name") or "").strip()
        try:
            member_count = int(info.get("group_member_count") or 0)
        except (TypeError, ValueError):
            member_count = 0
        if chat_type in {"group", "chatroom"} and group_key:
            if member_count <= 0 and hasattr(wx, "GetGroupMembers"):
                try:
                    member_count = len(list(wx.GetGroupMembers() or []))
                except Exception:
                    member_count = 0
            if member_count >= expected_count:
                return {
                    "group_key": group_key,
                    "chat_info": info,
                    "member_count": member_count,
                    "expected_member_count": expected_count,
                }
        time.sleep(0.35)
    raise RuntimeError(
        f"微信建群未通过结果核验：chat_type={last_info.get('chat_type') or 'unknown'}, "
        f"chat_name={last_info.get('chat_name') or ''}, "
        f"member_count={last_info.get('group_member_count') or 0}, expected={expected_count}"
    )


def _create_local_group_once(account_id: str, contacts: List[str]) -> Dict[str, Any]:
    init_db()
    _find_local_account(account_id)
    targets = _normalize_task_targets(contacts, max_targets=100)
    if len(targets) < 2:
        raise RuntimeError("创建群至少选择2个联系人")
    steps: List[Dict[str, Any]] = []
    hwnd = _open_local_create_group_picker(account_id, steps)
    selected = 0
    try:
        for target in targets:
            _select_group_picker_contact(hwnd, account_id, target, steps)
            selected += 1
        _finish_local_create_group(hwnd, steps)
    except Exception:
        try:
            _send_hotkey("esc", pause=0.25)
        except Exception:
            pass
        raise
    verification = _verify_created_local_group(account_id, targets)
    info = verification.get("chat_info") if isinstance(verification.get("chat_info"), dict) else {}
    group_key = str(verification.get("group_key") or "").strip()
    saved = _persist_group(
        account_id,
        {
            "group_key": group_key,
            "display_name": group_key,
            "member_count": int(verification.get("member_count") or 0),
            "source": "pc_wechat_uia_created_group",
            "raw": {"contacts": targets, "steps": steps, "chat_info": info},
        },
    )
    return {
        "ok": True,
        "contacts": targets,
        "selected": selected,
        "group": saved,
        "steps": steps,
        "group_verified": True,
        "verified_member_count": int(verification.get("member_count") or 0),
        "verification": verification,
    }


def _create_local_group_from_current_once(
    account_id: str,
    contacts: List[str],
    added_contacts: List[str],
) -> Dict[str, Any]:
    """Add configured contacts to the already-open customer conversation."""
    init_db()
    _find_local_account(account_id)
    targets = _normalize_task_targets(contacts, max_targets=100)
    additions = _normalize_task_targets(added_contacts, max_targets=99)
    if len(targets) < 2 or not additions:
        raise RuntimeError("当前会话拉群至少需要1个预设联系人")
    steps: List[Dict[str, Any]] = []
    hwnd = _open_current_chat_add_contact_picker(account_id, steps)
    selected = 0
    try:
        for target in additions:
            _select_group_picker_contact(hwnd, account_id, target, steps)
            selected += 1
        _finish_local_create_group(hwnd, steps)
    except Exception:
        try:
            _send_hotkey("esc", pause=0.25)
        except Exception:
            pass
        raise
    verification = _verify_created_local_group(account_id, targets)
    info = verification.get("chat_info") if isinstance(verification.get("chat_info"), dict) else {}
    group_key = str(verification.get("group_key") or "").strip()
    saved = _persist_group(
        account_id,
        {
            "group_key": group_key,
            "display_name": group_key,
            "member_count": int(verification.get("member_count") or 0),
            "source": "pc_wechat_uia_current_chat_created_group",
            "raw": {"contacts": targets, "added_contacts": additions, "steps": steps, "chat_info": info},
        },
    )
    return {
        "ok": True,
        "contacts": targets,
        "added_contacts": additions,
        "selected": selected,
        "group": saved,
        "steps": steps,
        "group_verified": True,
        "verified_member_count": int(verification.get("member_count") or 0),
        "verification": verification,
        "selection_method": "current_chat_more_add",
    }


def create_local_group(account_id: str, contacts: List[str]) -> Dict[str, Any]:
    return _run_local_driver_operation(
        account_id,
        "创建微信群",
        lambda: _create_local_group_once(account_id, contacts),
        retry_on_failure=False,
    )


def create_local_group_from_current(
    account_id: str,
    contacts: List[str],
    added_contacts: List[str],
) -> Dict[str, Any]:
    return _run_local_driver_operation(
        account_id,
        "当前会话拉群",
        lambda: _create_local_group_from_current_once(account_id, contacts, added_contacts),
        retry_on_failure=False,
    )


async def send_message(account_id: str, peer_id: str, text: str = "", *, attachments: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    text = str(text or "").strip()
    files = _normalize_attachments(attachments)
    if not text and not files:
        raise RuntimeError("缺少发送内容或附件")
    if files and not _is_local_account_id(account_id):
        raise RuntimeError("附件发送仅支持本机 PC 微信")
    results: List[Dict[str, Any]] = []
    if text:
        results.append(await send_text(account_id, peer_id, text))
    if files:
        results.append(_send_files_local(account_id, peer_id, files))
    return {"ok": True, "peer_id": peer_id, "results": results}


def _normalize_task_targets(targets: List[str], *, max_targets: int = 0) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for raw in targets or []:
        value = str(raw or "").strip()
        if not value:
            continue
        # External callers often pass a pasted block of phone numbers / sessions.
        parts = [x.strip() for x in re.split(r"[\s,，;；]+", value) if x.strip()]
        for part in parts:
            key = part.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(part)
    if max_targets > 0 and len(out) > max_targets:
        raise RuntimeError(f"too many targets in one task: max {max_targets}")
    return out


def _existing_task_by_client_request_id(account_id: str, client_request_id: str) -> Optional[Dict[str, Any]]:
    key = str(client_request_id or "").strip()[:180]
    if not key:
        return None
    with _connect() as conn:
        row = conn.execute(
            "select * from wechat_tasks where account_id=? and client_request_id=? limit 1",
            (account_id, key),
        ).fetchone()
    if not row:
        return None
    item = _row_to_dict(row)
    item["deduped"] = True
    return item


def _create_wechat_task(
    *,
    account_id: str,
    task_type: str,
    target_type: str,
    targets: List[str],
    payload: Dict[str, Any],
    strategy: Dict[str, Any],
    planned_total: Optional[int] = None,
    auth_context: Optional[Dict[str, Any]] = None,
    client_request_id: str = "",
    start_worker: bool = True,
) -> Dict[str, Any]:
    client_request_id = str(client_request_id or "").strip()[:180]
    if client_request_id:
        existing = _existing_task_by_client_request_id(account_id, client_request_id)
        if existing:
            return existing
    task_id = uuid.uuid4().hex
    now = _now_iso()
    total = int(planned_total if planned_total is not None else len(targets))
    try:
        with _connect() as conn:
            conn.execute(
                """
                insert into wechat_tasks(id, account_id, task_type, target_type, targets, payload, strategy, status, planned_total, created_at, updated_at, client_request_id)
                values(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    account_id,
                    task_type,
                    target_type,
                    _json_dumps(targets),
                    _json_dumps(payload),
                    _json_dumps(strategy),
                    "pending",
                    total,
                    now,
                    now,
                    client_request_id or None,
                ),
            )
    except sqlite3.IntegrityError:
        if not client_request_id:
            raise
        existing = _existing_task_by_client_request_id(account_id, client_request_id)
        if not existing:
            raise
        return existing
    if auth_context:
        _TASK_AUTH_CONTEXT[task_id] = dict(auth_context)
    if start_worker:
        _ensure_task_worker(account_id)
    return get_task(task_id) or {"id": task_id, "status": "pending", "planned_total": total}


def _ensure_task_worker(account_id: str) -> None:
    account_id = str(account_id or "").strip()
    if not account_id:
        return
    existing = _TASK_WORKERS.get(account_id)
    if existing is not None and not existing.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _TASK_WORKERS[account_id] = loop.create_task(_run_account_task_queue(account_id))


def _claim_next_pending_task(account_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            """
            select * from wechat_tasks
            where account_id=? and status='pending'
            order by created_at asc limit 1
            """,
            (account_id,),
        ).fetchone()
        if not row:
            return None
        task_id = str(row["id"])
        conn.execute(
            "update wechat_tasks set status='running', updated_at=? where id=? and status='pending'",
            (_now_iso(), task_id),
        )
        row = conn.execute("select * from wechat_tasks where id=? limit 1", (task_id,)).fetchone()
    return _row_to_dict(row) if row else None


def _has_pending_task(account_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "select id from wechat_tasks where account_id=? and status='pending' limit 1",
            (account_id,),
        ).fetchone()
    return bool(row)


async def _run_account_task_queue(account_id: str) -> None:
    try:
        while True:
            task = _claim_next_pending_task(account_id)
            if not task:
                return
            try:
                if task.get("task_type") in {"send_text", "send_message"}:
                    await _process_send_task(task)
                elif task.get("task_type") == "add_friend":
                    await _process_add_friend_task(task)
                elif task.get("task_type") == "moments_like":
                    await _process_moments_like_task(task)
                elif task.get("task_type") == "moments_comment":
                    await _process_moments_comment_task(task)
                elif task.get("task_type") == "moments_publish":
                    await _process_moments_publish_task(task)
                elif task.get("task_type") == "create_group":
                    await _process_create_group_task(task)
                else:
                    _finish_task(str(task.get("id") or ""), "failed", 0, 0, int(task.get("planned_total") or 0), "unsupported task type")
            except Exception as exc:
                _finish_task(
                    str(task.get("id") or ""),
                    "failed",
                    int(task.get("processed") or 0),
                    int(task.get("success") or 0),
                    int(task.get("failed") or 0) or int(task.get("planned_total") or 0),
                    str(exc),
                )
    finally:
        current = _TASK_WORKERS.get(account_id)
        if current is asyncio.current_task():
            _TASK_WORKERS.pop(account_id, None)
        if _has_pending_task(account_id):
            _ensure_task_worker(account_id)


def _finish_task(task_id: str, status: str, processed: int, success: int, failed: int, error_message: str = "") -> None:
    if not task_id:
        return
    with _connect() as conn:
        conn.execute(
            "update wechat_tasks set status=?, processed=?, success=?, failed=?, error_message=?, updated_at=? where id=?",
            (status, processed, success, failed, error_message, _now_iso(), task_id),
        )


def _update_task_payload(task_id: str, patch: Dict[str, Any]) -> None:
    if not task_id or not isinstance(patch, dict):
        return
    with _connect() as conn:
        row = conn.execute("select payload from wechat_tasks where id=? limit 1", (task_id,)).fetchone()
        payload = _safe_json_loads(str(row["payload"] or ""), {}) if row else {}
        if not isinstance(payload, dict):
            payload = {}
        payload.update(patch)
        conn.execute(
            "update wechat_tasks set payload=?, updated_at=? where id=?",
            (_json_dumps(payload), _now_iso(), task_id),
        )


def _update_task_progress(task_id: str, processed: int, success: int, failed: int, error_message: str = "") -> None:
    with _connect() as conn:
        conn.execute(
            "update wechat_tasks set processed=?, success=?, failed=?, error_message=?, updated_at=? where id=?",
            (processed, success, failed, error_message, _now_iso(), task_id),
        )


def _merge_task_payload(task_id: str, patch: Dict[str, Any]) -> None:
    if not task_id or not patch:
        return
    with _connect() as conn:
        row = conn.execute("select payload from wechat_tasks where id=?", (task_id,)).fetchone()
        if not row:
            return
        payload = _safe_json_loads(row["payload"], {})
        if not isinstance(payload, dict):
            payload = {}
        payload.update(patch)
        conn.execute(
            "update wechat_tasks set payload=?, updated_at=? where id=?",
            (_json_dumps(payload), _now_iso(), task_id),
        )


async def _sleep_between_targets(strategy: Dict[str, Any], idx: int, total: int, *, kind: str) -> None:
    if idx >= total - 1:
        return
    if kind == "add_friend":
        batch_size = max(1, int(strategy.get("friend_add_batch_size") or 1))
        if (idx + 1) % batch_size == 0:
            await _sleep(float(strategy.get("friend_add_batch_sleep") or 0))
            return
        low = float(strategy.get("friend_add_sleep_min") or 60)
        high = max(low, float(strategy.get("friend_add_sleep_max") or low))
        await _sleep(random.uniform(low, high))
        return
    batch_size = max(1, int(strategy.get("batch_size") or 1))
    if (idx + 1) % batch_size == 0:
        await _sleep(float(strategy.get("batch_sleep") or 0))
    else:
        low = float(strategy.get("send_sleep_min") or 0)
        high = max(low, float(strategy.get("send_sleep_max") or low))
        await _sleep(random.uniform(low, high))


async def _sleep_between_moments_targets(strategy: Dict[str, Any], idx: int, total: int) -> None:
    if idx >= total - 1:
        return
    low = float(strategy.get("moments_like_sleep_min") or 20.0)
    high = max(low, float(strategy.get("moments_like_sleep_max") or low))
    await _sleep(random.uniform(low, high))


async def create_send_task(
    account_id: str,
    targets: List[str],
    text: str,
    *,
    target_type: str = "direct",
    attachments: Optional[List[Dict[str, Any]]] = None,
    client_request_id: str = "",
) -> Dict[str, Any]:
    init_db()
    existing = _existing_task_by_client_request_id(account_id, client_request_id)
    if existing:
        return existing
    strategy = get_strategy()
    max_targets = int(strategy.get("max_targets_per_task") or 0)
    targets = _normalize_task_targets(targets, max_targets=max_targets)
    if not targets:
        raise RuntimeError("缺少接收人")
    text = str(text or "").strip()
    files = _normalize_attachments(attachments)
    if not text and not files:
        raise RuntimeError("缺少发送内容或附件")
    if _is_local_account_id(account_id):
        daily_limit = int(strategy.get("daily_send_limit") or 0)
        sent_today = _local_outbound_count_today(account_id)
        if daily_limit > 0 and sent_today + len(targets) > daily_limit:
            raise RuntimeError(f"daily send limit would be exceeded: {sent_today}/{daily_limit}")
    return _create_wechat_task(
        account_id=account_id,
        task_type="send_message" if files else "send_text",
        target_type=target_type,
        targets=targets,
        payload={"text": text, "attachments": files},
        strategy=strategy,
        client_request_id=client_request_id,
    )


async def _process_send_task(task: Dict[str, Any]) -> None:
    task_id = str(task.get("id") or "")
    account_id = str(task.get("account_id") or "")
    targets = _normalize_task_targets(list(task.get("targets") or []))
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    strategy = task.get("strategy") if isinstance(task.get("strategy"), dict) else get_strategy()
    text = str(payload.get("text") or "")
    files = _normalize_attachments(payload.get("attachments") if isinstance(payload.get("attachments"), list) else [])
    success = 0
    failed = 0
    processed = 0
    last_error = ""
    for idx, target in enumerate(targets):
        processed += 1
        ok = False
        err = ""
        for attempt in range(int(strategy["retry_max"]) + 1):
            try:
                await send_message(account_id, target, text, attachments=files)
                ok = True
                break
            except Exception as exc:
                err = str(exc)
                if attempt < int(strategy["retry_max"]):
                    await _sleep(float(strategy["retry_sleep"]))
        if ok:
            success += 1
        else:
            failed += 1
            last_error = err
            error_text = text or "；".join([str(item.get("filename") or Path(item.get("local_path") or "").name) for item in files])
            _persist_task_error_message(account_id, target, error_text, err)
        _update_task_progress(task_id, processed, success, failed, last_error)
        await _sleep_between_targets(strategy, idx, len(targets), kind="send")
    status = "success" if failed == 0 else ("partial_failed" if success else "failed")
    _finish_task(task_id, status, processed, success, failed, last_error)


def _existing_create_group_task(account_id: str, dedup_key: str) -> Optional[Dict[str, Any]]:
    key = str(dedup_key or "").strip()[:160]
    if not key:
        return None
    with _connect() as conn:
        rows = conn.execute(
            """
            select * from wechat_tasks
            where account_id=? and task_type='create_group'
              and status in ('pending','running','success','partial_failed')
            order by created_at desc limit 200
            """,
            (account_id,),
        ).fetchall()
    for row in rows:
        item = _row_to_dict(row)
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if str(payload.get("dedup_key") or "").strip() != key:
            continue
        status = str(item.get("status") or "").strip().lower()
        if status in {"pending", "running"} or (
            status in {"success", "partial_failed"} and bool(payload.get("group_verified"))
        ):
            item["deduped"] = True
            return item
    return None


def _has_verified_group_invite(account_id: str, peer_id: str, primary_contact: str) -> bool:
    peer_id = str(peer_id or "").strip()
    primary_contact = str(primary_contact or "").strip()
    if not peer_id or not primary_contact:
        return False
    with _connect() as conn:
        rows = conn.execute(
            """
            select * from wechat_tasks
            where account_id=? and task_type='create_group'
              and status in ('success','partial_failed')
            order by created_at desc limit 200
            """,
            (account_id,),
        ).fetchall()
    for row in rows:
        item = _row_to_dict(row)
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        targets = [str(value or "").strip() for value in (item.get("targets") or [])]
        if not bool(payload.get("group_verified")):
            continue
        if str(payload.get("source_peer_id") or "").strip() != peer_id:
            continue
        if primary_contact not in targets:
            continue
        return True
    return False


async def create_group_task(
    account_id: str,
    contacts: List[str],
    *,
    welcome_message: str = "",
    dedup_key: str = "",
    source_peer_id: str = "",
    source_inbound_message_id: str = "",
    group_invite_reason: str = "",
    matched_group_keywords: Optional[List[str]] = None,
    customer_wx_no: str = "",
    use_current_chat: bool = False,
    execute_now: bool = False,
    client_request_id: str = "",
) -> Dict[str, Any]:
    init_db()
    existing_by_request = _existing_task_by_client_request_id(account_id, client_request_id)
    if existing_by_request:
        return existing_by_request
    _find_local_account(account_id)
    targets = _normalize_task_targets(contacts, max_targets=100)
    if len(targets) < 2:
        raise RuntimeError("创建群至少选择2个联系人")
    normalized_dedup_key = str(dedup_key or "").strip()[:160]
    existing = _existing_create_group_task(account_id, normalized_dedup_key)
    if existing:
        return existing
    strategy = get_strategy()
    task = _create_wechat_task(
        account_id=account_id,
        task_type="create_group",
        target_type="group_contacts",
        targets=targets,
        payload={
            "welcome_message": str(welcome_message or "").strip()[:4000],
            "dedup_key": normalized_dedup_key,
            "source_peer_id": str(source_peer_id or "").strip()[:240],
            "source_inbound_message_id": str(source_inbound_message_id or "").strip()[:160],
            "customer_wx_no": str(customer_wx_no or "").strip()[:240],
            "use_current_chat": bool(use_current_chat),
            "group_invite_reason": str(group_invite_reason or "").strip()[:300],
            "matched_group_keywords": list(
                dict.fromkeys(str(item or "").strip() for item in (matched_group_keywords or []) if str(item or "").strip())
            )[:20],
        },
        strategy=strategy,
        planned_total=len(targets),
        client_request_id=client_request_id,
        start_worker=not execute_now,
    )
    if execute_now:
        await _process_create_group_task(task)
        return get_task(str(task.get("id") or "")) or task
    return task


async def _process_create_group_task(task: Dict[str, Any]) -> None:
    task_id = str(task.get("id") or "")
    account_id = str(task.get("account_id") or "")
    targets = _normalize_task_targets(list(task.get("targets") or []))
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    welcome_message = str(payload.get("welcome_message") or "").strip()[:4000]
    try:
        if bool(payload.get("use_current_chat")):
            customer_target = str(payload.get("customer_wx_no") or (targets[0] if targets else "")).strip()
            added_targets = [target for target in targets if target != customer_target]
            result = await asyncio.to_thread(
                create_local_group_from_current,
                account_id,
                targets,
                added_targets,
            )
        else:
            result = await asyncio.to_thread(create_local_group, account_id, targets)
        if not bool(result.get("group_verified")):
            raise RuntimeError("微信建群结果未通过群聊和成员校验")
        selected = int(result.get("selected") or len(targets))
        group = result.get("group") if isinstance(result.get("group"), dict) else {}
        group_key = str(group.get("group_key") or group.get("display_name") or "").strip()
        welcome_result: Dict[str, Any] = {}
        welcome_error = ""
        if welcome_message:
            if not group_key:
                welcome_error = "群已创建，但未识别到群名称，默认欢迎话术未发送"
            else:
                try:
                    welcome_args = (
                        account_id,
                        group_key,
                        welcome_message,
                        {"driver": "native_wechat_group_welcome", "group_task_id": task_id},
                    )
                    if bool(payload.get("use_current_chat")):
                        welcome_args = (*welcome_args, True)
                    welcome_result = await asyncio.to_thread(_send_text_local_slow, *welcome_args)
                except Exception as exc:
                    welcome_error = f"群已创建，但默认欢迎话术发送失败：{exc}"
        _update_task_payload(
            task_id,
            {
                "group_result": result,
                "welcome_message": welcome_message,
                "welcome_sent": bool(welcome_message and welcome_result and not welcome_error),
                "welcome_result": welcome_result,
                "group_verified": True,
                "verified_member_count": int(result.get("verified_member_count") or 0),
            },
        )
        _finish_task(
            task_id,
            "partial_failed" if welcome_error else "success",
            selected,
            selected,
            1 if welcome_error else 0,
            welcome_error,
        )
        source_peer_id = str(payload.get("source_peer_id") or "").strip()
        if source_peer_id:
            await _observe_wechat_intelligence(
                _AUTO_REPLY_AUTH_CONTEXT.get(account_id),
                {
                    "account_id": account_id,
                    "contact_key": source_peer_id,
                    "event_type": "group_created",
                    "status": "completed",
                    "inbound_message_id": str(payload.get("source_inbound_message_id") or "")[:255],
                    "payload": {
                        "group_task_id": task_id,
                        "selected": selected,
                        "welcome_sent": bool(welcome_message and welcome_result and not welcome_error),
                        "group_verified": True,
                        "group_key": group_key,
                        "welcome_error": welcome_error[:2000],
                    },
                    "error_message": welcome_error[:2000],
                },
            )
    except Exception as exc:
        _finish_task(task_id, "failed", 0, 0, len(targets), str(exc))
        source_peer_id = str(payload.get("source_peer_id") or "").strip()
        source_inbound_id = str(payload.get("source_inbound_message_id") or "").strip()
        if source_peer_id and source_inbound_id:
            _mark_auto_reply_group_invite_failed(account_id, source_peer_id, source_inbound_id, str(exc))
        if source_peer_id:
            await _observe_wechat_intelligence(
                _AUTO_REPLY_AUTH_CONTEXT.get(account_id),
                {
                    "account_id": account_id,
                    "contact_key": source_peer_id,
                    "event_type": "group_created",
                    "status": "failed",
                    "inbound_message_id": source_inbound_id[:255],
                    "payload": {"group_task_id": task_id},
                    "error_message": str(exc)[:2000],
                },
            )


def _persist_task_error_message(account_id: str, peer_id: str, text: str, err: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            insert into wechat_messages(id, account_id, peer_id, direction, msg_type, content, status, error_message, created_at)
            values(?,?,?,?,?,?,?,?,?)
            """,
            (uuid.uuid4().hex, account_id, peer_id, "out", "text", text, "failed", err[:1000], _now_iso()),
        )


_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".heic"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v", ".3gp"}


def native_wechat_upload_dir() -> Path:
    NATIVE_WECHAT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return NATIVE_WECHAT_UPLOAD_DIR


def _safe_upload_filename(name: str) -> str:
    raw = Path(name or "file").name.strip() or "file"
    raw = re.sub(r"[^\w\u4e00-\u9fff.\-()\[\] ]+", "_", raw, flags=re.UNICODE).strip(" .")
    return raw[:120] or "file"


def make_native_wechat_upload_path(filename: str) -> Path:
    safe = _safe_upload_filename(filename)
    suffix = Path(safe).suffix[:20]
    stem = Path(safe).stem[:80] or "file"
    return native_wechat_upload_dir() / f"{uuid.uuid4().hex}_{stem}{suffix}"


def native_wechat_file_kind(path: Path, content_type: str = "") -> str:
    suffix = path.suffix.lower()
    ctype = (content_type or mimetypes.guess_type(str(path))[0] or "").lower()
    if suffix in _IMAGE_SUFFIXES or ctype.startswith("image/"):
        return "image"
    if suffix in _VIDEO_SUFFIXES or ctype.startswith("video/"):
        return "video"
    return "file"


def _resolve_native_wechat_attachment(item: Dict[str, Any]) -> Dict[str, Any]:
    raw_path = str(item.get("local_path") or item.get("path") or "").strip()
    if not raw_path:
        raise RuntimeError("附件缺少本地路径")
    path = Path(raw_path).expanduser().resolve()
    upload_root = native_wechat_upload_dir().resolve()
    try:
        inside = path.is_relative_to(upload_root)
    except AttributeError:
        inside = str(path).lower().startswith(str(upload_root).lower() + os.sep)
    if not inside:
        raise RuntimeError("附件路径不在微信附件上传目录")
    if not path.exists() or not path.is_file():
        raise RuntimeError(f"附件不存在：{path.name}")
    filename = str(item.get("filename") or item.get("name") or path.name).strip() or path.name
    size = int(item.get("size") or path.stat().st_size)
    content_type = str(item.get("content_type") or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
    return {
        "local_path": str(path),
        "filename": filename,
        "size": size,
        "content_type": content_type,
        "kind": native_wechat_file_kind(path, content_type),
    }


def _normalize_attachments(attachments: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in attachments or []:
        if isinstance(item, dict):
            out.append(_resolve_native_wechat_attachment(item))
    return out


async def _sleep(seconds: float) -> None:
    await __import__("asyncio").sleep(max(0.0, seconds))


def list_peers(
    account_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
    chat_type: str = "",
    keyword: str = "",
) -> Dict[str, Any]:
    init_db()
    params: List[Any] = [account_id]
    where = "where account_id=?"
    if chat_type and chat_type != "unknown":
        where += " and chat_type=?"
        params.append(chat_type)
    keyword = str(keyword or "").strip()
    if keyword:
        where += " and (display_name like ? or peer_id like ? or last_content like ?)"
        like = f"%{keyword}%"
        params.extend([like, like, like])
    with _connect() as conn:
        total = conn.execute(f"select count(*) from wechat_session_state {where}", tuple(params)).fetchone()[0]
        rows = conn.execute(
            f"select * from wechat_session_state {where} order by updated_at desc, id desc limit ? offset ?",
            tuple(params + [int(limit), int(offset)]),
        ).fetchall()
        page_peer_ids = [str(row["peer_id"] or "").strip() for row in rows if str(row["peer_id"] or "").strip()]
        peer_rows = []
        if page_peer_ids:
            placeholders = ",".join("?" for _ in page_peer_ids)
            peer_rows = conn.execute(
                f"""
                select peer_id, last_inbound_at, last_outbound_at
                from wechat_peers
                where account_id=? and peer_id in ({placeholders})
                """,
                tuple([account_id, *page_peer_ids]),
            ).fetchall()
    peer_meta = {str(row["peer_id"]): _row_to_dict(row) for row in peer_rows}
    items = []
    for row in rows:
        item = _row_to_dict(row)
        item["is_new"] = bool(item.get("is_new"))
        item["is_muted"] = bool(item.get("is_muted"))
        meta = peer_meta.get(str(item.get("peer_id") or ""))
        if meta:
            item["last_inbound_at"] = meta.get("last_inbound_at") or ""
            item["last_outbound_at"] = meta.get("last_outbound_at") or ""
        items.append(item)
    items = _enrich_sessions_with_message_counts(account_id, items)
    return {"items": items, "count": int(total), "limit": limit, "offset": offset}


def list_contacts(account_id: str, *, limit: int = 100, offset: int = 0, keyword: str = "") -> Dict[str, Any]:
    init_db()
    params: List[Any] = [account_id]
    where = "where account_id=? and source in ('pc_wechat_uia_contacts','pc_wechat_uia_contact_profile','wx_driver_contacts','local','manual')"
    if keyword:
        where += " and (display_name like ? or remark like ? or wx_no like ? or contact_key like ?)"
        like = f"%{keyword}%"
        params.extend([like, like, like, like])
    with _connect() as conn:
        total = conn.execute(f"select count(*) from wechat_contacts {where}", tuple(params)).fetchone()[0]
        rows = conn.execute(
            f"select * from wechat_contacts {where} order by updated_at desc, id desc limit ? offset ?",
            tuple(params + [int(limit), int(offset)]),
        ).fetchall()
    return {"items": [_row_to_dict(row) for row in rows], "count": int(total), "limit": limit, "offset": offset}


def list_groups(account_id: str, *, limit: int = 100, offset: int = 0, keyword: str = "") -> Dict[str, Any]:
    init_db()
    params: List[Any] = [account_id]
    where = "where account_id=?"
    if keyword:
        where += " and (display_name like ? or remark like ? or group_key like ?)"
        like = f"%{keyword}%"
        params.extend([like, like, like])
    with _connect() as conn:
        total = conn.execute(f"select count(*) from wechat_groups {where}", tuple(params)).fetchone()[0]
        rows = conn.execute(
            f"select * from wechat_groups {where} order by updated_at desc, id desc limit ? offset ?",
            tuple(params + [int(limit), int(offset)]),
        ).fetchall()
    return {"items": [_row_to_dict(row) for row in rows], "count": int(total), "limit": limit, "offset": offset}


def list_group_members(account_id: str, group_key: str, *, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    init_db()
    with _connect() as conn:
        total = conn.execute(
            "select count(*) from wechat_group_members where account_id=? and group_key=?",
            (account_id, group_key),
        ).fetchone()[0]
        rows = conn.execute(
            """
            select * from wechat_group_members
            where account_id=? and group_key=?
            order by updated_at desc, id desc limit ? offset ?
            """,
            (account_id, group_key, int(limit), int(offset)),
        ).fetchall()
    return {"items": [_row_to_dict(row) for row in rows], "count": int(total), "limit": limit, "offset": offset}


def list_messages(account_id: str, peer_id: str, *, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    init_db()
    with _connect() as conn:
        total = conn.execute(
            "select count(*) from wechat_messages where account_id=? and peer_id=?",
            (account_id, peer_id),
        ).fetchone()[0]
        rows = conn.execute(
            """
            select * from wechat_messages
            where account_id=? and peer_id=?
            order by created_at desc, id desc
            limit ? offset ?
            """,
            (account_id, peer_id, int(limit), int(offset)),
        ).fetchall()
    items = [_normalize_message_public(_row_to_dict(row)) for row in rows]
    real_count = sum(1 for item in items if not item.get("is_system"))
    return {"items": items, "count": int(total), "real_message_count": real_count, "limit": limit, "offset": offset}


def fetch_conversation_messages(
    account_id: str,
    peer_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
    sync: bool = False,
    load_more_pages: int = 0,
) -> Dict[str, Any]:
    init_db()
    peer_id = str(peer_id or "").strip()
    if not peer_id:
        raise RuntimeError("缺少会话")
    sync_result: Optional[Dict[str, Any]] = None
    if sync:
        if _is_local_account_id(account_id):
            sync_result = sync_local_messages(account_id, peer_id, load_more_pages=load_more_pages)
            peer_id = str(sync_result.get("peer_id") or peer_id)
        else:
            sync_result = {"ok": False, "message": "非本机账号请先调用 /api/native-wechat/updates/poll 收取新消息"}
    data = list_messages(account_id, peer_id, limit=limit, offset=offset)
    counts = _message_counts_by_peer(account_id, [peer_id]).get(
        peer_id,
        {"message_count": int(data.get("count") or 0), "inbound_message_count": 0, "outbound_message_count": 0},
    )
    peer = None
    with _connect() as conn:
        row = conn.execute(
            "select * from wechat_session_state where account_id=? and peer_id=? limit 1",
            (account_id, peer_id),
        ).fetchone()
        if row:
            peer = _row_to_dict(row)
    return {
        "ok": True,
        "account_id": account_id,
        "peer_id": peer_id,
        "peer": peer,
        "sync_result": sync_result,
        **data,
        **counts,
    }


def list_tasks(account_id: str = "", *, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    init_db()
    params: List[Any] = []
    where = ""
    if account_id:
        where = "where account_id=?"
        params.append(account_id)
    with _connect() as conn:
        total = conn.execute(f"select count(*) from wechat_tasks {where}", tuple(params)).fetchone()[0]
        rows = conn.execute(
            f"select * from wechat_tasks {where} order by created_at desc, id desc limit ? offset ?",
            tuple(params + [int(limit), int(offset)]),
        ).fetchall()
    return {"items": [_row_to_dict(row) for row in rows], "count": int(total), "limit": limit, "offset": offset}


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    init_db()
    with _connect() as conn:
        row = conn.execute("select * from wechat_tasks where id=? limit 1", (task_id,)).fetchone()
    return _row_to_dict(row) if row else None
