"""本地「发布任务」接口：多来源 + 每天条数/时间点 + 台账去重 + 抖音/视频号。

配套执行器见 backend/app/services/publish_plan_runner.py。
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from .auth import _ServerUser, get_current_user_for_local
from ..db import get_db
from ..models import PublishAccount, PublishGroupItem, PublishPlan, PublishPlanItem, PublishTask

logger = logging.getLogger(__name__)
router = APIRouter()

_PLATFORMS = {"douyin", "wechat_channels", "tiktok"}
_MAX_TIMES = 24




class PublishScheduleReq(BaseModel):
    name: Optional[str] = None
    platform: str = "douyin"
    account_id: Optional[int] = None
    account_nickname: Optional[str] = None
    source_folder: Optional[str] = None
    source_groups: Optional[List[str]] = None
    source_records: Optional[List[Dict[str, Any]]] = None
    daily_count: int = 3
    times: Optional[List[str]] = None
    start_date: Optional[str] = None
    tags: Optional[str] = None
    ai_copy: bool = True
    options: Optional[Dict[str, Any]] = None
    page: int = 1
    page_size: int = 20


def _assign_schedule_slots(count: int, times: List[str], start_date: Optional[str], now: datetime,
                           min_lead: Optional[timedelta] = None) -> List[datetime]:
    """把 count 个素材按每天 len(times) 条、每条对应一个 HH:MM，从 start_date（默认今天）开始往后排。

    min_lead: 平台要求的最小提前量（抖音 2 小时、视频号 5 分钟）。太近的槽位直接跳过，
    避免创建出平台必然拒绝的定时任务。
    """
    clean = sorted({str(x).strip() for x in (times or []) if str(x).strip() and len(str(x).strip()) == 5})
    if not clean:
        return []
    day = now.date()
    if start_date:
        try:
            day = datetime.strptime(str(start_date).strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            day = now.date()
    slots: List[datetime] = []
    guard = 0
    while len(slots) < count and guard < 3650:
        guard += 1
        for hhmm in clean:
            try:
                hh, mm = int(hhmm[:2]), int(hhmm[3:])
            except ValueError:
                continue
            when = datetime.combine(day, datetime.min.time()).replace(hour=hh, minute=mm)
            if when <= now:
                continue
            if min_lead and when < now + min_lead:
                continue
            slots.append(when)
            if len(slots) >= count:
                break
        day = day + timedelta(days=1)
    return slots


def _schedule_candidates(db: Session, user_id: int, body: PublishScheduleReq) -> List[Dict[str, Any]]:
    """按来源收集素材，并剔除已经发过（同平台账号台账）的。"""
    from types import SimpleNamespace
    from ..services import publish_plan_runner as runner
    from ..models import PublishPlan as _Plan, PublishPlanItem as _Item

    tmp = SimpleNamespace(
        id=0, user_id=int(user_id), platform=str(body.platform or "douyin"),
        source_folder=str(body.source_folder or "").strip() or None,
        source_groups=[str(g).strip() for g in (body.source_groups or []) if str(g).strip()],
        source_records=[r for r in (body.source_records or []) if isinstance(r, dict)],
        options=dict(body.options or {}), tags=str(body.tags or ""),
    )
    items = runner.collect_candidates(db, tmp)
    if not items:
        return []
    done = {
        str(row[0])
        for row in (
            db.query(_Item.asset_key)
            .join(_Plan, _Plan.id == _Item.plan_id)
            .filter(_Plan.user_id == int(user_id), _Plan.platform == tmp.platform,
                    _Item.status.in_(("pending", "success")))
            .all()
        )
    }
    return [item for item in items if str(item.get("key")) not in done]


@router.post("/api/publish/plans/preview", summary="预览定时发布规划")
def preview_publish_schedule(
    body: PublishScheduleReq,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    times = _clean_times(body.times)
    if not times:
        raise HTTPException(400, detail="请先设置每天的发布时间（HH:MM）")
    daily = max(1, min(int(body.daily_count or len(times)), 20))
    times = times[:daily] if len(times) >= daily else times
    platform = str(body.platform or "douyin").strip().lower()
    candidates = _schedule_candidates(db, current_user.id, body)
    slots = _assign_schedule_slots(
        len(candidates), times, body.start_date, datetime.now(),
        min_lead=_schedule_min_lead(platform),
    )
    rows = []
    for idx, (item, when) in enumerate(zip(candidates, slots), start=1):
        rows.append({
            "index": idx,
            "key": item.get("key"),
            "name": item.get("name") or item.get("title") or item.get("path") or item.get("url") or "",
            "asset_id": item.get("asset_id") or "",
            "path": item.get("path") or "",
            "media_type": item.get("media_type") or "",
            "publish_at": when.strftime("%Y-%m-%d %H:%M"),
        })
    page = max(1, int(body.page or 1))
    size = max(1, min(int(body.page_size or 20), 200))
    start = (page - 1) * size
    return {
        "ok": True,
        "total": len(rows),
        "page": page,
        "page_size": size,
        "days": len({r["publish_at"][:10] for r in rows}),
        "times": times,
        "rows": rows[start:start + size],
    }


@router.post("/api/publish/plans/commit", summary="按规划立即创建定时发布任务")
async def commit_publish_schedule(
    request: Request,
    body: PublishScheduleReq,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    from ..services import publish_plan_runner as runner
    from .publish import PublishReq, create_publish_task

    times = _clean_times(body.times)
    if not times:
        raise HTTPException(400, detail="请先设置每天的发布时间（HH:MM）")
    daily = max(1, min(int(body.daily_count or len(times)), 20))
    times = times[:daily] if len(times) >= daily else times
    platform = str(body.platform or "douyin").strip().lower()
    candidates = _schedule_candidates(db, current_user.id, body)
    slots = _assign_schedule_slots(
        len(candidates), times, body.start_date, datetime.now(),
        min_lead=_schedule_min_lead(platform),
    )
    if not candidates or not slots:
        raise HTTPException(400, detail="没有待发布的素材（该来源下的素材都已发过）")
    acct = _resolve_plan_account(db, current_user.id, body.account_id, body.account_nickname)
    if acct is None and (body.account_id or str(body.account_nickname or "").strip()):
        raise HTTPException(404, detail="发布账号不存在，请先在「发布账号」里添加")
    if acct is not None and str(acct.platform or "").strip().lower() != platform:
        raise HTTPException(400, detail="选择的账号平台与规划平台不一致")
    time_key = "wechat_channels_publish_time" if platform == "wechat_channels" else "douyin_publish_time"

    # 这条记录是「已在上游排程」的批次台账：任务在平台侧创建好，
    # 到点由平台发布，不靠本机 runner 到点再点发布。
    plan_options = dict(body.options or {})
    plan_options["_batch_committed"] = True
    plan = PublishPlan(
        user_id=current_user.id,
        name=(str(body.name or "").strip() or f"定时任务 {datetime.now().strftime('%m-%d %H:%M')}")[:160],
        enabled=False,
        platform=platform,
        account_id=int(acct.id) if acct is not None else None,
        account_nickname=(acct.nickname if acct is not None else str(body.account_nickname or "")) or "",
        source_folder=str(body.source_folder or "").strip() or None,
        source_groups=[str(g).strip() for g in (body.source_groups or []) if str(g).strip()],
        source_records=[r for r in (body.source_records or []) if isinstance(r, dict) and str(r.get("url") or "").strip()],
        daily_count=len(times),
        times=times,
        tags=str(body.tags or "").strip() or None,
        ai_copy=bool(body.ai_copy),
        options=plan_options,
        fired_slots={},
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)

    def _save_item(item: Dict[str, Any], when: datetime, idx: int, asset_id: str,
                   task_id: Optional[int], status: str, error: str = "") -> Dict[str, Any]:
        key = str(item.get("key") or "")[:500]
        row = (
            db.query(PublishPlanItem)
            .filter(PublishPlanItem.plan_id == int(plan.id), PublishPlanItem.asset_key == key)
            .first()
        )
        if row is None:
            row = PublishPlanItem(plan_id=int(plan.id), user_id=int(current_user.id), asset_key=key)
            db.add(row)
        row.source_kind = str(item.get("kind") or "")
        row.source_ref = str(item.get("path") or item.get("url") or item.get("asset_id") or "")[:2000]
        row.asset_id = str(asset_id or "")
        row.title = str(item.get("name") or item.get("title") or item.get("path") or key)[:255]
        row.task_id = int(task_id) if task_id else None
        row.status = status
        row.error = (str(error)[:500] if error else None)
        row.used_date = when.strftime("%Y-%m-%d")
        db.add(row)
        db.commit()
        return {
            "index": idx,
            "key": key,
            "name": row.title,
            "asset_id": row.asset_id,
            "path": str(item.get("path") or ""),
            "publish_at": when.strftime("%Y-%m-%d %H:%M"),
            "task_id": row.task_id,
            "status": row.status,
            "error": row.error or "",
        }

    created, failed, commit_items = [], [], []
    for idx, (item, when) in enumerate(zip(candidates, slots), start=1):
        publish_at = when.strftime("%Y-%m-%d %H:%M")
        asset_id = ""
        task_id: Optional[int] = None
        try:
            asset_id = await runner._ensure_asset_id(current_user, item, str(body.tags or ""))
            if not asset_id:
                raise RuntimeError("未能导入素材到素材库")
            options = dict(body.options or {})
            options[time_key] = publish_at
            payload = PublishReq(
                asset_id=str(asset_id),
                account_id=int(acct.id) if acct is not None else body.account_id,
                account_nickname=(acct.nickname if acct is not None else str(body.account_nickname or "")),
                title="", description="", tags=str(body.tags or ""), options=options,
            )
            result = await create_publish_task(request, payload, current_user, db)
            raw_task_id = result.get("task_id")
            task_id = int(raw_task_id) if raw_task_id else None
            status = str(result.get("status") or "").strip()
            if status not in ("success", "pending", "failed"):
                status = "failed" if result.get("error") else "pending"
            error = str(result.get("error") or "")
            row = _save_item(item, when, idx, str(asset_id), task_id, status, error)
            commit_items.append(row)
            if status == "failed":
                failed.append({"key": item.get("key"), "publish_at": publish_at, "error": error[:200]})
            else:
                created.append({"asset_id": asset_id, "publish_at": publish_at, "task_id": task_id, "status": status, "result": result})
        except Exception as exc:  # noqa: BLE001
            logger.warning("[定时规划] 创建发布任务失败 key=%s: %s", item.get("key"), exc)
            row = _save_item(item, when, idx, str(asset_id or ""), None, "failed", str(exc)[:500])
            commit_items.append(row)
            failed.append({"key": item.get("key"), "publish_at": publish_at, "error": str(exc)[:200]})

    final_options = dict(plan.options or {})
    final_options["_commit_items"] = commit_items
    final_options["_commit_summary"] = {
        "total": len(candidates),
        "created": len(created),
        "failed": len(failed),
    }
    plan.options = final_options
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return {
        "ok": True,
        "plan_id": plan.id,
        "plan": _plan_payload(db, plan),
        "created": len(created),
        "failed": len(failed),
        "items": created,
        "errors": failed,
    }

class PlanItemTriggerReq(BaseModel):
    key: str
    publish_at: Optional[str] = None


def _plan_items_summary(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "total": len(rows),
        "created": sum(1 for r in rows if r.get("task_id") and r.get("status") in ("pending", "success")),
        "success": sum(1 for r in rows if r.get("status") == "success"),
        "pending": sum(1 for r in rows if r.get("status") == "pending"),
        "failed": sum(1 for r in rows if r.get("status") == "failed"),
        "not_created": sum(1 for r in rows if r.get("status") == "not_created"),
        "skipped": sum(1 for r in rows if r.get("status") == "skipped"),
    }


@router.get("/api/publish/plans/{plan_id}/items", summary="定时任务逐条状态（含未创建）")
def list_plan_items(
    plan_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    plan = (
        db.query(PublishPlan)
        .filter(PublishPlan.id == int(plan_id), PublishPlan.user_id == current_user.id)
        .first()
    )
    if plan is None:
        raise HTTPException(404, detail="发布任务不存在")
    rows = _plan_items_rows(db, plan, include_preview=True)
    return {"ok": True, "plan_id": plan.id, "items": rows, "summary": _plan_items_summary(rows)}


@router.post("/api/publish/plans/{plan_id}/trigger-item", summary="触发单条未创建/失败的定时任务")
async def trigger_plan_item(
    plan_id: int,
    body: PlanItemTriggerReq,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    from ..services import publish_plan_runner as runner
    from .publish import PublishReq, create_publish_task

    plan = (
        db.query(PublishPlan)
        .filter(PublishPlan.id == int(plan_id), PublishPlan.user_id == current_user.id)
        .first()
    )
    if plan is None:
        raise HTTPException(404, detail="发布任务不存在")
    if not (plan.options or {}).get("_batch_committed"):
        raise HTTPException(400, detail="只有已排程批次支持单条触发")

    rows = _plan_items_rows(db, plan, include_preview=True)
    row = next((r for r in rows if str(r.get("key")) == str(body.key)), None)
    if row is None:
        raise HTTPException(404, detail="这条素材已不在当前规划里")
    if row.get("status") in ("pending", "success"):
        return {"ok": True, "created": False, "item": row, "plan": _plan_payload(db, plan)}

    body_req = _plan_source_body(plan)
    candidates = _schedule_candidates(db, int(plan.user_id), body_req)
    candidate = next((c for c in candidates if str(c.get("key")) == str(body.key)), None)
    if candidate is None:
        raise HTTPException(404, detail="这条素材已不在来源目录/分组里")

    when: Optional[datetime] = None
    raw_when = str(body.publish_at or row.get("publish_at") or "").strip()
    if raw_when:
        try:
            when = datetime.strptime(raw_when[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            when = None
    now = datetime.now()
    min_lead = _schedule_min_lead(plan.platform)
    if when is None or when <= now or (min_lead and when < now + min_lead):
        times = _clean_times(plan.times)
        daily = max(1, min(int(plan.daily_count or len(times) or 1), 20))
        times = times[:daily] if len(times) >= daily else times
        slots = _assign_schedule_slots(1, times, None, now, min_lead=min_lead)
        if not slots:
            raise HTTPException(400, detail="没有可用的定时发布时间")
        when = slots[0]
    publish_at = when.strftime("%Y-%m-%d %H:%M")

    asset_id = ""
    task_id: Optional[int] = None
    status = "failed"
    error = ""
    created_flag = False
    result: Dict[str, Any] = {}
    try:
        asset_id = await runner._ensure_asset_id(current_user, candidate, str(plan.tags or ""))
        if not asset_id:
            raise RuntimeError("素材入库失败")
        existing = (
            db.query(PublishTask)
            .filter(
                PublishTask.user_id == current_user.id,
                PublishTask.asset_id == str(asset_id),
                PublishTask.status.in_(("pending", "success")),
            )
            .order_by(PublishTask.id.desc())
            .first()
        )
        if existing is not None:
            status = "skipped"
            task_id = existing.id
            error = f"同一素材已有发布任务 #{existing.id}，已跳过"
        else:
            acct = _resolve_plan_account(db, current_user.id, plan.account_id, plan.account_nickname)
            if acct is None:
                raise RuntimeError("发布账号不存在，请先在「发布账号」里添加")
            time_key = "wechat_channels_publish_time" if plan.platform == "wechat_channels" else "douyin_publish_time"
            options = {k: v for k, v in dict(plan.options or {}).items() if not str(k).startswith("_")}
            options[time_key] = publish_at
            payload = PublishReq(
                asset_id=str(asset_id),
                account_id=int(acct.id),
                account_nickname=acct.nickname or "",
                title="", description="", tags=str(plan.tags or ""), options=options,
            )
            result = await create_publish_task(request, payload, current_user, db)
            raw_task_id = result.get("task_id")
            task_id = int(raw_task_id) if raw_task_id else None
            status = str(result.get("status") or "").strip()
            if status not in ("success", "pending", "failed"):
                status = "failed" if result.get("error") else "pending"
            error = str(result.get("error") or "")
            created_flag = status != "failed"
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        task_id = None
        error = str(exc)[:500]
        created_flag = False

    updated = _save_commit_item(
        db, plan, candidate, when,
        int(row.get("index") or 0), str(asset_id), task_id, status, error,
    )
    items = [
        it for it in (plan.options or {}).get("_commit_items") or []
        if str(it.get("key")) != str(body.key)
    ]
    items.append(updated)
    items.sort(key=lambda x: str(x.get("publish_at") or ""))
    opts = dict(plan.options or {})
    opts["_commit_items"] = items
    opts["_commit_summary"] = {
        "total": len(items),
        "created": sum(1 for x in items if x.get("task_id") and x.get("status") in ("pending", "success")),
        "failed": sum(1 for x in items if x.get("status") == "failed"),
        "skipped": sum(1 for x in items if x.get("status") == "skipped"),
    }
    plan.options = opts
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return {
        "ok": True,
        "created": created_flag,
        "item": updated,
        "plan": _plan_payload(db, plan),
        "result": result,
    }


class PickFolderReq(BaseModel):
    initial: Optional[str] = None


class PublishGroupItemReq(BaseModel):
    kind: Optional[str] = "record"
    media_type: Optional[str] = "video"
    asset_id: Optional[str] = None
    url: Optional[str] = None
    local_path: Optional[str] = None
    title: Optional[str] = None
    prompt: Optional[str] = None
    source: Optional[str] = None
    source_id: Optional[str] = None


class PublishGroupReq(BaseModel):
    group: str
    items: List[PublishGroupItemReq] = []


class PublishPlanReq(BaseModel):
    name: Optional[str] = None
    platform: str = "douyin"
    account_id: Optional[int] = None
    account_nickname: Optional[str] = None
    source_folder: Optional[str] = None
    source_groups: Optional[List[str]] = None
    source_records: Optional[List[Dict[str, Any]]] = None
    daily_count: int = 1
    times: Optional[List[str]] = None
    tags: Optional[str] = None
    ai_copy: bool = True
    options: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


def _resolve_plan_account(
    db: Session,
    user_id: int,
    account_id: Optional[int],
    account_nickname: Optional[str],
) -> Optional[PublishAccount]:
    """和 POST /api/publish 用同一套账号解析，避免「列表里有、保存说没有」。"""
    try:
        from .publish import _resolve_publish_account_for_request
    except Exception:  # noqa: BLE001
        logger.exception("[定时发布] 加载发布账号解析失败")
        return None
    try:
        return _resolve_publish_account_for_request(
            db,
            int(user_id),
            int(account_id) if account_id else None,
            str(account_nickname or ""),
        )
    except Exception:  # noqa: BLE001
        logger.exception("[定时发布] 解析发布账号异常 account_id=%s nickname=%s", account_id, account_nickname)
        return None


def _pause_other_plans(db: Session, user_id: int, keep_id: Optional[int] = None) -> int:
    """一个设备只能启动一个定时发布：启动某个任务时，把其它任务停掉。"""
    query = db.query(PublishPlan).filter(PublishPlan.user_id == int(user_id))
    if keep_id:
        query = query.filter(PublishPlan.id != int(keep_id))
    stopped = 0
    for row in query.all():
        if row.enabled:
            row.enabled = False
            stopped += 1
    return stopped


def _folder_is_dir(folder: str) -> bool:
    """Path.is_dir() 对非法路径/断开的网络盘可能抛异常，统一当作不可用。"""
    text = str(folder or "").strip()
    if not text:
        return False
    try:
        return bool(Path(text).is_dir())
    except (OSError, ValueError):
        return False


def _schedule_min_lead(platform: str) -> Optional[timedelta]:
    p = str(platform or "").strip().lower()
    if p == "douyin":
        return timedelta(hours=2, minutes=1)
    if p == "wechat_channels":
        return timedelta(minutes=6)
    return None


def _plan_source_body(plan: PublishPlan) -> PublishScheduleReq:
    return PublishScheduleReq(
        name=plan.name,
        platform=plan.platform,
        account_id=plan.account_id,
        account_nickname=plan.account_nickname,
        source_folder=plan.source_folder,
        source_groups=list(plan.source_groups or []),
        source_records=list(plan.source_records or []),
        daily_count=int(plan.daily_count or 1),
        times=list(plan.times or []),
        tags=plan.tags,
        ai_copy=bool(plan.ai_copy),
        options=dict(plan.options or {}),
    )


def _save_commit_item(db: Session, plan: PublishPlan, item: Dict[str, Any], when: datetime,
                      idx: int, asset_id: str, task_id: Optional[int], status: str,
                      error: str = "") -> Dict[str, Any]:
    key = str(item.get("key") or "")[:500]
    row = (
        db.query(PublishPlanItem)
        .filter(PublishPlanItem.plan_id == int(plan.id), PublishPlanItem.asset_key == key)
        .first()
    )
    if row is None:
        row = PublishPlanItem(plan_id=int(plan.id), user_id=int(plan.user_id), asset_key=key)
        db.add(row)
    row.source_kind = str(item.get("kind") or "")
    row.source_ref = str(item.get("path") or item.get("url") or item.get("asset_id") or "")[:2000]
    row.asset_id = str(asset_id or "")
    row.title = str(item.get("name") or item.get("title") or item.get("path") or key)[:255]
    row.task_id = int(task_id) if task_id else None
    row.status = status
    row.error = (str(error)[:500] if error else None)
    row.used_date = when.strftime("%Y-%m-%d")
    db.add(row)
    db.commit()
    return {
        "index": idx,
        "key": key,
        "name": row.title,
        "asset_id": row.asset_id,
        "path": str(item.get("path") or ""),
        "publish_at": when.strftime("%Y-%m-%d %H:%M"),
        "task_id": row.task_id,
        "status": row.status,
        "error": row.error or "",
    }


def _plan_items_rows(db: Session, plan: PublishPlan, include_preview: bool = True) -> List[Dict[str, Any]]:
    """已处理条目 + 尚未创建任务的剩余候选，按计划时间排序。"""
    options = dict(plan.options or {})
    processed = list(options.get("_commit_items") or [])
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for idx, it in enumerate(processed, start=1):
        key = str(it.get("key") or "")
        if key:
            seen.add(key)
        status = str(it.get("status") or "pending")
        rows.append({
            "index": int(it.get("index") or idx),
            "key": key,
            "name": str(it.get("name") or it.get("asset_id") or ""),
            "asset_id": str(it.get("asset_id") or ""),
            "path": str(it.get("path") or ""),
            "publish_at": str(it.get("publish_at") or ""),
            "task_id": it.get("task_id"),
            "status": status,
            "error": str(it.get("error") or ""),
            "can_trigger": status == "failed",
        })
    db_items = (
        db.query(PublishPlanItem)
        .filter(PublishPlanItem.plan_id == int(plan.id))
        .order_by(PublishPlanItem.id.asc())
        .all()
    )
    task_ids = {int(r.task_id) for r in db_items if r.task_id}
    task_ids.update(int(it.get("task_id")) for it in processed if it.get("task_id"))
    tasks: Dict[int, PublishTask] = {}
    if task_ids:
        for task in db.query(PublishTask).filter(PublishTask.id.in_(task_ids)).all():
            tasks[int(task.id)] = task
    for row in rows:
        tid = row.get("task_id")
        task = tasks.get(int(tid)) if tid else None
        if task is not None and str(getattr(task, "title", "") or "").strip():
            row["name"] = str(task.title)
    db_by_key = {str(r.asset_key): r for r in db_items}
    for row in rows:
        dbrow = db_by_key.get(str(row.get("key") or ""))
        if dbrow is None:
            continue
        row["status"] = str(dbrow.status or row.get("status") or "pending")
        row["task_id"] = dbrow.task_id or row.get("task_id")
        row["can_trigger"] = row["status"] == "failed"
    for idx, dbrow in enumerate(db_items, start=1):
        key = str(dbrow.asset_key or "")
        if not key or key in seen:
            continue
        task = tasks.get(int(dbrow.task_id)) if dbrow.task_id else None
        opts = dict((task.meta or {}).get("options") or {}) if task is not None else {}
        publish_at = str(
            opts.get("douyin_publish_time")
            or opts.get("wechat_channels_publish_time")
            or opts.get("publish_time")
            or dbrow.used_date
            or ""
        )
        status = str(dbrow.status or "pending")
        rows.append({
            "index": idx,
            "key": key,
            "name": str((getattr(task, "title", "") if task is not None else "") or dbrow.title or dbrow.asset_id or key),
            "asset_id": str(dbrow.asset_id or ""),
            "path": str(dbrow.source_ref or ""),
            "publish_at": publish_at,
            "task_id": dbrow.task_id,
            "status": status,
            "error": str(dbrow.error or ""),
            "can_trigger": status == "failed",
        })
        seen.add(key)
    if include_preview and bool(options.get("_batch_committed")):
        body = _plan_source_body(plan)
        candidates = _schedule_candidates(db, int(plan.user_id), body)
        remaining = [c for c in candidates if str(c.get("key") or "") not in seen]
        times = _clean_times(plan.times)
        daily = max(1, min(int(plan.daily_count or len(times) or 1), 20))
        times = times[:daily] if len(times) >= daily else times
        # 剩余素材不能和已经创建的任务抢同一个时间点。
        # 从已处理条目的最后一个计划时间之后继续排。
        base_now = datetime.now()
        for existing_row in rows:
            raw_when = str(existing_row.get("publish_at") or "").strip()
            if not raw_when:
                continue
            try:
                existing_when = datetime.strptime(raw_when[:16], "%Y-%m-%d %H:%M")
            except ValueError:
                try:
                    existing_when = datetime.strptime(raw_when[:10], "%Y-%m-%d")
                except ValueError:
                    continue
            if existing_when > base_now:
                base_now = existing_when
        slots = _assign_schedule_slots(
            len(remaining), times, None, base_now,
            min_lead=_schedule_min_lead(plan.platform),
        )
        next_index = max([int(r.get("index") or 0) for r in rows] or [0])
        for offset, (item, when) in enumerate(zip(remaining, slots), start=1):
            rows.append({
                "index": next_index + offset,
                "key": str(item.get("key") or ""),
                "name": str(item.get("name") or item.get("title") or item.get("path") or item.get("asset_id") or ""),
                "asset_id": str(item.get("asset_id") or ""),
                "path": str(item.get("path") or ""),
                "publish_at": when.strftime("%Y-%m-%d %H:%M"),
                "task_id": None,
                "status": "not_created",
                "error": "",
                "can_trigger": True,
            })
    rows.sort(key=lambda r: (str(r.get("publish_at") or "9999"), int(r.get("index") or 0)))
    return rows


def _clean_times(values: Optional[List[str]]) -> List[str]:
    out: List[str] = []
    for raw in (values or []):
        text = str(raw or "").strip()
        if ":" not in text:
            continue
        head, _, tail = text.partition(":")
        try:
            hour, minute = int(head.strip()), int(tail.strip())
        except ValueError:
            continue
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            normalized = f"{hour:02d}:{minute:02d}"
            if normalized not in out:
                out.append(normalized)
    return sorted(out)[:_MAX_TIMES]


def _plan_payload(db: Session, plan: PublishPlan) -> Dict[str, Any]:
    today = datetime.now().strftime("%Y-%m-%d")
    items = (
        db.query(PublishPlanItem)
        .filter(PublishPlanItem.plan_id == int(plan.id))
        .order_by(PublishPlanItem.id.desc())
        .limit(200)
        .all()
    )
    sent_today = sum(1 for row in items if row.used_date == today and row.status in ("pending", "success"))
    base_count = db.query(func.count(PublishPlanItem.id)).filter(PublishPlanItem.plan_id == int(plan.id))
    item_total = int(base_count.scalar() or 0)
    created_total = int(base_count.filter(PublishPlanItem.task_id.isnot(None), PublishPlanItem.status.in_(("pending", "success"))).scalar() or 0)
    success_total = int(base_count.filter(PublishPlanItem.status == "success").scalar() or 0)
    pending_total = int(base_count.filter(PublishPlanItem.status == "pending").scalar() or 0)
    failed_total = int(base_count.filter(PublishPlanItem.status == "failed").scalar() or 0)
    stored_commit_items = list((plan.options or {}).get("_commit_items") or [])
    if not stored_commit_items:
        stored_commit_items = [
            {
                "index": idx,
                "key": row.asset_key,
                "name": row.title or row.asset_id or row.asset_key,
                "asset_id": row.asset_id or "",
                "path": row.source_ref or "",
                "publish_at": row.used_date or "",
                "task_id": row.task_id,
                "status": row.status,
                "error": row.error or "",
            }
            for idx, row in enumerate(items[:200], start=1)
        ]
    commit_items_payload = stored_commit_items
    commit_summary_payload = dict((plan.options or {}).get("_commit_summary") or {})
    if not commit_summary_payload:
        commit_summary_payload = {
            "total": item_total,
            "created": created_total,
            "failed": failed_total,
            "skipped": int(base_count.filter(PublishPlanItem.status == "skipped").scalar() or 0),
        }
    return {
        "id": plan.id,
        "user_id": plan.user_id,
        "name": plan.name or "",
        "enabled": bool(plan.enabled),
        "platform": plan.platform,
        "account_id": plan.account_id,
        "account_nickname": plan.account_nickname or "",
        "source_folder": plan.source_folder or "",
        "source_groups": list(plan.source_groups or []),
        "source_records": list(plan.source_records or []),
        "daily_count": int(plan.daily_count or 1),
        "times": list(plan.times or []),
        "tags": plan.tags or "",
        "ai_copy": bool(plan.ai_copy),
        "options": dict(plan.options or {}),
        "committed": bool((plan.options or {}).get("_batch_committed")),
        "commit_items": commit_items_payload,
        "commit_summary": commit_summary_payload,
        "item_total": item_total,
        "created_total": created_total,
        "success_total": success_total,
        "pending_total": pending_total,
        "sent_total": item_total,
        "sent_today": sent_today,
        "failed_total": failed_total,
        "last_run_at": plan.last_run_at.isoformat() if plan.last_run_at else None,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "items": [
            {
                "id": row.id,
                "asset_key": row.asset_key,
                "asset_id": row.asset_id or "",
                "source_kind": row.source_kind or "",
                "title": row.title or "",
                "tags": row.tags or "",
                "status": row.status,
                "task_id": row.task_id,
                "error": row.error or "",
                "used_date": row.used_date or "",
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in items[:50]
        ],
    }


@router.get("/api/publish/plans", summary="发布任务列表")
def list_publish_plans(
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(PublishPlan)
        .filter(PublishPlan.user_id == current_user.id)
        .order_by(PublishPlan.id.desc())
        .all()
    )
    return {"plans": [_plan_payload(db, row) for row in rows]}


@router.post("/api/publish/plans", summary="新建发布任务")
def create_publish_plan(
    body: PublishPlanReq,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    platform = str(body.platform or "").strip().lower()
    if platform not in _PLATFORMS:
        raise HTTPException(400, detail="平台只支持抖音（douyin）、TikTok（tiktok）或视频号（wechat_channels）")
    times = _clean_times(body.times)
    if not times:
        raise HTTPException(400, detail="请至少设置一个发布时间（HH:MM）")
    folder = str(body.source_folder or "").strip()
    groups = [str(g or "").strip() for g in (body.source_groups or []) if str(g or "").strip()]
    records = [r for r in (body.source_records or []) if isinstance(r, dict) and str(r.get("url") or "").strip()]
    if not folder and not groups and not records:
        raise HTTPException(400, detail="请至少指定一个素材来源：本地文件夹、素材库分组或内容记录")
    if folder and not _folder_is_dir(folder):
        raise HTTPException(400, detail=f"本地文件夹不存在或不可访问：{folder}")
    acct = _resolve_plan_account(db, current_user.id, body.account_id, body.account_nickname)
    if (body.account_id or str(body.account_nickname or "").strip()) and acct is None:
        raise HTTPException(404, detail="发布账号不存在，请先在「发布账号」里添加")
    plan = PublishPlan(
        user_id=current_user.id,
        name=(str(body.name or "").strip() or f"发布任务 {datetime.now().strftime('%m-%d %H:%M')}")[:160],
        enabled=bool(body.enabled if body.enabled is not None else True),
        platform=platform,
        account_id=int(acct.id) if acct is not None else None,
        account_nickname=(acct.nickname if acct is not None else (body.account_nickname or "")) or "",
        source_folder=folder or None,
        source_groups=groups,
        source_records=records,
        daily_count=len(times),
        times=times,
        tags=str(body.tags or "").strip() or None,
        ai_copy=bool(body.ai_copy),
        options=dict(body.options or {}),
        fired_slots={},
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    if plan.enabled:
        stopped = _pause_other_plans(db, current_user.id, keep_id=int(plan.id))
        if stopped:
            db.commit()
            logger.info("[定时发布] 启动 plan=%s，已停掉其它 %s 个任务（一个设备只跑一个）", plan.id, stopped)
    logger.info(
        "[发布任务] 新建 plan=%s platform=%s 每天=%s 时间=%s 来源folder=%s groups=%s records=%s",
        plan.id, plan.platform, plan.daily_count, plan.times,
        bool(plan.source_folder), len(groups), len(records),
    )
    return {"ok": True, "plan": _plan_payload(db, plan)}


@router.patch("/api/publish/plans/{plan_id}", summary="更新/启停发布任务")
def update_publish_plan(
    plan_id: int,
    body: PublishPlanReq,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    plan = (
        db.query(PublishPlan)
        .filter(PublishPlan.id == int(plan_id), PublishPlan.user_id == current_user.id)
        .first()
    )
    if plan is None:
        raise HTTPException(404, detail="发布任务不存在")
    if body.enabled is not None:
        plan.enabled = bool(body.enabled)
    if body.name is not None:
        name = str(body.name or "").strip()
        if name:
            plan.name = name[:160]
    platform = str(body.platform or "").strip().lower()
    if platform in _PLATFORMS:
        plan.platform = platform
    if body.times is not None:
        times = _clean_times(body.times)
        if times:
            plan.times = times
            plan.daily_count = len(times)
    if body.source_folder is not None:
        folder = str(body.source_folder or "").strip()
        if folder and not _folder_is_dir(folder):
            raise HTTPException(400, detail=f"本地文件夹不存在或不可访问：{folder}")
        plan.source_folder = folder or None
    if body.source_groups is not None:
        groups = [str(g or "").strip() for g in (body.source_groups or []) if str(g or "").strip()]
        plan.source_groups = groups
    if body.source_records is not None:
        plan.source_records = [r for r in (body.source_records or []) if isinstance(r, dict) and str(r.get("url") or "").strip()]
    if body.tags is not None:
        plan.tags = str(body.tags or "").strip() or None
    if body.ai_copy is not None:
        plan.ai_copy = bool(body.ai_copy)
    if body.account_id or str(body.account_nickname or "").strip():
        acct = _resolve_plan_account(db, current_user.id, body.account_id, body.account_nickname)
        if acct is None:
            raise HTTPException(404, detail="发布账号不存在")
        plan.account_id = int(acct.id)
        plan.account_nickname = acct.nickname or ""
    if body.options is not None:
        plan.options = dict(body.options or {})
    if plan.enabled:
        stopped = _pause_other_plans(db, current_user.id, keep_id=int(plan.id))
        if stopped:
            logger.info("[定时发布] 启动 plan=%s，已停掉其它 %s 个任务（一个设备只跑一个）", plan.id, stopped)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return {"ok": True, "plan": _plan_payload(db, plan)}


@router.delete("/api/publish/plans/{plan_id}", summary="删除发布任务")
def delete_publish_plan(
    plan_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    plan = (
        db.query(PublishPlan)
        .filter(PublishPlan.id == int(plan_id), PublishPlan.user_id == current_user.id)
        .first()
    )
    if plan is None:
        raise HTTPException(404, detail="发布任务不存在")
    db.query(PublishPlanItem).filter(PublishPlanItem.plan_id == int(plan.id)).delete()
    db.delete(plan)
    db.commit()
    return {"ok": True}


@router.post("/api/publish/plans/{plan_id}/run-now", summary="立即执行发布任务的一条")
async def run_publish_plan_now(
    plan_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    plan = (
        db.query(PublishPlan)
        .filter(PublishPlan.id == int(plan_id), PublishPlan.user_id == current_user.id)
        .first()
    )
    if plan is None:
        raise HTTPException(404, detail="发布任务不存在")
    from ..services.publish_plan_runner import run_plan_once

    result = await run_plan_once(int(plan.id))
    if not result.get("ok"):
        raise HTTPException(400, detail=str(result.get("error") or "发布失败"))
    return result


# ── 系统「选择文件夹」：本地后端直接拉原生对话框（不依赖桌面壳桥接） ──────
_PICK_FOLDER_PS = """[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms | Out-Null
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '选择要发布的素材文件夹'
$dialog.ShowNewFolderButton = $true
$initial = $env:LOBSTER_PICK_INITIAL
if ($initial -and (Test-Path -LiteralPath $initial)) { $dialog.SelectedPath = $initial }
# 用一个置顶的隐形宿主窗体当 owner，保证对话框弹在最前面（否则会藏在应用窗口后面，需要再点一次）
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.FormBorderStyle = 'None'
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.StartPosition = 'Manual'
$owner.Location = New-Object System.Drawing.Point(-2000, -2000)
$owner.Show()
$owner.Activate()
if ($env:LOBSTER_PICK_DRYRUN) { $owner.Close(); [Console]::Out.Write($env:LOBSTER_PICK_DRYRUN); exit 0 }
$result = $dialog.ShowDialog($owner)
$owner.Close()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($dialog.SelectedPath) }
"""


def _pick_directory_native(initial: str = "") -> Dict[str, Any]:
    """弹出系统文件夹选择框，返回 {"ok": True, "path": ...} / {"ok": False, "cancelled": True}。"""
    if os.name != "nt":
        return {"ok": False, "error": "当前系统不支持系统文件夹选择框"}
    env = dict(os.environ)
    env["LOBSTER_PICK_INITIAL"] = str(initial or "")
    handle = tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8-sig")
    try:
        handle.write(_PICK_FOLDER_PS)
        handle.close()
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Sta", "-ExecutionPolicy", "Bypass", "-File", handle.name],
            capture_output=True,
            env=env,
            timeout=1800,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "文件夹选择框超时未响应"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("[定时发布] 打开系统文件夹选择框失败")
        return {"ok": False, "error": f"无法打开系统文件夹选择框：{exc}"}
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
    path = (proc.stdout or b"").decode("utf-8", "replace").strip()
    if proc.returncode != 0 and not path:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()[:300]
        return {"ok": False, "error": err or f"文件夹选择框退出码 {proc.returncode}"}
    if not path:
        return {"ok": False, "cancelled": True}
    return {"ok": True, "path": path}


@router.post("/api/publish/pick-folder", summary="系统「选择文件夹」对话框（定时发布选本地文件夹）")
async def pick_publish_folder(
    body: PickFolderReq = PickFolderReq(),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    initial = str(body.initial or "").strip()
    return await asyncio.to_thread(_pick_directory_native, initial)


def _group_item_key(item: PublishGroupItemReq) -> str:
    if item.local_path:
        return f"file:{item.local_path}"
    if item.url:
        return f"url:{item.url}"
    if item.asset_id:
        return f"asset:{item.asset_id}"
    return ""


@router.get("/api/publish/groups", summary="发布分组列表（内容记录/素材归组）")
def list_publish_groups(
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(PublishGroupItem)
        .filter(PublishGroupItem.user_id == current_user.id)
        .order_by(PublishGroupItem.group_name.asc(), PublishGroupItem.id.asc())
        .all()
    )
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        bucket = grouped.setdefault(row.group_name, {"name": row.group_name, "count": 0, "video": 0, "image": 0})
        bucket["count"] += 1
        if str(row.media_type or "") == "image":
            bucket["image"] += 1
        else:
            bucket["video"] += 1
    return {"groups": list(grouped.values())}


@router.post("/api/publish/groups", summary="把内容记录/素材加入发布分组")
def create_publish_group(
    body: PublishGroupReq,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    name = str(body.group or "").strip()[:64]
    if not name:
        raise HTTPException(400, detail="请填写分组名")
    added = 0
    skipped = 0
    for item in (body.items or []):
        key = _group_item_key(item)
        if not key:
            skipped += 1
            continue
        exists = (
            db.query(PublishGroupItem)
            .filter(
                PublishGroupItem.user_id == current_user.id,
                PublishGroupItem.group_name == name,
                PublishGroupItem.item_key == key,
            )
            .first()
        )
        if exists is not None:
            skipped += 1
            continue
        media_type = str(item.media_type or "").strip().lower()
        db.add(PublishGroupItem(
            user_id=current_user.id,
            group_name=name,
            item_key=key,
            kind=str(item.kind or "record")[:24],
            media_type="image" if media_type == "image" else "video",
            asset_id=str(item.asset_id or "").strip() or None,
            url=str(item.url or "").strip() or None,
            local_path=str(item.local_path or "").strip() or None,
            title=str(item.title or "")[:255] or None,
            prompt=str(item.prompt or "")[:2000] or None,
        ))
        added += 1
    db.commit()
    logger.info("[发布分组] user=%s group=%s 新增=%s 跳过=%s", current_user.id, name, added, skipped)
    return {"ok": True, "group": name, "added": added, "skipped": skipped}


@router.get("/api/publish/groups/{group_name}/items", summary="发布分组里的素材")
def list_publish_group_items(
    group_name: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(PublishGroupItem)
        .filter(
            PublishGroupItem.user_id == current_user.id,
            PublishGroupItem.group_name == str(group_name or "").strip(),
        )
        .order_by(PublishGroupItem.id.asc())
        .all()
    )
    return {"items": [
        {
            "id": row.id, "item_key": row.item_key, "kind": row.kind, "media_type": row.media_type,
            "asset_id": row.asset_id or "", "url": row.url or "", "local_path": row.local_path or "",
            "title": row.title or "",
        }
        for row in rows
    ]}


@router.delete("/api/publish/groups/{group_name}", summary="删除发布分组")
def delete_publish_group(
    group_name: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    name = str(group_name or "").strip()
    if not name:
        raise HTTPException(400, detail="分组名不能为空")
    deleted = (
        db.query(PublishGroupItem)
        .filter(PublishGroupItem.user_id == current_user.id, PublishGroupItem.group_name == name)
        .delete()
    )
    db.commit()
    return {"ok": True, "deleted": int(deleted or 0)}
