"""YouTube Data API v3：使用 Refresh Token + 可选 HTTP 代理上传视频。"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlparse

import httplib2

logger = logging.getLogger(__name__)

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"

# httplib2 默认把 HTTP 308 当作「永久重定向」并强制要求 Location；
# YouTube/Google 可恢复上传协议用 308 表示「分块尚未传完」(Resume Incomplete)，不应当跳转跟随。
_HTTPLIB2_REDIRECT_CODES_NO_308 = frozenset((300, 301, 302, 303, 307))
_GOOGLE_PROXY_PROBE_URL = "https://oauth2.googleapis.com/token"
_SYSTEM_PROXY_CACHE_TTL_SEC = 120.0
_system_proxy_cache: Dict[str, Tuple[float, Optional[str]]] = {}


def _configure_httplib2_for_google_resumable_upload(http: httplib2.Http) -> httplib2.Http:
    http.redirect_codes = _HTTPLIB2_REDIRECT_CODES_NO_308
    return http


def _normalize_http_proxy_url(raw: str) -> Optional[str]:
    s = (raw or "").strip()
    if not s:
        return None
    u = urlparse(s)
    if u.scheme not in ("http", "https") or not u.hostname:
        return None
    port = u.port if u.port is not None else (443 if u.scheme == "https" else 8080)
    if u.username or u.password:
        user = quote(unquote(u.username or ""), safe="")
        pw = quote(unquote(u.password or ""), safe="")
        auth = f"{user}:{pw}@"
    else:
        auth = ""
    return f"{u.scheme}://{auth}{u.hostname}:{port}"


def resolve_windows_system_proxy_url(target_url: str = _GOOGLE_PROXY_PROBE_URL) -> Optional[str]:
    """解析 Windows 当前用户代理/PAC。

    Python/httpx 默认不会读 WinINET 的 PAC；浏览器能访问 Google 时，本机后端仍可能直连超时。
    这里用 .NET 的系统代理解析一次并短缓存，专供 YouTube/Google 出网链路 fallback。
    """
    if os.name != "nt":
        return None
    cache_key = target_url
    now = time.time()
    cached = _system_proxy_cache.get(cache_key)
    if cached and now - cached[0] < _SYSTEM_PROXY_CACHE_TTL_SEC:
        return cached[1]

    escaped_url = target_url.replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop';"
        f"$uri=[Uri]'{escaped_url}';"
        "$proxy=[System.Net.WebRequest]::GetSystemWebProxy().GetProxy($uri);"
        "if ($proxy -and $proxy.AbsoluteUri -and $proxy.AbsoluteUri -ne $uri.AbsoluteUri) "
        "{ [Console]::Out.Write($proxy.AbsoluteUri) }"
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=6.0,
            creationflags=flags,
        )
    except Exception as e:
        logger.debug("[youtube-proxy] 解析 Windows 系统代理失败: %s", e)
        _system_proxy_cache[cache_key] = (now, None)
        return None
    if proc.returncode != 0:
        logger.debug("[youtube-proxy] 解析 Windows 系统代理返回非 0: %s", (proc.stderr or "")[:300])
        _system_proxy_cache[cache_key] = (now, None)
        return None
    proxy_url = _normalize_http_proxy_url(proc.stdout or "")
    _system_proxy_cache[cache_key] = (now, proxy_url)
    if proxy_url:
        logger.info("[youtube-proxy] 使用 Windows 系统代理/PAC: %s", proxy_url)
    return proxy_url


def resolve_youtube_httpx_proxy_url(
    proxy_server: Optional[str],
    proxy_username: Optional[str],
    proxy_password: Optional[str],
    *,
    target_url: str = _GOOGLE_PROXY_PROBE_URL,
) -> Tuple[Optional[str], str]:
    """账号代理优先；账号未配置时使用 Windows 系统代理/PAC；最后才直连。"""
    explicit = build_httpx_proxy_url(proxy_server, proxy_username, proxy_password)
    if explicit:
        return explicit, "account"
    system_proxy = resolve_windows_system_proxy_url(target_url)
    if system_proxy:
        return system_proxy, "system"
    return None, "direct"


def youtube_httpx_client_kwargs(
    proxy_server: Optional[str],
    proxy_username: Optional[str],
    proxy_password: Optional[str],
    *,
    timeout: float = 30.0,
    target_url: str = _GOOGLE_PROXY_PROBE_URL,
) -> Tuple[Dict[str, Any], str]:
    proxy_url, source = resolve_youtube_httpx_proxy_url(
        proxy_server,
        proxy_username,
        proxy_password,
        target_url=target_url,
    )
    kwargs: Dict[str, Any] = {"timeout": timeout, "trust_env": False}
    if proxy_url:
        kwargs["proxy"] = proxy_url
    return kwargs, source


def _httplib2_http_for_proxy_url(proxy_url: Optional[str]) -> httplib2.Http:
    normalized = _normalize_http_proxy_url(proxy_url or "")
    if not normalized:
        return _configure_httplib2_for_google_resumable_upload(httplib2.Http())
    u = urlparse(normalized)
    from httplib2 import socks

    pi = httplib2.ProxyInfo(
        socks.PROXY_TYPE_HTTP,
        u.hostname or "",
        u.port or (443 if u.scheme == "https" else 8080),
        proxy_user=unquote(u.username) if u.username else None,
        proxy_pass=unquote(u.password) if u.password else None,
    )
    return _configure_httplib2_for_google_resumable_upload(httplib2.Http(proxy_info=pi))


def _youtube_api_error_reasons(http_error) -> List[str]:
    """从 Google API HttpError 的 JSON body 中解析 error.errors[].reason。"""
    out: List[str] = []
    try:
        raw = getattr(http_error, "content", None) or b""
        data = json.loads(raw.decode("utf-8"))
        for err in (data.get("error") or {}).get("errors") or []:
            if isinstance(err, dict):
                r = (err.get("reason") or "").strip()
                if r:
                    out.append(r)
    except Exception:
        pass
    return out


def _user_facing_youtube_error(http_error) -> Optional[str]:
    """已知 reason → 明确中文说明（避免把 youtubeSignupRequired 误报成 OAuth 未配置）。"""
    reasons = _youtube_api_error_reasons(http_error)
    if "youtubeSignupRequired" in reasons:
        return (
            "该 Google 账号尚未创建 YouTube 频道（YouTube 错误码 youtubeSignupRequired，接口会显示 HTTP 401）。"
            "请用同一账号在浏览器打开 https://www.youtube.com ，按提示完成「创建频道」后再上传。"
            "若只用邮箱登录过 Google、从未开过 YouTube，必须先建频道。"
        )
    if "insufficientPermissions" in reasons:
        return (
            "YouTube 返回权限不足（insufficientPermissions）。"
            "请确认 OAuth 授权时已勾选上传权限，并在 Google Cloud 控制台启用 YouTube Data API v3。"
        )
    return None


def build_httplib2_http_for_proxy(
    proxy_server: Optional[str],
    proxy_username: Optional[str],
    proxy_password: Optional[str],
) -> httplib2.Http:
    """账号代理优先；无账号代理时沿用 Windows 系统代理/PAC；否则直连。"""
    raw = (proxy_server or "").strip()
    if not raw:
        return _httplib2_http_for_proxy_url(resolve_windows_system_proxy_url(_GOOGLE_PROXY_PROBE_URL))

    u = urlparse(raw)
    if u.scheme not in ("http", "https"):
        raise ValueError("代理地址须以 http:// 或 https:// 开头，例如 http://1.2.3.4:8080")

    host = u.hostname
    if not host:
        raise ValueError("代理 URL 中缺少主机名")

    if u.port is not None:
        port = u.port
    else:
        port = 443 if u.scheme == "https" else 8080

    user = (proxy_username or "").strip() or (unquote(u.username) if u.username else None)
    pw = (proxy_password or "").strip() or (unquote(u.password) if u.password else None)

    from httplib2 import socks

    pi = httplib2.ProxyInfo(
        socks.PROXY_TYPE_HTTP,
        host,
        port,
        proxy_user=user or None,
        proxy_pass=pw or None,
    )
    return _configure_httplib2_for_google_resumable_upload(httplib2.Http(proxy_info=pi))


def build_httpx_proxy_url(
    proxy_server: Optional[str],
    proxy_username: Optional[str],
    proxy_password: Optional[str],
) -> Optional[str]:
    """供 httpx.AsyncClient(proxy=...) 使用；无代理返回 None。账号/密码规则与 build_httplib2_http_for_proxy 一致。"""
    raw = (proxy_server or "").strip()
    if not raw:
        return None
    u = urlparse(raw)
    if u.scheme not in ("http", "https"):
        raise ValueError("代理地址须以 http:// 或 https:// 开头，例如 http://1.2.3.4:8080")
    host = u.hostname
    if not host:
        raise ValueError("代理 URL 中缺少主机名")
    port = u.port if u.port is not None else (443 if u.scheme == "https" else 8080)
    user = (proxy_username or "").strip() or (unquote(u.username) if u.username else "")
    pw = (proxy_password or "").strip() or (unquote(u.password) if u.password else "")
    if user or pw:
        netloc = f"{quote(user, safe='')}:{quote(pw, safe='')}@{host}:{port}"
    else:
        netloc = f"{host}:{port}"
    return f"{u.scheme}://{netloc}"


def upload_local_video_file(
    *,
    file_path: str,
    title: str,
    description: str,
    privacy_status: str,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    proxy_server: Optional[str] = None,
    proxy_username: Optional[str] = None,
    proxy_password: Optional[str] = None,
    self_declared_made_for_kids: bool = False,
    contains_synthetic_media: Optional[bool] = None,
    category_id: str = "22",
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """同步上传；大文件使用可恢复上传。

    YouTube Data API videos.insert 与「本机 Lobster 实际传入」对照要点：
    - snippet: title, description, categoryId；可选 tags（≤500 字符规则由 YouTube 约束）
    - status: privacyStatus；selfDeclaredMadeForKids（COPPA，须在工作室或 API 中明确）；
      containsSyntheticMedia（AI/合成内容披露；由上层按素材类型传入布尔值）
    未实现但 API 支持的常见字段示例：status.publishAt（定时发布）、status.license、
    recordingDetails、localizations 等，需另接 videos.update 或扩展 body。
    """
    from google.oauth2.credentials import Credentials
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"视频文件不存在: {file_path}")

    creds = Credentials(
        token=None,
        refresh_token=refresh_token.strip(),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id.strip(),
        client_secret=client_secret.strip(),
        scopes=[YOUTUBE_UPLOAD_SCOPE],
    )

    base_http = build_httplib2_http_for_proxy(proxy_server, proxy_username, proxy_password)
    authed_http = AuthorizedHttp(creds, http=base_http)
    youtube = build("youtube", "v3", http=authed_http)

    snippet: Dict[str, Any] = {
        "title": (title or "").strip() or "Untitled",
        "description": (description or "").strip(),
        "categoryId": (category_id or "22").strip() or "22",
    }
    if tags:
        snippet["tags"] = list(tags)[:50]

    status: Dict[str, Any] = {
        "privacyStatus": privacy_status,
        # COPPA：不在 API 中声明时，工作室常强制补填，易长时间 Pending
        "selfDeclaredMadeForKids": bool(self_declared_made_for_kids),
    }
    if contains_synthetic_media is not None:
        status["containsSyntheticMedia"] = bool(contains_synthetic_media)

    body: Dict[str, Any] = {"snippet": snippet, "status": status}

    media = MediaFileUpload(
        str(p),
        mimetype="video/*",
        chunksize=256 * 1024,
        resumable=True,
    )

    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    try:
        while response is None:
            _status, response = request.next_chunk()
    except HttpError as e:
        custom = _user_facing_youtube_error(e)
        if custom:
            raise RuntimeError(custom) from e
        parts = [f"HTTP {e.resp.status}"]
        if getattr(e, "reason", None):
            parts.append(f"message={e.reason}")
        ed = getattr(e, "error_details", None)
        if ed:
            parts.append(f"details={ed}")
        if len(parts) == 1:
            parts.append(str(e))
        raise RuntimeError(
            "YouTube/Google 接口返回错误（与技能商店管理员无关；多为 OAuth 范围、API 未启用、频道未创建或配额）。"
            + "; ".join(parts)
        ) from e

    if not response or "id" not in response:
        raise RuntimeError("上传完成但未返回 video id")

    vid = response["id"]
    logger.info("[youtube-upload] ok video_id=%s", vid)
    return {"video_id": vid, "response": response}
