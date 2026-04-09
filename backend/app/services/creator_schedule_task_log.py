"""创作者定时任务：每次触发的执行记录（同步 + 智能编排）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Union

from sqlalchemy.orm import Session

from ..models import CreatorScheduleTaskLog

_MISSING = object()


def _trunc(s: Optional[str], max_len: int = 2000) -> Optional[str]:
    if s is None:
        return None
    t = str(s).strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def cancel_running_task_logs_for_account(
    db: Session, *, user_id: int, account_id: int, reason: str, commit: bool = True
) -> int:
    """将同一账号下未结束的任务记为已取消（新「确认并发布」覆盖旧编排）。"""
    now = datetime.utcnow()
    rows = (
        db.query(CreatorScheduleTaskLog)
        .filter(CreatorScheduleTaskLog.user_id == user_id)
        .filter(CreatorScheduleTaskLog.account_id == account_id)
        .filter(CreatorScheduleTaskLog.status == "running")
        .filter(CreatorScheduleTaskLog.finished_at.is_(None))
        .all()
    )
    n = 0
    detail = _trunc(reason, 2000) or "已取消"
    for row in rows:
        row.status = "cancelled"
        row.phase = "已取消"
        row.detail = detail
        row.finished_at = now
        row.updated_at = now
        n += 1
    if n and commit:
        db.commit()
    return n


def start_task_log(db: Session, *, user_id: int, account_id: int, trigger: str) -> CreatorScheduleTaskLog:
    now = datetime.utcnow()
    row = CreatorScheduleTaskLog(
        user_id=user_id,
        account_id=account_id,
        trigger=(trigger or "tick")[:32],
        status="running",
        phase="已开始",
        started_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_task_log(
    db: Session,
    log_id: int,
    *,
    phase: Optional[str] = None,
    detail: Optional[str] = None,
    sync_ok: Union[bool, None, Any] = _MISSING,
    sync_error: Union[str, None, Any] = _MISSING,
    item_count: Union[int, None, Any] = _MISSING,
    orchestration_ok: Union[bool, None, Any] = _MISSING,
    orchestration_error: Union[str, None, Any] = _MISSING,
) -> None:
    row = db.query(CreatorScheduleTaskLog).filter(CreatorScheduleTaskLog.id == log_id).first()
    if not row:
        return
    db.refresh(row)
    if row.status != "running":
        return
    if phase is not None:
        row.phase = phase[:256] if phase else ""
    if detail is not None:
        row.detail = _trunc(detail)
    if sync_ok is not _MISSING:
        row.sync_ok = bool(sync_ok) if sync_ok is not None else None
    if sync_error is not _MISSING:
        row.sync_error = _trunc(sync_error, 4000)
    if item_count is not _MISSING:
        row.item_count = int(item_count) if item_count is not None else None
    if orchestration_ok is not _MISSING:
        row.orchestration_ok = bool(orchestration_ok) if orchestration_ok is not None else None
    if orchestration_error is not _MISSING:
        row.orchestration_error = _trunc(orchestration_error, 4000)
    row.updated_at = datetime.utcnow()
    db.commit()


def finish_task_log(
    db: Session,
    log_id: int,
    *,
    status: str,
    phase: Optional[str] = None,
    detail: Optional[str] = None,
    sync_ok: Union[bool, None, Any] = _MISSING,
    sync_error: Union[str, None, Any] = _MISSING,
    item_count: Union[int, None, Any] = _MISSING,
    orchestration_ok: Union[bool, None, Any] = _MISSING,
    orchestration_error: Union[str, None, Any] = _MISSING,
) -> None:
    row = db.query(CreatorScheduleTaskLog).filter(CreatorScheduleTaskLog.id == log_id).first()
    if not row:
        return
    db.refresh(row)
    if row.status != "running":
        return
    row.status = (status or "failed")[:32]
    if phase is not None:
        row.phase = phase[:256] if phase else ""
    if detail is not None:
        row.detail = _trunc(detail)
    if sync_ok is not _MISSING:
        row.sync_ok = bool(sync_ok) if sync_ok is not None else None
    if sync_error is not _MISSING:
        row.sync_error = _trunc(sync_error, 4000)
    if item_count is not _MISSING:
        row.item_count = int(item_count) if item_count is not None else None
    if orchestration_ok is not _MISSING:
        row.orchestration_ok = bool(orchestration_ok) if orchestration_ok is not None else None
    if orchestration_error is not _MISSING:
        row.orchestration_error = _trunc(orchestration_error, 4000)
    fin = datetime.utcnow()
    row.finished_at = fin
    row.updated_at = fin
    db.commit()


def compute_final_status(
    *,
    sync_ok: bool,
    had_orchestration: bool,
    orchestration_ok: Optional[bool],
) -> str:
    """
    总状态：作品同步与智能编排解耦。
    - 只要跑了编排且编排成功：整体至少为「部分成功」（同步失败时）或「成功」（同步也成功）。
    - 避免「小红书接口 406 但图生视频/发布已成功」仍显示整任务失败。
    """
    if had_orchestration:
        if orchestration_ok is True:
            return "success" if sync_ok else "partial"
        if orchestration_ok is False:
            return "partial" if sync_ok else "failed"
        # 理论上不应出现：有编排标记但 ok 未知
        return "partial" if sync_ok else "failed"
    if not sync_ok:
        return "failed"
    return "success"
