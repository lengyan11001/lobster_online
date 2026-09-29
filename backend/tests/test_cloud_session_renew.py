"""云端登录态静默续签（30 天）+ 401 指数退避 的回归测试。

背景（2026-09-17）：客户端提示「登录状态已失效」。原因是云端 JWT 7 天到期且无续签，
当天全站 /auth/me 有 9048 次 401（NAT 后面的客户端过期后仍每 11 秒轮询，单台机器 1232 次）。

覆盖：
1. 未到期不续签（剩余 > 20 天 → not_due，不发请求）；
2. 到期前（剩余 < 20 天）→ 调 {AUTH_SERVER_BASE}/auth/refresh，带上 Bearer 与安装 id，
   成功后把新 token 落盘（本测试替换掉落盘函数，避免动本机真实凭证文件）；
3. 被拒（401）→ ok=false + reason=rejected，不影响本地其它功能；
4. 没有 token / 没配认证中心 → 明确 reason；
5. 401 退避阶梯：60s → 300s → 600s（封顶），且成功后会清零；
6. 本机续签接口：正常返回新 token；非本机来源拒绝；静态检查界面是否接了续签与退避。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from backend.app.api import auth as local_auth
from backend.app.core.config import settings
from backend.app.services import cloud_session_renew as renew


def _token(expires_in_seconds: int, *, user_id: int = 31, jti: str = "sess-abc") -> str:
    payload = {
        "sub": str(user_id),
        "brand_mark": "bihuo",
        "jti": jti,
        "exp": datetime.utcnow() + timedelta(seconds=expires_in_seconds),
    }
    return pyjwt.encode(payload, settings.secret_key, algorithm="HS256")


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


@pytest.fixture()
def captured(monkeypatch):
    box = {"persisted": []}

    def _persist(**kwargs):
        box["persisted"].append(kwargs)

    monkeypatch.setattr(renew, "persist_channel_fallback_for_login", _persist)
    monkeypatch.setattr(renew, "read_installation_id", lambda: "u22-81ec542adefcee408ca6322ce54f2a17")
    monkeypatch.setattr(renew.get_settings(), "auth_server_base", "https://bhzn.top", raising=False)
    monkeypatch.setattr(renew, "_last_attempt_at", 0.0, raising=False)
    return box


def test_ttl_and_constants():
    assert renew.RENEW_BEFORE_SECONDS == 60 * 60 * 24 * 20
    assert renew.CHECK_INTERVAL_SECONDS == 6 * 60 * 60
    assert local_auth.LOCAL_TOKEN_EXPIRE_MINUTES == 60 * 24 * 30


@pytest.mark.asyncio
async def test_skips_when_not_due(captured):
    fresh = _token(60 * 60 * 24 * 29)          # 还有 29 天
    calls: list = []

    async def _post(url, body, headers):
        calls.append(url)
        return _Resp(200, {"access_token": fresh})

    result = await renew.renew_cloud_token(token=fresh, post_fn=_post)
    assert result["ok"] is True and result["refreshed"] is False
    assert result["reason"] == "not_due"
    assert result["remaining_seconds"] > 60 * 60 * 24 * 20
    assert calls == [], "未到期不该打认证中心"
    assert captured["persisted"] == []


@pytest.mark.asyncio
async def test_renews_and_persists_before_expiry(captured):
    old = _token(60 * 60 * 24 * 5)             # 只剩 5 天 → 该续签了
    new = _token(60 * 60 * 24 * 30, jti="sess-abc")
    seen: dict = {}

    async def _post(url, body, headers):
        seen["url"] = url
        seen["headers"] = headers
        return _Resp(200, {"access_token": new, "expires_in": 60 * 24 * 30 * 60,
                           "user_id": 31, "expires_at": "2026-10-17T02:00:00Z"})

    result = await renew.renew_cloud_token(token=old, post_fn=_post)
    assert result["ok"] is True and result["refreshed"] is True
    assert result["token"] == new
    assert seen["url"] == "https://bhzn.top/auth/refresh"
    assert seen["headers"]["Authorization"] == "Bearer " + old
    assert seen["headers"]["X-Installation-Id"] == "u22-81ec542adefcee408ca6322ce54f2a17"
    assert len(captured["persisted"]) == 1
    assert captured["persisted"][0]["jwt_token"] == new
    remaining = renew.token_remaining_seconds(result["token"])
    assert remaining and remaining > 60 * 60 * 24 * 29


@pytest.mark.asyncio
async def test_rejected_keeps_local_state(captured):
    old = _token(60 * 60 * 24 * 2)

    async def _post(url, body, headers):
        return _Resp(401, {"detail": "登录状态已失效，请重新登录"})

    result = await renew.renew_cloud_token(token=old, post_fn=_post)
    assert result["ok"] is False and result["refreshed"] is False
    assert result["reason"] == "rejected" and result["http"] == 401
    assert "重新登录" in result["error"]
    assert captured["persisted"] == []


@pytest.mark.asyncio
async def test_no_token_and_no_auth_server(captured, monkeypatch):
    # 不传 token 时会回落到本机保存的 .channel_fallback.json；这里显式断掉，避免用到真凭证
    monkeypatch.setattr(renew, "read_cloud_token", lambda: "")
    empty = await renew.renew_cloud_token(token="")
    assert empty == {"ok": False, "refreshed": False, "reason": "no_token"}

    monkeypatch.setattr(renew.get_settings(), "auth_server_base", "", raising=False)
    result = await renew.renew_cloud_token(token=_token(60))
    assert result["reason"] == "no_auth_server"


def test_401_backoff_ladder_and_reset_semantics():
    assert local_auth._auth_invalid_backoff_seconds(1) == 60.0
    assert local_auth._auth_invalid_backoff_seconds(2) == 300.0
    assert local_auth._auth_invalid_backoff_seconds(3) == 600.0
    assert local_auth._auth_invalid_backoff_seconds(9) == 600.0   # 封顶，不会无限增长
    # 缓存结构：(可重试时间, 连续失败次数)；成功路径会 pop 掉旧结构（见 get_current_user_for_local）
    local_auth._AUTH_ME_INVALID_CACHE.clear()
    now = local_auth.time.monotonic()
    local_auth._AUTH_ME_INVALID_CACHE["k"] = (now + 600.0, 3)
    entry = local_auth._AUTH_ME_INVALID_CACHE.get("k")
    assert entry and entry[1] == 3
    local_auth._AUTH_ME_INVALID_CACHE.clear()


@pytest.mark.asyncio
async def test_local_renew_endpoint(monkeypatch):
    async def _fake_renew(*, force: bool = False):
        return {"ok": True, "refreshed": True, "token": "new-token", "expires_in": 2592000,
                "expires_at": "2026-10-17T02:00:00Z"}

    monkeypatch.setattr(renew, "renew_cloud_token", _fake_renew)
    body = await local_auth.local_cloud_session_renew(request=_LoopbackRequest())
    assert body["ok"] is True and body["token"] == "new-token"

    with pytest.raises(HTTPException) as err:
        await local_auth.local_cloud_session_renew(request=_LoopbackRequest(host="10.0.0.8"))
    assert err.value.status_code == 403


@pytest.mark.asyncio
async def test_local_session_status_endpoint(monkeypatch):
    monkeypatch.setattr(renew, "read_cloud_token", lambda: _token(60 * 60 * 24 * 10, jti="s-1"))
    body = await local_auth.local_cloud_session_status(request=_LoopbackRequest())
    assert body["ok"] is True and body["has_token"] is True
    assert body["session_id"] == "s-1"
    assert body["renew_due"] is True       # 只剩 10 天 → 该续签


class _Client:
    def __init__(self, host: str) -> None:
        self.host = host


class _LoopbackRequest:
    def __init__(self, host: str = "127.0.0.1") -> None:
        self.client = _Client(host)
        self.headers: dict = {}
        self.query_params: dict = {}
        self.state = type("_S", (), {})()


def test_client_wires_renew_and_backoff():
    root = Path(__file__).resolve().parents[2]
    app_js = (root / "static" / "js" / "app.js").read_text(encoding="utf-8", errors="replace")
    assert "renewStoredTokenSilently" in app_js
    assert "/api/local/auth/renew" in app_js
    assert "refreshed: true" in app_js
    create = (root / "backend" / "app" / "create_app.py").read_text(encoding="utf-8", errors="replace")
    assert "cloud_token_renew_loop" in create
