from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import ModelUsageEvent

logger = logging.getLogger(__name__)


def log_model_usage_event(
    db: Optional[Session] = None,
    *,
    category: str,
    event_kind: str,
    success: bool,
    user_id: Optional[int] = None,
    requested_model: str = "",
    model: str = "",
    provider: str = "",
    channel: str = "",
    route: str = "",
    endpoint: str = "",
    request_id: str = "",
    latency_ms: Optional[int] = None,
    error_message: str = "",
    meta: Optional[Dict[str, Any]] = None,
    commit: bool = True,
) -> Optional[ModelUsageEvent]:
    own_db = db is None
    work_db = db or SessionLocal()
    try:
        row = ModelUsageEvent(
            user_id=user_id,
            category=(category or "").strip() or "unknown",
            event_kind=(event_kind or "").strip() or "request",
            requested_model=(requested_model or "").strip() or None,
            model=(model or "").strip() or None,
            provider=(provider or "").strip() or None,
            channel=(channel or "").strip() or None,
            route=(route or "").strip() or None,
            endpoint=(endpoint or "").strip() or None,
            request_id=(request_id or "").strip() or None,
            success=bool(success),
            latency_ms=latency_ms,
            error_message=(error_message or "").strip()[:4000] or None,
            meta=meta or None,
        )
        work_db.add(row)
        if commit:
            work_db.commit()
            work_db.refresh(row)
        return row
    except Exception as exc:
        try:
            work_db.rollback()
        except Exception:
            pass
        logger.warning("[model-usage] persist failed: %s", exc)
        return None
    finally:
        if own_db:
            try:
                work_db.close()
            except Exception:
                pass


def _top_group_counts(
    db: Session,
    *,
    start: datetime,
    end: datetime,
    category: str,
    event_kind: str,
    group_fields: List[Any],
    limit: int = 5,
) -> List[Dict[str, Any]]:
    q = (
        db.query(
            *group_fields,
            func.count(ModelUsageEvent.id).label("total"),
            func.sum(case((ModelUsageEvent.success.is_(True), 1), else_=0)).label("success"),
            func.sum(case((ModelUsageEvent.success.is_(False), 1), else_=0)).label("failed"),
        )
        .filter(
            ModelUsageEvent.created_at >= start,
            ModelUsageEvent.created_at < end,
            ModelUsageEvent.category == category,
            ModelUsageEvent.event_kind == event_kind,
        )
        .group_by(*group_fields)
        .order_by(func.count(ModelUsageEvent.id).desc())
        .limit(limit)
    )
    rows = []
    for row in q.all():
        mapping = row._mapping
        item: Dict[str, Any] = {
            "total": int(mapping.get("total") or 0),
            "success": int(mapping.get("success") or 0),
            "failed": int(mapping.get("failed") or 0),
        }
        for key, value in mapping.items():
            if key in {"total", "success", "failed"}:
                continue
            item[key] = value or ""
        rows.append(item)
    return rows


def collect_model_usage_snapshot(db: Session, *, start: datetime, end: datetime) -> Dict[str, Any]:
    categories = ("dialog", "image", "video")
    event_kinds = ("request", "attempt")
    out: Dict[str, Any] = {}

    for category in categories:
        cat_data: Dict[str, Any] = {}
        for event_kind in event_kinds:
            total = int(
                db.query(func.count(ModelUsageEvent.id))
                .filter(
                    ModelUsageEvent.created_at >= start,
                    ModelUsageEvent.created_at < end,
                    ModelUsageEvent.category == category,
                    ModelUsageEvent.event_kind == event_kind,
                )
                .scalar()
                or 0
            )
            success = int(
                db.query(func.count(ModelUsageEvent.id))
                .filter(
                    ModelUsageEvent.created_at >= start,
                    ModelUsageEvent.created_at < end,
                    ModelUsageEvent.category == category,
                    ModelUsageEvent.event_kind == event_kind,
                    ModelUsageEvent.success.is_(True),
                )
                .scalar()
                or 0
            )
            failed = int(
                db.query(func.count(ModelUsageEvent.id))
                .filter(
                    ModelUsageEvent.created_at >= start,
                    ModelUsageEvent.created_at < end,
                    ModelUsageEvent.category == category,
                    ModelUsageEvent.event_kind == event_kind,
                    ModelUsageEvent.success.is_(False),
                )
                .scalar()
                or 0
            )
            cat_data[event_kind] = {
                "total": total,
                "success": success,
                "failed": failed,
                "top_models": _top_group_counts(
                    db,
                    start=start,
                    end=end,
                    category=category,
                    event_kind=event_kind,
                    group_fields=[ModelUsageEvent.model.label("model")],
                    limit=5,
                ),
                "top_providers": _top_group_counts(
                    db,
                    start=start,
                    end=end,
                    category=category,
                    event_kind=event_kind,
                    group_fields=[
                        ModelUsageEvent.provider.label("provider"),
                        ModelUsageEvent.channel.label("channel"),
                    ],
                    limit=5,
                ),
            }
        out[category] = cat_data
    return out
