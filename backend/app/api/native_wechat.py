from __future__ import annotations

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
    enabled: bool = False
    interval_seconds: int = Field(default=1800, ge=300, le=86400)


class AutoReplyRunBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    force: bool = True


class SyncBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    limit: int = Field(default=2000, ge=1, le=10000)


class GroupBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    group_key: str = Field(min_length=1, max_length=240)


class CreateGroupBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    contacts: List[str] = Field(default_factory=list, max_length=100)
    targets: List[str] = Field(default_factory=list, max_length=100)
    names: List[str] = Field(default_factory=list, max_length=100)


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
            for part in [x.strip() for x in re.split(r"[\s,，;；]+", text) if x.strip()]:
                key = part.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(part)
    return out


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
        items = engine.list_accounts()
        result = {"ok": True, "items": items, "count": len(items), "driver": engine.local_driver_status(passive=True)}
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
        result = {"ok": True, **engine.local_driver_status()}
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
        result = engine.diagnose_local_wechat_ui(account_id)
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
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        cfg = engine.get_auto_reply_config(account_id)
        if cfg.get("enabled"):
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
            interval_seconds=body.interval_seconds,
            user_id=current_user.id,
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
        )
        return result
    except Exception as exc:
        _raise_native_wechat_error("auto_reply_run_once", exc, account_id=body.account_id)


@router.post("/api/native-wechat/contacts/sync")
async def native_wechat_sync_contacts(
    body: SyncBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        return engine.sync_local_contacts(body.account_id, limit=body.limit)
    except Exception as exc:
        _raise_native_wechat_error("contacts_sync", exc, account_id=body.account_id)


@router.post("/api/native-wechat/sessions/sync")
async def native_wechat_sync_sessions(
    body: SyncBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        return engine.sync_local_sessions(body.account_id)
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
        return engine.sync_local_groups(body.account_id, limit=body.limit)
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
    body: CreateGroupBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        task = await engine.create_group_task(
            body.account_id,
            _merge_targets(body.contacts, body.targets, body.names),
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
        return engine.sync_local_group_members(body.account_id, body.group_key)
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
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    return {"ok": True, **engine.list_peers(account_id, limit=limit, offset=offset, chat_type=chat_type)}


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
        return engine.fetch_conversation_messages(
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
        return engine.sync_local_messages(body.account_id, body.peer_id, load_more_pages=body.load_more_pages)
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
        )
        return {
            "ok": True,
            "task": task,
            "queued": task.get("status") in {"pending", "running"},
            "message": "好友申请任务已加入队列，将按频率慢慢处理",
        }
    except Exception as exc:
        _raise_native_wechat_error("friends_add", exc, account_id=body.account_id)


@router.post("/api/native-wechat/moments/like")
async def native_wechat_moments_like(
    body: MomentsLikeBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        task = await engine.create_moments_like_task(
            body.account_id,
            _merge_targets(body.targets, body.contacts, body.names),
            dry_run=body.dry_run,
            max_scrolls=body.max_scrolls,
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


@router.post("/api/native-wechat/moments/publish")
async def native_wechat_moments_publish(
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
