from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_OEM_SERVER = "https://bhzn.top"
OEM_CODE_RE = re.compile(r"^[0-9]{4,12}$")
BRAND_MARK_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
SAFE_ASSET_SUFFIXES = {".exe", ".ico", ".jpeg", ".jpg", ".png", ".webp"}
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ASSET_BYTES = 16 * 1024 * 1024
DEFAULT_CACHE_MAX_AGE_SECONDS = 6 * 60 * 60


class OemBrandingError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_limited(response, limit: int) -> bytes:
    data = response.read(limit + 1)
    if len(data) > limit:
        raise OemBrandingError("OEM response is too large")
    return data


def _fetch_json(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "LobsterDesktop/OEM-1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(_read_limited(response, MAX_MANIFEST_BYTES).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read(MAX_MANIFEST_BYTES).decode("utf-8", errors="replace"))
            detail = str(body.get("detail") or "").strip() if isinstance(body, dict) else ""
        except (OSError, ValueError):
            detail = ""
        raise OemBrandingError(detail or f"OEM server returned HTTP {exc.code}") from exc
    if not isinstance(data, dict):
        raise OemBrandingError("OEM manifest must be an object")
    return data


def _download_asset(url: str, target: Path, expected_size: int, expected_sha256: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "LobsterDesktop/OEM-1.0"})
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.{os.getpid()}.part")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = _read_limited(response, min(MAX_ASSET_BYTES, expected_size + 1))
        if len(data) != expected_size:
            raise OemBrandingError(f"OEM asset size mismatch: {target.name}")
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise OemBrandingError(f"OEM asset checksum mismatch: {target.name}")
        partial.write_bytes(data)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)


def _cache_record_path(root: Path, code: str) -> Path:
    return root / "static" / "branding" / "cache" / "profiles" / f"{code}.json"


def _valid_cached_asset(root: Path, item: dict[str, Any]) -> bool:
    relative = str(item.get("relative_path") or "").replace("\\", "/").lstrip("/")
    expected_sha256 = str(item.get("sha256") or "").strip().lower()
    try:
        expected_size = int(item.get("size"))
    except (TypeError, ValueError):
        return False
    if not relative or not SHA256_RE.fullmatch(expected_sha256) or expected_size < 1:
        return False
    target = (root / Path(relative)).resolve()
    cache_root = (root / "static" / "branding" / "cache").resolve()
    if cache_root not in target.parents or not target.is_file() or target.stat().st_size != expected_size:
        return False
    return _sha256(target) == expected_sha256


def load_cached_oem_profile(root: Path, code: str) -> tuple[dict[str, Any] | None, float]:
    record_path = _cache_record_path(root, code)
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, 0.0
    if not isinstance(record, dict) or record.get("oem_code") != code:
        return None, 0.0
    mark = str(record.get("brand_mark") or "").strip().lower()
    profile = record.get("profile")
    assets = record.get("assets")
    if not BRAND_MARK_RE.fullmatch(mark) or not isinstance(profile, dict) or not isinstance(assets, list) or not assets:
        return None, 0.0
    if str(profile.get("mark") or "").strip().lower() != mark:
        return None, 0.0
    if not all(isinstance(item, dict) and _valid_cached_asset(root, item) for item in assets):
        return None, 0.0
    result = copy.deepcopy(profile)
    result["_oem_code"] = code
    result["_cache_profile_path"] = str(record_path.resolve())
    try:
        checked_at = float(record.get("checked_at") or 0)
    except (TypeError, ValueError):
        checked_at = 0.0
    return result, checked_at


def _validate_asset_url(server_base: str, raw_url: str) -> str:
    url = urllib.parse.urljoin(server_base.rstrip("/") + "/", raw_url)
    base_parts = urllib.parse.urlparse(server_base)
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in {"http", "https"} or parts.netloc != base_parts.netloc:
        raise OemBrandingError("OEM asset URL must use the configured server origin")
    return url


def _localize_profile_value(value: Any, browser_paths: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _localize_profile_value(item, browser_paths) for key, item in value.items()}
    if isinstance(value, list):
        return [_localize_profile_value(item, browser_paths) for item in value]
    if isinstance(value, str):
        return browser_paths.get(value, value)
    return value


def materialize_oem_profile(root: Path, server_base: str, code: str, payload: dict[str, Any]) -> dict[str, Any]:
    mark = str(payload.get("brand_mark") or "").strip().lower()
    version = str(payload.get("version") or "").strip()
    profile = payload.get("profile")
    assets = payload.get("assets")
    if payload.get("oem_code") != code or not BRAND_MARK_RE.fullmatch(mark):
        raise OemBrandingError("OEM manifest identity is invalid")
    if not SAFE_VERSION_RE.fullmatch(version) or not isinstance(profile, dict) or not isinstance(assets, list) or not assets:
        raise OemBrandingError("OEM manifest payload is invalid")
    if str(profile.get("mark") or mark).strip().lower() != mark:
        raise OemBrandingError("OEM profile brand mark does not match")

    relative_dir = Path("static") / "branding" / "cache" / mark / version
    target_dir = root / relative_dir
    browser_paths: dict[str, str] = {}
    cached_assets: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for raw_item in assets:
        if not isinstance(raw_item, dict):
            raise OemBrandingError("OEM asset entry is invalid")
        raw_url = str(raw_item.get("url") or "").strip()
        expected_sha256 = str(raw_item.get("sha256") or "").strip().lower()
        try:
            expected_size = int(raw_item.get("size"))
        except (TypeError, ValueError) as exc:
            raise OemBrandingError("OEM asset size is invalid") from exc
        filename = Path(urllib.parse.urlparse(raw_url).path).name
        if (
            not filename
            or filename in used_names
            or Path(filename).suffix.lower() not in SAFE_ASSET_SUFFIXES
            or not SHA256_RE.fullmatch(expected_sha256)
            or expected_size < 1
            or expected_size > MAX_ASSET_BYTES
        ):
            raise OemBrandingError("OEM asset metadata is invalid")
        used_names.add(filename)
        asset_url = _validate_asset_url(server_base, raw_url)
        relative_path = relative_dir / filename
        target = root / relative_path
        if not target.is_file() or target.stat().st_size != expected_size or _sha256(target) != expected_sha256:
            _download_asset(asset_url, target, expected_size, expected_sha256)
        browser_paths[raw_url] = "/" + relative_path.as_posix()
        cached_assets.append(
            {
                "key": str(raw_item.get("key") or filename),
                "relative_path": relative_path.as_posix(),
                "size": expected_size,
                "sha256": expected_sha256,
            }
        )

    localized = _localize_profile_value(copy.deepcopy(profile), browser_paths)
    localized["mark"] = mark
    install = localized.get("install") if isinstance(localized.get("install"), dict) else {}
    desktop_ico = str(install.get("desktop_ico") or "").lstrip("/")
    if desktop_ico:
        install["desktop_ico"] = desktop_ico
        localized["install"] = install

    record_path = _cache_record_path(root, code)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": int(payload.get("schema_version") or 1),
        "oem_code": code,
        "brand_mark": mark,
        "version": version,
        "checked_at": time.time(),
        "profile": localized,
        "assets": cached_assets,
    }
    partial = record_path.with_suffix(".json.part")
    partial.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(partial, record_path)
    localized["_oem_code"] = code
    localized["_cache_profile_path"] = str(record_path.resolve())
    return localized


def resolve_oem_branding(
    root: Path,
    code: str,
    server_base: str = DEFAULT_OEM_SERVER,
    *,
    cache_max_age_seconds: int = DEFAULT_CACHE_MAX_AGE_SECONDS,
    raise_on_error: bool = False,
) -> dict[str, Any] | None:
    normalized_code = str(code or "").strip()
    if not OEM_CODE_RE.fullmatch(normalized_code):
        return None
    cached, checked_at = load_cached_oem_profile(root, normalized_code)
    if cached is not None and time.time() - checked_at < max(0, cache_max_age_seconds):
        return cached

    base = str(server_base or DEFAULT_OEM_SERVER).strip().rstrip("/") or DEFAULT_OEM_SERVER
    endpoint = f"{base}/api/oem/bootstrap?{urllib.parse.urlencode({'code': normalized_code})}"
    try:
        payload = _fetch_json(endpoint, timeout=6)
        return materialize_oem_profile(root, base, normalized_code, payload)
    except Exception:
        if cached is not None:
            return cached
        if raise_on_error:
            raise
        return None
