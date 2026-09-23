"""私信弹层重试预算 + 关注按钮轮询（2026-09-21）。

现场：一个必然开不出私信弹层的主页会被三层重试点 36 次、耗 4~5 分钟（10 人轮次 90 分钟）；
关注按钮却相反——固定等 4.5 秒只试一次，页面慢一点就误判“该主页不允许关注”（近 3 天 17 次）。
"""

import asyncio

import backend.app.api.h5_chat_channel as h5_chat_channel

h5_chat_channel._install_douyin_origin_import_path()
import douyin_comment_scraper as scraper_module  # type: ignore  # noqa: E402
from douyin_comment_scraper import (  # type: ignore  # noqa: E402
    DouyinCommentScraper,
    DouyinPrivateMessageUnavailable,
)

PROFILE_URL = "https://www.douyin.com/user/demo"


class _FakeLocator:
    def __init__(self, page):
        self._page = page

    @property
    def first(self):
        return self

    def nth(self, _index):
        return self

    async def scroll_into_view_if_needed(self, **_kwargs):
        return None

    async def click(self, **_kwargs):
        self._page.clicks += 1
        return None

    async def bounding_box(self, **_kwargs):
        return {"x": 10, "y": 10, "width": 40, "height": 20}

    async def is_visible(self):
        return True

    async def evaluate(self, *_args, **_kwargs):
        return 100

    async def wait_for(self, **_kwargs):
        # 私信弹层永远不出现：就是现场那种“按钮点了没反应”的主页
        raise TimeoutError("dialog not visible")


class _FakePage:
    def __init__(self, follow_states=None):
        self.clicks = 0
        self.waits = []
        self.follow_states = list(follow_states or [])
        self.follow_checks = 0
        self.reloads = 0

    async def reload(self, *_args, **_kwargs):
        self.reloads += 1

    async def goto(self, *_args, **_kwargs):
        return None

    async def wait_for_timeout(self, ms):
        self.waits.append(ms)

    async def close(self):
        return None

    def locator(self, _selector):
        return _FakeLocator(self)

    async def evaluate(self, script, *_args, **_kwargs):
        text = str(script or "")
        if "followNode" in text:
            self.follow_checks += 1
            if self.follow_states:
                return self.follow_states.pop(0)
            return {"action": "not_found", "label": ""}
        if "firstWorkUrl" in text:
            return {"firstWorkUrl": ""}
        if "扫码登录" in text or "验证码登录" in text:
            return False
        return 2  # private_button_count


def _scraper_with(monkeypatch, page):
    scraper = DouyinCommentScraper(account_id=1, cdp_port=9332)

    async def fake_new_page(logger=None):
        return page

    async def noop(*_args, **_kwargs):
        return None

    async def fake_button_ready(_page, button_selectors, **_kwargs):
        return {"selector": button_selectors[0], "index": 1, "marker": "marker-1"}

    async def fake_click(*_args, **_kwargs):
        page.clicks += 1
        return True

    async def fake_mark(*_args, **_kwargs):
        return {"found": True, "marker": "marker-1", "debug": []}

    async def fake_find(*_args, **_kwargs):
        return {"index": 1, "count": 2, "area": 120, "reason": ""}

    monkeypatch.setattr(scraper, "_new_page", fake_new_page)
    monkeypatch.setattr(scraper, "_raise_if_profile_unavailable", noop)
    monkeypatch.setattr(scraper, "_raise_if_login_intercept", noop)
    monkeypatch.setattr(scraper, "_wait_for_profile_action_area_ready", noop)
    monkeypatch.setattr(scraper, "_wait_for_private_message_button_ready", fake_button_ready)
    monkeypatch.setattr(scraper, "_click_marked_private_message_button", fake_click)
    monkeypatch.setattr(scraper, "_mark_dom_private_message_button", fake_mark)
    monkeypatch.setattr(scraper, "_find_visible_private_message_button", fake_find)
    return scraper


def _open_dm(scraper, **kwargs):
    logs = []
    try:
        asyncio.run(
            scraper.send_private_message(
                PROFILE_URL,
                "第一行\n第二行",
                logger=lambda message, level="info": logs.append(message),
            )
        )
    except DouyinPrivateMessageUnavailable as exc:
        return "unavailable", str(exc), logs
    except Exception as exc:  # noqa: BLE001
        return type(exc).__name__, str(exc), logs
    return "ok", "", logs


def test_dm_open_dialog_stops_after_miss_limit(monkeypatch):
    page = _FakePage()
    scraper = _scraper_with(monkeypatch, page)
    monkeypatch.setattr(scraper_module, "DOUYIN_PM_DIALOG_MISS_LIMIT", 3)
    monkeypatch.setattr(scraper_module, "DOUYIN_PM_DIALOG_OPEN_BUDGET_SECONDS", 60.0)

    outcome, message, logs = _open_dm(scraper)

    assert outcome == "unavailable"
    assert "没有可用的私信入口" in message
    # 修复前这里是 36 次点击（三层重试）；现在连续 3 次弹层不出来就判定不可私信
    assert page.clicks == 3, f"应在连续失败断点处停下，实际点击 {page.clicks} 次"
    assert any("打开私信面板失败" in line for line in logs), logs


def test_dm_open_dialog_respects_time_budget(monkeypatch):
    page = _FakePage()
    scraper = _scraper_with(monkeypatch, page)
    monkeypatch.setattr(scraper_module, "DOUYIN_PM_DIALOG_MISS_LIMIT", 99)
    monkeypatch.setattr(scraper_module, "DOUYIN_PM_DIALOG_OPEN_BUDGET_SECONDS", 0.2)

    async def slow_wait(ms):
        page.waits.append(ms)
        await asyncio.sleep(0.05)

    monkeypatch.setattr(page, "wait_for_timeout", slow_wait)

    outcome, _message, _logs = _open_dm(scraper)

    assert outcome == "unavailable"
    # 预算只管时间：没有断点时也必须自己停下来，而不是点满所有层
    assert page.clicks <= 4, f"预算应该拦住重试，实际点击 {page.clicks} 次"


def test_follow_button_polls_until_it_appears(monkeypatch):
    page = _FakePage(
        follow_states=[
            {"action": "not_found", "label": ""},
            {"action": "clicked", "label": "关注"},
        ]
    )
    scraper = _scraper_with(monkeypatch, page)
    logs = []

    result = asyncio.run(
        scraper.follow_user_and_find_first_post(
            PROFILE_URL,
            "用户A",
            logger=lambda message, level="info": logs.append(str(message)),
        )
    )

    assert result["followed"] is True
    # 老逻辑固定等 4.5 秒只查一次，第二轮才出现的按钮会被判成“不允许关注”
    assert page.follow_checks == 2, f"应轮询到第 2 次命中，实际 {page.follow_checks} 次"
    assert page.reloads == 0
    assert not any("仍未出现" in line for line in logs)


def test_follow_button_failure_message_mentions_wait(monkeypatch):
    page = _FakePage()
    scraper = _scraper_with(monkeypatch, page)
    monkeypatch.setattr(scraper_module, "DOUYIN_FOLLOW_BUTTON_WAIT_SECONDS", 0.1)

    async def instant_wait(ms):
        page.waits.append(ms)

    monkeypatch.setattr(page, "wait_for_timeout", instant_wait)

    try:
        asyncio.run(scraper.follow_user_and_find_first_post(PROFILE_URL, "用户A"))
    except RuntimeError as exc:
        assert "未找到可点击的关注按钮" in str(exc)
        assert "已轮询等待" in str(exc)
        assert "并已刷新主页重试一次" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("按钮一直不出现时应该报错")
    assert page.follow_checks > 1, "应该是轮询多次后才失败"
    assert page.reloads == 1


def test_unreachable_target_is_skipped_not_failed():
    """「该主页没有可用的私信入口」不算失败：状态按 skipped 上报，也不进重试。"""
    from backend.app.api import h5_chat_channel as h5

    assert h5._normalize_target_state("unavailable") == "skipped"
    assert h5._normalize_target_state("skipped") == "skipped"
    assert h5._normalize_target_state("failed") == "failed"
    assert h5._normalize_target_state("sent") == "succeeded"

    assert h5._scheduled_douyin_precise_touch_user_status("direct_message", {"status": "unavailable"}) == "unavailable"
    assert h5._scheduled_douyin_precise_touch_user_status("direct_message", {"status": "sent"}) == "completed"
    assert h5._scheduled_douyin_precise_touch_user_status("direct_message", {"status": "failed"}) == "failed"


def test_targets_detail_reports_skipped_state():
    from backend.app.api import h5_chat_channel as h5

    results = [
        {
            "action": "direct_message",
            "label": "主动私信精准客户",
            "result": {"code": 200, "msg": "主动私信完成"},
            "users": [
                {"username": "甲", "status": "sent", "error": ""},
                {
                    "username": "乙",
                    "status": "unavailable",
                    "error": "该主页没有可用的私信入口：私信按钮已点击但面板未出现",
                },
            ],
            "stats": {},
        }
    ]

    detail = h5._build_targets_detail(results=results, action="precise_touch", fallback_reason="summary")
    states = {row["target"]: row["state"] for row in detail}

    assert states == {"甲": "succeeded", "乙": "skipped"}, states


def test_refresh_interaction_state_counts_skipped():
    """回归：refresh_interaction_state_from_workers 必须自己算 skipped。

    2026-09-22 线上事故：这个函数引用了没定义的 `skipped`，导致私信触达 worker
    一启动就 NameError 崩溃 → 两个触达节点"处理 0 个"。
    """
    import douyin_api  # type: ignore

    douyin_api.douyin_interaction_state["workers"] = [
        {"processed": 3, "success": 1, "failed": 1, "skipped": 1, "current_user": "甲"},
        {"processed": 2, "success": 2, "failed": 0, "skipped": 0, "current_user": ""},
    ]
    try:
        douyin_api.refresh_interaction_state_from_workers(10, 240, 360)
        state = douyin_api.douyin_interaction_state
        assert state["processed"] == 5
        assert state["success"] == 3
        assert state["failed"] == 1
        assert state["skipped"] == 1
    finally:
        douyin_api.douyin_interaction_state["workers"] = []


def test_follow_comment_state_refresh_has_no_stray_skipped():
    """同一个补丁曾把 skipped 写进 follow_comment 的刷新函数（死代码），别再回来。"""
    import douyin_api  # type: ignore

    douyin_api.douyin_follow_comment_state["workers"] = []
    douyin_api.refresh_follow_comment_state_from_workers(0, 240, 360)


def test_follow_button_unavailable_does_not_reload(monkeypatch):
    page = _FakePage(
        follow_states=[
            {
                "action": "unavailable",
                "label": "",
                "unavailable_reason": "用户不存在，该抖音主页已失效或已被删除",
            }
        ]
    )
    scraper = _scraper_with(monkeypatch, page)

    try:
        asyncio.run(scraper.follow_user_and_find_first_post(PROFILE_URL, "用户A"))
    except RuntimeError as exc:
        assert "用户不存在" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("主页不可用时应该直接失败")
    assert page.follow_checks == 1
    assert page.reloads == 0


def test_mention_queries_cover_precise_touch_failures():
    names = {
        "*一路有李*": (["*一路有李*", "一路有李", "一路"], [True, True, True]),
        "A混血儿～大小姐": (["A混血儿～大小姐", "A混血儿大小姐", "A混血儿"], [True, True, True]),
        "fantasticbebe": (["fantasticbebe", "fantasti"], [False, True]),
        "\u200d ————♡⃛ Lᵒᵛᵉ": (["\u200d ————♡⃛ Lᵒᵛᵉ", "Love"], [True, True]),
        "一个厉害的钓手": (["一个厉害的钓手", "一个厉害"], [False, True]),
        "川小川哈": (["川小川哈", "川小"], [False, True]),
        "痛症调理\u2011媚姐": (["痛症调理\u2011媚姐", "痛症调理媚姐", "痛症调理"], [True, True, True]),
        "萍儿、": (["萍儿、", "萍儿"], [True, True]),
    }
    for username, (queries, required) in names.items():
        actual = scraper_module.build_mention_search_queries(username)
        assert actual == queries, (username, actual)
        flags = [scraper_module.mention_query_requires_match(username, query) for query in actual]
        assert flags == required, (username, flags)

    assert scraper_module.mention_label_matches("Love", "Lovely", require_match=True) is False
    assert scraper_module.mention_label_matches("川小川哈", "川小明", require_match=True) is False
    assert scraper_module.mention_label_matches("\u200d ————♡⃛ Lᵒᵛᵉ", "Love", require_match=True) is True


def test_mention_candidate_error_keeps_old_sentence():
    message = scraper_module.format_mention_candidate_error(
        "萍儿、",
        {"root_visible": False, "root_count": 0, "row_count": 0, "editor_text": "@萍儿"},
    )
    assert message.startswith("输入 @萍儿、 后没有出现可点击的候选用户")
    assert "候选容器未出现" in message
    assert "编辑框内容=@萍儿" in message


def test_completed_result_includes_failure_reason_not_skip():
    users = [
        {"username": "甲", "status": "completed", "error": ""},
        {
            "username": "乙",
            "status": "failed",
            "error": "未找到可点击的关注按钮（已轮询等待 15 秒，并已刷新主页重试一次）",
        },
        {"username": "丙", "status": "unavailable", "error": "用户不存在，该抖音主页已失效或已被删除"},
        {
            "username": "丁",
            "status": "failed",
            "error": "未找到可点击的关注按钮（已轮询等待 15 秒，并已刷新主页重试一次）",
        },
    ]
    result = h5_chat_channel._scheduled_douyin_completed_result(
        "follow_comment",
        {"code": 200, "msg": "ok"},
        {"status": "done", "state": {"total": 4, "processed": 4, "success": 1, "failed": 2}},
        users=users,
    )
    assert result["code"] == 200
    assert "失败 2。失败原因：" in result["summary"]
    assert "未找到可点击的关注按钮" in result["summary"]
    assert result["summary"].count("未找到可点击的关注按钮") == 1
    assert "用户不存在" not in result["summary"]

    line = h5_chat_channel._scheduled_douyin_precise_touch_detail_line(
        {
            "label": "关注并评论",
            "selected": 10,
            "processed": 10,
            "success": 7,
            "failed": 3,
            "skipped": 0,
            "not_started": 0,
            "failure_reasons": ["未找到可点击的关注按钮（已轮询等待 15 秒）"],
        }
    )
    assert "未启动 0，失败原因：" in line
    assert "未找到可点击的关注按钮" in line
    skipped = h5_chat_channel._scheduled_douyin_precise_touch_detail_line(
        {
            "label": "关注并评论",
            "selected": 1,
            "processed": 1,
            "success": 0,
            "failed": 0,
            "skipped": 1,
            "not_started": 0,
            "failure_reasons": ["用户不存在"],
        }
    )
    assert "失败原因" not in skipped
