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


# 服务端模型编排是 server-owned（backend/app/api/sutui_chat_proxy.py:1296
# _sutui_chat_model_candidates）：deepseek-flash 是文本+图片的主档（官方 deepseek-flash
# 接口支持 image_url），后面依次 change2pro / YYAPI 的 gpt-5.6-sol 兜底。
# 所以这边只发一个 model id，兜底交给服务端候选链，不自己做“换模型”。
PRIMARY_MODEL_DEFAULT = "deepseek-flash"


def _strip_sutui(raw: str) -> str:
    raw = (raw or "").strip()
    return raw.split("/", 1)[1].strip() if raw.lower().startswith("sutui/") else raw


def _vision_allowed() -> bool:
    return str(os.environ.get("LOBSTER_AGENT_VISION") or "1").strip().lower() in {"1", "true", "yes", "on"}


def _model_name() -> str:
    override = _strip_sutui(os.environ.get("LOBSTER_AGENT_MODEL") or "")
    if override:
        return override
    return _strip_sutui(os.environ.get("LOBSTER_AGENT_PRIMARY_MODEL") or PRIMARY_MODEL_DEFAULT)


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


def _want_vision(observation: Observation, history: List[Dict[str, Any]], budget: Any, cfg: Dict[str, Any]) -> bool:
    """先便宜后视觉：DOM 观察够清楚（节点多）就不带图；页面是画布/iframe/已经被打回一次，
    才把截图一起发给 deepseek-flash（同一个 model，服务端候选链自己兜底到 gpt-5.6-sol）。
    带图一次最低 10 算力，所以按次限量。"""
    if not _vision_allowed() or not observation.screenshot_b64:
        return False
    if str(cfg.get("api_key") or "").strip():
        return False  # 直连 provider 档没有 sutui 的图片路由，别把图塞过去
    if not budget.take_vision():
        return False
    if any(not h.get("ok") for h in history):
        return True
    return len(observation.nodes) <= 10


def _build_payload(observation: Observation, goal: str, history: List[Dict[str, Any]], budget: Any, cfg: Dict[str, Any], *, vision: bool = False) -> Dict[str, Any]:
    model = str(cfg.get("model_name") or _model_name())
    obs = observation.to_llm_dict(include_image=False)
    text = json.dumps({"goal": goal, "observation": obs, "history": history[-6:],
                       "budget": {"steps_left": max(0, budget.max_steps - budget.steps),
                                  "llm_calls_left": max(0, budget.max_llm_calls - budget.llm_calls)}},
                      ensure_ascii=False)
    if vision and observation.screenshot_b64:
        content: Any = [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + observation.screenshot_b64}},
            {"type": "text", "text": text},
        ]
    else:
        content = text
    return {"model": model, "temperature": 0.1, "max_tokens": 400,
            "messages": [{"role": "system", "content": _system_prompt()},
                         {"role": "user", "content": content}]}


async def _post_chat(url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Tuple[Optional[Dict[str, Any]], int]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=90, write=30, pool=10)) as client:
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
    vision = _want_vision(observation, history, budget, cfg)
    payload = _build_payload(observation, goal, history, budget, cfg, vision=vision)
    logger.info("[agent] llm model=%s vision=%s url=%s", payload.get("model"), vision, url)
    data, status = await _post_chat(url, payload, _headers(cfg))
    if status == 402:
        # 余额不足跟模型档无关，重试纯属浪费：直接收手，日志里能看见真实原因
        logger.warning("[agent] insufficient credit for LLM call (402) -> stop recovery")
        return None
    if data is None and vision:
        # 视觉档挂了（路由/超时/4xx）不能让整次恢复失败：退 text-only 再问一次
        logger.warning("[agent] vision lane failed -> fallback text-only")
        payload = _build_payload(observation, goal, history, budget, cfg, vision=False)
        data, _ = await _post_chat(url, payload, _headers(cfg))
    if data is None:
        return None
    content = ""
    try:
        content = data["choices"][0]["message"]["content"] or ""
    except Exception:
        content = ""
    return _parse_action(_extract_json(content))


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
