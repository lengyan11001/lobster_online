"""加好友 TXT 模板下载：与个人微信保持一致（pywebview 原生保存，失败退回浏览器下载）。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_whatsapp_engine as engine  # noqa: E402


def test_frontend_uses_pywebview_save_like_wechat():
    js = (ROOT / "static" / "js" / "personal-whatsapp.js").read_text(encoding="utf-8")
    start = js.index("function downloadFriendTemplate()")
    body = js[start:start + 2000]
    assert "pywebview" in body
    assert "save_text_file" in body, "和个微一样走原生保存"
    assert "URL.createObjectURL" in body, "保留浏览器下载兜底"
    assert "openFriendAddModal" not in body, "下载模板不该弹添加好友弹窗"
    wechat = (ROOT / "static" / "js" / "juhe-wechat.js").read_text(encoding="utf-8")
    assert "save_text_file" in wechat, "个微那边也是这个做法"


def test_bulk_import_keeps_every_line(tmp_path, monkeypatch):
    """导入就是导入：2000 行全部入队，不按条数截断。"""
    monkeypatch.setattr(engine, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(engine, "STATE_DIR", tmp_path)
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "state.db")
    lines = ["user%04d,1800000%04d" % (i, i) for i in range(2000)]
    result = engine.create_add_contact_task(lines, queue_only=True)
    assert result["planned_total"] == 2000
    assert engine.list_friend_records(limit=1)["total"] == 2000


def test_daily_limit_does_not_block_enqueue(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(engine, "STATE_DIR", tmp_path)
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "state.db")
    result = engine.create_add_contact_task(["a,18000000001", "b,18000000002"], queue_only=True, daily_limit=1)
    assert result["queued_total"] == 2
