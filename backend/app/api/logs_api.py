"""系统日志只读接口：GET /api/logs 返回 lobster/logs/app.log 末尾内容，供「日志」Tab 查看。"""
import asyncio
from collections import deque
import io
import json
import logging
import platform
import re
import sys
import tempfile
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from .auth import _ServerUser, get_current_user_media_edit
from .openclaw_config import build_openclaw_status_snapshot
from ..core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

# lobster 项目根目录（与 run.py 中 _root 一致，即含 backend 的目录）
_BASE = Path(__file__).resolve().parent.parent.parent.parent
_LOG_FILE = (_BASE / "logs" / "app.log").resolve()
_MAX_LINES = 5000
_DEFAULT_TAIL = 2000
_DIAGNOSTIC_TAIL_BYTES = 8 * 1024 * 1024   # 2026-09-30：原来 1.5MB 只覆盖几小时
_DIAGNOSTIC_CONFIG_BYTES = 256 * 1024

_LOG_CANDIDATES = [
    "logs/app.log",
    "logs/app.log.1",
    "logs/native_wechat_diagnostics.jsonl",
    "logs/native_wechat_diagnostics.jsonl.1",
    "logs/native_wechat_auto_reply.jsonl",
    "logs/native_wechat_auto_reply.jsonl.1",
    # 个人 WhatsApp 接管（桌面版 UIA）：诊断包里带上，才能看清"没识别到窗口"到底是哪种情况
    "logs/native_whatsapp.jsonl",
    "logs/native_whatsapp.jsonl.1",
    # 客户端界面「跳闪/跳动」取证（鼠标一动就闪的现场），随诊断包一起带走
    "logs/client_ui_trace.jsonl",
    "logs/client_ui_trace.jsonl.1",
    "app.log",
    "backend.log",
    "backend.log.1",
    "backend_err.log",
    "backend_stdout.log",
    "backend_stderr.log",
    "mcp.log",
    "mcp.log.1",
    "openclaw.log",
    "openclaw_err.log",
    "openclaw_stdout.log",
    "openclaw_stderr.log",
    "openclaw_stderr2.log",
    "launcher.log",
    "desktop_launcher.log",
    "desktop_launcher.log.1",
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
            lines = deque(maxlen=max(1, int(tail)))
            total = 0
            for line in f:
                total += 1
                lines.append(line)
    except OSError:
        return "", 0
    return "".join(lines), total


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
        "openclaw_tool_scope_enabled": getattr(settings, "openclaw_tool_scope_enabled", None),
        "chat_require_capability_cost_confirm": getattr(settings, "chat_require_capability_cost_confirm", None),
        "lobster_chat_generation_early_finish": getattr(settings, "lobster_chat_generation_early_finish", None),
    }


def _external_log_candidates() -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    startup_dir = _BASE / "logs" / "openclaw_startup"
    if startup_dir.is_dir():
        try:
            logs = sorted(
                [p for p in startup_dir.glob("openclaw-startup-*.log") if p.is_file()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            candidates.extend((f"logs/openclaw_startup/{p.name}", p) for p in logs[:4])
        except OSError:
            pass
    openclaw_temp = Path(tempfile.gettempdir()) / "openclaw"
    if openclaw_temp.is_dir():
        try:
            logs = sorted(
                [p for p in openclaw_temp.glob("openclaw-*.log") if p.is_file()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            candidates.extend((f"external/openclaw/{p.name}", p) for p in logs[:4])
        except OSError:
            pass
    return candidates


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

        for arcname, path in _external_log_candidates():
            try:
                text, size, truncated = _read_tail_text(path, _DIAGNOSTIC_TAIL_BYTES)
            except OSError as e:
                summary["files"].append({"path": arcname, "source": str(path), "error": str(e)})
                continue
            if truncated:
                text = f"[tail only: last {_DIAGNOSTIC_TAIL_BYTES} bytes of {size}]\n" + text
            zf.writestr(_safe_arcname(arcname), _redact_text(text))
            summary["files"].append(
                {"path": arcname, "source": str(path), "size": size, "truncated": truncated}
            )

        # 2026-09-30：排查「一直进行中/字幕合成中」用这两样：
        #   1) 运行目录清单（raw.mp4 多大、有没有 captioned.mp4 -> 判断卡在下载/ffmpeg/上传）
        #   2) job 状态文件（每个 job 的 status/post_status/post_error）
        try:
            runs_root = (_BASE / "skills" / "comfly_seedance_tvc_video" / "runs").resolve()
            if runs_root.is_dir():
                lines = []
                for item in sorted(runs_root.rglob("*")):
                    if not item.is_file():
                        continue
                    try:
                        stat = item.stat()
                        lines.append("%s  %s  %s" % (item.relative_to(runs_root), stat.st_size, datetime.fromtimestamp(stat.st_mtime).isoformat(" ", "seconds")))
                    except OSError:
                        continue
                zf.writestr("seedance_runs_files.txt", "\n".join(lines)[:2000000])
                summary["files"].append({"path": "seedance_runs_files.txt", "entries": len(lines)})
        except Exception as e:
            summary["files"].append({"path": "seedance_runs_files.txt", "error": str(e)})

        job_store = (_BASE / "_lobster_runtime" / "seedance_tvc_job_store.json").resolve()
        if job_store.is_file():
            try:
                text, size, truncated = _read_tail_text(job_store, 2 * 1024 * 1024)
                zf.writestr("seedance_job_store.json", _redact_text(text))
                summary["files"].append({"path": "seedance_job_store.json", "size": size, "truncated": truncated})
            except OSError as e:
                summary["files"].append({"path": "seedance_job_store.json", "error": str(e)})

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

        openclaw_status: dict | None = None
        try:
            openclaw_status = build_openclaw_status_snapshot()
        except Exception as e:
            openclaw_status = {"error": str(e)}
        zf.writestr(
            "openclaw_status.json",
            json.dumps(openclaw_status, ensure_ascii=False, indent=2) + "\n",
        )
        summary["openclaw_status"] = openclaw_status

        openclaw_config = (_BASE / "openclaw" / "openclaw.json").resolve()
        if openclaw_config.is_file():
            try:
                text, size, truncated = _read_tail_text(openclaw_config, _DIAGNOSTIC_CONFIG_BYTES)
                if truncated:
                    text = f"[tail only: last {_DIAGNOSTIC_CONFIG_BYTES} bytes of {size}]\n" + text
                zf.writestr("openclaw/openclaw.json.redacted", _redact_text(text))
                summary["files"].append(
                    {
                        "path": "openclaw/openclaw.json",
                        "size": size,
                        "truncated": truncated,
                        "redacted": True,
                    }
                )
            except OSError as e:
                summary["files"].append({"path": "openclaw/openclaw.json", "error": str(e)})

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
    resp = None
    last_error = None
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            for attempt in range(3):
                try:
                    resp = await client.post(
                        url,
                        headers=headers,
                        data={"client_info": json.dumps(summary, ensure_ascii=False)},
                        files={"file": (bundle_name, bundle_bytes, "application/zip")},
                    )
                    if resp.status_code not in {408, 425, 429, 500, 502, 503, 504} or attempt >= 2:
                        break
                    last_error = f"upstream returned HTTP {resp.status_code}"
                except httpx.HTTPError as exc:
                    last_error = str(exc)
                    if attempt >= 2:
                        raise
                await asyncio.sleep(1.0 if attempt == 0 else 2.0)
    except httpx.HTTPError as e:
        logger.warning("[diagnostics] upload failed after retries url=%s err=%s", url, last_error or e)
        raise HTTPException(status_code=502, detail=f"diagnostic upload failed: {e}") from e

    if resp is None:
        raise HTTPException(status_code=502, detail=f"diagnostic upload failed: {last_error or 'no response'}")

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


# ---------------------------------------------------------------------------
# 客户端界面「跳闪 / 跳动」取证（2026-10-13）
#
# 现象：某些页面上鼠标一移动，界面就闪/跳几下；但服务端日志里干干净净——
#      没有 GET /（没有 reload）、没有重新拉视图 HTML。
# 做法：static/js/ui-jump-trace.js 在客户端记录「谁把内容重画了、谁把页面滚了、
#      出事前鼠标停在哪、调用栈是谁」，判定成一次跳闪就 POST 到这里落盘，
#      文件进 _LOG_CANDIDATES，下次上传诊断包就能直接看到触发点。
# ---------------------------------------------------------------------------
_UI_TRACE_FILE = (_BASE / "logs" / "client_ui_trace.jsonl").resolve()
_UI_TRACE_MAX_BYTES = 2 * 1024 * 1024
_UI_TRACE_LOCK = threading.Lock()


def append_client_ui_trace(payload: Any) -> int:
    """追加一条界面取证记录到 logs/client_ui_trace.jsonl，返回写入字节数。"""
    record = payload if isinstance(payload, dict) else {"payload": payload}
    record.setdefault("received_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    data = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    path = _UI_TRACE_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    with _UI_TRACE_LOCK:
        try:
            if path.exists() and path.stat().st_size + len(data) > _UI_TRACE_MAX_BYTES:
                rotated = path.with_name(path.name + ".1")
                if rotated.exists():
                    rotated.unlink()
                path.replace(rotated)
        except OSError:
            pass
        with path.open("ab") as handle:
            handle.write(data)
    return len(data)


@router.post("/api/client-ui-trace", summary="客户端界面跳闪/跳动取证上报")
async def post_client_ui_trace(
    request: Request,
    _: _ServerUser = Depends(get_current_user_media_edit),
):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {"payload": payload}
    try:
        written = append_client_ui_trace(payload)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"write failed: {exc}") from exc
    events = payload.get("events")
    logger.info(
        "[ui-trace] POST events=%s reason=%s view=%s mouse_over=%s bytes=%s path=%s",
        len(events) if isinstance(events, list) else "-",
        payload.get("reason") or "",
        payload.get("view") or "",
        ((payload.get("mouse") or {}) if isinstance(payload.get("mouse"), dict) else {}).get("over") or "",
        written,
        _UI_TRACE_FILE,
    )
    return {"ok": True, "written": written, "path": str(_UI_TRACE_FILE)}


@router.get("/api/client-ui-trace", summary="读取客户端界面取证日志（末尾 N 条）")
async def get_client_ui_trace(
    tail: int = Query(default=200, ge=1, le=5000, description="返回最后 N 条"),
    _: _ServerUser = Depends(get_current_user_media_edit),
):
    path = _UI_TRACE_FILE
    if not path.exists():
        return PlainTextResponse("(空) 还没有界面取证记录", media_type="text/plain; charset=utf-8")
    loop = asyncio.get_event_loop()
    text, _total = await loop.run_in_executor(None, _read_log_tail, path, tail)
    return PlainTextResponse(text or "(空)", media_type="text/plain; charset=utf-8")
