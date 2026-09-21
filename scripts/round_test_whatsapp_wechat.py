"""每轮回归测试：WhatsApp 一轮 + 微信一轮（两边都跑，微信侧只读）。

用法：
    .\python\python.exe scripts\round_test_whatsapp_wechat.py      # 客户端自带运行时
    python scripts\round_test_whatsapp_wechat.py                    # 开发机

说明：WhatsApp 侧只做"打开表单 + dry-run 按钮探测"，不点保存、不加好友；
微信侧只读驱动状态与缓存统计，不点界面、不发消息。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
import time

parser = argparse.ArgumentParser(description="每轮回归测试：WhatsApp + 微信")
parser.add_argument("--deep", action="store_true",
                    help="微信做深度检测（会显示/激活微信窗口、连 wxauto4）；默认只做被动检测，不打扰正在使用的微信")
ARGS = parser.parse_args()

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_whatsapp_engine as wa  # noqa: E402
from backend.app.services import native_wechat_engine as wx  # noqa: E402

rows: list = []


def step(name, fn):
    started = time.time()
    try:
        rows.append((name, "OK", time.time() - started, fn()))
    except Exception as exc:  # noqa: BLE001
        rows.append((name, "FAIL", time.time() - started, "%s: %s" % (type(exc).__name__, exc)))


def _count_by_status(db_path, table, column):
    if not db_path.is_file():
        return {}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return {row[column]: row["c"] for row in conn.execute(
            "select %s, count(*) c from %s group by %s" % (column, table, column))}
    finally:
        conn.close()


def wa_status():
    data = wa.status()
    return {
        "ok": data.get("ok"),
        "desktop_found": data.get("desktop_found"),
        "logged_in": data.get("logged_in"),
        "capabilities": data.get("capabilities"),
        "dependencies": data.get("dependencies"),
    }


def wa_form_probe():
    hwnd, _window = wa._window_or_raise()
    wa._open_new_chat_page(hwnd)
    root = wa._root_for_hwnd(hwnd)
    add_buttons = [
        node for node, _depth in wa._iter_nodes(root, max_depth=20, max_nodes=wa.CONTACT_TREE_NODES)
        if wa._node_type(node) == "ButtonControl"
        and wa._node_text(node).strip().casefold() in {"添加联系人", "add contact"} and wa._rect(node)
    ]
    if not add_buttons:
        raise RuntimeError("新聊天页没有出现「添加联系人」入口")
    wa._click(add_buttons[-1])
    time.sleep(0.7)
    root = wa._root_for_hwnd(hwnd)
    fields = wa._collect_contact_form_fields(root)
    probe = {
        "fields": sorted(fields),
        "save_button_visible": wa._click_button_matching(root, ("保存", "save"), dry_run=True),
        "view_contact_visible": wa._click_button_matching(root, ("查看联系人", "view contact"), dry_run=True),
    }
    wa._dismiss_contact_form(hwnd)
    return probe


def wechat_driver():
    # 默认被动：只扫窗口 + 依赖，不激活微信窗口、不连 wxauto4（别打扰正在用的微信）
    # --deep 时才连 wxauto4 做驱动可用性探测（会显示/激活微信窗口）
    data = wx._local_driver_status(passive=not ARGS.deep)
    return {key: data.get(key) for key in ("ok", "driver_ready", "full_driver_ready", "count")}


# ---- WhatsApp 一轮 ----
step("WA1 状态与依赖", wa_status)
step("WA2 进程识别", lambda: {"processes": [p.get("name") for p in wa.whatsapp_processes()]})
step("WA3 加好友记录统计", lambda: _count_by_status(ROOT / "data" / "native_whatsapp" / "state.db",
                                                     "whatsapp_tasks", "status"))
step("WA4 表单与保存按钮(dry-run)", wa_form_probe)

# ---- 微信一轮 ----
step("WX1 驱动状态%s" % ("(deep)" if ARGS.deep else "(passive, 不打扰微信)"), wechat_driver)
step("WX2 依赖", lambda: wx._local_driver_status(passive=True).get("dependencies"))
step("WX3 窗口可见性(只读)", lambda: {"hwnds": [w.get("hwnd") for w in wx._scan_local_wechat_windows(max_age_seconds=0)][:3]})

lines = ["轮次测试结果  %s" % time.strftime("%Y-%m-%d %H:%M:%S"), "-" * 78]
for name, state, cost, value in rows:
    lines.append("%-4s %-30s %6.1fs  %s" % (state, name, cost, json.dumps(value, ensure_ascii=False, default=str)[:160]))
ok = all(row[1] == "OK" for row in rows)
lines.append("-" * 78)
lines.append("全部通过" if ok else "有失败项")
report = "\n".join(lines)
print(report)
out_path = ROOT / "logs" / ("round_test_%s.txt" % time.strftime("%Y%m%d_%H%M%S"))
try:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print("\nsaved:", out_path)
except OSError as exc:
    print("\n保存报告失败:", exc)
raise SystemExit(0 if ok else 1)
