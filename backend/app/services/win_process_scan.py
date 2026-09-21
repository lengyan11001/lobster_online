"""Windows 进程枚举（不依赖 psutil）。

客户端出问题的机器上经常没有 psutil：老实现拿不到进程名/路径，就退化成"只看窗口类名"，
于是「设置」「Windows 输入体验」「Edge」这些系统窗口全被当成 WhatsApp 候选，
同时也没法用进程名过滤 → 识别率看起来时好时坏。

这里用 Windows 原生 API（kernel32）实现同一件事：
  * CreateToolhelp32Snapshot / Process32FirstW  → 全表 pid + 进程名
  * OpenProcess + QueryFullProcessImageNameW    → 单个 pid 的完整路径
psutil 存在时优先用它（行为与老版本一致），否则自动走 ctypes，功能等价。

2026-09-21 新增：diag_20260921073448_831a0dad 显示客户机 psutil=false → 候选窗口混入系统窗口。
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from typing import Any, Dict, List, Optional, Tuple

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_PATH_LONG = 32768

_SNAPSHOT_TTL_SECONDS = 1.5
_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {"at": 0.0, "rows": [], "paths": {}}


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def _psutil_module() -> Any:
    try:
        import psutil  # type: ignore

        return psutil
    except Exception:
        return None


def process_scan_backend() -> str:
    """返回当前实际使用的进程枚举后端：psutil / ctypes / none。"""
    if _psutil_module() is not None:
        return "psutil"
    if os.name == "nt":
        return "ctypes"
    return "none"


def _toolhelp_rows() -> List[Dict[str, Any]]:
    """ctypes 全表枚举：pid + 进程名（不含路径，要路径时按需单独查）。"""
    if os.name != "nt":
        return []
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not handle or handle == INVALID_HANDLE_VALUE:
        return []
    rows: List[Dict[str, Any]] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(handle, ctypes.byref(entry))
        while ok:
            pid = int(entry.th32ProcessID)
            name = str(entry.szExeFile or "").strip()
            if pid > 0 and name:
                rows.append({"pid": pid, "name": name, "exe": ""})
            ok = kernel32.Process32NextW(handle, ctypes.byref(entry))
    except Exception:
        return rows
    finally:
        try:
            kernel32.CloseHandle(handle)
        except Exception:
            pass
    return rows


def _psutil_rows() -> List[Dict[str, Any]]:
    psutil = _psutil_module()
    if psutil is None:
        return []
    rows: List[Dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            info = proc.info or {}
            rows.append(
                {
                    "pid": int(info.get("pid") or 0),
                    "name": str(info.get("name") or ""),
                    "exe": str(info.get("exe") or ""),
                }
            )
        except Exception:
            continue
    return rows


def snapshot_processes(*, ttl: float = _SNAPSHOT_TTL_SECONDS) -> List[Dict[str, Any]]:
    """全表进程列表（带短 TTL 缓存，避免一次窗口扫描里反复枚举）。"""
    now = time.monotonic()
    with _LOCK:
        cached = list(_CACHE.get("rows") or [])
        if cached and now - float(_CACHE.get("at") or 0.0) < ttl:
            return cached
    rows = _psutil_rows() or _toolhelp_rows()
    with _LOCK:
        _CACHE["rows"] = rows
        _CACHE["at"] = time.monotonic()
    return rows


def process_path(pid: int) -> str:
    """单个 pid 的完整可执行文件路径（拿不到返回空串）。"""
    pid = int(pid or 0)
    if pid <= 0 or os.name != "nt":
        return ""
    with _LOCK:
        cached = (_CACHE.get("paths") or {}).get(pid)
    if cached is not None:
        return str(cached)
    path = ""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        try:
            buffer = ctypes.create_unicode_buffer(MAX_PATH_LONG)
            size = wintypes.DWORD(MAX_PATH_LONG)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                path = str(buffer.value or "")
        except Exception:
            path = ""
        finally:
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
    with _LOCK:
        paths = dict(_CACHE.get("paths") or {})
        if len(paths) > 4096:
            paths.clear()
        paths[pid] = path
        _CACHE["paths"] = paths
    return path


def process_image(pid: int) -> Tuple[str, str]:
    """(进程名, 完整路径)。名字优先取自全表快照，路径按需查（失败留空）。"""
    pid = int(pid or 0)
    if pid <= 0:
        return "", ""
    if _psutil_module() is not None:
        psutil = _psutil_module()
        try:
            proc = psutil.Process(pid)  # type: ignore[union-attr]
            return str(proc.name() or ""), str(proc.exe() or "")
        except Exception:
            pass
    name = ""
    for row in snapshot_processes():
        if int(row.get("pid") or 0) == pid:
            name = str(row.get("name") or "")
            exe = str(row.get("exe") or "")
            if exe:
                return name, exe
            break
    path = process_path(pid)
    if not name and path:
        name = os.path.basename(path)
    return name, path


def reset_cache() -> None:
    with _LOCK:
        _CACHE["rows"] = []
        _CACHE["at"] = 0.0
        _CACHE["paths"] = {}


def sample() -> Dict[str, Any]:
    """诊断用：当前后端 + 前几条进程，确认枚举真的可用。"""
    rows = snapshot_processes(ttl=0.0)
    return {
        "backend": process_scan_backend(),
        "count": len(rows),
        "sample": [
            {"pid": row.get("pid"), "name": row.get("name")}
            for row in rows[:8]
        ],
    }
