"""Budget and feature flags for the publish agent."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

DEFAULT_MAX_STEPS = 12
DEFAULT_TIMEOUT_SECONDS = 90.0
DEFAULT_MAX_LLM_CALLS = 6


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip().lower()


def agent_mode(env: str = "browser") -> str:
    """off | observe | recover. 默认 recover（失败时才真正调用）。"""
    specific = _env(f"LOBSTER_AGENT_{env.upper()}_MODE", "")
    raw = specific or _env("LOBSTER_AGENT_MODE", "recover")
    return raw if raw in {"off", "observe", "recover"} else "recover"


def agent_enabled(env: str = "browser") -> bool:
    return agent_mode(env) in {"observe", "recover"}


@dataclass
class Budget:
    max_steps: int = DEFAULT_MAX_STEPS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_llm_calls: int = DEFAULT_MAX_LLM_CALLS
    steps: int = 0
    llm_calls: int = 0
    started_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = time.monotonic()

    def take_step(self) -> bool:
        if self.steps >= self.max_steps:
            return False
        if self.elapsed() > self.timeout_seconds:
            return False
        self.steps += 1
        return True

    def take_llm(self) -> bool:
        if self.llm_calls >= self.max_llm_calls:
            return False
        if self.elapsed() > self.timeout_seconds:
            return False
        self.llm_calls += 1
        return True

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def exhausted(self) -> bool:
        return self.steps >= self.max_steps or self.elapsed() > self.timeout_seconds
