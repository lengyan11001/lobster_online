"""
同步抖音 / 小红书 / 今日头条头条号创作者「作品列表」概括数据（标题、封面、播放/阅读、互动数）。

在已登录的 persistent context 内使用 context.request 携带 Cookie 请求创作者域接口；
抖音/小红书若直连失败，再导航到作品/笔记管理页用 XHR 兜底。
头条号在 mp.toutiao.com 上通过打开管理页并监听 XHR JSON 聚合列表（见 creator_content_sync_toutiao）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from .creator_content_sync_toutiao import sync_toutiao_creator_content

logger = logging.getLogger(__name__)

DOUYIN_WORK_LIST_TMPL = (
    "https://creator.douyin.com/janus/douyin/creator/pc/work_list"
    "?status=0&count={count}&max_cursor={max_cursor}&scene=star_atlas&device_platform=android&aid=1128"
)
XHS_POSTED_TMPL = (
    "https://creator.xiaohongshu.com/api/galaxy/v2/creator/note/user/posted?tab=0&page={page}"
)

DOUYIN_NAV_TRIGGER_URLS = (
    "https://creator.douyin.com/creator-micro/content/post/video",
    "https://creator.douyin.com/creator-micro/content/manage",
)
XHS_NOTE_MANAGER_URL = "https://creator.xiaohongshu.com/new/note-manager"


def _to_int(v: Any, default: int = 0) -> int:
    if v is None:
        return default
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    s = str(v).strip()
    if not s:
        return default
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return default


def _first_url(obj: Any) -> Optional[str]:
    if not obj:
        return None
    if isinstance(obj, str) and obj.startswith("http"):
        return obj
    if isinstance(obj, dict):
        lst = obj.get("url_list") or obj.get("urlList")
        if isinstance(lst, list) and lst:
            u = lst[0]
            if isinstance(u, str) and u.startswith("http"):
                return u
        u = obj.get("url")
        if isinstance(u, str) and u.startswith("http"):
            return u
    return None


def normalize_douyin_work_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """单条抖音 work_list items[] → 统一结构。"""
    mid = item.get("metrics") or {}
    cover = _first_url(item.get("cover"))
    return {
        "id": str(item.get("id", "")),
        "title": (item.get("description") or "").strip() or "(无标题)",
        "cover_url": cover,
        "metrics": {
            "view_count": _to_int(mid.get("view_count")),
            "like_count": _to_int(mid.get("like_count")),
            "comment_count": _to_int(mid.get("comment_count")),
            "share_count": _to_int(mid.get("share_count")),
            "collect_count": _to_int(mid.get("favorite_count")),
            "cover_show": _to_int(mid.get("cover_show")),
            "homepage_visit_count": _to_int(mid.get("homepage_visit_count")),
        },
        "content_type": str(item.get("type", "")),
        "create_time": item.get("create_time"),
    }


def normalize_xhs_note(note: Dict[str, Any]) -> Dict[str, Any]:
    """单条小红书 note/user/posted → 统一结构（不落敏感 token）。"""
    images = note.get("images_list") or []
    cover = None
    if images and isinstance(images, list) and isinstance(images[0], dict):
        cover = images[0].get("url")
    return {
        "id": str(note.get("id", "")),
        "title": (note.get("display_title") or "").strip() or "(无标题)",
        "cover_url": cover if isinstance(cover, str) else None,
        "metrics": {
            "view_count": _to_int(note.get("view_count")),
            "like_count": _to_int(note.get("likes")),
            "comment_count": _to_int(note.get("comments_count")),
            "share_count": _to_int(note.get("shared_count")),
            "collect_count": _to_int(note.get("collected_count")),
        },
        "content_type": str(note.get("type", "")),
        "time": note.get("time"),
    }


def _douyin_parse_page(data: dict) -> Tuple[Optional[str], List[Dict[str, Any]], bool, int, Optional[int]]:
    """
    解析单页 work_list JSON。
    返回: error, normalized_items, has_more, max_cursor(下一页入参), total
    """
    if not isinstance(data, dict):
        return "响应不是 JSON 对象", [], False, 0, None
    sc = data.get("status_code")
    if sc is not None and sc != 0:
        return f"抖音 status_code={sc}", [], False, 0, None
    items = data.get("items")
    if not isinstance(items, list):
        return "响应缺少 items 数组", [], False, 0, None
    out = [normalize_douyin_work_item(raw) for raw in items if isinstance(raw, dict)]
    has_more = bool(data.get("has_more"))
    nxt = data.get("max_cursor")
    try:
        next_cursor = int(nxt) if nxt is not None else 0
    except (TypeError, ValueError):
        next_cursor = 0
    total = data.get("total")
    th = int(total) if isinstance(total, (int, float)) else None
    return None, out, has_more, next_cursor, th


def _xhs_parse_posted_json(data: dict) -> Tuple[Optional[str], List[Dict[str, Any]], Optional[int]]:
    """解析单页 note/user/posted。返回 error, items, tags_notes_count"""
    if not isinstance(data, dict):
        return "响应不是 JSON 对象", [], None
    if data.get("code") != 0:
        return str(data.get("msg") or data.get("message") or "业务错误"), [], None
    inner = data.get("data") or {}
    notes = inner.get("notes")
    if not isinstance(notes, list):
        return "响应缺少 data.notes", [], None
    out = [normalize_xhs_note(raw) for raw in notes if isinstance(raw, dict)]
    tags_notes_count: Optional[int] = None
    tags = inner.get("tags")
    if isinstance(tags, list):
        for t in tags:
            if isinstance(t, dict) and t.get("checked"):
                v = t.get("notes_count")
                if isinstance(v, (int, float)):
                    tags_notes_count = int(v)
                break
    return None, out, tags_notes_count


async def _request_json(api_request: Any, url: str) -> Tuple[Optional[dict], Optional[str]]:
    try:
        resp = await api_request.get(url, timeout=60_000)
        txt = await resp.text()
        if resp.status != 200:
            return None, f"HTTP {resp.status}: {txt[:500]}"
        return json.loads(txt), None
    except Exception as e:
        logger.exception("request_json failed: %s", url)
        return None, str(e)


async def sync_douyin_work_list(
    api_request: Any,
    *,
    page_size: int = 50,
    max_pages: int = 20,
    start_max_cursor: int = 0,
    append_to: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """拉取抖音 work_list，支持从指定 max_cursor 续拉（用于导航兜底后的分页）。"""
    all_items: List[Dict[str, Any]] = list(append_to) if append_to is not None else []
    max_cursor = start_max_cursor
    total_hint: Optional[int] = None
    last_err: Optional[str] = None

    for _ in range(max_pages):
        url = DOUYIN_WORK_LIST_TMPL.format(count=page_size, max_cursor=max_cursor)
        data, err = await _request_json(api_request, url)
        if err:
            last_err = err
            break
        if not isinstance(data, dict):
            last_err = "响应不是 JSON 对象"
            break
        e2, chunk, has_more, next_c, total = _douyin_parse_page(data)
        if e2:
            last_err = e2
            break
        all_items.extend(chunk)
        if total_hint is None and total is not None:
            total_hint = total
        if not has_more:
            break
        if next_c == max_cursor:
            break
        max_cursor = next_c

    meta = {
        "source": "douyin_work_list",
        "total_reported": total_hint,
        "fetched_count": len(all_items),
    }
    if last_err and all_items:
        meta["sync_warning"] = last_err
        return {
            "ok": True,
            "error": None,
            "items": all_items,
            "meta": meta,
        }

    return {
        "ok": last_err is None,
        "error": last_err,
        "items": all_items,
        "meta": meta,
    }


async def sync_xhs_posted_notes(
    api_request: Any,
    *,
    max_pages: int = 30,
    start_page: int = 0,
    append_to: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """拉取小红书已发布笔记；可从 start_page 起续拉。"""
    all_items: List[Dict[str, Any]] = list(append_to) if append_to is not None else []
    last_err: Optional[str] = None
    tags_notes_count: Optional[int] = None

    for i in range(max_pages):
        page = start_page + i
        url = XHS_POSTED_TMPL.format(page=page)
        data, err = await _request_json(api_request, url)
        if err:
            last_err = err
            break
        if not isinstance(data, dict):
            last_err = "响应不是 JSON 对象"
            break
        e2, chunk, tnc = _xhs_parse_posted_json(data)
        if e2:
            last_err = e2
            break
        if tags_notes_count is None and tnc is not None:
            tags_notes_count = tnc
        if not chunk:
            break
        all_items.extend(chunk)

    meta = {
        "source": "xhs_note_user_posted",
        "tags_notes_count": tags_notes_count,
        "fetched_count": len(all_items),
    }
    # 直连 API 常返回 HTTP 406（需页面态 Cookie/签名）；兜底后前几页有数据但翻页仍 406 时，
    # 不应把整次同步标失败 —— 否则前端已展示列表仍提示「同步失败」。
    if last_err and all_items:
        meta["sync_warning"] = last_err
        return {
            "ok": True,
            "error": None,
            "items": all_items,
            "meta": meta,
        }

    return {
        "ok": last_err is None,
        "error": last_err,
        "items": all_items,
        "meta": meta,
    }


def _douyin_work_list_predicate(response: Any) -> bool:
    try:
        u = response.url
        return response.status == 200 and "work_list" in u and "janus/douyin/creator/pc" in u
    except Exception:
        return False


def _xhs_posted_predicate(response: Any) -> bool:
    try:
        u = response.url
        return response.status == 200 and "note/user/posted" in u
    except Exception:
        return False


async def _douyin_first_page_via_navigation(page: Any) -> Tuple[Optional[dict], Optional[str]]:
    last_err: Optional[str] = None
    for gurl in DOUYIN_NAV_TRIGGER_URLS:
        try:
            async with page.expect_response(_douyin_work_list_predicate, timeout=40_000) as ri:
                await page.goto(gurl, wait_until="domcontentloaded", timeout=45_000)
            resp = await ri.value
            data = await resp.json()
            return data, None
        except Exception as e:
            last_err = str(e)
            logger.info("douyin nav trigger failed url=%s err=%s", gurl, e)
    return None, last_err or "导航兜底未捕获 work_list 响应"


async def _xhs_first_page_via_navigation(page: Any) -> Tuple[Optional[dict], Optional[str]]:
    try:
        async with page.expect_response(_xhs_posted_predicate, timeout=40_000) as ri:
            await page.goto(XHS_NOTE_MANAGER_URL, wait_until="domcontentloaded", timeout=45_000)
        resp = await ri.value
        return await resp.json(), None
    except Exception as e:
        return None, str(e)


async def sync_account_creator_content(
    profile_dir: str,
    platform: str,
    *,
    new_context_headless: bool = False,
    browser_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    使用 browser_pool 的持久化上下文，在已有登录态下发起同步。

    new_context_headless: 仅当池内尚未为该 profile 创建 context 时，以无头方式启动（见 browser_pool 说明）。
    browser_options: 与发布账号 meta 解析一致；None 表示默认 UA、无代理。
    """
    from publisher.browser_pool import (
        _acquire_context,
        _default_browser_options,
        _get_page_with_reacquire,
        _setup_auto_close,
    )

    if platform not in ("douyin", "xiaohongshu", "toutiao"):
        return {"ok": False, "error": f"不支持的平台: {platform}", "items": [], "meta": {}}

    opts = browser_options if browser_options is not None else _default_browser_options()
    ctx, _ = await _acquire_context(
        profile_dir, new_headless=new_context_headless, browser_options=opts
    )
    page, ctx = await _get_page_with_reacquire(
        profile_dir,
        ctx,
        new_headless_on_recreate=new_context_headless,
        browser_options=opts,
    )
    try:
        if platform == "toutiao":
            result = await sync_toutiao_creator_content(page)
            _setup_auto_close(ctx, profile_dir, page, browser_options=opts)
            return result

        if platform == "douyin":
            await page.goto(
                "https://creator.douyin.com/creator-micro/home",
                wait_until="domcontentloaded",
                timeout=45_000,
            )
        else:
            await page.goto(
                "https://creator.xiaohongshu.com/new/home",
                wait_until="domcontentloaded",
                timeout=45_000,
            )
        await asyncio.sleep(1.5)
        req = page.context.request

        if platform == "douyin":
            result = await sync_douyin_work_list(req)
            if not result.get("ok"):
                logger.info("douyin API 同步失败，尝试导航兜底: %s", result.get("error"))
                raw, nav_err = await _douyin_first_page_via_navigation(page)
                if raw is None:
                    meta = dict(result.get("meta") or {})
                    meta["navigation_fallback_error"] = nav_err
                    result["meta"] = meta
                else:
                    err_p, chunk, has_more, next_c, total = _douyin_parse_page(raw)
                    if err_p:
                        result = {
                            "ok": False,
                            "error": err_p,
                            "items": [],
                            "meta": {"source": "douyin_nav_fallback", "navigation_error": nav_err},
                        }
                    elif has_more:
                        result = await sync_douyin_work_list(
                            req,
                            start_max_cursor=next_c,
                            append_to=chunk,
                            max_pages=19,
                        )
                        meta = dict(result.get("meta") or {})
                        meta["source"] = "douyin_work_list+nav"
                        meta["total_reported"] = meta.get("total_reported") or total
                        result["meta"] = meta
                    else:
                        result = {
                            "ok": True,
                            "error": None,
                            "items": chunk,
                            "meta": {
                                "source": "douyin_work_list+nav",
                                "total_reported": total,
                                "fetched_count": len(chunk),
                            },
                        }
        else:
            result = await sync_xhs_posted_notes(req)
            if not result.get("ok"):
                logger.info("xhs API 同步失败，尝试导航兜底: %s", result.get("error"))
                raw, nav_err = await _xhs_first_page_via_navigation(page)
                if raw is None:
                    meta = dict(result.get("meta") or {})
                    meta["navigation_fallback_error"] = nav_err
                    result["meta"] = meta
                else:
                    err_p, chunk, tnc = _xhs_parse_posted_json(raw)
                    if err_p:
                        result = {
                            "ok": False,
                            "error": err_p,
                            "items": [],
                            "meta": {"source": "xhs_nav_fallback", "navigation_error": nav_err},
                        }
                    else:
                        rest = await sync_xhs_posted_notes(
                            req,
                            start_page=1,
                            append_to=chunk,
                            max_pages=29,
                        )
                        meta = dict(rest.get("meta") or {})
                        meta["source"] = "xhs_note_user_posted+nav"
                        if tnc is not None:
                            meta["tags_notes_count"] = tnc
                        rest["meta"] = meta
                        result = rest

        _setup_auto_close(ctx, profile_dir, page, browser_options=opts)
        return result
    except Exception as e:
        logger.exception("sync_account_creator_content")
        try:
            _setup_auto_close(ctx, profile_dir, page, browser_options=opts)
        except Exception:
            pass
        return {"ok": False, "error": str(e), "items": [], "meta": {}}
