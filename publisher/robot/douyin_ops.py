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


def _noop(*_a: Any, **_k: Any) -> None:
    """驱动里的 _step 回调：op 层不需要再打日志。"""
    return None


@spec(name="douyin.set_cover", platform="douyin", surface=S.DOUYIN_EDITOR,
      summary="走封面流程（横封面入口→竖封面→完成）", precondition="在编辑页",
      postcondition="不再提示「横/竖双封面缺失」", params=["require"])
async def set_cover(page: Any, require: bool = True) -> Result:
    res = Result(op="douyin.set_cover")
    if not _check(page, S.DOUYIN_EDITOR, res):
        return res
    from skills.douyin_publish import driver as dd

    try:
        ok = await dd._douyin_run_cover_flow(page, _noop, require=bool(require))
    except Exception as exc:
        res.layer, res.error = "action", f"cover_failed:{type(exc).__name__}"
        return res
    res.evidence["flow_ok"] = bool(ok)
    try:
        body = str(await page.inner_text("body"))[:6000]
    except Exception:
        body = ""
    missing = "双封面缺失" in body
    res.evidence["cover_missing_warning"] = missing
    res.ok = bool(ok) and not missing
    if not res.ok:
        res.layer, res.error = "verify", "cover_not_set"
    return res


@spec(name="douyin.set_schedule", platform="douyin", surface=S.DOUYIN_EDITOR,
      summary="设置定时发布（立即/定时二选一）", precondition="在编辑页",
      postcondition="页面出现定时发布时间", params=["when"])
async def set_schedule(page: Any, when: str) -> Result:
    res = Result(op="douyin.set_schedule")
    if not _check(page, S.DOUYIN_EDITOR, res):
        return res
    value = str(when or "").strip()
    if not value:
        res.layer, res.error = "precondition", "empty_time"
        return res
    from skills.douyin_publish import driver as dd

    try:
        ok = await dd._douyin_apply_publish_time(page, {"publish_time": value}, _noop)
    except Exception as exc:
        res.layer, res.error = "action", f"schedule_failed:{type(exc).__name__}"
        return res
    try:
        body = str(await page.inner_text("body"))[:8000]
    except Exception:
        body = ""
    hit = ("定时发布" in body) and (value[-5:] in body or value[:10] in body)
    res.evidence.update({"applied": bool(ok), "time": value, "page_hit": hit})
    res.ok = bool(ok) and hit
    if not res.ok:
        res.layer, res.error = "verify", "schedule_not_confirmed"
    return res


@spec(name="douyin.set_declaration", platform="douyin", surface=S.DOUYIN_EDITOR,
      summary="设置自主声明（AI 生成/自行声明/无需）", precondition="在编辑页",
      postcondition="页面出现所选声明文本", params=["mode", "text"])
async def set_declaration(page: Any, mode: str = "ai", text: str = "") -> Result:
    res = Result(op="douyin.set_declaration")
    if not _check(page, S.DOUYIN_EDITOR, res):
        return res
    from skills.douyin_publish import driver as dd

    opts: Dict[str, Any] = {"douyin_declaration_mode": str(mode or "ai")}
    if text:
        opts["douyin_declaration_text"] = str(text)
    try:
        applied = await dd._douyin_apply_self_declaration(page, opts, _noop, "op")
    except Exception as exc:
        res.layer, res.error = "action", f"declaration_failed:{type(exc).__name__}"
        return res
    try:
        body = str(await page.inner_text("body"))[:8000]
    except Exception:
        body = ""
    chosen = str(text or "内容由AI生成")
    res.evidence.update({"applied": applied, "text": chosen})
    res.ok = (applied is not False) and (chosen[:4] in body)
    if not res.ok:
        res.layer, res.error = "verify", "declaration_not_confirmed"
    return res


@spec(name="douyin.verify_in_manage", platform="douyin", surface=S.DOUYIN_MANAGE,
      summary="作品管理页回读：这条标题真的发出来了", precondition="在作品管理页",
      postcondition="列表里能找到该标题", readonly=True, params=["title"])
async def verify_in_manage(page: Any, title: str, timeout_s: float = 25.0) -> Result:
    res = Result(op="douyin.verify_in_manage")
    if not _check(page, S.DOUYIN_MANAGE, res):
        return res
    want = str(title or "").strip()
    if not want:
        res.layer, res.error = "precondition", "empty_title"
        return res
    # 新作品进列表有延迟：轮询 + 偶尔刷新，别一上来就判失败
    deadline = asyncio.get_event_loop().time() + max(5.0, float(timeout_s))
    attempt = 0
    last_body = ""
    while asyncio.get_event_loop().time() < deadline:
        attempt += 1
        try:
            last_body = str(await page.inner_text("body"))
        except Exception:
            last_body = ""
        idx = last_body.find(want)
        if idx >= 0:
            res.evidence.update({"found": True, "attempts": attempt,
                                 "row_excerpt": last_body[max(0, idx - 20): idx + 60].replace("\n", " ")})
            res.ok = True
            return res
        if attempt in (3, 6):
            try:
                await page.reload(wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2.0)
            except Exception:
                pass
        await asyncio.sleep(2.0)
    res.evidence["found"] = False
    res.layer, res.error = "verify", "title_not_found"
    return res
