from __future__ import annotations

import base64
import asyncio
import json
import mimetypes
import os
import struct
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx

from ..core.config import settings


MESHY_API_BASE = "https://api.meshy.ai/openapi/v1"
DEFAULT_TARGET_FORMATS = ("glb", "fbx", "obj", "usdz")


class MeshyError(RuntimeError):
    pass


def resolve_meshy_api_key() -> str:
    return (
        getattr(settings, "meshy_api_key", None)
        or os.environ.get("MESHY_API_KEY")
        or os.environ.get("LOBSTER_MESHY_API_KEY")
        or ""
    ).strip()


def is_configured() -> bool:
    return bool(resolve_meshy_api_key())


def _headers(api_key: Optional[str] = None) -> Dict[str, str]:
    key = (api_key or resolve_meshy_api_key()).strip()
    if not key:
        raise MeshyError("MESHY_API_KEY is not configured")
    return {"Authorization": f"Bearer {key}"}


def _guess_mime(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0]
    if mime and mime.startswith("image/"):
        return mime
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"


def image_path_to_data_url(path: Path) -> str:
    raw = path.read_bytes()
    if not raw:
        raise MeshyError(f"input image is empty: {path.name}")
    return f"data:{_guess_mime(path)};base64,{base64.b64encode(raw).decode('ascii')}"


def _coerce_target_formats(values: Optional[Iterable[str]]) -> List[str]:
    allowed = {"glb", "fbx", "obj", "usdz", "stl", "blend"}
    out: List[str] = []
    for item in values or DEFAULT_TARGET_FORMATS:
        fmt = str(item or "").strip().lower().lstrip(".")
        if fmt in allowed and fmt not in out:
            out.append(fmt)
    return out or ["glb"]


async def get_balance() -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{MESHY_API_BASE}/balance", headers=_headers())
    if resp.status_code >= 400:
        raise MeshyError(_response_error(resp))
    return resp.json()


def _response_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:
        text = (resp.text or "").strip()
        return text or f"Meshy HTTP {resp.status_code}"
    if isinstance(data, dict):
        msg = data.get("message") or data.get("detail") or data.get("error")
        if msg:
            return str(msg)
    return json.dumps(data, ensure_ascii=False)[:800]


async def _request_with_retries(
    method: str,
    url: str,
    *,
    timeout: float,
    attempts: int = 3,
    **kwargs: Any,
) -> httpx.Response:
    last_exc: Optional[BaseException] = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                return await client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            await asyncio.sleep(min(8, 1.5 * attempt))
    raise MeshyError(f"Meshy request disconnected after {attempts} attempts: {last_exc}")


async def create_image_to_3d_task(
    image_path: Path,
    *,
    quality: str = "high",
    target_formats: Optional[Iterable[str]] = None,
    texture_prompt: str = "",
) -> Dict[str, Any]:
    body = {
        "image_url": image_path_to_data_url(image_path),
        "ai_model": "meshy-6",
        "should_texture": quality != "draft",
        "enable_pbr": quality in {"high", "production"},
        "should_remesh": quality in {"high", "production"},
        "target_polycount": 100000 if quality in {"high", "production"} else 30000,
        "target_formats": _coerce_target_formats(target_formats),
        "image_enhancement": True,
    }
    if quality == "production":
        body["texture_prompt"] = texture_prompt or "PBR material, preserve original surface colors and fine details"
    resp = await _request_with_retries(
        "POST",
        f"{MESHY_API_BASE}/image-to-3d",
        timeout=90.0,
        headers={**_headers(), "Content-Type": "application/json"},
        json=body,
    )
    if resp.status_code >= 400:
        raise MeshyError(_response_error(resp))
    return resp.json()


async def create_multi_image_to_3d_task(
    image_paths: List[Path],
    *,
    quality: str = "high",
    target_formats: Optional[Iterable[str]] = None,
    texture_prompt: str = "",
) -> Dict[str, Any]:
    if len(image_paths) < 2:
        return await create_image_to_3d_task(image_paths[0], quality=quality, target_formats=target_formats, texture_prompt=texture_prompt)
    image_urls = [image_path_to_data_url(path) for path in image_paths[:4]]
    body = {
        "image_urls": image_urls,
        "ai_model": "meshy-6",
        "should_texture": quality != "draft",
        "enable_pbr": quality in {"high", "production"},
        "should_remesh": quality in {"high", "production"},
        "target_polycount": 100000 if quality in {"high", "production"} else 30000,
        "target_formats": _coerce_target_formats(target_formats),
    }
    if quality == "production":
        body["texture_prompt"] = texture_prompt or "PBR material, preserve the uploaded references, clean topology, production-ready hard-surface asset"
    resp = await _request_with_retries(
        "POST",
        f"{MESHY_API_BASE}/multi-image-to-3d",
        timeout=90.0,
        headers={**_headers(), "Content-Type": "application/json"},
        json=body,
    )
    if resp.status_code >= 400:
        raise MeshyError(_response_error(resp))
    return resp.json()


async def get_image_to_3d_task(task_id: str) -> Dict[str, Any]:
    resp = await _request_with_retries(
        "GET",
        f"{MESHY_API_BASE}/image-to-3d/{task_id}",
        timeout=30.0,
        headers=_headers(),
    )
    if resp.status_code >= 400:
        raise MeshyError(_response_error(resp))
    return resp.json()


async def get_multi_image_to_3d_task(task_id: str) -> Dict[str, Any]:
    resp = await _request_with_retries(
        "GET",
        f"{MESHY_API_BASE}/multi-image-to-3d/{task_id}",
        timeout=30.0,
        headers=_headers(),
    )
    if resp.status_code >= 400:
        raise MeshyError(_response_error(resp))
    return resp.json()


async def poll_task(task_id: str, *, mode: str, timeout_seconds: int = 900, interval_seconds: int = 8) -> Dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + max(60, timeout_seconds)
    last: Dict[str, Any] = {}
    while asyncio.get_running_loop().time() < deadline:
        if mode == "multi-image-to-3d":
            last = await get_multi_image_to_3d_task(task_id)
        else:
            last = await get_image_to_3d_task(task_id)
        status = str(last.get("status") or "").upper()
        if status in {"SUCCEEDED", "FAILED", "EXPIRED"}:
            return last
        await asyncio.sleep(max(2, interval_seconds))
    raise MeshyError(f"Meshy task timed out: {task_id}")


async def download_url(url: str, dest: Path, *, timeout_seconds: float = 180.0) -> None:
    if not url:
        raise MeshyError("download url is empty")
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise MeshyError(f"download failed HTTP {resp.status_code}")
            with dest.open("wb") as f:
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        f.write(chunk)
    if not dest.exists() or dest.stat().st_size <= 0:
        raise MeshyError(f"download produced empty file: {dest.name}")


def inspect_glb(path: Path) -> Dict[str, Any]:
    if not path.exists() or path.stat().st_size < 32:
        return {}
    with path.open("rb") as f:
        magic, version, length = struct.unpack("<4sII", f.read(12))
        if magic != b"glTF":
            return {"file_size": path.stat().st_size}
        chunk_len, chunk_type = struct.unpack("<I4s", f.read(8))
        if chunk_type != b"JSON":
            return {"file_size": path.stat().st_size, "glb_version": version}
        data = json.loads(f.read(chunk_len).decode("utf-8"))
    accessors = data.get("accessors") or []
    meshes = data.get("meshes") or []
    vertex_count = 0
    index_count = 0
    for mesh in meshes:
        for prim in mesh.get("primitives") or []:
            pos = (prim.get("attributes") or {}).get("POSITION")
            idx = prim.get("indices")
            if isinstance(pos, int) and pos < len(accessors):
                vertex_count += int(accessors[pos].get("count") or 0)
            if isinstance(idx, int) and idx < len(accessors):
                index_count += int(accessors[idx].get("count") or 0)
    return {
        "file_size": path.stat().st_size,
        "glb_version": version,
        "mesh_count": len(meshes),
        "material_count": len(data.get("materials") or []),
        "node_count": len(data.get("nodes") or []),
        "vertex_count": vertex_count,
        "triangle_count": index_count // 3 if index_count else None,
    }
