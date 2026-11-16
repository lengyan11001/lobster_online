"""可组合能力（Agent 的工具面）。

每个模块导出的能力都满足：前置条件 → 动作 → 后置校验，且后置校验靠真实观察。
"""
from .base import CapResult, Capability  # noqa: F401

__all__ = ["CapResult", "Capability"]
