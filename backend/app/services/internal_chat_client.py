"""本机后端代用户调用 POST /chat 时，须带与浏览器一致的 X-Installation-Id，否则经 MCP→mcp-gateway 时认证中心返回 400。"""
from __future__ import annotations


def chat_headers_for_user(user_id: int, token: str) -> dict:
    uid = int(user_id)
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token.strip()}",
        "X-Installation-Id": f"lobster-internal-{uid}",
    }
