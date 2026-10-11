"""本地「发布任务」接口：多来源 + 每天条数/时间点 + 台账去重 + 抖音/视频号。

配套执行器见 backend/app/services/publish_plan_runner.py。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .auth import _ServerUser, get_current_user_for_local
from ..db import get_db
from ..models import PublishAccount, PublishGroupItem, PublishPlan, PublishPlanItem

logger = logging.getLogger(__name__)
router = APIRouter()

_PLATFORMS = {"douyin", "wechat_channels"}
_MAX_TIMES = 24


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
        "sent_total": len(items),
        "sent_today": sent_today,
        "failed_total": sum(1 for row in items if row.status == "failed"),
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
        raise HTTPException(400, detail="平台只支持抖音（douyin）或视频号（wechat_channels）")
    times = _clean_times(body.times)
    if not times:
        raise HTTPException(400, detail="请至少设置一个发布时间（HH:MM）")
    folder = str(body.source_folder or "").strip()
    groups = [str(g or "").strip() for g in (body.source_groups or []) if str(g or "").strip()]
    records = [r for r in (body.source_records or []) if isinstance(r, dict) and str(r.get("url") or "").strip()]
    if not folder and not groups and not records:
        raise HTTPException(400, detail="请至少指定一个素材来源：本地文件夹、素材库分组或内容记录")
    if folder and not Path(folder).is_dir():
        raise HTTPException(400, detail=f"本地文件夹不存在或不可访问：{folder}")
    acct = None
    if body.account_id:
        acct = (
            db.query(PublishAccount)
            .filter(PublishAccount.id == int(body.account_id), PublishAccount.user_id == current_user.id)
            .first()
        )
        if acct is None:
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
        daily_count=max(1, min(int(body.daily_count or 1), 50)),
        times=times,
        tags=str(body.tags or "").strip() or None,
        ai_copy=bool(body.ai_copy),
        options=dict(body.options or {}),
        fired_slots={},
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
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
    if body.times is not None:
        times = _clean_times(body.times)
        if times:
            plan.times = times
    if body.daily_count:
        plan.daily_count = max(1, min(int(body.daily_count), 50))
    if body.tags is not None:
        plan.tags = str(body.tags or "").strip() or None
    if body.ai_copy is not None:
        plan.ai_copy = bool(body.ai_copy)
    if body.account_id:
        acct = (
            db.query(PublishAccount)
            .filter(PublishAccount.id == int(body.account_id), PublishAccount.user_id == current_user.id)
            .first()
        )
        if acct is None:
            raise HTTPException(404, detail="发布账号不存在")
        plan.account_id = int(acct.id)
        plan.account_nickname = acct.nickname
    if body.options is not None:
        plan.options = dict(body.options or {})
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


@router.get("/api/publish/plan-dirs", summary="浏览本机目录（发布任务选择本地文件夹用）")
def browse_local_dirs(
    path: str = "",
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    raw = str(path or "").strip()
    if not raw:
        home = Path.home()
        entries = [p for p in (home / "Desktop", home, home / "Documents", home / "Videos", Path("D:/"), Path("E:/")) if p.is_dir()]
        uniq: List[Path] = []
        for entry in entries:
            if entry not in uniq:
                uniq.append(entry)
        return {
            "path": "",
            "parent": "",
            "dirs": [{"name": p.name or str(p), "path": str(p)} for p in uniq],
        }
    target = Path(raw).expanduser()
    if not target.is_dir():
        raise HTTPException(400, detail=f"目录不存在：{raw}")
    dirs: List[Dict[str, str]] = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            try:
                if child.is_dir() and not child.name.startswith((".", "~$", "$")):
                    dirs.append({"name": child.name, "path": str(child)})
            except OSError:
                continue
    except PermissionError as exc:
        raise HTTPException(400, detail=f"没有权限访问：{raw}") from exc
    parent = str(target.parent) if target.parent != target else ""
    return {"path": str(target), "parent": parent, "dirs": dirs[:500]}


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


@router.get("/api/publish/groups/{group_name}/items", summary="发布分组里的素材")
def list_publish_group_items(
    group_name: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(PublishGroupItem)
        .filter(PublishGroupItem.user_id == current_user.id, PublishGroupItem.group_name == str(group_name or "").strip())
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
