"""本地「发布任务」执行器。

按用户设定的一天条数 + 时间点，从多来源（本机文件夹 / 素材库创意分组 / 内容记录）挑素材：
1. 素材落成本地 asset（本地文件走 /api/assets/upload，内容记录走 /api/assets/save-url）；
2. LLM 先理解内容写标题/文案/标签（用户填了标签就只用用户标签，只生成标题和文案）；
3. 调用本地 POST /api/publish 走既有抖音 / 视频号发布链路；
4. 每个素材发布后在 publish_plan_items 记账，保证同一个素材只发一次。

后台循环由 create_app 启动，每 20 秒检查一次到点槽位。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..api.auth import create_access_token
from ..core.config import settings
from ..db import SessionLocal
from ..models import Asset, PublishGroupItem, PublishPlan, PublishPlanItem, User
from .publish_copy_llm import PublishCopyLLMError, generate_publish_copy

logger = logging.getLogger(__name__)

_TICK_INTERVAL_SEC = 20
_PUBLISH_TIMEOUT_SEC = 30 * 60
_API_TIMEOUT_SEC = 180
_MAX_FOLDER_FILES = 2000
_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".flv", ".wmv", ".webm"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_MEDIA_EXTS = _VIDEO_EXTS | _IMAGE_EXTS


def _api_base_url() -> str:
    base = (getattr(settings, "public_base_url", None) or "").strip().rstrip("/")
    if base:
        return base
    return f"http://127.0.0.1:{int(getattr(settings, 'port', 8000) or 8000)}"


def _auth_headers(user: User) -> Dict[str, str]:
    token = create_access_token({"sub": str(int(user.id))})
    return {"Authorization": f"Bearer {token}"}


def _media_type_of(filename: str) -> str:
    ext = Path(str(filename or "")).suffix.lower()
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _IMAGE_EXTS:
        return "image"
    return ""


def _creative_group_of(meta: Any) -> str:
    if not isinstance(meta, dict):
        return ""
    current = str(meta.get("creative_candidate_group") or "").strip()
    if current:
        return current[:40]
    groups = meta.get("creative_candidate_groups")
    if isinstance(groups, list):
        for item in groups:
            value = str(item or "").strip()
            if value:
                return value[:40]
    return ""


# ── 候选素材 ─────────────────────────────────────────────────────────────

def _folder_candidates(folder: str) -> List[Dict[str, Any]]:
    root = Path(str(folder or "").strip())
    if not str(folder or "").strip() or not root.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        if any(part.startswith(".") or part.startswith("~$") for part in path.parts):
            continue
        media_type = _media_type_of(path.name)
        if not media_type:
            continue
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        out.append({
            "key": f"file:{resolved}",
            "kind": "folder",
            "label": path.name,
            "path": str(resolved),
            "media_type": media_type,
            "asset_id": "",
            "url": "",
            "prompt": "",
            "title": path.stem[:80],
        })
        if len(out) >= _MAX_FOLDER_FILES:
            break
    return out


def _group_candidates(db, user_id: int, groups: List[str]) -> List[Dict[str, Any]]:
    wanted = {str(g or "").strip() for g in (groups or []) if str(g or "").strip()}
    if not wanted:
        return []
    rows = (
        db.query(Asset)
        .filter(Asset.user_id == int(user_id))
        .order_by(Asset.created_at.asc())
        .limit(4000)
        .all()
    )
    out: List[Dict[str, Any]] = []
    for row in rows:
        group = _creative_group_of(getattr(row, "meta", None))
        if group not in wanted:
            continue
        media_type = _media_type_of(getattr(row, "filename", "") or "")
        if not media_type:
            continue
        out.append({
            "key": f"asset:{row.asset_id}",
            "kind": "group",
            "label": (getattr(row, "filename", "") or row.asset_id),
            "path": "",
            "media_type": media_type,
            "asset_id": str(row.asset_id),
            "url": str(getattr(row, "source_url", "") or ""),
            "prompt": str(getattr(row, "prompt", "") or "")[:2000],
            "title": str(getattr(row, "filename", "") or "")[:80],
        })
    return out


def _plan_group_candidates(db, user_id: int, names: List[str]) -> tuple[List[Dict[str, Any]], set]:
    """发布分组（内容记录/素材归组）里的素材；返回 (候选列表, 真的有内容的分组名)。"""
    out: List[Dict[str, Any]] = []
    used: set = set()
    for name in names:
        rows = (
            db.query(PublishGroupItem)
            .filter(PublishGroupItem.user_id == int(user_id), PublishGroupItem.group_name == str(name))
            .order_by(PublishGroupItem.id.asc())
            .all()
        )
        if not rows:
            continue
        used.add(str(name))
        for row in rows:
            media_type = str(row.media_type or "").strip().lower()
            out.append({
                "key": f"group:{name}:{row.item_key}",
                "kind": "group",
                "label": str(row.title or row.item_key)[:80],
                "path": str(row.local_path or ""),
                "media_type": "image" if media_type == "image" else "video",
                "asset_id": str(row.asset_id or ""),
                "url": str(row.url or ""),
                "prompt": str(row.prompt or "")[:2000],
                "title": str(row.title or "")[:80],
            })
    return out, used


def _record_candidates(records: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in (records or []):
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url") or "").strip()
        media_type = str(raw.get("media_type") or "").strip().lower()
        source = str(raw.get("source") or "").strip()
        source_id = str(raw.get("source_id") or "").strip()
        if not url or media_type not in {"video", "image"}:
            continue
        out.append({
            "key": f"record:{source}:{source_id or url}",
            "kind": "record",
            "label": str(raw.get("title") or source_id or url)[:80],
            "path": "",
            "media_type": media_type,
            "asset_id": "",
            "url": url,
            "prompt": str(raw.get("prompt") or "")[:2000],
            "title": str(raw.get("title") or "")[:80],
        })
    return out


def collect_candidates(db, plan: PublishPlan) -> List[Dict[str, Any]]:
    """按来源顺序收集候选素材（文件夹 → 素材库分组 → 内容记录），已发过的会被台账过滤。"""
    items: List[Dict[str, Any]] = []
    try:
        items.extend(_folder_candidates(plan.source_folder or ""))
    except Exception:  # noqa: BLE001
        logger.exception("[发布任务] 扫描本地文件夹失败 plan=%s", plan.id)
    groups = [str(g).strip() for g in (plan.source_groups or []) if str(g).strip()]
    plan_names = [g for g in groups if not g.startswith("lib:")]
    lib_names = [g[4:] for g in groups if g.startswith("lib:")]
    from_groups, used_names = _plan_group_candidates(db, int(plan.user_id), plan_names)
    items.extend(from_groups)
    # 旧计划里直接存的是「素材库分组」名：没有发布分组同名内容时按素材库分组解析
    legacy_lib = [g for g in plan_names if g not in used_names]
    items.extend(_group_candidates(db, int(plan.user_id), lib_names + legacy_lib))
    items.extend(_record_candidates(plan.source_records or []))
    seen = set()
    unique: List[Dict[str, Any]] = []
    for item in items:
        if item["key"] in seen:
            continue
        seen.add(item["key"])
        unique.append(item)
    sent = {
        str(row[0])
        for row in db.query(PublishPlanItem.asset_key)
        .filter(PublishPlanItem.plan_id == int(plan.id))
        .all()
    }
    return [item for item in unique if item["key"] not in sent]


# ── 发布 ─────────────────────────────────────────────────────────────────

async def _post_local(user: User, path: str, *, json_body: Optional[dict] = None,
                      files: Optional[dict] = None, data: Optional[dict] = None,
                      timeout: int = _API_TIMEOUT_SEC) -> Dict[str, Any]:
    headers = _auth_headers(user)
    url = f"{_api_base_url()}{path}"
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await client.post(url, headers=headers, json=json_body, files=files, data=data)
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = str((resp.json() or {}).get("detail") or "")
        except Exception:  # noqa: BLE001
            detail = (resp.text or "")[:300]
        raise RuntimeError(f"HTTP {resp.status_code}: {detail[:300]}")
    try:
        return resp.json() or {}
    except Exception:  # noqa: BLE001
        return {}


async def _ensure_asset_id(user: User, candidate: Dict[str, Any], tags: str) -> str:
    asset_id = str(candidate.get("asset_id") or "").strip()
    if asset_id:
        return asset_id
    if candidate.get("kind") == "folder" and candidate.get("path"):
        path = Path(str(candidate["path"]))
        if not path.is_file():
            raise RuntimeError(f"本地文件不存在：{path}")
        raw = await asyncio.to_thread(path.read_bytes)
        payload = await _post_local(
            user,
            "/api/assets/upload",
            files={"file": (path.name, raw, "application/octet-stream")},
            data={"tags": tags or "", "creative_candidate_group": ""},
        )
        asset_id = str(payload.get("asset_id") or payload.get("id") or "").strip()
        if not asset_id:
            raise RuntimeError(f"本地文件入库失败：{payload}")
        return asset_id
    url = str(candidate.get("url") or "").strip()
    if not url:
        raise RuntimeError("素材缺少可发布的链接或本地文件")
    payload = await _post_local(
        user,
        "/api/assets/save-url",
        json_body={
            "url": url,
            "media_type": candidate.get("media_type") or "video",
            "name": str(candidate.get("title") or candidate.get("label") or "")[:180],
            "prompt": str(candidate.get("prompt") or "")[:2000],
            "tags": tags or "",
        },
    )
    asset_id = str(payload.get("asset_id") or "").strip()
    if not asset_id:
        raise RuntimeError(f"内容记录转存失败：{payload}")
    return asset_id


async def _understand_asset(user: User, asset_id: str, media_type: str) -> str:
    """让模型先理解图片/视频（抽帧 + 视觉理解），返回可当作文案素材的描述；失败返回空串。"""
    asset_id = str(asset_id or "").strip()
    if not asset_id:
        return ""
    try:
        payload = await _post_local(user, f"/api/assets/{asset_id}/ai-digest", json_body={})
    except Exception:  # noqa: BLE001
        logger.warning("[定时发布] 素材摘要理解失败，退回标签理解 asset=%s", asset_id)
        payload = {}
    if isinstance(payload, dict) and (payload.get("summary") or payload.get("title")):
        summary = str(payload.get("summary") or "").strip()
        title = str(payload.get("title") or "").strip()
        extra = []
        if title:
            extra.append(title)
        if summary:
            extra.append(summary)
        text = "；".join(extra)[:2000]
        logger.info(
            "[定时发布] 素材理解完成(多帧) asset=%s media=%s 帧=%s 要点长度=%d",
            asset_id, media_type, payload.get("frames"), len(text),
        )
        return text
    try:
        fallback = await _post_local(user, f"/api/assets/{asset_id}/ai-tags", json_body={})
    except Exception:  # noqa: BLE001
        logger.exception("[定时发布] 素材 AI 理解失败 asset=%s", asset_id)
        return ""
    item = fallback.get("item") if isinstance(fallback, dict) else None
    text = ""
    if isinstance(item, dict):
        text = str(item.get("tags") or "").strip()
    if not text and isinstance(fallback, dict):
        text = str(fallback.get("tags") or "").strip()
    logger.info("[定时发布] 素材理解完成(标签兜底) asset=%s media=%s 描述长度=%d", asset_id, media_type, len(text))
    return text


async def _build_copy(user: User, plan: PublishPlan, candidate: Dict[str, Any]) -> Tuple[str, str, str]:
    """LLM 先理解内容再写标题/文案/标签；用户填了标签就只用用户标签。"""
    user_tags = str(plan.tags or "").strip()
    title = ""
    description = ""
    tags = user_tags
    if plan.ai_copy:
        try:
            title, description, generated_tags = await generate_publish_copy(
                platform=str(plan.platform or "douyin"),
                media_type=str(candidate.get("media_type") or "video"),
                asset_prompt=str(candidate.get("prompt") or "")[:2000],
                filename=str(candidate.get("title") or candidate.get("label") or "video.mp4"),
                hint_title="",
                hint_desc="",
                hint_tags=user_tags,
            )
            if user_tags:
                tags = user_tags
            else:
                tags = str(generated_tags or "").strip() or str(candidate.get("prompt") or "")[:120]
        except PublishCopyLLMError as exc:
            logger.warning("[发布任务] LLM 文案生成失败 plan=%s key=%s: %s", plan.id, candidate["key"], exc)
        except Exception:  # noqa: BLE001
            logger.exception("[发布任务] LLM 文案生成异常 plan=%s key=%s", plan.id, candidate["key"])
    if not title:
        title = (str(candidate.get("title") or "").strip() or "作品发布")[:80]
    if not description:
        description = str(candidate.get("prompt") or "").strip()[:600]
    return title, description, tags


async def _publish_candidate(db, plan: PublishPlan, candidate: Dict[str, Any], user: User) -> PublishPlanItem:
    today = datetime.now().strftime("%Y-%m-%d")
    item = PublishPlanItem(
        plan_id=int(plan.id),
        user_id=int(plan.user_id),
        asset_key=str(candidate["key"])[:500],
        source_kind=str(candidate.get("kind") or ""),
        source_ref=str(candidate.get("path") or candidate.get("url") or candidate.get("asset_id") or "")[:2000],
        asset_id=str(candidate.get("asset_id") or ""),
        status="pending",
        used_date=today,
    )
    db.add(item)
    try:
        db.commit()
        db.refresh(item)
    except Exception:  # noqa: BLE001
        db.rollback()
        raise
    try:
        asset_id = await _ensure_asset_id(user, candidate, str(plan.tags or ""))
        # 先理解内容（图片看图 / 视频抽帧），没有现成提示词时用它当文案依据
        if not str(candidate.get("prompt") or "").strip():
            understanding = await _understand_asset(user, asset_id, str(candidate.get("media_type") or "video"))
            if understanding:
                candidate["prompt"] = understanding
        title, description, tags = await _build_copy(user, plan, candidate)
        item.asset_id = asset_id
        item.title = title
        item.description = description
        item.tags = tags
        db.add(item)
        db.commit()
        options = dict(plan.options or {})
        options["_source_prompt"] = str(candidate.get("prompt") or "")[:2000]
        options["_source_title"] = str(candidate.get("title") or candidate.get("label") or "")[:240]
        options["_publish_plan_id"] = int(plan.id)
        result = await _post_local(
            user,
            "/api/publish",
            json_body={
                "asset_id": asset_id,
                "account_id": int(plan.account_id) if plan.account_id else None,
                "account_nickname": plan.account_nickname or "",
                "title": title,
                "description": description,
                "tags": tags,
                "ai_publish_copy": False,
                "options": options,
            },
            timeout=_PUBLISH_TIMEOUT_SEC,
        )
        task_id = result.get("task_id")
        status = str(result.get("status") or "")
        item.task_id = int(task_id) if task_id else None
        if status == "success":
            item.status = "success"
        elif result.get("error"):
            item.status = "failed"
            item.error = str(result.get("error"))[:500]
        else:
            item.status = "pending"
        logger.info(
            "[发布任务] 已发布 plan=%s asset=%s status=%s task=%s",
            plan.id, asset_id, item.status, item.task_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[发布任务] 发布失败 plan=%s key=%s", plan.id, candidate["key"])
        item.status = "failed"
        item.error = str(exc)[:500]
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _due_slots(plan: PublishPlan, now: datetime) -> List[str]:
    times = [str(t or "").strip() for t in (plan.times or []) if str(t or "").strip()]
    times = sorted({t for t in times if len(t) == 5 and t[2] == ":"})
    fired = plan.fired_slots if isinstance(plan.fired_slots, dict) else {}
    today = now.strftime("%Y-%m-%d")
    current = now.strftime("%H:%M")
    return [t for t in times if t <= current and not fired.get(f"{today}|{t}")]


def _sent_today(db, plan: PublishPlan, today: str) -> int:
    return (
        db.query(PublishPlanItem)
        .filter(
            PublishPlanItem.plan_id == int(plan.id),
            PublishPlanItem.used_date == today,
            PublishPlanItem.status.in_(("pending", "success")),
        )
        .count()
    )


async def run_plan_tick(db, now: Optional[datetime] = None) -> int:
    """检查所有启用的发布任务，到点则发布一条。返回本轮发布成功的条数。"""
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    plans = db.query(PublishPlan).filter(PublishPlan.enabled.is_(True)).all()
    fired_count = 0
    for plan in plans:
        try:
            slots = _due_slots(plan, now)
            if not slots:
                continue
            user = db.query(User).filter(User.id == int(plan.user_id)).first()
            if user is None:
                continue
            budget = max(0, int(plan.daily_count or 1) - _sent_today(db, plan, today))
            if budget <= 0:
                continue
            candidates = collect_candidates(db, plan)
            if not candidates:
                logger.info("[发布任务] 无未发布素材 plan=%s", plan.id)
                continue
            fired_slots = dict(plan.fired_slots or {})
            for slot in slots:
                if budget <= 0 or not candidates:
                    break
                item = await _publish_candidate(db, plan, candidates.pop(0), user)
                if item.status != "failed":
                    budget -= 1
                    fired_count += 1
                fired_slots[f"{today}|{slot}"] = True
                db.query(PublishPlan).filter(PublishPlan.id == int(plan.id)).update(
                    {"fired_slots": fired_slots, "last_run_at": datetime.utcnow()}
                )
                db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("[发布任务] tick 异常 plan=%s", getattr(plan, "id", None))
            db.rollback()
    return fired_count


async def run_plan_once(plan_id: int) -> Dict[str, Any]:
    """立即执行指定任务的一条（用于「立即执行一次」）。"""
    db = SessionLocal()
    try:
        plan = db.query(PublishPlan).filter(PublishPlan.id == int(plan_id)).first()
        if plan is None:
            return {"ok": False, "error": "发布任务不存在"}
        user = db.query(User).filter(User.id == int(plan.user_id)).first()
        if user is None:
            return {"ok": False, "error": "发布任务所属用户不存在"}
        candidates = collect_candidates(db, plan)
        if not candidates:
            return {"ok": False, "error": "没有未发布的素材了（台账里都已发过）"}
        item = await _publish_candidate(db, plan, candidates[0], user)
        db.query(PublishPlan).filter(PublishPlan.id == int(plan.id)).update({"last_run_at": datetime.utcnow()})
        db.commit()
        return {"ok": item.status != "failed", "item_id": item.id, "status": item.status,
                "title": item.title, "asset_id": item.asset_id, "task_id": item.task_id,
                "error": item.error or ""}
    finally:
        db.close()


async def publish_plan_background_loop() -> None:
    logger.info("[启动] 发布任务调度已启动（每 %ss 检查一次槽位）", _TICK_INTERVAL_SEC)
    while True:
        await asyncio.sleep(_TICK_INTERVAL_SEC)
        db = SessionLocal()
        try:
            await run_plan_tick(db)
        except Exception:  # noqa: BLE001
            logger.exception("[发布任务] 调度循环异常")
        finally:
            db.close()
