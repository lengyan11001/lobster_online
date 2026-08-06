from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_DESKTOP_DIR = Path(__file__).resolve().parent
if str(_DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(_DESKTOP_DIR))

try:
    from desktop.oem_branding import BRAND_MARK_RE, DEFAULT_OEM_SERVER, OEM_CODE_RE, resolve_oem_branding
except ModuleNotFoundError:
    from oem_branding import BRAND_MARK_RE, DEFAULT_OEM_SERVER, OEM_CODE_RE, resolve_oem_branding


def resolve_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def read_env(root: Path) -> dict[str, str]:
    path = root / ".env"
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for raw in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def write_oem_code(root: Path, code: str) -> None:
    path = root / ".env"
    template = root / ".env.example"
    source = path if path.is_file() else template
    lines = source.read_text(encoding="utf-8-sig", errors="ignore").splitlines() if source.is_file() else []
    replacements = {"LOBSTER_BRAND_MARK": code, "LOBSTER_OEM_CODE": code}
    found: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else ""
        if key in replacements:
            output.append(f"{key}={replacements[key]}")
            found.add(key)
        else:
            output.append(line)
    for key, value in replacements.items():
        if key not in found:
            if output and output[-1].strip():
                output.append("")
            output.append(f"{key}={value}")
    partial = path.with_suffix(".env.part")
    partial.write_text("\n".join(output) + "\n", encoding="utf-8")
    os.replace(partial, path)


def _safe_launcher_filename(raw: object) -> str:
    value = str(raw or "").strip()
    if (
        not value
        or len(value) > 120
        or Path(value).name != value
        or not value.lower().endswith(".exe")
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
    ):
        raise RuntimeError("服务器未配置有效的品牌启动程序名称")
    return value


def _safe_shortcut_filename(raw: object) -> str:
    value = str(raw or "").strip()
    if (
        not value
        or len(value) > 120
        or Path(value).name != value
        or not value.lower().endswith(".lnk")
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
    ):
        raise RuntimeError("旧品牌快捷方式名称无效")
    return value


def _desktop_directories() -> list[Path]:
    candidates: list[Path] = []
    if os.name == "nt":
        try:
            import ctypes

            buffer = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer) == 0 and buffer.value:
                candidates.append(Path(buffer.value))
        except Exception:
            pass
    user_profile = str(os.environ.get("USERPROFILE") or "").strip()
    if user_profile:
        candidates.append(Path(user_profile) / "Desktop")
    for key in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        one_drive = str(os.environ.get(key) or "").strip()
        if one_drive:
            candidates.append(Path(one_drive) / "Desktop")
    result: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = os.path.normcase(str(path.resolve()))
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _read_cached_oem_record(root: Path, code: str) -> tuple[Path, dict] | None:
    if not OEM_CODE_RE.fullmatch(code):
        return None
    path = root / "static" / "branding" / "cache" / "profiles" / f"{code}.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("oem_code") != code:
        return None
    mark = str(record.get("brand_mark") or "").strip().lower()
    profile = record.get("profile")
    if not BRAND_MARK_RE.fullmatch(mark) or not isinstance(profile, dict):
        return None
    return path, record


def cleanup_previous_oem(root: Path, previous_code: str, current_code: str, current_profile: dict) -> None:
    if not previous_code or previous_code == current_code:
        return
    cached = _read_cached_oem_record(root, previous_code)
    if cached is None:
        return
    record_path, record = cached
    previous_mark = str(record["brand_mark"]).strip().lower()
    previous_profile = record["profile"]
    previous_install = previous_profile.get("install") if isinstance(previous_profile.get("install"), dict) else {}
    current_install = current_profile.get("install") if isinstance(current_profile.get("install"), dict) else {}
    current_mark = str(current_profile.get("mark") or "").strip().lower()

    try:
        previous_launcher = _safe_launcher_filename(previous_install.get("launcher_filename"))
    except RuntimeError:
        previous_launcher = ""
    try:
        current_launcher = _safe_launcher_filename(current_install.get("launcher_filename"))
    except RuntimeError:
        current_launcher = ""
    if previous_launcher and previous_launcher != current_launcher:
        (root / previous_launcher).unlink(missing_ok=True)

    try:
        previous_shortcut = _safe_shortcut_filename(previous_install.get("shortcut_lnk_name"))
    except RuntimeError:
        previous_shortcut = ""
    try:
        current_shortcut = _safe_shortcut_filename(current_install.get("shortcut_lnk_name"))
    except RuntimeError:
        current_shortcut = ""
    if previous_shortcut and previous_shortcut != current_shortcut:
        for desktop in _desktop_directories():
            (desktop / previous_shortcut).unlink(missing_ok=True)

    record_path.unlink(missing_ok=True)
    if previous_mark != current_mark:
        cache_root = (root / "static" / "branding" / "cache").resolve()
        previous_cache = (cache_root / previous_mark).resolve()
        if previous_cache.parent == cache_root and previous_cache.is_dir():
            shutil.rmtree(previous_cache)


def cleanup_inactive_oems(root: Path, current_code: str, current_profile: dict) -> None:
    profiles_dir = root / "static" / "branding" / "cache" / "profiles"
    if not profiles_dir.is_dir():
        return
    for path in list(profiles_dir.glob("*.json")):
        code = path.stem
        if code != current_code and OEM_CODE_RE.fullmatch(code):
            cleanup_previous_oem(root, code, current_code, current_profile)


def _restore_env(path: Path, existed: bool, content: bytes) -> None:
    if not existed:
        path.unlink(missing_ok=True)
        return
    partial = path.with_suffix(".env.restore")
    partial.write_bytes(content)
    os.replace(partial, path)


def install_brand_launcher(root: Path, profile: dict) -> Path:
    install = profile.get("install") if isinstance(profile.get("install"), dict) else {}
    raw_source = str(install.get("launcher_exe") or "").replace("\\", "/").lstrip("/")
    filename = _safe_launcher_filename(install.get("launcher_filename"))
    source = (root / Path(raw_source)).resolve()
    cache_root = (root / "static" / "branding" / "cache").resolve()
    if cache_root not in source.parents or not source.is_file() or source.suffix.lower() != ".exe":
        raise RuntimeError("品牌启动程序没有下载完成")
    target = root / filename
    partial = root / f".{filename}.{os.getpid()}.part"
    try:
        shutil.copy2(source, partial)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def run_install(root: Path, code: str) -> None:
    install = root / "install.bat"
    if os.name != "nt" or not install.is_file():
        raise RuntimeError("客户端目录中缺少 install.bat")
    env = os.environ.copy()
    env["LOBSTER_FACTORY_CONFIG"] = "1"
    env["LOBSTER_SKIP_INSTALL_PAUSE"] = "1"
    env["LOBSTER_SKIP_DESKTOP_SHORTCUT"] = "0"
    env["LOBSTER_BRAND_MARK"] = code
    env["LOBSTER_OEM_CODE"] = code
    try:
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", str(install)],
            cwd=str(root),
            env=env,
            timeout=30 * 60,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("依赖安装超过 30 分钟，请检查安装窗口") from exc
    if result.returncode != 0:
        raise RuntimeError(f"依赖安装失败，退出码 {result.returncode}")


def configure(root: Path, code: str) -> tuple[str, Path]:
    normalized = str(code or "").strip()
    if not OEM_CODE_RE.fullmatch(normalized):
        raise RuntimeError("请输入 4 到 12 位数字 OEM 编号")
    env = read_env(root)
    previous_code = str(env.get("LOBSTER_OEM_CODE") or "").strip()
    server_base = (
        os.environ.get("LOBSTER_OEM_BOOTSTRAP_BASE")
        or env.get("LOBSTER_OEM_BOOTSTRAP_BASE")
        or os.environ.get("AUTH_SERVER_BASE")
        or env.get("AUTH_SERVER_BASE")
        or DEFAULT_OEM_SERVER
    )
    profile = resolve_oem_branding(
        root,
        normalized,
        server_base,
        cache_max_age_seconds=0,
        raise_on_error=True,
    )
    if not profile:
        raise RuntimeError("编号无效或品牌资源下载失败，请检查网络和编号")
    launcher = install_brand_launcher(root, profile)
    env_path = root / ".env"
    env_existed = env_path.is_file()
    env_content = env_path.read_bytes() if env_existed else b""
    write_oem_code(root, normalized)
    try:
        run_install(root, normalized)
    except Exception:
        _restore_env(env_path, env_existed, env_content)
        raise
    cleanup_previous_oem(root, previous_code, normalized, profile)
    cleanup_inactive_oems(root, normalized, profile)
    name = str(profile.get("document_title") or profile.get("display_name") or profile.get("mark") or normalized)
    return name, launcher


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure this Online installation for an OEM factory code")
    parser.add_argument("--code", required=True)
    parser.add_argument("--root", type=Path, default=resolve_root())
    args = parser.parse_args()
    try:
        name, launcher = configure(args.root.resolve(), args.code)
    except Exception as exc:
        detail = base64.b64encode(str(exc).encode("utf-8")).decode("ascii")
        print(f"ERROR64\t{detail}", flush=True)
        return 1
    encoded_name = base64.b64encode(name.encode("utf-8")).decode("ascii")
    encoded_launcher = base64.b64encode(str(launcher).encode("utf-8")).decode("ascii")
    print(f"OK64\t{encoded_name}\t{encoded_launcher}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
