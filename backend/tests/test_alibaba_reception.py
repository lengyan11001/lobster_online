"""阿里 AI 接待（排期 / 红线 / 公海池）纯逻辑回归。"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.api import alibaba_reception as reception
from backend.app.db import Base
from backend.app.models import (
    AlibabaCustomerArchive,
    AlibabaInquiryAccount,
    AlibabaInquiryTrainingDoc,
)


def test_default_config_has_sane_red_lines():
    config = reception.default_reception_config()

    assert config["enabled"] is False          # 默认不真发
    assert config["dry_run"] is True           # 先演练
    assert config["interval_minutes"] == 30    # 阿里考核 1 小时回复率
    assert config["online_interval_seconds"] == 60
    assert config["hot_interval_seconds"] == 30
    assert config["max_turns"] == 8
    assert config["max_chars"] <= 400          # 回复不能长篇大论
    assert config["delay_min_seconds"] < config["delay_max_seconds"]
    assert isinstance(config["handoff_triggers"], list) and config["handoff_triggers"]
    assert isinstance(config["persona"], dict)


def test_work_window_supports_normal_and_cross_midnight():
    assert reception.in_work_window(datetime(2026, 9, 20, 9, 0), "08:00", "23:00") is True
    assert reception.in_work_window(datetime(2026, 9, 20, 23, 30), "08:00", "23:00") is False
    assert reception.in_work_window(datetime(2026, 9, 20, 2, 0), "22:00", "06:00") is True
    assert reception.in_work_window(datetime(2026, 9, 20, 12, 0), "22:00", "06:00") is False


def test_next_scan_seconds_prefers_hot_then_online_then_normal():
    config = reception.default_reception_config()

    assert reception.next_scan_seconds(config) == 1800
    assert reception.next_scan_seconds(config, online_state="online") == 60
    assert reception.next_scan_seconds(config, online_state="hot") == 30


def test_clamp_reply_text_keeps_sentence_boundary():
    sentence = "Hi John, thanks for the details. " * 20
    clamped = reception.clamp_reply_text(sentence, 120)

    assert len(clamped) <= 120
    assert clamped.endswith(".")
    assert reception.clamp_reply_text("short", 120) == "short"


def test_banned_words_and_handoff_triggers_detection():
    ban = reception.default_reception_config()["banned_words"]
    triggers = reception.default_reception_config()["handoff_triggers"]

    assert reception.find_banned_words("This is our cheapest offer", ban) == ["cheapest"]
    assert reception.find_banned_words("quality product", ban) == []
    assert "人工" in reception.find_handoff_triggers("I want to talk to a human manager", triggers) or \
        "human" in reception.find_handoff_triggers("I want to talk to a human manager", triggers)
    assert reception.find_handoff_triggers("nice product", triggers) == []


def test_pool_plan_touch_offsets_are_t0_t2_t5():
    anchor = datetime(2026, 9, 20, 10, 0)
    times = reception.pool_plan_times(anchor)

    assert times == [anchor, anchor + timedelta(days=2), anchor + timedelta(days=5)]
    plan = reception._pool_plan({"buyer_name": "Ahmed"}, {"name": "Lena", "title": "Sales", "company": "HIKONG"})
    assert [step["step"] for step in plan] == ["T0", "T+2d", "T+5d"]
    assert all("Ahmed" in step["content"] for step in plan)


def test_reply_delay_stays_inside_configured_range():
    config = reception.default_reception_config()
    for _ in range(20):
        delay = reception.reply_delay_seconds(config)
        assert config["delay_min_seconds"] <= delay <= config["delay_max_seconds"]


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    try:
        yield db
    finally:
        db.close()


def test_dashboard_counts_match_their_lists(session):
    """角标口径必须和对应列表一致：客户档案=archive 行数，知识库=training-doc 行数。"""
    session.add(AlibabaInquiryAccount(id=1, user_id=1, nickname="acct", status="active"))
    session.add(AlibabaInquiryTrainingDoc(user_id=1, account_id=1, title="2026 报价表", kind="price"))
    for index in range(3):
        session.add(AlibabaCustomerArchive(
            user_id=1, account_id=1, inquiry_id="inq-%d" % index, archive_key="key-%d" % index,
            display_name="客户 %d" % index, status="pending",
        ))
    session.commit()

    payload = reception.reception_dashboard(
        account_id=1,
        current_user=SimpleNamespace(id=1),
        db=session,
    )

    assert payload["stats"]["customer_archives"] == 3
    assert payload["stats"]["kb_docs"] == 1
    assert payload["stats"]["inquiries"] == 0
