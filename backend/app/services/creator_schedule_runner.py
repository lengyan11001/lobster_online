"""后台：按 next_run_at（UTC）触发抖音/小红书创作者作品同步，执行后顺延 interval_minutes；
若填写了生产要求/描述需求，则调用 POST /chat 按提纲自动编排（生成素材、发布等）。"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..api.creator_content import SYNC_PLATFORMS, perform_creator_content_sync
from ..db import SessionLocal
from ..models import PublishAccount, PublishAccountCreatorSchedule
from .creator_schedule_task_log import (
    compute_final_status,
    finish_task_log,
    start_task_log,
    update_task_log,
)
from .schedule_orchestration_run import run_schedule_orchestration_chat
from .schedule_review_timing import compute_next_review_run_at_after_orchestration

logger = logging.getLogger(__name__)

# 同一发布账号一次只跑一轮 tick（立即触发与 50s 后台轮询互斥）
_account_tick_locks: dict[int, asyncio.Lock] = {}
_locks_registry_lock = asyncio.Lock()


async def _get_account_tick_lock(account_id: int) -> asyncio.Lock:
    async with _locks_registry_lock:
        if account_id not in _account_tick_locks:
            _account_tick_locks[account_id] = asyncio.Lock()
        return _account_tick_locks[account_id]


async def _run_one_schedule_tick(
    db: Session, sch: PublishAccountCreatorSchedule, now: datetime
) -> None:
    lock = await _get_account_tick_lock(sch.account_id)
    async with lock:
        log_id: Optional[int] = None
        iv = max(1, int(sch.interval_minutes or 60))
        try:
            log_row = start_task_log(db, user_id=sch.user_id, account_id=sch.account_id, trigger="tick")
            log_id = log_row.id

            acct = db.query(PublishAccount).filter(PublishAccount.id == sch.account_id).first()
            if not acct:
                sch.next_run_at = now + timedelta(minutes=iv)
                db.commit()
                finish_task_log(
                    db,
                    log_id,
                    status="failed",
                    phase="账号不存在",
                    sync_ok=False,
                    sync_error="发布账号记录不存在",
                )
                return

            sync_ok = False
            sync_err: Optional[str] = None
            item_count: Optional[int] = None

            if acct.platform in SYNC_PLATFORMS:
                update_task_log(
                    db,
                    log_id,
                    phase="作品同步中",
                    detail="拉取抖音/小红书创作者作品列表",
                )
                try:
                    sync_result = await perform_creator_content_sync(
                        db,
                        user_id=sch.user_id,
                        account_id=sch.account_id,
                        headless=None,
                    )
                    db.refresh(sch)
                    sch.last_run_at = datetime.utcnow()
                    sch.last_run_error = None
                    sync_ok = bool(sync_result.get("ok"))
                    item_count = sync_result.get("item_count")
                    sync_err = sync_result.get("error")
                except ValueError as e:
                    db.refresh(sch)
                    sch.last_run_at = datetime.utcnow()
                    sch.last_run_error = str(e)
                    sync_ok = False
                    sync_err = str(e)
                    logger.warning("scheduled creator sync value error account_id=%s: %s", sch.account_id, e)
                except Exception as e:
                    db.refresh(sch)
                    sch.last_run_at = datetime.utcnow()
                    sch.last_run_error = str(e)
                    sync_ok = False
                    sync_err = str(e)
                    logger.exception("scheduled creator sync account_id=%s", sch.account_id)
            else:
                update_task_log(
                    db,
                    log_id,
                    phase="跳过作品同步",
                    detail="当前平台不支持作品列表同步，仅推进时间与编排",
                )
                sync_ok = True

            sch_mode = (getattr(sch, "schedule_publish_mode", None) or "immediate").strip().lower()
            rv_conf = bool(getattr(sch, "review_confirmed", False))
            dr_q = getattr(sch, "review_drafts_json", None) or []
            review_queue = (
                sch_mode == "review"
                and rv_conf
                and isinstance(dr_q, list)
                and len(dr_q) > 0
            )

            if not review_queue:
                sch.next_run_at = datetime.utcnow() + timedelta(minutes=iv)
            db.commit()

            update_task_log(
                db,
                log_id,
                phase="已顺延下次执行时间",
                sync_ok=sync_ok,
                sync_error=sync_err,
                item_count=item_count,
            )

            req = (sch.requirements_text or "").strip()
            had_orch = False
            orch_ok: Optional[bool] = None
            orch_err: Optional[str] = None
            orch_superseded = False

            if req:
                had_orch = True
                update_task_log(
                    db,
                    log_id,
                    phase="智能编排中",
                    detail="POST /chat（生成/发布等，可能耗时数十分钟）",
                )
                skip_orch = False
                if review_queue:
                    nra = getattr(sch, "next_run_at", None)
                    if (
                        nra is not None
                        and isinstance(nra, datetime)
                        and nra > datetime.utcnow()
                    ):
                        skip_orch = True
                if skip_orch:
                    had_orch = False
                    orch_ok = None
                    orch_err = None
                else:
                    try:
                        orch_res = await run_schedule_orchestration_chat(sch, acct)
                        if orch_res.get("superseded"):
                            orch_superseded = True
                            had_orch = True
                            ok_raw = orch_res.get("ok")
                            orch_ok = bool(ok_raw) if ok_raw is not None else None
                            orch_err = orch_res.get("error")
                        elif orch_res.get("skipped"):
                            had_orch = False
                            orch_ok = None
                            orch_err = None
                        else:
                            orch_ok = bool(orch_res.get("ok"))
                            orch_err = orch_res.get("error")
                    except Exception as e:
                        orch_ok = False
                        orch_err = str(e)
                        logger.exception("schedule orchestration account_id=%s", sch.account_id)

            db.refresh(sch)
            if review_queue:
                if orch_superseded:
                    pass
                elif had_orch and orch_ok is True:
                    sch.next_run_at = compute_next_review_run_at_after_orchestration(
                        sch, datetime.utcnow()
                    )
                    db.commit()
                elif had_orch and orch_ok is not True:
                    sch.next_run_at = datetime.utcnow() + timedelta(minutes=iv)
                    db.commit()

            final_status = compute_final_status(
                sync_ok=sync_ok,
                had_orchestration=had_orch,
                orchestration_ok=orch_ok,
            )
            finish_task_log(
                db,
                log_id,
                status=final_status,
                phase="已完成",
                detail="",
                sync_ok=sync_ok,
                sync_error=sync_err,
                item_count=item_count,
                orchestration_ok=orch_ok,
                orchestration_error=orch_err,
            )
            log_id = None
            logger.info("scheduled creator tick account_id=%s next=%s", sch.account_id, sch.next_run_at)
        except Exception as e:
            logger.exception("creator_schedule tick iteration account_id=%s", sch.account_id)
            if log_id is not None:
                try:
                    finish_task_log(
                        db,
                        log_id,
                        status="failed",
                        phase="异常中断",
                        detail=str(e),
                    )
                except Exception:
                    logger.exception("finish_task_log after tick failure")


async def _tick_once(account_id: Optional[int] = None) -> None:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        q = (
            db.query(PublishAccountCreatorSchedule)
            .filter(PublishAccountCreatorSchedule.enabled.is_(True))
            .filter(PublishAccountCreatorSchedule.next_run_at.isnot(None))
            .filter(PublishAccountCreatorSchedule.next_run_at <= now)
        )
        if account_id is not None:
            q = q.filter(PublishAccountCreatorSchedule.account_id == account_id)
        schedules = q.all()
        for sch in schedules:
            await _run_one_schedule_tick(db, sch, now)
    finally:
        db.close()


async def run_creator_schedule_tick_immediate(account_id: int) -> None:
    """确认发布等场景：不等下一轮 50s 轮询，立即对该账号跑一轮与 tick 相同的逻辑。"""
    try:
        await _tick_once(account_id=account_id)
    except Exception:
        logger.exception("run_creator_schedule_tick_immediate account_id=%s", account_id)


async def creator_schedule_background_loop() -> None:
    await asyncio.sleep(15)
    while True:
        try:
            await _tick_once()
        except Exception:
            logger.exception("creator_schedule_background_loop tick")
        await asyncio.sleep(50)
