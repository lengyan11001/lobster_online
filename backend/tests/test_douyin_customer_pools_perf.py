"""抖音获客客户池页性能：不再整页传全量评论/任务，改为按需取单任务明细。"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend" / "douyin_origin") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend" / "douyin_origin"))

douyin_api = pytest.importorskip("douyin_api")

PAGE = ROOT / "static" / "douyin-origin" / "douyin-customer-pools.html"


def test_page_uses_tasks_lite_instead_of_full_tasks():
    html = PAGE.read_text(encoding="utf-8")
    assert "/api/douyin/tasks-lite" in html, "列表页要拉精简任务，不能整页拉全量评论"
    assert 'fetch("/api/douyin/tasks")' not in html


def test_page_fetches_task_detail_on_demand():
    html = PAGE.read_text(encoding="utf-8")
    assert "ensureTaskDetail" in html
    assert "/api/douyin/tasks/${id}" in html


def test_task_endpoints_avoid_deep_compare():
    source = (ROOT / "backend" / "douyin_origin" / "douyin_api.py").read_text(encoding="utf-8")
    assert "normalized_tasks != douyin_tasks" not in source, "每次请求全量比较任务（含评论）太慢"


def test_lite_payload_does_not_deepcopy_task():
    source = (ROOT / "backend" / "douyin_origin" / "douyin_api.py").read_text(encoding="utf-8")
    start = source.index("def build_douyin_task_lite_payload(")
    body = source[start:start + 600]
    assert "ensure_douyin_task_shape(dict(task" not in body, "lite 构造别再深拷贝整任务"
    assert "ensure_douyin_task_shape(task" in body


def test_lite_payload_is_much_smaller_than_full():
    def make_tasks(n_tasks=5, n_comments=200):
        tasks = []
        for t in range(n_tasks):
            comments = [{
                "username": "u%d_%d" % (t, c),
                "comment": "内容 %d-%d" % (t, c),
                "comment_id": "cid-%d-%d" % (t, c),
                "profile_url": "https://www.douyin.com/user/sec_%d_%d" % (t, c),
                "time_text": "2026-09-20",
            } for c in range(n_comments)]
            tasks.append({"id": t + 1, "title": "视频%d" % t, "url": "u",
                          "all_comments": comments, "high_intent_users": comments[:10]})
        return tasks

    tasks = make_tasks()
    full = len(json.dumps(tasks, ensure_ascii=False).encode())
    lite = len(json.dumps([douyin_api.build_douyin_task_lite_payload(t) for t in tasks],
                          ensure_ascii=False).encode())
    assert lite * 10 < full, "lite 至少要小一个数量级（实测约 180 倍）"
