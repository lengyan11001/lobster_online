from __future__ import annotations

import asyncio

from backend.app.api import h5_chat_channel, openclaw_memory
from backend.app.services import native_wechat_engine as engine


def test_auto_reply_memory_context_uses_general_personal_memory(monkeypatch):
    docs = [
        {"id": "profile", "title": "个人IP人设", "content": "人设内容", "status": "active"},
        {"id": "company", "title": "公司与服务介绍", "content": "公司内容", "status": "active"},
        {"id": "faq", "title": "产品百问百答 FAQ", "content": "问答内容", "status": "active"},
        {"id": "disabled", "title": "旧产品资料", "content": "旧内容", "status": "disabled"},
    ]
    monkeypatch.setattr(openclaw_memory, "_load_index", lambda _user_id: docs)
    monkeypatch.setattr(
        openclaw_memory,
        "_read_canonical_memory_content",
        lambda doc, max_chars=1800: str(doc.get("content") or "")[:max_chars],
    )

    memory = engine._load_auto_reply_memory_context(31)

    assert memory["document_count"] == 3
    assert memory["titles"][0] == "产品百问百答 FAQ"
    assert "问答内容" in memory["text"]
    assert "公司内容" in memory["text"]
    assert "人设内容" in memory["text"]
    assert "旧内容" not in memory["text"]


def test_auto_reply_llm_uses_memory_and_replies_to_valid_text(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        content = b"json"
        text = ""

        @staticmethod
        def json():
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"should_reply":false,"category":"price","intent_level":"high",'
                                '"topic":"企业报价","conversation_summary":"询问企业版价格",'
                                '"reply":"企业版价格需要结合人数确认，我先帮您核实一下。"}'
                            )
                        }
                    }
                ]
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, json, headers):
            captured.update({"url": url, "json": json, "headers": headers})
            return FakeResponse()

    monkeypatch.setattr(engine.httpx, "AsyncClient", FakeAsyncClient)
    result = asyncio.run(
        engine._call_auto_reply_llm(
            auth_context={"token": "token", "installation_id": "iid"},
            user_id=31,
            peer_name="客户A",
            latest_message="企业版多少钱？",
            recent_context="对方: 企业版多少钱？",
            memory_context="企业版根据人数和服务范围报价，不得虚构固定价格。",
        )
    )

    prompt = captured["json"]["messages"][1]["content"]
    assert "不得虚构固定价格" in prompt
    assert result["should_reply"] is True
    assert result["category"] == "price"
    assert result["intent_level"] == "high"
    assert result["reply"].startswith("企业版价格")


def test_auto_reply_llm_returns_semantic_group_invite_decision(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        content = b"json"
        text = ""

        @staticmethod
        def json():
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"should_reply":true,"category":"cooperation","intent_level":"high",'
                                '"topic":"预约体验","conversation_summary":"客户希望预约线下体验",'
                                '"reply":"可以，我先帮您安排。","should_invite_group":true,'
                                '"matched_group_keywords":["预约体验"],"group_invite_reason":"客户明确提出预约"}'
                            )
                        }
                    }
                ]
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, json, headers):
            captured["prompt"] = json["messages"][1]["content"]
            captured["system_prompt"] = json["messages"][0]["content"]
            return FakeResponse()

    monkeypatch.setattr(engine.httpx, "AsyncClient", FakeAsyncClient)
    result = asyncio.run(
        engine._call_auto_reply_llm(
            auth_context={"token": "token", "installation_id": "iid"},
            user_id=31,
            peer_name="客户A",
            latest_message="我想预约明天下午体验",
            recent_context="对方: 你们在哪里？\n我: 在深圳南山",
            memory_context="体验预约需要确认日期和到店人数。",
            group_invite_rule_context="客户明确提出预约体验并确认时间时，可以邀请进入服务群。",
        )
    )

    assert "最近聊天记录" in captured["prompt"]
    assert "体验预约需要确认日期" in captured["prompt"]
    assert "客户明确提出预约体验" in captured["prompt"]
    assert "客户回复可以、好的、同意" in captured["system_prompt"]
    assert result["should_invite_group"] is True
    assert result["matched_group_keywords"] == ["预约体验"]


def test_auto_reply_llm_never_invites_without_configured_keywords(monkeypatch):
    class FakeResponse:
        status_code = 200
        content = b"json"
        text = ""

        @staticmethod
        def json():
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"should_reply":true,"category":"other","intent_level":"none",'
                                '"reply":"好的。","should_invite_group":true,'
                                '"matched_group_keywords":["误判"],"group_invite_reason":"误判"}'
                            )
                        }
                    }
                ]
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, json, headers):
            return FakeResponse()

    monkeypatch.setattr(engine.httpx, "AsyncClient", FakeAsyncClient)
    result = asyncio.run(
        engine._call_auto_reply_llm(
            auth_context={"token": "token", "installation_id": "iid"},
            user_id=31,
            peer_name="客户A",
            latest_message="你好",
            recent_context="",
            memory_context="",
        )
    )

    assert result["should_invite_group"] is False


def test_auto_reply_report_contains_received_and_reply_details():
    result = {
        "started_at": "2026-07-29T09:00:00",
        "finished_at": "2026-07-29T09:03:00",
        "duration_seconds": 180,
        "session_count": 18,
        "unread_private_count": 2,
        "unread_message_count": 5,
        "replied": 1,
        "skipped": 0,
        "failed": 1,
        "skipped_groups": 3,
        "items": [
            {
                "peer_id": "peer-a",
                "display_name": "客户A",
                "status": "sent",
                "category": "price",
                "intent_level": "high",
                "topic": "企业报价",
                "conversation_summary": "客户询问企业版价格",
                "inbound_preview": "企业版多少钱？",
                "reply_preview": "需要结合人数确认，我先帮您核实。",
            },
            {
                "peer_id": "peer-b",
                "display_name": "客户B",
                "status": "failed",
                "category": "service",
                "intent_level": "medium",
                "topic": "售后流程",
                "inbound_preview": "售后怎么联系？",
                "error": "发送失败",
            },
        ],
    }
    memory = {"document_count": 2, "titles": ["产品百问百答", "公司介绍"]}

    report = engine._build_auto_reply_report(result, memory)
    text = report["summary_text"]

    assert "扫描会话：18 个" in text
    assert "未读消息：5 条" in text
    assert "已自动回复：1 个会话" in text
    assert "高意向会话：1 个" in text
    assert "诉求：客户询问企业版价格" in text
    assert "收到：企业版多少钱？" in text
    assert "回复：需要结合人数确认" in text
    assert "原因：发送失败" in text
    assert report["category_counts"] == {"price": 1, "service": 1}

    workflow_text = h5_chat_channel._client_workflow_result_text(
        "native_wechat_poll",
        {"summary_text": text, "replied": 1},
    )
    assert workflow_text == text
