"""爆款TVC 技能内的分步 Veo 能力：供本机 MCP invoke_capability(comfly.veo) 调用。"""
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import UserComflyConfig
from ..services.comfly_veo_exec import run_comfly_veo
from .auth import _ServerUser, get_current_user_for_local, get_current_user_media_edit

logger = logging.getLogger(__name__)
router = APIRouter()


def _mask_secret(s: str) -> str:
    t = (s or "").strip()
    if not t:
        return ""
    if len(t) <= 10:
        return "••••"
    return t[:4] + "…" + t[-4:]


class ComflyUserConfigBody(BaseModel):
    """api_key / api_base 传 null 或不传表示不修改；传空字符串表示清除该项（清除后爆款TVC 不可用，直至重新填写）。"""

    api_key: Optional[str] = None
    api_base: Optional[str] = None


@router.get("/api/comfly/config", summary="爆款TVC：Comfly 凭据状态（技能卡片）")
def get_comfly_user_config(
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    row = db.query(UserComflyConfig).filter(UserComflyConfig.user_id == current_user.id).first()
    uk = (row.api_key or "").strip() if row else ""
    ub = (row.api_base or "").strip().rstrip("/") if row else ""
    hint = "https://ai.comfly.chat/v1"
    effective = bool(uk and ub)
    return {
        "has_user_key": bool(uk),
        "masked_user_key": _mask_secret(uk) if uk else "",
        "user_api_base": ub,
        "default_api_base_hint": hint,
        "effective_ready": effective,
    }


@router.post("/api/comfly/config", summary="保存爆款TVC 所需 Comfly 凭据")
def post_comfly_user_config(
    body: ComflyUserConfigBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    row = db.query(UserComflyConfig).filter(UserComflyConfig.user_id == current_user.id).first()
    if row is None:
        row = UserComflyConfig(user_id=current_user.id)
        db.add(row)
    if body.api_key is not None:
        v = body.api_key.strip()
        row.api_key = v if v else None
    if body.api_base is not None:
        v = body.api_base.strip()
        row.api_base = v.rstrip("/") if v else None
    db.commit()
    return {"ok": True, "message": "爆款TVC · Comfly 凭据已保存"}


class ComflyVeoRunBody(BaseModel):
    payload: Dict[str, Any]


@router.post("/api/comfly-veo/run")
async def comfly_veo_run(
    body: ComflyVeoRunBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
    db: Session = Depends(get_db),
):
    try:
        out = await run_comfly_veo(body.payload or {}, current_user.id, request, db)
        logger.info("[comfly_veo] ok user_id=%s action=%s", current_user.id, (body.payload or {}).get("action"))
        return out
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[comfly_veo] failed user_id=%s err=%s", current_user.id, e)
        raise HTTPException(status_code=500, detail=str(e)[:2000]) from e
