"""Playwright browser context pool — persistent sessions per account.

Each account gets its own user data directory so cookies/localStorage persist.
The pool lazily starts the Playwright instance on first use.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import json
import logging
import os
import random
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlparse

from .pw_timeouts import ms as _pw_ms
from .pw_timeouts import navigation_timeout_ms

logger = logging.getLogger(__name__)

_DOUYIN_IM_PROTO: Optional[Tuple[Any, Any]] = None

DEFAULT_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_DEFAULT_BROWSER_OPTIONS: Dict[str, Any] = {
    "user_agent": DEFAULT_CHROME_UA,
    "proxy": None,
}


def _default_browser_options() -> Dict[str, Any]:
    return dict(_DEFAULT_BROWSER_OPTIONS)


def _browser_open_error_message(exc: BaseException) -> str:
    raw = str(exc or "")
    if "ERR_EMPTY_RESPONSE" in raw or "accounts.google.com" in raw or "Page.goto" in raw:
        return "Built-in Chromium could not open the authorization page. Please use the system browser authorization link."
    if "Executable doesn't exist" in raw or "browserType.launch" in raw or "chromium" in raw.lower():
        return "Built-in Chromium is unavailable. Please use the system browser authorization link."
    return raw[:300] if raw else "Built-in Chromium failed to open."


def browser_options_from_publish_meta(meta: Optional[dict]) -> Dict[str, Any]:
    """
    从发布账号的 meta 解析 Playwright 可用的 browser 选项（UA / proxy）。
    meta 结构示例: {"browser": {"user_agent": "...", "proxy": {"server": "http://h:p", ...}}}
    条件不满足时抛出 ValueError（由 API 层转为 400）。
    """
    base = _default_browser_options()
    if not meta or not isinstance(meta, dict):
        return base
    br = meta.get("browser")
    if br is None:
        return base
    if not isinstance(br, dict):
        raise ValueError("账号 meta.browser 必须是对象")

    ua = br.get("user_agent")
    if ua is not None:
        if not isinstance(ua, str) or not ua.strip():
            raise ValueError("账号 meta.browser.user_agent 若填写须为非空字符串")
        base = {**base, "user_agent": ua.strip()}

    px = br.get("proxy")
    if px is None:
        pass
    elif px == {}:
        raise ValueError("账号 meta.browser.proxy 不能为空对象；不需要代理时请省略该字段")
    elif isinstance(px, dict):
        server = px.get("server")
        if not isinstance(server, str) or not server.strip():
            raise ValueError("代理 server 须为非空字符串，例如 http://host:port")
        s = server.strip().lower()
        if not (
            s.startswith("http://")
            or s.startswith("https://")
            or s.startswith("socks5://")
        ):
            raise ValueError("代理 server 须以 http://、https:// 或 socks5:// 开头")
        user = px.get("username")
        pw = px.get("password")
        has_u = user is not None and str(user).strip() != ""
        has_p = pw is not None and str(pw) != ""
        if has_u ^ has_p:
            raise ValueError("代理用户名与密码须同时填写或同时省略")
        pw_obj: Dict[str, Any] = {"server": server.strip()}
        if has_u:
            pw_obj["username"] = str(user).strip()
            pw_obj["password"] = str(pw)
        base = {**base, "proxy": pw_obj}
    else:
        raise ValueError("账号 meta.browser.proxy 必须是对象或省略")

    return base


def douyin_workbench_browser_options_from_publish_meta(meta: Optional[dict]) -> Dict[str, Any]:
    """Browser options for Douyin front-site workflows.

    The reference Douyin project uses a persistent real Chrome profile.  Keep
    this scoped to the workbench so creator-center publishing behavior does not
    change unexpectedly.
    """
    opts = browser_options_from_publish_meta(meta)
    if not opts.get("channel") and not opts.get("executable_path"):
        opts = {**opts, "channel": _BROWSER_CHANNEL or "chrome"}
    if not opts.get("viewport"):
        opts = {**opts, "viewport": {"width": 1440, "height": 960}}
    return opts


def browser_options_from_youtube_proxy_fields(
    proxy_server: Optional[str],
    proxy_username: Optional[str],
    proxy_password: Optional[str],
) -> Dict[str, Any]:
    """将 YouTube 账号页的代理字段转为与 `browser_options_from_publish_meta` 相同的结构。

    与发布「打开浏览器」共用同一 Playwright 持久化 Chromium（含 PLAYWRIGHT_CHROMIUM_PATH / CHANNEL）。
    """
    base = _default_browser_options()
    raw = (proxy_server or "").strip()
    if not raw:
        return base
    u = urlparse(raw)
    if u.scheme not in ("http", "https", "socks5"):
        raise ValueError("YouTube 代理须以 http://、https:// 或 socks5:// 开头")
    host = u.hostname
    if not host:
        raise ValueError("代理 URL 中缺少主机名")
    port = u.port if u.port is not None else (443 if u.scheme == "https" else 8080)
    user = (proxy_username or "").strip() or (unquote(u.username) if u.username else "")
    pw = (proxy_password or "").strip() or (unquote(u.password) if u.password else "")
    server = f"{u.scheme}://{host}:{port}"

    # Chromium / Playwright 不支持「带用户名密码的 SOCKS5」；经本机 HTTP 桥转发到上游 SOCKS5（见 socks_http_bridge）。
    if u.scheme == "socks5" and user and pw:
        from .socks_http_bridge import ensure_local_http_bridge

        local_http = ensure_local_http_bridge(host, port, user, pw)
        return {**base, "proxy": {"server": local_http}}

    pw_obj: Dict[str, Any] = {"server": server}
    if user or pw:
        if not user or not pw:
            raise ValueError(
                "使用代理认证时请同时填写用户名与密码，或在代理 URL 中使用 user:pass@host 形式"
            )
        pw_obj["username"] = user
        pw_obj["password"] = pw
    return {**base, "proxy": pw_obj}


def _fingerprint_browser_options(opts: Dict[str, Any]) -> str:
    proxy = opts.get("proxy")
    proxy_canon = None
    if proxy:
        proxy_canon = {
            "server": proxy["server"],
            "username": proxy.get("username"),
            "password": proxy.get("password"),
        }
    blob = json.dumps(
        {
            "user_agent": opts["user_agent"],
            "proxy": proxy_canon,
            "channel": opts.get("channel") or "",
            "executable_path": opts.get("executable_path") or "",
            "viewport": opts.get("viewport") or None,
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:40]


def _storage_key(profile_dir: str, browser_options: Dict[str, Any]) -> str:
    return f"{profile_dir}\0{_fingerprint_browser_options(browser_options)}"


def _publish_log_url(page: Any, tag: str) -> None:
    """定位「反复进出页面」：对照每条日志的 url 与 tag 即可判断是哪一步导航。"""
    try:
        u = (getattr(page, "url", None) or "").strip()
    except Exception:
        u = "<error>"
    logger.info("[PUBLISH-NAV] %s url=%s", tag, u[:500] if u else "")

_pw_instance: Any = None
_browser: Any = None
_lock = asyncio.Lock()
# storage_key = profile_dir + "\\0" + fingerprint(proxy+UA)；同一 profile 指纹变化时会关闭旧 context
_contexts: Dict[str, Any] = {}
_context_headless: Dict[str, bool] = {}
_profile_active_key: Dict[str, str] = {}

_BASE_DIR = Path(__file__).resolve().parent.parent
_CHROMIUM_PATH = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH", "")
# 例如 chrome：使用本机已安装的 Google Chrome，避免部分环境下 bundled Chromium SIGTRAP。
_BROWSER_CHANNEL = os.environ.get("PLAYWRIGHT_BROWSER_CHANNEL", "").strip()

# CDP attach 模式：用户自己开好 Chrome（--remote-debugging-port=9222），脚本通过 CDP 接管。
# 避开 Playwright launch 层的指纹检测；淘宝等反自动化站点在此模式下表现与"手动双击 Chrome"一致。
# 优先级：PLAYWRIGHT_CDP_URL > TAOBAO_CDP_URL。设了 CDP_URL 时 profile_dir 被忽略（以用户手动启动时的 --user-data-dir 为准）。
_CDP_URL = (
    os.environ.get("PLAYWRIGHT_CDP_URL", "").strip()
    or os.environ.get("TAOBAO_CDP_URL", "").strip()
)
_cdp_browser: Any = None


def _cdp_enabled() -> bool:
    return bool(_CDP_URL)


async def _connect_cdp_browser() -> Any:
    """懒加载 + 缓存：连接用户手动启动的 Chrome，复用同一个 browser 对象。"""
    global _pw_instance, _cdp_browser
    if _cdp_browser is not None and _cdp_browser.is_connected():
        return _cdp_browser
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError("playwright 未安装")
    if _pw_instance is None:
        _pw_instance = await async_playwright().__aenter__()
    logger.info("[BROWSER][CDP] connect_over_cdp %s", _CDP_URL)
    _cdp_browser = await _pw_instance.chromium.connect_over_cdp(_CDP_URL)
    return _cdp_browser


async def _acquire_cdp_context(profile_dir: str, key: str) -> Tuple[Any, bool]:
    """CDP 模式下返回用户 Chrome 的第一个 context，并打标 _lobster_cdp_external=True。

    此 context 属于用户手动启动的浏览器，脚本绝不应关闭它。
    profile_dir 仅用作 _contexts 缓存键，不实际影响浏览器进程。
    """
    browser = await _connect_cdp_browser()
    ctx_list = list(getattr(browser, "contexts", []) or [])
    if not ctx_list:
        raise RuntimeError(
            f"CDP 浏览器没有任何 context（{_CDP_URL}）。请确认 Chrome 已启动且未处于关机状态。"
        )
    ctx = ctx_list[0]
    try:
        setattr(ctx, "_lobster_cdp_external", True)
    except Exception:
        pass
    async with _lock:
        _contexts[key] = ctx
        _context_headless[key] = False
        _profile_active_key[profile_dir] = key
    logger.info("[BROWSER][CDP] attach 复用 context, pages=%s, 缓存键 profile=%s", len(ctx.pages), profile_dir[-60:])
    return ctx, False


async def _ensure_browser() -> Any:
    global _pw_instance, _browser
    async with _lock:
        if _browser and _browser.is_connected():
            return _browser
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "playwright 未安装。请运行: pip install playwright && python -m playwright install chromium"
            )
        _pw_instance = await async_playwright().__aenter__()

        launch_kwargs: Dict[str, Any] = {"headless": False}
        if _BROWSER_CHANNEL:
            launch_kwargs["channel"] = _BROWSER_CHANNEL
        elif _CHROMIUM_PATH and Path(_CHROMIUM_PATH).exists():
            launch_kwargs["executable_path"] = _CHROMIUM_PATH

        _browser = await _pw_instance.chromium.launch(**launch_kwargs)
        logger.info("Playwright Chromium launched (headless=False)")
        return _browser


async def _acquire_context(
    profile_dir: str,
    *,
    new_headless: bool = False,
    browser_options: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, bool]:
    """Get (or reuse) a persistent browser context for the given profile directory.

    Returns (context, created_new). If created_new is False, caller MUST NOT close it.

    browser_options: 由 browser_options_from_publish_meta 得到；None 表示默认 UA、无代理。
    同一 profile_dir 下代理或 UA 变更时会关闭旧 context 并按新指纹新建。

    new_headless: 仅在**新建** context 时生效；若缓存中已有同 storage_key 的 context 则直接复用
    （无法切换 headless）。同一 user_data 目录不可多开，与已有可见窗口并存时可能锁目录失败。
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError("playwright 未安装")

    opts = (
        browser_options
        if browser_options is not None
        else _default_browser_options()
    )
    key = _storage_key(profile_dir, opts)

    # CDP 模式：不启浏览器，连 用户已开的 Chrome，profile_dir 仅作缓存键
    if _cdp_enabled():
        async with _lock:
            existing = _contexts.get(key)
            if existing is not None:
                try:
                    if hasattr(existing, "is_closed") and existing.is_closed():
                        _contexts.pop(key, None)
                    else:
                        _profile_active_key[profile_dir] = key
                        return existing, False
                except Exception:
                    _contexts.pop(key, None)
        return await _acquire_cdp_context(profile_dir, key)

    global _pw_instance
    to_close_mismatch: Any = None
    async with _lock:
        if not _pw_instance:
            _pw_instance = await async_playwright().__aenter__()
        old_key = _profile_active_key.get(profile_dir)
        if old_key is not None and old_key != key:
            to_close_mismatch = _contexts.pop(old_key, None)
            _context_headless.pop(old_key, None)
            _profile_active_key.pop(profile_dir, None)
    if to_close_mismatch:
        try:
            await to_close_mismatch.close()
        except Exception:
            pass

    async with _lock:
        existing = _contexts.get(key)
        if existing:
            try:
                if hasattr(existing, "is_closed") and existing.is_closed():
                    _contexts.pop(key, None)
                    _context_headless.pop(key, None)
                    if _profile_active_key.get(profile_dir) == key:
                        _profile_active_key.pop(profile_dir, None)
                else:
                    _ = len(getattr(existing, "pages", []) or [])
                    _profile_active_key[profile_dir] = key
                    return existing, False
            except Exception:
                _contexts.pop(key, None)
                _context_headless.pop(key, None)
                if _profile_active_key.get(profile_dir) == key:
                    _profile_active_key.pop(profile_dir, None)

    # channel 优先级：opts 里显式指定 > 环境变量 > 自定义路径 > 默认 bundled Chromium
    channel_override = opts.get("channel") or _BROWSER_CHANNEL or ""

    launch_kwargs: Dict[str, Any] = {
        "headless": bool(new_headless),
        "viewport": opts.get("viewport") or {"width": 1280, "height": 800},
        "locale": "zh-CN",
        "permissions": ["geolocation"],
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=ThirdPartyCookieBlocking,SameSiteByDefaultCookies,CookiesWithoutSameSiteMustBeSecure",
        ],
    }
    # 使用真实 Chrome channel 时不覆盖 UA，让浏览器用自身真实 UA，减少指纹不匹配风险
    if not channel_override:
        launch_kwargs["user_agent"] = opts["user_agent"]
    if opts.get("proxy"):
        launch_kwargs["proxy"] = opts["proxy"]
    if channel_override:
        launch_kwargs["channel"] = channel_override
    elif opts.get("executable_path") and Path(str(opts.get("executable_path"))).exists():
        launch_kwargs["executable_path"] = str(opts.get("executable_path"))
    elif _CHROMIUM_PATH and Path(_CHROMIUM_PATH).exists():
        launch_kwargs["executable_path"] = _CHROMIUM_PATH

    # Playwright 默认会带 --disable-extensions，环境过「干净」易与日常 Chrome 指纹不一致。
    # 使用真实 Chrome channel 时去掉该默认项，允许沿用 profile 内扩展（更接近手动浏览器）。
    _ida: List[str] = ["--enable-automation"]
    if channel_override and os.environ.get("PLAYWRIGHT_KEEP_DISABLE_EXTENSIONS", "").strip() != "1":
        _ida.append("--disable-extensions")
    launch_kwargs["ignore_default_args"] = _ida

    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    logger.info(
        "[BROWSER] launch persistent context profile=%s headless=%s channel=%s executable=%s viewport=%s",
        profile_dir[-80:],
        bool(new_headless),
        launch_kwargs.get("channel") or "",
        "set" if launch_kwargs.get("executable_path") else "",
        launch_kwargs.get("viewport"),
    )
    ctx = await _pw_instance.chromium.launch_persistent_context(
        profile_dir, **launch_kwargs,
    )
    try:
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "try{if(!window.chrome)window.chrome={};if(!window.chrome.runtime)window.chrome.runtime={};}catch(e){}"
        )
    except Exception as e:
        logger.debug("persistent context add_init_script: %s", e)
    async with _lock:
        _contexts[key] = ctx
        _context_headless[key] = bool(new_headless)
        _profile_active_key[profile_dir] = key
    return ctx, True


async def _drop_cached_context(
    profile_dir: str,
    ctx: Any = None,
    *,
    browser_options: Optional[Dict[str, Any]] = None,
) -> None:
    """Best-effort remove/close cached context for a profile (matched by storage_key 或 ctx 实例)。"""
    to_close: Any = None
    try:
        async with _lock:
            if ctx is not None:
                for k, c in list(_contexts.items()):
                    pref = k.split("\0", 1)[0]
                    if c is ctx and pref == profile_dir:
                        to_close = _contexts.pop(k, None)
                        _context_headless.pop(k, None)
                        if _profile_active_key.get(profile_dir) == k:
                            _profile_active_key.pop(profile_dir, None)
                        break
            else:
                sk: Optional[str] = None
                if browser_options is not None:
                    sk = _storage_key(profile_dir, browser_options)
                if sk is None:
                    sk = _profile_active_key.get(profile_dir)
                if sk and sk in _contexts:
                    to_close = _contexts.pop(sk, None)
                    _context_headless.pop(sk, None)
                    if _profile_active_key.get(profile_dir) == sk:
                        _profile_active_key.pop(profile_dir, None)
    except Exception:
        pass
    try:
        if to_close is not None:
            if getattr(to_close, "_lobster_cdp_external", False):
                logger.info("[BROWSER][CDP] skip close (external, user-owned browser)")
            else:
                await to_close.close()
    except Exception:
        pass


async def _get_page_with_reacquire(
    profile_dir: str,
    ctx: Any,
    *,
    new_headless_on_recreate: bool = False,
    browser_options: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, Any]:
    """Get page; if context is closed, recreate context once and retry.

    new_headless_on_recreate: 仅重建 context 时生效；与创作者同步的 headless 策略一致。
    发布/登录路径使用默认 False（有头）。
    """
    opts = (
        browser_options
        if browser_options is not None
        else _default_browser_options()
    )
    try:
        page = await _get_page_and_focus(ctx)
        return page, ctx
    except Exception as e:
        msg = str(e).lower()
        if (
            "target page, context or browser has been closed" not in msg
            and "has been closed" not in msg
            and "targetclosederror" not in msg
        ):
            raise
        logger.warning("[BROWSER] stale context detected, recreating: profile=%s err=%s", profile_dir, e)
        await _drop_cached_context(profile_dir, ctx, browser_options=opts)
        new_ctx, _ = await _acquire_context(
            profile_dir,
            new_headless=new_headless_on_recreate,
            browser_options=opts,
        )
        page = await _get_page_and_focus(new_ctx)
        return page, new_ctx


async def _ensure_visible_interactive_context(
    profile_dir: str,
    browser_options: Optional[Dict[str, Any]] = None,
) -> None:
    """若池中仅有无头 context（如刚跑过作品同步），关闭之，以便后续以有头方式打开（发布/扫码登录）。"""
    # CDP 模式下浏览器由用户启动，永远可见可交互，无需替换
    if _cdp_enabled():
        return
    opts = (
        browser_options
        if browser_options is not None
        else _default_browser_options()
    )
    sk = _storage_key(profile_dir, opts)
    async with _lock:
        cached = _contexts.get(sk)
        is_h = _context_headless.get(sk, False)
    if cached and is_h:
        logger.info("[BROWSER] replace headless pool context with visible (publish/login): profile=%s", profile_dir)
        await _drop_cached_context(profile_dir, cached, browser_options=opts)


async def _ensure_headless_background_context(
    profile_dir: str,
    browser_options: Optional[Dict[str, Any]] = None,
) -> None:
    """Replace a visible cached context with a headless one for background protocol work."""
    if _cdp_enabled():
        return
    opts = (
        browser_options
        if browser_options is not None
        else _default_browser_options()
    )
    sk = _storage_key(profile_dir, opts)
    async with _lock:
        cached = _contexts.get(sk)
        is_h = _context_headless.get(sk, False)
    if cached and not is_h:
        logger.info("[BROWSER] replace visible pool context with headless (douyin protocol): profile=%s", profile_dir)
        await _drop_cached_context(profile_dir, cached, browser_options=opts)


def _setup_auto_close(
    ctx: Any,
    profile_dir: str,
    page: Any,
    *,
    browser_options: Optional[Dict[str, Any]] = None,
):
    """用户关闭窗口后释放池内 context。

    Facebook / Meta OAuth 常会再开标签页或弹出「选图验证」窗口；若任一子页关闭就整 context.close()，
    会清空持久化 Cookie，表现为「验证完又回到登录」循环。因此仅在**所有页面都关闭**后再释放。
    """
    # CDP 模式：浏览器由用户拥有，脚本不负责生命周期，页关闭不触发任何清理
    if getattr(ctx, "_lobster_cdp_external", False):
        return
    opts = (
        browser_options
        if browser_options is not None
        else _default_browser_options()
    )
    sk = _storage_key(profile_dir, opts)

    async def _close_pool():
        try:
            await ctx.close()
        except Exception:
            pass
        try:
            async with _lock:
                if _contexts.get(sk) is ctx:
                    _contexts.pop(sk, None)
                    _context_headless.pop(sk, None)
                    if _profile_active_key.get(profile_dir) == sk:
                        _profile_active_key.pop(profile_dir, None)
        except Exception:
            pass

    async def _maybe_close_after_last_page() -> None:
        await asyncio.sleep(0.35)
        try:
            n = len(ctx.pages)
        except Exception:
            n = 0
        if n > 0:
            logger.info(
                "[BROWSER] 某标签已关闭，仍有 %s 个页面，保留会话与 Cookie（profile …%s）",
                n,
                str(profile_dir)[-50:],
            )
            return
        logger.info("[BROWSER] 所有页面已关闭，释放 context（profile …%s）", str(profile_dir)[-50:])
        await _close_pool()

    def _schedule_maybe_close() -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                return
        try:
            loop.create_task(_maybe_close_after_last_page())
        except Exception:
            pass

    wired: set = getattr(ctx, "_lobster_wired_page_ids", None)
    if wired is None:
        wired = set()
        setattr(ctx, "_lobster_wired_page_ids", wired)

    def _wire_page_once(p: Any) -> None:
        try:
            pid = id(p)
            if pid in wired:
                return
            wired.add(pid)
            p.on("close", lambda _p=None: _schedule_maybe_close())
        except Exception:
            pass

    _wire_page_once(page)
    if getattr(ctx, "_lobster_auto_close_registered", False):
        return
    setattr(ctx, "_lobster_auto_close_registered", True)

    try:
        for p in list(getattr(ctx, "pages", []) or []):
            _wire_page_once(p)
    except Exception:
        pass
    try:
        ctx.on("page", lambda p: _wire_page_once(p))
    except Exception:
        pass


async def _get_page_and_focus(ctx: Any) -> Any:
    """Get first page (or create one) and bring to front."""
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    except Exception:
        # Let caller decide whether to reacquire context.
        raise
    await _bring_window_to_front(page)
    return page


async def _bring_window_to_front(page: Any) -> None:
    """Aggressively bring the browser window to OS foreground (Windows-friendly)."""
    try:
        await page.bring_to_front()
    except Exception:
        pass
    try:
        cdp = await page.context.new_cdp_session(page)
        try:
            target = await cdp.send("Browser.getWindowForTarget")
            wid = target.get("windowId")
            if wid:
                await cdp.send("Browser.setWindowBounds", {
                    "windowId": wid,
                    "bounds": {"windowState": "normal"},
                })
                await cdp.send("Browser.setWindowBounds", {
                    "windowId": wid,
                    "bounds": {"windowState": "maximized"},
                })
        finally:
            await cdp.detach()
    except Exception:
        pass


# ── Public API ────────────────────────────────────────────────────


async def open_login_browser(
    profile_dir: str,
    login_url: str,
    platform: str,
    timeout_sec: int = 120,
    browser_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Open browser for user to scan QR code. Returns immediately."""
    from .drivers import DRIVERS

    driver_cls = DRIVERS.get(platform)
    if not driver_cls:
        return {"logged_in": False, "message": f"不支持的平台: {platform}"}

    opts = browser_options if browser_options is not None else _default_browser_options()
    await _ensure_visible_interactive_context(profile_dir, browser_options=opts)
    ctx, created_new = await _acquire_context(
        profile_dir, new_headless=False, browser_options=opts
    )
    try:
        page, ctx = await _get_page_with_reacquire(profile_dir, ctx, browser_options=opts)
        await page.goto(
            login_url,
            wait_until="domcontentloaded",
            timeout=navigation_timeout_ms(30000),
        )
        logger.info("Login browser opened for %s at %s", platform, login_url)
        _setup_auto_close(ctx, profile_dir, page, browser_options=opts)
        return {"logged_in": False, "message": "浏览器已打开，请在窗口内扫码登录（不会自动关闭）"}
    except Exception as e:
        if created_new:
            await _drop_cached_context(profile_dir, ctx, browser_options=opts)
        return {"logged_in": False, "message": str(e)}


async def open_url_in_persistent_chromium(
    profile_dir: str,
    url: str,
    browser_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """在持久化 Chromium 中打开任意 URL（无平台 driver）。用于 YouTube OAuth 等与发布同源固定浏览器。"""
    opts = browser_options if browser_options is not None else _default_browser_options()
    ctx: Any = None
    created_new = False
    try:
        await _ensure_visible_interactive_context(profile_dir, browser_options=opts)
        ctx, created_new = await _acquire_context(
            profile_dir, new_headless=False, browser_options=opts
        )
        page, ctx = await _get_page_with_reacquire(profile_dir, ctx, browser_options=opts)
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=navigation_timeout_ms(120000),
        )
        try:
            host = urlparse(url).netloc or ""
        except Exception:
            host = ""
        logger.info(
            "[BROWSER] youtube/oauth persistent Chromium url_host=%s profile=%s",
            host[:120],
            profile_dir[:100],
        )
        _setup_auto_close(ctx, profile_dir, page, browser_options=opts)
        return {
            "ok": True,
            "message": "已在龙虾内置 Chromium 中打开（与发布「打开浏览器」相同引擎与可执行文件来源）",
        }
    except Exception as e:
        logger.exception("open_url_in_persistent_chromium failed")
        if created_new and ctx is not None:
            try:
                await _drop_cached_context(profile_dir, ctx, browser_options=opts)
            except Exception:
                pass
        return {"ok": False, "message": _browser_open_error_message(e)}


async def open_and_check_browser(
    profile_dir: str,
    login_url: str,
    platform: str,
    browser_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Open browser, bring to front, and check login status. Returns immediately."""
    from .drivers import DRIVERS

    driver_cls = DRIVERS.get(platform)
    if not driver_cls:
        return {"logged_in": False, "message": f"不支持的平台: {platform}"}

    opts = browser_options if browser_options is not None else _default_browser_options()
    await _ensure_visible_interactive_context(profile_dir, browser_options=opts)
    ctx, created_new = await _acquire_context(
        profile_dir, new_headless=False, browser_options=opts
    )
    try:
        page, ctx = await _get_page_with_reacquire(profile_dir, ctx, browser_options=opts)

        # 先打开该平台登录入口，避免持久化上下文复用时仍停留在其它站点（例如上次开过抖音）
        if login_url:
            try:
                await page.goto(
                    login_url,
                    wait_until="domcontentloaded",
                    timeout=navigation_timeout_ms(30000),
                )
                await asyncio.sleep(1)
            except Exception:
                pass

        driver = driver_cls()
        logged_in = await driver.check_login(page, navigate=True)

        if not logged_in:
            try:
                await page.goto(
                    login_url,
                    wait_until="domcontentloaded",
                    timeout=navigation_timeout_ms(30000),
                )
            except Exception:
                pass

        _setup_auto_close(ctx, profile_dir, page, browser_options=opts)

        if logged_in:
            return {"logged_in": True, "message": "浏览器已打开，当前已登录"}
        return {"logged_in": False, "message": "浏览器已打开，请扫码登录"}
    except Exception as e:
        if created_new:
            await _drop_cached_context(profile_dir, ctx, browser_options=opts)
        return {"logged_in": False, "message": str(e)}


async def check_browser_login(
    profile_dir: str,
    platform: str,
    browser_options: Optional[Dict[str, Any]] = None,
) -> bool:
    """Check login status. Opens a context if needed (persistent cookies)."""
    from .drivers import DRIVERS

    driver_cls = DRIVERS.get(platform)
    if not driver_cls:
        return False

    opts = browser_options if browser_options is not None else _default_browser_options()
    key = _storage_key(profile_dir, opts)

    async with _lock:
        ctx = _contexts.get(key)
        recreate_headless = bool(_context_headless.get(key, False))

    if ctx:
        try:
            if hasattr(ctx, "is_closed") and ctx.is_closed():
                ctx = None
        except Exception:
            ctx = None

    if not ctx:
        if not Path(profile_dir).exists():
            return False
        try:
            # 无池内 context 时新建：默认无头，避免仅「检测登录」就弹出窗口
            ctx, _ = await _acquire_context(
                profile_dir, new_headless=True, browser_options=opts
            )
            recreate_headless = True
        except Exception:
            return False

    try:
        page, ctx = await _get_page_with_reacquire(
            profile_dir,
            ctx,
            new_headless_on_recreate=recreate_headless,
            browser_options=opts,
        )
        driver = driver_cls()
        logged_in = await driver.check_login(page, navigate=True)
        if logged_in:
            try:
                await page.bring_to_front()
            except Exception:
                pass
        return logged_in
    except Exception:
        return False


async def open_douyin_front_browser(
    profile_dir: str,
    url: str = "https://www.douyin.com/jingxuan",
    browser_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Open the Douyin consumer site in the account's persistent browser profile."""
    opts = browser_options if browser_options is not None else _default_browser_options()
    await _ensure_visible_interactive_context(profile_dir, browser_options=opts)
    ctx, created_new = await _acquire_context(
        profile_dir, new_headless=False, browser_options=opts
    )
    try:
        page, ctx = await _get_page_with_reacquire(profile_dir, ctx, browser_options=opts)
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=navigation_timeout_ms(45000),
        )
        try:
            await page.wait_for_timeout(1000)
        except Exception:
            await asyncio.sleep(1)
        state = await _read_douyin_front_login_state(page, ctx)
        _setup_auto_close(ctx, profile_dir, page, browser_options=opts)
        logged_in = bool(state.get("logged_in"))
        return {
            "ok": True,
            "logged_in": logged_in,
            "message": "已打开抖音前台页面，当前账号已登录。" if logged_in else "已打开抖音前台页面，请在窗口里完成登录。",
            **state,
        }
    except Exception as e:
        if created_new:
            await _drop_cached_context(profile_dir, ctx, browser_options=opts)
        return {"ok": False, "logged_in": False, "message": str(e)}


async def _read_douyin_front_login_state(page: Any, ctx: Any) -> Dict[str, Any]:
    cookies = []
    try:
        cookies = await ctx.cookies(["https://www.douyin.com"])
    except Exception:
        cookies = []
    has_session_cookie = any(
        str(cookie.get("name", "")) in {"sessionid", "sessionid_ss", "sid_guard"}
        and str(cookie.get("value", "")).strip()
        for cookie in cookies
        if isinstance(cookie, dict)
    )
    state: Dict[str, Any] = {}
    try:
        state = await page.evaluate(
            """
            () => {
              const compact = (value) => String(value || '').replace(/\\s+/g, '');
              const bodyText = compact(document.body && document.body.innerText || '');
              const loginTexts = ['登录', '立即登录', '扫码登录', '去登录', '手机号登录', '验证码登录'];
              const loginPrompt = Array.from(document.querySelectorAll('button, a, div, span'))
                .some((el) => {
                  const text = compact(el.innerText || el.textContent || '');
                  return text && loginTexts.includes(text);
                });
              const qrLoginVisible = Array.from(document.querySelectorAll('img, canvas, div'))
                .some((el) => {
                  const className = compact(el.className || '').toLowerCase();
                  const alt = compact(el.getAttribute && el.getAttribute('alt') || '').toLowerCase();
                  const text = compact(el.innerText || '');
                  return className.includes('qrcode')
                    || className.includes('qr-code')
                    || alt.includes('qr')
                    || text.includes('扫码登录')
                    || text.includes('二维码');
                });
              const profileHints = ['退出登录', '账号与安全', '我的作品', '我的喜欢', '获赞', '粉丝', '关注']
                .some((text) => bodyText.includes(text));
              const profileLinkCount = document.querySelectorAll('a[href*="/user/"]').length;
              return {
                loginPrompt,
                qrLoginVisible,
                profileHints,
                profileLinkCount,
                path: location.pathname || '',
              };
            }
            """
        )
    except Exception:
        state = {}
    login_prompt = bool(state.get("loginPrompt"))
    qr_login_visible = bool(state.get("qrLoginVisible"))
    logged_in = bool(has_session_cookie and not login_prompt and not qr_login_visible)
    return {
        "logged_in": logged_in,
        "cookie": bool(has_session_cookie),
        "login_prompt": login_prompt,
        "qr_login_visible": qr_login_visible,
        "profile_hints": bool(state.get("profileHints")),
        "profile_link_count": int(state.get("profileLinkCount", 0) or 0),
        "path": str(state.get("path", "") or ""),
    }


async def check_douyin_front_login(
    profile_dir: str,
    url: str = "https://www.douyin.com/jingxuan",
    browser_options: Optional[Dict[str, Any]] = None,
    *,
    headless: bool = True,
) -> Dict[str, Any]:
    """Check Douyin consumer-site login state without using creator-center routes."""
    opts = browser_options if browser_options is not None else _default_browser_options()
    if not Path(profile_dir).exists():
        return {"logged_in": False, "message": "账号浏览器目录不存在，请先打开登录。", "cookie": False}
    if not headless:
        await _ensure_visible_interactive_context(profile_dir, browser_options=opts)
    ctx, created_new = await _acquire_context(
        profile_dir,
        new_headless=bool(headless),
        browser_options=opts,
    )
    try:
        page, ctx = await _get_page_with_reacquire(
            profile_dir,
            ctx,
            new_headless_on_recreate=bool(headless),
            browser_options=opts,
        )
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=navigation_timeout_ms(45000),
        )
        try:
            await page.wait_for_timeout(2500)
        except Exception:
            await asyncio.sleep(2.5)
        state = await _read_douyin_front_login_state(page, ctx)
        logged_in = bool(state.get("logged_in"))
        if not headless:
            _setup_auto_close(ctx, profile_dir, page, browser_options=opts)
        return {
            "logged_in": logged_in,
            "message": "抖音前台已登录" if logged_in else "抖音前台未检测到登录，请点击“打开登录”完成扫码。",
            **state,
        }
    except Exception as e:
        if created_new:
            await _drop_cached_context(profile_dir, ctx, browser_options=opts)
        return {"logged_in": False, "message": str(e), "cookie": False}


def _extract_douyin_aweme_id(url: str) -> str:
    import re

    text = str(url or "")
    for pattern in (r"/video/(\d+)", r"[?&]modal_id=(\d+)", r"/note/(\d+)"):
        m = re.search(pattern, text)
        if m:
            return m.group(1)
    return ""


def _douyin_protocol_platform_params() -> Dict[str, str]:
    return {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "pc_client_type": "1",
        "update_version_code": "170400",
        "version_code": "170400",
        "version_name": "17.4.0",
        "cookie_enabled": "true",
        "screen_width": "1707",
        "screen_height": "960",
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Edge",
        "browser_version": "125.0.0.0",
        "browser_online": "true",
        "engine_name": "Blink",
        "engine_version": "125.0.0.0",
        "os_name": "Windows",
        "os_version": "10",
        "cpu_core_num": "32",
        "device_memory": "8",
        "platform": "PC",
        "downlink": "10",
        "effective_type": "4g",
        "round_trip_time": "100",
    }


def _normalize_douyin_protocol_comment(comment: Dict[str, Any]) -> Dict[str, Any]:
    user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
    sec_uid = str(user.get("sec_uid", "") or "").strip()
    profile_url = f"https://www.douyin.com/user/{sec_uid}" if sec_uid else ""
    return {
        "comment_id": str(comment.get("cid", "") or "").strip(),
        "aweme_id": str(comment.get("aweme_id", "") or "").strip(),
        "text": str(comment.get("text", "") or "").strip(),
        "create_time": int(comment.get("create_time", 0) or 0),
        "digg_count": int(comment.get("digg_count", 0) or 0),
        "reply_comment_total": int(comment.get("reply_comment_total", 0) or 0),
        "nickname": str(user.get("nickname", "") or "").strip(),
        "uid": str(user.get("uid", "") or "").strip(),
        "sec_uid": sec_uid,
        "profile_url": profile_url,
        "avatar_url": str(
            ((user.get("avatar_thumb") or {}).get("url_list") or [""])[0]
            if isinstance(user.get("avatar_thumb"), dict)
            else ""
        ).strip(),
    }


def _collect_douyin_comments_protocol_sync(
    auth: Dict[str, Any],
    video_url: str,
    max_comments: int,
) -> Dict[str, Any]:
    import requests
    from backend.douyin_protocol_runtime import (
        HeaderBuilder,
        HeaderType,
        generate_a_bogus,
        generate_msToken,
        generate_webid,
        splice_url,
    )

    requests.packages.urllib3.disable_warnings()
    aweme_id = _extract_douyin_aweme_id(video_url)
    if not aweme_id:
        raise RuntimeError("无法从视频地址解析 aweme_id")
    cookie_map = auth.get("cookie") if isinstance(auth.get("cookie"), dict) else {}
    s_v_web_id = str(cookie_map.get("s_v_web_id", "") or "").strip()
    if not s_v_web_id:
        raise RuntimeError("协议模式缺少 s_v_web_id，请先在抖音前台打开账号页面刷新登录态")
    ms_token = str(cookie_map.get("msToken", "") or "").strip() or generate_msToken()
    cookie_map["msToken"] = ms_token
    referer = f"https://www.douyin.com/video/{aweme_id}"
    comments: List[Dict[str, Any]] = []
    cursor = 0
    has_more = 1
    while has_more == 1 and len(comments) < max_comments:
        count = min(20, max(1, max_comments - len(comments)))
        params = _douyin_protocol_platform_params()
        params.update({
            "aweme_id": aweme_id,
            "cursor": str(max(0, int(cursor or 0))),
            "count": str(count),
            "item_type": "0",
            "whale_cut_token": "",
            "cut_version": "1",
            "rcFT": "",
        })
        params["webid"] = generate_webid(type("Auth", (), {"cookie_str": auth.get("cookie_str", "")})(), referer)
        params["verifyFp"] = s_v_web_id
        params["fp"] = s_v_web_id
        params["msToken"] = ms_token
        query = splice_url(params)
        params["a_bogus"] = generate_a_bogus(query, "")
        headers = HeaderBuilder.build(HeaderType.GET)
        headers.set_referer(referer)
        response = requests.get(
            "https://www.douyin.com/aweme/v1/web/comment/list/",
            headers=headers.get(),
            cookies=cookie_map,
            params=params,
            verify=False,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        status_code = int(payload.get("status_code", 0) or 0)
        if status_code != 0:
            raise RuntimeError(
                f"评论协议接口返回异常 status_code={status_code}, status_msg={payload.get('status_msg') or '-'}"
            )
        page_comments = payload.get("comments") or []
        if not isinstance(page_comments, list) or not page_comments:
            break
        for item in page_comments:
            if isinstance(item, dict):
                comments.append(_normalize_douyin_protocol_comment(item))
                if len(comments) >= max_comments:
                    break
        has_more = int(payload.get("has_more", 0) or 0)
        cursor = int(payload.get("cursor", 0) or 0)
        if has_more == 1 and len(comments) < max_comments:
            time.sleep(0.35)
    return {
        "aweme_id": aweme_id,
        "video_url": referer,
        "comments": comments,
        "count": len(comments),
    }


async def _extract_douyin_protocol_auth_from_context(page: Optional[Any], ctx: Any) -> Dict[str, Any]:
    try:
        cookies = await ctx.cookies(["https://www.douyin.com"])
    except Exception:
        cookies = []
    cookie_map = {
        str(item.get("name", "")).strip(): str(item.get("value", "")).strip()
        for item in cookies
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    }
    crypt_sdk_raw = ""
    web_protect_raw = ""
    if page is not None:
        try:
            crypt_sdk_raw = await page.evaluate('() => localStorage.getItem("security-sdk/s_sdk_crypt_sdk")')
        except Exception:
            crypt_sdk_raw = ""
        try:
            web_protect_raw = await page.evaluate('() => localStorage.getItem("security-sdk/s_sdk_sign_data_key/web_protect")')
        except Exception:
            web_protect_raw = ""
    if not cookie_map.get("msToken"):
        try:
            from backend.douyin_protocol_runtime import generate_msToken

            cookie_map["msToken"] = generate_msToken()
        except Exception:
            pass
    return {
        "cookie": cookie_map,
        "cookie_str": "; ".join(f"{key}={value}" for key, value in cookie_map.items()),
        "crypt_sdk_raw": str(crypt_sdk_raw or ""),
        "web_protect_raw": str(web_protect_raw or ""),
    }


def _douyin_protocol_cookie_ready(auth: Dict[str, Any]) -> Tuple[bool, bool]:
    cookie_map = auth.get("cookie") if isinstance(auth.get("cookie"), dict) else {}
    has_session_cookie = any(
        str(cookie_map.get(name, "") or "").strip()
        for name in ("sessionid", "sessionid_ss", "sid_guard")
    )
    has_web_id = bool(str(cookie_map.get("s_v_web_id", "") or "").strip())
    return has_session_cookie, has_web_id


def _decode_douyin_storage_payload(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
    text = text.strip()
    if not text:
        return {}
    try:
        outer = json.loads(text)
    except Exception:
        return {}
    if isinstance(outer, dict) and isinstance(outer.get("data"), str):
        try:
            inner = json.loads(outer["data"])
        except Exception:
            return {}
        return inner if isinstance(inner, dict) else {}
    return outer if isinstance(outer, dict) else {}


def _load_douyin_im_proto_modules() -> Tuple[Any, Any]:
    global _DOUYIN_IM_PROTO
    if _DOUYIN_IM_PROTO is not None:
        return _DOUYIN_IM_PROTO
    from backend.douyin_protocol_runtime import STATIC_DIR

    def _load(module_name: str, path: Path) -> Any:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"failed to load douyin protobuf module: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    _DOUYIN_IM_PROTO = (
        _load("lobster_douyin_im_request_pb2", STATIC_DIR / "Request_pb2.py"),
        _load("lobster_douyin_im_response_pb2", STATIC_DIR / "Response_pb2.py"),
    )
    return _DOUYIN_IM_PROTO


def _extract_douyin_sec_user_id(profile_url: str) -> str:
    text = str(profile_url or "").strip()
    if not text:
        return ""
    if "/user/" in text:
        return text.split("/user/", 1)[1].split("?", 1)[0].split("/", 1)[0].strip()
    if text.startswith("MS4") or text.startswith("MS"):
        return text
    return ""


def _douyin_im_auth_material(auth: Dict[str, Any]) -> Dict[str, Any]:
    from backend.douyin_protocol_runtime import generate_msToken

    cookie_map = dict(auth.get("cookie") if isinstance(auth.get("cookie"), dict) else {})
    cookie_map = {str(k).strip(): str(v).strip() for k, v in cookie_map.items() if str(k).strip()}
    if not cookie_map.get("msToken"):
        cookie_map["msToken"] = generate_msToken()
    crypt_sdk = _decode_douyin_storage_payload(auth.get("crypt_sdk_raw"))
    web_protect = _decode_douyin_storage_payload(auth.get("web_protect_raw"))
    material = {
        "cookie": cookie_map,
        "cookie_str": "; ".join(f"{key}={value}" for key, value in cookie_map.items()),
        "ms_token": cookie_map.get("msToken", ""),
        "private_key": str(crypt_sdk.get("ec_privateKey", "") or "").strip(),
        "ticket": str(web_protect.get("ticket", "") or "").strip(),
        "ts_sign": str(web_protect.get("ts_sign", "") or "").strip(),
        "client_cert": str(web_protect.get("client_cert", "") or "").strip(),
        "my_uid": None,
    }
    missing = [
        name for name, value in (
            ("ec_privateKey", material["private_key"]),
            ("ticket", material["ticket"]),
            ("ts_sign", material["ts_sign"]),
            ("client_cert", material["client_cert"]),
            ("s_v_web_id", cookie_map.get("s_v_web_id", "")),
        )
        if not str(value or "").strip()
    ]
    if missing:
        raise RuntimeError("私信协议缺少登录签名材料：" + "、".join(missing))
    logger.info(
        "[DOUYIN-IM] auth material ready cookies=%s has_private_key=%s has_ticket=%s has_ts_sign=%s cert_len=%s webid=%s",
        ",".join(sorted(cookie_map.keys())),
        bool(material["private_key"]),
        bool(material["ticket"]),
        bool(material["ts_sign"]),
        len(material["client_cert"]),
        bool(cookie_map.get("s_v_web_id")),
    )
    return material


def _douyin_im_auth_proxy(material: Dict[str, Any]) -> Any:
    return type(
        "DouyinImAuth",
        (),
        {
            "cookie": material["cookie"],
            "cookie_str": material["cookie_str"],
            "ticket": material["ticket"],
            "ts_sign": material["ts_sign"],
            "private_key": material["private_key"],
        },
    )()


def _douyin_im_with_a_bogus(params: Dict[str, Any], data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from backend.douyin_protocol_runtime import generate_a_bogus, splice_url

    params = dict(params)
    params["a_bogus"] = generate_a_bogus(splice_url(params), splice_url(data or {}) if data else "")
    return params


def _douyin_im_public_error_message(exc: BaseException) -> str:
    raw = str(exc or "").strip()
    if "444 Client Error" in raw and "douyin.com" in raw:
        return "抖音接口拒绝本次协议请求，账号环境可能被风控，请重新打开登录页刷新后再试。"
    if "Client Error" in raw and "douyin.com" in raw:
        return "抖音接口返回异常，请稍后重试或重新打开登录后再试。"
    if " for url: " in raw:
        raw = raw.split(" for url: ", 1)[0].strip()
    if len(raw) > 240:
        raw = raw[:240] + "..."
    return raw or "私信发送失败，请稍后重试。"


def _douyin_im_query_current_uid(material: Dict[str, Any]) -> int:
    import requests
    from backend.douyin_protocol_runtime import HeaderBuilder, HeaderType, generate_webid

    auth_proxy = _douyin_im_auth_proxy(material)
    params = _douyin_protocol_platform_params()
    params["webid"] = generate_webid(auth_proxy, "https://www.douyin.com/")
    params["msToken"] = material["ms_token"]
    params["verifyFp"] = material["cookie"]["s_v_web_id"]
    params["fp"] = material["cookie"]["s_v_web_id"]
    params = _douyin_im_with_a_bogus(params)
    headers = HeaderBuilder.build(HeaderType.GET)
    headers.set_referer("https://www.douyin.com/")
    response = requests.get(
        "https://www.douyin.com/aweme/v1/web/query/user/",
        headers=headers.get(),
        cookies=material["cookie"],
        params=params,
        verify=False,
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if int(payload.get("status_code", 0) or 0) != 0:
        raise RuntimeError(f"查询当前抖音账号失败：{payload.get('status_msg') or payload}")
    return int(payload.get("user_uid") or 0)


def _douyin_im_query_target_user(material: Dict[str, Any], profile_url: str) -> Dict[str, Any]:
    import requests
    from backend.douyin_protocol_runtime import HeaderBuilder, HeaderType, generate_webid

    sec_user_id = _extract_douyin_sec_user_id(profile_url)
    if not sec_user_id:
        raise RuntimeError("客户缺少有效主页链接或 sec_user_id")
    profile_url = f"https://www.douyin.com/user/{sec_user_id}"
    auth_proxy = _douyin_im_auth_proxy(material)
    params = _douyin_protocol_platform_params()
    params.update({
        "publish_video_strategy_type": "2",
        "source": "channel_pc_web",
        "sec_user_id": sec_user_id,
        "personal_center_strategy": "1",
    })
    params["webid"] = generate_webid(auth_proxy, profile_url)
    params["msToken"] = material["ms_token"]
    params["verifyFp"] = material["cookie"]["s_v_web_id"]
    params["fp"] = material["cookie"]["s_v_web_id"]
    params = _douyin_im_with_a_bogus(params)
    headers = HeaderBuilder.build(HeaderType.GET)
    headers.set_referer(profile_url)
    response = requests.get(
        "https://www.douyin.com/aweme/v1/web/user/profile/other/",
        headers=headers.get(),
        cookies=material["cookie"],
        params=params,
        verify=False,
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if int(payload.get("status_code", 0) or 0) != 0:
        raise RuntimeError(f"查询客户主页失败：{payload.get('status_msg') or payload}")
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    if not str(user.get("uid", "") or "").strip():
        raise RuntimeError("客户主页失效或不存在，无法发送私信")
    return payload


def _douyin_im_build_request(material: Dict[str, Any], cmd: int) -> Any:
    from backend.douyin_protocol_runtime import HeaderBuilder, generate_webid

    RequestProto, _ = _load_douyin_im_proto_modules()
    request = RequestProto.Request()
    request.cmd = int(cmd)
    request.sequence_id = random.randint(10000, 11000)
    request.sdk_version = "1.1.3"
    request.token = material["ticket"]
    request.refer = 3
    request.inbox_type = 0
    request.build_number = "5fa6ff1:Detached: 5fa6ff1111fd53aafc4c753505d3c93daad74d27"
    request.device_id = "0"
    request.device_platform = "douyin_pc"
    request.headers["session_aid"] = "6383"
    request.headers["session_did"] = "0"
    request.headers["app_name"] = "douyin_pc"
    request.headers["priority_region"] = "cn"
    request.headers["user_agent"] = HeaderBuilder.ua
    request.headers["cookie_enabled"] = "true"
    request.headers["browser_language"] = "zh-CN"
    request.headers["browser_platform"] = "Win32"
    request.headers["browser_name"] = "Mozilla"
    request.headers["browser_version"] = HeaderBuilder.ua.split("Mozilla/")[-1]
    request.headers["browser_online"] = "true"
    request.headers["screen_width"] = "1707"
    request.headers["screen_height"] = "960"
    request.headers["referer"] = ""
    request.headers["timezone_name"] = "Etc/GMT-8"
    request.headers["deviceId"] = "0"
    # Keep this aligned with _reference/DouYin_Spider: proto headers use the
    # default generated webid rather than the authenticated root-page webid.
    request.headers["webid"] = generate_webid()
    request.headers["fp"] = material["cookie"]["s_v_web_id"]
    request.headers["is-retry"] = "0"
    request.auth_type = 4
    request.biz = "douyin_web"
    request.access = "web_sdk"
    request.ts_sign = material["ts_sign"]
    request.sdk_cert = base64.b64encode(material["client_cert"].encode("utf-8")).decode("utf-8")
    return request


def _douyin_im_parse_response(content: bytes) -> Dict[str, Any]:
    from google.protobuf.json_format import MessageToDict

    _, ResponseProto = _load_douyin_im_proto_modules()
    response = ResponseProto.Response()
    response.ParseFromString(content)
    return MessageToDict(response, preserving_proto_field_name=True)


def _douyin_im_response_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    notify = body.get("new_message_notify") if isinstance(body.get("new_message_notify"), dict) else {}
    message_body = notify.get("message") if isinstance(notify.get("message"), dict) else {}
    create_body = body.get("create_conversation_v2_body") if isinstance(body.get("create_conversation_v2_body"), dict) else {}
    conversations = create_body.get("conversation_info_list") if isinstance(create_body.get("conversation_info_list"), list) else []
    return {
        "cmd": payload.get("cmd"),
        "sequence_id": payload.get("sequence_id"),
        "message": payload.get("message"),
        "error_desc": payload.get("error_desc"),
        "inbox_type": payload.get("inbox_type"),
        "conversation_count": len(conversations),
        "notify_type": notify.get("notify_type"),
        "server_message_id": message_body.get("server_message_id"),
        "index_in_conversation": message_body.get("index_in_conversation"),
        "message_type": message_body.get("message_type"),
        "sender": message_body.get("sender"),
        "content": message_body.get("content"),
    }


def _douyin_im_create_conversation(material: Dict[str, Any], to_user_id: int) -> Dict[str, Any]:
    import requests
    from backend.douyin_protocol_runtime import HeaderBuilder, HeaderType, generate_req_sign

    if material.get("my_uid") is None:
        material["my_uid"] = _douyin_im_query_current_uid(material)
    my_uid = int(material["my_uid"])
    request = _douyin_im_build_request(material, 609)
    request.body.create_conversation_v2_body.conversation_type = 1
    request.body.create_conversation_v2_body.participants.extend([int(to_user_id), my_uid])
    request.reuqest_sign = generate_req_sign(
        {
            "sign_data": f"avatar_url=&idempotent_id=&name=&participants={int(to_user_id)},{my_uid}",
            "certType": "cookie",
            "scene": "web_protect",
        },
        material["private_key"],
    )
    headers = HeaderBuilder.build(HeaderType.PROTOBUF)
    headers.set_referer("https://www.douyin.com/")
    response = requests.post(
        "https://imapi.douyin.com/v2/conversation/create",
        headers=headers.get(),
        cookies=material["cookie"],
        data=request.SerializeToString(),
        verify=False,
        timeout=20,
    )
    response.raise_for_status()
    payload = _douyin_im_parse_response(response.content)
    logger.info("[DOUYIN-IM] create_conversation response=%s", json.dumps(_douyin_im_response_summary(payload), ensure_ascii=False))
    conversations = (
        payload.get("body", {})
        .get("create_conversation_v2_body", {})
        .get("conversation_info_list", [])
    )
    if not conversations:
        raise RuntimeError("创建私信会话失败")
    conversation = conversations[0]
    return {
        "conversation_id": conversation.get("conversation_id", ""),
        "conversation_short_id": conversation.get("conversation_short_id", ""),
        "ticket": conversation.get("ticket", ""),
    }


def _douyin_im_send_text_message(
    material: Dict[str, Any],
    conversation_id: str,
    conversation_short_id: str,
    ticket: str,
    message: str,
) -> Dict[str, Any]:
    import requests
    from backend.douyin_protocol_runtime import HeaderBuilder, HeaderType, generate_msToken, generate_req_sign

    RequestProto, _ = _load_douyin_im_proto_modules()
    request = _douyin_im_build_request(material, 100)
    client_message_id = str(uuid.uuid4())
    conversation_short_id_int = int(str(conversation_short_id or "0").strip() or "0")
    msg_content = {
        "mention_users": [],
        "aweType": 700,
        "richTextInfos": [],
        "text": str(message or ""),
    }
    content_text = json.dumps(msg_content, ensure_ascii=False, separators=(",", ":"))
    request.body.send_message_body.conversation_id = str(conversation_id)
    request.body.send_message_body.conversation_type = 1
    request.body.send_message_body.conversation_short_id = conversation_short_id_int
    request.body.send_message_body.content = content_text
    request.body.send_message_body.ext.append(RequestProto.ExtValue(key="s:client_message_id", value=client_message_id))
    request.body.send_message_body.ext.append(RequestProto.ExtValue(key="s:stime", value=str(int(time.time() * 1000))))
    request.body.send_message_body.ext.append(RequestProto.ExtValue(key="s:mentioned_users", value=""))
    request.body.send_message_body.message_type = 7
    request.body.send_message_body.ticket = str(ticket)
    request.body.send_message_body.client_message_id = client_message_id
    request.reuqest_sign = generate_req_sign(
        {
            "sign_data": f"content={content_text}&conversation_id={conversation_id}&conversation_short_id={conversation_short_id}",
            "certType": "cookie",
            "scene": "web_protect",
        },
        material["private_key"],
    )
    params = {
        "verifyFp": material["cookie"]["s_v_web_id"],
        "fp": material["cookie"]["s_v_web_id"],
        "msToken": generate_msToken(),
    }
    params = _douyin_im_with_a_bogus(params)
    headers = HeaderBuilder.build(HeaderType.PROTOBUF)
    headers.set_referer("https://www.douyin.com/")
    logger.info(
        "[DOUYIN-IM] send_message request conversation_id=%s short_id=%s ticket=%s client_message_id=%s content_len=%s req_sign_len=%s",
        str(conversation_id or "")[:80],
        str(conversation_short_id or ""),
        bool(str(ticket or "").strip()),
        client_message_id,
        len(str(message or "")),
        len(str(request.reuqest_sign or "")),
    )
    response = requests.post(
        "https://imapi.douyin.com/v1/message/send",
        params=params,
        headers=headers.get(),
        cookies=material["cookie"],
        data=request.SerializeToString(),
        verify=False,
        timeout=20,
    )
    response.raise_for_status()
    payload = _douyin_im_parse_response(response.content)
    summary = _douyin_im_response_summary(payload)
    logger.info("[DOUYIN-IM] send_message response=%s", json.dumps(summary, ensure_ascii=False))
    logger.debug("[DOUYIN-IM] send_message raw_response=%s", json.dumps(payload, ensure_ascii=False)[:4000])
    response_message = str(payload.get("message", "") or "").strip()
    if response_message and response_message.upper() != "OK":
        raise RuntimeError("发送私信失败：" + response_message)
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    notify = body.get("new_message_notify") if isinstance(body.get("new_message_notify"), dict) else {}
    message_body = notify.get("message") if isinstance(notify.get("message"), dict) else {}
    server_message_id = str(message_body.get("server_message_id", "") or "").strip()
    return {
        "raw": payload,
        "summary": summary,
        "server_message_id": server_message_id,
        "client_message_id": client_message_id,
        "ack_only": not bool(server_message_id),
    }


def _send_douyin_private_messages_protocol_sync(
    auth: Dict[str, Any],
    targets: List[Dict[str, Any]],
    message: str,
) -> Dict[str, Any]:
    material = _douyin_im_auth_material(auth)
    results: List[Dict[str, Any]] = []
    for target in targets:
        nickname = str(target.get("nickname") or target.get("author") or target.get("name") or "").strip()
        profile_url = str(target.get("profile_url") or "").strip()
        sec_user_id = str(target.get("sec_user_id") or target.get("sec_uid") or "").strip()
        if not profile_url and sec_user_id:
            profile_url = f"https://www.douyin.com/user/{sec_user_id}"
        row = {
            "nickname": nickname or sec_user_id or profile_url or "客户",
            "profile_url": profile_url,
            "sec_user_id": sec_user_id,
            "ok": False,
            "message": "",
        }
        try:
            user_info = _douyin_im_query_target_user(material, profile_url or sec_user_id)
            user = user_info.get("user") if isinstance(user_info.get("user"), dict) else {}
            to_user_id = int(user.get("uid") or 0)
            if not to_user_id:
                raise RuntimeError("客户 uid 为空")
            conversation = _douyin_im_create_conversation(material, to_user_id)
            send_result = _douyin_im_send_text_message(
                material,
                conversation["conversation_id"],
                conversation["conversation_short_id"],
                conversation["ticket"],
                message,
            )
            row.update({
                "ok": True,
                "message": "发送成功" if send_result.get("server_message_id") else "发送成功（抖音仅返回 OK 确认）",
                "target_uid": to_user_id,
                "conversation_id": conversation["conversation_id"],
                "conversation_short_id": conversation["conversation_short_id"],
                "server_message_id": send_result.get("server_message_id", ""),
                "client_message_id": send_result.get("client_message_id", ""),
                "ack_only": bool(send_result.get("ack_only")),
            })
        except Exception as exc:
            logger.exception("[DOUYIN-IM] send target failed nickname=%s profile=%s", nickname, profile_url or sec_user_id)
            row["message"] = _douyin_im_public_error_message(exc)
        results.append(row)
    success = sum(1 for item in results if item.get("ok"))
    failed = len(results) - success
    return {
        "ok": success > 0,
        "message": f"私信发送完成：成功 {success} 个，失败 {failed} 个。",
        "success": success,
        "failed": failed,
        "results": results,
    }


async def send_douyin_private_messages_protocol(
    profile_dir: str,
    targets: List[Dict[str, Any]],
    message: str,
    browser_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    message = str(message or "").strip()
    if not message:
        return {"ok": False, "logged_in": True, "message": "请先填写私信内容。", "results": []}
    targets = [item for item in (targets or []) if isinstance(item, dict)]
    if not targets:
        return {"ok": False, "logged_in": True, "message": "请先选择要发送私信的客户。", "results": []}
    opts = browser_options if browser_options is not None else _default_browser_options()
    if not Path(profile_dir).exists():
        return {"ok": False, "logged_in": False, "message": "账号浏览器目录不存在，请先打开登录。", "results": []}

    await _ensure_visible_interactive_context(profile_dir, browser_options=opts)
    ctx, created_new = await _acquire_context(profile_dir, new_headless=False, browser_options=opts)
    try:
        page, ctx = await _get_page_with_reacquire(
            profile_dir,
            ctx,
            new_headless_on_recreate=False,
            browser_options=opts,
        )
        await page.goto(
            "https://www.douyin.com/",
            wait_until="domcontentloaded",
            timeout=navigation_timeout_ms(45000),
        )
        try:
            await page.wait_for_timeout(2500)
        except Exception:
            await asyncio.sleep(2.5)
        login_state = await _read_douyin_front_login_state(page, ctx)
        auth = await _extract_douyin_protocol_auth_from_context(page, ctx)
        has_session_cookie, _ = _douyin_protocol_cookie_ready(auth)
        if not has_session_cookie:
            return {
                "ok": False,
                "logged_in": False,
                "message": "抖音前台未检测到登录，请先点击“打开登录”。",
                "results": [],
                **login_state,
            }
        payload = await asyncio.to_thread(
            _send_douyin_private_messages_protocol_sync,
            auth,
            targets,
            message,
        )
        payload["logged_in"] = True
        return payload
    except Exception as e:
        if created_new:
            await _drop_cached_context(profile_dir, ctx, browser_options=opts)
        return {"ok": False, "logged_in": True, "message": str(e), "results": []}


async def collect_douyin_search_results(
    profile_dir: str,
    keyword: str,
    max_results: int = 30,
    browser_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Collect Douyin search video cards from the consumer site using a persistent profile."""
    keyword = str(keyword or "").strip()
    if not keyword:
        return {"ok": False, "logged_in": False, "message": "请输入抖音搜索关键词", "data": []}
    max_results = max(1, min(int(max_results or 30), 100))
    opts = browser_options if browser_options is not None else _default_browser_options()
    if not Path(profile_dir).exists():
        return {"ok": False, "logged_in": False, "message": "账号浏览器目录不存在，请先打开登录。", "data": []}

    await _ensure_visible_interactive_context(profile_dir, browser_options=opts)
    ctx, created_new = await _acquire_context(
        profile_dir,
        new_headless=False,
        browser_options=opts,
    )
    page = None
    try:
        page, ctx = await _get_page_with_reacquire(
            profile_dir,
            ctx,
            new_headless_on_recreate=False,
            browser_options=opts,
        )
        try:
            await page.bring_to_front()
        except Exception:
            pass
        search_url = f"https://www.douyin.com/search/{quote(keyword)}?type=video"
        await page.goto(
            search_url,
            wait_until="domcontentloaded",
            timeout=navigation_timeout_ms(60000),
        )
        try:
            await page.wait_for_timeout(2500)
        except Exception:
            await asyncio.sleep(2.5)
        login_state = await _read_douyin_front_login_state(page, ctx)
        if not login_state.get("logged_in"):
            _setup_auto_close(ctx, profile_dir, page, browser_options=opts)
            return {
                "ok": False,
                "logged_in": False,
                "message": "抖音前台未检测到登录，请先点击“打开登录”。",
                "data": [],
                **login_state,
            }
        page_text = ""
        try:
            page_text = await page.evaluate(
                "() => String(document.body && document.body.innerText || '').slice(0, 4000)"
            )
        except Exception:
            page_text = ""

        seen: Dict[str, Dict[str, Any]] = {}
        rounds = 0
        stable_rounds = 0
        while len(seen) < max_results and rounds < 12 and stable_rounds < 4:
            rounds += 1
            batch = await page.evaluate(
                """
                (limit) => {
                  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                  const absolute = (href) => {
                    try { return new URL(String(href || ''), location.origin).href.split('?')[0]; }
                    catch (e) { return String(href || '').split('?')[0]; }
                  };
                  const awemeIdOf = (href) => {
                    const text = String(href || '');
                    const m1 = text.match(/\\/video\\/(\\d+)/);
                    if (m1) return m1[1];
                    const m2 = text.match(/[?&]modal_id=(\\d+)/);
                    if (m2) return m2[1];
                    return '';
                  };
                  const pickCard = (anchor) => {
                    let best = anchor;
                    let node = anchor;
                    for (let i = 0; i < 8 && node; i += 1, node = node.parentElement) {
                      const text = normalize(node.innerText || node.textContent || '');
                      const imgCount = node.querySelectorAll ? node.querySelectorAll('img').length : 0;
                      const userCount = node.querySelectorAll ? node.querySelectorAll('a[href*="/user/"]').length : 0;
                      if ((imgCount || userCount) && text.length >= 4 && text.length <= 1200) best = node;
                    }
                    return best;
                  };
                  const readStats = (text) => {
                    const out = { likes_text: '', comments_text: '', likes: 0, comments: 0 };
                    const compact = normalize(text);
                    const likeMatch = compact.match(/([\\d.]+\\s*[万wWkK]?)(?:\\s*)(?:点赞|赞|喜欢)/);
                    const commentMatch = compact.match(/([\\d.]+\\s*[万wWkK]?)(?:\\s*)(?:评论|条评论)/);
                    if (likeMatch) out.likes_text = likeMatch[1];
                    if (commentMatch) out.comments_text = commentMatch[1];
                    return out;
                  };
                  const anchors = Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="modal_id="]'));
                  const rows = [];
                  const seen = new Set();
                  for (const anchor of anchors) {
                    const rawHref = anchor.href || anchor.getAttribute('href') || '';
                    const awemeId = awemeIdOf(rawHref);
                    const url = awemeId ? `https://www.douyin.com/video/${awemeId}` : absolute(rawHref);
                    const key = awemeId || url;
                    if (!key || seen.has(key)) continue;
                    seen.add(key);
                    const card = pickCard(anchor);
                    const img = Array.from(card.querySelectorAll('img')).find((item) => {
                      const src = String(item.currentSrc || item.src || '').trim();
                      const w = Number(item.naturalWidth || item.width || 0);
                      const h = Number(item.naturalHeight || item.height || 0);
                      return src && (w >= 60 || h >= 60);
                    }) || card.querySelector('img');
                    const userLink = Array.from(card.querySelectorAll('a[href*="/user/"]')).find((item) => {
                      const href = String(item.href || item.getAttribute('href') || '');
                      return href.includes('/user/') && !href.includes('/user/self');
                    });
                    const cardText = normalize(card.innerText || card.textContent || '');
                    const anchorText = normalize(anchor.getAttribute('aria-label') || anchor.getAttribute('title') || anchor.innerText || anchor.textContent || '');
                    let title = anchorText || cardText.split(/\\n| · | 作者 | 评论 | 点赞 /)[0] || '';
                    if (title.length > 120) title = title.slice(0, 120);
                    const author = normalize(
                      userLink?.innerText
                      || userLink?.textContent
                      || userLink?.getAttribute('title')
                      || ''
                    );
                    const profileUrl = userLink ? absolute(userLink.href || userLink.getAttribute('href') || '') : '';
                    const cover = String(img?.currentSrc || img?.src || '').trim();
                    const stats = readStats(cardText);
                    rows.push({
                      aweme_id: awemeId,
                      url,
                      title,
                      author,
                      profile_url: profileUrl,
                      cover_image: cover,
                      likes_text: stats.likes_text,
                      comments_text: stats.comments_text,
                    });
                    if (rows.length >= limit) break;
                  }
                  return rows;
                }
                """,
                max_results,
            )
            before = len(seen)
            if isinstance(batch, list):
                for item in batch:
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("url", "") or "").strip()
                    aweme_id = _extract_douyin_aweme_id(url) or str(item.get("aweme_id", "") or "").strip()
                    key = aweme_id or url
                    if not key or key in seen:
                        continue
                    item["aweme_id"] = aweme_id
                    item["url"] = f"https://www.douyin.com/video/{aweme_id}" if aweme_id else url
                    seen[key] = item
                    if len(seen) >= max_results:
                        break
            stable_rounds = stable_rounds + 1 if len(seen) == before else 0
            if len(seen) >= max_results:
                break
            try:
                await page.mouse.wheel(0, 1800)
                await page.wait_for_timeout(1200)
            except Exception:
                await asyncio.sleep(1.2)

        rows = list(seen.values())[:max_results]
        if not rows:
            challenge_words = ("验证码", "验证", "安全校验", "环境异常", "访问过于频繁", "扫码")
            if any(word in page_text for word in challenge_words):
                _setup_auto_close(ctx, profile_dir, page, browser_options=opts)
                return {
                    "ok": False,
                    "logged_in": True,
                    "message": "抖音搜索页需要验证码或安全校验，已打开浏览器窗口，请在窗口里处理后再点击开始采集。",
                    "data": [],
                    "total": 0,
                    "search_url": search_url,
                }
        for index, item in enumerate(rows, start=1):
            item["index"] = index
            item["keyword"] = keyword
            item["export_selected"] = False
            profile_url = str(item.get("profile_url", "") or "").strip()
            sec_user_id = ""
            if "/user/" in profile_url:
                sec_user_id = profile_url.split("/user/", 1)[-1].split("?", 1)[0].strip("/")
            item["sec_user_id"] = sec_user_id
        _setup_auto_close(ctx, profile_dir, page, browser_options=opts)
        return {
            "ok": True,
            "logged_in": True,
            "message": f"抖音搜索完成，共采集到 {len(rows)} 条视频。",
            "data": rows,
            "total": len(rows),
            "search_url": search_url,
        }
    except Exception as e:
        if created_new:
            await _drop_cached_context(profile_dir, ctx, browser_options=opts)
        return {"ok": False, "logged_in": False, "message": str(e), "data": []}


async def collect_douyin_video_customers_protocol(
    profile_dir: str,
    video_url: str,
    max_comments: int = 100,
    browser_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Collect commenters under a Douyin video through the web comment protocol."""
    video_url = str(video_url or "").strip()
    aweme_id = _extract_douyin_aweme_id(video_url)
    if not aweme_id:
        return {"ok": False, "message": "缺少有效的视频地址或 aweme_id", "comments": [], "customers": []}
    max_comments = max(1, min(int(max_comments or 100), 500))
    opts = browser_options if browser_options is not None else _default_browser_options()
    if not Path(profile_dir).exists():
        return {"ok": False, "message": "账号浏览器目录不存在，请先打开登录。", "comments": [], "customers": []}

    ctx, created_new = await _acquire_context(
        profile_dir,
        new_headless=True,
        browser_options=opts,
    )
    try:
        target_url = f"https://www.douyin.com/video/{aweme_id}"
        auth = await _extract_douyin_protocol_auth_from_context(None, ctx)
        has_session_cookie, has_web_id = _douyin_protocol_cookie_ready(auth)
        if not has_session_cookie or not has_web_id:
            page, ctx = await _get_page_with_reacquire(
                profile_dir,
                ctx,
                new_headless_on_recreate=True,
                browser_options=opts,
            )
            await page.goto(
                "https://www.douyin.com/jingxuan",
                wait_until="domcontentloaded",
                timeout=navigation_timeout_ms(45000),
            )
            try:
                await page.wait_for_timeout(1800)
            except Exception:
                await asyncio.sleep(1.8)
            login_state = await _read_douyin_front_login_state(page, ctx)
            auth = await _extract_douyin_protocol_auth_from_context(page, ctx)
            has_session_cookie, has_web_id = _douyin_protocol_cookie_ready(auth)
        else:
            login_state = {
                "logged_in": True,
                "cookie": True,
                "login_prompt": False,
                "qr_login_visible": False,
                "path": "",
            }
        if not has_session_cookie:
            return {
                "ok": False,
                "logged_in": False,
                "message": "抖音前台未检测到登录，请先点击“打开登录”。",
                "comments": [],
                "customers": [],
                **login_state,
            }
        if not has_web_id:
            return {
                "ok": False,
                "logged_in": True,
                "message": "协议采集缺少 s_v_web_id，请点击“打开登录”在抖音前台页面刷新一次后再试。",
                "comments": [],
                "customers": [],
                **login_state,
            }
        payload = await asyncio.to_thread(
            _collect_douyin_comments_protocol_sync,
            auth,
            target_url,
            max_comments,
        )
        comments = payload.get("comments") if isinstance(payload, dict) else []
        customers_by_key: Dict[str, Dict[str, Any]] = {}
        for row in comments if isinstance(comments, list) else []:
            if not isinstance(row, dict):
                continue
            key = str(row.get("sec_uid") or row.get("uid") or row.get("profile_url") or row.get("nickname") or "").strip()
            if not key:
                continue
            current = customers_by_key.get(key)
            if not current:
                current = {
                    "id": key,
                    "nickname": str(row.get("nickname", "") or "").strip(),
                    "author": str(row.get("nickname", "") or "").strip(),
                    "uid": str(row.get("uid", "") or "").strip(),
                    "sec_user_id": str(row.get("sec_uid", "") or "").strip(),
                    "profile_url": str(row.get("profile_url", "") or "").strip(),
                    "avatar_url": str(row.get("avatar_url", "") or "").strip(),
                    "comment_count": 0,
                    "digg_count": 0,
                    "latest_comment": "",
                    "aweme_id": aweme_id,
                    "video_url": target_url,
                }
                customers_by_key[key] = current
            current["comment_count"] = int(current.get("comment_count", 0) or 0) + 1
            current["digg_count"] = int(current.get("digg_count", 0) or 0) + int(row.get("digg_count", 0) or 0)
            if not current.get("latest_comment"):
                current["latest_comment"] = str(row.get("text", "") or "").strip()
        customers = list(customers_by_key.values())
        return {
            "ok": True,
            "logged_in": True,
            "message": f"协议模式采集完成，共采集 {len(comments or [])} 条评论，沉淀 {len(customers)} 位客户。",
            "aweme_id": aweme_id,
            "video_url": target_url,
            "comments": comments or [],
            "customers": customers,
            "total_comments": len(comments or []),
            "total_customers": len(customers),
        }
    except Exception as e:
        if created_new:
            await _drop_cached_context(profile_dir, ctx, browser_options=opts)
        return {"ok": False, "message": str(e), "comments": [], "customers": [], "aweme_id": aweme_id}


async def run_publish_task(
    profile_dir: str,
    platform: str,
    file_path: str,
    title: str,
    description: str,
    tags: str,
    options: Optional[Dict[str, Any]] = None,
    cover_path: Optional[str] = None,
    browser_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run a publish task. Fails fast if not logged in (no blocking poll)."""
    from .drivers import DRIVERS

    logger.info("[PUBLISH] run_publish_task start: platform=%s file=%s title=%s profile=%s",
                platform, file_path, title, profile_dir)

    driver_cls = DRIVERS.get(platform)
    if not driver_cls:
        logger.error("[PUBLISH] unsupported platform: %s", platform)
        return {"ok": False, "error": f"不支持的平台: {platform}"}

    driver = driver_cls()
    opts = browser_options if browser_options is not None else _default_browser_options()
    logger.info("[PUBLISH] acquiring browser context...")
    await _ensure_visible_interactive_context(profile_dir, browser_options=opts)
    ctx, created_new = await _acquire_context(
        profile_dir, new_headless=False, browser_options=opts
    )
    logger.info("[PUBLISH] context acquired (new=%s)", created_new)
    try:
        page, ctx = await _get_page_with_reacquire(profile_dir, ctx, browser_options=opts)
        logger.info("[PUBLISH] page ready, checking login...")
        _publish_log_url(page, "1_after_acquire_page")

        # 头条：空白标签若先被动检测必失败，再 navigate=True 会多一次首页；改为直接进图文/视频业务入口再验登录。
        if platform == "toutiao":
            try:
                from skills.toutiao_publish.driver import toutiao_publish_entry_url
            except Exception:
                toutiao_publish_entry_url = None  # type: ignore
            try:
                u_blank = (getattr(page, "url", None) or "").strip().lower()
            except Exception:
                u_blank = ""
            is_blank = not u_blank or u_blank == "about:blank" or u_blank.startswith("chrome://")
            if is_blank and toutiao_publish_entry_url:
                try:
                    entry = toutiao_publish_entry_url(file_path, options or {})
                    logger.info("[PUBLISH-NAV] toutiao 空白页 -> 直达业务入口 %s（少一次首页往返）", entry)
                    await page.goto(
                        entry,
                        wait_until="domcontentloaded",
                        timeout=navigation_timeout_ms(40000),
                    )
                    await asyncio.sleep(1.2)
                except Exception as ex:
                    logger.warning("[PUBLISH-NAV] toutiao 直达业务入口失败: %s", ex)
                _publish_log_url(page, "1b_toutiao_entry_preload")

        # 先被动检测当前页是否已登录，避免每次发布都从首页再跳进编辑器（看起来像反复进出）。
        login_ok = False
        try:
            login_ok = await driver.check_login(page, navigate=False)
        except Exception:
            login_ok = False
        _publish_log_url(page, "2_after_login_passive")
        logger.info("[PUBLISH-NAV] passive_login_ok=%s", login_ok)
        if not login_ok:
            logger.info(
                "[PUBLISH-NAV] 3_passive_failed -> check_login(navigate=True)，"
                "头条会 goto 首页；若此处频繁出现且 url 变为 mp 根路径，即「编辑页被拉回首页」的原因"
            )
            login_ok = await driver.check_login(page, navigate=True)
        _publish_log_url(page, "4_after_login_final")
        logger.info("[PUBLISH] login check result: %s", login_ok)
        if not login_ok:
            try:
                await page.goto(
                    driver.login_url(),
                    wait_until="domcontentloaded",
                    timeout=navigation_timeout_ms(30000),
                )
            except Exception:
                pass
            _setup_auto_close(ctx, profile_dir, page, browser_options=opts)
            return {
                "ok": False,
                "need_login": True,
                "error": "未登录，已打开浏览器登录页，请扫码登录后再重试发布",
            }

        await _bring_window_to_front(page)
        from .platform_publish_limits import log_and_attach_warnings, normalize_publish_texts

        title_n, desc_n, tags_n, field_warnings = normalize_publish_texts(
            platform, file_path, title, description, tags
        )
        _publish_log_url(page, "5_before_driver_publish")
        logger.info("[PUBLISH] calling driver.publish()...")
        result = await driver.publish(
            page=page,
            file_path=file_path,
            title=title_n,
            description=desc_n,
            tags=tags_n,
            options=options or {},
            cover_path=cover_path,
        )
        result = log_and_attach_warnings(result, field_warnings)
        _publish_log_url(page, "6_after_driver_publish")
        logger.info("[PUBLISH] driver.publish() returned: ok=%s", result.get("ok"))
        if not result.get("ok"):
            logger.warning("[PUBLISH] publish error: %s", result.get("error"))
        _setup_auto_close(ctx, profile_dir, page, browser_options=opts)
        return result
    except Exception as exc:
        logger.exception("[PUBLISH] run_publish_task exception")
        return {"ok": False, "error": str(exc)}


async def dryrun_douyin_upload_in_context(
    profile_dir: str,
    file_path: str,
    title: str = "dryrun 标题",
    description: str = "dryrun 文案",
    tags: str = "dryrun,测试",
    browser_options: Optional[Dict[str, Any]] = None,
    *,
    publish_options: Optional[Dict[str, Any]] = None,
    after_publish: Optional[Callable[[Any, Dict[str, Any]], Awaitable[None]]] = None,
) -> Dict[str, Any]:
    """Dry-run a douyin publish flow INSIDE the current process.

    after_publish: 在 driver.publish 返回后、仍持有同一 page 时调用（用于探测脚本在同一 DOM 上采控件）。
    勿依赖「关闭再 goto page.url」恢复发布编辑页——抖音草稿不会随 URL 单独恢复。
    """
    from .drivers.douyin import DouyinDriver, UPLOAD_URL

    driver = DouyinDriver()
    opts = browser_options if browser_options is not None else _default_browser_options()
    await _ensure_visible_interactive_context(profile_dir, browser_options=opts)
    ctx, _created_new = await _acquire_context(
        profile_dir, new_headless=False, browser_options=opts
    )
    page = await _get_page_and_focus(ctx)

    await page.goto(
        UPLOAD_URL,
        wait_until="domcontentloaded",
        timeout=navigation_timeout_ms(30000),
    )
    try:
        await page.wait_for_load_state("networkidle", timeout=_pw_ms(15000))
    except Exception:
        pass

    frames = []
    try:
        for fr in getattr(page, "frames", []) or []:
            frames.append({"name": getattr(fr, "name", ""), "url": getattr(fr, "url", "")})
    except Exception:
        pass

    merged_opts: Dict[str, Any] = {"dry_run": True}
    if publish_options:
        merged_opts.update(publish_options)

    result = await driver.publish(
        page=page,
        file_path=file_path,
        title=title,
        description=description,
        tags=tags,
        options=merged_opts,
        cover_path=None,
    )
    if after_publish is not None:
        await after_publish(page, result)

    return {
        "page_url": getattr(page, "url", ""),
        "title": (await page.title()) if hasattr(page, "title") else "",
        "frame_count": len(frames),
        "frames": frames[:12],
        "driver_result": result,
    }
