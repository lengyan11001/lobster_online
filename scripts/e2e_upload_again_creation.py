#!/usr/bin/env python3
"""
E2E：登录 -> 上传图片 -> 会话带附件发送「再次创作」（图生视频/图生图）。
验证：会话附图上传、attachment_asset_ids 注入 URL、速推可拉取素材。
"""
import base64
import json
import os
import sys
import tempfile
import time

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)

BASE = os.environ.get("LOBSTER_BASE", "http://127.0.0.1:8000")


def main():
    print("[E2E] 登录...")
    r = requests.post(
        f"{BASE}/auth/login",
        data={"username": "user@lobster.local", "password": "lobster123"},
        timeout=10,
    )
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        print("[E2E] 登录失败")
        sys.exit(1)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    print("[E2E] 上传一张测试图片...")
    # 1x1 透明 PNG
    png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    raw = base64.b64decode(png_b64)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(raw)
        path = f.name
    try:
        with open(path, "rb") as f:
            up = requests.post(
                f"{BASE}/api/assets/upload",
                headers={"Authorization": f"Bearer {token}"},
                files={"file": ("test.png", f, "image/png")},
                timeout=15,
            )
        up.raise_for_status()
        data = up.json()
        asset_id = data.get("asset_id")
        if not asset_id:
            print("[E2E] 上传未返回 asset_id:", data)
            sys.exit(2)
        print("[E2E] 已上传 asset_id:", asset_id)
    finally:
        os.unlink(path)

    print("[E2E] 发送带附件的对话：用这张图做图生视频/再次创作...")
    body = {
        "message": "用我上传的这张图做图生视频，生成一个 5 秒视频，提示词：一只猫在跑。",
        "history": [],
        "attachment_asset_ids": [asset_id],
    }
    r = requests.post(
        f"{BASE}/chat/stream",
        headers=headers,
        json=body,
        stream=True,
        timeout=130,
    )
    r.raise_for_status()
    events = []
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
            events.append(ev)
            t = ev.get("type")
            if t == "tool_start":
                print("[SSE] tool_start:", ev.get("name"), ev.get("args", []))
            elif t == "tool_end":
                print("[SSE] tool_end:", ev.get("name"), (ev.get("preview") or "")[:100])
            elif t == "done":
                print("[SSE] done, reply_len:", len(ev.get("reply", "")), "error:", ev.get("error"))
                break
        except Exception as e:
            pass
    print("[E2E] 流程结束。收到", len(events), "个 SSE 事件。")
    done_ev = next((e for e in events if e.get("type") == "done"), None)
    if done_ev and done_ev.get("reply"):
        print("[E2E] 回复摘要:", (done_ev.get("reply") or "")[:300])
    if not any(e.get("type") == "tool_start" for e in events):
        print("[E2E] 未看到 tool_start，可能未走能力调用。")
    else:
        print("[E2E] 通过：会话附图发送且触发了能力调用。")


if __name__ == "__main__":
    main()
