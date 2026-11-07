import pathlib
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from publisher.browser_pool import (  # noqa: E402
    douyin_workbench_browser_options_from_publish_meta,
    wechat_channels_browser_options_from_publish_meta,
)
from backend.app.api.publish import _account_browser_profile_and_options  # noqa: E402


def test_wechat_channels_uses_douyin_cdp_browser():
    douyin = douyin_workbench_browser_options_from_publish_meta({})
    wechat = wechat_channels_browser_options_from_publish_meta({})

    assert douyin["douyin_cdp"] is True
    assert wechat["douyin_cdp"] is True
    assert wechat["channel"] == "chrome"
    assert wechat["cdp_start_url"] == "https://channels.weixin.qq.com/platform"
    assert wechat["viewport"] == douyin["viewport"]


def test_wechat_account_profile_options_enable_cdp():
    acct = SimpleNamespace(
        id=20,
        platform="wechat_channels",
        nickname="sph",
        browser_profile=r"D:\lobster_online\browser_data\wechat_channels_sph",
        meta={},
    )
    profile, opts = _account_browser_profile_and_options(acct)
    assert profile.endswith("wechat_channels_sph")
    assert opts["douyin_cdp"] is True
    assert opts["cdp_start_url"] == "https://channels.weixin.qq.com/platform"
    assert opts["channel"] == "chrome"
