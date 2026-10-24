"""TikTok 版评论区/搜索采集器（工作台引擎）。

与抖音版保持同一套方法名和返回结构，内部使用 TikTok 页面 DOM。
未接入的能力（私信/群/提及等）会抛出明确异常，不做假数据。
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
                        const m = href.match(/@([^/]+)\/video\/(\d+)/);
                        if (!m) continue;
                        const img = a.querySelector('img');
                        let title = (img && (img.alt || '')) || '';
                        if (!title) title = (a.innerText || '').trim().split('\n')[0] || '';
                        const cover = img ? (img.currentSrc || img.src || '') : '';
                        const text = (a.innerText || '').trim();
                        out.push({url: href, author: m[1], aweme_id: m[2], title: title.slice(0, 300), cover_image: cover, text: text.slice(0, 400)});
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

    async def send_video_comment_mentions(self, *args, **kwargs):
        raise TikTokPrivateMessageUnavailable(_UNAVAILABLE_MESSAGE)

    async def list_chat_groups(self, *args, **kwargs):
        raise TikTokPrivateMessageUnavailable(_UNAVAILABLE_MESSAGE)

    async def collect_stranger_private_messages(self, *args, **kwargs):
        raise TikTokPrivateMessageUnavailable(_UNAVAILABLE_MESSAGE)

    async def collect_chat_page_private_messages(self, *args, **kwargs):
        raise TikTokPrivateMessageUnavailable(_UNAVAILABLE_MESSAGE)

    async def collect_chat_page_conversation_detail(self, *args, **kwargs):
        raise TikTokPrivateMessageUnavailable(_UNAVAILABLE_MESSAGE)

    async def extract_chat_page_conversation_detail(self, *args, **kwargs):
        raise TikTokPrivateMessageUnavailable(_UNAVAILABLE_MESSAGE)

    async def open_chat_workspace_page(self, *args, **kwargs):
        raise TikTokPrivateMessageUnavailable(_UNAVAILABLE_MESSAGE)

    async def open_chat_page_conversation(self, *args, **kwargs):
        raise TikTokPrivateMessageUnavailable(_UNAVAILABLE_MESSAGE)

    async def send_open_chat_message(self, *args, **kwargs):
        raise TikTokPrivateMessageUnavailable(_UNAVAILABLE_MESSAGE)

    async def send_private_message(self, *args, **kwargs):
        raise TikTokPrivateMessageUnavailable(_UNAVAILABLE_MESSAGE)

    async def send_stranger_private_message(self, *args, **kwargs):
        raise TikTokPrivateMessageUnavailable(_UNAVAILABLE_MESSAGE)

    async def collect_chat_group_members_v2(self, *args, **kwargs):
        raise TikTokPrivateMessageUnavailable(_UNAVAILABLE_MESSAGE)