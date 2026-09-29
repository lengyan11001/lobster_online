"""微信回归测试（单独一个脚本，做 WhatsApp 的时候不要跑它）。

用法：
    .\python\python.exe scripts\round_test_wechat.py            # 被动：只扫窗口/依赖，不打扰微信
    .\python\python.exe scripts\round_test_wechat.py --deep     # 驱动级：会显示/激活微信窗口并连 wxauto4

注意：--deep 一定会把微信窗口显示出来（wxauto4 要求窗口可见），只在需要时手动跑。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_wechat_engine as wx  # noqa: E402

parser = argparse.ArgumentParser(description="微信回归测试")
parser.add_argument("--deep", action="store_true", help="深度检测（会显示/激活微信窗口并连 wxauto4）")
ARGS = parser.parse_args()

rows: list = []


def step(name, fn):
    started = time.time()
    try:
        rows.append((name, "OK", time.time() - started, fn()))
    except Exception as exc:  # noqa: BLE001
        rows.append((name, "FAIL", time.time() - started, "%s: %s" % (type(exc).__name__, exc)))


def driver():
    data = wx._local_driver_status(passive=not ARGS.deep)
    return {key: data.get(key) for key in ("ok", "driver_ready", "full_driver_ready", "count", "dependencies")}


step("WX1 驱动状态%s" % ("(deep)" if ARGS.deep else "(passive, 不打扰微信)"), driver)
step("WX2 窗口扫描(只读)", lambda: {"hwnds": [w.get("hwnd") for w in wx._scan_local_wechat_windows(max_age_seconds=0)][:3]})

lines = ["微信回归测试  %s" % time.strftime("%Y-%m-%d %H:%M:%S"), "-" * 78]
for name, state, cost, value in rows:
    lines.append("%-4s %-34s %6.1fs  %s" % (state, name, cost, json.dumps(value, ensure_ascii=False, default=str)[:150]))
ok = all(row[1] == "OK" for row in rows)
lines.append("-" * 78)
lines.append("全部通过" if ok else "有失败项")
report = "\n".join(lines)
print(report)
out_path = ROOT / "logs" / ("round_test_wechat_%s.txt" % time.strftime("%Y%m%d_%H%M%S"))
try:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print("\nsaved:", out_path)
except OSError as exc:
    print("\n保存报告失败:", exc)
raise SystemExit(0 if ok else 1)
