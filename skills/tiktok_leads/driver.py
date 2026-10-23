"""TikTok 浏览器获客驱动：复用已登录的 CDP TikTok profile。

只做公开页面：搜索视频、打开视频评论、按意向词筛客户；不发送私信、不关注、不点赞。
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List
from urllib.parse import quote

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.tiktok.com/search/video?q={q}"

_DEFAULT_INTENT_TERMS = [
    "price", "cost", "quote", "quotation", "how much", "moq", "minimum order",
    "sample", "order", "purchase", "wholesale", "bulk", "custom", "customize",
    "oem", "odm", "supplier", "contact", "whatsapp", "email", "where to buy",
    "价格", "多少钱", "报价", "费用", "起订量", "最小起订", "交期", "样品",
    "下单", "采购", "批发", "定制", "联系", "合作", "询价", "怎么买",
]


def _clean(value: Any, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def _intent_terms(prompt: str) -> List[str]:
    terms = list(_DEFAULT_INTENT_TERMS)
    # 用户补充的意向词按逗号/换行/分号切分，保留 "where to buy" 这类短语。
    for word in re.split(r"[,，\n;；|]+", str(prompt or "")):
        word = word.strip().lower()
        if len(word) >= 2 and word not in terms:
            terms.append(word)
    return terms


class TikTokLeadsDriver:
    async def search_videos(self, page: Any, keyword: str, limit: int = 20) -> List[Dict[str, Any]]:
        keyword = _clean(keyword, 120)
        if not keyword:
            return []
        url = SEARCH_URL.format(q=quote(keyword))
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(4)
        for _ in range(2):
            try:
                await page.mouse.wheel(0, 1600)
                await asyncio.sleep(1.2)
            except Exception:
                break
        rows = await page.evaluate(
            """() => {
              const out = [];
              const seen = new Set();
              for (const a of document.querySelectorAll('a[href*="/video/"]')) {
                const href = a.href || '';
                if (!href || seen.has(href)) continue;
                const m = href.match(/@([^/]+)\/video\/(\d+)/);
                if (!m) continue;
                seen.add(href);
                const card = a.closest('[data-e2e="search-card"], [class*="DivItemContainer"], [class*="search-card"]') || a.parentElement;
                const text = (card ? (card.innerText || '') : (a.innerText || '')).trim();
                out.push({ href, handle: m[1], video_id: m[2], card_text: text.slice(0, 500) });
              }
              return out;
            }"""
        )
        videos: List[Dict[str, Any]] = []
        for row in rows or []:
            text = _clean(row.get("card_text"), 500)
            lines = [x.strip() for x in text.splitlines() if x.strip() and x.strip() not in ("获赞最多", "推荐", "广告")]
            title = lines[0] if lines else ""
            videos.append({
                "video_id": str(row.get("video_id") or ""),
                "url": str(row.get("href") or ""),
                "author_handle": str(row.get("handle") or ""),
                "title": title[:300],
                "card_text": text,
                "keyword": keyword,
            })
            if len(videos) >= max(1, min(int(limit or 20), 50)):
                break
        logger.info("[TIKTOK-LEADS] search keyword=%s videos=%s", keyword, len(videos))
        return videos

    async def collect_comments(self, page: Any, video_url: str, max_comments: int = 100) -> List[Dict[str, Any]]:
        video_url = _clean(video_url, 1000)
        if not video_url:
            return []
        await page.goto(video_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(4)
        # 视频页右侧默认可能是“猜你喜欢”，必须切到“评论”标签才会加载评论列表。
        try:
            tab = page.get_by_text("评论", exact=True).last
            if await tab.count():
                await tab.click(timeout=5000)
            else:
                icon = page.locator('[data-e2e="comment-icon"]').last
                if await icon.count():
                    await icon.click(timeout=5000)
            await asyncio.sleep(2.0)
        except Exception:
            pass
        try:
            await page.wait_for_selector('[data-e2e="comment-level-1"]', timeout=30000)
        except Exception:
            logger.info("[TIKTOK-LEADS] no comments selector video=%s", video_url[:120])
            return []
        max_comments = max(1, min(int(max_comments or 100), 500))
        collected: Dict[str, Dict[str, Any]] = {}
        for _ in range(max(3, min(20, max_comments // 10 + 3))):
            rows = await page.evaluate(
                """() => {
                  const out = [];
                  for (const span of document.querySelectorAll('[data-e2e="comment-level-1"]')) {
                    const item = span.closest('[class*="DivCommentItemWrapper"], [class*="DivCommentObjectWrapper"]') || span.parentElement;
                    const userEl = item ? item.querySelector('[data-e2e="comment-username-1"]') : null;
                    const text = (span.innerText || '').trim();
                    const displayName = userEl ? (userEl.innerText || '').trim() : '';
                    let handle = '';
                    try {
                      const link = userEl ? (userEl.querySelector('a[href*="/@"]') || userEl.closest('a[href*="/@"]')) : null;
                      if (link) handle = (link.getAttribute('href') || '').replace(/^\/@/, '').split(/[?#]/)[0];
                    } catch (e) {}
                    if (!text) continue;
                    out.push({ username: displayName, handle, text, raw: item ? (item.innerText || '').slice(0, 500) : text });
                  }
                  return out;
                }"""
            )
            for row in rows or []:
                key = (str(row.get("username") or "") + "\n" + str(row.get("text") or "")).strip()
                if not key or key in collected:
                    continue
                display_name = _clean(row.get("username"), 120)
                handle = _clean(row.get("handle"), 120) or display_name
                collected[key] = {
                    "username": display_name,
                    "handle": handle,
                    "text": _clean(row.get("text"), 1000),
                    "raw": _clean(row.get("raw"), 1000),
                    "profile_url": ("https://www.tiktok.com/@" + handle) if handle else "",
                    "video_url": video_url,
                }
                if len(collected) >= max_comments:
                    break
            if len(collected) >= max_comments:
                break
            try:
                await page.evaluate(
                    """() => {
                      const el = document.querySelector('[class*="DivCommentListContainer"]');
                      if (el) el.scrollTop = el.scrollHeight;
                    }"""
                )
            except Exception:
                pass
            await asyncio.sleep(1.0)
        logger.info("[TIKTOK-LEADS] comments video=%s count=%s", video_url[:120], len(collected))
        return list(collected.values())[:max_comments]

    def score_leads(self, comments: List[Dict[str, Any]], intent_prompt: str = "", limit: int = 100) -> List[Dict[str, Any]]:
        terms = _intent_terms(intent_prompt)
        leads: List[Dict[str, Any]] = []
        seen = set()
        for item in comments or []:
            text = str(item.get("text") or "")
            low = text.lower()
            matched = [t for t in terms if t and t in low]
            if not matched:
                continue
            username = str(item.get("handle") or item.get("username") or "").strip()
            key = username or text[:120]
            if key in seen:
                continue
            seen.add(key)
            score = 60 + min(40, len(matched) * 8)
            leads.append({
                "handle": username,
                "display_name": item.get("username") or username,
                "profile_url": item.get("profile_url") or (("https://www.tiktok.com/@" + username) if username else ""),
                "comment_text": text,
                "video_url": item.get("video_url") or "",
                "score": score,
                "matched_terms": matched,
                "source": "tiktok_browser",
            })
        leads.sort(key=lambda x: int(x.get("score") or 0), reverse=True)
        return leads[:max(1, min(int(limit or 100), 500))]
