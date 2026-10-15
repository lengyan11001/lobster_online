"""个人 Telegram（用户协议 / Telethon）引擎 —— P0 骨架。

分工与 native_whatsapp_engine 一致：
  * 本模块 = 状态机 + 账本 + 限速/白名单 + sqlite
  * scripts/telegram_bridge.py = 真正与 Telegram 说话的那一层（MTProto / Telethon）

P0 只做：配置、状态、依赖探测、协议关键节点日志、登录会话托管、执行记录。
未接入的阶段（P2 只读同步 / P3 发送 / P4 自动化）一律显式返回 phase，
不伪造成功 —— 宁可界面上写着「P2 未接入」，也不能看起来能跑其实没实现。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parents[3]
STATE_DIR = ROOT_DIR / "data" / "native_telegram"
CONFIG_PATH = STATE_DIR / "config.json"
DB_PATH = STATE_DIR / "state.db"
AUTH_DIR = STATE_DIR / "session"
LOG_PATH = ROOT_DIR / "logs" / "native_telegram.jsonl"
PROTOCOL_PATH = ROOT_DIR / "logs" / "telegram_protocol.jsonl"
LEDGER_PATH = STATE_DIR / "protocol_ledger.jsonl"
BRIDGE_PATH = ROOT_DIR / "scripts" / "telegram_bridge.py"
DEFAULT_ACCOUNT_ID = "desktop-telegram-default"

# 限速默认值：直接沿用 WhatsApp 那份（两个平台里最严的一档），Telegram 侧只会更宽松。
SEND_MIN_GAP_SECONDS = 12
SEND_MAX_GAP_SECONDS = 28
SEND_PAUSE_EVERY = 3
SEND_PAUSE_SECONDS = 120
SEND_DAILY_LIMIT = 80
DEFAULT_FRIEND_ADD_INTERVAL_SECONDS = 45
DEFAULT_FRIEND_ADD_DAILY_LIMIT = 30
STALE_RUNNING_TASK_SECONDS = 15 * 60

BRIDGE_TIMEOUT_SECONDS = 90

_PHASE = {"sync": "P2", "send": "P3", "automation": "P4", "friend": "P4"}

_logger = logging.getLogger(__name__)
_db_lock = threading.RLock()
_login_lock = threading.RLock()
_login_proc: Optional[subprocess.Popen] = None
_login_state: Dict[str, Any] = {"step": "idle", "message": "", "error": "", "me": {}, "events": []}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _append_log(event: str, **fields: Any) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"at": _now_iso(), "event": event, **fields}
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception:
        _logger.debug("failed to write native Telegram log", exc_info=True)


def _append_protocol(event: str, **fields: Any) -> None:
    """协议关键节点：单独一份 jsonl，进诊断包白名单，专治「为什么没连上」只看日志就够。"""
    try:
        PROTOCOL_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"at": _now_iso(), "event": event, **fields}
        with PROTOCOL_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception:
        _logger.debug("failed to write native Telegram protocol log", exc_info=True)


def _stable_key(prefix: str, value: str) -> str:
    import hashlib

    normalized = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
    return f"{prefix}_{hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:24]}"


def _record_operation(action: str, status_value: str, target: str = "", message: str = "", raw: Any = None) -> None:
    try:
        with _db_lock, _connect() as conn:
            conn.execute(
                "insert into telegram_operations(id, account_id, action, target, status, message, raw, created_at) "
                "values(?,?,?,?,?,?,?,?)",
                (
                    _stable_key("op", _now_iso() + action + target),
                    DEFAULT_ACCOUNT_ID,
                    str(action)[:64],
                    str(target or "")[:500],
                    str(status_value)[:32],
                    str(message or "")[:2000],
                    json.dumps(raw, ensure_ascii=False, default=str)[:20000] if raw is not None else "",
                    _now_iso(),
                ),
            )
    except Exception:
        _logger.debug("failed to record native Telegram operation", exc_info=True)


def _connect() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma journal_mode=wal")
    conn.execute("pragma busy_timeout=10000")
    conn.executescript(
        """
        create table if not exists telegram_chats (
            id text primary key,
            account_id text not null,
            peer_key text not null,
            peer_type text not null default 'user',
            display_name text,
            username text,
            phone text,
            unread integer not null default 0,
            ts integer not null default 0,
            updated_at text not null,
            unique(account_id, peer_key)
        );
        create index if not exists idx_telegram_chats_ts
        on telegram_chats(account_id, ts desc);

        create table if not exists telegram_messages (
            id text primary key,
            account_id text not null,
            peer_key text not null,
            msg_id text not null,
            from_me integer not null default 0,
            ts integer not null default 0,
            content text,
            media text,
            created_at text not null,
            unique(account_id, msg_id)
        );
        create index if not exists idx_telegram_messages_peer
        on telegram_messages(account_id, peer_key, ts asc);

        create table if not exists telegram_contacts (
            id text primary key,
            account_id text not null,
            contact_key text not null,
            peer_key text not null,
            user_id text,
            display_name text,
            phone text,
            username text,
            source text,
            updated_at text not null,
            unique(account_id, contact_key)
        );
        create index if not exists idx_telegram_contacts_name
        on telegram_contacts(account_id, display_name);

        create table if not exists telegram_operations (
            id text primary key,
            account_id text not null,
            action text not null,
            target text,
            status text not null,
            message text,
            raw text,
            created_at text not null
        );
        create index if not exists idx_telegram_operations_created
        on telegram_operations(account_id, created_at desc);

        create table if not exists telegram_tasks (
            id text primary key,
            account_id text not null,
            task_type text not null,
            status text not null,
            target text,
            payload text,
            processed integer not null default 0,
            success integer not null default 0,
            failed integer not null default 0,
            error_message text,
            client_request_id text,
            created_at text not null,
            updated_at text not null
        );
        create index if not exists idx_telegram_tasks_queue
        on telegram_tasks(account_id, task_type, status, created_at);
        create unique index if not exists uq_telegram_tasks_client_request
        on telegram_tasks(client_request_id) where client_request_id <> '';

        create table if not exists telegram_send_control (
            account_id text primary key,
            enabled integer not null default 0,
            min_gap integer not null default 12,
            max_gap integer not null default 28,
            pause_every integer not null default 3,
            pause_seconds integer not null default 120,
            daily_limit integer not null default 80,
            running integer not null default 0,
            last_started_at text,
            last_stopped_at text,
            updated_at text not null
        );

        create table if not exists telegram_friend_add_control (
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


# ── 配置（api_id / api_hash 属于凭据：只落本地 json，绝不进 git、绝不进 OTA 明文）──
def _default_config() -> Dict[str, Any]:
    return {
        "account_id": DEFAULT_ACCOUNT_ID,
        "api_id": "",
        "api_hash": "",
        "phone": "",
        "proxy": "",
        "session_path": str(AUTH_DIR / "telegram.session"),
        "bridge_path": str(BRIDGE_PATH),
        "interval_seconds": 15,
        "max_unread_per_round": 50,
        "reply_instruction": "",
        "send_whitelist_only": True,
    }


def _read_config() -> Dict[str, Any]:
    cfg = _default_config()
    try:
        if CONFIG_PATH.exists():
            stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                cfg.update(stored)
    except Exception:
        _logger.debug("failed to read native Telegram config", exc_info=True)
    return cfg


def _write_config(cfg: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_config() -> Dict[str, Any]:
    """对外只暴露 api_hash 是否已配置，不回吐 api_hash 明文。"""
    cfg = _read_config()
    public = dict(cfg)
    public["api_hash_set"] = bool(cfg.get("api_hash"))
    public.pop("api_hash", None)
    return public


def save_config(
    *,
    api_id: Optional[str] = None,
    api_hash: Optional[str] = None,
    phone: Optional[str] = None,
    proxy: Optional[str] = None,
    interval_seconds: Optional[int] = None,
    max_unread_per_round: Optional[int] = None,
    reply_instruction: Optional[str] = None,
    send_whitelist_only: Optional[bool] = None,
) -> Dict[str, Any]:
    cfg = _read_config()
    if api_id is not None:
        cfg["api_id"] = re.sub(r"\D", "", str(api_id))[:20]
    if api_hash is not None:
        cfg["api_hash"] = str(api_hash).strip()[:64]
    if phone is not None:
        cfg["phone"] = re.sub(r"[^\d+]", "", str(phone))[:32]
    if proxy is not None:
        cfg["proxy"] = str(proxy).strip()[:200]
    if interval_seconds is not None:
        cfg["interval_seconds"] = max(1, min(int(interval_seconds), 300))
    if max_unread_per_round is not None:
        cfg["max_unread_per_round"] = max(1, min(int(max_unread_per_round), 100))
    if reply_instruction is not None:
        cfg["reply_instruction"] = str(reply_instruction)[:4000]
    if send_whitelist_only is not None:
        cfg["send_whitelist_only"] = bool(send_whitelist_only)
    _write_config(cfg)
    _append_protocol(
        "config_saved",
        api_id_set=bool(cfg.get("api_id")),
        api_hash_set=bool(cfg.get("api_hash")),
        phone_set=bool(cfg.get("phone")),
    )
    return get_config()


# ── 依赖探测 ──────────────────────────────────────────────────────────────
def _module_probe(name: str) -> tuple[bool, str]:
    import importlib.util

    try:
        spec = importlib.util.find_spec(name)
    except Exception:
        spec = None
    return (spec is not None, str(getattr(spec, "origin", "") or ""))


def dependency_report() -> Dict[str, Any]:
    telethon_ok, telethon_source = _module_probe("telethon")
    pyaes_ok, _ = _module_probe("pyaes")
    wheels_dir = ROOT_DIR / "deps" / "wheels"
    return {
        "telethon": telethon_ok,
        "telethon_source": telethon_source,
        "pyaes": pyaes_ok,
        "python": sys.executable,
        "bridge_exists": BRIDGE_PATH.exists(),
        "wheels_dir": str(wheels_dir),
        "wheels_dir_exists": wheels_dir.exists(),
        # 只读同步真正需要的就是这两样：桥接脚本在 + telethon 能 import
        "read_ready": bool(telethon_ok and BRIDGE_PATH.exists()),
        "hint": "" if telethon_ok else "未安装 telethon：P2 只读同步跑不起来（需要在联网环境把 wheel 补进 deps/wheels）",
    }


def _session_path() -> Path:
    raw = str(_read_config().get("session_path") or "").strip()
    return Path(raw) if raw else (AUTH_DIR / "telegram.session")


def _counts() -> Dict[str, int]:
    out = {"chats": 0, "contacts": 0, "messages": 0, "operations": 0}
    try:
        with _db_lock, _connect() as conn:
            for key, table in (
                ("chats", "telegram_chats"),
                ("contacts", "telegram_contacts"),
                ("messages", "telegram_messages"),
                ("operations", "telegram_operations"),
            ):
                row = conn.execute("select count(1) from " + table + " where account_id=?", (DEFAULT_ACCOUNT_ID,)).fetchone()
                out[key] = int(row[0] or 0) if row else 0
    except Exception:
        _logger.debug("failed to count native Telegram rows", exc_info=True)
    return out


def status() -> Dict[str, Any]:
    session_path = _session_path()
    try:
        linked = bool(session_path.exists() and session_path.stat().st_size > 0)
    except Exception:
        linked = False
    cfg = get_config()
    with _login_lock:
        login = {
            "step": str(_login_state.get("step") or "idle"),
            "message": str(_login_state.get("message") or ""),
            "error": str(_login_state.get("error") or ""),
            "running": bool(_login_proc is not None and _login_proc.poll() is None),
        }
        me = dict(_login_state.get("me") or {})
    return {
        "ok": True,
        "phase": "P0",
        "account_id": DEFAULT_ACCOUNT_ID,
        "linked": linked,
        "me": me,
        "login": login,
        "config": {
            "api_id": cfg.get("api_id") or "",
            "api_hash_set": bool(cfg.get("api_hash_set")),
            "phone": cfg.get("phone") or "",
            "proxy_set": bool(cfg.get("proxy")),
            "interval_seconds": cfg.get("interval_seconds"),
            "max_unread_per_round": cfg.get("max_unread_per_round"),
            "send_whitelist_only": bool(cfg.get("send_whitelist_only", True)),
        },
        "dependency": dependency_report(),
        "transport": {
            "bridge": str(BRIDGE_PATH),
            "bridge_exists": BRIDGE_PATH.exists(),
            "python": sys.executable,
            "session_file": str(session_path),
            "session_file_exists": linked,
        },
        "counts": _counts(),
        "limits": {
            "send_min_gap": SEND_MIN_GAP_SECONDS,
            "send_max_gap": SEND_MAX_GAP_SECONDS,
            "send_pause_every": SEND_PAUSE_EVERY,
            "send_pause_seconds": SEND_PAUSE_SECONDS,
            "send_daily_limit": SEND_DAILY_LIMIT,
            "friend_add_interval": DEFAULT_FRIEND_ADD_INTERVAL_SECONDS,
            "friend_add_daily_limit": DEFAULT_FRIEND_ADD_DAILY_LIMIT,
        },
        "logs": {"engine": str(LOG_PATH), "protocol": str(PROTOCOL_PATH), "ledger": str(LEDGER_PATH)},
        "phases": {"P0": "骨架（已完成）", "P1": "登录", "P2": "只读同步", "P3": "发送", "P4": "自动化", "P5": "打包"},
    }


def not_implemented(area: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    phase = _PHASE.get(area, "P1")
    return {
        "ok": False,
        "error": "not_implemented",
        "phase": phase,
        "area": area,
        "message": area + " 属于 " + phase + "，当前版本（P0 骨架）还没接入。",
        **(extra or {}),
    }


# ── 桥接调用 ──────────────────────────────────────────────────────────────
def _child_env() -> Dict[str, str]:
    """桥接子进程必须按 UTF-8 说话：Windows 上默认代码页会把中文日志变成乱码。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _bridge_argv(command: str, extra_args: Optional[List[str]] = None) -> List[str]:
    return [
        sys.executable,
        str(BRIDGE_PATH),
        command,
        "--auth", str(AUTH_DIR),
        "--ledger", str(LEDGER_PATH),
        *list(extra_args or []),
    ]


def _parse_last_json(stdout: str) -> Dict[str, Any]:
    for line in reversed([ln for ln in str(stdout or "").splitlines() if ln.strip()]):
        try:
            data = json.loads(line)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return {}


def run_bridge(command: str, extra_args: Optional[List[str]] = None, timeout: int = BRIDGE_TIMEOUT_SECONDS) -> Dict[str, Any]:
    argv = _bridge_argv(command, extra_args)
    if not BRIDGE_PATH.exists():
        _append_protocol("bridge_missing", command=command, bridge=str(BRIDGE_PATH))
        return {"ok": False, "exit_code": -1, "data": {"ok": False, "error": "bridge_missing", "hint": "找不到 " + str(BRIDGE_PATH)}, "stdout": "", "stderr": ""}
    try:
        proc = subprocess.run(
            argv, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=str(ROOT_DIR), env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        _append_protocol("bridge_timeout", command=command, timeout=timeout)
        return {"ok": False, "exit_code": -2, "data": {"ok": False, "error": "bridge_timeout"}, "stdout": "", "stderr": ""}
    except Exception as exc:
        _append_protocol("bridge_error", command=command, error=str(exc)[:300])
        return {"ok": False, "exit_code": -3, "data": {"ok": False, "error": "bridge_error", "message": str(exc)[:500]}, "stdout": "", "stderr": ""}
    data = _parse_last_json(proc.stdout)
    _append_protocol("bridge_command", command=command, exit_code=proc.returncode, bridge_ok=bool(data.get("ok", proc.returncode == 0)))
    return {
        "ok": proc.returncode == 0 and bool(data.get("ok", True)),
        "exit_code": proc.returncode,
        "data": data,
        "stdout": (proc.stdout or "")[-4000:],
        "stderr": (proc.stderr or "")[-4000:],
    }


# ── 登录会话（常驻子进程 + stdin/stdout 双向）───────────────────────────────
def _login_reader(proc: subprocess.Popen) -> None:
    try:
        for line in proc.stdout or []:
            line = str(line).strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception:
                data = {"event": "raw", "text": line[:500]}
            if not isinstance(data, dict):
                continue
            event = str(data.get("event") or "")
            with _login_lock:
                _login_state["step"] = event or _login_state.get("step")
                _login_state["message"] = str(data.get("message") or "")
                _login_state["error"] = str(data.get("error") or "") if event == "error" else ""
                if isinstance(data.get("me"), dict):
                    _login_state["me"] = data["me"]
                events = _login_state.get("events")
                if isinstance(events, list):
                    events.append(data)
                    del events[:-40]
            _append_protocol("login_event", event=event, message=str(data.get("message") or "")[:300], error=str(data.get("error") or "")[:300])
    except Exception:
        _logger.debug("login reader stopped", exc_info=True)


def login_write(payload: Dict[str, Any]) -> bool:
    with _login_lock:
        proc = _login_proc
    if proc is None or proc.poll() is not None or proc.stdin is None:
        return False
    try:
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        _append_protocol("login_input", cmd=str(payload.get("cmd") or ""))
        return True
    except Exception as exc:
        _append_protocol("login_input_failed", error=str(exc)[:300])
        return False


def login_start(phone: str = "") -> Dict[str, Any]:
    global _login_proc
    cfg = _read_config()
    if not cfg.get("api_id") or not cfg.get("api_hash"):
        _append_protocol("login_blocked", reason="api_id_missing")
        return {
            "ok": False,
            "error": "api_id_missing",
            "message": "还没配置 api_id / api_hash，先在「接管设置」里填上（my.telegram.org/apps 自助获取）。",
        }
    with _login_lock:
        if _login_proc is None or _login_proc.poll() is not None:
            if not BRIDGE_PATH.exists():
                return {"ok": False, "error": "bridge_missing", "message": "找不到 " + str(BRIDGE_PATH)}
            argv = _bridge_argv(
                "login",
                [
                    "--api-id", str(cfg.get("api_id") or ""),
                    "--api-hash", str(cfg.get("api_hash") or ""),
                    "--phone", str(phone or cfg.get("phone") or ""),
                ],
            )
            if cfg.get("proxy"):
                argv += ["--proxy", str(cfg.get("proxy"))]
            try:
                _login_proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    cwd=str(ROOT_DIR),
                    env=_child_env(),
                )
            except Exception as exc:
                _append_protocol("login_spawn_failed", error=str(exc)[:300])
                return {"ok": False, "error": "login_spawn_failed", "message": str(exc)[:500]}
            with _login_lock:
                _login_state.update({"step": "starting", "message": "", "error": "", "me": {}, "events": []})
            threading.Thread(target=_login_reader, args=(_login_proc,), daemon=True, name="lobster-telegram-login").start()
            _append_protocol("login_spawned", pid=int(getattr(_login_proc, "pid", 0) or 0))
        elif phone:
            login_write({"cmd": "phone", "value": phone})
    return login_status()


def login_input(kind: str, value: str) -> Dict[str, Any]:
    cmd = str(kind or "").strip().lower()
    if cmd not in {"phone", "code", "password"}:
        return {"ok": False, "error": "bad_input_kind", "message": "只支持 phone / code / password"}
    if not login_write({"cmd": cmd, "value": str(value or "")}):
        return {"ok": False, "error": "login_not_running", "message": "登录会话没在跑，先点「开始登录」。"}
    return login_status()


def login_status() -> Dict[str, Any]:
    with _login_lock:
        proc = _login_proc
        state = {
            "step": str(_login_state.get("step") or "idle"),
            "message": str(_login_state.get("message") or ""),
            "error": str(_login_state.get("error") or ""),
            "me": dict(_login_state.get("me") or {}),
            "events": list(_login_state.get("events") or [])[-10:],
            "running": bool(proc is not None and proc.poll() is None),
        }
    return {"ok": True, **state, "linked": bool(_session_path().exists())}


def login_cancel() -> Dict[str, Any]:
    global _login_proc
    with _login_lock:
        proc = _login_proc
        _login_proc = None
    if proc is not None and proc.poll() is None:
        try:
            if proc.stdin:
                try:
                    proc.stdin.write(json.dumps({"cmd": "cancel"}) + "\n")
                    proc.stdin.flush()
                except Exception:
                    pass
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        except Exception:
            _logger.debug("failed to stop Telegram login process", exc_info=True)
    with _login_lock:
        _login_state.update({"step": "idle", "message": "", "error": "", "me": {}, "events": []})
    _append_protocol("login_cancelled")
    return login_status()


def logout() -> Dict[str, Any]:
    result = run_bridge("logout")
    data = dict(result.get("data") or {})
    if result.get("ok") and data.get("ok"):
        session_path = _session_path()
        try:
            if session_path.exists():
                session_path.unlink()
        except Exception:
            _logger.debug("failed to remove Telegram session file", exc_info=True)
        with _login_lock:
            _login_state.update({"step": "idle", "message": "", "error": "", "me": {}, "events": []})
        _record_operation("logout", "ok", message="已解除绑定")
        return {"ok": True, "message": "已解除绑定", "bridge": data}
    _record_operation("logout", "failed", message=str(data.get("message") or ""))
    return {"ok": False, "message": str(data.get("message") or "解除绑定没成功"), "bridge": data}


# ── 读库（P0 表是空的，P2 才会写进来；接口先立起来，界面不用等）──────────────
def _page(table: str, *, limit: int, offset: int, keyword: str = "", keyword_column: str = "") -> Dict[str, Any]:
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    where = "account_id=?"
    params: List[Any] = [DEFAULT_ACCOUNT_ID]
    if keyword and keyword_column:
        where += " and " + keyword_column + " like ?"
        params.append("%" + keyword + "%")
    with _db_lock, _connect() as conn:
        total = int(conn.execute("select count(1) from " + table + " where " + where, tuple(params)).fetchone()[0] or 0)
        rows = conn.execute(
            "select * from " + table + " where " + where + " order by rowid desc limit ? offset ?",
            tuple(params) + (limit, offset),
        ).fetchall()
    return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def list_chats(*, limit: int = 50, offset: int = 0, keyword: str = "") -> Dict[str, Any]:
    return _page("telegram_chats", limit=limit, offset=offset, keyword=keyword, keyword_column="display_name")


def list_contacts(*, limit: int = 50, offset: int = 0, keyword: str = "") -> Dict[str, Any]:
    return _page("telegram_contacts", limit=limit, offset=offset, keyword=keyword, keyword_column="display_name")


def list_messages(peer_key: str, *, limit: int = 50) -> Dict[str, Any]:
    peer = str(peer_key or "").strip()
    if not peer:
        return {"items": [], "total": 0, "limit": limit, "offset": 0}
    limit = max(1, min(int(limit or 50), 200))
    with _db_lock, _connect() as conn:
        rows = conn.execute(
            "select * from telegram_messages where account_id=? and peer_key=? order by ts asc limit ?",
            (DEFAULT_ACCOUNT_ID, peer, limit),
        ).fetchall()
    return {"items": [dict(row) for row in rows], "total": len(rows), "limit": limit, "offset": 0}


def list_operations(*, limit: int = 50, offset: int = 0, keyword: str = "") -> Dict[str, Any]:
    return _page("telegram_operations", limit=limit, offset=offset, keyword=keyword, keyword_column="action")


def protocol_log(*, limit: int = 80) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 80), 500))
    items: List[Dict[str, Any]] = []
    try:
        if PROTOCOL_PATH.exists():
            with PROTOCOL_PATH.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()[-limit:]
            for line in lines:
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                if isinstance(data, dict):
                    items.append(data)
    except Exception:
        _logger.debug("failed to read Telegram protocol log", exc_info=True)
    return {"ok": True, "items": items, "path": str(PROTOCOL_PATH), "count": len(items)}


# ── 运行依赖体检 / 修复 ────────────────────────────────────────────────
# 形状照视频复刻（hypit_local）那套：一条条列依赖 + 一个「安装 / 修复运行依赖」按钮，
# 修复走客户端已有的 runtime_dependency_repair（deps/wheels 离线优先，缺了才联网）。
_REPAIR_STATE_NAME = "dependency_repair.json"
_REPAIR_LOG_LINES = 60
_repair_lock = threading.Lock()
_repair_thread: Optional[threading.Thread] = None


def _repair_state_path() -> Path:
    return STATE_DIR / _REPAIR_STATE_NAME


def _read_repair_state() -> Dict[str, Any]:
    defaults: Dict[str, Any] = {"status": "idle", "stage": "", "percent": 0, "error": "", "log": [], "source": "", "updated_at": ""}
    try:
        path = _repair_state_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update(data)
    except Exception:
        _logger.debug("failed to read Telegram repair state", exc_info=True)
    return defaults


def _write_repair_state(**fields: Any) -> Dict[str, Any]:
    state = _read_repair_state()
    state.update(fields)
    state["updated_at"] = _now_iso()
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _repair_state_path().write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        _logger.debug("failed to write Telegram repair state", exc_info=True)
    return state


def _wheel_hint(prefix: str) -> str:
    wheels = ROOT_DIR / "deps" / "wheels"
    try:
        if wheels.is_dir():
            hits = sorted(item.name for item in wheels.glob(prefix + "-*.whl"))
            if hits:
                return "本机未安装；离线包已备：" + hits[-1]
            return "本机未安装，deps/wheels 里也没有 " + prefix + " 的离线包"
    except Exception:
        _logger.debug("failed to scan wheel dir", exc_info=True)
    return "本机未安装"


def runtime_dependencies() -> List[Dict[str, Any]]:
    """依赖体检明细：界面上一条条显示，缺哪条一目了然。"""
    telethon_ok, telethon_source = _module_probe("telethon")
    pyaes_ok, pyaes_source = _module_probe("pyaes")
    return [
        {
            "key": "telethon",
            "label": "Telethon（MTProto 客户端）",
            "ok": telethon_ok,
            "detail": telethon_source or _wheel_hint("telethon"),
            "required_for": "P2 只读同步",
        },
        {
            "key": "pyaes",
            "label": "pyaes（MTProto 加密）",
            "ok": pyaes_ok,
            "detail": pyaes_source or _wheel_hint("pyaes"),
            "required_for": "P2 只读同步",
        },
        {
            "key": "bridge",
            "label": "桥接脚本 telegram_bridge.py",
            "ok": BRIDGE_PATH.exists(),
            "detail": str(BRIDGE_PATH),
            "required_for": "P1 登录",
        },
    ]


def runtime_status() -> Dict[str, Any]:
    dependencies = runtime_dependencies()
    running = bool(_repair_thread is not None and _repair_thread.is_alive())
    install = {**_read_repair_state(), "running": running}
    ready = all(bool(item.get("ok")) for item in dependencies)
    if ready and not running and str(install.get("status") or "") == "failed":
        # 依赖其实都装好了（上一次可能是网络/超时）：把旧的失败态自愈掉，别一直挂红
        install = {**_write_repair_state(status="completed", stage="依赖已就绪", percent=100, error=""), "running": False}
    return {
        "ok": True,
        "ready": ready,
        "dependencies": dependencies,
        "install": install,
        "python": sys.executable,
        "wheels_dir": str(ROOT_DIR / "deps" / "wheels"),
    }


def _repair_worker() -> None:
    _write_repair_state(status="running", stage="正在安装 Telegram 运行依赖（本地离线包优先）", percent=10, error="", log=[])
    try:
        from .runtime_dependency_repair import repair_runtime_dependencies

        result = repair_runtime_dependencies() or {}
        install = result.get("install") if isinstance(result.get("install"), dict) else {}
        checks = result.get("checks") if isinstance(result.get("checks"), list) else []
        # 日志按通用修复服务的真实字段拼：pip 的原文 + 每一项体检结论
        log: List[str] = []
        for line in str(install.get("log") or "").splitlines():
            if line.strip():
                log.append(line.rstrip())
        for item in checks:
            if not isinstance(item, dict):
                continue
            log.append(("[就绪] " if item.get("ok") else "[缺失] ") + str(item.get("label") or item.get("key") or ""))
            for failure in item.get("failures") or []:
                if isinstance(failure, dict):
                    log.append("    " + str(failure.get("module") or "") + ": " + str(failure.get("error") or "")[:200])
        ok = bool(result.get("ok"))
        _write_repair_state(
            status="completed" if ok else "failed",
            stage="依赖已就绪" if ok else "部分依赖仍不可用",
            percent=100,
            source=str(install.get("source") or ""),
            error="" if ok else str(result.get("message") or install.get("message") or "")[:600],
            log=[line for line in log if line.strip()][-_REPAIR_LOG_LINES:],
        )
        _append_protocol("dependency_repair", ok=ok, source=str(install.get("source") or ""), seconds=result.get("duration_seconds"))
    except Exception as exc:
        message = str(exc)[:600]
        _write_repair_state(status="failed", stage="依赖安装失败", percent=100, error=message)
        _append_protocol("dependency_repair_failed", error=message[:300])




def repair_dependencies() -> Dict[str, Any]:
    """幂等：已经在装就直接返回进度；真正的安装丢后台线程，接口马上返回。"""
    global _repair_thread
    with _repair_lock:
        running = bool(_repair_thread is not None and _repair_thread.is_alive())
        if not running:
            _write_repair_state(status="running", stage="正在启动修复", percent=1, error="", log=[])
            _repair_thread = threading.Thread(target=_repair_worker, daemon=True, name="lobster-telegram-repair")
            _repair_thread.start()
            _append_protocol("dependency_repair_started")
    status = runtime_status()
    status["started"] = not running
    return status