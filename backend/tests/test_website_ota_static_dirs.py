"""回归：OTA 只带「代码与功能资源」，绝不带用户运行目录。

2026-09-30 build 381 事故：static/canvas-web（灵感画布本体）不在
pack_client_code_ota.WEBSITE_OTA_PATHS / 发布器 manifest.paths 里，
客户端只拿到 static/views/canvas-studio.html，打开画布
GET /static/canvas-web/index.html → 404（诊断包 diag_20260930072739_8a9379e5）。

反向红线：manifest.paths 里列出的目录，客户端升级时会按「包里没有的文件」对账删除，
所以 **用户运行期产出目录绝不能列进去**（否则一升级就把用户文件清掉）。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import pack_client_code_ota as packer  # noqa: E402

# 用户运行期产出 / 本机私有 / 源工程目录：不得随 OTA、更不得列入 manifest.paths
RUNTIME_ONLY_STATIC_DIRS = {
    "generated",         # /static/generated：运行时产出（agent 头像等）
    "hifly_avatars",     # 用户自己视频的人脸/抠像图（.mp4.face.png / .rmbgcache.png）——本机私有数据
    "hifly_previews",    # 预览缓存
    "uploads",           # 用户上传
    "skill-cards",       # 技能卡图（当前无代码引用，未随包）
    "BihuIcon.iconset",  # 打包期图标源工程
}


def _declared_static_dirs() -> set[str]:
    return {
        item[len("static/"):].split("/")[0]
        for item in packer.WEBSITE_OTA_PATHS
        if item.startswith("static/")
    }


def test_every_static_dir_is_declared_or_runtime_only():
    actual = {p.name for p in (REPO_ROOT / "static").iterdir() if p.is_dir()}
    missing = sorted(n for n in actual if n not in _declared_static_dirs() and n not in RUNTIME_ONLY_STATIC_DIRS)
    assert not missing, f"static/ 下这些目录既没随 OTA、也没登记为运行目录，客户端升级后会 404：{missing}"


def test_runtime_dirs_never_enter_ota_paths():
    declared = _declared_static_dirs()
    bad = sorted(RUNTIME_ONLY_STATIC_DIRS & declared)
    assert not bad, f"用户运行目录不允许出现在 OTA 清单里（升级会被对账删除）：{bad}"


def test_canvas_web_ships_with_website_ota():
    assert "static/canvas-web" in packer.WEBSITE_OTA_PATHS
    assert (REPO_ROOT / "static/canvas-web/index.html").is_file()


def test_source_maps_are_not_shipped():
    assert packer._skip_file("static/canvas-web/assets/main.bad865f4.js.map") is True