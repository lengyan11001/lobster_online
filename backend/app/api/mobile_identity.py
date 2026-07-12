from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.orm import Session

from ..models import User

PHONE_EMAIL_SUFFIX = "@sms.lobster.local"
_CN_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")


def phone_email(mobile: str) -> str:
    return f"{(mobile or '').strip()}{PHONE_EMAIL_SUFFIX}"


def phone_from_user_email(email: str) -> str:
    value = (email or "").strip().lower()
    if not value.endswith(PHONE_EMAIL_SUFFIX):
        return ""
    raw = value[: -len(PHONE_EMAIL_SUFFIX)]
    return raw if _CN_MOBILE_RE.match(raw) else ""


def is_phone_account(user: Optional[User]) -> bool:
    return bool(user and phone_from_user_email(user.email or ""))


def phone_account_user(db: Session, mobile: str) -> Optional[User]:
    mobile = (mobile or "").strip()
    if not mobile:
        return None
    return db.query(User).filter(User.email == phone_email(mobile)).first()


def online_user_for_mobile_user(db: Session, current_user: User) -> User:
    """
    Client build compatibility shim.

    The online client does not carry the server-side mobile binding table, so
    here we gracefully fall back to the current user while still preserving the
    phone-account helpers used by shared routes.
    """
    if is_phone_account(current_user):
        return current_user
    return current_user


def online_user_for_mobile_binding(db: Session, current_user: User, binding: Optional[object]) -> User:
    return online_user_for_mobile_user(db, current_user)
