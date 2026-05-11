"""审核后发布：首条时间 + 间隔 与前端一致，用于 next_run_at 与队列游标。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


def _iv_minutes(sch) -> int:
    return max(1, int(getattr(sch, "interval_minutes", None) or 60))


def is_review_mode(sch) -> bool:
    mode = (getattr(sch, "schedule_publish_mode", None) or "immediate").strip().lower()
    return mode == "review"


def has_pending_review_queue(sch) -> bool:
    if not is_review_mode(sch) or not bool(getattr(sch, "review_confirmed", False)):
        return False
    drafts = getattr(sch, "review_drafts_json", None) or []
    return isinstance(drafts, list) and len(drafts) > 0


def compute_review_base_utc_naive(sch) -> datetime:
    """第 0 条基准时间（UTC naive）。须与前端「首条延后分钟 / 首条 ETA」语义一致。

    未指定 review_first_eta_at 时**不得**用 DB 里的 next_run_at 作基准：后者常为 bootstrap/普通定时顺延的
    「整段间隔后的下次 tick」（例如十几小时后），会导致「确认并发布」后 next_run_at 仍极远、编排一直不跑。
    未指定首条时间时一律视为马上发，从当前时刻起算。
    """
    eta = getattr(sch, "review_first_eta_at", None)
    if eta is not None:
        if isinstance(eta, datetime):
            return eta.replace(tzinfo=None) if eta.tzinfo else eta
    return datetime.utcnow()


def compute_review_slot_time_naive(sch, slot_index: int) -> datetime:
    """第 slot_index 条的计划时间（UTC naive）。"""
    base = compute_review_base_utc_naive(sch)
    iv = _iv_minutes(sch)
    return base + timedelta(minutes=iv * int(slot_index))


def compute_next_review_run_at_naive(sch, now: datetime) -> datetime:
    """
    当前游标 review_selected_slot 指向「下一条待发布」草稿时，下次触发时间。
    若队列已清空（由编排层提交），不应调用本函数。
    """
    cur = int(getattr(sch, "review_selected_slot", 0) or 0)
    t = compute_review_slot_time_naive(sch, cur)
    return max(t, now)


def compute_next_review_run_at_after_orchestration(
    sch, now: datetime
) -> Optional[datetime]:
    """
    一次编排成功后：游标已在 DB 中前进；若仍有待发布则排到下一槽时间。
    审核队列结束后返回 None，避免继续按普通间隔写“只同步”的任务记录。
    """
    if not has_pending_review_queue(sch):
        return None
    drafts = getattr(sch, "review_drafts_json", None) or []
    cur = int(getattr(sch, "review_selected_slot", 0) or 0)
    if cur >= len(drafts):
        return None
    return compute_next_review_run_at_naive(sch, now)
