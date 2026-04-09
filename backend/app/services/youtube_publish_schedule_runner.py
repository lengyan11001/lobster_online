"""后台：按 next_run_at 触发 YouTube 定时上传（每账号独立队列，不含审核后发布）。"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Literal, Optional

from sqlalchemy.orm import Session

from ..api.youtube_publish import (
    YoutubePublishUploadBody,
    YoutubeUploadUserError,
    _norm_asset_id_list,
    perform_youtube_upload,
)
from ..db import SessionLocal
from ..models import YoutubePublishSchedule

logger = logging.getLogger(__name__)

_locks: dict[str, asyncio.Lock] = {}
_locks_lock = asyncio.Lock()


async def _sched_lock(key: str) -> asyncio.Lock:
    async with _locks_lock:
        if key not in _locks:
            _locks[key] = asyncio.Lock()
        return _locks[key]


def _privacy(s: Optional[str]) -> Literal["private", "unlisted", "public"]:
    ps = (s or "public").strip().lower()
    if ps in ("private", "unlisted", "public"):
        return ps  # type: ignore[return-value]
    return "public"


def _material(s: Optional[str]) -> Literal["ai_generated", "script_batch"]:
    mo = (s or "script_batch").strip().lower()
    if mo == "ai_generated":
        return "ai_generated"
    return "script_batch"


async def _run_one_youtube_schedule(
    db: Session, sch: YoutubePublishSchedule, now: datetime
) -> None:
    key = f"{sch.user_id}:{sch.youtube_account_id}"
    lock = await _sched_lock(key)
    async with lock:
        iv = max(1, int(sch.interval_minutes or 60))
        aid = (sch.youtube_account_id or "").strip()
        uid = int(sch.user_id)
        queue = _norm_asset_id_list(sch.asset_ids_json)

        if not queue:
            sch.last_run_at = now
            sch.last_run_error = "待上传队列为空，已跳过本次上传"
            sch.next_run_at = now + timedelta(minutes=iv)
            db.commit()
            logger.info(
                "[youtube-schedule] skip empty queue user_id=%s account_id=%s next=%s",
                uid,
                aid,
                sch.next_run_at,
            )
            return

        asset_id = queue[0]
        rest = queue[1:]
        tags = sch.tags_json if isinstance(sch.tags_json, list) else None

        body = YoutubePublishUploadBody(
            account_id=aid,
            asset_id=asset_id,
            title=(sch.title or "")[:5000],
            description=sch.description or "",
            privacy_status=_privacy(sch.privacy_status),
            material_origin=_material(sch.material_origin),
            self_declared_made_for_kids=False,
            contains_synthetic_media=None,
            category_id=(sch.category_id or "22").strip() or "22",
            tags=tags,
        )

        try:
            res = await perform_youtube_upload(db, uid, body)
            vid = res.get("video_id")
            sch.asset_ids_json = rest
            sch.last_run_at = datetime.utcnow()
            sch.last_run_error = None
            sch.last_video_id = str(vid) if vid else None
            sch.next_run_at = datetime.utcnow() + timedelta(minutes=iv)
            db.commit()
            logger.info(
                "[youtube-schedule] ok user_id=%s account_id=%s asset_id=%s video_id=%s next=%s",
                uid,
                aid,
                asset_id,
                vid,
                sch.next_run_at,
            )
        except YoutubeUploadUserError as e:
            sch.last_run_at = datetime.utcnow()
            sch.last_run_error = e.detail[:4000] if e.detail else str(e)
            sch.next_run_at = datetime.utcnow() + timedelta(minutes=iv)
            db.commit()
            logger.warning(
                "[youtube-schedule] user error user_id=%s account_id=%s asset_id=%s: %s",
                uid,
                aid,
                asset_id,
                e.detail,
            )
        except RuntimeError as e:
            sch.last_run_at = datetime.utcnow()
            sch.last_run_error = str(e)[:4000]
            sch.next_run_at = datetime.utcnow() + timedelta(minutes=iv)
            db.commit()
            logger.warning(
                "[youtube-schedule] runtime user_id=%s account_id=%s asset_id=%s: %s",
                uid,
                aid,
                asset_id,
                e,
            )
        except Exception as e:
            sch.last_run_at = datetime.utcnow()
            sch.last_run_error = str(e)[:4000]
            sch.next_run_at = datetime.utcnow() + timedelta(minutes=iv)
            db.commit()
            logger.exception(
                "[youtube-schedule] failed user_id=%s account_id=%s asset_id=%s",
                uid,
                aid,
                asset_id,
            )


async def _tick_once() -> None:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        q = (
            db.query(YoutubePublishSchedule)
            .filter(YoutubePublishSchedule.enabled.is_(True))
            .filter(YoutubePublishSchedule.next_run_at.isnot(None))
            .filter(YoutubePublishSchedule.next_run_at <= now)
        )
        rows = q.all()
        for sch in rows:
            await _run_one_youtube_schedule(db, sch, now)
    finally:
        db.close()


async def youtube_publish_schedule_background_loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await _tick_once()
        except Exception:
            logger.exception("youtube_publish_schedule_background_loop tick")
        await asyncio.sleep(50)
