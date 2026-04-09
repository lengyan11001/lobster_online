"""
定时任务：将「生产要求 / 描述需求」按约定格式交给 POST /chat 完整编排（直连 LLM + MCP）。

到点由 creator_schedule_runner 调用；需本机 API 可访问（默认 127.0.0.1:port）、用户已配置对话模型 Key 或可走 OpenClaw，
且 MCP 可用；视频图生时可在请求体附带 attachment_asset_ids 以注入公网图 URL。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import httpx

from ..api.auth import create_access_token
from ..db import SessionLocal
from ..core.config import settings
from .internal_chat_client import chat_headers_for_user
from .schedule_orchestration_prompt import (
    build_schedule_orchestration_user_message,
    schedule_requirements_imply_publish,
)

if TYPE_CHECKING:
    from ..models import PublishAccount, PublishAccountCreatorSchedule

from ..models import PublishAccountCreatorSchedule

logger = logging.getLogger(__name__)

# 与 chat 内 task.get_result 长轮询一致，留足余量
_CHAT_TIMEOUT_SEC = 40 * 60
# 单次 /chat 内已加长工具轮次；仍可能停在「只写计划」→ 多轮续跑
_SCHEDULE_CHAT_MAX_ATTEMPTS = 3


def _api_base_url() -> str:
    base = (getattr(settings, "public_base_url", None) or "").strip().rstrip("/")
    if base:
        return base
    return f"http://127.0.0.1:{int(getattr(settings, 'port', 8000) or 8000)}"


def _schedule_publish_continuation_message(
    acct: "PublishAccount",
    attempt: int,
    asset_hints: List[str],
) -> str:
    nick = (getattr(acct, "nickname", None) or "").strip()
    plat = (getattr(acct, "platform", None) or "").strip()
    hints = [str(x).strip() for x in (asset_hints or []) if str(x).strip()]
    hint_tail = ""
    if hints:
        tail = "、".join(hints[-5:])
        hint_tail = f" 可优先使用下列素材 ID（来自上文工具结果）：{tail}。"
    return (
        f"【定时编排续跑 · 第{attempt + 1}轮】上一段对话尚未完成发布。"
        "你必须在本轮发起 **tool_calls**：可先 list_publish_accounts，再 **publish_content**；"
        f"account_nickname 必须是「{nick}」；平台：{plat}。"
        f"{hint_tail}"
        " title、description、tags 按定时任务说明撰写；publish_content 返回 JSON 须 status=success。"
        "禁止只回复文字计划、禁止「接下来要…」而不调用工具。"
    )


def _eval_publish_success(
    *,
    publish_required: bool,
    publish_ok: Optional[bool],
    tools: List[Any],
) -> Tuple[bool, Optional[str]]:
    """
    若用户要求发布：仅以 publish_ok is True 为编排成功。
    未要求发布：不因缺 publish 判失败。
    """
    if not publish_required:
        return True, None
    if publish_ok is True:
        return True, None
    if publish_ok is False:
        for t in reversed(tools):
            if not isinstance(t, dict):
                continue
            if t.get("tool_name") != "publish_content":
                continue
            if t.get("success") is False:
                return False, ((t.get("result_preview") or "") or "publish_content 失败")[:800]
        return False, "发布未成功（publish_content 返回失败或 need_login）"
    return False, "未调用 publish_content 或未等到发布成功（须工具返回 status=success）"


async def run_schedule_orchestration_chat(
    sch: "PublishAccountCreatorSchedule",
    acct: "PublishAccount",
) -> Dict[str, Any]:
    """
    requirements_text 非空时：签发短期 JWT，POST /chat，执行生成/发布等工具链。

    - 若 schedule_requirements_imply_publish(requirements) 为 True：必须以 publish_content 业务 success 为编排成功；
      否则自动追加最多 2 轮续跑消息，并启用 schedule_orchestration（加长工具轮次 + 防「只写计划」）。
    - 不要求发布时：HTTP 200 且 JSON 可解析即视为编排完成（与旧行为一致）。
    """
    mode = (getattr(sch, "schedule_publish_mode", None) or "immediate").strip().lower()
    if mode not in ("immediate", "review"):
        mode = "immediate"

    if mode == "review" and not bool(getattr(sch, "review_confirmed", False)):
        return {"skipped": True}

    req_text = (getattr(sch, "requirements_text", None) or "").strip()
    if not req_text:
        return {"skipped": True}

    extra_ctx: Optional[Dict[str, Any]] = None
    if mode == "review":
        drafts = getattr(sch, "review_drafts_json", None) or []
        idx = int(getattr(sch, "review_selected_slot", 0) or 0)
        if not isinstance(drafts, list) or not (0 <= idx < len(drafts)):
            return {
                "skipped": False,
                "ok": False,
                "error": "审核稿缺失或所选序号无效，请重新生成草稿并确认。",
            }
        slot = drafts[idx]
        if not isinstance(slot, dict):
            return {"skipped": False, "ok": False, "error": "审核稿格式无效"}
        extra_ctx = {"审核稿_JSON": json.dumps(slot, ensure_ascii=False)}

    publish_required = schedule_requirements_imply_publish(req_text)

    kind = (getattr(sch, "schedule_kind", None) or "image").strip().lower()
    vaid = (getattr(sch, "video_source_asset_id", None) or "").strip()
    attachment_ids: list[str] = []
    if kind == "video" and vaid:
        attachment_ids = [vaid]

    user_msg = build_schedule_orchestration_user_message(
        account=acct, schedule=sch, extra_context=extra_ctx
    )
    token = create_access_token(
        data={"sub": str(sch.user_id)},
        expires_delta=timedelta(hours=2),
    )
    url = f"{_api_base_url()}/chat"
    headers = chat_headers_for_user(int(sch.user_id), token)

    history_payload: List[Dict[str, str]] = []
    last_hints: List[str] = []
    last_fail_detail: Optional[str] = None

    gen_start: Optional[int] = None
    if mode == "review":
        db_g = SessionLocal()
        try:
            rg = (
                db_g.query(PublishAccountCreatorSchedule)
                .filter(PublishAccountCreatorSchedule.id == sch.id)
                .first()
            )
            if not rg:
                return {"skipped": False, "ok": False, "error": "定时记录不存在"}
            gen_start = int(getattr(rg, "review_confirm_generation", 0) or 0)
        finally:
            db_g.close()

    for attempt in range(_SCHEDULE_CHAT_MAX_ATTEMPTS):
        if mode == "review" and gen_start is not None:
            db_chk = SessionLocal()
            try:
                rg = (
                    db_chk.query(PublishAccountCreatorSchedule)
                    .filter(PublishAccountCreatorSchedule.id == sch.id)
                    .first()
                )
                cur = int(getattr(rg, "review_confirm_generation", 0) or 0) if rg else -1
                if cur != gen_start:
                    logger.info(
                        "定时编排中止 account_id=%s generation=%s!=%s",
                        sch.account_id,
                        cur,
                        gen_start,
                    )
                    return {"skipped": True, "superseded": True}
            finally:
                db_chk.close()
        msg_body = user_msg if attempt == 0 else _schedule_publish_continuation_message(
            acct, attempt, last_hints
        )
        payload = {
            "message": msg_body,
            "history": history_payload,
            "model": None,
            "attachment_asset_ids": attachment_ids,
            "orchestration_report": True,
            "schedule_orchestration": True,
        }

        try:
            async with httpx.AsyncClient(timeout=_CHAT_TIMEOUT_SEC, trust_env=False) as client:
                r = await client.post(url, json=payload, headers=headers)
        except Exception as e:
            logger.exception(
                "定时编排请求异常 account_id=%s url=%s attempt=%s",
                sch.account_id,
                url,
                attempt,
            )
            return {"skipped": False, "ok": False, "error": str(e)[:800], "attempts": attempt + 1}

        if r.status_code != 200:
            body_preview = (r.text or "")[:800]
            logger.warning(
                "定时编排 POST /chat 失败 account_id=%s status=%s attempt=%s body=%s",
                sch.account_id,
                r.status_code,
                attempt,
                body_preview,
            )
            return {
                "skipped": False,
                "ok": False,
                "error": body_preview or f"HTTP {r.status_code}",
                "http_status": r.status_code,
                "attempts": attempt + 1,
            }

        try:
            data = r.json()
        except Exception:
            logger.warning("定时编排返回非 JSON account_id=%s attempt=%s", sch.account_id, attempt)
            return {
                "skipped": False,
                "ok": False,
                "error": "响应非 JSON",
                "http_status": r.status_code,
                "attempts": attempt + 1,
            }

        reply = (data.get("reply") or "").strip()
        orch = data.get("orchestration") if isinstance(data.get("orchestration"), dict) else {}
        tools = orch.get("tools") if isinstance(orch.get("tools"), list) else []
        publish_ok = orch.get("publish_ok")
        hints = orch.get("asset_id_hints") if isinstance(orch.get("asset_id_hints"), list) else []
        if hints:
            last_hints = [str(h).strip() for h in hints if str(h).strip()]

        ok_eval, err_eval = _eval_publish_success(
            publish_required=publish_required,
            publish_ok=publish_ok if isinstance(publish_ok, (bool, type(None))) else None,
            tools=tools,
        )
        last_fail_detail = err_eval

        logger.info(
            "定时编排 account_id=%s attempt=%s/%s user_id=%s publish_required=%s publish_ok=%s ok=%s reply_len=%s",
            sch.account_id,
            attempt + 1,
            _SCHEDULE_CHAT_MAX_ATTEMPTS,
            sch.user_id,
            publish_required,
            publish_ok,
            ok_eval,
            len(reply),
        )

        if ok_eval:
            logger.info(
                "定时编排完成 account_id=%s 摘要=%s",
                sch.account_id,
                (reply[:400] + "…") if len(reply) > 400 else reply or "(空)",
            )
            if mode == "review":
                dbx = SessionLocal()
                try:
                    row = (
                        dbx.query(PublishAccountCreatorSchedule)
                        .filter(PublishAccountCreatorSchedule.id == sch.id)
                        .first()
                    )
                    if row and int(getattr(row, "review_confirm_generation", 0) or 0) != gen_start:
                        logger.warning(
                            "审核队列推进跳过: 新确认已覆盖 account_id=%s",
                            sch.account_id,
                        )
                        return {
                            "skipped": False,
                            "ok": True,
                            "superseded": True,
                            "reply_len": len(reply),
                            "attempts": attempt + 1,
                        }
                    if row:
                        drafts = getattr(row, "review_drafts_json", None) or []
                        idx = int(getattr(row, "review_selected_slot", 0) or 0)
                        if isinstance(drafts, list) and 0 <= idx < len(drafts):
                            next_idx = idx + 1
                            if next_idx >= len(drafts):
                                row.review_confirmed = False
                                row.review_drafts_json = None
                                row.review_selected_slot = 0
                                row.review_first_eta_at = None
                            else:
                                row.review_selected_slot = next_idx
                        else:
                            row.review_confirmed = False
                            row.review_drafts_json = None
                            row.review_selected_slot = 0
                            row.review_first_eta_at = None
                        row.updated_at = datetime.utcnow()
                        dbx.commit()
                except Exception:
                    logger.exception(
                        "advance review queue after orchestration account_id=%s", sch.account_id
                    )
                finally:
                    dbx.close()
            return {
                "skipped": False,
                "ok": True,
                "reply_len": len(reply),
                "attempts": attempt + 1,
            }

        history_payload = history_payload + [
            {"role": "user", "content": msg_body},
            {"role": "assistant", "content": reply},
        ]

    return {
        "skipped": False,
        "ok": False,
        "error": last_fail_detail or "编排未成功",
        "attempts": _SCHEDULE_CHAT_MAX_ATTEMPTS,
    }
