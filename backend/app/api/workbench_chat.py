"""独立的云端工作台对话通道。

这条链路与现有 /chat/stream 分离：
- 不走默认 AI 对话链
- 优先走真实 CLI 能力
- 对外统一呈现为“云端工作台”
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Dict, Optional, Tuple, Union

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ChatTurnLog, User
from ..services.coze_cli_service import (
    attach_real_preview_url,
    build_project_progress_message,
    get_project_result_snapshot,
    handle_cli_prompt,
)
from .auth import _ServerUser, get_current_user_for_chat

router = APIRouter()
logger = logging.getLogger(__name__)


class WorkbenchChatMessage(BaseModel):
    role: str = Field(..., description="user | assistant | system")
    content: str = Field(..., description="消息内容")


class WorkbenchChatRequest(BaseModel):
    message: str = Field(..., description="当前用户输入")
    history: Optional[list[WorkbenchChatMessage]] = Field(default_factory=list)
    session_id: Optional[str] = None
    context_id: Optional[str] = None
    model: Optional[str] = None


def _sse_payload(data: Dict[str, Any]) -> str:
    return "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"


async def _optional_workbench_user(request: Request) -> Tuple[Optional[Union[User, _ServerUser]], str]:
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth:
        return None, ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else auth
    if not token:
        return None, ""
    try:
        user = await get_current_user_for_chat(request, token=token)
        return user, token
    except HTTPException as exc:
        logger.warning(
            "[workbench_chat] 跳过登录校验 status=%s detail=%s，先允许匿名测试",
            exc.status_code,
            exc.detail,
        )
        return None, token


async def _record_workbench_turn(
    *,
    payload: WorkbenchChatRequest,
    current_user: Optional[Union[User, _ServerUser]],
    db: Session,
    assistant_reply: str,
    invoke_model: str,
    meta: Optional[dict[str, Any]] = None,
) -> None:
    if current_user is None:
        return
    try:
        db.add(
            ChatTurnLog(
                user_id=int(current_user.id),
                session_id=(payload.session_id or "")[:128] or None,
                context_id=(payload.context_id or "")[:128] or None,
                user_message=(payload.message or "")[:12000],
                assistant_reply=(assistant_reply or "")[:24000],
                meta={"mode": "workspace_cli", "invoke_model": invoke_model, **(meta or {})},
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("[workbench_chat] 保存会话记录失败")


async def _workbench_chat_stream(
    *,
    payload: WorkbenchChatRequest,
    raw_token: str,
    current_user: Optional[Union[User, _ServerUser]],
    db: Session,
) -> AsyncGenerator[str, None]:
    del raw_token  # workbench 当前优先走真实 CLI，不依赖对话模型 token
    yield _sse_payload({"type": "status", "message": "正在进入云端工作台模式…"})
    yield _sse_payload({"type": "status", "message": "正在检查工作台 CLI 状态…"})
    if current_user is None:
        yield _sse_payload({
            "type": "status",
            "message": "当前未强制校验龙虾登录态，已进入测试模式；工作台会优先按真实 CLI 状态执行。",
        })
    try:
        result = handle_cli_prompt((payload.message or "").strip(), session_id=payload.session_id)
        for item in result.statuses:
            if item:
                yield _sse_payload({"type": "status", "message": item})
        meta = result.meta or {}
        project_id = str(meta.get("project_id") or "").strip()
        project_name = str(meta.get("project_name") or "当前任务").strip()
        if meta.get("poll_project") and project_id:
            waited_seconds = 0
            max_wait_seconds = 600
            yield _sse_payload({"type": "status", "message": build_project_progress_message(project_name, waited_seconds)})
            while waited_seconds < max_wait_seconds:
                ok, snapshot, error = get_project_result_snapshot(project_id)
                if ok and str(snapshot.get("status") or "").strip().lower() == "done":
                    answer = str(snapshot.get("answer") or "").strip()
                    if answer:
                        final_reply = attach_real_preview_url(answer, project_id)
                        await _record_workbench_turn(
                            payload=payload,
                            current_user=current_user,
                            db=db,
                            assistant_reply=final_reply,
                            invoke_model=result.invoke_model,
                            meta={**meta, "status": snapshot},
                        )
                        yield _sse_payload({"type": "done", "reply": final_reply, "invoke_model": result.invoke_model})
                        return
                if error and waited_seconds <= 0:
                    yield _sse_payload({"type": "status", "message": "云端正在处理中，我会继续帮你查询结果。"})
                await asyncio.sleep(10)
                waited_seconds += 10
                yield _sse_payload({"type": "status", "message": build_project_progress_message(project_name, waited_seconds)})
            final_reply = (
                f"{project_name} 还在云端持续处理中。这个任务可能还需要一些时间，"
                "你可以先去做别的事情，稍后回到这个对话时我会继续帮你查询。"
            )
        else:
            final_reply = result.reply or "云端工作台已收到你的任务。"
        await _record_workbench_turn(
            payload=payload,
            current_user=current_user,
            db=db,
            assistant_reply=final_reply,
            invoke_model=result.invoke_model,
            meta=meta,
        )
        yield _sse_payload({"type": "done", "reply": final_reply, "invoke_model": result.invoke_model})
    except HTTPException as exc:
        msg = str(exc.detail or "云端工作台暂时不可用")
        yield _sse_payload({"type": "done", "reply": f"错误：{msg}"})
    except Exception:
        logger.exception("[workbench_chat] run failed")
        yield _sse_payload({"type": "done", "reply": "错误：云端工作台处理失败，请稍后再试。"})


@router.post("/chat/workbench/stream", summary="云端工作台对话（流式）")
async def workbench_chat_stream_endpoint(
    request: Request,
    payload: WorkbenchChatRequest,
    db: Session = Depends(get_db),
):
    current_user, raw_token = await _optional_workbench_user(request)
    return StreamingResponse(
        _workbench_chat_stream(
            payload=payload,
            raw_token=raw_token,
            current_user=current_user,
            db=db,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Chat-Mode": "workspace_cli",
        },
    )
