"""TikTok 版评论区/搜索采集器（工作台引擎）。

与抖音版保持同一套方法名和返回结构，内部使用 TikTok 页面 DOM。
私信、陌生人私信、群成员、评论 @ 提及、账号养护均按 TikTok 页面 DOM 单独实现。
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Awaitable, Callable, Dict, List, Optional
from urllib.parse import quote

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from .console_safe import safe_print
from .tiktok_client import TikTokClient, is_port_open

TIKTOK_COMMENT_COLLECTION_ATTEMPTS = 2
TIKTOK_COMMENT_COLLECTION_RETRY_BACKOFF_SECONDS = [1.0, 2.0]


class TikTokPrivateMessageUnavailable(RuntimeError):
    """TikTok 私信自动化暂未接入。"""


class TikTokMentionCommentStopped(RuntimeError):
    """提及评论任务被主动停止。"""


class TikTokCommentCollectionUnconfirmed(RuntimeError):
    """评论区已打开但无法确认评论内容（不是空评论）。"""


class TikTokSearchUpstreamError(RuntimeError):
    def __init__(self, status: int, url: str, body: str = ""):
        self.status = int(status or 0)
        self.url = url
        self.body = body or ""
        super().__init__(f"TikTok 搜索页返回 HTTP {self.status}：{self.url}")


def _emit(logger: Optional[Callable[[str, str], None]], message: str, level: str = "info") -> None:
    safe_print(message)
    if logger:
        try:
            logger(message, level)
        except Exception:
            pass


def extract_aweme_id(video_url: str) -> str:
    text = str(video_url or "").strip()
    match = re.search(r"/video/(\d+)", text)
    if match:
        return match.group(1)
    match = re.search(r"(\d{8,})", text)
    return match.group(1) if match else ""


def parse_count_text(text: str) -> int:
    raw = str(text or "").strip().lower().replace(",", "")
    if not raw:
        return 0
    match = re.search(r"(\d+(?:\.\d+)?)\s*([kmbw万千]?)", raw)
    if not match:
        return 0
    value = float(match.group(1))
    unit = match.group(2)
    multiplier = {"k": 1000, "千": 1000, "m": 1000000, "万": 10000, "b": 1000000000, "w": 10000}.get(unit, 1)
    return int(value * multiplier)


def _normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


_UNAVAILABLE_MESSAGE = "TikTok 版暂未接入该能力（当前版本支持：搜索、评论采集、视频评论、关注+评论、账号养护）。"
class TikTokCommentScraper:
    def __init__(
        self,
        headless: Optional[bool] = None,
        account_id: int | None = None,
        cdp_port: int | None = None,
        allow_workspace_fallback: bool = True,
        allow_cdp_reuse: bool = True,
    ):
        env_headless = os.environ.get("TIKTOK_HEADLESS", "").strip().lower()
        if headless is None:
            headless = env_headless in {"1", "true", "yes", "on"}
        self.headless = headless
        self.account_id = account_id
        self.cdp_port = int(cdp_port or 0) if cdp_port else None
        self.allow_workspace_fallback = bool(allow_workspace_fallback)
        self.allow_cdp_reuse = bool(allow_cdp_reuse)
        self.profile_dir = TikTokClient(self.cdp_port or 9419, account_id=account_id).resolve_profile_dir()
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._owns_browser = False
        self._owns_context = False
        self.last_login_probe: Dict[str, object] = {"state": "unknown", "reason": "not_checked"}

    def _emit(self, logger: Optional[Callable[[str, str], None]], message: str, level: str = "info") -> None:
        _emit(logger, message, level)

    async def _ensure_browser(self, logger: Optional[Callable[[str, str], None]] = None) -> Browser:
        if self._browser is not None:
            try:
                if self._browser.is_connected():
                    return self._browser
            except Exception:
                self._browser = None

        port = int(self.cdp_port or 9419)
        self._playwright = await async_playwright().start()

        if self.allow_cdp_reuse and is_port_open(port):
            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                self._owns_browser = False
                self._emit(logger, f"[TikTok] 已接管调试端口 {port}")
                return self._browser
            except Exception as exc:
                self._emit(logger, f"[TikTok] 接管端口 {port} 失败：{exc}", "warning")

        client = TikTokClient(port, account_id=self.account_id)
        if not client.launch_browser("https://www.tiktok.com/"):
            raise RuntimeError(f"TikTok 浏览器启动失败，profile={self.profile_dir}")

        deadline = time.time() + 20
        last_error: Optional[Exception] = None
        while time.time() < deadline:
            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                self._owns_browser = False
                return self._browser
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.5)
        raise RuntimeError(f"TikTok 调试端口 {port} 连接失败：{last_error}")

    async def _new_page(self, logger: Optional[Callable[[str, str], None]] = None) -> Page:
        browser = await self._ensure_browser(logger=logger)
        contexts = list(browser.contexts)
        if not contexts:
            raise RuntimeError("TikTok 浏览器没有可用的上下文")
        context = contexts[0]
        self._context = context
        page = await context.new_page()
        page.set_default_timeout(30000)
        return page

    async def close(self):
        try:
            if self._playwright is not None and self._owns_browser and self._browser is not None:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception:
            pass
        self._playwright = None
        self._browser = None
        self._context = None

    async def check_login_state(self, logger: Optional[Callable[[str, str], None]] = None) -> bool:
        self.last_login_probe = {"state": "unknown", "reason": "probe_started"}
        page = await self._new_page(logger=logger)
        try:
            await page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=45000)
            # TikTok 首屏是懒加载：先等页面出现作者主页链接或登录按钮，再判定。
            for _ in range(10):
                await page.wait_for_timeout(1000)
                try:
                    ready = await page.evaluate(
                        "() => !!document.querySelector('a[href^=\"/@\"]') || "
                        "Array.from(document.querySelectorAll('button, a')).some((el) => /^(log in|\u767b\u5f55|\u767b\u5165)$/i.test(String(el.innerText || '').trim()))"
                    )
                except Exception:
                    ready = False
                if ready:
                    break
            cookies = []
            try:
                cookies = await page.context.cookies(["https://www.tiktok.com"])
            except Exception:
                cookies = []
            names = {str(c.get("name", "")).lower() for c in cookies if isinstance(c, dict) and str(c.get("value", "")).strip()}
            has_session = bool(names & {"sessionid", "sessionid_ss", "sid_tt"})
            state = await page.evaluate(
                """
                () => {
                    const loginButton = Array.from(document.querySelectorAll('button, a'))
                        .some((el) => /^(log in|登录|登入)$/i.test(String(el.innerText || '').trim()));
                    const profileLink = document.querySelector('a[href^="/@"]');
                    return {loginButton, hasProfileLink: !!profileLink, path: location.pathname};
                }
                """
            )
            login_button = bool(state.get("loginButton"))
            has_profile = bool(state.get("hasProfileLink"))
            result = bool(has_session and not login_button and has_profile)
            self.last_login_probe = {
                "state": "online" if result else ("waiting" if not has_session else "unknown"),
                "reason": "session_cookie" if has_session else "no_session_cookie",
            }
            self._emit(logger, f"[TikTok] 登录态检测：{self.last_login_probe['state']}")
            return result
        except Exception as exc:
            self.last_login_probe = {"state": "unknown", "reason": f"probe_error:{exc}"}
            self._emit(logger, f"[TikTok] 登录态检测异常：{exc}", "warning")
            return False
        finally:
            try:
                await page.close()
            except Exception:
                pass
    async def scrape_search_results(
        self,
        keyword: str,
        max_results: int = 50,
        logger: Optional[Callable[[str, str], None]] = None,
        sort_type: Optional[str] = None,
        publish_time: Optional[str] = None,
    ) -> List[Dict]:
        keyword = _normalize_text(keyword)
        if not keyword:
            return []
        page = await self._new_page(logger=logger)
        try:
            url = f"https://www.tiktok.com/search/video?q={quote(keyword)}"
            self._emit(logger, f"[TikTok搜索] 打开搜索页：{keyword}")
            response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            status = int(response.status) if response is not None and response.status else 0
            if status >= 400:
                raise TikTokSearchUpstreamError(status, url)
            await page.wait_for_timeout(4000)

            collected: Dict[str, Dict] = {}
            stable_rounds = 0
            for _ in range(10):
                rows = await page.evaluate(
                    """
                    () => {
                      const out = [];
                      const seen = new Set();
                      for (const a of document.querySelectorAll('a[href*="/video/"]')) {
                        const href = a.href || '';
                        if (!href || seen.has(href)) continue;
                        seen.add(href);
                        const parts = href.split('/');
                        const videoAt = parts.indexOf('video');
                        const author = (parts[3] || '').split('@').join('');
                        const awemeId = videoAt >= 0 ? (parts[videoAt + 1] || '').split('?')[0] : '';
                        if (!author || !awemeId) continue;
                        const img = a.querySelector('img');
                        let title = (img && (img.alt || '')) || '';
                        if (!title) title = (a.innerText || '').split(String.fromCharCode(10))[0] || '';
                        const cover = img ? (img.currentSrc || img.src || '') : '';
                        const text = (a.innerText || '').trim();
                        out.push({url: href, author: author, aweme_id: awemeId, title: title.slice(0, 300), cover_image: cover, text: text.slice(0, 400)});
                      }
                      return out;
                    }
                    """
                )
                before = len(collected)
                for row in rows or []:
                    aweme_id = str(row.get("aweme_id", "") or "")
                    if not aweme_id or aweme_id in collected:
                        continue
                    text = str(row.get("text", "") or "")
                    numbers = re.findall(r"\d+(?:\.\d+)?[KMB\u4e07\u4ebf]?", text)
                    likes_text = numbers[-2] if len(numbers) >= 2 else (numbers[-1] if numbers else "")
                    comments_text = numbers[-1] if len(numbers) >= 2 else ""
                    collected[aweme_id] = {
                        "platform": "tiktok",
                        "aweme_id": aweme_id,
                        "url": row.get("url", ""),
                        "title": _normalize_text(row.get("title", ""))[:300],
                        "author": row.get("author", ""),
                        "cover_image": row.get("cover_image", ""),
                        "likes": parse_count_text(likes_text),
                        "likes_text": likes_text,
                        "comments": parse_count_text(comments_text),
                        "comments_text": comments_text,
                        "publish_time": "",
                        "duration": "",
                        "collects": 0,
                    }
                    if len(collected) >= max_results:
                        break
                if len(collected) >= max_results:
                    break
                stable_rounds = stable_rounds + 1 if len(collected) == before else 0
                if stable_rounds >= 3:
                    break
                await page.mouse.wheel(0, 1800)
                await page.wait_for_timeout(1200)

            self._emit(logger, f"[TikTok搜索] 关键词 {keyword} 采集到 {len(collected)} 条视频")
            return list(collected.values())[:max_results]
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def _open_comment_panel(self, page: Page, logger: Optional[Callable[[str, str], None]] = None) -> bool:
        for _ in range(2):
            try:
                clicked = await page.evaluate(
                    """
                    () => {
                      const direct = document.querySelector('[data-e2e="comment-icon"]');
                      if (direct) { (direct.closest('button') || direct).click(); return true; }
                      const wanted = new Set(['评论', 'comments', 'comment']);
                      const nodes = Array.from(document.querySelectorAll('button, [role="button"], div, span, p'));
                      for (const n of nodes) {
                        const text = String(n.innerText || '').trim().toLowerCase();
                        if (!wanted.has(text)) continue;
                        const rect = n.getBoundingClientRect();
                        if (rect.width < 5 || rect.height < 5) continue;
                        if (rect.left < window.innerWidth * 0.45) continue;
                        n.click();
                        return true;
                      }
                      const fallback = document.querySelector('[data-e2e="comments"]');
                      if (fallback) { fallback.click(); return true; }
                      return false;
                    }
                    """
                )
                if clicked:
                    await page.wait_for_timeout(2500)
                    return True
            except Exception:
                pass
            await page.wait_for_timeout(800)
        return False

    async def _collect_comments_from_page(
        self,
        page: Page,
        max_comments: int,
        max_scroll_rounds: int,
        logger: Optional[Callable[[str, str], None]] = None,
        progress_callback: Optional[Callable[[Dict], None]] = None,
        on_batch: Optional[Callable[[Page, List[Dict], int], Awaitable[None]]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> List[Dict]:
        opened = await self._open_comment_panel(page, logger=logger)
        if not opened:
            self._emit(logger, "[TikTok评论] 未找到评论入口，尝试等待评论区", "warning")
        try:
            await page.wait_for_selector('[data-e2e="comment-level-1"]', timeout=20000)
        except Exception:
            body = ""
            try:
                body = await page.locator("body").inner_text(timeout=3000)
            except Exception:
                pass
            if re.search(r"(no comments yet|暂无评论|还没有评论|be the first)", body or "", re.I):
                return []
            if not opened:
                raise TikTokCommentCollectionUnconfirmed("评论区未打开")
            return []

        collected: Dict[str, Dict] = {}
        max_comments = max(1, min(int(max_comments or 80), 2000))
        max_scroll_rounds = max(1, min(int(max_scroll_rounds or 18), 300))
        stable_rounds = 0
        for round_index in range(1, max_scroll_rounds + 1):
            if should_stop and should_stop():
                break
            rows = await page.evaluate(
                """
                () => {
                  const out = [];
                  const texts = Array.from(document.querySelectorAll('[data-e2e="comment-level-1"]'));
                  texts.forEach((node, index) => {
                    let wrapper = node;
                    let user = null;
                    for (let i = 0; i < 8 && wrapper; i++) {
                      if (wrapper.matches && wrapper.matches('[data-e2e="comment-username-1"]')) { user = wrapper; break; }
                      const found = wrapper.querySelector ? wrapper.querySelector('[data-e2e="comment-username-1"]') : null;
                      if (found) { user = found; break; }
                      wrapper = wrapper.parentElement;
                    }
                    const username = String(user ? user.innerText : '').trim().replace(/^@+/, '');
                    const holder = node.closest('[class*="DivCommentItemWrapper"]');
                    const link = (user && user.closest('a')) || (holder ? holder.querySelector('a[href^="/@"]') : null);
                    const href = link ? link.getAttribute('href') : '';
                    const content = String(node.innerText || '').trim();
                    const likeEl = wrapper && wrapper.querySelector ? wrapper.querySelector('[data-e2e="comment-like-count"]') : null;
                    out.push({
                      index,
                      username,
                      content: content.slice(0, 2000),
                      profile_url: href ? ('https://www.tiktok.com' + href) : '',
                      likes_text: likeEl ? String(likeEl.innerText || '').trim() : '',
                      comment_time: '',
                    });
                  });
                  return out;
                }
                """
            )
            before = len(collected)
            batch: List[Dict] = []
            for row in rows or []:
                username = _normalize_text(row.get("username", ""))
                content = _normalize_text(row.get("content", ""))
                if not username or not content:
                    continue
                key = f"{username}|{content[:120]}"
                if key in collected:
                    continue
                item = {
                    "comment_id": key,
                    "user_id": "",
                    "sec_user_id": "",
                    "username": username,
                    "content": content,
                    "avatar": "",
                    "avatar_url": "",
                    "like_count": parse_count_text(row.get("likes_text", "")),
                    "comment_time": row.get("comment_time", ""),
                    "profile_url": row.get("profile_url", ""),
                    "is_high_intent": False,
                }
                collected[key] = item
                batch.append(item)
                if len(collected) >= max_comments:
                    break

            if on_batch and batch:
                try:
                    await on_batch(page, batch, len(collected))
                except Exception as exc:
                    self._emit(logger, f"[TikTok评论] 批处理回调失败：{exc}", "warning")
            if progress_callback:
                try:
                    progress_callback(
                        {
                            "phase": "collecting",
                            "collected_comments": len(collected),
                            "visible_comments": len(rows or []),
                            "scroll_round": round_index,
                            "scroll_round_limit": max_scroll_rounds,
                            "last_message": f"已采集 {len(collected)} 条评论",
                        }
                    )
                except Exception:
                    pass

            if len(collected) >= max_comments:
                break
            stable_rounds = stable_rounds + 1 if len(collected) == before else 0
            if stable_rounds >= 5:
                break
            await page.evaluate(
                """
                () => {
                  const list = document.querySelector('[class*="DivCommentListContainer"]');
                  if (list) { list.scrollBy(0, 700); return; }
                  let el = document.querySelector('[data-e2e="comment-level-1"]');
                  while (el) {
                    if (el.scrollHeight > el.clientHeight + 20) { el.scrollBy(0, 700); return; }
                    el = el.parentElement;
                  }
                  window.scrollBy(0, 700);
                }
                """
            )
            await page.wait_for_timeout(900)

        return list(collected.values())[:max_comments]

    async def scrape_video_comments(
        self,
        video_url: str,
        max_comments: int = 80,
        max_scroll_rounds: int = 18,
        logger: Optional[Callable[[str, str], None]] = None,
        progress_callback: Optional[Callable[[Dict], None]] = None,
    ) -> List[Dict]:
        page = await self._new_page(logger=logger)
        try:
            self._emit(logger, f"[TikTok评论] 打开视频：{video_url}")
            await page.goto(str(video_url or "").strip(), wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3500)
            return await self._collect_comments_from_page(
                page,
                max_comments=max_comments,
                max_scroll_rounds=max_scroll_rounds,
                logger=logger,
                progress_callback=progress_callback,
            )
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def open_video_comment_page(
        self,
        video_url: str,
        expected_title: str = "",
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Page:
        video_url = str(video_url or "").strip()
        if not video_url:
            raise ValueError("\u7f3a\u5c11\u4f5c\u54c1\u5730\u5740")
        page = await self._new_page(logger=logger)
        try:
            self._emit(logger, f"[TikTok评论区] 打开作品：{expected_title or video_url}")
            await page.goto(video_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3500)
            await self._open_comment_panel(page, logger=logger)
            try:
                await page.wait_for_selector('[data-e2e="comment-level-1"]', timeout=12000)
            except Exception:
                pass
            return page
        except Exception:
            try:
                await page.close()
            except Exception:
                pass
            raise

    async def process_video_comment_batches(
        self,
        video_url: str,
        *,
        max_comments: int = 80,
        batch_size: int = 10,
        max_scroll_rounds: int = 18,
        logger: Optional[Callable[[str, str], None]] = None,
        on_batch: Optional[Callable[[Page, List[Dict], int], Awaitable[None]]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> List[Dict]:
        page = await self.open_video_comment_page(video_url, logger=logger)
        try:
            return await self._collect_comments_from_page(
                page,
                max_comments=max_comments,
                max_scroll_rounds=max_scroll_rounds,
                logger=logger,
                on_batch=on_batch,
                should_stop=should_stop,
            )
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def _type_into_comment_box(self, page: Page, text: str) -> bool:
        candidates = [
            '[data-e2e="comment-input"] div[contenteditable="true"]',
            '[data-e2e="comment-input"]',
            'div[contenteditable="true"][role="textbox"]',
            'div[contenteditable="true"]',
        ]
        for selector in candidates:
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    continue
                await locator.click(timeout=5000, force=True)
                await page.wait_for_timeout(300)
                await page.keyboard.type(text, delay=25)
                await page.wait_for_timeout(300)
                return True
            except Exception:
                continue
        return False

    async def send_video_comment(
        self,
        video_url: str,
        comment_text: str,
        expected_title: str = "",
        image_path: str = "",
        use_modal_entry: bool = False,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Dict:
        video_url = str(video_url or "").strip()
        comment_text = str(comment_text or "").strip()
        if not video_url:
            raise ValueError("\u7f3a\u5c11\u4f5c\u54c1\u5730\u5740")
        if not comment_text:
            raise ValueError("\u8bc4\u8bba\u5185\u5bb9\u4e0d\u80fd\u4e3a\u7a7a")
        if image_path:
            raise RuntimeError("TikTok \u7248\u6682\u4e0d\u652f\u6301\u56fe\u7247\u8bc4\u8bba")

        page = await self._new_page(logger=logger)
        try:
            self._emit(logger, f"[TikTok视频评论] 打开视频：{expected_title or video_url}")
            await page.goto(video_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3500)
            await self._open_comment_panel(page, logger=logger)
            if not await self._type_into_comment_box(page, comment_text):
                raise RuntimeError("\u672a\u627e\u5230 TikTok \u8bc4\u8bba\u8f93\u5165\u6846")
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2500)
            body = ""
            try:
                body = await page.locator("body").inner_text(timeout=3000)
            except Exception:
                pass
            if comment_text[:20] not in (body or ""):
                raise RuntimeError("\u8bc4\u8bba\u5df2\u63d0\u4ea4\u4f46\u9875\u9762\u672a\u786e\u8ba4\u663e\u793a\uff0c\u53ef\u80fd\u88ab\u98ce\u63a7\u62e6\u622a")
            self._emit(logger, f"[TikTok视频评论] 已发送：{expected_title or video_url}", "success")
            return {
                "success": True,
                "video_url": video_url,
                "target_url": video_url,
                "comment_text": comment_text,
                "image_path": "",
                "title": expected_title,
            }
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def reply_to_loaded_video_comment(
        self,
        page: Page,
        reply_text: str,
        target_comment: Dict,
        image_path: str = "",
        logger: Optional[Callable[[str, str], None]] = None,
        allow_scroll: bool = False,
    ) -> Dict:
        reply_text = str(reply_text or "").strip()
        if not reply_text:
            raise ValueError("\u56de\u590d\u5185\u5bb9\u4e0d\u80fd\u4e3a\u7a7a")
        if image_path:
            raise RuntimeError("TikTok \u7248\u6682\u4e0d\u652f\u6301\u56fe\u7247\u56de\u590d")
        username = _normalize_text((target_comment or {}).get("username", ""))
        if not username:
            raise ValueError("\u7f3a\u5c11\u76ee\u6807\u8bc4\u8bba\u7528\u6237\u540d")

        clicked = await page.evaluate(
            """
            (username) => {
              const nodes = Array.from(document.querySelectorAll('[data-e2e="comment-level-1"]'));
              for (const node of nodes) {
                const user = node.querySelector('[data-e2e="comment-username-1"]');
                if (!user || String(user.innerText || '').trim().replace(/^@+/, '') !== username) continue;
                const buttons = Array.from(node.querySelectorAll('button, [role="button"], span'));
                const replyBtn = buttons.find((b) => /^(reply|\u56de\u590d)$/i.test(String(b.innerText || '').trim()));
                const fallback = node.querySelector('[data-e2e="comment-reply-1"]');
                (replyBtn || fallback || node).click();
                return true;
              }
              return false;
            }
            """,
            username,
        )
        if not clicked:
            raise RuntimeError(f"\u672a\u627e\u5230\u7528\u6237 {username} \u7684\u8bc4\u8bba")
        await page.wait_for_timeout(800)
        if not await self._type_into_comment_box(page, f"@{username} {reply_text}"):
            raise RuntimeError("\u672a\u627e\u5230\u56de\u590d\u8f93\u5165\u6846")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(2000)
        return {"success": True, "reply_text": reply_text, "image_path": "", "target_comment": target_comment}

    async def reply_to_video_comment(
        self,
        video_url: str,
        reply_text: str,
        target_comment: Dict,
        image_path: str = "",
        expected_title: str = "",
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Dict:
        page = await self.open_video_comment_page(video_url, expected_title=expected_title, logger=logger)
        try:
            return await self.reply_to_loaded_video_comment(
                page, reply_text, target_comment, image_path=image_path, logger=logger
            )
        finally:
            try:
                await page.close()
            except Exception:
                pass
    async def _scrape_profile_page(self, page: Page, profile_url: str, max_videos: int) -> Dict:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
        for _ in range(12):
            await page.wait_for_timeout(1000)
            try:
                ready = await page.evaluate("() => document.querySelectorAll('a[href*=\"/video/\"]').length > 0")
            except Exception:
                ready = False
            if ready:
                break
        profile = await page.evaluate(
            """
            () => {
              const pick = (sel) => { const el = document.querySelector(sel); return el ? String(el.innerText || '').trim() : ''; };
              const name = pick('[data-e2e="user-title"]') || pick('h1');
              const bio = pick('[data-e2e="user-bio"]');
              return {nickname: name, bio};
            }
            """
        )
        videos = await page.evaluate(
            """
            () => {
              const out = []; const seen = new Set();
              for (const a of document.querySelectorAll('a[href*="/video/"]')) {
                const href = a.href || '';
                if (!href || seen.has(href)) continue;
                seen.add(href);
                const m = href.match(/@([^/]+)\\/video\\/(\\d+)/);
                if (!m) continue;
                const img = a.querySelector('img');
                out.push({url: href, aweme_id: m[2], author: m[1], title: (img && img.alt) || '', cover_image: img ? (img.currentSrc || img.src || '') : ''});
              }
              return out;
            }
            """
        )
        handle_match = re.search(r"/@([^/?#]+)", profile_url)
        handle = handle_match.group(1) if handle_match else ""
        display_name = _normalize_text((profile or {}).get("nickname", ""))
        profile_data = {
            "profile_url": profile_url,
            "username": handle or display_name,
            "nickname": display_name or handle,
            "bio": _normalize_text((profile or {}).get("bio", "")),
            "sec_user_id": "",
        }
        rows = []
        for item in (videos or [])[: max(0, int(max_videos or 0))]:
            rows.append(
                {
                    "platform": "tiktok",
                    "aweme_id": item.get("aweme_id", ""),
                    "url": item.get("url", ""),
                    "title": _normalize_text(item.get("title", ""))[:300],
                    "author": item.get("author", ""),
                    "cover_image": item.get("cover_image", ""),
                    "likes": 0,
                    "likes_text": "",
                    "comments": 0,
                    "comments_text": "",
                    "publish_time": "",
                    "duration": "",
                    "collects": 0,
                }
            )
        return {"profile": profile_data, "videos": rows}

    async def scrape_profile_videos(
        self,
        profile_url: str,
        max_videos: int = 10,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Dict:
        page = await self._new_page(logger=logger)
        try:
            return await self._scrape_profile_page(page, str(profile_url or "").strip(), max_videos)
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def scrape_profile_summary(
        self,
        profile_url: str,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Dict:
        payload = await self.scrape_profile_videos(profile_url, max_videos=0, logger=logger)
        return payload.get("profile", {})

    async def _self_profile_url(self, page: Page) -> str:
        await page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(2500)
        href = await page.evaluate(
            """
            () => {
              const links = Array.from(document.querySelectorAll('a[href^="/@"]'));
              for (const a of links) {
                const text = String(a.innerText || '');
                const cls = String(a.className || '');
                if (/profile|avatar|\u4e2a\u4eba|\u8d26\u53f7/i.test(text + ' ' + cls)) return a.getAttribute('href');
              }
              return links.length ? links[0].getAttribute('href') : '';
            }
            """
        )
        if not href:
            raise RuntimeError("\u672a\u627e\u5230\u5f53\u524d\u767b\u5f55\u8d26\u53f7\u4e3b\u9875\u5165\u53e3\uff08\u53ef\u80fd\u672a\u767b\u5f55\uff09")
        return "https://www.tiktok.com" + href

    async def scrape_self_videos(
        self,
        max_videos: int = 10,
        should_stop: Optional[Callable[[], bool]] = None,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Dict:
        page = await self._new_page(logger=logger)
        try:
            profile_url = await self._self_profile_url(page)
            return await self._scrape_profile_page(page, profile_url, max_videos)
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def follow_user_and_find_first_post(
        self,
        profile_url: str,
        expected_username: str = "",
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Dict:
        profile_url = str(profile_url or "").strip()
        if not profile_url:
            raise ValueError("\u7f3a\u5c11\u7528\u6237\u4e3b\u9875\u5730\u5740")
        page = await self._new_page(logger=logger)
        try:
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            followed = await page.evaluate(
                """
                () => {
                  const buttons = Array.from(document.querySelectorAll('button, [role="button"]'));
                  const btn = buttons.find((b) => /^(follow|\u5173\u6ce8)$/i.test(String(b.innerText || '').trim()));
                  if (!btn) return false;
                  btn.click();
                  return true;
                }
                """
            )
            await page.wait_for_timeout(1500)
            video_url = await page.evaluate(
                """
                () => {
                  const a = document.querySelector('a[href*="/video/"]');
                  return a ? a.href : '';
                }
                """
            )
            if not video_url:
                raise RuntimeError("\u4e3b\u9875\u6ca1\u6709\u53ef\u7528\u4f5c\u54c1")
            return {"success": True, "followed": bool(followed), "video_url": video_url, "username": expected_username}
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def follow_user_and_comment_first_post(
        self,
        profile_url: str,
        comment_text: str,
        expected_username: str = "",
        image_path: str = "",
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Dict:
        if image_path:
            raise RuntimeError("TikTok \u7248\u6682\u4e0d\u652f\u6301\u56fe\u7247\u8bc4\u8bba")
        result = await self.follow_user_and_find_first_post(profile_url, expected_username=expected_username, logger=logger)
        video_url = result.get("video_url", "")
        comment_result = await self.send_video_comment(video_url, comment_text, expected_title=expected_username, logger=logger)
        return {"success": True, "followed": result.get("followed"), "video_url": video_url, "comment": comment_result}

    # ------------------------------------------------------------------ 私信 / 消息中心
    async def open_chat_workspace_page(self, logger: Optional[Callable[[str, str], None]] = None) -> Page:
        page = await self._new_page(logger=logger)
        try:
            await page.goto("https://www.tiktok.com/messages", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4500)
            return page
        except Exception:
            try:
                await page.close()
            except Exception:
                pass
            raise

    async def _list_conversations(self, page: Page) -> List[Dict]:
        rows = await page.evaluate(
            """
            () => {
              const out = [];
              const seen = new Set();
              const push = (username, href, row) => {
                if (!username || seen.has(username) || !row) return;
                const img = row.querySelector('img');
                const rect = row.getBoundingClientRect();
                const text = String(row.innerText || '');
                const lines = text.split(String.fromCharCode(10)).map((s) => s.trim()).filter(Boolean);
                // 真正的会话条目：有头像、有昵称+最后一条消息、尺寸像列表行
                if (!img || lines.length < 2 || rect.width < 180 || rect.height < 40) return;
                seen.add(username);
                out.push({username: username, profile_url: href ? ('https://www.tiktok.com' + href) : '', lines: lines, avatar: img ? (img.currentSrc || img.src || '') : ''});
              };
              for (const a of document.querySelectorAll('a[href^="/@"]')) {
                const rect = a.getBoundingClientRect();
                if (rect.width < 30 || rect.height < 16) continue;
                if (rect.left > window.innerWidth * 0.6) continue;
                if (!a.closest('[class*="ChatList"], [class*="ChatItem"], [class*="DivChat"], [data-e2e*="dm"]')) continue;
                const href = a.getAttribute('href') || '';
                const username = href.slice(2).split('/')[0].split('?')[0];
                let row = a;
                for (let i = 0; i < 6 && row.parentElement; i++) {
                  if (row.getBoundingClientRect().width > 180) break;
                  row = row.parentElement;
                }
                push(username, href, row);
              }
              for (const node of document.querySelectorAll('[class*="DivChatItem"], [data-e2e="dm-conversation-item"]')) {
                const rect = node.getBoundingClientRect();
                if (rect.width < 120 || rect.height < 30) continue;
                const link = node.querySelector('a[href^="/@"]');
                const href = link ? link.getAttribute('href') : '';
                let username = href ? href.slice(2).split('/')[0].split('?')[0] : '';
                const lines = String(node.innerText || '').split(String.fromCharCode(10)).map((s) => s.trim()).filter(Boolean);
                if (!username) username = lines[0] || '';
                push(username, href, node);
              }
              return out;
            }
            """
        )
        result = []
        for index, row in enumerate(rows or []):
            lines = [str(item) for item in (row.get("lines") or []) if str(item).strip()]
            username = str(row.get("username", "") or "").strip()
            if not username:
                continue
            preview = ""
            for line in lines:
                if line and line != username:
                    preview = line
            result.append(
                {
                    "username": username,
                    "profile_url": str(row.get("profile_url", "") or "").strip(),
                    "avatar_url": str(row.get("avatar", "") or "").strip(),
                    "preview_text": preview,
                    "time_text": lines[-1] if len(lines) > 1 and lines[-1] != preview else "",
                    "index": index,
                    "is_group": bool(row.get("is_group")),
                }
            )
        return result

    async def _open_conversation_by_username(self, page: Page, username: str, profile_url: str = "") -> bool:
        username = str(username or "").strip()
        if not username:
            return False
        clicked = await page.evaluate(
            """
            (username) => {
              const wanted = String(username || '').trim().toLowerCase();
              const rows = Array.from(document.querySelectorAll('a[href^="/@"], [class*="DivChatItem"], [data-e2e="dm-conversation-item"]'));
              for (const node of rows) {
                const rect = node.getBoundingClientRect();
                if (rect.width < 30 || rect.height < 16) continue;
                const href = node.getAttribute('href') || '';
                const handle = href ? href.slice(2).split('/')[0].split('?')[0].toLowerCase() : '';
                const lines = String(node.innerText || '').toLowerCase().split(String.fromCharCode(10)).map((s) => s.trim());
                if (handle === wanted || lines.indexOf(wanted) >= 0) {
                  (node.closest('a') || node).click();
                  return true;
                }
                if (String(node.innerText || '').toLowerCase().indexOf('@' + wanted) >= 0) {
                  (node.closest('a') || node).click();
                  return true;
                }
              }
              return false;
            }
            """,
            username,
        )
        if not clicked:
            return False
        await page.wait_for_timeout(2500)
        return True
    async def open_chat_page_conversation(self, page: Page, row: Dict) -> tuple[bool, str]:
        username = str((row or {}).get("username", "") or "").strip()
        profile_url = str((row or {}).get("profile_url", "") or "").strip()
        if not username and profile_url:
            username = profile_url.rstrip("/").split("/@")[-1].split("/")[0]
        if not username:
            return False, "缺少会话用户名"
        try:
            if not str(page.url or "").startswith("https://www.tiktok.com/messages"):
                await page.goto("https://www.tiktok.com/messages", wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3500)
            opened = await self._open_conversation_by_username(page, username, profile_url)
            if not opened and profile_url:
                await page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2500)
                clicked = await page.evaluate(
                    """
                    () => {
                      const nodes = Array.from(document.querySelectorAll('button, [role="button"], a'));
                      const btn = nodes.find((n) => /^(message|\u53d1\u6d88\u606f|\u804a\u5929)$/i.test(String(n.innerText || '').trim()));
                      if (btn) { btn.click(); return true; }
                      const icon = document.querySelector('[data-e2e="message-button"], [data-e2e="dm-button"]');
                      if (icon) { icon.click(); return true; }
                      return false;
                    }
                    """
                )
                if clicked:
                    await page.wait_for_timeout(3500)
                    opened = True
            if not opened:
                return False, f"消息列表中没有找到 {username} 的会话"
            return True, ""
        except Exception as exc:
            return False, f"打开会话失败：{exc}"

    async def _read_messages(self, page: Page) -> List[Dict]:
        rows = await page.evaluate(
            """
            () => {
              const container = document.querySelector('[class*="DivMessageListContainer"], [class*="DivChatMessageList"], [data-e2e="dm-message-list"]');
              const nodes = Array.from(document.querySelectorAll('[data-e2e="dm-message"], [class*="DivMessageItem"], [class*="DivMessageBubble"], [class*="DivChatMessageItem"]'));
              const holder = container ? container.getBoundingClientRect() : { left: 0, width: window.innerWidth };
              const mid = holder.left + holder.width / 2;
              const out = [];
              for (const node of nodes) {
                const rect = node.getBoundingClientRect();
                if (rect.width < 10 || rect.height < 8) continue;
                const text = String(node.innerText || '').trim();
                if (!text) continue;
                out.push({ text: text, direction: rect.left + rect.width / 2 > mid ? 'outgoing' : 'incoming', top: rect.top });
              }
              out.sort((a, b) => a.top - b.top);
              return out.map((m) => ({ text: m.text, direction: m.direction, time_text: '' }));
            }
            """
        )
        return [row for row in (rows or []) if str(row.get("text", "")).strip()]

    async def extract_chat_page_conversation_detail(self, page: Page) -> Dict:
        header = {}
        try:
            header = await page.evaluate(
                """
                () => {
                  const head = document.querySelector('[class*="RightPanelHeader"], [class*="DivChatHeader"], [data-e2e="dm-conversation-header"]');
                  const link = head ? head.querySelector('a[href^="/@"]') : document.querySelector('a[href^="/@"]');
                  const avatar = head ? head.querySelector('img') : null;
                  return {
                    profile_url: link ? ('https://www.tiktok.com' + link.getAttribute('href')) : '',
                    username: link ? link.getAttribute('href').slice(2).split('/')[0] : '',
                    avatar_url: avatar ? (avatar.currentSrc || avatar.src || '') : ''
                  };
                }
                """
            )
        except Exception:
            header = {}
        messages = await self._read_messages(page)
        incoming = [m for m in messages if m.get("direction") == "incoming"]
        outgoing = [m for m in messages if m.get("direction") == "outgoing"]
        return {
            "username": str((header or {}).get("username", "") or ""),
            "avatar_url": str((header or {}).get("avatar_url", "") or ""),
            "profile_url": str((header or {}).get("profile_url", "") or ""),
            "incoming_message": str(incoming[-1].get("text", "") if incoming else ""),
            "reply_message": str(outgoing[-1].get("text", "") if outgoing else ""),
            "messages": messages,
        }

    async def collect_chat_page_conversation_detail(self, row: Dict, logger: Optional[Callable[[str, str], None]] = None) -> Dict:
        page = await self.open_chat_workspace_page(logger=logger)
        try:
            opened, reason = await self.open_chat_page_conversation(page, row or {})
            if not opened:
                raise RuntimeError(reason or "未找到目标会话")
            return await self.extract_chat_page_conversation_detail(page)
        finally:
            try:
                await page.close()
            except Exception:
                pass
    async def _collect_chat_rows(
        self,
        *,
        page: Optional[Page],
        max_conversations: int,
        should_stop: Optional[Callable[[], bool]],
        logger: Optional[Callable[[str, str], None]],
        progress_callback: Optional[Callable[[int, int, str], None]],
        item_callback: Optional[Callable[[Dict], Awaitable[None]]],
        include_details: bool,
        strangers_only: bool,
        source: str,
    ) -> List[Dict]:
        own_page = page is None
        work_page = page or await self.open_chat_workspace_page(logger=logger)
        rows: List[Dict] = []
        limit = max(1, int(max_conversations or 1))
        try:
            conversations = await self._list_conversations(work_page)
            total = min(len(conversations), limit)
            for index, conv in enumerate(conversations[:limit], start=1):
                if should_stop and should_stop():
                    break
                username = str(conv.get("username", "") or "")
                opened = await self._open_conversation_by_username(work_page, username)
                messages: List[Dict] = []
                if opened:
                    await work_page.wait_for_timeout(1200)
                    if include_details or not strangers_only:
                        messages = await self._read_messages(work_page)
                elif not strangers_only:
                    if progress_callback:
                        try:
                            progress_callback(index, total, username)
                        except Exception:
                            pass
                    continue
                incoming = [m for m in messages if m.get("direction") == "incoming"]
                outgoing = [m for m in messages if m.get("direction") == "outgoing"]
                if strangers_only:
                    if not incoming:
                        continue
                    if outgoing and messages and messages[-1].get("direction") == "outgoing":
                        continue
                row = {
                    "conversation_key": f"tiktok:{self.account_id or 0}:{username}",
                    "conversation_id": username,
                    "username": username,
                    "avatar_url": str(conv.get("avatar_url", "") or ""),
                    "profile_url": str(conv.get("profile_url", "") or ""),
                    "preview_text": str(incoming[-1].get("text", "") if incoming else (conv.get("preview_text", "") or "")),
                    "incoming_message": str(incoming[-1].get("text", "") if incoming else (conv.get("preview_text", "") or "")),
                    "last_message_text": str(messages[-1].get("text", "") if messages else (conv.get("preview_text", "") or "")),
                    "last_message_is_user": bool(messages and messages[-1].get("direction") == "incoming"),
                    "has_user_message": bool(incoming),
                    "time_text": str(conv.get("time_text", "") or ""),
                    "unread_count": 0,
                    "conversation_position": int(conv.get("index", index) or index),
                    "stranger_index": int(conv.get("index", index) or index),
                    "conversation_source": source,
                    "messages": messages,
                    "account_id": int(self.account_id or 0),
                    "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                rows.append(row)
                if item_callback:
                    try:
                        await item_callback(row)
                    except Exception as exc:
                        self._emit(logger, f"[TikTok私信] 单条回调失败：{exc}", "warning")
                if progress_callback:
                    try:
                        progress_callback(index, total, username)
                    except Exception:
                        pass
            return rows
        finally:
            if own_page:
                try:
                    await work_page.close()
                except Exception:
                    pass

    async def collect_chat_page_private_messages(
        self,
        max_conversations: int = 50,
        should_stop: Optional[Callable[[], bool]] = None,
        logger: Optional[Callable[[str, str], None]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        item_callback: Optional[Callable[[Dict], Awaitable[None]]] = None,
        page: Optional[Page] = None,
    ) -> List[Dict]:
        return await self._collect_chat_rows(
            page=page,
            max_conversations=max_conversations,
            should_stop=should_stop,
            logger=logger,
            progress_callback=progress_callback,
            item_callback=item_callback,
            include_details=True,
            strangers_only=False,
            source="chat_inbox",
        )

    async def collect_stranger_private_messages(
        self,
        max_conversations: int = 50,
        should_stop: Optional[Callable[[], bool]] = None,
        logger: Optional[Callable[[str, str], None]] = None,
        include_details: bool = False,
        item_callback: Optional[Callable[[Dict], Awaitable[None]]] = None,
        page: Optional[Page] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[Dict]:
        return await self._collect_chat_rows(
            page=page,
            max_conversations=max_conversations,
            should_stop=should_stop,
            logger=logger,
            progress_callback=progress_callback,
            item_callback=item_callback,
            include_details=include_details,
            strangers_only=True,
            source="stranger_messages",
        )
    async def _find_chat_input(self, page: Page):
        selectors = [
            '[data-e2e="dm-input"] div[contenteditable="true"]',
            'div[contenteditable="true"][role="textbox"]',
            'div[contenteditable="true"]',
            'textarea',
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector).last
                if await locator.count() == 0:
                    continue
                await locator.wait_for(state="visible", timeout=4000)
                return locator
            except Exception:
                continue
        return None

    async def send_open_chat_message(
        self,
        page: Page,
        message: str,
        logger: Optional[Callable[[str, str], None]] = None,
        username: str = "",
    ) -> Dict:
        parts = [
            str(part).strip()
            for part in str(message or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
            if str(part).strip()
        ]
        if not parts:
            raise ValueError("私信内容不能为空")
        input_box = await self._find_chat_input(page)
        if input_box is None:
            raise RuntimeError("当前会话没有找到私信输入框（会话可能还没打开）")
        sent: List[str] = []
        for index, part in enumerate(parts, start=1):
            await input_box.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await page.keyboard.type(part, delay=25)
            await page.wait_for_timeout(200)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(1200)
            delivered = False
            try:
                editor_text = await input_box.evaluate("(el) => String(el.textContent || '').trim()")
                delivered = not editor_text
            except Exception:
                delivered = False
            if not delivered:
                try:
                    delivered = await page.evaluate(
                        """
                        (text) => {
                          const needle = String(text || '').trim();
                          if (!needle) return false;
                          const nodes = Array.from(document.querySelectorAll('[data-e2e="dm-message"], [class*="DivMessageItem"], [class*="DivMessageBubble"]'));
                          return nodes.some((n) => String(n.innerText || '').indexOf(needle) >= 0);
                        }
                        """,
                        part,
                    )
                except Exception:
                    delivered = False
            if not delivered:
                raise RuntimeError(f"第 {index} 条消息已回车，但未确认发出")
            sent.append(part)
            self._emit(logger, f"[TikTok私信] 已发送第 {index}/{len(parts)} 条：{username or '-'}", "success")
            if index < len(parts):
                await page.wait_for_timeout(600)
        return {"success": True, "messages": sent, "message_count": len(sent)}
    async def send_private_message(
        self,
        profile_url: str,
        message: str,
        expected_username: str = "",
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Dict:
        profile_url = str(profile_url or "").strip()
        if not profile_url:
            raise ValueError("缺少用户主页地址")
        page = await self._new_page(logger=logger)
        try:
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2500)
            clicked = await page.evaluate(
                """
                () => {
                  const nodes = Array.from(document.querySelectorAll('button, [role="button"], a'));
                  const btn = nodes.find((n) => /^(message|\u53d1\u6d88\u606f|\u804a\u5929)$/i.test(String(n.innerText || '').trim()));
                  if (btn) { btn.click(); return true; }
                  const icon = document.querySelector('[data-e2e="message-button"], [data-e2e="dm-button"]');
                  if (icon) { icon.click(); return true; }
                  return false;
                }
                """
            )
            if not clicked:
                raise RuntimeError(f"{expected_username or profile_url} 主页没有可用的“发消息”入口")
            await page.wait_for_timeout(3500)
            result = await self.send_open_chat_message(page, message, logger=logger, username=expected_username)
            return {**result, "profile_url": profile_url, "username": expected_username}
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def send_stranger_private_message(
        self,
        row: Dict,
        message: str,
        logger: Optional[Callable[[str, str], None]] = None,
        page: Optional[Page] = None,
    ) -> Dict:
        own_page = page is None
        work_page = page or await self.open_chat_workspace_page(logger=logger)
        try:
            opened, reason = await self.open_chat_page_conversation(work_page, row or {})
            if not opened:
                raise RuntimeError(reason or "未找到目标会话")
            result = await self.send_open_chat_message(
                work_page,
                message,
                logger=logger,
                username=str((row or {}).get("username", "") or ""),
            )
            return {**result, "conversation_key": str((row or {}).get("conversation_key", "") or "")}
        finally:
            if own_page:
                try:
                    await work_page.close()
                except Exception:
                    pass
    # ------------------------------------------------------------------ 评论 @ 提及
    async def _pick_mention_candidate(self, page: Page, username: str, timeout_ms: int = 6000) -> str:
        deadline = time.monotonic() + max(1.0, timeout_ms / 1000.0)
        wanted = _normalize_text(username).lower()
        handle = wanted.split(" ")[0]
        while time.monotonic() < deadline:
            rows = await page.evaluate(
                """
                () => Array.from(document.querySelectorAll('[data-e2e="comment-at-list"]')).map((node, index) => {
                  const lines = String(node.innerText || '').split(String.fromCharCode(10)).map((s) => s.trim()).filter(Boolean);
                  return { lines: lines, index: index };
                })
                """
            )
            for row in rows or []:
                lines = [str(item).lower() for item in (row.get("lines") or [])]
                if not lines:
                    continue
                if lines[-1] == handle or handle in lines[-1] or (wanted and wanted in " ".join(lines)):
                    clicked = await page.evaluate(
                        """
                        (index) => {
                          const nodes = Array.from(document.querySelectorAll('[data-e2e="comment-at-list"]'));
                          const node = nodes[index];
                          if (!node) return false;
                          node.click();
                          return true;
                        }
                        """,
                        int(row.get("index", 0) or 0),
                    )
                    if clicked:
                        return str((row.get("lines") or [username])[-1] or username)
            await page.wait_for_timeout(400)
        raise RuntimeError(f"没有出现 @{username} 的候选")
    async def _find_comment_input(self, page: Page):
        selectors = [
            '[data-e2e="comment-input"] div[contenteditable="true"]',
            '[data-e2e="comment-input"]',
            'div[contenteditable="true"][role="textbox"]',
            'div[contenteditable="true"]',
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector).last
                if await locator.count() == 0:
                    continue
                await locator.wait_for(state="visible", timeout=4000)
                return locator
            except Exception:
                continue
        return None

    async def send_video_comment_mentions(
        self,
        video_url: str,
        mention_usernames: List[str],
        expected_title: str = "",
        max_mentions: int = 50,
        logger: Optional[Callable[[str, str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> Dict:
        video_url = str(video_url or "").strip()
        if not video_url:
            raise ValueError("缺少作品地址")
        names: List[str] = []
        seen = set()
        for raw in mention_usernames or []:
            name = _normalize_text(raw)
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            names.append(name)
            if len(names) >= max(1, min(int(max_mentions or 50), 50)):
                break
        if not names:
            raise ValueError("至少需要一个可用的精准客户昵称")

        page = await self._new_page(logger=logger)
        try:
            self._emit(logger, f"[TikTok评论@客户] 打开视频：{expected_title or video_url}")
            await page.goto(video_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3500)
            await self._open_comment_panel(page, logger=logger)
            await page.wait_for_timeout(1500)
            input_box = await self._find_comment_input(page)
            if input_box is None:
                raise RuntimeError("没有找到评论输入框")
            await input_box.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")

            selected: List[str] = []
            successful: List[Dict[str, str]] = []
            failed: List[Dict[str, str]] = []
            for username in names:
                if should_stop and should_stop():
                    break
                query = username.split(" ")[0] if " " in username else username
                try:
                    await input_box.click()
                    await page.keyboard.press("Control+A")
                    await page.keyboard.press("Backspace")
                    await page.keyboard.type(f"@{query}", delay=45)
                    label = await self._pick_mention_candidate(page, username, timeout_ms=7000)
                    await page.wait_for_timeout(400)
                    await page.keyboard.press("Space")
                    selected.append(label)
                    successful.append({"requested_username": username, "selected_label": label})
                    await page.wait_for_timeout(300)
                except Exception as exc:
                    failed.append({"requested_username": username, "error": str(exc)})
                    self._emit(logger, f"[TikTok评论@客户] 跳过 @{username}：{exc}", "warning")
                    try:
                        await page.keyboard.press("Escape")
                        await page.keyboard.press("Control+A")
                        await page.keyboard.press("Backspace")
                    except Exception:
                        pass

            if not selected:
                summary = "；".join(f"@{item['requested_username']}：{item['error']}" for item in failed[:5])
                raise RuntimeError(f"没有可发送的 @ 用户，失败明细：{summary or '全部候选失败'}")

            composed = " ".join(f"@{label}" for label in selected)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2500)
            try:
                remainder = await input_box.evaluate("(el) => String(el.textContent || '').trim()")
            except Exception:
                remainder = ""
            if remainder:
                raise RuntimeError("评论已回车，但输入框仍有内容，可能未提交成功")

            self._emit(logger, f"[TikTok评论@客户] 已发送，@ {len(selected)} 人", "success")
            return {
                "success": True,
                "video_url": video_url,
                "title": expected_title,
                "selected_mentions": selected,
                "successful_mentions": successful,
                "failed_mentions": failed,
                "comment_text": composed,
            }
        finally:
            try:
                await page.close()
            except Exception:
                pass
    # ------------------------------------------------------------------ 群聊 / 群成员
    async def _list_group_candidates(self, page: Page) -> List[Dict]:
        rows = await page.evaluate(
            """
            () => {
              const out = [];
              const seen = new Set();
              for (const node of document.querySelectorAll('[class*="DivChatItem"], [data-e2e="dm-conversation-item"], [class*="DivChatList"] > *')) {
                const rect = node.getBoundingClientRect();
                if (rect.width < 120 || rect.height < 30) continue;
                if (rect.left > window.innerWidth * 0.6) continue;
                const lines = String(node.innerText || '').split(String.fromCharCode(10)).map((s) => s.trim()).filter(Boolean);
                const name = lines[0] || '';
                if (!name) continue;
                const hasUserLink = !!node.querySelector('a[href^="/@"]');
                const text = lines.join(' ');
                const isGroup = !hasUserLink || /群|group/i.test(text) || node.querySelectorAll('img').length > 1;
                if (!isGroup) continue;
                const key = name.toLowerCase();
                if (seen.has(key)) continue;
                seen.add(key);
                out.push({ name: name, preview: lines[1] || '' });
              }
              return out;
            }
            """
        )
        return [
            {"name": str(row.get("name", "") or "").strip(), "preview": str(row.get("preview", "") or ""), "member_count": 0}
            for row in (rows or [])
            if str(row.get("name", "") or "").strip()
        ]

    async def list_chat_groups(
        self,
        group_keyword: str = "",
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> List[Dict]:
        page = await self.open_chat_workspace_page(logger=logger)
        try:
            groups = await self._list_group_candidates(page)
            keyword = _normalize_text(group_keyword).lower()
            if keyword:
                groups = [
                    g for g in groups
                    if keyword in g["name"].lower() or keyword in str(g.get("preview", "")).lower()
                ]
            return [
                {"name": g["name"], "key": g["name"], "member_count": int(g.get("member_count", 0) or 0)}
                for g in groups
            ]
        finally:
            try:
                await page.close()
            except Exception:
                pass
    async def collect_chat_group_members_v2(
        self,
        group_keyword: str = "",
        max_groups: int = 5,
        max_members_per_group: int = 50,
        selected_groups: Optional[List[str]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> List[Dict]:
        page = await self.open_chat_workspace_page(logger=logger)
        rows: List[Dict] = []
        seen = set()
        try:
            groups = await self._list_group_candidates(page)
            wanted = [str(item or "").strip() for item in (selected_groups or []) if str(item or "").strip()]
            if wanted:
                groups = [g for g in groups if g["name"] in wanted]
            elif group_keyword:
                keyword = _normalize_text(group_keyword).lower()
                groups = [g for g in groups if keyword in g["name"].lower()]
            groups = groups[: max(1, int(max_groups or 5))]

            for group in groups:
                if should_stop and should_stop():
                    break
                group_name = group["name"]
                opened = await self._open_conversation_by_username(page, group_name)
                if not opened:
                    continue
                await page.wait_for_timeout(1500)
                try:
                    await page.evaluate(
                        """
                        () => {
                          const head = document.querySelector('[class*="RightPanelHeader"], [class*="DivChatHeader"], [data-e2e="dm-conversation-header"]');
                          if (head) head.click();
                        }
                        """
                    )
                    await page.wait_for_timeout(2000)
                except Exception:
                    pass
                members = await page.evaluate(
                    """
                    () => {
                      const panel = document.querySelector('[role="dialog"], [class*="DivGroupInfo"], [class*="GroupMember"], [class*="DivMemberList"]') || document.body;
                      const out = [];
                      const seen = new Set();
                      for (const a of panel.querySelectorAll('a[href^="/@"]')) {
                        const href = a.getAttribute('href') || '';
                        const username = href.slice(2).split('/')[0].split('?')[0];
                        if (!username || seen.has(username)) continue;
                        seen.add(username);
                        const row = a.closest('[class*="Member"], [class*="Item"], li') || a;
                        const img = row.querySelector('img') || a.querySelector('img');
                        out.push({ username: username, profile_url: 'https://www.tiktok.com' + href, avatar_url: img ? (img.currentSrc || img.src || '') : '' });
                      }
                      return out;
                    }
                    """
                )
                for index, member in enumerate((members or [])[: max(1, int(max_members_per_group or 50))]):
                    username = str(member.get("username", "") or "").strip()
                    if not username:
                        continue
                    key = f"{group_name}|{username}"
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        {
                            "group_name": group_name,
                            "username": username,
                            "profile_url": str(member.get("profile_url", "") or ""),
                            "avatar_url": str(member.get("avatar_url", "") or ""),
                            "role": "",
                            "region": "",
                            "tiktok_id": "",
                            "sec_user_id": "",
                            "member_index": index,
                            "source": "tiktok_group",
                        }
                    )
            return rows
        finally:
            try:
                await page.close()
            except Exception:
                pass