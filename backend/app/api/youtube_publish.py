"""YouTube Data API：多账号（每账号独立 OAuth 客户端 + 代理 + Refresh Token），会话中按 account_id 指定上传。"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Literal, Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import get_db, SessionLocal
from ..models import Asset, YoutubePublishSchedule, YoutubeUploadJob
from publisher.browser_pool import _find_chrome_executable

from ..services.youtube_api_upload import (
    build_httplib2_http_for_proxy,
    upload_local_video_file,
    youtube_httpx_client_kwargs,
)
from ..services.youtube_analytics import sync_youtube_account_data
from .auth import _ServerUser, get_current_user_for_local

router = APIRouter()
logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

_oauth_states: Dict[str, Dict[str, Any]] = {}
_OAUTH_STATE_TTL_SEC = 600
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
YT_ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
YOUTUBE_FULL_SCOPE = " ".join([YOUTUBE_UPLOAD_SCOPE, YOUTUBE_READONLY_SCOPE, YT_ANALYTICS_SCOPE])


def _public_base_url() -> str:
    base = (getattr(settings, "public_base_url", None) or "").strip().rstrip("/")
    if base:
        return base
    return f"http://127.0.0.1:{int(getattr(settings, 'port', 8000) or 8000)}"


def _oauth_callback_url() -> str:
    return f"{_public_base_url()}/api/youtube-publish/oauth/callback"


def _prune_oauth_states() -> None:
    now = time.time()
    dead = [k for k, v in _oauth_states.items() if v.get("expires", 0) < now]
    for k in dead:
        del _oauth_states[k]


def _accounts_path(user_id: int) -> Path:
    return _BASE_DIR / f"youtube_accounts_{user_id}.json"


def _load_doc(user_id: int) -> Dict[str, Any]:
    path = _accounts_path(user_id)
    if not path.is_file():
        return {"accounts": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[youtube-publish] 读取账号文件失败 user_id=%s err=%s", user_id, e)
        raise HTTPException(status_code=500, detail="YouTube 账号数据损坏")
    if not isinstance(data, dict):
        return {"accounts": {}}
    if not isinstance(data.get("accounts"), dict):
        data["accounts"] = {}
    return data


def _save_doc(user_id: int, data: Dict[str, Any]) -> None:
    path = _accounts_path(user_id)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _new_account_id() -> str:
    return "yt_" + uuid.uuid4().hex[:12]


def _mask_proxy(url: Optional[str]) -> str:
    if not url or not str(url).strip():
        return ""
    s = str(url).strip()
    if len(s) <= 12:
        return "*" * len(s)
    return s[:6] + "…" + s[-6:]


def _mask_client_id(cid: Optional[str]) -> str:
    s = (cid or "").strip()
    if not s:
        return ""
    if len(s) <= 20:
        return s[:6] + "…"
    return s[:12] + "…" + s[-10:]


def _mark_account_error(user_id: int, account_id: str, message: str) -> None:
    try:
        doc = _load_doc(user_id)
        accounts = doc.get("accounts") if isinstance(doc.get("accounts"), dict) else {}
        if isinstance(accounts.get(account_id), dict):
            accounts[account_id]["status"] = "error"
            accounts[account_id]["last_error"] = message
            _save_doc(user_id, doc)
    except Exception as e:
        logger.warning(
            "[youtube-publish] 标记账号错误失败 user_id=%s account_id=%s err=%s",
            user_id,
            account_id,
            e,
        )


def _oauth_error_page(message: str, detail: str = "", status_code: int = 502) -> HTMLResponse:
    detail_html = ""
    if detail:
        detail_html = (
            "<pre style=\"white-space:pre-wrap;background:#f7f7f7;padding:.75rem;border-radius:.5rem;\">"
            f"{html.escape(detail[:2000])}</pre>"
        )
    return HTMLResponse(
        content="<html><head><meta charset=\"utf-8\"/></head>"
        "<body style=\"font-family:sans-serif;padding:1.5rem;line-height:1.6;\">"
        f"<p><strong>授权未完成</strong></p><p>{html.escape(message)}</p>"
        "<p>请关闭本页，回到龙虾 YouTube 账号列表重新点击「浏览器授权」。"
        "如果账号配置了代理，请确认代理可访问 Google OAuth。</p>"
        f"{detail_html}</body></html>",
        status_code=status_code,
    )


def _validate_proxy_url(ps: str) -> None:
    if not ps.strip():
        return
    from urllib.parse import urlparse

    u = urlparse(ps)
    if u.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400,
            detail="代理地址须为 http:// 或 https:// 开头，例如 http://静态IP:端口",
        )
    if not u.hostname:
        raise HTTPException(status_code=400, detail="代理 URL 无效")


class YoutubeAccountOut(BaseModel):
    account_id: str
    label: str = ""
    status: str
    oauth_client_id_masked: str = ""
    # 编辑表单用：完整 Client ID（仅本机列表接口返回）
    oauth_client_id: str = ""
    has_refresh_token: bool = False
    proxy_server_masked: str = ""
    proxy_server: str = ""  # 完整代理 URL（编辑表单用）
    proxy_has_auth: bool = False
    proxy_username: str = ""  # 代理用户名（密码不回显）
    last_error: str = ""
    created_at: str = ""
    oauth_redirect_uri: str = ""


class YoutubeAccountCreateIn(BaseModel):
    label: str = ""
    oauth_client_id: str = Field(..., min_length=1)
    oauth_client_secret: str = Field(..., min_length=1)
    proxy_server: str = ""
    proxy_username: str = ""
    proxy_password: str = ""


class YoutubeAccountPatchIn(BaseModel):
    label: Optional[str] = None
    oauth_client_id: Optional[str] = None
    oauth_client_secret: Optional[str] = None
    proxy_server: Optional[str] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None


class YoutubePublishUploadBody(BaseModel):
    account_id: str = Field(..., min_length=1, description="YouTube 账号 ID（yt_ 开头，见账号列表）")
    asset_id: str = Field(..., min_length=1, description="素材库 asset_id")
    title: str = Field("", max_length=5000)
    description: str = ""
    privacy_status: Literal["private", "unlisted", "public"] = "public"
    # 素材来源：决定未显式指定 contains_synthetic_media 时的默认披露
    material_origin: Literal["ai_generated", "script_batch"] = Field(
        "script_batch",
        description="ai_generated=AI 生成视频；script_batch=脚本/模板批量产出（非整段 AI 成片时可仍传 script_batch 并显式改 contains_synthetic_media）",
    )
    # 与 YouTube Data API status.selfDeclaredMadeForKids 一致；未填时默认 False
    self_declared_made_for_kids: bool = False
    # 与 status.containsSyntheticMedia 一致；省略时：ai_generated→true，script_batch→false
    contains_synthetic_media: Optional[bool] = None
    # 与 snippet.categoryId 一致，见 https://developers.google.com/youtube/v3/docs/videoCategories/list
    category_id: str = Field("22", description="视频分类 ID，默认 22（People & Blogs）")
    tags: Optional[list[str]] = Field(None, description="可选标签列表，须符合 YouTube 标签长度规则")
    # P0：自定义封面（图片素材 ID）
    thumbnail_asset_id: Optional[str] = Field(None, description="封面图素材 asset_id（可选）")
    # P1：YouTube 原生定时发布（ISO 8601；设置时必须 private）
    publish_at: Optional[str] = Field(None, description="定时发布时间，如 2026-12-01T10:00:00Z")


def _validate_upload_fields(
    body: "YoutubePublishUploadBody",
    asset: Asset,
) -> tuple[str, str, list[str], str]:
    """按 YouTube 真实限制校验并归一化标题/描述/标签/分类。"""
    title = (body.title or "").strip()
    if not title:
        title = (asset.filename or "Video")[:100]
    if len(title) > 100:
        raise HTTPException(400, detail="标题不能超过 100 个字符")
    if "<" in title or ">" in title:
        raise HTTPException(400, detail="标题不能包含 < 或 >")
    description = body.description or ""
    if len(description.encode("utf-8")) > 5000:
        raise HTTPException(400, detail="描述不能超过 5000 字节")
    tags: list[str] = []
    for raw in body.tags or []:
        t = str(raw).strip()
        if t and t not in tags:
            tags.append(t)
    if len(tags) > 50:
        raise HTTPException(400, detail="标签最多 50 个")
    if sum(len(t) for t in tags) > 500:
        raise HTTPException(400, detail="标签总长度不能超过 500 个字符")
    category = (body.category_id or "22").strip() or "22"
    if not category.isdigit():
        raise HTTPException(400, detail="category_id 必须是数字")
    if body.publish_at and (body.privacy_status or "").strip().lower() != "private":
        raise HTTPException(400, detail="设置 publish_at 定时发布时，privacy_status 必须是 private")
    return title, description, tags, category


class YoutubeUploadUserError(Exception):
    """定时任务等非 HTTP 场景与路由共用：带 HTTP 语义状态码。"""

    def __init__(self, detail: str, status_code: int = 400) -> None:
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


SCHEDULE_INTERVAL_MIN = 1
SCHEDULE_INTERVAL_MAX = 10080  # 7 天，与创作者定时一致


class YoutubeOauthStartBody(BaseModel):
    """打开 YouTube OAuth 授权页。字段名保留 open_chromium 以兼容旧前端。"""

    open_chromium: bool = True


def _open_url_in_system_chrome(url: str) -> Dict[str, Any]:
    try:
        chrome_path = _find_chrome_executable()
        if not chrome_path:
            return {"ok": False, "message": "未找到 Google Chrome，请安装 Chrome 或配置 CHROME_PATH"}
        # Launch Chrome directly. The old PowerShell `-Command ... $args` form
        # never received its arguments on Windows PowerShell 5.1, so Popen
        # succeeded while Chrome was never started.
        subprocess.Popen(
            [chrome_path, "--new-window", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0),
        )
        return {"ok": True, "message": f"已用 Google Chrome 打开授权页: {chrome_path}"}
    except Exception as e:
        logger.warning("[youtube-publish] open system Chrome failed: %s", e)
        return {"ok": False, "message": str(e)}


class YoutubePublishSchedulePut(BaseModel):
    enabled: bool = False
    interval_minutes: int = Field(60, ge=SCHEDULE_INTERVAL_MIN, le=SCHEDULE_INTERVAL_MAX)
    asset_ids: list[str] = Field(
        default_factory=list,
        description="待上传视频素材 asset_id 队列（先进先出）；可为空，到点仅顺延时间",
    )
    material_origin: Literal["ai_generated", "script_batch"] = "script_batch"
    privacy_status: Literal["private", "unlisted", "public"] = "public"
    title: str = ""
    description: str = ""
    category_id: str = "22"
    tags: Optional[list[str]] = None


@router.get("/api/youtube-publish/summary")
async def youtube_publish_summary(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    doc = _load_doc(current_user.id)
    accounts = doc.get("accounts") if isinstance(doc.get("accounts"), dict) else {}
    n = 0
    has_ready = False
    for _aid, ent in accounts.items():
        if not isinstance(ent, dict):
            continue
        n += 1
        rt = (ent.get("refresh_token") or "").strip()
        st = (ent.get("status") or "").strip()
        if rt and st == "ready":
            has_ready = True
    return {"accounts_count": n, "has_ready": has_ready}


@router.get("/api/youtube-publish/accounts", response_model=list[YoutubeAccountOut])
async def list_youtube_accounts(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    doc = _load_doc(current_user.id)
    accounts = doc.get("accounts") if isinstance(doc.get("accounts"), dict) else {}
    redir = _oauth_callback_url()
    out: list[YoutubeAccountOut] = []
    for aid in sorted(accounts.keys()):
        ent = accounts[aid]
        if not isinstance(ent, dict):
            continue
        cid = (ent.get("oauth_client_id") or "").strip()
        rt = (ent.get("refresh_token") or "").strip()
        ps = (ent.get("proxy_server") or "").strip()
        pu = (ent.get("proxy_username") or "").strip()
        pp = (ent.get("proxy_password") or "").strip()
        st = (ent.get("status") or "pending").strip() or "pending"
        out.append(
            YoutubeAccountOut(
                account_id=aid,
                label=(ent.get("label") or "") if isinstance(ent.get("label"), str) else "",
                status=st,
                oauth_client_id_masked=_mask_client_id(cid) if cid else "",
                oauth_client_id=cid,
                has_refresh_token=bool(rt),
                proxy_server_masked=_mask_proxy(ps) if ps else "",
                proxy_server=ps,
                proxy_has_auth=bool(ps and (pu or pp)),
                proxy_username=pu,
                last_error=(ent.get("last_error") or "")[:500] if isinstance(ent.get("last_error"), str) else "",
                created_at=(ent.get("created_at") or "") if isinstance(ent.get("created_at"), str) else "",
                oauth_redirect_uri=redir,
            )
        )
    return out


@router.post("/api/youtube-publish/accounts", response_model=YoutubeAccountOut)
async def create_youtube_account(
    body: YoutubeAccountCreateIn,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    ps = (body.proxy_server or "").strip()
    _validate_proxy_url(ps)
    pu = (body.proxy_username or "").strip()
    pp = (body.proxy_password or "").strip()
    if not ps:
        pu = ""
        pp = ""
    if pu and not pp:
        raise HTTPException(
            status_code=400,
            detail="填写了代理用户名时请同时填写代理密码，或清空用户名与密码",
        )

    doc = _load_doc(current_user.id)
    accounts = doc.setdefault("accounts", {})
    assert isinstance(accounts, dict)
    aid = _new_account_id()
    while aid in accounts:
        aid = _new_account_id()
    from datetime import datetime, timezone

    accounts[aid] = {
        "label": (body.label or "").strip(),
        "oauth_client_id": body.oauth_client_id.strip(),
        "oauth_client_secret": body.oauth_client_secret.strip(),
        "proxy_server": ps,
        "proxy_username": pu,
        "proxy_password": pp,
        "refresh_token": "",
        "status": "pending",
        "last_error": "",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _save_doc(current_user.id, doc)
    logger.info("[youtube-publish] 创建账号 user_id=%s account_id=%s", current_user.id, aid)
    ent = accounts[aid]
    _cid = (ent.get("oauth_client_id") or "").strip()
    _ps = (ent.get("proxy_server") or "").strip()
    _pu = (ent.get("proxy_username") or "").strip()
    _pp = (ent.get("proxy_password") or "").strip()
    return YoutubeAccountOut(
        account_id=aid,
        label=ent.get("label") or "",
        status=ent.get("status") or "pending",
        oauth_client_id_masked=_mask_client_id(_cid) if _cid else "",
        oauth_client_id=_cid,
        has_refresh_token=False,
        proxy_server_masked=_mask_proxy(_ps) if _ps else "",
        proxy_server=_ps,
        proxy_has_auth=bool(_ps and (_pu or _pp)),
        proxy_username=_pu,
        last_error="",
        created_at=ent.get("created_at") or "",
        oauth_redirect_uri=_oauth_callback_url(),
    )


@router.patch("/api/youtube-publish/accounts/{account_id}", response_model=YoutubeAccountOut)
async def patch_youtube_account(
    account_id: str,
    body: YoutubeAccountPatchIn,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    doc = _load_doc(current_user.id)
    accounts = doc.get("accounts") if isinstance(doc.get("accounts"), dict) else {}
    aid = account_id.strip()
    ent = accounts.get(aid)
    if not isinstance(ent, dict):
        raise HTTPException(status_code=404, detail="账号不存在")
    if body.label is not None:
        ent["label"] = (body.label or "").strip()
    if body.oauth_client_id is not None and (body.oauth_client_id or "").strip():
        ent["oauth_client_id"] = body.oauth_client_id.strip()
    if body.oauth_client_secret is not None and (body.oauth_client_secret or "").strip():
        ent["oauth_client_secret"] = body.oauth_client_secret.strip()
    if body.proxy_server is not None:
        _validate_proxy_url((body.proxy_server or "").strip())
        ent["proxy_server"] = (body.proxy_server or "").strip()
    if body.proxy_username is not None:
        ent["proxy_username"] = (body.proxy_username or "").strip()
    if body.proxy_password is not None:
        if (body.proxy_password or "").strip():
            ent["proxy_password"] = body.proxy_password.strip()
        elif not (ent.get("proxy_username") or "").strip():
            ent["proxy_password"] = ""
    if not (ent.get("proxy_server") or "").strip():
        ent["proxy_username"] = ""
        ent["proxy_password"] = ""
    pu = (ent.get("proxy_username") or "").strip()
    pp = (ent.get("proxy_password") or "").strip()
    if pu and not pp:
        raise HTTPException(
            status_code=400,
            detail="填写了代理用户名时请同时填写代理密码，或清空用户名与密码",
        )
    ent.setdefault("status", "pending")
    accounts[aid] = ent
    _save_doc(current_user.id, doc)
    return _account_to_out(aid, ent)


def _account_to_out(aid: str, ent: Dict[str, Any]) -> YoutubeAccountOut:
    cid = (ent.get("oauth_client_id") or "").strip()
    rt = (ent.get("refresh_token") or "").strip()
    ps = (ent.get("proxy_server") or "").strip()
    pu = (ent.get("proxy_username") or "").strip()
    pp = (ent.get("proxy_password") or "").strip()
    return YoutubeAccountOut(
        account_id=aid,
        label=(ent.get("label") or "") if isinstance(ent.get("label"), str) else "",
        status=(ent.get("status") or "pending").strip() or "pending",
        oauth_client_id_masked=_mask_client_id(cid) if cid else "",
        oauth_client_id=cid,
        has_refresh_token=bool(rt),
        proxy_server_masked=_mask_proxy(ps) if ps else "",
        proxy_server=ps,
        proxy_has_auth=bool(ps and (pu or pp)),
        proxy_username=pu,
        last_error=(ent.get("last_error") or "")[:500] if isinstance(ent.get("last_error"), str) else "",
        created_at=(ent.get("created_at") or "") if isinstance(ent.get("created_at"), str) else "",
        oauth_redirect_uri=_oauth_callback_url(),
    )


def _youtube_account_proxy_url(ent: Dict[str, Any]) -> tuple[Optional[str], str]:
    kwargs, source = youtube_httpx_client_kwargs(
        (ent.get("proxy_server") or "").strip() or None,
        (ent.get("proxy_username") or "").strip() or None,
        (ent.get("proxy_password") or "").strip() or None,
        timeout=30.0,
    )
    proxy = kwargs.get("proxy")
    return (str(proxy) if proxy else None), source


@router.delete("/api/youtube-publish/accounts/{account_id}")
async def delete_youtube_account(
    account_id: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    doc = _load_doc(current_user.id)
    accounts = doc.get("accounts") if isinstance(doc.get("accounts"), dict) else {}
    aid = account_id.strip()
    if aid not in accounts:
        raise HTTPException(status_code=404, detail="账号不存在")
    del accounts[aid]
    _save_doc(current_user.id, doc)
    return {"ok": True}


@router.post("/api/youtube-publish/accounts/{account_id}/oauth/start")
async def youtube_account_oauth_start(
    account_id: str,
    body: YoutubeOauthStartBody = Body(),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    """返回 Google 授权页 URL。

    默认 **open_chromium=true**：用与发布「打开浏览器」相同的 **系统 Chrome 持久化上下文**
    （字段名保留 open_chromium 以兼容旧前端）打开授权页；
    代理与账号中填写的 **proxy_server / proxy_username / proxy_password** 对齐到发布侧 `meta.browser.proxy` 规则。

    若内置浏览器启动失败，仍返回 **url**，前端可回退 `window.open`。
    设 **open_chromium=false** 可仅要链接、自行用系统浏览器打开。

    **回调之后**：换取 refresh_token、拉素材、YouTube API 上传等仍走账号代理（httpx/httplib2）。
    """
    _prune_oauth_states()
    doc = _load_doc(current_user.id)
    accounts = doc.get("accounts") if isinstance(doc.get("accounts"), dict) else {}
    aid = account_id.strip()
    ent = accounts.get(aid)
    if not isinstance(ent, dict):
        raise HTTPException(status_code=404, detail="账号不存在")
    cid = (ent.get("oauth_client_id") or "").strip()
    csec = (ent.get("oauth_client_secret") or "").strip()
    if not cid or not csec:
        raise HTTPException(status_code=400, detail="该账号缺少 OAuth Client ID 或 Secret，请先编辑保存")
    redirect_uri = _oauth_callback_url()
    st = secrets.token_urlsafe(32)
    _oauth_states[st] = {
        "user_id": current_user.id,
        "account_id": aid,
        "client_id": cid,
        "client_secret": csec,
        "redirect_uri": redirect_uri,
        "expires": time.time() + _OAUTH_STATE_TTL_SEC,
    }
    q = urlencode(
        {
            "client_id": cid,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": YOUTUBE_FULL_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": st,
        }
    )
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{q}"
    logger.info("[youtube-publish] oauth start user_id=%s account_id=%s", current_user.id, aid)
    out: Dict[str, Any] = {
        "url": url,
        "redirect_uri": redirect_uri,
        "chromium_opened": False,
        "chromium_message": "",
    }
    if body.open_chromium:
        launch_res = _open_url_in_system_chrome(url)
        out["chromium_opened"] = bool(launch_res.get("ok"))
        out["chromium_message"] = str(launch_res.get("message") or "")
    return out


@router.get("/api/youtube-publish/oauth/callback", include_in_schema=False)
async def youtube_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    _prune_oauth_states()
    if error:
        return HTMLResponse(
            content="<html><head><meta charset=\"utf-8\"/></head><body style=\"font-family:sans-serif;padding:1.5rem;\">"
            f"<p>Google 返回错误: {html.escape(error)}</p><p>请关闭本页后重试。</p></body></html>",
            status_code=400,
        )
    if not code or not state:
        return HTMLResponse(
            content="<html><head><meta charset=\"utf-8\"/></head><body style=\"font-family:sans-serif;padding:1.5rem;\">"
            "<p>缺少 code 或 state。</p></body></html>",
            status_code=400,
        )
    st = _oauth_states.pop(state, None)
    if not st or float(st.get("expires", 0)) < time.time():
        return HTMLResponse(
            content="<html><head><meta charset=\"utf-8\"/></head><body style=\"font-family:sans-serif;padding:1.5rem;\">"
            "<p>授权已过期，请返回 YouTube 账号页重新点击「浏览器授权」。</p></body></html>",
            status_code=400,
        )
    uid = int(st["user_id"])
    aid = (st.get("account_id") or "").strip()
    if not aid:
        return HTMLResponse(
            content="<html><head><meta charset=\"utf-8\"/></head><body style=\"font-family:sans-serif;padding:1.5rem;\">"
            "<p>内部错误：缺少 account_id。</p></body></html>",
            status_code=500,
        )
    redirect_uri = str(st["redirect_uri"])
    doc_px = _load_doc(uid)
    ac_px = doc_px.get("accounts") if isinstance(doc_px.get("accounts"), dict) else {}
    ent_px = ac_px.get(aid) if isinstance(ac_px, dict) else None
    client_kw: Dict[str, Any] = {"timeout": 30.0, "trust_env": False}
    proxy_source = "direct"
    if isinstance(ent_px, dict):
        try:
            client_kw, proxy_source = youtube_httpx_client_kwargs(
                (ent_px.get("proxy_server") or "").strip() or None,
                (ent_px.get("proxy_username") or "").strip() or None,
                (ent_px.get("proxy_password") or "").strip() or None,
                timeout=30.0,
            )
        except ValueError as e:
            logger.warning("[youtube-publish] oauth token 代理 URL 无效: %s", e)
    if proxy_source != "direct":
        logger.info(
            "[youtube-publish] oauth token exchange via %s proxy (account_id=%s)",
            proxy_source,
            aid,
        )
    try:
        async with httpx.AsyncClient(**client_kw) as c:
            r = await c.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": st["client_id"],
                    "client_secret": st["client_secret"],
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as e:
        msg = f"换取 Token 网络失败：{type(e).__name__}: {e}"
        logger.warning(
            "[youtube-publish] token exchange network failed user_id=%s account_id=%s proxy=%s err=%s",
            uid,
            aid,
            proxy_source,
            e,
            exc_info=True,
        )
        _mark_account_error(uid, aid, msg)
        return _oauth_error_page(
            "Google 已返回授权 code，但本机后端连接 Google OAuth token 接口时网络断开。"
            "这通常是系统代理、账号代理或本机网络把连接中断，不是用户没有勾选权限。",
            msg,
            status_code=502,
        )
    if r.status_code != 200:
        logger.warning("[youtube-publish] token exchange failed: %s %s", r.status_code, r.text[:500])
        _mark_account_error(uid, aid, f"换取 Token 失败 HTTP {r.status_code}")
        return _oauth_error_page(
            f"换取 Token 失败: HTTP {r.status_code}",
            r.text[:2000],
            status_code=502,
        )
    tok = r.json()
    rt = (tok.get("refresh_token") or "").strip()
    if not rt:
        doc = _load_doc(uid)
        accounts = doc.get("accounts") if isinstance(doc.get("accounts"), dict) else {}
        if isinstance(accounts.get(aid), dict):
            accounts[aid]["status"] = "error"
            accounts[aid]["last_error"] = "Google 未返回 refresh_token，请移除第三方授权后重试"
            _save_doc(uid, doc)
        return HTMLResponse(
            content="<html><head><meta charset=\"utf-8\"/></head><body style=\"font-family:sans-serif;padding:1.5rem;\">"
            "<p>Google 未返回 refresh_token。请在 Google 账号的「第三方应用授权」中移除本应用后，再重新授权；"
            "并确认 OAuth 客户端类型与重定向 URI 已在 Google Cloud 中正确配置。</p></body></html>",
            status_code=400,
        )
    doc = _load_doc(uid)
    accounts = doc.get("accounts") if isinstance(doc.get("accounts"), dict) else {}
    ent = accounts.get(aid)
    if not isinstance(ent, dict):
        return HTMLResponse(
            content="<html><head><meta charset=\"utf-8\"/></head><body style=\"font-family:sans-serif;padding:1.5rem;\">"
            "<p>账号已不存在，请关闭本页。</p></body></html>",
            status_code=400,
        )
    ent["refresh_token"] = rt
    ent["status"] = "ready"
    ent["last_error"] = ""
    accounts[aid] = ent
    _save_doc(uid, doc)
    logger.info("[youtube-publish] oauth callback ok user_id=%s account_id=%s", uid, aid)
    return HTMLResponse(
        content="<html><head><meta charset=\"utf-8\"/></head><body style=\"font-family:sans-serif;padding:1.5rem;\">"
        "<p><strong>授权成功</strong>，账号已标记为可用。</p>"
        "<p>龙虾 YouTube 账号列表会自动刷新；本页将尝试自动关闭。</p>"
        "<script>setTimeout(function(){try{window.close();}catch(e){}},1200);</script>"
        "</body></html>"
    )


def _local_asset_copy_for_upload(asset: Asset) -> Optional[str]:
    """素材缺公网 source_url 时，直接复制本机文件作为上传源（不改动原文件）。"""
    try:
        from .assets import ASSETS_DIR

        fn = (getattr(asset, "filename", None) or "").strip()
        if not fn or "/" in fn or "\\" in fn:
            return None
        src = ASSETS_DIR / fn
        if not src.is_file():
            return None
        suf = Path(fn).suffix or ".mp4"
        fd, path = tempfile.mkstemp(suffix=suf)
        os.close(fd)
        shutil.copy2(src, path)
        logger.info(
            "[youtube-publish] 素材无公网 URL，已使用本机文件直传 asset_id=%s",
            getattr(asset, "asset_id", ""),
        )
        return path
    except Exception as e:
        logger.warning(
            "[youtube-publish] 本机素材复制失败 asset_id=%s err=%s",
            getattr(asset, "asset_id", ""),
            e,
        )
        return None


async def _ensure_asset_source_url(
    asset: Asset,
    *,
    request: Optional[Any] = None,
    db: Optional[Session] = None,
    user_id: Optional[int] = None,
) -> str:
    """缺 source_url 时主动补传（云端/TOS），返回最新公网 URL。"""
    if db is None:
        return ""
    try:
        from .assets import (
            _HeaderSnapshotRequest,
            _refresh_asset_source_url_from_local_file,
            get_asset_public_url,
        )

        if request is not None and user_id is not None:
            refreshed = await asyncio.to_thread(
                get_asset_public_url,
                asset.asset_id,
                int(user_id),
                request,
                db,
            )
        else:
            # 定时任务等无请求上下文：空 header 会跳过认证中心，直接走 TOS 兜底补传
            refreshed = await asyncio.to_thread(
                _refresh_asset_source_url_from_local_file,
                asset,
                _HeaderSnapshotRequest({}),
                db,
                reason="youtube_upload_missing_source_url",
            )
        if refreshed:
            logger.info(
                "[youtube-publish] 已自动补传 source_url asset_id=%s url=%s",
                getattr(asset, "asset_id", ""),
                str(refreshed)[:120],
            )
            return str(refreshed).strip()
    except Exception as e:
        logger.warning(
            "[youtube-publish] 自动补传 source_url 失败 asset_id=%s err=%s",
            getattr(asset, "asset_id", ""),
            e,
        )
    try:
        db.refresh(asset)
    except Exception:
        pass
    return (getattr(asset, "source_url", None) or "").strip()


async def _resolve_asset_to_temp_path(
    asset: Asset,
    proxy_server: Optional[str] = None,
    proxy_username: Optional[str] = None,
    proxy_password: Optional[str] = None,
    *,
    request: Optional[Any] = None,
    db: Optional[Session] = None,
    user_id: Optional[int] = None,
) -> str:
    url = (getattr(asset, "source_url", None) or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        url = await _ensure_asset_source_url(
            asset,
            request=request,
            db=db,
            user_id=user_id,
        )
    if not (url.startswith("http://") or url.startswith("https://")):
        local_copy = _local_asset_copy_for_upload(asset)
        if local_copy:
            return local_copy
        raise HTTPException(
            status_code=400,
            detail="素材无公网 source_url，且本机也找不到对应文件，请先在素材管理中重新上传",
        )
    try:
        client_kw, proxy_source = youtube_httpx_client_kwargs(
            proxy_server,
            proxy_username,
            proxy_password,
            timeout=300.0,
            target_url=url,
        )
    except ValueError as e:
        if (proxy_server or "").strip():
            logger.warning("[youtube-publish] 下载素材时代理 URL 无效，将直连: %s", e)
        client_kw = {"timeout": 300.0, "trust_env": False}
        proxy_source = "direct"
    if proxy_source != "direct":
        logger.info("[youtube-publish] 下载素材 via %s proxy", proxy_source)
    async with httpx.AsyncClient(**client_kw) as c:
        r = await c.get(url)
    r.raise_for_status()
    suf = Path(asset.filename or "").suffix or ".mp4"
    fd, path = tempfile.mkstemp(suffix=suf)
    import os as _os

    try:
        _os.write(fd, r.content)
    finally:
        _os.close(fd)
    return path


async def perform_youtube_upload(
    db: Session,
    user_id: int,
    body: YoutubePublishUploadBody,
    request: Optional[Any] = None,
) -> Dict[str, Any]:
    """供 HTTP 与定时任务共用；失败抛 YoutubeUploadUserError 或 RuntimeError。"""
    aid = body.account_id.strip()
    try:
        doc = _load_doc(user_id)
    except HTTPException as e:
        det = e.detail if isinstance(e.detail, str) else "账号数据异常"
        raise YoutubeUploadUserError(det, status_code=int(getattr(e, "status_code", None) or 500)) from e
    accounts = doc.get("accounts") if isinstance(doc.get("accounts"), dict) else {}
    ent = accounts.get(aid)
    if not isinstance(ent, dict):
        raise YoutubeUploadUserError("YouTube 账号不存在或无权访问", status_code=404)

    cid = (ent.get("oauth_client_id") or "").strip()
    csec = (ent.get("oauth_client_secret") or "").strip()
    if not cid or not csec:
        raise YoutubeUploadUserError("该账号缺少 OAuth 客户端配置")

    rt = (ent.get("refresh_token") or "").strip()
    if not rt:
        raise YoutubeUploadUserError("该账号尚未完成授权，请在账号列表中完成浏览器授权")

    st = (ent.get("status") or "").strip()
    if st != "ready":
        raise YoutubeUploadUserError(
            f"该账号当前不可用（状态: {st}）。请检查列表中的错误说明或重新授权。",
        )

    asset = (
        db.query(Asset)
        .filter(Asset.asset_id == body.asset_id.strip(), Asset.user_id == user_id)
        .first()
    )
    if not asset:
        raise YoutubeUploadUserError("素材不存在或无权访问", status_code=404)

    mt = (asset.media_type or "").lower()
    fn = (asset.filename or "").lower()
    is_video = (mt == "video") or mt.startswith("video/") or fn.endswith(
        (".mp4", ".mov", ".webm", ".mkv", ".avi")
    )
    if not is_video:
        raise YoutubeUploadUserError("当前素材不是视频类型，请选择视频素材")

    try:
        eff_title, eff_desc, eff_tags, eff_category = _validate_upload_fields(body, asset)
    except HTTPException as e:
        raise YoutubeUploadUserError(str(e.detail), status_code=int(e.status_code or 400)) from e

    temp_path: Optional[str] = None
    thumb_temp_path: Optional[str] = None
    try:
        temp_path = await _resolve_asset_to_temp_path(
            asset,
            proxy_server=(ent.get("proxy_server") or "").strip() or None,
            proxy_username=(ent.get("proxy_username") or "").strip() or None,
            proxy_password=(ent.get("proxy_password") or "").strip() or None,
            request=request,
            db=db,
            user_id=user_id,
        )
        effective_csm = body.contains_synthetic_media
        if effective_csm is None:
            effective_csm = body.material_origin == "ai_generated"
        thumb_asset_id = (body.thumbnail_asset_id or "").strip()
        if thumb_asset_id:
            thumb_asset = (
                db.query(Asset)
                .filter(Asset.asset_id == thumb_asset_id, Asset.user_id == user_id)
                .first()
            )
            if not thumb_asset:
                raise YoutubeUploadUserError("封面素材不存在或无权访问", status_code=404)
            thumb_temp_path = await _resolve_asset_to_temp_path(
                thumb_asset,
                proxy_server=(ent.get("proxy_server") or "").strip() or None,
                proxy_username=(ent.get("proxy_username") or "").strip() or None,
                proxy_password=(ent.get("proxy_password") or "").strip() or None,
                request=request,
                db=db,
                user_id=user_id,
            )
        result = upload_local_video_file(
            file_path=temp_path,
            title=eff_title,
            description=eff_desc,
            privacy_status=body.privacy_status,
            refresh_token=rt,
            client_id=cid,
            client_secret=csec,
            proxy_server=(ent.get("proxy_server") or "").strip() or None,
            proxy_username=(ent.get("proxy_username") or "").strip() or None,
            proxy_password=(ent.get("proxy_password") or "").strip() or None,
            self_declared_made_for_kids=body.self_declared_made_for_kids,
            contains_synthetic_media=effective_csm,
            category_id=eff_category,
            tags=eff_tags,
            thumbnail_path=thumb_temp_path,
            publish_at=body.publish_at,
        )
        vid = result.get("video_id")
        return {
            "ok": True,
            "video_id": vid,
            "watch_url": f"https://www.youtube.com/watch?v={vid}" if vid else None,
            "youtube_account_id": aid,
        }
    finally:
        for _p in (temp_path, thumb_temp_path):
            if _p:
                try:
                    Path(_p).unlink(missing_ok=True)
                except OSError:
                    pass


def _get_or_create_youtube_publish_schedule(
    db: Session, user_id: int, youtube_account_id: str
) -> YoutubePublishSchedule:
    aid = youtube_account_id.strip()
    row = (
        db.query(YoutubePublishSchedule)
        .filter(
            YoutubePublishSchedule.user_id == user_id,
            YoutubePublishSchedule.youtube_account_id == aid,
        )
        .first()
    )
    if row:
        return row
    row = YoutubePublishSchedule(
        user_id=user_id,
        youtube_account_id=aid,
        enabled=False,
        interval_minutes=60,
        next_run_at=None,
        asset_ids_json=[],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _norm_asset_id_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw:
        s = str(x).strip()
        if s and s not in out:
            out.append(s)
    return out


def _youtube_schedule_to_dict(row: YoutubePublishSchedule) -> Dict[str, Any]:
    from ..datetime_iso import isoformat_utc

    tags = row.tags_json if isinstance(row.tags_json, list) else None
    return {
        "youtube_account_id": row.youtube_account_id,
        "enabled": row.enabled,
        "interval_minutes": row.interval_minutes,
        "next_run_at": isoformat_utc(row.next_run_at),
        "asset_ids": _norm_asset_id_list(row.asset_ids_json),
        "material_origin": (row.material_origin or "script_batch").strip(),
        "privacy_status": (row.privacy_status or "public").strip(),
        "title": row.title or "",
        "description": row.description or "",
        "category_id": row.category_id or "22",
        "tags": tags,
        "last_run_at": isoformat_utc(row.last_run_at),
        "last_run_error": (row.last_run_error or "")[:2000] if row.last_run_error else None,
        "last_video_id": row.last_video_id,
    }


@router.post("/api/youtube-publish/upload")
async def youtube_publish_upload(
    body: YoutubePublishUploadBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    try:
        return await perform_youtube_upload(db, current_user.id, body, request=request)
    except YoutubeUploadUserError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("[youtube-publish] upload RuntimeError user_id=%s: %s", current_user.id, e)
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.exception("[youtube-publish] upload failed user_id=%s", current_user.id)
        raise HTTPException(status_code=500, detail=f"上传失败: {e}") from e


@router.get("/api/youtube-publish/accounts/{account_id}/publish-schedule")
def get_youtube_publish_schedule(
    account_id: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    aid = account_id.strip()
    doc = _load_doc(current_user.id)
    accounts = doc.get("accounts") if isinstance(doc.get("accounts"), dict) else {}
    if aid not in accounts:
        raise HTTPException(status_code=404, detail="YouTube 账号不存在")
    row = _get_or_create_youtube_publish_schedule(db, current_user.id, aid)
    return _youtube_schedule_to_dict(row)


@router.put("/api/youtube-publish/accounts/{account_id}/publish-schedule")
def put_youtube_publish_schedule(
    account_id: str,
    body: YoutubePublishSchedulePut,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    from datetime import datetime

    aid = account_id.strip()
    doc = _load_doc(current_user.id)
    accounts = doc.get("accounts") if isinstance(doc.get("accounts"), dict) else {}
    if aid not in accounts:
        raise HTTPException(status_code=404, detail="YouTube 账号不存在")

    iv = int(body.interval_minutes)
    if iv < SCHEDULE_INTERVAL_MIN or iv > SCHEDULE_INTERVAL_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"interval_minutes 须在 {SCHEDULE_INTERVAL_MIN}～{SCHEDULE_INTERVAL_MAX} 之间",
        )

    row = _get_or_create_youtube_publish_schedule(db, current_user.id, aid)
    prev_enabled = bool(row.enabled)
    asset_ids = []
    for x in body.asset_ids or []:
        s = str(x).strip()
        if s and s not in asset_ids:
            asset_ids.append(s)

    mo = (body.material_origin or "script_batch").strip().lower()
    if mo not in ("ai_generated", "script_batch"):
        mo = "script_batch"
    ps = (body.privacy_status or "public").strip().lower()
    if ps not in ("private", "unlisted", "public"):
        ps = "public"

    row.enabled = bool(body.enabled)
    row.interval_minutes = iv
    row.asset_ids_json = asset_ids
    row.material_origin = mo
    row.privacy_status = ps
    row.title = (body.title or "")[:5000]
    row.description = body.description or ""
    row.category_id = (body.category_id or "22").strip() or "22"
    row.tags_json = body.tags if body.tags else None

    if row.enabled:
        if not prev_enabled or row.next_run_at is None:
            row.next_run_at = datetime.utcnow()
    else:
        row.next_run_at = None

    db.commit()
    db.refresh(row)
    return _youtube_schedule_to_dict(row)


# ── YouTube Analytics ──────────────────────────────────────────────────────


@router.get("/api/youtube-publish/accounts/{account_id}/analytics")
async def youtube_account_analytics(
    account_id: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    doc = _load_doc(current_user.id)
    accounts = doc.get("accounts", {})
    if account_id not in accounts:
        raise HTTPException(status_code=404, detail="YouTube 账号不存在")
    ent = accounts[account_id]
    if ent.get("status") != "ready":
        raise HTTPException(status_code=400, detail="账号未完成授权")

    proxy_url, _proxy_source = _youtube_account_proxy_url(ent)
    logger.info("[youtube-publish] analytics sync user_id=%s account_id=%s proxy=%s", current_user.id, account_id, _proxy_source)
    try:
        data = await sync_youtube_account_data(ent, proxy_url=proxy_url)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return data


@router.post("/api/youtube-publish/accounts/{account_id}/sync-analytics")
async def youtube_sync_analytics(
    account_id: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    doc = _load_doc(current_user.id)
    accounts = doc.get("accounts", {})
    if account_id not in accounts:
        raise HTTPException(status_code=404, detail="YouTube 账号不存在")
    ent = accounts[account_id]
    if ent.get("status") != "ready":
        raise HTTPException(status_code=400, detail="账号未完成授权")

    proxy_url, _proxy_source = _youtube_account_proxy_url(ent)
    logger.info("[youtube-publish] analytics sync user_id=%s account_id=%s proxy=%s", current_user.id, account_id, _proxy_source)
    try:
        data = await sync_youtube_account_data(ent, proxy_url=proxy_url)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"status": "synced", "data": data}


def get_youtube_account_entry(user_id: int, account_id: str) -> Optional[Dict[str, Any]]:
    """读取单个 YouTube 账号记录（供发布中心复用）。"""
    doc = _load_doc(int(user_id))
    accounts = doc.get("accounts") if isinstance(doc.get("accounts"), dict) else {}
    ent = accounts.get((account_id or "").strip())
    return ent if isinstance(ent, dict) else None


def create_youtube_account_record(
    *,
    user_id: int,
    label: str = "",
    oauth_client_id: str,
    oauth_client_secret: str,
    proxy_server: str = "",
    proxy_username: str = "",
    proxy_password: str = "",
) -> tuple[str, Dict[str, Any]]:
    ps = (proxy_server or "").strip()
    _validate_proxy_url(ps)
    pu = (proxy_username or "").strip()
    pp = (proxy_password or "").strip()
    if not ps:
        pu = ""
        pp = ""
    if pu and not pp:
        raise HTTPException(400, detail="填写了代理用户名时请同时填写代理密码")
    doc = _load_doc(int(user_id))
    accounts = doc.setdefault("accounts", {})
    if not isinstance(accounts, dict):
        accounts = {}
        doc["accounts"] = accounts
    aid = _new_account_id()
    while aid in accounts:
        aid = _new_account_id()
    from datetime import datetime, timezone

    accounts[aid] = {
        "label": (label or "").strip(),
        "oauth_client_id": (oauth_client_id or "").strip(),
        "oauth_client_secret": (oauth_client_secret or "").strip(),
        "proxy_server": ps,
        "proxy_username": pu,
        "proxy_password": pp,
        "refresh_token": "",
        "status": "pending",
        "last_error": "",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _save_doc(int(user_id), doc)
    return aid, accounts[aid]


def start_oauth_for_account(user_id: int, account_id: str, *, open_chrome: bool = True) -> Dict[str, Any]:
    """生成 Google 授权链接，必要时打开系统 Chrome。发布中心与技能页共用。"""
    _prune_oauth_states()
    ent = get_youtube_account_entry(user_id, account_id)
    if not ent:
        raise HTTPException(404, detail="YouTube 账号不存在")
    cid = (ent.get("oauth_client_id") or "").strip()
    csec = (ent.get("oauth_client_secret") or "").strip()
    if not cid or not csec:
        raise HTTPException(400, detail="该账号缺少 OAuth Client ID 或 Secret")
    redirect_uri = _oauth_callback_url()
    st = secrets.token_urlsafe(32)
    _oauth_states[st] = {
        "user_id": int(user_id),
        "account_id": account_id,
        "client_id": cid,
        "client_secret": csec,
        "redirect_uri": redirect_uri,
        "expires": time.time() + _OAUTH_STATE_TTL_SEC,
    }
    q = urlencode(
        {
            "client_id": cid,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": YOUTUBE_FULL_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": st,
        }
    )
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{q}"
    out: Dict[str, Any] = {
        "url": url,
        "redirect_uri": redirect_uri,
        "chromium_opened": False,
        "message": "",
    }
    if open_chrome:
        res = _open_url_in_system_chrome(url)
        out["chromium_opened"] = bool(res.get("ok"))
        out["message"] = str(res.get("message") or "")
    return out


def fetch_video_status(
    *,
    video_id: str,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    proxy_server: Optional[str] = None,
    proxy_username: Optional[str] = None,
    proxy_password: Optional[str] = None,
) -> Dict[str, Any]:
    """查询上传后处理状态（uploadStatus / processingStatus）。"""
    from google.oauth2.credentials import Credentials
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=(refresh_token or "").strip(),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=(client_id or "").strip(),
        client_secret=(client_secret or "").strip(),
        scopes=[YOUTUBE_READONLY_SCOPE],
    )
    http = build_httplib2_http_for_proxy(proxy_server, proxy_username, proxy_password)
    youtube = build("youtube", "v3", http=AuthorizedHttp(creds, http=http))
    resp = youtube.videos().list(part="status,processingDetails", id=video_id).execute()
    item = (resp.get("items") or [{}])[0] if isinstance(resp, dict) else {}
    status = item.get("status") if isinstance(item.get("status"), dict) else {}
    details = item.get("processingDetails") if isinstance(item.get("processingDetails"), dict) else {}
    return {
        "video_id": video_id,
        "upload_status": str(status.get("uploadStatus") or ""),
        "privacy_status": str(status.get("privacyStatus") or ""),
        "failure_reason": str(status.get("failureReason") or ""),
        "rejection_reason": str(status.get("rejectionReason") or ""),
        "processing_status": str(details.get("processingStatus") or ""),
    }


async def _run_upload_job_async(job_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.query(YoutubeUploadJob).filter(YoutubeUploadJob.job_id == job_id).first()
        if not job:
            return
        job.status = "uploading"
        job.progress = 5
        db.commit()
        body = YoutubePublishUploadBody(
            account_id=job.account_id,
            asset_id=job.asset_id,
            title=job.title or "",
            description=job.description or "",
            privacy_status=job.privacy_status or "public",
            material_origin=job.material_origin or "script_batch",
            self_declared_made_for_kids=bool(job.made_for_kids),
            contains_synthetic_media=job.contains_synthetic_media,
            category_id=job.category_id or "22",
            tags=job.tags_json if isinstance(job.tags_json, list) else None,
            thumbnail_asset_id=job.thumbnail_asset_id,
            publish_at=job.publish_at,
        )
        res = await perform_youtube_upload(db, job.user_id, body)
        job.video_id = str(res.get("video_id") or "") or None
        job.watch_url = str(res.get("watch_url") or "") or None
        job.status = "uploaded"
        job.progress = 90
        db.commit()

        ent = get_youtube_account_entry(job.user_id, job.account_id) or {}
        for _ in range(20):
            try:
                st = await asyncio.to_thread(
                    fetch_video_status,
                    video_id=job.video_id or "",
                    refresh_token=(ent.get("refresh_token") or ""),
                    client_id=(ent.get("oauth_client_id") or ""),
                    client_secret=(ent.get("oauth_client_secret") or ""),
                    proxy_server=(ent.get("proxy_server") or "").strip() or None,
                    proxy_username=(ent.get("proxy_username") or "").strip() or None,
                    proxy_password=(ent.get("proxy_password") or "").strip() or None,
                )
                job.upload_status = st.get("upload_status") or job.upload_status
                job.processing_status = st.get("processing_status") or job.processing_status
                db.commit()
                if (job.upload_status or "") in ("processed", "failed", "rejected"):
                    break
                if (job.processing_status or "") in ("succeeded", "failed", "terminated"):
                    break
            except Exception as e:
                logger.warning("[youtube-job] 状态查询失败 job=%s err=%s", job_id, e)
            await asyncio.sleep(15)
        if (job.upload_status or "") == "processed":
            job.status = "published"
        elif (job.upload_status or "") in ("failed", "rejected"):
            job.status = "failed"
            job.error_message = f"uploadStatus={job.upload_status}"
        else:
            job.status = "processing"
        job.progress = 100
        db.commit()
    except Exception as e:
        logger.exception("[youtube-job] 失败 job=%s", job_id)
        try:
            job = db.query(YoutubeUploadJob).filter(YoutubeUploadJob.job_id == job_id).first()
            if job:
                job.status = "failed"
                job.error_message = str(e)[:1000]
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


@router.post("/api/youtube-publish/uploads", summary="创建 YouTube 异步上传任务")
async def create_youtube_upload_job(
    body: YoutubePublishUploadBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    ent = get_youtube_account_entry(current_user.id, body.account_id)
    if not ent:
        raise HTTPException(404, detail="YouTube 账号不存在")
    if not (ent.get("refresh_token") or "").strip() or (ent.get("status") or "") != "ready":
        raise HTTPException(400, detail="该账号尚未授权或不可用")
    job = YoutubeUploadJob(
        job_id="up_" + uuid.uuid4().hex[:16],
        user_id=current_user.id,
        account_id=body.account_id.strip(),
        asset_id=body.asset_id.strip(),
        title=(body.title or "")[:100],
        description=body.description or "",
        tags_json=list(body.tags or []),
        category_id=(body.category_id or "22").strip() or "22",
        privacy_status=body.privacy_status,
        material_origin=body.material_origin,
        made_for_kids=bool(body.self_declared_made_for_kids),
        contains_synthetic_media=body.contains_synthetic_media,
        thumbnail_asset_id=(body.thumbnail_asset_id or "").strip() or None,
        publish_at=(body.publish_at or "").strip() or None,
        status="queued",
        progress=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    asyncio.create_task(_run_upload_job_async(job.job_id))
    return {"job_id": job.job_id, "status": "queued"}


@router.get("/api/youtube-publish/uploads/{job_id}", summary="查询 YouTube 上传任务")
def get_youtube_upload_job(
    job_id: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    row = (
        db.query(YoutubeUploadJob)
        .filter(
            YoutubeUploadJob.job_id == job_id.strip(),
            YoutubeUploadJob.user_id == current_user.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(404, detail="任务不存在")
    return {
        "job_id": row.job_id,
        "status": row.status,
        "progress": row.progress,
        "video_id": row.video_id,
        "watch_url": row.watch_url,
        "upload_status": row.upload_status,
        "processing_status": row.processing_status,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }
