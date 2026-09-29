"""发布数据（播放量）采集归一与上报载荷：抖音 / 视频号。

只做只读口径：把各平台「创作者后台」作品列表里已有的计数，归一成统一字段，
再打包成上报云端用的批次载荷。不修改任何平台数据，也不做写操作。

归一字段（缺失一律记 0，采样只用于趋势对比）：
- views       播放/阅读数
- likes       点赞
- comments    评论
- shares      转发/分享
- favorites   收藏/喜欢
- impressions 曝光（若平台提供）
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_BEIJING_OFFSET = timedelta(hours=8)

# 只有这两个平台参与「发布数据（播放量）每日同步」
METRIC_PLATFORMS: Tuple[str, ...] = ("douyin", "wechat_channels")

PLATFORM_LABEL: Dict[str, str] = {
    "douyin": "抖音",
    "wechat_channels": "视频号",
}

SAMPLE_BATCH_SIZE = 100

VIEW_KEYS = (
    "read_count",
    "play_count",
    "view_count",
    "exposure_count",
    "play_cnt",
    "read_cnt",
    "view_cnt",
    "vv",
)
LIKE_KEYS = ("like_count", "like_cnt", "digg_count", "likes", "praise_count")
COMMENT_KEYS = ("comment_count", "comment_cnt", "comments", "comments_count")
SHARE_KEYS = ("forward_count", "share_count", "share_cnt", "shared_count", "forward_cnt")
FAVORITE_KEYS = ("fav_count", "favorite_count", "collect_count", "collected_count", "fav_cnt")
IMPRESSION_KEYS = ("impression_count", "exposure_count", "show_count", "recommend_count")
TITLE_KEYS = ("title", "desc", "description", "caption", "content", "short_title")
ID_KEYS = ("object_id", "id", "item_id", "aweme_id", "export_id", "nonce_id", "object_nonce_id")
URL_KEYS = ("url", "jump_url", "share_url", "item_url")
TIME_KEYS = ("create_time", "publish_time", "create_timestamp", "ptime", "time")


def _to_int(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return int(bool(value)) if isinstance(value, bool) else default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return default
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return default


def _first_value(obj: Dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in obj and obj[key] not in (None, ""):
            return obj[key]
    return None


def _first_text(obj: Dict[str, Any], keys: Sequence[str]) -> str:
    value = _first_value(obj, keys)
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _to_epoch_seconds(value: Any) -> Optional[int]:
    """平台时间可能是秒/毫秒时间戳或 ISO 字符串；无法识别返回 None。"""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if re.fullmatch(r"\d{10,13}", text):
            return _normalize_epoch(int(text))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _normalize_epoch(int(value))
    return None


def _normalize_epoch(raw: int) -> int:
    if raw > 10_000_000_000:  # 毫秒
        return int(raw // 1000)
    return int(raw)


def epoch_to_utc_datetime(epoch_seconds: Optional[int]) -> Optional[datetime]:
    if not epoch_seconds:
        return None
    try:
        return datetime.utcfromtimestamp(int(epoch_seconds))
    except (OverflowError, OSError, ValueError):
        return None


def normalize_channels_post_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    """视频号助手作品对象 → 统一结构（字段名与抖音快照保持一致，便于复用）。"""
    obj = raw if isinstance(raw, dict) else {}
    return {
        "id": _first_text(obj, ID_KEYS),
        "title": _first_text(obj, TITLE_KEYS) or "(无标题)",
        "cover_url": _first_text(obj, ("cover_url", "cover", "thumb_url")) or None,
        "item_url": _first_text(obj, URL_KEYS) or None,
        "metrics": {
            "view_count": _to_int(_first_value(obj, VIEW_KEYS)),
            "like_count": _to_int(_first_value(obj, LIKE_KEYS)),
            "comment_count": _to_int(_first_value(obj, COMMENT_KEYS)),
            "share_count": _to_int(_first_value(obj, SHARE_KEYS)),
            "collect_count": _to_int(_first_value(obj, FAVORITE_KEYS)),
            "impression_count": _to_int(_first_value(obj, IMPRESSION_KEYS)),
        },
        "content_type": _first_text(obj, ("content_type", "object_type", "type")),
        "create_time": _to_epoch_seconds(_first_value(obj, TIME_KEYS)),
    }


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _find_post_list(payload: Any) -> Optional[List[Dict[str, Any]]]:
    """在多层 JSON 里定位作品数组：data.object.list / data.list / list。"""
    data = _as_dict(payload)
    candidates: List[Any] = [
        _as_dict(data.get("data")).get("list"),
        _as_dict(_as_dict(data.get("data")).get("object")).get("list"),
        data.get("list"),
        _as_dict(data.get("object")).get("list"),
    ]
    for candidate in candidates:
        if isinstance(candidate, list):
            return [x for x in candidate if isinstance(x, dict)]
    return None


def parse_channels_post_list_json(payload: Any) -> Tuple[Optional[str], List[Dict[str, Any]], bool]:
    """解析视频号助手作品列表响应。

    返回（错误、归一后的作品、是否还有下一页）。视频号用游标翻页，这里只做首页判定，
    采集侧默认抓最近若干条即可（播放量趋势采样不需要全量）。
    """
    if not isinstance(payload, dict):
        return "响应不是 JSON 对象", [], False
    ret = payload.get("errCode", payload.get("errcode", payload.get("ret")))
    if isinstance(ret, int) and ret not in (0,):
        msg = str(payload.get("errMsg") or payload.get("errmsg") or payload.get("msg") or "")
        return f"视频号返回 errCode={ret} {msg}".strip(), [], False
    raw_items = _find_post_list(payload)
    if raw_items is None:
        return "响应中未找到作品列表", [], False
    items = [normalize_channels_post_item(x) for x in raw_items]
    data = _as_dict(payload.get("data")) or _as_dict(payload.get("object"))
    inner = _as_dict(data.get("object"))
    has_more_field = data.get("has_more", data.get("hasMore"))
    if isinstance(has_more_field, bool):
        has_more = has_more_field
    else:
        # 视频号用游标翻页：last_buffer / next_cursor 出现在 data 或其 object 子节点
        has_more = bool(
            data.get("last_buffer")
            or inner.get("last_buffer")
            or data.get("next_cursor")
            or inner.get("next_cursor")
        )
    return None, items, has_more


def douyin_item_to_metric_row(item: Dict[str, Any], *, platform: str = "douyin") -> Dict[str, Any]:
    """抖音 creator work_list 归一结果（已是统一结构）→ 指标行。"""
    obj = item if isinstance(item, dict) else {}
    metrics = _as_dict(obj.get("metrics"))
    create_time = obj.get("create_time")
    published_at = None
    if isinstance(create_time, (int, float)) and not isinstance(create_time, bool):
        published_at = epoch_to_utc_datetime(_normalize_epoch(int(create_time)))
    elif isinstance(create_time, str):
        published_at = epoch_to_utc_datetime(_to_epoch_seconds(create_time))
    return {
        "platform": platform,
        "item_id": str(obj.get("id") or "").strip(),
        "item_url": (obj.get("item_url") or obj.get("share_url") or "").strip() or None,
        "title": (str(obj.get("title") or "").strip() or "(无标题)")[:500],
        "published_at": published_at,
        "views": _to_int(metrics.get("view_count")),
        "likes": _to_int(metrics.get("like_count")),
        "comments": _to_int(metrics.get("comment_count")),
        "shares": _to_int(metrics.get("share_count")),
        "favorites": _to_int(metrics.get("collect_count")),
        "impressions": _to_int(metrics.get("impression_count")),
    }


def build_metric_rows(
    items: Iterable[Dict[str, Any]],
    *,
    platform: str,
) -> List[Dict[str, Any]]:
    """作品列表 → 指标行；缺 item_id 的行丢弃（无法做幂等与去重）。"""
    if platform not in METRIC_PLATFORMS:
        raise ValueError(f"不支持的平台: {platform}")
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for item in items or []:
        row = douyin_item_to_metric_row(item, platform=platform)
        if not row["item_id"] or row["item_id"] in seen:
            continue
        seen.add(row["item_id"])
        rows.append(row)
    return rows


def beijing_day(dt_utc: datetime) -> str:
    """裸 UTC → 北京日期（YYYY-MM-DD），采样按北京自然日唯一。"""
    if dt_utc.tzinfo is not None:
        dt_utc = dt_utc.astimezone(timezone.utc).replace(tzinfo=None)
    shifted = dt_utc + _BEIJING_OFFSET
    return shifted.strftime("%Y-%m-%d")


def sample_key(
    *,
    installation_id: str,
    platform: str,
    item_id: str,
    sampled_day: str,
) -> str:
    """幂等键：同一天同一条作品只上报一次（重跑覆盖当日数值）。"""
    raw = f"{(installation_id or '').strip()}|{platform}|{item_id}|{sampled_day}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


def chunk_rows(rows: Sequence[Dict[str, Any]], size: int = SAMPLE_BATCH_SIZE) -> List[List[Dict[str, Any]]]:
    if size <= 0:
        raise ValueError("size 必须为正整数")
    return [list(rows[i : i + size]) for i in range(0, len(rows), size)]


def build_batch_payload(
    *,
    installation_id: str,
    user_id: int,
    rows: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """云端上报载荷；字段与本地采样表一一对应，时间为裸 UTC ISO。"""
    samples: List[Dict[str, Any]] = []
    for row in rows:
        published_at = row.get("published_at")
        sampled_at = row.get("sampled_at")
        samples.append(
            {
                "sample_key": row.get("sample_key"),
                "platform": row.get("platform"),
                "item_id": str(row.get("item_id") or ""),
                "item_url": row.get("item_url"),
                "title": row.get("title"),
                "account_id": row.get("account_id"),
                "account_nickname": row.get("account_nickname"),
                "published_at": published_at.isoformat() if isinstance(published_at, datetime) else None,
                "views": int(row.get("views") or 0),
                "likes": int(row.get("likes") or 0),
                "comments": int(row.get("comments") or 0),
                "shares": int(row.get("shares") or 0),
                "favorites": int(row.get("favorites") or 0),
                "impressions": int(row.get("impressions") or 0),
                "sampled_at": sampled_at.isoformat() if isinstance(sampled_at, datetime) else None,
                "sampled_day": row.get("sampled_day"),
                "source": row.get("source") or "daily_0200",
            }
        )
    return {
        "installation_id": installation_id,
        "user_id": int(user_id or 0),
        "samples": samples,
    }
