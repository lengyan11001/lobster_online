"""WhatsApp 回归测试（就测 WhatsApp，不碰微信）。

用法：
    .\python\python.exe scripts\round_test_whatsapp.py

只做只读/无副作用的检查：状态与依赖、进程识别、记录统计、加好友表单与按钮 dry-run 探测。
不点保存、不加好友、不发消息、不动微信。
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_whatsapp_engine as wa  # noqa: E402

rows: list = []


def step(name, fn):
    started = time.time()
    try:
        rows.append((name, "OK", time.time() - started, fn()))
    except Exception as exc:  # noqa: BLE001
        rows.append((name, "FAIL", time.time() - started, "%s: %s" % (type(exc).__name__, exc)))


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


def wa_task_counts():
    db = ROOT / "data" / "native_whatsapp" / "state.db"
    if not db.is_file():
        return {}
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return {row["status"]: row["c"] for row in conn.execute(
            "select status, count(*) c from whatsapp_tasks group by status")}
    finally:
        conn.close()


def wa_template():
    result = wa.write_friend_template()
    return {"path": result["path"], "exists": pathlib.Path(result["path"]).is_file()}


step("WA1 状态与依赖", wa_status)
step("WA2 进程识别", lambda: {"processes": [p.get("name") for p in wa.whatsapp_processes()]})
step("WA3 加好友记录统计", wa_task_counts)
step("WA4 表单与保存按钮(dry-run)", wa_form_probe)
step("WA5 TXT 模板落盘", wa_template)

lines = ["WhatsApp 回归测试  %s" % time.strftime("%Y-%m-%d %H:%M:%S"), "-" * 78]
for name, state, cost, value in rows:
    lines.append("%-4s %-28s %6.1fs  %s" % (state, name, cost, json.dumps(value, ensure_ascii=False, default=str)[:150]))
ok = all(row[1] == "OK" for row in rows)
lines.append("-" * 78)
lines.append("全部通过" if ok else "有失败项")
report = "\n".join(lines)
print(report)
out_path = ROOT / "logs" / ("round_test_whatsapp_%s.txt" % time.strftime("%Y%m%d_%H%M%S"))
try:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print("\nsaved:", out_path)
except OSError as exc:
    print("\n保存报告失败:", exc)
raise SystemExit(0 if ok else 1)
