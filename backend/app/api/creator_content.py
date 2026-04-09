"""创作者作品列表同步：抖音 work_list、小红书 note/user/posted。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .auth import _ServerUser, get_current_user_for_local
from .publish import BROWSER_DATA_DIR
from ..core.config import settings
from ..db import get_db
from ..datetime_iso import isoformat_utc
from ..models import CreatorContentSnapshot, PublishAccount
from ..services.creator_content_sync import sync_account_creator_content
from publisher.browser_pool import browser_options_from_publish_meta

logger = logging.getLogger(__name__)

router = APIRouter()

SYNC_PLATFORMS = frozenset({"douyin", "xiaohongshu", "toutiao"})

_PLATFORM_LABEL = {
    "douyin": "抖音",
    "xiaohongshu": "小红书",
    "toutiao": "今日头条（头条号）",
}

_PUBLISH_DATA_MAX_ITEMS_PER_ACCOUNT = 40
_PUBLISH_DATA_MAX_INSIGHT_KEYS = 80


def _slim_item_for_llm(item: Dict[str, Any]) -> Dict[str, Any]:
    """去掉封面 URL 等冗长字段，保留标题与各平台 metrics。"""
    m = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    return {
        "id": str(item.get("id", "")),
        "title": (item.get("title") or "")[:500],
        "metrics": m,
        "content_type": str(item.get("content_type", "")),
        "time": item.get("time") or item.get("create_time"),
    }


def _truncate_toutiao_insights(meta: Optional[Dict[str, Any]], max_keys: int) -> Dict[str, Any]:
    if not meta or not isinstance(meta, dict):
        return {}
    ins = meta.get("toutiao_insights")
    if not isinstance(ins, dict) or not ins:
        return {}
    keys = sorted(ins.keys(), key=lambda x: str(x).lower())[:max_keys]
    out: Dict[str, Any] = {}
    for k in keys:
        v = ins[k]
        if isinstance(v, (dict, list)):
            s = json.dumps(v, ensure_ascii=False)
        else:
            s = str(v)
        if len(s) > 400:
            s = s[:400] + "…"
        out[str(k)] = s
    return out


def _account_payload_for_llm(
    acct: PublishAccount,
    row: Optional[CreatorContentSnapshot],
) -> Dict[str, Any]:
    plat = acct.platform
    base: Dict[str, Any] = {
        "account_id": acct.id,
        "nickname": acct.nickname or "",
        "platform": plat,
        "platform_label": _PLATFORM_LABEL.get(plat, plat),
    }
    if not row:
        base["hint"] = "尚无同步快照；可在对话中请求「同步发布数据」从各平台拉取最新作品数据。"
        base["items_sample"] = []
        base["meta_summary"] = {}
        return base
    items = row.items if isinstance(row.items, list) else []
    slim = [_slim_item_for_llm(x) for x in items[:_PUBLISH_DATA_MAX_ITEMS_PER_ACCOUNT] if isinstance(x, dict)]
    meta_full = row.meta if isinstance(row.meta, dict) else {}
    meta_summary: Dict[str, Any] = {}
    if plat == "toutiao":
        ti = _truncate_toutiao_insights(meta_full, _PUBLISH_DATA_MAX_INSIGHT_KEYS)
        if ti:
            meta_summary["toutiao_insights"] = ti
    base["snapshot_id"] = row.id
    base["fetched_at"] = isoformat_utc(row.fetched_at)
    base["sync_error"] = row.sync_error
    base["item_count"] = len(items)
    base["items_sample"] = slim
    base["meta_summary"] = meta_summary
    if row.sync_error:
        base["hint"] = "上次同步有错误，数据可能不完整，结论仅供参考。"
    return base


def _latest_snapshot(
    db: Session,
    user_id: int,
    account_id: int,
) -> Optional[CreatorContentSnapshot]:
    return (
        db.query(CreatorContentSnapshot)
        .filter(
            CreatorContentSnapshot.user_id == user_id,
            CreatorContentSnapshot.account_id == account_id,
        )
        .order_by(CreatorContentSnapshot.id.desc())
        .first()
    )


def _accounts_for_publish_data_query(
    db: Session,
    user_id: int,
    *,
    scope: str,
    platform: Optional[str],
    account_id: Optional[int],
    account_nickname: Optional[str],
) -> List[PublishAccount]:
    q = db.query(PublishAccount).filter(PublishAccount.user_id == user_id)
    sc = (scope or "all").strip().lower()
    if sc == "account":
        if account_id is not None:
            q = q.filter(PublishAccount.id == int(account_id))
        elif (account_nickname or "").strip():
            q = q.filter(PublishAccount.nickname == (account_nickname or "").strip())
        else:
            raise HTTPException(status_code=400, detail="scope=account 时需要 account_id 或 account_nickname")
    elif sc == "platform":
        p = (platform or "").strip()
        if p not in SYNC_PLATFORMS:
            raise HTTPException(
                status_code=400,
                detail=f"scope=platform 时需要 platform 为 {', '.join(sorted(SYNC_PLATFORMS))}",
            )
        q = q.filter(PublishAccount.platform == p)
    elif sc != "all":
        raise HTTPException(status_code=400, detail="scope 须为 all | platform | account")
    return [a for a in q.all() if a.platform in SYNC_PLATFORMS]


def build_creator_publish_data_payload(
    db: Session,
    user_id: int,
    *,
    scope: str = "all",
    platform: Optional[str] = None,
    account_id: Optional[int] = None,
    account_nickname: Optional[str] = None,
) -> Dict[str, Any]:
    """组装供对话模型阅读的 JSON（各平台字段不完全一致，由模型自行解读）。"""
    sc = (scope or "all").strip().lower()
    accounts = _accounts_for_publish_data_query(
        db,
        user_id,
        scope=scope,
        platform=platform,
        account_id=account_id,
        account_nickname=account_nickname,
    )
    if sc == "account" and not accounts:
        raise HTTPException(
            status_code=404,
            detail="账号不存在或不是抖音/小红书/今日头条（无作品同步）",
        )
    out_accounts: List[Dict[str, Any]] = []
    for acct in accounts:
        row = _latest_snapshot(db, user_id, acct.id)
        out_accounts.append(_account_payload_for_llm(acct, row))
    return {
        "ok": True,
        "scope": scope,
        "note": (
            "数据来自本机最近一次「创作者作品同步」。抖音/小红书/今日头条字段结构不同；"
            "头条号可能含 meta_summary.toutiao_insights（账号级抓取摘要）。"
        ),
        "accounts": out_accounts,
    }


async def perform_sync_creator_publish_accounts(
    db: Session,
    *,
    user_id: int,
    headless: Optional[bool] = None,
    platform: Optional[str] = None,
    account_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """依次同步多个账号的创作者作品（Playwright，可能较久）。account_ids 为 None 表示所有可同步账号。"""
    q = db.query(PublishAccount).filter(PublishAccount.user_id == user_id)
    if account_ids:
        q = q.filter(PublishAccount.id.in_(account_ids))
    raw = q.all()
    if account_ids:
        found = {a.id for a in raw}
        missing = set(account_ids) - found
        if missing:
            raise ValueError(f"账号不存在或无权访问: {sorted(missing)}")
        syncable = [a for a in raw if a.platform in SYNC_PLATFORMS]
        if len(syncable) != len(raw):
            bad = [a.id for a in raw if a.platform not in SYNC_PLATFORMS]
            raise ValueError(f"以下账号不是抖音/小红书/今日头条，无法同步作品数据: {bad}")
        accounts = syncable
    else:
        accounts = [a for a in raw if a.platform in SYNC_PLATFORMS]
    if platform:
        p = platform.strip()
        if p not in SYNC_PLATFORMS:
            raise ValueError(f"platform 须为 {', '.join(sorted(SYNC_PLATFORMS))}")
        accounts = [a for a in accounts if a.platform == p]
    if not accounts:
        raise ValueError("没有可同步的抖音/小红书/今日头条账号")
    results: List[Dict[str, Any]] = []
    for acct in accounts:
        try:
            r = await perform_creator_content_sync(
                db,
                user_id=user_id,
                account_id=acct.id,
                headless=headless,
            )
            results.append(
                {
                    "account_id": acct.id,
                    "nickname": acct.nickname,
                    "platform": acct.platform,
                    "ok": bool(r.get("ok")),
                    "item_count": r.get("item_count"),
                    "error": r.get("error"),
                    "fetched_at": r.get("fetched_at"),
                }
            )
        except Exception as e:
            logger.exception("perform_sync_creator_publish_accounts account_id=%s", acct.id)
            results.append(
                {
                    "account_id": acct.id,
                    "nickname": acct.nickname,
                    "platform": acct.platform,
                    "ok": False,
                    "item_count": None,
                    "error": str(e),
                    "fetched_at": None,
                }
            )
    ok_any = any(x.get("ok") for x in results)
    return {
        "ok": ok_any,
        "synced_accounts": len(results),
        "results": results,
    }


class SyncAllCreatorContentBody(BaseModel):
    """批量同步创作者作品：默认所有抖音/小红书/头条号；可限定平台或账号 ID 列表。"""

    headless: Optional[bool] = Field(default=None, description="无头浏览器；null 用服务器默认")
    platform: Optional[str] = Field(default=None, description="仅同步该平台，如 douyin")
    account_ids: Optional[List[int]] = Field(default=None, description="仅同步这些账号；省略则全部可同步账号")


def _profile_dir(acct: PublishAccount) -> str:
    return acct.browser_profile or str(BROWSER_DATA_DIR / f"{acct.platform}_{acct.nickname}")


class SyncCreatorContentBody(BaseModel):
    """POST 同步可选参数。"""

    headless: Optional[bool] = Field(
        default=None,
        description="是否无头启动浏览器；null 表示使用服务器配置 CREATOR_SYNC_HEADLESS",
    )


def _ttl_effective(ttl_query: Optional[int]) -> int:
    if ttl_query is not None:
        return max(0, int(ttl_query))
    return max(0, int(settings.creator_content_ttl_seconds))


def _stale_payload(fetched_at: Optional[datetime], ttl_sec: int) -> Dict[str, Any]:
    if not fetched_at:
        return {
            "age_seconds": None,
            "ttl_seconds": ttl_sec,
            "is_stale": True,
            "suggest_sync": True,
        }
    now = datetime.utcnow()
    age = max(0.0, (now - fetched_at).total_seconds())
    is_stale = ttl_sec > 0 and age > ttl_sec
    return {
        "age_seconds": int(age),
        "ttl_seconds": ttl_sec,
        "is_stale": is_stale,
        "suggest_sync": is_stale or False,
    }


@router.get("/api/creator-content/settings", summary="创作者作品数据：客户端用默认 TTL 等")
def creator_content_public_settings(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    _ = current_user
    return {
        "creator_content_ttl_seconds": max(0, int(settings.creator_content_ttl_seconds)),
        "creator_sync_headless_default": bool(settings.creator_sync_headless),
        "supported_platforms": sorted(SYNC_PLATFORMS),
    }


async def perform_creator_content_sync(
    db: Session,
    *,
    user_id: int,
    account_id: int,
    headless: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    执行一次创作者作品同步并写入快照。供 HTTP 与定时任务共用。
    headless 为 None 时使用 settings.creator_sync_headless。
    """
    acct = (
        db.query(PublishAccount)
        .filter(
            PublishAccount.id == account_id,
            PublishAccount.user_id == user_id,
        )
        .first()
    )
    if not acct:
        raise ValueError("账号不存在")
    if acct.platform not in SYNC_PLATFORMS:
        raise ValueError(f"当前仅支持同步: {', '.join(sorted(SYNC_PLATFORMS))}")

    profile = _profile_dir(acct)
    hl = settings.creator_sync_headless if headless is None else bool(headless)
    bopts = browser_options_from_publish_meta(acct.meta)
    result: Dict[str, Any] = await sync_account_creator_content(
        profile,
        acct.platform,
        new_context_headless=hl,
        browser_options=bopts,
    )
    items: List[Any] = result.get("items") or []
    meta: Dict[str, Any] = dict(result.get("meta") or {})
    meta["ok"] = bool(result.get("ok"))
    meta["headless_used_for_new_context"] = hl
    err = result.get("error")

    snap = CreatorContentSnapshot(
        user_id=user_id,
        account_id=acct.id,
        platform=acct.platform,
        items=items if items else None,
        meta=meta or None,
        sync_error=str(err) if err else None,
        fetched_at=datetime.utcnow(),
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)

    return {
        "ok": bool(result.get("ok")),
        "snapshot_id": snap.id,
        "error": err,
        "item_count": len(items),
        "fetched_at": isoformat_utc(snap.fetched_at),
        "items": items,
        "meta": meta,
    }


@router.post("/api/accounts/{account_id}/sync-creator-content", summary="同步创作者作品概括数据")
async def sync_creator_content(
    account_id: int,
    body: SyncCreatorContentBody = SyncCreatorContentBody(),
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    try:
        return await perform_creator_content_sync(
            db,
            user_id=current_user.id,
            account_id=account_id,
            headless=body.headless,
        )
    except ValueError as e:
        msg = str(e)
        if "账号不存在" in msg:
            raise HTTPException(404, detail=msg) from e
        raise HTTPException(400, detail=msg) from e


@router.get("/api/accounts/{account_id}/creator-content", summary="获取最近一次同步的作品列表")
def get_creator_content_latest(
    account_id: int,
    ttl_seconds: Optional[int] = Query(
        None,
        ge=0,
        description="覆盖默认 TTL（秒）用于 is_stale；不传则用服务器配置",
    ),
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = (
        db.query(PublishAccount)
        .filter(
            PublishAccount.id == account_id,
            PublishAccount.user_id == current_user.id,
        )
        .first()
    )
    if not acct:
        raise HTTPException(404, detail="账号不存在")

    ttl_sec = _ttl_effective(ttl_seconds)

    row = (
        db.query(CreatorContentSnapshot)
        .filter(
            CreatorContentSnapshot.user_id == current_user.id,
            CreatorContentSnapshot.account_id == account_id,
        )
        .order_by(CreatorContentSnapshot.id.desc())
        .first()
    )
    if not row:
        stale = _stale_payload(None, ttl_sec)
        return {
            "has_snapshot": False,
            "account_id": account_id,
            "platform": acct.platform,
            "items": [],
            "meta": None,
            "sync_error": None,
            "fetched_at": None,
            **stale,
        }

    stale = _stale_payload(row.fetched_at, ttl_sec)
    if row.sync_error:
        stale["suggest_sync"] = True

    return {
        "has_snapshot": True,
        "snapshot_id": row.id,
        "account_id": account_id,
        "platform": row.platform,
        "items": row.items or [],
        "meta": row.meta,
        "sync_error": row.sync_error,
        "fetched_at": isoformat_utc(row.fetched_at),
        **stale,
    }


@router.get("/api/creator-content/publish-data", summary="对话用：读取本地创作者作品同步快照（结构化 JSON）")
def get_creator_publish_data(
    scope: str = Query("all", description="all=全部可同步账号 · platform=按平台 · account=单个账号"),
    platform: Optional[str] = Query(None, description="scope=platform 时：douyin | xiaohongshu | toutiao"),
    account_id: Optional[int] = Query(None, description="scope=account 时优先使用"),
    account_nickname: Optional[str] = Query(None, description="scope=account 时与 account_id 二选一"),
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    return build_creator_publish_data_payload(
        db,
        current_user.id,
        scope=scope,
        platform=platform,
        account_id=account_id,
        account_nickname=account_nickname,
    )


@router.post("/api/creator-content/sync-all", summary="批量同步抖音/小红书/头条作品数据（Playwright，可能较久）")
async def sync_all_creator_content(
    body: SyncAllCreatorContentBody = SyncAllCreatorContentBody(),
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    try:
        return await perform_sync_creator_publish_accounts(
            db,
            user_id=current_user.id,
            headless=body.headless,
            platform=body.platform,
            account_ids=body.account_ids,
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from e
