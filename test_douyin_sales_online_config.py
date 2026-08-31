import asyncio

import backend.app.api.h5_chat_channel as h5_chat_channel

h5_chat_channel._install_douyin_origin_import_path()
import douyin_api  # type: ignore  # noqa: E402
from douyin_comment_scraper import DouyinCommentScraper  # type: ignore  # noqa: E402
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from backend.app.api.h5_chat_channel import (
    _merge_scheduled_douyin_collection_params,
    _merge_scheduled_douyin_precise_touch_params,
    _merge_scheduled_douyin_stranger_params,
    _scheduled_douyin_changed_conversations,
    _scheduled_douyin_followup_actions,
    _scheduled_douyin_online_config_params,
    _run_scheduled_douyin_sales_action,
    _scheduled_douyin_result_payload,
    _scheduled_douyin_sales_action_from_context,
    _scheduled_douyin_search_keywords,
)


class _FakeKeyboard:
    def __init__(self):
        self.presses = []

    async def press(self, key):
        self.presses.append(key)


class _FakeMentionPage:
    def __init__(self):
        self.keyboard = _FakeKeyboard()

    async def wait_for_timeout(self, _timeout_ms):
        await asyncio.sleep(0)


class _FakeLocatorCollection:
    def __init__(self, items):
        self.items = list(items)

    async def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class _FakeVisibleLocator:
    def __init__(self, visible, children=None):
        self.visible = visible
        self.children = children or {}

    async def is_visible(self):
        return self.visible

    def locator(self, selector):
        return _FakeLocatorCollection(self.children.get(selector, []))


class _FakeComposerPage:
    def __init__(self, dialogs):
        self.dialogs = dialogs

    def locator(self, selector):
        if selector == '[data-e2e="im-dialog"], #messageContent':
            return _FakeLocatorCollection(self.dialogs)
        return _FakeLocatorCollection([])


def test_mention_commit_requires_real_entity_and_uses_keyboard_fallback():
    scraper = DouyinCommentScraper(account_id=1, cdp_port=9332)
    page = _FakeMentionPage()
    snapshots = iter(
        [
            {"editor_text": "@目标用户", "suggestion_visible": True, "mention_entities": []},
            {"editor_text": "@目标用户", "suggestion_visible": True, "mention_entities": []},
            {"editor_text": "@目标用户", "suggestion_visible": True, "mention_entities": []},
            {
                "editor_text": "@目标用户 ",
                "suggestion_visible": False,
                "mention_entities": ["@目标用户"],
            },
        ]
    )

    async def read_snapshot(_page):
        return next(snapshots)

    scraper._read_comment_submission_snapshot = read_snapshot
    result = asyncio.run(
        scraper._wait_for_mention_commit(
            page,
            before_editor_text="",
            expected_username="目标用户",
            selected_label="目标用户",
        )
    )

    assert result == "@目标用户"
    assert page.keyboard.presses == ["ArrowDown", "Enter"]


def test_visible_message_composer_skips_hidden_stale_controls():
    input_selector = (
        'div[data-e2e="msg-input"] [contenteditable="true"], '
        '.public-DraftEditor-content[contenteditable="true"]'
    )
    send_selector = ".e2e-send-msg-btn, [class*='send-msg-btn'], span.e2e-send-msg-btn"
    hidden_input = _FakeVisibleLocator(False)
    hidden_send = _FakeVisibleLocator(False)
    visible_input = _FakeVisibleLocator(True)
    visible_send = _FakeVisibleLocator(True)
    hidden_dialog = _FakeVisibleLocator(
        False,
        {input_selector: [hidden_input], send_selector: [hidden_send]},
    )
    visible_dialog = _FakeVisibleLocator(
        True,
        {
            input_selector: [hidden_input, visible_input],
            send_selector: [hidden_send, visible_send],
        },
    )
    page = _FakeComposerPage([hidden_dialog, visible_dialog])
    scraper = DouyinCommentScraper(account_id=1, cdp_port=9332)

    dialog, input_box, send_button = asyncio.run(
        scraper._resolve_visible_message_composer(page)
    )

    assert dialog is visible_dialog
    assert input_box is visible_input
    assert send_button is visible_send


def test_self_video_load_skips_transient_login_probe_and_uses_real_page(monkeypatch):
    config = {
        "douyin_accounts": [{"id": 1, "port": 9332, "status": "online"}],
        "douyin_default_account_id": 1,
    }
    calls = []

    class _FakeScraper:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        async def scrape_self_videos(self, **kwargs):
            calls.append(("scrape", kwargs))
            return {"profile": {"username": "测试账号"}, "videos": [{"url": "https://www.douyin.com/video/1"}]}

        async def close(self):
            calls.append(("close", {}))

    monkeypatch.setattr(douyin_api, "load_global_config", lambda: config)
    monkeypatch.setattr(douyin_api, "save_global_config", lambda _config: (_ for _ in ()).throw(AssertionError("unexpected config save")))
    monkeypatch.setattr(douyin_api, "save_douyin_mention_self_video_cache", lambda: None)
    monkeypatch.setattr(douyin_api.DouyinClient, "launch_browser", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(douyin_api, "probe_douyin_account_login_state", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("login probe must not run")))
    monkeypatch.setattr(douyin_api, "DouyinCommentScraper", _FakeScraper)

    result = asyncio.run(douyin_api.douyin_get_self_videos(account_id=1, max_videos=6))

    assert result["code"] == 200
    assert result["videos"] == [{"url": "https://www.douyin.com/video/1"}]
    assert [item[0] for item in calls] == ["init", "scrape", "close"]


def test_self_video_load_marks_waiting_only_for_visible_login_intercept(monkeypatch):
    config = {
        "douyin_accounts": [{"id": 1, "port": 9332, "status": "online"}],
        "douyin_default_account_id": 1,
    }
    saved = []

    class _FakeScraper:
        def __init__(self, **_kwargs):
            pass

        async def scrape_self_videos(self, **_kwargs):
            raise RuntimeError("当前抖音浏览器未登录，或登录态已失效，页面出现登录拦截")

        async def close(self):
            pass

    monkeypatch.setattr(douyin_api, "load_global_config", lambda: config)
    monkeypatch.setattr(douyin_api, "save_global_config", lambda value: saved.append(value))
    monkeypatch.setattr(douyin_api.DouyinClient, "launch_browser", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(douyin_api, "DouyinCommentScraper", _FakeScraper)

    result = asyncio.run(douyin_api.douyin_get_self_videos(account_id=1, max_videos=6))

    assert result["code"] == 400
    assert result["type"] == "account_waiting_login"
    assert config["douyin_accounts"][0]["status"] == "waiting"
    assert saved == [config]


def test_self_video_load_uses_same_account_cache_when_profile_page_times_out(monkeypatch):
    config = {
        "douyin_accounts": [{"id": 1, "port": 9332, "status": "online"}],
        "douyin_default_account_id": 1,
    }

    class _FakeScraper:
        def __init__(self, **_kwargs):
            pass

        async def scrape_self_videos(self, **_kwargs):
            raise RuntimeError("Douyin profile first-screen connection timed out; retry later")

        async def close(self):
            pass

    monkeypatch.setattr(douyin_api, "load_global_config", lambda: config)
    monkeypatch.setattr(douyin_api.DouyinClient, "launch_browser", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(douyin_api, "DouyinCommentScraper", _FakeScraper)
    monkeypatch.setattr(
        douyin_api,
        "douyin_mention_self_video_cache",
        {
            "account_id": 1,
            "profile": {"username": "test account"},
            "videos": [{"url": "https://www.douyin.com/video/1"}],
            "fetched_at": "2026-08-31 22:00:00",
        },
    )

    result = asyncio.run(douyin_api.douyin_get_self_videos(account_id=1, max_videos=6))

    assert result["code"] == 200
    assert result["cache_fallback"] is True
    assert result["videos"] == [{"url": "https://www.douyin.com/video/1"}]


def test_profile_navigation_uses_commit_and_retries_after_timeout():
    class _FakePage:
        def __init__(self):
            self.calls = []

        async def goto(self, _url, *, wait_until, timeout):
            self.calls.append((wait_until, timeout))
            if len(self.calls) == 1:
                raise PlaywrightTimeoutError("first navigation timed out")

        async def wait_for_timeout(self, _milliseconds):
            pass

    page = _FakePage()
    scraper = DouyinCommentScraper(account_id=1, cdp_port=9332)

    asyncio.run(scraper._goto_profile_page(page, "https://www.douyin.com/user/self"))

    assert page.calls == [("commit", 15000), ("commit", 15000)]


def test_precise_touch_persists_each_direct_message_result(monkeypatch):
    selected_users = [
        {"username": "成功用户", "profile_url": "https://www.douyin.com/user/success"},
        {"username": "失败用户", "profile_url": "https://www.douyin.com/user/failed"},
    ]
    interaction_users = [
        {**selected_users[0], "interaction_status": "sent", "interaction_message": "已发送"},
        {
            **selected_users[1],
            "interaction_status": "failed",
            "interaction_error": "输入框未出现",
        },
    ]
    state_updates = []

    monkeypatch.setattr(
        douyin_api,
        "collect_douyin_precise_touch_users",
        lambda _action, _limit: [dict(row) for row in selected_users],
    )
    monkeypatch.setattr(
        douyin_api,
        "update_douyin_precise_touch_users",
        lambda rows, **kwargs: state_updates.append(
            ([row["username"] for row in rows], kwargs["status"], kwargs.get("error", ""))
        ),
    )
    monkeypatch.setattr(
        douyin_api,
        "collect_douyin_interaction_users",
        lambda _selected_task_ids=None: [dict(row) for row in interaction_users],
    )

    async def start_interaction(request):
        return {"code": 200, "total": len(request["users"])}

    async def interaction_status(*_args, **_kwargs):
        return {
            "running": False,
            "state": {"total": 2, "processed": 2, "success": 1, "failed": 1},
        }

    monkeypatch.setattr(douyin_api, "douyin_start_interaction", start_interaction)
    monkeypatch.setattr(douyin_api, "douyin_interaction_status", interaction_status)
    monkeypatch.setattr(
        h5_chat_channel,
        "_load_scheduled_douyin_online_config_params",
        lambda _action: {},
    )

    result = asyncio.run(
        _run_scheduled_douyin_sales_action(
            "precise_touch",
            {
                "touch_actions": ["direct_message"],
                "max_users": 2,
                "interval_minutes_min": 1,
                "interval_minutes_max": 1,
            },
        )
    )

    final_updates = {
        names[0]: (status, error)
        for names, status, error in state_updates
        if len(names) == 1 and status != "queued"
    }
    assert final_updates["成功用户"] == ("completed", "")
    assert final_updates["失败用户"] == ("failed", "输入框未出现")
    assert result["stats"]["success"] == 0
    assert result["stats"]["failed"] == 1


def test_precise_touch_skips_known_unavailable_users_but_retries_generic_failures(monkeypatch):
    unavailable_user = {
        "username": "deleted-user",
        "profile_url": "https://www.douyin.com/user/deleted-user",
        "is_high_intent": True,
    }
    retryable_user = {
        "username": "retry-user",
        "profile_url": "https://www.douyin.com/user/retry-user",
        "is_high_intent": True,
    }
    unavailable_key = douyin_api.precise_customer_touch_identity_key(unavailable_user)
    retryable_key = douyin_api.precise_customer_touch_identity_key(retryable_user)
    monkeypatch.setattr(
        douyin_api,
        "build_combined_douyin_customer_pools",
        lambda: ([], [dict(unavailable_user), dict(retryable_user)]),
    )
    monkeypatch.setattr(
        douyin_api,
        "douyin_precise_touch_state",
        {
            unavailable_key: {
                "direct_message": {
                    "status": "failed",
                    "error": "用户不存在：该抖音主页已失效或已被删除",
                }
            },
            retryable_key: {
                "direct_message": {
                    "status": "failed",
                    "error": "页面输入框未出现",
                }
            },
        },
    )

    users = douyin_api.collect_douyin_precise_touch_users(
        action="direct_message",
        limit=10,
    )

    assert [row["username"] for row in users] == ["retry-user"]


def test_precise_touch_persists_confirmed_missing_account_as_unavailable(monkeypatch):
    user = {
        "username": "deleted-user",
        "profile_url": "https://www.douyin.com/user/deleted-user",
    }
    key = douyin_api.precise_customer_touch_identity_key(user)
    monkeypatch.setattr(douyin_api, "douyin_precise_touch_state", {})
    monkeypatch.setattr(douyin_api, "save_douyin_precise_touch_state", lambda: None)
    monkeypatch.setattr(douyin_api, "save_douyin_tasks_state", lambda: None)

    douyin_api.update_douyin_precise_touch_users(
        [user],
        action="direct_message",
        status="failed",
        error="用户不存在：该抖音主页已失效或已被删除，无法发送私信",
    )

    assert douyin_api.douyin_precise_touch_state[key]["direct_message"]["status"] == "unavailable"


def test_precise_touch_busy_launch_reports_not_started_without_stale_success(monkeypatch):
    selected_users = [
        {
            "username": "待重试客户",
            "profile_url": "https://www.douyin.com/user/retry",
            "follow_comment_status": "completed",
        }
    ]
    state_updates = []

    monkeypatch.setattr(
        douyin_api,
        "collect_douyin_precise_touch_users",
        lambda _action, _limit: [dict(row) for row in selected_users],
    )
    monkeypatch.setattr(
        douyin_api,
        "update_douyin_precise_touch_users",
        lambda rows, **kwargs: state_updates.append((kwargs["status"], kwargs.get("error", ""))),
    )
    monkeypatch.setattr(
        douyin_api,
        "collect_douyin_interaction_users",
        lambda _selected_task_ids=None: [dict(row) for row in selected_users],
    )

    async def busy_follow_comment(request):
        assert request["users"]
        return {"code": 400, "msg": "关注评论任务已在执行中"}

    monkeypatch.setattr(douyin_api, "douyin_start_follow_comment", busy_follow_comment)
    monkeypatch.setattr(
        h5_chat_channel,
        "_load_scheduled_douyin_online_config_params",
        lambda _action: {"comment_mode": "fixed", "comment_text": "你好"},
    )

    result = asyncio.run(
        _run_scheduled_douyin_sales_action(
            "precise_touch",
            {"touch_actions": ["follow_comment"], "max_users": 1},
        )
    )

    stat = result["action_stats"][0]
    assert stat == {
        "action": "follow_comment",
        "label": stat["label"],
        "selected": 1,
        "processed": 0,
        "success": 0,
        "failed": 0,
        "not_started": 1,
        "started": False,
        "result_code": 400,
        "error": "关注评论任务已在执行中",
    }
    assert result["stats"]["success_users"] == 0
    assert result["stats"]["not_started_users"] == 1
    assert state_updates[-1][0] == "failed"


def test_precise_touch_starts_later_independent_actions_after_rejection(monkeypatch):
    selected_users = [
        {
            "username": "串行客户",
            "profile_url": "https://www.douyin.com/user/serial",
        }
    ]
    claimed_actions = []
    started_actions = []

    def collect_users(action, _limit):
        claimed_actions.append(action)
        return [dict(row) for row in selected_users]

    monkeypatch.setattr(douyin_api, "collect_douyin_precise_touch_users", collect_users)
    monkeypatch.setattr(douyin_api, "update_douyin_precise_touch_users", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(
        douyin_api,
        "collect_douyin_interaction_users",
        lambda _selected_task_ids=None: [dict(row) for row in selected_users],
    )

    async def busy_follow_comment(request):
        started_actions.append("follow_comment")
        return {"code": 400, "msg": "已有任务运行"}

    async def direct_message(request):
        started_actions.append("direct_message")
        return {"code": 200, "total": len(request["users"])}

    monkeypatch.setattr(douyin_api, "douyin_start_follow_comment", busy_follow_comment)
    monkeypatch.setattr(douyin_api, "douyin_start_interaction", direct_message)
    monkeypatch.setattr(
        h5_chat_channel,
        "_load_scheduled_douyin_online_config_params",
        lambda action: (
            {"comment_mode": "fixed", "comment_text": "你好"}
            if action == "follow_comment"
            else {"message_mode": "fixed", "message": "你好"}
        ),
    )

    result = asyncio.run(
        _run_scheduled_douyin_sales_action(
            "precise_touch",
            {
                "touch_actions": ["follow_comment", "direct_message"],
                "max_users": 1,
            },
        )
    )

    assert claimed_actions == ["follow_comment", "direct_message"]
    assert started_actions == ["follow_comment", "direct_message"]
    assert [item["result_code"] for item in result["action_stats"]] == [400, 200]
    assert result["stats"]["processed"] == 1
    assert result["stats"]["not_started"] == 1


def test_precise_touch_continues_to_direct_message_when_mention_page_is_unavailable(monkeypatch):
    selected_users = [
        {
            "username": "retry customer",
            "profile_url": "https://www.douyin.com/user/retry-customer",
        }
    ]
    claimed_actions = []
    started_actions = []

    def collect_users(action, _limit):
        claimed_actions.append(action)
        return [dict(row) for row in selected_users]

    monkeypatch.setattr(douyin_api, "collect_douyin_precise_touch_users", collect_users)
    monkeypatch.setattr(douyin_api, "update_douyin_precise_touch_users", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(
        douyin_api,
        "collect_douyin_interaction_users",
        lambda _selected_task_ids=None: [dict(row) for row in selected_users],
    )

    async def unavailable_self_videos(**_kwargs):
        return {"code": 503, "type": "self_video_page_unavailable", "videos": []}

    async def start_direct_message(request):
        started_actions.append("direct_message")
        assert request["users"] == selected_users
        return {"code": 200, "total": len(request["users"])}

    monkeypatch.setattr(douyin_api, "douyin_get_self_videos", unavailable_self_videos)
    monkeypatch.setattr(douyin_api, "douyin_start_interaction", start_direct_message)
    monkeypatch.setattr(
        h5_chat_channel,
        "_load_scheduled_douyin_online_config_params",
        lambda action: {"message_mode": "fixed", "message": "hello"} if action == "direct_message" else {},
    )

    result = asyncio.run(
        _run_scheduled_douyin_sales_action(
            "precise_touch",
            {"touch_actions": ["mention_comment", "direct_message"], "max_users": 1},
        )
    )

    assert claimed_actions == ["mention_comment", "direct_message"]
    assert started_actions == ["direct_message"]
    assert [item["result_code"] for item in result["action_stats"]] == [503, 200]


def test_h5_employee_editor_exposes_precise_touch_actions():
    from pathlib import Path

    root = Path(__file__).resolve().parent
    script = (root / "static" / "js" / "views" / "h5-employees.js").read_text(encoding="utf-8")
    html = (root / "static" / "views" / "h5-employees.html").read_text(encoding="utf-8")

    assert "oeNodeDouyinFollowupField" in html
    assert "oeNodeDouyinTouchField" in html
    assert "customer_scope:'current_collection_batch'" in script
    assert "migrateDouyinFollowupNodes" in script
    for field_id in (
        "oeNodeDouyinKeyword",
        "oeNodeDouyinRegions",
        "oeNodeDouyinMaxResults",
        "oeNodeDouyinMode",
        "oeNodeDouyinReplyPreciseComments",
        "oeNodeDouyinReplyCommentMode",
        "oeNodeDouyinReplyCommentText",
        "oeNodeDouyinReplyCommentPrompt",
        "oeNodeDouyinReplyCommentSeedText",
        "oeNodeDouyinReplyCommentFixedField",
        "oeNodeDouyinReplyCommentAiField",
        "oeNodeDouyinReplyCommentRewriteField",
        "oeNodeDouyinFollowupMentionComment",
        "oeNodeDouyinFollowupFollowComment",
        "oeNodeDouyinFollowupDirectMessage",
    ):
        assert f'id="{field_id}"' in html
    assert "row.params.keyword=keyword" in script
    assert "if (!keyword) throw new Error('请填写采集关键词')" not in script
    assert "留空使用当前设备 Online 已配置的全部关键词" in html
    assert "el('oeNodeDouyinKeyword').value" in script
    assert "if(event.target.id==='oeNodeDouyinReplyCommentMode') syncNodeModalFields();" in script
    assert "replyMode=String((el('oeNodeDouyinReplyCommentMode')" in script
    assert "field.hidden=!visible" in script
    assert "input.disabled=!visible" in script
    assert 'id="oeNodeDouyinReplyCommentFixedField" hidden' in html
    assert 'id="oeNodeDouyinReplyCommentAiField" hidden' in html
    assert 'id="oeNodeDouyinReplyCommentRewriteField" hidden' in html
    assert "return saveTemplate().then" in script
    assert "function workflowParams(node)" in script
    assert "function douyinNodeAction(node, fallbackText)" in script
    assert "fillNodeOptions(node && node.ability_key,node && node.ability_label,node)" in script
    assert "selectedSalesAction === 'precise_touch'" in script


def test_precise_touch_actions_default_all_but_keep_explicit_empty():
    assert _scheduled_douyin_followup_actions(None, default_all=True) == [
        "follow_comment",
        "mention_comment",
        "direct_message",
    ]
    assert _scheduled_douyin_followup_actions([], default_all=True) == []


def test_online_scheduler_preserves_selected_and_explicit_empty_touch_actions():
    selected = douyin_api.normalize_douyin_schedule_plan(
        {"type": "precise_touch", "touch_actions": ["direct_message", "follow_comment"]}
    )
    empty = douyin_api.normalize_douyin_schedule_plan(
        {"type": "precise_touch", "touch_actions": []}
    )
    legacy_missing = douyin_api.normalize_douyin_schedule_plan({"type": "precise_touch"})

    assert selected["touch_actions"] == ["follow_comment", "direct_message"]
    assert empty["touch_actions"] == []
    assert legacy_missing["touch_actions"] == ["follow_comment", "mention_comment", "direct_message"]


def test_collection_node_params_override_online_defaults():
    params = _merge_scheduled_douyin_collection_params(
        {
            "keyword": "Online 全局关键词",
            "regions": ["全国"],
            "max_results": 50,
            "mode": "script",
        },
        {
            "keyword": "服务器节点关键词",
            "regions": ["深圳", "东莞"],
            "max_results": 80,
            "mode": "api",
            "followup_actions": ["direct_message", "reply_comments"],
        },
    )

    assert params["keyword"] == "服务器节点关键词"
    assert params["regions"] == ["深圳", "东莞"]
    assert params["max_results"] == 80
    assert params["mode"] == "api"
    assert params["reply_precise_comments"] is True
    assert params["followup_actions"] == []
    assert params["customer_scope"] == "current_collection_batch"


def test_collection_node_always_disables_followup_actions():
    params = _merge_scheduled_douyin_collection_params({}, {"followup_actions": []})
    assert params["followup_actions"] == []


def test_collection_workflow_title_keeps_online_keywords_and_followups():
    params = _merge_scheduled_douyin_collection_params(
        {
            "keyword": "AI赚钱",
            "keywords": ["AI赚钱", "AI直播", "AI短视频", "AI获客", "AI智能体", "AI"],
            "max_results": 50,
        },
        {
            "keyword": "抖音获客·关键词抓取精准客户",
            "followup_actions": [
                "reply_comments",
                "mention_comment",
                "follow_comment",
                "direct_message",
            ],
        },
    )

    assert params["keyword"] == "AI赚钱"
    assert params["keywords"] == ["AI赚钱", "AI直播", "AI短视频", "AI获客", "AI智能体", "AI"]
    assert params["followup_actions"] == []
    assert params["customer_scope"] == "current_collection_batch"


def test_precise_touch_node_uses_pool_and_preserves_selected_action_order():
    params = _merge_scheduled_douyin_precise_touch_params(
        {"touch_actions": ["reply_comments", "follow_comment", "mention_comment", "direct_message"]},
        {"touch_actions": ["mention_comment", "follow_comment"], "max_users": 12},
    )

    assert params["touch_actions"] == ["follow_comment", "mention_comment"]
    assert params["max_users"] == 12
    assert params["customer_scope"] == "precise_pool"


def test_precise_customer_reply_processes_only_selected_users(monkeypatch):
    calls = []

    class FakePage:
        async def close(self):
            return None

    class FakeScraper:
        def __init__(self, account_id, cdp_port):
            self.account_id = account_id
            self.cdp_port = cdp_port

        async def open_video_comment_page(self, video_url, **kwargs):
            calls.append(("open", video_url, ""))
            return FakePage()

        async def reply_to_loaded_video_comment(self, page, reply_text, target_comment, **kwargs):
            calls.append(("reply", target_comment["username"], reply_text))

        async def close(self):
            return None

    monkeypatch.setattr(douyin_api, "DouyinCommentScraper", FakeScraper)
    monkeypatch.setattr(douyin_api, "load_global_config", lambda: {})
    monkeypatch.setattr(
        douyin_api,
        "get_online_douyin_accounts",
        lambda _config: [{"id": 1, "port": 9332, "status": "online"}],
    )
    monkeypatch.setattr(
        douyin_api,
        "generate_douyin_video_comment_text",
        lambda task, **_kwargs: f"回复-{task['author']}",
    )

    result = asyncio.run(
        douyin_api.run_douyin_precise_customer_replies(
            [
                {"username": "甲", "task_url": "https://v/1", "task_title": "视频1", "task_author": "作者1"},
                {"username": "乙", "task_url": "https://v/2", "task_title": "视频2", "task_author": "作者2"},
            ],
            comment_mode="fixed",
            comment_text="固定回复",
            interval_minutes_min=0,
            interval_minutes_max=0,
        )
    )

    assert result["code"] == 200
    assert result["success"] == 2
    assert result["failed"] == 0
    assert calls == [
        ("open", "https://v/1", ""),
        ("reply", "甲", "回复-作者1"),
        ("open", "https://v/2", ""),
        ("reply", "乙", "回复-作者2"),
    ]


def test_precise_acquisition_reuses_page_orders_comments_and_retries_current_only(monkeypatch):
    opened_pages = []
    reply_calls = []
    state_updates = []
    failed_once = False

    class FakePage:
        def __init__(self, page_number):
            self.page_number = page_number
            self.closed = False

        async def close(self):
            self.closed = True

    class FakeScraper:
        def __init__(self, account_id, cdp_port):
            self.account_id = account_id
            self.cdp_port = cdp_port

        async def open_video_comment_page(self, video_url, **kwargs):
            page = FakePage(len(opened_pages) + 1)
            opened_pages.append((video_url, page))
            return page

        async def reply_to_loaded_video_comment(self, page, reply_text, target_comment, **kwargs):
            nonlocal failed_once
            username = target_comment["username"]
            reply_calls.append((page.page_number, username, kwargs.get("allow_scroll")))
            if username == "乙" and not failed_once:
                failed_once = True
                raise RuntimeError("page stale")

        async def close(self):
            return None

    monkeypatch.setattr(douyin_api, "DouyinCommentScraper", FakeScraper)
    monkeypatch.setattr(
        douyin_api,
        "generate_douyin_video_comment_text",
        lambda *_args, **_kwargs: "统一回复",
    )
    monkeypatch.setattr(
        douyin_api,
        "update_douyin_precise_touch_users",
        lambda users, **kwargs: state_updates.append((users[0]["username"], kwargs["status"])),
    )
    monkeypatch.setattr(douyin_api, "save_douyin_tasks_state", lambda: None)

    result = asyncio.run(
        douyin_api._run_douyin_precise_customer_reply_batch_worker(
            {"url": "https://www.douyin.com/video/123", "title": "测试视频"},
            [
                {"username": "丙", "comment_index": 3, "comment": "c"},
                {"username": "甲", "comment_index": 1, "comment": "a"},
                {"username": "乙", "comment_index": 2, "comment": "b"},
            ],
            {"id": 1, "port": 9332},
            "fixed",
            "统一回复",
            "",
            "",
            0,
            0,
        )
    )

    assert [item["user"]["username"] for item in result] == ["甲", "乙", "丙"]
    assert [item["status"] for item in result] == ["completed", "completed", "completed"]
    assert len(opened_pages) == 2
    assert reply_calls == [(1, "甲", True), (1, "乙", True), (2, "乙", True), (2, "丙", True)]
    assert state_updates.count(("甲", "completed")) == 1
    assert state_updates.count(("乙", "completed")) == 1
    assert state_updates.count(("丙", "completed")) == 1


def test_ai_filter_preserves_global_comment_indexes_across_batches(monkeypatch):
    client = douyin_api.AIClient("https://example.invalid", "test-key")
    comments = [
        {
            "comment_index": index,
            "username": f"用户{index}",
            "user_id": f"user-{index}",
            "content": f"评论{index}",
            "comment_time": f"time-{index}",
        }
        for index in range(1, 162)
    ]

    def fake_filter_batch(_title, batch, *_args, **_kwargs):
        positions = [1]
        if len(batch) > 1:
            positions.append(len(batch))
        return [
            {
                **batch[position - 1],
                "comment_index": position,
                "comment": batch[position - 1]["content"],
            }
            for position in positions
        ]

    monkeypatch.setattr(client, "_filter_comments_batch", fake_filter_batch)

    result = client.filter_comments("测试视频", comments)

    assert [row["comment_index"] for row in result] == [1, 80, 81, 160, 161]


def test_ai_filter_logs_and_uses_explicit_collection_batch_size(monkeypatch):
    client = douyin_api.AIClient("https://example.invalid", "test-key")
    events = []
    comments = [
        {
            "comment_index": index,
            "username": f"用户{index}",
            "user_id": f"user-{index}",
            "content": f"评论{index}",
        }
        for index in range(1, 26)
    ]

    def fake_filter_batch(_title, batch, *_args, **_kwargs):
        return [{**batch[0], "comment_index": 1}]

    monkeypatch.setattr(client, "_filter_comments_batch", fake_filter_batch)

    result = client.filter_comments(
        "测试视频",
        comments,
        batch_size=20,
        event_logger=lambda event, **fields: events.append((event, fields)),
    )

    invoke = next(fields for event, fields in events if event == "ai_filter_invoke")
    assert invoke["batch_size"] == 20
    assert invoke["total_batches"] == 2
    assert len(result) == 2


def test_old_self_comment_time_line_is_not_kept_as_timestamp():
    assert douyin_api.normalize_douyin_self_comment_time("喜羊羊 ... 这条评论 2周前") == "2周前"
    assert douyin_api.normalize_douyin_self_comment_time("喜羊羊 ... 这条评论") == ""


def test_precise_reply_restores_bad_batch_indexes_from_source_comments():
    source_comments = [
        {
            "comment_index": 1,
            "username": "第一批",
            "user_id": "user-1",
            "comment": "评论1",
            "comment_time": "time-1",
        },
        {
            "comment_index": 161,
            "username": "第三批",
            "user_id": "user-161",
            "comment": "评论161",
            "comment_time": "time-161",
        },
    ]
    filtered_users = [
        {**source_comments[1], "comment_index": 1},
        source_comments[0],
    ]

    restored = douyin_api._restore_douyin_comment_order(filtered_users, source_comments)
    ordered = douyin_api._sort_douyin_comment_users(restored)

    assert [row["username"] for row in ordered] == ["第一批", "第三批"]
    assert [row["comment_index"] for row in ordered] == [1, 161]


def test_precise_pool_groups_canonical_video_urls_and_reuses_each_page(monkeypatch):
    open_calls = []
    reply_calls = []

    class FakePage:
        def __init__(self, video_url):
            self.video_url = video_url

        async def close(self):
            return None

    class FakeScraper:
        def __init__(self, account_id, cdp_port):
            pass

        async def open_video_comment_page(self, video_url, **kwargs):
            open_calls.append(video_url)
            return FakePage(video_url)

        async def reply_to_loaded_video_comment(self, page, reply_text, target_comment, **kwargs):
            reply_calls.append((page.video_url, target_comment["username"]))

        async def close(self):
            return None

    monkeypatch.setattr(douyin_api, "DouyinCommentScraper", FakeScraper)
    monkeypatch.setattr(
        douyin_api,
        "generate_douyin_video_comment_text",
        lambda task, **_kwargs: f"回复-{task['author']}",
    )

    result = asyncio.run(
        douyin_api._run_douyin_precise_customer_reply_worker(
            [
                {
                    "username": "乙",
                    "comment_index": 2,
                    "task_url": "https://www.douyin.com/video/123",
                    "task_author": "作者1",
                },
                {
                    "username": "另一个视频",
                    "comment_index": 1,
                    "task_url": "https://www.douyin.com/video/456",
                    "task_author": "作者2",
                },
                {
                    "username": "甲",
                    "comment_index": 1,
                    "task_url": "https://www.douyin.com/user/self?modal_id=123",
                    "task_author": "作者1",
                },
            ],
            {"id": 1, "port": 9332},
            "fixed",
            "固定回复",
            "",
            "",
            0,
            0,
        )
    )

    assert [item["status"] for item in result] == ["completed", "completed", "completed"]
    assert open_calls == ["https://www.douyin.com/video/123", "https://www.douyin.com/video/456"]
    assert reply_calls == [
        ("https://www.douyin.com/video/123", "甲"),
        ("https://www.douyin.com/video/123", "乙"),
        ("https://www.douyin.com/video/456", "另一个视频"),
    ]


def test_precise_touch_pool_excludes_completed_and_inflight_but_keeps_failed(monkeypatch):
    rows = [
        {"sec_user_id": "done", "username": "已完成", "is_high_intent": True},
        {"sec_user_id": "busy", "username": "执行中", "is_high_intent": True},
        {"sec_user_id": "retry", "username": "失败可重试", "is_high_intent": True},
    ]
    monkeypatch.setattr(douyin_api, "build_combined_douyin_customer_pools", lambda: (rows, rows))
    monkeypatch.setattr(
        douyin_api,
        "douyin_precise_touch_state",
        {
            "user:done": {"follow_comment": {"status": "completed"}},
            "user:busy": {"follow_comment": {"status": "processing"}},
            "user:retry": {"follow_comment": {"status": "failed"}},
        },
    )

    selected = douyin_api.collect_douyin_precise_touch_users("follow_comment", limit=10)

    assert [row["username"] for row in selected] == ["失败可重试"]


def test_stranger_workflow_explicit_false_overrides_online_config():
    params = _merge_scheduled_douyin_stranger_params(
        {"wechat_add_friend_enabled": True, "message": "saved reply"},
        {"wechat_add_friend_enabled": False},
    )

    assert params["wechat_add_friend_enabled"] is False
    assert params["message"] == "saved reply"


def test_old_sales_workflow_context_recovers_the_real_action():
    context = {"department_id": "sales", "ability_label": "抖音自己评论区接管"}

    assert _scheduled_douyin_sales_action_from_context(context) == "mention_comment"


def test_search_action_uses_online_plan_instead_of_ip_persona_fields():
    params = _scheduled_douyin_online_config_params(
        "search_collect",
        config={"douyin_default_account_id": 3, "comment_scroll_rounds": 88, "comment_max_comments": 166},
        plans=[
            {
                "type": "collect_precise",
                "keyword": "本机配置关键词",
                "max_results": 36,
                "max_videos_per_run": 4,
                "updated_at": "2026-08-05 12:00:00",
            }
        ],
        search_sessions=[{"keyword": "旧搜索关键词", "updated_at": 1}],
    )

    assert params == {
        "account_id": 3,
        "keywords": ["本机配置关键词", "旧搜索关键词"],
        "keyword": "本机配置关键词",
        "max_results": 36,
        "max_videos_per_run": 4,
        "comment_scroll_rounds": 88,
        "comment_max_comments": 166,
        "mode": "script",
    }


def test_search_action_uses_all_enabled_online_keywords():
    params = _scheduled_douyin_online_config_params(
        "search_collect",
        plans=[
            {"type": "collect_precise", "keyword": "工业机器人", "enabled": True, "updated_at": "2"},
            {"type": "collect_precise", "keyword": "数控加工", "enabled": True, "updated_at": "1"},
            {"type": "collect_precise", "keyword": "已停用", "enabled": False, "updated_at": "3"},
        ],
        search_sessions=[
            {"keyword": "精密零件", "updated_at": 3},
            {"keyword": "工业机器人", "updated_at": 2},
        ],
    )

    assert params["keywords"] == ["工业机器人", "数控加工", "精密零件"]
    assert _scheduled_douyin_search_keywords(params) == ["工业机器人", "数控加工", "精密零件"]


def test_search_keywords_split_manual_input_and_keep_all_online_values():
    online_keywords = [f"行业词{i}" for i in range(1, 16)]

    assert _scheduled_douyin_search_keywords(
        {"keyword": "深圳装修、口腔种植, 母婴门店\n工业机器人；数控加工"}
    ) == ["深圳装修", "口腔种植", "母婴门店", "工业机器人", "数控加工"]
    assert _scheduled_douyin_search_keywords({"keywords": online_keywords}) == online_keywords


def test_multi_keyword_collection_runs_each_keyword_and_merges_tasks(monkeypatch):
    calls = []

    async def fake_single(params):
        keyword = params["keyword"]
        calls.append(keyword)
        task_id = len(calls)
        return {
            "code": 200,
            "msg": "ok",
            "keyword": keyword,
            "search_total": 10,
            "selected_task_ids": [task_id],
            "selected_videos_total": 1,
            "selected_item_keys": [f"video:{task_id}"],
            "session_id": f"session:{task_id}",
            "items": [{"title": keyword}],
        }

    async def fake_wait(task_ids):
        return {"status": "done", "tasks": [{"id": task_ids[0], "status": "completed"}]}

    monkeypatch.setattr(h5_chat_channel, "_run_scheduled_douyin_single_search_collect_action", fake_single)
    monkeypatch.setattr(h5_chat_channel, "_wait_for_douyin_collect_completion", fake_wait)

    result = asyncio.run(
        h5_chat_channel._run_scheduled_douyin_search_collect_action(
            {"keywords": ["工业机器人", "数控加工", "精密零件"]}
        )
    )

    assert calls == ["工业机器人", "数控加工", "精密零件"]
    assert result["selected_task_ids"] == [1, 2, 3]
    assert result["search_total"] == 30
    assert [row["collection_status"] for row in result["keyword_summaries"]] == ["done", "done", "done"]


def test_direct_message_action_uses_online_interaction_plan():
    params = _scheduled_douyin_online_config_params(
        "direct_message",
        config={"douyin_default_account_id": 2},
        plans=[
            {
                "type": "interaction",
                "message_mode": "rewrite",
                "message_seed_text": "本机私信基准话术",
                "message_prompt": "按客户信息自然改写",
                "max_users_per_run": 12,
                "interaction_interval_minutes_min": 3,
                "interaction_interval_minutes_max": 7,
            }
        ],
    )

    assert params["account_id"] == 2
    assert params["message_mode"] == "rewrite"
    assert params["message_seed_text"] == "本机私信基准话术"
    assert params["max_users"] == 12
    assert params["interval_minutes_min"] == 3
    assert params["interval_minutes_max"] == 7


def test_stranger_takeover_reuses_online_monitor_configuration():
    params = _scheduled_douyin_online_config_params(
        "stranger_message",
        config={"douyin_default_account_id": 5},
        stranger_monitors=[
            {
                "account_id": 5,
                "enabled": True,
                "interval_minutes": 9,
                "max_conversations": 40,
                "auto_reply_enabled": True,
                "reply_mode": "ai_lead",
                "reply_prompt": "基于本机资料回复",
                "contact_value": "wx-local",
                "wechat_add_friend_enabled": True,
            }
        ],
    )

    assert params["account_id"] == 5
    assert params["interval_minutes"] == 9
    assert params["max_users"] == 40
    assert params["auto_reply_enabled"] is True
    assert params["reply_mode"] == "ai_lead"
    assert params["contact_value"] == "wx-local"
    assert params["wechat_add_friend_enabled"] is True


def test_sales_result_payload_exposes_readable_fields_at_top_level():
    result = {
        "code": 200,
        "msg": "主动私信执行完成",
        "summary": "共 2 人，成功 1 人，失败 1 人",
        "status": "done",
        "account_id": 2,
        "stats": {"total": 2, "processed": 2, "success": 1, "failed": 1},
        "final_state": {"running": False, "processed": 2},
        "users": [
            {"username": "客户甲", "status": "sent", "sent_text": "您好"},
            {"username": "客户乙", "status": "failed", "error": "账号限制"},
        ],
    }

    payload = _scheduled_douyin_result_payload("direct_message", result, {"account_id": 2})

    assert payload["summary"] == result["summary"]
    assert payload["stats"]["success"] == 1
    assert payload["users"][0]["sent_text"] == "您好"
    assert payload["final_state"]["running"] is False
    assert payload["mcp_result"] == result


def test_stranger_takeover_returns_changed_incoming_and_reply_content_only():
    before = [
        {
            "conversation_key": "old",
            "username": "旧客户",
            "incoming_message": "旧消息",
            "reply_status": "sent",
            "reply_message": "旧回复",
        }
    ]
    after = [
        dict(before[0]),
        {
            "conversation_key": "new",
            "username": "新客户",
            "incoming_message": "这个产品怎么报价？",
            "unread_count": 1,
            "reply_status": "sent",
            "reply_message": "您好，请告诉我需要的数量。",
            "reply_updated_at": "2026-08-05 16:00:00",
        },
    ]

    rows = _scheduled_douyin_changed_conversations(before, after)

    assert len(rows) == 1
    assert rows[0]["username"] == "新客户"
    assert rows[0]["incoming_message"] == "这个产品怎么报价？"
    assert rows[0]["reply_message"] == "您好，请告诉我需要的数量。"
