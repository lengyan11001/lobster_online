from __future__ import annotations

"""数字人koubo：本地运行环境探测 + 依赖安装任务队列 + 分段报价。

第一阶段只做两件事：
1. 依赖（ffmpeg / Node / Python / numpy+Pillow / whisper-cli / whisper 模型）状态与按需安装；
2. 口播稿 → 分段与 credits 报价（移植 skills/digital_human_koubo/scripts/build_tasks.py 的算法）。
不触发生成、不扣算力。
"""

import math
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

ROOT = Path(__file__).resolve().parents[3]
DEPS = ROOT / "deps"
SKILL_DIR = ROOT / "skills" / "digital_human_koubo"
FFMPEG_DIR = DEPS / "ffmpeg"
WHISPER_DIR = DEPS / "whisper"
WHISPER_MODELS = WHISPER_DIR / "models"
WHISPER_MODEL_PATH = WHISPER_MODELS / "ggml-medium-q5_0.bin"
WHISPER_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium-q5_0.bin"
NODE_EXE = ROOT / "nodejs" / "node.exe"
PY_EXE = ROOT / "python" / "python.exe"
UV_EXE = DEPS / "uv" / "uv.exe"

CPS_ZH = 5.4
CREDITS_PER_SEC = {"720p": 17, "480p": 10}
MAX_SEG_SECONDS = 15

_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


def _run(cmd: List[str], timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _probe_ffmpeg() -> Dict[str, Any]:
    exe = FFMPEG_DIR / "ffmpeg.exe"
    probe = FFMPEG_DIR / "ffprobe.exe"
    if not exe.is_file():
        return {"status": "missing", "detail": "未找到内置 ffmpeg"}
    ver = ""
    try:
        ver = _first_line(_run([str(exe), "-version"]).stdout)
    except Exception:
        pass
    return {
        "status": "ok" if probe.is_file() else "missing",
        "detail": ver or "已内置",
        "version": (ver.split()[2] if len(ver.split()) > 2 else ""),
    }


def _probe_node() -> Dict[str, Any]:
    if not NODE_EXE.is_file():
        return {"status": "missing", "detail": "未找到内置 Node"}
    try:
        ver = _first_line(_run([str(NODE_EXE), "-v"]).stdout)
    except Exception:
        ver = ""
    return {"status": "ok", "detail": "已内置", "version": ver}


def _probe_python() -> Dict[str, Any]:
    if not PY_EXE.is_file():
        return {"status": "missing", "detail": "未找到内置 Python"}
    try:
        ver = _first_line(_run([str(PY_EXE), "-c", "import sys;print(sys.version.split()[0])"]).stdout)
    except Exception:
        ver = ""
    return {"status": "ok", "detail": "已内置", "version": ver}


def _probe_python_pkgs() -> Dict[str, Any]:
    if not PY_EXE.is_file():
        return {"status": "missing", "detail": "需要先有内置 Python"}
    try:
        r = _run([str(PY_EXE), "-c", "import numpy, PIL; print(numpy.__version__, PIL.__version__)"])
    except Exception as exc:
        return {"status": "missing", "detail": f"检测失败: {exc}"}
    if r.returncode == 0:
        return {"status": "ok", "detail": "numpy + Pillow", "version": r.stdout.strip()}
    return {"status": "missing", "detail": "numpy / Pillow 未安装"}


def _probe_whisper() -> Dict[str, Any]:
    exe = WHISPER_DIR / "whisper-cli.exe"
    if exe.is_file():
        return {"status": "ok", "detail": str(exe), "version": ""}
    found = list(WHISPER_DIR.glob("**/whisper-cli.exe")) if WHISPER_DIR.is_dir() else []
    if found:
        return {"status": "ok", "detail": str(found[0])}
    return {"status": "missing", "detail": "whisper-cli 未安装"}


def _probe_model() -> Dict[str, Any]:
    if WHISPER_MODEL_PATH.is_file():
        mb = WHISPER_MODEL_PATH.stat().st_size / 1048576
        if mb > 500:
            return {"status": "ok", "detail": f"{mb:.0f} MB", "version": "ggml-medium-q5_0"}
        return {"status": "missing", "detail": f"文件不完整（{mb:.0f} MB）"}
    return {"status": "missing", "detail": "约 514 MB，按需下载"}


def _component_list() -> List[Dict[str, Any]]:
    defs = [
        ("ffmpeg", "FFmpeg / ffprobe", "抽帧、拼接、响度归一", False, _probe_ffmpeg, "内置"),
        ("node", "Node.js", "Remotion 渲染", False, _probe_node, "内置"),
        ("python", "Python", "回听、对齐、声音统一", False, _probe_python, "内置"),
        ("python_pkgs", "numpy + Pillow", "声音频谱匹配、预览图", True, _probe_python_pkgs, "约 20 MB"),
        ("whisper", "whisper-cli", "逐段回听、逐字时间轴", True, _probe_whisper, "约 30 MB"),
        ("whisper_model", "whisper 模型", "ggml-medium-q5_0", True, _probe_model, "约 520 MB"),
    ]
    out: List[Dict[str, Any]] = []
    with _JOBS_LOCK:
        running = {j.get("component"): j for j in _JOBS.values() if j.get("status") == "running"}
    for cid, name, purpose, installable, fn, size in defs:
        try:
            info = fn()
        except Exception as exc:
            info = {"status": "error", "detail": str(exc)}
        job = running.get(cid)
        if job:
            info = {"status": "installing", "detail": job.get("message") or "安装中", "progress": job.get("progress", 0)}
        out.append({
            "id": cid,
            "name": name,
            "purpose": purpose,
            "size": size,
            "installable": installable,
            **info,
        })
    return out


@router.get("/api/skills/digital-human-koubo/status", summary="数字人koubo 依赖与技能状态")
def koubo_status():
    comps = _component_list()
    ready = all(c["status"] == "ok" for c in comps)
    return {
        "package_id": "digital_human_koubo_skill",
        "skill_dir": str(SKILL_DIR),
        "skill_exists": (SKILL_DIR / "SKILL.md").is_file(),
        "components": comps,
        "ready": ready,
        "phase": 1,
        "note": "第一阶段只验证分段与报价；生成走现有 Renoise/Grok 链路，未接入。",
    }


def _new_job(component: str) -> Dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "component": component,
        "status": "running",
        "progress": 0,
        "message": "准备中",
        "error": "",
        "started_at": time.time(),
        "finished_at": None,
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = job
    return job


_MIRRORS = ("https://gh-proxy.com/", "https://ghproxy.net/", "https://ghfast.top/")


def _with_mirrors(url: str) -> List[str]:
    if "huggingface.co/" in url:
        return [url, url.replace("https://huggingface.co/", "https://hf-mirror.com/")]
    if "github.com/" in url:
        return [url] + [m + url for m in _MIRRORS]
    return [url]


def _download(url: str, dest: Path, job: Dict[str, Any], label: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    last_err = ""
    for candidate in _with_mirrors(url):
        for attempt in (1, 2):
            try:
                with httpx.stream("GET", candidate, follow_redirects=True, timeout=httpx.Timeout(30.0, read=None)) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length") or 0)
                    done = 0
                    with open(tmp, "wb") as fh:
                        for chunk in resp.iter_bytes(1024 * 256):
                            if not chunk:
                                continue
                            fh.write(chunk)
                            done += len(chunk)
                            if total:
                                job["progress"] = min(92, int(done * 92 / total))
                                job["message"] = f"{label} {done // 1048576} / {total // 1048576} MB"
                            else:
                                job["message"] = f"{label} {done // 1048576} MB"
                tmp.replace(dest)
                return
            except Exception as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except Exception:
                        pass
    raise RuntimeError(f"{label} 下载失败（已尝试直连与镜像）：{last_err[:300]}")


def _install_python_pkgs(job: Dict[str, Any]) -> None:
    job["message"] = "安装 numpy / Pillow"
    job["progress"] = 10
    if UV_EXE.is_file():
        cmd = [str(UV_EXE), "pip", "install", "--python", str(PY_EXE), "numpy", "pillow"]
    else:
        cmd = [str(PY_EXE), "-m", "pip", "install", "--disable-pip-version-check", "numpy", "pillow"]
    r = _run(cmd, timeout=900)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "pip install 失败")[-1200:])
    job["progress"] = 95
    job["message"] = "已安装"


_WHISPER_FALLBACK_URL = (
    "https://github.com/ggml-org/whisper.cpp/releases/download/v1.9.2/whisper-bin-x64.zip"
)


def _resolve_whisper_zip() -> str:
    """whisper.cpp 的 latest release 已不再附带 Windows 二进制，需回扫历史 release。"""
    headers = {"User-Agent": "lobster-client"}
    tagged: list[tuple[str, str]] = []
    anyrel: list[tuple[str, str]] = []
    for repo in ("ggml-org/whisper.cpp", "ggerganov/whisper.cpp"):
        try:
            with httpx.Client(timeout=25.0, headers=headers, follow_redirects=True) as client:
                r = client.get(f"https://api.github.com/repos/{repo}/releases", params={"per_page": 30})
                if r.status_code != 200:
                    continue
                for rel in (r.json() or []):
                    tag = str(rel.get("tag_name") or "")
                    for asset in (rel.get("assets") or []):
                        name = str(asset.get("name") or "")
                        url = str(asset.get("browser_download_url") or "")
                        if name == "whisper-bin-x64.zip" and url:
                            (tagged if tag.startswith("v") else anyrel).append((tag, url))
        except Exception:
            continue
    if tagged:
        return tagged[0][1]
    if anyrel:
        return anyrel[0][1]
    return _WHISPER_FALLBACK_URL


def _install_whisper(job: Dict[str, Any]) -> None:
    job["message"] = "解析 whisper.cpp 最新版"
    url = _resolve_whisper_zip()
    zip_path = WHISPER_DIR / "whisper-bin-x64.zip"
    _download(url, zip_path, job, "下载 whisper.cpp")
    job["message"] = "解压"
    job["progress"] = 93
    WHISPER_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(WHISPER_DIR)
    exe = list(WHISPER_DIR.glob("**/whisper-cli.exe"))
    if not exe:
        raise RuntimeError("压缩包内未找到 whisper-cli.exe")
    src_dir = exe[0].parent
    if src_dir != WHISPER_DIR:
        for item in src_dir.iterdir():
            target = WHISPER_DIR / item.name
            if target.exists():
                continue
            shutil.move(str(item), str(target))
    job["progress"] = 97
    job["message"] = "whisper-cli 就绪"


def _install_model(job: Dict[str, Any]) -> None:
    job["message"] = "下载 ggml-medium-q5_0（约 514 MB）"
    _download(WHISPER_MODEL_URL, WHISPER_MODEL_PATH, job, "下载模型")


_INSTALLERS = {
    "python_pkgs": _install_python_pkgs,
    "whisper": _install_whisper,
    "whisper_model": _install_model,
}


def _run_job(job: Dict[str, Any]) -> None:
    try:
        fn = _INSTALLERS.get(job["component"])
        if not fn:
            raise RuntimeError(f"该组件不支持自动安装: {job['component']}")
        fn(job)
        job["status"] = "done"
        job["progress"] = 100
        job["message"] = "安装完成"
    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)[:1500]
        job["message"] = "安装失败"
    finally:
        job["finished_at"] = time.time()


class InstallBody(BaseModel):
    component: str = Field(..., min_length=1)


@router.post("/api/skills/digital-human-koubo/deps/install", summary="安装数字人koubo 依赖")
def install_dep(body: InstallBody):
    comp = (body.component or "").strip()
    if comp == "all":
        missing = [c["id"] for c in _component_list() if c["installable"] and c["status"] != "ok"]
        if not missing:
            return {"ok": True, "jobs": [], "message": "依赖已齐"}
        jobs = []
        for cid in missing:
            job = _new_job(cid)
            threading.Thread(target=_run_job, args=(job,), daemon=True).start()
            jobs.append(job["id"])
            break  # 一次一个，前端依次触发，避免同时抢带宽
        return {"ok": True, "jobs": jobs, "component": missing[0]}
    if comp not in _INSTALLERS:
        raise HTTPException(status_code=400, detail=f"不支持自动安装: {comp}")
    job = _new_job(comp)
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return {"ok": True, "job_id": job["id"], "component": comp}


@router.get("/api/skills/digital-human-koubo/deps/jobs/{job_id}", summary="依赖安装进度")
def dep_job(job_id: str):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job


class QuoteBody(BaseModel):
    text: str = Field(..., min_length=1)
    shot: str = "full"
    host: str = "tank"


def _split_script(text: str) -> List[str]:
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return []
    parts = re.split(r"(?<=[。！？；!?;])", raw)
    out: List[str] = []
    buf = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(buf) + len(part) <= 46:
            buf += part
            continue
        if buf:
            out.append(buf)
            buf = ""
        while len(part) > 46:
            out.append(part[:46])
            part = part[46:]
        buf = part
    if buf:
        out.append(buf)
    return out


@router.post("/api/skills/digital-human-koubo/quote", summary="口播稿分段与 credits 报价")
def koubo_quote(body: QuoteBody):
    chunks = _split_script(body.text)
    if not chunks:
        raise HTTPException(status_code=400, detail="文案为空")
    shot = body.shot if body.shot in {"full", "pip", "vlog", "depth"} else "full"
    res = "480p" if shot == "pip" else "720p"
    segs = []
    total = 0
    for i, chunk in enumerate(chunks, 1):
        chars = len(re.sub(r"[\s，。、：；！？,.:;!?“”]", "", chunk))
        secs = max(4, min(MAX_SEG_SECONDS, math.ceil(chars / CPS_ZH + 1.0)))
        credits = secs * CREDITS_PER_SEC[res]
        total += credits
        segs.append({
            "id": f"s{i}",
            "text": chunk,
            "shot": shot,
            "resolution": res,
            "seconds": secs,
            "credits": credits,
        })
    return {
        "segments": segs,
        "total_seconds": sum(s["seconds"] for s in segs),
        "total_credits": total,
        "chars_per_second": CPS_ZH,
        "note": "按 build_tasks.py 同一算法估算；实际以 Renoise 实时报价为准。",
    }
