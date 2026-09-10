from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..services import native_whatsapp_engine as engine
from .auth import _ServerUser, get_current_user_for_local
from .creative_film_studio import _installation_id_from_request, _raw_token_from_request


router = APIRouter()


class WhatsAppConfigBody(BaseModel):
    interval_seconds: Optional[int] = Field(default=None, ge=1, le=300)
    takeover_session_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    max_unread_per_round: Optional[int] = Field(default=None, ge=1, le=100)
    reply_instruction: Optional[str] = Field(default=None, max_length=4000)


class WhatsAppRunBody(BaseModel):
    account_id: str = Field(default=engine.DEFAULT_ACCOUNT_ID, min_length=1, max_length=160)
    config_override: Optional[Dict[str, Any]] = None


class WhatsAppSyncBody(BaseModel):
    account_id: str = Field(default=engine.DEFAULT_ACCOUNT_ID, min_length=1, max_length=160)
    limit: int = Field(default=500, ge=1, le=5000)
    max_scrolls: int = Field(default=20, ge=1, le=150)


class WhatsAppConversationBody(BaseModel):
    account_id: str = Field(default=engine.DEFAULT_ACCOUNT_ID, min_length=1, max_length=160)
    target: str = Field(min_length=1, max_length=500)


class WhatsAppSendBody(WhatsAppConversationBody):
    content: str = Field(min_length=1, max_length=4000)


class WhatsAppContactBody(BaseModel):
    account_id: str = Field(default=engine.DEFAULT_ACCOUNT_ID, min_length=1, max_length=160)
    first_name: str = Field(min_length=1, max_length=200)
    last_name: str = Field(default="", max_length=200)
    username: str = Field(default="", max_length=240)
    phone: str = Field(default="", max_length=80)
    country_code: str = Field(default="+86", max_length=20)


def _ensure_default_account(account_id: str) -> None:
    if str(account_id or "").strip() != engine.DEFAULT_ACCOUNT_ID:
        raise HTTPException(status_code=400, detail="当前仅支持本机桌面 WhatsApp 默认账号")


def _desktop_error(exc: Exception) -> HTTPException:
    message = str(exc or "WhatsApp 桌面操作失败")[:1000]
    status_code = 409 if "正在执行" in message else 502
    return HTTPException(status_code=status_code, detail=message)


@router.get("/api/native-whatsapp/status")
async def native_whatsapp_status(current_user: _ServerUser = Depends(get_current_user_for_local)):
    del current_user
    return engine.status()


@router.get("/api/native-whatsapp/config")
async def native_whatsapp_config(current_user: _ServerUser = Depends(get_current_user_for_local)):
    del current_user
    return {"ok": True, "config": engine.get_config()}


@router.post("/api/native-whatsapp/config")
async def native_whatsapp_save_config(
    body: WhatsAppConfigBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return {"ok": True, "config": engine.save_config(**body.model_dump())}


@router.post("/api/native-whatsapp/run-once")
async def native_whatsapp_run_once(
    request: Request,
    body: WhatsAppRunBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    _ensure_default_account(body.account_id)
    try:
        return await engine.run_once(
            auth_context={
                "token": _raw_token_from_request(request),
                "user_id": current_user.id,
                "installation_id": _installation_id_from_request(request, current_user.id),
            },
            config_override=body.config_override,
        )
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.post("/api/native-whatsapp/stop")
async def native_whatsapp_stop(
    body: WhatsAppRunBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del body, current_user
    return engine.request_stop()


@router.get("/api/native-whatsapp/sessions")
async def native_whatsapp_sessions(
    limit: int = 50,
    offset: int = 0,
    keyword: str = "",
    chat_type: str = "",
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return {"ok": True, **engine.list_sessions(limit=limit, offset=offset, keyword=keyword, chat_type=chat_type)}


@router.post("/api/native-whatsapp/sessions/sync")
async def native_whatsapp_sync_sessions(
    body: WhatsAppSyncBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    _ensure_default_account(body.account_id)
    try:
        return await asyncio.to_thread(engine.sync_sessions, limit=body.limit, max_scrolls=body.max_scrolls)
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.post("/api/native-whatsapp/conversations/open")
async def native_whatsapp_open_conversation(
    body: WhatsAppConversationBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    _ensure_default_account(body.account_id)
    try:
        return await asyncio.to_thread(engine.open_conversation, body.target)
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.get("/api/native-whatsapp/sessions/{peer_key}/messages")
async def native_whatsapp_messages(
    peer_key: str,
    limit: int = 100,
    offset: int = 0,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return {"ok": True, **engine.list_messages(peer_key, limit=limit, offset=offset)}


@router.post("/api/native-whatsapp/messages/send")
async def native_whatsapp_send_message(
    body: WhatsAppSendBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    _ensure_default_account(body.account_id)
    try:
        return await asyncio.to_thread(engine.send_message, body.target, body.content)
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.get("/api/native-whatsapp/contacts")
async def native_whatsapp_contacts(
    limit: int = 50,
    offset: int = 0,
    keyword: str = "",
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return {"ok": True, **engine.list_contacts(limit=limit, offset=offset, keyword=keyword)}


@router.post("/api/native-whatsapp/contacts/sync")
async def native_whatsapp_sync_contacts(
    body: WhatsAppSyncBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    _ensure_default_account(body.account_id)
    try:
        return await asyncio.to_thread(engine.sync_contacts, limit=body.limit, max_scrolls=body.max_scrolls)
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.post("/api/native-whatsapp/contacts")
async def native_whatsapp_add_contact(
    body: WhatsAppContactBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    _ensure_default_account(body.account_id)
    try:
        return await asyncio.to_thread(
            engine.add_contact,
            first_name=body.first_name,
            last_name=body.last_name,
            username=body.username,
            phone=body.phone,
            country_code=body.country_code,
        )
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.get("/api/native-whatsapp/operations")
async def native_whatsapp_operations(
    limit: int = 50,
    offset: int = 0,
    keyword: str = "",
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return {"ok": True, **engine.list_operations(limit=limit, offset=offset, keyword=keyword)}
