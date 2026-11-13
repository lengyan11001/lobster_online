"""视频号获客 API：本地微信 UI 自动化，不接 TikHub。"""
from __future__ import annotations

import asyncio
import csv
from collections import Counter
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

DOUYIN_INTENT_DIRECTION_DEFAULT = (
    "请筛选评论中是否属于精准客户。这里的精准客户指：有真实需求、了解意愿、"
    "咨询意愿、联系意愿的人。优先保留想了解、想咨询、感兴趣、想试试、想做、"
    "想进一步沟通，以及询问价格、费用、怎么买、怎么报名、怎么合作、怎么联系、"
    "适合我吗、新手能做吗、怎么开始这类评论。排除纯夸赞、纯围观、纯玩笑、"
    "无明确需求、重复内容。只判断是否精准，不做分层。"
)

DEFAULT_SETTINGS: Dict[str, Any] = {
    "max_results": 20,
    "max_comments": 200,
    "max_comments_initial": 80,
    "max_comments_deep": 200,
    "extend_min_leads": 3,
    "extend_rate": 0.05,
    "max_scroll_rounds": 8,
    "min_likes": 500,
    "avg_multiplier": 1.5,
    "exclude_historical_duplicates": True,
    "comment_direction": DOUYIN_INTENT_DIRECTION_DEFAULT,
    "filter_strategy": "prompt",
    "model": "",
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




def _douyin_ai_module():
    douyin_dir = Path(__file__).resolve().parents[1] / "douyin_origin"
    if str(douyin_dir) not in sys.path:
        sys.path.insert(0, str(douyin_dir))
    from backend.douyin_origin import douyin_api as dy

    return dy


def ai_available(request: Optional[Request] = None) -> bool:
    try:
        return bool(_douyin_ai_module().douyin_ai_available(request=request))
    except Exception:
        return False


def ai_filter_comments(
    request: Optional[Request],
    title: str,
    comments: List[Dict[str, Any]],
    prompt: str,
    strategy: str = "prompt",
    model: str = "",
) -> Dict[str, Any]:
    if not comments:
        return {"ok": True, "items": [], "error": ""}
    try:
        dy = _douyin_ai_module()
        dy.set_douyin_ai_auth_token_from_request(request)
        client = dy.create_ai_client(model)
        rows = []
        for index, row in enumerate(comments, start=1):
            rows.append({
                "comment_index": index,
                "username": row.get("nickname") or row.get("username") or "",
                "content": row.get("content") or "",
                "comment_id": row.get("key") or row.get("comment_id") or "",
                "comment_time": row.get("time") or "",
                "ip_location": row.get("region") or "",
                "like_count": row.get("like") or "",
                "reply_count": row.get("reply_count") or "",
                "avatar_url": row.get("avatar") or "",
            })
        result = client.filter_comments(
            title or "",
            rows,
            direction=str(prompt or ""),
            intent_profile="douyin_transactional",
            filter_strategy=str(strategy or "prompt"),
        )
        merged = []
        for item in result or []:
            try:
                index = int(item.get("comment_index") or 0) - 1
            except (TypeError, ValueError):
                index = -1
            source = comments[index] if 0 <= index < len(comments) else {}
            merged.append({
                **source,
                **item,
                "comment_index": index + 1 if index >= 0 else 0,
                "is_high_intent": True,
                "ai_reason": item.get("reason") or "",
                "ai_score": item.get("score") or 0,
                "ai_intent_level": item.get("intent_level") or "high",
            })
        return {"ok": True, "items": merged, "error": ""}
    except Exception as exc:
        return {"ok": False, "items": [], "error": str(exc)[:300]}


def parse_like_count(value: Any) -> int:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return 0
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([万亿]?)", text)
    if not match:
        return 0
    number = float(match.group(1))
    unit = match.group(2)
    if unit == "万":
        number *= 10000
    elif unit == "亿":
        number *= 100000000
    return int(number)


def mark_video_candidates(
    items: List[Dict[str, Any]],
    settings: Dict[str, Any],
    historical_keys: Optional[set] = None,
) -> List[Dict[str, Any]]:
    if not items:
        return []
    historical_keys = historical_keys or set()
    for item in items:
        item["likes_num"] = parse_like_count(item.get("like"))
    average = sum(int(item.get("likes_num") or 0) for item in items) / max(1, len(items))
    min_likes = max(0, int(settings.get("min_likes") or 0))
    avg_multiplier = max(0.0, float(settings.get("avg_multiplier") or 0))
    exclude_duplicates = bool(settings.get("exclude_historical_duplicates", True))
    threshold = average * avg_multiplier if avg_multiplier > 0 else 0
    for item in items:
        if item.get("is_live"):
            item["criteria_matched"] = False
            item["criteria_reason"] = "直播卡片"
            item["export_selected"] = False
            continue
        likes = int(item.get("likes_num") or 0)
        meets_floor = min_likes <= 0 or likes >= min_likes
        beats_average = avg_multiplier <= 0 or likes >= threshold
        matched = meets_floor or beats_average
        reasons = []
        if min_likes > 0 and likes >= min_likes:
            reasons.append(f"点赞至少 {min_likes}")
        if avg_multiplier > 0 and likes >= threshold:
            reasons.append(f"点赞达到平均值的 {avg_multiplier:g} 倍（均值 {average:.0f}）")
        duplicate = str(item.get("key") or "") in historical_keys
        if duplicate:
            reasons.append("历史重复")
        item["avg_likes"] = round(average, 2)
        item["criteria_matched"] = bool(matched)
        item["criteria_reason"] = " / ".join(reasons)
        item["is_historical_duplicate"] = duplicate
        item["export_selected"] = bool(matched and not (exclude_duplicates and duplicate))
    return items

def _customer_key(row: Dict[str, Any]) -> str:
    return str(row.get("customer_id") or row.get("nickname") or row.get("username") or "")


def _sync_customers() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    tasks = _load(BLOB_TASKS, [])
    settings = _settings()
    terms = [t.strip() for t in str(settings.get("intent_terms") or "").split(",") if t.strip()]
    all_rows: Dict[str, Dict[str, Any]] = {}
    precise: Dict[str, Dict[str, Any]] = {}
    for task in tasks if isinstance(tasks, list) else []:
        all_comments = task.get("all_comments") or task.get("comments") or []
        high_intent = task.get("high_intent_users") or []
        high_by_key = {}
        for row in high_intent:
            key = str(row.get("comment_id") or _customer_key(row) or "")
            if key:
                high_by_key[key] = row
        for comment in all_comments:
            raw_key = str(comment.get("comment_id") or _customer_key(comment) or "")
            if not raw_key:
                continue
            ai_row = high_by_key.get(raw_key, {})
            is_high = bool(ai_row)
            row = {
                "customer_id": raw_key,
                "nickname": comment.get("nickname") or comment.get("username") or "",
                "username": comment.get("username") or "",
                "content": comment.get("content") or comment.get("comment") or "",
                "like_count": parse_like_count(comment.get("like_count") or comment.get("like")),
                "region": comment.get("region") or comment.get("ip_location") or "",
                "comment_time": comment.get("time") or comment.get("comment_time") or "",
                "reply_count": comment.get("reply_count") or "",
                "avatar": comment.get("avatar") or comment.get("avatar_url") or "",
                "video_title": task.get("title") or "",
                "keyword": task.get("keyword") or "",
                "source": task.get("source") or "video_comment",
                "is_high_intent": is_high,
                "ai_reason": ai_row.get("reason") or ai_row.get("ai_reason") or "",
                "ai_score": ai_row.get("score") or ai_row.get("ai_score") or 0,
                "ai_intent_level": ai_row.get("intent_level") or ai_row.get("ai_intent_level") or ("high" if is_high else ""),
                "intent_score": round(float(ai_row.get("score") or 0) * 100) if is_high else _score_intent(comment.get("content") or "", terms),
            }
            old = all_rows.get(raw_key)
            if not old or row["intent_score"] > int(old.get("intent_score") or 0):
                all_rows[raw_key] = row
            if is_high:
                precise[raw_key] = row
        for raw_key, ai_row in high_by_key.items():
            if raw_key in all_rows:
                all_rows[raw_key].update({
                    "is_high_intent": True,
                    "ai_reason": ai_row.get("reason") or "",
                    "ai_score": ai_row.get("score") or 0,
                    "ai_intent_level": ai_row.get("intent_level") or "high",
                })
                precise[raw_key] = all_rows[raw_key]
            else:
                row = {
                    "customer_id": raw_key,
                    "nickname": ai_row.get("nickname") or ai_row.get("username") or "",
                    "content": ai_row.get("content") or ai_row.get("comment") or "",
                    "video_title": task.get("title") or "",
                    "keyword": task.get("keyword") or "",
                    "source": task.get("source") or "video_comment",
                    "is_high_intent": True,
                    "ai_reason": ai_row.get("reason") or "",
                    "ai_score": ai_row.get("score") or 0,
                    "ai_intent_level": ai_row.get("intent_level") or "high",
                    "intent_score": round(float(ai_row.get("score") or 0) * 100),
                }
                all_rows[raw_key] = row
                precise[raw_key] = row
    for row in _load(BLOB_CUSTOMERS_PRECISE, []):
        if isinstance(row, dict) and _customer_key(row):
            precise[_customer_key(row)] = row
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
            "is_high_intent": True,
            "ai_reason": "账号简介含联系方式",
            "ai_score": 1.0,
            "ai_intent_level": "high",
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
    deep = bool(body.get("deep"))
    max_queries = max(1, min(int(body.get("max_queries") or 6), 10))
    queries = [keyword]
    if deep:
        suffixes = ("怎么选", "推荐", "价格", "加盟", "本地", "避坑", "案例", "公司")
        queries = list(dict.fromkeys([keyword] + [keyword + suffix for suffix in suffixes]))[:max_queries]
    merged: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    per_query = max(10, min(30, max_results))
    for query in queries:
        try:
            result = await asyncio.to_thread(
                channels_ui.search_videos,
                query,
                max_results=per_query,
                max_scroll_rounds=int(body.get("max_scroll_rounds") or settings.get("max_scroll_rounds") or 8),
            )
            for item in result.get("items") or []:
                key = str(item.get("key") or item.get("raw") or "")
                if key and key not in merged:
                    item["source_query"] = query
                    merged[key] = item
        except Exception as exc:
            errors.append(f"{query}: {str(exc)[:120]}")
        if len(merged) >= max_results:
            break
    if not merged:
        _log(f"视频搜索失败：{errors[:2]}", level="error", keyword=keyword)
        return _error((errors[0] if errors else "没有搜到视频")[:300], 500)
    result = {"keyword": keyword, "items": list(merged.values()), "count": len(merged), "queries": queries, "errors": errors}
    _log(f"视频深搜：{keyword}，查询 {len(queries)} 个，去重后 {len(merged)} 条")
    sessions = _load(BLOB_SESSIONS, [])
    historical_keys = {
        str(item.get("key"))
        for old in sessions if isinstance(old, dict)
        for item in (old.get("items") or [])
        if item.get("key")
    }
    items = mark_video_candidates(result.get("items") or [], settings, historical_keys)
    session = {
        "id": _next_id(sessions),
        "keyword": keyword,
        "created_at": _now_text(),
        "items": items,
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
            max_scroll_rounds=int(body.get("max_scroll_rounds") or settings.get("max_scroll_rounds") or 8),
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




def _task_summary(task: Dict[str, Any]) -> Dict[str, Any]:
    comments = task.get("all_comments") or task.get("comments") or []
    high = task.get("high_intent_users") or []
    regions = Counter()
    for row in comments:
        region = str(row.get("region") or row.get("ip_location") or "").strip()
        if region:
            regions[region] += 1
    top = []
    for row in sorted(high, key=lambda item: float(item.get("score") or item.get("ai_score") or 0), reverse=True)[:8]:
        top.append({
            "nickname": row.get("nickname") or row.get("username") or "",
            "content": row.get("content") or row.get("comment") or "",
            "score": row.get("score") or row.get("ai_score") or 0,
            "reason": row.get("reason") or row.get("ai_reason") or "",
            "region": row.get("region") or row.get("ip_location") or "",
            "time": row.get("time") or row.get("comment_time") or "",
        })
    return {
        "comments": len(comments),
        "high_intent": len(high),
        "high_intent_rate": round(len(high) / max(1, len(comments)), 4),
        "regions": regions.most_common(8),
        "top_leads": top,
    }

def _task_lite(task: Dict[str, Any]) -> Dict[str, Any]:
    row = {k: v for k, v in task.items() if k not in ("comments", "all_comments", "high_intent_users")}
    row["all_count"] = len(task.get("all_comments") or task.get("comments") or [])
    row["high_intent_count"] = len(task.get("high_intent_users") or [])
    row["ai_used"] = bool(task.get("ai_used"))
    row["ai_error"] = str(task.get("ai_error") or "")
    return row


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
    videos = [row for row in session.get("items") or [] if str(row.get("key")) in keys and not row.get("is_live")]
    if not videos:
        return _error("请先选择视频")
    max_comments = int(body.get("max_comments") or _settings().get("max_comments") or 100)
    force = bool(body.get("force"))
    tasks = _load(BLOB_TASKS, [])
    created = []
    for video in videos:
        if not force and any(t.get("card_key") == video.get("key") and t.get("status") != "failed" for t in tasks):
            continue
        task = {
            "id": _next_id(tasks),
            "session_id": session_id,
            "keyword": session.get("keyword") or "",
            "source_query": video.get("source_query") or session.get("keyword") or "",
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
            "all_comments": [],
            "high_intent_users": [],
            "ai_used": False,
            "ai_error": "",
            "filter_prompt": _settings().get("comment_direction") or DOUYIN_INTENT_DIRECTION_DEFAULT,
            "filter_strategy": _settings().get("filter_strategy") or "prompt",
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
        settings = _settings()
        deep_target = int(body.get("max_comments") or task.get("deep_target") or task.get("target_count") or settings.get("max_comments_deep") or 200)
        initial_target = max(20, min(deep_target, int(settings.get("max_comments_initial") or 80)))
        result = await asyncio.to_thread(
            channels_ui.collect_comments_for_card,
            task.get("source_query") or task.get("keyword") or "",
            task.get("card_key") or "",
            max_comments=initial_target,
            close_after=False,
        )

        def merge_comments(existing: List[Dict[str, Any]], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            by_key = {str(row.get("comment_id") or ""): row for row in existing if row.get("comment_id")}
            for row in rows:
                key = str(row.get("key") or f"{row.get('nickname') or ''}|{row.get('content') or ''}")
                if not key or key in by_key:
                    continue
                by_key[key] = {
                    **row,
                    "comment_id": key,
                    "video_title": task.get("title") or "",
                    "keyword": task.get("keyword") or "",
                }
            merged_rows = list(by_key.values())
            for index, row in enumerate(merged_rows, start=1):
                row["comment_index"] = index
            return merged_rows

        comments = merge_comments([], result.get("items") or [])
        if result.get("detail"):
            task["detail"] = result["detail"]
        prompt = str(body.get("prompt") or task.get("filter_prompt") or settings.get("comment_direction") or DOUYIN_INTENT_DIRECTION_DEFAULT)
        strategy = str(body.get("strategy") or task.get("filter_strategy") or settings.get("filter_strategy") or "prompt")
        ai_result = await asyncio.to_thread(
            ai_filter_comments,
            request,
            task.get("title") or "",
            comments,
            prompt,
            strategy,
            str(settings.get("model") or ""),
        )
        high = ai_result.get("items") or []
        rate = len(high) / max(1, len(comments))
        extended = False
        if ai_result.get("ok") and deep_target > initial_target and (len(high) >= int(settings.get("extend_min_leads") or 3) or rate >= float(settings.get("extend_rate") or 0.05)):
            extra = await asyncio.to_thread(channels_ui.collect_comments, max_comments=deep_target)
            comments = merge_comments(comments, extra.get("items") or [])
            ai_result = await asyncio.to_thread(
                ai_filter_comments,
                request,
                task.get("title") or "",
                comments,
                prompt,
                strategy,
                str(settings.get("model") or ""),
            )
            high = ai_result.get("items") or []
            extended = True
        task["all_comments"] = comments
        task["comments"] = comments
        task["collected"] = len(comments)
        task["target_count"] = deep_target
        task["initial_target"] = initial_target
        task["scan_depth"] = deep_target if extended else initial_target
        task["extended"] = extended
        task["status"] = "done"
        task["error"] = ""
        task["filter_prompt"] = prompt
        task["filter_strategy"] = strategy
        task["ai_used"] = bool(ai_result.get("ok"))
        task["ai_error"] = str(ai_result.get("error") or "")
        task["high_intent_users"] = high
        task["summary"] = _task_summary(task)
    except Exception as exc:
        task["status"] = "failed"
        task["error"] = str(exc)[:300]
    finally:
        try:
            channels_ui.close_comment_panel()
            channels_ui.close_extra_channels_windows(keep_hwnd=channels_ui.channels_hwnd(required=False))
        except Exception:
            pass
    task["updated_at"] = _now_text()
    _save(BLOB_TASKS, tasks)
    _persist_customers()
    _log(f"评论任务 {task_id}：{task.get('collected', 0)} 条，状态 {task.get('status')}")
    return _ok({"task": _task_lite(task)}) if task.get("status") == "done" else _error(task.get("error") or "采集失败", 500)


@router.post("/tasks/{task_id}/refilter-customers")
async def refilter_customers(task_id: int, request: Request):
    body = await _json_body(request)
    tasks = _load(BLOB_TASKS, [])
    task = next((t for t in tasks if int(t.get("id") or 0) == int(task_id)), None)
    if not task:
        return _error("任务不存在", 404)
    comments = task.get("all_comments") or task.get("comments") or []
    if not comments:
        return _error("这条视频还没有已采集的评论，暂时不能筛选客户")
    settings = _settings()
    prompt = str(body.get("prompt") or task.get("filter_prompt") or settings.get("comment_direction") or DOUYIN_INTENT_DIRECTION_DEFAULT)
    strategy = str(body.get("strategy") or task.get("filter_strategy") or settings.get("filter_strategy") or "prompt")
    ai_result = await asyncio.to_thread(
        ai_filter_comments,
        request,
        task.get("title") or "",
        comments,
        prompt,
        strategy,
        str(settings.get("model") or ""),
    )
    if not ai_result.get("ok"):
        task["ai_used"] = False
        task["ai_error"] = str(ai_result.get("error") or "AI 筛选失败")
        _save(BLOB_TASKS, tasks)
        _log(f"AI 重筛失败：{task['ai_error']}", level="error", task_id=task_id)
        return _error(f"AI 筛选失败：{task['ai_error']}", 500)
    task["high_intent_users"] = ai_result.get("items") or []
    task["filter_prompt"] = prompt
    task["filter_strategy"] = strategy
    task["ai_used"] = True
    task["ai_error"] = ""
    task["updated_at"] = _now_text()
    _save(BLOB_TASKS, tasks)
    _persist_customers()
    _log(f"AI 重筛任务 {task_id}：评论 {len(comments)} 条，精准 {len(task['high_intent_users'])} 人")
    return _ok({"count": len(task["high_intent_users"]), "task": _task_lite(task)})


@router.get("/ai/status")
async def ai_status(request: Request):
    settings = _settings()
    return _ok({
        "available": ai_available(request),
        "model": str(settings.get("model") or ""),
        "strategy": str(settings.get("filter_strategy") or "prompt"),
        "direction": str(settings.get("comment_direction") or DOUYIN_INTENT_DIRECTION_DEFAULT),
    })


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
