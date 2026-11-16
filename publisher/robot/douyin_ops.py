"""抖音发布操作：surface 闸门（upload/editor/manage）+ 快照/地标验证。"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict

from . import surface as S
from .ops import Result, spec


def _check(page: Any, required: str, res: Result) -> bool:
    surf = S.detect_browser(page)
    res.surface = surf.id
    res.before = {"surface": surf.id, "anchors": surf.anchors}
    if surf.id != required:
        res.layer, res.error = "surface", "wrong_surface"
        res.evidence["required_surface"] = required
        return False
    return True


@spec(name="douyin.draft_state", platform="douyin", surface="any",
      summary="当前在上传页/编辑页/管理页", precondition="有页面",
      postcondition="返回 surface", readonly=True)
async def draft_state(page: Any) -> Result:
    res = Result(op="douyin.draft_state")
    surf = S.detect_browser(page)
    res.surface = surf.id
    res.after = {"surface": surf.id, "anchors": surf.anchors}
    res.ok = surf.known
    if not res.ok:
        res.layer, res.error = "surface", "unknown_page"
    return res


@spec(name="douyin.upload_video", platform="douyin", surface=S.DOUYIN_UPLOAD,
      summary="挂载本地视频直到进入上传", precondition="在上传页且文件存在",
      postcondition="upload 阶段不变量成立", params=["path"])
async def upload_video(page: Any, path: str) -> Result:
    res = Result(op="douyin.upload_video")
    if not path or not os.path.isfile(str(path)):
        res.layer, res.error = "precondition", "file_not_found"
        return res
    if not _check(page, S.DOUYIN_UPLOAD, res):
        return res
    from skills.douyin_publish import driver as dd

    try:
        handle = await dd._douyin_find_video_file_input(page)
        if handle is None:
            res.layer, res.error = "precondition", "file_input_not_found"
            return res
        ok = await dd._douyin_cdp_set_file_input(page, handle, str(path))
    except Exception as exc:
        res.layer, res.error = "action", f"set_input_failed:{type(exc).__name__}"
        return res
    if not ok:
        res.layer, res.error = "action", "set_input_rejected"
        return res
    from publisher.agent.verifier import verify_stage

    for _ in range(12):
        if await verify_stage(page, "douyin", "upload"):
            res.after = {"surface": S.detect_browser(page).id, "url": str(getattr(page, "url", ""))}
            res.ok = True
            res.evidence["file"] = str(path)
            return res
        await asyncio.sleep(1.0)
    res.layer, res.error = "verify", "upload_not_verified"
    return res


@spec(name="douyin.set_copy", platform="douyin", surface=S.DOUYIN_EDITOR,
      summary="填写标题/描述", precondition="在编辑页",
      postcondition="标题 input 与描述 contenteditable 都能读回", params=["title", "description"])
async def set_copy(page: Any, title: str, description: str = "") -> Result:
    res = Result(op="douyin.set_copy")
    if not _check(page, S.DOUYIN_EDITOR, res):
        return res
    written = {}
    # 标题：普通 input（真机确认 placeholder=填写作品标题…）
    if title:
        ok = False
        loc = page.locator('input[placeholder*="填写作品标题"]').first
        try:
            if await loc.count() == 0:
                res.layer, res.error = "precondition", "title_input_not_found"
                return res
            await loc.click(timeout=4000)
            await loc.fill(str(title), timeout=4000)
            ok = str(title)[:8] in (await loc.input_value(timeout=2000) or "")
            if ok:
                written["title"] = 'input[placeholder*="填写作品标题"]'
        except Exception as exc:
            res.layer, res.error = "action", f"title_write_failed:{type(exc).__name__}"
            return res
        if not ok:
            res.layer, res.error = "action", "title_not_written"
            return res
    # 描述：编辑器是 contenteditable 的 div（真机确认 class=editor-kit-container），不是 textarea
    if description:
        ok = False
        for sel in ('.editor-kit-container [contenteditable="true"]',
                    '.editor-kit-container',
                    '[contenteditable="true"]'):
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                await loc.click(timeout=4000)
                await loc.fill(str(description), timeout=4000)
                text = await loc.inner_text(timeout=2000)
                if str(description)[:8] in (text or ""):
                    written["description"] = sel
                    ok = True
                    break
            except Exception:
                continue
        if not ok:
            res.layer, res.error = "action", "description_not_written"
            res.evidence["written"] = written
            return res
    res.after = {"url": str(getattr(page, "url", "")), "written": written}
    res.ok = True
    res.evidence["written"] = written
    return res


@spec(name="douyin.publish", platform="douyin", surface=S.DOUYIN_EDITOR,
      summary="点发布并确认落到管理页", precondition="在编辑页且标题非空",
      postcondition="url 进入 /content/manage", destructive=True, params=["dry_run"])
async def publish(page: Any, timeout_s: float = 45.0, dry_run: bool = False) -> Result:
    res = Result(op="douyin.publish")
    if not _check(page, S.DOUYIN_EDITOR, res):
        return res
    # 前置：标题必须有内容。上一版没这道闸，结果在"描述没写进去"的情况下也点了发布。
    try:
        title = await page.locator('input[placeholder*="填写作品标题"]').first.input_value(timeout=3000)
    except Exception:
        title = ""
    res.evidence["title"] = (title or "")[:40]
    if not str(title or "").strip():
        res.layer, res.error = "precondition", "title_empty"
        return res
    from publisher.agent.actions import _resolve_target

    target = await _resolve_target(page, {"by": "role", "role": "button", "name": "发布"})
    if target is None:
        res.layer, res.error = "precondition", "publish_button_not_found"
        return res
    res.evidence["button_found"] = True
    if dry_run:
        res.ok = True
        res.evidence["dry_run"] = True
        return res
    try:
        await target.click(timeout=6000)
    except Exception as exc:
        res.layer, res.error = "action", f"click_failed:{type(exc).__name__}"
        return res
    deadline = asyncio.get_event_loop().time() + max(5.0, float(timeout_s))
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(1.0)
        if "/content/manage" in str(getattr(page, "url", "")):
            res.after = {"url": str(getattr(page, "url", ""))}
            res.ok = True
            return res
    res.after = {"url": str(getattr(page, "url", ""))}
    res.layer, res.error = "verify", "publish_not_confirmed"
    return res
