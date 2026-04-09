"""定时任务附加动作：按配置触发文生视频 / 图生视频（经本机 MCP）。"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

import httpx

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..models import PublishAccount, PublishAccountCreatorSchedule

logger = logging.getLogger(__name__)

# 与 chat 一致：本机 MCP HTTP
_DEFAULT_MCP = "http://127.0.0.1:8001/mcp"


async def run_schedule_video_generate_if_configured(
    db: "Session",
    sch: "PublishAccountCreatorSchedule",
    acct: "PublishAccount",
) -> None:
    """schedule_kind=video 且填写了生产要求时：无素材 ID 走文生视频，有合法素材公网 URL 走图生视频。"""
    kind = (getattr(sch, "schedule_kind", None) or "image").strip().lower()
    if kind != "video":
        return
    prompt = (sch.requirements_text or "").strip()
    if not prompt:
        return

    from ..models import Asset, User

    user = db.query(User).filter(User.id == sch.user_id).first()
    sutui_token = (user.sutui_token or "").strip() if user else ""
    if not sutui_token:
        logger.warning(
            "定时视频已跳过 account_id=%s：用户未配置速推 Token（个人设置中的 sutui_token）",
            sch.account_id,
        )
        return

    aid = (getattr(sch, "video_source_asset_id", None) or "").strip()
    image_url = ""
    if aid:
        asset = (
            db.query(Asset)
            .filter(Asset.user_id == sch.user_id, Asset.asset_id == aid)
            .first()
        )
        if not asset:
            logger.warning("定时图生视频已跳过 account_id=%s：素材 ID 不存在 %s", sch.account_id, aid)
            return
        image_url = (asset.source_url or "").strip()
        if not image_url.startswith("http"):
            logger.warning(
                "定时图生视频已跳过 account_id=%s：素材 %s 无公网 URL（需 TOS 等）",
                sch.account_id,
                aid,
            )
            return

    payload: Dict[str, Any] = {"prompt": prompt, "duration": "5"}
    if image_url:
        payload["model"] = "wan/v2.6/image-to-video"
        payload["image_url"] = image_url
    else:
        payload["model"] = "wan/v2.6/text-to-video"

    mcp_url = _DEFAULT_MCP
    body = {
        "jsonrpc": "2.0",
        "id": "creator-schedule",
        "method": "tools/call",
        "params": {
            "name": "invoke_capability",
            "arguments": {"capability_id": "video.generate", "payload": payload},
        },
    }
    headers = {
        "Content-Type": "application/json",
        "X-Sutui-Token": sutui_token,
    }
    try:
        async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
            r = await client.post(mcp_url, json=body, headers=headers)
        data = r.json() if r.content else {}
        err = data.get("error")
        if err:
            logger.warning(
                "定时视频 MCP 错误 account_id=%s: %s",
                sch.account_id,
                str(err)[:500],
            )
            return
        result = data.get("result") or {}
        content = result.get("content") or []
        text = ""
        if isinstance(content, list) and content:
            text = next(
                (
                    x.get("text", "")
                    for x in content
                    if isinstance(x, dict) and x.get("type") == "text"
                ),
                "",
            )
        logger.info(
            "定时视频已提交 account_id=%s i2v=%s mcp_status=%s 摘要=%s",
            sch.account_id,
            bool(image_url),
            r.status_code,
            (text[:200] + "…") if len(text) > 200 else text or json.dumps(result, ensure_ascii=False)[:200],
        )
    except Exception:
        logger.exception("定时视频 MCP 调用失败 account_id=%s", sch.account_id)
