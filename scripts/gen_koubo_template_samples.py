# -*- coding: utf-8 -*-
"""Generate 480p sample clips for digital-human-koubo templates.

Usage:
  python scripts/gen_koubo_template_samples.py            # only missing
  python scripts/gen_koubo_template_samples.py --force    # regenerate all
  python scripts/gen_koubo_template_samples.py --ids a,b  # selected
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import koubo_template_engine as engine  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="", help="base final.mp4 path")
    ap.add_argument("--ids", default="", help="comma separated template ids")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    data_dir = ROOT / "data" / "koubo"
    rows = json.loads((data_dir / "projects.json").read_text(encoding="utf-8"))
    if not rows:
        raise SystemExit("no projects found in data/koubo/projects.json")
    base_row = next((r for r in rows if r.get("id") == "c59d7eed"), rows[0])
    base = Path(args.base) if args.base else data_dir / "output" / str(base_row["id"]) / "final.mp4"
    if not base.is_file():
        raise SystemExit("base video missing: %s" % base)

    templates = engine.public_templates()
    ids = [x.strip() for x in args.ids.split(",") if x.strip()] if args.ids else [t["id"] for t in templates]
    out_dir = ROOT / "static" / "koubo" / "templates" / "samples"
    out_dir.mkdir(parents=True, exist_ok=True)

    pid = "sample_" + uuid.uuid4().hex[:6]
    tmp_dir = data_dir / "output" / pid
    tmp_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(base, tmp_dir / "final.mp4")
    project = dict(base_row)
    project["id"] = pid

    done = []
    try:
        for tid in ids:
            target = out_dir / (tid + ".mp4")
            if target.is_file() and not args.force:
                print("skip %s" % tid, flush=True)
                continue
            print("render %s" % tid, flush=True)
            out = engine.render(project, tid, quality="preview")
            shutil.copyfile(out, target)
            size = target.stat().st_size
            done.append({"id": tid, "size": size})
            print("ok %s %d" % (tid, size), flush=True)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print(json.dumps({"generated": done, "count": len(done)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
