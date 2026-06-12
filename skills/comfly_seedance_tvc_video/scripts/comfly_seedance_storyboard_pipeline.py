from __future__ import annotations

import concurrent.futures
import json
import logging
import shutil
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypedDict
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

try:
    from runtime import Args  # type: ignore
except ImportError:
    class Args:  # type: ignore[override]
        def __init__(self, input: Dict[str, Any]):
            self.input = input


class Input(TypedDict, total=False):
    reference_image: str
    reference_images: List[str]
    apikey: str
    base_url: str
    task_text: str
    platform: str
    country: str
    language: str
    analysis_model: str
    image_model: str
    image_model_fallback: str
    video_model: str
    video_channel: str
    video_base_url: str
    video_fallback_model: str
    video_fallback_channel: str
    video_fallback_base_url: str
    video_fallbacks: List[Dict[str, Any]]
    workflow_mode: str
    aspect_ratio: str
    storyboard_count: int
    segment_count: int
    segment_duration_seconds: int
    total_duration_seconds: int
    shot_concurrency: int
    poll_interval_seconds: int
    max_polls: int
    output_dir: str
    upload_retries: int
    analysis_retries: int
    image_generation_retries: int
    video_submit_retries: int
    network_retry_delay_seconds: int
    merge_clips: bool
    ffmpeg_path: str
    clip_download_retries: int
    clip_download_timeout_seconds: int
    generate_audio: bool
    watermark: bool
    seed: int


@dataclass
class PipelineConfig:
    base_url: str
    api_key: str
    task_text: str = ""
    platform: str = "brand_tvc"
    country: str = "China"
    language: str = "zh-CN"
    analysis_model: str = "gpt-4.1-mini"
    image_model: str = "gpt-image-2"
    image_model_fallback: str = "nano-banana-2"
    video_model: str = "doubao-seedance-2-0-fast-260128"
    video_channel: str = "seedance"
    video_base_url: str = ""
    video_fallback_model: str = "veo3.1-fast"
    video_fallback_channel: str = "comfly"
    video_fallback_base_url: str = ""
    video_fallbacks: List[Dict[str, Any]] = field(default_factory=list)
    workflow_mode: str = "storyboard"
    aspect_ratio: str = "9:16"
    segment_count: int = 2
    segment_duration_seconds: int = 10
    total_duration_seconds: int = 20
    shot_concurrency: int = 2
    poll_interval_seconds: int = 10
    max_polls: int = 90
    output_dir: str = ""
    upload_retries: int = 3
    analysis_retries: int = 2
    image_generation_retries: int = 3
    video_submit_retries: int = 2
    network_retry_delay_seconds: int = 3
    merge_clips: bool = True
    ffmpeg_path: str = "ffmpeg"
    clip_download_retries: int = 2
    clip_download_timeout_seconds: int = 180
    generate_audio: bool = True
    watermark: bool = False
    seed: int = -1


ALLOWED_TOTAL_DURATIONS = (10, 20, 30, 40, 50, 60)
ALLOWED_YUNWU_TOTAL_DURATIONS = (8, 16, 24, 32, 40, 48)
FIXED_SEGMENT_DURATION_SECONDS = 10
YUNWU_SEGMENT_DURATION_SECONDS = 8
_SEEDANCE_MODEL_ALIASES = {
    "seedance-2-0-pro-250528": "doubao-seedance-2-0-260128",
    "seedance-2-0-lite-250428": "doubao-seedance-2-0-fast-260128",
    "seedance-2-0-260128": "doubao-seedance-2-0-260128",
    "seedance-2-0-fast-260128": "doubao-seedance-2-0-fast-260128",
}


class PipelineError(RuntimeError):
    pass


class InsufficientCreditError(PipelineError):
    pass


class StopNewSubmissionsError(PipelineError):
    pass


@dataclass
class SubmitControl:
    lock: threading.Lock = field(default_factory=threading.Lock)
    stop_new_submits: threading.Event = field(default_factory=threading.Event)
    submitted_success_count: int = 0
    stop_reason: str = ""

    def note_submit_success(self) -> None:
        with self.lock:
            self.submitted_success_count += 1

    def mark_stop_new_submits(self, reason: str) -> bool:
        with self.lock:
            already_stopped = self.stop_new_submits.is_set()
            if not already_stopped:
                self.stop_reason = reason
                self.stop_new_submits.set()
            return already_stopped

    def should_stop_new_submits(self) -> bool:
        return self.stop_new_submits.is_set()

    def current_stop_reason(self) -> str:
        with self.lock:
            return self.stop_reason

    def has_any_successful_submit(self) -> bool:
        with self.lock:
            return self.submitted_success_count > 0


def _extract_progress_percent(payload: Any) -> Optional[int]:
    if not isinstance(payload, dict):
        return None
    keys = (
        "progress",
        "percent",
        "percentage",
        "progress_percent",
        "completion",
        "completed_percent",
    )
    stack: List[Any] = [payload]
    seen = 0
    while stack and seen < 64:
        seen += 1
        cur = stack.pop(0)
        if not isinstance(cur, dict):
            continue
        for key in keys:
            val = cur.get(key)
            if isinstance(val, (int, float)):
                num = float(val)
                if 0 <= num <= 1:
                    num *= 100
                if 0 <= num <= 100:
                    return int(round(num))
            if isinstance(val, str):
                s = val.strip().rstrip("%")
                try:
                    num = float(s)
                except ValueError:
                    continue
                if 0 <= num <= 1:
                    num *= 100
                if 0 <= num <= 100:
                    return int(round(num))
        for key in ("data", "result", "output", "meta", "metadata", "task"):
            child = cur.get(key)
            if isinstance(child, dict):
                stack.append(child)
    return None


def _optional_model(value: Any) -> str:
    model = str(value or "").strip()
    if model.lower() in {"none", "null", "off", "false", "0", "disabled", "disable", "no"}:
        return ""
    return model


class RunLogger:
    def __init__(self, base_dir: str, config: PipelineConfig, raw_input: Dict[str, Any]) -> None:
        root = Path(base_dir)
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = root / f"run_{stamp}"
        n = 1
        while self.run_dir.exists():
            n += 1
            self.run_dir = root / f"run_{stamp}_{n:02d}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.manifest: Dict[str, Any] = {
            "run_dir": str(self.run_dir),
            "created_at": datetime.now().isoformat(),
            "status": "running",
            "config": {
                "base_url": config.base_url,
                "analysis_model": config.analysis_model,
                "image_model": config.image_model,
                "image_model_fallback": config.image_model_fallback,
                "video_model": config.video_model,
                "video_channel": config.video_channel,
                "video_base_url": config.video_base_url,
                "video_fallback_model": config.video_fallback_model,
                "video_fallback_channel": config.video_fallback_channel,
                "video_fallback_base_url": config.video_fallback_base_url,
                "video_fallbacks": config.video_fallbacks or [],
                "workflow_mode": config.workflow_mode,
                "aspect_ratio": config.aspect_ratio,
                "segment_count": config.segment_count,
                "segment_duration_seconds": config.segment_duration_seconds,
                "total_duration_seconds": config.total_duration_seconds,
                "shot_concurrency": config.shot_concurrency,
                "generate_audio": config.generate_audio,
                "watermark": config.watermark,
            },
            "input": {k: v for k, v in raw_input.items() if k != "apikey"},
            "steps": {},
            "segments": {},
            "errors": [],
        }
        self.write_json("00_input.json", self.manifest["input"])
        self._save()

    def write_json(self, filename: str, payload: Any) -> None:
        with (self.run_dir / filename).open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def step(self, name: str, status: str, attempts: int = 0, payload: Any = None, error: Optional[str] = None) -> None:
        with self.lock:
            entry = {
                "status": status,
                "attempts": attempts,
                "error": error,
                "updated_at": datetime.now().isoformat(),
            }
            if isinstance(payload, dict):
                for key in ("progress", "message", "task_id", "status"):
                    if key in payload and payload.get(key) is not None:
                        entry[key if key != "status" else "upstream_status"] = payload.get(key)
            self.manifest["steps"][name] = entry
            self._save()
        if payload is not None:
            self.write_json(f"{name}.json", payload)

    def segment(self, index: int, stage: str, status: str, attempts: int = 0, payload: Any = None, error: Optional[str] = None) -> None:
        key = str(index)
        with self.lock:
            entry = {
                "status": status,
                "attempts": attempts,
                "error": error,
                "updated_at": datetime.now().isoformat(),
            }
            if isinstance(payload, dict):
                for payload_key in ("progress", "message", "task_id", "status"):
                    if payload_key in payload and payload.get(payload_key) is not None:
                        entry[payload_key if payload_key != "status" else "upstream_status"] = payload.get(payload_key)
            self.manifest["segments"].setdefault(key, {})[stage] = entry
            self._save()
        if payload is not None:
            self.write_json(f"segment_{index:02d}_{stage}.json", payload)

    def error(self, where: str, message: str) -> None:
        with self.lock:
            self.manifest["errors"].append({"where": where, "message": message, "ts": datetime.now().isoformat()})
            self._save()

    def finish(self, status: str, payload: Any = None) -> None:
        with self.lock:
            self.manifest["status"] = status
            self.manifest["finished_at"] = datetime.now().isoformat()
            self._save()
        if payload is not None:
            self.write_json("99_result.json", payload)

    def _save(self) -> None:
        with (self.run_dir / "manifest.json").open("w", encoding="utf-8") as f:
            json.dump(self.manifest, f, ensure_ascii=False, indent=2)


def _normalize_aspect_ratio(raw: str, default: str = "9:16") -> str:
    s = (raw or "").strip().replace(" ", "").lower()
    aliases = {
        "portrait": "9:16",
        "vertical": "9:16",
        "landscape": "16:9",
        "horizontal": "16:9",
        "square": "1:1",
    }
    if not s:
        return default
    if s in aliases:
        return aliases[s]
    if s in {"1:1", "4:3", "3:4", "4:5", "5:4", "9:16", "16:9", "21:9"}:
        return s
    return default


def _normalize_seedance_model(raw: str) -> str:
    model = (raw or "").strip()
    return _SEEDANCE_MODEL_ALIASES.get(model, model)


def _is_veo_video_model(raw: str) -> bool:
    s = (raw or "").strip().lower().replace("_", "-").replace(" ", "")
    return s.startswith("veo") or s in {"yunwu-veo3.1-plus", "veo3.1-plus", "veo3.1"}


def _is_grok_video_model(raw: str) -> bool:
    s = (raw or "").strip().lower().replace("_", "-").replace(" ", "")
    return s in {
        "grok-imagine-video-1.5-preview",
        "grok-imagine-1.0-video",
        "grok-video-3",
        "yingmeng1.5plus",
        "影梦1.5plus",
    } or s.startswith("xai/grok-imagine-video/")


def _normalize_video_channel(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s in {"openmind", "open-mind", "om", "openmindapi"}:
        return "openmind"
    if s in {"yunwu", "yw", "cloudmist", "cloud-mist", "云雾", "雲霧"}:
        return "yunwu"
    if s in {"comfly", "veo", "veo3", "veo3.1", "veo31"}:
        return "comfly"
    return "seedance"


def _normalize_api_base_url(raw: str, default: str) -> str:
    base = (raw or default or "").strip().rstrip("/")
    if base.lower().endswith("/v1") or base.lower().endswith("/v2"):
        base = base[:-3].rstrip("/")
    return base


def _normalize_video_base_url_for_channel(channel: str, raw: str, default: str) -> str:
    return _normalize_api_base_url(raw, default)


def _default_video_base_url(channel: str, api_base: str) -> str:
    return api_base


def _default_video_model(channel: str) -> str:
    normalized = _normalize_video_channel(channel)
    if normalized == "openmind":
        return "veo31-fast"
    if normalized == "yunwu":
        return "veo3.1"
    if normalized == "comfly":
        return "veo3.1-fast"
    return "doubao-seedance-2-0-fast-260128"


def _video_provider_label(channel: str, model: str) -> str:
    return f"{_normalize_video_channel(channel)}:{(model or '').strip()}"


def _normalize_video_provider_item(raw: Any, base_url: str) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    channel = _normalize_video_channel(str(raw.get("channel") or raw.get("video_channel") or ""))
    model = str(raw.get("model") or raw.get("video_model") or "").strip() or _default_video_model(channel)
    default_base = _default_video_base_url(channel, base_url)
    provider_base_url = _normalize_video_base_url_for_channel(
        channel,
        str(raw.get("base_url") or raw.get("video_base_url") or ""),
        default_base,
    )
    return {"channel": channel, "model": model, "base_url": provider_base_url}


def _ordered_video_providers(client: "ComflySeedanceClient") -> List[Dict[str, str]]:
    providers: List[Dict[str, str]] = [
        {
            "role": "primary",
            "channel": client.video_channel,
            "base_url": client.video_base_url,
            "model": (client.config.video_model or "").strip() or _default_video_model(client.video_channel),
        }
    ]
    seen = {_video_provider_label(providers[0]["channel"], providers[0]["model"])}

    for raw in client.config.video_fallbacks or []:
        item = _normalize_video_provider_item(raw, client.base_url)
        if not item:
            continue
        label = _video_provider_label(item["channel"], item["model"])
        if label in seen:
            continue
        seen.add(label)
        providers.append({"role": "fallback", **item})

    fallback_channel = _normalize_video_channel(client.config.video_fallback_channel)
    fallback_model = (client.config.video_fallback_model or "").strip() or _default_video_model(fallback_channel)
    label = _video_provider_label(fallback_channel, fallback_model)
    if label not in seen:
        providers.append(
            {
                "role": "fallback",
                "channel": fallback_channel,
                "base_url": client.video_fallback_base_url,
                "model": fallback_model,
            }
        )
    return providers


def _segment_seconds_for_channel(channel: str) -> int:
    return YUNWU_SEGMENT_DURATION_SECONDS if _normalize_video_channel(channel) == "yunwu" else FIXED_SEGMENT_DURATION_SECONDS


def _segment_seconds_for_video(channel: str, model: str) -> int:
    normalized = _normalize_video_channel(channel)
    if normalized == "yunwu":
        return YUNWU_SEGMENT_DURATION_SECONDS
    if _is_veo_video_model(model):
        return YUNWU_SEGMENT_DURATION_SECONDS
    if _is_grok_video_model(model):
        return FIXED_SEGMENT_DURATION_SECONDS
    return FIXED_SEGMENT_DURATION_SECONDS


def _allowed_total_durations_for_channel(channel: str) -> tuple[int, ...]:
    return ALLOWED_YUNWU_TOTAL_DURATIONS if _normalize_video_channel(channel) == "yunwu" else ALLOWED_TOTAL_DURATIONS


def _allowed_total_durations_for_video(channel: str, model: str) -> tuple[int, ...]:
    return ALLOWED_YUNWU_TOTAL_DURATIONS if _segment_seconds_for_video(channel, model) == YUNWU_SEGMENT_DURATION_SECONDS else ALLOWED_TOTAL_DURATIONS


def _normalize_seedance_duration(raw: int) -> int:
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        seconds = FIXED_SEGMENT_DURATION_SECONDS
    return 10 if seconds >= 10 else 5


def _first_text(d: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = d.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _non_retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(key in text for key in ("http 400", "http 401", "http 403", "http 404", "missing", "not found"))


def _is_insufficient_credit_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return "http 402" in text or "积分不足" in str(exc or "") or "余额不足" in str(exc or "")


def _is_transient_video_poll_error(exc: Exception, channel: str) -> bool:
    text = str(exc or "").lower()
    normalized_channel = _normalize_video_channel(channel)
    if normalized_channel == "openmind":
        return any(
            flag in text
            for flag in (
                "http 500",
                "http 502",
                "http 503",
                "http 504",
                "readtimeout",
                "timed out",
                "timeout",
                "connection reset",
                "temporarily unavailable",
            )
        )
    return any(flag in text for flag in ("readtimeout", "timed out", "timeout"))


def _retry(action: str, attempts: int, delay: int, logger_obj: RunLogger, fn: Callable[[], Any]) -> tuple[Any, int]:
    last: Optional[Exception] = None
    for i in range(1, attempts + 1):
        try:
            return fn(), i
        except Exception as exc:
            last = exc
            logger_obj.error(action, f"attempt {i} failed: {exc}")
            if i >= attempts or _non_retryable(exc):
                break
            time.sleep(delay * i)
    raise PipelineError(f"{action} failed after {attempts} attempt(s): {last}")


def _parse_json(text: str) -> Dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        raise PipelineError("Model returned empty text")
    candidates = [stripped]
    if "```json" in stripped:
        start = stripped.find("```json") + len("```json")
        end = stripped.find("```", start)
        if end > start:
            candidates.insert(0, stripped[start:end].strip())
    lbrace = stripped.find("{")
    rbrace = stripped.rfind("}")
    if lbrace >= 0 and rbrace > lbrace:
        candidates.append(stripped[lbrace : rbrace + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise PipelineError(f"Unable to parse JSON from model output: {stripped[:500]}")


def _download_file(url: str, path: Path, timeout_seconds: int) -> Path:
    r = requests.get(url, timeout=timeout_seconds)
    if r.status_code != 200:
        raise PipelineError(f"Download failed HTTP {r.status_code}: {url}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return path


def _resolve_tool_binary(tool_name: str, configured_path: str = "") -> str:
    explicit = (configured_path or "").strip()
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which(explicit or tool_name)
    return found or explicit or tool_name


def _probe_stream_types(media_path: str, ffmpeg_path: str) -> List[str]:
    ffprobe_binary = _resolve_tool_binary("ffprobe", "")
    if (not ffprobe_binary or ffprobe_binary == "ffprobe") and ffmpeg_path and ffmpeg_path != "ffmpeg":
        ffmpeg_candidate = Path(ffmpeg_path)
        sidecar_name = "ffprobe.exe" if ffmpeg_candidate.suffix.lower() == ".exe" else "ffprobe"
        candidate = ffmpeg_candidate.with_name(sidecar_name)
        if candidate.exists():
            ffprobe_binary = str(candidate)
    proc = subprocess.run(
        [ffprobe_binary, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", media_path],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return []
    try:
        payload = json.loads(proc.stdout or "{}")
    except Exception:
        return []
    return [str(s.get("codec_type", "")).strip().lower() for s in payload.get("streams", []) if isinstance(s, dict)]


def _merge_completed_segments(config: PipelineConfig, logger_obj: RunLogger, segments: List[Dict[str, Any]]) -> Dict[str, Any]:
    ffmpeg_binary = _resolve_tool_binary("ffmpeg", config.ffmpeg_path)
    clips_dir = logger_obj.run_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    downloaded: List[Dict[str, Any]] = []
    for seg in sorted(segments, key=lambda item: int(item.get("index", 0))):
        index = int(seg.get("index", 0))
        clip_url = _first_text(seg, "mp4url")
        clip_path = clips_dir / f"segment_{index:02d}.mp4"
        logger_obj.segment(index, "merge_download", "running", payload={"index": index, "url": clip_url})
        downloaded_path, attempts = _retry(
            f"download_segment_{index:02d}",
            config.clip_download_retries,
            config.network_retry_delay_seconds,
            logger_obj,
            lambda clip_url=clip_url, clip_path=clip_path: _download_file(clip_url, clip_path, config.clip_download_timeout_seconds),
        )
        logger_obj.segment(index, "merge_download", "success", attempts=attempts, payload={"index": index, "path": str(downloaded_path)})
        downloaded.append({"index": index, "path": str(downloaded_path), "url": clip_url})

    merged_path = logger_obj.run_dir / "merged_output.mp4"
    if len(downloaded) == 1:
        shutil.copyfile(downloaded[0]["path"], merged_path)
        streams = _probe_stream_types(downloaded[0]["path"], ffmpeg_binary)
        return {
            "status": "success",
            "merged_video_path": str(merged_path),
            "downloaded_clips": downloaded,
            "merge_mode": "single_clip_copy",
            "audio_preserved": "audio" in streams,
        }

    cmd: List[str] = [ffmpeg_binary, "-y"]
    video_inputs: List[str] = []
    audio_inputs: List[str] = []
    all_have_audio = True
    for item in downloaded:
        cmd.extend(["-i", item["path"]])
        input_index = len(video_inputs)
        video_inputs.append(f"[{input_index}:v:0]")
        stream_types = _probe_stream_types(item["path"], ffmpeg_binary)
        if "audio" in stream_types:
            audio_inputs.append(f"[{input_index}:a:0]")
        else:
            all_have_audio = False
    output_arg = merged_path.resolve().as_posix()
    if all_have_audio and len(audio_inputs) == len(downloaded):
        interleaved: List[str] = []
        for v, a in zip(video_inputs, audio_inputs):
            interleaved.extend([v, a])
        filter_complex = "".join(interleaved) + f"concat=n={len(downloaded)}:v=1:a=1[v][a]"
        cmd.extend(["-filter_complex", filter_complex, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", "-movflags", "+faststart", output_arg])
        audio_preserved = True
    else:
        filter_complex = "".join(video_inputs) + f"concat=n={len(downloaded)}:v=1:a=0[v]"
        cmd.extend(["-filter_complex", filter_complex, "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", output_arg])
        audio_preserved = False
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not merged_path.exists():
        raise PipelineError(proc.stderr.strip() or proc.stdout.strip() or f"ffmpeg exited with {proc.returncode}")
    return {
        "status": "success",
        "merged_video_path": str(merged_path),
        "downloaded_clips": downloaded,
        "merge_mode": "ffmpeg_concat_filter",
        "audio_preserved": audio_preserved,
        "ffmpeg_command": cmd,
    }


def _locale_guidance(config: PipelineConfig) -> str:
    if (config.country or "").strip().lower() in {"china", "mainland china", ""}:
        return "Use Simplified Chinese copy and China-market premium brand advertising style."
    if (config.country or "").strip():
        return f"Use localized copy and premium commercial styling appropriate for {config.country.strip()}."
    return "Use localized styling that matches the intended market and language."


def _analysis_prompt(config: PipelineConfig) -> str:
    return (
        "You are a senior TVC storyboard director and premium commercial planner.\n"
        "The user may upload one or more reference images, or may provide only a text task brief. If reference images are present, the first image can be a storyboard board example, and other images may be product references, packaging references, scene references, or style references. If no reference image is present, infer the product, scene, and style from the text task brief.\n"
        "Your task is to plan one coherent commercial film with a single visual identity, a single campaign arc, and smooth transitions between segments.\n"
        f"The final film must be exactly {config.total_duration_seconds} seconds, split into exactly {config.segment_count} storyboard boards.\n"
        f"Each storyboard board must represent exactly {config.segment_duration_seconds} seconds of the same film.\n"
        "Return strict JSON with top-level keys: product_summary, global_style, campaign_context, storyboard_boards.\n"
        "product_summary must contain: brand_name, product_name, product_form, consistency_rules.\n"
        "global_style must contain: palette_cn, tone_cn, keywords_en.\n"
        "campaign_context must contain: hero_subject_cn, campaign_arc_cn, continuity_rules_cn, transition_style_cn.\n"
        "storyboard_boards must be an array of exactly "
        f"{config.segment_count} items.\n"
        "Each item must contain these keys:\n"
        "index, time_range_cn, duration_seconds, board_title_cn, board_goal_cn, narrative_stage_cn, continuity_anchor_cn, transition_in_cn, transition_out_cn, subshots_cn, voiceover_cn, board_copy_cn, visual_focus_cn, composition_notes_cn, storyboard_image_prompt_en, seedance_prompt_en.\n"
        "Rules:\n"
        f"1. This is not per-shot output. Each item is one complete {config.segment_duration_seconds}-second storyboard board image for one segment of the same final film.\n"
        f"2. Every board must have duration_seconds={config.segment_duration_seconds}, and the boards together must cover {config.total_duration_seconds} seconds continuously with no overlap and no gaps.\n"
        f"3. Each board must internally describe 3-5 micro-shots or scenes inside the same {config.segment_duration_seconds}-second segment.\n"
        "4. The full set of boards must feel like one integrated commercial, not disconnected mini videos. The story should progress naturally from opening, to product proof, to payoff, to brand close.\n"
        "5. continuity_anchor_cn must describe what must visually stay continuous from the previous segment into this one.\n"
        "6. transition_in_cn and transition_out_cn must describe how the current segment connects to its neighbors in camera motion, lighting, props, subject behavior, or composition rhythm.\n"
        "7. voiceover_cn should be the full Chinese narration for that segment, but all segments together must read like one continuous ad voice-over.\n"
        "8. board_copy_cn should be the Chinese labels / supporting copy that should appear on the board image.\n"
        "9. storyboard_image_prompt_en must ask for one polished storyboard board image with premium graphic design, multiple internal sub-panels, time labels, Chinese script/copy blocks, and product-consistent visuals.\n"
        "10. Keep product consistency across all boards: same package family, logo family, hero subject, props, colorway, materials, and campaign art direction.\n"
        "11. The final segment must feel like a true commercial ending, clearly resolving the same campaign arc established by the first segment.\n"
        "12. Return JSON only.\n"
        f"Extra user task brief: {config.task_text or 'No extra task text provided.'}\n"
        f"Locale guidance: {_locale_guidance(config)}"
    )


def _coerce_plan(plan: Dict[str, Any], config: PipelineConfig) -> None:
    if not isinstance(plan.get("product_summary"), dict):
        plan["product_summary"] = {"brand_name": "", "product_name": "", "product_form": "", "consistency_rules": ""}
    if not isinstance(plan.get("global_style"), dict):
        plan["global_style"] = {"palette_cn": "", "tone_cn": "", "keywords_en": []}
    if not isinstance(plan.get("campaign_context"), dict):
        plan["campaign_context"] = {
            "hero_subject_cn": "",
            "campaign_arc_cn": "",
            "continuity_rules_cn": "",
            "transition_style_cn": "",
        }

    raw = plan.get("storyboard_boards")
    if raw is None:
        raw = plan.get("storyboards")
    if not isinstance(raw, list):
        plan["storyboard_boards"] = []
        return

    cleaned: List[Dict[str, Any]] = []
    start_second = 0
    for i, item in enumerate(raw[: config.segment_count], start=1):
        if not isinstance(item, dict):
            continue
        one = dict(item)
        one["index"] = i
        one["duration_seconds"] = config.segment_duration_seconds
        one["time_range_cn"] = f"{start_second}-{start_second + config.segment_duration_seconds}秒"
        one["narrative_stage_cn"] = _first_text(one, "narrative_stage_cn") or (
            "开场引入" if i == 1 else ("品牌收束" if i == config.segment_count else "中段推进")
        )
        one["continuity_anchor_cn"] = _first_text(one, "continuity_anchor_cn", "must_keep_cn")
        one["transition_in_cn"] = _first_text(one, "transition_in_cn") or ("从上一段自然承接同一品牌氛围与主体动作" if i > 1 else "从品牌主视觉开场")
        one["transition_out_cn"] = _first_text(one, "transition_out_cn") or ("自然引出下一段的主体动作与卖点" if i < config.segment_count else "收束到品牌结尾与购买记忆点")
        start_second += config.segment_duration_seconds
        cleaned.append(one)
    plan["storyboard_boards"] = cleaned


def _compose_board_image_prompt(
    board: Dict[str, Any],
    product_summary: Dict[str, Any],
    global_style: Dict[str, Any],
    campaign_context: Dict[str, Any],
    total_duration_seconds: int,
) -> str:
    brand = _first_text(product_summary, "brand_name")
    product = _first_text(product_summary, "product_name", "product_form")
    consistency = _first_text(product_summary, "consistency_rules")
    style_keywords = global_style.get("keywords_en")
    if isinstance(style_keywords, list):
        style_keywords = ", ".join(str(x).strip() for x in style_keywords if str(x).strip())
    else:
        style_keywords = str(style_keywords or "").strip()

    parts = [
        _first_text(board, "storyboard_image_prompt_en"),
        f"Create one premium Chinese storyboard board image for a {board.get('duration_seconds', FIXED_SEGMENT_DURATION_SECONDS)}-second segment inside a single {total_duration_seconds}-second commercial film",
        f"Brand and product: {brand} {product}".strip(),
        f"Board title in Chinese: {_first_text(board, 'board_title_cn')}",
        f"Board goal in Chinese: {_first_text(board, 'board_goal_cn')}",
        f"Narrative stage in Chinese: {_first_text(board, 'narrative_stage_cn')}",
        f"Hero subject continuity in Chinese: {_first_text(campaign_context, 'hero_subject_cn')}",
        f"Campaign arc in Chinese: {_first_text(campaign_context, 'campaign_arc_cn')}",
        f"Continuity anchor in Chinese: {_first_text(board, 'continuity_anchor_cn')}",
        f"Transition into this segment in Chinese: {_first_text(board, 'transition_in_cn')}",
        f"Transition out of this segment in Chinese: {_first_text(board, 'transition_out_cn')}",
        f"Global continuity rules in Chinese: {_first_text(campaign_context, 'continuity_rules_cn')}",
        f"Chinese sub-shot plan: {_first_text(board, 'subshots_cn')}",
        f"Chinese voice-over script: {_first_text(board, 'voiceover_cn')}",
        f"Chinese supporting copy: {_first_text(board, 'board_copy_cn')}",
        f"Visual focus: {_first_text(board, 'visual_focus_cn')}",
        f"Composition note: {_first_text(board, 'composition_notes_cn')}",
        f"Style keywords: {style_keywords}",
        f"Consistency rules: {consistency}",
        "The board must look like a polished ad-planning sheet with multiple internal panels, scene thumbnails, Chinese shot notes, Chinese copy blocks, and clear time labels",
        "This board must clearly belong to the same campaign film as the other boards, with continuous subject identity, prop logic, motion logic, and visual rhythm",
        "Keep product identity, packaging, logo family, props, and campaign color palette highly consistent with the uploaded references",
        "No random extra brands, no fake UI, no watermark, no irrelevant English slogans",
    ]
    return ". ".join(part for part in parts if part)


def _compose_segment_reference_prompt(
    board: Dict[str, Any],
    product_summary: Dict[str, Any],
    global_style: Dict[str, Any],
    campaign_context: Dict[str, Any],
    total_duration_seconds: int,
) -> str:
    brand = _first_text(product_summary, "brand_name")
    product = _first_text(product_summary, "product_name", "product_form")
    consistency = _first_text(product_summary, "consistency_rules")
    style_keywords = global_style.get("keywords_en")
    if isinstance(style_keywords, list):
        style_keywords = ", ".join(str(x).strip() for x in style_keywords if str(x).strip())
    else:
        style_keywords = str(style_keywords or "").strip()

    parts = [
        f"Create one photoreal premium commercial keyframe as a visual reference image for a {board.get('duration_seconds', FIXED_SEGMENT_DURATION_SECONDS)}-second segment inside a single {total_duration_seconds}-second commercial film",
        f"Brand and product: {brand} {product}".strip(),
        f"Board title in Chinese: {_first_text(board, 'board_title_cn')}",
        f"Narrative stage in Chinese: {_first_text(board, 'narrative_stage_cn')}",
        f"Board goal in Chinese: {_first_text(board, 'board_goal_cn')}",
        f"Hero subject continuity in Chinese: {_first_text(campaign_context, 'hero_subject_cn')}",
        f"Campaign arc in Chinese: {_first_text(campaign_context, 'campaign_arc_cn')}",
        f"Continuity anchor in Chinese: {_first_text(board, 'continuity_anchor_cn')}",
        f"Transition into this segment in Chinese: {_first_text(board, 'transition_in_cn')}",
        f"Transition out of this segment in Chinese: {_first_text(board, 'transition_out_cn')}",
        f"Chinese sub-shot plan: {_first_text(board, 'subshots_cn')}",
        f"Visual focus: {_first_text(board, 'visual_focus_cn')}",
        f"Composition note: {_first_text(board, 'composition_notes_cn')}",
        f"Style keywords: {style_keywords}",
        f"Consistency rules: {consistency}",
        "Return one single-scene cinematic still for motion guidance, not a storyboard board and not a multi-panel layout",
        "Do not include storyboard panels, split screens, time labels, shot callouts, copy blocks, subtitles, poster typography, collage composition, UI overlays, or watermark",
        "Use one single-camera composition with believable product placement, lighting continuity, and room for natural motion development across the whole segment",
        "Keep product identity, packaging, logo family, props, and campaign color palette highly consistent with the uploaded references",
    ]
    return ". ".join(part for part in parts if part)


def _compose_seedance_prompt(
    board: Dict[str, Any],
    product_summary: Dict[str, Any],
    global_style: Dict[str, Any],
    campaign_context: Dict[str, Any],
    total_duration_seconds: int,
) -> str:
    brand = _first_text(product_summary, "brand_name")
    product = _first_text(product_summary, "product_name", "product_form")
    style_keywords = global_style.get("keywords_en")
    if isinstance(style_keywords, list):
        style_keywords = ", ".join(str(x).strip() for x in style_keywords if str(x).strip())
    else:
        style_keywords = str(style_keywords or "").strip()
    parts = [
        f"Create one premium photoreal commercial video segment for {brand} {product}".strip(),
        f"Board title in Chinese: {_first_text(board, 'board_title_cn')}",
        f"Board goal in Chinese: {_first_text(board, 'board_goal_cn')}",
        f"Narrative stage in Chinese: {_first_text(board, 'narrative_stage_cn')}",
        f"Animate the full {int(board.get('duration_seconds') or FIXED_SEGMENT_DURATION_SECONDS)}-second sequence according to the provided segment reference image and the continuity plan",
        f"This segment is one part of a single {total_duration_seconds}-second commercial and must feel continuous with the previous and next segments after merge",
        f"Keep the product identity exactly consistent with the references for {brand} {product}".strip(),
        f"Follow the overall campaign arc in Chinese: {_first_text(campaign_context, 'campaign_arc_cn')}",
        f"Keep the same hero subject in Chinese: {_first_text(campaign_context, 'hero_subject_cn')}",
        f"Respect these continuity rules in Chinese: {_first_text(campaign_context, 'continuity_rules_cn')}",
        f"Transition into this segment in Chinese: {_first_text(board, 'transition_in_cn')}",
        f"Transition out of this segment in Chinese: {_first_text(board, 'transition_out_cn')}",
        f"Continuity anchor in Chinese: {_first_text(board, 'continuity_anchor_cn')}",
        f"Visual focus in Chinese: {_first_text(board, 'visual_focus_cn')}",
        f"Composition notes in Chinese: {_first_text(board, 'composition_notes_cn')}",
        f"Follow this Chinese narration: {_first_text(board, 'voiceover_cn')}",
        f"Treat this Chinese copy as campaign messaging guidance only, not on-screen typography: {_first_text(board, 'board_copy_cn')}",
        f"Maintain premium commercial styling: {style_keywords}",
        "Respect the order of the micro-shots described on the board, with natural transitions inside one coherent commercial segment",
        "Preserve the same subject, lighting direction, color treatment, prop logic, and emotional progression so the merged video feels like one unified ad rather than disconnected clips",
        "Do not render storyboard boards, split panels, time labels, copy blocks, comic layouts, or presentation-sheet compositions",
        "Do not introduce extra products, extra brand marks, unwanted subtitles, UI overlays, or watermarks",
    ]
    return ". ".join(part for part in parts if part)


class ComflySeedanceClient:
    def __init__(self, config: PipelineConfig, logger_obj: RunLogger) -> None:
        self.config = config
        self.logger = logger_obj
        self.base_url = _normalize_api_base_url(config.base_url, "https://ai.comfly.org")
        self.video_channel = _normalize_video_channel(config.video_channel)
        self.video_base_url = _normalize_video_base_url_for_channel(
            self.video_channel,
            config.video_base_url,
            _default_video_base_url(self.video_channel, self.base_url),
        )
        self.video_fallback_channel = _normalize_video_channel(config.video_fallback_channel)
        self.video_fallback_base_url = _normalize_video_base_url_for_channel(
            self.video_fallback_channel,
            config.video_fallback_base_url,
            _default_video_base_url(self.video_fallback_channel, self.base_url),
        )
        self.config.video_base_url = self.video_base_url
        self.config.video_fallback_base_url = self.video_fallback_base_url
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {config.api_key.strip()}", "Accept": "application/json"})

    def _trace_request(self, phase: str, url: str, body: Any) -> None:
        hdrs = {k: ("[REDACTED]" if k.lower() in {"authorization", "x-api-key"} else v) for k, v in self.session.headers.items()}
        body_s = json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else ("null" if body is None else str(body))
        logger.warning(
            "[COMFLY_SEEDANCE_HTTP_DEBUG] phase=%s url=%s headers_json=%s body=%s",
            phase,
            url,
            json.dumps(hdrs, ensure_ascii=False),
            body_s,
        )

    def _check(self, response: requests.Response) -> Dict[str, Any]:
        try:
            payload = response.json()
        except Exception:
            payload = {"raw_text": response.text}
        if response.status_code != 200:
            raise PipelineError(f"HTTP {response.status_code}: {payload}")
        if not isinstance(payload, dict):
            raise PipelineError(f"Invalid payload: {payload}")
        return payload

    def upload(self, src: str) -> tuple[str, int]:
        if src.startswith(("http://", "https://")):
            return src, 0
        path = Path(src)
        if not path.exists():
            raise PipelineError(f"Reference image file not found: {src}")

        def call() -> str:
            up_url = f"{self.base_url}/v1/files"
            self._trace_request("upload_file", up_url, {"file": path.name, "multipart": True})
            with path.open("rb") as f:
                r = self.session.post(up_url, files={"file": (path.name, f, "application/octet-stream")}, timeout=120)
            payload = self._check(r)
            url = payload.get("url")
            if not isinstance(url, str) or not url:
                raise PipelineError(f"Upload returned no url: {payload}")
            return url

        return _retry("upload", self.config.upload_retries, self.config.network_retry_delay_seconds, self.logger, call)

    def analyze(self, image_urls: List[str]) -> tuple[Dict[str, Any], int]:
        prompt = _analysis_prompt(self.config)
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
        body = {
            "model": self.config.analysis_model,
            "stream": False,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 5000,
        }

        def call() -> Dict[str, Any]:
            chat_url = f"{self.base_url}/v1/chat/completions"
            self._trace_request("chat_completions", chat_url, body)
            r = self.session.post(chat_url, headers={"Content-Type": "application/json"}, json=body, timeout=180)
            payload = self._check(r)
            text = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = _parse_json(text)
            parsed["_raw_text"] = text
            return parsed

        return _retry("analyze", self.config.analysis_retries, self.config.network_retry_delay_seconds, self.logger, call)

    def generate_board_image(self, prompt: str, refs: List[str], action: str) -> tuple[Dict[str, Any], int]:
        primary = (self.config.image_model or "").strip()
        fallback = (self.config.image_model_fallback or "").strip()
        if fallback.lower() in {"none", "null", "off", "false", "0", "disabled", "disable", "no"}:
            fallback = ""
        models: List[str] = [primary] if primary else []
        if fallback and fallback != primary:
            models.append(fallback)
        if not models:
            raise PipelineError("No image model configured")

        last_exc: Optional[Exception] = None
        total_attempts = 0
        for model_idx, model_id in enumerate(models):
            body: Dict[str, Any] = {
                "model": model_id,
                "prompt": prompt,
                "aspect_ratio": _normalize_aspect_ratio(self.config.aspect_ratio),
                "response_format": "url",
            }
            if refs:
                body["image"] = refs
            tag = action if model_idx == 0 else f"{action}_fallback_{model_id}"

            def call(_body=body) -> Dict[str, Any]:
                img_url = f"{self.base_url}/v1/images/generations"
                self._trace_request("images_generations", img_url, _body)
                r = self.session.post(img_url, headers={"Content-Type": "application/json"}, json=_body, timeout=300)
                payload = self._check(r)
                data = payload.get("data", [])
                if not isinstance(data, list) or not data:
                    raise PipelineError(f"Image generation returned no data: {payload}")
                url = data[0].get("url")
                if not isinstance(url, str) or not url:
                    raise PipelineError(f"Image generation returned no url: {payload}")
                return {"url": url, "revised_prompt": data[0].get("revised_prompt"), "raw": payload, "request": _body, "model": model_id}

            try:
                result, attempts = _retry(tag, self.config.image_generation_retries, self.config.network_retry_delay_seconds, self.logger, call)
                total_attempts += attempts
                if model_idx > 0:
                    result["fallback_used"] = True
                    result["primary_model_failure"] = str(last_exc) if last_exc else None
                return result, total_attempts
            except Exception as exc:
                total_attempts += self.config.image_generation_retries
                last_exc = exc
                if model_idx + 1 < len(models):
                    self.logger.error(action, f"primary model {model_id} exhausted, falling back to {models[model_idx + 1]}: {exc}")
                    continue
        raise PipelineError(f"{action} failed on image model {models[-1] if models else primary}: {last_exc}")

    def submit_seedance_video(
        self,
        prompt: str,
        segment_reference_url: str,
        reference_urls: List[str],
        duration_seconds: int,
        action: str,
        *,
        channel: str = "",
        model: str = "",
        base_url: str = "",
    ) -> tuple[Dict[str, Any], int]:
        video_channel = _normalize_video_channel(channel or self.video_channel)
        video_model = (model or self.config.video_model or _default_video_model(video_channel)).strip() or _default_video_model(video_channel)
        video_base_url = _normalize_video_base_url_for_channel(video_channel, base_url or "", _default_video_base_url(video_channel, self.base_url))
        if video_channel == "openmind":
            images = [segment_reference_url] if segment_reference_url else []
            is_grok = _is_grok_video_model(video_model)
            body = {
                "model": video_model,
                "prompt": prompt,
                "images": images,
                "seconds": str(int(duration_seconds)) if is_grok else int(duration_seconds),
                "duration": str(int(duration_seconds)) if is_grok else int(duration_seconds),
                "aspect_ratio": _normalize_aspect_ratio(self.config.aspect_ratio),
                "resolution": "720p",
            }
            body["size"] = "720x1280" if body["aspect_ratio"] == "9:16" else "1280x720"
            if images:
                body["image"] = images[0]
                body["image_url"] = images[0]
            if self.config.seed >= 0:
                body["seed"] = int(self.config.seed)

            def call_openmind() -> Dict[str, Any]:
                vid_url = f"{video_base_url}/openmind/v1/videos"
                self._trace_request("openmind_video_submit", vid_url, body)
                r = self.session.post(vid_url, headers={"Content-Type": "application/json"}, json=body, timeout=180)
                payload = self._check(r)
                data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                task_id = str(
                    payload.get("id")
                    or payload.get("task_id")
                    or payload.get("video_id")
                    or data.get("id")
                    or data.get("task_id")
                    or data.get("video_id")
                    or ""
                ).strip()
                if not task_id:
                    raise PipelineError(f"OpenMind submit returned no task id: {payload}")
                payload["_request"] = body
                payload["task_id"] = task_id
                payload["video_channel"] = "openmind"
                payload["video_base_url"] = video_base_url
                payload["video_model"] = video_model
                return payload

            return _retry(action, 1, self.config.network_retry_delay_seconds, self.logger, call_openmind)

        if video_channel == "yunwu":
            images = [segment_reference_url] if segment_reference_url else []
            body = {
                "enable_upsample": True,
                "enhance_prompt": False,
                "images": images,
                "model": video_model,
                "prompt": prompt,
                "aspect_ratio": _normalize_aspect_ratio(self.config.aspect_ratio),
            }
            if _is_grok_video_model(video_model):
                body["duration"] = int(duration_seconds)
                body["seconds"] = str(int(duration_seconds))

            def call_yunwu() -> Dict[str, Any]:
                vid_url = f"{video_base_url}/v1/video/create"
                self._trace_request("yunwu_video_submit", vid_url, body)
                r = self.session.post(vid_url, headers={"Content-Type": "application/json"}, json=body, timeout=120)
                payload = self._check(r)
                data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                task_id = str(payload.get("id") or payload.get("task_id") or data.get("id") or data.get("task_id") or "").strip()
                if not task_id:
                    raise PipelineError(f"Yunwu submit returned no task id: {payload}")
                payload["_request"] = body
                payload["task_id"] = task_id
                payload["video_channel"] = "yunwu"
                payload["video_base_url"] = video_base_url
                payload["video_model"] = video_model
                return payload

            return _retry(action, self.config.video_submit_retries, self.config.network_retry_delay_seconds, self.logger, call_yunwu)

        if video_channel == "comfly":
            images = [segment_reference_url] if segment_reference_url else []
            body = {
                "prompt": prompt,
                "model": video_model,
                "images": images,
                "watermark": bool(self.config.watermark),
                "aspect_ratio": _normalize_aspect_ratio(self.config.aspect_ratio),
            }
            if _is_grok_video_model(video_model):
                body["duration"] = int(duration_seconds)
                body["seconds"] = str(int(duration_seconds))

            def call_comfly_veo() -> Dict[str, Any]:
                vid_url = f"{video_base_url}/v2/videos/generations"
                self._trace_request("comfly_video_submit", vid_url, body)
                r = self.session.post(vid_url, headers={"Content-Type": "application/json"}, json=body, timeout=120)
                payload = self._check(r)
                task_id = str(payload.get("id") or payload.get("task_id") or payload.get("video_id") or "").strip()
                data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                task_id = task_id or str(data.get("id") or data.get("task_id") or "").strip()
                if not task_id:
                    raise PipelineError(f"Comfly Veo submit returned no task id: {payload}")
                payload["_request"] = body
                payload["task_id"] = task_id
                payload["video_channel"] = "comfly"
                payload["video_base_url"] = video_base_url
                payload["video_model"] = video_model
                return payload

            return _retry(action, self.config.video_submit_retries, self.config.network_retry_delay_seconds, self.logger, call_comfly_veo)

        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        if segment_reference_url:
            content.append({"type": "image_url", "image_url": {"url": segment_reference_url}, "role": "reference_image"})
        for ref_url in reference_urls:
            if ref_url and ref_url != segment_reference_url:
                content.append({"type": "image_url", "image_url": {"url": ref_url}, "role": "reference_image"})
        body: Dict[str, Any] = {
            "model": _normalize_seedance_model(video_model),
            "content": content,
            "ratio": _normalize_aspect_ratio(self.config.aspect_ratio),
            "duration": _normalize_seedance_duration(duration_seconds),
            "generate_audio": bool(self.config.generate_audio),
            "watermark": bool(self.config.watermark),
        }
        if self.config.seed >= 0:
            body["seed"] = int(self.config.seed)

        def call() -> Dict[str, Any]:
            vid_url = f"{self.base_url}/seedance/v3/contents/generations/tasks"
            self._trace_request("seedance_submit", vid_url, body)
            r = self.session.post(vid_url, headers={"Content-Type": "application/json"}, json=body, timeout=120)
            payload = self._check(r)
            task_id = payload.get("id") or payload.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                raise PipelineError(f"Seedance submit returned no task id: {payload}")
            payload["_request"] = body
            return payload

        return _retry(action, self.config.video_submit_retries, self.config.network_retry_delay_seconds, self.logger, call)

    def poll_seedance_video(
        self,
        task_id: str,
        *,
        channel: str = "",
        base_url: str = "",
        on_progress: Optional[Callable[[List[Dict[str, Any]], Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        video_channel = _normalize_video_channel(channel or self.video_channel)
        video_base_url = _normalize_video_base_url_for_channel(video_channel, base_url or "", _default_video_base_url(video_channel, self.base_url))
        history: List[Dict[str, Any]] = []
        for attempt in range(1, self.config.max_polls + 1):
            def call() -> Dict[str, Any]:
                if video_channel == "openmind":
                    poll_url = f"{video_base_url}/openmind/v1/videos/{task_id}"
                    phase = "openmind_video_poll"
                elif video_channel == "yunwu":
                    poll_url = f"{video_base_url}/v1/video/query?{urlencode({'id': task_id})}"
                    phase = "yunwu_video_poll"
                elif video_channel == "comfly":
                    poll_url = f"{video_base_url}/v2/videos/generations/{task_id}"
                    phase = "comfly_video_poll"
                else:
                    poll_url = f"{self.base_url}/seedance/v3/contents/generations/tasks/{task_id}"
                    phase = "seedance_poll"
                self._trace_request(phase, poll_url, None)
                r = self.session.get(poll_url, timeout=60)
                return self._check(r)

            try:
                payload, request_attempts = _retry(f"poll_{task_id}", 3, self.config.network_retry_delay_seconds, self.logger, call)
            except Exception as exc:
                if _is_transient_video_poll_error(exc, video_channel):
                    history_item = {
                        "attempt": attempt,
                        "request_attempts": 3,
                        "status": "poll_retry",
                        "video_url": "",
                        "error": str(exc),
                    }
                    history.append(history_item)
                    if on_progress is not None:
                        try:
                            on_progress(history, {"status": "poll_retry", "error": str(exc), "task_id": task_id})
                        except Exception:
                            logger.warning("video poll progress callback failed", exc_info=True)
                    logger.warning(
                        "Transient %s video poll error for task %s on attempt %s/%s: %s",
                        video_channel,
                        task_id,
                        attempt,
                        self.config.max_polls,
                        exc,
                    )
                    time.sleep(self.config.poll_interval_seconds)
                    continue
                raise
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            status = str(
                payload.get("status")
                or payload.get("state")
                or payload.get("task_status")
                or data.get("status")
                or data.get("state")
                or data.get("task_status")
                or result.get("status")
                or ""
            ).strip().lower()
            content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
            video_obj = payload.get("video") if isinstance(payload.get("video"), dict) else {}
            video_url = str(
                (content.get("video_url") if isinstance(content, dict) else "")
                or data.get("video_url")
                or data.get("output")
                or data.get("url")
                or data.get("mp4url")
                or data.get("mp4_url")
                or result.get("video_url")
                or result.get("output")
                or result.get("url")
                or video_obj.get("url")
                or payload.get("video_url")
                or payload.get("output")
                or payload.get("url")
                or payload.get("mp4url")
                or ""
            ).strip()
            outputs = data.get("outputs") if isinstance(data, dict) else None
            if not video_url and isinstance(outputs, list):
                for item in outputs:
                    if isinstance(item, str) and item.strip():
                        video_url = item.strip()
                        break
                    if isinstance(item, dict):
                        video_url = str(item.get("url") or item.get("video_url") or item.get("output") or "").strip()
                        if video_url:
                            break
            upstream_progress = _extract_progress_percent(payload)
            history_item = {
                "attempt": attempt,
                "request_attempts": request_attempts,
                "status": status,
                "video_url": video_url,
            }
            if upstream_progress is not None:
                history_item["progress"] = upstream_progress
            history.append(history_item)
            if on_progress is not None:
                try:
                    on_progress(history, payload)
                except Exception:
                    logger.warning("video poll progress callback failed", exc_info=True)
            if status in {"succeeded", "success", "completed", "complete", "done"} and video_url:
                return {"task_id": task_id, "status": status, "mp4url": video_url, "raw": payload, "history": history}
            if status in {"failed", "failure", "error", "cancelled", "canceled", "expired"}:
                err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                raise PipelineError(f"Seedance task failed: {err or payload}")
            time.sleep(self.config.poll_interval_seconds)
        raise PipelineError(f"Seedance task timed out: {task_id}")


def _build_segment_plan(
    config: PipelineConfig,
    board: Dict[str, Any],
    product_summary: Dict[str, Any],
    global_style: Dict[str, Any],
    campaign_context: Dict[str, Any],
) -> Dict[str, Any]:
    index = int(board.get("index", 0))
    duration_seconds = config.segment_duration_seconds
    board_image_prompt = _compose_board_image_prompt(
        board,
        product_summary,
        global_style,
        campaign_context,
        config.total_duration_seconds,
    )
    segment_reference_prompt = _compose_segment_reference_prompt(
        board,
        product_summary,
        global_style,
        campaign_context,
        config.total_duration_seconds,
    )
    video_prompt = _compose_seedance_prompt(
        board,
        product_summary,
        global_style,
        campaign_context,
        config.total_duration_seconds,
    )
    return {
        "index": index,
        "board": board,
        "duration_seconds": duration_seconds,
        "board_image_prompt": board_image_prompt,
        "segment_reference_prompt": segment_reference_prompt,
        "video_prompt": video_prompt,
    }


def _generate_segment_image(
    client: ComflySeedanceClient,
    logger_obj: RunLogger,
    segment_plan: Dict[str, Any],
    reference_image_urls: List[str],
) -> Dict[str, Any]:
    index = int(segment_plan["index"])
    board_image_result, board_image_attempts = client.generate_board_image(
        segment_plan["board_image_prompt"],
        reference_image_urls,
        f"segment_{index:02d}_board_image",
    )
    logger_obj.segment(index, "board_image", "success", attempts=board_image_attempts, payload=board_image_result)
    segment_reference_result, segment_reference_attempts = client.generate_board_image(
        segment_plan["segment_reference_prompt"],
        reference_image_urls,
        f"segment_{index:02d}_segment_reference_image",
    )
    logger_obj.segment(
        index,
        "segment_reference_image",
        "success",
        attempts=segment_reference_attempts,
        payload=segment_reference_result,
    )
    out = dict(segment_plan)
    out["board_image_result"] = board_image_result
    out["segment_reference_result"] = segment_reference_result
    return out


def _submit_segment_video(
    client: ComflySeedanceClient,
    logger_obj: RunLogger,
    segment_plan: Dict[str, Any],
    reference_image_urls: List[str],
    submit_control: Optional[SubmitControl] = None,
) -> Dict[str, Any]:
    index = int(segment_plan["index"])
    segment_reference_result = segment_plan["segment_reference_result"]
    providers = _ordered_video_providers(client)

    if submit_control is not None and submit_control.should_stop_new_submits():
        reason = submit_control.current_stop_reason() or "余额不足，已停止新的分镜提交"
        raise StopNewSubmissionsError(reason)

    last_error = ""
    for provider_index, provider in enumerate(providers, start=1):
        if submit_control is not None and submit_control.should_stop_new_submits():
            reason = submit_control.current_stop_reason() or "余额不足，已停止新的分镜提交"
            raise StopNewSubmissionsError(reason)
        provider_role = provider["role"]
        provider_channel = _normalize_video_channel(provider["channel"])
        provider_model = provider["model"]
        provider_base_url = provider["base_url"]
        if provider_role == "fallback":
            logger_obj.segment(
                index,
                "video_fallback",
                "running",
                payload={
                    "from_channel": providers[0]["channel"],
                    "from_model": providers[0]["model"],
                    "to_channel": provider_channel,
                    "to_model": provider_model,
                    "previous_error": last_error,
                },
            )
        try:
            submit_result, submit_attempts = client.submit_seedance_video(
                segment_plan["video_prompt"],
                segment_reference_result["url"],
                reference_image_urls,
                int(segment_plan["duration_seconds"]),
                f"segment_{index:02d}_submit_{provider_role}",
                channel=provider_channel,
                model=provider_model,
                base_url=provider_base_url,
            )
            logger_obj.segment(index, f"submit_{provider_role}", "success", attempts=submit_attempts, payload=submit_result)
            out = dict(segment_plan)
            out["submit_result"] = submit_result
            out["video_task_id"] = str(submit_result.get("id") or submit_result.get("task_id") or "").strip()
            out["video_channel"] = provider_channel
            out["video_base_url"] = provider_base_url
            out["video_model"] = provider_model
            out["video_provider_role"] = provider_role
            out["video_provider_attempt"] = provider_index
            if submit_control is not None:
                submit_control.note_submit_success()
            return out
        except Exception as exc:
            last_error = str(exc)
            logger_obj.segment(
                index,
                f"submit_{provider_role}",
                "failed",
                error=last_error,
                payload={"video_channel": provider_channel, "video_model": provider_model, "provider_role": provider_role},
            )
            if submit_control is not None and _is_insufficient_credit_error(exc):
                if submit_control.has_any_successful_submit():
                    reason = (
                        "余额不足，已停止新的分镜提交；已成功提交的分镜会继续合成。"
                    )
                    submit_control.mark_stop_new_submits(reason)
                    raise StopNewSubmissionsError(reason) from exc
                reason = "余额不足，未成功提交任何分镜，请先充值后重试。"
                submit_control.mark_stop_new_submits(reason)
                raise InsufficientCreditError(reason) from exc
    raise PipelineError(f"segment {index:02d} video submit failed: {last_error}")


def _segment_poll_progress_from_history(history: List[Dict[str, Any]], max_polls: int) -> int:
    if not history:
        return 0
    latest = history[-1]
    upstream = latest.get("progress") if isinstance(latest, dict) else None
    if isinstance(upstream, (int, float)):
        return max(1, min(99, int(round(float(upstream)))))
    attempt = int((latest or {}).get("attempt") or len(history) or 1)
    total = max(1, int(max_polls or 1))
    return max(1, min(95, int(round((attempt / total) * 95))))


def _poll_segment_video(
    client: ComflySeedanceClient,
    logger_obj: RunLogger,
    segment_plan: Dict[str, Any],
) -> Dict[str, Any]:
    index = int(segment_plan["index"])
    logger_obj.segment(
        index,
        "poll",
        "running",
        payload={
            "task_id": segment_plan["video_task_id"],
            "video_channel": segment_plan.get("video_channel") or client.video_channel,
            "video_model": segment_plan.get("video_model") or client.config.video_model,
            "progress": 0,
            "message": "视频任务已提交，正在等待生成结果",
        },
    )

    def on_poll_progress(history: List[Dict[str, Any]], payload: Dict[str, Any]) -> None:
        latest = history[-1] if history else {}
        logger_obj.segment(
            index,
            "poll",
            "running",
            attempts=len(history),
            payload={
                "task_id": segment_plan["video_task_id"],
                "status": latest.get("status") or "",
                "progress": _segment_poll_progress_from_history(history, client.config.max_polls),
                "history": history[-8:],
            },
        )

    poll_result = client.poll_seedance_video(
        segment_plan["video_task_id"],
        channel=str(segment_plan.get("video_channel") or ""),
        base_url=str(segment_plan.get("video_base_url") or ""),
        on_progress=on_poll_progress,
    )
    logger_obj.segment(index, "poll", "success", attempts=len(poll_result.get("history", [])), payload=poll_result)
    board = segment_plan["board"]
    board_image_result = segment_plan.get("board_image_result") or {}
    segment_reference_result = segment_plan["segment_reference_result"]
    return {
        "index": index,
        "time_range_cn": board.get("time_range_cn"),
        "board_title_cn": board.get("board_title_cn"),
        "board_goal_cn": board.get("board_goal_cn"),
        "narrative_stage_cn": board.get("narrative_stage_cn"),
        "continuity_anchor_cn": board.get("continuity_anchor_cn"),
        "transition_in_cn": board.get("transition_in_cn"),
        "transition_out_cn": board.get("transition_out_cn"),
        "subshots_cn": board.get("subshots_cn"),
        "voiceover_cn": board.get("voiceover_cn"),
        "board_copy_cn": board.get("board_copy_cn"),
        "visual_focus_cn": board.get("visual_focus_cn"),
        "composition_notes_cn": board.get("composition_notes_cn"),
        "duration_seconds": int(segment_plan["duration_seconds"]),
        "storyboard_image_prompt_en": board.get("storyboard_image_prompt_en"),
        "seedance_prompt_en": board.get("seedance_prompt_en"),
        "segment_reference_prompt_en": segment_plan["segment_reference_prompt"],
        "first_frame_prompt_en": segment_plan["segment_reference_prompt"],
        "submitted_video_prompt_en": segment_plan["video_prompt"],
        "storyboard_board_image_url": board_image_result.get("url") or segment_reference_result["url"],
        "storyboard_image_revised_prompt": board_image_result.get("revised_prompt"),
        "segment_reference_image_url": segment_reference_result["url"],
        "segment_reference_revised_prompt": segment_reference_result.get("revised_prompt"),
        "first_frame_image_url": segment_reference_result["url"],
        "first_frame_revised_prompt": segment_reference_result.get("revised_prompt"),
        "video_submission_mode": "reference_images_only",
        "video_task_id": segment_plan["video_task_id"],
        "video_status": poll_result["status"],
        "mp4url": poll_result["mp4url"],
        "video_raw": poll_result["raw"],
        "video_channel": segment_plan.get("video_channel") or client.video_channel,
        "video_model": segment_plan.get("video_model") or client.config.video_model,
        "video_provider_role": segment_plan.get("video_provider_role") or "primary",
        "workflow_mode": segment_plan.get("workflow_mode") or client.config.workflow_mode,
    }


def _run_single_segment_pipeline(
    client: ComflySeedanceClient,
    logger_obj: RunLogger,
    segment_plan: Dict[str, Any],
    reference_image_urls: List[str],
    submit_control: Optional[SubmitControl] = None,
) -> Dict[str, Any]:
    image_ready_plan = _generate_segment_image(
        client,
        logger_obj,
        segment_plan,
        reference_image_urls,
    )
    submitted_plan = _submit_segment_video(
        client,
        logger_obj,
        image_ready_plan,
        reference_image_urls,
        submit_control,
    )
    return _poll_segment_video(
        client,
        logger_obj,
        submitted_plan,
    )


def _direct_video_prompt(config: PipelineConfig) -> str:
    prompt = (config.task_text or "").strip()
    if prompt:
        return prompt
    return "基于上传参考图生成一段自然、连贯、适合短视频平台发布的图生视频。"


def _build_direct_segment_plan(config: PipelineConfig, reference_image_urls: List[str]) -> Dict[str, Any]:
    if not reference_image_urls:
        raise PipelineError("direct_video requires at least one reference image")
    video_prompt = _direct_video_prompt(config)
    board = {
        "index": 1,
        "time_range_cn": f"0-{config.segment_duration_seconds}秒",
        "board_title_cn": "直接图生视频",
        "board_goal_cn": "使用用户上传图片和提示词直接生成视频",
        "narrative_stage_cn": "direct_video",
        "visual_focus_cn": "用户上传参考图",
        "seedance_prompt_en": video_prompt,
        "storyboard_image_prompt_en": "",
    }
    return {
        "index": 1,
        "board": board,
        "duration_seconds": config.segment_duration_seconds,
        "board_image_prompt": "",
        "segment_reference_prompt": video_prompt,
        "video_prompt": video_prompt,
        "segment_reference_result": {
            "url": reference_image_urls[0],
            "source": "uploaded_reference_image",
        },
        "board_image_result": {
            "url": reference_image_urls[0],
            "source": "uploaded_reference_image",
        },
        "workflow_mode": "direct_video",
    }


def _should_use_direct_video(config: PipelineConfig, reference_image_urls: List[str]) -> bool:
    mode = (config.workflow_mode or "").strip().lower().replace("-", "_")
    if mode not in {"direct", "direct_video", "image_to_video", "i2v"}:
        return False
    return bool(reference_image_urls) and config.segment_count == 1


def _finish_segments(
    config: PipelineConfig,
    logger_obj: RunLogger,
    reference_image_urls: List[str],
    results_map: Dict[int, Dict[str, Any]],
    failure_map: Dict[int, Dict[str, Any]],
    *,
    product_summary: Optional[Dict[str, Any]] = None,
    global_style: Optional[Dict[str, Any]] = None,
    campaign_context: Optional[Dict[str, Any]] = None,
    boards: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    results = [results_map[idx] for idx in sorted(results_map.keys())]
    errors = [failure_map[idx] for idx in sorted(failure_map.keys())]
    merge_result: Optional[Dict[str, Any]] = None
    if results and config.merge_clips:
        try:
            merge_result = _merge_completed_segments(config, logger_obj, results)
            logger_obj.step("90_merge_clips", "success", attempts=1, payload=merge_result)
        except Exception as exc:
            merge_result = {"status": "failed", "error": str(exc)}
            logger_obj.step("90_merge_clips", "failed", attempts=1, error=str(exc), payload=merge_result)

    final_video = _final_video_deliverable(config.merge_clips, merge_result, results)
    output = {
        "run_dir": str(logger_obj.run_dir),
        "final_video": final_video,
        "reference_image_urls": reference_image_urls,
        "product_summary": product_summary or {},
        "global_style": global_style or {},
        "campaign_context": campaign_context or {},
        "storyboard_boards": boards or [],
        "completed_segments": results,
        "completed_shots": results,
        "failed_segments": errors,
        "failed_shots": errors,
        "merge_result": merge_result,
        "workflow_mode": config.workflow_mode,
        "config": {
            "analysis_model": config.analysis_model,
            "image_model": config.image_model,
            "image_model_fallback": config.image_model_fallback,
            "video_model": config.video_model,
            "video_channel": config.video_channel,
            "video_base_url": config.video_base_url,
            "video_fallback_model": config.video_fallback_model,
            "video_fallback_channel": config.video_fallback_channel,
            "video_fallback_base_url": config.video_fallback_base_url,
            "workflow_mode": config.workflow_mode,
            "aspect_ratio": config.aspect_ratio,
            "segment_count": config.segment_count,
            "segment_duration_seconds": config.segment_duration_seconds,
            "total_duration_seconds": config.total_duration_seconds,
            "shot_concurrency": config.shot_concurrency,
            "generate_audio": config.generate_audio,
            "watermark": config.watermark,
        },
    }
    logger_obj.finish("partial_failure" if errors else "success", output)
    return output


def _final_video_deliverable(merge_clips: bool, merge_result: Optional[Dict[str, Any]], segments: List[Dict[str, Any]]) -> Dict[str, Any]:
    if merge_clips and isinstance(merge_result, dict) and merge_result.get("status") == "success":
        return {
            "path": merge_result.get("merged_video_path"),
            "url": None,
            "kind": "merged_local",
            "hint": "Merged local video completed.",
        }
    if segments:
        return {
            "path": None,
            "url": segments[0].get("mp4url"),
            "kind": "single_clip_remote" if len(segments) == 1 else "multi_clip_remote",
            "hint": "Single clip completed." if len(segments) == 1 else "Multiple remote clips completed without local merge.",
        }
    return {"path": None, "url": None, "kind": "no_video", "hint": "No successful clip generated."}


def _build_config(data: Input) -> PipelineConfig:
    api_key = (data.get("apikey") or "").strip()
    if not api_key:
        raise PipelineError("Missing apikey")
    raw_video_model = str(data.get("video_model") or "").strip()
    model_hint = raw_video_model.lower().replace(" ", "")
    inferred_channel = "openmind" if _is_grok_video_model(raw_video_model) else ("yunwu" if model_hint in {"yunwu-veo3.1-plus", "veo3.1-plus", "veo3.1"} else "seedance")
    video_channel = _normalize_video_channel(str(data.get("video_channel") or data.get("channel") or inferred_channel))
    if model_hint in {"yunwu-veo3.1-plus", "veo3.1-plus", "veo3.1"}:
        raw_video_model = "veo3.1"
    video_model_default = _default_video_model(video_channel)
    effective_video_model = (raw_video_model or video_model_default).strip() or video_model_default
    segment_seconds = _segment_seconds_for_video(video_channel, effective_video_model)
    allowed_totals = _allowed_total_durations_for_video(video_channel, effective_video_model)
    requested_segment_count = data.get("segment_count", data.get("storyboard_count"))
    if data.get("total_duration_seconds") is None and requested_segment_count is not None:
        raw_total = int(requested_segment_count) * segment_seconds
    else:
        raw_total = int(data.get("total_duration_seconds", 20))
    if raw_total not in allowed_totals:
        raise PipelineError(f"total_duration_seconds must be one of {list(allowed_totals)}")
    raw_segment_duration = int(data.get("segment_duration_seconds", segment_seconds))
    if raw_segment_duration != segment_seconds:
        raise PipelineError(f"segment_duration_seconds must be exactly {segment_seconds}")
    segment_count = raw_total // segment_seconds
    if requested_segment_count is not None and int(requested_segment_count) != segment_count:
        raise PipelineError(
            f"segment_count/storyboard_count must match total_duration_seconds / {segment_seconds}"
        )
    base_url = (data.get("base_url") or "https://ai.comfly.org").rstrip("/")
    video_base_default = _default_video_base_url(video_channel, base_url)
    fallback_channel = _normalize_video_channel(str(data.get("video_fallback_channel") or data.get("fallback_video_channel") or "comfly"))
    fallback_base_default = _default_video_base_url(fallback_channel, base_url)
    fallback_model_default = _default_video_model(fallback_channel)
    return PipelineConfig(
        base_url=base_url,
        api_key=api_key,
        task_text=(data.get("task_text") or "").strip(),
        platform=(data.get("platform") or "brand_tvc").strip() or "brand_tvc",
        country=(data.get("country") or "China").strip() or "China",
        language=(data.get("language") or "zh-CN").strip() or "zh-CN",
        analysis_model=(data.get("analysis_model") or "gpt-4.1-mini").strip() or "gpt-4.1-mini",
        image_model=_optional_model(data.get("image_model")) or "gpt-image-2",
        image_model_fallback=_optional_model(data.get("image_model_fallback")),
        video_model=effective_video_model,
        video_channel=video_channel,
        video_base_url=_normalize_video_base_url_for_channel(video_channel, str(data.get("video_base_url") or ""), video_base_default),
        video_fallback_model=(str(data.get("video_fallback_model") or data.get("fallback_video_model") or fallback_model_default).strip() or fallback_model_default),
        video_fallback_channel=fallback_channel,
        video_fallback_base_url=_normalize_video_base_url_for_channel(fallback_channel, str(data.get("video_fallback_base_url") or data.get("fallback_video_base_url") or ""), fallback_base_default),
        video_fallbacks=list(data.get("video_fallbacks") or data.get("fallback_video_providers") or []),
        workflow_mode=(str(data.get("workflow_mode") or "storyboard").strip().lower().replace("-", "_") or "storyboard"),
        aspect_ratio=_normalize_aspect_ratio(str(data.get("aspect_ratio") or "9:16"), "9:16"),
        segment_count=segment_count,
        segment_duration_seconds=segment_seconds,
        total_duration_seconds=raw_total,
        shot_concurrency=max(1, int(data.get("shot_concurrency", 2))),
        poll_interval_seconds=max(5, int(data.get("poll_interval_seconds", 10))),
        max_polls=max(10, int(data.get("max_polls", 90))),
        output_dir=(data.get("output_dir") or "").strip(),
        upload_retries=max(1, int(data.get("upload_retries", 3))),
        analysis_retries=max(1, int(data.get("analysis_retries", 2))),
        image_generation_retries=max(1, int(data.get("image_generation_retries", 3))),
        video_submit_retries=max(1, int(data.get("video_submit_retries", 2))),
        network_retry_delay_seconds=max(1, int(data.get("network_retry_delay_seconds", 3))),
        merge_clips=True,
        ffmpeg_path=(data.get("ffmpeg_path") or "ffmpeg").strip() or "ffmpeg",
        clip_download_retries=max(1, int(data.get("clip_download_retries", 2))),
        clip_download_timeout_seconds=max(30, int(data.get("clip_download_timeout_seconds", 180))),
        generate_audio=bool(data.get("generate_audio", True)),
        watermark=bool(data.get("watermark", False)),
        seed=int(data.get("seed", -1)),
    )


def run_pipeline(data: Input) -> Dict[str, Any]:
    config = _build_config(data)
    output_dir = config.output_dir or str(Path(__file__).resolve().parent.parent / "runs")
    logger_obj = RunLogger(output_dir, config, data)
    try:
        raw_refs = [str(x).strip() for x in (data.get("reference_images") or []) if str(x).strip()]
        primary = (data.get("reference_image") or "").strip()
        if primary and primary not in raw_refs:
            raw_refs.insert(0, primary)

        client = ComflySeedanceClient(config, logger_obj)
        reference_image_urls: List[str] = []
        for idx, ref in enumerate(raw_refs, start=1):
            ref_url, upload_attempts = client.upload(ref)
            reference_image_urls.append(ref_url)
            logger_obj.step(
                f"01_reference_upload_{idx:02d}",
                "success",
                attempts=upload_attempts,
                payload={"reference_image": ref, "reference_image_url": ref_url},
            )

        if _should_use_direct_video(config, reference_image_urls):
            segment_plan = _build_direct_segment_plan(config, reference_image_urls)
            logger_obj.step(
                "02_direct_video_plan",
                "success",
                attempts=0,
                payload={
                    "workflow_mode": "direct_video",
                    "reference_image_url": reference_image_urls[0],
                    "submitted_video_prompt": segment_plan["video_prompt"],
                    "segment_duration_seconds": segment_plan["duration_seconds"],
                },
            )
            logger_obj.segment(
                1,
                "plan",
                "ready",
                payload={
                    "board": segment_plan["board"],
                    "first_frame_image_url": reference_image_urls[0],
                    "submitted_video_prompt": segment_plan["video_prompt"],
                    "workflow_mode": "direct_video",
                },
            )
            results_map: Dict[int, Dict[str, Any]] = {}
            failure_map: Dict[int, Dict[str, Any]] = {}
            try:
                submitted = _submit_segment_video(client, logger_obj, segment_plan, reference_image_urls)
                result = _poll_segment_video(client, logger_obj, submitted)
                results_map[1] = result
                logger_obj.segment(1, "final", "success", payload=result)
            except Exception as exc:
                payload = {"index": 1, "error": str(exc), "traceback": traceback.format_exc()}
                failure_map[1] = payload
                logger_obj.segment(1, "final", "failed", error=str(exc), payload=payload)
            return _finish_segments(
                config,
                logger_obj,
                reference_image_urls,
                results_map,
                failure_map,
                boards=[segment_plan["board"]],
                product_summary={"mode": "direct_video"},
                campaign_context={"task_text": config.task_text},
            )

        storyboard_plan, analysis_attempts = client.analyze(reference_image_urls)
        _coerce_plan(storyboard_plan, config)
        logger_obj.step("02_storyboard_plan", "success", attempts=analysis_attempts, payload=storyboard_plan)

        product_summary = storyboard_plan.get("product_summary", {})
        global_style = storyboard_plan.get("global_style", {})
        campaign_context = storyboard_plan.get("campaign_context", {})
        boards = storyboard_plan.get("storyboard_boards", [])
        if not isinstance(boards, list) or not boards:
            raise PipelineError(f"Invalid storyboard board plan: {storyboard_plan}")

        pending = [sb for sb in boards[: config.segment_count] if isinstance(sb, dict)]
        segment_plans = [
            _build_segment_plan(
                config,
                sb,
                product_summary if isinstance(product_summary, dict) else {},
                global_style if isinstance(global_style, dict) else {},
                campaign_context if isinstance(campaign_context, dict) else {},
            )
            for sb in pending
        ]
        for segment_plan in segment_plans:
            logger_obj.segment(
                int(segment_plan["index"]),
                "plan",
                "ready",
                payload={
                    "board": segment_plan["board"],
                    "storyboard_board_image_prompt": segment_plan["board_image_prompt"],
                    "segment_reference_prompt": segment_plan["segment_reference_prompt"],
                    "first_frame_prompt": segment_plan["segment_reference_prompt"],
                    "submitted_video_prompt": segment_plan["video_prompt"],
                },
            )

        results_map: Dict[int, Dict[str, Any]] = {}
        failure_map: Dict[int, Dict[str, Any]] = {}
        submit_control = SubmitControl()
        with concurrent.futures.ThreadPoolExecutor(max_workers=config.shot_concurrency) as ex:
            futures = {
                ex.submit(
                    _run_single_segment_pipeline,
                    client,
                    logger_obj,
                    segment_plan,
                    reference_image_urls,
                    submit_control,
                ): segment_plan
                for segment_plan in segment_plans
            }
            for future in concurrent.futures.as_completed(futures):
                segment_plan = futures[future]
                idx = int(segment_plan["index"])
                try:
                    result = future.result()
                    results_map[idx] = result
                    logger_obj.segment(idx, "final", "success", payload=result)
                except StopNewSubmissionsError as exc:
                    payload = {
                        "index": idx,
                        "error": str(exc),
                        "reason": "stop_new_submits",
                    }
                    failure_map[idx] = payload
                    logger_obj.segment(idx, "final", "failed", error=str(exc), payload=payload)
                except InsufficientCreditError as exc:
                    payload = {
                        "index": idx,
                        "error": str(exc),
                        "reason": "insufficient_credit",
                    }
                    failure_map[idx] = payload
                    logger_obj.segment(idx, "final", "failed", error=str(exc), payload=payload)
                except Exception as exc:
                    payload = {"index": idx, "error": str(exc), "traceback": traceback.format_exc()}
                    failure_map[idx] = payload
                    logger_obj.segment(idx, "final", "failed", error=str(exc), payload=payload)

        return _finish_segments(
            config,
            logger_obj,
            reference_image_urls,
            results_map,
            failure_map,
            product_summary=product_summary if isinstance(product_summary, dict) else {},
            global_style=global_style if isinstance(global_style, dict) else {},
            campaign_context=campaign_context if isinstance(campaign_context, dict) else {},
            boards=boards if isinstance(boards, list) else [],
        )
    except Exception:
        logger_obj.finish("failed", {"error": traceback.format_exc()})
        raise


def handler(args: Args) -> Dict[str, Any]:
    try:
        result = run_pipeline(args.input)
        return {
            "code": 206 if result.get("failed_segments") else 200,
            "msg": "Pipeline finished" if result.get("failed_segments") else "Pipeline completed successfully",
            "data": result,
        }
    except Exception as exc:
        return {"code": -500, "msg": f"Pipeline failed: {exc}", "data": None}


def _write_json_stdout(payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    stdout = getattr(sys, "stdout", None)
    if stdout is not None and hasattr(stdout, "reconfigure"):
        try:
            stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))


if __name__ == "__main__":
    raw = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    _write_json_stdout(handler(Args(raw)))
