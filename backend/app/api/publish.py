"""Publishing accounts and task management."""
import asyncio
import json
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from publisher.browser_pool import browser_options_from_publish_meta

try:
    from publisher.browser_pool import douyin_workbench_browser_options_from_publish_meta
except ImportError:
    # Keep backend bootable if an older OTA only partially updated publisher/.
    def douyin_workbench_browser_options_from_publish_meta(meta: Optional[dict]) -> Dict[str, Any]:
        opts = browser_options_from_publish_meta(meta)
        opts = {**opts, "douyin_cdp": True}
        if not opts.get("viewport"):
            opts = {**opts, "viewport": {"width": 1440, "height": 960}}
        return opts

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import OperationalError
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from .auth import _ServerUser, get_current_user_for_local
from ..core.config import settings
from ..datetime_iso import isoformat_utc
from ..db import get_db
from ..models import (
    Asset,
    CreatorContentSnapshot,
    PublishAccount,
    PublishAccountCreatorSchedule,
    PublishTask,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_account_publish_locks: Dict[int, asyncio.Lock] = {}
_account_locks_meta_lock = asyncio.Lock()


async def _get_account_publish_lock(account_id: int) -> asyncio.Lock:
    """Per-account lock preventing concurrent Playwright publishes to the same browser context."""
    async with _account_locks_meta_lock:
        lock = _account_publish_locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            _account_publish_locks[account_id] = lock
        return lock

_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
BROWSER_DATA_DIR = _BASE_DIR / "browser_data"
BROWSER_DATA_DIR.mkdir(exist_ok=True)
DATA_DIR = _BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DOUYIN_WORKBENCH_CONFIG_PATH = DATA_DIR / "douyin_workbench_config.json"
DOUYIN_INTENT_DIRECTION_DEFAULT = (
    "请筛选评论中是否属于精准客户。这里的精准客户指：有真实需求、了解意愿、"
    "咨询意愿、联系意愿的人。优先保留想了解、想咨询、感兴趣、想试试、想做、"
    "想进一步沟通，以及询问价格、费用、怎么买、怎么报名、怎么合作、怎么联系、"
    "适合我吗、新手能做吗、怎么开始这类评论。排除纯夸赞、纯围观、纯玩笑、"
    "无明确需求、重复内容。只判断是否精准，不做分层。"
)
DOUYIN_COMMENT_FILTER_STRATEGIES = {"prompt", "reverse"}


def _normalize_douyin_comment_filter_strategy(value: object) -> str:
    normalized = str(value or "prompt").strip().lower()
    return normalized if normalized in DOUYIN_COMMENT_FILTER_STRATEGIES else "prompt"


DOUYIN_SEARCH_MODES = {"api", "script"}


def _normalize_douyin_search_mode(value: object) -> str:
    normalized = str(value or "api").strip().lower()
    return normalized if normalized in DOUYIN_SEARCH_MODES else "api"


def _format_douyin_compact_count(value: object) -> str:
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


def _format_douyin_duration_text(value: object) -> str:
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


def _format_douyin_publish_time(value: object) -> str:
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


def _build_douyin_profile_url(sec_uid: str) -> str:
    sec_uid = str(sec_uid or "").strip()
    return f"https://www.douyin.com/user/{sec_uid}" if sec_uid else ""


def _pick_tikhub_douyin_cover(video: Dict[str, Any]) -> str:
    for key in ("cover", "dynamic_cover", "origin_cover"):
        node = video.get(key)
        if isinstance(node, dict):
            url_list = node.get("url_list")
            if isinstance(url_list, list):
                for url in url_list:
                    if isinstance(url, str) and url.strip():
                        return url.strip()
    return ""


def _normalize_tikhub_douyin_search_items(
    payload: Dict[str, Any],
    *,
    keyword: str,
    max_results: int,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
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
        share_url_probe = ""
        share_info_probe = aweme.get("share_info") if isinstance(aweme.get("share_info"), dict) else {}
        if isinstance(share_info_probe, dict):
            share_url_probe = str(share_info_probe.get("share_url") or "").strip()
        if not share_url_probe:
            share_url_probe = str(aweme.get("share_url") or "").strip()
        if "/share/note/" in share_url_probe:
            continue
        aweme_id = str(aweme.get("aweme_id") or "").strip()
        desc = str(aweme.get("desc") or "").strip()
        author = aweme.get("author") if isinstance(aweme.get("author"), dict) else {}
        stats = aweme.get("statistics") if isinstance(aweme.get("statistics"), dict) else {}
        video = aweme.get("video") if isinstance(aweme.get("video"), dict) else {}
        sec_uid = str(author.get("sec_uid") or "").strip()
        share_info = aweme.get("share_info") if isinstance(aweme.get("share_info"), dict) else {}
        share_url = str(share_info.get("share_url") or aweme.get("share_url") or "").strip()
        canonical_url = f"https://www.douyin.com/video/{aweme_id}" if aweme_id else (share_url or "")
        digg_count = int(stats.get("digg_count") or 0)
        comment_count = int(stats.get("comment_count") or 0)
        row = {
            "aweme_id": aweme_id,
            "url": canonical_url,
            "share_url": share_url,
            "title": desc,
            "author": str(author.get("nickname") or "").strip(),
            "profile_url": _build_douyin_profile_url(sec_uid),
            "cover_image": _pick_tikhub_douyin_cover(video),
            "likes": digg_count,
            "comments": comment_count,
            "likes_text": _format_douyin_compact_count(digg_count),
            "comments_text": _format_douyin_compact_count(comment_count),
            "duration": _format_douyin_duration_text(aweme.get("duration")),
            "publish_time": _format_douyin_publish_time(aweme.get("create_time")),
            "criteria_reason": "",
            "index": len(rows) + 1,
            "keyword": keyword,
            "export_selected": False,
            "sec_user_id": sec_uid,
        }
        if not row["url"] and share_url:
            row["url"] = share_url
        if not row["title"] and aweme_id:
            row["title"] = f"抖音视频 {aweme_id}"
        if not row["url"] or not row["title"]:
            continue
        rows.append(row)
        if len(rows) >= max_results:
            break
    return rows


async def _tikhub_douyin_keyword_search(keyword: str, max_results: int) -> Dict[str, Any]:
    base = ((getattr(settings, "tikhub_api_base", None) or "") or os.environ.get("TIKHUB_API_BASE", "")).strip().rstrip("/") or "https://api.tikhub.dev"
    api_key = ((getattr(settings, "tikhub_api_key", None) or "") or os.environ.get("TIKHUB_API_KEY", "")).strip()
    if not api_key:
        raise RuntimeError("服务器未配置 TIKHUB_API_KEY")
    payload = {
        "keyword": keyword,
        "offset": "0",
        "count": str(max(1, min(int(max_results or 30), 30))),
        "sort_type": "0",
        "publish_time": "0",
        "filter_duration": "0",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{base}/api/v1/douyin/search/fetch_general_search_v1",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    resp.raise_for_status()
    data = resp.json()
    code = int(data.get("code") or 0)
    if code != 200:
        raise RuntimeError(str(data.get("message_zh") or data.get("message") or "Tikhub 搜索失败"))
    rows = _normalize_tikhub_douyin_search_items(data, keyword=keyword, max_results=max_results)
    return {
        "ok": True,
        "message": f"抖音搜索完成，共采集到 {len(rows)} 条视频。",
        "data": rows,
        "total": len(rows),
        "request_id": str(data.get("request_id") or "").strip(),
        "cache_url": str(data.get("cache_url") or "").strip(),
    }


def _default_douyin_workbench_config() -> Dict[str, Any]:
    return {
        "ai_filter_enabled": True,
        "comment_direction": DOUYIN_INTENT_DIRECTION_DEFAULT,
        "comment_filter_strategy": "prompt",
        "comment_max_comments": 120,
    }


def _load_douyin_workbench_config() -> Dict[str, Any]:
    config = _default_douyin_workbench_config()
    try:
        if DOUYIN_WORKBENCH_CONFIG_PATH.exists():
            raw = json.loads(DOUYIN_WORKBENCH_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                config.update(raw)
    except Exception as exc:
        logger.warning("[DOUYIN-WORKBENCH] load config failed: %s", exc)
    config["comment_direction"] = str(config.get("comment_direction") or "").strip() or DOUYIN_INTENT_DIRECTION_DEFAULT
    config["comment_filter_strategy"] = _normalize_douyin_comment_filter_strategy(config.get("comment_filter_strategy"))
    config["comment_max_comments"] = max(1, min(int(config.get("comment_max_comments") or 120), 500))
    config["ai_filter_enabled"] = bool(config.get("ai_filter_enabled", True))
    return config


def _save_douyin_workbench_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    config = _load_douyin_workbench_config()
    if "ai_filter_enabled" in patch:
        config["ai_filter_enabled"] = bool(patch.get("ai_filter_enabled"))
    if "comment_direction" in patch:
        config["comment_direction"] = str(patch.get("comment_direction") or "").strip() or DOUYIN_INTENT_DIRECTION_DEFAULT
    if "comment_filter_strategy" in patch:
        config["comment_filter_strategy"] = _normalize_douyin_comment_filter_strategy(patch.get("comment_filter_strategy"))
    if "comment_max_comments" in patch:
        config["comment_max_comments"] = max(1, min(int(patch.get("comment_max_comments") or 120), 500))
    try:
        DOUYIN_WORKBENCH_CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("[DOUYIN-WORKBENCH] save config failed: %s", exc)
    return config

# 主素材为下列后缀时，头条图文应按「单图封面」上传主图，忽略模型误传的 toutiao_graphic_no_cover。
_IMAGE_MAIN_ASSET_SUFFIX = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
# 视频主素材须走上传视频链路；若同上轮纯文残留 no_cover=true，会误进图文发布页。
_VIDEO_MAIN_ASSET_SUFFIX = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".flv", ".m4v"}


def _query_publish_account_by_nickname(
    db: Session, user_id: int, nick: str, platform_hint: Optional[str] = None
) -> Optional[PublishAccount]:
    """按昵称查且必须唯一；0 条返回 None；多条抛 400。"""
    q = db.query(PublishAccount).filter(
        PublishAccount.user_id == user_id,
        PublishAccount.nickname == nick,
    )
    if platform_hint:
        q = q.filter(PublishAccount.platform == platform_hint)
    n = q.count()
    if n == 0:
        return None
    if n > 1:
        ids = [r.id for r in q.all()]
        logger.warning(
            "[PUBLISH-API] 拒绝-同昵称多账号 user_id=%s nickname_repr=%r count=%d account_ids=%s",
            user_id,
            nick,
            n,
            ids,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                "存在多个相同昵称的发布账号，无法仅凭昵称识别。请改用 account_id 调用发布，"
                "或在「发布管理」中为各平台账号设置互不相同的昵称。"
            ),
        )
    return q.first()


_ACCOUNT_NICK_PREFIX_PATTERNS = [
    (
        re.compile(r"^(?:抖店|抖音商城|douyin[_\s-]*shop)\s*(?:账号|帐号|账户|号|昵称)?\s*[:：\-_/|]?\s*(.+)$", re.IGNORECASE),
        "douyin_shop",
    ),
    (
        re.compile(r"^(?:抖音|douyin)\s*(?:账号|帐号|账户|号|昵称)?\s*[:：\-_/|]?\s*(.+)$", re.IGNORECASE),
        "douyin",
    ),
    (
        re.compile(r"^(?:小红书|xiaohongshu|xhs)\s*(?:账号|帐号|账户|号|昵称)?\s*[:：\-_/|]?\s*(.+)$", re.IGNORECASE),
        "xiaohongshu",
    ),
    (
        re.compile(r"^(?:今日头条|头条|toutiao)\s*(?:账号|帐号|账户|号|昵称)?\s*[:：\-_/|]?\s*(.+)$", re.IGNORECASE),
        "toutiao",
    ),
]
_ACCOUNT_NICK_GENERIC_PREFIX_RE = re.compile(
    r"^(?:账号|帐号|账户|号|昵称)\s*[:：\-_/|]?\s*(.+)$",
    re.IGNORECASE,
)


def _clean_account_nickname_fragment(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"^[\s\"'“”‘’《》<>【】\[\]（）()]+|[\s\"'“”‘’《》<>【】\[\]（）()]+$", "", text)
    text = re.sub(r"^[：:，,。.\-_/|]+|[：:，,。.\-_/|]+$", "", text)
    return text.strip()


def _account_nickname_candidates(raw_nick: str) -> tuple[List[str], Optional[str]]:
    """Return nickname lookup candidates and an optional platform hint inferred from natural text."""
    candidates: List[str] = []
    platform_hint: Optional[str] = None

    def add(value: str) -> None:
        candidate = _clean_account_nickname_fragment(value)
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    nick = _clean_account_nickname_fragment(raw_nick)
    add(nick)
    if not nick:
        return candidates, platform_hint

    for pattern, platform in _ACCOUNT_NICK_PREFIX_PATTERNS:
        m = pattern.match(nick)
        if not m:
            continue
        if platform_hint is None:
            platform_hint = platform
        add(m.group(1))

    generic = _ACCOUNT_NICK_GENERIC_PREFIX_RE.match(nick)
    if generic:
        add(generic.group(1))

    return candidates, platform_hint


def _resolve_publish_account_for_request(
    db: Session,
    user_id: int,
    account_id: Optional[int],
    account_nickname: Optional[str],
) -> Optional[PublishAccount]:
    """
    用户常以「2」「6」指昵称；若误把该数字填在 account_id 里，主键无匹配时按昵称再查一次。
    若同时传 account_nickname 与 account_id，优先按昵称解析（与单发时一致）。
    LLM 常在昵称前加平台名（如"抖音123"），精确匹配失败后自动剥离平台前缀重试。
    """
    nick = (account_nickname or "").strip()
    if nick:
        candidates, platform_hint = _account_nickname_candidates(nick)
        for candidate in candidates:
            acct = _query_publish_account_by_nickname(db, user_id, candidate, platform_hint)
            if acct is not None:
                if candidate != nick or platform_hint:
                    logger.info(
                        "[PUBLISH-API] account_nickname=%r 规范化为 nickname=%r platform_hint=%r 命中 id=%s",
                        nick, candidate, platform_hint, acct.id,
                    )
                return acct
        return None
    if account_id is not None:
        acct = (
            db.query(PublishAccount)
            .filter(
                PublishAccount.id == account_id,
                PublishAccount.user_id == user_id,
            )
            .first()
        )
        if acct is not None:
            return acct
        nick_candidate = str(account_id).strip()
        candidates, platform_hint = _account_nickname_candidates(nick_candidate)
        for candidate in candidates:
            acct = _query_publish_account_by_nickname(db, user_id, candidate, platform_hint)
            if acct is not None:
                logger.info(
                    "[PUBLISH-API] account_id=%s 无主键匹配，已按昵称「%s」解析为 id=%s platform=%s",
                    account_id,
                    candidate,
                    acct.id,
                    acct.platform,
                )
                return acct
        return None
    return None


def _effective_publish_copy_from_asset(
    asset: Asset,
    title: Optional[str],
    description: Optional[str],
    tags: Optional[str],
    *,
    xhs_strict: bool = False,
) -> tuple[str, str, str]:
    """
    未传标题/正文时，用素材 generation prompt、素材 tags、文件名 stem 补全（非 LLM，避免发布链路再调模型）。
    xhs_strict：小红书入参已校验过；用户未传 description 但传了 tags 时，勿用素材 prompt 冒充正文（由前端驱动用话题拼正文）。
    """
    t = (title or "").strip()
    d = (description or "").strip()
    g = (tags or "").strip()
    prompt = (getattr(asset, "prompt", None) or "").strip()
    asset_tags = (getattr(asset, "tags", None) or "").strip()
    stem = Path(asset.filename or "untitled").stem or "作品"

    def _title_from_description_line() -> str:
        """用户/模型已写正文但未写标题时，用正文首行作标题，避免用 asset_id 文件名当标题。"""
        if not d:
            return ""
        line0 = d.split("\n", 1)[0].strip()
        return line0[:120] if line0 else ""

    if not t:
        if xhs_strict:
            # 小红书：优先正文首行（≤20 字由后续 normalize 截断），否则文件名 stem
            t = (_title_from_description_line()[:20] if d else "") or stem[:120]
        elif prompt:
            first = prompt.split("\n", 1)[0].strip()
            t = (
                (first[:120] if first else prompt[:120])
                or _title_from_description_line()
                or stem[:120]
            )
        else:
            t = _title_from_description_line() or stem[:120]
    if not d:
        if xhs_strict and g:
            d = ""
        elif prompt:
            d = prompt[:5000]
        else:
            d = t if t else "作品分享"
    if not g and asset_tags:
        g = asset_tags[:2000]
    return t, d, g


def _sanitize_internal_publish_tags(g: str) -> str:
    """去掉 MCP/速推自动入库标记，避免抖音等把 tags 拼进描述后出现 #sutui.transfer_url 等。"""
    parts = [x.strip() for x in (g or "").split(",") if x.strip()]
    drop = {"auto", "task.get_result", "sutui.transfer_url", "transfer_url"}
    out: List[str] = []
    for p in parts:
        pl = p.lower()
        if pl in drop:
            continue
        # 形如 sutui.xxx 的能力 ID，不作用户话题
        if pl.startswith("sutui.") and len(pl) > 6:
            continue
        if pl.endswith(".transfer_url"):
            continue
        out.append(p)
    return ",".join(out)


def _infer_asset_media_type(a: Asset) -> str:
    mt = (getattr(a, "media_type", None) or "").strip().lower()
    if mt in ("video", "image", "audio"):
        return mt
    suf = Path(a.filename or "").suffix.lower()
    if suf in (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"):
        return "video"
    if suf in _IMAGE_MAIN_ASSET_SUFFIX:
        return "image"
    return "video"


def _asset_likely_ai_generated(asset: Optional[Asset]) -> bool:
    if asset is None:
        return False
    tags = (getattr(asset, "tags", None) or "").strip().lower()
    model = (getattr(asset, "model", None) or "").strip()
    meta = getattr(asset, "meta", None) if isinstance(getattr(asset, "meta", None), dict) else {}
    if model:
        return True
    if meta.get("generation_task_id") or meta.get("generated") or meta.get("ai_generated"):
        return True
    if tags:
        needles = (
            "image.generate",
            "video.generate",
            "task.get_result",
            "ai_generated",
            "synthetic",
        )
        if any(n in tags for n in needles):
            return True
    return False


def _douyin_publish_opts_wants_declaration(opts: dict) -> bool:
    keys = (
        "douyin_declaration_mode",
        "douyin_self_declaration",
        "douyin_declaration_text",
        "douyin_self_declaration_text",
        "self_declaration",
        "self_declaration_text",
        "declaration_mode",
        "declaration",
        "douyin_ai_generated",
        "ai_generated",
        "contains_ai_generated",
        "contains_synthetic_media",
        "synthetic_media",
        "material_origin",
    )
    if any(k in opts for k in keys):
        return True
    inner = opts.get("douyin")
    return isinstance(inner, dict) and any(k in inner for k in keys)


def _truthy(v: Optional[object]) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "on")


def _toutiao_opts_wants_graphic_no_cover(opts: Optional[dict]) -> bool:
    """与头条发布驱动一致：顶层 toutiao_graphic_no_cover 或 toutiao.{graphic_no_cover|no_cover}。"""
    if not isinstance(opts, dict):
        return False
    if _truthy(opts.get("toutiao_graphic_no_cover")):
        return True
    inner = opts.get("toutiao")
    if isinstance(inner, dict):
        if _truthy(inner.get("graphic_no_cover")) or _truthy(inner.get("no_cover")):
            return True
    return False


_TOUTIAO_PUBLISH_NO_ASSET_SENTINEL = "__toutiao_graphic_no_asset__"


def _effective_publish_copy_no_asset(
    title: Optional[str],
    description: Optional[str],
    tags: Optional[str],
) -> tuple[str, str, str]:
    """无素材行：仅用入参补全文案（今日头条无封面纯文等）。"""
    t = (title or "").strip()
    d = (description or "").strip()
    g = (tags or "").strip()

    def _title_from_description_line() -> str:
        if not d:
            return ""
        line0 = d.split("\n", 1)[0].strip()
        return line0[:120] if line0 else ""

    if not t:
        t = _title_from_description_line() or "分享"
    if not d:
        d = t if t else "作品分享"
    return t, d, g


def _toutiao_strip_graphic_no_cover_for_image_main(publish_opts: dict, main_suffix: str) -> None:
    """
    对话常在 publish_content 的 options 里带 toutiao_graphic_no_cover=true；
    主素材为**图片**时强制走单图封面流程；主素材为**视频**时须走上传视频入口，不能有「纯文无封面」残留。
    确需「有图占位但仍无封面」时传 toutiao_force_graphic_no_cover: true（仅对图片主素材生效）。
    """
    suf = (main_suffix or "").lower()
    is_img = suf in _IMAGE_MAIN_ASSET_SUFFIX
    is_vid = suf in _VIDEO_MAIN_ASSET_SUFFIX
    if not is_img and not is_vid:
        return
    if is_img and _truthy(publish_opts.get("toutiao_force_graphic_no_cover")):
        return
    had = publish_opts.get("toutiao_graphic_no_cover") is not None
    inner = publish_opts.get("toutiao")
    if isinstance(inner, dict):
        had = had or inner.get("graphic_no_cover") is not None or inner.get("no_cover") is not None
    if not had:
        return
    publish_opts.pop("toutiao_graphic_no_cover", None)
    if is_vid:
        publish_opts.pop("toutiao_force_graphic_no_cover", None)
    if isinstance(inner, dict):
        inner = {k: v for k, v in inner.items() if k not in ("graphic_no_cover", "no_cover")}
        if inner:
            publish_opts["toutiao"] = inner
        else:
            publish_opts.pop("toutiao", None)
    logger.info(
        "[PUBLISH-API] 头条：主素材为%s(%s)，已移除 options 中的无封面开关",
        "图" if is_img else "视频",
        suf,
    )


SUPPORTED_PLATFORMS = {
    "douyin": {"name": "抖音", "login_url": "https://creator.douyin.com"},
    "bilibili": {"name": "B站", "login_url": "https://member.bilibili.com"},
    "xiaohongshu": {"name": "小红书", "login_url": "https://creator.xiaohongshu.com"},
    "kuaishou": {"name": "快手", "login_url": "https://cp.kuaishou.com"},
    "toutiao": {"name": "今日头条", "login_url": "https://mp.toutiao.com/auth/page/login?redirect_url=JTJGcHJvZmlsZV92NCUyRg=="},
    "douyin_shop": {"name": "抖店", "login_url": "https://fxg.jinritemai.com/"},
    "xiaohongshu_shop": {"name": "小红书店铺", "login_url": "https://ark.xiaohongshu.com/"},
    "alibaba1688": {"name": "1688", "login_url": "https://work.1688.com/"},
    "taobao": {"name": "淘宝", "login_url": "https://seller.taobao.com/"},
    "pinduoduo": {"name": "拼多多", "login_url": "https://mms.pinduoduo.com/"},
}

DOUYIN_WORKBENCH_URL = "https://www.douyin.com/jingxuan"

def _ensure_tiny_mp4(path: Path) -> Path:
    # A tiny MP4 (base64) for dry-run uploads.
    import base64
    tiny_b64 = (
        "AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAACMbW9vdgAAAGxtdmhk"
        "AAAAAAAAAAAAAAAAAAAAAAAD6AAAA+gAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAA"
        "AAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIAAAIVdHJhawAA"
        "AFx0a2hkAAAAAAAAAAAAAAAAAAAAAAABAAAAAAAAA+gAAAAAAAAAAAAAAAAEAAAAA"
        "AAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAIAAAABAAAAAQAAAAAAJGVkdHMAAAAc"
        "ZWxzdAAAAAAAAAABAAAD6AAAA+gAAAAAAAEabWRpYQAAACBtZGhkAAAAAAAAAAAA"
        "AAAAAAAAAAAyAAAAMgAAVcQAAAAAAC1oZGxyAAAAAAAAAAB2aWRlAAAAAAAAAAAA"
        "AAAAAFZpZGVvSGFuZGxlcgAAAAE3bWluZgAAABR2bWhkAAAAAAAAAAAAAAAALGRp"
        "bmYAAAAcZHJlZgAAAAAAAAABAAAADHVybCAAAAABAAAAK3N0YmwAAAAVc3RzZAAA"
        "AAEAAAANYXZjMQAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAUYXZjQwEB/4QAF2JtZGF0AAAAAA=="
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return path
    path.write_bytes(base64.b64decode(tiny_b64))
    return path


# ── Account CRUD ──────────────────────────────────────────────────

class AddAccountReq(BaseModel):
    platform: str
    nickname: str
    proxy_server: Optional[str] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None
    user_agent: Optional[str] = None


class DouyinSearchCollectReq(BaseModel):
    keyword: str
    account_id: Optional[int] = None
    max_results: Optional[int] = 30
    mode: Optional[str] = "api"


class DouyinVideoCustomersReq(BaseModel):
    account_id: Optional[int] = None
    video_url: Optional[str] = None
    aweme_id: Optional[str] = None
    max_comments: Optional[int] = 100
    ai_filter_enabled: Optional[bool] = True
    comment_direction: Optional[str] = None
    comment_filter_strategy: Optional[str] = "prompt"


class DouyinWorkbenchConfigReq(BaseModel):
    ai_filter_enabled: Optional[bool] = None
    comment_direction: Optional[str] = None
    comment_filter_strategy: Optional[str] = None
    comment_max_comments: Optional[int] = None


class DouyinMessageTarget(BaseModel):
    nickname: Optional[str] = None
    author: Optional[str] = None
    profile_url: Optional[str] = None
    sec_user_id: Optional[str] = None
    sec_uid: Optional[str] = None


class DouyinMessageSendReq(BaseModel):
    account_id: Optional[int] = None
    message: str
    targets: List[DouyinMessageTarget] = []


def _douyin_comment_text(row: Dict[str, Any]) -> str:
    return str(row.get("content") or row.get("comment") or row.get("text") or row.get("latest_comment") or "").strip()


def _douyin_comment_user(row: Dict[str, Any]) -> str:
    return str(row.get("username") or row.get("nickname") or row.get("author") or "").strip()


def _douyin_comment_key(row: Dict[str, Any]) -> str:
    return str(
        row.get("sec_user_id")
        or row.get("sec_uid")
        or row.get("user_xsec_token")
        or row.get("profile_url")
        or row.get("uid")
        or row.get("user_id")
        or _douyin_comment_user(row)
        or row.get("comment_id")
        or ""
    ).strip()


def _extract_json_object_from_text(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("模型返回为空")
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
    if fenced:
        blob = fenced.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型返回中未找到 JSON")
        blob = raw[start : end + 1]
    data = json.loads(blob)
    if not isinstance(data, dict):
        raise ValueError("模型 JSON 根节点不是对象")
    return data


def _normalize_douyin_filter_ref(ref: Dict[str, Any], comments: List[Dict[str, Any]], fallback_index: int) -> Optional[Dict[str, Any]]:
    try:
        comment_index = int(ref.get("comment_index") or ref.get("index") or fallback_index)
    except Exception:
        comment_index = fallback_index
    if comment_index < 1 or comment_index > len(comments):
        return None
    source = comments[comment_index - 1] if isinstance(comments[comment_index - 1], dict) else {}
    content = _douyin_comment_text(source)
    if not content:
        return None
    nickname = _douyin_comment_user(source)
    out = {
        "comment_index": comment_index,
        "username": nickname,
        "nickname": nickname,
        "author": nickname,
        "user_id": str(source.get("user_id") or source.get("uid") or "").strip(),
        "uid": str(source.get("uid") or source.get("user_id") or "").strip(),
        "user_xsec_token": str(source.get("user_xsec_token") or "").strip(),
        "sec_user_id": str(source.get("sec_user_id") or source.get("sec_uid") or "").strip(),
        "sec_uid": str(source.get("sec_uid") or source.get("sec_user_id") or "").strip(),
        "comment_id": str(source.get("comment_id") or "").strip(),
        "comment": content,
        "content": content,
        "latest_comment": content,
        "comment_time": str(source.get("comment_time") or source.get("create_time") or "").strip(),
        "like_count": source.get("like_count", source.get("digg_count", "")),
        "digg_count": source.get("digg_count", source.get("like_count", 0)),
        "reply_count": source.get("reply_count", source.get("reply_comment_total", "")),
        "profile_url": str(source.get("profile_url") or "").strip(),
        "avatar_url": str(source.get("avatar_url") or "").strip(),
        "intent_level": str(ref.get("intent_level") or "high").strip() or "high",
        "reason": str(ref.get("reason") or "").strip(),
        "score": ref.get("score", 0.8),
    }
    return out


def _dedupe_douyin_filter_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        key = _douyin_comment_key(row) or f"{row.get('comment_index')}|{_douyin_comment_text(row)}"
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _douyin_prompt_fallback_filter(comments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keywords = (
        "多少钱", "价格", "费用", "收费", "报价", "套餐",
        "怎么买", "怎么下单", "怎么购买", "怎么报名", "哪里报名", "想报名", "想买", "下单",
        "怎么合作", "合作", "商务合作", "加盟", "代理",
        "怎么联系", "联系方式", "求联系方式", "电话", "微信", "私信", "私聊", "对接",
        "咨询", "想咨询", "想了解", "了解一下", "详细聊聊", "给个方案", "有没有方案",
        "我需要", "我想", "适合我吗", "适不适合我", "能不能做", "可以做吗",
        "感兴趣", "有兴趣", "想试试", "想做", "怎么弄", "怎么搞", "怎么开始",
        "想入手", "入手", "能下手吗", "能不能买", "可以买吗", "可以入吗",
        "有没有", "有吗", "怎么选", "推荐一下", "回复我", "回我一下",
    )
    refs = []
    for index, row in enumerate(comments, start=1):
        compact = "".join(_douyin_comment_text(row).split())
        if compact and any(word in compact for word in keywords):
            refs.append({"comment_index": index, "intent_level": "medium", "reason": "抖音意向关键词兜底筛选", "score": 0.6})
    return [_normalize_douyin_filter_ref(ref, comments, idx) for idx, ref in enumerate(refs, start=1) if _normalize_douyin_filter_ref(ref, comments, idx)]


def _douyin_reverse_fallback_filter(comments: List[Dict[str, Any]], post_title: str = "") -> List[Dict[str, Any]]:
    trivial_exact = {
        "哈哈", "哈哈哈", "呵呵", "哦", "嗯", "好的", "收到", "来了", "路过", "打卡", "支持",
        "不错", "真好", "牛", "厉害", "赞", "好", "好看", "看看", "学到了", "收藏了",
        "先收藏", "滴滴", "在吗", "回我", "回一下", "回复一下",
    }
    off_topic_exact = {"吃了吗", "吃饭了吗", "穿什么", "穿啥", "在干嘛", "干嘛呢", "睡了吗", "早安", "晚安"}
    abusive_keywords = ("骗子", "骗人", "垃圾", "滚", "有病", "智商税", "脑残", "傻", "装逼", "坑人", "恶心")
    intent_keywords = ("价格", "费用", "收费", "报价", "多少钱", "怎么卖", "怎么买", "合作", "联系方式", "联系", "微信", "私信", "咨询", "了解", "想买", "想要", "需要", "推荐", "可以吗", "能吗", "适合")
    title_tokens = set(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,8}", str(post_title or "").lower()))
    refs = []
    for index, row in enumerate(comments, start=1):
        text = _douyin_comment_text(row)
        compact = "".join(text.split())
        if not compact or compact in trivial_exact or compact in off_topic_exact:
            continue
        if any(word in compact for word in abusive_keywords):
            continue
        text_only = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]", "", compact)
        if len(text_only) <= 1:
            continue
        has_intent = any(word in compact for word in intent_keywords)
        has_topic = (not title_tokens) or any(token in compact.lower() for token in title_tokens)
        if not has_intent and not has_topic:
            continue
        refs.append({"comment_index": index, "intent_level": "high", "reason": "保留有效互动评论", "score": 0.65 if not has_intent else 0.78})
    rows = [_normalize_douyin_filter_ref(ref, comments, idx) for idx, ref in enumerate(refs, start=1)]
    return [row for row in rows if row]


async def _filter_douyin_comments_for_workbench(
    *,
    comments: List[Dict[str, Any]],
    post_title: str,
    direction: str,
    strategy: str,
    request: Request,
) -> Dict[str, Any]:
    comments = [row for row in (comments or []) if isinstance(row, dict) and _douyin_comment_text(row)]
    strategy = _normalize_douyin_comment_filter_strategy(strategy)
    if not comments:
        return {"high_intent_users": [], "fallback_used": False, "message": "没有可筛选的评论"}

    fallback = _douyin_reverse_fallback_filter if strategy == "reverse" else lambda rows, title="": _douyin_prompt_fallback_filter(rows)
    raw_token = _bearer_from_request(request)
    asb = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    model = (getattr(settings, "lobster_default_sutui_chat_model", None) or "deepseek-chat").strip() or "deepseek-chat"
    if not raw_token or not asb:
        rows = _dedupe_douyin_filter_rows(fallback(comments, post_title))
        return {"high_intent_users": rows, "fallback_used": True, "message": "未获取到登录 Token，已使用本地规则兜底筛选。"}

    system_prompt = (
        "你是一个抖音评论反向筛选助手。只保留和当前视频主题相关、且有意义的互动评论；排除无意义、灌水、攻击、广告、跑题闲聊。"
        if strategy == "reverse"
        else "你是一个抖音评论筛选助手。必须以用户提供的精准客户筛选提示词为最高优先级，只判断评论是不是精准客户，符合就返回。"
    )
    lines = []
    for index, row in enumerate(comments, start=1):
        lines.append(f"{index}. 用户：{_douyin_comment_user(row) or '未知'}；评论：{_douyin_comment_text(row)}")
    user_prompt = (
        f"视频标题：{post_title or '未提供'}\n\n"
        f"精准客户筛选提示词：{direction or DOUYIN_INTENT_DIRECTION_DEFAULT}\n\n"
        "评论列表：\n" + "\n".join(lines) + "\n\n"
        "请严格返回 JSON，格式为：{\"high_intent_refs\":[{\"comment_index\":1,\"intent_level\":\"high\",\"reason\":\"理由\",\"score\":0.8}]}。"
        "comment_index 必须来自评论列表序号；如果没有符合条件的评论，返回空数组。"
    )
    try:
        async with httpx.AsyncClient(timeout=45.0, trust_env=False) as client:
            resp = await client.post(
                f"{asb}/api/sutui-chat/completions",
                json={
                    "model_name": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                    "temperature": 0.1,
                },
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {raw_token}"},
            )
            resp.raise_for_status()
        payload = resp.json() if resp.content else {}
        content = ""
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") if isinstance(choices[0], dict) else {}
            content = str((msg or {}).get("content") or choices[0].get("text") or "")
        if not content:
            content = str(payload.get("content") or payload.get("text") or "")
        parsed = _extract_json_object_from_text(content)
        refs = parsed.get("high_intent_refs") or parsed.get("high_intent_users") or []
        if not isinstance(refs, list):
            refs = []
        rows = []
        for index, ref in enumerate(refs, start=1):
            if not isinstance(ref, dict):
                continue
            row = _normalize_douyin_filter_ref(ref, comments, index)
            if row:
                rows.append(row)
        return {"high_intent_users": _dedupe_douyin_filter_rows(rows), "fallback_used": False, "message": "AI 筛选完成"}
    except Exception as exc:
        logger.warning("[DOUYIN-WORKBENCH] AI filter failed, fallback used: %s", exc)
        rows = _dedupe_douyin_filter_rows(fallback(comments, post_title))
        return {"high_intent_users": rows, "fallback_used": True, "message": "AI 筛选暂不可用，已使用本地规则兜底筛选。"}


def _merge_douyin_high_intent_into_customers(
    customers: List[Dict[str, Any]],
    high_intent_users: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_key: Dict[str, Dict[str, Any]] = {}
    for row in customers or []:
        if not isinstance(row, dict):
            continue
        key = _douyin_comment_key(row)
        if key:
            by_key[key] = dict(row)

    for item in high_intent_users or []:
        if not isinstance(item, dict):
            continue
        key = _douyin_comment_key(item)
        if not key:
            continue
        current = by_key.get(key, {})
        current.update(
            {
                "id": current.get("id") or key,
                "nickname": current.get("nickname") or item.get("nickname") or item.get("username") or item.get("author") or "",
                "author": current.get("author") or item.get("author") or item.get("nickname") or item.get("username") or "",
                "uid": current.get("uid") or item.get("uid") or item.get("user_id") or "",
                "sec_user_id": current.get("sec_user_id") or item.get("sec_user_id") or item.get("sec_uid") or "",
                "profile_url": current.get("profile_url") or item.get("profile_url") or "",
                "avatar_url": current.get("avatar_url") or item.get("avatar_url") or "",
                "latest_comment": current.get("latest_comment") or item.get("latest_comment") or item.get("comment") or item.get("content") or "",
                "comment_count": current.get("comment_count") or 1,
                "digg_count": current.get("digg_count") or item.get("digg_count") or item.get("like_count") or 0,
                "is_high_intent": True,
                "intent_level": item.get("intent_level") or "high",
                "intent_reason": item.get("reason") or "",
                "intent_score": item.get("score", ""),
            }
        )
        by_key[key] = current
    return list(by_key.values())


@router.get("/api/accounts", summary="列出发布账号")
def list_accounts(
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    rows = db.query(PublishAccount).filter(
        PublishAccount.user_id == current_user.id,
    ).order_by(PublishAccount.created_at.desc()).all()
    acct_ids = [a.id for a in rows]
    last_sync_map = {}
    sched_map = {}
    try:
        if acct_ids:
            subq = (
                db.query(
                    CreatorContentSnapshot.account_id.label("aid"),
                    func.max(CreatorContentSnapshot.id).label("mid"),
                )
                .filter(
                    CreatorContentSnapshot.user_id == current_user.id,
                    CreatorContentSnapshot.account_id.in_(acct_ids),
                )
                .group_by(CreatorContentSnapshot.account_id)
                .subquery()
            )
            snap_rows = (
                db.query(CreatorContentSnapshot)
                .join(
                    subq,
                    (CreatorContentSnapshot.account_id == subq.c.aid)
                    & (CreatorContentSnapshot.id == subq.c.mid),
                )
                .all()
            )
            for s in snap_rows:
                n = len(s.items) if s.items else 0
                last_sync_map[s.account_id] = {
                    "fetched_at": isoformat_utc(s.fetched_at),
                    "item_count": n,
                    "sync_error": s.sync_error,
                }
        if acct_ids:
            for sch in (
                db.query(PublishAccountCreatorSchedule)
                .filter(
                    PublishAccountCreatorSchedule.user_id == current_user.id,
                    PublishAccountCreatorSchedule.account_id.in_(acct_ids),
                )
                .all()
            ):
                sk = (getattr(sch, "schedule_kind", None) or "image").strip().lower()
                if sk not in ("image", "video"):
                    sk = "image"
                pm = (getattr(sch, "schedule_publish_mode", None) or "immediate").strip().lower()
                if pm not in ("immediate", "review"):
                    pm = "immediate"
                sched_map[sch.account_id] = {
                    "enabled": sch.enabled,
                    "interval_minutes": getattr(sch, "interval_minutes", None) or 60,
                    "next_run_at": isoformat_utc(getattr(sch, "next_run_at", None)),
                    "schedule_kind": sk,
                    "video_source_asset_id": getattr(sch, "video_source_asset_id", None),
                    "schedule_publish_mode": pm,
                    "review_variant_count": int(getattr(sch, "review_variant_count", None) or 3),
                    "review_first_eta_at": isoformat_utc(
                        getattr(sch, "review_first_eta_at", None)
                    ),
                    "review_drafts_json": getattr(sch, "review_drafts_json", None),
                    "review_confirmed": bool(getattr(sch, "review_confirmed", False)),
                    "review_selected_slot": int(getattr(sch, "review_selected_slot", None) or 0),
                }
    except OperationalError as e:
        logger.warning("[PUBLISH-API] 创作者快照/定时表不可用，仅返回账号列表（请重启后端以建表）: %s", e)
    return {
        "accounts": [
            {
                "id": a.id,
                "platform": a.platform,
                "platform_name": SUPPORTED_PLATFORMS.get(a.platform, {}).get("name", a.platform),
                "nickname": a.nickname,
                "status": a.status,
                "last_login": isoformat_utc(a.last_login),
                "created_at": a.created_at.isoformat() if a.created_at else "",
                "last_creator_sync": last_sync_map.get(a.id),
                "creator_schedule": sched_map.get(a.id),
            }
            for a in rows
        ],
        "platforms": [
            {"id": k, "name": v["name"]} for k, v in SUPPORTED_PLATFORMS.items()
        ],
    }


@router.post("/api/accounts", summary="添加发布账号")
def add_account(
    body: AddAccountReq,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    if body.platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(400, detail=f"不支持的平台: {body.platform}")

    nick = body.nickname.strip()
    if not nick:
        raise HTTPException(400, detail="账号昵称不能为空")

    browser: dict = {}
    ps = (body.proxy_server or "").strip()
    if ps:
        px: dict = {"server": ps}
        u = (body.proxy_username or "").strip()
        p = body.proxy_password or ""
        if u or p:
            if not u or not p:
                raise HTTPException(
                    400, detail="代理用户名与密码须同时填写或同时留空"
                )
            px["username"] = u
            px["password"] = str(p)
        browser["proxy"] = px
    ua_in = (body.user_agent or "").strip()
    if ua_in:
        browser["user_agent"] = ua_in
    meta = {"browser": browser} if browser else None
    if meta:
        try:
            browser_options_from_publish_meta(meta)
        except ValueError as e:
            raise HTTPException(400, detail=str(e))

    profile_dir = BROWSER_DATA_DIR / f"{body.platform}_{nick}"
    profile_dir.mkdir(parents=True, exist_ok=True)

    acct = PublishAccount(
        user_id=current_user.id,
        platform=body.platform,
        nickname=nick,
        status="pending",
        browser_profile=str(profile_dir),
        meta=meta,
    )
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return {
        "id": acct.id,
        "platform": acct.platform,
        "nickname": acct.nickname,
        "status": acct.status,
        "message": f"账号已添加，请点击「登录」完成扫码",
    }


@router.post("/api/accounts/{account_id}/login", summary="启动浏览器登录")
async def start_login(
    account_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = db.query(PublishAccount).filter(
        PublishAccount.id == account_id,
        PublishAccount.user_id == current_user.id,
    ).first()
    if not acct:
        raise HTTPException(404, detail="账号不存在")

    platform_info = SUPPORTED_PLATFORMS.get(acct.platform, {})
    login_url = platform_info.get("login_url", "")

    try:
        from publisher.browser_pool import open_login_browser

        bopts = browser_options_from_publish_meta(acct.meta)
        _ = await open_login_browser(
            profile_dir=acct.browser_profile or str(BROWSER_DATA_DIR / f"{acct.platform}_{acct.nickname}"),
            login_url=login_url,
            platform=acct.platform,
            browser_options=bopts,
        )
        # Don't block/poll here; don't pop interruptive messages.
        acct.status = "pending"
        db.commit()
        return {"ok": True, "status": "pending", "message": "已打开浏览器，请扫码登录（完成后手动关闭窗口）"}
    except Exception as e:
        logger.exception("Login browser failed")
        return {"ok": False, "status": "error", "message": str(e)}


@router.post("/api/accounts/{account_id}/open-browser", summary="打开账号浏览器")
async def open_browser(
    account_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = db.query(PublishAccount).filter(
        PublishAccount.id == account_id,
        PublishAccount.user_id == current_user.id,
    ).first()
    if not acct:
        raise HTTPException(404, detail="账号不存在")

    platform_info = SUPPORTED_PLATFORMS.get(acct.platform, {})
    login_url = platform_info.get("login_url", "")
    profile_dir = acct.browser_profile or str(BROWSER_DATA_DIR / f"{acct.platform}_{acct.nickname}")

    try:
        from publisher.browser_pool import open_and_check_browser

        bopts = browser_options_from_publish_meta(acct.meta)
        result = await open_and_check_browser(
            profile_dir=profile_dir,
            login_url=login_url,
            platform=acct.platform,
            browser_options=bopts,
        )
        logged_in = result.get("logged_in", False)
        if logged_in and acct.status != "active":
            acct.status = "active"
            acct.last_login = datetime.utcnow()
            db.commit()
        return {"ok": True, "logged_in": logged_in, "message": result.get("message", "")}
    except Exception as e:
        logger.exception("Open browser failed")
        return {"ok": False, "logged_in": False, "message": str(e)}


@router.get("/api/accounts/{account_id}/login-status", summary="检查登录状态")
async def check_login_status(
    account_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = db.query(PublishAccount).filter(
        PublishAccount.id == account_id,
        PublishAccount.user_id == current_user.id,
    ).first()
    if not acct:
        raise HTTPException(404, detail="账号不存在")

    try:
        from publisher.browser_pool import check_browser_login

        bopts = browser_options_from_publish_meta(acct.meta)
        logged_in = await check_browser_login(
            profile_dir=acct.browser_profile or str(BROWSER_DATA_DIR / f"{acct.platform}_{acct.nickname}"),
            platform=acct.platform,
            browser_options=bopts,
        )
        if logged_in and acct.status != "active":
            acct.status = "active"
            acct.last_login = datetime.utcnow()
            db.commit()
        return {"logged_in": logged_in, "message": "已登录" if logged_in else "未登录，请在浏览器中扫码"}
    except Exception as e:
        logger.exception("Check login status failed")
        return {"logged_in": False, "message": str(e)}


@router.post("/api/accounts/{account_id}/douyin-workbench/open", summary="打开抖音获客工作台登录页")
async def open_douyin_workbench_browser(
    account_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = db.query(PublishAccount).filter(
        PublishAccount.id == account_id,
        PublishAccount.user_id == current_user.id,
    ).first()
    if not acct:
        raise HTTPException(404, detail="账号不存在")
    if acct.platform != "douyin":
        raise HTTPException(400, detail="该入口只支持抖音账号")

    profile_dir = acct.browser_profile or str(BROWSER_DATA_DIR / f"{acct.platform}_{acct.nickname}")
    try:
        from publisher.browser_pool import open_douyin_front_browser

        bopts = douyin_workbench_browser_options_from_publish_meta(acct.meta)
        result = await open_douyin_front_browser(
            profile_dir=profile_dir,
            url=DOUYIN_WORKBENCH_URL,
            browser_options=bopts,
        )
        if result.get("ok"):
            logged_in = bool(result.get("logged_in"))
            acct.status = "active" if logged_in else "pending"
            if logged_in:
                acct.last_login = datetime.utcnow()
            db.commit()
        return result
    except Exception as e:
        logger.exception("Open douyin workbench browser failed")
        return {"ok": False, "logged_in": False, "message": str(e)}


@router.get("/api/accounts/{account_id}/douyin-workbench/login-status", summary="检查抖音获客工作台登录状态")
async def check_douyin_workbench_login_status(
    account_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = db.query(PublishAccount).filter(
        PublishAccount.id == account_id,
        PublishAccount.user_id == current_user.id,
    ).first()
    if not acct:
        raise HTTPException(404, detail="账号不存在")
    if acct.platform != "douyin":
        raise HTTPException(400, detail="该入口只支持抖音账号")

    profile_dir = acct.browser_profile or str(BROWSER_DATA_DIR / f"{acct.platform}_{acct.nickname}")
    try:
        from publisher.browser_pool import check_douyin_front_login

        bopts = douyin_workbench_browser_options_from_publish_meta(acct.meta)
        result = await check_douyin_front_login(
            profile_dir=profile_dir,
            url=DOUYIN_WORKBENCH_URL,
            browser_options=bopts,
            headless=True,
        )
        logged_in = bool(result.get("logged_in"))
        acct.status = "active" if logged_in else "pending"
        if logged_in:
            acct.last_login = datetime.utcnow()
        db.commit()
        return {
            "logged_in": logged_in,
            "status": acct.status,
            "message": result.get("message") or ("抖音前台已登录" if logged_in else "抖音前台未登录"),
            "detail": {
                "cookie": bool(result.get("cookie")),
                "login_prompt": bool(result.get("login_prompt")),
                "path": result.get("path") or "",
                "entry_url": DOUYIN_WORKBENCH_URL,
            },
        }
    except Exception as e:
        logger.exception("Check douyin workbench login status failed")
        return {"logged_in": False, "status": "error", "message": str(e)}


@router.delete("/api/accounts/{account_id}", summary="删除发布账号")
def delete_account(
    account_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = db.query(PublishAccount).filter(
        PublishAccount.id == account_id,
        PublishAccount.user_id == current_user.id,
    ).first()
    if not acct:
        raise HTTPException(404, detail="账号不存在")
    import shutil
    if acct.browser_profile:
        p = Path(acct.browser_profile)
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    db.delete(acct)
    db.commit()
    return {"ok": True}


# ── Publish tasks ─────────────────────────────────────────────────

class PublishReq(BaseModel):
    # 可选：今日头条无封面纯文 + options.toutiao_graphic_no_cover 时无需真实素材
    asset_id: Optional[str] = None
    account_id: Optional[int] = None
    account_nickname: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    # None：抖音/头条等未传 title+description 时可能自动 AI；小红书见 create_publish_task 校验（默认不自动 AI）。
    # True：强制 AI 生成（失败则 503）；False：仅用素材/入参补全，不调用模型。
    ai_publish_copy: Optional[bool] = None
    # platform-specific options, e.g. douyin schedule/visibility/location/yellow_cart；
    # 抖音视频：douyin_cover_mode = smart | upload | manual（见 create_publish_task 校验）；
    # douyin_manual_cover_wait_sec：manual 时轮询秒数上限（默认 600）。
    # 头条：无图纯文可 toutiao_graphic_no_cover=true；主素材为图片时 API 会忽略该开关走单图封面，
    # 除非 toutiao_force_graphic_no_cover=true（极少用）。
    options: Optional[dict] = None
    # 可选第二图片；头条视频作单独封面；头条图文时主 asset 即「封面图」，此项可作补充配图
    cover_asset_id: Optional[str] = None


@router.post("/api/douyin/dryrun", summary="抖音发布 dry-run（走到发布前一步）")
async def douyin_dryrun(
    account_nickname: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = db.query(PublishAccount).filter(
        PublishAccount.user_id == current_user.id,
        PublishAccount.platform == "douyin",
        PublishAccount.nickname == account_nickname.strip(),
    ).first()
    if not acct or not acct.browser_profile:
        raise HTTPException(404, detail="抖音账号不存在或未配置浏览器 profile")

    # Generate a tiny local MP4 for upload dry-run
    from .assets import ASSETS_DIR
    mp4_path = _ensure_tiny_mp4(Path(ASSETS_DIR) / "dryrun_tiny.mp4")

    try:
        from publisher.browser_pool import dryrun_douyin_upload_in_context

        bopts = browser_options_from_publish_meta(acct.meta)
        result = await dryrun_douyin_upload_in_context(
            profile_dir=acct.browser_profile,
            file_path=str(mp4_path),
            browser_options=bopts,
        )
        return {"ok": True, "result": result}
    except Exception as e:
        logger.exception("Douyin dryrun failed")
        return {"ok": False, "error": str(e)}


@router.get("/api/douyin/workbench/config", summary="抖音工作台：读取采集默认配置")
async def douyin_workbench_get_config(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    _ = current_user
    return {"code": 200, **_load_douyin_workbench_config()}


@router.post("/api/douyin/workbench/config", summary="抖音工作台：保存采集默认配置")
async def douyin_workbench_save_config(
    body: DouyinWorkbenchConfigReq,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    _ = current_user
    patch = body.model_dump(exclude_unset=True) if hasattr(body, "model_dump") else body.dict(exclude_unset=True)
    return {"code": 200, "msg": "抖音采集默认配置已保存。", **_save_douyin_workbench_config(patch)}


@router.post("/api/douyin/search/collect", summary="抖音工作台：搜索采集视频线索")
async def douyin_workbench_search_collect(
    body: DouyinSearchCollectReq,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    keyword = (body.keyword or "").strip()
    search_mode = _normalize_douyin_search_mode(body.mode)
    max_results = max(1, min(int(body.max_results or 30), 100))
    if not keyword:
        return {"code": 400, "msg": "请输入抖音搜索关键词", "data": [], "total": 0}
    if search_mode == "api":
        try:
            result = await _tikhub_douyin_keyword_search(keyword, max_results)
            return {
                "code": 200,
                "msg": result.get("message") or f"抖音搜索完成，共 {int(result.get('total') or 0)} 条结果。",
                "data": result.get("data") or [],
                "total": int(result.get("total") or 0),
                "account_id": "api",
                "search_mode": "api",
                "request_id": result.get("request_id") or "",
                "cache_url": result.get("cache_url") or "",
            }
        except Exception as e:
            logger.exception("Douyin workbench api search collect failed")
            logger.warning("Douyin workbench search falling back to script mode: %s", e)
            search_mode = "script"
    query = db.query(PublishAccount).filter(
        PublishAccount.user_id == current_user.id,
        PublishAccount.platform == "douyin",
    )
    if body.account_id:
        query = query.filter(PublishAccount.id == int(body.account_id))
    else:
        query = query.order_by(PublishAccount.last_login.desc().nullslast(), PublishAccount.created_at.desc())
    acct = query.first()
    if not acct:
        return {
            "code": 400,
            "type": "no_online_account",
            "msg": "没有可用的抖音账号，请先在左侧添加并登录。",
            "data": [],
            "total": 0,
        }

    profile_dir = acct.browser_profile or str(BROWSER_DATA_DIR / f"{acct.platform}_{acct.nickname}")
    try:
        from publisher.browser_pool import collect_douyin_search_results

        bopts = douyin_workbench_browser_options_from_publish_meta(acct.meta)
        result = await collect_douyin_search_results(
            profile_dir=profile_dir,
            keyword=keyword,
            max_results=max_results,
            browser_options=bopts,
        )
        if not result.get("ok"):
            return {
                "code": 400 if not result.get("logged_in") else 500,
                "type": "no_online_account" if not result.get("logged_in") else "search_failed",
                "msg": result.get("message") or "抖音搜索采集失败",
                "data": [],
                "total": 0,
                "account_id": acct.id,
            }
        acct.status = "active"
        acct.last_login = datetime.utcnow()
        db.commit()
        return {
            "code": 200,
            "msg": result.get("message") or f"抖音搜索完成，共 {int(result.get('total') or 0)} 条结果。",
            "data": result.get("data") or [],
            "total": int(result.get("total") or 0),
            "account_id": acct.id,
            "search_url": result.get("search_url") or "",
            "search_mode": "script",
        }
    except Exception as e:
        logger.exception("Douyin workbench search collect failed")
        return {
            "code": 500,
            "msg": f"抖音搜索采集失败：{e}",
            "data": [],
            "total": 0,
            "account_id": acct.id,
            "search_mode": "script",
        }


@router.post("/api/douyin/video/customers", summary="抖音工作台：协议模式采集视频评论客户")
async def douyin_workbench_video_customers(
    body: DouyinVideoCustomersReq,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    video_url = (body.video_url or "").strip()
    aweme_id = (body.aweme_id or "").strip()
    if not video_url and aweme_id:
        video_url = f"https://www.douyin.com/video/{aweme_id}"
    if not video_url:
        return {"code": 400, "msg": "请选择一个要采集客户的视频", "customers": [], "comments": []}

    query = db.query(PublishAccount).filter(
        PublishAccount.user_id == current_user.id,
        PublishAccount.platform == "douyin",
    )
    if body.account_id:
        query = query.filter(PublishAccount.id == int(body.account_id))
    else:
        query = query.order_by(PublishAccount.last_login.desc().nullslast(), PublishAccount.created_at.desc())
    acct = query.first()
    if not acct:
        return {
            "code": 400,
            "type": "no_online_account",
            "msg": "没有可用的抖音账号，请先在左侧添加并登录。",
            "customers": [],
            "comments": [],
        }

    profile_dir = acct.browser_profile or str(BROWSER_DATA_DIR / f"{acct.platform}_{acct.nickname}")
    try:
        from publisher.browser_pool import collect_douyin_video_customers_protocol

        default_config = _load_douyin_workbench_config()
        ai_filter_enabled = bool(body.ai_filter_enabled if body.ai_filter_enabled is not None else default_config.get("ai_filter_enabled", True))
        comment_direction = (body.comment_direction or default_config.get("comment_direction") or DOUYIN_INTENT_DIRECTION_DEFAULT).strip()
        comment_filter_strategy = _normalize_douyin_comment_filter_strategy(body.comment_filter_strategy or default_config.get("comment_filter_strategy"))
        bopts = douyin_workbench_browser_options_from_publish_meta(acct.meta)
        result = await collect_douyin_video_customers_protocol(
            profile_dir=profile_dir,
            video_url=video_url,
            max_comments=int(body.max_comments or 100),
            browser_options=bopts,
        )
        if not result.get("ok"):
            return {
                "code": 400 if not result.get("logged_in") else 500,
                "type": "no_online_account" if not result.get("logged_in") else "collect_failed",
                "msg": result.get("message") or "采集视频客户失败",
                "customers": [],
                "comments": [],
                "account_id": acct.id,
            }
        acct.status = "active"
        acct.last_login = datetime.utcnow()
        db.commit()
        raw_comments = result.get("comments") if isinstance(result.get("comments"), list) else []
        normalized_comments: List[Dict[str, Any]] = []
        for row in raw_comments:
            if not isinstance(row, dict):
                continue
            normalized = dict(row)
            normalized.setdefault("content", _douyin_comment_text(row))
            normalized.setdefault("comment", _douyin_comment_text(row))
            normalized.setdefault("username", _douyin_comment_user(row))
            normalized.setdefault("author", _douyin_comment_user(row))
            normalized.setdefault("sec_user_id", str(row.get("sec_user_id") or row.get("sec_uid") or "").strip())
            normalized_comments.append(normalized)
        customers = result.get("customers") or []
        high_intent_users: List[Dict[str, Any]] = []
        ai_filter_result = {
            "enabled": ai_filter_enabled,
            "strategy": comment_filter_strategy,
            "prompt": comment_direction,
            "fallback_used": False,
            "message": "",
        }
        if ai_filter_enabled:
            filter_result = await _filter_douyin_comments_for_workbench(
                comments=normalized_comments,
                post_title=video_url,
                direction=comment_direction,
                strategy=comment_filter_strategy,
                request=request,
            )
            high_intent_users = filter_result.get("high_intent_users") or []
            ai_filter_result.update(
                {
                    "fallback_used": bool(filter_result.get("fallback_used")),
                    "message": filter_result.get("message") or "",
                    "precise_count": len(high_intent_users),
                }
            )
            customers = _merge_douyin_high_intent_into_customers(customers, high_intent_users)
        return {
            "code": 200,
            "msg": result.get("message") or "视频客户采集完成",
            "customers": customers,
            "comments": normalized_comments,
            "high_intent_users": high_intent_users,
            "precise_customers": high_intent_users,
            "total_customers": len(customers),
            "total_comments": len(normalized_comments),
            "total_high_intent": len(high_intent_users),
            "ai_filter": ai_filter_result,
            "account_id": acct.id,
            "aweme_id": result.get("aweme_id") or aweme_id,
            "video_url": result.get("video_url") or video_url,
        }
    except Exception as e:
        logger.exception("Douyin workbench video customers failed")
        return {"code": 500, "msg": f"采集视频客户失败：{e}", "customers": [], "comments": [], "account_id": acct.id}


@router.post("/api/douyin/message/send", summary="抖音工作台：协议模式发送私信")
async def douyin_workbench_message_send(
    body: DouyinMessageSendReq,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    message = (body.message or "").strip()
    if not message:
        return {"code": 400, "msg": "请先填写私信内容。", "results": []}
    targets = [item.dict() for item in (body.targets or [])]
    cleaned_targets = []
    seen = set()
    for item in targets:
        profile_url = (item.get("profile_url") or "").strip()
        sec_user_id = (item.get("sec_user_id") or item.get("sec_uid") or "").strip()
        key = profile_url or sec_user_id
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned_targets.append(item)
    if not cleaned_targets:
        return {"code": 400, "msg": "请先选择有主页链接或 sec_user_id 的客户。", "results": []}

    query = db.query(PublishAccount).filter(
        PublishAccount.user_id == current_user.id,
        PublishAccount.platform == "douyin",
    )
    if body.account_id:
        query = query.filter(PublishAccount.id == int(body.account_id))
    else:
        query = query.order_by(PublishAccount.last_login.desc().nullslast(), PublishAccount.created_at.desc())
    acct = query.first()
    if not acct:
        return {
            "code": 400,
            "type": "no_online_account",
            "msg": "没有可用的抖音账号，请先在左侧添加并登录。",
            "results": [],
        }

    profile_dir = acct.browser_profile or str(BROWSER_DATA_DIR / f"{acct.platform}_{acct.nickname}")
    try:
        from publisher.browser_pool import send_douyin_private_messages_protocol

        bopts = douyin_workbench_browser_options_from_publish_meta(acct.meta)
        result = await send_douyin_private_messages_protocol(
            profile_dir=profile_dir,
            targets=cleaned_targets,
            message=message,
            browser_options=bopts,
        )
        if not result.get("ok"):
            fail_reason = ""
            for item in result.get("results") or []:
                if isinstance(item, dict) and not item.get("ok") and str(item.get("message", "") or "").strip():
                    fail_reason = str(item.get("message", "") or "").strip()
                    break
            return {
                "code": 400 if not result.get("logged_in") else 500,
                "type": "no_online_account" if not result.get("logged_in") else "send_failed",
                "msg": fail_reason or result.get("message") or "私信发送失败",
                "results": result.get("results") or [],
                "account_id": acct.id,
            }
        acct.status = "active"
        acct.last_login = datetime.utcnow()
        db.commit()
        return {
            "code": 200,
            "msg": result.get("message") or "私信发送完成",
            "results": result.get("results") or [],
            "success": int(result.get("success") or 0),
            "failed": int(result.get("failed") or 0),
            "account_id": acct.id,
        }
    except Exception as e:
        logger.exception("Douyin workbench message send failed")
        return {"code": 500, "msg": f"私信发送失败：{e}", "results": [], "account_id": acct.id}


def _bearer_from_request(request: Request) -> str:
    a = (request.headers.get("Authorization") or "").strip()
    if a.lower().startswith("bearer "):
        return a[7:].strip()
    return ""


def _chat_model_from_request(request: Request) -> str:
    return (request.headers.get("X-Chat-Model") or request.headers.get("x-chat-model") or "").strip()


@router.post("/api/publish", summary="发布素材到平台")
async def create_publish_task(
    request: Request,
    body: PublishReq,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _nick_raw = body.account_nickname or ""
    _nick_s = _nick_raw.strip()
    _asset_id_s = (body.asset_id or "").strip()
    logger.info(
        "[PUBLISH-API] 入参 user_id=%s asset_id=%s account_id=%r account_nickname_repr=%s len(strip)=%d",
        current_user.id,
        _asset_id_s or "(empty)",
        body.account_id,
        repr(_nick_raw[:200]) if len(_nick_raw) <= 200 else repr(_nick_raw[:200] + "…"),
        len(_nick_s),
    )

    acct = _resolve_publish_account_for_request(
        db, current_user.id, body.account_id, body.account_nickname
    )
    if not acct:
        if body.account_id is not None and not _nick_s:
            logger.warning(
                "[PUBLISH-API] 拒绝-发布账号不存在 user_id=%s 原因=account_id 与昵称均无匹配 account_id=%s",
                current_user.id,
                body.account_id,
            )
        elif body.account_nickname:
            all_rows = (
                db.query(PublishAccount.nickname, PublishAccount.platform, PublishAccount.id)
                .filter(PublishAccount.user_id == current_user.id)
                .order_by(PublishAccount.id.asc())
                .all()
            )
            preview = [(r[0], r[1], r[2]) for r in all_rows[:30]]
            logger.warning(
                "[PUBLISH-API] 拒绝-发布账号不存在 user_id=%s 原因=昵称无匹配 "
                "nickname_repr=%r len=%d 库内账号(昵称,platform,id)前30条=%s",
                current_user.id,
                _nick_s,
                len(_nick_s),
                preview,
            )
        else:
            logger.warning(
                "[PUBLISH-API] 拒绝-发布账号不存在 user_id=%s 原因=未传 account_id 与 account_nickname asset_id=%s",
                current_user.id,
                _asset_id_s,
            )
        raise HTTPException(404, detail="发布账号不存在，请先在「发布管理」中添加账号")

    # 头条且无主素材：默认按「无封面图文」走文案发布链（与 MCP/对话层常漏传 options 的情况对齐）。
    # 显式传 toutiao_graphic_no_cover: false 时不改写；若已指定独立封面素材则不自动无封面。
    if (
        acct.platform == "toutiao"
        and not _asset_id_s
        and not (body.cover_asset_id or "").strip()
    ):
        if body.options is None:
            body.options = {}
        if isinstance(body.options, dict) and body.options.get("toutiao_graphic_no_cover") is not False:
            if not _toutiao_opts_wants_graphic_no_cover(body.options):
                body.options = dict(body.options)
                body.options.setdefault("toutiao_graphic_no_cover", True)
                logger.info(
                    "[PUBLISH-API] 头条无 asset_id：已自动 options.toutiao_graphic_no_cover=true"
                )
    _opts_early = body.options if isinstance(body.options, dict) else {}

    toutiao_text_only = (
        acct.platform == "toutiao"
        and not _asset_id_s
        and _toutiao_opts_wants_graphic_no_cover(_opts_early)
    )
    if not _asset_id_s and not toutiao_text_only:
        raise HTTPException(
            status_code=400,
            detail=(
                "请提供素材 asset_id。"
                "若发布今日头条无封面纯文字，请在 options 中设置 toutiao_graphic_no_cover: true。"
            ),
        )

    asset: Optional[Asset] = None
    if _asset_id_s:
        asset = db.query(Asset).filter(
            Asset.asset_id == _asset_id_s,
            Asset.user_id == current_user.id,
        ).first()
        if not asset:
            logger.warning(
                "[PUBLISH-API] 拒绝-素材不存在 user_id=%s asset_id=%s",
                current_user.id,
                _asset_id_s,
            )
            raise HTTPException(404, detail=f"素材不存在: {_asset_id_s}")
    # Allow publishing even when status isn't active: run_publish_task will open browser and wait for login.

    body_title_s = (body.title or "").strip()
    body_desc_s = (body.description or "").strip()
    body_tags_raw_s = (body.tags or "").strip()
    internal_options = body.options if isinstance(body.options, dict) else {}
    internal_source_prompt = str(
        internal_options.get("_source_prompt")
        or internal_options.get("source_prompt")
        or ""
    ).strip()

    xhs_strict = acct.platform == "xiaohongshu"
    if xhs_strict:
        if body.ai_publish_copy is True:
            if not body_desc_s:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "使用 AI 撰写小红书文案时，须在请求里附带与视频相关的文字说明或要点（不可留空），"
                        "以免生成内容与视频不符。"
                    ),
                )
            use_llm = True
        else:
            if not body_title_s or not (body_desc_s or body_tags_raw_s):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "小红书发布需要标题，以及正文或话题标签至少一项。"
                        "请补充后再试；若希望根据口语要点由 AI 生成全文，请在对话里说明要点，"
                        "由助手代为填写并发起发布。"
                    ),
                )
            use_llm = False
    else:
        if body.ai_publish_copy is True:
            use_llm = True
        elif body.ai_publish_copy is False:
            use_llm = False
        else:
            use_llm = (not body_title_s) and (not body_desc_s)

    eff_title = ""
    eff_desc = ""
    eff_tags = ""
    if use_llm:
        from ..services.publish_copy_llm import PublishCopyLLMError, generate_publish_copy

        copy_source_prompt = (
            ((getattr(asset, "prompt", None) or "").strip() if asset is not None else "")
            or internal_source_prompt
        )
        if internal_source_prompt and not ((getattr(asset, "prompt", None) or "").strip() if asset is not None else ""):
            logger.info(
                "[PUBLISH-API] AI 发布文案使用会话 source_prompt 兜底 asset_id=%s source_len=%d",
                (getattr(asset, "asset_id", None) or "-") if asset is not None else "-",
                len(internal_source_prompt),
            )
        try:
            eff_title, eff_desc, eff_tags = await generate_publish_copy(
                platform=acct.platform,
                media_type=_infer_asset_media_type(asset) if asset is not None else "image",
                asset_prompt=copy_source_prompt,
                filename=(asset.filename or "") if asset is not None else "toutiao_graphic_text_only.mp4",
                hint_title=body_title_s,
                hint_desc=body_desc_s,
                hint_tags=body_tags_raw_s,
                raw_token=_bearer_from_request(request) or None,
                chat_model=_chat_model_from_request(request) or None,
            )
            logger.info(
                "[PUBLISH-API] 已用 AI 生成发布文案 title_len=%d desc_len=%d tags_len=%d",
                len(eff_title),
                len(eff_desc),
                len(eff_tags),
            )
        except PublishCopyLLMError as e:
            if body.ai_publish_copy is True:
                raise HTTPException(status_code=503, detail=str(e)) from e
            logger.warning("[PUBLISH-API] AI 发布文案不可用，回退素材补全: %s", e)
            if asset is not None:
                eff_title, eff_desc, eff_tags = _effective_publish_copy_from_asset(
                    asset,
                    body.title,
                    body.description,
                    body.tags,
                    xhs_strict=xhs_strict,
                )
            else:
                eff_title, eff_desc, eff_tags = _effective_publish_copy_no_asset(
                    body.title,
                    body.description,
                    body.tags,
                )
        eff_tags = _sanitize_internal_publish_tags(eff_tags)
    else:
        if asset is not None:
            eff_title, eff_desc, eff_tags = _effective_publish_copy_from_asset(
                asset,
                body.title,
                body.description,
                body.tags,
                xhs_strict=xhs_strict,
            )
        else:
            eff_title, eff_desc, eff_tags = _effective_publish_copy_no_asset(
                body.title,
                body.description,
                body.tags,
            )
        eff_tags = _sanitize_internal_publish_tags(eff_tags)
        if asset is not None and (not xhs_strict) and (not body_title_s or not body_desc_s):
            logger.info(
                "[PUBLISH-API] 标题或描述未传，已从素材 prompt/文件名 补全 title_len=%d desc_len=%d tags_len=%d",
                len(eff_title),
                len(eff_desc),
                len(eff_tags),
            )

    _opts = body.options or {}
    _tt_nc = _opts.get("toutiao_graphic_no_cover")
    logger.info(
        "[PUBLISH-API] 请求: asset_id=%s account=%s platform=%s options.toutiao_graphic_no_cover=%r",
        _asset_id_s or _TOUTIAO_PUBLISH_NO_ASSET_SENTINEL,
        acct.nickname,
        acct.platform,
        _tt_nc,
    )

    task = PublishTask(
        user_id=current_user.id,
        asset_id=_asset_id_s or _TOUTIAO_PUBLISH_NO_ASSET_SENTINEL,
        account_id=acct.id,
        title=eff_title,
        description=eff_desc,
        tags=eff_tags,
        status="pending",
        meta={
            "options": body.options or {},
            "cover_asset_id": body.cover_asset_id,
            "platform": acct.platform,
            "account_nickname": acct.nickname,
        },
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    logger.info(
        "[PUBLISH-API] task_id=%s 若感觉页面反复进出：在 backend.log 搜 PUBLISH-NAV / TOUTIAO-NAV；"
        "多条 1_after_acquire=多次发布请求；单条内出现 3_passive_failed 且 url 变首页=登录检测拉回",
        task.id,
    )

    publish_lock = await _get_account_publish_lock(acct.id)
    if publish_lock.locked():
        logger.warning(
            "[PUBLISH-API] 账号 %s(%s) 已有发布任务正在执行，拒绝并发请求",
            acct.nickname, acct.id,
        )
        task.status = "failed"
        task.error = "该账号当前有发布任务正在执行，请等待完成后再试"
        task.finished_at = datetime.utcnow()
        db.commit()
        return {
            "task_id": task.id,
            "status": task.status,
            "error": task.error,
        }

    try:
        from publisher.browser_pool import run_publish_task
        from .assets import ASSETS_DIR

        async def _resolve_asset_path(a: Asset):
            """返回 (path_str, temp_path_to_delete)。仅用公网 source_url 下载到临时文件，不使用本地路径。"""
            url = getattr(a, "source_url", None) or ""
            if not (url.startswith("http://") or url.startswith("https://")):
                raise HTTPException(
                    400,
                    detail="素材未上传至火山（无公网链接），请先在素材管理中上传并同步至火山后再发布。",
                )
            async with httpx.AsyncClient(timeout=120.0) as c:
                r = await c.get(url)
            r.raise_for_status()
            suf = Path(a.filename or "").suffix or ".mp4"
            fd, path = tempfile.mkstemp(suffix=suf)
            try:
                import os
                os.write(fd, r.content)
            finally:
                import os
                os.close(fd)
            return path, path

        if toutiao_text_only:
            file_path = str(
                _ensure_tiny_mp4(Path(ASSETS_DIR) / "toutiao_text_only_placeholder.mp4")
            )
            temp_video = None
            logger.info(
                "[PUBLISH-API] 头条无素材纯文：占位主文件（驱动走 graphic_no_cover）path=%s",
                file_path,
            )
        else:
            file_path, temp_video = await _resolve_asset_path(asset)
        logger.info(
            "[PUBLISH-API] asset file=%s exists=%s",
            file_path,
            Path(file_path).exists(),
        )
        logger.info(
            "[PUBLISH-API] 实际发布文案 len(title)=%d len(description)=%d len(tags)=%d title_head=%r desc_head=%r",
            len(eff_title),
            len(eff_desc),
            len(eff_tags),
            eff_title[:50],
            (eff_desc[:80] + ("…" if len(eff_desc) > 80 else "")),
        )
        if acct.platform == "toutiao" and not eff_desc.strip():
            logger.warning(
                "[PUBLISH-API] 头条 description 仍为空（素材无 prompt）：图文可能无正文。"
            )
        if acct.platform == "xiaohongshu" and not eff_desc.strip() and not eff_tags.strip():
            logger.warning(
                "[PUBLISH-API] 小红书 description 与 tags 仍为空（素材无可用文案）。"
            )

        publish_opts = {
            str(k): v
            for k, v in dict(body.options or {}).items()
            if not str(k).startswith("_")
        }
        if (
            acct.platform == "douyin"
            and not _douyin_publish_opts_wants_declaration(publish_opts)
            and _asset_likely_ai_generated(asset)
        ):
            publish_opts["douyin_declaration_mode"] = "ai_generated"
            logger.info(
                "[PUBLISH-API] 抖音素材疑似 AI 生成，已默认选择自主声明: 内容由AI生成 asset_id=%s",
                _asset_id_s,
            )
        if acct.platform == "douyin" and _infer_asset_media_type(asset) == "video":
            mode = (publish_opts.get("douyin_cover_mode") or "smart").strip().lower()
            if mode not in ("smart", "upload", "manual"):
                raise HTTPException(
                    400,
                    detail="抖音视频发布须在 options.douyin_cover_mode 指定 smart | upload | manual",
                )
            publish_opts["douyin_cover_mode"] = mode
            if mode == "upload" and not (body.cover_asset_id or "").strip():
                raise HTTPException(
                    400,
                    detail="douyin_cover_mode=upload 时必须指定 cover_asset_id（封面图素材）",
                )

        cover_path = None
        temp_cover = None
        if body.cover_asset_id:
            cover = db.query(Asset).filter(
                Asset.asset_id == body.cover_asset_id,
                Asset.user_id == current_user.id,
            ).first()
            if cover:
                cover_path, temp_cover = await _resolve_asset_path(cover)
        if acct.platform == "toutiao":
            # 纯文占位文件仍是 .mp4；若此处按「视频主素材」剥掉 no_cover，驱动会把占位当真视频走上传视频页。
            if not toutiao_text_only:
                main_suf = (
                    Path(asset.filename or "").suffix if asset is not None else ""
                ) or Path(file_path).suffix
                _toutiao_strip_graphic_no_cover_for_image_main(publish_opts, main_suf)

        logger.info("[PUBLISH-API] calling run_publish_task: platform=%s profile=%s title=%s",
                     acct.platform, acct.browser_profile, eff_title[:40])
        bopts = browser_options_from_publish_meta(acct.meta)
        async with publish_lock:
            result = await run_publish_task(
                profile_dir=acct.browser_profile,
                platform=acct.platform,
                file_path=file_path,
                title=eff_title,
                description=eff_desc,
                tags=eff_tags,
                options=publish_opts,
                cover_path=cover_path,
                browser_options=bopts,
            )
        for p in (temp_video, temp_cover):
            if p and Path(p).exists():
                try:
                    Path(p).unlink()
                except Exception:
                    pass
        logger.info("[PUBLISH-API] result: %s", {k: v for k, v in result.items() if k != "applied"})
        task.status = "success" if result.get("ok") else "failed"
        if result.get("need_login"):
            task.status = "need_login"
        task.result_url = result.get("url", "")
        task.error = result.get("error", "")
        task.meta = {
            **(task.meta or {}),
            "driver_result": result,
        }
        task.finished_at = datetime.utcnow()
        db.commit()
    except Exception as e:
        task.status = "failed"
        task.error = str(e)
        task.finished_at = datetime.utcnow()
        db.commit()
        logger.exception("[PUBLISH-API] publish task exception")

    resp = {
        "task_id": task.id,
        "status": task.status,
        "result_url": task.result_url,
        "error": task.error,
    }
    _dr = (task.meta or {}).get("driver_result") or {}
    if task.status == "success" and _dr.get("toutiao_submission_hint"):
        resp["toutiao_submission_hint"] = _dr["toutiao_submission_hint"]
    if task.status == "failed" and acct.platform == "toutiao":
        err = (task.error or "").strip()
        if "视频发布需要封面" in err or (
            "视频" in err and "封面" in err and ("上传" in err or "截取" in err or "本地上传" in err)
        ):
            resp["agent_constraints"] = [
                "【必读】本次为头条/西瓜「视频」发布失败。禁止再用封面图或其它图片素材的 asset_id 调用 publish_content 发头条「图文」顶替；用户要的是视频，不是文章。",
                "可采取：仍用原视频 asset_id 重试（建议去掉 cover_asset_id）；或如实告知用户自动化封面未成功、需对方在发布页手动选封面后再发。",
            ]
    if task.status == "need_login" or (task.meta and task.meta.get("driver_result", {}).get("need_login")):
        resp["need_login"] = True
    logger.info("[PUBLISH-API] response: %s", resp)
    return resp


@router.get("/api/publish/tasks", summary="发布任务列表")
def list_publish_tasks(
    limit: int = 50,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(PublishTask)
        .filter(PublishTask.user_id == current_user.id)
        .order_by(PublishTask.created_at.desc())
        .limit(min(limit, 200))
        .all()
    )
    def _task_dict(t):
        meta = t.meta or {}
        driver_result = meta.get("driver_result", {})
        steps = driver_result.get("applied", {}).get("steps", [])
        return {
            "id": t.id,
            "asset_id": t.asset_id,
            "account_id": t.account_id,
            "title": t.title,
            "status": t.status,
            "result_url": t.result_url,
            "error": t.error,
            "platform": meta.get("platform", ""),
            "account_nickname": meta.get("account_nickname", ""),
            "steps": steps,
            "created_at": t.created_at.isoformat() if t.created_at else "",
            "finished_at": t.finished_at.isoformat() if t.finished_at else None,
        }
    return {"tasks": [_task_dict(t) for t in rows]}
