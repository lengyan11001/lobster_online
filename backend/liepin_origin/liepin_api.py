# -*- coding: utf-8 -*-
"""猎聘技能 HTTP 入口（本地）。

客户端里的 AI 通过 MCP/能力网关调用这些接口；也支持直接 curl 调试。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .liepin_actions import run

router = APIRouter(prefix="/api/liepin", tags=["liepin"])


class LiepinActionIn(BaseModel):
    action: str = Field(..., description="能力 id，例如 liepin.candidates.search")
    params: Dict[str, Any] = Field(default_factory=dict)


@router.get("/status", summary="猎聘技能状态")
def liepin_status() -> Dict[str, Any]:
    return run("liepin.browser.open", {"action": "status"})


@router.get("/ledger", summary="猎聘动作台账")
def liepin_ledger(kind: Optional[str] = None) -> Dict[str, Any]:
    return run("liepin.ledger.read", {"kind": kind})


@router.post("/action", summary="执行猎聘能力")
def liepin_action(body: LiepinActionIn) -> Dict[str, Any]:
    return run(body.action, body.params)
