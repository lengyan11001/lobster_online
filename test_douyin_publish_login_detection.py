import pytest

from skills.douyin_publish.driver import DouyinDriver


class FakePage:
    url = "https://creator.douyin.com/creator-micro/content/upload"
    frames = []

    def __init__(self):
        self.goto_calls = []

    async def goto(self, url, **_kwargs):
        self.goto_calls.append(url)
        self.url = url
        return None

    async def query_selector(self, selector):
        if selector == 'text="作品管理"':
            return object()
        return None

    async def query_selector_all(self, selector):
        return []

    async def evaluate(self, script):
        return {
            "url": self.url,
            "bodyText": "抖音创作者中心 我是创作者 扫码登录 验证码登录 手机号登录 登录",
        }


@pytest.mark.asyncio
async def test_douyin_check_login_rejects_creator_qr_login_page():
    page = FakePage()

    assert await DouyinDriver().check_login(page, navigate=True) is False
