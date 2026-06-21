"""Local asset storage/export path helpers.

The asset database stores filenames only, so the internal asset directory must
remain stable. The user-configurable path is for manual export/download from
the asset library.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException

_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
_CUSTOM_CONFIGS_FILE = _BASE_DIR / "custom_configs.json"
_CONFIG_KEY = "ASSET_LIBRARY_EXPORT_DIR"


def get_internal_assets_dir() -> Path:
    path = _BASE_DIR / "assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_default_asset_export_dir() -> Path:
    return _BASE_DIR / "素材库"


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


def _normalize_export_dir(value: str | None) -> Path:
    raw = os.path.expandvars(str(value or "").strip().strip('"'))
    if not raw:
        return get_default_asset_export_dir().resolve()
    if any(ch in raw for ch in ("\x00", "\n", "\r")):
        raise HTTPException(status_code=400, detail="素材路径包含非法字符")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (_BASE_DIR / path)
    try:
        return path.resolve()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"素材路径无效: {exc}") from exc


def get_asset_export_dir() -> Path:
    data = _load_custom_configs()
    configs = data.get("configs")
    raw = configs.get(_CONFIG_KEY) if isinstance(configs, dict) else ""
    path = _normalize_export_dir(str(raw or ""))
    path.mkdir(parents=True, exist_ok=True)
    return path


def set_asset_export_dir(value: str | None) -> dict[str, Any]:
    path = _normalize_export_dir(value)
    if path.exists() and not path.is_dir():
        raise HTTPException(status_code=400, detail="素材路径已存在但不是文件夹")
    path.mkdir(parents=True, exist_ok=True)

    data = _load_custom_configs()
    configs = data.get("configs")
    if not isinstance(configs, dict):
        configs = {}
        data["configs"] = configs

    raw = str(value or "").strip()
    default_path = get_default_asset_export_dir().resolve()
    if not raw or path == default_path:
        configs.pop(_CONFIG_KEY, None)
    else:
        configs[_CONFIG_KEY] = str(path)
    _save_custom_configs(data)
    return get_asset_path_settings()


def get_asset_export_dir_for_media(media_type: str) -> Path:
    folder_name = {
        "image": "图片",
        "video": "视频",
        "document": "文档",
        "audio": "音频",
    }.get((media_type or "").strip().lower(), "其他")
    path = get_asset_export_dir() / folder_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_asset_path_settings() -> dict[str, Any]:
    export_dir = get_asset_export_dir()
    default_dir = get_default_asset_export_dir().resolve()
    return {
        "ok": True,
        "internal_assets_dir": str(get_internal_assets_dir().resolve()),
        "default_export_dir": str(default_dir),
        "export_dir": str(export_dir),
        "using_default_export_dir": export_dir.resolve() == default_dir,
    }
