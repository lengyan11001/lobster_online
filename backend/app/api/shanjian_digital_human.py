from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ShanjianDigitalHumanProfile, ShanjianDigitalHumanVideoTask
from .assets import get_asset_public_url
from .auth import _ServerUser, get_current_user_for_local
from .shanjian_smart_clip import _data, _get, _post

router = APIRouter()


class _TokenBody(BaseModel):
    token: Optional[str] = None


class ProfileTrainBody(_TokenBody):
    title: str = "未命名数字人"
    mode: str = "image"
    image_url: Optional[str] = None
    image_asset_id: Optional[str] = None
    video_url: Optional[str] = None
    video_asset_id: Optional[str] = None
    auth_video_url: Optional[str] = None
    auth_video_asset_id: Optional[str] = None
    auth_text: str = Field(..., min_length=2, max_length=500)
    callback_url: str = ""
    make_default: bool = True


class ProfileTaskBody(_TokenBody):
    task_id: Optional[str] = None
    profile_id: Optional[int] = None


class SetDefaultBody(BaseModel):
    profile_id: int = Field(..., gt=0)


class CreateVideoBody(_TokenBody):
    profile_id: Optional[int] = None
    virtualman_id: Optional[str] = None
    title: str = "数字人口播"
    text: Optional[str] = None
    speaker_id: Optional[str] = None
    audio_url: Optional[str] = None
    audio_asset_id: Optional[str] = None
    language: str = "zh-CN"
    speed_ratio: float = 1.0
    callback_url: str = ""


class VideoTaskBody(_TokenBody):
    task_id: Optional[str] = None
    record_id: Optional[int] = None


def _clean_text(value: Optional[str]) -> str:
    return str(value or "").strip()


def _normalize_mode(value: str) -> str:
    raw = _clean_text(value).lower()
    aliases = {
        "image": "image",
        "image_train": "image",
        "photo": "image",
        "video": "video",
        "pro": "video",
        "professional": "video",
        "fast_video": "fast_video",
        "fast": "fast_video",
    }
    mode = aliases.get(raw)
    if not mode:
        raise HTTPException(status_code=400, detail="mode 仅支持 image / video / fast_video")
    return mode


def _pick_result_value(result: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in result and result.get(key) not in (None, ""):
            return result.get(key)
    return None


def _task_status_text(status: str) -> str:
    mapping = {
        "processing": "处理中",
        "succeed": "已完成",
        "failed": "失败",
    }
    return mapping.get(_clean_text(status), _clean_text(status) or "处理中")


def _resolve_asset_or_url(
    *,
    request: Request,
    db: Session,
    current_user: _ServerUser,
    url: Optional[str],
    asset_id: Optional[str],
    label: str,
) -> str:
    raw_url = _clean_text(url)
    if raw_url:
        if raw_url.startswith("http://") or raw_url.startswith("https://"):
            return raw_url
        raise HTTPException(status_code=400, detail=f"{label} URL 必须是 http(s) 地址")
    aid = _clean_text(asset_id)
    if not aid:
        raise HTTPException(status_code=400, detail=f"请提供{label} URL 或 asset_id")
    public_url = get_asset_public_url(aid, int(current_user.id), request, db)
    if not public_url:
        raise HTTPException(status_code=400, detail=f"{label}素材还没有可用公网地址，请先确认素材已上传成功")
    return public_url


def _clear_default_profiles(db: Session, user_id: int) -> None:
    db.query(ShanjianDigitalHumanProfile).filter(
        ShanjianDigitalHumanProfile.user_id == int(user_id),
        ShanjianDigitalHumanProfile.is_default.is_(True),
    ).update({"is_default": False}, synchronize_session=False)


def _profile_to_dict(row: ShanjianDigitalHumanProfile) -> Dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "title": row.title,
        "train_mode": row.train_mode,
        "status": row.status,
        "status_text": _task_status_text(row.status),
        "is_default": bool(row.is_default),
        "task_id": row.task_id or "",
        "request_id": row.request_id or "",
        "virtualman_id": row.virtualman_id or "",
        "source_asset_id": row.source_asset_id or "",
        "source_url": row.source_url or "",
        "auth_video_asset_id": row.auth_video_asset_id or "",
        "auth_video_url": row.auth_video_url or "",
        "auth_text": row.auth_text or "",
        "cover_url": row.cover_url or "",
        "error_message": row.error_message or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def _video_task_to_dict(row: ShanjianDigitalHumanVideoTask) -> Dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "profile_id": row.profile_id,
        "title": row.title,
        "status": row.status,
        "status_text": _task_status_text(row.status),
        "task_id": row.task_id,
        "request_id": row.request_id or "",
        "virtualman_id": row.virtualman_id or "",
        "audio_asset_id": row.audio_asset_id or "",
        "audio_url": row.audio_url or "",
        "speaker_id": row.speaker_id or "",
        "text": row.text or "",
        "video_url": row.video_url or "",
        "cover_url": row.cover_url or "",
        "duration": row.duration,
        "error_message": row.error_message or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def _resolve_profile_for_video(
    db: Session,
    current_user: _ServerUser,
    profile_id: Optional[int],
    virtualman_id: Optional[str],
) -> tuple[Optional[ShanjianDigitalHumanProfile], str]:
    vmid = _clean_text(virtualman_id)
    if profile_id:
        row = db.query(ShanjianDigitalHumanProfile).filter(
            ShanjianDigitalHumanProfile.id == int(profile_id),
            ShanjianDigitalHumanProfile.user_id == int(current_user.id),
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="未找到对应的闪剪数字人档案")
        if row.status != "succeed" or not _clean_text(row.virtualman_id):
            raise HTTPException(status_code=400, detail="该闪剪数字人还未训练完成，暂时不能用于出片")
        return row, _clean_text(row.virtualman_id)
    if vmid:
        row = db.query(ShanjianDigitalHumanProfile).filter(
            ShanjianDigitalHumanProfile.user_id == int(current_user.id),
            ShanjianDigitalHumanProfile.virtualman_id == vmid,
        ).first()
        return row, vmid
    row = db.query(ShanjianDigitalHumanProfile).filter(
        ShanjianDigitalHumanProfile.user_id == int(current_user.id),
        ShanjianDigitalHumanProfile.is_default.is_(True),
        ShanjianDigitalHumanProfile.status == "succeed",
    ).order_by(ShanjianDigitalHumanProfile.updated_at.desc()).first()
    if row and _clean_text(row.virtualman_id):
        return row, _clean_text(row.virtualman_id)
    raise HTTPException(status_code=400, detail="请先创建并训练一个自己的闪剪数字人，或显式传入 virtualman_id")


def _profile_endpoint_and_payload(
    body: ProfileTrainBody,
    *,
    request: Request,
    db: Session,
    current_user: _ServerUser,
) -> tuple[str, Dict[str, Any], str, Optional[str], Optional[str]]:
    mode = _normalize_mode(body.mode)
    source_asset_id = _clean_text(body.image_asset_id if mode == "image" else body.video_asset_id) or None
    auth_asset_id = _clean_text(body.auth_video_asset_id) or None
    auth_video_url = _resolve_asset_or_url(
        request=request,
        db=db,
        current_user=current_user,
        url=body.auth_video_url,
        asset_id=body.auth_video_asset_id,
        label="授权视频",
    )
    payload: Dict[str, Any] = {
        "title": _clean_text(body.title)[:80] or "未命名数字人",
        "authVideoUrl": auth_video_url,
        "authText": _clean_text(body.auth_text),
    }
    if _clean_text(body.callback_url):
        payload["callbackUrl"] = _clean_text(body.callback_url)
    if mode == "image":
        source_url = _resolve_asset_or_url(
            request=request,
            db=db,
            current_user=current_user,
            url=body.image_url,
            asset_id=body.image_asset_id,
            label="训练图片",
        )
        payload["imageUrl"] = source_url
        return "/v1/virtualman/image/train", payload, source_url, source_asset_id, auth_asset_id
    source_url = _resolve_asset_or_url(
        request=request,
        db=db,
        current_user=current_user,
        url=body.video_url,
        asset_id=body.video_asset_id,
        label="训练视频",
    )
    payload["videoUrl"] = source_url
    endpoint = "/v1/virtualman/fast/train" if mode == "fast_video" else "/v1/virtualman/train"
    return endpoint, payload, source_url, source_asset_id, auth_asset_id


@router.get("/api/shanjian-digital-human/profiles")
async def list_profiles(
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    rows = db.query(ShanjianDigitalHumanProfile).filter(
        ShanjianDigitalHumanProfile.user_id == int(current_user.id)
    ).order_by(
        ShanjianDigitalHumanProfile.is_default.desc(),
        ShanjianDigitalHumanProfile.updated_at.desc(),
        ShanjianDigitalHumanProfile.id.desc(),
    ).all()
    return {"ok": True, "items": [_profile_to_dict(row) for row in rows]}


@router.get("/api/shanjian-digital-human/videos")
async def list_video_tasks(
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    rows = db.query(ShanjianDigitalHumanVideoTask).filter(
        ShanjianDigitalHumanVideoTask.user_id == int(current_user.id)
    ).order_by(
        ShanjianDigitalHumanVideoTask.updated_at.desc(),
        ShanjianDigitalHumanVideoTask.id.desc(),
    ).limit(100).all()
    return {"ok": True, "items": [_video_task_to_dict(row) for row in rows]}


@router.post("/api/shanjian-digital-human/profile/train")
async def create_profile(
    body: ProfileTrainBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    endpoint, payload, source_url, source_asset_id, auth_asset_id = _profile_endpoint_and_payload(
        body,
        request=request,
        db=db,
        current_user=current_user,
    )
    upstream = await _post(endpoint, body.token, payload)
    data = _data(upstream)
    task_id = _clean_text(data.get("taskId"))
    if not task_id:
        raise HTTPException(status_code=502, detail="闪剪未返回 taskId")
    if body.make_default:
        _clear_default_profiles(db, int(current_user.id))
    row = ShanjianDigitalHumanProfile(
        user_id=int(current_user.id),
        title=_clean_text(body.title)[:80] or "未命名数字人",
        train_mode=_normalize_mode(body.mode),
        status="processing",
        is_default=bool(body.make_default),
        task_id=task_id,
        request_id=_clean_text(upstream.get("requestId")),
        source_asset_id=source_asset_id,
        source_url=source_url,
        auth_video_asset_id=auth_asset_id,
        auth_video_url=_clean_text(payload.get("authVideoUrl")),
        auth_text=_clean_text(body.auth_text),
        train_payload=payload,
        train_result=upstream,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "ok": True,
        "profile": _profile_to_dict(row),
        "task_id": task_id,
        "request_id": row.request_id or "",
        "raw": upstream,
    }


@router.post("/api/shanjian-digital-human/profile/task")
async def query_profile_task(
    body: ProfileTaskBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    row = None
    if body.profile_id:
        row = db.query(ShanjianDigitalHumanProfile).filter(
            ShanjianDigitalHumanProfile.id == int(body.profile_id),
            ShanjianDigitalHumanProfile.user_id == int(current_user.id),
        ).first()
    elif _clean_text(body.task_id):
        row = db.query(ShanjianDigitalHumanProfile).filter(
            ShanjianDigitalHumanProfile.task_id == _clean_text(body.task_id),
            ShanjianDigitalHumanProfile.user_id == int(current_user.id),
        ).first()
    if not row:
        raise HTTPException(status_code=404, detail="未找到对应的闪剪数字人任务")

    payload = await _get("/v1/task/info", body.token, {"taskId": row.task_id})
    data = _data(payload)
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    status = _clean_text(data.get("status")) or "processing"
    virtualman_id = _clean_text(_pick_result_value(result, "virtualmanId", "virtualManId", "id"))
    cover_url = _clean_text(_pick_result_value(result, "coverUrl", "imageUrl", "posterUrl"))
    error_message = _clean_text(data.get("errorMessage") or payload.get("message"))

    row.status = status
    row.request_id = _clean_text(payload.get("requestId")) or row.request_id
    row.virtualman_id = virtualman_id or row.virtualman_id
    row.cover_url = cover_url or row.cover_url
    row.train_result = payload
    row.error_message = error_message or None
    row.updated_at = datetime.utcnow()

    if status == "succeed" and row.is_default:
        _clear_default_profiles(db, int(current_user.id))
        row.is_default = True

    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "ok": status != "failed",
        "status": status,
        "status_text": _task_status_text(status),
        "virtualman_id": row.virtualman_id or "",
        "profile": _profile_to_dict(row),
        "message": error_message,
        "raw": payload,
    }


@router.post("/api/shanjian-digital-human/profile/default")
async def set_default_profile(
    body: SetDefaultBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    row = db.query(ShanjianDigitalHumanProfile).filter(
        ShanjianDigitalHumanProfile.id == int(body.profile_id),
        ShanjianDigitalHumanProfile.user_id == int(current_user.id),
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="未找到对应的闪剪数字人档案")
    _clear_default_profiles(db, int(current_user.id))
    row.is_default = True
    row.updated_at = datetime.utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "profile": _profile_to_dict(row)}


@router.post("/api/shanjian-digital-human/video/create")
async def create_video(
    body: CreateVideoBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    profile, virtualman_id = _resolve_profile_for_video(db, current_user, body.profile_id, body.virtualman_id)
    text = _clean_text(body.text)
    speaker_id = _clean_text(body.speaker_id)
    audio_url = _clean_text(body.audio_url)
    audio_asset_id = _clean_text(body.audio_asset_id) or None
    if not audio_url and audio_asset_id:
        audio_url = _resolve_asset_or_url(
            request=request,
            db=db,
            current_user=current_user,
            url=None,
            asset_id=audio_asset_id,
            label="驱动音频",
        )
    if not audio_url and (not text or not speaker_id):
        raise HTTPException(status_code=400, detail="请提供 audio_url / audio_asset_id，或同时提供 text + speaker_id")

    payload: Dict[str, Any] = {
        "title": _clean_text(body.title)[:80] or "数字人口播",
        "virtualmanId": virtualman_id,
    }
    if _clean_text(body.callback_url):
        payload["callbackUrl"] = _clean_text(body.callback_url)
    if audio_url:
        payload["audioUrl"] = audio_url
    else:
        payload["text"] = text
        payload["speakerId"] = speaker_id
        payload["speakerExtra"] = {
            "speedRatio": max(0.5, min(float(body.speed_ratio or 1.0), 2.0)),
            "language": _clean_text(body.language) or "zh-CN",
        }

    upstream = await _post("/v1/virtualman/video", body.token, payload)
    data = _data(upstream)
    task_id = _clean_text(data.get("taskId"))
    if not task_id:
        raise HTTPException(status_code=502, detail="闪剪未返回 taskId")
    row = ShanjianDigitalHumanVideoTask(
        user_id=int(current_user.id),
        profile_id=getattr(profile, "id", None),
        title=_clean_text(body.title)[:80] or "数字人口播",
        status="processing",
        task_id=task_id,
        request_id=_clean_text(upstream.get("requestId")),
        virtualman_id=virtualman_id,
        audio_asset_id=audio_asset_id,
        audio_url=audio_url or None,
        speaker_id=speaker_id or None,
        text=text or None,
        submit_payload=payload,
        result_payload=upstream,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "ok": True,
        "task_id": task_id,
        "record": _video_task_to_dict(row),
        "raw": upstream,
    }


@router.post("/api/shanjian-digital-human/video/task")
async def query_video_task(
    body: VideoTaskBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    row = None
    if body.record_id:
        row = db.query(ShanjianDigitalHumanVideoTask).filter(
            ShanjianDigitalHumanVideoTask.id == int(body.record_id),
            ShanjianDigitalHumanVideoTask.user_id == int(current_user.id),
        ).first()
    elif _clean_text(body.task_id):
        row = db.query(ShanjianDigitalHumanVideoTask).filter(
            ShanjianDigitalHumanVideoTask.task_id == _clean_text(body.task_id),
            ShanjianDigitalHumanVideoTask.user_id == int(current_user.id),
        ).first()
    if not row:
        raise HTTPException(status_code=404, detail="未找到对应的闪剪视频任务")

    payload = await _get("/v1/task/info", body.token, {"taskId": row.task_id})
    data = _data(payload)
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    status = _clean_text(data.get("status")) or "processing"
    error_message = _clean_text(data.get("errorMessage") or payload.get("message"))

    row.status = status
    row.request_id = _clean_text(payload.get("requestId")) or row.request_id
    row.video_url = _clean_text(_pick_result_value(result, "videoUrl")) or row.video_url
    row.cover_url = _clean_text(_pick_result_value(result, "coverUrl")) or row.cover_url
    try:
        duration_value = _pick_result_value(result, "duration")
        row.duration = int(duration_value) if duration_value not in (None, "") else row.duration
    except Exception:
        pass
    row.result_payload = payload
    row.error_message = error_message or None
    row.updated_at = datetime.utcnow()

    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "ok": status != "failed",
        "status": status,
        "status_text": _task_status_text(status),
        "video_url": row.video_url or "",
        "cover_url": row.cover_url or "",
        "duration": row.duration,
        "record": _video_task_to_dict(row),
        "message": error_message,
        "raw": payload,
    }
