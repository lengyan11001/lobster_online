"""视频号获客 API：本地微信 UI 驱动用 mock，不依赖真实微信。"""
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

channels_api = pytest.importorskip("backend.channels_origin.channels_api")
channels_ui = pytest.importorskip("backend.channels_origin.channels_ui")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from douyin_origin.state_store import RuntimeStateStore

    monkeypatch.setattr(channels_api, "store", RuntimeStateStore(tmp_path / "channels.db"))
    app = FastAPI()
    app.include_router(channels_api.router)
    return TestClient(app)


def _fake_search(keyword, **kwargs):
    return {
        "keyword": keyword,
        "count": 1,
        "doc": f"{keyword} - 搜一搜",
        "items": [
            {
                "key": "装修避坑|示例账号|00:17|昨天",
                "title": "装修避坑",
                "author": "示例账号",
                "date": "昨天",
                "duration": "00:17",
                "like": "224",
                "rect": [100, 100, 200, 300],
            }
        ],
    }


def _fake_comments(keyword, card_key, **kwargs):
    return {
        "count": 1,
        "items": [
            {"nickname": "小王", "content": "需要设计，加我微信 abc12345", "like": "6"},
        ],
        "detail": {"author": "示例账号", "评论": "78"},
    }


def test_extract_contacts():
    result = channels_ui.extract_contacts("装修设计：13240370368 微信：wscz3321 QQ：12345678")
    assert "13240370368" in result["phones"]
    assert "wscz3321" in result["wechats"]
    assert "12345678" in result["qqs"]


def test_search_task_pipeline(client, monkeypatch):
    monkeypatch.setattr(channels_api.channels_ui, "search_videos", _fake_search)
    monkeypatch.setattr(channels_api.channels_ui, "collect_comments_for_card", _fake_comments)

    search = client.post("/api/channels/search/videos", json={"keyword": "装修", "max_results": 5}).json()
    assert search["code"] == 200
    session = search["data"]["session"]
    assert len(session["items"]) == 1

    task = client.post(
        "/api/channels/tasks/from-search",
        json={"session_id": session["id"], "keys": ["装修避坑|示例账号|00:17|昨天"]},
    ).json()
    assert task["code"] == 200
    created = task["data"]["created"]
    assert len(created) == 1

    run = client.post(f"/api/channels/tasks/{created[0]['id']}/run", json={"max_comments": 5}).json()
    assert run["code"] == 200
    assert run["data"]["task"]["collected"] == 1

    pools = client.get("/api/channels/customer-pools").json()["data"]
    assert pools["all_count"] == 1
    assert pools["precise_count"] == 1
    assert pools["precise"][0]["nickname"] == "小王"


def test_private_message_is_explicitly_unsupported(client):
    data = client.get("/api/channels/inbox/status").json()["data"]
    assert data["supported"] is False
    resp = client.post("/api/channels/inbox/send", json={}).json()
    assert resp["code"] == 501
    assert resp["supported"] is False


def test_capabilities(client):
    data = client.get("/api/channels/capabilities").json()["data"]
    by_key = {row["key"]: row for row in data["items"]}
    assert by_key["video_search"]["supported"] is True
    assert by_key["video_comments"]["supported"] is True
    assert by_key["private_message"]["supported"] is False
