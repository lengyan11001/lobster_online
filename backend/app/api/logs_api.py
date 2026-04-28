"""系统日志只读接口：GET /api/logs 返回 lobster/logs/app.log 末尾内容，供「日志」Tab 查看。"""
import asyncio
import io
import json
import logging
import platform
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from .auth import _ServerUser, get_current_user_media_edit
from ..core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

# lobster 项目根目录（与 run.py 中 _root 一致，即含 backend 的目录）
_BASE = Path(__file__).resolve().parent.parent.parent.parent
_LOG_FILE = (_BASE / "logs" / "app.log").resolve()
_MAX_LINES = 5000
_DEFAULT_TAIL = 2000
_DIAGNOSTIC_TAIL_BYTES = 1536 * 1024
_DIAGNOSTIC_CONFIG_BYTES = 256 * 1024

_LOG_CANDIDATES = [
    "logs/app.log",
    "app.log",
    "backend.log",
    "backend_err.log",
    "backend_stdout.log",
    "backend_stderr.log",
    "mcp.log",
    "openclaw.log",
    "openclaw_err.log",
    "openclaw_stdout.log",
    "openclaw_stderr.log",
    "openclaw_stderr2.log",
    "launcher.log",
]
_CONFIG_CANDIDATES = [
    ".env",
    "CLIENT_CODE_VERSION.json",
    "static/client_version.json",
    "custom_configs.json",
    "sutui_config.json",
    "upstream_urls.json",
    "models_config.json",
    "skill_registry.json",
    "installed_packages.json",
    "twilio_whatsapp_config.json",
    "wecom_cloud_config.json",
]

_SECRET_LINE_RE = re.compile(
    r"(?im)^([^\n#]*(?:api[_-]?key|access[_-]?key|secret|token|password|passwd|authorization|cookie|mchnt_key|app_secret)[^\n:=]*\s*[:=]\s*)(.+)$"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_API_TOKEN_RE = re.compile(r"\b(?:sk|xai|ak)-[A-Za-z0-9._-]{12,}\b", re.IGNORECASE)


def _read_log_tail(path: Path, tail: int) -> tuple[str, int]:
    """同步读文件最后 tail 行，在 executor 中调用避免阻塞。返回 (文本, 总行数)。"""
    if not path.exists():
        return "", 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return "", 0
    n = len(lines)
    if n > tail:
        lines = lines[-tail:]
    return "".join(lines), n


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_text(text: str) -> str:
    text = _SECRET_LINE_RE.sub(lambda m: m.group(1) + "<redacted>", text or "")
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    text = _JWT_RE.sub("<jwt-redacted>", text)
    text = _API_TOKEN_RE.sub("<token-redacted>", text)
    return text


def _read_tail_text(path: Path, max_bytes: int) -> tuple[str, int, bool]:
    size = path.stat().st_size
    truncated = size > max_bytes
    with open(path, "rb") as f:
        if truncated:
            f.seek(max(0, size - max_bytes))
        data = f.read(max_bytes)
    return data.decode("utf-8", errors="replace"), size, truncated


def _safe_arcname(rel: str, suffix: str = "") -> str:
    cleaned = rel.replace("\\", "/").strip("/").replace("..", "_")
    return f"{cleaned}{suffix}" if suffix else cleaned


def _settings_summary() -> dict:
    return {
        "auth_server_base": getattr(settings, "auth_server_base", None),
        "lobster_brand_mark": getattr(settings, "lobster_brand_mark", None),
        "lobster_default_image_generate_model": getattr(settings, "lobster_default_image_generate_model", None),
        "lobster_default_sutui_chat_model": getattr(settings, "lobster_default_sutui_chat_model", None),
        "lobster_openclaw_primary_chat": getattr(settings, "lobster_openclaw_primary_chat", None),
        "lobster_openclaw_only_chat": getattr(settings, "lobster_openclaw_only_chat", None),
        "lobster_openclaw_chat_prefix_gate": getattr(settings, "lobster_openclaw_chat_prefix_gate", None),
        "chat_require_capability_cost_confirm": getattr(settings, "chat_require_capability_cost_confirm", None),
        "lobster_chat_generation_early_finish": getattr(settings, "lobster_chat_generation_early_finish", None),
    }


def _build_diagnostic_bundle() -> tuple[str, bytes, dict]:
    created_at = _utc_iso()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bundle_name = f"lobster-diagnostics-{stamp}.zip"
    summary = {
        "created_at": created_at,
        "client_root": str(_BASE),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "settings": _settings_summary(),
        "files": [],
    }
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in _LOG_CANDIDATES:
            path = (_BASE / rel).resolve()
            if not path.exists() or not path.is_file():
                continue
            try:
                text, size, truncated = _read_tail_text(path, _DIAGNOSTIC_TAIL_BYTES)
            except OSError as e:
                summary["files"].append({"path": rel, "error": str(e)})
                continue
            arcname = _safe_arcname(rel)
            if truncated:
                text = f"[tail only: last {_DIAGNOSTIC_TAIL_BYTES} bytes of {size}]\n" + text
            zf.writestr(arcname, _redact_text(text))
            summary["files"].append({"path": rel, "size": size, "truncated": truncated})

        for rel in _CONFIG_CANDIDATES:
            path = (_BASE / rel).resolve()
            if not path.exists() or not path.is_file():
                continue
            try:
                text, size, truncated = _read_tail_text(path, _DIAGNOSTIC_CONFIG_BYTES)
            except OSError as e:
                summary["files"].append({"path": rel, "error": str(e)})
                continue
            arcname = _safe_arcname(rel, ".redacted" if rel == ".env" else "")
            if truncated:
                text = f"[tail only: last {_DIAGNOSTIC_CONFIG_BYTES} bytes of {size}]\n" + text
            zf.writestr(arcname, _redact_text(text))
            summary["files"].append({"path": rel, "size": size, "truncated": truncated, "redacted": True})

        zf.writestr(
            "diagnostic_summary.json",
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        )
    return bundle_name, mem.getvalue(), summary


@router.get("/api/logs", summary="读取系统日志（末尾 N 行）")
async def get_logs(
    tail: int = Query(default=_DEFAULT_TAIL, ge=100, le=_MAX_LINES, description="返回最后 N 行"),
    _: _ServerUser = Depends(get_current_user_media_edit),
):
    """返回 lobster/logs/app.log 最后 tail 行，用于前端「日志」Tab 或排错。鉴权与素材库等一致（在线版用认证中心 JWT）。"""
    logger.info("[日志] GET /api/logs tail=%s path=%s exists=%s", tail, _LOG_FILE, _LOG_FILE.exists())
    if not _LOG_FILE.exists():
        logger.warning("[日志] 文件不存在: %s", _LOG_FILE)
        return PlainTextResponse(
            f"日志文件不存在: {_LOG_FILE}\n请确认已用 start.bat 或 run_backend 启动过至少一次。",
            status_code=404,
        )
    loop = asyncio.get_event_loop()
    text, total = await loop.run_in_executor(None, _read_log_tail, _LOG_FILE, tail)
    lines_returned = len(text.splitlines())
    logger.info("[日志] 返回 lines=%s total=%s", lines_returned, total)
    return PlainTextResponse(
        text if text else "(空)",
        media_type="text/plain; charset=utf-8",
        headers={"X-Log-Lines": str(lines_returned), "X-Log-Total-Lines": str(total)},
    )


@router.post("/api/logs/upload-diagnostics", summary="Build and upload a redacted diagnostic bundle")
async def upload_logs_diagnostics(
    request: Request,
    _: _ServerUser = Depends(get_current_user_media_edit),
):
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    server_base = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    if not server_base:
        raise HTTPException(status_code=503, detail="AUTH_SERVER_BASE is not configured")

    bundle_name, bundle_bytes, summary = _build_diagnostic_bundle()
    headers = {"Authorization": auth}
    installation_id = (request.headers.get("X-Installation-Id") or "").strip()
    if installation_id:
        headers["X-Installation-Id"] = installation_id

    url = f"{server_base}/api/diagnostics/upload"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                url,
                headers=headers,
                data={"client_info": json.dumps(summary, ensure_ascii=False)},
                files={"file": (bundle_name, bundle_bytes, "application/zip")},
            )
    except httpx.HTTPError as e:
        logger.warning("[diagnostics] upload failed url=%s err=%s", url, e)
        raise HTTPException(status_code=502, detail=f"diagnostic upload failed: {e}") from e

    try:
        payload = resp.json()
    except Exception:
        payload = {"detail": resp.text[:1000]}
    if resp.status_code >= 400:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        raise HTTPException(status_code=resp.status_code, detail=detail or "diagnostic upload rejected")

    logger.info(
        "[diagnostics] uploaded bundle=%s size=%s diagnostic_id=%s",
        bundle_name,
        len(bundle_bytes),
        payload.get("diagnostic_id") if isinstance(payload, dict) else None,
    )
    return {
        "ok": True,
        "diagnostic_id": payload.get("diagnostic_id") if isinstance(payload, dict) else None,
        "server": server_base,
        "bundle": {
            "filename": bundle_name,
            "size": len(bundle_bytes),
            "files": summary.get("files", []),
        },
        "server_response": payload,
    }
