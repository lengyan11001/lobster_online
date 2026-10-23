"""TikTok browser lead acquisition endpoints (local CDP browser)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .auth import _ServerUser, get_current_user_for_local
from ..db import get_db
from ..models import PublishAccount
from publisher.browser_pool import _ensure_tiktok_cdp_context
from skills.tiktok_leads.driver import TikTokLeadsDriver

router = APIRouter()
_lock = asyncio.Lock()
_BASE_DIR = Path(__file__).resolve().parents[3]


class TikTokBrowserSearchBody(BaseModel):
    keyword: str = Field("", max_length=160)
    limit: int = Field(20, ge=1, le=50)


class TikTokBrowserCommentsBody(BaseModel):
    video_url: str = Field("", max_length=1200)
    max_comments: int = Field(100, ge=1, le=500)
    intent_prompt: str = Field("", max_length=1000)
    max_leads: int = Field(100, ge=1, le=500)


def _tiktok_account(db: Session, user_id: int) -> PublishAccount:
    account = (
        db.query(PublishAccount)
        .filter(PublishAccount.user_id == int(user_id), PublishAccount.platform == "tiktok")
        .order_by(PublishAccount.id.asc())
        .first()
    )
    if account is None:
        raise HTTPException(status_code=400, detail="请先在发布中心添加并登录 TikTok 账号")
    return account


def _profile_dir(account: PublishAccount) -> str:
    value = str(account.browser_profile or "").strip()
    if value:
        return value
    return str(_BASE_DIR / "browser_data" / ("tiktok_" + str(account.nickname or "default")))


async def _page_for(account: PublishAccount):
    profile = _profile_dir(account)
    ctx = await _ensure_tiktok_cdp_context(profile, browser_options={"tiktok_cdp": True})
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    return page, profile


@router.get("/api/social-leads/tiktok/browser/status")
async def tiktok_browser_status(
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    account = _tiktok_account(db, current_user.id)
    return {"ok": True, "account_id": account.id, "nickname": account.nickname, "status": account.status, "profile": _profile_dir(account)}


@router.post("/api/social-leads/tiktok/browser/search")
async def tiktok_browser_search(
    body: TikTokBrowserSearchBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    keyword = str(body.keyword or "").strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="请输入搜索关键词")
    account = _tiktok_account(db, current_user.id)
    async with _lock:
        page, profile = await _page_for(account)
        videos = await TikTokLeadsDriver().search_videos(page, keyword, body.limit)
    return {"ok": True, "keyword": keyword, "videos": videos, "total": len(videos), "profile": profile}


@router.post("/api/social-leads/tiktok/browser/comments")
async def tiktok_browser_comments(
    body: TikTokBrowserCommentsBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    video_url = str(body.video_url or "").strip()
    if not video_url:
        raise HTTPException(status_code=400, detail="请先选择一个 TikTok 视频")
    account = _tiktok_account(db, current_user.id)
    driver = TikTokLeadsDriver()
    async with _lock:
        page, profile = await _page_for(account)
        comments = await driver.collect_comments(page, video_url, body.max_comments)
    leads = driver.score_leads(comments, body.intent_prompt, body.max_leads)
    return {
        "ok": True,
        "video_url": video_url,
        "comments": comments,
        "leads": leads,
        "total_comments": len(comments),
        "total_leads": len(leads),
        "profile": profile,
    }
