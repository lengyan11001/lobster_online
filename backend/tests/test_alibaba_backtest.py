"""真人判定 / 信息门槛 / 背调结论化 回归。"""
from backend.app.api import alibaba_backtest as bt


def test_human_detection_accepts_real_question():
    result = bt.is_human_like_message(
        "Hi, we are a trading company in Dubai. Can you quote 500 pcs of R36U with shipping to Jebel Ali?"
    )

    assert result["human"] is True
    assert result["score"] >= 35
    assert "含提问/需求词" in result["signals"]
    assert "quantity" in result["signals"]
    assert "model" in result["signals"]


def test_human_detection_rejects_templates_and_catalog_only():
    assert bt.is_human_like_message("Dear Sir, we are interested in your products, please send")["human"] is False
    assert bt.is_human_like_message("Please send me your catalogue")["human"] is False
    assert bt.is_human_like_message("https://example.com/abc")["human"] is False
    assert bt.is_human_like_message("ok")["human"] is False
    repeated = bt.is_human_like_message("Please send me your price list", previous_texts=["Please send me your price list"])
    assert repeated["human"] is False


def test_company_or_email_alone_is_not_enough():
    name_only = bt.assess_info_sufficiency({"company_name": "ABC Trading Ltd"})
    email_only = bt.assess_info_sufficiency({"email": "john@abctrading.com"})
    both = bt.assess_info_sufficiency({"company_name": "ABC Trading Ltd", "email": "john@abctrading.com"})
    full = bt.assess_info_sufficiency({"company_name": "ABC Trading Ltd", "domain": "abctrading.com",
                                      "phone": "+971501234567"})

    assert name_only["can_enrich"] is False and name_only["level"] == "L1"
    assert email_only["can_enrich"] is False and email_only["level"] == "L1"
    assert both["can_enrich"] is True and both["level"] == "L2"
    assert full["can_enrich"] is True and full["level"] == "L3"
    assert "website/email" in name_only["missing"]
    assert "company_name" in email_only["missing"]
    assert "phone/whatsapp" in full.get("optional", []) or "phone/whatsapp" not in full["missing"]


def test_verdict_is_explicit_and_never_notifies_client():
    thin = bt.backtest_verdict(fields={"company_name": "ABC Trading Ltd"}, evidence_count=4, sources_hit=["web_search"])
    good = bt.backtest_verdict(
        fields={"company_name": "ABC Trading Ltd", "domain": "abctrading.com"},
        evidence_count=6, sources_hit=["official_website", "web_search", "tikhub_linkedin_company"],
        grade="A", score=82,
    )
    weak = bt.backtest_verdict(
        fields={"company_name": "SoluTech SA", "email": "x@solutec.es"},
        evidence_count=2, sources_hit=["web_search"], grade="C", score=41,
    )

    assert thin["verdict"] == "needs_more_info" and thin["notify"] == "none"
    assert good["verdict"] == "qualified" and good["confidence"] == "high" and good["notify"] == "business"
    assert weak["verdict"] == "watch" and weak["notify"] == "none"
    for item in (thin, good, weak):
        assert item["why"] and isinstance(item["gaps"], list)


def test_source_registry_has_room_for_new_platforms():
    keys = {item["key"] for item in bt.SOURCE_REGISTRY}

    assert {"official_website", "company_registry", "tikhub_linkedin_company"} <= keys
    assert "customs_data" in keys and "local_registry" in keys


def test_info_request_instruction_asks_for_the_missing_fields():
    thin = bt.assess_info_sufficiency({"company_name": "ABC Trading Ltd"})
    instruction = bt.build_info_request_instruction(thin, {"name": "Lena", "title": "Sales Manager", "company": "HIKONG"})

    assert "继续要信息" in instruction
    assert "官网或邮箱" in instruction
    assert "一次只问 1-2 个字段" in instruction
    assert "Lena" in instruction and "HIKONG" in instruction


def test_info_request_instruction_switches_to_closing_when_enough():
    good = bt.assess_info_sufficiency({"company_name": "ABC Trading Ltd", "domain": "abctrading.com"})
    instruction = bt.build_info_request_instruction(good, {"name": "Lena"})

    assert "信息已够" in instruction
    assert "整理一份对应方案/报价" in instruction
    assert "不要催促下单" in instruction
