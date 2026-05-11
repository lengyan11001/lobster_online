from __future__ import annotations

import asyncio
import json
import logging
import re
import time
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

MCP_URL = "http://127.0.0.1:8001/mcp"
TERMINAL_SUCCESS = {"completed", "complete", "success", "succeeded", "finished", "done"}
TERMINAL_FAILURE = {"failed", "error", "cancelled", "canceled", "timeout", "rejected"}


class GoalVideoPipelinePayload(BaseModel):
    action: str = Field("run_pipeline", description="run_pipeline/start_pipeline/poll_pipeline")
    job_id: Optional[str] = None
    goal: str = Field("", description="用户目标，例如：给某产品生成 8 秒宣传视频")
    platform: str = "douyin"
    language: str = "中文"
    duration: Optional[int] = Field(8, ge=3, le=60)
    aspect_ratio: str = "9:16"
    memory_scope: str = "default"
    planning_model: Optional[str] = None
    image_model: Optional[str] = None
    video_model: Optional[str] = None
    reference_asset_ids: List[str] = Field(default_factory=list)
    reference_image_urls: List[str] = Field(default_factory=list)
    image_retry_count: int = Field(2, ge=0, le=5)
    video_retry_count: int = Field(2, ge=0, le=5)
    poll_interval_seconds: int = Field(12, ge=5, le=60)
    image_poll_timeout_seconds: int = Field(900, ge=60, le=3600)
    video_poll_timeout_seconds: int = Field(2400, ge=120, le=7200)


class GoalVideoPipelineBody(BaseModel):
    payload: GoalVideoPipelinePayload


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
            is_video = low.endswith((".mp4", ".webm", ".mov", ".m4v", ".avi")) or "video" in low
            is_image = low.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")) or "image" in low
            if want == "video" and is_image and not is_video:
                continue
            if want == "image" and is_video:
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


def _mcp_headers(token: str, installation_id: str) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if installation_id:
        headers["X-Installation-Id"] = installation_id
    return headers


def _parse_mcp_text_response(data: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(data.get("result"), dict):
        content = data["result"].get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                text = item.get("text")
                if isinstance(text, str):
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict):
                            return parsed
                    except Exception:
                        return {"text": text}
    return data


async def _invoke_capability(
    *,
    capability_id: str,
    payload: Dict[str, Any],
    token: str,
    installation_id: str,
    timeout: float,
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
        r = await client.post(MCP_URL, json=body, headers=_mcp_headers(token, installation_id))
    try:
        data = r.json() if r.content else {}
    except Exception as e:
        raise RuntimeError(f"MCP returned non-JSON HTTP {r.status_code}: {(r.text or '')[:500]}") from e
    parsed = _parse_mcp_text_response(data)
    if r.status_code >= 400:
        raise RuntimeError(f"MCP HTTP {r.status_code}: {_failure_message(parsed) or str(parsed)[:500]}")
    if data.get("error"):
        raise RuntimeError(_failure_message(data.get("error")) or str(data.get("error"))[:500])
    if isinstance(parsed, dict) and parsed.get("error"):
        raise RuntimeError(_failure_message(parsed) or str(parsed.get("error"))[:500])
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
            if idx < total:
                await asyncio.sleep(min(8.0, 1.5 * idx))
    raise RuntimeError(f"{label} failed after {total} attempts: {last}") from last


async def _call_planning_llm(*, pl: GoalVideoPipelinePayload, token: str, installation_id: str) -> Dict[str, Any]:
    asb = (settings.auth_server_base or "").strip().rstrip("/")
    if not asb or not token:
        raise RuntimeError("AUTH_SERVER_BASE or user token is missing; cannot build goal video plan")

    memory_context = ""
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
        "video_prompt 只描述从该图片生成视频的镜头运动、主体动作、氛围和字幕方向。"
    )
    user = {
        "goal": pl.goal,
        "platform": pl.platform,
        "language": pl.language,
        "duration": pl.duration,
        "aspect_ratio": pl.aspect_ratio,
        "memory_context": memory_context[:12000],
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
    plan["video_prompt"] = _safe_str(plan.get("video_prompt"), 2500)
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
) -> Dict[str, Any]:
    capability_id = "image.generate" if kind == "image" else "video.generate"
    progress(f"{kind}_submit", f"submit {capability_id}", None)
    submit = await _invoke_capability(
        capability_id=capability_id,
        payload=submit_payload,
        token=token,
        installation_id=installation_id,
        timeout=180.0,
    )
    task_ids = _collect_task_ids(submit)
    task_id = task_ids[0] if task_ids else ""
    saved = _collect_saved_assets(submit)
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
        raise RuntimeError(f"{capability_id} did not return task_id/media")

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
        saved = _collect_saved_assets(last)
        urls = _collect_urls(last, want=kind)
        status = _extract_status(last)
        if saved or urls or status in TERMINAL_SUCCESS:
            return {
                "task_id": task_id,
                "submit_result": submit,
                "final_result": last,
                "saved_assets": saved,
                "media_urls": urls,
                "status": status or "completed",
            }
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
) -> Dict[str, Any]:
    if not (pl.goal or "").strip():
        raise HTTPException(status_code=400, detail="goal is required")

    def emit(stage: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        if progress:
            progress(stage, message, extra)

    plan = await _retry_async(
        "plan",
        2,
        lambda: _call_planning_llm(pl=pl, token=token, installation_id=installation_id),
        emit,
    )
    emit("plan_done", "plan generated", {"title": plan.get("title")})

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
        ),
        emit,
    )
    image_asset_id = _first_asset_id(image_result, "image")
    image_urls = _collect_urls(image_result, want="image")

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


async def _background_runner(job_id: str, pl: GoalVideoPipelinePayload, token: str, installation_id: str) -> None:
    def progress(stage: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        append_goal_video_progress(job_id, stage=stage, message=message, extra=extra)

    try:
        result = await run_goal_video_pipeline(pl=pl, token=token, installation_id=installation_id, progress=progress)
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
        return await run_goal_video_pipeline(pl=pl, token=token, installation_id=installation_id)
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
