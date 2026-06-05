#!/usr/bin/env python3
"""
启动前在线客户端代码热更新（纯代码 zip，不含 python/nodejs/deps 等大依赖）。

- 仅在 .env 配置 CLIENT_CODE_MANIFEST_URL（HTTPS）时拉取 manifest。
- 本地版本：CLIENT_CODE_VERSION.json 的 build（整数）与 version（语义版本，默认 1.0.0）。
- 满足任一即更新：① 服务端 build 更大；② build 相同且 manifest.version 高于本地（如 1.0.0 → 1.0.1，便于只发「小版本」包）。
- 兼容旧 manifest：下载 bundle_url，校验 sha256 后，对 manifest.paths 所列路径做「整路径覆盖」
  （目录则先删再拷，文件则覆盖）；绝不触碰 python/、deps/、browser_chromium/、nodejs 可执行文件等。
- 新 manifest 可额外下发 patches/resources：新 updater 会优先应用增量补丁；补丁失败时回退到旧
  bundle_url 全量包。老 updater 会忽略新字段，继续走 bundle_url。
- openclaw/：覆盖前保留本地 workspace、运行态目录、.env/登录态文件；gateway token 跟随 OTA 包覆盖，
  保证根 .env 与 openclaw.json 一致；覆盖后把 zip 内
  openclaw/workspace/LOBSTER_CHAT_POLICY_*.md 合并进保留后的 workspace（避免 OTA 丢策略导致 /chat 不调 MCP）。

禁止静默伪装成功：校验失败或解压失败时不改本地代码。
"""
from __future__ import annotations

import datetime
import errno
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


def _ssl_context(*, allow_unverified: bool = False) -> ssl.SSLContext:
    """构建 SSL context：优先 certifi → 系统 CA → 不验证（兜底）。"""
    if allow_unverified:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    try:
        ctx = ssl.create_default_context()
        if ctx.get_ca_certs():
            return ctx
    except Exception:
        pass
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "CLIENT_CODE_VERSION.json"
# 供纯静态启动（serve_online_client）读取，与 CLIENT_CODE_VERSION.json 同步
STATIC_CLIENT_VERSION_FILE = ROOT / "static" / "client_version.json"
DEFAULT_CLIENT_SEMVER = "1.0.0"

# 与 pack_code.sh 思路一致：仅代码与脚本，无嵌入式运行时与 wheel
DEFAULT_PATHS: tuple[str, ...] = (
    "scripts",
    "backend",
    "desktop",
    "mcp",
    "static",
    "publisher",
    "skills",
    "skill_registry.json",
    "upstream_urls.json",
    ".env",
    "必火智能AI.exe",
    "openclaw",
    "requirements.txt",
    ".env.example",
    "install.bat",
    "start.bat",
    "run_backend.bat",
    "run_mcp.bat",
    "nodejs/package.json",
    "nodejs/package-lock.json",
    "nodejs/ensure-npm-cli.mjs",
    "nodejs/run-npm.mjs",
    "nodejs/.gitignore",
    # Version must be applied last. If a large path such as skills/ fails or
    # times out, writing this first makes the next launch skip the unfinished OTA.
    "CLIENT_CODE_VERSION.json",
)

# 可选：整包 node 依赖（体积大）；一般发 OTA 仅用 DEFAULT_PATHS，目标机点授权在线安装即可
DEFAULT_PATHS_WITH_NODEJS_DEPS: tuple[str, ...] = DEFAULT_PATHS + (
    "nodejs/.openclaw/npm",
    "nodejs/node_modules",
)

BLOCKED_PREFIXES = (
    "python/",
    "python\\",
    "deps/",
    "deps\\",
    "browser_chromium/",
    "browser_chromium\\",
    ".git/",
    ".git\\",
    "nodejs/node.exe",
    "nodejs/node",
)
BLOCKED_EXACT = frozenset(
    {
        "openclaw/.env",
        "openclaw/.channel_fallback.json",
        "openclaw/.weixin_login_last.json",
        "openclaw/update-check.json",
    }
)
ALLOWED_NODEJS_EXACT = frozenset(
    {
        "nodejs/package.json",
        "nodejs/package-lock.json",
        "nodejs/ensure-npm-cli.mjs",
        "nodejs/run-npm.mjs",
        "nodejs/.gitignore",
    }
)
# 覆盖整树：另一机解压后 OpenClaw / 微信 / npm spawn 即就绪（不含 node.exe）
ALLOWED_NODEJS_TREE_PREFIXES: tuple[str, ...] = (
    "nodejs/node_modules",
    "nodejs/.openclaw/npm",
)

# 与 backend chat 读取路径一致；OTA 宜随包更新，安装机保留其余 workspace 文件
_OPENCLAW_POLICY_FILENAMES = ("LOBSTER_CHAT_POLICY_INTRO.md", "LOBSTER_CHAT_POLICY_TOOLS.md")
_PRESERVED_STATIC_REL_PATHS = ("static/hifly_previews",)
_PRESERVED_DESKTOP_REL_PATHS = ("desktop/webview2",)
_RESOURCE_STATE_DIR = ROOT / "static" / ".resource_packs"
_DESKTOP_EXE_NAME = "必火智能AI.exe"
_DESKTOP_EXE_NAMES = (_DESKTOP_EXE_NAME, "必火AI员工.exe")
_PENDING_UPDATE_DIR = ROOT / ".updates"
_PENDING_EXE_MARKER = _PENDING_UPDATE_DIR / "pending_exe_replace.json"
_STOP_CLIENT_SERVICES_DONE = False


def _load_dotenv_simple(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


def _local_build() -> int:
    if not VERSION_FILE.is_file():
        return 0
    try:
        data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
        b = data.get("build")
        return int(b) if b is not None else 0
    except Exception:
        return 0


def _local_semver() -> str:
    if not VERSION_FILE.is_file():
        return DEFAULT_CLIENT_SEMVER
    try:
        data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
        v = str(data.get("version", "") or "").strip()
        return v if v else DEFAULT_CLIENT_SEMVER
    except Exception:
        return DEFAULT_CLIENT_SEMVER


def _semver_is_newer(remote: str, local: str) -> bool:
    """manifest 的 version 是否严格高于本机（支持 1.0.1 / v1.2.3）。"""
    r = (remote or "").strip().lstrip("vV")
    l = (local or "").strip().lstrip("vV")
    if not r or not l:
        return False
    if r == l:
        return False
    try:
        from packaging.version import Version

        return Version(r) > Version(l)
    except Exception:
        # 无 packaging 或非常规串：按数字段比较
        def _parts(x: str) -> list[int]:
            out: list[int] = []
            for seg in x.split("."):
                n = ""
                for c in seg:
                    if c.isdigit():
                        n += c
                    else:
                        break
                out.append(int(n) if n else 0)
            return out or [0]

        rp, lp = _parts(r), _parts(l)
        ln = max(len(rp), len(lp))
        rp.extend([0] * (ln - len(rp)))
        lp.extend([0] * (ln - len(lp)))
        return tuple(rp) > tuple(lp)


def _save_local_build(build: int, version_from_manifest: str | None = None) -> None:
    prev: dict = {}
    if VERSION_FILE.is_file():
        try:
            prev = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
            if not isinstance(prev, dict):
                prev = {}
        except Exception:
            prev = {}
    prev["build"] = int(build)
    applied = datetime.datetime.utcnow().isoformat() + "Z"
    prev["applied_at"] = applied
    mv = (version_from_manifest or "").strip()
    if mv:
        prev["version"] = mv
    else:
        ex = str(prev.get("version", "")).strip()
        prev["version"] = ex if ex else DEFAULT_CLIENT_SEMVER
    semver = str(prev.get("version", "") or DEFAULT_CLIENT_SEMVER).strip() or DEFAULT_CLIENT_SEMVER
    prev["version"] = semver
    VERSION_FILE.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        STATIC_CLIENT_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATIC_CLIENT_VERSION_FILE.write_text(
            json.dumps(
                {"version": semver, "build": int(build), "applied_at": applied},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def _urlopen_with_fallback(req: urllib.request.Request, timeout: int) -> bytes:
    """先用正常 SSL 验证；若证书校验失败则降级为不验证模式重试。"""
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            return resp.read()
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e) or "SSL" in str(e).upper():
            print(f"[code] [WARN] SSL 证书验证失败，降级为不验证模式: {e}", flush=True)
            req2 = urllib.request.Request(req.full_url, headers=dict(req.headers))
            with urllib.request.urlopen(req2, timeout=timeout, context=_ssl_context(allow_unverified=True)) as resp:
                return resp.read()
        raise


def _fetch_json(url: str, timeout: int = 45) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "LobsterClientCode/1.0"})
    raw = _urlopen_with_fallback(req, timeout)
    return json.loads(raw.decode("utf-8"))


def _download_file(url: str, dest: Path, timeout: int = 300) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "LobsterClientCode/1.0"})
    dest.write_bytes(_urlopen_with_fallback(req, timeout))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _locked_file_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if not isinstance(exc, OSError):
        return False
    winerror = getattr(exc, "winerror", None)
    if winerror in {5, 32, 33}:
        return True
    return getattr(exc, "errno", None) in {errno.EACCES, errno.EPERM}


def _remove_pending_exe_marker_for_target(dst: Path) -> None:
    if not _PENDING_EXE_MARKER.is_file():
        return
    try:
        data = json.loads(_PENDING_EXE_MARKER.read_text(encoding="utf-8"))
        target = ROOT / _norm_rel(str(data.get("target_path") or "")).replace("/", os.sep)
        if target.resolve() == dst.resolve():
            _PENDING_EXE_MARKER.unlink(missing_ok=True)
    except Exception:
        pass


def _stage_pending_exe_replace(src: Path, dst: Path, exc: BaseException) -> None:
    _PENDING_UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    sha = _sha256_file(src)
    pending = _PENDING_UPDATE_DIR / f"{dst.name}.{sha[:16]}.pending"
    shutil.copy2(src, pending)
    payload = {
        "target_path": _norm_rel(str(dst.relative_to(ROOT))),
        "pending_path": _norm_rel(str(pending.relative_to(ROOT))),
        "sha256": sha,
        "staged_at": datetime.datetime.utcnow().isoformat() + "Z",
        "reason": str(exc)[:500],
    }
    _PENDING_EXE_MARKER.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[code] [WARN] {dst.name} 正在被占用，已暂存为下次启动替换: {payload['pending_path']}",
        flush=True,
    )


def _apply_pending_exe_replace() -> None:
    if not _PENDING_EXE_MARKER.is_file():
        return
    try:
        data = json.loads(_PENDING_EXE_MARKER.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[code] [WARN] pending exe 标记损坏，已忽略: {exc}", flush=True)
        _PENDING_EXE_MARKER.unlink(missing_ok=True)
        return
    target_rel = _norm_rel(str(data.get("target_path") or ""))
    pending_rel = _norm_rel(str(data.get("pending_path") or ""))
    if not target_rel or not pending_rel:
        _PENDING_EXE_MARKER.unlink(missing_ok=True)
        return
    dst = ROOT / target_rel.replace("/", os.sep)
    pending = ROOT / pending_rel.replace("/", os.sep)
    if not pending.is_file():
        print(f"[code] [WARN] pending exe 文件不存在，已清理标记: {pending_rel}", flush=True)
        _PENDING_EXE_MARKER.unlink(missing_ok=True)
        return
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(pending), str(dst))
        _PENDING_EXE_MARKER.unlink(missing_ok=True)
        print(f"[code] 已完成上次暂存的 EXE 替换: {target_rel}", flush=True)
    except OSError as exc:
        if _locked_file_error(exc):
            print(f"[code] [WARN] {dst.name} 仍被占用，继续保留 pending，下次启动再替换。", flush=True)
            return
        print(f"[code] [WARN] pending exe 替换失败: {exc}", flush=True)


def _apply_exe_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.updating.{os.getpid()}")
    try:
        if tmp.exists():
            tmp.unlink()
        shutil.copy2(src, tmp)
        os.replace(str(tmp), str(dst))
        _remove_pending_exe_marker_for_target(dst)
    except OSError as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        if _locked_file_error(exc):
            _stage_pending_exe_replace(src, dst, exc)
            return
        raise


def _norm_rel(name: str) -> str:
    return name.strip().strip("/").replace("\\", "/")


def _path_allowed(rel: str) -> bool:
    r = _norm_rel(rel)
    if not r or ".." in r.split("/"):
        return False
    rl = r.lower()
    if rl in BLOCKED_EXACT:
        return False
    if rl == "python" or rl.startswith("python/"):
        return False
    if rl == "deps" or rl.startswith("deps/"):
        return False
    if rl == "browser_chromium" or rl.startswith("browser_chromium/"):
        return False
    if rl == ".git" or rl.startswith(".git/"):
        return False
    if rl.startswith("nodejs/"):
        if r in ALLOWED_NODEJS_EXACT:
            return True
        for pref in ALLOWED_NODEJS_TREE_PREFIXES:
            if r == pref or r.startswith(pref + "/"):
                return True
        return False
    for bad in BLOCKED_PREFIXES:
        if rl.startswith(bad.lower().replace("\\", "/")):
            return False
    return True


def _as_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _netstat_listening_pids(port: int) -> set[int]:
    if os.name != "nt":
        return set()
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            text=True,
            errors="ignore",
            creationflags=_creation_flags(),
            timeout=8,
        )
    except Exception as exc:
        print(f"[code] [WARN] netstat 检查端口 {port} 失败: {exc}", flush=True)
        return set()
    pids: set[int] = set()
    markers = (f":{int(port)} ", f":{int(port)}\t")
    for raw in out.splitlines():
        line = raw.strip()
        if "LISTENING" not in line.upper():
            continue
        if not any(marker in line for marker in markers):
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pids.add(int(parts[-1]))
        except Exception:
            pass
    return pids


def _process_command_line(pid: int) -> str:
    if os.name != "nt":
        return ""
    try:
        out = subprocess.check_output(
            ["wmic", "process", "where", f"ProcessId={int(pid)}", "get", "CommandLine", "/value"],
            text=True,
            errors="ignore",
            creationflags=_creation_flags(),
            timeout=5,
        )
        for raw in out.splitlines():
            line = raw.strip()
            if line.lower().startswith("commandline="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId = {int(pid)}\").CommandLine",
            ],
            text=True,
            errors="ignore",
            creationflags=_creation_flags(),
            timeout=6,
        )
        return out.strip()
    except Exception:
        return ""


def _process_belongs_to_this_client(pid: int) -> bool:
    cmd = _process_command_line(pid)
    if not cmd:
        return False
    normalized = os.path.normcase(cmd).replace("/", "\\")
    try:
        normalized_root = os.path.normcase(str(ROOT.resolve())).replace("/", "\\")
    except Exception:
        normalized_root = os.path.normcase(str(ROOT)).replace("/", "\\")
    if normalized_root and normalized_root in normalized:
        return True
    markers = (
        "lobster_online",
        "必火",
        "run_backend.bat",
        "run_mcp.bat",
        "backend\\run.py",
        "run_module('mcp'",
        'run_module("mcp"',
        "openclaw.mjs",
    )
    return any(marker.lower() in normalized for marker in markers)


def _taskkill_pid_tree(pid: int, label: str) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                cwd=str(ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_creation_flags(),
                timeout=10,
            )
            print(f"[code] 已停止旧 {label} 进程 PID={pid}", flush=True)
            return
        except Exception as exc:
            print(f"[code] [WARN] 停止旧 {label} 进程 PID={pid} 失败: {exc}", flush=True)
    try:
        os.kill(int(pid), 15)
    except Exception:
        pass


def _wait_ports_closed(ports: list[int], seconds: float = 8.0) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if all(not _netstat_listening_pids(port) for port in ports):
            return
        time.sleep(0.35)


def _client_service_ports() -> list[tuple[int, str]]:
    env = _load_dotenv_simple(ROOT / ".env")
    env.update({k: v for k, v in os.environ.items() if k in {"PORT", "MCP_PORT", "OPENCLAW_GATEWAY_URL"}})
    ports: list[tuple[int, str]] = []
    ports.append((_as_int(env.get("PORT"), 8000), "Backend"))
    ports.append((_as_int(env.get("MCP_PORT"), 8001), "MCP"))
    gateway_url = str(env.get("OPENCLAW_GATEWAY_URL") or "http://127.0.0.1:18789").strip()
    gateway_port = 18789
    try:
        parsed = urllib.parse.urlparse(gateway_url)
        if parsed.port:
            gateway_port = int(parsed.port)
    except Exception:
        pass
    ports.append((gateway_port, "OpenClaw Gateway"))

    seen: set[int] = set()
    deduped: list[tuple[int, str]] = []
    for port, label in ports:
        if port <= 0 or port in seen:
            continue
        seen.add(port)
        deduped.append((port, label))
    return deduped


def _stop_client_services_before_update() -> None:
    global _STOP_CLIENT_SERVICES_DONE
    if _STOP_CLIENT_SERVICES_DONE:
        return
    _STOP_CLIENT_SERVICES_DONE = True
    if str(os.environ.get("CLIENT_CODE_UPDATE_STOP_SERVICES") or "1").strip().lower() in {"0", "false", "no", "off"}:
        print("[code] 已按配置跳过更新前停止本地服务。", flush=True)
        return

    services = _client_service_ports()
    print(
        "[code-progress] stop_services_start "
        + " ".join(f"{label}={port}" for port, label in services),
        flush=True,
    )
    killed_ports: list[int] = []
    for port, label in services:
        pids = _netstat_listening_pids(port)
        if not pids:
            continue
        for pid in sorted(pids):
            if pid == os.getpid():
                continue
            if not _process_belongs_to_this_client(pid):
                print(f"[code] [WARN] 端口 {port} 被非本客户端进程占用，未停止 PID={pid}", flush=True)
                continue
            _taskkill_pid_tree(pid, label)
            killed_ports.append(port)
    if killed_ports:
        _wait_ports_closed(sorted(set(killed_ports)), 8.0)
    print("[code-progress] stop_services_done", flush=True)


def _valid_sha256(value: str) -> bool:
    s = (value or "").strip().lower()
    return len(s) == 64 and all(c in "0123456789abcdef" for c in s)


def _artifact_url(item: dict[str, Any]) -> str:
    return str(item.get("url") or item.get("bundle_url") or "").strip()


def _artifact_sha(item: dict[str, Any]) -> str:
    return str(item.get("sha256") or "").strip().lower()


def _artifact_paths(item: dict[str, Any], default_paths: tuple[str, ...] | list[str] = DEFAULT_PATHS) -> list[str]:
    paths = item.get("paths")
    if not isinstance(paths, list) or not paths:
        paths = list(default_paths)
    normalized = [_norm_rel(str(p)) for p in paths if _norm_rel(str(p))]
    version_paths = {"CLIENT_CODE_VERSION.json", "static/client_version.json"}
    head = [p for p in normalized if p not in version_paths]
    tail = [p for p in normalized if p in version_paths]
    return head + tail


def _validate_paths(paths: list[str]) -> bool:
    for rel in paths:
        if not _path_allowed(rel):
            print(f"[code] [ERR] 禁止通过热更新覆盖的路径: {rel}", flush=True)
            return False
    return True


def _zip_inner_root(extract_root: Path) -> Path:
    """zip 根下只有一层 lobster_online/ 等时，进入该子目录再取 paths。"""
    inner = extract_root
    if (inner / "backend").is_dir():
        return inner
    subdirs = [p for p in inner.iterdir() if p.is_dir()]
    if len(subdirs) == 1 and (subdirs[0] / "backend").is_dir():
        return subdirs[0]
    return inner


def _merge_openclaw_policies_from_bundle(bundle_openclaw: Path, target_openclaw: Path) -> None:
    """把包内聊天策略 Markdown 覆盖写入本机 workspace（本机无 workspace 时创建）。"""
    src_ws = bundle_openclaw / "workspace"
    dst_ws = target_openclaw / "workspace"
    for name in _OPENCLAW_POLICY_FILENAMES:
        sf = src_ws / name
        if not sf.is_file():
            continue
        dst_ws.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sf, dst_ws / name)


_OPENCLAW_PRESERVE_DIR_NAMES = {
    ".openclaw",
    "agents",
    "cron",
    "delivery-queue",
    "devices",
    "identity",
    "memory",
    "openclaw-weixin",
    "tasks",
    "user_memory",
}
_OPENCLAW_PRESERVE_FILE_NAMES = {
    ".env",
    ".channel_fallback.json",
    ".weixin_login_last.json",
    "update-check.json",
}
_OPENCLAW_GATEWAY_TOKEN_PLACEHOLDER = "LOBSTER_AUTO_TOKEN_PLACEHOLDER"


def _read_openclaw_gateway_token(config_path: Path) -> str:
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        token = str(data.get("gateway", {}).get("auth", {}).get("token") or "").strip()
        if token == _OPENCLAW_GATEWAY_TOKEN_PLACEHOLDER:
            return ""
        return token
    except Exception:
        return ""


def _write_openclaw_gateway_token(config_path: Path, token: str) -> None:
    token = (token or "").strip()
    if not token or token == _OPENCLAW_GATEWAY_TOKEN_PLACEHOLDER or not config_path.is_file():
        return
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        data.setdefault("gateway", {}).setdefault("auth", {})["token"] = token
        config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"[code] [WARN] 保留 OpenClaw Gateway token 失败: {exc}", flush=True)


def _apply_openclaw_with_preserve(src: Path, dst: Path) -> None:
    """Replace OpenClaw code while preserving local runtime state and user memory."""
    preserved: list[tuple[str, Path]] = []
    tmp_root = Path(tempfile.mkdtemp(prefix="lobster_oc_preserve_"))
    try:
        if dst.is_dir():
            for child in dst.iterdir():
                if child.is_dir() and (child.name == "workspace" or child.name.startswith("workspace-")):
                    holder = tmp_root / child.name
                    shutil.move(str(child), str(holder))
                    preserved.append((child.name, holder))
                elif child.is_dir() and child.name in _OPENCLAW_PRESERVE_DIR_NAMES:
                    holder = tmp_root / child.name
                    shutil.move(str(child), str(holder))
                    preserved.append((child.name, holder))
            for filename in _OPENCLAW_PRESERVE_FILE_NAMES:
                state_file = dst / filename
                if state_file.is_file():
                    holder = tmp_root / filename
                    shutil.copy2(state_file, holder)
                    preserved.append((filename, holder))

        if dst.exists():
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        shutil.copytree(src, dst)

        for filename in _OPENCLAW_PRESERVE_FILE_NAMES:
            bundled_state_file = dst / filename
            if bundled_state_file.exists():
                if bundled_state_file.is_dir():
                    shutil.rmtree(bundled_state_file)
                else:
                    bundled_state_file.unlink()

        for name, holder in preserved:
            target = dst / name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            if holder.is_dir():
                shutil.move(str(holder), str(target))
            elif holder.is_file():
                shutil.copy2(holder, target)

        _merge_openclaw_policies_from_bundle(src, dst)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _apply_path(src: Path, dst: Path) -> None:
    rel = _norm_rel(str(dst.relative_to(ROOT)))
    if rel == "openclaw" and src.is_dir():
        _apply_openclaw_with_preserve(src, dst)
        return
    if rel in _DESKTOP_EXE_NAMES and src.is_file():
        _apply_exe_file(src, dst)
        return
    preserved: list[tuple[str, Path]] = []
    tmp_root: Path | None = None
    preserve_rels: tuple[str, ...] = ()
    if src.is_dir() and rel == "static":
        preserve_rels = _PRESERVED_STATIC_REL_PATHS
    elif src.is_dir() and rel == "desktop":
        preserve_rels = _PRESERVED_DESKTOP_REL_PATHS
    if preserve_rels:
        tmp_root = Path(tempfile.mkdtemp(prefix="lobster_path_preserve_"))
        for child_rel in preserve_rels:
            child = ROOT / child_rel
            if not child.exists():
                continue
            holder = tmp_root / child_rel
            holder.parent.mkdir(parents=True, exist_ok=True)
            if child.is_dir():
                shutil.copytree(child, holder)
            else:
                shutil.copy2(child, holder)
            preserved.append((child_rel, holder))
    if dst.exists():
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_file():
        shutil.copy2(src, dst)
    else:
        shutil.copytree(src, dst)
    try:
        for child_rel, holder in preserved:
            target = ROOT / child_rel
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            if holder.is_dir():
                shutil.copytree(holder, target)
            else:
                shutil.copy2(holder, target)
    finally:
        if tmp_root is not None:
            shutil.rmtree(tmp_root, ignore_errors=True)


def _extract_zip_to(path: Path, target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(path, "r") as zf:
            zf.extractall(target)
    except zipfile.BadZipFile as e:
        raise RuntimeError(f"zip 损坏: {e}") from e
    return _zip_inner_root(target)


def _download_verified(url: str, expect_sha: str, dest: Path, *, label: str) -> bool:
    if not url.lower().startswith(("https://", "http://")):
        print(f"[code] [ERR] {label}.url 格式无效。", flush=True)
        return False
    if not _valid_sha256(expect_sha):
        print(f"[code] [ERR] {label}.sha256 无效。", flush=True)
        return False
    print(f"[code-progress] download_start label={label} url={url}", flush=True)
    try:
        _download_file(url, dest)
    except Exception as e:
        print(f"[code] [WARN] {label} 下载失败: {e}", flush=True)
        return False
    print(f"[code-progress] download_done label={label} bytes={dest.stat().st_size if dest.exists() else 0}", flush=True)
    print(f"[code-progress] verify_start label={label}", flush=True)
    got = _sha256_file(dest)
    if got.lower() != expect_sha:
        print(
            f"[code] [ERR] {label} SHA256 不匹配（期望 {expect_sha[:16]}… 实际 {got[:16]}…）。",
            flush=True,
        )
        return False
    print(f"[code-progress] verify_done label={label}", flush=True)
    return True


def _apply_bundle_zip(zpath: Path, paths: list[str], tdir: Path) -> list[str]:
    print(f"[code-progress] extract_start file={zpath.name}", flush=True)
    extract_root = tdir / ("extracted_" + hashlib.sha1(str(zpath).encode("utf-8")).hexdigest()[:8])
    inner = _extract_zip_to(zpath, extract_root)
    print(f"[code-progress] extract_done file={zpath.name}", flush=True)
    applied: list[str] = []
    total = max(1, len(paths))
    for idx, rel in enumerate(paths, 1):
        src = inner / rel.replace("/", os.sep)
        if not src.exists():
            print(f"[code] [WARN] 包内无路径 {rel}，跳过。", flush=True)
            continue
        print(f"[code-progress] apply_path {idx}/{total} {rel}", flush=True)
        dst = ROOT / rel.replace("/", os.sep)
        _apply_path(src, dst)
        applied.append(rel)
    print(f"[code-progress] apply_done count={len(applied)}", flush=True)
    return applied


def _patch_matches_local(patch: dict[str, Any], local_build: int, local_ver: str) -> bool:
    from_build = patch.get("from_build")
    if from_build is not None and _as_int(from_build, -1) != int(local_build):
        return False
    from_version = str(patch.get("from_version") or "").strip()
    if from_version and from_version != local_ver:
        return False
    return True


def _select_patch(manifest: dict[str, Any], local_build: int, local_ver: str, remote_build: int) -> dict[str, Any] | None:
    patches = manifest.get("patches")
    if not isinstance(patches, list):
        return None
    candidates: list[dict[str, Any]] = []
    for item in patches:
        if not isinstance(item, dict):
            continue
        to_build = _as_int(item.get("to_build", item.get("build", remote_build)), 0)
        if to_build != remote_build:
            continue
        if not _patch_matches_local(item, local_build, local_ver):
            continue
        candidates.append(item)
    if not candidates:
        return None
    candidates.sort(key=lambda x: _as_int(x.get("priority"), 0), reverse=True)
    return candidates[0]


def _resource_state_path() -> Path:
    return _RESOURCE_STATE_DIR / "installed.json"


def _load_resource_state() -> dict[str, Any]:
    path = _resource_state_path()
    if not path.is_file():
        return {"resources": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("resources", {})
            return data
    except Exception:
        pass
    return {"resources": {}}


def _save_resource_state(state: dict[str, Any]) -> None:
    _RESOURCE_STATE_DIR.mkdir(parents=True, exist_ok=True)
    _resource_state_path().write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _resource_installed(item: dict[str, Any], state: dict[str, Any]) -> bool:
    rid = str(item.get("id") or item.get("name") or "").strip()
    expect_sha = _artifact_sha(item)
    installed = (state.get("resources") or {}).get(rid)
    if not rid or not isinstance(installed, dict):
        return False
    if expect_sha and str(installed.get("sha256") or "").lower() != expect_sha:
        return False
    marker = item.get("marker")
    if marker:
        return (ROOT / _norm_rel(str(marker))).exists()
    for rel in _artifact_paths(item, []):
        if not (ROOT / rel.replace("/", os.sep)).exists():
            return False
    return True


def _apply_resources(resources: Any, tdir: Path) -> None:
    if not isinstance(resources, list) or not resources:
        return
    state = _load_resource_state()
    for item in resources:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("id") or item.get("name") or "").strip() or "resource"
        mode = str(item.get("mode") or "once").strip().lower()
        if mode == "once" and _resource_installed(item, state):
            print(f"[code] 资源包已存在，跳过: {rid}", flush=True)
            continue
        url = _artifact_url(item)
        sha = _artifact_sha(item)
        zpath = tdir / f"resource_{rid}.zip"
        print(f"[code] 下载资源包: {rid}", flush=True)
        if not _download_verified(url, sha, zpath, label=f"resource {rid}"):
            continue
        paths = _artifact_paths(item, [])
        if not paths:
            print(f"[code] [WARN] resource {rid} 未声明 paths，跳过应用。", flush=True)
            continue
        if not _validate_paths(paths):
            continue
        try:
            applied = _apply_bundle_zip(zpath, paths, tdir)
        except Exception as e:
            print(f"[code] [WARN] resource {rid} 应用失败: {e}", flush=True)
            continue
        state.setdefault("resources", {})[rid] = {
            "sha256": sha,
            "applied_at": datetime.datetime.utcnow().isoformat() + "Z",
            "paths": applied,
        }
        _save_resource_state(state)
        print(f"[code] 资源包已应用: {rid} ({len(applied)} paths)", flush=True)


def _apply_update_artifact(artifact: dict[str, Any], tdir: Path, *, label: str) -> list[str] | None:
    url = _artifact_url(artifact)
    sha = _artifact_sha(artifact)
    paths = _artifact_paths(artifact)
    if not _validate_paths(paths):
        return None
    zpath = tdir / f"{label}.zip"
    if not _download_verified(url, sha, zpath, label=label):
        return None
    _stop_client_services_before_update()
    try:
        applied = _apply_bundle_zip(zpath, paths, tdir)
    except Exception as e:
        print(f"[code] [ERR] {label} 应用失败: {e}", flush=True)
        return None
    if not applied:
        print(f"[code] [ERR] {label} 包内未找到任何可覆盖路径。", flush=True)
        return None
    return applied


def main() -> int:
    _apply_pending_exe_replace()
    env = _load_dotenv_simple(ROOT / ".env")
    env.update({k: v for k, v in os.environ.items() if k.startswith("CLIENT_CODE_")})

    manifest_url = (env.get("CLIENT_CODE_MANIFEST_URL") or "").strip()
    if not manifest_url:
        return 0

    if not manifest_url.lower().startswith(("https://", "http://")):
        print("[code] [ERR] CLIENT_CODE_MANIFEST_URL 格式无效。", flush=True)
        return 0

    local = _local_build()
    local_ver = _local_semver()
    try:
        manifest = _fetch_json(manifest_url)
    except urllib.error.URLError as e:
        print(f"[code] [WARN] 无法拉取 manifest，使用本地代码: {e}", flush=True)
        return 0
    except Exception as e:
        print(f"[code] [WARN] manifest 解析失败，跳过更新: {e}", flush=True)
        return 0

    try:
        remote_build = int(manifest.get("build", 0))
    except (TypeError, ValueError):
        print("[code] [WARN] manifest 缺少合法整数 build，跳过更新。", flush=True)
        return 0

    remote_ver = str(manifest.get("version") or "").strip()
    need_update = remote_build > local
    if not need_update and remote_build == local and remote_ver and _semver_is_newer(remote_ver, local_ver):
        need_update = True
    if not need_update:
        print(f"[code] 本地代码包已是最新 (build={local}, version={local_ver})。", flush=True)
        with tempfile.TemporaryDirectory() as td:
            _apply_resources(manifest.get("resources"), Path(td))
        return 0

    if remote_build > local:
        print(f"[code-progress] found_update remote_build={remote_build} local_build={local}", flush=True)
        print(f"[code] 发现新版本 build={remote_build}（本地 build={local}），正在下载…", flush=True)
    else:
        print(f"[code-progress] found_update remote_version={remote_ver} local_version={local_ver}", flush=True)
        print(
            f"[code] 发现新版本 version={remote_ver}（本地 {local_ver}，build 均为 {local}），正在下载…",
            flush=True,
        )

    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        applied: list[str] | None = None
        patch = _select_patch(manifest, local, local_ver, remote_build)
        if patch is not None:
            p_from = patch.get("from_build", patch.get("from_version", "?"))
            print(f"[code] 优先尝试增量补丁: {p_from} -> {remote_build}", flush=True)
            applied = _apply_update_artifact(patch, tdir, label="patch")
            if applied is None:
                print("[code] [WARN] 增量补丁失败，回退 full OTA。", flush=True)

        if applied is None:
            full_artifact = {
                "url": manifest.get("bundle_url"),
                "sha256": manifest.get("sha256"),
                "paths": manifest.get("paths") or list(DEFAULT_PATHS),
            }
            applied = _apply_update_artifact(full_artifact, tdir, label="bundle")

        if applied is None:
            print("[code] [ERR] 未成功应用任何更新，未写入版本号。", flush=True)
            return 0

        _apply_resources(manifest.get("resources"), tdir)

    mver = manifest.get("version")
    mver_s = str(mver).strip() if mver is not None else ""
    _save_local_build(remote_build, mver_s or None)
    print(f"[code] 已覆盖更新 build={remote_build}，路径: {', '.join(applied)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
