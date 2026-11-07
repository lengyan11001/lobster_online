#!/usr/bin/env python3
"""把 CutCLI 模板字体从 TOS 补到 static/fonts。

字体已从 OTA 主包移出（压缩后约 20.9MB），改由
backend.app.services.runtime_assets 在客户端启动时后台补拉。
这个脚本用于打包机 / 干净 checkout 手动预热，逻辑只有一份，避免两份清单漂移。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_runtime_assets():
    path = ROOT / "backend" / "app" / "services" / "runtime_assets.py"
    spec = importlib.util.spec_from_file_location("lobster_runtime_assets", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    mod = _load_runtime_assets()
    pending = mod.missing_assets(ROOT)
    if not pending:
        print("ok: all runtime fonts present")
        return 0
    print(f"missing {len(pending)} font(s), fetching from TOS ...")
    mod.ensure_assets(ROOT, log=print)
    remaining = mod.missing_assets(ROOT)
    if remaining:
        print(f"[ERR] still missing: {[a['rel'] for a in remaining]}")
        return 1
    print("ok: runtime fonts ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
