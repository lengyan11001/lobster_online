from __future__ import annotations

from backend.app.services import native_wechat_engine as engine


def _use_temp_native_wechat_db(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native_wechat_engine.db")
    engine.init_db()


def test_partial_contact_sync_merges_without_dropping_existing(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)

    account_id = "wechat-account-1"
    existing = [
        {
            "contact_key": f"wxid_{index:04d}",
            "display_name": f"联系人{index:04d}",
            "source": "pc_wechat_uia_contacts",
        }
        for index in range(1000)
    ]
    engine._merge_contacts_snapshot(account_id, existing)

    partial = [
        {
            "contact_key": f"wxid_{index:04d}",
            "display_name": f"联系人{index:04d} updated",
            "source": "pc_wechat_uia_contacts",
        }
        for index in range(400)
    ]
    saved = engine._merge_contacts_snapshot(account_id, partial)

    page = engine.list_contacts(account_id, limit=1, offset=0)
    updated = engine.list_contacts(account_id, limit=5, keyword="updated")
    assert len(saved) == 400
    assert page["count"] == 1000
    assert updated["count"] == 400


def test_contact_sync_round_budget_covers_large_address_books():
    assert engine._contact_sync_max_rounds(3000) >= 600
    assert engine._contact_sync_max_rounds(10000) >= 900
