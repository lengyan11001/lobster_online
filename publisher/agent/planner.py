"""LLM planner: returns a strict JSON action for the next step."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

from .models import Action, Observation

logger = logging.getLogger(__name__)

_ALLOWED_ACTIONS = Action.ALLOWED


# 模型编排全在服务端（sutui_chat_proxy.py:1296 _sutui_chat_model_candidates）：
# deepseek-flash 直连主档 → change2pro / YYAPI 的 gpt-5.6-sol 兜底，带不带图由服务端判定。
# 客户端只管发请求，不选模型、不换档、不做兜底。
DEFAULT_MODEL = "deepseek-flash"


def _model_name() -> str:
    raw = (os.environ.get("LOBSTER_AGENT_MODEL") or "").strip()
    if raw.lower().startswith("sutui/"):
        raw = raw.split("/", 1)[1].strip()
    return raw or DEFAULT_MODEL


def _system_prompt() -> str:
    return (
        "You are a browser/desktop automation assistant. Reply with ONE JSON object only, no prose.\n"
        "Schema: {\"thought\":str,\"action\":str,\"target\":object,\"text\":str,\"expect\":object,\"confidence\":float}\n"
        "Allowed actions: click,type,set_files,press,wait_for,scroll,close_overlay,reload,screenshot,abort.\n"
        "target supports {by:'text'|'role'|'selector'|'point', value, role, x, y}.\n"
        "To attach a video/image the ONLY correct action is set_files with "
        "target {by:'selector', value:'input[type=file]'} (the file path is supplied by the caller).\n"
        "expect.kind MUST be one of: text, role, url_contains, element_count, media_attached, any.\n"
        "  text: {kind:'text', value:'visible text'} | role: {kind:'role', role:'button', name:'name'}\n"
        "  element_count: {kind:'element_count', selector:'css', min:1} | media_attached: {kind:'media_attached'}\n"
        "Never publish/delete/pay/switch account/execute JS. Confidence below 0.6 => abort.\n"
        "Prefer semantic targets (text/role). Do not repeat the same failed action twice."
    )

def _resolve_llm_config() -> Dict[str, Any]:
    """Reuse the client chat config when available; otherwise fall back to env keys."""
    try:
        from backend.app.api.chat import _resolve_config  # type: ignore
        cfg = _resolve_config(_model_name())
        if cfg:
            return dict(cfg)
    except Exception as exc:
        logger.debug("[agent] resolve chat config failed: %s", exc)
    base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com").strip().rstrip("/")
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if key:
        return {"base_url": base, "api_key": key, "model_name": _model_name(), "provider": "openai"}
    return {}


def _extract_json(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\\s*", "", raw)
        raw = re.sub(r"\\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    match = re.search(r"\\{.*\\}", raw, re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_action(data: Dict[str, Any]) -> Optional[Action]:
    if not isinstance(data, dict) or not data:
        return None
    action = Action(
        action=str(data.get("action") or "").strip().lower(),
        target=dict(data.get("target") or {}),
        text=str(data.get("text") or ""),
        expect=dict(data.get("expect") or {}),
        confidence=float(data.get("confidence") or 0.0),
        thought=str(data.get("thought") or ""),
    )
    if not action.valid():
        return None
    if action.confidence < 0.6:
        return Action(action="abort", confidence=action.confidence, thought=action.thought)
    return action

def _chat_endpoint(cfg: Dict[str, Any]) -> str:
    base = str(cfg.get("base_url") or "").strip().rstrip("/")
    if base:
        if base.endswith("/v1"):
            return base + "/chat/completions"
        if "deepseek" in base:
            return base + "/chat/completions"
        return base + "/v1/chat/completions"
    try:
        from backend.app.core.config import settings  # type: ignore
        return settings.auth_server_base.rstrip("/") + "/api/sutui-chat/completions"
    except Exception:
        return ""


def _headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    out = {"Content-Type": "application/json"}
    key = str(cfg.get("api_key") or "").strip()
    if key:
        out["Authorization"] = f"Bearer {key}"
    else:
        try:
            from backend.app.services.creator_metrics_daily_runner import read_login_context  # type: ignore
            token, _ = read_login_context()
            if token:
                out["Authorization"] = f"Bearer {token}"
        except Exception:
            pass
    return out


def _build_payload(observation: Observation, goal: str, history: List[Dict[str, Any]], budget: Any, cfg: Dict[str, Any]) -> Dict[str, Any]:
    obs = observation.to_llm_dict(include_image=False)
    text = json.dumps({"goal": goal, "observation": obs, "history": history[-6:],
                       "budget": {"steps_left": max(0, budget.max_steps - budget.steps),
                                  "llm_calls_left": max(0, budget.max_llm_calls - budget.llm_calls)}},
                      ensure_ascii=False)
    if observation.screenshot_b64:
        content: Any = [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + observation.screenshot_b64}},
            {"type": "text", "text": text},
        ]
    else:
        content = text
    return {"model": str(cfg.get("model_name") or _model_name()), "temperature": 0.1, "max_tokens": 400,
            "messages": [{"role": "system", "content": _system_prompt()},
                         {"role": "user", "content": content}]}


async def _post_chat(url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Tuple[Optional[Dict[str, Any]], int]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=240, write=30, pool=10)) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                logger.warning("[agent] llm http %s: %s", resp.status_code, resp.text[:300])
                return None, resp.status_code
            return resp.json(), resp.status_code
    except Exception as exc:
        logger.warning("[agent] llm decide failed: %s: %s", type(exc).__name__, exc or "(no message)")
        return None, 0


async def decide_async(observation: Observation, goal: str, history: List[Dict[str, Any]], budget: Any) -> Optional[Action]:
    if not budget.take_llm():
        return None
    cfg = _resolve_llm_config()
    url = _chat_endpoint(cfg)
    if not url:
        logger.info("[agent] no llm endpoint configured")
        return None
    payload = _build_payload(observation, goal, history, budget, cfg)
    logger.info("[agent] llm model=%s image=%s url=%s", payload.get("model"),
                isinstance(payload["messages"][1]["content"], list), url)
    data, status = await _post_chat(url, payload, _headers(cfg))
    if status == 402:
        logger.warning("[agent] insufficient credit for LLM call (402) -> stop recovery")
        return None
    if data is None:
        return None
    logger.info("[agent] llm served by model=%s", data.get("model") or "-")
    content = ""
    try:
        content = data["choices"][0]["message"]["content"] or ""
    except Exception:
        content = ""
    action = _parse_action(_extract_json(content))
    if action is None:
        logger.warning("[agent] unparsable llm reply: %s", (content or "")[:300])
    return action


def decide_sync(observation: Observation, goal: str, history: List[Dict[str, Any]], budget: Any) -> Optional[Action]:
    if not budget.take_llm():
        return None
    cfg = _resolve_llm_config()
    url = _chat_endpoint(cfg)
    if not url:
        return None
    payload = _build_payload(observation, goal, history, budget, cfg)
    try:
        with httpx.Client(timeout=httpx.Timeout(connect=10, read=60, write=30, pool=10)) as client:
            resp = client.post(url, json=payload, headers=_headers(cfg))
            if resp.status_code >= 400:
                logger.warning("[agent] llm http %s: %s", resp.status_code, resp.text[:300])
                return None
            data = resp.json()
        content = ""
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except Exception:
            content = ""
        return _parse_action(_extract_json(content))
    except Exception as exc:
        logger.warning("[agent] llm decide(sync) failed: %s: %s", type(exc).__name__, exc or "(no message)")
        return None
