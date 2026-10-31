from __future__ import annotations

"""数字人koubo：本地运行环境探测 + 依赖安装任务队列 + 分段报价。

第一阶段只做两件事：
1. 依赖（ffmpeg / Node / Python / numpy+Pillow / whisper-cli / whisper 模型）状态与按需安装；
2. 口播稿 → 分段与 credits 报价（移植 skills/digital_human_koubo/scripts/build_tasks.py 的算法）。
不触发生成、不扣算力。
"""

import io
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

router = APIRouter()

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data" / "koubo"
UPLOAD_DIR = DATA_DIR / "uploads"
PROJECTS_PATH = DATA_DIR / "projects.json"
DEPS = ROOT / "deps"
SKILL_DIR = ROOT / "skills" / "digital_human_koubo"
FFMPEG_DIR = DEPS / "ffmpeg"
WHISPER_DIR = DEPS / "whisper"
WHISPER_MODELS = WHISPER_DIR / "models"
WHISPER_MODEL_PATH = WHISPER_MODELS / "ggml-medium-q5_0.bin"
WHISPER_MODEL_URL = (
    "https://lobster-online-assets-2103871705.tos-cn-guangzhou.volces.com"
    "/assets/client-code/deps/whisper/ggml-medium-q5_0.bin"
)
WHISPER_MODEL_FALLBACK = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium-q5_0.bin"
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
    try:
        _download(WHISPER_MODEL_URL, WHISPER_MODEL_PATH, job, "下载模型（TOS）")
    except Exception as exc:
        job["message"] = "TOS 下载失败，回退 HuggingFace"
        job["progress"] = 0
        try:
            _download(WHISPER_MODEL_FALLBACK, WHISPER_MODEL_PATH, job, "下载模型（HF）")
        except Exception:
            raise exc


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


# ─────────────────────────────────────────────────────────────
# 项目 / 素材（第一阶段：本地落盘 + 任务包导出，不触发生成）
# ─────────────────────────────────────────────────────────────

_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
_MAX_UPLOAD_BYTES = 15 * 1024 * 1024
_HOSTS = {"tank": "白背心数字人", "me": "用户本人数字人"}


def _load_projects() -> List[Dict[str, Any]]:
    if not PROJECTS_PATH.is_file():
        return []
    try:
        data = json.loads(PROJECTS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_projects(items: List[Dict[str, Any]]) -> None:
    PROJECTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROJECTS_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _analyze_image(path: Path) -> Dict[str, Any]:
    """分辨率 / 清晰度 / 亮度三项体检。没有做人脸检测（客户端未带 cv2），只给可解释的提示。"""
    info: Dict[str, Any] = {"width": 0, "height": 0, "sharpness": 0.0, "brightness": 0.0, "warnings": []}
    try:
        from PIL import Image
        import numpy as np

        with Image.open(path) as img:
            img.load()
            w, h = img.size
            info["width"], info["height"] = int(w), int(h)
            gray = img.convert("L")
            scale = 320.0 / max(1, max(w, h))
            if scale < 1:
                gray = gray.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            arr = np.asarray(gray, dtype=np.float32)
            info["brightness"] = round(float(arr.mean()), 1)
            if arr.shape[0] > 2 and arr.shape[1] > 2:
                lap = np.abs(np.diff(arr, axis=0)[:, :-1] + np.diff(arr, axis=1)[:-1, :])
                info["sharpness"] = round(float(lap.var()), 1)
    except Exception as exc:
        info["warnings"].append("图片解析失败：%s" % exc)
        return info

    if min(info["width"], info["height"]) < 512:
        info["warnings"].append("短边小于 512px，特写会糊")
    if info["brightness"] < 60:
        info["warnings"].append("整体偏暗，生成的脸容易发灰")
    if info["brightness"] > 215:
        info["warnings"].append("整体过曝，脸部细节会丢")
    if info["sharpness"] < 25:
        info["warnings"].append("清晰度偏低，可能有运动模糊")
    return info


@router.post("/api/skills/digital-human-koubo/uploads", summary="上传数字人形象照")
async def koubo_upload(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="只支持 jpg / png / webp")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="文件超过 15MB")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex[:16] + ext
    path = UPLOAD_DIR / name
    path.write_bytes(raw)
    info = _analyze_image(path)
    return {
        "id": name,
        "url": "/api/skills/digital-human-koubo/uploads/" + name,
        "size": len(raw),
        "checks": info,
        "tips": [
            "正脸、平视、光线均匀",
            "嘴巴闭合，头发不挡脸",
            "背景尽量干净，人物占画面 50%-70%",
            "只用本人或已获授权的照片",
        ],
    }


@router.get("/api/skills/digital-human-koubo/uploads/{name}", summary="读取已上传的形象照")
def koubo_upload_get(name: str):
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="非法文件名")
    path = UPLOAD_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(str(path))


class ProjectBody(BaseModel):
    name: str = ""
    script: str = Field(..., min_length=1)
    shot: str = "full"
    host: str = "tank"
    photo_id: Optional[str] = None


def _persona() -> Dict[str, Any]:
    try:
        return json.loads((SKILL_DIR / "persona.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_segments(text: str, shot: str) -> List[Dict[str, Any]]:
    chunks = _split_script(text)
    res = "480p" if shot == "pip" else "720p"
    out: List[Dict[str, Any]] = []
    for i, chunk in enumerate(chunks, 1):
        chars = len(re.sub(r"[\s，。、：；！？,.:;!?“”]", "", chunk))
        secs = max(4, min(MAX_SEG_SECONDS, math.ceil(chars / CPS_ZH + 1.0)))
        out.append({
            "id": "s%d" % i,
            "text": chunk,
            "shot": shot,
            "resolution": res,
            "seconds": secs,
            "credits": secs * CREDITS_PER_SEC[res],
        })
    return out


@router.post("/api/skills/digital-human-koubo/projects", summary="保存数字人koubo 项目")
def koubo_project_create(body: ProjectBody):
    shot = body.shot if body.shot in {"full", "pip", "vlog", "depth"} else "full"
    host = body.host if body.host in _HOSTS else "tank"
    segs = _build_segments(body.script, shot)
    if not segs:
        raise HTTPException(status_code=400, detail="文案为空")
    items = _load_projects()
    pid = uuid.uuid4().hex[:8]
    now = datetime.now().isoformat(timespec="seconds")
    row = {
        "id": pid,
        "name": (body.name or "").strip() or ("口播项目 " + now[5:16].replace("T", " ")),
        "script": body.script,
        "shot": shot,
        "host": host,
        "photo_id": body.photo_id or "",
        "segments": segs,
        "total_seconds": sum(s["seconds"] for s in segs),
        "total_credits": sum(s["credits"] for s in segs),
        "status": "ready",
        "created_at": now,
    }
    items.insert(0, row)
    _save_projects(items[:200])
    return row


@router.get("/api/skills/digital-human-koubo/projects", summary="数字人koubo 项目列表")
def koubo_projects():
    return {"projects": _load_projects()}


@router.get("/api/skills/digital-human-koubo/projects/{pid}", summary="数字人koubo 项目详情")
def koubo_project(pid: str):
    for row in _load_projects():
        if row.get("id") == pid:
            return row
    raise HTTPException(status_code=404, detail="项目不存在")


@router.delete("/api/skills/digital-human-koubo/projects/{pid}", summary="删除数字人koubo 项目")
def koubo_project_delete(pid: str):
    items = _load_projects()
    kept = [r for r in items if r.get("id") != pid]
    if len(kept) == len(items):
        raise HTTPException(status_code=404, detail="项目不存在")
    _save_projects(kept)
    return {"ok": True}


@router.get("/api/skills/digital-human-koubo/projects/{pid}/tasks", summary="导出 Renoise tasks.json")
def koubo_project_tasks(pid: str):
    row = koubo_project(pid)
    persona = _persona()
    host = row.get("host") or "tank"
    if host == "me":
        hp = (persona.get("other_hosts") or {}).get("me") or {}
        ref = hp.get("reference_material") or ""
        template = hp.get("prompt_template") or ""
        ratio = hp.get("ratio") or "16:9"
        voice = hp.get("voice") or ""
    else:
        ref = ((persona.get("renoise") or {}).get("reference_material") or "")
        template = persona.get("prompt_template") or ""
        ratio = ((persona.get("model") or {}).get("ratio") or "9:16")
        voice = ((persona.get("voice") or {}).get("desc") or "")
    photo_ref = ("@" + ref) if ref else ("@upload:" + (row.get("photo_id") or ""))
    purpose = "explaining clearly"
    tasks = []
    for seg in row.get("segments") or []:
        act = ((persona.get("acts") or {}).get(seg.get("shot") or "full") or "")
        prompt = (template.format(ref=photo_ref, purpose=purpose, act=act, voice=voice,
                                  language="Mandarin Chinese", line=seg.get("text") or "")
                  if template else seg.get("text") or "")
        tasks.append({
            "model": ((persona.get("model") or {}).get("name") or "grok-video-1.5"),
            "kind": "video",
            "ratio": ratio,
            "resolution": seg.get("resolution") or "720p",
            "duration": seg.get("seconds") or 10,
            "label": "口播 " + (seg.get("id") or ""),
            "idempotencyKey": "dh-%s-%s-v1" % (pid, seg.get("id") or ""),
            "references": [{"ref": photo_ref, "role": "reference_image"}],
            "prompt": prompt,
        })
    return {
        "project_id": pid,
        "tasks": tasks,
        "note": "第一阶段只导出任务描述，未提交生成。实际提交前需要 Renoise MCP 的 material id 与实时报价。",
    }


# ─────────────────────────────────────────────────────────────
# 真正提交生成：走本机 MCP gateway 的 video.generate（Grok 1.5 图生视频）
# 前提：请求带登录 token、形象照已进素材库、账号有算力。
# ─────────────────────────────────────────────────────────────

LOCAL_BASE = "http://127.0.0.1:8000"
OUTPUT_DIR = DATA_DIR / "output"
_GEN: Dict[str, Dict[str, Any]] = {}
_GEN_LOCK = threading.Lock()
GEN_MODEL = "xai/grok-imagine-video-1.5/image-to-video"


class GenerateBody(BaseModel):
    all: bool = False


def _auth_header(request: Request) -> Dict[str, str]:
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth:
        raise HTTPException(status_code=401, detail="需要登录后才能提交生成")
    if not auth.lower().startswith("bearer "):
        auth = "Bearer " + auth
    return {"Authorization": auth}


def _deep_find(obj: Any, keys: tuple, depth: int = 0) -> Optional[str]:
    if depth > 10:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and isinstance(v, (str, int)) and str(v).strip():
                return str(v)
        for v in obj.values():
            found = _deep_find(v, keys, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _deep_find(v, keys, depth + 1)
            if found:
                return found
    return None


def _deep_urls(obj: Any, exts: tuple, depth: int = 0) -> List[str]:
    out: List[str] = []
    if depth > 12:
        return out
    if isinstance(obj, str):
        low = obj.lower()
        if low.startswith("http") and any(low.split("?")[0].endswith(e) for e in exts):
            out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_deep_urls(v, exts, depth + 1))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_deep_urls(v, exts, depth + 1))
    return out


def _parse_mcp_response(data: Any) -> Any:
    """MCP tools/call 返回 content[].text，里面常常又是一层 JSON 字符串。"""
    if isinstance(data, dict) and "result" in data:
        data = data["result"]
    if isinstance(data, dict) and isinstance(data.get("content"), list):
        chunks = []
        for item in data["content"]:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                chunks.append(item["text"])
        text = "\n".join(chunks).strip()
        if text:
            try:
                return json.loads(text)
            except Exception:
                return {"_text": text}
    return data


def _mcp_call(token: str, name: str, arguments: Dict[str, Any], timeout: float = 180.0) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": name, "arguments": arguments}}
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=30.0), trust_env=False) as client:
        r = client.post(LOCAL_BASE + "/mcp-gateway", json=payload,
                        headers={"Authorization": token, "Content-Type": "application/json"})
    try:
        data = r.json()
    except Exception:
        data = {"_raw": r.text[:2000]}
    if r.status_code >= 400:
        raise RuntimeError("MCP %s HTTP %s: %s" % (name, r.status_code, str(data)[:500]))
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError("MCP %s 错误: %s" % (name, str(data["error"])[:500]))
    return _parse_mcp_response(data)


def _ensure_asset(token: str, project: Dict[str, Any]) -> str:
    asset_id = project.get("photo_asset_id") or ""
    if asset_id:
        return asset_id
    photo_id = project.get("photo_id") or ""
    src = UPLOAD_DIR / photo_id if photo_id else None
    if not src or not src.is_file():
        raise RuntimeError("请先在第一步上传人物照片")
    with open(src, "rb") as fh:
        files = {"file": (src.name, fh, "image/jpeg" if src.suffix.lower() in (".jpg", ".jpeg") else "image/png")}
        with httpx.Client(timeout=120.0, trust_env=False) as client:
            r = client.post(LOCAL_BASE + "/api/assets/upload", files=files,
                            headers={"Authorization": token})
    if r.status_code >= 400:
        raise RuntimeError("素材入库失败 HTTP %s: %s" % (r.status_code, r.text[:400]))
    data = r.json() or {}
    aid = (data.get("asset_id") or (data.get("asset") or {}).get("asset_id")
           or (data.get("asset") or {}).get("id") or data.get("id") or "")
    if not aid:
        raise RuntimeError("素材入库未返回 asset_id: %s" % str(data)[:400])
    project["photo_asset_id"] = str(aid)
    return str(aid)


def _prompt_for(project: Dict[str, Any], seg: Dict[str, Any]) -> str:
    persona = _persona()
    host = project.get("host") or "tank"
    if host == "me":
        hp = (persona.get("other_hosts") or {}).get("me") or {}
        template = hp.get("prompt_template") or ""
        voice = hp.get("voice") or ""
        language = "Mandarin Chinese"
    else:
        template = persona.get("prompt_template") or ""
        voice = ((persona.get("voice") or {}).get("desc") or "")
        language = "Mandarin Chinese"
    act = ((persona.get("acts") or {}).get(seg.get("shot") or "full") or "")
    line = seg.get("text") or ""
    if template:
        try:
            return template.format(ref="the reference image", purpose="explaining clearly",
                                   act=act, voice=voice, language=language, line=line)
        except Exception:
            pass
    return "The person in the reference image talks to the camera, clearly lip-synced, in a %s voice:\n\"%s\"\nSound: only the voice, no music, no subtitles, no on-screen text." % (voice, line)


def _gen_job(job_id: str, project_id: str, token: str, only_first: bool) -> None:
    job = _GEN[job_id]
    try:
        items = _load_projects()
        project = next((r for r in items if r.get("id") == project_id), None)
        if not project:
            raise RuntimeError("项目不存在")
        asset_id = _ensure_asset(token, project)
        segs = project.get("segments") or []
        if only_first:
            segs = segs[:1]
        out_dir = OUTPUT_DIR / project_id
        out_dir.mkdir(parents=True, exist_ok=True)
        job["total"] = len(segs)
        generated = []
        for idx, seg in enumerate(segs, 1):
            job["current"] = idx
            job["message"] = "提交第 %d/%d 段" % (idx, len(segs))
            payload = {
                "model": GEN_MODEL,
                "prompt": _prompt_for(project, seg),
                "asset_id": asset_id,
                "duration": int(seg.get("seconds") or 8),
                "ratio": "16:9" if project.get("host") == "me" else "9:16",
                "resolution": seg.get("resolution") or "720p",
            }
            resp = _mcp_call(token, "invoke_capability",
                             {"capability_id": "video.generate", "payload": payload}, timeout=300.0)
            task_id = _deep_find(resp, ("task_id", "taskId", "request_id", "id"))
            urls = _deep_urls(resp, (".mp4", ".webm"))
            if not urls and task_id:
                job["message"] = "第 %d 段已提交，等待生成（task %s）" % (idx, task_id[:12])
                deadline = time.time() + 1800
                while time.time() < deadline:
                    time.sleep(10)
                    poll = _mcp_call(token, "invoke_capability",
                                     {"capability_id": "task.get_result", "payload": {"task_id": task_id}},
                                     timeout=180.0)
                    urls = _deep_urls(poll, (".mp4", ".webm"))
                    if urls:
                        break
                    status = str(_deep_find(poll, ("status", "state")) or "")
                    if status.lower() in ("failed", "error", "cancelled", "canceled"):
                        raise RuntimeError("第 %d 段生成失败：%s" % (idx, str(poll)[:300]))
            if not urls:
                raise RuntimeError("第 %d 段未拿到视频地址：%s" % (idx, str(resp)[:300]))
            url = urls[0]
            dest = out_dir / ("s%d.mp4" % idx)
            job["message"] = "下载第 %d 段" % idx
            with httpx.Client(timeout=httpx.Timeout(120.0, read=None), follow_redirects=True) as client:
                with client.stream("GET", url) as r:
                    r.raise_for_status()
                    with open(dest, "wb") as fh:
                        for chunk in r.iter_bytes(1024 * 256):
                            fh.write(chunk)
            generated.append({"id": seg.get("id"), "path": str(dest), "url": url, "task_id": task_id or ""})
            job["done"] = idx

        # 拼接
        job["message"] = "拼接成片"
        final = out_dir / "final.mp4"
        if len(generated) == 1:
            shutil.copyfile(generated[0]["path"], final)
        else:
            list_file = out_dir / "list.txt"
            list_file.write_text("".join("file '%s'\n" % Path(g["path"]).name for g in generated), encoding="utf-8")
            r = _run([str(FFMPEG_DIR / "ffmpeg.exe"), "-y", "-f", "concat", "-safe", "0",
                      "-i", str(list_file), "-c", "copy", str(final)], timeout=300)
            if r.returncode != 0:
                raise RuntimeError("拼接失败：%s" % (r.stderr or "")[-500:])

        items = _load_projects()
        for row in items:
            if row.get("id") == project_id:
                row["photo_asset_id"] = asset_id
                row["status"] = "generated"
                row["output"] = "/api/skills/digital-human-koubo/projects/%s/video" % project_id
                row["generated_segments"] = generated
                row["generated_at"] = datetime.now().isoformat(timespec="seconds")
        _save_projects(items)
        job["status"] = "done"
        job["progress"] = 100
        job["message"] = "生成完成"
        job["output"] = "/api/skills/digital-human-koubo/projects/%s/video" % project_id
    except Exception as exc:
        job["status"] = "error"
        msg = str(exc)[:1200]
        low = msg.lower()
        if "401" in msg or "unauthorized" in low or "登录" in msg:
            msg = "登录状态无效或已过期，请重新登录客户端后再提交（" + msg[:200] + "）"
        job["error"] = msg
        job["message"] = "生成失败"
        try:
            rows = _load_projects()
            for row in rows:
                if row.get("id") == project_id and row.get("status") == "generating":
                    row["status"] = "ready"
            _save_projects(rows)
        except Exception:
            pass
    finally:
        job["finished_at"] = time.time()


@router.post("/api/skills/digital-human-koubo/projects/{pid}/generate", summary="提交真实生成")
def koubo_generate(pid: str, body: GenerateBody, request: Request):
    headers = _auth_header(request)
    project = koubo_project(pid)
    if not project.get("segments"):
        raise HTTPException(status_code=400, detail="项目没有分段")
    if project.get("status") == "generating":
        raise HTTPException(status_code=409, detail="已有生成任务在跑")
    items = _load_projects()
    for row in items:
        if row.get("id") == pid:
            row["status"] = "generating"
    _save_projects(items)
    job_id = uuid.uuid4().hex[:10]
    job = {"id": job_id, "status": "running", "progress": 0, "message": "准备提交",
           "current": 0, "total": 1, "error": "", "started_at": time.time()}
    with _GEN_LOCK:
        _GEN[job_id] = job
    threading.Thread(target=_gen_job, args=(job_id, pid, headers["Authorization"], not body.all), daemon=True).start()
    return {"ok": True, "job_id": job_id, "mode": "first" if not body.all else "all"}


@router.get("/api/skills/digital-human-koubo/generate/jobs/{job_id}", summary="生成进度")
def koubo_generate_job(job_id: str):
    with _GEN_LOCK:
        job = _GEN.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.get("/api/skills/digital-human-koubo/projects/{pid}/video", summary="成片预览/下载")
def koubo_project_video(pid: str):
    path = OUTPUT_DIR / pid / "final.mp4"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="成片还没有生成")
    return FileResponse(str(path), media_type="video/mp4", filename="koubo-%s.mp4" % pid)
