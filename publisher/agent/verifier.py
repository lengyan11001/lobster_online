"""Stage invariants: verify an action really took effect."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)

Verifier = Callable[[Any, Dict[str, Any]], Awaitable[bool]]


async def _text_visible(page: Any, text: str) -> bool:
    try:
        return (await page.get_by_text(text, exact=False).count()) > 0
    except Exception:
        return False


async def _role_visible(page: Any, role: str, name: str) -> bool:
    try:
        return (await page.get_by_role(role, name=name, exact=False).count()) > 0
    except Exception:
        return False


async def verify_expect(page: Any, expect: Optional[Dict[str, Any]], *, platform: str = "", stage: str = "") -> bool:
    expect = dict(expect or {})
    kind = str(expect.get("kind") or "").strip().lower()
    if not kind or kind == "any":
        return True
    if kind == "text":
        return await _text_visible(page, str(expect.get("value") or ""))
    if kind == "role":
        return await _role_visible(page, str(expect.get("role") or "button"), str(expect.get("name") or expect.get("value") or ""))
    if kind == "url_contains":
        try:
            return str(expect.get("value") or "") in str(page.url or "")
        except Exception:
            return False
    if kind in {"element_count", "count"}:
        selector = str(expect.get("selector") or "")
        want = int(expect.get("min") or expect.get("value") or 1)
        try:
            return (await page.locator(selector).count()) >= want
        except Exception:
            return False
    if kind in {"state", "file_chooser_or_upload_started", "upload_started"}:
        # 模型自造的状态断言：等价于“素材已挂载”
        return await verify_expect(page, {"kind": "media_attached"}, platform=platform, stage=stage)
    if kind == "media_attached":
        try:
            if platform == "wechat_channels":
                return (await page.get_by_text("删除", exact=True).count()) > 0
            if (await page.locator("video").count()) > 0:
                return True
            return (await page.get_by_text("删除", exact=True).count()) > 0
        except Exception:
            return False
    logger.warning("[agent] unknown expect kind=%s -> unverified", kind)
    return False


async def verify_stage(page: Any, platform: str, stage: str) -> bool:
    stage = str(stage or "").lower()
    if stage == "upload":
        if await verify_expect(page, {"kind": "media_attached"}, platform=platform, stage=stage):
            return True
        # 抖音：素材已挂上并进入编辑页/解析中，等价于“已进入上传流程”
        for text in ("重新上传", "上传成功", "文件解析中", "作品描述", "设置封面"):
            if await _text_visible(page, text):
                return True
        return False
    if stage == "publish":
        for text in ("发布成功", "已发布", "作品管理", "审核中"):
            if await _text_visible(page, text):
                return True
        return False
    return True
