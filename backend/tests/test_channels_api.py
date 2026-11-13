"""视频号获客后端：搜索 → 详情 → 评论 → 客户池 → 能力边界。"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))
if str(ROOT / "backend" / "channels_origin") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend" / "channels_origin"))

channels_api = pytest.importorskip("channels_api")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from douyin_origin.state_store import RuntimeStateStore

    monkeypatch.setattr(channels_api, "store", RuntimeStateStore(tmp_path / "channels.db"))
    app = FastAPI()
    app.include_router(channels_api.router)
    return TestClient(app)


def _fake_search(*args, **kwargs):
    return {
        "keyword": "装修",
        "pages": 1,
        "items": [
            {
                "video_id": "",
                "export_id": "export/demo-1",
                "object_nonce_id": "13863805038164703408",
                "title": "装修避坑",
                "nickname": "示例账号",
                "cover": "",
                "duration": "00:17",
                "publish_time": 1794020101,
                "date_text": "6天前",
            }
        ],
    }


def _fake_detail(**kwargs):
    return {
        "id": "15049331375694421273",
        "username": "v2_demo@finder",
        "nickname": "示例账号",
        "title": "装修避坑",
        "like_count": 12702,
        "comment_count": 1476,
        "create_time": 1794020101,
    }


def _fake_comments(*args, **kwargs):
    return {
        "object_id": "15049331375694421273",
        "pages": 1,
        "comments": [
            {"comment_id": "1", "nickname": "小王", "username": "v2_wang@finder",
             "content": "需要设计，加我微信 abc12345", "like_count": 6, "create_time": 1794122234},
            {"comment_id": "2", "nickname": "路人", "username": "v5_lu@stranger",
             "content": "路过看看", "like_count": 0, "create_time": 1794122235},
        ],
    }


def test_capabilities_mark_unsupported_items(client):
    data = client.get("/api/channels/capabilities").json()["data"]
    by_key = {row["key"]: row for row in data["items"]}
    assert by_key["public_search"]["supported"] is True
    assert by_key["video_comments"]["supported"] is True
    assert by_key["cold_dm"]["supported"] is False
    assert by_key["group_members"]["supported"] is False


def test_search_then_collect_comments_builds_precise_customers(client, monkeypatch):
    monkeypatch.setattr(channels_api.tikhub_channels, "search_videos", _fake_search)
    monkeypatch.setattr(channels_api.tikhub_channels, "video_detail", _fake_detail)
    monkeypatch.setattr(channels_api.tikhub_channels, "video_comments", _fake_comments)

    search = client.post("/api/channels/search/collect", json={"keyword": "装修", "max_results": 5}).json()
    assert search["code"] == 200
    session = search["data"]["session"]
    assert len(session["items"]) == 1

    created = client.post(
        "/api/channels/tasks/from-search",
        json={"session_id": session["id"], "video_ids": ["export/demo-1"], "max_comments": 10},
    ).json()["data"]["created"]
    assert len(created) == 1

    run = client.post(f"/api/channels/tasks/{created[0]['id']}/run", json={}).json()
    assert run["code"] == 200
    assert run["data"]["task"]["collected"] == 2

    pools = client.get("/api/channels/customer-pools").json()["data"]
    assert pools["all_count"] == 2
    assert pools["precise_count"] == 1
    assert pools["precise"][0]["nickname"] == "小王"


def test_config_never_returns_raw_key(client):
    saved = client.post("/api/channels/config", json={"tikhub_api_key": "demo-secret-key-123456"}).json()
    assert saved["code"] == 200
    text = saved["data"]
    assert "tikhub_api_key" not in text
    assert text["tikhub_configured"] is True
    assert text["tikhub_key_masked"] != "demo-secret-key-123456"


def test_douyin_only_actions_report_unsupported(client):
    resp = client.post("/api/channels/group-members/start", json={}).json()
    assert resp["code"] == 501
    assert resp["supported"] is False
    status = client.get("/api/channels/follow-comment/status").json()["data"]
    assert status["supported"] is False
