"""同城爆款「方案记录」本地账本。

每次「生成方案」写一条记录；方案里的卡片（含生成出来的场景图/视频/任务号/字幕编辑）
随用户操作持续更新，所以点开某条记录可以原样回填到界面。

- 文件：_lobster_runtime/local_bestseller_plans.json
- 保留：30 天 TTL + 最多 30 条（超出丢最旧的）
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()
_PLANS: Dict[str, Dict[str, Any]] = {}
_LOADED = False

_PLANS_FILE = Path(__file__).resolve().parents[3] / "_lobster_runtime" / "local_bestseller_plans.json"
_PLAN_TTL_SEC = 86400 * 30
_MAX_PLANS = 30


def _ensure_loaded_unlocked() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        raw = json.loads(_PLANS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    if isinstance(raw, dict):
        for pid, plan in raw.items():
            if isinstance(plan, dict):
                _PLANS[str(pid)] = plan


def _save_unlocked() -> None:
    try:
        _PLANS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PLANS_FILE.with_name(_PLANS_FILE.name + ".tmp")
        tmp.write_text(json.dumps(_PLANS, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_PLANS_FILE)
    except OSError:
        pass


def _prune_unlocked(now: float) -> None:
    dead = [
        pid
        for pid, plan in _PLANS.items()
        if now - float(plan.get("created_at_ts") or 0) > _PLAN_TTL_SEC
    ]
    for pid in dead:
        _PLANS.pop(pid, None)
    if len(_PLANS) > _MAX_PLANS:
        ordered = sorted(
            _PLANS.items(),
            key=lambda kv: float((kv[1] or {}).get("updated_at_ts") or (kv[1] or {}).get("created_at_ts") or 0),
            reverse=True,
        )
        for pid, _plan in ordered[_MAX_PLANS:]:
            _PLANS.pop(pid, None)


def _counts(items: List[Dict[str, Any]]) -> Dict[str, int]:
    scene = 0
    video = 0
    failed = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("image_url") or item.get("image_asset_id") or item.get("scene_url") or item.get("scene_asset_id"):
            scene += 1
        if item.get("video_url") or str(item.get("video_status") or "").lower() == "completed":
            video += 1
        if str(item.get("video_status") or "").lower() == "failed" or str(item.get("scene_status") or "").lower() == "failed":
            failed += 1
    return {"scene_ready": scene, "video_ready": video, "failed": failed}


def _summary(plan: Dict[str, Any]) -> Dict[str, Any]:
    plan = plan or {}
    items = plan.get("items") if isinstance(plan.get("items"), list) else []
    day_start = int(plan.get("day_start") or 0) or 1
    day_end = int(plan.get("day_end") or 0) or day_start
    return {
        "plan_id": plan.get("plan_id"),
        "title": f"{day_end - day_start + 1}天方案（Day {day_start}-{day_end}）",
        "day_start": day_start,
        "day_end": day_end,
        "card_count": len(items),
        "created_at_ts": plan.get("created_at_ts"),
        "updated_at_ts": plan.get("updated_at_ts"),
        "profile": plan.get("profile") or {},
        **_counts(items),
    }


def create_plan(
    *,
    user_id: int,
    items: List[Dict[str, Any]],
    profile: Optional[Dict[str, Any]] = None,
    day_start: int = 1,
    day_end: int = 1,
) -> str:
    pid = uuid.uuid4().hex
    now = time.time()
    with _LOCK:
        _ensure_loaded_unlocked()
        _prune_unlocked(now)
        _PLANS[pid] = {
            "plan_id": pid,
            "user_id": int(user_id),
            "items": list(items or []),
            "profile": dict(profile or {}),
            "day_start": int(day_start or 1),
            "day_end": int(day_end or day_start or 1),
            "created_at_ts": now,
            "updated_at_ts": now,
        }
        _save_unlocked()
    return pid


def update_plan(
    plan_id: str,
    *,
    user_id: int,
    items: Optional[List[Dict[str, Any]]] = None,
    profile: Optional[Dict[str, Any]] = None,
    day_start: Optional[int] = None,
    day_end: Optional[int] = None,
) -> bool:
    pid = (plan_id or "").strip().lower()
    if not pid:
        return False
    with _LOCK:
        _ensure_loaded_unlocked()
        plan = _PLANS.get(pid)
        if not plan or int(plan.get("user_id") or -1) != int(user_id):
            return False
        if items is not None:
            plan["items"] = list(items)
        if isinstance(profile, dict) and profile:
            plan["profile"] = profile
        if day_start is not None:
            plan["day_start"] = int(day_start)
        if day_end is not None:
            plan["day_end"] = int(day_end)
        plan["updated_at_ts"] = time.time()
        _save_unlocked()
        return True


def list_plans(user_id: int, *, limit: int = 30) -> List[Dict[str, Any]]:
    now = time.time()
    with _LOCK:
        _ensure_loaded_unlocked()
        _prune_unlocked(now)
        rows = [
            _summary(plan)
            for plan in _PLANS.values()
            if int(plan.get("user_id") or -1) == int(user_id)
        ]
        rows.sort(key=lambda row: float(row.get("updated_at_ts") or row.get("created_at_ts") or 0), reverse=True)
        _save_unlocked()
        return rows[: max(1, int(limit or 30))]


def get_plan(plan_id: str, *, user_id: int) -> Optional[Dict[str, Any]]:
    pid = (plan_id or "").strip().lower()
    if not pid:
        return None
    with _LOCK:
        _ensure_loaded_unlocked()
        plan = _PLANS.get(pid)
        if not plan or int(plan.get("user_id") or -1) != int(user_id):
            return None
        return json.loads(json.dumps(plan, ensure_ascii=False))


def delete_plan(plan_id: str, *, user_id: int) -> bool:
    pid = (plan_id or "").strip().lower()
    if not pid:
        return False
    with _LOCK:
        _ensure_loaded_unlocked()
        plan = _PLANS.get(pid)
        if not plan or int(plan.get("user_id") or -1) != int(user_id):
            return False
        _PLANS.pop(pid, None)
        _save_unlocked()
        return True
