"""全局任务控制接口：悬浮暂停键 + 一键停所有在跑的自动化。

页面/悬浮键都可以用：
  GET  /api/client/tasks/status        谁在跑、跑了多久、暂停标记在不在、悬浮键在不在
  POST /api/client/tasks/stop-all      置位暂停标记（各引擎循环立刻能看见）+ 通知各引擎自己的 stop
  POST /api/client/tasks/resume        清掉暂停标记
  POST /api/client/task-overlay/start  拉起悬浮键（置顶小窗）
  POST /api/client/task-overlay/stop   关掉悬浮键
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..services import task_control
from .auth import _ServerUser, get_current_user_for_local


router = APIRouter()


class OverlayStartBody(BaseModel):
    idle_seconds: int = Field(default=90, ge=0, le=86400)


@router.get("/api/client/tasks/status")
async def client_tasks_status(current_user: _ServerUser = Depends(get_current_user_for_local)):
    del current_user
    return {"ok": True, "task": task_control.task_status(), "overlay": task_control.overlay_status()}


@router.post("/api/client/tasks/stop-all")
async def client_tasks_stop_all(current_user: _ServerUser = Depends(get_current_user_for_local)):
    del current_user
    return {"ok": True, **task_control.stop_all(reason="api")}


@router.post("/api/client/tasks/resume")
async def client_tasks_resume(current_user: _ServerUser = Depends(get_current_user_for_local)):
    del current_user
    return {"ok": True, **task_control.clear_stop()}


@router.post("/api/client/task-overlay/start")
async def client_task_overlay_start(
    body: OverlayStartBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return {"ok": True, "overlay": task_control.ensure_overlay(idle_seconds=body.idle_seconds)}


@router.post("/api/client/task-overlay/stop")
async def client_task_overlay_stop(current_user: _ServerUser = Depends(get_current_user_for_local)):
    del current_user
    return {"ok": True, "overlay": task_control.stop_overlay()}
