from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

_ROOT_DIR = Path(__file__).resolve().parents[3]
_ENV_PATH = _ROOT_DIR / ".env"
_SHANJIAN_API_BASE = "https://openapi.shanjian.tv"


class TokenBody(BaseModel):
    token: Optional[str] = None


class TemplateListBody(TokenBody):
    page_size: int = 24
    sid: str = ""
    scene: str = "virtualman"
    search_key: str = ""
    search_value: str = ""
    sort_by: str = "desc"


class TemplateDetailBody(TokenBody):
    template_id: str = Field(..., min_length=1)


class CommonAssetsBody(TokenBody):
    page_size: int = 24
    sid: str = ""


class SubmitClipBody(TokenBody):
    title: str = "智能剪辑"
    scene: str = "virtualman"
    style_id: str = Field(..., min_length=1)
    virtualman_id: Optional[str] = None
    video_url: Optional[str] = None
    speaker_id: Optional[str] = None
    content: Optional[str] = None
    audio_url: Optional[str] = None
    language: str = "zh-CN"
    speed_ratio: float = 1.0
    materials: List[Dict[str, Any]] = Field(default_factory=list)
    material_sound_switch: bool = False
    introduce_name: str = ""
    introduce_description: str = ""
    header_switch: bool = True
    material_switch: bool = True
    subtitle_switch: bool = True
    keyword_switch: bool = True
    watermark_show: bool = True
    material_match_way: str = "fuzzyMatch"
    resource_preprocess_method: str = "roughCut"
    material_composition: str = "random"
    video_duration: int = 30


class TaskBody(TokenBody):
    task_id: str = Field(..., min_length=1)


def _read_env_value(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    try:
        if not _ENV_PATH.exists():
            return ""
        pattern = re.compile(r"^\s*" + re.escape(name) + r"\s*=\s*(.*)\s*$")
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            match = pattern.match(line)
            if not match:
                continue
            raw = match.group(1).strip()
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
                raw = raw[1:-1]
            return raw.strip()
    except Exception:
        return ""
    return ""


def _resolved_token(token: Optional[str]) -> str:
    value = (token or "").strip() or _read_env_value("SHANJIAN_OPENAPI_TOKEN")
    if value:
        return value
    raise HTTPException(status_code=400, detail="请先在 .env 配置 SHANJIAN_OPENAPI_TOKEN 后再使用智能剪辑")


def _headers(token: Optional[str]) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_resolved_token(token)}",
        "Accept": "application/json",
    }


def _url(path: str) -> str:
    return f"{_SHANJIAN_API_BASE}{path if path.startswith('/') else '/' + path}"


def _raise_business_error(payload: Dict[str, Any]) -> None:
    code = str(payload.get("code") or "").strip()
    if code in {"", "Succeed"}:
        return
    message = str(payload.get("message") or payload.get("msg") or "山涧接口返回业务错误")
    raise HTTPException(status_code=502, detail=f"山涧错误 {code}: {message}")


async def _get(path: str, token: Optional[str], params: Dict[str, Any]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(_url(path), headers=_headers(token), params=params)
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="山涧 OpenAPI Token 无效或已过期")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"山涧 HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    try:
        payload = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="山涧返回格式不是 JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="山涧返回格式无效")
    _raise_business_error(payload)
    return payload


async def _post(path: str, token: Optional[str], body: Dict[str, Any]) -> Dict[str, Any]:
    headers = _headers(token)
    headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(_url(path), headers=headers, json=body)
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="山涧 OpenAPI Token 无效或已过期")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"山涧 HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    try:
        payload = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="山涧返回格式不是 JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="山涧返回格式无效")
    _raise_business_error(payload)
    return payload


def _data(payload: Dict[str, Any]) -> Dict[str, Any]:
    value = payload.get("data")
    return value if isinstance(value, dict) else {}


@router.post("/api/shanjian-smart-clip/templates")
async def list_templates(body: TemplateListBody):
    params: Dict[str, Any] = {
        "pageSize": max(1, min(int(body.page_size or 24), 60)),
        "scene": body.scene or "virtualman",
        "sortBy": body.sort_by or "desc",
    }
    if body.sid.strip():
        params["sid"] = body.sid.strip()
    if body.search_key.strip() and body.search_value.strip():
        params["searchKey"] = body.search_key.strip()
        params["searchValue"] = body.search_value.strip()
    payload = await _get("/v1/clip/template", body.token, params)
    data = _data(payload)
    return {"ok": True, "results": data.get("results") or [], "sid": data.get("sid") or "", "raw": payload}


@router.post("/api/shanjian-smart-clip/template-detail")
async def template_detail(body: TemplateDetailBody):
    payload = await _get(f"/v1/clip/template/detail/{body.template_id.strip()}", body.token, {})
    return {"ok": True, "item": _data(payload), "raw": payload}


@router.post("/api/shanjian-smart-clip/virtualmans")
async def common_virtualmans(body: CommonAssetsBody):
    params: Dict[str, Any] = {"pageSize": max(1, min(int(body.page_size or 24), 60))}
    if body.sid.strip():
        params["sid"] = body.sid.strip()
    payload = await _get("/v1/assets/virtualman/common", body.token, params)
    data = _data(payload)
    return {"ok": True, "results": data.get("results") or [], "sid": data.get("sid") or "", "raw": payload}


@router.post("/api/shanjian-smart-clip/voices")
async def common_voices(body: TokenBody):
    payload = await _get("/v1/assets/voice/common", body.token, {})
    data = _data(payload)
    return {"ok": True, "results": data.get("results") or [], "raw": payload}


@router.post("/api/shanjian-smart-clip/submit")
async def submit_clip(body: SubmitClipBody):
    scene = (body.scene or "virtualman").strip()
    content = (body.content or "").strip()
    audio_url = (body.audio_url or "").strip()
    speaker_id = (body.speaker_id or "").strip()
    video_url = (body.video_url or "").strip()
    if scene not in {"virtualman", "realMan", "oralMixCutting", "newsMixCutting"}:
        raise HTTPException(status_code=400, detail="不支持的智能剪辑类型")
    if scene == "virtualman" and not (body.virtualman_id or "").strip():
        raise HTTPException(status_code=400, detail="请先选择数字人")
    if scene == "realMan" and not video_url:
        raise HTTPException(status_code=400, detail="真人口播需要填写真人视频 URL")
    if scene in {"virtualman", "oralMixCutting"} and not audio_url and (not content or not speaker_id):
        raise HTTPException(status_code=400, detail="文本生成需要填写文案并选择声音；音频生成需要填写音频 URL")

    payload: Dict[str, Any] = {
        "styleId": body.style_id.strip(),
        "title": str(body.title or "智能剪辑").strip()[:80],
        "materialSoundSwitch": bool(body.material_sound_switch),
        "packRules": {
            "headerSwitch": bool(body.header_switch),
            "materialSwitch": bool(body.material_switch),
            "subtitleSwitch": bool(body.subtitle_switch),
            "keywordSwitch": bool(body.keyword_switch),
        },
        "processRules": {
            "watermarkShow": bool(body.watermark_show),
            "materialMatchWay": body.material_match_way if body.material_match_way in {"fuzzyMatch", "preciseMatch"} else "fuzzyMatch",
        },
    }
    endpoint = "/v1/clip/video/virtualman_broadcast"
    if scene == "virtualman":
        payload["virtualmanId"] = (body.virtualman_id or "").strip()
    elif scene == "realMan":
        endpoint = "/v1/clip/video/realman_broadcast"
        payload["videoUrl"] = video_url
        payload["language"] = body.language or "zh-CN"
        payload["processRules"]["resourcePreprocessMethod"] = (
            body.resource_preprocess_method
            if body.resource_preprocess_method in {"roughCut", "sliceMerge"}
            else "roughCut"
        )
    elif scene == "oralMixCutting":
        endpoint = "/v1/clip/video/broadcast_mixcut"
    elif scene == "newsMixCutting":
        endpoint = "/v1/clip/video/news_mixcut"
        payload["processRules"]["materialComposition"] = (
            body.material_composition if body.material_composition in {"random", "sequential"} else "random"
        )
        payload["processRules"]["videoDuration"] = max(5, min(int(body.video_duration or 30), 300))

    if scene in {"virtualman", "oralMixCutting"}:
        if audio_url:
            payload["audioUrl"] = audio_url
            payload["language"] = body.language or "zh-CN"
        else:
            payload["content"] = content
            payload["speakerId"] = speaker_id
            payload["speakerExtra"] = {
                "speedRatio": max(0.5, min(float(body.speed_ratio or 1), 2.0)),
                "language": body.language or "zh-CN",
            }
    if body.materials:
        cleaned = []
        for item in body.materials[:20]:
            if not isinstance(item, dict):
                continue
            file_url = str(item.get("fileUrl") or item.get("file_url") or "").strip()
            kind = str(item.get("type") or "").strip()
            if file_url and kind in {"image", "video"}:
                cleaned.append({"type": kind, "fileUrl": file_url})
        if cleaned:
            payload["materials"] = cleaned
    if body.introduce_name.strip() or body.introduce_description.strip():
        payload["introduceCard"] = {
            "name": body.introduce_name.strip(),
            "description": body.introduce_description.strip(),
        }

    upstream = await _post(endpoint, body.token, payload)
    data = _data(upstream)
    task_id = str(data.get("taskId") or "").strip()
    if not task_id:
        raise HTTPException(status_code=502, detail="山涧未返回 taskId")
    return {
        "ok": True,
        "task_id": task_id,
        "request_id": upstream.get("requestId") or "",
        "estimated_cost": {"unit": "credits", "scene": scene},
        "raw": upstream,
    }


@router.post("/api/shanjian-smart-clip/task")
async def task_info(body: TaskBody):
    payload = await _get("/v1/task/info", body.token, {"taskId": body.task_id.strip()})
    data = _data(payload)
    status = str(data.get("status") or "").strip()
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    return {
        "ok": status != "failed",
        "task_id": data.get("taskId") or body.task_id.strip(),
        "status": status or "processing",
        "status_text": {"processing": "处理中", "succeed": "已完成", "failed": "失败"}.get(status, status or "处理中"),
        "video_url": result.get("videoUrl") or "",
        "cover_url": result.get("coverUrl") or "",
        "duration": result.get("duration"),
        "cost_rights": data.get("costRights") or {},
        "error_code": data.get("errorCode") or "",
        "message": data.get("errorMessage") or payload.get("message") or "",
        "raw": payload,
    }
