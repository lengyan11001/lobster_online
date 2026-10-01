import asyncio
import sys
from datetime import datetime
from pathlib import Path


DOUYIN_ORIGIN_ROOT = Path(__file__).resolve().parent / "backend" / "douyin_origin"
sys.path.insert(0, str(DOUYIN_ORIGIN_ROOT))

import douyin_api  # noqa: E402
from douyin_comment_scraper import conversation_time_is_older_than_24h  # noqa: E402
from douyin_api import (  # noqa: E402
    extract_douyin_mainland_mobile_numbers,
    generate_douyin_stranger_reply_message,
    normalize_douyin_stranger_message_monitor_state,
)


def test_empty_legacy_fixed_reply_migrates_to_ai_auto():
    state = normalize_douyin_stranger_message_monitor_state(
        {
            "reply_mode": "fixed",
            "reply_message": "",
            "wechat_add_friend_enabled": True,
        },
        account_id=7,
    )

    assert state["reply_mode"] == "ai_auto"
    assert state["wechat_add_friend_enabled"] is True


def test_extract_phone_numbers_from_new_message_text_only_once():
    numbers = extract_douyin_mainland_mobile_numbers(
        [
            {"incoming_message": "phone 139 2748 5337 or 139-2748-5337"},
            {"content": "another 18823851682, invalid 12812345678"},
        ]
    )

    assert numbers == ["13927485337", "18823851682"]


def test_extract_phone_numbers_ignores_outgoing_messages_and_stored_numbers():
    numbers = extract_douyin_mainland_mobile_numbers(
        [
            {
                "conversation_key": "same-contact",
                "last_message_is_user": True,
                "phone_numbers": ["18823851682", "13927485337"],
                "messages": [
                    {"direction": "outgoing", "text": "18823851682"},
                    {"direction": "incoming", "text": "13927485337"},
                ],
            }
        ]
    )

    assert numbers == ["13927485337"]


def test_ai_lead_reply_uses_incoming_context_and_appends_contact(monkeypatch):
    captured = {}

    def fake_ai(system_prompt, user_prompt, **kwargs):
        captured["user_prompt"] = user_prompt
        return "先回应一下\n稍等我整理"

    monkeypatch.setattr(douyin_api, "request_douyin_ai_comment", fake_ai)

    result = generate_douyin_stranger_reply_message(
        {
            "username": "客户A",
            "incoming_message": "想了解方案",
            "time_text": "刚刚",
            "unread_count": 1,
        },
        mode="ai_lead",
        prompt_text="先回答问题，再自然引导继续沟通",
        contact_value="wx_demo_001",
    )

    assert result.splitlines() == ["先回应一下", "稍等我整理", "麻烦您绿泡泡", "wx_demo_001"]
    assert "想了解方案" in captured["user_prompt"]
    assert "先回答问题，再自然引导继续沟通" in captured["user_prompt"]


def test_conversation_time_cutoff_stops_at_any_yesterday_row():
    now = datetime(2026, 8, 19, 2, 0, 0)

    # Yesterday 23:00 is only three hours ago, but it is still the list
    # boundary represented by Douyin's display timestamp.
    assert conversation_time_is_older_than_24h("昨天 23:00", now=now) is True
    assert conversation_time_is_older_than_24h("昨天", now=now) is True
    assert conversation_time_is_older_than_24h("今天 01:00", now=now) is False


def test_monitor_start_accepts_empty_legacy_fixed_reply(monkeypatch):
    account = {"id": 5, "status": "online"}

    monkeypatch.setattr(douyin_api, "load_global_config", lambda: {"douyin_accounts": [account]})
    monkeypatch.setattr(douyin_api, "get_douyin_account_by_id", lambda account_id, config: account)
    monkeypatch.setattr(douyin_api, "save_douyin_stranger_message_monitor_config", lambda: None)

    async def ensure_scheduler():
        return None

    monkeypatch.setattr(douyin_api, "ensure_douyin_stranger_message_monitor_scheduler", ensure_scheduler)
    try:
        result = asyncio.run(
            douyin_api.douyin_start_stranger_message_monitor(
                request={
                    "account_id": 5,
                    "auto_reply_enabled": True,
                    "reply_mode": "fixed",
                    "message": "",
                    "wechat_add_friend_enabled": True,
                }
            )
        )
    finally:
        douyin_api.douyin_stranger_message_monitor_states.pop("5", None)

    assert result["code"] == 200
    assert result["monitor"]["reply_mode"] == "ai_auto"
    assert result["monitor"]["wechat_add_friend_enabled"] is True


def test_current_stranger_leads_page_has_one_non_blocking_monitor_start():
    root = Path(__file__).resolve().parent
    script = (root / "static" / "douyin-origin" / "douyin-workbench-shared.js").read_text(encoding="utf-8")
    html = (root / "static" / "douyin-origin" / "douyin-stranger-leads.html").read_text(encoding="utf-8")

    assert script.count("function startDouyinStrangerMessageMonitor()") == 1
    assert "开启监控自动回复前，请先填写固定引流文案" not in script
    assert "wechat_add_friend_enabled:wechatAddFriendEnabled" in script
    assert '<option value="ai_auto" selected>' in html
    assert "20260816-douyin-takeover-v2" in html


def test_h5_stranger_task_is_one_shot_and_adds_friends_once(monkeypatch):
    account = {"id": 5, "status": "online", "port": 9336}
    rows = [
        {
            "conversation_key": "with-phone",
            "username": "有手机号",
            "last_message_text": "我的电话是13927485337",
            "last_message_is_user": True,
            "messages": [{"text": "我的电话是13927485337", "is_incoming": True}],
        },
        {
            "conversation_key": "without-phone",
            "username": "没有手机号",
            "last_message_text": "想了解一下",
            "last_message_is_user": True,
            "messages": [{"text": "想了解一下", "is_incoming": True}],
        },
        {
            "conversation_key": "last-is-self",
            "username": "最后是自己",
            "last_message_text": "已发送资料",
            "last_message_is_user": False,
            "messages": [{"text": "已发送资料", "is_incoming": False}],
        },
    ]
    stored_rows = []
    sent_rows = []
    add_friend_calls = []
    call_order = []

    class FakeScraper:
        async def collect_stranger_private_messages(self, **kwargs):
            call_order.append("stranger")
            assert kwargs["max_conversations"] >= 10000
            assert kwargs["include_details"] is True
            return [
                await kwargs["item_callback"](dict(rows[0]), object()),
                await kwargs["item_callback"](dict(rows[2]), object()),
            ]

        async def collect_chat_page_private_messages(self, **kwargs):
            call_order.append("normal")
            assert kwargs["max_conversations"] >= 10000
            return [await kwargs["item_callback"](dict(rows[1]), object())]

        async def send_open_chat_message(self, page, message, **kwargs):
            sent_rows.append({"username": kwargs.get("username"), "message": message})
            return {"success": True}

        async def close(self):
            return None

    def store(account_id, incoming):
        by_key = {
            douyin_api.stranger_message_row_key(row): dict(row)
            for row in stored_rows
            if douyin_api.stranger_message_row_key(row)
        }
        order = list(by_key)
        for row in incoming:
            normalized = douyin_api.normalize_douyin_stranger_message_row({**row, "account_id": account_id})
            key = douyin_api.stranger_message_row_key(normalized)
            if key not in order:
                order.append(key)
            by_key[key] = {**by_key.get(key, {}), **normalized}
        stored_rows[:] = [by_key[key] for key in order]
        return len(stored_rows)

    async def fake_add_friend(numbers):
        add_friend_calls.append(list(numbers))
        return {"enabled": True, "queued": True, "targets": list(numbers), "task_id": "friend-task-1"}

    monkeypatch.setattr(douyin_api, "load_global_config", lambda: {"douyin_accounts": [account]})
    monkeypatch.setattr(douyin_api, "get_douyin_account_by_id", lambda account_id, config: account)
    monkeypatch.setattr(douyin_api, "is_douyin_stranger_message_monitor_busy", lambda account_id: (False, ""))
    monkeypatch.setattr(douyin_api, "create_douyin_message_scraper", lambda account, config: FakeScraper())
    monkeypatch.setattr(douyin_api, "collect_douyin_stranger_message_results", lambda account_id=0: list(stored_rows))
    monkeypatch.setattr(douyin_api, "merge_douyin_stranger_message_results", store)
    monkeypatch.setattr(douyin_api, "merge_douyin_inbox_results", lambda account_id, incoming: len(incoming))
    monkeypatch.setattr(douyin_api, "_queue_douyin_wechat_friend_add", fake_add_friend)

    result = asyncio.run(
        douyin_api.run_douyin_h5_stranger_message_task_once(
            account_id=5,
            fixed_message="请留下手机号，我安排同事联系您",
            wechat_add_friend_enabled=True,
        )
    )

    assert result["status"] == "completed"
    assert result["processed_user_last"] == 2
    assert result["normal_conversations"] == 1
    assert result["stranger_conversations"] == 2
    assert call_order == ["normal", "stranger"]
    assert result["extracted_phone_numbers"] == ["13927485337"]
    assert result["wechat_add_targets"] == ["13927485337"]
    assert [row["username"] for row in sent_rows] == ["没有手机号"]
    assert add_friend_calls == [["13927485337"]]

    second_result = asyncio.run(
        douyin_api.run_douyin_h5_stranger_message_task_once(
            account_id=5,
            fixed_message="请留下手机号，我安排同事联系您",
            wechat_add_friend_enabled=True,
        )
    )

    assert second_result["status"] == "completed"
    assert second_result["wechat_add_targets"] == []
    assert second_result["skipped_duplicate_reply"] == 1
    assert [row["username"] for row in sent_rows] == ["没有手机号"]
    assert add_friend_calls == [["13927485337"]]


def test_current_detail_direction_wins_over_stored_row(monkeypatch):
    old_row = douyin_api.normalize_douyin_stranger_message_row(
        {
            "account_id": 5,
            "conversation_key": "same-contact",
            "username": "同一个联系人",
            "last_message_text": "我之前发的",
            "last_message_is_user": False,
            "reply_status": "sent",
        }
    )
    monkeypatch.setattr(douyin_api, "douyin_stranger_message_results", [old_row])
    monkeypatch.setattr(douyin_api, "save_douyin_stranger_message_results", lambda: None)

    douyin_api.merge_douyin_stranger_message_results(
        5,
        [
            {
                "account_id": 5,
                "conversation_key": "same-contact",
                "username": "同一个联系人",
                "last_message_text": "我刚发的",
                "last_message_is_user": True,
                "detail_read_status": "ok",
            }
        ],
    )

    current = douyin_api.collect_douyin_stranger_message_results(5)[0]
    assert current["last_message_text"] == "我刚发的"
    assert current["last_message_is_user"] is True
    assert current["reply_status"] == "sent"

# ---------------- 私信里的微信号（2026-09-27） ----------------

def test_extract_wechat_id_requires_hint_word():
    from douyin_api import extract_douyin_wechat_ids, looks_like_douyin_wechat_id

    # 有引导词 + 号（同一句 / 换行 / 空格分隔）都能抓
    assert extract_douyin_wechat_ids([{"incoming_message": "我微信是 lisijia8888"}]) == ["lisijia8888"]
    assert extract_douyin_wechat_ids([{"incoming_message": "vx：Meng2026-ok 加我"}]) == ["Meng2026-ok"]
    assert extract_douyin_wechat_ids([{"incoming_message": "微信\nabc_123456"}]) == ["abc_123456"]
    # 没有引导词不猜（避免把昵称/抖音号当微信号）
    assert extract_douyin_wechat_ids([{"incoming_message": "lisijia8888 是我抖音号"}]) == []
    # 只有引导词、后面没有合法候选
    assert extract_douyin_wechat_ids([{"incoming_message": "加我微信聊"}]) == []


def test_extract_wechat_id_ignores_phones_and_junk():
    from douyin_api import extract_douyin_wechat_ids

    # 手机号（纯数字）不算微信号，仍按手机号走
    assert extract_douyin_wechat_ids([{"incoming_message": "微信同号 13927485337"}]) == []
    # 太短 / 含中文 / 常见英文词
    assert extract_douyin_wechat_ids([{"incoming_message": "微信 abc12"}]) == []
    assert extract_douyin_wechat_ids([{"incoming_message": "微信 abcdefg中文"}]) == []
    assert extract_douyin_wechat_ids([{"incoming_message": "wechat is wechat"}]) == []
    # 多个候选去重
    assert extract_douyin_wechat_ids([
        {"incoming_message": "微信：lisijia8888"},
        {"incoming_message": "微信号 lisijia8888"},
    ]) == ["lisijia8888"]


def test_wechat_contact_entries_include_wechat_kind():
    from douyin_api import douyin_wechat_contact_entries

    entries = douyin_wechat_contact_entries([
        {
            "username": "张老师",
            "conversation_source": "chat_inbox",
            "phone_numbers": ["13927485337"],
            "wechat_ids": ["lisijia8888"],
        }
    ])
    assert {"value": "13927485337", "kind": "mobile", "username": "张老师", "conversation_id": "chat_inbox"} in entries
    assert {"value": "lisijia8888", "kind": "wechat_id", "username": "张老师", "conversation_id": "chat_inbox"} in entries

    # 只有微信号、没有手机号时也要上报
    only_wechat = douyin_wechat_contact_entries([
        {"username": "李四", "wechat_ids": ["Meng2026-ok"]}
    ])
    assert [e["value"] for e in only_wechat] == ["Meng2026-ok"]
    assert only_wechat[0]["kind"] == "wechat_id"


def test_wechat_hint_present_gate():
    from douyin_api import douyin_wechat_hint_present

    assert douyin_wechat_hint_present({"incoming_message": "加个微信吧"}) is True
    assert douyin_wechat_hint_present({"incoming_message": "多少钱？"}) is False

def test_wechat_id_flows_same_path_as_phone(monkeypatch):
    """微信号和手机号在微信里是同一个搜索框，所以走同一条路：能抽出来 + 能当加好友目标。"""
    from douyin_api import extract_douyin_wechat_ids, douyin_wechat_contact_entries
    import douyin_api

    assert extract_douyin_wechat_ids([{"incoming_message": "加我微信 eiaiyuangong"}]) == ["eiaiyuangong"]
    assert extract_douyin_wechat_ids([{"incoming_message": "VX：eiaiyuangong"}]) == ["eiaiyuangong"]
    assert douyin_api.looks_like_douyin_wechat_id("eiaiyuangong") is True
    assert douyin_api.looks_like_douyin_wechat_id("eiaiyuangon") is True   # 11 位也合法
    assert douyin_api.looks_like_douyin_wechat_id("eiai") is False        # 太短

    entries = douyin_wechat_contact_entries([
        {"username": "运营", "conversation_source": "chat_inbox",
         "phone_numbers": [], "wechat_ids": ["eiaiyuangong"]}
    ])
    assert entries == [{"value": "eiaiyuangong", "kind": "wechat_id",
                        "username": "运营", "conversation_id": "chat_inbox"}]

    # 加好友任务接受微信号（和手机号同一个调用）
    captured = {}

    class _FakeEngine:
        LOCAL_DEFAULT_ACCOUNT_ID = 1

        @staticmethod
        async def create_add_friend_task(account_id, values):
            captured["account_id"] = account_id
            captured["values"] = list(values)
            return {"id": "task-1"}

    monkeypatch.setitem(sys.modules, "app.services.native_wechat_engine", _FakeEngine)
    monkeypatch.setitem(sys.modules, "backend.app.services.native_wechat_engine", _FakeEngine)
    result = asyncio.run(douyin_api._queue_douyin_wechat_friend_add(["eiaiyuangong"]))
    assert result["queued"] is True
    assert captured["values"] == ["eiaiyuangong"]



def test_wechat_ai_judges_message_without_hint_word(monkeypatch):
    """客户不带「微信/vx/加我」直接甩号时，也要交给 AI 判断，不能只靠规则。"""
    from douyin_api import (
        douyin_wechat_should_ask_ai,
        douyin_wechat_candidate_tokens,
        extract_douyin_wechat_id_by_ai,
        extract_douyin_wechat_ids,
    )

    row = {"incoming_message": "哈喽 zm_kd3 沟通吧"}
    assert extract_douyin_wechat_ids([row]) == []          # 规则保持保守：没引导词不猜
    assert douyin_wechat_candidate_tokens(row) == ["zm_kd3"]
    assert douyin_wechat_should_ask_ai(row) is True

    row2 = {"incoming_message": "jin10190922 你找我吧"}
    assert douyin_wechat_candidate_tokens(row2) == ["jin10190922"]
    assert douyin_wechat_should_ask_ai(row2) is True

    assert douyin_wechat_should_ask_ai({"incoming_message": "多少钱？"}) is False
    assert douyin_wechat_should_ask_ai({"incoming_message": "在吗 你好"}) is False

    seen_prompts = []

    def fake_ai(system_prompt, user_prompt, **kwargs):
        seen_prompts.append(user_prompt)
        return '{"wechat_id": "zm_kd3", "evidence": "哈喽 zm_kd3 沟通吧"}'

    monkeypatch.setattr(douyin_api, "request_douyin_ai_comment", fake_ai)
    douyin_api._DOUYIN_WECHAT_AI_CACHE.clear()
    hit = extract_douyin_wechat_id_by_ai(row)
    assert hit["wechat_id"] == "zm_kd3"
    assert hit["evidence"]
    assert "zm_kd3" in seen_prompts[0] and "疑似微信号候选" in seen_prompts[0]

    # AI 不能瞎编：格式不合法/是抖音号一律丢掉
    monkeypatch.setattr(
        douyin_api, "request_douyin_ai_comment",
        lambda *a, **k: '{"wechat_id": "我的抖音号", "evidence": "x"}',
    )
    douyin_api._DOUYIN_WECHAT_AI_CACHE.clear()
    assert extract_douyin_wechat_id_by_ai(row)["wechat_id"] == ""

    # 纯聊天不去调 AI
    calls = []
    monkeypatch.setattr(douyin_api, "request_douyin_ai_comment",
                        lambda *a, **k: calls.append(1) or "{}")
    douyin_api._DOUYIN_WECHAT_AI_CACHE.clear()
    assert extract_douyin_wechat_id_by_ai({"incoming_message": "多少钱？"})["wechat_id"] == ""
    assert calls == []



def test_wechat_ai_skipped_when_phone_already_found():
    """拿到手机号就不折腾微信号（不再单独调 AI）；没手机号但有像号的候选才调。"""
    from douyin_api import douyin_wechat_needs_ai

    assert douyin_wechat_needs_ai({"incoming_message": "哈喽 zm_kd3 沟通吧"}) is True
    assert douyin_wechat_needs_ai(
        {"incoming_message": "哈喽 zm_kd3 沟通吧", "phone_numbers": ["13927485337"]}
    ) is False
    assert douyin_wechat_needs_ai(
        {"incoming_message": "哈喽 zm_kd3 沟通吧", "wechat_ids": ["zm_kd3"]}
    ) is False
    assert douyin_wechat_needs_ai({"incoming_message": "多少钱？"}) is False


def test_takeover_memory_reply_also_judges_wechat_id(monkeypatch):
    """记忆接管多轮对话：请求回复时让 AI 直接给出「有没有可能有微信号」。"""
    import json as _json

    from douyin_api import generate_douyin_takeover_memory_reply_with_wechat

    row = {
        "username": "小亮",
        "incoming_message": "哈喽 zm_kd3 沟通吧",
        "messages": [
            {"direction": "incoming", "text": "你们这个怎么卖"},
            {"direction": "outgoing", "text": "看你要哪个版本"},
            {"direction": "incoming", "text": "哈喽 zm_kd3 沟通吧"},
        ],
    }
    memory = "## 百问百答\n问：多少钱？\n答：基础版 999 元。"

    def fake_ai(system_prompt, user_prompt, **kwargs):
        assert "wechat_id" in system_prompt
        assert "zm_kd3" in user_prompt          # 对话上下文要带上
        return _json.dumps(
            {
                "replies": ["基础版 999，含拍摄和剪辑", "你要竖屏还是横屏"],
                "wechat_id": "zm_kd3",
                "evidence": "哈喽 zm_kd3 沟通吧",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(douyin_api, "request_douyin_ai_comment", fake_ai)
    got = generate_douyin_takeover_memory_reply_with_wechat(
        row, memory_context=memory, prompt_text="")
    assert got["message"].splitlines() == ["基础版 999，含拍摄和剪辑", "你要竖屏还是横屏"]
    assert got["wechat_id"] == "zm_kd3"
    assert got["evidence"]

    # AI 瞎填（抖音号/乱码）→ 丢掉微信号，但回复照用
    monkeypatch.setattr(
        douyin_api, "request_douyin_ai_comment",
        lambda *a, **k: _json.dumps({"replies": ["好的"], "wechat_id": "我的抖音号"}, ensure_ascii=False),
    )
    got2 = generate_douyin_takeover_memory_reply_with_wechat(row, memory_context=memory, prompt_text="")
    assert got2["wechat_id"] == "" and got2["message"] == "好的"

    # AI 没按 JSON 回（旧行为）→ 整段当回复，微信号留空
    monkeypatch.setattr(douyin_api, "request_douyin_ai_comment", lambda *a, **k: "第一行\n第二行")
    got3 = generate_douyin_takeover_memory_reply_with_wechat(row, memory_context=memory, prompt_text="")
    assert got3["message"] == "第一行\n第二行" and got3["wechat_id"] == ""

    # 旧入口保持返回字符串
    monkeypatch.setattr(douyin_api, "request_douyin_ai_comment", fake_ai)
    legacy = douyin_api.generate_douyin_takeover_memory_reply(row, memory_context=memory, prompt_text="")
    assert isinstance(legacy, str) and "999" in legacy
