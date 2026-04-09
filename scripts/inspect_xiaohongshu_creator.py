#!/usr/bin/env python3
"""启动可见浏览器打开小红书创作者平台，等你扫码登录后，探索发布流程并导出页面控件信息。

用法（在 lobster 项目根目录）:
  python3 scripts/inspect_xiaohongshu_creator.py

  只采集「图文」和「长文」两页（跳过视频等）:
  INSPECT_XHS_TARGETS=image,article python3 scripts/inspect_xiaohongshu_creator.py

1. 会打开 Chromium，访问 https://creator.xiaohongshu.com
2. 请在浏览器中完成扫码登录
3. 登录后在终端按 Enter，脚本会按顺序访问各发布页并采集控件，写入 scripts/xiaohongshu_creator_controls.json

图文/长文页会额外模拟「上传一张测试图」，等待编辑界面出现后再采一次，得到「上传后编辑界面」的按钮/输入框（标题、发布等）。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# 项目根
ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from playwright_lobster_env import ensure_playwright_browsers_path

OUTPUT_JSON = ROOT / "scripts" / "xiaohongshu_creator_controls.json"
CREATOR_BASE = "https://creator.xiaohongshu.com"
# 用于「上传后编辑界面」采集的 1x1 测试图（最小 PNG）
_TINY_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def _get_tiny_image_path() -> Path:
    p = ROOT / "scripts" / "_inspect_tiny.png"
    if not p.exists() or p.stat().st_size == 0:
        import base64
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(base64.b64decode(_TINY_PNG_B64))
    return p


def _blocking_input(msg: str) -> str:
    return input(msg)


async def _wait_login_enter():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: _blocking_input("请在浏览器中完成扫码登录，登录成功后在本终端按 Enter 继续... "),
    )


def _js_collect_controls():
    """在页面内执行：收集按钮、链接、输入框、上传区域等，便于后续写选择器。"""
    return """
    () => {
        const out = {
            url: window.location.href,
            title: document.title,
            buttons: [],
            links: [],
            inputs: [],
            fileInputs: [],
            textareas: [],
            byRole: [],
        };
        function safeText(el, max) {
            const t = (el.textContent || el.value || '').trim();
            return t.length > (max || 80) ? t.substring(0, max) + '...' : t;
        }
        function attr(el, a) { try { return el.getAttribute(a) || ''; } catch(e) { return ''; } }
        document.querySelectorAll('button, [role="button"], input[type="submit"]').forEach((el, i) => {
            const text = safeText(el, 50);
            if (!text && el.type === 'submit') return;
            out.buttons.push({
                index: i,
                tag: el.tagName,
                text: text,
                id: attr(el, 'id'),
                className: (el.className || '').toString().substring(0, 120),
                placeholder: attr(el, 'placeholder'),
                ariaLabel: attr(el, 'aria-label'),
                name: attr(el, 'name'),
            });
        });
        document.querySelectorAll('a[href]').forEach((el, i) => {
            const text = safeText(el, 40);
            const href = attr(el, 'href') || '';
            if (href.startsWith('javascript:') && !text) return;
            out.links.push({
                index: i,
                text: text,
                href: href.substring(0, 200),
                id: attr(el, 'id'),
                className: (el.className || '').toString().substring(0, 80),
            });
        });
        document.querySelectorAll('input:not([type="hidden"])').forEach((el, i) => {
            out.inputs.push({
                index: i,
                type: attr(el, 'type') || 'text',
                id: attr(el, 'id'),
                name: attr(el, 'name'),
                placeholder: attr(el, 'placeholder'),
                className: (el.className || '').toString().substring(0, 80),
            });
        });
        document.querySelectorAll('input[type="file"]').forEach((el, i) => {
            out.fileInputs.push({
                index: i,
                id: attr(el, 'id'),
                name: attr(el, 'name'),
                accept: attr(el, 'accept'),
                className: (el.className || '').toString().substring(0, 80),
            });
        });
        document.querySelectorAll('textarea').forEach((el, i) => {
            out.textareas.push({
                index: i,
                id: attr(el, 'id'),
                name: attr(el, 'name'),
                placeholder: attr(el, 'placeholder'),
                className: (el.className || '').toString().substring(0, 80),
            });
        });
        return out;
    }
    """


async def main():
    ensure_playwright_browsers_path(ROOT)
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("请先安装: pip install playwright && python -m playwright install chromium")
        return 2

    print("启动 Chromium（可见窗口）...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
        )
        page = await context.new_page()
        await page.goto(CREATOR_BASE, wait_until="domcontentloaded", timeout=30000)
        print("已打开:", CREATOR_BASE)
        import os
        auto_wait = int(os.environ.get("AUTO_WAIT", "0"))
        if auto_wait > 0:
            print("等待 %d 秒：请在此时间内完成扫码登录，并进入要采集的页面（例如点「上传视频」后进入填写标题/正文的页面）。" % auto_wait)
            await asyncio.sleep(auto_wait)
        else:
            await _wait_login_enter()

        # 当前页先收集一次（可能是首页）
        snapshots = []
        try:
            data = await page.evaluate(_js_collect_controls())
            data["note"] = "登录后首页/当前页"
            snapshots.append(data)
            print("已采集当前页控件，URL:", data.get("url"))
        except Exception as e:
            print("采集当前页失败:", e)

        # 尝试通过链接进入「发布」相关页面：常见文案
        publish_keywords = ["发布", "创作", "写笔记", "发笔记", "上传", "内容管理", "创作中心"]
        for kw in publish_keywords:
            try:
                loc = page.get_by_role("link", name=kw)
                if await loc.count() > 0:
                    await loc.first.click()
                    await asyncio.sleep(2)
                    data = await page.evaluate(_js_collect_controls())
                    data["note"] = f"点击「{kw}」后页面"
                    snapshots.append(data)
                    print("已进入并采集:", data.get("url"))
                    break
            except Exception as e:
                print("尝试点击「%s」失败:" % kw, e)

        # 访问已知有效路径，采集视频页、图文页、长文页等控件（避免 /creator/post 等会触发「页面不见了」）
        all_paths = [
            ("/publish/publish?from=menu&target=video", "发布视频页 target=video"),
            ("/publish/publish?from=menu&target=image", "发布图文页 target=image"),
            ("/publish/publish?from=menu&target=article", "发布长文页 target=article"),
            ("/publish/publish", "发布页（无 target）"),
            ("/publish", "发布列表页"),
        ]
        env_targets = (os.environ.get("INSPECT_XHS_TARGETS") or "").strip().lower()
        if env_targets:
            want = set(x.strip() for x in env_targets.split(",") if x.strip())
            paths_with_notes = [(p, n) for p, n in all_paths if any(t in p for t in want)]
            print("仅采集: %s" % [n for _, n in paths_with_notes])
        else:
            paths_with_notes = all_paths
        tiny_image = _get_tiny_image_path()

        async def _query_file_input(pg):
            el = await pg.query_selector("input.upload-input")
            if el:
                return el
            el = await pg.query_selector('input[type="file"]')
            if el:
                return el
            for fr in getattr(pg, "frames", []):
                try:
                    el = await fr.query_selector("input.upload-input")
                    if el:
                        return el
                    el = await fr.query_selector('input[type="file"]')
                    if el:
                        return el
                except Exception:
                    continue
            return None

        for path, note in paths_with_notes:
            try:
                url = CREATOR_BASE.rstrip("/") + path
                await page.goto(url, wait_until="domcontentloaded", timeout=10000)
                await asyncio.sleep(2)
                data = await page.evaluate(_js_collect_controls())
                if (data.get("title") or "").strip() == "你访问的页面不见了":
                    print("跳过（页面不见了）:", url)
                    continue
                data["note"] = note
                snapshots.append(data)
                print("已采集:", note, "->", data.get("url"))

                # 图文/长文页：模拟上传一张图，再采集「上传后编辑界面」
                if "target=image" in path or "target=article" in path:
                    file_input = await _query_file_input(page)
                    if file_input:
                        try:
                            await file_input.set_input_files(str(tiny_image))
                            print("  已上传测试图，等待编辑界面…")
                            for _ in range(25):
                                await asyncio.sleep(1)
                                data2 = await page.evaluate(_js_collect_controls())
                                if (data2.get("title") or "").strip() == "你访问的页面不见了":
                                    break
                                # 出现标题框或发布按钮视为进入编辑页
                                inputs = data2.get("inputs") or []
                                buttons = data2.get("buttons") or []
                                has_title = any(
                                    (inp.get("placeholder") or "").find("标题") >= 0
                                    for inp in inputs
                                )
                                has_publish = any(
                                    (b.get("text") or "").strip() == "发布"
                                    for b in buttons
                                )
                                if has_title or has_publish:
                                    data2["note"] = note + "（上传后编辑界面）"
                                    snapshots.append(data2)
                                    print("  已采集:", data2["note"])
                                    break
                            else:
                                print("  未检测到编辑界面，跳过")
                        except Exception as e:
                            print("  上传后采集失败:", e)
                    else:
                        print("  未找到文件输入框，跳过编辑界面采集")
            except Exception as e:
                print("采集失败 %s: %s" % (path, e))

        # 写回 JSON
        OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
            json.dump({"snapshots": snapshots, "base_url": CREATOR_BASE}, f, ensure_ascii=False, indent=2)
        print("已写入:", OUTPUT_JSON)

        await asyncio.sleep(1)
        await browser.close()
    return 0


if __name__ == "__main__":
    exit(asyncio.run(main()))
