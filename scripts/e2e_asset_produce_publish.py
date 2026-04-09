#!/usr/bin/env python3
"""
E2E：用浏览器登录后台，在会话里发送「用素材ID xxx 速推生产视频并发布到 NICK」，
并**真正等到流程结束**，从页面上读取：助手最终回复、发布任务状态，以确认视频是否生成、是否发到抖音。
不编造结果，只输出页面上实际看到的内容。
用法:
  python scripts/e2e_asset_produce_publish.py [BASE_URL] [ASSET_ID] [ACCOUNT_NICK]
  默认: BASE_URL=http://192.168.200.57:8000  ASSET_ID=4a26b3bed21c  ACCOUNT_NICK=123
  等待最长约 8 分钟（视频生成+发布），请勿中断。
"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BROWSER_DIR = BASE_DIR / "browser_chromium"
DEFAULT_URL = "http://192.168.200.57:8000/"
DEFAULT_ASSET_ID = "4a26b3bed21c"
DEFAULT_ACCOUNT_NICK = "123"
LOGIN_EMAIL = "user@lobster.local"
LOGIN_PASSWORD = "lobster123"
# 等「正在思考」消失（即收到 done）的最长时间（秒）。视频生成约 1–3 分钟 + 发布
WAIT_DONE_TIMEOUT_SEC = 480


def main():
    base_url = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL).rstrip("/")
    if not base_url.startswith("http"):
        base_url = "http://" + base_url
    url = base_url + "/"
    asset_id = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_ASSET_ID
    account_nick = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_ACCOUNT_NICK

    message = f"用素材ID {asset_id} 调用速推生产一个视频，并发布到{account_nick}"

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

    print(f"[1] 打开后台 {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)

        # 登录
        try:
            page.wait_for_selector("#loginForm", state="visible", timeout=6000)
        except Exception:
            pass
        email_el = page.query_selector("#loginForm input[type='email'], #loginForm input[name='username']")
        if email_el:
            print("[2] 登录")
            email_el.fill(LOGIN_EMAIL)
            pw = page.query_selector("#loginForm input[type='password']")
            if pw:
                pw.fill(LOGIN_PASSWORD)
            btn = page.query_selector("#loginForm button[type='submit']")
            if btn:
                btn.click()
            else:
                page.get_by_role("button", name="登录").click()
            page.wait_for_timeout(4000)
        else:
            print("[2] 未看到登录表单（可能已登录）")

        # 智能对话
        chat_nav = page.query_selector('.nav-left-item[data-view="chat"]')
        if not chat_nav:
            print("[3] 未找到「智能对话」")
            page.wait_for_timeout(3000)
            browser.close()
            return
        if "active" not in (chat_nav.get_attribute("class") or ""):
            chat_nav.click()
            page.wait_for_timeout(1500)

        # 发送
        input_el = page.query_selector("#chatInput")
        if not input_el:
            print("[3] 未找到 #chatInput")
            browser.close()
            return
        print(f"[3] 发送: {message[:55]}...")
        input_el.fill(message)
        page.wait_for_timeout(400)
        send_btn = page.query_selector("#chatSendBtn")
        if send_btn:
            send_btn.click()
        else:
            page.keyboard.press("Enter")

        # 等待「正在思考」消失 = 流式结束（最长 WAIT_DONE_TIMEOUT_SEC）
        print(f"[4] 等待流程结束（最长 {WAIT_DONE_TIMEOUT_SEC} 秒，请勿关浏览器）…")
        try:
            page.wait_for_selector("#chatTypingIndicator", state="detached", timeout=WAIT_DONE_TIMEOUT_SEC * 1000)
        except Exception as e:
            print(f"    超时或异常: {e}")
            print("    当前页面上可能仍显示「正在思考」或步骤，请手动查看。")

        # 回复可能还在逐行打出，多等几秒再取全文
        page.wait_for_timeout(5000)

        # 从页面读取最后一条助手回复全文
        last_reply = ""
        for el in reversed(page.query_selector_all(".chat-msg.assistant")):
            if "typing" in (el.get_attribute("class") or ""):
                continue
            body = el.query_selector(".chat-msg-body")
            if body:
                last_reply = (body.inner_text() or "").strip()
            else:
                last_reply = (el.inner_text() or "").strip()
            if last_reply:
                break

        print("")
        print("========== 页面上读到的「助手最后回复」==========")
        print(last_reply if last_reply else "(未读到助手回复)")
        print("==================================================")

        # 进入发布管理 → 点「发布记录」→ 读任务列表
        publish_nav = page.query_selector('.nav-left-item[data-view="publish"]')
        if publish_nav:
            publish_nav.click()
            page.wait_for_timeout(2000)
            tab_tasks = page.query_selector('.pub-tab[data-pub-tab="tasks"]')
            if tab_tasks:
                tab_tasks.click()
                page.wait_for_timeout(3000)
            task_list_el = page.query_selector("#taskList")
            if task_list_el:
                task_text = (task_list_el.inner_text() or "").strip()
                print("")
                print("========== 发布管理 · 发布记录 (#taskList) ==========")
                print(task_text[:1500] if len(task_text) > 1500 else task_text)
                print("======================================================")
            else:
                pub = page.query_selector("#content-publish")
                print("")
                print("========== 发布管理区域 (#content-publish) ==========")
                print((pub.inner_text() or "")[:1500] if pub else "(无)")
                print("======================================================")
        else:
            print("(未找到「发布管理」入口)")

        print("")
        print("请根据上面「助手最后回复」和「发布管理」内容，自行判断：视频是否生成、是否已发到抖音。")
        print("10 秒后关闭浏览器…")
        page.wait_for_timeout(10000)
        browser.close()


if __name__ == "__main__":
    main()
