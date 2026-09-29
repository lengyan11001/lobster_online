"""抖音私信接管 AI 线索模式：加好友来源选「从抖音私信提取手机号」时不该强制 contact_value。

线上 09-16~09-20 连续 15 轮失败：节点下发
{"reply_mode":"ai_lead","wechat_add_friend_enabled":true,
 "wechat_add_friend_targets_source":"douyin_private_message_phone"}
没有 contact_value，客户端却硬要求 → 直接 400。
"""

import asyncio

import backend.app.api.h5_chat_channel as h5_chat_channel

h5_chat_channel._install_douyin_origin_import_path()
import douyin_api  # type: ignore  # noqa: E402


def _run_once(monkeypatch, **kwargs):
    monkeypatch.setattr(douyin_api, "load_global_config", lambda: {"douyin_accounts": []})
    return asyncio.run(douyin_api.run_douyin_h5_stranger_message_task_once(**kwargs))


def test_contact_from_private_message_is_accepted_without_contact_value(monkeypatch):
    result = _run_once(
        monkeypatch,
        reply_mode="ai_lead",
        auto_reply_enabled=True,
        wechat_add_friend_enabled=True,
        wechat_add_friend_targets_source="douyin_private_message_phone",
        contact_value="",
    )

    # 通过了联系方式校验，继续往下走到"没有在线账号"（测试机不登录账号）
    assert result.get("status") == "skipped", result
    assert result.get("reason") == "no_online_account", result
    assert "contact" not in str(result.get("message") or "").lower()


def test_ai_lead_without_contact_and_without_source_still_blocks(monkeypatch):
    result = _run_once(
        monkeypatch,
        reply_mode="ai_lead",
        auto_reply_enabled=True,
        wechat_add_friend_enabled=True,
        wechat_add_friend_targets_source="",
        contact_value="",
    )

    assert result.get("code") == 400, result
    assert "联系方式" in str(result.get("message") or ""), result
    assert "English" not in str(result.get("message") or "")


def test_contact_value_still_wins_when_provided(monkeypatch):
    result = _run_once(
        monkeypatch,
        reply_mode="ai_lead",
        auto_reply_enabled=True,
        contact_value="wx_demo_001",
    )

    assert result.get("status") == "skipped", result


def test_stranger_param_merge_keeps_targets_source():
    merged = h5_chat_channel._merge_scheduled_douyin_stranger_params(
        {"contact_value": ""},
        {
            "reply_mode": "ai_lead",
            "wechat_add_friend_enabled": True,
            "wechat_add_friend_targets_source": "douyin_private_message_phone",
        },
    )

    assert merged["wechat_add_friend_targets_source"] == "douyin_private_message_phone"
    assert merged["reply_mode"] == "ai_lead"
