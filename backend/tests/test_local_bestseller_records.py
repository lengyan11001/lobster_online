"""同城爆款「方案记录」：每次生成方案一条，方案内容（含生成出来的场景图/视频）原样保存并回填。

背景（2026-09-30）：原先把「视频任务账本」当记录列表，用户看不懂（同一天多次生产看起来一样、
单独生成的场景图根本不在里面）。用户口径：弹窗选的是「方案」——每次生成方案一条记录，
方案里不管用户生成了什么，点开原样返回界面。

这里同时锁住两块：
1) 视频任务账本（job store）仍然按 feature 过滤，供内部查询；
2) 方案记录（plan store）：create/update/list/get/delete + HTTP 接口 + 前端接线。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import local_bestseller as lb
from backend.app.api.comfly_seedance_tvc import _recent_job_summary
from backend.app.services import comfly_seedance_tvc_job_store as store
from backend.app.services import local_bestseller_plan_store as plan_store

ROOT = Path(__file__).resolve().parents[2]


def _card(day: int, **extra) -> dict:
    card = {
        "id": str(day),
        "day": day,
        "title": f"Day {day}",
        "subtitle_text": f"字幕{day}",
        "video_prompt": "提示词",
    }
    card.update(extra)
    return card


@pytest.fixture()
def plan_file(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_store, "_PLANS_FILE", tmp_path / "local_bestseller_plans.json")
    plan_store._PLANS.clear()
    yield plan_store
    plan_store._PLANS.clear()


def _plan_client() -> TestClient:
    app = FastAPI()
    app.include_router(lb.router)
    app.dependency_overrides[lb.get_current_user_for_local] = lambda: SimpleNamespace(id=31)
    return TestClient(app)


# ---------- 1) 视频任务账本（保留） ----------

def _make_job(user_id: int, meta: dict, tmp_path: Path) -> str:
    return store.create_job_record(
        user_id=user_id,
        inp={"task_text": "同城爆款测试"},
        auto_save=True,
        job_output_dir=str(tmp_path),
        meta=meta,
    )


def test_jobs_filter_by_feature_and_summary_exposes_day(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_JOB_STORE_FILE", tmp_path / "seedance_jobs.json")
    store._JOBS.clear()
    try:
        local_bestseller = _make_job(
            31,
            {"feature": "local_bestseller", "day": 3, "title": "同城爆款 Day 3 视频"},
            tmp_path,
        )
        other_feature = _make_job(31, {"feature": "seedance_tvc", "title": "创意视频任务"}, tmp_path)

        rows = store.list_jobs_for_user(31, limit=10, feature="local_bestseller")
        assert [row["job_id"] for row in rows] == [local_bestseller]

        summary = _recent_job_summary(store.get_job(local_bestseller))
        assert summary["feature"] == "local_bestseller"
        assert summary["day"] == 3
        assert summary["title"] == "同城爆款 Day 3 视频"
        assert store.get_job(other_feature) is not None
    finally:
        store._JOBS.clear()


# ---------- 2) 方案记录：账本 ----------

def test_create_update_list_get_delete(plan_file):
    plan_id = plan_file.create_plan(
        user_id=31, items=[_card(1), _card(2)], profile={"name": "艾蜜"}, day_start=1, day_end=2
    )

    rows = plan_file.list_plans(31)
    assert [row["plan_id"] for row in rows] == [plan_id]
    assert rows[0]["card_count"] == 2
    assert rows[0]["title"] == "2天方案（Day 1-2）"

    # 方案里生成出来的东西（场景图/视频）随操作写回，原样保存
    assert (
        plan_file.update_plan(
            plan_id,
            user_id=31,
            items=[
                _card(1, image_url="https://x/1.png"),
                _card(2, video_url="https://x/2.mp4", video_status="completed"),
            ],
        )
        is True
    )
    detail = plan_file.get_plan(plan_id, user_id=31)
    assert detail["items"][0]["image_url"] == "https://x/1.png"
    assert detail["items"][1]["video_url"] == "https://x/2.mp4"

    summary = plan_file.list_plans(31)[0]
    assert summary["scene_ready"] == 1
    assert summary["video_ready"] == 1

    assert plan_file.delete_plan(plan_id, user_id=31) is True
    assert plan_file.get_plan(plan_id, user_id=31) is None
    assert plan_file.delete_plan(plan_id, user_id=31) is False


def test_plans_are_scoped_to_owner_and_keep_raw_cards(plan_file):
    mine = plan_file.create_plan(
        user_id=31,
        items=[_card(3, subtitle_text="原样字幕", bgm={"key": "day-3"})],
        day_start=3,
        day_end=3,
    )
    plan_file.create_plan(user_id=99, items=[_card(1)], day_start=1, day_end=1)

    assert [row["plan_id"] for row in plan_file.list_plans(31)] == [mine]
    assert plan_file.update_plan(mine, user_id=99, items=[_card(9)]) is False
    assert plan_file.get_plan(mine, user_id=99) is None

    detail = plan_file.get_plan(mine, user_id=31)
    assert detail["items"][0]["subtitle_text"] == "原样字幕"
    assert detail["items"][0]["bgm"] == {"key": "day-3"}


def test_new_plan_is_a_new_record(plan_file):
    first = plan_file.create_plan(user_id=31, items=[_card(1)], day_start=1, day_end=1)
    second = plan_file.create_plan(user_id=31, items=[_card(1), _card(2)], day_start=1, day_end=2)
    assert first != second
    assert len(plan_file.list_plans(31)) == 2


# ---------- 3) 方案记录：HTTP 接口 ----------

def test_plan_record_http_endpoints(plan_file):
    client = _plan_client()

    saved = client.post(
        "/api/local-bestseller/plans",
        json={"items": [_card(1), _card(2, image_url="https://x/2.png")], "days": 2, "profile": {"name": "艾蜜"}},
    )
    assert saved.status_code == 200
    plan_id = saved.json()["plan_id"]

    listed = client.get("/api/local-bestseller/plans", params={"limit": 10})
    assert listed.status_code == 200
    rows = listed.json()["items"]
    assert [row["plan_id"] for row in rows] == [plan_id]
    assert rows[0]["scene_ready"] == 1
    assert rows[0]["card_count"] == 2

    detail = client.get(f"/api/local-bestseller/plans/{plan_id}")
    assert detail.status_code == 200
    assert len(detail.json()["plan"]["items"]) == 2
    assert detail.json()["plan"]["items"][0]["subtitle_text"] == "字幕1"

    updated = client.post(
        "/api/local-bestseller/plans",
        json={"plan_id": plan_id, "items": [_card(1, video_url="https://x/1.mp4")], "days": 1},
    )
    assert updated.status_code == 200
    assert updated.json().get("updated") is True

    assert client.delete(f"/api/local-bestseller/plans/{plan_id}").status_code == 200
    assert client.get(f"/api/local-bestseller/plans/{plan_id}").status_code == 404


def test_plan_save_rejects_empty_items(plan_file):
    client = _plan_client()
    assert client.post("/api/local-bestseller/plans", json={"items": []}).status_code == 400


# ---------- 4) 前端接线 ----------

def test_view_uses_plan_records_and_snapshots_plan():
    js = (ROOT / "static/js/local-bestseller.js").read_text(encoding="utf-8")
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")

    assert 'id="localBestsellerRecordsBtn"' in html
    assert "方案记录" in html
    assert 'id="localBestsellerRecordsModal"' in html
    assert 'id="localBestsellerRecordsList"' in html
    assert 'id="localBestsellerRecordsClose"' in html

    # 弹窗列方案、点一条原样回填
    assert "local-bestseller/plans?limit=" in js
    assert js.count("function restorePlanRecord(") == 1
    assert "state.plan = normalizePlan(items);" in js
    assert "data-lb-record-plan=" in js
    assert "data-lb-record-delete=" in js

    # 每次「生成方案」各一条 + 卡片变化自动写快照
    assert "state.planId = '';" in js
    assert "schedulePlanSave();" in js
    assert js.count("function savePlanSnapshot(") == 1
    assert "local-bestseller/plans'," in js

    # 旧的「视频任务当记录」那套已经撤掉
    assert "data-lb-record-restore" not in js
    assert "feature=local_bestseller" not in js


def test_local_bestseller_batch_buttons_give_feedback_without_plan():
    """顶部批量按钮不能点了没反应：缺方案时给提示，只有提交中才禁用。"""
    js = (ROOT / "static/js/local-bestseller.js").read_text(encoding="utf-8")

    assert "再批量上传底图" in js
    assert "batchSceneTopBtn.disabled = state.submitting;" in js
    assert "batchVideoTopBtn.disabled = state.submitting;" in js
    assert "batchSceneTopBtn.disabled = state.submitting || !hasPlan;" not in js
    assert "batchVideoTopBtn.disabled = state.submitting || !hasPlan || !hasSceneImage;" not in js


def test_local_bestseller_video_fallbacks_have_no_yunwu():
    """同城爆款兜底链不许再出现云雾(yunwu)：openmind 主通道 + 我们自己的 comfly/xai。"""
    src = (ROOT / "backend/app/api/local_bestseller.py").read_text(encoding="utf-8")
    block = src.split("def _seedance_grok_video_fallbacks()", 1)[1].split("async def _submit_card_video_via_seedance", 1)[0]
    assert "yunwu" not in block
    assert '"channel": "comfly"' in block
    assert block.index('"channel": "comfly"') < block.index('"channel": "xai"')


def test_seedance_tvc_policy_fetch_passes_feature():
    """同城爆款要拿到自己的渠道策略（OpenMind 160），必须把 feature 传到策略接口。"""
    src = (ROOT / "backend/app/api/comfly_seedance_tvc.py").read_text(encoding="utf-8")
    assert '"feature": (feature or "").strip() or "seedance_tvc",' in src
    assert "feature=feature," in src
    assert 'feature = str((meta or {}).get("feature") or "").strip()' in src
