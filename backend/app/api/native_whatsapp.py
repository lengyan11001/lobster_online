from __future__ import annotations

import asyncio
import csv
import io as _io
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
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
    group_invite_enabled: Optional[bool] = None
    group_invite_memory_doc_id: Optional[str] = Field(default=None, max_length=64)
    group_invite_keywords: Optional[str] = Field(default=None, max_length=500)
    group_invite_contacts: Optional[List[str]] = Field(default=None, max_length=30)
    group_invite_group_name: Optional[str] = Field(default=None, max_length=60)
    group_invite_welcome_message: Optional[str] = Field(default=None, max_length=1000)


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



class WhatsAppContactUpdateBody(BaseModel):
    account_id: str = Field(default=engine.DEFAULT_ACCOUNT_ID, min_length=1, max_length=160)
    contact_key: str = Field(min_length=1, max_length=240)
    phone: str = Field(default="", max_length=80)
    display_name: str = Field(default="", max_length=500)


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


@router.get("/api/native-whatsapp/web/login")
async def native_whatsapp_web_login_status(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return {"ok": True, **(await asyncio.to_thread(engine.web_login_status))}


@router.post("/api/native-whatsapp/web/login")
async def native_whatsapp_web_login_start(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    try:
        return {"ok": True, **(await asyncio.to_thread(engine.web_login_start))}
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.post("/api/native-whatsapp/web/logout")
async def native_whatsapp_web_logout(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    try:
        return {"ok": True, **(await asyncio.to_thread(engine.web_logout))}
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.post("/api/native-whatsapp/web/contacts/sync")
async def native_whatsapp_web_sync_contacts(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    try:
        return await asyncio.to_thread(engine.web_sync_contacts)
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.get("/api/native-whatsapp/contacts/stats")
async def native_whatsapp_contact_stats(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return {"ok": True, **(await asyncio.to_thread(engine.contact_stats))}


@router.get("/api/native-whatsapp/contacts/export")
async def native_whatsapp_contacts_export(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    rows = await asyncio.to_thread(engine.export_contacts)
    buf = _io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["手机号", "名字", "名", "姓", "@用户名", "来源", "更新时间"])
    for row in rows:
        writer.writerow([
            str(row.get("phone") or ""),
            str(row.get("display_name") or ""),
            str(row.get("first_name") or ""),
            str(row.get("last_name") or ""),
            str(row.get("username") or ""),
            str(row.get("source") or ""),
            str(row.get("updated_at") or ""),
        ])
    payload = "\ufeff" + buf.getvalue()
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=whatsapp-contacts.csv"},
    )


@router.post("/api/native-whatsapp/contacts/update")
async def native_whatsapp_update_contact(
    body: WhatsAppContactUpdateBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    _ensure_default_account(body.account_id)
    try:
        return await asyncio.to_thread(
            engine.update_contact, body.contact_key, phone=body.phone, display_name=body.display_name
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


# ---------------------------------------------------------------------------
# 2026-10-15（二）：协议直连 —— 读接口 + 关键节点日志 + 中风险发送（限速）
# ---------------------------------------------------------------------------


class WhatsAppWebCheckBody(BaseModel):
    phones: List[str] = Field(default_factory=list, max_length=100)


class WhatsAppWebSendBody(BaseModel):
    account_id: str = Field(default=engine.DEFAULT_ACCOUNT_ID, min_length=1, max_length=160)
    targets: List[str] = Field(default_factory=list, max_length=50)
    text: str = Field(default="", max_length=4000)
    image_path: str = Field(default="", max_length=500)
    file_path: str = Field(default="", max_length=500)
    caption: str = Field(default="", max_length=1000)
    min_gap: Optional[int] = Field(default=None, ge=3, le=600)
    max_gap: Optional[int] = Field(default=None, ge=3, le=900)
    pause_every: Optional[int] = Field(default=None, ge=1, le=100)
    pause_seconds: Optional[int] = Field(default=None, ge=0, le=3600)
    daily_limit: Optional[int] = Field(default=None, ge=0, le=1000)
    auto_start: bool = False


class WhatsAppWebSendControlBody(BaseModel):
    account_id: str = Field(default=engine.DEFAULT_ACCOUNT_ID, min_length=1, max_length=160)
    min_gap: Optional[int] = Field(default=None, ge=3, le=600)
    max_gap: Optional[int] = Field(default=None, ge=3, le=900)
    pause_every: Optional[int] = Field(default=None, ge=1, le=100)
    pause_seconds: Optional[int] = Field(default=None, ge=0, le=3600)
    daily_limit: Optional[int] = Field(default=None, ge=0, le=1000)


@router.get("/api/native-whatsapp/web/chats")
async def native_whatsapp_web_chats(
    refresh: int = 0,
    wait: int = 25,
    keyword: str = "",
    limit: int = 200,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    """会话列表（协议）。refresh=1 才真连一次协议，平时读本地账，页面刷新不会每次干等 20 秒。"""
    del current_user
    try:
        return await asyncio.to_thread(engine.web_chats, refresh=bool(refresh), wait=wait, keyword=keyword, limit=limit)
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.get("/api/native-whatsapp/web/groups")
async def native_whatsapp_web_groups(
    refresh: int = 0,
    wait: int = 20,
    limit: int = 200,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    try:
        return await asyncio.to_thread(engine.web_groups, refresh=bool(refresh), wait=wait, limit=limit)
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.get("/api/native-whatsapp/web/messages")
async def native_whatsapp_web_messages(
    jid: str,
    refresh: int = 0,
    limit: int = 50,
    wait: int = 12,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    """读消息走协议（不再用 UIA 点界面）：refresh=1 真拉一次并落库。"""
    del current_user
    try:
        return await asyncio.to_thread(engine.web_messages, jid, refresh=bool(refresh), limit=limit, wait=wait)
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.post("/api/native-whatsapp/web/check")
async def native_whatsapp_web_check(
    body: WhatsAppWebCheckBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    try:
        return await asyncio.to_thread(engine.web_check, body.phones)
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.get("/api/native-whatsapp/web/profile")
async def native_whatsapp_web_profile(
    jid: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    try:
        return await asyncio.to_thread(engine.web_profile, jid)
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.get("/api/native-whatsapp/web/protocol/log")
async def native_whatsapp_web_protocol_log(
    limit: int = 50,
    event: str = "",
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    """关键节点账本（logs/whatsapp_protocol.jsonl 的最后 N 条）：以后查问题只看这里。"""
    del current_user
    return await asyncio.to_thread(engine.protocol_log, limit=limit, event=event)


@router.get("/api/native-whatsapp/web/send/config")
async def native_whatsapp_web_send_config(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return {
        "ok": True,
        "control": await asyncio.to_thread(engine.get_send_control),
        "summary": await asyncio.to_thread(engine.send_queue_summary),
    }


@router.post("/api/native-whatsapp/web/send/config")
async def native_whatsapp_web_send_config_save(
    body: WhatsAppWebSendControlBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    _ensure_default_account(body.account_id)
    del current_user
    try:
        control = await asyncio.to_thread(
            engine.save_send_control,
            body.account_id,
            min_gap=body.min_gap,
            max_gap=body.max_gap,
            pause_every=body.pause_every,
            pause_seconds=body.pause_seconds,
            daily_limit=body.daily_limit,
        )
        return {"ok": True, "control": control, "summary": await asyncio.to_thread(engine.send_queue_summary)}
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.get("/api/native-whatsapp/web/send/queue")
async def native_whatsapp_web_send_queue(
    limit: int = 20,
    offset: int = 0,
    status: str = "",
    keyword: str = "",
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    records = await asyncio.to_thread(engine.list_send_records, "", limit=limit, offset=offset, status=status, keyword=keyword)
    return {
        "ok": True,
        "control": await asyncio.to_thread(engine.get_send_control),
        "summary": await asyncio.to_thread(engine.send_queue_summary),
        **records,
    }


@router.post("/api/native-whatsapp/web/send")
async def native_whatsapp_web_send(
    body: WhatsAppWebSendBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    """中风险：入队发送（白名单 + 去重 + 限速；默认不自动启动队列）。"""
    _ensure_default_account(body.account_id)
    del current_user
    try:
        result = await asyncio.to_thread(
            engine.enqueue_send,
            body.targets,
            text=body.text,
            image_path=body.image_path,
            file_path=body.file_path,
            caption=body.caption,
            min_gap=body.min_gap,
            max_gap=body.max_gap,
            pause_every=body.pause_every,
            pause_seconds=body.pause_seconds,
            daily_limit=body.daily_limit,
        )
        if body.auto_start and result.get("queued"):
            result["control"] = await engine.start_send_queue(body.account_id)
            result["summary"] = engine.send_queue_summary(body.account_id)
        return result
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.post("/api/native-whatsapp/web/send/start")
async def native_whatsapp_web_send_start(
    body: WhatsAppRunBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    _ensure_default_account(body.account_id)
    del current_user
    try:
        control = await engine.start_send_queue(body.account_id)
        return {"ok": True, "control": control, "summary": engine.send_queue_summary(body.account_id)}
    except Exception as exc:
        raise _desktop_error(exc) from exc


@router.post("/api/native-whatsapp/web/send/stop")
async def native_whatsapp_web_send_stop(
    body: WhatsAppRunBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    _ensure_default_account(body.account_id)
    del current_user
    try:
        control = await engine.stop_send_queue(body.account_id)
        return {"ok": True, "control": control, "summary": engine.send_queue_summary(body.account_id)}
    except Exception as exc:
        raise _desktop_error(exc) from exc
