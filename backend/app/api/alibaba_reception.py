"""阿里询盘 AI 接待（排期 / 红线 / 接待状态机）与公海池激活。

这一层是"新框架"：不再依赖旧的"同步后一进一回 + 话术摘要"，而是

* 排期驱动 + 在线自适应频率（常态 30 分钟 / 在线 60s / 秒读 30s）
* 每个询盘一个接待状态机（真人确认 → 采集 → 背调 → 分级 → 动作）
* 红线写死：轮次上限、长度上限、随机延迟、禁词、命中即转人工
* dry-run：先只生成不发送，用来在速腾环境里看 AI 味
* 公海池：T0 / T+2d / T+5d 三次触达，回复即转成询盘
"""
from __future__ import annotations

import hashlib
import logging
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .alibaba_inquiries import (
    ReplyDraftBody,
    _account_lock,
    _account_or_404,
    _enqueue_archive_enrichment,
    _get_account_page,
    _goto,
    _latest_reply_state,
    _now,
    _send_reply_via_page,
    draft_reply,
)
from .alibaba_backtest import (
    assess_info_sufficiency,
    backtest_verdict,
    build_info_request_instruction,
    is_human_like_message,
)
from .auth import _ServerUser, get_current_user_for_local
from ..db import get_db
from ..models import (
    AlibabaCustomerArchive,
    AlibabaCustomerArchiveEvidence,
    AlibabaCustomerProfile,
    AlibabaInquiry,
    AlibabaInquiryMessage,
    AlibabaInquiryPhraseSummary,
    AlibabaInquiryTrainingDoc,
    AlibabaPublicPoolTarget,
    AlibabaReceptionConfig,
    AlibabaReceptionSession,
)

logger = logging.getLogger(__name__)
router = APIRouter()

DEFAULT_HANDOFF_TRIGGERS: List[str] = [
    "真人", "人工", "投诉", "退款", "律师", "合同条款", "大额", "验厂", "commission",
    "human", "real person", "complain", "refund", "lawyer", "manager",
]
DEFAULT_BANNED_WORDS: List[str] = [
    "最低价", "绝对", "保证", "100%", "免费送", "cheapest", "best price", "guarantee", "百分百",
]
DEFAULT_PERSONA: Dict[str, Any] = {
    "name": "",
    "title": "Sales Manager",
    "company": "",
    "style": "简短、口语化、专业；一问一答；不重复对方原话；不用书面腔；每次只推进一小步",
    "opening": "先自我介绍 + 公司一句话，再确认对方是否是决策人",
    "ask_order": ["公司名称", "官网", "邮箱", "电话/WhatsApp", "需求型号与数量"],
}
POOL_TOUCH_OFFSETS_DAYS = (0, 2, 5)

# 已读未回：换角度撩动（每轮换一个角度，不重复；用完即停，转公海池）
DEFAULT_NUDGE_ANGLES: List[Dict[str, str]] = [
    {"key": "value_case", "label": "价值/案例",
     "goal": "用同行或同类买家的落地场景切入，问对方是不是也在做这件事"},
    {"key": "new_price", "label": "新品/新价",
     "goal": "提到新型号或更新后的报价表已经就绪，问要不要现在发过去"},
    {"key": "choice", "label": "选择题",
     "goal": "给两个具体选项让对方低成本回答（型号 A 还是 B / 先样品还是整单）"},
    {"key": "resource", "label": "资源",
     "goal": "提供一份对方用得上的资料（选型指南/认证/检测报告），换一次回复"},
]
NUDGE_MAX_CHARS = 240


# ---------------------------------------------------------------- 配置与规则（纯函数，便于测试）

def default_reception_config() -> Dict[str, Any]:
    return {
        "enabled": False,
        "dry_run": True,
        "pending_window_days": 30,
        "interval_minutes": 30,
        "online_interval_seconds": 60,
        "hot_interval_seconds": 30,
        "work_window_start": "08:00",
        "work_window_end": "23:00",
        "max_turns": 8,
        "max_chars": 380,
        "delay_min_seconds": 25,
        "delay_max_seconds": 90,
        "accept_small_orders": True,
        "accept_personal_orders": False,
        "handoff_triggers": list(DEFAULT_HANDOFF_TRIGGERS),
        "banned_words": list(DEFAULT_BANNED_WORDS),
        "persona": dict(DEFAULT_PERSONA),
        "read_no_reply_enabled": True,
        "nudge_max": 3,
        "nudge_intervals_minutes": [120, 1440, 4320],
        "nudge_angles": [dict(item) for item in DEFAULT_NUDGE_ANGLES],
        "nudge_max_chars": NUDGE_MAX_CHARS,
    }


def _as_list(value: Any, fallback: List[str]) -> List[str]:
    if isinstance(value, list):
        items = [str(x).strip() for x in value if str(x or "").strip()]
        if items:
            return items
    if isinstance(value, str):
        items = [x.strip() for x in value.replace("，", ",").split(",") if x.strip()]
        if items:
            return items
    return list(fallback)


def _as_int_list(value: Any, fallback: List[int]) -> List[int]:
    items: List[int] = []
    if isinstance(value, (list, tuple)):
        for raw in value:
            try:
                number = int(float(str(raw).strip()))
            except (TypeError, ValueError):
                continue
            if number > 0:
                items.append(number)
    elif isinstance(value, str):
        for chunk in value.replace("，", ",").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                number = int(float(chunk))
            except ValueError:
                continue
            if number > 0:
                items.append(number)
    return items or list(fallback)


def _as_angle_list(value: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                key = str(item.get("key") or "").strip()
                label = str(item.get("label") or key).strip()
                goal = str(item.get("goal") or "").strip()
            else:
                text = str(item or "").strip()
                if not text:
                    continue
                key, label, goal = text, text, text
            if not (key or label or goal):
                continue
            out.append({"key": key or label, "label": label or key, "goal": goal or label})
    return out or [dict(item) for item in DEFAULT_NUDGE_ANGLES]


def next_nudge_angle(config: Dict[str, Any], turn_index: int) -> Dict[str, str]:
    """第 N 次撩动用哪个角度：按顺序轮转，保证每次角度不同。"""
    angles = _as_angle_list(config.get("nudge_angles"))
    if not angles:
        return {"key": "value_case", "label": "价值/案例", "goal": ""}
    return angles[int(turn_index or 0) % len(angles)]


def config_to_dict(row: Optional[AlibabaReceptionConfig]) -> Dict[str, Any]:
    base = default_reception_config()
    if row is None:
        return base
    base.update(
        {
            "id": row.id,
            "account_id": row.account_id,
            "enabled": bool(row.enabled),
            "dry_run": bool(row.dry_run),
            "interval_minutes": int(row.interval_minutes or 30),
            "online_interval_seconds": int(row.online_interval_seconds or 60),
            "hot_interval_seconds": int(row.hot_interval_seconds or 30),
            "work_window_start": row.work_window_start or "08:00",
            "work_window_end": row.work_window_end or "23:00",
            "max_turns": int(row.max_turns or 8),
            "max_chars": int(row.max_chars or 380),
            "delay_min_seconds": int(row.delay_min_seconds or 25),
            "delay_max_seconds": int(row.delay_max_seconds or 90),
            "accept_small_orders": bool(row.accept_small_orders),
            "accept_personal_orders": bool(row.accept_personal_orders),
            "handoff_triggers": _as_list(row.handoff_triggers, DEFAULT_HANDOFF_TRIGGERS),
            "banned_words": _as_list(row.banned_words, DEFAULT_BANNED_WORDS),
            "persona": row.persona if isinstance(row.persona, dict) and row.persona else dict(DEFAULT_PERSONA),
            "pending_window_days": int(
                (row.meta or {}).get("pending_window_days", 30)
                if isinstance(row.meta, dict)
                else 30
            ),
            "read_no_reply_enabled": bool((row.meta or {}).get("read_no_reply_enabled", True))
            if isinstance(row.meta, dict) else True,
            "nudge_max": int((row.meta or {}).get("nudge_max", 3)) if isinstance(row.meta, dict) else 3,
            "nudge_intervals_minutes": _as_int_list(
                (row.meta or {}).get("nudge_intervals_minutes"), [120, 1440, 4320]
            ) if isinstance(row.meta, dict) else [120, 1440, 4320],
            "nudge_angles": _as_angle_list((row.meta or {}).get("nudge_angles"))
            if isinstance(row.meta, dict) else [dict(item) for item in DEFAULT_NUDGE_ANGLES],
            "nudge_max_chars": int((row.meta or {}).get("nudge_max_chars", NUDGE_MAX_CHARS))
            if isinstance(row.meta, dict) else NUDGE_MAX_CHARS,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
    )
    return base


def _parse_hhmm(value: str, fallback: str) -> int:
    text = str(value or fallback).strip()
    try:
        hour, minute = text.split(":", 1)
        return max(0, min(23, int(hour))) * 60 + max(0, min(59, int(minute)))
    except Exception:
        hour, minute = str(fallback).split(":", 1)
        return int(hour) * 60 + int(minute)


def in_work_window(now: datetime, start: str, end: str) -> bool:
    """工作时间窗（本地时间）。窗口跨零点也能正确判断。"""
    cur = now.hour * 60 + now.minute
    begin = _parse_hhmm(start, "08:00")
    finish = _parse_hhmm(end, "23:00")
    if begin == finish:
        return True
    if begin < finish:
        return begin <= cur <= finish
    return cur >= begin or cur <= finish


def next_scan_seconds(config: Dict[str, Any], *, online_state: str = "unknown", hot: bool = False) -> int:
    """在线自适应频率：秒读 30s / 在线 60s / 常态 30 分钟。"""
    if str(online_state or "").lower() in {"hot", "typing"} or hot:
        return max(10, int(config.get("hot_interval_seconds") or 30))
    if str(online_state or "").lower() in {"online", "active"}:
        return max(15, int(config.get("online_interval_seconds") or 60))
    return max(60, int(config.get("interval_minutes") or 30) * 60)


def clamp_reply_text(text: str, max_chars: int) -> str:
    """限制长度：优先断句，其次硬截断。"""
    body = str(text or "").strip()
    limit = max(40, int(max_chars or 380))
    if len(body) <= limit:
        return body
    window = body[:limit]
    for sep in ("\n\n", "\n", "。", ". ", "!", "?", "！", "？"):
        idx = window.rfind(sep)
        if idx >= limit * 0.5:
            return window[: idx + len(sep)].strip()
    return window.strip()


def find_banned_words(text: str, words: List[str]) -> List[str]:
    body = str(text or "")
    return [w for w in (words or []) if w and w.lower() in body.lower()]


def find_handoff_triggers(text: str, triggers: List[str]) -> List[str]:
    body = str(text or "")
    return [t for t in (triggers or []) if t and t.lower() in body.lower()]


def pool_plan_times(now: Optional[datetime] = None) -> List[datetime]:
    anchor = now or _now()
    return [anchor + timedelta(days=offset) for offset in POOL_TOUCH_OFFSETS_DAYS]


def reply_delay_seconds(config: Dict[str, Any]) -> float:
    low = max(0, int(config.get("delay_min_seconds") or 25))
    high = max(low, int(config.get("delay_max_seconds") or 90))
    return float(random.uniform(low, high))


# ---------------------------------------------------------------- 会话与配置存取

def _get_or_create_config(db: Session, user_id: int, account_id: int) -> AlibabaReceptionConfig:
    row = (
        db.query(AlibabaReceptionConfig)
        .filter(AlibabaReceptionConfig.user_id == user_id, AlibabaReceptionConfig.account_id == account_id)
        .first()
    )
    if row:
        return row
    row = AlibabaReceptionConfig(
        user_id=user_id,
        account_id=account_id,
        enabled=False,
        dry_run=True,
        interval_minutes=30,
        online_interval_seconds=60,
        hot_interval_seconds=30,
        work_window_start="08:00",
        work_window_end="23:00",
        max_turns=8,
        max_chars=380,
        delay_min_seconds=25,
        delay_max_seconds=90,
        accept_small_orders=True,
        accept_personal_orders=False,
        handoff_triggers=list(DEFAULT_HANDOFF_TRIGGERS),
        banned_words=list(DEFAULT_BANNED_WORDS),
        persona=dict(DEFAULT_PERSONA),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _get_or_create_session(db: Session, user_id: int, account_id: int, inquiry_id: str) -> AlibabaReceptionSession:
    row = (
        db.query(AlibabaReceptionSession)
        .filter(
            AlibabaReceptionSession.account_id == account_id,
            AlibabaReceptionSession.inquiry_id == inquiry_id,
        )
        .first()
    )
    if row:
        return row
    row = AlibabaReceptionSession(
        user_id=user_id,
        account_id=account_id,
        inquiry_id=inquiry_id,
        stage="new",
        read_state="unknown",
        online_state="unknown",
        turn_count=0,
        priority="normal",
        human_takeover=False,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def session_to_dict(row: AlibabaReceptionSession) -> Dict[str, Any]:
    return {
        "inquiry_id": row.inquiry_id,
        "stage": row.stage,
        "read_state": row.read_state,
        "online_state": row.online_state,
        "turn_count": int(row.turn_count or 0),
        "priority": row.priority,
        "grade": row.grade,
        "score": row.score,
        "human_takeover": bool(row.human_takeover),
        "last_buyer_at": row.last_buyer_at.isoformat() if row.last_buyer_at else None,
        "last_seller_at": row.last_seller_at.isoformat() if row.last_seller_at else None,
        "cooldown_until": row.cooldown_until.isoformat() if row.cooldown_until else None,
        "last_action": row.last_action,
        "last_action_at": row.last_action_at.isoformat() if row.last_action_at else None,
        "meta": row.meta if isinstance(row.meta, dict) else {},
    }


def _sync_session_from_messages(db: Session, row: AlibabaReceptionSession) -> AlibabaReceptionSession:
    """用本地消息记录刷新状态机：最后一条是买家 → 待回；卖家回得多 → 已读已回。"""
    latest = _latest_reply_state(db, row.account_id, row.inquiry_id)
    buyer = latest.get("buyer")
    seller = latest.get("seller")
    row.last_buyer_at = getattr(buyer, "sent_at", None) or row.last_buyer_at
    row.last_seller_at = getattr(seller, "sent_at", None) or row.last_seller_at
    if buyer and seller:
        row.read_state = "read_replied"
    elif seller:
        row.read_state = "read_no_reply"
    elif buyer:
        row.read_state = "unread"
    if row.turn_count == 0:
        row.turn_count = int(
            db.query(func.count(AlibabaInquiryMessage.id))
            .filter(
                AlibabaInquiryMessage.account_id == row.account_id,
                AlibabaInquiryMessage.inquiry_id == row.inquiry_id,
                AlibabaInquiryMessage.direction == "seller",
            )
            .scalar()
            or 0
        )
    if row.stage in {"", "new"} and row.read_state == "read_replied":
        row.stage = "confirm_human"
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return row


def _pending_inquiries(
    db: Session,
    user_id: int,
    account_id: int,
    limit: int = 20,
    *,
    window_days: int = 0,
    now: Optional[datetime] = None,
) -> List[AlibabaInquiry]:
    """待回询盘：买家最后一条比卖家新（阿里那边的"已读未回 / 未处理"）。

    window_days > 0 时只看最近 N 天内还有动静的会话，避免把几个月前的历史询盘
    当成"待处理"，把接待台的数字撑虚高。window_days = 0 表示不过滤（全部历史）。
    """
    anchor = now or _now()
    deadline = anchor - timedelta(days=int(window_days)) if int(window_days or 0) > 0 else None
    rows = (
        db.query(AlibabaInquiry)
        .filter(AlibabaInquiry.user_id == user_id, AlibabaInquiry.account_id == account_id)
        .order_by(AlibabaInquiry.last_message_at.desc().nullslast(), AlibabaInquiry.id.desc())
        .limit(400)
        .all()
    )
    out: List[AlibabaInquiry] = []
    for inquiry in rows:
        latest = _latest_reply_state(db, account_id, inquiry.inquiry_id)
        buyer, seller = latest.get("buyer"), latest.get("seller")
        if buyer is None:
            continue
        buyer_at = getattr(buyer, "sent_at", None) or getattr(buyer, "created_at", None)
        seller_at = getattr(seller, "sent_at", None) or getattr(seller, "created_at", None)
        if not (seller_at is None or (buyer_at and buyer_at > seller_at)):
            continue
        if deadline is not None:
            latest_at = max([value for value in (buyer_at, seller_at) if value] or [None]) if (buyer_at or seller_at) else None
            if latest_at is None or latest_at < deadline:
                continue
        out.append(inquiry)
        if len(out) >= limit:
            break
    return out


def pending_counts(
    db: Session,
    user_id: int,
    account_id: int,
    *,
    window_days: int = 30,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    """窗口内待处理 / 窗口外历史 两个数字，接待台用它解释"为什么是这个数"。"""
    anchor = now or _now()
    in_window = _pending_inquiries(db, user_id, account_id, limit=500, window_days=window_days, now=anchor)
    all_pending = _pending_inquiries(db, user_id, account_id, limit=500, window_days=0, now=anchor)
    return {
        "in_window": len(in_window),
        "historical": max(0, len(all_pending) - len(in_window)),
        "total": len(all_pending),
    }


def _nudge_state(session: AlibabaReceptionSession) -> Dict[str, Any]:
    meta = session.meta if isinstance(session.meta, dict) else {}
    try:
        count = int(meta.get("nudge_count") or 0)
    except (TypeError, ValueError):
        count = 0
    return {
        "count": count,
        "last_angle": str(meta.get("last_nudge_angle") or ""),
        "last_at": _parse_iso(meta.get("last_nudge_at")),
    }


def _parse_iso(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def nudge_due_at(config: Dict[str, Any], session: AlibabaReceptionSession, now: Optional[datetime] = None) -> Optional[datetime]:
    """下一次可以撩动的时间点（None = 不该再撩）。"""
    if not bool(config.get("read_no_reply_enabled", True)):
        return None
    state = _nudge_state(session)
    max_times = int(config.get("nudge_max") or 0)
    intervals = _as_int_list(config.get("nudge_intervals_minutes"), [120, 1440, 4320])
    if state["count"] >= max_times:
        return None
    if not session.last_seller_at:
        return None
    if session.last_buyer_at and session.last_seller_at <= session.last_buyer_at:
        return None      # 买家又说话了 → 走正常接待，不再撩
    index = min(state["count"], len(intervals) - 1)
    anchor = state["last_at"] or session.last_seller_at
    return anchor + timedelta(minutes=int(intervals[index]))


def _nudge_candidates(
    db: Session,
    user_id: int,
    account_id: int,
    *,
    config: Dict[str, Any],
    limit: int = 3,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """已读未回、到点可以换角度撩动的会话（按最久没动静的先来）。"""
    anchor = now or _now()
    rows = (
        db.query(AlibabaReceptionSession)
        .filter(
            AlibabaReceptionSession.account_id == account_id,
            AlibabaReceptionSession.human_takeover.is_(False),
        )
        .all()
    )
    out: List[Dict[str, Any]] = []
    for session in rows:
        due = nudge_due_at(config, session, anchor)
        if due is None or due > anchor:
            continue
        inquiry = (
            db.query(AlibabaInquiry)
            .filter(
                AlibabaInquiry.user_id == user_id,
                AlibabaInquiry.account_id == account_id,
                AlibabaInquiry.inquiry_id == session.inquiry_id,
            )
            .first()
        )
        if not inquiry:
            continue
        state = _nudge_state(session)
        out.append({
            "session": session,
            "inquiry": inquiry,
            "nudge_index": state["count"],
            "last_angle": state["last_angle"],
            "due_at": due,
        })
    out.sort(key=lambda item: item["due_at"])
    return out[: max(1, limit)]


def archive_facts(db: Session, account_id: int, inquiry_id: str) -> Dict[str, Any]:
    """汇总用于"信息够不够/结论"的事实：档案字段 + 证据数 + 命中来源。"""
    archive = (
        db.query(AlibabaCustomerArchive)
        .filter(AlibabaCustomerArchive.account_id == account_id, AlibabaCustomerArchive.inquiry_id == inquiry_id)
        .first()
    )
    inquiry = (
        db.query(AlibabaInquiry)
        .filter(AlibabaInquiry.account_id == account_id, AlibabaInquiry.inquiry_id == inquiry_id)
        .first()
    )
    profile = (
        db.query(AlibabaCustomerProfile)
        .filter(AlibabaCustomerProfile.account_id == account_id, AlibabaCustomerProfile.inquiry_id == inquiry_id)
        .first()
    )
    seed = archive.seed if archive and isinstance(archive.seed, dict) else {}
    fields = {
        "company_name": (archive.company_name if archive else None) or (inquiry.company_name if inquiry else None)
        or seed.get("company_name"),
        "domain": (archive.domain if archive else None) or seed.get("domain"),
        "email": (archive.email if archive else None) or (profile.email if profile else None) or seed.get("email"),
        "phone": (archive.phone if archive else None) or seed.get("phone"),
        "country": (archive.country if archive else None) or (inquiry.country if inquiry else None),
        "buyer_login_id": inquiry.buyer_login_id if inquiry else None,
    }
    sources: List[str] = []
    evidence_count = 0
    if archive:
        rows = (
            db.query(AlibabaCustomerArchiveEvidence)
            .filter(AlibabaCustomerArchiveEvidence.archive_id == archive.id)
            .all()
        )
        evidence_count = len(rows)
        for item in rows:
            key = str(item.source_type or "").strip()
            if key and key not in sources and not key.startswith("alibaba_"):
                sources.append(key)
    return {
        "fields": {key: value for key, value in fields.items() if str(value or "").strip()},
        "grade": (archive.grade if archive else "") or "",
        "score": archive.score if archive else None,
        "evidence_count": evidence_count,
        "sources": sources[:8],
    }


def reception_verdict(db: Session, account_id: int, inquiry_id: str) -> Dict[str, Any]:
    facts = archive_facts(db, account_id, inquiry_id)
    verdict = backtest_verdict(
        fields=facts["fields"],
        evidence_count=facts["evidence_count"],
        sources_hit=facts["sources"],
        grade=facts["grade"],
        score=facts["score"],
    )
    verdict["sufficiency"] = assess_info_sufficiency(facts["fields"])
    verdict["fields"] = facts["fields"]
    return verdict


# ---------------------------------------------------------------- 请求体

class ReceptionConfigBody(BaseModel):
    enabled: Optional[bool] = None
    dry_run: Optional[bool] = None
    pending_window_days: Optional[int] = Field(default=None, ge=0, le=3650)
    read_no_reply_enabled: Optional[bool] = None
    nudge_max: Optional[int] = Field(default=None, ge=0, le=10)
    nudge_intervals_minutes: Optional[List[int]] = None
    nudge_angles: Optional[List[Dict[str, Any]]] = None
    nudge_max_chars: Optional[int] = Field(default=None, ge=60, le=1200)
    interval_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    online_interval_seconds: Optional[int] = Field(default=None, ge=10, le=3600)
    hot_interval_seconds: Optional[int] = Field(default=None, ge=10, le=3600)
    work_window_start: Optional[str] = None
    work_window_end: Optional[str] = None
    max_turns: Optional[int] = Field(default=None, ge=1, le=30)
    max_chars: Optional[int] = Field(default=None, ge=80, le=2000)
    delay_min_seconds: Optional[int] = Field(default=None, ge=0, le=600)
    delay_max_seconds: Optional[int] = Field(default=None, ge=0, le=1200)
    accept_small_orders: Optional[bool] = None
    accept_personal_orders: Optional[bool] = None
    handoff_triggers: Optional[List[str]] = None
    banned_words: Optional[List[str]] = None
    persona: Optional[Dict[str, Any]] = None


class ReceptionRunBody(BaseModel):
    dry_run: Optional[bool] = None
    limit: int = Field(default=5, ge=1, le=30)
    inquiry_ids: List[str] = Field(default_factory=list)
    include_nudges: bool = True
    nudge_limit: int = Field(default=3, ge=0, le=20)


class TakeoverBody(BaseModel):
    on: bool = True
    note: str = ""


class PoolImportBody(BaseModel):
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    source_scope: str = "main"


class PoolTouchBody(BaseModel):
    step: int = Field(default=1, ge=1, le=3)
    content: str = ""
    dry_run: bool = True


# ---------------------------------------------------------------- 配置接口

@router.get("/api/alibaba-inquiries/accounts/{account_id}/reception-config", summary="AI 接待排期与红线配置")
def get_reception_config(
    account_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    row = _get_or_create_config(db, current_user.id, account_id)
    data = config_to_dict(row)
    data["next_scan_seconds"] = next_scan_seconds(data)
    data["in_work_window"] = in_work_window(_now(), data["work_window_start"], data["work_window_end"])
    return {"ok": True, "config": data}


@router.post("/api/alibaba-inquiries/accounts/{account_id}/reception-config", summary="保存 AI 接待排期与红线配置")
def save_reception_config(
    account_id: int,
    body: ReceptionConfigBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    row = _get_or_create_config(db, current_user.id, account_id)
    payload = body.model_dump(exclude_none=True)
    window_days = payload.pop("pending_window_days", None)
    meta_keys = {
        "read_no_reply_enabled": "read_no_reply_enabled",
        "nudge_max": "nudge_max",
        "nudge_intervals_minutes": "nudge_intervals_minutes",
        "nudge_angles": "nudge_angles",
        "nudge_max_chars": "nudge_max_chars",
    }
    meta_updates = {key: payload.pop(key) for key in list(payload.keys()) if key in meta_keys}
    for key, value in payload.items():
        setattr(row, key, value)
    if window_days is not None or meta_updates:
        meta = dict(row.meta) if isinstance(row.meta, dict) else {}
        if window_days is not None:
            meta["pending_window_days"] = int(window_days)
        for key, value in meta_updates.items():
            if key == "nudge_angles":
                meta[key] = _as_angle_list(value)
            elif key == "nudge_intervals_minutes":
                meta[key] = _as_int_list(value, [120, 1440, 4320])
            else:
                meta[key] = value
        row.meta = meta
    if row.delay_max_seconds < row.delay_min_seconds:
        row.delay_max_seconds = row.delay_min_seconds
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    data = config_to_dict(row)
    data["next_scan_seconds"] = next_scan_seconds(data)
    data["in_work_window"] = in_work_window(_now(), data["work_window_start"], data["work_window_end"])
    return {"ok": True, "config": data}


# ---------------------------------------------------------------- 接待台总览

@router.get("/api/alibaba-inquiries/accounts/{account_id}/dashboard", summary="接待台总览")
def reception_dashboard(
    account_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    config = config_to_dict(_get_or_create_config(db, current_user.id, account_id))
    now = _now()
    today_start = datetime(now.year, now.month, now.day)

    inquiry_total = (
        db.query(func.count(AlibabaInquiry.id))
        .filter(AlibabaInquiry.user_id == current_user.id, AlibabaInquiry.account_id == account_id)
        .scalar()
        or 0
    )
    window_days = int(config.get("pending_window_days") or 0)
    pending = _pending_inquiries(db, current_user.id, account_id, limit=200, window_days=window_days, now=now)
    counts = pending_counts(db, current_user.id, account_id, window_days=window_days, now=now)
    try:
        nudge_ready = len(_nudge_candidates(
            db, current_user.id, account_id, config=config, limit=50, now=now
        ))
    except Exception:
        nudge_ready = 0
    sessions = (
        db.query(AlibabaReceptionSession)
        .filter(AlibabaReceptionSession.account_id == account_id)
        .all()
    )
    session_map = {s.inquiry_id: s for s in sessions}
    awaiting = counts["in_window"]
    online = sum(1 for s in sessions if str(s.online_state or "") in {"online", "hot", "typing"})
    read_no_reply = sum(1 for s in sessions if s.read_state == "read_no_reply")
    handoff = sum(1 for s in sessions if s.human_takeover)
    today_sent = (
        db.query(func.count(AlibabaInquiryMessage.id))
        .filter(
            AlibabaInquiryMessage.account_id == account_id,
            AlibabaInquiryMessage.direction == "seller",
            AlibabaInquiryMessage.sent_at >= today_start,
        )
        .scalar()
        or 0
    )
    pool_pending = (
        db.query(func.count(AlibabaPublicPoolTarget.id))
        .filter(
            AlibabaPublicPoolTarget.account_id == account_id,
            AlibabaPublicPoolTarget.status.in_(("pending", "touching")),
        )
        .scalar()
        or 0
    )
    kb_docs = (
        db.query(func.count(AlibabaInquiryTrainingDoc.id))
        .filter(
            AlibabaInquiryTrainingDoc.user_id == current_user.id,
            or_(
                AlibabaInquiryTrainingDoc.account_id == account_id,
                AlibabaInquiryTrainingDoc.account_id.is_(None),
            ),
        )
        .scalar()
        or 0
    )
    strategy = (
        db.query(AlibabaInquiryPhraseSummary)
        .filter(
            AlibabaInquiryPhraseSummary.user_id == current_user.id,
            or_(
                AlibabaInquiryPhraseSummary.account_id == account_id,
                AlibabaInquiryPhraseSummary.account_id.is_(None),
            ),
        )
        .order_by(AlibabaInquiryPhraseSummary.id.desc())
        .first()
    )

    queue: List[Dict[str, Any]] = []
    for inquiry in pending[:8]:
        row = session_map.get(inquiry.inquiry_id) or _get_or_create_session(
            db, current_user.id, account_id, inquiry.inquiry_id
        )
        item_verdict = reception_verdict(db, account_id, inquiry.inquiry_id)
        queue.append(
            {
                "inquiry_id": inquiry.inquiry_id,
                "title": inquiry.title,
                "buyer_name": inquiry.buyer_name,
                "company_name": inquiry.company_name,
                "country": inquiry.country,
                "preview": (inquiry.preview or "")[:160],
                "last_message_at": inquiry.last_message_at.isoformat() if inquiry.last_message_at else None,
                "session": session_to_dict(row),
                "verdict": item_verdict.get("verdict_label"),
                "verdict_key": item_verdict.get("verdict"),
                "notify": item_verdict.get("notify"),
                "info_level": (item_verdict.get("sufficiency") or {}).get("level"),
                "next_ask": (item_verdict.get("gaps") or [])[:2],
                "next_action": (
                    "人工已接管"
                    if row.human_takeover
                    else ("待确认对方是活人" if row.read_state in {"unread", "unknown"} else "可以继续接待")
                ),
            }
        )

    return {
        "ok": True,
        "stats": {
            "inquiries": int(inquiry_total),
            "awaiting_reply": awaiting,
            "pending_historical": counts["historical"],
            "pending_total": counts["total"],
            "pending_window_days": window_days,
            "nudge_ready": nudge_ready,
            "online": online,
            "read_no_reply": read_no_reply,
            "today_sent": int(today_sent),
            "human_takeover": handoff,
            "pool_pending": int(pool_pending),
            "kb_docs": int(kb_docs),
        },
        "config": config,
        "next_scan_seconds": next_scan_seconds(config),
        "strategy": {
            "id": strategy.id if strategy else None,
            "summary_type": strategy.summary_type if strategy else "",
            "content": (strategy.content or "")[:400] if strategy else "",
        },
        "queue": queue,
    }


@router.get(
    "/api/alibaba-inquiries/accounts/{account_id}/inquiries/{inquiry_id}/reception",
    summary="单个询盘的接待状态机",
)
def inquiry_reception(
    account_id: int,
    inquiry_id: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    inquiry = (
        db.query(AlibabaInquiry)
        .filter(
            AlibabaInquiry.user_id == current_user.id,
            AlibabaInquiry.account_id == account_id,
            AlibabaInquiry.inquiry_id == inquiry_id,
        )
        .first()
    )
    if not inquiry:
        raise HTTPException(404, detail="询盘不存在")
    row = _get_or_create_session(db, current_user.id, account_id, inquiry_id)
    row = _sync_session_from_messages(db, row)
    config = config_to_dict(_get_or_create_config(db, current_user.id, account_id))
    verdict = reception_verdict(db, account_id, inquiry_id)
    return {
        "ok": True,
        "session": session_to_dict(row),
        "config": config,
        "verdict": verdict,
        "info_sufficiency": verdict.get("sufficiency") or {},
        "next_ask": (verdict.get("gaps") or [])[:2],
        "next_scan_seconds": next_scan_seconds(config, online_state=row.online_state),
        "turns_left": max(0, int(config["max_turns"]) - int(row.turn_count or 0)),
    }


@router.post(
    "/api/alibaba-inquiries/accounts/{account_id}/inquiries/{inquiry_id}/takeover",
    summary="人工接管 / 交回 AI",
)
def set_takeover(
    account_id: int,
    inquiry_id: str,
    body: TakeoverBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    row = _get_or_create_session(db, current_user.id, account_id, inquiry_id)
    row.human_takeover = bool(body.on)
    row.stage = "handoff" if body.on else "confirm_human"
    row.last_action = "human_takeover" if body.on else "release_to_ai"
    row.last_action_at = _now()
    if body.note:
        meta = row.meta if isinstance(row.meta, dict) else {}
        meta["takeover_note"] = str(body.note)[:500]
        row.meta = meta
    db.commit()
    return {"ok": True, "session": session_to_dict(row)}


# ---------------------------------------------------------------- 跑一轮接待（支持 dry-run）

@router.post("/api/alibaba-inquiries/accounts/{account_id}/reception/run", summary="跑一轮 AI 接待")
async def run_reception(
    account_id: int,
    body: ReceptionRunBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = _account_or_404(db, current_user.id, account_id)
    config = config_to_dict(_get_or_create_config(db, current_user.id, account_id))
    dry_run = bool(config["dry_run"] if body.dry_run is None else body.dry_run)
    now = _now()
    result: Dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "enabled": bool(config["enabled"]),
        "in_work_window": in_work_window(now, config["work_window_start"], config["work_window_end"]),
        "items": [],
        "sent": 0,
        "previewed": 0,
        "skipped": 0,
        "handoff": 0,
        "blocked": 0,
        "nudged": 0,
        "nudge_previewed": 0,
        "not_human": 0,
        "enrich_queued": 0,
    }
    if not config["enabled"] and not dry_run:
        result["skipped_reason"] = "排期未启用（可先用 dry-run 预览）"
        return result
    if not result["in_work_window"] and not dry_run:
        result["skipped_reason"] = "当前不在工作时间窗内"
        return result

    if body.inquiry_ids:
        candidates = [
            i
            for i in _pending_inquiries(
                db, current_user.id, account_id, limit=200,
                window_days=int(config.get("pending_window_days") or 0),
            )
            if i.inquiry_id in set(body.inquiry_ids)
        ][: body.limit]
    else:
        candidates = _pending_inquiries(
            db, current_user.id, account_id, limit=body.limit,
            window_days=int(config.get("pending_window_days") or 0),
        )

    lock = await _account_lock(account_id, "reply")
    for inquiry in candidates:
        entry: Dict[str, Any] = {"inquiry_id": inquiry.inquiry_id, "buyer": inquiry.buyer_name or "", "status": "pending"}
        row = _sync_session_from_messages(db, _get_or_create_session(db, current_user.id, account_id, inquiry.inquiry_id))
        if row.human_takeover:
            entry.update({"status": "skipped", "reason": "人工已接管"})
            result["skipped"] += 1
            result["items"].append(entry)
            continue
        if int(row.turn_count or 0) >= int(config["max_turns"]):
            entry.update({"status": "skipped", "reason": "已达轮次上限"})
            result["skipped"] += 1
            result["items"].append(entry)
            continue
        if row.cooldown_until and row.cooldown_until > now:
            entry.update({"status": "skipped", "reason": "冷却中"})
            result["skipped"] += 1
            result["items"].append(entry)
            continue
        try:
            persona = config.get("persona") or {}
            facts = archive_facts(db, account_id, inquiry.inquiry_id)
            buyer_message = _latest_reply_state(db, account_id, inquiry.inquiry_id).get("buyer")
            human_check = is_human_like_message(getattr(buyer_message, "content", "") or "")
            entry["human_check"] = human_check
            if not human_check["human"]:
                meta = dict(row.meta) if isinstance(row.meta, dict) else {}
                meta["human_check"] = human_check
                row.meta = meta
                row.last_action = "skip_not_human"
                row.last_action_at = _now()
                db.commit()
                result["not_human"] = int(result.get("not_human") or 0) + 1
                entry.update({
                    "status": "skip_not_human",
                    "reason": "内容不像真人（%s）" % "、".join(human_check["reasons"]) if human_check["reasons"] else "内容过短/无具体需求",
                })
                result["items"].append(entry)
                continue
            sufficiency = assess_info_sufficiency(facts["fields"])
            entry["info_level"] = sufficiency["level"]
            instruction = build_info_request_instruction(sufficiency, persona)
            draft = await draft_reply(
                account_id,
                ReplyDraftBody(inquiry_id=inquiry.inquiry_id, instruction=instruction),
                request,
                current_user,
                db,
            )
            content = clamp_reply_text(str((draft or {}).get("draft", {}).get("reply") or ""), config["max_chars"])
            if not content:
                raise RuntimeError("draft empty")
            buyer_text = " ".join(
                str(getattr(m, "content", "") or "")
                for m in [
                    _latest_reply_state(db, account_id, inquiry.inquiry_id).get("buyer")
                ]
                if m is not None
            )
            triggers = find_handoff_triggers(f"{buyer_text}\n{content}", list(config["handoff_triggers"]))
            banned = find_banned_words(content, list(config["banned_words"]))
            if triggers:
                row.human_takeover = True
                row.stage = "handoff"
                row.last_action = "auto_handoff"
                row.last_action_at = _now()
                db.commit()
                result["handoff"] += 1
                entry.update({"status": "handoff", "reason": "命中转人工触发词：" + ",".join(triggers[:3]), "draft": content})
                result["items"].append(entry)
                continue
            if banned:
                result["blocked"] += 1
                entry.update({"status": "blocked", "reason": "命中禁词：" + ",".join(banned[:3]), "draft": content})
                result["items"].append(entry)
                continue
            if dry_run:
                result["previewed"] += 1
                entry.update({"status": "dry_run", "draft": content, "delay_seconds": int(reply_delay_seconds(config))})
                result["items"].append(entry)
                continue
            async with lock:
                page = await _get_account_page(acct, visible=True)
                await _goto(
                    page,
                    inquiry.source_url
                    or f"https://message.alibaba.com/message/maDetail.htm?imInquiryId={inquiry.inquiry_id}",
                )
                send = await _send_reply_via_page(page, content)
                if not send.get("ok"):
                    raise RuntimeError(str(send.get("error") or "send failed"))
            uid = hashlib.sha1(f"reception\0{_now().isoformat()}\0{content}".encode("utf-8")).hexdigest()[:40]
            db.add(
                AlibabaInquiryMessage(
                    user_id=current_user.id,
                    account_id=account_id,
                    inquiry_id=inquiry.inquiry_id,
                    message_uid=uid,
                    direction="seller",
                    sender_name="me",
                    content=content,
                    msg_type="text",
                    sent_at=_now(),
                    raw={"source": "ai_reception", "config_max_chars": config["max_chars"]},
                )
            )
            row.turn_count = int(row.turn_count or 0) + 1
            row.stage = "collect" if not sufficiency["can_enrich"] else "qualified"
            row.last_action = "ai_reply"
            row.last_action_at = _now()
            row.last_seller_at = _now()
            row.cooldown_until = _now() + timedelta(seconds=reply_delay_seconds(config))
            db.commit()
            result["sent"] += 1
            entry.update({"status": "sent", "draft": content})
            if sufficiency["can_enrich"]:
                # 信息够了 → 自动起背调（不再手动点）
                try:
                    profile = (
                        db.query(AlibabaCustomerProfile)
                        .filter(
                            AlibabaCustomerProfile.account_id == account_id,
                            AlibabaCustomerProfile.inquiry_id == inquiry.inquiry_id,
                        )
                        .first()
                    )
                    job = _enqueue_archive_enrichment(
                        db=db,
                        user_id=current_user.id,
                        account_id=account_id,
                        inquiry=inquiry,
                        profile=profile,
                        force=False,
                        max_results=8,
                    )
                    entry["enrich"] = "queued"
                    entry["enrich_job"] = (job or {}).get("job_id") or (job or {}).get("id")
                    result["enrich_queued"] = int(result.get("enrich_queued") or 0) + 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[ALI-RECEPTION] enqueue enrich failed inquiry=%s err=%s", inquiry.inquiry_id, exc)
                    entry["enrich"] = "failed"
            else:
                entry["next_ask"] = sufficiency["missing"]
            result["items"].append(entry)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ALI-RECEPTION] run failed inquiry=%s err=%s", inquiry.inquiry_id, exc)
            db.rollback()
            entry.update({"status": "failed", "error": str(exc)[:400]})
            result["items"].append(entry)

    # ---- 已读未回：换角度撩动（另一个角度的话术触达）----
    if body.include_nudges and int(body.nudge_limit or 0) > 0 and bool(config.get("read_no_reply_enabled", True)):
        nudge_candidates = _nudge_candidates(
            db, current_user.id, account_id, config=config, limit=int(body.nudge_limit), now=now
        )
        result["nudge_candidates"] = len(nudge_candidates)
        for candidate in nudge_candidates:
            session = candidate["session"]
            inquiry = candidate["inquiry"]
            angle = next_nudge_angle(config, candidate["nudge_index"])
            entry: Dict[str, Any] = {
                "inquiry_id": inquiry.inquiry_id,
                "buyer": inquiry.buyer_name or "",
                "kind": "nudge",
                "nudge_index": candidate["nudge_index"] + 1,
                "angle": angle.get("label") or angle.get("key"),
                "status": "pending",
            }
            if not result["in_work_window"] and not dry_run:
                entry.update({"status": "skipped", "reason": "不在工作时段"})
                result["skipped"] += 1
                result["items"].append(entry)
                continue
            try:
                persona = config.get("persona") or {}
                instruction = (
                    "本轮目的：换角度触达（第 %d 次，角度『%s』：%s）。"
                    "要求：%d 字符以内、一句话 + 一个轻问题、不要催单、不要重复上一轮角度（上一轮：%s）；"
                    "人称/公司按人设：%s %s @ %s"
                    % (
                        candidate["nudge_index"] + 1,
                        angle.get("label") or "",
                        angle.get("goal") or "",
                        int(config.get("nudge_max_chars") or NUDGE_MAX_CHARS),
                        candidate["last_angle"] or "无",
                        persona.get("name") or "",
                        persona.get("title") or "",
                        persona.get("company") or "",
                    )
                )
                draft = await draft_reply(
                    account_id,
                    ReplyDraftBody(inquiry_id=inquiry.inquiry_id, instruction=instruction),
                    request,
                    current_user,
                    db,
                )
                content = clamp_reply_text(
                    str((draft or {}).get("draft", {}).get("reply") or ""),
                    int(config.get("nudge_max_chars") or NUDGE_MAX_CHARS),
                )
                if not content:
                    raise RuntimeError("nudge draft empty")
                banned = find_banned_words(content, list(config["banned_words"]))
                if banned:
                    result["blocked"] += 1
                    entry.update({"status": "blocked", "reason": "命中禁词：" + ",".join(banned[:3]), "draft": content})
                    result["items"].append(entry)
                    continue
                if dry_run:
                    result["nudge_previewed"] += 1
                    entry.update({"status": "dry_run", "draft": content})
                    result["items"].append(entry)
                    continue
                async with lock:
                    page = await _get_account_page(acct, visible=True)
                    await _goto(
                        page,
                        inquiry.source_url
                        or f"https://message.alibaba.com/message/maDetail.htm?imInquiryId={inquiry.inquiry_id}",
                    )
                    send = await _send_reply_via_page(page, content)
                    if not send.get("ok"):
                        raise RuntimeError(str(send.get("error") or "send failed"))
                uid = hashlib.sha1(
                    f"nudge\0{_now().isoformat()}\0{content}".encode("utf-8")
                ).hexdigest()[:40]
                db.add(
                    AlibabaInquiryMessage(
                        user_id=current_user.id,
                        account_id=account_id,
                        inquiry_id=inquiry.inquiry_id,
                        message_uid=uid,
                        direction="seller",
                        sender_name="me",
                        content=content,
                        msg_type="text",
                        sent_at=_now(),
                        raw={"source": "ai_nudge", "angle": angle.get("key"), "nudge_index": candidate["nudge_index"] + 1},
                    )
                )
                meta = dict(session.meta) if isinstance(session.meta, dict) else {}
                meta["nudge_count"] = candidate["nudge_index"] + 1
                meta["last_nudge_angle"] = angle.get("label") or angle.get("key")
                meta["last_nudge_at"] = _now().isoformat()
                session.meta = meta
                session.stage = "nudge" if meta["nudge_count"] < int(config.get("nudge_max") or 3) else "dormant"
                session.last_action = "ai_nudge"
                session.last_action_at = _now()
                session.last_seller_at = _now()
                db.commit()
                result["nudged"] += 1
                entry.update({"status": "sent", "draft": content})
                result["items"].append(entry)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[ALI-RECEPTION] nudge failed inquiry=%s err=%s", inquiry.inquiry_id, exc)
                db.rollback()
                entry.update({"status": "failed", "error": str(exc)[:400]})
                result["items"].append(entry)

    result["next_scan_seconds"] = next_scan_seconds(config)
    return result


# ---------------------------------------------------------------- 公海池激活

def _pool_key(row: Dict[str, Any]) -> str:
    raw = "|".join(
        str(row.get(k) or "").strip().lower()
        for k in ("buyer_login_id", "buyer_name", "company_name", "country")
    ).strip("|")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:40] if raw else ""


def _pool_plan(payload: Dict[str, Any], persona: Dict[str, Any]) -> List[Dict[str, str]]:
    name = str(payload.get("buyer_name") or payload.get("company_name") or "there")
    company = str(persona.get("company") or "")
    sender = " ".join(x for x in [str(persona.get("name") or ""), str(persona.get("title") or "")] if x).strip()
    intro = f"Hi {name}, this is {sender}{(' from ' + company) if company else ''}."
    return [
        {
            "step": "T0",
            "content": f"{intro} We noticed your earlier inquiry about our products. Are you still sourcing this quarter?",
        },
        {
            "step": "T+2d",
            "content": f"{intro} Sharing our newest price list and lead time for this line — want me to send the details here?",
        },
        {
            "step": "T+5d",
            "content": f"{intro} Last check: if you are still comparing suppliers, tell me your target model/quantity and I will quote directly.",
        },
    ]


@router.get("/api/alibaba-inquiries/accounts/{account_id}/public-pool/targets", summary="公海池待激活列表")
def list_pool_targets(
    account_id: int,
    status: str = "",
    limit: int = 100,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    query = db.query(AlibabaPublicPoolTarget).filter(
        AlibabaPublicPoolTarget.user_id == current_user.id,
        AlibabaPublicPoolTarget.account_id == account_id,
    )
    if status:
        query = query.filter(AlibabaPublicPoolTarget.status == status)
    rows = (
        query.order_by(AlibabaPublicPoolTarget.next_touch_at.asc().nullslast(), AlibabaPublicPoolTarget.id.desc())
        .limit(max(1, min(500, limit)))
        .all()
    )
    return {
        "ok": True,
        "targets": [
            {
                "id": r.id,
                "buyer_key": r.buyer_key,
                "buyer_name": r.buyer_name,
                "company_name": r.company_name,
                "country": r.country,
                "product_line": r.product_line,
                "source_scope": r.source_scope,
                "status": r.status,
                "touch_count": int(r.touch_count or 0),
                "last_touch_at": r.last_touch_at.isoformat() if r.last_touch_at else None,
                "next_touch_at": r.next_touch_at.isoformat() if r.next_touch_at else None,
                "reply_at": r.reply_at.isoformat() if r.reply_at else None,
                "converted_inquiry_id": r.converted_inquiry_id,
                "chat_url": r.chat_url or "",
                "plan": r.plan if isinstance(r.plan, list) else [],
            }
            for r in rows
        ],
    }


@router.post("/api/alibaba-inquiries/accounts/{account_id}/public-pool/targets", summary="导入公海池客户")
def import_pool_targets(
    account_id: int,
    body: PoolImportBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    created, updated = 0, 0
    for raw in body.rows or []:
        key = str(raw.get("buyer_key") or "").strip() or _pool_key(raw)
        if not key:
            continue
        row = (
            db.query(AlibabaPublicPoolTarget)
            .filter(
                AlibabaPublicPoolTarget.account_id == account_id,
                AlibabaPublicPoolTarget.buyer_key == key,
            )
            .first()
        )
        fields = {
            "buyer_name": str(raw.get("buyer_name") or "").strip()[:255],
            "company_name": str(raw.get("company_name") or "").strip()[:255],
            "country": str(raw.get("country") or "").strip()[:128],
            "product_line": str(raw.get("product_line") or "").strip()[:255],
            "chat_url": str(raw.get("chat_url") or "").strip(),
            "source_scope": str(raw.get("source_scope") or body.source_scope or "main").strip()[:16],
        }
        if row:
            for k, v in fields.items():
                if v:
                    setattr(row, k, v)
            row.updated_at = _now()
            updated += 1
        else:
            db.add(
                AlibabaPublicPoolTarget(
                    user_id=current_user.id,
                    account_id=account_id,
                    buyer_key=key,
                    status="pending",
                    touch_count=0,
                    next_touch_at=_now(),
                    raw=raw,
                    **fields,
                )
            )
            created += 1
    db.commit()
    return {"ok": True, "created": created, "updated": updated}


@router.post(
    "/api/alibaba-inquiries/accounts/{account_id}/public-pool/targets/{target_id}/plan",
    summary="生成三次触达话术",
)
def plan_pool_target(
    account_id: int,
    target_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    row = (
        db.query(AlibabaPublicPoolTarget)
        .filter(
            AlibabaPublicPoolTarget.id == target_id,
            AlibabaPublicPoolTarget.account_id == account_id,
            AlibabaPublicPoolTarget.user_id == current_user.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(404, detail="公海池目标不存在")
    config = config_to_dict(_get_or_create_config(db, current_user.id, account_id))
    plan = _pool_plan(
        {"buyer_name": row.buyer_name, "company_name": row.company_name},
        config.get("persona") or {},
    )
    row.plan = plan
    times = pool_plan_times(_now())
    if not row.next_touch_at:
        row.next_touch_at = times[0]
    db.commit()
    return {"ok": True, "plan": plan, "next_touch_at": row.next_touch_at.isoformat() if row.next_touch_at else None}


@router.post(
    "/api/alibaba-inquiries/accounts/{account_id}/public-pool/targets/{target_id}/touch",
    summary="按节奏触达（第三次后停）",
)
async def touch_pool_target(
    account_id: int,
    target_id: int,
    body: PoolTouchBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = _account_or_404(db, current_user.id, account_id)
    row = (
        db.query(AlibabaPublicPoolTarget)
        .filter(
            AlibabaPublicPoolTarget.id == target_id,
            AlibabaPublicPoolTarget.account_id == account_id,
            AlibabaPublicPoolTarget.user_id == current_user.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(404, detail="公海池目标不存在")
    if int(row.touch_count or 0) >= 3 and row.status != "replied":
        return {"ok": True, "sent": False, "message": "已连发三次，等对方回复后再动"}
    content = str(body.content or "").strip()
    if not content:
        plan = row.plan if isinstance(row.plan, list) else []
        step_index = max(0, min(len(plan) - 1, int(body.step) - 1)) if plan else 0
        content = str((plan[step_index] or {}).get("content") or "") if plan else ""
        if not content:
            raise HTTPException(400, detail="没有话术：先生成三次触达话术或手动填写内容")
    if body.dry_run:
        return {"ok": True, "sent": False, "dry_run": True, "content": content}
    if not row.chat_url:
        raise HTTPException(400, detail="该目标没有聊天链接：请在列表里补 chat_url（打开对方窗口的地址）")
    lock = await _account_lock(account_id, "reply")
    async with lock:
        page = await _get_account_page(acct, visible=True)
        await _goto(page, row.chat_url)
        send = await _send_reply_via_page(page, content)
    if not send.get("ok"):
        return {"ok": False, "sent": False, "message": str(send.get("error") or "发送失败")}
    row.touch_count = int(row.touch_count or 0) + 1
    row.last_touch_at = _now()
    row.status = "touching" if row.touch_count < 3 else "contacted"
    times = pool_plan_times(row.last_touch_at)
    row.next_touch_at = times[min(row.touch_count, len(times) - 1)]
    db.commit()
    return {
        "ok": True,
        "sent": True,
        "touch_count": row.touch_count,
        "next_touch_at": row.next_touch_at.isoformat() if row.next_touch_at else None,
    }


@router.delete(
    "/api/alibaba-inquiries/accounts/{account_id}/public-pool/targets/{target_id}",
    summary="删除公海池目标",
)
def delete_pool_target(
    account_id: int,
    target_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    row = (
        db.query(AlibabaPublicPoolTarget)
        .filter(
            AlibabaPublicPoolTarget.id == target_id,
            AlibabaPublicPoolTarget.account_id == account_id,
            AlibabaPublicPoolTarget.user_id == current_user.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(404, detail="公海池目标不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/api/alibaba-inquiries/accounts/{account_id}/public-pool/scan", summary="从公海池页面抓取（待接入选择器）")
def scan_public_pool(
    account_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    raise HTTPException(
        501,
        detail=(
            "公海池自动抓取还没接入：需要真实页面结构（主/子账号公海池列表 DOM）才能写选择器。"
            "现在可先用【导入】把手上的公海客户贴进来，按 T0/T+2d/T+5d 三次触达跑起来；"
            "或者把成都公司对话框插件的数据源接进来（同一套 targets 表）。"
        ),
    )


# ---------------------------------------------------------------- 知识库删除

@router.delete(
    "/api/alibaba-inquiries/accounts/{account_id}/training-docs/{doc_id}",
    summary="删除知识库资料",
)
def delete_training_doc(
    account_id: int,
    doc_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    row = (
        db.query(AlibabaInquiryTrainingDoc)
        .filter(
            AlibabaInquiryTrainingDoc.id == doc_id,
            AlibabaInquiryTrainingDoc.user_id == current_user.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(404, detail="资料不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}
