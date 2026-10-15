from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..services import native_telegram_engine as engine
from .auth import _ServerUser, get_current_user_for_local


router = APIRouter()


class TelegramConfigBody(BaseModel):
    api_id: Optional[str] = Field(default=None, max_length=20)
    api_hash: Optional[str] = Field(default=None, max_length=64)
    phone: Optional[str] = Field(default=None, max_length=32)
    proxy: Optional[str] = Field(default=None, max_length=200)
    interval_seconds: Optional[int] = Field(default=None, ge=1, le=300)
    max_unread_per_round: Optional[int] = Field(default=None, ge=1, le=100)
    reply_instruction: Optional[str] = Field(default=None, max_length=4000)
    send_whitelist_only: Optional[bool] = None


class TelegramLoginStartBody(BaseModel):
    phone: str = Field(default="", max_length=32)


class TelegramLoginInputBody(BaseModel):
    kind: str = Field(min_length=1, max_length=16)
    value: str = Field(default="", max_length=200)


def _telegram_error(exc: Exception) -> HTTPException:
    message = str(exc or "Telegram 操作失败")[:1000]
    status_code = 409 if "正在执行" in message else 502
    return HTTPException(status_code=status_code, detail=message)


@router.get("/api/native-telegram/status")
async def native_telegram_status(current_user: _ServerUser = Depends(get_current_user_for_local)):
    del current_user
    return engine.status()


@router.get("/api/native-telegram/config")
async def native_telegram_config(current_user: _ServerUser = Depends(get_current_user_for_local)):
    del current_user
    return {"ok": True, "config": engine.get_config()}


@router.post("/api/native-telegram/config")
async def native_telegram_save_config(
    body: TelegramConfigBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return {"ok": True, "config": engine.save_config(**body.model_dump())}


@router.get("/api/native-telegram/dependencies")
async def native_telegram_dependencies(current_user: _ServerUser = Depends(get_current_user_for_local)):
    del current_user
    return {"ok": True, "dependency": engine.dependency_report()}


@router.get("/api/native-telegram/protocol/log")
async def native_telegram_protocol_log(
    limit: int = 80,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return engine.protocol_log(limit=limit)


@router.get("/api/native-telegram/operations")
async def native_telegram_operations(
    limit: int = 50,
    offset: int = 0,
    keyword: str = "",
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return engine.list_operations(limit=limit, offset=offset, keyword=keyword)


@router.post("/api/native-telegram/login/start")
async def native_telegram_login_start(
    body: TelegramLoginStartBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    try:
        return engine.login_start(body.phone)
    except Exception as exc:
        raise _telegram_error(exc) from exc


@router.post("/api/native-telegram/login/input")
async def native_telegram_login_input(
    body: TelegramLoginInputBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return engine.login_input(body.kind, body.value)


@router.get("/api/native-telegram/login/status")
async def native_telegram_login_status(current_user: _ServerUser = Depends(get_current_user_for_local)):
    del current_user
    return engine.login_status()


@router.post("/api/native-telegram/login/cancel")
async def native_telegram_login_cancel(current_user: _ServerUser = Depends(get_current_user_for_local)):
    del current_user
    return engine.login_cancel()


@router.post("/api/native-telegram/logout")
async def native_telegram_logout(current_user: _ServerUser = Depends(get_current_user_for_local)):
    del current_user
    return engine.logout()


@router.get("/api/native-telegram/chats")
async def native_telegram_chats(
    limit: int = 50,
    offset: int = 0,
    keyword: str = "",
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return engine.list_chats(limit=limit, offset=offset, keyword=keyword)


@router.get("/api/native-telegram/contacts")
async def native_telegram_contacts(
    limit: int = 50,
    offset: int = 0,
    keyword: str = "",
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return engine.list_contacts(limit=limit, offset=offset, keyword=keyword)


@router.get("/api/native-telegram/chats/{peer_key}/messages")
async def native_telegram_messages(
    peer_key: str,
    limit: int = 50,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return engine.list_messages(peer_key, limit=limit)


@router.post("/api/native-telegram/sync/contacts")
async def native_telegram_sync_contacts(current_user: _ServerUser = Depends(get_current_user_for_local)):
    del current_user
    return engine.not_implemented("sync", {"action": "sync_contacts"})


@router.post("/api/native-telegram/sync/chats")
async def native_telegram_sync_chats(current_user: _ServerUser = Depends(get_current_user_for_local)):
    del current_user
    return engine.not_implemented("sync", {"action": "sync_chats"})
