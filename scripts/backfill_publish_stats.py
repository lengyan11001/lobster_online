# -*- coding: utf-8 -*-
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from backend.app.db import SessionLocal
from backend.app.models import Asset, PublishTask, PublishAccount

db = SessionLocal()
try:
    tasks = db.query(PublishTask).filter(PublishTask.status == "success").all()
    stats, last, plat_cache = {}, {}, {}
    for t in tasks:
        meta = t.meta if isinstance(t.meta, dict) else {}
        dr = meta.get("driver_result") if isinstance(meta.get("driver_result"), dict) else {}
        if dr.get("dry_run"):
            continue
        if t.account_id not in plat_cache:
            a = db.query(PublishAccount).filter(PublishAccount.id == t.account_id).first()
            plat = (a.platform if a else "") or ""
            if not plat and int(t.account_id) <= -1000:
                plat = "douyin"   # 抖音 origin 虚拟槽位（-1001/-1002…）
            plat_cache[t.account_id] = plat
        plat = plat_cache[t.account_id]
        if not plat or not t.asset_id:
            continue
        stats.setdefault(t.asset_id, {})
        stats[t.asset_id][plat] = stats[t.asset_id].get(plat, 0) + 1
        ts = t.finished_at or t.created_at
        iso = ts.isoformat(timespec="seconds") if ts else ""
        if t.asset_id not in last or iso > last[t.asset_id][0]:
            last[t.asset_id] = (iso, plat)

    updated = 0
    for aid, st in stats.items():
        row = db.query(Asset).filter(Asset.asset_id == aid).first()
        if not row:
            continue
        meta = dict(row.meta or {}) if isinstance(row.meta, dict) else {}
        meta["publish_stats"] = st
        if aid in last:
            meta["last_publish_at"] = last[aid][0]
            meta["last_publish_platform"] = last[aid][1]
        row.meta = meta
        updated += 1
    db.commit()
    print("assets updated:", updated)
    t = db.query(PublishTask).filter(PublishTask.id == 121).first()
    row = db.query(Asset).filter(Asset.asset_id == t.asset_id).first()
    print("task121 ->", t.asset_id, (row.meta or {}).get("publish_stats"), (row.meta or {}).get("last_publish_platform"))
finally:
    db.close()