#!/usr/bin/env python3
"""用内置浏览器访问龙虾页面并验证：登录、打开「日志」Tab、检查是否加载成功。
在本地或与 192.168.200.57 同网的机器上运行，可访问内网地址。
用法: python scripts/verify_lobster_browser.py [BASE_URL]
默认 BASE_URL=http://192.168.200.57:8000
"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BROWSER_DIR = BASE_DIR / "browser_chromium"
DEFAULT_URL = "http://192.168.200.57:8000/"
# 默认登录（与 backend config 一致）
DEFAULT_EMAIL = "user@lobster.local"
DEFAULT_PASSWORD = "lobster123"


def main():
    base_url = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL).rstrip("/")
    if not base_url.startswith("http"):
        base_url = "http://" + base_url
    url = base_url + "/"

    # 若本机有可用的 browser_chromium 则优先用（离线包）；否则用 playwright 默认
    exe = BROWSER_DIR / "chromium-1208/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    if not exe.exists():
        exe = next((f for f in BROWSER_DIR.rglob("Google Chrome for Testing") if f.is_file()), None)
    if exe and exe.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSER_DIR)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("请先安装: pip install playwright && playwright install chromium")
        sys.exit(1)

    print(f"[1] 打开 {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(1500)

        # 若有登录表单则登录（#loginForm 为单机版登录）
        try:
            page.wait_for_selector("#loginForm", state="visible", timeout=5000)
        except Exception:
            pass
        email_el = page.query_selector("#loginForm input[type='email'], #loginForm input[name='username']")
        if email_el:
            print("[2] 填写登录并提交")
            email_el.fill(DEFAULT_EMAIL)
            pw = page.query_selector("#loginForm input[type='password']")
            if pw:
                pw.fill(DEFAULT_PASSWORD)
            btn = page.query_selector("#loginForm button[type='submit']")
            if btn:
                btn.click()
            else:
                page.get_by_role("button", name="登录").click()
            page.wait_for_timeout(3000)
        else:
            print("[2] 未看到登录表单（可能已登录或为在线版）")

        # 点击「日志」
        logs_nav = page.query_selector('.nav-left-item[data-view="logs"], a:has-text("日志"), div:has-text("日志")')
        if logs_nav:
            logs_nav.click()
            page.wait_for_timeout(25000)
            pre = page.query_selector("#logsContent")
            if pre:
                text = pre.inner_text()
                if "加载失败" in text or "加载超时" in text:
                    print("[3] 日志 Tab 加载异常:", text[:200])
                elif "加载中" in text and len(text.strip()) < 50:
                    print("[3] 日志仍显示加载中，可能请求未返回")
                else:
                    print("[3] 日志已加载，行数约:", len([l for l in text.splitlines() if l.strip()]))
            else:
                print("[3] 未找到 #logsContent")
        else:
            print("[2] 未找到「日志」入口，请确认已登录")

        print("5 秒后自动关闭浏览器…")
        page.wait_for_timeout(5000)
        browser.close()


if __name__ == "__main__":
    main()
