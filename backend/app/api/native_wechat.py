from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from ..services import native_wechat_engine as engine
from .auth import _ServerUser, get_current_user_for_local
from .creative_film_studio import _installation_id_from_request, _raw_token_from_request


router = APIRouter()


class LoginStartBody(BaseModel):
    session_key: Optional[str] = Field(default=None, max_length=120)
    force: bool = False


class LoginWaitBody(BaseModel):
    session_key: str = Field(min_length=1, max_length=120)
    timeout_seconds: int = Field(default=120, ge=1, le=480)


class PollBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    timeout_ms: Optional[int] = Field(default=None, ge=1000, le=60000)


class AutoReplyConfigBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    # Omitted by configuration-only clients; keep the existing runtime state.
    enabled: Optional[bool] = None
    interval_seconds: int = Field(default=15, ge=1, le=86400)
    group_invite_enabled: Optional[bool] = None
    language: Optional[str] = Field(default=None, max_length=64)
    target_language: Optional[str] = Field(default=None, max_length=64)
    memory_doc_ids: Optional[List[str]] = Field(default=None, max_length=20)
    group_invite_memory_doc_id: Optional[str] = Field(default=None, max_length=64)
    group_invite_keywords: Optional[str] = Field(default=None, max_length=2000)
    group_invite_contacts: Optional[List[str]] = Field(default=None, max_length=20)
    group_invite_primary_contact: Optional[str] = Field(default=None, max_length=240)
    group_invite_primary_contact_name: Optional[str] = Field(default=None, max_length=240)
    group_invite_welcome_message: Optional[str] = Field(default=None, max_length=4000)


class AutoReplyRunBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    force: bool = True
    check_friend_requests: bool = True
    config_override: Optional[Dict[str, Any]] = None


class AutoReplyDiagnosticsBody(BaseModel):
    enabled: bool


class SyncBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    limit: int = Field(default=10000, ge=1, le=10000)


class GroupBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    group_key: str = Field(min_length=1, max_length=240)


class CreateGroupBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    contacts: List[str] = Field(default_factory=list, max_length=100)
    targets: List[str] = Field(default_factory=list, max_length=100)
    names: List[str] = Field(default_factory=list, max_length=100)
    welcome_message: str = Field(default="", max_length=4000)
    dedup_key: str = Field(default="", max_length=160)
    source_peer_id: str = Field(default="", max_length=240)
    source_inbound_message_id: str = Field(default="", max_length=160)
    group_invite_reason: str = Field(default="", max_length=300)
    matched_group_keywords: List[str] = Field(default_factory=list, max_length=20)


class MessageSyncBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    peer_id: str = Field(default="", max_length=240)
    load_more_pages: int = Field(default=0, ge=0, le=3)


class MessageFetchBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    peer_id: str = Field(min_length=1, max_length=240)
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
    sync: bool = False
    load_more_pages: int = Field(default=0, ge=0, le=3)


class SendTextBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    to_usernames: List[str] = Field(default_factory=list, max_length=100)
    to_username: str = Field(default="", max_length=240)
    targets: List[str] = Field(default_factory=list, max_length=100)
    sessions: List[str] = Field(default_factory=list, max_length=100)
    phones: List[str] = Field(default_factory=list, max_length=100)
    phone_numbers: List[str] = Field(default_factory=list, max_length=100)
    content: str = Field(default="", max_length=4000)
    target_type: str = Field(default="direct", max_length=32)
    attachments: List[Dict[str, Any]] = Field(default_factory=list, max_length=20)


class AddFriendBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    keyword: str = Field(default="", max_length=160)
    keywords: List[str] = Field(default_factory=list, max_length=100)
    targets: List[str] = Field(default_factory=list, max_length=100)
    phones: List[str] = Field(default_factory=list, max_length=100)
    phone_numbers: List[str] = Field(default_factory=list, max_length=100)
    apply_message: str = Field(default="", max_length=120)
    remark: str = Field(default="", max_length=120)
    tags: List[str] = Field(default_factory=list, max_length=20)
    permission: str = Field(default="朋友圈", max_length=20)
    prepare_only: bool = False
    queue_only: bool = False
    client_request_id: str = Field(default="", max_length=180)


class FriendQueueSettingsBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    interval_seconds: int = Field(default=60, ge=1, le=86400)


class MomentsLikeBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    targets: List[str] = Field(default_factory=list, max_length=100)
    contacts: List[str] = Field(default_factory=list, max_length=100)
    names: List[str] = Field(default_factory=list, max_length=100)
    dry_run: bool = False
    max_scrolls: int = Field(default=20, ge=1, le=120)


class MomentsCommentBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    targets: List[str] = Field(default_factory=list, max_length=100)
    contacts: List[str] = Field(default_factory=list, max_length=100)
    names: List[str] = Field(default_factory=list, max_length=100)
    dry_run: bool = False
    max_scrolls: int = Field(default=6, ge=1, le=30)


class MomentsEngageBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    targets: List[str] = Field(default_factory=list, max_length=100)
    contacts: List[str] = Field(default_factory=list, max_length=100)
    names: List[str] = Field(default_factory=list, max_length=100)
    moment_action: str = Field(default="like_comment", max_length=32)
    dry_run: bool = False
    max_scrolls: int = Field(default=6, ge=1, le=30)


class MomentsPublishBody(BaseModel):
    account_id: str = Field(default="pc-wechat-default", min_length=1, max_length=160)
    content: str = Field(default="", max_length=4000)
    text: str = Field(default="", max_length=4000)
    attachments: List[Dict[str, Any]] = Field(default_factory=list, max_length=9)
    media_type: str = Field(default="image_text", max_length=32)
    visibility: str = Field(default="public", max_length=32)


def _merge_targets(*items: Any) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        values = item if isinstance(item, list) else [item]
        for raw in values:
            text = str(raw or "").strip()
            if not text:
                continue
            for part in [x.strip() for x in re.split(r"[\r\n,，、;；]+", text) if x.strip()]:
                key = part.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(part)
    return out


def _client_request_id(request: Request) -> str:
    return str(request.headers.get("x-lobster-request-id") or "").strip()[:180]


def _diagnostic_detail(operation: str, exc: Exception, *, account_id: str = "", extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    diag = engine.create_native_wechat_diagnostic(
        operation,
        error=str(exc),
        account_id=account_id,
        extra=extra or {},
    )
    return {
        "message": str(exc),
        "diagnostic_code": diag.get("code"),
        "diagnostic": diag,
    }


def _raise_native_wechat_error(
    operation: str,
    exc: Exception,
    *,
    account_id: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    raise HTTPException(
        status_code=502,
        detail=_diagnostic_detail(operation, exc, account_id=account_id, extra=extra),
    ) from exc


def _attach_diagnostic_if_needed(
    result: Dict[str, Any],
    operation: str,
    *,
    account_id: str = "",
    reason: str = "",
) -> Dict[str, Any]:
    diag = engine.create_native_wechat_diagnostic(
        operation,
        error=reason,
        account_id=account_id,
        extra={"result": result},
    )
    result["diagnostic"] = diag
    result["diagnostic_code"] = diag.get("code")
    return result


@router.get("/api/native-wechat/strategy")
async def native_wechat_strategy(current_user: _ServerUser = Depends(get_current_user_for_local)):
    return {"ok": True, "strategy": engine.get_strategy()}


@router.get("/api/native-wechat/accounts")
async def native_wechat_accounts(current_user: _ServerUser = Depends(get_current_user_for_local)):
    try:
        items, driver = await asyncio.gather(
            asyncio.to_thread(engine.list_accounts),
            asyncio.to_thread(engine.local_driver_status, passive=True),
        )
        result = {"ok": True, "items": items, "count": len(items), "driver": driver}
        has_local_window = any(
            item.get("source") == "pc_wechat" and int(item.get("hwnd") or 0) > 0 and not item.get("offline")
            for item in items
        )
        if not has_local_window:
            return _attach_diagnostic_if_needed(result, "accounts", reason="no reusable local pc wechat window")
        return result
    except Exception as exc:
        _raise_native_wechat_error("accounts", exc)


@router.get("/api/native-wechat/local/status")
async def native_wechat_local_status(current_user: _ServerUser = Depends(get_current_user_for_local)):
    try:
        result = {"ok": True, **await asyncio.to_thread(engine.local_driver_status)}
        if not result.get("ok") or int(result.get("count") or 0) <= 0:
            return _attach_diagnostic_if_needed(result, "local_status", reason="local pc wechat window not detected")
        return result
    except Exception as exc:
        _raise_native_wechat_error("local_status", exc)


@router.get("/api/native-wechat/local/diagnostic-code")
async def native_wechat_local_diagnostic_code(
    account_id: str = "",
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    diag = engine.create_native_wechat_diagnostic(
        "manual_diagnostic_code",
        account_id=account_id,
        error="manual diagnostic requested",
    )
    return {"ok": True, "diagnostic_code": diag.get("code"), "diagnostic": diag}


@router.get("/api/native-wechat/local/diagnose")
async def native_wechat_local_diagnose(
    account_id: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        result = await asyncio.to_thread(engine.diagnose_local_wechat_ui, account_id)
        diag = engine.create_native_wechat_diagnostic(
            "local_diagnose",
            account_id=account_id,
            error="" if result.get("ok") else str(result.get("error") or "diagnose returned not ok"),
            extra={"diagnose": result},
        )
        result["diagnostic_code"] = diag.get("code")
        result["diagnostic"] = diag
        return result
    except Exception as exc:
        _raise_native_wechat_error("local_diagnose", exc, account_id=account_id)


@router.post("/api/native-wechat/login/start")
async def native_wechat_login_start(
    body: LoginStartBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        return await engine.start_login(force=body.force, session_key=body.session_key or "")
    except Exception as exc:
        _raise_native_wechat_error("login_start", exc)


@router.post("/api/native-wechat/login/wait")
async def native_wechat_login_wait(
    body: LoginWaitBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        return await engine.wait_login(session_key=body.session_key, timeout_seconds=body.timeout_seconds)
    except Exception as exc:
        _raise_native_wechat_error("login_wait", exc)


@router.post("/api/native-wechat/updates/poll")
async def native_wechat_poll_updates(
    body: PollBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        return await engine.poll_updates(body.account_id, timeout_ms=body.timeout_ms)
    except Exception as exc:
        _raise_native_wechat_error("updates_poll", exc, account_id=body.account_id)


@router.get("/api/native-wechat/auto-reply/config")
async def native_wechat_auto_reply_config(
    request: Request,
    account_id: str,
    start_worker: bool = True,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        cfg = engine.get_auto_reply_config(account_id)
        if start_worker and cfg.get("enabled"):
            engine.ensure_auto_reply_worker(
                account_id,
                auth_context={
                    "token": _raw_token_from_request(request),
                    "user_id": current_user.id,
                    "installation_id": _installation_id_from_request(request, current_user.id),
                },
            )
        return {"ok": True, "config": cfg}
    except Exception as exc:
        _raise_native_wechat_error("auto_reply_config_get", exc, account_id=account_id)


@router.post("/api/native-wechat/auto-reply/config")
async def native_wechat_save_auto_reply_config(
    request: Request,
    body: AutoReplyConfigBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        cfg = engine.save_auto_reply_config(
            body.account_id,
            enabled=body.enabled,
            interval_seconds=15,
            group_invite_enabled=body.group_invite_enabled,
            language=body.language or body.target_language,
            user_id=current_user.id,
            memory_doc_ids=body.memory_doc_ids,
            group_invite_memory_doc_id=body.group_invite_memory_doc_id,
            group_invite_keywords=body.group_invite_keywords,
            group_invite_contacts=body.group_invite_contacts,
            group_invite_primary_contact=body.group_invite_primary_contact,
            group_invite_primary_contact_name=body.group_invite_primary_contact_name,
            group_invite_welcome_message=body.group_invite_welcome_message,
            auth_context={
                "token": _raw_token_from_request(request),
                "user_id": current_user.id,
                "installation_id": _installation_id_from_request(request, current_user.id),
            },
        )
        return {"ok": True, "config": cfg}
    except Exception as exc:
        _raise_native_wechat_error("auto_reply_config_save", exc, account_id=body.account_id)


@router.post("/api/native-wechat/auto-reply/run-once")
async def native_wechat_run_auto_reply_once(
    request: Request,
    body: AutoReplyRunBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        result = await engine.run_auto_reply_once(
            body.account_id,
            auth_context={
                "token": _raw_token_from_request(request),
                "user_id": current_user.id,
                "installation_id": _installation_id_from_request(request, current_user.id),
            },
            force=body.force,
            trigger="manual",
            check_friend_requests=body.check_friend_requests,
            config_override=body.config_override,
        )
        return result
    except Exception as exc:
        _raise_native_wechat_error("auto_reply_run_once", exc, account_id=body.account_id)


@router.post("/api/native-wechat/auto-reply/stop")
async def native_wechat_stop_auto_reply(
    body: AutoReplyRunBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    try:
        return engine.request_auto_reply_stop(body.account_id)
    except Exception as exc:
        _raise_native_wechat_error("auto_reply_stop", exc, account_id=body.account_id)


@router.get("/api/native-wechat/auto-reply/diagnostics")
async def native_wechat_auto_reply_diagnostics(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return {
        "ok": True,
        "enabled": engine.auto_reply_diagnostics_enabled(),
        "log_path": str(engine.NATIVE_WECHAT_AUTO_REPLY_LOG),
        "disable_marker": str(engine.NATIVE_WECHAT_AUTO_REPLY_LOG_DISABLE_MARKER),
    }


@router.post("/api/native-wechat/auto-reply/diagnostics")
async def native_wechat_set_auto_reply_diagnostics(
    body: AutoReplyDiagnosticsBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return engine.set_auto_reply_diagnostics_enabled(body.enabled)


@router.post("/api/native-wechat/contacts/sync")
async def native_wechat_sync_contacts(
    body: SyncBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        return await asyncio.to_thread(engine.sync_local_contacts, body.account_id, limit=body.limit)
    except Exception as exc:
        _raise_native_wechat_error("contacts_sync", exc, account_id=body.account_id)


@router.post("/api/native-wechat/sessions/sync")
async def native_wechat_sync_sessions(
    body: SyncBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        return await asyncio.to_thread(engine.sync_local_sessions, body.account_id)
    except Exception as exc:
        _raise_native_wechat_error("sessions_sync", exc, account_id=body.account_id)


@router.get("/api/native-wechat/contacts")
async def native_wechat_contacts(
    account_id: str,
    keyword: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    return {"ok": True, **engine.list_contacts(account_id, limit=limit, offset=offset, keyword=keyword)}


@router.post("/api/native-wechat/groups/sync")
async def native_wechat_sync_groups(
    body: SyncBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        return await asyncio.to_thread(engine.sync_local_groups, body.account_id, limit=body.limit)
    except Exception as exc:
        _raise_native_wechat_error("groups_sync", exc, account_id=body.account_id)


@router.get("/api/native-wechat/groups")
async def native_wechat_groups(
    account_id: str,
    keyword: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    return {"ok": True, **engine.list_groups(account_id, limit=limit, offset=offset, keyword=keyword)}


@router.post("/api/native-wechat/groups/create")
async def native_wechat_create_group(
    request: Request,
    body: CreateGroupBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        task = await engine.create_group_task(
            body.account_id,
            _merge_targets(body.contacts, body.targets, body.names),
            welcome_message=body.welcome_message,
            dedup_key=body.dedup_key,
            source_peer_id=body.source_peer_id,
            source_inbound_message_id=body.source_inbound_message_id,
            group_invite_reason=body.group_invite_reason,
            matched_group_keywords=body.matched_group_keywords,
            client_request_id=_client_request_id(request),
        )
        return {
            "ok": True,
            "task": task,
            "queued": task.get("status") in {"pending", "running"},
            "message": "创建群任务已加入队列",
        }
    except Exception as exc:
        _raise_native_wechat_error("groups_create", exc, account_id=body.account_id)


@router.post("/api/native-wechat/groups/members/sync")
async def native_wechat_sync_group_members(
    body: GroupBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        return await asyncio.to_thread(
            engine.sync_local_group_members,
            body.account_id,
            body.group_key,
        )
    except Exception as exc:
        _raise_native_wechat_error("group_members_sync", exc, account_id=body.account_id)


@router.get("/api/native-wechat/groups/members")
async def native_wechat_group_members(
    account_id: str,
    group_key: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    return {"ok": True, **engine.list_group_members(account_id, group_key, limit=limit, offset=offset)}


@router.get("/api/native-wechat/peers")
async def native_wechat_peers(
    account_id: str,
    chat_type: str = "",
    keyword: str = "",
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    return {
        "ok": True,
        **engine.list_peers(
            account_id,
            limit=limit,
            offset=offset,
            chat_type=chat_type,
            keyword=keyword,
        ),
    }


@router.get("/api/native-wechat/messages")
async def native_wechat_messages(
    account_id: str,
    peer_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    return {"ok": True, **engine.list_messages(account_id, peer_id, limit=limit, offset=offset)}


@router.post("/api/native-wechat/messages/fetch")
async def native_wechat_fetch_messages(
    body: MessageFetchBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        return await asyncio.to_thread(
            engine.fetch_conversation_messages,
            body.account_id,
            body.peer_id,
            limit=body.limit,
            offset=body.offset,
            sync=body.sync,
            load_more_pages=body.load_more_pages,
        )
    except Exception as exc:
        _raise_native_wechat_error("messages_fetch", exc, account_id=body.account_id)


@router.post("/api/native-wechat/messages/sync")
async def native_wechat_sync_messages(
    body: MessageSyncBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        return await asyncio.to_thread(
            engine.sync_local_messages,
            body.account_id,
            body.peer_id,
            load_more_pages=body.load_more_pages,
        )
    except Exception as exc:
        _raise_native_wechat_error("messages_sync", exc, account_id=body.account_id)


@router.post("/api/native-wechat/files/upload")
async def native_wechat_upload_file(
    file: UploadFile = File(...),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    target = engine.make_native_wechat_upload_path(file.filename or "file")
    total = 0
    try:
        with target.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > engine.NATIVE_WECHAT_MAX_UPLOAD_BYTES:
                    out.close()
                    try:
                        target.unlink(missing_ok=True)
                    except Exception:
                        pass
                    raise HTTPException(status_code=413, detail="文件过大")
                out.write(chunk)
    finally:
        await file.close()
    if total <= 0:
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="文件为空")
    content_type = file.content_type or "application/octet-stream"
    item = {
        "local_path": str(target.resolve()),
        "filename": file.filename or target.name,
        "size": total,
        "content_type": content_type,
        "kind": engine.native_wechat_file_kind(target, content_type),
    }
    return {"ok": True, "file": item}


@router.post("/api/native-wechat/messages/send")
async def native_wechat_send_text(
    request: Request,
    body: SendTextBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        task = await engine.create_send_task(
            body.account_id,
            _merge_targets(body.to_usernames, body.to_username, body.targets, body.sessions, body.phones, body.phone_numbers),
            body.content,
            target_type=body.target_type,
            attachments=body.attachments,
            client_request_id=_client_request_id(request),
        )
        return {
            "ok": True,
            "task": task,
            "success_count": int(task.get("success") or 0),
            "failed_count": int(task.get("failed") or 0),
            "queued": task.get("status") in {"pending", "running"},
        }
    except Exception as exc:
        _raise_native_wechat_error("messages_send", exc, account_id=body.account_id)


@router.post("/api/native-wechat/friends/add")
async def native_wechat_add_friend(
    request: Request,
    body: AddFriendBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        task = await engine.create_add_friend_task(
            body.account_id,
            _merge_targets(body.keyword, body.keywords, body.targets, body.phones, body.phone_numbers),
            apply_message=body.apply_message,
            remark=body.remark,
            tags=body.tags,
            permission=body.permission,
            prepare_only=body.prepare_only,
            queue_only=body.queue_only,
            client_request_id=body.client_request_id or _client_request_id(request),
        )
        return {
            "ok": True,
            "task": task,
            "queued": task.get("status") in {"pending", "queued", "running"},
            "message": "好友申请任务已加入队列，将按频率慢慢处理",
        }
    except Exception as exc:
        _raise_native_wechat_error("friends_add", exc, account_id=body.account_id)


@router.get("/api/native-wechat/friends/records")
async def native_wechat_friend_records(
    account_id: str = "",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    return {"ok": True, **engine.list_friend_records(account_id, limit=limit, offset=offset)}


@router.get("/api/native-wechat/friends/queue")
async def native_wechat_friend_queue(
    account_id: str = "",
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    if not account_id:
        raise HTTPException(status_code=422, detail="missing account_id")
    return {"ok": True, "control": engine.get_friend_add_control(account_id)}


@router.post("/api/native-wechat/friends/queue/settings")
async def native_wechat_friend_queue_settings(
    body: FriendQueueSettingsBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        return {"ok": True, "control": engine.save_friend_add_control(body.account_id, interval_seconds=body.interval_seconds)}
    except Exception as exc:
        _raise_native_wechat_error("friends_queue_settings", exc, account_id=body.account_id)


@router.post("/api/native-wechat/friends/queue/start")
async def native_wechat_friend_queue_start(
    body: FriendQueueSettingsBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        engine.save_friend_add_control(body.account_id, interval_seconds=body.interval_seconds)
        return {"ok": True, "control": await engine.start_friend_add_queue(body.account_id)}
    except Exception as exc:
        _raise_native_wechat_error("friends_queue_start", exc, account_id=body.account_id)


@router.post("/api/native-wechat/friends/queue/stop")
async def native_wechat_friend_queue_stop(
    body: FriendQueueSettingsBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        return {"ok": True, "control": await engine.stop_friend_add_queue(body.account_id)}
    except Exception as exc:
        _raise_native_wechat_error("friends_queue_stop", exc, account_id=body.account_id)


@router.post("/api/native-wechat/moments/like")
async def native_wechat_moments_like(
    request: Request,
    body: MomentsLikeBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        task = await engine.create_moments_like_task(
            body.account_id,
            _merge_targets(body.targets, body.contacts, body.names),
            dry_run=body.dry_run,
            max_scrolls=body.max_scrolls,
            client_request_id=_client_request_id(request),
        )
        return {
            "ok": True,
            "task": task,
            "queued": task.get("status") in {"pending", "running"},
            "message": "朋友圈点赞任务已加入队列" if not body.dry_run else "朋友圈点赞探测任务已加入队列",
        }
    except Exception as exc:
        _raise_native_wechat_error("moments_like", exc, account_id=body.account_id)


@router.post("/api/native-wechat/moments/comment")
async def native_wechat_moments_comment(
    request: Request,
    body: MomentsCommentBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        raw_token = _raw_token_from_request(request)
        if not raw_token:
            raise HTTPException(status_code=401, detail="需要登录后才能生成朋友圈评论")
        task = await engine.create_moments_comment_task(
            body.account_id,
            _merge_targets(body.targets, body.contacts, body.names),
            dry_run=body.dry_run,
            max_scrolls=body.max_scrolls,
            user_id=current_user.id,
            auth_context={
                "token": raw_token,
                "user_id": current_user.id,
                "installation_id": _installation_id_from_request(request, current_user.id),
            },
            client_request_id=_client_request_id(request),
        )
        return {
            "ok": True,
            "task": task,
            "queued": task.get("status") in {"pending", "running"},
            "message": "朋友圈评论任务已加入队列" if not body.dry_run else "朋友圈评论探测任务已加入队列",
        }
    except HTTPException:
        raise
    except Exception as exc:
        _raise_native_wechat_error("moments_comment", exc, account_id=body.account_id)


@router.post("/api/native-wechat/moments/engage")
async def native_wechat_moments_engage(
    request: Request,
    body: MomentsEngageBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        action = str(body.moment_action or "like_comment").strip().lower() or "like_comment"
        if action not in {"like", "comment", "like_comment", "both"}:
            raise HTTPException(status_code=422, detail="朋友圈互动动作只支持点赞、评论或点赞并评论")
        raw_token = _raw_token_from_request(request)
        if action in {"comment", "like_comment", "both"} and not raw_token:
            raise HTTPException(status_code=401, detail="需要登录后才能生成朋友圈评论")
        task = await engine.create_moments_engage_task(
            body.account_id,
            _merge_targets(body.targets, body.contacts, body.names),
            moment_action=action,
            dry_run=body.dry_run,
            max_scrolls=body.max_scrolls,
            user_id=current_user.id,
            auth_context={
                "token": raw_token,
                "user_id": current_user.id,
                "installation_id": _installation_id_from_request(request, current_user.id),
            },
            client_request_id=_client_request_id(request),
        )
        return {
            "ok": True,
            "task": task,
            "queued": task.get("status") in {"pending", "running"},
            "message": "朋友圈互动任务已加入队列",
        }
    except HTTPException:
        raise
    except Exception as exc:
        _raise_native_wechat_error("moments_engage", exc, account_id=body.account_id)


@router.post("/api/native-wechat/moments/publish")
async def native_wechat_moments_publish(
    request: Request,
    body: MomentsPublishBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        content = (body.content or body.text or "").strip()
        task = await engine.create_moments_publish_task(
            body.account_id,
            content,
            attachments=body.attachments,
            media_type=body.media_type,
            visibility=body.visibility,
            client_request_id=_client_request_id(request),
        )
        return {
            "ok": True,
            "task": task,
            "queued": task.get("status") in {"pending", "running"},
            "message": "朋友圈发布任务已加入队列",
        }
    except Exception as exc:
        _raise_native_wechat_error("moments_publish", exc, account_id=body.account_id)


@router.get("/api/native-wechat/tasks")
async def native_wechat_tasks(
    account_id: str = "",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    return {"ok": True, **engine.list_tasks(account_id, limit=limit, offset=offset)}


@router.get("/api/native-wechat/tasks/{task_id}")
async def native_wechat_task_detail(
    task_id: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    task = engine.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True, "task": task}
