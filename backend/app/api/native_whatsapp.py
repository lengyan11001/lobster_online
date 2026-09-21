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
    memory_doc_ids: Optional[List[str]] = Field(default=None, max_length=20)


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


def _client_request_id(request: Request) -> str:
    """客户端幂等键：同一次批量导入重发时不会重复入队。"""
    return str(request.headers.get("X-Client-Request-Id") or request.headers.get("X-Request-Id") or "").strip()[:200]


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


# ── 批量加好友 + 接管（对齐微信协议助手的接口命名，2026-09-21）────────────────


class WhatsAppFriendAddBody(BaseModel):
    account_id: str = Field(default=engine.DEFAULT_ACCOUNT_ID, min_length=1, max_length=160)
    targets: Optional[list] = None
    keywords: Optional[list] = None
    phones: Optional[list] = None
    apply_message: str = Field(default="", max_length=1000)
    remark: str = Field(default="", max_length=200)
    bulk_import: bool = False
    queue_only: bool = True
    interval_seconds: Optional[int] = Field(default=None, ge=1, le=86400)
    daily_limit: Optional[int] = Field(default=None, ge=0, le=1000)
    client_request_id: str = Field(default="", max_length=200)


class WhatsAppFriendQueueBody(BaseModel):
    account_id: str = Field(default=engine.DEFAULT_ACCOUNT_ID, min_length=1, max_length=160)
    interval_seconds: Optional[int] = Field(default=None, ge=1, le=86400)
    daily_limit: Optional[int] = Field(default=None, ge=0, le=1000)


class WhatsAppAutoReplyLoopBody(BaseModel):
    account_id: str = Field(default=engine.DEFAULT_ACCOUNT_ID, min_length=1, max_length=160)
    interval_seconds: Optional[int] = Field(default=None, ge=5, le=300)
    config_override: Optional[Dict[str, Any]] = None


@router.post("/api/native-whatsapp/friends/add")
async def native_whatsapp_friend_add(
    request: Request,
    body: WhatsAppFriendAddBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    """批量加好友：一行一个目标（电话 / 名字,电话 / @用户名），逐条入队慢慢加。"""
    _ensure_default_account(body.account_id)
    raw_targets: list = []
    for group in (body.targets, body.keywords, body.phones):
        if isinstance(group, list):
            raw_targets.extend(group)
    try:
        task = await asyncio.to_thread(
            engine.create_add_contact_task,
            raw_targets,
            apply_message=body.apply_message,
            remark=body.remark,
            bulk_import=body.bulk_import,
            queue_only=body.queue_only,
            client_request_id=body.client_request_id or _client_request_id(request),
            interval_seconds=body.interval_seconds,
            daily_limit=body.daily_limit,
        )
        return {
            "ok": True,
            "task": task,
            "queued": task.get("status") in {"pending", "queued", "running"},
            "message": "已加入加好友队列，将按间隔逐条处理",
        }
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.get("/api/native-whatsapp/friends/records")
async def native_whatsapp_friend_records(
    account_id: str = "",
    limit: int = 50,
    offset: int = 0,
    status: str = "",
    keyword: str = "",
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return {"ok": True, **engine.list_friend_records(account_id, limit=limit, offset=offset, status=status, keyword=keyword)}


@router.post("/api/native-whatsapp/friends/records/{task_id}/retry")
async def native_whatsapp_friend_record_retry(
    task_id: str,
    account_id: str = "",
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    """手动重试一条失败的加好友记录；队列没开就顺手启动，保证它真的会被处理。"""
    del current_user
    try:
        result = await asyncio.to_thread(engine.retry_friend_record, task_id, account_id)
    except Exception as exc:
        raise _desktop_error(exc) from exc
    control = await engine.start_friend_add_queue(account_id)
    return {**result, "control": control, "summary": engine.friend_add_queue_summary(account_id)}


@router.post("/api/native-whatsapp/friends/records/{task_id}/cancel")
async def native_whatsapp_friend_record_cancel(
    task_id: str,
    account_id: str = "",
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    """停止一条排队中/执行中的加好友记录（执行中的会在几秒内中断）。"""
    del current_user
    try:
        result = await asyncio.to_thread(engine.cancel_friend_record, task_id, account_id)
    except Exception as exc:
        raise _desktop_error(exc) from exc
    return {**result, "summary": engine.friend_add_queue_summary(account_id)}


@router.delete("/api/native-whatsapp/friends/records/{task_id}")
async def native_whatsapp_friend_record_delete(
    task_id: str,
    account_id: str = "",
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    """删除一条加好友记录（执行中的先停止）。"""
    del current_user
    try:
        result = await asyncio.to_thread(engine.delete_friend_record, task_id, account_id)
    except Exception as exc:
        raise _desktop_error(exc) from exc
    return {**result, "summary": engine.friend_add_queue_summary(account_id)}


@router.get("/api/native-whatsapp/friends/queue")
async def native_whatsapp_friend_queue(
    account_id: str = "",
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return {
        "ok": True,
        "control": engine.get_friend_add_control(account_id),
        "summary": engine.friend_add_queue_summary(account_id),
    }


@router.post("/api/native-whatsapp/friends/queue/settings")
async def native_whatsapp_friend_queue_settings(
    body: WhatsAppFriendQueueBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    _ensure_default_account(body.account_id)
    try:
        control = await asyncio.to_thread(
            engine.save_friend_add_control,
            body.account_id,
            interval_seconds=body.interval_seconds,
            daily_limit=body.daily_limit,
        )
        return {"ok": True, "control": control, "summary": engine.friend_add_queue_summary(body.account_id)}
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.post("/api/native-whatsapp/friends/queue/start")
async def native_whatsapp_friend_queue_start(
    body: WhatsAppFriendQueueBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    _ensure_default_account(body.account_id)
    try:
        if body.interval_seconds is not None or body.daily_limit is not None:
            await asyncio.to_thread(
                engine.save_friend_add_control,
                body.account_id,
                interval_seconds=body.interval_seconds,
                daily_limit=body.daily_limit,
            )
        control = await engine.start_friend_add_queue(body.account_id)
        return {"ok": True, "control": control, "summary": engine.friend_add_queue_summary(body.account_id)}
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.post("/api/native-whatsapp/friends/queue/stop")
async def native_whatsapp_friend_queue_stop(
    body: WhatsAppFriendQueueBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    _ensure_default_account(body.account_id)
    try:
        control = await engine.stop_friend_add_queue(body.account_id)
        return {"ok": True, "control": control, "summary": engine.friend_add_queue_summary(body.account_id)}
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.get("/api/native-whatsapp/auto-reply/config")
async def native_whatsapp_auto_reply_config(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return {"ok": True, "config": engine.get_config(), "state": engine.auto_reply_state()}


@router.post("/api/native-whatsapp/auto-reply/config")
async def native_whatsapp_auto_reply_save_config(
    body: WhatsAppConfigBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return {"ok": True, "config": engine.save_config(**body.model_dump()), "state": engine.auto_reply_state()}


@router.post("/api/native-whatsapp/auto-reply/run-once")
async def native_whatsapp_auto_reply_run_once(
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


@router.post("/api/native-whatsapp/auto-reply/loop/start")
async def native_whatsapp_auto_reply_loop_start(
    request: Request,
    body: WhatsAppAutoReplyLoopBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    """常驻接管：按间隔自动跑下一轮，直到手动停止。"""
    _ensure_default_account(body.account_id)
    try:
        state = await engine.start_auto_reply_loop(
            interval_seconds=body.interval_seconds,
            auth_context={
                "token": _raw_token_from_request(request),
                "user_id": current_user.id,
                "installation_id": _installation_id_from_request(request, current_user.id),
            },
            config_override=body.config_override,
        )
        return {"ok": True, "state": state}
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.post("/api/native-whatsapp/auto-reply/loop/stop")
async def native_whatsapp_auto_reply_loop_stop(
    body: WhatsAppRunBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del body, current_user
    return {"ok": True, "state": engine.stop_auto_reply_loop()}


@router.get("/api/native-whatsapp/auto-reply/diagnostics")
async def native_whatsapp_auto_reply_diagnostics(
    limit: int = 20,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return engine.auto_reply_diagnostics(limit=limit)
