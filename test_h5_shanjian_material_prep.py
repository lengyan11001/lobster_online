# -*- coding: utf-8 -*-
"""闪剪素材合规化：本地把超出分辨率上限的素材压成副本再交给闪剪。"""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from backend.app.api import assets as assets_api
from backend.app.api import h5_chat_channel as channel


class FakeQuery:
    def __init__(self, owner):
        self.owner = owner

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        if self.owner.rows:
            return self.owner.rows[0]
        return self.owner.added[0] if self.owner.added else None

    def all(self):
        return list(self.owner.rows)


class FakeDB:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.added = []

    def query(self, *_args, **_kwargs):
        return FakeQuery(self)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        return None

    def expire_all(self):
        return None

    def close(self):
        return None


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/api/assets/shanjian-prepare", "headers": []})


def _asset(asset_id="a1", media_type="video", group="数字人口播素材", **meta_extra):
    meta = {"creative_candidate_group": group, "creative_candidate_groups": [group], **meta_extra}
    return SimpleNamespace(
        asset_id=asset_id,
        user_id=7,
        media_type=media_type,
        filename=f"{asset_id}.mp4",
        file_size=10,
        tags="",
        source_url="",
        meta=meta,
    )


def test_shanjian_scaled_dimensions_caps_longest_edge_and_keeps_even():
    assert assets_api.shanjian_scaled_dimensions(1080, 2400, 2000) == (900, 2000)
    width, height = assets_api.shanjian_scaled_dimensions(2048, 1365, 2000)
    assert max(width, height) == 2000
    assert width % 2 == 0 and height % 2 == 0
    assert abs((width / height) - (2048 / 1365)) < 0.01
    assert assets_api.shanjian_scaled_dimensions(1918, 1038, 2000) == (1918, 1038)


def test_shanjian_material_needs_downscale_matches_upstream_limit():
    assert assets_api.shanjian_material_needs_downscale(1080, 2400) is True
    assert assets_api.shanjian_material_needs_downscale(2048, 1365) is True
    assert assets_api.shanjian_material_needs_downscale(1918, 1038) is False


def test_shanjian_tools_prefers_bundled_ffmpeg(tmp_path, monkeypatch):
    deps = tmp_path / "deps" / "ffmpeg"
    deps.mkdir(parents=True)
    ffmpeg = deps / ("ffmpeg.exe" if assets_api.os.name == "nt" else "ffmpeg")
    ffprobe = deps / ("ffprobe.exe" if assets_api.os.name == "nt" else "ffprobe")
    ffmpeg.write_bytes(b"x")
    ffprobe.write_bytes(b"x")
    monkeypatch.setattr(assets_api, "_BASE_DIR", tmp_path)
    monkeypatch.setattr(assets_api, "_SHANJIAN_TOOL_CACHE", {})
    monkeypatch.setattr(assets_api.shutil, "which", lambda _name: None)
    monkeypatch.delenv("FFMPEG_BIN", raising=False)
    monkeypatch.delenv("FFPROBE_BIN", raising=False)

    resolved_ffmpeg, resolved_ffprobe = assets_api._shanjian_tools()

    assert resolved_ffmpeg == str(ffmpeg)
    assert resolved_ffprobe == str(ffprobe)


@pytest.mark.asyncio
async def test_ensure_shanjian_compliant_copy_converts_and_moves_group(tmp_path, monkeypatch):
    row = _asset("orig1")
    db = FakeDB()
    syncs = []
    source = tmp_path / "source.mp4"
    source.write_bytes(b"big-video")

    async def fake_materialize(_row, _request, directory):
        return source

    def fake_probe(_path):
        return 1080, 2400, 12.5

    def fake_transcode(_source, dest, *, media_type, width, height):
        dest.write_bytes(b"small-video")

    def fake_save(data, ext):
        assert data == b"small-video"
        assert ext == ".mp4"
        return "copy1", "copy1.mp4", len(data)

    monkeypatch.setattr(assets_api, "_materialize_library_file", fake_materialize)
    monkeypatch.setattr(assets_api, "probe_media_dimensions", fake_probe)
    monkeypatch.setattr(assets_api, "_transcode_shanjian_copy", fake_transcode)
    monkeypatch.setattr(assets_api, "_save_bytes", fake_save)
    monkeypatch.setattr(assets_api, "_snapshot_auth_server_upload_headers", lambda _request: {})

    async def fake_register(_row, _request):
        return {"asset_id": "remote-copy1"}

    monkeypatch.setattr(assets_api, "_register_user_upload_asset_to_auth_server", fake_register)
    monkeypatch.setattr(
        assets_api,
        "_sync_asset_labels_to_auth_server",
        lambda row, group, tags, request, db: syncs.append((row.asset_id, group)),
    )

    result = await assets_api.ensure_shanjian_compliant_copy(row, request=_request(), db=db)

    assert result["action"] == "converted"
    assert result["compliant_asset_id"] == "copy1"
    assert result["target_width"] == 900 and result["target_height"] == 2000

    copy_row = db.added[0]
    assert copy_row.asset_id == "copy1"
    assert copy_row.meta["creative_candidate_group"] == "数字人口播素材"
    assert copy_row.meta["shanjian_max_edge"] == 2000
    assert copy_row.meta["shanjian_compliant_from"] == "orig1"
    assert copy_row.meta["width"] == 900 and copy_row.meta["height"] == 2000

    # 原件保留，只是把分组让给副本，并记下副本 ID
    assert "creative_candidate_group" not in row.meta
    assert "creative_candidate_groups" not in row.meta
    assert row.meta["shanjian_compliant_asset_id"] == "copy1"
    assert row.meta["shanjian_group_original"] == "数字人口播素材"
    assert ("copy1", "数字人口播素材") in syncs
    assert ("orig1", "") in syncs


@pytest.mark.asyncio
async def test_ensure_shanjian_compliant_copy_skips_material_within_limit(tmp_path, monkeypatch):
    row = _asset("orig2")
    db = FakeDB()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")

    async def fake_materialize(_row, _request, _directory):
        return source

    monkeypatch.setattr(assets_api, "_materialize_library_file", fake_materialize)
    monkeypatch.setattr(assets_api, "probe_media_dimensions", lambda _path: (1918, 1038, 8.0))
    monkeypatch.setattr(
        assets_api,
        "_transcode_shanjian_copy",
        lambda *_args, **_kwargs: pytest.fail("不该转码"),
    )

    result = await assets_api.ensure_shanjian_compliant_copy(row, request=_request(), db=db)

    assert result["action"] == "skipped"
    assert result["reason"] == "within_limit"
    assert db.added == []
    assert row.meta["creative_candidate_group"] == "数字人口播素材"


@pytest.mark.asyncio
async def test_ensure_shanjian_compliant_copy_reuses_existing_copy(monkeypatch):
    row = _asset("orig3", shanjian_compliant_asset_id="copy3")
    existing = _asset("copy3", shanjian_max_edge=2000, shanjian_compliant_from="orig3")
    db = FakeDB(rows=[existing])

    monkeypatch.setattr(
        assets_api,
        "_asset_local_path",
        lambda item: "C:/assets/x.mp4" if item.asset_id == "copy3" else None,
    )
    monkeypatch.setattr(
        assets_api,
        "_materialize_library_file",
        lambda *_args, **_kwargs: pytest.fail("命中副本时不该再读源文件"),
    )

    result = await assets_api.ensure_shanjian_compliant_copy(row, request=_request(), db=db)

    assert result["action"] == "reused"
    assert result["compliant_asset_id"] == "copy3"
    assert db.added == []


def test_shanjian_template_group_names_collects_and_dedupes():
    groups = channel._shanjian_template_group_names(
        {
            "digital_human_asset_groups": ["数字人口播素材", " 场景空镜 "],
            "asset_groups": "数字人口播素材，备用组",
        }
    )

    assert groups == ["数字人口播素材", "场景空镜", "备用组"]


@pytest.mark.asyncio
async def test_prepare_shanjian_template_materials_uses_node_groups(monkeypatch):
    rows = [
        _asset("a1", group="数字人口播素材"),
        _asset("b2", media_type="image", group="数字人口播素材"),
        _asset("c3", group="别的组"),
    ]
    converted = []

    async def fake_ensure(row, *, request, db, max_edge):
        converted.append(row.asset_id)
        return {"action": "converted", "asset_id": row.asset_id}

    monkeypatch.setattr(channel, "SessionLocal", lambda: FakeDB(rows=rows))
    monkeypatch.setattr(channel, "_auth_context", lambda: ("jwt-token", "install-1"))
    monkeypatch.setattr(channel, "_decode_jwt_sub", lambda _token: "7")
    monkeypatch.setattr(assets_api, "ensure_shanjian_compliant_copy", fake_ensure)

    result = await channel._prepare_shanjian_template_materials(
        {"template_mode": "active_personal_template", "digital_human_asset_groups": ["数字人口播素材"]},
        headers={"Authorization": "Bearer jwt-token"},
        cloud=None,
        base="",
        run_id="run-1",
    )

    assert converted == ["a1", "b2"]
    assert result["converted"] == 2
    assert result["candidates"] == 2
    assert result["groups"] == ["数字人口播素材"]


@pytest.mark.asyncio
async def test_prepare_shanjian_template_materials_reads_live_personal_template(monkeypatch):
    rows = [_asset("a1", group="数字人口播素材")]
    called = {}

    class FakeResponse:
        content = b"{}"

        def json(self):
            return {"ok": True, "item": {"meta": {"digital_human_asset_groups": ["数字人口播素材"]}}}

    class FakeCloud:
        async def get(self, url, **_kwargs):
            called["url"] = url
            return FakeResponse()

    async def fake_ensure(row, *, request, db, max_edge):
        return {"action": "converted", "asset_id": row.asset_id}

    monkeypatch.setattr(channel, "SessionLocal", lambda: FakeDB(rows=rows))
    monkeypatch.setattr(channel, "_auth_context", lambda: ("jwt-token", "install-1"))
    monkeypatch.setattr(channel, "_decode_jwt_sub", lambda _token: "7")
    monkeypatch.setattr(assets_api, "ensure_shanjian_compliant_copy", fake_ensure)

    result = await channel._prepare_shanjian_template_materials(
        {"template_mode": "active_personal_template"},
        headers={"Authorization": "Bearer jwt-token"},
        cloud=FakeCloud(),
        base="https://bhzn.top",
        run_id="run-2",
    )

    assert called["url"] == "https://bhzn.top/api/ip-content/personal-default"
    assert result["converted"] == 1
    assert result["groups"] == ["数字人口播素材"]


@pytest.mark.asyncio
async def test_prepare_shanjian_template_materials_skips_without_template(monkeypatch):
    def fail_session():
        return pytest.fail("不用模板时不该访问素材库")

    monkeypatch.setattr(channel, "SessionLocal", fail_session)

    result = await channel._prepare_shanjian_template_materials(
        {"script_source": "auto"},
        headers={},
        cloud=None,
        base="",
        run_id="run-3",
    )

    assert result == {"ok": True, "skipped": True, "reason": "no_template"}
