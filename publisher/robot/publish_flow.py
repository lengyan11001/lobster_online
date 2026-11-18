"""抖音发布流程（robot 优先）：把 6 个 op 串成一条可回退的链。

设计：
- 每一步独立验证，失败立刻停，把失败层带回给调用方；
- 全部成功才算发布成功（并用作品管理页回读标题做硬证据）；
- 任何一步失败 → 调用方回退到老的 driver.publish（不改变对外契约）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


async def douyin_publish_flow(
    page: Any,
    *,
    file_path: str,
    title: str,
    description: str = "",
    schedule: str = "",
    declaration: str = "",
    cover: bool = True,
    dry_run: bool = False,
    timeout_s: float = 60.0,
) -> Dict[str, Any]:
    from . import douyin_ops as D

    steps: List[Dict[str, Any]] = []

    async def run(name: str, coro: Any, *, fatal: bool = True) -> bool:
        try:
            res = await coro
        except Exception as exc:  # 网络/页面异常都当成失败层
            steps.append({"op": name, "ok": False, "error": f"{type(exc).__name__}"})
            return not fatal
        steps.append({"op": name, "ok": bool(res.ok),
                      "layer": res.layer, "error": res.error,
                      "evidence": res.evidence})
        if not res.ok:
            logger.warning("[ROBOT][douyin] %s failed layer=%s err=%s", name, res.layer, res.error)
            return not fatal
        return True

    # 一律先回到干净的上传页：
    # 1) 页面可能停在首页（发布主流程里登录检测会跳转）
    # 2) 也可能留着上一轮的编辑页草稿 —— 那种情况绝不能接着发，会发错内容
    upload_url = "https://creator.douyin.com/creator-micro/content/upload"
    try:
        await page.goto(upload_url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(2.5)
    except Exception as exc:
        steps.append({"op": "goto_upload", "ok": False, "error": type(exc).__name__})
        return {"ok": False, "path": "robot", "layer": "surface",
                "error": "nav_failed", "steps": steps}
    state = await D.draft_state(page)
    steps.append({"op": "douyin.draft_state", "ok": state.ok, "surface": state.surface, "fresh": True})
    if not state.ok or state.surface != "douyin.upload":
        return {"ok": False, "path": "robot", "layer": "surface",
                "error": f"not_on_upload:{state.surface}", "steps": steps}

    if not await run("douyin.upload_video", D.upload_video(page, file_path)):
        return {"ok": False, "path": "robot", "layer": "upload", "steps": steps}

    if not await run("douyin.set_copy", D.set_copy(page, title, description)):
        return {"ok": False, "path": "robot", "layer": "copy", "steps": steps}

    if cover:
        # 封面失败不算致命（抖音会用默认帧），但要在结果里留痕
        await run("douyin.set_cover", D.set_cover(page, require=False), fatal=False)

    if declaration:
        mode = "declare" if declaration not in {"", "无需添加自主声明"} else "none"
        await run("douyin.set_declaration", D.set_declaration(page, mode, declaration), fatal=False)

    if schedule:
        if not await run("douyin.set_schedule", D.set_schedule(page, schedule)):
            return {"ok": False, "path": "robot", "layer": "schedule", "steps": steps}

    if dry_run:
        ok = await run("douyin.publish", D.publish(page, dry_run=True), fatal=False)
        return {"ok": bool(ok), "path": "robot", "dry_run": True, "steps": steps}

    if not await run("douyin.publish", D.publish(page, dry_run=False, timeout_s=timeout_s)):
        return {"ok": False, "path": "robot", "layer": "publish", "steps": steps}

    # 硬证据（best-effort）：作品管理页回读标题。
    # 注意：publish 已经成功了，回读失败**不能**让调用方回退重发，否则就是重复发布。
    verified = False
    try:
        from .douyin_ops import verify_in_manage  # type: ignore

        ok_manage = await verify_in_manage(page, title)
        verified = bool(ok_manage.ok)
        steps.append({"op": "douyin.verify_in_manage", "ok": verified,
                      "evidence": ok_manage.evidence})
    except Exception as exc:
        steps.append({"op": "douyin.verify_in_manage", "ok": False, "error": type(exc).__name__})

    return {"ok": True, "path": "robot", "published": True, "verified": verified, "steps": steps}
