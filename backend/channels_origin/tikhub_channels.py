"""视频号公开数据客户端：TikHub 微信视频号/搜一搜接口。

只做公开数据读取：关键词搜视频、视频详情、一级评论、账号作品。
不包含登录态、不发私信、不点赞不关注。
"""
from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

API_BASE = (os.environ.get("TIKHUB_API_BASE") or "https://api.tikhub.dev").rstrip("/")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_SEARCH_PATH = "/api/v1/wechat_search/v2/fetch_search_videos"
_DETAIL_PATH = "/api/v1/wechat_channels/v2/fetch_video_detail"
_COMMENTS_PATH = "/api/v1/wechat_channels/v2/fetch_video_comments"
_USER_VIDEOS_PATH = "/api/v1/wechat_channels/v2/fetch_user_videos"
_CHANNEL_INFO_PATH = "/api/v1/wechat_channels/v2/fetch_channel_info"


class TikHubError(RuntimeError):
    pass


def _read_env_file(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def resolve_api_key(override: str = "") -> str:
    key = str(override or "").strip()
    if key:
        return key
    key = (os.environ.get("TIKHUB_API_KEY") or "").strip()
    if key:
        return key
    # 本地开发兜底：客户端与服务端仓库并排时，复用服务端已配置的 key。
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / ".env",
        here.parents[3] / "lobster_server" / ".env",
        here.parents[3] / ".env",
    ]
    for path in candidates:
        env = _read_env_file(path)
        key = str(env.get("TIKHUB_API_KEY") or "").strip()
        if key:
            return key
    raise TikHubError("未配置 TIKHUB_API_KEY，无法调用视频号公开数据接口")


def _post(path: str, payload: Dict[str, Any], api_key: str = "", timeout: int = 45) -> Dict[str, Any]:
    key = resolve_api_key(api_key)
    try:
        resp = requests.post(
            API_BASE + path,
            json=payload,
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": UA,
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise TikHubError(f"视频号接口请求失败：{exc}") from exc
    try:
        body = resp.json()
    except Exception as exc:
        raise TikHubError(f"视频号接口返回非 JSON（HTTP {resp.status_code}）") from exc
    if resp.status_code >= 400 or int(body.get("code") or 0) not in (0, 200):
        message = (
            body.get("message_zh")
            or body.get("message")
            or f"HTTP {resp.status_code}"
        )
        raise TikHubError(str(message)[:300])
    return body


def _clean_title(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).replace("\n", " ").strip()


def _iter_items(results: Any) -> Iterable[Dict[str, Any]]:
    """搜索结果分组结构在不同版本里会变，做宽松拍平。"""
    if not isinstance(results, dict):
        return []
    groups = results.get("data")
    if not isinstance(groups, list):
        return []
    for group in groups:
        if not isinstance(group, dict):
            continue
        boxes = group.get("subBoxes")
        if not isinstance(boxes, list):
            boxes = [group]
        for box in boxes:
            if not isinstance(box, dict):
                continue
            items = box.get("items")
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        yield item


def _normalize_search_item(item: Dict[str, Any]) -> Dict[str, Any]:
    ext: Dict[str, Any] = {}
    jump = item.get("jumpInfo")
    if isinstance(jump, dict):
        raw_ext = jump.get("extInfo")
        if isinstance(raw_ext, str):
            try:
                ext = json.loads(raw_ext)
            except Exception:
                ext = {}
        elif isinstance(raw_ext, dict):
            ext = raw_ext
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    return {
        "video_id": "",
        "export_id": str(item.get("exportId") or ""),
        "object_nonce_id": str(ext.get("feedNonceId") or ""),
        "doc_id": str(item.get("docID") or ""),
        "hash_doc_id": str(item.get("hashDocID") or ""),
        "title": _clean_title(item.get("title")),
        "nickname": str(source.get("title") or ""),
        "avatar": str(source.get("iconUrl") or ""),
        "cover": str(item.get("image") or ""),
        "duration": str(item.get("duration") or ""),
        "publish_time": item.get("pubTime") or 0,
        "date_text": str(item.get("dateTime") or ""),
    }


def search_videos(
    keyword: str,
    *,
    sort: str = "hot",
    publish_time: str = "week",
    max_results: int = 20,
    api_key: str = "",
) -> Dict[str, Any]:
    keyword = str(keyword or "").strip()
    if not keyword:
        raise TikHubError("搜索关键词不能为空")
    limit = max(1, min(int(max_results or 20), 50))
    items: List[Dict[str, Any]] = []
    cursor = ""
    pages = 0
    while len(items) < limit and pages < 3:
        payload: Dict[str, Any] = {
            "keyword": keyword,
            "sort": sort or "hot",
            "publish_time": publish_time or "week",
            "raw": True,
        }
        if cursor:
            payload["cursor"] = cursor
        body = _post(_SEARCH_PATH, payload, api_key=api_key)
        data = body.get("data") or {}
        results = data.get("results") or {}
        for item in _iter_items(results):
            normalized = _normalize_search_item(item)
            if not normalized["export_id"] and not normalized["doc_id"]:
                continue
            items.append(normalized)
            if len(items) >= limit:
                break
        pages += 1
        cursor = str(data.get("cursor") or results.get("cursor") or "")
        if not cursor or not results.get("continueFlag"):
            break
    return {"keyword": keyword, "items": items, "pages": pages}


def video_detail(
    *,
    export_id: str = "",
    object_id: str = "",
    object_nonce_id: str = "",
    share_url: str = "",
    api_key: str = "",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"raw": False}
    if object_id:
        payload["object_id"] = str(object_id)
    elif export_id:
        payload["export_id"] = str(export_id)
    elif share_url:
        payload["share_url"] = str(share_url)
    else:
        raise TikHubError("需要 export_id / object_id / share_url 之一")
    if object_nonce_id:
        payload["object_nonce_id"] = str(object_nonce_id)
    body = _post(_DETAIL_PATH, payload, api_key=api_key, timeout=60)
    data = body.get("data")
    if not isinstance(data, dict) or not data.get("id"):
        message = body.get("message_zh") or body.get("message") or "未取到视频详情"
        raise TikHubError(str(message)[:300])
    return data


def video_comments(
    object_id: str,
    *,
    max_comments: int = 100,
    api_key: str = "",
) -> Dict[str, Any]:
    object_id = str(object_id or "").strip()
    if not object_id:
        raise TikHubError("评论采集需要视频 object_id")
    limit = max(1, min(int(max_comments or 100), 500))
    comments: List[Dict[str, Any]] = []
    last_buffer = ""
    pages = 0
    while len(comments) < limit and pages < 10:
        payload: Dict[str, Any] = {"object_id": object_id, "raw": False}
        if last_buffer:
            payload["last_buffer"] = last_buffer
        body = _post(_COMMENTS_PATH, payload, api_key=api_key, timeout=60)
        data = body.get("data") or {}
        rows = data.get("comments")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                comments.append(
                    {
                        "comment_id": str(row.get("comment_id") or ""),
                        "nickname": str(row.get("nickname") or ""),
                        "username": str(row.get("username") or ""),
                        "content": str(row.get("content") or ""),
                        "like_count": int(row.get("like_count") or 0),
                        "reply_count": int(row.get("reply_count") or 0),
                        "create_time": int(row.get("create_time") or 0),
                        "ip_location": str(row.get("ip_location") or ""),
                    }
                )
                if len(comments) >= limit:
                    break
        pages += 1
        last_buffer = str(data.get("last_buffer") or "")
        if not data.get("down_continue") or not last_buffer:
            break
    return {"object_id": object_id, "comments": comments, "pages": pages}


def user_videos(username: str, *, max_videos: int = 30, api_key: str = "") -> Dict[str, Any]:
    username = str(username or "").strip()
    if not username:
        raise TikHubError("缺少视频号 username")
    limit = max(1, min(int(max_videos or 30), 100))
    videos: List[Dict[str, Any]] = []
    last_buffer = ""
    pages = 0
    while len(videos) < limit and pages < 5:
        payload: Dict[str, Any] = {"username": username, "raw": False}
        if last_buffer:
            payload["last_buffer"] = last_buffer
        body = _post(_USER_VIDEOS_PATH, payload, api_key=api_key, timeout=60)
        data = body.get("data") or {}
        rows = data.get("videos")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                videos.append(
                    {
                        "video_id": str(row.get("id") or ""),
                        "title": _clean_title(row.get("title") or row.get("description")),
                        "cover": str(row.get("cover_img_url") or row.get("cover_url") or ""),
                        "duration": str(row.get("duration") or ""),
                        "like_count": int(row.get("like_count") or 0),
                        "comment_count": int(row.get("comment_count") or 0),
                        "create_time": int(row.get("create_time") or 0),
                        "username": username,
                        "nickname": str(data.get("nickname") or ""),
                    }
                )
                if len(videos) >= limit:
                    break
        pages += 1
        last_buffer = str(data.get("last_buffer") or "")
        if not data.get("up_continue") or not last_buffer:
            break
    return {
        "username": username,
        "nickname": str((body.get("data") or {}).get("nickname") or "") if pages else "",
        "videos": videos,
        "pages": pages,
    }


def channel_info(username: str, *, api_key: str = "") -> Dict[str, Any]:
    username = str(username or "").strip()
    if not username:
        raise TikHubError("缺少视频号 username")
    body = _post(_CHANNEL_INFO_PATH, {"username": username, "raw": False}, api_key=api_key, timeout=60)
    return body.get("data") or {}
