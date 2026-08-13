"""User settings: model selection, preferences."""
import asyncio
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import get_db
from .auth import (
    get_current_user,
    get_current_user_for_local,
    get_current_user_media_edit,
    _ServerUser,
)
from .openclaw_config import clear_openclaw_local_provider_keys
from ..models import ConsumptionAccount, User
from ..services.openclaw_channel_auth_store import persist_channel_fallback_for_login
from ..services.asset_storage_paths import get_asset_path_settings, set_asset_export_dir
from ..services.chat_route_mode import (
    CHAT_ROUTE_MODE_DIRECT,
    CHAT_ROUTE_MODE_OPENCLAW,
    DEFAULT_CHAT_ROUTE_MODE,
    get_chat_route_mode,
    normalize_chat_route_mode,
    set_chat_route_mode,
)
from ..services.runtime_dependency_repair import (
    RuntimeDependencyRepairBusy,
    repair_runtime_dependencies,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_CUSTOM_CONFIGS_FILE = Path(__file__).resolve().parent.parent.parent.parent / "custom_configs.json"
_CLIENT_CODE_VERSION_FILE = Path(__file__).resolve().parent.parent.parent.parent / "CLIENT_CODE_VERSION.json"
_CLIENT_ROOT = _CLIENT_CODE_VERSION_FILE.parent
_CLIENT_UPDATE_CHECK_SCRIPT = _CLIENT_ROOT / "scripts" / "check_client_code_update.py"
_CLIENT_UPDATE_STATUS_PREFIX = "__LOBSTER_UPDATE_STATUS__="
_CLIENT_UPDATE_STATUS_CACHE_SECONDS = 60.0
_client_update_status_cache: tuple[float, dict[str, Any]] | None = None
_client_update_status_lock = asyncio.Lock()
_MACHINE_ID_FILE_NAME = "machine_identity.json"
_MACHINE_INSTANCE_ID_CACHE = ""


def _load_custom_configs() -> dict[str, Any]:
    if not _CUSTOM_CONFIGS_FILE.exists():
        return {"configs": {}, "custom_models": []}
    try:
        data = json.loads(_CUSTOM_CONFIGS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"configs": {}, "custom_models": []}


def _save_custom_configs(data: dict[str, Any]) -> None:
    _CUSTOM_CONFIGS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _remove_local_tos_config() -> bool:
    data = _load_custom_configs()
    configs = data.get("configs")
    if not isinstance(configs, dict) or "TOS_CONFIG" not in configs:
        return False
    configs.pop("TOS_CONFIG", None)
    _save_custom_configs(data)
    return True


def _normalize_server_tos_config(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    cfg = {str(k): v for k, v in raw.items()}
    required = (
        "access_key",
        "secret_key",
        "endpoint",
        "region",
        "bucket_name",
        "public_domain",
    )
    if not all(str(cfg.get(k) or "").strip() for k in required):
        return None
    return cfg


def _save_local_tos_config(cfg: dict[str, Any]) -> bool:
    data = _load_custom_configs()
    configs = data.get("configs")
    if not isinstance(configs, dict):
        configs = {}
        data["configs"] = configs
    before = configs.get("TOS_CONFIG")
    configs["TOS_CONFIG"] = cfg
    _save_custom_configs(data)
    return before != cfg


_DEFAULT_CLIENT_SEMVER = "1.0.0"


def _read_client_code_version_for_ui() -> tuple[int, Optional[str], str]:
    """本机纯代码包 OTA：build 用于比对；version 为展示用语义版本（默认 1.0.0）。"""
    try:
        if not _CLIENT_CODE_VERSION_FILE.is_file():
            return 0, None, _DEFAULT_CLIENT_SEMVER
        data = json.loads(_CLIENT_CODE_VERSION_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return 0, None, _DEFAULT_CLIENT_SEMVER
        b = data.get("build", 0)
        build = int(b) if b is not None else 0
        at = data.get("applied_at")
        applied = str(at).strip() if at else None
        ver = str(data.get("version", "") or "").strip() or _DEFAULT_CLIENT_SEMVER
        return build, applied or None, ver
    except Exception:
        return 0, None, _DEFAULT_CLIENT_SEMVER


def _run_client_update_status_check() -> dict[str, Any]:
    build, _applied_at, version = _read_client_code_version_for_ui()
    fallback: dict[str, Any] = {
        "ok": False,
        "configured": False,
        "available": False,
        "restart_required": False,
        "local_build": build,
        "local_version": version,
        "remote_build": None,
        "remote_version": "",
        "reason": "",
    }
    if not _CLIENT_UPDATE_CHECK_SCRIPT.is_file():
        fallback["message"] = "当前客户端缺少更新检查组件"
        return fallback
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            [sys.executable, str(_CLIENT_UPDATE_CHECK_SCRIPT), "--check-only"],
            cwd=str(_CLIENT_ROOT),
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=50,
            creationflags=flags,
        )
    except subprocess.TimeoutExpired:
        fallback["message"] = "检查更新超时，稍后会自动重试"
        return fallback
    except Exception as exc:
        fallback["message"] = f"检查更新失败：{exc}"
        return fallback

    for line in reversed((completed.stdout or "").splitlines()):
        if not line.startswith(_CLIENT_UPDATE_STATUS_PREFIX):
            continue
        try:
            payload = json.loads(line[len(_CLIENT_UPDATE_STATUS_PREFIX) :])
        except json.JSONDecodeError:
            break
        if isinstance(payload, dict):
            payload["checked_at"] = int(time.time() * 1000)
            return payload
    fallback["message"] = "更新检查程序未返回有效结果"
    return fallback


async def _client_update_status() -> dict[str, Any]:
    global _client_update_status_cache
    now = time.monotonic()
    cached = _client_update_status_cache
    if cached and now - cached[0] < _CLIENT_UPDATE_STATUS_CACHE_SECONDS:
        return dict(cached[1])
    async with _client_update_status_lock:
        now = time.monotonic()
        cached = _client_update_status_cache
        if cached and now - cached[0] < _CLIENT_UPDATE_STATUS_CACHE_SECONDS:
            return dict(cached[1])
        payload = await asyncio.to_thread(_run_client_update_status_check)
        _client_update_status_cache = (time.monotonic(), dict(payload))
        return payload


@router.get("/api/edition", summary="在线版（固定 edition=online）")
async def get_edition():
    use_independent = getattr(settings, "lobster_independent_auth", True)
    cb, cat, cver = _read_client_code_version_for_ui()
    out: dict = {
        "edition": "online",
        "use_independent_auth": bool(use_independent),
        "allow_self_config_model": getattr(settings, "sutui_online_model_self_config", True),
        "client_code_build": cb,
        "client_code_applied_at": cat,
        "client_code_version": cver,
    }
    if not use_independent:
        out["recharge_url"] = (getattr(settings, "sutui_recharge_url", None) or "").strip() or None
    out["use_fuiou_pay"] = False
    out["use_own_wechat_login"] = False
    base = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    if base:
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                r = await client.get(f"{base}/api/edition")
            if r.status_code == 200:
                remote = r.json()
                if isinstance(remote, dict):
                    if "use_fuiou_pay" in remote:
                        out["use_fuiou_pay"] = bool(remote.get("use_fuiou_pay"))
                    if "use_own_wechat_login" in remote:
                        out["use_own_wechat_login"] = bool(remote.get("use_own_wechat_login"))
        except Exception as e:
            logger.debug("edition merge from auth server failed: %s", e)
    return out


@router.get("/api/client-update/status", summary="检查本机客户端是否有新版本")
async def get_client_update_status():
    return await _client_update_status()


def _get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class UpdateSettingsRequest(BaseModel):
    preferred_model: Optional[str] = None


class ChatRouteModeRequest(BaseModel):
    mode: str


class AssetPathSettingsRequest(BaseModel):
    export_dir: Optional[str] = None


class InstallationIdSyncRequest(BaseModel):
    installation_id: str


class MachineIdentityResponse(BaseModel):
    ok: bool = True
    machine_instance_id: str


_INSTALLATION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,128}$")


def _normalize_installation_id(raw: str) -> str:
    value = (raw or "").strip()
    return value if _INSTALLATION_ID_RE.fullmatch(value) else ""


def _machine_identity_candidates() -> list[Path]:
    """Return machine-local identity paths, preferring locations outside OTA."""
    paths: list[Path] = []
    if os.name == "nt":
        for env_name in ("PROGRAMDATA", "LOCALAPPDATA", "APPDATA"):
            root = str(os.environ.get(env_name) or "").strip()
            if root:
                paths.append(Path(root) / "LobsterOnline" / _MACHINE_ID_FILE_NAME)
    paths.append(_CLIENT_ROOT / "openclaw" / "identity" / _MACHINE_ID_FILE_NAME)
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _machine_identity_file() -> Path:
    candidates = _machine_identity_candidates()
    return candidates[0] if candidates else _CLIENT_ROOT / "openclaw" / "identity" / _MACHINE_ID_FILE_NAME


def _get_or_create_machine_instance_id() -> str:
    global _MACHINE_INSTANCE_ID_CACHE
    cached = _normalize_installation_id(_MACHINE_INSTANCE_ID_CACHE)
    if cached:
        return cached
    candidates = _machine_identity_candidates()
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            value = _normalize_installation_id(str(data.get("machine_instance_id") or ""))
            if value:
                _MACHINE_INSTANCE_ID_CACHE = value
                return value
        except Exception:
            pass

    created_at_ms = int(time.time() * 1000)
    value = f"m{created_at_ms:x}{uuid.uuid4().hex}"
    payload = {
        "version": 1,
        "machine_instance_id": value,
        "created_at_ms": created_at_ms,
    }
    errors: list[str] = []
    for path in candidates:
        tmp = path.with_suffix(".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(str(tmp), str(path))
            _MACHINE_INSTANCE_ID_CACHE = value
            return value
        except OSError as exc:
            errors.append(f"{path}: {exc}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    _MACHINE_INSTANCE_ID_CACHE = value
    if errors:
        logger.warning("machine identity persistence failed at all paths: %s", "; ".join(errors[:3]))
    return value


def _bearer_token_from_request(request: Request) -> str:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return auth


@router.get("/api/settings", summary="获取用户设置")
def get_settings(current_user: User = Depends(get_current_user)):
    preferred = "sutui"
    return {"preferred_model": preferred}


@router.get("/api/settings/machine-identity", response_model=MachineIdentityResponse)
def get_machine_identity():
    return MachineIdentityResponse(machine_instance_id=_get_or_create_machine_instance_id())


@router.post("/api/settings", summary="更新用户设置")
def update_settings(
    body: UpdateSettingsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.preferred_model is not None:
        current_user.preferred_model = body.preferred_model.strip() or "openclaw"
    db.commit()
    return {"preferred_model": current_user.preferred_model}


def _load_local_model_entries() -> list:
    """本机 openclaw / custom_configs 中的可选直连模型（与单机版同源逻辑）。"""
    base_dir = Path(__file__).resolve().parent.parent.parent.parent
    models = []

    config_path = base_dir / "models_config.json"
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            models = data.get("models", [])
        except Exception:
            pass
    if not models:
        models = [
            {"id": "openclaw", "name": "默认 (OpenClaw)", "description": "OpenClaw 默认路由"},
            {"id": "anthropic/claude-sonnet-4-5", "name": "Claude Sonnet 4.5", "description": "Anthropic 快速模型"},
            {"id": "openai/gpt-4o", "name": "GPT-4o", "description": "OpenAI 多模态模型"},
            {"id": "deepseek/deepseek-chat", "name": "DeepSeek Chat", "description": "DeepSeek 对话模型"},
        ]

    existing_ids = {m.get("id") for m in models}

    custom_path = base_dir / "custom_configs.json"
    if custom_path.exists():
        try:
            custom_data = json.loads(custom_path.read_text(encoding="utf-8"))
            for cm in custom_data.get("custom_models", []):
                mid = cm.get("model_id", "")
                if mid and mid not in existing_ids:
                    models.append({
                        "id": mid,
                        "name": cm.get("display_name") or mid,
                        "description": cm.get("provider", "自定义模型"),
                        "custom": True,
                    })
                    existing_ids.add(mid)
        except Exception:
            pass

    return models


@router.get("/api/settings/models", summary="可选模型列表")
def list_models():
    out = [
        {
            "id": "sutui_aggregate",
            "name": "速推聚合",
            "description": "速推多模型；进入智能会话后在子下拉选择具体模型",
        }
    ]
    if getattr(settings, "sutui_online_model_self_config", True):
        for m in _load_local_model_entries():
            mid = m.get("id")
            if not mid or mid in ("sutui", "sutui_aggregate"):
                continue
            out.append(m)
    return {"models": out}


@router.get("/api/settings/lan-info", summary="获取局域网访问信息")
def get_lan_info():
    ip = _get_lan_ip()
    port = getattr(settings, "port", 8000)
    return {
        "lan_ip": ip,
        "port": port,
        "url": f"http://{ip}:{port}",
    }


@router.get("/api/settings/chat-route", summary="获取智能对话路由模式")
def get_chat_route_settings(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    return {
        "mode": get_chat_route_mode(),
        "default_mode": DEFAULT_CHAT_ROUTE_MODE,
        "modes": [
            {"value": CHAT_ROUTE_MODE_DIRECT, "label": "直连 + MCP"},
            {"value": CHAT_ROUTE_MODE_OPENCLAW, "label": "OpenClaw Gateway"},
        ],
    }


@router.post("/api/settings/chat-route", summary="更新智能对话路由模式")
def update_chat_route_settings(
    body: ChatRouteModeRequest,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    mode = normalize_chat_route_mode(body.mode)
    if not mode:
        raise HTTPException(status_code=400, detail="无效的智能对话路由模式")
    return {"ok": True, "mode": set_chat_route_mode(mode)}


@router.post("/api/settings/installation-id/sync", summary="Sync current local installation id cache")
async def sync_local_installation_id(
    body: InstallationIdSyncRequest,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    installation_id = _normalize_installation_id(body.installation_id)
    if not installation_id:
        raise HTTPException(status_code=400, detail="invalid installation id")
    jwt_token = _bearer_token_from_request(request)
    if jwt_token:
        persist_channel_fallback_for_login(
            jwt_token=jwt_token,
            installation_id=installation_id,
            user_id=current_user.id,
            db=db,
        )
    else:
        row = db.query(User).filter(User.id == current_user.id).first()
        if row is not None:
            row.client_installation_id = installation_id
            db.add(row)
            db.commit()
    logger.info(
        "[settings] local installation id synced user_id=%s installation_id=%s",
        current_user.id,
        installation_id[:16],
    )
    return {"ok": True, "installation_id": installation_id}


@router.post(
    "/api/settings/sync-tos-from-server",
    summary="从认证中心同步 TOS 到本机 custom_configs.json",
)
async def sync_tos_from_server(
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    """Sync server-provided TOS config when available; otherwise use server-side upload."""
    removed = False
    saved = False
    server_status: dict[str, Any] = {}
    mode = "server-side-upload"
    message = "服务器未下发 TOS_CONFIG，本机将使用服务器转存上传"
    base = (settings.auth_server_base or "").strip().rstrip("/")
    auth = (request.headers.get("Authorization") or "").strip()
    if base and auth:
        url = f"{base}/api/settings/tos-config"
        try:
            async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
                r = await client.get(url, headers={"Authorization": auth})
            if r.status_code < 400:
                body: Any = r.json()
                if isinstance(body, dict):
                    tos_cfg = _normalize_server_tos_config(body.get("TOS_CONFIG"))
                    if tos_cfg:
                        saved = _save_local_tos_config(tos_cfg)
                        mode = "local-tos"
                        message = "已同步服务器下发的 TOS_CONFIG，本机将优先直传 TOS"
                    else:
                        removed = _remove_local_tos_config()
                    server_status = {
                        "mode": mode if tos_cfg else (body.get("mode") or "server-side-upload"),
                        "tos_configured": bool(body.get("tos_configured")),
                        "bucket_name": str(body.get("bucket_name") or ""),
                        "public_domain": str(body.get("public_domain") or ""),
                        "tos_config_received": bool(tos_cfg),
                    }
                else:
                    removed = _remove_local_tos_config()
                    server_status = {"error": "response is not object"}
            else:
                if r.status_code == 404:
                    removed = _remove_local_tos_config()
                server_status = {"status_code": r.status_code}
        except Exception as e:
            server_status = {"error": f"{type(e).__name__}: {e}"}

    logger.info(
        "[sync-tos] mode=%s saved_local_tos_config=%s removed_local_tos_config=%s user_id=%s server_status=%s",
        mode,
        saved,
        removed,
        getattr(current_user, "id", None),
        server_status,
    )
    out = {
        "ok": True,
        "skipped": False,
        "mode": mode,
        "saved_local_tos_config": saved,
        "removed_local_tos_config": removed,
        "server": server_status,
        "message": message,
    }
    return out


@router.get("/api/settings/asset-paths", summary="Get local asset storage/export paths")
def get_asset_paths_settings(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    return get_asset_path_settings()


@router.post("/api/settings/asset-paths", summary="Update local asset export path")
def update_asset_paths_settings(
    body: AssetPathSettingsRequest,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    return set_asset_export_dir(body.export_dir)


@router.post("/api/settings/repair-runtime-dependencies", summary="Repair local runtime dependencies")
def repair_local_runtime_dependencies(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    try:
        return repair_runtime_dependencies()
    except RuntimeDependencyRepairBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/api/settings/clear-local-user-config",
    summary="清除本机个人配置（OpenClaw 各厂商 Key、本机库 Token/算力账号等）",
)
async def clear_local_user_config(
    current: _ServerUser = Depends(get_current_user_media_edit),
    db: Session = Depends(get_db),
):
    """
    与云端「是否存用户 Key」无关：各厂商 API Key 仅写在本机 openclaw/.env，本接口会清除这些文件中的 Key。

    本机 lobster.db：若存在 users 行则清空速推 Token、首选模型；始终按 user_id 删除 consumption_accounts。
    不删除：云端算力与素材、登录态；不整文件删除 custom_configs.json（请用自定义配置 Tab 管理）。

    鉴权仅用于确认当前操作者（与素材库一致）；不在远端保存任何 Key。
    """
    oc_cleared, oc_restarted = clear_openclaw_local_provider_keys()
    parts: list[str] = []
    if oc_cleared:
        parts.append(
            "已清除本机 openclaw/.env 中的各厂商 API Key（Anthropic / OpenAI / DeepSeek / Gemini），仅本机文件"
        )
        if oc_restarted:
            parts.append("已尝试重启本机 OpenClaw Gateway")

    row = db.query(User).filter(User.id == current.id).first()
    if row is not None:
        row.sutui_token = None
        row.preferred_model = "sutui"
    n_del = db.query(ConsumptionAccount).filter(ConsumptionAccount.user_id == current.id).delete(
        synchronize_session=False
    )
    db.commit()

    if row is not None:
        parts.append("已清除本机数据库中的速推 Token、首选模型、算力账号列表")
    elif n_del:
        parts.append("已清除本机数据库中的算力账号记录（本机尚无 users 行，未存过速推 Token）")
    elif not oc_cleared:
        parts.append("本机数据库无该用户 users 行且无算力账号记录；OpenClaw 未配置过 Key 则无文件变更")

    return {
        "ok": True,
        "message": "；".join(parts) if parts else "已完成",
        "openclaw_keys_cleared": oc_cleared,
    }
