from pathlib import Path

"""客户端界面跳闪取证通道（2026-10-13）。

线上现象：鼠标一动界面就闪/跳几下，但服务端日志里没有 reload、没有重新拉视图。
这里验证取证落盘通道：POST 写入 logs/client_ui_trace.jsonl、GET 能读回、
文件大小超限会轮转、并且该文件已经进诊断包清单。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import logs_api as module
from backend.app.api.auth import _ServerUser, get_current_user_media_edit


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[get_current_user_media_edit] = lambda: _ServerUser(id=116)
    return TestClient(app)


def test_ui_trace_roundtrip_and_bundle_membership(tmp_path, monkeypatch):
    target = tmp_path / "client_ui_trace.jsonl"
    monkeypatch.setattr(module, "_UI_TRACE_FILE", target)

    # 取证文件必须进诊断包清单，否则报了也拿不到
    assert "logs/client_ui_trace.jsonl" in module._LOG_CANDIDATES

    client = _client()
    payload = {
        "reason": "burst",
        "view": "content-wechat-article",
        "mouse": {"x": 1500, "y": 40, "over": "button#headerDropdownBtn"},
        "events": [
            {"kind": "innerHTML", "detail": "div#wechatArticlePreview len=812", "stack": "render @ wechat-article.js:1"},
            {"kind": "scroll-jump", "detail": "window dy=-540 to=0"},
        ],
    }
    resp = client.post("/api/client-ui-trace", json=payload)
    assert resp.status_code == 200
    assert resp.json()["written"] > 0
    assert target.is_file()

    read = client.get("/api/client-ui-trace?tail=50")
    assert read.status_code == 200
    assert "content-wechat-article" in read.text
    assert "scroll-jump" in read.text
    assert "#headerDropdownBtn" in read.text


def test_ui_trace_rotates_when_file_grows(tmp_path, monkeypatch):
    target = tmp_path / "client_ui_trace.jsonl"
    monkeypatch.setattr(module, "_UI_TRACE_FILE", target)
    monkeypatch.setattr(module, "_UI_TRACE_MAX_BYTES", 400)

    for index in range(6):
        module.append_client_ui_trace(
            {"reason": "burst", "n": index, "events": [{"kind": "innerHTML", "detail": "x" * 60}]}
        )

    assert target.is_file()
    assert target.with_name(target.name + ".1").is_file()


def test_ui_trace_empty_response_is_plain_text(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_UI_TRACE_FILE", tmp_path / "missing.jsonl")
    resp = _client().get("/api/client-ui-trace")
    assert resp.status_code == 200
    assert "还没有界面取证记录" in resp.text


def _trace_js_code() -> str:
    """ui-jump-trace.js 去掉注释后的代码（注释里会引用坏写法做说明）。"""
    path = Path(__file__).resolve().parent / "static" / "js" / "ui-jump-trace.js"
    js = path.read_text(encoding="utf-8")
    return "\n".join(line for line in js.splitlines() if not line.strip().startswith("//"))


def test_client_trace_script_uses_authenticated_fetch():
    """sendBeacon 不能带 Authorization，落到鉴权接口上必然 401。

    2026-10-13 真机实测：用 sendBeacon 的版本，8 次跳闪全部 401 被丢掉，一条没落盘。
    """
    code = _trace_js_code()
    assert "sendBeacon" not in code
    assert "headers: authHeaders()" in code
    assert "localStorage.getItem('token')" in code


def test_client_trace_ignores_startup_renders():
    """只有「用户刚操作过」的结构性事件才算跳闪，否则启动时视图初始化会疯狂误报。"""
    code = _trace_js_code()
    assert "lastInputAt" in code
    assert "settleUntil" in code
    assert "since_input_ms" in code
    assert "noteUserInput" in code
