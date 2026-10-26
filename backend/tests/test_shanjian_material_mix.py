from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from backend.app.api import assets as assets_module  # noqa: E402
from backend.app.api import h5_chat_channel, shanjian_smart_clip  # noqa: E402


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *args, **kwargs):
        return _FakeQuery(self._rows)


def _asset(asset_id, media_type, *, filename="", tags="", group="", row_id=0):
    return types.SimpleNamespace(
        id=row_id,
        asset_id=asset_id,
        media_type=media_type,
        filename=filename,
        tags=tags,
        source_url=f"https://cdn.example.com/{asset_id}.bin",
        meta={"creative_candidate_group": group} if group else {},
    )


def _patch_assets(monkeypatch):
    monkeypatch.setattr(assets_module, "_asset_hidden_from_library", lambda row: False)
    monkeypatch.setattr(
        assets_module,
        "_creative_candidate_group",
        lambda meta: str((meta or {}).get("creative_candidate_group") or ""),
    )

    async def _fake_compliant(row, **kwargs):
        return {}

    monkeypatch.setattr(assets_module, "ensure_shanjian_compliant_copy", _fake_compliant)


def _select(body, rows):
    return asyncio.run(
        shanjian_smart_clip.select_mix_materials(
            body,
            request=None,
            current_user=types.SimpleNamespace(id=7),
            db=_FakeDB(rows),
        )
    )


def test_material_selection_keeps_zero_keyword_assets(monkeypatch):
    _patch_assets(monkeypatch)
    rows = [
        _asset("v-unmatched", "video", filename="IMG_0001.mp4", row_id=2),
        _asset("img-matched", "image", filename="火锅海报.png", tags="火锅", row_id=1),
    ]
    result = _select(
        shanjian_smart_clip.MaterialSelectBody(groups=[], script="今晚吃火锅", limit=20),
        rows,
    )
    ids = [item["asset_id"] for item in result["materials"]]
    assert "v-unmatched" in ids
    assert set(ids) == {"v-unmatched", "img-matched"}


def test_material_selection_puts_video_first_on_equal_score(monkeypatch):
    _patch_assets(monkeypatch)
    rows = [
        _asset("img-1", "image", filename="火锅.png", tags="火锅", row_id=9),
        _asset("video-1", "video", filename="火锅.mp4", tags="火锅", row_id=8),
    ]
    result = _select(
        shanjian_smart_clip.MaterialSelectBody(groups=[], script="火锅", limit=20),
        rows,
    )
    assert [item["asset_id"] for item in result["materials"]] == ["video-1", "img-1"]


def test_material_selection_respects_group_filter_and_limit(monkeypatch):
    _patch_assets(monkeypatch)
    rows = [
        _asset(f"v{i}", "video", filename=f"clip-{i}.mp4", group="门店素材", row_id=100 - i)
        for i in range(25)
    ]
    rows.append(_asset("other", "video", filename="other.mp4", group="竞品素材", row_id=1))
    result = _select(
        shanjian_smart_clip.MaterialSelectBody(groups=["门店素材"], script="", limit=99),
        rows,
    )
    assert result["limit"] == 20
    assert result["selected"] == 20
    assert all(item["asset_id"] != "other" for item in result["materials"])
    assert all(item["type"] == "video" for item in result["materials"])


def test_oral_mix_cutting_submit_sends_selected_duration_and_materials(monkeypatch):
    captured = {}

    async def _fake_post(path, token, body):
        captured["path"] = path
        captured["body"] = body
        return {"code": "Succeed", "data": {"taskId": "task-1"}}

    monkeypatch.setattr(shanjian_smart_clip, "_post", _fake_post)
    body = shanjian_smart_clip.SubmitClipBody(
        scene="oralMixCutting",
        style_id="style-9",
        title="素材混剪测试",
        audio_url="https://cdn.example.com/voice.mp3",
        video_duration=47,
        material_composition="sequential",
        materials=[
            {"type": "video", "fileUrl": "https://cdn.example.com/a.mp4"},
            {"type": "image", "fileUrl": "https://cdn.example.com/b.png"},
        ],
    )
    result = asyncio.run(
        shanjian_smart_clip.submit_clip(
            body,
            current_user=types.SimpleNamespace(id=7),
            db=_FakeDB([]),
        )
    )
    assert result["task_id"] == "task-1"
    assert captured["path"] == "/v1/clip/video/broadcast_mixcut"
    payload = captured["body"]
    assert payload["styleId"] == "style-9"
    assert payload["audioUrl"] == "https://cdn.example.com/voice.mp3"
    assert payload["processRules"]["videoDuration"] == 47
    assert payload["processRules"]["materialComposition"] == "order"
    assert len(payload["materials"]) == 2
    assert "virtualmanId" not in payload


def test_material_mix_workflow_does_not_resolve_a_digital_human():
    import inspect

    source = inspect.getsource(h5_chat_channel._run_shanjian_material_mix_workflow)
    assert "virtualman" not in source.lower()
    assert "/api/shanjian-smart-clip/materials/select" in source
    assert "/api/shanjian-smart-clip/submit" in source
    assert '"scene": "oralMixCutting"' in source


def test_material_mix_node_is_wired_in_workflow_ui_and_dispatch():
    js = (ROOT / "static" / "js" / "views" / "h5-employees.js").read_text(encoding="utf-8")
    html = (ROOT / "static" / "views" / "h5-employees.html").read_text(encoding="utf-8")
    channel = (ROOT / "backend" / "app" / "api" / "h5_chat_channel.py").read_text(encoding="utf-8")

    assert "['shanjian_material_mix','素材混剪'" in js
    assert "if (row.key === 'shanjian_material_mix')" in js
    assert "action:'shanjian_material_mix'" in js
    assert "oeNodeMaterialMixDuration" in js
    assert "oeNodeMaterialMixDuration" in html
    assert 'if action == "shanjian_material_mix":' in channel
    assert "return await _run_shanjian_material_mix_workflow(" in channel

def test_material_mix_workflow_submits_oral_mix_without_digital_human(monkeypatch):
    calls = []

    async def _fake_event(*args, **kwargs):
        return None

    async def _fake_local_post(path, body, *, headers, timeout_seconds=0.0, request_id=""):
        calls.append((path, body))
        if path == "/api/shanjian-smart-clip/materials/select":
            return {
                "ok": True,
                "materials": [
                    {"type": "video", "fileUrl": "https://cdn.example.com/a.mp4"},
                    {"type": "image", "fileUrl": "https://cdn.example.com/b.png"},
                ],
                "candidates": 2,
                "selected": 2,
            }
        if path == "/api/shanjian-smart-clip/submit":
            return {"ok": True, "task_id": "mix-task-1"}
        if path == "/api/shanjian-smart-clip/task":
            return {
                "ok": True,
                "status": "succeed",
                "video_url": "https://cdn.example.com/mix.mp4",
                "cover_url": "https://cdn.example.com/mix.jpg",
                "duration": 45,
            }
        raise AssertionError(f"unexpected local api: {path}")

    async def _fake_asset(**kwargs):
        return "local-asset-1", {"status": "ready"}

    async def _fake_rehost(audio_url, *, title):
        assert audio_url == "data:audio/mpeg;base64,AAAA"
        return "https://tos.example.com/rehosted.mp3"

    monkeypatch.setattr(h5_chat_channel, "_workflow_event", _fake_event)
    monkeypatch.setattr(h5_chat_channel, "_rehost_audio_for_shanjian", _fake_rehost)
    monkeypatch.setattr(h5_chat_channel, "_post_local_api_json", _fake_local_post)
    monkeypatch.setattr(h5_chat_channel, "_ensure_local_workflow_asset", _fake_asset)

    result = asyncio.run(
        h5_chat_channel._run_shanjian_material_mix_workflow(
            {
                "audio_url": "data:audio/mpeg;base64,AAAA",
                "script": "今晚带你看看这家火锅店",
                "style_id": "style-9",
                "video_duration": 45,
                "asset_groups": ["门店素材"],
                "title": "火锅探店",
                "material_composition": "order",
            },
            headers={"Authorization": "Bearer test"},
            run_id="run-1",
            cloud=object(),
            base="http://cloud.local",
        )
    )

    paths = [path for path, _body in calls]
    assert paths == [
        "/api/shanjian-smart-clip/materials/select",
        "/api/shanjian-smart-clip/submit",
        "/api/shanjian-smart-clip/task",
    ]
    select_body = calls[0][1]
    assert select_body["groups"] == ["门店素材"]
    assert select_body["script"] == "今晚带你看看这家火锅店"
    submit_body = calls[1][1]
    assert submit_body["scene"] == "oralMixCutting"
    assert submit_body["audio_url"] == "https://tos.example.com/rehosted.mp3"
    assert submit_body["style_id"] == "style-9"
    assert submit_body["video_duration"] == 45
    assert submit_body["material_composition"] == "order"
    assert [item["fileUrl"] for item in submit_body["materials"]] == [
        "https://cdn.example.com/a.mp4",
        "https://cdn.example.com/b.png",
    ]
    assert "virtualman" not in str(submit_body).lower()
    assert result["video_url"] == "https://cdn.example.com/mix.mp4"
    assert result["video_asset_id"] == "local-asset-1"
    assert result["material_count"] == 2
