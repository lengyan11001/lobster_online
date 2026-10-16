"""对话附件多模态注入 + 会话级附件记忆（2026-10-16 修复“上传图片后模型说没收到图”）

背景：附件以前只以文本 URL 进 messages，指望模型自己调 image.understand；
2026-09-10 主模型切到 deepseek-flash 后不再稳定触发（调用被写成文本/干脆不调），
模型于是回答“没收到图片”。这里把图作为多模态 part 直给模型，并加会话级短期记忆。
"""
from __future__ import annotations

import pytest

from backend.app.api import chat as chat_mod
from backend.app.api import h5_chat_channel as h5_mod


@pytest.fixture(autouse=True)
def _clean_memory():
    chat_mod._SESSION_ATTACHMENT_MEMORY.clear()
    h5_mod._H5_ATTACHMENT_MEMORY.clear()
    yield
    chat_mod._SESSION_ATTACHMENT_MEMORY.clear()
    h5_mod._H5_ATTACHMENT_MEMORY.clear()


def _payload(message, urls=None, session_id=None):
    return chat_mod.ChatRequest(
        message=message,
        attachment_image_urls=list(urls or []),
        session_id=session_id,
    )


def test_image_attachment_becomes_multimodal_part():
    msg = chat_mod._build_user_message_with_attachments(
        _payload("理解一下这张图片", urls=["https://example.com/a.jpg"])
    )
    assert isinstance(msg["content"], list)
    assert msg["content"][0]["type"] == "text"
    assert msg["content"][1] == {"type": "image_url", "image_url": {"url": "https://example.com/a.jpg"}}


def test_video_and_audio_are_not_injected_as_image_parts():
    msg = chat_mod._build_user_message_with_attachments(
        _payload("看下这个", urls=["https://example.com/v.mp4", "https://example.com/a.PNG"])
    )
    types = [part["type"] for part in msg["content"]]
    assert types == ["text", "image_url"]
    assert msg["content"][1]["image_url"]["url"].endswith("a.PNG")


def test_without_attachment_the_message_stays_plain_text():
    msg = chat_mod._build_user_message_with_attachments(_payload("你好"))
    assert isinstance(msg["content"], str)


def test_inject_images_false_falls_back_to_text():
    msg = chat_mod._build_user_message_with_attachments(
        _payload("理解一下", urls=["https://example.com/a.jpg"]), inject_images=False
    )
    assert isinstance(msg["content"], str)


def test_direct_llm_prefix_wrapper_supports_multimodal_content():
    messages = [
        chat_mod._build_user_message_with_attachments(
            _payload("理解一下", urls=["https://example.com/a.jpg"])
        )
    ]
    chat_mod._wrap_last_user_for_direct_llm(messages)
    assert messages[0]["content"][0]["text"].startswith(chat_mod._DIRECT_LLM_USER_PREFIX)
    assert messages[0]["content"][1]["type"] == "image_url"


def test_session_memory_recalls_last_upload_for_followup():
    first = chat_mod._build_user_message_with_attachments(
        _payload("理解一下这张图片", urls=["https://example.com/a.jpg"], session_id="s-1")
    )
    assert isinstance(first["content"], list)

    # 下一轮没有附件，但用户仍说“这张图片” -> 用会话里最近上传的那张
    second = chat_mod._build_user_message_with_attachments(
        _payload("再仔细看下这张图片里的文字", session_id="s-1")
    )
    assert isinstance(second["content"], list)
    assert second["content"][1]["image_url"]["url"] == "https://example.com/a.jpg"
    assert "最近上传的素材" in second["content"][0]["text"]


def test_session_memory_not_used_for_unrelated_turn():
    chat_mod._build_user_message_with_attachments(
        _payload("理解一下这张图片", urls=["https://example.com/a.jpg"], session_id="s-2")
    )
    msg = chat_mod._build_user_message_with_attachments(
        _payload("帮我写一段带货文案", session_id="s-2")
    )
    assert isinstance(msg["content"], str)


def test_failed_turn_keeps_memory_for_retry():
    key = "s-retry"
    chat_mod._build_user_message_with_attachments(
        _payload("理解一下", urls=["https://example.com/a.jpg"], session_id=key)
    )
    for _ in range(3):
        recalled = chat_mod._recall_turn_attachments(
            chat_mod._turn_memory_key(_payload("这张图呢", session_id=key)), "这张图呢"
        )
        assert recalled["recalled"] is True
    chat_mod._consume_turn_memory(chat_mod._turn_memory_key(_payload("这张图呢", session_id=key)))
    again = chat_mod._recall_turn_attachments(chat_mod._turn_memory_key(_payload("这张图呢", session_id=key)), "这张图呢")
    assert again["recalled"] is True


def test_h5_user_content_parts_multimodal_and_plain():
    parts = h5_mod._h5_user_content_parts("理解一下这张图片", ["https://example.com/a.jpg"])
    assert isinstance(parts, list) and parts[1]["type"] == "image_url"
    assert h5_mod._h5_user_content_parts("你好", []) == "你好"


def test_h5_memory_recall_remember_and_consume():
    item = {"id": "msg-1", "session_id": "h5-s-1"}
    urls, aids, recalled, key = h5_mod._h5_apply_attachment_memory(
        item, "理解一下这张图片", ["https://example.com/a.jpg"], ["asset-1"]
    )
    assert urls == ["https://example.com/a.jpg"] and recalled is False

    urls2, aids2, recalled2, _ = h5_mod._h5_apply_attachment_memory(
        {"id": "msg-2", "session_id": "h5-s-1"}, "刚才那张图片里的文字是什么", [], []
    )
    assert recalled2 is True and urls2 == ["https://example.com/a.jpg"]

    # 无关消息不召回
    urls3, _, recalled3, _ = h5_mod._h5_apply_attachment_memory(
        {"id": "msg-3", "session_id": "h5-s-1"}, "帮我写个开头", [], []
    )
    assert recalled3 is False and urls3 == []

    h5_mod._h5_consume_memory_turn(key)
    assert key in h5_mod._H5_ATTACHMENT_MEMORY  # 成功一轮只消耗次数，不立刻清空
