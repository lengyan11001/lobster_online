"""升级红线测试：代码 OTA 对账不得删除用户数据。

背景：客户端更新器 _sync_tree_incremental 会把 manifest.paths 里列出目录中
「包里没有的文件」删掉（2026-09-20 build 341 品牌图事故、2026-09-30 canvas-web 404 事故）。
本测试锁死：用户运行期数据目录永远保留，同时正常的 stale 代码文件仍会被清理。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def _load_updater(tmp_root: Path):
    spec = importlib.util.spec_from_file_location("ccu_under_test", str(REPO_ROOT / "scripts/check_client_code_update.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ccu_under_test"] = mod
    spec.loader.exec_module(mod)
    mod.ROOT = tmp_root  # 让 _sync_tree_incremental 以临时目录为客户端根
    return mod


def test_user_data_survives_code_reconcile(tmp_path):
    root = tmp_path / "client"
    src_static = tmp_path / "pkg" / "static"
    dst_static = root / "static"

    # 包里带的代码/功能资源
    (src_static / "js").mkdir(parents=True)
    (src_static / "js" / "new.js").write_text("new", encoding="utf-8")
    (src_static / "canvas-web").mkdir(parents=True)
    (src_static / "canvas-web" / "index.html").write_text("<html>", encoding="utf-8")

    # 客户端上的用户数据（绝不能被升级删除）
    user_files = [
        dst_static / "hifly_avatars" / "abc.mp4.face.png",
        dst_static / "generated" / "agent" / "avatars" / "h5-employee-male-idle.png",
        dst_static / "hifly_previews" / "cache.json",
        dst_static / "uploads" / "my.png",
        dst_static / "branding" / "daka_logo.png",
    ]
    for path in user_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("user-data", encoding="utf-8")

    # 旧的、包中已不存在的代码文件（应当被清理）
    stale = dst_static / "js" / "old-removed.js"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("stale", encoding="utf-8")

    mod = _load_updater(root)
    stats = mod._sync_tree_incremental(src_static, dst_static)

    for path in user_files:
        assert path.is_file(), f"用户数据被升级删除了：{path}"
    assert not stale.exists(), "stale 代码文件应被清理"
    assert (dst_static / "canvas-web" / "index.html").is_file()
    assert stats["preserved"] >= len(user_files)
    assert stats["removed"] >= 1


def test_red_line_list_covers_known_user_dirs():
    mod = _load_updater(Path("."))
    for rel in ("static/generated", "static/hifly_avatars", "static/hifly_previews",
                "static/uploads", "static/branding", "logs", ".updates", "openclaw/workspace"):
        assert mod._is_user_data_protected(rel), rel
        assert mod._is_user_data_protected(rel + "/x/y.bin"), rel
    assert not mod._is_user_data_protected("static/js/app.js")