"""Device-side browser page extraction (fallback for server-side fetch)."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()

_PROFILE_DIR = Path(__file__).resolve().parents[3] / "browser_data" / "web_extract"
_LOCK = asyncio.Lock()
_EXTRACT_JS = """
() => {
  const meta = {};
  document.querySelectorAll('meta').forEach((node) => {
    const key = (node.getAttribute('property') || node.getAttribute('name') || '').toLowerCase();
    const value = node.getAttribute('content') || '';
    if (key && value && !meta[key]) meta[key] = value;
  });
  const links = Array.from(document.querySelectorAll('a[href]')).slice(0, 300).map((node) => ({
    url: node.href,
    text: (node.innerText || '').trim().slice(0, 120)
  }));
  const images = Array.from(document.querySelectorAll('img[src]')).slice(0, 100).map((node) => ({
    url: node.src,
    alt: (node.alt || '').slice(0, 160)
  }));
  const text = document.body ? String(document.body.innerText || '') : '';
  return {
    title: document.title || '',
    description: meta['description'] || meta['og:description'] || '',
    text: text,
    links: links,
    images: images,
    url: location.href
  };
}
"""


class BrowserExtractBody(BaseModel):
    url: str = Field(..., min_length=4, max_length=2048)
    wait_ms: int = Field(3000, ge=0, le=30000)
    scroll_rounds: int = Field(2, ge=0, le=10)
    max_chars: int = Field(30000, ge=1000, le=60000)


def _normalize_text(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in str(value or "").replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line)


async def _extract(body: BrowserExtractBody) -> Dict[str, Any]:
    from playwright.async_api import async_playwright

    async with _LOCK:
        _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as pw:
            try:
                context = await pw.chromium.launch_persistent_context(
                    str(_PROFILE_DIR),
                    headless=True,
                    channel="chrome",
                    args=["--no-first-run", "--no-default-browser-check"],
                )
            except Exception:
                context = await pw.chromium.launch_persistent_context(
                    str(_PROFILE_DIR),
                    headless=True,
                    args=["--no-first-run", "--no-default-browser-check"],
                )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(body.url, wait_until="domcontentloaded", timeout=45000)
                if body.wait_ms:
                    await page.wait_for_timeout(body.wait_ms)
                for _ in range(max(0, body.scroll_rounds)):
                    try:
                        await page.evaluate("() => window.scrollBy(0, Math.round(window.innerHeight * 0.85))")
                    except Exception:
                        pass
                    await page.wait_for_timeout(800)
                raw = await page.evaluate(_EXTRACT_JS)
                text = _normalize_text(raw.get("text", ""))[: body.max_chars]
                final_url = str(raw.get("url") or body.url)
                status = "ok"
                if len(text) < 120:
                    low = final_url.lower()
                    if any(marker in low for marker in ("login", "signin", "passport", "captcha", "challenge")):
                        status = "needs_login"
                    else:
                        status = "low_content"
                return {
                    "url": body.url,
                    "final_url": final_url,
                    "site": urlparse(final_url).netloc,
                    "status": status,
                    "title": str(raw.get("title", ""))[:300],
                    "description": str(raw.get("description", ""))[:600],
                    "text": text,
                    "word_count": len(re.findall(r"\S+", text)),
                    "links": raw.get("links", [])[:300],
                    "images": raw.get("images", [])[:100],
                    "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            finally:
                try:
                    await context.close()
                except Exception:
                    pass


@router.post("/api/web/browser-extract", summary="用本机浏览器打开网址并提取内容")
async def web_browser_extract(body: BrowserExtractBody) -> Dict[str, Any]:
    parsed = urlparse(body.url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"ok": False, "code": "invalid_url", "error": "只支持 http/https 网址"}
    try:
        data = await _extract(body)
    except Exception as exc:
        logger.warning("[web-browser-extract] failed url=%s err=%s", body.url, exc)
        return {"ok": False, "code": "extract_failed", "error": str(exc)}
    return {"ok": True, "data": data}