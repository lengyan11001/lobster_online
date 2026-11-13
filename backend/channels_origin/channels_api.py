"""视频号获客后端。

能力边界（2026-11 实测）：
- 公开数据：TikHub 视频号搜索 / 视频详情 / 一级评论 / 账号作品，全部可用。
- 登录态：视频号助手 https://channels.weixin.qq.com/platform 扫码登录可用。
- 自有内容：视频号助手 /platform/comment 评论区、/platform/private_msg 私信可用。
- 不能照搬抖音的：关注、点赞、给别人的视频评论、群成员提取、主动冷私信、养号。
"""
from __future__ import annotations

import asyncio
import csv
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

import channels_browser  # noqa: E402
import tikhub_channels  # noqa: E402
from runtime import EXPORT_DIR, STATE_DB_FILE  # noqa: E402

router = APIRouter(prefix="/api/channels", tags=["channels"])
store = RuntimeStateStore(STATE_DB_FILE)
_lock = threading.RLock()

BLOB_SESSIONS = "channels_search_sessions"
BLOB_TASKS = "channels_tasks"
BLOB_CUSTOMERS_ALL = "channels_customers_all"
BLOB_CUSTOMERS_PRECISE = "channels_customers_precise"
BLOB_MONITOR_TARGETS = "channels_monitor_targets"
BLOB_MONITOR_VIDEOS = "channels_monitor_videos"
BLOB_SETTINGS = "channels_settings"
BLOB_INBOX = "channels_inbox_conversations"
BLOB_LOGS = "channels_logs"
BLOB_PLANS = "channels_scheduler_plans"

DEFAULT_SETTINGS: Dict[str, Any] = {
    "tikhub_api_key": "",
    "max_results": 20,
    "max_comments": 100,
    "default_sort": "hot",
    "default_publish_time": "week",
    "enrich_limit": 0,
    "intent_terms": "多少钱,价格,怎么买,哪里买,咨询,加盟,合作,联系,微信,电话,报名,领取,求推荐,地址,能不能,可以吗,加我,需要,报价,方案,私信",
    "send_min_interval": 6,
    "send_max_interval": 12,
}

UNSUPPORTED = {
    "follow-comment": "视频号没有公开的“关注并评论”Web/API。抖音靠网页自动化，视频号只能走微信客户端 UI 自动化，暂不提供。",
    "video-comment": "视频号助手只能管理自己作品的评论，不能给别人的视频公开评论。",
    "mention-comment": "视频号没有公开的评论区 @ 客户接口，暂不提供。",
    "group-members": "视频号没有群成员列表接口；群成员提取需微信客户端 UI 自动化，暂不提供。",
    "nurture": "视频号没有公开的养号接口；模拟浏览/点赞/关注需微信客户端 UI 自动化，暂不提供。",
    "stranger-messages": "视频号不能像抖音那样主动给陌生人发私信；只能回复已进入私信会话的用户。",
}


def _now() -> int:
    return int(time.time())


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


def _api_key() -> str:
    return str(_settings().get("tikhub_api_key") or "").strip()


def _mask_key(key: str) -> str:
    key = str(key or "")
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * max(4, len(key) - 8) + key[-4:]


def _public_settings() -> Dict[str, Any]:
    data = _settings()
    key = str(data.pop("tikhub_api_key", "") or "")
    try:
        effective = tikhub_channels.resolve_api_key(key)
        configured = True
    except Exception:
        effective = ""
        configured = False
    data["tikhub_configured"] = configured
    data["tikhub_key_masked"] = _mask_key(effective)
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


def _clean_text(value: Any, limit: int = 500) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    return text.replace("\n", " ").strip()[:limit]


def _video_key(video: Dict[str, Any]) -> str:
    return str(video.get("video_id") or video.get("export_id") or video.get("doc_id") or "")


def _customer_key(row: Dict[str, Any]) -> str:
    return str(row.get("username") or row.get("nickname") or row.get("comment_id") or "")


def _intent_score(text: str, terms: List[str]) -> int:
    text = str(text or "")
    score = 0
    for term in terms:
        term = str(term or "").strip()
        if term and term in text:
            score += 18
    if any(ch in text for ch in ("?", "？")):
        score += 12
    if re.search(r"(\d{5,}|微信|vx|VX|wx|WX|电话|溦|薇|威|V信|加V|加v)", text):
        score += 18
    if re.search(r"[A-Za-z]{3,}\d{2,}", text):
        score += 12
    return max(0, min(100, score))


def _capabilities() -> List[Dict[str, Any]]:
    return [
        {"key": "public_search", "name": "关键词搜视频", "supported": True, "channel": "TikHub / 微信搜一搜", "note": "实测返回视频列表"},
        {"key": "video_detail", "name": "视频详情/作者", "supported": True, "channel": "TikHub", "note": "exportId + feedNonceId 换 objectId、username"},
        {"key": "video_comments", "name": "一级评论采集", "supported": True, "channel": "TikHub", "note": "实测单条视频 26 条评论"},
        {"key": "account_videos", "name": "同行账号作品", "supported": True, "channel": "TikHub", "note": "按 username 拉主页作品"},
        {"key": "own_comments", "name": "我的评论区", "supported": True, "channel": "视频号助手 /platform/comment", "note": "扫码登录后可用"},
        {"key": "private_reply", "name": "私信回复", "supported": True, "channel": "视频号助手 /platform/private_msg", "note": "只能回复已有会话"},
        {"key": "cold_dm", "name": "主动冷私信", "supported": False, "channel": "无公开接口", "note": "需微信客户端 UI 自动化"},
        {"key": "follow_like_comment", "name": "关注/点赞/评论别人", "supported": False, "channel": "无公开接口", "note": "视频号助手只能管自己作品"},
        {"key": "group_members", "name": "群成员提取", "supported": False, "channel": "无公开接口", "note": "需微信客户端 UI 自动化"},
        {"key": "nurture", "name": "账号养号", "supported": False, "channel": "无公开接口", "note": "需客户端自动化"},
    ]


@router.get("/origin-status")
async def origin_status():
    return _ok({"ready": True, "platform": "wechat_channels"})


@router.get("/capabilities")
async def capabilities():
    return _ok({"items": _capabilities(), "checked_at": _now_text()})


@router.get("/config")
async def get_config():
    return _ok(_public_settings())


@router.post("/config")
async def save_config(request: Request):
    body = await _json_body(request)
    current = _settings()
    for key, value in body.items():
        if key in DEFAULT_SETTINGS and value is not None:
            current[key] = value
    _save(BLOB_SETTINGS, current)
    _log("保存视频号获客配置")
    return _ok(_public_settings())


@router.get("/local-settings")
async def get_local_settings():
    return _ok(_public_settings())


@router.post("/local-settings")
async def save_local_settings(request: Request):
    return await save_config(request)


@router.get("/stats")
async def stats():
    sessions = _load(BLOB_SESSIONS, [])
    tasks = _load(BLOB_TASKS, [])
    all_rows = _load(BLOB_CUSTOMERS_ALL, [])
    precise = _load(BLOB_CUSTOMERS_PRECISE, [])
    conversations = _load(BLOB_INBOX, [])
    return _ok({
        "search_sessions": len(sessions) if isinstance(sessions, list) else 0,
        "tasks": len(tasks) if isinstance(tasks, list) else 0,
        "videos": sum(len(t.get("comments") or []) for t in tasks if isinstance(t, dict)),
        "customers": len(all_rows) if isinstance(all_rows, list) else 0,
        "precise_customers": len(precise) if isinstance(precise, list) else 0,
        "conversations": len(conversations) if isinstance(conversations, list) else 0,
    })


@router.get("/logs")
async def logs(limit: int = 200):
    rows = _load(BLOB_LOGS, [])
    return _ok(rows[-max(1, min(int(limit or 200), 500)):])


@router.post("/page-log")
async def page_log(request: Request):
    body = await _json_body(request)
    _log(body.get("message") or body.get("msg") or "", level=str(body.get("level") or "info"))
    return _ok({})

# ── 账号 / 登录 ────────────────────────────────────────────────────────────

@router.get("/account-nurture/status")
async def nurture_status():
    return _ok({"running": False, "supported": False, "message": UNSUPPORTED["nurture"]})


@router.post("/account-nurture/start")
@router.post("/account-nurture/stop")
async def nurture_start_stop():
    return _error(UNSUPPORTED["nurture"], 501, supported=False)


@router.get("/account/status")
async def account_status():
    return _ok(await channels_browser.login_status())


@router.post("/account/{account_id}/login")
@router.post("/account/{account_id}/view")
async def account_login(account_id: str):
    result = await channels_browser.open_login()
    _log("打开视频号助手登录页", account_id=account_id)
    return _ok(result) if result.get("ok") else _error(result.get("error") or "打开登录页失败")


@router.post("/account/{account_id}/check")
async def account_check(account_id: str):
    return _ok(await channels_browser.login_status())


@router.post("/account/{account_id}/logout")
async def account_logout(account_id: str):
    return _ok({"logged_out": False, "message": "视频号助手不支持接口退出，请在浏览器里退出登录或删除 data/channels_browser 目录。"})


# ── 搜索采集 ──────────────────────────────────────────────────────────────

def _sessions() -> List[Dict[str, Any]]:
    rows = _load(BLOB_SESSIONS, [])
    return rows if isinstance(rows, list) else []


@router.get("/search/sessions")
async def search_sessions():
    rows = _sessions()
    return _ok([{k: v for k, v in row.items() if k != "items"} for row in reversed(rows[-50:])])


@router.post("/search/sessions")
async def save_search_session(request: Request):
    body = await _json_body(request)
    rows = _sessions()
    row = {
        "id": _next_id(rows),
        "keyword": _clean_text(body.get("keyword"), 100),
        "sort": body.get("sort") or "hot",
        "publish_time": body.get("publish_time") or "week",
        "created_at": _now_text(),
        "items": [],
    }
    rows.append(row)
    _save(BLOB_SESSIONS, rows)
    return _ok(row)


@router.post("/search/collect")
async def search_collect(request: Request):
    body = await _json_body(request)
    keyword = _clean_text(body.get("keyword"), 100)
    if not keyword:
        return _error("请输入搜索关键词")
    settings = _settings()
    try:
        result = await asyncio.to_thread(
            tikhub_channels.search_videos,
            keyword,
            sort=str(body.get("sort") or settings.get("default_sort") or "hot"),
            publish_time=str(body.get("publish_time") or settings.get("default_publish_time") or "week"),
            max_results=int(body.get("max_results") or settings.get("max_results") or 20),
            api_key=_api_key(),
        )
    except Exception as exc:
        _log(f"视频号搜索失败：{exc}", level="error", keyword=keyword)
        return _error(str(exc)[:300])

    items = result.get("items") or []
    raw_enrich = body.get("enrich_limit")
    if raw_enrich is None:
        raw_enrich = settings.get("enrich_limit")
    if raw_enrich is None:
        raw_enrich = 0
    enrich_limit = int(raw_enrich)
    detail_errors: List[str] = []
    for item in items[: max(0, enrich_limit)]:
        try:
            detail = await asyncio.to_thread(
                tikhub_channels.video_detail,
                export_id=item.get("export_id", ""),
                object_nonce_id=item.get("object_nonce_id", ""),
                api_key=_api_key(),
            )
            item.update({
                "video_id": str(detail.get("id") or ""),
                "nickname": detail.get("nickname") or item.get("nickname") or "",
                "username": detail.get("username") or "",
                "title": _clean_text(detail.get("title") or item.get("title"), 500),
                "like_count": int(detail.get("like_count") or 0),
                "comment_count": int(detail.get("comment_count") or 0),
                "read_count": int(detail.get("read_count") or 0),
                "cover": detail.get("cover_img_url") or item.get("cover") or "",
                "create_time": int(detail.get("create_time") or item.get("publish_time") or 0),
            })
        except Exception as exc:
            detail_errors.append(str(exc)[:120])

    rows = _sessions()
    session = {
        "id": _next_id(rows),
        "keyword": keyword,
        "sort": body.get("sort") or "hot",
        "publish_time": body.get("publish_time") or "week",
        "created_at": _now_text(),
        "items": items,
        "pages": result.get("pages") or 1,
        "detail_errors": detail_errors,
    }
    rows.append(session)
    _save(BLOB_SESSIONS, rows)
    _log(f"视频号搜索采集：{keyword}，{len(items)} 条视频", keyword=keyword)
    return _ok({"session": session, "items": items})


@router.post("/search/export")
async def search_export(request: Request):
    body = await _json_body(request)
    session_id = int(body.get("session_id") or 0)
    session = next((row for row in _sessions() if int(row.get("id") or 0) == session_id), None)
    if not session:
        return _error("搜索会话不存在")
    rows = session.get("items") or []
    filename = f"channels_search_{session_id}_{int(time.time())}.csv"
    path = EXPORT_DIR / filename
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["标题", "作者", "时长", "点赞", "评论数", "发布时间", "export_id", "object_nonce_id", "username"])
        for row in rows:
            writer.writerow([
                row.get("title", ""), row.get("nickname", ""), row.get("duration", ""),
                row.get("like_count", 0), row.get("comment_count", 0), row.get("date_text", ""),
                row.get("export_id", ""), row.get("object_nonce_id", ""), row.get("username", ""),
            ])
    return _ok({"path": str(path), "filename": filename, "download_url": f"/api/channels/download/{filename}"})


@router.get("/download/{filename}")
async def download(filename: str):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "", filename)
    path = EXPORT_DIR / safe
    if not path.exists():
        return _error("文件不存在", 404)
    return FileResponse(str(path), filename=safe, media_type="text/csv")

# ── 视频评论任务 ──────────────────────────────────────────────────────────

def _tasks() -> List[Dict[str, Any]]:
    rows = _load(BLOB_TASKS, [])
    return rows if isinstance(rows, list) else []


def _task_lite(task: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in task.items() if k != "comments"}


@router.get("/tasks")
async def list_tasks():
    return _ok([_task_lite(t) for t in reversed(_tasks()[-100:])])


@router.get("/tasks-lite")
async def list_tasks_lite():
    return await list_tasks()


@router.get("/tasks/{task_id}")
async def get_task(task_id: int):
    task = next((t for t in _tasks() if int(t.get("id") or 0) == int(task_id)), None)
    return _ok(task) if task else _error("任务不存在", 404)


@router.post("/tasks/from-search")
async def tasks_from_search(request: Request):
    body = await _json_body(request)
    session_id = int(body.get("session_id") or 0)
    selected = {str(v) for v in (body.get("video_ids") or []) if str(v)}
    session = next((row for row in _sessions() if int(row.get("id") or 0) == session_id), None)
    if not session:
        return _error("搜索会话不存在")
    videos = [v for v in session.get("items") or [] if _video_key(v) in selected or str(v.get("export_id")) in selected]
    if not videos:
        return _error("请先选择视频")
    max_comments = int(body.get("max_comments") or _settings().get("max_comments") or 100)
    tasks = _tasks()
    created = []
    for video in videos:
        if not video.get("video_id"):
            try:
                detail = await asyncio.to_thread(
                    tikhub_channels.video_detail,
                    export_id=video.get("export_id", ""),
                    object_nonce_id=video.get("object_nonce_id", ""),
                    api_key=_api_key(),
                )
                video.update({
                    "video_id": str(detail.get("id") or ""),
                    "username": detail.get("username") or "",
                    "nickname": detail.get("nickname") or video.get("nickname") or "",
                    "comment_count": int(detail.get("comment_count") or 0),
                })
            except Exception as exc:
                _log(f"视频详情失败：{exc}", level="error", video=str(video.get("title", ""))[:50])
        if not video.get("video_id"):
            continue
        if any(t.get("video_id") == video.get("video_id") for t in tasks):
            continue
        task = {
            "id": _next_id(tasks),
            "session_id": session_id,
            "keyword": session.get("keyword") or "",
            "video_id": video.get("video_id"),
            "export_id": video.get("export_id") or "",
            "title": video.get("title") or "",
            "nickname": video.get("nickname") or "",
            "username": video.get("username") or "",
            "cover": video.get("cover") or "",
            "like_count": int(video.get("like_count") or 0),
            "comment_count": int(video.get("comment_count") or 0),
            "target_count": max_comments,
            "collected": 0,
            "status": "pending",
            "created_at": _now_text(),
            "updated_at": _now_text(),
            "comments": [],
            "source": "search",
        }
        tasks.append(task)
        created.append(_task_lite(task))
    _save(BLOB_TASKS, tasks)
    _log(f"创建视频评论任务 {len(created)} 个")
    return _ok({"created": created})


async def _run_task(task_id: int, max_comments: Optional[int] = None) -> Dict[str, Any]:
    tasks = _tasks()
    task = next((t for t in tasks if int(t.get("id") or 0) == int(task_id)), None)
    if not task:
        return {"ok": False, "error": "任务不存在"}
    task["status"] = "running"
    task["updated_at"] = _now_text()
    _save(BLOB_TASKS, tasks)
    try:
        result = await asyncio.to_thread(
            tikhub_channels.video_comments,
            str(task.get("video_id") or ""),
            max_comments=int(max_comments or task.get("target_count") or 100),
            api_key=_api_key(),
        )
        comments = []
        for row in result.get("comments") or []:
            comments.append({**row, "video_id": task.get("video_id"), "video_title": task.get("title"),
                             "keyword": task.get("keyword"), "source": task.get("source") or "video_comment"})
        task["comments"] = comments
        task["collected"] = len(comments)
        task["status"] = "done"
        task["error"] = ""
    except Exception as exc:
        task["status"] = "failed"
        task["error"] = str(exc)[:300]
    task["updated_at"] = _now_text()
    _save(BLOB_TASKS, tasks)
    _persist_customers()
    _log(f"评论采集任务 {task_id}：{task.get('collected', 0)} 条，状态 {task.get('status')}")
    return {"ok": task.get("status") == "done", "task": _task_lite(task), "error": task.get("error") or ""}


@router.post("/tasks/{task_id}/run")
async def run_task(task_id: int, request: Request):
    body = await _json_body(request)
    result = await _run_task(task_id, body.get("max_comments"))
    return _ok(result) if result.get("ok") else _error(result.get("error") or "采集失败", 500)


@router.post("/tasks/{task_id}/refilter-customers")
async def refilter(task_id: int, request: Request):
    body = await _json_body(request)
    prompt = str(body.get("prompt") or "")
    terms = [t.strip() for t in re.split(r"[,，、;；\s]+", prompt) if t.strip()]
    settings = _settings()
    base_terms = [t.strip() for t in str(settings.get("intent_terms") or "").split(",") if t.strip()]
    if terms:
        settings["intent_terms"] = ",".join(dict.fromkeys(base_terms + terms))
        _save(BLOB_SETTINGS, settings)
    _persist_customers()
    return _ok({"message": "已按筛选条件重算精准客户", "terms": terms})


@router.post("/tasks/{task_id}/high-intent-users")
async def high_intent(task_id: int):
    _persist_customers()
    task = next((t for t in _tasks() if int(t.get("id") or 0) == int(task_id)), None)
    if not task:
        return _error("任务不存在", 404)
    rows = [r for r in _load(BLOB_CUSTOMERS_PRECISE, []) if r.get("video_id") == task.get("video_id")]
    return _ok(rows)


# ── 客户池 ────────────────────────────────────────────────────────────────

def _sync_customers_from_tasks() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    tasks = _tasks()
    settings = _settings()
    terms = [t.strip() for t in str(settings.get("intent_terms") or "").split(",") if t.strip()]
    all_rows: Dict[str, Dict[str, Any]] = {}
    precise: Dict[str, Dict[str, Any]] = {}
    for task in tasks:
        for row in task.get("comments") or []:
            key = _customer_key(row)
            if not key:
                continue
            customer = {
                "customer_id": key,
                "nickname": row.get("nickname") or "",
                "username": row.get("username") or "",
                "content": row.get("content") or "",
                "like_count": int(row.get("like_count") or 0),
                "create_time": int(row.get("create_time") or 0),
                "comment_id": row.get("comment_id") or "",
                "video_id": task.get("video_id") or "",
                "video_title": task.get("title") or "",
                "keyword": task.get("keyword") or "",
                "source": task.get("source") or "video_comment",
                "intent_score": _intent_score(row.get("content") or "", terms),
            }
            old = all_rows.get(key)
            if not old or customer["intent_score"] > int(old.get("intent_score") or 0):
                all_rows[key] = customer
    for row in _load(BLOB_CUSTOMERS_PRECISE, []):
        if isinstance(row, dict) and _customer_key(row):
            precise[_customer_key(row)] = row
    for key, row in all_rows.items():
        if int(row.get("intent_score") or 0) >= 36:
            precise.setdefault(key, row)
    return list(all_rows.values()), list(precise.values())


def _persist_customers() -> None:
    all_rows, precise = _sync_customers_from_tasks()
    _save(BLOB_CUSTOMERS_ALL, all_rows)
    _save(BLOB_CUSTOMERS_PRECISE, precise)


@router.get("/customer-pools")
async def customer_pools():
    all_rows = _load(BLOB_CUSTOMERS_ALL, [])
    precise = _load(BLOB_CUSTOMERS_PRECISE, [])
    if not isinstance(all_rows, list):
        all_rows = []
    if not isinstance(precise, list):
        precise = []
    if not all_rows and not precise:
        _persist_customers()
        all_rows = _load(BLOB_CUSTOMERS_ALL, [])
        precise = _load(BLOB_CUSTOMERS_PRECISE, [])
    return _ok({"all": all_rows, "precise": precise, "all_count": len(all_rows), "precise_count": len(precise)})


@router.get("/monitor/customer-pools")
async def monitor_customer_pools(target_id: int = 0):
    return await customer_pools()


@router.post("/customers/delete")
async def delete_customers(request: Request):
    body = await _json_body(request)
    keys = {str(v) for v in (body.get("ids") or body.get("customers") or [])}
    if not keys:
        return _error("请选择客户")
    tasks = _tasks()
    for task in tasks:
        task["comments"] = [c for c in task.get("comments") or [] if _customer_key(c) not in keys]
    _save(BLOB_TASKS, tasks)
    precise = [r for r in _load(BLOB_CUSTOMERS_PRECISE, []) if _customer_key(r) not in keys]
    _save(BLOB_CUSTOMERS_PRECISE, precise)
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

# ── 监控同行 ──────────────────────────────────────────────────────────────

@router.get("/monitor/targets")
async def monitor_targets():
    return _ok({"targets": _load(BLOB_MONITOR_TARGETS, []), "videos": _load(BLOB_MONITOR_VIDEOS, [])})


@router.post("/monitor/targets")
async def add_monitor_target(request: Request):
    body = await _json_body(request)
    username = str(body.get("username") or "").strip()
    share_url = str(body.get("share_url") or body.get("video_url") or "").strip()
    nickname = str(body.get("nickname") or "").strip()
    if not username and share_url:
        try:
            detail = await asyncio.to_thread(tikhub_channels.video_detail, share_url=share_url, api_key=_api_key())
            username = str(detail.get("username") or "")
            nickname = nickname or str(detail.get("nickname") or "")
        except Exception as exc:
            return _error(f"解析分享链接失败：{exc}")
    if not username:
        return _error("请输入同行视频号 username（v2_...@finder）或作品/分享链接")
    targets = _load(BLOB_MONITOR_TARGETS, [])
    if any(t.get("username") == username for t in targets):
        return _error("该同行已在监控列表")
    target = {"id": _next_id(targets), "username": username, "nickname": nickname, "status": "active",
              "created_at": _now_text(), "last_refresh": "", "video_count": 0}
    targets.append(target)
    _save(BLOB_MONITOR_TARGETS, targets)
    return _ok(target)


@router.post("/monitor/targets/{target_id}/refresh")
async def refresh_target(target_id: int):
    targets = _load(BLOB_MONITOR_TARGETS, [])
    target = next((t for t in targets if int(t.get("id") or 0) == int(target_id)), None)
    if not target:
        return _error("监控目标不存在", 404)
    try:
        result = await asyncio.to_thread(tikhub_channels.user_videos, target.get("username"), max_videos=30, api_key=_api_key())
    except Exception as exc:
        return _error(str(exc)[:300])
    videos = [v for v in _load(BLOB_MONITOR_VIDEOS, []) if int(v.get("target_id") or 0) != int(target_id)]
    for row in result.get("videos") or []:
        videos.append({**row, "target_id": int(target_id), "selected": True, "nickname": result.get("nickname") or target.get("nickname") or ""})
    target["video_count"] = len(result.get("videos") or [])
    target["last_refresh"] = _now_text()
    target["nickname"] = result.get("nickname") or target.get("nickname") or ""
    _save(BLOB_MONITOR_TARGETS, targets)
    _save(BLOB_MONITOR_VIDEOS, videos)
    return _ok({"target": target, "videos": [v for v in videos if int(v.get("target_id") or 0) == int(target_id)]})


@router.post("/monitor/targets/{target_id}/delete")
async def delete_target(target_id: int):
    _save(BLOB_MONITOR_TARGETS, [t for t in _load(BLOB_MONITOR_TARGETS, []) if int(t.get("id") or 0) != int(target_id)])
    _save(BLOB_MONITOR_VIDEOS, [v for v in _load(BLOB_MONITOR_VIDEOS, []) if int(v.get("target_id") or 0) != int(target_id)])
    return _ok({"deleted": target_id})


@router.post("/monitor/targets/{target_id}/videos/select")
async def select_target_videos(target_id: int, request: Request):
    body = await _json_body(request)
    selected = {str(v) for v in (body.get("video_ids") or [])}
    videos = _load(BLOB_MONITOR_VIDEOS, [])
    for row in videos:
        if int(row.get("target_id") or 0) == int(target_id):
            row["selected"] = str(row.get("video_id")) in selected
    _save(BLOB_MONITOR_VIDEOS, videos)
    return _ok({"selected": len(selected)})


@router.post("/monitor/run")
async def monitor_run(request: Request):
    body = await _json_body(request)
    target_id = int(body.get("target_id") or 0)
    videos = [v for v in _load(BLOB_MONITOR_VIDEOS, [])
              if int(v.get("target_id") or 0) == target_id and v.get("selected", True)]
    if not videos:
        return _error("该同行没有选中的作品，请先刷新")
    tasks = _tasks()
    created = []
    for video in videos:
        if not video.get("video_id") or any(t.get("video_id") == video.get("video_id") for t in tasks):
            continue
        task = {"id": _next_id(tasks), "session_id": 0,
                "keyword": f"同行:{video.get('nickname') or target_id}", "video_id": str(video.get("video_id")),
                "export_id": "", "title": video.get("title") or "", "nickname": video.get("nickname") or "",
                "username": video.get("username") or "", "cover": video.get("cover") or "",
                "like_count": int(video.get("like_count") or 0), "comment_count": int(video.get("comment_count") or 0),
                "target_count": int(body.get("max_comments") or _settings().get("max_comments") or 100),
                "collected": 0, "status": "pending", "created_at": _now_text(), "updated_at": _now_text(),
                "comments": [], "source": "monitor"}
        tasks.append(task)
        created.append(task)
    _save(BLOB_TASKS, tasks)
    return _ok({"created": [_task_lite(t) for t in created]})

# ── 视频号助手：我的评论区 / 私信 ──────────────────────────────────────────

@router.get("/self-comment-monitor/status")
async def self_comment_status():
    rows = _load("channels_self_comments", [])
    return _ok({"running": False, "count": len(rows) if isinstance(rows, list) else 0, "supported": True})


@router.post("/self-comment-monitor/run-once")
async def self_comment_run_once():
    result = await channels_browser.collect_own_comments()
    if result.get("ok"):
        _save("channels_self_comments", result.get("rows") or [])
    _log(f"我的评论区采集：{len(result.get('rows') or [])} 条")
    return _ok(result)


@router.post("/self-comment-monitor/start")
@router.post("/self-comment-monitor/stop")
@router.post("/self-comment-monitor/config")
async def self_comment_noop(request: Request):
    return _ok({"running": False, "message": "我的评论区按次采集；需要常驻请在排期中心配置。"})


@router.get("/inbox/status")
async def inbox_status():
    rows = _load(BLOB_INBOX, [])
    return _ok({"conversations": rows, "count": len(rows) if isinstance(rows, list) else 0, "supported": True})


@router.get("/stranger-messages/status")
async def stranger_status():
    return _ok({"running": False, "supported": False, "message": UNSUPPORTED["stranger-messages"]})


@router.post("/stranger-messages/monitor/start")
@router.post("/stranger-messages/monitor/stop")
@router.post("/stranger-messages/collect")
@router.post("/stranger-messages/send")
async def stranger_unsupported():
    return _error(UNSUPPORTED["stranger-messages"], 501, supported=False)


@router.post("/inbox/collect")
async def inbox_collect():
    result = await channels_browser.collect_private_messages()
    if result.get("ok"):
        _save(BLOB_INBOX, result.get("rows") or [])
    _log(f"私信会话采集：{len(result.get('rows') or [])} 个")
    return _ok(result)


@router.post("/inbox/conversation/detail")
async def inbox_detail(request: Request):
    body = await _json_body(request)
    key = str(body.get("nickname") or body.get("username") or "")
    row = next((r for r in _load(BLOB_INBOX, []) if key in (r.get("nickname"), r.get("username"))), None)
    return _ok(row) if row else _error("会话不存在", 404)


@router.post("/inbox/send")
async def inbox_send(request: Request):
    body = await _json_body(request)
    result = await channels_browser.send_private_message(
        str(body.get("nickname") or body.get("username") or ""),
        str(body.get("text") or body.get("message") or ""),
    )
    _log(f"私信发送：{result.get('nickname', '')} -> {'成功' if result.get('ok') else result.get('error')}")
    return _ok(result) if result.get("ok") else _error(result.get("error") or "发送失败", 500, need_login=result.get("need_login", False))


@router.post("/inbox/monitor/start")
@router.post("/inbox/monitor/stop")
async def inbox_monitor_noop():
    return _ok({"running": False, "message": "私信按次采集；需要常驻请在排期中心配置。"})


@router.get("/interaction/status")
async def interaction_status():
    return _ok(_load("channels_interaction_state", {}) or {"running": False, "sent": 0, "failed": 0})


@router.post("/interaction/start")
async def interaction_start(request: Request):
    body = await _json_body(request)
    text = str(body.get("message") or body.get("text") or "").strip()
    keys = {str(v) for v in (body.get("ids") or body.get("customers") or [])}
    if not text:
        return _error("请输入私信话术")
    precise = _load(BLOB_CUSTOMERS_PRECISE, [])
    targets = [r for r in precise if not keys or _customer_key(r) in keys]
    conversations = {str(r.get("nickname") or "") for r in _load(BLOB_INBOX, [])}
    if not conversations:
        return _error("还没有拉取私信会话。视频号只能回复已有会话，请先点“采集私信会话”。", 400, supported=False)
    matched = [r for r in targets if r.get("nickname") in conversations]
    if not matched:
        return _error("选中的客户都不在现有私信会话里。视频号不能主动冷私信，请先让对方私信你。", 400, supported=False)
    state = {"running": True, "total": len(matched), "sent": 0, "failed": 0, "started_at": _now_text()}
    _save("channels_interaction_state", state)
    for row in matched:
        result = await channels_browser.send_private_message(row.get("nickname") or "", text)
        if result.get("ok"):
            state["sent"] += 1
        else:
            state["failed"] += 1
        state["last"] = {"nickname": row.get("nickname"), "ok": bool(result.get("ok")), "error": result.get("error", "")}
        _save("channels_interaction_state", state)
        await asyncio.sleep(2)
    state["running"] = False
    state["finished_at"] = _now_text()
    _save("channels_interaction_state", state)
    return _ok(state)


@router.post("/interaction/stop")
@router.post("/interaction/reset")
async def interaction_stop():
    state = _load("channels_interaction_state", {}) or {}
    state["running"] = False
    _save("channels_interaction_state", state)
    return _ok(state)


# ── 排期中心 ──────────────────────────────────────────────────────────────

@router.get("/scheduler/overview")
async def scheduler_overview():
    plans = _load(BLOB_PLANS, [])
    return _ok({"plans": plans, "count": len(plans) if isinstance(plans, list) else 0})


@router.get("/scheduler/plans")
async def scheduler_plans():
    return _ok(_load(BLOB_PLANS, []))


@router.post("/scheduler/plans")
async def create_plan(request: Request):
    body = await _json_body(request)
    plans = _load(BLOB_PLANS, [])
    plan = {"id": _next_id(plans), "name": _clean_text(body.get("name") or body.get("keyword") or "视频号搜索计划", 80),
            "kind": body.get("kind") or "search", "keyword": _clean_text(body.get("keyword"), 100),
            "sort": body.get("sort") or "hot", "publish_time": body.get("publish_time") or "week",
            "max_results": int(body.get("max_results") or 20), "max_comments": int(body.get("max_comments") or 100),
            "enabled": bool(body.get("enabled", True)), "created_at": _now_text(), "last_run": "", "last_result": ""}
    if not plan["keyword"]:
        return _error("请输入关键词")
    plans.append(plan)
    _save(BLOB_PLANS, plans)
    return _ok(plan)


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload
        self._json = payload

    async def json(self):
        return self._payload


@router.post("/scheduler/plans/{plan_id}/run")
async def run_plan(plan_id: int):
    plans = _load(BLOB_PLANS, [])
    plan = next((p for p in plans if int(p.get("id") or 0) == int(plan_id)), None)
    if not plan:
        return _error("计划不存在", 404)
    result = await search_collect(_FakeRequest({"keyword": plan.get("keyword"), "sort": plan.get("sort"),
                                                 "publish_time": plan.get("publish_time"), "max_results": plan.get("max_results")}))
    if result.get("code") != 200:
        return result
    session = (result.get("data") or {}).get("session") or {}
    plan["last_run"] = _now_text()
    plan["last_result"] = f"搜索到 {len(session.get('items') or [])} 条视频"
    _save(BLOB_PLANS, plans)
    return _ok({"plan": plan, "session": session})


@router.post("/scheduler/plans/{plan_id}/toggle")
async def toggle_plan(plan_id: int):
    plans = _load(BLOB_PLANS, [])
    plan = next((p for p in plans if int(p.get("id") or 0) == int(plan_id)), None)
    if not plan:
        return _error("计划不存在", 404)
    plan["enabled"] = not bool(plan.get("enabled", True))
    _save(BLOB_PLANS, plans)
    return _ok(plan)


@router.post("/scheduler/plans/{plan_id}/delete")
async def delete_plan(plan_id: int):
    _save(BLOB_PLANS, [p for p in _load(BLOB_PLANS, []) if int(p.get("id") or 0) != int(plan_id)])
    return _ok({"deleted": plan_id})


# ── 明确不支持的抖音式能力 ────────────────────────────────────────────────

@router.get("/group-members/status")
@router.get("/mention-comment/status")
@router.get("/follow-comment/status")
@router.get("/video-comment/status")
async def unsupported_status(request: Request):
    path = request.url.path
    for key in ("group-members", "mention-comment", "follow-comment", "video-comment"):
        if key in path:
            capability = {"group-members": "group_members", "mention-comment": "follow_like_comment",
                          "follow-comment": "follow_like_comment", "video-comment": "follow_like_comment"}[key]
            return _ok({"running": False, "supported": False, "capability": capability, "message": UNSUPPORTED[key]})
    return _ok({"running": False, "supported": False})


@router.post("/group-members/{action}")
@router.post("/mention-comment/{action}")
@router.post("/follow-comment/{action}")
@router.post("/video-comment/{action}")
async def unsupported_action(request: Request):
    path = request.url.path
    for key in ("group-members", "mention-comment", "follow-comment", "video-comment"):
        if key in path:
            return _error(UNSUPPORTED[key], 501, supported=False)
    return _error("视频号暂不支持该能力", 501, supported=False)


@router.post("/start")
@router.post("/stop")
async def legacy_start_stop():
    return _ok({"running": False, "message": "视频号获客按任务执行；请使用搜索采集/评论任务。"})


@router.post("/monitor/keyword-expand-search")
async def keyword_expand(request: Request):
    body = await _json_body(request)
    keyword = _clean_text(body.get("keyword"), 100)
    if not keyword:
        return _error("请输入种子关键词")
    variants = [keyword] + [keyword + suffix for suffix in ("怎么选", "推荐", "价格", "加盟", "本地", "避坑", "案例")]
    return _ok({"keyword": keyword, "keywords": variants})
