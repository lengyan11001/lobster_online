import asyncio
import inspect
from pathlib import Path

from backend.app.services import native_wechat_engine as engine


ROOT = Path(__file__).resolve().parent


def test_h5_dispatches_one_combined_moments_task():
    channel = (ROOT / "backend" / "app" / "api" / "h5_chat_channel.py").read_text(encoding="utf-8")
    start = channel.index('if action == "native_wechat_moments_engage":')
    end = channel.index('if action == "ip_moments_generate_images":', start)
    branch = channel[start:end]

    assert '"/api/native-wechat/moments/engage"' in branch
    assert '"/api/native-wechat/moments/like"' not in branch
    assert '"/api/native-wechat/moments/comment"' not in branch
    assert "max_scrolls * 4" not in branch


def test_combined_moments_task_processes_each_contact_once(monkeypatch):
    calls = []
    finished = []

    async def fake_process(account_id, target, **kwargs):
        calls.append((account_id, target, kwargs["moment_action"]))
        return {
            "target": target,
            "status": "success",
            "liked": 1,
            "already_liked": 0,
            "commented": 1,
            "already_commented": 0,
        }

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(engine, "_local_moments_or_main_hwnd", lambda _account_id: 123)
    monkeypatch.setattr(engine, "_local_my_names", lambda _account_id: ["我"])
    monkeypatch.setattr(engine, "_process_contact_moments_engage_target", fake_process)
    monkeypatch.setattr(engine, "_merge_task_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "_update_task_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "_finish_task", lambda *args: finished.append(args))
    monkeypatch.setattr(engine, "_sleep", fake_sleep)
    monkeypatch.setattr(
        engine,
        "_sleep_between_moments_targets",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("combined flow must not use the old long delay")),
    )
    engine._TASK_AUTH_CONTEXT["task-1"] = {"token": "token", "user_id": 31}

    asyncio.run(
        engine._process_moments_engage_task(
            {
                "id": "task-1",
                "account_id": "pc-wechat-default",
                "targets": ["wx-one", "wx-two"],
                "payload": {"moment_action": "like_comment", "user_id": 31},
            }
        )
    )

    assert calls == [
        ("pc-wechat-default", "wx-one", "like_comment"),
        ("pc-wechat-default", "wx-two", "like_comment"),
    ]
    assert finished[-1][1:5] == ("success", 2, 2, 0)


def test_contact_engage_flow_has_no_global_timeline_fallback_or_scrolling():
    source = inspect.getsource(engine._process_contact_moments_engage_target)
    open_source = inspect.getsource(engine._open_local_contact_moments)

    assert "_open_local_moments(" not in source
    assert "_scroll_local_moments(" not in source
    assert "_open_local_contact_moments(" in source
    assert "_like_first_visible_moments_post(" in source
    assert "_comment_first_visible_moments_post(" in source
    assert "_open_local_contact_profile_via_search" in open_source
    assert "_scan_and_like_visible_moments" not in open_source


def test_comment_dedupe_and_send_require_ui_confirmation():
    comment_source = inspect.getsource(engine._comment_first_visible_moments_post)
    submit_source = inspect.getsource(engine._submit_moments_comment_at_point)

    assert "already_recorded_and_confirmed" in comment_source
    assert "record_unconfirmed" in comment_source
    assert "_wait_for_moments_comment_confirmation" in submit_source
    assert "未在朋友圈中确认到评论内容" in submit_source


def test_combined_task_is_offloaded_from_api_event_loop():
    source = inspect.getsource(engine._run_account_task_queue)

    start = source.index('elif task_type == "moments_engage":')
    branch = source[start : source.index('elif task_type == "moments_publish":', start)]

    assert "asyncio.to_thread" in branch
    assert "asyncio.run" in branch
    assert "_process_moments_engage_task(task)" in branch


def test_h5_does_not_turn_partial_moments_success_into_full_failure():
    source = (ROOT / "backend" / "app" / "api" / "h5_chat_channel.py").read_text(encoding="utf-8")

    assert 'if status == "partial_failed":' in source
    assert '"status": "partial_success"' in source
    assert "朋友圈互动部分完成" in source


def test_empty_contact_album_is_a_skip_for_like_and_comment(monkeypatch):
    root = object()
    monkeypatch.setattr(engine, "_local_wechat_hwnd", lambda _account_id: 1001)
    monkeypatch.setattr(engine, "_uia_foreground_or_main_root", lambda _hwnd: root)
    monkeypatch.setattr(engine, "_visible_contact_album_cells", lambda _root: [])

    like_result = engine._scan_and_like_contact_album_page(
        "pc-wechat-default", "wx-user", dry_run=False, seen=set(), steps=[]
    )
    assert like_result["skipped"] == 1
    assert like_result["target_handled"] is True
    assert like_result["liked"] == 0

    async def run_comment():
        return await engine._scan_and_comment_contact_album_page(
            "pc-wechat-default",
            "wx-user",
            dry_run=False,
            auth_context={"token": "token"},
            user_id=1,
            steps=[],
            self_names=["self"],
        )

    comment_result = asyncio.run(run_comment())
    assert comment_result["skipped"] == 1
    assert comment_result["result"]["status"] == "skipped"
    assert comment_result["result"]["reason"] == "no_recent_post"


def test_search_result_rejects_network_video_row_and_keeps_contact_row(monkeypatch):
    class Node:
        def __init__(self, text, children=None):
            self.Name = text
            self.ClassName = "mmui::XTableCell"
            self._children = children or []

        def GetChildren(self):
            return self._children

    category = Node("联系人")
    first_contact = Node("张三我是张三已添加")
    group = Node("群聊")
    network = Node("搜索网络结果\n文章、公众号、视频号等\nwxid_target")
    second_contact = Node("李四\nwxid_target")
    root = Node("", [category, first_contact, group, network, second_contact])
    rects = {
        category: (0, 100, 100, 130),
        first_contact: (0, 140, 100, 170),
        group: (0, 180, 100, 210),
        network: (0, 220, 100, 250),
        second_contact: (0, 260, 100, 290),
    }
    monkeypatch.setattr(engine, "_uia_rect_tuple", lambda node: rects.get(node))

    assert engine._find_local_contact_search_result(root, "wxid_target", search_bottom=90) is first_contact
    assert not engine._is_local_contact_search_result(network, "wxid_target")

    # Without a 联系人 section we fail closed instead of clicking a generic
    # result that could route into 视频号/搜一搜.
    no_category_root = Node("", [network, first_contact, group])
    assert engine._find_local_contact_search_result(no_category_root, "wxid_target", search_bottom=90) is None

    no_group_root = Node("", [category, first_contact, network])
    assert engine._find_local_contact_search_result(no_group_root, "wxid_target", search_bottom=90) is first_contact


def test_search_result_accepts_wechat_search_content_contact_row(monkeypatch):
    """WeChat 4.x exposes direct search contacts as SearchContentCellView."""
    class Node:
        def __init__(self, text, class_name, children=None):
            self.Name = text
            self.ClassName = class_name
            self._children = children or []

        def GetChildren(self):
            return self._children

    category = Node("联系人", "mmui::XTableCell")
    first_contact = Node("hddhdjjdjensb", "mmui::SearchContentCellView")
    network = Node("搜索网络结果", "mmui::XTableCell")
    root = Node("", "mmui::SearchContentPopover", [category, first_contact, network])
    rects = {
        category: (0, 454, 320, 486),
        first_contact: (0, 486, 320, 550),
        network: (0, 550, 320, 582),
    }
    monkeypatch.setattr(engine, "_uia_rect_tuple", lambda node: rects.get(node))

    assert engine._find_local_contact_search_result(
        root, "hddhdjjdjensb", search_bottom=448
    ) is first_contact


def test_search_edit_reads_value_pattern_instead_of_placeholder_name():
    class ValuePattern:
        Value = "hddhdjjdjensb"

    class SearchEdit:
        Name = "搜索"

        @staticmethod
        def GetValuePattern():
            return ValuePattern()

    assert engine._uia_get_value(SearchEdit()) == "hddhdjjdjensb"


def test_top_search_field_uses_window_relative_coordinates(monkeypatch):
    """A WeChat window on the right side of the desktop still exposes search."""
    class Node:
        def __init__(self, name, class_name, control_type, rect, children=None):
            self.Name = name
            self.ClassName = class_name
            self.ControlTypeName = control_type
            self.BoundingRectangle = type("Rect", (), {
                "left": rect[0], "top": rect[1], "right": rect[2], "bottom": rect[3]
            })()
            self._children = children or []

        def GetChildren(self):
            return self._children

    search = Node("搜索", "mmui::XValidatorTextEdit", "EditControl", (1080, 202, 1180, 224))
    root = Node("微信", "mmui::MainWindow", "WindowControl", (947, 171, 1896, 962), [search])
    assert engine._find_local_top_search_field(root) is search
