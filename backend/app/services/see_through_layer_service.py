from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional


_DEFAULT_ROOT = (
    Path("D:/3")
    / "\u62c6\u5206\u5185\u90e8\u6d4b\u8bd5\u7248\u672c"
    / "\u7f51\u9875UI\u8bbe\u8ba1"
    / "see-through"
)
_DEFAULT_PYTHON = Path("C:/Users/admin/ai-runtime/miniconda3/envs/see_through/python.exe")
_DEFAULT_LAYERDIFF = Path("C:/Users/admin/ai-runtime/models/seethroughv0.0.2_layerdiff3d")
_DEFAULT_MARIGOLD = Path("C:/Users/admin/ai-runtime/models/seethroughv0.0.1_marigold")


def _first_existing(paths: List[Path]) -> Optional[Path]:
    for path in paths:
        if path.exists():
            return path
    return None


def root_dir() -> Path:
    raw = os.environ.get("AI3D_SEE_THROUGH_ROOT") or os.environ.get("SEE_THROUGH_ROOT")
    return Path(raw).expanduser() if raw else _DEFAULT_ROOT


def script_path() -> Path:
    raw = os.environ.get("AI3D_SEE_THROUGH_SCRIPT") or os.environ.get("SEE_THROUGH_SCRIPT")
    if raw:
        path = Path(raw)
        return path if path.is_absolute() else root_dir() / path
    return root_dir() / "inference" / "scripts" / "inference_psd.py"


def python_command() -> Optional[str]:
    raw = os.environ.get("AI3D_SEE_THROUGH_PYTHON") or os.environ.get("SEE_THROUGH_PYTHON")
    if raw:
        return raw
    if _DEFAULT_PYTHON.exists():
        return str(_DEFAULT_PYTHON)
    found = shutil.which("python")
    return found


def _model_path(*names: str, default: Path) -> Optional[Path]:
    for name in names:
        raw = os.environ.get(name)
        if raw:
            path = Path(raw).expanduser()
            return path if path.exists() else None
    return default if default.exists() else None


def health() -> Dict[str, Any]:
    root = root_dir()
    script = script_path()
    py = python_command()
    python_exists = bool(py and (Path(py).exists() if (":" in py or "/" in py or "\\" in py) else shutil.which(py)))
    deps: Dict[str, bool] = {}
    dependencies_ready = False
    if python_exists and py:
        try:
            probe = subprocess.run(
                [
                    py,
                    "-c",
                    (
                        "import importlib.util,json;"
                        "mods=['torch','diffusers','psd_tools','cv2','PIL'];"
                        "print(json.dumps({m: bool(importlib.util.find_spec(m)) for m in mods}))"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=12,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if probe.returncode == 0:
                loaded = json.loads((probe.stdout or "{}").strip() or "{}")
                if isinstance(loaded, dict):
                    deps = {str(k): bool(v) for k, v in loaded.items()}
            dependencies_ready = all(deps.get(name) for name in ("torch", "diffusers", "psd_tools", "cv2", "PIL"))
        except Exception:
            dependencies_ready = False
    ready = bool(root.exists() and script.exists() and python_exists and dependencies_ready)
    return {
        "ready": ready,
        "root": str(root),
        "root_exists": root.exists(),
        "script": str(script),
        "script_exists": script.exists(),
        "python": py or "",
        "python_exists": python_exists,
        "dependencies": deps,
        "dependencies_ready": dependencies_ready,
        "layerdiff_model": str(_model_path("AI3D_SEE_THROUGH_LAYERDIFF", "SEE_THROUGH_LAYERDIFF", default=_DEFAULT_LAYERDIFF) or ""),
        "marigold_model": str(_model_path("AI3D_SEE_THROUGH_MARIGOLD", "SEE_THROUGH_MARIGOLD", default=_DEFAULT_MARIGOLD) or ""),
    }


def _collect_outputs(save_dir: Path) -> Dict[str, Any]:
    json_files = sorted(save_dir.glob("*.psd.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not json_files:
        raise RuntimeError("see-through 没有输出 PSD JSON 元数据。")
    json_path = json_files[0]
    base_name = json_path.name[:-len(".psd.json")]
    part_dir = save_dir / base_name
    psd_path = save_dir / f"{base_name}.psd"
    depth_psd_path = save_dir / f"{base_name}_depth.psd"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    parts = data.get("parts") if isinstance(data.get("parts"), dict) else {}
    frame_size = data.get("frame_size") if isinstance(data.get("frame_size"), list) else []
    layers: List[Dict[str, Any]] = []
    for tag, info in parts.items():
        if not isinstance(info, dict):
            continue
        png_path = part_dir / f"{tag}.png"
        depth_path = part_dir / f"{tag}_depth.png"
        if not png_path.is_file():
            optimized_png = part_dir / "optimized" / f"{tag}.png"
            if optimized_png.is_file():
                png_path = optimized_png
        if not png_path.is_file():
            continue
        layers.append({
            "tag": str(tag),
            "path": str(png_path),
            "depth_path": str(depth_path) if depth_path.is_file() else "",
            "xyxy": info.get("xyxy"),
            "part_id": info.get("part_id"),
            "depth_median": info.get("depth_median"),
        })
    return {
        "json_path": str(json_path),
        "psd_path": str(psd_path) if psd_path.is_file() else "",
        "depth_psd_path": str(depth_psd_path) if depth_psd_path.is_file() else "",
        "part_dir": str(part_dir),
        "reconstruction_path": str(part_dir / "reconstruction.png") if (part_dir / "reconstruction.png").is_file() else "",
        "source_preview_path": str(part_dir / "src_img.png") if (part_dir / "src_img.png").is_file() else "",
        "frame_size": frame_size,
        "layers": layers,
    }


async def run_layer_decomposition(
    *,
    source_path: Path,
    save_dir: Path,
    resolution: int = 1280,
    timeout_seconds: int = 3600,
) -> Dict[str, Any]:
    status = health()
    if not status.get("ready"):
        raise RuntimeError(
            "see-through 分层环境未就绪："
            f"root_exists={status.get('root_exists')}, script_exists={status.get('script_exists')}, "
            f"python_exists={status.get('python_exists')}。请先配置 AI3D_SEE_THROUGH_ROOT 和 AI3D_SEE_THROUGH_PYTHON，或运行 see-through 安装脚本。"
        )
    source_path = source_path.resolve()
    save_dir.mkdir(parents=True, exist_ok=True)
    args = [
        str(status["python"]),
        str(script_path().resolve()),
        "--srcp",
        str(source_path),
        "--save_dir",
        str(save_dir.resolve()),
        "--save_to_psd",
        "--resolution",
        str(int(resolution or 1280)),
    ]
    if os.environ.get("AI3D_SEE_THROUGH_TBLR_SPLIT", "").strip().lower() in {"1", "true", "yes"}:
        args.append("--tblr_split")
    layerdiff = _model_path("AI3D_SEE_THROUGH_LAYERDIFF", "SEE_THROUGH_LAYERDIFF", default=_DEFAULT_LAYERDIFF)
    marigold = _model_path("AI3D_SEE_THROUGH_MARIGOLD", "SEE_THROUGH_MARIGOLD", default=_DEFAULT_MARIGOLD)
    if layerdiff:
        args.extend(["--repo_id_layerdiff", str(layerdiff)])
    if marigold:
        args.extend(["--repo_id_depth", str(marigold)])
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "HF_ENDPOINT": os.environ.get("AI3D_SEE_THROUGH_HF_ENDPOINT") or os.environ.get("HF_ENDPOINT") or "https://hf-mirror.com",
        "HF_HOME": os.environ.get("AI3D_SEE_THROUGH_HF_HOME") or os.environ.get("HF_HOME") or str(root_dir().parent / "image-split-workspace" / "hf-cache"),
    }
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(root_dir()),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout_raw, stderr_raw = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(f"see-through 分层超时（>{timeout_seconds}s）。") from exc
    stdout = stdout_raw.decode("utf-8", errors="replace")
    stderr = stderr_raw.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        detail = (stderr or stdout or "unknown error").strip()[-3000:]
        raise RuntimeError(f"see-through 分层失败：{detail}")
    outputs = _collect_outputs(save_dir)
    outputs["stdout_tail"] = stdout[-3000:]
    outputs["stderr_tail"] = stderr[-3000:]
    outputs["resolution"] = int(resolution or 1280)
    outputs["health"] = status
    return outputs
