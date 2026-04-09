#!/usr/bin/env python3
"""
交互式探测：用 **assets/ 下真实素材** 走抖音发布 dry_run（视频或图文），
在**未点最终发布**的页面上落盘 **控件级** DOM（data-testid / role / aria / class / xpath），
供改 driver 用 selector 匹配，而非依赖肉眼文案。

视频：未传 --video 时优先 `assets/probe_douyin.mp4`，否则 `assets/**/*.mp4` 中**最近修改**的一个。
图文：`--image` 且未传 --image-path 时优先 `probe_douyin_image.png` / `.jpg`，否则 assets 内**最近修改**的图片。

用法（须在能弹窗的终端执行，勿后台）:
  cd lobster_online
  python3 scripts/probe_douyin_publish_interactive.py
  python3 scripts/probe_douyin_publish_interactive.py --image

流程:
  1. 默认 profile 已含登录态时：**直接** dry_run + 同页采集（不先等扫码）
  2. 首次需扫码：加 `--login-wait-sec 60`（或先手动登录一次再跑）
  3. DouyinDriver.publish(..., dry_run=True)；**同一 page** 上采集 DOM（不重开 URL）

可选:
  --video /path/to/file.mp4
  --image                    探测「发布图文」上传图片（与视频二选一）
  --image-path /path/to.jpg
  --manual-cover-wait-sec N  仅视频：封面 manual 等待秒数（>0 时启用）

环境: PLAYWRIGHT_CHROMIUM_PATH、PLAYWRIGHT_BROWSER_CHANNEL（与项目其它脚本一致）
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_SCRIPTS_DIR))

from playwright_lobster_env import ensure_playwright_browsers_path

OUT_BASE = _SCRIPTS_DIR / "_probe_three_out"
DEFAULT_PROFILE = ROOT / "browser_data" / "probe_douyin_publish_ui"
# 与素材库、发布脚本一致：真实文件放在项目根下 assets/
DEFAULT_PROBE_MP4 = ROOT / "assets" / "probe_douyin.mp4"
# 与 skills.douyin_publish.driver._IMAGE_EXTS 一致
_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"})


def _resolve_probe_video(cli_video: str) -> Path:
    if (cli_video or "").strip():
        p = Path(cli_video.strip()).resolve()
        if not p.is_file():
            raise SystemExit(f"找不到视频: {p}")
        return p

    assets_dir = ROOT / "assets"
    if not assets_dir.is_dir():
        raise SystemExit(f"找不到素材目录: {assets_dir}")

    pref = DEFAULT_PROBE_MP4.resolve()
    if pref.is_file() and pref.stat().st_size > 0:
        print(f"[info] 使用默认探测视频: {pref.relative_to(ROOT)}", flush=True)
        return pref

    candidates = [
        p
        for p in assets_dir.rglob("*.mp4")
        if p.is_file() and p.stat().st_size > 0
    ]
    if not candidates:
        raise SystemExit(
            f"{assets_dir} 下没有可用的 .mp4（非空文件）。"
            "请放入素材或指定: --video /path/to/your.mp4"
        )
    chosen = max(candidates, key=lambda p: p.stat().st_mtime)
    print(f"[info] 从 assets 自动选用（最近修改）: {chosen.relative_to(ROOT)}", flush=True)
    return chosen


def _resolve_probe_image(cli_image: str) -> Path:
    if (cli_image or "").strip():
        p = Path(cli_image.strip()).resolve()
        if not p.is_file():
            raise SystemExit(f"找不到图片: {p}")
        if p.suffix.lower() not in _IMAGE_EXTS:
            raise SystemExit(
                f"扩展名 {p.suffix!r} 不在抖音图文支持列表内: {sorted(_IMAGE_EXTS)}"
            )
        return p

    assets_dir = ROOT / "assets"
    if not assets_dir.is_dir():
        raise SystemExit(f"找不到素材目录: {assets_dir}")

    for name in ("probe_douyin_image.png", "probe_douyin_image.jpg", "probe_douyin_image.jpeg"):
        pref = (assets_dir / name).resolve()
        if pref.is_file() and pref.stat().st_size > 0:
            print(f"[info] 使用默认探测图片: {pref.relative_to(ROOT)}", flush=True)
            return pref

    candidates = [
        p
        for p in assets_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in _IMAGE_EXTS
        and p.stat().st_size > 0
    ]
    if not candidates:
        raise SystemExit(
            f"{assets_dir} 下没有可用图片（{', '.join(sorted(_IMAGE_EXTS))}）。"
            "请放入素材或指定: --image-path /path/to/picture.png"
        )
    chosen = max(candidates, key=lambda p: p.stat().st_mtime)
    print(f"[info] 从 assets 自动选用图片（最近修改）: {chosen.relative_to(ROOT)}", flush=True)
    return chosen


# 候选发布区控件：可见的 button / role=button，带几何与属性（匹配用，不依赖「两个字」）
_PUBLISH_CANDIDATES_JS = """
() => {
  const out = [];
  const sel = 'button, [role="button"], a.semi-button, .semi-button-primary, [class*="Button-primary"]';
  document.querySelectorAll(sel).forEach((el) => {
    try {
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) return;
      const tid = el.getAttribute('data-testid') || '';
      const cls = (typeof el.className === 'string' ? el.className : '').slice(0, 500);
      out.push({
        tag: el.tagName,
        id: el.id || '',
        dataTestid: tid,
        role: el.getAttribute('role') || '',
        ariaLabel: (el.getAttribute('aria-label') || '').slice(0, 200),
        className: cls,
        innerText: (el.innerText || '').trim().slice(0, 120),
        disabled: !!el.disabled,
        rect: { x: r.x, y: r.y, w: r.width, h: r.height },
      });
    } catch (e) {}
  });
  return { url: location.href, candidateCount: out.length, candidates: out };
}
"""


def _load_probe_module():
    p = _SCRIPTS_DIR / "probe_douyin_cover_structure.py"
    spec = importlib.util.spec_from_file_location("probe_douyin_cover_structure", p)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载: {p}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _stdin_line(msg: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: input(msg))


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


async def _run_probe_flow(
    profile_dir: Path,
    media_path: Path,
    out_dir: Path,
    *,
    skip_login: bool,
    login_wait_sec: int,
    end_pause: bool,
    manual_cover_wait_sec: int,
    is_image: bool,
) -> None:
    mod = _load_probe_module()
    structure_js = mod._STRUCTURE_PROBE_JS
    dump_checkpoint = mod._dump_checkpoint

    os.environ.setdefault("PLAYWRIGHT_BROWSER_CHANNEL", "chrome")

    from publisher.browser_pool import _drop_cached_context, dryrun_douyin_upload_in_context

    # 仅当显式要求等待扫码（>0 秒）时才开第一段浏览器；默认 0 = 假定 profile 已登录，直接抓
    if not skip_login and login_wait_sec > 0:
        from playwright.async_api import async_playwright

        w = max(0, int(login_wait_sec))
        launch_kwargs = mod._chromium_launch_kwargs()
        async with async_playwright() as p:
            try:
                ctx = await p.chromium.launch_persistent_context(str(profile_dir), **launch_kwargs)
            except Exception as e:
                msg = str(e).lower()
                if "executable" in msg and "doesn't exist" in msg:
                    kw = {k: v for k, v in launch_kwargs.items() if k not in ("executable_path", "channel")}
                    kw["channel"] = "chrome"
                    ctx = await p.chromium.launch_persistent_context(str(profile_dir), **kw)
                else:
                    raise
            try:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await page.goto(
                    "https://creator.douyin.com/creator-micro/home",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
            except Exception:
                pass
            print(
                f"\n>>> 浏览器已打开：请在 {w} 秒内完成抖音创作者扫码登录。\n"
                ">>> 倒计时结束后自动继续（上传测试素材并采集控件）…\n",
                flush=True,
            )
            await asyncio.sleep(float(w))
            await ctx.close()
    else:
        print(
            "[info] 跳过扫码等待，直接 dry_run + 采集（当前 profile 须已登录）…\n",
            flush=True,
        )

    publish_opts: Dict[str, Any] = {}
    if not is_image:
        mcw = max(60, int(manual_cover_wait_sec))
        if manual_cover_wait_sec > 0:
            publish_opts["douyin_cover_mode"] = "manual"
            publish_opts["douyin_manual_cover_wait_sec"] = mcw
            print(
                f"[info] 封面 manual 模式，将等待最多 {mcw} 秒供你在浏览器内选封面。\n",
                flush=True,
            )

    async def _after_publish(page: Any, drive_result: Dict[str, Any]) -> None:
        print("[2/2] 采集控件结构（**当前会话同一页**，非重开 URL）…", flush=True)
        if not (drive_result or {}).get("ok"):
            print(
                "[warn] driver.publish 未完全成功，仍保存当前页快照便于排查：",
                (drive_result or {}).get("error", drive_result),
                flush=True,
            )
        await asyncio.sleep(0.5)
        cp_dir = out_dir / "checkpoint_after_dryrun"
        await dump_checkpoint(page, cp_dir, checkpoint_id="after_dryrun")

        btn_data = await page.evaluate(_PUBLISH_CANDIDATES_JS)
        _write_json(out_dir / "publish_button_candidates.json", btn_data)

        struct_full = await page.evaluate(structure_js)
        _write_json(out_dir / "structure_full.json", struct_full)

        hits = struct_full.get("keywordHits") or []
        publish_related = [
            h
            for h in hits
            if isinstance(h, dict)
            and (
                "发布" in (h.get("innerText") or "")
                or "发布" in (h.get("ariaLabel") or "")
                or "publish" in (h.get("dataTestid") or "").lower()
            )
        ]
        _write_json(out_dir / "keyword_hits_publish_subset.json", publish_related[:200])

        print("完成。请查看:", out_dir, flush=True)

    label = "图文" if is_image else "视频"
    print(f"[1/2] dry_run（{label}，不点最终发布）…", flush=True)
    dr: Dict[str, Any] = await dryrun_douyin_upload_in_context(
        str(profile_dir),
        str(media_path),
        title="probe标题",
        description="probe描述用于探测",
        tags="probe,test",
        publish_options=publish_opts or None,
        after_publish=_after_publish,
    )
    _write_json(out_dir / "dryrun_result.json", dr)

    await _drop_cached_context(str(profile_dir))
    if end_pause:
        print("\n按回车结束进程…", flush=True)
        await _stdin_line("")


async def main_async() -> None:
    ensure_playwright_browsers_path(ROOT)
    ap = argparse.ArgumentParser(
        description="抖音发布页：dry_run + 同页控件探测（默认 profile 已登录则直接抓）"
    )
    ap.add_argument("--profile-dir", default="", help=f"默认 {DEFAULT_PROFILE}")
    ap.add_argument(
        "--video",
        default="",
        help="mp4 路径（仅非 --image）；不传则优先 probe_douyin.mp4，否则 assets 内最近修改的 .mp4",
    )
    ap.add_argument(
        "--image",
        action="store_true",
        help="探测发布图文：上传图片 dry_run + 同页采 DOM（与默认视频模式二选一）",
    )
    ap.add_argument(
        "--image-path",
        default="",
        help="图文用图片路径；不传则优先 probe_douyin_image.*，否则 assets 内最近修改的图片",
    )
    ap.add_argument(
        "--skip-login-wait",
        action="store_true",
        help="与默认行为相同：跳过扫码阶段；保留仅作显式说明",
    )
    ap.add_argument(
        "--login-wait-sec",
        type=int,
        default=0,
        help=">0 时先打开创作者首页并等待该秒数扫码；默认 0（假定已登录，直接 dry_run）",
    )
    ap.add_argument(
        "--manual-cover-wait-sec",
        type=int,
        default=0,
        help=">0 时封面走 manual，并等待该秒数（至少 60）供你在浏览器内选封面",
    )
    ap.add_argument(
        "--end-pause",
        action="store_true",
        help="采集结束后须按回车再关浏览器（默认自动关闭）",
    )
    args = ap.parse_args()
    if args.image and (args.video or "").strip():
        raise SystemExit("不要同时指定 --image 与 --video；图文请只用 --image [--image-path]")

    prof = Path(args.profile_dir.strip() or str(DEFAULT_PROFILE))
    if not prof.is_absolute():
        prof = (ROOT / prof).resolve()
    prof.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    is_image = bool(args.image)
    if is_image:
        out_dir = OUT_BASE / f"douyin_image_upload_interactive_{stamp}"
        latest_name = "DOUYIN_IMAGE_UPLOAD_INTERACTIVE_LATEST.txt"
        media_path = _resolve_probe_image(args.image_path)
    else:
        out_dir = OUT_BASE / f"douyin_publish_interactive_{stamp}"
        latest_name = "DOUYIN_PUBLISH_INTERACTIVE_LATEST.txt"
        media_path = _resolve_probe_video(args.video)

    out_dir.mkdir(parents=True, exist_ok=True)
    (OUT_BASE / latest_name).write_text(str(out_dir.resolve()) + "\n", encoding="utf-8")

    await _run_probe_flow(
        prof,
        media_path,
        out_dir,
        skip_login=args.skip_login_wait,
        login_wait_sec=args.login_wait_sec,
        end_pause=args.end_pause,
        manual_cover_wait_sec=args.manual_cover_wait_sec,
        is_image=is_image,
    )


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
