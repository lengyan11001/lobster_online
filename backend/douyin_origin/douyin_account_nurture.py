from __future__ import annotations

import asyncio
import os
import random
import subprocess
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Set

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from douyin_client import DouyinClient
from win_subprocess import run_hidden


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_int(value: object, default: int, minimum: int = 0, maximum: Optional[int] = None) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


async def _sleep_with_stop_check(delay_seconds: int, should_continue: Callable[[], bool]):
    remaining = max(0, int(delay_seconds))
    while remaining > 0 and should_continue():
        await asyncio.sleep(min(1, remaining))
        remaining -= 1


class DouyinAccountNurtureScheduler:
    def __init__(
        self,
        accounts: List[Dict],
        config: Dict,
        broadcast_log: Callable[[str, str], None],
        enabled_account_ids: Optional[List[int]] = None,
    ):
        self.accounts = accounts or []
        self.config = config or {}
        self.broadcast_log = broadcast_log
        self.is_running = True
        self.started_at = _now_text()
        self.worker_tasks: List[asyncio.Task] = []
        self.account_states: Dict[int, Dict] = {}
        self.enabled_account_ids: Set[int] = set()
        self._trigger_immediate_account_ids: Set[int] = set()

        for account in self.accounts:
            account_id = int(account.get("id", 0) or 0)
            if account_id <= 0:
                continue
            self.account_states[account_id] = self._build_initial_state(account)

        online_ids = {
            int(account.get("id", 0) or 0)
            for account in self.accounts
            if int(account.get("id", 0) or 0) > 0 and str(account.get("status", "")).lower() == "online"
        }
        if enabled_account_ids is None:
            self.enabled_account_ids = set(online_ids)
        else:
            self.enabled_account_ids = {int(account_id) for account_id in enabled_account_ids if int(account_id) in online_ids}
        self._trigger_immediate_account_ids = set(self.enabled_account_ids)
        self._sync_participation_flags()

    def _build_initial_state(self, account: Dict) -> Dict:
        account_id = int(account.get("id", 0) or 0)
        port = int(account.get("port", 0) or 0)
        profile_dir = ""
        if account_id > 0 or port > 0:
            try:
                profile_dir = DouyinClient(port=port, account_id=account_id or None).resolve_profile_dir()
            except Exception:
                profile_dir = ""
        return {
            "account_id": account_id,
            "port": port,
            "account_status": str(account.get("status", "offline") or "offline"),
            "profile_dir": profile_dir,
            "connection_mode": "待连接",
            "worker_status": "idle",
            "last_action": "等待启动",
            "last_error": "",
            "last_started_at": "",
            "last_finished_at": "",
            "next_run_at": "",
            "completed_sessions": 0,
            "current_session_minutes": 0,
            "current_video_count": 0,
            "likes_sent": 0,
            "is_enabled": False,
            "can_participate": str(account.get("status", "offline") or "offline").lower() == "online",
        }

    def _set_state(self, account_id: int, **updates):
        state = self.account_states.setdefault(account_id, self._build_initial_state({"id": account_id}))
        state.update(updates)

    def _get_account_ids(self, online_only: bool = False) -> List[int]:
        result: List[int] = []
        for account in self.accounts:
            account_id = int(account.get("id", 0) or 0)
            if account_id <= 0:
                continue
            if online_only and str(account.get("status", "")).lower() != "online":
                continue
            result.append(account_id)
        return result

    def _resolve_target_account_ids(self, account_ids: Optional[List[int]] = None, online_only: bool = False) -> List[int]:
        valid_ids = set(self._get_account_ids(online_only=online_only))
        if account_ids is None:
            return sorted(valid_ids)
        return sorted({int(account_id) for account_id in account_ids if int(account_id) in valid_ids})

    def _sync_participation_flags(self):
        online_ids = set(self._get_account_ids(online_only=True))
        self.enabled_account_ids &= online_ids
        self._trigger_immediate_account_ids &= online_ids

        for account in self.accounts:
            account_id = int(account.get("id", 0) or 0)
            if account_id <= 0:
                continue
            is_online = str(account.get("status", "offline") or "offline").lower() == "online"
            is_enabled = account_id in self.enabled_account_ids and is_online
            state = self.account_states.setdefault(account_id, self._build_initial_state(account))
            state["account_status"] = str(account.get("status", "offline") or "offline")
            state["port"] = int(account.get("port", 0) or 0)
            state["can_participate"] = is_online
            state["is_enabled"] = is_enabled
            if not is_online:
                state["worker_status"] = "idle"
                state["next_run_at"] = ""
                state["current_session_minutes"] = 0
                if not state.get("last_action"):
                    state["last_action"] = "当前未登录，不参与养号"

    def stop(self):
        self.is_running = False
        self._close_account_browsers(self._get_account_ids(online_only=False))

    def _is_running(self) -> bool:
        return self.is_running

    def get_online_accounts(self) -> List[Dict]:
        return [account for account in self.accounts if str(account.get("status", "")).lower() == "online"]

    def get_enabled_online_accounts(self) -> List[Dict]:
        self._sync_participation_flags()
        return [
            account
            for account in self.accounts
            if int(account.get("id", 0) or 0) in self.enabled_account_ids
            and str(account.get("status", "")).lower() == "online"
        ]

    def has_enabled_online_accounts(self) -> bool:
        return bool(self.get_enabled_online_accounts())

    def enable_accounts(self, account_ids: Optional[List[int]] = None) -> List[int]:
        target_ids = self._resolve_target_account_ids(account_ids, online_only=True)
        if not target_ids:
            return []

        next_start_text = self._next_active_start().strftime("%Y-%m-%d %H:%M:%S")
        active_now = self._is_in_active_window()
        for account_id in target_ids:
            self.enabled_account_ids.add(account_id)
            self._trigger_immediate_account_ids.add(account_id)
            self._set_state(
                account_id,
                is_enabled=True,
                can_participate=True,
                worker_status="waiting" if self.is_running else "idle",
                next_run_at="" if active_now else next_start_text,
                last_action="已加入养号排班，等待开始" if active_now else f"已加入养号排班，将于 {next_start_text} 后开始",
                last_error="",
            )
        self._sync_participation_flags()
        return target_ids

    def disable_accounts(self, account_ids: Optional[List[int]] = None) -> List[int]:
        target_ids = self._resolve_target_account_ids(account_ids, online_only=False)
        if not target_ids:
            return []

        disabled_ids: List[int] = []
        for account_id in target_ids:
            if account_id in self.enabled_account_ids or self.account_states.get(account_id, {}).get("is_enabled"):
                disabled_ids.append(account_id)
            self.enabled_account_ids.discard(account_id)
            self._trigger_immediate_account_ids.discard(account_id)
            account_status = str(self.account_states.get(account_id, {}).get("account_status", "offline") or "offline").lower()
            self._set_state(
                account_id,
                is_enabled=False,
                can_participate=account_status == "online",
                worker_status="paused" if account_status == "online" else "idle",
                next_run_at="",
                current_session_minutes=0,
                last_action="该账号养号已暂停" if account_status == "online" else "当前未登录，不参与养号",
            )
        self._close_account_browsers(disabled_ids)
        self._sync_participation_flags()
        return disabled_ids

    def _close_account_browsers(self, account_ids: Optional[List[int]] = None):
        target_ids = self._resolve_target_account_ids(account_ids, online_only=False)
        if not target_ids:
            return

        def _close():
            for account in self.accounts:
                account_id = int(account.get("id", 0) or 0)
                if account_id not in target_ids:
                    continue
                port = int(account.get("port", 0) or 0)
                if port <= 0:
                    continue
                try:
                    DouyinClient(port=port, account_id=account_id or None).close_browser()
                except Exception:
                    pass

        threading.Thread(target=_close, name="douyin-nurture-close-browser", daemon=True).start()

    def _is_account_enabled(self, account_id: int) -> bool:
        self._sync_participation_flags()
        return account_id in self.enabled_account_ids

    def _should_keep_account_running(self, account_id: int) -> bool:
        return self.is_running and self._is_account_enabled(account_id)

    def _consume_immediate_trigger(self, account_id: int) -> bool:
        if account_id in self._trigger_immediate_account_ids:
            self._trigger_immediate_account_ids.discard(account_id)
            return True
        return False

    def _interval_min_minutes(self) -> int:
        return _safe_int(self.config.get("douyin_nurture_interval_min_minutes", 120), 120, minimum=30, maximum=720)

    def _interval_max_minutes(self) -> int:
        minimum = self._interval_min_minutes()
        return _safe_int(self.config.get("douyin_nurture_interval_max_minutes", 180), 180, minimum=minimum, maximum=720)

    def _session_min_minutes(self) -> int:
        return _safe_int(self.config.get("douyin_nurture_session_min_minutes", 20), 20, minimum=5, maximum=180)

    def _session_max_minutes(self) -> int:
        minimum = self._session_min_minutes()
        return _safe_int(self.config.get("douyin_nurture_session_max_minutes", 40), 40, minimum=minimum, maximum=180)

    def _active_start_hour(self) -> int:
        return _safe_int(self.config.get("douyin_nurture_active_start_hour", 9), 9, minimum=6, maximum=20)

    def _active_end_hour(self) -> int:
        minimum = self._active_start_hour() + 1
        return _safe_int(self.config.get("douyin_nurture_active_end_hour", 23), 23, minimum=minimum, maximum=23)

    def _rules(self) -> Dict[str, str]:
        return {
            "entry_url": "https://www.douyin.com/jingxuan",
            "session_label": f"{self._session_min_minutes()}-{self._session_max_minutes()} 分钟 / 次",
            "interval_label": f"{self._interval_min_minutes()}-{self._interval_max_minutes()} 分钟 / 次",
            "active_window_label": f"{self._active_start_hour():02d}:00-{self._active_end_hour():02d}:00",
            "daily_runs_label": "约 5-6 次 / 天",
            "warning": "养号期间不要执行其他抖音任务，否则浏览器会互相抢占，容易冲突。",
        }

    def snapshot(self) -> Dict:
        self._sync_participation_flags()
        states = [
            self.account_states.get(int(account.get("id", 0) or 0), self._build_initial_state(account))
            for account in self.accounts
            if int(account.get("id", 0) or 0) > 0
        ]
        running_accounts = sum(1 for item in states if item.get("is_enabled") and item.get("worker_status") == "running")
        waiting_accounts = sum(1 for item in states if item.get("is_enabled") and item.get("worker_status") == "waiting")
        total_sessions = sum(int(item.get("completed_sessions", 0) or 0) for item in states)
        return {
            "running": self.is_running,
            "started_at": self.started_at,
            "running_accounts": running_accounts,
            "waiting_accounts": waiting_accounts,
            "enabled_accounts": len(self.enabled_account_ids),
            "online_accounts": len(self.get_online_accounts()),
            "total_sessions": total_sessions,
            "accounts": states,
            "rules": self._rules(),
        }

    def is_active_window_now(self) -> bool:
        return self._is_in_active_window()

    def next_active_start_text(self) -> str:
        return self._next_active_start().strftime("%Y-%m-%d %H:%M:%S")

    def is_actively_blocking_other_tasks(self) -> bool:
        if not self.is_running or not self.has_enabled_online_accounts():
            return False
        if any(state.get("is_enabled") and state.get("worker_status") == "running" for state in self.account_states.values()):
            return True
        return self._is_in_active_window()

    def _is_in_active_window(self, current: Optional[datetime] = None) -> bool:
        now = current or datetime.now()
        return self._active_start_hour() <= now.hour < self._active_end_hour()

    def _next_active_start(self, current: Optional[datetime] = None) -> datetime:
        now = current or datetime.now()
        today_start = now.replace(hour=self._active_start_hour(), minute=0, second=0, microsecond=0)
        if now < today_start:
            return today_start
        return today_start + timedelta(days=1)

    async def run(self):
        online_accounts = self.get_online_accounts()
        if not online_accounts:
            self.broadcast_log("抖音养号启动失败：当前没有已登录账号。", "warning")
            self.is_running = False
            return

        if not self.has_enabled_online_accounts():
            self.broadcast_log("抖音养号启动失败：当前没有可参与的在线账号。", "warning")
            self.is_running = False
            return

        rules = self._rules()
        enabled_count = len(self.get_enabled_online_accounts())
        self.broadcast_log(
            (
                f"抖音养号已启动：{enabled_count} 个已登录账号进入循环，"
                f"单次时长约 {rules['session_label']}，间隔约 {rules['interval_label']}，"
                f"活跃时段 {rules['active_window_label']}，{rules['daily_runs_label']}。"
            ),
            "success",
        )
        self.broadcast_log(f"抖音养号说明：{rules['warning']}", "warning")

        self.worker_tasks = [asyncio.create_task(self._worker(account)) for account in online_accounts]
        try:
            await asyncio.gather(*self.worker_tasks, return_exceptions=True)
        finally:
            self.is_running = False
            for account in online_accounts:
                account_id = int(account.get("id", 0) or 0)
                state = self.account_states.get(account_id, {})
                self._set_state(
                    account_id,
                    worker_status="stopped",
                    is_enabled=bool(state.get("is_enabled")),
                    next_run_at="",
                    last_action="养号任务已停止" if state.get("is_enabled") else "该账号未参与本轮养号",
                    current_session_minutes=0,
                )

    async def _worker(self, account: Dict):
        account_id = int(account.get("id", 0) or 0)
        first_run = True

        while self.is_running:
            if not self._is_account_enabled(account_id):
                login_status = str(account.get("status", "offline") or "offline").lower()
                self._set_state(
                    account_id,
                    worker_status="paused" if login_status == "online" else "idle",
                    next_run_at="",
                    current_session_minutes=0,
                    last_action="该账号未加入养号排班" if login_status == "online" else "当前未登录，不参与养号",
                )
                await asyncio.sleep(1)
                continue

            now = datetime.now()
            if not self._is_in_active_window(now):
                next_start = self._next_active_start(now)
                wait_seconds = max(0, int((next_start - now).total_seconds()))
                self._set_state(
                    account_id,
                    worker_status="waiting",
                    next_run_at=next_start.strftime("%Y-%m-%d %H:%M:%S"),
                    last_action=f"夜间暂停中，等待 {next_start.strftime('%H:%M')} 后继续",
                    last_error="",
                )
                await _sleep_with_stop_check(wait_seconds, lambda: self._should_keep_account_running(account_id))
                continue

            should_run_immediately = first_run or self._consume_immediate_trigger(account_id)
            first_run = False
            delay_seconds = 0
            if not should_run_immediately:
                delay_seconds = random.randint(self._interval_min_minutes(), self._interval_max_minutes()) * 60

            if delay_seconds > 0:
                next_run_at = datetime.now() + timedelta(seconds=delay_seconds)
                active_end = datetime.now().replace(
                    hour=self._active_end_hour(),
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                if next_run_at >= active_end:
                    next_start = self._next_active_start()
                    wait_seconds = max(0, int((next_start - datetime.now()).total_seconds()))
                    self._set_state(
                        account_id,
                        worker_status="waiting",
                        next_run_at=next_start.strftime("%Y-%m-%d %H:%M:%S"),
                        last_action=f"今天已完成本轮安排，等待明天 {next_start.strftime('%H:%M')} 后继续",
                        last_error="",
                    )
                    await _sleep_with_stop_check(wait_seconds, lambda: self._should_keep_account_running(account_id))
                    continue

                self._set_state(
                    account_id,
                    worker_status="waiting",
                    next_run_at=next_run_at.strftime("%Y-%m-%d %H:%M:%S"),
                    last_action=f"等待下一轮养号，约 {delay_seconds // 60} 分钟后开始",
                    last_error="",
                )
                await _sleep_with_stop_check(delay_seconds, lambda: self._should_keep_account_running(account_id))
                if not self._should_keep_account_running(account_id):
                    continue

            if not self._is_in_active_window() or not self._should_keep_account_running(account_id):
                continue
            await self._run_session(account)

    def _read_browser_command_line_by_port(self, port: int) -> str:
        if os.name != "nt" or port <= 0:
            return ""

        script = f"""
$line = netstat -ano -p tcp | Select-String -Pattern ':{port}\\s+.*LISTENING' | Select-Object -First 1
if (-not $line) {{
    exit 0
}}
$parts = (($line.ToString() -replace '\\s+', ' ').Trim()).Split(' ')
$pid = $parts[$parts.Length - 1]
if (-not $pid) {{
    exit 0
}}
$proc = Get-CimInstance Win32_Process -Filter "ProcessId = $pid" | Select-Object -First 1
if ($proc -and $proc.CommandLine) {{
    Write-Output $proc.CommandLine
}}
"""

        try:
            result = run_hidden(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                timeout=8,
            )
            return (result.stdout or "").strip()
        except Exception:
            return ""

    def _validate_existing_browser_profile(self, port: int, profile_dir: str) -> tuple[Optional[bool], str]:
        command_line = self._read_browser_command_line_by_port(port)
        if not command_line:
            return None, ""

        normalized_cmd = command_line.replace('"', "").replace("/", "\\").lower()
        normalized_profile = profile_dir.replace('"', "").replace("/", "\\").lower()
        if "--user-data-dir=" not in normalized_cmd:
            return None, command_line
        is_match = normalized_profile in normalized_cmd
        return is_match, command_line

    async def _wait_for_expected_browser_profile(
        self,
        port: int,
        profile_dir: str,
        timeout_seconds: int = 10,
        interval_seconds: float = 1.0,
    ) -> tuple[Optional[bool], str]:
        deadline = time.monotonic() + timeout_seconds
        last_result: Optional[bool] = None
        last_command_line = ""

        while self.is_running and time.monotonic() < deadline:
            matched_profile, command_line = self._validate_existing_browser_profile(port, profile_dir)
            if matched_profile is True:
                return True, command_line
            if matched_profile is not None:
                last_result = matched_profile
            if command_line:
                last_command_line = command_line
            await asyncio.sleep(interval_seconds)

        matched_profile, command_line = self._validate_existing_browser_profile(port, profile_dir)
        if matched_profile is not None:
            last_result = matched_profile
        if command_line:
            last_command_line = command_line
        return last_result, last_command_line

    async def _connect_browser_with_retry(
        self,
        playwright,
        port: int,
        timeout_seconds: int = 18,
        interval_seconds: float = 1.0,
    ) -> Browser:
        deadline = time.monotonic() + timeout_seconds
        last_error: Optional[Exception] = None

        while self.is_running and time.monotonic() < deadline:
            try:
                return await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(interval_seconds)

        raise RuntimeError(
            f"端口 {port} 浏览器尚未就绪，等待 {timeout_seconds} 秒后仍无法连接：{last_error or 'unknown error'}"
        )

    async def _wait_for_persistent_context(
        self,
        browser: Browser,
        account_id: int,
        port: int,
        profile_dir: str,
        timeout_seconds: int = 12,
    ) -> BrowserContext:
        deadline = time.monotonic() + timeout_seconds
        while self.is_running and time.monotonic() < deadline:
            if browser.contexts:
                return browser.contexts[0]
            await asyncio.sleep(0.5)

        if browser.contexts:
            return browser.contexts[0]

        raise RuntimeError(
            f"账号 {account_id} 未获取到持久登录上下文。请确认端口 {port} 对应浏览器已使用资料目录 {profile_dir} 启动并保持登录。"
        )

    async def _open_first_jingxuan_video(self, page: Page) -> bool:
        for attempt in range(1, 7):
            try:
                click_target = await page.evaluate(
                    """
                    () => {
                        const isVisible = (node) => {
                            if (!node) return false;
                            const rect = node.getBoundingClientRect();
                            return rect.width > 120 && rect.height > 120 && rect.bottom > 120 && rect.right > 120;
                        };
                        const normalizeTarget = (node) => {
                            if (!node) return null;
                            const clickable = node.matches?.('[href*="/video/"]')
                                ? node
                                : node.querySelector?.('[href*="/video/"]') || node;
                            if (!clickable) return null;
                            clickable.scrollIntoView?.({ block: 'center', inline: 'center', behavior: 'instant' });
                            const rect = clickable.getBoundingClientRect();
                            if (!isVisible(clickable)) return null;
                            return {
                                x: Math.round(rect.left + rect.width / 2),
                                y: Math.round(rect.top + rect.height / 2),
                                top: rect.top,
                                left: rect.left,
                                width: rect.width,
                                height: rect.height,
                            };
                        };
                        const candidates = Array.from(
                            document.querySelectorAll('.discover-video-card-item, .discover-video-card-item [href*="/video/"], div[href*="/video/"]')
                        )
                            .map(normalizeTarget)
                            .filter(Boolean)
                            .sort((a, b) => (Math.abs(a.top - b.top) < 6 ? a.left - b.left : a.top - b.top));
                        return candidates[0] || null;
                    }
                    """,
                )
            except Exception:
                click_target = None

            if click_target and click_target.get("x") and click_target.get("y"):
                try:
                    await page.mouse.move(float(click_target["x"]), float(click_target["y"]))
                    await page.mouse.click(
                        float(click_target["x"]),
                        float(click_target["y"]),
                        delay=random.randint(60, 180),
                    )
                    await page.wait_for_function(
                        """
                        () => {
                            const url = String(window.location.href || "");
                            if (url.includes("modal_id=") || /\\/video\\/\\d+/.test(url)) {
                                return true;
                            }
                            const video = document.querySelector("video");
                            if (!video) return false;
                            const rect = video.getBoundingClientRect();
                            return rect.width > 320 && rect.height > 240;
                        }
                        """,
                        timeout=8000,
                    )
                    return True
                except Exception:
                    pass

            fallback_video_id = await page.evaluate(
                """
                () => {
                    const isVisible = (node) => {
                        if (!node) return false;
                        const rect = node.getBoundingClientRect();
                        return rect.width > 120 && rect.height > 120 && rect.bottom > 120 && rect.right > 120;
                    };
                    const target = Array.from(document.querySelectorAll('[data-aweme-id], .discover-video-card-item'))
                        .find((node) => isVisible(node));
                    if (!target) return "";
                    return target.getAttribute("data-aweme-id") || target.id || "";
                }
                """
            )
            if fallback_video_id:
                try:
                    await page.wait_for_function(
                        f"""
                        () => {{
                            const target = document.querySelector('[data-aweme-id="{fallback_video_id}"], [id="{fallback_video_id}"]');
                            if (!target) return false;
                            const rect = target.getBoundingClientRect();
                            return rect.width > 120 && rect.height > 120;
                        }}
                        """,
                        timeout=3000,
                    )
                    click_target = await page.evaluate(
                        f"""
                        () => {{
                            const target = document.querySelector('[data-aweme-id="{fallback_video_id}"], [id="{fallback_video_id}"]');
                            if (!target) return null;
                            const rect = target.getBoundingClientRect();
                            return {{
                                x: Math.round(rect.left + rect.width / 2),
                                y: Math.round(rect.top + rect.height / 2),
                            }};
                        }}
                        """
                    )
                    if click_target and click_target.get("x") and click_target.get("y"):
                        await page.mouse.move(float(click_target["x"]), float(click_target["y"]))
                        await page.mouse.click(
                            float(click_target["x"]),
                            float(click_target["y"]),
                            delay=random.randint(60, 180),
                        )
                        await page.wait_for_function(
                            """
                            () => {
                                const url = String(window.location.href || "");
                                return url.includes("modal_id=") || /\\/video\\/\\d+/.test(url);
                            }
                            """,
                            timeout=8000,
                        )
                        return True
                except Exception:
                    pass

            if attempt in (2, 4):
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    pass
            else:
                try:
                    await page.mouse.wheel(0, random.randint(220, 540))
                except Exception:
                    pass
            await asyncio.sleep(min(1 + attempt, 4))

        return False

    async def _focus_video_page(self, page: Page):
        try:
            await page.mouse.move(780, 420)
            await page.mouse.click(780, 420, delay=random.randint(30, 120))
        except Exception:
            pass

    async def _like_current_video(self, page: Page) -> bool:
        try:
            await self._focus_video_page(page)
            await page.keyboard.press("z")
            return True
        except Exception:
            return False

    async def _next_video(self, page: Page):
        await self._focus_video_page(page)
        await page.mouse.wheel(0, random.randint(820, 1320))

    async def _run_session(self, account: Dict):
        account_id = int(account.get("id", 0) or 0)
        port = int(account.get("port", 0) or 0)
        if not self._should_keep_account_running(account_id):
            return

        session_minutes = random.randint(self._session_min_minutes(), self._session_max_minutes())
        session_seconds = session_minutes * 60
        should_continue = lambda: self._should_keep_account_running(account_id)

        client = DouyinClient(port=port, account_id=account_id)
        profile_dir = client.resolve_profile_dir()
        expected_account_name = f"account_{account_id}"

        self._set_state(
            account_id,
            worker_status="running",
            profile_dir=profile_dir,
            connection_mode="准备连接抖音浏览器",
            last_started_at=_now_text(),
            last_finished_at="",
            next_run_at="",
            last_action="正在启动养号会话",
            last_error="",
            current_session_minutes=session_minutes,
            current_video_count=0,
            likes_sent=0,
        )
        self.broadcast_log(
            f"[抖音养号][账号{account_id}] 开始新一轮养号会话，目标时长约 {session_minutes} 分钟，端口 {port}，资料目录 {profile_dir}",
            "info",
        )

        browser: Optional[Browser] = None
        context: Optional[BrowserContext] = None
        page: Optional[Page] = None
        watched_count = 0
        likes_sent = 0

        try:
            async with async_playwright() as playwright:
                connection_mode = f"复用端口 {port} 已登录浏览器 / 持久登录上下文"
                reused_existing_browser = False
                try:
                    browser = await self._connect_browser_with_retry(
                        playwright,
                        port,
                        timeout_seconds=4,
                        interval_seconds=1.0,
                    )
                    reused_existing_browser = True
                except Exception:
                    self.broadcast_log(
                        f"[抖音养号][账号{account_id}] 未检测到浏览器，尝试自动拉起 {expected_account_name} profile：{profile_dir}",
                        "warning",
                    )
                    if not client.launch_browser(start_url="https://www.douyin.com/jingxuan"):
                        raise RuntimeError("抖音浏览器启动失败")
                    browser = await self._connect_browser_with_retry(
                        playwright,
                        port,
                        timeout_seconds=20,
                        interval_seconds=1.0,
                    )
                    connection_mode = f"自动拉起 {expected_account_name} profile / 持久登录上下文"

                if reused_existing_browser:
                    matched_profile, _ = await self._wait_for_expected_browser_profile(
                        port=port,
                        profile_dir=profile_dir,
                        timeout_seconds=8,
                        interval_seconds=1.0,
                    )
                    if matched_profile is False:
                        raise RuntimeError(
                            f"端口 {port} 当前浏览器不是预期的账号资料目录。预期 {profile_dir}，请先关闭当前端口浏览器后再重试。"
                        )
                    if matched_profile is None:
                        connection_mode = f"复用端口 {port} 浏览器 / profile 未校验"
                        self.broadcast_log(
                            f"[抖音养号][账号{account_id}] 已复用端口 {port} 浏览器，但未读取到进程命令行，无法进一步校验 profile。",
                            "warning",
                        )
                    else:
                        self.broadcast_log(
                            f"[抖音养号][账号{account_id}] 端口 {port} 浏览器命令行校验通过，使用资料目录：{profile_dir}",
                            "info",
                        )

                context = await self._wait_for_persistent_context(
                    browser=browser,
                    account_id=account_id,
                    port=port,
                    profile_dir=profile_dir,
                )
                self._set_state(
                    account_id,
                    connection_mode=connection_mode,
                    last_action="已连接登录资料，准备进入抖音精选",
                )
                self.broadcast_log(
                    f"[抖音养号][账号{account_id}] 已连接浏览器，当前模式：{connection_mode}",
                    "info",
                )

                page = await context.new_page()
                await page.goto("https://www.douyin.com/jingxuan", wait_until="domcontentloaded", timeout=45000)
                await _sleep_with_stop_check(random.randint(3, 8), should_continue)
                if not should_continue():
                    return

                opened = await self._open_first_jingxuan_video(page)
                if not opened:
                    raise RuntimeError("未能打开抖音精选页的首个视频")

                watched_count = 1
                self._set_state(account_id, current_video_count=watched_count, last_action="已进入首个视频，开始自然观看")
                deadline = time.monotonic() + session_seconds

                while should_continue() and time.monotonic() < deadline:
                    remaining = int(max(0, deadline - time.monotonic()))
                    if remaining <= 0:
                        break

                    dwell_seconds = min(remaining, random.randint(4, 38))
                    self._set_state(
                        account_id,
                        current_video_count=watched_count,
                        likes_sent=likes_sent,
                        last_action=f"自然停留 {dwell_seconds} 秒，模拟精选页观看",
                    )
                    await _sleep_with_stop_check(dwell_seconds, should_continue)
                    if not should_continue():
                        break

                    if random.random() < 0.62:
                        liked = await self._like_current_video(page)
                        if liked:
                            likes_sent += 1
                            self._set_state(
                                account_id,
                                current_video_count=watched_count,
                                likes_sent=likes_sent,
                                last_action="随机点赞成功，准备切到下一个视频",
                            )
                            self.broadcast_log(
                                f"[抖音养号][账号{account_id}] 已对第 {watched_count} 条视频执行随机点赞（Z 键）。",
                                "info",
                            )
                        await _sleep_with_stop_check(random.randint(1, 4), should_continue)
                        if not should_continue():
                            break

                    await self._next_video(page)
                    watched_count += 1
                    self._set_state(
                        account_id,
                        current_video_count=watched_count,
                        likes_sent=likes_sent,
                        last_action="已下滑切换到下一个视频",
                    )
                    await _sleep_with_stop_check(random.randint(2, 6), should_continue)

                state = self.account_states.get(account_id, {})
                completed_sessions = int(state.get("completed_sessions", 0) or 0)
                if self.is_running and self._is_account_enabled(account_id):
                    completed_sessions += 1
                    self._set_state(
                        account_id,
                        worker_status="waiting",
                        last_finished_at=_now_text(),
                        last_action="本轮抖音养号会话完成",
                        completed_sessions=completed_sessions,
                        current_session_minutes=0,
                        current_video_count=watched_count,
                        likes_sent=likes_sent,
                    )
                    self.broadcast_log(
                        f"[抖音养号][账号{account_id}] 本轮会话完成，共浏览约 {watched_count} 条视频，随机点赞 {likes_sent} 次。",
                        "success",
                    )
                elif self.is_running:
                    self._set_state(
                        account_id,
                        worker_status="paused",
                        last_finished_at=_now_text(),
                        last_action="该账号养号已暂停",
                        current_session_minutes=0,
                        current_video_count=watched_count,
                        likes_sent=likes_sent,
                    )
                    self.broadcast_log(f"[抖音养号][账号{account_id}] 已暂停该账号的养号。", "warning")
                else:
                    self._set_state(
                        account_id,
                        worker_status="stopped",
                        last_finished_at=_now_text(),
                        last_action="养号任务已停止",
                        current_session_minutes=0,
                        current_video_count=watched_count,
                        likes_sent=likes_sent,
                    )
        except Exception as exc:
            target_status = "waiting" if self._is_account_enabled(account_id) and self.is_running else ("paused" if self.is_running else "stopped")
            last_action = "本轮抖音养号会话异常结束" if self.is_running else "养号任务已停止"
            self._set_state(
                account_id,
                worker_status=target_status,
                last_finished_at=_now_text(),
                last_action=last_action,
                last_error=str(exc),
                current_session_minutes=0,
                current_video_count=watched_count,
                likes_sent=likes_sent,
            )
            if self.is_running and self._is_account_enabled(account_id):
                self.broadcast_log(f"[抖音养号][账号{account_id}] 会话失败：{exc}", "error")
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
