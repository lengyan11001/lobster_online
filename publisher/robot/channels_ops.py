"""视频号发布操作（surface 闸门 + 回读校验），复用 wechat_channels_publish 驱动里的现成件。"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from . import surface as S
from .ops import Result, spec

CHANNELS_UPLOAD = "channels.upload"
CHANNELS_EDITOR = "channels.editor"


def _detect(page: Any) -> str:
    url = str(getattr(page, "url", "") or "")
    if "channels.weixin.qq.com" not in url:
        return S.UNKNOWN
    if "/platform/post/create" in url:
        return CHANNELS_EDITOR
    if "/login" in url:
        return "channels.login"
    if "/platform" in url:
        return CHANNELS_UPLOAD
    return S.UNKNOWN


def _gate(page: Any, required: str, res: Result) -> bool:
    res.surface = _detect(page)
    res.before = {"surface": res.surface, "url": str(getattr(page, "url", ""))}
    if res.surface != required:
        res.layer, res.error = "surface", "wrong_surface"
        res.evidence["required_surface"] = required
        return False
    return True


@spec(name="channels.draft_state", platform="channels", surface="any",
      summary="视频号当前在哪一步（上传/编辑/登录）", precondition="有页面",
      postcondition="返回 surface", readonly=True)
async def draft_state(page: Any) -> Result:
    res = Result(op="channels.draft_state")
    res.surface = _detect(page)
    res.after = {"surface": res.surface, "url": str(getattr(page, "url", ""))}
    res.ok = res.surface != S.UNKNOWN
    if not res.ok:
        res.layer, res.error = "surface", "unknown_page"
    return res


@spec(name="channels.upload_video", platform="channels", surface=CHANNELS_UPLOAD,
      summary="挂本地视频到视频号发布页", precondition="在 post/create 页且文件存在",
      postcondition="编辑器出现（发表可用/出现视频）", params=["path"])
async def upload_video(page: Any, path: str) -> Result:
    res = Result(op="channels.upload_video")
    if not path or not os.path.isfile(str(path)):
        res.layer, res.error = "precondition", "file_not_found"
        return res
    if not _gate(page, CHANNELS_UPLOAD, res):
        return res
    from skills.wechat_channels_publish import driver as cd

    try:
        file_input = await cd._find_file_input(page)
        if file_input is None:
            res.layer, res.error = "precondition", "file_input_not_found"
            return res
        await cd._upload_media_file(page, file_input, str(path))
    except Exception as exc:
        res.layer, res.error = "action", f"upload_failed:{type(exc).__name__}"
        return res
    for _ in range(20):
        await asyncio.sleep(1.0)
        text = await cd._deep_text(page)
        if "发表" in text and ("秒" in text or "视频" in text):
            res.after = {"url": str(getattr(page, "url", ""))}
            res.ok = True
            res.evidence["editor_text_hit"] = True
            return res
    res.layer, res.error = "verify", "editor_not_ready"
    return res


@spec(name="channels.set_copy", platform="channels", surface=CHANNELS_EDITOR,
      summary="填写视频号描述", precondition="在视频号编辑页",
      postcondition="描述框能读回该文本", params=["description"])
async def set_copy(page: Any, description: str, title: str = "") -> Result:
    res = Result(op="channels.set_copy")
    if not _gate(page, CHANNELS_EDITOR, res):
        return res
    value = str(description or title or "").strip()
    if not value:
        res.layer, res.error = "precondition", "empty_text"
        return res
    written = ""
    for sel in ('div[contenteditable="true"]', "textarea", 'input[type="text"]'):
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            await loc.click(timeout=4000)
            await loc.fill(value, timeout=4000)
            written = sel
            break
        except Exception:
            continue
    if not written:
        res.layer, res.error = "action", "description_not_written"
        return res
    try:
        body = str(await page.inner_text("body"))[:6000]
    except Exception:
        body = ""
    res.after = {"url": str(getattr(page, "url", ""))}
    res.ok = value[:8] in body
    res.evidence["selector"] = written
    if not res.ok:
        res.layer, res.error = "verify", "copy_not_visible"
    return res


@spec(name="channels.publish", platform="channels", surface=CHANNELS_EDITOR,
      summary="点发表（dry_run 只验证按钮可用）", precondition="在编辑页",
      postcondition="dry_run: 按钮存在且可点；real: 出现发布成功", destructive=True,
      params=["dry_run"])
async def publish(page: Any, dry_run: bool = True, timeout_s: float = 90.0) -> Result:
    res = Result(op="channels.publish")
    if not _gate(page, CHANNELS_EDITOR, res):
        return res
    from skills.wechat_channels_publish import driver as cd

    try:
        btn = await cd._publish_button(page)
    except Exception:
        btn = None
    if btn is None:
        res.layer, res.error = "precondition", "publish_button_not_found"
        return res
    enabled = await cd._button_enabled(btn)
    res.evidence["button_enabled"] = bool(enabled)
    if not enabled:
        res.layer, res.error = "precondition", "publish_button_disabled"
        return res
    if dry_run:
        res.ok = True
        res.evidence["dry_run"] = True
        return res
    try:
        await btn.click(timeout=8000)
    except Exception as exc:
        res.layer, res.error = "action", f"click_failed:{type(exc).__name__}"
        return res
    deadline = asyncio.get_event_loop().time() + max(10.0, float(timeout_s))
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(1.5)
        text = await cd._deep_text(page)
        if "发表成功" in text or "已发表" in text:
            res.ok = True
            res.after = {"url": str(getattr(page, "url", ""))}
            return res
    res.layer, res.error = "verify", "publish_not_confirmed"
    return res
