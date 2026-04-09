#!/usr/bin/env python3
"""
三平台发布控件探测（本机）：开一个可见 Chromium + 持久化 Profile，你依次登录
抖音 / 头条号 / 小红书后，脚本自动跳转发布相关页并落盘 HTML、控件清单、全页截图。

用法（必须在能弹浏览器、终端能回车的环境执行，勿纯后台）:
  cd lobster_online
  python3 scripts/probe_publish_three_platforms.py

仅抖音、默认每阶段给你 8 分钟登录+浏览:
  python3 scripts/probe_publish_three_platforms.py --only douyin --phase-wait 480

环境变量（可选）:
  PLAYWRIGHT_CHROMIUM_PATH  指定 Chromium 可执行文件
  PROBE_PHASE_WAIT         覆盖每阶段等待秒数（默认 420）

输出目录:
  scripts/_probe_three_out/<时间戳>/
    douyin_*.{html,json,png}
    toutiao_*.{html,json,png}
    xhs_*.{html,json,png}
  另写一份汇总: scripts/_probe_three_out/LATEST 符号链或 latest.json 路径 —— 简化：打印最终目录路径即可

与正式发布的 user-data 分离，避免污染账号目录；需与真号一致可自行把本目录 cookies 或整目录拷贝到 browser_data/douyin_* （不推荐，手动操作为准）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, List

ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_SCRIPTS_DIR))

from playwright_lobster_env import ensure_playwright_browsers_path

PROFILE = ROOT / "browser_data" / "probe_three_publish"
OUT_BASE = _SCRIPTS_DIR / "_probe_three_out"

# 与现有 driver / inspect 脚本对齐的 URL
DOUYIN_HOME = "https://creator.douyin.com/creator-micro/home"
DOUYIN_UPLOAD = "https://creator.douyin.com/creator-micro/content/upload"
DOUYIN_POST_VIDEO = "https://creator.douyin.com/creator-micro/content/post/video?enter_from=publish_page"

TOUTIAO_LOGIN = "https://mp.toutiao.com/login/"
TOUTIAO_STOPS: List[tuple[str, str]] = [
    ("toutiao_graphic_publish", "https://mp.toutiao.com/profile_v4/graphic/publish"),
    ("toutiao_xigua_upload", "https://mp.toutiao.com/profile_v4/xigua/upload-video"),
    ("toutiao_xigua_publish", "https://mp.toutiao.com/profile_v4/xigua/publish"),
]

XHS_BASE = "https://creator.xiaohongshu.com"
XHS_PATHS: List[tuple[str, str]] = [
    ("/publish/publish?from=menu&target=video", "xhs_video"),
    ("/publish/publish?from=menu&target=image", "xhs_image"),
    ("/publish/publish?from=menu&target=article", "xhs_article"),
]

# 与 inspect_toutiao_mp_home 一致：可交互控件扫描（略缩短 slice）
_CONTROLS_EVAL_JS = """
() => {
  const tags = ['INPUT', 'TEXTAREA', 'BUTTON', 'A', 'SELECT'];
  const roleBtns = Array.from(document.querySelectorAll('[role="button"]'));
  const roleInputs = Array.from(document.querySelectorAll(
    '[role="textbox"], [role="combobox"], [role="searchbox"], [role="listbox"]'
  ));
  const editables = Array.from(document.querySelectorAll('[contenteditable="true"]'));
  const set = new Set();
  const out = [];
  function add(el) {
    if (!el || set.has(el)) return;
    set.add(el);
    try {
      const r = el.getBoundingClientRect();
      const visible = r.width > 0 && r.height > 0;
      const tn = el.tagName;
      const ce = el.isContentEditable || el.getAttribute('contenteditable') === 'true';
      out.push({
        tag: ce ? 'CONTENTEDITABLE' : tn,
        type: el.type || '',
        placeholder: el.getAttribute('placeholder') || '',
        id: el.id || '',
        className: (typeof el.className === 'string' ? el.className : '').slice(0, 200),
        name: el.name || '',
        innerText: (el.innerText || '').trim().slice(0, 120),
        ariaLabel: el.getAttribute('aria-label') || '',
        dataTestid: el.getAttribute('data-testid') || '',
        href: (tn === 'A' ? (el.getAttribute('href') || '') : '').slice(0, 200),
        visible: visible,
        role: el.getAttribute('role') || '',
        contentEditable: !!ce,
      });
    } catch (e) {}
  }
  tags.forEach(t => document.querySelectorAll(t.toLowerCase()).forEach(add));
  roleBtns.forEach(add);
  roleInputs.forEach(add);
  editables.forEach(add);
  return { url: location.href, title: document.title, controls: out };
}
"""


def _chromium_path() -> str:
    return os.environ.get("PLAYWRIGHT_CHROMIUM_PATH", "").strip()


async def _dump_page(page: Any, out_dir: Path, tag: str) -> None:
    await asyncio.sleep(1.2)
    try:
        html = await page.content()
        (out_dir / f"{tag}.html").write_text(html, encoding="utf-8", errors="replace")
    except Exception as e:
        (out_dir / f"{tag}_html_error.txt").write_text(str(e), encoding="utf-8")
    try:
        data = await page.evaluate(_CONTROLS_EVAL_JS)
        (out_dir / f"{tag}_controls.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        (out_dir / f"{tag}_controls_error.txt").write_text(str(e), encoding="utf-8")
    try:
        await page.screenshot(path=str(out_dir / f"{tag}.png"), full_page=True)
    except Exception:
        pass


async def _phase_countdown(seconds: int, label: str) -> None:
    env = int(os.environ.get("PROBE_PHASE_WAIT", "0") or 0)
    sec = max(30, env or seconds)
    print(
        f"\n—— {label} —— 最长等待 {sec} 秒（登录并打开需要抓的页），按 Ctrl+C 可提前结束本阶段倒计时。\n",
        flush=True,
    )
    for i in range(sec):
        if i > 0 and i % 60 == 0:
            print(f"  … {label} 剩余约 {sec - i} 秒", flush=True)
        await asyncio.sleep(1)


async def run_douyin(ctx: Any, out_dir: Path, phase_wait: int) -> None:
    page = await ctx.new_page()
    await page.goto(DOUYIN_HOME, wait_until="domcontentloaded", timeout=120000)
    print("\n[抖音] 已在当前标签打开创作者首页。请登录（若未登录）。", flush=True)
    await _phase_countdown(phase_wait, "抖音：登录")
    try:
        from skills.douyin_publish.driver import _dismiss_overlays

        await _dismiss_overlays(page, "probe_three")
    except Exception:
        pass
    await page.goto(DOUYIN_UPLOAD, wait_until="domcontentloaded", timeout=120000)
    await _dump_page(page, out_dir, "douyin_upload")
    await page.goto(DOUYIN_POST_VIDEO, wait_until="domcontentloaded", timeout=120000)
    await _dump_page(page, out_dir, "douyin_post_video")
    await page.close()


async def run_toutiao(ctx: Any, out_dir: Path, phase_wait: int) -> None:
    page = await ctx.new_page()
    await page.goto(TOUTIAO_LOGIN, wait_until="domcontentloaded", timeout=120000)
    print("\n[头条号] 已在当前标签打开 mp.toutiao.com 登录页。请完成登录。", flush=True)
    await _phase_countdown(phase_wait, "头条号：登录")
    for tag, url in TOUTIAO_STOPS:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=120000)
            if "xigua/upload" in url:
                for name in ("暂不开通", "暂不通"):
                    try:
                        loc = page.get_by_role("button", name=name)
                        if await loc.count() > 0 and await loc.first.is_visible():
                            await loc.first.click(timeout=5000)
                            await asyncio.sleep(1.0)
                            break
                    except Exception:
                        pass
            await _dump_page(page, out_dir, tag)
        except Exception as e:
            (out_dir / f"{tag}_error.txt").write_text(str(e), encoding="utf-8")
    await page.close()


async def run_xhs(ctx: Any, out_dir: Path, phase_wait: int) -> None:
    page = await ctx.new_page()
    await page.goto(XHS_BASE, wait_until="domcontentloaded", timeout=120000)
    print("\n[小红书] 已打开创作者平台。请扫码登录。", flush=True)
    await _phase_countdown(phase_wait, "小红书：登录")
    for path, tag in XHS_PATHS:
        url = XHS_BASE.rstrip("/") + path
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=120000)
            await asyncio.sleep(2.0)
            await _dump_page(page, out_dir, tag)
        except Exception as e:
            (out_dir / f"{tag}_error.txt").write_text(str(e), encoding="utf-8")
    await page.close()


async def _main(only: str, phase_wait: int) -> None:
    ensure_playwright_browsers_path(ROOT)
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise SystemExit("请先安装: pip install playwright && python -m playwright install chromium")

    PROFILE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_BASE / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    (OUT_BASE / "LATEST.txt").write_text(str(out_dir.resolve()) + "\n", encoding="utf-8")

    launch_kwargs: dict[str, Any] = {
        "headless": False,
        "viewport": {"width": 1400, "height": 900},
        "locale": "zh-CN",
    }
    exe = _chromium_path()
    if exe and Path(exe).exists():
        launch_kwargs["executable_path"] = exe

    print("=" * 60, flush=True)
    print("三平台控件探测 · 持久化目录:", PROFILE, flush=True)
    print("输出目录:", out_dir, flush=True)
    print("=" * 60, flush=True)

    async with async_playwright() as p:
        try:
            ctx = await p.chromium.launch_persistent_context(str(PROFILE), **launch_kwargs)
        except Exception as e:
            msg = str(e).lower()
            if "executable" in msg and "doesn't exist" in msg:
                kw = {k: v for k, v in launch_kwargs.items() if k != "executable_path"}
                kw["channel"] = "chrome"
                print("[info] 改用具机 Chrome (channel=chrome)", flush=True)
                ctx = await p.chromium.launch_persistent_context(str(PROFILE), **kw)
            else:
                raise
        try:
            only_l = (only or "all").strip().lower()
            if only_l in ("all", "douyin"):
                await run_douyin(ctx, out_dir, phase_wait)
            if only_l in ("all", "toutiao"):
                await run_toutiao(ctx, out_dir, phase_wait)
            if only_l in ("all", "xhs", "xiaohongshu"):
                await run_xhs(ctx, out_dir, phase_wait)
        finally:
            await ctx.close()

    print("\n完成。输出:", out_dir, flush=True)
    print("每个页面: *_controls.json + *.html + *.png", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="三平台发布页控件探测")
    ap.add_argument(
        "--only",
        choices=("all", "douyin", "toutiao", "xhs", "xiaohongshu"),
        default="all",
        help="只跑某一平台（默认 all 顺序：抖音→头条→小红书）",
    )
    ap.add_argument("--phase-wait", type=int, default=420, help="每平台登录等待秒数（可用环境变量 PROBE_PHASE_WAIT 覆盖）")
    args = ap.parse_args()
    asyncio.run(_main(args.only, max(60, args.phase_wait)))