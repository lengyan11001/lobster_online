"""能力层：把"怎么做"从剧本改成带前置/后置校验的积木。

设计约定（这一层是给 Agent 调用的，不是给人看的脚本）：
- 每个能力自己声明 precondition / postcondition；
- 能力返回 CapResult，其中 verified 必须是**真观察到**的证据，不能靠"动作执行成功"；
- 能力不负责重试和决策，失败了就如实返回，由 Agent 决定下一步。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class CapResult:
    name: str
    ok: bool = False
    precondition: bool = False
    action_ok: bool = False
    verified: bool = False
    error: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    artifact: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "precondition": self.precondition,
            "action_ok": self.action_ok,
            "verified": self.verified,
            "error": self.error,
            "evidence": self.evidence,
            "artifact": self.artifact,
        }


@dataclass
class Capability:
    """能力的元信息：给 planner 看的说明书，也是回归测试的断言依据。"""

    name: str
    platform: str
    summary: str
    precondition: str
    postcondition: str
    destructive: bool = False
    params: List[str] = field(default_factory=list)

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "platform": self.platform,
            "summary": self.summary,
            "precondition": self.precondition,
            "postcondition": self.postcondition,
            "destructive": self.destructive,
            "params": list(self.params),
        }
