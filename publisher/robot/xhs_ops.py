"""小红书发布操作（surface 闸门 + 回读校验），复用 xiaohongshu_publish 驱动里的现成件。"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from . import surface as S
from .ops import Result, spec

XHS_PUBLISH = "xhs.publish"
XHS_EDITOR = "xhs.editor"


def _detect(page: Any) -> str:
    url = str(getattr(page, "url", "") or "")
    if "creator.xiaohongshu.com" not in url:
        return S.UNKNOWN
    if "/publish/publish" in url:
        return XHS_EDITOR
    if "login" in url or url.rstrip("/") == "https://creator.xiaohongshu.com":
        return "xhs.login"
    return XHS_PUBLISH


def _gate(page: Any, required: str, res: Result) -> bool:
    res.surface = _detect(page)
    res.before = {"surface": res.surface, "url": str(getattr(page, "url", ""))}
    if res.surface != required:
        res.layer, res.error = "surface", "wrong_surface"
        res.evidence["required_surface"] = required
        return False
    return True


@spec(name="xhs.draft_state", platform="xhs", surface="any",
      summary="小红书当前在哪一步", precondition="有页面",
      postcondition="返回 surface", readonly=True)
async def draft_state(page: Any) -> Result:
    res = Result(op="xhs.draft_state")
    res.surface = _detect(page)
    res.after = {"surface": res.surface, "url": str(getattr(page, "url", ""))}
    res.ok = res.surface != S.UNKNOWN
    if not res.ok:
        res.layer, res.error = "surface", "unknown_page"
    return res


@spec(name="xhs.upload_video", platform="xhs", surface=XHS_PUBLISH,
      summary="挂本地视频到小红书发布页", precondition="在 publish 页且文件存在",
      postcondition="出现编辑态（标题/正文可填）", params=["path"])
async def upload_video(page: Any, path: str) -> Result:
    res = Result(op="xhs.upload_video")
    if not path or not os.path.isfile(str(path)):
        res.layer, res.error = "precondition", "file_not_found"
        return res
    if not _gate(page, XHS_PUBLISH, res):
        return res
    from skills.xiaohongshu_publish import driver as xd

    try:
        file_input = await xd._xhs_wait_file_input_after_nav(page, 20000)
        if file_input is None:
            res.layer, res.error = "precondition", "file_input_not_found"
            return res
        await file_input.set_input_files(str(path))
    except Exception as exc:
        res.layer, res.error = "action", f"upload_failed:{type(exc).__name__}"
        return res
    for _ in range(25):
        await asyncio.sleep(1.0)
        try:
            body = str(await page.inner_text("body"))[:6000]
        except Exception:
            body = ""
        if "发布" in body and ("标题" in body or "正文" in body):
            res.ok = True
            res.evidence["editor_text_hit"] = True
            return res
    res.layer, res.error = "verify", "editor_not_ready"
    return res


@spec(name="xhs.set_copy", platform="xhs", surface=XHS_EDITOR,
      summary="填写小红书标题/正文", precondition="在发布编辑页",
      postcondition="标题/正文能读回", params=["title", "description"])
async def set_copy(page: Any, title: str, description: str = "") -> Result:
    res = Result(op="xhs.set_copy")
    if not _gate(page, XHS_EDITOR, res):
        return res
    from skills.xiaohongshu_publish import driver as xd

    written = {}
    if description:
        try:
            ok = await xd._xhs_fill_body_in_any_frame(page, str(description), lambda *a, **k: None)
            if ok:
                written["body"] = True
        except Exception:
            pass
    if title:
        for sel in ('input[placeholder*="标题"]', 'input[type="text"]'):
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                await loc.click(timeout=4000)
                await loc.fill(str(title), timeout=4000)
                if str(title)[:8] in (await loc.input_value(timeout=2000) or ""):
                    written["title"] = sel
                    break
            except Exception:
                continue
    res.after = {"url": str(getattr(page, "url", ""))}
    res.ok = bool(written) and (not title or "title" in written)
    res.evidence["written"] = written
    if not res.ok:
        res.layer, res.error = "action", "copy_not_written"
    return res


@spec(name="xhs.publish", platform="xhs", surface=XHS_EDITOR,
      summary="点发布（dry_run 只验证按钮可用）", precondition="在编辑页",
      postcondition="dry_run: 按钮可用；real: 出现发布成功", destructive=True,
      params=["dry_run"])
async def publish(page: Any, dry_run: bool = True, timeout_s: float = 90.0) -> Result:
    res = Result(op="xhs.publish")
    if not _gate(page, XHS_EDITOR, res):
        return res
    from skills.xiaohongshu_publish import driver as xd

    try:
        btn = await xd._xhs_pick_best_publish_button(page)
    except Exception:
        btn = None
    if btn is None:
        res.layer, res.error = "precondition", "publish_button_not_found"
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
        try:
            body = str(await page.inner_text("body"))[:6000]
        except Exception:
            body = ""
        if "发布成功" in body or "已发布" in body:
            res.ok = True
            return res
    res.layer, res.error = "verify", "publish_not_confirmed"
    return res
