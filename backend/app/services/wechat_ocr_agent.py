"""LLM 驱动的微信操作代理：观察 → 思考 → 动作 → 校验，一步步推进。

为什么要有这一层：
- 微信 4.2 的 UIA 树失效，固定脚本只能写死每一步；界面一变（弹窗、引导、位置漂移）就死；
- 人的做法是"看一眼、判断在哪一步、点该点的地方、没反应就换个点法"——
  这里把这套循环交给模型，动作层仍用 OCR 原语（wechat_ocr_actions/driver）。

约定：
- 模型只允许输出受限动作集（见 _ACTIONS），每次一个动作；
- 动作前必须能从当前观察里找到目标（click_text 要求文本真实存在）；
- 每个动作后重新观察，把"界面变化"回灌给模型，避免原地打转；
- 出现 done/fail、步数上限、或连续无变化即结束。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
import win32con

from backend.app.services import wechat_ocr_actions as act
from backend.app.services import wechat_ocr_driver as drv

MAX_STEPS = 16
MAX_TEXTS = 42

_SYSTEM = """你是操作 Windows 微信 4.2 的自动化代理。这个版本没有 UIA 控件树，你只能通过截图 OCR 的文本和坐标来操作。

规矩：
1. 每一步只输出一个动作，输出必须是严格 JSON（不要 markdown 代码块、不要多余文字）。
2. 只能点当前观察里真实存在的文本；看不到就先 wait 或 scroll，不许猜坐标。
3. 每次动作后系统会告诉你界面有没有变化；连续两次没变化就必须换做法，不能重复同一个动作。
4. 不要点"删除/取消/退出/关闭/投诉/举报/支付/转账"这类破坏性按钮，除非目标明确要求取消。
5. 达成目标立刻 done；确实做不到才 fail。

可用动作（JSON）：
{"thought":"...","action":"click_text","text":"要点的文字"}
{"thought":"...","action":"click_xy","x":0.18,"y":0.17}          // 窗口内相对坐标 0~1
{"thought":"...","action":"type","text":"要输入的内容"}            // 先 click 输入框再 type
{"thought":"...","action":"key","key":"enter|esc|tab|backspace"}
{"thought":"...","action":"scroll","notches":5,"x":0.5,"y":0.5}   // 正数向下
{"thought":"...","action":"wait","seconds":1.5}
{"thought":"...","action":"done","status":"submitted|pending_submit|already_friend|not_found","summary":"..."}
{"thought":"...","action":"fail","reason":"..."}
"""


# ------------------------------------------------------------------ LLM

def _ai_client(model: str = ""):
    backend = str(Path(__file__).resolve().parents[2])
    for p in (backend, str(Path(backend) / "douyin_origin")):
        if p not in sys.path:
            sys.path.insert(0, p)
    from douyin_origin.douyin_api import create_ai_client

    return create_ai_client(model)


_FALLBACK_MODELS = ["gpt-4o-mini", "gpt-4o", "deepseek-chat"]
_ACTIVE_MODEL: Optional[str] = None


def _post_chat(client, body: Dict[str, Any], timeout: float = 90) -> Dict[str, Any]:
    """调 chat/completions，带模型降级（配置的 gpt-5.4 可能无渠道）+ 429 退避。"""
    global _ACTIVE_MODEL
    models: List[str] = []
    if _ACTIVE_MODEL:
        models.append(_ACTIVE_MODEL)
    base = str(body.get("model") or client.model)
    models.append(base)
    models += [m for m in _FALLBACK_MODELS if m not in models]
    last = "unknown"
    for m in models:
        for attempt in range(2):
            try:
                r = requests.post(client.api_url, headers=client.headers,
                                  json=dict(body, model=m), timeout=timeout)
                if r.status_code == 200:
                    _ACTIVE_MODEL = m
                    return r.json()
                last = f"{m} HTTP {r.status_code} {r.text[:100]}"
                if r.status_code in (408, 429, 500, 502, 503, 504):
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break
            except Exception as exc:
                last = f"{m} ERR {exc}"
                time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"模型都不可用: {last}")


def _parse_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    raise ValueError(f"模型返回不是 JSON: {text[:160]}")


def llm_decide(client, goal: str, obs: Dict[str, Any], history: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload_obs = {
        "goal": goal,
        "state": obs.get("state"),
        "window": obs["window"],
        "screen_texts": obs["texts"],
        "history": history[-8:],
    }
    user = (
        "当前观察（窗口内相对坐标，x/y 是文本中心除以窗口宽高）：\n"
        + json.dumps(payload_obs, ensure_ascii=False)
        + "\n\n输出下一步动作的 JSON。"
    )
    body = {
        "model": client.model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
    }
    data = _post_chat(client, body)
    content = data["choices"][0]["message"]["content"]
    return _parse_json(content)


# ------------------------------------------------------------------ 观察

_NOISE = {"口", "X", "x", "α", "O", "o", "Q", "8=", "六", "—", "-", "·", "V", "v"}
_NOISE_PARTS = ("分钟前", "小时前", "刚刚", "昨天", "前天", "星期", "上午", "下午")


def _is_noise_item(it: Dict[str, Any], rect: Tuple[int, int, int, int]) -> bool:
    text = (it["text"] or "").strip()
    if not text:
        return True
    if text in _NOISE or len(text) <= 1:
        return True
    if any(p in text for p in _NOISE_PARTS):
        return True
    l, t, r, b = rect
    h = max(1, b - t)
    # 窗口控制按钮/标题栏（顶部 4%）
    if it["cy"] - t < h * 0.04:
        return True
    return False


def state_of(items: Sequence[Dict[str, Any]]) -> str:
    """把 OCR 结果归到一个页面状态，用于校验每步是否真的推进了。"""
    texts = [it["text"] for it in items]
    joined = "\n".join(texts)
    if "搜索微信号" in joined or ("添加朋友" in joined and "搜索" in joined):
        return "add_page"
    if any(k in joined for k in ("添加到通讯录", "申请添加朋友", "加为朋友")):
        return "result_card"
    if any(k in joined for k in ("找不到", "无法找到", "用户不存在")):
        return "not_found"
    if "发送添加朋友申请" in joined or ("发送" in joined and "申请" in joined):
        return "apply_form"
    if "发起群聊" in joined and "添加朋友" in joined:
        return "plus_menu"
    if any(t.strip() in ("发消息", "语音聊天", "视频聊天") for t in texts):
        return "profile_card"
    # 聊天页没有「搜索」占位文字（只有放大镜图标），靠会话列表特征判
    if "搜索" in joined:
        return "chat"
    if any(t.strip() in ("服务号", "公众号", "订阅号", "文件传输助手", "微信") for t in texts):
        return "chat"
    return "unknown"


def observe(hwnd: int, *, rect: Tuple[int, int, int, int]) -> Dict[str, Any]:
    l, t, r, b = rect
    w, h = max(1, r - l), max(1, b - t)
    items = [it for it in drv.read_window(hwnd) if not _is_noise_item(it, rect)]
    texts = []
    for it in items[:MAX_TEXTS]:
        texts.append({
            "t": it["text"][:40],
            "x": round((it["cx"] - l) / w, 3),
            "y": round((it["cy"] - t) / h, 3),
        })
    return {
        "window": {"w": w, "h": h},
        "state": state_of(items),
        "texts": texts,
        "raw": items,
        "keys": {it["text"] for it in items},
    }


def _diff_texts(before: set, after: set, limit: int = 8) -> str:
    added = [x for x in after - before if x.strip()][:limit]
    gone = [x for x in before - after if x.strip()][:limit]
    parts = []
    if added:
        parts.append("新增:" + "|".join(x[:18] for x in added))
    if gone:
        parts.append("消失:" + "|".join(x[:18] for x in gone))
    return "；".join(parts) or "无变化"


# ------------------------------------------------------------------ 执行

def _pick(items: Sequence[Dict[str, Any]], text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    exact = [it for it in items if it["text"].strip() == text.strip()]
    if exact:
        return exact[0]
    cand = [it for it in items if text.strip() in it["text"]]
    if not cand:
        return None
    cand.sort(key=lambda it: len(it["text"]))
    return cand[0]


def run_action(hwnd: int, rect: Tuple[int, int, int, int], observation: Dict[str, Any],
               action: Dict[str, Any]) -> Tuple[bool, str]:
    kind = str(action.get("action") or "")
    l, t, r, b = rect
    w, h = max(1, r - l), max(1, b - t)
    try:
        if kind == "click_text":
            hit = _pick(observation["raw"], str(action.get("text") or ""))
            if not hit:
                return False, f"未找到文本 {action.get('text')!r}"
            drv.click_checked(hwnd, hit["cx"], hit["cy"])
            return True, f"点击 {hit['text'][:16]!r}"
        if kind == "click_xy":
            x = l + int(float(action.get("x", 0.5)) * w)
            y = t + int(float(action.get("y", 0.5)) * h)
            drv.click_checked(hwnd, x, y)
            return True, f"点击 ({action.get('x')},{action.get('y')})"
        if kind == "type":
            act._paste_text(str(action.get("text") or ""))
            return True, "输入文本"
        if kind == "key":
            vk = {"enter": win32con.VK_RETURN, "esc": win32con.VK_ESCAPE,
                  "tab": win32con.VK_TAB, "backspace": win32con.VK_BACK}.get(
                str(action.get("key") or "").lower())
            if not vk:
                return False, f"不支持的按键 {action.get('key')!r}"
            act._tap(vk)
            return True, f"按键 {action.get('key')}"
        if kind == "scroll":
            x = l + int(float(action.get("x", 0.5)) * w)
            y = t + int(float(action.get("y", 0.5)) * h)
            drv.wheel_at(x, y, int(action.get("notches", 3)), hwnd=hwnd)
            return True, f"滚动 {action.get('notches')}"
        if kind == "wait":
            time.sleep(float(action.get("seconds", 1.0)))
            return True, "等待"
        return False, f"未知动作 {kind!r}"
    except Exception as exc:
        return False, f"动作异常: {exc}"


# ------------------------------------------------------------------ 主循环

def run_goal(
    goal: str,
    *,
    model: str = "",
    max_steps: int = MAX_STEPS,
    settle: float = 1.0,
    verbose: bool = True,
) -> Dict[str, Any]:
    hwnd = drv.find_wechat_hwnd()
    if not hwnd:
        return {"ok": False, "error": "没找到微信窗口"}
    rect = act._focus(hwnd)
    client = _ai_client(model)
    history: List[Dict[str, Any]] = []
    obs = observe(hwnd, rect=rect)
    last_keys = obs["keys"]
    stale = 0

    for step in range(1, max_steps + 1):
        try:
            decision = llm_decide(client, goal, obs, history)
        except Exception as exc:
            return {"ok": False, "error": f"模型调用失败: {exc}", "steps": history}

        kind = str(decision.get("action") or "")
        thought = str(decision.get("thought") or "")[:120]
        if verbose:
            print(f"[{step}] {kind} | {thought} | {json.dumps(decision, ensure_ascii=False)[:120]}")

        if kind == "done":
            return {"ok": True, "status": decision.get("status") or "done",
                    "summary": decision.get("summary") or "", "steps": history,
                    "model": _ACTIVE_MODEL or client.model}
        if kind == "fail":
            return {"ok": False, "error": decision.get("reason") or "模型判定失败",
                    "steps": history, "model": client.model}

        ok, effect = run_action(hwnd, rect, obs, decision)
        time.sleep(settle)
        new_obs = observe(hwnd, rect=rect)
        delta = _diff_texts(last_keys, new_obs["keys"]) if ok else effect
        if not ok:
            delta = f"动作失败: {effect}"
        changed = (new_obs["state"] != obs.get("state")) or (new_obs["keys"] != last_keys)
        stale = 0 if (changed and ok) else stale + 1
        history.append({
            "step": step,
            "action": kind,
            "detail": {k: v for k, v in decision.items() if k not in ("thought", "action")},
            "result": delta,
            "changed": changed,
        })
        if verbose:
            print(f"    -> {delta}")
        if stale >= 3:
            history.append({"step": step, "action": "hint",
                            "result": "连续 3 步界面无变化，请换一种做法（换入口/先点输入框/先滚动）"})
            stale = 0
        obs = new_obs
        last_keys = new_obs["keys"]

    return {"ok": False, "error": f"{max_steps} 步内没完成", "steps": history, "model": client.model}


# ------------------------------------------------------------------ 具体任务

def _page_state(hwnd: int, rect: Tuple[int, int, int, int]) -> str:
    items = [it for it in drv.read_window(hwnd) if not _is_noise_item(it, rect)]
    return state_of(items)


def _find_text_item(hwnd: int, rect: Tuple[int, int, int, int], needle: str) -> Optional[Dict[str, Any]]:
    items = [it for it in drv.read_window(hwnd) if not _is_noise_item(it, rect)]
    return _pick(items, needle)


def add_friend_agent(
    keyword: str,
    *,
    hello: str = "",
    remark: str = "",
    submit: bool = True,
    model: str = "",
    max_steps: int = 8,
    verbose: bool = True,
) -> Dict[str, Any]:
    """加好友：先走脚本段（已验证），失败再让模型看着界面纠错。"""
    res = act.add_friend(keyword, hello=hello, remark=remark, submit=submit)
    if verbose:
        print("script:", json.dumps({k: v for k, v in res.items() if k != "notes"}, ensure_ascii=False))
    if res.get("ok") or res.get("status") in ("not_found",):
        return res

    goal = (
        f"在微信里添加好友：微信号/手机号 {keyword}。"
        "当前可能已经打开了某个微信子窗口（添加朋友/资料卡/申请页），先看状态再动手："
        "如果没搜过，就在搜索框输入并点搜索；如果出现结果卡片，点『添加到通讯录』；"
        "如果到了申请页，把打招呼语改成指定内容后点『确定』。"
        + (f"打招呼语：{hello}。" if hello else "")
        + ("提交后页面关闭才算成功，done(status=submitted)。" if submit else "走到申请页就 done(status=pending_submit)。")
    )
    rec = run_goal(goal, model=model, max_steps=max_steps, verbose=verbose)
    rec["fallback_from"] = res.get("status") or res.get("error")
    return rec


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="LLM 驱动的微信操作代理")
    ap.add_argument("--goal", help="自由目标（自然语言）")
    ap.add_argument("--keyword", help="加好友：微信号/手机号")
    ap.add_argument("--hello", default="")
    ap.add_argument("--remark", default="")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--model", default="")
    ap.add_argument("--steps", type=int, default=16)
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.keyword:
        res = add_friend_agent(args.keyword, hello=args.hello, remark=args.remark,
                               submit=args.submit, model=args.model, max_steps=args.steps)
    elif args.goal:
        res = run_goal(args.goal, model=args.model, max_steps=args.steps)
    else:
        ap.error("需要 --keyword 或 --goal")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())