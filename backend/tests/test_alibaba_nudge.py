"""已读未回 → 换角度触达（撩动）逻辑回归。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.api import alibaba_reception as reception
from backend.app.db import Base
from backend.app.models import AlibabaInquiry, AlibabaInquiryMessage, AlibabaReceptionSession

NOW = datetime(2026, 9, 21, 12, 0)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    try:
        yield db
    finally:
        db.close()


def _seed(db, inquiry_id: str, *, seller_minutes_ago: int, nudge_count: int = 0, takeover: bool = False,
          buyer_after: bool = False):
    seller_at = NOW - timedelta(minutes=seller_minutes_ago)
    db.add(AlibabaInquiry(user_id=1, account_id=1, inquiry_id=inquiry_id, buyer_name="Buyer"))
    db.add(AlibabaInquiryMessage(user_id=1, account_id=1, inquiry_id=inquiry_id, message_uid="b-" + inquiry_id,
                                 direction="buyer", content="hi", sent_at=seller_at - timedelta(minutes=5)))
    db.add(AlibabaInquiryMessage(user_id=1, account_id=1, inquiry_id=inquiry_id, message_uid="s-" + inquiry_id,
                                 direction="seller", content="hello", sent_at=seller_at))
    if buyer_after:
        db.add(AlibabaInquiryMessage(user_id=1, account_id=1, inquiry_id=inquiry_id, message_uid="b2-" + inquiry_id,
                                     direction="buyer", content="still here", sent_at=seller_at + timedelta(minutes=1)))
    db.add(AlibabaReceptionSession(
        user_id=1, account_id=1, inquiry_id=inquiry_id, stage="nudge" if nudge_count else "confirm_human",
        read_state="read_no_reply", turn_count=1, human_takeover=takeover,
        last_seller_at=seller_at,
        last_buyer_at=seller_at + timedelta(minutes=1) if buyer_after else seller_at - timedelta(minutes=5),
        meta={"nudge_count": nudge_count, "last_nudge_angle": "价值/案例" if nudge_count else ""},
    ))
    db.commit()


class _FakeSession:
    def __init__(self, count: int):
        self.meta = {"nudge_count": count}
        self.last_seller_at = NOW
        self.last_buyer_at = NOW - timedelta(hours=1)


def test_angle_rotation_is_unique_per_turn():
    config = reception.default_reception_config()

    labels = [reception.next_nudge_angle(config, index)["label"] for index in range(4)]

    assert labels == ["价值/案例", "新品/新价", "选择题", "资源"]
    assert len(set(labels)) == 4


def test_nudge_due_at_respects_intervals_and_max():
    config = reception.default_reception_config()
    row = _FakeSession(0)

    assert reception.nudge_due_at(config, row, NOW + timedelta(minutes=119)) == NOW + timedelta(minutes=120)
    assert reception.nudge_due_at(config, row, NOW + timedelta(minutes=121)) == NOW + timedelta(minutes=120)

    row.meta = {"nudge_count": 3}
    assert reception.nudge_due_at(config, row, NOW + timedelta(days=30)) is None

    config_off = dict(config, read_no_reply_enabled=False)
    row.meta = {"nudge_count": 0}
    assert reception.nudge_due_at(config_off, row, NOW + timedelta(days=1)) is None


def test_nudge_candidates_filtering(session):
    config = reception.default_reception_config()
    _seed(session, "too_early", seller_minutes_ago=30)
    _seed(session, "due", seller_minutes_ago=180)
    _seed(session, "exhausted", seller_minutes_ago=180, nudge_count=3)
    _seed(session, "takeover", seller_minutes_ago=180, takeover=True)
    _seed(session, "buyer_replied", seller_minutes_ago=180, buyer_after=True)

    pending = reception._nudge_candidates(session, 1, 1, config=config, limit=10, now=NOW)

    assert [item["inquiry"].inquiry_id for item in pending] == ["due"]
    assert pending[0]["nudge_index"] == 0


def test_second_nudge_waits_full_day(session):
    config = reception.default_reception_config()
    _seed(session, "second", seller_minutes_ago=180, nudge_count=1)

    soon = reception._nudge_candidates(session, 1, 1, config=config, limit=10, now=NOW)
    later = reception._nudge_candidates(session, 1, 1, config=config, limit=10,
                                       now=NOW + timedelta(days=1, minutes=1))

    assert soon == []
    assert [item["inquiry"].inquiry_id for item in later] == ["second"]
    assert later[0]["nudge_index"] == 1
