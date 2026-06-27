from __future__ import annotations

import asyncio
import base64
import contextvars
import json
import os
import random
import re
import sys
import time
import traceback
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from ai_client import AIClient
from console_safe import safe_print
from douyin_account_nurture import DouyinAccountNurtureScheduler
from douyin_client import DouyinClient, is_port_open
from douyin_comment_scraper import DouyinCommentScraper, DouyinMentionCommentStopped, extract_aweme_id
from runtime_paths import resolve_install_dir, resolve_runtime_root
from state_store import RuntimeStateStore


router = APIRouter(prefix="/api/douyin", tags=["douyin"])

_douyin_ai_auth_token_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "douyin_ai_auth_token",
    default="",
)


def _write_excel_sheets(filepath: Path, sheets: List[tuple[str, List[Dict]]]) -> None:
    try:
        import pandas as pd  # type: ignore
    except Exception as exc:
        raise RuntimeError("Excel export requires pandas/openpyxl in the client runtime") from exc

    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        for sheet_name, rows in sheets:
            pd.DataFrame(rows).to_excel(writer, sheet_name=sheet_name, index=False)


BASE_DIR = resolve_runtime_root(resolve_install_dir())
ROOT_DATA_DIR = BASE_DIR / "data"
ROOT_DATA_DIR.mkdir(exist_ok=True)
DOUYIN_DATA_DIR = ROOT_DATA_DIR / "douyin"
DOUYIN_DATA_DIR.mkdir(exist_ok=True)
DOUYIN_DAILY_DIR = DOUYIN_DATA_DIR / "daily"
DOUYIN_DAILY_DIR.mkdir(exist_ok=True)
DOUYIN_COMMENT_IMAGE_DIR = DOUYIN_DATA_DIR / "comment_images"
DOUYIN_COMMENT_IMAGE_DIR.mkdir(exist_ok=True)
GLOBAL_CONFIG_FILE = ROOT_DATA_DIR / "config.json"
CUSTOM_CONFIGS_FILE = BASE_DIR / "custom_configs.json"
SEARCH_HISTORY_FILE = DOUYIN_DATA_DIR / "search_history.json"
LATEST_RESULTS_FILE = DOUYIN_DATA_DIR / "results.xlsx"
STATE_DB_FILE = ROOT_DATA_DIR / "app_state.db"

DOUYIN_ACCOUNT_LIMIT = 3
DOUYIN_ACCOUNT_LOGIN_CHECK_TIMEOUT_SECONDS = 25
DOUYIN_ACCOUNT_VIEW_NAVIGATION_TIMEOUT_MS = 15000

DEFAULT_DOUYIN_ACCOUNTS = [
    {"id": account_id, "status": "offline", "port": 9331 + account_id}
    for account_id in range(1, DOUYIN_ACCOUNT_LIMIT + 1)
]

DOUYIN_MENTION_COMMENT_MAX_USERS_PER_COMMENT = 50
DOUYIN_MENTION_COMMENT_SAFE_TEXT_LIMIT = 180
DOUYIN_SCHEDULE_PLANS_BLOB_KEY = "douyin_schedule_plans_v1"
DOUYIN_SCHEDULE_TYPES = {"collect_precise", "follow_comment", "interaction"}
DOUYIN_GROUP_NAME_PATTERN = re.compile(r"群|群聊|交流群|社群|分群")
DOUYIN_GROUP_PREVIEW_PATTERN = re.compile(
    r"加入了群聊|通过.+加入了群聊|新成员可查看历史消息|群聊已满员|查看和管理\s*群成员|本群|置顶公告"
)
DOUYIN_GROUP_SENDER_PREFIX_PATTERN = re.compile(r"^[^：:\n]{1,24}[：:]")


douyin_search_cache: Dict[str, List[Dict]] = {"latest": []}

DOUYIN_SEARCH_MODES = {"api", "script"}
douyin_tasks: List[Dict] = []
douyin_all_customer_pool: List[Dict] = []
douyin_precise_customer_pool: List[Dict] = []
douyin_running = False
douyin_stop_requested = False
douyin_background_task: Optional[asyncio.Task] = None
douyin_video_comment_running = False
douyin_video_comment_stop_requested = False
douyin_video_comment_background_task: Optional[asyncio.Task] = None
douyin_mention_comment_running = False
douyin_mention_comment_stop_requested = False
douyin_mention_comment_background_task: Optional[asyncio.Task] = None
douyin_follow_comment_running = False
douyin_follow_comment_stop_requested = False
douyin_follow_comment_background_task: Optional[asyncio.Task] = None
douyin_interaction_running = False
douyin_interaction_stop_requested = False
douyin_interaction_background_task: Optional[asyncio.Task] = None
douyin_stranger_message_running = False
douyin_stranger_message_stop_requested = False
douyin_stranger_message_background_task: Optional[asyncio.Task] = None
douyin_stranger_message_monitor_task: Optional[asyncio.Task] = None
douyin_stranger_message_monitor_started = False
douyin_group_member_running = False
douyin_group_member_stop_requested = False
douyin_group_member_background_task: Optional[asyncio.Task] = None
douyin_state_store = RuntimeStateStore(STATE_DB_FILE)
douyin_logs = deque(maxlen=500)
douyin_log_counter = 0


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


douyin_interaction_state: Dict[str, object] = {
    "running": False,
    "message": "",
    "total": 0,
    "processed": 0,
    "success": 0,
    "failed": 0,
    "interval_seconds": 180,
    "interval_seconds_min": 180,
    "interval_seconds_max": 300,
    "account_id": None,
    "current_user": "",
    "current_users": [],
    "started_at": "",
    "finished_at": "",
    "last_error": "",
    "message_mode": "fixed",
    "message_summary": "",
    "current_message_text": "",
    "last_message_text": "",
    "account_ids": [],
    "workers": [],
}
douyin_stranger_message_results: List[Dict] = []
douyin_stranger_message_seen_records: Dict[str, Dict[str, str]] = {}
douyin_stranger_message_state: Dict[str, object] = {
    "running": False,
    "phase": "",
    "message": "",
    "total": 0,
    "processed": 0,
    "success": 0,
    "failed": 0,
    "account_id": None,
    "current_user": "",
    "current_message_text": "",
    "started_at": "",
    "finished_at": "",
    "last_error": "",
    "last_message_text": "",
}
douyin_stranger_message_monitor_states: Dict[str, Dict[str, object]] = {}
douyin_stranger_message_monitor_tasks_by_account: Dict[int, asyncio.Task] = {}
douyin_inbox_running = False
douyin_inbox_stop_requested = False
douyin_inbox_background_task: Optional[asyncio.Task] = None
douyin_inbox_results: List[Dict] = []
douyin_inbox_state: Dict[str, object] = {
    "running": False,
    "phase": "",
    "message": "",
    "total": 0,
    "processed": 0,
    "success": 0,
    "failed": 0,
    "account_id": None,
    "current_user": "",
    "started_at": "",
    "finished_at": "",
    "last_error": "",
    "last_message_text": "",
}
douyin_inbox_monitor_states: Dict[str, Dict[str, object]] = {}
douyin_inbox_monitor_tasks_by_account: Dict[int, asyncio.Task] = {}
douyin_inbox_page_workers: Dict[int, "DouyinInboxPageWorker"] = {}
douyin_self_comment_monitor_results: List[Dict] = []
douyin_self_comment_monitor_states: Dict[str, Dict[str, object]] = {}
douyin_self_comment_monitor_tasks_by_account: Dict[int, asyncio.Task] = {}
douyin_video_comment_state: Dict[str, object] = {
    "running": False,
    "message": "",
    "total": 0,
    "processed": 0,
    "success": 0,
    "failed": 0,
    "interval_seconds": 300,
    "interval_seconds_min": 240,
    "interval_seconds_max": 360,
    "account_id": None,
    "current_task_title": "",
    "comment_mode": "fixed",
    "comment_summary": "",
    "started_at": "",
    "finished_at": "",
    "last_error": "",
    "current_comment_text": "",
    "last_comment_text": "",
    "last_task_title": "",
    "next_interval_seconds": 0,
}
douyin_mention_self_video_cache: Dict[str, object] = {
    "account_id": 0,
    "profile": {},
    "videos": [],
    "fetched_at": "",
    "selected_video_url": "",
}
douyin_mention_comment_history: Dict[str, Dict[str, object]] = {}
douyin_mention_comment_state: Dict[str, object] = {
    "running": False,
    "message": "",
    "total": 0,
    "processed": 0,
    "success": 0,
    "failed": 0,
    "selected_total": 0,
    "truncated": 0,
    "account_id": None,
    "video_url": "",
    "video_title": "",
    "video_cover_image": "",
    "current_user": "",
    "current_users": [],
    "started_at": "",
    "finished_at": "",
    "last_error": "",
    "comment_preview": "",
}
douyin_follow_comment_state: Dict[str, object] = {
    "running": False,
    "message": "",
    "total": 0,
    "processed": 0,
    "success": 0,
    "failed": 0,
    "commented": 0,
    "skipped_no_posts": 0,
    "interval_seconds": 180,
    "interval_seconds_min": 240,
    "interval_seconds_max": 360,
    "account_id": None,
    "comment_mode": "fixed",
    "comment_summary": "",
    "current_user": "",
    "current_users": [],
    "started_at": "",
    "finished_at": "",
    "last_error": "",
    "current_comment_text": "",
    "last_comment_text": "",
    "account_ids": [],
    "workers": [],
}
douyin_group_member_results: List[Dict] = []
douyin_manual_interaction_users: List[Dict] = []
douyin_group_member_state: Dict[str, object] = {
    "running": False,
    "message": "",
    "total_groups": 0,
    "processed_groups": 0,
    "total_members": 0,
    "account_id": None,
    "group_keyword": "",
    "selected_groups": [],
    "available_groups": [],
    "current_group": "",
    "started_at": "",
    "finished_at": "",
    "last_error": "",
}
DOUYIN_SEARCH_SESSIONS_BLOB_KEY = "douyin_search_sessions"
DOUYIN_MENTION_SELF_VIDEO_CACHE_BLOB_KEY = "douyin_mention_self_video_cache"
DOUYIN_MENTION_COMMENT_HISTORY_BLOB_KEY = "douyin_mention_comment_history"
DOUYIN_STRANGER_MESSAGE_RESULTS_BLOB_KEY = "douyin_stranger_messages"
DOUYIN_STRANGER_MESSAGE_MONITOR_CONFIG_BLOB_KEY = "douyin_stranger_message_monitor_config"
DOUYIN_STRANGER_MESSAGE_SEEN_RECORDS_BLOB_KEY = "douyin_stranger_message_seen_records"
DOUYIN_STRANGER_MESSAGE_SEEN_LIMIT_PER_ACCOUNT = 5000
DOUYIN_INBOX_RESULTS_BLOB_KEY = "douyin_inbox_messages"
DOUYIN_INBOX_MONITOR_CONFIG_BLOB_KEY = "douyin_inbox_monitor_config"
DOUYIN_SELF_COMMENT_MONITOR_RESULTS_BLOB_KEY = "douyin_self_comment_monitor_results"
DOUYIN_SELF_COMMENT_MONITOR_CONFIG_BLOB_KEY = "douyin_self_comment_monitor_config"
DOUYIN_INTENT_DIRECTION_DEFAULT = (
    "请筛选评论中是否属于精准客户。这里的精准客户指：有真实需求、了解意愿、"
    "咨询意愿、联系意愿的人。优先保留想了解、想咨询、感兴趣、想试试、想做、"
    "想进一步沟通，以及询问价格、费用、怎么买、怎么报名、怎么合作、怎么联系、"
    "适合我吗、新手能做吗、怎么开始这类评论。排除纯夸赞、纯围观、纯玩笑、"
    "无明确需求、重复内容。只判断是否精准，不做分层。"
)
DOUYIN_LEGACY_COMMENT_DIRECTION_VALUES = {"亲切、有趣、鼓励"}
DOUYIN_COMMENT_FILTER_STRATEGIES = {"prompt", "reverse"}
DOUYIN_MONITOR_SLOTS = ((0, 0, "00:00"), (18, 30, "18:30"))
douyin_monitor_scheduler_task: Optional[asyncio.Task] = None
douyin_monitor_scheduler_started = False
douyin_schedule_scheduler_task: Optional[asyncio.Task] = None
douyin_schedule_scheduler_started = False
douyin_monitor_runtime_state: Dict[str, object] = {
    "running": False,
    "message": "",
    "last_run_at": "",
    "next_run_at": "",
    "last_slot_key": "",
    "last_error": "",
}
douyin_schedule_plans: List[Dict[str, object]] = []
douyin_schedule_runtime_state: Dict[str, object] = {
    "running": False,
    "message": "",
    "last_tick_at": "",
    "last_run_at": "",
    "next_run_at": "",
    "active_plan_id": "",
    "active_plan_name": "",
    "active_phase": "",
    "busy_reason": "",
    "last_error": "",
}
douyin_account_nurture_scheduler: Optional[DouyinAccountNurtureScheduler] = None
douyin_account_nurture_background_task: Optional[asyncio.Task] = None
DOUYIN_SHARE_URL_PATTERN = re.compile(r"https?://[^\s<>'\"，。；！？、]+", flags=re.I)


def douyin_log(message: str, level: str = "info"):
    global douyin_log_counter
    douyin_log_counter += 1
    text = str(message or "")
    level_text = str(level or "info")
    douyin_logs.append(
        {
            "id": douyin_log_counter,
            "time": datetime.now().strftime("%H:%M:%S"),
            "message": text,
            "level": level_text,
        }
    )
    console_text = f"[douyin][{level_text}] {text}"
    try:
        safe_print(console_text, end="")
        return
    except UnicodeEncodeError:
        pass
    except Exception:
        return

    try:
        stdout = getattr(sys, "stdout", None)
        if stdout is None:
            return
        encoding = getattr(stdout, "encoding", None) or "utf-8"
        payload = (console_text + "\n").encode(encoding, errors="replace")
        buffer = getattr(stdout, "buffer", None)
        if buffer is not None:
            buffer.write(payload)
            buffer.flush()
            return
        stdout.write(payload.decode(encoding, errors="replace"))
        stdout.flush()
    except Exception:
        return


DOUYIN_FILTER_LOG_DIR = BASE_DIR / "logs"


def _douyin_filter_log_path() -> Path:
    try:
        DOUYIN_FILTER_LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return DOUYIN_FILTER_LOG_DIR / f"douyin_filter_{datetime.now().strftime('%Y%m%d')}.log"


def _douyin_filter_preview(value: object, limit: int = 240) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ⏎ ").strip()
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


_DOUYIN_FILTER_LONG_FIELD_LIMITS = {
    "prompt": 400,
    "system_prompt": 8000,
    "user_prompt": 20000,
    "raw_response": 12000,
    "direction": 400,
    "custom_prompt": 400,
    "title": 200,
    "error": 600,
}


def log_douyin_filter_event(event: str, **fields) -> None:
    """记录抖音精准客户筛选过程，写到 RUNTIME/logs/douyin_filter_YYYYMMDD.log，每行一条 JSON。"""
    record = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "event": str(event or "")}
    for key, value in fields.items():
        if key in _DOUYIN_FILTER_LONG_FIELD_LIMITS and value is not None:
            record[key] = _douyin_filter_preview(value, _DOUYIN_FILTER_LONG_FIELD_LIMITS[key])
        else:
            record[key] = value
    try:
        with _douyin_filter_log_path().open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        douyin_log(f"[抖音筛选日志] 写入失败: {exc}", "warning")

    summary_keys = ("scope", "title", "comments_in", "precise_out", "fallback_used", "strategy", "duration_ms")
    summary = " | ".join(f"{k}={record[k]}" for k in summary_keys if k in record)
    level = "error" if event == "error" else ("warning" if record.get("fallback_used") else "info")
    douyin_log(f"[抖音筛选] {event} {summary}".strip(), level)


def get_douyin_comment_direction(config: Dict) -> str:
    value = str(config.get("douyin_comment_direction", "") or "").strip()
    if not value or value in DOUYIN_LEGACY_COMMENT_DIRECTION_VALUES:
        legacy_value = str(config.get("comment_direction", "") or "").strip()
        if legacy_value and legacy_value not in DOUYIN_LEGACY_COMMENT_DIRECTION_VALUES:
            return legacy_value
        return DOUYIN_INTENT_DIRECTION_DEFAULT
    return value


def normalize_douyin_comment_filter_strategy(value: object) -> str:
    normalized = str(value or "prompt").strip().lower()
    return normalized if normalized in DOUYIN_COMMENT_FILTER_STRATEGIES else "prompt"


def get_douyin_comment_filter_strategy(config: Dict) -> str:
    return normalize_douyin_comment_filter_strategy(config.get("douyin_comment_filter_strategy", "prompt"))


def build_default_douyin_stranger_message_monitor_state(account_id: int = 0) -> Dict[str, object]:
    return {
        "enabled": False,
        "running": False,
        "interval_minutes": 30,
        "account_id": int(account_id or 0) or None,
        "max_conversations": 100,
        "auto_reply_enabled": True,
        "reply_mode": "fixed",
        "reply_message": "",
        "reply_prompt": "",
        "contact_value": "",
        "message": "陌生人消息监控未开启。",
        "last_run_at": "",
        "next_run_at": "",
        "last_error": "",
        "last_new_count": 0,
        "last_total_count": 0,
        "last_auto_reply_total": 0,
        "last_auto_reply_success": 0,
        "last_auto_reply_failed": 0,
        "last_skip_reason": "",
        "last_cycle_status": "idle",
        "seen_message_count": 0,
    }


def normalize_douyin_stranger_message_monitor_state(
    state: Optional[Dict],
    *,
    account_id: int = 0,
) -> Dict[str, object]:
    base = build_default_douyin_stranger_message_monitor_state(account_id)
    payload = state if isinstance(state, dict) else {}
    base.update(
        {
            "enabled": bool(payload.get("enabled", base["enabled"])),
            "running": bool(payload.get("running", base["running"])),
            "interval_minutes": max(1, min(int(payload.get("interval_minutes", base["interval_minutes"]) or 30), 1440)),
            "account_id": int(payload.get("account_id", account_id) or account_id or 0) or None,
            "max_conversations": max(1, min(int(payload.get("max_conversations", base["max_conversations"]) or 100), 100)),
            "auto_reply_enabled": bool(payload.get("auto_reply_enabled", base["auto_reply_enabled"])),
            "reply_mode": normalize_douyin_stranger_reply_mode(payload.get("reply_mode", base["reply_mode"])),
            "reply_message": str(payload.get("reply_message", base["reply_message"]) or "").strip(),
            "reply_prompt": str(payload.get("reply_prompt", base["reply_prompt"]) or "").strip(),
            "contact_value": str(payload.get("contact_value", base["contact_value"]) or "").strip(),
            "message": normalize_douyin_text(payload.get("message", base["message"])),
            "last_run_at": normalize_douyin_text(payload.get("last_run_at", base["last_run_at"])),
            "next_run_at": normalize_douyin_text(payload.get("next_run_at", base["next_run_at"])),
            "last_error": normalize_douyin_text(payload.get("last_error", base["last_error"])),
            "last_new_count": max(0, int(payload.get("last_new_count", base["last_new_count"]) or 0)),
            "last_total_count": max(0, int(payload.get("last_total_count", base["last_total_count"]) or 0)),
            "last_auto_reply_total": max(0, int(payload.get("last_auto_reply_total", base["last_auto_reply_total"]) or 0)),
            "last_auto_reply_success": max(0, int(payload.get("last_auto_reply_success", base["last_auto_reply_success"]) or 0)),
            "last_auto_reply_failed": max(0, int(payload.get("last_auto_reply_failed", base["last_auto_reply_failed"]) or 0)),
            "last_skip_reason": normalize_douyin_text(payload.get("last_skip_reason", base["last_skip_reason"])),
            "last_cycle_status": normalize_douyin_text(payload.get("last_cycle_status", base["last_cycle_status"])) or "idle",
            "seen_message_count": max(0, int(payload.get("seen_message_count", base["seen_message_count"]) or 0)),
        }
    )
    return base


def get_douyin_stranger_message_monitor_state(account_id: int, create: bool = False) -> Dict[str, object]:
    key = str(int(account_id or 0) or 0)
    if key == "0":
        return build_default_douyin_stranger_message_monitor_state()
    existing = douyin_stranger_message_monitor_states.get(key)
    if existing is None and create:
        existing = normalize_douyin_stranger_message_monitor_state({}, account_id=int(key))
        douyin_stranger_message_monitor_states[key] = existing
    return existing or build_default_douyin_stranger_message_monitor_state(int(key))


def list_douyin_stranger_message_monitor_states() -> List[Dict[str, object]]:
    states: List[Dict[str, object]] = []
    for key, raw_state in douyin_stranger_message_monitor_states.items():
        account_id = int(key or 0) or 0
        if account_id <= 0:
            continue
        states.append(normalize_douyin_stranger_message_monitor_state(raw_state, account_id=account_id))
    states.sort(key=lambda item: int(item.get("account_id", 0) or 0))
    return states


def save_douyin_tasks_state():
    try:
        douyin_state_store.save_blob_json("douyin_tasks", douyin_tasks or [])
        all_rows, precise_rows = build_douyin_customer_pools_from_tasks(douyin_tasks)
        douyin_state_store.save_douyin_customer_pools(all_rows, precise_rows)
        combined_all_rows, combined_precise_rows = build_combined_douyin_customer_pools()
        sync_douyin_customer_pool_cache(combined_all_rows, combined_precise_rows)
    except Exception:
        pass


def save_douyin_group_member_results():
    try:
        douyin_state_store.save_blob_json("douyin_group_members", douyin_group_member_results or [])
    except Exception:
        pass


def save_douyin_manual_interaction_users():
    try:
        douyin_state_store.save_blob_json("douyin_manual_interaction_users", douyin_manual_interaction_users or [])
    except Exception:
        pass


def save_douyin_stranger_message_results():
    try:
        douyin_state_store.save_blob_json(
            DOUYIN_STRANGER_MESSAGE_RESULTS_BLOB_KEY,
            douyin_stranger_message_results or [],
        )
    except Exception:
        pass


def save_douyin_inbox_results():
    try:
        douyin_state_store.save_blob_json(
            DOUYIN_INBOX_RESULTS_BLOB_KEY,
            douyin_inbox_results or [],
        )
    except Exception:
        pass


def save_douyin_self_comment_monitor_results():
    try:
        douyin_state_store.save_blob_json(
            DOUYIN_SELF_COMMENT_MONITOR_RESULTS_BLOB_KEY,
            douyin_self_comment_monitor_results or [],
        )
    except Exception:
        pass


def save_douyin_stranger_message_monitor_config():
    try:
        payload = []
        for state in list_douyin_stranger_message_monitor_states():
            payload.append(
                {
                    "enabled": bool(state.get("enabled")),
                    "interval_minutes": max(1, min(int(state.get("interval_minutes", 30) or 30), 1440)),
                    "account_id": int(state.get("account_id", 0) or 0),
                    "max_conversations": max(1, min(int(state.get("max_conversations", 100) or 100), 100)),
                    "auto_reply_enabled": bool(state.get("auto_reply_enabled", True)),
                    "reply_mode": normalize_douyin_stranger_reply_mode(state.get("reply_mode", "fixed")),
                    "reply_message": str(state.get("reply_message", "") or "").strip(),
                    "reply_prompt": str(state.get("reply_prompt", "") or "").strip(),
                    "contact_value": str(state.get("contact_value", "") or "").strip(),
                }
            )
        douyin_state_store.save_blob_json(
            DOUYIN_STRANGER_MESSAGE_MONITOR_CONFIG_BLOB_KEY,
            payload,
        )
    except Exception:
        pass


def save_douyin_inbox_monitor_config():
    try:
        payload = []
        for state in list_douyin_inbox_monitor_states():
            payload.append(
                {
                    "enabled": bool(state.get("enabled")),
                    "interval_minutes": max(1, min(int(state.get("interval_minutes", 30) or 30), 1440)),
                    "account_id": int(state.get("account_id", 0) or 0),
                    "max_conversations": max(1, min(int(state.get("max_conversations", 100) or 100), 100)),
                    "auto_reply_enabled": bool(state.get("auto_reply_enabled", False)),
                    "reply_mode": normalize_douyin_inbox_reply_mode(state.get("reply_mode", "fixed")),
                    "reply_message": str(state.get("reply_message", "") or "").strip(),
                    "reply_prompt": str(state.get("reply_prompt", "") or "").strip(),
                    "contact_value": str(state.get("contact_value", "") or "").strip(),
                }
            )
        douyin_state_store.save_blob_json(
            DOUYIN_INBOX_MONITOR_CONFIG_BLOB_KEY,
            payload,
        )
    except Exception:
        pass


def save_douyin_self_comment_monitor_config():
    try:
        payload = []
        for state in list_douyin_self_comment_monitor_states():
            payload.append(
                {
                    "enabled": bool(state.get("enabled")),
                    "interval_minutes": max(1, min(int(state.get("interval_minutes", 30) or 30), 1440)),
                    "account_id": int(state.get("account_id", 0) or 0),
                    "max_videos": max(1, min(int(state.get("max_videos", 20) or 20), 100)),
                    "max_comments_per_video": max(5, min(int(state.get("max_comments_per_video", 80) or 80), 500)),
                    "auto_reply_enabled": bool(state.get("auto_reply_enabled", False)),
                    "reply_mode": normalize_douyin_inbox_reply_mode(state.get("reply_mode", "fixed")),
                    "reply_message": str(state.get("reply_message", "") or "").strip(),
                    "reply_prompt": str(state.get("reply_prompt", "") or "").strip(),
                    "contact_value": str(state.get("contact_value", "") or "").strip(),
                    "reply_image_path": str(state.get("reply_image_path", "") or "").strip(),
                }
            )
        douyin_state_store.save_blob_json(
            DOUYIN_SELF_COMMENT_MONITOR_CONFIG_BLOB_KEY,
            payload,
        )
    except Exception:
        pass


def save_douyin_stranger_message_seen_records():
    try:
        douyin_state_store.save_blob_json(
            DOUYIN_STRANGER_MESSAGE_SEEN_RECORDS_BLOB_KEY,
            douyin_stranger_message_seen_records or {},
        )
    except Exception:
        pass


def save_douyin_mention_self_video_cache():
    try:
        douyin_state_store.save_blob_json(
            DOUYIN_MENTION_SELF_VIDEO_CACHE_BLOB_KEY,
            douyin_mention_self_video_cache or {},
        )
    except Exception:
        pass


def save_douyin_mention_comment_history():
    try:
        douyin_state_store.save_blob_json(
            DOUYIN_MENTION_COMMENT_HISTORY_BLOB_KEY,
            douyin_mention_comment_history or {},
        )
    except Exception:
        pass


def ensure_douyin_task_shape(task: Dict) -> Dict:
    task["cover_image"] = str(task.get("cover_image", "") or "").strip()
    task["source_session_id"] = str(task.get("source_session_id", "") or "").strip()
    task["source_item_key"] = str(task.get("source_item_key", "") or "").strip()
    task["source_keyword"] = str(task.get("source_keyword", "") or "").strip()
    task["source_comment_count"] = max(
        0,
        int(
            task.get("source_comment_count", task.get("comments", task.get("comment_total", 0))) or 0
        ),
    )
    task["source_comment_count_text"] = str(
        task.get("source_comment_count_text", task.get("comments_text", ""))
        or ""
    ).strip()
    task.setdefault("all_comments", [])
    task.setdefault("high_intent_users", [])
    task.setdefault("comment_count", 0)
    task["capture_comment_limit"] = max(0, int(task.get("capture_comment_limit", 0) or 0))
    task["capture_target_comments"] = max(0, int(task.get("capture_target_comments", 0) or 0))
    task["status"] = str(task.get("status", "pending") or "pending").strip() or "pending"
    task["error"] = str(task.get("error", "") or "").strip()
    raw_progress = task.get("collect_progress", {})
    progress = raw_progress if isinstance(raw_progress, dict) else {}
    progress_missing = not isinstance(raw_progress, dict) or not progress
    inferred_phase = (
        "completed"
        if task.get("status") == "completed"
        else "failed"
        if task.get("status") == "failed"
        else "scrolling"
        if task.get("status") == "processing"
        else "idle"
    )
    inferred_collected = max(
        int(progress.get("collected_comments", 0) or 0),
        int(task.get("comment_count", 0) or 0),
        len(task.get("all_comments", []) or []),
    )
    inferred_target = max(
        int(progress.get("target_comments", 0) or 0),
        int(task.get("capture_target_comments", 0) or 0),
        int(task.get("source_comment_count", 0) or 0),
    )
    if progress_missing and inferred_collected > 0 and inferred_target <= 0:
        inferred_target = inferred_collected
    task["collect_progress"] = {
        "phase": str(progress.get("phase", inferred_phase) or inferred_phase).strip() or inferred_phase,
        "account_id": int(progress.get("account_id", task.get("collect_account_id", 0)) or 0),
        "collected_comments": max(0, inferred_collected),
        "visible_comments": max(0, int(progress.get("visible_comments", 0) or 0)),
        "source_comment_total": max(
            0,
            int(
                progress.get(
                    "source_comment_total",
                    task.get("source_comment_count", inferred_collected if progress_missing else 0),
                )
                or 0
            ),
        ),
        "target_comments": max(0, inferred_target),
        "scroll_round": max(0, int(progress.get("scroll_round", 0) or 0)),
        "scroll_round_limit": max(0, int(progress.get("scroll_round_limit", 0) or 0)),
        "started_at": str(progress.get("started_at", "") or "").strip(),
        "updated_at": str(progress.get("updated_at", "") or "").strip(),
        "last_message": str(
            progress.get(
                "last_message",
                "评论采集已完成。"
                if progress_missing and task.get("status") == "completed"
                else task.get("error", "")
                if progress_missing and task.get("status") == "failed"
                else "",
            )
            or ""
        ).strip(),
    }
    task["video_comment_status"] = str(task.get("video_comment_status", "pending") or "pending").strip() or "pending"
    task["video_comment_error"] = str(task.get("video_comment_error", "") or "").strip()
    task["video_comment_mode"] = normalize_douyin_video_comment_mode(task.get("video_comment_mode"))
    task["video_comment_prompt"] = str(task.get("video_comment_prompt", "") or "").strip()
    task["video_comment_seed_text"] = str(task.get("video_comment_seed_text", "") or "").strip()
    task["video_comment_summary"] = str(task.get("video_comment_summary", "") or "").strip()
    task["video_comment_text"] = str(task.get("video_comment_text", "") or "").strip()
    task["video_comment_account_id"] = str(task.get("video_comment_account_id", "") or "").strip()
    task["video_comment_started_at"] = str(task.get("video_comment_started_at", "") or "").strip()
    task["video_comment_finished_at"] = str(task.get("video_comment_finished_at", "") or "").strip()
    return task


def build_douyin_task_lite_payload(task: Dict) -> Dict:
    normalized = ensure_douyin_task_shape(dict(task if isinstance(task, dict) else {}))
    return {
        "id": int(normalized.get("id", 0) or 0),
        "title": str(normalized.get("title", "") or "").strip(),
        "url": str(normalized.get("url", "") or "").strip(),
        "author": str(normalized.get("author", "") or "").strip(),
        "cover_image": str(normalized.get("cover_image", "") or "").strip(),
        "source_session_id": str(normalized.get("source_session_id", "") or "").strip(),
        "source_item_key": str(normalized.get("source_item_key", "") or "").strip(),
        "source_keyword": str(normalized.get("source_keyword", "") or "").strip(),
        "status": str(normalized.get("status", "pending") or "pending").strip() or "pending",
        "error": str(normalized.get("error", "") or "").strip(),
        "source_comment_count": max(int(normalized.get("source_comment_count", 0) or 0), 0),
        "capture_comment_limit": max(int(normalized.get("capture_comment_limit", 0) or 0), 0),
        "capture_target_comments": max(int(normalized.get("capture_target_comments", 0) or 0), 0),
        "collect_progress": dict(normalized.get("collect_progress", {}) or {}),
        "comment_count": max(
            int(normalized.get("comment_count", 0) or 0),
            len(normalized.get("all_comments", []) or []),
        ),
        "high_intent_count": len(normalized.get("high_intent_users", []) or []),
        "all_comments": [],
        "high_intent_users": [],
    }


def backfill_task_cover_images(tasks: List[Dict]) -> bool:
    latest_rows = douyin_search_cache.get("latest") or []
    if not isinstance(latest_rows, list) or not latest_rows:
        return False

    cover_lookup: Dict[str, str] = {}
    for row in latest_rows:
        if not isinstance(row, dict):
            continue
        cover = str(row.get("cover_image", "") or "").strip()
        if not cover:
            continue
        key = build_search_result_key(row)
        if key:
            cover_lookup[key] = cover

    if not cover_lookup:
        return False

    changed = False
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        current_cover = str(task.get("cover_image", "") or "").strip()
        if current_cover:
            continue
        lookup_key = build_search_result_key(
            {
                "url": task.get("url", ""),
                "title": task.get("title", ""),
                "author": task.get("author", ""),
            }
        )
        cover = cover_lookup.get(lookup_key, "")
        if cover:
            task["cover_image"] = cover
            changed = True
    return changed


def restore_douyin_tasks_state():
    global douyin_tasks
    try:
        loaded = douyin_state_store.load_blob_json("douyin_tasks", default=[])
        if isinstance(loaded, list):
            douyin_tasks = [ensure_douyin_task_shape(task if isinstance(task, dict) else {}) for task in loaded]
    except Exception:
        douyin_tasks = []


def restore_douyin_group_member_results():
    global douyin_group_member_results
    try:
        loaded = douyin_state_store.load_blob_json("douyin_group_members", default=[])
        if isinstance(loaded, list):
            douyin_group_member_results = [row for row in loaded if isinstance(row, dict)]
    except Exception:
        douyin_group_member_results = []


def restore_douyin_manual_interaction_users():
    global douyin_manual_interaction_users
    try:
        loaded = douyin_state_store.load_blob_json("douyin_manual_interaction_users", default=[])
        if isinstance(loaded, list):
            douyin_manual_interaction_users = [normalize_high_intent_user(row) for row in loaded if isinstance(row, dict)]
    except Exception:
        douyin_manual_interaction_users = []


def restore_douyin_stranger_message_results():
    global douyin_stranger_message_results
    try:
        loaded = douyin_state_store.load_blob_json(
            DOUYIN_STRANGER_MESSAGE_RESULTS_BLOB_KEY,
            default=[],
        )
        if isinstance(loaded, list):
            douyin_stranger_message_results = [
                normalize_douyin_stranger_message_row(row)
                for row in loaded
                if isinstance(row, dict)
            ]
    except Exception:
        douyin_stranger_message_results = []


def restore_douyin_inbox_results():
    global douyin_inbox_results
    try:
        loaded = douyin_state_store.load_blob_json(
            DOUYIN_INBOX_RESULTS_BLOB_KEY,
            default=[],
        )
        if isinstance(loaded, list):
            douyin_inbox_results = [
                normalize_douyin_inbox_row(row)
                for row in loaded
                if isinstance(row, dict) and not is_douyin_group_like_stranger_message_row(row)
            ]
    except Exception:
        douyin_inbox_results = []


def normalize_douyin_self_comment_monitor_state(
    payload: Optional[Dict[str, object]] = None,
    *,
    account_id: int = 0,
) -> Dict[str, object]:
    source = payload if isinstance(payload, dict) else {}
    raw_reply_mode = str(source.get("reply_mode", "fixed") or "fixed").strip().lower()
    reply_mode = raw_reply_mode if raw_reply_mode in {"fixed", "rewrite", "ai"} else "fixed"
    base = {
        "enabled": False,
        "running": False,
        "interval_minutes": 30,
        "account_id": int(account_id or source.get("account_id", 0) or 0) or None,
        "max_videos": 20,
        "max_comments_per_video": 80,
        "message": "",
        "last_run_at": "",
        "next_run_at": "",
        "last_error": "",
        "last_skip_reason": "",
        "last_cycle_status": "idle",
        "last_video_count": 0,
        "last_comment_count": 0,
        "last_new_comment_count": 0,
        "last_precise_count": 0,
        "auto_reply_enabled": False,
        "reply_mode": "fixed",
        "reply_message": "",
        "reply_prompt": "",
        "contact_value": "",
        "reply_image_path": "",
        "last_auto_reply_total": 0,
        "last_auto_reply_success": 0,
        "last_auto_reply_failed": 0,
    }
    base.update(
        {
            "enabled": bool(source.get("enabled", base["enabled"])),
            "running": bool(source.get("running", base["running"])),
            "interval_minutes": max(1, min(int(source.get("interval_minutes", base["interval_minutes"]) or 30), 1440)),
            "account_id": int(source.get("account_id", base["account_id"]) or base["account_id"] or 0) or None,
            "max_videos": max(1, min(int(source.get("max_videos", base["max_videos"]) or 20), 100)),
            "max_comments_per_video": max(5, min(int(source.get("max_comments_per_video", base["max_comments_per_video"]) or 80), 500)),
            "message": normalize_douyin_text(source.get("message", base["message"])),
            "last_run_at": normalize_douyin_text(source.get("last_run_at", base["last_run_at"])),
            "next_run_at": normalize_douyin_text(source.get("next_run_at", base["next_run_at"])),
            "last_error": normalize_douyin_text(source.get("last_error", base["last_error"])),
            "last_skip_reason": normalize_douyin_text(source.get("last_skip_reason", base["last_skip_reason"])),
            "last_cycle_status": normalize_douyin_text(source.get("last_cycle_status", base["last_cycle_status"])) or "idle",
            "last_video_count": max(0, int(source.get("last_video_count", base["last_video_count"]) or 0)),
            "last_comment_count": max(0, int(source.get("last_comment_count", base["last_comment_count"]) or 0)),
            "last_new_comment_count": max(0, int(source.get("last_new_comment_count", base["last_new_comment_count"]) or 0)),
            "last_precise_count": max(0, int(source.get("last_precise_count", base["last_precise_count"]) or 0)),
            "auto_reply_enabled": bool(source.get("auto_reply_enabled", base["auto_reply_enabled"])),
            "reply_mode": reply_mode,
            "reply_message": str(source.get("reply_message", base["reply_message"]) or "").strip(),
            "reply_prompt": str(source.get("reply_prompt", base["reply_prompt"]) or "").strip(),
            "contact_value": str(source.get("contact_value", base["contact_value"]) or "").strip(),
            "reply_image_path": str(source.get("reply_image_path", source.get("comment_image_path", base["reply_image_path"])) or "").strip(),
            "last_auto_reply_total": max(0, int(source.get("last_auto_reply_total", base["last_auto_reply_total"]) or 0)),
            "last_auto_reply_success": max(0, int(source.get("last_auto_reply_success", base["last_auto_reply_success"]) or 0)),
            "last_auto_reply_failed": max(0, int(source.get("last_auto_reply_failed", base["last_auto_reply_failed"]) or 0)),
        }
    )
    return base


def get_douyin_self_comment_monitor_state(account_id: int, create: bool = False) -> Dict[str, object]:
    key = str(int(account_id or 0) or 0)
    if key == "0":
        return normalize_douyin_self_comment_monitor_state({})
    existing = douyin_self_comment_monitor_states.get(key)
    if existing is None and create:
        existing = normalize_douyin_self_comment_monitor_state({}, account_id=int(key))
        douyin_self_comment_monitor_states[key] = existing
    return existing or normalize_douyin_self_comment_monitor_state({}, account_id=int(key))


def list_douyin_self_comment_monitor_states() -> List[Dict[str, object]]:
    states: List[Dict[str, object]] = []
    for key, raw_state in douyin_self_comment_monitor_states.items():
        account_id = int(key or 0) or 0
        if account_id <= 0:
            continue
        states.append(normalize_douyin_self_comment_monitor_state(raw_state, account_id=account_id))
    states.sort(key=lambda item: int(item.get("account_id", 0) or 0))
    return states


def normalize_douyin_self_comment_row(row: Dict) -> Dict:
    source = row if isinstance(row, dict) else {}
    normalized = normalize_high_intent_user(source)
    normalized["profile_url"] = ensure_douyin_profile_url(normalized)
    normalized["account_id"] = int(source.get("account_id", 0) or 0)
    normalized["aweme_id"] = normalize_douyin_text(source.get("aweme_id", ""))
    normalized["video_url"] = str(source.get("video_url", "") or "").strip()
    normalized["video_title"] = normalize_douyin_text(source.get("video_title", "") or source.get("title", ""))
    normalized["video_cover_image"] = str(source.get("video_cover_image", "") or source.get("cover_image", "") or "").strip()
    normalized["comment"] = normalize_douyin_text(source.get("comment", "") or source.get("content", ""))
    normalized["content"] = normalized["comment"]
    normalized["comment_time"] = normalize_douyin_text(source.get("comment_time", ""))
    normalized["comment_key"] = normalize_douyin_text(source.get("comment_key", "")) or build_douyin_self_comment_comment_key(normalized)
    normalized["row_key"] = normalize_douyin_text(source.get("row_key", ""))
    if not normalized["row_key"]:
        normalized["row_key"] = build_douyin_self_comment_row_key(normalized)
    normalized["is_high_intent"] = bool(source.get("is_high_intent", False))
    normalized["intent_level"] = normalize_douyin_text(source.get("intent_level", ""))
    normalized["score"] = source.get("score", "")
    normalized["reason"] = normalize_douyin_text(source.get("reason", ""))
    normalized["first_seen_at"] = normalize_douyin_text(source.get("first_seen_at", ""))
    normalized["last_seen_at"] = normalize_douyin_text(source.get("last_seen_at", ""))
    normalized["reply_status"] = normalize_douyin_text(source.get("reply_status", "pending")).lower() or "pending"
    normalized["reply_error"] = normalize_douyin_text(source.get("reply_error", ""))
    normalized["reply_message"] = normalize_douyin_text(source.get("reply_message", ""))
    raw_reply_mode = str(source.get("reply_mode", "fixed") or "fixed").strip().lower()
    normalized["reply_mode"] = raw_reply_mode if raw_reply_mode in {"fixed", "rewrite", "ai"} else "fixed"
    normalized["reply_account_id"] = str(source.get("reply_account_id", "") or "").strip()
    normalized["reply_started_at"] = normalize_douyin_text(source.get("reply_started_at", ""))
    normalized["reply_finished_at"] = normalize_douyin_text(source.get("reply_finished_at", ""))
    normalized["reply_updated_at"] = normalize_douyin_text(source.get("reply_updated_at", ""))
    normalized["source"] = "douyin_self_comment_monitor"
    return normalized


def build_douyin_self_comment_row_key(row: Dict) -> str:
    if not isinstance(row, dict):
        return ""
    account_id = int(row.get("account_id", 0) or 0)
    aweme_id = normalize_douyin_text(row.get("aweme_id", ""))
    comment_key = normalize_douyin_text(row.get("comment_key", "")) or build_douyin_self_comment_comment_key(row)
    if account_id and aweme_id and comment_key:
        return f"{account_id}|{aweme_id}|{comment_key}"
    video_url = normalize_douyin_text(row.get("video_url", ""))
    username = normalize_douyin_text(row.get("username", ""))
    comment = normalize_douyin_text(row.get("comment", row.get("content", "")))
    comment_time = normalize_douyin_text(row.get("comment_time", ""))
    return "|".join([str(account_id), aweme_id or video_url, username, comment, comment_time])


def build_douyin_self_comment_comment_key(row: Dict) -> str:
    if not isinstance(row, dict):
        return ""
    comment_id = normalize_douyin_text(row.get("comment_id", ""))
    if comment_id:
        return f"id:{comment_id}"
    return "|".join(
        [
            normalize_douyin_text(row.get("user_id", "")),
            normalize_douyin_text(row.get("username", "")),
            normalize_douyin_text(row.get("comment", row.get("content", ""))),
            normalize_douyin_text(row.get("comment_time", "")),
        ]
    )


def collect_douyin_self_comment_monitor_results(account_id: int = 0) -> List[Dict]:
    target_account_id = int(account_id or 0)
    rows: List[Dict] = []
    for row in douyin_self_comment_monitor_results:
        normalized = normalize_douyin_self_comment_row(row if isinstance(row, dict) else {})
        if target_account_id > 0 and int(normalized.get("account_id", 0) or 0) != target_account_id:
            continue
        rows.append(normalized)
    rows.sort(key=lambda item: (str(item.get("last_seen_at", "") or ""), str(item.get("comment_time", "") or "")), reverse=True)
    return rows


def merge_douyin_self_comment_monitor_results(account_id: int, rows: List[Dict]) -> int:
    global douyin_self_comment_monitor_results

    target_account_id = int(account_id or 0)
    if target_account_id <= 0:
        return 0

    account_rows = [
        normalize_douyin_self_comment_row(row)
        for row in douyin_self_comment_monitor_results
        if int((row or {}).get("account_id", 0) or 0) == target_account_id
    ]
    other_rows = [
        normalize_douyin_self_comment_row(row)
        for row in douyin_self_comment_monitor_results
        if int((row or {}).get("account_id", 0) or 0) != target_account_id
    ]
    account_map = {
        build_douyin_self_comment_row_key(row): row
        for row in account_rows
        if build_douyin_self_comment_row_key(row)
    }
    incoming_keys: List[str] = []
    changed = 0
    for raw_row in rows or []:
        if not isinstance(raw_row, dict):
            continue
        normalized = normalize_douyin_self_comment_row({**raw_row, "account_id": target_account_id})
        key = build_douyin_self_comment_row_key(normalized)
        if not key:
            continue
        existing = account_map.get(key)
        if existing:
            for keep_key in (
                "reply_status",
                "reply_error",
                "reply_message",
                "reply_mode",
                "reply_account_id",
                "reply_started_at",
                "reply_finished_at",
                "reply_updated_at",
                "first_seen_at",
            ):
                if existing.get(keep_key):
                    normalized[keep_key] = existing.get(keep_key)
            if existing.get("is_high_intent"):
                normalized["is_high_intent"] = True
                for keep_key in ("intent_level", "score", "reason"):
                    if existing.get(keep_key) not in (None, ""):
                        normalized[keep_key] = existing.get(keep_key)
            if existing != normalized:
                changed += 1
        else:
            changed += 1
        account_map[key] = normalized
        if key not in incoming_keys:
            incoming_keys.append(key)

    if not changed:
        return 0

    preserved_keys = [key for key in account_map.keys() if key not in incoming_keys]
    ordered_keys = incoming_keys + preserved_keys
    douyin_self_comment_monitor_results = other_rows + [account_map[key] for key in ordered_keys if key in account_map]
    save_douyin_self_comment_monitor_results()
    return changed


def update_douyin_self_comment_monitor_rows(
    target_rows: List[Dict],
    *,
    status: str,
    error: Optional[str] = None,
    message: Optional[str] = None,
    reply_mode: Optional[str] = None,
    account_id: Optional[int] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
) -> int:
    keys = {build_douyin_self_comment_row_key(row) for row in target_rows if build_douyin_self_comment_row_key(row)}
    if not keys:
        return 0
    changed = 0
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    next_rows: List[Dict] = []
    for raw_row in douyin_self_comment_monitor_results:
        normalized = normalize_douyin_self_comment_row(raw_row if isinstance(raw_row, dict) else {})
        if build_douyin_self_comment_row_key(normalized) in keys:
            normalized["reply_status"] = str(status or "").strip() or normalized.get("reply_status", "pending")
            if error is not None:
                normalized["reply_error"] = str(error or "")
            if message is not None:
                normalized["reply_message"] = str(message or "")
            if reply_mode is not None:
                normalized["reply_mode"] = normalize_douyin_inbox_reply_mode(reply_mode)
            if account_id is not None:
                normalized["reply_account_id"] = str(account_id or "")
            if started_at is not None:
                normalized["reply_started_at"] = str(started_at or "")
            if finished_at is not None:
                normalized["reply_finished_at"] = str(finished_at or "")
            normalized["reply_updated_at"] = updated_at
            changed += 1
        next_rows.append(normalized)
    if changed:
        douyin_self_comment_monitor_results[:] = next_rows
        save_douyin_self_comment_monitor_results()
    return changed


def restore_douyin_self_comment_monitor_results():
    global douyin_self_comment_monitor_results
    try:
        loaded = douyin_state_store.load_blob_json(
            DOUYIN_SELF_COMMENT_MONITOR_RESULTS_BLOB_KEY,
            default=[],
        )
        if isinstance(loaded, list):
            douyin_self_comment_monitor_results = [
                normalize_douyin_self_comment_row(row)
                for row in loaded
                if isinstance(row, dict)
            ]
    except Exception:
        douyin_self_comment_monitor_results = []


def restore_douyin_self_comment_monitor_config():
    global douyin_self_comment_monitor_states
    try:
        loaded = douyin_state_store.load_blob_json(
            DOUYIN_SELF_COMMENT_MONITOR_CONFIG_BLOB_KEY,
            default=[],
        )
        next_states: Dict[str, Dict[str, object]] = {}
        items = loaded if isinstance(loaded, list) else [loaded] if isinstance(loaded, dict) else []
        for raw_state in items:
            if not isinstance(raw_state, dict):
                continue
            account_id = int(raw_state.get("account_id", 0) or 0)
            if account_id <= 0:
                continue
            next_states[str(account_id)] = normalize_douyin_self_comment_monitor_state(
                raw_state,
                account_id=account_id,
            )
        douyin_self_comment_monitor_states = next_states
    except Exception:
        douyin_self_comment_monitor_states = {}


def restore_douyin_stranger_message_monitor_config():
    global douyin_stranger_message_monitor_states
    try:
        loaded = douyin_state_store.load_blob_json(
            DOUYIN_STRANGER_MESSAGE_MONITOR_CONFIG_BLOB_KEY,
            default=[],
        )
        next_states: Dict[str, Dict[str, object]] = {}
        items = loaded if isinstance(loaded, list) else [loaded] if isinstance(loaded, dict) else []
        for raw_state in items:
            if not isinstance(raw_state, dict):
                continue
            account_id = int(raw_state.get("account_id", 0) or 0)
            if account_id <= 0:
                continue
            next_states[str(account_id)] = normalize_douyin_stranger_message_monitor_state(
                raw_state,
                account_id=account_id,
            )
        douyin_stranger_message_monitor_states = next_states
    except Exception:
        douyin_stranger_message_monitor_states = {}


def restore_douyin_inbox_monitor_config():
    global douyin_inbox_monitor_states
    try:
        loaded = douyin_state_store.load_blob_json(
            DOUYIN_INBOX_MONITOR_CONFIG_BLOB_KEY,
            default=[],
        )
        next_states: Dict[str, Dict[str, object]] = {}
        items = loaded if isinstance(loaded, list) else [loaded] if isinstance(loaded, dict) else []
        for raw_state in items:
            if not isinstance(raw_state, dict):
                continue
            account_id = int(raw_state.get("account_id", 0) or 0)
            if account_id <= 0:
                continue
            next_states[str(account_id)] = normalize_douyin_inbox_monitor_state(
                raw_state,
                account_id=account_id,
            )
        douyin_inbox_monitor_states = next_states
    except Exception:
        douyin_inbox_monitor_states = {}


def restore_douyin_stranger_message_seen_records():
    global douyin_stranger_message_seen_records
    try:
        loaded = douyin_state_store.load_blob_json(
            DOUYIN_STRANGER_MESSAGE_SEEN_RECORDS_BLOB_KEY,
            default={},
        )
        next_records: Dict[str, Dict[str, str]] = {}
        if isinstance(loaded, dict):
            for raw_account_id, raw_bucket in loaded.items():
                account_key = str(int(raw_account_id or 0) or 0)
                if account_key == "0":
                    continue
                bucket: Dict[str, str] = {}
                if isinstance(raw_bucket, dict):
                    for raw_fingerprint, raw_timestamp in raw_bucket.items():
                        fingerprint = normalize_douyin_text(raw_fingerprint)
                        if not fingerprint:
                            continue
                        bucket[fingerprint] = normalize_douyin_text(raw_timestamp)
                elif isinstance(raw_bucket, list):
                    for raw_fingerprint in raw_bucket:
                        fingerprint = normalize_douyin_text(raw_fingerprint)
                        if fingerprint:
                            bucket[fingerprint] = ""
                if bucket:
                    next_records[account_key] = bucket
        douyin_stranger_message_seen_records = next_records
    except Exception:
        douyin_stranger_message_seen_records = {}


def restore_douyin_customer_pools_state():
    global douyin_all_customer_pool, douyin_precise_customer_pool
    try:
        all_rows, precise_rows = build_combined_douyin_customer_pools()
        if not all_rows and not precise_rows:
            all_rows, precise_rows = douyin_state_store.load_douyin_customer_pools()
        douyin_all_customer_pool = [row for row in all_rows if isinstance(row, dict)]
        douyin_precise_customer_pool = [row for row in precise_rows if isinstance(row, dict)]
    except Exception:
        douyin_all_customer_pool = []
        douyin_precise_customer_pool = []


def restore_douyin_mention_self_video_cache():
    global douyin_mention_self_video_cache
    try:
        loaded = douyin_state_store.load_blob_json(
            DOUYIN_MENTION_SELF_VIDEO_CACHE_BLOB_KEY,
            default={},
        )
        if isinstance(loaded, dict):
            douyin_mention_self_video_cache = {
                "account_id": int(loaded.get("account_id", 0) or 0),
                "profile": loaded.get("profile", {}) if isinstance(loaded.get("profile"), dict) else {},
                "videos": [row for row in (loaded.get("videos", []) or []) if isinstance(row, dict)],
                "fetched_at": str(loaded.get("fetched_at", "") or "").strip(),
                "selected_video_url": str(loaded.get("selected_video_url", "") or "").strip(),
            }
    except Exception:
        douyin_mention_self_video_cache = {
            "account_id": 0,
            "profile": {},
            "videos": [],
            "fetched_at": "",
            "selected_video_url": "",
        }


def restore_douyin_mention_comment_history():
    global douyin_mention_comment_history
    try:
        loaded = douyin_state_store.load_blob_json(
            DOUYIN_MENTION_COMMENT_HISTORY_BLOB_KEY,
            default={},
        )
        if isinstance(loaded, dict):
            douyin_mention_comment_history = {
                str(key or "").strip(): value
                for key, value in loaded.items()
                if str(key or "").strip() and isinstance(value, dict)
            }
    except Exception:
        douyin_mention_comment_history = {}


def normalize_douyin_schedule_type(value: object) -> str:
    normalized = str(value or "collect_precise").strip().lower()
    return normalized if normalized in DOUYIN_SCHEDULE_TYPES else "collect_precise"


def coerce_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enabled", "active"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled", "inactive", ""}:
        return False
    return default


def normalize_schedule_time_text(value: object, default: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", text):
        return text
    return default


def parse_schedule_datetime(value: object) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def parse_comment_timestamp(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return 0
    direct = parse_schedule_datetime(text)
    if direct:
        return direct.timestamp()
    for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%m-%d %H:%M:%S", "%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt.startswith("%m-%d"):
                parsed = parsed.replace(year=datetime.now().year)
            return parsed.timestamp()
        except ValueError:
            continue
    return 0


def format_schedule_datetime(value: Optional[datetime]) -> str:
    if not value:
        return ""
    return value.strftime("%Y-%m-%d %H:%M:%S")


def build_douyin_schedule_plan_id() -> str:
    return f"plan_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000, 9999)}"


def normalize_douyin_schedule_plan(raw_plan: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    plan = raw_plan if isinstance(raw_plan, dict) else {}
    schedule_type = normalize_douyin_schedule_type(plan.get("type"))
    interval_minutes = max(5, min(int(plan.get("interval_minutes", 120) or 120), 7 * 24 * 60))
    max_results = max(5, min(int(plan.get("max_results", 50) or 50), 100))
    max_videos_per_run = max(1, min(int(plan.get("max_videos_per_run", 50) or 50), 50))
    max_users_per_run = max(1, min(int(plan.get("max_users_per_run", 30) or 30), 200))
    follow_interval_minutes_min = max(
        0,
        min(float(plan.get("follow_interval_minutes_min", 3) or 3), 1440),
    )
    follow_interval_minutes_max = max(
        follow_interval_minutes_min,
        min(float(plan.get("follow_interval_minutes_max", follow_interval_minutes_min or 5) or follow_interval_minutes_min or 5), 1440),
    )
    interaction_interval_minutes_min = max(
        0,
        min(float(plan.get("interaction_interval_minutes_min", 3) or 3), 1440),
    )
    interaction_interval_minutes_max = max(
        interaction_interval_minutes_min,
        min(float(plan.get("interaction_interval_minutes_max", interaction_interval_minutes_min or 5) or interaction_interval_minutes_min or 5), 1440),
    )
    comment_scroll_rounds = max(20, min(int(plan.get("comment_scroll_rounds", 300) or 300), 300))
    comment_max_comments = max(20, min(int(plan.get("comment_max_comments", 500) or 500), 500))
    next_run_at = parse_schedule_datetime(plan.get("next_run_at"))
    window_start = normalize_schedule_time_text(plan.get("window_start"), "09:00")
    window_end = normalize_schedule_time_text(plan.get("window_end"), "23:00")
    normalized = {
        "id": str(plan.get("id", "") or "").strip() or build_douyin_schedule_plan_id(),
        "name": str(plan.get("name", "") or "").strip()
        or {
            "collect_precise": "采集视频精准客户",
            "follow_comment": "精准互动计划",
            "interaction": "精准私信计划",
        }.get(schedule_type, "抖音排期计划"),
        "type": schedule_type,
        "enabled": coerce_bool(plan.get("enabled", True), True),
        "keyword": str(plan.get("keyword", "") or "").strip(),
        "interval_minutes": interval_minutes,
        "window_start": window_start,
        "window_end": window_end,
        "max_results": max_results,
        "max_videos_per_run": max_videos_per_run,
        "max_users_per_run": max_users_per_run,
        "comment_scroll_rounds": comment_scroll_rounds,
        "comment_max_comments": comment_max_comments,
        "comment_mode": normalize_douyin_video_comment_mode(plan.get("comment_mode")),
        "comment_text": str(plan.get("comment_text", "") or "").strip(),
        "comment_prompt": str(plan.get("comment_prompt", "") or "").strip(),
        "comment_seed_text": str(plan.get("comment_seed_text", "") or "").strip(),
        "message_mode": normalize_douyin_video_comment_mode(plan.get("message_mode")),
        "message": str(plan.get("message", "") or "").strip(),
        "message_prompt": str(plan.get("message_prompt", "") or "").strip(),
        "message_seed_text": str(plan.get("message_seed_text", "") or "").strip(),
        "follow_interval_minutes_min": follow_interval_minutes_min,
        "follow_interval_minutes_max": follow_interval_minutes_max,
        "interaction_interval_minutes_min": interaction_interval_minutes_min,
        "interaction_interval_minutes_max": interaction_interval_minutes_max,
        "require_follow_comment_completed": coerce_bool(plan.get("require_follow_comment_completed", False), False),
        "last_run_at": str(plan.get("last_run_at", "") or "").strip(),
        "next_run_at": format_schedule_datetime(next_run_at) if next_run_at else "",
        "last_status": str(plan.get("last_status", "idle") or "idle").strip() or "idle",
        "last_message": str(plan.get("last_message", "") or "").strip(),
        "last_error": str(plan.get("last_error", "") or "").strip(),
        "total_runs": max(0, int(plan.get("total_runs", 0) or 0)),
        "success_runs": max(0, int(plan.get("success_runs", 0) or 0)),
        "failure_runs": max(0, int(plan.get("failure_runs", 0) or 0)),
        "updated_at": str(plan.get("updated_at", "") or "").strip() or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if normalized["enabled"] and not normalized["next_run_at"]:
        normalized["next_run_at"] = format_schedule_datetime(
            get_next_schedule_window_anchor(
                datetime.now(),
                window_start,
                window_end,
                include_current_if_inside=True,
            )
        )
    return normalized


def save_douyin_schedule_plans():
    try:
        douyin_state_store.save_blob_json(DOUYIN_SCHEDULE_PLANS_BLOB_KEY, douyin_schedule_plans or [])
    except Exception:
        pass


def restore_douyin_schedule_plans():
    global douyin_schedule_plans
    try:
        loaded = douyin_state_store.load_blob_json(DOUYIN_SCHEDULE_PLANS_BLOB_KEY, default=[])
        if isinstance(loaded, list):
            douyin_schedule_plans = [
                normalize_douyin_schedule_plan(item)
                for item in loaded
                if isinstance(item, dict)
            ]
    except Exception:
        douyin_schedule_plans = []


def is_schedule_time_window_open(
    now: Optional[datetime],
    start_text: object,
    end_text: object,
) -> bool:
    current = now or datetime.now()
    start_value = normalize_schedule_time_text(start_text, "09:00")
    end_value = normalize_schedule_time_text(end_text, "23:00")
    start_hour, start_minute = [int(item) for item in start_value.split(":")]
    end_hour, end_minute = [int(item) for item in end_value.split(":")]
    current_minutes = current.hour * 60 + current.minute
    start_minutes = start_hour * 60 + start_minute
    end_minutes = end_hour * 60 + end_minute
    if start_minutes <= end_minutes:
        return start_minutes <= current_minutes <= end_minutes
    return current_minutes >= start_minutes or current_minutes <= end_minutes


def get_next_schedule_window_anchor(
    current: Optional[datetime],
    start_text: object,
    end_text: object,
    *,
    include_current_if_inside: bool = True,
) -> datetime:
    moment = current or datetime.now()
    start_value = normalize_schedule_time_text(start_text, "09:00")
    end_value = normalize_schedule_time_text(end_text, "23:00")
    start_hour, start_minute = [int(item) for item in start_value.split(":")]
    end_hour, end_minute = [int(item) for item in end_value.split(":")]
    start_minutes = start_hour * 60 + start_minute
    end_minutes = end_hour * 60 + end_minute

    if include_current_if_inside and is_schedule_time_window_open(moment, start_value, end_value):
        return moment

    today = moment.date()
    today_start = datetime.combine(today, datetime.min.time()).replace(hour=start_hour, minute=start_minute)

    if start_minutes <= end_minutes:
        if moment <= today_start:
            return today_start
        return today_start + timedelta(days=1)

    current_minutes = moment.hour * 60 + moment.minute
    if current_minutes < start_minutes and current_minutes > end_minutes:
        return today_start
    if current_minutes <= end_minutes:
        return today_start
    return today_start + timedelta(days=1)


def get_next_douyin_schedule_run(
    current: Optional[datetime],
    interval_minutes: int,
    preferred_start: Optional[datetime] = None,
    window_start: object = "09:00",
    window_end: object = "23:00",
) -> datetime:
    base = preferred_start or current or datetime.now()
    candidate = base + timedelta(minutes=max(5, int(interval_minutes or 5)))
    if is_schedule_time_window_open(candidate, window_start, window_end):
        return candidate
    return get_next_schedule_window_anchor(
        candidate,
        window_start,
        window_end,
        include_current_if_inside=False,
    )


def update_douyin_schedule_plan_runtime(
    plan_id: str,
    *,
    status: Optional[str] = None,
    message: Optional[str] = None,
    error: Optional[str] = None,
    bump_total: bool = False,
    success: bool = False,
    failed: bool = False,
    next_run_at: Optional[datetime] = None,
):
    changed = False
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for index, raw_plan in enumerate(douyin_schedule_plans):
        plan = normalize_douyin_schedule_plan(raw_plan)
        if str(plan.get("id", "") or "") != str(plan_id or ""):
            continue
        if status is not None:
            plan["last_status"] = status
        if message is not None:
            plan["last_message"] = str(message or "").strip()
        if error is not None:
            plan["last_error"] = str(error or "").strip()
        if bump_total:
            plan["total_runs"] = int(plan.get("total_runs", 0) or 0) + 1
            plan["last_run_at"] = now_text
        if success:
            plan["success_runs"] = int(plan.get("success_runs", 0) or 0) + 1
        if failed:
            plan["failure_runs"] = int(plan.get("failure_runs", 0) or 0) + 1
        if next_run_at is not None:
            plan["next_run_at"] = format_schedule_datetime(next_run_at)
        plan["updated_at"] = now_text
        douyin_schedule_plans[index] = plan
        changed = True
        break
    if changed:
        save_douyin_schedule_plans()


def reconcile_douyin_runtime_state() -> bool:
    """Repair stale runtime flags when the background task is already gone."""
    global douyin_running, douyin_stop_requested, douyin_background_task

    has_processing_task = any(task.get("status") == "processing" for task in douyin_tasks)
    background_alive = bool(douyin_background_task and not douyin_background_task.done())

    if background_alive:
        douyin_running = True
        return True

    state_changed = False
    if has_processing_task:
        for task in douyin_tasks:
            if task.get("status") == "processing":
                task["status"] = "pending"
                task["error"] = task.get("error") or "Recovered from stale running state"
                task["collect_progress"] = {
                    **ensure_douyin_task_shape(task).get("collect_progress", {}),
                    "phase": "idle",
                    "last_message": "任务中断，已从旧的运行态恢复。",
                    "updated_at": _now_text(),
                }
        state_changed = True

    if douyin_running or douyin_background_task is not None or douyin_stop_requested or state_changed:
        douyin_running = False
        douyin_stop_requested = False
        douyin_background_task = None
        if state_changed:
            save_douyin_tasks_state()

    return False


def reconcile_douyin_video_comment_runtime_state() -> bool:
    global douyin_video_comment_running, douyin_video_comment_stop_requested, douyin_video_comment_background_task

    background_alive = bool(
        douyin_video_comment_background_task and not douyin_video_comment_background_task.done()
    )
    if background_alive:
        douyin_video_comment_running = True
        douyin_video_comment_state["running"] = True
        return True

    if douyin_video_comment_running or douyin_video_comment_background_task is not None or douyin_video_comment_stop_requested:
        douyin_video_comment_running = False
        douyin_video_comment_stop_requested = False
        douyin_video_comment_background_task = None
        douyin_video_comment_state.update({"running": False, "current_task_title": "", "current_comment_text": ""})
    return False


def reconcile_douyin_mention_comment_runtime_state() -> bool:
    global douyin_mention_comment_running, douyin_mention_comment_stop_requested, douyin_mention_comment_background_task

    background_alive = bool(
        douyin_mention_comment_background_task and not douyin_mention_comment_background_task.done()
    )
    if background_alive:
        douyin_mention_comment_running = True
        douyin_mention_comment_state["running"] = True
        return True

    repaired = False
    for task in douyin_tasks:
        for user in task.get("high_intent_users", []) or []:
            if user.get("mention_comment_status") == "processing":
                user["mention_comment_status"] = "queued"
                user["mention_comment_error"] = user.get("mention_comment_error") or "Recovered from stale mention-comment state"
                user["mention_comment_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                repaired = True

    for user in douyin_manual_interaction_users:
        if user.get("mention_comment_status") == "processing":
            user["mention_comment_status"] = "queued"
            user["mention_comment_error"] = user.get("mention_comment_error") or "Recovered from stale mention-comment state"
            user["mention_comment_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            repaired = True

    if repaired:
        save_douyin_tasks_state()
        save_douyin_manual_interaction_users()

    if douyin_mention_comment_running or douyin_mention_comment_background_task is not None or douyin_mention_comment_stop_requested:
        douyin_mention_comment_running = False
        douyin_mention_comment_stop_requested = False
        douyin_mention_comment_background_task = None
        douyin_mention_comment_state.update(
            {
                "running": False,
                "current_user": "",
                "current_users": [],
                "comment_preview": "",
            }
        )
    return False


def reconcile_douyin_follow_comment_runtime_state() -> bool:
    global douyin_follow_comment_running, douyin_follow_comment_stop_requested, douyin_follow_comment_background_task

    background_alive = bool(
        douyin_follow_comment_background_task and not douyin_follow_comment_background_task.done()
    )
    if background_alive:
        douyin_follow_comment_running = True
        douyin_follow_comment_state["running"] = True
        return True

    repaired = False
    for task in douyin_tasks:
        for user in task.get("high_intent_users", []) or []:
            if user.get("follow_comment_status") == "processing":
                user["follow_comment_status"] = "queued"
                user["follow_comment_error"] = user.get("follow_comment_error") or "Recovered from stale follow-comment state"
                user["follow_comment_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                repaired = True

    for user in douyin_manual_interaction_users:
        if user.get("follow_comment_status") == "processing":
            user["follow_comment_status"] = "queued"
            user["follow_comment_error"] = user.get("follow_comment_error") or "Recovered from stale follow-comment state"
            user["follow_comment_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            repaired = True

    if repaired:
        save_douyin_tasks_state()
        save_douyin_manual_interaction_users()

    if douyin_follow_comment_running or douyin_follow_comment_background_task is not None or douyin_follow_comment_stop_requested:
        douyin_follow_comment_running = False
        douyin_follow_comment_stop_requested = False
        douyin_follow_comment_background_task = None
        douyin_follow_comment_state.update(
            {
                "running": False,
                "current_user": "",
                "current_users": [],
                "current_comment_text": "",
                "account_ids": [],
                "workers": [],
            }
        )
    return False


def reconcile_douyin_group_member_runtime_state() -> bool:
    global douyin_group_member_running, douyin_group_member_stop_requested, douyin_group_member_background_task

    background_alive = bool(
        douyin_group_member_background_task and not douyin_group_member_background_task.done()
    )
    if background_alive:
        douyin_group_member_running = True
        douyin_group_member_state["running"] = True
        return True

    if douyin_group_member_running or douyin_group_member_background_task is not None or douyin_group_member_stop_requested:
        douyin_group_member_running = False
        douyin_group_member_stop_requested = False
        douyin_group_member_background_task = None
        douyin_group_member_state.update({"running": False, "current_group": ""})
    return False


def reconcile_douyin_account_nurture_runtime_state() -> bool:
    global douyin_account_nurture_scheduler, douyin_account_nurture_background_task

    background_alive = bool(
        douyin_account_nurture_background_task and not douyin_account_nurture_background_task.done()
    )
    if background_alive and douyin_account_nurture_scheduler:
        return True

    if douyin_account_nurture_scheduler and not douyin_account_nurture_scheduler.is_running:
        douyin_account_nurture_scheduler = None
    if douyin_account_nurture_background_task is not None and not background_alive:
        douyin_account_nurture_background_task = None
    return False


def build_douyin_account_nurture_idle_status(config: Optional[Dict] = None) -> Dict:
    current_config = config or load_global_config()
    accounts = _normalize_accounts(current_config.get("douyin_accounts"))
    session_min = max(5, int(current_config.get("douyin_nurture_session_min_minutes", 20) or 20))
    session_max = max(session_min, int(current_config.get("douyin_nurture_session_max_minutes", 40) or 40))
    interval_min = max(30, int(current_config.get("douyin_nurture_interval_min_minutes", 120) or 120))
    interval_max = max(interval_min, int(current_config.get("douyin_nurture_interval_max_minutes", 180) or 180))
    active_start_hour = max(6, min(int(current_config.get("douyin_nurture_active_start_hour", 9) or 9), 20))
    active_end_hour = max(active_start_hour + 1, min(int(current_config.get("douyin_nurture_active_end_hour", 23) or 23), 23))
    return {
        "running": False,
        "started_at": "",
        "running_accounts": 0,
        "waiting_accounts": 0,
        "enabled_accounts": 0,
        "online_accounts": sum(1 for account in accounts if account.get("status") == "online"),
        "total_sessions": 0,
        "rules": {
            "entry_url": "https://www.douyin.com/jingxuan",
            "session_label": f"{session_min}-{session_max} 分钟 / 次",
            "interval_label": f"{interval_min}-{interval_max} 分钟 / 次",
            "active_window_label": f"{active_start_hour:02d}:00-{active_end_hour:02d}:00",
            "daily_runs_label": "约 5-6 次 / 天",
            "warning": "养号期间不要执行其他抖音任务，否则浏览器会互相抢占，容易冲突。",
        },
        "accounts": [
            {
                "account_id": int(account.get("id", 0) or 0),
                "port": int(account.get("port", 0) or 0),
                "account_status": str(account.get("status", "offline") or "offline"),
                "profile_dir": (
                    DouyinClient(
                        port=int(account.get("port", 0) or 0),
                        account_id=int(account.get("id", 0) or 0) or None,
                    ).resolve_profile_dir()
                    if int(account.get("id", 0) or 0) or int(account.get("port", 0) or 0)
                    else ""
                ),
                "connection_mode": "待连接",
                "worker_status": "idle",
                "last_action": "未启动养号",
                "last_error": "",
                "last_started_at": "",
                "last_finished_at": "",
                "next_run_at": "",
                "completed_sessions": 0,
                "current_session_minutes": 0,
                "current_video_count": 0,
                "likes_sent": 0,
                "is_enabled": False,
                "can_participate": str(account.get("status", "offline") or "offline") == "online",
            }
            for account in accounts
        ],
    }


def parse_douyin_nurture_account_ids(request: Optional[Dict]) -> List[int]:
    source = request if isinstance(request, dict) else {}
    return sorted({int(account_id) for account_id in (source.get("account_ids") or []) if int(account_id or 0) > 0})


def build_douyin_nurture_conflict(action_label: str) -> Optional[Dict]:
    running = reconcile_douyin_account_nurture_runtime_state()
    if running and douyin_account_nurture_scheduler and douyin_account_nurture_scheduler.is_actively_blocking_other_tasks():
        return {
            "code": 409,
            "type": "account_nurture_running",
            "msg": f"账号养号正在运行，请先暂停养号后再{action_label}。养号期间不要执行其他抖音任务，否则可能冲突。",
        }
    return None


def safe_int(value, default: int) -> int:
    try:
        text = str(value).strip() if value is not None else ""
        if not text:
            return int(default)
        return int(text)
    except Exception:
        return int(default)


def _normalize_accounts(accounts: Optional[List[Dict]]) -> List[Dict]:
    normalized_by_id: Dict[int, Dict] = {}
    source = accounts if isinstance(accounts, list) else []
    for fallback, row in enumerate(source, start=1):
        item = row if isinstance(row, dict) else {}
        account_id = safe_int(item.get("id", fallback), fallback)
        if account_id <= 0 or account_id > DOUYIN_ACCOUNT_LIMIT:
            continue
        normalized_by_id[account_id] = {
            "id": account_id,
            "status": str(item.get("status", "offline") or "offline"),
            "port": safe_int(item.get("port", 9331 + account_id), 9331 + account_id),
        }

    for fallback in DEFAULT_DOUYIN_ACCOUNTS:
        fallback_id = int(fallback.get("id", 0) or 0)
        if fallback_id <= 0 or fallback_id in normalized_by_id:
            continue
        normalized_by_id[fallback_id] = dict(fallback)

    if not normalized_by_id:
        normalized_by_id = {
            int(item["id"]): dict(item)
            for item in DEFAULT_DOUYIN_ACCOUNTS
        }

    normalized = list(normalized_by_id.values())
    normalized.sort(key=lambda row: row["id"])
    return normalized


def load_global_config() -> Dict:
    defaults = {
        "api_url": "https://ai.comfly.chat/v1/chat/completions",
        "api_key": "",
        "model": "gpt-5.4",
        "comment_direction": "亲切、有趣、鼓励",
        "douyin_comment_direction": DOUYIN_INTENT_DIRECTION_DEFAULT,
        "douyin_comment_filter_strategy": "prompt",
        "search_min_likes": 500,
        "search_avg_multiplier": 1.5,
        "search_exclude_historical_duplicates": True,
        "comment_scroll_rounds": 300,
        "comment_max_comments": 500,
        "douyin_default_account_id": 1,
        "douyin_accounts": DEFAULT_DOUYIN_ACCOUNTS,
        "douyin_message_show_browser": False,
        "douyin_nurture_interval_min_minutes": 120,
        "douyin_nurture_interval_max_minutes": 180,
        "douyin_nurture_session_min_minutes": 20,
        "douyin_nurture_session_max_minutes": 40,
        "douyin_nurture_active_start_hour": 9,
        "douyin_nurture_active_end_hour": 23,
    }
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                defaults.update(loaded)
        except Exception:
            pass
    defaults["douyin_accounts"] = _normalize_accounts(defaults.get("douyin_accounts"))
    defaults["douyin_default_account_id"] = safe_int(defaults.get("douyin_default_account_id", 1), 1)
    if defaults["douyin_default_account_id"] <= 0 or defaults["douyin_default_account_id"] > DOUYIN_ACCOUNT_LIMIT:
        defaults["douyin_default_account_id"] = 1
    return defaults


def save_global_config(config: Dict):
    payload = {}
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                payload.update(loaded)
        except Exception:
            pass
    payload.update(config)
    payload["douyin_accounts"] = _normalize_accounts(payload.get("douyin_accounts"))
    payload["douyin_default_account_id"] = safe_int(payload.get("douyin_default_account_id", 1), 1)
    if payload["douyin_default_account_id"] <= 0 or payload["douyin_default_account_id"] > DOUYIN_ACCOUNT_LIMIT:
        payload["douyin_default_account_id"] = 1
    with open(GLOBAL_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def should_show_douyin_message_browser(config: Optional[Dict] = None) -> bool:
    return True


def create_douyin_message_scraper(account: Dict, config: Optional[Dict] = None) -> DouyinCommentScraper:
    return DouyinCommentScraper(
        headless=False,
        account_id=account["id"],
        cdp_port=account["port"],
        allow_workspace_fallback=True,
        allow_cdp_reuse=True,
    )


def create_douyin_inbox_scraper(account: Dict, config: Optional[Dict] = None) -> DouyinCommentScraper:
    return DouyinCommentScraper(
        headless=False,
        account_id=account["id"],
        cdp_port=account["port"],
        allow_workspace_fallback=True,
        allow_cdp_reuse=True,
    )


class DouyinInboxPageWorker:
    def __init__(self, account_id: int):
        self.account_id = int(account_id or 0)
        self._lock = asyncio.Lock()
        self._scraper: Optional[DouyinCommentScraper] = None
        self._page = None
        self._started_at = ""
        self._last_used_at = ""

    @staticmethod
    def _is_retryable_runtime_error(exc: Exception) -> bool:
        message = str(exc or "")
        return any(
            marker in message
            for marker in [
                "Target page, context or browser has been closed",
                "Browser has been closed",
                "Connection closed",
                "has been disconnected",
                "Protocol error",
                "Page.wait_for_timeout",
            ]
        )

    async def _reset_runtime(self):
        scraper = self._scraper
        self._scraper = None
        self._page = None
        if scraper:
            try:
                await scraper.close()
            except Exception:
                pass

    async def _ensure_runtime(self):
        if self._page:
            try:
                if not self._page.is_closed():
                    return
            except Exception:
                pass
        await self._reset_runtime()
        config = load_global_config()
        account = get_douyin_account_by_id(self.account_id, config)
        if not account:
            raise RuntimeError(f"未找到抖音账号 {self.account_id}")
        if str(account.get("status", "") or "").strip() != "online":
            raise RuntimeError(f"账号 {self.account_id} 还没有登录完成")
        scraper = create_douyin_inbox_scraper(account, config)
        page = await scraper.open_chat_workspace_page(logger=douyin_log)
        self._scraper = scraper
        self._page = page
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not self._started_at:
            self._started_at = timestamp
        self._last_used_at = timestamp

    async def fetch_detail(self, row: Dict) -> Dict:
        async with self._lock:
            for attempt in range(2):
                await self._ensure_runtime()
                try:
                    opened, reason = await self._scraper.open_chat_page_conversation(self._page, row)
                    if not opened:
                        raise RuntimeError(reason or "未找到目标会话")
                    try:
                        await self._page.wait_for_function(
                            """() => Boolean(document.querySelector('[data-e2e=\"msg-item-content\"]'))""",
                            timeout=15000,
                        )
                    except Exception:
                        try:
                            await self._page.wait_for_function(
                                """() => Boolean(document.querySelector('.RightPanelHeadertitle, .RightPanelHeader'))""",
                                timeout=5000,
                            )
                        except Exception:
                            pass
                    await self._page.wait_for_timeout(1200)
                    detail = await self._scraper.extract_chat_page_conversation_detail(self._page)
                    messages = detail.get("messages", []) if isinstance(detail, dict) else []
                    if not isinstance(messages, list) or not messages:
                        raise RuntimeError("已打开会话，但没有读取到右侧消息气泡")
                    self._last_used_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    return {
                        "conversation_key": str(row.get("conversation_key", "") or row.get("conversation_id", "") or "").strip(),
                        "username": str(detail.get("username", "") or row.get("username", "") or "").strip(),
                        "avatar_url": str(detail.get("avatar_url", "") or row.get("avatar_url", "") or row.get("avatar", "") or "").strip(),
                        "profile_url": str(detail.get("profile_url", "") or row.get("profile_url", "") or "").strip(),
                        "incoming_message": str(detail.get("incoming_message", "") or row.get("incoming_message", "") or row.get("preview_text", "") or "").strip(),
                        "reply_message": str(detail.get("reply_message", "") or "").strip(),
                        "messages": detail.get("messages", []),
                        "fetched_at": self._last_used_at,
                    }
                except Exception as exc:
                    if attempt == 0 and self._is_retryable_runtime_error(exc):
                        await self._reset_runtime()
                        continue
                    raise

    async def send_message(self, row: Dict, message: str) -> Dict:
        async with self._lock:
            for attempt in range(2):
                await self._ensure_runtime()
                try:
                    message_text = str(message or "").strip()
                    message_parts = [
                        part.strip()
                        for part in message_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                        if str(part or "").strip()
                    ]
                    if not message_parts:
                        raise RuntimeError("私信内容不能为空")

                    opened, reason = await self._scraper.open_chat_page_conversation(self._page, row)
                    if not opened:
                        raise RuntimeError(reason or "未找到目标会话")
                    input_box = self._page.locator(
                        'div[data-e2e="msg-input"] [contenteditable="true"], .public-DraftEditor-content[contenteditable="true"]'
                    ).first
                    await input_box.wait_for(state="visible", timeout=15000)
                    send_button = self._page.locator(".e2e-send-msg-btn, [class*='send-msg-btn'], span.e2e-send-msg-btn").first
                    await send_button.wait_for(state="visible", timeout=15000)

                    expected_username = str(row.get("username", "") or row.get("conversation_key", "") or "-").strip()
                    sent_messages = []
                    total_parts = len(message_parts)
                    for index, message_part in enumerate(message_parts, start=1):
                        await input_box.click()
                        await self._page.keyboard.press("Control+A")
                        await self._page.keyboard.press("Backspace")
                        await self._page.keyboard.type(message_part, delay=35)
                        await send_button.click(timeout=10000)
                        await self._page.wait_for_timeout(1200)
                        delivered = await self._page.evaluate(
                            """
                            (sentText) => {
                                const needle = String(sentText || '').trim();
                                if (!needle) return false;
                                const texts = Array.from(document.querySelectorAll('[data-e2e="msg-item-content"]'))
                                    .map((node) => (node.textContent || '').trim())
                                    .filter(Boolean);
                                if (texts.some((text) => text.includes(needle))) return true;
                                const editor = document.querySelector(
                                    'div[data-e2e="msg-input"] [contenteditable="true"][role="textbox"]'
                                );
                                const editorText = (editor?.textContent || '').trim();
                                return editorText.length === 0;
                            }
                            """,
                            message_part,
                        )
                        if not delivered:
                            raise RuntimeError(f"第 {index} 条消息发送按钮已点击，但未确认消息已发出")
                        sent_messages.append(message_part)
                        douyin_log(
                            f"[抖音私信聚合] 已发送第 {index}/{total_parts} 条：{expected_username}",
                            "success",
                        )
                        if index < total_parts:
                            await self._page.wait_for_timeout(700)

                    await self._page.wait_for_timeout(1200)
                    detail = await self._scraper.extract_chat_page_conversation_detail(self._page)
                    self._last_used_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    result = {
                        "success": True,
                        "conversation_key": str(row.get("conversation_key", "") or row.get("conversation_id", "") or "").strip(),
                        "username": str(detail.get("username", "") or row.get("username", "") or "").strip(),
                        "avatar_url": str(detail.get("avatar_url", "") or row.get("avatar_url", "") or row.get("avatar", "") or "").strip(),
                        "profile_url": str(detail.get("profile_url", "") or row.get("profile_url", "") or "").strip(),
                        "message": message_text,
                        "messages_sent": sent_messages,
                        "message_count": len(sent_messages),
                        "detail": {
                            "conversation_key": str(row.get("conversation_key", "") or row.get("conversation_id", "") or "").strip(),
                            "username": str(detail.get("username", "") or row.get("username", "") or "").strip(),
                            "avatar_url": str(detail.get("avatar_url", "") or row.get("avatar_url", "") or row.get("avatar", "") or "").strip(),
                            "profile_url": str(detail.get("profile_url", "") or row.get("profile_url", "") or "").strip(),
                            "incoming_message": str(detail.get("incoming_message", "") or row.get("incoming_message", "") or row.get("preview_text", "") or "").strip(),
                            "reply_message": str(detail.get("reply_message", "") or "").strip(),
                            "messages": detail.get("messages", []),
                            "fetched_at": self._last_used_at,
                        },
                    }
                    self._last_used_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    return result
                except Exception as exc:
                    if attempt == 0 and self._is_retryable_runtime_error(exc):
                        await self._reset_runtime()
                        continue
                    raise


def get_douyin_inbox_page_worker(account_id: int) -> DouyinInboxPageWorker:
    target_account_id = int(account_id or 0)
    if target_account_id <= 0:
        raise ValueError("账号 ID 无效")
    worker = douyin_inbox_page_workers.get(target_account_id)
    if worker is None:
        worker = DouyinInboxPageWorker(target_account_id)
        douyin_inbox_page_workers[target_account_id] = worker
    return worker


def load_search_history() -> Dict:
    if not SEARCH_HISTORY_FILE.exists():
        return {"version": 1, "items": {}}
    try:
        with open(SEARCH_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"version": 1, "items": {}}
        items = data.get("items", {})
        if not isinstance(items, dict):
            items = {}
        return {"version": data.get("version", 1), "items": items}
    except Exception:
        return {"version": 1, "items": {}}


def save_search_history(history: Dict):
    payload = history if isinstance(history, dict) else {"version": 1, "items": {}}
    payload.setdefault("version", 1)
    payload.setdefault("items", {})
    with open(SEARCH_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def build_search_result_key(item: Dict) -> str:
    aweme_id = str(item.get("aweme_id", "") or "").strip()
    if aweme_id:
        return f"aweme:{aweme_id}"
    url = str(item.get("url", "") or "").strip().lower()
    if url:
        return f"url:{url}"
    title = " ".join(str(item.get("title", "") or "").lower().split())
    author = " ".join(str(item.get("author", "") or "").lower().split())
    if title or author:
        return f"text:{title}|{author}"
    return ""


def build_douyin_search_session_item_key(item: Dict) -> str:
    url = str(item.get("url", "") or item.get("task_url", "") or "").strip()
    match = re.search(r"/video/(\d+)", url)
    if match:
        return f"video:{match.group(1)}"
    if url:
        return f"url:{url}"
    title = normalize_douyin_text(item.get("title", "") or item.get("task_title", ""))
    author = normalize_douyin_text(item.get("author", "") or item.get("task_author", ""))
    return f"meta:{title}::{author}"


def normalize_douyin_search_mode(value: object) -> str:
    normalized = normalize_douyin_text(value).lower()
    return normalized if normalized in DOUYIN_SEARCH_MODES else "api"


def load_custom_configs() -> Dict:
    if not CUSTOM_CONFIGS_FILE.exists():
        return {"configs": {}, "custom_models": []}
    try:
        with open(CUSTOM_CONFIGS_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            return loaded
    except Exception:
        pass
    return {"configs": {}, "custom_models": []}


def get_tikhub_runtime_config() -> Dict[str, str]:
    custom_configs = load_custom_configs()
    configs = custom_configs.get("configs") if isinstance(custom_configs, dict) else {}
    if not isinstance(configs, dict):
        configs = {}
    api_key = str(
        os.environ.get("TIKHUB_API_KEY")
        or configs.get("TIKHUB_API_KEY")
        or configs.get("tikhub_api_key")
        or ""
    ).strip()
    api_base = str(
        os.environ.get("TIKHUB_API_BASE")
        or configs.get("TIKHUB_API_BASE")
        or configs.get("tikhub_api_base")
        or "https://api.tikhub.dev"
    ).strip().rstrip("/")
    return {
        "api_key": api_key,
        "api_base": api_base or "https://api.tikhub.dev",
    }


def format_douyin_compact_count(value: object) -> str:
    try:
        count = int(float(value or 0))
    except Exception:
        return ""
    if count <= 0:
        return ""
    if count >= 100000000:
        text = f"{count / 100000000:.1f}".rstrip("0").rstrip(".")
        return f"{text}亿"
    if count >= 10000:
        text = f"{count / 10000:.1f}".rstrip("0").rstrip(".")
        return f"{text}w"
    return str(count)


def format_douyin_duration_text(value: object) -> str:
    try:
        raw = int(value or 0)
    except Exception:
        return ""
    if raw <= 0:
        return ""
    seconds = raw // 1000 if raw > 1000 else raw
    minutes = seconds // 60
    remain = seconds % 60
    hours = minutes // 60
    if hours > 0:
        minutes = minutes % 60
        return f"{hours:02d}:{minutes:02d}:{remain:02d}"
    return f"{minutes:02d}:{remain:02d}"


def format_douyin_publish_time(value: object) -> str:
    try:
        ts = int(value or 0)
    except Exception:
        return ""
    if ts <= 0:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def pick_tikhub_douyin_cover(video: Dict) -> str:
    for key in ("cover", "dynamic_cover", "origin_cover"):
        node = video.get(key)
        if isinstance(node, dict):
            url_list = node.get("url_list")
            if isinstance(url_list, list):
                for url in url_list:
                    if isinstance(url, str) and url.strip():
                        return url.strip()
    return ""


def normalize_tikhub_douyin_search_results(payload: Dict, keyword: str, max_results: int) -> List[Dict]:
    rows: List[Dict] = []
    data = payload.get("data") or {}
    raw_items = []
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            raw_items = data.get("data") or []
        elif isinstance(data.get("business_data"), list):
            raw_items = data.get("business_data") or []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        aweme = raw.get("aweme_info")
        if not isinstance(aweme, dict):
            nested = raw.get("data")
            aweme = nested.get("aweme_info") if isinstance(nested, dict) else None
        if not isinstance(aweme, dict):
            continue
        share_info_probe = aweme.get("share_info") if isinstance(aweme.get("share_info"), dict) else {}
        share_url = str(share_info_probe.get("share_url") or aweme.get("share_url") or "").strip()
        if "/share/note/" in share_url:
            continue
        aweme_id = str(aweme.get("aweme_id") or "").strip()
        author = aweme.get("author") if isinstance(aweme.get("author"), dict) else {}
        stats = aweme.get("statistics") if isinstance(aweme.get("statistics"), dict) else {}
        video = aweme.get("video") if isinstance(aweme.get("video"), dict) else {}
        sec_uid = str(author.get("sec_uid") or "").strip()
        row = {
            "aweme_id": aweme_id,
            "title": str(aweme.get("desc") or "").strip() or (f"抖音视频 {aweme_id}" if aweme_id else ""),
            "url": f"https://www.douyin.com/video/{aweme_id}" if aweme_id else share_url,
            "author": str(author.get("nickname") or "").strip(),
            "profile_url": f"https://www.douyin.com/user/{sec_uid}" if sec_uid else "",
            "cover_image": pick_tikhub_douyin_cover(video),
            "likes": int(stats.get("digg_count") or 0),
            "comments": int(stats.get("comment_count") or 0),
            "likes_text": format_douyin_compact_count(stats.get("digg_count") or 0),
            "comments_text": format_douyin_compact_count(stats.get("comment_count") or 0),
            "duration": format_douyin_duration_text(aweme.get("duration")),
            "publish_time": format_douyin_publish_time(aweme.get("create_time")),
            "criteria_reason": "",
            "is_historical_duplicate": False,
            "export_selected": True,
            "source_session_id": "",
            "source_item_key": "",
            "keyword": keyword,
            "sec_user_id": sec_uid,
        }
        if not row["url"] or not row["title"]:
            continue
        rows.append(row)
        if len(rows) >= max_results:
            break
    return rows


def normalize_douyin_search_session_result(
    item: Dict,
    *,
    source_session_id: str = "",
    selected_item_keys: Optional[Set[str]] = None,
) -> Dict:
    normalized = {
        "title": str(item.get("title", "") or "").strip(),
        "url": str(item.get("url", "") or "").strip(),
        "author": str(item.get("author", "") or "").strip(),
        "cover_image": str(item.get("cover_image", "") or "").strip(),
        "likes": int(item.get("likes", 0) or 0),
        "comments": int(item.get("comments", 0) or 0),
        "duration": str(item.get("duration", "") or "").strip(),
        "publish_time": str(item.get("publish_time", "") or "").strip(),
        "criteria_reason": str(item.get("criteria_reason", "") or "").strip(),
        "is_historical_duplicate": bool(item.get("is_historical_duplicate")),
        "source_session_id": str(source_session_id or item.get("source_session_id", "") or "").strip(),
    }
    source_item_key = str(item.get("source_item_key", "") or "").strip() or build_douyin_search_session_item_key(normalized)
    normalized["source_item_key"] = source_item_key
    export_selected = bool(item.get("export_selected", True))
    if selected_item_keys is not None:
        export_selected = source_item_key in selected_item_keys
    normalized["export_selected"] = export_selected
    return normalized


def load_douyin_search_sessions_state() -> List[Dict]:
    payload = douyin_state_store.load_blob_json(DOUYIN_SEARCH_SESSIONS_BLOB_KEY, default=[])
    sessions = payload.get("sessions", []) if isinstance(payload, dict) else payload
    if not isinstance(sessions, list):
        return []
    return [dict(item) for item in sessions if isinstance(item, dict)]


def load_douyin_search_sessions_saved_at() -> int:
    payload = douyin_state_store.load_blob_json(DOUYIN_SEARCH_SESSIONS_BLOB_KEY, default=[])
    if isinstance(payload, dict):
        return int(payload.get("saved_at", 0) or 0)
    return 0


def save_douyin_search_sessions_state(sessions: List[Dict], *, saved_at: int = 0) -> None:
    effective_saved_at = int(saved_at or int(datetime.now().timestamp() * 1000))
    douyin_state_store.save_blob_json(
        DOUYIN_SEARCH_SESSIONS_BLOB_KEY,
        {"saved_at": effective_saved_at, "sessions": sessions or []},
    )


def upsert_douyin_search_session_state(
    *,
    keyword: str,
    account_id: object,
    results: List[Dict],
    capture_state: Optional[Dict] = None,
    session_id: str = "",
    active_tab: str = "videos",
) -> Dict:
    sessions = load_douyin_search_sessions_state()
    keyword_text = str(keyword or "").strip() or "未命名关键词"
    account_text = str(account_id or "").strip()
    existing_index = -1
    for index, session in enumerate(sessions):
        if session_id and str(session.get("id", "") or "").strip() == session_id:
            existing_index = index
            break
        if (
            not session_id
            and str(session.get("keyword", "") or "").strip().lower() == keyword_text.lower()
            and str(session.get("account_id", "") or "").strip() == account_text
        ):
            existing_index = index
            break

    now_ms = int(datetime.now().timestamp() * 1000)
    existing = sessions[existing_index] if existing_index >= 0 else {}
    final_session_id = str(existing.get("id", "") or session_id or f"dy-search-{now_ms}-{random.randint(1000, 9999)}").strip()
    selected_item_keys = {
        str(item.get("source_item_key", "") or "").strip()
        for item in results or []
        if isinstance(item, dict) and bool(item.get("export_selected", True))
    }
    normalized_results = [
        normalize_douyin_search_session_result(
            item,
            source_session_id=final_session_id,
            selected_item_keys=selected_item_keys,
        )
        for item in (results or [])
        if isinstance(item, dict)
    ]
    selected_key = str(existing.get("selected_item_key", "") or "").strip()
    valid_keys = {str(item.get("source_item_key", "") or "").strip() for item in normalized_results}
    if selected_key not in valid_keys:
        selected_key = normalized_results[0].get("source_item_key", "") if normalized_results else ""
    next_session = {
        **existing,
        "id": final_session_id,
        "keyword": keyword_text,
        "account_id": account_text,
        "created_at": int(existing.get("created_at", now_ms) or now_ms),
        "updated_at": now_ms,
        "active_tab": active_tab if active_tab in {"videos", "all", "precise"} else str(existing.get("active_tab", "videos") or "videos"),
        "selected_item_key": selected_key,
        "capture_state": capture_state if isinstance(capture_state, dict) else existing.get("capture_state", {}),
        "results": normalized_results,
    }
    if existing_index >= 0:
        sessions[existing_index] = next_session
    else:
        sessions.insert(0, next_session)
    save_douyin_search_sessions_state(sessions[:30])
    return next_session


def annotate_search_duplicates(results: List[Dict], keyword: str) -> List[Dict]:
    if not results:
        return []

    history = load_search_history()
    items = history.setdefault("items", {})
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    normalized_keyword = " ".join(str(keyword or "").split())

    annotated = []
    for raw_item in results:
        item = dict(raw_item)
        key = build_search_result_key(item)
        previous = items.get(key) if key else None
        previous_seen_count = int(previous.get("seen_count", 0) or 0) if previous else 0
        previous_keywords = list(previous.get("keywords", [])) if previous else []
        keywords = [kw for kw in previous_keywords if kw]
        if normalized_keyword and normalized_keyword not in keywords:
            keywords.append(normalized_keyword)

        if previous:
            first_seen_at = previous.get("first_seen_at") or now_text
            last_seen_at = previous.get("last_seen_at") or first_seen_at
            item["is_historical_duplicate"] = True
            item["first_seen_at"] = first_seen_at
            item["last_seen_at"] = last_seen_at
            item["seen_count"] = previous_seen_count + 1
            item["historical_duplicate_reason"] = f"首次发现于 {first_seen_at}，此前已出现 {previous_seen_count} 次"
        else:
            item["is_historical_duplicate"] = False
            item["first_seen_at"] = now_text
            item["last_seen_at"] = ""
            item["seen_count"] = 1
            item["historical_duplicate_reason"] = ""

        item["search_history_key"] = key
        if key:
            items[key] = {
                "first_seen_at": item["first_seen_at"],
                "last_seen_at": now_text,
                "seen_count": item["seen_count"],
                "keywords": keywords,
                "last_title": item.get("title", ""),
                "last_author": item.get("author", ""),
                "last_url": item.get("url", ""),
            }
        annotated.append(item)

    save_search_history(history)
    return annotated


def mark_search_results_for_export(results: List[Dict]) -> List[Dict]:
    if not results:
        return []

    config = load_global_config()
    normalized = []
    for item in results:
        row = dict(item)
        row["likes"] = int(row.get("likes", 0) or 0)
        normalized.append(row)

    average_likes = sum(row["likes"] for row in normalized) / len(normalized)
    min_likes = max(0, int(config.get("search_min_likes", 500) or 0))
    avg_multiplier = max(0, float(config.get("search_avg_multiplier", 1.5) or 0))
    boosted_threshold = average_likes * avg_multiplier if avg_multiplier > 0 else 0
    exclude_duplicates = bool(config.get("search_exclude_historical_duplicates", True))

    for item in normalized:
        likes = item["likes"]
        meets_floor = min_likes <= 0 or likes >= min_likes
        beats_average = avg_multiplier <= 0 or likes >= boosted_threshold
        matched = meets_floor or beats_average

        reasons = []
        if min_likes > 0 and likes >= min_likes:
            reasons.append(f"点赞至少 {min_likes}")
        if avg_multiplier > 0 and likes >= boosted_threshold:
            reasons.append(f"点赞达到平均值的 {avg_multiplier:g} 倍以上（均值 {average_likes:.0f}）")
        if item.get("is_historical_duplicate"):
            reasons.append("历史重复")
            if item.get("historical_duplicate_reason"):
                reasons.append(item["historical_duplicate_reason"])

        item["avg_likes"] = round(average_likes, 2)
        item["criteria_matched"] = matched
        item["criteria_reason"] = " / ".join(reasons)
        item["export_selected"] = matched and not (exclude_duplicates and item.get("is_historical_duplicate"))

    normalized.sort(key=lambda row: row.get("likes", 0), reverse=True)
    return normalized


async def run_douyin_keyword_search(
    account: Dict,
    keyword: str,
    *,
    max_results: int = 50,
    update_latest: bool = False,
) -> Dict:
    keyword_text = normalize_douyin_text(keyword)
    if not keyword_text:
        raise RuntimeError("请输入抖音搜索关键词")
    result_limit = max(10, min(int(max_results or 50), 100))
    search_url = f"https://www.douyin.com/search/{requests.utils.quote(keyword_text)}?type=video"
    browser_result = await ensure_douyin_account_browser_ready_async(account, start_url=search_url)
    if int(browser_result.get("code", 0) or 0) != 200:
        raise RuntimeError(str(browser_result.get("msg", "") or "抖音搜索浏览器启动失败"))

    scraper = DouyinCommentScraper(account_id=account["id"], cdp_port=account["port"])
    douyin_log(f"[抖音搜索] 开始搜索关键词：{keyword_text}", "info")
    try:
        results = await scraper.scrape_search_results(keyword_text, max_results=result_limit, logger=douyin_log)
    finally:
        await scraper.close()

    results = annotate_search_duplicates(results, keyword_text)
    results = mark_search_results_for_export(results)
    session = upsert_douyin_search_session_state(
        keyword=keyword_text,
        account_id=account.get("id", ""),
        results=results,
        capture_state={
            "source": "monitor_ai_expand",
            "searched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "max_results": result_limit,
        },
    )
    if update_latest:
        douyin_search_cache["latest"] = results
    douyin_log(f"[抖音搜索] 搜索完成：{keyword_text}，共 {len(results)} 条", "success")
    return {
        "keyword": keyword_text,
        "results": results,
        "total": len(results),
        "session": session,
    }


async def run_douyin_keyword_search_via_api(
    keyword: str,
    *,
    max_results: int = 50,
    account_id: object = "api",
) -> Dict:
    keyword_text = normalize_douyin_text(keyword)
    if not keyword_text:
        raise RuntimeError("请输入抖音搜索关键词")
    result_limit = max(10, min(int(max_results or 50), 100))
    tikhub_config = get_tikhub_runtime_config()
    api_key = tikhub_config["api_key"]
    api_base = tikhub_config["api_base"]
    if not api_key:
        raise RuntimeError("未配置 TIKHUB_API_KEY")
    payload = {
        "keyword": keyword_text,
        "offset": "0",
        "count": str(min(result_limit, 30)),
        "sort_type": "0",
        "publish_time": "0",
        "filter_duration": "0",
    }
    response = requests.post(
        f"{api_base}/api/v1/douyin/search/fetch_general_search_v1",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    if int(data.get("code") or 0) != 200:
        raise RuntimeError(str(data.get("message_zh") or data.get("message") or "接口搜索失败"))
    results = normalize_tikhub_douyin_search_results(data, keyword_text, result_limit)
    results = annotate_search_duplicates(results, keyword_text)
    results = mark_search_results_for_export(results)
    session = upsert_douyin_search_session_state(
        keyword=keyword_text,
        account_id=account_id,
        results=results,
        capture_state={
            "source": "tikhub_api",
            "searched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "max_results": result_limit,
            "request_id": str(data.get("request_id") or "").strip(),
            "cache_url": str(data.get("cache_url") or "").strip(),
        },
    )
    douyin_search_cache["latest"] = results
    douyin_log(f"[抖音搜索] 接口搜索完成：{keyword_text}，共 {len(results)} 条", "success")
    return {
        "keyword": keyword_text,
        "results": results,
        "total": len(results),
        "session": session,
        "request_id": str(data.get("request_id") or "").strip(),
        "cache_url": str(data.get("cache_url") or "").strip(),
    }


def user_choice_key(row: Dict) -> str:
    comment_id = " ".join(str(row.get("comment_id", "") or "").split())
    if comment_id:
        return f"id:{comment_id}"
    return "|".join(
        [
            " ".join(str(row.get("user_id", "") or "").split()),
            " ".join(str(row.get("username", "") or "").split()),
            " ".join(str(row.get("comment", row.get("content", "")) or "").split()),
            " ".join(str(row.get("comment_time", "") or "").split()),
        ]
    )


def user_choice_loose_key(row: Dict) -> str:
    return "|".join(
        [
            " ".join(str(row.get("user_id", "") or "").split()),
            " ".join(str(row.get("username", "") or "").split()),
            " ".join(str(row.get("comment", row.get("content", "")) or "").split()),
        ]
    )


def mention_comment_identity_key(row: Dict) -> str:
    if not isinstance(row, dict):
        return ""
    sec_user_id = normalize_douyin_text(row.get("sec_user_id", ""))
    if sec_user_id:
        return f"sec:{sec_user_id}"
    profile_url = normalize_douyin_text(ensure_douyin_profile_url(row))
    if profile_url:
        return f"profile:{profile_url}"
    douyin_id = normalize_douyin_text(row.get("douyin_id", ""))
    if douyin_id:
        return f"douyin:{douyin_id}"
    user_id = normalize_douyin_text(row.get("user_id", ""))
    if user_id:
        return f"user:{user_id}"
    username = normalize_douyin_text(row.get("username", ""))
    if username:
        return f"name:{username.lower()}"
    return ""


def get_douyin_mention_comment_history_for_row(row: Dict) -> Dict[str, object]:
    key = mention_comment_identity_key(row if isinstance(row, dict) else {})
    if not key:
        return {}
    history = douyin_mention_comment_history.get(key, {})
    return history if isinstance(history, dict) else {}


def dedupe_users(rows: List[Dict]) -> List[Dict]:
    seen = set()
    deduped = []
    for row in rows or []:
        key = user_choice_key(row)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def set_tasks_from_rows(rows: List[Dict]) -> List[Dict]:
    global douyin_tasks

    def build_preserve_key(row: Dict) -> str:
        session_id = str(row.get("source_session_id", "") or "").strip()
        item_key = str(row.get("source_item_key", "") or "").strip()
        if session_id and item_key:
            return f"session:{session_id}|item:{item_key}"
        url = str(row.get("url", "") or "").strip()
        title = str(row.get("title", "") or "").strip()
        author = str(row.get("author", "") or "").strip()
        return f"url:{url}|title:{title}|author:{author}"

    existing_lookup: Dict[str, Dict] = {}
    used_ids = set()
    next_id = 1
    for task in douyin_tasks or []:
        if not isinstance(task, dict):
            continue
        normalized_task = ensure_douyin_task_shape(task)
        existing_lookup[build_preserve_key(normalized_task)] = normalized_task
        task_id = int(normalized_task.get("id", 0) or 0)
        if task_id > 0:
            used_ids.add(task_id)
            next_id = max(next_id, task_id + 1)

    tasks = []
    selected_keys = set()
    for row in rows:
        url = str(row.get("url", "")).strip()
        if not url:
            continue
        row_payload = {
            "platform": "douyin",
            "url": url,
            "title": str(row.get("title", "")).strip(),
            "author": str(row.get("author", "")).strip(),
            "cover_image": str(row.get("cover_image", "")).strip(),
            "source_session_id": str(row.get("source_session_id", "")).strip(),
            "source_item_key": str(row.get("source_item_key", "")).strip(),
            "source_keyword": str(row.get("source_keyword", "")).strip(),
            "likes": int(row.get("likes", 0) or 0),
            "source_comment_count": int(row.get("comments", row.get("source_comment_count", 0)) or 0),
            "source_comment_count_text": str(
                row.get("comments_text", row.get("source_comment_count_text", ""))
                or ""
            ).strip(),
            "publish_time": str(row.get("publish_time", "")).strip(),
        }
        preserve_key = build_preserve_key(row_payload)
        if preserve_key in selected_keys:
            continue
        selected_keys.add(preserve_key)
        preserved = existing_lookup.get(preserve_key)
        if preserved:
            merged = ensure_douyin_task_shape(
                {
                    **preserved,
                    **row_payload,
                    "id": int(preserved.get("id", 0) or 0),
                }
            )
        else:
            while next_id in used_ids:
                next_id += 1
            merged = ensure_douyin_task_shape(
                {
                    "id": next_id,
                    **row_payload,
                    "all_comments": [],
                    "high_intent_users": [],
                    "comment_count": 0,
                    "capture_comment_limit": 0,
                    "capture_target_comments": 0,
                    "collect_progress": {},
                    "status": "pending",
                    "error": "",
                    "video_comment_status": "pending",
                    "video_comment_error": "",
                    "video_comment_mode": "fixed",
                    "video_comment_prompt": "",
                    "video_comment_seed_text": "",
                    "video_comment_summary": "",
                    "video_comment_text": "",
                    "video_comment_account_id": "",
                    "video_comment_started_at": "",
                    "video_comment_finished_at": "",
                }
            )
            used_ids.add(next_id)
            next_id += 1
        tasks.append(merged)

    # Keep unrelated historical tasks so switching keywords or re-running "沿用搜索勾选"
    # does not wipe previously collected comment users and precise customers.
    for task in douyin_tasks or []:
        if not isinstance(task, dict):
            continue
        normalized_task = ensure_douyin_task_shape(task)
        preserve_key = build_preserve_key(normalized_task)
        if preserve_key in selected_keys:
            continue
        tasks.append(normalized_task)

    douyin_tasks = tasks
    save_douyin_tasks_state()
    return douyin_tasks


def normalize_high_intent_user(row: Dict) -> Dict:
    normalized = {
        "comment_index": row.get("comment_index", ""),
        "username": row.get("username", ""),
        "user_id": row.get("user_id", ""),
        "user_xsec_token": row.get("user_xsec_token", ""),
        "comment_id": row.get("comment_id", ""),
        "comment": row.get("comment", row.get("content", "")),
        "content": row.get("comment", row.get("content", "")),
        "comment_time": row.get("comment_time", ""),
        "avatar": row.get("avatar", row.get("avatar_url", "")),
        "avatar_url": row.get("avatar_url", row.get("avatar", "")),
        "like_count": row.get("like_count", ""),
        "reply_count": row.get("reply_count", ""),
        "profile_url": row.get("profile_url", ""),
        "intent_level": row.get("intent_level", "manual"),
        "score": row.get("score", ""),
        "reason": row.get("reason", "手动勾选"),
        "interaction_status": row.get("interaction_status", "pending"),
        "interaction_error": row.get("interaction_error", ""),
        "interaction_message": row.get("interaction_message", ""),
        "interaction_account_id": row.get("interaction_account_id", ""),
        "interaction_started_at": row.get("interaction_started_at", ""),
        "interaction_finished_at": row.get("interaction_finished_at", ""),
        "interaction_updated_at": row.get("interaction_updated_at", ""),
        "follow_comment_status": row.get("follow_comment_status", "pending"),
        "follow_comment_error": row.get("follow_comment_error", ""),
        "follow_comment_text": row.get("follow_comment_text", ""),
        "follow_comment_account_id": row.get("follow_comment_account_id", ""),
        "follow_comment_result": row.get("follow_comment_result", ""),
        "follow_comment_started_at": row.get("follow_comment_started_at", ""),
        "follow_comment_finished_at": row.get("follow_comment_finished_at", ""),
        "follow_comment_updated_at": row.get("follow_comment_updated_at", ""),
        "mention_comment_status": row.get("mention_comment_status", "pending"),
        "mention_comment_error": row.get("mention_comment_error", ""),
        "mention_comment_text": row.get("mention_comment_text", ""),
        "mention_comment_account_id": row.get("mention_comment_account_id", ""),
        "mention_comment_result": row.get("mention_comment_result", ""),
        "mention_comment_video_url": row.get("mention_comment_video_url", ""),
        "mention_comment_video_title": row.get("mention_comment_video_title", ""),
        "mention_comment_started_at": row.get("mention_comment_started_at", ""),
        "mention_comment_finished_at": row.get("mention_comment_finished_at", ""),
        "mention_comment_updated_at": row.get("mention_comment_updated_at", ""),
        "group_name": row.get("group_name", ""),
        "role": row.get("role", ""),
        "douyin_id": row.get("douyin_id", ""),
        "sec_user_id": row.get("sec_user_id", ""),
        "region": row.get("region", ""),
        "source_session_id": row.get("source_session_id", ""),
        "source_item_key": row.get("source_item_key", ""),
        "source_keyword": row.get("source_keyword", ""),
        "source": row.get("source", ""),
        "is_high_intent": bool(row.get("is_high_intent", False)),
        "task_title": row.get("task_title", ""),
        "task_url": row.get("task_url", ""),
        "task_author": row.get("task_author", ""),
        "task_id": int(row.get("task_id", 0) or 0),
        "target_id": int(row.get("target_id", row.get("monitor_target_id", 0)) or 0),
        "target_username": row.get("target_username", row.get("monitor_target_username", "")),
        "target_profile_url": row.get("target_profile_url", row.get("monitor_target_profile_url", "")),
        "target_avatar_url": row.get("target_avatar_url", row.get("monitor_target_avatar_url", "")),
        "monitor_target_id": int(row.get("monitor_target_id", row.get("target_id", 0)) or 0),
        "monitor_target_username": row.get("monitor_target_username", row.get("target_username", "")),
        "monitor_target_profile_url": row.get("monitor_target_profile_url", row.get("target_profile_url", "")),
        "monitor_target_avatar_url": row.get("monitor_target_avatar_url", row.get("target_avatar_url", "")),
        "aweme_id": row.get("aweme_id", ""),
        "comment_key": row.get("comment_key", ""),
        "user_key": row.get("user_key", ""),
        "latest_video_title": row.get("latest_video_title", row.get("video_title", row.get("task_title", ""))),
        "latest_video_url": row.get("latest_video_url", row.get("video_url", row.get("task_url", ""))),
        "video_title": row.get("video_title", row.get("latest_video_title", "")),
        "video_url": row.get("video_url", row.get("latest_video_url", "")),
        "video_cover_image": row.get("video_cover_image", row.get("cover_image", "")),
        "cover_image": row.get("cover_image", row.get("video_cover_image", "")),
        "first_seen_at": row.get("first_seen_at", ""),
        "last_seen_at": row.get("last_seen_at", ""),
        "seen_count": row.get("seen_count", ""),
        "seen_count_total": row.get("seen_count_total", ""),
        "comment_count": row.get("comment_count", ""),
        "source_video_count": row.get("source_video_count", ""),
    }
    history = get_douyin_mention_comment_history_for_row(normalized)
    if history and str(normalized.get("mention_comment_status", "pending") or "pending").lower() in {"", "pending", "queued"}:
        normalized["mention_comment_status"] = str(history.get("status", "completed") or "completed")
        normalized["mention_comment_error"] = str(history.get("error", "") or "")
        normalized["mention_comment_text"] = str(history.get("comment_text", "") or "")
        normalized["mention_comment_account_id"] = str(history.get("account_id", "") or "")
        normalized["mention_comment_result"] = str(history.get("result", "") or "")
        normalized["mention_comment_video_url"] = str(history.get("video_url", "") or "")
        normalized["mention_comment_video_title"] = str(history.get("video_title", "") or "")
        normalized["mention_comment_started_at"] = str(history.get("started_at", "") or "")
        normalized["mention_comment_finished_at"] = str(history.get("finished_at", "") or "")
        normalized["mention_comment_updated_at"] = str(history.get("updated_at", "") or "")
    normalized["mention_comment_identity_key"] = mention_comment_identity_key(normalized)
    normalized["mention_comment_is_historical"] = bool(history)
    return normalized


def normalize_douyin_collection_mode(value: Optional[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "script":
        return "script"
    return "protocol"


def douyin_collection_mode_label(value: Optional[str]) -> str:
    return "脚本模式" if normalize_douyin_collection_mode(value) == "script" else "协议模式"


def normalize_douyin_protocol_comment_row(row: Dict, task: Optional[Dict] = None, index: int = 0) -> Dict:
    payload = dict(row or {}) if isinstance(row, dict) else {}
    task_payload = task if isinstance(task, dict) else {}
    sec_user_id = str(payload.get("sec_uid", "") or payload.get("user_id", "") or "").strip()
    profile_url = str(payload.get("profile_url", "") or "").strip()
    if not profile_url and sec_user_id:
        profile_url = f"https://www.douyin.com/user/{sec_user_id}"
    created_ts = int(payload.get("create_time", 0) or 0)
    comment_time = ""
    if created_ts > 0:
        try:
            comment_time = datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            comment_time = str(created_ts)
    normalized = normalize_high_intent_user(
        {
            "comment_index": index + 1,
            "comment_id": str(payload.get("comment_id", "") or "").strip(),
            "username": str(payload.get("nickname", "") or "").strip(),
            "user_id": sec_user_id,
            "sec_user_id": sec_user_id,
            "comment": str(payload.get("text", "") or "").strip(),
            "content": str(payload.get("text", "") or "").strip(),
            "comment_time": comment_time,
            "avatar": str(payload.get("avatar_url", "") or "").strip(),
            "avatar_url": str(payload.get("avatar_url", "") or "").strip(),
            "like_count": int(payload.get("digg_count", 0) or 0),
            "reply_count": int(payload.get("reply_comment_total", 0) or 0),
            "profile_url": profile_url,
            "region": "",
            "source_session_id": str(task_payload.get("source_session_id", "") or "").strip(),
            "source_item_key": str(task_payload.get("source_item_key", "") or "").strip(),
            "source_keyword": str(task_payload.get("source_keyword", "") or "").strip(),
            "task_title": str(task_payload.get("title", "") or "").strip(),
            "task_url": str(task_payload.get("url", "") or "").strip(),
            "task_author": str(task_payload.get("author", "") or task_payload.get("task_author", "") or "").strip(),
        }
    )
    normalized["create_time"] = created_ts
    normalized["aweme_id"] = str(payload.get("aweme_id", task_payload.get("aweme_id", "")) or "").strip()
    return normalized


def ensure_douyin_profile_url(row: Dict) -> str:
    profile_url = str(row.get("profile_url", "") or "").strip()
    if profile_url:
        return profile_url
    sec_user_id = str(row.get("sec_user_id", "") or row.get("user_id", "") or "").strip()
    if sec_user_id:
        return f"https://www.douyin.com/user/{sec_user_id}"
    return ""


def extract_url_from_share_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = DOUYIN_SHARE_URL_PATTERN.search(text)
    if not match:
        return text if text.startswith(("http://", "https://")) else ""
    url = str(match.group(0) or "").strip()
    return url.rstrip(".,;!?)】）>\"'，。；！？、")


def resolve_douyin_share_url(value: object) -> str:
    raw_url = extract_url_from_share_text(value)
    if not raw_url:
        return ""

    normalized = raw_url
    try:
        response = requests.get(
            raw_url,
            allow_redirects=True,
            timeout=15,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                )
            },
        )
        final_url = str(getattr(response, "url", "") or "").strip()
        if final_url:
            normalized = final_url
    except Exception:
        pass

    normalized = normalized.split("#", 1)[0].strip()
    return normalized


def is_douyin_video_url(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(re.search(r"/(?:video|note)/\d+", text) or re.search(r"[?&]modal_id=\d+", text))


def normalize_douyin_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def is_douyin_group_like_stranger_message_row(row: Dict) -> bool:
    if not isinstance(row, dict):
        return False
    normalized = {
        "username": normalize_douyin_text(row.get("username", "")),
        "preview_text": normalize_douyin_text(
            row.get("preview_text", "")
            or row.get("incoming_message", "")
            or row.get("content", "")
        ),
    }
    username = normalized["username"]
    preview_text = normalized["preview_text"]
    if not username and not preview_text:
        return False
    if DOUYIN_GROUP_NAME_PATTERN.search(username):
        return True
    if DOUYIN_GROUP_PREVIEW_PATTERN.search(preview_text):
        return True
    if DOUYIN_GROUP_SENDER_PREFIX_PATTERN.search(preview_text):
        return True
    return False


def normalize_douyin_inbox_row(row: Dict) -> Dict:
    normalized = normalize_douyin_stranger_message_row(row if isinstance(row, dict) else {})
    normalized["conversation_source"] = normalize_douyin_text(
        row.get("conversation_source", "") or "chat_inbox"
    )
    messages = row.get("messages", [])
    if isinstance(messages, list):
        normalized["messages"] = [
            {
                "direction": "outgoing"
                if normalize_douyin_text((item or {}).get("direction", "")) == "outgoing"
                else "incoming",
                "text": normalize_douyin_text((item or {}).get("text", "")),
                "time_text": normalize_douyin_text((item or {}).get("time_text", "")),
                "label": normalize_douyin_text((item or {}).get("label", "")),
            }
            for item in messages
            if isinstance(item, dict) and normalize_douyin_text(item.get("text", ""))
        ]
    else:
        normalized["messages"] = []
    normalized["fetched_at"] = normalize_douyin_text(row.get("fetched_at", ""))
    normalized["is_partial"] = bool(row.get("is_partial", False))
    return normalized


def build_douyin_inbox_fallback_detail(row: Dict) -> Dict:
    normalized = normalize_douyin_inbox_row(row if isinstance(row, dict) else {})
    messages: List[Dict[str, str]] = []
    incoming_text = normalize_douyin_text(
        normalized.get("incoming_message", "") or normalized.get("preview_text", "")
    )
    incoming_time = normalize_douyin_text(
        normalized.get("time_text", "") or normalized.get("updated_at", "") or normalized.get("created_at", "")
    )
    if incoming_text:
        messages.append(
            {
                "direction": "incoming",
                "text": incoming_text,
                "time_text": incoming_time or "最近",
                "label": normalize_douyin_text(normalized.get("username", "")) or "对方",
            }
        )
    reply_text = normalize_douyin_text(normalized.get("reply_message", ""))
    reply_time = normalize_douyin_text(
        normalized.get("reply_finished_at", "")
        or normalized.get("reply_updated_at", "")
        or normalized.get("reply_started_at", "")
    )
    if reply_text:
        messages.append(
            {
                "direction": "outgoing",
                "text": reply_text,
                "time_text": reply_time or incoming_time or "最近",
                "label": "我",
            }
        )
    return {
        "conversation_key": normalize_douyin_text(
            normalized.get("conversation_key", "") or normalized.get("conversation_id", "")
        ),
        "username": normalize_douyin_text(normalized.get("username", "")),
        "avatar_url": str(normalized.get("avatar_url", "") or normalized.get("avatar", "") or "").strip(),
        "profile_url": str(normalized.get("profile_url", "") or "").strip(),
        "incoming_message": incoming_text,
        "reply_message": reply_text,
        "messages": messages,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "is_partial": True,
    }


def normalize_douyin_inbox_monitor_state(
    payload: Optional[Dict[str, object]] = None,
    *,
    account_id: int = 0,
) -> Dict[str, object]:
    source = payload if isinstance(payload, dict) else {}
    base = {
        "enabled": False,
        "running": False,
        "interval_minutes": 30,
        "account_id": int(account_id or source.get("account_id", 0) or 0) or None,
        "max_conversations": 100,
        "message": "",
        "last_run_at": "",
        "next_run_at": "",
        "last_error": "",
        "last_skip_reason": "",
        "last_cycle_status": "idle",
        "last_total_count": 0,
        "last_unread_count": 0,
        "auto_reply_enabled": False,
        "reply_mode": "fixed",
        "reply_message": "",
        "reply_prompt": "",
        "contact_value": "",
        "last_auto_reply_total": 0,
        "last_auto_reply_success": 0,
        "last_auto_reply_failed": 0,
    }
    base.update(
        {
            "enabled": bool(source.get("enabled", base["enabled"])),
            "running": bool(source.get("running", base["running"])),
            "interval_minutes": max(1, min(int(source.get("interval_minutes", base["interval_minutes"]) or 30), 1440)),
            "account_id": int(source.get("account_id", base["account_id"]) or base["account_id"] or 0) or None,
            "max_conversations": max(1, min(int(source.get("max_conversations", base["max_conversations"]) or 100), 100)),
            "message": normalize_douyin_text(source.get("message", base["message"])),
            "last_run_at": normalize_douyin_text(source.get("last_run_at", base["last_run_at"])),
            "next_run_at": normalize_douyin_text(source.get("next_run_at", base["next_run_at"])),
            "last_error": normalize_douyin_text(source.get("last_error", base["last_error"])),
            "last_skip_reason": normalize_douyin_text(source.get("last_skip_reason", base["last_skip_reason"])),
            "last_cycle_status": normalize_douyin_text(source.get("last_cycle_status", base["last_cycle_status"])) or "idle",
            "last_total_count": max(0, int(source.get("last_total_count", base["last_total_count"]) or 0)),
            "last_unread_count": max(0, int(source.get("last_unread_count", base["last_unread_count"]) or 0)),
            "auto_reply_enabled": bool(source.get("auto_reply_enabled", base["auto_reply_enabled"])),
            "reply_mode": normalize_douyin_inbox_reply_mode(source.get("reply_mode", base["reply_mode"])),
            "reply_message": str(source.get("reply_message", base["reply_message"]) or "").strip(),
            "reply_prompt": str(source.get("reply_prompt", base["reply_prompt"]) or "").strip(),
            "contact_value": str(source.get("contact_value", base["contact_value"]) or "").strip(),
            "last_auto_reply_total": max(0, int(source.get("last_auto_reply_total", base["last_auto_reply_total"]) or 0)),
            "last_auto_reply_success": max(0, int(source.get("last_auto_reply_success", base["last_auto_reply_success"]) or 0)),
            "last_auto_reply_failed": max(0, int(source.get("last_auto_reply_failed", base["last_auto_reply_failed"]) or 0)),
        }
    )
    return base


def get_douyin_inbox_monitor_state(account_id: int, create: bool = False) -> Dict[str, object]:
    key = str(int(account_id or 0) or 0)
    if key == "0":
        return normalize_douyin_inbox_monitor_state({})
    existing = douyin_inbox_monitor_states.get(key)
    if existing is None and create:
        existing = normalize_douyin_inbox_monitor_state({}, account_id=int(key))
        douyin_inbox_monitor_states[key] = existing
    return existing or normalize_douyin_inbox_monitor_state({}, account_id=int(key))


def list_douyin_inbox_monitor_states() -> List[Dict[str, object]]:
    states: List[Dict[str, object]] = []
    for key, raw_state in douyin_inbox_monitor_states.items():
        account_id = int(key or 0) or 0
        if account_id <= 0:
            continue
        states.append(normalize_douyin_inbox_monitor_state(raw_state, account_id=account_id))
    states.sort(key=lambda item: int(item.get("account_id", 0) or 0))
    return states


def group_member_row_key(row: Dict) -> str:
    if not isinstance(row, dict):
        return ""
    return "|".join(
        [
            normalize_douyin_text(row.get("group_name", "")),
            normalize_douyin_text(row.get("username", "")),
            normalize_douyin_text(ensure_douyin_profile_url(row)),
            normalize_douyin_text(row.get("sec_user_id", "")),
            normalize_douyin_text(row.get("douyin_id", "")),
        ]
    )


def normalize_douyin_stranger_message_row(row: Dict) -> Dict:
    normalized = {
        "conversation_key": normalize_douyin_text(
            row.get("conversation_key", "") or row.get("conversation_id", "")
        ),
        "conversation_id": normalize_douyin_text(row.get("conversation_id", "")),
        "username": normalize_douyin_text(row.get("username", "")),
        "avatar": str(row.get("avatar", row.get("avatar_url", "")) or "").strip(),
        "avatar_url": str(row.get("avatar_url", row.get("avatar", "")) or "").strip(),
        "preview_text": normalize_douyin_text(
            row.get("preview_text", "")
            or row.get("incoming_message", "")
            or row.get("content", "")
        ),
        "incoming_message": normalize_douyin_text(
            row.get("incoming_message", "")
            or row.get("preview_text", "")
            or row.get("content", "")
        ),
        "profile_url": str(row.get("profile_url", "") or "").strip(),
        "sec_user_id": normalize_douyin_text(
            row.get("sec_user_id", "") or row.get("user_id", "")
        ),
        "account_id": int(row.get("account_id", 0) or 0),
        "unread_count": max(0, int(row.get("unread_count", 0) or 0)),
        "is_unread": bool(row.get("is_unread", False)),
        "time_text": normalize_douyin_text(row.get("time_text", "")),
        "stranger_index": max(0, int(row.get("stranger_index", 0) or 0)),
        "conversation_source": normalize_douyin_text(
            row.get("conversation_source", "") or "stranger_messages"
        ),
        "collected_at": normalize_douyin_text(row.get("collected_at", "")),
        "reply_status": normalize_douyin_text(row.get("reply_status", "pending")).lower() or "pending",
        "reply_error": normalize_douyin_text(row.get("reply_error", "")),
        "reply_message": normalize_douyin_text(row.get("reply_message", "")),
        "reply_account_id": str(row.get("reply_account_id", "") or "").strip(),
        "reply_started_at": normalize_douyin_text(row.get("reply_started_at", "")),
        "reply_finished_at": normalize_douyin_text(row.get("reply_finished_at", "")),
        "reply_updated_at": normalize_douyin_text(row.get("reply_updated_at", "")),
    }
    normalized["profile_url"] = ensure_douyin_profile_url(normalized)
    if not normalized["conversation_key"]:
        normalized["conversation_key"] = normalize_douyin_text(
            normalized["conversation_id"]
            or normalized["profile_url"]
            or normalized["sec_user_id"]
            or normalized["username"]
        )
    return normalized


def stranger_message_row_key(row: Dict) -> str:
    if not isinstance(row, dict):
        return ""
    normalized = normalize_douyin_stranger_message_row(row)
    conversation_key = normalize_douyin_text(normalized.get("conversation_key", ""))
    placeholder_conversation_key = conversation_key.startswith("index:")
    if conversation_key and not placeholder_conversation_key:
        return f"conversation:{normalize_douyin_text(normalized['conversation_key'])}"
    if normalized["profile_url"]:
        return f"profile:{normalize_douyin_text(normalized['profile_url'])}"
    if normalized["sec_user_id"]:
        return f"sec:{normalize_douyin_text(normalized['sec_user_id'])}"
    username = normalize_douyin_text(normalized.get("username", ""))
    account_id = int(normalized.get("account_id", 0) or 0)
    if username and account_id > 0:
        return f"account:{account_id}|user:{username}"
    if conversation_key:
        return f"conversation:{conversation_key}"
    return username


def collect_douyin_stranger_message_results(account_id: int = 0) -> List[Dict]:
    target_account_id = int(account_id or 0)
    rows: List[Dict] = []
    for row in douyin_stranger_message_results:
        normalized = normalize_douyin_stranger_message_row(row if isinstance(row, dict) else {})
        if target_account_id > 0 and int(normalized.get("account_id", 0) or 0) != target_account_id:
            continue
        if is_douyin_group_like_stranger_message_row(normalized):
            continue
        rows.append(normalized)
    return rows


def collect_douyin_inbox_results(account_id: int = 0) -> List[Dict]:
    target_account_id = int(account_id or 0)
    rows: List[Dict] = []
    for row in douyin_inbox_results:
        normalized = normalize_douyin_inbox_row(row if isinstance(row, dict) else {})
        if target_account_id > 0 and int(normalized.get("account_id", 0) or 0) != target_account_id:
            continue
        if is_douyin_group_like_stranger_message_row(normalized):
            continue
        rows.append(normalized)
    return rows


def _build_douyin_stranger_message_reply_fields(row: Dict) -> Dict[str, object]:
    normalized = normalize_douyin_stranger_message_row(row if isinstance(row, dict) else {})
    return {
        "reply_status": normalized.get("reply_status", "pending"),
        "reply_error": normalized.get("reply_error", ""),
        "reply_message": normalized.get("reply_message", ""),
        "reply_account_id": normalized.get("reply_account_id", ""),
        "reply_started_at": normalized.get("reply_started_at", ""),
        "reply_finished_at": normalized.get("reply_finished_at", ""),
        "reply_updated_at": normalized.get("reply_updated_at", ""),
    }


def replace_douyin_stranger_message_results(account_id: int, rows: List[Dict]) -> int:
    global douyin_stranger_message_results

    target_account_id = int(account_id or 0)
    existing_map = {
        stranger_message_row_key(row): normalize_douyin_stranger_message_row(row)
        for row in douyin_stranger_message_results
        if int((row or {}).get("account_id", 0) or 0) == target_account_id
    }
    keep_rows = [
        normalize_douyin_stranger_message_row(row)
        for row in douyin_stranger_message_results
        if int((row or {}).get("account_id", 0) or 0) != target_account_id
    ]
    next_rows: List[Dict] = []
    seen = set()
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            continue
        normalized = normalize_douyin_stranger_message_row({**raw_row, "account_id": target_account_id})
        if is_douyin_group_like_stranger_message_row(normalized):
            continue
        key = stranger_message_row_key(normalized)
        if not key or key in seen:
            continue
        seen.add(key)
        existing = existing_map.get(key)
        if existing:
            normalized.update(_build_douyin_stranger_message_reply_fields(existing))
        next_rows.append(normalized)

    douyin_stranger_message_results = keep_rows + next_rows
    save_douyin_stranger_message_results()
    return len(next_rows)


def merge_douyin_stranger_message_results(account_id: int, rows: List[Dict]) -> int:
    global douyin_stranger_message_results

    target_account_id = int(account_id or 0)
    if target_account_id <= 0:
        return 0

    account_rows = [
        normalize_douyin_stranger_message_row(row)
        for row in douyin_stranger_message_results
        if int((row or {}).get("account_id", 0) or 0) == target_account_id
    ]
    other_rows = [
        normalize_douyin_stranger_message_row(row)
        for row in douyin_stranger_message_results
        if int((row or {}).get("account_id", 0) or 0) != target_account_id
    ]
    account_map = {
        stranger_message_row_key(row): row
        for row in account_rows
        if stranger_message_row_key(row)
    }
    incoming_keys: List[str] = []
    changed = 0
    for raw_row in rows or []:
        if not isinstance(raw_row, dict):
            continue
        normalized = normalize_douyin_stranger_message_row({**raw_row, "account_id": target_account_id})
        if is_douyin_group_like_stranger_message_row(normalized):
            continue
        key = stranger_message_row_key(normalized)
        if not key:
            continue
        existing = account_map.get(key)
        if existing:
            normalized.update(_build_douyin_stranger_message_reply_fields(existing))
            if existing != normalized:
                changed += 1
        else:
            changed += 1
        account_map[key] = normalized
        if key not in incoming_keys:
            incoming_keys.append(key)

    if not changed:
        return 0

    preserved_keys = [key for key in account_map.keys() if key not in incoming_keys]
    ordered_keys = incoming_keys + preserved_keys
    douyin_stranger_message_results = other_rows + [account_map[key] for key in ordered_keys if key in account_map]
    save_douyin_stranger_message_results()
    return changed


def replace_douyin_inbox_results(account_id: int, rows: List[Dict]) -> int:
    global douyin_inbox_results

    target_account_id = int(account_id or 0)
    keep_rows = [
        normalize_douyin_inbox_row(row)
        for row in douyin_inbox_results
        if int((row or {}).get("account_id", 0) or 0) != target_account_id
    ]
    next_rows: List[Dict] = []
    seen = set()
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            continue
        normalized = normalize_douyin_inbox_row({**raw_row, "account_id": target_account_id})
        if is_douyin_group_like_stranger_message_row(normalized):
            continue
        key = stranger_message_row_key(normalized)
        if not key or key in seen:
            continue
        seen.add(key)
        next_rows.append(normalized)

    douyin_inbox_results = keep_rows + next_rows
    save_douyin_inbox_results()
    return len(next_rows)


def merge_douyin_inbox_results(account_id: int, rows: List[Dict]) -> int:
    global douyin_inbox_results

    target_account_id = int(account_id or 0)
    if target_account_id <= 0:
        return 0

    account_rows = [
        normalize_douyin_inbox_row(row)
        for row in douyin_inbox_results
        if int((row or {}).get("account_id", 0) or 0) == target_account_id
    ]
    other_rows = [
        normalize_douyin_inbox_row(row)
        for row in douyin_inbox_results
        if int((row or {}).get("account_id", 0) or 0) != target_account_id
    ]
    account_map = {
        stranger_message_row_key(row): row
        for row in account_rows
        if stranger_message_row_key(row)
    }
    incoming_keys: List[str] = []
    changed = 0
    for raw_row in rows or []:
        if not isinstance(raw_row, dict):
            continue
        normalized = normalize_douyin_inbox_row({**raw_row, "account_id": target_account_id})
        if is_douyin_group_like_stranger_message_row(normalized):
            continue
        key = stranger_message_row_key(normalized)
        if not key:
            continue
        existing = account_map.get(key)
        if existing != normalized:
            changed += 1
        account_map[key] = normalized
        if key not in incoming_keys:
            incoming_keys.append(key)

    if not changed:
        return 0

    preserved_keys = [key for key in account_map.keys() if key not in incoming_keys]
    ordered_keys = incoming_keys + preserved_keys
    douyin_inbox_results = other_rows + [account_map[key] for key in ordered_keys if key in account_map]
    save_douyin_inbox_results()
    return changed


def update_douyin_inbox_rows(
    target_rows: List[Dict],
    *,
    status: str,
    error: Optional[str] = None,
    message: Optional[str] = None,
    account_id: Optional[int] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
) -> int:
    keys = {stranger_message_row_key(row) for row in target_rows if stranger_message_row_key(row)}
    if not keys:
        return 0

    updated = 0
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    next_rows: List[Dict] = []
    for raw_row in douyin_inbox_results:
        normalized = normalize_douyin_inbox_row(raw_row if isinstance(raw_row, dict) else {})
        if stranger_message_row_key(normalized) in keys:
            normalized["reply_status"] = normalize_douyin_text(status).lower() or "pending"
            normalized["reply_updated_at"] = timestamp
            if error is not None:
                normalized["reply_error"] = normalize_douyin_text(error)
            if message is not None:
                normalized["reply_message"] = normalize_douyin_text(message)
            if account_id is not None:
                normalized["reply_account_id"] = str(account_id)
            if started_at is not None:
                normalized["reply_started_at"] = normalize_douyin_text(started_at)
            if finished_at is not None:
                normalized["reply_finished_at"] = normalize_douyin_text(finished_at)
            elif normalized["reply_status"] in {"queued", "processing"}:
                normalized["reply_finished_at"] = ""
            updated += 1
        next_rows.append(normalized)

    if updated:
        douyin_inbox_results[:] = next_rows
        save_douyin_inbox_results()
    return updated


def get_douyin_stranger_message_seen_count(account_id: int = 0) -> int:
    target_account_id = int(account_id or 0)
    if target_account_id > 0:
        return len(douyin_stranger_message_seen_records.get(str(target_account_id), {}) or {})
    return sum(len(bucket or {}) for bucket in douyin_stranger_message_seen_records.values())


def is_douyin_stranger_message_unread_row(row: Dict) -> bool:
    normalized = normalize_douyin_stranger_message_row(row if isinstance(row, dict) else {})
    unread_count = max(0, int(normalized.get("unread_count", 0) or 0))
    return bool(normalized.get("is_unread")) or unread_count > 0


def stranger_message_seen_fingerprints(row: Dict, account_id: Optional[int] = None) -> List[str]:
    normalized = normalize_douyin_stranger_message_row(row if isinstance(row, dict) else {})
    final_account_id = int(account_id or normalized.get("account_id", 0) or 0)
    if final_account_id <= 0:
        return []
    conversation_key = stranger_message_row_key(normalized)
    preview_text = normalize_douyin_text(
        normalized.get("incoming_message", "")
        or normalized.get("preview_text", "")
    )
    time_text = normalize_douyin_text(normalized.get("time_text", "") or normalized.get("collected_at", ""))
    unread_count = max(0, int(normalized.get("unread_count", 0) or 0))
    if not conversation_key:
        return []
    if not preview_text and not time_text and unread_count <= 0:
        return []

    fingerprints: List[str] = []

    def push_fingerprint(conversation_identity: str):
        identity = normalize_douyin_text(conversation_identity)
        if not identity:
            return
        fingerprint = "|".join(
            [
                f"account:{final_account_id}",
                identity,
                preview_text,
                time_text,
                f"unread:{unread_count}",
            ]
        ).lower()
        if fingerprint and fingerprint not in fingerprints:
            fingerprints.append(fingerprint)

    push_fingerprint(conversation_key)
    raw_conversation_key = normalize_douyin_text(
        normalized.get("conversation_key", "")
        or normalized.get("conversation_id", "")
    )
    if raw_conversation_key:
        push_fingerprint(f"conversation:{raw_conversation_key}")
    return fingerprints


def split_unseen_douyin_stranger_message_rows(account_id: int, rows: List[Dict]) -> tuple[List[Dict], List[Dict]]:
    target_account_id = int(account_id or 0)
    if target_account_id <= 0:
        return ([], [row for row in (rows or []) if isinstance(row, dict)])

    bucket = dict(douyin_stranger_message_seen_records.get(str(target_account_id), {}) or {})
    unseen_rows: List[Dict] = []
    seen_rows: List[Dict] = []
    for raw_row in rows or []:
        if not isinstance(raw_row, dict):
            continue
        normalized = normalize_douyin_stranger_message_row({**raw_row, "account_id": target_account_id})
        fingerprints = stranger_message_seen_fingerprints(normalized, target_account_id)
        if not fingerprints or not any(fingerprint in bucket for fingerprint in fingerprints):
            unseen_rows.append(normalized)
        else:
            seen_rows.append(normalized)
    return unseen_rows, seen_rows


def bootstrap_douyin_stranger_message_seen_records():
    grouped_rows: Dict[int, List[Dict]] = {}
    for raw_row in douyin_stranger_message_results:
        if not isinstance(raw_row, dict):
            continue
        normalized = normalize_douyin_stranger_message_row(raw_row)
        account_id = int(normalized.get("account_id", 0) or 0)
        if account_id <= 0:
            continue
        grouped_rows.setdefault(account_id, []).append(normalized)
    for account_id, rows in grouped_rows.items():
        if douyin_stranger_message_seen_records.get(str(account_id)):
            continue
        mark_douyin_stranger_message_rows_seen(account_id, rows)


def mark_douyin_stranger_message_rows_seen(account_id: int, rows: List[Dict]) -> int:
    target_account_id = int(account_id or 0)
    if target_account_id <= 0:
        return 0

    bucket_key = str(target_account_id)
    bucket = dict(douyin_stranger_message_seen_records.get(bucket_key, {}) or {})
    changed = False
    new_count = 0
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for raw_row in rows or []:
        if not isinstance(raw_row, dict):
            continue
        normalized = normalize_douyin_stranger_message_row({**raw_row, "account_id": target_account_id})
        fingerprints = stranger_message_seen_fingerprints(normalized, target_account_id)
        if not fingerprints:
            continue
        fingerprint = fingerprints[0]
        if not any(existing in bucket for existing in fingerprints):
            new_count += 1
            changed = True
        bucket[fingerprint] = normalize_douyin_text(normalized.get("collected_at", "") or timestamp)

    if len(bucket) > DOUYIN_STRANGER_MESSAGE_SEEN_LIMIT_PER_ACCOUNT:
        keep_items = sorted(
            bucket.items(),
            key=lambda item: normalize_douyin_text(item[1]),
            reverse=True,
        )[:DOUYIN_STRANGER_MESSAGE_SEEN_LIMIT_PER_ACCOUNT]
        next_bucket = {key: value for key, value in reversed(keep_items)}
        if next_bucket != bucket:
            changed = True
        bucket = next_bucket

    if changed:
        douyin_stranger_message_seen_records[bucket_key] = bucket
        save_douyin_stranger_message_seen_records()

    return new_count


def update_douyin_stranger_message_rows(
    target_rows: List[Dict],
    *,
    status: str,
    error: Optional[str] = None,
    message: Optional[str] = None,
    account_id: Optional[int] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
) -> int:
    keys = {stranger_message_row_key(row) for row in target_rows if stranger_message_row_key(row)}
    if not keys:
        return 0

    updated = 0
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    next_rows: List[Dict] = []
    for raw_row in douyin_stranger_message_results:
        normalized = normalize_douyin_stranger_message_row(raw_row if isinstance(raw_row, dict) else {})
        if stranger_message_row_key(normalized) in keys:
            normalized["reply_status"] = normalize_douyin_text(status).lower() or "pending"
            normalized["reply_updated_at"] = timestamp
            if error is not None:
                normalized["reply_error"] = normalize_douyin_text(error)
            if message is not None:
                normalized["reply_message"] = normalize_douyin_text(message)
            if account_id is not None:
                normalized["reply_account_id"] = str(account_id)
            if started_at is not None:
                normalized["reply_started_at"] = normalize_douyin_text(started_at)
            if finished_at is not None:
                normalized["reply_finished_at"] = normalize_douyin_text(finished_at)
            elif normalized["reply_status"] in {"queued", "processing"}:
                normalized["reply_finished_at"] = ""
            updated += 1
        next_rows.append(normalized)

    if updated:
        douyin_stranger_message_results[:] = next_rows
        save_douyin_stranger_message_results()
    return updated


def remove_group_members_from_results(rows: List[Dict]) -> int:
    global douyin_group_member_results

    keys = set()
    for row in rows:
        key = group_member_row_key(row)
        if key:
            keys.add(key)
    if not keys:
        return 0

    before = len(douyin_group_member_results)
    douyin_group_member_results = [
        row for row in douyin_group_member_results if group_member_row_key(row) not in keys
    ]
    removed = before - len(douyin_group_member_results)
    if removed:
        save_douyin_group_member_results()
    return removed


def collect_douyin_interaction_users() -> List[Dict]:
    rows: List[Dict] = []
    seen = set()

    def append_user(raw_user: Dict, extra: Optional[Dict] = None):
        if not isinstance(raw_user, dict):
            return
        normalized = normalize_high_intent_user(raw_user)
        normalized["profile_url"] = ensure_douyin_profile_url(normalized)
        row = {**normalized, **(extra or {})}
        key = user_choice_key(row)
        if not key or key in seen:
            return
        seen.add(key)
        rows.append(row)

    for task in douyin_tasks:
        for user in task.get("high_intent_users", []) or []:
            normalized = normalize_high_intent_user(user)
            append_user(
                normalized,
                {
                    "task_id": int(task.get("id", 0) or 0),
                    "task_title": task.get("title", ""),
                    "task_url": task.get("url", ""),
                    "task_author": task.get("author", ""),
                    "source_session_id": str(
                        normalized.get("source_session_id", "") or task.get("source_session_id", "") or ""
                    ).strip(),
                    "source_item_key": str(
                        normalized.get("source_item_key", "") or task.get("source_item_key", "") or ""
                    ).strip(),
                },
            )
    _monitor_all_rows, monitor_precise_rows = build_monitor_customer_pools(None)
    for user in monitor_precise_rows:
        append_user(
            user,
            {
                "source": "douyin_monitor",
                "task_id": 0,
                "task_title": str(user.get("latest_video_title", "") or user.get("task_title", "") or "同行监控"),
                "task_url": str(user.get("latest_video_url", "") or user.get("task_url", "") or ""),
                "task_author": str(user.get("monitor_target_username", "") or user.get("target_username", "") or ""),
            },
        )
    for user in douyin_manual_interaction_users:
        normalized = normalize_high_intent_user(user)
        append_user(
            normalized,
            {
                "task_id": 0,
                "task_title": normalized.get("task_title", "") or normalized.get("group_name", "群成员导入"),
                "task_url": normalized.get("task_url", ""),
                "task_author": normalized.get("task_author", ""),
            },
            )

    def sort_key(row: Dict) -> tuple[int, float, str]:
        interaction_status = str(row.get("interaction_status", "pending") or "pending").strip().lower()
        status_rank = {
            "pending": 0,
            "failed": 1,
            "interrupted": 1,
            "skipped": 2,
            "queued": 3,
            "processing": 4,
            "sent": 9,
            "completed": 9,
        }.get(interaction_status, 0)
        timestamp = (
            parse_comment_timestamp(row.get("last_seen_at", ""))
            or parse_comment_timestamp(row.get("comment_time", ""))
            or parse_comment_timestamp(row.get("first_seen_at", ""))
        )
        username = str(row.get("username", "") or "")
        return (status_rank, -float(timestamp or 0), username)

    rows.sort(key=sort_key)
    return rows


def update_douyin_monitor_comment_users(
    target_rows: List[Dict],
    updates: Dict[str, object],
) -> int:
    strict_keys = {user_choice_key(row) for row in target_rows if user_choice_key(row)}
    loose_keys = {user_choice_loose_key(row) for row in target_rows if user_choice_loose_key(row)}
    monitor_user_keys = {
        build_monitor_user_key(row)
        for row in target_rows
        if isinstance(row, dict) and build_monitor_user_key(row)
    }
    if not strict_keys and not loose_keys and not monitor_user_keys:
        return 0

    matched_rows: List[Dict] = []
    for comment in douyin_state_store.load_douyin_monitor_comments(None):
        if not isinstance(comment, dict):
            continue
        strict_key = user_choice_key(comment)
        loose_key = user_choice_loose_key(comment)
        monitor_user_key = build_monitor_user_key(comment)
        if not (
            (strict_key and strict_key in strict_keys)
            or (loose_key and loose_key in loose_keys)
            or (monitor_user_key and monitor_user_key in monitor_user_keys)
        ):
            continue
        next_row = dict(comment)
        next_row.update(updates)
        if "is_high_intent" not in updates:
            next_row["is_high_intent"] = bool(comment.get("is_high_intent", True))
        next_row["_preserve_monitor_seen_state"] = True
        matched_rows.append(next_row)

    if matched_rows:
        douyin_state_store.save_douyin_monitor_comments(matched_rows)
    return len(matched_rows)


def build_douyin_interaction_user_status_payload(row: Dict, lite: bool = False) -> Dict:
    if not isinstance(row, dict):
        return {}
    if not lite:
        return dict(row)
    return {
        "comment_id": str(row.get("comment_id", "") or "").strip(),
        "username": str(row.get("username", "") or "").strip(),
        "user_id": str(row.get("user_id", "") or "").strip(),
        "comment": str(row.get("comment", row.get("content", "")) or "").strip(),
        "comment_time": str(row.get("comment_time", "") or "").strip(),
        "region": str(row.get("region", row.get("location", row.get("ip_location", ""))) or "").strip(),
        "like_count": row.get("like_count", ""),
        "reply_count": row.get("reply_count", ""),
        "profile_url": str(row.get("profile_url", "") or "").strip(),
        "avatar": str(row.get("avatar", row.get("avatar_url", "")) or "").strip(),
        "reason": str(row.get("reason", "") or "").strip(),
        "source": str(row.get("source", "") or "").strip(),
        "is_high_intent": bool(row.get("is_high_intent", True)),
        "source_session_id": str(row.get("source_session_id", "") or "").strip(),
        "source_keyword": str(row.get("source_keyword", "") or "").strip(),
        "task_id": int(row.get("task_id", 0) or 0),
        "task_title": str(row.get("task_title", "") or "").strip(),
        "task_url": str(row.get("task_url", "") or "").strip(),
        "task_author": str(row.get("task_author", "") or "").strip(),
        "monitor_target_id": int(row.get("monitor_target_id", row.get("target_id", 0)) or 0),
        "monitor_target_username": str(row.get("monitor_target_username", row.get("target_username", "")) or "").strip(),
        "monitor_target_profile_url": str(row.get("monitor_target_profile_url", row.get("target_profile_url", "")) or "").strip(),
        "last_seen_at": str(row.get("last_seen_at", "") or "").strip(),
        "first_seen_at": str(row.get("first_seen_at", "") or "").strip(),
        "source_video_count": int(row.get("source_video_count", 0) or 0),
        "comment_count": int(row.get("comment_count", 0) or 0),
        "follow_comment_status": str(row.get("follow_comment_status", "pending") or "pending").strip(),
        "follow_comment_error": str(row.get("follow_comment_error", "") or "").strip(),
        "follow_comment_result": str(row.get("follow_comment_result", "") or "").strip(),
        "follow_comment_account_id": str(row.get("follow_comment_account_id", "") or "").strip(),
        "interaction_status": str(row.get("interaction_status", "pending") or "pending").strip(),
        "interaction_error": str(row.get("interaction_error", "") or "").strip(),
        "interaction_account_id": str(row.get("interaction_account_id", "") or "").strip(),
    }


def sync_douyin_customer_pool_cache(all_rows: List[Dict], precise_rows: List[Dict]):
    global douyin_all_customer_pool, douyin_precise_customer_pool
    douyin_all_customer_pool = [dict(row) for row in all_rows if isinstance(row, dict)]
    douyin_precise_customer_pool = [dict(row) for row in precise_rows if isinstance(row, dict)]


def build_douyin_customer_pools_from_tasks(tasks: List[Dict]) -> tuple[List[Dict], List[Dict]]:
    all_rows: List[Dict] = []
    precise_rows: List[Dict] = []
    seen_all = set()
    seen_precise = set()

    def normalize_pool_row(source: Dict, task: Dict, is_high_intent: bool) -> Dict:
        normalized = normalize_high_intent_user(source or {})
        normalized["profile_url"] = ensure_douyin_profile_url(normalized)
        normalized["task_id"] = int(task.get("id", 0) or 0)
        normalized["task_title"] = str(task.get("title", "") or "")
        normalized["task_url"] = str(task.get("url", "") or "")
        normalized["task_author"] = str(task.get("author", "") or "")
        normalized["source_session_id"] = str(
            normalized.get("source_session_id", "") or task.get("source_session_id", "") or ""
        ).strip()
        normalized["source_item_key"] = str(
            normalized.get("source_item_key", "") or task.get("source_item_key", "") or ""
        ).strip()
        normalized["title"] = normalized["task_title"]
        normalized["url"] = normalized["task_url"]
        normalized["author"] = normalized["task_author"]
        normalized["cover_image"] = str(task.get("cover_image", "") or "")
        normalized["is_high_intent"] = bool(is_high_intent)
        normalized["source"] = str(normalized.get("source", "") or "douyin_task")
        return normalized

    def append_row(bucket: List[Dict], seen: Set[str], source: Dict, task: Dict, is_high_intent: bool):
        row = normalize_pool_row(source, task, is_high_intent)
        key = user_choice_key(row) if normalize_douyin_text(row.get("comment_id", "")) else user_choice_loose_key(row)
        if not key or key in seen:
            return
        seen.add(key)
        bucket.append(row)

    for raw_task in tasks or []:
        if not isinstance(raw_task, dict):
            continue
        task = ensure_douyin_task_shape(dict(raw_task))
        high_intent_rows = [normalize_high_intent_user(row) for row in (task.get("high_intent_users", []) or []) if isinstance(row, dict)]
        all_comment_rows = [normalize_high_intent_user(row) for row in (task.get("all_comments", []) or []) if isinstance(row, dict)]
        high_intent_strict = {user_choice_key(row) for row in high_intent_rows if user_choice_key(row)}
        high_intent_loose = {user_choice_loose_key(row) for row in high_intent_rows if user_choice_loose_key(row)}
        comment_lookup = {
            user_choice_loose_key(row): row
            for row in all_comment_rows
            if user_choice_loose_key(row)
        }

        for comment in all_comment_rows:
            comment_strict_key = user_choice_key(comment)
            comment_loose_key = user_choice_loose_key(comment)
            is_high_intent = bool(
                (comment_strict_key and comment_strict_key in high_intent_strict)
                or (comment_loose_key and comment_loose_key in high_intent_loose)
            )
            append_row(all_rows, seen_all, comment, task, is_high_intent)
            if is_high_intent:
                append_row(precise_rows, seen_precise, comment, task, True)

        for user in high_intent_rows:
            merged = {**comment_lookup.get(user_choice_loose_key(user), {}), **user}
            append_row(all_rows, seen_all, merged, task, True)
            append_row(precise_rows, seen_precise, merged, task, True)

    return all_rows, precise_rows


def normalize_combined_customer_pool_row(row: Dict) -> Dict:
    normalized = dict(row or {})
    source = str(normalized.get("source", "") or "").strip() or "douyin_task"
    normalized["source"] = source
    normalized["profile_url"] = ensure_douyin_profile_url(normalized)

    if source == "douyin_monitor":
        monitor_target_id = int(
            normalized.get("monitor_target_id", normalized.get("target_id", 0)) or 0
        )
        monitor_target_username = str(
            normalized.get("monitor_target_username", normalized.get("target_username", "")) or ""
        ).strip()
        task_title = str(
            normalized.get("task_title", "")
            or normalized.get("latest_video_title", "")
            or normalized.get("video_title", "")
            or monitor_target_username
            or "同行监控"
        ).strip()
        task_url = str(
            normalized.get("task_url", "")
            or normalized.get("latest_video_url", "")
            or normalized.get("video_url", "")
            or normalized.get("target_profile_url", "")
            or normalized.get("profile_url", "")
        ).strip()
        task_author = str(
            normalized.get("task_author", "")
            or monitor_target_username
            or normalized.get("author", "")
        ).strip()
        normalized["task_id"] = 0
        normalized["task_title"] = task_title
        normalized["task_url"] = task_url
        normalized["task_author"] = task_author
        normalized["title"] = task_title
        normalized["url"] = task_url
        normalized["author"] = task_author
        normalized["cover_image"] = str(
            normalized.get("cover_image", "")
            or normalized.get("video_cover_image", "")
            or normalized.get("target_avatar_url", "")
        ).strip()
        normalized["monitor_target_id"] = monitor_target_id
        normalized["monitor_target_username"] = monitor_target_username
        normalized["monitor_target_profile_url"] = str(
            normalized.get("monitor_target_profile_url", "")
            or normalized.get("target_profile_url", "")
        ).strip()
    else:
        normalized["task_id"] = int(normalized.get("task_id", 0) or 0)
        normalized["task_title"] = str(
            normalized.get("task_title", "") or normalized.get("title", "")
        ).strip()
        normalized["task_url"] = str(
            normalized.get("task_url", "") or normalized.get("url", "")
        ).strip()
        normalized["task_author"] = str(
            normalized.get("task_author", "") or normalized.get("author", "")
        ).strip()
        normalized["title"] = str(
            normalized.get("title", "") or normalized.get("task_title", "")
        ).strip()
        normalized["url"] = str(
            normalized.get("url", "") or normalized.get("task_url", "")
        ).strip()
        normalized["author"] = str(
            normalized.get("author", "") or normalized.get("task_author", "")
        ).strip()
        normalized["cover_image"] = str(
            normalized.get("cover_image", "") or normalized.get("video_cover_image", "")
        ).strip()

    normalized["is_high_intent"] = bool(normalized.get("is_high_intent"))
    return normalized


def merge_combined_customer_pool_row(target: Dict, incoming: Dict) -> Dict:
    merged = dict(target or {})
    next_row = dict(incoming or {})

    for key, value in next_row.items():
        if key == "is_high_intent":
            merged[key] = bool(merged.get(key)) or bool(value)
            continue
        if key in {"comment_count", "seen_count_total", "source_video_count"}:
            merged[key] = max(int(merged.get(key, 0) or 0), int(value or 0))
            continue
        if key == "source":
            incoming_source = str(value or "").strip()
            existing_source = str(merged.get(key, "") or "").strip()
            existing_task_id = int(merged.get("task_id", 0) or 0)
            incoming_task_id = int(next_row.get("task_id", 0) or 0)
            if incoming_task_id > 0:
                merged[key] = incoming_source or "douyin_task"
            elif not existing_source or existing_task_id <= 0:
                merged[key] = value
            continue
        if key in {"last_seen_at", "comment_time"}:
            if str(value or "").strip() >= str(merged.get(key, "") or "").strip():
                merged[key] = value
            continue
        if key == "cover_image" and not str(merged.get("cover_image", "") or "").strip():
            merged[key] = value
            continue
        if key.startswith("monitor_target_") and not str(merged.get(key, "") or "").strip():
            merged[key] = value
            continue
        if not str(merged.get(key, "") or "").strip() and value not in (None, "", [], {}):
            merged[key] = value

    return merged


def build_combined_douyin_customer_pools() -> tuple[List[Dict], List[Dict]]:
    task_all_rows, _ = build_douyin_customer_pools_from_tasks(douyin_tasks)
    monitor_all_rows, _ = build_monitor_customer_pools(None)

    merged_rows: Dict[str, Dict] = {}
    ordered_keys: List[str] = []

    def append_row(raw_row: Dict):
        if not isinstance(raw_row, dict):
            return
        row = normalize_combined_customer_pool_row(raw_row)
        key = (
            user_choice_key(row)
            if normalize_douyin_text(row.get("comment_id", ""))
            else user_choice_loose_key(row)
        )
        if not key:
            return
        if key not in merged_rows:
            merged_rows[key] = row
            ordered_keys.append(key)
            return
        merged_rows[key] = merge_combined_customer_pool_row(merged_rows[key], row)

    for row in task_all_rows:
        append_row(row)
    for row in monitor_all_rows:
        append_row(row)

    all_rows = [merged_rows[key] for key in ordered_keys]
    precise_rows = [dict(row) for row in all_rows if bool(row.get("is_high_intent"))]
    return all_rows, precise_rows


def delete_douyin_customers_from_tasks(raw_rows: List[Dict]) -> Dict[str, int]:
    strict_keys_by_task: Dict[int, Set[str]] = {}
    loose_keys_by_task: Dict[int, Set[str]] = {}
    global_strict_keys: Set[str] = set()
    global_loose_keys: Set[str] = set()
    skipped_monitor = 0
    requested = 0

    for raw_row in raw_rows or []:
        if not isinstance(raw_row, dict):
            continue
        requested += 1
        task_id = int(raw_row.get("task_id", 0) or 0)
        source = str(raw_row.get("source", "") or "").strip()
        monitor_target_id = int(raw_row.get("monitor_target_id", raw_row.get("target_id", 0)) or 0)
        if source == "douyin_monitor" or (task_id <= 0 and monitor_target_id > 0):
            skipped_monitor += 1
            continue

        normalized = normalize_high_intent_user(raw_row)
        strict_key = user_choice_key(normalized)
        loose_key = user_choice_loose_key(normalized)
        if not strict_key and not loose_key:
            continue

        if task_id > 0:
            if strict_key:
                strict_keys_by_task.setdefault(task_id, set()).add(strict_key)
            if loose_key:
                loose_keys_by_task.setdefault(task_id, set()).add(loose_key)
        else:
            if strict_key:
                global_strict_keys.add(strict_key)
            if loose_key:
                global_loose_keys.add(loose_key)

    if not strict_keys_by_task and not loose_keys_by_task and not global_strict_keys and not global_loose_keys:
        return {
            "requested": requested,
            "removed": 0,
            "removed_precise": 0,
            "skipped_monitor": skipped_monitor,
            "matched_tasks": 0,
        }

    removed_customer_keys: Set[str] = set()
    removed_precise_keys: Set[str] = set()
    matched_task_ids: Set[int] = set()

    def should_remove(task_id: int, row: Dict) -> bool:
        normalized = normalize_high_intent_user(row)
        strict_key = user_choice_key(normalized)
        loose_key = user_choice_loose_key(normalized)
        task_strict = strict_keys_by_task.get(task_id, set())
        task_loose = loose_keys_by_task.get(task_id, set())
        return bool(
            (strict_key and (strict_key in task_strict or strict_key in global_strict_keys))
            or (loose_key and (loose_key in task_loose or loose_key in global_loose_keys))
        )

    def record_removed(task_id: int, row: Dict, *, precise: bool = False):
        normalized = normalize_high_intent_user(row)
        strict_key = user_choice_key(normalized)
        loose_key = user_choice_loose_key(normalized)
        identity = f"{task_id}:{strict_key or loose_key}"
        if strict_key or loose_key:
            removed_customer_keys.add(identity)
            if precise:
                removed_precise_keys.add(identity)

    for task in douyin_tasks:
        if not isinstance(task, dict):
            continue
        task_id = int(task.get("id", 0) or 0)
        if task_id <= 0:
            continue

        original_comments = task.get("all_comments", []) or []
        original_precise = task.get("high_intent_users", []) or []

        next_comments = []
        for row in original_comments:
            if should_remove(task_id, row):
                record_removed(task_id, row, precise=False)
                continue
            next_comments.append(row)

        next_precise = []
        for row in original_precise:
            if should_remove(task_id, row):
                record_removed(task_id, row, precise=True)
                continue
            next_precise.append(row)

        if len(next_comments) == len(original_comments) and len(next_precise) == len(original_precise):
            continue

        task["all_comments"] = next_comments
        task["high_intent_users"] = next_precise
        task["comment_count"] = len(next_comments)
        matched_task_ids.add(task_id)

    if matched_task_ids:
        save_douyin_tasks_state()

    return {
        "requested": requested,
        "removed": len(removed_customer_keys),
        "removed_precise": len(removed_precise_keys),
        "skipped_monitor": skipped_monitor,
        "matched_tasks": len(matched_task_ids),
    }


def add_douyin_customers_to_precise_pool(raw_rows: List[Dict]) -> Dict[str, int]:
    task_rows_by_id: Dict[int, List[Dict]] = {}
    monitor_rows: List[Dict] = []
    requested = 0

    for raw_row in raw_rows or []:
        if not isinstance(raw_row, dict):
            continue
        requested += 1
        task_id = int(raw_row.get("task_id", 0) or 0)
        source = str(raw_row.get("source", "") or "").strip()
        monitor_target_id = int(raw_row.get("monitor_target_id", raw_row.get("target_id", 0)) or 0)
        if source == "douyin_monitor" or (task_id <= 0 and monitor_target_id > 0):
            monitor_rows.append(raw_row)
        elif task_id > 0:
            task_rows_by_id.setdefault(task_id, []).append(raw_row)

    added_task = 0
    matched_tasks = 0
    for task in douyin_tasks:
        if not isinstance(task, dict):
            continue
        task_id = int(task.get("id", 0) or 0)
        if task_id not in task_rows_by_id:
            continue

        valid_pool: Dict[str, Dict] = {}
        for row in (task.get("all_comments", []) or []) + (task.get("high_intent_users", []) or []):
            normalized = normalize_high_intent_user(row)
            strict_key = user_choice_key(normalized)
            loose_key = user_choice_loose_key(normalized)
            if strict_key:
                valid_pool[("strict", strict_key)] = normalized
            if loose_key:
                valid_pool[("loose", loose_key)] = normalized

        next_rows = [normalize_high_intent_user(row) for row in task.get("high_intent_users", []) or [] if isinstance(row, dict)]
        existing_strict = {user_choice_key(row) for row in next_rows if user_choice_key(row)}
        existing_loose = {user_choice_loose_key(row) for row in next_rows if user_choice_loose_key(row)}
        before = len(next_rows)

        for raw_row in task_rows_by_id.get(task_id, []):
            normalized_raw = normalize_high_intent_user(raw_row)
            strict_key = user_choice_key(normalized_raw)
            loose_key = user_choice_loose_key(normalized_raw)
            if (strict_key and strict_key in existing_strict) or (loose_key and loose_key in existing_loose):
                continue
            matched = (
                valid_pool.get(("strict", strict_key))
                if strict_key
                else None
            ) or (valid_pool.get(("loose", loose_key)) if loose_key else None)
            if not matched:
                continue
            next_rows.append(matched)
            if strict_key:
                existing_strict.add(strict_key)
            if loose_key:
                existing_loose.add(loose_key)

        if len(next_rows) != before:
            task["high_intent_users"] = dedupe_users(next_rows)
            added_task += len(task["high_intent_users"]) - before
            matched_tasks += 1

    if matched_tasks:
        save_douyin_tasks_state()

    added_monitor = update_douyin_monitor_comment_users(
        monitor_rows,
        {
            "is_high_intent": True,
            "intent_level": "manual",
            "reason": "全部客户页手动加入精准",
        },
    ) if monitor_rows else 0

    return {
        "requested": requested,
        "added": max(0, added_task) + int(added_monitor or 0),
        "added_task": max(0, added_task),
        "added_monitor": int(added_monitor or 0),
        "matched_tasks": matched_tasks,
        "monitor_requested": len(monitor_rows),
    }


def update_douyin_interaction_users(
    target_rows: List[Dict],
    *,
    status: str,
    error: Optional[str] = None,
    message: Optional[str] = None,
    account_id: Optional[int] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
) -> int:
    keys = {user_choice_key(row) for row in target_rows if user_choice_key(row)}
    monitor_keys = {
        build_monitor_user_key(row)
        for row in target_rows
        if isinstance(row, dict) and build_monitor_user_key(row)
    }
    if not keys and not monitor_keys:
        return 0

    updated = 0
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for task in douyin_tasks:
        for user in task.get("high_intent_users", []) or []:
            if user_choice_key(user) not in keys:
                continue
            user["interaction_status"] = status
            user["interaction_updated_at"] = timestamp
            if error is not None:
                user["interaction_error"] = error
            if message is not None:
                user["interaction_message"] = message
            if account_id is not None:
                user["interaction_account_id"] = account_id
            if started_at is not None:
                user["interaction_started_at"] = started_at
            if finished_at is not None:
                user["interaction_finished_at"] = finished_at
            elif status in {"queued", "processing"}:
                user["interaction_finished_at"] = ""
            updated += 1

    for user in douyin_manual_interaction_users:
        if user_choice_key(user) not in keys:
            continue
        user["interaction_status"] = status
        user["interaction_updated_at"] = timestamp
        if error is not None:
            user["interaction_error"] = error
        if message is not None:
            user["interaction_message"] = message
        if account_id is not None:
            user["interaction_account_id"] = account_id
        if started_at is not None:
            user["interaction_started_at"] = started_at
        if finished_at is not None:
            user["interaction_finished_at"] = finished_at
        elif status in {"queued", "processing"}:
            user["interaction_finished_at"] = ""
        updated += 1

    updated += update_douyin_monitor_comment_users(
        target_rows,
        {
            "interaction_status": status,
            "interaction_updated_at": timestamp,
            **({"interaction_error": error} if error is not None else {}),
            **({"interaction_message": message} if message is not None else {}),
            **({"interaction_account_id": account_id} if account_id is not None else {}),
            **({"interaction_started_at": started_at} if started_at is not None else {}),
            **(
                {"interaction_finished_at": finished_at}
                if finished_at is not None
                else ({"interaction_finished_at": ""} if status in {"queued", "processing"} else {})
            ),
        },
    )

    if updated:
        save_douyin_tasks_state()
        save_douyin_manual_interaction_users()
    return updated


def update_douyin_follow_comment_users(
    target_rows: List[Dict],
    *,
    status: str,
    error: Optional[str] = None,
    comment_text: Optional[str] = None,
    account_id: Optional[int] = None,
    result: Optional[str] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
) -> int:
    keys = {user_choice_key(row) for row in target_rows if user_choice_key(row)}
    monitor_keys = {
        build_monitor_user_key(row)
        for row in target_rows
        if isinstance(row, dict) and build_monitor_user_key(row)
    }
    if not keys and not monitor_keys:
        return 0

    updated = 0
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for task in douyin_tasks:
        for user in task.get("high_intent_users", []) or []:
            if user_choice_key(user) not in keys:
                continue
            user["follow_comment_status"] = status
            user["follow_comment_updated_at"] = timestamp
            if error is not None:
                user["follow_comment_error"] = error
            if comment_text is not None:
                user["follow_comment_text"] = comment_text
            if account_id is not None:
                user["follow_comment_account_id"] = account_id
            if result is not None:
                user["follow_comment_result"] = result
            if started_at is not None:
                user["follow_comment_started_at"] = started_at
            if finished_at is not None:
                user["follow_comment_finished_at"] = finished_at
            elif status in {"queued", "processing"}:
                user["follow_comment_finished_at"] = ""
            updated += 1

    for user in douyin_manual_interaction_users:
        if user_choice_key(user) not in keys:
            continue
        user["follow_comment_status"] = status
        user["follow_comment_updated_at"] = timestamp
        if error is not None:
            user["follow_comment_error"] = error
        if comment_text is not None:
            user["follow_comment_text"] = comment_text
        if account_id is not None:
            user["follow_comment_account_id"] = account_id
        if result is not None:
            user["follow_comment_result"] = result
        if started_at is not None:
            user["follow_comment_started_at"] = started_at
        if finished_at is not None:
            user["follow_comment_finished_at"] = finished_at
        elif status in {"queued", "processing"}:
            user["follow_comment_finished_at"] = ""
        updated += 1

    updated += update_douyin_monitor_comment_users(
        target_rows,
        {
            "follow_comment_status": status,
            "follow_comment_updated_at": timestamp,
            **({"follow_comment_error": error} if error is not None else {}),
            **({"follow_comment_text": comment_text} if comment_text is not None else {}),
            **({"follow_comment_account_id": account_id} if account_id is not None else {}),
            **({"follow_comment_result": result} if result is not None else {}),
            **({"follow_comment_started_at": started_at} if started_at is not None else {}),
            **(
                {"follow_comment_finished_at": finished_at}
                if finished_at is not None
                else ({"follow_comment_finished_at": ""} if status in {"queued", "processing"} else {})
            ),
        },
    )

    if updated:
        save_douyin_tasks_state()
        save_douyin_manual_interaction_users()
    return updated


def update_douyin_mention_comment_users(
    target_rows: List[Dict],
    *,
    status: str,
    error: Optional[str] = None,
    comment_text: Optional[str] = None,
    account_id: Optional[int] = None,
    result: Optional[str] = None,
    video_url: Optional[str] = None,
    video_title: Optional[str] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
) -> int:
    keys = {user_choice_key(row) for row in target_rows if user_choice_key(row)}
    if not keys:
        return 0

    updated = 0
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for task in douyin_tasks:
        for user in task.get("high_intent_users", []) or []:
            if user_choice_key(user) not in keys:
                continue
            user["mention_comment_status"] = status
            user["mention_comment_updated_at"] = timestamp
            if error is not None:
                user["mention_comment_error"] = error
            if comment_text is not None:
                user["mention_comment_text"] = comment_text
            if account_id is not None:
                user["mention_comment_account_id"] = account_id
            if result is not None:
                user["mention_comment_result"] = result
            if video_url is not None:
                user["mention_comment_video_url"] = video_url
            if video_title is not None:
                user["mention_comment_video_title"] = video_title
            if started_at is not None:
                user["mention_comment_started_at"] = started_at
            if finished_at is not None:
                user["mention_comment_finished_at"] = finished_at
            elif status in {"queued", "processing"}:
                user["mention_comment_finished_at"] = ""
            updated += 1

    for user in douyin_manual_interaction_users:
        if user_choice_key(user) not in keys:
            continue
        user["mention_comment_status"] = status
        user["mention_comment_updated_at"] = timestamp
        if error is not None:
            user["mention_comment_error"] = error
        if comment_text is not None:
            user["mention_comment_text"] = comment_text
        if account_id is not None:
            user["mention_comment_account_id"] = account_id
        if result is not None:
            user["mention_comment_result"] = result
        if video_url is not None:
            user["mention_comment_video_url"] = video_url
        if video_title is not None:
            user["mention_comment_video_title"] = video_title
        if started_at is not None:
            user["mention_comment_started_at"] = started_at
        if finished_at is not None:
            user["mention_comment_finished_at"] = finished_at
        elif status in {"queued", "processing"}:
            user["mention_comment_finished_at"] = ""
        updated += 1

    if updated:
        save_douyin_tasks_state()
        save_douyin_manual_interaction_users()
    return updated


def upsert_douyin_mention_comment_history(
    target_rows: List[Dict],
    *,
    status: str,
    error: Optional[str] = None,
    comment_text: Optional[str] = None,
    account_id: Optional[int] = None,
    result: Optional[str] = None,
    video_url: Optional[str] = None,
    video_title: Optional[str] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
) -> int:
    updated = 0
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for row in target_rows or []:
        key = mention_comment_identity_key(row if isinstance(row, dict) else {})
        if not key:
            continue
        current = douyin_mention_comment_history.get(key, {})
        payload = {
            "status": str(status or current.get("status", "") or "").strip() or "completed",
            "username": normalize_douyin_text(row.get("username", "") if isinstance(row, dict) else ""),
            "profile_url": ensure_douyin_profile_url(row if isinstance(row, dict) else {}),
            "sec_user_id": normalize_douyin_text(row.get("sec_user_id", "") if isinstance(row, dict) else ""),
            "douyin_id": normalize_douyin_text(row.get("douyin_id", "") if isinstance(row, dict) else ""),
            "error": str(error if error is not None else current.get("error", "") or "").strip(),
            "comment_text": str(comment_text if comment_text is not None else current.get("comment_text", "") or "").strip(),
            "account_id": str(account_id if account_id is not None else current.get("account_id", "") or "").strip(),
            "result": str(result if result is not None else current.get("result", "") or "").strip(),
            "video_url": str(video_url if video_url is not None else current.get("video_url", "") or "").strip(),
            "video_title": str(video_title if video_title is not None else current.get("video_title", "") or "").strip(),
            "started_at": str(started_at if started_at is not None else current.get("started_at", "") or "").strip(),
            "finished_at": str(finished_at if finished_at is not None else current.get("finished_at", "") or "").strip(),
            "updated_at": timestamp,
        }
        douyin_mention_comment_history[key] = payload
        updated += 1
    if updated:
        save_douyin_mention_comment_history()
    return updated


def filter_douyin_mention_comment_rows_by_username(target_rows: List[Dict], usernames: List[str]) -> List[Dict]:
    wanted = {
        normalize_douyin_text(name).lower()
        for name in (usernames or [])
        if normalize_douyin_text(name)
    }
    if not wanted:
        return []
    matched: List[Dict] = []
    for row in target_rows or []:
        username = normalize_douyin_text(row.get("username", "") if isinstance(row, dict) else "").lower()
        if username in wanted:
            matched.append(row)
    return matched


restore_douyin_mention_comment_history()
restore_douyin_manual_interaction_users()
restore_douyin_stranger_message_results()
restore_douyin_inbox_results()
restore_douyin_self_comment_monitor_results()
restore_douyin_stranger_message_monitor_config()
restore_douyin_inbox_monitor_config()
restore_douyin_self_comment_monitor_config()
restore_douyin_stranger_message_seen_records()
bootstrap_douyin_stranger_message_seen_records()
restore_douyin_mention_self_video_cache()


def reconcile_douyin_stranger_message_runtime_state() -> bool:
    global douyin_stranger_message_running, douyin_stranger_message_stop_requested
    global douyin_stranger_message_background_task

    background_alive = bool(
        douyin_stranger_message_background_task
        and not douyin_stranger_message_background_task.done()
    )
    if background_alive:
        douyin_stranger_message_running = True
        douyin_stranger_message_state["running"] = True
        return True

    repaired = False
    next_rows: List[Dict] = []
    for raw_row in douyin_stranger_message_results:
        normalized = normalize_douyin_stranger_message_row(raw_row if isinstance(raw_row, dict) else {})
        if normalized.get("reply_status") == "processing":
            normalized["reply_status"] = "queued"
            normalized["reply_error"] = normalized.get("reply_error") or "Recovered from stale stranger-message state"
            normalized["reply_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            repaired = True
        next_rows.append(normalized)
    if repaired:
        douyin_stranger_message_results[:] = next_rows
        save_douyin_stranger_message_results()

    if (
        douyin_stranger_message_running
        or douyin_stranger_message_background_task is not None
        or douyin_stranger_message_stop_requested
    ):
        douyin_stranger_message_running = False
        douyin_stranger_message_stop_requested = False
        douyin_stranger_message_background_task = None
        douyin_stranger_message_state.update(
            {
                "running": False,
                "current_user": "",
                "current_message_text": "",
            }
        )
    return False


def is_douyin_stranger_message_monitor_busy(account_id: int = 0) -> tuple[bool, str]:
    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()

    if douyin_running:
        return True, "评论采集任务正在执行"
    if douyin_video_comment_running:
        return True, "视频评论任务正在执行"
    if douyin_mention_comment_running:
        return True, "评论@精准客户任务正在执行"
    if douyin_follow_comment_running:
        return True, "关注评论任务正在执行"
    if douyin_interaction_running:
        return True, "精准客户私信任务正在执行"
    if douyin_group_member_running:
        return True, "群成员提取任务正在执行"
    if douyin_stranger_message_running and int(douyin_stranger_message_state.get("account_id", 0) or 0) == int(account_id or 0):
        return True, "私信引流任务正在执行"
    if bool(douyin_monitor_runtime_state.get("running")):
        return True, "同行监控正在执行"
    if (
        reconcile_douyin_account_nurture_runtime_state()
        and douyin_account_nurture_scheduler
        and douyin_account_nurture_scheduler.is_actively_blocking_other_tasks()
    ):
        return True, "账号养号正在执行"
    return False, ""


def reconcile_douyin_inbox_runtime_state() -> bool:
    global douyin_inbox_running, douyin_inbox_stop_requested, douyin_inbox_background_task

    background_alive = bool(
        douyin_inbox_background_task
        and not douyin_inbox_background_task.done()
    )
    if background_alive:
        douyin_inbox_running = True
        douyin_inbox_state["running"] = True
        return True

    if douyin_inbox_running or douyin_inbox_background_task is not None or douyin_inbox_stop_requested:
        douyin_inbox_running = False
        douyin_inbox_stop_requested = False
        douyin_inbox_background_task = None
        douyin_inbox_state.update(
            {
                "running": False,
                "current_user": "",
            }
        )
    return False


def is_douyin_inbox_monitor_busy(account_id: int = 0) -> tuple[bool, str]:
    busy, reason = is_douyin_stranger_message_monitor_busy(account_id)
    if busy:
        return busy, reason
    if douyin_inbox_running and int(douyin_inbox_state.get("account_id", 0) or 0) == int(account_id or 0):
        return True, "消息聚合采集任务正在执行"
    task = douyin_self_comment_monitor_tasks_by_account.get(int(account_id or 0))
    if task and not task.done():
        return True, "我的评论区监控正在执行"
    return False, ""


def set_douyin_self_comment_monitor_idle_message(account_id: int, message: str = ""):
    state = get_douyin_self_comment_monitor_state(account_id, create=True)
    next_run_text = normalize_douyin_text(state.get("next_run_at", ""))
    fallback = f"我的评论区监控已开启，下一轮检查时间 {next_run_text}。" if next_run_text else "我的评论区监控已开启，等待下一轮检查。"
    state.update(
        {
            "running": False,
            "message": normalize_douyin_text(message) or fallback,
        }
    )


def schedule_next_douyin_self_comment_monitor_run(account_id: int, base_time: Optional[datetime] = None) -> str:
    state = get_douyin_self_comment_monitor_state(account_id, create=True)
    current = base_time or datetime.now()
    interval_minutes = max(
        1,
        min(int(state.get("interval_minutes", 30) or 30), 1440),
    )
    next_run = current + timedelta(minutes=interval_minutes)
    next_run_text = next_run.strftime("%Y-%m-%d %H:%M:%S")
    state["next_run_at"] = next_run_text
    return next_run_text


def set_douyin_inbox_monitor_idle_message(account_id: int, message: str = ""):
    state = get_douyin_inbox_monitor_state(account_id, create=True)
    next_run_text = normalize_douyin_text(state.get("next_run_at", ""))
    fallback = f"自动采集已开启，下一次检查时间 {next_run_text}。" if next_run_text else "自动采集已开启，等待下一轮检查。"
    state.update(
        {
            "running": False,
            "message": normalize_douyin_text(message) or fallback,
        }
    )


def schedule_next_douyin_inbox_monitor_run(account_id: int, base_time: Optional[datetime] = None) -> str:
    state = get_douyin_inbox_monitor_state(account_id, create=True)
    current = base_time or datetime.now()
    interval_minutes = max(
        1,
        min(int(state.get("interval_minutes", 30) or 30), 1440),
    )
    next_run = current + timedelta(minutes=interval_minutes)
    next_run_text = next_run.strftime("%Y-%m-%d %H:%M:%S")
    state["next_run_at"] = next_run_text
    return next_run_text


def set_douyin_stranger_message_monitor_idle_message(account_id: int, message: str = ""):
    state = get_douyin_stranger_message_monitor_state(account_id, create=True)
    next_run_text = normalize_douyin_text(state.get("next_run_at", ""))
    fallback = f"监控已开启，下一次检查时间 {next_run_text}。" if next_run_text else "监控已开启，等待下一轮检查。"
    state.update(
        {
            "running": False,
            "message": normalize_douyin_text(message) or fallback,
        }
    )


def schedule_next_douyin_stranger_message_monitor_run(account_id: int, base_time: Optional[datetime] = None) -> str:
    state = get_douyin_stranger_message_monitor_state(account_id, create=True)
    current = base_time or datetime.now()
    interval_minutes = max(
        1,
        min(int(state.get("interval_minutes", 30) or 30), 1440),
    )
    next_run = current + timedelta(minutes=interval_minutes)
    next_run_text = next_run.strftime("%Y-%m-%d %H:%M:%S")
    state["next_run_at"] = next_run_text
    return next_run_text


def reconcile_douyin_interaction_runtime_state() -> bool:
    global douyin_interaction_running, douyin_interaction_stop_requested, douyin_interaction_background_task

    background_alive = bool(
        douyin_interaction_background_task and not douyin_interaction_background_task.done()
    )
    if background_alive:
        douyin_interaction_running = True
        douyin_interaction_state["running"] = True
        return True

    repaired = False
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    interrupted_note = "任务中断，未确认是否已发送，请在表格里手动确认后再决定是否重发。"
    for task in douyin_tasks:
        for user in task.get("high_intent_users", []) or []:
            current_status = str(user.get("interaction_status", "") or "").strip().lower()
            if current_status == "processing":
                user["interaction_status"] = "interrupted"
                user["interaction_error"] = interrupted_note
                user["interaction_updated_at"] = timestamp
                user["interaction_finished_at"] = ""
                repaired = True
            elif current_status == "queued":
                user["interaction_status"] = "pending"
                user["interaction_updated_at"] = timestamp
                repaired = True

    for user in douyin_manual_interaction_users:
        current_status = str(user.get("interaction_status", "") or "").strip().lower()
        if current_status == "processing":
            user["interaction_status"] = "interrupted"
            user["interaction_error"] = interrupted_note
            user["interaction_updated_at"] = timestamp
            user["interaction_finished_at"] = ""
            repaired = True
        elif current_status == "queued":
            user["interaction_status"] = "pending"
            user["interaction_updated_at"] = timestamp
            repaired = True

    if repaired:
        save_douyin_tasks_state()
        save_douyin_manual_interaction_users()

    if douyin_interaction_running or douyin_interaction_background_task is not None or douyin_interaction_stop_requested:
        douyin_interaction_running = False
        douyin_interaction_stop_requested = False
        douyin_interaction_background_task = None
        douyin_interaction_state.update(
            {
                "running": False,
                "current_user": "",
                "current_users": [],
                "current_message_text": "",
                "account_ids": [],
                "workers": [],
            }
        )

    return False


def create_ai_client(model_override: str = "") -> AIClient:
    config = load_global_config()
    server_api_url, server_token = get_douyin_server_ai_proxy_credentials()
    if server_api_url and server_token:
        api_url = server_api_url
        api_key = server_token
    else:
        api_key = str(config.get("api_key", "") or "").strip()
        api_url = str(config.get("api_url", "https://ai.comfly.chat/v1/chat/completions") or "").strip()
    return AIClient(
        api_url=api_url,
        api_key=api_key,
        model=str(model_override or config.get("model", "gpt-5.4") or "gpt-5.4"),
    )


def get_douyin_request_bearer_token(request: Optional[Request] = None) -> str:
    if request is None:
        return ""
    auth = str(request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def set_douyin_ai_auth_token_from_request(request: Optional[Request] = None) -> None:
    token = get_douyin_request_bearer_token(request)
    if token:
        _douyin_ai_auth_token_var.set(token)


def get_douyin_server_ai_proxy_credentials() -> tuple[str, str]:
    token = str(_douyin_ai_auth_token_var.get("") or "").strip()
    if not token:
        token = str(os.environ.get("LOBSTER_COMFLY_PROXY_DEFAULT_TOKEN") or "").strip()
    if not token:
        try:
            from backend.app.services.openclaw_channel_auth_store import read_channel_fallback  # type: ignore

            token = str((read_channel_fallback()[0] or "")).strip()
        except Exception:
            token = ""
    auth_base = get_douyin_auth_server_base()
    if not auth_base or not token:
        return "", ""
    return f"{auth_base}/api/comfly-proxy/v1/chat/completions", token


def get_douyin_auth_server_base() -> str:
    auth_base = str(os.environ.get("AUTH_SERVER_BASE") or "").strip().rstrip("/")
    if not auth_base:
        auth_base = str(os.environ.get("LOBSTER_AUTH_SERVER_BASE") or "").strip().rstrip("/")
    if auth_base:
        return auth_base
    try:
        from backend.app.core.config import settings as app_settings  # type: ignore

        return str(getattr(app_settings, "auth_server_base", "") or "").strip().rstrip("/")
    except Exception:
        return ""


def douyin_ai_available(config: Optional[Dict] = None, request: Optional[Request] = None) -> bool:
    cfg = config if isinstance(config, dict) else load_global_config()
    if get_douyin_request_bearer_token(request):
        return bool(get_douyin_auth_server_base())
    url, token = get_douyin_server_ai_proxy_credentials()
    if url and token:
        return True
    return bool(str(cfg.get("api_key", "") or "").strip())


def get_douyin_ai_service_label(api_url: str) -> str:
    parsed = urlparse(str(api_url or "").strip())
    host = str(parsed.netloc or "").strip()
    return host or "AI 接口"


def parse_json_object_from_text(text: object) -> Dict:
    raw = str(text or "").strip()
    if not raw:
        return {}
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            continue
    return {}


def normalize_ai_expanded_keywords(seed_keyword: str, keywords: object, *, limit: int = 10) -> List[str]:
    seed = normalize_douyin_text(seed_keyword)
    values: List[str] = []
    if isinstance(keywords, list):
        values = [normalize_douyin_text(item) for item in keywords]
    elif isinstance(keywords, str):
        values = [
            normalize_douyin_text(item)
            for item in re.split(r"[\n,，;；、]+", keywords)
        ]
    seen: Set[str] = set()
    result: List[str] = []
    for value in values:
        value = value.strip(" -_*#\"'“”‘’")
        if (
            not value
            or len(value) > 28
            or value in {"{}", "[]", "null", "None"}
            or re.fullmatch(r"[\W_]+", value, flags=re.UNICODE)
            or re.search(r"[{}\[\]]", value)
        ):
            continue
        lowered = value.lower()
        if lowered == seed.lower() or lowered in seen:
            continue
        seen.add(lowered)
        result.append(value)
        if len(result) >= max(1, int(limit or 10)):
            break
    return result


def build_fallback_expanded_keywords(seed_keyword: str, *, limit: int = 10) -> List[str]:
    seed = normalize_douyin_text(seed_keyword)
    if not seed:
        return []
    suffixes = [
        "攻略",
        "避坑",
        "推荐",
        "价格",
        "怎么选",
        "真实体验",
        "测评",
        "最新",
        "哪里好",
        "注意事项",
        "性价比",
        "案例",
    ]
    prefixes = ["", "本地", "附近"]
    candidates: List[str] = []
    for suffix in suffixes:
        candidates.append(f"{seed}{suffix}")
    for prefix in prefixes:
        if prefix:
            candidates.append(f"{prefix}{seed}")
    return normalize_ai_expanded_keywords(seed, candidates, limit=limit)


def expand_douyin_keywords_with_ai(seed_keyword: str, *, limit: int = 10) -> List[str]:
    seed = normalize_douyin_text(seed_keyword)
    if not seed:
        return []
    client = create_ai_client(model_override="gpt-5.5")
    if not str(client.api_key or "").strip():
        raise RuntimeError("AI 接口 Key 未配置，无法自动拓词。")
    prompt = f"""
你是抖音获客搜索词规划助手。请基于用户给的种子关键词，生成 {limit} 个适合在抖音搜索视频的中文关键词。

种子关键词：{seed}

要求：
- 关键词要能扩充视频池，优先覆盖同城/行业/需求/痛点/产品/行为词。
- 不要生成太泛的词，例如“买房”“生活”“赚钱”这种单独泛词。
- 不要返回和种子关键词完全相同的词。
- 每个关键词 2 到 18 个中文字符为主，可以包含楼盘名、地区名、行业短语。
- 只返回 JSON，格式：{{"keywords":["关键词1","关键词2"]}}
""".strip()
    content = client.filter_with_prompt(prompt)
    parsed = parse_json_object_from_text(content)
    keywords = normalize_ai_expanded_keywords(seed, parsed.get("keywords", []), limit=limit)
    if not keywords:
        keywords = normalize_ai_expanded_keywords(seed, content, limit=limit)
    if len(keywords) < max(1, int(limit or 10)):
        fallback = build_fallback_expanded_keywords(seed, limit=limit)
        merged = normalize_ai_expanded_keywords(seed, keywords + fallback, limit=limit)
        if merged:
            return merged
    return keywords


def is_douyin_ai_generation_error(error: Exception | str) -> bool:
    text = str(error or "").strip().lower()
    return text.startswith("ai ")


def normalize_douyin_video_comment_mode(value: Optional[str]) -> str:
    mode = str(value or "fixed").strip().lower()
    if mode in {"fixed", "ai", "rewrite"}:
        return mode
    return "fixed"


def douyin_video_comment_mode_label(mode: Optional[str]) -> str:
    normalized = normalize_douyin_video_comment_mode(mode)
    return {
        "fixed": "固定文案",
        "ai": "AI 自动生成",
        "rewrite": "AI 同方向改编",
    }.get(normalized, "固定文案")


def normalize_douyin_stranger_reply_mode(value: Optional[str]) -> str:
    mode = str(value or "fixed").strip().lower()
    if mode in {"fixed", "ai_lead"}:
        return mode
    return "fixed"


def douyin_stranger_reply_mode_label(mode: Optional[str]) -> str:
    normalized = normalize_douyin_stranger_reply_mode(mode)
    return {
        "fixed": "固定文案",
        "ai_lead": "AI 引导加绿泡泡",
    }.get(normalized, "固定文案")


def normalize_douyin_inbox_reply_mode(value: Optional[str]) -> str:
    mode = str(value or "fixed").strip().lower()
    if mode in {"fixed", "rewrite", "ai", "ai_auto"}:
        return "ai" if mode == "ai_auto" else mode
    return "fixed"


def douyin_inbox_reply_mode_label(mode: Optional[str]) -> str:
    normalized = normalize_douyin_inbox_reply_mode(mode)
    return {
        "fixed": "固定回复",
        "rewrite": "AI 同方向回复",
        "ai": "AI 自动回复",
    }.get(normalized, "固定回复")


def append_douyin_contact_lines(message: str, contact_value: str) -> str:
    base_text = str(message or "").strip()
    cleaned_contact = normalize_douyin_text(contact_value)
    if not cleaned_contact:
        return base_text
    if cleaned_contact in base_text:
        return base_text
    parts = [part.strip() for part in base_text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if part.strip()]
    if not any("绿泡泡" in part or "微信" in part for part in parts):
        parts.append("麻烦您绿泡泡")
    parts.append(cleaned_contact)
    return "\n".join(parts)


def clean_douyin_video_comment_text(value: Optional[str], limit: int = 40) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()
    text = text.strip("\"'“”‘’`")
    if text.startswith("```") and text.endswith("```"):
        text = text.strip("`").strip()
    if len(text) > limit:
        text = text[:limit].rstrip("，。！？,.!? ")
    return text


def clean_douyin_message_line(value: Optional[str], limit: int = 18) -> str:
    text = clean_douyin_video_comment_text(value, limit=max(1, int(limit or 18)))
    return text.strip("，。！？,.!? ").strip()


def summarize_douyin_video_comment_settings(
    mode: Optional[str],
    fixed_text: str = "",
    prompt_text: str = "",
    seed_text: str = "",
) -> str:
    normalized = normalize_douyin_video_comment_mode(mode)
    if normalized == "fixed":
        preview = clean_douyin_video_comment_text(fixed_text, limit=18)
        return f"{douyin_video_comment_mode_label(normalized)}：{preview or '未填写'}"
    if normalized == "rewrite":
        preview = clean_douyin_video_comment_text(seed_text, limit=18)
        return f"{douyin_video_comment_mode_label(normalized)}：{preview or '未填写基准文案'}"
    preview = clean_douyin_video_comment_text(prompt_text, limit=18)
    return f"{douyin_video_comment_mode_label(normalized)}：{preview or '按视频内容自动生成'}"


def normalize_douyin_mention_users(raw_rows: List[Dict], max_mentions: int = 1000) -> List[Dict]:
    normalized_rows: List[Dict] = []
    seen_usernames = set()
    limit = max(1, min(int(max_mentions or 1000), 5000))

    for raw_row in raw_rows or []:
        if not isinstance(raw_row, dict):
            continue
        normalized = normalize_high_intent_user(raw_row)
        username = normalize_douyin_text(normalized.get("username", ""))
        if not username or username in seen_usernames:
            continue
        normalized["username"] = username
        normalized["profile_url"] = ensure_douyin_profile_url(normalized)
        seen_usernames.add(username)
        normalized_rows.append(normalized)
        if len(normalized_rows) >= limit:
            break

    return normalized_rows


def build_douyin_mention_comment_preview(rows: List[Dict], limit: int = 12) -> str:
    usernames = [
        f"@{normalize_douyin_text(row.get('username', ''))}"
        for row in rows or []
        if normalize_douyin_text(row.get("username", ""))
    ]
    if not usernames:
        return ""
    clipped = usernames[: max(1, int(limit or 12))]
    preview = " ".join(clipped).strip()
    if len(usernames) > len(clipped):
        preview = f"{preview} ..."
    return preview


def split_douyin_mention_comment_batches(
    rows: List[Dict],
    *,
    max_mentions_per_comment: int = DOUYIN_MENTION_COMMENT_MAX_USERS_PER_COMMENT,
    max_comment_chars: int = DOUYIN_MENTION_COMMENT_SAFE_TEXT_LIMIT,
) -> List[List[Dict]]:
    normalized_limit = max(1, min(int(max_mentions_per_comment or DOUYIN_MENTION_COMMENT_MAX_USERS_PER_COMMENT), DOUYIN_MENTION_COMMENT_MAX_USERS_PER_COMMENT))
    safe_chars = max(40, int(max_comment_chars or DOUYIN_MENTION_COMMENT_SAFE_TEXT_LIMIT))
    batches: List[List[Dict]] = []
    current_batch: List[Dict] = []
    current_length = 0

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        username = normalize_douyin_text(row.get("username", ""))
        if not username:
            continue
        mention_piece = f"@{username}"
        piece_length = len(mention_piece) + (1 if current_batch else 0)
        should_split = (
            current_batch
            and (
                len(current_batch) >= normalized_limit
                or current_length + piece_length > safe_chars
            )
        )
        if should_split:
            batches.append(current_batch)
            current_batch = []
            current_length = 0
            piece_length = len(mention_piece)
        current_batch.append(row)
        current_length += piece_length

    if current_batch:
        batches.append(current_batch)
    return batches


def split_already_mentioned_users(rows: List[Dict]) -> tuple[List[Dict], List[Dict]]:
    available: List[Dict] = []
    skipped: List[Dict] = []
    for row in rows or []:
        history = get_douyin_mention_comment_history_for_row(row if isinstance(row, dict) else {})
        history_status = str(history.get("status", "") or "").strip().lower()
        current_status = str((row or {}).get("mention_comment_status", "") or "").strip().lower()
        if history_status == "completed" or current_status == "completed":
            skipped.append(row)
            continue
        available.append(row)
    return available, skipped


def request_douyin_ai_comment(system_prompt: str, user_prompt: str, max_tokens: int = 180) -> str:
    client = create_ai_client()
    if not str(client.api_key or "").strip():
        raise RuntimeError("当前未配置 AI 接口 Key，不能使用 AI 评论模式。")
    service_label = get_douyin_ai_service_label(client.api_url)
    payload = {
        "model": client.model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
    }
    last_error: Optional[Exception] = None

    for attempt in range(1, 3):
        try:
            response = requests.post(
                client.api_url,
                json=payload,
                headers=client.headers,
                timeout=(10, 45),
            )
            if response.status_code != 200:
                body_preview = clean_douyin_video_comment_text(response.text or "", limit=80)
                detail = f"AI 接口调用失败：HTTP {response.status_code}"
                if body_preview:
                    detail = f"{detail}，{body_preview}"
                raise RuntimeError(detail)

            result = response.json()
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            cleaned = clean_douyin_video_comment_text(content)
            if not cleaned:
                raise RuntimeError("AI 没有返回可用私信内容。")
            return cleaned
        except requests.exceptions.Timeout as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5)
                continue
            raise RuntimeError(
                f"AI 接口请求超时（{service_label}），请稍后重试，或先切换到固定文案模式。"
            ) from exc
        except requests.exceptions.RequestException as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5)
                continue
            raise RuntimeError(
                f"AI 接口连接失败（{service_label}）：{exc.__class__.__name__}，请检查网络或接口配置。"
            ) from exc
        except RuntimeError as exc:
            last_error = exc
            if "HTTP 5" in str(exc) and attempt < 2:
                time.sleep(1.5)
                continue
            raise

    raise RuntimeError(str(last_error or "AI 接口调用失败。"))


def generate_douyin_video_comment_text(
    task: Dict,
    *,
    mode: Optional[str],
    fixed_text: str = "",
    prompt_text: str = "",
    seed_text: str = "",
) -> str:
    normalized = normalize_douyin_video_comment_mode(mode)
    title = str(task.get("title", "") or "").strip()
    author = str(task.get("author", "") or "").strip()
    url = str(task.get("url", "") or "").strip()

    if normalized == "fixed":
        final_text = clean_douyin_video_comment_text(fixed_text, limit=120)
        if not final_text:
            raise RuntimeError("固定文案为空，无法执行评论。")
        return final_text

    system_prompt = (
        "你是一个真实的抖音普通用户，正在给短视频写一条自然评论。\n"
        "要求：\n"
        "1. 只输出一条可直接发送的评论，不要解释。\n"
        "2. 语气自然，像真人，不要营销，不要客服腔。\n"
        "3. 不要出现私信、主页、联系方式、引流、AI、机器人等词。\n"
        "4. 控制在 10 到 32 个中文字符内，最多 36 个字符。\n"
        "5. 不要复制标题原句，要像看完视频后的真实反馈。\n"
        "6. 如果用户给了补充方向、核心观点或态度，那就是高优先级硬约束，必须围绕它来写，不能反向，不能跑题，不能弱化成无关的泛泛而谈。\n"
        "7. 如果补充方向里出现了明确判断、立场、关键词或对象，输出时必须保留这些核心信息，允许换一种更自然的说法，但不能改变意思。"
    )

    if normalized == "rewrite":
        cleaned_seed = clean_douyin_video_comment_text(seed_text, limit=120)
        if not cleaned_seed:
            raise RuntimeError("请先填写用于改编的基准文案。")
        user_prompt = (
            f"视频标题：{title or '未知'}\n"
            f"作者：{author or '未知'}\n"
            f"视频链接：{url or '未知'}\n"
            f"基准文案：{cleaned_seed}\n\n"
            "请把这条基准文案改写成一个新的版本。\n"
            "要求：方向、态度、情绪、核心意图保持一致，不要跑题，不要反向，不要变成营销文案。"
        )
        return request_douyin_ai_comment(system_prompt, user_prompt)

    direction = clean_douyin_video_comment_text(prompt_text, limit=120)
    user_prompt = (
        f"视频标题：{title or '未知'}\n"
        f"作者：{author or '未知'}\n"
        f"视频链接：{url or '未知'}\n"
        f"补充方向（高优先级，必须遵守）：{direction or '自然、真诚、有一点具体感受'}\n\n"
        "请基于视频内容生成一条自然评论。\n"
        "额外要求：\n"
        "1. 如果补充方向里有明确观点、态度、判断或关键词，评论必须围绕这个方向展开。\n"
        "2. 不能只因为视频标题带了别的内容，就把评论写成无关方向。\n"
        "3. 可以结合视频场景自然表达，但核心观点必须和补充方向一致。\n"
        "4. 不要照抄补充方向原句，可以更口语一点，但意思不能变。"
    )
    return request_douyin_ai_comment(system_prompt, user_prompt)


def generate_douyin_interaction_message(
    user: Dict,
    *,
    mode: Optional[str],
    fixed_text: str = "",
    prompt_text: str = "",
    seed_text: str = "",
) -> str:
    normalized = normalize_douyin_video_comment_mode(mode)
    username = str(user.get("username", "") or "").strip()
    comment_text = clean_douyin_video_comment_text(user.get("comment", "") or user.get("content", ""), limit=120)
    region = str(user.get("region", "") or "").strip()
    task_title = str(
        user.get("task_title", "")
        or user.get("title", "")
        or user.get("latest_video_title", "")
        or user.get("video_title", "")
        or ""
    ).strip()
    task_author = str(
        user.get("task_author", "")
        or user.get("author", "")
        or user.get("monitor_target_username", "")
        or ""
    ).strip()
    profile_url = str(user.get("profile_url", "") or "").strip()

    if normalized == "fixed":
        final_text = clean_douyin_video_comment_text(
            user.get("_interaction_fixed_message", "") or fixed_text,
            limit=120,
        )
        if not final_text:
            raise RuntimeError("固定私信文案为空，无法执行。")
        return final_text

    system_prompt = (
        "你是一个真实的抖音普通用户，准备给对方发第一条私信。\n"
        "要求：\n"
        "1. 只输出一条可直接发送的私信，不要解释。\n"
        "2. 语气自然、友好、像真人，不要客服腔，不要群发味道。\n"
        "3. 不要直接索要联系方式，不要硬广，不要夸张，不要连续追问。\n"
        "4. 控制在 14 到 48 个中文字符内，最多 56 个字符。\n"
        "5. 可以基于对方的评论和视频上下文开场，但要像真实交流，不要照抄原评论。\n"
        "6. 如果用户给了补充方向、核心观点或基准文案，必须优先遵守，不能跑偏。"
    )

    if normalized == "rewrite":
        cleaned_seed = clean_douyin_video_comment_text(seed_text, limit=120)
        if not cleaned_seed:
            raise RuntimeError("请先填写用于改编的私信基准文案。")
        user_prompt = (
            f"目标昵称：{username or '未知'}\n"
            f"对方评论：{comment_text or '未知'}\n"
            f"地区：{region or '未知'}\n"
            f"来源视频：{task_title or '未知'}\n"
            f"来源作者：{task_author or '未知'}\n"
            f"主页链接：{profile_url or '未知'}\n"
            f"基准文案：{cleaned_seed}\n\n"
            "请把这条私信改写成一个新的版本。\n"
            "要求：保持原来的方向、意图和语气，不要变成营销话术，不要太冒进。"
        )
        return request_douyin_ai_comment(system_prompt, user_prompt)

    direction = clean_douyin_video_comment_text(prompt_text, limit=120)
    user_prompt = (
        f"目标昵称：{username or '未知'}\n"
        f"对方评论：{comment_text or '未知'}\n"
        f"地区：{region or '未知'}\n"
        f"来源视频：{task_title or '未知'}\n"
        f"来源作者：{task_author or '未知'}\n"
        f"主页链接：{profile_url or '未知'}\n"
        f"补充方向（高优先级，必须遵守）：{direction or '自然开场、像同行交流、先从对方评论切入'}\n\n"
        "请生成一条适合发给对方的第一句私信。\n"
        "额外要求：\n"
        "1. 可以轻微结合对方评论或视频场景，但不要显得像复制粘贴模板。\n"
        "2. 如果补充方向里有明确观点、目标或语气，必须围绕它来写。\n"
        "3. 尽量像真实聊天开场，而不是完整销售话术。"
    )
    return request_douyin_ai_comment(system_prompt, user_prompt)


def normalize_douyin_interaction_fixed_messages(payload: Dict) -> List[str]:
    messages: List[str] = []
    raw_messages = payload.get("messages")
    if isinstance(raw_messages, list):
        for item in raw_messages:
            text = clean_douyin_video_comment_text(str(item or ""), limit=120)
            if text:
                messages.append(text)
    if not messages:
        fallback = clean_douyin_video_comment_text(str(payload.get("message", "") or ""), limit=120)
        if fallback:
            messages.append(fallback)

    deduped: List[str] = []
    seen = set()
    for message in messages:
        if message in seen:
            continue
        seen.add(message)
        deduped.append(message)
    return deduped


def assign_douyin_interaction_fixed_messages(users: List[Dict], messages: List[str]) -> None:
    if not messages:
        return
    for index, user in enumerate(users):
        user["_interaction_fixed_message"] = messages[index % len(messages)]


def generate_douyin_stranger_reply_message(
    row: Dict,
    *,
    mode: Optional[str],
    fixed_text: str = "",
    prompt_text: str = "",
    contact_value: str = "",
) -> str:
    normalized = normalize_douyin_stranger_reply_mode(mode)
    if normalized == "fixed":
        final_text = str(fixed_text or "").strip()
        if not final_text:
            raise RuntimeError("固定引流文案为空，无法执行。")
        return final_text

    cleaned_contact = normalize_douyin_text(contact_value)
    if not cleaned_contact:
        raise RuntimeError("请先填写联系方式。")

    username = str(row.get("username", "") or "").strip()
    incoming_message = clean_douyin_video_comment_text(
        row.get("incoming_message", "") or row.get("preview_text", ""),
        limit=120,
    )
    time_text = str(row.get("time_text", "") or "").strip()
    unread_count = max(0, int(row.get("unread_count", 0) or 0))
    direction = clean_douyin_video_comment_text(prompt_text, limit=120)

    system_prompt = (
        "你是一个真实的人，正在回复抖音私信。\n"
        "你的目标是自然地把对方引导到绿泡泡继续沟通，但不能显得像客服、机器人或模板群发。\n"
        "要求：\n"
        "1. 只输出前置回复内容，不要输出“麻烦您绿泡泡”，不要输出联系方式。\n"
        "2. 只输出 1 到 2 行，每行一条短消息。\n"
        "3. 每行尽量控制在 4 到 14 个中文字符内，最多 18 个字符。\n"
        "4. 语气自然、简短、像真人聊天，不要长篇解释，不要营销，不要夸张。\n"
        "5. 可以简单回应对方的问题，但不要一次讲太多细节，要为后续加绿泡泡留空间。\n"
        "6. 不要重复用户原话，不要出现 AI、系统、机器人、引流、联系方式、微信、绿泡泡 等词。\n"
        "7. 如果用户给了补充方向，必须优先遵守。"
    )
    user_prompt = (
        f"对方昵称：{username or '未知'}\n"
        f"对方最近私信：{incoming_message or '未知'}\n"
        f"消息时间：{time_text or '未知'}\n"
        f"未读数：{unread_count}\n"
        f"补充方向（高优先级，必须遵守）：{direction or '先自然回应，再顺着把对方引到绿泡泡继续聊'}\n\n"
        "请只输出 1 到 2 行简短回复，每行一句。"
    )

    ai_text = request_douyin_ai_comment(system_prompt, user_prompt, max_tokens=120)
    ai_lines: List[str] = []
    seen = set()
    for raw_line in str(ai_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = clean_douyin_message_line(raw_line, limit=18)
        if not line:
            continue
        if any(flag in line for flag in ("绿泡泡", "微信", cleaned_contact)):
            continue
        if line in seen:
            continue
        seen.add(line)
        ai_lines.append(line)
        if len(ai_lines) >= 2:
            break

    if not ai_lines:
        ai_lines = ["有的", "我发您看看"]

    return "\n".join(ai_lines + ["麻烦您绿泡泡", cleaned_contact])


def generate_douyin_inbox_reply_message(
    row: Dict,
    *,
    mode: Optional[str],
    fixed_text: str = "",
    prompt_text: str = "",
    contact_value: str = "",
) -> str:
    normalized = normalize_douyin_inbox_reply_mode(mode)
    username = str(row.get("username", "") or "").strip()
    incoming_message = clean_douyin_video_comment_text(
        row.get("incoming_message", "") or row.get("preview_text", ""),
        limit=120,
    )
    prompt_seed = clean_douyin_video_comment_text(prompt_text, limit=120)

    if normalized == "fixed":
        final_text = str(fixed_text or "").strip()
        if not final_text:
            raise RuntimeError("固定回复文案为空，无法执行。")
        return append_douyin_contact_lines(final_text, contact_value)

    cleaned_contact = normalize_douyin_text(contact_value)
    if not cleaned_contact:
        raise RuntimeError("请先填写绿泡泡联系方式。")

    system_prompt = (
        "你是一个真实的人，正在回复抖音私信。\n"
        "你的目标是先自然回应对方，再把对方引导到绿泡泡继续沟通。\n"
        "要求：\n"
        "1. 只输出前置回复内容，不要直接输出联系方式。\n"
        "2. 只输出 1 到 2 句，每句都要很短。\n"
        "3. 每句尽量控制在 4 到 18 个中文字符内。\n"
        "4. 语气自然、口语化、简短，不要客服腔，不要长篇解释。\n"
        "5. 可以简单回应对方的问题，但不要一下子把所有内容说完。\n"
        "6. 不要出现 AI、系统、机器人、引流、联系方式、微信、绿泡泡 等词。\n"
        "7. 如果给了回复方向或基准意图，必须优先遵守。"
    )

    if normalized == "rewrite":
        if not prompt_seed:
            raise RuntimeError("请先填写 AI 同方向回复的方向或基准文案。")
        user_prompt = (
            f"对方昵称：{username or '未知'}\n"
            f"对方最近私信：{incoming_message or '未知'}\n"
            f"回复方向 / 基准意图：{prompt_seed}\n\n"
            "请按这个方向改写成 1 到 2 句很短的私信回复。"
        )
    else:
        user_prompt = (
            f"对方昵称：{username or '未知'}\n"
            f"对方最近私信：{incoming_message or '未知'}\n"
            f"补充方向（可选，高优先级）：{prompt_seed or '先自然回应，再引导对方去绿泡泡继续聊'}\n\n"
            "请生成 1 到 2 句很短的私信回复。"
        )

    ai_text = request_douyin_ai_comment(system_prompt, user_prompt, max_tokens=120)
    ai_lines: List[str] = []
    seen = set()
    for raw_line in str(ai_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = clean_douyin_message_line(raw_line, limit=18)
        if not line:
            continue
        if any(flag in line for flag in ("绿泡泡", "微信", cleaned_contact)):
            continue
        if line in seen:
            continue
        seen.add(line)
        ai_lines.append(line)
        if len(ai_lines) >= 2:
            break

    if not ai_lines:
        ai_lines = ["收到", "我发您看看"]

    return append_douyin_contact_lines("\n".join(ai_lines), cleaned_contact)


def generate_douyin_self_comment_reply_message(
    row: Dict,
    *,
    mode: Optional[str],
    fixed_text: str = "",
    prompt_text: str = "",
    contact_value: str = "",
) -> str:
    normalized = normalize_douyin_inbox_reply_mode(mode)
    username = str(row.get("username", "") or "").strip()
    comment_text = clean_douyin_video_comment_text(
        row.get("comment", "") or row.get("content", ""),
        limit=120,
    )
    video_title = clean_douyin_video_comment_text(row.get("video_title", "") or row.get("title", ""), limit=120)
    prompt_seed = clean_douyin_video_comment_text(prompt_text, limit=120)

    if normalized == "fixed":
        final_text = str(fixed_text or "").strip()
        if not final_text:
            raise RuntimeError("固定回复文案为空，无法回复评论。")
        return append_douyin_contact_lines(final_text, contact_value)

    cleaned_contact = normalize_douyin_text(contact_value)
    if not cleaned_contact:
        raise RuntimeError("请先填写联系方式。")

    system_prompt = (
        "你正在以抖音作者的身份回复自己作品评论区里的潜在客户。\n"
        "要求：\n"
        "1. 只输出一条可直接发送的评论回复，不要解释。\n"
        "2. 语气自然、像真人，先回应对方评论，再轻轻引导继续沟通。\n"
        "3. 不要显得像客服或群发，不要夸张营销。\n"
        "4. 前半段不要直接输出联系方式；系统会在末尾补充联系方式。\n"
        "5. 控制在 1 到 2 句，尽量短。"
    )
    if normalized == "rewrite":
        if not prompt_seed:
            raise RuntimeError("请先填写 AI 同方向回复的方向或基准文案。")
        user_prompt = (
            f"评论用户：{username or '未知'}\n"
            f"用户评论：{comment_text or '未知'}\n"
            f"来源作品：{video_title or '未知'}\n"
            f"回复方向 / 基准意图：{prompt_seed}\n\n"
            "请按这个方向改写成一条自然的评论回复。"
        )
    else:
        user_prompt = (
            f"评论用户：{username or '未知'}\n"
            f"用户评论：{comment_text or '未知'}\n"
            f"来源作品：{video_title or '未知'}\n"
            f"补充方向：{prompt_seed or '自然回应需求，引导对方继续沟通'}\n\n"
            "请生成一条适合回复在评论区的短回复。"
        )

    ai_text = request_douyin_ai_comment(system_prompt, user_prompt, max_tokens=120)
    clean_text = clean_douyin_video_comment_text(ai_text, limit=80)
    if not clean_text:
        clean_text = "可以的，我发你看看"
    return append_douyin_contact_lines(clean_text, cleaned_contact)


def validate_douyin_inbox_reply_settings(
    *,
    auto_reply_enabled: bool,
    reply_mode: str,
    reply_message: str = "",
    reply_prompt: str = "",
    contact_value: str = "",
    reply_image_path: str = "",
) -> Optional[str]:
    if not auto_reply_enabled:
        return None
    normalized_mode = normalize_douyin_inbox_reply_mode(reply_mode)
    if normalized_mode == "fixed":
        if not str(reply_message or "").strip() and not str(reply_image_path or "").strip():
            return "开启私信聚合自动回复前，请先填写固定回复文案。"
        return None
    if normalized_mode == "rewrite" and not str(reply_prompt or "").strip():
        return "开启 AI 同方向回复前，请先填写回复方向或基准文案。"
    if not normalize_douyin_text(contact_value):
        return "开启 AI 自动回复前，请先填写绿泡泡联系方式。"
    return None


restore_douyin_tasks_state()
restore_douyin_customer_pools_state()
restore_douyin_group_member_results()
restore_douyin_schedule_plans()
if not douyin_all_customer_pool and not douyin_precise_customer_pool and douyin_tasks:
    save_douyin_tasks_state()


def get_next_monitor_run_time(now: Optional[datetime] = None) -> datetime:
    current = now or datetime.now()
    candidates = []
    for hour, minute, _label in DOUYIN_MONITOR_SLOTS:
        slot = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if slot > current:
            candidates.append(slot)
    if candidates:
        return min(candidates)
    next_day = current + timedelta(days=1)
    return next_day.replace(hour=DOUYIN_MONITOR_SLOTS[0][0], minute=DOUYIN_MONITOR_SLOTS[0][1], second=0, microsecond=0)


def get_latest_due_monitor_slot(now: Optional[datetime] = None) -> tuple[Optional[str], Optional[datetime]]:
    current = now or datetime.now()
    due_slots: List[tuple[datetime, str]] = []
    for hour, minute, label in DOUYIN_MONITOR_SLOTS:
        slot_time = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if current >= slot_time:
            due_slots.append((slot_time, f"{current.strftime('%Y-%m-%d')} {label}"))
    if not due_slots:
        return None, None
    due_slots.sort(key=lambda item: item[0])
    latest = due_slots[-1]
    return latest[1], latest[0]


def build_monitor_comment_key(row: Dict) -> str:
    comment_id = normalize_douyin_text(row.get("comment_id", ""))
    if comment_id:
        return f"id:{comment_id}"
    return "|".join(
        [
            normalize_douyin_text(row.get("user_id", "")),
            normalize_douyin_text(row.get("username", "")),
            normalize_douyin_text(row.get("comment", row.get("content", ""))),
            normalize_douyin_text(row.get("comment_time", "")),
        ]
    )


def build_monitor_user_key(row: Dict) -> str:
    sec_user_id = normalize_douyin_text(row.get("sec_user_id", ""))
    user_id = normalize_douyin_text(row.get("user_id", ""))
    profile_url = normalize_douyin_text(row.get("profile_url", ""))
    username = normalize_douyin_text(row.get("username", ""))
    return "|".join([sec_user_id, user_id, profile_url, username])


def build_monitor_comment_match_keys(row: Dict) -> set[str]:
    if not isinstance(row, dict):
        return set()
    normalized = normalize_high_intent_user(row)
    normalized["profile_url"] = ensure_douyin_profile_url(normalized)
    keys: set[str] = set()

    comment_key = build_monitor_comment_key(normalized)
    if comment_key:
        keys.add(f"comment:{comment_key}")
    strict_key = user_choice_key(normalized)
    if strict_key:
        keys.add(f"strict:{strict_key}")
    loose_key = user_choice_loose_key(normalized)
    if loose_key:
        keys.add(f"loose:{loose_key}")

    comment_index = normalize_douyin_text(normalized.get("comment_index", ""))
    if comment_index:
        keys.add(f"index:{comment_index}")
    content = normalize_douyin_text(normalized.get("comment", normalized.get("content", "")))
    username = normalize_douyin_text(normalized.get("username", ""))
    user_id = normalize_douyin_text(normalized.get("user_id", ""))
    sec_user_id = normalize_douyin_text(normalized.get("sec_user_id", ""))
    profile_url = normalize_douyin_text(normalized.get("profile_url", ""))
    if username and content:
        keys.add(f"username_content:{username}|{content}")
    if user_id and content:
        keys.add(f"user_content:{user_id}|{content}")
    if sec_user_id and content:
        keys.add(f"sec_content:{sec_user_id}|{content}")
    if profile_url and content:
        keys.add(f"profile_content:{profile_url}|{content}")

    user_key = build_monitor_user_key(normalized)
    if user_key and (user_id or sec_user_id or profile_url):
        keys.add(f"user:{user_key}")
    return keys


def build_monitor_precise_user_index(precise_users: List[Dict]) -> Dict[str, Dict]:
    index: Dict[str, Dict] = {}
    for user in precise_users or []:
        if not isinstance(user, dict):
            continue
        normalized = normalize_high_intent_user(user)
        normalized["profile_url"] = ensure_douyin_profile_url(normalized)
        for key in build_monitor_comment_match_keys(normalized):
            index.setdefault(key, normalized)
    return index


def find_monitor_precise_match(row: Dict, precise_index: Dict[str, Dict]) -> Optional[Dict]:
    if not precise_index:
        return None
    for key in build_monitor_comment_match_keys(row):
        match = precise_index.get(key)
        if match:
            return match
    return None


def parse_monitor_video_sort_timestamp(video: Dict) -> float:
    for key in ("publish_timestamp", "create_time"):
        try:
            value = float(video.get(key, 0) or 0)
        except Exception:
            value = 0
        if value > 0:
            return value
    for key in ("publish_time", "publish_time_text"):
        value = str(video.get(key, "") or "").strip()
        if not value:
            continue
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt).timestamp()
            except Exception:
                continue
    return 0


def parse_monitor_video_seen_timestamp(video: Dict) -> float:
    for key in ("last_seen_at", "updated_at"):
        value = str(video.get(key, "") or "").strip()
        if not value:
            continue
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt).timestamp()
            except Exception:
                continue
    return 0


def normalize_monitor_author_name(value: object) -> str:
    text = normalize_douyin_text(value)
    for suffix in ("的抖音", "抖音号", " - 抖音", "- 抖音", " 抖音"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text


def extract_monitor_video_author_sec_user_id(video: Dict) -> str:
    if not isinstance(video, dict):
        return ""
    for key in ("author_sec_user_id", "author_sec_uid"):
        value = normalize_douyin_text(video.get(key, ""))
        if value:
            return value
    raw = video.get("raw") if isinstance(video.get("raw"), dict) else {}
    author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
    for key in ("sec_uid", "sec_user_id"):
        value = normalize_douyin_text(author.get(key, ""))
        if value:
            return value
    return ""


def is_monitor_video_for_target(video: Dict, target: Dict) -> bool:
    if not isinstance(video, dict) or not isinstance(target, dict):
        return True
    target_sec_user_id = normalize_douyin_text(target.get("sec_user_id", ""))
    video_author_sec_user_id = extract_monitor_video_author_sec_user_id(video)
    if target_sec_user_id and video_author_sec_user_id:
        return target_sec_user_id == video_author_sec_user_id

    target_name = normalize_monitor_author_name(target.get("username", ""))
    video_author = normalize_monitor_author_name(video.get("author", ""))
    if target_name and video_author:
        return target_name == video_author

    return True


def filter_monitor_videos_for_target(videos: List[Dict], target: Dict) -> List[Dict]:
    return [video for video in (videos or []) if is_monitor_video_for_target(video, target)]


def load_monitor_targets_bundle() -> List[Dict]:
    targets = douyin_state_store.load_douyin_monitor_targets()
    videos = douyin_state_store.load_douyin_monitor_videos()
    videos_by_target: Dict[int, List[Dict]] = {}
    for video in videos:
        target_id = int(video.get("target_id", 0) or 0)
        if target_id <= 0:
            continue
        videos_by_target.setdefault(target_id, []).append(video)

    result = []
    for target in targets:
        if str(target.get("status", "active") or "active").strip() != "active":
            continue
        target_id = int(target.get("target_id", 0) or 0)
        target_videos = filter_monitor_videos_for_target(videos_by_target.get(target_id, []), target)
        target_videos.sort(
            key=lambda row: (
                1 if parse_monitor_video_sort_timestamp(row) > 0 else 0,
                parse_monitor_video_sort_timestamp(row),
                parse_monitor_video_seen_timestamp(row),
                -int(row.get("video_order", 9999) or 9999),
                str(row.get("aweme_id", "") or ""),
            ),
            reverse=True,
        )
        result.append(
            {
                **target,
                "videos": target_videos[:10],
                "video_count": len(target_videos),
                "selected_video_count": sum(1 for video in target_videos if video.get("is_selected")),
                "new_video_count": sum(1 for video in target_videos if video.get("is_new")),
            }
        )
    return result


def distribute_monitor_video_jobs(jobs: List[Dict], accounts: List[Dict]) -> List[Dict]:
    active_accounts = accounts[: max(1, min(len(accounts), len(jobs)))]
    buckets = [{"account": account, "jobs": []} for account in active_accounts]
    for index, job in enumerate(jobs):
        buckets[index % len(buckets)]["jobs"].append(job)
    return [bucket for bucket in buckets if bucket["jobs"]]


def merge_monitor_videos(
    existing_videos: List[Dict],
    fetched_videos: List[Dict],
    *,
    initial_sync: bool,
    auto_collect_new: bool,
) -> List[Dict]:
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing_by_aweme = {
        str(video.get("aweme_id", "") or "").strip(): dict(video)
        for video in existing_videos or []
        if isinstance(video, dict) and str(video.get("aweme_id", "") or "").strip()
    }
    merged = []
    seen = set()
    for index, raw_video in enumerate(fetched_videos or [], start=1):
        if not isinstance(raw_video, dict):
            continue
        aweme_id = str(raw_video.get("aweme_id", "") or "").strip()
        if not aweme_id or aweme_id in seen:
            continue
        seen.add(aweme_id)
        existing = existing_by_aweme.get(aweme_id, {})
        is_existing = bool(existing)
        merged.append(
            {
                **existing,
                **raw_video,
                "aweme_id": aweme_id,
                "video_order": index,
                "first_seen_at": str(existing.get("first_seen_at", "") or now_text),
                "last_seen_at": now_text,
                "last_collected_at": str(existing.get("last_collected_at", "") or ""),
                "is_selected": bool(
                    existing.get("is_selected", False)
                    if is_existing
                    else (False if initial_sync else auto_collect_new)
                ),
                "is_new": not is_existing,
            }
        )

    for aweme_id, existing in existing_by_aweme.items():
        if aweme_id in seen:
            continue
        merged.append(
            {
                **existing,
                "is_new": False,
            }
        )
    merged.sort(key=lambda row: (int(row.get("video_order", 9999) or 9999), row.get("last_seen_at", "")))
    return merged


def select_monitor_videos_for_collection(
    videos: List[Dict],
    *,
    auto_collect_new: bool = True,
) -> List[Dict]:
    selected: List[Dict] = []
    seen_aweme_ids: Set[str] = set()
    for video in videos or []:
        if not isinstance(video, dict):
            continue
        aweme_id = str(video.get("aweme_id", "") or "").strip()
        if not aweme_id or aweme_id in seen_aweme_ids:
            continue
        should_collect = bool(video.get("is_selected")) or (
            bool(auto_collect_new)
            and bool(video.get("is_new"))
            and not str(video.get("last_collected_at", "") or "").strip()
        )
        if not should_collect:
            continue
        selected.append(video)
        seen_aweme_ids.add(aweme_id)
    return selected


def resolve_monitor_target_profile_identity(target: Dict) -> str:
    profile_url = str(target.get("profile_url", "") or "").strip()
    if profile_url:
        return profile_url
    return str(target.get("sec_user_id", "") or "").strip()


async def prepare_douyin_monitor_protocol_channel(account: Dict, *, start_url: str = "https://www.douyin.com/user/self?from_tab_name=main") -> tuple[object, object]:
    from douyin_comment_api_experiment import DouyinCommentApiExperiment

    browser_result = await ensure_douyin_account_browser_ready_async(account, start_url=start_url)
    if int(browser_result.get("code", 0) or 0) != 200:
        raise RuntimeError(str(browser_result.get("msg", "") or "账号浏览器预热失败"))
    protocol_client = DouyinCommentApiExperiment(
        account_id=account["id"],
        cdp_port=account["port"],
    )
    protocol_auth = await protocol_client.extract_auth()
    return protocol_client, protocol_auth


def build_monitor_comment_rows(
    target: Dict,
    video: Dict,
    comments: List[Dict],
    precise_users: List[Dict],
) -> List[Dict]:
    precise_index = build_monitor_precise_user_index(precise_users)
    rows = []
    for comment in comments or []:
        if not isinstance(comment, dict):
            continue
        normalized = normalize_high_intent_user(comment)
        normalized["profile_url"] = ensure_douyin_profile_url(normalized)
        comment_key = build_monitor_comment_key(normalized)
        user_key = build_monitor_user_key(normalized)
        if not comment_key:
            continue
        precise_match = find_monitor_precise_match(normalized, precise_index)
        if precise_match:
            for key in ("intent_level", "score", "reason"):
                value = precise_match.get(key, "")
                if value not in (None, ""):
                    normalized[key] = value
        rows.append(
            {
                **normalized,
                "target_id": int(target.get("target_id", 0) or 0),
                "target_username": str(target.get("username", "") or ""),
                "target_profile_url": str(target.get("profile_url", "") or ""),
                "target_sec_user_id": str(target.get("sec_user_id", "") or ""),
                "aweme_id": str(video.get("aweme_id", "") or ""),
                "video_title": str(video.get("title", "") or ""),
                "video_url": str(video.get("url", "") or ""),
                "video_cover_image": str(video.get("cover_image", "") or ""),
                "comment_key": comment_key,
                "user_key": user_key,
                "is_high_intent": bool(precise_match),
                "source": "douyin_monitor",
            }
        )
    return rows


def merge_monitor_action_state(target: Dict, comment: Dict, prefix: str, extra_fields: List[str]) -> None:
    status_key = f"{prefix}_status"
    incoming_status = str(comment.get(status_key, "") or "").strip()
    if not incoming_status:
        return

    current_status = str(target.get(status_key, "") or "").strip()
    status_priority = {
        "": 0,
        "pending": 1,
        "queued": 2,
        "processing": 3,
        "failed": 4,
        "interrupted": 4,
        "skipped": 4,
        "completed": 5,
        "sent": 5,
    }
    incoming_updated = str(comment.get(f"{prefix}_updated_at", "") or "")
    current_updated = str(target.get(f"{prefix}_updated_at", "") or "")
    should_replace = (
        status_priority.get(incoming_status, 1) > status_priority.get(current_status, 1)
        or (incoming_updated and incoming_updated >= current_updated)
    )
    if not should_replace:
        return

    target[status_key] = incoming_status
    for field in extra_fields:
        key = f"{prefix}_{field}"
        if key in comment:
            target[key] = comment.get(key, "")


def build_monitor_customer_pools(target_id: int | None = None) -> tuple[List[Dict], List[Dict]]:
    targets = {
        int(item.get("target_id", 0) or 0): item
        for item in douyin_state_store.load_douyin_monitor_targets()
    }
    videos = {
        (int(item.get("target_id", 0) or 0), str(item.get("aweme_id", "") or "").strip()): item
        for item in douyin_state_store.load_douyin_monitor_videos(target_id)
    }
    comments = douyin_state_store.load_douyin_monitor_comments(target_id)

    all_rows_map: Dict[str, Dict] = {}
    precise_rows_map: Dict[str, Dict] = {}

    for comment in comments:
        if not isinstance(comment, dict):
            continue
        target_ref = targets.get(int(comment.get("target_id", 0) or 0), {})
        video_ref = videos.get(
            (
                int(comment.get("target_id", 0) or 0),
                str(comment.get("aweme_id", "") or "").strip(),
            ),
            {},
        )
        user_key = build_monitor_user_key(comment)
        if not user_key:
            continue
        target_bucket = all_rows_map.get(user_key)
        last_seen_at = str(comment.get("last_seen_at", "") or "")
        source_video_key = f"{comment.get('target_id', 0)}|{comment.get('aweme_id', '')}"
        if not target_bucket:
            target_bucket = {
                "target_id": int(comment.get("target_id", 0) or 0),
                "monitor_target_id": int(comment.get("target_id", 0) or 0),
                "target_username": str(target_ref.get("username", comment.get("target_username", "")) or ""),
                "monitor_target_username": str(target_ref.get("username", comment.get("target_username", "")) or ""),
                "target_profile_url": str(target_ref.get("profile_url", comment.get("target_profile_url", "")) or ""),
                "monitor_target_profile_url": str(target_ref.get("profile_url", comment.get("target_profile_url", "")) or ""),
                "target_avatar_url": str(target_ref.get("avatar_url", "") or ""),
                "monitor_target_avatar_url": str(target_ref.get("avatar_url", "") or ""),
                "username": str(comment.get("username", "") or ""),
                "user_id": str(comment.get("user_id", "") or ""),
                "sec_user_id": str(comment.get("sec_user_id", "") or ""),
                "profile_url": ensure_douyin_profile_url(comment),
                "avatar_url": str(comment.get("avatar_url", comment.get("avatar", "")) or ""),
                "comment": str(comment.get("comment", comment.get("content", "")) or ""),
                "content": str(comment.get("comment", comment.get("content", "")) or ""),
                "comment_time": str(comment.get("comment_time", "") or ""),
                "region": str(comment.get("region", comment.get("location", "")) or ""),
                "last_seen_at": last_seen_at,
                "first_seen_at": str(comment.get("first_seen_at", "") or ""),
                "source_video_count": 0,
                "comment_count": 0,
                "seen_count_total": 0,
                "is_high_intent": False,
                "interaction_status": "pending",
                "interaction_error": "",
                "interaction_message": "",
                "interaction_account_id": "",
                "interaction_started_at": "",
                "interaction_finished_at": "",
                "interaction_updated_at": "",
                "follow_comment_status": "pending",
                "follow_comment_error": "",
                "follow_comment_text": "",
                "follow_comment_account_id": "",
                "follow_comment_result": "",
                "follow_comment_started_at": "",
                "follow_comment_finished_at": "",
                "follow_comment_updated_at": "",
                "latest_video_title": str(video_ref.get("title", comment.get("video_title", "")) or ""),
                "latest_video_url": str(video_ref.get("url", comment.get("video_url", "")) or ""),
                "video_cover_image": str(video_ref.get("cover_image", comment.get("video_cover_image", "")) or ""),
                "source": "douyin_monitor",
                "_source_videos": set(),
            }
            all_rows_map[user_key] = target_bucket
        if last_seen_at >= str(target_bucket.get("last_seen_at", "") or ""):
            target_bucket["comment"] = str(comment.get("comment", comment.get("content", "")) or "")
            target_bucket["content"] = str(comment.get("comment", comment.get("content", "")) or "")
            target_bucket["comment_time"] = str(comment.get("comment_time", "") or "")
            target_bucket["region"] = str(comment.get("region", comment.get("location", "")) or "")
            target_bucket["last_seen_at"] = last_seen_at
            target_bucket["latest_video_title"] = str(video_ref.get("title", comment.get("video_title", "")) or "")
            target_bucket["latest_video_url"] = str(video_ref.get("url", comment.get("video_url", "")) or "")
            target_bucket["video_cover_image"] = str(video_ref.get("cover_image", comment.get("video_cover_image", "")) or "")
        target_bucket["first_seen_at"] = min(
            [value for value in [str(target_bucket.get("first_seen_at", "") or ""), str(comment.get("first_seen_at", "") or "")] if value] or [""]
        )
        target_bucket["comment_count"] = int(target_bucket.get("comment_count", 0) or 0) + 1
        target_bucket["seen_count_total"] = int(target_bucket.get("seen_count_total", 0) or 0) + int(comment.get("seen_count", 0) or 0)
        target_bucket["is_high_intent"] = bool(target_bucket.get("is_high_intent")) or bool(comment.get("is_high_intent"))
        merge_monitor_action_state(
            target_bucket,
            comment,
            "interaction",
            ["error", "message", "account_id", "started_at", "finished_at", "updated_at"],
        )
        merge_monitor_action_state(
            target_bucket,
            comment,
            "follow_comment",
            ["error", "text", "account_id", "result", "started_at", "finished_at", "updated_at"],
        )
        target_bucket["_source_videos"].add(source_video_key)

        if comment.get("is_high_intent"):
            precise_rows_map[user_key] = target_bucket

    all_rows = []
    for row in all_rows_map.values():
        row["source_video_count"] = len(row.pop("_source_videos", set()))
        all_rows.append(row)
    precise_rows = []
    for row in precise_rows_map.values():
        precise_rows.append(dict(row))

    all_rows.sort(key=lambda row: (str(row.get("last_seen_at", "") or ""), str(row.get("comment_time", "") or "")), reverse=True)
    precise_rows.sort(key=lambda row: (str(row.get("last_seen_at", "") or ""), str(row.get("comment_time", "") or "")), reverse=True)
    return all_rows, precise_rows


async def sync_monitor_target_profile_and_videos(
    target: Dict,
    scraper: DouyinCommentScraper,
    *,
    initial_sync: bool,
    protocol_client: object = None,
    protocol_auth: object = None,
) -> Dict:
    payload = {}
    sync_method = "script"
    protocol_error = ""
    profile_url = str(target.get("profile_url", "") or "")
    profile_identity = resolve_monitor_target_profile_identity(target)
    if protocol_client is not None and protocol_auth is not None:
        try:
            payload = await asyncio.to_thread(
                lambda: protocol_client.get_user_posts(
                    protocol_auth,
                    profile_identity,
                    max_videos=10,
                )
            )
            sync_method = "protocol"
            douyin_log(
                f"[抖音同行监控] 已通过协议同步同行最新作品：{target.get('username') or profile_url}",
                "info",
            )
        except Exception as exc:
            protocol_error = str(exc)
            douyin_log(
                f"[抖音同行监控] 协议同步同行作品失败，自动回退页面同步：{target.get('username') or profile_url}，原因：{exc}",
                "warning",
            )
            payload = {}
    if not payload:
        payload = await scraper.scrape_profile_videos(
            profile_url,
            max_videos=10,
            logger=douyin_log,
        )
    profile = payload.get("profile", {}) if isinstance(payload, dict) else {}
    fetched_videos = payload.get("videos", []) if isinstance(payload, dict) else []
    existing_videos = douyin_state_store.load_douyin_monitor_videos(int(target.get("target_id", 0) or 0))
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    merged_target = douyin_state_store.save_douyin_monitor_target(
        {
            **target,
            **profile,
            "profile_url": str(profile.get("profile_url", target.get("profile_url", "")) or ""),
            "last_video_sync_at": now_text,
            "next_run_at": get_next_monitor_run_time().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "active",
            "last_video_sync_method": sync_method,
            "last_video_sync_error": protocol_error,
        }
    )
    merged_videos = merge_monitor_videos(
        existing_videos,
        fetched_videos,
        initial_sync=initial_sync,
        auto_collect_new=bool(merged_target.get("auto_collect_new", True)),
    )
    douyin_state_store.save_douyin_monitor_videos(int(merged_target.get("target_id", 0) or 0), merged_videos)
    douyin_state_store.append_douyin_monitor_video_snapshots(
        [
            {
                "target_id": int(merged_target.get("target_id", 0) or 0),
                "aweme_id": str(video.get("aweme_id", "") or ""),
                "captured_at": now_text,
                "likes": int(video.get("likes", 0) or 0),
                "comments": int(video.get("comments", 0) or 0),
                "video_title": str(video.get("title", "") or ""),
                "video_url": str(video.get("url", "") or ""),
            }
            for video in merged_videos
            if str(video.get("aweme_id", "") or "").strip()
        ]
    )
    visible_videos = filter_monitor_videos_for_target(merged_videos, merged_target)
    return {
        **merged_target,
        "videos": visible_videos[:10],
    }


async def run_douyin_monitor_comment_worker(
    jobs: List[Dict],
    account: Dict,
    config: Dict,
    state_lock: asyncio.Lock,
    run_payload: Dict,
):
    scraper = DouyinCommentScraper(account_id=account["id"], cdp_port=account["port"])
    ai_client = create_ai_client()
    protocol_client = None
    protocol_auth = None
    try:
        try:
            from douyin_comment_api_experiment import DouyinCommentApiExperiment

            warmup_url = str((jobs[0] if jobs else {}).get("video", {}).get("url", "") or "").strip()
            if not warmup_url:
                warmup_url = "https://www.douyin.com/user/self?from_tab_name=main"
            browser_result = await ensure_douyin_account_browser_ready_async(account, start_url=warmup_url)
            if int(browser_result.get("code", 0) or 0) == 200:
                protocol_client = DouyinCommentApiExperiment(account_id=account["id"], cdp_port=account["port"])
                protocol_auth = await protocol_client.extract_auth()
                douyin_log(
                    f"[抖音同行监控] 账号 {account['id']} 已准备协议评论采集通道，失败时会自动回退页面脚本。",
                    "info",
                )
            else:
                douyin_log(
                    f"[抖音同行监控] 账号 {account['id']} 浏览器预热失败，评论采集将使用页面脚本：{browser_result.get('msg', '')}",
                    "warning",
                )
        except Exception as exc:
            protocol_client = None
            protocol_auth = None
            douyin_log(
                f"[抖音同行监控] 账号 {account['id']} 协议评论采集通道不可用，将使用页面脚本：{exc}",
                "warning",
            )
        douyin_log(
            f"[抖音同行监控] 账号 {account['id']} 已接管 {len(jobs)} 条视频采集任务",
            "info",
        )
        for job in jobs:
            if not isinstance(job, dict):
                continue
            target = job.get("target", {}) if isinstance(job.get("target"), dict) else {}
            video = job.get("video", {}) if isinstance(job.get("video"), dict) else {}
            target_id = int(job.get("target_id", target.get("target_id", 0)) or 0)
            aweme_id = str(job.get("aweme_id", video.get("aweme_id", "")) or "").strip()
            if target_id <= 0 or not aweme_id:
                continue

            douyin_log(
                f"[抖音同行监控] 账号 {account['id']} 开始采集视频：{video.get('title') or video.get('url')}",
                "info",
            )
            max_comments = max(20, min(int(config.get("comment_max_comments", 200) or 200), 500))
            comments = []
            collection_method = "script"
            if protocol_client is not None and protocol_auth is not None:
                try:
                    comments_payload = await asyncio.to_thread(
                        lambda: protocol_client.get_all_comments(
                            protocol_auth,
                            str(video.get("url", "") or ""),
                            max_comments=max_comments,
                        )
                    )
                    raw_comments = comments_payload.get("comments", []) if isinstance(comments_payload, dict) else []
                    comments = [
                        normalize_douyin_protocol_comment_row(row, task=video, index=index)
                        for index, row in enumerate(raw_comments)
                        if isinstance(row, dict)
                    ]
                    collection_method = "protocol"
                    douyin_log(
                        f"[抖音同行监控] 账号 {account['id']} 已通过协议采集视频评论 {len(comments)} 条：{video.get('title') or video.get('url')}",
                        "info",
                    )
                except Exception as exc:
                    douyin_log(
                        f"[抖音同行监控] 协议采集评论失败，自动回退页面脚本：{video.get('title') or video.get('url')}，原因：{exc}",
                        "warning",
                    )
                    comments = []
            if collection_method != "protocol":
                comments = await scraper.scrape_video_comments(
                    str(video.get("url", "") or ""),
                    max_comments=max_comments,
                    max_scroll_rounds=max(20, min(int(config.get("comment_scroll_rounds", 120) or 120), 300)),
                    logger=douyin_log,
                )
            existing_comment_keys = {
                str(item.get("comment_key", "") or "")
                for item in douyin_state_store.load_douyin_monitor_comments(target_id)
                if str(item.get("aweme_id", "") or "").strip() == aweme_id
            }
            fresh_comments = []
            for comment in comments:
                key = build_monitor_comment_key(comment)
                if not key or key in existing_comment_keys:
                    continue
                fresh_comments.append(comment)

            precise_users = []
            if fresh_comments:
                _filter_prompt = get_douyin_comment_direction(config)
                _filter_strategy = get_douyin_comment_filter_strategy(config)
                _filter_started_at = time.time()
                log_douyin_filter_event(
                    "start",
                    scope="monitor_cycle",
                    target_id=int(target_id or 0),
                    aweme_id=str(aweme_id or ""),
                    title=video.get("title", ""),
                    comments_in=len(fresh_comments),
                    strategy=_filter_strategy,
                    prompt=_filter_prompt,
                )
                try:
                    precise_users = await asyncio.to_thread(
                        lambda: ai_client.filter_comments(
                            str(video.get("title", "") or ""),
                            fresh_comments,
                            _filter_prompt,
                            "douyin_transactional",
                            "",
                            _filter_strategy,
                            event_logger=log_douyin_filter_event,
                        )
                    )
                except Exception as _filter_exc:
                    log_douyin_filter_event(
                        "error",
                        scope="monitor_cycle",
                        target_id=int(target_id or 0),
                        aweme_id=str(aweme_id or ""),
                        title=video.get("title", ""),
                        comments_in=len(fresh_comments),
                        strategy=_filter_strategy,
                        duration_ms=int((time.time() - _filter_started_at) * 1000),
                        error=str(_filter_exc),
                    )
                    raise
                log_douyin_filter_event(
                    "done",
                    scope="monitor_cycle",
                    target_id=int(target_id or 0),
                    aweme_id=str(aweme_id or ""),
                    title=video.get("title", ""),
                    comments_in=len(fresh_comments),
                    precise_out=len(precise_users or []),
                    strategy=_filter_strategy,
                    duration_ms=int((time.time() - _filter_started_at) * 1000),
                )
                comment_rows = build_monitor_comment_rows(target, video, fresh_comments, precise_users)
                for row in comment_rows:
                    row["collect_method"] = collection_method
                douyin_state_store.save_douyin_monitor_comments(comment_rows)
                async with state_lock:
                    run_payload["new_comments"] = int(run_payload.get("new_comments", 0) or 0) + len(comment_rows)
                    run_payload["new_precise"] = int(run_payload.get("new_precise", 0) or 0) + len(precise_users)

            async with state_lock:
                videos = douyin_state_store.load_douyin_monitor_videos(target_id)
                now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                updated = False
                for item in videos:
                    if str(item.get("aweme_id", "") or "").strip() != aweme_id:
                        continue
                    item["last_collected_at"] = now_text
                    item["is_new"] = False
                    updated = True
                    break
                if updated:
                    douyin_state_store.save_douyin_monitor_videos(target_id, videos)
    finally:
        await scraper.close()


async def run_douyin_monitor_cycle(
    *,
    trigger_type: str = "manual",
    target_ids: Optional[List[int]] = None,
    slot_key: str = "",
) -> Dict:
    global douyin_monitor_runtime_state
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    next_run_at = get_next_monitor_run_time().strftime("%Y-%m-%d %H:%M:%S")
    douyin_monitor_runtime_state.update(
        {
            "running": True,
            "message": "同行监控执行中",
            "last_error": "",
            "next_run_at": next_run_at,
        }
    )

    run_payload = {
        "trigger_type": trigger_type,
        "slot_key": slot_key,
        "status": "running",
        "started_at": started_at,
        "finished_at": "",
        "target_ids": target_ids or [],
        "targets_total": 0,
        "videos_total": 0,
        "new_comments": 0,
        "new_precise": 0,
        "skipped_targets_no_selection": 0,
        "error": "",
        "message": "",
    }

    config = load_global_config()
    nurture_conflict = build_douyin_nurture_conflict("执行同行监控")
    if nurture_conflict:
        message = str(nurture_conflict.get("msg", "") or "账号养号正在运行，无法执行同行监控。")
        finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        douyin_monitor_runtime_state.update(
            {
                "running": False,
                "message": message,
                "last_error": message,
                "last_run_at": finished_at,
                "next_run_at": next_run_at,
            }
        )
        run_payload.update({"status": "failed", "finished_at": finished_at, "error": message, "message": message})
        douyin_state_store.save_douyin_monitor_run(run_payload)
        return run_payload
    accounts = get_online_douyin_accounts(config)
    if not accounts:
        message = "当前没有在线抖音账号，无法执行同行监控。"
        douyin_monitor_runtime_state.update(
            {
                "running": False,
                "message": message,
                "last_error": message,
                "last_run_at": started_at,
                "next_run_at": next_run_at,
            }
        )
        run_payload.update({"status": "failed", "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "error": message})
        douyin_state_store.save_douyin_monitor_run(run_payload)
        return run_payload

    sync_account = accounts[0]
    normalized_target_ids = {int(target_id) for target_id in (target_ids or [])}
    bundle = load_monitor_targets_bundle()
    active_targets = [
        item for item in bundle
        if str(item.get("status", "active") or "active").strip() == "active"
        and (not normalized_target_ids or int(item.get("target_id", 0) or 0) in normalized_target_ids)
    ]
    run_payload["targets_total"] = len(active_targets)

    scraper = DouyinCommentScraper(account_id=sync_account["id"], cdp_port=sync_account["port"])
    try:
        sync_protocol_client = None
        sync_protocol_auth = None
        try:
            sync_protocol_client, sync_protocol_auth = await prepare_douyin_monitor_protocol_channel(sync_account)
            douyin_log(
                f"[抖音同行监控] 账号 {sync_account['id']} 已准备协议作品同步通道。",
                "info",
            )
        except Exception as exc:
            sync_protocol_client = None
            sync_protocol_auth = None
            douyin_log(
                f"[抖音同行监控] 协议作品同步通道不可用，将使用页面方式：{exc}",
                "warning",
            )

        synced_targets_map: Dict[int, Dict] = {}
        jobs: List[Dict] = []
        for target in active_targets:
            target_id = int(target.get("target_id", 0) or 0)
            if target_id <= 0:
                continue
            douyin_log(
                f"[抖音同行监控] 账号 {sync_account['id']} 开始同步同行：{target.get('username') or target.get('profile_url')}",
                "info",
            )
            synced = await sync_monitor_target_profile_and_videos(
                target,
                scraper,
                initial_sync=False,
                protocol_client=sync_protocol_client,
                protocol_auth=sync_protocol_auth,
            )
            synced_targets_map[target_id] = synced
            videos = synced.get("videos", []) if isinstance(synced, dict) else []
            auto_collect_new = bool(synced.get("auto_collect_new", True))
            candidate_videos = select_monitor_videos_for_collection(
                videos,
                auto_collect_new=auto_collect_new,
            )
            if not candidate_videos:
                run_payload["skipped_targets_no_selection"] = int(run_payload.get("skipped_targets_no_selection", 0) or 0) + 1
                douyin_log(
                    f"[抖音同行监控] 本轮无需要采集的视频：{synced.get('username') or synced.get('profile_url')}。已同步最新作品，未发现自动采集的新视频或手动勾选视频。",
                    "warning",
                )
            run_payload["videos_total"] = int(run_payload.get("videos_total", 0) or 0) + len(candidate_videos)
            for video in candidate_videos:
                aweme_id = str(video.get("aweme_id", "") or "").strip()
                if not aweme_id:
                    continue
                jobs.append(
                    {
                        "target": synced,
                        "video": video,
                        "target_id": target_id,
                        "aweme_id": aweme_id,
                    }
                )

        used_account_count = 1
        if jobs:
            batches = distribute_monitor_video_jobs(jobs, accounts)
            used_account_count = len(batches)
            state_lock = asyncio.Lock()
            douyin_log(
                f"[抖音同行监控] 已启动 {used_account_count} 个账号并发，共 {len(jobs)} 条视频采集任务",
                "info",
            )
            await asyncio.gather(
                *[
                    run_douyin_monitor_comment_worker(
                        item["jobs"],
                        item["account"],
                        config,
                        state_lock,
                        run_payload,
                    )
                    for item in batches
                ]
            )

        finished_collect_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for synced in synced_targets_map.values():
            douyin_state_store.save_douyin_monitor_target(
                {
                    **synced,
                    "last_collect_at": finished_collect_at,
                    "next_run_at": next_run_at,
                }
            )

        finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        run_payload.update(
            {
                "status": "completed",
                "finished_at": finished_at,
            }
        )
        if int(run_payload.get("videos_total", 0) or 0) <= 0:
            message = f"同行监控已完成，使用 {used_account_count} 个账号同步，本轮没有勾选任何监控视频，只同步了主页和视频列表。"
        else:
            message = (
                f"同行监控已完成，使用 {used_account_count} 个账号并发，本轮新增评论 {run_payload['new_comments']} 条，新增精准客户 {run_payload['new_precise']} 人。"
            )
            skipped = int(run_payload.get("skipped_targets_no_selection", 0) or 0)
            if skipped > 0:
                message += f" 另有 {skipped} 个同行因未勾选视频而只做了视频同步。"
        run_payload["message"] = message
        douyin_monitor_runtime_state.update(
            {
                "running": False,
                "message": message,
                "last_run_at": finished_at,
                "next_run_at": next_run_at,
                "last_slot_key": slot_key,
            }
        )
        douyin_log(douyin_monitor_runtime_state["message"], "success")
        douyin_state_store.save_douyin_monitor_run(run_payload)
        return run_payload
    except Exception as exc:
        finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        douyin_monitor_runtime_state.update(
            {
                "running": False,
                "message": f"同行监控失败：{exc}",
                "last_error": str(exc),
                "last_run_at": finished_at,
                "next_run_at": next_run_at,
            }
        )
        run_payload.update({"status": "failed", "finished_at": finished_at, "error": str(exc), "message": str(exc)})
        douyin_log(f"[抖音同行监控] 失败：{exc}", "error")
        douyin_state_store.save_douyin_monitor_run(run_payload)
        raise
    finally:
        await scraper.close()


async def douyin_monitor_scheduler_loop():
    global douyin_monitor_runtime_state
    while True:
        try:
            now = datetime.now()
            next_run = get_next_monitor_run_time(now)
            latest_run = douyin_state_store.load_latest_douyin_monitor_run()
            slot_key, slot_time = get_latest_due_monitor_slot(now)
            douyin_monitor_runtime_state["next_run_at"] = next_run.strftime("%Y-%m-%d %H:%M:%S")
            if slot_key and slot_time and not douyin_monitor_runtime_state.get("running"):
                if (
                    reconcile_douyin_account_nurture_runtime_state()
                    and douyin_account_nurture_scheduler
                    and douyin_account_nurture_scheduler.is_actively_blocking_other_tasks()
                ):
                    if str(douyin_monitor_runtime_state.get("last_slot_key", "") or "") != slot_key:
                        douyin_monitor_runtime_state["last_slot_key"] = slot_key
                        douyin_log("[抖音同行监控] 跳过本次定时执行：当前账号养号运行中。", "warning")
                elif str(latest_run.get("slot_key", "") or "") != slot_key:
                    await run_douyin_monitor_cycle(trigger_type="scheduled", slot_key=slot_key)
            await asyncio.sleep(45)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            douyin_monitor_runtime_state.update(
                {
                    "running": False,
                    "last_error": str(exc),
                    "message": f"同行监控调度异常：{exc}",
                }
            )
            douyin_log(f"[抖音同行监控] 调度异常：{exc}", "error")
            await asyncio.sleep(60)


async def ensure_douyin_monitor_scheduler():
    global douyin_monitor_scheduler_task, douyin_monitor_scheduler_started
    if douyin_monitor_scheduler_started and douyin_monitor_scheduler_task and not douyin_monitor_scheduler_task.done():
        return
    douyin_monitor_scheduler_started = True
    douyin_monitor_runtime_state["next_run_at"] = get_next_monitor_run_time().strftime("%Y-%m-%d %H:%M:%S")
    douyin_monitor_scheduler_task = asyncio.create_task(douyin_monitor_scheduler_loop())


def get_douyin_schedule_busy_reason() -> str:
    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()
    reconcile_douyin_account_nurture_runtime_state()
    nurture_conflict = build_douyin_nurture_conflict("执行排期中心")
    if nurture_conflict:
        return str(nurture_conflict.get("msg", "") or "养号任务正在运行")
    if douyin_running:
        return "评论采集任务正在执行"
    if douyin_video_comment_running:
        return "视频评论任务正在执行"
    if douyin_mention_comment_running:
        return "评论@精准客户任务正在执行"
    if douyin_follow_comment_running:
        return "关注评论任务正在执行"
    if douyin_interaction_running:
        return "私信任务正在执行"
    if douyin_group_member_running:
        return "群成员提取任务正在执行"
    if douyin_stranger_message_running:
        return "私信引流任务正在执行"
    return ""


def match_douyin_tasks_for_rows(rows: List[Dict]) -> List[Dict]:
    matched: List[Dict] = []
    seen_ids: Set[int] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        session_id = str(row.get("source_session_id", "") or "").strip()
        item_key = str(row.get("source_item_key", "") or "").strip()
        url = str(row.get("url", "") or "").strip()
        title = str(row.get("title", "") or "").strip()
        author = str(row.get("author", "") or "").strip()
        for task in douyin_tasks:
            task_id = int(task.get("id", 0) or 0)
            if task_id <= 0 or task_id in seen_ids:
                continue
            same_source = (
                session_id
                and item_key
                and session_id == str(task.get("source_session_id", "") or "").strip()
                and item_key == str(task.get("source_item_key", "") or "").strip()
            )
            same_meta = (
                url == str(task.get("url", "") or "").strip()
                and title == str(task.get("title", "") or "").strip()
                and author == str(task.get("author", "") or "").strip()
            )
            if same_source or same_meta:
                matched.append(task)
                seen_ids.add(task_id)
                break
    return matched


def collect_plan_follow_comment_users(plan: Dict[str, object]) -> List[Dict]:
    rows = collect_douyin_interaction_users()
    max_users = max(1, int(plan.get("max_users_per_run", 30) or 30))
    candidates = [
        row
        for row in rows
        if str(row.get("follow_comment_status", "pending") or "pending").strip() != "completed"
    ]
    candidates.sort(
        key=lambda row: parse_comment_timestamp(str(row.get("comment_time", "") or "")) or 0,
        reverse=True,
    )
    return candidates[:max_users]


def collect_plan_interaction_users(plan: Dict[str, object]) -> List[Dict]:
    rows = collect_douyin_interaction_users()
    max_users = max(1, int(plan.get("max_users_per_run", 30) or 30))
    require_follow_done = bool(plan.get("require_follow_comment_completed", False))
    candidates = []
    for row in rows:
        if str(row.get("interaction_status", "pending") or "pending").strip() == "sent":
            continue
        if require_follow_done and str(row.get("follow_comment_status", "pending") or "pending").strip() != "completed":
            continue
        candidates.append(row)
    candidates.sort(
        key=lambda row: parse_comment_timestamp(str(row.get("comment_time", "") or "")) or 0,
        reverse=True,
    )
    return candidates[:max_users]


async def execute_douyin_schedule_plan(plan: Dict[str, object]) -> Dict[str, object]:
    normalized = normalize_douyin_schedule_plan(plan)
    plan_type = str(normalized.get("type", "") or "")
    if plan_type == "collect_precise":
        keyword = str(normalized.get("keyword", "") or "").strip()
        if not keyword:
            return {"code": 400, "msg": "采集计划缺少关键词。"}
        search_result = await douyin_search_collect(
            {
                "keyword": keyword,
                "max_results": int(normalized.get("max_results", 50) or 50),
            }
        )
        if int(search_result.get("code", 0) or 0) != 200:
            return search_result
        raw_results = search_result.get("data", [])
        normalized_results = [
            normalize_douyin_search_session_result(item)
            for item in (raw_results if isinstance(raw_results, list) else [])
            if isinstance(item, dict)
        ]
        max_videos = max(1, int(normalized.get("max_videos_per_run", 50) or 50))
        selected_item_keys: List[str] = [
            str(item.get("source_item_key", "") or "").strip()
            for item in normalized_results
            if bool(item.get("export_selected", True))
        ][:max_videos]
        if not selected_item_keys:
            return {"code": 400, "msg": f"关键词“{keyword}”本次没有可用视频。"}
        session = upsert_douyin_search_session_state(
            keyword=keyword,
            account_id=search_result.get("account_id", ""),
            results=normalized_results,
            capture_state={
                "enabled": True,
                "status": "running",
                "region_values": ["全国"],
                "account_id": "auto",
                "task_ids": [],
                "selected_item_keys": selected_item_keys,
                "matched_users": 0,
                "precise_users": 0,
                "last_message": f"排期计划已启动关键词“{keyword}”，正在自动抓取评论并筛选精准客户。",
                "updated_at": int(datetime.now().timestamp() * 1000),
            },
        )
        rows = [
            {
                **item,
                "source_session_id": str(session.get("id", "") or "").strip(),
                "source_item_key": str(item.get("source_item_key", "") or "").strip(),
                "source_keyword": keyword,
            }
            for item in (session.get("results", []) if isinstance(session.get("results", []), list) else [])
            if str(item.get("source_item_key", "") or "").strip() in set(selected_item_keys)
        ]
        if not rows:
            return {"code": 400, "msg": f"关键词“{keyword}”本次没有可用视频。"}
        set_tasks_from_rows(rows)
        matched_tasks = match_douyin_tasks_for_rows(rows)
        task_ids = [int(task.get("id", 0) or 0) for task in matched_tasks if int(task.get("id", 0) or 0) > 0]
        if not task_ids:
            return {"code": 400, "msg": f"关键词“{keyword}”本次没有匹配到可执行任务。"}
        upsert_douyin_search_session_state(
            keyword=keyword,
            account_id=search_result.get("account_id", ""),
            results=session.get("results", []),
            session_id=str(session.get("id", "") or "").strip(),
            capture_state={
                "enabled": True,
                "status": "running",
                "region_values": ["全国"],
                "account_id": "auto",
                "task_ids": task_ids,
                "selected_item_keys": selected_item_keys,
                "matched_users": 0,
                "precise_users": 0,
                "last_message": f"排期计划已启动关键词“{keyword}”，本轮正在执行 {len(task_ids)} 条视频采集任务。",
                "updated_at": int(datetime.now().timestamp() * 1000),
            },
        )
        start_result = await douyin_start_tasks(
            request={
                "selected_task_ids": task_ids,
                "comment_scroll_rounds": normalized.get("comment_scroll_rounds", 300),
                "comment_max_comments": normalized.get("comment_max_comments", 500),
            }
        )
        if int(start_result.get("code", 0) or 0) == 200:
            actual_started = int(start_result.get("selected_count", len(task_ids)) or 0)
            skipped_existing = max(0, len(task_ids) - actual_started)
            start_result["msg"] = (
                f"计划已启动：关键词“{keyword}”，本轮命中 {len(task_ids)} 条视频，实际启动 {actual_started} 条"
                + (f"，跳过 {skipped_existing} 条已有客户数据的历史完成任务" if skipped_existing else "")
                + "。"
            )
        return start_result
    if plan_type == "follow_comment":
        users = collect_plan_follow_comment_users(normalized)
        if not users:
            return {"code": 400, "msg": "当前没有可执行的精准互动客户。"}
        payload = {
            "users": users,
            "comment_mode": normalized.get("comment_mode"),
            "comment_text": normalized.get("comment_text", ""),
            "comment_prompt": normalized.get("comment_prompt", ""),
            "comment_seed_text": normalized.get("comment_seed_text", ""),
            "interval_minutes_min": normalized.get("follow_interval_minutes_min", 3),
            "interval_minutes_max": normalized.get("follow_interval_minutes_max", 5),
        }
        return await douyin_start_follow_comment(request=payload)
    if plan_type == "interaction":
        users = collect_plan_interaction_users(normalized)
        if not users:
            return {"code": 400, "msg": "当前没有可执行的精准私信客户。"}
        payload = {
            "users": users,
            "message_mode": normalized.get("message_mode"),
            "message": normalized.get("message", ""),
            "message_prompt": normalized.get("message_prompt", ""),
            "message_seed_text": normalized.get("message_seed_text", ""),
            "interval_minutes_min": normalized.get("interaction_interval_minutes_min", 3),
            "interval_minutes_max": normalized.get("interaction_interval_minutes_max", 5),
        }
        return await douyin_start_interaction(request=payload)
    return {"code": 400, "msg": "未知的排期计划类型。"}


async def douyin_schedule_scheduler_loop():
    global douyin_schedule_runtime_state
    while True:
        try:
            now = datetime.now()
            douyin_schedule_runtime_state["running"] = True
            douyin_schedule_runtime_state["last_tick_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
            busy_reason = get_douyin_schedule_busy_reason()
            douyin_schedule_runtime_state["busy_reason"] = busy_reason
            enabled_plans = [
                normalize_douyin_schedule_plan(plan)
                for plan in douyin_schedule_plans
                if isinstance(plan, dict) and bool(plan.get("enabled", True))
            ]
            due_plans: List[Dict[str, object]] = []
            next_due_times: List[datetime] = []
            for plan in enabled_plans:
                next_run = parse_schedule_datetime(plan.get("next_run_at"))
                if not next_run:
                    next_run = now
                    plan["next_run_at"] = format_schedule_datetime(next_run)
                next_due_times.append(next_run)
                if next_run <= now:
                    due_plans.append(plan)
            douyin_schedule_runtime_state["next_run_at"] = format_schedule_datetime(min(next_due_times) if next_due_times else None)

            if busy_reason:
                if douyin_schedule_runtime_state.get("active_plan_id") and not any(
                    str(plan.get("id", "") or "") == str(douyin_schedule_runtime_state.get("active_plan_id", "") or "")
                    for plan in enabled_plans
                ):
                    douyin_schedule_runtime_state["active_plan_id"] = ""
                    douyin_schedule_runtime_state["active_plan_name"] = ""
                    douyin_schedule_runtime_state["active_phase"] = ""
            else:
                douyin_schedule_runtime_state["active_plan_id"] = ""
                douyin_schedule_runtime_state["active_plan_name"] = ""
                douyin_schedule_runtime_state["active_phase"] = ""
                for plan in sorted(due_plans, key=lambda item: str(item.get("next_run_at", "") or "")):
                    if not is_schedule_time_window_open(now, plan.get("window_start"), plan.get("window_end")):
                        update_douyin_schedule_plan_runtime(
                            str(plan.get("id", "") or ""),
                            next_run_at=get_next_schedule_window_anchor(
                                now,
                                plan.get("window_start"),
                                plan.get("window_end"),
                                include_current_if_inside=False,
                            ),
                        )
                        continue
                    plan_id = str(plan.get("id", "") or "")
                    douyin_schedule_runtime_state["active_plan_id"] = plan_id
                    douyin_schedule_runtime_state["active_plan_name"] = str(plan.get("name", "") or "")
                    douyin_schedule_runtime_state["active_phase"] = "launching"
                    result = await execute_douyin_schedule_plan(plan)
                    success = int(result.get("code", 0) or 0) == 200
                    next_run = get_next_douyin_schedule_run(
                        now,
                        int(plan.get("interval_minutes", 120) or 120),
                        window_start=plan.get("window_start"),
                        window_end=plan.get("window_end"),
                    )
                    update_douyin_schedule_plan_runtime(
                        plan_id,
                        status="success" if success else "failed",
                        message=str(result.get("msg", "") or ""),
                        error="" if success else str(result.get("msg", "") or ""),
                        bump_total=True,
                        success=success,
                        failed=not success,
                        next_run_at=next_run,
                    )
                    douyin_schedule_runtime_state["last_run_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
                    douyin_schedule_runtime_state["message"] = str(result.get("msg", "") or "")
                    douyin_schedule_runtime_state["last_error"] = "" if success else str(result.get("msg", "") or "")
                    douyin_schedule_runtime_state["active_phase"] = "waiting" if success else "idle"
                    if success:
                        douyin_log(
                            f"[抖音排期中心] 已触发计划《{plan.get('name', '未命名计划')}》：{result.get('msg', '')}",
                            "success",
                        )
                    else:
                        douyin_log(
                            f"[抖音排期中心] 计划《{plan.get('name', '未命名计划')}》触发失败：{result.get('msg', '')}",
                            "error",
                        )
                    break
            await asyncio.sleep(15)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            douyin_schedule_runtime_state.update(
                {
                    "running": False,
                    "last_error": str(exc),
                    "message": f"排期中心调度异常：{exc}",
                }
            )
            douyin_log(f"[抖音排期中心] 调度异常：{exc}", "error")
            await asyncio.sleep(30)


async def ensure_douyin_schedule_scheduler():
    global douyin_schedule_scheduler_task, douyin_schedule_scheduler_started
    if (
        douyin_schedule_scheduler_started
        and douyin_schedule_scheduler_task
        and not douyin_schedule_scheduler_task.done()
    ):
        return
    douyin_schedule_scheduler_started = True
    douyin_schedule_runtime_state["next_run_at"] = format_schedule_datetime(datetime.now())
    douyin_schedule_scheduler_task = asyncio.create_task(douyin_schedule_scheduler_loop())


def get_active_douyin_account(config: Optional[Dict] = None) -> Optional[Dict]:
    current_config = config or load_global_config()
    accounts = _normalize_accounts(current_config.get("douyin_accounts"))
    preferred_id = int(current_config.get("douyin_default_account_id", 1) or 1)

    preferred = next(
        (account for account in accounts if account["id"] == preferred_id and account.get("status") == "online"),
        None,
    )
    if preferred:
        return preferred
    return next((account for account in accounts if account.get("status") == "online"), None)


def get_douyin_account_by_id(account_id: int, config: Optional[Dict] = None) -> Optional[Dict]:
    current_config = config or load_global_config()
    accounts = _normalize_accounts(current_config.get("douyin_accounts"))
    target_id = int(account_id or 0)
    return next((account for account in accounts if int(account.get("id", 0) or 0) == target_id), None)


def get_online_douyin_accounts(config: Optional[Dict] = None) -> List[Dict]:
    current_config = config or load_global_config()
    accounts = _normalize_accounts(current_config.get("douyin_accounts"))
    preferred_id = int(current_config.get("douyin_default_account_id", 1) or 1)
    online_accounts = [account for account in accounts if account.get("status") == "online"]
    online_accounts.sort(key=lambda account: (account["id"] != preferred_id, account["id"]))
    return online_accounts


async def detect_douyin_account_login_state(account: Dict) -> bool:
    scraper = DouyinCommentScraper(account_id=account["id"], cdp_port=account["port"])
    try:
        return bool(
            await asyncio.wait_for(
                scraper.check_login_state(logger=douyin_log),
                timeout=DOUYIN_ACCOUNT_LOGIN_CHECK_TIMEOUT_SECONDS,
            )
        )
    except asyncio.TimeoutError:
        douyin_log(
            f"[抖音账号] 账号 {account.get('id', '-')} 登录态检测超过 {DOUYIN_ACCOUNT_LOGIN_CHECK_TIMEOUT_SECONDS} 秒，先标记为待登录，避免阻塞配置页。",
            "warning",
        )
        return False
    finally:
        await scraper.close()


def ensure_douyin_account_browser_ready(
    account: Dict,
    start_url: str = "https://www.douyin.com/user/self?from_tab_name=main",
) -> Dict[str, object]:
    client = DouyinClient(account["port"], account_id=account["id"])
    browser_already_open = is_port_open(int(account.get("port", 0) or 0))
    if browser_already_open:
        return {"code": 200, "reused_browser": True}

    success = client.launch_browser(start_url)
    if success:
        return {"code": 200, "reused_browser": False}

    launch_summary = str(getattr(client, "last_launch_summary", "") or "").strip()
    return {
        "code": 500,
        "msg": (
            f"账号 {account['id']} 浏览器启动失败。"
            f" 本次尝试结果：{launch_summary or '未拿到更多细节'}。"
            " 如果你正在使用 Chrome Dev/Canary，建议先关闭残留浏览器后重试。"
        ),
        "launch_summary": launch_summary,
        "reused_browser": False,
    }


async def ensure_douyin_account_browser_ready_async(
    account: Dict,
    start_url: str = "https://www.douyin.com/user/self?from_tab_name=main",
) -> Dict[str, object]:
    return await asyncio.to_thread(ensure_douyin_account_browser_ready, account, start_url)


async def open_douyin_account_homepage(account: Dict) -> Dict[str, object]:
    target_url = "https://www.douyin.com/user/self?from_tab_name=main"
    browser_result = await ensure_douyin_account_browser_ready_async(account, start_url=target_url)
    if int(browser_result.get("code", 0) or 0) != 200:
        return browser_result
    browser_already_open = bool(browser_result.get("reused_browser"))

    if browser_already_open:
        scraper = DouyinCommentScraper(
            headless=False,
            account_id=account["id"],
            cdp_port=account["port"],
        )
        page = None
        try:
            page = await scraper._new_page(logger=douyin_log)
            await page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=DOUYIN_ACCOUNT_VIEW_NAVIGATION_TIMEOUT_MS,
            )
            await page.bring_to_front()
        finally:
            await scraper.close()

    return {
        "code": 200,
        "msg": f"账号 {account['id']} 的抖音主页已打开，浏览器会保持打开。",
        "reused_browser": browser_already_open,
    }


def get_runnable_douyin_tasks(
    selected_task_ids: Optional[Set[int]] = None,
    include_completed_with_comments: bool = False,
) -> List[Dict]:
    normalized_ids = {int(task_id) for task_id in selected_task_ids or set()}
    tasks = []
    for task in douyin_tasks:
        task_id = int(task.get("id", 0) or 0)
        if normalized_ids and task_id not in normalized_ids:
            continue
        existing_comments = task.get("all_comments", []) or []
        existing_comment_count = max(
            int(task.get("comment_count", 0) or 0),
            len(existing_comments) if isinstance(existing_comments, list) else 0,
        )
        if (
            not include_completed_with_comments
            and task.get("status") == "completed"
            and existing_comment_count > 0
        ):
            continue
        tasks.append(task)
    return tasks


def get_commentable_douyin_tasks(selected_task_ids: Optional[Set[int]] = None) -> List[Dict]:
    normalized_ids = {int(task_id) for task_id in selected_task_ids or set()}
    rows = []
    for task in douyin_tasks:
        task_id = int(task.get("id", 0) or 0)
        if normalized_ids and task_id not in normalized_ids:
            continue
        if not str(task.get("url", "") or "").strip():
            continue
        if task.get("video_comment_status") == "commented":
            continue
        rows.append(task)
    return rows


def distribute_douyin_tasks(tasks: List[Dict], accounts: List[Dict]) -> List[Dict]:
    active_accounts = accounts[: max(1, min(len(accounts), len(tasks)))]
    buckets = [{"account": account, "tasks": []} for account in active_accounts]
    for index, task in enumerate(tasks):
        buckets[index % len(buckets)]["tasks"].append(task)
    return [bucket for bucket in buckets if bucket["tasks"]]


async def run_douyin_task_worker(
    tasks: List[Dict],
    account: Dict,
    scroll_rounds: int,
    max_comments: int,
    config: Dict,
    state_lock: asyncio.Lock,
):
    scraper = DouyinCommentScraper(account_id=account["id"], cdp_port=account["port"])
    ai_client = create_ai_client()
    try:
        warmup_url = str((tasks[0] if tasks else {}).get("url", "") or "").strip() or "https://www.douyin.com/user/self?from_tab_name=main"
        browser_result = await ensure_douyin_account_browser_ready_async(account, start_url=warmup_url)
        if int(browser_result.get("code", 0) or 0) != 200:
            error_message = str(browser_result.get("msg", "") or "账号浏览器启动失败")
            async with state_lock:
                for task in tasks:
                    task["status"] = "failed"
                    task["error"] = error_message
                    task["collect_account_id"] = account["id"]
                    task["collect_progress"] = {
                        **ensure_douyin_task_shape(task).get("collect_progress", {}),
                        "phase": "failed",
                        "account_id": int(account["id"] or 0),
                        "updated_at": _now_text(),
                        "last_message": error_message,
                    }
                save_douyin_tasks_state()
            douyin_log(
                f"[抖音评论采集] 账号 {account['id']} 浏览器预热失败：{error_message}",
                "error",
            )
            return

        douyin_log(
            f"[抖音评论采集] 账号 {account['id']} 已接管 {len(tasks)} 条任务",
            "info",
        )
        for task in tasks:
            if douyin_stop_requested:
                break

            try:
                source_comment_total = max(
                    0,
                    int(task.get("source_comment_count", task.get("comments", 0)) or 0),
                )
                target_comment_total = min(source_comment_total, max_comments) if source_comment_total > 0 else max_comments
                started_at_text = _now_text()
                async with state_lock:
                    task["status"] = "processing"
                    task["error"] = ""
                    task["all_comments"] = []
                    task["high_intent_users"] = []
                    task["comment_count"] = 0
                    task["collect_account_id"] = account["id"]
                    task["capture_comment_limit"] = max_comments
                    task["capture_target_comments"] = target_comment_total
                    task["collect_progress"] = {
                        "phase": "opening",
                        "account_id": int(account["id"] or 0),
                        "collected_comments": 0,
                        "visible_comments": 0,
                        "source_comment_total": source_comment_total,
                        "target_comments": target_comment_total,
                        "scroll_round": 0,
                        "scroll_round_limit": max(1, int(scroll_rounds or 0)),
                        "started_at": started_at_text,
                        "updated_at": started_at_text,
                        "last_message": "正在打开视频并进入评论区。",
                    }
                    save_douyin_tasks_state()
                douyin_log(
                    f"[抖音评论采集] 账号 {account['id']} 开始处理：{task.get('title') or task.get('url')}",
                    "info",
                )
            except BaseException as init_exc:
                if isinstance(init_exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
                douyin_log(
                    f"[抖音评论采集] 账号 {account['id']} 初始化任务失败：{task.get('title') or task.get('url')}，{type(init_exc).__name__}: {init_exc}",
                    "error",
                )
                try:
                    traceback.print_exc()
                except Exception:
                    pass
                try:
                    task["status"] = "failed"
                    task["error"] = f"{type(init_exc).__name__}: {init_exc}"
                    save_douyin_tasks_state()
                except Exception:
                    pass
                continue

            try:
                def update_collect_progress(progress_patch: Dict[str, object]) -> None:
                    progress = ensure_douyin_task_shape(task).get("collect_progress", {})
                    source_total = max(
                        0,
                        int(progress_patch.get("source_comment_total", progress.get("source_comment_total", source_comment_total)) or 0),
                    )
                    target_total = max(
                        0,
                        int(progress_patch.get("target_comments", progress.get("target_comments", target_comment_total)) or 0),
                    )
                    if not target_total and source_total > 0:
                        target_total = min(source_total, max_comments)
                    progress.update(
                        {
                            "phase": str(progress_patch.get("phase", progress.get("phase", "processing")) or "processing").strip() or "processing",
                            "account_id": int(progress_patch.get("account_id", progress.get("account_id", account["id"])) or 0),
                            "collected_comments": max(
                                0,
                                int(progress_patch.get("collected_comments", progress.get("collected_comments", 0)) or 0),
                            ),
                            "visible_comments": max(
                                0,
                                int(progress_patch.get("visible_comments", progress.get("visible_comments", 0)) or 0),
                            ),
                            "source_comment_total": source_total,
                            "target_comments": target_total,
                            "scroll_round": max(
                                0,
                                int(progress_patch.get("scroll_round", progress.get("scroll_round", 0)) or 0),
                            ),
                            "scroll_round_limit": max(
                                0,
                                int(progress_patch.get("scroll_round_limit", progress.get("scroll_round_limit", scroll_rounds)) or 0),
                            ),
                            "started_at": str(progress.get("started_at", started_at_text) or started_at_text),
                            "updated_at": _now_text(),
                            "last_message": str(progress_patch.get("last_message", progress.get("last_message", "")) or "").strip(),
                        }
                    )
                    task["capture_comment_limit"] = max_comments
                    task["capture_target_comments"] = target_total
                    task["collect_progress"] = progress

                comments = await scraper.scrape_video_comments(
                    task["url"],
                    max_comments=max_comments,
                    max_scroll_rounds=scroll_rounds,
                    logger=douyin_log,
                    progress_callback=update_collect_progress,
                )
                update_collect_progress(
                    {
                        "phase": "filtering",
                        "collected_comments": len(comments),
                        "visible_comments": len(comments),
                        "last_message": "评论已抓取完成，正在筛选精准客户。",
                    }
                )
                _filter_prompt = get_douyin_comment_direction(config)
                _filter_strategy = get_douyin_comment_filter_strategy(config)
                _filter_started_at = time.time()
                log_douyin_filter_event(
                    "start",
                    scope="task_worker",
                    task_id=int(task.get("id", 0) or 0),
                    title=task.get("title", ""),
                    comments_in=len(comments),
                    strategy=_filter_strategy,
                    prompt=_filter_prompt,
                )
                try:
                    high_intent = await asyncio.to_thread(
                        lambda: ai_client.filter_comments(
                            task.get("title", ""),
                            comments,
                            _filter_prompt,
                            "douyin_transactional",
                            "",
                            _filter_strategy,
                            event_logger=log_douyin_filter_event,
                        )
                    )
                except Exception as _filter_exc:
                    log_douyin_filter_event(
                        "error",
                        scope="task_worker",
                        task_id=int(task.get("id", 0) or 0),
                        title=task.get("title", ""),
                        comments_in=len(comments),
                        strategy=_filter_strategy,
                        duration_ms=int((time.time() - _filter_started_at) * 1000),
                        error=str(_filter_exc),
                    )
                    raise
                log_douyin_filter_event(
                    "done",
                    scope="task_worker",
                    task_id=int(task.get("id", 0) or 0),
                    title=task.get("title", ""),
                    comments_in=len(comments),
                    precise_out=len(high_intent or []),
                    strategy=_filter_strategy,
                    duration_ms=int((time.time() - _filter_started_at) * 1000),
                )
                async with state_lock:
                    task["all_comments"] = comments
                    task["comment_count"] = len(comments)
                    task["high_intent_users"] = dedupe_users(high_intent)
                    task["status"] = "completed"
                    task["error"] = ""
                    task["collect_progress"] = {
                        **ensure_douyin_task_shape(task).get("collect_progress", {}),
                        "phase": "completed",
                        "account_id": int(account["id"] or 0),
                        "collected_comments": len(comments),
                        "visible_comments": len(comments),
                        "updated_at": _now_text(),
                        "last_message": f"评论采集完成，共采集 {len(comments)} 条评论。",
                    }
                    save_douyin_tasks_state()
                douyin_log(
                    f"[抖音评论采集] 账号 {account['id']} 完成：{task.get('title') or task.get('url')}，评论 {len(comments)} 条，高意向 {(len(task['high_intent_users']) if task.get('high_intent_users') else 0)} 人",
                    "success",
                )
            except Exception as exc:
                async with state_lock:
                    task["status"] = "failed"
                    task["error"] = str(exc)
                    task["collect_progress"] = {
                        **ensure_douyin_task_shape(task).get("collect_progress", {}),
                        "phase": "failed",
                        "account_id": int(account["id"] or 0),
                        "updated_at": _now_text(),
                        "last_message": f"评论采集失败：{exc}",
                    }
                    save_douyin_tasks_state()
                douyin_log(
                    f"[抖音评论采集] 账号 {account['id']} 失败：{task.get('title') or task.get('url')}，原因：{exc}",
                    "error",
                )
                try:
                    traceback.print_exc()
                except Exception:
                    pass
            except BaseException as exc:
                async with state_lock:
                    task["status"] = "failed"
                    task["error"] = f"{type(exc).__name__}: {exc}"
                    task["collect_progress"] = {
                        **ensure_douyin_task_shape(task).get("collect_progress", {}),
                        "phase": "failed",
                        "account_id": int(account["id"] or 0),
                        "updated_at": _now_text(),
                        "last_message": f"评论采集异常中断：{type(exc).__name__}: {exc}",
                    }
                    save_douyin_tasks_state()
                douyin_log(
                    f"[抖音评论采集] 账号 {account['id']} 异常中断：{task.get('title') or task.get('url')}，{type(exc).__name__}: {exc}",
                    "error",
                )
                try:
                    traceback.print_exc()
                except Exception:
                    pass
                if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
                # 非取消类异常（如 Playwright 内部 BaseException）：记录并继续下一个任务
                continue

            # 每个任务结束后短暂等待，避免 0 评论或快速完成的任务紧接着开下一个 page 时
            # 因为浏览器资源未完全释放而触发 Playwright 内部异常
            try:
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
    finally:
        await scraper.close()


async def run_douyin_task_worker_protocol(
    tasks: List[Dict],
    account: Dict,
    max_comments: int,
    state_lock: asyncio.Lock,
):
    try:
        from douyin_comment_api_experiment import DouyinCommentApiExperiment
    except Exception as exc:
        error_message = f"协议模式初始化失败：{exc}"
        async with state_lock:
            for task in tasks:
                task["status"] = "failed"
                task["error"] = error_message
                task["collect_account_id"] = account["id"]
                task["collect_progress"] = {
                    **ensure_douyin_task_shape(task).get("collect_progress", {}),
                    "phase": "failed",
                    "account_id": int(account["id"] or 0),
                    "updated_at": _now_text(),
                    "last_message": error_message,
                }
            save_douyin_tasks_state()
        douyin_log(f"[抖音评论采集] 账号 {account['id']} 协议模式初始化失败：{exc}", "error")
        return

    client = DouyinCommentApiExperiment(account_id=account["id"], cdp_port=account["port"])
    warmup_url = str((tasks[0] if tasks else {}).get("url", "") or "").strip() or "https://www.douyin.com/user/self?from_tab_name=main"
    browser_result = await ensure_douyin_account_browser_ready_async(account, start_url=warmup_url)
    if int(browser_result.get("code", 0) or 0) != 200:
        error_message = str(browser_result.get("msg", "") or "账号浏览器启动失败")
        async with state_lock:
            for task in tasks:
                task["status"] = "failed"
                task["error"] = error_message
                task["collect_account_id"] = account["id"]
                task["collect_progress"] = {
                    **ensure_douyin_task_shape(task).get("collect_progress", {}),
                    "phase": "failed",
                    "account_id": int(account["id"] or 0),
                    "updated_at": _now_text(),
                    "last_message": error_message,
                }
            save_douyin_tasks_state()
        douyin_log(f"[抖音评论采集] 账号 {account['id']} 浏览器预热失败：{error_message}", "error")
        return

    try:
        auth = await client.extract_auth()
    except Exception as exc:
        error_message = f"协议模式认证提取失败：{exc}"
        async with state_lock:
            for task in tasks:
                task["status"] = "failed"
                task["error"] = error_message
                task["collect_account_id"] = account["id"]
                task["collect_progress"] = {
                    **ensure_douyin_task_shape(task).get("collect_progress", {}),
                    "phase": "failed",
                    "account_id": int(account["id"] or 0),
                    "updated_at": _now_text(),
                    "last_message": error_message,
                }
            save_douyin_tasks_state()
        douyin_log(f"[抖音评论采集] 账号 {account['id']} 协议模式认证提取失败：{exc}", "error")
        return

    douyin_log(
        f"[抖音评论采集] 账号 {account['id']} 已接管 {len(tasks)} 条任务，模式 {douyin_collection_mode_label('protocol')}",
        "info",
    )
    ai_client = create_ai_client()
    for task in tasks:
        if douyin_stop_requested:
            break
        try:
            work_info = await asyncio.to_thread(client.get_work_info, auth, str(task.get("url", "") or "").strip())
            source_comment_total = max(
                0,
                int(
                    work_info.get("comment_count", task.get("source_comment_count", task.get("comments", 0)))
                    or 0
                ),
            )
            target_comment_total = min(source_comment_total, max_comments) if source_comment_total > 0 else max_comments
            started_at_text = _now_text()
            async with state_lock:
                task["status"] = "processing"
                task["error"] = ""
                task["all_comments"] = []
                task["high_intent_users"] = []
                task["comment_count"] = 0
                task["collect_account_id"] = account["id"]
                task["capture_comment_limit"] = max_comments
                task["capture_target_comments"] = target_comment_total
                task["collect_progress"] = {
                    "phase": "opening",
                    "account_id": int(account["id"] or 0),
                    "collected_comments": 0,
                    "visible_comments": 0,
                    "source_comment_total": source_comment_total,
                    "target_comments": target_comment_total,
                    "scroll_round": 0,
                    "scroll_round_limit": 0,
                    "started_at": started_at_text,
                    "updated_at": started_at_text,
                    "last_message": "正在通过协议接口抓取评论。",
                }
                save_douyin_tasks_state()
            douyin_log(
                f"[抖音评论采集] 账号 {account['id']} 开始处理：{task.get('title') or task.get('url')}",
                "info",
            )
            comments_payload = await asyncio.to_thread(
                lambda: client.get_all_comments(
                    auth,
                    str(task.get("url", "") or "").strip(),
                    max_comments=max_comments,
                )
            )
            raw_comments = comments_payload.get("comments", []) if isinstance(comments_payload, dict) else []
            comments = [
                normalize_douyin_protocol_comment_row(row, task=task, index=index)
                for index, row in enumerate(raw_comments)
                if isinstance(row, dict)
            ]
            _filter_prompt = get_douyin_comment_direction(load_global_config())
            _filter_strategy = get_douyin_comment_filter_strategy(load_global_config())
            _filter_started_at = time.time()
            log_douyin_filter_event(
                "start",
                scope="task_worker",
                task_id=int(task.get("id", 0) or 0),
                title=task.get("title", ""),
                comments_in=len(comments),
                strategy=_filter_strategy,
                prompt=_filter_prompt,
            )
            try:
                high_intent = await asyncio.to_thread(
                    lambda: ai_client.filter_comments(
                        task.get("title", ""),
                        comments,
                        _filter_prompt,
                        "douyin_transactional",
                        "",
                        _filter_strategy,
                        event_logger=log_douyin_filter_event,
                    )
                )
            except Exception as _filter_exc:
                log_douyin_filter_event(
                    "error",
                    scope="task_worker",
                    task_id=int(task.get("id", 0) or 0),
                    title=task.get("title", ""),
                    comments_in=len(comments),
                    strategy=_filter_strategy,
                    duration_ms=int((time.time() - _filter_started_at) * 1000),
                    error=str(_filter_exc),
                )
                raise
            log_douyin_filter_event(
                "done",
                scope="task_worker",
                task_id=int(task.get("id", 0) or 0),
                title=task.get("title", ""),
                comments_in=len(comments),
                precise_out=len(high_intent or []),
                strategy=_filter_strategy,
                duration_ms=int((time.time() - _filter_started_at) * 1000),
            )
            async with state_lock:
                task["all_comments"] = comments
                task["comment_count"] = len(comments)
                task["high_intent_users"] = dedupe_users(high_intent)
                task["status"] = "completed"
                task["error"] = ""
                task["collect_progress"] = {
                    **ensure_douyin_task_shape(task).get("collect_progress", {}),
                    "phase": "completed",
                    "account_id": int(account["id"] or 0),
                    "collected_comments": len(comments),
                    "visible_comments": len(comments),
                    "updated_at": _now_text(),
                    "last_message": f"协议模式采集完成，共采集 {len(comments)} 条评论。",
                }
                save_douyin_tasks_state()
            douyin_log(
                f"[抖音评论采集] 账号 {account['id']} 完成：{task.get('title') or task.get('url')}，评论 {len(comments)} 条，高意向 {(len(task['high_intent_users']) if task.get('high_intent_users') else 0)} 人",
                "success",
            )
        except Exception as exc:
            async with state_lock:
                task["status"] = "failed"
                task["error"] = str(exc)
                task["collect_progress"] = {
                    **ensure_douyin_task_shape(task).get("collect_progress", {}),
                    "phase": "failed",
                    "account_id": int(account["id"] or 0),
                    "updated_at": _now_text(),
                    "last_message": f"协议模式采集失败：{exc}",
                }
                save_douyin_tasks_state()
            douyin_log(
                f"[抖音评论采集] 账号 {account['id']} 失败：{task.get('title') or task.get('url')}，原因：{exc}",
                "error",
            )
            try:
                traceback.print_exc()
            except Exception:
                pass


async def run_douyin_tasks(
    selected_task_ids: Optional[Set[int]] = None,
    comment_scroll_rounds: Optional[int] = None,
    comment_max_comments: Optional[int] = None,
    collection_mode: Optional[str] = None,
    force_recollect: bool = False,
):
    global douyin_running, douyin_stop_requested, douyin_background_task

    config = load_global_config()
    accounts = get_online_douyin_accounts(config)
    runnable_tasks = get_runnable_douyin_tasks(
        selected_task_ids,
        include_completed_with_comments=force_recollect,
    )
    if not runnable_tasks:
        douyin_running = False
        douyin_background_task = None
        douyin_stop_requested = False
        save_douyin_tasks_state()
        return

    if not accounts:
        for task in runnable_tasks:
            task["status"] = "failed"
            task["error"] = "No online Douyin account is available"
        douyin_running = False
        douyin_background_task = None
        douyin_stop_requested = False
        save_douyin_tasks_state()
        return

    scroll_rounds = max(
        20,
        min(int(comment_scroll_rounds if comment_scroll_rounds is not None else (config.get("comment_scroll_rounds", 300) or 300)), 300),
    )
    comment_limit = max(
        20,
        min(int(comment_max_comments if comment_max_comments is not None else (config.get("comment_max_comments", 500) or 500)), 500),
    )
    resolved_collection_mode = normalize_douyin_collection_mode(collection_mode)
    max_comments = comment_limit
    batches = distribute_douyin_tasks(runnable_tasks, accounts)
    state_lock = asyncio.Lock()
    douyin_log(
        f"[抖音评论采集] 已启动 {len(batches)} 个账号并发，共 {len(runnable_tasks)} 条任务，模式 {douyin_collection_mode_label(resolved_collection_mode)}，滚动 {scroll_rounds} 轮，最多采集 {max_comments} 条评论",
        "info",
    )

    try:
        worker_coroutines = []
        for item in batches:
            if resolved_collection_mode == "script":
                worker_coroutines.append(
                    run_douyin_task_worker(
                        item["tasks"],
                        item["account"],
                        scroll_rounds,
                        max_comments,
                        config,
                        state_lock,
                    )
                )
            else:
                worker_coroutines.append(
                    run_douyin_task_worker_protocol(
                        item["tasks"],
                        item["account"],
                        max_comments,
                        state_lock,
                    )
                )
        await asyncio.gather(*worker_coroutines)
    except Exception as exc:
        douyin_log(
            f"[抖音评论采集] 执行异常：{type(exc).__name__}: {exc}",
            "error",
        )
        try:
            traceback.print_exc()
        except Exception:
            pass
    finally:
        douyin_running = False
        douyin_background_task = None
        douyin_stop_requested = False
        save_douyin_tasks_state()
        douyin_log("[抖音评论采集] 本轮任务已结束", "info")


async def run_douyin_video_comments(
    tasks: List[Dict],
    comment_mode: str,
    comment_text: str,
    comment_prompt: str,
    comment_seed_text: str,
    account: Dict,
    interval_seconds_min: int,
    interval_seconds_max: int,
    image_path: str = "",
):
    global douyin_video_comment_running, douyin_video_comment_stop_requested, douyin_video_comment_background_task

    scraper = DouyinCommentScraper(account_id=account["id"], cdp_port=account["port"])
    protocol_client = None
    protocol_auth = None
    total = len(tasks)
    success = 0
    failed = 0
    mode = normalize_douyin_video_comment_mode(comment_mode)
    mode_label = douyin_video_comment_mode_label(mode)
    image_path = str(image_path or "").strip()
    comment_summary = summarize_douyin_video_comment_settings(
        mode,
        fixed_text=comment_text,
        prompt_text=comment_prompt,
        seed_text=comment_seed_text,
    )
    if image_path:
        comment_summary = f"{comment_summary} + 图片"
    douyin_video_comment_state.update(
        {
            "running": True,
            "message": "视频评论任务执行中",
            "total": total,
            "processed": 0,
            "success": 0,
            "failed": 0,
            "interval_seconds": interval_seconds_min,
            "interval_seconds_min": interval_seconds_min,
            "interval_seconds_max": interval_seconds_max,
            "account_id": account["id"],
            "current_task_title": "",
            "comment_mode": mode,
            "comment_summary": comment_summary,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": "",
            "last_error": "",
            "current_comment_text": "",
            "last_comment_text": "",
            "last_task_title": "",
            "next_interval_seconds": 0,
        }
    )

    interval_desc = (
        f"{interval_seconds_min}-{interval_seconds_max} 秒"
        if interval_seconds_min != interval_seconds_max
        else f"{interval_seconds_min} 秒"
    )

    try:
        try:
            from douyin_comment_api_experiment import DouyinCommentApiExperiment

            protocol_client = DouyinCommentApiExperiment(account_id=account["id"], cdp_port=account["port"])
            protocol_auth = await protocol_client.extract_auth()
            douyin_log(
                f"[抖音视频评论] 账号 {account['id']} 已准备协议评论备选通道，页面发送失败时会自动尝试协议发布。",
                "info",
            )
        except Exception as exc:
            protocol_client = None
            protocol_auth = None
            douyin_log(
                f"[抖音视频评论] 账号 {account['id']} 协议评论备选通道不可用，将只使用页面 DOM 发送：{exc}",
                "warning",
            )
        douyin_log(
            f"[抖音视频评论] 使用账号 {account['id']} 启动，共 {total} 个视频，间隔 {interval_desc}，模式 {mode_label}",
            "info",
        )
        for index, task in enumerate(tasks, start=1):
            if douyin_video_comment_stop_requested:
                break

            started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            douyin_video_comment_state["current_task_title"] = str(task.get("title", "") or task.get("url", ""))
            douyin_video_comment_state["current_comment_text"] = ""
            task["video_comment_status"] = "processing"
            task["video_comment_error"] = ""
            task["video_comment_mode"] = mode
            task["video_comment_prompt"] = comment_prompt
            task["video_comment_seed_text"] = comment_seed_text
            task["video_comment_summary"] = comment_summary
            task["video_comment_text"] = ""
            task["video_comment_image_path"] = image_path
            task["video_comment_account_id"] = account["id"]
            task["video_comment_started_at"] = started_at
            task["video_comment_finished_at"] = ""
            save_douyin_tasks_state()
            douyin_log(f"[抖音视频评论] 开始发送：{task.get('title') or task.get('url')}")

            try:
                final_comment_text = generate_douyin_video_comment_text(
                    task,
                    mode=mode,
                    fixed_text=comment_text,
                    prompt_text=comment_prompt,
                    seed_text=comment_seed_text,
                )
                task["video_comment_text"] = final_comment_text
                douyin_video_comment_state["current_comment_text"] = final_comment_text
                douyin_video_comment_state["last_comment_text"] = final_comment_text
                douyin_video_comment_state["last_task_title"] = str(task.get("title", "") or task.get("url", ""))
                douyin_log(
                    f"[抖音视频评论] 已生成评论（{mode_label}）：{clean_douyin_video_comment_text(final_comment_text, limit=30)}"
                )
                try:
                    await scraper.send_video_comment(
                        task.get("url", ""),
                        final_comment_text,
                        expected_title=str(task.get("title", "") or ""),
                        image_path=image_path,
                        use_modal_entry=bool(image_path),
                        logger=douyin_log,
                    )
                except Exception as dom_exc:
                    if image_path or protocol_client is None or protocol_auth is None:
                        raise
                    douyin_log(
                        f"[抖音视频评论] 页面 DOM 发送失败，尝试协议发布备选：{dom_exc}",
                        "warning",
                    )
                    protocol_result = await asyncio.to_thread(
                        protocol_client.publish_comment,
                        protocol_auth,
                        str(task.get("url", "") or ""),
                        final_comment_text,
                    )
                    douyin_log(
                        f"[抖音视频评论] 已通过协议发布备选发送，comment_id={protocol_result.get('comment_id', '')}",
                        "success",
                    )
                success += 1
                task["video_comment_status"] = "commented"
                task["video_comment_error"] = ""
                task["video_comment_finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                douyin_log(f"[抖音视频评论] 发送成功：{task.get('title') or task.get('url')}", "success")
            except Exception as exc:
                failed += 1
                task["video_comment_status"] = "failed"
                task["video_comment_error"] = str(exc)
                task["video_comment_finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                douyin_video_comment_state["last_error"] = str(exc)
                douyin_video_comment_state["current_comment_text"] = ""
                douyin_log(
                    f"[抖音视频评论] 发送失败：{task.get('title') or task.get('url')}，原因：{exc}",
                    "error",
                )
            finally:
                save_douyin_tasks_state()
                douyin_video_comment_state["processed"] = success + failed
                douyin_video_comment_state["success"] = success
                douyin_video_comment_state["failed"] = failed

            has_next_task = index < total
            if has_next_task and not douyin_video_comment_stop_requested:
                next_interval_seconds = (
                    random.randint(interval_seconds_min, interval_seconds_max)
                    if interval_seconds_max > interval_seconds_min
                    else interval_seconds_min
                )
                douyin_video_comment_state["interval_seconds"] = next_interval_seconds
                douyin_video_comment_state["next_interval_seconds"] = next_interval_seconds
                douyin_video_comment_state["message"] = (
                    f"等待 {next_interval_seconds} 秒后继续下一条视频评论"
                )
                remaining_seconds = next_interval_seconds
                while remaining_seconds > 0 and not douyin_video_comment_stop_requested:
                    wait_seconds = min(1, remaining_seconds)
                    await asyncio.sleep(wait_seconds)
                    remaining_seconds -= wait_seconds
                douyin_video_comment_state["next_interval_seconds"] = 0
    finally:
        await scraper.close()
        douyin_video_comment_running = False
        douyin_video_comment_stop_requested = False
        douyin_video_comment_background_task = None
        douyin_video_comment_state["running"] = False
        douyin_video_comment_state["current_task_title"] = ""
        douyin_video_comment_state["current_comment_text"] = ""
        douyin_video_comment_state["next_interval_seconds"] = 0
        douyin_video_comment_state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        douyin_video_comment_state["message"] = (
            "视频评论任务已停止"
            if douyin_video_comment_state["processed"] < total
            else "视频评论任务已完成"
        )
        douyin_log(
            f"[抖音视频评论] 本轮结束，成功 {douyin_video_comment_state['success']}，失败 {douyin_video_comment_state['failed']}",
            "info",
        )


async def run_douyin_mention_comments(
    *,
    account: Dict,
    video_url: str,
    video_title: str,
    video_cover_image: str,
    selected_users: List[Dict],
    selected_total: int,
    truncated_count: int,
):
    global douyin_mention_comment_running, douyin_mention_comment_stop_requested, douyin_mention_comment_background_task

    scraper = DouyinCommentScraper(account_id=account["id"], cdp_port=account["port"])
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    comment_preview = build_douyin_mention_comment_preview(selected_users, limit=10)
    batches = split_douyin_mention_comment_batches(
        selected_users,
        max_mentions_per_comment=DOUYIN_MENTION_COMMENT_MAX_USERS_PER_COMMENT,
        max_comment_chars=DOUYIN_MENTION_COMMENT_SAFE_TEXT_LIMIT,
    )
    total_batches = len(batches)
    accumulated_success = 0
    accumulated_failed = 0

    douyin_mention_comment_state.update(
        {
            "running": True,
            "message": "评论 @ 精准客户任务执行中",
            "total": len(selected_users),
            "processed": 0,
            "success": 0,
            "failed": 0,
            "selected_total": selected_total,
            "truncated": truncated_count,
            "account_id": account["id"],
            "video_url": video_url,
            "video_title": video_title,
            "video_cover_image": video_cover_image,
            "current_user": "",
            "current_users": [row.get("username", "") for row in selected_users[:8]],
            "started_at": started_at,
            "finished_at": "",
            "last_error": "",
            "comment_preview": comment_preview,
        }
    )

    update_douyin_mention_comment_users(
        selected_users,
        status="processing",
        error="",
        comment_text=comment_preview,
        account_id=int(account["id"]),
        result="准备评论中",
        video_url=video_url,
        video_title=video_title,
        started_at=started_at,
        finished_at="",
    )

    try:
        if douyin_mention_comment_stop_requested:
            return

        douyin_log(
            f"[抖音评论@客户] 使用账号 {account['id']} 准备在作品《{video_title or video_url}》下 @ {len(selected_users)} 位精准客户"
            + (f"，系统将按单条最多 {DOUYIN_MENTION_COMMENT_MAX_USERS_PER_COMMENT} 人、约 {DOUYIN_MENTION_COMMENT_SAFE_TEXT_LIMIT} 字自动拆成 {total_batches} 条评论" if total_batches > 1 else "")
            + (f"，已截断 {truncated_count} 位超出上限的用户" if truncated_count else ""),
            "info",
        )

        first_username = str((selected_users[0] or {}).get("username", "") or "").strip() if selected_users else ""
        if first_username:
            douyin_mention_comment_state["current_user"] = first_username
        await asyncio.sleep(0)

        for batch_index, batch_rows in enumerate(batches, start=1):
            if douyin_mention_comment_stop_requested:
                break
            batch_preview = build_douyin_mention_comment_preview(batch_rows, limit=8)
            batch_first_username = str((batch_rows[0] or {}).get("username", "") or "").strip() if batch_rows else ""
            douyin_mention_comment_state.update(
                {
                    "current_user": batch_first_username,
                    "current_users": [str(row.get("username", "") or "").strip() for row in batch_rows[:8]],
                    "comment_preview": batch_preview,
                    "message": f"评论 @ 精准客户任务执行中，第 {batch_index}/{total_batches} 批",
                }
            )
            douyin_log(
                f"[抖音评论@客户] 开始第 {batch_index}/{total_batches} 批，计划 @ {len(batch_rows)} 人：{batch_preview}",
                "info",
            )
            try:
                result = await scraper.send_video_comment_mentions(
                    video_url=video_url,
                    mention_usernames=[str(row.get("username", "") or "") for row in batch_rows],
                    expected_title=video_title,
                    max_mentions=len(batch_rows),
                    logger=douyin_log,
                    should_stop=lambda: bool(douyin_mention_comment_stop_requested),
                )
                finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                sent_labels = result.get("selected_mentions", []) or []
                successful_mentions = result.get("successful_mentions", []) or []
                failed_mentions = result.get("failed_mentions", []) or []
                sent_text = str(result.get("comment_text", "") or batch_preview).strip()
                success_rows = filter_douyin_mention_comment_rows_by_username(
                    batch_rows,
                    [str(item.get("requested_username", "") or "") for item in successful_mentions if isinstance(item, dict)],
                )
                failed_rows = filter_douyin_mention_comment_rows_by_username(
                    batch_rows,
                    [str(item.get("requested_username", "") or "") for item in failed_mentions if isinstance(item, dict)],
                )
                failed_error_lookup = {
                    normalize_douyin_text(item.get("requested_username", "") if isinstance(item, dict) else "").lower():
                    str(item.get("error", "") or "").strip()
                    for item in failed_mentions
                    if isinstance(item, dict) and normalize_douyin_text(item.get("requested_username", "") if isinstance(item, dict) else "")
                }

                update_douyin_mention_comment_users(
                    success_rows,
                    status="completed",
                    error="",
                    comment_text=sent_text,
                    account_id=int(account["id"]),
                    result=f"第 {batch_index}/{total_batches} 批已评论并 @ {len(sent_labels) or len(success_rows)} 人",
                    video_url=video_url,
                    video_title=video_title,
                    started_at=started_at,
                    finished_at=finished_at,
                )
                upsert_douyin_mention_comment_history(
                    success_rows,
                    status="completed",
                    error="",
                    comment_text=sent_text,
                    account_id=int(account["id"]),
                    result=f"第 {batch_index}/{total_batches} 批已评论并 @ {len(sent_labels) or len(success_rows)} 人",
                    video_url=video_url,
                    video_title=video_title,
                    started_at=started_at,
                    finished_at=finished_at,
                )
                for row in failed_rows:
                    username_key = normalize_douyin_text(row.get("username", "") if isinstance(row, dict) else "").lower()
                    row_error = failed_error_lookup.get(username_key, "候选未出现或点击失败")
                    update_douyin_mention_comment_users(
                        [row],
                        status="failed",
                        error=row_error,
                        comment_text=sent_text,
                        account_id=int(account["id"]),
                        result=f"第 {batch_index}/{total_batches} 批已跳过，未写入本次评论",
                        video_url=video_url,
                        video_title=video_title,
                        started_at=started_at,
                        finished_at=finished_at,
                    )
                    upsert_douyin_mention_comment_history(
                        [row],
                        status="failed",
                        error=row_error,
                        comment_text=sent_text,
                        account_id=int(account["id"]),
                        result=f"第 {batch_index}/{total_batches} 批已跳过，未写入本次评论",
                        video_url=video_url,
                        video_title=video_title,
                        started_at=started_at,
                        finished_at=finished_at,
                    )
                accumulated_success += len(success_rows)
                accumulated_failed += len(failed_rows)
                douyin_mention_comment_state.update(
                    {
                        "processed": accumulated_success + accumulated_failed,
                        "success": accumulated_success,
                        "failed": accumulated_failed,
                        "current_user": "",
                        "current_users": sent_labels[:8] or douyin_mention_comment_state.get("current_users", []),
                        "comment_preview": sent_text,
                    }
                )
                douyin_log(
                    f"[抖音评论@客户] 第 {batch_index}/{total_batches} 批完成：成功 @ {len(sent_labels) or len(success_rows)} 人"
                    + (f"，跳过 {len(failed_rows)} 人" if failed_rows else ""),
                    "success" if len(success_rows) else "warning",
                )
            except DouyinMentionCommentStopped:
                raise
            except Exception as exc:
                finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                accumulated_failed += len(batch_rows)
                update_douyin_mention_comment_users(
                    batch_rows,
                    status="failed",
                    error=str(exc),
                    comment_text=batch_preview,
                    account_id=int(account["id"]),
                    result=f"第 {batch_index}/{total_batches} 批发送失败",
                    video_url=video_url,
                    video_title=video_title,
                    started_at=started_at,
                    finished_at=finished_at,
                )
                upsert_douyin_mention_comment_history(
                    batch_rows,
                    status="failed",
                    error=str(exc),
                    comment_text=batch_preview,
                    account_id=int(account["id"]),
                    result=f"第 {batch_index}/{total_batches} 批发送失败",
                    video_url=video_url,
                    video_title=video_title,
                    started_at=started_at,
                    finished_at=finished_at,
                )
                douyin_mention_comment_state.update(
                    {
                        "processed": accumulated_success + accumulated_failed,
                        "success": accumulated_success,
                        "failed": accumulated_failed,
                        "last_error": str(exc),
                        "current_user": "",
                    }
                )
                douyin_log(
                    f"[抖音评论@客户] 第 {batch_index}/{total_batches} 批失败：{exc}",
                    "error",
                )

        douyin_log(
            f"[抖音评论@客户] 已完成：作品《{video_title or video_url}》累计成功 @ {accumulated_success} 人，失败 {accumulated_failed} 人，共执行 {total_batches} 批",
            "success" if accumulated_success else "warning",
        )
    except DouyinMentionCommentStopped:
        douyin_mention_comment_state.update(
            {
                "current_user": "",
                "message": "评论 @ 精准客户任务已停止",
            }
        )
        douyin_log("[抖音评论@客户] 已按停止请求中断当前任务", "warning")
    except Exception as exc:
        finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        batch_failure_message = f"批量 @ 发送失败：{exc}"
        update_douyin_mention_comment_users(
            selected_users,
            status="failed",
            error=str(exc),
            comment_text=comment_preview,
            account_id=int(account["id"]),
            result="发送失败",
            video_url=video_url,
            video_title=video_title,
            started_at=started_at,
            finished_at=finished_at,
        )
        douyin_mention_comment_state.update(
            {
                "processed": len(selected_users),
                "success": 0,
                "failed": len(selected_users),
                "last_error": str(exc),
                "current_user": "",
                "message": batch_failure_message,
            }
        )
        douyin_log(f"[抖音评论@客户] 执行失败：{batch_failure_message}", "error")
    finally:
        if (
            douyin_mention_comment_state.get("success", 0) == 0
            and douyin_mention_comment_state.get("failed", 0) == 0
        ):
            update_douyin_mention_comment_users(
                selected_users,
                status="pending",
                error="",
                comment_text=comment_preview,
                account_id=int(account["id"]),
                result="任务已停止",
                video_url=video_url,
                video_title=video_title,
                started_at=started_at,
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        await scraper.close()
        douyin_mention_comment_running = False
        douyin_mention_comment_stop_requested = False
        douyin_mention_comment_background_task = None
        douyin_mention_comment_state["running"] = False
        douyin_mention_comment_state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        douyin_mention_comment_state["current_user"] = ""
        douyin_mention_comment_state["message"] = (
            "评论 @ 精准客户任务已停止"
            if douyin_mention_comment_state["success"] < len(selected_users)
            else "评论 @ 精准客户任务已完成"
        )
        douyin_log(
            f"[抖音评论@客户] 本轮结束，成功 {douyin_mention_comment_state['success']}，失败 {douyin_mention_comment_state['failed']}"
            + (
                "。说明：失败用户已跳过，成功用户会继续写入并发送。"
                if douyin_mention_comment_state.get("failed", 0) and douyin_mention_comment_state.get("success", 0) > 0
                else ""
            ),
            "info",
        )


def build_follow_comment_worker_state(account: Dict, assigned_total: int) -> Dict:
    return {
        "account_id": account["id"],
        "status": "queued",
        "assigned_total": assigned_total,
        "processed": 0,
        "success": 0,
        "failed": 0,
        "commented": 0,
        "skipped_no_posts": 0,
        "current_user": "",
        "cooldown_remaining": 0,
        "last_error": "",
    }


def get_follow_comment_assigned_total() -> int:
    return sum(
        int(worker.get("assigned_total", 0) or 0)
        for worker in douyin_follow_comment_state.get("workers", [])
        if isinstance(worker, dict)
    )


def refresh_follow_comment_state_from_workers(
    total: int,
    interval_seconds_min: int,
    interval_seconds_max: int,
):
    workers = douyin_follow_comment_state.get("workers", [])
    if not isinstance(workers, list):
        workers = []
    processed = sum(int(worker.get("processed", 0) or 0) for worker in workers if isinstance(worker, dict))
    success = sum(int(worker.get("success", 0) or 0) for worker in workers if isinstance(worker, dict))
    failed = sum(int(worker.get("failed", 0) or 0) for worker in workers if isinstance(worker, dict))
    commented = sum(int(worker.get("commented", 0) or 0) for worker in workers if isinstance(worker, dict))
    skipped_no_posts = sum(int(worker.get("skipped_no_posts", 0) or 0) for worker in workers if isinstance(worker, dict))
    current_users = [
        str(worker.get("current_user", "") or "")
        for worker in workers
        if isinstance(worker, dict) and str(worker.get("current_user", "") or "").strip()
    ]
    douyin_follow_comment_state["total"] = total
    douyin_follow_comment_state["processed"] = processed
    douyin_follow_comment_state["success"] = success
    douyin_follow_comment_state["failed"] = failed
    douyin_follow_comment_state["commented"] = commented
    douyin_follow_comment_state["skipped_no_posts"] = skipped_no_posts
    douyin_follow_comment_state["interval_seconds"] = interval_seconds_min
    douyin_follow_comment_state["interval_seconds_min"] = interval_seconds_min
    douyin_follow_comment_state["interval_seconds_max"] = interval_seconds_max
    douyin_follow_comment_state["current_users"] = current_users
    douyin_follow_comment_state["current_user"] = current_users[0] if current_users else ""

    if douyin_follow_comment_stop_requested:
        douyin_follow_comment_state["message"] = "正在停止关注评论任务..."
        return

    worker_messages = []
    for worker in workers:
        if not isinstance(worker, dict):
            continue
        account_id = worker.get("account_id")
        status = str(worker.get("status", "") or "")
        current_user = str(worker.get("current_user", "") or "").strip()
        cooldown_remaining = int(worker.get("cooldown_remaining", 0) or 0)
        if status == "processing" and current_user:
            worker_messages.append(f"账号 {account_id} 正在处理 {current_user}")
        elif status == "cooldown":
            worker_messages.append(f"账号 {account_id} 冷却 {cooldown_remaining} 秒")

    if worker_messages:
        douyin_follow_comment_state["message"] = "；".join(worker_messages[:3])
    else:
        account_ids = douyin_follow_comment_state.get("account_ids", [])
        account_count = len(account_ids) if isinstance(account_ids, list) else 0
        douyin_follow_comment_state["message"] = (
            f"关注评论任务执行中，{account_count} 个账号并发"
            if account_count > 1
            else "关注评论任务执行中"
        )


async def run_douyin_follow_comment_worker(
    users: List[Dict],
    comment_mode: str,
    comment_text: str,
    comment_prompt: str,
    comment_seed_text: str,
    image_path: str,
    account: Dict,
    interval_seconds_min: int,
    interval_seconds_max: int,
    worker_state: Dict,
    state_lock: asyncio.Lock,
):
    image_path = str(image_path or "").strip()
    scraper = DouyinCommentScraper(account_id=account["id"], cdp_port=account["port"])
    protocol_client = None
    protocol_auth = None
    try:
        try:
            from douyin_comment_api_experiment import DouyinCommentApiExperiment

            protocol_client = DouyinCommentApiExperiment(account_id=account["id"], cdp_port=account["port"])
            protocol_auth = await protocol_client.extract_auth()
            douyin_log(
                f"[抖音关注评论] 账号 {account['id']} 已准备协议评论备选通道，页面评论失败时会自动尝试协议发布。",
                "info",
            )
        except Exception as exc:
            protocol_client = None
            protocol_auth = None
            douyin_log(
                f"[抖音关注评论] 账号 {account['id']} 协议评论备选通道不可用，将只使用页面 DOM 评论：{exc}",
                "warning",
            )
        interval_desc = (
            f"{interval_seconds_min}-{interval_seconds_max} 秒"
            if interval_seconds_min != interval_seconds_max
            else f"{interval_seconds_min} 秒"
        )
        douyin_log(
            f"[抖音关注评论] 账号 {account['id']} 已接管 {len(users)} 位用户，间隔 {interval_desc}，模式 {douyin_video_comment_mode_label(comment_mode)}",
            "info",
        )
        for index, user in enumerate(users, start=1):
            if douyin_follow_comment_stop_requested:
                break

            started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            current_user = str(user.get("username", "") or user.get("profile_url", ""))
            async with state_lock:
                worker_state["status"] = "processing"
                worker_state["current_user"] = current_user
                worker_state["cooldown_remaining"] = 0
                douyin_follow_comment_state["current_comment_text"] = ""
                refresh_follow_comment_state_from_workers(
                    get_follow_comment_assigned_total(),
                    interval_seconds_min,
                    interval_seconds_max,
                )
            douyin_log(f"[抖音关注评论] 账号 {account['id']} 开始处理：{current_user}", "info")

            final_comment_text = ""
            try:
                target_work = {
                    "title": f"{str(user.get('username', '') or current_user or '该用户')}的首个作品",
                    "author": str(user.get("username", "") or "该用户"),
                    "url": str(user.get("profile_url", "") or ""),
                }
                final_comment_text = generate_douyin_video_comment_text(
                    target_work,
                    mode=comment_mode,
                    fixed_text=comment_text,
                    prompt_text=comment_prompt,
                    seed_text=comment_seed_text,
                )
                douyin_follow_comment_state["current_comment_text"] = final_comment_text
                douyin_follow_comment_state["last_comment_text"] = final_comment_text
                update_douyin_follow_comment_users(
                    [user],
                    status="processing",
                    error="",
                    comment_text=final_comment_text,
                    account_id=account["id"],
                    result="",
                    started_at=started_at,
                    finished_at="",
                )
                douyin_log(
                    f"[抖音关注评论] 账号 {account['id']} 已生成评论（{douyin_video_comment_mode_label(comment_mode)}）：{clean_douyin_video_comment_text(final_comment_text, limit=30)}"
                )
                try:
                    result = await scraper.follow_user_and_comment_first_post(
                        user.get("profile_url", ""),
                        final_comment_text,
                        expected_username=str(user.get("username", "") or ""),
                        image_path=image_path,
                        logger=douyin_log,
                    )
                except Exception as dom_exc:
                    if image_path or protocol_client is None or protocol_auth is None:
                        raise
                    profile_result = await scraper.follow_user_and_find_first_post(
                        user.get("profile_url", ""),
                        expected_username=str(user.get("username", "") or ""),
                        logger=douyin_log,
                    )
                    protocol_video_url = str(profile_result.get("video_url", "") or "").strip()
                    if not protocol_video_url:
                        raise
                    douyin_log(
                        f"[抖音关注评论] 页面评论失败，尝试对首个作品走协议发布备选：{dom_exc}",
                        "warning",
                    )
                    protocol_result = await asyncio.to_thread(
                        protocol_client.publish_comment,
                        protocol_auth,
                        protocol_video_url,
                        final_comment_text,
                    )
                    result = {
                        **profile_result,
                        "success": True,
                        "has_posts": True,
                        "commented": True,
                        "summary": f"已关注并通过协议备选完成首作品评论，comment_id={protocol_result.get('comment_id', '')}",
                    }
                summary = str(result.get("summary", "") or "").strip() or "已完成主页关注评论"
                has_posts = bool(result.get("has_posts"))
                commented = bool(result.get("commented"))
                update_douyin_follow_comment_users(
                    [user],
                    status="completed",
                    error="",
                    comment_text=final_comment_text,
                    account_id=account["id"],
                    result=summary,
                    started_at=started_at,
                    finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                douyin_log(f"[抖音关注评论] 账号 {account['id']} 完成：{current_user}，{summary}", "success")
                async with state_lock:
                    worker_state["processed"] = int(worker_state.get("processed", 0) or 0) + 1
                    worker_state["success"] = int(worker_state.get("success", 0) or 0) + 1
                    if commented:
                        worker_state["commented"] = int(worker_state.get("commented", 0) or 0) + 1
                    if not has_posts:
                        worker_state["skipped_no_posts"] = int(worker_state.get("skipped_no_posts", 0) or 0) + 1
                    worker_state["last_error"] = ""
                    refresh_follow_comment_state_from_workers(
                        get_follow_comment_assigned_total(),
                        interval_seconds_min,
                        interval_seconds_max,
                    )
            except Exception as exc:
                update_douyin_follow_comment_users(
                    [user],
                    status="failed",
                    error=str(exc),
                    comment_text=final_comment_text,
                    account_id=account["id"],
                    result="",
                    started_at=started_at,
                    finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                douyin_log(f"[抖音关注评论] 账号 {account['id']} 失败：{current_user}，原因：{exc}", "error")
                async with state_lock:
                    worker_state["processed"] = int(worker_state.get("processed", 0) or 0) + 1
                    worker_state["failed"] = int(worker_state.get("failed", 0) or 0) + 1
                    worker_state["last_error"] = str(exc)
                    douyin_follow_comment_state["last_error"] = str(exc)
                    refresh_follow_comment_state_from_workers(
                        get_follow_comment_assigned_total(),
                        interval_seconds_min,
                        interval_seconds_max,
                    )

            has_next_user = index < len(users)
            if has_next_user and not douyin_follow_comment_stop_requested:
                wait_interval_seconds = (
                    random.randint(interval_seconds_min, interval_seconds_max)
                    if interval_seconds_max > interval_seconds_min
                    else interval_seconds_min
                )
                remaining_seconds = wait_interval_seconds
                async with state_lock:
                    worker_state["status"] = "cooldown"
                    worker_state["cooldown_remaining"] = remaining_seconds
                    refresh_follow_comment_state_from_workers(
                        get_follow_comment_assigned_total(),
                        interval_seconds_min,
                        interval_seconds_max,
                    )
                while remaining_seconds > 0 and not douyin_follow_comment_stop_requested:
                    wait_seconds = min(1, remaining_seconds)
                    await asyncio.sleep(wait_seconds)
                    remaining_seconds -= wait_seconds
                    async with state_lock:
                        worker_state["cooldown_remaining"] = remaining_seconds
                        refresh_follow_comment_state_from_workers(
                            get_follow_comment_assigned_total(),
                            interval_seconds_min,
                            interval_seconds_max,
                        )

        async with state_lock:
            worker_state["status"] = "stopped" if douyin_follow_comment_stop_requested else "completed"
            worker_state["current_user"] = ""
            worker_state["cooldown_remaining"] = 0
            refresh_follow_comment_state_from_workers(
                get_follow_comment_assigned_total(),
                interval_seconds_min,
                interval_seconds_max,
            )
    except Exception as exc:
        async with state_lock:
            worker_state["status"] = "failed"
            worker_state["current_user"] = ""
            worker_state["cooldown_remaining"] = 0
            worker_state["last_error"] = str(exc)
            douyin_follow_comment_state["last_error"] = str(exc)
            refresh_follow_comment_state_from_workers(
                get_follow_comment_assigned_total(),
                interval_seconds_min,
                interval_seconds_max,
            )
        douyin_log(f"[抖音关注评论] 账号 {account['id']} worker 异常退出：{exc}", "error")
    finally:
        await scraper.close()


async def run_douyin_follow_comments(
    users: List[Dict],
    comment_mode: str,
    comment_text: str,
    comment_prompt: str,
    comment_seed_text: str,
    accounts: List[Dict],
    interval_seconds_min: int,
    interval_seconds_max: int,
    image_path: str = "",
):
    global douyin_follow_comment_running, douyin_follow_comment_stop_requested, douyin_follow_comment_background_task

    image_path = str(image_path or "").strip()
    total = len(users)
    batches = distribute_interaction_users(users, accounts)
    workers = [build_follow_comment_worker_state(item["account"], len(item["users"])) for item in batches]
    comment_summary = summarize_douyin_video_comment_settings(
        comment_mode,
        fixed_text=comment_text,
        prompt_text=comment_prompt,
        seed_text=comment_seed_text,
    )
    if image_path:
        comment_summary = f"{comment_summary} + 图片"
    douyin_follow_comment_state.update(
        {
            "running": True,
            "message": "关注评论任务执行中",
            "total": total,
            "processed": 0,
            "success": 0,
            "failed": 0,
            "commented": 0,
            "skipped_no_posts": 0,
            "interval_seconds": interval_seconds_min,
            "interval_seconds_min": interval_seconds_min,
            "interval_seconds_max": interval_seconds_max,
            "account_id": workers[0]["account_id"] if len(workers) == 1 else None,
            "comment_mode": comment_mode,
            "comment_summary": comment_summary,
            "account_ids": [worker["account_id"] for worker in workers],
            "current_user": "",
            "current_users": [],
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": "",
            "last_error": "",
            "current_comment_text": "",
            "comment_image_path": image_path,
            "workers": workers,
        }
    )

    state_lock = asyncio.Lock()
    refresh_follow_comment_state_from_workers(total, interval_seconds_min, interval_seconds_max)
    try:
        interval_desc = (
            f"{interval_seconds_min}-{interval_seconds_max} 秒"
            if interval_seconds_min != interval_seconds_max
            else f"{interval_seconds_min} 秒"
        )
        douyin_log(
            f"[抖音关注评论] 已启动 {len(workers)} 个账号并发，共 {total} 人，间隔 {interval_desc}，模式 {douyin_video_comment_mode_label(comment_mode)}",
            "info",
        )
        await asyncio.gather(
            *[
                run_douyin_follow_comment_worker(
                    item["users"],
                    comment_mode,
                    comment_text,
                    comment_prompt,
                    comment_seed_text,
                    image_path,
                    item["account"],
                    interval_seconds_min,
                    interval_seconds_max,
                    worker_state,
                    state_lock,
                )
                for item, worker_state in zip(batches, workers)
            ]
        )
    finally:
        douyin_follow_comment_running = False
        douyin_follow_comment_stop_requested = False
        douyin_follow_comment_background_task = None
        douyin_follow_comment_state["running"] = False
        douyin_follow_comment_state["current_user"] = ""
        douyin_follow_comment_state["current_users"] = []
        douyin_follow_comment_state["current_comment_text"] = ""
        douyin_follow_comment_state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        douyin_follow_comment_state["message"] = (
            "关注评论任务已停止"
            if douyin_follow_comment_state["processed"] < total
            else "关注评论任务已完成"
        )
        douyin_log(
            f"[抖音关注评论] 本轮结束，成功 {douyin_follow_comment_state['success']}，评论成功 {douyin_follow_comment_state['commented']}，无作品跳过 {douyin_follow_comment_state['skipped_no_posts']}，失败 {douyin_follow_comment_state['failed']}，使用 {len(workers)} 个账号",
            "info",
        )


def build_interaction_worker_state(account: Dict, assigned_total: int) -> Dict:
    return {
        "account_id": account["id"],
        "status": "queued",
        "assigned_total": assigned_total,
        "processed": 0,
        "success": 0,
        "failed": 0,
        "current_user": "",
        "current_message_text": "",
        "cooldown_remaining": 0,
        "last_error": "",
    }


def _is_invalid_douyin_profile_error(exc: Exception) -> bool:
    message = str(exc or "").strip()
    return any(
        keyword in message
        for keyword in (
            "用户不存在",
            "主页链接无效",
            "缺少用户主页地址",
            "无效的用户主页地址",
            "invalid douyin profile url",
        )
    )


def _should_fallback_from_douyin_im_error(exc: Exception) -> bool:
    if _is_invalid_douyin_profile_error(exc):
        return False
    class_name = exc.__class__.__name__
    if class_name == "DouyinImApiProfileError":
        return False
    if class_name == "DouyinImApiUnavailableError":
        return True
    message = str(exc or "").strip().lower()
    fallback_markers = (
        "douyin im auth material incomplete",
        "missing",
        "node_modules",
        "jsrsasign",
        "a_bogus",
        "execjs",
        "query target user failed",
        "query current user failed",
        "create conversation failed",
        "send im message failed",
        "connection aborted",
        "timeout",
    )
    return any(marker in message for marker in fallback_markers)


def distribute_interaction_users(users: List[Dict], accounts: List[Dict]) -> List[Dict]:
    active_accounts = accounts[: max(1, min(len(accounts), len(users)))]
    buckets = [{"account": account, "users": []} for account in active_accounts]
    for index, user in enumerate(users):
        buckets[index % len(buckets)]["users"].append(user)
    return [bucket for bucket in buckets if bucket["users"]]


def get_interaction_assigned_total() -> int:
    return sum(
        int(worker.get("assigned_total", 0) or 0)
        for worker in douyin_interaction_state.get("workers", [])
        if isinstance(worker, dict)
    )


def refresh_interaction_state_from_workers(
    total: int,
    interval_seconds_min: int,
    interval_seconds_max: int,
):
    workers = douyin_interaction_state.get("workers", [])
    if not isinstance(workers, list):
        workers = []
    processed = sum(int(worker.get("processed", 0) or 0) for worker in workers if isinstance(worker, dict))
    success = sum(int(worker.get("success", 0) or 0) for worker in workers if isinstance(worker, dict))
    failed = sum(int(worker.get("failed", 0) or 0) for worker in workers if isinstance(worker, dict))
    current_users = [
        str(worker.get("current_user", "") or "")
        for worker in workers
        if isinstance(worker, dict) and str(worker.get("current_user", "") or "").strip()
    ]
    douyin_interaction_state["total"] = total
    douyin_interaction_state["processed"] = processed
    douyin_interaction_state["success"] = success
    douyin_interaction_state["failed"] = failed
    douyin_interaction_state["interval_seconds"] = interval_seconds_min
    douyin_interaction_state["interval_seconds_min"] = interval_seconds_min
    douyin_interaction_state["interval_seconds_max"] = interval_seconds_max
    douyin_interaction_state["current_users"] = current_users
    douyin_interaction_state["current_user"] = current_users[0] if current_users else ""
    current_messages = [
        str(worker.get("current_message_text", "") or "")
        for worker in workers
        if isinstance(worker, dict) and str(worker.get("current_message_text", "") or "").strip()
    ]
    douyin_interaction_state["current_message_text"] = current_messages[0] if current_messages else ""

    if douyin_interaction_stop_requested:
        douyin_interaction_state["message"] = "正在停止私信任务..."
        return

    worker_messages = []
    for worker in workers:
        if not isinstance(worker, dict):
            continue
        account_id = worker.get("account_id")
        status = str(worker.get("status", "") or "")
        current_user = str(worker.get("current_user", "") or "").strip()
        cooldown_remaining = int(worker.get("cooldown_remaining", 0) or 0)
        if status == "processing" and current_user:
            worker_messages.append(f"账号 {account_id} 正在发送 {current_user}")
        elif status == "cooldown":
            worker_messages.append(f"账号 {account_id} 冷却 {cooldown_remaining} 秒")

    if worker_messages:
        douyin_interaction_state["message"] = "；".join(worker_messages[:3])
    else:
        account_ids = douyin_interaction_state.get("account_ids", [])
        account_count = len(account_ids) if isinstance(account_ids, list) else 0
        douyin_interaction_state["message"] = (
            f"私信任务执行中，{account_count} 个账号并发"
            if account_count > 1
            else "私信任务执行中"
        )


async def run_douyin_interaction_worker(
    users: List[Dict],
    message_mode: str,
    fixed_message: str,
    message_prompt: str,
    message_seed_text: str,
    account: Dict,
    interval_seconds_min: int,
    interval_seconds_max: int,
    worker_state: Dict,
    state_lock: asyncio.Lock,
):
    config = load_global_config()
    show_browser = True
    scraper: Optional[DouyinCommentScraper] = None
    im_client = None
    im_auth = None
    im_direct_enabled = False
    consecutive_ai_failures = 0
    ai_failure_abort_message = "本轮已暂停：AI 接口连续超时或不可用，请稍后重试，或先切换到固定文案模式。"
    try:
        douyin_log(
            f"[抖音私信] 账号 {account['id']} 本轮使用页面 DOM 发送，暂不调用 IM 协议直发，避免协议尝试后再页面发送触发频繁。",
            "info",
        )

        interval_desc = (
            f"{interval_seconds_min}-{interval_seconds_max} 秒"
            if interval_seconds_min != interval_seconds_max
            else f"{interval_seconds_min} 秒"
        )
        douyin_log(
            f"[抖音私信] 账号 {account['id']} 已接管 {len(users)} 位用户，间隔 {interval_desc}，浏览器模式：{'显示窗口' if show_browser else '无头运行'}",
            "info",
        )
        for index, user in enumerate(users, start=1):
            if douyin_interaction_stop_requested:
                break

            started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            current_user = str(user.get("username", "") or user.get("profile_url", ""))
            final_message = ""
            try:
                final_message = generate_douyin_interaction_message(
                    user,
                    mode=message_mode,
                    fixed_text=fixed_message,
                    prompt_text=message_prompt,
                    seed_text=message_seed_text,
                )
                async with state_lock:
                    worker_state["status"] = "processing"
                    worker_state["current_user"] = current_user
                    worker_state["current_message_text"] = final_message
                    worker_state["cooldown_remaining"] = 0
                    douyin_interaction_state["current_message_text"] = final_message
                    refresh_interaction_state_from_workers(
                        get_interaction_assigned_total(),
                        interval_seconds_min,
                        interval_seconds_max,
                    )

                update_douyin_interaction_users(
                    [user],
                    status="processing",
                    error="",
                    message=final_message,
                    account_id=account["id"],
                    started_at=started_at,
                    finished_at="",
                )
                douyin_log(f"[抖音私信] 账号 {account['id']} 开始处理：{current_user}", "info")

                if im_direct_enabled and im_client is not None and im_auth is not None:
                    try:
                        im_auth = await im_client.extract_auth()
                        protocol_result = await asyncio.to_thread(
                            im_client.send_private_message,
                            im_auth,
                            user.get("profile_url", ""),
                            final_message,
                        )
                        server_message_id = str(
                            ((protocol_result or {}).get("send_result") or {}).get("server_message_id", "")
                            or ""
                        ).strip()
                        douyin_log(
                            f"[抖音私信] 账号 {account['id']} 已通过 IM 协议直发：{current_user}，server_message_id={server_message_id}",
                            "info",
                        )
                    except Exception as im_exc:
                        refreshed = False
                        if _should_fallback_from_douyin_im_error(im_exc):
                            try:
                                douyin_log(
                                    f"[抖音私信] 账号 {account['id']} IM 协议发送失败，正在刷新抖音前台参数后重试：{im_exc}",
                                    "warning",
                                )
                                im_auth = await im_client.extract_auth()
                                retry_protocol_result = await asyncio.to_thread(
                                    im_client.send_private_message,
                                    im_auth,
                                    user.get("profile_url", ""),
                                    final_message,
                                )
                                retry_server_message_id = str(
                                    ((retry_protocol_result or {}).get("send_result") or {}).get("server_message_id", "")
                                    or ""
                                ).strip()
                                refreshed = True
                                douyin_log(
                                    f"[抖音私信] 账号 {account['id']} 已刷新 IM 参数并直发成功：{current_user}，server_message_id={retry_server_message_id}",
                                    "info",
                                )
                            except Exception as refresh_exc:
                                im_exc = refresh_exc
                        if refreshed:
                            pass
                        elif _should_fallback_from_douyin_im_error(im_exc):
                            im_direct_enabled = False
                            douyin_log(
                                f"[抖音私信] 账号 {account['id']} IM 协议发送失败，自动回退页面私信：{im_exc}",
                                "warning",
                            )
                            if scraper is None:
                                scraper = create_douyin_message_scraper(account, config)
                            await scraper.send_private_message(
                                user.get("profile_url", ""),
                                final_message,
                                expected_username=str(user.get("username", "") or ""),
                                logger=douyin_log,
                            )
                        else:
                            raise
                else:
                    if scraper is None:
                        scraper = create_douyin_message_scraper(account, config)
                    await scraper.send_private_message(
                        user.get("profile_url", ""),
                        final_message,
                        expected_username=str(user.get("username", "") or ""),
                        logger=douyin_log,
                    )
                update_douyin_interaction_users(
                    [user],
                    status="sent",
                    error="",
                    message=final_message,
                    account_id=account["id"],
                    started_at=started_at,
                    finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                douyin_interaction_state["last_message_text"] = final_message
                douyin_log(f"[抖音私信] 账号 {account['id']} 发送成功：{current_user}", "success")
                consecutive_ai_failures = 0
                async with state_lock:
                    worker_state["processed"] = int(worker_state.get("processed", 0) or 0) + 1
                    worker_state["success"] = int(worker_state.get("success", 0) or 0) + 1
                    worker_state["last_error"] = ""
                    refresh_interaction_state_from_workers(
                        get_interaction_assigned_total(),
                        interval_seconds_min,
                        interval_seconds_max,
                    )
            except Exception as exc:
                update_douyin_interaction_users(
                    [user],
                    status="failed",
                    error=str(exc),
                    message=final_message,
                    account_id=account["id"],
                    started_at=started_at,
                    finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                douyin_log(f"[抖音私信] 账号 {account['id']} 发送失败：{current_user}，原因：{exc}", "error")
                if is_douyin_ai_generation_error(exc):
                    consecutive_ai_failures += 1
                else:
                    consecutive_ai_failures = 0
                async with state_lock:
                    worker_state["processed"] = int(worker_state.get("processed", 0) or 0) + 1
                    worker_state["failed"] = int(worker_state.get("failed", 0) or 0) + 1
                    worker_state["last_error"] = str(exc)
                    douyin_interaction_state["last_error"] = str(exc)
                    refresh_interaction_state_from_workers(
                        get_interaction_assigned_total(),
                        interval_seconds_min,
                        interval_seconds_max,
                    )

                if consecutive_ai_failures >= 3:
                    remaining_users = users[index:]
                    if remaining_users:
                        update_douyin_interaction_users(
                            remaining_users,
                            status="pending",
                            error=ai_failure_abort_message,
                            message="",
                            account_id=account["id"],
                            started_at="",
                            finished_at="",
                        )
                    douyin_log(
                        f"[抖音私信] 账号 {account['id']} 连续 {consecutive_ai_failures} 次 AI 生成失败，已暂停本轮剩余私信。请稍后重试，或切换到固定文案模式。",
                        "warning",
                    )
                    async with state_lock:
                        worker_state["status"] = "failed"
                        worker_state["last_error"] = ai_failure_abort_message
                        douyin_interaction_state["last_error"] = ai_failure_abort_message
                        refresh_interaction_state_from_workers(
                            get_interaction_assigned_total(),
                            interval_seconds_min,
                            interval_seconds_max,
                        )
                    break

            has_next_user = index < len(users)
            if has_next_user and not douyin_interaction_stop_requested:
                remaining_seconds = (
                    random.randint(interval_seconds_min, interval_seconds_max)
                    if interval_seconds_max > interval_seconds_min
                    else interval_seconds_min
                )
                async with state_lock:
                    worker_state["status"] = "cooldown"
                    worker_state["current_message_text"] = ""
                    worker_state["cooldown_remaining"] = remaining_seconds
                    refresh_interaction_state_from_workers(
                        get_interaction_assigned_total(),
                        interval_seconds_min,
                        interval_seconds_max,
                    )
                while remaining_seconds > 0 and not douyin_interaction_stop_requested:
                    wait_seconds = min(1, remaining_seconds)
                    await asyncio.sleep(wait_seconds)
                    remaining_seconds -= wait_seconds
                    async with state_lock:
                        worker_state["cooldown_remaining"] = remaining_seconds
                        refresh_interaction_state_from_workers(
                            get_interaction_assigned_total(),
                            interval_seconds_min,
                            interval_seconds_max,
                        )

        async with state_lock:
            worker_state["status"] = "stopped" if douyin_interaction_stop_requested else "completed"
            worker_state["current_user"] = ""
            worker_state["current_message_text"] = ""
            worker_state["cooldown_remaining"] = 0
            refresh_interaction_state_from_workers(
                get_interaction_assigned_total(),
                interval_seconds_min,
                interval_seconds_max,
            )
    except Exception as exc:
        async with state_lock:
            worker_state["status"] = "failed"
            worker_state["current_user"] = ""
            worker_state["current_message_text"] = ""
            worker_state["cooldown_remaining"] = 0
            worker_state["last_error"] = str(exc)
            douyin_interaction_state["last_error"] = str(exc)
            refresh_interaction_state_from_workers(
                get_interaction_assigned_total(),
                interval_seconds_min,
                interval_seconds_max,
            )
        douyin_log(f"[抖音私信] 账号 {account['id']} worker 异常退出：{exc}", "error")
    finally:
        if scraper is not None:
            await scraper.close()


async def run_douyin_interactions(
    users: List[Dict],
    message_mode: str,
    fixed_message: str,
    fixed_messages: Optional[List[str]],
    message_prompt: str,
    message_seed_text: str,
    accounts: List[Dict],
    interval_seconds_min: int,
    interval_seconds_max: int,
):
    global douyin_interaction_running, douyin_interaction_stop_requested, douyin_interaction_background_task

    total = len(users)
    fixed_messages = fixed_messages or ([fixed_message] if fixed_message else [])
    if normalize_douyin_video_comment_mode(message_mode) == "fixed":
        assign_douyin_interaction_fixed_messages(users, fixed_messages)
    batches = distribute_interaction_users(users, accounts)
    workers = [build_interaction_worker_state(item["account"], len(item["users"])) for item in batches]
    douyin_interaction_state.update(
        {
            "running": True,
            "message": "私信任务执行中",
            "total": total,
            "processed": 0,
            "success": 0,
            "failed": 0,
            "interval_seconds": interval_seconds_min,
            "interval_seconds_min": interval_seconds_min,
            "interval_seconds_max": interval_seconds_max,
            "account_id": workers[0]["account_id"] if len(workers) == 1 else None,
            "account_ids": [worker["account_id"] for worker in workers],
            "current_user": "",
            "current_users": [],
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": "",
            "last_error": "",
            "message_mode": normalize_douyin_video_comment_mode(message_mode),
            "message_summary": summarize_douyin_video_comment_settings(
                message_mode,
                fixed_text=(
                    f"{len(fixed_messages)} 条固定话术轮换"
                    if normalize_douyin_video_comment_mode(message_mode) == "fixed" and len(fixed_messages) > 1
                    else fixed_message
                ),
                prompt_text=message_prompt,
                seed_text=message_seed_text,
            ),
            "current_message_text": "",
            "last_message_text": "",
            "workers": workers,
        }
    )

    state_lock = asyncio.Lock()
    refresh_interaction_state_from_workers(total, interval_seconds_min, interval_seconds_max)
    try:
        interval_desc = (
            f"{interval_seconds_min}-{interval_seconds_max} 秒"
            if interval_seconds_min != interval_seconds_max
            else f"{interval_seconds_min} 秒"
        )
        douyin_log(
            f"[抖音私信] 已启动 {len(workers)} 个账号并发，共 {total} 人，间隔 {interval_desc}",
            "info",
        )
        await asyncio.gather(
            *[
                run_douyin_interaction_worker(
                    item["users"],
                    message_mode,
                    fixed_message,
                    message_prompt,
                    message_seed_text,
                    item["account"],
                    interval_seconds_min,
                    interval_seconds_max,
                    worker_state,
                    state_lock,
                )
                for item, worker_state in zip(batches, workers)
            ]
        )
    finally:
        douyin_interaction_running = False
        douyin_interaction_stop_requested = False
        douyin_interaction_background_task = None
        douyin_interaction_state["running"] = False
        douyin_interaction_state["current_user"] = ""
        douyin_interaction_state["current_users"] = []
        douyin_interaction_state["current_message_text"] = ""
        douyin_interaction_state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        douyin_interaction_state["message"] = (
            "私信任务已停止"
            if douyin_interaction_state["processed"] < total
            else "私信任务已完成"
        )
        douyin_log(
            f"[抖音私信] 本轮结束，成功 {douyin_interaction_state['success']}，失败 {douyin_interaction_state['failed']}，使用 {len(workers)} 个账号",
            "info",
        )



def refresh_douyin_stranger_message_progress(
    *,
    phase: str,
    total: int,
    processed: int,
    success: int,
    failed: int,
    account_id: int,
    current_user: str = "",
    current_message_text: str = "",
    message: str = "",
):
    douyin_stranger_message_state.update(
        {
            "running": True,
            "phase": normalize_douyin_text(phase).lower(),
            "total": max(0, int(total or 0)),
            "processed": max(0, int(processed or 0)),
            "success": max(0, int(success or 0)),
            "failed": max(0, int(failed or 0)),
            "account_id": int(account_id or 0) or None,
            "current_user": normalize_douyin_text(current_user),
            "current_message_text": normalize_douyin_text(current_message_text),
            "message": normalize_douyin_text(message),
        }
    )


async def run_douyin_stranger_message_collection(
    account: Dict,
    max_conversations: int,
):
    global douyin_stranger_message_running, douyin_stranger_message_stop_requested
    global douyin_stranger_message_background_task

    config = load_global_config()
    show_browser = should_show_douyin_message_browser(config)
    scraper = create_douyin_message_scraper(account, config)
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    douyin_stranger_message_state.update(
        {
            "running": True,
            "phase": "collect",
            "message": "正在采集陌生人私信",
            "total": max_conversations,
            "processed": 0,
            "success": 0,
            "failed": 0,
            "account_id": account["id"],
            "current_user": "",
            "current_message_text": "",
            "started_at": started_at,
            "finished_at": "",
            "last_error": "",
        }
    )
    try:
        douyin_log(
            f"[抖音私信引流] 开始采集陌生人私信，账号 {account['id']}，最多 {max_conversations} 条，浏览器模式：{'显示窗口' if show_browser else '无头运行'}",
            "info",
        )
        rows = await scraper.collect_stranger_private_messages(
            max_conversations=max_conversations,
            should_stop=lambda: bool(douyin_stranger_message_stop_requested),
            logger=douyin_log,
            progress_callback=lambda processed, total, username: refresh_douyin_stranger_message_progress(
                phase="collect",
                total=total,
                processed=processed,
                success=processed,
                failed=0,
                account_id=account["id"],
                current_user=username,
                message=f"正在采集陌生人私信，已读取 {processed}/{total} 条",
            ),
        )
        count = replace_douyin_stranger_message_results(account["id"], rows)
        mark_douyin_stranger_message_rows_seen(account["id"], rows)
        refresh_douyin_stranger_message_progress(
            phase="collect",
            total=count,
            processed=count,
            success=count,
            failed=0,
            account_id=account["id"],
            current_user="",
            message="陌生人私信采集已完成" if not douyin_stranger_message_stop_requested else "陌生人私信采集已停止",
        )
        douyin_log(
            f"[抖音私信引流] 采集完成，账号 {account['id']} 共得到 {count} 条陌生人私信",
            "warning" if douyin_stranger_message_stop_requested else "success",
        )
    except Exception as exc:
        douyin_stranger_message_state["last_error"] = str(exc)
        douyin_stranger_message_state["message"] = "陌生人私信采集失败"
        douyin_log(f"[抖音私信引流] 采集失败：{exc}", "error")
    finally:
        await scraper.close()
        douyin_stranger_message_running = False
        douyin_stranger_message_stop_requested = False
        douyin_stranger_message_background_task = None
        douyin_stranger_message_state["running"] = False
        douyin_stranger_message_state["current_user"] = ""
        douyin_stranger_message_state["current_message_text"] = ""
        douyin_stranger_message_state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def run_douyin_inbox_collection(
    account: Dict,
    max_conversations: int,
    *,
    auto_reply_enabled: bool = False,
    reply_mode: str = "fixed",
    reply_message: str = "",
    reply_prompt: str = "",
    contact_value: str = "",
):
    global douyin_inbox_running, douyin_inbox_stop_requested
    global douyin_inbox_background_task

    config = load_global_config()
    show_browser = should_show_douyin_message_browser(config)
    scraper = create_douyin_message_scraper(account, config)
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    douyin_inbox_state.update(
        {
            "running": True,
            "phase": "collect",
            "message": "正在采集当前消息页会话",
            "total": max_conversations,
            "processed": 0,
            "success": 0,
            "failed": 0,
            "account_id": account["id"],
            "current_user": "",
            "started_at": started_at,
            "finished_at": "",
            "last_error": "",
        }
    )
    try:
        douyin_log(
            f"[抖音私信聚合] 开始采集当前消息页会话，账号 {account['id']}，最多 {max_conversations} 条，浏览器模式：可见浏览器",
            "info",
        )
        rows = await scraper.collect_chat_page_private_messages(
            max_conversations=max_conversations,
            should_stop=lambda: bool(douyin_inbox_stop_requested),
            logger=douyin_log,
            progress_callback=lambda processed, total, username: douyin_inbox_state.update(
                {
                    "phase": "collect",
                    "total": total,
                    "processed": processed,
                    "success": processed,
                    "failed": 0,
                    "account_id": account["id"],
                    "current_user": username,
                    "message": f"正在采集当前消息页会话，已读取 {processed}/{total} 条",
                }
            ),
        )
        count = replace_douyin_inbox_results(account["id"], rows)
        auto_reply_result = {
            "total": 0,
            "processed": 0,
            "success": 0,
            "failed": 0,
        }
        if auto_reply_enabled:
            reply_rows = collect_pending_douyin_inbox_auto_reply_rows(account["id"], rows)
            if reply_rows:
                douyin_log(
                    f"[抖音私信聚合] 当前消息页采集完成后，开始自动回复 {len(reply_rows)} 个会话",
                    "info",
                )
                auto_reply_result = await send_douyin_inbox_messages_for_monitor(
                    account,
                    reply_rows,
                    reply_mode=reply_mode,
                    reply_message=reply_message,
                    reply_prompt=reply_prompt,
                    contact_value=contact_value,
                )
        douyin_inbox_state.update(
            {
                "phase": "collect",
                "total": count,
                "processed": count,
                "success": count,
                "failed": 0,
                "account_id": account["id"],
                "current_user": "",
                "message": (
                    f"当前消息页会话采集已完成，自动回复 {int(auto_reply_result.get('success', 0))} 个"
                    if auto_reply_enabled and not douyin_inbox_stop_requested
                    else "当前消息页会话采集已完成"
                )
                if not douyin_inbox_stop_requested
                else "当前消息页会话采集已停止",
                "last_auto_reply_total": int(auto_reply_result.get("processed", 0)),
                "last_auto_reply_success": int(auto_reply_result.get("success", 0)),
                "last_auto_reply_failed": int(auto_reply_result.get("failed", 0)),
            }
        )
        douyin_log(
            f"[抖音私信聚合] 采集完成，账号 {account['id']} 共得到 {count} 条当前消息页会话",
            "warning" if douyin_inbox_stop_requested else "success",
        )
    except Exception as exc:
        douyin_inbox_state["last_error"] = str(exc)
        douyin_inbox_state["message"] = "当前消息页会话采集失败"
        douyin_log(f"[抖音私信聚合] 采集失败：{exc}", "error")
    finally:
        await scraper.close()
        douyin_inbox_running = False
        douyin_inbox_stop_requested = False
        douyin_inbox_background_task = None
        douyin_inbox_state["running"] = False
        douyin_inbox_state["current_user"] = ""
        douyin_inbox_state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def collect_pending_douyin_inbox_auto_reply_rows(account_id: int, rows: Optional[List[Dict]] = None) -> List[Dict]:
    target_account_id = int(account_id or 0)
    source_rows = rows if isinstance(rows, list) else collect_douyin_inbox_results(target_account_id)
    candidates: List[Dict] = []
    for raw_row in source_rows or []:
        if not isinstance(raw_row, dict):
            continue
        normalized = normalize_douyin_inbox_row({**raw_row, "account_id": target_account_id})
        if is_douyin_group_like_stranger_message_row(normalized):
            continue
        if not is_douyin_stranger_message_unread_row(normalized):
            continue
        if normalize_douyin_text(normalized.get("reply_status", "pending")) in {"sent", "queued", "processing"}:
            continue
        if not normalize_douyin_text(normalized.get("username", "")):
            continue
        if not normalize_douyin_text(normalized.get("incoming_message", "") or normalized.get("preview_text", "")):
            continue
        candidates.append(normalized)
    return candidates


async def send_douyin_inbox_messages_for_monitor(
    account: Dict,
    rows: List[Dict],
    *,
    reply_mode: str = "fixed",
    reply_message: str = "",
    reply_prompt: str = "",
    contact_value: str = "",
) -> Dict[str, object]:
    worker = get_douyin_inbox_page_worker(int(account.get("id", 0) or 0))
    total = len(rows)
    success = 0
    failed = 0
    processed = 0
    for row in rows:
        current_user = str(row.get("username", "") or row.get("profile_url", "") or "-")
        item_started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            final_message = generate_douyin_inbox_reply_message(
                row,
                mode=reply_mode,
                fixed_text=reply_message,
                prompt_text=reply_prompt,
                contact_value=contact_value,
            )
        except Exception as exc:
            processed += 1
            failed += 1
            update_douyin_inbox_rows(
                [row],
                status="failed",
                error=str(exc),
                message="",
                account_id=int(account.get("id", 0) or 0),
                started_at=item_started_at,
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            douyin_log(f"[抖音私信聚合] 自动回复生成失败：{current_user}，原因：{exc}", "error")
            continue

        update_douyin_inbox_rows(
            [row],
            status="processing",
            error="",
            message=final_message,
            account_id=int(account.get("id", 0) or 0),
            started_at=item_started_at,
            finished_at="",
        )
        try:
            result = await worker.send_message(row, final_message)
            processed += 1
            success += 1
            finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            detail = result.get("detail", {}) if isinstance(result, dict) else {}
            merged_row = {
                **row,
                **(detail if isinstance(detail, dict) else {}),
                "account_id": int(account.get("id", 0) or 0),
                "reply_status": "sent",
                "reply_error": "",
                "reply_message": final_message,
                "reply_account_id": str(int(account.get("id", 0) or 0)),
                "reply_started_at": item_started_at,
                "reply_finished_at": finished_at,
                "reply_updated_at": finished_at,
                "is_unread": False,
                "unread_count": 0,
            }
            merge_douyin_inbox_results(int(account.get("id", 0) or 0), [merged_row])
            douyin_log(f"[抖音私信聚合] 自动回复成功：{current_user}", "success")
        except Exception as exc:
            processed += 1
            failed += 1
            update_douyin_inbox_rows(
                [row],
                status="failed",
                error=str(exc),
                message=final_message,
                account_id=int(account.get("id", 0) or 0),
                started_at=item_started_at,
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            douyin_log(f"[抖音私信聚合] 自动回复失败：{current_user}，原因：{exc}", "error")
        if processed < total:
            await asyncio.sleep(1.2)

    return {
        "total": total,
        "processed": processed,
        "success": success,
        "failed": failed,
        "stopped": False,
    }


async def run_douyin_inbox_monitor_cycle(account_id: int, trigger_type: str = "scheduled") -> Dict[str, object]:
    state = get_douyin_inbox_monitor_state(account_id, create=True)
    started_at = datetime.now()
    started_at_text = started_at.strftime("%Y-%m-%d %H:%M:%S")
    state["last_run_at"] = started_at_text
    state["last_error"] = ""
    state["last_skip_reason"] = ""

    if not bool(state.get("enabled")):
        schedule_next_douyin_inbox_monitor_run(account_id, started_at)
        set_douyin_inbox_monitor_idle_message(account_id, "自动采集未开启。")
        state["last_cycle_status"] = "disabled"
        return {"status": "disabled", "message": "自动采集未开启。"}

    max_conversations = max(
        1,
        min(int(state.get("max_conversations", 100) or 100), 100),
    )
    auto_reply_enabled = bool(state.get("auto_reply_enabled"))
    reply_mode = normalize_douyin_inbox_reply_mode(state.get("reply_mode", "fixed"))
    reply_message = str(state.get("reply_message", "") or "").strip()
    reply_prompt = str(state.get("reply_prompt", "") or "").strip()
    contact_value = str(state.get("contact_value", "") or "").strip()
    config = load_global_config()
    account = get_douyin_account_by_id(account_id, config) if account_id > 0 else get_active_douyin_account(config)
    if not account:
        schedule_next_douyin_inbox_monitor_run(account_id, started_at)
        message = "本轮跳过：当前没有可用的抖音在线账号。"
        state.update(
            {
                "last_cycle_status": "skipped",
                "last_skip_reason": "no_online_account",
                "message": message,
            }
        )
        douyin_log(f"[抖音私信聚合] {message}", "warning")
        return {"status": "skipped", "message": message}
    if str(account.get("status", "") or "").strip() != "online":
        schedule_next_douyin_inbox_monitor_run(account_id, started_at)
        message = f"本轮跳过：账号 {account.get('id')} 还没有完成登录。"
        state.update(
            {
                "account_id": int(account.get("id", 0) or 0) or None,
                "last_cycle_status": "skipped",
                "last_skip_reason": "account_waiting_login",
                "message": message,
            }
        )
        douyin_log(f"[抖音私信聚合] {message}", "warning")
        return {"status": "skipped", "message": message}

    busy, busy_reason = is_douyin_inbox_monitor_busy(int(account.get("id", 0) or 0))
    if busy:
        schedule_next_douyin_inbox_monitor_run(account_id, started_at)
        message = f"本轮跳过：{busy_reason}，等待下一次。"
        state.update(
            {
                "account_id": int(account.get("id", 0) or 0) or None,
                "last_cycle_status": "skipped",
                "last_skip_reason": normalize_douyin_text(busy_reason),
                "message": message,
            }
        )
        douyin_log(f"[抖音私信聚合] {message}", "warning")
        return {"status": "skipped", "message": message}

    scraper = create_douyin_inbox_scraper(account, config)
    state.update(
        {
            "running": True,
            "account_id": account["id"],
            "message": f"正在检查当前消息页会话，账号 {account['id']}，最多读取 {max_conversations} 条。",
            "last_cycle_status": "running",
        }
    )
    try:
        rows = await scraper.collect_chat_page_private_messages(
            max_conversations=max_conversations,
            should_stop=lambda: False,
            logger=douyin_log,
            progress_callback=lambda processed, total, username: state.update(
                {
                    "running": True,
                    "message": f"账号 {account['id']} 自动采集中，已读取 {processed}/{total} 条，当前 {normalize_douyin_text(username)}",
                }
            ),
        )
        changed = merge_douyin_inbox_results(account["id"], rows)
        auto_reply_result = {
            "total": 0,
            "processed": 0,
            "success": 0,
            "failed": 0,
        }
        if auto_reply_enabled:
            reply_rows = collect_pending_douyin_inbox_auto_reply_rows(account["id"], rows)
            if reply_rows:
                douyin_log(
                    f"[抖音私信聚合] 本轮自动采集后，开始自动回复 {len(reply_rows)} 个会话",
                    "info",
                )
                auto_reply_result = await send_douyin_inbox_messages_for_monitor(
                    account,
                    reply_rows,
                    reply_mode=reply_mode,
                    reply_message=reply_message,
                    reply_prompt=reply_prompt,
                    contact_value=contact_value,
                )
        total_count = len([row for row in rows if isinstance(row, dict)])
        unread_total = len(
            [
                row
                for row in rows
                if isinstance(row, dict)
                and is_douyin_stranger_message_unread_row({**row, "account_id": account["id"]})
            ]
        )
        schedule_next_douyin_inbox_monitor_run(account_id, started_at)
        message = (
            f"本轮完成：识别 {total_count} 条当前消息页会话，"
            f"其中未读 {unread_total} 条，当前沉淀 {len(collect_douyin_inbox_results(account['id']))} 条。"
        )
        if auto_reply_enabled and int(auto_reply_result.get("processed", 0) or 0) > 0:
            message += (
                f" 自动回复 {int(auto_reply_result.get('success', 0) or 0)} 成功，"
                f"{int(auto_reply_result.get('failed', 0) or 0)} 失败。"
            )
        state.update(
            {
                "running": False,
                "message": message,
                "last_total_count": total_count,
                "last_unread_count": unread_total,
                "last_auto_reply_total": int(auto_reply_result.get("processed", 0) or 0),
                "last_auto_reply_success": int(auto_reply_result.get("success", 0) or 0),
                "last_auto_reply_failed": int(auto_reply_result.get("failed", 0) or 0),
                "last_skip_reason": "",
                "last_cycle_status": "completed",
            }
        )
        douyin_log(
            (
                f"[抖音私信聚合] 本轮完成，账号 {account['id']} 会话 {total_count} 条，未读 {unread_total} 条，"
                f"更新 {changed} 条，自动回复成功 {int(auto_reply_result.get('success', 0) or 0)} 条"
            ),
            "success",
        )
        return {"status": "completed", "message": message}
    except Exception as exc:
        schedule_next_douyin_inbox_monitor_run(account_id, started_at)
        state.update(
            {
                "running": False,
                "last_error": str(exc),
                "message": f"自动采集失败：{exc}",
                "last_cycle_status": "failed",
            }
        )
        douyin_log(f"[抖音私信聚合] 自动采集失败：{exc}", "error")
        return {"status": "failed", "message": str(exc)}
    finally:
        await scraper.close()
        state["running"] = False
        douyin_inbox_monitor_tasks_by_account.pop(int(account_id or 0), None)
        if bool(state.get("enabled")):
            set_douyin_inbox_monitor_idle_message(account_id, state.get("message", ""))


async def ensure_douyin_inbox_monitor_scheduler():
    now = datetime.now()
    for state in list_douyin_inbox_monitor_states():
        account_id = int(state.get("account_id", 0) or 0)
        if account_id <= 0:
            continue
        if not bool(state.get("enabled")):
            continue
        next_run_text = normalize_douyin_text(state.get("next_run_at", ""))
        if not next_run_text:
            schedule_next_douyin_inbox_monitor_run(account_id, now)
            next_run_text = normalize_douyin_text(state.get("next_run_at", ""))
        try:
            next_run_at = datetime.strptime(next_run_text, "%Y-%m-%d %H:%M:%S")
        except Exception:
            next_run_at = now
        if next_run_at > now:
            continue
        task = douyin_inbox_monitor_tasks_by_account.get(account_id)
        if task and not task.done():
            continue
        douyin_inbox_monitor_tasks_by_account[account_id] = asyncio.create_task(
            run_douyin_inbox_monitor_cycle(account_id, trigger_type="scheduled")
        )


def build_douyin_self_comment_rows(
    account: Dict,
    video: Dict,
    comments: List[Dict],
    precise_users: List[Dict],
    existing_rows: Optional[List[Dict]] = None,
) -> List[Dict]:
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    account_id = int((account or {}).get("id", 0) or 0)
    aweme_id = normalize_douyin_text((video or {}).get("aweme_id", "")) or normalize_douyin_text(extract_aweme_id(str((video or {}).get("url", "") or "")))
    video_url = str((video or {}).get("url", "") or "").strip()
    video_title = str((video or {}).get("title", "") or "").strip()
    precise_index = build_monitor_precise_user_index(precise_users or [])
    existing_map = {
        build_douyin_self_comment_row_key(row): normalize_douyin_self_comment_row(row)
        for row in (existing_rows or [])
        if isinstance(row, dict) and build_douyin_self_comment_row_key(row)
    }
    rows: List[Dict] = []
    for comment in comments or []:
        if not isinstance(comment, dict):
            continue
        normalized = normalize_high_intent_user(comment)
        normalized["profile_url"] = ensure_douyin_profile_url(normalized)
        comment_key = build_douyin_self_comment_comment_key(normalized)
        row = normalize_douyin_self_comment_row(
            {
                **normalized,
                "account_id": account_id,
                "aweme_id": aweme_id,
                "video_url": video_url,
                "video_title": video_title,
                "video_cover_image": str((video or {}).get("cover_image", "") or ""),
                "comment_key": comment_key,
                "first_seen_at": now_text,
                "last_seen_at": now_text,
                "reply_status": "pending",
                "source": "douyin_self_comment_monitor",
            }
        )
        precise_match = find_monitor_precise_match(row, precise_index)
        if precise_match:
            row["is_high_intent"] = True
            for key in ("intent_level", "score", "reason"):
                value = precise_match.get(key, "")
                if value not in (None, ""):
                    row[key] = value
        existing = existing_map.get(build_douyin_self_comment_row_key(row))
        if existing:
            row["first_seen_at"] = existing.get("first_seen_at") or row["first_seen_at"]
            for key in (
                "reply_status",
                "reply_error",
                "reply_message",
                "reply_mode",
                "reply_account_id",
                "reply_started_at",
                "reply_finished_at",
                "reply_updated_at",
            ):
                if existing.get(key):
                    row[key] = existing.get(key)
            if existing.get("is_high_intent"):
                row["is_high_intent"] = True
                for key in ("intent_level", "score", "reason"):
                    if existing.get(key) not in (None, ""):
                        row[key] = existing.get(key)
        rows.append(row)
    return rows


def collect_pending_douyin_self_comment_auto_reply_rows(account_id: int, rows: Optional[List[Dict]] = None) -> List[Dict]:
    source_rows = rows if isinstance(rows, list) else collect_douyin_self_comment_monitor_results(account_id)
    pending: List[Dict] = []
    seen = set()
    for raw_row in source_rows or []:
        if not isinstance(raw_row, dict):
            continue
        row = normalize_douyin_self_comment_row({**raw_row, "account_id": int(account_id or 0)})
        key = build_douyin_self_comment_row_key(row)
        if not key or key in seen:
            continue
        if not row.get("is_high_intent"):
            continue
        if str(row.get("reply_status", "pending") or "pending").lower() in {"sent", "completed", "processing"}:
            continue
        pending.append(row)
        seen.add(key)
    return pending


async def send_douyin_self_comment_replies_for_monitor(
    account: Dict,
    rows: List[Dict],
    *,
    reply_mode: str = "fixed",
    reply_message: str = "",
    reply_prompt: str = "",
    contact_value: str = "",
    image_path: str = "",
) -> Dict[str, int]:
    account_id = int((account or {}).get("id", 0) or 0)
    image_path = str(image_path or "").strip()
    result = {"total": len(rows or []), "processed": 0, "success": 0, "failed": 0}
    scraper = DouyinCommentScraper(account_id=account["id"], cdp_port=account["port"])
    try:
        for raw_row in rows or []:
            row = normalize_douyin_self_comment_row({**raw_row, "account_id": account_id})
            started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            final_message = ""
            try:
                if normalize_douyin_inbox_reply_mode(reply_mode) == "fixed" and not str(reply_message or "").strip() and image_path:
                    final_message = ""
                else:
                    final_message = generate_douyin_self_comment_reply_message(
                        row,
                        mode=reply_mode,
                        fixed_text=reply_message,
                        prompt_text=reply_prompt,
                        contact_value=contact_value,
                    )
                update_douyin_self_comment_monitor_rows(
                    [row],
                    status="processing",
                    error="",
                    message=final_message,
                    reply_mode=reply_mode,
                    account_id=account_id,
                    started_at=started_at,
                    finished_at="",
                )
                await scraper.reply_to_video_comment(
                    str(row.get("video_url", "") or ""),
                    final_message,
                    row,
                    expected_title=str(row.get("video_title", "") or ""),
                    image_path=image_path,
                    logger=douyin_log,
                )
                finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                update_douyin_self_comment_monitor_rows(
                    [row],
                    status="sent",
                    error="",
                    message=final_message,
                    reply_mode=reply_mode,
                    account_id=account_id,
                    started_at=started_at,
                    finished_at=finished_at,
                )
                result["success"] += 1
            except Exception as exc:
                update_douyin_self_comment_monitor_rows(
                    [row],
                    status="failed",
                    error=str(exc),
                    message=final_message,
                    reply_mode=reply_mode,
                    account_id=account_id,
                    started_at=started_at,
                    finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                result["failed"] += 1
                douyin_log(f"[抖音我的评论区] 自动回复失败：{row.get('username') or '-'}，原因：{exc}", "error")
            finally:
                result["processed"] += 1
    finally:
        await scraper.close()
    return result


async def send_douyin_self_comment_replies_on_loaded_page(
    scraper: DouyinCommentScraper,
    page,
    account: Dict,
    rows: List[Dict],
    *,
    reply_mode: str = "fixed",
    reply_message: str = "",
    reply_prompt: str = "",
    contact_value: str = "",
    image_path: str = "",
) -> Dict[str, int]:
    account_id = int((account or {}).get("id", 0) or 0)
    image_path = str(image_path or "").strip()
    result = {"total": len(rows or []), "processed": 0, "success": 0, "failed": 0}
    for raw_row in rows or []:
        row = normalize_douyin_self_comment_row({**raw_row, "account_id": account_id})
        started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        final_message = ""
        try:
            if normalize_douyin_inbox_reply_mode(reply_mode) == "fixed" and not str(reply_message or "").strip() and image_path:
                final_message = ""
            else:
                final_message = generate_douyin_self_comment_reply_message(
                    row,
                    mode=reply_mode,
                    fixed_text=reply_message,
                    prompt_text=reply_prompt,
                    contact_value=contact_value,
                )
            update_douyin_self_comment_monitor_rows(
                [row],
                status="processing",
                error="",
                message=final_message,
                reply_mode=reply_mode,
                account_id=account_id,
                started_at=started_at,
                finished_at="",
            )
            await scraper.reply_to_loaded_video_comment(
                page,
                final_message,
                row,
                image_path=image_path,
                logger=douyin_log,
                allow_scroll=False,
            )
            update_douyin_self_comment_monitor_rows(
                [row],
                status="sent",
                error="",
                message=final_message,
                reply_mode=reply_mode,
                account_id=account_id,
                started_at=started_at,
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            result["success"] += 1
        except Exception as exc:
            update_douyin_self_comment_monitor_rows(
                [row],
                status="failed",
                error=str(exc),
                message=final_message,
                reply_mode=reply_mode,
                account_id=account_id,
                started_at=started_at,
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            result["failed"] += 1
            douyin_log(f"[抖音我的评论区] 当前批次自动回复失败：{row.get('username') or '-'}，原因：{exc}", "error")
        finally:
            result["processed"] += 1
    return result


async def run_douyin_self_comment_monitor_cycle(account_id: int, trigger_type: str = "scheduled") -> Dict[str, object]:
    state = get_douyin_self_comment_monitor_state(account_id, create=True)
    started_at = datetime.now()
    started_at_text = started_at.strftime("%Y-%m-%d %H:%M:%S")
    state["last_run_at"] = started_at_text
    state["last_error"] = ""
    state["last_skip_reason"] = ""

    if not bool(state.get("enabled")):
        schedule_next_douyin_self_comment_monitor_run(account_id, started_at)
        set_douyin_self_comment_monitor_idle_message(account_id, "我的评论区监控未开启。")
        state["last_cycle_status"] = "disabled"
        return {"status": "disabled", "message": "我的评论区监控未开启。"}

    config = load_global_config()
    account = get_douyin_account_by_id(account_id, config) if account_id > 0 else get_active_douyin_account(config)
    if not account:
        schedule_next_douyin_self_comment_monitor_run(account_id, started_at)
        message = "本轮跳过：当前没有可用的抖音在线账号。"
        state.update({"last_cycle_status": "skipped", "last_skip_reason": "no_online_account", "message": message})
        douyin_log(f"[抖音我的评论区] {message}", "warning")
        return {"status": "skipped", "message": message}
    if str(account.get("status", "") or "").strip() != "online":
        schedule_next_douyin_self_comment_monitor_run(account_id, started_at)
        message = f"本轮跳过：账号 {account.get('id')} 还没有完成登录。"
        state.update({"account_id": int(account.get("id", 0) or 0) or None, "last_cycle_status": "skipped", "last_skip_reason": "account_waiting_login", "message": message})
        douyin_log(f"[抖音我的评论区] {message}", "warning")
        return {"status": "skipped", "message": message}

    busy, busy_reason = is_douyin_stranger_message_monitor_busy(int(account.get("id", 0) or 0))
    if not busy and douyin_inbox_running and int(douyin_inbox_state.get("account_id", 0) or 0) == int(account.get("id", 0) or 0):
        busy, busy_reason = True, "消息聚合采集任务正在执行"
    if busy:
        schedule_next_douyin_self_comment_monitor_run(account_id, started_at)
        message = f"本轮跳过：{busy_reason}，等待下一次。"
        state.update({"account_id": int(account.get("id", 0) or 0) or None, "last_cycle_status": "skipped", "last_skip_reason": normalize_douyin_text(busy_reason), "message": message})
        douyin_log(f"[抖音我的评论区] {message}", "warning")
        return {"status": "skipped", "message": message}

    max_videos = max(1, min(int(state.get("max_videos", 20) or 20), 100))
    max_comments = max(5, min(int(state.get("max_comments_per_video", 80) or 80), 500))
    auto_reply_enabled = bool(state.get("auto_reply_enabled"))
    reply_mode = normalize_douyin_inbox_reply_mode(state.get("reply_mode", "fixed"))
    reply_message = str(state.get("reply_message", "") or "").strip()
    reply_prompt = str(state.get("reply_prompt", "") or "").strip()
    contact_value = str(state.get("contact_value", "") or "").strip()
    reply_image_path = str(state.get("reply_image_path", "") or state.get("comment_image_path", "") or "").strip()
    ai_client = create_ai_client()
    scraper = DouyinCommentScraper(account_id=account["id"], cdp_port=account["port"])
    state.update(
        {
            "running": True,
            "account_id": account["id"],
            "message": f"正在检查我的评论区：账号 {account['id']}，作品 {max_videos} 个，每个最多 {max_comments} 条评论。",
            "last_cycle_status": "running",
        }
    )
    douyin_log(f"[抖音我的评论区] 开始检查账号 {account['id']} 的作品评论区。", "info")
    try:
        profile_payload = await scraper.scrape_self_videos(max_videos=max_videos, logger=douyin_log)
        videos = (profile_payload.get("videos", []) if isinstance(profile_payload, dict) else []) or []
        total_comments = 0
        new_comments_total = 0
        precise_total = 0
        auto_reply_result = {"total": 0, "processed": 0, "success": 0, "failed": 0}
        merged_rows_all: List[Dict] = []
        existing_rows_all = collect_douyin_self_comment_monitor_results(account["id"])
        existing_keys = {build_douyin_self_comment_row_key(row) for row in existing_rows_all if build_douyin_self_comment_row_key(row)}
        for video in videos[:max_videos]:
            try:
                if not isinstance(video, dict):
                    continue
                video_url = str(video.get("url", "") or "").strip()
                if not video_url:
                    continue
                batch_comments_for_video: List[Dict] = []

                async def handle_self_comment_batch(page, batch_comments: List[Dict], batch_index: int):
                    nonlocal new_comments_total, precise_total, auto_reply_result, existing_rows_all, batch_comments_for_video
                    if not batch_comments:
                        return
                    batch_comments_for_video.extend(batch_comments)
                    candidate_rows = build_douyin_self_comment_rows(account, video, batch_comments, [], existing_rows_all)
                    fresh_comments = [
                        row
                        for row in candidate_rows
                        if build_douyin_self_comment_row_key(row) and build_douyin_self_comment_row_key(row) not in existing_keys
                    ]
                    precise_users: List[Dict] = []
                    if fresh_comments:
                        _filter_prompt = get_douyin_comment_direction(config)
                        _filter_strategy = get_douyin_comment_filter_strategy(config)
                        _filter_started_at = time.time()
                        log_douyin_filter_event(
                            "start",
                            scope="self_comment_monitor_batch",
                            aweme_id=str(video.get("aweme_id", "") or ""),
                            title=video.get("title", ""),
                            comments_in=len(fresh_comments),
                            strategy=_filter_strategy,
                            prompt=_filter_prompt,
                        )
                        douyin_log(
                            f"[抖音我的评论区] 当前作品第 {batch_index} 批采集 {len(batch_comments)} 条，新评论 {len(fresh_comments)} 条，开始 AI 筛选。",
                            "info",
                        )
                        precise_users = await asyncio.to_thread(
                            lambda: ai_client.filter_comments(
                                str(video.get("title", "") or ""),
                                fresh_comments,
                                _filter_prompt,
                                "douyin_transactional",
                                "",
                                _filter_strategy,
                                event_logger=log_douyin_filter_event,
                            )
                        )
                        log_douyin_filter_event(
                            "done",
                            scope="self_comment_monitor_batch",
                            aweme_id=str(video.get("aweme_id", "") or ""),
                            title=video.get("title", ""),
                            comments_in=len(fresh_comments),
                            precise_out=len(precise_users or []),
                            strategy=_filter_strategy,
                            duration_ms=int((time.time() - _filter_started_at) * 1000),
                        )
                    rows = build_douyin_self_comment_rows(account, video, batch_comments, precise_users, existing_rows_all)
                    merge_douyin_self_comment_monitor_results(account["id"], rows)
                    merged_rows_all.extend(rows)
                    existing_rows_all = collect_douyin_self_comment_monitor_results(account["id"])
                    new_comments_total += len(fresh_comments)
                    precise_total += len(precise_users or [])
                    if auto_reply_enabled:
                        reply_rows = collect_pending_douyin_self_comment_auto_reply_rows(account["id"], rows)
                        if reply_rows:
                            douyin_log(
                                f"[抖音我的评论区] 当前作品第 {batch_index} 批命中 {len(reply_rows)} 条待回复高意向评论，原地自动回复。",
                                "info",
                            )
                            current_reply_result = await send_douyin_self_comment_replies_on_loaded_page(
                                scraper,
                                page,
                                account,
                                reply_rows,
                                reply_mode=reply_mode,
                                reply_message=reply_message,
                                reply_prompt=reply_prompt,
                                contact_value=contact_value,
                                image_path=reply_image_path,
                            )
                            for key in ("total", "processed", "success", "failed"):
                                auto_reply_result[key] = int(auto_reply_result.get(key, 0) or 0) + int(current_reply_result.get(key, 0) or 0)
                    for row in rows:
                        key = build_douyin_self_comment_row_key(row)
                        if key:
                            existing_keys.add(key)

                comments = await scraper.process_video_comment_batches(
                    video_url,
                    max_comments=max_comments,
                    batch_size=1,
                    max_scroll_rounds=max(10, min(int(config.get("comment_scroll_rounds", 80) or 80), 300)),
                    logger=douyin_log,
                    on_batch=handle_self_comment_batch,
                )
                total_comments += len(comments or [])
            except Exception as video_exc:
                douyin_log(
                    f"[抖音我的评论区] 当前作品评论采集跳过：{video.get('title') if isinstance(video, dict) else '-'}，原因：{video_exc}",
                    "warning",
                )
                continue

        schedule_next_douyin_self_comment_monitor_run(account_id, started_at)
        message = f"本轮完成：检查 {len(videos)} 个作品，读取 {total_comments} 条评论，新增 {new_comments_total} 条，高意向 {precise_total} 条。"
        if auto_reply_enabled and int(auto_reply_result.get("processed", 0) or 0) > 0:
            message += f" 自动回复 {int(auto_reply_result.get('success', 0) or 0)} 成功，{int(auto_reply_result.get('failed', 0) or 0)} 失败。"
        state.update(
            {
                "running": False,
                "message": message,
                "last_video_count": len(videos),
                "last_comment_count": total_comments,
                "last_new_comment_count": new_comments_total,
                "last_precise_count": precise_total,
                "last_auto_reply_total": int(auto_reply_result.get("processed", 0) or 0),
                "last_auto_reply_success": int(auto_reply_result.get("success", 0) or 0),
                "last_auto_reply_failed": int(auto_reply_result.get("failed", 0) or 0),
                "last_skip_reason": "",
                "last_cycle_status": "completed",
            }
        )
        douyin_log(f"[抖音我的评论区] {message}", "success")
        return {"status": "completed", "message": message}
    except Exception as exc:
        schedule_next_douyin_self_comment_monitor_run(account_id, started_at)
        state.update(
            {
                "running": False,
                "last_error": str(exc),
                "message": f"我的评论区监控失败：{exc}",
                "last_cycle_status": "failed",
            }
        )
        douyin_log(f"[抖音我的评论区] 监控失败：{exc}", "error")
        return {"status": "failed", "message": str(exc)}
    finally:
        await scraper.close()
        state["running"] = False
        douyin_self_comment_monitor_tasks_by_account.pop(int(account_id or 0), None)
        if bool(state.get("enabled")):
            set_douyin_self_comment_monitor_idle_message(account_id, state.get("message", ""))


async def ensure_douyin_self_comment_monitor_scheduler():
    now = datetime.now()
    for state in list_douyin_self_comment_monitor_states():
        account_id = int(state.get("account_id", 0) or 0)
        if account_id <= 0 or not bool(state.get("enabled")):
            continue
        next_run_text = normalize_douyin_text(state.get("next_run_at", ""))
        if not next_run_text:
            schedule_next_douyin_self_comment_monitor_run(account_id, now)
            next_run_text = normalize_douyin_text(state.get("next_run_at", ""))
        try:
            next_run_at = datetime.strptime(next_run_text, "%Y-%m-%d %H:%M:%S")
        except Exception:
            next_run_at = now
        if next_run_at > now:
            continue
        task = douyin_self_comment_monitor_tasks_by_account.get(account_id)
        if task and not task.done():
            continue
        douyin_self_comment_monitor_tasks_by_account[account_id] = asyncio.create_task(
            run_douyin_self_comment_monitor_cycle(account_id, trigger_type="scheduled")
        )

async def run_douyin_stranger_message_send(
    account: Dict,
    rows: List[Dict],
    message: str,
    *,
    reply_mode: str = "fixed",
    reply_prompt: str = "",
    contact_value: str = "",
):
    global douyin_stranger_message_running, douyin_stranger_message_stop_requested
    global douyin_stranger_message_background_task

    config = load_global_config()
    show_browser = should_show_douyin_message_browser(config)
    scraper = create_douyin_message_scraper(account, config)
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(rows)
    douyin_stranger_message_state.update(
        {
            "running": True,
            "phase": "send",
            "message": "陌生人私信发送中",
            "total": total,
            "processed": 0,
            "success": 0,
            "failed": 0,
            "account_id": account["id"],
            "current_user": "",
            "current_message_text": "",
            "started_at": started_at,
            "finished_at": "",
            "last_error": "",
        }
    )
    success = 0
    failed = 0
    processed = 0
    result = {
        "total": total,
        "processed": 0,
        "success": 0,
        "failed": 0,
        "stopped": False,
    }
    shared_page = None
    try:
        douyin_log(
            f"[抖音私信引流] 开始发送引流私信，账号 {account['id']}，共 {total} 人，浏览器模式：{'显示窗口' if show_browser else '无头运行'}",
            "info",
        )
        shared_page = await scraper._new_page(logger=douyin_log)
        for row in rows:
            if douyin_stranger_message_stop_requested:
                break

            current_user = str(row.get("username", "") or row.get("profile_url", ""))
            item_started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            final_message = message
            try:
                final_message = generate_douyin_stranger_reply_message(
                    row,
                    mode=reply_mode,
                    fixed_text=message,
                    prompt_text=reply_prompt,
                    contact_value=contact_value,
                )
            except Exception as exc:
                processed += 1
                failed += 1
                update_douyin_stranger_message_rows(
                    [row],
                    status="failed",
                    error=str(exc),
                    message="",
                    account_id=account["id"],
                    started_at=item_started_at,
                    finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                douyin_stranger_message_state["last_error"] = str(exc)
                douyin_log(f"[抖音私信引流] 生成回复失败：{current_user}，原因：{exc}", "error")
                refresh_douyin_stranger_message_progress(
                    phase="send",
                    total=total,
                    processed=processed,
                    success=success,
                    failed=failed,
                    account_id=account["id"],
                    current_user=current_user,
                    current_message_text="",
                    message="陌生人私信发送中",
                )
                if processed < total and not douyin_stranger_message_stop_requested:
                    await asyncio.sleep(2)
                continue
            refresh_douyin_stranger_message_progress(
                phase="send",
                total=total,
                processed=processed,
                success=success,
                failed=failed,
                account_id=account["id"],
                current_user=current_user,
                current_message_text=final_message,
                message=f"正在发送引流私信，第 {processed + 1}/{total} 人",
            )
            update_douyin_stranger_message_rows(
                [row],
                status="processing",
                error="",
                message=final_message,
                account_id=account["id"],
                started_at=item_started_at,
                finished_at="",
            )
            try:
                await scraper.send_stranger_private_message(
                    row,
                    final_message,
                    logger=douyin_log,
                    page=shared_page,
                )
                processed += 1
                success += 1
                update_douyin_stranger_message_rows(
                    [row],
                    status="sent",
                    error="",
                    message=final_message,
                    account_id=account["id"],
                    started_at=item_started_at,
                    finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                douyin_stranger_message_state["last_message_text"] = final_message
                douyin_log(f"[抖音私信引流] 发送成功：{current_user}", "success")
            except Exception as exc:
                processed += 1
                failed += 1
                update_douyin_stranger_message_rows(
                    [row],
                    status="failed",
                    error=str(exc),
                    message=final_message,
                    account_id=account["id"],
                    started_at=item_started_at,
                    finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                douyin_stranger_message_state["last_error"] = str(exc)
                douyin_log(f"[抖音私信引流] 发送失败：{current_user}，原因：{exc}", "error")

            refresh_douyin_stranger_message_progress(
                phase="send",
                total=total,
                processed=processed,
                success=success,
                failed=failed,
                account_id=account["id"],
                current_user=current_user,
                current_message_text=final_message,
                message="陌生人私信发送中",
            )
            if processed < total and not douyin_stranger_message_stop_requested:
                await asyncio.sleep(2)

        douyin_stranger_message_state["message"] = (
            "陌生人私信发送已停止"
            if douyin_stranger_message_stop_requested and processed < total
            else "陌生人私信发送已完成"
        )
        douyin_log(
            f"[抖音私信引流] 本轮结束，成功 {success}，失败 {failed}，账号 {account['id']}",
            "warning" if douyin_stranger_message_stop_requested and processed < total else "info",
        )
        result = {
            "total": total,
            "processed": processed,
            "success": success,
            "failed": failed,
            "stopped": bool(douyin_stranger_message_stop_requested and processed < total),
        }
    finally:
        if shared_page is not None:
            try:
                await shared_page.close()
            except Exception:
                pass
        await scraper.close()
        douyin_stranger_message_running = False
        douyin_stranger_message_stop_requested = False
        douyin_stranger_message_background_task = None
        douyin_stranger_message_state["running"] = False
        douyin_stranger_message_state["current_user"] = ""
        douyin_stranger_message_state["current_message_text"] = ""
        douyin_stranger_message_state["processed"] = processed
        douyin_stranger_message_state["success"] = success
        douyin_stranger_message_state["failed"] = failed
        douyin_stranger_message_state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return result


async def send_douyin_stranger_messages_for_monitor(
    account: Dict,
    rows: List[Dict],
    message: str,
    *,
    reply_mode: str = "fixed",
    reply_prompt: str = "",
    contact_value: str = "",
) -> Dict[str, object]:
    config = load_global_config()
    scraper = create_douyin_message_scraper(account, config)
    total = len(rows)
    success = 0
    failed = 0
    processed = 0
    shared_page = None
    try:
        shared_page = await scraper._new_page(logger=douyin_log)
        for row in rows:
            current_user = str(row.get("username", "") or row.get("profile_url", ""))
            item_started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                final_message = generate_douyin_stranger_reply_message(
                    row,
                    mode=reply_mode,
                    fixed_text=message,
                    prompt_text=reply_prompt,
                    contact_value=contact_value,
                )
            except Exception as exc:
                processed += 1
                failed += 1
                update_douyin_stranger_message_rows(
                    [row],
                    status="failed",
                    error=str(exc),
                    message="",
                    account_id=account["id"],
                    started_at=item_started_at,
                    finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                douyin_log(f"[抖音陌生人消息监控] 自动回复生成失败：{current_user}，原因：{exc}", "error")
                if processed < total:
                    await asyncio.sleep(2)
                continue
            update_douyin_stranger_message_rows(
                [row],
                status="processing",
                error="",
                message=final_message,
                account_id=account["id"],
                started_at=item_started_at,
                finished_at="",
            )
            try:
                await scraper.send_stranger_private_message(
                    row,
                    final_message,
                    logger=douyin_log,
                    page=shared_page,
                )
                processed += 1
                success += 1
                update_douyin_stranger_message_rows(
                    [row],
                    status="sent",
                    error="",
                    message=final_message,
                    account_id=account["id"],
                    started_at=item_started_at,
                    finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                douyin_log(f"[抖音陌生人消息监控] 自动回复成功：{current_user}", "success")
            except Exception as exc:
                processed += 1
                failed += 1
                update_douyin_stranger_message_rows(
                    [row],
                    status="failed",
                    error=str(exc),
                    message=final_message,
                    account_id=account["id"],
                    started_at=item_started_at,
                    finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                douyin_log(f"[抖音陌生人消息监控] 自动回复失败：{current_user}，原因：{exc}", "error")
            if processed < total:
                await asyncio.sleep(2)
    finally:
        if shared_page is not None:
            try:
                await shared_page.close()
            except Exception:
                pass
        await scraper.close()
    return {
        "total": total,
        "processed": processed,
        "success": success,
        "failed": failed,
        "stopped": False,
    }


async def run_douyin_stranger_message_monitor_cycle(account_id: int, trigger_type: str = "scheduled") -> Dict[str, object]:
    state = get_douyin_stranger_message_monitor_state(account_id, create=True)
    started_at = datetime.now()
    started_at_text = started_at.strftime("%Y-%m-%d %H:%M:%S")
    state["last_run_at"] = started_at_text
    state["last_error"] = ""
    state["last_skip_reason"] = ""

    if not bool(state.get("enabled")):
        schedule_next_douyin_stranger_message_monitor_run(account_id, started_at)
        set_douyin_stranger_message_monitor_idle_message(account_id, "监控未开启。")
        state["last_cycle_status"] = "disabled"
        return {"status": "disabled", "message": "监控未开启。"}

    max_conversations = max(
        1,
        min(int(state.get("max_conversations", 100) or 100), 100),
    )
    auto_reply_enabled = bool(state.get("auto_reply_enabled", True))
    reply_mode = normalize_douyin_stranger_reply_mode(state.get("reply_mode", "fixed"))
    reply_message = str(state.get("reply_message", "") or "").strip()
    reply_prompt = str(state.get("reply_prompt", "") or "").strip()
    contact_value = str(state.get("contact_value", "") or "").strip()
    config = load_global_config()
    account = get_douyin_account_by_id(account_id, config) if account_id > 0 else get_active_douyin_account(config)
    if not account:
        schedule_next_douyin_stranger_message_monitor_run(account_id, started_at)
        message = "本轮跳过：当前没有可用的抖音在线账号。"
        state.update(
            {
                "last_cycle_status": "skipped",
                "last_skip_reason": "no_online_account",
                "message": message,
            }
        )
        douyin_log(f"[抖音陌生人消息监控] {message}", "warning")
        return {"status": "skipped", "message": message}
    if str(account.get("status", "") or "").strip() != "online":
        schedule_next_douyin_stranger_message_monitor_run(account_id, started_at)
        message = f"本轮跳过：账号 {account.get('id')} 还没有完成登录。"
        state.update(
            {
                "account_id": int(account.get("id", 0) or 0) or None,
                "last_cycle_status": "skipped",
                "last_skip_reason": "account_waiting_login",
                "message": message,
            }
        )
        douyin_log(f"[抖音陌生人消息监控] {message}", "warning")
        return {"status": "skipped", "message": message}

    busy, busy_reason = is_douyin_stranger_message_monitor_busy(int(account.get("id", 0) or 0))
    if busy:
        schedule_next_douyin_stranger_message_monitor_run(account_id, started_at)
        message = f"本轮跳过：{busy_reason}，等待下一次。"
        state.update(
            {
                "account_id": int(account.get("id", 0) or 0) or None,
                "last_cycle_status": "skipped",
                "last_skip_reason": normalize_douyin_text(busy_reason),
                "message": message,
            }
        )
        douyin_log(f"[抖音陌生人消息监控] {message}", "warning")
        return {"status": "skipped", "message": message}

    config = load_global_config()
    show_browser = should_show_douyin_message_browser(config)
    scraper = create_douyin_message_scraper(account, config)
    state.update(
        {
            "running": True,
            "account_id": account["id"],
            "phase": "monitor_collect",
            "last_cycle_status": "running",
            "message": f"正在检查陌生人消息未读红点，账号 {account['id']}，最多读取 {max_conversations} 条会话。",
            "last_auto_reply_total": 0,
            "last_auto_reply_success": 0,
            "last_auto_reply_failed": 0,
            "seen_message_count": get_douyin_stranger_message_seen_count(account["id"]),
        }
    )
    try:
        douyin_log(
            f"[抖音陌生人消息监控] 开始检查，账号 {account['id']}，最多 {max_conversations} 条，浏览器模式：{'显示窗口' if show_browser else '无头运行'}",
            "info",
        )
        rows = await scraper.collect_stranger_private_messages(
            max_conversations=max_conversations,
            should_stop=lambda: False,
            logger=douyin_log,
            progress_callback=lambda processed, total, username: state.update(
                {
                    "running": True,
                    "message": f"账号 {account['id']} 监控检查中，已读取 {processed}/{total} 条，会优先识别带未读红点的会话，当前 {normalize_douyin_text(username)}",
                }
            ),
        )
        changed = merge_douyin_stranger_message_results(account["id"], rows)
        unread_rows = [
            normalize_douyin_stranger_message_row({**row, "account_id": account["id"]})
            for row in rows
            if isinstance(row, dict) and is_douyin_stranger_message_unread_row({**row, "account_id": account["id"]})
        ]
        unseen_rows, seen_rows = split_unseen_douyin_stranger_message_rows(account["id"], unread_rows)
        new_count = len(unseen_rows)
        mark_douyin_stranger_message_rows_seen(account["id"], unread_rows)
        total_count = len([row for row in rows if isinstance(row, dict)])
        unread_total = len(unread_rows)
        seen_total = get_douyin_stranger_message_seen_count(account["id"])
        auto_reply_result = {
            "total": 0,
            "processed": 0,
            "success": 0,
            "failed": 0,
            "stopped": False,
        }
        if auto_reply_enabled and unseen_rows:
            auto_reply_total = len(unseen_rows)
            queued_message = (
                reply_message
                if reply_mode == "fixed"
                else "\n".join(
                    [
                        "AI 引导加绿泡泡",
                        "麻烦您绿泡泡",
                        normalize_douyin_text(contact_value),
                    ]
                )
            )
            update_douyin_stranger_message_rows(
                unseen_rows,
                status="queued",
                error="",
                message=queued_message,
                account_id=account["id"],
                started_at="",
                finished_at="",
            )
            state["message"] = (
                f"本轮识别到 {new_count} 条带未读红点的新会话，正在自动回复 {auto_reply_total} 个新会话。"
            )
            douyin_log(
                f"[抖音陌生人消息监控] 本轮识别到 {new_count} 条带未读红点的新会话，开始自动回复 {auto_reply_total} 个新会话",
                "info",
            )
            auto_reply_result = await send_douyin_stranger_messages_for_monitor(
                account,
                unseen_rows,
                reply_message,
                reply_mode=reply_mode,
                reply_prompt=reply_prompt,
                contact_value=contact_value,
            )
        schedule_next_douyin_stranger_message_monitor_run(account_id, started_at)
        auto_reply_suffix = ""
        if auto_reply_enabled:
            if unseen_rows:
                success_count = int(auto_reply_result.get("success", 0))
                failed_count = int(auto_reply_result.get("failed", 0))
                auto_reply_suffix = (
                    f"，已自动回复 {success_count} 个"
                    f"{f'，失败 {failed_count} 个' if failed_count > 0 else ''}"
                )
            else:
                auto_reply_suffix = "，本轮没有新的未读会话，不需要自动回复"
        else:
            auto_reply_suffix = "，当前只监控未读，不自动回复"
        message = (
            f"本轮完成：识别 {total_count} 条会话，其中带未读红点 {unread_total} 条，"
            f"新增未读 {new_count} 条，历史去重 {len(seen_rows)} 条，"
            f"当前沉淀 {len(collect_douyin_stranger_message_results(account['id']))} 条"
            f"{auto_reply_suffix}。"
        )
        state.update(
            {
                "running": False,
                "message": message,
                "last_new_count": new_count,
                "last_total_count": total_count,
                "last_auto_reply_total": int(auto_reply_result.get("processed", 0)),
                "last_auto_reply_success": int(auto_reply_result.get("success", 0)),
                "last_auto_reply_failed": int(auto_reply_result.get("failed", 0)),
                "last_skip_reason": "",
                "last_cycle_status": "completed",
                "seen_message_count": seen_total,
            }
        )
        douyin_log(
            f"[抖音陌生人消息监控] 本轮完成，账号 {account['id']} 未读 {unread_total} 条，新增未读 {new_count} 条，读取 {total_count} 条，会话更新 {changed} 条",
            "success",
        )
        return {
            "status": "completed",
            "message": message,
            "account_id": int(account["id"]),
            "new_count": int(new_count),
            "total_count": int(total_count),
            "unread_count": int(unread_total),
            "updated_count": int(changed),
            "auto_reply_total": int(auto_reply_result.get("processed", 0)),
            "auto_reply_success": int(auto_reply_result.get("success", 0)),
            "auto_reply_failed": int(auto_reply_result.get("failed", 0)),
        }
    except Exception as exc:
        schedule_next_douyin_stranger_message_monitor_run(account_id, started_at)
        state.update(
            {
                "running": False,
                "last_error": str(exc),
                "last_cycle_status": "failed",
                "message": f"监控检查失败：{exc}",
            }
        )
        douyin_log(f"[抖音陌生人消息监控] 检查失败：{exc}", "error")
        return {"status": "failed", "message": str(exc), "error": str(exc)}
    finally:
        await scraper.close()
        state["running"] = False
        douyin_stranger_message_monitor_tasks_by_account.pop(int(account_id or 0), None)


async def douyin_stranger_message_monitor_loop():
    while True:
        try:
            enabled_states = [state for state in list_douyin_stranger_message_monitor_states() if bool(state.get("enabled"))]
            if not enabled_states:
                await asyncio.sleep(10)
                continue

            now = datetime.now()
            for state in enabled_states:
                account_id = int(state.get("account_id", 0) or 0)
                if account_id <= 0:
                    continue
                next_run_text = normalize_douyin_text(state.get("next_run_at", ""))
                if not next_run_text:
                    next_run_text = schedule_next_douyin_stranger_message_monitor_run(account_id, now)
                    set_douyin_stranger_message_monitor_idle_message(account_id)
                should_run = False
                try:
                    should_run = now >= datetime.strptime(next_run_text, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    should_run = True
                if not should_run or bool(state.get("running")):
                    continue
                task = douyin_stranger_message_monitor_tasks_by_account.get(account_id)
                if task and not task.done():
                    continue
                douyin_stranger_message_monitor_tasks_by_account[account_id] = asyncio.create_task(
                    run_douyin_stranger_message_monitor_cycle(account_id, trigger_type="scheduled")
                )

            await asyncio.sleep(15)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            douyin_log(f"[抖音陌生人消息监控] 调度异常：{exc}", "error")
            await asyncio.sleep(30)


async def ensure_douyin_stranger_message_monitor_scheduler():
    global douyin_stranger_message_monitor_task, douyin_stranger_message_monitor_started
    if (
        douyin_stranger_message_monitor_started
        and douyin_stranger_message_monitor_task
        and not douyin_stranger_message_monitor_task.done()
    ):
        return
    douyin_stranger_message_monitor_started = True
    for state in list_douyin_stranger_message_monitor_states():
        account_id = int(state.get("account_id", 0) or 0)
        if account_id <= 0:
            continue
        if bool(state.get("enabled")):
            if not normalize_douyin_text(state.get("next_run_at", "")):
                schedule_next_douyin_stranger_message_monitor_run(account_id, datetime.now())
            set_douyin_stranger_message_monitor_idle_message(account_id)
        else:
            state.update(
                {
                    "running": False,
                    "message": "陌生人消息监控未开启。",
                    "last_cycle_status": normalize_douyin_text(state.get("last_cycle_status", "idle")) or "idle",
                    "next_run_at": "",
                }
            )
    douyin_stranger_message_monitor_task = asyncio.create_task(douyin_stranger_message_monitor_loop())


async def run_douyin_group_member_collection(
    account: Dict,
    group_keyword: str,
    max_groups: int,
    max_members_per_group: int,
    selected_groups: Optional[List[str]] = None,
):
    global douyin_group_member_running, douyin_group_member_stop_requested, douyin_group_member_background_task
    global douyin_group_member_results

    scraper = DouyinCommentScraper(account_id=account["id"], cdp_port=account["port"])
    selected_group_names = [str(item or "").strip() for item in (selected_groups or []) if str(item or "").strip()]
    douyin_group_member_state.update(
        {
            "running": True,
            "message": "群成员提取执行中",
            "total_groups": len(selected_group_names) if selected_group_names else max_groups,
            "processed_groups": 0,
            "total_members": 0,
            "account_id": account["id"],
            "group_keyword": group_keyword,
            "selected_groups": selected_group_names,
            "current_group": "",
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": "",
            "last_error": "",
        }
    )

    try:
        douyin_log(
            f"[抖音群成员] 使用账号 {account['id']} 启动，群关键词：{group_keyword or '自动识别群聊'}，最多群 {max_groups}，每群最多成员 {max_members_per_group}",
            "info",
        )
        rows = await scraper.collect_chat_group_members_v2(
            group_keyword=group_keyword,
            max_groups=max_groups,
            max_members_per_group=max_members_per_group,
            selected_groups=selected_group_names,
            should_stop=lambda: bool(douyin_group_member_stop_requested),
            logger=douyin_log,
        )
        douyin_group_member_results = rows
        save_douyin_group_member_results()
        group_names = {
            str(row.get("group_name", "")).strip()
            for row in rows
            if isinstance(row, dict) and str(row.get("group_name", "")).strip()
        }
        douyin_group_member_state["processed_groups"] = len(group_names)
        douyin_group_member_state["total_members"] = len(rows)
        if douyin_group_member_stop_requested:
            douyin_group_member_state["message"] = "群成员提取已停止"
        else:
            douyin_group_member_state["message"] = "群成员提取已完成"
        douyin_log(
            f"[抖音群成员] 本轮结束，共提取 {len(group_names)} 个群、{len(rows)} 位成员",
            "warning" if douyin_group_member_stop_requested else "success",
        )
    except Exception as exc:
        douyin_group_member_state["last_error"] = str(exc)
        douyin_group_member_state["message"] = "群成员提取失败"
        douyin_log(f"[抖音群成员] 提取失败：{exc}", "error")
    finally:
        await scraper.close()
        douyin_group_member_running = False
        douyin_group_member_stop_requested = False
        douyin_group_member_background_task = None
        douyin_group_member_state["running"] = False
        douyin_group_member_state["current_group"] = ""
        douyin_group_member_state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def export_search_rows(data: List[Dict]) -> str:
    rows = []
    for item in data:
        rows.append(
            {
                "标题": item.get("title", ""),
                "作者": item.get("author", ""),
                "视频链接": item.get("url", ""),
                "AwemeID": item.get("aweme_id", ""),
                "点赞数": item.get("likes", 0),
                "发布时间": item.get("publish_time", ""),
                "视频时长": item.get("duration", ""),
                "默认符合条件": "是" if item.get("criteria_matched") else "否",
                "筛选理由": item.get("criteria_reason", ""),
                "历史重复": "是" if item.get("is_historical_duplicate") else "否",
                "首次发现时间": item.get("first_seen_at", ""),
                "最近发现时间": item.get("last_seen_at", ""),
                "累计出现次数": item.get("seen_count", 1),
                "导出勾选": "是" if item.get("export_selected") else "否",
            }
        )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = DOUYIN_DAILY_DIR / f"douyin_search_results_{ts}.xlsx"
    _write_excel_sheets(filepath, [("搜索结果", rows)])
    return str(filepath)


def export_task_results() -> str:
    if not douyin_tasks:
        raise ValueError("No Douyin task results available for export")

    comment_rows = []
    high_intent_rows = []
    for task in douyin_tasks:
        task_id = task.get("id")
        title = task.get("title", "")
        url = task.get("url", "")
        author = task.get("author", "")

        for comment in task.get("all_comments", []) or []:
            comment_rows.append(
                {
                    "任务ID": task_id,
                    "标题": title,
                    "作者": author,
                    "视频链接": url,
                    "评论序号": comment.get("comment_index", ""),
                    "评论ID": comment.get("comment_id", ""),
                    "用户ID": comment.get("user_id", ""),
                    "用户名": comment.get("username", ""),
                    "评论内容": comment.get("comment", comment.get("content", "")),
                    "评论时间": comment.get("comment_time", ""),
                    "点赞数": comment.get("like_count", 0),
                    "用户主页": comment.get("profile_url", ""),
                }
            )

        for user in task.get("high_intent_users", []) or []:
            high_intent_rows.append(
                {
                    "任务ID": task_id,
                    "标题": title,
                    "作者": author,
                    "视频链接": url,
                    "评论序号": user.get("comment_index", ""),
                    "评论ID": user.get("comment_id", ""),
                    "用户ID": user.get("user_id", ""),
                    "用户名": user.get("username", ""),
                    "评论内容": user.get("comment", user.get("content", "")),
                    "评论时间": user.get("comment_time", ""),
                    "点赞数": user.get("like_count", 0),
                    "筛选级别": user.get("intent_level", ""),
                    "筛选分数": user.get("score", ""),
                    "筛选理由": user.get("reason", ""),
                    "用户主页": user.get("profile_url", ""),
                    "互动状态": user.get("interaction_status", ""),
                    "互动错误": user.get("interaction_error", ""),
                    "私信内容": user.get("interaction_message", ""),
                    "互动账号": user.get("interaction_account_id", ""),
                    "开始互动时间": user.get("interaction_started_at", ""),
                    "完成互动时间": user.get("interaction_finished_at", ""),
                    "关注评论状态": user.get("follow_comment_status", ""),
                    "关注评论结果": user.get("follow_comment_result", ""),
                    "关注评论错误": user.get("follow_comment_error", ""),
                    "关注评论内容": user.get("follow_comment_text", ""),
                    "关注评论账号": user.get("follow_comment_account_id", ""),
                    "关注评论开始时间": user.get("follow_comment_started_at", ""),
                    "关注评论完成时间": user.get("follow_comment_finished_at", ""),
                }
            )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = DOUYIN_DAILY_DIR / f"douyin_comment_results_{ts}.xlsx"
    sheets = [
        ("全部评论用户", comment_rows),
        ("高意向客户", high_intent_rows),
    ]
    _write_excel_sheets(filepath, sheets)
    _write_excel_sheets(LATEST_RESULTS_FILE, sheets)
    return str(filepath)


def export_group_member_results() -> str:
    if not douyin_group_member_results:
        raise ValueError("No Douyin group member results available for export")

    rows = []
    for item in douyin_group_member_results:
        rows.append(
            {
                "群名称": item.get("group_name", ""),
                "成员昵称": item.get("username", ""),
                "成员角色": item.get("role", ""),
                "抖音号": item.get("douyin_id", ""),
                "地区": item.get("region", ""),
                "主页链接": ensure_douyin_profile_url(item),
                "SecUserID": item.get("sec_user_id", ""),
                "群预览": item.get("group_preview", ""),
            }
        )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = DOUYIN_DAILY_DIR / f"douyin_group_members_{ts}.xlsx"
    _write_excel_sheets(filepath, [("群成员", rows)])
    return str(filepath)


def import_group_members_to_interaction_pool(rows: List[Dict]) -> int:
    global douyin_manual_interaction_users

    valid_pool = {user_choice_key(row): row for row in collect_douyin_interaction_users()}
    for user in douyin_manual_interaction_users:
        key = user_choice_key(user)
        if key:
            valid_pool[key] = user

    imported = 0
    for item in rows:
        if not isinstance(item, dict):
            continue
        normalized = normalize_high_intent_user(
            {
                "username": item.get("username", ""),
                "profile_url": ensure_douyin_profile_url(item),
                "region": item.get("region", ""),
                "comment": f"[群成员] 来自群聊：{item.get('group_name', '')}",
                "content": f"[群成员] 来自群聊：{item.get('group_name', '')}",
                "comment_time": "",
                "like_count": "",
                "reply_count": "",
                "intent_level": "group_member",
                "reason": f"群成员导入：{item.get('group_name', '')}",
                "task_title": f"[群聊] {item.get('group_name', '')}",
                "group_name": item.get("group_name", ""),
                "douyin_id": item.get("douyin_id", ""),
                "sec_user_id": item.get("sec_user_id", ""),
                "role": item.get("role", ""),
            }
        )
        key = user_choice_key(normalized)
        if not key or key in valid_pool:
            continue
        valid_pool[key] = normalized
        douyin_manual_interaction_users.append(normalized)
        imported += 1

    if imported:
        douyin_manual_interaction_users = dedupe_users(douyin_manual_interaction_users)
        save_douyin_manual_interaction_users()
    return imported


@router.get("/monitor/targets")
async def douyin_get_monitor_targets():
    latest_run = douyin_state_store.load_latest_douyin_monitor_run()
    return {
        "code": 200,
        "targets": load_monitor_targets_bundle(),
        "state": douyin_monitor_runtime_state,
        "latest_run": latest_run,
    }


@router.post("/monitor/keyword-expand-search")
async def douyin_monitor_keyword_expand_search(http_request: Request = None, request: Optional[dict] = None):
    nurture_conflict = build_douyin_nurture_conflict("执行 AI 拓词搜索")
    if nurture_conflict:
        return nurture_conflict
    set_douyin_ai_auth_token_from_request(http_request)
    payload = request if isinstance(request, dict) else {}
    seed_keyword = normalize_douyin_text(payload.get("keyword", ""))
    if not seed_keyword:
        return {"code": 400, "msg": "请输入一个种子关键词。"}

    expand_count = max(1, min(int(payload.get("expand_count", 5) or 5), 5))
    max_results_per_keyword = max(10, min(int(payload.get("max_results_per_keyword", 50) or 50), 100))
    interval_min_seconds = max(5, min(int(payload.get("interval_min_seconds", 12) or 12), 120))
    interval_max_seconds = max(interval_min_seconds, min(int(payload.get("interval_max_seconds", 25) or 25), 180))
    include_seed = bool(payload.get("include_seed", True))

    config = load_global_config()
    account = get_active_douyin_account(config)
    if not account:
        return {
            "code": 400,
            "type": "no_online_account",
            "msg": "当前没有在线抖音账号，无法执行 AI 拓词搜索。",
            "keywords": [],
            "results": [],
        }

    try:
        expanded_keywords = await asyncio.to_thread(
            lambda: expand_douyin_keywords_with_ai(seed_keyword, limit=expand_count)
        )
    except Exception as exc:
        douyin_log(f"[抖音同行监控] AI 拓词失败：{seed_keyword}，原因：{exc}", "error")
        return {"code": 500, "msg": f"AI 拓词失败：{exc}", "keywords": [], "results": []}

    keywords = []
    seen_keywords: Set[str] = set()
    for keyword in ([seed_keyword] if include_seed else []) + expanded_keywords:
        normalized = normalize_douyin_text(keyword)
        lowered = normalized.lower()
        if not normalized or lowered in seen_keywords:
            continue
        seen_keywords.add(lowered)
        keywords.append(normalized)

    combined_results: List[Dict] = []
    combined_by_key: Dict[str, Dict] = {}
    keyword_summaries: List[Dict] = []
    errors: List[Dict] = []
    for index, keyword in enumerate(keywords, start=1):
        try:
            if index > 1:
                wait_seconds = random.randint(interval_min_seconds, interval_max_seconds)
                douyin_log(
                    f"[抖音同行监控] AI 拓词搜索等待 {wait_seconds} 秒后继续，降低账号搜索频率。",
                    "info",
                )
                await asyncio.sleep(wait_seconds)
            douyin_log(
                f"[抖音同行监控] AI 拓词搜索 {index}/{len(keywords)}：{keyword}",
                "info",
            )
            search_payload = await run_douyin_keyword_search(
                account,
                keyword,
                max_results=max_results_per_keyword,
                update_latest=False,
            )
            results = search_payload.get("results", []) if isinstance(search_payload, dict) else []
            added_count = 0
            for item in results:
                if not isinstance(item, dict):
                    continue
                key = build_douyin_search_session_item_key(item)
                if not key:
                    continue
                if key in combined_by_key:
                    hit_keywords = combined_by_key[key].setdefault("expanded_hit_keywords", [])
                    if keyword not in hit_keywords:
                        hit_keywords.append(keyword)
                    continue
                row = dict(item)
                row["expanded_seed_keyword"] = seed_keyword
                row["expanded_hit_keywords"] = [keyword]
                combined_by_key[key] = row
                combined_results.append(row)
                added_count += 1
            keyword_summaries.append(
                {
                    "keyword": keyword,
                    "total": len(results),
                    "added": added_count,
                }
            )
        except Exception as exc:
            errors.append({"keyword": keyword, "error": str(exc)})
            douyin_log(f"[抖音同行监控] 拓词搜索失败：{keyword}，原因：{exc}", "error")

    combined_results.sort(
        key=lambda row: (
            int(row.get("comments", 0) or 0),
            int(row.get("likes", 0) or 0),
        ),
        reverse=True,
    )
    douyin_search_cache["latest"] = combined_results
    pool_session = upsert_douyin_search_session_state(
        keyword=f"{seed_keyword} · AI拓词池",
        account_id=account.get("id", ""),
        results=combined_results,
        capture_state={
            "source": "monitor_ai_expand_pool",
            "seed_keyword": seed_keyword,
            "keywords": keywords,
            "keyword_summaries": keyword_summaries,
            "errors": errors,
            "interval_seconds": [interval_min_seconds, interval_max_seconds],
            "searched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )

    message = f"AI 已拓展 {len(expanded_keywords)} 个关键词，完成 {len(keyword_summaries)} 个关键词搜索，去重后视频池 {len(combined_results)} 条。"
    if errors:
        message += f" 其中 {len(errors)} 个关键词失败。"
    douyin_log(f"[抖音同行监控] {message}", "success" if combined_results else "warning")
    return {
        "code": 200,
        "msg": message,
        "seed_keyword": seed_keyword,
        "expanded_keywords": expanded_keywords,
        "keywords": keywords,
        "keyword_summaries": keyword_summaries,
        "errors": errors,
        "interval_seconds": [interval_min_seconds, interval_max_seconds],
        "results": combined_results,
        "total": len(combined_results),
        "session": pool_session,
        "account_id": account.get("id"),
    }


@router.post("/monitor/targets")
async def douyin_add_monitor_target(request: Optional[dict] = None):
    nurture_conflict = build_douyin_nurture_conflict("添加同行监控")
    if nurture_conflict:
        return nurture_conflict
    payload = request if isinstance(request, dict) else {}
    share_text = str(payload.get("share_url", "") or payload.get("profile_url", "") or "").strip()
    share_url = resolve_douyin_share_url(share_text)
    if not share_url:
        return {"code": 400, "msg": "未识别到可用链接，请直接粘贴抖音分享文案或主页链接。"}

    config = load_global_config()
    account = get_active_douyin_account(config)
    if not account:
        return {
            "code": 400,
            "type": "no_online_account",
            "msg": "当前没有在线抖音账号，请先登录后再添加同行。",
        }

    monitor_profile_url = share_url
    seed_profile: Dict = {}
    scraper = DouyinCommentScraper(account_id=account["id"], cdp_port=account["port"])
    protocol_client = None
    protocol_auth = None
    try:
        try:
            protocol_client, protocol_auth = await prepare_douyin_monitor_protocol_channel(account)
            douyin_log(
                f"[抖音同行监控] 账号 {account['id']} 已准备协议作品同步通道，用于添加同行。",
                "info",
            )
        except Exception as exc:
            protocol_client = None
            protocol_auth = None
            douyin_log(
                f"[抖音同行监控] 添加同行时协议作品同步通道不可用，将使用页面方式：{exc}",
                "warning",
            )
        if is_douyin_video_url(share_url):
            if protocol_client is None or protocol_auth is None:
                return {
                    "code": 400,
                    "msg": "当前粘贴的是视频链接，需要先通过协议读取视频作者主页；请确认账号在线后重试，或直接粘贴同行主页链接。",
                }
            video_author_profile = await asyncio.to_thread(
                lambda: protocol_client.get_work_author_profile(protocol_auth, share_url)
            )
            monitor_profile_url = str(video_author_profile.get("profile_url", "") or "").strip()
            if not monitor_profile_url:
                return {"code": 400, "msg": "未能从视频链接中识别作者主页，请直接粘贴同行主页链接。"}
            seed_profile = {
                key: value
                for key, value in video_author_profile.items()
                if key
                in {
                    "username",
                    "douyin_id",
                    "sec_user_id",
                    "profile_url",
                    "avatar_url",
                    "signature",
                    "bio",
                    "fans_count_text",
                    "follow_count_text",
                    "liked_count_text",
                }
            }
            douyin_log(
                f"[抖音同行监控] 已从视频链接识别同行作者：{seed_profile.get('username') or monitor_profile_url}",
                "info",
            )
        existing_target = douyin_state_store.find_douyin_monitor_target(profile_url=monitor_profile_url)
        seed_target = {
            **existing_target,
            **seed_profile,
            "profile_url": monitor_profile_url,
            "status": "active",
            "auto_collect_new": bool(existing_target.get("auto_collect_new", True)) if existing_target else True,
        }
        synced_target = await sync_monitor_target_profile_and_videos(
            seed_target,
            scraper,
            initial_sync=not bool(existing_target),
            protocol_client=protocol_client,
            protocol_auth=protocol_auth,
        )
        douyin_log(f"[抖音同行监控] 已添加同行：{synced_target.get('username') or synced_target.get('profile_url')}", "success")
        return {
            "code": 200,
            "msg": "同行主页已加入监控，并已自动从分享文案中提取链接、同步最近 10 条视频。",
            "target": synced_target,
            "targets": load_monitor_targets_bundle(),
        }
    except Exception as exc:
        douyin_log(f"[抖音同行监控] 添加同行失败：{exc}", "error")
        return {"code": 500, "msg": f"添加同行失败：{exc}"}
    finally:
        await scraper.close()


@router.post("/monitor/targets/{target_id}/videos/select")
async def douyin_select_monitor_videos(target_id: int, request: Optional[dict] = None):
    payload = request if isinstance(request, dict) else {}
    aweme_ids = payload.get("aweme_ids", [])
    if not isinstance(aweme_ids, list):
        return {"code": 400, "msg": "aweme_ids 必须是数组。"}
    douyin_state_store.set_douyin_monitor_video_selection(target_id, aweme_ids)
    return {
        "code": 200,
        "msg": f"已保存 {len([item for item in aweme_ids if str(item or '').strip()])} 条视频的监控选择。",
        "targets": load_monitor_targets_bundle(),
    }


@router.post("/monitor/targets/{target_id}/refresh")
async def douyin_refresh_monitor_target(target_id: int):
    nurture_conflict = build_douyin_nurture_conflict("刷新同行主页")
    if nurture_conflict:
        return nurture_conflict
    target = next(
        (
            item
            for item in douyin_state_store.load_douyin_monitor_targets()
            if int(item.get("target_id", 0) or 0) == int(target_id)
            and str(item.get("status", "active") or "active").strip() == "active"
        ),
        None,
    )
    if not target:
        return {"code": 404, "msg": "未找到对应的同行监控。"}
    if douyin_monitor_runtime_state.get("running"):
        return {
            "code": 409,
            "msg": "同行监控正在执行中，请等待当前这一轮结束后再刷新。",
            "state": douyin_monitor_runtime_state,
            "targets": load_monitor_targets_bundle(),
        }

    config = load_global_config()
    account = get_active_douyin_account(config)
    if not account:
        return {"code": 400, "msg": "当前没有在线抖音账号，无法刷新同行主页。"}

    scraper = DouyinCommentScraper(account_id=account["id"], cdp_port=account["port"])
    protocol_client = None
    protocol_auth = None
    try:
        try:
            protocol_client, protocol_auth = await prepare_douyin_monitor_protocol_channel(account)
            douyin_log(
                f"[抖音同行监控] 账号 {account['id']} 已准备协议作品同步通道，用于刷新同行。",
                "info",
            )
        except Exception as exc:
            protocol_client = None
            protocol_auth = None
            douyin_log(
                f"[抖音同行监控] 刷新同行时协议作品同步通道不可用，将使用页面方式：{exc}",
                "warning",
            )
        synced = await sync_monitor_target_profile_and_videos(
            target,
            scraper,
            initial_sync=False,
            protocol_client=protocol_client,
            protocol_auth=protocol_auth,
        )
        message = f"已刷新同行“{synced.get('username') or synced.get('profile_url') or target_id}”的主页和最近 10 条视频，本次未采集评论。"
        douyin_log(f"[抖音同行监控] 已刷新同行主页与视频：{synced.get('username') or synced.get('profile_url')}", "success")
        return {
            "code": 200,
            "msg": message,
            "result": {
                "status": "completed",
                "message": message,
                "target_id": int(synced.get("target_id", 0) or 0),
                "videos_total": 0,
                "new_comments": 0,
                "new_precise": 0,
                "refresh_only": True,
            },
            "target": synced,
            "targets": load_monitor_targets_bundle(),
        }
    except Exception as exc:
        douyin_log(f"[抖音同行监控] 刷新同行失败：{exc}", "error")
        return {
            "code": 500,
            "msg": f"刷新当前同行失败：{exc}",
            "result": {
                "status": "failed",
                "message": str(exc),
                "target_id": int(target_id or 0),
                "refresh_only": True,
            },
            "targets": load_monitor_targets_bundle(),
        }
    finally:
        await scraper.close()


@router.post("/monitor/targets/{target_id}/delete")
async def douyin_delete_monitor_target(target_id: int):
    target = next(
        (item for item in douyin_state_store.load_douyin_monitor_targets() if int(item.get("target_id", 0) or 0) == int(target_id)),
        None,
    )
    if not target:
        return {"code": 404, "msg": "未找到对应的同行监控。"}
    stopped = douyin_state_store.stop_douyin_monitor_target(target_id)
    if not stopped:
        return {"code": 500, "msg": "停止同行监控失败，请稍后重试。"}
    douyin_log(f"[抖音同行监控] 已停止同行：{target.get('username') or target.get('profile_url')}", "warning")
    return {
        "code": 200,
        "msg": f"已停止同行监控：{target.get('username') or target.get('profile_url') or target_id}，历史视频和客户沉淀已保留。",
        "targets": load_monitor_targets_bundle(),
    }


@router.post("/monitor/run")
async def douyin_run_monitor_targets(http_request: Request = None, request: Optional[dict] = None):
    nurture_conflict = build_douyin_nurture_conflict("执行同行监控")
    if nurture_conflict:
        return nurture_conflict
    set_douyin_ai_auth_token_from_request(http_request)
    payload = request if isinstance(request, dict) else {}
    target_ids = payload.get("target_ids", [])
    if target_ids is not None and not isinstance(target_ids, list):
        return {"code": 400, "msg": "target_ids 必须是数组。"}
    if douyin_monitor_runtime_state.get("running"):
        return {
            "code": 409,
            "msg": "同行监控正在执行中，请等待当前这一轮结束后再试。",
            "state": douyin_monitor_runtime_state,
            "targets": load_monitor_targets_bundle(),
        }
    try:
        result = await run_douyin_monitor_cycle(
            trigger_type="manual",
            target_ids=[int(item) for item in (target_ids or []) if int(item or 0)],
        )
    except Exception as exc:
        return {
            "code": 500,
            "msg": f"同行监控执行失败：{exc}",
            "result": {"status": "failed", "message": str(exc)},
            "state": douyin_monitor_runtime_state,
            "targets": load_monitor_targets_bundle(),
        }
    return {
        "code": 200 if result.get("status") == "completed" else 500,
        "msg": result.get("error") or "同行监控已执行完成。",
        "result": result,
        "targets": load_monitor_targets_bundle(),
    }


@router.get("/monitor/customer-pools")
async def douyin_get_monitor_customer_pools(target_id: int = 0):
    all_rows, precise_rows = build_monitor_customer_pools(target_id if target_id > 0 else None)
    return {
        "code": 200,
        "all_customers": all_rows,
        "precise_customers": precise_rows,
    }


@router.post("/monitor/targets/{target_id}/videos/{aweme_id}/refilter")
async def douyin_refilter_monitor_video_customers(
    target_id: int,
    aweme_id: str,
    http_request: Request = None,
    request: Optional[dict] = None,
):
    set_douyin_ai_auth_token_from_request(http_request)
    payload = request if isinstance(request, dict) else {}
    target_id = int(target_id or 0)
    aweme_id = str(aweme_id or "").strip()
    if target_id <= 0 or not aweme_id:
        return {"code": 400, "msg": "缺少有效的视频标识。"}

    target = next(
        (item for item in douyin_state_store.load_douyin_monitor_targets() if int(item.get("target_id", 0) or 0) == target_id),
        None,
    )
    if not target:
        return {"code": 404, "msg": "未找到对应的同行监控对象。"}

    video = next(
        (
            item
            for item in douyin_state_store.load_douyin_monitor_videos(target_id)
            if str(item.get("aweme_id", "") or "").strip() == aweme_id
        ),
        None,
    )
    if not video:
        return {"code": 404, "msg": "未找到对应的视频。"}

    comments = [
        item
        for item in douyin_state_store.load_douyin_monitor_comments(target_id)
        if str(item.get("aweme_id", "") or "").strip() == aweme_id
    ]
    if not comments:
        return {"code": 400, "msg": "这条视频还没有已采集的评论，暂时不能筛选客户。"}

    prompt_text = str(payload.get("prompt", "") or "").strip()
    config = load_global_config()
    prompt_to_use = prompt_text or get_douyin_comment_direction(config)
    strategy_to_use = normalize_douyin_comment_filter_strategy(
        payload.get("strategy") or get_douyin_comment_filter_strategy(config)
    )
    ai_client = create_ai_client()

    _filter_started_at = time.time()
    log_douyin_filter_event(
        "start",
        scope="monitor_refilter",
        target_id=int(target_id or 0),
        aweme_id=str(aweme_id or ""),
        title=video.get("title", ""),
        comments_in=len(comments),
        strategy=strategy_to_use,
        prompt=prompt_to_use,
    )
    try:
        precise_users = await asyncio.to_thread(
            lambda: ai_client.filter_comments(
                str(video.get("title", "") or ""),
                comments,
                prompt_to_use,
                "douyin_transactional",
                "",
                strategy_to_use,
                event_logger=log_douyin_filter_event,
            )
        )
    except Exception as exc:
        log_douyin_filter_event(
            "error",
            scope="monitor_refilter",
            target_id=int(target_id or 0),
            aweme_id=str(aweme_id or ""),
            title=video.get("title", ""),
            comments_in=len(comments),
            strategy=strategy_to_use,
            duration_ms=int((time.time() - _filter_started_at) * 1000),
            error=str(exc),
        )
        return {"code": 500, "msg": f"重新筛选失败：{exc}"}

    log_douyin_filter_event(
        "done",
        scope="monitor_refilter",
        target_id=int(target_id or 0),
        aweme_id=str(aweme_id or ""),
        title=video.get("title", ""),
        comments_in=len(comments),
        precise_out=len(precise_users or []),
        strategy=strategy_to_use,
        duration_ms=int((time.time() - _filter_started_at) * 1000),
    )
    comment_rows = build_monitor_comment_rows(target, video, comments, precise_users)
    douyin_state_store.save_douyin_monitor_comments(comment_rows)
    all_rows, precise_rows = build_monitor_customer_pools(target_id)
    douyin_log(
        f"[抖音同行监控] 已重新筛选视频《{video.get('title') or aweme_id}》，评论 {len(comments)} 条，精准客户 {len(precise_users)} 人",
        "success",
    )
    return {
        "code": 200,
        "msg": f"已完成重新筛选：评论 {len(comments)} 条，精准客户 {len(precise_users)} 人。",
        "all_customers": all_rows,
        "precise_customers": precise_rows,
        "all_count": len([row for row in all_rows if str(row.get('latest_video_url', '') or '') == str(video.get('url', '') or '')]),
        "precise_count": len([row for row in precise_rows if str(row.get('latest_video_url', '') or '') == str(video.get('url', '') or '')]),
    }


@router.get("/monitor/status")
async def douyin_get_monitor_status():
    return {
        "code": 200,
        "state": douyin_monitor_runtime_state,
        "latest_run": douyin_state_store.load_latest_douyin_monitor_run(),
    }


@router.get("/config")
async def douyin_get_config(http_request: Request = None):
    config = load_global_config()
    local_api_key_configured = bool(str(config.get("api_key", "") or "").strip())
    server_ai_proxy_available = douyin_ai_available(config, http_request) and not local_api_key_configured
    return {
        "code": 200,
        "model": config.get("model", "gpt-5.4"),
        "api_key_configured": local_api_key_configured or server_ai_proxy_available,
        "local_api_key_configured": local_api_key_configured,
        "server_ai_proxy_available": server_ai_proxy_available,
        "comment_direction": get_douyin_comment_direction(config),
        "comment_filter_strategy": get_douyin_comment_filter_strategy(config),
        "search_min_likes": config.get("search_min_likes", 500),
        "search_avg_multiplier": config.get("search_avg_multiplier", 1.5),
        "search_exclude_historical_duplicates": config.get("search_exclude_historical_duplicates", True),
        "comment_scroll_rounds": config.get("comment_scroll_rounds", 300),
        "comment_max_comments": config.get("comment_max_comments", 500),
        "douyin_default_account_id": config.get("douyin_default_account_id", 1),
        "douyin_accounts": _normalize_accounts(config.get("douyin_accounts")),
        "douyin_message_show_browser": bool(config.get("douyin_message_show_browser", False)),
        "douyin_nurture_interval_min_minutes": config.get("douyin_nurture_interval_min_minutes", 120),
        "douyin_nurture_interval_max_minutes": config.get("douyin_nurture_interval_max_minutes", 180),
        "douyin_nurture_session_min_minutes": config.get("douyin_nurture_session_min_minutes", 20),
        "douyin_nurture_session_max_minutes": config.get("douyin_nurture_session_max_minutes", 40),
        "douyin_nurture_active_start_hour": config.get("douyin_nurture_active_start_hour", 9),
        "douyin_nurture_active_end_hour": config.get("douyin_nurture_active_end_hour", 23),
    }


@router.post("/config")
async def douyin_update_config(request: dict):
    config = load_global_config()

    if "model" in request:
        config["model"] = str(request["model"] or "").strip()
    if "comment_direction" in request:
        config["douyin_comment_direction"] = str(request["comment_direction"] or "").strip()
    if "comment_filter_strategy" in request:
        config["douyin_comment_filter_strategy"] = normalize_douyin_comment_filter_strategy(request["comment_filter_strategy"])
    if "search_min_likes" in request:
        config["search_min_likes"] = max(0, int(request["search_min_likes"] or 0))
    if "search_avg_multiplier" in request:
        config["search_avg_multiplier"] = max(0, float(request["search_avg_multiplier"] or 0))
    if "search_exclude_historical_duplicates" in request:
        config["search_exclude_historical_duplicates"] = bool(request["search_exclude_historical_duplicates"])
    if "comment_scroll_rounds" in request:
        config["comment_scroll_rounds"] = max(20, min(int(request["comment_scroll_rounds"] or 20), 300))
    if "comment_max_comments" in request:
        config["comment_max_comments"] = max(20, min(int(request["comment_max_comments"] or 20), 500))
    if "douyin_default_account_id" in request:
        config["douyin_default_account_id"] = int(request["douyin_default_account_id"] or 1)
    if "douyin_accounts" in request:
        config["douyin_accounts"] = _normalize_accounts(request["douyin_accounts"])
    if "douyin_message_show_browser" in request:
        config["douyin_message_show_browser"] = bool(request["douyin_message_show_browser"])
    if "douyin_nurture_interval_min_minutes" in request:
        config["douyin_nurture_interval_min_minutes"] = max(30, int(request["douyin_nurture_interval_min_minutes"] or 30))
    if "douyin_nurture_interval_max_minutes" in request:
        config["douyin_nurture_interval_max_minutes"] = max(
            int(config.get("douyin_nurture_interval_min_minutes", 120) or 120),
            int(request["douyin_nurture_interval_max_minutes"] or config.get("douyin_nurture_interval_min_minutes", 120) or 120),
        )
    if "douyin_nurture_session_min_minutes" in request:
        config["douyin_nurture_session_min_minutes"] = max(5, int(request["douyin_nurture_session_min_minutes"] or 5))
    if "douyin_nurture_session_max_minutes" in request:
        config["douyin_nurture_session_max_minutes"] = max(
            int(config.get("douyin_nurture_session_min_minutes", 20) or 20),
            int(request["douyin_nurture_session_max_minutes"] or config.get("douyin_nurture_session_min_minutes", 20) or 20),
        )
    if "douyin_nurture_active_start_hour" in request:
        config["douyin_nurture_active_start_hour"] = max(6, min(int(request["douyin_nurture_active_start_hour"] or 9), 20))
    if "douyin_nurture_active_end_hour" in request:
        config["douyin_nurture_active_end_hour"] = max(
            int(config.get("douyin_nurture_active_start_hour", 9) or 9) + 1,
            min(int(request["douyin_nurture_active_end_hour"] or 23), 23),
        )

    save_global_config(config)
    return {"code": 200, "msg": "抖音配置保存成功"}


@router.get("/stats")
async def douyin_get_stats():
    reconcile_douyin_runtime_state()
    nurture_running = reconcile_douyin_account_nurture_runtime_state()
    config = load_global_config()
    accounts = _normalize_accounts(config.get("douyin_accounts"))
    active_account = get_active_douyin_account(config)

    return {
        "code": 200,
        "total_tasks": len(douyin_tasks),
        "completed_tasks": sum(1 for task in douyin_tasks if task.get("status") == "completed"),
        "high_intent_users": sum(len(task.get("high_intent_users", []) or []) for task in douyin_tasks),
        "online_accounts": sum(1 for account in accounts if account.get("status") == "online"),
        "douyin_accounts": accounts,
        "douyin_default_account_id": config.get("douyin_default_account_id", 1),
        "active_account_id": active_account.get("id") if active_account else None,
        "account_nurture_running": nurture_running,
    }


@router.get("/account-nurture/status")
async def douyin_get_account_nurture_status():
    config = load_global_config()
    if reconcile_douyin_account_nurture_runtime_state() and douyin_account_nurture_scheduler:
        snapshot = douyin_account_nurture_scheduler.snapshot()
    else:
        snapshot = build_douyin_account_nurture_idle_status(config)
    return {"code": 200, "status": snapshot}


@router.post("/account-nurture/start")
async def douyin_start_account_nurture(request: Optional[dict] = None):
    global douyin_account_nurture_scheduler, douyin_account_nurture_background_task

    target_account_ids = parse_douyin_nurture_account_ids(request)

    if reconcile_douyin_account_nurture_runtime_state() and douyin_account_nurture_scheduler:
        enabled_ids = douyin_account_nurture_scheduler.enable_accounts(target_account_ids or None)
        snapshot = douyin_account_nurture_scheduler.snapshot()
        if target_account_ids and not enabled_ids:
            return {"code": 400, "type": "no_online_account", "msg": "指定账号当前未登录，无法加入养号。", "status": snapshot}

        if target_account_ids:
            account_text = "、".join(str(account_id) for account_id in enabled_ids)
            return {"code": 200, "msg": f"账号 {account_text} 已加入养号排班。", "status": snapshot}

        return {
            "code": 200,
            "msg": "已把所有已登录账号加入养号排班。",
            "status": snapshot,
        }

    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()
    monitor_running = bool(douyin_monitor_runtime_state.get("running"))
    if (
        douyin_running
        or douyin_video_comment_running
        or douyin_mention_comment_running
        or douyin_follow_comment_running
        or douyin_interaction_running
        or douyin_group_member_running
        or douyin_stranger_message_running
        or monitor_running
    ):
        return {
            "code": 409,
            "type": "douyin_task_conflict",
            "msg": "当前还有其他抖音任务在执行，请先停止后再启动养号。养号期间不要执行其他抖音任务，否则可能冲突。",
        }

    config = load_global_config()
    accounts = _normalize_accounts(config.get("douyin_accounts"))
    online_accounts = [account for account in accounts if account.get("status") == "online"]
    selected_online_accounts = (
        [account for account in online_accounts if int(account.get("id", 0) or 0) in set(target_account_ids)]
        if target_account_ids
        else online_accounts
    )
    if not selected_online_accounts:
        return {"code": 400, "type": "no_online_account", "msg": "当前没有在线抖音账号，无法启动养号。"}

    douyin_account_nurture_scheduler = DouyinAccountNurtureScheduler(
        accounts=accounts,
        config=dict(config),
        broadcast_log=douyin_log,
        enabled_account_ids=[int(account.get("id", 0) or 0) for account in selected_online_accounts],
    )

    async def _run_account_nurture():
        global douyin_account_nurture_background_task, douyin_account_nurture_scheduler
        try:
            if douyin_account_nurture_scheduler:
                await douyin_account_nurture_scheduler.run()
        finally:
            douyin_account_nurture_background_task = None

    douyin_account_nurture_background_task = asyncio.create_task(_run_account_nurture())
    snapshot = douyin_account_nurture_scheduler.snapshot()
    if douyin_account_nurture_scheduler.is_active_window_now():
        message = (
            f"抖音养号已启动，当前使用 {len(selected_online_accounts)} 个在线账号。"
            "养号期间请不要执行其他抖音任务。"
        )
    else:
        next_start_text = douyin_account_nurture_scheduler.next_active_start_text()
        message = (
            f"抖音养号已进入等待队列，当前时间不在养号时段内，将于 {next_start_text} 开始。"
            f"本次计划使用 {len(selected_online_accounts)} 个已登录账号。"
        )
    return {
        "code": 200,
        "msg": message,
        "status": snapshot,
    }


@router.post("/account-nurture/stop")
async def douyin_stop_account_nurture(request: Optional[dict] = None):
    global douyin_account_nurture_scheduler

    target_account_ids = parse_douyin_nurture_account_ids(request)

    if not reconcile_douyin_account_nurture_runtime_state() or not douyin_account_nurture_scheduler:
        return {"code": 200, "msg": "当前没有正在运行的抖音养号任务。"}

    if target_account_ids:
        disabled_ids = douyin_account_nurture_scheduler.disable_accounts(target_account_ids)
        snapshot = douyin_account_nurture_scheduler.snapshot()
        if not disabled_ids:
            return {"code": 200, "msg": "指定账号当前没有参与养号。", "status": snapshot}

        account_text = "、".join(str(account_id) for account_id in disabled_ids)
        if douyin_account_nurture_scheduler.has_enabled_online_accounts():
            douyin_log(f"抖音养号已暂停账号：{account_text}。", "warning")
            return {
                "code": 200,
                "msg": f"已暂停账号 {account_text} 的养号，其他账号继续运行。",
                "status": snapshot,
            }

        douyin_account_nurture_scheduler.stop()
        douyin_log(f"抖音养号最后一个账号已暂停：{account_text}。", "warning")
        return {
            "code": 200,
            "msg": f"已暂停账号 {account_text}，当前没有账号继续养号，本轮任务已全部停止。",
            "status": douyin_account_nurture_scheduler.snapshot(),
        }

    douyin_account_nurture_scheduler.stop()
    douyin_log("抖音养号已收到暂停指令。", "warning")
    return {"code": 200, "msg": "抖音养号已暂停。", "status": douyin_account_nurture_scheduler.snapshot()}


@router.post("/account/{account_id}/login")
async def douyin_login_account(account_id: int):
    try:
        config = load_global_config()
        accounts = _normalize_accounts(config.get("douyin_accounts"))
        account = next((item for item in accounts if item["id"] == account_id), None)
        if not account:
            raise HTTPException(status_code=400, detail="Invalid Douyin account ID")

        client = DouyinClient(account["port"], account_id=account["id"])
        profile_dir = client.resolve_profile_dir()
        douyin_log(f"[抖音账号] 准备启动账号 {account_id} 浏览器，端口 {account['port']}，profile: {profile_dir}")
        success = await asyncio.to_thread(client.launch_browser)
        if not success:
            msg = f"账号 {account_id} 浏览器启动失败，请确认本机已安装 Google Chrome，且端口 {account['port']} 未被占用。profile: {profile_dir}"
            douyin_log(f"[抖音账号] {msg}", "error")
            return {"code": 500, "msg": msg}

        is_logged_in = await detect_douyin_account_login_state(account)
        account["status"] = "online" if is_logged_in else "waiting"
        config["douyin_accounts"] = accounts
        save_global_config(config)
        if is_logged_in:
            return {"code": 200, "status": "online", "msg": "Douyin account is already online"}
        return {"code": 200, "status": "waiting", "msg": "Browser launched, please finish login in the Douyin window"}
    except HTTPException:
        raise
    except Exception as exc:
        detail = traceback.format_exc()
        msg = f"账号 {account_id} 登录浏览器启动异常：{exc}"
        douyin_log(f"[抖音账号] {msg}\n{detail}", "error")
        return {"code": 500, "msg": msg, "detail": detail}


@router.post("/account/{account_id}/logout")
async def douyin_logout_account(account_id: int):
    config = load_global_config()
    accounts = _normalize_accounts(config.get("douyin_accounts"))
    account = next((item for item in accounts if item["id"] == account_id), None)
    if not account:
        raise HTTPException(status_code=400, detail="Invalid Douyin account ID")

    client = DouyinClient(account["port"], account_id=account["id"])
    await asyncio.to_thread(client.close_browser)

    account["status"] = "offline"
    config["douyin_accounts"] = accounts
    save_global_config(config)
    return {"code": 200, "msg": "Douyin account logged out"}


@router.post("/account/{account_id}/check")
async def douyin_check_account(account_id: int):
    config = load_global_config()
    accounts = _normalize_accounts(config.get("douyin_accounts"))
    account = next((item for item in accounts if item["id"] == account_id), None)
    if not account:
        raise HTTPException(status_code=400, detail="Invalid Douyin account ID")

    is_logged_in = await detect_douyin_account_login_state(account)
    account["status"] = "online" if is_logged_in else "waiting"
    config["douyin_accounts"] = accounts
    save_global_config(config)
    return {"code": 200, "status": account["status"]}


@router.post("/account/{account_id}/view")
async def douyin_view_account(account_id: int):
    try:
        config = load_global_config()
        accounts = _normalize_accounts(config.get("douyin_accounts"))
        account = next((item for item in accounts if item["id"] == account_id), None)
        if not account:
            raise HTTPException(status_code=400, detail="Invalid Douyin account ID")
        return await open_douyin_account_homepage(account)
    except HTTPException:
        raise
    except Exception as exc:
        detail = traceback.format_exc()
        msg = f"账号 {account_id} 打开抖音主页异常：{exc}"
        douyin_log(f"[抖音账号] {msg}\n{detail}", "error")
        return {"code": 500, "msg": msg, "detail": detail}


@router.post("/search/collect")
async def douyin_search_collect(request: dict):
    nurture_conflict = build_douyin_nurture_conflict("??????")
    if nurture_conflict:
        return nurture_conflict
    request = request or {}
    keyword = str(request.get("keyword", "") or "").strip()
    if not keyword:
        return {"code": 400, "msg": "??????????"}
    search_mode = normalize_douyin_search_mode(request.get("mode", "api"))
    max_results = max(10, min(int(request.get("max_results", 50) or 50), 100))
    if search_mode == "api":
        try:
            payload = await run_douyin_keyword_search_via_api(
                keyword,
                max_results=max_results,
                account_id="api",
            )
        except Exception as exc:
            douyin_log(f"[抖音搜索] 接口模式失败，准备回退脚本模式：{keyword}，原因：{exc}", "warning")
            search_mode = "script"
        else:
            return {
                "code": 200,
                "msg": "抖音搜索已通过接口模式完成",
                "data": payload["results"],
                "total": len(payload["results"]),
                "account_id": "api",
                "search_mode": "api",
                "request_id": payload.get("request_id", ""),
                "cache_url": payload.get("cache_url", ""),
            }
    config = load_global_config()
    account = get_active_douyin_account(config)
    if not account:
        return {
            "code": 400,
            "type": "no_online_account",
            "msg": "No online Douyin account is available. Please sign in first.",
            "data": [],
            "total": 0,
        }
    try:
        payload = await run_douyin_keyword_search(
            account,
            keyword,
            max_results=max_results,
            update_latest=True,
        )
    except Exception as exc:
        douyin_log(f"[????] ?????{keyword}????{exc}", "error")
        return {"code": 500, "msg": f"??????: {exc}", "data": [], "total": 0, "search_mode": "script"}
    return {
        "code": 200,
        "msg": f"??????????? {account['id']}",
        "data": payload["results"],
        "total": len(payload["results"]),
        "account_id": account["id"],
        "search_mode": "script",
    }


@router.post("/search/export")
async def douyin_search_export(request: dict):
    data = request.get("data", []) if isinstance(request, dict) else []
    if not data:
        return {"code": 400, "msg": "没有可导出的搜索结果"}
    try:
        file = export_search_rows(data)
        return {"code": 200, "msg": "导出成功", "file": file}
    except Exception as exc:
        return {"code": 500, "msg": f"导出失败: {exc}"}


@router.get("/search/sessions")
async def douyin_get_search_sessions():
    return {
        "code": 200,
        "saved_at": load_douyin_search_sessions_saved_at(),
        "sessions": load_douyin_search_sessions_state(),
    }


@router.post("/search/sessions")
async def douyin_save_search_sessions(request: Optional[dict] = None):
    payload = request if isinstance(request, dict) else {}
    sessions = payload.get("sessions", [])
    incoming_saved_at = int(payload.get("saved_at", 0) or 0)
    if not isinstance(sessions, list):
        return {"code": 400, "msg": "sessions 必须是数组。"}
    current_saved_at = load_douyin_search_sessions_saved_at()
    if incoming_saved_at and current_saved_at and incoming_saved_at < current_saved_at:
        return {
            "code": 200,
            "ignored": True,
            "msg": "已忽略过期的关键词卡片快照。",
            "count": len(load_douyin_search_sessions_state()),
            "saved_at": current_saved_at,
        }
    normalized_sessions = [dict(item) for item in sessions if isinstance(item, dict)]
    save_douyin_search_sessions_state(normalized_sessions, saved_at=incoming_saved_at)
    return {
        "code": 200,
        "msg": "搜索关键词卡片已保存到本地数据库。",
        "count": len(normalized_sessions),
        "saved_at": int(incoming_saved_at or load_douyin_search_sessions_saved_at() or 0),
    }


@router.post("/tasks/from-search")
async def douyin_tasks_from_search(request: dict):
    selected = request.get("data", []) if isinstance(request, dict) else []
    if not isinstance(selected, list) or not selected:
        return {"code": 400, "msg": "Please select at least one search result"}

    rows = []
    for item in selected:
        url = str(item.get("url", "")).strip()
        if not url:
            continue
        rows.append(
            {
                "url": url,
                "title": str(item.get("title", "")).strip(),
                "author": str(item.get("author", "")).strip(),
                "cover_image": str(item.get("cover_image", "")).strip(),
                "source_session_id": str(item.get("source_session_id", "")).strip(),
                "source_item_key": str(item.get("source_item_key", "")).strip(),
                "comments": int(item.get("comments", 0) or 0),
                "comments_text": str(item.get("comments_text", "") or "").strip(),
                "likes": int(item.get("likes", 0) or 0),
                "publish_time": str(item.get("publish_time", "")).strip(),
            }
        )

    if not rows:
        return {"code": 400, "msg": "No valid video URL was found in the selected search results"}

    tasks = set_tasks_from_rows(rows)
    selected_count = len(rows)
    douyin_log(f"[抖音任务] 已同步搜索勾选 {selected_count} 条到任务池，当前任务池累计 {len(tasks)} 条", "success")
    return {
        "code": 200,
        "msg": f"已同步 {selected_count} 条搜索视频到任务池，当前累计 {len(tasks)} 条抖音任务",
        "selected_total": selected_count,
        "total": len(tasks),
    }


@router.get("/tasks")
async def douyin_get_tasks():
    global douyin_tasks
    await ensure_douyin_schedule_scheduler()
    reconcile_douyin_runtime_state()
    normalized_tasks = [ensure_douyin_task_shape(task if isinstance(task, dict) else {}) for task in douyin_tasks]
    if normalized_tasks != douyin_tasks:
        douyin_tasks = normalized_tasks
        save_douyin_tasks_state()
    if backfill_task_cover_images(douyin_tasks):
        save_douyin_tasks_state()
    return {
        "code": 200,
        "tasks": douyin_tasks,
        "total": len(douyin_tasks),
        "running": douyin_running,
        "completed": sum(1 for task in douyin_tasks if task.get("status") == "completed"),
    }


@router.get("/tasks-lite")
async def douyin_get_tasks_lite():
    global douyin_tasks
    await ensure_douyin_schedule_scheduler()
    reconcile_douyin_runtime_state()
    normalized_tasks = [ensure_douyin_task_shape(task if isinstance(task, dict) else {}) for task in douyin_tasks]
    if normalized_tasks != douyin_tasks:
        douyin_tasks = normalized_tasks
        save_douyin_tasks_state()
    if backfill_task_cover_images(douyin_tasks):
        save_douyin_tasks_state()
    lite_tasks = [build_douyin_task_lite_payload(task) for task in douyin_tasks if isinstance(task, dict)]
    high_intent_total = sum(int(task.get("high_intent_count", 0) or 0) for task in lite_tasks)
    return {
        "code": 200,
        "tasks": lite_tasks,
        "total": len(lite_tasks),
        "running": douyin_running,
        "completed": sum(1 for task in douyin_tasks if task.get("status") == "completed"),
        "high_intent_users": high_intent_total,
    }


@router.get("/customer-pools")
async def douyin_get_customer_pools():
    all_rows, precise_rows = build_combined_douyin_customer_pools()
    sync_douyin_customer_pool_cache(all_rows, precise_rows)
    return {
        "code": 200,
        "all_customers": douyin_all_customer_pool,
        "precise_customers": douyin_precise_customer_pool,
        "all_total": len(douyin_all_customer_pool),
        "precise_total": len(douyin_precise_customer_pool),
    }


@router.post("/customers/delete")
async def douyin_delete_customers(request: Optional[dict] = None):
    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()

    if (
        douyin_running
        or douyin_video_comment_running
        or douyin_mention_comment_running
        or douyin_follow_comment_running
        or douyin_interaction_running
        or douyin_group_member_running
        or douyin_stranger_message_running
    ):
        return {"code": 400, "msg": "当前有任务正在执行，暂时不能删除客户。"}

    payload = request if isinstance(request, dict) else {}
    raw_rows = payload.get("rows", [])
    if not isinstance(raw_rows, list) or not raw_rows:
        return {"code": 400, "msg": "请先勾选至少一个客户。"}

    result = delete_douyin_customers_from_tasks(raw_rows)
    removed = int(result.get("removed", 0) or 0)
    skipped_monitor = int(result.get("skipped_monitor", 0) or 0)

    if removed <= 0:
        if skipped_monitor > 0:
            return {
                "code": 400,
                "msg": "当前勾选的客户都来自同行监控，暂不支持在这里删除，请回到监控链路处理。",
                "skipped_monitor": skipped_monitor,
            }
        return {"code": 404, "msg": "未找到匹配的客户。"}

    all_rows, precise_rows = build_combined_douyin_customer_pools()
    sync_douyin_customer_pool_cache(all_rows, precise_rows)
    msg = f"已删除 {removed} 位客户。"
    if skipped_monitor > 0:
        msg += f" 另有 {skipped_monitor} 位同行监控客户未处理。"
    douyin_log(f"[抖音客户池] 已删除 {removed} 位客户", "warning")
    return {
        "code": 200,
        "msg": msg,
        "removed": removed,
        "removed_precise": int(result.get("removed_precise", 0) or 0),
        "skipped_monitor": skipped_monitor,
        "all_total": len(all_rows),
        "precise_total": len(precise_rows),
    }


@router.post("/customers/add-precise")
async def douyin_add_customers_to_precise(request: Optional[dict] = None):
    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()

    if (
        douyin_running
        or douyin_video_comment_running
        or douyin_mention_comment_running
        or douyin_follow_comment_running
        or douyin_interaction_running
        or douyin_group_member_running
        or douyin_stranger_message_running
    ):
        return {"code": 400, "msg": "当前有任务正在执行，暂时不能修改精准客户池。"}

    payload = request if isinstance(request, dict) else {}
    raw_rows = payload.get("rows", [])
    if not isinstance(raw_rows, list) or not raw_rows:
        return {"code": 400, "msg": "请先勾选至少一个客户。"}

    candidate_rows = [row for row in raw_rows if isinstance(row, dict) and not bool(row.get("is_high_intent"))]
    if not candidate_rows:
        return {"code": 400, "msg": "所选客户已在精准客户池中。"}

    result = add_douyin_customers_to_precise_pool(candidate_rows)
    added = int(result.get("added", 0) or 0)
    if added <= 0:
        return {"code": 404, "msg": "未找到可加入精准客户池的客户。", **result}

    all_rows, precise_rows = build_combined_douyin_customer_pools()
    sync_douyin_customer_pool_cache(all_rows, precise_rows)
    douyin_log(
        f"[抖音客户池] 已从全部客户页加入精准客户 {added} 位，其中同行监控 {int(result.get('added_monitor', 0) or 0)} 位",
        "success",
    )
    return {
        "code": 200,
        "msg": f"已加入精准客户池 {added} 位。",
        **result,
        "all_total": len(all_rows),
        "precise_total": len(precise_rows),
    }


@router.get("/scheduler/overview")
async def douyin_scheduler_overview():
    await ensure_douyin_schedule_scheduler()
    plans = [normalize_douyin_schedule_plan(plan) for plan in douyin_schedule_plans if isinstance(plan, dict)]
    plans.sort(
        key=lambda item: (
            not bool(item.get("enabled", True)),
            parse_schedule_datetime(item.get("next_run_at")) or datetime.max,
            parse_schedule_datetime(item.get("updated_at")) or datetime.min,
        ),
        reverse=False,
    )
    return {
        "code": 200,
        "plans": plans,
        "state": douyin_schedule_runtime_state,
        "busy_reason": get_douyin_schedule_busy_reason(),
    }


@router.post("/scheduler/plans")
async def douyin_save_scheduler_plan(request: Optional[dict] = None):
    payload = request if isinstance(request, dict) else {}
    raw_plan = payload.get("plan", payload if isinstance(payload, dict) else {})
    if not isinstance(raw_plan, dict):
        return {"code": 400, "msg": "计划配置格式不正确。"}
    plan = normalize_douyin_schedule_plan(raw_plan)
    if plan["type"] == "collect_precise" and not str(plan.get("keyword", "") or "").strip():
        return {"code": 400, "msg": "采集精准客户计划必须填写关键词。"}

    plan["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing_index = next(
        (index for index, item in enumerate(douyin_schedule_plans) if str(item.get("id", "") or "") == str(plan["id"])),
        -1,
    )
    if existing_index >= 0:
        previous = normalize_douyin_schedule_plan(douyin_schedule_plans[existing_index])
        for field in ("last_run_at", "last_status", "last_message", "last_error", "total_runs", "success_runs", "failure_runs", "next_run_at"):
            if field not in raw_plan:
                plan[field] = previous.get(field)
        douyin_schedule_plans[existing_index] = plan
    else:
        douyin_schedule_plans.append(plan)
    if plan.get("enabled", True):
        next_run = get_next_schedule_window_anchor(
            datetime.now(),
            plan.get("window_start"),
            plan.get("window_end"),
            include_current_if_inside=True,
        )
        plan["next_run_at"] = format_schedule_datetime(next_run)
        if existing_index >= 0:
            douyin_schedule_plans[existing_index] = plan
        else:
            douyin_schedule_plans[-1] = plan
    save_douyin_schedule_plans()
    await ensure_douyin_schedule_scheduler()
    return {"code": 200, "msg": "排期计划已保存。", "plan": plan, "plans": douyin_schedule_plans}


@router.post("/scheduler/plans/{plan_id}/toggle")
async def douyin_toggle_scheduler_plan(plan_id: str, request: Optional[dict] = None):
    payload = request if isinstance(request, dict) else {}
    enabled = coerce_bool(payload.get("enabled", True), True)
    for index, item in enumerate(douyin_schedule_plans):
        plan = normalize_douyin_schedule_plan(item)
        if str(plan.get("id", "") or "") != str(plan_id or ""):
            continue
        plan["enabled"] = enabled
        if enabled and not str(plan.get("next_run_at", "") or "").strip():
            plan["next_run_at"] = format_schedule_datetime(
                get_next_schedule_window_anchor(
                    datetime.now(),
                    plan.get("window_start"),
                    plan.get("window_end"),
                    include_current_if_inside=True,
                )
            )
        plan["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        douyin_schedule_plans[index] = plan
        save_douyin_schedule_plans()
        await ensure_douyin_schedule_scheduler()
        return {"code": 200, "msg": f"计划已{'启用' if enabled else '暂停'}。", "plan": plan}
    return {"code": 404, "msg": "未找到对应计划。"}


@router.post("/scheduler/plans/{plan_id}/run")
async def douyin_run_scheduler_plan_now(plan_id: str):
    await ensure_douyin_schedule_scheduler()
    plan = next(
        (normalize_douyin_schedule_plan(item) for item in douyin_schedule_plans if str(item.get("id", "") or "") == str(plan_id or "")),
        None,
    )
    if not plan:
        return {"code": 404, "msg": "未找到对应计划。"}
    busy_reason = get_douyin_schedule_busy_reason()
    if busy_reason:
        return {"code": 400, "msg": f"当前不能立即执行：{busy_reason}。"}
    douyin_schedule_runtime_state["active_plan_id"] = str(plan.get("id", "") or "")
    douyin_schedule_runtime_state["active_plan_name"] = str(plan.get("name", "") or "")
    douyin_schedule_runtime_state["active_phase"] = "launching"
    result = await execute_douyin_schedule_plan(plan)
    success = int(result.get("code", 0) or 0) == 200
    update_douyin_schedule_plan_runtime(
        str(plan.get("id", "") or ""),
        status="success" if success else "failed",
        message=str(result.get("msg", "") or ""),
        error="" if success else str(result.get("msg", "") or ""),
        bump_total=True,
        success=success,
        failed=not success,
        next_run_at=get_next_douyin_schedule_run(
            datetime.now(),
            int(plan.get("interval_minutes", 120) or 120),
            window_start=plan.get("window_start"),
            window_end=plan.get("window_end"),
        ),
    )
    douyin_schedule_runtime_state["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    douyin_schedule_runtime_state["message"] = str(result.get("msg", "") or "")
    douyin_schedule_runtime_state["last_error"] = "" if success else str(result.get("msg", "") or "")
    douyin_schedule_runtime_state["active_phase"] = "waiting" if success else "idle"
    return result


@router.post("/scheduler/plans/{plan_id}/delete")
async def douyin_delete_scheduler_plan(plan_id: str):
    before_total = len(douyin_schedule_plans)
    douyin_schedule_plans[:] = [
        item for item in douyin_schedule_plans if str(item.get("id", "") or "") != str(plan_id or "")
    ]
    if len(douyin_schedule_plans) == before_total:
        return {"code": 404, "msg": "未找到对应计划。"}
    save_douyin_schedule_plans()
    if str(douyin_schedule_runtime_state.get("active_plan_id", "") or "") == str(plan_id or ""):
        douyin_schedule_runtime_state["active_plan_id"] = ""
        douyin_schedule_runtime_state["active_plan_name"] = ""
        douyin_schedule_runtime_state["active_phase"] = ""
    return {"code": 200, "msg": "排期计划已删除。", "plans": douyin_schedule_plans}


@router.post("/tasks/{task_id}/high-intent-users")
async def douyin_update_task_high_intent_users(task_id: int, request: dict):
    selected_users = request.get("users", [])
    if not isinstance(selected_users, list):
        return {"code": 400, "msg": "users must be a list"}

    task = next((item for item in douyin_tasks if int(item.get("id", 0) or 0) == int(task_id)), None)
    if not task:
        return {"code": 404, "msg": "Task not found"}

    valid_pool = {}
    for row in task.get("all_comments", []) or []:
        normalized = normalize_high_intent_user(row)
        valid_pool[user_choice_key(normalized)] = normalized

    for row in task.get("high_intent_users", []) or []:
        normalized = normalize_high_intent_user(row)
        valid_pool[user_choice_key(normalized)] = normalized

    normalized_rows = []
    for row in selected_users:
        key = user_choice_key(row)
        if key in valid_pool:
            normalized_rows.append(valid_pool[key])

    task["high_intent_users"] = dedupe_users(normalized_rows)
    save_douyin_tasks_state()
    return {"code": 200, "msg": "High-intent users updated", "count": len(task["high_intent_users"])}


@router.post("/tasks/{task_id}/refilter-customers")
async def douyin_refilter_task_customers(
    task_id: int,
    http_request: Request = None,
    request: Optional[dict] = None,
):
    set_douyin_ai_auth_token_from_request(http_request)
    payload = request if isinstance(request, dict) else {}
    task = next((item for item in douyin_tasks if int(item.get("id", 0) or 0) == int(task_id)), None)
    if not task:
        return {"code": 404, "msg": "未找到对应的视频任务。"}

    comments = task.get("all_comments", []) or []
    if not comments:
        return {"code": 400, "msg": "这条视频还没有已采集的评论，暂时不能筛选客户。"}

    prompt_text = str(payload.get("prompt", "") or "").strip()
    config = load_global_config()
    prompt_to_use = prompt_text or get_douyin_comment_direction(config)
    strategy_to_use = normalize_douyin_comment_filter_strategy(
        payload.get("strategy") or get_douyin_comment_filter_strategy(config)
    )
    ai_client = create_ai_client()

    _filter_started_at = time.time()
    log_douyin_filter_event(
        "start",
        scope="task_refilter",
        task_id=int(task.get("id", 0) or 0),
        title=task.get("title", ""),
        comments_in=len(comments),
        strategy=strategy_to_use,
        prompt=prompt_to_use,
    )
    try:
        precise_users = await asyncio.to_thread(
            lambda: ai_client.filter_comments(
                str(task.get("title", "") or task.get("url", "") or ""),
                comments,
                prompt_to_use,
                "douyin_transactional",
                "",
                strategy_to_use,
                event_logger=log_douyin_filter_event,
            )
        )
    except Exception as exc:
        log_douyin_filter_event(
            "error",
            scope="task_refilter",
            task_id=int(task.get("id", 0) or 0),
            title=task.get("title", ""),
            comments_in=len(comments),
            strategy=strategy_to_use,
            duration_ms=int((time.time() - _filter_started_at) * 1000),
            error=str(exc),
        )
        return {"code": 500, "msg": f"重新筛选失败：{exc}"}

    log_douyin_filter_event(
        "done",
        scope="task_refilter",
        task_id=int(task.get("id", 0) or 0),
        title=task.get("title", ""),
        comments_in=len(comments),
        precise_out=len(precise_users or []),
        strategy=strategy_to_use,
        duration_ms=int((time.time() - _filter_started_at) * 1000),
    )

    valid_pool = {}
    for row in comments:
        normalized = normalize_high_intent_user(row)
        valid_pool[user_choice_key(normalized)] = normalized

    for row in task.get("high_intent_users", []) or []:
        normalized = normalize_high_intent_user(row)
        valid_pool[user_choice_key(normalized)] = normalized

    normalized_rows = []
    for row in precise_users or []:
        normalized = normalize_high_intent_user(row)
        key = user_choice_key(normalized)
        normalized_rows.append(valid_pool.get(key, normalized))

    task["high_intent_users"] = dedupe_users(normalized_rows)
    save_douyin_tasks_state()
    douyin_log(
        f"[抖音搜索采集] 已重新筛选视频《{task.get('title') or task.get('url') or task_id}》，评论 {len(comments)} 条，精准客户 {len(task['high_intent_users'])} 人",
        "success",
    )
    return {
        "code": 200,
        "msg": f"已完成重新筛选：评论 {len(comments)} 条，精准客户 {len(task['high_intent_users'])} 人。",
        "count": len(task["high_intent_users"]),
    }


@router.get("/logs")
async def douyin_get_logs(since_id: int = 0):
    return {
        "code": 200,
        "logs": [item for item in list(douyin_logs) if int(item.get("id", 0) or 0) > int(since_id or 0)],
        "latest_id": douyin_log_counter,
    }


@router.post("/page-log")
async def douyin_page_log(payload: Dict):
    message = str((payload or {}).get("message", "") or "").strip()
    level = str((payload or {}).get("level", "info") or "info").strip().lower()
    source = str((payload or {}).get("source", "") or "").strip()
    if not message:
        return {"code": 200}
    if level not in {"info", "success", "warning", "error"}:
        level = "info"
    prefix = f"[抖音页面日志]{f'[{source}]' if source else ''}"
    douyin_log(f"{prefix} {message}"[:1000], level)
    return {"code": 200}


@router.get("/group-members/status")
async def douyin_group_members_status():
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    return {
        "code": 200,
        "running": bool(douyin_group_member_state.get("running")),
        "state": douyin_group_member_state,
        "results": douyin_group_member_results,
    }


@router.post("/group-members/preview")
async def douyin_preview_group_members(request: Optional[dict] = None):
    nurture_conflict = build_douyin_nurture_conflict("识别群聊")
    if nurture_conflict:
        return nurture_conflict
    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()

    if douyin_running or douyin_video_comment_running or douyin_mention_comment_running or douyin_follow_comment_running or douyin_interaction_running or douyin_group_member_running or douyin_stranger_message_running:
        return {"code": 400, "msg": "当前已有任务在执行，请先停止后再识别群聊。"}

    payload = request if isinstance(request, dict) else {}
    group_keyword = str(payload.get("group_keyword", "") or "").strip()

    account = get_active_douyin_account(load_global_config())
    if not account:
        return {
            "code": 400,
            "type": "no_online_account",
            "msg": "当前没有可用的抖音在线账号，请先登录。",
            "groups": [],
        }

    scraper = DouyinCommentScraper(account_id=account["id"], cdp_port=account["port"])
    try:
        groups = await scraper.list_chat_groups(group_keyword=group_keyword, logger=douyin_log)
    finally:
        await scraper.close()

    douyin_group_member_state["available_groups"] = groups
    douyin_group_member_state["group_keyword"] = group_keyword
    return {
        "code": 200,
        "msg": f"已识别 {len(groups)} 个群聊",
        "groups": groups,
        "account_id": account["id"],
    }


@router.post("/group-members/start")
async def douyin_start_group_members(request: Optional[dict] = None):
    nurture_conflict = build_douyin_nurture_conflict("提取群成员")
    if nurture_conflict:
        return nurture_conflict
    global douyin_group_member_running, douyin_group_member_stop_requested, douyin_group_member_background_task

    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()

    if douyin_running:
        return {"code": 400, "msg": "评论采集任务正在执行，请先停止后再开始群成员提取。"}
    if douyin_video_comment_running:
        return {"code": 400, "msg": "视频评论任务正在执行，请先停止后再开始群成员提取。"}
    if douyin_mention_comment_running:
        return {"code": 400, "msg": "评论@精准客户任务正在执行，请先停止后再开始群成员提取。"}
    if douyin_follow_comment_running:
        return {"code": 400, "msg": "关注评论任务正在执行，请先停止后再开始群成员提取。"}
    if douyin_interaction_running:
        return {"code": 400, "msg": "私信任务正在执行，请先停止后再开始群成员提取。"}
    if douyin_stranger_message_running:
        return {"code": 400, "msg": "私信引流任务正在执行，请先停止后再开始群成员提取。"}
    if douyin_group_member_running:
        return {"code": 400, "msg": "群成员提取任务已在执行中。"}

    payload = request if isinstance(request, dict) else {}
    group_keyword = str(payload.get("group_keyword", "") or "").strip()
    max_groups = max(1, min(int(payload.get("max_groups", 5) or 5), 50))
    max_members_per_group = max(1, min(int(payload.get("max_members_per_group", 50) or 50), 500))
    selected_groups = [
        str(item or "").strip()
        for item in (payload.get("selected_groups", []) if isinstance(payload.get("selected_groups", []), list) else [])
        if str(item or "").strip()
    ]
    if selected_groups:
        max_groups = max(len(selected_groups), max_groups)

    account = get_active_douyin_account(load_global_config())
    if not account:
        return {
            "code": 400,
            "type": "no_online_account",
            "msg": "当前没有可用的抖音在线账号，请先登录。",
        }

    douyin_group_member_running = True
    douyin_group_member_stop_requested = False
    douyin_group_member_background_task = asyncio.create_task(
        run_douyin_group_member_collection(
            account=account,
            group_keyword=group_keyword,
            max_groups=max_groups,
            max_members_per_group=max_members_per_group,
            selected_groups=selected_groups,
        )
    )
    douyin_group_member_state["selected_groups"] = selected_groups
    douyin_log(
        f"[抖音群成员] 已启动，账号 {account['id']}，群关键词：{group_keyword or '自动识别群聊'}"
        + (f"，指定群 {len(selected_groups)} 个" if selected_groups else ""),
        "success",
    )
    return {
        "code": 200,
        "msg": f"群成员提取任务已启动，使用账号 {account['id']}",
        "account_id": account["id"],
        "group_keyword": group_keyword,
        "max_groups": max_groups,
        "max_members_per_group": max_members_per_group,
        "selected_groups": selected_groups,
    }


@router.post("/group-members/stop")
async def douyin_stop_group_members():
    global douyin_group_member_stop_requested, douyin_group_member_background_task

    reconcile_douyin_group_member_runtime_state()
    if not douyin_group_member_running:
        douyin_group_member_stop_requested = False
        douyin_group_member_background_task = None
        return {"code": 200, "msg": "当前没有正在执行的群成员提取任务。"}

    douyin_group_member_stop_requested = True
    douyin_log("[抖音群成员] 已请求停止", "warning")
    return {"code": 200, "msg": "已请求停止群成员提取任务。"}


@router.post("/group-members/export")
async def douyin_export_group_members():
    try:
        file = export_group_member_results()
        return {"code": 200, "msg": "群成员结果已导出", "file": file}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"导出失败: {exc}")


@router.post("/group-members/add-to-interaction")
async def douyin_add_group_members_to_interaction(request: Optional[dict] = None):
    payload = request if isinstance(request, dict) else {}
    raw_rows = payload.get("rows", [])
    if not isinstance(raw_rows, list) or not raw_rows:
        return {"code": 400, "msg": "请先勾选至少一个群成员。"}

    imported = import_group_members_to_interaction_pool(raw_rows)
    removed = remove_group_members_from_results(raw_rows)
    total = len(collect_douyin_interaction_users())
    return {
        "code": 200,
        "msg": (
            f"已加入私信列表 {imported} 人，并从当前群成员列表移除 {removed} 人"
            if imported > 0
            else (
                f"本次没有新增成员，但已从当前群成员列表移除 {removed} 人"
                if removed > 0
                else "本次没有新增成员，可能已在私信列表中"
            )
        ),
        "imported": imported,
        "removed": removed,
        "total": total,
    }


@router.post("/group-members/delete")
async def douyin_delete_group_members(request: Optional[dict] = None):
    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()

    if douyin_running or douyin_video_comment_running or douyin_mention_comment_running or douyin_follow_comment_running or douyin_interaction_running or douyin_group_member_running or douyin_stranger_message_running:
        return {"code": 400, "msg": "当前有任务正在执行，暂时不能删除群成员结果。"}

    payload = request if isinstance(request, dict) else {}
    raw_rows = payload.get("rows", [])
    if not isinstance(raw_rows, list) or not raw_rows:
        return {"code": 400, "msg": "请先勾选至少一个群成员。"}

    removed = remove_group_members_from_results(raw_rows)
    if removed <= 0:
        return {"code": 404, "msg": "未找到匹配的群成员结果。"}

    douyin_log(f"[抖音群成员] 已删除 {removed} 条群成员结果", "warning")
    return {
        "code": 200,
        "msg": f"已删除 {removed} 条群成员结果。",
        "removed": removed,
        "total": len(douyin_group_member_results),
    }


@router.get("/mention-comment/status")
async def douyin_mention_comment_status():
    reconcile_douyin_mention_comment_runtime_state()
    return {
        "code": 200,
        "running": bool(douyin_mention_comment_state.get("running")),
        "state": douyin_mention_comment_state,
        "video_cache": douyin_mention_self_video_cache,
        "history_total": len(douyin_mention_comment_history),
    }


@router.get("/mention-comment/videos")
async def douyin_get_self_videos(account_id: int = 0, max_videos: int = 12):
    nurture_conflict = build_douyin_nurture_conflict("采集自己的视频列表")
    if nurture_conflict:
        return nurture_conflict

    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()

    if douyin_running or douyin_video_comment_running or douyin_mention_comment_running or douyin_follow_comment_running or douyin_interaction_running or douyin_group_member_running:
        return {"code": 409, "msg": "当前有抖音任务正在执行，请先停止后再刷新自己的视频列表。"}

    config = load_global_config()
    accounts = _normalize_accounts(config.get("douyin_accounts"))
    target_account_id = int(account_id or 0)
    account = next(
        (row for row in accounts if int(row.get("id", 0) or 0) == target_account_id),
        None,
    ) if target_account_id > 0 else get_active_douyin_account(config)

    if not account:
        return {
            "code": 400,
            "type": "no_online_account",
            "msg": "当前没有可用的抖音在线账号，请先登录后再采集自己的视频。",
        }

    client = DouyinClient(account["port"], account_id=account["id"])
    if not await asyncio.to_thread(client.launch_browser, "https://www.douyin.com/user/self?from_tab_name=main"):
        return {"code": 500, "msg": f"账号 {account['id']} 浏览器启动失败，无法采集自己的视频列表。"}

    is_logged_in = await detect_douyin_account_login_state(account)
    account["status"] = "online" if is_logged_in else "waiting"
    config["douyin_accounts"] = accounts
    save_global_config(config)

    if not is_logged_in:
        return {
            "code": 400,
            "type": "account_waiting_login",
            "msg": f"账号 {account['id']} 浏览器已打开，但尚未完成登录，请先扫码登录后再刷新。",
            "account": account,
            "videos": [],
            "profile": {},
        }

    scraper = DouyinCommentScraper(account_id=account["id"], cdp_port=account["port"])
    try:
        result = await scraper.scrape_self_videos(
            max_videos=max_videos,
            logger=douyin_log,
        )
    finally:
        await scraper.close()

    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    douyin_mention_self_video_cache.update(
        {
            "account_id": int(account["id"]),
            "profile": result.get("profile", {}) if isinstance(result.get("profile"), dict) else {},
            "videos": [row for row in (result.get("videos", []) or []) if isinstance(row, dict)],
            "fetched_at": fetched_at,
            "selected_video_url": str(
                douyin_mention_self_video_cache.get("selected_video_url", "") or ""
            ).strip(),
        }
    )
    save_douyin_mention_self_video_cache()

    return {
        "code": 200,
        "msg": f"已读取账号 {account['id']} 的个人主页作品列表。",
        "account": account,
        "profile": result.get("profile", {}),
        "videos": result.get("videos", []),
        "fetched_at": fetched_at,
    }


@router.post("/mention-comment/start")
async def douyin_start_mention_comment(request: Optional[dict] = None):
    nurture_conflict = build_douyin_nurture_conflict("执行评论@精准客户")
    if nurture_conflict:
        return nurture_conflict
    global douyin_mention_comment_running, douyin_mention_comment_stop_requested, douyin_mention_comment_background_task

    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()

    if douyin_running:
        return {"code": 400, "msg": "评论采集任务正在执行，请先停止后再开始评论@精准客户。"}
    if douyin_video_comment_running:
        return {"code": 400, "msg": "视频评论任务正在执行，请先停止后再开始评论@精准客户。"}
    if douyin_follow_comment_running:
        return {"code": 400, "msg": "关注评论任务正在执行，请先停止后再开始评论@精准客户。"}
    if douyin_interaction_running:
        return {"code": 400, "msg": "私信任务正在执行，请先停止后再开始评论@精准客户。"}
    if douyin_stranger_message_running:
        return {"code": 400, "msg": "私信引流任务正在执行，请先停止后再开始评论@精准客户。"}
    if douyin_group_member_running:
        return {"code": 400, "msg": "群成员提取任务正在执行，请先停止后再开始评论@精准客户。"}
    if douyin_mention_comment_running:
        return {"code": 400, "msg": "评论@精准客户任务已在执行中。"}

    payload = request if isinstance(request, dict) else {}
    raw_users = payload.get("users", [])
    video_url = str(payload.get("video_url", "") or "").strip()
    video_title = str(payload.get("video_title", "") or "").strip()
    video_cover_image = str(payload.get("video_cover_image", "") or "").strip()
    account_id = int(payload.get("account_id", 0) or 0)
    max_mentions = max(1, min(int(payload.get("max_mentions", DOUYIN_MENTION_COMMENT_MAX_USERS_PER_COMMENT) or DOUYIN_MENTION_COMMENT_MAX_USERS_PER_COMMENT), DOUYIN_MENTION_COMMENT_MAX_USERS_PER_COMMENT))

    if not video_url:
        return {"code": 400, "msg": "请先选择要执行的自己视频。"}
    if not isinstance(raw_users, list) or not raw_users:
        return {"code": 400, "msg": "请先勾选至少一个精准客户。"}

    selected_total = len([row for row in raw_users if isinstance(row, dict)])
    selected_users = normalize_douyin_mention_users(raw_users, max_mentions=selected_total or 1)
    if not selected_users:
        return {"code": 400, "msg": "当前勾选里没有可用的客户昵称，无法执行 @ 评论。"}
    available_users, skipped_history_users = split_already_mentioned_users(selected_users)
    if not available_users:
        return {
            "code": 400,
            "msg": "当前勾选客户都已经执行过 @ 评论，系统已自动拦截重复发送。",
            "skipped_existing": len(skipped_history_users),
        }
    truncated_count = max(0, selected_total - len(selected_users))
    mention_batches = split_douyin_mention_comment_batches(
        available_users,
        max_mentions_per_comment=max_mentions,
        max_comment_chars=DOUYIN_MENTION_COMMENT_SAFE_TEXT_LIMIT,
    )
    skipped_existing = len(skipped_history_users)

    config = load_global_config()
    accounts = _normalize_accounts(config.get("douyin_accounts"))
    account = next(
        (row for row in accounts if int(row.get("id", 0) or 0) == account_id and row.get("status") == "online"),
        None,
    ) if account_id > 0 else get_active_douyin_account(config)

    if not account:
        return {
            "code": 400,
            "type": "no_online_account",
            "msg": "当前没有可用的抖音在线账号，请先登录。"
        }

    comment_preview = build_douyin_mention_comment_preview(selected_users, limit=8)
    douyin_log(
        f"[抖音评论@客户] 准备启动，账号 {account['id']}，作品《{video_title or video_url}》，共 {len(available_users)} 人"
        + (f"，预计拆成 {len(mention_batches)} 条评论" if mention_batches else "")
        + (f"，已截断 {truncated_count} 人" if truncated_count else ""),
        "info",
    )
    if skipped_existing:
        douyin_log(
            f"[抖音评论@客户] 已跳过 {skipped_existing} 位历史执行过的客户，避免重复 @",
        "info",
    )

    douyin_mention_comment_running = True
    douyin_mention_comment_stop_requested = False
    douyin_mention_comment_background_task = asyncio.create_task(
        run_douyin_mention_comments(
            account=account,
            video_url=video_url,
            video_title=video_title,
            video_cover_image=video_cover_image,
            selected_users=available_users,
            selected_total=selected_total,
            truncated_count=truncated_count,
        )
    )
    douyin_mention_self_video_cache["selected_video_url"] = video_url
    save_douyin_mention_self_video_cache()
    return {
        "code": 200,
        "msg": f"评论@精准客户任务已启动，使用账号 {account['id']}，作品《{video_title or video_url}》，本次计划发送 {len(available_users)} 人"
        + (f"，自动拆成 {len(mention_batches)} 条评论" if mention_batches else "")
        + (f"，已截断 {truncated_count} 人" if truncated_count else "")
        + (f"，已跳过 {skipped_existing} 位历史执行客户" if skipped_existing else ""),
        "total": len(available_users),
        "selected_total": selected_total,
        "truncated": truncated_count,
        "skipped_existing": skipped_existing,
        "account_id": account["id"],
        "comment_preview": build_douyin_mention_comment_preview(available_users, limit=8),
    }


@router.post("/mention-comment/stop")
async def douyin_stop_mention_comment():
    global douyin_mention_comment_stop_requested, douyin_mention_comment_background_task

    reconcile_douyin_mention_comment_runtime_state()
    if not douyin_mention_comment_running:
        douyin_mention_comment_stop_requested = False
        douyin_mention_comment_background_task = None
        return {"code": 200, "msg": "当前没有正在执行的评论@精准客户任务。"}

    douyin_mention_comment_stop_requested = True
    douyin_log("[抖音评论@客户] 已请求停止", "warning")
    return {"code": 200, "msg": "已请求停止评论@精准客户任务。"}


@router.get("/video-comment/status")
async def douyin_video_comment_status():
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    return {
        "code": 200,
        "running": bool(douyin_video_comment_state.get("running")),
        "state": douyin_video_comment_state,
    }


@router.post("/video-comment/upload-image")
async def douyin_upload_video_comment_image(request: Request, file: Optional[UploadFile] = File(None)):
    upload = file
    filename = ""
    content = b""
    content_type = str(request.headers.get("content-type") or "").lower()

    if "application/json" in content_type:
        try:
            payload = await request.json()
        except Exception as exc:
            return {"code": 400, "msg": f"读取上传 JSON 失败：{exc}"}
        if not isinstance(payload, dict):
            return {"code": 400, "msg": "上传图片参数格式不正确。"}
        filename = str(payload.get("filename") or payload.get("name") or "").strip()
        encoded = str(
            payload.get("data_base64")
            or payload.get("base64")
            or payload.get("dataUrl")
            or payload.get("data_url")
            or ""
        ).strip()
        if "," in encoded and encoded.lower().startswith("data:"):
            encoded = encoded.split(",", 1)[1]
        if not encoded:
            return {"code": 400, "msg": "没有收到评论图片内容，请重新选择图片后再试。"}
        try:
            content = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            return {"code": 400, "msg": f"评论图片内容解析失败：{exc}"}

    if not content and upload is None:
        try:
            form = await request.form()
        except Exception as exc:
            return {"code": 400, "msg": f"读取上传表单失败：{exc}"}
        for value in form.values():
            if hasattr(value, "filename") and hasattr(value, "read"):
                upload = value
                break
    if not content and upload is None:
        return {"code": 400, "msg": "没有收到评论图片文件，请重新选择图片后再试。"}

    if not content and upload is not None:
        filename = str(upload.filename or "").strip()
        content = await upload.read()

    suffix = Path(filename).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return {"code": 400, "msg": "请上传 png、jpg、jpeg、webp 或 gif 图片"}

    max_bytes = 15 * 1024 * 1024
    if not content:
        return {"code": 400, "msg": "图片文件为空"}
    if len(content) > max_bytes:
        return {"code": 400, "msg": "图片不能超过 15MB"}

    DOUYIN_COMMENT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"video_comment_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{random.randint(1000, 9999)}{suffix}"
    target_path = (DOUYIN_COMMENT_IMAGE_DIR / safe_name).resolve()
    try:
        target_path.write_bytes(content)
    except Exception as exc:
        return {"code": 500, "msg": f"保存评论图片失败：{exc}"}

    return {
        "code": 200,
        "msg": "评论图片已上传",
        "image_path": str(target_path),
        "filename": safe_name,
        "size": len(content),
    }


@router.post("/video-comment/start")
async def douyin_start_video_comment(http_request: Request = None, request: Optional[dict] = None):
    nurture_conflict = build_douyin_nurture_conflict("执行视频评论")
    if nurture_conflict:
        return nurture_conflict
    set_douyin_ai_auth_token_from_request(http_request)
    global douyin_video_comment_running, douyin_video_comment_stop_requested, douyin_video_comment_background_task

    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()

    if douyin_running:
        return {"code": 400, "msg": "评论采集任务正在执行，请先停止后再开始视频评论。"}
    if douyin_interaction_running:
        return {"code": 400, "msg": "私信任务正在执行，请先停止后再开始视频评论。"}
    if douyin_follow_comment_running:
        return {"code": 400, "msg": "关注评论任务正在执行，请先停止后再开始视频评论。"}
    if douyin_mention_comment_running:
        return {"code": 400, "msg": "评论@精准客户任务正在执行，请先停止后再开始视频评论。"}
    if douyin_stranger_message_running:
        return {"code": 400, "msg": "私信引流任务正在执行，请先停止后再开始视频评论。"}
    if douyin_group_member_running:
        return {"code": 400, "msg": "群成员提取任务正在执行，请先停止后再开始视频评论。"}
    if douyin_video_comment_running:
        return {"code": 400, "msg": "视频评论任务已在执行中。"}

    payload = request if isinstance(request, dict) else {}
    comment_mode = normalize_douyin_video_comment_mode(payload.get("comment_mode"))
    comment_text = str(payload.get("comment_text", "") or "").strip()
    comment_prompt = str(payload.get("comment_prompt", "") or "").strip()
    comment_seed_text = str(payload.get("comment_seed_text", "") or "").strip()
    comment_image_path = str(payload.get("comment_image_path", "") or payload.get("image_path", "") or "").strip()
    raw_selected_ids = payload.get("selected_task_ids")
    raw_interval_min = payload.get("interval_minutes_min", payload.get("interval_minutes", 4))
    raw_interval_max = payload.get("interval_minutes_max", payload.get("interval_minutes", 6))
    interval_minutes_min = max(0, min(float(raw_interval_min or 4), 1440))
    interval_minutes_max = max(0, min(float(raw_interval_max or interval_minutes_min), 1440))
    if interval_minutes_max < interval_minutes_min:
        interval_minutes_min, interval_minutes_max = interval_minutes_max, interval_minutes_min
    interval_seconds_min = max(0, min(int(interval_minutes_min * 60), 24 * 60 * 60))
    interval_seconds_max = max(interval_seconds_min, min(int(interval_minutes_max * 60), 24 * 60 * 60))

    if comment_image_path and not os.path.isfile(comment_image_path):
        return {"code": 400, "msg": f"评论图片不存在：{comment_image_path}"}
    if comment_mode == "fixed" and not comment_text and not comment_image_path:
        return {"code": 400, "msg": "请先填写视频评论内容。"}
    if comment_mode == "rewrite" and not comment_seed_text:
        return {"code": 400, "msg": "请先填写用于 AI 改编的基准文案。"}

    selected_task_ids: Optional[Set[int]] = None
    selected_total = 0
    if raw_selected_ids is not None:
        if not isinstance(raw_selected_ids, list):
            return {"code": 400, "msg": "selected_task_ids must be a list"}
        selected_task_ids = {int(task_id) for task_id in raw_selected_ids}
        selected_total = len(selected_task_ids)
        if not selected_task_ids:
            return {"code": 400, "msg": "请先勾选至少一个视频任务。"}

    runnable_tasks = get_commentable_douyin_tasks(selected_task_ids)
    if not runnable_tasks:
        return {"code": 400, "msg": "没有可评论的视频任务，已评论过的视频会自动跳过。"}
    skipped_commented = max(0, selected_total - len(runnable_tasks)) if selected_total else 0

    config = load_global_config()
    if comment_mode in {"ai", "rewrite"} and not douyin_ai_available(config, http_request):
        return {"code": 400, "msg": "当前还没有配置 AI 接口 Key，暂时不能使用 AI 评论模式。"}

    account = get_active_douyin_account(config)
    if not account:
        return {
            "code": 400,
            "type": "no_online_account",
            "msg": "当前没有可用的抖音在线账号，请先登录。",
        }

    mode_label = douyin_video_comment_mode_label(comment_mode)
    douyin_log(
        f"[抖音视频评论] 准备启动，共 {len(runnable_tasks)} 个视频，间隔 {interval_minutes_min:g}-{interval_minutes_max:g} 分钟，模式 {mode_label}"
        + (f"，跳过已评论 {skipped_commented} 条" if skipped_commented else ""),
        "info",
    )
    douyin_video_comment_running = True
    douyin_video_comment_stop_requested = False
    douyin_video_comment_background_task = asyncio.create_task(
        run_douyin_video_comments(
            runnable_tasks,
            comment_mode,
            comment_text,
            comment_prompt,
            comment_seed_text,
            account,
            interval_seconds_min,
            interval_seconds_max,
            comment_image_path,
        )
    )
    return {
        "code": 200,
        "msg": f"视频评论任务已启动，使用账号 {account['id']}，共 {len(runnable_tasks)} 条任务，模式 {mode_label}，间隔 {interval_minutes_min:g}-{interval_minutes_max:g} 分钟随机"
        + (f"，已跳过 {skipped_commented} 条已评论视频" if skipped_commented else ""),
        "total": len(runnable_tasks),
        "account_id": account["id"],
        "interval_seconds": interval_seconds_min,
        "interval_seconds_min": interval_seconds_min,
        "interval_seconds_max": interval_seconds_max,
        "comment_mode": comment_mode,
        "skipped_commented": skipped_commented,
    }


@router.post("/video-comment/stop")
async def douyin_stop_video_comment():
    global douyin_video_comment_stop_requested, douyin_video_comment_background_task

    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    if not douyin_video_comment_running:
        douyin_video_comment_stop_requested = False
        douyin_video_comment_background_task = None
        return {"code": 200, "msg": "当前没有正在执行的视频评论任务。"}

    douyin_video_comment_stop_requested = True
    douyin_log("[抖音视频评论] 已请求停止", "warning")
    return {"code": 200, "msg": "已请求停止视频评论任务。"}


@router.get("/follow-comment/status")
async def douyin_follow_comment_status(lite: bool = False, include_users: bool = True):
    reconcile_douyin_follow_comment_runtime_state()
    users = None
    if include_users:
        users = [
            build_douyin_interaction_user_status_payload(row, lite=lite)
            for row in collect_douyin_interaction_users()
        ]
    return {
        "code": 200,
        "running": bool(douyin_follow_comment_state.get("running")),
        "state": douyin_follow_comment_state,
        **({"users": users} if users is not None else {}),
    }


@router.post("/follow-comment/start")
async def douyin_start_follow_comment(http_request: Request = None, request: Optional[dict] = None):
    nurture_conflict = build_douyin_nurture_conflict("执行关注评论")
    if nurture_conflict:
        return nurture_conflict
    set_douyin_ai_auth_token_from_request(http_request)
    global douyin_follow_comment_running, douyin_follow_comment_stop_requested, douyin_follow_comment_background_task

    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()

    if douyin_running:
        return {"code": 400, "msg": "评论采集任务正在执行，请先停止后再开始关注评论。"}
    if douyin_video_comment_running:
        return {"code": 400, "msg": "视频评论任务正在执行，请先停止后再开始关注评论。"}
    if douyin_mention_comment_running:
        return {"code": 400, "msg": "评论@精准客户任务正在执行，请先停止后再开始关注评论。"}
    if douyin_interaction_running:
        return {"code": 400, "msg": "私信任务正在执行，请先停止后再开始关注评论。"}
    if douyin_group_member_running:
        return {"code": 400, "msg": "群成员提取任务正在执行，请先停止后再开始关注评论。"}
    if douyin_stranger_message_running:
        return {"code": 400, "msg": "私信引流任务正在执行，请先停止后再开始关注评论。"}
    if douyin_follow_comment_running:
        return {"code": 400, "msg": "关注评论任务已在执行中。"}

    payload = request if isinstance(request, dict) else {}
    comment_mode = normalize_douyin_video_comment_mode(payload.get("comment_mode"))
    comment_text = str(payload.get("comment_text", "") or "").strip()
    comment_prompt = str(payload.get("comment_prompt", "") or "").strip()
    comment_seed_text = str(payload.get("comment_seed_text", "") or "").strip()
    comment_image_path = str(payload.get("comment_image_path", "") or payload.get("image_path", "") or "").strip()
    raw_users = payload.get("users", [])
    raw_interval_min = payload.get("interval_minutes_min", payload.get("interval_minutes", 4))
    raw_interval_max = payload.get("interval_minutes_max", payload.get("interval_minutes", 6))
    interval_minutes_min = max(0, min(float(raw_interval_min or 4), 1440))
    interval_minutes_max = max(0, min(float(raw_interval_max or interval_minutes_min), 1440))
    if interval_minutes_max < interval_minutes_min:
        interval_minutes_min, interval_minutes_max = interval_minutes_max, interval_minutes_min
    interval_seconds_min = max(0, min(int(interval_minutes_min * 60), 24 * 60 * 60))
    interval_seconds_max = max(interval_seconds_min, min(int(interval_minutes_max * 60), 24 * 60 * 60))
    if comment_image_path and not os.path.isfile(comment_image_path):
        return {"code": 400, "msg": f"评论图片不存在：{comment_image_path}"}
    if comment_mode == "fixed" and not comment_text and not comment_image_path:
        return {"code": 400, "msg": "请先填写主页首作品评论内容。"}
    if comment_mode == "rewrite" and not comment_seed_text:
        return {"code": 400, "msg": "请先填写基准文案后再执行 AI 改编。"}
    if not isinstance(raw_users, list) or not raw_users:
        return {"code": 400, "msg": "请先勾选至少一个高意向用户。"}

    valid_pool = {user_choice_key(row): row for row in collect_douyin_interaction_users()}
    selected_users: List[Dict] = []
    skipped_completed_users: List[Dict] = []
    seen = set()
    for raw_user in raw_users:
        key = user_choice_key(raw_user if isinstance(raw_user, dict) else {})
        if not key or key in seen:
            continue
        candidate = valid_pool.get(key)
        if not candidate:
            continue
        seen.add(key)
        if str(candidate.get("follow_comment_status", "") or "").strip() == "completed":
            skipped_completed_users.append(candidate)
            continue
        selected_users.append(candidate)

    if not selected_users:
        if skipped_completed_users:
            return {
                "code": 400,
                "msg": f"所选用户都已执行过关注评论，已自动跳过 {len(skipped_completed_users)} 人。",
                "skipped_completed": len(skipped_completed_users),
            }
        return {"code": 400, "msg": "当前任务列表里没有可用的高意向用户。"}

    config = load_global_config()
    if comment_mode in {"ai", "rewrite"} and not douyin_ai_available(config, http_request):
        return {
            "code": 400,
            "msg": "当前未配置 AI 接口 Key，不能使用 AI 评论模式。",
        }
    accounts = get_online_douyin_accounts(config)
    if not accounts:
        return {
            "code": 400,
            "type": "no_online_account",
            "msg": "当前没有可用的抖音在线账号，请先登录。",
        }

    batches = distribute_interaction_users(selected_users, accounts)
    for item in batches:
        update_douyin_follow_comment_users(
            item["users"],
            status="queued",
            error="",
            comment_text=comment_text if comment_mode == "fixed" else "",
            account_id=item["account"]["id"],
            result="",
            started_at="",
            finished_at="",
        )

    douyin_follow_comment_running = True
    douyin_follow_comment_stop_requested = False
    douyin_follow_comment_background_task = asyncio.create_task(
        run_douyin_follow_comments(
            selected_users,
            comment_mode,
            comment_text,
            comment_prompt,
            comment_seed_text,
            accounts,
            interval_seconds_min,
            interval_seconds_max,
            comment_image_path,
        )
    )
    account_ids = [item["account"]["id"] for item in batches]
    interval_desc = (
        f"{interval_minutes_min:g}-{interval_minutes_max:g} 分钟随机"
        if interval_minutes_min != interval_minutes_max
        else f"{interval_minutes_min:g} 分钟"
    )
    douyin_log(
        f"[抖音关注评论] 已启动，并发账号 {', '.join(str(account_id) for account_id in account_ids)}，共 {len(selected_users)} 人，间隔 {interval_desc}，模式 {douyin_video_comment_mode_label(comment_mode)}",
        "success",
    )
    return {
        "code": 200,
        "msg": (
            f"关注评论任务已启动，使用 {len(account_ids)} 个账号并发，共 {len(selected_users)} 人"
            + (
                f"，已自动跳过 {len(skipped_completed_users)} 个已完成用户"
                if skipped_completed_users
                else ""
            )
        ),
        "total": len(selected_users),
        "skipped_completed": len(skipped_completed_users),
        "account_id": account_ids[0] if len(account_ids) == 1 else None,
        "account_ids": account_ids,
        "interval_seconds": interval_seconds_min,
        "interval_seconds_min": interval_seconds_min,
        "interval_seconds_max": interval_seconds_max,
        "comment_mode": comment_mode,
        "comment_image_path": comment_image_path,
    }


@router.post("/follow-comment/stop")
async def douyin_stop_follow_comment():
    global douyin_follow_comment_stop_requested, douyin_follow_comment_background_task

    reconcile_douyin_follow_comment_runtime_state()
    if not douyin_follow_comment_running:
        douyin_follow_comment_stop_requested = False
        douyin_follow_comment_background_task = None
        return {"code": 200, "msg": "当前没有正在执行的关注评论任务。"}

    douyin_follow_comment_stop_requested = True
    douyin_follow_comment_state["message"] = "关注评论任务停止中"
    background_task = douyin_follow_comment_background_task
    if background_task and not background_task.done():
        background_task.cancel()
    douyin_log("[抖音关注评论] 已请求停止", "warning")
    return {"code": 200, "msg": "已请求停止关注评论任务。"}


@router.get("/interaction/status")
async def douyin_interaction_status(lite: bool = False, include_users: bool = True):
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    users = None
    if include_users:
        users = [
            build_douyin_interaction_user_status_payload(row, lite=lite)
            for row in collect_douyin_interaction_users()
        ]
    return {
        "code": 200,
        "running": bool(douyin_interaction_state.get("running")),
        "state": douyin_interaction_state,
        **({"users": users} if users is not None else {}),
    }


@router.post("/interaction/start")
async def douyin_start_interaction(http_request: Request = None, request: Optional[dict] = None):
    nurture_conflict = build_douyin_nurture_conflict("执行私信互动")
    if nurture_conflict:
        return nurture_conflict
    set_douyin_ai_auth_token_from_request(http_request)
    global douyin_interaction_running, douyin_interaction_stop_requested, douyin_interaction_background_task

    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()

    if douyin_running:
        return {"code": 400, "msg": "评论采集任务正在执行，请先停止后再开始私信。"}
    if douyin_video_comment_running:
        return {"code": 400, "msg": "视频评论任务正在执行，请先停止后再开始私信。"}
    if douyin_mention_comment_running:
        return {"code": 400, "msg": "评论@精准客户任务正在执行，请先停止后再开始私信。"}
    if douyin_follow_comment_running:
        return {"code": 400, "msg": "关注评论任务正在执行，请先停止后再开始私信。"}
    if douyin_group_member_running:
        return {"code": 400, "msg": "群成员提取任务正在执行，请先停止后再开始私信。"}
    if douyin_stranger_message_running:
        return {"code": 400, "msg": "私信引流任务正在执行，请先停止后再开始私信。"}
    if douyin_interaction_running:
        return {"code": 400, "msg": "私信任务已在执行中。"}

    payload = request if isinstance(request, dict) else {}
    message_mode = normalize_douyin_video_comment_mode(payload.get("message_mode"))
    fixed_messages = normalize_douyin_interaction_fixed_messages(payload)
    message = fixed_messages[0] if fixed_messages else ""
    message_prompt = str(payload.get("message_prompt", "") or "").strip()
    message_seed_text = str(payload.get("message_seed_text", "") or "").strip()
    raw_users = payload.get("users", [])
    raw_interval_seconds_min = payload.get("interval_seconds_min", payload.get("interval_seconds"))
    raw_interval_seconds_max = payload.get("interval_seconds_max", payload.get("interval_seconds"))
    if raw_interval_seconds_min is None and raw_interval_seconds_max is None:
        raw_interval_min = payload.get("interval_minutes_min", payload.get("interval_minutes", 4))
        raw_interval_max = payload.get("interval_minutes_max", payload.get("interval_minutes", 6))
        interval_minutes_min = max(0, min(float(raw_interval_min or 4), 1440))
        interval_minutes_max = max(0, min(float(raw_interval_max or interval_minutes_min), 1440))
        if interval_minutes_max < interval_minutes_min:
            interval_minutes_min, interval_minutes_max = interval_minutes_max, interval_minutes_min
        interval_seconds_min = max(0, min(int(interval_minutes_min * 60), 24 * 60 * 60))
        interval_seconds_max = max(interval_seconds_min, min(int(interval_minutes_max * 60), 24 * 60 * 60))
    else:
        interval_seconds_min = max(0, min(int(float(raw_interval_seconds_min or 20)), 24 * 60 * 60))
        interval_seconds_max = max(
            interval_seconds_min,
            min(int(float(raw_interval_seconds_max or interval_seconds_min)), 24 * 60 * 60),
        )
    if message_mode == "fixed" and not message:
        return {"code": 400, "msg": "请先填写固定私信内容。"}
    if message_mode == "rewrite" and not message_seed_text:
        return {"code": 400, "msg": "请先填写私信基准文案后再执行 AI 改编。"}
    if not isinstance(raw_users, list) or not raw_users:
        return {"code": 400, "msg": "请先勾选至少一个高意向用户。"}

    valid_pool = {user_choice_key(row): row for row in collect_douyin_interaction_users()}
    selected_users: List[Dict] = []
    seen = set()
    skipped_sent = 0
    for raw_user in raw_users:
        key = user_choice_key(raw_user if isinstance(raw_user, dict) else {})
        if not key or key in seen:
            continue
        candidate = valid_pool.get(key)
        if not candidate:
            continue
        status = str(candidate.get("interaction_status", "pending") or "pending").strip().lower()
        if status == "sent":
            skipped_sent += 1
            continue
        seen.add(key)
        selected_users.append(candidate)

    if not selected_users:
        if skipped_sent:
            return {"code": 400, "msg": f"当前勾选的 {skipped_sent} 位客户都已经完成私信发送，不需要重复执行。"}
        return {"code": 400, "msg": "当前任务列表里没有可用的高意向用户。"}
    if message_mode == "fixed":
        assign_douyin_interaction_fixed_messages(selected_users, fixed_messages)

    config = load_global_config()
    if message_mode in {"ai", "rewrite"} and not douyin_ai_available(config, http_request):
        return {
            "code": 400,
            "msg": "当前未配置 AI 接口 Key，不能使用 AI 私信模式。",
        }
    accounts = get_online_douyin_accounts(config)
    if not accounts:
        return {
            "code": 400,
            "type": "no_online_account",
            "msg": "当前没有可用的抖音在线账号，请先登录。",
        }

    batches = distribute_interaction_users(selected_users, accounts)
    for item in batches:
        update_douyin_interaction_users(
            item["users"],
            status="queued",
            error="",
            message="",
            account_id=item["account"]["id"],
            started_at="",
            finished_at="",
        )

    douyin_interaction_running = True
    douyin_interaction_stop_requested = False
    douyin_interaction_background_task = asyncio.create_task(
        run_douyin_interactions(
            selected_users,
            message_mode,
            message,
            fixed_messages,
            message_prompt,
            message_seed_text,
            accounts,
            interval_seconds_min,
            interval_seconds_max,
        )
    )
    account_ids = [item["account"]["id"] for item in batches]
    interval_desc = (
        f"{interval_seconds_min}-{interval_seconds_max} 秒随机"
        if interval_seconds_min != interval_seconds_max
        else f"{interval_seconds_min} 秒"
    )
    douyin_log(
        f"[抖音私信] 已启动，并发账号 {', '.join(str(account_id) for account_id in account_ids)}，共 {len(selected_users)} 人，已按账号自动分配，间隔 {interval_desc}，模式 {douyin_video_comment_mode_label(message_mode)}"
        + (f"，固定话术 {len(fixed_messages)} 条轮换" if message_mode == "fixed" and len(fixed_messages) > 1 else ""),
        "success",
    )
    launch_msg = f"私信任务已启动，使用 {len(account_ids)} 个账号并发，共 {len(selected_users)} 人，已按账号自动分配，每账号间隔 {interval_desc}"
    if message_mode == "fixed" and len(fixed_messages) > 1:
        launch_msg += f"，固定话术 {len(fixed_messages)} 条轮换发送"
    if skipped_sent:
        launch_msg += f"，已自动跳过 {skipped_sent} 位已发送客户"
    return {
        "code": 200,
        "msg": launch_msg,
        "total": len(selected_users),
        "account_id": account_ids[0] if len(account_ids) == 1 else None,
        "account_ids": account_ids,
        "concurrent_accounts": len(account_ids),
        "interval_seconds": interval_seconds_min,
        "interval_seconds_min": interval_seconds_min,
        "interval_seconds_max": interval_seconds_max,
        "message_mode": message_mode,
        "skipped_sent": skipped_sent,
    }


@router.post("/interaction/stop")
async def douyin_stop_interaction():
    global douyin_interaction_stop_requested, douyin_interaction_background_task

    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    if not douyin_interaction_running:
        douyin_interaction_stop_requested = False
        douyin_interaction_background_task = None
        return {"code": 200, "msg": "当前没有正在执行的私信任务。"}

    douyin_interaction_stop_requested = True
    douyin_interaction_state["message"] = "私信任务停止中"
    background_task = douyin_interaction_background_task
    if background_task and not background_task.done():
        background_task.cancel()
    douyin_log("[抖音私信] 已请求停止", "warning")
    return {"code": 200, "msg": "已请求停止私信任务。"}


@router.post("/interaction/reset")
async def douyin_reset_interaction(request: Optional[dict] = None):
    payload = request if isinstance(request, dict) else {}
    raw_users = payload.get("users", [])
    if not isinstance(raw_users, list) or not raw_users:
        return {"code": 400, "msg": "请先勾选要重置的客户。"}

    target_status = str(payload.get("status", "pending") or "pending").strip().lower()
    if target_status not in {"pending", "sent"}:
        return {"code": 400, "msg": "目标状态只能是「待发送」或「已发送」。"}

    valid_pool = {user_choice_key(row): row for row in collect_douyin_interaction_users()}
    rows: List[Dict] = []
    for raw_user in raw_users:
        key = user_choice_key(raw_user if isinstance(raw_user, dict) else {})
        if not key:
            continue
        candidate = valid_pool.get(key)
        if not candidate:
            continue
        current = str(candidate.get("interaction_status", "") or "").strip().lower()
        if target_status == "pending" and current != "interrupted":
            continue
        if target_status == "sent" and current not in {"interrupted", "failed", "pending"}:
            continue
        rows.append(candidate)

    if not rows:
        return {"code": 400, "msg": "没有可重置的客户，可能状态已经更新。"}

    finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if target_status == "sent" else ""
    update_douyin_interaction_users(
        rows,
        status=target_status,
        error="",
        finished_at=finished_at,
    )
    label = "待发送" if target_status == "pending" else "已发送"
    douyin_log(f"[抖音私信] 已将 {len(rows)} 位客户的私信状态重置为「{label}」", "info")
    return {"code": 200, "msg": f"已将 {len(rows)} 位客户重置为「{label}」。", "updated": len(rows)}


@router.get("/inbox/status")
async def douyin_inbox_status():
    reconcile_douyin_inbox_runtime_state()
    await ensure_douyin_inbox_monitor_scheduler()
    await ensure_douyin_self_comment_monitor_scheduler()
    return {
        "code": 200,
        "running": bool(douyin_inbox_state.get("running")),
        "state": douyin_inbox_state,
        "results": collect_douyin_inbox_results(),
        "monitors": list_douyin_inbox_monitor_states(),
    }


@router.get("/self-comment-monitor/status")
async def douyin_self_comment_monitor_status(account_id: int = 0):
    await ensure_douyin_self_comment_monitor_scheduler()
    config = load_global_config()
    account = get_douyin_account_by_id(account_id, config) if account_id > 0 else get_active_douyin_account(config)
    target_account_id = int((account or {}).get("id", 0) or account_id or 0)
    state = get_douyin_self_comment_monitor_state(target_account_id) if target_account_id > 0 else normalize_douyin_self_comment_monitor_state({})
    return {
        "code": 200,
        "running": bool(state.get("running")),
        "state": state,
        "results": collect_douyin_self_comment_monitor_results(target_account_id),
        "monitors": list_douyin_self_comment_monitor_states(),
        "account_id": target_account_id or None,
    }


@router.post("/self-comment-monitor/config")
async def douyin_save_self_comment_monitor_config_endpoint(http_request: Request = None, request: Optional[dict] = None):
    set_douyin_ai_auth_token_from_request(http_request)
    payload = request if isinstance(request, dict) else {}
    config = load_global_config()
    account_id = int(payload.get("account_id", 0) or 0)
    account = get_douyin_account_by_id(account_id, config) if account_id > 0 else get_active_douyin_account(config)
    target_account_id = int((account or {}).get("id", 0) or account_id or 0)
    if target_account_id <= 0:
        return {"code": 400, "msg": "请先选择要保存默认回复配置的抖音账号。"}

    interval_minutes = max(1, min(int(payload.get("interval_minutes", 30) or 30), 1440))
    max_videos = max(1, min(int(payload.get("max_videos", 20) or 20), 100))
    max_comments_per_video = max(5, min(int(payload.get("max_comments_per_video", 80) or 80), 500))
    auto_reply_enabled = bool(payload.get("auto_reply_enabled", False))
    reply_mode = normalize_douyin_inbox_reply_mode(payload.get("reply_mode", "fixed"))
    reply_message = str(payload.get("reply_message", "") or payload.get("message", "") or "").strip()
    reply_prompt = str(payload.get("reply_prompt", "") or "").strip()
    contact_value = str(payload.get("contact_value", "") or "").strip()
    reply_image_path = str(payload.get("reply_image_path", "") or payload.get("comment_image_path", "") or payload.get("image_path", "") or "").strip()
    if reply_image_path and not os.path.isfile(reply_image_path):
        return {"code": 400, "msg": f"评论回复图片不存在：{reply_image_path}"}

    validation_error = validate_douyin_inbox_reply_settings(
        auto_reply_enabled=auto_reply_enabled,
        reply_mode=reply_mode,
        reply_message=reply_message,
        reply_prompt=reply_prompt,
        contact_value=contact_value,
        reply_image_path=reply_image_path,
    )
    if validation_error:
        return {"code": 400, "msg": validation_error}
    if auto_reply_enabled and reply_mode in {"ai", "rewrite"} and not douyin_ai_available(config, http_request):
        return {"code": 400, "msg": "当前还没有配置 AI 接口 Key，暂时不能保存 AI 自动回复配置。"}

    existing = get_douyin_self_comment_monitor_state(target_account_id, create=True)
    state = normalize_douyin_self_comment_monitor_state(
        {
            **existing,
            "interval_minutes": interval_minutes,
            "account_id": target_account_id,
            "max_videos": max_videos,
            "max_comments_per_video": max_comments_per_video,
            "auto_reply_enabled": auto_reply_enabled,
            "reply_mode": reply_mode,
            "reply_message": reply_message,
            "reply_prompt": reply_prompt,
            "contact_value": contact_value,
            "reply_image_path": reply_image_path,
            "message": existing.get("message") or "我的评论区默认回复配置已保存。",
        },
        account_id=target_account_id,
    )
    douyin_self_comment_monitor_states[str(target_account_id)] = state
    save_douyin_self_comment_monitor_config()
    douyin_log(f"[抖音我的评论区] 已保存账号 {target_account_id} 默认回复配置。", "success")
    return {
        "code": 200,
        "msg": f"账号 {target_account_id} 的默认回复配置已保存。",
        "monitor": state,
        "monitors": list_douyin_self_comment_monitor_states(),
    }


@router.post("/self-comment-monitor/start")
async def douyin_start_self_comment_monitor(http_request: Request = None, request: Optional[dict] = None):
    set_douyin_ai_auth_token_from_request(http_request)
    payload = request if isinstance(request, dict) else {}
    config = load_global_config()
    account_id = int(payload.get("account_id", 0) or 0)
    account = get_douyin_account_by_id(account_id, config) if account_id > 0 else get_active_douyin_account(config)
    if not account:
        return {"code": 400, "type": "no_online_account", "msg": "当前没有可用的抖音在线账号，请先登录。"}
    if str(account.get("status", "") or "").strip() != "online":
        return {"code": 400, "type": "account_waiting_login", "msg": f"账号 {account.get('id')} 还没有完成登录，请先检查或登录。"}

    interval_minutes = max(1, min(int(payload.get("interval_minutes", 30) or 30), 1440))
    max_videos = max(1, min(int(payload.get("max_videos", 20) or 20), 100))
    max_comments_per_video = max(5, min(int(payload.get("max_comments_per_video", 80) or 80), 500))
    auto_reply_enabled = bool(payload.get("auto_reply_enabled", False))
    reply_mode = normalize_douyin_inbox_reply_mode(payload.get("reply_mode", "fixed"))
    reply_message = str(payload.get("reply_message", "") or payload.get("message", "") or "").strip()
    reply_prompt = str(payload.get("reply_prompt", "") or "").strip()
    contact_value = str(payload.get("contact_value", "") or "").strip()
    reply_image_path = str(payload.get("reply_image_path", "") or payload.get("comment_image_path", "") or payload.get("image_path", "") or "").strip()
    if reply_image_path and not os.path.isfile(reply_image_path):
        return {"code": 400, "msg": f"评论回复图片不存在：{reply_image_path}"}
    validation_error = validate_douyin_inbox_reply_settings(
        auto_reply_enabled=auto_reply_enabled,
        reply_mode=reply_mode,
        reply_message=reply_message,
        reply_prompt=reply_prompt,
        contact_value=contact_value,
        reply_image_path=reply_image_path,
    )
    if validation_error:
        return {"code": 400, "msg": validation_error}
    if auto_reply_enabled and reply_mode in {"ai", "rewrite"} and not douyin_ai_available(config, http_request):
        return {"code": 400, "msg": "当前还没有配置 AI 接口 Key，暂时不能开启 AI 自动回复。"}

    target_account_id = int(account.get("id", 0) or 0)
    task = douyin_self_comment_monitor_tasks_by_account.get(target_account_id)
    if task and not task.done():
        return {"code": 400, "msg": f"账号 {target_account_id} 的我的评论区监控正在执行中，请稍后再试。"}

    state = normalize_douyin_self_comment_monitor_state(
        {
            "enabled": True,
            "running": False,
            "interval_minutes": interval_minutes,
            "account_id": target_account_id,
            "max_videos": max_videos,
            "max_comments_per_video": max_comments_per_video,
            "auto_reply_enabled": auto_reply_enabled,
            "reply_mode": reply_mode,
            "reply_message": reply_message,
            "reply_prompt": reply_prompt,
            "contact_value": contact_value,
            "reply_image_path": reply_image_path,
            "last_error": "",
            "last_skip_reason": "",
            "message": "我的评论区监控已开启，准备立即检查一轮。",
        },
        account_id=target_account_id,
    )
    state["next_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    douyin_self_comment_monitor_states[str(target_account_id)] = state
    save_douyin_self_comment_monitor_config()
    douyin_self_comment_monitor_tasks_by_account[target_account_id] = asyncio.create_task(
        run_douyin_self_comment_monitor_cycle(target_account_id, trigger_type="manual")
    )
    douyin_log(
        f"[抖音我的评论区] 已开启账号 {target_account_id} 监控：作品 {max_videos} 个，每个最多 {max_comments_per_video} 条，间隔 {interval_minutes} 分钟，自动回复={'开启' if auto_reply_enabled else '关闭'}。",
        "success",
    )
    return {
        "code": 200,
        "msg": f"账号 {target_account_id} 的我的评论区监控已开启，并会立即检查一轮。",
        "monitor": state,
        "monitors": list_douyin_self_comment_monitor_states(),
    }


@router.post("/self-comment-monitor/stop")
async def douyin_stop_self_comment_monitor(request: Optional[dict] = None):
    payload = request if isinstance(request, dict) else {}
    account_id = int(payload.get("account_id", 0) or 0)
    config = load_global_config()
    account = get_douyin_account_by_id(account_id, config) if account_id > 0 else get_active_douyin_account(config)
    target_account_id = int((account or {}).get("id", 0) or account_id or 0)
    if target_account_id <= 0:
        return {"code": 400, "msg": "请先指定要停止监控的抖音账号。"}
    state = get_douyin_self_comment_monitor_state(target_account_id)
    if not state.get("enabled"):
        return {"code": 200, "msg": f"账号 {target_account_id} 当前没有开启我的评论区监控。", "monitor": state, "monitors": list_douyin_self_comment_monitor_states()}
    state["enabled"] = False
    state["running"] = False
    state["next_run_at"] = ""
    state["message"] = "我的评论区监控已关闭。"
    task = douyin_self_comment_monitor_tasks_by_account.get(target_account_id)
    if task and not task.done():
        task.cancel()
    save_douyin_self_comment_monitor_config()
    douyin_log(f"[抖音我的评论区] 已关闭账号 {target_account_id} 的监控。", "warning")
    return {
        "code": 200,
        "msg": f"账号 {target_account_id} 的我的评论区监控已关闭。",
        "monitor": state,
        "monitors": list_douyin_self_comment_monitor_states(),
    }


@router.post("/self-comment-monitor/run-once")
async def douyin_run_self_comment_monitor_once(request: Optional[dict] = None):
    payload = request if isinstance(request, dict) else {}
    config = load_global_config()
    account_id = int(payload.get("account_id", 0) or 0)
    account = get_douyin_account_by_id(account_id, config) if account_id > 0 else get_active_douyin_account(config)
    if not account:
        return {"code": 400, "type": "no_online_account", "msg": "当前没有可用的抖音在线账号，请先登录。"}
    target_account_id = int(account.get("id", 0) or 0)
    state = get_douyin_self_comment_monitor_state(target_account_id, create=True)
    if not state.get("enabled"):
        state["enabled"] = True
        state["account_id"] = target_account_id
    task = douyin_self_comment_monitor_tasks_by_account.get(target_account_id)
    if task and not task.done():
        return {"code": 400, "msg": f"账号 {target_account_id} 的我的评论区监控正在执行中，请稍后再试。"}
    result = await run_douyin_self_comment_monitor_cycle(target_account_id, trigger_type="manual")
    return {
        "code": 200 if result.get("status") != "failed" else 400,
        "msg": result.get("message", "我的评论区检查完成。"),
        "result": result,
        "monitor": get_douyin_self_comment_monitor_state(target_account_id),
        "results": collect_douyin_self_comment_monitor_results(target_account_id),
    }


@router.post("/inbox/monitor/start")
async def douyin_start_inbox_monitor(request: Optional[dict] = None):
    payload = request if isinstance(request, dict) else {}
    config = load_global_config()
    accounts = _normalize_accounts(config.get("douyin_accounts"))
    account_id = int(payload.get("account_id", 0) or 0)
    account = get_douyin_account_by_id(account_id, config) if account_id > 0 else get_active_douyin_account(config)
    if account_id > 0 and not account:
        return {"code": 400, "msg": f"未找到抖音账号 {account_id}。"}
    if not account and not accounts:
        return {"code": 400, "msg": "当前没有可用的抖音账号配置。"}

    interval_minutes = max(1, min(int(payload.get("interval_minutes", 30) or 30), 1440))
    max_conversations = max(1, min(int(payload.get("max_conversations", 100) or 100), 100))
    auto_reply_enabled = bool(payload.get("auto_reply_enabled", False))
    reply_mode = normalize_douyin_inbox_reply_mode(payload.get("reply_mode", "fixed"))
    reply_message = str(payload.get("message", "") or payload.get("reply_message", "") or "").strip()
    reply_prompt = str(payload.get("reply_prompt", "") or "").strip()
    contact_value = str(payload.get("contact_value", "") or "").strip()
    validation_error = validate_douyin_inbox_reply_settings(
        auto_reply_enabled=auto_reply_enabled,
        reply_mode=reply_mode,
        reply_message=reply_message,
        reply_prompt=reply_prompt,
        contact_value=contact_value,
    )
    if validation_error:
        return {"code": 400, "msg": validation_error}
    target_account_id = int((account or {}).get("id", 0) or account_id or config.get("douyin_default_account_id", 1) or 1)
    state = normalize_douyin_inbox_monitor_state(
        {
            "enabled": True,
            "running": False,
            "interval_minutes": interval_minutes,
            "account_id": target_account_id or None,
            "max_conversations": max_conversations,
            "last_error": "",
            "last_skip_reason": "",
            "message": "当前消息页自动采集已开启，系统会定时进入消息页刷新会话。",
            "last_cycle_status": "idle",
            "auto_reply_enabled": auto_reply_enabled,
            "reply_mode": reply_mode,
            "reply_message": reply_message,
            "reply_prompt": reply_prompt,
            "contact_value": contact_value,
        },
        account_id=target_account_id,
    )
    state["next_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    douyin_inbox_monitor_states[str(target_account_id)] = state
    save_douyin_inbox_monitor_config()
    await ensure_douyin_inbox_monitor_scheduler()
    douyin_log(
        (
            f"[抖音私信聚合] 已开启自动采集，账号 {target_account_id}，每 {interval_minutes} 分钟检查一次，"
            f"最多读取 {max_conversations} 条，自动回复={auto_reply_enabled and douyin_inbox_reply_mode_label(reply_mode) or '关闭'}"
        ),
        "success",
    )
    return {
        "code": 200,
        "msg": (
            f"当前消息页自动采集已开启，账号 {target_account_id} 会每 {interval_minutes} 分钟自动检查一次；如果当前有其他抖音任务，本轮会自动跳过。"
            + (
                f" 自动回复模式：{douyin_inbox_reply_mode_label(reply_mode)}。"
                if auto_reply_enabled
                else ""
            )
        ),
        "monitor": state,
        "monitors": list_douyin_inbox_monitor_states(),
    }


@router.post("/inbox/monitor/stop")
async def douyin_stop_inbox_monitor(request: Optional[dict] = None):
    payload = request if isinstance(request, dict) else {}
    account_id = int(payload.get("account_id", 0) or 0)
    if account_id <= 0:
        return {"code": 400, "msg": "请先指定要关闭自动采集的账号。"}
    state = get_douyin_inbox_monitor_state(account_id)
    if int(state.get("account_id", 0) or 0) <= 0:
        return {"code": 200, "msg": f"账号 {account_id} 当前没有开启当前消息页自动采集。", "monitor": state, "monitors": list_douyin_inbox_monitor_states()}
    state.update(
        {
            "enabled": False,
            "running": False,
            "message": "当前消息页自动采集已关闭。",
            "next_run_at": "",
            "last_cycle_status": "idle",
        }
    )
    save_douyin_inbox_monitor_config()
    douyin_log(f"[抖音私信聚合] 已关闭自动采集，账号 {account_id}", "warning")
    return {
        "code": 200,
        "msg": f"账号 {account_id} 的当前消息页自动采集已关闭。",
        "monitor": state,
        "monitors": list_douyin_inbox_monitor_states(),
    }


@router.post("/inbox/collect")
async def douyin_collect_inbox_messages(request: Optional[dict] = None):
    nurture_conflict = build_douyin_nurture_conflict("采集当前消息页会话")
    if nurture_conflict:
        return nurture_conflict
    global douyin_inbox_running, douyin_inbox_stop_requested
    global douyin_inbox_background_task

    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()
    reconcile_douyin_inbox_runtime_state()

    if douyin_running:
        return {"code": 400, "msg": "评论采集任务正在执行，请先停止后再采集当前消息页会话。"}
    if douyin_video_comment_running:
        return {"code": 400, "msg": "视频评论任务正在执行，请先停止后再采集当前消息页会话。"}
    if douyin_mention_comment_running:
        return {"code": 400, "msg": "评论@精准客户任务正在执行，请先停止后再采集当前消息页会话。"}
    if douyin_follow_comment_running:
        return {"code": 400, "msg": "关注评论任务正在执行，请先停止后再采集当前消息页会话。"}
    if douyin_interaction_running:
        return {"code": 400, "msg": "精准客户私信任务正在执行，请先停止后再采集当前消息页会话。"}
    if douyin_group_member_running:
        return {"code": 400, "msg": "群成员提取任务正在执行，请先停止后再采集当前消息页会话。"}
    if douyin_stranger_message_running:
        return {"code": 400, "msg": "陌生人私信任务正在执行，请先停止后再采集当前消息页会话。"}
    if douyin_inbox_running:
        return {"code": 400, "msg": "私信聚合采集任务已在执行中。"}

    payload = request if isinstance(request, dict) else {}
    account_id = int(payload.get("account_id", 0) or 0)
    max_conversations = max(1, min(int(payload.get("max_conversations", 100) or 100), 100))
    auto_reply_enabled = bool(payload.get("auto_reply_enabled", False))
    reply_mode = normalize_douyin_inbox_reply_mode(payload.get("reply_mode", "fixed"))
    reply_message = str(payload.get("message", "") or payload.get("reply_message", "") or "").strip()
    reply_prompt = str(payload.get("reply_prompt", "") or "").strip()
    contact_value = str(payload.get("contact_value", "") or "").strip()
    validation_error = validate_douyin_inbox_reply_settings(
        auto_reply_enabled=auto_reply_enabled,
        reply_mode=reply_mode,
        reply_message=reply_message,
        reply_prompt=reply_prompt,
        contact_value=contact_value,
    )
    if validation_error:
        return {"code": 400, "msg": validation_error}
    config = load_global_config()
    account = get_douyin_account_by_id(account_id, config) if account_id > 0 else get_active_douyin_account(config)
    if not account:
        return {
            "code": 400,
            "type": "no_online_account",
            "msg": "当前没有可用的抖音在线账号，请先登录。",
        }
    if str(account.get("status", "") or "").strip() != "online":
        return {
            "code": 400,
            "type": "account_waiting_login",
            "msg": f"账号 {account.get('id')} 还没有登录完成，请先检查或登录。",
        }

    douyin_inbox_running = True
    douyin_inbox_stop_requested = False
    douyin_inbox_background_task = asyncio.create_task(
        run_douyin_inbox_collection(
            account,
            max_conversations,
            auto_reply_enabled=auto_reply_enabled,
            reply_mode=reply_mode,
            reply_message=reply_message,
            reply_prompt=reply_prompt,
            contact_value=contact_value,
        )
    )
    douyin_log(
        (
            f"[抖音私信聚合] 已启动当前消息页采集，账号 {account['id']}，最多 {max_conversations} 条，"
            f"自动回复={auto_reply_enabled and douyin_inbox_reply_mode_label(reply_mode) or '关闭'}"
        ),
        "success",
    )
    return {
        "code": 200,
        "msg": (
            f"当前消息页会话采集已启动，使用账号 {account['id']}"
            + (
                f"，并会按 {douyin_inbox_reply_mode_label(reply_mode)} 自动回复符合条件的私信"
                if auto_reply_enabled
                else ""
            )
        ),
        "account_id": account["id"],
        "max_conversations": max_conversations,
    }


@router.post("/inbox/conversation/detail")
async def douyin_collect_inbox_conversation_detail(request: Optional[dict] = None):
    payload = request if isinstance(request, dict) else {}
    row_payload = payload.get("row") if isinstance(payload.get("row"), dict) else {}
    account_id = int(
        payload.get("account_id", 0)
        or row_payload.get("account_id", 0)
        or 0
    )
    if account_id <= 0:
        return {"code": 400, "msg": "请先指定要查看详情的抖音账号。"}

    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()
    reconcile_douyin_inbox_runtime_state()

    busy, busy_reason = is_douyin_inbox_monitor_busy(account_id)
    if busy:
        return {"code": 400, "msg": f"当前无法读取会话详情：{busy_reason}。"}

    config = load_global_config()
    account = get_douyin_account_by_id(account_id, config)
    if not account:
        return {"code": 400, "msg": f"未找到抖音账号 {account_id}。"}
    if str(account.get("status", "") or "").strip() != "online":
        return {"code": 400, "msg": f"账号 {account_id} 还没有登录完成，请先检查或登录。"}

    conversation_key = normalize_douyin_text(
        payload.get("conversation_key", "")
        or row_payload.get("conversation_key", "")
        or row_payload.get("conversation_id", "")
    )
    profile_url = str(payload.get("profile_url", "") or row_payload.get("profile_url", "") or "").strip()
    username = normalize_douyin_text(payload.get("username", "") or row_payload.get("username", ""))
    preview_text = normalize_douyin_text(
        payload.get("preview_text", "")
        or row_payload.get("preview_text", "")
        or row_payload.get("incoming_message", "")
    )

    saved_rows = collect_douyin_inbox_results(account_id)
    matched_row = None
    for existing in saved_rows:
        existing_key = stranger_message_row_key(existing)
        if conversation_key and normalize_douyin_text(existing.get("conversation_key", "") or existing.get("conversation_id", "")) == conversation_key:
            matched_row = existing
            break
        if profile_url and str(existing.get("profile_url", "") or "").strip() == profile_url:
            matched_row = existing
            break
        if username and normalize_douyin_text(existing.get("username", "")) == username and preview_text and normalize_douyin_text(existing.get("preview_text", "") or existing.get("incoming_message", "")) == preview_text:
            matched_row = existing
            break
        requested_key = stranger_message_row_key({**row_payload, "account_id": account_id})
        if requested_key and existing_key == requested_key:
            matched_row = existing
            break

    target_row = normalize_douyin_inbox_row(
        {
            **(matched_row or {}),
            **row_payload,
            "account_id": account_id,
            "conversation_key": conversation_key or (matched_row or {}).get("conversation_key", ""),
            "profile_url": profile_url or (matched_row or {}).get("profile_url", ""),
            "username": username or (matched_row or {}).get("username", ""),
            "preview_text": preview_text or (matched_row or {}).get("preview_text", ""),
        }
    )
    if not normalize_douyin_text(target_row.get("conversation_key", "")) and not str(target_row.get("profile_url", "") or "").strip() and not normalize_douyin_text(target_row.get("username", "")):
        return {"code": 400, "msg": "没有找到可用于打开详情的会话标识。"}

    try:
        douyin_log(
            f"[抖音私信聚合] 开始读取会话详情，账号 {account_id}，目标 {normalize_douyin_text(target_row.get('username', '')) or '-'}",
            "info",
        )
        worker = get_douyin_inbox_page_worker(account_id)
        detail = await worker.fetch_detail(target_row)
        return {
            "code": 200,
            "msg": "会话详情已读取。",
            "detail": detail,
        }
    except Exception as exc:
        douyin_log(f"[抖音私信聚合] 读取会话详情失败：{exc}", "error")
        fallback_detail = build_douyin_inbox_fallback_detail(target_row)
        if fallback_detail.get("messages"):
            return {
                "code": 200,
                "msg": f"实时读取失败，先展示已采集消息：{exc}",
                "detail": fallback_detail,
            }
        return {"code": 400, "msg": f"读取会话详情失败：{exc}"}


@router.post("/inbox/send")
async def douyin_send_inbox_message(request: Optional[dict] = None):
    payload = request if isinstance(request, dict) else {}
    row_payload = payload.get("row") if isinstance(payload.get("row"), dict) else {}
    reply_message = str(payload.get("message", "") or "").strip()
    if not reply_message:
        return {"code": 400, "msg": "请先输入要发送的消息。"}

    account_id = int(
        payload.get("account_id", 0)
        or row_payload.get("account_id", 0)
        or 0
    )
    if account_id <= 0:
        return {"code": 400, "msg": "请先指定要发送消息的抖音账号。"}

    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()
    reconcile_douyin_inbox_runtime_state()

    busy, busy_reason = is_douyin_inbox_monitor_busy(account_id)
    if busy:
        return {"code": 400, "msg": f"当前无法发送消息：{busy_reason}。"}

    config = load_global_config()
    account = get_douyin_account_by_id(account_id, config)
    if not account:
        return {"code": 400, "msg": f"未找到抖音账号 {account_id}。"}
    if str(account.get("status", "") or "").strip() != "online":
        return {"code": 400, "msg": f"账号 {account_id} 还没有登录完成，请先检查或登录。"}

    conversation_key = normalize_douyin_text(
        payload.get("conversation_key", "")
        or row_payload.get("conversation_key", "")
        or row_payload.get("conversation_id", "")
    )
    profile_url = str(payload.get("profile_url", "") or row_payload.get("profile_url", "") or "").strip()
    username = normalize_douyin_text(payload.get("username", "") or row_payload.get("username", ""))
    preview_text = normalize_douyin_text(
        payload.get("preview_text", "")
        or row_payload.get("preview_text", "")
        or row_payload.get("incoming_message", "")
    )

    saved_rows = collect_douyin_inbox_results(account_id)
    matched_row = None
    requested_key = stranger_message_row_key({**row_payload, "account_id": account_id})
    for existing in saved_rows:
        existing_key = stranger_message_row_key(existing)
        if conversation_key and normalize_douyin_text(existing.get("conversation_key", "") or existing.get("conversation_id", "")) == conversation_key:
            matched_row = existing
            break
        if profile_url and str(existing.get("profile_url", "") or "").strip() == profile_url:
            matched_row = existing
            break
        if username and normalize_douyin_text(existing.get("username", "")) == username and preview_text and normalize_douyin_text(existing.get("preview_text", "") or existing.get("incoming_message", "")) == preview_text:
            matched_row = existing
            break
        if requested_key and existing_key == requested_key:
            matched_row = existing
            break

    target_row = normalize_douyin_inbox_row(
        {
            **(matched_row or {}),
            **row_payload,
            "account_id": account_id,
            "conversation_key": conversation_key or (matched_row or {}).get("conversation_key", ""),
            "profile_url": profile_url or (matched_row or {}).get("profile_url", ""),
            "username": username or (matched_row or {}).get("username", ""),
            "preview_text": preview_text or (matched_row or {}).get("preview_text", ""),
        }
    )
    if not normalize_douyin_text(target_row.get("conversation_key", "")) and not str(target_row.get("profile_url", "") or "").strip() and not normalize_douyin_text(target_row.get("username", "")):
        return {"code": 400, "msg": "没有找到可用于发送的会话标识。"}

    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    update_douyin_inbox_rows(
        [target_row],
        status="processing",
        error="",
        message=reply_message,
        account_id=account_id,
        started_at=started_at,
        finished_at="",
    )
    try:
        douyin_log(
            f"[抖音私信聚合] 开始发送消息，账号 {account_id}，目标 {normalize_douyin_text(target_row.get('username', '')) or '-'}",
            "info",
        )
        worker = get_douyin_inbox_page_worker(account_id)
        result = await worker.send_message(target_row, reply_message)
        finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        update_douyin_inbox_rows(
            [target_row],
            status="sent",
            error="",
            message=reply_message,
            account_id=account_id,
            started_at=started_at,
            finished_at=finished_at,
        )
        refreshed_row = {
            **target_row,
            "reply_status": "sent",
            "reply_error": "",
            "reply_message": reply_message,
            "reply_account_id": str(account_id),
            "reply_started_at": started_at,
            "reply_finished_at": finished_at,
            "reply_updated_at": finished_at,
        }
        merge_douyin_inbox_results(account_id, [refreshed_row])
        return {
            "code": 200,
            "msg": "消息已发送。",
            "detail": result.get("detail", {}),
            "result": result,
        }
    except Exception as exc:
        update_douyin_inbox_rows(
            [target_row],
            status="failed",
            error=str(exc),
            message=reply_message,
            account_id=account_id,
            started_at=started_at,
            finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        douyin_log(f"[抖音私信聚合] 发送消息失败：{exc}", "error")
        return {"code": 400, "msg": f"发送消息失败：{exc}"}


@router.get("/stranger-messages/status")
async def douyin_stranger_message_status():
    reconcile_douyin_stranger_message_runtime_state()
    return {
        "code": 200,
        "running": bool(douyin_stranger_message_state.get("running")),
        "state": douyin_stranger_message_state,
        "results": collect_douyin_stranger_message_results(),
        "monitors": list_douyin_stranger_message_monitor_states(),
    }


@router.post("/stranger-messages/monitor/start")
async def douyin_start_stranger_message_monitor(http_request: Request = None, request: Optional[dict] = None):
    set_douyin_ai_auth_token_from_request(http_request)
    payload = request if isinstance(request, dict) else {}
    config = load_global_config()
    accounts = _normalize_accounts(config.get("douyin_accounts"))
    account_id = int(payload.get("account_id", 0) or 0)
    account = get_douyin_account_by_id(account_id, config) if account_id > 0 else get_active_douyin_account(config)
    if account_id > 0 and not account:
        return {"code": 400, "msg": f"未找到抖音账号 {account_id}。"}
    if not account and not accounts:
        return {"code": 400, "msg": "当前没有可用的抖音账号配置。"}

    interval_minutes = max(1, min(int(payload.get("interval_minutes", 30) or 30), 1440))
    max_conversations = max(1, min(int(payload.get("max_conversations", 100) or 100), 100))
    auto_reply_enabled = bool(payload.get("auto_reply_enabled", True))
    reply_mode = normalize_douyin_stranger_reply_mode(payload.get("reply_mode"))
    reply_message = str(payload.get("message", "") or "").strip()
    reply_prompt = str(payload.get("reply_prompt", "") or "").strip()
    contact_value = str(payload.get("contact_value", "") or "").strip()
    if auto_reply_enabled:
        if reply_mode == "fixed" and not reply_message:
            return {"code": 400, "msg": "开启监控自动回复前，请先填写固定引流文案。"}
        if reply_mode == "ai_lead":
            if not contact_value:
                return {"code": 400, "msg": "开启监控自动回复前，请先填写绿泡泡联系方式。"}
            if not douyin_ai_available(config, http_request):
                return {"code": 400, "msg": "当前还没有配置 AI 接口 Key，暂时不能开启 AI 自动回复监控。"}
    target_account_id = int((account or {}).get("id", 0) or account_id or config.get("douyin_default_account_id", 1) or 1)

    state = normalize_douyin_stranger_message_monitor_state(
        {
            "enabled": True,
            "running": False,
            "interval_minutes": interval_minutes,
            "account_id": target_account_id or None,
            "max_conversations": max_conversations,
            "auto_reply_enabled": auto_reply_enabled,
            "reply_mode": reply_mode,
            "reply_message": reply_message,
            "reply_prompt": reply_prompt,
            "contact_value": contact_value,
            "last_error": "",
            "last_skip_reason": "",
        },
        account_id=target_account_id,
    )
    state["next_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state["message"] = "陌生人消息监控已开启，系统会自动进入陌生人消息列表检查。"
    state["last_cycle_status"] = "idle"
    douyin_stranger_message_monitor_states[str(target_account_id)] = state
    save_douyin_stranger_message_monitor_config()
    await ensure_douyin_stranger_message_monitor_scheduler()
    douyin_log(
        f"[抖音陌生人消息监控] 已开启，账号 {target_account_id}，每 {interval_minutes} 分钟检查一次，最多读取 {max_conversations} 条",
        "success",
    )
    return {
        "code": 200,
        "msg": (
            f"陌生人消息监控已开启，账号 {target_account_id} 会每 {interval_minutes} 分钟自动进入“陌生人消息”列表检查一次；"
            f"如果当前有其他抖音任务，本轮会自动跳过。"
            f"{' 发现带未读红点的新增陌生人消息后会自动逐个回复新会话。' if auto_reply_enabled else ' 当前只监控带未读红点的新会话，不自动回复。'}"
        ),
        "monitor": state,
        "monitors": list_douyin_stranger_message_monitor_states(),
    }


@router.post("/stranger-messages/monitor/stop")
async def douyin_stop_stranger_message_monitor(request: Optional[dict] = None):
    payload = request if isinstance(request, dict) else {}
    account_id = int(payload.get("account_id", 0) or 0)
    if account_id <= 0:
        return {"code": 400, "msg": "请先指定要关闭监控的账号。"}
    state = get_douyin_stranger_message_monitor_state(account_id)
    if int(state.get("account_id", 0) or 0) <= 0:
        return {"code": 200, "msg": f"账号 {account_id} 当前没有开启陌生人消息监控。", "monitor": state, "monitors": list_douyin_stranger_message_monitor_states()}
    state.update(
        {
            "enabled": False,
            "running": False,
            "message": "陌生人消息监控已关闭。",
            "next_run_at": "",
            "last_cycle_status": "idle",
        }
    )
    save_douyin_stranger_message_monitor_config()
    douyin_log(f"[抖音陌生人消息监控] 已关闭，账号 {account_id}", "warning")
    return {
        "code": 200,
        "msg": f"账号 {account_id} 的陌生人消息监控已关闭。",
        "monitor": state,
        "monitors": list_douyin_stranger_message_monitor_states(),
    }


@router.post("/stranger-messages/collect")
async def douyin_collect_stranger_messages(request: Optional[dict] = None):
    nurture_conflict = build_douyin_nurture_conflict("采集陌生人私信")
    if nurture_conflict:
        return nurture_conflict
    global douyin_stranger_message_running, douyin_stranger_message_stop_requested
    global douyin_stranger_message_background_task

    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()

    if douyin_running:
        return {"code": 400, "msg": "评论采集任务正在执行，请先停止后再采集陌生人私信。"}
    if douyin_video_comment_running:
        return {"code": 400, "msg": "视频评论任务正在执行，请先停止后再采集陌生人私信。"}
    if douyin_mention_comment_running:
        return {"code": 400, "msg": "评论@精准客户任务正在执行，请先停止后再采集陌生人私信。"}
    if douyin_follow_comment_running:
        return {"code": 400, "msg": "关注评论任务正在执行，请先停止后再采集陌生人私信。"}
    if douyin_interaction_running:
        return {"code": 400, "msg": "精准客户私信任务正在执行，请先停止后再采集陌生人私信。"}
    if douyin_group_member_running:
        return {"code": 400, "msg": "群成员提取任务正在执行，请先停止后再采集陌生人私信。"}
    if douyin_stranger_message_running:
        return {"code": 400, "msg": "私信引流任务已在执行中。"}

    payload = request if isinstance(request, dict) else {}
    account_id = int(payload.get("account_id", 0) or 0)
    max_conversations = max(1, min(int(payload.get("max_conversations", 100) or 100), 100))
    config = load_global_config()
    account = get_douyin_account_by_id(account_id, config) if account_id > 0 else get_active_douyin_account(config)
    if not account:
        return {
            "code": 400,
            "type": "no_online_account",
            "msg": "当前没有可用的抖音在线账号，请先登录。",
        }
    if str(account.get("status", "") or "").strip() != "online":
        return {
            "code": 400,
            "type": "account_waiting_login",
            "msg": f"账号 {account.get('id')} 还没有登录完成，请先检查或登录。",
        }

    douyin_stranger_message_running = True
    douyin_stranger_message_stop_requested = False
    douyin_stranger_message_background_task = asyncio.create_task(
        run_douyin_stranger_message_collection(account, max_conversations)
    )
    douyin_log(
        f"[抖音私信引流] 已启动采集，账号 {account['id']}，最多 {max_conversations} 条",
        "success",
    )
    return {
        "code": 200,
        "msg": f"陌生人私信采集已启动，使用账号 {account['id']}",
        "account_id": account["id"],
        "max_conversations": max_conversations,
    }


@router.post("/stranger-messages/send")
async def douyin_send_stranger_messages(http_request: Request = None, request: Optional[dict] = None):
    nurture_conflict = build_douyin_nurture_conflict("发送私信引流")
    if nurture_conflict:
        return nurture_conflict
    set_douyin_ai_auth_token_from_request(http_request)
    global douyin_stranger_message_running, douyin_stranger_message_stop_requested
    global douyin_stranger_message_background_task

    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()

    if douyin_running:
        return {"code": 400, "msg": "评论采集任务正在执行，请先停止后再发送私信引流。"}
    if douyin_video_comment_running:
        return {"code": 400, "msg": "视频评论任务正在执行，请先停止后再发送私信引流。"}
    if douyin_mention_comment_running:
        return {"code": 400, "msg": "评论@精准客户任务正在执行，请先停止后再发送私信引流。"}
    if douyin_follow_comment_running:
        return {"code": 400, "msg": "关注评论任务正在执行，请先停止后再发送私信引流。"}
    if douyin_interaction_running:
        return {"code": 400, "msg": "精准客户私信任务正在执行，请先停止后再发送私信引流。"}
    if douyin_group_member_running:
        return {"code": 400, "msg": "群成员提取任务正在执行，请先停止后再发送私信引流。"}
    if douyin_stranger_message_running:
        return {"code": 400, "msg": "私信引流任务已在执行中。"}

    payload = request if isinstance(request, dict) else {}
    account_id = int(payload.get("account_id", 0) or 0)
    message = str(payload.get("message", "") or "").strip()
    reply_mode = normalize_douyin_stranger_reply_mode(payload.get("reply_mode"))
    reply_prompt = str(payload.get("reply_prompt", "") or "").strip()
    contact_value = str(payload.get("contact_value", "") or "").strip()
    raw_rows = payload.get("rows", [])
    if reply_mode == "fixed" and not message:
        return {"code": 400, "msg": "请先填写引流私信内容。"}
    if reply_mode == "ai_lead":
        if not contact_value:
            return {"code": 400, "msg": "请先填写要引导添加的联系方式。"}
        config = load_global_config()
        if not douyin_ai_available(config, http_request):
            return {"code": 400, "msg": "当前还没有配置 AI 接口 Key，暂时不能使用 AI 引导加绿泡泡模式。"}
    if not isinstance(raw_rows, list) or not raw_rows:
        return {"code": 400, "msg": "请先勾选至少一条陌生人私信。"}

    config = load_global_config()
    account = get_douyin_account_by_id(account_id, config) if account_id > 0 else get_active_douyin_account(config)
    if not account:
        return {
            "code": 400,
            "type": "no_online_account",
            "msg": "当前没有可用的抖音在线账号，请先登录。",
        }
    if str(account.get("status", "") or "").strip() != "online":
        return {
            "code": 400,
            "type": "account_waiting_login",
            "msg": f"账号 {account.get('id')} 还没有登录完成，请先检查或登录。",
        }

    valid_pool = {
        stranger_message_row_key(row): row
        for row in collect_douyin_stranger_message_results(account["id"])
    }
    selected_rows: List[Dict] = []
    seen = set()
    for raw_row in raw_rows:
        key = stranger_message_row_key(raw_row if isinstance(raw_row, dict) else {})
        if not key or key in seen:
            continue
        candidate = valid_pool.get(key)
        if not candidate:
            continue
        seen.add(key)
        selected_rows.append(candidate)
    if not selected_rows:
        return {"code": 400, "msg": "当前账号下没有匹配的陌生人私信数据，请先重新采集。"}

    update_douyin_stranger_message_rows(
        selected_rows,
        status="queued",
        error="",
        message=(
            message
            if reply_mode == "fixed"
            else "\n".join(
                [
                    "AI 引导加绿泡泡",
                    "麻烦您绿泡泡",
                    normalize_douyin_text(contact_value),
                ]
            )
        ),
        account_id=account["id"],
        started_at="",
        finished_at="",
    )
    douyin_stranger_message_running = True
    douyin_stranger_message_stop_requested = False
    douyin_stranger_message_background_task = asyncio.create_task(
        run_douyin_stranger_message_send(
            account,
            selected_rows,
            message,
            reply_mode=reply_mode,
            reply_prompt=reply_prompt,
            contact_value=contact_value,
        )
    )
    douyin_log(
        f"[抖音私信引流] 已启动发送，账号 {account['id']}，共 {len(selected_rows)} 人，模式 {douyin_stranger_reply_mode_label(reply_mode)}",
        "success",
    )
    return {
        "code": 200,
        "msg": f"私信引流已启动，使用账号 {account['id']}，共 {len(selected_rows)} 人，模式 {douyin_stranger_reply_mode_label(reply_mode)}",
        "account_id": account["id"],
        "total": len(selected_rows),
        "reply_mode": reply_mode,
    }


@router.post("/stranger-messages/stop")
async def douyin_stop_stranger_messages():
    global douyin_stranger_message_stop_requested, douyin_stranger_message_background_task

    reconcile_douyin_stranger_message_runtime_state()
    if not douyin_stranger_message_running:
        douyin_stranger_message_stop_requested = False
        douyin_stranger_message_background_task = None
        return {"code": 200, "msg": "当前没有正在执行的私信引流任务。"}

    douyin_stranger_message_stop_requested = True
    douyin_log("[抖音私信引流] 已请求停止", "warning")
    return {"code": 200, "msg": "已请求停止私信引流任务。"}


@router.post("/start")
async def douyin_start_tasks(http_request: Request = None, request: Optional[dict] = None):
    nurture_conflict = build_douyin_nurture_conflict("执行评论采集")
    if nurture_conflict:
        return nurture_conflict
    set_douyin_ai_auth_token_from_request(http_request)
    global douyin_running, douyin_stop_requested, douyin_background_task
    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()

    if douyin_running:
        return {"code": 400, "msg": "评论采集任务已在执行中。"}
    if douyin_video_comment_running:
        return {"code": 400, "msg": "视频评论任务正在执行，请先停止后再开始评论采集。"}
    if douyin_mention_comment_running:
        return {"code": 400, "msg": "评论@精准客户任务正在执行，请先停止后再开始评论采集。"}
    if douyin_follow_comment_running:
        return {"code": 400, "msg": "关注评论任务正在执行，请先停止后再开始评论采集。"}
    if douyin_interaction_running:
        return {"code": 400, "msg": "私信任务正在执行，请先停止后再开始评论采集。"}
    if douyin_group_member_running:
        return {"code": 400, "msg": "群成员提取任务正在执行，请先停止后再开始评论采集。"}
    if douyin_stranger_message_running:
        return {"code": 400, "msg": "私信引流任务正在执行，请先停止后再开始评论采集。"}
    if not douyin_tasks:
        return {"code": 400, "msg": "请先准备抖音任务。"}

    payload = request if isinstance(request, dict) else {}
    raw_selected_ids = payload.get("selected_task_ids")
    comment_scroll_rounds = max(20, min(int(payload.get("comment_scroll_rounds", 300) or 300), 300))
    comment_max_comments = max(20, min(int(payload.get("comment_max_comments", 500) or 500), 500))
    collection_mode = normalize_douyin_collection_mode(payload.get("collection_mode"))
    force_recollect = bool(payload.get("force_recollect"))
    selected_task_ids: Optional[Set[int]] = None
    if raw_selected_ids is not None:
        if not isinstance(raw_selected_ids, list):
            return {"code": 400, "msg": "selected_task_ids must be a list"}
        selected_task_ids = {int(task_id) for task_id in raw_selected_ids}
        if not selected_task_ids:
            return {"code": 400, "msg": "请先勾选至少一个任务。"}

        existing_ids = {int(task.get("id", 0) or 0) for task in douyin_tasks}
        missing_ids = sorted(selected_task_ids - existing_ids)
        if missing_ids:
            return {"code": 400, "msg": "任务不存在：" + ', '.join(str(task_id) for task_id in missing_ids)}

    allow_completed_recollect = bool(force_recollect and selected_task_ids)
    runnable_tasks = get_runnable_douyin_tasks(
        selected_task_ids,
        include_completed_with_comments=allow_completed_recollect,
    )
    if not runnable_tasks:
        return {"code": 400, "msg": "没有可执行的采集任务，已完成任务会自动跳过。"}
    skipped_completed = (
        max(0, len(selected_task_ids or set()) - len(runnable_tasks))
        if selected_task_ids and not allow_completed_recollect
        else 0
    )
    recollect_completed = 0
    if allow_completed_recollect:
        for task in runnable_tasks:
            existing_comments = task.get("all_comments", []) or []
            existing_comment_count = max(
                int(task.get("comment_count", 0) or 0),
                len(existing_comments) if isinstance(existing_comments, list) else 0,
            )
            if task.get("status") == "completed" and existing_comment_count > 0:
                recollect_completed += 1

    config = load_global_config()
    accounts = get_online_douyin_accounts(config)
    if not accounts:
        return {
            "code": 400,
            "type": "no_online_account",
            "msg": "当前没有可用的抖音在线账号，请先登录。",
        }

    douyin_running = True
    douyin_stop_requested = False
    douyin_background_task = asyncio.create_task(
        run_douyin_tasks(
            selected_task_ids,
            comment_scroll_rounds=comment_scroll_rounds,
            comment_max_comments=comment_max_comments,
            collection_mode=collection_mode,
            force_recollect=allow_completed_recollect,
        )
    )
    preferred_account_id = accounts[0]["id"] if accounts else None
    account_ids = [account["id"] for account in accounts[: max(1, min(len(accounts), len(runnable_tasks)))]]
    douyin_log(
        f"[抖音评论采集] 已启动，并发账号 {', '.join(str(account_id) for account_id in account_ids)}，共 {len(runnable_tasks)} 条任务，模式 {douyin_collection_mode_label(collection_mode)}，滚动 {comment_scroll_rounds} 轮，最多采集 {comment_max_comments} 条评论"
        + (f"，重新采集已完成任务 {recollect_completed} 条" if recollect_completed else "")
        + (f"，跳过已有客户数据的历史完成任务 {skipped_completed} 条" if skipped_completed else ""),
        "success",
    )
    return {
        "code": 200,
        "msg": (
            f"评论采集任务已启动，使用 {len(account_ids)} 个账号并发，共 {len(runnable_tasks)} 条任务，模式 {douyin_collection_mode_label(collection_mode)}"
            + (f"，优先账号 {preferred_account_id}" if preferred_account_id else "")
            + (f"，将重新采集 {recollect_completed} 条已完成任务" if recollect_completed else "")
            + (f"，已跳过 {skipped_completed} 条已有客户数据的历史完成任务" if skipped_completed else "")
        ),
        "selected_count": len(runnable_tasks),
        "skipped_completed": skipped_completed,
        "account_id": preferred_account_id,
        "account_ids": account_ids,
        "comment_scroll_rounds": comment_scroll_rounds,
        "comment_max_comments": comment_max_comments,
        "collection_mode": collection_mode,
    }


@router.post("/tasks/delete")
async def douyin_delete_tasks(request: dict):
    global douyin_tasks
    reconcile_douyin_runtime_state()
    reconcile_douyin_video_comment_runtime_state()
    reconcile_douyin_mention_comment_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    reconcile_douyin_interaction_runtime_state()
    reconcile_douyin_group_member_runtime_state()
    reconcile_douyin_stranger_message_runtime_state()

    raw_ids = request.get("task_ids", []) if isinstance(request, dict) else []
    if not isinstance(raw_ids, list):
        return {"code": 400, "msg": "task_ids 必须是数组。"}
    if not raw_ids:
        return {"code": 400, "msg": "请至少提供一个任务 ID。"}
    if douyin_running or douyin_video_comment_running or douyin_mention_comment_running or douyin_follow_comment_running or douyin_interaction_running or douyin_group_member_running or douyin_stranger_message_running:
        return {"code": 400, "msg": "当前有任务正在执行，不能删除任务。"}

    task_ids = {int(task_id) for task_id in raw_ids}
    matched_tasks = [task for task in douyin_tasks if int(task.get("id", 0) or 0) in task_ids]
    if not matched_tasks:
        return {"code": 404, "msg": "未找到匹配的任务。"}

    processing_ids = [int(task.get("id", 0) or 0) for task in matched_tasks if task.get("status") == "processing"]
    if processing_ids:
        return {
            "code": 400,
            "msg": f"任务 {', '.join(str(task_id) for task_id in processing_ids)} 仍在采集中。",
        }

    before_total = len(douyin_tasks)
    douyin_tasks = [task for task in douyin_tasks if int(task.get("id", 0) or 0) not in task_ids]
    deleted_count = before_total - len(douyin_tasks)
    save_douyin_tasks_state()
    douyin_log(f"[抖音任务] 已删除 {deleted_count} 条任务", "warning")
    return {
        "code": 200,
        "msg": f"已删除 {deleted_count} 条抖音任务。",
        "deleted": deleted_count,
        "total": len(douyin_tasks),
    }


@router.post("/tasks/{task_id}/delete")
async def douyin_delete_task(task_id: int):
    return await douyin_delete_tasks({"task_ids": [task_id]})


@router.post("/stop")
async def douyin_stop_tasks():
    global douyin_running, douyin_stop_requested, douyin_background_task
    reconcile_douyin_runtime_state()
    reconcile_douyin_follow_comment_runtime_state()
    if not douyin_running:
        douyin_stop_requested = False
        douyin_background_task = None
        return {"code": 200, "msg": "当前没有正在执行的评论采集任务。"}
    douyin_stop_requested = True
    douyin_log("[抖音评论采集] 已请求停止", "warning")
    return {"code": 200, "msg": "已请求停止评论采集任务。"}


@router.post("/export")
async def douyin_export_results():
    try:
        file = export_task_results()
        return {"code": 200, "msg": "Export successful", "file": file}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}")
