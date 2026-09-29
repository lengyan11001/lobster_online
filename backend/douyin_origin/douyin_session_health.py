"""抖音会话健康跟踪：动作级埋点、登录拦截取证、掉线状态与写操作节流。

背景（线上 2026-09-16 user 54/260）：

- 08:00:26 最后一次写操作成功，08:05:32 已取不到账号身份，08:16:04 私信第一条就报
  "页面出现登录拦截"，但这段空白里日志没有 URL、没有验证类型、没有截图，只能靠时间窗反推；
- 掉线之后仍把 16 条私信/评论全部跑完，全是无效尝试；
- `CDP HTTP 200` 与"能连上浏览器"是两回事（同一天出现 `connect_over_cdp Timeout 30000ms`）。

这个模块只负责"看得见 + 立刻停"，不改变任何业务动作的成功路径：

1. 每条动作前后写 `logs/douyin_session_health.jsonl`；
2. 命中登录拦截时写 `logs/douyin_login_events.jsonl`（含 URL / 验证类型 / 页面文本 / 截图）；
3. 掉线状态落 `logs/douyin_session_state.json`（供看板与"自动暂停"使用）；
4. `allow_write_action()` 提供写操作节流（默认关闭，`min_interval_seconds=0` 即不生效）。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


_CLIENT_ROOT = Path(__file__).resolve().parents[2]  # backend/douyin_origin -> backend -> client root
_LOCK = threading.RLock()
_WRITE_HISTORY: Dict[tuple, list] = {}

CAPTCHA_SIGNALS = (
    ("slider", ("滑块", "拖动滑块", "拼图", "slider")),
    ("sms", ("短信验证", "验证码", "获取验证码", "verification code", "sms code")),
    ("qr", ("扫码登录", "请扫码", "二维码", "scan qr")),
    ("profile", ("身份验证", "实名认证", "verify identity")),
)

LOGIN_URL_SIGNALS = ("/login", "passport", "sso.") 
LOGIN_TEXT_SIGNALS = (
    "登录拦截",
    "页面出现登录拦截",
    "请先登录",
    "登录后即可",
    "扫码登录",
    "登录抖音",
    "未登录",
    "登录态已失效",
    "安全验证",
    "验证码",
)
WALL_EXCEPTION_SIGNALS = (
    "页面出现登录拦截",
    "浏览器未登录",
    "登录态已失效",
    "登录拦截",
    "当前抖音账号未处于可",
)


class DouyinLoginWallError(RuntimeError):
    """命中抖音登录拦截/未登录页时抛出，调用方据此整轮中止。"""

    def __init__(
        self,
        message: str = "",
        *,
        account_id: Any = None,
        url: str = "",
        captcha_type: str = "",
    ) -> None:
        super().__init__(message or "抖音页面出现登录拦截")
        self.account_id = account_id
        self.url = str(url or "")
        self.captcha_type = str(captcha_type or "")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def logs_dir(root: Optional[Any] = None) -> Path:
    base = Path(root) if root else _CLIENT_ROOT
    path = base / "logs"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return path


def session_health_path(root: Optional[Any] = None) -> Path:
    return logs_dir(root) / "douyin_session_health.jsonl"


def login_events_path(root: Optional[Any] = None) -> Path:
    return logs_dir(root) / "douyin_login_events.jsonl"


def timeline_path(root: Optional[Any] = None) -> Path:
    return logs_dir(root) / "douyin_session_timeline.jsonl"


def state_path(root: Optional[Any] = None) -> Path:
    return logs_dir(root) / "douyin_session_state.json"


def _append_jsonl(path: Path, payload: Dict[str, Any]) -> bool:
    """Append one JSON line.  Never raises: diagnostics must not break a task."""
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def extract_captcha(text: Any) -> Dict[str, str]:
    value = str(text or "")
    low = value.lower()
    for kind, signals in CAPTCHA_SIGNALS:
        for signal in signals:
            if signal.lower() in low:
                return {"type": kind, "signal": signal}
    return {"type": "none", "signal": ""}


def detect_login_wall(url: Any = "", text: Any = "") -> Dict[str, Any]:
    """页面是否已经是登录墙，以及抖音要的是哪种验证。"""
    url_value = str(url or "")
    text_value = str(text or "")
    url_signal = any(signal in url_value.lower() for signal in LOGIN_URL_SIGNALS)
    matched_text = next((signal for signal in LOGIN_TEXT_SIGNALS if signal in text_value), "")
    captcha = extract_captcha(text_value)
    return {
        "login_wall": bool(url_signal or matched_text),
        "url_signal": bool(url_signal),
        "text_signal": matched_text,
        "captcha": captcha,
        "url": url_value,
    }


async def snapshot_page(page: Any) -> Dict[str, str]:
    """Best-effort read of url/title/正文；任何一步失败都不影响其它字段。"""
    url = str(getattr(page, "url", "") or "")
    title = ""
    text = ""
    try:
        title = str(await page.title())
    except Exception:
        title = ""
    try:
        text = str(await page.evaluate("() => (document.body && document.body.innerText) || ''"))
    except Exception:
        text = ""
    return {"url": url, "title": title, "text": text}


async def capture_login_wall(
    page: Any,
    *,
    account_id: Any = None,
    action: str = "",
    reason: str = "",
    root: Optional[Any] = None,
    screenshot: bool = True,
) -> Dict[str, Any]:
    """记录登录拦截现场：URL、验证类型、页面文本片段、截图（可选）。"""
    snapshot = await snapshot_page(page)
    verdict = detect_login_wall(snapshot.get("url"), snapshot.get("text"))
    event: Dict[str, Any] = {
        "event": "douyin_login_wall",
        "at": now_iso(),
        "account_id": account_id,
        "action": action,
        "reason": reason,
        "login_wall": verdict["login_wall"],
        "url_signal": verdict["url_signal"],
        "text_signal": verdict["text_signal"],
        "captcha": verdict["captcha"],
        "url": verdict["url"],
        "title": snapshot.get("title", ""),
        "text_excerpt": str(snapshot.get("text", ""))[:500],
    }
    if screenshot:
        try:
            raw = await page.screenshot()
            if isinstance(raw, (bytes, bytearray)) and raw:
                shots = logs_dir(root) / "douyin_login_shots"
                shots.mkdir(parents=True, exist_ok=True)
                suffix = int(time.time())
                tag = account_id if account_id not in (None, "") else "na"
                target = shots / f"login_wall_{suffix}_{tag}.png"
                target.write_bytes(bytes(raw))
                event["screenshot"] = str(target)
        except Exception:
            pass
    _append_jsonl(login_events_path(root), event)
    return event


def record_session_health(root: Optional[Any] = None, **fields: Any) -> bool:
    payload = {"event": "douyin_session_health", "at": now_iso(), **fields}
    return _append_jsonl(session_health_path(root), payload)


def record_timeline(root: Optional[Any] = None, **fields: Any) -> bool:
    payload = {"event": "douyin_session_timeline", "at": now_iso(), **fields}
    return _append_jsonl(timeline_path(root), payload)


def record_login_wall_event(root: Optional[Any] = None, **fields: Any) -> bool:
    payload = {"event": "douyin_login_wall", "at": now_iso(), **fields}
    return _append_jsonl(login_events_path(root), payload)


def _load_state(root: Optional[Any] = None) -> Dict[str, Any]:
    path = state_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: Dict[str, Any], root: Optional[Any] = None) -> None:
    path = state_path(root)
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _account_key(account_id: Any) -> str:
    return str(account_id if account_id not in (None, "") else "default")


def mark_need_relogin(
    account_id: Any,
    *,
    reason: str = "",
    url: str = "",
    root: Optional[Any] = None,
) -> Dict[str, Any]:
    """标记该账号需要重新登录（供"自动暂停写任务 + 提示用户"使用）。"""
    state = _load_state(root)
    entry = {
        "need_relogin": True,
        "since": now_iso(),
        "reason": str(reason or "")[:300],
        "url": str(url or ""),
    }
    state[_account_key(account_id)] = entry
    _save_state(state, root)
    record_session_health(root, account_id=account_id, phase="need_relogin", **entry)
    return entry


def clear_need_relogin(account_id: Any, *, root: Optional[Any] = None) -> Dict[str, Any]:
    state = _load_state(root)
    key = _account_key(account_id)
    previous = state.get(key) or {}
    state[key] = {
        "need_relogin": False,
        "since": "",
        "reason": "",
        "url": "",
        "cleared_at": now_iso(),
        "was_since": str(previous.get("since") or ""),
    }
    _save_state(state, root)
    record_session_health(root, account_id=account_id, phase="relogin_cleared")
    return state[key]


def session_state(account_id: Any, *, root: Optional[Any] = None) -> Dict[str, Any]:
    state = _load_state(root)
    entry = state.get(_account_key(account_id))
    if not isinstance(entry, dict):
        return {"need_relogin": False, "since": "", "reason": "", "url": ""}
    return entry


def is_login_wall_error(exc: Any) -> bool:
    """登录拦截异常识别：新异常类型，或历史中文文案。"""
    if isinstance(exc, DouyinLoginWallError):
        return True
    message = str(exc or "")
    return any(signal in message for signal in WALL_EXCEPTION_SIGNALS)


def allow_write_action(
    account_id: Any,
    action: str,
    *,
    min_interval_seconds: float = 0,
    max_per_hour: int = 0,
    root: Optional[Any] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """写操作节流（默认关闭）。返回是否放行 + 需要等待的秒数。

    线上出现过一个账号一分钟内 8~20 次同类写操作（预扣/提交风暴），这是风控最敏感的特征；
    需要启用时把 `min_interval_seconds` / `max_per_hour` 传进来即可（可用环境变量做开关）。
    """
    timestamp = float(now if now is not None else time.time())
    key = (_account_key(account_id), str(action or "default"))
    with _LOCK:
        history = [item for item in _WRITE_HISTORY.get(key, []) if timestamp - item < 3600]
        last = history[-1] if history else None
        if min_interval_seconds and last is not None and (timestamp - last) < float(min_interval_seconds):
            wait = float(min_interval_seconds) - (timestamp - last)
            _WRITE_HISTORY[key] = history
            record_session_health(
                root,
                account_id=account_id,
                action=action,
                phase="throttled_interval",
                wait_seconds=round(wait, 1),
            )
            return {"allowed": False, "wait_seconds": round(wait, 1), "reason": "min_interval"}
        if max_per_hour and len(history) >= int(max_per_hour):
            _WRITE_HISTORY[key] = history
            record_session_health(
                root,
                account_id=account_id,
                action=action,
                phase="throttled_hourly",
                used_last_hour=len(history),
            )
            return {"allowed": False, "wait_seconds": 0.0, "reason": "hourly_cap", "used_last_hour": len(history)}
        history.append(timestamp)
        _WRITE_HISTORY[key] = history
    return {"allowed": True, "wait_seconds": 0.0, "reason": "", "used_last_hour": len(history)}
