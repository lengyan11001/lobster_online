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
    assert "capture_wx_no=False" in open_source


def test_comment_dedupe_and_send_require_ui_confirmation():
    comment_source = inspect.getsource(engine._comment_first_visible_moments_post)
    submit_source = inspect.getsource(engine._submit_moments_comment_at_point)

    assert "already_recorded_and_confirmed" in comment_source
    assert "record_unconfirmed" in comment_source
    assert "_wait_for_moments_comment_confirmation" in submit_source
    assert "未在朋友圈中确认到评论内容" in submit_source


def test_combined_task_is_offloaded_from_api_event_loop():
    source = inspect.getsource(engine._run_account_task_queue)

    start = source.index('elif task.get("task_type") == "moments_engage":')
    branch = source[start : source.index('elif task.get("task_type") == "moments_publish":', start)]

    assert "asyncio.to_thread" in branch
    assert "asyncio.run" in branch
    assert "_process_moments_engage_task(task)" in branch


def test_h5_does_not_turn_partial_moments_success_into_full_failure():
    source = (ROOT / "backend" / "app" / "api" / "h5_chat_channel.py").read_text(encoding="utf-8")

    assert 'if status == "partial_failed":' in source
    assert '"status": "partial_success"' in source
    assert "朋友圈互动部分完成" in source
