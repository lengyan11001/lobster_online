"""Alibaba International inquiry takeover workbench."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .auth import _ServerUser, get_current_user_for_local
from ..core.config import settings
from ..db import get_db
from ..models import (
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
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALIBABA_INQUIRY_LIST_URL = (
    "https://message.alibaba.com/message/default.htm"
    "?spm=a2700.7756200.0.0.4f8b71d229DykT#feedback/all"
)
ALIBABA_INQUIRY_CREATE_URL = "https://message.alibaba.com/message/default.htm#feedback/all"
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 80
_account_sync_locks: Dict[int, asyncio.Lock] = {}
_account_reply_locks: Dict[int, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


class CreateAccountBody(BaseModel):
    nickname: str = Field(default="阿里国际站账号", max_length=128)


class SyncBody(BaseModel):
    max_scrolls: int = Field(default=180, ge=1, le=1000)
    stop_after_idle_rounds: int = Field(default=8, ge=2, le=30)
    sync_details: bool = False
    detail_limit: int = Field(default=0, ge=0, le=2000)


class AnalyzeBody(BaseModel):
    sample_limit: int = Field(default=120, ge=10, le=1000)


class ReplyDraftBody(BaseModel):
    inquiry_id: str
    instruction: str = ""


class ReplySendBody(BaseModel):
    inquiry_id: str
    content: str
    dry_run: bool = False


def _now() -> datetime:
    return datetime.utcnow()


def _dt(value: Any) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _safe_name(value: str, fallback: str = "account") -> str:
    text = re.sub(r"[^\w\-.一-龥]+", "_", str(value or "").strip(), flags=re.UNICODE).strip("._")
    return (text or fallback)[:80]


def _compact(text: Any, limit: int = 500) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) > limit:
        return value[: limit - 1] + "…"
    return value


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
    return opts


async def _get_account_page(acct: AlibabaInquiryAccount, *, visible: bool = True):
    from publisher.browser_pool import (
        _acquire_context,
        _ensure_visible_interactive_context,
        _get_page_with_reacquire,
        _setup_auto_close,
    )

    profile_dir = acct.browser_profile or str(_BROWSER_DATA_DIR / f"alibaba_inquiry_{acct.user_id}_{acct.id}")
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
    lower = f"{url}\n{text}".lower()
    login_words = ["login.alibaba", "passport.alibaba", "sign in", "扫码", "登录", "password"]
    logged_in = bool(anchors) or ("message.alibaba.com/message" in lower and "feedback" in lower and not any(x in lower for x in login_words[:2]))
    if any(x in lower for x in login_words) and not anchors:
        logged_in = False
    return {"logged_in": logged_in, "url": url, "anchors": int(anchors or 0)}


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


async def _scroll_collect_all(page: Any, max_scrolls: int, idle_rounds: int) -> Dict[str, Any]:
    await _goto(page, ALIBABA_INQUIRY_LIST_URL)
    await asyncio.sleep(1.5)
    state = await _page_login_state(page)
    if not state.get("logged_in"):
        return {"ok": False, "need_login": True, "rows": [], "message": "未登录，请先扫码登录阿里国际站账号"}

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
        await asyncio.sleep(random.uniform(0.45, 0.95))
    rows = list(seen.values())
    rows.sort(key=lambda x: x.get("last_message_at") or datetime.min, reverse=True)
    return {"ok": True, "need_login": False, "rows": rows, "scroll_rounds": round_index + 1}


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


async def _extract_detail(page: Any, inquiry: AlibabaInquiry) -> Dict[str, Any]:
    url = inquiry.source_url or f"https://message.alibaba.com/message/maDetail.htm?imInquiryId={inquiry.inquiry_id}"
    await _goto(page, url)
    await asyncio.sleep(random.uniform(1.2, 2.0))
    text = await page.evaluate("() => (document.body && document.body.innerText || '')")
    parsed = _parse_detail_text(text, inquiry)
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
    parsed["profile"]["avatar_url"] = avatar_url or None
    parsed["profile"]["raw"] = {"url": page.url}
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


def _upsert_detail(db: Session, user_id: int, account_id: int, inquiry: AlibabaInquiry, detail: Dict[str, Any]) -> Dict[str, int]:
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

    inserted = 0
    for msg in detail.get("messages") or []:
        uid = str(msg.get("message_uid") or "").strip()
        if not uid:
            uid = hashlib.sha1(json.dumps(msg, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:40]
        exists = (
            db.query(AlibabaInquiryMessage)
            .filter(
                AlibabaInquiryMessage.account_id == account_id,
                AlibabaInquiryMessage.inquiry_id == inquiry.inquiry_id,
                AlibabaInquiryMessage.message_uid == uid,
            )
            .first()
        )
        if exists:
            continue
        db.add(
            AlibabaInquiryMessage(
                user_id=user_id,
                account_id=account_id,
                inquiry_id=inquiry.inquiry_id,
                message_uid=uid,
                direction=str(msg.get("direction") or "unknown")[:16],
                sender_name=(msg.get("sender_name") or "")[:255],
                content=str(msg.get("content") or "")[:8000],
                msg_type=str(msg.get("msg_type") or "text")[:32],
                sent_at=msg.get("sent_at"),
                raw=msg.get("raw") if isinstance(msg.get("raw"), dict) else None,
            )
        )
        inserted += 1
    inquiry.updated_at = _now()
    return {"messages_inserted": inserted, "profile_saved": 1}


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
        db.query(func.count(AlibabaCustomerProfile.id))
        .filter(AlibabaCustomerProfile.account_id == acct.id)
        .scalar()
        or 0
    )
    return {
        "id": acct.id,
        "nickname": acct.nickname,
        "status": acct.status,
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


@router.post("/api/alibaba-inquiries/accounts/{account_id}/login", summary="打开阿里国际站登录页")
async def open_login(
    account_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = _account_or_404(db, current_user.id, account_id)
    try:
        page = await _get_account_page(acct, visible=True)
        await _goto(page, ALIBABA_INQUIRY_LIST_URL, timeout_ms=120000)
        acct.status = "pending"
        acct.last_error = None
        db.commit()
        return {"ok": True, "message": "已打开阿里国际站询盘页面，请在浏览器里扫码/登录"}
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
        await _goto(page, ALIBABA_INQUIRY_LIST_URL, timeout_ms=90000)
        state = await _page_login_state(page)
        acct.status = "active" if state.get("logged_in") else "pending"
        if state.get("logged_in"):
            acct.last_login = _now()
        acct.last_error = None
        db.commit()
        return {"ok": True, "logged_in": bool(state.get("logged_in")), "status": acct.status, "detail": state}
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
            )
            if result.get("need_login"):
                acct.status = "pending"
                acct.sync_status = "failed"
                acct.sync_progress = "未登录"
                acct.last_error = result.get("message") or "未登录"
                db.commit()
                return {"ok": False, "need_login": True, "message": acct.last_error, "account": _serialize_account(db, acct)}
            rows = result.get("rows") or []
            counts = _upsert_inquiries(db, current_user.id, account_id, rows)
            acct.status = "active"
            acct.last_login = acct.last_login or _now()
            acct.last_sync_at = _now()
            acct.sync_progress = f"已同步列表 {len(rows)} 条"
            detail_counts = {"details_synced": 0, "messages_inserted": 0}
            db.commit()

            if body.sync_details and rows:
                q = (
                    db.query(AlibabaInquiry)
                    .filter(AlibabaInquiry.account_id == account_id)
                    .order_by(AlibabaInquiry.last_message_at.desc().nullslast(), AlibabaInquiry.updated_at.desc())
                )
                detail_rows = q.limit(body.detail_limit).all() if body.detail_limit else q.all()
                for idx, inquiry in enumerate(detail_rows, start=1):
                    acct.sync_progress = f"正在同步详情 {idx}/{len(detail_rows)}"
                    db.commit()
                    try:
                        detail = await _extract_detail(page, inquiry)
                        dc = _upsert_detail(db, current_user.id, account_id, inquiry, detail)
                        detail_counts["details_synced"] += 1
                        detail_counts["messages_inserted"] += dc.get("messages_inserted", 0)
                        db.commit()
                        await asyncio.sleep(random.uniform(0.6, 1.4))
                    except Exception as exc:
                        logger.warning("[ALIBABA-INQUIRY] detail sync failed inquiry=%s err=%s", inquiry.inquiry_id, exc)
                        db.rollback()
            acct.sync_status = "idle"
            acct.sync_progress = f"同步完成：列表 {len(rows)} 条，新增 {counts['created']} 条，更新 {counts['updated']} 条"
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
                **detail_counts,
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
    items = []
    for row in rows:
        item = _serialize_inquiry(row)
        if row.inquiry_id in profiles:
            item["profile"] = profiles[row.inquiry_id]
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
        return {"ok": True, "message": "详情已同步", **counts}
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
    file: UploadFile = File(...),
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    filename = _safe_name(file.filename or "training.txt", "training.txt")
    suffix = Path(filename).suffix
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    account_dir = _UPLOAD_DIR / str(current_user.id) / str(account_id)
    account_dir.mkdir(parents=True, exist_ok=True)
    path = account_dir / f"{stamp}_{hashlib.sha1(filename.encode()).hexdigest()[:8]}{suffix or '.dat'}"
    content = await file.read()
    path.write_bytes(content)
    extracted = _extract_text_from_file(path, filename)
    doc = AlibabaInquiryTrainingDoc(
        user_id=current_user.id,
        account_id=account_id,
        title=(title or file.filename or filename)[:255],
        kind=(kind or "script")[:32],
        filename=file.filename or filename,
        content=extracted,
        file_path=str(path),
        meta={"size": len(content), "content_type": file.content_type or ""},
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


def _history_for_ai(db: Session, user_id: int, account_id: int, sample_limit: int) -> str:
    inquiries = (
        db.query(AlibabaInquiry)
        .filter(AlibabaInquiry.user_id == user_id, AlibabaInquiry.account_id == account_id)
        .order_by(AlibabaInquiry.last_message_at.desc().nullslast(), AlibabaInquiry.updated_at.desc())
        .limit(sample_limit)
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


@router.post("/api/alibaba-inquiries/accounts/{account_id}/analyze", summary="AI总结历史询盘话术")
async def analyze_history(
    account_id: int,
    body: AnalyzeBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    history = _history_for_ai(db, current_user.id, account_id, body.sample_limit)
    if not history.strip():
        raise HTTPException(400, detail="还没有询盘或资料可分析，请先同步询盘或上传话术资料")
    prompt = (
        "请分析下面阿里国际站历史询盘、客服回复和用户上传资料，输出 JSON：\n"
        "{overview:string, good_scripts:string[], negative_scripts:string[], buyer_questions:string[], reply_rules:string[]}。\n"
        "good_scripts 是值得复用的成交/推进话术；negative_scripts 是容易显得敷衍、冒犯、太营销、承诺过度或无效的话术；"
        "reply_rules 是后续 AI 回复必须遵守的规则。\n\n"
        f"{history}"
    )
    try:
        data = await _call_llm_for_json(request, prompt)
    except Exception as exc:
        logger.warning("[ALIBABA-INQUIRY] LLM analyze fallback: %s", exc)
        data = _fallback_summary(history)
    content = json.dumps(data, ensure_ascii=False, indent=2)
    row = AlibabaInquiryPhraseSummary(
        user_id=current_user.id,
        account_id=account_id,
        summary_type="history",
        content=content,
        source_count=int(body.sample_limit),
        meta=data,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "summary": _serialize_summary(row)}


def _serialize_summary(row: AlibabaInquiryPhraseSummary) -> Dict[str, Any]:
    return {
        "id": row.id,
        "account_id": row.account_id,
        "summary_type": row.summary_type,
        "content": row.content,
        "meta": row.meta or {},
        "source_count": row.source_count,
        "created_at": _dt(row.created_at),
    }


@router.get("/api/alibaba-inquiries/accounts/{account_id}/summaries", summary="话术总结列表")
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
    summaries = (
        db.query(AlibabaInquiryPhraseSummary)
        .filter(AlibabaInquiryPhraseSummary.user_id == current_user.id, AlibabaInquiryPhraseSummary.account_id == account_id)
        .order_by(AlibabaInquiryPhraseSummary.created_at.desc())
        .limit(3)
        .all()
    )
    docs = (
        db.query(AlibabaInquiryTrainingDoc)
        .filter(AlibabaInquiryTrainingDoc.user_id == current_user.id, AlibabaInquiryTrainingDoc.account_id == account_id)
        .order_by(AlibabaInquiryTrainingDoc.created_at.desc())
        .limit(8)
        .all()
    )
    prompt = (
        "请给阿里国际站询盘写一条英文回复，只返回 JSON：{reply:string, reason:string, risk:string}。\n"
        "要求：自然、专业、不过度承诺、不催促成交；如果信息不足，先追问关键需求。\n"
        f"额外指令：{body.instruction}\n\n"
        f"询盘：{json.dumps(detail, ensure_ascii=False)[:30000]}\n\n"
        f"话术总结：{json.dumps([s.meta or s.content for s in summaries], ensure_ascii=False)[:10000]}\n\n"
        f"用户资料：{json.dumps([{ 'title': d.title, 'content': (d.content or '')[:3000]} for d in docs], ensure_ascii=False)[:20000]}"
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
