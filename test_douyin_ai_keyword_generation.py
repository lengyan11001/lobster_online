"""AI 关键词生成：JSON 回包不能被 40 字上限截断，兜底必须守住节点设置的数量。

2026-09-21 线上：节点设 ai_keyword_count=3，但每轮都搜 9 个关键词。
根因是 request_douyin_ai_comment 用评论文案的 40 字上限截断关键词 JSON，
json.loads 失败后静默回退到 Online 的原关键词列表（9 个）。
"""

import asyncio

import backend.app.api.h5_chat_channel as h5_chat_channel

h5_chat_channel._install_douyin_origin_import_path()
import douyin_api  # type: ignore  # noqa: E402

from backend.app.api.h5_chat_channel import _scheduled_douyin_ai_keyword_note  # noqa: E402

# 节点要求"每个关键词 4-12 个字、最多 3 个"，模型给的 JSON 必然超过 40 字。
MODEL_REPLY = '{"keywords": ["普通人做AI短视频", "AI获客教程", "AI直播带货"]}'
AI_WORDS = ["普通人做AI短视频", "AI获客教程", "AI直播带货"]
NODE_KEYWORDS = [
    "AI短视频",
    "AI",
    "AI直播",
    "AI赚钱",
    "AI数字人",
    "AI获客",
    "AI智能体",
    "workbuddy",
    "AI龙虾",
]


class _StubClient:
    api_key = "stub-key"
    api_url = "https://ai.example/v1/chat/completions"
    model = "gpt-5.4"
    headers = {"Authorization": "Bearer stub-key"}


class _StubResponse:
    status_code = 200

    def __init__(self, content):
        self.text = content
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _patch_ai_http(monkeypatch, reply):
    monkeypatch.setattr(douyin_api, "create_ai_client", lambda *args, **kwargs: _StubClient())
    monkeypatch.setattr(douyin_api.requests, "post", lambda *args, **kwargs: _StubResponse(reply))


def test_comment_reply_is_still_capped_at_40_chars(monkeypatch):
    """默认行为不变：评论文案依旧只取 40 字。"""
    _patch_ai_http(monkeypatch, MODEL_REPLY)

    assert len(MODEL_REPLY) > 40
    assert douyin_api.request_douyin_ai_comment("sys", "user") == MODEL_REPLY[:40].rstrip("，。！？,.!? ")


def test_keyword_json_survives_with_response_limit(monkeypatch):
    _patch_ai_http(monkeypatch, MODEL_REPLY)

    raw = douyin_api.request_douyin_ai_comment("sys", "user", response_limit=400)

    assert raw == MODEL_REPLY
    assert h5_chat_channel._parse_douyin_ai_keywords(raw, limit=3) == AI_WORDS


def test_generate_ai_keywords_parses_reply_longer_than_40_chars(monkeypatch):
    calls = {}

    def fake_request(system_prompt, user_prompt, max_tokens=180, response_limit=40):
        calls["response_limit"] = response_limit
        return MODEL_REPLY

    monkeypatch.setattr(douyin_api, "request_douyin_ai_comment", fake_request)

    words = h5_chat_channel._generate_douyin_ai_keywords(
        library=["AI获客"],
        memory_texts=[],
        persona_text="",
        recent_keywords=[],
        count=3,
        publish_window="7",
    )

    assert words == AI_WORDS
    assert calls["response_limit"] > 40
    # 回归护栏：修复前那种 40 字截断会让解析结果为空，就是这次的线上故障。
    assert h5_chat_channel._parse_douyin_ai_keywords(MODEL_REPLY[:40], limit=3) == []


def test_generate_ai_keywords_drops_words_used_in_last_days(monkeypatch):
    monkeypatch.setattr(
        douyin_api,
        "request_douyin_ai_comment",
        lambda system_prompt, user_prompt, max_tokens=180, response_limit=40: MODEL_REPLY,
    )

    words = h5_chat_channel._generate_douyin_ai_keywords(
        library=[],
        memory_texts=[],
        persona_text="",
        recent_keywords=AI_WORDS,
        count=3,
        publish_window="7",
    )

    assert words == []


def _apply_ai_keywords(monkeypatch, ai_keywords):
    monkeypatch.setattr(
        h5_chat_channel,
        "_generate_douyin_ai_keywords",
        lambda **kwargs: list(ai_keywords),
    )
    monkeypatch.setattr(h5_chat_channel, "_record_douyin_ai_keywords", lambda rows: None)
    monkeypatch.setattr(h5_chat_channel, "_douyin_ai_last_keywords", lambda: [])
    params = {"keywords": list(NODE_KEYWORDS), "keyword": NODE_KEYWORDS[0], "max_results": 50}
    return asyncio.run(
        h5_chat_channel._apply_scheduled_douyin_ai_keywords(params, {"ai_keyword_count": 3}, {})
    )


def test_ai_keyword_fallback_is_capped_to_node_count(monkeypatch):
    merged = _apply_ai_keywords(monkeypatch, [])

    assert merged["keywords"] == NODE_KEYWORDS[:3]
    assert merged["keyword"] == NODE_KEYWORDS[0]
    assert merged["ai_keywords_used"] is False
    assert "沿用原关键词" in merged["ai_keyword_fallback"]

    note = _scheduled_douyin_ai_keyword_note(merged)
    assert "AI 关键词未生成" in note
    assert "3 个已有关键词" in note


def test_ai_keyword_success_uses_ai_words_and_clears_fallback(monkeypatch):
    merged = _apply_ai_keywords(monkeypatch, AI_WORDS)

    assert merged["keywords"] == AI_WORDS
    assert merged["ai_keywords_used"] is True
    assert "ai_keyword_fallback" not in merged
    assert _scheduled_douyin_ai_keyword_note(merged) == ""


def test_end_to_end_node_count_is_respected_with_real_http_layer(monkeypatch):
    """走真实 request_douyin_ai_comment / _generate_douyin_ai_keywords，只看 HTTP 层打桩。"""
    _patch_ai_http(monkeypatch, MODEL_REPLY)
    monkeypatch.setattr(h5_chat_channel, "_record_douyin_ai_keywords", lambda rows: None)

    params = {"keywords": list(NODE_KEYWORDS), "keyword": NODE_KEYWORDS[0], "max_results": 50}
    merged = asyncio.run(
        h5_chat_channel._apply_scheduled_douyin_ai_keywords(params, {"ai_keyword_count": 3}, {})
    )

    assert merged["keywords"] == AI_WORDS
    assert len(merged["keywords"]) == 3
    assert merged["ai_keywords_used"] is True
    assert "ai_keyword_fallback" not in merged
    assert _scheduled_douyin_ai_keyword_note(merged) == ""
