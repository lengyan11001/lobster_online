import asyncio
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

from backend.app.api import h5_chat_channel as channel
from backend.app.services import document_text_extractor as document_extractor
from backend.app.services.document_text_extractor import extract_document_text


def test_online_video_split_uses_bundled_ffmpeg_segmenter(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    output = tmp_path / "segments"
    commands = []

    monkeypatch.setattr(channel, "find_ffmpeg", lambda: "C:/bundle/ffmpeg.exe")

    def fake_run(command, **_kwargs):
        commands.append(command)
        pattern = str(command[-1])
        Path(pattern.replace("%03d", "000")).write_bytes(b"one")
        Path(pattern.replace("%03d", "001")).write_bytes(b"two")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(channel.subprocess, "run", fake_run)

    segments = channel._split_online_video_file(
        source,
        output,
        segment_seconds=3,
        max_segments=120,
    )

    assert [path.name for path in segments] == ["segment_000.mp4", "segment_001.mp4"]
    assert commands[0][commands[0].index("-segment_time") + 1] == "3"
    assert "libx264" in commands[0]


def test_online_video_split_downloads_splits_and_uploads_segments(tmp_path, monkeypatch):
    events = []

    async def fake_event(_cloud, _base, _headers, _message_id, event_type, payload):
        events.append((event_type, payload))

    async def fake_download(_url, target):
        target.write_bytes(b"source-video")
        return target.stat().st_size

    def fake_split(_source, output_dir, **_kwargs):
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = [output_dir / "segment_000.mp4", output_dir / "segment_001.mp4"]
        for index, path in enumerate(paths, start=1):
            path.write_bytes(f"segment-{index}".encode("ascii"))
        return paths

    async def fake_upload(_cloud, _base, _headers, _path, **kwargs):
        index = kwargs["segment_index"]
        return {"asset_id": f"segment-{index}", "media_type": "video"}

    class FakeCloud:
        async def delete(self, url, **_kwargs):
            return SimpleNamespace(status_code=200, text="", content=b"{}")

    monkeypatch.setattr(channel, "_post_cloud_event", fake_event)
    monkeypatch.setattr(channel, "_download_online_split_source", fake_download)
    monkeypatch.setattr(channel, "_split_online_video_file", fake_split)
    monkeypatch.setattr(channel, "_upload_online_split_segment", fake_upload)

    result = asyncio.run(
        channel._run_online_video_split_command(
            FakeCloud(),
            "https://server.example.com",
            {"Authorization": "Bearer test"},
            "split-message-id",
            {
                "source_asset_id": "source-asset",
                "source_url": "https://cdn.example.com/source.mp4",
                "source_filename": "source.mp4",
                "segment_seconds": 3,
                "max_segments": 120,
            },
        )
    )

    assert result["total"] == 2
    assert [item["asset_id"] for item in result["assets"]] == ["segment-1", "segment-2"]
    assert any(payload.get("stage") == "split" for _event_type, payload in events)
    assert any(payload.get("current") == 2 for _event_type, payload in events)


def test_client_command_dispatches_online_video_split(monkeypatch):
    completed = []
    cleaned = []

    async def fake_run(*_args, **_kwargs):
        return {
            "total": 4,
            "assets": [],
            "action": "split_uploaded_video_asset",
            "source_asset_id": "source-asset",
        }

    async def fake_complete(_cloud, _base, _headers, message_id, **kwargs):
        completed.append((message_id, kwargs))

    async def fake_cleanup(_cloud, _base, _headers, source_asset_id):
        cleaned.append(source_asset_id)

    monkeypatch.setattr(channel, "_run_online_video_split_command", fake_run)
    monkeypatch.setattr(channel, "_complete_cloud_message", fake_complete)
    monkeypatch.setattr(channel, "_cleanup_online_split_source", fake_cleanup)
    command = {
        "action": "split_uploaded_video_asset",
        "source_asset_id": "source-asset",
        "source_url": "https://cdn.example.com/source.mp4",
    }
    item = {
        "id": "message-id",
        "content": channel._H5_CLIENT_COMMAND_PREFIX + json.dumps(command),
    }

    asyncio.run(
        channel._run_client_command(
            object(),
            "https://server.example.com",
            {"Authorization": "Bearer test"},
            "jwt-token",
            "installation-id",
            item,
        )
    )

    assert completed[0][0] == "message-id"
    assert completed[0][1]["reply_text"] == "视频切片完成，共生成 4 段"
    assert cleaned == ["source-asset"]


def test_online_reports_memory_parse_capability_only_when_runtime_is_ready(monkeypatch):
    monkeypatch.setattr(channel, "document_parser_runtime_status", lambda: (True, ()))

    capabilities = channel._h5_client_capabilities()

    assert "asset_video_split_v1" in capabilities
    assert "memory_document_parse_v1" in capabilities
    assert "memory_document_generate_v1" in capabilities


def test_online_hides_memory_parse_capability_when_runtime_is_incomplete(monkeypatch):
    monkeypatch.setattr(channel, "document_parser_runtime_status", lambda: (False, ("pypdf",)))

    capabilities = channel._h5_client_capabilities()

    assert "asset_video_split_v1" in capabilities
    assert "memory_document_parse_v1" not in capabilities
    assert "memory_document_generate_v1" not in capabilities


def test_document_parser_runtime_status_lists_missing_dependencies(monkeypatch):
    real_import_module = document_extractor.importlib.import_module

    def fake_import_module(module_name):
        if module_name == "pypdf":
            raise ImportError("not installed")
        return real_import_module(module_name)

    monkeypatch.setattr(document_extractor.importlib, "import_module", fake_import_module)
    try:
        assert document_extractor.document_parser_runtime_status(refresh=True) == (False, ("pypdf",))
    finally:
        document_extractor._document_parser_runtime_status_cached.cache_clear()


def test_online_document_parser_extracts_pptx_text():
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:p><a:t>本机解析内容</a:t></a:p></p:sld>',
        )

    text = extract_document_text(payload.getvalue(), "intro.pptx")

    assert "本机解析内容" in text
    assert "第 1 页" in text


def test_online_memory_parse_downloads_parses_and_writes_back(monkeypatch):
    events = []
    callback_payloads = []

    async def fake_event(_cloud, _base, _headers, _message_id, event_type, payload):
        events.append((event_type, payload))

    async def fake_download(_url, target):
        data = b"document-bytes"
        target.write_bytes(data)
        return data

    def fake_extract(data, filename):
        assert data == b"document-bytes"
        assert filename == "intro.pdf"
        return "解析后的资料"

    class Response:
        status_code = 200
        text = ""
        content = b"{}"

        @staticmethod
        def json():
            return {"document": {"doc_id": "memory-doc"}}

    class FakeCloud:
        async def post(self, url, **kwargs):
            callback_payloads.append((url, kwargs.get("json")))
            return Response()

    monkeypatch.setattr(channel, "_post_cloud_event", fake_event)
    monkeypatch.setattr(channel, "_download_online_memory_source", fake_download)
    monkeypatch.setattr(channel, "extract_document_text", fake_extract)

    result = asyncio.run(
        channel._run_online_memory_parse_command(
            FakeCloud(),
            "https://server.example.com",
            {"Authorization": "Bearer test"},
            "memory-message-id",
            {
                "source_asset_id": "memory-source",
                "source_url": "https://cdn.example.com/intro.pdf",
                "source_filename": "intro.pdf",
                "title": "产品资料",
            },
        )
    )

    assert result["action"] == "parse_uploaded_memory_document"
    assert result["document"]["doc_id"] == "memory-doc"
    assert callback_payloads[0][0].endswith("/complete-online-upload")
    assert callback_payloads[0][1]["content_text"] == "解析后的资料"
    assert any(payload.get("stage") == "parse" for _event_type, payload in events)


def test_memory_source_cleanup_runs_after_message_completion(monkeypatch):
    calls = []

    async def fake_run(*_args, **_kwargs):
        return {
            "action": "parse_uploaded_memory_document",
            "source_asset_id": "memory-source",
            "filename": "intro.pdf",
        }

    async def fake_complete(*_args, **_kwargs):
        calls.append("complete")

    async def fake_cleanup(*_args, **_kwargs):
        calls.append("cleanup")

    monkeypatch.setattr(channel, "_run_online_memory_parse_command", fake_run)
    monkeypatch.setattr(channel, "_complete_cloud_message", fake_complete)
    monkeypatch.setattr(channel, "_cleanup_online_memory_source", fake_cleanup)
    item = {
        "id": "memory-message",
        "content": channel._H5_CLIENT_COMMAND_PREFIX + json.dumps({
            "action": "parse_uploaded_memory_document",
            "source_asset_id": "memory-source",
            "source_url": "https://cdn.example.com/intro.pdf",
        }),
    }

    asyncio.run(
        channel._run_client_command(
            object(),
            "https://server.example.com",
            {"Authorization": "Bearer test"},
            "jwt-token",
            "installation-id",
            item,
        )
    )

    assert calls == ["complete", "cleanup"]


def test_memory_command_rechecks_runtime_before_downloading(monkeypatch):
    calls = []

    def fake_require(*, refresh=False):
        assert refresh is True
        raise RuntimeError("Online 资料解析依赖不完整，缺少：pypdf")

    async def fake_run(*_args, **_kwargs):
        calls.append("run")

    async def fake_complete(*_args, **kwargs):
        calls.append(("complete", kwargs.get("error")))

    async def fake_cleanup(*_args, **_kwargs):
        calls.append("cleanup")

    monkeypatch.setattr(channel, "require_document_parser_runtime", fake_require)
    monkeypatch.setattr(channel, "_run_online_memory_parse_command", fake_run)
    monkeypatch.setattr(channel, "_complete_cloud_message", fake_complete)
    monkeypatch.setattr(channel, "_cleanup_online_memory_source", fake_cleanup)
    item = {
        "id": "legacy-memory-message",
        "content": channel._H5_CLIENT_COMMAND_PREFIX + json.dumps({
            "action": "parse_uploaded_memory_document",
            "source_asset_id": "memory-source",
            "source_url": "https://cdn.example.com/intro.pdf",
        }),
    }

    asyncio.run(
        channel._run_client_command(
            object(),
            "https://server.example.com",
            {"Authorization": "Bearer test"},
            "jwt-token",
            "installation-id",
            item,
        )
    )

    assert "run" not in calls
    assert calls[0] == "cleanup"
    assert calls[1][0] == "complete"
    assert "pypdf" in calls[1][1]


def test_online_memory_generation_parses_every_source_and_writes_back(monkeypatch):
    events = []
    callback_payloads = []

    async def fake_event(_cloud, _base, _headers, _message_id, event_type, payload):
        events.append((event_type, payload))

    async def fake_download(url, target):
        data = f"bytes:{url.rsplit('/', 1)[-1]}".encode("utf-8")
        target.write_bytes(data)
        return data

    def fake_extract(data, filename):
        return f"parsed:{filename}:{data.decode('utf-8')}"

    class Response:
        status_code = 200
        text = ""
        content = b"{}"

        @staticmethod
        def json():
            return {
                "documents": {"profile": "企业资料"},
                "doc_types": ["profile"],
                "source_images": [],
                "file_results": [
                    {"filename": "intro.pdf", "status": "processed", "error": ""},
                    {"filename": "plan.pptx", "status": "processed", "error": ""},
                ],
            }

    class FakeCloud:
        async def post(self, url, **kwargs):
            callback_payloads.append((url, kwargs.get("json")))
            return Response()

    monkeypatch.setattr(channel, "_post_cloud_event", fake_event)
    monkeypatch.setattr(channel, "_download_online_memory_source", fake_download)
    monkeypatch.setattr(channel, "extract_document_text", fake_extract)

    result = asyncio.run(
        channel._run_online_memory_generation_command(
            FakeCloud(),
            "https://server.example.com",
            {"Authorization": "Bearer test"},
            "memory-generate-message",
            {
                "sources": [
                    {
                        "source_asset_id": "source-pdf",
                        "source_url": "https://cdn.example.com/intro.pdf",
                        "source_filename": "intro.pdf",
                        "role": "source",
                    },
                    {
                        "source_asset_id": "reference-pptx",
                        "source_url": "https://cdn.example.com/plan.pptx",
                        "source_filename": "plan.pptx",
                        "role": "reference",
                    },
                ]
            },
        )
    )

    assert result["documents"] == {"profile": "企业资料"}
    assert result["source_asset_ids"] == ["source-pdf", "reference-pptx"]
    assert callback_payloads[0][0].endswith("/complete-online-generation-upload")
    callback_sources = callback_payloads[0][1]["sources"]
    assert [item["source_asset_id"] for item in callback_sources] == ["source-pdf", "reference-pptx"]
    assert all(item["sha256"] for item in callback_sources)
    assert [payload.get("current") for _kind, payload in events if payload.get("stage") == "parse"] == [1, 2]


def test_online_memory_generation_cleans_every_source_after_completion(monkeypatch):
    calls = []

    def fake_require(*, refresh=False):
        assert refresh is True

    async def fake_run(*_args, **_kwargs):
        return {
            "action": "generate_memory_documents_from_upload",
            "source_asset_ids": ["source-one", "source-two"],
            "documents": {"profile": "content"},
        }

    async def fake_complete(*_args, **_kwargs):
        calls.append("complete")

    async def fake_cleanup(_cloud, _base, _headers, source_asset_id):
        calls.append(f"cleanup:{source_asset_id}")

    monkeypatch.setattr(channel, "require_document_parser_runtime", fake_require)
    monkeypatch.setattr(channel, "_run_online_memory_generation_command", fake_run)
    monkeypatch.setattr(channel, "_complete_cloud_message", fake_complete)
    monkeypatch.setattr(channel, "_cleanup_online_memory_source", fake_cleanup)
    item = {
        "id": "memory-generate-message",
        "content": channel._H5_CLIENT_COMMAND_PREFIX + json.dumps({
            "action": "generate_memory_documents_from_upload",
            "sources": [
                {"source_asset_id": "source-one"},
                {"source_asset_id": "source-two"},
            ],
        }),
    }

    asyncio.run(
        channel._run_client_command(
            object(),
            "https://server.example.com",
            {"Authorization": "Bearer test"},
            "jwt-token",
            "installation-id",
            item,
        )
    )

    assert calls == ["complete", "cleanup:source-one", "cleanup:source-two"]
