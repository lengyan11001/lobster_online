"""OCR 驱动：UIA 控件树读不到时，用截图 + OCR 驱动微信界面。

2026-11-27 背景：微信 4.2 的无障碍树会整体失效（重启也不恢复），
所有"按控件名找元素"的路径都会失败；这里改成按像素找文字。

对齐参照实现（xiaoli `visual_backend.py` / `visual_vision.py`）：
- 引擎：优先 rapidocr 3.x + PP-OCRv5 mobile（1x 原生 OCR，Det.limit_side_len=224，
  onnxruntime 限 2 线程）；环境里只有 rapidocr_onnxruntime 时自动降级到 2.x 接口
- 文本清洗：`norm_cjk` 去掉 CJK 之间被 OCR 插入的空格
- 去重：内容键 `昵称|正文`（同 `_extract_comments` 的 key 口径）+ dhash 帧指纹兜底
- 增量：滚动后只 OCR 底部条带，条带顶部必须与上一帧重叠行对得上；对不上整区重读
- 停轮：连续两轮 0 新增，或帧指纹连续两轮不变（到底了）

公开 API：
    ocr_image / read_window / find_text / click_text     基础读屏
    wheel_at / is_foreground                             滚动（带前台校验）
    region_fingerprint                                   帧指纹（dhash 8x8）
    cluster_rows / parse_row                             行聚类与字段解析
    collect_comments(hwnd)                               评论区滚动采集
命令行自测（真机）：
    python -m backend.app.services.wechat_ocr_driver probe --dump
    python -m backend.app.services.wechat_ocr_driver collect --max 60
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import win32api
import win32gui
from PIL import Image, ImageGrab

# ---------------------------------------------------------------- 引擎

_ENGINE: Any = None
_ENGINE_KIND = ""


def _engine() -> Any:
    """懒加载 OCR 引擎。rapidocr 3.x 优先，缺失时退回 rapidocr_onnxruntime。"""
    global _ENGINE, _ENGINE_KIND
    if _ENGINE is not None:
        return _ENGINE
    try:
        from rapidocr import RapidOCR  # type: ignore
        from rapidocr.utils.typings import ModelType, OCRVersion  # type: ignore

        _ENGINE = RapidOCR(
            params={
                "Det.ocr_version": OCRVersion.PPOCRV5,
                "Det.model_type": ModelType.MOBILE,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,
                "Det.limit_side_len": 224,
                "Det.limit_type": "min",
                "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            }
        )
        _ENGINE_KIND = "rapidocr3"
        return _ENGINE
    except Exception:
        pass
    from rapidocr_onnxruntime import RapidOCR  # type: ignore

    _ENGINE = RapidOCR()
    _ENGINE_KIND = "rapidocr2"
    return _ENGINE


def engine_kind() -> str:
    _engine()
    return _ENGINE_KIND


def _raw_ocr(img: Image.Image) -> List[Tuple[Any, str, float]]:
    """→ [(box, text, score)]，屏蔽 2.x / 3.x 返回结构差异。"""
    import numpy as np

    eng = _engine()
    arr = np.array(img.convert("RGB"))
    if _ENGINE_KIND == "rapidocr3":
        out = eng(arr)
        txts = list(getattr(out, "txts", None) or [])
        scores = list(getattr(out, "scores", None) or [])
        boxes = list(getattr(out, "boxes", None) or [])
        return [(b, str(t), float(s)) for b, t, s in zip(boxes, txts, scores)]
    result, _elapse = eng(arr)
    out: List[Tuple[Any, str, float]] = []
    for row in result or []:
        out.append((row[0], str(row[1]), float(row[2])))
    return out


# ---------------------------------------------------------------- 文本清洗

_SPACE_CJK = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")
_SPACE_CJK_PUNCT = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[，。！？：；、（）「」『』])")
_SPACE_PUNCT_CJK = re.compile(r"(?<=[，。！？：；、（）「」『』])\s+(?=[\u4e00-\u9fff])")


def norm_cjk(text: str) -> str:
    """中文单字间被 OCR 插入的空格去掉；英文/数字内部空格保留。"""
    if not text:
        return text or ""
    out = _SPACE_CJK.sub("", text)
    out = _SPACE_CJK_PUNCT.sub("", out)
    out = _SPACE_PUNCT_CJK.sub("", out)
    return out.strip()


def is_noise(text: str) -> bool:
    """空文本或纯标点/符号（无 CJK 无字母无数字）。单字消息是合法的。"""
    if not text:
        return True
    return not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", text)


# ---------------------------------------------------------------- 截图 / OCR

def _dwm_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """可见窗口边界（不含 Win10 之后 GetWindowRect 多出来的不可见边框）。"""
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        dwm = ctypes.windll.dwmapi
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        hr = dwm.DwmGetWindowAttribute(
            wintypes.HWND(int(hwnd)), ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect), ctypes.sizeof(rect),
        )
        if hr == 0 and rect.right > rect.left and rect.bottom > rect.top:
            return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    except Exception:
        pass
    return None


def window_rect(hwnd: int) -> Tuple[int, int, int, int]:
    return _dwm_rect(hwnd) or tuple(int(v) for v in win32gui.GetWindowRect(int(hwnd)))  # type: ignore[return-value]


def shot(hwnd: int, *, region: Optional[Tuple[int, int, int, int]] = None) -> Image.Image:
    left, top, right, bottom = window_rect(hwnd)
    box = tuple(int(v) for v in (region or (left, top, right, bottom)))
    return ImageGrab.grab(bbox=box, all_screens=True)


def ocr_image(img: Any, *, max_w: int = 0, min_score: float = 0.0) -> List[Dict[str, Any]]:
    """图像 → [{text, score, left, top, right, bottom, cx, cy}]（图像像素坐标）。"""
    if max_w > 0 and img.width > max_w:
        ratio = max_w / img.width
        img = img.resize((max_w, max(1, int(img.height * ratio))), Image.LANCZOS)
    out: List[Dict[str, Any]] = []
    for box, text, score in _raw_ocr(img):
        text = norm_cjk(text)
        if is_noise(text) or score < min_score:
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        l, t, r, b = min(xs), min(ys), max(xs), max(ys)
        out.append(
            {
                "text": text,
                "score": round(float(score), 4),
                "left": int(l), "top": int(t), "right": int(r), "bottom": int(b),
                "x": int(l), "y": int(t), "w": int(r - l), "h": int(b - t),
                "cx": int(sum(xs) / len(xs)), "cy": int(sum(ys) / len(ys)),
            }
        )
    out.sort(key=lambda it: (it["top"], it["left"]))
    return out


def read_window(
    hwnd: int,
    *,
    region: Optional[Tuple[int, int, int, int]] = None,
    max_w: int = 0,
) -> List[Dict[str, Any]]:
    """截 region（屏幕坐标）→ OCR → 坐标换算回屏幕坐标。"""
    left, top, right, bottom = window_rect(hwnd)
    box = tuple(int(v) for v in (region or (left, top, right, bottom)))
    items = ocr_image(shot(hwnd, region=box), max_w=max_w)
    dx, dy = box[0], box[1]
    for it in items:
        for k in ("left", "right", "cx", "x"):
            it[k] += dx
        for k in ("top", "bottom", "cy", "y"):
            it[k] += dy
    return items


def _grab(hwnd: int, region: Optional[Tuple[int, int, int, int]]) -> Tuple[Image.Image, Tuple[int, int, int, int]]:
    left, top, right, bottom = window_rect(hwnd)
    box = tuple(int(v) for v in (region or (left, top, right, bottom)))
    return ImageGrab.grab(bbox=box, all_screens=True), box


def read_region_fast(
    hwnd: int,
    region: Tuple[int, int, int, int],
    *,
    min_score: float = 0.0,
) -> Tuple[List[Dict[str, Any]], Image.Image]:
    """一次截图同时给 OCR 行和原图（帧指纹复用同一张图，省一次抓屏）。"""
    img, box = _grab(hwnd, region)
    items = ocr_image(img, min_score=min_score)
    dx, dy = box[0], box[1]
    for it in items:
        for k in ("left", "right", "cx", "x"):
            it[k] += dx
        for k in ("top", "bottom", "cy", "y"):
            it[k] += dy
    return items, img


# ---------------------------------------------------------------- 基础动作

def find_text(
    items: Sequence[Dict[str, Any]],
    needle: str,
    *,
    min_score: float = 0.5,
    exact: bool = False,
    nth: int = 0,
) -> Optional[Dict[str, Any]]:
    hits = [
        it for it in items
        if it["score"] >= min_score and ((it["text"] == needle) if exact else (needle in it["text"]))
    ]
    hits.sort(key=lambda it: (it["top"], it["left"]))
    return hits[nth] if len(hits) > nth else None


def click_point(x: int, y: int) -> None:
    import win32api
    import win32con

    win32api.SetCursorPos((int(x), int(y)))
    time.sleep(0.15)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def right_click_point(x: int, y: int) -> None:
    import win32api
    import win32con

    win32api.SetCursorPos((int(x), int(y)))
    time.sleep(0.15)
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)


def click_text(hwnd: int, needle: str, *, region=None, exact: bool = False, nth: int = 0) -> Optional[Dict[str, Any]]:
    items = read_window(hwnd, region=region)
    hit = find_text(items, needle, exact=exact, nth=nth)
    if hit:
        click_point(hit["cx"], hit["cy"])
    return hit


def _root_window(hwnd: int) -> int:
    h = int(hwnd)
    try:
        while True:
            owner = win32gui.GetWindow(h, 4)  # GW_OWNER
            parent = win32gui.GetParent(h)
            nxt = owner or parent
            if not nxt:
                return h
            h = int(nxt)
    except Exception:
        return h


def covered_by(hwnd: int, x: int, y: int) -> Optional[str]:
    """(x, y) 上盖着的窗口如果不是微信，返回它的描述。

    自动化必须先过这一关：桌面上有透明/置顶窗口时，SetCursorPos+mouse_event
    会把点击打到那层窗口上——对用户桌面来说就是"乱点"，宁可报错也不能盲点。
    """
    import ctypes
    from ctypes import wintypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    try:
        w = int(ctypes.windll.user32.WindowFromPoint(POINT(int(x), int(y))) or 0)
        if not w:
            return "none"
        root = int(win32gui.GetAncestor(w, 2) or w)
        if root == _root_window(hwnd):
            return None
        # 微信自己的弹出菜单/浮层是独立顶层窗（Qt51514QWindowToolSaveBits 等），
        # 同进程就算"没被遮挡"
        import win32process

        try:
            if win32process.GetWindowThreadProcessId(root)[1] == \
                    win32process.GetWindowThreadProcessId(int(hwnd))[1]:
                return None
        except Exception:
            pass
        return f"{win32gui.GetClassName(root)} {win32gui.GetWindowText(root)[:24]!r}"
    except Exception as e:
        return f"probe-error {e}"


def unset_topmost(hwnd: int) -> None:
    import win32con

    try:
        win32gui.SetWindowPos(int(hwnd), win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
    except Exception:
        pass


def click_plain(x: int, y: int) -> None:
    """不做遮挡校验的点击。

    用于微信自己的弹出菜单：菜单是临时顶层窗，一旦把主窗口提到前面
    （TOPMOST/SetForegroundWindow）菜单就会被系统关掉，点了等于没点。
    调用方必须保证此前的窗口层级已经摆好。
    """
    click_point(x, y)


def make_topmost(hwnd: int) -> None:
    """把微信整体提到最前（会压过 Chrome 等普通窗口），只调一次。"""
    import win32con

    try:
        win32gui.SetWindowPos(int(hwnd), win32con.HWND_TOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
        time.sleep(0.3)
    except Exception:
        pass


def click_checked(hwnd: int, x: int, y: int) -> None:
    """带遮挡校验的点击：被别的窗口盖住先临时置顶，仍不行就报错，绝不盲点。"""
    import win32con

    who = covered_by(hwnd, x, y)
    if who:
        try:
            win32gui.SetWindowPos(int(hwnd), win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
            time.sleep(0.35)
        except Exception:
            pass
        who = covered_by(hwnd, x, y)
        if who:
            raise RuntimeError(f"目标位置被其他窗口遮挡（{who}），已停止点击；请把微信切到最前")
    click_point(x, y)


def is_foreground(hwnd: int) -> bool:
    """前台是不是这个窗口（或其顶层宿主窗口 / 子窗口）。"""
    try:
        fg = int(win32gui.GetForegroundWindow() or 0)
        if not fg:
            return False
        target = _root_window(hwnd)
        if fg == int(hwnd) or fg == target:
            return True
        return _root_window(fg) == target
    except Exception:
        return False


def ensure_visible(hwnd: int) -> bool:
    """窗口被收进托盘/最小化时拉回来（微信 Esc 会直接隐藏主窗口）。"""
    import win32con

    h = int(hwnd)
    try:
        if not win32gui.IsWindow(h):
            return False
        if not win32gui.IsWindowVisible(h) or win32gui.IsIconic(h):
            win32gui.ShowWindow(h, win32con.SW_RESTORE)
            time.sleep(0.35)
            if not win32gui.IsWindowVisible(h):
                win32gui.ShowWindow(h, win32con.SW_SHOW)
                time.sleep(0.35)
        return bool(win32gui.IsWindowVisible(h))
    except Exception:
        return False


def bring_to_front(hwnd: int) -> bool:
    """把窗口切到前台（滚动前必须，否则滚轮落在别的窗口上）。

    后台进程直接 SetForegroundWindow 常被前台锁拦下，这里用
    最小化→还原 + SwitchToThisWindow 解锁，再补一次 SetForegroundWindow。
    """
    import ctypes
    import win32con

    h = int(hwnd)
    ensure_visible(h)
    try:
        root = _root_window(h)
        if win32gui.GetForegroundWindow() != root:
            # 不做最小化→还原：4.2 的内嵌视频号 webview 会因此变成空白页
            if win32gui.IsIconic(root):
                win32gui.ShowWindow(root, win32con.SW_RESTORE)
                time.sleep(0.35)
            try:
                ctypes.windll.user32.SwitchToThisWindow(ctypes.c_void_p(root), True)
            except Exception:
                pass
            time.sleep(0.3)
            try:
                win32gui.SetForegroundWindow(root)
            except Exception:
                pass
            time.sleep(0.3)
    except Exception:
        pass
    return is_foreground(h)


def wheel_at(x: int, y: int, notches: int = 6, *, hwnd: Optional[int] = None, interval: float = 0.03) -> None:
    """在 (x, y) 上滚轮。传 hwnd 时先校验前台，避免滚错窗口。"""
    import win32api
    import win32con

    if hwnd is not None and not is_foreground(hwnd):
        raise RuntimeError("前台不是微信窗口，已停止滚动")
    if hwnd is not None:
        who = covered_by(hwnd, x, y)
        if who:
            raise RuntimeError(f"滚动位置被其他窗口遮挡（{who}），已停止")
    win32api.SetCursorPos((int(x), int(y)))
    time.sleep(0.08)
    delta = -120 if notches > 0 else 120  # 正数=向下滚
    for _ in range(max(1, abs(int(notches)))):
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
        time.sleep(interval)


# ---------------------------------------------------------------- 帧指纹 / 去重

def region_fingerprint(img: Image.Image, box: Optional[Tuple[int, int, int, int]] = None) -> Optional[int]:
    """dhash 8x8（9x8 灰度，比较水平相邻像素）→ 63bit 指纹。

    跨帧判重的兜底：块几何随滚动整体位移，只有像素内容稳定。
    """
    try:
        if box:
            img = img.crop(tuple(int(v) for v in box))
        import numpy as np

        crop = np.asarray(img.convert("L").resize((9, 8), Image.BILINEAR), dtype=np.int16)
        bits = 0
        for y in range(8):
            for x in range(8):
                bits = (bits << 1) | (1 if crop[y, x] > crop[y, x + 1] else 0)
        return bits
    except Exception:
        return None


def hamming(a: Optional[int], b: Optional[int]) -> int:
    if a is None or b is None:
        return 64
    return bin(int(a) ^ int(b)).count("1")


# ---------------------------------------------------------------- 行聚类 / 字段解析

_TIME_RE = re.compile(r"(刚刚|\d+\s*秒前|\d+\s*分钟前|\d+\s*小时前|\d+\s*天前|昨天|前天|\d{1,2}月\d{1,2}日|\d{4}年\d{1,2}月\d{1,2}日|\d{4}年)")
_REPLY_RE = re.compile(r"^(\d+)\s*条回复$")
_NUM_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(万|w|W)?$")
_CJK_SHORT = re.compile(r"^[\u4e00-\u9fa5]{2,8}$")


def panel_left_edge(img: Image.Image) -> int:
    """评论区面板左边界（图像局部 x）。截图裁得比面板宽时（含视频画面/
    侧栏黑边），头像带必须相对面板左沿而不是图像左沿。"""
    import numpy as np

    arr = np.asarray(img.convert("RGB"), dtype=np.int16)
    rh, rw = arr.shape[:2]
    white = (np.abs(arr - 255).sum(axis=2) < 24).mean(axis=0)
    run = 0
    for x in range(rw):
        if white[x] > 0.9:
            run += 1
            if run >= 6:
                return x - 5
        else:
            run = 0
    return 0


def avatar_tops(
    img: Image.Image,
    *,
    band: Optional[Tuple[int, int]] = None,
    min_h: int = 16,
    max_h: int = 60,
    min_peak: int = 10,
    seg_gap: int = 6,
) -> List[int]:
    """评论区左侧头像的顶部 y 列表（图像局部坐标）。

    评论区每条评论（含缩进回复）左侧都有一个头像，是最稳的行锚点：纯文本行
    聚类会被昵称/正文/时间不同字号的行距切碎，头像不会。判据：面板左沿往右
    14~50px 的窄带内逐行统计"显著非白"像素数，连续成段、高度落在头像尺寸
    区间、峰值足够即为头像；贴上下缘的段视为被滚动裁断，丢弃。
    """
    import numpy as np

    arr = np.asarray(img.convert("RGB"), dtype=np.int16)
    rh, rw = arr.shape[:2]
    if rw < 60 or rh < 60:
        return []
    edge = panel_left_edge(img)
    x0, x1 = band if band else (edge + 14, edge + 50)
    x1 = min(x1, rw - 1)
    if x1 - x0 < 10:
        return []
    bg = np.median(arr[:, x1 - 3:x1, :].reshape(-1, 3), axis=0)
    dist = np.abs(arr[:, x0:x1, :] - bg.astype(np.int16)).sum(axis=2)
    # 阈值 28：白底/浅色头像与面板白的差值只有 ~30-60，70 会整列漏检
    counts = (dist > 28).sum(axis=1)

    tops: List[int] = []
    start = -1
    last = -1
    gap = 0
    for y in range(rh):
        if int(counts[y]) >= 2:
            if start < 0:
                start = y
            last = y
            gap = 0
        elif start >= 0:
            gap += 1
            if gap > seg_gap:
                h = last + 1 - start
                if start > 1 and last < rh - 2 and min_h <= h <= max_h \
                        and int(counts[start:last + 1].max()) >= min_peak:
                    tops.append(start)
                start = -1
    if start >= 0:
        h = last + 1 - start
        if start > 1 and last < rh - 2 and min_h <= h <= max_h \
                and int(counts[start:last + 1].max()) >= min_peak:
            tops.append(start)

    merged: List[int] = []
    for t in tops:
        if not merged or t - merged[-1] >= 14:
            merged.append(t)
    return merged


def _meta_filtered(lines: Sequence[Dict[str, Any]], region: Tuple[int, int, int, int]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """把昵称/正文之外的行（时间、地区、作者、点赞数、回复按钮）拆出去。"""
    rw = max(1, region[2] - region[0])
    meta: Dict[str, Any] = {"like": "", "time": "", "region": "", "reply_count": "", "is_author": False}
    body: List[Dict[str, Any]] = []
    nums = [
        it["right"] for it in lines
        if _NUM_RE.match(it["text"].replace(" ", "")) and it["right"] >= region[0] + rw * 0.8
    ]
    like_left = (min(nums) - 8) if nums else int(region[2] - 62)
    for it in lines:
        text = it["text"].strip()
        if not text:
            continue
        if text in ("回复", "展开", "收起", "点赞", "分享", "收藏", "置顶", "凸", "已置顶") \
                or ("发表评论" in text and len(text) <= 16):
            continue
        if "作者" in text:
            meta["is_author"] = True
            text = text.replace("作者", "").strip(" ·|/")
            if not text:
                continue
        m = re.search(r"(\d+)\s*条回复", text)
        if m and len(text) <= 12:
            meta["reply_count"] = m.group(1)
            continue
        mt = _TIME_RE.search(text)
        if mt and len(text) <= 24:
            rest = text.replace(mt.group(0), "").strip(" ·|/")
            if _CJK_SHORT.match(rest or ""):
                meta["region"] = meta["region"] or rest
            meta["time"] = meta["time"] or mt.group(0).replace(" ", "")
            continue
        if _NUM_RE.match(text.replace(" ", "")) and it["right"] >= like_left:
            meta["like"] = meta["like"] or text.replace(" ", "")
            continue
        body.append(it)
    return body, meta


def cluster_rows(items: Sequence[Dict[str, Any]], *, gap_ratio: float = 0.62) -> List[List[Dict[str, Any]]]:
    """按竖直间距把 OCR 行聚成评论行组。

    同一行内的多行正文行距 ≈ 0.35 倍行高，行组之间 ≈ 1.0 倍以上；
    阈值取 0.62 倍行高（中位数）即可稳定分开，且不依赖气泡/头像检测。
    """
    rows: List[List[Dict[str, Any]]] = []
    if not items:
        return rows
    lines = sorted(items, key=lambda it: (it["top"], it["left"]))
    hs = sorted(it["h"] for it in lines)
    med_h = hs[len(hs) // 2] or 16
    gap = max(4.0, float(med_h) * gap_ratio)
    cur = [lines[0]]
    for it in lines[1:]:
        if it["top"] - max(x["bottom"] for x in cur) <= gap:
            cur.append(it)
        else:
            rows.append(cur)
            cur = [it]
    rows.append(cur)
    return rows


def _split_head(text: str) -> Tuple[str, Dict[str, Any]]:
    """块首行（昵称行）→ (昵称, meta)。行内常并着 作者/地区/时间。"""
    meta: Dict[str, Any] = {"like": "", "time": "", "region": "", "reply_count": "", "is_author": False}
    text = (text or "").strip()
    if "作者" in text:
        meta["is_author"] = True
        text = text.replace("作者", " ")
    mt = _TIME_RE.search(text)
    if mt:
        meta["time"] = mt.group(0).replace(" ", "")
        text = text.replace(mt.group(0), " ")
    tokens = [t for t in text.replace("\u3000", " ").split() if t]
    while len(tokens) > 1 and _CJK_SHORT.match(tokens[-1]):
        meta["region"] = tokens.pop()
    nickname = norm_cjk(" ".join(tokens)).strip(" ·|/：:")
    return nickname, meta


def parse_block(
    lines: Sequence[Dict[str, Any]],
    *,
    avatar_top: int,
    region: Tuple[int, int, int, int],
) -> Optional[Dict[str, Any]]:
    """头像块 → 一条评论。块首行（贴着头像顶）是昵称行，其余为正文。"""
    if not lines:
        return None
    ordered = sorted(lines, key=lambda it: (it["top"], it["left"]))
    head: Optional[Dict[str, Any]] = None
    for it in ordered:
        if it["top"] <= avatar_top + 26:
            head = it
            break
    nickname = ""
    meta = {"like": "", "time": "", "region": "", "reply_count": "", "is_author": False}
    rest = list(ordered)
    if head is not None:
        rest.remove(head)
        nickname, head_meta = _split_head(head["text"])
        meta.update({k: v for k, v in head_meta.items() if v})
    # 点赞数：与"回复"按钮同一行、位于左列的数字行（微信评论区布局），
    # 整行只有一个数字且在"回复"行上下 10px 内即判点赞
    reply_ys = [it["cy"] for it in rest if it["text"].strip() == "回复"]
    like_line: Optional[Dict[str, Any]] = None
    kept: List[Dict[str, Any]] = []
    for it in rest:
        text = it["text"].strip()
        if _NUM_RE.match(text.replace(" ", "")) and any(abs(it["cy"] - ry) <= 12 for ry in reply_ys):
            like_line = like_line or it
            continue
        kept.append(it)
    body, body_meta = _meta_filtered(kept, region)
    if like_line is not None and not body_meta.get("like"):
        body_meta["like"] = like_line["text"].replace(" ", "")
    for k, v in body_meta.items():
        if v and not meta.get(k):
            meta[k] = v
    content = norm_cjk(" ".join(ln["text"] for ln in body))
    content = re.sub(r"\s+", " ", content).strip()
    # 点赞图标被 OCR 读成"凸"并与数字连在正文尾（'…过分了凸1'）
    m2 = re.search(r"凸\s*(\d{1,6})\s*$", content)
    if m2:
        content = content[: m2.start()].strip()
        meta["like"] = meta["like"] or m2.group(1)
    content = re.sub(r"\s*\d{1,6}\s*回复\s*$", "", content).strip()
    content = re.sub(r"^[凸\(\[]\s*|\s*[凸\)\]]$", "", content).strip()
    like = meta["like"]
    if content and not like and body:
        m = re.search(r"^(.*[\u4e00-\u9fffA-Za-z，。！？、）】])\s*(\d{1,6})$", content)
        if m and body[-1]["right"] >= region[2] - 78:
            content, like = m.group(1).strip(), m.group(2)
    if not content and not nickname:
        return None
    if not nickname and _NUM_RE.match(content.replace(" ", "")) and len(content) <= 8:
        return None
    if not content:
        content = "[图片/表情]"
    return {
        "nickname": nickname,
        "content": content,
        "like": like,
        "region": meta["region"],
        "time": meta["time"],
        "reply_count": meta["reply_count"],
        "is_author": meta["is_author"],
        "y": int(avatar_top),
        "h": int(max(ln["bottom"] for ln in ordered) - avatar_top),
    }


def parse_row(
    lines: Sequence[Dict[str, Any]],
    *,
    region: Tuple[int, int, int, int],
    like_left: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """行组 → {nickname, content, like, region, time, reply_count, is_author}。"""
    if not lines:
        return None
    rw = max(1, region[2] - region[0])
    ordered = sorted(lines, key=lambda it: (it["top"], it["left"]))
    body, meta = _meta_filtered(ordered, region)
    if not body:
        return None
    nickname = ""
    content_lines = body
    first = body[0]["text"].strip()
    if len(body) >= 2 and len(first) <= 20 and not _TIME_RE.search(first):
        nickname = first
        content_lines = body[1:]
    elif "：" in first and len(first.split("：", 1)[0]) <= 12 and len(body) == 1:
        nickname, rest = first.split("：", 1)
        content_lines = [dict(body[0], text=rest)] if rest.strip() else []
    content = norm_cjk(" ".join(ln["text"] for ln in content_lines))
    content = re.sub(r"\s+", " ", content).strip()
    like = meta["like"]
    # 点赞数贴着右缘时会被 OCR 并进正文尾（'...收藏一下128'）：按点赞列左界拆出
    if content and not like and like_left is not None and content_lines:
        tail = content_lines[-1]
        m = re.search(r"^(.*[\u4e00-\u9fffA-Za-z，。！？、）】])\s*(\d{1,6})$", content)
        if m and tail["right"] >= like_left - 16:
            content, like = m.group(1).strip(), m.group(2)
    if not content and not nickname:
        return None
    if not content:
        content = "[图片/表情]"
    return {
        "nickname": nickname,
        "content": content,
        "like": like,
        "region": meta["region"],
        "time": meta["time"],
        "reply_count": meta["reply_count"],
        "is_author": meta["is_author"],
        "y": int(min(ln["top"] for ln in lines)),
        "h": int(max(ln["bottom"] for ln in lines) - min(ln["top"] for ln in lines)),
    }


def extract_rows(
    items: Sequence[Dict[str, Any]],
    *,
    region: Tuple[int, int, int, int],
    img: Optional[Image.Image] = None,
    band: Optional[Tuple[int, int]] = None,
) -> List[Dict[str, Any]]:
    """OCR 行 → 评论行。

    主通道：头像锚定（img 提供时）——每条评论/回复一个左侧头像，块 =
    [本头像 top, 下一头像 top)，块内行归属不会串行；首个头像之上的内容
    （面板头"评论 78"、搜索框）与末条之后的输入框全部丢弃。
    兜底：竖直间距聚类（无 img 或无头像时）。
    """
    # 底部输入框占位（"XX 发表评论"）不属于评论区内容；
    # 头像所在窄带以左是视频页（+关注/点赞数），不是评论区文字
    items = [
        it for it in items
        if "发表评论" not in it["text"] and it["left"] >= region[0] + 40
    ]
    rows: List[Dict[str, Any]] = []
    tops: List[int] = []
    if img is not None:
        tops = [region[1] + t for t in avatar_tops(img, band=band)]
    if len(tops) >= 2:
        for i, top in enumerate(tops):
            bottom = tops[i + 1] if i + 1 < len(tops) else region[3]
            group = [it for it in items if top - 2 <= (it["cy"]) < bottom - 2]
            row = parse_block(group, avatar_top=top, region=region)
            if row:
                rows.append(row)
    else:
        for group in cluster_rows(items):
            row = parse_row(group, region=region)
            if row:
                rows.append(row)
    rows.sort(key=lambda r: r["y"])
    for row in rows:
        row["key"] = f"{row['nickname']}|{row['content']}"
    return rows


def dedupe_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同键去重，保留首次出现顺序（滚动采集的合并口径）。"""
    seen: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = row.get("key") or f"{row.get('nickname','')}|{row.get('content','')}"
        if key not in seen:
            seen[key] = row
    return list(seen.values())


# ---------------------------------------------------------------- 评论区采集

def comment_total(hwnd: int, region: Optional[Tuple[int, int, int, int]] = None) -> Optional[int]:
    """评论区面板头部的总条数（"评论 826"）。读不到返回 None。"""
    l, t, r, b = region or default_comment_region(hwnd)
    head = (int(l), int(t), int(r), int(t) + 46)
    for it in read_window(hwnd, region=head):
        m = re.search(r"(\d{1,6})", it["text"])
        if m and ("评论" in it["text"]):
            return int(m.group(1))
    return None


def default_comment_region(hwnd: int) -> Tuple[int, int, int, int]:
    """视频号评论区默认区域：优先按像素找面板白底左沿，找不到退回 62% 窗口宽。"""
    l, t, r, b = window_rect(hwnd)
    w, h = r - l, b - t
    left = int(l + w * 0.62)
    try:
        import numpy as np

        img = ImageGrab.grab(bbox=(l, t, r, b), all_screens=True).convert("RGB")
        arr = np.asarray(img, dtype=np.int16)
        white = (np.abs(arr - 255).sum(axis=2) < 24).mean(axis=0)
        run = 0
        for x in range(int(w * 0.45), w):
            if white[x] > 0.9:
                run += 1
                if run >= 10:
                    left = l + x - 9
                    break
            else:
                run = 0
    except Exception:
        pass
    return (int(left), int(t + h * 0.06), int(r - 2), int(b - h * 0.02))


def _bottom_strip(region: Tuple[int, int, int, int], ratio: float) -> Tuple[int, int, int, int]:
    l, t, r, b = region
    ratio = min(0.9, max(0.25, float(ratio)))
    return (l, int(b - (b - t) * ratio), r, b)


def _add_rows(
    target: List[Dict[str, Any]],
    seen: set,
    rows: Sequence[Dict[str, Any]],
    *,
    region: Tuple[int, int, int, int],
    top_pad: int = 6,
    skip_top_partial: bool = True,
    content_seen: Optional[set] = None,
) -> Tuple[int, int]:
    """合并一帧的行组 → (added, overlap)。

    滚动裁切边界产生的残行直接丢，避免同一条评论两个键（真机：条带顶部半行
    会把 '回复@阿星：…' 读成昵称）。去重两级：`昵称|正文` 全键（与 UIA
    `_extract_comments` 同口径）；正文 >= 8 字时再按正文去重——边界处行组
    切法变化会让昵称漂移，只靠全键会重复入列。
    overlap = 本帧落在已知集合里的行数：为 0 说明条带没和上一帧对齐
    （滚过量/条带全在空白区），调用方据此决定是否整区补读。
    """
    added = 0
    overlap = 0
    for row in rows:
        if skip_top_partial and row["y"] <= region[1] + top_pad:
            continue
        if row["y"] + row["h"] >= region[3] - 2:
            continue
        key = row["key"]
        content = row.get("content") or ""
        known = key in seen or (
            content_seen is not None and len(content) >= 8 and content in content_seen
        )
        if known:
            overlap += 1
            continue
        seen.add(key)
        if content_seen is not None and len(content) >= 8:
            content_seen.add(content)
        target.append(row)
        added += 1
    return added, overlap


_FEED_MARKS = ("推荐", "朋友", "直播", "关注", "搜索")
_PANEL_MARKS = ("发表评论", "回复")


def _texts(items: Sequence[Dict[str, Any]]) -> List[str]:
    return [it["text"] for it in items]


def _panel_open(items: Sequence[Dict[str, Any]]) -> bool:
    texts = _texts(items)
    if any("发表评论" in t for t in texts):
        return True
    # 面板头 OCR 常带前缀（"×评论1"、"评论 826"）
    return any(re.search(r"评论\s*\d", t) for t in texts)


def _pane_blank(hwnd: int, *, secs: int = 3) -> bool:
    """右侧内容区是否空白（4.2 内嵌 webview 挂掉时的典型状态）。"""
    l, t, r, b = window_rect(hwnd)
    box = (int(l + (r - l) * 0.30), int(t + (b - t) * 0.10), int(r - 4), int(b - 4))
    items = read_window(hwnd, region=box)
    return len(items) < 2


def _feed_open(items: Sequence[Dict[str, Any]]) -> bool:
    texts = _texts(items)
    return sum(1 for t in texts if t in _FEED_MARKS) >= 2


def _hover_probe_flyout(hwnd: int, rel_y: int) -> List[Dict[str, Any]]:
    """鼠标悬停在左侧栏图标上（不点击，点击会 toggle 掉已开的浮层），
    只 OCR 图标右侧的浮层窄条。"""
    import win32api

    l, t, r, b = window_rect(hwnd)
    win32api.SetCursorPos((int(l + 27), int(t + rel_y)))
    time.sleep(0.55)
    box = (int(l), int(t + max(0, rel_y - 30)), int(min(r, l + 235)), int(min(b, t + rel_y + 300)))
    return read_window(hwnd, region=box)


_FLYOUT_MARKS = ("朋友圈", "搜一搜", "小程序", "游戏", "视频号")


def _flyout_visible(hwnd: int, rel_y: int) -> List[Dict[str, Any]]:
    """左侧栏浮层（发现 → 朋友圈/视频号/搜一搜/…）是否可见，返回该窄条的 OCR 行。"""
    l, t, r, b = window_rect(hwnd)
    box = (int(l), int(t + max(0, rel_y - 40)), int(min(r, l + 240)), int(min(b, t + rel_y + 320)))
    items = read_window(hwnd, region=box)
    # 图标常被 OCR 成前缀字符（"六搜一搜"、"α视频号"、"W朋友圈"）→ 用包含匹配
    hits = sum(1 for m in _FLYOUT_MARKS if any(m in it["text"] for it in items))
    return items if hits >= 2 else []


_DISCOVER_REL: Optional[int] = None   # 上次成功打开「发现」的侧栏 y（记住省一次全扫）


def _discover_candidates() -> List[int]:
    rels = list(range(150, 341, 35))
    if _DISCOVER_REL in rels:
        rels.remove(_DISCOVER_REL)
        rels.insert(0, _DISCOVER_REL)
    return rels


def _open_channels_feed(hwnd: int, *, notes: List[str], timeout: float = 20.0) -> bool:
    """发现 → 视频号。

    两种情况都要认：
    - 「发现」浮层展开（第一次用）：OCR 到 朋友圈/视频号… 再点「视频号」；
    - 「发现」记住上次的 视频号（常见）：点一下侧栏图标就直接进推荐流，没有浮层。
    """
    global _DISCOVER_REL
    l, t, r, b = window_rect(hwnd)
    for rel in _discover_candidates():
        probe = _flyout_visible(hwnd, rel)
        if not probe:
            click_checked(hwnd, l + 27, t + rel)
            time.sleep(0.75)
            items = read_window(hwnd)
            if _panel_open(items):
                _DISCOVER_REL = rel
                notes.append(f"click: 侧栏发现 @rel_y={rel} → 视频号评论区直接出现")
                return True
            if _feed_open(items):
                _DISCOVER_REL = rel
                notes.append(f"click: 侧栏发现 @rel_y={rel} → 推荐流已出现")
                return True
            probe = _flyout_visible(hwnd, rel)
        if not probe:
            continue
        hit = next((it for it in probe
                    if it["text"].rstrip().endswith("视频号") and "文案" not in it["text"]), None)
        if not hit:
            continue
        _DISCOVER_REL = rel
        notes.append(f"click: 侧栏发现 @rel_y={rel}")
        click_checked(hwnd, hit["cx"], hit["cy"])
        notes.append("click: 浮层里的「视频号」")
        deadline = time.time() + timeout
        blank = 0
        while time.time() < deadline:
            time.sleep(0.9)
            items = read_window(hwnd)
            if _panel_open(items):
                notes.append("panel: 视频号直接落在评论区")
                return True
            if _feed_open(items):
                notes.append("feed: 推荐流已出现")
                return True
            blank = blank + 1 if _pane_blank(hwnd) else 0
            if blank >= 2:
                notes.append("fail: 视频号内容区空白（4.2 内嵌 webview 挂了）——"
                             "重开微信后重试，不要再点，点了也不会渲染")
                return False
        return False
    notes.append("fail: 左侧栏没找到「视频号」（浮层和直达都没成）")
    return False


def open_comment_panel(hwnd: int, *, log: Optional[List[str]] = None, timeout: float = 20.0) -> Dict[str, Any]:
    """无人值守打开视频号评论区：发现 → 视频号 → 底部评论图标。

    全流程只用像素/OCR（4.2 的 UIA 树失效）：
    1. 面板已开（"发表评论"/"评论 N"）→ 直接返回；
    2. 推荐流已开 → 只差评论图标；
    3. 都不是 → 左侧栏悬停（不点击，点击会 toggle 浮层）找到「视频号」再点；
    4. 底部动作条四个图标无文字，从右往左逐个试，每点一次验一次面板标记，
       点到为止，不连击。
    """
    notes: List[str] = log if log is not None else []
    ensure_visible(hwnd)
    if not is_foreground(hwnd):
        bring_to_front(hwnd)
    l, t, r, b = window_rect(hwnd)
    items = read_window(hwnd)
    if _panel_open(items):
        notes.append("panel: 已打开")
        return {"ok": True, "opened": False, "notes": notes}

    if not _feed_open(items):
        if not _open_channels_feed(hwnd, notes=notes, timeout=timeout):
            return {"ok": False, "opened": False, "notes": notes}
        items = read_window(hwnd)
        if _panel_open(items):
            return {"ok": True, "opened": False, "notes": notes}

    for attempt in range(5):
        items = read_window(hwnd)
        if _panel_open(items):
            return {"ok": True, "opened": True, "notes": notes}
        cands = [
            it for it in items
            if it["top"] > t + (b - t) * 0.55 and it["left"] > l + (r - l) * 0.5
            and len(it["text"]) <= 8
        ]
        cands.sort(key=lambda it: it["left"])
        if not cands:
            time.sleep(0.9)
            continue
        target = cands[-1 - attempt] if len(cands) > attempt else cands[0]
        click_checked(hwnd, target["cx"], max(t + 30, target["top"] - 12))
        notes.append(f"click: 动作条候选 {target['text']!r}")
        time.sleep(1.5)
        if _panel_open(read_window(hwnd)):
            return {"ok": True, "opened": True, "notes": notes}
    notes.append("fail: 点了候选图标但评论区没出现")
    return {"ok": False, "opened": False, "notes": notes}


def collect_auto(
    hwnd: Optional[int] = None,
    *,
    max_comments: int = 100,
    max_scroll_rounds: int = 12,
    **kw: Any,
) -> Dict[str, Any]:
    """无人值守采集：自己打开视频号评论区，再滚动采集。"""
    h = int(hwnd or find_wechat_hwnd() or 0)
    if not h:
        raise RuntimeError("没找到微信窗口")
    log: List[str] = []
    try:
        panel = open_comment_panel(h, log=log)
        if not panel.get("ok"):
            return {"ok": False, "count": 0, "items": [], "notes": log, "panel": panel}
        res = collect_comments(h, max_comments=max_comments, max_scroll_rounds=max_scroll_rounds, **kw)
        res["ok"] = True
        res["panel"] = panel
        res["notes"] = log + list(res.get("notes") or [])
        return res
    finally:
        unset_topmost(h)   # 置顶只是点击兜底，跑完必须还原


def collect_comments(
    hwnd: int,
    *,
    region: Optional[Tuple[int, int, int, int]] = None,
    max_comments: int = 100,
    max_scroll_rounds: int = 8,
    notches: int = 6,
    settle: float = 1.2,
    incremental: bool = False,
    strip_ratio: float = 0.55,
    log: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """滚动采集评论区：整区 OCR 起手 → 每轮滚 notches → 增量 OCR + 文本去重。

    增量口径：滚动后只 OCR 底部 strip_ratio 条带，条带里必须至少有 1 行是
    已知内容（overlap > 0）才认这一帧；overlap == 0 说明滚过量或条带落在
    空白，立即整区补读（不滚），宁可多花一次 OCR 也不漏行。
    停轮：连续 2 轮 0 新增，或帧指纹连续 2 轮 hamming<=2（到底），或到条数上限。
    """
    ensure_visible(hwnd)
    if not is_foreground(hwnd):
        bring_to_front(hwnd)
    region = tuple(int(v) for v in (region or default_comment_region(hwnd)))  # type: ignore[assignment]
    t0 = time.perf_counter()
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    content_seen: set = set()
    notes: List[str] = log if log is not None else []
    frames: List[int] = []
    frame_no = 0
    scroll_rounds = 0
    prev_fp: Optional[int] = None
    same_fp = 0
    stale = 0
    lost = 0
    reached_bottom = False
    want_full = True

    while True:
        f0 = time.perf_counter()
        strip = want_full or not incremental or scroll_rounds == 0
        area = region if strip else _bottom_strip(region, strip_ratio)
        items, img = read_region_fast(hwnd, area)
        if frame_no and not _panel_open(items):
            # 面板被滚动/误点关掉了：这一帧是视频页，不能当评论收
            lost += 1
            notes.append(f"f{frame_no + 1}: 无面板头（面板被关），尝试重开")
            if lost > 2:
                reached_bottom = False
                break
            open_comment_panel(hwnd)
            time.sleep(0.8)
            continue
        lost = 0
        frame_rows = extract_rows(items, region=area, img=img)
        added, overlap = _add_rows(
            rows, seen, frame_rows,
            region=area,
            top_pad=6,
            skip_top_partial=bool(rows),
            content_seen=content_seen,
        )
        fp = region_fingerprint(img)
        diff = hamming(prev_fp, fp) if prev_fp is not None else 64
        if prev_fp is not None and diff <= 2:
            same_fp += 1
        else:
            same_fp = 0
        prev_fp = fp
        dt = int((time.perf_counter() - f0) * 1000)
        frames.append(dt)
        frame_no += 1
        notes.append(
            f"f{frame_no} {'full' if area is region else 'strip'} {dt}ms "
            f"lines={len(items)} rows={len(frame_rows)} new={added} overlap={overlap} fp={diff}"
        )

        # 条带没对齐（滚过量/空白）→ 不保留可疑帧顺序，立即整区补读
        if area is not region and overlap == 0:
            want_full = True
            notes.append(f"f{frame_no}: strip overlap=0，整区补读")
            continue
        want_full = False

        if len(rows) >= max_comments:
            break
        if same_fp >= 2:
            reached_bottom = True
            notes.append("帧指纹连续 2 帧不变 → 到底")
            break
        stale = stale + 1 if added == 0 else 0
        if stale >= 2:
            reached_bottom = True
            notes.append("连续 2 轮 0 新增 → 停止")
            break
        if scroll_rounds >= max_scroll_rounds:
            break

        cx = (region[0] + region[2]) // 2
        cy = region[1] + max(60, (region[3] - region[1]) // 3)
        wheel_at(cx, cy, notches, hwnd=hwnd)
        scroll_rounds += 1
        time.sleep(settle)

    out = rows[: max(1, int(max_comments))]
    for idx, row in enumerate(out):
        row["index"] = idx + 1
    return {
        "items": out,
        "count": len(out),
        "scroll_rounds": scroll_rounds,
        "frames": frame_no,
        "frame_ms": frames,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        "bottom": reached_bottom,
        "total": comment_total(hwnd, region),
        "region": list(region),
        "engine": engine_kind(),
        "notes": notes,
    }


# ---------------------------------------------------------------- 窗口定位 / CLI

_WECHAT_CLASSES = {
    "WeChatMainWndForPC",
    "Qt51514QWindowIcon",
    "Qt51512QWindowIcon",
    "Qt51515QWindowIcon",
    "Qt5QWindowIcon",
}


def _weixin_pids() -> set:
    try:
        import psutil

        return {
            p.pid for p in psutil.process_iter(["name"])
            if (p.info.get("name") or "").lower() in ("weixin.exe", "wechat.exe")
        }
    except Exception:
        return set()


_PAGE_CLASSES = ("Qt51514QWindowIcon",)


def _tool_window(cls: str) -> bool:
    return ("ToolSaveBits" in cls) or ("MessageWindow" in cls) or ("IME" in cls) or cls.startswith("#")


def wechat_pages(pid: Optional[int] = None) -> List[Dict[str, Any]]:
    """微信进程下的所有顶层页面窗（4.2 的子页面是独立窗口，不是主窗内嵌）。"""
    import win32process

    if pid is None:
        h = find_wechat_hwnd()
        pid = win32process.GetWindowThreadProcessId(int(h))[1] if h else 0
    out: List[Dict[str, Any]] = []

    def _cb(w, _):
        try:
            if not win32gui.IsWindowVisible(w) or win32gui.IsIconic(w):
                return True
            cls = win32gui.GetClassName(w)
            if cls not in _PAGE_CLASSES:
                return True
            if pid and win32process.GetWindowThreadProcessId(w)[1] != pid:
                return True
            l, t, r, b = win32gui.GetWindowRect(w)
            if r - l < 180 or b - t < 120:
                return True
            out.append({"hwnd": w, "title": win32gui.GetWindowText(w),
                        "rect": (l, t, r, b), "area": (r - l) * (b - t)})
        except Exception:
            pass
        return True

    win32gui.EnumWindows(_cb, None)
    return out


def activate_window(hwnd: int) -> bool:
    """强激活：AttachThreadInput 绕开前台锁（SetForegroundWindow 常被拒）。"""
    import win32process

    h = int(hwnd)
    try:
        if win32gui.GetForegroundWindow() == h:
            return True
        fg = win32gui.GetForegroundWindow()
        t_fg = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
        me = win32api.GetCurrentThreadId()
        if t_fg:
            win32process.AttachThreadInput(me, t_fg, True)
        try:
            win32gui.BringWindowToTop(h)
            win32gui.SetForegroundWindow(h)
        finally:
            if t_fg:
                win32process.AttachThreadInput(me, t_fg, False)
        time.sleep(0.3)
    except Exception:
        pass
    return win32gui.GetForegroundWindow() == h


def bring_page_front(hwnd: int) -> Tuple[int, int, int, int]:
    """子页面窗提到最前（点击后它常常开在主窗后面，看起来像"没反应"）。"""
    import ctypes
    import win32con

    w = int(hwnd)
    try:
        win32gui.ShowWindow(w, win32con.SW_SHOW)
        time.sleep(0.15)
        win32gui.ShowWindow(w, win32con.SW_RESTORE)
        time.sleep(0.15)
        try:
            ctypes.windll.user32.SwitchToThisWindow(ctypes.c_void_p(w), True)
        except Exception:
            pass
        activate_window(w)
        # 主窗若是 TOPMOST，子页面永远压不上去；子页面自己也置顶
        try:
            win32gui.SetWindowPos(w, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
        except Exception:
            pass
        time.sleep(0.35)
    except Exception:
        pass
    return window_rect(w)


def page_by_title(title_part: str) -> Optional[Dict[str, Any]]:
    for p in wechat_pages():
        if title_part and title_part in (p["title"] or ""):
            return p
    return None


def close_other_pages(main_hwnd: int) -> int:
    """关掉遗留的微信子页面窗（添加朋友/申请页 …）。

    这些窗会盖在主窗上，连左侧栏图标都点不到，表现为"点了没反应"。
    """
    closed = 0
    for p in wechat_pages():
        if (p["title"] or "") == "微信" or p["hwnd"] == int(main_hwnd):
            continue
        try:
            win32gui.PostMessage(p["hwnd"], 0x0010, 0, 0)   # WM_CLOSE
            closed += 1
        except Exception:
            pass
    if closed:
        time.sleep(0.6)
    return closed


def current_page(main_hwnd: int) -> Tuple[int, Tuple[int, int, int, int]]:
    """当前该操作哪一层窗：前台若是微信的页面窗就用它，否则用主窗。"""
    import win32process

    try:
        fg = int(win32gui.GetForegroundWindow() or 0)
        if fg and fg != int(main_hwnd):
            pid_main = win32process.GetWindowThreadProcessId(int(main_hwnd))[1]
            if win32process.GetWindowThreadProcessId(fg)[1] == pid_main \
                    and win32gui.GetClassName(fg) in _PAGE_CLASSES:
                return fg, window_rect(fg)
    except Exception:
        pass
    return int(main_hwnd), window_rect(int(main_hwnd))


def find_wechat_hwnd() -> int:
    """微信主窗口：按进程名（Weixin.exe）认，隐藏/最小化也返回，供 ensure_visible 拉回。"""
    import win32process

    pids = _weixin_pids()
    cands: List[Tuple[int, int]] = []

    def _cb(hwnd, _):
        try:
            # 最小化/收进托盘也要返回：调用方用 ensure_visible 拉回来
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pids and pid not in pids:
                return True
            cls = win32gui.GetClassName(hwnd)
            title = win32gui.GetWindowText(hwnd)
            if not (cls in _WECHAT_CLASSES or "微信" in (title or "") or "WeChat" in (title or "")):
                return True
            iconic = bool(win32gui.IsIconic(hwnd))
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            area = max(0, r - l) * max(0, b - t)
            if iconic:
                area = 10 ** 6          # 最小化时 rect 是 160x32 的占位，别按它筛掉
            if area <= 200 * 200:
                return True
            score = area
            if win32gui.IsIconic(hwnd):
                score -= 10 ** 6          # 最小化的排后面，但不排除
            if cls == "Qt51514QWindowIcon":
                score += 10 ** 8
            if title == "微信":
                score += 10 ** 9
            if win32gui.IsWindowVisible(hwnd):
                score += 10 ** 7
            cands.append((score, hwnd))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(_cb, None)
    if not cands:
        return 0
    cands.sort(reverse=True)
    return int(cands[0][1])


def _parse_region(text: Optional[str]) -> Optional[Tuple[int, int, int, int]]:
    if not text:
        return None
    parts = [int(float(p)) for p in re.split(r"[,\s]+", text.strip()) if p]
    if len(parts) != 4:
        raise SystemExit("--region 需要 4 个整数：left,top,right,bottom")
    return (parts[0], parts[1], parts[2], parts[3])


def _dump(img: Image.Image, prefix: str) -> str:
    out_dir = os.environ.get("WECHAT_OCR_DUMP", os.path.join(os.getcwd(), "_ocr_dump"))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{prefix}_{time.strftime('%H%M%S')}.png")
    img.save(path)
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="微信 OCR 驱动（UIA 失效时的读屏通道）")
    ap.add_argument("cmd", choices=["probe", "collect", "auto", "windows"])
    ap.add_argument("--hwnd", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--region", default=None, help="left,top,right,bottom（屏幕坐标）")
    ap.add_argument("--max", type=int, default=60, dest="max_comments")
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--notches", type=int, default=6)
    ap.add_argument("--settle", type=float, default=1.2)
    ap.add_argument("--incremental", action="store_true",
                    help="只 OCR 底部条带（高面板才划算；矮面板整区更快）")
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.cmd == "windows":
        hwnd = args.hwnd or find_wechat_hwnd()
        rows = []
        win32gui.EnumWindows(lambda h, _: rows.append((h, win32gui.GetClassName(h), win32gui.GetWindowText(h)))
                             if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) else None, None)
        print(json.dumps({"wechat": hwnd, "rect": window_rect(hwnd) if hwnd else None,
                          "windows": [r for r in rows if "微信" in r[2] or "WeChat" in r[2]]},
                         ensure_ascii=False, indent=2))
        return 0

    hwnd = args.hwnd or find_wechat_hwnd()
    if not hwnd:
        print("未找到微信窗口，请用 --hwnd 指定", file=sys.stderr)
        return 2
    region = _parse_region(args.region)

    if args.cmd == "probe":
        t0 = time.perf_counter()
        box = tuple(region or window_rect(hwnd))
        items, img = read_region_fast(hwnd, box)
        dt = int((time.perf_counter() - t0) * 1000)
        rows = extract_rows(items, region=box, img=img)
        out = {"hwnd": hwnd, "rect": list(box), "engine": engine_kind(),
               "ms": dt, "lines": len(items), "rows": len(rows),
               "rows_sample": rows[:15], "lines_sample": [it["text"] for it in items[:15]]}
        if args.dump:
            out["dump"] = _dump(img, "probe")
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "auto":
        res = collect_auto(
            hwnd, max_comments=args.max_comments,
            max_scroll_rounds=args.rounds, notches=args.notches, settle=args.settle,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    res = collect_comments(
        hwnd, region=region, max_comments=args.max_comments,
        max_scroll_rounds=args.rounds, notches=args.notches, settle=args.settle,
        incremental=args.incremental,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())