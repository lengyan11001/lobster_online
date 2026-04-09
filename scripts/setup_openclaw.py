"""Helper script called by install.bat to configure OpenClaw Gateway."""
import json
import os
import re
import secrets
import sys
from pathlib import Path


def _model_to_agent_id(model: str) -> str:
    slug = model.lower().replace("/", "-").replace(".", "-")
    slug = re.sub(r'[^a-z0-9_-]', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug[:64] or "main"


ALL_MODELS = [
    "anthropic/claude-sonnet-4-5",
    "anthropic/claude-opus-4-6",
    "anthropic/claude-haiku-3-5",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "openai/o3-mini",
    "deepseek/deepseek-chat",
    "deepseek/deepseek-reasoner",
    "google/gemini-2.5-pro",
    "google/gemini-2.5-flash",
]

DEFAULT_PRIMARY = "anthropic/claude-sonnet-4-5"


def _build_agents_list(primary: str) -> list:
    agents = [{"id": "main", "default": True}]
    seen: set = set()
    for m in ALL_MODELS:
        if m in seen:
            continue
        seen.add(m)
        agents.append({"id": _model_to_agent_id(m), "model": m})
    return agents


def _build_default_config(token: str) -> dict:
    return {
        "agents": {
            "defaults": {
                "workspace": "./openclaw/workspace",
                "model": {"primary": DEFAULT_PRIMARY},
            },
            "list": _build_agents_list(DEFAULT_PRIMARY),
        },
        "gateway": {
            "mode": "local",
            "port": 18789,
            "bind": "loopback",
            "auth": {"mode": "token", "token": token},
            "http": {"endpoints": {"chatCompletions": {"enabled": True}}},
        },
    }


def _ensure_deepseek_provider(cfg: dict, oc_config: Path, oc_env: Path):
    """Add DeepSeek provider only if DEEPSEEK_API_KEY is set in openclaw/.env.

    Uses the actual key value directly (not ${ENV_VAR} template) to avoid
    OpenClaw SecretRef startup failure when key is empty.
    """
    ds_key = ""
    if oc_env.exists():
        for line in oc_env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY="):
                ds_key = line.split("=", 1)[1].strip()
                break

    providers = cfg.setdefault("models", {}).setdefault("providers", {})
    if ds_key:
        providers["deepseek"] = {
            "baseUrl": "https://api.deepseek.com",
            "api": "openai-completions",
            "apiKey": ds_key,
            "models": [
                {"id": "deepseek-chat", "name": "DeepSeek Chat",
                 "input": ["text"], "contextWindow": 65536, "maxTokens": 8192},
                {"id": "deepseek-reasoner", "name": "DeepSeek Reasoner",
                 "reasoning": True, "input": ["text"], "contextWindow": 65536, "maxTokens": 8192},
            ],
        }
        oc_config.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("[OK] Added DeepSeek provider (key found)")
    else:
        providers.pop("deepseek", None)
        if not providers:
            cfg.get("models", {}).pop("providers", None)
            if not cfg.get("models"):
                cfg.pop("models", None)
        oc_config.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _ensure_agents_list(cfg: dict, oc_config: Path):
    """Ensure agents.list exists with all supported models."""
    agents_node = cfg.get("agents", {})
    if not agents_node.get("list"):
        primary = agents_node.get("defaults", {}).get("model", {}).get("primary", DEFAULT_PRIMARY)
        cfg.setdefault("agents", {})["list"] = _build_agents_list(primary)
        oc_config.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("[OK] Added multi-agent list to openclaw.json")


# 须为「项目根」相对路径：OpenClaw 用 path.resolve() 相对 process.cwd()，不用 openclaw/ 目录。
# 龙虾启动 Gateway 时 cwd=项目根；CLI 也请在项目根执行（见 README / 使用说明）。
WEIXIN_PLUGIN_LOAD_PATH = "nodejs/node_modules/@tencent-weixin/openclaw-weixin"
_WEIXIN_PLUGIN_LOAD_PATH_LEGACY = "../nodejs/node_modules/@tencent-weixin/openclaw-weixin"


def _ensure_weixin_plugin(cfg: dict, oc_config: Path, base: Path) -> None:
    """Register Tencent WeChat channel plugin from bundled npm package (full pack / npm install)."""
    plugin_dir = base / "nodejs" / "node_modules" / "@tencent-weixin" / "openclaw-weixin"
    if not plugin_dir.is_dir():
        print(
            "[WARN] @tencent-weixin/openclaw-weixin not found under nodejs/node_modules — "
            "run install.bat step 4 or: cd nodejs && npm install"
        )
        return
    plugins = cfg.setdefault("plugins", {})
    if plugins.get("enabled") is not False:
        plugins.setdefault("enabled", True)
    load = plugins.setdefault("load", {})
    paths = load.setdefault("paths", [])
    if _WEIXIN_PLUGIN_LOAD_PATH_LEGACY in paths:
        paths[:] = [p for p in paths if p != _WEIXIN_PLUGIN_LOAD_PATH_LEGACY]
    if WEIXIN_PLUGIN_LOAD_PATH not in paths:
        paths.append(WEIXIN_PLUGIN_LOAD_PATH)
    wx_entry = plugins.setdefault("entries", {}).setdefault("openclaw-weixin", {})
    if wx_entry.get("enabled") is not False:
        wx_entry.setdefault("enabled", True)
    oc_config.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("[OK] OpenClaw WeChat plugin (openclaw-weixin) registered in openclaw.json")


def main():
    base = Path(__file__).resolve().parent.parent
    oc_dir = base / "openclaw"
    oc_config = oc_dir / "openclaw.json"
    oc_env = oc_dir / ".env"
    dot_env = base / ".env"
    dot_env_example = base / ".env.example"

    oc_dir.mkdir(parents=True, exist_ok=True)
    (oc_dir / "workspace").mkdir(parents=True, exist_ok=True)

    token = secrets.token_hex(32)

    if oc_config.exists():
        try:
            cfg = json.loads(oc_config.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        existing_token = cfg.get("gateway", {}).get("auth", {}).get("token", "")
        if existing_token and existing_token != "LOBSTER_AUTO_TOKEN_PLACEHOLDER":
            token = existing_token
            print("[OK] openclaw.json already configured, keeping existing token")
        else:
            cfg.setdefault("gateway", {}).setdefault("auth", {})["token"] = token
            oc_config.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print("[OK] Updated openclaw.json with new token")
    else:
        cfg = _build_default_config(token)
        oc_config.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("[OK] Created openclaw.json with multi-agent config")

    _ensure_deepseek_provider(cfg, oc_config, oc_env)
    _ensure_agents_list(cfg, oc_config)

    try:
        cfg = json.loads(oc_config.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    _ensure_weixin_plugin(cfg, oc_config, base)

    if not oc_env.exists():
        oc_env.write_text(
            "# OpenClaw LLM API Keys\nANTHROPIC_API_KEY=\nOPENAI_API_KEY=\nDEEPSEEK_API_KEY=\nGEMINI_API_KEY=\n",
            encoding="utf-8",
        )
        print("[OK] Created openclaw/.env")

    if not dot_env.exists() and dot_env_example.exists():
        dot_env.write_text(dot_env_example.read_text(encoding="utf-8"), encoding="utf-8")
        print("[OK] Created .env from .env.example")

    if dot_env.exists():
        text = dot_env.read_text(encoding="utf-8")
        lines = text.splitlines()
        new_lines = []
        url_set = False
        token_set = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("OPENCLAW_GATEWAY_URL="):
                new_lines.append("OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789")
                url_set = True
            elif stripped.startswith("OPENCLAW_GATEWAY_TOKEN="):
                new_lines.append(f"OPENCLAW_GATEWAY_TOKEN={token}")
                token_set = True
            else:
                new_lines.append(line)
        if not url_set:
            new_lines.append("OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789")
        if not token_set:
            new_lines.append(f"OPENCLAW_GATEWAY_TOKEN={token}")
        dot_env.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        print("[OK] Updated .env with OpenClaw Gateway URL and token")

    # Create upstream_urls.json for SuTui MCP
    upstream_urls_path = base / "upstream_urls.json"
    if not upstream_urls_path.exists():
        upstream_urls_path.write_text(
            json.dumps({"sutui": "https://mcp.sutui.cc/mcp"}, indent=2) + "\n",
            encoding="utf-8",
        )
        print("[OK] Created upstream_urls.json with SuTui MCP URL")

    print("[OK] OpenClaw configuration complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
