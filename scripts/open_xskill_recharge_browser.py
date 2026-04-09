#!/usr/bin/env python3
"""启动浏览器打开速推，等待用户扫码登录后进入充值页并记录 xskill API 请求。"""
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT_DIR = Path(__file__).resolve().parent.parent
CAPTURE_FILE = OUT_DIR / "xskill_recharge_captured_requests.jsonl"


def is_xskill_api(url: str) -> bool:
    return "xskill" in url and ("api" in url or "recharge" in url or "pay" in url or "order" in url or "balance" in url)


def main():
    captured = []
    if CAPTURE_FILE.exists():
        CAPTURE_FILE.write_text("")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel=None)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        def on_request(req):
            if not is_xskill_api(req.url):
                return
            entry = {"url": req.url, "method": req.method}
            try:
                if req.post_data:
                    entry["post_data"] = req.post_data
            except Exception:
                pass
            captured.append(entry)
            print(f"[请求] {req.method} {req.url}")

        def on_response(resp):
            if not is_xskill_api(resp.url):
                return
            try:
                body = resp.text()
                if body and body.strip().startswith("{"):
                    with open(CAPTURE_FILE, "a", encoding="utf-8") as f:
                        f.write(json.dumps({
                            "url": resp.url,
                            "status": resp.status,
                            "method": resp.request.method,
                            "body": body[:3000] if len(body) > 3000 else body,
                        }, ensure_ascii=False) + "\n")
            except Exception:
                pass
            print(f"[响应] {resp.status} {resp.url}")

        page.on("request", on_request)
        page.on("response", on_response)

        start_url = "https://www.xskill.ai/#/login?redirect=%2F&wechat=1"
        print("正在打开", start_url)
        page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
        print("请在浏览器中完成扫码登录。登录成功后脚本会在 90 秒后自动打开充值页。")
        print("等待 90 秒…")
        time.sleep(90)

        print("正在打开充值页…")
        try:
            page.goto("https://www.xskill.ai/#/cn-recharge", wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            print("打开充值页:", e)
        print("在充值页等待 45 秒，请选择金额/支付方式或点击充值（会记录 API）。")
        time.sleep(45)
        browser.close()

    out = OUT_DIR / "xskill_captured_requests.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(captured, f, indent=2, ensure_ascii=False)
    print(f"已保存 {len(captured)} 个 xskill API 请求到 {out}")


if __name__ == "__main__":
    main()
