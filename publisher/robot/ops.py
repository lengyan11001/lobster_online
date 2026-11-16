"""Op：带 surface 闸门 + 快照校验的操作。

统一协议：
    op(要求 surface, 前置 guard, 动作, 后置 = 快照差异)
任何一条不成立都返回 ok=False，且 error 说清楚卡在哪一层：
    wrong_surface / precondition_failed / action_failed / not_verified
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import surface as S


@dataclass
class Result:
    op: str
    ok: bool = False
    layer: str = ""          # 卡在哪一层
    error: str = ""
    surface: str = ""
    before: Dict[str, Any] = field(default_factory=dict)
    after: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "op": self.op, "ok": self.ok, "layer": self.layer, "error": self.error,
            "surface": self.surface, "before": self.before, "after": self.after,
            "evidence": self.evidence,
        }


@dataclass
class OpSpec:
    name: str
    platform: str
    surface: str
    summary: str
    precondition: str
    postcondition: str
    destructive: bool = False
    readonly: bool = False
    params: List[str] = field(default_factory=list)


OPS: Dict[str, OpSpec] = {}
OP_FUNCS: Dict[str, Callable] = {}


def spec(**kwargs: Any) -> Callable:
    def deco(fn: Callable) -> Callable:
        OPS[kwargs["name"]] = OpSpec(**kwargs)
        OP_FUNCS[kwargs["name"]] = fn
        fn.__op_spec__ = OPS[kwargs["name"]]
        return fn
    return deco


def describe() -> List[Dict[str, Any]]:
    out = []
    for s in OPS.values():
        out.append({
            "name": s.name, "platform": s.platform, "surface": s.surface,
            "summary": s.summary, "precondition": s.precondition,
            "postcondition": s.postcondition, "destructive": s.destructive,
            "readonly": s.readonly, "params": s.params,
        })
    return sorted(out, key=lambda d: d["name"])


def _ensure_surface(window: Any, required: str, res: Result) -> Optional[S.Snapshot]:
    snap = S.snapshot_desktop(window)
    res.surface = snap.surface
    res.before = snap.to_dict()
    if snap.surface != required:
        res.layer = "surface"
        res.error = "wrong_surface"
        res.evidence["required_surface"] = required
        return None
    return snap
