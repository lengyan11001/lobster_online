"""Agent core data models."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Observation:
    env: str
    stage: str
    url: str = ""
    title: str = ""
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    visible_text: str = ""
    screenshot_b64: str = ""
    signature: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_llm_dict(self, include_image: bool = True) -> Dict[str, Any]:
        payload = {
            "env": self.env,
            "url": self.url,
            "title": self.title,
            "stage": self.stage,
            "nodes": self.nodes[:80],
            "visible_text": self.visible_text[:2000],
            "extra": self.extra,
        }
        if include_image and self.screenshot_b64:
            payload["screenshot_b64"] = self.screenshot_b64
        return payload


@dataclass
class Action:
    action: str
    target: Dict[str, Any] = field(default_factory=dict)
    text: str = ""
    expect: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    thought: str = ""

    ALLOWED = {
        "click", "type", "set_files", "press", "wait_for",
        "scroll", "close_overlay", "reload", "screenshot", "abort",
    }

    def valid(self) -> bool:
        return self.action in self.ALLOWED and 0.0 <= float(self.confidence or 0.0) <= 1.0


@dataclass
class StepResult:
    ok: bool
    error: str = ""
    observed: str = ""


@dataclass
class RecoverResult:
    ok: bool = False
    recovered: bool = False
    steps: int = 0
    error: str = ""
    need_login: bool = False
    trace_id: str = ""
    mode: str = "off"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "recovered": self.recovered,
            "steps": self.steps,
            "error": self.error,
            "need_login": self.need_login,
            "trace_id": self.trace_id,
            "mode": self.mode,
        }
