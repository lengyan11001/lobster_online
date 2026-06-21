"""OpenClaw Gateway configuration: status check, API key management, model selection, restart."""
import asyncio
import json
import logging
import os
import platform
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..core.config import settings
from .auth import get_current_user_for_local, _ServerUser

logger = logging.getLogger(__name__)

# 清除本机 Key / 保存配置 / 手动重启 可能并发触发重启；串行化避免重复 Popen 出多个 node
_OPENCLAW_RESTART_LOCK = threading.Lock()

# 微信 OpenClaw 插件扫码登录（channels login）仅允许单任务，避免多进程争用
_WEIXIN_LOGIN_LOCK = threading.Lock()
_weixin_login_jobs: Dict[str, Dict[str, Any]] = {}
_weixin_login_active_job_id: Optional[str] = None
_WEIXIN_LOGIN_PROC_HOLDER: Dict[str, Any] = {"proc": None}  # 当前子进程，供超时杀掉
_WEIXIN_LOGIN_MAX_SEC = 520

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
_OC_DIR = _BASE_DIR / "openclaw"
_OC_CONFIG = _OC_DIR / "openclaw.json"
_WEIXIN_LEDGER = _OC_DIR / ".weixin_login_last.json"
_OC_ENV = _OC_DIR / ".env"
_OC_PLUGIN_STATE_BACKUP = _OC_DIR / ".lobster_plugin_state_backup.json"
_OPENCLAW_STARTUP_LOG_DIR = _BASE_DIR / "logs" / "openclaw_startup"
_OPENCLAW_STARTUP_DIAG_LOCK = threading.Lock()
_OPENCLAW_LAST_STARTUP_DIAG: Dict[str, Any] = {}
_OPENCLAW_GATEWAY_TOKEN_PLACEHOLDER = "LOBSTER_AUTO_TOKEN_PLACEHOLDER"
_OPENCLAW_PLUGIN_MODE_LEAN = "lean"
_OPENCLAW_PLUGIN_MODE_WEIXIN = "weixin"
_OPENCLAW_WEIXIN_PLUGIN_ID = "openclaw-weixin"

SUPPORTED_PROVIDERS = [
    {"id": "anthropic", "name": "Anthropic", "env_key": "ANTHROPIC_API_KEY",
     "models": ["anthropic/claude-sonnet-4-5", "anthropic/claude-opus-4-6", "anthropic/claude-haiku-3-5"]},
    {"id": "openai", "name": "OpenAI", "env_key": "OPENAI_API_KEY",
     "models": ["openai/gpt-4o", "openai/gpt-4o-mini", "openai/o3-mini"]},
    {"id": "deepseek", "name": "DeepSeek", "env_key": "DEEPSEEK_API_KEY",
     "models": ["deepseek/deepseek-chat", "deepseek/deepseek-reasoner"]},
    {"id": "google", "name": "Google", "env_key": "GEMINI_API_KEY",
     "models": ["google/gemini-2.5-pro", "google/gemini-2.5-flash"]},
]

DEEPSEEK_PROVIDER_TEMPLATE = {
    "baseUrl": "https://api.deepseek.com",
    "api": "openai-completions",
    "models": [
        {"id": "deepseek-chat", "name": "DeepSeek Chat", "input": ["text"],
         "contextWindow": 65536, "maxTokens": 8192},
        {"id": "deepseek-reasoner", "name": "DeepSeek Reasoner", "reasoning": True,
         "input": ["text"], "contextWindow": 65536, "maxTokens": 8192},
    ],
}

LOBSTER_SUTUI_PROVIDER_ID = "lobster-sutui"
LOBSTER_SUTUI_BASE_URL = "http://127.0.0.1:8000/internal/openclaw-sutui/v1"
LOBSTER_SUTUI_CHAT_MODEL_ID = "deepseek-chat"
LOBSTER_SUTUI_CHAT_MODEL = f"{LOBSTER_SUTUI_PROVIDER_ID}/{LOBSTER_SUTUI_CHAT_MODEL_ID}"
LOBSTER_SUTUI_AGENT_ID = "lobster-sutui-deepseek-chat"
LOBSTER_SUTUI_SKILL_MODEL_ID = "openclaw-skill-chat"
LOBSTER_SUTUI_SKILL_MODEL = f"{LOBSTER_SUTUI_PROVIDER_ID}/{LOBSTER_SUTUI_SKILL_MODEL_ID}"
LOBSTER_SUTUI_MODEL_ENTRIES = [
    {
        "id": LOBSTER_SUTUI_CHAT_MODEL_ID,
        "name": "Sutui DeepSeek Chat",
        "reasoning": False,
        "input": ["text"],
        "contextWindow": 65536,
        "maxTokens": 8192,
    },
    {
        "id": LOBSTER_SUTUI_SKILL_MODEL_ID,
        "name": "Sutui OpenClaw Skill Chat",
        "reasoning": False,
        "input": ["text"],
        "contextWindow": 65536,
        "maxTokens": 8192,
    },
]
LOBSTER_SUTUI_MAIN_AGENT_TOOLS = {
    "deny": [
        "group:runtime",
        "group:fs",
        "group:ui",
        "group:web",
        "group:automation",
        "group:nodes",
        "x_search",
    ],
    "exec": {"security": "deny", "ask": "off"},
}
LOBSTER_SUTUI_THINKING_DEFAULT = "off"


def _apply_lobster_sutui_provider_config(provider: dict, proxy_key: str = "") -> bool:
    changed = False
    if provider.get("baseUrl") != LOBSTER_SUTUI_BASE_URL:
        provider["baseUrl"] = LOBSTER_SUTUI_BASE_URL
        changed = True
    if provider.get("api") != "openai-completions":
        provider["api"] = "openai-completions"
        changed = True
    if proxy_key and provider.get("apiKey") != proxy_key:
        provider["apiKey"] = proxy_key
        changed = True
    entries = provider.setdefault("models", [])
    if not isinstance(entries, list):
        entries = []
        provider["models"] = entries
        changed = True
    for desired in LOBSTER_SUTUI_MODEL_ENTRIES:
        existing = next(
            (item for item in entries if isinstance(item, dict) and item.get("id") == desired["id"]),
            None,
        )
        if not existing:
            entries.append(dict(desired))
            changed = True
            continue
        for key, value in desired.items():
            if existing.get(key) != value:
                existing[key] = value
                changed = True
    return changed


def _mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return ""
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def _read_oc_env() -> dict[str, str]:
    result: dict[str, str] = {}
    if not _OC_ENV.exists():
        return result
    for line in _OC_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def _write_oc_env(data: dict[str, str]):
    _OC_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append("# OpenClaw LLM API Keys")
    lines.append("# 在龙虾后台设置后自动写入")
    for k, v in sorted(data.items()):
        lines.append(f"{k}={v}")
    _OC_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_oc_config() -> dict:
    if not _OC_CONFIG.exists():
        return {}
    try:
        return json.loads(_OC_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_oc_config(config: dict):
    _OC_DIR.mkdir(parents=True, exist_ok=True)
    _OC_CONFIG.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _configured_gateway_token(config: Optional[dict] = None) -> str:
    token = (settings.openclaw_gateway_token or os.environ.get("OPENCLAW_GATEWAY_TOKEN") or "").strip()
    if token and token != _OPENCLAW_GATEWAY_TOKEN_PLACEHOLDER:
        return token
    try:
        cfg = config if isinstance(config, dict) else _read_oc_config()
        auth = ((cfg.get("gateway") or {}).get("auth") or {}) if isinstance(cfg, dict) else {}
        cfg_token = str(auth.get("token") or "").strip()
        if cfg_token and cfg_token != _OPENCLAW_GATEWAY_TOKEN_PLACEHOLDER:
            return cfg_token
    except Exception:
        pass
    return ""


def _new_openclaw_local_launch_config() -> dict[str, Any]:
    """Build the minimum runtime config OpenClaw needs for local Gateway launch."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "meta": {"lastTouchedAt": now},
        "commands": {
            "native": "auto",
            "nativeSkills": "auto",
            "restart": True,
            "ownerDisplay": "raw",
        },
        "tools": {"profile": "full"},
        "plugins": {
            "enabled": False,
            "entries": {},
            "installs": {},
            "load": {"paths": []},
        },
        "gateway": {
            "mode": "local",
            "auth": {},
            "http": {
                "endpoints": {
                    "chatCompletions": {"enabled": True},
                },
            },
            "reload": {"mode": "off"},
        },
        "mcp": {
            "servers": {
                "lobster": {
                    "url": "http://127.0.0.1:8000/mcp-gateway",
                    "transport": "streamable-http",
                },
            },
        },
        "models": {"mode": "merge", "providers": {}},
        "agents": {"defaults": {}, "list": []},
        "channels": {"slack": {"enabled": False}},
        "discovery": {"mdns": {"mode": "off"}},
    }


def _read_plugin_state_backup() -> dict:
    if not _OC_PLUGIN_STATE_BACKUP.exists():
        return {}
    try:
        data = json.loads(_OC_PLUGIN_STATE_BACKUP.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_plugin_state_backup(data: dict) -> None:
    _OC_DIR.mkdir(parents=True, exist_ok=True)
    _OC_PLUGIN_STATE_BACKUP.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _stash_openclaw_plugin_state(config: dict) -> bool:
    backup = _read_plugin_state_backup()
    next_backup = dict(backup)
    plugins = config.get("plugins")
    channels = config.get("channels")
    changed = False
    if isinstance(plugins, dict) and plugins:
        next_backup["plugins"] = plugins
    if isinstance(channels, dict) and _OPENCLAW_WEIXIN_PLUGIN_ID in channels:
        saved_channels = next_backup.get("channels")
        if not isinstance(saved_channels, dict):
            saved_channels = {}
        saved_channels[_OPENCLAW_WEIXIN_PLUGIN_ID] = channels[_OPENCLAW_WEIXIN_PLUGIN_ID]
        next_backup["channels"] = saved_channels
    if isinstance(channels, dict) and isinstance(channels.get("slack"), dict):
        saved_channels = next_backup.get("channels")
        if not isinstance(saved_channels, dict):
            saved_channels = {}
        saved_channels["slack"] = channels["slack"]
        next_backup["channels"] = saved_channels
    if next_backup != backup:
        _write_plugin_state_backup(next_backup)
        changed = True
    return changed


def _restore_openclaw_plugin_state(config: dict) -> bool:
    backup = _read_plugin_state_backup()
    changed = False
    plugins = backup.get("plugins")
    if isinstance(plugins, dict) and plugins and config.get("plugins") != plugins:
        config["plugins"] = plugins
        changed = True
    saved_channels = backup.get("channels")
    if isinstance(saved_channels, dict) and _OPENCLAW_WEIXIN_PLUGIN_ID in saved_channels:
        channels = config.setdefault("channels", {})
        if isinstance(channels, dict) and channels.get(_OPENCLAW_WEIXIN_PLUGIN_ID) != saved_channels[_OPENCLAW_WEIXIN_PLUGIN_ID]:
            channels[_OPENCLAW_WEIXIN_PLUGIN_ID] = saved_channels[_OPENCLAW_WEIXIN_PLUGIN_ID]
            changed = True
    return changed


def _is_openclaw_install_stage_path(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return ".openclaw-install-stage-" in value.replace("\\", "/")


def _cleanup_openclaw_install_stage_dirs() -> list[str]:
    """Remove OpenClaw plugin install staging leftovers before gateway launch.

    The OpenClaw gateway auto-discovers extension directories. If an interrupted
    plugin install leaves .openclaw-install-stage-* directories behind, the same
    plugin can be loaded multiple times and gateway startup can take much longer.
    """
    ext_dir = _OC_DIR / "extensions"
    if not ext_dir.is_dir():
        return []
    removed: list[str] = []
    for path in ext_dir.iterdir():
        if not path.is_dir() or not path.name.startswith(".openclaw-install-stage-"):
            continue
        err = _rmtree_best_effort(path)
        if err:
            logger.warning("Failed to remove OpenClaw staging extension %s: %s", path, err)
        else:
            removed.append(str(path))
    return removed


def _set_openclaw_plugin_launch_mode(config: dict, mode: str = _OPENCLAW_PLUGIN_MODE_LEAN) -> bool:
    """Keep normal OpenClaw startup light; enable channel plugins only when needed."""
    mode = mode if mode == _OPENCLAW_PLUGIN_MODE_WEIXIN else _OPENCLAW_PLUGIN_MODE_LEAN
    if mode == _OPENCLAW_PLUGIN_MODE_LEAN:
        changed = _stash_openclaw_plugin_state(config)
        lean_plugins = {"enabled": False, "entries": {}, "installs": {}, "load": {"paths": []}}
        if config.get("plugins") != lean_plugins:
            config["plugins"] = lean_plugins
            changed = True
        channels = config.get("channels")
        if isinstance(channels, dict) and _OPENCLAW_WEIXIN_PLUGIN_ID in channels:
            channels.pop(_OPENCLAW_WEIXIN_PLUGIN_ID, None)
            changed = True
        channels = config.setdefault("channels", {})
        if isinstance(channels, dict):
            slack_cfg = channels.setdefault("slack", {})
            if not isinstance(slack_cfg, dict):
                slack_cfg = {}
                channels["slack"] = slack_cfg
                changed = True
            if slack_cfg.get("enabled") is not False:
                slack_cfg["enabled"] = False
                changed = True
        return changed

    changed = _restore_openclaw_plugin_state(config)
    plugins = config.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        config["plugins"] = plugins = {}
        changed = True
    desired_enabled = True
    if plugins.get("enabled") is not desired_enabled:
        plugins["enabled"] = desired_enabled
        changed = True

    entries = plugins.setdefault("entries", {})
    if not isinstance(entries, dict):
        entries = {}
        plugins["entries"] = entries
        changed = True
    installs = plugins.get("installs")
    if not isinstance(installs, dict):
        installs = {}

    plugin_ids = set(entries.keys()) | set(installs.keys())
    plugin_ids.add(_OPENCLAW_WEIXIN_PLUGIN_ID)

    for plugin_id in sorted(pid for pid in plugin_ids if isinstance(pid, str) and pid.strip()):
        entry = entries.setdefault(plugin_id, {})
        if not isinstance(entry, dict):
            entry = {}
            entries[plugin_id] = entry
            changed = True
        should_enable = plugin_id == _OPENCLAW_WEIXIN_PLUGIN_ID
        if entry.get("enabled") is not should_enable:
            entry["enabled"] = should_enable
            changed = True

    deny = plugins.get("deny")
    if not isinstance(deny, list):
        deny = []
    deny_set = {str(x) for x in deny if isinstance(x, str) and x.strip()}
    deny_set.discard(_OPENCLAW_WEIXIN_PLUGIN_ID)
    desired_denied = set(plugin_ids)
    desired_denied.discard(_OPENCLAW_WEIXIN_PLUGIN_ID)
    new_deny = sorted(deny_set | desired_denied)
    if new_deny != deny:
        plugins["deny"] = new_deny
        changed = True

    allow = plugins.get("allow")
    if isinstance(allow, list) and allow:
        allow_set = {str(x) for x in allow if isinstance(x, str) and x.strip()}
        allow_set.add(_OPENCLAW_WEIXIN_PLUGIN_ID)
        new_allow = sorted(allow_set)
        if new_allow != allow:
            plugins["allow"] = new_allow
            changed = True

    load = plugins.setdefault("load", {})
    if not isinstance(load, dict):
        load = {}
        plugins["load"] = load
        changed = True
    paths = load.get("paths")
    if not isinstance(paths, list):
        paths = []
    stable_weixin = _OC_DIR / "extensions" / "openclaw-weixin"
    bundled_weixin = _BASE_DIR / "nodejs" / "node_modules" / "@tencent-weixin" / "openclaw-weixin"
    stable_weixin_s = str(stable_weixin)
    if stable_weixin.exists():
        new_paths = [stable_weixin_s]
        if new_paths != paths:
            load["paths"] = new_paths
            changed = True
    elif bundled_weixin.exists():
        new_paths = [str(bundled_weixin)]
        if new_paths != paths:
            load["paths"] = new_paths
            changed = True
    else:
        new_paths = [
            p for p in paths
            if not (isinstance(p, str) and ("openclaw-weixin" in p or _is_openclaw_install_stage_path(p)))
        ]
        if new_paths != paths:
            load["paths"] = new_paths
            changed = True

    return changed


def _openclaw_local_model_patch_needed(config: dict) -> bool:
    try:
        agents = config.get("agents")
        if not isinstance(agents, dict):
            return True
        defaults = agents.get("defaults")
        if not isinstance(defaults, dict):
            return True
        model_cfg = defaults.get("model")
        if not isinstance(model_cfg, dict) or model_cfg.get("primary") != LOBSTER_SUTUI_CHAT_MODEL:
            return True
        if defaults.get("thinkingDefault") != LOBSTER_SUTUI_THINKING_DEFAULT:
            return True
        if int(defaults.get("timeoutSeconds") or 0) < 600:
            return True
        llm_cfg = defaults.get("llm")
        if not isinstance(llm_cfg, dict) or int(llm_cfg.get("idleTimeoutSeconds") or 0) < 180:
            return True

        models = config.get("models")
        if not isinstance(models, dict) or models.get("mode") != "merge":
            return True
        providers = models.get("providers")
        if not isinstance(providers, dict):
            return True
        provider = providers.get(LOBSTER_SUTUI_PROVIDER_ID)
        if not isinstance(provider, dict):
            return True
        if provider.get("baseUrl") != LOBSTER_SUTUI_BASE_URL or provider.get("api") != "openai-completions":
            return True
        entries = provider.get("models")
        if not isinstance(entries, list):
            return True
        entry_ids = {item.get("id") for item in entries if isinstance(item, dict)}
        if not {LOBSTER_SUTUI_CHAT_MODEL_ID, LOBSTER_SUTUI_SKILL_MODEL_ID}.issubset(entry_ids):
            return True

        agent_list = agents.get("list")
        if not isinstance(agent_list, list):
            return True
        found_main = False
        found_sutui = False
        for item in agent_list:
            if not isinstance(item, dict):
                continue
            if item.get("id") == "main":
                found_main = True
                if item.get("model") not in (None, "", LOBSTER_SUTUI_CHAT_MODEL):
                    return True
                if item.get("thinkingDefault") != LOBSTER_SUTUI_THINKING_DEFAULT:
                    return True
            if item.get("id") == LOBSTER_SUTUI_AGENT_ID and item.get("model") == LOBSTER_SUTUI_CHAT_MODEL:
                found_sutui = True
                if item.get("thinkingDefault") != LOBSTER_SUTUI_THINKING_DEFAULT:
                    return True
        return not (found_main and found_sutui)
    except Exception:
        return True


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _ensure_lobster_sutui_local_models(config: dict) -> bool:
    changed = False
    if not isinstance(config, dict):
        return False

    models = config.setdefault("models", {})
    if not isinstance(models, dict):
        models = {}
        config["models"] = models
        changed = True
    if models.get("mode") != "merge":
        models["mode"] = "merge"
        changed = True
    providers = models.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        models["providers"] = providers
        changed = True
    provider = providers.setdefault(LOBSTER_SUTUI_PROVIDER_ID, {})
    if not isinstance(provider, dict):
        provider = {}
        providers[LOBSTER_SUTUI_PROVIDER_ID] = provider
        changed = True
    if provider.get("baseUrl") != LOBSTER_SUTUI_BASE_URL:
        provider["baseUrl"] = LOBSTER_SUTUI_BASE_URL
        changed = True
    if provider.get("api") != "openai-completions":
        provider["api"] = "openai-completions"
        changed = True
    env_data = _read_oc_env()
    proxy_key = env_data.get("OPENCLAW_SUTUI_PROXY_KEY", "").strip()
    if not proxy_key:
        proxy_key = (settings.openclaw_sutui_proxy_key or "").strip()
    if _apply_lobster_sutui_provider_config(provider, proxy_key):
        changed = True

    mcp = config.setdefault("mcp", {})
    if not isinstance(mcp, dict):
        mcp = {}
        config["mcp"] = mcp
        changed = True
    servers = mcp.setdefault("servers", {})
    if not isinstance(servers, dict):
        servers = {}
        mcp["servers"] = servers
        changed = True
    desired_mcp = {"url": "http://127.0.0.1:8000/mcp-gateway", "transport": "streamable-http"}
    if servers.get("lobster") != desired_mcp:
        servers["lobster"] = desired_mcp
        changed = True

    agents = config.setdefault("agents", {})
    if not isinstance(agents, dict):
        agents = {}
        config["agents"] = agents
        changed = True
    defaults = agents.setdefault("defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}
        agents["defaults"] = defaults
        changed = True
    model_cfg = defaults.setdefault("model", {})
    if not isinstance(model_cfg, dict):
        model_cfg = {}
        defaults["model"] = model_cfg
        changed = True
    if model_cfg.get("primary") != LOBSTER_SUTUI_CHAT_MODEL:
        model_cfg["primary"] = LOBSTER_SUTUI_CHAT_MODEL
        changed = True
    if defaults.get("thinkingDefault") != LOBSTER_SUTUI_THINKING_DEFAULT:
        defaults["thinkingDefault"] = LOBSTER_SUTUI_THINKING_DEFAULT
        changed = True
    if _safe_int(defaults.get("timeoutSeconds"), 0) < 600:
        defaults["timeoutSeconds"] = 600
        changed = True
    llm_cfg = defaults.setdefault("llm", {})
    if not isinstance(llm_cfg, dict):
        llm_cfg = {}
        defaults["llm"] = llm_cfg
        changed = True
    if _safe_int(llm_cfg.get("idleTimeoutSeconds"), 0) < 180:
        llm_cfg["idleTimeoutSeconds"] = 180
        changed = True

    agent_list = agents.setdefault("list", [])
    if not isinstance(agent_list, list):
        agent_list = []
        agents["list"] = agent_list
        changed = True

    def ensure_agent(agent_id: str, *, model: str | None = None, default: bool = False) -> None:
        nonlocal changed
        found = next((item for item in agent_list if isinstance(item, dict) and item.get("id") == agent_id), None)
        if not found:
            found = {"id": agent_id}
            agent_list.append(found)
            changed = True
        if default and found.get("default") is not True:
            found["default"] = True
            changed = True
        if model is not None and found.get("model") != model:
            found["model"] = model
            changed = True
        if agent_id in {"main", LOBSTER_SUTUI_AGENT_ID} and found.get("thinkingDefault") != LOBSTER_SUTUI_THINKING_DEFAULT:
            found["thinkingDefault"] = LOBSTER_SUTUI_THINKING_DEFAULT
            changed = True
        if agent_id in {"main", LOBSTER_SUTUI_AGENT_ID}:
            tools = found.get("tools")
            if not isinstance(tools, dict):
                found["tools"] = json.loads(json.dumps(LOBSTER_SUTUI_MAIN_AGENT_TOOLS))
                changed = True
            else:
                if tools.get("deny") != LOBSTER_SUTUI_MAIN_AGENT_TOOLS["deny"]:
                    tools["deny"] = list(LOBSTER_SUTUI_MAIN_AGENT_TOOLS["deny"])
                    changed = True
                exec_cfg = tools.get("exec")
                if not isinstance(exec_cfg, dict):
                    tools["exec"] = dict(LOBSTER_SUTUI_MAIN_AGENT_TOOLS["exec"])
                    changed = True
                else:
                    for key, value in LOBSTER_SUTUI_MAIN_AGENT_TOOLS["exec"].items():
                        if exec_cfg.get(key) != value:
                            exec_cfg[key] = value
                            changed = True

    ensure_agent("main", default=True)
    ensure_agent(LOBSTER_SUTUI_AGENT_ID, model=LOBSTER_SUTUI_CHAT_MODEL)
    return changed


def _ensure_lobster_sutui_agent_model_files() -> bool:
    changed = False
    env_data = _read_oc_env()
    proxy_key = env_data.get("OPENCLAW_SUTUI_PROXY_KEY", "").strip()
    if not proxy_key:
        proxy_key = (settings.openclaw_sutui_proxy_key or "").strip()
    agent_root = _OC_DIR / "agents"
    if not agent_root.is_dir():
        return False
    for path in agent_root.glob("*/agent/models.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Skipping invalid OpenClaw agent models file: %s", path)
            continue
        if not isinstance(data, dict):
            continue
        providers = data.setdefault("providers", {})
        if not isinstance(providers, dict):
            providers = {}
            data["providers"] = providers
        provider = providers.setdefault(LOBSTER_SUTUI_PROVIDER_ID, {})
        if not isinstance(provider, dict):
            provider = {}
            providers[LOBSTER_SUTUI_PROVIDER_ID] = provider
        before = json.dumps(data, ensure_ascii=False, sort_keys=True)
        _apply_lobster_sutui_provider_config(provider, proxy_key)
        after = json.dumps(data, ensure_ascii=False, sort_keys=True)
        if after != before:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            changed = True
    return changed


def _openclaw_agent_model_patch_needed() -> bool:
    agent_root = _OC_DIR / "agents"
    if not agent_root.is_dir():
        return False
    for path in agent_root.glob("*/agent/models.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return True
        if not isinstance(data, dict):
            return True
        providers = data.get("providers")
        if not isinstance(providers, dict):
            return True
        provider = providers.get(LOBSTER_SUTUI_PROVIDER_ID)
        if not isinstance(provider, dict):
            return True
        if provider.get("baseUrl") != LOBSTER_SUTUI_BASE_URL or provider.get("api") != "openai-completions":
            return True
        entries = provider.get("models")
        if not isinstance(entries, list):
            return True
        entry_ids = {item.get("id") for item in entries if isinstance(item, dict)}
        if not {LOBSTER_SUTUI_CHAT_MODEL_ID, LOBSTER_SUTUI_SKILL_MODEL_ID}.issubset(entry_ids):
            return True
    return False


def _openclaw_json_needs_local_launch_patch(plugin_mode: str = _OPENCLAW_PLUGIN_MODE_LEAN) -> bool:
    """Read-only check used while Gateway may already be running.

    Writing openclaw.json while OpenClaw is alive can trigger its own config
    watcher and an in-process restart. On Windows that restart can stall for
    minutes, so callers must use this read-only probe and then restart kill-first.
    """
    if plugin_mode != _OPENCLAW_PLUGIN_MODE_LEAN:
        return False
    try:
        if not _OC_CONFIG.exists():
            return True
        cfg = _read_oc_config()
        if not isinstance(cfg, dict) or not cfg:
            return True
        if _openclaw_local_model_patch_needed(cfg):
            return True
        if _openclaw_agent_model_patch_needed():
            return True
        lean_plugins = {"enabled": False, "entries": {}, "installs": {}, "load": {"paths": []}}
        plugins = cfg.get("plugins")
        if plugins != lean_plugins:
            return True
        if isinstance(plugins, dict):
            load = plugins.get("load")
            if isinstance(load, dict):
                paths = load.get("paths")
                if isinstance(paths, list) and any(_is_openclaw_install_stage_path(p) for p in paths):
                    return True
            entries = plugins.get("entries")
            if isinstance(entries, dict):
                for plugin_cfg in entries.values():
                    if not isinstance(plugin_cfg, dict):
                        continue
                    for key in ("path", "source", "sourcePath", "installPath"):
                        if _is_openclaw_install_stage_path(plugin_cfg.get(key)):
                            return True
            installs = plugins.get("installs")
            if isinstance(installs, dict):
                for install_cfg in installs.values():
                    if not isinstance(install_cfg, dict):
                        continue
                    for key in ("sourcePath", "installPath"):
                        if _is_openclaw_install_stage_path(install_cfg.get(key)):
                            return True
        channels = cfg.get("channels")
        if not isinstance(channels, dict):
            return True
        if _OPENCLAW_WEIXIN_PLUGIN_ID in channels:
            return True
        slack_cfg = channels.get("slack")
        if not isinstance(slack_cfg, dict) or slack_cfg.get("enabled") is not False:
            return True
        gateway = cfg.get("gateway")
        if not isinstance(gateway, dict):
            return True
        if gateway.get("mode") != "local":
            return True
        http_cfg = gateway.get("http")
        if not isinstance(http_cfg, dict):
            return True
        endpoints = http_cfg.get("endpoints")
        if not isinstance(endpoints, dict):
            return True
        chat_endpoint = endpoints.get("chatCompletions")
        if not isinstance(chat_endpoint, dict) or chat_endpoint.get("enabled") is not True:
            return True
        reload_cfg = gateway.get("reload")
        if not isinstance(reload_cfg, dict) or reload_cfg.get("mode") != "off":
            return True
        discovery = cfg.get("discovery")
        if not isinstance(discovery, dict):
            return True
        mdns_cfg = discovery.get("mdns")
        if not isinstance(mdns_cfg, dict) or mdns_cfg.get("mode") != "off":
            return True
        return False
    except Exception as e:
        logger.warning("openclaw_json_needs_local_launch_patch failed: %s", e)
        return False


def _openclaw_gateway_local_restart_reasons() -> list[str]:
    """Return concrete local drift reasons that require a Gateway restart."""
    reasons: list[str] = []
    checks = (
        ("openclaw_json", _openclaw_json_needs_local_launch_patch),
        ("slack_stage_patch", _openclaw_gateway_slack_stage_patch_needed),
        ("pricing_refresh_patch", _openclaw_gateway_pricing_patch_needed),
        ("mcp_tool_timeout_patch", _openclaw_mcp_tool_timeout_patch_needed),
        ("mcp_sdk_timeout_patch", _openclaw_mcp_sdk_timeout_patch_needed),
        ("gateway_startup_preflight_patch", _openclaw_gateway_startup_preflight_patch_needed),
        ("latency_fast_path_patch", _openclaw_latency_fast_path_patch_needed),
    )
    for name, check in checks:
        try:
            if check():
                reasons.append(name)
        except Exception as e:
            logger.warning("OpenClaw restart reason check failed: reason=%s error=%s", name, e)
    return reasons


def _openclaw_gateway_needs_local_restart() -> bool:
    """Whether the local Gateway should be restarted before chat/status use."""
    return bool(_openclaw_gateway_local_restart_reasons())


def _ensure_openclaw_json_for_local_launch(plugin_mode: str = _OPENCLAW_PLUGIN_MODE_LEAN) -> bool:
    """在拉起 OpenClaw 子进程前修正磁盘上的 openclaw.json。

    - OpenClaw 将 plugins.load.paths 里的相对路径按 process.cwd() 解析；若 cwd 非项目根会找不到插件。
      此处把已存在于项目根下的相对路径写成绝对路径。
    - 将 lobster-sutui 的 apiKey 写成与 openclaw/.env、后端 settings 一致的明文，避免 ${OPENCLAW_SUTUI_PROXY_KEY}
      在校验阶段尚未注入 process 时报警。
    """
    try:
        cfg = _read_oc_config()
        changed = False
        if not isinstance(cfg, dict) or not cfg:
            cfg = _new_openclaw_local_launch_config()
            changed = True
            logger.info("Recreated missing or invalid openclaw.json for local OpenClaw launch")
        plugins = cfg.get("plugins")
        if isinstance(plugins, dict):
            stable_weixin = _OC_DIR / "extensions" / "openclaw-weixin"
            stable_weixin_index = stable_weixin / "index.ts"
            load = plugins.get("load")
            if isinstance(load, dict):
                paths = load.get("paths")
                if isinstance(paths, list):
                    new_paths: list[Any] = []
                    for raw in paths:
                        if not isinstance(raw, str) or not raw.strip():
                            new_paths.append(raw)
                            continue
                        p = raw.strip()
                        if _is_openclaw_install_stage_path(p):
                            if stable_weixin.exists():
                                new_paths.append(str(stable_weixin))
                            changed = True
                            continue
                        r = Path(p)
                        if r.is_absolute():
                            new_paths.append(p)
                            continue
                        candidate = (_BASE_DIR / p).resolve()
                        if candidate.is_dir() or candidate.is_file():
                            new_paths.append(str(candidate))
                            if str(candidate) != p:
                                changed = True
                        else:
                            new_paths.append(p)
                    load["paths"] = new_paths
            entries = plugins.get("entries")
            if isinstance(entries, dict):
                for plugin_id, plugin_cfg in list(entries.items()):
                    if not isinstance(plugin_cfg, dict):
                        continue
                    for key in ("path", "source", "sourcePath", "installPath"):
                        val = plugin_cfg.get(key)
                        if not _is_openclaw_install_stage_path(val):
                            continue
                        if plugin_id == "openclaw-weixin" and stable_weixin.exists():
                            plugin_cfg[key] = str(stable_weixin if key == "installPath" else (stable_weixin_index if stable_weixin_index.exists() else stable_weixin))
                        else:
                            plugin_cfg.pop(key, None)
                        changed = True
            installs = plugins.get("installs")
            if isinstance(installs, dict):
                for plugin_id, install_cfg in list(installs.items()):
                    if not isinstance(install_cfg, dict):
                        continue
                    for key in ("sourcePath", "installPath"):
                        val = install_cfg.get(key)
                        if not _is_openclaw_install_stage_path(val):
                            continue
                        if plugin_id == "openclaw-weixin" and stable_weixin.exists():
                            install_cfg[key] = str(stable_weixin if key == "installPath" else (stable_weixin_index if stable_weixin_index.exists() else stable_weixin))
                        else:
                            install_cfg.pop(key, None)
                        changed = True
        if _set_openclaw_plugin_launch_mode(cfg, plugin_mode):
            changed = True
        gateway = cfg.get("gateway")
        if not isinstance(gateway, dict):
            gateway = {}
            cfg["gateway"] = gateway
            changed = True
        if gateway.get("mode") != "local":
            gateway["mode"] = "local"
            changed = True
        http_cfg = gateway.setdefault("http", {})
        if not isinstance(http_cfg, dict):
            http_cfg = {}
            gateway["http"] = http_cfg
            changed = True
        endpoints = http_cfg.setdefault("endpoints", {})
        if not isinstance(endpoints, dict):
            endpoints = {}
            http_cfg["endpoints"] = endpoints
            changed = True
        chat_endpoint = endpoints.setdefault("chatCompletions", {})
        if not isinstance(chat_endpoint, dict):
            chat_endpoint = {}
            endpoints["chatCompletions"] = chat_endpoint
            changed = True
        if chat_endpoint.get("enabled") is not True:
            chat_endpoint["enabled"] = True
            changed = True
        reload_cfg = gateway.setdefault("reload", {})
        if not isinstance(reload_cfg, dict):
            reload_cfg = {}
            gateway["reload"] = reload_cfg
            changed = True
        if reload_cfg.get("mode") != "off":
            reload_cfg["mode"] = "off"
            changed = True
        auth = gateway.setdefault("auth", {})
        if not isinstance(auth, dict):
            auth = {}
            gateway["auth"] = auth
            changed = True
        env_gateway_token = _configured_gateway_token(cfg)
        cfg_gateway_token = str(auth.get("token") or "").strip()
        if (
            env_gateway_token
            and env_gateway_token != _OPENCLAW_GATEWAY_TOKEN_PLACEHOLDER
            and cfg_gateway_token != env_gateway_token
        ):
            auth["token"] = env_gateway_token
            changed = True
        if env_gateway_token and auth.get("mode") != "token":
            auth["mode"] = "token"
            changed = True
        discovery = cfg.get("discovery")
        if not isinstance(discovery, dict):
            discovery = {}
            cfg["discovery"] = discovery
            changed = True
        mdns_cfg = discovery.setdefault("mdns", {})
        if not isinstance(mdns_cfg, dict):
            mdns_cfg = {}
            discovery["mdns"] = mdns_cfg
            changed = True
        if mdns_cfg.get("mode") != "off":
            mdns_cfg["mode"] = "off"
            changed = True
        env_data = _read_oc_env()
        if _ensure_lobster_sutui_local_models(cfg):
            changed = True
        if _ensure_lobster_sutui_agent_model_files():
            changed = True
        models = cfg.get("models")
        if isinstance(models, dict):
            provs = models.get("providers")
            if isinstance(provs, dict):
                ls = provs.get("lobster-sutui")
                if isinstance(ls, dict):
                    proxy_key = env_data.get("OPENCLAW_SUTUI_PROXY_KEY", "").strip()
                    if not proxy_key:
                        proxy_key = (settings.openclaw_sutui_proxy_key or "").strip()
                    if proxy_key and ls.get("apiKey") != proxy_key:
                        ls["apiKey"] = proxy_key
                        changed = True
        if changed:
            _write_oc_config(cfg)
            logger.info(
                "Patched openclaw.json for local OpenClaw launch "
                "(plugin mode=%s / plugin paths / staging refs / lobster-sutui provider)",
                plugin_mode,
            )
        return changed
    except Exception as e:
        logger.warning("ensure_openclaw_json_for_local_launch failed: %s", e)
        return False


def _model_to_agent_id(model: str) -> str:
    """Slugify a model ID into an OpenClaw agent ID."""
    slug = model.lower().replace("/", "-").replace(".", "-")
    slug = re.sub(r'[^a-z0-9_-]', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug[:64] or "main"


def _build_agents_list(primary_model: str) -> list[dict]:
    """Build the agents.list array from SUPPORTED_PROVIDERS.

    The default agent ('main') uses the primary model.
    Every supported model also gets a dedicated agent so switching the primary
    later never leaves a model without an agent entry.
    """
    agents = [{"id": "main", "default": True}]
    seen: set[str] = set()
    for prov in SUPPORTED_PROVIDERS:
        for model_id in prov["models"]:
            if model_id in seen:
                continue
            seen.add(model_id)
            agents.append({"id": _model_to_agent_id(model_id), "model": model_id})
    return agents


def _ensure_provider_configs(config: dict):
    """Dynamically add/remove non-built-in providers based on actual API key values.

    Uses the real key value in openclaw.json (not ${ENV_VAR} templates) to avoid
    OpenClaw SecretRef startup failures when keys are empty.
    """
    env_data = _read_oc_env()
    providers = config.setdefault("models", {}).setdefault("providers", {})

    ds_key = env_data.get("DEEPSEEK_API_KEY", "").strip()
    if ds_key:
        ds_cfg = dict(DEEPSEEK_PROVIDER_TEMPLATE)
        ds_cfg["apiKey"] = ds_key
        providers["deepseek"] = ds_cfg
    else:
        providers.pop("deepseek", None)

    if not providers:
        config.get("models", {}).pop("providers", None)
        if not config.get("models"):
            config.pop("models", None)

    proxy_key = env_data.get("OPENCLAW_SUTUI_PROXY_KEY", "").strip()
    if not proxy_key:
        proxy_key = (settings.openclaw_sutui_proxy_key or "").strip()
    ls = providers.get("lobster-sutui")
    if proxy_key and isinstance(ls, dict):
        ls["apiKey"] = proxy_key


def _ensure_agents_list(config: dict):
    """Ensure agents.list contains an agent for every supported model."""
    agents_node = config.setdefault("agents", {})
    primary = agents_node.get("defaults", {}).get("model", {}).get("primary", _DEFAULT_PRIMARY)
    agents_node["list"] = _build_agents_list(primary)


_DEFAULT_PRIMARY = "anthropic/claude-sonnet-4-5"


def build_openclaw_status_snapshot() -> dict:
    """Return the local Gateway launch state for UI and diagnostics.

    `online` intentionally means "the Gateway port is listening".  Config or
    patch drift is returned separately so the UI does not look stuck in
    "starting" while a usable Gateway process already exists.
    """
    listener_pids = _find_listener_pids_on_18789()
    gateway_pids = _find_openclaw_gateway_process_pids()
    restart_reasons = _openclaw_gateway_local_restart_reasons()
    needs_restart = bool(restart_reasons)
    entry = _find_openclaw_entry()
    online = bool(listener_pids)
    if online and needs_restart:
        state = "running_needs_sync"
        message = "OpenClaw Gateway 已启动，配置待同步重启"
    elif online:
        state = "running"
        message = "OpenClaw Gateway 运行中"
    elif gateway_pids:
        state = "starting"
        message = "OpenClaw Gateway 启动中"
    elif entry:
        state = "stopped"
        message = "OpenClaw Gateway 未运行"
    else:
        state = "missing_entry"
        message = "未找到 node 或 openclaw.mjs，请使用完整安装包或检查 nodejs 目录"
    return {
        "online": online,
        "status_code": 200 if online else None,
        "listener_online": online,
        "listener_pids": listener_pids,
        "gateway_pids": gateway_pids,
        "gateway_processes": _find_openclaw_gateway_process_infos(),
        "config_synced": not needs_restart,
        "needs_restart": bool(needs_restart),
        "restart_reasons": restart_reasons,
        "entry_found": bool(entry),
        "state": state,
        "message": message,
        "last_startup": _openclaw_last_startup_diag_snapshot(),
    }


@router.get("/api/openclaw/status", summary="OpenClaw Gateway 状态")
async def openclaw_status(current_user: _ServerUser = Depends(get_current_user_for_local)):
    return await asyncio.to_thread(build_openclaw_status_snapshot)


@router.get("/api/openclaw/config", summary="读取 OpenClaw 配置")
def get_openclaw_config(current_user: _ServerUser = Depends(get_current_user_for_local)):
    env_data = _read_oc_env()
    config = _read_oc_config()

    primary_model = ""
    try:
        primary_model = (
            config.get("agents", {}).get("defaults", {}).get("model", {}).get("primary", "")
            or config.get("agent", {}).get("model", {}).get("primary", "")
        )
    except Exception:
        pass

    providers_status = []
    for p in SUPPORTED_PROVIDERS:
        raw_key = env_data.get(p["env_key"], "")
        providers_status.append({
            "id": p["id"],
            "name": p["name"],
            "env_key": p["env_key"],
            "configured": bool(raw_key),
            "masked_key": _mask_key(raw_key),
            "models": p["models"],
        })

    return {
        "primary_model": primary_model,
        "providers": providers_status,
    }


class UpdateOpenClawConfig(BaseModel):
    primary_model: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None


@router.post("/api/openclaw/config", summary="更新 OpenClaw 配置（本地）")
def update_openclaw_config(
    body: UpdateOpenClawConfig,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    env_data = _read_oc_env()
    changed_keys = False

    key_map = {
        "ANTHROPIC_API_KEY": body.anthropic_api_key,
        "OPENAI_API_KEY": body.openai_api_key,
        "DEEPSEEK_API_KEY": body.deepseek_api_key,
        "GEMINI_API_KEY": body.gemini_api_key,
    }
    for env_key, value in key_map.items():
        if value is not None:
            env_data[env_key] = value.strip()
            changed_keys = True

    if changed_keys:
        _write_oc_env(env_data)

    config = _read_oc_config()

    if body.primary_model is not None:
        config.setdefault("agents", {}).setdefault("defaults", {}).setdefault("model", {})["primary"] = body.primary_model.strip()

    _ensure_provider_configs(config)
    _ensure_agents_list(config)
    _write_oc_config(config)

    restarted = False
    if changed_keys:
        restarted = _restart_openclaw_gateway()

    msg = "配置已保存"
    if restarted:
        msg += "，OpenClaw Gateway 已自动重启。"
    elif changed_keys:
        msg += "。API Key 已更新，但自动重启失败，请手动重启（stop.bat + start.bat）。"
    else:
        msg += "。"

    return {"ok": True, "message": msg, "restarted": restarted}


def _find_listener_pids_on_18789() -> list[int]:
    """在 18789 上 **LISTEN** 的进程 PID（不含连到该端口的客户端）。可能多个（异常残留时）。"""
    try:
        if platform.system() == "Windows":
            out = subprocess.check_output(
                'netstat -ano | findstr ":18789 " | findstr "LISTENING"',
                shell=True, text=True, stderr=subprocess.DEVNULL,
            )
            pids: set[int] = set()
            for line in out.strip().splitlines():
                parts = line.split()
                if parts:
                    try:
                        pids.add(int(parts[-1]))
                    except ValueError:
                        continue
            return sorted(pids)
        try:
            out = subprocess.check_output(
                ["lsof", "-nP", "-iTCP:18789", "-sTCP:LISTEN", "-t"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            return []
        lines = [x.strip() for x in out.strip().splitlines() if x.strip().isdigit()]
        return sorted({int(x) for x in lines})
    except Exception:
        return []


def _find_openclaw_pid() -> Optional[int]:
    """兼容：返回 18789 上第一个监听 PID（若无则 None）。"""
    pids = _find_listener_pids_on_18789()
    return pids[0] if pids else None


_PROCESS_SECRET_RE = re.compile(
    r"(?i)(--(?:token|gateway-token|password|gateway-password)\s+)(\S+)|"
    r"((?:TOKEN|KEY|SECRET|PASSWORD|PASSWD)=)([^\s]+)"
)


def _safe_process_command_line(value: Any, max_len: int = 1600) -> str:
    text = str(value or "")
    text = _PROCESS_SECRET_RE.sub(lambda m: (m.group(1) or m.group(3) or "") + "<redacted>", text)
    if len(text) > max_len:
        return text[:max_len] + "...<truncated>"
    return text


def _find_openclaw_gateway_process_infos() -> list[dict[str, Any]]:
    """Return process snapshots for `openclaw.mjs gateway --port 18789`."""
    try:
        if platform.system() == "Windows":
            script = (
                "$items = Get-CimInstance Win32_Process | "
                "Where-Object { "
                "$_.Name -match '^node(\\.exe)?$' -and "
                "$_.CommandLine -match 'openclaw\\.mjs' -and "
                "$_.CommandLine -match '\\bgateway\\b' -and "
                "$_.CommandLine -match '18789' "
                "} | Select-Object "
                "@{Name='pid';Expression={$_.ProcessId}},"
                "@{Name='ppid';Expression={$_.ParentProcessId}},"
                "@{Name='name';Expression={$_.Name}},"
                "@{Name='command_line';Expression={$_.CommandLine}}; "
                "$items | ConvertTo-Json -Compress"
            )
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", script],
                text=True,
                errors="replace",
                stderr=subprocess.DEVNULL,
            )
            raw = out.strip()
            if not raw:
                return []
            parsed = json.loads(raw)
            rows = parsed if isinstance(parsed, list) else [parsed]
            infos: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    pid = int(row.get("pid") or 0)
                except (TypeError, ValueError):
                    continue
                if pid <= 0:
                    continue
                try:
                    ppid: Optional[int] = int(row.get("ppid") or 0)
                except (TypeError, ValueError):
                    ppid = None
                infos.append(
                    {
                        "pid": pid,
                        "ppid": ppid,
                        "name": str(row.get("name") or ""),
                        "command_line": _safe_process_command_line(row.get("command_line")),
                    }
                )
            return sorted(infos, key=lambda x: int(x.get("pid") or 0))
        out = subprocess.check_output(
            ["ps", "-eo", "pid=,ppid=,comm=,args="],
            text=True,
            errors="replace",
            stderr=subprocess.DEVNULL,
        )
        infos: list[dict[str, Any]] = []
        for line in out.splitlines():
            parts = line.strip().split(maxsplit=3)
            if len(parts) < 4 or not parts[0].isdigit():
                continue
            cmdline = parts[3]
            if "openclaw.mjs" in cmdline and "gateway" in cmdline and "18789" in cmdline:
                infos.append(
                    {
                        "pid": int(parts[0]),
                        "ppid": int(parts[1]) if parts[1].isdigit() else None,
                        "name": parts[2],
                        "command_line": _safe_process_command_line(cmdline),
                    }
                )
        return sorted(infos, key=lambda x: int(x.get("pid") or 0))
    except Exception:
        return []


def _find_openclaw_gateway_process_pids() -> list[int]:
    return [int(info["pid"]) for info in _find_openclaw_gateway_process_infos() if info.get("pid")]


def _wait_until_no_openclaw_gateway_processes(max_wait: float = 6.0) -> None:
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if not _find_listener_pids_on_18789() and not _find_openclaw_gateway_process_pids():
            return
        time.sleep(0.2)


def _read_log_tail_for_warning(path: Path, max_bytes: int = 12000) -> str:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(max(0, size - max_bytes))
            data = f.read(max_bytes)
        text = data.decode("utf-8", errors="replace")
        if size > max_bytes:
            return f"[tail only: last {max_bytes} bytes of {size}]\n{text}"
        return text
    except Exception as exc:
        return f"<cannot read {path}: {exc}>"


_SENSITIVE_ENV_RE = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|PASSWD|COOKIE|AUTHORIZATION)", re.IGNORECASE)


def _safe_startup_env_summary(env: dict) -> dict:
    names = [
        "OPENCLAW_CONFIG_PATH",
        "OPENCLAW_STATE_DIR",
        "OPENCLAW_DISABLE_BONJOUR",
        "OPENCLAW_NO_RESPAWN",
        "OPENCLAW_DEBUG_INGRESS_TIMING",
        "NODE_DISABLE_COMPILE_CACHE",
        "NODE_COMPILE_CACHE",
        "LOBSTER_OPENCLAW_FAST_THINKING_OFF",
        "LOBSTER_OPENCLAW_SKIP_SKILLS_SNAPSHOT",
        "LOBSTER_OPENCLAW_DISABLE_SLACK_STAGE",
        "LOBSTER_OPENCLAW_DISABLE_MODEL_PRICING",
        "LOBSTER_OPENCLAW_MCP_TOOL_TIMEOUT_MS",
        "LOBSTER_OPENCLAW_MCP_SDK_TIMEOUT_MS",
        "OPENCLAW_GATEWAY_URL",
        "OPENCLAW_GATEWAY_TOKEN",
        "OPENCLAW_SUTUI_PROXY_KEY",
        "PATH",
    ]
    summary: dict[str, str] = {}
    for name in names:
        raw = str(env.get(name) or "")
        if _SENSITIVE_ENV_RE.search(name):
            summary[name] = "<set>" if raw else ""
        elif name == "PATH":
            summary[name] = raw[:500]
        else:
            summary[name] = raw[:1000]
    return summary


def _startup_diag_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_openclaw_startup_log_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return _OPENCLAW_STARTUP_LOG_DIR / f"openclaw-startup-{stamp}-{suffix}.log"


def _write_openclaw_startup_diag_line(path: Optional[Path], event: str, **fields: Any) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": _startup_diag_now(),
            "event": event,
            **fields,
        }
        with path.open("a", encoding="utf-8", errors="replace") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        logger.warning("Failed writing OpenClaw startup diagnostic %s: %s", path, exc)


def _remember_openclaw_startup_diag(**fields: Any) -> None:
    with _OPENCLAW_STARTUP_DIAG_LOCK:
        _OPENCLAW_LAST_STARTUP_DIAG.clear()
        _OPENCLAW_LAST_STARTUP_DIAG.update(
            {
                "updated_at": _startup_diag_now(),
                **fields,
            }
        )


def _openclaw_last_startup_diag_snapshot() -> dict:
    with _OPENCLAW_STARTUP_DIAG_LOCK:
        return dict(_OPENCLAW_LAST_STARTUP_DIAG)


def _openclaw_startup_log_candidates(limit: int = 4) -> list[Path]:
    try:
        if not _OPENCLAW_STARTUP_LOG_DIR.is_dir():
            return []
        return sorted(
            [p for p in _OPENCLAW_STARTUP_LOG_DIR.glob("openclaw-startup-*.log") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]
    except OSError:
        return []


def _openclaw_log_candidates(limit: int = 4) -> list[Path]:
    paths: list[Path] = []
    root_log = _BASE_DIR / "openclaw.log"
    if root_log.is_file():
        paths.append(root_log)
    paths.extend(_openclaw_startup_log_candidates(limit))
    temp_dir = Path(tempfile.gettempdir()) / "openclaw"
    if temp_dir.is_dir():
        try:
            temp_logs = sorted(
                [p for p in temp_dir.glob("openclaw-*.log") if p.is_file()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            paths.extend(temp_logs[:limit])
        except OSError:
            pass
    seen: set[Path] = set()
    result: list[Path] = []
    for p in paths:
        try:
            key = p.resolve()
        except OSError:
            key = p
        if key in seen:
            continue
        seen.add(key)
        result.append(p)
        if len(result) >= limit:
            break
    return result


def _openclaw_package_version(mjs_path: str) -> str:
    pkg = Path(mjs_path).parent / "package.json"
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
        return str(data.get("version") or "")
    except Exception:
        return ""


def _openclaw_gateway_slack_stage_patch_needed() -> bool:
    try:
        entry = _find_openclaw_entry()
        if not entry:
            return False
        _node_path, mjs_path = entry
        dist_dir = Path(mjs_path).resolve().parent / "dist"
        if not dist_dir.is_dir():
            return False
        for path in sorted(dist_dir.glob("gateway-cli-*.js")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "LOBSTER_OPENCLAW_DISABLE_SLACK_STAGE" in text:
                continue
            if 'name: "slack"' in text and "handleSlackHttpRequest(req, res)" in text:
                return True
        return False
    except Exception as e:
        logger.warning("openclaw_gateway_slack_stage_patch_needed failed: %s", e)
        return False


def _openclaw_gateway_pricing_patch_needed() -> bool:
    try:
        entry = _find_openclaw_entry()
        if not entry:
            return False
        _node_path, mjs_path = entry
        dist_dir = Path(mjs_path).resolve().parent / "dist"
        if not dist_dir.is_dir():
            return False
        for path in sorted(dist_dir.glob("usage-format-*.js")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "LOBSTER_OPENCLAW_DISABLE_MODEL_PRICING" in text:
                continue
            if "function startGatewayModelPricingRefresh(params)" in text:
                return True
        return False
    except Exception as e:
        logger.warning("openclaw_gateway_pricing_patch_needed failed: %s", e)
        return False


def _openclaw_mcp_tool_timeout_patch_needed() -> bool:
    try:
        entry = _find_openclaw_entry()
        if not entry:
            return False
        _node_path, mjs_path = entry
        dist_dir = Path(mjs_path).resolve().parent / "dist"
        if not dist_dir.is_dir():
            return False
        for path in sorted(dist_dir.glob("content-blocks-*.js")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "LOBSTER_OPENCLAW_MCP_TOOL_TIMEOUT_MS" in text:
                continue
            if _openclaw_mcp_tool_timeout_patchable(text):
                return True
        return False
    except Exception as e:
        logger.warning("openclaw_mcp_tool_timeout_patch_needed failed: %s", e)
        return False


def _openclaw_mcp_sdk_timeout_patch_needed() -> bool:
    try:
        entry = _find_openclaw_entry()
        if not entry:
            return False
        _node_path, mjs_path = entry
        for path in _openclaw_mcp_sdk_protocol_paths(mjs_path):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "LOBSTER_OPENCLAW_MCP_SDK_TIMEOUT_MS" in text:
                continue
            if _openclaw_mcp_sdk_timeout_patchable(text):
                return True
        return False
    except Exception as e:
        logger.warning("openclaw_mcp_sdk_timeout_patch_needed failed: %s", e)
        return False


def _openclaw_latency_fast_path_patch_needed() -> bool:
    try:
        entry = _find_openclaw_entry()
        if not entry:
            return False
        _node_path, mjs_path = entry
        dist_dir = Path(mjs_path).resolve().parent / "dist"
        if not dist_dir.is_dir():
            return False
        for path in sorted(dist_dir.glob("agent-command-*.js")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "LOBSTER_OPENCLAW_LATENCY_TRACE_V3" not in text:
                return True
        return False
    except Exception as e:
        logger.warning("openclaw_latency_fast_path_patch_needed failed: %s", e)
        return False


def _openclaw_gateway_startup_preflight_patch_needed() -> bool:
    try:
        entry = _find_openclaw_entry()
        if not entry:
            return False
        _node_path, mjs_path = entry
        dist_dir = Path(mjs_path).resolve().parent / "dist"
        if not dist_dir.is_dir():
            return False
        for path in sorted(dist_dir.glob("logger-*.js")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "LOBSTER_OPENCLAW_SKIP_GATEWAY_DOCTOR_PREFLIGHT" in text:
                continue
            if 'if (primary === "agent") return false;' in text and "function shouldMigrateStateFromPath" in text:
                return True
        return False
    except Exception as e:
        logger.warning("openclaw_gateway_startup_preflight_patch_needed failed: %s", e)
        return False


def _openclaw_mcp_tool_timeout_patchable(text: str) -> bool:
    return _OPENCLAW_MCP_TOOL_TIMEOUT_CALL_RE.search(text or "") is not None


_OPENCLAW_MCP_TOOL_TIMEOUT_CALL_RE = re.compile(
    r"(?P<indent>[ \t]*)return\s+await\s+session\.client\.callTool\(\{\s*"
    r"name:\s*toolName\s*,\s*"
    r"arguments:\s*isMcpConfigRecord\(input\)\s*\?\s*input\s*:\s*\{\}\s*"
    r"\}\s*\)\s*;",
    re.DOTALL,
)


def _apply_openclaw_mcp_tool_timeout_patch(text: str) -> tuple[str, bool]:
    def repl(match: re.Match[str]) -> str:
        indent = match.group("indent") or ""
        inner = indent + "\t"
        return (
            f"{indent}const lobsterToolTimeoutMsRaw = Number(process.env.LOBSTER_OPENCLAW_MCP_TOOL_TIMEOUT_MS ?? 600000);\n"
            f"{indent}const lobsterToolTimeoutMs = Number.isFinite(lobsterToolTimeoutMsRaw) && lobsterToolTimeoutMsRaw > 0 ? Math.floor(lobsterToolTimeoutMsRaw) : 600000;\n"
            f"{indent}return await session.client.callTool({{\n"
            f"{inner}name: toolName,\n"
            f"{inner}arguments: isMcpConfigRecord(input) ? input : {{}}\n"
            f"{indent}}}, void 0, {{ timeout: lobsterToolTimeoutMs }});"
        )

    new_text, count = _OPENCLAW_MCP_TOOL_TIMEOUT_CALL_RE.subn(repl, text or "", count=1)
    return new_text, bool(count)


_OPENCLAW_MCP_SDK_TIMEOUT_ESM_RE = re.compile(
    r"export\s+const\s+DEFAULT_REQUEST_TIMEOUT_MSEC\s*=\s*60000\s*;"
)
_OPENCLAW_MCP_SDK_TIMEOUT_CJS_RE = re.compile(
    r"exports\.DEFAULT_REQUEST_TIMEOUT_MSEC\s*=\s*60000\s*;"
)


def _openclaw_mcp_sdk_protocol_paths(mjs_path: str) -> list[Path]:
    openclaw_dir = Path(mjs_path).resolve().parent
    sdk_dirs = [
        openclaw_dir.parent / "@modelcontextprotocol" / "sdk" / "dist",
        openclaw_dir / "node_modules" / "@modelcontextprotocol" / "sdk" / "dist",
    ]
    paths: list[Path] = []
    seen: set[Path] = set()
    for sdk_dir in sdk_dirs:
        for candidate in (
            sdk_dir / "esm" / "shared" / "protocol.js",
            sdk_dir / "cjs" / "shared" / "protocol.js",
        ):
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(candidate)
    return paths


def _openclaw_mcp_sdk_timeout_patchable(text: str) -> bool:
    raw = text or ""
    return bool(
        _OPENCLAW_MCP_SDK_TIMEOUT_ESM_RE.search(raw)
        or _OPENCLAW_MCP_SDK_TIMEOUT_CJS_RE.search(raw)
    )


def _apply_openclaw_mcp_sdk_timeout_patch(text: str) -> tuple[str, bool]:
    raw = text or ""
    timeout_expr = (
        "(() => {\n"
        "\tconst raw = Number(process.env.LOBSTER_OPENCLAW_MCP_SDK_TIMEOUT_MS ?? process.env.LOBSTER_OPENCLAW_MCP_TOOL_TIMEOUT_MS ?? 600000);\n"
        "\treturn Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : 600000;\n"
        "})()"
    )
    new_text, count = _OPENCLAW_MCP_SDK_TIMEOUT_ESM_RE.subn(
        f"export const DEFAULT_REQUEST_TIMEOUT_MSEC = {timeout_expr};",
        raw,
        count=1,
    )
    if count:
        return new_text, True
    new_text, count = _OPENCLAW_MCP_SDK_TIMEOUT_CJS_RE.subn(
        f"exports.DEFAULT_REQUEST_TIMEOUT_MSEC = {timeout_expr};",
        raw,
        count=1,
    )
    return new_text, bool(count)


def _patch_openclaw_gateway_slack_stage(mjs_path: str) -> bool:
    """Disable OpenClaw's built-in Slack HTTP stage when local product does not use it.

    OpenClaw 2026.4.1 registers the Slack stage unconditionally. If
    @slack/web-api is not bundled, every Gateway request can throw before the
    normal chat/model stages. This tiny runtime patch keeps the bundled version
    fixed while allowing normal startup on machines without Slack dependencies.
    """
    marker = "LOBSTER_OPENCLAW_DISABLE_SLACK_STAGE"
    try:
        dist_dir = Path(mjs_path).resolve().parent / "dist"
        if not dist_dir.is_dir():
            return False
        candidates = sorted(dist_dir.glob("gateway-cli-*.js"))
        changed = False
        old = (
            '\t\t\t\t{\n'
            '\t\t\t\t\tname: "slack",\n'
            '\t\t\t\t\trun: () => handleSlackHttpRequest(req, res)\n'
            '\t\t\t\t}\n'
        )
        new = (
            '\t\t\t\t{\n'
            '\t\t\t\t\tname: "slack",\n'
            '\t\t\t\t\trun: () => {\n'
            f'\t\t\t\t\t\tif (String(process.env.{marker} ?? "").trim() === "1") return false;\n'
            '\t\t\t\t\t\tif (configSnapshot.channels?.slack?.enabled === false) return false;\n'
            '\t\t\t\t\t\treturn handleSlackHttpRequest(req, res);\n'
            '\t\t\t\t\t}\n'
            '\t\t\t\t}\n'
        )
        for path in candidates:
            text = path.read_text(encoding="utf-8")
            if marker in text:
                continue
            if old not in text:
                logger.warning("OpenClaw Slack stage patch skipped, pattern not found: %s", path)
                continue
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
            logger.info("Patched OpenClaw Gateway Slack stage guard: %s", path)
            changed = True
        return changed
    except Exception as e:
        logger.warning("patch_openclaw_gateway_slack_stage failed: %s", e)
        return False


def _patch_openclaw_gateway_pricing_refresh(mjs_path: str) -> bool:
    """Allow local launch to skip OpenRouter pricing bootstrap."""
    marker = "LOBSTER_OPENCLAW_DISABLE_MODEL_PRICING"
    try:
        dist_dir = Path(mjs_path).resolve().parent / "dist"
        if not dist_dir.is_dir():
            return False
        candidates = sorted(dist_dir.glob("usage-format-*.js"))
        changed = False
        old = "function startGatewayModelPricingRefresh(params) {\n\trefreshGatewayModelPricingCache(params).catch((error) => {"
        new = (
            "function startGatewayModelPricingRefresh(params) {\n"
            f"\tif (String(process.env.{marker} ?? \"\").trim() === \"1\") return () => {{ clearRefreshTimer(); }};\n"
            "\trefreshGatewayModelPricingCache(params).catch((error) => {"
        )
        for path in candidates:
            text = path.read_text(encoding="utf-8")
            if marker in text:
                continue
            if old not in text:
                logger.warning("OpenClaw pricing refresh patch skipped, pattern not found: %s", path)
                continue
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
            logger.info("Patched OpenClaw model pricing refresh guard: %s", path)
            changed = True
        return changed
    except Exception as e:
        logger.warning("patch_openclaw_gateway_pricing_refresh failed: %s", e)
        return False


def _patch_openclaw_mcp_tool_timeout(mjs_path: str) -> bool:
    """Raise OpenClaw MCP tool-call wait time above the SDK default 60s.

    The MCP SDK default request timeout is 60000ms. Image/video generation can
    legitimately take longer than that, and timing out at the OpenClaw tool
    layer can make the model retry while the upstream task is still running.
    """
    marker = "LOBSTER_OPENCLAW_MCP_TOOL_TIMEOUT_MS"
    try:
        dist_dir = Path(mjs_path).resolve().parent / "dist"
        if not dist_dir.is_dir():
            return False
        candidates = sorted(dist_dir.glob("content-blocks-*.js"))
        changed = False
        for path in candidates:
            text = path.read_text(encoding="utf-8")
            if marker in text:
                continue
            new_text, patched = _apply_openclaw_mcp_tool_timeout_patch(text)
            if not patched:
                logger.warning(
                    "OpenClaw MCP tool timeout patch skipped, unsupported callTool shape; will not force restart for this patch: %s",
                    path,
                )
                continue
            path.write_text(new_text, encoding="utf-8")
            logger.info("Patched OpenClaw MCP tool timeout: %s", path)
            changed = True
        return changed
    except Exception as e:
        logger.warning("patch_openclaw_mcp_tool_timeout failed: %s", e)
        return False


def _patch_openclaw_mcp_sdk_timeout(mjs_path: str) -> bool:
    """Raise MCP SDK's default request timeout for OpenClaw's bundled runtime.

    OpenClaw can route tools through SDK helpers that fall back to the MCP
    default 60000ms. The H5 failure in diag_20260526073230_aaf96528 hit that
    exact boundary while the local MCP server was still saving generated assets.
    """
    marker = "LOBSTER_OPENCLAW_MCP_SDK_TIMEOUT_MS"
    changed = False
    try:
        for path in _openclaw_mcp_sdk_protocol_paths(mjs_path):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            if marker in text:
                continue
            new_text, patched = _apply_openclaw_mcp_sdk_timeout_patch(text)
            if not patched:
                logger.warning("OpenClaw MCP SDK timeout patch skipped, pattern not found: %s", path)
                continue
            path.write_text(new_text, encoding="utf-8")
            logger.info("Patched OpenClaw MCP SDK default timeout: %s", path)
            changed = True
        return changed
    except Exception as e:
        logger.warning("patch_openclaw_mcp_sdk_timeout failed: %s", e)
        return False


def _patch_openclaw_gateway_startup_preflight(mjs_path: str) -> bool:
    """Skip OpenClaw's generic doctor/config preflight for gateway startup.

    Gateway still reads and validates config inside its own command path. This
    avoids an expensive pre-listener preflight that can hang silently on some
    Windows machines, leaving only a node.exe process and no 18789 listener.
    """
    marker = "LOBSTER_OPENCLAW_SKIP_GATEWAY_DOCTOR_PREFLIGHT"
    try:
        dist_dir = Path(mjs_path).resolve().parent / "dist"
        if not dist_dir.is_dir():
            return False
        changed = False
        old = 'if (primary === "agent") return false;'
        new = 'if (primary === "agent" || primary === "gateway") return false;'
        for path in sorted(dist_dir.glob("logger-*.js")):
            text = path.read_text(encoding="utf-8")
            if marker in text:
                continue
            if old not in text or "function shouldMigrateStateFromPath" not in text:
                continue
            text = text.replace(old, new, 1)
            path.write_text(f"// {marker}\n" + text, encoding="utf-8")
            logger.info("Patched OpenClaw gateway startup preflight skip: %s", path)
            changed = True
        return changed
    except Exception as e:
        logger.warning("patch_openclaw_gateway_startup_preflight failed: %s", e)
        return False


def _apply_openclaw_agent_command_fast_path_patch(text: str) -> tuple[str, bool]:
    marker = "LOBSTER_OPENCLAW_LATENCY_TRACE_V3"
    if marker in text:
        return text, False

    original = text
    replacements: list[tuple[str, str, str]] = [
        (
            "\t\tlet resolvedThinkLevel = thinkOnce ?? thinkOverride ?? persistedThinking;",
            (
                "\t\tlet resolvedThinkLevel = thinkOnce ?? thinkOverride ?? persistedThinking;\n"
                "\t\tconst lobsterV3TraceStart = Date.now();\n"
                "\t\ttry { runtime.error(`[LOBSTER_TRACE_V3] stage=post_prepare_enter run_id=${runId} elapsed_ms=${Date.now() - lobsterV3TraceStart}`); } catch {}\n"
                "\t\tif (!resolvedThinkLevel && process.env.LOBSTER_OPENCLAW_FAST_THINKING_OFF === \"1\") {\n"
                "\t\t\tconst lobsterDefaultThinking = agentCfg?.thinkingDefault ?? cfg?.agents?.defaults?.thinkingDefault;\n"
                "\t\t\tif (String(lobsterDefaultThinking ?? \"\").trim().toLowerCase() === \"off\") {\n"
                "\t\t\t\tresolvedThinkLevel = \"off\";\n"
                "\t\t\t\ttry { runtime.error(`[LOBSTER_TRACE_V3] stage=thinking_default_fast_off run_id=${runId} elapsed_ms=${Date.now() - lobsterV3TraceStart}`); } catch {}\n"
                "\t\t\t}\n"
                "\t\t}"
            ),
            "thinking default fast path",
        ),
        (
            (
                "\t\tconst needsSkillsSnapshot = isNewSession || !sessionEntry?.skillsSnapshot;\n"
                "\t\tconst skillsSnapshotVersion = getSkillsSnapshotVersion(workspaceDir);\n"
                "\t\tconst skillFilter = resolveAgentSkillsFilter(cfg, sessionAgentId);\n"
                "\t\tconst skillsSnapshot = needsSkillsSnapshot ? buildWorkspaceSkillSnapshot(workspaceDir, {\n"
                "\t\t\tconfig: cfg,\n"
                "\t\t\teligibility: { remote: getRemoteSkillEligibility() },\n"
                "\t\t\tsnapshotVersion: skillsSnapshotVersion,\n"
                "\t\t\tskillFilter\n"
                "\t\t}) : sessionEntry?.skillsSnapshot;"
            ),
            (
                "\t\tconst lobsterSkipSkillsSnapshot = process.env.LOBSTER_OPENCLAW_SKIP_SKILLS_SNAPSHOT === \"1\" && (sessionAgentId === \"main\" || String(sessionAgentId).startsWith(\"lobster-sutui-\"));\n"
                "\t\tconst needsSkillsSnapshot = !lobsterSkipSkillsSnapshot && (isNewSession || !sessionEntry?.skillsSnapshot);\n"
                "\t\tconst skillsSnapshotVersion = getSkillsSnapshotVersion(workspaceDir);\n"
                "\t\tconst skillFilter = resolveAgentSkillsFilter(cfg, sessionAgentId);\n"
                "\t\ttry { runtime.error(`[LOBSTER_TRACE_V3] stage=skills_snapshot_decision run_id=${runId} elapsed_ms=${Date.now() - lobsterV3TraceStart} skip=${String(lobsterSkipSkillsSnapshot)} needs=${String(needsSkillsSnapshot)}`); } catch {}\n"
                "\t\tconst skillsSnapshot = lobsterSkipSkillsSnapshot ? {\n"
                "\t\t\tprompt: \"\",\n"
                "\t\t\tskills: [],\n"
                "\t\t\tresolvedSkills: [],\n"
                "\t\t\tversion: skillsSnapshotVersion,\n"
                "\t\t\t...(skillFilter === void 0 ? {} : { skillFilter })\n"
                "\t\t} : needsSkillsSnapshot ? (() => {\n"
                "\t\t\ttry { runtime.error(`[LOBSTER_TRACE_V3] stage=skills_snapshot_start run_id=${runId} elapsed_ms=${Date.now() - lobsterV3TraceStart}`); } catch {}\n"
                "\t\t\tconst snapshot = buildWorkspaceSkillSnapshot(workspaceDir, {\n"
                "\t\t\t\tconfig: cfg,\n"
                "\t\t\t\teligibility: { remote: getRemoteSkillEligibility() },\n"
                "\t\t\t\tsnapshotVersion: skillsSnapshotVersion,\n"
                "\t\t\t\tskillFilter\n"
                "\t\t\t});\n"
                "\t\t\ttry { runtime.error(`[LOBSTER_TRACE_V3] stage=skills_snapshot_end run_id=${runId} elapsed_ms=${Date.now() - lobsterV3TraceStart} skill_count=${String(snapshot?.skills?.length ?? 0)}`); } catch {}\n"
                "\t\t\treturn snapshot;\n"
                "\t\t})() : sessionEntry?.skillsSnapshot;"
            ),
            "skills snapshot fast path",
        ),
        (
            "\t\tconst configuredDefaultRef = resolveDefaultModelForAgent({",
            (
                "\t\ttry { runtime.error(`[LOBSTER_TRACE_V3] stage=model_resolution_start run_id=${runId} elapsed_ms=${Date.now() - lobsterV3TraceStart}`); } catch {}\n"
                "\t\tconst configuredDefaultRef = resolveDefaultModelForAgent({"
            ),
            "model resolution start",
        ),
        (
            (
                "\t\tif (needsModelCatalog) {\n"
                "\t\t\tmodelCatalog = await loadModelCatalog({ config: cfg });"
            ),
            (
                "\t\tif (needsModelCatalog) {\n"
                "\t\t\ttry { runtime.error(`[LOBSTER_TRACE_V3] stage=model_catalog_start run_id=${runId} elapsed_ms=${Date.now() - lobsterV3TraceStart} reason=allowlist_or_override`); } catch {}\n"
                "\t\t\tmodelCatalog = await loadModelCatalog({ config: cfg });\n"
                "\t\t\ttry { runtime.error(`[LOBSTER_TRACE_V3] stage=model_catalog_end run_id=${runId} elapsed_ms=${Date.now() - lobsterV3TraceStart} reason=allowlist_or_override count=${String(modelCatalog?.length ?? 0)}`); } catch {}"
            ),
            "model catalog timing",
        ),
        (
            (
                "\t\t\tif (!catalogForThinking || catalogForThinking.length === 0) {\n"
                "\t\t\t\tmodelCatalog = await loadModelCatalog({ config: cfg });\n"
                "\t\t\t\tcatalogForThinking = modelCatalog;\n"
                "\t\t\t}"
            ),
            (
                "\t\t\tif (!catalogForThinking || catalogForThinking.length === 0) {\n"
                "\t\t\t\ttry { runtime.error(`[LOBSTER_TRACE_V3] stage=model_catalog_start run_id=${runId} elapsed_ms=${Date.now() - lobsterV3TraceStart} reason=thinking_default`); } catch {}\n"
                "\t\t\t\tmodelCatalog = await loadModelCatalog({ config: cfg });\n"
                "\t\t\t\ttry { runtime.error(`[LOBSTER_TRACE_V3] stage=model_catalog_end run_id=${runId} elapsed_ms=${Date.now() - lobsterV3TraceStart} reason=thinking_default count=${String(modelCatalog?.length ?? 0)}`); } catch {}\n"
                "\t\t\t\tcatalogForThinking = modelCatalog;\n"
                "\t\t\t}"
            ),
            "thinking catalog timing",
        ),
        (
            (
                "\t\t\tconst resolvedSessionFile = await resolveSessionTranscriptFile({\n"
                "\t\t\t\tsessionId,"
            ),
            (
                "\t\t\ttry { runtime.error(`[LOBSTER_TRACE_V3] stage=session_file_resolve_start run_id=${runId} elapsed_ms=${Date.now() - lobsterV3TraceStart} kind=store_key`); } catch {}\n"
                "\t\t\tconst resolvedSessionFile = await resolveSessionTranscriptFile({\n"
                "\t\t\t\tsessionId,"
            ),
            "session file start",
        ),
        (
            (
                "\t\t\tsessionFile = resolvedSessionFile.sessionFile;\n"
                "\t\t\tsessionEntry = resolvedSessionFile.sessionEntry;"
            ),
            (
                "\t\t\tsessionFile = resolvedSessionFile.sessionFile;\n"
                "\t\t\tsessionEntry = resolvedSessionFile.sessionEntry;\n"
                "\t\t\ttry { runtime.error(`[LOBSTER_TRACE_V3] stage=session_file_resolve_end run_id=${runId} elapsed_ms=${Date.now() - lobsterV3TraceStart} kind=store_key`); } catch {}"
            ),
            "session file end",
        ),
        (
            (
                "\t\tif (!sessionFile) {\n"
                "\t\t\tconst resolvedSessionFile = await resolveSessionTranscriptFile({"
            ),
            (
                "\t\tif (!sessionFile) {\n"
                "\t\t\ttry { runtime.error(`[LOBSTER_TRACE_V3] stage=session_file_resolve_start run_id=${runId} elapsed_ms=${Date.now() - lobsterV3TraceStart} kind=fallback`); } catch {}\n"
                "\t\t\tconst resolvedSessionFile = await resolveSessionTranscriptFile({"
            ),
            "fallback session file start",
        ),
        (
            (
                "\t\tconst startedAt = Date.now();\n"
                "\t\tlet lifecycleEnded = false;"
            ),
            (
                "\t\ttry { runtime.error(`[LOBSTER_TRACE_V3] stage=pre_fallback_ready run_id=${runId} elapsed_ms=${Date.now() - lobsterV3TraceStart} provider=${String(provider)} model=${String(model)} think=${String(resolvedThinkLevel ?? \"\")}`); } catch {}\n"
                "\t\tconst startedAt = Date.now();\n"
                "\t\tlet lifecycleEnded = false;"
            ),
            "pre fallback ready",
        ),
    ]

    missing: list[str] = []
    for old, new, label in replacements:
        if old not in text:
            missing.append(label)
            continue
        text = text.replace(old, new, 1)
    if missing:
        logger.warning("OpenClaw latency fast-path patch partially skipped: %s", ", ".join(missing))
    if text == original:
        return original, False
    return f"// {marker}\n" + text, True


def _patch_openclaw_latency_trace(mjs_path: str) -> bool:
    """Add lightweight OpenClaw 2026.4.1 latency probes around chat and agent runs.

    This is diagnostic-only: it does not change routing, prompts, tool access, or
    model behavior. It writes timing markers into the normal OpenClaw log so the
    next diagnostic upload can show where long pre-tool delays happen.
    """
    marker = "LOBSTER_OPENCLAW_LATENCY_TRACE"
    try:
        dist_dir = Path(mjs_path).resolve().parent / "dist"
        if not dist_dir.is_dir():
            return False
        changed = False

        gateway_candidates = sorted(dist_dir.glob("gateway-cli-*.js"))
        gateway_old = (
            "\tif (!stream) {\n"
            "\t\ttry {\n"
            "\t\t\tconst content = resolveAgentResponseText(await agentCommandFromIngress(commandInput, defaultRuntime, deps));"
        )
        gateway_new = (
            "\tconst lobsterTraceId = String(req.headers[\"x-lobster-trace-id\"] ?? \"\").trim() || runId;\n"
            "\tconst lobsterTraceStart = Date.now();\n"
            "\tlogWarn(`[LOBSTER_TRACE] trace_id=${lobsterTraceId} stage=openai_compat_received run_id=${runId} agent_id=${agentId} session=${sessionKey} stream=${stream} model=${model} msg_chars=${String(prompt.message ?? \"\").length} image_count=${images.length}`);\n"
            "\tif (!stream) {\n"
            "\t\ttry {\n"
            "\t\t\tlogWarn(`[LOBSTER_TRACE] trace_id=${lobsterTraceId} stage=agent_command_start run_id=${runId} elapsed_ms=${Date.now() - lobsterTraceStart}`);\n"
            "\t\t\tconst lobsterAgentStart = Date.now();\n"
            "\t\t\tconst content = resolveAgentResponseText(await agentCommandFromIngress(commandInput, defaultRuntime, deps));\n"
            "\t\t\tlogWarn(`[LOBSTER_TRACE] trace_id=${lobsterTraceId} stage=agent_command_end run_id=${runId} duration_ms=${Date.now() - lobsterAgentStart} total_ms=${Date.now() - lobsterTraceStart} content_chars=${String(content ?? \"\").length}`);"
        )
        for path in gateway_candidates:
            text = path.read_text(encoding="utf-8")
            if marker in text:
                continue
            if gateway_old not in text:
                logger.warning("OpenClaw latency trace gateway patch skipped, pattern not found: %s", path)
                continue
            text = f"// {marker}\n" + text.replace(gateway_old, gateway_new, 1)
            path.write_text(text, encoding="utf-8")
            logger.info("Patched OpenClaw latency trace gateway probes: %s", path)
            changed = True

        runner_candidates = sorted(dist_dir.glob("agent-runner.runtime-*.js"))
        runner_old = (
            "\t\t\t\tconst { embeddedContext, senderContext, runBaseParams } = buildEmbeddedRunExecutionParams({"
        )
        runner_new = (
            "\t\t\t\tconst lobsterRunnerTraceStart = Date.now();\n"
            "\t\t\t\tdefaultRuntime.error(`[LOBSTER_TRACE] stage=runner_embedded_prepare run_id=${runId} provider=${String(provider)} model=${String(model)} session=${String(params.sessionKey ?? \"\")} cmd_chars=${String(params.commandBody ?? \"\").length}`);\n"
            "\t\t\t\tconst { embeddedContext, senderContext, runBaseParams } = buildEmbeddedRunExecutionParams({"
        )
        tool_old = (
            "\t\t\t\t\t\t\tonAgentEvent: async (evt) => {\n"
            "\t\t\t\t\t\t\t\tconst hasLifecyclePhase = evt.stream === \"lifecycle\" && typeof evt.data.phase === \"string\";"
        )
        tool_new = (
            "\t\t\t\t\t\t\tonAgentEvent: async (evt) => {\n"
            "\t\t\t\t\t\t\t\ttry {\n"
            "\t\t\t\t\t\t\t\t\tif (evt?.stream === \"tool\") defaultRuntime.error(`[LOBSTER_TRACE] stage=runner_tool_event run_id=${runId} phase=${String(evt.data?.phase ?? \"\")} name=${String(evt.data?.name ?? \"\")} elapsed_ms=${Date.now() - lobsterRunnerTraceStart}`);\n"
            "\t\t\t\t\t\t\t\t\tif (evt?.stream === \"lifecycle\" && typeof evt.data?.phase === \"string\") defaultRuntime.error(`[LOBSTER_TRACE] stage=runner_lifecycle run_id=${runId} phase=${String(evt.data.phase)} elapsed_ms=${Date.now() - lobsterRunnerTraceStart}`);\n"
            "\t\t\t\t\t\t\t\t} catch {}\n"
            "\t\t\t\t\t\t\t\tconst hasLifecyclePhase = evt.stream === \"lifecycle\" && typeof evt.data.phase === \"string\";"
        )
        embedded_old = "\t\t\t\t\t\tconst result = await runEmbeddedPiAgent({"
        embedded_new = (
            "\t\t\t\t\t\tdefaultRuntime.error(`[LOBSTER_TRACE] stage=run_embedded_start run_id=${runId} elapsed_ms=${Date.now() - lobsterRunnerTraceStart}`);\n"
            "\t\t\t\t\t\tconst result = await runEmbeddedPiAgent({"
        )
        embedded_end_old = "\t\t\t\t\t\tbootstrapPromptWarningSignaturesSeen = resolveBootstrapWarningSignaturesSeen(result.meta?.systemPromptReport);"
        embedded_end_new = (
            "\t\t\t\t\t\tdefaultRuntime.error(`[LOBSTER_TRACE] stage=run_embedded_end run_id=${runId} duration_ms=${Date.now() - lobsterRunnerTraceStart}`);\n"
            "\t\t\t\t\t\tbootstrapPromptWarningSignaturesSeen = resolveBootstrapWarningSignaturesSeen(result.meta?.systemPromptReport);"
        )
        for path in runner_candidates:
            text = path.read_text(encoding="utf-8")
            if marker in text:
                continue
            if not all(pat in text for pat in (runner_old, tool_old, embedded_old, embedded_end_old)):
                logger.warning("OpenClaw latency trace runner patch skipped, pattern not found: %s", path)
                continue
            runner_idx = text.find(runner_old)
            if runner_idx < 0:
                logger.warning("OpenClaw latency trace runner anchor missing: %s", path)
                continue
            prefix = text[:runner_idx]
            target = text[runner_idx:]
            if not all(pat in target for pat in (tool_old, embedded_old, embedded_end_old)):
                logger.warning("OpenClaw latency trace runner target block incomplete: %s", path)
                continue
            next_run_idx = target.find("const result = await runEmbeddedPiAgent({", target.find(embedded_old) + len(embedded_old))
            if next_run_idx > 0:
                target_block = target[:next_run_idx]
                target_rest = target[next_run_idx:]
            else:
                target_block = target
                target_rest = ""
            if not all(pat in target_block for pat in (tool_old, embedded_old, embedded_end_old)):
                logger.warning("OpenClaw latency trace runner target block markers crossed next run: %s", path)
                continue
            target_block = target_block.replace(runner_old, runner_new, 1)
            target_block = target_block.replace(tool_old, tool_new, 1)
            target_block = target_block.replace(embedded_old, embedded_new, 1)
            target_block = target_block.replace(embedded_end_old, embedded_end_new, 1)
            target = target_block + target_rest
            path.write_text(f"// {marker}\n" + prefix + target, encoding="utf-8")
            logger.info("Patched OpenClaw latency trace runner probes: %s", path)
            changed = True

        # OpenAI-compatible Gateway requests do not go through
        # agent-runner.runtime in OpenClaw 2026.4.1. They enter via
        # agent-command -> pi-embedded, so add a second layer of probes there.
        marker_v2 = "LOBSTER_OPENCLAW_LATENCY_TRACE_V2"

        command_candidates = sorted(dist_dir.glob("agent-command-*.js"))
        command_replacements = [
            (
                (
                    "async function agentCommandInternal(opts, runtime = defaultRuntime, deps = createDefaultDeps()) {\n"
                    "\tconst prepared = await prepareAgentCommandExecution(opts, runtime);"
                ),
                (
                    "async function agentCommandInternal(opts, runtime = defaultRuntime, deps = createDefaultDeps()) {\n"
                    "\tconst lobsterAgentTraceStart = Date.now();\n"
                    "\tconst lobsterAgentTraceRunId = String(opts.runId ?? \"\");\n"
                    "\tconst lobsterAgentTraceSession = String(opts.sessionKey ?? opts.sessionId ?? \"\");\n"
                    "\ttry { runtime.error(`[LOBSTER_TRACE_V2] stage=agent_internal_enter run_id=${lobsterAgentTraceRunId} session=${lobsterAgentTraceSession}`); } catch {}\n"
                    "\ttry { runtime.error(`[LOBSTER_TRACE_V2] stage=agent_prepare_start run_id=${lobsterAgentTraceRunId} elapsed_ms=${Date.now() - lobsterAgentTraceStart}`); } catch {}\n"
                    "\tconst prepared = await prepareAgentCommandExecution(opts, runtime);\n"
                    "\ttry { runtime.error(`[LOBSTER_TRACE_V2] stage=agent_prepare_end run_id=${String(prepared?.runId ?? lobsterAgentTraceRunId)} elapsed_ms=${Date.now() - lobsterAgentTraceStart} workspace=${String(prepared?.workspaceDir ?? \"\")}`); } catch {}"
                ),
                "agent-command prepare",
            ),
            (
                "\t\t\tconst fallbackResult = await runWithModelFallback({",
                (
                    "\t\t\ttry { runtime.error(`[LOBSTER_TRACE_V2] stage=fallback_wrapper_start run_id=${runId} elapsed_ms=${Date.now() - lobsterAgentTraceStart} provider=${String(provider)} model=${String(model)}`); } catch {}\n"
                    "\t\t\tconst fallbackResult = await runWithModelFallback({"
                ),
                "agent-command fallback start",
            ),
            (
                (
                    "\t\t\t\trun: async (providerOverride, modelOverride, runOptions) => {\n"
                    "\t\t\t\t\tconst isFallbackRetry = fallbackAttemptIndex > 0;\n"
                    "\t\t\t\t\tfallbackAttemptIndex += 1;\n"
                    "\t\t\t\t\treturn runAgentAttempt({"
                ),
                (
                    "\t\t\t\trun: async (providerOverride, modelOverride, runOptions) => {\n"
                    "\t\t\t\t\tconst isFallbackRetry = fallbackAttemptIndex > 0;\n"
                    "\t\t\t\t\tfallbackAttemptIndex += 1;\n"
                    "\t\t\t\t\ttry { runtime.error(`[LOBSTER_TRACE_V2] stage=run_agent_attempt_start run_id=${runId} elapsed_ms=${Date.now() - lobsterAgentTraceStart} attempt=${fallbackAttemptIndex} provider=${String(providerOverride)} model=${String(modelOverride)}`); } catch {}\n"
                    "\t\t\t\t\treturn runAgentAttempt({"
                ),
                "agent-command attempt start",
            ),
            (
                "\t\t\tresult = fallbackResult.result;",
                (
                    "\t\t\ttry { runtime.error(`[LOBSTER_TRACE_V2] stage=fallback_wrapper_end run_id=${runId} elapsed_ms=${Date.now() - lobsterAgentTraceStart} provider=${String(fallbackResult.provider)} model=${String(fallbackResult.model)}`); } catch {}\n"
                    "\t\t\tresult = fallbackResult.result;"
                ),
                "agent-command fallback end",
            ),
        ]
        for path in command_candidates:
            text = path.read_text(encoding="utf-8")
            if marker_v2 in text:
                continue
            original = text
            missing: list[str] = []
            for old, new, label in command_replacements:
                if old not in text:
                    missing.append(label)
                    continue
                text = text.replace(old, new, 1)
            if missing:
                logger.warning("OpenClaw latency trace v2 agent-command patch partially skipped %s: %s", path, ", ".join(missing))
            if text != original:
                path.write_text(f"// {marker_v2}\n" + text, encoding="utf-8")
                logger.info("Patched OpenClaw latency trace v2 agent-command probes: %s", path)
                changed = True

        for path in command_candidates:
            text = path.read_text(encoding="utf-8")
            new_text, patched = _apply_openclaw_agent_command_fast_path_patch(text)
            if patched:
                path.write_text(new_text, encoding="utf-8")
                logger.info("Patched OpenClaw latency fast-path agent-command probes: %s", path)
                changed = True

        embedded_candidates = sorted(dist_dir.glob("pi-embedded-*.js"))
        embedded_replacements = [
            (
                (
                    "async function runEmbeddedAttempt(params) {\n"
                    "\tconst resolvedWorkspace = resolveUserPath(params.workspaceDir);"
                ),
                (
                    "async function runEmbeddedAttempt(params) {\n"
                    "\tconst lobsterAttemptTraceStart = Date.now();\n"
                    "\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_enter run_id=${String(params.runId ?? \"\")} session=${String(params.sessionKey ?? params.sessionId ?? \"\")} provider=${String(params.provider ?? \"\")} model=${String(params.modelId ?? \"\")}`); } catch {}\n"
                    "\tconst resolvedWorkspace = resolveUserPath(params.workspaceDir);"
                ),
                "attempt enter",
            ),
            (
                "\tawait fs.mkdir(effectiveWorkspace, { recursive: true });",
                (
                    "\tawait fs.mkdir(effectiveWorkspace, { recursive: true });\n"
                    "\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_workspace_ready run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart} workspace=${String(effectiveWorkspace)}`); } catch {}"
                ),
                "attempt workspace",
            ),
            (
                "\t\tconst { shouldLoadSkillEntries, skillEntries } = resolveEmbeddedRunSkillEntries({",
                (
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_skills_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart}`); } catch {}\n"
                    "\t\tconst { shouldLoadSkillEntries, skillEntries } = resolveEmbeddedRunSkillEntries({"
                ),
                "attempt skills start",
            ),
            (
                "\t\tconst sessionLabel = params.sessionKey ?? params.sessionId;",
                (
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_skills_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart}`); } catch {}\n"
                    "\t\tconst sessionLabel = params.sessionKey ?? params.sessionId;"
                ),
                "attempt skills end",
            ),
            (
                "\t\tconst { bootstrapFiles: hookAdjustedBootstrapFiles, contextFiles } = await resolveBootstrapContextForRun({",
                (
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_bootstrap_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart}`); } catch {}\n"
                    "\t\tconst { bootstrapFiles: hookAdjustedBootstrapFiles, contextFiles } = await resolveBootstrapContextForRun({"
                ),
                "attempt bootstrap start",
            ),
            (
                "\t\tconst bootstrapMaxChars = resolveBootstrapMaxChars(params.config);",
                (
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_bootstrap_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart} bootstrap_files=${hookAdjustedBootstrapFiles.length} context_files=${contextFiles.length}`); } catch {}\n"
                    "\t\tconst bootstrapMaxChars = resolveBootstrapMaxChars(params.config);"
                ),
                "attempt bootstrap end",
            ),
            (
                "\t\tconst toolsRaw = params.disableTools ? [] : (() => {",
                (
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_builtin_tools_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart} disable_tools=${String(params.disableTools === true)}`); } catch {}\n"
                    "\t\tconst toolsRaw = params.disableTools ? [] : (() => {"
                ),
                "attempt builtin tools start",
            ),
            (
                "\t\t})();\n\t\tconst toolsEnabled = supportsModelTools(params.model);",
                (
                    "\t\t})();\n"
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_builtin_tools_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart} tool_count=${toolsRaw.length}`); } catch {}\n"
                    "\t\tconst toolsEnabled = supportsModelTools(params.model);"
                ),
                "attempt builtin tools end",
            ),
            (
                "\t\tconst bundleMcpSessionRuntime = toolsEnabled ? await getOrCreateSessionMcpRuntime({",
                (
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_mcp_session_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart} tools_enabled=${String(toolsEnabled)}`); } catch {}\n"
                    "\t\tconst bundleMcpSessionRuntime = toolsEnabled ? await getOrCreateSessionMcpRuntime({"
                ),
                "attempt mcp session start",
            ),
            (
                "\t\t}) : void 0;\n\t\tconst bundleMcpRuntime = bundleMcpSessionRuntime ? await materializeBundleMcpToolsForRun({",
                (
                    "\t\t}) : void 0;\n"
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_mcp_session_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart} has_runtime=${String(Boolean(bundleMcpSessionRuntime))}`); } catch {}\n"
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_mcp_materialize_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart}`); } catch {}\n"
                    "\t\tconst bundleMcpRuntime = bundleMcpSessionRuntime ? await materializeBundleMcpToolsForRun({"
                ),
                "attempt mcp materialize start",
            ),
            (
                "\t\t}) : void 0;\n\t\tconst bundleLspRuntime = toolsEnabled ? await createBundleLspToolRuntime({",
                (
                    "\t\t}) : void 0;\n"
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_mcp_materialize_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart} mcp_tools=${String(bundleMcpRuntime?.tools?.length ?? 0)}`); } catch {}\n"
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_lsp_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart}`); } catch {}\n"
                    "\t\tconst bundleLspRuntime = toolsEnabled ? await createBundleLspToolRuntime({"
                ),
                "attempt lsp start",
            ),
            (
                "\t\t}) : void 0;\n\t\tconst effectiveTools = [",
                (
                    "\t\t}) : void 0;\n"
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_lsp_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart} lsp_tools=${String(bundleLspRuntime?.tools?.length ?? 0)}`); } catch {}\n"
                    "\t\tconst effectiveTools = ["
                ),
                "attempt lsp end",
            ),
            (
                "\t\tconst machineName = await getMachineDisplayName();",
                (
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_system_prompt_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart} effective_tools=${effectiveTools.length}`); } catch {}\n"
                    "\t\tconst machineName = await getMachineDisplayName();"
                ),
                "attempt system prompt start",
            ),
            (
                "\t\tlet systemPromptText = createSystemPromptOverride(appendPrompt)();",
                (
                    "\t\tlet systemPromptText = createSystemPromptOverride(appendPrompt)();\n"
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_system_prompt_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart} system_chars=${String(systemPromptText ?? \"\").length}`); } catch {}"
                ),
                "attempt system prompt end",
            ),
            (
                "\t\tconst sessionLock = await acquireSessionWriteLock({",
                (
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_session_lock_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart} session_file=${String(params.sessionFile ?? \"\")}`); } catch {}\n"
                    "\t\tconst sessionLock = await acquireSessionWriteLock({"
                ),
                "attempt session lock start",
            ),
            (
                "\t\t});\n\t\tlet sessionManager;",
                (
                    "\t\t});\n"
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_session_lock_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart}`); } catch {}\n"
                    "\t\tlet sessionManager;"
                ),
                "attempt session lock end",
            ),
            (
                "\t\t\tsessionManager = guardSessionManager(SessionManager.open(params.sessionFile), {",
                (
                    "\t\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_session_open_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart}`); } catch {}\n"
                    "\t\t\tsessionManager = guardSessionManager(SessionManager.open(params.sessionFile), {"
                ),
                "attempt session open start",
            ),
            (
                "\t\t\ttrackSessionManagerAccess(params.sessionFile);",
                (
                    "\t\t\ttrackSessionManagerAccess(params.sessionFile);\n"
                    "\t\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_session_open_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart} had_session_file=${String(hadSessionFile)}`); } catch {}"
                ),
                "attempt session open end",
            ),
            (
                "\t\t\tawait runAttemptContextEngineBootstrap({",
                (
                    "\t\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_context_bootstrap_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart}`); } catch {}\n"
                    "\t\t\tawait runAttemptContextEngineBootstrap({"
                ),
                "attempt context bootstrap start",
            ),
            (
                "\t\t\tawait prepareSessionManagerForRun({",
                (
                    "\t\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_context_bootstrap_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart}`); } catch {}\n"
                    "\t\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_prepare_session_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart}`); } catch {}\n"
                    "\t\t\tawait prepareSessionManagerForRun({"
                ),
                "attempt prepare session start",
            ),
            (
                "\t\t\tconst settingsManager = createPreparedEmbeddedPiSettingsManager({",
                (
                    "\t\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_prepare_session_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart}`); } catch {}\n"
                    "\t\t\tconst settingsManager = createPreparedEmbeddedPiSettingsManager({"
                ),
                "attempt prepare session end",
            ),
            (
                "\t\t\t\tawait resourceLoader.reload();",
                (
                    "\t\t\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_resource_reload_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart}`); } catch {}\n"
                    "\t\t\t\tawait resourceLoader.reload();\n"
                    "\t\t\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_resource_reload_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart}`); } catch {}"
                ),
                "attempt resource reload",
            ),
            (
                "\t\t\t({session} = await createAgentSession({",
                (
                    "\t\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_create_session_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart}`); } catch {}\n"
                    "\t\t\t({session} = await createAgentSession({"
                ),
                "attempt create session start",
            ),
            (
                "\t\t\tapplySystemPromptOverrideToSession(session, systemPromptText);",
                (
                    "\t\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_create_session_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart}`); } catch {}\n"
                    "\t\t\tapplySystemPromptOverrideToSession(session, systemPromptText);"
                ),
                "attempt create session end",
            ),
            (
                "\t\t\t\tconst prior = await sanitizeSessionHistory({",
                (
                    "\t\t\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_history_sanitize_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart} messages=${activeSession.messages.length}`); } catch {}\n"
                    "\t\t\t\tconst prior = await sanitizeSessionHistory({"
                ),
                "attempt history sanitize start",
            ),
            (
                "\t\t\t\tcacheTrace?.recordStage(\"session:limited\", { messages: limited });",
                (
                    "\t\t\t\tcacheTrace?.recordStage(\"session:limited\", { messages: limited });\n"
                    "\t\t\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_history_sanitize_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart} limited_messages=${limited.length}`); } catch {}"
                ),
                "attempt history sanitize end",
            ),
            (
                "\t\t\tconst prePromptMessageCount = activeSession.messages.length;",
                (
                    "\t\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_prompt_phase_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart} messages=${activeSession.messages.length}`); } catch {}\n"
                    "\t\t\tconst prePromptMessageCount = activeSession.messages.length;"
                ),
                "attempt prompt phase start",
            ),
            (
                (
                    "\t\t\t\t\tif (imageResult.images.length > 0) await abortable(activeSession.prompt(effectivePrompt, { images: imageResult.images }));\n"
                    "\t\t\t\t\telse await abortable(activeSession.prompt(effectivePrompt));"
                ),
                (
                    "\t\t\t\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_prompt_call_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart} prompt_chars=${effectivePrompt.length} history_messages=${activeSession.messages.length} prompt_images=${imageResult.images.length}`); } catch {}\n"
                    "\t\t\t\t\tif (imageResult.images.length > 0) await abortable(activeSession.prompt(effectivePrompt, { images: imageResult.images }));\n"
                    "\t\t\t\t\telse await abortable(activeSession.prompt(effectivePrompt));\n"
                    "\t\t\t\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=attempt_prompt_call_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterAttemptTraceStart} messages=${activeSession.messages.length}`); } catch {}"
                ),
                "attempt prompt call",
            ),
            (
                (
                    "async function runEmbeddedPiAgent(params) {\n"
                    "\tconst sessionLane = resolveSessionLane(params.sessionKey?.trim() || params.sessionId);"
                ),
                (
                    "async function runEmbeddedPiAgent(params) {\n"
                    "\tconst lobsterEmbeddedTraceStart = Date.now();\n"
                    "\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=embedded_agent_enter run_id=${String(params.runId ?? \"\")} session=${String(params.sessionKey ?? params.sessionId ?? \"\")} provider=${String(params.provider ?? \"\")} model=${String(params.model ?? \"\")}`); } catch {}\n"
                    "\tconst sessionLane = resolveSessionLane(params.sessionKey?.trim() || params.sessionId);"
                ),
                "embedded enter",
            ),
            (
                "\treturn enqueueSession(() => enqueueGlobal(async () => {\n\t\tconst started = Date.now();",
                (
                    "\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=embedded_agent_enqueue run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterEmbeddedTraceStart}`); } catch {}\n"
                    "\treturn enqueueSession(() => enqueueGlobal(async () => {\n"
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=embedded_queue_enter run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterEmbeddedTraceStart}`); } catch {}\n"
                    "\t\tconst started = Date.now();"
                ),
                "embedded queue",
            ),
            (
                "\t\tif (workspaceResolution.usedFallback) log$13.warn(`[workspace-fallback] caller=runEmbeddedPiAgent reason=${workspaceResolution.fallbackReason} run=${params.runId} session=${redactedSessionId} sessionKey=${redactedSessionKey} agent=${workspaceResolution.agentId} workspace=${redactedWorkspace}`);\n\t\tensureRuntimePluginsLoaded({",
                (
                    "\t\tif (workspaceResolution.usedFallback) log$13.warn(`[workspace-fallback] caller=runEmbeddedPiAgent reason=${workspaceResolution.fallbackReason} run=${params.runId} session=${redactedSessionId} sessionKey=${redactedSessionKey} agent=${workspaceResolution.agentId} workspace=${redactedWorkspace}`);\n"
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=embedded_plugins_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterEmbeddedTraceStart} workspace=${redactedWorkspace}`); } catch {}\n"
                    "\t\tensureRuntimePluginsLoaded({"
                ),
                "embedded plugins start",
            ),
            (
                "\t\t});\n\t\tlet provider = (params.provider ?? \"anthropic\").trim() || \"anthropic\";",
                (
                    "\t\t});\n"
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=embedded_plugins_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterEmbeddedTraceStart}`); } catch {}\n"
                    "\t\tlet provider = (params.provider ?? \"anthropic\").trim() || \"anthropic\";"
                ),
                "embedded plugins end",
            ),
            (
                "\t\tawait ensureOpenClawModelsJson(params.config, agentDir);",
                (
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=embedded_models_json_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterEmbeddedTraceStart}`); } catch {}\n"
                    "\t\tawait ensureOpenClawModelsJson(params.config, agentDir);\n"
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=embedded_models_json_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterEmbeddedTraceStart}`); } catch {}"
                ),
                "embedded models json",
            ),
            (
                "\t\tconst hookSelection = await resolveHookModelSelection({",
                (
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=embedded_hook_model_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterEmbeddedTraceStart}`); } catch {}\n"
                    "\t\tconst hookSelection = await resolveHookModelSelection({"
                ),
                "embedded hook start",
            ),
            (
                "\t\tprovider = hookSelection.provider;",
                (
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=embedded_hook_model_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterEmbeddedTraceStart}`); } catch {}\n"
                    "\t\tprovider = hookSelection.provider;"
                ),
                "embedded hook end",
            ),
            (
                "\t\tconst { model, error, authStorage, modelRegistry } = await resolveModelAsync(provider, modelId, agentDir, params.config);",
                (
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=embedded_resolve_model_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterEmbeddedTraceStart} provider=${provider} model=${modelId}`); } catch {}\n"
                    "\t\tconst { model, error, authStorage, modelRegistry } = await resolveModelAsync(provider, modelId, agentDir, params.config);\n"
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=embedded_resolve_model_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterEmbeddedTraceStart} model_ok=${String(Boolean(model))}`); } catch {}"
                ),
                "embedded resolve model",
            ),
            (
                "\t\tawait initializeAuthProfile();",
                (
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=embedded_auth_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterEmbeddedTraceStart}`); } catch {}\n"
                    "\t\tawait initializeAuthProfile();\n"
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=embedded_auth_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterEmbeddedTraceStart}`); } catch {}"
                ),
                "embedded auth",
            ),
            (
                "\t\tconst contextEngine = await resolveContextEngine(params.config);",
                (
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=embedded_context_engine_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterEmbeddedTraceStart}`); } catch {}\n"
                    "\t\tconst contextEngine = await resolveContextEngine(params.config);\n"
                    "\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=embedded_context_engine_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterEmbeddedTraceStart} owns_compaction=${String(contextEngine?.info?.ownsCompaction === true)}`); } catch {}"
                ),
                "embedded context engine",
            ),
            (
                "\t\t\t\tconst attempt = await runEmbeddedAttempt({",
                (
                    "\t\t\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=embedded_attempt_start run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterEmbeddedTraceStart} iteration=${runLoopIterations}`); } catch {}\n"
                    "\t\t\t\tconst attempt = await runEmbeddedAttempt({"
                ),
                "embedded attempt start",
            ),
            (
                "\t\t\t\tconst { aborted, promptError, timedOut, timedOutDuringCompaction, sessionIdUsed, lastAssistant } = attempt;",
                (
                    "\t\t\t\ttry { log$13.warn(`[LOBSTER_TRACE_V2] stage=embedded_attempt_end run_id=${String(params.runId ?? \"\")} elapsed_ms=${Date.now() - lobsterEmbeddedTraceStart} aborted=${String(attempt?.aborted === true)} timed_out=${String(attempt?.timedOut === true)} tool_count=${String(attempt?.toolMetas?.length ?? 0)}`); } catch {}\n"
                    "\t\t\t\tconst { aborted, promptError, timedOut, timedOutDuringCompaction, sessionIdUsed, lastAssistant } = attempt;"
                ),
                "embedded attempt end",
            ),
        ]
        for path in embedded_candidates:
            text = path.read_text(encoding="utf-8")
            if marker_v2 in text:
                continue
            original = text
            missing: list[str] = []
            attempt_start = text.find("async function runEmbeddedAttempt(params) {")
            embedded_start = text.find("async function runEmbeddedPiAgent(params) {")
            if attempt_start < 0 or embedded_start < 0 or embedded_start <= attempt_start:
                logger.warning("OpenClaw latency trace v2 embedded patch skipped, function anchors not found: %s", path)
                continue
            prefix = text[:attempt_start]
            attempt_block = text[attempt_start:embedded_start]
            embedded_block = text[embedded_start:]
            for old, new, label in embedded_replacements:
                if label.startswith("attempt "):
                    target_block = attempt_block
                elif label.startswith("embedded "):
                    target_block = embedded_block
                else:
                    target_block = text
                if old not in target_block:
                    missing.append(label)
                    continue
                target_block = target_block.replace(old, new, 1)
                if label.startswith("attempt "):
                    attempt_block = target_block
                elif label.startswith("embedded "):
                    embedded_block = target_block
                else:
                    text = target_block
            text = prefix + attempt_block + embedded_block
            if missing:
                logger.warning("OpenClaw latency trace v2 embedded patch partially skipped %s: %s", path, ", ".join(missing))
            if text != original:
                path.write_text(f"// {marker_v2}\n" + text, encoding="utf-8")
                logger.info("Patched OpenClaw latency trace v2 embedded probes: %s", path)
                changed = True
        return changed
    except Exception as e:
        logger.warning("patch_openclaw_latency_trace failed: %s", e)
        return False


def _log_openclaw_gateway_start_diagnostics(
    reason: str,
    proc: Optional[subprocess.Popen] = None,
    last_error: str = "",
    startup_log_path: Optional[Path] = None,
) -> None:
    returncode = None
    try:
        returncode = proc.poll() if proc is not None else None
    except Exception:
        returncode = None
    listener_pids = _find_listener_pids_on_18789()
    gateway_processes = _find_openclaw_gateway_process_infos()
    gateway_pids = [int(info["pid"]) for info in gateway_processes if info.get("pid")]
    logger.warning(
        "OpenClaw Gateway start diagnostics: reason=%s returncode=%s listener_pids=%s gateway_pids=%s gateway_processes=%s last_error=%s",
        reason,
        returncode,
        listener_pids,
        gateway_pids,
        gateway_processes,
        last_error or "-",
    )
    if startup_log_path is not None:
        _write_openclaw_startup_diag_line(
            startup_log_path,
            "readiness_diagnostics",
            reason=reason,
            returncode=returncode,
            listener_pids=listener_pids,
            gateway_pids=gateway_pids,
            gateway_processes=gateway_processes,
            last_error=last_error or "",
        )
        _remember_openclaw_startup_diag(
            status="failed",
            reason=reason,
            returncode=returncode,
            listener_pids=listener_pids,
            gateway_pids=gateway_pids,
            gateway_processes=gateway_processes,
            last_error=last_error or "",
            log_path=str(startup_log_path),
        )
    for path in _openclaw_log_candidates():
        logger.warning("OpenClaw log tail path=%s\n%s", path, _read_log_tail_for_warning(path))


def _wait_for_openclaw_gateway_ready(
    max_wait: float = 30.0,
    proc: Optional[subprocess.Popen] = None,
    startup_log_path: Optional[Path] = None,
) -> Optional[int]:
    """Wait until the Gateway listens on 18789.

    Do not probe the OpenClaw HTTP root here. Some OpenClaw gateway builds run
    channel middleware even for a health-style GET, and optional channels with
    missing dependencies can make readiness checks look like real chat hangs.
    """
    deadline = time.time() + max_wait
    wait_started_at = time.time()
    next_diag_at = wait_started_at + 10.0
    last_error = ""
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            logger.warning(
                "OpenClaw Gateway process exited before listening, returncode=%s",
                proc.returncode,
            )
            _log_openclaw_gateway_start_diagnostics("process-exited", proc, last_error, startup_log_path)
            return None
        pid = _find_openclaw_pid()
        if pid:
            logger.info("OpenClaw Gateway ready, PID %s listening on 18789", pid)
            if startup_log_path is not None:
                _write_openclaw_startup_diag_line(
                    startup_log_path,
                    "ready",
                    pid=pid,
                    elapsed_sec=round(time.time() - wait_started_at, 3),
                )
                _remember_openclaw_startup_diag(status="ready", pid=pid, log_path=str(startup_log_path))
            return pid
        now = time.time()
        if startup_log_path is not None and now >= next_diag_at:
            gateway_processes = _find_openclaw_gateway_process_infos()
            _write_openclaw_startup_diag_line(
                startup_log_path,
                "readiness_probe",
                elapsed_sec=round(now - wait_started_at, 3),
                returncode=proc.poll() if proc is not None else None,
                listener_pids=_find_listener_pids_on_18789(),
                gateway_pids=[int(info["pid"]) for info in gateway_processes if info.get("pid")],
                gateway_processes=gateway_processes,
            )
            next_diag_at = now + 10.0
        time.sleep(0.5)
    if last_error:
        logger.warning("OpenClaw Gateway listener found but HTTP readiness timed out: %s", last_error)
    _log_openclaw_gateway_start_diagnostics("readiness-timeout", proc, last_error, startup_log_path)
    return None


def _wait_until_no_listener_on_18789(max_wait: float = 6.0) -> None:
    """杀掉进程后端口可能短暂未释放，轮询直到无 LISTEN 或超时。"""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if not _find_listener_pids_on_18789():
            return
        time.sleep(0.12)


def _kill_pid(pid: int):
    try:
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=10)
        else:
            os.kill(pid, 9)
    except Exception as e:
        logger.warning("Failed to kill PID %s: %s", pid, e)


def _build_openclaw_env() -> dict:
    """Build environment variables for the OpenClaw child process."""
    env = dict(os.environ)
    oc_env = _read_oc_env()
    env.update(oc_env)
    gateway_url = (settings.openclaw_gateway_url or env.get("OPENCLAW_GATEWAY_URL") or "http://127.0.0.1:18789").strip()
    if gateway_url:
        env["OPENCLAW_GATEWAY_URL"] = gateway_url
    gateway_token = _configured_gateway_token()
    if gateway_token:
        env["OPENCLAW_GATEWAY_TOKEN"] = gateway_token
    env["OPENCLAW_CONFIG_PATH"] = str(_OC_CONFIG)
    env["OPENCLAW_STATE_DIR"] = str(_OC_DIR)
    env["OPENCLAW_DISABLE_BONJOUR"] = "1"
    env["OPENCLAW_NO_RESPAWN"] = "1"
    env.setdefault("OPENCLAW_DEBUG_INGRESS_TIMING", "1")
    # Windows users have repeatedly hit a silent pre-listener stall while Node
    # builds/reads its compile cache. Prefer deterministic gateway startup over
    # caching here; OpenClaw chat latency is dominated by the running gateway,
    # not by this one-time import cache.
    env["NODE_DISABLE_COMPILE_CACHE"] = "1"
    env.pop("NODE_COMPILE_CACHE", None)
    env.setdefault("LOBSTER_OPENCLAW_FAST_THINKING_OFF", "1")
    env.setdefault("LOBSTER_OPENCLAW_SKIP_SKILLS_SNAPSHOT", "1")
    env["LOBSTER_OPENCLAW_DISABLE_SLACK_STAGE"] = "1"
    env["LOBSTER_OPENCLAW_DISABLE_MODEL_PRICING"] = "1"
    env.setdefault("LOBSTER_OPENCLAW_MCP_TOOL_TIMEOUT_MS", "600000")
    env.setdefault("LOBSTER_OPENCLAW_MCP_SDK_TIMEOUT_MS", env.get("LOBSTER_OPENCLAW_MCP_TOOL_TIMEOUT_MS", "600000"))
    return env


def _nodejs_bundle_dir() -> Path:
    """含 node.exe 与 node_modules 的目录，默认同仓库 `nodejs/`，可用环境变量覆盖。"""
    raw = (os.environ.get("LOBSTER_NODEJS_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (_BASE_DIR / "nodejs").resolve()


def _find_openclaw_entry() -> Optional[tuple]:
    """Find node executable and openclaw.mjs path. Returns (node_path, mjs_path) or None."""
    import shutil

    bundle = _nodejs_bundle_dir()
    base = _BASE_DIR

    seen_mjs: set[Path] = set()
    mjs_candidates: list[Path] = [
        bundle / "node_modules" / "openclaw" / "openclaw.mjs",
        base / "nodejs" / "node_modules" / "openclaw" / "openclaw.mjs",
        base / "node_modules" / "openclaw" / "openclaw.mjs",
    ]
    mjs_path = None
    for p in mjs_candidates:
        try:
            r = p.resolve()
        except Exception:
            r = p
        if r in seen_mjs:
            continue
        seen_mjs.add(r)
        if p.exists():
            mjs_path = str(p)
            break

    node_path = None
    if platform.system() == "Windows":
        for p in (bundle / "node.exe", bundle / "node", base / "nodejs" / "node.exe", base / "nodejs" / "node"):
            if p.exists():
                node_path = str(p)
                break
        if not node_path:
            node_path = shutil.which("node")
    else:
        # macOS/Linux：包内 node.exe 常为 Windows PE，不可执行；勿选用。
        for p in (bundle / "node", base / "nodejs" / "node"):
            if p.exists() and os.access(p, os.X_OK):
                node_path = str(p)
                break
        if not node_path:
            node_path = shutil.which("node")

    if not (node_path and mjs_path):
        logger.warning(
            "[openclaw] 未解析到入口：node=%r openclaw.mjs=%r bundle=%s（可设置 LOBSTER_NODEJS_DIR）",
            node_path,
            mjs_path,
            bundle,
        )
    if node_path and mjs_path:
        return (node_path, mjs_path)
    return None


def _resolve_nodejs_bundle_node_path(bundle: Path) -> Optional[str]:
    import shutil

    if platform.system() == "Windows":
        for p in (bundle / "node.exe", bundle / "node"):
            if p.exists():
                return str(p.resolve())
        return shutil.which("node")
    bundled = bundle / "node"
    if bundled.exists() and os.access(bundled, os.X_OK):
        return str(bundled.resolve())
    return shutil.which("node")


def _nodejs_bundle_deps_ready(bundle: Path) -> bool:
    oc = bundle / "node_modules" / "openclaw" / "openclaw.mjs"
    wx = bundle / "node_modules" / "@tencent-weixin" / "openclaw-weixin" / "package.json"
    return oc.is_file() and wx.is_file()


def _nodejs_npm_spawn_ready(bundle: Path) -> bool:
    """OpenClaw 在 Windows 上会走 node_modules/npm/bin（含 npm-prefix.js），不能只存在半截 npm。"""
    nb = bundle / "node_modules" / "npm" / "bin"
    lib = bundle / "node_modules" / "npm" / "lib" / "cli.js"
    return (nb / "npm-cli.js").is_file() and (nb / "npm-prefix.js").is_file() and lib.is_file()


def _nodejs_npm_cache_ready(bundle: Path) -> bool:
    nb = bundle / ".openclaw" / "npm" / "bin"
    lib = bundle / ".openclaw" / "npm" / "lib" / "cli.js"
    return (nb / "npm-cli.js").is_file() and (nb / "npm-prefix.js").is_file() and lib.is_file()


def _sync_nodejs_npm_from_cache(bundle: Path) -> Optional[str]:
    """Restore node_modules/npm from bundled .openclaw/npm without network access."""
    src = bundle / ".openclaw" / "npm"
    dst = bundle / "node_modules" / "npm"
    if not _nodejs_npm_cache_ready(bundle):
        return "nodejs/.openclaw/npm cache is incomplete; cannot repair npm CLI offline"
    err = _rmtree_best_effort(dst)
    if err:
        return err
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
    except Exception as exc:
        return f"offline npm CLI sync failed: {exc}"
    if not _nodejs_npm_spawn_ready(bundle):
        return "npm CLI is still incomplete after offline sync"
    return None


def _ensure_nodejs_npm_spawn_ready_for_gateway(startup_log_path: Path) -> bool:
    """Keep Gateway startup local-only: sync cached npm if possible, never download."""
    bundle = _nodejs_bundle_dir()
    if _nodejs_npm_spawn_ready(bundle):
        _write_openclaw_startup_diag_line(
            startup_log_path,
            "npm_spawn_ready",
            bundle=str(bundle),
            source="node_modules",
        )
        return True
    _write_openclaw_startup_diag_line(
        startup_log_path,
        "npm_spawn_missing",
        bundle=str(bundle),
        cache_ready=_nodejs_npm_cache_ready(bundle),
    )
    if _nodejs_npm_cache_ready(bundle):
        err = _sync_nodejs_npm_from_cache(bundle)
        _write_openclaw_startup_diag_line(
            startup_log_path,
            "npm_spawn_cache_sync",
            ok=not err,
            error=err or "",
        )
        if not err:
            logger.info("[openclaw] restored npm CLI from bundled cache before Gateway launch")
            return True
        logger.warning("[openclaw] failed to restore npm CLI from cache before Gateway launch: %s", err)
    message = "nodejs/npm CLI is incomplete; continuing Gateway launch without startup download."
    logger.warning("[openclaw] %s", message)
    _write_openclaw_startup_diag_line(
        startup_log_path,
        "npm_spawn_unavailable",
        message=message,
    )
    return True


def _rmtree_best_effort(path: Path) -> Optional[str]:
    """删除目录；失败时返回简短说明。处理 Windows 只读文件。"""
    if not path.exists():
        return None

    def _onerror(func: Any, p: str, _exc: Any) -> None:
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            raise

    try:
        shutil.rmtree(path, onerror=_onerror)
    except OSError as e:
        return f"无法删除 {path}（可能被占用或权限不足）：{e}"
    return None


def _purge_npm_for_resync(bundle: Path, *, include_openclaw_cache: bool) -> Optional[str]:
    """授权时自动清理，无需手删。可先只删 node_modules/npm、保留 .openclaw/npm，降低对既有功能与无谓重下的影响；仍失败再带 include_openclaw_cache=True。"""
    paths = [bundle / "node_modules" / "npm"]
    if include_openclaw_cache:
        paths.append(bundle / ".openclaw" / "npm")
    for p in paths:
        existed = p.exists()
        err = _rmtree_best_effort(p)
        if err:
            return err
        if existed:
            logger.info("[nodejs-deps] purged %s", p)
    return None


def _ensure_nodejs_deps_download_if_needed(
    job_id: Optional[str],
    log_buf: Optional[list[str]],
    phase_prefix: str,
) -> Optional[str]:
    """修复 node_modules/npm 供 OpenClaw 装插件；必要时再在线安装 openclaw / 微信包。清理粒度递进，避免无谓动到健康缓存与其它依赖。"""
    bundle = _nodejs_bundle_dir()
    ensure_mjs = bundle / "ensure-npm-cli.mjs"
    run_npm_mjs = bundle / "run-npm.mjs"
    if not ensure_mjs.is_file() or not run_npm_mjs.is_file():
        return "缺少 ensure-npm-cli.mjs 或 run-npm.mjs，请更新客户端。"

    need_install = not _nodejs_bundle_deps_ready(bundle)
    need_npm_sync = not _nodejs_npm_spawn_ready(bundle)
    node_path = _resolve_nodejs_bundle_node_path(bundle)
    if not node_path and (need_install or need_npm_sync):
        return (
            "未找到 Node 可执行文件。请使用含 node.exe 的完整安装包，"
            "或设置环境变量 LOBSTER_NODEJS_DIR 指向含 node.exe 的 nodejs 目录。"
        )
    if not node_path:
        return None

    env = _build_openclaw_env()

    def announce(msg: str) -> None:
        text = f"{phase_prefix}{msg}" if phase_prefix else msg
        if job_id:
            _weixin_job_update(job_id, status="running", message=text)
        logger.info("[nodejs-deps] %s", text)

    def run_argv(argv: list[str], timeout: float, label: str) -> Optional[str]:
        announce(label)
        popen_kw: Dict[str, Any] = {
            "cwd": str(bundle),
            "env": env,
            "capture_output": True,
            "text": True,
            "timeout": timeout,
            "errors": "replace",
        }
        if platform.system() == "Windows":
            popen_kw["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[arg-type]
        try:
            r = subprocess.run(argv, **popen_kw)
        except subprocess.TimeoutExpired:
            return f"{label} 超时，请检查网络后重试。"
        if log_buf is not None:
            out = (r.stdout or "") + (r.stderr or "")
            if out.strip():
                log_buf.append(out[-12_000:])
        if r.returncode != 0:
            tail = ((r.stderr or "") + (r.stdout or ""))[-1800:]
            logger.warning("[nodejs-deps] %s exit=%s tail=%s", label, r.returncode, tail[-600:])
            short = " ".join(tail.strip().split())[:360]
            return f"{label} 失败（请检查网络与磁盘权限）。{short}"
        return None

    if need_npm_sync:
        announce("正在清理不完整的 npm 安装目录（保留本地 npm 缓存，不影响已正常的其他依赖）…")
        purge_err = _purge_npm_for_resync(bundle, include_openclaw_cache=False)
        if purge_err:
            return purge_err
        for attempt in (1, 2):
            label = (
                "正在准备完整 npm CLI（供 OpenClaw 安装插件，首次可能下载较慢）…"
                if attempt == 1
                else "npm 仍不完整，已连同缓存一并清理并重试…"
            )
            err = run_argv([node_path, str(ensure_mjs)], 300.0, label)
            if err:
                return err
            if _nodejs_npm_spawn_ready(bundle):
                break
            if attempt >= 2:
                return "npm CLI 仍不完整，请关闭占用 nodejs 目录的程序后，再次点击授权。"
            purge_err = _purge_npm_for_resync(bundle, include_openclaw_cache=True)
            if purge_err:
                return purge_err

    if not need_install:
        return None

    err = run_argv([node_path, str(ensure_mjs)], 240.0, "正在确认 npm 与 OpenClaw 安装环境…")
    if err:
        return err
    err = run_argv(
        [node_path, str(run_npm_mjs), "install", "--no-fund", "--no-audit"],
        900.0,
        "正在在线安装 OpenClaw 与微信插件（约 1～5 分钟，请稍候）…",
    )
    if err:
        return err
    err = run_argv([node_path, str(ensure_mjs)], 180.0, "正在将完整 npm 同步回 node_modules…")
    if err:
        return err
    if not _nodejs_bundle_deps_ready(bundle):
        return "依赖安装后仍未检测到 openclaw 或微信插件，请再次点击授权。"
    if not _nodejs_npm_spawn_ready(bundle):
        announce("依赖已装好但 npm 仍异常，先仅清理 spawn 目录并重试同步…")
        purge_err = _purge_npm_for_resync(bundle, include_openclaw_cache=False)
        if purge_err:
            return purge_err
        err = run_argv(
            [node_path, str(ensure_mjs)],
            300.0,
            "正在将完整 npm 同步回 node_modules…",
        )
        if err:
            return err
        if not _nodejs_npm_spawn_ready(bundle):
            announce("仍异常，将清除 npm 缓存后最后一次重试…")
            purge_err = _purge_npm_for_resync(bundle, include_openclaw_cache=True)
            if purge_err:
                return purge_err
            err = run_argv(
                [node_path, str(ensure_mjs)],
                300.0,
                "正在重新下载并同步 npm CLI…",
            )
            if err:
                return err
        if not _nodejs_npm_spawn_ready(bundle):
            return "npm CLI 仍未就绪，请关闭占用程序后再次点击授权。"
    return None


def _line_https_url(line: str) -> Optional[str]:
    s = (line or "").strip()
    if "https://" not in s:
        return None
    m = re.search(r"(https://[^\s\]\)\"'<>]+)", s)
    if not m:
        return None
    return m.group(1).rstrip(").,;'")


def _likely_weixin_qr_url(url: str) -> bool:
    u = url.lower()
    if "weixin.qq.com" in u or "ilink" in u or "wechat" in u:
        return True
    return len(url) >= 40


def _weixin_job_is_terminal(st: str) -> bool:
    return st in ("success", "failed", "timeout")


def _weixin_job_snapshot(job_id: str) -> Dict[str, Any]:
    with _WEIXIN_LOGIN_LOCK:
        j = dict(_weixin_login_jobs.get(job_id) or {})
    j.pop("log_tail", None)
    tail = ""
    with _WEIXIN_LOGIN_LOCK:
        raw = _weixin_login_jobs.get(job_id) or {}
        tail = str(raw.get("log_tail") or "")
    if tail:
        j["log_tail"] = tail[-4000:]
    return j


def _weixin_job_update(job_id: str, **kwargs: Any) -> None:
    with _WEIXIN_LOGIN_LOCK:
        cur = _weixin_login_jobs.get(job_id)
        if not cur:
            return
        cur.update({k: v for k, v in kwargs.items() if v is not None or k in ("qrcode_url", "message")})
        if "log_tail" in kwargs and kwargs["log_tail"] is not None:
            lt = str(kwargs["log_tail"])
            cur["log_tail"] = lt[-12_000:]


def _write_weixin_login_ledger(job_id: str, ok: bool, detail: str = "") -> None:
    try:
        _OC_DIR.mkdir(parents=True, exist_ok=True)
        _WEIXIN_LEDGER.write_text(
            json.dumps(
                {
                    "ok": ok,
                    "job_id": job_id,
                    "at": datetime.now(timezone.utc).isoformat(),
                    "detail": (detail or "")[:800],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("[weixin-login] ledger write failed: %s", e)


def _weixin_run_sync_openclaw_step(
    job_id: str,
    node_path: str,
    mjs_path: str,
    env: dict,
    log_buf: list[str],
    message: str,
    argv_tail: list[str],
    timeout_sec: float = 300.0,
) -> int:
    """Run a single non-interactive openclaw CLI step; append output to log_buf and job log_tail."""
    cmd = [node_path, mjs_path] + argv_tail
    _weixin_job_update(job_id, status="running", message=message, log_tail="".join(log_buf[-200:]))
    kwargs: Dict[str, Any] = dict(
        cwd=str(_BASE_DIR),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
    )
    if platform.system() == "Windows":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    try:
        proc = subprocess.run(cmd, **kwargs)
    except subprocess.TimeoutExpired:
        log_buf.append(f"\n[timeout] {' '.join(argv_tail[:4])}…\n")
        tail = "".join(log_buf[-200:])
        _weixin_job_update(job_id, log_tail=tail)
        return -99
    combined = (proc.stdout or "") + (proc.stderr or "")
    if combined.strip():
        for line in combined.splitlines():
            log_buf.append(line + "\n")
    tail = "".join(log_buf[-200:])
    _weixin_job_update(job_id, log_tail=tail)
    return int(proc.returncode)


def _weixin_run_prep_before_login(job_id: str, node_path: str, mjs_path: str, env: dict, log_buf: list[str]) -> None:
    """腾讯微信插件 README：① plugins install ② config set enabled ③ channels login（流式）④ gateway restart（成功后执行）。"""
    bundled = _BASE_DIR / "nodejs" / "node_modules" / "@tencent-weixin" / "openclaw-weixin"
    install_argv = (
        ["plugins", "install", str(bundled)]
        if bundled.is_dir()
        else ["plugins", "install", "@tencent-weixin/openclaw-weixin"]
    )
    rc1 = _weixin_run_sync_openclaw_step(
        job_id,
        node_path,
        mjs_path,
        env,
        log_buf,
        "① openclaw plugins install（微信插件）…",
        install_argv,
    )
    if rc1 not in (0, -99):
        logger.warning("[weixin-login] plugins install exit=%s, continue to config/login", rc1)
    rc2 = _weixin_run_sync_openclaw_step(
        job_id,
        node_path,
        mjs_path,
        env,
        log_buf,
        "② openclaw config set plugins.entries.openclaw-weixin.enabled true…",
        ["config", "set", "plugins.entries.openclaw-weixin.enabled", "true"],
        timeout_sec=120.0,
    )
    if rc2 not in (0, -99):
        logger.warning("[weixin-login] config set exit=%s, continue to channels login", rc2)


def _weixin_login_worker(job_id: str) -> None:
    global _weixin_login_active_job_id
    log_buf_early: list[str] = []
    err_deps = _ensure_nodejs_deps_download_if_needed(job_id, log_buf_early, "")
    if err_deps:
        _weixin_job_update(job_id, status="failed", message=err_deps, log_tail="".join(log_buf_early)[-4000:])
        _write_weixin_login_ledger(job_id, False, err_deps[:500])
        with _WEIXIN_LOGIN_LOCK:
            if _weixin_login_active_job_id == job_id:
                _weixin_login_active_job_id = None
        return

    entry = _find_openclaw_entry()
    if not entry:
        _weixin_job_update(
            job_id,
            status="failed",
            message=(
                "未找到 node 或 openclaw.mjs。请确认已使用完整安装包；"
                "若 nodejs 不在默认目录，请设置环境变量 LOBSTER_NODEJS_DIR。"
            ),
        )
        _write_weixin_login_ledger(job_id, False, "no openclaw entry")
        with _WEIXIN_LOGIN_LOCK:
            if _weixin_login_active_job_id == job_id:
                _weixin_login_active_job_id = None
        return

    node_path, mjs_path = entry
    _ensure_openclaw_json_for_local_launch(_OPENCLAW_PLUGIN_MODE_WEIXIN)
    env = _build_openclaw_env()
    cmd = [node_path, mjs_path, "channels", "login", "--channel", "openclaw-weixin"]
    log_buf: list[str] = []
    qrcode_sent = False
    rc: Optional[int] = None
    timer: Optional[threading.Timer] = None

    def _kill_proc() -> None:
        proc = _WEIXIN_LOGIN_PROC_HOLDER.get("proc")
        if proc is not None and proc.poll() is None:
            logger.warning("[weixin-login] timeout, killing pid=%s", proc.pid)
            try:
                proc.kill()
            except OSError as e:
                logger.warning("[weixin-login] kill failed: %s", e)
            _weixin_job_update(job_id, status="timeout", message="等待扫码超时，请关闭窗口后重试")
            _write_weixin_login_ledger(job_id, False, "timeout")

    try:
        _weixin_run_prep_before_login(job_id, node_path, mjs_path, env, log_buf)
        _weixin_job_update(job_id, status="running", message="③ openclaw channels login --channel openclaw-weixin（等待控制台输出二维码链接）…")
        kwargs: Dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": env,
            "cwd": str(_BASE_DIR),
        }
        if platform.system() == "Windows":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

        proc = subprocess.Popen(cmd, **kwargs)
        _WEIXIN_LOGIN_PROC_HOLDER["proc"] = proc
        timer = threading.Timer(float(_WEIXIN_LOGIN_MAX_SEC), _kill_proc)
        timer.daemon = True
        timer.start()

        if proc.stdout:
            for line in proc.stdout:
                log_buf.append(line)
                tail = "".join(log_buf[-200:])
                _weixin_job_update(job_id, log_tail=tail)
                url = _line_https_url(line)
                if url and not qrcode_sent and _likely_weixin_qr_url(url):
                    qrcode_sent = True
                    _weixin_job_update(
                        job_id,
                        status="qrcode_ready",
                        qrcode_url=url,
                        message="③ 已取得二维码链接：请扫下方页面内二维码，或在浏览器打开链接。",
                    )

        rc = proc.wait()
    except Exception as e:
        logger.exception("[weixin-login] worker error job_id=%s", job_id)
        _weixin_job_update(job_id, status="failed", message=str(e)[:500])
        _write_weixin_login_ledger(job_id, False, str(e)[:500])
    finally:
        if timer:
            timer.cancel()
        _WEIXIN_LOGIN_PROC_HOLDER["proc"] = None
        with _WEIXIN_LOGIN_LOCK:
            st_now = (_weixin_login_jobs.get(job_id) or {}).get("status")
        if st_now not in ("timeout", "failed") and rc is not None:
            if rc == 0:
                _weixin_job_update(job_id, status="success", message="微信渠道已登录，凭证已写入 OpenClaw 状态目录")
                _write_weixin_login_ledger(job_id, True, "channels login exit 0")
                try:
                    restarted = _restart_openclaw_gateway(plugin_mode=_OPENCLAW_PLUGIN_MODE_WEIXIN)
                    _weixin_job_update(
                        job_id,
                        gateway_restarted=restarted,
                        message=(
                            "④ 已完成：微信渠道已登录，已重启 OpenClaw Gateway。"
                            if restarted
                            else "④ 微信渠道已登录，但自动重启 Gateway 失败，请手动执行 openclaw gateway restart。"
                        ),
                    )
                except Exception as e:
                    logger.warning("[weixin-login] restart after login: %s", e)
                    _weixin_job_update(job_id, gateway_restarted=False, message=f"登录成功但重启 Gateway 异常：{e!s}"[:400])
            else:
                msg = f"登录进程退出码 {rc}，请查看下方日志或 openclaw.log"
                _weixin_job_update(job_id, status="failed", message=msg)
                _write_weixin_login_ledger(job_id, False, msg)
        with _WEIXIN_LOGIN_LOCK:
            if _weixin_login_active_job_id == job_id:
                _weixin_login_active_job_id = None


def _restart_openclaw_gateway_impl(
    wait_ready_sec: float = 30.0,
    plugin_mode: str = _OPENCLAW_PLUGIN_MODE_LEAN,
) -> bool:
    """在已持有 _OPENCLAW_RESTART_LOCK 时调用：杀光监听 PID，等端口释放，再启动唯一 Gateway。"""
    started_at = time.perf_counter()
    for pid in sorted(set(_find_listener_pids_on_18789()) | set(_find_openclaw_gateway_process_pids())):
        logger.info("Killing OpenClaw Gateway PID %s", pid)
        _kill_pid(pid)
    _wait_until_no_listener_on_18789(6.0)
    _wait_until_no_openclaw_gateway_processes(6.0)
    leftover = sorted(set(_find_listener_pids_on_18789()) | set(_find_openclaw_gateway_process_pids()))
    if leftover:
        logger.warning("OpenClaw Gateway PIDs still alive after kill: %s — retrying SIGKILL", leftover)
        for pid in leftover:
            _kill_pid(pid)
        _wait_until_no_listener_on_18789(4.0)
        _wait_until_no_openclaw_gateway_processes(4.0)
    logger.info("OpenClaw Gateway preflight cleanup took %.2fs", time.perf_counter() - started_at)

    # nodejs/npm 与 OpenClaw 依赖仅在「微信授权」流程中在线安装，启动/Gateway 重启不在此下载，以免拖死服务启动。
    entry = _find_openclaw_entry()
    if not entry:
        logger.warning("Cannot restart OpenClaw: node or openclaw.mjs not found")
        return False

    _ensure_openclaw_json_for_local_launch(plugin_mode)
    removed_staging = _cleanup_openclaw_install_stage_dirs()
    if removed_staging:
        logger.info("Removed OpenClaw staging extension leftovers before launch: %s", removed_staging)
    logger.info("OpenClaw Gateway config/dependency preflight took %.2fs", time.perf_counter() - started_at)

    node_path, mjs_path = entry
    _patch_openclaw_gateway_slack_stage(mjs_path)
    _patch_openclaw_gateway_pricing_refresh(mjs_path)
    _patch_openclaw_mcp_tool_timeout(mjs_path)
    _patch_openclaw_mcp_sdk_timeout(mjs_path)
    _patch_openclaw_gateway_startup_preflight(mjs_path)
    _patch_openclaw_latency_trace(mjs_path)
    env = _build_openclaw_env()
    startup_log_path = _new_openclaw_startup_log_path()

    try:
        cmd = [node_path, mjs_path, "gateway", "--port", "18789"]
        startup_log_path.parent.mkdir(parents=True, exist_ok=True)
        if platform.system() == "Windows":
            _ensure_nodejs_npm_spawn_ready_for_gateway(startup_log_path)
        _write_openclaw_startup_diag_line(
            startup_log_path,
            "launch_prepare",
            cmd=cmd,
            cwd=str(_BASE_DIR),
            node_path=str(node_path),
            mjs_path=str(mjs_path),
            plugin_mode=plugin_mode,
            openclaw_version=_openclaw_package_version(mjs_path) or "unknown",
            env=_safe_startup_env_summary(env),
            preflight_elapsed_sec=round(time.perf_counter() - started_at, 3),
        )
        _remember_openclaw_startup_diag(
            status="launching",
            reason="launch_prepare",
            log_path=str(startup_log_path),
            plugin_mode=plugin_mode,
            openclaw_version=_openclaw_package_version(mjs_path) or "unknown",
        )
        startup_log_file = startup_log_path.open("a", encoding="utf-8", errors="replace")

        kwargs = {
            "stdout": startup_log_file,
            "stderr": startup_log_file,
            "env": env,
            "cwd": str(_BASE_DIR),
        }
        if platform.system() == "Windows":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        popen_started_at = time.perf_counter()
        proc = subprocess.Popen(cmd, **kwargs)
        try:
            startup_log_file.close()
        except Exception:
            pass
        time.sleep(0.2)
        gateway_processes = _find_openclaw_gateway_process_infos()
        _write_openclaw_startup_diag_line(
            startup_log_path,
            "process_started",
            pid=proc.pid,
            gateway_pids=[int(info["pid"]) for info in gateway_processes if info.get("pid")],
            gateway_processes=gateway_processes,
        )
        _remember_openclaw_startup_diag(
            status="waiting_for_listener",
            pid=proc.pid,
            log_path=str(startup_log_path),
        )
        logger.info(
            "OpenClaw Gateway restarting: %s (openclaw=%s plugin_mode=%s)",
            " ".join(cmd),
            _openclaw_package_version(mjs_path) or "unknown",
            plugin_mode,
        )

        new_pid = _wait_for_openclaw_gateway_ready(wait_ready_sec, proc, startup_log_path)
        if new_pid:
            _write_openclaw_startup_diag_line(
                startup_log_path,
                "launch_success",
                pid=new_pid,
                node_ready_sec=round(time.perf_counter() - popen_started_at, 3),
                total_sec=round(time.perf_counter() - started_at, 3),
            )
            logger.info(
                "OpenClaw Gateway restarted, PID %s, node_ready=%.2fs total=%.2fs",
                new_pid,
                time.perf_counter() - popen_started_at,
                time.perf_counter() - started_at,
            )
            return True
        logger.warning("OpenClaw Gateway process started but not ready after %.1fs", wait_ready_sec)
        leftover_processes = _find_openclaw_gateway_process_infos()
        leftovers = sorted(set(_find_listener_pids_on_18789()) | {int(info["pid"]) for info in leftover_processes if info.get("pid")})
        if leftovers:
            logger.warning("Killing OpenClaw Gateway PIDs after readiness timeout: %s", leftovers)
            _write_openclaw_startup_diag_line(
                startup_log_path,
                "kill_after_timeout",
                leftovers=leftovers,
                gateway_processes=leftover_processes,
                wait_ready_sec=wait_ready_sec,
            )
            for pid in leftovers:
                _kill_pid(pid)
            _wait_until_no_listener_on_18789(4.0)
            _wait_until_no_openclaw_gateway_processes(4.0)
        return False
    except Exception as e:
        logger.error("Failed to restart OpenClaw Gateway: %s", e)
        _write_openclaw_startup_diag_line(startup_log_path, "launch_exception", error=str(e))
        _remember_openclaw_startup_diag(status="failed", reason="launch_exception", error=str(e), log_path=str(startup_log_path))
        return False


def _restart_openclaw_gateway(
    wait_ready_sec: float = 30.0,
    plugin_mode: str = _OPENCLAW_PLUGIN_MODE_LEAN,
) -> bool:
    """串行重启，避免「清除配置」与「保存 Key」等并发各拉起一个 node。"""
    with _OPENCLAW_RESTART_LOCK:
        return _restart_openclaw_gateway_impl(wait_ready_sec=wait_ready_sec, plugin_mode=plugin_mode)


def _ensure_openclaw_gateway_running(
    wait_ready_sec: float = 30.0,
    plugin_mode: str = _OPENCLAW_PLUGIN_MODE_LEAN,
) -> bool:
    """Start/restart Gateway only if it is still needed after acquiring the lock."""
    with _OPENCLAW_RESTART_LOCK:
        pid = _find_openclaw_pid()
        restart_reasons = _openclaw_gateway_local_restart_reasons()
        if pid and not restart_reasons:
            return True
        logger.warning(
            "OpenClaw Gateway ensure will restart: pid=%s reasons=%s",
            pid,
            restart_reasons or ["missing_pid"],
        )
        return _restart_openclaw_gateway_impl(wait_ready_sec=wait_ready_sec, plugin_mode=plugin_mode)


@router.post("/api/openclaw/restart", summary="重启 OpenClaw Gateway")
async def restart_openclaw(current_user: _ServerUser = Depends(get_current_user_for_local)):
    ok = _restart_openclaw_gateway()
    if ok:
        return {"ok": True, "message": "OpenClaw Gateway 已重启"}
    return {"ok": False, "message": "重启失败，请手动执行 stop.bat + start.bat"}


def _prune_old_weixin_login_jobs() -> None:
    now = time.time()
    with _WEIXIN_LOGIN_LOCK:
        dead = [
            k
            for k, v in list(_weixin_login_jobs.items())
            if _weixin_job_is_terminal(str(v.get("status") or ""))
            and now - float(v.get("started_at") or 0) > 3600
        ]
        for k in dead:
            _weixin_login_jobs.pop(k, None)


@router.post("/api/openclaw/weixin-login/start", summary="启动 OpenClaw 微信插件扫码登录（本机子进程）")
async def openclaw_weixin_login_start(current_user: _ServerUser = Depends(get_current_user_for_local)):
    """等价于在项目根设置 OPENCLAW_CONFIG_PATH / OPENCLAW_STATE_DIR 后执行：
    node openclaw.mjs channels login --channel openclaw-weixin
    """
    global _weixin_login_active_job_id
    _prune_old_weixin_login_jobs()
    with _WEIXIN_LOGIN_LOCK:
        if _weixin_login_active_job_id:
            existing = _weixin_login_jobs.get(_weixin_login_active_job_id)
            if existing:
                st = str(existing.get("status") or "")
                if not _weixin_job_is_terminal(st):
                    age = time.time() - float(existing.get("started_at") or 0)
                    if age < float(_WEIXIN_LOGIN_MAX_SEC) + 180.0:
                        return {
                            "job_id": _weixin_login_active_job_id,
                            "status": st,
                            "reused": True,
                        }
        jid = uuid.uuid4().hex
        _weixin_login_jobs[jid] = {
            "job_id": jid,
            "status": "starting",
            "qrcode_url": None,
            "message": "正在启动…",
            "started_at": time.time(),
            "gateway_restarted": None,
        }
        _weixin_login_active_job_id = jid
    threading.Thread(target=_weixin_login_worker, args=(jid,), daemon=True).start()
    logger.info("[weixin-login] job started job_id=%s user_id=%s", jid, current_user.id)
    return {"job_id": jid, "status": "starting", "reused": False}


@router.get("/api/openclaw/weixin-login/status", summary="查询微信扫码登录任务状态")
async def openclaw_weixin_login_status(
    job_id: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    jid = (job_id or "").strip()
    if not jid:
        raise HTTPException(status_code=400, detail="缺少 job_id")
    with _WEIXIN_LOGIN_LOCK:
        if jid not in _weixin_login_jobs:
            raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return _weixin_job_snapshot(jid)


@router.get("/api/openclaw/weixin-login/last", summary="上次微信渠道登录结果摘要（本机 ledger）")
async def openclaw_weixin_login_last(current_user: _ServerUser = Depends(get_current_user_for_local)):
    if not _WEIXIN_LEDGER.exists():
        return {"last_ok": False, "at": None, "detail": ""}
    try:
        data = json.loads(_WEIXIN_LEDGER.read_text(encoding="utf-8"))
        return {
            "last_ok": bool(data.get("ok")),
            "at": data.get("at"),
            "detail": str(data.get("detail") or "")[:500],
        }
    except Exception:
        return {"last_ok": False, "at": None, "detail": "ledger 损坏"}


def clear_openclaw_local_provider_keys() -> tuple[bool, bool]:
    """从本机 openclaw/.env 移除各厂商 API Key（仅写本地文件，不上传任何服务端）。

    Returns:
        (env_changed, gateway_restarted)
    """
    env_data = _read_oc_env()
    changed = False
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY"):
        if k in env_data and (env_data[k] or "").strip():
            del env_data[k]
            changed = True
    if not changed:
        return False, False
    _write_oc_env(env_data)
    restarted = _restart_openclaw_gateway()
    return True, restarted


# --------------- SuTui MCP Config ---------------

_SUTUI_CONFIG_PATH = _BASE_DIR / "sutui_config.json"
_UPSTREAM_URLS_PATH = _BASE_DIR / "upstream_urls.json"
_SUTUI_DEFAULT_URL = "https://api.xskill.ai/api/v3/mcp-http"


def _read_sutui_config() -> dict:
    if not _SUTUI_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(_SUTUI_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_sutui_config(data: dict):
    _SUTUI_CONFIG_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _read_upstream_urls() -> dict:
    if not _UPSTREAM_URLS_PATH.exists():
        return {}
    try:
        return json.loads(_UPSTREAM_URLS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_upstream_urls(data: dict):
    _UPSTREAM_URLS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


@router.get("/api/sutui/config", summary="读取速推配置")
def get_sutui_config(current_user: _ServerUser = Depends(get_current_user_for_local)):
    from ..core.config import settings
    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    urls = _read_upstream_urls()
    url = urls.get("sutui", _SUTUI_DEFAULT_URL)
    if edition == "online":
        token = (getattr(current_user, "sutui_token", None) or "").strip()
        return {"token": _mask_key(token) if token else "", "has_token": bool(token), "url": url, "edition": "online"}
    cfg = _read_sutui_config()
    token = cfg.get("token", "")
    return {
        "token": _mask_key(token) if token else "",
        "has_token": bool(token),
        "url": url,
    }


class UpdateSutuiConfig(BaseModel):
    token: Optional[str] = None
    url: Optional[str] = None


@router.post("/api/sutui/config", summary="保存速推配置（本地）")
def update_sutui_config(
    body: UpdateSutuiConfig,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    from ..core.config import settings
    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    if edition == "online":
        if body.token is not None:
            raise HTTPException(400, detail="在线版 Token 由速推登录提供，无需在此配置")
    cfg = _read_sutui_config()
    if body.token is not None and edition != "online":
        cfg["token"] = body.token.strip()
    _write_sutui_config(cfg)

    if body.url is not None and body.url.strip():
        urls = _read_upstream_urls()
        urls["sutui"] = body.url.strip()
        _write_upstream_urls(urls)
    elif not _read_upstream_urls().get("sutui"):
        urls = _read_upstream_urls()
        urls["sutui"] = _SUTUI_DEFAULT_URL
        _write_upstream_urls(urls)

    return {"ok": True, "message": "速推配置已保存"}


@router.get("/api/sutui/balance", summary="速推余额（代理到认证中心）")
async def get_sutui_balance(request: Request):
    base = _auth_server_base()
    token = request.headers.get("Authorization") or ""
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{base}/api/sutui/balance", headers={"Authorization": token})
    from fastapi.responses import Response
    return Response(content=r.content, status_code=r.status_code, media_type="application/json")


# --------------- 速推模型与定价（代理到认证中心，与预扣/扣费同源） ---------------

@router.get("/api/sutui/models", summary="速推模型与定价（代理到认证中心）")
async def get_sutui_models(request: Request):
    base = _auth_server_base()
    token = request.headers.get("Authorization") or ""
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.get(f"{base}/api/sutui/models", headers={"Authorization": token})
    from fastapi.responses import Response
    return Response(content=r.content, status_code=r.status_code, media_type="application/json")


# --------------- 速推充值（对接速推真实接口：get_pay_info_list / create_wx_order_info）---------------

_XSKILL_RECHARGE_URL = "https://www.xskill.ai/#/cn-recharge"
_CUSTOM_CONFIGS_FILE = _BASE_DIR / "custom_configs.json"


def _default_recharge_shops():
    """默认充值档位（当 get_pay_info_list 失败时使用）。"""
    return [
        {"shop_id": 0, "money_yuan": 100, "title": "100 元 - 10000 算力"},
        {"shop_id": 0, "money_yuan": 300, "title": "300 元 - 30000 算力"},
        {"shop_id": 0, "money_yuan": 500, "title": "500 元 - 50000 算力"},
        {"shop_id": 0, "money_yuan": 1000, "title": "1000 元 - 100000 算力"},
    ]


def _get_custom_recharge_tiers() -> Optional[list]:
    """从 custom_configs.json 读取 RECHARGE_TIERS，用于自定义展示的档位、顺序和文案。
    格式: configs.RECHARGE_TIERS.shops = [ { \"shop_id\": 73, \"label\": \"1000元 推荐\", \"money_yuan\": 1000 }, ... ]。
    注意：实际支付金额由速推侧 shop_id 决定，label/money_yuan 仅用于展示；shop_id 需与速推商品一致。"""
    if not _CUSTOM_CONFIGS_FILE.exists():
        return None
    try:
        data = json.loads(_CUSTOM_CONFIGS_FILE.read_text(encoding="utf-8"))
        cfg = (data.get("configs") or {}).get("RECHARGE_TIERS")
        if not isinstance(cfg, dict):
            return None
        shops = cfg.get("shops")
        if not isinstance(shops, list) or not shops:
            return None
        out = []
        for s in shops:
            if not isinstance(s, dict):
                continue
            sid = s.get("shop_id")
            if sid is None:
                continue
            label = (s.get("label") or s.get("title") or "").strip() or f"{s.get('money_yuan', 0)} 元"
            money_yuan = s.get("money_yuan")
            if money_yuan is None:
                money_yuan = s.get("money")
                if isinstance(money_yuan, (int, float)) and money_yuan > 100:
                    money_yuan = money_yuan / 1000.0
            out.append({"shop_id": int(sid), "title": label, "money_yuan": float(money_yuan) if money_yuan is not None else 0, "tag": s.get("tag") or ""})
        return out if out else None
    except Exception as e:
        logger.debug("RECHARGE_TIERS read failed: %s", e)
        return None


@router.get("/api/sutui/recharge-options", summary="充值选项（代理到认证中心）")
async def get_sutui_recharge_options(request: Request):
    base = _auth_server_base()
    token = request.headers.get("Authorization") or ""
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{base}/api/sutui/recharge-options", headers={"Authorization": token})
    from fastapi.responses import Response
    return Response(content=r.content, status_code=r.status_code, media_type="application/json")


class RechargeCreateBody(BaseModel):
    shop_id: int
    amount_yuan: Optional[float] = None


def _auth_server_base() -> str:
    base = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="未配置 AUTH_SERVER_BASE")
    return base


@router.post("/api/sutui/recharge-create", summary="创建充值订单（代理到认证中心）")
async def create_sutui_recharge(body: RechargeCreateBody, request: Request):
    base = _auth_server_base()
    token = request.headers.get("Authorization") or ""
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{base}/api/sutui/recharge-create",
            json=body.model_dump(),
            headers={"Authorization": token, "Content-Type": "application/json"},
        )
    from fastapi.responses import Response
    return Response(content=r.content, status_code=r.status_code, media_type="application/json")
