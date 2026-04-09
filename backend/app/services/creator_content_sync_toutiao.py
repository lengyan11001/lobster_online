"""
今日头条头条号（mp.toutiao.com）作品列表 + 数据/收益字段同步。

策略：在已登录的 Playwright 上下文中依次打开 **首页、内容管理、收益总览、数据总览、视频/图文发布入口** 等，
监听 XHR/fetch 的 JSON：（1）递归提取含标题的作品条目；（2）从整棵 JSON 中收集与 **收益、粉丝、阅读/播放** 等相关的标量字段，
写入快照 `meta["toutiao_insights"]` 供前端展示。站点改版时需调整触发 URL 或关键词。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# 用于触发列表 / 数据 / 收益类 XHR 的页面（依次打开，各停留数秒抓包）
# 顺序：首页与内容管理 → 收益总览（数据-收益）→ 其他入口
TOUTIAO_TRIGGER_URLS = (
    "https://mp.toutiao.com/profile_v4/index",
    "https://mp.toutiao.com/profile_v4/manage/content/all",
    "https://mp.toutiao.com/profile_v4/analysis/income-overview",
    "https://mp.toutiao.com/profile_v4/analysis/overview",
    "https://mp.toutiao.com/profile_v4/xigua/upload-video",
    "https://mp.toutiao.com/profile_v4/xigua/publish",
    "https://mp.toutiao.com/profile_v4/graphic/publish",
    "https://mp.toutiao.com/",
)

LIST_URL_KEYWORDS = (
    "article",
    "content",
    "publish",
    "pgc",
    "graphic",
    "weitoutiao",
    "video",
    "post",
    "list",
    "manage",
    "creation",
    "item",
    "income",
    "revenue",
    "analysis",
    "overview",
    "data",
    "stat",
    "dashboard",
    "fan",
    "follow",
    "creator",
    "monitor",
    "summary",
    "account",
    "profit",
    "earning",
)


def _toutiao_url_maybe_list(u: str) -> bool:
    ul = (u or "").lower()
    if "mp.toutiao.com" not in ul:
        return False
    return any(k in ul for k in LIST_URL_KEYWORDS)


# 从任意 JSON 中「捞」与账号、收益、数据总览相关的叶子字段（后台改版时需调整关键词）
_INSIGHT_KEY_HINTS_EN = (
    "income",
    "revenue",
    "earning",
    "profit",
    "rpm",
    "cpm",
    "cpc",
    "amount",
    "money",
    "pay",
    "cash",
    "yesterday",
    "total_",
    "sum_",
    "follower",
    "fans",
    "subscribe",
    "read",
    "view",
    "play",
    "vv",
    "impression",
    "show",
    "comment",
    "like",
    "digg",
    "nickname",
    "screen_name",
    "user_name",
    "author_name",
    "media_name",
)
_INSIGHT_KEY_HINTS_ZH = ("收益", "收入", "元", "粉丝", "展现", "播放", "阅读", "统计", "累计", "昨日")


def _insight_key_interesting(key: str) -> bool:
    ks = str(key)
    kl = ks.lower()
    if any(h in kl for h in _INSIGHT_KEY_HINTS_EN):
        return True
    if any(h in ks for h in _INSIGHT_KEY_HINTS_ZH):
        return True
    if kl in (
        "fans_count",
        "follower_count",
        "user_id",
        "media_id",
        "read_count",
        "play_count",
        "video_play_count",
    ):
        return True
    return False


def _harvest_toutiao_insights(data: Any, out: Dict[str, Any], depth: int = 0) -> None:
    """递归收集疑似「数据/收益/粉丝/阅读」等标量字段，写入 out（同名后者覆盖）。"""
    if depth > 14 or len(out) >= 150:
        return
    if isinstance(data, dict):
        for k, v in data.items():
            if _insight_key_interesting(k):
                if isinstance(v, (int, float, bool)):
                    out[str(k)] = v
                elif isinstance(v, str):
                    s = v.strip()
                    if s and len(s) <= 280:
                        out[str(k)] = s
                elif v is None:
                    continue
                # dict/list 不作为叶子，下钻
            if isinstance(v, (dict, list)):
                _harvest_toutiao_insights(v, out, depth + 1)
    elif isinstance(data, list):
        for el in data[:100]:
            _harvest_toutiao_insights(el, out, depth + 1)


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


def _first_media_url(obj: Any) -> Optional[str]:
    if not obj:
        return None
    if isinstance(obj, str) and obj.startswith("http"):
        return obj
    if isinstance(obj, dict):
        for key in ("url", "image_url", "thumb_url", "cover_url", "src", "uri"):
            u = obj.get(key)
            if isinstance(u, str) and u.startswith("http"):
                return u
        lst = obj.get("url_list") or obj.get("urlList") or obj.get("images")
        if isinstance(lst, list) and lst:
            return _first_media_url(lst[0])
    return None


def _extract_toutiao_like_dicts(data: Any, depth: int = 0) -> List[Dict[str, Any]]:
    """递归查找疑似单条内容的 dict（标题 + id/时间/阅读量 等，减少嵌套大块误报）。"""
    out: List[Dict[str, Any]] = []
    if depth > 10:
        return out
    if isinstance(data, dict):
        title = (
            data.get("title")
            or data.get("article_title")
            or data.get("name")
            or data.get("content_title")
        )
        if isinstance(title, str) and title.strip():
            mid = (
                data.get("id")
                or data.get("article_id")
                or data.get("group_id")
                or data.get("item_id")
                or data.get("gid")
            )
            has_stats = any(
                data.get(k) is not None
                for k in (
                    "read_count",
                    "view_count",
                    "impression_count",
                    "comment_count",
                    "digg_count",
                    "play_count",
                    "video_play_count",
                    "vv",
                )
            )
            has_time = data.get("create_time") or data.get("publish_time") or data.get("ctime")
            if mid is not None or has_stats or has_time:
                out.append(data)
        for v in data.values():
            out.extend(_extract_toutiao_like_dicts(v, depth + 1))
    elif isinstance(data, list):
        for el in data:
            out.extend(_extract_toutiao_like_dicts(el, depth + 1))
    return out


def normalize_toutiao_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    title = (
        (raw.get("title") or raw.get("article_title") or raw.get("name") or "").strip() or "(无标题)"
    )
    mid = (
        raw.get("id")
        or raw.get("article_id")
        or raw.get("group_id")
        or raw.get("item_id")
        or raw.get("gid")
        or raw.get("articleId")
    )
    cover = _first_media_url(raw.get("cover_image") or raw.get("cover") or raw.get("thumb") or raw)
    metrics = {
        "view_count": _to_int(
            raw.get("read_count")
            or raw.get("view_count")
            or raw.get("impression_count")
            or raw.get("show_count")
        ),
        "play_count": _to_int(
            raw.get("play_count") or raw.get("video_play_count") or raw.get("vv") or raw.get("video_play")
        ),
        "like_count": _to_int(
            raw.get("digg_count") or raw.get("like_count") or raw.get("praise_count")
        ),
        "comment_count": _to_int(raw.get("comment_count") or raw.get("comments_count")),
        "share_count": _to_int(raw.get("share_count") or raw.get("forward_count")),
        "collect_count": _to_int(raw.get("collect_count") or raw.get("favorite_count")),
    }
    return {
        "id": str(mid) if mid is not None else "",
        "title": title,
        "cover_url": cover,
        "metrics": metrics,
        "content_type": str(raw.get("content_type") or raw.get("article_type") or raw.get("type") or ""),
        "create_time": raw.get("create_time") or raw.get("publish_time") or raw.get("ctime"),
    }


async def _dismiss_xigua_modal_if_any(page: Any) -> None:
    """西瓜上传页开通引导会挡接口；与发布驱动一致点「暂不开通」。"""
    for name in ("暂不开通", "暂不通"):
        try:
            loc = page.get_by_role("button", name=name)
            if await loc.count() > 0:
                first = loc.first
                if await first.is_visible():
                    await first.click(timeout=4000)
                    await asyncio.sleep(1.0)
                    return
        except Exception:
            pass
        try:
            alt = page.locator(f'button:has-text("{name}")')
            if await alt.count() > 0:
                await alt.first.click(timeout=4000)
                await asyncio.sleep(1.0)
                return
        except Exception:
            pass


async def _collect_toutiao_json_bodies(page: Any, goto_url: str, settle_sec: float = 9.0) -> List[Any]:
    """打开页面并在短时间内收集疑似列表接口的 JSON 根对象。"""
    bodies: List[Any] = []
    seen_urls: Set[str] = set()

    async def handle_response(response: Any) -> None:
        try:
            if response.request.resource_type not in ("xhr", "fetch"):
                return
            u = response.url
            if not _toutiao_url_maybe_list(u):
                return
            # 按完整 URL 去重，避免收益/数据接口同 path 不同 query 被误跳过
            nu = u[:600]
            if nu in seen_urls or len(seen_urls) > 400:
                return
            seen_urls.add(nu)
            ct = (response.headers or {}).get("content-type", "") or ""
            if "json" not in ct.lower():
                return
            txt = (await response.text())[:800_000]
            if len(txt) < 3:
                return
            data = json.loads(txt)
            bodies.append(data)
        except Exception:
            pass

    def _on_response(response: Any) -> None:
        asyncio.create_task(handle_response(response))

    page.on("response", _on_response)
    try:
        await page.goto(goto_url, wait_until="domcontentloaded", timeout=55_000)
        if "upload-video" in goto_url:
            await asyncio.sleep(1.5)
            await _dismiss_xigua_modal_if_any(page)
        await asyncio.sleep(settle_sec)
    finally:
        try:
            page.remove_listener("response", _on_response)
        except Exception:
            pass
    return bodies


def _item_sort_key(item: Dict[str, Any]) -> float:
    """新在前：按 create_time 数值或时间戳降序。"""
    ct = item.get("create_time")
    if isinstance(ct, (int, float)):
        return float(ct)
    if isinstance(ct, str) and ct.strip().isdigit():
        try:
            return float(ct.strip())
        except ValueError:
            pass
    return 0.0


async def sync_toutiao_creator_content(page: Any) -> Dict[str, Any]:
    """在已登录页上下文中抓取头条号作品列表 + 数据/收益相关 XHR 摘要。"""
    all_raw: Dict[str, Dict[str, Any]] = {}
    insights: Dict[str, Any] = {}
    meta: Dict[str, Any] = {"source": "toutiao_mp_xhr", "triggers_tried": []}

    for turl in TOUTIAO_TRIGGER_URLS:
        meta["triggers_tried"].append(turl)
        try:
            blobs = await _collect_toutiao_json_bodies(page, turl, settle_sec=8.5)
        except Exception as e:
            logger.info("toutiao trigger failed %s: %s", turl, e)
            continue
        for data in blobs:
            _harvest_toutiao_insights(data, insights)
            for d in _extract_toutiao_like_dicts(data):
                rid = str(
                    d.get("id")
                    or d.get("article_id")
                    or d.get("group_id")
                    or d.get("item_id")
                    or d.get("gid")
                    or ""
                )
                tstub = (str(d.get("title") or ""))[:80]
                tkey = str(d.get("publish_time") or d.get("create_time") or "")
                key = rid or f"h:{hash(tstub + '|' + tkey)}"
                if key not in all_raw:
                    all_raw[key] = d

    items = [normalize_toutiao_item(r) for r in all_raw.values()]
    items.sort(key=lambda x: -_item_sort_key(x))

    meta["toutiao_insights"] = dict(sorted(insights.items(), key=lambda kv: str(kv[0]).lower()))
    meta["toutiao_insights_count"] = len(insights)

    ok = len(items) > 0 or len(insights) > 0
    err = (
        None
        if ok
        else "未从头条号后台抓到有效 JSON（请确认已登录；可尝试在网页打开「数据-收益」后再同步）"
    )
    meta["fetched_count"] = len(items)
    return {
        "ok": ok,
        "error": err,
        "items": items,
        "meta": meta,
    }
