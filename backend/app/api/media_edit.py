"""本地素材剪辑 API（media.edit），供 MCP upstream=local 调用。"""
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .auth import get_current_user_media_edit, _ServerUser
from ..db import get_db
from ..services.media_edit_exec import run_operation

router = APIRouter()
logger = logging.getLogger(__name__)


class MediaEditRunBody(BaseModel):
    """与 invoke_capability 中 payload 一致：含 operation 与各 op 专用字段。"""
    payload: Dict[str, Any]


@router.post("/api/media-edit/run")
async def media_edit_run(
    body: MediaEditRunBody,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
    db: Session = Depends(get_db),
):
    pl = body.payload if isinstance(body.payload, dict) else {}
    logger.info(
        "[media_edit] request user_id=%s operation=%s asset_id=%s",
        current_user.id,
        pl.get("operation"),
        pl.get("asset_id"),
    )
    try:
        out = run_operation(db, current_user.id, body.payload)
        logger.info(
            "[media_edit] ok user_id=%s output_asset_id=%s",
            current_user.id,
            (out or {}).get("output_asset_id"),
        )
        return out
    except ValueError as e:
        logger.warning("[media_edit] 400 user_id=%s err=%s", current_user.id, e)
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.exception("[media_edit] 500 user_id=%s err=%s", current_user.id, e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        logger.exception("[media_edit] 500 unexpected user_id=%s err=%s", current_user.id, e)
        raise HTTPException(status_code=500, detail=f"media.edit 内部错误: {e}") from e
