"""积分流水：每次余额变动写入 credit_ledger，便于对账（预扣/结算/退款/充值等）。"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional, Union

from sqlalchemy.orm import Session

from ..models import CreditLedger
from .credits_amount import quantize_credits, quantize_credits_signed

logger = logging.getLogger(__name__)

# 仅入库、供运营对账；用户侧接口应剥掉后再返回
_LEDGER_RECON_KEY = "_recon"
_PUBLIC_LEDGER_META_HIDDEN_KEYS = {_LEDGER_RECON_KEY, "upstream", "sutui_pool", "sutui_token_ref"}


def public_ledger_meta(meta: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """从流水 meta 中移除站内对账字段，避免通过 API 暴露给用户。"""
    if not meta:
        return meta
    out = {k: v for k, v in meta.items() if k not in _PUBLIC_LEDGER_META_HIDDEN_KEYS}
    return out or None


def append_credit_ledger(
    db: Session,
    user_id: int,
    delta: Union[int, float, Decimal, str],
    entry_type: str,
    balance_after: Union[int, float, Decimal, str],
    *,
    description: str = "",
    ref_type: Optional[str] = None,
    ref_id: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
) -> CreditLedger:
    """delta：正数为入账，负数为出账；balance_after 为变动后 users.credits 快照（最多 4 位小数，非负）。"""
    d = quantize_credits_signed(delta)
    bal = quantize_credits(balance_after)
    row = CreditLedger(
        user_id=user_id,
        delta=d,
        balance_after=bal,
        entry_type=(entry_type or "unknown")[:32],
        description=(description or "")[:512] or None,
        ref_type=(ref_type or "")[:32] or None,
        ref_id=(ref_id or "")[:128] or None,
        meta=meta,
    )
    db.add(row)
    try:
        from .sutui_api_audit import maybe_log_credit_ledger_append

        maybe_log_credit_ledger_append(
            user_id=user_id,
            entry_type=row.entry_type or "",
            delta=d,
            balance_after=bal,
            ref_type=ref_type,
            ref_id=ref_id,
            description=description or "",
            meta=meta,
        )
    except Exception:
        pass
    return row
