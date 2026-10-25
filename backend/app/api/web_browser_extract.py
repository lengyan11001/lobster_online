"""Device-side browser page extraction (fallback for server-side fetch)."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()

_PROFILE_DIR = Path(__file__).resolve().parents[3] / "browser_data" / "web_extract"
_ASSIST_PROFILE_DIR = Path(__file__).resolve().parents[3] / "browser_data" / "web_extract_assist"
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
    click_texts: List[str] = Field(default_factory=list, max_length=5)
    assist: bool = False
    assist_timeout_seconds: int = Field(180, ge=10, le=600)


def _normalize_text(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in str(value or "").replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line)


_GATE_JS = """
() => {
  const wanted = ['i am 18 or older', 'yes, i am 18', 'enter', 'agree', 'accept', 'continue', '同意', '进入', '我已满18岁', '确认'];
  const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
  for (const n of nodes) {
    const text = String(n.innerText || '').trim().toLowerCase();
    if (!text) continue;
    if (wanted.some((word) => text === word || text.includes(word))) {
      n.click();
      return text;
    }
  }
  return '';
}
"""


_CLICK_JS = """
(label) => {
  const wanted = String(label || '').trim().toLowerCase().replace(/\s+/g, ' ');
  if (!wanted) return false;
  const nodes = Array.from(document.querySelectorAll('a, button, [role="button"], [role="menuitem"]'));
  let exact = null;
  let best = null;
  let bestLen = 1e9;
  for (const node of nodes) {
    const text = String(node.innerText || '').trim().toLowerCase().replace(/\s+/g, ' ');
    if (!text || text.length > 60) continue;
    if (text === wanted) { exact = node; break; }
    if (text.includes(wanted) && text.length < bestLen) { best = node; bestLen = text.length; }
  }
  const target = exact || best;
  if (!target) return false;
  try { target.scrollIntoView({ block: 'center' }); } catch (e) {}
  target.click();
  return true;
}
"""


_LINK_JS = """
(label) => {
  const wanted = String(label || '').trim().toLowerCase().replace(/\s+/g, ' ');
  if (!wanted) return '';
  const nodes = Array.from(document.querySelectorAll('a[href]'));
  let best = '';
  let bestLen = 1e9;
  for (const node of nodes) {
    const raw = String(node.getAttribute('href') || '').trim();
    if (!raw || raw.startsWith('#') || raw.toLowerCase().startsWith('javascript:')) continue;
    const text = String(node.innerText || '').trim().toLowerCase().replace(/\s+/g, ' ');
    if (!text || text.length > 60) continue;
    if (text === wanted) return node.href;
    if (text.includes(wanted) && text.length < bestLen) { best = node.href; bestLen = text.length; }
  }
  return best;
}
"""


async def _dismiss_gates(page) -> None:
    for _ in range(3):
        try:
            clicked = await page.evaluate(_GATE_JS)
        except Exception:
            return
        if not clicked:
            return
        await page.wait_for_timeout(1500)


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(
    r"(?:400[- ]?\d{3}[- ]?\d{4}"
    r"|0\d{2,3}[- ]?\d{7,8}(?:[-转]\d{1,6})?"
    r"|1[3-9]\d{9}"
    r"|\+\d{1,3}[- ]\d{2,4}[- ]\d{3,8}"
    r"|\(\d{2,4}\)\s?\d{3,8})"
)
# 通用规则：只按多语言的“联系/咨询”语义和职能词归类，不写死任何站点或客户。
_CONTACT_PRIMARY = (
    "联系", "contact", "contacto", "kontakt", "お問い合わせ", "문의",
    "get in touch", "enquiry", "inquiry", "询价", "咨询",
)
_CONTACT_HINTS = _CONTACT_PRIMARY + ("about us", "aboutus", "关于我们", "关于", "sales", "销售", "客服", "support", "help")


def _contact_lines(text: str, links, pages) -> List[str]:
    blob_parts = [str(text or "")]
    for page in pages or []:
        if isinstance(page, dict) and page.get("status") == "ok":
            blob_parts.append(str(page.get("text") or ""))
    blob = "\n".join(blob_parts)
    found: List[str] = []
    for email in dict.fromkeys(_EMAIL_RE.findall(blob)):
        found.append(f"- 邮箱：{email}")
    for phone in dict.fromkeys(_PHONE_RE.findall(blob)):
        found.append(f"- 电话：{phone}")
    candidates = []
    for link in links or []:
        if not isinstance(link, dict):
            continue
        label = str(link.get("text") or "")
        url = str(link.get("url") or "")
        low = (label + " " + url).lower()
        if not any(hint in low for hint in _CONTACT_HINTS):
            continue
        tier = 0 if any(hint in low for hint in _CONTACT_PRIMARY) else 1
        candidates.append((tier, f"- 联系入口：{label or url} {url}"))
    seen = set()
    for _tier, line in sorted(candidates, key=lambda item: item[0]):
        if line in seen:
            continue
        seen.add(line)
        found.append(line)
    return found[:8]


def _build_digest(title: str, description: str, site: str, text: str, links, pages=None) -> str:
    lines = [f"标题：{title}", f"站点：{site}"]
    if description:
        lines.append(f"描述：{description}")
    contact = _contact_lines(text, links, pages)
    if contact:
        # 通用规则：联系方式放在摘要最前面，正文再长也不会把它挤掉。
        lines.append("")
        lines.append("联系方式线索：")
        lines.extend(contact)
    lines.append("")
    lines.append("正文节选：")
    lines.append(text[:6000])
    items = links if isinstance(links, list) else []
    if items:
        def _link_rank(link) -> int:
            low = (str(link.get("text") or "") + " " + str(link.get("url") or "")).lower()
            if any(hint in low for hint in _CONTACT_PRIMARY):
                return 0
            if any(hint in low for hint in _CONTACT_HINTS):
                return 1
            return 2

        ranked = sorted(items, key=_link_rank)
        lines.append("")
        lines.append("主要链接：")
        for link in ranked[:20]:
            label = str(link.get("text") or "").strip() or str(link.get("url") or "")
            lines.append(f"- {label[:60]} {link.get('url', '')}")
    opened = pages if isinstance(pages, list) else []
    if opened:
        lines.append("")
        lines.append("已点开的页面：")
        for page in opened:
            if not isinstance(page, dict) or page.get("status") != "ok":
                continue
            lines.append(f"--- {page.get('label', '')} | {page.get('title', '')} | {page.get('url', '')}")
            lines.append(str(page.get("text", ""))[:3000])
    return chr(10).join(lines)


_ASSIST: Dict[str, Any] = {"playwright": None, "context": None, "page": None, "last_used": 0.0, "reaper": None}
_ASSIST_LOCK = asyncio.Lock()
_ASSIST_IDLE_SECONDS = 300


def _looks_like_gate(text: str) -> bool:
    """True when the page still shows a login / age / captcha wall."""
    sample = str(text or "").strip().lower()
    if len(sample) >= 600:
        return False
    markers = (
        "log in", "sign in", "sign up", "登录", "注册", "密码", "验证码", "password", "captcha",
        "18+", "adults only", "are you 18", "confirm you are 18", "age verification", "年龄确认", "是否满18",
    )
    return any(marker in sample for marker in markers)


_looks_like_login = _looks_like_gate


async def _close_assist() -> None:
    context = _ASSIST.get("context")
    playwright = _ASSIST.get("playwright")
    _ASSIST.update({"playwright": None, "context": None, "page": None, "last_used": 0.0})
    try:
        if context is not None:
            await context.close()
    except Exception:
        pass
    try:
        if playwright is not None:
            await playwright.stop()
    except Exception:
        pass


async def _assist_reaper() -> None:
    while True:
        await asyncio.sleep(30)
        if _ASSIST_LOCK.locked():
            continue
        last = float(_ASSIST.get("last_used") or 0.0)
        if not last or time.monotonic() - last < _ASSIST_IDLE_SECONDS:
            if _ASSIST.get("context") is None:
                return
            continue
        logger.info("[web-browser-extract] assist browser idle, closing")
        await _close_assist()
        return


def _ensure_reaper() -> None:
    task = _ASSIST.get("reaper")
    if task is not None and not task.done():
        return
    try:
        _ASSIST["reaper"] = asyncio.create_task(_assist_reaper())
    except RuntimeError:
        _ASSIST["reaper"] = None


async def _ensure_assist_page():
    from playwright.async_api import async_playwright

    context = _ASSIST.get("context")
    page = _ASSIST.get("page")
    if context is not None and page is not None:
        try:
            if not page.is_closed():
                return page
        except Exception:
            pass
    _ASSIST_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    playwright = await async_playwright().start()
    try:
        context = await playwright.chromium.launch_persistent_context(
            str(_ASSIST_PROFILE_DIR),
            headless=False,
            channel="chrome",
            args=["--no-first-run", "--no-default-browser-check"],
        )
    except Exception:
        context = await playwright.chromium.launch_persistent_context(
            str(_ASSIST_PROFILE_DIR),
            headless=False,
            args=["--no-first-run", "--no-default-browser-check"],
        )
    page = context.pages[0] if context.pages else await context.new_page()
    _ASSIST.update({"playwright": playwright, "context": context, "page": page})
    return page


async def _collect_clicked_pages(page, labels: List[str], base_url: str) -> List[Dict[str, Any]]:
    """Open nav entries (contacts / pricing / characters) and keep a short digest of each."""
    out: List[Dict[str, Any]] = []
    context = page.context
    for label in labels:
        before = set(context.pages)
        current = ""
        try:
            current = str(page.url or "")
        except Exception:
            current = ""
        href = ""
        if current.rstrip("/") != base_url.rstrip("/"):
            try:
                await page.goto(base_url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(1200)
            except Exception:
                pass
        try:
            href = str(await page.evaluate(_LINK_JS, label) or "")
        except Exception:
            href = ""
        target = page
        opened_new_tab = False
        navigated = False
        if href and href.rstrip("/") != current.rstrip("/"):
            try:
                await page.goto(href, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(2000)
                navigated = True
            except Exception:
                navigated = False
        if not navigated:
            try:
                clicked = await page.evaluate(_CLICK_JS, label)
            except Exception as exc:
                out.append({"label": label, "status": "error", "error": str(exc)[:200]})
                continue
            if not clicked:
                out.append({"label": label, "status": "not_found"})
                continue
            await page.wait_for_timeout(2500)
            extra = [item for item in context.pages if item not in before]
            if extra:
                target = extra[-1]
                opened_new_tab = True
                try:
                    await target.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                await target.wait_for_timeout(1200)
        try:
            raw_page = await target.evaluate(_EXTRACT_JS)
            page_text = _normalize_text(raw_page.get("text", ""))
            out.append({
                "label": label,
                "status": "ok",
                "url": str(raw_page.get("url") or ""),
                "title": str(raw_page.get("title") or "")[:200],
                "text": page_text[:4000],
                "links": raw_page.get("links", [])[:30],
            })
        except Exception as exc:
            out.append({"label": label, "status": "error", "error": str(exc)[:200]})
        finally:
            if opened_new_tab:
                try:
                    await target.close()
                except Exception:
                    pass
            try:
                if str(page.url or "").rstrip("/") != base_url.rstrip("/"):
                    await page.goto(base_url, wait_until="domcontentloaded", timeout=45000)
                    await page.wait_for_timeout(1200)
            except Exception:
                pass
    return out


async def _extract_assisted(body: BrowserExtractBody) -> Dict[str, Any]:
    async with _ASSIST_LOCK:
        _ensure_reaper()
        page = await _ensure_assist_page()
        _ASSIST["last_used"] = time.monotonic()
        current = ""
        try:
            current = str(page.url or "")
        except Exception:
            current = ""
        if current.rstrip("/") != body.url.rstrip("/"):
            await page.goto(body.url, wait_until="domcontentloaded", timeout=60000)
        if body.wait_ms:
            await page.wait_for_timeout(body.wait_ms)
        await _dismiss_gates(page)

        deadline = time.monotonic() + max(10, int(body.assist_timeout_seconds))
        waited = 0
        while True:
            try:
                text = await page.evaluate("() => document.body ? String(document.body.innerText || '') : ''")
            except Exception:
                text = ""
            if len(text) >= 400 and not _looks_like_gate(text):
                break
            if time.monotonic() >= deadline:
                break
            await _dismiss_gates(page)
            await page.wait_for_timeout(2000)
            waited += 2
            _ASSIST["last_used"] = time.monotonic()

        for _ in range(max(0, body.scroll_rounds)):
            try:
                await page.evaluate("() => window.scrollBy(0, Math.round(window.innerHeight * 0.85))")
            except Exception:
                pass
            await page.wait_for_timeout(800)
        raw = await page.evaluate(_EXTRACT_JS)
        text_value = _normalize_text(raw.get("text", ""))[: body.max_chars]
        final_url = str(raw.get("url") or body.url)
        title = str(raw.get("title", ""))[:300]
        description = str(raw.get("description", ""))[:600]
        site = urlparse(final_url).netloc
        links = raw.get("links", [])[:300]

        labels = [str(item).strip() for item in (body.click_texts or []) if str(item).strip()][:5]
        pages = await _collect_clicked_pages(page, labels, final_url) if labels else []

        needs_user = bool(_looks_like_gate(text_value)) or len(text_value) < 200
        _ASSIST["last_used"] = time.monotonic()
        digest = _build_digest(title, description, site, text_value, links, pages)
        return {
            "url": body.url,
            "final_url": final_url,
            "site": site,
            "status": "needs_user" if needs_user else "ok",
            "title": title,
            "description": description,
            "text": text_value,
            "word_count": len(re.findall(r"\S+", text_value)),
            "links": links,
            "images": raw.get("images", [])[:100],
            "pages": pages,
            "digest": digest,
            "assisted": True,
            "waited_seconds": waited,
            "user_hint": (
                "浏览器已拉起，请在里面完成登录或年龄确认；完成后会自动继续。"
                if needs_user
                else "已自动继续完成。"
            ),
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }


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
                await _dismiss_gates(page)
                await page.wait_for_timeout(1200)
                for _ in range(max(0, body.scroll_rounds)):
                    try:
                        await page.evaluate("() => window.scrollBy(0, Math.round(window.innerHeight * 0.85))")
                    except Exception:
                        pass
                    await page.wait_for_timeout(800)
                raw = await page.evaluate(_EXTRACT_JS)
                text = _normalize_text(raw.get("text", ""))[: body.max_chars]
                final_url = str(raw.get("url") or body.url)
                labels = [str(item).strip() for item in (body.click_texts or []) if str(item).strip()][:5]
                pages: List[Dict[str, Any]] = []
                if labels:
                    pages = await _collect_clicked_pages(page, labels, final_url)
                status = "ok"
                if len(text) < 120:
                    low = final_url.lower()
                    if any(marker in low for marker in ("login", "signin", "passport", "captcha", "challenge")):
                        status = "needs_login"
                    else:
                        status = "low_content"
                title = str(raw.get("title", ""))[:300]
                description = str(raw.get("description", ""))[:600]
                site = urlparse(final_url).netloc
                links = raw.get("links", [])[:300]
                digest = _build_digest(title, description, site, text, links, pages)
                return {
                    "url": body.url,
                    "final_url": final_url,
                    "site": site,
                    "status": status,
                    "title": title,
                    "description": description,
                    "text": text,
                    "word_count": len(re.findall(r"\S+", text)),
                    "links": links,
                    "images": raw.get("images", [])[:100],
                    "pages": pages,
                    "digest": digest,
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
        data = await (_extract_assisted(body) if body.assist else _extract(body))
    except Exception as exc:
        logger.warning("[web-browser-extract] failed url=%s err=%s", body.url, exc)
        return {"ok": False, "code": "extract_failed", "error": str(exc)}
    return {"ok": True, "data": data}