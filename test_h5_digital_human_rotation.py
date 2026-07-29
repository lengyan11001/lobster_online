import asyncio
from datetime import date

from backend.app.api.h5_chat_channel import _resolve_workflow_virtualman, _select_daily_virtualman


def _candidates():
    return [
        {"profile_id": 1, "virtualman_id": "avatar-1", "title": "Avatar 1"},
        {"profile_id": 2, "virtualman_id": "avatar-2", "title": "Avatar 2"},
        {"profile_id": 3, "virtualman_id": "avatar-3", "title": "Avatar 3"},
    ]


def test_daily_virtualman_rotates_on_consecutive_days():
    first = _select_daily_virtualman(
        _candidates(), local_day=date(2026, 7, 27), rotation_key="sales|node-7"
    )
    second = _select_daily_virtualman(
        _candidates(), local_day=date(2026, 7, 28), rotation_key="sales|node-7"
    )

    assert first["virtualman_id"] != second["virtualman_id"]


def test_daily_virtualman_is_stable_for_same_day_and_node():
    first = _select_daily_virtualman(
        _candidates(), local_day=date(2026, 7, 27), rotation_key="sales|node-7"
    )
    retry = _select_daily_virtualman(
        list(reversed(_candidates())), local_day=date(2026, 7, 27), rotation_key="sales|node-7"
    )

    assert first == retry


def test_daily_virtualman_reuses_only_available_avatar():
    selected = _select_daily_virtualman(
        [{"profile_id": 9, "virtualman_id": "only-avatar"}],
        local_day=date(2026, 7, 27),
        rotation_key="sales|node-7",
    )

    assert selected["virtualman_id"] == "only-avatar"


def test_sales_nodes_use_distinct_sequential_avatars_on_same_day():
    selected = [
        _select_daily_virtualman(
            _candidates(),
            local_day=date(2026, 7, 29),
            rotation_key=f"sales|node-{slot}",
            sequence_slot=slot,
        )["virtualman_id"]
        for slot in range(3)
    ]

    assert len(set(selected)) == 3


def test_sales_sequence_advances_on_next_day():
    first = _select_daily_virtualman(
        _candidates(), local_day=date(2026, 7, 29), rotation_key="sales", sequence_slot=0
    )
    next_day = _select_daily_virtualman(
        _candidates(), local_day=date(2026, 7, 30), rotation_key="sales", sequence_slot=0
    )

    assert first["virtualman_id"] != next_day["virtualman_id"]


class _ProfileResponse:
    status_code = 200
    content = b"profiles"

    def __init__(self, items):
        self._items = items

    def json(self):
        return {"ok": True, "items": self._items}


class _ProfileCloud:
    def __init__(self, items):
        self._items = items

    async def get(self, *args, **kwargs):
        return _ProfileResponse(self._items)


def test_existing_sales_task_refreshes_candidates_from_cloud():
    selected = asyncio.run(
        _resolve_workflow_virtualman(
            {
                "virtualman_id": "legacy-fixed-avatar",
                "script_source": "ip_daily_industry_hot_oral",
                "h5_context": {"workflow_template_id": 8, "workflow_node_id": "node-7"},
                "schedule_config": {"timezone_offset_minutes": 480},
            },
            cloud=_ProfileCloud(
                [
                    {"id": 10, "virtualman_id": "avatar-10", "status": "succeed"},
                    {"id": 11, "virtualman_id": "avatar-11", "status": "succeed"},
                ]
            ),
            base="https://example.test",
            headers={"Authorization": "Bearer token"},
            current_item={"created_at": "2026-07-27T02:00:00Z"},
        )
    )

    assert selected["virtualman_id"] in {"avatar-10", "avatar-11"}
    assert selected["virtualman_id"] != "legacy-fixed-avatar"
    assert selected["selection_mode"] == "daily_round_robin"
    assert selected["selection_date"] == "2026-07-27"


def test_successful_empty_cloud_profile_list_does_not_reuse_deleted_snapshot():
    selected = asyncio.run(
        _resolve_workflow_virtualman(
            {
                "virtualman_id": "deleted-avatar",
                "virtualman_selection_mode": "daily_round_robin",
                "virtualman_candidates": [
                    {"profile_id": 1, "virtualman_id": "deleted-avatar"},
                ],
            },
            cloud=_ProfileCloud([]),
            base="https://example.test",
            headers={},
            current_item={"created_at": "2026-07-27T02:00:00Z"},
        )
    )

    assert selected == {}
