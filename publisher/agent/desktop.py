"""Desktop (Windows UIA) adapter for WeChat takeover."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import logging
from typing import Any, Dict, List, Optional

from .budget import Budget, agent_mode
from .models import Action, Observation, RecoverResult
from .planner import decide_sync
from .trace import TraceWriter

logger = logging.getLogger(__name__)

# 个人微信 = Weixin.exe / WeChat.exe；企业微信 = WXWork.exe。
# 标题里都带“微信”，只按标题匹配会把企业微信当成个人微信（真机上已经踩过一次）。
_WECHAT_PROCESS_NAMES = {"weixin.exe", "wechat.exe"}
_BLOCKED_PROCESS_NAMES = {"wxwork.exe", "wework.exe"}


def _process_name(pid: int) -> str:
    try:
        import psutil
        return str(psutil.Process(int(pid)).name() or "").lower()
    except Exception:
        return ""


def _is_personal_wechat(pid: int) -> bool:
    name = _process_name(pid)
    if not name:
        return True  # 查不到进程名时不要误杀，交给类名/标题判断
    if name in _BLOCKED_PROCESS_NAMES:
        return False
    return name in _WECHAT_PROCESS_NAMES


def find_window(keyword: str = "微信") -> Any:
    """找“微信”主窗口。

    踩过的坑：企业微信的窗口标题是「企业微信」，也包含“微信”，
    pywinauto 按标题包含匹配会直接拿成 企业微信（class=WeWorkWindow），
    后面所有观察/点击都打在错误的应用上，还“验证通过”。所以这里显式排掉 WeWork。
    """
    try:
        from pywinauto import Desktop
        best = None
        best_score = -1e9
        for win in Desktop(backend="uia").windows():
            try:
                title = str(win.window_text() or "").strip()
                cls = str(win.class_name() or "")
                if not title or keyword not in title:
                    continue
                if "企业微信" in title or "WeWorkWindow" in cls or "WeCom" in cls:
                    continue
                if "FileExportWindow" in cls or "Notification" in cls:
                    continue
                try:
                    if not _is_personal_wechat(int(win.process_id())):
                        continue
                except Exception:
                    pass
                r = win.rectangle()
                area = max(0, (r.right - r.left)) * max(0, (r.bottom - r.top))
                if area < 300 * 300:
                    continue
                score = area / 1e6
                if "MainWindow" in cls or "mmui" in cls:
                    score += 50
                if title == keyword:
                    score += 20
                if score > best_score:
                    best_score = score
                    best = win
            except Exception:
                continue
        if best is not None:
            return best
    except Exception as exc:
        logger.debug("[agent] find_window failed: %s", exc)
    # 兜底：微信被最小化/收进托盘时，pywinauto 的 windows() 看不到它（top-level 隐藏），
    # 但 win32 枚举仍能拿到句柄 —— 这一步是“接管失败”最常见的现场。
    try:
        import win32gui
        from pywinauto import Desktop

        picked = []
        def _cb(hwnd: int, _param) -> bool:
            try:
                title = win32gui.GetWindowText(hwnd).strip()
                cls = win32gui.GetClassName(hwnd)
                if not title or keyword not in title:
                    return True
                if "企业微信" in title or "WeWorkWindow" in cls or "WeCom" in cls:
                    return True
                if "FileExportWindow" in cls or "Notification" in cls:
                    return True
                if not (cls.startswith("Qt") or "mmui" in cls):
                    return True
                try:
                    import win32process
                    _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
                except Exception:
                    pid = 0
                if pid and not _is_personal_wechat(pid):
                    return True
                l, t, r, b = win32gui.GetWindowRect(hwnd)
                area = max(0, r - l) * max(0, b - t)
                if area < 300 * 300:
                    return True
                picked.append((area, hwnd))
            except Exception:
                pass
            return True

        win32gui.EnumWindows(_cb, None)
        if picked:
            picked.sort(reverse=True)
            return Desktop(backend="uia").window(handle=picked[0][1])
    except Exception as exc:
        logger.debug("[agent] win32 fallback failed: %s", exc)
    return None


def find_sns_window() -> Any:
    """朋友圈是独立顶层窗口（class=Qt51514QWindowIcon，title=朋友圈）。"""
    try:
        import win32gui
        from pywinauto import Desktop
    except Exception:
        return None
    picked = []
    def _cb(hwnd: int, _p) -> bool:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd).strip()
            cls = win32gui.GetClassName(hwnd)
            if title != "朋友圈" and "SNS" not in cls:
                return True
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            area = max(0, r - l) * max(0, b - t)
            if area < 200 * 200:
                return True
            picked.append((area, hwnd))
        except Exception:
            pass
        return True
    try:
        win32gui.EnumWindows(_cb, None)
        if picked:
            picked.sort(reverse=True)
            return Desktop(backend="uia").window(handle=picked[0][1])
    except Exception as exc:
        logger.debug("[agent] find_sns_window failed: %s", exc)
    return None


def activate_window(window: Any) -> bool:
    """把窗口弄到前台：最小化 / 收到托盘 / 被别的窗口盖住时，点击和输入都不会真正生效。
    只靠 pywinauto.set_focus() 在微信这种 Qt 无边框窗口上不可靠，先走一遍 win32。"""
    hwnd = int(getattr(window, "handle", 0) or 0)
    if hwnd:
        try:
            import win32con
            import win32gui
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            elif not win32gui.IsWindowVisible(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
            win32gui.SetForegroundWindow(hwnd)
        except Exception as exc:
            logger.debug("[agent] win32 activate failed: %s", exc)
    for call in ("set_focus", "restore"):
        try:
            getattr(window, call)()
        except Exception:
            continue
    try:
        return bool(window.is_active())
    except Exception:
        return False


def _rect(window: Any) -> Optional[tuple]:
    try:
        r = window.rectangle()
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        return None


def _window_visible(window: Any) -> bool:
    """窗口是不是真的在屏幕上。UI 元素就算窗口被最小化/收到托盘，UIA 里仍报 visible。"""
    hwnd = int(getattr(window, "handle", 0) or 0)
    if not hwnd:
        return True
    try:
        import win32gui
        return bool(win32gui.IsWindowVisible(hwnd)) and not win32gui.IsIconic(hwnd)
    except Exception:
        return True


def _chat_left(window: Any, left: int, width: int) -> int:
    """会话列右边界：优先用 session_list 控件的真实 rect，取不到再按比例估。"""
    for c, info in _iter_controls(window, limit=600):
        try:
            if str(getattr(info, "automation_id", "") or "") == "session_list":
                return int(c.rectangle().right)
        except Exception:
            continue
    return int(left + width * 0.28)


def _region(x: int, left: int, width: int) -> str:
    if width <= 0:
        return "other"
    rel = (x - left) / float(width)
    if rel < 0.06:
        return "nav"
    if rel < 0.28:
        return "sessions"
    return "chat"


def observe_desktop_window(window: Any, *, stage: str) -> Observation:
    """观察要有用：之前只取 descendants()[:60]，微信前 60 个全是左侧导航按钮，
    会话列表和聊天区根本进不来（真机 13 个节点，全是「通讯录/收藏/朋友圈」）。"""
    nodes: List[Dict[str, Any]] = []
    title = ""
    try:
        title = str(window.window_text() or "")
    except Exception:
        pass
    rect = _rect(window) or (0, 0, 0, 0)
    left, width = rect[0], rect[2] - rect[0]
    chat_left = _chat_left(window, left, width)
    seen = set()
    try:
        controls = window.descendants()
    except Exception:
        controls = []
    for c in controls[:1200]:
        try:
            info = c.element_info
            name = str(info.name or "").strip()
            if not name:
                continue
            rc = c.rectangle()
            w, h = int(rc.right - rc.left), int(rc.bottom - rc.top)
            if w <= 0 or h <= 0:
                continue
            try:
                if not c.is_visible():
                    continue
            except Exception:
                pass
            key = (str(info.control_type or ""), name[:40], int(rc.left) // 4, int(rc.top) // 4)
            if key in seen:
                continue
            seen.add(key)
            nodes.append({
                "region": ("chat" if int(rc.left) >= chat_left else _region(int(rc.left), left, width)),
                "role": str(info.control_type or ""),
                "text": name[:80],
                "x": int(rc.left), "y": int(rc.top), "w": w, "h": h,
                "enabled": True,
            })
        except Exception:
            continue
    # 分区配额：会话列/聊天区不能被导航按钮挤掉
    priority = {"sessions": 0, "chat": 1, "nav": 2, "other": 3}
    nodes.sort(key=lambda n: (priority.get(n["region"], 9), n["y"]))
    nodes = nodes[:80]
    shot_b64 = ""
    try:
        from PIL import ImageGrab
        if rect and rect[2] > rect[0] and rect[3] > rect[1]:
            img = ImageGrab.grab(bbox=rect)
            longest = max(img.size)
            if longest > 1024:
                ratio = 1024.0 / longest
                img = img.resize((max(1, int(img.width * ratio)), max(1, int(img.height * ratio))))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=70)
            shot_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:
        logger.debug("[agent] desktop screenshot failed: %s", exc)
    sig_src = "desktop|" + stage + "|" + title + "|" + "|".join(
        (str(n.get("region")) + ":" + str(n.get("role")) + ":" + str(n.get("text")))[:40] for n in nodes[:40]
    )
    signature = "sig_" + hashlib.sha256(sig_src.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return Observation(env="desktop", stage=stage, url="", title=title, nodes=nodes,
                       visible_text="", screenshot_b64=shot_b64, signature=signature)


def _iter_controls(window: Any, limit: int = 1200):
    try:
        controls = window.descendants()[:limit]
    except Exception:
        controls = []
    for c in controls:
        try:
            info = c.element_info
        except Exception:
            continue
        yield c, info


def _visible(el: Any) -> bool:
    try:
        return bool(el.is_visible())
    except Exception:
        return True


def _find_control(window: Any, name: str, *, region: str = "", exact_first: bool = True) -> Any:
    """按名字找控件：先精确再包含，跳过不可见项，可选限定区域（sessions/chat/nav）。"""
    want = str(name or "").strip()
    if not want:
        return None
    if want.startswith("aid:"):
        want_aid = want[4:]
        for c, info in _iter_controls(window):
            try:
                if str(getattr(info, "automation_id", "") or "") == want_aid and _visible(c):
                    return c
            except Exception:
                continue
        return None
    rect = _rect(window) or (0, 0, 0, 0)
    left, width = rect[0], rect[2] - rect[0]
    # 控件类型优先级：可点/可输入的优先，避免命中同名 Text（微信里 "新的朋友" 既是 ListItem 又是 Text）
    type_rank = {"Button": 0, "ListItem": 1, "Edit": 2, "TreeItem": 3, "MenuItem": 4, "Text": 9}
    best = None
    best_rank = 99
    loose = None
    for c, info in _iter_controls(window):
        try:
            text = str(info.name or "").strip()
            if not text:
                continue
            if not _visible(c):
                continue
            if region:
                rc = c.rectangle()
                if _region(int(rc.left), left, width) != region:
                    continue
            ctype = str(info.control_type or "")
            rank = type_rank.get(ctype, 5)
            if exact_first and text == want:
                if rank < best_rank:
                    best, best_rank = c, rank
                    if rank == 0:
                        return c
                continue
            if loose is None and want in text:
                loose = c
        except Exception:
            continue
    return best if best is not None else loose


def _session_rows(window: Any) -> List[Any]:
    rect = _rect(window) or (0, 0, 0, 0)
    left, width = rect[0], rect[2] - rect[0]
    rows = []
    for c, info in _iter_controls(window):
        try:
            if str(info.control_type or "") != "ListItem":
                continue
            rc = c.rectangle()
            if _region(int(rc.left), left, width) != "sessions":
                continue
            if not _visible(c):
                continue
            rows.append(c)
        except Exception:
            continue
    return rows


def verify_desktop_stage(window: Any, stage: str, *, expect_text: str = "", guard_name: str = "") -> bool:
    """阶段的硬条件，不能只看“某个动作执行成功”。"""
    stage = str(stage or "").lower()
    if stage == "wechat_moments":
        if not _window_visible(window):
            return False
        rect = _rect(window) or (0, 0, 0, 0)
        rows = 0
        for c, info in _iter_controls(window, limit=600):
            try:
                if str(info.control_type or "") == "ListItem" and _visible(c) and (c.rectangle().right - c.rectangle().left) > 200:
                    rows += 1
            except Exception:
                continue
        return rows >= 2
    if stage == "wechat_moments_like":
        if not _window_visible(window):
            return False
        return _find_control(window, "取消赞", region="") is not None
    if stage == "wechat_moments_publish":
        if not _window_visible(window):
            return False
        for text in ("这一刻的想法", "发表", "谁可以看"):
            if _find_control(window, text) is not None:
                return True
        return False
    if stage == "wechat_friend_requests":
        if not _window_visible(window):
            return False
        rows = list_friend_requests(window)
        if not rows:
            return False
        # 申请页的特征：行里有「已添加 / 验证语」，或带「接受」按钮
        for item in rows:
            if item["status"] == "accepted" or "验证" in item["raw"] or "请求" in item["raw"]:
                return True
        for c, info in _iter_controls(window, limit=600):
            try:
                nm = str(info.name or "")
                if _visible(c) and ("接受" in nm or "通过验证" in nm):
                    return True
            except Exception:
                continue
        return False
    if stage == "wechat_send":
        if not _window_visible(window):
            return False
        if expect_text:
            for c, info in _iter_controls(window):
                try:
                    if expect_text in str(info.name or "") and _visible(c):
                        return _guard_ok(window, guard_name)
                except Exception:
                    continue
            return False
        return _guard_ok(window, guard_name)
    if stage == "wechat_sessions":
        if not _window_visible(window):
            return False
        # 真机确认的硬标志（微信 4.x）：聊天页可见 automation_id=session_list / chat_message_list；
        # 通讯录页这两个都不可见（只剩 contact_list）。所以“没有这两个 = 不在聊天页”，
        # 不能用“搜索框 + 左侧有行”兜底——通讯录页同样满足，会假成功。
        aids = set()
        for _c, info in _iter_controls(window, limit=600):
            try:
                if not _visible(_c):
                    continue
                aid = str(getattr(info, "automation_id", "") or "")
                if aid:
                    aids.add(aid)
            except Exception:
                continue
        if "session_list" in aids or "chat_message_list" in aids:
            return True
        if aids:
            return False
        # 老版本兜底：聊天页一定有输入框（chat 区里宽度很大的 Edit）
        rect = _rect(window) or (0, 0, 0, 0)
        left, width = rect[0], rect[2] - rect[0]
        for c, info in _iter_controls(window, limit=600):
            try:
                if str(info.control_type or "") != "Edit":
                    continue
                rc = c.rectangle()
                if _region(int(rc.left), left, width) == "chat" and (rc.right - rc.left) > 300 and _visible(c):
                    return True
            except Exception:
                continue
        return False
    return True


def list_friend_requests(window: Any) -> List[Dict[str, str]]:
    """只读枚举「新的朋友」页里的申请记录：已通过/待处理分开。

    微信把申请记录做成 ListItem，文本形如「昵称 验证语 已添加」；
    没有「已添加」的就是还没处理的（可能带「接受」按钮）。
    """
    out: List[Dict[str, str]] = []
    for c, info in _iter_controls(window):
        try:
            if str(info.control_type or "") != "ListItem":
                continue
            if not _visible(c):
                continue
            name = str(info.name or "").strip()
            if not name or len(name) < 2:
                continue
            raw = " ".join(name.split())
            if raw in {"新的朋友", "群聊", "标签", "公众号", "服务号", "企业微信联系人", "我的企业"}:
                continue
            status = "accepted" if "已添加" in raw else "pending"
            head = raw
            for marker in ("已添加", "已过期", "等待验证"):
                if marker in head:
                    head = head.split(marker)[0]
                    break
            out.append({"name": head.strip()[:40], "status": status, "raw": raw[:120]})
        except Exception:
            continue
    return out


def verify_desktop(window: Any, expect: Optional[Dict[str, Any]]) -> bool:
    expect = dict(expect or {})
    kind = str(expect.get("kind") or "").lower()
    if not kind or kind == "any":
        return False  # 没写期望就不算验证通过，否则第一个动作就“成功”了
    if kind in {"text", "role"}:
        value = str(expect.get("value") or expect.get("name") or "")
        return _find_control(window, value, region=str(expect.get("region") or "")) is not None
    if kind == "title_contains":
        try:
            return str(expect.get("value") or "") in str(window.window_text() or "")
        except Exception:
            return False
    if kind == "session_list":
        return verify_desktop_stage(window, "wechat_sessions")
    if kind in {"element_count", "count"}:
        # 模型常用网页那套写法；桌面端就按会话行数理解
        want = int(expect.get("min") or expect.get("value") or 1)
        return len(_session_rows(window)) >= want
    logger.warning("[agent] unknown desktop expect kind=%s -> unverified", kind)
    return False


# 接管场景的硬边界：只允许“把界面摆回聊天页”，不允许打字/回车。
# 微信里往输入框打字再回车，等于以本人身份给客户发消息。
def _send_allowlist() -> List[str]:
    raw = os.environ.get("LOBSTER_WX_SEND_ALLOWLIST") or ""
    return [x.strip() for x in raw.split(",") if x.strip()]


def _chat_input(window: Any) -> Any:
    """聊天输入框：优先用真机确认的 automation_id=chat_input_field。"""
    ctl = _find_control(window, "aid:chat_input_field")
    if ctl is not None:
        return ctl
    rect = _rect(window) or (0, 0, 0, 0)
    left, width = rect[0], rect[2] - rect[0]
    for c, info in _iter_controls(window):
        try:
            if str(info.control_type or "") != "Edit":
                continue
            rc = c.rectangle()
            if _region(int(rc.left), left, width) == "chat" and (rc.right - rc.left) > 300 and _visible(c):
                return c
        except Exception:
            continue
    return None


def _current_chat_name(window: Any) -> str:
    ctl = _chat_input(window)
    if ctl is None:
        return ""
    try:
        return str(ctl.element_info.name or "").strip()
    except Exception:
        return ""


def _guard_ok(window: Any, guard_name: str) -> bool:
    guard = str(guard_name or "").strip()
    if not guard:
        guard = (_send_allowlist() or [""])[0]
    allowed = _send_allowlist()
    current = _current_chat_name(window)
    if not current:
        return False
    if guard and current != guard and not current.startswith(guard):
        logger.warning("[agent] send guard: 当前会话=%s 目标=%s", current, guard)
        return False
    if allowed and not any(current == a or current.startswith(a) for a in allowed):
        logger.warning("[agent] send guard: 当前会话=%s 不在白名单 %s", current, allowed)
        return False
    return True


_STAGE_ALLOWED_ACTIONS = {
    "wechat_sessions": {"click", "scroll", "close_overlay", "wait_for", "screenshot", "press", "abort"},
    # 只有这个阶段允许打字，且只在白名单会话里
    "wechat_send": {"click", "scroll", "close_overlay", "wait_for", "screenshot", "press", "type", "send", "abort"},
}


def run_desktop_action(window: Any, action: Action, *, stage: str = "", guard_name: str = "") -> Any:
    from .models import StepResult
    name = action.action
    target = action.target or {}
    allowed = _STAGE_ALLOWED_ACTIONS.get(str(stage or "").lower())
    if allowed is not None and name not in allowed:
        return StepResult(False, f"action '{name}' blocked in stage '{stage}'")
    if name == "press":
        key = str(target.get("value") or action.text or "").strip().upper()
        ok_keys = {"{ESC}", "ESC", "ESCAPE"}
        if str(stage or "").lower() == "wechat_send":
            ok_keys |= {"{ENTER}", "ENTER", "{~}", "RETURN"}
        if key not in ok_keys:
            return StepResult(False, f"press blocked ({key})")
    activate_window(window)
    try:
        if name == "abort":
            return StepResult(False, "agent_abort")
        if name == "press":
            from pywinauto import keyboard
            keyboard.send_keys(str(target.get("value") or action.text or "{ESC}"))
            return StepResult(True, "pressed")
        if name == "close_overlay":
            from pywinauto import keyboard
            keyboard.send_keys("{ESC}")
            return StepResult(True, "overlay_closed")
        if name == "scroll":
            from pywinauto import mouse
            x, y = int(target.get("x") or 0), int(target.get("y") or 0)
            mouse.scroll(coords=(x, y), wheel_dist=int(target.get("value") or -3))
            return StepResult(True, "scrolled")
        if name == "send":
            # 一条动作做完“清空草稿 → 写入 → 回车发送”，只在白名单会话、只在 wechat_send 阶段
            if str(stage or "").lower() != "wechat_send":
                return StepResult(False, "send blocked outside wechat_send")
            if not _guard_ok(window, guard_name):
                return StepResult(False, "send_guard_blocked")
            text = str(action.text or target.get("text") or "")
            if not text:
                return StepResult(False, "empty_text")
            control = _chat_input(window)
            if control is None:
                return StepResult(False, "chat_input_not_found")
            control.click_input()
            from pywinauto import keyboard
            keyboard.send_keys("^a")
            keyboard.send_keys("{DELETE}")
            keyboard.send_keys(text, with_spaces=True)
            keyboard.send_keys("{ENTER}")
            return StepResult(True, "sent")
        if str(target.get("by") or "").lower() == "point":
            from pywinauto import mouse
            mouse.click(coords=(int(target.get("x") or 0), int(target.get("y") or 0)))
            return StepResult(True, "clicked_point")
        control = _find_control(window, str(target.get("value") or target.get("name") or ""),
                                region=str(target.get("region") or ""))
        if control is None and name == "wait_for":
            return StepResult(False, "target_not_found")
        if name == "wait_for":
            return StepResult(True, "visible")
        if control is None:
            return StepResult(False, "target_not_found")
        if name == "click":
            control.click_input()
            return StepResult(True, "clicked")
        if name == "type":
            text = str(action.text or target.get("text") or "")
            if not text:
                return StepResult(False, "empty_text")
            if str(stage or "").lower() == "wechat_send":
                # 只往“当前会话的输入框”里写字，且当前会话必须在白名单里
                if not _guard_ok(window, guard_name):
                    return StepResult(False, "send_guard_blocked")
                control = _chat_input(window)
                if control is None:
                    return StepResult(False, "chat_input_not_found")
            if control is None:
                return StepResult(False, "target_not_found")
            control.click_input()
            from pywinauto import keyboard
            keyboard.send_keys(text, with_spaces=True)
            return StepResult(True, "typed")
        return StepResult(False, "unsupported_action")
    except Exception as exc:
        return StepResult(False, f"{type(exc).__name__}: {exc}"[:200])


def recover_desktop(
    window: Any = None,
    *,
    stage: str,
    goal: str,
    max_steps: int = 8,
    timeout_seconds: float = 60.0,
    mode: Optional[str] = None,
    guard_name: str = "",
    expect_text: str = "",
) -> RecoverResult:
    from . import memory
    mode = (mode or agent_mode("desktop")).lower()
    result = RecoverResult(mode=mode)
    if mode == "off":
        return result
    win = window or find_window()
    if win is None:
        result.error = "window_not_found"
        return result
    activate_window(win)
    writer = TraceWriter(platform="wechat", stage=stage, env="desktop")
    budget = Budget(max_steps=max_steps, timeout_seconds=timeout_seconds)
    history = []
    try:
        first = observe_desktop_window(win, stage=stage)
        writer.event("observe", signature=first.signature, title=first.title, nodes=len(first.nodes))
        shot = writer.save_screenshot(first.screenshot_b64, "observe")
        if shot:
            writer.event("screenshot", path=shot)
        if mode == "observe":
            writer.finish(False, 0, "observe_only")
            return result
        # 快速通道：阶段本来就正常就别调 LLM（每次决策最低 10 算力）
        if verify_desktop_stage(win, stage, expect_text=expect_text, guard_name=guard_name):
            result.ok = True
            result.recovered = False
            result.steps = 0
            writer.finish(True, 0, "already_ok")
            return result
        executed = []
        while budget.take_step():
            obs = observe_desktop_window(win, stage=stage)
            action = decide_sync(obs, goal, history, budget)
            if action is None:
                result.error = "no_action"
                break
            writer.event("decide", action=action.action, target=action.target,
                         confidence=action.confidence, thought=action.thought[:300])
            if action.action == "abort":
                result.error = "agent_abort"
                break
            step = run_desktop_action(win, action, stage=stage, guard_name=guard_name)
            verified = bool(step.ok) and verify_desktop(win, action.expect)
            history.append({"action": action.action, "target": action.target, "ok": step.ok,
                            "verified": verified, "error": step.error})
            writer.event("step", action=action.action, ok=step.ok, verified=verified, error=step.error)
            if verified:
                executed.append({"action": action.action, "target": action.target,
                                 "text": action.text, "expect": action.expect})
            # 只有阶段硬条件满足才算恢复成功
            if verify_desktop_stage(win, stage, expect_text=expect_text, guard_name=guard_name):
                result.ok = True
                result.recovered = True
                if executed:
                    memory.record_success(obs.signature, executed)
                break
        result.steps = budget.steps
        if not result.ok:
            memory.record_failure(first.signature)
            if not result.error:
                result.error = "recovery_failed"
        writer.finish(result.ok, result.steps, result.error)
        return result
    except Exception as exc:
        logger.warning("[agent] desktop recovery crashed: %s", exc)
        result.error = f"{type(exc).__name__}: {exc}"[:200]
        writer.finish(False, budget.steps, result.error)
        return result
