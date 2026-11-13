"""视频号获客 API：本地微信 UI 自动化，不接 TikHub。"""
from __future__ import annotations

import csv
import json
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from douyin_origin.state_store import RuntimeStateStore  # noqa: E402

from . import channels_ui  # noqa: E402
from .runtime import EXPORT_DIR, STATE_DB_FILE  # noqa: E402

router = APIRouter(prefix="/api/channels", tags=["channels"])
store = RuntimeStateStore(STATE_DB_FILE)
_lock = threading.RLock()

BLOB_SESSIONS = "channels_ui_search_sessions"
BLOB_TASKS = "channels_ui_tasks"
BLOB_CUSTOMERS_ALL = "channels_ui_customers_all"
BLOB_CUSTOMERS_PRECISE = "channels_ui_customers_precise"
BLOB_ACCOUNTS = "channels_ui_accounts"
BLOB_SETTINGS = "channels_ui_settings"
BLOB_LOGS = "channels_ui_logs"

DEFAULT_SETTINGS: Dict[str, Any] = {
    "max_results": 20,
    "max_comments": 100,
    "intent_terms": "多少钱,价格,怎么买,哪里买,咨询,加盟,合作,联系,微信,电话,报名,领取,求推荐,地址,能不能,可以吗,加我,需要,报价,方案,私信",
}


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load(key: str, default):
    try:
        value = store.load_blob_json(key, default=default)
        return default if value is None else value
    except Exception:
        return default


def _save(key: str, value) -> None:
    with _lock:
        store.save_blob_json(key, value)


def _settings() -> Dict[str, Any]:
    data = dict(DEFAULT_SETTINGS)
    saved = _load(BLOB_SETTINGS, {})
    if isinstance(saved, dict):
        data.update({k: v for k, v in saved.items() if v is not None})
    return data


def _log(message: str, level: str = "info", **extra) -> None:
    rows = _load(BLOB_LOGS, [])
    if not isinstance(rows, list):
        rows = []
    rows.append({"time": _now_text(), "level": level, "message": str(message)[:1000], **extra})
    _save(BLOB_LOGS, rows[-500:])


def _next_id(rows: List[Dict[str, Any]]) -> int:
    ids = [int(row.get("id") or 0) for row in rows if isinstance(row, dict)]
    return (max(ids) if ids else 0) + 1


async def _json_body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _ok(data: Any = None, message: str = "") -> Dict[str, Any]:
    return {"code": 200, "data": data, "message": message}


def _error(message: str, code: int = 400, **extra) -> Dict[str, Any]:
    return {"code": code, "message": message, **extra}


def _score_intent(text: str, terms: List[str]) -> int:
    value = str(text or "")
    score = 0
    for term in terms:
        if term and term in value:
            score += 18
    if "?" in value or "？" in value:
        score += 12
    if re.search(r"(1[3-9]\d{9}|微信|vx|VX|wx|WX|电话|溦|薇|加V|加v)", value):
        score += 18
    if re.search(r"[A-Za-z]{3,}\d{2,}", value):
        score += 12
    return max(0, min(100, score))


def _customer_key(row: Dict[str, Any]) -> str:
    return str(row.get("customer_id") or row.get("nickname") or row.get("username") or "")


def _sync_customers() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    tasks = _load(BLOB_TASKS, [])
    settings = _settings()
    terms = [t.strip() for t in str(settings.get("intent_terms") or "").split(",") if t.strip()]
    all_rows: Dict[str, Dict[str, Any]] = {}
    precise: Dict[str, Dict[str, Any]] = {}
    for task in tasks if isinstance(tasks, list) else []:
        for comment in task.get("comments") or []:
            key = _customer_key(comment)
            if not key:
                continue
            row = {
                "customer_id": key,
                "nickname": comment.get("nickname") or "",
                "username": comment.get("username") or "",
                "content": comment.get("content") or "",
                "like_count": int(comment.get("like_count") or comment.get("like") or 0),
                "video_title": task.get("title") or "",
                "keyword": task.get("keyword") or "",
                "source": task.get("source") or "video_comment",
                "intent_score": _score_intent(comment.get("content") or "", terms),
            }
            old = all_rows.get(key)
            if not old or row["intent_score"] > int(old.get("intent_score") or 0):
                all_rows[key] = row
    for row in _load(BLOB_CUSTOMERS_PRECISE, []):
        if isinstance(row, dict) and _customer_key(row):
            precise[_customer_key(row)] = row
    for key, row in all_rows.items():
        if int(row.get("intent_score") or 0) >= 36:
            precise.setdefault(key, row)
    for row in _load(BLOB_ACCOUNTS, []):
        if not isinstance(row, dict):
            continue
        contacts = row.get("contacts") or {}
        has_contact = any(contacts.get(k) for k in ("phones", "wechats", "qqs"))
        if not has_contact:
            continue
        key = f"account:{row.get('name') or ''}"
        customer = {
            "customer_id": key,
            "nickname": row.get("name") or "",
            "username": "",
            "content": row.get("bio") or "",
            "like_count": 0,
            "video_title": "",
            "keyword": row.get("keyword") or "",
            "source": "account_bio",
            "intent_score": 80,
            "contacts": contacts,
        }
        all_rows.setdefault(key, customer)
        precise.setdefault(key, customer)
    return list(all_rows.values()), list(precise.values())


def _persist_customers() -> None:
    all_rows, precise = _sync_customers()
    _save(BLOB_CUSTOMERS_ALL, all_rows)
    _save(BLOB_CUSTOMERS_PRECISE, precise)


@router.get("/origin-status")
async def origin_status():
    return _ok({"ready": True, "mode": "local_wechat_ui"})


@router.get("/status")
async def status():
    return _ok(channels_ui.status())


@router.post("/open")
async def open_channels():
    try:
        return _ok(channels_ui.open_channels())
    except Exception as exc:
        return _error(str(exc)[:300], 500)


@router.get("/config")
async def get_config():
    return _ok(_settings())


@router.post("/config")
async def save_config(request: Request):
    body = await _json_body(request)
    current = _settings()
    for key in DEFAULT_SETTINGS:
        if body.get(key) is not None:
            current[key] = body[key]
    _save(BLOB_SETTINGS, current)
    _log("保存视频号获客配置")
    return _ok(current)


@router.get("/logs")
async def logs(limit: int = 200):
    rows = _load(BLOB_LOGS, [])
    return _ok(rows[-max(1, min(int(limit or 200), 500)):])


@router.get("/stats")
async def stats():
    tasks = _load(BLOB_TASKS, [])
    all_rows = _load(BLOB_CUSTOMERS_ALL, [])
    precise = _load(BLOB_CUSTOMERS_PRECISE, [])
    accounts = _load(BLOB_ACCOUNTS, [])
    return _ok({
        "tasks": len(tasks) if isinstance(tasks, list) else 0,
        "comments": sum(len(t.get("comments") or []) for t in tasks if isinstance(t, dict)),
        "customers": len(all_rows) if isinstance(all_rows, list) else 0,
        "precise_customers": len(precise) if isinstance(precise, list) else 0,
        "accounts": len(accounts) if isinstance(accounts, list) else 0,
    })

@router.post("/search/videos")
async def search_videos_api(request: Request):
    import asyncio

    body = await _json_body(request)
    keyword = str(body.get("keyword") or "").strip()
    if not keyword:
        return _error("请输入搜索关键词")
    settings = _settings()
    max_results = int(body.get("max_results") or settings.get("max_results") or 20)
    try:
        result = await asyncio.to_thread(
            channels_ui.search_videos,
            keyword,
            max_results=max_results,
            max_scroll_rounds=int(body.get("max_scroll_rounds") or 6),
        )
    except Exception as exc:
        _log(f"视频搜索失败：{exc}", level="error", keyword=keyword)
        return _error(str(exc)[:300], 500)
    sessions = _load(BLOB_SESSIONS, [])
    session = {
        "id": _next_id(sessions),
        "keyword": keyword,
        "created_at": _now_text(),
        "items": result.get("items") or [],
    }
    sessions.append(session)
    _save(BLOB_SESSIONS, sessions)
    _log(f"视频搜索：{keyword}，{len(session['items'])} 条")
    return _ok({"session": session, "items": session["items"]})


@router.get("/search/sessions")
async def search_sessions():
    rows = _load(BLOB_SESSIONS, [])
    return _ok([{k: v for k, v in row.items() if k != "items"} for row in reversed(rows[-50:])])


@router.post("/search/accounts")
async def search_accounts_api(request: Request):
    import asyncio

    body = await _json_body(request)
    keyword = str(body.get("keyword") or "").strip()
    if not keyword:
        return _error("请输入搜索关键词")
    max_results = int(body.get("max_results") or 20)
    try:
        result = await asyncio.to_thread(
            channels_ui.search_accounts,
            keyword,
            max_results=max_results,
            max_scroll_rounds=int(body.get("max_scroll_rounds") or 6),
        )
    except Exception as exc:
        _log(f"账号搜索失败：{exc}", level="error", keyword=keyword)
        return _error(str(exc)[:300], 500)
    rows = _load(BLOB_ACCOUNTS, [])
    existing = {row.get("key"): row for row in rows if isinstance(row, dict)}
    for row in result.get("items") or []:
        row["keyword"] = keyword
        row["created_at"] = _now_text()
        existing[row.get("key")] = row
    _save(BLOB_ACCOUNTS, list(existing.values()))
    _persist_customers()
    _log(f"账号搜索：{keyword}，{len(result.get('items') or [])} 个账号")
    return _ok({"items": result.get("items") or [], "count": result.get("count") or 0})


@router.get("/accounts")
async def list_accounts():
    return _ok(_load(BLOB_ACCOUNTS, []))


@router.post("/accounts/import")
async def import_accounts(request: Request):
    body = await _json_body(request)
    rows = _load(BLOB_ACCOUNTS, [])
    existing = {row.get("key"): row for row in rows if isinstance(row, dict)}
    added = 0
    for row in body.get("accounts") or []:
        if not isinstance(row, dict):
            continue
        key = row.get("key") or f"{row.get('name')}|{str(row.get('bio') or '')[:80]}"
        row["key"] = key
        row.setdefault("created_at", _now_text())
        if key not in existing:
            added += 1
        existing[key] = row
    _save(BLOB_ACCOUNTS, list(existing.values()))
    _persist_customers()
    return _ok({"added": added, "total": len(existing)})


def _task_lite(task: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in task.items() if k != "comments"}


@router.get("/tasks")
async def list_tasks():
    return _ok([_task_lite(t) for t in reversed(_load(BLOB_TASKS, [])[-100:])])


@router.get("/tasks/{task_id}")
async def get_task(task_id: int):
    task = next((t for t in _load(BLOB_TASKS, []) if int(t.get("id") or 0) == int(task_id)), None)
    return _ok(task) if task else _error("任务不存在", 404)


@router.post("/tasks/from-search")
async def tasks_from_search(request: Request):
    body = await _json_body(request)
    session_id = int(body.get("session_id") or 0)
    keys = {str(v) for v in (body.get("keys") or body.get("video_ids") or [])}
    sessions = _load(BLOB_SESSIONS, [])
    session = next((row for row in sessions if int(row.get("id") or 0) == session_id), None)
    if not session:
        return _error("搜索会话不存在")
    videos = [row for row in session.get("items") or [] if str(row.get("key")) in keys]
    if not videos:
        return _error("请先选择视频")
    max_comments = int(body.get("max_comments") or _settings().get("max_comments") or 100)
    tasks = _load(BLOB_TASKS, [])
    created = []
    for video in videos:
        if any(t.get("card_key") == video.get("key") for t in tasks):
            continue
        task = {
            "id": _next_id(tasks),
            "session_id": session_id,
            "keyword": session.get("keyword") or "",
            "card_key": video.get("key") or "",
            "title": video.get("title") or "",
            "author": video.get("author") or "",
            "date": video.get("date") or "",
            "duration": video.get("duration") or "",
            "like": video.get("like") or "",
            "target_count": max_comments,
            "collected": 0,
            "status": "pending",
            "created_at": _now_text(),
            "updated_at": _now_text(),
            "comments": [],
            "source": "video_search",
        }
        tasks.append(task)
        created.append(_task_lite(task))
    _save(BLOB_TASKS, tasks)
    _log(f"创建评论任务 {len(created)} 个")
    return _ok({"created": created})


@router.post("/tasks/{task_id}/run")
async def run_task(task_id: int, request: Request):
    import asyncio

    body = await _json_body(request)
    tasks = _load(BLOB_TASKS, [])
    task = next((t for t in tasks if int(t.get("id") or 0) == int(task_id)), None)
    if not task:
        return _error("任务不存在", 404)
    task["status"] = "running"
    task["updated_at"] = _now_text()
    _save(BLOB_TASKS, tasks)
    try:
        result = await asyncio.to_thread(
            channels_ui.collect_comments_for_card,
            task.get("keyword") or "",
            task.get("card_key") or "",
            max_comments=int(body.get("max_comments") or task.get("target_count") or 100),
        )
        comments = []
        for row in result.get("items") or []:
            comments.append({**row, "video_title": task.get("title") or "", "keyword": task.get("keyword") or ""})
        task["comments"] = comments
        task["collected"] = len(comments)
        task["status"] = "done"
        task["error"] = ""
        if result.get("detail"):
            task["detail"] = result["detail"]
    except Exception as exc:
        task["status"] = "failed"
        task["error"] = str(exc)[:300]
    task["updated_at"] = _now_text()
    _save(BLOB_TASKS, tasks)
    _persist_customers()
    _log(f"评论任务 {task_id}：{task.get('collected', 0)} 条，状态 {task.get('status')}")
    return _ok({"task": _task_lite(task)}) if task.get("status") == "done" else _error(task.get("error") or "采集失败", 500)


@router.post("/tasks/{task_id}/refilter-customers")
async def refilter_customers(task_id: int, request: Request):
    body = await _json_body(request)
    prompt = str(body.get("prompt") or "")
    terms = [t.strip() for t in re.split(r"[,，、;；\s]+", prompt) if t.strip()]
    settings = _settings()
    base = [t.strip() for t in str(settings.get("intent_terms") or "").split(",") if t.strip()]
    if terms:
        settings["intent_terms"] = ",".join(dict.fromkeys(base + terms))
        _save(BLOB_SETTINGS, settings)
    _persist_customers()
    return _ok({"terms": terms})

@router.get("/customer-pools")
async def customer_pools():
    _persist_customers()
    all_rows = _load(BLOB_CUSTOMERS_ALL, [])
    precise = _load(BLOB_CUSTOMERS_PRECISE, [])
    return _ok({"all": all_rows, "precise": precise, "all_count": len(all_rows), "precise_count": len(precise)})


@router.post("/customers/delete")
async def delete_customers(request: Request):
    body = await _json_body(request)
    keys = {str(v) for v in (body.get("ids") or [])}
    if not keys:
        return _error("请选择客户")
    tasks = _load(BLOB_TASKS, [])
    for task in tasks:
        task["comments"] = [c for c in task.get("comments") or [] if _customer_key(c) not in keys]
    _save(BLOB_TASKS, tasks)
    precise = [r for r in _load(BLOB_CUSTOMERS_PRECISE, []) if _customer_key(r) not in keys]
    accounts = [r for r in _load(BLOB_ACCOUNTS, []) if f"account:{r.get('name') or ''}" not in keys]
    _save(BLOB_CUSTOMERS_PRECISE, precise)
    _save(BLOB_ACCOUNTS, accounts)
    _persist_customers()
    return _ok({"deleted": len(keys)})


@router.post("/customers/add-precise")
async def add_precise(request: Request):
    body = await _json_body(request)
    keys = {str(v) for v in (body.get("ids") or [])}
    all_rows = _load(BLOB_CUSTOMERS_ALL, [])
    precise = {_customer_key(r): r for r in _load(BLOB_CUSTOMERS_PRECISE, [])}
    added = 0
    for row in all_rows:
        if _customer_key(row) in keys:
            precise[_customer_key(row)] = row
            added += 1
    _save(BLOB_CUSTOMERS_PRECISE, list(precise.values()))
    return _ok({"added": added})


def _write_csv(filename: str, headers: List[str], rows: List[List[Any]]) -> Tuple[Path, str]:
    path = EXPORT_DIR / filename
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        writer.writerows(rows)
    return path, f"/api/channels/download/{filename}"


@router.get("/export/customers")
async def export_customers(precise: int = 0):
    rows = _load(BLOB_CUSTOMERS_PRECISE if precise else BLOB_CUSTOMERS_ALL, [])
    filename = f"channels_customers_{'precise' if precise else 'all'}_{int(time.time())}.csv"
    path, url = _write_csv(
        filename,
        ["昵称", "评论/简介", "关键词", "视频", "来源", "意向分", "手机", "微信", "QQ"],
        [
            [
                row.get("nickname", ""), row.get("content", ""), row.get("keyword", ""),
                row.get("video_title", ""), row.get("source", ""), row.get("intent_score", 0),
                ",".join((row.get("contacts") or {}).get("phones") or []),
                ",".join((row.get("contacts") or {}).get("wechats") or []),
                ",".join((row.get("contacts") or {}).get("qqs") or []),
            ]
            for row in rows if isinstance(row, dict)
        ],
    )
    return _ok({"filename": filename, "path": str(path), "download_url": url})


@router.get("/export/accounts")
async def export_accounts():
    rows = _load(BLOB_ACCOUNTS, [])
    filename = f"channels_accounts_{int(time.time())}.csv"
    path, url = _write_csv(
        filename,
        ["账号", "认证", "简介", "视频数", "更新时间", "手机", "微信", "QQ"],
        [
            [
                row.get("name", ""), row.get("cert", ""), row.get("bio", ""),
                row.get("video_count", ""), row.get("updated", ""),
                ",".join((row.get("contacts") or {}).get("phones") or []),
                ",".join((row.get("contacts") or {}).get("wechats") or []),
                ",".join((row.get("contacts") or {}).get("qqs") or []),
            ]
            for row in rows if isinstance(row, dict)
        ],
    )
    return _ok({"filename": filename, "path": str(path), "download_url": url})


@router.get("/download/{filename}")
async def download(filename: str):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "", filename)
    path = EXPORT_DIR / safe
    if not path.exists():
        return _error("文件不存在", 404)
    return FileResponse(str(path), filename=safe, media_type="text/csv")


@router.get("/inbox/status")
async def inbox_status():
    return _ok({
        "supported": False,
        "message": "本地视频号 UI 没有私信入口：作者主页只有关注，个人中心没有消息中心。可以回复已有微信会话，但不能主动冷私信。",
    })


@router.post("/inbox/send")
async def inbox_send():
    return _error("本地视频号 UI 不支持主动私信；请先用微信聊天回复已有会话。", 501, supported=False)


@router.post("/page-log")
async def page_log(request: Request):
    body = await _json_body(request)
    _log(body.get("message") or body.get("msg") or "", level=str(body.get("level") or "info"))
    return _ok({})


@router.get("/capabilities")
async def capabilities():
    return _ok({
        "items": [
            {"key": "video_search", "name": "视频搜索", "supported": True, "note": "本地微信视频号搜索框 + 结果卡片 UIA"},
            {"key": "video_comments", "name": "视频评论", "supported": True, "note": "评论面板 UIA + 滚动加载"},
            {"key": "account_search", "name": "账号搜索", "supported": True, "note": "账号 tab + 账号卡片 UIA"},
            {"key": "account_contacts", "name": "账号简介联系方式", "supported": True, "note": "手机号/微信号/QQ 正则提取"},
            {"key": "private_message", "name": "主动私信", "supported": False, "note": "本地视频号 UI 无入口，只能回复已有微信会话"},
            {"key": "follow_like_comment", "name": "关注/点赞/评论", "supported": False, "note": "本轮只做只读采集，不执行写操作"},
            {"key": "my_comments", "name": "我的评论区", "supported": False, "note": "当前账号无作品，且本地 UI 无统一评论管理入口"},
        ]
    })
