from __future__ import annotations

import base64
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from enum import Enum
from functools import partial
from pathlib import Path
from typing import Any, Dict, Optional

import execjs
import execjs._external_runtime as execjs_external_runtime
import execjs._runtimes as execjs_runtimes
import execjs._runner_sources as execjs_runner_sources
import requests

_raw_popen = subprocess.Popen


def _utf8_popen(*args, **kwargs):
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "ignore")
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
        if creationflags:
            kwargs.setdefault("creationflags", creationflags)
        if "startupinfo" not in kwargs:
            startupinfo_factory = getattr(subprocess, "STARTUPINFO", None)
            startf_use_showwindow = getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
            sw_hide = getattr(subprocess, "SW_HIDE", 0)
            if startupinfo_factory and startf_use_showwindow:
                startupinfo = startupinfo_factory()
                startupinfo.dwFlags |= startf_use_showwindow
                startupinfo.wShowWindow = sw_hide
                kwargs["startupinfo"] = startupinfo
    return _raw_popen(*args, **kwargs)


execjs_external_runtime.Popen = _utf8_popen


def resolve_protocol_root() -> Path:
    candidates = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS).resolve() / "douyin_protocol")
    candidates.extend(
        [
            Path(__file__).resolve().parent / "douyin_protocol",
            Path.cwd() / "douyin_protocol",
        ]
    )
    for candidate in candidates:
        if (candidate / "static" / "dy_ab.js").exists():
            return candidate
    return candidates[0]


PROTOCOL_ROOT = resolve_protocol_root()
STATIC_DIR = PROTOCOL_ROOT / "static"
DOUYIN_PROTOCOL_DEPS_ERROR = "抖音协议模式依赖未安装，请重新安装更新包"


def _ensure_protocol_node_deps() -> None:
    jsrsasign_pkg = PROTOCOL_ROOT / "node_modules" / "jsrsasign" / "package.json"
    if not jsrsasign_pkg.is_file():
        raise RuntimeError(DOUYIN_PROTOCOL_DEPS_ERROR)


def _iter_node_candidates():
    install_root = Path(__file__).resolve().parents[2]
    candidates = [
        PROTOCOL_ROOT / "node" / "node.exe",
        install_root / "nodejs" / "node.exe",
    ]
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "nodejs" / "node.exe")

    for candidate in candidates:
        if candidate.exists():
            yield candidate

    system_node = shutil.which("node")
    if system_node:
        yield Path(system_node)


def _force_execjs_node_runtime() -> execjs.ExternalRuntime:
    checked = []
    for node_path in _iter_node_candidates():
        checked.append(str(node_path))
        node_dir = str(node_path.parent)
        if node_dir:
            path_parts = os.environ.get("PATH", "").split(os.pathsep)
            if node_dir not in path_parts:
                os.environ["PATH"] = node_dir + os.pathsep + os.environ.get("PATH", "")

        runtime = execjs_external_runtime.ExternalRuntime(
            name="Bundled Node.js (Douyin Protocol)",
            command=["node"],
            encoding="UTF-8",
            runner_source=execjs_runner_sources.Node,
        )
        if runtime.is_available():
            execjs_runtimes._runtimes = [
                ("BundledNode", runtime),
                *[(name, item) for name, item in execjs_runtimes._runtimes if name != "BundledNode"],
            ]
            return runtime

    if not checked:
        raise FileNotFoundError(
            "Douyin protocol Node runtime not found: "
            "please provide backend/douyin_origin/douyin_protocol/node/node.exe, "
            "nodejs/node.exe, or install Node.js on PATH."
        )
    raise RuntimeError(f"Douyin Node runtime is unavailable. Checked: {', '.join(checked)}")


EXECJS_RUNTIME = _force_execjs_node_runtime()


def _compile_static_js(filename: str):
    script_path = STATIC_DIR / filename
    if not script_path.exists():
        raise FileNotFoundError(f"Douyin protocol script not found: {script_path}")
    _ensure_protocol_node_deps()
    node_modules = PROTOCOL_ROOT / "node_modules"
    cwd = str(node_modules if node_modules.is_dir() else PROTOCOL_ROOT)
    try:
        return EXECJS_RUNTIME.compile(script_path.read_text(encoding="utf-8"), cwd=cwd)
    except Exception as exc:
        if "jsrsasign" in str(exc):
            raise RuntimeError(DOUYIN_PROTOCOL_DEPS_ERROR) from exc
        raise


_login_js = _compile_static_js("login.js")
_dy_js = _compile_static_js("dy_ab.js")
_sign_js = _compile_static_js("dy_live_sign.js")


def generate_req_sign(value: Any, private_key: str) -> str:
    return _dy_js.call("get_req_sign", value, private_key)


def generate_a_bogus(query: str, data: str = "") -> str:
    return _dy_js.call("get_ab", query, data)


def generate_msToken(randomlength: int = 107) -> str:
    random_str = ""
    base_str = "ABCDEFGHIGKLMNOPQRSTUVWXYZabcdefghigklmnopqrstuvwxyz0123456789="
    length = len(base_str) - 1
    for _ in range(randomlength):
        random_str += base_str[random.randint(0, length)]
    return random_str


def generate_fake_webid(random_length: int = 19) -> str:
    random_str = ""
    base_str = "0123456789"
    length = len(base_str) - 1
    for _ in range(random_length):
        random_str += base_str[random.randint(0, length)]
    return random_str


def generate_webid(auth: Any = None, url: str = "") -> str:
    if url == "":
        url = "https://www.douyin.com/discover?modal_id=7376449060384935209"
    try:
        headers = HeaderBuilder.build(HeaderType.DOC)
        headers.set_header("cookie", auth.cookie_str if auth else "")
        headers.set_header("upgrade-insecure-requests", "1")
        response = requests.get(url, headers=headers.get(), verify=False, timeout=20)
        return re.findall(r'\\"user_unique_id\\":\\"(.*?)\\"', response.text)[0]
    except Exception:
        return generate_fake_webid()


def generate_ree_key(private_key: str) -> str:
    return _dy_js.call("get_ree_key", private_key)


def generate_bd_ticket_client_data(api: str, ticket: str, ts_sign: str, private_key: str) -> str:
    timestamp = int(time.time())
    res_sign = f"ticket={ticket}&path={api}&timestamp={timestamp}"
    payload = {
        "ts_sign": ts_sign,
        "req_content": "ticket,path,timestamp",
        "req_sign": generate_req_sign(res_sign, private_key),
        "timestamp": timestamp,
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("utf-8")


def generate_csrf_token(cookie_str: str) -> tuple[Optional[str], Optional[str]]:
    headers = {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "cache-control": "no-cache",
        "cookie": str(cookie_str or ""),
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referer": "https://www.douyin.com/?recommend=1",
        "sec-ch-ua": '"Microsoft Edge";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": HeaderBuilder.ua,
        "x-secsdk-csrf-request": "1",
        "x-secsdk-csrf-version": "1.2.22",
    }
    try:
        response = requests.head(
            "https://www.douyin.com/service/2/abtest_config/",
            headers=headers,
            verify=False,
            timeout=20,
        )
        token_header = str(response.headers.get("X-Ware-Csrf-Token", "") or "")
        parts = token_header.split(",")
        return (
            parts[1] if len(parts) > 1 else None,
            parts[4] if len(parts) > 4 else None,
        )
    except Exception:
        return None, None


def splice_url(params: Optional[Dict[str, Any]]) -> str:
    if not params:
        return ""
    parts = []
    for key, value in params.items():
        if value is None:
            value = ""
        parts.append(f"{key}={urllib.parse.quote(str(value))}")
    return "&".join(parts)


class HeaderType(Enum):
    DOC = "DOC"
    POST = "POST"
    FORM = "FORM"
    GET = "GET"
    PROTOBUF = "PROTOBUF"


class Header:
    def __init__(self):
        self.headers: Dict[str, str] = {}

    def with_bd(self, api: str, auth: Any):
        self.set_header(
            "bd-ticket-guard-client-data",
            generate_bd_ticket_client_data(api, auth.ticket, auth.ts_sign, auth.private_key),
        )
        self.set_header("bd-ticket-guard-iteration-version", "1")
        self.set_header("bd-ticket-guard-ree-public-key", generate_ree_key(auth.private_key))
        self.set_header("bd-ticket-guard-version", "2")
        self.set_header("bd-ticket-guard-web-version", "1")
        return self

    def with_csrf(self, cookie_str: str):
        token, _ = generate_csrf_token(cookie_str)
        if token:
            self.set_header("x-secsdk-csrf-token", token)
        return self

    def set_header(self, key: str, value: str):
        self.headers[key] = value
        return self

    def set_referer(self, url: str):
        self.set_header("referer", url)
        return self

    def remove_header(self, key: str):
        if key in self.headers:
            del self.headers[key]
        return self

    def get(self) -> Dict[str, str]:
        return self.headers

    def __call__(self) -> Dict[str, str]:
        return self.headers


class HeaderBuilder:
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0"

    @staticmethod
    def build(header_type: HeaderType) -> Header:
        header = Header()
        header.set_header("user-agent", HeaderBuilder.ua)
        header.set_header("cache-control", "no-cache")
        header.set_header("pragma", "no-cache")
        header.set_header("sec-ch-ua", '"Microsoft Edge";v="125", "Chromium";v="125", "Not.A/Brand";v="24"')
        header.set_header("sec-ch-ua-mobile", "?0")
        header.set_header("sec-ch-ua-platform", '"Windows"')
        header.set_header("sec-fetch-dest", "empty")
        header.set_header("sec-fetch-mode", "cors")
        header.set_header("sec-fetch-site", "same-origin")
        header.set_header("priority", "u=1, i")
        header.set_header("accept-language", "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6")
        if header_type == HeaderType.POST:
            header.set_header("accept", "*/*")
            header.set_header("content-type", "application/json; charset=UTF-8")
        elif header_type == HeaderType.FORM:
            header.set_header("accept", "application/json, text/plain, */*")
            header.set_header("content-type", "application/x-www-form-urlencoded; charset=UTF-8")
        elif header_type == HeaderType.PROTOBUF:
            header.set_header("accept", "application/x-protobuf")
            header.set_header("content-type", "application/x-protobuf")
        elif header_type == HeaderType.GET:
            header.set_header("accept", "application/json, text/plain, */*")
        elif header_type == HeaderType.DOC:
            header = Header()
            header.headers.update(
                {
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
                    "cache-control": "no-cache",
                    "cookie": "",
                    "pragma": "no-cache",
                    "priority": "u=0, i",
                    "sec-ch-ua": '"Microsoft Edge";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Windows"',
                    "sec-fetch-dest": "document",
                    "sec-fetch-mode": "navigate",
                    "sec-fetch-site": "none",
                    "sec-fetch-user": "?1",
                    "upgrade-insecure-requests": "1",
                    "user-agent": HeaderBuilder.ua,
                }
            )
        return header
