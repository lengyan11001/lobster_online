from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "douyin_origin"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.douyin_origin import douyin_comment_scraper as scraper  # noqa: E402


class _CaptchaPage:
    def __init__(self, url: str = "", title: str = "", text: str = "") -> None:
        self.url = url
        self._title = title
        self._text = text

    async def title(self) -> str:
        return self._title

    async def evaluate(self, _script: str) -> str:
        return self._text


def test_captcha_page_detected_by_url_title_and_text():
    fake_self = SimpleNamespace()
    check = scraper.DouyinCommentScraper._is_captcha_page
    assert asyncio.run(check(fake_self, _CaptchaPage(url="https://rmc.bytedance.com/verifycenter/captcha/v2"))) is True
    assert asyncio.run(check(fake_self, _CaptchaPage(title="验证码中间页"))) is True
    assert asyncio.run(check(fake_self, _CaptchaPage(text="请完成下列验证后继续"))) is True
    assert asyncio.run(check(fake_self, _CaptchaPage(url="https://www.douyin.com/jingxuan", title="抖音精选", text="推荐视频"))) is False
