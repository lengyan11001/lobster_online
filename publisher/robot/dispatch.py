"""把 robot 操作暴露成"一次调用"。Agent 的动作 op 就是走这里。"""
from __future__ import annotations

import inspect
from typing import Any, Dict, Optional

from .ops import OP_FUNCS, OPS, Result


def list_ops(platform: str = "") -> list:
    out = []
    for name, spec in OPS.items():
        if platform and spec.platform != platform:
            continue
        out.append({
            "name": name,
            "summary": spec.summary,
            "requires_surface": spec.surface,
            "precondition": spec.precondition,
            "postcondition": spec.postcondition,
            "params": spec.params,
            "destructive": spec.destructive,
            "readonly": spec.readonly,
        })
    return sorted(out, key=lambda d: d["name"])


def _call(name: str, params: Dict[str, Any], *, window: Any = None, page: Any = None) -> Any:
    fn = OP_FUNCS.get(name)
    if fn is None:
        res = Result(op=str(name))
        res.layer, res.error = "dispatch", "unknown_op"
        return res
    kwargs = dict(params or {})
    if not kwargs:
        spec = OPS.get(str(name))
        need = [p for p in (spec.params if spec else []) if p not in {"dry_run", "limit"}]
        if need:
            res = Result(op=str(name))
            res.layer, res.error = "dispatch", "missing_param:" + ",".join(need)
            return res
    try:
        sig = inspect.signature(fn)
        if "window" in sig.parameters and window is not None:
            kwargs.setdefault("window", window)
        if "page" in sig.parameters and page is not None:
            kwargs.setdefault("page", page)
    except Exception:
        pass
    try:
        return fn(**kwargs)
    except TypeError as exc:
        res = Result(op=str(name))
        res.layer, res.error = "dispatch", f"bad_params:{exc}"[:160]
        return res


def run_op(name: str, params: Optional[Dict[str, Any]] = None, *, window: Any = None,
           page: Any = None) -> Result:
    """同步入口（桌面/UIA 用）。"""
    out = _call(name, params or {}, window=window, page=page)
    if inspect.isawaitable(out):
        out.close()  # type: ignore[union-attr]
        res = Result(op=str(name))
        res.layer, res.error = "dispatch", "async_op_in_sync_context"
        return res
    return out


async def run_op_async(name: str, params: Optional[Dict[str, Any]] = None, *,
                       window: Any = None, page: Any = None) -> Result:
    """异步入口（浏览器/Playwright 用）。"""
    out = _call(name, params or {}, window=window, page=page)
    if inspect.isawaitable(out):
        return await out
    return out
