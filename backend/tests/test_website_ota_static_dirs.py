"""回归：static/ 下新增的资源目录必须进 OTA 白名单，否则客户端升级后 404。

2026-09-30 build 381 事故：static/canvas-web（灵感画布本体）没进
pack_client_code_ota.WEBSITE_OTA_PATHS / 发布器 manifest.paths，
客户端只拿到 static/views/canvas-studio.html，打开画布 404
（诊断包 diag_20260930072739_8a9379e5: GET /static/canvas-web/index.html 404）。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import pack_client_code_ota as packer  # noqa: E402

# 运行时生成 / 明确不发 OTA 的目录（新增目录请显式加进来，或加进 WEBSITE_OTA_PATHS）
RUNTIME_ONLY_STATIC_DIRS = {
    "generated",        # 运行时产出目录：里面既有入库的 agent 头像，也有运行时文件；
                        # 因 OTA 对账会删除"包里没有的文件"，暂不整目录入包（待人工确认）
    "hifly_previews",   # 预览缓存（.gitignore）
    "uploads",          # 用户上传（.gitignore）
    "skill-cards",      # 技能卡图（已随包，见 WEBSITE_OTA_PATHS）
    "BihuIcon.iconset", # 源工程图标集，仅打包期使用
}


def _declared_static_dirs() -> set[str]:
    out = set()
    for item in packer.WEBSITE_OTA_PATHS:
        if item.startswith("static/"):
            rest = item[len("static/"):]
            out.add(rest.split("/")[0])
    return out


def test_every_static_dir_is_covered_by_website_ota_paths():
    static_root = REPO_ROOT / "static"
    actual = {p.name for p in static_root.iterdir() if p.is_dir()}
    declared = _declared_static_dirs()
    missing = sorted(
        name for name in actual
        if name not in declared and name not in RUNTIME_ONLY_STATIC_DIRS
    )
    assert not missing, (
        "static/ 下这些目录既不在 WEBSITE_OTA_PATHS 里，也没列入 RUNTIME_ONLY_STATIC_DIRS，"
        f"客户端升级后会 404：{missing}"
    )


def test_canvas_web_ships_with_website_ota():
    assert "static/canvas-web" in packer.WEBSITE_OTA_PATHS
    assert (REPO_ROOT / "static/canvas-web/index.html").is_file()
