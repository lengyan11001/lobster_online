#!/usr/bin/env python3
"""
抖音视频发布页 · 封面区 DOM 结构探测（本机）

不依赖「按文案点」：在页面内执行 JS，抓取 id / data-testid / role / aria / class / XPath /
selector 线索，以及 [role=dialog]、含「封面」等关键词的节点，落盘 JSON + HTML + 截图。

用法（须能弹出浏览器；与正式账号目录隔离）:
  cd lobster_online
  python3 scripts/probe_douyin_cover_structure.py

默认（脚本代操作界面 + 多阶段落盘）:
  · 首跳发视频页；cp00/cp01 抓登录前后；cp02–cp04 抓首页/上传/发视频；cp05–cp09 抓封面展开各步
  · 根目录 cover_structure.json 与 *.json 与最后一阶段一致（兼容只读根目录的旧用法）

只等你手动点完再抓、不要整页重载:
  python3 scripts/probe_douyin_cover_structure.py --wait-enter

不要自动跳转发视频、不要点封面:
  python3 scripts/probe_douyin_cover_structure.py --no-nav --no-expand

环境变量:
  PLAYWRIGHT_CHROMIUM_PATH   指定 Chromium
  PLAYWRIGHT_BROWSER_CHANNEL  未设且 macOS 时脚本内可设 chrome（与 start_online 一致）

输出（全量、不截断条数；多阶段 checkpoint 各一套 JSON + HTML + 截图）:
  scripts/_probe_three_out/douyin_cover_structure_<时间戳>/
    checkpoints_index.json   # 全流程各阶段列表与路径
    cp00_*/ … cpNN_* /        # 每阶段子目录：cover_structure.json + 拆分 json + page.html + page.png
    cover_structure.json       # 与「最后一阶段」索引相同（兼容旧脚本）
  scripts/_probe_three_out/DOUYIN_COVER_LATEST.txt
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_SCRIPTS_DIR))

from playwright_lobster_env import ensure_playwright_browsers_path

PROFILE = ROOT / "browser_data" / "probe_douyin_cover"
OUT_BASE = _SCRIPTS_DIR / "_probe_three_out"

DOUYIN_HOME = "https://creator.douyin.com/creator-micro/home"
DOUYIN_UPLOAD = "https://creator.douyin.com/creator-micro/content/upload"
DOUYIN_POST_VIDEO = (
    "https://creator.douyin.com/creator-micro/content/post/video?enter_from=publish_page"
)

# 全量探测：条数不截断；单字段长度上限防 JSON 爆炸；拆文件落盘
_STRUCTURE_PROBE_JS = """
() => {
  function xpath(el) {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '//*[@id="' + String(el.id).replace(/"/g, '\\\\"') + '"]';
    if (el === document.body) return '/html/body';
    const parent = el.parentNode;
    if (!parent || parent.nodeType !== 1) return '';
    const siblings = parent.children;
    const same = Array.prototype.filter.call(siblings, function (c) {
      return c.tagName === el.tagName;
    });
    const idx = same.indexOf(el) + 1;
    return xpath(parent) + '/' + el.tagName.toLowerCase() + '[' + idx + ']';
  }

  function selectorHint(el) {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '#' + (typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(el.id) : el.id);
    const tid = el.getAttribute('data-testid');
    if (tid) {
      const esc = typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(tid) : tid.replace(/"/g, '\\\\"');
      return '[data-testid="' + esc + '"]';
    }
    const rn = el.getAttribute('role');
    const al = el.getAttribute('aria-label');
    if (rn && al) return '[role="' + rn + '"][aria-label="' + al.slice(0, 80).replace(/"/g, '\\\\"') + '"]';
    if (rn) return el.tagName.toLowerCase() + '[role="' + rn + '"]';
    return el.tagName.toLowerCase();
  }

  function rectOf(el) {
    try {
      const r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, w: r.width, h: r.height };
    } catch (e) {
      return null;
    }
  }

  function attrsRecord(el, maxVal) {
    const out = {};
    if (!el.attributes) return out;
    const mv = maxVal || 4000;
    for (let i = 0; i < el.attributes.length; i++) {
      const a = el.attributes[i];
      if (a.name === 'style') {
        out.style = (a.value || '').slice(0, 800);
        continue;
      }
      out[a.name] = (a.value || '').slice(0, mv);
    }
    return out;
  }

  function dataAttrsOnly(el) {
    const out = {};
    if (!el.attributes) return out;
    for (let i = 0; i < el.attributes.length; i++) {
      const a = el.attributes[i];
      if (a.name.indexOf('data-') === 0) out[a.name] = (a.value || '').slice(0, 2000);
    }
    return out;
  }

  function describeInteractive(el, opts) {
    if (!el || el.nodeType !== 1) return null;
    const tn = el.tagName;
    const isFC = tn === 'INPUT' || tn === 'TEXTAREA' || tn === 'SELECT' || tn === 'OPTION';
    const r = rectOf(el);
    const rw = r ? r.width : 0;
    const rh = r ? r.height : 0;
    const visible = rw > 0 && rh > 0;
    const incl = opts && opts.includeInvisible;
    if (!isFC && !visible && !incl) return null;
    const cls = typeof el.className === 'string' ? el.className : '';
    const it = (el.innerText || '').trim().slice(0, 2000);
    let valueStr = '';
    try {
      if ('value' in el && el.value !== undefined) valueStr = String(el.value).slice(0, 4000);
    } catch (e) {}
    const rr = r || { x: 0, y: 0, width: 0, height: 0 };
    return {
      tag: tn,
      id: el.id || '',
      className: cls.slice(0, 4000),
      attrs: attrsRecord(el, 4000),
      dataAttrs: dataAttrsOnly(el),
      dataTestid: el.getAttribute('data-testid') || '',
      role: el.getAttribute('role') || '',
      ariaLabel: el.getAttribute('aria-label') || '',
      ariaChecked: el.getAttribute('aria-checked') || '',
      ariaExpanded: el.getAttribute('aria-expanded') || '',
      name: el.getAttribute('name') || '',
      type: el.getAttribute('type') || '',
      value: valueStr,
      placeholder: el.getAttribute('placeholder') || '',
      disabled: !!el.disabled,
      readOnly: !!el.readOnly,
      checked: !!el.checked,
      tabIndex: el.getAttribute('tabindex') || '',
      href: tn === 'A' ? (el.getAttribute('href') || '').slice(0, 800) : '',
      src: (tn === 'IMG' || tn === 'IFRAME' || tn === 'VIDEO' || tn === 'SOURCE')
        ? (el.getAttribute('src') || '').slice(0, 800) : '',
      innerText: it,
      selectorHint: selectorHint(el),
      xpath: xpath(el),
      rect: { x: rr.x, y: rr.y, w: rr.width, h: rr.height },
      visible: visible,
    };
  }

  const seen = new Set();
  const interactiveAll = [];
  function pushEl(el) {
    if (!el || seen.has(el)) return;
    seen.add(el);
    const d = describeInteractive(el, { includeInvisible: true });
    if (d) interactiveAll.push(d);
  }

  const tags = ['INPUT', 'TEXTAREA', 'BUTTON', 'A', 'SELECT', 'OPTION', 'LABEL', 'IMG', 'IFRAME', 'VIDEO', 'CANVAS'];
  tags.forEach(function (t) {
    document.querySelectorAll(t.toLowerCase()).forEach(pushEl);
  });
  const roles = [
    'button', 'tab', 'radio', 'checkbox', 'switch', 'option', 'menuitem', 'combobox',
    'listbox', 'textbox', 'searchbox', 'slider', 'link', 'gridcell', 'separator', 'navigation'
  ];
  roles.forEach(function (rname) {
    document.querySelectorAll('[role="' + rname + '"]').forEach(pushEl);
  });
  document.querySelectorAll('[contenteditable="true"]').forEach(function (el) {
    if (seen.has(el)) return;
    seen.add(el);
    const d = describeInteractive(el, { includeInvisible: false });
    if (d) interactiveAll.push(d);
  });

  const KW = /封面|横|竖|智能|完成|设置|检测|识别|发布|定时|可见|位置|话题|原创|高清|推荐|暂存|草稿|小黄车|商品|合集/;
  const keywordHits = [];
  document.querySelectorAll('*').forEach(function (el) {
    if (el.nodeType !== 1) return;
    const quick = ((el.innerText || '').trim().slice(0, 600)) + (typeof el.className === 'string' ? el.className : '') + (el.id || '');
    if (!KW.test(quick)) return;
    const d = describeInteractive(el, { includeInvisible: true });
    if (d) keywordHits.push(d);
  });

  const dialogSelectors = [
    '[role="dialog"]', '[role="alertdialog"]', '.semi-modal-wrap',
    '[class*="Modal"]', '[class*="modal-wrap"]', '[class*="Drawer"]', '[class*="drawer"]'
  ];
  const dialogSeen = new Set();
  const dialogsAll = [];
  dialogSelectors.forEach(function (sel) {
    document.querySelectorAll(sel).forEach(function (el) {
      if (dialogSeen.has(el)) return;
      dialogSeen.add(el);
      dialogsAll.push({
        matchedSelector: sel,
        selectorHint: selectorHint(el),
        xpath: xpath(el),
        attrs: attrsRecord(el, 4000),
        innerText: (el.innerText || '').trim().slice(0, 12000),
        childElementCount: el.children ? el.children.length : 0,
        outerHTML: el.outerHTML ? el.outerHTML.slice(0, 150000) : '',
      });
    });
  });

  const iframes = [];
  document.querySelectorAll('iframe').forEach(function (el) {
    iframes.push({
      id: el.id || '',
      name: el.name || '',
      src: (el.getAttribute('src') || '').slice(0, 2000),
      title: el.getAttribute('title') || '',
      xpath: xpath(el),
    });
  });

  const forms = [];
  document.querySelectorAll('form').forEach(function (el) {
    forms.push({
      id: el.id || '',
      action: (el.getAttribute('action') || '').slice(0, 2000),
      method: (el.getAttribute('method') || '').toLowerCase(),
      xpath: xpath(el),
      fieldCount: el.querySelectorAll('input, textarea, select').length,
    });
  });

  return {
    url: location.href,
    title: document.title,
    userAgent: navigator.userAgent,
    viewport: { w: window.innerWidth, h: window.innerHeight },
    counts: {
      interactiveAll: interactiveAll.length,
      keywordHits: keywordHits.length,
      dialogs: dialogsAll.length,
      iframes: iframes.length,
      forms: forms.length,
    },
    interactiveAll: interactiveAll,
    keywordHits: keywordHits,
    dialogsAll: dialogsAll,
    iframes: iframes,
    forms: forms,
  };
}
"""


def _chromium_launch_kwargs() -> Dict[str, Any]:
    kw: Dict[str, Any] = {
        "headless": False,
        "viewport": {"width": 1400, "height": 900},
        "locale": "zh-CN",
    }
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH", "").strip()
    if exe and Path(exe).exists():
        kw["executable_path"] = exe
    ch = os.environ.get("PLAYWRIGHT_BROWSER_CHANNEL", "").strip()
    if ch:
        kw["channel"] = ch
    elif sys.platform == "darwin" and "executable_path" not in kw and "channel" not in kw:
        kw["channel"] = "chrome"
    return kw


async def _goto_settle(page: Any, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded", timeout=120000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    await asyncio.sleep(0.4)


async def _dismiss_overlays_nav(page: Any) -> tuple[list[str], int]:
    log: list[str] = []
    n = 0
    try:
        from skills.douyin_publish.driver import _dismiss_overlays

        n = await _dismiss_overlays(page, "probe_cover_nav")
        log.append(f"dismiss_overlays:{n}")
    except Exception as e:
        log.append(f"dismiss_overlays_skip:{e!s}")
    return log, n


async def _auto_expand_cover_ui(
    page: Any,
    *,
    snap: Optional[Callable[[str, List[str]], Awaitable[None]]] = None,
) -> list[str]:
    """
    由脚本代点（无需用户手点浏览器）：尽量展开封面相关弹层，便于 structure 抓到 dialog。
    失败项静默跳过，仅记日志。
    """
    log: list[str] = []
    try:
        from skills.douyin_publish.driver import _dismiss_overlays

        n = await _dismiss_overlays(page, "probe_cover")
        log.append(f"dismiss_overlays:{n}")
    except Exception as e:
        log.append(f"dismiss_overlays_skip:{e!s}")

    if snap:
        await snap("cp05_expand_dismiss", list(log))

    try:
        await page.evaluate(
            "() => { window.scrollTo(0, document.body.scrollHeight); }"
        )
        await asyncio.sleep(0.35)
        await page.evaluate("() => { window.scrollTo(0, 0); }")
        log.append("scroll_full")
    except Exception as e:
        log.append(f"scroll_skip:{e!s}")

    if snap:
        await snap("cp06_expand_scroll", list(log))

    async def _click_visible_exact(text: str) -> bool:
        loc = page.get_by_text(text, exact=True)
        cnt = await loc.count()
        for i in range(cnt):
            try:
                el = loc.nth(i)
                if await el.is_visible():
                    await el.click(timeout=8000)
                    return True
            except Exception:
                continue
        return False

    for lab in ("设置横封面", "设置横版封面"):
        if await _click_visible_exact(lab):
            log.append(f"click:{lab}")
            await asyncio.sleep(0.85)
            if snap:
                await snap("cp07_after_horizontal_cover", list(log))
            break

    for lab in ("设置竖封面", "设置竖版封面"):
        if await _click_visible_exact(lab):
            log.append(f"click:{lab}")
            await asyncio.sleep(0.85)
            if snap:
                await snap("cp08_after_vertical_cover", list(log))
            break

    try:
        nd = await page.locator('[role="dialog"]').count()
        for i in range(min(nd, 5)):
            dlg = page.locator('[role="dialog"]').nth(i)
            btn = dlg.get_by_role("button", name="完成", exact=True).first
            try:
                if await btn.is_visible():
                    await btn.click(timeout=8000)
                    log.append("click:dialog[完成]")
                    await asyncio.sleep(0.5)
                    break
            except Exception:
                continue
    except Exception as e:
        log.append(f"dialog_complete_skip:{e!s}")

    if await _click_visible_exact("完成"):
        log.append("click:完成")

    await asyncio.sleep(1.2)
    if snap:
        await snap("cp09_expand_final", list(log))
    return log


def _write_json_file(out_dir: Path, name: str, obj: Any) -> None:
    (out_dir / name).write_text(
        json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def _dump_checkpoint(
    page: Any,
    dest_dir: Path,
    *,
    checkpoint_id: str,
    automation: Optional[List[str]] = None,
) -> Dict[str, Any]:
    await asyncio.sleep(0.8)
    data: Any = await page.evaluate(_STRUCTURE_PROBE_JS)
    if automation is not None:
        data["automation"] = {"actions": automation}

    heavy: list[tuple[str, str]] = [
        ("interactiveAll", "interactive_all.json"),
        ("keywordHits", "keyword_hits.json"),
        ("dialogsAll", "dialogs_all.json"),
        ("iframes", "iframes.json"),
        ("forms", "forms.json"),
    ]
    dest_dir.mkdir(parents=True, exist_ok=True)
    files_map: Dict[str, str] = {}
    for key, fname in heavy:
        payload = data.pop(key)
        _write_json_file(dest_dir, fname, payload if payload is not None else [])
        files_map[key] = fname

    index: Dict[str, Any] = {
        "url": data.pop("url"),
        "title": data.pop("title"),
        "userAgent": data.pop("userAgent"),
        "viewport": data.pop("viewport"),
        "counts": data.pop("counts"),
        "files": files_map,
    }
    if "automation" in data:
        index["automation"] = data.pop("automation")
    for k, v in data.items():
        index[k] = v

    index["checkpoint_id"] = checkpoint_id
    _write_json_file(dest_dir, "cover_structure.json", index)
    try:
        html = await page.content()
        (dest_dir / "page.html").write_text(html, encoding="utf-8", errors="replace")
    except Exception as e:
        (dest_dir / "page_html_error.txt").write_text(str(e), encoding="utf-8")
    try:
        await page.screenshot(path=str(dest_dir / "page.png"), full_page=True)
    except Exception:
        pass

    return {
        "checkpoint_id": checkpoint_id,
        "relative_dir": checkpoint_id,
        "url": index.get("url"),
        "title": index.get("title"),
        "counts": index.get("counts"),
    }


async def _stdin_confirm(msg: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: input(msg))


async def main() -> None:
    ensure_playwright_browsers_path(ROOT)
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise SystemExit("请先安装: pip install playwright && python -m playwright install chromium")

    ap = argparse.ArgumentParser(description="抖音封面区 DOM 结构探测")
    ap.add_argument(
        "--wait-enter",
        "--stdin",
        dest="wait_enter",
        action="store_true",
        help="打开首 URL 后等终端回车再继续；默认不重载整站导航（避免冲掉你手点的界面）",
    )
    ap.add_argument(
        "--nav-after-enter",
        action="store_true",
        help="与 --wait-enter 同用：回车后仍执行首页→上传→发视频跳转",
    )
    ap.add_argument(
        "--auto-wait-sec",
        type=int,
        default=60,
        help="未使用 --wait-enter 时：登录等待秒数（默认 60）后再自动导航",
    )
    ap.add_argument(
        "--start-url",
        default=DOUYIN_POST_VIDEO,
        help="首跳 URL，默认发视频发布页",
    )
    ap.add_argument(
        "--no-nav",
        action="store_true",
        help="不执行首页→上传→发视频自动跳转",
    )
    ap.add_argument(
        "--no-expand",
        action="store_true",
        help="不尝试自动点横/竖封面等",
    )
    ap.add_argument("--stdin-confirm", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if getattr(args, "stdin_confirm", False):
        args.wait_enter = True

    PROFILE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_BASE / f"douyin_cover_structure_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (OUT_BASE / "DOUYIN_COVER_LATEST.txt").write_text(str(out_dir.resolve()) + "\n", encoding="utf-8")

    launch_kwargs = _chromium_launch_kwargs()

    print("=" * 60, flush=True)
    print("抖音封面结构探测 · profile:", PROFILE, flush=True)
    print("输出:", out_dir, flush=True)
    print("=" * 60, flush=True)

    async with async_playwright() as p:
        try:
            ctx = await p.chromium.launch_persistent_context(str(PROFILE), **launch_kwargs)
        except Exception as e:
            msg = str(e).lower()
            if "executable" in msg and "doesn't exist" in msg:
                kw = {k: v for k, v in launch_kwargs.items() if k not in ("executable_path", "channel")}
                kw["channel"] = "chrome"
                print("[info] fallback channel=chrome", flush=True)
                ctx = await p.chromium.launch_persistent_context(str(PROFILE), **kw)
            else:
                raise
        try:
            page = await ctx.new_page()
            checkpoints: List[Dict[str, Any]] = []
            last_ck_dir: Optional[Path] = None

            async def snap(tag: str, auto: List[str]) -> None:
                nonlocal last_ck_dir
                print(f"  checkpoint → {tag}", flush=True)
                summ = await _dump_checkpoint(
                    page, out_dir / tag, checkpoint_id=tag, automation=auto
                )
                checkpoints.append(summ)
                last_ck_dir = out_dir / tag

            await page.goto(args.start_url, wait_until="domcontentloaded", timeout=120000)
            print("\n已在浏览器打开:\n  " + args.start_url, flush=True)
            await snap("cp00_start_url", ["phase:after_first_goto"])

            if args.wait_enter:
                await _stdin_confirm(
                    "\n在浏览器里登录/点到你要看的界面；准备好后回车。\n"
                    "（默认不再整页跳转；需要脚本强制进发视频页请加 --nav-after-enter）\n\n按回车继续…"
                )
            else:
                w = max(0, int(args.auto_wait_sec))
                print(f"\n等待 {w} 秒供登录，随后脚本将自动跳转发视频页并尝试点封面…", flush=True)
                if w > 0:
                    await asyncio.sleep(w)

            await snap("cp01_after_login_wait", ["phase:after_wait_or_stdin"])

            nav_log: list[str] = []
            if not args.no_nav and (not args.wait_enter or args.nav_after_enter):
                print("自动导航：首页 → 上传 → 发视频…", flush=True)
                await _goto_settle(page, DOUYIN_HOME)
                nav_log.append("goto:home")
                await snap("cp02_home", nav_log + ["phase:nav"])

                await _goto_settle(page, DOUYIN_UPLOAD)
                nav_log.append("goto:upload")
                await snap("cp03_upload", nav_log + ["phase:nav"])

                await _goto_settle(page, DOUYIN_POST_VIDEO)
                nav_log.append("goto:post_video")
                dl, _n = await _dismiss_overlays_nav(page)
                nav_log.extend(dl)
                await snap("cp04_post_video", nav_log + ["phase:nav"])
            elif args.no_nav:
                nav_log = ["skipped:--no-nav"]
            else:
                nav_log = ["skipped:wait_enter_without_nav_after_enter"]

            expand_log: list[str] = []
            if not args.no_expand:
                print("自动尝试：展开封面相关 UI（多阶段 checkpoint）…", flush=True)

                async def expand_snap(tag: str, partial: List[str]) -> None:
                    await snap(tag, nav_log + partial)

                expand_log = await _auto_expand_cover_ui(page, snap=expand_snap)
            else:
                expand_log = ["skipped:--no-expand"]

            master: Dict[str, Any] = {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "checkpoints": checkpoints,
                "last_checkpoint_dir": last_ck_dir.name if last_ck_dir else None,
            }
            _write_json_file(out_dir, "checkpoints_index.json", master)

            if last_ck_dir and (last_ck_dir / "cover_structure.json").is_file():
                shutil.copyfile(
                    str(last_ck_dir / "cover_structure.json"),
                    str(out_dir / "cover_structure.json"),
                )
                if (last_ck_dir / "page.html").is_file():
                    shutil.copyfile(
                        str(last_ck_dir / "page.html"),
                        str(out_dir / "page.html"),
                    )
                if (last_ck_dir / "page.png").is_file():
                    shutil.copyfile(
                        str(last_ck_dir / "page.png"),
                        str(out_dir / "page.png"),
                    )
                for fname in (
                    "interactive_all.json",
                    "keyword_hits.json",
                    "dialogs_all.json",
                    "iframes.json",
                    "forms.json",
                ):
                    src = last_ck_dir / fname
                    if src.is_file():
                        shutil.copyfile(str(src), str(out_dir / fname))

            print(
                "\n已写入 checkpoints_index.json + 各 cp*_*/ 子目录；根目录 cover_structure.json 同最后阶段",
                flush=True,
            )
            print("路径:", out_dir.resolve(), flush=True)
        finally:
            await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
