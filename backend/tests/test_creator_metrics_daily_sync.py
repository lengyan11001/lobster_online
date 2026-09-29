"""发布数据（播放量）每日 02:00 同步 + 上报云端 的回归测试。

需求（2026-09-16 用户）：个人发布中心里抖音、视频号要能定期把素材的播放量拿回来并上报服务器；
每天凌晨两点同步一次即可；朋友圈（朋友圈视频）本轮不做。

覆盖点：
1. 参与同步的平台只有抖音 + 视频号（朋友圈不在其中），且视频号已进入作品同步白名单；
2. 下一次执行时间落在北京时间 02:00-06:00 错峰窗口内：同一台机器固定某一分钟（不挤在同一时刻），
   过一次就顺延到第二天，窗口可用环境变量覆盖；
3. 视频号作品列表接口 JSON 的归一（播放/点赞/评论/转发/收藏）；登录拦截时不空转；
4. 本地采样表按「北京自然日 + 作品」幂等：同日重跑只更新数值并重新排队上报；
5. 上报失败指数退避重试，成功才标记 uploaded；未登录/未配置服务器时保留 pending；
6. 每日一轮只处理抖音 + 视频号账号（小红书等不参与），并在汇总里给出视频号账号数据。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.api import creator_content as creator_api
from backend.app.models import CreatorMetricSample, PublishAccount
from backend.app.services import creator_metrics_collect as collect_mod
from backend.app.services import creator_metrics_daily_runner as runner
from backend.app.services import wechat_channels_creator_sync as channels_sync

BEIJING = timezone(timedelta(hours=8))


# ── 通用替身 ──────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, url: str, status: int, payload: Any, text: str = "") -> None:
        self.url = url
        self.status = status
        self._payload = payload
        self.text = text

    async def json(self) -> Any:
        return self._payload


class _FakeMouse:
    def __init__(self) -> None:
        self.wheels: List[tuple] = []

    async def wheel(self, x: int, y: int) -> None:
        self.wheels.append((x, y))


class _FakeChannelsPage:
    """只实现采集用到的四个能力：url / evaluate / on / wait_for_timeout。"""

    def __init__(self, *, url: str, text: str, payloads: Optional[List[Dict[str, Any]]] = None) -> None:
        self.url = url
        self._text = text
        self._payloads = list(payloads or [])
        self._delivered = False
        self.handlers: List[tuple] = []
        self.mouse = _FakeMouse()
        self.waits = 0

    def on(self, event: str, handler: Any) -> None:
        self.handlers.append((event, handler))

    async def evaluate(self, _script: str) -> str:
        return self._text

    async def wait_for_timeout(self, _ms: int) -> None:
        self.waits += 1
        if self._delivered:
            return
        self._delivered = True
        for payload in self._payloads:
            await self._deliver(
                _FakeResponse(
                    "https://channels.weixin.qq.com/cgi-bin/mmfinderassistant-bin/post/post_list?x=1",
                    200,
                    payload,
                )
            )

    async def _deliver(self, response: _FakeResponse) -> None:
        for _event, handler in self.handlers:
            await handler(response)


class _UploadResp:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    PublishAccount.__table__.create(engine)
    CreatorMetricSample.__table__.create(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


CHANNELS_RAW_ITEM = {
    "object_id": "ch_1001",
    "title": "视频号作品A",
    "create_time": 1757894400,
    "read_count": 12345,
    "like_count": 321,
    "comment_count": 12,
    "forward_count": 45,
    "fav_count": 6,
    "cover_url": "https://example.test/cover.jpg",
}

DOUYIN_ITEM = {
    "id": "dy_2001",
    "title": "抖音作品B",
    "create_time": 1757894400,
    "metrics": {
        "view_count": 999,
        "like_count": 88,
        "comment_count": 7,
        "share_count": 5,
        "collect_count": 4,
    },
}


# ── 1. 平台口径 ───────────────────────────────────────────────────────────


def test_metric_platforms_are_douyin_and_channels_only():
    assert collect_mod.METRIC_PLATFORMS == ("douyin", "wechat_channels")
    assert "moments" not in collect_mod.METRIC_PLATFORMS
    assert "wechat_channels" in creator_api.SYNC_PLATFORMS
    assert creator_api.SYNC_PLATFORMS >= {"douyin", "wechat_channels"}
    assert creator_api._PLATFORM_LABEL["wechat_channels"] == "视频号"
    assert collect_mod.PLATFORM_LABEL["wechat_channels"] == "视频号"


# ── 2. 每天凌晨 02:00-06:00 错峰（打散，不再固定 2 点） ────────────────────


def _minute_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def test_next_daily_run_at_spreads_inside_window_and_rolls_over():
    window = runner.daily_window()
    assert window == (120, 360), "默认错峰窗口 = 北京 02:00-06:00"

    before = datetime(2026, 9, 16, 1, 0, tzinfo=BEIJING)
    target = runner.next_daily_run_at(before, seed="install:abc", jitter_value=0)
    assert target.date().isoformat() == "2026-09-16"
    assert window[0] <= _minute_of_day(target) < window[1]
    assert target.second == 0 and target.microsecond == 0

    # 同一台机器同一天固定：换个时钟点也落在同一分钟
    later = datetime(2026, 9, 16, 1, 30, tzinfo=BEIJING)
    assert runner.next_daily_run_at(later, seed="install:abc", jitter_value=0) == target

    # 过了本机时段 → 顺延到第二天同一时段
    after = target + timedelta(minutes=1)
    nxt = runner.next_daily_run_at(after, seed="install:abc", jitter_value=0)
    assert nxt == target + timedelta(days=1)

    # UTC 输入也按北京时间判断窗口
    utc_now = datetime(2026, 9, 15, 19, 30, tzinfo=timezone.utc)  # 北京 09-16 03:30
    utc_target = runner.next_daily_run_at(utc_now, seed="install:abc", jitter_value=0)
    assert window[0] <= _minute_of_day(utc_target) < window[1]

    # 每日微抖仍留在窗口内（第 2 个 05:59 之后的抖动会跨到 06:00 之后，故这里放宽到窗口尾部）
    jittered = runner.next_daily_run_at(before, seed="install:abc", jitter_minutes=5)
    again = runner.next_daily_run_at(before, seed="install:abc", jitter_minutes=5)
    assert jittered == again, "同一天同一台机器必须可复现"
    assert window[0] <= _minute_of_day(jittered) <= window[1] + 5


def test_clients_are_spread_across_the_window_not_all_at_2am():
    window = runner.daily_window()
    slots = [
        runner.stable_slot_minute(f"install:u22-{i:08x}") for i in range(2000)
    ]
    assert all(window[0] <= s < window[1] for s in slots), "所有机器都落进 02:00-06:00"
    assert len(set(slots)) > 200, "2000 台机器应铺满窗口内绝大多数分钟"
    from collections import Counter

    counted = Counter(slots)
    busiest = counted.most_common(1)[0][1]
    assert busiest / len(slots) < 0.02, f"最挤的分钟只占 {busiest / len(slots):.1%}，不会集中"
    assert 120 <= min(slots) and max(slots) < 360
    # 同一个 installation 永远同一分钟（可预期、可对时间）
    assert runner.stable_slot_minute("install:u22-1") == runner.stable_slot_minute("install:u22-1")


def test_window_can_be_overridden_by_env(monkeypatch):
    monkeypatch.setenv("LOBSTER_PUBLISH_METRICS_WINDOW", "01:30-03:00")
    window = runner.daily_window()
    assert window == (90, 180)
    slot = runner.stable_slot_minute("install:x", window=window)
    assert 90 <= slot < 180

    # 非法值回退到默认窗口
    monkeypatch.setenv("LOBSTER_PUBLISH_METRICS_WINDOW", "6-2")
    assert runner.daily_window() == (runner.DAILY_WINDOW_START_MINUTE, runner.DAILY_WINDOW_END_MINUTE)


# ── 3. 视频号作品列表归一 + 登录拦截 ──────────────────────────────────────


def test_parse_channels_post_list_json_normalizes_counts():
    payload = {"errCode": 0, "data": {"object": {"list": [CHANNELS_RAW_ITEM], "last_buffer": "abc"}}}
    err, items, has_more = collect_mod.parse_channels_post_list_json(payload)
    assert err is None and has_more is True
    assert len(items) == 1
    item = items[0]
    assert item["id"] == "ch_1001"
    assert item["metrics"]["view_count"] == 12345
    assert item["metrics"]["like_count"] == 321
    assert item["metrics"]["comment_count"] == 12
    assert item["metrics"]["share_count"] == 45
    assert item["metrics"]["collect_count"] == 6

    bad_err, bad_items, _ = collect_mod.parse_channels_post_list_json({"errCode": -1, "errMsg": "未登录"})
    assert bad_err and "未登录" in bad_err and bad_items == []


@pytest.mark.asyncio
async def test_collect_channels_posts_reads_works_list_xhr():
    page = _FakeChannelsPage(
        url="https://channels.weixin.qq.com/platform/post/list",
        text="视频号助手 内容管理 数据中心 发表动态",
        payloads=[{"errCode": 0, "data": {"object": {"list": [CHANNELS_RAW_ITEM]}}}],
    )
    result = await channels_sync.collect_channels_posts(page)
    assert result["ok"] is True
    assert result["error"] is None
    assert [i["id"] for i in result["items"]] == ["ch_1001"]
    assert result["meta"]["need_relogin"] is False
    assert result["meta"]["logged_in"] is True
    assert result["meta"]["source"] == "wechat_channels_post_list"


@pytest.mark.asyncio
async def test_collect_channels_posts_reports_login_wall_without_retry():
    page = _FakeChannelsPage(
        url="https://channels.weixin.qq.com/login.html?redirect=/platform/post/list",
        text="请使用微信扫码登录",
    )
    result = await channels_sync.collect_channels_posts(page)
    assert result["ok"] is False
    assert result["items"] == []
    assert result["meta"]["need_relogin"] is True
    assert "重新登录" in str(result["error"])


def test_is_channels_post_list_response_filters_other_traffic():
    hit = _FakeResponse(
        "https://channels.weixin.qq.com/cgi-bin/mmfinderassistant-bin/post/post_list", 200, {}
    )
    miss_status = _FakeResponse(
        "https://channels.weixin.qq.com/cgi-bin/mmfinderassistant-bin/post/post_list", 302, {}
    )
    miss_url = _FakeResponse("https://channels.weixin.qq.com/cgi-bin/mmfinderassistant-bin/user", 200, {})
    assert channels_sync.is_channels_post_list_response(hit) is True
    assert channels_sync.is_channels_post_list_response(miss_status) is False
    assert channels_sync.is_channels_post_list_response(miss_url) is False


# ── 4. 本地采样：北京自然日幂等 ───────────────────────────────────────────


def test_build_sample_rows_uses_beijing_day_and_dedupes_items():
    account = PublishAccount(id=9527, user_id=54, platform="wechat_channels", nickname="诺诺老师")
    sampled_at = datetime(2026, 9, 15, 18, 30)  # 北京 2026-09-16 02:30
    items = [DOUYIN_ITEM, dict(DOUYIN_ITEM), {"id": "", "metrics": {"view_count": 5}}]
    rows = runner.build_sample_rows(
        user_id=54,
        account=account,
        platform="douyin",
        items=items,
        installation_id="inst-abc",
        sampled_at=sampled_at,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["sampled_day"] == "2026-09-16"
    assert row["views"] == 999 and row["likes"] == 88 and row["favorites"] == 4
    assert row["account_id"] == 9527 and row["account_nickname"] == "诺诺老师"
    assert row["sample_key"] == collect_mod.sample_key(
        installation_id="inst-abc", platform="douyin", item_id="dy_2001", sampled_day="2026-09-16"
    )

    # 换一天 → 不是同一行（每天各留一条轨迹）
    tomorrow = runner.build_sample_rows(
        user_id=54,
        account=account,
        platform="douyin",
        items=[DOUYIN_ITEM],
        installation_id="inst-abc",
        sampled_at=sampled_at + timedelta(days=1),
    )
    assert tomorrow[0]["sampled_day"] == "2026-09-17"
    assert tomorrow[0]["sample_key"] != row["sample_key"]


def test_upsert_metric_samples_is_idempotent_and_requeues_on_change(session):
    account = PublishAccount(id=7, user_id=54, platform="douyin", nickname="阿迪老师")
    rows = runner.build_sample_rows(
        user_id=54,
        account=account,
        platform="douyin",
        items=[DOUYIN_ITEM],
        installation_id="inst-abc",
        sampled_at=datetime(2026, 9, 15, 18, 30),
    )
    first = runner.upsert_metric_samples(session, rows)
    assert first == {"created": 1, "updated": 0}
    stored = session.query(CreatorMetricSample).one()
    stored.uploaded_at = datetime.utcnow()
    session.commit()

    same = runner.upsert_metric_samples(session, rows)
    assert same == {"created": 0, "updated": 0}
    assert session.query(CreatorMetricSample).count() == 1

    changed_item = dict(DOUYIN_ITEM)
    changed_item["metrics"] = dict(DOUYIN_ITEM["metrics"], view_count=1500)
    changed = runner.upsert_metric_samples(
        session,
        runner.build_sample_rows(
            user_id=54,
            account=account,
            platform="douyin",
            items=[changed_item],
            installation_id="inst-abc",
            sampled_at=datetime(2026, 9, 15, 20, 0),
        ),
    )
    assert changed == {"created": 0, "updated": 1}
    again = session.query(CreatorMetricSample).one()
    assert again.views == 1500
    assert again.uploaded_at is None, "数值变化后必须重新排队上报"


# ── 5. 上报：重试 + 未登录跳过 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_pending_samples_retries_then_marks_uploaded(session, monkeypatch):
    account = PublishAccount(id=9, user_id=54, platform="wechat_channels", nickname="诺诺老师")
    runner.upsert_metric_samples(
        session,
        runner.build_sample_rows(
            user_id=54,
            account=account,
            platform="wechat_channels",
            items=[
                {
                    "id": "ch_1001",
                    "title": "视频号作品A",
                    "metrics": {"view_count": 12345, "like_count": 321},
                }
            ],
            installation_id="inst-abc",
            sampled_at=datetime(2026, 9, 15, 18, 30),
        ),
    )
    monkeypatch.setattr(runner.settings, "auth_server_base", "https://cloud.test")
    monkeypatch.setattr(runner, "read_login_context", lambda: ("jwt-token", "inst-abc"))

    calls: List[Dict[str, Any]] = []
    sleeps: List[float] = []

    async def post_fn(url, payload, headers):
        calls.append({"url": url, "payload": payload, "headers": headers})
        if len(calls) < 3:
            return _UploadResp(502, "bad gateway")
        return _UploadResp(200, "ok")

    async def sleep_fn(seconds):
        sleeps.append(seconds)

    result = await runner.upload_pending_metric_samples(
        session, installation_id="inst-abc", user_id=54, post_fn=post_fn, sleep_fn=sleep_fn
    )
    assert result["uploaded"] == 1
    assert result["failed"] == 0
    assert result["pending"] == 0
    assert len(calls) == 3
    assert sleeps == [5.0, 30.0]
    assert calls[0]["url"] == "https://cloud.test/api/publish/metrics:batch"
    assert calls[0]["headers"]["Authorization"] == "Bearer jwt-token"
    assert calls[0]["headers"]["X-Installation-Id"] == "inst-abc"
    payload = calls[0]["payload"]
    assert payload["installation_id"] == "inst-abc" and payload["user_id"] == 54
    sample = payload["samples"][0]
    assert sample["platform"] == "wechat_channels"
    assert sample["item_id"] == "ch_1001"
    assert sample["views"] == 12345
    assert sample["sampled_day"] == "2026-09-16"
    assert sample["account_nickname"] == "诺诺老师"

    row = session.query(CreatorMetricSample).one()
    assert row.uploaded_at is not None
    assert row.upload_attempts == 3
    assert row.last_upload_error is None


@pytest.mark.asyncio
async def test_upload_pending_samples_keeps_pending_when_server_fails(session, monkeypatch):
    account = PublishAccount(id=11, user_id=54, platform="douyin", nickname="阿迪老师")
    runner.upsert_metric_samples(
        session,
        runner.build_sample_rows(
            user_id=54,
            account=account,
            platform="douyin",
            items=[DOUYIN_ITEM],
            installation_id="inst-abc",
            sampled_at=datetime(2026, 9, 15, 18, 30),
        ),
    )
    monkeypatch.setattr(runner.settings, "auth_server_base", "https://cloud.test")
    monkeypatch.setattr(runner, "read_login_context", lambda: ("jwt-token", "inst-abc"))

    async def post_fn(url, payload, headers):
        return _UploadResp(404, "not found")

    async def sleep_fn(seconds):
        return None

    result = await runner.upload_pending_metric_samples(
        session, installation_id="inst-abc", user_id=54, post_fn=post_fn, sleep_fn=sleep_fn
    )
    assert result["uploaded"] == 0 and result["failed"] == 1
    assert result["pending"] == 1
    row = session.query(CreatorMetricSample).one()
    assert row.uploaded_at is None
    assert "404" in str(row.last_upload_error)
    assert row.upload_attempts == 3


@pytest.mark.asyncio
async def test_upload_pending_samples_skips_without_login(session, monkeypatch):
    account = PublishAccount(id=12, user_id=54, platform="douyin", nickname="阿迪老师")
    runner.upsert_metric_samples(
        session,
        runner.build_sample_rows(
            user_id=54,
            account=account,
            platform="douyin",
            items=[DOUYIN_ITEM],
            installation_id="inst-abc",
            sampled_at=datetime(2026, 9, 15, 18, 30),
        ),
    )
    monkeypatch.setattr(runner.settings, "auth_server_base", "")
    monkeypatch.setattr(runner, "read_login_context", lambda: ("", ""))

    async def post_fn(url, payload, headers):  # 不应被调用
        raise AssertionError("未登录时不应发起上报")

    result = await runner.upload_pending_metric_samples(
        session, installation_id="inst-abc", post_fn=post_fn
    )
    assert result["ok"] is False
    assert result["skipped"]
    assert result["pending"] == 1


# ── 6. 每日一轮：只做抖音 + 视频号 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_daily_metrics_cycle_covers_douyin_and_channels_only(session, monkeypatch):
    session.add_all(
        [
            PublishAccount(id=1, user_id=54, platform="douyin", nickname="阿迪老师", status="active"),
            PublishAccount(id=2, user_id=54, platform="wechat_channels", nickname="诺诺老师", status="active"),
            PublishAccount(id=3, user_id=54, platform="xiaohongshu", nickname="小红书号", status="active"),
        ]
    )
    session.commit()
    monkeypatch.setattr(runner.settings, "auth_server_base", "https://cloud.test")
    monkeypatch.setattr(runner, "read_login_context", lambda: ("jwt-token", "inst-abc"))

    synced: List[int] = []

    async def sync_fn(db, *, user_id, account_id):
        synced.append(account_id)
        if account_id == 1:
            return {"ok": True, "items": [DOUYIN_ITEM], "error": None, "meta": {}}
        return {
            "ok": True,
            "items": [
                {
                    "id": "ch_1001",
                    "title": "视频号作品A",
                    "metrics": {"view_count": 12345, "like_count": 321, "comment_count": 12},
                }
            ],
            "error": None,
            "meta": {"need_relogin": False},
        }

    sent: List[Dict[str, Any]] = []

    async def post_fn(url, payload, headers):
        sent.append(payload)
        return _UploadResp(200, "ok")

    async def sleep_fn(seconds):
        return None

    summary = await runner.run_daily_metrics_cycle(
        db=session,
        user_id=54,
        installation_id="inst-abc",
        sync_fn=sync_fn,
        post_fn=post_fn,
        sleep_fn=sleep_fn,
        sampled_at=datetime(2026, 9, 15, 18, 30),
    )
    assert synced == [1, 2], "只采集抖音 + 视频号账号"
    assert summary["account_count"] == 2
    assert summary["samples_created"] == 2
    assert summary["upload"]["uploaded"] == 2
    assert summary["platforms"] == ["douyin", "wechat_channels"]
    assert session.query(CreatorMetricSample).count() == 2
    assert all(r.uploaded_at is not None for r in session.query(CreatorMetricSample).all())
    platforms_sent = sorted({s["platform"] for payload in sent for s in payload["samples"]})
    assert platforms_sent == ["douyin", "wechat_channels"]


# ── 7. 个人发布中心读数：按平台/账号汇总 ──────────────────────────────────


def test_build_publish_metrics_payload_summarises_by_platform_and_account(session, monkeypatch):
    dy_account = PublishAccount(id=1, user_id=54, platform="douyin", nickname="阿迪老师")
    ch_account = PublishAccount(id=2, user_id=54, platform="wechat_channels", nickname="诺诺老师")
    now = datetime.utcnow()
    runner.upsert_metric_samples(
        session,
        runner.build_sample_rows(
            user_id=54,
            account=dy_account,
            platform="douyin",
            items=[DOUYIN_ITEM],
            installation_id="inst-abc",
            sampled_at=now,
        )
        + runner.build_sample_rows(
            user_id=54,
            account=ch_account,
            platform="wechat_channels",
            items=[
                {
                    "id": "ch_1001",
                    "title": "视频号作品A",
                    "metrics": {"view_count": 12345, "like_count": 321},
                }
            ],
            installation_id="inst-abc",
            sampled_at=now,
        ),
    )
    payload = creator_api.build_publish_metrics_payload(session, 54, days=30, limit=10)
    assert payload["ok"] is True
    assert payload["schedule"]["window"] == "02:00-06:00"
    assert payload["schedule"]["window_minutes"] == [120, 360]
    assert 120 <= payload["schedule"]["slot_minute"] < 360
    assert payload["schedule"]["timezone"] == "Asia/Shanghai"
    assert payload["schedule"]["skipped_platforms"] == ["moments"]
    by_platform = {p["platform"]: p for p in payload["platforms"]}
    assert set(by_platform) == {"douyin", "wechat_channels"}
    assert by_platform["douyin"]["views"] == 999
    assert by_platform["douyin"]["accounts"][0]["nickname"] == "阿迪老师"
    assert by_platform["wechat_channels"]["views"] == 12345
    assert by_platform["wechat_channels"]["platform_label"] == "视频号"
    assert by_platform["wechat_channels"]["items"][0]["item_id"] == "ch_1001"
    assert payload["upload_queue"]["pending"] == 2


def test_build_publish_metrics_payload_rejects_unknown_platform(session):
    with pytest.raises(Exception) as exc:
        creator_api.build_publish_metrics_payload(session, 54, platform="moments")
    assert "wechat_channels" in str(exc.value)
