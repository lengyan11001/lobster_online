"""审核发布草稿快照：写入、摘要、按账号裁剪条数。"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from sqlalchemy.orm import Session

from ..models import CreatorScheduleReviewSnapshot

logger = logging.getLogger(__name__)

MAX_SNAPSHOTS_PER_ACCOUNT = 60


def summarize_drafts(drafts: Any) -> str:
    if not isinstance(drafts, list) or not drafts:
        return "0 条"
    n = len(drafts)
    gen = 0
    for d in drafts:
        if not isinstance(d, dict):
            continue
        g = d.get("generated")
        if isinstance(g, dict) and (
            (g.get("reply_excerpt") or "").strip()
            or (g.get("asset_ids") or [])
            or (g.get("preview_urls") or [])
        ):
            gen += 1
    return f"{n} 条提示词，{gen} 条含生成预览"


def _prune_old(db: Session, *, account_id: int) -> None:
    rows: List[CreatorScheduleReviewSnapshot] = (
        db.query(CreatorScheduleReviewSnapshot)
        .filter(CreatorScheduleReviewSnapshot.account_id == account_id)
        .order_by(CreatorScheduleReviewSnapshot.created_at.asc())
        .all()
    )
    if len(rows) <= MAX_SNAPSHOTS_PER_ACCOUNT:
        return
    for r in rows[: len(rows) - MAX_SNAPSHOTS_PER_ACCOUNT]:
        db.delete(r)


def append_review_snapshot(
    db: Session,
    *,
    user_id: int,
    account_id: int,
    kind: str,
    status: str,
    drafts_json: Any,
    error_detail: Optional[str] = None,
) -> Optional[int]:
    """写入一条快照并裁剪旧记录；失败时仍应落库以便用户看到错误原因。"""
    try:
        summary = summarize_drafts(drafts_json)
        row = CreatorScheduleReviewSnapshot(
            user_id=user_id,
            account_id=account_id,
            kind=(kind or "unknown")[:32],
            status=(status or "ok")[:16],
            summary=summary[:512],
            drafts_json=drafts_json,
            error_detail=(error_detail or None),
        )
        db.add(row)
        db.flush()
        sid = int(row.id)
        _prune_old(db, account_id=account_id)
        return sid
    except Exception:
        logger.exception(
            "append_review_snapshot failed account_id=%s kind=%s", account_id, kind
        )
        return None


def snapshot_to_list_item(row: CreatorScheduleReviewSnapshot) -> dict:
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "kind": row.kind,
        "status": row.status,
        "summary": row.summary or "",
        "error_detail": (row.error_detail or "")[:2000] if row.error_detail else None,
    }
