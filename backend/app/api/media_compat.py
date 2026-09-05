"""Local-only video compatibility endpoint for the Online desktop client."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ..services.media_compat import create_poster, prepare_playback
from .auth import get_current_user_for_local

router = APIRouter()
_ROOT = Path(__file__).resolve().parents[3]
_CACHE_DIR = _ROOT / "cache" / "media_compat"
_MAX_VIDEO_BYTES = 1024 * 1024 * 1024
_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv"}
_CACHE_LOCKS: dict[str, asyncio.Lock] = {}
_MAX_CACHE_BYTES = 4 * 1024 * 1024 * 1024


def _cache_paths(url: str) -> tuple[Path, Path]:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{key}.mp4", _CACHE_DIR / f"{key}.jpg"


def _safe_filename(value: str) -> str:
    name = Path(str(value or "digital-human.mp4").replace("\\", "/")).name
    return name or "digital-human.mp4"


def _assert_fetchable_url(url: str) -> None:
    parsed = urlparse(url)
    host = str(parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return
    try:
        addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail="视频域名无法解析") from exc
    for entry in addresses:
        address = ipaddress.ip_address(entry[4][0])
        # Some desktop DNS/proxy setups resolve Volcengine TOS to the
        # benchmarking range 198.18.0.0/15. Allow only this narrow HTTPS
        # object-storage case; keep other private/reserved destinations blocked.
        tos_host = host.endswith(".volces.com") or host.endswith(".volces.com.cn")
        benchmark_tos = tos_host and parsed.scheme == "https" and address in ipaddress.ip_network("198.18.0.0/15")
        if (address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved) and not benchmark_tos:
            raise HTTPException(status_code=400, detail="不支持内网视频地址")


def _file_response(playback: Path, filename: str) -> FileResponse:
    return FileResponse(
        str(playback),
        media_type="video/mp4",
        headers={"Cache-Control": "private, max-age=86400", "Accept-Ranges": "bytes"},
    )


def _prune_cache() -> None:
    """Bound compatibility artifacts without touching original user assets."""
    try:
        files = [item for item in _CACHE_DIR.iterdir() if item.is_file()]
        total = sum(item.stat().st_size for item in files)
        if total <= _MAX_CACHE_BYTES:
            return
        for item in sorted(files, key=lambda path: path.stat().st_mtime):
            try:
                size = item.stat().st_size
                item.unlink()
                total -= size
            except OSError:
                continue
            if total <= _MAX_CACHE_BYTES * 0.85:
                break
    except OSError:
        return


@router.get("/api/media/compat", summary="本机视频兼容播放（H.264/AAC）")
async def local_media_compat(
    request: Request,
    url: str = Query(..., min_length=1, max_length=4000),
    filename: str = Query("digital-human.mp4", max_length=180),
    variant: str = Query("", max_length=16),
    token: str = Query("", max_length=4096),
):
    bearer = (request.headers.get("authorization") or "").strip()
    supplied_token = token.strip() or (bearer[7:].strip() if bearer.lower().startswith("bearer ") else "")
    if not supplied_token:
        raise HTTPException(status_code=401, detail="请先登录")
    await get_current_user_for_local(request, token=supplied_token)
    raw_url = str(url or "").strip()
    if raw_url.startswith(("blob:", "data:", "file:")):
        raise HTTPException(status_code=400, detail="本地临时地址不支持兼容转换")
    absolute_url = urljoin(str(request.base_url), raw_url) if raw_url.startswith("/") else raw_url
    parsed = urlparse(absolute_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="视频地址无效")
    _assert_fetchable_url(absolute_url)
    normalized_variant = (variant or "").strip().lower()
    if normalized_variant not in {"", "poster"}:
        raise HTTPException(status_code=400, detail="不支持的媒体变体")

    playback_path, poster_path = _cache_paths(absolute_url)
    key = hashlib.sha256(absolute_url.encode("utf-8")).hexdigest()
    lock = _CACHE_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        if not playback_path.is_file() or playback_path.stat().st_size <= 0:
            source_origin = f"{parsed.scheme}://{parsed.netloc}".lower()
            local_origin = str(request.base_url).rstrip("/").lower()
            source_host = str(parsed.hostname or "").lower()
            auth = f"Bearer {supplied_token}" if (
                source_origin == local_origin
                or source_host in {"localhost", "127.0.0.1", "::1"}
            ) else ""
            headers = {"User-Agent": "Lobster-Online/2.0", "Accept": "*/*"}
            if auth:
                headers["Authorization"] = auth
            try:
                timeout = httpx.Timeout(600.0, connect=15.0, read=600.0)
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
                    async with client.stream("GET", absolute_url, headers=headers) as response:
                        if response.status_code >= 400:
                            raise HTTPException(status_code=502, detail=f"视频下载失败 HTTP {response.status_code}")
                        content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                        suffix = Path(urlparse(str(response.url)).path).suffix.lower() or Path(parsed.path).suffix.lower()
                        if not content_type.startswith("video/") and suffix not in _VIDEO_EXTENSIONS:
                            raise HTTPException(status_code=400, detail="地址不是视频素材")
                        declared = int(response.headers.get("content-length") or 0)
                        if declared > _MAX_VIDEO_BYTES:
                            raise HTTPException(status_code=413, detail="视频文件过大")
                        with tempfile.TemporaryDirectory(prefix="media_compat_") as temp:
                            source = Path(temp) / "source.bin"
                            total = 0
                            with source.open("wb") as output:
                                async for chunk in response.aiter_bytes(256 * 1024):
                                    total += len(chunk)
                                    if total > _MAX_VIDEO_BYTES:
                                        raise HTTPException(status_code=413, detail="视频文件过大")
                                    output.write(chunk)
                            converted = await asyncio.to_thread(prepare_playback, source, playback_path)
                            if not converted:
                                raise HTTPException(status_code=500, detail="本机无法转换视频，请确认 ffmpeg 依赖完整")
                            await asyncio.to_thread(_prune_cache)
            except HTTPException:
                raise
            except (httpx.HTTPError, OSError) as exc:
                raise HTTPException(status_code=502, detail="视频下载失败") from exc

        if normalized_variant == "poster":
            if not poster_path.is_file() or poster_path.stat().st_size <= 0:
                if not await asyncio.to_thread(create_poster, playback_path, poster_path):
                    raise HTTPException(status_code=500, detail="视频封面生成失败")
            return FileResponse(str(poster_path), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
        return _file_response(playback_path, filename)
