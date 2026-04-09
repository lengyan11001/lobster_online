"""YouTube OAuth 多客户端注册表：仅本机 lobster_online/youtube_oauth_clients.json。

必须显式指定 oauth_client_key，不使用 default_key 或 .env 兜底。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

_BASE = Path(__file__).resolve().parent.parent.parent.parent
REGISTRY_PATH = _BASE / "youtube_oauth_clients.json"


def registry_path() -> Path:
    return REGISTRY_PATH


def load_registry() -> Dict[str, Any]:
    if not REGISTRY_PATH.is_file():
        return {}
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[youtube-oauth-registry] 读取失败: %s", e)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_registry(data: Dict[str, Any]) -> None:
    REGISTRY_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def validate_registry(data: Dict[str, Any]) -> None:
    clients = data.get("clients")
    if not isinstance(clients, dict) or not clients:
        raise ValueError("clients 必须为非空对象")
    for k, v in clients.items():
        if not isinstance(k, str) or not k.strip():
            raise ValueError("客户端 key 必须为非空字符串")
        if not isinstance(v, dict):
            raise ValueError(f"客户端「{k}」配置无效")
        cid = (v.get("oauth_client_id") or "").strip()
        csec = (v.get("oauth_client_secret") or "").strip()
        if not cid or not csec:
            raise ValueError(f"客户端「{k}」缺少 oauth_client_id 或 oauth_client_secret")


def list_client_keys(data: Dict[str, Any]) -> List[str]:
    c = data.get("clients")
    if not isinstance(c, dict):
        return []
    return sorted(c.keys())


def try_resolve_credentials(raw: Dict[str, Any]) -> Tuple[str, str]:
    """仅按显式 oauth_client_key 从注册表解析；未指定 key 或表中无此项则返回空。"""
    key = (raw.get("oauth_client_key") or "").strip()
    if not key:
        return "", ""
    reg = load_registry()
    clients = reg.get("clients") if isinstance(reg.get("clients"), dict) else {}
    entry = clients.get(key)
    if not isinstance(entry, dict):
        return "", ""
    cid = (entry.get("oauth_client_id") or "").strip()
    csec = (entry.get("oauth_client_secret") or "").strip()
    if not cid or not csec:
        return "", ""
    return cid, csec
