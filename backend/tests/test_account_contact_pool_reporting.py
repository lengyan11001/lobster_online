"""抖音私信接管上报 / 个微自动加好友领取「服务端账号池」的回归。

用户需求（2026-09-22）：
- 工作流节点「抖音私信接管」里的「自动提交好友申请」勾选 → 走现状（本机提交微信加好友）；
  不勾选 → 把识别到的号码上报到服务端（账号级数据）。
- 「个微自动加好友」节点增加来源选项：本机导入名单 / 上级抖音私信结果 / 服务端上报池，
  同账号的另一台机器执行该节点时也能领取这些号码去加好友。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "douyin_origin"))

from backend.app.api import h5_chat_channel as channel
from backend.douyin_origin.douyin_api import douyin_wechat_contact_entries

DOUYIN_API = ROOT / "backend" / "douyin_origin" / "douyin_api.py"
EMPLOYEES_HTML = ROOT / "static" / "views" / "h5-employees.html"
EMPLOYEES_JS = ROOT / "static" / "js" / "views" / "h5-employees.js"


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = b"{}"
        self.text = ""

    def json(self):
        return self._payload


class _FakeCloud:
    """记录 POST，用来断言请求路径与请求体，不需要真实服务端。"""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    async def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        payload = self.responses.pop(0) if self.responses else {"ok": True}
        return _FakeResponse(payload)


def test_douyin_contact_entries_dedupe_and_keep_username():
    rows = [
        {"username": "小王", "conversation_source": "chat_inbox", "phone_numbers": ["13800138000", "138 0013 8000"]},
        {"username": "小李", "conversation_source": "stranger", "phone_numbers": ["13900139000"]},
        {"username": "没号码", "phone_numbers": []},
    ]
    entries = douyin_wechat_contact_entries(rows)
    assert [item["value"] for item in entries] == ["13800138000", "13900139000"]
    assert entries[0]["username"] == "小王"
    assert entries[1]["conversation_id"] == "stranger"
    assert all(item["kind"] == "mobile" for item in entries)


def test_douyin_one_shot_result_exposes_contact_entries():
    source = DOUYIN_API.read_text(encoding="utf-8")
    assert '"wechat_contact_entries": douyin_wechat_contact_entries(prepared_rows)' in source


def test_report_uploads_entries_into_account_pool():
    cloud = _FakeCloud([{"ok": True, "created": 2, "refreshed": 0, "skipped": 0, "pending": 2}])
    result = {
        "extracted_phone_numbers": ["13800138000"],
        "wechat_contact_entries": [
            {"value": "13800138000", "kind": "mobile", "username": "小王", "conversation_id": "chat_inbox"}
        ],
    }
    report = asyncio.run(
        channel._report_douyin_wechat_contacts_to_cloud(
            cloud,
            "https://cloud.example",
            {"Authorization": "Bearer t"},
            result=result,
            account_id=3,
        )
    )
    assert cloud.calls[0]["url"] == "https://cloud.example/api/wechat-contact-pool/report"
    body = cloud.calls[0]["json"]
    assert body["platform"] == "douyin"
    assert body["account_label"] == "抖音账号 3"
    assert body["items"][0]["value"] == "13800138000"
    assert report["ok"] is True and report["count"] == 1 and report["pending"] == 2


def test_report_falls_back_to_extracted_numbers_when_detail_missing():
    cloud = _FakeCloud([{"ok": True, "created": 1, "pending": 1}])
    report = asyncio.run(
        channel._report_douyin_wechat_contacts_to_cloud(
            cloud,
            "https://cloud.example",
            {},
            result={"extracted_phone_numbers": ["13700137000"]},
            account_id=1,
        )
    )
    assert report["ok"] is True
    assert cloud.calls[0]["json"]["items"][0]["value"] == "13700137000"


def test_report_skips_without_contact_and_flags_missing_cloud():
    skipped = asyncio.run(
        channel._report_douyin_wechat_contacts_to_cloud(
            _FakeCloud(), "https://cloud.example", {}, result={"extracted_phone_numbers": []}, account_id=1
        )
    )
    assert skipped["skipped"] is True and skipped["reason"] == "no_contact"
    missing = asyncio.run(
        channel._report_douyin_wechat_contacts_to_cloud(
            None,
            "",
            {},
            result={"extracted_phone_numbers": ["13800138000"]},
            account_id=1,
        )
    )
    assert missing["ok"] is False and missing["reason"] == "cloud_missing"


def test_claim_reads_pool_items_and_ack_writes_back():
    cloud = _FakeCloud(
        [
            {
                "ok": True,
                "claimed": 2,
                "items": [
                    {"id": 1, "value": "13800138000", "kind": "mobile", "username": "小王", "account_label": "抖音账号 1"},
                    {"id": 2, "value": "13900139000", "kind": "mobile", "username": "", "account_label": ""},
                ],
            },
            {"ok": True, "added": 2, "released": 0},
        ]
    )
    items = asyncio.run(
        channel._claim_reported_wechat_contacts(
            cloud, "https://cloud.example", {}, limit=999
        )
    )
    assert [item["value"] for item in items] == ["13800138000", "13900139000"]
    claim_body = cloud.calls[0]["json"]
    assert claim_body["platform"] == "douyin"
    # 领取数量上限 200，防止节点里填个巨大的数字把整池一次领空
    assert claim_body["limit"] == 200

    acked = asyncio.run(
        channel._ack_reported_wechat_contacts(
            cloud,
            "https://cloud.example",
            {},
            added=["13800138000"],
            failed=["13900139000"],
            error="boom",
        )
    )
    ack_body = cloud.calls[1]["json"]
    assert ack_body["added"] == ["13800138000"] and ack_body["failed"] == ["13900139000"]
    assert ack_body["error"] == "boom"
    assert acked["ok"] is True


def test_pool_mode_is_recognized_for_native_add_friend():
    assert "server_reported_pool" in channel._WECHAT_CONTACT_POOL_SOURCE_MODES
    source = (ROOT / "backend" / "app" / "api" / "h5_chat_channel.py").read_text(encoding="utf-8")
    start = source.index('if action == "native_wechat_add_friend":')
    body = source[start:start + 6000]
    assert "_claim_reported_wechat_contacts(" in body
    assert "server_pool_empty" in body
    assert "_ack_reported_wechat_contacts(" in body
    douyin_branch = source.index('if action == "stranger_message":')
    douyin_body = source[douyin_branch:douyin_branch + 3000]
    assert "if not wechat_add_friend_enabled:" in douyin_body
    assert "_report_douyin_wechat_contacts_to_cloud(" in douyin_body


def test_workflow_editor_exposes_target_source_option():
    html = EMPLOYEES_HTML.read_text(encoding="utf-8")
    for value in ("local_import", "douyin_private_message_phone", "server_reported_pool"):
        assert f'name="oeNodeNativeAddFriendSource" value="{value}"' in html
    assert "不勾选时本机不动微信" in html
    js = EMPLOYEES_JS.read_text(encoding="utf-8")
    assert "function nativeAddFriendSourceFromForm()" in js
    assert "row.params.source_mode='server_reported_pool'" in js
    assert "row.params.max_targets=Math.max(1,Math.min(200," in js


def test_native_add_friend_pool_branch_claims_then_acks(monkeypatch):
    """个微自动加好友选「服务端上报池」：领取 → 提交本机加好友 → 回执。"""
    posted = {}

    async def fake_post_local(path, body, *, headers, timeout_seconds=7200.0, request_id=""):
        posted["path"] = path
        posted["body"] = body
        return {"ok": True, "task": {"id": "task-1"}}

    monkeypatch.setattr(channel, "_post_local_api_json", fake_post_local)
    cloud = _FakeCloud(
        [
            {
                "ok": True,
                "claimed": 1,
                "items": [
                    {"id": 7, "value": "13800138000", "kind": "mobile", "username": "小王", "account_label": "抖音账号 1"}
                ],
            },
            {"ok": True, "added": 1, "released": 0},
        ]
    )
    result = asyncio.run(
        channel._run_client_workflow_action(
            "native_wechat_add_friend",
            {
                "source_mode": "server_reported_pool",
                "max_targets": 20,
                "targets": ["13700137000"],  # 选了服务端池就不该再混进本地名单
                "account_id": "pc-wechat-default",
            },
            headers={"Authorization": "Bearer t"},
            run_id="",
            cloud=cloud,
            base="https://cloud.example",
        )
    )
    assert posted["path"] == "/api/native-wechat/friends/add"
    assert posted["body"]["targets"] == ["13800138000"]
    assert cloud.calls[0]["url"].endswith("/api/wechat-contact-pool/claim")
    assert cloud.calls[0]["json"]["limit"] == 20
    assert result["targets"] == ["13800138000"]
    assert result["server_pool_items"][0]["username"] == "小王"
    assert result["server_pool_ack"]["added"] == 1
    assert cloud.calls[1]["url"].endswith("/api/wechat-contact-pool/ack")
    assert cloud.calls[1]["json"]["added"] == ["13800138000"]


def test_native_add_friend_pool_empty_skips_with_hint(monkeypatch):
    async def should_not_call_local(*_args, **_kwargs):
        raise AssertionError("池子里没有号码时不该去动本机微信")

    monkeypatch.setattr(channel, "_post_local_api_json", should_not_call_local)
    cloud = _FakeCloud([{"ok": True, "claimed": 0, "items": []}])
    result = asyncio.run(
        channel._run_client_workflow_action(
            "native_wechat_add_friend",
            {"source_mode": "server_reported_pool", "account_id": "pc-wechat-default"},
            headers={"Authorization": "Bearer t"},
            run_id="",
            cloud=cloud,
            base="https://cloud.example",
        )
    )
    assert result["skipped"] is True
    assert result["reason"] == "server_pool_empty"
    assert "自动提交好友申请" in result["message"]
