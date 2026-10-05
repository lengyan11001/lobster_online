"""热门视频跟创：侧边栏位置/改名 + 跟创面板 + 历史弹窗的回归。

2026-10-05 需求：抖音平台信息台挪到「AI营销创作」下面，改名「热门视频跟创」；
里面加视频链接/本地视频、参考图（默认用 IP 人设形象照）、两个模式（复刻特效 / 复刻单人动作）、
提示词、创建任务，以及「历史记录」弹窗。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_sidebar_entry_moved_under_ai_marketing_and_renamed():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "热门视频跟创" in html
    assert "抖音平台信息台" not in html, "旧名字应该只留在隐藏视图的 key 里，侧边栏不该再出现"
    market = html.index("AI营销创作")
    entry = html.index("热门视频跟创")
    group_end = html.index("</details>", market)
    assert market < entry < group_end, "「热门视频跟创」必须在「AI营销创作」分组内部"
    assert 'data-jump-view="douyin-information-desk"' in html


def test_desk_view_has_copy_panel_and_history_modal():
    view = (ROOT / "static" / "views" / "douyin-information-desk.html").read_text(encoding="utf-8")
    assert "<h2>热门视频跟创</h2>" in view
    for marker in (
        "douyinCopyVideoUrl",
        "douyinCopyVideoFile",
        "douyinCopyImageFile",
        "douyinCopyImagePreview",
        "douyinCopyMode",
        "douyinCopyPrompt",
        "douyinCopyCreateBtn",
        "douyinCopyHistoryBtn",
        "douyinCopyModal",
        "douyinCopyModalBody",
    ):
        assert marker in view, marker
    assert "复刻特效" in view and "复刻单人动作" in view
    # 视频来源要写明支持抖音作品链接（2026-10-05：用户就是用 douyin.com/video/xxx 这种）
    assert "douyin.com/video" in view
    # 2026-10-05：新建跟创的表单和历史记录点击都走弹窗
    for marker in ("douyinCopyNewBtn", "douyinCopyCreateModal", "douyinCopyDetailModal", "douyinCopyDetailBody"):
        assert marker in view, marker


def test_copy_modes_default_image_and_modal_wired_in_js():
    js = (ROOT / "static" / "js" / "douyin-information-desk.js").read_text(encoding="utf-8")
    assert "effect_copy" in js and "action_copy" in js and "person_swap" in js
    # 没传图片就用 IP 人设里的形象照
    assert "/api/ip-content/personal-default" in js
    assert "loadCopyDefaultImage" in js
    # 创建任务 + 历史记录弹窗
    assert "createCopyTask" in js and "openCopyHistory" in js
    assert "initCopyPanel" in js
    # 榜单卡片上的跟创改成先选模式
    assert "startCardCopy" in js
