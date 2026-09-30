"""同城爆款「生产记录」：本地 job 账本要能按 feature 过滤，摘要要带 feature/day。

背景（2026-09-30）：同城爆款界面看不到历史生产记录；每次「合成视频」其实都会在
_lobster_runtime/seedance_tvc_job_store.json 里留一条（meta.feature=local_bestseller，
meta.day=当天），只是没有入口。这里锁死这条链路的后端契约。
"""
from __future__ import annotations

from pathlib import Path

from backend.app.api.comfly_seedance_tvc import _recent_job_summary
from backend.app.services import comfly_seedance_tvc_job_store as store

ROOT = Path(__file__).resolve().parents[2]


def _make(user_id: int, meta: dict, tmp_path: Path) -> str:
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
        local_bestseller = _make(
            31,
            {"feature": "local_bestseller", "day": 3, "title": "同城爆款 Day 3 视频"},
            tmp_path,
        )
        other_feature = _make(31, {"feature": "seedance_tvc", "title": "创意视频任务"}, tmp_path)
        other_user = _make(99, {"feature": "local_bestseller", "day": 5}, tmp_path)

        rows = store.list_jobs_for_user(31, limit=10, feature="local_bestseller")
        assert [row["job_id"] for row in rows] == [local_bestseller]

        assert {row["job_id"] for row in store.list_jobs_for_user(31, limit=10)} == {
            local_bestseller,
            other_feature,
        }
        assert {row["job_id"] for row in store.list_jobs_for_user(31, limit=10, feature="seedance_tvc")} == {
            other_feature
        }

        summary = _recent_job_summary(store.get_job(local_bestseller))
        assert summary["feature"] == "local_bestseller"
        assert summary["day"] == 3
        assert summary["title"] == "同城爆款 Day 3 视频"
        assert store.get_job(other_user) is not None

        # 老记录没有 meta.title 时，同城爆款按 Day 兜底出标题
        legacy = _make(31, {"feature": "local_bestseller", "day": 1}, tmp_path)
        assert _recent_job_summary(store.get_job(legacy))["title"] == "同城爆款 Day 1 视频"
    finally:
        store._JOBS.clear()


def test_local_bestseller_view_has_records_entry():
    js = (ROOT / "static/js/local-bestseller.js").read_text(encoding="utf-8")
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")

    # 入口按钮 + 弹窗（生产记录不常驻在结果区）
    assert 'id="localBestsellerRecordsBtn"' in html
    assert 'id="localBestsellerRecordsModal"' in html
    assert 'id="localBestsellerRecordsList"' in html
    assert 'id="localBestsellerRecordsClose"' in html
    assert js.count("function openRecords(") == 1
    assert js.count("function closeRecords(") == 1
    assert "toggleRecords" not in js
    assert "&feature=local_bestseller" in js
    assert "data-lb-record-refresh" in js
    assert "data-lb-record-day" in js
    assert "function refreshRecordsIfOpen(" in js


def test_seedance_tvc_policy_fetch_passes_feature():
    """同城爆款要拿到自己的渠道策略（OpenMind 160），必须把 feature 传到策略接口。"""
    src = (ROOT / "backend/app/api/comfly_seedance_tvc.py").read_text(encoding="utf-8")
    assert '"feature": (feature or "").strip() or "seedance_tvc",' in src
    assert "feature=feature," in src
    assert 'feature = str((meta or {}).get("feature") or "").strip()' in src


def test_local_bestseller_batch_buttons_give_feedback_without_plan():
    """顶部批量按钮不能点了没反应：缺方案时给提示，只有提交中才禁用。"""
    js = (ROOT / "static/js/local-bestseller.js").read_text(encoding="utf-8")

    assert "再批量上传底图" in js
    assert "batchSceneTopBtn.disabled = state.submitting;" in js
    assert "batchVideoTopBtn.disabled = state.submitting;" in js
    assert "batchSceneTopBtn.disabled = state.submitting || !hasPlan;" not in js
    assert "batchVideoTopBtn.disabled = state.submitting || !hasPlan || !hasSceneImage;" not in js


def test_records_endpoint_filters_local_bestseller_over_http(tmp_path, monkeypatch):
    """真实走一遍 HTTP：GET .../pipeline/jobs?feature=local_bestseller 只回同城爆款任务。"""
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import comfly_seedance_tvc as mod

    monkeypatch.setattr(store, "_JOB_STORE_FILE", tmp_path / "seedance_jobs.json")
    store._JOBS.clear()
    try:
        wanted = _make(31, {"feature": "local_bestseller", "day": 2, "title": "同城爆款 Day 2 视频"}, tmp_path)
        _make(31, {"feature": "seedance_tvc", "title": "创意视频任务"}, tmp_path)

        app = FastAPI()
        app.include_router(mod.router)
        app.dependency_overrides[mod.get_current_user_media_edit] = lambda: SimpleNamespace(id=31)
        client = TestClient(app)

        resp = client.get("/api/comfly-seedance-tvc/pipeline/jobs", params={"limit": 10, "feature": "local_bestseller"})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert [item["job_id"] for item in items] == [wanted]
        assert items[0]["feature"] == "local_bestseller"
        assert items[0]["day"] == 2

        all_items = client.get("/api/comfly-seedance-tvc/pipeline/jobs", params={"limit": 10}).json()["items"]
        assert len(all_items) == 2
    finally:
        store._JOBS.clear()
