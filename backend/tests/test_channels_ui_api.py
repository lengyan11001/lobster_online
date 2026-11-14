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


def _fake_ai(request, title, comments, prompt, strategy="prompt", model=""):
    return {
        "ok": True,
        "error": "",
        "items": [
            {
                "comment_id": "小王|需要设计，加我微信 abc12345",
                "nickname": "小王",
                "content": "需要设计，加我微信 abc12345",
                "reason": "明确设计需求并留下联系方式",
                "score": 0.96,
                "intent_level": "high",
                "is_high_intent": True,
            }
        ],
    }


def test_search_task_pipeline(client, monkeypatch):
    monkeypatch.setattr(channels_api.channels_ui, "search_videos", _fake_search)
    monkeypatch.setattr(channels_api.channels_ui, "collect_comments_for_card", _fake_comments)
    monkeypatch.setattr(channels_api, "ai_filter_comments", _fake_ai)

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


def test_parse_like_count():
    assert channels_api.parse_like_count("1.9万") == 19000
    assert channels_api.parse_like_count("4429") == 4429
    assert channels_api.parse_like_count("10万+") == 100000
    assert channels_api.parse_like_count("") == 0


def test_mark_video_candidates_filters_and_dedupes():
    items = [
        {"key": "a", "like": "100"},
        {"key": "b", "like": "1000"},
        {"key": "c", "like": "1.9万"},
    ]
    result = channels_api.mark_video_candidates(
        items,
        {"min_likes": 500, "avg_multiplier": 1.5, "exclude_historical_duplicates": True},
        {"c"},
    )
    by_key = {row["key"]: row for row in result}
    assert by_key["a"]["criteria_matched"] is False
    assert by_key["b"]["criteria_matched"] is True
    assert by_key["c"]["is_historical_duplicate"] is True
    assert by_key["c"]["export_selected"] is False


def test_open_channels_reuses_existing_window(monkeypatch):
    monkeypatch.setattr(channels_ui, "channels_hwnd", lambda **kwargs: 4242)
    monkeypatch.setattr(channels_ui, "ensure_feed_tab", lambda hwnd=None: True)
    monkeypatch.setattr(channels_ui, "_activate_hwnd", lambda hwnd: True)
    monkeypatch.setattr(channels_ui, "active_doc_name", lambda hwnd=None: "视频号")

    def _boom():
        raise AssertionError("已有视频号窗口时不应再点微信侧栏")

    monkeypatch.setattr(channels_ui, "main_window", _boom)
    result = channels_ui.open_channels()
    assert result["ok"] is True
    assert result["reused"] is True
    assert result["hwnd"] == 4242


def test_close_extra_channels_windows_keeps_keep(monkeypatch):
    posted = []
    monkeypatch.setattr(channels_ui, "_wechat_appex_windows", lambda: [1, 2, 3])
    monkeypatch.setattr(channels_ui, "_is_channels_window", lambda win: True)
    monkeypatch.setattr(channels_ui.win32gui, "PostMessage", lambda win, msg, wp, lp: posted.append(win))
    monkeypatch.setattr(channels_ui.time, "sleep", lambda *_: None)
    assert channels_ui.close_extra_channels_windows(keep_hwnd=1) == 2
    assert posted == [2, 3]


def test_is_channels_window_skips_mini_program(monkeypatch):
    monkeypatch.setattr(channels_ui.win32gui, "GetWindowText", lambda win: "AI小助手")
    monkeypatch.setattr(channels_ui.win32gui, "IsWindowVisible", lambda win: True)
    monkeypatch.setattr(channels_ui.win32gui, "GetWindowRect", lambda win: (0, 0, 400, 600))
    monkeypatch.setattr(channels_ui, "_doc_names", lambda win: ["AppIndex", "Context"])
    assert channels_ui._is_channels_window(9) is False


def test_is_channels_window_matches_video_tab(monkeypatch):
    monkeypatch.setattr(channels_ui.win32gui, "GetWindowText", lambda win: "小田 设计 装修")
    monkeypatch.setattr(channels_ui.win32gui, "IsWindowVisible", lambda win: True)
    monkeypatch.setattr(channels_ui.win32gui, "GetWindowRect", lambda win: (0, 0, 1200, 800))
    monkeypatch.setattr(channels_ui, "_doc_names", lambda win: ["装修避坑 - 第一页"])
    assert channels_ui._is_channels_window(8) is True
