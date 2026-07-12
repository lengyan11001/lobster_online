from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..core.config import settings
from ..services.goal_video_job_store import (
    append_goal_video_progress,
    create_goal_video_job,
    get_goal_video_job,
    update_goal_video_job,
)
from .auth import _ServerUser, get_current_user_media_edit

logger = logging.getLogger(__name__)

router = APIRouter()


def _local_mcp_url() -> str:
    port = os.environ.get("MCP_PORT") or str(getattr(settings, "mcp_port", 8001))
    return f"http://127.0.0.1:{port}/mcp"
TERMINAL_SUCCESS = {"completed", "complete", "success", "succeeded", "finished", "done"}
TERMINAL_FAILURE = {"failed", "error", "cancelled", "canceled", "timeout", "rejected"}
PIPELINE_PRECHARGED_CONTEXT_KEY = "_lobster_pipeline_precharged"
VIDEO_NO_TEXT_CONSTRAINT = (
    "画面中不要出现任何可读文字、字母、数字、字幕、标题、商标标识、水印、招牌、界面元素、标签、"
    "英文、拼音、随机乱码字符或伪文字；只用真实画面、构图、光影和人物/产品动作表达。"
)


class GoalVideoPipelinePayload(BaseModel):
    action: str = Field("run_pipeline", description="run_pipeline/start_pipeline/poll_pipeline")
    job_id: Optional[str] = None
    goal: str = Field("", description="用户目标，例如：给某产品生成 6 秒宣传视频")
    platform: str = "douyin"
    language: str = "中文"
    duration: Optional[int] = Field(6, ge=3, le=60)
    aspect_ratio: str = "9:16"
    memory_scope: str = "default"
    planning_model: Optional[str] = None
    image_model: Optional[str] = None
    video_model: Optional[str] = None
    precomputed_plan: Dict[str, Any] = Field(default_factory=dict)
    reference_asset_ids: List[str] = Field(default_factory=list)
    reference_image_urls: List[str] = Field(default_factory=list)
    memory_doc_ids: List[str] = Field(default_factory=list)
    image_retry_count: int = Field(2, ge=0, le=5)
    video_retry_count: int = Field(2, ge=0, le=5)
    poll_interval_seconds: int = Field(12, ge=5, le=60)
    image_poll_timeout_seconds: int = Field(900, ge=60, le=3600)
    video_poll_timeout_seconds: int = Field(2400, ge=120, le=7200)


class GoalVideoPipelineBody(BaseModel):
    payload: GoalVideoPipelinePayload


class PipelinePartialResultError(RuntimeError):
    def __init__(self, message: str, partial_result: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.partial_result = partial_result or {}


def _raw_token_from_request(request: Request) -> str:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _installation_id_from_request(request: Request, user_id: int) -> str:
    xi = (request.headers.get("X-Installation-Id") or request.headers.get("x-installation-id") or "").strip()
    return xi or f"lobster-internal-{int(user_id)}"


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("LLM returned empty text")
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw, re.IGNORECASE)
    blob = fenced.group(1) if fenced else raw[raw.find("{") : raw.rfind("}") + 1]
    if not blob or not blob.startswith("{") or not blob.endswith("}"):
        raise ValueError("LLM did not return a JSON object")
    data = json.loads(blob)
    if not isinstance(data, dict):
        raise ValueError("LLM JSON root is not an object")
    return data


def _safe_str(value: Any, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _with_video_no_text_constraint(prompt: Any, limit: int = 2500) -> str:
    text = _safe_str(prompt, limit).strip()
    if not text:
        return VIDEO_NO_TEXT_CONSTRAINT[:limit]
    if "不要出现任何可读文字" in text or "随机乱码字符" in text:
        return text[:limit]
    return f"{text}\n\n反向约束：{VIDEO_NO_TEXT_CONSTRAINT}"[:limit]


def _json_preview(value: Any, limit: int = 1200) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return text.strip()[:limit]


def _dedupe_strings(items: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for raw in items:
        s = (raw or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _collect_urls(obj: Any, *, want: str = "") -> List[str]:
    out: List[str] = []
    if obj is None:
        return out
    if isinstance(obj, str):
        for u in re.findall(r"https?://[^\s\"'<>，。；;、)\]\}]+", obj):
            low = u.lower().split("?", 1)[0].split("#", 1)[0]
            is_video = low.endswith((".mp4", ".webm", ".mov", ".m4v", ".avi"))
            is_image = low.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"))
            if want == "video" and is_image and not is_video:
                continue
            if want == "image" and is_video:
                continue
            if want == "video" and not is_video:
                continue
            if want == "image" and not is_image:
                continue
            out.append(u)
        return out
    if isinstance(obj, dict):
        for v in obj.values():
            out.extend(_collect_urls(v, want=want))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_collect_urls(v, want=want))
    return _dedupe_strings(out)


def _url_matches_kind(url: Any, kind: str) -> bool:
    low = str(url or "").strip().lower().split("?", 1)[0].split("#", 1)[0]
    if kind == "video":
        return low.endswith((".mp4", ".webm", ".mov", ".m4v", ".avi"))
    if kind == "image":
        return low.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"))
    return bool(low)


def _saved_assets_for_kind(saved: List[Dict[str, Any]], kind: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in saved or []:
        if not isinstance(item, dict):
            continue
        mt = str(item.get("media_type") or item.get("type") or "").strip().lower()
        if mt:
            if kind == "video" and mt != "video":
                continue
            if kind == "image" and mt != "image":
                continue
        elif kind in {"video", "image"}:
            refs = [
                item.get("filename"),
                item.get("url"),
                item.get("source_url"),
                item.get("public_url"),
                item.get("preview_url"),
            ]
            if not any(_url_matches_kind(ref, kind) for ref in refs):
                continue
        out.append(item)
    return out


def _collect_task_ids(obj: Any) -> List[str]:
    out: List[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in {"task_id", "taskId", "taskid", "id"} and isinstance(v, (str, int)):
                s = str(v).strip()
                if len(s) >= 8:
                    out.append(s)
            out.extend(_collect_task_ids(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_collect_task_ids(v))
    return _dedupe_strings(out)


def _collect_saved_assets(obj: Any) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    if isinstance(obj, dict):
        saved = obj.get("saved_assets")
        if isinstance(saved, list):
            for item in saved:
                if isinstance(item, dict):
                    aid = _safe_str(item.get("asset_id") or item.get("id"), 64)
                    if aid:
                        found.append(dict(item, asset_id=aid))
        asset = obj.get("asset")
        if isinstance(asset, dict):
            aid = _safe_str(asset.get("asset_id") or asset.get("id"), 64)
            if aid:
                found.append(dict(asset, asset_id=aid))
        for v in obj.values():
            found.extend(_collect_saved_assets(v))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(_collect_saved_assets(v))
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for item in found:
        aid = _safe_str(item.get("asset_id"), 64)
        if aid and aid not in seen:
            seen.add(aid)
            out.append(item)
    return out


def _extract_status(obj: Any) -> str:
    if isinstance(obj, dict):
        for k in ("status", "state", "task_status", "taskStatus"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip().lower()
        for v in obj.values():
            st = _extract_status(v)
            if st:
                return st
    elif isinstance(obj, list):
        for v in obj:
            st = _extract_status(v)
            if st:
                return st
    return ""


def _failure_message(obj: Any) -> str:
    if isinstance(obj, dict):
        for k in ("error", "message", "detail", "fail_reason", "reason"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()[:1000]
            if isinstance(v, dict):
                nested = _failure_message(v)
                if nested:
                    return nested
        for v in obj.values():
            msg = _failure_message(v)
            if msg:
                return msg
    elif isinstance(obj, list):
        for v in obj:
            msg = _failure_message(v)
            if msg:
                return msg
    return ""


def _has_failure_marker(obj: Any, _depth: int = 0) -> bool:
    if _depth > 12:
        return False
    if isinstance(obj, dict):
        if obj.get("ok") is False or obj.get("success") is False or obj.get("isError") is True:
            return True
        for k in ("error", "errors", "exception", "fail_reason", "failure_reason"):
            if obj.get(k):
                return True
        status = _extract_status(obj)
        if status in TERMINAL_FAILURE:
            return True
        for k in ("result", "data", "payload", "content"):
            v = obj.get(k)
            if v is not obj and _has_failure_marker(v, _depth + 1):
                return True
    elif isinstance(obj, list):
        return any(_has_failure_marker(v, _depth + 1) for v in obj)
    return False


def _is_non_retryable_generation_error(exc: BaseException) -> bool:
    text = str(exc or "")
    low = text.lower()
    return (
        "http 400" in low
        or "上游 rest http 400" in low
        or "bad request" in low
        or "image_size 必须" in text
        or "aspect_ratio 必须" in text
        or "invalid parameter" in low
        or "invalid request" in low
        or "http 402" in low
        or "payment required" in low
        or "insufficient balance" in low
        or "算力不足" in text
        or "余额不足" in text
        or "预扣需" in text
    )


def _mcp_headers(token: str, installation_id: str) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if installation_id:
        headers["X-Installation-Id"] = installation_id
    return headers


def _billing_base() -> str:
    return (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")


def _billing_headers(token: str, installation_id: str) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if installation_id:
        headers["X-Installation-Id"] = installation_id
    billing_key = (getattr(settings, "lobster_mcp_billing_internal_key", None) or "").strip()
    if billing_key:
        headers["X-Lobster-Mcp-Billing"] = billing_key
    return headers


async def _pre_deduct_pipeline_total(
    *,
    capability_id: str,
    payload: Dict[str, Any],
    token: str,
    installation_id: str,
) -> Dict[str, Any]:
    base = _billing_base()
    if not base or not token:
        raise RuntimeError("pipeline pre-deduct unavailable: missing auth server or user token")
    body = {"capability_id": capability_id, "params": payload}
    headers = _billing_headers(token, installation_id)
    headers["X-Billing-Idempotency-Key"] = f"pipeline:{capability_id}:{uuid.uuid4().hex}"
    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
        resp = await client.post(f"{base}/capabilities/pre-deduct", json=body, headers=headers)
    data = resp.json() if resp.content else {}
    if resp.status_code == 402:
        detail = data.get("detail") if isinstance(data, dict) else ""
        raise RuntimeError(str(detail or "算力不足，请先充值"))
    if resp.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else ""
        raise RuntimeError(str(detail or resp.text or f"pipeline pre-deduct HTTP {resp.status_code}")[:1000])
    return data if isinstance(data, dict) else {"credits_charged": 0}


async def _refund_pipeline_total(
    *,
    capability_id: str,
    credits: Any,
    token: str,
    installation_id: str,
) -> None:
    try:
        amount = float(credits or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0 or not token:
        return
    base = _billing_base()
    if not base:
        return
    try:
        async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
            await client.post(
                f"{base}/capabilities/refund",
                json={"capability_id": capability_id, "credits": amount},
                headers=_billing_headers(token, installation_id),
            )
    except Exception as exc:
        logger.warning("[pipeline.billing] refund failed capability_id=%s credits=%s: %s", capability_id, amount, exc)


async def _record_pipeline_total(
    *,
    capability_id: str,
    payload: Dict[str, Any],
    result: Optional[Dict[str, Any]],
    token: str,
    installation_id: str,
    credits_charged: Any,
    success: bool,
    error_message: str = "",
) -> None:
    if not token:
        return
    base = _billing_base()
    if not base:
        return
    try:
        amount = float(credits_charged or 0)
    except (TypeError, ValueError):
        amount = 0.0
    body = {
        "capability_id": capability_id,
        "success": success,
        "request_payload": payload,
        "response_payload": result or {},
        "error_message": (error_message or "")[:1000] or None,
        "source": "pipeline_total",
        "credits_charged": amount,
        "pre_deduct_applied": amount > 0,
        "credits_pre_deducted": amount if amount > 0 else None,
    }
    try:
        async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
            await client.post(f"{base}/capabilities/record-call", json=body, headers=_billing_headers(token, installation_id))
    except Exception as exc:
        logger.warning("[pipeline.billing] record failed capability_id=%s: %s", capability_id, exc)


def _pipeline_context_headers(context: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not isinstance(context, dict) or not context.get("precharged"):
        return {}
    headers = {"X-Lobster-Pipeline-Precharged": "1"}
    pipeline_id = str(context.get("pipeline_id") or "").strip()
    capability_id = str(context.get("capability_id") or "").strip()
    if pipeline_id:
        headers["X-Lobster-Pipeline-Id"] = pipeline_id[:128]
    if capability_id:
        headers["X-Lobster-Pipeline-Capability"] = capability_id[:128]
    return headers


def _parse_mcp_text_response(data: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(data.get("result"), dict):
        result = data["result"]
        is_error = bool(result.get("isError"))
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                text = item.get("text")
                if isinstance(text, str):
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict):
                            if is_error:
                                parsed = dict(parsed)
                                parsed.setdefault("isError", True)
                            return parsed
                    except Exception:
                        return {"error": text, "isError": True} if is_error else {"text": text}
    return data


async def _invoke_capability(
    *,
    capability_id: str,
    payload: Dict[str, Any],
    token: str,
    installation_id: str,
    timeout: float,
    pipeline_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    body = {
        "jsonrpc": "2.0",
        "id": f"goal-video-{int(time.time() * 1000)}",
        "method": "tools/call",
        "params": {
            "name": "invoke_capability",
            "arguments": {
                "capability_id": capability_id,
                "payload": payload,
            },
        },
    }
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        headers = _mcp_headers(token, installation_id)
        headers.update(_pipeline_context_headers(pipeline_context))
        r = await client.post(_local_mcp_url(), json=body, headers=headers)
    try:
        data = r.json() if r.content else {}
    except Exception as e:
        raise RuntimeError(f"MCP returned non-JSON HTTP {r.status_code}: {(r.text or '')[:500]}") from e
    parsed = _parse_mcp_text_response(data)
    if r.status_code >= 400:
        raise RuntimeError(f"MCP HTTP {r.status_code}: {_failure_message(parsed) or str(parsed)[:500]}")
    if data.get("error"):
        raise RuntimeError(_failure_message(data.get("error")) or str(data.get("error"))[:500])
    if isinstance(parsed, dict) and _has_failure_marker(parsed):
        raise RuntimeError(_failure_message(parsed) or _json_preview(parsed, 1000))
    return parsed


async def _retry_async(
    label: str,
    attempts: int,
    func: Callable[[], Any],
    progress: Optional[Callable[[str, str, Optional[Dict[str, Any]]], None]] = None,
) -> Any:
    last: Optional[BaseException] = None
    total = max(1, int(attempts))
    for idx in range(1, total + 1):
        try:
            if progress:
                progress(label, f"{label} attempt {idx}/{total}", {"attempt": idx, "total": total})
            return await func()
        except Exception as e:
            last = e
            logger.warning("[goal.video.pipeline] %s attempt %s/%s failed: %s", label, idx, total, e)
            if _is_non_retryable_generation_error(e):
                raise RuntimeError(f"{label} failed: {e}") from e
            if idx < total:
                await asyncio.sleep(min(8.0, 1.5 * idx))
    raise RuntimeError(f"{label} failed after {total} attempts: {last}") from last


def _clean_memory_doc_ids(raw: Any, limit: int = 8) -> List[str]:
    source = raw if isinstance(raw, list) else []
    out: List[str] = []
    for item in source:
        text = re.sub(r"[^A-Za-z0-9_-]", "", str(item or "").strip())[:80]
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _selected_memory_context(*, doc_ids: List[str], token: str, installation_id: str) -> str:
    selected_ids = _clean_memory_doc_ids(doc_ids)
    if not selected_ids:
        return ""
    try:
        from .openclaw_chat_gateway import _decode_token_user_id
        from .openclaw_memory import _load_index, _read_canonical_memory_content

        user_id = _decode_token_user_id(token, installation_id)
        if not user_id:
            return ""
        rows = _load_index(int(user_id))
        by_id = {str(row.get("id") or "").strip(): row for row in rows if isinstance(row, dict)}
        lines: List[str] = []
        total = 0
        for doc_id in selected_ids:
            row = by_id.get(doc_id)
            if not row:
                continue
            title = str(row.get("title") or row.get("filename") or doc_id).strip()
            text = _read_canonical_memory_content(row, max_chars=2400)
            if not text:
                continue
            chunk = f"## 记忆文件：{title}\n{text}"
            remain = 12000 - total
            if remain <= 0:
                break
            if len(chunk) > remain:
                chunk = chunk[:remain]
            lines.append(chunk.strip())
            total += len(chunk)
        return "\n\n".join(line for line in lines if line).strip()
    except Exception as exc:
        logger.warning("[goal.video.pipeline] selected memory context unavailable: %s", exc)
        return ""


async def _call_planning_llm(*, pl: GoalVideoPipelinePayload, token: str, installation_id: str) -> Dict[str, Any]:
    asb = (settings.auth_server_base or "").strip().rstrip("/")
    if not asb or not token:
        raise RuntimeError("AUTH_SERVER_BASE or user token is missing; cannot build goal video plan")

    selected_memory_doc_ids = _clean_memory_doc_ids(pl.memory_doc_ids)
    memory_context = ""
    if selected_memory_doc_ids:
        memory_context = _selected_memory_context(doc_ids=selected_memory_doc_ids, token=token, installation_id=installation_id)
        if not memory_context:
            raise RuntimeError("选中的记忆文件未同步到本机，无法按记忆生成创意视频")
    else:
        try:
            from .openclaw_chat_gateway import _build_openclaw_memory_context

            memory_context = _build_openclaw_memory_context(
                [{"role": "user", "content": pl.goal}],
                token,
                installation_id,
                pl.memory_scope,
            )
        except Exception as e:
            logger.warning("[goal.video.pipeline] memory context unavailable: %s", e)

    model = (
        (pl.planning_model or "").strip()
        or (settings.lobster_orchestration_sutui_chat_model or "").strip()
        or (settings.lobster_default_sutui_chat_model or "").strip()
        or "deepseek-chat"
    )
    system = (
        "你是一个短视频成片流水线规划器。只输出一个 JSON 对象，不要 Markdown。\n"
        "根据用户目标和记忆资料，生成真实可执行的宣传视频方案。\n"
        "必须包含字段：selling_points(array), copy(string), image_prompt(string), video_prompt(string), title(string)。\n"
        "不要编造素材 ID、任务 ID、费用或已完成状态。image_prompt 只描述需要生成的关键画面；"
        "video_prompt 只描述从该图片生成视频的镜头运动、主体动作和氛围。"
        "video_prompt 不要要求字幕、标题、文字、字母、数字、商标标识、水印或任何可读字符。"
    )
    user = {
        "goal": pl.goal,
        "platform": pl.platform,
        "language": pl.language,
        "duration": pl.duration,
        "aspect_ratio": pl.aspect_ratio,
        "memory_context": memory_context[:12000],
        "memory_doc_ids": selected_memory_doc_ids,
        "reference_asset_ids": pl.reference_asset_ids,
        "reference_image_urls": pl.reference_image_urls,
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        "stream": False,
        "temperature": 0.2,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "X-Installation-Id": installation_id,
    }
    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
        r = await client.post(f"{asb}/api/sutui-chat/completions", json=body, headers=headers)
    if r.status_code >= 400:
        raise RuntimeError(f"sutui-chat HTTP {r.status_code}: {(r.text or '')[:800]}")
    data = r.json() if r.content else {}
    content = ""
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        content = _safe_str(data, 4000)
    plan = _extract_json_object(content)
    plan["selling_points"] = [str(x).strip() for x in (plan.get("selling_points") or []) if str(x).strip()][:8]
    plan["copy"] = _safe_str(plan.get("copy"), 2000)
    plan["image_prompt"] = _safe_str(plan.get("image_prompt"), 2500)
    plan["video_prompt"] = _with_video_no_text_constraint(plan.get("video_prompt"), 2500)
    plan["title"] = _safe_str(plan.get("title"), 120)
    if not plan["image_prompt"] or not plan["video_prompt"]:
        raise RuntimeError("plan missing image_prompt or video_prompt")
    return plan


async def _submit_and_wait_generation(
    *,
    kind: str,
    submit_payload: Dict[str, Any],
    token: str,
    installation_id: str,
    timeout_seconds: int,
    interval_seconds: int,
    progress: Callable[[str, str, Optional[Dict[str, Any]]], None],
    pipeline_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    capability_id = "image.generate" if kind == "image" else "video.generate"
    progress(f"{kind}_submit", f"submit {capability_id}", None)
    submit = await _invoke_capability(
        capability_id=capability_id,
        payload=submit_payload,
        token=token,
        installation_id=installation_id,
        timeout=180.0,
        pipeline_context=pipeline_context,
    )
    task_ids = _collect_task_ids(submit)
    task_id = task_ids[0] if task_ids else ""
    saved = _saved_assets_for_kind(_collect_saved_assets(submit), kind)
    urls = _collect_urls(submit, want=kind)
    if saved or urls:
        return {
            "task_id": task_id,
            "submit_result": submit,
            "final_result": submit,
            "saved_assets": saved,
            "media_urls": urls,
        }
    if not task_id:
        detail = _failure_message(submit) or _json_preview(submit, 1200)
        logger.warning(
            "[goal.video.pipeline] %s submit returned no task/media payload=%s",
            capability_id,
            detail,
        )
        raise RuntimeError(f"{capability_id} did not return task_id/media: {detail}")

    progress(f"{kind}_poll", f"poll task {task_id[:32]}", {"task_id": task_id})
    deadline = time.monotonic() + int(timeout_seconds)
    last: Dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = await _invoke_capability(
            capability_id="task.get_result",
            payload={"task_id": task_id, "capability_id": capability_id},
            token=token,
            installation_id=installation_id,
            timeout=360.0,
        )
        saved = _saved_assets_for_kind(_collect_saved_assets(last), kind)
        urls = _collect_urls(last, want=kind)
        status = _extract_status(last)
        if saved or urls:
            return {
                "task_id": task_id,
                "submit_result": submit,
                "final_result": last,
                "saved_assets": saved,
                "media_urls": urls,
                "status": status or "completed",
            }
        if status in TERMINAL_SUCCESS:
            detail = _failure_message(last) or _json_preview(last, 1200)
            raise RuntimeError(f"{capability_id} task completed but returned no {kind} media: {detail}")
        if status in TERMINAL_FAILURE:
            raise RuntimeError(_failure_message(last) or f"{capability_id} task failed: {status}")
        await asyncio.sleep(max(5, int(interval_seconds)))
    raise RuntimeError(f"{capability_id} task timed out after {timeout_seconds}s: {task_id}")


def _first_asset_id(result: Dict[str, Any], media_type: str = "") -> str:
    for item in result.get("saved_assets") or []:
        if not isinstance(item, dict):
            continue
        if media_type and item.get("media_type") and item.get("media_type") != media_type:
            continue
        aid = _safe_str(item.get("asset_id") or item.get("id"), 64)
        if aid:
            return aid
    return ""


async def run_goal_video_pipeline(
    *,
    pl: GoalVideoPipelinePayload,
    token: str,
    installation_id: str,
    progress: Optional[Callable[[str, str, Optional[Dict[str, Any]]], None]] = None,
    billing_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not (pl.goal or "").strip():
        raise HTTPException(status_code=400, detail="goal is required")

    def emit(stage: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        if progress:
            progress(stage, message, extra)

    plan = dict(pl.precomputed_plan or {}) if isinstance(pl.precomputed_plan, dict) else {}
    if not plan:
        plan = await _retry_async(
            "plan",
            2,
            lambda: _call_planning_llm(pl=pl, token=token, installation_id=installation_id),
            emit,
        )
    emit("plan_done", "plan generated", {"title": plan.get("title"), "partial_plan": plan})
    plan["image_prompt"] = _with_video_no_text_constraint(plan.get("image_prompt"), 2500)

    image_payload: Dict[str, Any] = {
        "prompt": plan["image_prompt"],
        "aspect_ratio": pl.aspect_ratio,
    }
    if pl.image_model:
        image_payload["model"] = pl.image_model
    if pl.reference_image_urls:
        image_payload["image_url"] = pl.reference_image_urls[0]
        image_payload["image_urls"] = pl.reference_image_urls
    if pl.reference_asset_ids:
        image_payload["asset_id"] = pl.reference_asset_ids[0]
        image_payload["asset_ids"] = pl.reference_asset_ids

    image_result = await _retry_async(
        "image",
        pl.image_retry_count + 1,
        lambda: _submit_and_wait_generation(
            kind="image",
            submit_payload=image_payload,
            token=token,
            installation_id=installation_id,
            timeout_seconds=pl.image_poll_timeout_seconds,
            interval_seconds=pl.poll_interval_seconds,
            progress=emit,
            pipeline_context=billing_context,
        ),
        emit,
    )
    image_asset_id = _first_asset_id(image_result, "image")
    image_urls = _collect_urls(image_result, want="image")
    emit(
        "image_done",
        "image generated",
        {
            "image_asset_id": image_asset_id,
            "image_url": image_urls[0] if image_urls else "",
            "partial_image": image_result,
            "partial_plan": plan,
        },
    )

    video_payload: Dict[str, Any] = {
        "prompt": plan["video_prompt"],
        "aspect_ratio": pl.aspect_ratio,
    }
    if pl.duration:
        video_payload["duration"] = int(pl.duration)
    if pl.video_model:
        video_payload["model"] = pl.video_model
    if image_asset_id:
        video_payload["asset_id"] = image_asset_id
    elif image_urls:
        video_payload["image_url"] = image_urls[0]
    else:
        raise RuntimeError("image generation finished but no image asset/url was available for video generation")

    video_result = await _retry_async(
        "video",
        pl.video_retry_count + 1,
        lambda: _submit_and_wait_generation(
            kind="video",
            submit_payload=video_payload,
            token=token,
            installation_id=installation_id,
            timeout_seconds=pl.video_poll_timeout_seconds,
            interval_seconds=pl.poll_interval_seconds,
            progress=emit,
            pipeline_context=billing_context,
        ),
        emit,
    )
    video_asset_id = _first_asset_id(video_result, "video") or _first_asset_id(video_result)
    video_urls = _collect_urls(video_result, want="video")

    saved_assets = []
    saved_assets.extend(image_result.get("saved_assets") or [])
    saved_assets.extend(video_result.get("saved_assets") or [])
    return {
        "ok": True,
        "pipeline": "goal_video_pipeline",
        "status": "completed",
        "plan": plan,
        "image": image_result,
        "video": video_result,
        "saved_assets": _collect_saved_assets({"saved_assets": saved_assets}),
        "image_asset_id": image_asset_id,
        "video_asset_id": video_asset_id,
        "final_asset_id": video_asset_id,
        "media_urls": {
            "image": image_urls,
            "video": video_urls,
        },
        "message": "已按目标生成文案、图片和视频；请以 final_asset_id/video_asset_id 为准，不要编造素材 ID。",
    }


async def run_goal_image_pipeline(
    *,
    pl: GoalVideoPipelinePayload,
    token: str,
    installation_id: str,
    progress: Optional[Callable[[str, str, Optional[Dict[str, Any]]], None]] = None,
    billing_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """创意成片的前半段：根据目标/记忆规划文案和图片提示词，只生成图片后结束。"""
    if not (pl.goal or "").strip():
        raise HTTPException(status_code=400, detail="goal is required")

    def emit(stage: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        if progress:
            progress(stage, message, extra)

    plan = dict(pl.precomputed_plan or {}) if isinstance(pl.precomputed_plan, dict) else {}
    if not plan:
        plan = await _retry_async(
            "plan",
            2,
            lambda: _call_planning_llm(pl=pl, token=token, installation_id=installation_id),
            emit,
        )
    emit("plan_done", "plan generated", {"title": plan.get("title"), "partial_plan": plan})

    image_payload: Dict[str, Any] = {
        "prompt": plan["image_prompt"],
        "aspect_ratio": pl.aspect_ratio,
    }
    if pl.image_model:
        image_payload["model"] = pl.image_model
    if pl.reference_image_urls:
        image_payload["image_url"] = pl.reference_image_urls[0]
        image_payload["image_urls"] = pl.reference_image_urls
    if pl.reference_asset_ids:
        image_payload["asset_id"] = pl.reference_asset_ids[0]
        image_payload["asset_ids"] = pl.reference_asset_ids

    image_result = await _retry_async(
        "image",
        pl.image_retry_count + 1,
        lambda: _submit_and_wait_generation(
            kind="image",
            submit_payload=image_payload,
            token=token,
            installation_id=installation_id,
            timeout_seconds=pl.image_poll_timeout_seconds,
            interval_seconds=pl.poll_interval_seconds,
            progress=emit,
            pipeline_context=billing_context,
        ),
        emit,
    )
    image_asset_id = _first_asset_id(image_result, "image") or _first_asset_id(image_result)
    image_urls = _collect_urls(image_result, want="image")
    return {
        "ok": True,
        "pipeline": "goal_image_pipeline",
        "status": "completed",
        "plan": plan,
        "image": image_result,
        "saved_assets": _collect_saved_assets({"saved_assets": image_result.get("saved_assets") or []}),
        "image_asset_id": image_asset_id,
        "final_asset_id": image_asset_id,
        "media_urls": {
            "image": image_urls,
        },
        "skill_prompt": plan.get("image_prompt") or "",
        "message": "已按目标生成文案和创意图片；请以 final_asset_id/image_asset_id 为准，不要编造素材 ID。",
    }


async def run_goal_video_from_reference_pipeline(
    *,
    pl: GoalVideoPipelinePayload,
    token: str,
    installation_id: str,
    reference_asset_id: str = "",
    reference_image_url: str = "",
    progress: Optional[Callable[[str, str, Optional[Dict[str, Any]]], None]] = None,
    billing_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """根据目标/记忆规划文案和视频提示词，跳过图片生成，直接用指定参考图生成视频。"""
    if not (pl.goal or "").strip():
        raise HTTPException(status_code=400, detail="goal is required")
    reference_asset_id = (reference_asset_id or "").strip()
    reference_image_url = (reference_image_url or "").strip()
    if not reference_asset_id and not reference_image_url:
        raise RuntimeError("reference image is required")

    def emit(stage: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        if progress:
            progress(stage, message, extra)

    plan = dict(pl.precomputed_plan or {}) if isinstance(pl.precomputed_plan, dict) else {}
    if not plan:
        plan = await _retry_async(
            "plan",
            2,
            lambda: _call_planning_llm(pl=pl, token=token, installation_id=installation_id),
            emit,
        )
    emit("plan_done", "plan generated", {"title": plan.get("title"), "partial_plan": plan})

    video_payload: Dict[str, Any] = {
        "prompt": plan["video_prompt"],
        "aspect_ratio": pl.aspect_ratio,
    }
    if pl.duration:
        video_payload["duration"] = int(pl.duration)
    if pl.video_model:
        video_payload["model"] = pl.video_model
    if reference_asset_id:
        video_payload["asset_id"] = reference_asset_id
    if reference_image_url:
        video_payload["image_url"] = reference_image_url

    video_result = await _retry_async(
        "video",
        pl.video_retry_count + 1,
        lambda: _submit_and_wait_generation(
            kind="video",
            submit_payload=video_payload,
            token=token,
            installation_id=installation_id,
            timeout_seconds=pl.video_poll_timeout_seconds,
            interval_seconds=pl.poll_interval_seconds,
            progress=emit,
            pipeline_context=billing_context,
        ),
        emit,
    )
    video_asset_id = _first_asset_id(video_result, "video") or _first_asset_id(video_result)
    video_urls = _collect_urls(video_result, want="video")
    return {
        "ok": True,
        "pipeline": "goal_video_reference_pipeline",
        "status": "completed",
        "plan": plan,
        "reference_asset_id": reference_asset_id,
        "reference_image_url": reference_image_url,
        "video": video_result,
        "saved_assets": _collect_saved_assets({"saved_assets": video_result.get("saved_assets") or []}),
        "video_asset_id": video_asset_id,
        "final_asset_id": video_asset_id,
        "media_urls": {
            "video": video_urls,
        },
        "skill_prompt": plan.get("video_prompt") or "",
        "message": "已按目标生成文案，并用备选图片生成视频；请以 final_asset_id/video_asset_id 为准，不要编造素材 ID。",
    }


def _goal_pipeline_billing_payload(pl: GoalVideoPipelinePayload, *, source_mode: str = "ai_image") -> Dict[str, Any]:
    payload = pl.model_dump()
    payload["source_mode"] = source_mode
    if source_mode != "ai_image":
        payload["reference_asset_ids"] = list(pl.reference_asset_ids or [])
        payload["reference_image_urls"] = list(pl.reference_image_urls or [])
    return payload


def _goal_video_partial_from_image(
    *,
    pl: GoalVideoPipelinePayload,
    plan: Optional[Dict[str, Any]],
    image_result: Optional[Dict[str, Any]],
    error_message: str,
) -> Dict[str, Any]:
    image_result = image_result or {}
    image_asset_id = _first_asset_id(image_result, "image") or _first_asset_id(image_result)
    image_urls = _collect_urls(image_result, want="image")
    if not image_asset_id and pl.reference_asset_ids:
        image_asset_id = str(pl.reference_asset_ids[0] or "").strip()
    if not image_urls and pl.reference_image_urls:
        image_urls = [str(x).strip() for x in pl.reference_image_urls if str(x).strip()]
    return {
        "ok": False,
        "pipeline": "goal_video_pipeline",
        "status": "partial_image",
        "resume_available": bool(image_asset_id or image_urls),
        "error": error_message[:2000],
        "plan": plan or {},
        "image": image_result,
        "saved_assets": _collect_saved_assets({"saved_assets": image_result.get("saved_assets") or []}),
        "image_asset_id": image_asset_id,
        "final_asset_id": image_asset_id,
        "media_urls": {"image": image_urls, "video": []},
        "resume_payload": {
            "capability_id": "goal.video.pipeline",
            "source_mode": "reference_image",
            "goal": pl.goal,
            "platform": pl.platform,
            "duration": pl.duration,
            "aspect_ratio": pl.aspect_ratio,
            "language": pl.language,
            "memory_scope": "none",
            "memory_doc_ids": list(pl.memory_doc_ids or []),
            "planning_model": pl.planning_model,
            "video_model": pl.video_model,
            "precomputed_plan": plan or {},
            "reference_asset_ids": [image_asset_id] if image_asset_id else [],
            "reference_image_urls": image_urls[:1],
        },
    }


async def run_goal_video_pipeline_with_total_billing(
    *,
    pl: GoalVideoPipelinePayload,
    token: str,
    installation_id: str,
    source_mode: str = "ai_image",
    progress: Optional[Callable[[str, str, Optional[Dict[str, Any]]], None]] = None,
) -> Dict[str, Any]:
    pre_payload = _goal_pipeline_billing_payload(pl, source_mode=source_mode)
    pre = await _pre_deduct_pipeline_total(
        capability_id="goal.video.pipeline",
        payload=pre_payload,
        token=token,
        installation_id=installation_id,
    )
    credits = pre.get("credits_charged") if isinstance(pre, dict) else 0
    billing_context = {
        "precharged": True,
        "pipeline_id": uuid.uuid4().hex,
        "capability_id": "goal.video.pipeline",
    }
    captured: Dict[str, Any] = {"plan": None, "image_result": None}

    def wrapped_progress(stage: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        if isinstance(extra, dict):
            if stage == "plan_done":
                captured["plan"] = {"title": extra.get("title")}
            if extra.get("partial_plan") and isinstance(extra.get("partial_plan"), dict):
                captured["plan"] = extra.get("partial_plan")
            if extra.get("partial_image") and isinstance(extra.get("partial_image"), dict):
                captured["image_result"] = extra.get("partial_image")
        if progress:
            progress(stage, message, extra)

    try:
        if source_mode != "ai_image":
            ref_asset = (pl.reference_asset_ids or [""])[0] if pl.reference_asset_ids else ""
            ref_url = (pl.reference_image_urls or [""])[0] if pl.reference_image_urls else ""
            result = await run_goal_video_from_reference_pipeline(
                pl=pl,
                token=token,
                installation_id=installation_id,
                reference_asset_id=ref_asset,
                reference_image_url=ref_url,
                progress=wrapped_progress,
                billing_context=billing_context,
            )
        else:
            result = await run_goal_video_pipeline(
                pl=pl,
                token=token,
                installation_id=installation_id,
                progress=wrapped_progress,
                billing_context=billing_context,
            )
    except Exception as exc:
        await _refund_pipeline_total(
            capability_id="goal.video.pipeline",
            credits=credits,
            token=token,
            installation_id=installation_id,
        )
        await _record_pipeline_total(
            capability_id="goal.video.pipeline",
            payload=pre_payload,
            result=None,
            token=token,
            installation_id=installation_id,
            credits_charged=0,
            success=False,
            error_message=str(exc),
        )
        partial = {}
        if captured.get("image_result") or pl.reference_asset_ids or pl.reference_image_urls:
            partial = _goal_video_partial_from_image(
                pl=pl,
                plan=captured.get("plan") if isinstance(captured.get("plan"), dict) else None,
                image_result=captured.get("image_result") if isinstance(captured.get("image_result"), dict) else None,
                error_message=str(exc),
            )
            partial["pipeline_billing"] = {
                "pre_deduct_applied": bool(credits),
                "credits_charged": credits or 0,
                "refunded": True,
            }
            raise PipelinePartialResultError(str(exc), partial) from exc
        raise
    result["pipeline_billing"] = {
        "pre_deduct_applied": bool(credits),
        "credits_charged": credits or 0,
        "billing_rule": pre.get("billing_rule") if isinstance(pre, dict) else "",
        "breakdown": pre.get("breakdown") if isinstance(pre, dict) else {},
    }
    await _record_pipeline_total(
        capability_id="goal.video.pipeline",
        payload=pre_payload,
        result=result,
        token=token,
        installation_id=installation_id,
        credits_charged=credits,
        success=True,
    )
    return result


async def _background_runner(job_id: str, pl: GoalVideoPipelinePayload, token: str, installation_id: str) -> None:
    def progress(stage: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        append_goal_video_progress(job_id, stage=stage, message=message, extra=extra)

    try:
        result = await run_goal_video_pipeline_with_total_billing(
            pl=pl,
            token=token,
            installation_id=installation_id,
            progress=progress,
        )
    except Exception as e:
        logger.exception("[goal.video.pipeline] background job failed job_id=%s", job_id)
        update_goal_video_job(job_id, status="failed", stage="failed", error=str(e)[:2000])
        return
    update_goal_video_job(job_id, status="completed", stage="completed", error=None, result=result)


@router.post("/api/goal-video/pipeline/run")
async def goal_video_pipeline_run(
    body: GoalVideoPipelineBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    pl = body.payload
    token = _raw_token_from_request(request)
    installation_id = _installation_id_from_request(request, current_user.id)
    try:
        return await run_goal_video_pipeline_with_total_billing(
            pl=pl,
            token=token,
            installation_id=installation_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:2000]) from e


@router.post("/api/goal-video/pipeline/start")
async def goal_video_pipeline_start(
    body: GoalVideoPipelineBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    pl = body.payload
    if not (pl.goal or "").strip():
        raise HTTPException(status_code=400, detail="goal is required")
    token = _raw_token_from_request(request)
    installation_id = _installation_id_from_request(request, current_user.id)
    job_id = create_goal_video_job(user_id=current_user.id, payload=pl.model_dump())

    def _log_task_done(task: asyncio.Task) -> None:
        try:
            _ = task.exception()
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_background_runner(job_id, pl, token, installation_id))
    task.add_done_callback(_log_task_done)
    return {"ok": True, "async": True, "job_id": job_id, "poll_path": f"/api/goal-video/pipeline/jobs/{job_id}"}


@router.get("/api/goal-video/pipeline/jobs/{job_id}")
async def goal_video_pipeline_job_status(
    job_id: str,
    compact: bool = False,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    job = get_goal_video_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if int(job.get("user_id") or -1) != int(current_user.id):
        raise HTTPException(status_code=403, detail="无权查看该任务")
    out: Dict[str, Any] = {
        "ok": True,
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "stage": job.get("stage"),
        "created_at_ts": job.get("created_at_ts"),
        "updated_at_ts": job.get("updated_at_ts"),
        "progress": job.get("progress") or [],
    }
    if job.get("status") == "failed":
        out["error"] = job.get("error")
    if job.get("status") == "completed" and not compact:
        out["result"] = job.get("result")
    return out
