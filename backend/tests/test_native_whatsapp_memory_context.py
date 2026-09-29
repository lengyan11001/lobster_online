"""WhatsApp 接管：记忆文件选择与注入（对齐个微那套）。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.api import openclaw_memory  # noqa: E402
from backend.app.services import native_whatsapp_engine as engine  # noqa: E402


def _fake_docs():
    return [
        {"id": "doc_a", "title": "公司介绍", "filename": "a.md", "status": "active"},
        {"id": "doc_b", "title": "报价表", "filename": "b.md", "status": "active"},
    ]


def test_loads_only_selected_memory_docs(monkeypatch):
    monkeypatch.setattr(openclaw_memory, "_load_index", lambda user_id: _fake_docs())
    monkeypatch.setattr(
        openclaw_memory, "_read_canonical_memory_content",
        lambda doc, max_chars=12000: "内容-%s" % doc.get("id"),
    )
    picked = engine._load_auto_reply_memory_context(7, max_chars=12000, max_docs=5, selected_doc_ids=["doc_b"])
    assert picked["document_count"] == 1
    assert picked["titles"] == ["报价表"]
    assert "内容-doc_b" in picked["text"]
    assert "公司介绍" not in picked["text"]


def test_loads_all_active_docs_when_nothing_selected(monkeypatch):
    monkeypatch.setattr(openclaw_memory, "_load_index", lambda user_id: _fake_docs())
    monkeypatch.setattr(
        openclaw_memory, "_read_canonical_memory_content",
        lambda doc, max_chars=12000: "内容-%s" % doc.get("id"),
    )
    picked = engine._load_auto_reply_memory_context(7, selected_doc_ids=[])
    assert picked["document_count"] == 2


def test_no_user_id_returns_empty(monkeypatch):
    monkeypatch.setattr(openclaw_memory, "_load_index", lambda user_id: _fake_docs())
    assert engine._load_auto_reply_memory_context(None)["text"] == ""


def test_config_round_trips_memory_doc_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "STATE_DIR", tmp_path)
    monkeypatch.setattr(engine, "CONFIG_PATH", tmp_path / "config.json")
    saved = engine.save_config(memory_doc_ids=["doc_a", "doc_b"])
    assert saved["memory_doc_ids"] == ["doc_a", "doc_b"]
    assert engine.get_config()["memory_doc_ids"] == ["doc_a", "doc_b"]
    engine.save_config(memory_doc_ids=[])


def test_reply_prompt_includes_memory_and_frontend_has_selector():
    source = (ROOT / "backend" / "app" / "services" / "native_whatsapp_engine.py").read_text(encoding="utf-8")
    assert "memory_text" in source
    assert "参考资料（优先遵守" in source
    assert "memory_context" in source
    html = (ROOT / "static" / "views" / "personal-whatsapp.html").read_text(encoding="utf-8")
    assert 'id="personalWhatsappMemoryDoc"' in html
    js = (ROOT / "static" / "js" / "personal-whatsapp.js").read_text(encoding="utf-8")
    assert "/api/openclaw/memory/list" in js
    assert "memory_doc_ids" in js
