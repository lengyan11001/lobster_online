"""加好友 TXT 模板下载 + 批量导入不截断（2026-09-21）。"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_whatsapp_engine as engine  # noqa: E402


@pytest.fixture()
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(engine, "STATE_DIR", tmp_path)
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "state.db")
    return tmp_path


def test_template_is_written_to_local_file(isolated_root):
    """webview 里 blob 下载点不动，所以模板必须落到本地文件。"""
    result = engine.write_friend_template()
    path = pathlib.Path(result["path"])
    assert path.is_file(), "模板文件必须真的写出来"
    assert path.parent.name == "tmp_templates"
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "要带 UTF-8 BOM，记事本打开才不乱码"
    assert "# 一行一个目标" in result["content"]
    assert "+393311234567" in result["content"], "示例里要有国际号码"


def test_bulk_import_keeps_every_line(isolated_root):
    """导入就是导入：2000 行必须全部入队，不按条数截断。"""
    lines = ["user%04d,1800000%04d" % (i, i) for i in range(2000)]
    result = engine.create_add_contact_task(lines, queue_only=True)
    assert result["planned_total"] == 2000
    assert result["queued_total"] == 2000
    records = engine.list_friend_records(limit=1)
    assert records["total"] == 2000


def test_daily_limit_does_not_block_enqueue(isolated_root):
    """日额度只影响执行速度，不能拦导入。"""
    result = engine.create_add_contact_task(
        ["a,18000000001", "b,18000000002"], queue_only=True, daily_limit=1,
    )
    assert result["queued_total"] == 2
    control = engine.get_friend_add_control()
    assert int(control.get("daily_limit") or 0) == 1


def test_template_endpoint_and_frontend_use_backend():
    api = (ROOT / "backend" / "app" / "api" / "native_whatsapp.py").read_text(encoding="utf-8")
    assert '/api/native-whatsapp/friends/template' in api
    js = (ROOT / "static" / "js" / "personal-whatsapp.js").read_text(encoding="utf-8")
    start = js.index("function downloadFriendTemplate()")
    body = js[start:start + 1200]
    assert "/api/native-whatsapp/friends/template" in body
    assert "createObjectURL" not in body, "不再依赖 webview 里点不动的 blob 下载"
    assert "全量导入" in js
