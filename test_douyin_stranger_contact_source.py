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


def test_memory_takeover_mode_survives_the_merge():
    """记忆接管节点不能再被 Online 里的固定文案顶掉（2026-10-03 用户 221 演示复现）。

    H5 节点下发 {"reply_mode": "ai_memory", "memory_takeover": true}，Online 私信引流
    里另存着一份固定文案。旧代码在合并参数时只放行 ai_lead，把 ai_memory 降级成
    fixed，于是节点选的是「AI 记忆接管」，实际发的却是那份固定话术。
    """
    merged = h5_chat_channel._merge_scheduled_douyin_stranger_params(
        {"reply_mode": "fixed", "message": "亲，您一直发这段我没法判断您的需求呀。"},
        {"reply_mode": "ai_memory", "wechat_add_friend_enabled": False},
    )

    assert merged["reply_mode"] == "ai_memory", merged
    # 固定文案留着无妨（记忆接管分支不用它），但模式必须是节点选的记忆接管。
    assert merged["message"] == "亲，您一直发这段我没法判断您的需求呀。"


def test_fixed_and_ai_lead_modes_still_pass_through():
    for mode in ("fixed", "ai_lead"):
        merged = h5_chat_channel._merge_scheduled_douyin_stranger_params(
            {"reply_mode": "fixed", "message": "固定文案"},
            {"reply_mode": mode},
        )
        assert merged["reply_mode"] == mode, merged


def test_unknown_reply_mode_falls_back_to_fixed():
    merged = h5_chat_channel._merge_scheduled_douyin_stranger_params(
        {"reply_mode": "ai_lead"},
        {"reply_mode": "ai_auto"},
    )

    assert merged["reply_mode"] == "fixed", merged


def test_memory_doc_ids_travel_with_the_task():
    """Online 私信引流里选的记忆文件要跟着一次性任务下发。"""
    params = h5_chat_channel._scheduled_douyin_online_config_params(
        "stranger_message",
        config={"douyin_default_account_id": 1},
        plans=[],
        search_sessions=[],
        stranger_monitors=[
            {
                "account_id": 1,
                "enabled": True,
                "reply_mode": "ai_memory",
                "reply_message": "固定文案",
                "memory_doc_ids": ["doc-a", "  ", "doc-b"],
            }
        ],
        self_comment_monitors=[],
    )
    # 空白项在拼参数时就丢掉，只保留真正选中的记忆文件。
    assert params["memory_doc_ids"] == ["doc-a", "doc-b"]

    merged = h5_chat_channel._merge_scheduled_douyin_stranger_params(params, {"reply_mode": "ai_memory"})
    assert merged["reply_mode"] == "ai_memory"
    assert merged["memory_doc_ids"] == ["doc-a", "doc-b"]
