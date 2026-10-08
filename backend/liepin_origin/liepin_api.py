# -*- coding: utf-8 -*-
"""猎聘技能 HTTP 入口（本地）。

两类用法：
1) 通用入口：POST /api/liepin/action  {action, params}
2) 能力直连：每个能力一个 REST 路由（本地 MCP / mastra 就按这些路径调用）
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .liepin_actions import run

router = APIRouter(prefix="/api/liepin", tags=["liepin"])


class LiepinActionIn(BaseModel):
    action: str = Field(..., description="能力 id，例如 liepin.candidates.search")
    params: Dict[str, Any] = Field(default_factory=dict)


class BrowserIn(BaseModel):
    action: str = "start"


class SearchIn(BaseModel):
    query: str = ""
    page: int = 0
    pages: int = 1
    limit: int = 20
    mode: str = "auto"
    filters: Dict[str, Any] = Field(default_factory=dict)


class SuggestIn(BaseModel):
    keyword: str


class DetailIn(BaseModel):
    name: str
    age: Optional[int] = None
    confirm: bool = False


class ChatListIn(BaseModel):
    page: int = 0
    page_size: int = 30
    mode: str = "auto"


class ChatSendIn(BaseModel):
    target: str
    text: str
    confirm: bool = False


class LedgerIn(BaseModel):
    kind: Optional[str] = None


class ExportIn(BaseModel):
    rows: list = Field(default_factory=list)
    format: str = "xlsx"
    filename: str = ""


@router.get("/status", summary="猎聘技能状态")
def liepin_status() -> Dict[str, Any]:
    return run("liepin.browser.open", {"action": "status"})


@router.get("/ledger", summary="猎聘动作台账")
def liepin_ledger(kind: Optional[str] = None) -> Dict[str, Any]:
    return run("liepin.ledger.read", {"kind": kind})


@router.post("/action", summary="通用能力入口")
def liepin_action(body: LiepinActionIn) -> Dict[str, Any]:
    return run(body.action, body.params)


# ---------- 能力直连路由（给本地 MCP / mastra 用） ----------
@router.post("/browser/open", summary="启动/重启浏览器窗口")
def liepin_browser_open(body: BrowserIn) -> Dict[str, Any]:
    return run("liepin.browser.open", {"action": body.action})


@router.post("/login/status", summary="登录状态")
def liepin_login_status() -> Dict[str, Any]:
    return run("liepin.login.status", {})


@router.post("/session/info", summary="账号 + 权益额度 + 未读 + 新招呼")
def liepin_session_info() -> Dict[str, Any]:
    return run("liepin.session.info", {})


@router.post("/candidates/search", summary="搜索人才（协议直连，可翻页）")
def liepin_candidates_search(body: SearchIn) -> Dict[str, Any]:
    return run("liepin.candidates.search", body.model_dump())


@router.post("/candidates/suggest", summary="关键词联想")
def liepin_candidates_suggest(body: SuggestIn) -> Dict[str, Any]:
    return run("liepin.candidates.suggest", body.model_dump())


@router.post("/candidates/detail", summary="打开简历详情（消耗查看权益）")
def liepin_candidates_detail(body: DetailIn) -> Dict[str, Any]:
    return run("liepin.candidates.detail", body.model_dump())


@router.post("/chat/list", summary="沟通会话列表（分页）")
def liepin_chat_list(body: ChatListIn) -> Dict[str, Any]:
    return run("liepin.chat.list", body.model_dump())


@router.post("/chat/send", summary="发送沟通消息（需 confirm）")
def liepin_chat_send(body: ChatSendIn) -> Dict[str, Any]:
    return run("liepin.chat.send", body.model_dump())


@router.post("/applications/list", summary="求职者投递列表（分页）")
def liepin_applications_list(body: ChatListIn) -> Dict[str, Any]:
    return run("liepin.applications.list", {"page": body.page, "page_size": body.page_size})


@router.post("/ledger/read", summary="动作台账")
def liepin_ledger_read(body: LedgerIn) -> Dict[str, Any]:
    return run("liepin.ledger.read", body.model_dump())


@router.post("/report/export", summary="导出候选人 Excel/CSV")
def liepin_report_export(body: ExportIn) -> Dict[str, Any]:
    return run("liepin.report.export", body.model_dump())
