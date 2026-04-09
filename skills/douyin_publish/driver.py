"""Douyin (抖音) creator platform driver — creator.douyin.com automation."""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from typing import Any, Dict, List, Optional

from publisher.pw_timeouts import ms as _pw_ms
from publisher.pw_timeouts import navigation_timeout_ms as _nav_ms
from publisher.pw_timeouts import publish_timeout_scale as _pw_scale

from skills._base import BaseDriver

logger = logging.getLogger(__name__)

UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
HOME_URL = "https://creator.douyin.com/creator-micro/home"

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".flv"}

# 抖音创作者中心对话题/标签数量有上限（常见为 5 个），超出会导致校验失败或无法发布
_DOUYIN_MAX_TAGS = 5


def _douyin_tag_list(tags: str) -> List[str]:
    raw = [t.strip() for t in (tags or "").split(",") if t.strip()]
    if len(raw) > _DOUYIN_MAX_TAGS:
        logger.warning(
            "[DOUYIN] 标签超过平台上限 %d 个，已仅保留前 %d 个（共传入 %d 个）",
            _DOUYIN_MAX_TAGS,
            _DOUYIN_MAX_TAGS,
            len(raw),
        )
        return raw[:_DOUYIN_MAX_TAGS]
    return raw


def _douyin_video_wait_rounds(file_size: int) -> int:
    """大视频上传/跳转、转码等待更久：约每 8MB 多一轮，每轮 2s，上限 120 轮（约 4 分钟）。"""
    return min(120, 30 + int(max(0, file_size) / (8 * 1024 * 1024)))


def _douyin_image_wait_rounds(file_size: int) -> int:
    """大图上传跳转编辑页：每约 15MB 多一轮，上限 50 轮（约 100s）。"""
    return min(50, 30 + int(max(0, file_size) / (15 * 1024 * 1024)))


async def _human_delay(lo: float = 0.5, hi: float = 1.5):
    await asyncio.sleep(random.uniform(lo, hi))


# 避免视频/图片仍在传输或转码时就点「发布」导致无效提交
_JS_DOUYIN_UPLOAD_BUSY = """
() => {
  const b = (document.body && document.body.innerText) || '';
  if (b.includes('上传失败') || b.includes('转码失败')) return 'fail';
  if (b.includes('上传中') || b.includes('正在上传') || b.includes('导入中')) return 'busy';
  if (b.includes('转码中') || b.includes('压缩中')) return 'busy';
  if (b.includes('视频处理中') || b.includes('作品处理中')) return 'busy';
  return '';
}
"""


_JS_DOUYIN_COVER_STILL_REQUIRED = """
() => {
  const b = (document.body && document.body.innerText) || '';
  if (/请选择封面|必须选择封面|未选择封面|请先选择封面|封面.*必填|需.*选择.*封面/.test(b)) return true;
  // 抖音常见：未选封面时不出现整页「必须选择封面」，仅封面区「暂无封面」等
  if (/暂无封面|未设置封面|请设置封面|封面未设置|未添加封面|请添加封面|请先设置封面/.test(b)) return true;
  return false;
}
"""

_DOUYIN_COVER_HORIZONTAL_LABELS = ("设置横封面", "设置横版封面")
_DOUYIN_COVER_VERTICAL_LABELS = ("设置竖封面", "设置竖版封面")


async def _douyin_wait_setting_cover_modal_open(page: Any, timeout_ms: Optional[int] = None) -> bool:
    """设置封面弹层已出现（含 Tab 设置横封面/设置竖封面 或标题 设置封面）。"""
    tm = _pw_ms(12000) if timeout_ms is None else int(timeout_ms)
    try:
        await page.wait_for_function(
            """
            () => {
              const roots = document.querySelectorAll('[role="dialog"], .semi-modal-wrap, .semi-modal');
              for (const el of roots) {
                if (!el.offsetParent) continue;
                const t = (el.innerText || '').slice(0, 4000);
                if (t.includes('设置横封面') && t.includes('设置竖封面')) return true;
                if (t.includes('设置封面') && (t.includes('上传封面') || t.includes('封面预览'))) return true;
              }
              return false;
            }
            """,
            timeout=tm,
        )
        return True
    except Exception as ex:
        logger.debug("[DOUYIN-COVER] wait modal: %s", ex)
        return False


async def _douyin_scroll_to_cover_block(page: Any) -> None:
    """将横封面/设置封面向滚动到视口内（避免页面停在底部时找不到横封面4:3）。"""
    try:
        hit = await page.evaluate("""
        () => {
          const needles = ['横封面4:3', '横封面', '设置封面', '视频封面'];
          for (const needle of needles) {
            for (const el of document.querySelectorAll('span, div, label, p, h2, h3, h4, button, a')) {
              const t = (el.textContent || '').trim();
              if (t.length > 120) continue;
              if (t.includes(needle)) {
                el.scrollIntoView({block:'center', inline:'nearest'});
                return needle;
              }
            }
          }
          window.scrollTo(0, 0);
          return '';
        }
        """)
        logger.info("[DOUYIN-COVER] scroll_to_cover_block hit=%s", hit)
        await asyncio.sleep(0.45)
    except Exception as ex:
        logger.debug("[DOUYIN-COVER] scroll_to_cover_block: %s", ex)


async def _douyin_find_video_file_input(page: Any) -> Any:
    """
    上传页常有多个 file input（如封面图 + 视频）。第一个 query_selector 可能点到仅 accept image 的 input，
    导致视频未真正上传。
    """
    try:
        all_inputs = await page.query_selector_all('input[type="file"]')
    except Exception:
        all_inputs = []
    for fi in all_inputs:
        try:
            acc = (await fi.get_attribute("accept")) or ""
            acc_l = acc.lower()
            if "video" in acc_l or "mp4" in acc_l or "movie" in acc_l or "quicktime" in acc_l:
                logger.info("[DOUYIN-VIDEO] pick file input accept=%s", acc[:120])
                return fi
        except Exception:
            continue
    for fi in all_inputs:
        try:
            acc = (await fi.get_attribute("accept")) or ""
            acc_l = acc.lower()
            if "image" in acc_l and "video" not in acc_l:
                continue
            logger.info("[DOUYIN-VIDEO] pick file input (non-image-only) accept=%s", acc[:120])
            return fi
        except Exception:
            continue
    return all_inputs[0] if all_inputs else None


async def _douyin_click_first_visible_exact_text(page: Any, text: str) -> bool:
    loc = page.get_by_text(text, exact=True)
    n = await loc.count()
    for i in range(n):
        el = loc.nth(i)
        try:
            if await el.is_visible():
                await el.click(timeout=_pw_ms(8000))
                return True
        except Exception as ex:
            logger.debug("[DOUYIN-COVER] click %r #%s: %s", text, i, ex)
    return False


async def _douyin_click_horizontal_cover_entry(page: Any) -> bool:
    """
    打开横封面编辑弹层：新版必须先点卡片上的「选择封面」
    （DOM 常见为 div[class*='filter-'] + div.title 文案「选择封面」），点「横封面4:3」文案往往不会出弹层。
    仅当检测到「设置封面」弹层出现后才返回 True。
    """
    # 1) 优先：第一个可见「选择封面」（一般为横 4:3 卡片）
    try:
        loc = page.get_by_text("选择封面", exact=True)
        n = await loc.count()
        for i in range(n):
            el = loc.nth(i)
            try:
                if await el.is_visible():
                    await el.click(timeout=_pw_ms(8000))
                    logger.info("[DOUYIN-COVER] clicked 选择封面 text #%s", i)
                    if await _douyin_wait_setting_cover_modal_open(page):
                        return True
                    logger.warning("[DOUYIN-COVER] 选择封面 #%s 已点但未出现设置封面弹层", i)
            except Exception as ex:
                logger.debug("[DOUYIN-COVER] 选择封面 #%s: %s", i, ex)
    except Exception as ex:
        logger.debug("[DOUYIN-COVER] 选择封面: %s", ex)

    # 2) 整块 filter 容器（图标 + 选择封面）
    try:
        flt = page.locator("div[class*='filter-']").filter(has_text="选择封面").first
        if await flt.is_visible():
            await flt.click(timeout=_pw_ms(8000))
            logger.info("[DOUYIN-COVER] clicked filter block 选择封面")
            if await _douyin_wait_setting_cover_modal_open(page):
                return True
    except Exception as ex:
        logger.debug("[DOUYIN-COVER] filter block: %s", ex)

    # 3) 回退：横封面4:3 / 设置横封面 等（仍须弹层出现）
    try:
        loc = page.get_by_text(re.compile(r"横封面\s*4\s*:\s*3"))
        n = await loc.count()
        for i in range(n):
            el = loc.nth(i)
            try:
                if await el.is_visible():
                    await el.click(timeout=_pw_ms(8000))
                    logger.info("[DOUYIN-COVER] clicked 横封面4:3 (regex) #%s", i)
                    if await _douyin_wait_setting_cover_modal_open(page):
                        return True
            except Exception as ex:
                logger.debug("[DOUYIN-COVER] 横封面4:3 #%s: %s", i, ex)
    except Exception as ex:
        logger.debug("[DOUYIN-COVER] 横封面4:3 regex: %s", ex)

    if await _douyin_click_first_visible_exact_text(page, "横封面4:3"):
        if await _douyin_wait_setting_cover_modal_open(page):
            return True
    for lab in _DOUYIN_COVER_HORIZONTAL_LABELS:
        if await _douyin_click_first_visible_exact_text(page, lab):
            if await _douyin_wait_setting_cover_modal_open(page):
                return True
    try:
        loc = page.locator("text=横封面").first
        if await loc.is_visible():
            await loc.click(timeout=_pw_ms(8000))
            logger.info("[DOUYIN-COVER] clicked text 横封面")
            if await _douyin_wait_setting_cover_modal_open(page):
                return True
    except Exception as ex:
        logger.debug("[DOUYIN-COVER] 横封面 substring: %s", ex)
    return False


async def _douyin_click_vertical_cover_in_flow(page: Any) -> bool:
    """在设置封面弹层内进入竖封面：优先点顶部 Tab「设置竖封面」，再尝试底部主按钮（避免误点底部其它入口）。"""
    await asyncio.sleep(0.5)
    # 1) Tab（新版默认先进横封面 Tab，须点 Tab 切到竖封面；先于 button 避免点到错误控件却仍返回 True）
    try:
        tab = page.get_by_role("tab", name="设置竖封面", exact=True)
        if await tab.count():
            t0 = tab.first
            if await t0.is_visible():
                await t0.click(timeout=_pw_ms(8000))
                logger.info("[DOUYIN-COVER] clicked tab 设置竖封面")
                await asyncio.sleep(0.45)
                return True
    except Exception as ex:
        logger.debug("[DOUYIN-COVER] tab 设置竖封面: %s", ex)
    # 2) 弹窗内主按钮「设置竖封面」
    try:
        btn = page.get_by_role("button", name="设置竖封面", exact=True)
        n = await btn.count()
        for i in range(n):
            b = btn.nth(i)
            try:
                if await b.is_visible():
                    await b.click(timeout=_pw_ms(8000))
                    logger.info("[DOUYIN-COVER] clicked button 设置竖封面 #%s", i)
                    await asyncio.sleep(0.45)
                    return True
            except Exception as ex:
                logger.debug("[DOUYIN-COVER] button 设置竖封面 #%s: %s", i, ex)
    except Exception as ex:
        logger.debug("[DOUYIN-COVER] role=button 设置竖封面: %s", ex)
    try:
        loc = page.locator("button.semi-button-primary").filter(has_text="设置竖封面")
        n = await loc.count()
        for i in range(n):
            b = loc.nth(i)
            try:
                if await b.is_visible():
                    await b.click(timeout=_pw_ms(8000))
                    logger.info("[DOUYIN-COVER] clicked semi-button-primary 设置竖封面 #%s", i)
                    await asyncio.sleep(0.45)
                    return True
            except Exception as ex:
                logger.debug("[DOUYIN-COVER] semi primary #%s: %s", i, ex)
    except Exception as ex:
        logger.debug("[DOUYIN-COVER] semi-button-primary: %s", ex)
    # 3) 弹层内任意「设置竖封面」文案
    try:
        dlg = page.locator('[role="dialog"], .semi-modal-wrap').filter(has_text="设置封面").last
        if await dlg.count():
            inner = dlg.get_by_text("设置竖封面", exact=True).first
            if await inner.is_visible():
                await inner.click(timeout=_pw_ms(8000))
                logger.info("[DOUYIN-COVER] clicked in modal text 设置竖封面")
                return True
    except Exception as ex:
        logger.debug("[DOUYIN-COVER] modal 设置竖封面: %s", ex)
    for lab in _DOUYIN_COVER_VERTICAL_LABELS:
        if await _douyin_click_first_visible_exact_text(page, lab):
            return True
    try:
        loc = page.locator("text=竖封面").first
        if await loc.is_visible():
            await loc.click(timeout=_pw_ms(8000))
            logger.info("[DOUYIN-COVER] clicked text 竖封面")
            return True
    except Exception as ex:
        logger.debug("[DOUYIN-COVER] 竖封面 substring: %s", ex)
    return False


async def _douyin_try_click_horizontal_cover_modal_once(page: Any) -> bool:
    """单次尝试点击弹层底部「设置横封面」。由外层重试包装，缓解弹层动画/节点晚挂载导致的偶发失败。"""
    await asyncio.sleep(0.85)
    try:
        await page.evaluate(
            """
            () => {
              const roots = document.querySelectorAll(
                '.dy-creator-content-modal-wrap, [role="dialog"], .semi-modal-wrap'
              );
              for (const m of roots) {
                try {
                  if (!m.offsetParent) continue;
                  m.scrollTop = m.scrollHeight;
                } catch (e) {}
              }
              try {
                window.scrollTo(0, document.body.scrollHeight);
              } catch (e) {}
            }
            """
        )
    except Exception:
        pass
    await asyncio.sleep(0.45)

    try:
        clicked = await page.evaluate(
            """
            () => {
              function norm(s) {
                return (s || '').replace(/\\s+/g, ' ').trim();
              }
              const roots = document.querySelectorAll(
                '.dy-creator-content-modal-wrap, [role="dialog"], .semi-modal-wrap'
              );
              const candidates = [];
              for (const m of roots) {
                try {
                  if (!m.offsetParent) continue;
                } catch (e) { continue; }
                const preview = (m.innerText || '').slice(0, 900);
                if (!preview.includes('封面')) continue;
                for (const b of m.querySelectorAll('button, [role="button"]')) {
                  const t = norm(b.textContent || '');
                  if (t !== '设置横封面') continue;
                  const br = b.getBoundingClientRect();
                  if (br.width < 4 || br.height < 4) continue;
                  candidates.push({ el: b, bottom: br.bottom, right: br.right });
                }
              }
              candidates.sort((a, b) => b.bottom - a.bottom);
              for (const c of candidates) {
                try {
                  c.el.scrollIntoView({ block: 'center', inline: 'nearest' });
                } catch (e) {}
              }
              if (candidates.length) {
                try {
                  candidates[0].el.click();
                  return true;
                } catch (e) {}
              }
              return false;
            }
            """
        )
        if clicked:
            logger.info("[DOUYIN-COVER] clicked 设置横封面 (JS bottom-most in modal)")
            await asyncio.sleep(0.65)
            return True
    except Exception as ex:
        logger.debug("[DOUYIN-COVER] JS 设置横封面: %s", ex)

    try:
        for spec in (
            ".dy-creator-content-modal-wrap",
            ".semi-modal-wrap",
            '[role="dialog"]',
        ):
            root = page.locator(spec).last
            try:
                if not await root.is_visible():
                    continue
            except Exception:
                continue
            loc = root.locator("button, [role='button']").filter(
                has_text=re.compile(r"^\s*设置横封面\s*$")
            )
            n = await loc.count()
            for i in range(n - 1, -1, -1):
                btn = loc.nth(i)
                try:
                    if not await btn.is_visible():
                        continue
                    await btn.scroll_into_view_if_needed(timeout=_pw_ms(5000))
                    await btn.click(timeout=_pw_ms(8000))
                    logger.info("[DOUYIN-COVER] clicked 设置横封面 (%s locator #%s)", spec, i)
                    await asyncio.sleep(0.55)
                    return True
                except Exception as ex:
                    try:
                        await btn.click(force=True, timeout=_pw_ms(8000))
                        logger.info("[DOUYIN-COVER] clicked 设置横封面 force (%s #%s)", spec, i)
                        await asyncio.sleep(0.55)
                        return True
                    except Exception:
                        logger.debug("[DOUYIN-COVER] 设置横封面 %s #%s: %s", spec, i, ex)
    except Exception as ex:
        logger.debug("[DOUYIN-COVER] locator 设置横封面: %s", ex)

    try:
        loc_primary = page.locator("button.semi-button-primary").filter(
            has_text=re.compile(r"^\s*设置横封面\s*$")
        )
        n = await loc_primary.count()
        for i in range(n - 1, -1, -1):
            btn = loc_primary.nth(i)
            try:
                if not await btn.is_visible():
                    continue
                await btn.click(timeout=_pw_ms(8000))
                logger.info("[DOUYIN-COVER] clicked semi-button-primary 设置横封面 #%s", i)
                await asyncio.sleep(0.55)
                return True
            except Exception as ex:
                logger.debug("[DOUYIN-COVER] primary 设置横封面 #%s: %s", i, ex)
    except Exception as ex:
        logger.debug("[DOUYIN-COVER] primary 设置横封面: %s", ex)
    try:
        role_btn = page.get_by_role("button", name="设置横封面", exact=True)
        cnt = await role_btn.count()
        for i in range(cnt - 1, -1, -1):
            b = role_btn.nth(i)
            try:
                if not await b.is_visible():
                    continue
                await b.scroll_into_view_if_needed(timeout=_pw_ms(5000))
                await b.click(timeout=_pw_ms(8000))
                logger.info("[DOUYIN-COVER] clicked role=button 设置横封面 #%s", i)
                await asyncio.sleep(0.55)
                return True
            except Exception as ex:
                logger.debug("[DOUYIN-COVER] role 设置横封面 #%s: %s", i, ex)
    except Exception as ex:
        logger.debug("[DOUYIN-COVER] role 设置横封面: %s", ex)
    return False


async def _douyin_click_horizontal_cover_primary_in_modal(page: Any) -> bool:
    """竖封面就绪后点底部「设置横封面」；最多 3 次，减轻偶现未点到。"""
    for attempt in range(3):
        if attempt:
            logger.info(
                "[DOUYIN-COVER] 设置横封面重试 %s/3（等待弹层/主按钮就绪）",
                attempt + 1,
            )
            await asyncio.sleep(1.0 + 0.75 * (attempt - 1))
        if await _douyin_try_click_horizontal_cover_modal_once(page):
            return True
    return False


async def _douyin_click_complete_in_cover_modal(page: Any) -> bool:
    """弹层内点「完成」：与「设置竖封面」同系列，多为 semi-button-primary + semi-button-light + span「完成」。"""
    modal_roots = page.locator('.semi-modal-wrap, [role="dialog"]').filter(has_text="设置封面")
    try:
        n = await modal_roots.count()
        for i in range(min(n, 6)):
            root = modal_roots.nth(i)
            try:
                if not await root.is_visible():
                    continue
            except Exception:
                continue
            for sel in (
                "button.semi-button-primary.semi-button-light",
                "button.semi-button-primary",
            ):
                try:
                    loc = root.locator(sel).filter(has_text="完成")
                    m = await loc.count()
                    for j in range(m):
                        btn = loc.nth(j)
                        try:
                            if await btn.is_visible():
                                await btn.click(timeout=_pw_ms(8000))
                                logger.info("[DOUYIN-COVER] clicked 完成 (%s in cover modal)", sel)
                                return True
                        except Exception:
                            continue
                except Exception:
                    continue
            try:
                btn = root.get_by_role("button", name="完成", exact=True).first
                if await btn.is_visible():
                    await btn.click(timeout=_pw_ms(8000))
                    logger.info("[DOUYIN-COVER] clicked 完成 (role in cover modal)")
                    return True
            except Exception:
                pass
    except Exception as ex:
        logger.debug("[DOUYIN-COVER] complete in modal: %s", ex)

    try:
        nd = await page.locator('[role="dialog"]').count()
        for i in range(min(nd, 5)):
            dlg = page.locator('[role="dialog"]').nth(i)
            btn = dlg.get_by_role("button", name="完成", exact=True).first
            try:
                if await btn.is_visible():
                    await btn.click(timeout=_pw_ms(8000))
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return await _douyin_click_first_visible_exact_text(page, "完成")


async def _douyin_run_cover_flow(page: Any, _step, *, require: bool) -> bool:
    """横入口→竖封面→弹层内主按钮「设置横封面」→「完成」。先滚到横封面区域再点入口（含横封面4:3）。
    require=False：无横入口时跳过。require=True：必须点到横入口。
    """
    await _douyin_scroll_to_cover_block(page)
    if not await _douyin_click_horizontal_cover_entry(page):
        if not require:
            return True
        _step(
            "封面流程-打开设置封面弹层",
            False,
            detail="未点开设置封面弹层：请先能点到卡片「选择封面」或横封面4:3，且出现含「设置横封面/设置竖封面」的弹窗",
        )
        return False
    _step("封面流程-打开设置封面弹层", True)
    if not await _douyin_click_vertical_cover_in_flow(page):
        _step("封面流程-设置竖封面", False, detail="未点到设置竖封面/设置竖版封面")
        return False
    _step("封面流程-设置竖封面", True)
    if not await _douyin_click_horizontal_cover_primary_in_modal(page):
        _step(
            "封面流程-弹层内设置横封面",
            False,
            detail="未点到弹层底部主按钮「设置横封面」（竖封面就绪后须点红色主按钮）",
        )
        return False
    _step("封面流程-弹层内设置横封面", True)
    if not await _douyin_click_complete_in_cover_modal(page):
        _step("封面流程-完成", False, detail="未点到完成")
        return False
    _step("封面流程-完成", True)
    await asyncio.sleep(2.0)
    _step("封面流程-弹层关闭后等待", True)
    return True


async def _douyin_switch_publish_tab(page: Any, which: str) -> bool:
    """which: video | image → 点「发布视频」或「发布图文」（优先 role=tab，与新版上传页结构一致）。"""
    label = "发布视频" if which == "video" else "发布图文"
    try:
        tab = page.get_by_role("tab", name=label, exact=True)
        if await tab.count() > 0:
            t0 = tab.first
            if await t0.is_visible():
                await t0.click(timeout=_pw_ms(8000))
                await asyncio.sleep(1.0)
                logger.info("[DOUYIN-TAB] clicked role=tab %r", label)
                return True
    except Exception as e:
        logger.debug("[DOUYIN-TAB] role=tab %s: %s", label, e)
    try:
        loc = page.get_by_text(label, exact=True).first
        if await loc.is_visible():
            await loc.click(timeout=_pw_ms(8000))
            await asyncio.sleep(1.0)
            logger.info("[DOUYIN-TAB] clicked get_by_text %r", label)
            return True
    except Exception as e:
        logger.debug("[DOUYIN-TAB] get_by_text %s: %s", label, e)
    try:
        clicked = await page.evaluate(
            """
            (label) => {
              for (const el of document.querySelectorAll('[role="tab"], button, div, span, a')) {
                const t = (el.textContent || '').trim();
                if (t === label) {
                  try { el.click(); return true; } catch(e) {}
                }
              }
              return false;
            }
            """,
            label,
        )
        if clicked:
            await asyncio.sleep(1.0)
            logger.info("[DOUYIN-TAB] clicked via DOM scan %r", label)
            return True
    except Exception:
        pass
    return False


async def _douyin_video_ensure_cover(
    page: Any,
    cover_path: Optional[str],
    _step,
    *,
    cover_mode: str = "smart",
    manual_wait_sec: int = 600,
) -> bool:
    """
    封面策略（options.douyin_cover_mode）：
    - smart / upload：固定先滚到横封面区域，从「横封面4:3」等入口走横→竖→完成；不依赖智能推荐/文案是否在视口内。
    - upload：须已通过 cover_asset_id 提供封面图；先上传再横竖流程。
    - manual：不自动点横竖；仅在浏览器内由用户操作，脚本轮询直至无「必选封面」或超时。
    """
    mode = (cover_mode or "smart").strip().lower()
    if mode not in ("smart", "upload", "manual"):
        mode = "smart"
    wait_manual = max(60, min(3600, int(manual_wait_sec)))

    if mode == "manual":
        _step("封面(manual)", True, detail="请在浏览器内完成封面；脚本轮询检测「必选封面」提示")
        for elapsed in range(0, wait_manual, 2):
            try:
                still = await page.evaluate(_JS_DOUYIN_COVER_STILL_REQUIRED)
            except Exception:
                still = False
            if not still:
                _step("封面(manual)", True, detail="已不再提示必选封面", waited_s=elapsed)
                return True
            await asyncio.sleep(2)
        _step("封面(manual)", False, detail=f"等待 {wait_manual}s 仍提示必选封面")
        return False

    if mode == "upload":
        if not cover_path or not os.path.isfile(cover_path):
            _step("封面(upload)", False, detail="douyin_cover_mode=upload 须指定有效 cover_asset_id")
            return False

    await _douyin_scroll_to_cover_block(page)

    uploaded = False
    if cover_path and os.path.isfile(cover_path):
        try:
            inputs = await page.query_selector_all('input[type="file"]')
        except Exception:
            inputs = []
        for fi in inputs:
            try:
                acc = (await fi.get_attribute("accept")) or ""
                if "image" not in acc.lower():
                    continue
                await fi.set_input_files(cover_path)
                uploaded = True
                logger.info("[DOUYIN-VIDEO-COVER] set_input_files cover_path=%s accept=%s", cover_path, acc[:80])
                break
            except Exception as ex:
                logger.debug("[DOUYIN-VIDEO-COVER] file input skip: %s", ex)
        if uploaded:
            await asyncio.sleep(1.2)
            _step("上传视频封面", True, path=os.path.basename(cover_path), mode=mode)
            try:
                for txt in ("确定", "完成", "应用"):
                    loc = page.get_by_role("button", name=txt, exact=True)
                    if await loc.count():
                        await loc.first.click(timeout=_pw_ms(2500))
                        await asyncio.sleep(0.4)
                        break
            except Exception:
                pass

    await _douyin_scroll_to_cover_block(page)
    _step(
        "封面流程-开始",
        True,
        detail="固定从横封面(含横封面4:3)进入竖封面并完成，不依赖智能推荐",
        mode=mode,
    )
    if not await _douyin_run_cover_flow(page, _step, require=True):
        return False

    try:
        if await page.evaluate(_JS_DOUYIN_COVER_STILL_REQUIRED):
            _step(
                "选择视频封面",
                False,
                detail="横竖流程后仍提示必选封面：可改 manual 在浏览器手选；upload 请检查 cover_asset_id",
                mode=mode,
            )
            return False
    except Exception:
        pass
    return True


async def _douyin_bottom_publish_ready(page: Any) -> bool:
    """
    底部主操作「发布」是否可见且可点。
    排除顶部「高清发布」（douyin-creator-master-button / header-button），与结构探测一致。
    """
    try:
        loc = page.locator("button.button-dhlUZE").filter(has_text=re.compile(r"^\s*发布\s*$"))
        n = await loc.count()
        for i in range(n):
            el = loc.nth(i)
            try:
                if not await el.is_visible():
                    continue
                cls = (await el.get_attribute("class")) or ""
                if "douyin-creator-master-button" in cls or "header-button" in cls:
                    continue
                if await el.is_disabled():
                    continue
                return True
            except Exception:
                continue
    except Exception:
        pass
    try:
        loc = page.get_by_role("button", name="发布", exact=True)
        cnt = await loc.count()
        for i in range(cnt):
            el = loc.nth(i)
            try:
                if not await el.is_visible():
                    continue
                cls = (await el.get_attribute("class")) or ""
                if "douyin-creator-master-button" in cls or "header-button" in cls:
                    continue
                if await el.is_disabled():
                    continue
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False


async def _douyin_wait_until_publish_clickable(page, max_wait_s: int, _step, label: str) -> bool:
    """
    轮询：底部「发布」主按钮可点（优先 button-dhlUZE，排除高清发布）。
    全页 innerText 的 busy 仅作辅助：若页面残留「转码中」等文案但按钮已可用，仍以按钮为准。
    """
    deadline = max(10, int(max_wait_s))
    for i in range(deadline):
        try:
            if await _douyin_bottom_publish_ready(page):
                _step(f"{label}发布按钮可点击", True, waited_rounds=i)
                return True
        except Exception:
            pass
        try:
            st = await page.evaluate(_JS_DOUYIN_UPLOAD_BUSY)
        except Exception:
            st = ""
        if st == "fail":
            _step(f"{label}检测到上传/转码失败", False)
            return False
        if st == "busy":
            if i % 5 == 0 and i > 0:
                logger.info("[DOUYIN] %s still busy (upload/transcode), round=%d", label, i)
            await asyncio.sleep(1)
            continue
        # 不再在「非 busy 但未匹配到底部发布」时提前放行，否则易在编辑填完后仍点不到真实「发布」
        if i > 0 and i % 25 == 0:
            try:
                await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass
            await _dismiss_overlays(page, f"{label}_wait_publish")
        await asyncio.sleep(1)
    _step(f"{label}等待发布按钮可点超时", False, waited_s=deadline)
    return False


# ---------------------------------------------------------------------------
# Dismiss known overlays / popups (我知道了, guide dialogs, etc.)
# ---------------------------------------------------------------------------
_JS_DISMISS_OVERLAYS = """
() => {
    let dismissed = 0;
    const safeTexts = ['我知道了', '知道了', '关闭', '一律不允许'];
    document.querySelectorAll('button, [role="button"], a').forEach(el => {
        const t = (el.textContent || '').trim();
        if (t.includes('发布') || t.includes('暂存') || t.includes('封面')
            || t.includes('上传') || t.includes('图文') || t.includes('视频')) return;
        for (const dt of safeTexts) {
            if (t === dt || (t.length < 10 && t.includes(dt))) {
                try { el.click(); dismissed++; } catch(e) {}
                break;
            }
        }
    });
    // Close icon in modals
    document.querySelectorAll('.semi-modal-wrap .semi-icon-close, .semi-modal [aria-label="close"]').forEach(el => {
        try { el.click(); dismissed++; } catch(e) {}
    });
    return dismissed;
}
"""


async def _dismiss_overlays(page, label: str = "") -> int:
    total = 0
    for attempt in range(3):
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
        except Exception:
            pass
        try:
            n = await page.evaluate(_JS_DISMISS_OVERLAYS)
            total += (n or 0)
            logger.info("[DOUYIN-DISMISS] %s attempt=%d dismissed=%d", label, attempt, n)
        except Exception as exc:
            logger.debug("[DOUYIN-DISMISS] %s attempt=%d err=%s", label, attempt, exc)
        if attempt < 2:
            await asyncio.sleep(0.5)
        try:
            remaining = await page.evaluate(
                "() => document.querySelectorAll('.semi-portal .semi-modal-wrap').length"
            )
            if remaining == 0:
                break
        except Exception:
            break
    return total


async def _douyin_dismiss_hashtag_suggestion(page: Any, label: str) -> None:
    """
    作品描述中带 # 时易弹出话题下拉里表，遮挡封面区/发布按钮。
    Escape + 点击标题输入框失焦；再移除常见 Tippy 根节点（与小红书同类问题）。
    """
    try:
        for _ in range(4):
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.12)
    except Exception:
        pass
    try:
        for sel in (
            'input[placeholder*="标题"]',
            'input[placeholder*="添加作品标题"]',
            'input.semi-input',
        ):
            try:
                inp = await page.query_selector(sel)
                if inp and await inp.is_visible():
                    await inp.click(timeout=3000)
                    await asyncio.sleep(0.15)
                    break
            except Exception:
                continue
    except Exception:
        pass
    try:
        n = await page.evaluate("""
        () => {
            let n = 0;
            document.querySelectorAll('[data-tippy-root], [id^="tippy-"]').forEach(el => {
                try { el.remove(); n++; } catch (e) {}
            });
            return n;
        }
        """)
        if n:
            logger.info("[DOUYIN-DISMISS] %s removed tippy roots n=%d", label, n)
    except Exception:
        pass
    await _human_delay(0.12, 0.28)


# ---------------------------------------------------------------------------
# Discard draft prompt — "有上次未编辑的草稿" → click 放弃
# ---------------------------------------------------------------------------
async def _discard_draft(page, label: str = "") -> bool:
    """If the upload page shows a draft prompt, click 放弃 (discard) to start fresh."""
    try:
        body_text = await page.evaluate("() => (document.body.innerText || '').substring(0, 1000)")
        if "草稿" not in body_text:
            return False
        logger.info("[DOUYIN-DRAFT] %s draft prompt detected", label)

        # Look for 放弃 button
        discard_btn = None
        for sel in [
            'button:has-text("放弃")',
            'text="放弃"',
            'button:has-text("丢弃")',
            'button:has-text("不继续")',
        ]:
            try:
                el = await page.query_selector(sel)
                if el:
                    discard_btn = el
                    break
            except Exception:
                continue

        if discard_btn:
            await discard_btn.click(force=True, timeout=_pw_ms(3000))
            logger.info("[DOUYIN-DRAFT] %s clicked discard button", label)
            await _human_delay(1, 2)
            return True

        # Fallback: JS click any button with 放弃
        clicked = await page.evaluate("""
        () => {
            const btns = document.querySelectorAll('button, [role="button"], a');
            for (const b of btns) {
                const t = (b.textContent || '').trim();
                if (t === '放弃' || t.includes('放弃')) {
                    b.click();
                    return true;
                }
            }
            return false;
        }
        """)
        if clicked:
            logger.info("[DOUYIN-DRAFT] %s JS clicked discard", label)
            await _human_delay(1, 2)
            return True

        logger.warning("[DOUYIN-DRAFT] %s draft detected but discard button not found", label)
        return False
    except Exception as e:
        logger.debug("[DOUYIN-DRAFT] %s error: %s", label, e)
        return False


# ---------------------------------------------------------------------------
# Scroll helpers
# ---------------------------------------------------------------------------
async def _scroll_page_fully(page, label: str = ""):
    """Scroll to bottom then back to top to trigger lazy-loaded content."""
    try:
        await page.evaluate("""
        async () => {
            const delay = ms => new Promise(r => setTimeout(r, ms));
            const totalH = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
            const step = Math.min(800, totalH / 3);
            for (let y = 0; y < totalH; y += step) {
                window.scrollTo(0, y);
                await delay(150);
            }
            window.scrollTo(0, totalH);
            await delay(200);
            window.scrollTo(0, 0);
        }
        """)
        logger.info("[DOUYIN-SCROLL] %s scrolled page fully", label)
    except Exception as exc:
        logger.debug("[DOUYIN-SCROLL] %s error: %s", label, exc)


async def _scroll_and_find(page, selectors, label: str = ""):
    """Try to find element; if not found, scroll down step by step and retry."""
    if isinstance(selectors, str):
        selectors = [selectors]

    for sel in selectors:
        el = await page.query_selector(sel)
        if el:
            try:
                await el.scroll_into_view_if_needed()
            except Exception:
                pass
            return el

    try:
        total_h = await page.evaluate(
            "() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
        )
    except Exception:
        total_h = 3000

    step = 400
    for y in range(0, total_h + step, step):
        try:
            await page.evaluate(f"() => window.scrollTo(0, {y})")
        except Exception:
            break
        await asyncio.sleep(0.3)
        for sel in selectors:
            el = await page.query_selector(sel)
            if el:
                logger.info("[DOUYIN-SCROLL] %s found '%s' at scroll y=%d", label, sel, y)
                try:
                    await el.scroll_into_view_if_needed()
                except Exception:
                    pass
                return el
    return None


# ---------------------------------------------------------------------------
# Find and click the real 发布 button (red/primary), not 暂存离开
# ---------------------------------------------------------------------------
async def _find_publish_button(page, label: str = ""):
    """定位底部主「发布」按钮（新版 class 含 button-dhlUZE）；排除顶部「高清发布」。"""
    await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    await asyncio.sleep(0.6)

    btn_info = await page.evaluate("""
    () => {
        const btns = Array.from(document.querySelectorAll('button'));
        const allTexts = btns.map(b => (b.textContent || '').trim()).filter(t => t.length < 20);
        const candidates = [];
        for (const b of btns) {
            const t = (b.textContent || '').trim();
            if (t !== '发布') continue;
            const cls = (b.className || '');
            if (cls.includes('douyin-creator-master-button') || cls.includes('header-button')) continue;
            const style = window.getComputedStyle(b);
            const bg = style.backgroundColor || '';
            const isPrimary = cls.includes('primary') || cls.includes('danger') || cls.includes('button-dhlUZE')
                || bg.includes('255') || bg.includes('254') || bg.includes('252');
            candidates.push({isPrimary, cls: cls.substring(0, 120)});
            try { b.scrollIntoView({block: 'center'}); } catch(e) {}
        }
        return {found: candidates.length > 0, count: candidates.length,
                primary: candidates.some(c => c.isPrimary),
                allTexts: allTexts.slice(0, 30)};
    }
    """)
    logger.info("[DOUYIN-%s] button scan: %s", label, btn_info)

    # 1) 新版底部固定栏（探测：button.button-dhlUZE.primary…fixed）
    try:
        loc = page.locator("button.button-dhlUZE").filter(has_text=re.compile(r"^\s*发布\s*$"))
        n = await loc.count()
        for i in range(n):
            el = loc.nth(i)
            try:
                if not await el.is_visible():
                    continue
                cls = (await el.get_attribute("class")) or ""
                if "douyin-creator-master-button" in cls or "header-button" in cls:
                    continue
                logger.info("[DOUYIN-%s] picked button-dhlUZE 发布 #%s", label, i)
                return el
            except Exception:
                continue
    except Exception as ex:
        logger.debug("[DOUYIN-%s] button-dhlUZE: %s", label, ex)

    await asyncio.sleep(0.3)

    # 2) role=button「发布」，跳过 header
    try:
        loc = page.get_by_role("button", name="发布", exact=True)
        cnt = await loc.count()
        logger.info("[DOUYIN-%s] get_by_role count=%d", label, cnt)
        for i in range(cnt):
            el = loc.nth(i)
            try:
                if not await el.is_visible():
                    continue
                cls = (await el.get_attribute("class")) or ""
                if "douyin-creator-master-button" in cls or "header-button" in cls:
                    continue
                txt = (await el.inner_text()).strip()
                if txt == "发布":
                    return el
            except Exception:
                continue
    except Exception:
        pass

    # 3) 兜底：遍历 button 文案
    btns = await page.query_selector_all("button")
    for btn in btns:
        try:
            txt = (await btn.inner_text()).strip()
            if txt != "发布":
                continue
            cls = (await btn.get_attribute("class")) or ""
            if "douyin-creator-master-button" in cls or "header-button" in cls:
                continue
            await btn.scroll_into_view_if_needed()
            return btn
        except Exception:
            continue

    return None


async def _click_publish_button(page, publish_btn, label: str = ""):
    """Scroll the publish button into view and click it, with retries."""
    try:
        if hasattr(publish_btn, 'scroll_into_view_if_needed'):
            await publish_btn.scroll_into_view_if_needed()
        elif hasattr(publish_btn, 'first'):
            await publish_btn.first.scroll_into_view_if_needed()
    except Exception:
        pass
    await asyncio.sleep(0.3)

    try:
        await publish_btn.click(timeout=_pw_ms(5000))
        return
    except Exception:
        pass

    await _dismiss_overlays(page, f"{label}_publish_retry")
    try:
        if hasattr(publish_btn, 'click'):
            await publish_btn.click(force=True, timeout=_pw_ms(5000))
        else:
            await publish_btn.first.click(force=True, timeout=_pw_ms(5000))
        return
    except Exception:
        pass

    # JS last resort
    await page.evaluate("""
    () => {
        const btns = document.querySelectorAll('button');
        for (const b of btns) {
            if ((b.textContent || '').trim() === '发布') {
                b.scrollIntoView();
                b.click();
                return;
            }
        }
    }
    """)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _query_any_frame(page: Any, selector: str) -> Any:
    try:
        el = await page.query_selector(selector)
        if el:
            return el
    except Exception:
        pass
    try:
        frames = page.frames
    except Exception:
        frames = []
    for fr in frames or []:
        try:
            el = await fr.query_selector(selector)
            if el:
                return el
        except Exception:
            continue
    return None


async def _query_all_any_frame(page: Any, selector: str) -> List[Any]:
    out: List[Any] = []
    try:
        out.extend(await page.query_selector_all(selector))
    except Exception:
        pass
    try:
        frames = page.frames
    except Exception:
        frames = []
    for fr in frames or []:
        try:
            out.extend(await fr.query_selector_all(selector))
        except Exception:
            continue
    return out


# ===========================================================================
# DouyinDriver
# ===========================================================================
class DouyinDriver(BaseDriver):

    def login_url(self) -> str:
        return "https://creator.douyin.com"

    async def _passive_login_check(self, page: Any) -> bool:
        try:
            url = getattr(page, "url", "") or ""
            if "login" in url or "passport" in url:
                return False
            markers = [
                'text="退出登录"', 'text="作品管理"', 'text="内容管理"',
                'text="发布作品"', 'a[href*="content"]', 'a[href*="upload"]',
            ]
            for sel in markers:
                if await _query_any_frame(page, sel):
                    return True
            if "creator-micro/content/upload" in url:
                if await _query_any_frame(page, 'input[type="file"]'):
                    return True
            return False
        except Exception:
            return False

    async def check_login(self, page: Any, navigate: bool = True) -> bool:
        if not navigate:
            return await self._passive_login_check(page)
        try:
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=_nav_ms(15000))
            await asyncio.sleep(2)
            if await self._passive_login_check(page):
                return True
            try:
                await page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=_nav_ms(15000))
                await asyncio.sleep(1)
                if await _query_any_frame(page, 'input[type="file"]'):
                    return True
            except Exception:
                pass
            return False
        except Exception:
            return False

    # -----------------------------------------------------------------------
    # Main publish entry
    # -----------------------------------------------------------------------
    async def publish(
        self, page: Any, file_path: str, title: str, description: str,
        tags: str, options: Optional[Dict[str, Any]] = None,
        cover_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        options = options or {}
        applied: Dict[str, Any] = {"steps": []}

        def _step(note: str, ok: bool, **extra):
            entry = {"action": note, "ok": ok, **extra}
            applied["steps"].append(entry)
            logger.info("[DOUYIN-PUBLISH] %s => %s %s", note, "OK" if ok else "FAIL", extra or "")

        try:
            file_ext = os.path.splitext(file_path)[1].lower()
            is_image = file_ext in _IMAGE_EXTS
            logger.info("[DOUYIN-PUBLISH] === START === file=%s ext=%s is_image=%s title=%s",
                        file_path, file_ext, is_image, title)

            if not os.path.isfile(file_path):
                _step("检查文件存在", False, path=file_path)
                return {"ok": False, "error": f"文件不存在: {file_path}", "applied": applied}
            file_size = os.path.getsize(file_path)
            _step("检查文件存在", True, size=file_size)

            media_type = "图片" if is_image else "视频"
            _step(f"自动识别素材类型: {media_type}", True, ext=file_ext, type=media_type)

            # ── Step 1: navigate to upload page (高清发布) ──
            logger.info("[DOUYIN] navigating to upload page: %s", UPLOAD_URL)
            await page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=_nav_ms(30000))
            await _human_delay(2, 4)

            n = await _dismiss_overlays(page, "upload_page")
            if n:
                _step("关闭弹窗", True, count=n)

            # ── Step 2: discard any lingering draft ──
            if await _discard_draft(page, "upload_page"):
                _step("放弃旧草稿", True)
                await _human_delay(1, 2)
                # May need to re-navigate after discarding draft
                await page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=_nav_ms(30000))
                await _human_delay(2, 3)
                await _dismiss_overlays(page, "upload_after_discard")

            if is_image:
                return await self._publish_image(page, file_path, title, description,
                                                 tags, options, applied, _step)
            else:
                return await self._publish_video(page, file_path, title, description,
                                                 tags, options, cover_path, applied, _step)

        except Exception as e:
            logger.exception("[DOUYIN-PUBLISH] publish failed")
            _step("异常", False, error=str(e))
            return {"ok": False, "error": str(e), "applied": applied}

    # ===================================================================
    # IMAGE FLOW: upload page → click "发布图文" tab → use image file input → wait redirect → fill form → publish
    # ===================================================================
    async def _publish_image(self, page, file_path, title, description, tags,
                             options, applied, _step):

        img_wait_rounds = _douyin_image_wait_rounds(os.path.getsize(file_path))
        _step("图文上传等待轮次", True, rounds=img_wait_rounds)

        # We are already on /content/upload (from main publish method).
        # The page has tabs: 发布视频 | 发布图文 | 发布全景视频 | 发布文章
        # Default tab is 发布视频. We need to click 发布图文 to switch.

        # ── Click "发布图文" tab ──
        if await _douyin_switch_publish_tab(page, "image"):
            _step("切换到发布图文tab", True)
        else:
            await page.evaluate("""
            () => {
                for (const el of document.querySelectorAll('*')) {
                    if ((el.textContent||'').trim() === '发布图文' && el.offsetParent) {
                        el.click(); return true;
                    }
                }
                return false;
            }
            """)
            _step("切换到发布图文tab", True, method="js_fallback")

        await _human_delay(2, 3)
        await _dismiss_overlays(page, "image_tab")

        # ── Find the IMAGE file input (accept contains "image") ──
        # After clicking 发布图文, there are 2 file inputs:
        # - video input (hidden, accept="video/*...")
        # - image input (visible, accept="image/png,image/jpeg...")
        uploaded = False

        # Strategy 1: input[type=file] — 优先仅 accept 图片的（避免与视频/混合 input 混淆）
        all_inputs = await page.query_selector_all('input[type="file"]')
        image_input = None
        image_fallback = None
        for fi in all_inputs:
            try:
                accept = (await fi.get_attribute("accept") or "").lower()
                if "image" not in accept:
                    continue
                image_fallback = image_fallback or fi
                if "video" not in accept:
                    image_input = fi
                    break
            except Exception:
                continue
        if image_input is None:
            image_input = image_fallback

        if image_input:
            try:
                await image_input.set_input_files(file_path)
                logger.info("[DOUYIN-IMAGE] uploaded via image file input (accept=image)")
                _step("上传图片文件", True, method="image-input")
                uploaded = True
            except Exception as e:
                logger.warning("[DOUYIN-IMAGE] set_input_files failed: %s", e)

        # Strategy 2: expect_file_chooser + click "上传图文" button
        if not uploaded:
            upload_btn = await page.query_selector('button:has-text("上传图文")')
            if upload_btn:
                try:
                    async with page.expect_file_chooser(timeout=_pw_ms(5000)) as fc_info:
                        await upload_btn.click(timeout=_pw_ms(3000))
                    fc = await fc_info.value
                    await fc.set_files(file_path)
                    logger.info("[DOUYIN-IMAGE] uploaded via filechooser on '上传图文' button")
                    _step("上传图片文件", True, method="filechooser")
                    uploaded = True
                except Exception as e:
                    logger.debug("[DOUYIN-IMAGE] filechooser failed: %s", e)

        # Strategy 3: click "点击上传" text area
        if not uploaded:
            try:
                click_area = await page.query_selector('text="点击上传"')
                if click_area:
                    async with page.expect_file_chooser(timeout=_pw_ms(5000)) as fc_info:
                        await click_area.click(force=True, timeout=_pw_ms(3000))
                    fc = await fc_info.value
                    await fc.set_files(file_path)
                    _step("上传图片文件", True, method="click-upload")
                    uploaded = True
            except Exception:
                pass

        if not uploaded:
            diag = await page.evaluate("""
            () => ({
                url: location.href,
                inputs: Array.from(document.querySelectorAll('input')).map(el => ({
                    type: el.type, accept: (el.accept||'').substring(0,60),
                    visible: !!el.offsetParent,
                })),
                buttons: Array.from(document.querySelectorAll('button')).filter(b => b.offsetParent).map(b => ({
                    text: (b.textContent||'').trim().substring(0,30),
                })).slice(0, 20),
            })
            """)
            import json
            logger.error("[DOUYIN-IMAGE] DOM DIAGNOSTICS:\n%s", json.dumps(diag, ensure_ascii=False, indent=2))
            _step("上传图片文件", False, diagnostics=diag)
            return {"ok": False, "error": "找不到图片上传入口", "applied": applied}

        # ── Wait for redirect to image editing page ──
        logger.info("[DOUYIN-IMAGE] waiting for redirect to editing page...")
        redirected = False
        for _ in range(img_wait_rounds):
            await asyncio.sleep(2)
            cur = page.url or ""
            if "post/image" in cur or "post/publish" in cur:
                logger.info("[DOUYIN-IMAGE] redirected to: %s", cur)
                redirected = True
                break
            # Check if editing form appeared on same page
            form_el = await page.query_selector('[contenteditable="true"], input[placeholder*="标题"]')
            if form_el:
                logger.info("[DOUYIN-IMAGE] form appeared (URL: %s)", cur)
                redirected = True
                break

        if not redirected:
            _step("等待跳转到编辑页", False, url=page.url)
            return {"ok": False, "error": "上传图片后未跳转到编辑页", "applied": applied}

        _step("进入图文编辑页面", True, url=page.url)
        await _human_delay(2, 3)
        await _dismiss_overlays(page, "image_edit_page")
        if await _discard_draft(page, "image_edit"):
            _step("放弃编辑页草稿", True)

        # ── Fill title ──
        title_input = await _scroll_and_find(page, [
            'input.semi-input[placeholder*="标题"]',
            'input[placeholder*="添加作品标题"]',
            'input.semi-input',
        ], "image_title")
        if title_input and title:
            await title_input.click()
            await title_input.fill("")
            await _human_delay(0.2, 0.4)
            await title_input.fill(title[:20])
            _step("填写标题", True, value=title[:20])
        else:
            _step("填写标题", False, found=bool(title_input))

        # ── Fill description ──
        text = description or title or ""
        if tags:
            tag_list = _douyin_tag_list(tags)
            if tag_list:
                text += " " + " ".join(f"#{t}" for t in tag_list)

        content_editor = await _scroll_and_find(page, [
            '.zone-container[contenteditable="true"]',
            '[contenteditable="true"].notranslate',
            '[contenteditable="true"]',
        ], "image_editor")
        if content_editor:
            await content_editor.click()
            await _human_delay(0.2, 0.4)
            await page.keyboard.press("Control+KeyA")
            await page.keyboard.press("Delete")
            await page.keyboard.type(text[:500])
            _step("填写描述/文案", True, length=len(text))
            if "#" in text or (tags and str(tags).strip()):
                await _douyin_dismiss_hashtag_suggestion(page, "image_after_desc")
        else:
            _step("填写描述/文案", False)

        await _human_delay(1, 2)
        await _dismiss_overlays(page, "image_before_publish")
        await _scroll_page_fully(page, "image_before_publish_btn")
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(0.5)

        _s = _pw_scale()
        img_btn_wait = min(int(150 * _s), max(int(45 * _s), img_wait_rounds * 2))
        if not await _douyin_wait_until_publish_clickable(page, img_btn_wait, _step, "图文"):
            return {
                "ok": False,
                "error": "图片可能仍在上传中，或发布按钮不可用，请稍后重试或手动发布",
                "applied": applied,
            }

        # ── Find and click publish button ──
        publish_btn = await _find_publish_button(page, "IMAGE")
        if not publish_btn:
            _step("找到发布按钮", False)
            return {"ok": False, "error": "找不到发布按钮", "applied": applied}

        # Verify
        try:
            verify_txt = (await publish_btn.inner_text()).strip()
            logger.info("[DOUYIN-IMAGE] verified button: '%s'", verify_txt)
            if verify_txt != "发布":
                logger.error("[DOUYIN-IMAGE] wrong button text: '%s'", verify_txt)
                _step("找到发布按钮", False, error=f"按钮文字不对: {verify_txt}")
                return {"ok": False, "error": f"找到的按钮文字是'{verify_txt}'而非'发布'", "applied": applied}
        except Exception:
            pass

        _step("找到发布按钮", True)

        if options.get("dry_run"):
            _step("dry_run — 未点击发布", True)
            return {"ok": True, "url": page.url, "applied": applied, "dry_run": True}

        logger.info("[DOUYIN-IMAGE] clicking publish...")
        await _click_publish_button(page, publish_btn, "image")
        _step("点击发布按钮", True)

        return await self._check_publish_result(page, applied, _step)

    # ===================================================================
    # VIDEO FLOW: 上传视频 → 等传完/转码 → 封面 → 标题与描述 → 发布
    # ===================================================================
    async def _publish_video(self, page, file_path, title, description, tags,
                             options, cover_path, applied, _step):

        file_size = os.path.getsize(file_path)
        wait_rounds = _douyin_video_wait_rounds(file_size)
        _step("视频上传等待轮次", True, rounds=wait_rounds, file_size=file_size)

        await _douyin_switch_publish_tab(page, "video")
        await _human_delay(1, 2)
        await _dismiss_overlays(page, "video_tab")

        # 与图文一致：页面上常有多个 file input（封面/图片 + 视频），必须选 accept 含 video 的
        try:
            await page.wait_for_selector('input[type="file"]', state="attached", timeout=_pw_ms(15000))
        except Exception:
            pass

        video_uploaded = False
        file_input = await _douyin_find_video_file_input(page)
        if file_input:
            try:
                await file_input.set_input_files(file_path)
                logger.info("[DOUYIN-VIDEO] set_input_files on video input OK path=%s", file_path)
                video_uploaded = True
            except Exception as e:
                logger.warning("[DOUYIN-VIDEO] set_input_files failed: %s", e)

        if not video_uploaded:
            for ubs in ('button:has-text("上传视频")', 'text="点击上传"'):
                try:
                    ubtn = await page.query_selector(ubs)
                    if ubtn and await ubtn.is_visible():
                        async with page.expect_file_chooser(timeout=_pw_ms(8000)) as fc_info:
                            await ubtn.click(timeout=_pw_ms(3000))
                        fc = await fc_info.value
                        await fc.set_files(file_path)
                        logger.info("[DOUYIN-VIDEO] uploaded via filechooser selector=%s", ubs)
                        video_uploaded = True
                        break
                except Exception as e:
                    logger.debug("[DOUYIN-VIDEO] filechooser %s: %s", ubs, e)

        if not video_uploaded:
            try:
                ubtn = await page.query_selector('[class*="upload"] button')
                if ubtn and await ubtn.is_visible():
                    async with page.expect_file_chooser(timeout=_pw_ms(8000)) as fc_info:
                        await ubtn.click(timeout=_pw_ms(3000))
                    fc = await fc_info.value
                    await fc.set_files(file_path)
                    video_uploaded = True
            except Exception as e:
                logger.debug("[DOUYIN-VIDEO] filechooser upload button: %s", e)

        if not video_uploaded:
            _step("上传视频文件", False)
            return {
                "ok": False,
                "error": "无法将视频填入上传入口（多为误选仅图片的 file input，已改为按 accept 选视频）",
                "applied": applied,
            }

        _step("上传视频文件", True)
        await asyncio.sleep(1.0)

        # Wait for redirect to publish/post page
        logger.info("[DOUYIN-VIDEO] waiting for redirect...")
        redirected = False
        for _ in range(wait_rounds):
            await asyncio.sleep(2)
            cur = page.url or ""
            if "post/video" in cur or ("publish" in cur and "upload" not in cur):
                logger.info("[DOUYIN-VIDEO] redirected to: %s", cur)
                redirected = True
                break

        if not redirected:
            try:
                title_in = await page.query_selector('input[placeholder*="标题"]')
                if title_in and await title_in.is_visible():
                    redirected = True
                    logger.info("[DOUYIN-VIDEO] title input visible without URL change")
            except Exception:
                pass

        if not redirected:
            _step("等待视频处理/跳转", False)
            return {"ok": False, "error": "视频上传后未跳转到发布页面", "applied": applied}

        _step("进入视频发布页面", True)
        await _human_delay(1, 2)
        await _dismiss_overlays(page, "video_after_redirect")

        # ── 先等视频上传/转码结束，再点横竖封面（避免视频未就绪时封面入口无效）──
        stable_clear = 0
        for _w in range(wait_rounds):
            try:
                busy_st = await page.evaluate(_JS_DOUYIN_UPLOAD_BUSY)
            except Exception:
                busy_st = ""
            if busy_st == "fail":
                _step("视频上传失败", False)
                return {"ok": False, "error": "视频上传或转码失败", "applied": applied}
            if busy_st == "busy":
                stable_clear = 0
                await asyncio.sleep(2)
                continue
            stable_clear += 1
            has_reupload = False
            try:
                has_reupload = await page.evaluate(
                    "() => (document.body.innerText||'').includes('重新上传')"
                )
            except Exception:
                pass
            if stable_clear >= 2 or has_reupload:
                logger.info(
                    "[DOUYIN-VIDEO] video processing idle (stable=%s reupload=%s round=%s)",
                    stable_clear,
                    has_reupload,
                    _w + 1,
                )
                _step("视频处理完成", True, rounds=_w + 1)
                break
            await asyncio.sleep(2)
        else:
            logger.warning("[DOUYIN-VIDEO] wait loop exhausted without stable idle; will still probe 发布 button")

        cover_mode = str((options or {}).get("douyin_cover_mode") or "smart").strip().lower()
        if cover_mode not in ("smart", "upload", "manual"):
            cover_mode = "smart"
        manual_wait = int((options or {}).get("douyin_manual_cover_wait_sec") or 600)
        manual_wait = max(60, min(3600, manual_wait))

        if not await _douyin_video_ensure_cover(
            page,
            cover_path,
            _step,
            cover_mode=cover_mode,
            manual_wait_sec=manual_wait,
        ):
            return {
                "ok": False,
                "error": (
                    "视频封面未就绪：请检查 options.douyin_cover_mode（smart/upload/manual）；"
                    "manual 请在浏览器内选封面；upload 须传 cover_asset_id"
                ),
                "applied": applied,
            }

        # ── 封面完成后再滚动并填写标题、描述（避免先填文案时页面仍在传视频）──
        await _scroll_page_fully(page, "video_preload")
        await _dismiss_overlays(page, "video_after_scroll")
        await page.evaluate("() => window.scrollTo(0, 0)")

        # ── Title ──
        title_input = await _scroll_and_find(page, [
            'input[placeholder*="标题"]',
            'input.semi-input',
        ], "video_title")
        if title_input and title:
            await title_input.click()
            await title_input.fill("")
            await _human_delay(0.2, 0.4)
            await title_input.fill(title[:30])
            _step("填写标题", True, value=title[:30])
        else:
            notranslate = await _scroll_and_find(page, [
                '.notranslate[contenteditable]',
            ], "video_title_ce")
            if notranslate and title:
                await notranslate.click()
                await page.keyboard.press("Control+KeyA")
                await page.keyboard.press("Delete")
                await page.keyboard.type(title[:30])
                _step("填写标题", True, value=title[:30])
            else:
                _step("填写标题", False)

        # ── Description ──
        text = description or ""
        if tags:
            tag_list = _douyin_tag_list(tags)
            if tag_list:
                text += " " + " ".join(f"#{t}" for t in tag_list)

        zone = await _scroll_and_find(page, [
            '.zone-container[contenteditable="true"]',
        ], "video_desc")
        if zone and text:
            await zone.click()
            await _human_delay(0.2, 0.4)
            await page.keyboard.press("Control+KeyA")
            await page.keyboard.press("Delete")
            await page.keyboard.type(text[:500])
            _step("填写描述/文案", True, length=len(text))
        else:
            ce = await _scroll_and_find(page, [
                '[contenteditable="true"]',
            ], "video_desc_ce")
            if ce and text:
                await ce.click()
                await _human_delay(0.2, 0.4)
                await page.keyboard.type(text[:500])
                _step("填写描述/文案", True, length=len(text))
            else:
                _step("填写描述/文案", False)

        if text and ("#" in text or (tags and str(tags).strip())):
            await _douyin_dismiss_hashtag_suggestion(page, "video_after_desc")

        _s = _pw_scale()
        # 封面与填表后仍可能长时间转码；小视频原先 floor=60s 易超时，提高到 120s（上限仍 180*s）
        btn_wait = min(int(180 * _s), max(int(120 * _s), wait_rounds * 2))
        if not await _douyin_wait_until_publish_clickable(page, btn_wait, _step, "视频"):
            return {
                "ok": False,
                "error": "视频可能仍在上传/转码，或发布按钮不可用，请稍后重试或手动发布",
                "applied": applied,
            }

        await _human_delay(1, 2)
        await _dismiss_overlays(page, "video_before_publish")

        # ── Find and click publish button ──
        publish_btn = await _find_publish_button(page, "VIDEO")
        if not publish_btn:
            _step("找到发布按钮", False)
            return {"ok": False, "error": "找不到发布按钮", "applied": applied}

        try:
            verify_txt = (await publish_btn.inner_text()).strip()
            logger.info("[DOUYIN-VIDEO] verified button: '%s'", verify_txt)
            if verify_txt != "发布":
                _step("找到发布按钮", False, error=f"按钮文字不对: {verify_txt}")
                return {"ok": False, "error": f"找到的按钮文字是'{verify_txt}'而非'发布'", "applied": applied}
        except Exception:
            pass

        _step("找到发布按钮", True)

        if options.get("dry_run"):
            return {"ok": True, "url": page.url, "applied": applied, "dry_run": True}

        logger.info("[DOUYIN-VIDEO] clicking publish...")
        await _click_publish_button(page, publish_btn, "video")
        _step("点击发布按钮", True)

        return await self._check_publish_result(page, applied, _step)

    # ===================================================================
    # Check publish result
    # ===================================================================
    async def _check_publish_result(self, page, applied, _step):
        pre_url = page.url or ""
        logger.info("[DOUYIN-PUBLISH] waiting for result, pre_url=%s", pre_url)
        publish_ok = False
        error_msg = ""

        # 整页 innerText 易出现「发布完成」等说明文案误判；正文仅接受更明确的关键词，「发布完成」仅信 toast 容器
        _JS_CHECK = """
        () => {
            const r = {status:'unknown'};
            const body = document.body ? document.body.innerText : '';
            const okStrict = ['发布成功', '作品发布成功', '已发布', '作品已发布', '提交成功'];
            const okToastExtra = ['发布完成'];
            const fail = ['发布失败', '上传失败', '审核不通过', '内容不符合', '审核未通过', '请选择封面', '必须选择封面'];
            for (const k of okStrict) { if (body.includes(k)) return {status:'success', keyword:k, src:'body_strict'}; }
            for (const k of fail) { if (body.includes(k)) return {status:'fail', keyword:k, src:'body'}; }

            const toastSels = [
                '.semi-toast-content', '.semi-toast-wrapper',
                '.semi-notification-content', '.semi-notification',
                '[class*="toast"]', '[class*="Toast"]',
                '[class*="notice"]', '[class*="message"]',
            ];
            const okAll = okStrict.concat(okToastExtra);
            for (const sel of toastSels) {
                for (const el of document.querySelectorAll(sel)) {
                    const t = (el.textContent || '').trim();
                    if (!t) continue;
                    for (const k of okAll) { if (t.includes(k)) return {status:'success', keyword:k, src:'toast'}; }
                    for (const k of fail) { if (t.includes(k)) return {status:'fail', keyword:k, src:'toast'}; }
                }
            }
            return r;
        }
        """

        for ci in range(20):
            if ci < 3:
                await asyncio.sleep(1)
            else:
                await _human_delay(1.5, 3)

            cur = page.url or ""
            logger.info("[DOUYIN-PUBLISH] check[%d] url=%s", ci, cur)

            # URL is on manage page = success
            if "/content/manage" in cur:
                logger.info("[DOUYIN-PUBLISH] on manage page => success")
                publish_ok = True
                break

            # JS text / toast scan
            try:
                jr = await page.evaluate(_JS_CHECK)
                logger.info("[DOUYIN-PUBLISH] JS: %s", jr)
                if jr.get("status") == "success":
                    publish_ok = True
                    break
                elif jr.get("status") == "fail":
                    error_msg = jr.get("keyword", "发布失败")
                    break
            except Exception:
                pass

            if ci in (3, 7, 11, 15):
                await _dismiss_overlays(page, f"result_check_{ci}")

        # Final URL check
        final_url = page.url or ""
        if not publish_ok and not error_msg:
            if "/content/manage" in final_url:
                publish_ok = True
            elif "/content/upload" in final_url and final_url != pre_url:
                error_msg = "页面跳转到上传页(可能点了暂存而非发布)"

        logger.info("[DOUYIN-PUBLISH] === DONE === ok=%s url=%s err=%s", publish_ok, final_url, error_msg)

        if publish_ok:
            _step("发布成功", True, url=final_url)
            return {"ok": True, "url": final_url, "applied": applied}
        elif error_msg:
            _step("发布失败", False, error=error_msg, url=final_url)
            return {"ok": False, "error": f"发布失败: {error_msg}", "url": final_url, "applied": applied}
        else:
            _step("发布状态不确定", False, url=final_url)
            return {
                "ok": False,
                "error": f"点击发布后未检测到成功标志（当前页面: {final_url}），请手动确认",
                "url": final_url,
                "applied": applied,
            }
