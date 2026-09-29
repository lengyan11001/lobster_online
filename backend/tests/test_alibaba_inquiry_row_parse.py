"""会话列表行解析（中文标签）与"待处理时间窗"回归。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.api import alibaba_inquiries as inquiries_api
from backend.app.api import alibaba_reception as reception
from backend.app.db import Base
from backend.app.models import AlibabaInquiry, AlibabaInquiryMessage

ROW_TM = ("询价单号：13867725322 更新时间： 2026-7-16   创建时间： 2026-7-15 "
          "Inquiry from TM [信息] A Alhassan Abdullahi TM 商机 Rae MA 洽谈中 查看详情")
ROW_WITH_MESSAGE = ("询价单号：13842125575 更新时间： 2026-6-11   创建时间： 2026-6-11 "
                    "Inquiry from TM my what's app 876-793-8970 M max advance TM 商机 Rae MA 洽谈中 查看详情")


def test_parse_inquiry_labels_extracts_name_status_and_dates():
    parsed = inquiries_api._parse_inquiry_labels(ROW_TM)

    assert parsed["buyer_name"] == "Alhassan Abdullahi"
    assert parsed["status"] == "洽谈中"
    assert parsed["last_message_at"] == datetime(2026, 7, 16)
    assert parsed["created_at_on_platform"] == datetime(2026, 7, 15)
    assert "preview" not in parsed           # 这行本来就没有买家留言


def test_parse_inquiry_labels_keeps_buyer_last_message_as_preview():
    parsed = inquiries_api._parse_inquiry_labels(ROW_WITH_MESSAGE)

    assert parsed["buyer_name"] == "max advance"
    assert "what's app" in parsed.get("preview", "")


def test_clean_buyer_name_rejects_labels_and_sentences():
    assert inquiries_api._clean_buyer_name("更新时间： 2026-7-16") == ""
    assert inquiries_api._clean_buyer_name("Inquiry from TM") == ""
    assert inquiries_api._clean_buyer_name("Hi there") == ""
    assert inquiries_api._clean_buyer_name("Hope Ilunga") == "Hope Ilunga"


def test_parse_list_row_no_longer_stores_label_as_buyer():
    row = {"id": "13867725322", "href": "https://message.alibaba.com/message/maDetail.htm?imInquiryId=13867725322",
           "text": ROW_TM, "lines": [ROW_TM]}
    parsed = inquiries_api._parse_list_row(row)

    assert parsed["buyer_name"] == "Alhassan Abdullahi"
    assert parsed["status"] == "洽谈中"
    assert "更新时间" not in (parsed["title"] or "")
    assert parsed["last_message_at"] == datetime(2026, 7, 16)
    assert parsed["created_at_on_platform"] == datetime(2026, 7, 15)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    try:
        yield db
    finally:
        db.close()


def _seed(db, inquiry_id: str, buyer_days_ago: int, *, replied: bool):
    db.add(AlibabaInquiry(user_id=1, account_id=1, inquiry_id=inquiry_id, buyer_name="Buyer " + inquiry_id,
                          last_message_at=datetime.utcnow() - timedelta(days=buyer_days_ago)))
    buyer_at = datetime.utcnow() - timedelta(days=buyer_days_ago)
    if replied:
        db.add(AlibabaInquiryMessage(user_id=1, account_id=1, inquiry_id=inquiry_id, message_uid="b-" + inquiry_id,
                                     direction="buyer", content="hi", sent_at=buyer_at))
        db.add(AlibabaInquiryMessage(user_id=1, account_id=1, inquiry_id=inquiry_id, message_uid="s-" + inquiry_id,
                                     direction="seller", content="hello", sent_at=buyer_at - timedelta(hours=1)))
    else:
        db.add(AlibabaInquiryMessage(user_id=1, account_id=1, inquiry_id=inquiry_id, message_uid="b-" + inquiry_id,
                                     direction="buyer", content="hi", sent_at=buyer_at))
    db.commit()


def test_pending_window_filters_old_history(session):
    _seed(session, "fresh", 2, replied=True)
    _seed(session, "old", 90, replied=True)

    all_pending = reception._pending_inquiries(session, 1, 1, limit=50, window_days=0)
    windowed = reception._pending_inquiries(session, 1, 1, limit=50, window_days=30)
    counts = reception.pending_counts(session, 1, 1, window_days=30)

    assert {row.inquiry_id for row in all_pending} == {"fresh", "old"}
    assert [row.inquiry_id for row in windowed] == ["fresh"]
    assert counts == {"in_window": 1, "historical": 1, "total": 2}


def test_reception_config_window_default_and_roundtrip(session):
    assert reception.default_reception_config()["pending_window_days"] == 30

    row = reception._get_or_create_config(session, 1, 1)
    assert reception.config_to_dict(row)["pending_window_days"] == 30

    row.meta = {"pending_window_days": 7}
    session.commit()
    assert reception.config_to_dict(row)["pending_window_days"] == 7
