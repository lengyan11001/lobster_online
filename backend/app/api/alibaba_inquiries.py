"""Alibaba International inquiry takeover workbench."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import random
import re
from datetime import datetime
from html import unescape as html_unescape
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .auth import _ServerUser, get_current_user_for_local
from ..core.config import settings
from ..db import SessionLocal, get_db
from ..models import (
    AlibabaCustomerArchive,
    AlibabaCustomerArchiveEvidence,
    AlibabaCustomerArchiveJob,
    AlibabaCustomerProfile,
    AlibabaInquiry,
    AlibabaInquiryAccount,
    AlibabaInquiryMessage,
    AlibabaInquiryPhraseSummary,
    AlibabaInquiryTrainingDoc,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
_BROWSER_DATA_DIR = _BASE_DIR / "browser_data"
_DATA_DIR = _BASE_DIR / "data" / "alibaba_inquiries"
_UPLOAD_DIR = _DATA_DIR / "uploads"
_BROWSER_STATE_DIR = _DATA_DIR / "browser_states"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
_BROWSER_STATE_DIR.mkdir(parents=True, exist_ok=True)

ALIBABA_INQUIRY_LIST_URL = (
    "https://message.alibaba.com/message/default.htm"
    "?spm=a2700.7756200.0.0.4f8b71d229DykT#feedback/all"
)
ALIBABA_INQUIRY_CREATE_URL = "https://message.alibaba.com/message/default.htm#feedback/all"
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 80
_account_sync_locks: Dict[int, asyncio.Lock] = {}
_account_reply_locks: Dict[int, asyncio.Lock] = {}
_archive_job_tasks: Dict[int, asyncio.Task] = {}
_locks_guard = asyncio.Lock()


class CreateAccountBody(BaseModel):
    nickname: str = Field(default="阿里国际站账号", max_length=128)


class SyncBody(BaseModel):
    max_scrolls: int = Field(default=180, ge=1, le=1000)
    max_pages: int = Field(default=200, ge=1, le=200)
    stop_after_idle_rounds: int = Field(default=8, ge=2, le=30)
    sync_details: bool = True
    detail_limit: int = Field(default=0, ge=0, le=2000)


class AnalyzeBody(BaseModel):
    doc_ids: List[int] = Field(default_factory=list)


class AutoReplyConfigBody(BaseModel):
    enabled: bool = True


class ArchiveEnrichBody(BaseModel):
    force: bool = False
    max_results: int = Field(default=8, ge=0, le=20)


class ArchiveUpdateBody(BaseModel):
    display_name: Optional[str] = None
    status: Optional[str] = None
    grade: Optional[str] = None
    score: Optional[int] = Field(default=None, ge=0, le=100)
    basics: Dict[str, Any] = Field(default_factory=dict)
    pending_review: Dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class ReplyDraftBody(BaseModel):
    inquiry_id: str
    instruction: str = ""


class ReplySendBody(BaseModel):
    inquiry_id: str
    content: str
    dry_run: bool = False


def _now() -> datetime:
    return datetime.utcnow()


def _llm_request_context(request: Request) -> Any:
    headers = {
        "Authorization": request.headers.get("Authorization", ""),
        "X-Installation-Id": request.headers.get("X-Installation-Id", ""),
    }
    return SimpleNamespace(headers=headers)


def _dt(value: Any) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _safe_name(value: str, fallback: str = "account") -> str:
    text = re.sub(r"[^\w\-.一-龥]+", "_", str(value or "").strip(), flags=re.UNICODE).strip("._")
    return (text or fallback)[:80]


def _default_account_profile(acct: AlibabaInquiryAccount) -> Path:
    return _BROWSER_DATA_DIR / f"alibaba_inquiry_{acct.user_id}_{acct.id}_{_safe_name(acct.nickname)}"


def _resolve_account_profile(acct: AlibabaInquiryAccount) -> str:
    profile = str(acct.browser_profile or "").strip()
    if not profile:
        profile = str(_default_account_profile(acct))
        acct.browser_profile = profile
    Path(profile).mkdir(parents=True, exist_ok=True)
    return profile


def _account_storage_state_path(acct: AlibabaInquiryAccount) -> Path:
    digest = hashlib.sha1(f"{acct.user_id}:{acct.id}".encode("utf-8")).hexdigest()[:12]
    return _BROWSER_STATE_DIR / f"account_{acct.user_id}_{acct.id}_{digest}.json"


async def _restore_account_browser_state(acct: AlibabaInquiryAccount, page: Any) -> bool:
    path = _account_storage_state_path(acct)
    if not path.is_file():
        return False
    ctx = getattr(page, "context", None)
    if ctx is None:
        return False
    restored: set = getattr(ctx, "_lobster_alibaba_restored_states", set())
    key = str(path)
    if key in restored:
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[ALIBABA-INQUIRY] read browser state failed path=%s err=%s", path, exc)
        return False
    cookies = data.get("cookies") if isinstance(data, dict) else []
    origins = data.get("origins") if isinstance(data, dict) else []
    if isinstance(cookies, list) and cookies:
        try:
            await ctx.add_cookies(cookies)
        except Exception as exc:
            logger.warning("[ALIBABA-INQUIRY] restore cookies failed path=%s err=%s", path, exc)
    local_storage_by_origin: Dict[str, Dict[str, str]] = {}
    if isinstance(origins, list):
        for item in origins:
            if not isinstance(item, dict):
                continue
            origin = str(item.get("origin") or "").strip()
            pairs = item.get("localStorage") or []
            if not origin or not isinstance(pairs, list):
                continue
            local_storage_by_origin[origin] = {
                str(pair.get("name") or ""): str(pair.get("value") or "")
                for pair in pairs
                if isinstance(pair, dict) and str(pair.get("name") or "")
            }
    if local_storage_by_origin:
        try:
            state_json = json.dumps(local_storage_by_origin, ensure_ascii=False)
            await ctx.add_init_script(
                f"""
                (() => {{
                  const state = {state_json};
                  try {{
                    const pairs = state[location.origin];
                    if (!pairs || !window.localStorage) return;
                    Object.keys(pairs).forEach((key) => localStorage.setItem(key, pairs[key]));
                  }} catch (e) {{}}
                }})();
                """
            )
        except Exception as exc:
            logger.debug("[ALIBABA-INQUIRY] restore localStorage init failed: %s", exc)
    restored.add(key)
    try:
        setattr(ctx, "_lobster_alibaba_restored_states", restored)
    except Exception:
        pass
    logger.info(
        "[ALIBABA-INQUIRY] restored browser state profile=%s cookies=%s origins=%s",
        str(acct.browser_profile or "")[-80:],
        len(cookies) if isinstance(cookies, list) else 0,
        len(local_storage_by_origin),
    )
    return True


async def _save_account_browser_state(acct: AlibabaInquiryAccount, page: Any) -> None:
    ctx = getattr(page, "context", None)
    if ctx is None:
        return
    path = _account_storage_state_path(acct)
    try:
        state = await ctx.storage_state()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        logger.info(
            "[ALIBABA-INQUIRY] saved browser state profile=%s cookies=%s origins=%s",
            str(acct.browser_profile or "")[-80:],
            len(state.get("cookies") or []) if isinstance(state, dict) else 0,
            len(state.get("origins") or []) if isinstance(state, dict) else 0,
        )
    except Exception as exc:
        logger.warning("[ALIBABA-INQUIRY] save browser state failed path=%s err=%s", path, exc)


def _account_runtime_snapshot(acct: AlibabaInquiryAccount) -> Any:
    return SimpleNamespace(
        id=int(acct.id),
        user_id=int(acct.user_id),
        nickname=str(acct.nickname or ""),
        browser_profile=str(acct.browser_profile or _default_account_profile(acct)),
    )


async def _watch_login_and_save_state(acct_snapshot: Any, page: Any, *, seconds: int = 600) -> None:
    deadline = datetime.utcnow().timestamp() + max(30, int(seconds))
    last_url = ""
    while datetime.utcnow().timestamp() < deadline:
        try:
            state = await _combined_login_state(page)
            page_info = state.get("page") if isinstance(state.get("page"), dict) else {}
            last_url = str(page_info.get("url") or getattr(page, "url", "") or "")
            if state.get("logged_in"):
                await _save_account_browser_state(acct_snapshot, page)
                logger.info(
                    "[ALIBABA-INQUIRY] login watcher saved state profile=%s url=%s",
                    str(getattr(acct_snapshot, "browser_profile", ""))[-80:],
                    last_url[:240],
                )
                return
        except Exception as exc:
            raw = str(exc).lower()
            if "has been closed" in raw or "targetclosederror" in raw or "target page" in raw:
                logger.info(
                    "[ALIBABA-INQUIRY] login watcher stopped because page closed profile=%s last_url=%s",
                    str(getattr(acct_snapshot, "browser_profile", ""))[-80:],
                    last_url[:240],
                )
                return
            logger.debug("[ALIBABA-INQUIRY] login watcher check failed: %s", exc)
        await asyncio.sleep(3)
    logger.info(
        "[ALIBABA-INQUIRY] login watcher timeout profile=%s last_url=%s",
        str(getattr(acct_snapshot, "browser_profile", ""))[-80:],
        last_url[:240],
    )


def _schedule_login_state_watch(acct: AlibabaInquiryAccount, page: Any) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    snapshot = _account_runtime_snapshot(acct)
    task_key = f"_lobster_alibaba_watch_{snapshot.user_id}_{snapshot.id}"
    ctx = getattr(page, "context", None)
    old_task = getattr(ctx, task_key, None) if ctx is not None else None
    if old_task is not None and not old_task.done():
        return
    task = loop.create_task(_watch_login_and_save_state(snapshot, page))
    if ctx is not None:
        try:
            setattr(ctx, task_key, task)
        except Exception:
            pass


def _login_state_for_log(state: Dict[str, Any]) -> Dict[str, Any]:
    page = state.get("page") if isinstance(state.get("page"), dict) else {}
    req = state.get("request") if isinstance(state.get("request"), dict) else {}
    cookies = state.get("cookies") if isinstance(state.get("cookies"), dict) else {}
    return {
        "logged_in": bool(state.get("logged_in")),
        "page_url": str(page.get("url") or "")[:300],
        "page_logged": bool(page.get("logged_in")),
        "page_login": bool(page.get("login_page")),
        "request_url": str(req.get("url") or "")[:300],
        "request_status": req.get("status"),
        "request_logged": bool(req.get("logged_in")),
        "request_login": bool(req.get("login_page")),
        "cookie_logged": bool(cookies.get("logged_in")),
        "cookie_matched": cookies.get("matched") or [],
        "cookie_xman_identity": bool(cookies.get("xman_identity")),
    }


def _compact(text: Any, limit: int = 500) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) > limit:
        return value[: limit - 1] + "…"
    return value


def _entity_text(value: Any, limit: int = 500) -> str:
    return _compact(html_unescape(str(value or "")), limit)


def _strip_html(value: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", value or "", flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<noscript[\s\S]*?</noscript>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_unescape(text.replace("&nbsp;", " "))
    text = re.sub(r"@[a-z-]+\s+[^{]+\{[^{}]{0,1200}\}", " ", text, flags=re.IGNORECASE)
    return _compact(text, 1200)


def _dedupe_strings(items: List[str], limit: int = 20) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _domain_from_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = "https://" + raw
    try:
        host = (urlparse(raw).hostname or "").lower()
    except Exception:
        host = ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _domain_from_email(value: str) -> str:
    text = str(value or "").strip()
    if "@" not in text:
        return ""
    domain = text.rsplit("@", 1)[-1].lower().strip(" .;，,")
    if domain.startswith("www."):
        domain = domain[4:]
    if domain in {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "qq.com", "163.com", "126.com"}:
        return ""
    return domain


def _parse_dt(value: str) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    patterns = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ]
    for fmt in patterns:
        try:
            return datetime.strptime(text[: len(datetime.utcnow().strftime(fmt))], fmt)
        except Exception:
            continue
    return None


def _first_platform_dt(text: str) -> Optional[datetime]:
    match = re.search(r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?", text or "")
    if not match:
        match = re.search(r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}", text or "")
    return _parse_dt(match.group(0)) if match else None


async def _account_lock(account_id: int, kind: str = "sync") -> asyncio.Lock:
    store = _account_reply_locks if kind == "reply" else _account_sync_locks
    async with _locks_guard:
        lock = store.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            store[account_id] = lock
        return lock


def _browser_options(meta: Optional[dict] = None) -> Dict[str, Any]:
    try:
        from publisher.browser_pool import DEFAULT_CHROME_UA, browser_options_from_publish_meta

        opts = browser_options_from_publish_meta(meta if isinstance(meta, dict) else None)
    except Exception:
        DEFAULT_CHROME_UA = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
        opts = {"user_agent": DEFAULT_CHROME_UA, "proxy": None}
    opts.setdefault("user_agent", DEFAULT_CHROME_UA)
    opts.setdefault("proxy", None)
    opts.setdefault("viewport", {"width": 1440, "height": 940})
    opts.setdefault("channel", os.environ.get("PLAYWRIGHT_BROWSER_CHANNEL", "").strip() or "chrome")
    # Alibaba International is sensitive to Playwright's launched-browser
    # fingerprint. Reuse the Douyin workbench CDP path so the account opens in
    # a real Chrome process with a persistent user-data-dir and minimal flags.
    opts["douyin_cdp"] = True
    opts["cdp_start_url"] = ALIBABA_INQUIRY_LIST_URL
    return opts


async def _get_account_page(acct: AlibabaInquiryAccount, *, visible: bool = True):
    from publisher.browser_pool import (
        _acquire_context,
        _ensure_visible_interactive_context,
        _get_page_with_reacquire,
        _setup_auto_close,
    )

    profile_dir = _resolve_account_profile(acct)
    opts = _browser_options(acct.meta)
    if visible:
        await _ensure_visible_interactive_context(profile_dir, browser_options=opts)
    ctx, _ = await _acquire_context(profile_dir, new_headless=not visible, browser_options=opts)
    page, ctx = await _get_page_with_reacquire(
        profile_dir,
        ctx,
        new_headless_on_recreate=not visible,
        browser_options=opts,
    )
    if visible:
        try:
            _setup_auto_close(ctx, profile_dir, page, browser_options=opts)
        except Exception:
            pass
    await _restore_account_browser_state(acct, page)
    return page


async def _goto(page: Any, url: str, *, timeout_ms: int = 90000) -> None:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        logger.warning("[ALIBABA-INQUIRY] page.goto domcontentloaded timeout/url issue: %s", url[:180])
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass


async def _page_login_state(page: Any) -> Dict[str, Any]:
    try:
        url = str(page.url or "")
    except Exception:
        url = ""
    try:
        text = await page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 5000)")
    except Exception:
        text = ""
    try:
        anchors = await page.evaluate(
            "() => document.querySelectorAll('a[href*=\"maDetail.htm?imInquiryId=\"]').length"
        )
    except Exception:
        anchors = 0
    lower_url = url.lower()
    lower_text = text.lower()
    try:
        password_inputs = await page.evaluate("() => document.querySelectorAll('input[type=\"password\"]').length")
    except Exception:
        password_inputs = 0
    login_page = (
        "login.alibaba" in lower_url
        or "passport.alibaba" in lower_url
        or bool(password_inputs)
        or ("sign in" in lower_text and "password" in lower_text and not anchors)
    )
    message_page = "message.alibaba.com/message" in lower_url
    logged_in = bool(anchors) or (message_page and "feedback" in lower_url and not login_page)
    return {
        "logged_in": logged_in,
        "url": url,
        "anchors": int(anchors or 0),
        "login_page": bool(login_page),
        "password_inputs": int(password_inputs or 0),
        "source": "page",
    }


async def _request_login_state(page: Any) -> Dict[str, Any]:
    """Check Alibaba login using the context request API, without moving the visible page."""
    try:
        resp = await page.context.request.get(
            ALIBABA_INQUIRY_LIST_URL,
            timeout=30000,
            max_redirects=8,
        )
        url = str(getattr(resp, "url", "") or "")
        status = int(getattr(resp, "status", 0) or 0)
        text = ""
        try:
            content_type = str((getattr(resp, "headers", {}) or {}).get("content-type", "")).lower()
            if "text" in content_type or "html" in content_type or "json" in content_type:
                text = (await resp.text())[:10000]
        except Exception:
            text = ""
        lower = f"{url}\n{text}".lower()
        anchors = len(re.findall(r"maDetail\.htm\?imInquiryId=", text or "", flags=re.I))
        login_page = (
            "login.alibaba" in lower
            or "passport.alibaba" in lower
            or ("sign in" in lower and "password" in lower and "message.alibaba.com/message" not in url.lower())
        )
        message_page = "message.alibaba.com/message" in url.lower()
        logged_in = bool(status and status < 400 and not login_page and (message_page or anchors))
        return {
            "logged_in": logged_in,
            "url": url,
            "status": status,
            "anchors": anchors,
            "login_page": bool(login_page),
            "source": "context_request",
        }
    except Exception as exc:
        return {"logged_in": False, "error": str(exc)[:500], "source": "context_request"}


async def _cookie_login_state(page: Any) -> Dict[str, Any]:
    """Best-effort cookie fallback; used only when page/request checks are inconclusive."""
    urls = [
        "https://message.alibaba.com",
        "https://www.alibaba.com",
        "https://login.alibaba.com",
        "https://passport.alibaba.com",
    ]
    try:
        cookies = await page.context.cookies(urls)
    except Exception as exc:
        return {"logged_in": False, "error": str(exc)[:500], "source": "cookies"}
    cookie_map = {
        str(item.get("name", "")).strip(): str(item.get("value", "")).strip()
        for item in (cookies or [])
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    }
    xman_us_t = cookie_map.get("xman_us_t", "")
    xman_t = cookie_map.get("xman_t", "")
    strong_cookie_names = {
        "ali_intl_member_id",
        "ali_member_id",
        "unb",
        "login_aliyunid",
        "intl_user_id",
    }
    has_xman_identity = bool(
        re.search(r"(?:^|[&;])x_lid=", xman_us_t)
        or re.search(r"(?:^|[&;])ctoken=", xman_us_t)
        or re.search(r"(?:^|[&;])x_lid=", xman_t)
        or re.search(r"(?:^|[&;])ctoken=", xman_t)
    )
    has_strong_cookie = any(cookie_map.get(name) for name in strong_cookie_names)
    logged_in = bool(has_xman_identity or has_strong_cookie)
    return {
        "logged_in": logged_in,
        "cookie_count": len(cookie_map),
        "matched": sorted(name for name in strong_cookie_names if cookie_map.get(name))[:12],
        "xman_identity": has_xman_identity,
        "source": "cookies",
    }


async def _combined_login_state(page: Any) -> Dict[str, Any]:
    """Read login status without forcing the visible Alibaba page to navigate."""
    page_state = await _page_login_state(page)
    request_state: Dict[str, Any] = {}
    cookie_state: Dict[str, Any] = {}
    logged_in = bool(page_state.get("logged_in"))
    explicit_login_page = bool(page_state.get("login_page")) and not logged_in
    if not logged_in and not explicit_login_page:
        request_state = await _request_login_state(page)
        logged_in = bool(request_state.get("logged_in"))
    # Alibaba's message center can reject context.request while the visible
    # Chrome profile still has a valid web session. Always inspect cookies
    # before deciding to send the user back to the login page.
    if not logged_in and not explicit_login_page:
        cookie_state = await _cookie_login_state(page)
        logged_in = bool(cookie_state.get("logged_in"))
    return {
        "logged_in": logged_in,
        "page": page_state,
        "request": request_state,
        "cookies": cookie_state,
        "note": "检测登录不再强制跳转阿里询盘页；同步询盘时才会进入业务页。",
    }


def _parse_list_row(row: Dict[str, Any]) -> Dict[str, Any]:
    text = _compact(row.get("text") or "\n".join(row.get("lines") or []), 5000)
    lines = [str(x or "").strip() for x in (row.get("lines") or []) if str(x or "").strip()]
    if not lines and text:
        lines = [x.strip() for x in re.split(r"\s{2,}|\n+", text) if x.strip()]

    inquiry_id = str(row.get("id") or "").strip()
    href = str(row.get("href") or "").strip()
    if not inquiry_id:
        m = re.search(r"imInquiryId=(\d+)", href)
        inquiry_id = m.group(1) if m else ""

    dates = re.findall(r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?", text)
    status = ""
    for cand in ("Ongoing", "Unread", "Replied", "Pending", "Closed", "Spam", "Archived", "Starred"):
        if re.search(rf"\b{re.escape(cand)}\b", text, re.I):
            status = cand
            break

    ignored = {
        inquiry_id,
        status,
        "message",
        "[message]",
        "ongoing",
        "unread",
        "replied",
        "pending",
        "closed",
        "spam",
        "archived",
    }
    title = ""
    buyer = ""
    preview = ""
    for line in lines:
        low = line.lower()
        if not line or low in ignored or re.match(r"^20\d{2}[-/]", line):
            continue
        if not title and len(line) > 12 and not re.fullmatch(r"[\w .@-]{1,32}", line):
            title = line[:255]
            continue
        if not buyer and re.search(r"[A-Za-z\u4e00-\u9fa5]", line) and len(line) <= 80:
            buyer = line[:255]
            continue
        if not preview and len(line) > 8:
            preview = line[:500]
    if not title:
        title = lines[0][:255] if lines else f"询盘 {inquiry_id}"
    if not preview:
        preview = text[:500]

    return {
        "inquiry_id": inquiry_id,
        "title": title,
        "buyer_name": buyer,
        "status": status,
        "preview": preview,
        "source_url": href,
        "last_message_at": _parse_dt(dates[0]) if dates else _first_platform_dt(text),
        "created_at_on_platform": _parse_dt(dates[-1]) if dates else None,
        "raw_text": text,
        "raw": row,
    }


async def _extract_inquiry_rows_from_page(page: Any) -> List[Dict[str, Any]]:
    js = r"""
    () => {
      const anchors = Array.from(document.querySelectorAll('a[href*="maDetail.htm?imInquiryId="]'));
      return anchors.map((a) => {
        const href = a.href || '';
        const id = (href.match(/imInquiryId=(\d+)/) || [])[1] || '';
        let node = a;
        for (let i = 0; i < 5 && node && node.parentElement; i += 1) {
          const t = (node.innerText || node.textContent || '').trim();
          if (t && t.length > 80) break;
          node = node.parentElement;
        }
        const text = ((node && (node.innerText || node.textContent)) || (a.innerText || a.textContent) || '')
          .replace(/\u00a0/g, ' ')
          .trim();
        const lines = text.split(/\n+/).map((x) => x.trim()).filter(Boolean);
        const img = node ? node.querySelector('img') : null;
        return {
          id,
          href,
          text,
          lines,
          avatar_url: img ? (img.currentSrc || img.src || '') : '',
        };
      }).filter((x) => x.id);
    }
    """
    try:
        rows = await page.evaluate(js)
    except Exception as exc:
        logger.warning("[ALIBABA-INQUIRY] extract list rows failed: %s", exc)
        rows = []
    seen: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        parsed = _parse_list_row(row if isinstance(row, dict) else {})
        if parsed.get("inquiry_id"):
            if row.get("avatar_url"):
                parsed.setdefault("raw", {})["avatar_url"] = row.get("avatar_url")
            seen[parsed["inquiry_id"]] = parsed
    return list(seen.values())


async def _scroll_once(page: Any) -> Dict[str, Any]:
    js = r"""
    () => {
      const doc = document.scrollingElement || document.documentElement || document.body;
      const candidates = Array.from(document.querySelectorAll('*'))
        .filter((el) => {
          const style = window.getComputedStyle(el);
          return el.scrollHeight > el.clientHeight + 120
            && ['auto', 'scroll', 'overlay'].includes(style.overflowY);
        })
        .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
      const targets = [doc, ...candidates.slice(0, 4)];
      let moved = 0;
      for (const el of targets) {
        const before = el.scrollTop || 0;
        const step = Math.max(520, Math.floor((el.clientHeight || window.innerHeight || 800) * 0.86));
        el.scrollTop = Math.min((el.scrollHeight || 0), before + step);
        if ((el.scrollTop || 0) > before) moved += 1;
      }
      window.scrollBy(0, Math.max(520, Math.floor((window.innerHeight || 800) * 0.86)));
      const bottom = targets.every((el) => ((el.scrollTop || 0) + (el.clientHeight || 0) + 8) >= (el.scrollHeight || 0));
      return { moved, bottom, scrollables: candidates.length };
    }
    """
    try:
        return await page.evaluate(js)
    except Exception:
        try:
            await page.mouse.wheel(0, random.randint(700, 1100))
        except Exception:
            pass
        return {"moved": 0, "bottom": False, "scrollables": 0}


async def _reset_list_scroll(page: Any) -> None:
    js = r"""
    () => {
      const doc = document.scrollingElement || document.documentElement || document.body;
      const candidates = Array.from(document.querySelectorAll('*'))
        .filter((el) => {
          const style = window.getComputedStyle(el);
          return el.scrollHeight > el.clientHeight + 120
            && ['auto', 'scroll', 'overlay'].includes(style.overflowY);
        })
        .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
      for (const el of [doc, ...candidates.slice(0, 6)]) {
        try {
          el.scrollTop = 0;
          el.dispatchEvent(new Event('scroll', { bubbles: true }));
        } catch (e) {}
      }
      try { window.scrollTo(0, 0); } catch (e) {}
      return true;
    }
    """
    try:
        await page.evaluate(js)
        await asyncio.sleep(0.35)
    except Exception:
        pass


async def _current_inquiry_ids(page: Any) -> List[str]:
    try:
        ids = await page.evaluate(
            r"""
            () => Array.from(document.querySelectorAll('a[href*="maDetail.htm?imInquiryId="]'))
              .map((a) => ((a.href || '').match(/imInquiryId=(\d+)/) || [])[1] || '')
              .filter(Boolean)
            """
        )
        return [str(x) for x in (ids or []) if x]
    except Exception:
        return []


async def _collect_current_inquiry_page(page: Any, max_scrolls: int, idle_rounds: int) -> Dict[str, Any]:
    await _reset_list_scroll(page)
    seen: Dict[str, Dict[str, Any]] = {}
    idle = 0
    last_count = 0
    scroll_meta: Dict[str, Any] = {}
    for round_index in range(max(1, max_scrolls)):
        rows = await _extract_inquiry_rows_from_page(page)
        for row in rows:
            seen[row["inquiry_id"]] = row
        count = len(seen)
        if count <= last_count:
            idle += 1
        else:
            idle = 0
        last_count = count
        if idle >= idle_rounds and scroll_meta.get("bottom"):
            break
        scroll_meta = await _scroll_once(page)
        await asyncio.sleep(random.uniform(0.35, 0.75))
    return {
        "rows": list(seen.values()),
        "scroll_rounds": round_index + 1,
        "bottom": bool(scroll_meta.get("bottom")),
    }


async def _click_next_inquiry_page(page: Any) -> Dict[str, Any]:
    before_ids = await _current_inquiry_ids(page)
    js = r"""
    () => {
      const isVisible = (el) => {
        const box = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return box.width > 4 && box.height > 4 && style.visibility !== 'hidden' && style.display !== 'none';
      };
      const disabled = (el) => {
        const cls = String(el.className || '').toLowerCase();
        return Boolean(el.disabled || el.getAttribute('disabled') !== null || el.getAttribute('aria-disabled') === 'true'
          || cls.includes('disabled') || cls.includes('ui2-disabled'));
      };
      const candidates = Array.from(document.querySelectorAll('a.next, button.next, a[class~="next"], button[class~="next"]'))
        .filter(isVisible);
      let target = candidates.find((el) => /next|下一页|下页/i.test((el.innerText || el.textContent || '') + ' ' + String(el.className || '')));
      if (!target) {
        target = Array.from(document.querySelectorAll('a,button')).filter(isVisible).find((el) => {
          const text = (el.innerText || el.textContent || el.title || el.getAttribute('aria-label') || '').trim();
          return /^(下一页|下页|Next|>)$/i.test(text);
        });
      }
      if (!target) return { clicked: false, reason: 'next_not_found' };
      if (disabled(target)) return { clicked: false, reason: 'next_disabled', text: (target.innerText || '').trim(), cls: String(target.className || '') };
      target.scrollIntoView({ block: 'center', inline: 'center' });
      target.click();
      return { clicked: true, text: (target.innerText || '').trim(), cls: String(target.className || '') };
    }
    """
    try:
        result = await page.evaluate(js)
    except Exception as exc:
        return {"clicked": False, "reason": f"click_failed: {exc}"}
    if not result or not result.get("clicked"):
        return result or {"clicked": False, "reason": "next_not_found"}
    await asyncio.sleep(random.uniform(0.9, 1.6))
    for _ in range(18):
        after_ids = await _current_inquiry_ids(page)
        if after_ids and after_ids != before_ids:
            result["changed"] = True
            return result
        await asyncio.sleep(0.5)
    result["changed"] = False
    result["reason"] = "next_page_not_changed"
    return result


async def _scroll_collect_all(page: Any, max_scrolls: int, idle_rounds: int, max_pages: int = 1) -> Dict[str, Any]:
    await _goto(page, ALIBABA_INQUIRY_LIST_URL)
    await asyncio.sleep(1.5)
    state = await _page_login_state(page)
    if not state.get("logged_in"):
        return {"ok": False, "need_login": True, "rows": [], "message": "未登录，请先扫码登录阿里国际站账号"}

    seen: Dict[str, Dict[str, Any]] = {}
    page_stats: List[Dict[str, Any]] = []
    total_scroll_rounds = 0
    pages_limit = max(1, int(max_pages or 1))
    for page_index in range(pages_limit):
        current = await _collect_current_inquiry_page(page, max_scrolls=max_scrolls, idle_rounds=idle_rounds)
        rows_on_page = current.get("rows") or []
        for row in rows_on_page:
            if not isinstance(row.get("raw"), dict):
                row["raw"] = {}
            row["raw"]["source_page"] = page_index + 1
            seen[row["inquiry_id"]] = row
        total_scroll_rounds += int(current.get("scroll_rounds") or 0)
        page_stats.append(
            {
                "page": page_index + 1,
                "rows": len(rows_on_page),
                "scroll_rounds": current.get("scroll_rounds") or 0,
            }
        )
        if page_index + 1 >= pages_limit:
            break
        next_result = await _click_next_inquiry_page(page)
        page_stats[-1]["next"] = next_result
        if not next_result.get("clicked") or not next_result.get("changed", True):
            break
    rows = list(seen.values())
    rows.sort(key=lambda x: x.get("last_message_at") or datetime.min, reverse=True)
    return {
        "ok": True,
        "need_login": False,
        "rows": rows,
        "scroll_rounds": total_scroll_rounds,
        "pages_scanned": len(page_stats),
        "page_stats": page_stats,
    }


def _field_after(lines: List[str], labels: List[str]) -> str:
    labels_l = [x.lower() for x in labels]
    for i, line in enumerate(lines):
        low = line.lower().rstrip(":：")
        if low in labels_l or any(low.endswith(x) for x in labels_l):
            for nxt in lines[i + 1 : i + 4]:
                if nxt and nxt.lower().rstrip(":：") not in labels_l:
                    return nxt[:255]
        for label in labels:
            m = re.match(rf"^{re.escape(label)}\s*[:：]\s*(.+)$", line, re.I)
            if m:
                return m.group(1).strip()[:255]
    return ""


def _parse_detail_text(text: str, inquiry: Optional[AlibabaInquiry]) -> Dict[str, Any]:
    raw_text = str(text or "").replace("\u00a0", " ")
    lines = [x.strip() for x in raw_text.splitlines() if x.strip()]
    buyer_name = _field_after(lines, ["Buyer", "Customer", "客户", "买家"]) or (inquiry.buyer_name if inquiry else "")
    country = _field_after(lines, ["Country", "国家/地区", "国家", "Region"])
    company = _field_after(lines, ["Company Name", "Company", "公司名称", "公司"])
    email = _field_after(lines, ["Email", "E-mail", "邮箱"])
    registration_time = _field_after(lines, ["Registration Time", "注册时间"])

    tags: List[str] = []
    tag_started = False
    for line in lines:
        low = line.lower()
        if "preference tags" in low or "偏好标签" in line:
            tag_started = True
            continue
        if tag_started and ("customer activity" in low or "activity" == low or "订单" in line):
            break
        if tag_started and len(line) <= 80:
            tags.append(line)
    activity: Dict[str, Any] = {}
    for num, label in re.findall(
        r"(\d+)\s+(Product Views|Valid Inquiries|Valid RFQs|Login Days|Spam Inquiries|Blacklisted)",
        raw_text,
        flags=re.I,
    ):
        activity[label] = int(num)

    messages: List[Dict[str, Any]] = []
    matches = list(re.finditer(r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?", raw_text))
    stop_markers = {
        "Customer",
        "Customer Details",
        "Customer activity",
        "Preference Tags",
        "Orders",
        "Files",
        "Follow-up tips",
        "Manage follow-ups",
    }
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw_text)
        chunk = raw_text[start:end].strip()
        chunk_lines = [x.strip() for x in chunk.splitlines() if x.strip()]
        content_lines: List[str] = []
        sender = ""
        for line in chunk_lines[:30]:
            if line in stop_markers:
                break
            if not sender and len(line) <= 80 and not re.search(r"[?？。.!！]$", line):
                sender = line
                continue
            content_lines.append(line)
        content = "\n".join(content_lines).strip()
        if not content:
            continue
        direction = "unknown"
        low_sender = sender.lower()
        if buyer_name and buyer_name.lower() in low_sender:
            direction = "buyer"
        elif low_sender in {"me", "you", "seller"} or "ma" in low_sender:
            direction = "seller"
        msg_uid = hashlib.sha1(f"{match.group(0)}\0{sender}\0{content[:160]}".encode("utf-8", "ignore")).hexdigest()[:40]
        messages.append(
            {
                "message_uid": msg_uid,
                "sent_at": _parse_dt(match.group(0)),
                "sender_name": sender[:255],
                "direction": direction,
                "content": content[:8000],
                "msg_type": "product" if "product" in content.lower() and len(content) < 500 else "text",
                "raw": {"chunk": chunk[:4000]},
            }
        )

    return {
        "profile": {
            "buyer_name": buyer_name[:255] if buyer_name else None,
            "country": country[:128] if country else None,
            "company_name": company[:255] if company else None,
            "email": email[:255] if email and email != "-" else None,
            "registration_time": registration_time[:64] if registration_time else None,
            "attributes": {"preference_tags": tags},
            "activity": activity,
            "raw_text": raw_text[:20000],
        },
        "messages": messages,
    }


async def _load_full_detail_messages(page: Any, max_rounds: int = 36) -> Dict[str, Any]:
    snapshot_js = r"""
    () => {
      const findScroller = () => {
        const preferred = ['.common-load-more', '.im-message-flow', '.im-session-wrapper', '.session-wrapper-inner'];
        for (const sel of preferred) {
          const el = document.querySelector(sel);
          if (el && el.scrollHeight >= el.clientHeight) return el;
        }
        return Array.from(document.querySelectorAll('*'))
          .filter((el) => el.scrollHeight > el.clientHeight + 80)
          .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))[0] || null;
      };
      const el = findScroller();
      const text = el ? (el.innerText || el.textContent || '') : (document.body && document.body.innerText || '');
      const first = (document.querySelector('.message-item-wrapper.item-left, .message-item-wrapper.item-right') || {});
      return {
        found: Boolean(el),
        scrollTop: el ? el.scrollTop || 0 : 0,
        scrollHeight: el ? el.scrollHeight || 0 : 0,
        clientHeight: el ? el.clientHeight || 0 : 0,
        textLen: text.length,
        msgCount: document.querySelectorAll('.message-item-wrapper.item-left, .message-item-wrapper.item-right').length,
        firstText: ((first.innerText || first.textContent || '') + '').replace(/\s+/g, ' ').slice(0, 180)
      };
    }
    """
    click_more_js = r"""
    () => {
      const isVisible = (el) => {
        const box = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return box.width > 4 && box.height > 4 && style.visibility !== 'hidden' && style.display !== 'none';
      };
      const nodes = Array.from(document.querySelectorAll('button,a,span,div'));
      const target = nodes.find((el) => {
        if (!isVisible(el)) return false;
        const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
        return text.length > 0 && text.length < 80 && /(查看更多|加载更多|历史消息|更多消息|View more|Load more|Show more)/i.test(text);
      });
      if (!target) return { clicked: false };
      target.scrollIntoView({ block: 'center', inline: 'center' });
      target.click();
      return { clicked: true, text: (target.innerText || target.textContent || '').trim() };
    }
    """
    scroll_top_js = r"""
    () => {
      const findScroller = () => {
        const preferred = ['.common-load-more', '.im-message-flow', '.im-session-wrapper', '.session-wrapper-inner'];
        for (const sel of preferred) {
          const el = document.querySelector(sel);
          if (el && el.scrollHeight >= el.clientHeight) return el;
        }
        return Array.from(document.querySelectorAll('*'))
          .filter((el) => el.scrollHeight > el.clientHeight + 80)
          .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))[0] || null;
      };
      const el = findScroller();
      if (el) {
        el.scrollTop = 0;
        el.dispatchEvent(new Event('scroll', { bubbles: true }));
      }
      try { window.scrollTo(0, 0); } catch (e) {}
      return { moved: Boolean(el), scrollTop: el ? el.scrollTop || 0 : 0 };
    }
    """
    last_sig = ""
    stable = 0
    meta: Dict[str, Any] = {"rounds": 0, "msg_count": 0, "text_len": 0}
    for idx in range(max(1, max_rounds)):
        try:
            before = await page.evaluate(snapshot_js)
        except Exception:
            before = {}
        try:
            clicked = await page.evaluate(click_more_js)
        except Exception:
            clicked = {"clicked": False}
        try:
            await page.evaluate(scroll_top_js)
        except Exception:
            pass
        await asyncio.sleep(random.uniform(0.75, 1.35))
        try:
            after = await page.evaluate(snapshot_js)
        except Exception:
            after = before or {}
        sig = json.dumps(
            {
                "count": after.get("msgCount"),
                "text": after.get("textLen"),
                "first": after.get("firstText"),
                "top": after.get("scrollTop"),
                "height": after.get("scrollHeight"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if sig == last_sig and not clicked.get("clicked") and int(after.get("scrollTop") or 0) <= 2:
            stable += 1
        else:
            stable = 0
        last_sig = sig
        meta = {
            "rounds": idx + 1,
            "msg_count": int(after.get("msgCount") or 0),
            "text_len": int(after.get("textLen") or 0),
            "scroll_top": int(after.get("scrollTop") or 0),
            "scroll_height": int(after.get("scrollHeight") or 0),
            "clicked_more": bool(clicked.get("clicked")),
        }
        if stable >= 2:
            break
    return meta


async def _extract_detail_dom_messages(page: Any, inquiry: AlibabaInquiry) -> List[Dict[str, Any]]:
    js = r"""
    () => {
      const dateRe = /20\d{2}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?/;
      const cleanupLines = (text) => (text || '')
        .replace(/\u00a0/g, ' ')
        .split(/\n+/)
        .map((x) => x.trim())
        .filter(Boolean);
      const skipLine = (line) => /^(翻译|已读|未读|发送失败|新版回复卡片|开通新版|\[立即开启\])$/i.test(line)
        || /^翻译提示/.test(line);
      const nodes = Array.from(document.querySelectorAll('.message-item-wrapper'))
        .filter((el) => /\bitem-left\b|\bitem-right\b/.test(String(el.className || '')));
      return nodes.map((el, index) => {
        const cls = String(el.className || '');
        const direction = /\bitem-right\b/.test(cls) ? 'seller' : 'buyer';
        const rawText = (el.innerText || el.textContent || '').replace(/\u00a0/g, ' ').trim();
        const lines = cleanupLines(rawText);
        const dateMatch = rawText.match(dateRe);
        const sentAt = dateMatch ? dateMatch[0] : '';
        const dateIndex = sentAt ? lines.findIndex((line) => line.includes(sentAt)) : -1;
        let sender = '';
        let contentLines = [];
        if (dateIndex >= 0) {
          if (direction === 'buyer' && dateIndex > 0) sender = lines[0] || '';
          contentLines = lines.slice(dateIndex + 1);
        } else {
          if (direction === 'buyer') {
            sender = lines[0] || '';
            contentLines = lines.slice(1);
          } else {
            contentLines = lines.slice(0);
          }
        }
        while (contentLines.length && /^[A-Z\u4e00-\u9fa5]$/.test(contentLines[0])) contentLines.shift();
        while (contentLines.length && (/^[A-Z\u4e00-\u9fa5]$/.test(contentLines[contentLines.length - 1]) || /^(已读|未读)$/i.test(contentLines[contentLines.length - 1]))) contentLines.pop();
        contentLines = contentLines.filter((line) => line !== sender && line !== sentAt && !skipLine(line));
        const imgs = Array.from(el.querySelectorAll('img')).map((img) => {
          const box = img.getBoundingClientRect();
          return {
            type: 'image',
            url: img.currentSrc || img.src || '',
            alt: img.alt || '',
            width: Math.round(box.width || img.naturalWidth || img.width || 0),
            height: Math.round(box.height || img.naturalHeight || img.height || 0)
          };
        }).filter((x) => x.url && (x.width > 72 || x.height > 72) && !/avatar|head|logo/i.test(x.url + ' ' + x.alt));
        const videos = Array.from(el.querySelectorAll('video')).map((video) => ({
          type: 'video',
          url: video.currentSrc || video.src || '',
          poster: video.poster || ''
        })).filter((x) => x.url || x.poster);
        const links = Array.from(el.querySelectorAll('a[href]')).map((a) => ({
          type: 'link',
          url: a.href || '',
          text: (a.innerText || a.textContent || '').trim()
        })).filter((x) => x.url && !/^javascript:/i.test(x.url));
        const attachments = imgs.concat(videos).concat(links);
        let msgType = 'text';
        if (videos.length) msgType = 'video';
        else if (imgs.length) msgType = 'image';
        else if (links.length) msgType = 'link';
        return {
          index,
          class_name: cls,
          direction,
          sender_name: sender,
          sent_at: sentAt,
          content: contentLines.join('\n').trim(),
          msg_type: msgType,
          attachments,
          raw_text: rawText
        };
      });
    }
    """
    try:
        rows = await page.evaluate(js)
    except Exception as exc:
        logger.warning("[ALIBABA-INQUIRY] extract dom messages failed inquiry=%s err=%s", inquiry.inquiry_id, exc)
        rows = []
    messages: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        attachments = row.get("attachments") if isinstance(row.get("attachments"), list) else []
        content = str(row.get("content") or "").strip()
        if not content and attachments:
            content = "[attachment]"
        if not content:
            continue
        sent_at = _parse_dt(str(row.get("sent_at") or "")) or _first_platform_dt(str(row.get("raw_text") or ""))
        uid_src = json.dumps(
            {
                "inquiry": inquiry.inquiry_id,
                "sent_at": _dt(sent_at),
                "direction": row.get("direction") or "unknown",
                "sender": row.get("sender_name") or "",
                "content": content[:500],
                "attachments": attachments,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        messages.append(
            {
                "message_uid": hashlib.sha1(uid_src.encode("utf-8", "ignore")).hexdigest()[:40],
                "sent_at": sent_at,
                "sender_name": str(row.get("sender_name") or "")[:255],
                "direction": str(row.get("direction") or "unknown")[:16],
                "content": content[:8000],
                "msg_type": str(row.get("msg_type") or "text")[:32],
                "raw": {
                    "source": "dom",
                    "class_name": row.get("class_name") or "",
                    "raw_text": str(row.get("raw_text") or "")[:4000],
                    "attachments": attachments,
                },
            }
        )
    return messages


async def _extract_detail_dom_profile(page: Any, inquiry: AlibabaInquiry) -> Dict[str, Any]:
    js = r"""
    () => {
      const panel = document.querySelector('.im-alicrm-box') || document.querySelector('[class*="alicrm"]');
      if (!panel) return {};
      const lines = (panel.innerText || panel.textContent || '')
        .replace(/\u00a0/g, ' ')
        .split(/\n+/)
        .map((x) => x.trim())
        .filter(Boolean);
      const after = (labels) => {
        const fieldLabels = new Set(['公司名称', 'Company Name', 'Company', '邮箱', 'Email', 'E-mail', '注册时间', 'Registration Time', '买家标签', '买家特征', '客户行为数据']);
        for (const label of labels) {
          const idx = lines.findIndex((line) => line === label || line.replace(/[:：]$/, '') === label);
          if (idx >= 0) {
            for (const item of lines.slice(idx + 1, idx + 5)) {
              if (!item || /^(客户详情|加为客户)$/.test(item)) continue;
              if (item === '-' || fieldLabels.has(item)) return '';
              return item;
            }
          }
        }
        return '';
      };
      let buyer = '';
      let country = '';
      const detailIdx = lines.findIndex((line) => line === '客户详情' || /^Customer Details$/i.test(line));
      if (detailIdx > 1) {
        buyer = lines[detailIdx - 2] || '';
        country = lines[detailIdx - 1] || '';
      } else {
        const firstUseful = lines.findIndex((line) => line && !/^[A-Z]$/.test(line) && !/^(客户|订单|文件)$/.test(line));
        buyer = firstUseful >= 0 ? lines[firstUseful] : '';
        country = firstUseful >= 0 ? (lines[firstUseful + 1] || '') : '';
      }
      const activity = {};
      for (let i = 0; i < lines.length - 1; i += 1) {
        if (/^\d+$/.test(lines[i]) && /产品浏览数|有效询盘|有效RFQ数|登录天数|垃圾询盘数|被加为黑名单数|Product Views|Valid Inquiries|Valid RFQs|Login Days|Spam Inquiries|Blacklisted/i.test(lines[i + 1])) {
          activity[lines[i + 1]] = Number(lines[i]);
        }
      }
      const featureIdx = lines.findIndex((line) => /买家特征|Buyer Features|Preference Tags/i.test(line));
      const tags = [];
      if (featureIdx >= 0) {
        for (const item of lines.slice(featureIdx + 1)) {
          if (/发送名片|客户行为数据|跟进建议|Customer activity|Orders|Files/i.test(item)) break;
          if (item && item !== '-') tags.push(item);
        }
      }
      const avatar = panel.querySelector('img');
      return {
        buyer_name: buyer,
        country,
        company_name: after(['公司名称', 'Company Name', 'Company']),
        email: after(['邮箱', 'Email', 'E-mail']),
        registration_time: after(['注册时间', 'Registration Time']),
        attributes: { preference_tags: tags },
        activity,
        avatar_url: avatar ? (avatar.currentSrc || avatar.src || '') : '',
        raw_text: lines.join('\n')
      };
    }
    """
    try:
        profile = await page.evaluate(js)
    except Exception as exc:
        logger.warning("[ALIBABA-INQUIRY] extract dom profile failed inquiry=%s err=%s", inquiry.inquiry_id, exc)
        profile = {}
    return profile if isinstance(profile, dict) else {}


async def _extract_detail(page: Any, inquiry: AlibabaInquiry) -> Dict[str, Any]:
    url = inquiry.source_url or f"https://message.alibaba.com/message/maDetail.htm?imInquiryId={inquiry.inquiry_id}"
    await _goto(page, url)
    await asyncio.sleep(random.uniform(1.2, 2.0))
    load_meta = await _load_full_detail_messages(page)
    text = await page.evaluate("() => (document.body && document.body.innerText || '')")
    parsed = _parse_detail_text(text, inquiry)
    dom_profile = await _extract_detail_dom_profile(page, inquiry)
    if dom_profile:
        for key in ("buyer_name", "country", "company_name", "email", "registration_time", "raw_text"):
            value = dom_profile.get(key)
            if value and value != "-":
                parsed["profile"][key] = value
        if isinstance(dom_profile.get("attributes"), dict):
            parsed["profile"]["attributes"] = dom_profile.get("attributes")
        if isinstance(dom_profile.get("activity"), dict):
            parsed["profile"]["activity"] = dom_profile.get("activity")
    dom_messages = await _extract_detail_dom_messages(page, inquiry)
    if dom_messages:
        parsed["messages"] = dom_messages
    try:
        avatar_url = await page.evaluate(
            r"""
            () => {
              const imgs = Array.from(document.querySelectorAll('img'));
              const scored = imgs.map((img) => {
                const src = img.currentSrc || img.src || '';
                const alt = img.alt || '';
                const box = img.getBoundingClientRect();
                let score = 0;
                if (/avatar|head|profile|customer|buyer/i.test(src + ' ' + alt)) score += 4;
                if (box.width >= 24 && box.width <= 96 && box.height >= 24 && box.height <= 96) score += 2;
                if (src.startsWith('http')) score += 1;
                return {src, score};
              }).filter((x) => x.src).sort((a, b) => b.score - a.score);
              return scored[0] ? scored[0].src : '';
            }
            """
        )
    except Exception:
        avatar_url = ""
    parsed["profile"]["avatar_url"] = dom_profile.get("avatar_url") or avatar_url or None
    parsed["profile"]["raw"] = {"url": page.url, "message_load": load_meta}
    return parsed


def _upsert_inquiries(db: Session, user_id: int, account_id: int, rows: List[Dict[str, Any]]) -> Dict[str, int]:
    created = 0
    updated = 0
    now = _now()
    for row in rows:
        inquiry_id = str(row.get("inquiry_id") or "").strip()
        if not inquiry_id:
            continue
        obj = (
            db.query(AlibabaInquiry)
            .filter(AlibabaInquiry.account_id == account_id, AlibabaInquiry.inquiry_id == inquiry_id)
            .first()
        )
        if not obj:
            obj = AlibabaInquiry(user_id=user_id, account_id=account_id, inquiry_id=inquiry_id)
            db.add(obj)
            created += 1
        else:
            updated += 1
        for key in (
            "title",
            "buyer_name",
            "buyer_login_id",
            "company_name",
            "country",
            "owner_name",
            "status",
            "preview",
            "source_url",
            "created_at_on_platform",
            "last_message_at",
            "raw_text",
            "raw",
        ):
            if key in row and row.get(key) not in (None, ""):
                setattr(obj, key, row.get(key))
        obj.synced_at = now
        obj.updated_at = now
    return {"created": created, "updated": updated}


def _upsert_detail(db: Session, user_id: int, account_id: int, inquiry: AlibabaInquiry, detail: Dict[str, Any]) -> Dict[str, Any]:
    profile_payload = detail.get("profile") if isinstance(detail.get("profile"), dict) else {}
    profile = (
        db.query(AlibabaCustomerProfile)
        .filter(AlibabaCustomerProfile.account_id == account_id, AlibabaCustomerProfile.inquiry_id == inquiry.inquiry_id)
        .first()
    )
    if not profile:
        profile = AlibabaCustomerProfile(user_id=user_id, account_id=account_id, inquiry_id=inquiry.inquiry_id)
        db.add(profile)
    for key in (
        "buyer_name",
        "buyer_login_id",
        "avatar_url",
        "country",
        "company_name",
        "email",
        "registration_time",
        "attributes",
        "activity",
        "raw_text",
        "raw",
    ):
        if key in profile_payload:
            setattr(profile, key, profile_payload.get(key))
    profile.updated_at = _now()
    if profile.buyer_name:
        inquiry.buyer_name = profile.buyer_name
    if profile.country:
        inquiry.country = profile.country
    if profile.company_name:
        inquiry.company_name = profile.company_name

    incoming_messages: List[Dict[str, Any]] = []
    incoming_uids: List[str] = []
    for msg in detail.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        uid = str(msg.get("message_uid") or "").strip()
        if not uid:
            uid = hashlib.sha1(json.dumps(msg, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:40]
        msg = dict(msg)
        msg["message_uid"] = uid
        incoming_messages.append(msg)
        incoming_uids.append(uid)

    existing_uids = set()
    if incoming_uids:
        existing_uids = {
            uid
            for (uid,) in db.query(AlibabaInquiryMessage.message_uid)
            .filter(
                AlibabaInquiryMessage.account_id == account_id,
                AlibabaInquiryMessage.inquiry_id == inquiry.inquiry_id,
                AlibabaInquiryMessage.message_uid.in_(incoming_uids),
            )
            .all()
        }

    inserted = 0
    inserted_rows: List[AlibabaInquiryMessage] = []
    seen_uids = set(existing_uids)
    for msg in incoming_messages:
        uid = str(msg.get("message_uid") or "").strip()
        if not uid or uid in seen_uids:
            continue
        seen_uids.add(uid)
        sent_at = msg.get("sent_at")
        if isinstance(sent_at, str):
            sent_at = _parse_dt(sent_at)
        msg_obj = AlibabaInquiryMessage(
            user_id=user_id,
            account_id=account_id,
            inquiry_id=inquiry.inquiry_id,
            message_uid=uid,
            direction=str(msg.get("direction") or "unknown")[:16],
            sender_name=(msg.get("sender_name") or "")[:255],
            content=str(msg.get("content") or "")[:8000],
            msg_type=str(msg.get("msg_type") or "text")[:32],
            sent_at=sent_at,
            raw=msg.get("raw") if isinstance(msg.get("raw"), dict) else None,
        )
        db.add(msg_obj)
        inserted_rows.append(msg_obj)
        inserted += 1
        if sent_at and (not inquiry.last_message_at or sent_at > inquiry.last_message_at):
            inquiry.last_message_at = sent_at
    inquiry.updated_at = _now()
    if inserted_rows:
        db.flush()
    return {
        "messages_inserted": inserted,
        "profile_saved": 1,
        "new_messages": [_serialize_new_message(row, inquiry) for row in inserted_rows],
    }


def _account_or_404(db: Session, user_id: int, account_id: int) -> AlibabaInquiryAccount:
    acct = (
        db.query(AlibabaInquiryAccount)
        .filter(AlibabaInquiryAccount.id == account_id, AlibabaInquiryAccount.user_id == user_id)
        .first()
    )
    if not acct:
        raise HTTPException(status_code=404, detail="阿里询盘账号不存在")
    return acct


def _serialize_account(db: Session, acct: AlibabaInquiryAccount) -> Dict[str, Any]:
    inquiry_count = (
        db.query(func.count(AlibabaInquiry.id))
        .filter(AlibabaInquiry.account_id == acct.id)
        .scalar()
        or 0
    )
    customer_count = (
        db.query(func.count(AlibabaCustomerArchive.id))
        .filter(AlibabaCustomerArchive.account_id == acct.id)
        .scalar()
        or 0
    )
    active_strategy = _active_summary(db, acct.user_id, acct.id)
    return {
        "id": acct.id,
        "nickname": acct.nickname,
        "status": acct.status,
        "auto_reply_enabled": bool(getattr(acct, "auto_reply_enabled", False)),
        "active_strategy_id": active_strategy.id if active_strategy else None,
        "sync_status": acct.sync_status,
        "sync_progress": acct.sync_progress or "",
        "last_login": _dt(acct.last_login),
        "last_sync_at": _dt(acct.last_sync_at),
        "last_error": acct.last_error or "",
        "inquiry_count": int(inquiry_count),
        "customer_count": int(customer_count),
        "created_at": _dt(acct.created_at),
    }


def _serialize_inquiry(row: AlibabaInquiry) -> Dict[str, Any]:
    return {
        "id": row.id,
        "account_id": row.account_id,
        "inquiry_id": row.inquiry_id,
        "title": row.title or f"询盘 {row.inquiry_id}",
        "buyer_name": row.buyer_name or "",
        "buyer_login_id": row.buyer_login_id or "",
        "company_name": row.company_name or "",
        "country": row.country or "",
        "owner_name": row.owner_name or "",
        "status": row.status or "",
        "preview": row.preview or "",
        "source_url": row.source_url or "",
        "created_at_on_platform": _dt(row.created_at_on_platform),
        "last_message_at": _dt(row.last_message_at),
        "ai_intent": row.ai_intent or "",
        "ai_notes": row.ai_notes or "",
        "synced_at": _dt(row.synced_at),
    }


def _serialize_profile(row: Optional[AlibabaCustomerProfile]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    return {
        "id": row.id,
        "inquiry_id": row.inquiry_id,
        "buyer_name": row.buyer_name or "",
        "buyer_login_id": row.buyer_login_id or "",
        "avatar_url": row.avatar_url or "",
        "country": row.country or "",
        "company_name": row.company_name or "",
        "email": row.email or "",
        "registration_time": row.registration_time or "",
        "attributes": row.attributes or {},
        "activity": row.activity or {},
        "updated_at": _dt(row.updated_at),
    }


def _serialize_archive(row: Optional[AlibabaCustomerArchive], db: Optional[Session] = None) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    profile = row.profile if isinstance(row.profile, dict) else {}
    pending = row.pending_review if isinstance(row.pending_review, dict) else {}
    evidence_count = 0
    if db is not None and row.id:
        evidence_count = (
            db.query(func.count(AlibabaCustomerArchiveEvidence.id))
            .filter(AlibabaCustomerArchiveEvidence.archive_id == row.id)
            .scalar()
            or 0
        )
    raw = row.raw if isinstance(row.raw, dict) else {}
    source_catalog = _normalize_public_source_catalog(profile.get("source_catalog") or raw.get("source_catalog") or [])
    used_sources = _normalize_public_used_sources(profile.get("used_sources") or raw.get("used_sources") or [])
    return {
        "id": row.id,
        "account_id": row.account_id,
        "inquiry_id": row.inquiry_id,
        "archive_key": row.archive_key,
        "status": row.status,
        "display_name": row.display_name,
        "company_name": row.company_name or "",
        "buyer_name": row.buyer_name or "",
        "country": row.country or "",
        "domain": row.domain or "",
        "email": row.email or "",
        "phone": row.phone or "",
        "grade": row.grade or "",
        "score": row.score,
        "summary": row.summary or "",
        "seed": row.seed or {},
        "profile": profile,
        "pending_review": pending,
        "linked_inquiry_ids": _linked_inquiry_ids(row),
        "field_evidence": _normalize_public_field_evidence(row.field_evidence or {}),
        "manual_overrides": row.manual_overrides or {},
        "source_catalog": source_catalog,
        "used_sources": used_sources,
        "raw": {
            "evidence_count": raw.get("evidence_count"),
            "route": raw.get("route"),
        },
        "pending_count": len(pending.get("items") or []) if isinstance(pending.get("items"), list) else 0,
        "evidence_count": int(evidence_count),
        "last_enriched_at": _dt(row.last_enriched_at),
        "last_error": row.last_error or "",
        "created_at": _dt(row.created_at),
        "updated_at": _dt(row.updated_at),
    }


def _normalize_public_field_evidence(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: Dict[str, Any] = {}
    for field, item in value.items():
        if not isinstance(item, dict):
            continue
        row = dict(item)
        sources = row.get("sources") if isinstance(row.get("sources"), list) else []
        clean_sources = []
        source_notes = row.get("source_notes") if isinstance(row.get("source_notes"), list) else []
        for source in sources:
            text = str(source or "").strip()
            if not text:
                continue
            if re.match(r"^https?://", text, re.IGNORECASE):
                continue
            clean_sources.append(_archive_public_source_type(text))
        row["sources"] = _dedupe_strings(clean_sources, 8)
        row["source_notes"] = _dedupe_strings([str(x or "").strip() for x in source_notes if str(x or "").strip()], 4)
        out[str(field)] = row
    return out


def _normalize_public_used_sources(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    grouped: Dict[str, Dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        st = _archive_public_source_type(item.get("source_type") or "")
        if not st or st in {"research_plan", "source_inventory"}:
            continue
        row = grouped.setdefault(st, {"source_type": st, "title": _archive_source_label(st), "count": 0, "fields": []})
        try:
            row["count"] += int(item.get("count") or 1)
        except Exception:
            row["count"] += 1
        row["fields"] = _dedupe_strings(list(row.get("fields") or []) + [str(x or "") for x in (item.get("fields") or [])], 8)
    return sorted(grouped.values(), key=lambda x: (-int(x.get("count") or 0), str(x.get("title") or "")))


def _normalize_public_source_catalog(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        st = _archive_public_source_type(item.get("source_type") or "")
        if not st or st in seen or st in {"research_plan", "source_inventory"}:
            continue
        seen.add(st)
        out.append(
            {
                "source_type": st,
                "title": _archive_source_label(st),
                "category": "实际使用",
                "role": item.get("role") or "本次客户档案补全实际引用的信息源。",
                "used": True,
            }
        )
    return out


def _serialize_archive_evidence(row: AlibabaCustomerArchiveEvidence) -> Dict[str, Any]:
    raw = row.raw if isinstance(row.raw, dict) else {}
    public_raw = {
        k: raw.get(k)
        for k in ("field", "purpose", "query", "page_kind", "result_count", "candidate_score", "discovered_from")
        if raw.get(k) not in (None, "")
    }
    return {
        "id": row.id,
        "source_type": _archive_public_source_type(row.source_type),
        "source_label": _archive_source_label(row.source_type),
        "title": row.title or "",
        "url": row.url or "",
        "snippet": row.snippet or "",
        "confidence": row.confidence,
        "raw": public_raw,
        "created_at": _dt(row.created_at),
    }


def _serialize_archive_job(row: AlibabaCustomerArchiveJob) -> Dict[str, Any]:
    return {
        "id": row.id,
        "archive_id": row.archive_id,
        "inquiry_id": row.inquiry_id,
        "status": row.status,
        "progress": row.progress or "",
        "error": row.error or "",
        "created_at": _dt(row.created_at),
        "updated_at": _dt(row.updated_at),
    }


def _serialize_message(row: AlibabaInquiryMessage) -> Dict[str, Any]:
    return {
        "id": row.id,
        "inquiry_id": row.inquiry_id,
        "message_uid": row.message_uid,
        "direction": row.direction,
        "sender_name": row.sender_name or "",
        "content": row.content,
        "msg_type": row.msg_type,
        "sent_at": _dt(row.sent_at),
        "created_at": _dt(row.created_at),
    }


def _serialize_new_message(row: AlibabaInquiryMessage, inquiry: AlibabaInquiry) -> Dict[str, Any]:
    data = _serialize_message(row)
    data.update(
        {
            "title": inquiry.title or f"询盘 {inquiry.inquiry_id}",
            "buyer_name": inquiry.buyer_name or "",
            "company_name": inquiry.company_name or "",
            "country": inquiry.country or "",
            "preview": inquiry.preview or "",
            "should_reply": False,
            "reply_reason": "",
        }
    )
    raw = row.raw if isinstance(row.raw, dict) else {}
    attachments = raw.get("attachments") if isinstance(raw.get("attachments"), list) else []
    if attachments:
        data["attachments"] = attachments
    return data


def _message_sort_key(row: Optional[AlibabaInquiryMessage]) -> Any:
    if not row:
        return (datetime.min, 0)
    return (row.sent_at or datetime.min, row.id or 0)


def _annotate_reply_candidates(db: Session, account_id: int, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not messages:
        return {"new_messages": [], "reply_candidates": []}
    by_inquiry = {str(item.get("inquiry_id") or "") for item in messages if item.get("inquiry_id")}
    candidate_uids: Dict[str, str] = {}
    for inquiry_id in by_inquiry:
        latest_buyer = (
            db.query(AlibabaInquiryMessage)
            .filter(
                AlibabaInquiryMessage.account_id == account_id,
                AlibabaInquiryMessage.inquiry_id == inquiry_id,
                AlibabaInquiryMessage.direction == "buyer",
            )
            .order_by(AlibabaInquiryMessage.sent_at.desc().nullslast(), AlibabaInquiryMessage.id.desc())
            .first()
        )
        latest_seller = (
            db.query(AlibabaInquiryMessage)
            .filter(
                AlibabaInquiryMessage.account_id == account_id,
                AlibabaInquiryMessage.inquiry_id == inquiry_id,
                AlibabaInquiryMessage.direction == "seller",
            )
            .order_by(AlibabaInquiryMessage.sent_at.desc().nullslast(), AlibabaInquiryMessage.id.desc())
            .first()
        )
        if latest_buyer and _message_sort_key(latest_buyer) > _message_sort_key(latest_seller):
            candidate_uids[inquiry_id] = latest_buyer.message_uid
    annotated: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    for item in messages:
        copied = dict(item)
        inquiry_id = str(copied.get("inquiry_id") or "")
        is_candidate = copied.get("direction") == "buyer" and copied.get("message_uid") == candidate_uids.get(inquiry_id)
        copied["should_reply"] = bool(is_candidate)
        copied["reply_reason"] = "买家最后一条新消息之后还没有卖家回复" if is_candidate else ""
        annotated.append(copied)
        if is_candidate:
            candidates.append(copied)
    return {"new_messages": annotated, "reply_candidates": candidates}


def _latest_local_message_at_map(db: Session, account_id: int, inquiry_ids: List[str]) -> Dict[str, datetime]:
    ids = [str(x or "").strip() for x in inquiry_ids if str(x or "").strip()]
    if not ids:
        return {}
    rows = (
        db.query(AlibabaInquiryMessage.inquiry_id, func.max(AlibabaInquiryMessage.sent_at))
        .filter(AlibabaInquiryMessage.account_id == account_id, AlibabaInquiryMessage.inquiry_id.in_(ids))
        .group_by(AlibabaInquiryMessage.inquiry_id)
        .all()
    )
    return {str(inquiry_id): latest for inquiry_id, latest in rows if latest}


def _needs_detail_incremental_sync(inquiry: AlibabaInquiry, latest_local: Optional[datetime]) -> bool:
    if not latest_local:
        return True
    remote_latest = inquiry.last_message_at
    if not remote_latest:
        return False
    return latest_local < remote_latest


def _latest_reply_state(db: Session, account_id: int, inquiry_id: str) -> Dict[str, Optional[AlibabaInquiryMessage]]:
    latest_buyer = (
        db.query(AlibabaInquiryMessage)
        .filter(
            AlibabaInquiryMessage.account_id == account_id,
            AlibabaInquiryMessage.inquiry_id == inquiry_id,
            AlibabaInquiryMessage.direction == "buyer",
        )
        .order_by(AlibabaInquiryMessage.sent_at.desc().nullslast(), AlibabaInquiryMessage.id.desc())
        .first()
    )
    latest_seller = (
        db.query(AlibabaInquiryMessage)
        .filter(
            AlibabaInquiryMessage.account_id == account_id,
            AlibabaInquiryMessage.inquiry_id == inquiry_id,
            AlibabaInquiryMessage.direction == "seller",
        )
        .order_by(AlibabaInquiryMessage.sent_at.desc().nullslast(), AlibabaInquiryMessage.id.desc())
        .first()
    )
    return {"buyer": latest_buyer, "seller": latest_seller}


async def _auto_reply_after_sync(
    page: Any,
    account_id: int,
    reply_candidates: List[Dict[str, Any]],
    request: Request,
    current_user: _ServerUser,
    db: Session,
    *,
    enabled: bool,
    max_replies: int = 5,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "enabled": bool(enabled),
        "candidate_count": len(reply_candidates or []),
        "attempted": 0,
        "sent": 0,
        "skipped": 0,
        "failed": 0,
        "items": [],
    }
    if not enabled:
        return result
    active = _active_summary(db, current_user.id, account_id)
    if not active:
        result["skipped_reason"] = "no_active_strategy"
        return result
    result["active_strategy_id"] = active.id

    seen: set = set()
    candidates: List[Dict[str, Any]] = []
    for item in reply_candidates or []:
        inquiry_id = str(item.get("inquiry_id") or "").strip()
        if not inquiry_id or inquiry_id in seen:
            continue
        seen.add(inquiry_id)
        candidates.append(item)
        if len(candidates) >= max_replies:
            break

    for item in candidates:
        inquiry_id = str(item.get("inquiry_id") or "").strip()
        entry: Dict[str, Any] = {"inquiry_id": inquiry_id, "status": "pending"}
        result["items"].append(entry)
        inquiry = (
            db.query(AlibabaInquiry)
            .filter(
                AlibabaInquiry.user_id == current_user.id,
                AlibabaInquiry.account_id == account_id,
                AlibabaInquiry.inquiry_id == inquiry_id,
            )
            .first()
        )
        if not inquiry:
            entry.update({"status": "skipped", "reason": "inquiry_not_found"})
            result["skipped"] += 1
            continue
        latest = _latest_reply_state(db, account_id, inquiry_id)
        if not (latest.get("buyer") and _message_sort_key(latest.get("buyer")) > _message_sort_key(latest.get("seller"))):
            entry.update({"status": "skipped", "reason": "already_replied"})
            result["skipped"] += 1
            continue
        result["attempted"] += 1
        try:
            draft_resp = await draft_reply(
                account_id,
                ReplyDraftBody(inquiry_id=inquiry_id, instruction=""),
                request,
                current_user,
                db,
            )
            draft = draft_resp.get("draft") if isinstance(draft_resp, dict) else {}
            content = str((draft or {}).get("reply") or "").strip()
            if not content:
                raise RuntimeError("draft reply empty")
            await asyncio.sleep(random.uniform(4.0, 9.0))
            await _goto(page, inquiry.source_url or f"https://message.alibaba.com/message/maDetail.htm?imInquiryId={inquiry.inquiry_id}")
            send_result = await _send_reply_via_page(page, content)
            if not send_result.get("ok"):
                raise RuntimeError(str(send_result.get("error") or "send failed"))
            uid = hashlib.sha1(f"auto\0{active.id}\0{_now().isoformat()}\0{content}".encode("utf-8")).hexdigest()[:40]
            db.add(
                AlibabaInquiryMessage(
                    user_id=current_user.id,
                    account_id=account_id,
                    inquiry_id=inquiry.inquiry_id,
                    message_uid=uid,
                    direction="seller",
                    sender_name="me",
                    content=content,
                    msg_type="text",
                    sent_at=_now(),
                    raw={"source": "auto_reply", "strategy_id": active.id, "draft": draft},
                )
            )
            db.commit()
            result["sent"] += 1
            entry.update({"status": "sent", "reply_preview": content[:240]})
            await asyncio.sleep(random.uniform(8.0, 18.0))
        except Exception as exc:
            logger.warning("[ALIBABA-INQUIRY] auto reply failed inquiry=%s err=%s", inquiry_id, exc)
            db.rollback()
            result["failed"] += 1
            entry.update({"status": "failed", "error": str(exc)[:500]})
    if result["candidate_count"] > len(candidates):
        result["skipped"] += result["candidate_count"] - len(candidates)
        result["limit_reached"] = True
    return result


@router.get("/api/alibaba-inquiries/accounts", summary="阿里询盘接管账号列表")
def list_accounts(
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(AlibabaInquiryAccount)
        .filter(AlibabaInquiryAccount.user_id == current_user.id)
        .order_by(AlibabaInquiryAccount.created_at.desc())
        .all()
    )
    return {"ok": True, "accounts": [_serialize_account(db, row) for row in rows]}


@router.post("/api/alibaba-inquiries/accounts", summary="新增阿里询盘账号")
def create_account(
    body: CreateAccountBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    nickname = (body.nickname or "阿里国际站账号").strip()[:128]
    acct = AlibabaInquiryAccount(user_id=current_user.id, nickname=nickname, status="pending")
    db.add(acct)
    db.commit()
    db.refresh(acct)
    profile = _BROWSER_DATA_DIR / f"alibaba_inquiry_{current_user.id}_{acct.id}_{_safe_name(nickname)}"
    profile.mkdir(parents=True, exist_ok=True)
    acct.browser_profile = str(profile)
    db.commit()
    db.refresh(acct)
    return {"ok": True, "account": _serialize_account(db, acct), "message": "账号已添加，请打开登录完成扫码"}


@router.post("/api/alibaba-inquiries/accounts/{account_id}/login", summary="打开阿里国际站账号浏览器")
async def open_login(
    account_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = _account_or_404(db, current_user.id, account_id)
    try:
        page = await _get_account_page(acct, visible=True)
        state = await _combined_login_state(page)
        if state.get("logged_in"):
            try:
                url = str(getattr(page, "url", "") or "").lower()
            except Exception:
                url = ""
            if "message.alibaba.com/message" not in url or "login" in url:
                await _goto(page, ALIBABA_INQUIRY_LIST_URL, timeout_ms=120000)
                state = await _combined_login_state(page)
            logger.info(
                "[ALIBABA-INQUIRY] open account logged profile=%s state=%s",
                str(acct.browser_profile or "")[-100:],
                _login_state_for_log(state),
            )
            await _save_account_browser_state(acct, page)
            acct.status = "active"
            acct.last_login = _now()
            acct.last_error = None
            db.commit()
            return {
                "ok": True,
                "logged_in": True,
                "status": acct.status,
                "detail": state,
                "message": "已打开账号浏览器，当前已登录，不再跳转登录页。",
            }

        await _goto(page, ALIBABA_INQUIRY_LIST_URL, timeout_ms=120000)
        state = await _combined_login_state(page)
        logged_in = bool(state.get("logged_in"))
        acct.status = "active" if logged_in else "pending"
        if logged_in:
            await _save_account_browser_state(acct, page)
            acct.last_login = _now()
        else:
            _schedule_login_state_watch(acct, page)
        acct.last_error = None
        db.commit()
        logger.info(
            "[ALIBABA-INQUIRY] open account after goto profile=%s state=%s",
            str(acct.browser_profile or "")[-100:],
            _login_state_for_log(state),
        )
        return {
            "ok": True,
            "logged_in": logged_in,
            "status": acct.status,
            "detail": state,
            "message": "已打开阿里国际站询盘页面。" if logged_in else "已打开阿里国际站询盘/登录页面，请在浏览器里扫码/登录。",
        }
    except Exception as exc:
        logger.exception("[ALIBABA-INQUIRY] open login failed")
        acct.status = "error"
        acct.last_error = str(exc)[:1000]
        db.commit()
        return {"ok": False, "message": str(exc)}


@router.get("/api/alibaba-inquiries/accounts/{account_id}/login-status", summary="检测阿里询盘登录态")
async def login_status(
    account_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = _account_or_404(db, current_user.id, account_id)
    try:
        page = await _get_account_page(acct, visible=True)
        detail = await _combined_login_state(page)
        logged_in = bool(detail.get("logged_in"))
        acct.status = "active" if logged_in else "pending"
        if logged_in:
            await _save_account_browser_state(acct, page)
            acct.last_login = _now()
        else:
            _schedule_login_state_watch(acct, page)
        acct.last_error = None
        db.commit()
        return {"ok": True, "logged_in": logged_in, "status": acct.status, "detail": detail}
    except Exception as exc:
        logger.exception("[ALIBABA-INQUIRY] check login failed")
        acct.status = "error"
        acct.last_error = str(exc)[:1000]
        db.commit()
        return {"ok": False, "logged_in": False, "status": "error", "message": str(exc)}


@router.post("/api/alibaba-inquiries/accounts/{account_id}/sync", summary="滚动同步阿里询盘历史列表")
async def sync_inquiries(
    account_id: int,
    body: SyncBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = _account_or_404(db, current_user.id, account_id)
    lock = await _account_lock(account_id, "sync")
    if lock.locked():
        return {"ok": False, "message": "该账号正在同步中，请稍后查看结果", "account": _serialize_account(db, acct)}
    async with lock:
        acct.sync_status = "running"
        acct.sync_progress = "正在打开阿里询盘列表"
        acct.last_error = None
        db.commit()
        try:
            page = await _get_account_page(acct, visible=True)
            result = await _scroll_collect_all(
                page,
                max_scrolls=body.max_scrolls,
                idle_rounds=body.stop_after_idle_rounds,
                max_pages=body.max_pages,
            )
            if result.get("need_login"):
                acct.status = "pending"
                acct.sync_status = "failed"
                acct.sync_progress = "未登录"
                acct.last_error = result.get("message") or "未登录"
                db.commit()
                return {"ok": False, "need_login": True, "message": acct.last_error, "account": _serialize_account(db, acct)}
            rows = result.get("rows") or []
            await _save_account_browser_state(acct, page)
            counts = _upsert_inquiries(db, current_user.id, account_id, rows)
            acct.status = "active"
            acct.last_login = acct.last_login or _now()
            acct.last_sync_at = _now()
            acct.sync_progress = f"已同步列表 {len(rows)} 条"
            detail_counts = {"details_synced": 0, "details_skipped": 0, "messages_inserted": 0}
            new_messages: List[Dict[str, Any]] = []
            db.commit()

            if body.sync_details and rows:
                current_inquiry_ids = [str(row.get("inquiry_id") or "").strip() for row in rows if row.get("inquiry_id")]
                q = (
                    db.query(AlibabaInquiry)
                    .filter(AlibabaInquiry.account_id == account_id, AlibabaInquiry.inquiry_id.in_(current_inquiry_ids))
                    .order_by(AlibabaInquiry.last_message_at.desc().nullslast(), AlibabaInquiry.updated_at.desc())
                )
                all_detail_rows = q.limit(body.detail_limit).all() if body.detail_limit else q.all()
                latest_map = _latest_local_message_at_map(db, account_id, [row.inquiry_id for row in all_detail_rows])
                detail_rows = [
                    row
                    for row in all_detail_rows
                    if _needs_detail_incremental_sync(row, latest_map.get(row.inquiry_id))
                ]
                detail_counts["details_skipped"] = len(all_detail_rows) - len(detail_rows)
                for idx, inquiry in enumerate(detail_rows, start=1):
                    acct.sync_progress = f"正在同步详情 {idx}/{len(detail_rows)}"
                    db.commit()
                    try:
                        detail = await _extract_detail(page, inquiry)
                        dc = _upsert_detail(db, current_user.id, account_id, inquiry, detail)
                        db.commit()
                        detail_counts["details_synced"] += 1
                        detail_counts["messages_inserted"] += dc.get("messages_inserted", 0)
                        new_messages.extend(dc.get("new_messages") or [])
                        await asyncio.sleep(random.uniform(0.6, 1.4))
                    except Exception as exc:
                        logger.warning("[ALIBABA-INQUIRY] detail sync failed inquiry=%s err=%s", inquiry.inquiry_id, exc)
                        db.rollback()
            reply_info = _annotate_reply_candidates(db, account_id, new_messages)
            new_messages = reply_info["new_messages"]
            reply_candidates = reply_info["reply_candidates"]
            auto_reply = await _auto_reply_after_sync(
                page,
                account_id,
                reply_candidates,
                request,
                current_user,
                db,
                enabled=bool(getattr(acct, "auto_reply_enabled", False)),
            )
            acct.sync_status = "idle"
            acct.sync_progress = f"同步完成：询盘 {len(rows)} 条，新增消息 {len(new_messages)} 条，待回复 {len(reply_candidates)} 条"
            acct.last_sync_at = _now()
            acct.last_error = None
            db.commit()
            return {
                "ok": True,
                "message": acct.sync_progress,
                "created": counts["created"],
                "updated": counts["updated"],
                "total_seen": len(rows),
                "scroll_rounds": result.get("scroll_rounds") or 0,
                "pages_scanned": result.get("pages_scanned") or 0,
                **detail_counts,
                "new_messages_count": len(new_messages),
                "new_messages": new_messages,
                "reply_candidate_count": len(reply_candidates),
                "reply_candidates": reply_candidates,
                "auto_reply": auto_reply,
                "account": _serialize_account(db, acct),
            }
        except Exception as exc:
            logger.exception("[ALIBABA-INQUIRY] sync failed")
            db.rollback()
            acct = _account_or_404(db, current_user.id, account_id)
            acct.sync_status = "failed"
            acct.sync_progress = "同步失败"
            acct.last_error = str(exc)[:1000]
            db.commit()
            return {"ok": False, "message": str(exc), "account": _serialize_account(db, acct)}


@router.get("/api/alibaba-inquiries/accounts/{account_id}/inquiries", summary="分页查询阿里询盘")
def list_inquiries(
    account_id: int,
    q: str = "",
    status: str = "",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    limit = max(1, min(MAX_PAGE_SIZE, int(limit or DEFAULT_PAGE_SIZE)))
    offset = max(0, int(offset or 0))
    query = db.query(AlibabaInquiry).filter(
        AlibabaInquiry.user_id == current_user.id,
        AlibabaInquiry.account_id == account_id,
    )
    kw = (q or "").strip()
    if kw:
        like = f"%{kw}%"
        query = query.filter(
            or_(
                AlibabaInquiry.inquiry_id.like(like),
                AlibabaInquiry.title.like(like),
                AlibabaInquiry.buyer_name.like(like),
                AlibabaInquiry.company_name.like(like),
                AlibabaInquiry.preview.like(like),
            )
        )
    st = (status or "").strip()
    if st:
        query = query.filter(AlibabaInquiry.status == st)
    total = query.count()
    rows = (
        query.order_by(AlibabaInquiry.last_message_at.desc().nullslast(), AlibabaInquiry.updated_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    inquiry_ids = [x.inquiry_id for x in rows]
    profiles = {}
    archives = {}
    if inquiry_ids:
        for profile in (
            db.query(AlibabaCustomerProfile)
            .filter(
                AlibabaCustomerProfile.account_id == account_id,
                AlibabaCustomerProfile.inquiry_id.in_(inquiry_ids),
            )
            .all()
        ):
            profiles[profile.inquiry_id] = _serialize_profile(profile)
        archive_rows = db.query(AlibabaCustomerArchive).filter(AlibabaCustomerArchive.account_id == account_id).all()
        inquiry_id_set = set(inquiry_ids)
        for archive in archive_rows:
            serialized = _serialize_archive(archive, db)
            for linked_id in _linked_inquiry_ids(archive):
                if linked_id in inquiry_id_set:
                    archives[linked_id] = serialized
    items = []
    for row in rows:
        item = _serialize_inquiry(row)
        if row.inquiry_id in profiles:
            item["profile"] = profiles[row.inquiry_id]
        if row.inquiry_id in archives:
            item["archive"] = archives[row.inquiry_id]
        items.append(item)
    return {"ok": True, "total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/api/alibaba-inquiries/accounts/{account_id}/inquiries/{inquiry_id}", summary="查询询盘详情")
def get_inquiry_detail(
    account_id: int,
    inquiry_id: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    inquiry = (
        db.query(AlibabaInquiry)
        .filter(
            AlibabaInquiry.user_id == current_user.id,
            AlibabaInquiry.account_id == account_id,
            AlibabaInquiry.inquiry_id == inquiry_id,
        )
        .first()
    )
    if not inquiry:
        raise HTTPException(404, detail="询盘不存在")
    profile = (
        db.query(AlibabaCustomerProfile)
        .filter(AlibabaCustomerProfile.account_id == account_id, AlibabaCustomerProfile.inquiry_id == inquiry_id)
        .first()
    )
    archive = _archive_for_inquiry(db, account_id, inquiry_id)
    messages = (
        db.query(AlibabaInquiryMessage)
        .filter(AlibabaInquiryMessage.account_id == account_id, AlibabaInquiryMessage.inquiry_id == inquiry_id)
        .order_by(AlibabaInquiryMessage.sent_at.asc().nullslast(), AlibabaInquiryMessage.id.asc())
        .all()
    )
    return {
        "ok": True,
        "inquiry": _serialize_inquiry(inquiry),
        "profile": _serialize_profile(profile),
        "archive": _serialize_archive(archive, db),
        "messages": [_serialize_message(x) for x in messages],
    }


@router.post("/api/alibaba-inquiries/accounts/{account_id}/inquiries/{inquiry_id}/sync-detail", summary="同步单个询盘详情")
async def sync_inquiry_detail(
    account_id: int,
    inquiry_id: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = _account_or_404(db, current_user.id, account_id)
    inquiry = (
        db.query(AlibabaInquiry)
        .filter(
            AlibabaInquiry.user_id == current_user.id,
            AlibabaInquiry.account_id == account_id,
            AlibabaInquiry.inquiry_id == inquiry_id,
        )
        .first()
    )
    if not inquiry:
        raise HTTPException(404, detail="询盘不存在")
    try:
        page = await _get_account_page(acct, visible=True)
        detail = await _extract_detail(page, inquiry)
        counts = _upsert_detail(db, current_user.id, account_id, inquiry, detail)
        db.commit()
        reply_info = _annotate_reply_candidates(db, account_id, counts.get("new_messages") or [])
        return {
            "ok": True,
            "message": "详情已同步",
            **counts,
            "new_messages_count": len(reply_info["new_messages"]),
            "new_messages": reply_info["new_messages"],
            "reply_candidate_count": len(reply_info["reply_candidates"]),
            "reply_candidates": reply_info["reply_candidates"],
        }
    except Exception as exc:
        logger.exception("[ALIBABA-INQUIRY] sync detail failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/alibaba-inquiries/accounts/{account_id}/customers", summary="客户档案分页")
def list_customers(
    account_id: int,
    q: str = "",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    limit = max(1, min(MAX_PAGE_SIZE, int(limit or DEFAULT_PAGE_SIZE)))
    offset = max(0, int(offset or 0))
    query = db.query(AlibabaCustomerProfile).filter(
        AlibabaCustomerProfile.user_id == current_user.id,
        AlibabaCustomerProfile.account_id == account_id,
    )
    kw = (q or "").strip()
    if kw:
        like = f"%{kw}%"
        query = query.filter(
            or_(
                AlibabaCustomerProfile.buyer_name.like(like),
                AlibabaCustomerProfile.company_name.like(like),
                AlibabaCustomerProfile.country.like(like),
                AlibabaCustomerProfile.email.like(like),
            )
        )
    total = query.count()
    rows = query.order_by(AlibabaCustomerProfile.updated_at.desc()).offset(offset).limit(limit).all()
    return {"ok": True, "total": total, "limit": limit, "offset": offset, "items": [_serialize_profile(x) for x in rows]}


def _archive_seed_from_inquiry(
    db: Session,
    account_id: int,
    inquiry: AlibabaInquiry,
    profile: Optional[AlibabaCustomerProfile],
) -> Dict[str, Any]:
    messages = (
        db.query(AlibabaInquiryMessage)
        .filter(AlibabaInquiryMessage.account_id == account_id, AlibabaInquiryMessage.inquiry_id == inquiry.inquiry_id)
        .order_by(AlibabaInquiryMessage.sent_at.asc().nullslast(), AlibabaInquiryMessage.id.asc())
        .limit(120)
        .all()
    )
    message_items = [_serialize_message(x) for x in messages]
    profile_data = _serialize_profile(profile) or {}
    raw_text_parts = [
        inquiry.title or "",
        inquiry.preview or "",
        inquiry.raw_text or "",
        profile.raw_text if profile else "",
        "\n".join(f"{m.direction}/{m.sender_name or '-'}: {m.content}" for m in messages),
    ]
    raw_blob = "\n".join(x for x in raw_text_parts if x)
    emails = _dedupe_strings(re.findall(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", raw_blob, re.IGNORECASE), 12)
    urls = _dedupe_strings(re.findall(r"https?://[^\s\"'<>，。！？、；]+", raw_blob, re.IGNORECASE), 12)
    phone_blob = re.sub(r"https?://\S+", " ", raw_blob, flags=re.IGNORECASE)
    phones = []
    inquiry_digits = re.sub(r"\D+", "", str(inquiry.inquiry_id or ""))
    for item in re.findall(r"(?<!\d)(?:\+?\d[\d\s().-]{6,}\d)(?!\d)", phone_blob):
        digits = re.sub(r"\D+", "", item)
        if digits and inquiry_digits and digits == inquiry_digits:
            continue
        if re.search(r"\b20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}\b", item):
            continue
        if re.match(r"^20\d{2}\d{1,2}\d{1,2}$", digits):
            continue
        if 7 <= len(digits) <= 18 and not re.match(r"^20\d{6,}", digits):
            phones.append(item.strip())
    phones = _dedupe_strings(phones, 8)

    domains = []
    if profile and profile.email:
        domains.append(_domain_from_email(profile.email))
    for email in emails:
        domains.append(_domain_from_email(email))
    for url in urls:
        domains.append(_domain_from_url(url))
    domains = [x for x in _dedupe_strings(domains, 10) if x]

    company_name = _entity_text((profile.company_name if profile else "") or inquiry.company_name or "", 255)
    buyer_name = _entity_text((profile.buyer_name if profile else "") or inquiry.buyer_name or "", 255)
    email = _entity_text((profile.email if profile else "") or (emails[0] if emails else ""), 255)
    phone = _entity_text(phones[0] if phones else "", 64)
    domain = domains[0] if domains else ""
    country = _entity_text((profile.country if profile else "") or inquiry.country or "", 128)
    market_scope = "CN" if re.search(r"中国|China|Hong Kong|Taiwan|Macau", country or "", re.IGNORECASE) else "GLOBAL"
    return {
        "schema_version": "alibaba_archive_seed_v1",
        "inquiry_id": inquiry.inquiry_id,
        "title": inquiry.title or f"询盘 {inquiry.inquiry_id}",
        "buyer_name": buyer_name,
        "buyer_login_id": (profile.buyer_login_id if profile else "") or inquiry.buyer_login_id or "",
        "company_name": company_name,
        "country": country,
        "email": email,
        "phone": phone,
        "domain": domain,
        "domains": domains,
        "urls": urls,
        "emails": emails,
        "phones": phones,
        "market_scope": market_scope,
        "messages": message_items,
        "messages_text": _compact(raw_blob, 18000),
        "alibaba_profile": profile_data,
        "last_message_at": _dt(inquiry.last_message_at),
        "source_url": inquiry.source_url or "",
        "seed_quality": {
            "has_company": bool(company_name),
            "has_domain": bool(domain),
            "has_email": bool(email),
            "has_messages": bool(messages),
        },
    }


def _augment_seed_with_related_inquiries(db: Session, account_id: int, seed: Dict[str, Any], linked_ids: List[str]) -> Dict[str, Any]:
    linked_ids = _dedupe_strings([str(x or "").strip() for x in linked_ids if str(x or "").strip()], 120)
    if not linked_ids:
        return seed
    rows = (
        db.query(AlibabaInquiry)
        .filter(AlibabaInquiry.account_id == account_id, AlibabaInquiry.inquiry_id.in_(linked_ids))
        .order_by(AlibabaInquiry.last_message_at.desc().nullslast(), AlibabaInquiry.id.desc())
        .limit(30)
        .all()
    )
    related = []
    for row in rows:
        related.append(
            {
                "inquiry_id": row.inquiry_id,
                "title": row.title or "",
                "buyer_name": row.buyer_name or "",
                "company_name": row.company_name or "",
                "country": row.country or "",
                "preview": row.preview or "",
                "last_message_at": _dt(row.last_message_at),
            }
        )
    merged = dict(seed)
    merged["linked_inquiry_ids"] = linked_ids
    merged["related_inquiries"] = related
    return merged


def _archive_key(seed: Dict[str, Any]) -> str:
    basis = (
        str(seed.get("domain") or "")
        or str(seed.get("company_name") or "")
        or str(seed.get("email") or "")
        or str(seed.get("buyer_name") or "")
        or str(seed.get("inquiry_id") or "")
    ).strip().lower()
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
    return f"ali_{digest}"


def _first_text(*values: Any, limit: int = 255) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text[:limit]
    return ""


def _json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return value.get("items") or []
    return []


def _linked_inquiry_ids(row: Optional[AlibabaCustomerArchive]) -> List[str]:
    if not row:
        return []
    ids = [str(row.inquiry_id or "").strip()]
    for item in _json_list(row.linked_inquiry_ids):
        text = str(item or "").strip()
        if text:
            ids.append(text)
    return _dedupe_strings(ids, 200)


_ARCHIVE_SOURCE_LABELS: Dict[str, str] = {
    "alibaba_inquiry": "阿里询盘原始消息",
    "alibaba_profile": "阿里询盘右侧客户属性",
    "official_website": "官方公开网站",
    "web_search": "公开网页搜索",
    "company_registry": "企业主体库",
    "research_plan": "字段深度调研计划",
    "professional_network_company": "职业社媒公司资料",
    "short_video_account_search": "短视频账号公开资料",
    "short_video_content_search": "短视频内容公开资料",
    "commerce_product_search": "电商商品公开资料",
    "local_video_account_search": "视频号账号公开资料",
    "local_video_content_search": "视频号内容公开资料",
    "visual_social_search": "图片社媒公开资料",
    "public_discussion_search": "海外公开讨论资料",
    # 兼容旧档案记录，展示层统一转为产品内名称。
    "source_inventory": "公开资料调研状态",
    "tikhub_linkedin_company": "职业社媒公司资料",
    "tikhub_linkedin_posts": "职业社媒公司动态",
    "tikhub_tiktok_user_search": "短视频账号公开资料",
    "tikhub_tiktok_video_search": "短视频内容公开资料",
    "tikhub_tiktok_shop_product_search": "电商商品公开资料",
    "tikhub_wechat_channels_user_search": "视频号账号公开资料",
    "tikhub_wechat_channels_search": "视频号内容公开资料",
    "tikhub_instagram_search": "图片社媒公开资料",
    "tikhub_x_search": "海外公开讨论资料",
}

_ARCHIVE_SOURCE_TYPE_ALIASES: Dict[str, str] = {
    "tikhub_linkedin_company": "professional_network_company",
    "tikhub_linkedin_posts": "professional_network_company",
    "tikhub_tiktok_user_search": "short_video_account_search",
    "tikhub_tiktok_video_search": "short_video_content_search",
    "tikhub_tiktok_shop_product_search": "commerce_product_search",
    "tikhub_wechat_channels_user_search": "local_video_account_search",
    "tikhub_wechat_channels_search": "local_video_content_search",
    "tikhub_instagram_search": "visual_social_search",
    "tikhub_x_search": "public_discussion_search",
}


_ARCHIVE_FIELD_LABELS: Dict[str, str] = {
    "company_name": "公司名称",
    "buyer_name": "买家姓名",
    "country": "国家/地区",
    "domain": "官网/域名",
    "email": "邮箱",
    "phone": "电话",
    "product_keywords": "产品关键词",
    "messages_text": "询盘消息",
}


def _archive_source_label(source_type: Any) -> str:
    key = _archive_public_source_type(source_type)
    return _ARCHIVE_SOURCE_LABELS.get(key, key or "未知信息源")


def _archive_public_source_type(source_type: Any) -> str:
    key = str(source_type or "").strip()
    return _ARCHIVE_SOURCE_TYPE_ALIASES.get(key, key)


def _archive_field_label(field: Any) -> str:
    key = str(field or "").strip()
    return _ARCHIVE_FIELD_LABELS.get(key, key or "未知字段")


def _auth_server_base() -> str:
    return ((getattr(settings, "auth_server_base", None) or os.environ.get("AUTH_SERVER_BASE") or "").strip().rstrip("/"))


def _request_header(ctx: Any, name: str) -> str:
    headers = getattr(ctx, "headers", {}) if ctx is not None else {}
    try:
        return str(headers.get(name) or headers.get(name.lower()) or "").strip()
    except Exception:
        return ""


def _archive_source_catalog(evidence: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    roles = {
        "alibaba_inquiry": "用于提取买家、公司、联系方式、采购意图和历史沟通上下文。",
        "alibaba_profile": "用于补充阿里页面展示的客户画像、注册/活动信息、偏好标签等。",
        "official_website": "用于核验公司主体、主营业务、联系方式、品牌/产品描述和站点可信度。",
        "web_search": "用于搜索公司、域名、邮箱、联系人、工商/备案、进口商等公开资料。",
        "company_registry": "用于核验企业注册名称、注册号、辖区、状态和注册地址，作为公司主体结论的核心证据。",
        "professional_network_company": "用于调研公司主页、简介、行业、规模和员工动态，辅助判断 B2B 主体可信度。",
        "short_video_account_search": "用于发现海外短视频侧的品牌、渠道和用户公开资料。",
        "short_video_content_search": "用于发现产品词相关内容和市场表达。",
        "commerce_product_search": "用于判断客户是否可能与零售、分销或货盘需求相关。",
        "local_video_account_search": "用于中文市场主体和视频号账号公开资料核验。",
        "local_video_content_search": "用于中文市场视频内容公开资料核验。",
        "visual_social_search": "用于搜索品牌、买家、产品在图片社媒上的公开痕迹。",
        "public_discussion_search": "用于搜索企业、买家、产品相关公开讨论、投诉、新闻和品牌动态。",
    }
    used = _archive_used_sources(evidence or [])
    catalog = []
    for item in used:
        st = str(item.get("source_type") or "")
        catalog.append(
            {
                "source_type": st,
                "title": _archive_source_label(st),
                "category": "实际使用",
                "role": roles.get(st, "本次客户档案补全实际引用的信息源。"),
                "used": True,
            }
        )
    return catalog


def _archive_used_sources(evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        st = _archive_public_source_type(item.get("source_type") or "web_search")
        if st in {"research_plan", "source_inventory"}:
            continue
        row = grouped.setdefault(
            st,
            {
                "source_type": st,
                "title": _archive_source_label(st),
                "count": 0,
                "urls": [],
                "fields": [],
            },
        )
        row["count"] += 1
        if item.get("url"):
            row["urls"] = _dedupe_strings(list(row.get("urls") or []) + [str(item.get("url") or "")], 5)
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        if raw.get("field"):
            row["fields"] = _dedupe_strings(list(row.get("fields") or []) + [_archive_field_label(raw.get("field"))], 8)
    return sorted(grouped.values(), key=lambda x: (-int(x.get("count") or 0), str(x.get("title") or "")))


def _clean_product_keyword(value: Any, seed: Dict[str, Any]) -> str:
    text = _entity_text(value, 120)
    text = re.sub(r"^(?:行业|产品|最近询盘产品|最常采购行业)\s*[:：-]?\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return ""
    low = text.lower()
    hard_noise = (
        "inquiry from",
        "thank you",
        "thanks for",
        "your interest",
        "catalog",
        "product catalog",
        "alibaba.com live",
        "livestream",
        "the content",
        "rae ma",
        "tm 商机",
        "加入直播",
        "回放",
        "客户隐藏",
        "更新时间",
        "创建时间",
    )
    if any(x in low for x in hard_noise):
        return ""
    if low in {"alibaba", "live", "tm", "ma", "mike", "seller", "buyer", "sample"}:
        return ""
    text = re.sub(r"\s+(?:for|of|with|and|or)$", "", text, flags=re.IGNORECASE).strip()
    if re.search(r"(询价|询盘|浏览数|登录天数|垃圾询盘|有效rfq|订单总数|订单总金额|交易供应商)", text, re.IGNORECASE):
        return ""
    company = _entity_text(seed.get("company_name") or "", 160).lower()
    buyer = _entity_text(seed.get("buyer_name") or "", 160).lower()
    if low and (low == company or low == buyer):
        return ""
    company_tokens = set(_company_tokens(company))
    text_tokens = set(_company_tokens(text))
    if company_tokens and text_tokens and len(text_tokens - company_tokens) == 0:
        return ""
    # Buyer names or owner names sometimes appear as capitalized phrases; they are not product signals.
    buyer_tokens = set(_company_tokens(buyer))
    if buyer_tokens and text_tokens and len(text_tokens - buyer_tokens) == 0:
        return ""
    if len(text) < 3 or len(text) > 90:
        return ""
    return text


def _seed_product_keywords(seed: Dict[str, Any], limit: int = 8) -> List[str]:
    raw_parts = [
        seed.get("title") or "",
        seed.get("messages_text") or "",
        json.dumps(seed.get("alibaba_profile") or {}, ensure_ascii=False) if isinstance(seed.get("alibaba_profile"), dict) else "",
    ]
    raw_blob = html_unescape("\n".join(str(x or "") for x in raw_parts))
    text = _entity_text(raw_blob, 20000)
    candidates: List[str] = []
    for match in re.findall(r"回放\s*([^\r\n]{4,180}?)(?:加入直播|$)", raw_blob, flags=re.IGNORECASE):
        cleaned = _clean_product_keyword(match, seed)
        if cleaned:
            candidates.append(cleaned)
    product_line_re = re.compile(r"\b(?:rugged|barcode|scanner|pda|tablet|terminal|handheld|android|waterproof|smartphone|phone|nfc|inventory)\b", re.IGNORECASE)
    for line in re.split(r"[\r\n|]+", raw_blob):
        line = line.strip()
        if product_line_re.search(line):
            cleaned = _clean_product_keyword(line, seed)
            if cleaned:
                candidates.append(cleaned)
    for match in re.findall(r"最常采购行业\s+([\s\S]{0,260}?)(?:累计线上交易|最近询盘产品|客户行为数据|$)", text):
        for part in re.split(r"[|\n,，;；]+", match):
            cleaned = _clean_product_keyword(part, seed)
            if cleaned:
                candidates.append(cleaned)
    for pattern in (
        r"(?:looking for|need|want|interested in|buy|purchase|quote for|quotation for)\s+([A-Za-z0-9][A-Za-z0-9 /&+\-]{2,60})",
        r"(?:产品|采购|询价|报价|需要|想要|寻找)[:： ]*([\u4e00-\u9fa5A-Za-z0-9 /&+\-]{2,40})",
    ):
        for hit in re.findall(pattern, text, flags=re.IGNORECASE):
            cleaned = _clean_product_keyword(re.sub(r"[\s,.;!?，。；！？]+$", "", str(hit or "").strip()), seed)
            if cleaned:
                candidates.append(cleaned)
    for item in re.findall(r"\b[A-Z][A-Za-z0-9+\-/]{2,}(?:\s+[A-Za-z0-9+\-/]{2,}){0,3}\b", text):
        cleaned = _clean_product_keyword(item, seed)
        if cleaned:
            candidates.append(cleaned)
    return _dedupe_strings(candidates, limit)


def _search_entity_variants(value: Any, limit: int = 4) -> List[str]:
    text = _entity_text(value, 180)
    if not text:
        return []
    variants = [
        text,
        re.sub(r"\s*&\s*", " ", text),
        re.sub(r"\s*&\s*", " and ", text),
        re.sub(r"\b(?:ltd|llc|inc|corp|company|limited|sarl|sa|co)\b\.?", "", text, flags=re.IGNORECASE),
    ]
    out: List[str] = []
    for item in variants:
        item = re.sub(r"[^A-Za-z0-9\u4e00-\u9fa5&+.\- ]+", " ", item)
        item = re.sub(r"\s+", " ", item).strip(" -")
        if item:
            out.append(item)
    return _dedupe_strings(out, limit)


def _archive_research_plan(seed: Dict[str, Any]) -> List[Dict[str, Any]]:
    product_keywords = _seed_product_keywords(seed, 6)
    plan: List[Dict[str, Any]] = []
    fields = [
        ("company_name", seed.get("company_name"), "调研公司主体、官网、行业、主营产品、职业社媒、电商痕迹、工商/备案/进口商线索。"),
        ("domain", seed.get("domain"), "调研官网首页、关于我们、联系方式、邮箱域名一致性、站点可信度和社媒外链。"),
        ("email", seed.get("email"), "核验邮箱域名与公司主体是否一致，搜索邮箱公开出现位置，避免把私人邮箱误判为公司主体。"),
        ("buyer_name", seed.get("buyer_name"), "结合国家/公司搜索买家公开职业、社媒和历史询盘上下文，只做低风险辅助线索。"),
        ("country", seed.get("country"), "结合区域判断市场语言、平台优先级、进口/分销可能性和需人工核验的合规风险。"),
    ]
    for field, value, strategy in fields:
        if value:
            plan.append(
                {
                    "field": field,
                    "label": _archive_field_label(field),
                    "value": str(value),
                    "strategy": strategy,
                    "candidate_sources": ["阿里询盘", "官网", "公开网页搜索", "社媒/电商公开资料"],
                }
            )
    if product_keywords:
        plan.append(
            {
                "field": "product_keywords",
                "label": "产品关键词",
                "value": "、".join(product_keywords),
                "strategy": "围绕产品词调研行业、竞品、短视频内容、电商商品线索、客户采购意图和可能的销售路径。",
                "candidate_sources": ["阿里询盘消息", "公开网页搜索", "社媒/电商公开资料"],
            }
        )
    return plan


def _archive_research_plan_evidence(seed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    plan = _archive_research_plan(seed)
    if not plan:
        return None
    lines = [f"{idx + 1}. {item['label']}：{item['strategy']}" for idx, item in enumerate(plan)]
    return {
        "source_type": "research_plan",
        "title": "客户档案深度调研计划",
        "url": "",
        "snippet": "\n".join(lines),
        "confidence": "A",
        "raw": {"plan": plan},
    }


def _archive_public_search_queries(seed: Dict[str, Any]) -> List[Dict[str, str]]:
    company = _entity_text(seed.get("company_name") or "", 160)
    domain = _entity_text(seed.get("domain") or "", 180)
    email = _entity_text(seed.get("email") or "", 180)
    buyer = _entity_text(seed.get("buyer_name") or "", 160)
    country = _entity_text(seed.get("country") or "", 80)
    products = _seed_product_keywords(seed, 4)
    scope = str(seed.get("market_scope") or "").upper()
    rows: List[Dict[str, str]] = []

    def add(field: str, query: str, purpose: str) -> None:
        query = query.strip()
        if query:
            rows.append({"field": field, "query": query, "purpose": purpose})

    company_variants = _search_entity_variants(company, 4)
    primary_company = company_variants[0] if company_variants else company
    if primary_company:
        add("company_name", primary_company, "搜索公司主体公开资料，优先发现官网")
        if country:
            add("company_name", f"{primary_company} {country}", "结合国家/地区核验公司主体")
            for country_term in [x for x in _country_aliases(country) if x and not x.startswith(".")][:2]:
                add("company_name", f"{primary_company} {country_term}", "结合国家/地区核验公司主体")
        add("company_name", f"{primary_company} official website contact", "核验公司官网、主营业务和联系方式")
        add("company_name", f"{primary_company} company profile", "查找公司公开资料和主体描述")
        if len(company_variants) > 1:
            add("company_name", company_variants[1], "使用简化公司名补充搜索")
        add("company_name", f"{primary_company} professional profile company", "查找职业社媒公司主页和员工/动态")
        add("company_name", f"{primary_company} importer distributor supplier", "判断是否是进口商、分销商或供应商")
        if scope == "CN":
            add("company_name", f"{primary_company} 工商 企业信用 备案", "核验工商、企业信用、备案等中文公开信息")
    if domain:
        host = _domain_from_url(domain)
        add("domain", f"site:{host or domain} about contact products", "深挖官网关于我们、联系方式和产品页")
        add("domain", f"\"{host or domain}\" company reviews complaints social", "搜索域名对应口碑、投诉和社媒痕迹")
    if email:
        add("email", f"\"{email}\"", "搜索邮箱公开出现位置")
        mail_domain = _domain_from_email(email)
        if mail_domain:
            add("email", f"\"{mail_domain}\" company contact", "核验邮箱域名和公司主体关系")
    if buyer:
        buyer_q = _search_entity_variants(buyer, 1)[0] if _search_entity_variants(buyer, 1) else buyer
        add("buyer_name", f"{buyer_q} {primary_company or country}", "结合公司/国家查找买家公开身份线索")
    for keyword in products:
        clean_keyword = _clean_product_keyword(keyword, seed)
        if not clean_keyword or not primary_company:
            continue
        add("product_keywords", f"{clean_keyword} {primary_company}", "围绕产品词核验公司/买家业务相关性")
    seen = set()
    out: List[Dict[str, str]] = []
    for row in rows:
        key = row["query"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= 12:
            break
    return out


def _rough_result_count(data: Any) -> int:
    if isinstance(data, list):
        return len(data)
    if not isinstance(data, dict):
        return 0
    for key in ("data", "items", "list", "results", "users", "videos", "products", "aweme_list", "user_list"):
        value = data.get(key)
        if isinstance(value, list):
            return len(value)
        if isinstance(value, dict):
            count = _rough_result_count(value)
            if count:
                return count
    total = data.get("total") or data.get("count")
    try:
        return int(total)
    except Exception:
        return 1 if data else 0


def _remote_signal_snippet(data: Any, limit: int = 1400) -> str:
    rows: List[str] = []

    def walk(value: Any, depth: int = 0) -> None:
        if len(rows) >= 8 or depth > 4:
            return
        if isinstance(value, list):
            for item in value[:8]:
                walk(item, depth + 1)
                if len(rows) >= 8:
                    break
            return
        if not isinstance(value, dict):
            return
        title = _first_text(
            value.get("title"),
            value.get("name"),
            value.get("nickname"),
            value.get("unique_id"),
            value.get("username"),
            value.get("companyName"),
            value.get("company_name"),
            value.get("desc"),
            value.get("description"),
            value.get("content"),
            limit=120,
        )
        desc = _first_text(value.get("signature"), value.get("summary"), value.get("desc"), value.get("description"), value.get("content"), limit=180)
        if title:
            rows.append(title + (f"：{desc}" if desc and desc != title else ""))
        for key in ("data", "items", "list", "results", "users", "videos", "products", "company", "companies", "user_list"):
            if key in value:
                walk(value.get(key), depth + 1)
                if len(rows) >= 8:
                    break

    walk(data)
    if rows:
        return _compact("\n".join(_dedupe_strings(rows, 8)), limit)
    return _compact(json.dumps(data, ensure_ascii=False), limit)


async def _collect_remote_public_signals(seed: Dict[str, Any], max_results: int, request_ctx: Any = None) -> List[Dict[str, Any]]:
    company = str(seed.get("company_name") or "").strip()
    buyer = str(seed.get("buyer_name") or "").strip()
    country = str(seed.get("country") or "").strip()
    domain = str(seed.get("domain") or "").strip()
    email = str(seed.get("email") or "").strip()
    phone = str(seed.get("phone") or "").strip()
    messages_text = str(seed.get("messages_text") or seed.get("messages") or "").strip()
    products = _seed_product_keywords(seed, 3)
    keyword = _first_text(company, products[0] if products else "", buyer, limit=80)
    if not keyword and not domain and not email:
        return []
    base = _auth_server_base()
    auth = _request_header(request_ctx, "Authorization")
    if not base or not auth:
        return []
    headers = {"Authorization": auth, "Content-Type": "application/json", "Accept": "application/json"}
    xi = _request_header(request_ctx, "X-Installation-Id")
    if xi:
        headers["X-Installation-Id"] = xi
    body = {
        "company_name": company,
        "buyer_name": buyer,
        "country": country,
        "domain": domain,
        "email": email,
        "phone": phone,
        "messages_text": messages_text[:4000],
        "product_keywords": products,
        "market_scope": str(seed.get("market_scope") or ""),
        "max_results": max(1, min(12, int(max_results or 6))),
    }

    async def request_remote(path: str) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=70.0, trust_env=False) as client:
                resp = await client.post(f"{base}{path}", json=body, headers=headers)
            if resp.status_code >= 400:
                logger.info("[ALIBABA-INQUIRY] remote research skipped path=%s status=%s body=%s", path, resp.status_code, (resp.text or "")[:260])
                return {}
            return resp.json() if resp.content else {}
        except Exception as exc:
            logger.info("[ALIBABA-INQUIRY] remote research skipped path=%s err=%s", path, exc)
            return {}

    def append_items(payload: Dict[str, Any], default_confidence: str = "B") -> None:
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            source_type = str(item.get("source_type") or "").strip()
            snippet = _compact(item.get("snippet") or _remote_signal_snippet(item.get("raw") or {}), 1600)
            if not source_type or not snippet:
                continue
            raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
            raw = dict(raw)
            raw.update(
                {
                    "field": raw.get("field") or item.get("field") or "",
                    "result_count": raw.get("result_count") or item.get("result_count") or 0,
                    "body_fetched": bool(raw.get("body_fetched")),
                }
            )
            out.append(
                {
                    "source_type": source_type,
                    "source_label": _archive_source_label(source_type),
                    "title": _compact(item.get("title") or f"{_archive_source_label(source_type)}：{_archive_field_label(item.get('field') or raw.get('field'))}", 260),
                    "url": str(item.get("url") or ""),
                    "snippet": snippet,
                    "confidence": str(item.get("confidence") or default_confidence)[:16],
                    "raw": raw,
                }
            )

    try:
        out: List[Dict[str, Any]] = []
        first_priority = await request_remote("/api/alibaba-customer-research/evidence")
        append_items(first_priority, "A")
        for gap in first_priority.get("required_resources") or []:
            if str(gap or "").strip():
                out.append(
                    {
                        "source_type": "source_inventory",
                        "source_label": _archive_source_label("source_inventory"),
                        "title": "本次档案补全资源状态",
                        "url": "",
                        "snippet": _compact(gap, 360),
                        "confidence": "A",
                        "raw": {"required_resource": True},
                    }
                )
        public_signals = await request_remote("/api/alibaba-customer-research/public-signals")
        append_items(public_signals, "B")
        return out
    except Exception as exc:
        logger.info("[ALIBABA-INQUIRY] remote public signal skipped: %s", exc)
        return []


def _archive_for_inquiry(db: Session, account_id: int, inquiry_id: str) -> Optional[AlibabaCustomerArchive]:
    inquiry_id = str(inquiry_id or "").strip()
    if not inquiry_id:
        return None
    row = (
        db.query(AlibabaCustomerArchive)
        .filter(AlibabaCustomerArchive.account_id == account_id, AlibabaCustomerArchive.inquiry_id == inquiry_id)
        .first()
    )
    if row:
        return row
    rows = db.query(AlibabaCustomerArchive).filter(AlibabaCustomerArchive.account_id == account_id).all()
    for item in rows:
        if inquiry_id in _linked_inquiry_ids(item):
            return item
    return None


def _archive_field_evidence(seed: Dict[str, Any], evidence: List[Dict[str, Any]], profile: Dict[str, Any]) -> Dict[str, Any]:
    basics = profile.get("basics") if isinstance(profile.get("basics"), dict) else {}
    notes = profile.get("field_evidence") if isinstance(profile.get("field_evidence"), dict) else {}
    out: Dict[str, Any] = {}
    seed_source = "alibaba_inquiry"
    for field in ("company_name", "buyer_name", "country", "domain", "email", "phone"):
        value = _first_text(basics.get(field), seed.get(field))
        if not value:
            continue
        out[field] = {
            "value": value,
            "confidence": "B" if field in {"email", "phone"} else "C",
            "sources": [seed_source],
            "note": "来自阿里询盘原始线索，回复客户前需要结合证据链核验。",
        }
    for field, item in notes.items():
        if isinstance(item, dict):
            merged = out.get(field, {})
            merged.update(item)
            out[field] = merged
    for item in evidence or []:
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        field = str(raw.get("field") or "").strip()
        if field and field in out:
            out[field].setdefault("sources", [])
            out[field]["sources"] = _dedupe_strings(
                list(out[field].get("sources") or []) + [_archive_public_source_type(item.get("source_type") or "")],
                8,
            )
            if item.get("snippet"):
                out[field].setdefault("source_notes", [])
                out[field]["source_notes"] = _dedupe_strings(
                    list(out[field].get("source_notes") or [])
                    + [f"{_archive_source_label(item.get('source_type'))}：{_compact(item.get('snippet') or '', 180)}"],
                    4,
                )
    source_types = [_archive_public_source_type(x.get("source_type") or "") for x in evidence if x.get("source_type")]
    if source_types:
        for field in ("company_name", "domain"):
            if field in out:
                out[field].setdefault("sources", [])
                out[field]["sources"] = _dedupe_strings(list(out[field]["sources"]) + source_types[:3], 6)
    return out


def _merge_archive_evidence(existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]], limit: int = 80) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for item in list(existing or []) + list(incoming or []):
        if not isinstance(item, dict):
            continue
        key = (str(item.get("url") or "") or str(item.get("title") or "") or str(item.get("snippet") or "")[:120]).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _seed_missing_items(seed: Dict[str, Any]) -> List[str]:
    items = []
    if not seed.get("company_name") and not seed.get("domain"):
        items.append("缺少可核验公司名或官网域名，只能先按买家/邮箱做低置信档案。")
    if not seed.get("email") and not seed.get("phone"):
        items.append("缺少可触达联系方式，联系人置信度需要人工补充。")
    if not seed.get("messages"):
        items.append("本地还没有完整询盘消息，建议先同步询盘详情。")
    return items


def _inquiry_evidence(seed: Dict[str, Any]) -> List[Dict[str, Any]]:
    evidence: List[Dict[str, Any]] = []
    profile = seed.get("alibaba_profile") if isinstance(seed.get("alibaba_profile"), dict) else {}
    evidence.append(
        {
            "source_type": "alibaba_inquiry",
            "title": f"阿里询盘 {seed.get('inquiry_id') or ''}",
            "url": seed.get("source_url") or "",
            "snippet": seed.get("messages_text") or seed.get("title") or "",
            "confidence": "A",
            "raw": {"seed": seed},
        }
    )
    if profile:
        evidence.append(
            {
                "source_type": "alibaba_profile",
                "title": "阿里询盘右侧买家属性",
                "url": seed.get("source_url") or "",
                "snippet": _compact(profile.get("raw_text") or json.dumps(profile, ensure_ascii=False), 1600),
                "confidence": "A",
                "raw": profile,
            }
        )
    return evidence


async def _fetch_domain_home(domain: str) -> Optional[Dict[str, Any]]:
    pages = await _fetch_domain_pages(domain)
    return pages[0] if pages else None


def _website_page_kind(url: str, text: str = "") -> str:
    value = f"{url} {text}".lower()
    if re.search(r"about|company|profile|who-we-are|a-propos|propos|qui-sommes|关于|简介", value):
        return "关于我们"
    if re.search(r"contact|support|enquiry|inquiry|contacts|联系我们|联系", value):
        return "联系方式"
    if re.search(r"product|service|solution|catalog|shop|metier|m[eé]tier|services|produit|产品|服务|方案", value):
        return "产品/服务"
    return "官网首页"


def _extract_official_page_links(base_url: str, html: str, limit: int = 4) -> List[str]:
    links: List[str] = []
    for href, label in re.findall(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>([\s\S]*?)</a>", html or "", flags=re.IGNORECASE):
        text = _strip_html(label)
        kind = _website_page_kind(href, text)
        if kind == "官网首页":
            continue
        url = urljoin(base_url, href)
        if not re.match(r"^https?://", url, re.IGNORECASE):
            continue
        if _domain_from_url(url) != _domain_from_url(base_url):
            continue
        links.append(url.split("#", 1)[0])
    return _dedupe_strings(links, limit)


async def _fetch_domain_pages(domain: str, max_pages: int = 4) -> List[Dict[str, Any]]:
    host = _domain_from_url(domain)
    if not host:
        return []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.8,zh-CN;q=0.7",
    }
    out: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, trust_env=True, headers=headers) as client:
        home_html = ""
        home_url = ""
        for scheme in ("https", "http"):
            url = f"{scheme}://{host}"
            try:
                resp = await client.get(url)
                if resp.status_code >= 400:
                    continue
                text = resp.text or ""
                home_html = text
                home_url = str(resp.url)
                title_match = re.search(r"<title[^>]*>([\s\S]*?)</title>", text, re.IGNORECASE)
                title = _strip_html(title_match.group(1)) if title_match else host
                snippet = _strip_html(text[:30000])
                if snippet:
                    out.append(
                        {
                            "source_type": "official_website",
                            "title": f"官方公开网站 · {_website_page_kind(str(resp.url), title)} · {title or host}",
                            "url": str(resp.url),
                            "snippet": snippet,
                            "confidence": "B",
                            "raw": {"status_code": resp.status_code, "final_url": str(resp.url), "field": "domain", "page_kind": "官网首页"},
                        }
                    )
                break
            except Exception as exc:
                logger.debug("[ALIBABA-INQUIRY] domain fetch failed domain=%s err=%s", host, exc)
        if home_html and home_url:
            for link in _extract_official_page_links(home_url, home_html, max(0, max_pages - len(out))):
                if len(out) >= max_pages:
                    break
                try:
                    resp = await client.get(link)
                    if resp.status_code >= 400:
                        continue
                    text = resp.text or ""
                    title_match = re.search(r"<title[^>]*>([\s\S]*?)</title>", text, re.IGNORECASE)
                    title = _strip_html(title_match.group(1)) if title_match else _website_page_kind(str(resp.url))
                    snippet = _strip_html(text[:30000])
                    if not snippet:
                        continue
                    kind = _website_page_kind(str(resp.url), title)
                    out.append(
                        {
                            "source_type": "official_website",
                            "title": f"官方公开网站 · {kind} · {title or host}",
                            "url": str(resp.url),
                            "snippet": snippet,
                            "confidence": "B",
                            "raw": {"status_code": resp.status_code, "final_url": str(resp.url), "field": "domain", "page_kind": kind},
                        }
                    )
                except Exception as exc:
                    logger.debug("[ALIBABA-INQUIRY] domain subpage fetch failed url=%s err=%s", link, exc)
    return out


async def _search_serper(query: str, max_results: int) -> List[Dict[str, Any]]:
    key = (os.environ.get("SERPER_API_KEY") or os.environ.get("GOOGLE_SERPER_API_KEY") or "").strip()
    if not key:
        return []
    headers = {"X-API-KEY": key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=18.0, trust_env=False) as client:
        resp = await client.post("https://google.serper.dev/search", json={"q": query, "num": max_results}, headers=headers)
    if resp.status_code >= 400:
        raise RuntimeError(f"Search HTTP {resp.status_code}: {(resp.text or '')[:300]}")
    data = resp.json() if resp.content else {}
    rows = []
    for item in (data.get("organic") or [])[:max_results]:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "source_type": "web_search",
                "title": item.get("title") or "",
                "url": item.get("link") or "",
                "snippet": item.get("snippet") or "",
                "confidence": "B",
                "raw": {"query": query},
            }
        )
    return rows


async def _search_tavily(query: str, max_results: int) -> List[Dict[str, Any]]:
    key = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if not key:
        return []
    payload = {"api_key": key, "query": query, "max_results": max_results, "include_answer": False}
    async with httpx.AsyncClient(timeout=18.0, trust_env=False) as client:
        resp = await client.post("https://api.tavily.com/search", json=payload)
    if resp.status_code >= 400:
        raise RuntimeError(f"Search HTTP {resp.status_code}: {(resp.text or '')[:300]}")
    data = resp.json() if resp.content else {}
    rows = []
    for item in (data.get("results") or [])[:max_results]:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "source_type": "web_search",
                "title": item.get("title") or "",
                "url": item.get("url") or "",
                "snippet": item.get("content") or "",
                "confidence": "B",
                "raw": {"query": query},
            }
        )
    return rows


def _duckduckgo_result_url(value: str) -> str:
    raw = str(value or "").replace("&amp;", "&").strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    try:
        parsed = urlparse(raw)
        qs = parse_qs(parsed.query or "")
        if qs.get("uddg"):
            return unquote(qs["uddg"][0])
    except Exception:
        pass
    return raw


async def _search_duckduckgo_html(query: str, max_results: int) -> List[Dict[str, Any]]:
    if max_results <= 0:
        return []
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.8,zh-CN;q=0.7",
    }
    async with httpx.AsyncClient(timeout=18.0, follow_redirects=True, trust_env=False, headers=headers) as client:
        resp = await client.get(url)
    if resp.status_code >= 400:
        raise RuntimeError(f"Search HTTP {resp.status_code}: {(resp.text or '')[:300]}")
    html = resp.text or ""
    rows: List[Dict[str, Any]] = []
    for link in re.finditer(r"<a[^>]+class=\"result__a\"[^>]+href=\"([^\"]+)\"[^>]*>([\s\S]*?)</a>", html, flags=re.IGNORECASE):
        start = max(0, link.start() - 1200)
        end = min(len(html), link.end() + 1800)
        block = html[start:end]
        snippet = ""
        sm = re.search(r"class=\"result__snippet\"[^>]*>([\s\S]*?)</a>", block, flags=re.IGNORECASE) or re.search(r"class=\"result__snippet\"[^>]*>([\s\S]*?)</div>", block, flags=re.IGNORECASE)
        if sm:
            snippet = _strip_html(sm.group(1))
        rows.append(
            {
                "source_type": "web_search",
                "title": _strip_html(link.group(2)),
                "url": _duckduckgo_result_url(link.group(1)),
                "snippet": snippet,
                "confidence": "B",
                "raw": {"query": query},
            }
        )
        if len(rows) >= max_results:
            break
    return rows


def _yahoo_result_url(value: str) -> str:
    raw = html_unescape(str(value or "")).strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    try:
        parsed = urlparse(raw)
        if parsed.netloc.lower().endswith("search.yahoo.com"):
            m = re.search(r"/RU=([^/]+)", parsed.path)
            if m:
                return unquote(m.group(1))
            qs = parse_qs(parsed.query or "")
            for key in ("RU", "u"):
                if qs.get(key):
                    return unquote(qs[key][0])
    except Exception:
        pass
    return raw


async def _search_yahoo_html(query: str, max_results: int) -> List[Dict[str, Any]]:
    if max_results <= 0:
        return []
    url = f"https://search.yahoo.com/search?p={quote_plus(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.8,zh-CN;q=0.7",
    }
    async with httpx.AsyncClient(timeout=22.0, follow_redirects=True, trust_env=True, headers=headers) as client:
        resp = await client.get(url)
    if resp.status_code >= 400:
        raise RuntimeError(f"Search HTTP {resp.status_code}: {(resp.text or '')[:300]}")
    html = resp.text or ""
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for match in re.finditer(r"<a\b([^>]*)>([\s\S]*?)</a>", html, flags=re.IGNORECASE):
        attrs = match.group(1) or ""
        body = match.group(2) or ""
        if 'data-matarget="algo"' not in attrs and "<h3" not in body.lower():
            continue
        href_match = re.search(r"href=[\"']([^\"']+)[\"']", attrs, flags=re.IGNORECASE)
        if not href_match:
            continue
        result_url = _yahoo_result_url(href_match.group(1))
        if not re.match(r"^https?://", result_url, re.IGNORECASE):
            continue
        host = _domain_from_url(result_url)
        if not host or host.endswith("yahoo.com"):
            continue
        title_match = re.search(r"<h3[^>]*>([\s\S]*?)</h3>", body, flags=re.IGNORECASE)
        title = _strip_html(title_match.group(1) if title_match else body)
        title = re.sub(r"^[A-Z][A-Za-z]+https?://\S+\s*", "", title).strip()
        if not title:
            continue
        li_end = html.find("</li>", match.end())
        block = html[match.start() : (li_end + 5 if li_end > match.end() else min(len(html), match.end() + 1800))]
        snippet = ""
        snippet_match = re.search(r"<p[^>]*>([\s\S]*?)</p>", block, flags=re.IGNORECASE)
        if snippet_match:
            snippet = _strip_html(snippet_match.group(1))
        key = result_url.lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "source_type": "web_search",
                "title": title,
                "url": result_url,
                "snippet": snippet,
                "confidence": "B",
                "raw": {"query": query},
            }
        )
        if len(rows) >= max_results:
            break
    return rows


async def _search_bing_html(query: str, max_results: int) -> List[Dict[str, Any]]:
    if max_results <= 0:
        return []
    url = f"https://www.bing.com/search?q={quote_plus(query)}&count={max(1, min(10, max_results))}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.8,zh-CN;q=0.7",
    }
    async with httpx.AsyncClient(timeout=18.0, follow_redirects=True, trust_env=False, headers=headers) as client:
        resp = await client.get(url)
    if resp.status_code >= 400:
        raise RuntimeError(f"Search HTTP {resp.status_code}: {(resp.text or '')[:300]}")
    html = resp.text or ""
    rows = []
    for block in re.findall(r"<li class=\"b_algo\"[\s\S]*?</li>", html, flags=re.IGNORECASE)[:max_results]:
        link = re.search(r"<a[^>]+href=\"([^\"]+)\"[^>]*>([\s\S]*?)</a>", block, flags=re.IGNORECASE)
        if not link:
            continue
        snippet = ""
        pm = re.search(r"<p[^>]*>([\s\S]*?)</p>", block, flags=re.IGNORECASE)
        if pm:
            snippet = _strip_html(pm.group(1))
        rows.append(
            {
                "source_type": "web_search",
                "title": _strip_html(link.group(2)),
                "url": link.group(1),
                "snippet": snippet,
                "confidence": "C",
                "raw": {"query": query},
            }
        )
    return rows


async def _search_public_web(query: str, max_results: int) -> List[Dict[str, Any]]:
    if not query.strip() or max_results <= 0:
        return []
    for fn in (_search_yahoo_html, _search_duckduckgo_html, _search_bing_html, _search_serper, _search_tavily):
        try:
            rows = await fn(query, max_results)
            if rows:
                return rows
        except Exception as exc:
            logger.info("[ALIBABA-INQUIRY] web search provider failed query=%s err=%s", query[:120], exc)
    return []


_NON_OFFICIAL_DOMAINS = {
    "alibaba.com",
    "made-in-china.com",
    "globalsources.com",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "tiktok.com",
    "google.com",
    "bing.com",
    "yahoo.com",
    "yelp.com",
    "yellowpages.com",
    "opencorporates.com",
    "dnb.com",
    "emis.com",
    "zoominfo.com",
    "crunchbase.com",
    "apollo.io",
    "signalhire.com",
    "rocketreach.co",
    "craft.co",
    "tracxn.com",
    "cbinsights.com",
    "importgenius.com",
    "panjiva.com",
    "volza.com",
    "seair.co.in",
    "exportgenius.in",
    "listofcompaniesin.com",
    "companylist.org",
    "businesslistings.net",
}


def _country_aliases(value: Any) -> List[str]:
    country = _entity_text(value, 80).lower()
    aliases = {
        "巴西": ["brazil", "brasil", ".br"],
        "刚果（布）": ["congo", "pointe-noire", "brazzaville", ".cg"],
        "刚果": ["congo", "pointe-noire", "brazzaville", ".cg"],
        "美国": ["united states", "usa", "u.s.", ".us"],
        "印度": ["india", ".in"],
        "新西兰": ["new zealand", ".nz"],
        "加拿大": ["canada", ".ca"],
        "尼日利亚": ["nigeria", ".ng"],
        "墨西哥": ["mexico", ".mx"],
        "埃及": ["egypt", ".eg"],
        "加纳": ["ghana", ".gh"],
        "肯尼亚": ["kenya", ".ke"],
    }
    out: List[str] = []
    for key, values in aliases.items():
        if key.lower() in country:
            out.extend(values)
    if country:
        out.append(country)
    return _dedupe_strings(out, 8)


def _company_tokens(value: Any) -> List[str]:
    text = _entity_text(value, 255)
    text = re.sub(r"[^A-Za-z0-9\u4e00-\u9fa5]+", " ", text).lower()
    stop = {"ltd", "llc", "inc", "co", "corp", "company", "limited", "sa", "sarl", "the", "and", "group", "trading"}
    return [x for x in text.split() if len(x) >= 3 and x not in stop][:8]


def _is_non_official_domain(host: str) -> bool:
    host = (host or "").lower().lstrip("www.")
    return any(host == item or host.endswith("." + item) for item in _NON_OFFICIAL_DOMAINS)


def _official_candidate_score(seed: Dict[str, Any], row: Dict[str, Any]) -> int:
    url = str(row.get("url") or "")
    host = _domain_from_url(url)
    if not host or _is_non_official_domain(host):
        return -100
    title = str(row.get("title") or "")
    snippet = str(row.get("snippet") or "")
    hay = f"{host} {title} {snippet}".lower()
    tokens = _company_tokens(seed.get("company_name"))
    country_hits = _country_aliases(seed.get("country"))
    has_country_hit = bool(country_hits and any(alias and alias in hay for alias in country_hits))
    score = 0
    for token in tokens:
        if token in hay:
            score += 18
        if token in host:
            score += 22
    if re.search(r"\b(official|home|about|contact|products|services|solutions)\b", hay, re.IGNORECASE):
        score += 12
    if has_country_hit:
        score += 12
    if re.search(r"/(about|company|contact|products|services|solutions)", url, re.IGNORECASE):
        score += 8
    if re.search(r"directory|company-profile|profile|reviews|supplier|manufacturer|yellow|business-listing|companies/", url, re.IGNORECASE):
        score -= 35
    if host and not any(token in host.lower() for token in tokens):
        score -= 10
    if len(tokens) <= 1 and seed.get("country") and not has_country_hit:
        score -= 55
    return score


def _token_match_count(tokens: List[str], hay: str) -> int:
    value = hay.lower()
    return sum(1 for token in tokens if token and token in value)


def _public_search_row_relevant(seed: Dict[str, Any], row: Dict[str, Any], field: str) -> bool:
    url = str(row.get("url") or "")
    if re.search(r"/aclick|/ads?|doubleclick|googleadservices", url, re.IGNORECASE):
        return False
    hay = f"{_domain_from_url(url)} {row.get('title') or ''} {row.get('snippet') or ''}".lower()
    company_tokens = _company_tokens(seed.get("company_name"))
    buyer_tokens = _company_tokens(seed.get("buyer_name"))
    country_hits = _country_aliases(seed.get("country"))
    has_country_hit = bool(country_hits and any(alias and alias in hay for alias in country_hits))
    company_need = 2 if len(company_tokens) >= 2 else 1
    company_hits = _token_match_count(company_tokens, hay)
    if field == "company_name":
        if len(company_tokens) <= 1 and seed.get("country") and not has_country_hit:
            return False
        return not company_tokens or company_hits >= company_need
    if field == "buyer_name":
        if not buyer_tokens:
            return False
        buyer_phrase = _entity_text(seed.get("buyer_name") or "", 160).lower()
        if buyer_phrase and buyer_phrase in hay:
            return True
        return len(buyer_tokens) == 1 and _token_match_count(buyer_tokens, hay) >= 1
    if field == "product_keywords":
        if len(company_tokens) <= 1 and seed.get("country") and not has_country_hit:
            return False
        return bool(company_tokens and company_hits >= company_need)
    return True


async def _discover_official_pages_from_search(seed: Dict[str, Any], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    company = str(seed.get("company_name") or "").strip()
    if not company or not rows:
        return []
    ranked = sorted(rows, key=lambda item: _official_candidate_score(seed, item), reverse=True)
    for row in ranked[:5]:
        score = _official_candidate_score(seed, row)
        if score < 28:
            continue
        host = _domain_from_url(str(row.get("url") or ""))
        if not host:
            continue
        pages = await _fetch_domain_pages(host)
        if pages:
            for item in pages:
                raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
                raw.update({"field": "company_name", "discovered_from": "public_web_search", "candidate_score": score})
                item["raw"] = raw
            return pages
    return []


async def _collect_archive_evidence(seed: Dict[str, Any], max_results: int, request_ctx: Any = None) -> List[Dict[str, Any]]:
    evidence = _inquiry_evidence(seed)
    plan = _archive_research_plan_evidence(seed)
    if plan:
        evidence.append(plan)
    remote_evidence = await _collect_remote_public_signals(seed, max_results, request_ctx)
    evidence.extend(remote_evidence)
    has_remote_first_priority = any(
        _archive_public_source_type(item.get("source_type") or "") in {"official_website", "web_search", "company_registry"}
        for item in remote_evidence
    )
    domain = str(seed.get("domain") or "").strip()
    if domain and not any(_archive_public_source_type(x.get("source_type") or "") == "official_website" for x in evidence):
        for page in await _fetch_domain_pages(domain):
            page.setdefault("raw", {})
            if isinstance(page["raw"], dict):
                page["raw"]["field"] = "domain"
            evidence.append(page)
    queries = [] if has_remote_first_priority else _archive_public_search_queries(seed)
    per_query = max(1, min(4, int(max_results or 4)))
    public_rows: List[Dict[str, Any]] = []
    for row in queries:
        rows = await _search_public_web(row["query"], per_query)
        rows = [item for item in rows if _public_search_row_relevant(seed, item, row.get("field") or "")]
        for item in rows:
            raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
            raw.update({"field": row.get("field") or "", "purpose": row.get("purpose") or "", "query": row.get("query") or raw.get("query") or ""})
            item["raw"] = raw
            if row.get("purpose"):
                item["title"] = f"{_archive_field_label(row.get('field'))} · {item.get('title') or row.get('purpose')}"
        evidence.extend(rows)
        public_rows.extend(rows)
        await asyncio.sleep(random.uniform(0.35, 0.8))
    if not any(str(x.get("source_type") or "") == "official_website" for x in evidence):
        evidence.extend(await _discover_official_pages_from_search(seed, public_rows))

    seen = set()
    out: List[Dict[str, Any]] = []
    limit = max(24, min(80, int(max_results or 8) * 5))
    priority = {
        "alibaba_inquiry": 0,
        "alibaba_profile": 1,
        "official_website": 2,
        "company_registry": 3,
        "web_search": 4,
    }
    ordered_evidence = sorted(
        evidence,
        key=lambda x: (
            priority.get(_archive_public_source_type(x.get("source_type") or ""), 4),
            str(x.get("title") or ""),
        ),
    )
    for item in ordered_evidence:
        if _archive_public_source_type(item.get("source_type") or "") in {"research_plan", "source_inventory"}:
            continue
        title = _compact(item.get("title") or item.get("source_type") or "资料来源", 260)
        url = str(item.get("url") or "").strip()
        snippet = _compact(item.get("snippet") or "", 1600)
        key = (url or title).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "source_type": _archive_public_source_type(item.get("source_type") or "web_search")[:64],
                "source_label": _archive_source_label(item.get("source_type") or "web_search"),
                "title": title,
                "url": url,
                "snippet": snippet,
                "confidence": str(item.get("confidence") or "C")[:16],
                "raw": item.get("raw") if isinstance(item.get("raw"), dict) else {},
            }
        )
        if len(out) >= limit:
            break
    return out


def _fallback_archive_profile(seed: Dict[str, Any], evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
    pending_items = _seed_missing_items(seed)
    external_count = len([x for x in evidence if not str(x.get("source_type") or "").startswith("alibaba_")])
    source_catalog = _archive_source_catalog(evidence)
    used_sources = _archive_used_sources(evidence)
    products = _seed_product_keywords(seed, 8)
    official_rows = [x for x in evidence if _archive_public_source_type(x.get("source_type") or "") == "official_website"]
    web_rows = [x for x in evidence if _archive_public_source_type(x.get("source_type") or "") == "web_search"]
    registry_rows = [x for x in evidence if _archive_public_source_type(x.get("source_type") or "") == "company_registry"]
    official_domain = _domain_from_url(str(official_rows[0].get("url") or "")) if official_rows else (seed.get("domain") or "")
    website_signals = [
        f"{_compact(x.get('title') or '官网页面', 80)}：{_compact(x.get('snippet') or '', 220)}"
        for x in official_rows[:6]
        if x.get("snippet")
    ]
    registration_signals = [
        f"{_compact(x.get('title') or '企业主体库', 90)}：{_compact(x.get('snippet') or '', 220)}"
        for x in registry_rows[:6]
        if x.get("snippet")
    ]
    contacts = []
    if seed.get("email"):
        contacts.append({"name": seed.get("buyer_name") or "", "role": "买家", "email": seed.get("email"), "phone": "", "confidence": "B", "source": "阿里询盘原始消息"})
    if seed.get("phone"):
        contacts.append({"name": seed.get("buyer_name") or "", "role": "买家", "email": "", "phone": seed.get("phone"), "confidence": "B", "source": "阿里询盘原始消息"})
    evidence_text = "\n".join(str(x.get("snippet") or "") for x in official_rows + web_rows[:4])
    for email in _dedupe_strings(re.findall(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", evidence_text, re.IGNORECASE), 4):
        contacts.append({"name": seed.get("buyer_name") or "", "role": "公开联系方式", "email": email, "phone": "", "confidence": "B", "source": "公开证据"})
    for phone in _dedupe_strings(re.findall(r"(?<!\d)(?:\+?\d[\d\s().-]{6,}\d)(?!\d)", evidence_text), 4):
        digits = re.sub(r"\D+", "", phone)
        if 7 <= len(digits) <= 18:
            contacts.append({"name": seed.get("buyer_name") or "", "role": "公开联系方式", "email": "", "phone": phone.strip(), "confidence": "B", "source": "公开证据"})
    contacts = contacts[:8]
    if contacts:
        pending_items = [x for x in pending_items if "缺少可触达联系方式" not in x]
    score = 40
    if seed.get("company_name"):
        score += 12
    if official_domain:
        score += 12
    if contacts:
        score += 10
    if external_count:
        score += min(16, external_count * 4)
    score = max(0, min(100, score))
    grade = "P2" if score >= 70 else ("P3" if score >= 55 else "P4")
    result = {
        "overview": (
            f"{seed.get('company_name') or seed.get('buyer_name') or '未知客户'}："
            + (f"已发现疑似官网 {official_domain}，并抓取官网页面用于核验。" if official_domain else "已根据阿里询盘和公开资料生成基础档案。")
            + ("已匹配到企业主体库记录。" if registry_rows else "")
            + "缺失或低置信信息已标记待核验。"
        ),
        "entity_resolution": {
            "status": "needs_review" if pending_items or not external_count else ("matched" if (official_rows or registry_rows) else "needs_review"),
            "resolved_company": seed.get("company_name") or "",
            "domain": official_domain or seed.get("domain") or "",
            "country": seed.get("country") or "",
            "confidence": "medium" if external_count else "low",
            "reason": "基于阿里询盘字段、消息内容、官网正文和企业主体库整理；未找到的字段不做推断。",
        },
        "basics": {
            "company_name": seed.get("company_name") or "",
            "buyer_name": seed.get("buyer_name") or "",
            "country": seed.get("country") or "",
            "domain": official_domain or seed.get("domain") or "",
            "email": seed.get("email") or "",
            "phone": seed.get("phone") or "",
        },
        "demand_profile": {
            "summary": _compact(seed.get("messages_text") or seed.get("title") or "", 520),
            "product_keywords": products,
            "purchase_intent": "待人工判断",
            "questions": [],
        },
        "company_profile": {
            "summary": (registration_signals[0] if registration_signals else (website_signals[0] if website_signals else "未发现足够明确的官网页面或企业主体库记录，暂不做主体结论。")),
            "business_scope": _compact("；".join(website_signals[:3]), 900) if website_signals else "",
            "registration_signals": registration_signals,
            "website_signals": website_signals,
            "important_findings": website_signals[:4] + registration_signals[:2],
        },
        "social_presence": {
            "summary": "社媒公开资料未命中时保留为空并进入调研缺口。",
            "platforms": [],
            "signals": [],
        },
        "commerce_signals": {
            "summary": "商品/卖家线索会来自电商公开数据和公开搜索；未命中时不推断。",
            "products": products,
            "marketplace_signals": [],
        },
        "contact_validation": {
            "email_domain_match": bool(seed.get("email") and official_domain and _domain_from_email(str(seed.get("email"))) in str(official_domain)),
            "phone_signals": [],
            "notes": ["联系方式来自阿里询盘或官网公开页面，回复客户前仍建议人工核验一次。"],
        },
        "contacts": contacts,
        "risk": {"level": "unknown", "signals": ["公开核验不足时不做风险结论。"]},
        "lead_score": {"score": score, "grade": grade, "reason": "基础评分来自信息完整度和证据数量，不代表最终成交概率。"},
        "pending_review": {"items": pending_items or ["建议人工复核公司主体、联系人职位和真实采购需求。"]},
        "next_actions": ["补齐公司官网/邮箱域名", "人工确认客户主营业务和采购场景", "回复前只依据已确认资料，不承诺价格/交期/库存。"],
        "research_gaps": pending_items or ["公司主体、联系人职位、采购规模、预算、交期、认证要求仍需继续核验。"],
        "source_catalog": source_catalog,
        "used_sources": used_sources,
        "evidence_notes": [f"{_archive_source_label(x.get('source_type'))}：{x.get('title')}" for x in evidence[:12]],
    }
    result["field_evidence"] = _archive_field_evidence(seed, evidence, result)
    return result


def _archive_status_from_profile(profile: Dict[str, Any], evidence: List[Dict[str, Any]]) -> str:
    entity = profile.get("entity_resolution") if isinstance(profile.get("entity_resolution"), dict) else {}
    pending = profile.get("pending_review") if isinstance(profile.get("pending_review"), dict) else {}
    pending_items = pending.get("items") if isinstance(pending.get("items"), list) else []
    external_count = len(_usable_archive_external_evidence(evidence))
    confidence = str(entity.get("confidence") or "").lower()
    if confidence == "low" or pending_items or external_count <= 0:
        return "needs_review"
    return "completed"


def _archive_evidence_text(item: Dict[str, Any]) -> str:
    return _compact(" ".join(str(item.get(key) or "") for key in ("title", "snippet", "url")), 2400)


def _usable_archive_external_evidence(evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        source_type = _archive_public_source_type(item.get("source_type") or "")
        if source_type.startswith("alibaba_") or source_type in {"research_plan", "source_inventory"}:
            continue
        snippet = _compact(item.get("snippet") or "", 2000)
        url = str(item.get("url") or "").strip()
        if source_type == "official_website" and (len(snippet) >= 80 or url):
            out.append(item)
            continue
        if source_type == "company_registry" and len(snippet) >= 40:
            out.append(item)
            continue
        # Search result titles alone are not customer intelligence. Keep them
        # as evidence for audit, but do not let them become visible findings.
        if source_type == "web_search":
            if len(snippet) >= 180 and url and not _is_non_official_domain(_domain_from_url(url)):
                out.append(item)
            continue
        if len(snippet) >= 140:
            out.append(item)
    return out


def _archive_confirmed_official_notes(evidence: List[Dict[str, Any]], limit: int = 4) -> List[str]:
    notes: List[str] = []
    for item in evidence or []:
        if _archive_public_source_type(item.get("source_type") or "") != "official_website":
            continue
        snippet = _compact(item.get("snippet") or "", 360)
        title = _compact(item.get("title") or "官网页面", 80)
        if snippet:
            notes.append(f"{title}：{snippet}")
        if len(notes) >= limit:
            break
    return notes


def _archive_confirmed_registry_notes(evidence: List[Dict[str, Any]], limit: int = 4) -> List[str]:
    notes: List[str] = []
    for item in evidence or []:
        if _archive_public_source_type(item.get("source_type") or "") != "company_registry":
            continue
        snippet = _compact(item.get("snippet") or "", 360)
        title = _compact(item.get("title") or "企业主体库", 90)
        if snippet:
            notes.append(f"{title}：{snippet}")
        if len(notes) >= limit:
            break
    return notes


def _archive_confirmed_commerce_notes(seed: Dict[str, Any], evidence: List[Dict[str, Any]], limit: int = 5) -> List[str]:
    notes: List[str] = []
    company_tokens = _company_tokens(seed.get("company_name"))
    for item in evidence or []:
        source_type = _archive_public_source_type(item.get("source_type") or "")
        if source_type not in {"commerce_product_search"}:
            continue
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        field = str(raw.get("field") or "").strip()
        snippet = _compact(item.get("snippet") or "", 320)
        title = _compact(item.get("title") or "商品/店铺公开数据", 80)
        hay = f"{title} {snippet}".lower()
        company_hit = bool(company_tokens and _token_match_count(company_tokens, hay) >= min(2, len(company_tokens)))
        # Product-keyword searches only describe market/category noise. They do
        # not prove this Alibaba buyer has those products or purchase capacity.
        if field != "product_keywords" and company_hit and len(snippet) >= 80:
            notes.append(f"{title}：{snippet}")
        if len(notes) >= limit:
            break
    return notes


def _archive_confirmed_social_notes(seed: Dict[str, Any], evidence: List[Dict[str, Any]], limit: int = 5) -> List[str]:
    notes: List[str] = []
    company_tokens = _company_tokens(seed.get("company_name"))
    social_types = {
        "professional_network_company",
        "short_video_account_search",
        "short_video_content_search",
        "local_video_account_search",
        "local_video_content_search",
        "visual_social_search",
        "public_discussion_search",
    }
    for item in evidence or []:
        source_type = _archive_public_source_type(item.get("source_type") or "")
        if source_type not in social_types:
            continue
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        field = str(raw.get("field") or "").strip()
        snippet = _compact(item.get("snippet") or "", 320)
        title = _compact(item.get("title") or _archive_source_label(source_type), 80)
        hay = f"{title} {snippet}".lower()
        company_hit = bool(company_tokens and _token_match_count(company_tokens, hay) >= min(2, len(company_tokens)))
        if field != "product_keywords" and company_hit and len(snippet) >= 100:
            notes.append(f"{title}：{snippet}")
        if len(notes) >= limit:
            break
    return notes


def _archive_required_resources(seed: Dict[str, Any], evidence: List[Dict[str, Any]]) -> List[str]:
    usable = _usable_archive_external_evidence(evidence)
    has_official = any(_archive_public_source_type(x.get("source_type") or "") == "official_website" for x in usable)
    has_registry = any(_archive_public_source_type(x.get("source_type") or "") == "company_registry" for x in usable)
    has_email_domain = bool(_domain_from_email(str(seed.get("email") or "")))
    has_company = bool(_entity_text(seed.get("company_name") or "", 160))
    has_country = bool(_entity_text(seed.get("country") or "", 80))
    has_contact = bool(_entity_text(seed.get("email") or seed.get("phone") or "", 160))
    resources: List[str] = []
    if not has_company:
        resources.append("阿里询盘里缺明确公司名：需要从详情页、名片、邮件签名或聊天记录补公司主体。")
    if not has_country:
        resources.append("缺国家/地区：需要阿里买家属性或询盘地址字段，否则同名公司容易匹配错。")
    if not has_contact:
        resources.append("缺邮箱/电话/WhatsApp：需要阿里详情页联系方式或邮件头，才能做联系人核验。")
    if not has_official and not has_registry and not has_email_domain:
        resources.append("缺官网、企业邮箱域名或企业主体库命中：需要官网域名、企业邮箱，或可查企业库账号来确认主体。")
    if not usable:
        resources.append("公开搜索没有抓到可用正文：需要接入稳定搜索正文抓取、企业注册/信用库、海关/贸易数据或职业社媒/企业联系人数据源。")
    if not _archive_confirmed_commerce_notes(seed, evidence):
        resources.append("缺商品/交易侧证据：如要判断采购能力，需要海关数据、B2B 店铺、采购历史、官网产品页或平台商品页。")
    return _dedupe_strings(resources, 8)


def _sanitize_archive_profile(profile: Dict[str, Any], seed: Dict[str, Any], evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
    data = copy.deepcopy(profile if isinstance(profile, dict) else {})
    usable = _usable_archive_external_evidence(evidence)
    official_notes = _archive_confirmed_official_notes(evidence)
    registry_notes = _archive_confirmed_registry_notes(evidence)
    commerce_notes = _archive_confirmed_commerce_notes(seed, evidence)
    social_notes = _archive_confirmed_social_notes(seed, evidence)
    resources = _archive_required_resources(seed, evidence)
    ready = bool(usable and (official_notes or registry_notes) and not resources[:2])
    confidence = "high" if len(usable) >= 4 and official_notes and registry_notes else ("medium" if usable and (official_notes or registry_notes) else ("medium" if usable else "low"))

    data_quality = {
        "ready": ready,
        "confidence": confidence,
        "usable_evidence_count": len(usable),
        "reason": "已拿到可核验公开正文，可作为销售跟进参考。" if ready else "当前主要是阿里询盘原始信息或搜索结果标题，不能当作完整客户档案。",
    }
    data["data_quality"] = data_quality
    data["required_resources"] = resources

    company = data.get("company_profile") if isinstance(data.get("company_profile"), dict) else {}
    company = dict(company)
    company["website_signals"] = official_notes
    company["registration_signals"] = registry_notes
    company["important_findings"] = _dedupe_strings(registry_notes[:2] + official_notes[:3], 5)
    if not official_notes and not registry_notes:
        company["summary"] = "未拿到可核验的官网/企业库正文，暂不输出公司主体结论。"
        company["business_scope"] = ""
    data["company_profile"] = company

    commerce = data.get("commerce_signals") if isinstance(data.get("commerce_signals"), dict) else {}
    commerce = dict(commerce)
    commerce["marketplace_signals"] = commerce_notes
    commerce["products"] = []
    if not commerce_notes:
        commerce["summary"] = "未拿到可核验的商品、店铺或交易侧正文。询盘里的产品词只作为需求关键词，不作为产品证据展示。"
    data["commerce_signals"] = commerce

    social = data.get("social_presence") if isinstance(data.get("social_presence"), dict) else {}
    social = dict(social)
    social["signals"] = social_notes
    platforms = social.get("platforms") if isinstance(social.get("platforms"), list) else []
    social["platforms"] = [
        item for item in platforms
        if isinstance(item, dict)
        and _compact(item.get("signal") or item.get("summary") or "", 400)
        and str(item.get("confidence") or "").lower() not in {"low", "c"}
    ][:8]
    if not social_notes and not social["platforms"]:
        social["summary"] = "未确认到可归属该客户的社媒账号或公开讨论，不展示同名搜索结果。"
    data["social_presence"] = social

    demand = data.get("demand_profile") if isinstance(data.get("demand_profile"), dict) else {}
    demand = dict(demand)
    demand["product_keywords"] = _seed_product_keywords(seed, 8)
    data["demand_profile"] = demand

    pending = data.get("pending_review") if isinstance(data.get("pending_review"), dict) else {}
    pending_items = pending.get("items") if isinstance(pending.get("items"), list) else []
    pending["items"] = _dedupe_strings([str(x) for x in pending_items if str(x or "").strip()] + resources, 12)
    data["pending_review"] = pending
    gaps = data.get("research_gaps") if isinstance(data.get("research_gaps"), list) else []
    data["research_gaps"] = _dedupe_strings([str(x) for x in gaps if str(x or "").strip()] + resources, 12)

    entity = data.get("entity_resolution") if isinstance(data.get("entity_resolution"), dict) else {}
    entity = dict(entity)
    entity["confidence"] = confidence
    if not usable:
        entity["status"] = "insufficient_data"
        entity["reason"] = "没有拿到可核验的外部正文，只能保留阿里询盘原始线索，不能确认同名公司就是该客户。"
    data["entity_resolution"] = entity

    risk = data.get("risk") if isinstance(data.get("risk"), dict) else {}
    risk = dict(risk)
    risk["level"] = "unknown" if not usable else (risk.get("level") or "low")
    risk["signals"] = _dedupe_strings((risk.get("signals") if isinstance(risk.get("signals"), list) else []) + (["外部证据不足，不做风险结论。"] if not usable else []), 8)
    data["risk"] = risk

    lead_score = data.get("lead_score") if isinstance(data.get("lead_score"), dict) else {}
    lead_score = dict(lead_score)
    if not usable:
        lead_score.update({"score": 20, "grade": "P4", "reason": "外部证据不足，只能作为待补全询盘，不能作为高价值客户档案。"})
    elif len(usable) < 3:
        lead_score["score"] = min(int(float(lead_score.get("score") or 45)), 55)
        lead_score["grade"] = "P3"
        lead_score["reason"] = lead_score.get("reason") or "证据较少，需要继续补充官网、联系人或交易侧资料。"
    data["lead_score"] = lead_score

    if not usable:
        data["overview"] = "当前资料不足：系统只拿到阿里询盘原始信息或弱搜索结果，未形成可核验客户档案。请先补充公司主体、官网/邮箱域名、联系人或企业/贸易数据源。"
    return data


def _score_grade_from_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    lead_score = profile.get("lead_score") if isinstance(profile.get("lead_score"), dict) else {}
    try:
        score = int(float(lead_score.get("score")))
    except Exception:
        score = None
    if score is not None:
        score = max(0, min(100, score))
    grade = str(lead_score.get("grade") or "").strip().upper()
    if not grade and score is not None:
        grade = "P0" if score >= 90 else ("P1" if score >= 80 else ("P2" if score >= 70 else ("P3" if score >= 60 else "P4")))
    return {"score": score, "grade": grade[:16] if grade else ""}


async def _generate_archive_profile(
    request: Request,
    seed: Dict[str, Any],
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:
    prompt = (
        "你是资深外贸获客和客户尽调分析员。请基于阿里询盘种子信息、字段调研计划、公开网页证据和社媒/电商公开证据生成客户档案，只返回严格 JSON。\n"
        "绝对要求：不能编造公司、联系人、采购规模、认证、价格、交期、风险结论；证据没有明确写出的内容必须放到 pending_review.items。\n"
        "阿里询盘右侧属性只能作为原始线索，不能直接当成完整客户档案。官网正文、企业主体库、公开网页、社媒、电商证据需要标明来源。所有标题、枚举说明、原因说明都用中文。\n"
        "你要做深度调研组织：公司线索必须分析公司详情；域名线索必须分析官网/邮箱一致性；买家线索只能作为低置信辅助；产品词要结合社媒/电商/公开搜索判断意图。\n"
        "如果证据不足，必须明确写入 research_gaps 和 pending_review.items，不能模拟数据、不能补幻想信息。\n"
        "输出 JSON 字段：\n"
        "{overview:string, entity_resolution:{status:string,resolved_company:string,domain:string,country:string,confidence:string,reason:string}, "
        "basics:{company_name:string,buyer_name:string,country:string,domain:string,email:string,phone:string,industry:string,website_summary:string}, "
        "demand_profile:{summary:string,product_keywords:string[],purchase_intent:string,questions:string[]}, "
        "company_profile:{summary:string,business_scope:string,registration_signals:string[],website_signals:string[],important_findings:string[]}, "
        "social_presence:{summary:string,platforms:{platform:string,account:string,signal:string,confidence:string,source:string}[],signals:string[]}, "
        "commerce_signals:{summary:string,products:string[],marketplace_signals:string[]}, "
        "contact_validation:{email_domain_match:boolean,phone_signals:string[],notes:string[]}, "
        "contacts:{name:string,role:string,email:string,phone:string,confidence:string,source:string}[], "
        "risk:{level:string,signals:string[]}, lead_score:{score:number,grade:string,reason:string}, "
        "pending_review:{items:string[]}, next_actions:string[], research_gaps:string[], source_catalog:any[], used_sources:any[], evidence_notes:string[], "
        "field_evidence:{field:{value:string,confidence:string,sources:string[],note:string}}}。\n\n"
        f"信息源目录：{json.dumps(_archive_source_catalog(evidence), ensure_ascii=False)[:12000]}\n\n"
        f"实际使用信息源：{json.dumps(_archive_used_sources(evidence), ensure_ascii=False)[:12000]}\n\n"
        f"询盘种子：{json.dumps(seed, ensure_ascii=False)[:30000]}\n\n"
        f"证据：{json.dumps(evidence, ensure_ascii=False)[:40000]}"
    )
    try:
        data = await _call_llm_for_json(request, prompt)
    except Exception as exc:
        logger.warning("[ALIBABA-INQUIRY] archive LLM fallback inquiry=%s err=%s", seed.get("inquiry_id"), exc)
        data = _fallback_archive_profile(seed, evidence)
    if not isinstance(data, dict):
        data = _fallback_archive_profile(seed, evidence)
    fallback = _fallback_archive_profile(seed, evidence)
    for key, value in fallback.items():
        data.setdefault(key, value)
    if not isinstance(data.get("pending_review"), dict):
        data["pending_review"] = fallback["pending_review"]
    if not isinstance(data.get("contacts"), list):
        data["contacts"] = []
    if not isinstance(data.get("next_actions"), list):
        data["next_actions"] = fallback["next_actions"]
    if not isinstance(data.get("evidence_notes"), list):
        data["evidence_notes"] = fallback["evidence_notes"]
    if not isinstance(data.get("company_profile"), dict):
        data["company_profile"] = fallback["company_profile"]
    if not isinstance(data.get("social_presence"), dict):
        data["social_presence"] = fallback["social_presence"]
    if not isinstance(data.get("commerce_signals"), dict):
        data["commerce_signals"] = fallback["commerce_signals"]
    if not isinstance(data.get("contact_validation"), dict):
        data["contact_validation"] = fallback["contact_validation"]
    if not isinstance(data.get("research_gaps"), list):
        data["research_gaps"] = fallback["research_gaps"]
    data["source_catalog"] = _archive_source_catalog(evidence)
    data["used_sources"] = _archive_used_sources(evidence)
    if not isinstance(data.get("field_evidence"), dict):
        data["field_evidence"] = _archive_field_evidence(seed, evidence, data)
    data = _sanitize_archive_profile(data, seed, evidence)
    data["field_evidence"] = _archive_field_evidence(seed, evidence, data)
    return data


def _archive_or_404(db: Session, user_id: int, archive_id: int) -> AlibabaCustomerArchive:
    row = (
        db.query(AlibabaCustomerArchive)
        .filter(AlibabaCustomerArchive.id == archive_id, AlibabaCustomerArchive.user_id == user_id)
        .first()
    )
    if not row:
        raise HTTPException(404, detail="客户档案不存在")
    return row


def _get_or_create_archive(
    db: Session,
    user_id: int,
    account_id: int,
    inquiry_id: str,
    seed: Dict[str, Any],
) -> AlibabaCustomerArchive:
    key = _archive_key(seed)
    row = (
        db.query(AlibabaCustomerArchive)
        .filter(AlibabaCustomerArchive.account_id == account_id, AlibabaCustomerArchive.archive_key == key)
        .first()
    )
    if not row:
        row = (
            db.query(AlibabaCustomerArchive)
            .filter(AlibabaCustomerArchive.account_id == account_id, AlibabaCustomerArchive.inquiry_id == inquiry_id)
            .first()
        )
    display = _first_text(seed.get("company_name"), seed.get("buyer_name"), seed.get("title"), f"询盘 {inquiry_id}")
    if not row:
        row = AlibabaCustomerArchive(
            user_id=user_id,
            account_id=account_id,
            inquiry_id=inquiry_id,
            archive_key=key,
            status="pending",
            display_name=display,
        )
        db.add(row)
        db.flush()
    row.archive_key = row.archive_key or key
    linked_ids = _linked_inquiry_ids(row)
    if inquiry_id not in linked_ids:
        linked_ids.append(inquiry_id)
    row.linked_inquiry_ids = _dedupe_strings(linked_ids, 200)
    seed = _augment_seed_with_related_inquiries(db, account_id, seed, row.linked_inquiry_ids or [])
    row.display_name = display
    row.company_name = _first_text(seed.get("company_name")) or None
    row.buyer_name = _first_text(seed.get("buyer_name")) or None
    row.country = _first_text(seed.get("country"), limit=128) or None
    row.domain = _first_text(seed.get("domain")) or None
    row.email = _first_text(seed.get("email")) or None
    row.phone = _first_text(seed.get("phone"), limit=64) or None
    row.seed = seed
    row.updated_at = _now()
    return row


async def _run_archive_enrichment(
    *,
    db: Session,
    request: Request,
    current_user: _ServerUser,
    account_id: int,
    inquiry: AlibabaInquiry,
    profile: Optional[AlibabaCustomerProfile],
    max_results: int,
) -> Dict[str, Any]:
    seed = _archive_seed_from_inquiry(db, account_id, inquiry, profile)
    archive = _get_or_create_archive(db, current_user.id, account_id, inquiry.inquiry_id, seed)
    archive.status = "running"
    archive.last_error = None
    job = AlibabaCustomerArchiveJob(
        user_id=current_user.id,
        account_id=account_id,
        inquiry_id=inquiry.inquiry_id,
        archive_id=archive.id,
        status="running",
        progress="正在提取询盘种子并补充公开资料",
        seed=seed,
    )
    db.add(job)
    db.commit()
    db.refresh(archive)
    db.refresh(job)
    archive_id = int(archive.id)
    job_id = int(job.id)
    try:
        evidence = await _collect_archive_evidence(seed, max_results, request)
        job.progress = "正在整理客户档案"
        db.commit()
        profile_payload = await _generate_archive_profile(request, seed, evidence)
        score_grade = _score_grade_from_profile(profile_payload)
        status = _archive_status_from_profile(profile_payload, evidence)
        basics = profile_payload.get("basics") if isinstance(profile_payload.get("basics"), dict) else {}
        entity = profile_payload.get("entity_resolution") if isinstance(profile_payload.get("entity_resolution"), dict) else {}
        field_evidence = _archive_field_evidence(seed, evidence, profile_payload)
        archive.status = status
        archive.display_name = _first_text(
            basics.get("company_name"),
            entity.get("resolved_company"),
            seed.get("company_name"),
            seed.get("buyer_name"),
            archive.display_name,
        )
        archive.company_name = _first_text(basics.get("company_name"), entity.get("resolved_company"), seed.get("company_name")) or None
        archive.buyer_name = _first_text(basics.get("buyer_name"), seed.get("buyer_name")) or None
        archive.country = _first_text(basics.get("country"), entity.get("country"), seed.get("country"), limit=128) or None
        archive.domain = _first_text(basics.get("domain"), entity.get("domain"), seed.get("domain")) or None
        archive.email = _first_text(basics.get("email"), seed.get("email")) or None
        archive.phone = _first_text(basics.get("phone"), seed.get("phone"), limit=64) or None
        archive.score = score_grade["score"]
        archive.grade = score_grade["grade"] or None
        archive.summary = _compact(profile_payload.get("overview") or "", 2000)
        archive.seed = seed
        archive.profile = profile_payload
        archive.field_evidence = field_evidence
        archive.pending_review = profile_payload.get("pending_review") if isinstance(profile_payload.get("pending_review"), dict) else {"items": _seed_missing_items(seed)}
        archive.raw = {
            "evidence_count": len(evidence),
            "source": "cnbd-route-19188-v8.1-clean",
            "source_catalog": _archive_source_catalog(evidence),
            "used_sources": _archive_used_sources(evidence),
            "route": seed.get("market_scope") or "GLOBAL",
        }
        archive.last_enriched_at = _now()
        archive.updated_at = _now()
        db.query(AlibabaCustomerArchiveEvidence).filter(AlibabaCustomerArchiveEvidence.archive_id == archive.id).delete(synchronize_session=False)
        for item in evidence:
            db.add(
                AlibabaCustomerArchiveEvidence(
                    user_id=current_user.id,
                    account_id=account_id,
                    inquiry_id=inquiry.inquiry_id,
                    archive_id=archive.id,
                    source_type=str(item.get("source_type") or "web_search")[:64],
                    title=str(item.get("title") or "")[:512],
                    url=str(item.get("url") or ""),
                    snippet=str(item.get("snippet") or ""),
                    confidence=str(item.get("confidence") or "C")[:16],
                    raw=item.get("raw") if isinstance(item.get("raw"), dict) else {},
                )
            )
        job.status = "succeeded"
        job.progress = f"补全完成：证据 {len(evidence)} 条，状态 {status}"
        job.result = {"archive_id": archive.id, "status": status, "evidence_count": len(evidence), "profile": profile_payload}
        job.updated_at = _now()
        db.commit()
        db.refresh(archive)
        db.refresh(job)
        return {"ok": True, "archive": _serialize_archive(archive, db), "job": _serialize_archive_job(job)}
    except Exception as exc:
        logger.exception("[ALIBABA-INQUIRY] customer archive enrichment failed")
        db.rollback()
        archive = _archive_or_404(db, current_user.id, archive_id)
        job = db.query(AlibabaCustomerArchiveJob).filter(AlibabaCustomerArchiveJob.id == job_id).first()
        archive.status = "failed"
        archive.last_error = str(exc)[:2000]
        archive.updated_at = _now()
        if job:
            job.status = "failed"
            job.progress = "补全失败"
            job.error = str(exc)[:2000]
            job.updated_at = _now()
        db.commit()
        raise HTTPException(500, detail=str(exc))


def _archive_job_or_404(db: Session, user_id: int, job_id: int) -> AlibabaCustomerArchiveJob:
    row = (
        db.query(AlibabaCustomerArchiveJob)
        .filter(AlibabaCustomerArchiveJob.id == job_id, AlibabaCustomerArchiveJob.user_id == user_id)
        .first()
    )
    if not row:
        raise HTTPException(404, detail="客户档案补全任务不存在")
    return row


def _serialize_archive_job_bundle(db: Session, job: AlibabaCustomerArchiveJob) -> Dict[str, Any]:
    archive = None
    if job.archive_id:
        archive = db.query(AlibabaCustomerArchive).filter(AlibabaCustomerArchive.id == job.archive_id).first()
    return {
        "ok": True,
        "job": _serialize_archive_job(job),
        "archive": _serialize_archive(archive, db) if archive else None,
    }


def _enqueue_archive_enrichment(
    *,
    db: Session,
    user_id: int,
    account_id: int,
    inquiry: AlibabaInquiry,
    profile: Optional[AlibabaCustomerProfile],
    force: bool,
    max_results: int,
) -> Dict[str, Any]:
    seed = _archive_seed_from_inquiry(db, account_id, inquiry, profile)
    archive = _get_or_create_archive(db, user_id, account_id, inquiry.inquiry_id, seed)
    active_job = None
    if not force:
        active_job = (
            db.query(AlibabaCustomerArchiveJob)
            .filter(
                AlibabaCustomerArchiveJob.archive_id == archive.id,
                AlibabaCustomerArchiveJob.status.in_(["queued", "running"]),
            )
            .order_by(AlibabaCustomerArchiveJob.updated_at.desc(), AlibabaCustomerArchiveJob.id.desc())
            .first()
        )
    if active_job:
        active_job.progress = active_job.progress or "等待客户档案补全"
        active_job.seed = active_job.seed or seed
        active_job.updated_at = _now()
        archive.status = active_job.status
        archive.updated_at = _now()
        db.commit()
        db.refresh(active_job)
        db.refresh(archive)
        return {"archive": archive, "job": active_job, "scheduled": False, "max_results": max_results}

    archive.status = "queued"
    archive.last_error = None
    archive.seed = seed
    archive.updated_at = _now()
    job = AlibabaCustomerArchiveJob(
        user_id=user_id,
        account_id=account_id,
        inquiry_id=inquiry.inquiry_id,
        archive_id=archive.id,
        status="queued",
        progress="客户档案补全已排队",
        seed=seed,
        result={"max_results": max_results, "force": bool(force)},
    )
    db.add(job)
    db.commit()
    db.refresh(archive)
    db.refresh(job)
    return {"archive": archive, "job": job, "scheduled": True, "max_results": max_results}


def _schedule_archive_enrichment_job(job_id: int, llm_ctx: Any, max_results: int, force: bool = False) -> None:
    existing = _archive_job_tasks.get(int(job_id))
    if existing and not existing.done():
        return
    loop = asyncio.get_running_loop()
    task = loop.create_task(_run_archive_enrichment_job(job_id=int(job_id), llm_ctx=llm_ctx, max_results=max_results, force=force))
    _archive_job_tasks[int(job_id)] = task

    def _cleanup(_: asyncio.Task) -> None:
        _archive_job_tasks.pop(int(job_id), None)

    task.add_done_callback(_cleanup)


async def _run_archive_enrichment_job(*, job_id: int, llm_ctx: Any, max_results: int, force: bool = False) -> None:
    db = SessionLocal()
    try:
        job = db.query(AlibabaCustomerArchiveJob).filter(AlibabaCustomerArchiveJob.id == job_id).first()
        if not job:
            return
        inquiry = (
            db.query(AlibabaInquiry)
            .filter(
                AlibabaInquiry.user_id == job.user_id,
                AlibabaInquiry.account_id == job.account_id,
                AlibabaInquiry.inquiry_id == job.inquiry_id,
            )
            .first()
        )
        if not inquiry:
            job.status = "failed"
            job.error = "原始询盘不存在"
            job.progress = "客户档案补全失败"
            job.updated_at = _now()
            db.commit()
            return
        profile = (
            db.query(AlibabaCustomerProfile)
            .filter(AlibabaCustomerProfile.account_id == job.account_id, AlibabaCustomerProfile.inquiry_id == job.inquiry_id)
            .first()
        )
        seed = job.seed if isinstance(job.seed, dict) else _archive_seed_from_inquiry(db, job.account_id, inquiry, profile)
        archive = _get_or_create_archive(db, job.user_id, job.account_id, inquiry.inquiry_id, seed)
        seed = _augment_seed_with_related_inquiries(db, job.account_id, seed, _linked_inquiry_ids(archive))
        job.archive_id = archive.id
        job.status = "running"
        job.progress = "正在提取询盘线索并深度调研公开证据"
        job.error = None
        job.seed = seed
        job.updated_at = _now()
        archive.status = "running"
        archive.last_error = None
        archive.seed = seed
        archive.updated_at = _now()
        db.commit()

        previous_evidence = []
        if not force:
            previous_rows = (
                db.query(AlibabaCustomerArchiveEvidence)
                .filter(AlibabaCustomerArchiveEvidence.archive_id == archive.id)
                .order_by(AlibabaCustomerArchiveEvidence.created_at.asc(), AlibabaCustomerArchiveEvidence.id.asc())
                .all()
            )
            previous_evidence = [_serialize_archive_evidence(x) for x in previous_rows]

        evidence = await _collect_archive_evidence(seed, max_results, llm_ctx)
        evidence = _merge_archive_evidence(previous_evidence, evidence)
        job.progress = "正在根据证据生成客户档案"
        job.updated_at = _now()
        db.commit()

        profile_payload = await _generate_archive_profile(llm_ctx, seed, evidence)
        score_grade = _score_grade_from_profile(profile_payload)
        status = _archive_status_from_profile(profile_payload, evidence)
        basics = profile_payload.get("basics") if isinstance(profile_payload.get("basics"), dict) else {}
        entity = profile_payload.get("entity_resolution") if isinstance(profile_payload.get("entity_resolution"), dict) else {}
        field_evidence = _archive_field_evidence(seed, evidence, profile_payload)

        archive.status = status
        archive.display_name = _first_text(
            basics.get("company_name"),
            entity.get("resolved_company"),
            seed.get("company_name"),
            seed.get("buyer_name"),
            archive.display_name,
        )
        archive.company_name = _first_text(basics.get("company_name"), entity.get("resolved_company"), seed.get("company_name")) or None
        archive.buyer_name = _first_text(basics.get("buyer_name"), seed.get("buyer_name")) or None
        archive.country = _first_text(basics.get("country"), entity.get("country"), seed.get("country"), limit=128) or None
        archive.domain = _first_text(basics.get("domain"), entity.get("domain"), seed.get("domain")) or None
        archive.email = _first_text(basics.get("email"), seed.get("email")) or None
        archive.phone = _first_text(basics.get("phone"), seed.get("phone"), limit=64) or None
        archive.score = score_grade["score"]
        archive.grade = score_grade["grade"] or None
        archive.summary = _compact(profile_payload.get("overview") or "", 2000)
        archive.seed = seed
        archive.profile = profile_payload
        archive.field_evidence = field_evidence
        archive.pending_review = profile_payload.get("pending_review") if isinstance(profile_payload.get("pending_review"), dict) else {"items": _seed_missing_items(seed)}
        archive.raw = {
            "evidence_count": len(evidence),
            "source": "cnbd-route-19188-v8.1-clean",
            "route": seed.get("market_scope") or "GLOBAL",
            "source_catalog": _archive_source_catalog(evidence),
            "used_sources": _archive_used_sources(evidence),
        }
        archive.last_enriched_at = _now()
        archive.updated_at = _now()

        db.query(AlibabaCustomerArchiveEvidence).filter(AlibabaCustomerArchiveEvidence.archive_id == archive.id).delete(synchronize_session=False)
        for item in evidence:
            db.add(
                AlibabaCustomerArchiveEvidence(
                    user_id=job.user_id,
                    account_id=job.account_id,
                    inquiry_id=job.inquiry_id,
                    archive_id=archive.id,
                    source_type=str(item.get("source_type") or "web_search")[:64],
                    title=str(item.get("title") or "")[:512],
                    url=str(item.get("url") or ""),
                    snippet=str(item.get("snippet") or ""),
                    confidence=str(item.get("confidence") or "C")[:16],
                    raw=item.get("raw") if isinstance(item.get("raw"), dict) else {},
                )
            )
        job.status = "succeeded"
        job.progress = f"补全完成：证据 {len(evidence)} 条，状态 {status}"
        job.result = {
            "archive_id": archive.id,
            "status": status,
            "evidence_count": len(evidence),
            "source_catalog": _archive_source_catalog(evidence),
            "used_sources": _archive_used_sources(evidence),
            "profile": profile_payload,
        }
        job.updated_at = _now()
        db.commit()
    except Exception as exc:
        logger.exception("[ALIBABA-INQUIRY] customer archive background job failed")
        db.rollback()
        try:
            job = db.query(AlibabaCustomerArchiveJob).filter(AlibabaCustomerArchiveJob.id == job_id).first()
            if job:
                job.status = "failed"
                job.progress = "客户档案补全失败"
                job.error = str(exc)[:2000]
                job.updated_at = _now()
                if job.archive_id:
                    archive = db.query(AlibabaCustomerArchive).filter(AlibabaCustomerArchive.id == job.archive_id).first()
                    if archive:
                        archive.status = "failed"
                        archive.last_error = str(exc)[:2000]
                        archive.updated_at = _now()
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("[ALIBABA-INQUIRY] failed to persist archive job error")
    finally:
        db.close()


@router.get("/api/alibaba-inquiries/accounts/{account_id}/customer-archives", summary="客户档案分页")
def list_customer_archives(
    account_id: int,
    q: str = "",
    status: str = "",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    limit = max(1, min(MAX_PAGE_SIZE, int(limit or DEFAULT_PAGE_SIZE)))
    offset = max(0, int(offset or 0))
    query = db.query(AlibabaCustomerArchive).filter(
        AlibabaCustomerArchive.user_id == current_user.id,
        AlibabaCustomerArchive.account_id == account_id,
    )
    st = (status or "").strip()
    if st:
        query = query.filter(AlibabaCustomerArchive.status == st)
    kw = (q or "").strip()
    if kw:
        like = f"%{kw}%"
        query = query.filter(
            or_(
                AlibabaCustomerArchive.display_name.like(like),
                AlibabaCustomerArchive.company_name.like(like),
                AlibabaCustomerArchive.buyer_name.like(like),
                AlibabaCustomerArchive.country.like(like),
                AlibabaCustomerArchive.domain.like(like),
                AlibabaCustomerArchive.email.like(like),
            )
        )
    total = query.count()
    rows = query.order_by(AlibabaCustomerArchive.updated_at.desc()).offset(offset).limit(limit).all()
    return {"ok": True, "total": total, "limit": limit, "offset": offset, "items": [_serialize_archive(x, db) for x in rows]}


@router.get("/api/alibaba-inquiries/customer-archives/{archive_id}", summary="客户档案详情")
def get_customer_archive(
    archive_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    archive = _archive_or_404(db, current_user.id, archive_id)
    evidence = (
        db.query(AlibabaCustomerArchiveEvidence)
        .filter(AlibabaCustomerArchiveEvidence.archive_id == archive.id)
        .order_by(AlibabaCustomerArchiveEvidence.created_at.asc(), AlibabaCustomerArchiveEvidence.id.asc())
        .all()
    )
    jobs = (
        db.query(AlibabaCustomerArchiveJob)
        .filter(AlibabaCustomerArchiveJob.archive_id == archive.id)
        .order_by(AlibabaCustomerArchiveJob.created_at.desc())
        .limit(10)
        .all()
    )
    inquiry = (
        db.query(AlibabaInquiry)
        .filter(AlibabaInquiry.account_id == archive.account_id, AlibabaInquiry.inquiry_id == archive.inquiry_id)
        .first()
    )
    return {
        "ok": True,
        "archive": _serialize_archive(archive, db),
        "evidence": [_serialize_archive_evidence(x) for x in evidence],
        "jobs": [_serialize_archive_job(x) for x in jobs],
        "inquiry": _serialize_inquiry(inquiry) if inquiry else None,
    }


@router.post("/api/alibaba-inquiries/accounts/{account_id}/inquiries/{inquiry_id}/archive/enrich", summary="生成或更新客户档案")
async def enrich_inquiry_archive(
    account_id: int,
    inquiry_id: str,
    body: ArchiveEnrichBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    inquiry = (
        db.query(AlibabaInquiry)
        .filter(
            AlibabaInquiry.user_id == current_user.id,
            AlibabaInquiry.account_id == account_id,
            AlibabaInquiry.inquiry_id == inquiry_id,
        )
        .first()
    )
    if not inquiry:
        raise HTTPException(404, detail="询盘不存在")
    profile = (
        db.query(AlibabaCustomerProfile)
        .filter(AlibabaCustomerProfile.account_id == account_id, AlibabaCustomerProfile.inquiry_id == inquiry_id)
        .first()
    )
    bundle = _enqueue_archive_enrichment(
        db=db,
        user_id=current_user.id,
        account_id=account_id,
        inquiry=inquiry,
        profile=profile,
        force=body.force,
        max_results=body.max_results,
    )
    job = bundle["job"]
    _schedule_archive_enrichment_job(int(job.id), _llm_request_context(request), bundle["max_results"], force=body.force)
    return {
        "ok": True,
        "queued": True,
        "archive": _serialize_archive(bundle["archive"], db),
        "job": _serialize_archive_job(job),
    }


@router.post("/api/alibaba-inquiries/customer-archives/{archive_id}/rerun", summary="重新补全客户档案")
async def rerun_customer_archive(
    archive_id: int,
    body: ArchiveEnrichBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    archive = _archive_or_404(db, current_user.id, archive_id)
    inquiry = (
        db.query(AlibabaInquiry)
        .filter(
            AlibabaInquiry.user_id == current_user.id,
            AlibabaInquiry.account_id == archive.account_id,
            AlibabaInquiry.inquiry_id == archive.inquiry_id,
        )
        .first()
    )
    if not inquiry:
        raise HTTPException(404, detail="原始询盘不存在，无法重新补全")
    profile = (
        db.query(AlibabaCustomerProfile)
        .filter(AlibabaCustomerProfile.account_id == archive.account_id, AlibabaCustomerProfile.inquiry_id == archive.inquiry_id)
        .first()
    )
    bundle = _enqueue_archive_enrichment(
        db=db,
        user_id=current_user.id,
        account_id=archive.account_id,
        inquiry=inquiry,
        profile=profile,
        force=True,
        max_results=body.max_results,
    )
    job = bundle["job"]
    _schedule_archive_enrichment_job(int(job.id), _llm_request_context(request), bundle["max_results"], force=True)
    return {
        "ok": True,
        "queued": True,
        "archive": _serialize_archive(bundle["archive"], db),
        "job": _serialize_archive_job(job),
    }


@router.get("/api/alibaba-inquiries/customer-archive-jobs/{job_id}", summary="查询客户档案补全任务")
def get_customer_archive_job(
    job_id: int,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    job = _archive_job_or_404(db, current_user.id, job_id)
    if job.status in {"queued", "running"}:
        result = job.result if isinstance(job.result, dict) else {}
        max_results = int(result.get("max_results") or 8)
        force = bool(result.get("force") or False)
        _schedule_archive_enrichment_job(int(job.id), _llm_request_context(request), max_results, force=force)
    return _serialize_archive_job_bundle(db, job)


@router.post("/api/alibaba-inquiries/customer-archive-jobs/{job_id}/resume", summary="恢复客户档案补全任务")
async def resume_customer_archive_job(
    job_id: int,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    job = _archive_job_or_404(db, current_user.id, job_id)
    result = job.result if isinstance(job.result, dict) else {}
    max_results = int(result.get("max_results") or 8)
    force = bool(result.get("force") or False)
    if job.status not in {"queued", "running", "failed"}:
        return _serialize_archive_job_bundle(db, job)
    job.status = "queued"
    job.progress = "客户档案补全已排队"
    job.error = None
    job.updated_at = _now()
    db.commit()
    db.refresh(job)
    _schedule_archive_enrichment_job(int(job.id), _llm_request_context(request), max_results, force=force)
    return _serialize_archive_job_bundle(db, job)


@router.patch("/api/alibaba-inquiries/customer-archives/{archive_id}", summary="人工修正客户档案")
def update_customer_archive(
    archive_id: int,
    body: ArchiveUpdateBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    archive = _archive_or_404(db, current_user.id, archive_id)
    profile_payload = dict(archive.profile) if isinstance(archive.profile, dict) else {}
    basics = dict(profile_payload.get("basics")) if isinstance(profile_payload.get("basics"), dict) else {}
    incoming_basics = body.basics if isinstance(body.basics, dict) else {}
    for key in ("company_name", "buyer_name", "country", "domain", "email", "phone"):
        if key in incoming_basics:
            basics[key] = _first_text(incoming_basics.get(key), limit=255)
    profile_payload["basics"] = basics
    if body.display_name is not None:
        archive.display_name = _first_text(body.display_name, archive.display_name) or archive.display_name
    archive.company_name = _first_text(basics.get("company_name"), archive.company_name) or None
    archive.buyer_name = _first_text(basics.get("buyer_name"), archive.buyer_name) or None
    archive.country = _first_text(basics.get("country"), archive.country, limit=128) or None
    archive.domain = _first_text(basics.get("domain"), archive.domain) or None
    archive.email = _first_text(basics.get("email"), archive.email) or None
    archive.phone = _first_text(basics.get("phone"), archive.phone, limit=64) or None
    if body.status:
        archive.status = _first_text(body.status, limit=32) or archive.status
    if body.grade is not None:
        archive.grade = _first_text(body.grade, limit=16) or None
    if body.score is not None:
        archive.score = max(0, min(100, int(body.score)))
    if isinstance(body.pending_review, dict) and body.pending_review:
        archive.pending_review = body.pending_review
        profile_payload["pending_review"] = body.pending_review
    overrides = dict(archive.manual_overrides) if isinstance(archive.manual_overrides, dict) else {}
    overrides.update(
        {
            "basics": incoming_basics,
            "display_name": body.display_name,
            "status": body.status,
            "grade": body.grade,
            "score": body.score,
            "notes": body.notes,
            "updated_at": _dt(_now()),
        }
    )
    archive.manual_overrides = overrides
    archive.profile = profile_payload
    archive.field_evidence = archive.field_evidence or _archive_field_evidence(archive.seed or {}, [], profile_payload)
    archive.updated_at = _now()
    db.commit()
    db.refresh(archive)
    return {"ok": True, "archive": _serialize_archive(archive, db)}


def _extract_text_from_file(path: Path, filename: str) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".csv", ".json", ".html", ".htm", ".log"}:
        for enc in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
            try:
                return path.read_text(encoding=enc, errors="ignore")[:200000]
            except Exception:
                continue
    if suffix == ".docx":
        try:
            from docx import Document  # type: ignore

            doc = Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs if p.text).strip()[:200000]
        except Exception as exc:
            return f"[{filename}] 已上传，暂未能提取 docx 文本：{exc}"
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(str(path))
            parts = []
            for page in reader.pages[:30]:
                parts.append(page.extract_text() or "")
            return "\n".join(parts).strip()[:200000]
        except Exception as exc:
            return f"[{filename}] 已上传，暂未能提取 PDF 文本：{exc}"
    return f"[{filename}] 已上传，后续可作为话术资料附件；当前类型暂不自动提取正文。"


@router.post("/api/alibaba-inquiries/accounts/{account_id}/training-docs", summary="上传询盘话术/资料")
async def upload_training_doc(
    account_id: int,
    kind: str = Form(default="script"),
    title: str = Form(default=""),
    content: str = Form(default=""),
    file: Optional[UploadFile] = File(default=None),
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    text_content = (content or "").strip()
    file_path = ""
    filename = ""
    raw_size = 0
    content_type = ""
    if file is not None and (file.filename or "").strip():
        filename = _safe_name(file.filename or "training.txt", "training.txt")
        suffix = Path(filename).suffix
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        account_dir = _UPLOAD_DIR / str(current_user.id) / str(account_id)
        account_dir.mkdir(parents=True, exist_ok=True)
        path = account_dir / f"{stamp}_{hashlib.sha1(filename.encode()).hexdigest()[:8]}{suffix or '.dat'}"
        raw = await file.read()
        raw_size = len(raw)
        content_type = file.content_type or ""
        if raw:
            path.write_bytes(raw)
            file_path = str(path)
            extracted = _extract_text_from_file(path, filename)
            if extracted.strip():
                text_content = extracted
    if not text_content:
        raise HTTPException(400, detail="请上传资料文件，或直接录入产品资料/FAQ内容。")
    safe_kind = (kind or "script").strip()[:32] or "script"
    fallback_title = file.filename if file is not None and file.filename else f"{safe_kind}资料"
    doc = AlibabaInquiryTrainingDoc(
        user_id=current_user.id,
        account_id=account_id,
        title=(title or fallback_title)[:255],
        kind=safe_kind,
        filename=(file.filename if file is not None and file.filename else filename),
        content=text_content[:200000],
        file_path=file_path,
        meta={"size": raw_size or len(text_content.encode("utf-8")), "content_type": content_type, "source": "file" if file_path else "text"},
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return {"ok": True, "doc": _serialize_doc(doc)}


def _serialize_doc(row: AlibabaInquiryTrainingDoc) -> Dict[str, Any]:
    return {
        "id": row.id,
        "account_id": row.account_id,
        "title": row.title,
        "kind": row.kind,
        "filename": row.filename or "",
        "content_preview": _compact(row.content or "", 180),
        "created_at": _dt(row.created_at),
    }


@router.get("/api/alibaba-inquiries/accounts/{account_id}/training-docs", summary="话术资料列表")
def list_training_docs(
    account_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    rows = (
        db.query(AlibabaInquiryTrainingDoc)
        .filter(
            AlibabaInquiryTrainingDoc.user_id == current_user.id,
            or_(AlibabaInquiryTrainingDoc.account_id == account_id, AlibabaInquiryTrainingDoc.account_id.is_(None)),
        )
        .order_by(AlibabaInquiryTrainingDoc.created_at.desc())
        .all()
    )
    return {"ok": True, "items": [_serialize_doc(x) for x in rows]}


def _history_for_ai(db: Session, user_id: int, account_id: int) -> str:
    inquiries = (
        db.query(AlibabaInquiry)
        .filter(AlibabaInquiry.user_id == user_id, AlibabaInquiry.account_id == account_id)
        .order_by(AlibabaInquiry.last_message_at.desc().nullslast(), AlibabaInquiry.updated_at.desc())
        .all()
    )
    parts: List[str] = []
    for inquiry in inquiries:
        msgs = (
            db.query(AlibabaInquiryMessage)
            .filter(AlibabaInquiryMessage.account_id == account_id, AlibabaInquiryMessage.inquiry_id == inquiry.inquiry_id)
            .order_by(AlibabaInquiryMessage.sent_at.asc().nullslast(), AlibabaInquiryMessage.id.asc())
            .limit(40)
            .all()
        )
        if not msgs and inquiry.raw_text:
            parts.append(f"询盘 {inquiry.inquiry_id} 买家={inquiry.buyer_name or '-'}\n列表摘要：{inquiry.raw_text[:1200]}")
            continue
        msg_text = "\n".join(f"{m.direction}/{m.sender_name or '-'}: {m.content[:800]}" for m in msgs)
        parts.append(f"询盘 {inquiry.inquiry_id} 买家={inquiry.buyer_name or '-'} 标题={inquiry.title or '-'}\n{msg_text}")
    docs = (
        db.query(AlibabaInquiryTrainingDoc)
        .filter(
            AlibabaInquiryTrainingDoc.user_id == user_id,
            or_(AlibabaInquiryTrainingDoc.account_id == account_id, AlibabaInquiryTrainingDoc.account_id.is_(None)),
        )
        .order_by(AlibabaInquiryTrainingDoc.created_at.desc())
        .limit(20)
        .all()
    )
    if docs:
        parts.append("用户上传资料/话术：\n" + "\n\n".join(f"{d.title}:\n{(d.content or '')[:3000]}" for d in docs))
    return "\n\n---\n\n".join(parts)[:60000]


def _strategy_source_for_ai(db: Session, user_id: int, account_id: int, doc_ids: Optional[List[int]] = None) -> Dict[str, Any]:
    inquiries = (
        db.query(AlibabaInquiry)
        .filter(AlibabaInquiry.user_id == user_id, AlibabaInquiry.account_id == account_id)
        .order_by(AlibabaInquiry.last_message_at.desc().nullslast(), AlibabaInquiry.updated_at.desc())
        .all()
    )
    parts: List[str] = []
    message_count = 0
    for inquiry in inquiries:
        msgs = (
            db.query(AlibabaInquiryMessage)
            .filter(AlibabaInquiryMessage.account_id == account_id, AlibabaInquiryMessage.inquiry_id == inquiry.inquiry_id)
            .order_by(AlibabaInquiryMessage.sent_at.asc().nullslast(), AlibabaInquiryMessage.id.asc())
            .all()
        )
        message_count += len(msgs)
        if not msgs and inquiry.raw_text:
            parts.append(f"询盘 {inquiry.inquiry_id} 买家={inquiry.buyer_name or '-'}\n列表摘要：{inquiry.raw_text[:1200]}")
            continue
        msg_text = "\n".join(f"{m.direction}/{m.sender_name or '-'}: {m.content[:800]}" for m in msgs)
        parts.append(f"询盘 {inquiry.inquiry_id} 买家={inquiry.buyer_name or '-'} 标题={inquiry.title or '-'}\n{msg_text}")
    selected_ids = [int(x) for x in (doc_ids or []) if str(x).isdigit() or isinstance(x, int)]
    docs: List[AlibabaInquiryTrainingDoc] = []
    if selected_ids:
        docs = (
            db.query(AlibabaInquiryTrainingDoc)
            .filter(
                AlibabaInquiryTrainingDoc.user_id == user_id,
                AlibabaInquiryTrainingDoc.id.in_(selected_ids),
                or_(AlibabaInquiryTrainingDoc.account_id == account_id, AlibabaInquiryTrainingDoc.account_id.is_(None)),
            )
            .order_by(AlibabaInquiryTrainingDoc.created_at.desc())
            .all()
        )
    if docs:
        parts.append("用户选择的产品资料/FAQ/话术资料：\n" + "\n\n".join(f"{d.title} ({d.kind}):\n{(d.content or '')[:5000]}" for d in docs))
    text = "\n\n---\n\n".join(parts)
    return {
        "text": text[:100000],
        "inquiry_count": len(inquiries),
        "message_count": message_count,
        "doc_count": len(docs),
        "doc_ids": [d.id for d in docs],
        "doc_titles": [d.title for d in docs],
        "truncated": len(text) > 100000,
    }


async def _call_llm_for_json(request: Request, prompt: str) -> Dict[str, Any]:
    auth = (request.headers.get("Authorization") or "").strip()
    raw_token = auth.split(" ", 1)[-1].strip() if auth.lower().startswith("bearer ") else auth
    asb = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    model = (
        getattr(settings, "lobster_orchestration_sutui_chat_model", "")
        or getattr(settings, "openclaw_default_model", "")
        or "deepseek-chat"
    )
    headers = {"Content-Type": "application/json"}
    url = ""
    if asb and raw_token:
        url = f"{asb}/api/sutui-chat/completions"
        headers["Authorization"] = f"Bearer {raw_token}"
        xi = (request.headers.get("X-Installation-Id") or "").strip()
        if xi:
            headers["X-Installation-Id"] = xi
    else:
        key = (os.environ.get("OPENAI_API_KEY") or os.environ.get("HAODUOMI_API_KEY") or os.environ.get("COMFLY_API_KEY") or "").strip()
        base = (os.environ.get("OPENAI_BASE_URL") or os.environ.get("HAODUOMI_OPENAI_BASE_URL") or os.environ.get("COMFLY_API_BASE") or "").strip().rstrip("/")
        if key and base:
            url = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
            headers["Authorization"] = f"Bearer {key}"
            model = os.environ.get("OPENAI_MODEL") or os.environ.get("HAODUOMI_MODEL") or model
    if not url:
        raise RuntimeError("未配置可用文本模型")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是外贸询盘销售主管，只返回严格 JSON，不要 Markdown。"},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "temperature": 0.35,
    }
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
        resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code >= 400:
        raise RuntimeError(f"LLM HTTP {resp.status_code}: {(resp.text or '')[:800]}")
    data = resp.json() if resp.content else {}
    content = ""
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        content = json.dumps(data, ensure_ascii=False)
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", content)
    raw = m.group(1) if m else content
    i, j = raw.find("{"), raw.rfind("}")
    if i >= 0 and j > i:
        raw = raw[i : j + 1]
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("LLM 未返回 JSON 对象")
    return parsed


def _fallback_summary(history: str) -> Dict[str, Any]:
    lines = [x.strip() for x in history.splitlines() if len(x.strip()) > 8]
    seller = [x.split(":", 1)[-1].strip() for x in lines if x.lower().startswith("seller/") or x.lower().startswith("out/")]
    buyer = [x.split(":", 1)[-1].strip() for x in lines if x.lower().startswith("buyer/") or x.lower().startswith("in/")]
    return {
        "overview": "AI 服务暂不可用，已根据本地历史做基础归纳。建议补充产品资料、报价规则、交期和售后政策后再生成正式话术。",
        "good_scripts": seller[:12],
        "negative_scripts": [],
        "buyer_questions": buyer[:12],
        "reply_rules": ["先确认需求和应用场景", "再给出产品匹配建议", "涉及价格、认证、交期时提醒人工确认"],
    }


@router.post("/api/alibaba-inquiries/accounts/{account_id}/analyze", summary="生成阿里询盘回复策略")
async def analyze_history(
    account_id: int,
    body: AnalyzeBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    source = _strategy_source_for_ai(db, current_user.id, account_id, body.doc_ids)
    history = str(source.get("text") or "")
    if not history.strip():
        raise HTTPException(400, detail="还没有询盘或资料可分析，请先同步询盘或上传话术资料")
    prompt = (
        "请分析下面阿里国际站历史询盘、客服回复和用户上传资料，输出 JSON：\n"
        "{overview:string, good_scripts:string[], negative_scripts:string[], buyer_questions:string[], reply_rules:string[]}。\n"
        "good_scripts 是值得复用的成交/推进话术；negative_scripts 是容易显得敷衍、冒犯、太营销、承诺过度或无效的话术；"
        "reply_rules 是后续 AI 回复必须遵守的规则。\n\n"
        "额外硬性要求：输出必须增加 safety_rules、doc_strategy、missing_info_questions、refusal_rules。"
        "不能幻觉，不能模拟不存在的数据，不能乱承诺，不能编造价格、库存、认证、交期、售后、公司能力。"
        "资料没有明确给出的内容，必须追问或提示人工确认。\n\n"
        f"{history}"
    )
    prompt += (
        "\n\nStrict output contract:\n"
        "Return only one JSON object with these keys: overview, history_analysis, good_scripts, "
        "negative_scripts, buyer_questions, reply_rules, doc_strategy, safety_rules, "
        "missing_info_questions, refusal_rules.\n"
        "The strategy is for real customer support. Never invent prices, MOQ, inventory, "
        "certifications, delivery time, warranty, company capability, case numbers, or test data. "
        "If selected product/FAQ docs do not explicitly contain an answer, the reply strategy must "
        "ask a follow-up question or route to human confirmation.\n"
        "Selected script/negative-script docs are style or anti-pattern references only. Do not treat "
        "them as product facts unless the same fact is explicitly present in product/FAQ material.\n"
    )
    try:
        data = await _call_llm_for_json(request, prompt)
    except Exception as exc:
        logger.warning("[ALIBABA-INQUIRY] LLM analyze fallback: %s", exc)
        data = _fallback_summary(history)
    data.setdefault("history_analysis", [])
    data.setdefault("doc_strategy", [])
    data.setdefault("safety_rules", ["不得编造价格、库存、认证、交期、售后或公司能力", "资料没有明确给出的内容必须追问或提示人工确认"])
    data.setdefault("missing_info_questions", ["请确认采购数量、应用场景、目标国家/地区、交期要求和是否需要认证。"])
    data.setdefault("refusal_rules", ["不能确认的信息不要替客户下结论；不能替公司承诺未在资料中出现的能力。"])
    def _list_field(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        text = str(value or "").strip()
        return [text] if text else []

    data["history_analysis"] = _list_field(data.get("history_analysis")) or _list_field(data.get("overview"))
    data["good_scripts"] = _list_field(data.get("good_scripts"))
    data["negative_scripts"] = _list_field(data.get("negative_scripts"))
    data["buyer_questions"] = _list_field(data.get("buyer_questions"))
    data["reply_rules"] = _list_field(data.get("reply_rules"))
    data["doc_strategy"] = _list_field(data.get("doc_strategy"))
    data["safety_rules"] = _list_field(data.get("safety_rules")) or [
        "不得编造价格、库存、认证、交期、售后或公司能力。",
        "资料没有明确给出的内容，必须追问或提示人工确认。",
    ]
    data["missing_info_questions"] = _list_field(data.get("missing_info_questions")) or [
        "请确认采购数量、应用场景、目标国家/地区、交期要求，以及是否需要认证。",
    ]
    data["refusal_rules"] = _list_field(data.get("refusal_rules")) or [
        "不能确认的信息不要替客户下结论；不能替公司承诺资料中没有出现的能力。",
    ]
    meta = dict(data)
    meta.update(
        {
            "enabled": False,
            "source": {
                "mode": "full",
                "inquiry_count": source.get("inquiry_count") or 0,
                "message_count": source.get("message_count") or 0,
                "doc_count": source.get("doc_count") or 0,
                "doc_ids": source.get("doc_ids") or [],
                "doc_titles": source.get("doc_titles") or [],
                "truncated": bool(source.get("truncated")),
            },
            "safety_contract": "不得幻觉、不得模拟数据、不得编造价格/库存/认证/交期/售后/公司能力；缺资料必须追问或人工确认。",
        }
    )
    content = json.dumps(meta, ensure_ascii=False, indent=2)
    row = AlibabaInquiryPhraseSummary(
        user_id=current_user.id,
        account_id=account_id,
        summary_type="strategy",
        content=content,
        source_count=int(source.get("inquiry_count") or 0),
        meta=meta,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "summary": _serialize_summary(row)}


def _serialize_summary(row: AlibabaInquiryPhraseSummary) -> Dict[str, Any]:
    meta = row.meta or {}
    return {
        "id": row.id,
        "account_id": row.account_id,
        "summary_type": row.summary_type,
        "content": row.content,
        "meta": meta,
        "enabled": bool(meta.get("enabled")),
        "source_count": row.source_count,
        "created_at": _dt(row.created_at),
    }


def _active_summary(db: Session, user_id: int, account_id: int) -> Optional[AlibabaInquiryPhraseSummary]:
    rows = (
        db.query(AlibabaInquiryPhraseSummary)
        .filter(
            AlibabaInquiryPhraseSummary.user_id == user_id,
            AlibabaInquiryPhraseSummary.account_id == account_id,
            AlibabaInquiryPhraseSummary.summary_type == "strategy",
        )
        .order_by(AlibabaInquiryPhraseSummary.created_at.desc())
        .all()
    )
    for row in rows:
        meta = row.meta if isinstance(row.meta, dict) else {}
        if meta.get("enabled"):
            return row
    return None


@router.get("/api/alibaba-inquiries/accounts/{account_id}/summaries", summary="话术策略列表")
def list_summaries(
    account_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    rows = (
        db.query(AlibabaInquiryPhraseSummary)
        .filter(
            AlibabaInquiryPhraseSummary.user_id == current_user.id,
            or_(AlibabaInquiryPhraseSummary.account_id == account_id, AlibabaInquiryPhraseSummary.account_id.is_(None)),
        )
        .order_by(AlibabaInquiryPhraseSummary.created_at.desc())
        .all()
    )
    return {"ok": True, "items": [_serialize_summary(x) for x in rows]}


@router.post("/api/alibaba-inquiries/accounts/{account_id}/summaries/{summary_id}/enable", summary="启用阿里询盘回复策略")
def enable_summary(
    account_id: int,
    summary_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = _account_or_404(db, current_user.id, account_id)
    rows = (
        db.query(AlibabaInquiryPhraseSummary)
        .filter(
            AlibabaInquiryPhraseSummary.user_id == current_user.id,
            AlibabaInquiryPhraseSummary.account_id == account_id,
            AlibabaInquiryPhraseSummary.summary_type == "strategy",
        )
        .all()
    )
    target = None
    for row in rows:
        if int(row.id) == int(summary_id):
            target = row
    if not target:
        raise HTTPException(404, detail="策略不存在")
    now_text = _now().isoformat()
    for row in rows:
        meta = dict(row.meta or {})
        meta["enabled"] = int(row.id) == int(summary_id)
        if meta["enabled"]:
            meta["enabled_at"] = now_text
        row.meta = meta
        row.updated_at = _now()
    acct.auto_reply_enabled = True
    acct.updated_at = _now()
    db.commit()
    db.refresh(target)
    db.refresh(acct)
    return {"ok": True, "summary": _serialize_summary(target), "account": _serialize_account(db, acct)}


@router.get("/api/alibaba-inquiries/accounts/{account_id}/auto-reply/config", summary="Alibaba inquiry auto reply config")
def get_auto_reply_config(
    account_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = _account_or_404(db, current_user.id, account_id)
    active = _active_summary(db, current_user.id, account_id)
    return {
        "ok": True,
        "enabled": bool(getattr(acct, "auto_reply_enabled", False)),
        "active_strategy": _serialize_summary(active) if active else None,
        "account": _serialize_account(db, acct),
    }


@router.post("/api/alibaba-inquiries/accounts/{account_id}/auto-reply/config", summary="Set Alibaba inquiry auto reply config")
def set_auto_reply_config(
    account_id: int,
    body: AutoReplyConfigBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = _account_or_404(db, current_user.id, account_id)
    active = _active_summary(db, current_user.id, account_id)
    if body.enabled and not active:
        raise HTTPException(400, detail="请先生成并启用一条回复策略，再开启自动接管。")
    acct.auto_reply_enabled = bool(body.enabled)
    if not body.enabled:
        rows = (
            db.query(AlibabaInquiryPhraseSummary)
            .filter(
                AlibabaInquiryPhraseSummary.user_id == current_user.id,
                AlibabaInquiryPhraseSummary.account_id == account_id,
                AlibabaInquiryPhraseSummary.summary_type == "strategy",
            )
            .all()
        )
        for row in rows:
            meta = dict(row.meta or {})
            if meta.get("enabled"):
                meta["enabled"] = False
                meta["disabled_at"] = _now().isoformat()
                row.meta = meta
                row.updated_at = _now()
        active = None
    acct.updated_at = _now()
    db.commit()
    db.refresh(acct)
    return {
        "ok": True,
        "enabled": bool(getattr(acct, "auto_reply_enabled", False)),
        "active_strategy": _serialize_summary(active) if active else None,
        "account": _serialize_account(db, acct),
    }


@router.post("/api/alibaba-inquiries/accounts/{account_id}/reply/draft", summary="生成询盘回复草稿")
async def draft_reply(
    account_id: int,
    body: ReplyDraftBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    inquiry = (
        db.query(AlibabaInquiry)
        .filter(
            AlibabaInquiry.user_id == current_user.id,
            AlibabaInquiry.account_id == account_id,
            AlibabaInquiry.inquiry_id == body.inquiry_id,
        )
        .first()
    )
    if not inquiry:
        raise HTTPException(404, detail="询盘不存在")
    detail = get_inquiry_detail(account_id, body.inquiry_id, current_user, db)
    archive = _archive_for_inquiry(db, account_id, body.inquiry_id)
    archive_payload = _serialize_archive(archive, db) if archive else None
    active_strategy = _active_summary(db, current_user.id, account_id)
    strategy_payload = active_strategy.meta if active_strategy and isinstance(active_strategy.meta, dict) else {}
    strategy_source = strategy_payload.get("source") if isinstance(strategy_payload.get("source"), dict) else {}
    strategy_doc_ids = [int(x) for x in (strategy_source.get("doc_ids") or []) if str(x).isdigit() or isinstance(x, int)]
    docs = []
    if strategy_doc_ids:
        docs = (
            db.query(AlibabaInquiryTrainingDoc)
            .filter(
                AlibabaInquiryTrainingDoc.user_id == current_user.id,
                AlibabaInquiryTrainingDoc.id.in_(strategy_doc_ids),
                or_(AlibabaInquiryTrainingDoc.account_id == account_id, AlibabaInquiryTrainingDoc.account_id.is_(None)),
            )
            .order_by(AlibabaInquiryTrainingDoc.created_at.desc())
            .all()
        )
    prompt = (
        "请给阿里国际站询盘写一条英文回复，只返回 JSON：{reply:string, reason:string, risk:string}。\n"
        "要求：自然、专业、不过度承诺、不催促成交；如果信息不足，先追问关键需求。\n"
        f"额外指令：{body.instruction}\n\n"
        f"询盘：{json.dumps(detail, ensure_ascii=False)[:30000]}\n\n"
        f"客户档案：{json.dumps(archive_payload or {'enabled': False, 'warning': '暂无已补全客户档案'}, ensure_ascii=False)[:20000]}\n\n"
        f"启用回复策略：{json.dumps(strategy_payload or {'enabled': False, 'warning': '未启用回复策略'}, ensure_ascii=False)[:18000]}\n\n"
        f"用户资料：{json.dumps([{ 'title': d.title, 'content': (d.content or '')[:3000]} for d in docs], ensure_ascii=False)[:20000]}"
    )
    prompt += (
        "\n\n安全边界：只能依据当前询盘、客户档案中有证据的事实、启用策略、选中资料/FAQ回复。"
        "不得编造价格、库存、认证、交期、售后、公司能力；缺少资料时必须追问或建议人工确认。"
    )
    try:
        data = await _call_llm_for_json(request, prompt)
        reply = str(data.get("reply") or "").strip()
        if not reply:
            raise RuntimeError("reply empty")
    except Exception as exc:
        logger.warning("[ALIBABA-INQUIRY] draft fallback: %s", exc)
        last_msg = ""
        for msg in reversed(detail.get("messages") or []):
            if msg.get("direction") != "seller" and msg.get("content"):
                last_msg = msg["content"]
                break
        reply = (
            "Hello, thank you for your message. Could you please share more details about your required quantity, "
            "application scenario, and any specific requirements? Then we can recommend the most suitable solution for you."
        )
        data = {"reply": reply, "reason": f"基于最近买家消息生成兜底回复：{last_msg[:120]}", "risk": "AI 服务不可用，建议人工确认后发送"}
    return {"ok": True, "draft": data}


async def _send_reply_via_page(page: Any, content: str) -> Dict[str, Any]:
    selectors = [
        "textarea",
        "[contenteditable='true']",
        "[role='textbox']",
        ".sendbox textarea",
        ".chat-input textarea",
        ".next-input textarea",
    ]
    input_handle = None
    for selector in selectors:
        try:
            loc = page.locator(selector).last
            if await loc.count():
                input_handle = loc
                break
        except Exception:
            continue
    if input_handle is None:
        return {"ok": False, "error": "未找到回复输入框"}
    try:
        await input_handle.click(timeout=8000)
        await asyncio.sleep(random.uniform(0.3, 0.7))
        await input_handle.type(content, delay=random.randint(35, 95))
    except Exception:
        try:
            await input_handle.fill(content, timeout=8000)
        except Exception as exc:
            return {"ok": False, "error": f"输入回复失败：{exc}"}

    button_selectors = [
        "button:has-text('Send')",
        "button:has-text('发送')",
        "button:has-text('Reply')",
        "button:has-text('回复')",
        ".send-btn",
        ".next-btn-primary",
    ]
    for selector in button_selectors:
        try:
            btn = page.locator(selector).last
            if await btn.count():
                await asyncio.sleep(random.uniform(0.5, 1.2))
                await btn.click(timeout=8000)
                return {"ok": True}
        except Exception:
            continue
    try:
        await page.keyboard.press("Control+Enter")
        return {"ok": True, "used_hotkey": True}
    except Exception as exc:
        return {"ok": False, "error": f"未找到发送按钮：{exc}"}


@router.post("/api/alibaba-inquiries/accounts/{account_id}/reply/send", summary="发送询盘回复")
async def send_reply(
    account_id: int,
    body: ReplySendBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = _account_or_404(db, current_user.id, account_id)
    inquiry = (
        db.query(AlibabaInquiry)
        .filter(
            AlibabaInquiry.user_id == current_user.id,
            AlibabaInquiry.account_id == account_id,
            AlibabaInquiry.inquiry_id == body.inquiry_id,
        )
        .first()
    )
    if not inquiry:
        raise HTTPException(404, detail="询盘不存在")
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, detail="回复内容不能为空")
    lock = await _account_lock(account_id, "reply")
    async with lock:
        page = await _get_account_page(acct, visible=True)
        await _goto(page, inquiry.source_url or f"https://message.alibaba.com/message/maDetail.htm?imInquiryId={inquiry.inquiry_id}")
        if body.dry_run:
            return {"ok": True, "dry_run": True, "message": "已打开询盘详情，未发送"}
        result = await _send_reply_via_page(page, content)
        if not result.get("ok"):
            return {"ok": False, "message": result.get("error") or "发送失败"}
        uid = hashlib.sha1(f"manual\0{_now().isoformat()}\0{content}".encode("utf-8")).hexdigest()[:40]
        db.add(
            AlibabaInquiryMessage(
                user_id=current_user.id,
                account_id=account_id,
                inquiry_id=inquiry.inquiry_id,
                message_uid=uid,
                direction="seller",
                sender_name="me",
                content=content,
                msg_type="text",
                sent_at=_now(),
                raw={"source": "manual_send"},
            )
        )
        db.commit()
        return {"ok": True, "message": "回复已发送"}
