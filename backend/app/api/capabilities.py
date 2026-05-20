"""能力网关（在线客户端后端）。

速推类能力：用户算力预扣/结算/退款的 **唯一业务编排点** 在 **lobster_server** 的 MCP
``invoke_capability``（调用速推前在该处完成 pre-deduct 等）。本机 MCP 经 mcp-gateway 转发，**不经**本路由对速推做计费。

本文件 POST /capabilities/pre-deduct|record-call|refund **不做本地加减分**，仅原样转发认证中心（浏览器/旧客户端兼容）；
速推工具链请勿在本仓库另写第二套扣费。余额事实来源：认证中心（lobster-server）数据库。
"""
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import get_db
from .auth import get_current_user_for_chat, get_current_user_for_local, _ServerUser
from ..models import CapabilityCallLog, CapabilityConfig, User
from ..services.capability_cost_confirm import resolve_capability_confirm

router = APIRouter()


def _auth_server_base() -> str:
    base = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="未配置 AUTH_SERVER_BASE")
    return base


def _proxy_headers(request: Request) -> dict:
    """转发 Authorization、X-Installation-Id 及 MCP 计费信任头到认证中心。"""
    token = request.headers.get("Authorization") or ""
    h = {"Authorization": token, "Content-Type": "application/json"}
    iid = (request.headers.get("X-Installation-Id") or "").strip()
    if iid:
        h["X-Installation-Id"] = iid
    bk = (getattr(settings, "lobster_mcp_billing_internal_key", None) or "").strip()
    if not bk:
        bk = (os.environ.get("LOBSTER_MCP_BILLING_INTERNAL_KEY") or "").strip()
    if bk:
        h["X-Lobster-Mcp-Billing"] = bk
    return h


@router.get("/capabilities/available", summary="当前可用能力列表（本地）")
def list_available(
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    rows = db.query(CapabilityConfig).filter(CapabilityConfig.enabled.is_(True)).order_by(CapabilityConfig.capability_id).all()
    return {
        "capabilities": [
            {
                "capability_id": r.capability_id,
                "description": r.description,
                "upstream": r.upstream,
                "upstream_tool": r.upstream_tool,
                "arg_schema": r.arg_schema,
                "is_default": r.is_default,
                "unit_credits": r.unit_credits,
            }
            for r in rows
        ]
    }


@router.get("/capabilities/registry", summary="能力注册列表（本地）")
def list_registry(
    _: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    rows = db.query(CapabilityConfig).order_by(CapabilityConfig.capability_id).all()
    return [
        {
            "capability_id": r.capability_id,
            "description": r.description,
            "upstream": r.upstream,
            "upstream_tool": r.upstream_tool,
            "enabled": r.enabled,
            "is_default": r.is_default,
            "unit_credits": r.unit_credits,
        }
        for r in rows
    ]


class ConfirmCapabilityInvokeIn(BaseModel):
    confirm_token: str = Field(..., min_length=8)
    accept: bool = False


@router.post("/capabilities/confirm-invoke", summary="确认或取消「预估扣费后」的能力调用")
async def confirm_capability_invoke(
    body: ConfirmCapabilityInvokeIn,
    current_user: Union[User, _ServerUser] = Depends(get_current_user_for_chat),
):
    """与 chat/stream 推送的 confirm_token 配对；accept 后继续执行 MCP。"""
    ok = resolve_capability_confirm(body.confirm_token.strip(), int(current_user.id), body.accept)
    if not ok:
        raise HTTPException(status_code=404, detail="确认已失效或 token 无效，请重新发起对话")
    return {"ok": True, "accepted": body.accept}


class RecordCallIn(BaseModel):
    capability_id: str
    success: bool = True
    latency_ms: Optional[int] = None
    request_payload: Optional[dict] = None
    response_payload: Optional[dict] = None
    error_message: Optional[str] = None
    source: str = "mcp_invoke"
    chat_session_id: Optional[str] = None
    chat_context_id: Optional[str] = None
    """若由 pre-deduct 已扣过，传本次扣费数，避免重复扣。"""
    credits_charged: Optional[float] = None
    # 与认证中心 RecordCallIn 对齐；缺省时 JSON 会被本路由解析后丢弃，认证中心会走 direct_charge/unit 再扣一遍。
    pre_deduct_applied: bool = False
    credits_pre_deducted: Optional[float] = None
    credits_final: Optional[float] = None
    sutui_pool: Optional[str] = None
    sutui_token_ref: Optional[str] = None


_GENERATION_HISTORY_CAPABILITIES = {
    "image.generate",
    "video.generate",
    "task.get_result",
    "goal.video.pipeline",
    "comfly.daihuo",
    "comfly.daihuo.pipeline",
    "comfly.veo",
    "comfly.seedance.tvc.pipeline",
    "seedance.tvc.pipeline",
    "hifly.video.create_by_tts",
    "hifly.digital_human",
    "media.edit",
}

_TASK_ID_KEYS = ("task_id", "taskId", "taskid", "TaskId")
_STATUS_KEYS = ("status", "state", "task_status", "taskStatus")
_MODEL_KEYS = ("model", "model_id", "modelId", "model_name", "modelName")
_PROMPT_KEYS = ("prompt", "goal", "text", "content", "script", "voice_text", "speech_text", "narration")
_URL_RE = re.compile(r"https?://[^\s\"'<>)\]}]+", re.I)


def _maybe_json(v: Any) -> Any:
    if not isinstance(v, str):
        return v
    s = v.strip()
    if not s or s[0] not in "{[":
        return v
    try:
        return json.loads(s)
    except Exception:
        return v


def _walk_values(obj: Any, *, max_depth: int = 8):
    if max_depth < 0:
        return
    obj = _maybe_json(obj)
    yield obj
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_values(v, max_depth=max_depth - 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_values(v, max_depth=max_depth - 1)


def _first_deep_value(obj: Any, keys: tuple[str, ...]) -> str:
    for item in _walk_values(obj):
        if not isinstance(item, dict):
            continue
        for k in keys:
            if k not in item:
                continue
            v = item.get(k)
            if v is None:
                continue
            s = str(v).strip()
            if not s:
                continue
            if k == "id" and len(s) < 8:
                continue
            return s
    return ""


def _extract_prompt(obj: Any) -> str:
    for item in _walk_values(obj):
        if not isinstance(item, dict):
            continue
        for k in _PROMPT_KEYS:
            v = item.get(k)
            if not isinstance(v, str):
                continue
            s = v.strip()
            if s and "http://" not in s and "https://" not in s:
                return s[:500]
    return ""


def _extract_media_urls(obj: Any) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in _walk_values(obj):
        if isinstance(item, dict):
            for k in ("url", "source_url", "media_url", "video_url", "image_url", "download_url", "preview_url"):
                v = item.get(k)
                if isinstance(v, str):
                    if k == "image_url" and "video" in str(item.get("model") or "").lower():
                        continue
                    for m in _URL_RE.findall(v):
                        u = m.rstrip(".,;，。；")
                        if u not in seen:
                            seen.add(u)
                            out.append(u)
        elif isinstance(item, str):
            for m in _URL_RE.findall(item):
                u = m.rstrip(".,;，。；")
                if u not in seen:
                    seen.add(u)
                    out.append(u)
    return out[:12]


def _extract_saved_assets(obj: Any) -> List[Dict[str, Any]]:
    assets: List[Dict[str, Any]] = []
    seen = set()
    for item in _walk_values(obj):
        if not isinstance(item, dict):
            continue
        for k in ("saved_assets", "assets"):
            v = item.get(k)
            if not isinstance(v, list):
                continue
            for a in v:
                if not isinstance(a, dict):
                    continue
                aid = str(a.get("asset_id") or a.get("id") or "").strip()
                url = str(a.get("source_url") or a.get("url") or a.get("media_url") or "").strip()
                key = aid or url
                if not key or key in seen:
                    continue
                seen.add(key)
                assets.append(
                    {
                        "asset_id": aid,
                        "url": url,
                        "media_type": a.get("media_type") or a.get("type") or "",
                    }
                )
    return assets[:12]


def _status_in_progress(status: str) -> bool:
    s = (status or "").strip().lower()
    return any(x in s for x in ("pending", "process", "running", "queue", "submitted", "生成中", "排队", "进行中"))


def _row_to_dict(row: CapabilityCallLog, source: str) -> Dict[str, Any]:
    return {
        "id": row.id,
        "source": source,
        "capability_id": row.capability_id,
        "success": row.success,
        "credits_charged": row.credits_charged,
        "latency_ms": row.latency_ms,
        "request_payload": row.request_payload,
        "response_payload": row.response_payload,
        "error_message": row.error_message,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


def _normalize_generation_log(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cap = str(raw.get("capability_id") or "").strip()
    req = raw.get("request_payload") or {}
    resp = raw.get("response_payload") or {}
    task_id = _first_deep_value(resp, _TASK_ID_KEYS) or _first_deep_value(req, _TASK_ID_KEYS)
    request_urls = set(_extract_media_urls(req))
    media_urls = [u for u in _extract_media_urls(resp) if u not in request_urls]
    saved_assets = _extract_saved_assets(resp)
    if cap not in _GENERATION_HISTORY_CAPABILITIES and not task_id and not media_urls and not saved_assets:
        return None
    status_text = str(raw.get("status") or "").strip() or _first_deep_value(resp, _STATUS_KEYS)
    if not status_text:
        if raw.get("success") is False:
            status_text = "failed"
        elif media_urls or saved_assets:
            status_text = "completed"
        else:
            status_text = "submitted"
    if _status_in_progress(status_text) and not saved_assets:
        media_urls = []
    prompt = _extract_prompt(req) or _extract_prompt(resp)
    return {
        "id": raw.get("id"),
        "source": raw.get("source") or "",
        "capability_id": cap,
        "task_id": task_id,
        "status": status_text,
        "success": raw.get("success"),
        "created_at": raw.get("created_at") or "",
        "updated_at": raw.get("created_at") or "",
        "credits_charged": raw.get("credits_charged"),
        "latency_ms": raw.get("latency_ms"),
        "model": _first_deep_value(req, _MODEL_KEYS) or _first_deep_value(resp, _MODEL_KEYS),
        "prompt": prompt,
        "media_urls": media_urls,
        "saved_assets": saved_assets,
        "error_message": raw.get("error_message") or "",
        "logs": [
            {
                "id": raw.get("id"),
                "source": raw.get("source") or "",
                "capability_id": cap,
                "status": status_text,
                "success": raw.get("success"),
                "created_at": raw.get("created_at") or "",
                "error_message": raw.get("error_message") or "",
            }
        ],
    }


def _ts_key(s: str) -> float:
    if not s:
        return 0.0
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _merge_generation_logs(raw_logs: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    rows = []
    for raw in raw_logs:
        n = _normalize_generation_log(raw)
        if n:
            rows.append(n)
    rows.sort(key=lambda x: _ts_key(x.get("created_at") or ""))
    for item in rows:
        key = item.get("task_id") or f"{item.get('source')}:{item.get('id')}"
        g = groups.get(key)
        if not g:
            g = dict(item)
            groups[key] = g
            continue
        if item.get("created_at") and _ts_key(item["created_at"]) >= _ts_key(g.get("updated_at") or ""):
            g["updated_at"] = item["created_at"]
            g["status"] = item.get("status") or g.get("status")
            g["success"] = item.get("success")
            g["error_message"] = item.get("error_message") or g.get("error_message") or ""
        for k in ("task_id", "model", "prompt", "capability_id"):
            if item.get(k) and not g.get(k):
                g[k] = item[k]
        for k in ("media_urls", "saved_assets"):
            existing = g.get(k) or []
            seen = {json.dumps(v, sort_keys=True, ensure_ascii=False) for v in existing}
            for v in item.get(k) or []:
                sig = json.dumps(v, sort_keys=True, ensure_ascii=False)
                if sig not in seen:
                    seen.add(sig)
                    existing.append(v)
            g[k] = existing[:12]
        g.setdefault("logs", []).extend(item.get("logs") or [])
    out = list(groups.values())
    out.sort(key=lambda x: _ts_key(x.get("updated_at") or x.get("created_at") or ""), reverse=True)
    return out[:limit]


def _mcp_text_from_response(obj: Any) -> str:
    obj = _maybe_json(obj)
    if isinstance(obj, dict):
        content = ((obj.get("result") or {}).get("content") if isinstance(obj.get("result"), dict) else None)
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict) and isinstance(c.get("text"), str):
                    parts.append(c["text"])
            if parts:
                return "\n".join(parts)
        for k in ("text", "message", "detail"):
            if isinstance(obj.get(k), str):
                return obj[k]
    return obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)


@router.post("/capabilities/pre-deduct", summary="预扣算力（代理到认证中心）")
async def pre_deduct(request: Request):
    """代理到认证中心预扣；一般由 MCP 调用。"""
    base = _auth_server_base()
    body = await request.body()
    from fastapi.responses import Response

    h = _proxy_headers(request)
    ct = (request.headers.get("content-type") or "").strip() or "application/json"
    h["Content-Type"] = ct
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{base}/capabilities/pre-deduct",
            content=body,
            headers=h,
        )
    mt = r.headers.get("content-type") or "application/json"
    return Response(content=r.content, status_code=r.status_code, media_type=mt)


class RefundIn(BaseModel):
    capability_id: str
    credits: float


@router.post("/capabilities/refund", summary="退还预扣算力（代理到认证中心）")
async def refund_credits(body: RefundIn, request: Request):
    base = _auth_server_base()
    h = _proxy_headers(request)
    h["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{base}/capabilities/refund",
            json=body.model_dump(),
            headers=h,
        )
    from fastapi.responses import Response
    return Response(content=r.content, status_code=r.status_code, media_type="application/json")


@router.post("/capabilities/record-call", summary="记录能力调用并扣算力（代理到认证中心）")
async def record_call(body: RecordCallIn, request: Request):
    base = _auth_server_base()
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{base}/capabilities/record-call",
            json=body.model_dump(),
            headers=_proxy_headers(request),
        )
    from fastapi.responses import Response
    return Response(content=r.content, status_code=r.status_code, media_type="application/json")


@router.get("/capabilities/my-call-logs", summary="我的能力调用记录（本地）")
def my_call_logs(
    capability_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    q = db.query(CapabilityCallLog).filter(CapabilityCallLog.user_id == current_user.id)
    if capability_id:
        q = q.filter(CapabilityCallLog.capability_id == capability_id)
    rows = q.order_by(CapabilityCallLog.created_at.desc()).offset(max(offset, 0)).limit(min(max(limit, 1), 200)).all()
    return [
        {
            "id": r.id,
            "capability_id": r.capability_id,
            "success": r.success,
            "credits_charged": r.credits_charged,
            "latency_ms": r.latency_ms,
            "request_payload": r.request_payload,
            "response_payload": r.response_payload,
            "error_message": r.error_message,
            "source": r.source,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in rows
    ]


@router.get("/api/generation-history", summary="生成历史：按 task_id 聚合生成与查询记录")
async def generation_history(
    request: Request,
    limit: int = 80,
    offset: int = 0,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    """给前端“生成历史”使用。

    在线版的速推生成事实来源在 lobster_server；本机库只保留少量兼容记录。
    因此这里合并本地 CapabilityCallLog 与认证中心 /capabilities/my-call-logs，再按 task_id 聚合。
    """
    lim = min(max(limit, 1), 100)
    off = max(offset, 0)
    fetch_limit = min(max((lim + off) * 4, 120), 200)
    raw_logs: List[Dict[str, Any]] = []
    remote_error = ""

    try:
        user_id = int(getattr(current_user, "id", 0) or 0)
    except Exception:
        user_id = 0
    if user_id:
        q = (
            db.query(CapabilityCallLog)
            .filter(CapabilityCallLog.user_id == user_id)
            .order_by(CapabilityCallLog.created_at.desc())
            .limit(fetch_limit)
        )
        for row in q.all():
            raw_logs.append(_row_to_dict(row, "local"))

    base = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    if base:
        try:
            headers = _proxy_headers(request)
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    f"{base}/capabilities/my-call-logs",
                    params={"limit": fetch_limit, "offset": 0},
                    headers=headers,
                )
            if r.status_code == 200:
                data = r.json()
                rows = data if isinstance(data, list) else (data.get("items") if isinstance(data, dict) else [])
                if isinstance(rows, list):
                    for row in rows:
                        if isinstance(row, dict):
                            rr = dict(row)
                            rr["source"] = "cloud"
                            raw_logs.append(rr)
            else:
                remote_error = f"云端记录读取失败：HTTP {r.status_code}"
        except Exception as e:
            remote_error = f"云端记录读取失败：{e}"

    merged = _merge_generation_logs(raw_logs, limit=max(fetch_limit, off + lim))
    page = merged[off : off + lim]
    return {
        "items": page,
        "total": len(merged),
        "offset": off,
        "limit": lim,
        "remote_error": remote_error,
        "note": "生成任务记录来自 capability_call_logs；当前页按 task_id 聚合，查询进度走 task.get_result。",
    }


class GenerationHistoryRefreshIn(BaseModel):
    task_id: str = Field(..., min_length=6)
    origin_capability_id: Optional[str] = None


@router.post("/api/generation-history/refresh", summary="生成历史：按 task_id 查询进度")
async def refresh_generation_history_item(
    body: GenerationHistoryRefreshIn,
    request: Request,
    _: _ServerUser = Depends(get_current_user_for_local),
):
    task_id = (body.task_id or "").strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id 不能为空")
    base = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="未配置 AUTH_SERVER_BASE，无法通过云端查询 task.get_result")
    rpc = {
        "jsonrpc": "2.0",
        "id": f"generation-history-{int(datetime.utcnow().timestamp())}",
        "method": "tools/call",
        "params": {
            "name": "invoke_capability",
            "arguments": {
                "capability_id": "task.get_result",
                "payload": {"task_id": task_id},
            },
        },
    }
    headers = _proxy_headers(request)
    headers["X-Lobster-OpenClaw-Mcp"] = "1"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(240.0, connect=20.0)) as client:
            r = await client.post(f"{base}/mcp-gateway", json=rpc, headers=headers)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"查询进度失败：{e}") from e
    if r.status_code >= 400:
        detail = r.text[:500] if r.text else f"HTTP {r.status_code}"
        raise HTTPException(status_code=502, detail=f"查询进度失败：{detail}")
    try:
        data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"查询进度返回不是 JSON：{e}") from e
    text = _mcp_text_from_response(data)
    parsed_text = _maybe_json(text)
    status_text = _first_deep_value(parsed_text, _STATUS_KEYS) or _first_deep_value(data, _STATUS_KEYS)
    media_urls = _extract_media_urls(parsed_text) or _extract_media_urls(data)
    saved_assets = _extract_saved_assets(parsed_text) or _extract_saved_assets(data)
    if not status_text:
        status_text = "completed" if (media_urls or saved_assets) else "processing"
    if _status_in_progress(status_text) and not saved_assets:
        media_urls = []
    is_error = bool(((data.get("result") or {}).get("isError")) if isinstance(data, dict) else False)
    return {
        "ok": not is_error,
        "task_id": task_id,
        "origin_capability_id": body.origin_capability_id or "",
        "status": status_text,
        "media_urls": media_urls,
        "saved_assets": saved_assets,
        "result_text": text[:4000],
        "raw": data,
    }


