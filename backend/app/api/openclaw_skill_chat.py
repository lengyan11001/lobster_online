from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..core.config import settings
from ..models import User
from ..services.openclaw_tool_scope import (
    HEADER_ALLOWED_CAPABILITIES,
    HEADER_ALLOWED_TOOLS,
    HEADER_INTENT,
)
from .auth import _ServerUser, get_current_user_for_chat, oauth2_scheme
from .mcp_gateway import set_mcp_token_for_agent, set_openclaw_tool_scope_for_agent
from .openclaw_config import (
    _find_openclaw_pid,
    _read_oc_config,
    _restart_openclaw_gateway,
    _write_oc_config,
)

router = APIRouter()
logger = logging.getLogger(__name__)
_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
_OC_DIR = _BASE_DIR / "openclaw"

_AUTO_APPROVAL_MAX_STEPS = 8
_TOOL_MARKUP_REPAIR_MAX_STEPS = 2
_SEMANTIC_REPAIR_MAX_STEPS = 4
_APPROVAL_COMMAND_RE = re.compile(
    r"/approve\s+([A-Za-z0-9][A-Za-z0-9._:-]*)\s+(?:allow-once|allow-always|always)\b",
    re.IGNORECASE,
)
_APPROVAL_REQUIRED_RE = re.compile(
    r"Approval required\s*\(id\s+([A-Za-z0-9][A-Za-z0-9._:-]*)",
    re.IGNORECASE,
)
_APPROVAL_NOISE_RE = re.compile(
    r"(Approval required|Reply with:\s*/approve|/approve\s+|allow-once|allow-always|需要批准|请批准|批准执行|审批码)",
    re.IGNORECASE,
)
_MESSAGE_FAILED_RE = re.compile(r"(?:⚠️\s*)?(?:✉️\s*)?Message:\s*`?[^`\r\n]+`?\s+failed", re.IGNORECASE)
_IMAGE_PATH_RE = re.compile(r"[A-Za-z]:\\[^\r\n`<>\"']+\.(?:png|jpe?g|webp|gif)", re.IGNORECASE)
_UNEXECUTED_TOOL_REPLY = (
    "OpenClaw 返回了未执行的工具调用文本，浏览器步骤没有继续执行。"
    "这通常是模型把工具调用当成普通文本输出了；请重新发送任务，我已记录这次异常。"
)


_SKILL_AGENT_IDS = ("lobster-browser-use", "lobster-computer-use")
_AUTO_EXEC_POLICY = {"host": "gateway", "security": "full", "ask": "off"}
_CONFIRM_EXEC_POLICY = {"host": "gateway", "security": "allowlist", "ask": "always"}
_SKILL_DENIED_TOOLS = (
    "browser",
    "canvas",
    "web_fetch",
    "web_search",
    "x_search",
    "read",
    "write",
    "memory_get",
    "memory_search",
    "nodes",
)
_SKILL_AGENT_TIMEOUT_SECONDS = 600
_SUTUI_WORKSPACE_MODEL_ID = "openclaw-skill-chat"
_SKILL_WORKSPACE_MODEL = f"lobster-sutui/{_SUTUI_WORKSPACE_MODEL_ID}"
_SUTUI_WORKSPACE_MODEL_ENTRY: Dict[str, Any] = {
    "id": _SUTUI_WORKSPACE_MODEL_ID,
    "name": "Sutui OpenClaw Skill Chat (server scheduled)",
    "reasoning": False,
    "input": ["text"],
    "contextWindow": 65536,
    "maxTokens": 8192,
}


class OpenClawSkillChatMessage(BaseModel):
    role: str = Field(..., description="user | assistant")
    content: str = Field(..., description="message content")


class OpenClawSkillChatRequest(BaseModel):
    skill_id: str = Field(..., description="browser_use_skill | computer_use_skill")
    message: str = Field(..., description="current user message")
    history: List[OpenClawSkillChatMessage] = Field(default_factory=list)
    model: Optional[str] = None


class OpenClawSkillExecConfigRequest(BaseModel):
    mode: str = Field("auto", description="auto | confirm")


class OpenClawSkillRuntimeCleanupRequest(BaseModel):
    skill_id: str = Field("browser_use_skill", description="browser_use_skill")


_SKILL_DEFS: Dict[str, Dict[str, str]] = {
    "browser_use_skill": {
        "name": "Browser Use",
        "agent_id": "lobster-browser-use",
        "intent": "openclaw_browser_use",
        "system": (
            "你正在龙虾的 Browser Use 独立工作台中。"
            "这里只处理网页浏览、网页测试、表单填写、页面信息提取、截图检查、公开网页内容下载等浏览器任务。"
            "优先使用 OpenClaw 已有浏览器能力和本工作台能力；缺少 URL、账号或关键确认信息时再简短追问。"
            "不要把 API key 或其它密钥写入脚本；如确实需要 LLM，优先使用系统已配置的模型或环境变量。"
            "普通浏览、搜索、读取公开内容、截图检查、下载任务素材、运行当前任务所需自动化脚本，不要向用户索要 /approve。"
            "涉及登录、支付、提交表单、删除、发布、修改账号或系统设置等高风险动作前，必须先说明将要执行的动作并等待用户确认。"
            "不要调用龙虾主聊天流程，也不要把任务改写成图片、视频生成或发布任务。"
            "浏览器执行必须使用 exec 工具运行 browser-use CLI，不要调用 browser 工具，也不要输出 DSML/XML/tool_calls 文本。"
            "本工作台的 browser、canvas、web_fetch、web_search、x_search 工具已禁用；网页访问、搜索、提取、保存文件都必须通过 exec 运行 browser-use 或 PowerShell 完成。"
            "不要调用 write 工具，也不要把脚本写成 DSML/XML；需要保存文件时直接通过 exec 运行 PowerShell 命令。"
            "当前 exec 运行在 Windows PowerShell，不要使用 &&，不要使用 chcp 65001 &&。"
            "每条 browser-use 命令都用这个前缀：$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'; browser-use ..."
            "本工作台需要让用户看见浏览器动作，首次打开网页优先执行：$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'; browser-use close; browser-use --headed open <url>"
            "browser-use CLI 没有 act、search、tab 命令；也不要使用 screenshot --save、extract --goal、PowerShell 的 && 或 ||。"
            "使用 open、state、click、input、type、keys、wait、get、screenshot、close 等命令；截图保存用 browser-use screenshot \"D:\\path\\file.png\"。"
            "For Weibo search tasks, prefer mobile public search URLs such as https://m.weibo.cn/search?containerid=100103type%3D1%26q%3DQUERY%26t%3D2; do not stop at desktop login pages."
            "你是执行型浏览器代理，不是只给建议的聊天助手。收到任务后要拆成步骤并连续执行，直到拿到可交付结果。"
            "打开网页、进入首页、看到搜索框、准备搜索都只是中间步骤，不算完成，不能作为最终回复。"
            "每次 browser-use 命令后都要继续 state/get/screenshot 观察页面，再决定下一步并继续执行。"
            "如果桌面站点出现访客限制、登录墙、验证码或空白页，先尝试同站点移动端、公开页面或可访问的搜索结果入口；仍被拦截时再保存阻塞证据。"
            "如果用户要求下载、保存、导出、整理内容，必须把提取到的内容保存到本地工作区文件，并在最终回复中给出完整文件路径。"
            "保存文本文件时可以用 PowerShell：New-Item -ItemType Directory -Force .\\downloads; Set-Content -LiteralPath .\\downloads\\结果.txt -Encoding UTF8 -Value \"内容\"。"
            "只有在任务完成、目标站要求登录/验证码、遇到必须用户确认的动作、或确实无法继续时，才输出最终自然语言回复。"
            "如果命令失败，直接用自然语言说明失败原因和下一步，不要把工具调用标记当回复文本。"
        ),
    },
    "computer_use_skill": {
        "name": "Computer Use",
        "agent_id": "lobster-computer-use",
        "intent": "openclaw_computer_use",
        "system": (
            "你正在龙虾的 Computer Use 独立工作台中。"
            "这里只处理本机或浏览器界面的观察、点击、输入、复制、检查状态等电脑操作任务。"
            "能直接操作时给出简短进度；需要识别屏幕、网页或应用状态时优先使用 OpenClaw 可用的电脑或浏览器能力。"
            "不要把 API key 或其它密钥写入脚本；如确实需要 LLM，优先使用系统已配置的模型或环境变量。"
            "普通观察、点击、输入、截图检查、运行当前任务所需自动化脚本，不要向用户索要 /approve。"
            "涉及登录、支付、提交、删除、发布、修改系统设置等高风险动作前，必须先说明将要执行的动作并等待用户确认。"
            "不要调用龙虾主聊天流程，也不要把任务改写成图片、视频生成或发布任务。"
            "操作桌面应用时必须先检查目标进程是否已存在；已存在则激活已有窗口，禁止重复启动新实例。"
            "截图和点击前必须把目标窗口切到前台并验证前台进程就是目标应用；验证失败时不要点击、不要声称完成。"
        ),
    },
}


def _installation_id_from_request(request: Request) -> str:
    return (
        request.headers.get("X-Installation-Id")
        or request.headers.get("x-installation-id")
        or ""
    ).strip()


def _normalize_exec_mode(mode: str) -> str:
    value = (mode or "").strip().lower()
    if value in {"auto", "off", "allow", "allow-always"}:
        return "auto"
    if value in {"confirm", "ask", "manual", "always"}:
        return "confirm"
    raise HTTPException(status_code=400, detail="mode 只支持 auto 或 confirm")


def _policy_for_mode(mode: str) -> Dict[str, str]:
    return dict(_AUTO_EXEC_POLICY if mode == "auto" else _CONFIRM_EXEC_POLICY)


def _ensure_lobster_sutui_workspace_model(config: Dict[str, Any]) -> bool:
    changed = False
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
    provider = providers.setdefault("lobster-sutui", {})
    if not isinstance(provider, dict):
        provider = {}
        providers["lobster-sutui"] = provider
        changed = True

    desired_base = "http://127.0.0.1:8000/internal/openclaw-sutui/v1"
    if provider.get("baseUrl") != desired_base:
        provider["baseUrl"] = desired_base
        changed = True
    if provider.get("api") != "openai-completions":
        provider["api"] = "openai-completions"
        changed = True
    proxy_key = (getattr(settings, "openclaw_sutui_proxy_key", None) or "").strip()
    if proxy_key and provider.get("apiKey") != proxy_key:
        provider["apiKey"] = proxy_key
        changed = True

    entries = provider.setdefault("models", [])
    if not isinstance(entries, list):
        entries = []
        provider["models"] = entries
        changed = True
    found = next((m for m in entries if isinstance(m, dict) and m.get("id") == _SUTUI_WORKSPACE_MODEL_ID), None)
    if not found:
        entries.append(dict(_SUTUI_WORKSPACE_MODEL_ENTRY))
        changed = True
    return changed


def _ensure_skill_agent_model_files() -> bool:
    changed = False
    desired_base = "http://127.0.0.1:8000/internal/openclaw-sutui/v1"
    proxy_key = (getattr(settings, "openclaw_sutui_proxy_key", None) or "").strip()
    for agent_id in _SKILL_AGENT_IDS:
        path = _OC_DIR / "agents" / agent_id / "agent" / "models.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("[openclaw_skill_chat] skip invalid agent models file: %s", path)
            continue
        if not isinstance(data, dict):
            continue
        providers = data.setdefault("providers", {})
        if not isinstance(providers, dict):
            providers = {}
            data["providers"] = providers
        provider = providers.setdefault("lobster-sutui", {})
        if not isinstance(provider, dict):
            provider = {}
            providers["lobster-sutui"] = provider
        before = json.dumps(data, ensure_ascii=False, sort_keys=True)
        provider["baseUrl"] = desired_base
        provider["api"] = "openai-completions"
        if proxy_key:
            provider["apiKey"] = proxy_key
        entries = provider.setdefault("models", [])
        if not isinstance(entries, list):
            entries = []
            provider["models"] = entries
        found = next((m for m in entries if isinstance(m, dict) and m.get("id") == _SUTUI_WORKSPACE_MODEL_ID), None)
        if not found:
            entries.append(dict(_SUTUI_WORKSPACE_MODEL_ENTRY))
        after = json.dumps(data, ensure_ascii=False, sort_keys=True)
        if after != before:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            changed = True
    return changed


def _ensure_computer_use_workspace_helper() -> bool:
    src = _BASE_DIR / "skills" / "computer_use_skill" / "focus_app_and_screenshot.ps1"
    dst = _OC_DIR / "workspace-lobster-computer-use" / "focus_app_and_screenshot.ps1"
    if not src.is_file():
        logger.warning("[openclaw_skill_chat] missing computer-use helper template: %s", src)
        return False
    try:
        content = src.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("[openclaw_skill_chat] read computer-use helper failed: %s", exc)
        return False
    try:
        if dst.is_file() and dst.read_text(encoding="utf-8") == content:
            return True
    except Exception:
        pass
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(content, encoding="utf-8")
    return True


def _agent_exec_mode(agent: Dict[str, Any]) -> str:
    exec_cfg = ((agent.get("tools") or {}).get("exec") or {}) if isinstance(agent, dict) else {}
    if not isinstance(exec_cfg, dict):
        return "missing"
    if all(str(exec_cfg.get(k) or "").strip().lower() == v for k, v in _AUTO_EXEC_POLICY.items()):
        return "auto"
    if str(exec_cfg.get("ask") or "").strip().lower() in {"always", "on-miss"}:
        return "confirm"
    return "custom"


def _ensure_skill_denied_tools(tools: Dict[str, Any]) -> bool:
    existing = tools.get("deny")
    merged: list[str] = []
    if isinstance(existing, list):
        for item in existing:
            value = str(item or "").strip()
            if value and value not in merged:
                merged.append(value)
    for tool_name in _SKILL_DENIED_TOOLS:
        if tool_name not in merged:
            merged.append(tool_name)
    if existing != merged:
        tools["deny"] = merged
        return True
    return False


def _skill_exec_config_snapshot(config: Dict[str, Any]) -> Dict[str, Any]:
    agents = config.get("agents", {}).get("list", [])
    if not isinstance(agents, list):
        agents = []
    rows: Dict[str, Dict[str, Any]] = {}
    modes: List[str] = []
    for agent_id in _SKILL_AGENT_IDS:
        found = next((a for a in agents if isinstance(a, dict) and a.get("id") == agent_id), None)
        mode = _agent_exec_mode(found or {})
        modes.append(mode)
        exec_cfg = ((found or {}).get("tools") or {}).get("exec") if isinstance(found, dict) else None
        rows[agent_id] = {
            "exists": bool(found),
            "mode": mode,
            "exec": exec_cfg if isinstance(exec_cfg, dict) else {},
            "deny": ((found or {}).get("tools") or {}).get("deny", []) if isinstance(found, dict) else [],
        }
    overall = "auto" if all(m == "auto" for m in modes) else "confirm" if all(m == "confirm" for m in modes) else "custom"
    return {
        "mode": overall,
        "agents": rows,
        "timeout_seconds": ((config.get("agents") or {}).get("defaults") or {}).get("timeoutSeconds")
        if isinstance(config, dict)
        else None,
        "gateway_online": bool(_find_openclaw_pid()),
    }


def _apply_skill_exec_config(mode: str = "auto", *, only_if_missing: bool = False) -> Dict[str, Any]:
    normalized = _normalize_exec_mode(mode)
    config = _read_oc_config()
    if not isinstance(config, dict):
        config = {}
    changed = _ensure_lobster_sutui_workspace_model(config)
    changed = _ensure_skill_agent_model_files() or changed
    agents_node = config.setdefault("agents", {})
    if not isinstance(agents_node, dict):
        agents_node = {}
        config["agents"] = agents_node
    defaults_node = agents_node.setdefault("defaults", {})
    if not isinstance(defaults_node, dict):
        defaults_node = {}
        agents_node["defaults"] = defaults_node
        changed = True
    try:
        current_timeout = int(defaults_node.get("timeoutSeconds") or 0)
    except (TypeError, ValueError):
        current_timeout = 0
    if current_timeout < _SKILL_AGENT_TIMEOUT_SECONDS:
        defaults_node["timeoutSeconds"] = _SKILL_AGENT_TIMEOUT_SECONDS
        changed = True
    agents = agents_node.setdefault("list", [])
    if not isinstance(agents, list):
        agents = []
        agents_node["list"] = agents

    desired = _policy_for_mode(normalized)
    for agent_id in _SKILL_AGENT_IDS:
        agent = next((a for a in agents if isinstance(a, dict) and a.get("id") == agent_id), None)
        if agent is None:
            agent = {"id": agent_id, "model": _SKILL_WORKSPACE_MODEL}
            agents.append(agent)
            changed = True
        if agent.get("model") != _SKILL_WORKSPACE_MODEL:
            agent["model"] = _SKILL_WORKSPACE_MODEL
            changed = True
        tools = agent.setdefault("tools", {})
        if not isinstance(tools, dict):
            tools = {}
            agent["tools"] = tools
            changed = True
        changed = _ensure_skill_denied_tools(tools) or changed
        exec_cfg = tools.setdefault("exec", {})
        if not isinstance(exec_cfg, dict):
            exec_cfg = {}
            tools["exec"] = exec_cfg
            changed = True
        already_configured = all(str(exec_cfg.get(k) or "").strip() for k in ("host", "security", "ask"))
        if only_if_missing and already_configured:
            continue
        for key, value in desired.items():
            if exec_cfg.get(key) != value:
                exec_cfg[key] = value
                changed = True

    if changed:
        _write_oc_config(config)
    snapshot = _skill_exec_config_snapshot(config)
    snapshot["changed"] = changed
    return snapshot


def _ensure_skill_exec_config() -> Dict[str, Any]:
    snapshot = _apply_skill_exec_config("auto", only_if_missing=True)
    snapshot["computer_use_helper_ready"] = _ensure_computer_use_workspace_helper()
    restarted = False
    if snapshot.get("changed"):
        restarted = _restart_openclaw_gateway()
        snapshot["gateway_online"] = bool(_find_openclaw_pid())
    snapshot["restarted"] = restarted
    return snapshot


def _upgrade_approval_mode(content: str) -> str:
    return _APPROVAL_COMMAND_RE.sub(
        lambda match: f"/approve {match.group(1)} allow-always",
        content or "",
    )


def _looks_like_unexecuted_tool_markup(content: str) -> bool:
    value = (content or "").strip().lower()
    if not value:
        return False
    return (
        ("dsml" in value and "tool_calls" in value)
        or "<tool_calls" in value
        or "</tool_calls" in value
        or "<invoke" in value
        or "</invoke" in value
    )


def _normalize_openclaw_reply(reply: str) -> str:
    value = (reply or "").strip()
    if not value:
        return value
    if _looks_like_unexecuted_tool_markup(value):
        logger.warning("[openclaw_skill_chat] unexecuted tool markup returned body=%s", value[:500])
        return _UNEXECUTED_TOOL_REPLY
    value = _MESSAGE_FAILED_RE.sub("", value).strip()
    return value


def _tool_markup_repair_message() -> str:
    return (
        "上一条回复包含未执行的 DSML/XML/tool_calls 文本，前端无法执行它。"
        "请立刻改用 OpenClaw 原生工具调用 exec 继续当前任务，不要输出解释、DSML、XML 或 tool_calls 文本。"
        "如果需要操作浏览器，只能通过 exec 运行 browser-use CLI。"
        "不要调用 write 工具；需要保存文件时用 exec 执行 PowerShell。"
        "browser-use 没有 search、tab、act 命令；搜索请使用 input/click/keys，或直接 open 可访问的移动端/公开搜索 URL。"
    )


def _extract_weibo_search_query(message: str) -> str:
    value = (message or "").strip()
    if "微博" not in value or "搜索" not in value:
        return ""
    match = re.search(r"搜索\s*([^，,。；;\s]+)", value)
    if match:
        return (match.group(1) or "").strip()
    return ""


def _looks_like_weibo_login_wall(reply: str) -> bool:
    value = (reply or "").strip().lower()
    if not value:
        return False
    login_terms = ("登录", "登陆", "login", "验证码", "二维码", "需要登录", "需要先登录")
    blocker_terms = ("无法", "不能", "需要", "拦截", "阻止", "visitor", "verify")
    return any(term in value for term in login_terms) and any(term in value for term in blocker_terms)


def _weibo_mobile_repair_message(query: str) -> str:
    encoded_query = quote((query or "五一旅游").strip(), safe="")
    mobile_url = f"https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D{encoded_query}%26t%3D2"
    return (
        "Do not treat the Weibo desktop login page as task completion. Continue the same task now.\n"
        f"Open the mobile public Weibo search page with exec: $env:PYTHONIOENCODING='utf-8'; "
        f"$env:PYTHONUTF8='1'; browser-use open \"{mobile_url}\"\n"
        "Then run browser-use state, open the first visible post or full-text entry, extract the post text with "
        "browser-use get text or browser-use get html plus PowerShell text cleanup, and save the result to "
        ".\\downloads\\weibo_search_first_post.txt using exec.\n"
        "Do not open s.weibo.com again. Do not use Refer parameters. Do not use browser-use extract, "
        "screenshot --save, PowerShell &&, or PowerShell ||. Keep executing until the downloaded text file exists."
    )


def _looks_like_incomplete_weibo_task(reply: str) -> bool:
    value = (reply or "").strip().lower()
    if not value:
        return False
    compact = re.sub(r"[\s.。…!！?？~～`'\"-]+", "", value)
    if compact in {"", "嗯", "好的", "ok", "收到", "在的"}:
        return True
    if value in {".", "..", "...", "……", "…"}:
        return True
    if ".txt" in value or "downloads" in value or "已保存" in value or "saved" in value:
        return False
    early_stop_terms = (
        "在的",
        "有什么需要",
        "有什么具体",
        "还需要",
        "继续浏览",
        "提取更多",
        "其他操作",
        "需要帮忙",
        "what specific",
        "what would you like",
        "anything else",
    )
    result_terms = ("微博", "weibo", "搜索结果", "search result")
    return any(term in value for term in early_stop_terms) and (
        any(term in value for term in result_terms) or len(value) < 200
    )


def _weibo_finish_download_message(query: str) -> str:
    return (
        "You stopped early. The user already asked for the first Weibo post and to download its content; "
        "do not ask what to do next.\n"
        "Continue from the current browser page. Use only exec commands.\n"
        "Run this kind of command first: $env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'; "
        "browser-use eval \"document.body.innerText.substring(0, 6000)\"\n"
        "From the visible text, identify the first actual post result for the search query "
        f"\"{query or '五一旅游'}\". Skip ad/topic/navigation blocks. Extract the author, time if visible, "
        "post body, and visible counts if available.\n"
        "Save it to .\\downloads\\weibo_search_first_post.txt with PowerShell Set-Content -Encoding UTF8. "
        "Do not use browser-use get-text, browser-use extract, screenshot --save, PowerShell &&, or PowerShell ||. "
        "If scrolling is needed, the valid syntax is browser-use scroll --amount 500 down. "
        "Do not answer with ellipses, acknowledgements, or questions. "
        "Your final reply must include the full saved file path and a short summary."
    )


def _weibo_download_path() -> Path:
    return _OC_DIR / "workspace-lobster-browser-use" / "downloads" / "weibo_search_first_post.txt"


def _ensure_weibo_reply_has_full_path(reply: str, query: str) -> str:
    value = (reply or "").strip()
    if not query:
        return value
    path = _weibo_download_path()
    if not path.exists():
        return value
    full_path = str(path)
    if full_path in value:
        return value
    return f"{value}\n\n本地文件：{full_path}".strip()


def _clean_history_content(role: str, content: str) -> str:
    value = (content or "").strip()
    if not value:
        return ""
    if _looks_like_unexecuted_tool_markup(value):
        return ""
    if role == "user" and _APPROVAL_COMMAND_RE.search(value):
        return ""
    if role == "assistant" and _APPROVAL_NOISE_RE.search(value):
        return ""
    if role == "assistant" and _MESSAGE_FAILED_RE.search(value):
        return ""
    return _upgrade_approval_mode(value)


def _skill_runtime_rules(skill_def: Dict[str, str]) -> str:
    agent_id = skill_def.get("agent_id") or ""
    common = (
        "Do not call read, write, memory_get, memory_search, nodes, browser, web_fetch, web_search, x_search, or canvas. "
        "Never answer with only ellipses, acknowledgements, or greetings. "
        "Never ask the user what to do next when the current message already contains the target action. "
    )
    if agent_id == "lobster-computer-use":
        return (
            "STRICT RULES FOR THIS INDEPENDENT COMPUTER USE CHAT: use exec plus Windows PowerShell/Python desktop automation only. "
            + common
            + "For desktop app tasks, do not use web/browser automation commands unless the user explicitly asks to operate a web page. "
            "Desktop app execution order is mandatory: 1) derive the process name from the exe path; "
            "2) run Get-Process for that process; if any live process has MainWindowHandle, DO NOT call Start-Process again; "
            "3) only if no live process/window exists, call Start-Process once and wait in a loop for MainWindowHandle; "
            "4) bring the target window to foreground with user32 ShowWindowAsync(handle, 9) and SetForegroundWindow(handle); "
            "5) verify the foreground window belongs to the target process using GetForegroundWindow plus GetWindowThreadProcessId; "
            "6) only after this verification may you capture a full-screen screenshot into .\\computer_use_<timestamp>.png; "
            "7) only click/type after a fresh foreground screenshot clearly shows the requested target. "
            "Prefer running the workspace helper first: powershell -ExecutionPolicy Bypass -File .\\focus_app_and_screenshot.ps1 -ExePath '<exe path>'; "
            "the helper already prevents duplicate launches, focuses the existing window, verifies foreground ownership, and returns JSON with screenshot. "
            "If foreground verification fails, do not screenshot stale Chrome/Codex windows, do not click, and report the failure plus the latest process/window title. "
            "For requests like clicking the first draft in Jianling/Jianying/Jianling.exe, never start a second app instance; focus the existing Jianling window first, "
            "then click the first visible draft tile only after it is visible in the foreground screenshot. "
            "If the target is not clear from the foreground screenshot, return the fresh screenshot path and ask one concise confirmation; "
            "do not claim that you clicked anything unless the post-click screenshot confirms the app opened the draft. "
            "When the user asks for a screenshot, the final answer must include the exact saved PNG/JPG path. "
            "Do not use any Message/file-send syntax; Lobster will render local image paths automatically. "
            "The task is not complete until the requested action succeeds and a post-action screenshot has been saved."
        )
    return (
        "STRICT RULES FOR THIS INDEPENDENT BROWSER USE CHAT: use exec plus browser-use/PowerShell only. "
        + common
        + "For Weibo search/download tasks, open m.weibo.cn public search, then inspect with "
        "browser-use state or browser-use eval \"document.body.innerText.substring(0, 6000)\". "
        "Do not use browser-use get-text, browser-use extract, screenshot --save, screenshot --path, "
        "PowerShell &&, or PowerShell ||. If scrolling is needed, use browser-use scroll --amount 500 down. "
        "If the user asks to download content, the task is not complete "
        "until a local file has been written under .\\downloads and your final answer includes that path. "
    )


def _safe_messages(payload: OpenClawSkillChatRequest, skill_def: Dict[str, str], auto_exec_enabled: bool) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = [{"role": "system", "content": skill_def["system"]}]
    messages.append(
        {
            "role": "system",
            "content": (
                "这个二级工作台会自动把普通 exec 审批处理为 allow-always。"
                "不要把审批码交给用户，也不要停下来等待用户批准。"
                "只有登录、支付、发布、删除、修改账号或系统设置等高风险动作，才需要先向用户确认。"
            ),
        }
    )
    messages.append(
        {
            "role": "system",
            "content": _skill_runtime_rules(skill_def),
        }
    )
    if not auto_exec_enabled:
        messages.append(
            {
                "role": "system",
                "content": "当前已切换为确认模式：如果 exec 返回审批请求，请把 /approve 命令原样交给用户确认，不要自动批准。",
            }
        )
    for item in (payload.history or [])[-20:]:
        role = (item.role or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = (item.content or "").strip()
        if not content:
            continue
        content = _clean_history_content(role, content)
        if not content:
            continue
        messages.append({"role": role, "content": content[:8000]})
    user_msg = (payload.message or "").strip()
    if not user_msg:
        raise HTTPException(status_code=400, detail="消息不能为空")
    if auto_exec_enabled:
        user_msg = _upgrade_approval_mode(user_msg)
    messages.append({"role": "user", "content": user_msg[:12000]})
    return messages


def _extract_openclaw_reply(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    request_id = data.get("id") or data.get("request_id")
    if request_id:
        logger.info("[openclaw_skill_chat] OpenClaw response id=%s", request_id)
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict):
                txt = part.get("text") or part.get("content")
                if isinstance(txt, str) and txt.strip():
                    parts.append(txt.strip())
        return "\n".join(parts).strip()
    return ""


async def _post_openclaw_chat(
    client: httpx.AsyncClient,
    oc_base: str,
    model: str,
    messages: List[Dict[str, str]],
    headers: Dict[str, str],
) -> str:
    resp = await client.post(
        f"{oc_base}/v1/chat/completions",
        json={
            "model": model,
            "messages": messages,
            "stream": False,
            "timeoutSeconds": _SKILL_AGENT_TIMEOUT_SECONDS,
        },
        headers=headers,
    )
    if resp.status_code != 200:
        body = (resp.text or "").replace("\n", " ").strip()
        if len(body) > 500:
            body = body[:500] + "..."
        logger.warning("[openclaw_skill_chat] OpenClaw HTTP %s body=%s", resp.status_code, body)
        raise HTTPException(status_code=502, detail=f"OpenClaw 返回异常：HTTP {resp.status_code}")

    try:
        data = resp.json()
    except Exception as exc:
        logger.warning("[openclaw_skill_chat] invalid JSON err=%s", exc)
        raise HTTPException(status_code=502, detail="OpenClaw 返回内容不是 JSON")

    reply = _extract_openclaw_reply(data)
    if not reply:
        raise HTTPException(status_code=502, detail="OpenClaw 未返回有效回复")
    return reply


def _extract_approval_request_id(reply: str) -> str:
    if not reply:
        return ""
    for pattern in (_APPROVAL_COMMAND_RE, _APPROVAL_REQUIRED_RE):
        match = pattern.search(reply)
        if match:
            return (match.group(1) or "").strip()
    return ""


def _run_browser_use_close_all() -> Dict[str, Any]:
    cmd = shutil.which("browser-use")
    if not cmd:
        return {"ok": False, "error": "browser-use command not found"}
    try:
        if cmd.lower().endswith((".cmd", ".bat")):
            args: Union[str, List[str]] = f'"{cmd}" close --all'
            use_shell = True
        else:
            args = [cmd, "close", "--all"]
            use_shell = False
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=use_shell,
            timeout=15,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "").strip()[:1000],
            "stderr": (proc.stderr or "").strip()[:1000],
        }
    except FileNotFoundError:
        return {"ok": False, "error": f"browser-use command not found: {cmd}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "browser-use close --all timed out"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _force_kill_browser_use_runtime() -> Dict[str, Any]:
    if os.name != "nt":
        return {"ok": True, "skipped": True, "reason": "windows-only cleanup"}

    ps_script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$pattern = 'browser_use\.skill_cli\.daemon|browser-use-user-data-dir-'
$names = @('python.exe', 'pythonw.exe', 'chrome.exe', 'msedge.exe')
$targets = @(Get-CimInstance Win32_Process | Where-Object {
  ($names -contains $_.Name) -and ($_.CommandLine -match $pattern)
})
$killed = @()
foreach ($p in $targets) {
  try {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
    $killed += [pscustomobject]@{ pid = [int]$p.ProcessId; name = [string]$p.Name }
  } catch {
    $killed += [pscustomobject]@{ pid = [int]$p.ProcessId; name = [string]$p.Name; error = [string]$_.Exception.Message }
  }
}
Start-Sleep -Milliseconds 300
$leftovers = @(Get-CimInstance Win32_Process | Where-Object {
  ($names -contains $_.Name) -and ($_.CommandLine -match $pattern)
} | ForEach-Object {
  [pscustomobject]@{ pid = [int]$_.ProcessId; name = [string]$_.Name }
})
[pscustomobject]@{ killed = $killed; leftovers = $leftovers } | ConvertTo-Json -Compress -Depth 4
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "force cleanup timed out"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    stdout = (proc.stdout or "").strip()
    parsed: Dict[str, Any] = {}
    if stdout:
        try:
            parsed = json.loads(stdout)
        except Exception:
            parsed = {"raw": stdout[:2000]}
    parsed["ok"] = proc.returncode == 0 and not parsed.get("leftovers")
    parsed["returncode"] = proc.returncode
    stderr = (proc.stderr or "").strip()
    if stderr:
        parsed["stderr"] = stderr[:1000]
    return parsed


def _cleanup_browser_use_runtime() -> Dict[str, Any]:
    close_result = _run_browser_use_close_all()
    force_result = _force_kill_browser_use_runtime()
    leftovers = force_result.get("leftovers") if isinstance(force_result, dict) else []
    if not isinstance(leftovers, list):
        leftovers = [leftovers] if leftovers else []
    return {
        "ok": not leftovers,
        "close": close_result,
        "force": force_result,
        "leftovers_count": len(leftovers),
    }


def _workspace_dir_for_skill(skill_id: str) -> Optional[Path]:
    if skill_id == "browser_use_skill":
        return _OC_DIR / "workspace-lobster-browser-use"
    if skill_id == "computer_use_skill":
        return _OC_DIR / "workspace-lobster-computer-use"
    return None


def _resolve_skill_workspace_file(skill_id: str, raw_path: str) -> Path:
    workspace = _workspace_dir_for_skill(skill_id)
    if not workspace:
        raise HTTPException(status_code=400, detail="未知 OpenClaw 技能")
    try:
        base = workspace.resolve()
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = base / candidate
        resolved = candidate.resolve()
        resolved.relative_to(base)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="文件路径无效")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    if resolved.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise HTTPException(status_code=400, detail="仅支持预览图片文件")
    return resolved


def _looks_like_screenshot_request(text: str) -> bool:
    value = (text or "").lower()
    return any(term in value for term in ("截图", "截屏", "屏幕", "screenshot", "capture"))


def _collect_workspace_image_attachments(skill_id: str, reply: str, user_message: str) -> List[Dict[str, Any]]:
    workspace = _workspace_dir_for_skill(skill_id)
    if not workspace or not workspace.exists():
        return []
    seen: Set[str] = set()
    attachments: List[Dict[str, Any]] = []

    def add_path(raw_path: str) -> None:
        if len(attachments) >= 4:
            return
        try:
            path = _resolve_skill_workspace_file(skill_id, raw_path)
        except HTTPException:
            return
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        attachments.append({"kind": "image", "path": key, "name": path.name, "size": size})

    for match in _IMAGE_PATH_RE.finditer(reply or ""):
        add_path(match.group(0))

    if not attachments and (_looks_like_screenshot_request(user_message) or _looks_like_screenshot_request(reply)):
        images = [
            p
            for p in workspace.glob("*")
            if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        ]
        images.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        for path in images[:2]:
            add_path(str(path))

    return attachments


@router.get("/api/openclaw/skill-chat/config", summary="OpenClaw 技能工作台执行配置")
async def openclaw_skill_chat_config(
    raw_token: str = Depends(oauth2_scheme),
    current_user: Union[User, _ServerUser] = Depends(get_current_user_for_chat),
):
    config = _read_oc_config()
    snapshot = _skill_exec_config_snapshot(config if isinstance(config, dict) else {})
    snapshot["ok"] = True
    return snapshot


@router.post("/api/openclaw/skill-chat/config/ensure", summary="补齐 OpenClaw 技能工作台执行配置")
async def ensure_openclaw_skill_chat_config(
    raw_token: str = Depends(oauth2_scheme),
    current_user: Union[User, _ServerUser] = Depends(get_current_user_for_chat),
):
    snapshot = _ensure_skill_exec_config()
    snapshot["ok"] = True
    return snapshot


@router.post("/api/openclaw/skill-chat/config", summary="更新 OpenClaw 技能工作台执行配置")
async def update_openclaw_skill_chat_config(
    body: OpenClawSkillExecConfigRequest,
    raw_token: str = Depends(oauth2_scheme),
    current_user: Union[User, _ServerUser] = Depends(get_current_user_for_chat),
):
    snapshot = _apply_skill_exec_config(body.mode, only_if_missing=False)
    restarted = False
    if snapshot.get("changed"):
        restarted = _restart_openclaw_gateway()
        snapshot["gateway_online"] = bool(_find_openclaw_pid())
    snapshot["restarted"] = restarted
    snapshot["ok"] = True
    return snapshot


@router.post("/api/openclaw/skill-chat/runtime/cleanup", summary="清理 OpenClaw Browser Use 运行时")
async def cleanup_openclaw_skill_chat_runtime(
    body: OpenClawSkillRuntimeCleanupRequest,
    raw_token: str = Depends(oauth2_scheme),
    current_user: Union[User, _ServerUser] = Depends(get_current_user_for_chat),
):
    skill_id = (body.skill_id or "").strip()
    if skill_id != "browser_use_skill":
        return {"ok": True, "skipped": True, "skill_id": skill_id}

    result = await asyncio.to_thread(_cleanup_browser_use_runtime)
    logger.info(
        "[openclaw_skill_chat] browser-use runtime cleanup user=%s ok=%s leftovers=%s",
        getattr(current_user, "id", "-"),
        result.get("ok"),
        result.get("leftovers_count"),
    )
    return result


@router.get("/api/openclaw/skill-chat/workspace-file", summary="预览 OpenClaw 技能工作台图片")
async def openclaw_skill_chat_workspace_file(
    skill_id: str,
    path: str,
    raw_token: str = Depends(oauth2_scheme),
    current_user: Union[User, _ServerUser] = Depends(get_current_user_for_chat),
):
    resolved = _resolve_skill_workspace_file((skill_id or "").strip(), path or "")
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(resolved.suffix.lower(), "application/octet-stream")
    return FileResponse(resolved, media_type=media_type, filename=resolved.name)


@router.post("/api/openclaw/skill-chat", summary="独立 OpenClaw 技能工作台对话")
async def openclaw_skill_chat(
    payload: OpenClawSkillChatRequest,
    request: Request,
    raw_token: str = Depends(oauth2_scheme),
    current_user: Union[User, _ServerUser] = Depends(get_current_user_for_chat),
):
    skill_id = (payload.skill_id or "").strip()
    skill_def = _SKILL_DEFS.get(skill_id)
    if not skill_def:
        raise HTTPException(status_code=400, detail="未知 OpenClaw 技能")

    oc_base = (settings.openclaw_gateway_url or "").strip().rstrip("/")
    oc_token = (settings.openclaw_gateway_token or "").strip()
    if not oc_base or not oc_token:
        raise HTTPException(status_code=503, detail="本机未配置 OpenClaw Gateway")

    agent_id = skill_def["agent_id"]
    installation_id = _installation_id_from_request(request)
    user_token = (raw_token or "").strip()
    if user_token and not installation_id.lower().startswith("lobster-internal-"):
        set_mcp_token_for_agent(agent_id, user_token, installation_id=installation_id or None)
    set_openclaw_tool_scope_for_agent(
        agent_id,
        {
            HEADER_INTENT: skill_def["intent"],
            HEADER_ALLOWED_TOOLS: "",
            HEADER_ALLOWED_CAPABILITIES: "",
        },
    )

    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {oc_token}",
        "x-openclaw-agent-id": agent_id,
    }
    if user_token and not installation_id.lower().startswith("lobster-internal-"):
        headers["x-user-authorization"] = f"Bearer {user_token}"
    if installation_id and not installation_id.lower().startswith("lobster-internal-"):
        headers["X-Installation-Id"] = installation_id

    config_snapshot = _ensure_skill_exec_config()
    if config_snapshot.get("restarted"):
        await asyncio.sleep(3)
    auto_exec_enabled = config_snapshot.get("mode") == "auto"
    messages = _safe_messages(payload, skill_def, auto_exec_enabled)
    model = (payload.model or f"openclaw/{agent_id}").strip() or f"openclaw/{agent_id}"
    reply = ""
    auto_approved: List[str] = []
    seen_approval_ids: Set[str] = set()
    tool_markup_repairs = 0
    semantic_repairs = 0
    weibo_search_query = _extract_weibo_search_query(payload.message)

    logger.info(
        "[openclaw_skill_chat] start skill=%s agent=%s model=%s user=%s history=%s msg_len=%s auto_exec=%s",
        skill_id,
        agent_id,
        model,
        getattr(current_user, "id", "-"),
        len(payload.history or []),
        len(payload.message or ""),
        auto_exec_enabled,
    )

    try:
        async with httpx.AsyncClient(timeout=240.0, trust_env=False) as client:
            for step in range(_AUTO_APPROVAL_MAX_STEPS + 1):
                reply = await _post_openclaw_chat(client, oc_base, model, messages, headers)
                logger.info(
                    "[openclaw_skill_chat] step=%s reply_len=%s skill=%s agent=%s",
                    step,
                    len(reply or ""),
                    skill_id,
                    agent_id,
                )
                if _looks_like_unexecuted_tool_markup(reply):
                    if tool_markup_repairs >= _TOOL_MARKUP_REPAIR_MAX_STEPS:
                        logger.warning(
                            "[openclaw_skill_chat] tool markup repair limit reached skill=%s agent=%s user=%s",
                            skill_id,
                            agent_id,
                            getattr(current_user, "id", "-"),
                        )
                        break
                    tool_markup_repairs += 1
                    logger.warning(
                        "[openclaw_skill_chat] repairing unexecuted tool markup skill=%s agent=%s attempt=%s body=%s",
                        skill_id,
                        agent_id,
                        tool_markup_repairs,
                        (reply or "")[:500],
                    )
                    messages.append({"role": "assistant", "content": _UNEXECUTED_TOOL_REPLY})
                    messages.append({"role": "user", "content": _tool_markup_repair_message()})
                    continue
                if (
                    weibo_search_query
                    and semantic_repairs < _SEMANTIC_REPAIR_MAX_STEPS
                    and _looks_like_weibo_login_wall(reply)
                ):
                    semantic_repairs += 1
                    logger.warning(
                        "[openclaw_skill_chat] repairing weibo login wall skill=%s agent=%s attempt=%s query=%s body=%s",
                        skill_id,
                        agent_id,
                        semantic_repairs,
                        weibo_search_query,
                        (reply or "")[:500],
                    )
                    messages.append({"role": "assistant", "content": reply[:4000]})
                    messages.append({"role": "user", "content": _weibo_mobile_repair_message(weibo_search_query)})
                    continue
                if (
                    weibo_search_query
                    and semantic_repairs < _SEMANTIC_REPAIR_MAX_STEPS
                    and _looks_like_incomplete_weibo_task(reply)
                ):
                    semantic_repairs += 1
                    logger.warning(
                        "[openclaw_skill_chat] repairing incomplete weibo task skill=%s agent=%s attempt=%s query=%s body=%s",
                        skill_id,
                        agent_id,
                        semantic_repairs,
                        weibo_search_query,
                        (reply or "")[:500],
                    )
                    messages.append({"role": "assistant", "content": reply[:4000]})
                    messages.append({"role": "user", "content": _weibo_finish_download_message(weibo_search_query)})
                    continue
                approval_id = _extract_approval_request_id(reply)
                if not approval_id:
                    break
                if not auto_exec_enabled:
                    break
                if approval_id in seen_approval_ids:
                    logger.warning(
                        "[openclaw_skill_chat] duplicate approval prompt skill=%s agent=%s approval=%s user=%s",
                        skill_id,
                        agent_id,
                        approval_id,
                        getattr(current_user, "id", "-"),
                    )
                    break
                if len(auto_approved) >= _AUTO_APPROVAL_MAX_STEPS:
                    logger.warning(
                        "[openclaw_skill_chat] auto approval limit reached skill=%s agent=%s user=%s",
                        skill_id,
                        agent_id,
                        getattr(current_user, "id", "-"),
                    )
                    break

                seen_approval_ids.add(approval_id)
                auto_approved.append(approval_id)
                logger.info(
                    "[openclaw_skill_chat] auto approving OpenClaw exec skill=%s agent=%s approval=%s user=%s",
                    skill_id,
                    agent_id,
                    approval_id,
                    getattr(current_user, "id", "-"),
                )
                messages.append({"role": "assistant", "content": reply})
                messages.append({"role": "user", "content": f"/approve {approval_id} allow-always"})
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "[openclaw_skill_chat] OpenClaw request failed skill=%s user=%s err=%s",
            skill_id,
            getattr(current_user, "id", "-"),
            exc,
        )
        raise HTTPException(status_code=502, detail=f"OpenClaw 请求失败：{exc}")

    reply = _normalize_openclaw_reply(reply)
    reply = _ensure_weibo_reply_has_full_path(reply, weibo_search_query)
    attachments = _collect_workspace_image_attachments(skill_id, reply, payload.message or "")
    logger.info(
        "[openclaw_skill_chat] done skill=%s agent=%s user=%s reply_len=%s attachments=%s auto_approved=%s",
        skill_id,
        agent_id,
        getattr(current_user, "id", "-"),
        len(reply or ""),
        len(attachments),
        len(auto_approved),
    )

    download_path = str(_weibo_download_path()) if weibo_search_query and _weibo_download_path().exists() else ""
    return {
        "ok": True,
        "skill_id": skill_id,
        "skill_name": skill_def["name"],
        "reply": reply,
        "attachments": attachments,
        "agent_id": agent_id,
        "model": model,
        "download_path": download_path,
        "auto_approved": auto_approved,
        "exec_config": config_snapshot,
    }
