"""WhatsApp 回复生成的服务端地址必须和微信侧一致（否则 404）。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_whatsapp_engine as wa  # noqa: E402
from backend.app.services import native_wechat_engine as wx  # noqa: E402


def test_proxy_base_matches_wechat_and_is_not_h5():
    """实测：h5.bhzn.top 上 /api/sutui-chat/completions 是 404，主站才是 401（接口在）。"""
    whatsapp_base = wa._server_proxy_base()
    wechat_base = wx._server_proxy_base()
    assert whatsapp_base == wechat_base, "两边必须用同一个服务端地址"
    assert "h5.bhzn.top" not in whatsapp_base
    assert whatsapp_base.endswith("bhzn.top")


def test_reply_endpoint_path():
    url = wa._server_proxy_base() + "/api/sutui-chat/completions"
    assert url.endswith("/api/sutui-chat/completions")
    assert not url.startswith("https://h5.")


def test_observe_reuses_same_base():
    source = (ROOT / "backend" / "app" / "services" / "native_whatsapp_engine.py").read_text(encoding="utf-8")
    assert "/api/wechat-intelligence/observe" in source
    assert source.count("_server_proxy_base()") >= 2, "回复与回写都要用同一个 base"
