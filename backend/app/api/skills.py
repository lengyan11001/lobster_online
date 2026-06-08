"""Skill/MCP package management: install, uninstall, list store."""
import hashlib
import json
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..db import get_db
from .auth import get_current_user_for_local, get_current_user_media_edit, _ServerUser
from ..models import CapabilityConfig

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
_SKILL_STORE_ADMIN_CACHE: dict[str, tuple[float, bool]] = {}
_SKILL_STORE_ADMIN_CACHE_TTL_SEC = 120.0


def _load_registry() -> dict:
    p = _BASE_DIR / "skill_registry.json"
    if not p.exists():
        return {"packages": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"packages": {}}


def _load_installed() -> dict:
    p = _BASE_DIR / "installed_packages.json"
    if not p.exists():
        return {"installed": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"installed": []}


def _save_installed(data: dict):
    p = _BASE_DIR / "installed_packages.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_local_catalog() -> dict:
    p = _BASE_DIR / "mcp" / "capability_catalog.local.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_local_catalog(catalog: dict):
    p = _BASE_DIR / "mcp" / "capability_catalog.local.json"
    p.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_upstream_urls() -> dict:
    p = _BASE_DIR / "upstream_urls.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_upstream_urls(urls: dict):
    p = _BASE_DIR / "upstream_urls.json"
    p.write_text(json.dumps(urls, ensure_ascii=False, indent=2), encoding="utf-8")


async def _is_skill_store_admin(request: Request) -> bool:

    settings = get_settings()
    base = (settings.auth_server_base or "").strip().rstrip("/")
    auth = (request.headers.get("Authorization") or "").strip()
    if not base or not auth:
        return False

    bearer = auth if auth.lower().startswith("bearer ") else f"Bearer {auth}"
    cache_key = hashlib.sha256(bearer.encode("utf-8")).hexdigest()
    now = time.monotonic()
    cached = _SKILL_STORE_ADMIN_CACHE.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    headers = {"Authorization": bearer}
    installation_id = (request.headers.get("X-Installation-Id") or "").strip()
    if installation_id:
        headers["X-Installation-Id"] = installation_id
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base}/skills/skill-store-admin", headers=headers)
        is_admin = bool(resp.status_code == 200 and resp.json().get("is_skill_store_admin"))
    except Exception:
        is_admin = False
    _SKILL_STORE_ADMIN_CACHE[cache_key] = (
        time.monotonic() + _SKILL_STORE_ADMIN_CACHE_TTL_SEC,
        is_admin,
    )
    return is_admin


def _package_visible_in_store(pkg: dict, is_admin: bool) -> bool:
    visibility = str(pkg.get("store_visibility") or "").strip().lower()
    if visibility in {"hidden", "off", "disabled"}:
        return False
    if visibility == "debug" and not is_admin:
        return False
    return True


@router.get("/skills/store", summary="技能商店列表")
async def list_store(request: Request):
    registry = _load_registry()
    installed = set(_load_installed().get("installed", []))
    packages = registry.get("packages", {})
    is_admin = await _is_skill_store_admin(request)
    out = []
    for pkg_id, pkg in packages.items():
        if not isinstance(pkg, dict):
            continue
        if not _package_visible_in_store(pkg, is_admin):
            continue
        out.append({
            "id": pkg_id,
            "name": pkg.get("name", pkg_id),
            "description": pkg.get("description", ""),
            "type": pkg.get("type", ""),
            "tags": pkg.get("tags", []),
            "status": "installed" if pkg_id in installed else pkg.get("status", "available"),
            "capabilities_count": len(pkg.get("capabilities", {})),
            "unlock_price_credits": pkg.get("unlock_price_credits"),
            "unlock_price_yuan": pkg.get("unlock_price_yuan"),
            "default_installed": pkg.get("default_installed"),
            "store_visibility": pkg.get("store_visibility"),
        })
    return {"packages": out, "is_skill_store_admin": is_admin}


@router.get("/skills/installed", summary="已安装技能列表")
def list_installed():
    installed_data = _load_installed()
    registry = _load_registry()
    packages = registry.get("packages", {})
    out = []
    for pkg_id in installed_data.get("installed", []):
        pkg = packages.get(pkg_id, {})
        out.append({
            "id": pkg_id,
            "name": pkg.get("name", pkg_id),
            "description": pkg.get("description", ""),
            "capabilities_count": len(pkg.get("capabilities", {})),
        })
    return {"installed": out}


class SkillInstallRequest(BaseModel):
    package_id: str


@router.post("/skills/install", summary="安装技能包")
async def install_skill(
    body: SkillInstallRequest,
    _: _ServerUser = Depends(get_current_user_media_edit),
    db: Session = Depends(get_db),
):
    registry = _load_registry()
    packages = registry.get("packages", {})
    package = packages.get(body.package_id)
    if not package:
        raise HTTPException(status_code=404, detail=f"技能包 {body.package_id} 不存在")
    if package.get("status") == "coming_soon":
        raise HTTPException(status_code=400, detail="该技能包即将推出，暂不可安装")

    installed_data = _load_installed()
    installed_list = installed_data.get("installed", [])
    if body.package_id in installed_list:
        return {"message": f"{package.get('name', body.package_id)} 已安装", "already_installed": True}

    capabilities = package.get("capabilities", {})
    if capabilities:
        catalog = _load_local_catalog()
        catalog.update(capabilities)
        _save_local_catalog(catalog)
        for cap_id, cap_cfg in capabilities.items():
            existing = db.query(CapabilityConfig).filter(CapabilityConfig.capability_id == cap_id).first()
            if not existing:
                db.add(CapabilityConfig(
                    capability_id=cap_id,
                    description=str(cap_cfg.get("description") or cap_id),
                    upstream=str(cap_cfg.get("upstream") or "sutui"),
                    upstream_tool=str(cap_cfg.get("upstream_tool") or ""),
                    arg_schema=cap_cfg.get("arg_schema") if isinstance(cap_cfg.get("arg_schema"), dict) else None,
                    enabled=True,
                    is_default=bool(cap_cfg.get("is_default", False)),
                    unit_credits=int(cap_cfg.get("unit_credits") or 0),
                ))
        db.commit()

    if package.get("type") == "upstream_mcp":
        config = package.get("config", {})
        upstream_name = config.get("upstream_name", "")
        import os
        upstream_url = os.environ.get(config.get("upstream_url_env", ""), "") or config.get("upstream_url_default", "")
        if upstream_name and upstream_url:
            urls = _load_upstream_urls()
            urls[upstream_name] = upstream_url
            _save_upstream_urls(urls)

    installed_list.append(body.package_id)
    installed_data["installed"] = installed_list
    _save_installed(installed_data)

    return {
        "message": f"已安装 {package.get('name', body.package_id)}，新增 {len(capabilities)} 个能力",
        "package_id": body.package_id,
        "capabilities_added": len(capabilities),
    }


class AddMcpRequest(BaseModel):
    name: str
    url: str


@router.post("/skills/add-mcp", summary="添加 MCP 连接（本地）")
def add_mcp(
    body: AddMcpRequest,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    name = body.name.strip()
    url = body.url.strip()
    if not name or not url:
        raise HTTPException(status_code=400, detail="名称和 URL 不能为空")

    # 1. Write to openclaw.json
    oc_config_path = _BASE_DIR / "openclaw" / "openclaw.json"
    if oc_config_path.exists():
        try:
            import re
            text = oc_config_path.read_text(encoding="utf-8")
            text = re.sub(r'//.*', '', text)
            config = json.loads(text)
        except Exception:
            config = {}
    else:
        config = {}

    mcp_servers = config.setdefault("mcp", {}).setdefault("servers", {})
    mcp_servers[name] = {"url": url}

    oc_config_path.parent.mkdir(parents=True, exist_ok=True)
    oc_config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # 2. Write to upstream_urls.json
    urls = _load_upstream_urls()
    urls[name] = url
    _save_upstream_urls(urls)

    # 3. Add to skill_registry.json so it shows in the store
    pkg_id = f"mcp_{name}"
    registry = _load_registry()
    packages = registry.setdefault("packages", {})
    if pkg_id not in packages:
        packages[pkg_id] = {
            "name": name,
            "description": f"MCP: {url}",
            "type": "remote_mcp",
            "config": {"mcp_url": url},
            "capabilities": {},
            "tags": ["mcp"],
        }
        p = _BASE_DIR / "skill_registry.json"
        p.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

    # 4. Mark as installed
    installed_data = _load_installed()
    installed_list = installed_data.get("installed", [])
    if pkg_id not in installed_list:
        installed_list.append(pkg_id)
        installed_data["installed"] = installed_list
        _save_installed(installed_data)

    return {
        "ok": True,
        "message": f"MCP '{name}' 已添加 ({url})。重启 OpenClaw Gateway 后生效。",
    }


@router.post("/skills/uninstall", summary="卸载技能包")
async def uninstall_skill(
    body: SkillInstallRequest,
    _: _ServerUser = Depends(get_current_user_media_edit),
    db: Session = Depends(get_db),
):
    registry = _load_registry()
    packages = registry.get("packages", {})
    package = packages.get(body.package_id, {})

    installed_data = _load_installed()
    installed_list = installed_data.get("installed", [])
    if body.package_id not in installed_list:
        raise HTTPException(status_code=400, detail="该技能包未安装")

    capabilities = package.get("capabilities", {})
    if capabilities:
        catalog = _load_local_catalog()
        for cap_id in capabilities:
            catalog.pop(cap_id, None)
            existing = db.query(CapabilityConfig).filter(CapabilityConfig.capability_id == cap_id).first()
            if existing:
                db.delete(existing)
        _save_local_catalog(catalog)
        db.commit()

    installed_list.remove(body.package_id)
    installed_data["installed"] = installed_list
    _save_installed(installed_data)

    return {"message": f"已卸载 {package.get('name', body.package_id)}", "package_id": body.package_id}
