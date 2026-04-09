"""小红书创作者平台发布驱动 — creator.xiaohongshu.com 自动化。"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import sys
from typing import Any, Dict, List, Optional

from publisher.pw_timeouts import file_input_wait_ms
from publisher.pw_timeouts import navigation_timeout_ms as _nav_ms

from skills._base import BaseDriver

logger = logging.getLogger(__name__)

LOGIN_URL = "https://creator.xiaohongshu.com"
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
PUBLISH_VIDEO_URL = "https://creator.xiaohongshu.com/publish/publish"
PUBLISH_VIDEO_URL_TARGET = "https://creator.xiaohongshu.com/publish/publish?from=menu&target=video"
# 发布图文（单图/多图）：target=image 为文字配图
PUBLISH_IMAGE_URL_TARGET = "https://creator.xiaohongshu.com/publish/publish?from=menu&target=image"
# 发布长文：target=article 为图文笔记（≥140 字等）
PUBLISH_ARTICLE_URL_TARGET = "https://creator.xiaohongshu.com/publish/publish?from=menu&target=article"


async def _human_delay(lo: float = 0.5, hi: float = 1.5):
    await asyncio.sleep(random.uniform(lo, hi))


_JS_XHS_UPLOAD_BUSY = """
() => {
  const b = (document.body && document.body.innerText) || '';
  if (b.includes('上传失败')) return 'fail';
  if (b.includes('上传中') || b.includes('正在上传') || b.includes('导入中')) return 'busy';
  return '';
}
"""


async def _xhs_iter_publish_buttons(page: Any):
    """主文档 + 各 frame 中带「发布」文案的 button。"""
    seen = []
    try:
        for el in await page.query_selector_all('button:has-text("发布")'):
            seen.append(el)
    except Exception:
        pass
    try:
        for fr in page.frames:
            try:
                for el in await fr.query_selector_all('button:has-text("发布")'):
                    seen.append(el)
            except Exception:
                continue
    except Exception:
        pass
    return seen


async def _xhs_pick_best_publish_button(page: Any) -> Any:
    """
    侧栏/浮层常有「发布」类按钮；主发布 CTA 多在编辑区底部，取可见、可点、文案贴近「发布」且纵向位置最靠下的一个。
    """
    candidates = []
    for el in await _xhs_iter_publish_buttons(page):
        try:
            if await el.is_disabled():
                continue
            if not await el.is_visible():
                continue
            raw = (await el.inner_text()).strip()
            t = raw.replace("\n", " ").strip()
            if "定时发布" in t or "存草稿" in t:
                continue
            if t != "发布" and not (t.startswith("发布") and len(t) <= 6):
                continue
            box = await el.bounding_box()
            y = float((box or {}).get("y") or 0)
            h = float((box or {}).get("height") or 0)
            score = y + h * 0.5
            cls = (await el.get_attribute("class")) or ""
            if "primary" in cls.lower() or "red" in cls.lower():
                score += 800
            candidates.append((score, el, t))
        except Exception:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    _score, best, label = candidates[0]
    logger.info("[XHS-PUBLISH] pick primary 发布 btn text=%r score=%.1f", label, _score)
    return best


async def _xhs_wait_publish_clickable(page: Any, max_wait_s: int, _step) -> bool:
    n = max(30, int(max_wait_s))
    for i in range(n):
        try:
            st = await page.evaluate(_JS_XHS_UPLOAD_BUSY)
        except Exception:
            st = ""
        if st == "fail":
            _step("检测到上传失败", False)
            return False
        if st == "busy":
            await asyncio.sleep(1)
            continue
        for btn in await _xhs_iter_publish_buttons(page):
            try:
                if await btn.is_disabled():
                    continue
                vis = await btn.is_visible()
                if vis is False:
                    continue
            except Exception:
                continue
            _step("发布按钮已可点", True, waited=i)
            return True
        await asyncio.sleep(1)
    _step("等待发布按钮可点超时", False)
    return False


async def _xhs_verify_after_publish(page: Any, _step) -> bool:
    """点击发布后：失败文案则 False；成功/审核中文案或离开编辑 URL 则 True；超时则 False。"""
    pre_url = ""
    try:
        pre_url = page.url or ""
    except Exception:
        pass
    for i in range(35):
        await asyncio.sleep(1.2)
        try:
            url = page.url or ""
        except Exception:
            url = ""
        if pre_url and url and url != pre_url and "xiaohongshu.com" in url:
            if "/login" not in url:
                _step("发布后页面已跳转", True, url=url[:120])
                return True
        try:
            jr = await page.evaluate(
                """
                () => {
                  const b = (document.body && document.body.innerText) || '';
                  if (b.includes('发布失败') || b.includes('上传失败')) return { s: 'fail' };
                  if (b.includes('笔记发布成功') || b.includes('发布成功') || b.includes('提交成功')
                      || b.includes('审核中') || b.includes('进入审核')) return { s: 'ok' };
                  return { s: 'unknown' };
                }
                """
            )
        except Exception:
            jr = {"s": "unknown"}
        if jr.get("s") == "fail":
            _step("发布后检测到失败提示", False)
            return False
        if jr.get("s") == "ok":
            _step("发布后检测到成功/审核提示", True)
            return True
    _step("发布后未检测到明确结果", False)
    return False


def _xhs_skip_body_dtext_placeholder(ph: str) -> bool:
    """视频发布页上「地点/群聊/类型声明」等也用 d-text 样式，点击会被浮层拦截，需跳过。"""
    s = (ph or "").strip()
    if not s:
        return False
    if "填写标题" in s:
        return False
    bad_sub = (
        "群聊",
        "添加地点",
        "选择合集",
        "关联群聊",
        "谁可以看",
        "内容类型",
        "类型声明",
        "添加内容",
    )
    return any(b in s for b in bad_sub)


async def _xhs_meaningful_dtext_target(el: Any) -> bool:
    """探测中 placeholder 为空且宽度约 4px 的 input.d-text 为幽灵控件，勿当正文框。"""
    try:
        box = await el.bounding_box()
        if box is not None and float(box.get("width") or 0) < 12:
            return False
    except Exception:
        pass
    return True


async def _xhs_try_prose_mirror_in_root(root: Any, page: Any, desc_use: str, _step) -> bool:
    """正文多为 Tiptap/ProseMirror contenteditable，优先于 input.d-text。"""
    selectors = (
        'div.tiptap.ProseMirror[contenteditable="true"]',
        'div.ProseMirror[contenteditable="true"][role="textbox"]',
        'div.ProseMirror[contenteditable="true"]',
    )
    for sel in selectors:
        try:
            el = await root.query_selector(sel)
        except Exception:
            el = None
        if not el:
            continue
        try:
            if not await el.is_visible():
                continue
        except Exception:
            continue
        try:
            await el.click(timeout=15000, force=True)
            await asyncio.sleep(0.12)
            if sys.platform == "darwin":
                await page.keyboard.press("Meta+A")
            else:
                await page.keyboard.press("Control+A")
            await asyncio.sleep(0.05)
            await page.keyboard.press("Backspace")
            await page.keyboard.insert_text(desc_use)
            await asyncio.sleep(0.25)
            head = (desc_use.strip().replace("\n", " ")[:40] or "").strip()
            min_len = max(1, len((desc_use or "").strip()))
            ok = await el.evaluate(
                """(el, pack) => {
                  const head = pack.head;
                  const minLen = pack.minLen;
                  const t = ((el.innerText || '') + '').replace(/\\s+/g, ' ').trim();
                  if (!t.length) return false;
                  if (!head || head.length < 2) return true;
                  const h = head.slice(0, 24);
                  return t.includes(h) || t.length >= Math.max(6, Math.floor(minLen * 0.35));
                }""",
                {"head": head, "minLen": min_len},
            )
            if not ok:
                logger.debug("[XHS] ProseMirror innerText 校验未过，尝试下一选择器")
                continue
            _step("填写正文", True)
            return True
        except Exception as e:
            logger.debug("[XHS] ProseMirror fill failed: %s", e)
            continue
    return False


def _xhs_merge_tags_into_description(description: str, tags: str, max_len: int = 1000) -> str:
    """小红书笔记正文常与话题同区；tags 仅传 MCP 时若不合并，页面上会像「没有文案」。"""
    d = (description or "").strip()
    parts = [t.strip() for t in (tags or "").split(",") if t.strip()]
    if not parts:
        return d[:max_len]
    suffix = " " + " ".join(f"#{p}" for p in parts)
    combined = (d + suffix).strip() if d else suffix.strip()
    return combined[:max_len]


async def _xhs_fill_body_in_any_frame(page: Any, desc_use: str, _step) -> bool:
    """
    正文：优先各 frame 内 **Tiptap ProseMirror**（contenteditable），再退化为 input.d-text / textarea。
    d-text 需跳过标题框、地点/群聊/类型声明等下拉占位，以及极窄幽灵 input（探测宽约 4px）。
    """
    if not (desc_use or "").strip():
        _step("填写正文", False, error="正文内容为空（勿在未传 description/tags 且无标题兜底时调用）")
        return False

    roots: List[Any] = [page]
    try:
        for fr in getattr(page, "frames", []) or []:
            if fr not in roots:
                roots.append(fr)
    except Exception:
        pass

    for root in roots:
        if await _xhs_try_prose_mirror_in_root(root, page, desc_use, _step):
            return True

        try:
            dtexts = await root.query_selector_all("input.d-text")
        except Exception:
            dtexts = []

        title_idx = -1
        for i, el in enumerate(dtexts):
            try:
                ph = (await el.get_attribute("placeholder")) or ""
            except Exception:
                ph = ""
            if "填写标题" in ph:
                title_idx = i
                break

        start_j = title_idx + 1 if title_idx >= 0 else 1
        for j in range(start_j, len(dtexts)):
            el = dtexts[j]
            try:
                ph_j = (await el.get_attribute("placeholder")) or ""
            except Exception:
                ph_j = ""
            if _xhs_skip_body_dtext_placeholder(ph_j):
                continue
            if not (ph_j or "").strip() and not await _xhs_meaningful_dtext_target(el):
                continue
            try:
                if not await el.is_visible():
                    continue
            except Exception:
                continue
            try:
                await el.click(timeout=5000)
                await el.fill("")
                await el.fill(desc_use)
                _step("填写正文", True)
                return True
            except Exception as e:
                logger.debug("[XHS] body d-text[%s] fill failed: %s", j, e)
                continue

        for el in dtexts:
            try:
                ph = (await el.get_attribute("placeholder")) or ""
                if "填写标题" in ph:
                    continue
                if _xhs_skip_body_dtext_placeholder(ph):
                    continue
                if not (ph or "").strip() and not await _xhs_meaningful_dtext_target(el):
                    continue
                if not await el.is_visible():
                    continue
                await el.click(timeout=5000)
                await el.fill("")
                await el.fill(desc_use)
                _step("填写正文", True)
                return True
            except Exception:
                continue

        try:
            for el in await root.query_selector_all("textarea"):
                try:
                    if not await el.is_visible():
                        continue
                    ph = (await el.get_attribute("placeholder")) or ""
                    if "标题" in ph:
                        continue
                    await el.fill(desc_use)
                    _step("填写正文", True)
                    return True
                except Exception:
                    continue
        except Exception:
            pass

    _step("填写正文", False, error="未找到可见正文框（已查主文档与 iframe）")
    logger.warning(
        "[XIAOHONGSHU] 有描述/话题文案但未写入任何输入框 len=%d",
        len(desc_use),
    )
    return False


async def _xhs_dismiss_hashtag_popover(page: Any, stage: str) -> None:
    """
    正文含 # 话题时，Tippy/推荐浮层会挡住底部「发布」点击（intercepts pointer events）。
    多次 Escape、移除 tippy 根节点、再点标题框让正文失焦。
    """
    try:
        for _ in range(4):
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.12)
    except Exception:
        pass
    try:
        roots: List[Any] = [page]
        for fr in getattr(page, "frames", []) or []:
            if fr not in roots:
                roots.append(fr)
        for root in roots:
            try:
                n = await root.evaluate("""
                () => {
                    let n = 0;
                    document.querySelectorAll('[data-tippy-root], [id^="tippy-"]').forEach(el => {
                        try { el.remove(); n++; } catch (e) {}
                    });
                    return n;
                }
                """)
                if n:
                    logger.info("[XHS-PUBLISH] dismiss tippy removed=%d stage=%s", n, stage)
            except Exception:
                continue
    except Exception:
        pass
    try:
        title_el = await _query_any_frame_async(page, 'input.d-text[placeholder*="填写标题"]')
        if not title_el:
            title_el = await _query_any_frame_async(page, 'input[placeholder*="填写标题"]')
        if title_el:
            await title_el.click(timeout=5000)
            await asyncio.sleep(0.15)
    except Exception:
        pass
    await asyncio.sleep(random.uniform(0.12, 0.28))
    logger.info("[XHS-PUBLISH] hashtag popover dismiss done stage=%s", stage)


async def _query_any_frame_async(page: Any, selector: str):
    try:
        el = await page.query_selector(selector)
        if el:
            return el
    except Exception:
        pass
    try:
        for fr in page.frames:
            try:
                el = await fr.query_selector(selector)
                if el:
                    return el
            except Exception:
                continue
    except Exception:
        pass
    return None


async def _xhs_wait_file_input_after_nav(page: Any, timeout_ms: int) -> Any:
    """慢网 SPA：发布页 goto 后 file input 可能晚于 domcontentloaded 才挂载，轮询等待。"""
    deadline = asyncio.get_running_loop().time() + max(0.5, float(timeout_ms) / 1000.0)
    while asyncio.get_running_loop().time() < deadline:
        file_input = await _query_any_frame_async(page, "input.upload-input")
        if not file_input:
            file_input = await _query_any_frame_async(page, 'input[type="file"]')
        if not file_input:
            try:
                file_input = await page.query_selector('input[type="file"]')
            except Exception:
                file_input = None
        if file_input:
            return file_input
        await asyncio.sleep(0.45)
    return None


class XiaohongshuDriver(BaseDriver):
    """小红书创作者平台发布视频、图文（单图）或长文。"""

    def login_url(self) -> str:
        return LOGIN_URL

    async def _passive_login_check(self, page: Any) -> bool:
        try:
            url = (getattr(page, "url", None) or "").strip()
            if "creator.xiaohongshu.com" not in url:
                return False
            try:
                title = await page.title()
            except Exception:
                title = ""
            if "你访问的页面不见了" in (title or ""):
                return False
            if "/new/" in url or "/publish/" in url:
                return True
            # 首页可能为 /new/home，或 SPA 未更新 URL
            try:
                content = (await page.content() or "")[:2000]
            except Exception:
                content = ""
            if "login" in url or "passport" in url or "扫码" in content:
                return False
            return True
        except Exception:
            return False

    async def check_login(self, page: Any, navigate: bool = True) -> bool:
        if not navigate:
            return await self._passive_login_check(page)
        try:
            await page.goto(
                LOGIN_URL, wait_until="domcontentloaded", timeout=_nav_ms(15000)
            )
            await _human_delay(2, 3)
            if await self._passive_login_check(page):
                return True
            await page.goto(
                PUBLISH_VIDEO_URL,
                wait_until="domcontentloaded",
                timeout=_nav_ms(15000),
            )
            await _human_delay(1, 2)
            if await _query_any_frame_async(page, 'input.upload-input'):
                return True
            return False
        except Exception:
            return False

    async def publish(
        self,
        page: Any,
        file_path: str,
        title: str,
        description: str,
        tags: str,
        options: Optional[Dict[str, Any]] = None,
        cover_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        options = options or {}
        applied: Dict[str, Any] = {"steps": []}

        def _step(note: str, ok: bool, **extra):
            entry = {"action": note, "ok": ok, **extra}
            applied["steps"].append(entry)
            logger.info("[XIAOHONGSHU-PUBLISH] %s => %s %s", note, "OK" if ok else "FAIL", extra or "")

        try:
            if not os.path.isfile(file_path):
                _step("检查文件存在", False, path=file_path)
                return {"ok": False, "error": f"文件不存在: {file_path}", "applied": applied}
            _step("检查文件存在", True, size=os.path.getsize(file_path))

            file_ext = os.path.splitext(file_path)[1].lower()
            is_image = file_ext in _IMAGE_EXTS
            publish_type = (options.get("xiaohongshu_publish_type") or options.get("publish_type") or "").strip().lower()
            type_label = "长文" if (is_image and publish_type == "article") else ("图文" if is_image else "视频")
            _step("识别素材类型", True, type=type_label)

            if is_image:
                if publish_type == "article":
                    await page.goto(
                        PUBLISH_ARTICLE_URL_TARGET,
                        wait_until="domcontentloaded",
                        timeout=_nav_ms(30000),
                    )
                    await _human_delay(2, 3)
                    # 长文入口无上传框，需先点「新的创作」进入编辑
                    try:
                        btn = await page.query_selector('button:has-text("新的创作")')
                        if btn:
                            await btn.click()
                            _step("点击「新的创作」", True)
                            await _human_delay(2, 4)
                        else:
                            clicked = await page.evaluate("""() => {
                                const btns = document.querySelectorAll('button');
                                for (const b of btns) { if ((b.textContent||'').trim() === '新的创作') { b.click(); return true; } } return false;
                            }""")
                            if clicked:
                                _step("点击「新的创作」", True)
                                await _human_delay(2, 4)
                    except Exception as e:
                        logger.warning("[XIAOHONGSHU] 点击「新的创作」失败: %s", e)
                else:
                    await page.goto(
                        PUBLISH_IMAGE_URL_TARGET,
                        wait_until="domcontentloaded",
                        timeout=_nav_ms(30000),
                    )
                await _human_delay(2, 4)
            else:
                await page.goto(
                    PUBLISH_VIDEO_URL_TARGET,
                    wait_until="domcontentloaded",
                    timeout=_nav_ms(30000),
                )
                await _human_delay(2, 4)

            # 慢网：SPA 晚挂载上传控件，轮询等待（时长见 PUBLISH_FILE_INPUT_WAIT_MS / SCALE）
            file_input = await _xhs_wait_file_input_after_nav(page, file_input_wait_ms())
            if not file_input:
                if is_image and publish_type == "article":
                    _step("查找上传输入框", False)
                    return {"ok": False, "error": "长文页未检测到上传入口，请先点击「新的创作」或在网页端完成首次编辑后再试", "applied": applied}
                _step("查找上传输入框", False)
                return {"ok": False, "error": "未找到上传输入框（视频/图片）", "applied": applied}
            _step("查找上传输入框", True)

            await file_input.set_input_files(file_path)
            _step("选择本地文件", True)
            await _human_delay(1, 2)

            fsz = os.path.getsize(file_path)
            # 大文件浏览器上传更久：基础 60s + 每 4MB 多 1s，上限 600s（与抖音头条思路一致）
            wait_secs = min(600, 60 + int(fsz / (4 * 1024 * 1024)))
            _step("上传等待上限(秒)", True, wait_secs=wait_secs, file_size=fsz)

            # 等待上传完成进入编辑页（采集：placeholder「填写标题会有更多赞哦」、按钮「发布」）
            for _ in range(wait_secs):
                title_el = await _query_any_frame_async(page, 'input.d-text[placeholder*="填写标题"]')
                if not title_el:
                    title_el = await _query_any_frame_async(page, 'input[placeholder*="填写标题"]')
                if title_el:
                    break
                publish_btn = await page.query_selector('button:has-text("发布")')
                if not publish_btn and hasattr(page, "frames"):
                    for fr in page.frames:
                        try:
                            publish_btn = await fr.query_selector('button:has-text("发布")')
                            if publish_btn:
                                break
                        except Exception:
                            continue
                if publish_btn:
                    break
                await asyncio.sleep(1)
            else:
                _step("等待上传完成", False)
                return {"ok": False, "error": "上传超时，未进入编辑页", "applied": applied}
            _step("等待上传完成", True)
            await _human_delay(0.5, 1)

            # 填写标题（小红书限制约 20 字；publisher 入口已裁剪，此处再兜底）
            title_use = (title or "")[:20]
            title_el = await _query_any_frame_async(page, 'input.d-text[placeholder*="填写标题"]') or await _query_any_frame_async(page, 'input[placeholder*="填写标题"]')
            if title_el and title_use:
                await title_el.fill(title_use)
                _step("填写标题", True)
            elif title_el:
                _step("填写标题", True)
            await _human_delay(0.3, 0.6)

            # 正文/描述（保守 1000 字；合并 tags，且必须在各 frame 内查找正文框）
            desc_use = _xhs_merge_tags_into_description(description or "", tags or "", 1000)
            if not (desc_use or "").strip():
                # 定时/模型常只传 title、description 为空；小红书标题与「正文」分栏，不写入会像「无文案」
                desc_use = (title or "").strip()[:1000]
            if not (desc_use or "").strip():
                _step("正文为空", False)
                return {
                    "ok": False,
                    "error": "小红书笔记需要正文：请在 publish_content 传入 description（或 tags），至少提供可展示的标题/正文",
                    "applied": applied,
                }
            body_ok = await _xhs_fill_body_in_any_frame(page, desc_use, _step)
            if not body_ok:
                return {
                    "ok": False,
                    "error": "未能写入笔记正文（页面结构可能变更）；请检查创作者发布页或稍后重试",
                    "applied": applied,
                }
            await _human_delay(0.2, 0.5)
            if "#" in desc_use or "#" in (tags or ""):
                await _xhs_dismiss_hashtag_popover(page, "after_body")

            click_wait = min(180, max(45, wait_secs))
            if not await _xhs_wait_publish_clickable(page, click_wait, _step):
                return {
                    "ok": False,
                    "error": "素材可能仍在上传中，或发布按钮不可用，请稍后重试或手动发布",
                    "applied": applied,
                }

            # 点击发布：优先底部主按钮，避免侧栏「发布」仅切换视图不提交
            publish_btn = await _xhs_pick_best_publish_button(page)
            _xhs_has_hashtag = "#" in desc_use or "#" in (tags or "")
            if not publish_btn:
                if _xhs_has_hashtag:
                    await _xhs_dismiss_hashtag_popover(page, "before_publish_click")
                clicked = await page.evaluate("""
                    () => {
                        const btns = Array.from(document.querySelectorAll('button'));
                        let best = null;
                        let bestY = -1;
                        for (const b of btns) {
                            const t = (b.textContent || '').trim().replace(/\\s+/g, ' ');
                            if (t !== '发布') continue;
                            if (!b.offsetParent || b.disabled) continue;
                            const r = b.getBoundingClientRect();
                            const y = r.bottom;
                            if (y > bestY) { bestY = y; best = b; }
                        }
                        if (best) {
                            best.scrollIntoView({block: 'center'});
                            best.click();
                            return true;
                        }
                        return false;
                    }
                """)
                if not clicked:
                    _step("点击发布", False)
                    return {"ok": False, "error": "未找到主发布按钮", "applied": applied}
            else:
                await publish_btn.scroll_into_view_if_needed()
                await asyncio.sleep(0.35)
                if _xhs_has_hashtag:
                    await _xhs_dismiss_hashtag_popover(page, "before_publish_click")
                try:
                    await publish_btn.click(timeout=12000)
                except Exception:
                    await publish_btn.click(force=True, timeout=12000)
            _step("点击发布", True)

            try:
                post_url = page.url or ""
            except Exception:
                post_url = ""
            if not await _xhs_verify_after_publish(page, _step):
                return {
                    "ok": False,
                    "error": "已点击发布，但未检测到成功或审核提示；请到小红书创作者中心确认是否需补充操作",
                    "url": post_url,
                    "applied": applied,
                }
            return {"ok": True, "url": post_url, "applied": applied}
        except Exception as e:
            logger.exception("[XIAOHONGSHU-PUBLISH] publish failed")
            _step("异常", False, error=str(e))
            return {"ok": False, "error": str(e), "applied": applied}
