import asyncio
from backend.app.api import h5_chat_channel as h5


class FakeResponse:
    def __init__(self, data):
        self.status_code = 200
        self.content = b"{}"
        self._data = data

    def json(self):
        return self._data


class FakeCloud:
    def __init__(self, items):
        self.items = items
        self.calls = []

    async def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        return FakeResponse({"items": self.items})


def _candidate(profile_id, virtualman_id, title="张老师"):
    return {
        "provider": "shanjian",
        "profile_id": profile_id,
        "virtualman_id": virtualman_id,
        "avatar": virtualman_id,
        "title": title,
    }


def test_scheduled_task_uses_current_virtualman_after_retrain():
    cloud = FakeCloud([
        {"id": 25, "status": "succeed", "virtualman_id": "new-25", "title": "张老师2"},
        {"id": 6, "status": "succeed", "virtualman_id": "new-6", "title": "张老师4"},
        {"id": 3, "status": "succeed", "virtualman_id": "new-3", "title": "张老师1"},
    ])
    source = {
        "virtualman_selection_mode": "daily_sequence",
        "virtualman_rotation_slot": 0,
        "virtualman_candidates": [
            _candidate(190, "old-190", "张老师3"),
            _candidate(25, "old-25", "张老师2"),
            _candidate(6, "old-6", "张老师4"),
            _candidate(3, "old-3", "张老师1"),
        ],
    }
    result = asyncio.run(h5._resolve_workflow_virtualman(source, cloud=cloud, base="https://bhzn.top", headers={}, current_item=None))
    print("daily result", result)
    assert result["virtualman_id"] in {"new-25", "new-6", "new-3"}
    assert result["virtualman_id"] != "old-190"


def test_fixed_virtualman_is_migrated_by_profile_id():
    cloud = FakeCloud([
        {"id": 25, "status": "succeed", "virtualman_id": "new-25", "title": "张老师2"},
    ])
    source = {
        "virtualman_selection_mode": "fixed",
        "virtualman_id": "old-25",
        "virtualman_candidates": [_candidate(25, "old-25", "张老师2")],
    }
    result = asyncio.run(h5._resolve_workflow_virtualman(source, cloud=cloud, base="https://bhzn.top", headers={}, current_item=None))
    print("fixed result", result)
    assert result["virtualman_id"] == "new-25"


def test_deleted_fixed_profile_returns_empty_for_clear_error():
    cloud = FakeCloud([
        {"id": 25, "status": "succeed", "virtualman_id": "new-25", "title": "张老师2"},
    ])
    source = {
        "virtualman_selection_mode": "fixed",
        "virtualman_id": "old-190",
        "virtualman_candidates": [_candidate(190, "old-190", "张老师3")],
    }
    result = asyncio.run(h5._resolve_workflow_virtualman(source, cloud=cloud, base="https://bhzn.top", headers={}, current_item=None))
    print("deleted result", result)
    assert result == {}
