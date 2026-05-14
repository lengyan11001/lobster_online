from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Generic, List, Optional, TypeVar, TypedDict

import requests

logger = logging.getLogger(__name__)

try:
    from runtime import Args  # type: ignore
except ImportError:
    T = TypeVar("T")

    class Args(Generic[T]):  # type: ignore[override]
        def __init__(self, input: T):
            self.input = input


class Input(TypedDict, total=False):
    product_image: str
    apikey: str
    base_url: str
    task_text: str
    platform: str
    country: str
    language: str
    target_market: str
    analysis_model: str
    image_model: str
    video_model: str
    aspect_ratio: str
    enhance_prompt: bool
    watermark: bool
    storyboard_count: int
    shot_concurrency: int
    poll_interval_seconds: int
    max_polls: int
    output_dir: str
    upload_retries: int
    analysis_retries: int
    image_generation_retries: int
    video_submit_retries: int
    video_generation_retries: int
    network_retry_delay_seconds: int
    merge_clips: bool
    ffmpeg_path: str
    clip_download_retries: int
    clip_download_timeout_seconds: int
    shot_refill_retries: int
    image_request_style: str
    image_size: str
    generation_time_limit_seconds: int


class Output(TypedDict):
    code: int
    msg: str
    data: Dict[str, Any] | None


@dataclass
class PipelineConfig:
    base_url: str
    api_key: str
    task_text: str = ""
    platform: str = "douyin"
    country: str = ""
    language: str = ""
    target_market: str = ""
    analysis_model: str = "gemini-2.5-pro"
    # SKILL 默认 nano-banana-2；图生 body 仅对该模型附加 image_size（默认 1K）
    image_model: str = "nano-banana-2"
    image_size: str = "1K"
    video_model: str = "veo3.1-fast"
    aspect_ratio: str = "9:16"
    enhance_prompt: bool = False
    watermark: bool = False
    storyboard_count: int = 6
    shot_concurrency: int = 5
    poll_interval_seconds: int = 12
    max_polls: int = 50
    output_dir: str = ""
    upload_retries: int = 3
    analysis_retries: int = 2
    image_generation_retries: int = 3
    video_submit_retries: int = 2
    video_generation_retries: int = 2
    network_retry_delay_seconds: int = 3
    merge_clips: bool = True
    ffmpeg_path: str = "ffmpeg"
    clip_download_retries: int = 2
    clip_download_timeout_seconds: int = 180
    shot_refill_retries: int = 2
    # 与 Comfly /v1/images/generations（Banana-2 Pro）对齐：无 OpenAI 的 n 字段；image_size 仅 banana-2
    generation_time_limit_seconds: int = 1200
    image_request_style: str = "comfly"


class PipelineError(RuntimeError):
    pass


def _ensure_before_deadline(deadline_monotonic: float | None, label: str = "generation") -> None:
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        raise PipelineError(f"{label} deadline reached")


def _seconds_until_deadline(deadline_monotonic: float | None) -> float | None:
    if deadline_monotonic is None:
        return None
    return max(0.0, deadline_monotonic - time.monotonic())


MODEL_UNIT_COSTS: Dict[str, int] = {
    "analysis": 1,
    "image": 2,
    "video": 2,
}


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
                "platform": config.platform,
                "country": config.country,
                "language": config.language,
                "analysis_model": config.analysis_model,
                "image_model": config.image_model,
                "video_model": config.video_model,
                "aspect_ratio": config.aspect_ratio,
                "enhance_prompt": config.enhance_prompt,
                "watermark": config.watermark,
                "storyboard_count": config.storyboard_count,
                "shot_concurrency": config.shot_concurrency,
                "upload_retries": config.upload_retries,
                "analysis_retries": config.analysis_retries,
                "image_generation_retries": config.image_generation_retries,
                "video_submit_retries": config.video_submit_retries,
                "video_generation_retries": config.video_generation_retries,
                "merge_clips": config.merge_clips,
                "ffmpeg_path": config.ffmpeg_path,
                "clip_download_retries": config.clip_download_retries,
                "clip_download_timeout_seconds": config.clip_download_timeout_seconds,
                "shot_refill_retries": config.shot_refill_retries,
                "generation_time_limit_seconds": config.generation_time_limit_seconds,
            },
            "input": {k: v for k, v in raw_input.items() if k != "apikey"},
            "usage": {
                "unit_costs": dict(MODEL_UNIT_COSTS),
                "summary": {
                    "analysis_count": 0,
                    "image_count": 0,
                    "video_count": 0,
                    "total_points": 0,
                    "total_units": 0,
                },
                "breakdown": {
                    "analysis": {},
                    "image": {},
                    "video": {},
                },
                "records": [],
            },
            "steps": {},
            "shots": {},
            "errors": [],
        }
        self.write_json("00_input.json", self.manifest["input"])
        self._save()

    def write_json(self, filename: str, payload: Any) -> None:
        with (self.run_dir / filename).open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def step(self, name: str, status: str, attempts: int = 0, payload: Any = None, error: str | None = None) -> None:
        with self.lock:
            self.manifest["steps"][name] = {"status": status, "attempts": attempts, "error": error, "updated_at": datetime.now().isoformat()}
            self._save()
        if payload is not None:
            self.write_json(f"{name}.json", payload)

    def shot(self, index: int, stage: str, status: str, attempts: int = 0, payload: Any = None, error: str | None = None) -> None:
        key = str(index)
        with self.lock:
            self.manifest["shots"].setdefault(key, {})[stage] = {"status": status, "attempts": attempts, "error": error, "updated_at": datetime.now().isoformat()}
            self._save()
        if payload is not None:
            self.write_json(f"shot_{index:02d}_{stage}.json", payload)

    def error(self, where: str, message: str) -> None:
        with self.lock:
            self.manifest["errors"].append({"where": where, "message": message, "ts": datetime.now().isoformat()})
            self._save()

    def record_usage(self, kind: str, model: str, context: str, units: int | None = None, payload: Any = None) -> None:
        applied_units = MODEL_UNIT_COSTS.get(kind, 0) if units is None else units
        ts = datetime.now().isoformat()
        with self.lock:
            usage = self.manifest["usage"]
            summary = usage["summary"]
            breakdown = usage["breakdown"]
            summary[f"{kind}_count"] += 1
            summary["total_points"] += applied_units
            summary["total_units"] += applied_units
            model_bucket = breakdown.setdefault(kind, {}).setdefault(model, {"count": 0, "points": 0, "units": 0})
            model_bucket["count"] += 1
            model_bucket["points"] += applied_units
            model_bucket["units"] += applied_units
            usage["records"].append(
                {
                    "kind": kind,
                    "model": model,
                    "context": context,
                    "points": applied_units,
                    "units": applied_units,
                    "ts": ts,
                    "payload": payload,
                }
            )
            self._save()

    def usage_snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.manifest["usage"], ensure_ascii=False))

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


def _text_is_non_retryable(text: str) -> bool:
    keys = ["http 400", "http 401", "http 403", "http 404", "missing apikey", "missing product_image", "image file not found", "unable to parse json"]
    return any(k in text for k in keys)


def _non_retryable(exc: Exception) -> bool:
    return _text_is_non_retryable(str(exc).lower())


def _should_rerun_failed_shot(error_message: str) -> bool:
    return not _text_is_non_retryable(error_message.lower())


def _retry(action: str, attempts: int, delay: int, logger: RunLogger, fn: Callable[[], Any]) -> tuple[Any, int]:
    last: Optional[Exception] = None
    for i in range(1, attempts + 1):
        try:
            return fn(), i
        except Exception as exc:
            last = exc
            logger.error(action, f"attempt {i} failed: {exc}")
            if i >= attempts or _non_retryable(exc):
                break
            time.sleep(delay * i)
    raise PipelineError(f"{action} failed after {attempts} attempt(s): {last}")


def _normalize_comfly_api_key(raw: str) -> str:
    """技能商店里若误填「Bearer sk-…」，避免 Authorization 变成 Bearer Bearer … 导致上游 403。"""
    k = (raw or "").strip()
    while k.lower().startswith("bearer "):
        k = k[7:].strip()
    return k


def _comfly_model_supports_image_size_field(model: str) -> bool:
    """Comfly 文档：image_size 仅 nano-banana-2；勿对 nano-banana / Flash 等误传。"""
    m = (model or "").strip().lower()
    return m == "nano-banana-2" or m.startswith("nano-banana-2-")


# Comfly 图生 / Veo 提交允许的 aspect_ratio（与上游校验一致）；默认与 SKILL「Ecommerce defaults → output ratio 9:16」一致。
_COMFLY_ASPECT_RATIO_CANONICAL: Dict[str, str] = {
    "1:1": "1:1",
    "1:4": "1:4",
    "1:8": "1:8",
    "2:3": "2:3",
    "3:2": "3:2",
    "3:4": "3:4",
    "4:1": "4:1",
    "4:3": "4:3",
    "4:5": "4:5",
    "5:4": "5:4",
    "8:1": "8:1",
    "9:16": "9:16",
    "16:9": "16:9",
    "21:9": "21:9",
}


def _normalize_aspect_ratio_for_comfly(raw: str, *, default: str = "9:16") -> str:
    """将任意配置收敛为上游允许的 aspect_ratio 字符串；无法识别时回退为技能默认竖屏 9:16。"""
    if default not in _COMFLY_ASPECT_RATIO_CANONICAL:
        default = "9:16"
    s = (raw or "").strip().replace(" ", "").lower()
    if not s:
        return default
    if s in _COMFLY_ASPECT_RATIO_CANONICAL:
        return _COMFLY_ASPECT_RATIO_CANONICAL[s]
    aliases = {
        "portrait": "9:16",
        "vertical": "9:16",
        "landscape": "16:9",
        "horizontal": "16:9",
        "square": "1:1",
        "douyin": "9:16",
        "tiktok": "9:16",
    }
    if s in aliases:
        return aliases[s]
    # 误传 OpenAI 风格 size → 按技能竖屏默认
    if "x" in s and s.replace("x", "").isdigit():
        try:
            w, _, h = s.partition("x")
            wi, hi = int(w), int(h)
            if hi > wi:
                return "9:16"
            if wi > hi:
                return "16:9"
            return "1:1"
        except ValueError:
            pass
    return default


class ComflyClient:
    def __init__(self, config: PipelineConfig, logger: RunLogger) -> None:
        self.config = config
        self.logger = logger
        self.base_url = config.base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {_normalize_comfly_api_key(config.api_key)}", "Accept": "application/json"}
        )
        self._trace_request("session_init", self.base_url, None, None)

    def _effective_headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        h = {str(k): str(v) for k, v in self.session.headers.items()}
        if extra:
            h.update({str(k): str(v) for k, v in extra.items()})
        return h

    def _redact_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        redacted: Dict[str, str] = {}
        for key, value in headers.items():
            lowered = key.lower()
            if lowered in {"authorization", "proxy-authorization", "x-api-key"}:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = value
        return redacted

    def _trace_request(self, phase: str, url: str, extra_headers: Optional[Dict[str, str]], body: Any) -> None:
        """打印调试请求信息，但敏感认证头必须脱敏。"""
        hdrs = self._redact_headers(self._effective_headers(extra_headers))
        if isinstance(body, dict):
            body_s = json.dumps(body, ensure_ascii=False)
        elif body is None:
            body_s = "null"
        else:
            body_s = str(body)[:8000]
        logger.warning(
            "[COMFLY_PIPE_HTTP_DEBUG] phase=%s url=%s headers_json=%s body=%s",
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
        if src.startswith("http://") or src.startswith("https://"):
            return src, 0
        path = Path(src)
        if not path.exists():
            raise PipelineError(f"Image file not found: {src}")

        def call() -> str:
            up_url = f"{self.base_url}/v1/files"
            self._trace_request("upload_file", up_url, None, {"file": path.name, "multipart": True})
            with path.open("rb") as f:
                r = self.session.post(up_url, files={"file": (path.name, f, "application/octet-stream")}, timeout=120)
            payload = self._check(r)
            url = payload.get("url")
            if not isinstance(url, str) or not url:
                raise PipelineError(f"Upload returned no url: {payload}")
            return url

        return _retry("upload", self.config.upload_retries, self.config.network_retry_delay_seconds, self.logger, call)

    def analyze(self, model: str, prompt: str, image_urls: List[str]) -> tuple[Dict[str, Any], int]:
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
        body = {"model": model, "stream": False, "messages": [{"role": "user", "content": content}], "max_tokens": 4000}

        def call() -> Dict[str, Any]:
            chat_url = f"{self.base_url}/v1/chat/completions"
            ex = {"Content-Type": "application/json"}
            self._trace_request("chat_completions", chat_url, ex, body)
            r = self.session.post(chat_url, headers=ex, json=body, timeout=180)
            payload = self._check(r)
            text = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = self._parse_json(text)
            parsed["_raw_text"] = text
            return parsed

        return _retry("analyze", self.config.analysis_retries, self.config.network_retry_delay_seconds, self.logger, call)

    def generate_image(self, model: str, prompt: str, aspect_ratio: str, refs: List[str], action: str) -> tuple[Dict[str, Any], int]:
        ar = _normalize_aspect_ratio_for_comfly(aspect_ratio)
        # Comfly 文档：POST /v1/images/generations — model、prompt 必需；aspect_ratio、response_format、image[] 可选；勿传文档未列的 n（OpenAI 字段）
        body: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": ar,
            "response_format": "url",
        }
        if refs:
            body["image"] = refs
        isz = (self.config.image_size or "").strip().upper()
        if isz in ("1K", "2K", "4K") and _comfly_model_supports_image_size_field(model):
            body["image_size"] = isz

        def call() -> Dict[str, Any]:
            img_url = f"{self.base_url}/v1/images/generations"
            ex = {"Content-Type": "application/json"}
            self._trace_request("images_generations", img_url, ex, body)
            r = self.session.post(img_url, headers=ex, json=body, timeout=180)
            payload = self._check(r)
            data = payload.get("data", [])
            if not isinstance(data, list) or not data:
                raise PipelineError(f"Image generation returned no data: {payload}")
            url = data[0].get("url")
            if not isinstance(url, str) or not url:
                raise PipelineError(f"Image generation returned no url: {payload}")
            return {"url": url, "revised_prompt": data[0].get("revised_prompt"), "raw": payload, "request": body}

        return _retry(action, self.config.image_generation_retries, self.config.network_retry_delay_seconds, self.logger, call)

    def submit_video(self, prompt: str, model: str, images: List[str], aspect_ratio: str, enhance_prompt: bool, watermark: bool, action: str) -> tuple[Dict[str, Any], int]:
        ar = _normalize_aspect_ratio_for_comfly(aspect_ratio)
        body: Dict[str, Any] = {"prompt": prompt, "model": model, "images": images, "aspect_ratio": ar, "watermark": bool(watermark)}
        if enhance_prompt:
            body["enhance_prompt"] = True

        def call() -> Dict[str, Any]:
            vid_url = f"{self.base_url}/v2/videos/generations"
            ex = {"Content-Type": "application/json"}
            self._trace_request("videos_generations_submit", vid_url, ex, body)
            r = self.session.post(vid_url, headers=ex, json=body, timeout=120)
            payload = self._check(r)
            task_id = payload.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                raise PipelineError(f"Video submit returned no task_id: {payload}")
            payload["_request"] = body
            return payload

        return _retry(action, self.config.video_submit_retries, self.config.network_retry_delay_seconds, self.logger, call)

    def poll_video(self, task_id: str, poll_interval_seconds: int, max_polls: int, deadline_monotonic: float | None = None) -> Dict[str, Any]:
        history: List[Dict[str, Any]] = []
        for attempt in range(1, max_polls + 1):
            _ensure_before_deadline(deadline_monotonic, "video polling")
            def call() -> Dict[str, Any]:
                poll_url = f"{self.base_url}/v2/videos/generations/{task_id}"
                self._trace_request("videos_generations_poll", poll_url, None, None)
                r = self.session.get(poll_url, timeout=60)
                return self._check(r)

            payload, request_attempts = _retry(f"poll_{task_id}", 3, self.config.network_retry_delay_seconds, self.logger, call)
            status = payload.get("status", "")
            progress = payload.get("progress", "")
            fail_reason = payload.get("fail_reason", "")
            mp4url = payload.get("data", {}).get("output", "") if isinstance(payload.get("data"), dict) else ""
            history.append({"attempt": attempt, "request_attempts": request_attempts, "status": status, "progress": progress, "fail_reason": fail_reason, "mp4url": mp4url})
            if status == "SUCCESS" and mp4url:
                return {"task_id": task_id, "status": status, "progress": progress, "mp4url": mp4url, "raw": payload, "history": history}
            if status == "FAILURE":
                raise PipelineError(f"Video task failed: {fail_reason or payload}")
            remaining = _seconds_until_deadline(deadline_monotonic)
            if remaining is not None and remaining <= 0:
                raise PipelineError("video polling deadline reached")
            sleep_seconds = min(float(poll_interval_seconds), remaining) if remaining is not None else float(poll_interval_seconds)
            time.sleep(max(0.1, sleep_seconds))
        raise PipelineError(f"Video task timed out: {task_id}")

    def _parse_json(self, text: str) -> Dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            raise PipelineError("Model returned empty text")
        stripped = text.strip()
        candidates = [stripped]
        fs = stripped.find("```json")
        if fs >= 0:
            s = fs + len("```json")
            e = stripped.find("```", s)
            if e > s:
                candidates.insert(0, stripped[s:e].strip())
        ls = stripped.find("[")
        le = stripped.rfind("]")
        if ls >= 0 and le > ls:
            candidates.append(stripped[ls : le + 1])
        os_ = stripped.find("{")
        oe = stripped.rfind("}")
        if os_ >= 0 and oe > os_:
            candidates.append(stripped[os_ : oe + 1])
        for c in candidates:
            try:
                parsed = json.loads(c)
                if isinstance(parsed, list):
                    return {"storyboards": parsed}
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        raise PipelineError(f"Unable to parse JSON from model output: {text[:500]}")


def _build_config(data: Input) -> PipelineConfig:
    api_key = _normalize_comfly_api_key(data.get("apikey") or os.getenv("COMFLY_API_KEY", ""))
    if not api_key:
        raise PipelineError("Missing apikey")
    locale_inputs = _resolve_locale_inputs(data)
    raw_irs = (data.get("image_request_style") or "").strip().lower()
    if raw_irs in ("openai_images", "openai", "oai"):
        irs = "openai_images"
    elif raw_irs in ("comfly", "legacy"):
        irs = "comfly"
    else:
        irs = "comfly"
    _raw_imsz = str(data.get("image_size") or "1K").strip().upper()
    _imsz = _raw_imsz if _raw_imsz in ("1K", "2K", "4K") else "1K"
    return PipelineConfig(
        base_url=data.get("base_url", os.getenv("COMFLY_API_BASE", "https://ai.comfly.chat")),
        api_key=api_key,
        task_text=locale_inputs["task_text"],
        platform=locale_inputs["platform"],
        country=locale_inputs["country"],
        language=locale_inputs["language"],
        target_market=data.get("target_market", ""),
        analysis_model=data.get("analysis_model", "gemini-2.5-pro"),
        image_model=data.get("image_model", "nano-banana-2"),
        image_size=_imsz,
        video_model=data.get("video_model", "veo3.1-fast"),
        aspect_ratio=_normalize_aspect_ratio_for_comfly(str(data.get("aspect_ratio") or "9:16")),
        enhance_prompt=bool(data.get("enhance_prompt", False)),
        watermark=bool(data.get("watermark", False)),
        storyboard_count=int(data.get("storyboard_count", 6)),
        shot_concurrency=max(1, int(data.get("shot_concurrency", data.get("storyboard_count", 6)))),
        poll_interval_seconds=int(data.get("poll_interval_seconds", 12)),
        max_polls=int(data.get("max_polls", 50)),
        output_dir=data.get("output_dir", ""),
        upload_retries=int(data.get("upload_retries", 3)),
        analysis_retries=int(data.get("analysis_retries", 2)),
        image_generation_retries=int(data.get("image_generation_retries", 3)),
        video_submit_retries=int(data.get("video_submit_retries", 2)),
        video_generation_retries=int(data.get("video_generation_retries", 2)),
        network_retry_delay_seconds=int(data.get("network_retry_delay_seconds", 3)),
        merge_clips=bool(data.get("merge_clips", True)),
        ffmpeg_path=data.get("ffmpeg_path", "ffmpeg"),
        clip_download_retries=int(data.get("clip_download_retries", 2)),
        clip_download_timeout_seconds=int(data.get("clip_download_timeout_seconds", 180)),
        shot_refill_retries=int(data.get("shot_refill_retries", 2)),
        generation_time_limit_seconds=max(60, int(data.get("generation_time_limit_seconds", 1200))),
        image_request_style=irs,
    )


def _normalize_platform(platform: str) -> str:
    value = (platform or "").strip().lower()
    if value in {"tk", "tiktok", "tik tok"}:
        return "tiktok"
    if value in {"dy", "douyin", "抖音"}:
        return "douyin"
    if value in {"xiaohongshu", "xhs", "小红书"}:
        return "xiaohongshu"
    if value in {"kuaishou", "快手"}:
        return "kuaishou"
    if value in {"instagram", "ig"}:
        return "instagram"
    return value or "douyin"


_COUNTRY_ALIASES: Dict[str, str] = {
    "china": "China",
    "中国": "China",
    "中国大陆": "China",
    "大陆": "China",
    "mainland china": "China",
    "united states": "United States",
    "usa": "United States",
    "us": "United States",
    "america": "United States",
    "美国": "United States",
    "美区": "United States",
    "united kingdom": "United Kingdom",
    "uk": "United Kingdom",
    "britain": "United Kingdom",
    "england": "United Kingdom",
    "英国": "United Kingdom",
    "英区": "United Kingdom",
    "japan": "Japan",
    "日本": "Japan",
    "日区": "Japan",
    "south korea": "South Korea",
    "korea": "South Korea",
    "韩国": "South Korea",
    "韩区": "South Korea",
    "france": "France",
    "法国": "France",
    "germany": "Germany",
    "德国": "Germany",
    "spain": "Spain",
    "西班牙": "Spain",
    "mexico": "Mexico",
    "墨西哥": "Mexico",
    "brazil": "Brazil",
    "巴西": "Brazil",
    "russia": "Russia",
    "俄罗斯": "Russia",
    "thailand": "Thailand",
    "泰国": "Thailand",
    "indonesia": "Indonesia",
    "印尼": "Indonesia",
    "印度尼西亚": "Indonesia",
    "malaysia": "Malaysia",
    "马来西亚": "Malaysia",
    "vietnam": "Vietnam",
    "越南": "Vietnam",
}

_LANGUAGE_METADATA: Dict[str, Dict[str, str]] = {
    "simplified chinese": {"language_name": "Simplified Chinese", "language_code": "zh-CN"},
    "chinese": {"language_name": "Simplified Chinese", "language_code": "zh-CN"},
    "zh": {"language_name": "Simplified Chinese", "language_code": "zh-CN"},
    "zh-cn": {"language_name": "Simplified Chinese", "language_code": "zh-CN"},
    "中文": {"language_name": "Simplified Chinese", "language_code": "zh-CN"},
    "汉语": {"language_name": "Simplified Chinese", "language_code": "zh-CN"},
    "普通话": {"language_name": "Simplified Chinese", "language_code": "zh-CN"},
    "简体中文": {"language_name": "Simplified Chinese", "language_code": "zh-CN"},
    "english": {"language_name": "English", "language_code": "en-US"},
    "en": {"language_name": "English", "language_code": "en-US"},
    "en-us": {"language_name": "English", "language_code": "en-US"},
    "en-gb": {"language_name": "English", "language_code": "en-GB"},
    "英文": {"language_name": "English", "language_code": "en-US"},
    "英语": {"language_name": "English", "language_code": "en-US"},
    "japanese": {"language_name": "Japanese", "language_code": "ja-JP"},
    "ja": {"language_name": "Japanese", "language_code": "ja-JP"},
    "ja-jp": {"language_name": "Japanese", "language_code": "ja-JP"},
    "日语": {"language_name": "Japanese", "language_code": "ja-JP"},
    "日本语": {"language_name": "Japanese", "language_code": "ja-JP"},
    "korean": {"language_name": "Korean", "language_code": "ko-KR"},
    "ko": {"language_name": "Korean", "language_code": "ko-KR"},
    "ko-kr": {"language_name": "Korean", "language_code": "ko-KR"},
    "韩语": {"language_name": "Korean", "language_code": "ko-KR"},
    "french": {"language_name": "French", "language_code": "fr-FR"},
    "fr": {"language_name": "French", "language_code": "fr-FR"},
    "fr-fr": {"language_name": "French", "language_code": "fr-FR"},
    "法语": {"language_name": "French", "language_code": "fr-FR"},
    "german": {"language_name": "German", "language_code": "de-DE"},
    "de": {"language_name": "German", "language_code": "de-DE"},
    "de-de": {"language_name": "German", "language_code": "de-DE"},
    "德语": {"language_name": "German", "language_code": "de-DE"},
    "spanish": {"language_name": "Spanish", "language_code": "es-ES"},
    "es": {"language_name": "Spanish", "language_code": "es-ES"},
    "es-es": {"language_name": "Spanish", "language_code": "es-ES"},
    "es-mx": {"language_name": "Spanish", "language_code": "es-MX"},
    "西班牙语": {"language_name": "Spanish", "language_code": "es-ES"},
    "portuguese": {"language_name": "Portuguese", "language_code": "pt-BR"},
    "pt": {"language_name": "Portuguese", "language_code": "pt-BR"},
    "pt-br": {"language_name": "Portuguese", "language_code": "pt-BR"},
    "葡萄牙语": {"language_name": "Portuguese", "language_code": "pt-BR"},
    "russian": {"language_name": "Russian", "language_code": "ru-RU"},
    "ru": {"language_name": "Russian", "language_code": "ru-RU"},
    "ru-ru": {"language_name": "Russian", "language_code": "ru-RU"},
    "俄语": {"language_name": "Russian", "language_code": "ru-RU"},
    "thai": {"language_name": "Thai", "language_code": "th-TH"},
    "th": {"language_name": "Thai", "language_code": "th-TH"},
    "th-th": {"language_name": "Thai", "language_code": "th-TH"},
    "泰语": {"language_name": "Thai", "language_code": "th-TH"},
    "indonesian": {"language_name": "Indonesian", "language_code": "id-ID"},
    "id": {"language_name": "Indonesian", "language_code": "id-ID"},
    "id-id": {"language_name": "Indonesian", "language_code": "id-ID"},
    "印尼语": {"language_name": "Indonesian", "language_code": "id-ID"},
    "bahasa indonesia": {"language_name": "Indonesian", "language_code": "id-ID"},
    "malay": {"language_name": "Malay", "language_code": "ms-MY"},
    "ms": {"language_name": "Malay", "language_code": "ms-MY"},
    "ms-my": {"language_name": "Malay", "language_code": "ms-MY"},
    "马来语": {"language_name": "Malay", "language_code": "ms-MY"},
    "vietnamese": {"language_name": "Vietnamese", "language_code": "vi-VN"},
    "vi": {"language_name": "Vietnamese", "language_code": "vi-VN"},
    "vi-vn": {"language_name": "Vietnamese", "language_code": "vi-VN"},
    "越南语": {"language_name": "Vietnamese", "language_code": "vi-VN"},
}

_LANGUAGE_TO_COUNTRY: Dict[str, str] = {
    "zh-cn": "China",
    "en": "United States",
    "en-us": "United States",
    "en-gb": "United Kingdom",
    "ja-jp": "Japan",
    "ko-kr": "South Korea",
    "fr-fr": "France",
    "de-de": "Germany",
    "es-es": "Spain",
    "es-mx": "Mexico",
    "pt-br": "Brazil",
    "ru-ru": "Russia",
    "th-th": "Thailand",
    "id-id": "Indonesia",
    "ms-my": "Malaysia",
    "vi-vn": "Vietnam",
}

_COUNTRY_TASK_HINTS: List[tuple[tuple[str, ...], str]] = [
    (("中国", "中国大陆", "国内", "本土", "国货"), "China"),
    (("美国", "美区", "美国站", "united states", "usa", "american"), "United States"),
    (("英国", "英区", "united kingdom", "uk", "british"), "United Kingdom"),
    (("日本", "日区", "japan", "japanese market"), "Japan"),
    (("韩国", "韩区", "south korea", "korea", "korean market"), "South Korea"),
    (("法国", "france", "french market"), "France"),
    (("德国", "germany", "german market"), "Germany"),
    (("西班牙", "spain"), "Spain"),
    (("墨西哥", "mexico"), "Mexico"),
    (("巴西", "brazil"), "Brazil"),
    (("俄罗斯", "russia"), "Russia"),
    (("泰国", "thailand"), "Thailand"),
    (("印尼", "印度尼西亚", "indonesia"), "Indonesia"),
    (("马来西亚", "malaysia"), "Malaysia"),
    (("越南", "vietnam"), "Vietnam"),
]

_LANGUAGE_TASK_HINTS: List[tuple[tuple[str, ...], str]] = [
    (("中文", "汉语", "普通话", "简体中文", "chinese", "mandarin"), "zh-CN"),
    (("英文", "英语", "english"), "en-US"),
    (("日语", "japanese", "日本语"), "ja-JP"),
    (("韩语", "korean"), "ko-KR"),
    (("法语", "french"), "fr-FR"),
    (("德语", "german"), "de-DE"),
    (("西班牙语", "spanish"), "es-ES"),
    (("葡萄牙语", "portuguese"), "pt-BR"),
    (("俄语", "russian"), "ru-RU"),
    (("泰语", "thai"), "th-TH"),
    (("印尼语", "bahasa indonesia", "indonesian"), "id-ID"),
    (("马来语", "malay"), "ms-MY"),
    (("越南语", "vietnamese"), "vi-VN"),
]

_UI_MENTION_REPLACEMENTS: List[tuple[re.Pattern[str], str]] = [
    (re.compile(r"左下角小黄车"), "现在就去下单"),
    (re.compile(r"小黄车"), "现在就去下单"),
    (re.compile(r"购物车图标?"), "立即下单"),
    (re.compile(r"购物车"), "立即下单"),
    (re.compile(r"点(?:击|开)?左下角"), "现在就下单"),
    (re.compile(r"点击下方链接"), "现在就下单"),
    (re.compile(r"点击链接"), "现在就下单"),
    (re.compile(r"下方链接"), "立即下单"),
    (re.compile(r"左下角"), ""),
    (re.compile(r"shop(?:ping)? cart icon", re.IGNORECASE), "order now"),
    (re.compile(r"cart icon", re.IGNORECASE), "order now"),
    (re.compile(r"shop now button", re.IGNORECASE), "order now"),
    (re.compile(r"button overlay", re.IGNORECASE), ""),
    (re.compile(r"click the link below", re.IGNORECASE), "order now"),
    (re.compile(r"tap the link below", re.IGNORECASE), "order now"),
    (re.compile(r"link below", re.IGNORECASE), "order now"),
    (re.compile(r"lower-left", re.IGNORECASE), ""),
]


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _text_contains_any(text: str, needles: tuple[str, ...]) -> bool:
    raw = text or ""
    lower = raw.casefold()
    for needle in needles:
        if _contains_cjk(needle):
            if needle in raw:
                return True
        else:
            if needle.casefold() in lower:
                return True
    return False


def _normalize_country_name(country: str) -> str:
    raw = (country or "").strip()
    if not raw:
        return ""
    return _COUNTRY_ALIASES.get(raw.casefold(), raw)


def _language_meta(language: str) -> Dict[str, str]:
    raw = (language or "").strip()
    if not raw:
        return {}
    meta = _LANGUAGE_METADATA.get(raw.casefold())
    if meta:
        return dict(meta)
    return {"language_name": raw, "language_code": raw}


def _infer_locale_from_task_text(task_text: str) -> Dict[str, str]:
    text = (task_text or "").strip()
    if not text:
        return {"platform": "", "country": "", "language": ""}

    platform = ""
    if _text_contains_any(text, ("tiktok", "tik tok", "tk带货", "tk shop", "tiktok shop", "跨境", "出海", "海外")):
        platform = "tiktok"
    elif _text_contains_any(text, ("抖音", "douyin", "dy")):
        platform = "douyin"
    elif _text_contains_any(text, ("小红书", "xiaohongshu", "xhs")):
        platform = "xiaohongshu"
    elif _text_contains_any(text, ("快手", "kuaishou")):
        platform = "kuaishou"
    elif _text_contains_any(text, ("instagram", "ig")):
        platform = "instagram"

    country = ""
    for needles, canonical in _COUNTRY_TASK_HINTS:
        if _text_contains_any(text, needles):
            country = canonical
            break

    language = ""
    for needles, language_code in _LANGUAGE_TASK_HINTS:
        if _text_contains_any(text, needles):
            language = language_code
            break

    cross_border = _text_contains_any(text, ("跨境", "出海", "海外", "tk带货", "tiktok", "tik tok", "tiktok shop"))
    if cross_border and not platform:
        platform = "tiktok"
    if cross_border and not language:
        language = "en-US"
    if not country and language:
        country = _LANGUAGE_TO_COUNTRY.get(language.casefold(), "")
    if not country and not cross_border and _contains_cjk(text):
        country = "China"
    if not language and country == "China":
        language = "zh-CN"

    return {"platform": platform, "country": country, "language": language}


def _resolve_locale_inputs(data: Input) -> Dict[str, str]:
    task_text = str(data.get("task_text", "") or "").strip()
    inferred = _infer_locale_from_task_text(task_text)
    platform = str(data.get("platform", "") or "").strip() or inferred["platform"] or "douyin"
    country = str(data.get("country", "") or "").strip() or inferred["country"]
    language = str(data.get("language", "") or "").strip() or inferred["language"]
    return {
        "task_text": task_text,
        "platform": platform,
        "country": country,
        "language": language,
    }


def _sanitize_ui_mentions(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned
    for pattern, replacement in _UI_MENTION_REPLACEMENTS:
        cleaned = pattern.sub(replacement, cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"([，。！？,.!?:;；])\1+", r"\1", cleaned)
    cleaned = re.sub(r"[，,]\s*[，,]", "，", cleaned)
    return cleaned.strip(" \t\r\n,，")


def _sanitize_storyboard_fields(storyboard: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = dict(storyboard)
    for key in (
        "title_cn",
        "goal_cn",
        "scene_cn",
        "hook_line_cn",
        "selling_point_cn",
        "cta_cn",
        "storyboard_image_prompt_en",
        "video_prompt_en",
    ):
        value = sanitized.get(key)
        if isinstance(value, str) and value.strip():
            sanitized[key] = _sanitize_ui_mentions(value)
    return sanitized


def _locale_defaults(platform: str, country: str, language: str, target_market: str) -> Dict[str, str]:
    normalized_platform = _normalize_platform(platform)
    normalized_country = _normalize_country_name(country)
    normalized_language = (language or "").strip()
    country_key = normalized_country.lower()

    country_language_map: Dict[str, Dict[str, str]] = {
        "united states": {"language_name": "English", "language_code": "en-US", "market": "United States ecommerce shoppers", "character_hint": "Use a contemporary US-market creator look and English-speaking persona."},
        "usa": {"language_name": "English", "language_code": "en-US", "market": "United States ecommerce shoppers", "character_hint": "Use a contemporary US-market creator look and English-speaking persona."},
        "uk": {"language_name": "English", "language_code": "en-GB", "market": "United Kingdom ecommerce shoppers", "character_hint": "Use a contemporary UK-market creator look and English-speaking persona."},
        "united kingdom": {"language_name": "English", "language_code": "en-GB", "market": "United Kingdom ecommerce shoppers", "character_hint": "Use a contemporary UK-market creator look and English-speaking persona."},
        "japan": {"language_name": "Japanese", "language_code": "ja-JP", "market": "Japan ecommerce shoppers", "character_hint": "Use a Japan-market character look, styling, and lifestyle cues."},
        "korea": {"language_name": "Korean", "language_code": "ko-KR", "market": "South Korea ecommerce shoppers", "character_hint": "Use a Korea-market character look, styling, and lifestyle cues."},
        "south korea": {"language_name": "Korean", "language_code": "ko-KR", "market": "South Korea ecommerce shoppers", "character_hint": "Use a Korea-market character look, styling, and lifestyle cues."},
        "france": {"language_name": "French", "language_code": "fr-FR", "market": "France ecommerce shoppers", "character_hint": "Use a France-market character look, styling, and urban lifestyle cues."},
        "germany": {"language_name": "German", "language_code": "de-DE", "market": "Germany ecommerce shoppers", "character_hint": "Use a Germany-market character look, styling, and urban lifestyle cues."},
        "spain": {"language_name": "Spanish", "language_code": "es-ES", "market": "Spain ecommerce shoppers", "character_hint": "Use a Spain-market character look, styling, and urban lifestyle cues."},
        "mexico": {"language_name": "Spanish", "language_code": "es-MX", "market": "Mexico ecommerce shoppers", "character_hint": "Use a Mexico-market character look, styling, and urban lifestyle cues."},
        "brazil": {"language_name": "Portuguese", "language_code": "pt-BR", "market": "Brazil ecommerce shoppers", "character_hint": "Use a Brazil-market character look, styling, and urban lifestyle cues."},
        "russia": {"language_name": "Russian", "language_code": "ru-RU", "market": "Russia ecommerce shoppers", "character_hint": "Use a Russia-market character look, styling, and urban lifestyle cues."},
        "thailand": {"language_name": "Thai", "language_code": "th-TH", "market": "Thailand ecommerce shoppers", "character_hint": "Use a Thailand-market character look, styling, and lifestyle cues."},
        "indonesia": {"language_name": "Indonesian", "language_code": "id-ID", "market": "Indonesia ecommerce shoppers", "character_hint": "Use an Indonesia-market character look, styling, and lifestyle cues."},
        "malaysia": {"language_name": "Malay", "language_code": "ms-MY", "market": "Malaysia ecommerce shoppers", "character_hint": "Use a Malaysia-market character look, styling, and lifestyle cues."},
        "vietnam": {"language_name": "Vietnamese", "language_code": "vi-VN", "market": "Vietnam ecommerce shoppers", "character_hint": "Use a Vietnam-market character look, styling, and lifestyle cues."},
        "china": {"language_name": "Simplified Chinese", "language_code": "zh-CN", "market": "Chinese ecommerce shoppers", "character_hint": "Use a mainland China creator look, Chinese ecommerce tone, and local lifestyle cues."},
        "mainland china": {"language_name": "Simplified Chinese", "language_code": "zh-CN", "market": "Chinese ecommerce shoppers", "character_hint": "Use a mainland China creator look, Chinese ecommerce tone, and local lifestyle cues."},
    }

    language_meta = _language_meta(normalized_language)
    if language_meta:
        language_name = language_meta["language_name"]
        language_code = language_meta["language_code"]
    elif normalized_country and country_key in country_language_map:
        language_name = country_language_map[country_key]["language_name"]
        language_code = country_language_map[country_key]["language_code"]
    elif normalized_platform == "tiktok":
        language_name = "English"
        language_code = "en-US"
    else:
        language_name = "Simplified Chinese"
        language_code = "zh-CN"

    resolved_country = normalized_country or _LANGUAGE_TO_COUNTRY.get(language_code.casefold(), "")
    resolved_country_key = resolved_country.lower()

    if resolved_country and resolved_country_key in country_language_map:
        market = target_market or country_language_map[resolved_country_key]["market"]
        character_hint = country_language_map[resolved_country_key]["character_hint"]
    elif normalized_platform == "tiktok":
        market = target_market or "Global TikTok ecommerce shoppers"
        character_hint = "Use an international TikTok creator look, English-speaking persona, and global ecommerce styling."
    else:
        market = target_market or "Chinese ecommerce shoppers"
        character_hint = "Use a mainland China ecommerce creator look, Chinese-speaking persona, and domestic short-video styling."

    return {
        "platform": normalized_platform,
        "country": resolved_country or ("China" if normalized_platform != "tiktok" else ""),
        "language_name": language_name,
        "language_code": language_code,
        "market": market,
        "character_hint": character_hint,
        "copy_rule": "Use the country's main consumer language for all hooks, selling points, CTA, character naming style, scenario wording, and spoken on-camera dialogue."
        if resolved_country
        else ("Use English copy and international creator naming if platform is TikTok." if normalized_platform == "tiktok" else "Use Simplified Chinese copy and a domestic creator persona by default."),
    }


def _storyboard_prompt(config: PipelineConfig) -> str:
    locale = _locale_defaults(config.platform, config.country, config.language, config.target_market)
    task_brief = ""
    if config.task_text:
        task_brief = (
            f"User task brief: {config.task_text}\n"
            "Additional instruction: Respect any explicit locale, platform, nationality, character, or dialogue requirements from the task brief.\n"
        )
    return f"""
You are an expert ecommerce video strategist, storyboard designer, and character consistency planner.
The user provided one product image. Build a conversion-oriented short video plan for {locale["platform"]}.
Return strict JSON with: locale_profile, product_summary, character, storyboards.
Each storyboard must include: index, title_cn, goal_cn, scene_cn, hook_line_cn, selling_point_cn, cta_cn, storyboard_image_prompt_en, video_prompt_en.
Schema requirements (downstream code parses these as objects, not plain strings):
- product_summary MUST be a JSON object, e.g. {{"selling_points": ["bullet1", "bullet2"]}} or at minimum {{"summary": "one paragraph"}}. Never return product_summary as a single bare string.
- character MUST be a JSON object, e.g. {{"description_en": "...", "appearance_en": "...", "style_en": "..."}}. Never return character as a single bare string.
Rules:
1. Create exactly {config.storyboard_count} storyboard items.
2. Keep the same main character identity across all shots.
3. Make storyboard_image_prompt_en usable for image generation.
4. Make video_prompt_en usable for Veo.
5. No subtitles, no UI, no watermark, no stickers.
5a. Never mention or depict shopping cart icons, yellow cart icons, lower-left buttons, floating CTA badges, app interface chrome, clickable overlays, logos, or platform UI in any field.
5b. hook_line_cn, selling_point_cn, and cta_cn are planning/voiceover metadata only. They must never become visible typography, captions, subtitles, banners, callouts, price tags, labels, or any other on-screen text.
5c. storyboard_image_prompt_en and video_prompt_en must describe action, camera movement, product usage, scene, and emotion only. Do not ask the model to render any readable Chinese, English, pseudo-text, brand-like random letters, UI words, or malformed captions.
6. For compatibility, keep the field names title_cn, goal_cn, scene_cn, hook_line_cn, selling_point_cn, cta_cn, but the actual text content inside those fields must use the required local language instead of always Chinese.
7. Character identity, face, styling, name, daily environment, and speaking tone must match the platform and country setting. Do not reuse a China-market character for overseas scenarios.
8. If platform is TikTok and no country is specified, use English copy and a global TikTok creator persona.
9. If no platform and no country are specified, default to mainland China domestic ecommerce style and Simplified Chinese copy.
10. If a country is specified, prioritize that country's main consumer language and localized character style.
11. If the presenter speaks on camera, the spoken dialogue in video_prompt_en must clearly be delivered in {locale["language_name"]}, not a mismatched language.
{task_brief}Platform: {locale["platform"]}
Country: {locale["country"] or "Not specified"}
Required copy language: {locale["language_name"]} ({locale["language_code"]})
Target audience: {locale["market"]}
Character localization guidance: {locale["character_hint"]}
Language rule: {locale["copy_rule"]}
Aspect ratio: {config.aspect_ratio}
""".strip()


def _first_text(mapping: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _final_video_deliverable(
    merge_clips: bool,
    merge_result: Dict[str, Any] | None,
    completed_shots: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """对外交付：只读这一条即可拿到「最终视频」路径或 URL（片段明细见 completed_shots）。"""
    mr = merge_result if isinstance(merge_result, dict) else None
    if merge_clips and mr and mr.get("status") == "success":
        p = mr.get("merged_video_path")
        if isinstance(p, str) and p.strip():
            return {
                "path": p.strip(),
                "url": None,
                "kind": "merged_local",
                "hint": "已用 FFmpeg 拼接为一条完整成片（本地路径）。",
            }
    if merge_clips and mr and mr.get("status") == "failed":
        return {
            "path": None,
            "url": None,
            "kind": "merge_failed",
            "hint": str(mr.get("error") or "merge failed"),
        }
    shots = [s for s in completed_shots if isinstance(s, dict)]
    if not shots:
        return {"path": None, "url": None, "kind": "no_video", "hint": "无成功生成的镜头。"}
    if len(shots) == 1:
        u = _first_text(shots[0], "mp4url")
        if u:
            return {
                "path": None,
                "url": u,
                "kind": "single_clip_remote",
                "hint": "单镜头成片（未合并或多镜时请设 merge_clips=true）。",
            }
    if not merge_clips:
        return {
            "path": None,
            "url": None,
            "kind": "multiple_clips_no_merge",
            "hint": "已生成多条云端 mp4，未合并；请传 merge_clips=true 得到 final_video.path。",
        }
    return {"path": None, "url": None, "kind": "unknown", "hint": None}


def _first_list_of_text(mapping: Dict[str, Any], *keys: str) -> List[str]:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, list):
            items = [str(item).strip() for item in value if isinstance(item, str) and item.strip()]
            if items:
                return items
    return []


def _build_character_image_prompt(character: Dict[str, Any], storyboards: List[Dict[str, Any]], locale: Dict[str, str]) -> str:
    direct_prompt = _first_text(character, "character_image_prompt_en")
    if direct_prompt:
        return direct_prompt

    appearance = _first_text(character, "appearance_en", "face_details_en", "look", "look_en")
    style_cn = _first_text(character, "style_cn", "style", "style_en")
    description_cn = _first_text(character, "description_cn", "description_en", "description")
    first_storyboard_prompt = ""
    if storyboards:
        first_storyboard_prompt = _first_text(storyboards[0], "storyboard_image_prompt_en", "video_prompt_en")

    parts: List[str] = []
    if appearance:
        parts.append(appearance)
    if first_storyboard_prompt:
        parts.append(f"Use this scene only as styling reference, but make it a clean solo character portrait: {first_storyboard_prompt}")
    if style_cn:
        parts.append(f"Style notes: {style_cn}")
    if description_cn:
        parts.append(f"Character background for personality consistency: {description_cn}")
    if locale.get("character_hint"):
        parts.append(f"Localization guidance: {locale['character_hint']}")
    if locale.get("market") or locale.get("language_name"):
        parts.append(
            f"The character should feel native to {locale.get('market') or 'the target market'} and convincingly present as a fluent {locale.get('language_name') or 'local-language'} speaking creator"
        )
    parts.append("Create a clean ecommerce character reference portrait for repeated shot consistency")
    parts.append("Show the same core person that will appear in every storyboard scene")
    parts.append("No subtitles, no UI, no watermark, no text")
    return ". ".join(part for part in parts if part)


def _coerce_plan_character_and_product_summary(storyboard_plan: Dict[str, Any]) -> None:
    """分析模型常把 character / product_summary 写成一段字符串；本管道需要 dict 才能拼 prompt。"""
    ch = storyboard_plan.get("character")
    if isinstance(ch, str) and ch.strip():
        storyboard_plan["character"] = {"description_en": ch.strip()}
    elif not isinstance(ch, dict):
        storyboard_plan["character"] = {}
    ps = storyboard_plan.get("product_summary")
    if isinstance(ps, str) and ps.strip():
        storyboard_plan["product_summary"] = {"selling_points": [ps.strip()]}
    elif not isinstance(ps, dict):
        storyboard_plan["product_summary"] = {}


def _compose_image_prompt(storyboard: Dict[str, Any], character: Dict[str, Any], product_summary: Dict[str, Any]) -> str:
    selling_points = ", ".join(_first_list_of_text(product_summary, "selling_points", "main_selling_points_cn", "keywords"))
    consistency = "; ".join(_first_list_of_text(character, "consistency_rules_cn", "consistency_rules_en", "consistency_rules"))
    core = _first_text(storyboard, "storyboard_image_prompt_en", "video_prompt_en")
    prompt_parts = [
        core,
        "Keep the same main character identity across all shots",
    ]
    if selling_points:
        prompt_parts.append(f"Product selling points: {selling_points}")
    if consistency:
        prompt_parts.append(f"Consistency rules: {consistency}")
    prompt_parts.append("Commercial product shot, no subtitles, no UI, no watermark, no shopping cart icons, no CTA badges, no platform logos")
    prompt_parts.append("Zero visible text in the generated image: no captions, subtitles, labels, banners, slogan cards, price tags, app UI, watermark, random letters, Chinese characters, English words, or pseudo-text. If any text-like element would appear, replace it with a plain blank shape or clean surface.")
    return ". ".join(part for part in prompt_parts if part)


def _compose_video_prompt(storyboard: Dict[str, Any], fallback_prompt: str, locale: Dict[str, str]) -> str:
    base_prompt = _sanitize_ui_mentions(_first_text(storyboard, "video_prompt_en", "storyboard_image_prompt_en") or fallback_prompt)
    prompt_parts = [base_prompt]
    language_name = locale.get("language_name") or "the target language"
    market = locale.get("market") or "the target market"
    country = locale.get("country") or "the target locale"
    character_hint = locale.get("character_hint") or ""
    prompt_parts.append(f"All spoken dialogue, ad-lib lines, and lip sync must be in {language_name}.")
    prompt_parts.append(f"If an on-camera presenter appears, the presenter must feel native to {market} and match the locale of {country}.")
    if character_hint:
        prompt_parts.append(character_hint)
    prompt_parts.append("Do not switch the presenter into a different-country persona or mismatched language accent.")
    prompt_parts.append("No subtitles, no UI, no watermark, no on-screen text, no shopping cart icons, no CTA badges, no platform logos, no clickable overlays.")
    prompt_parts.append("The planning fields hook_line_cn, selling_point_cn, and cta_cn are not visual text. Express hooks, selling points, and CTA through action, camera movement, product demonstration, facial expression, and spoken dialogue only.")
    prompt_parts.append("Zero readable text in the frame: no captions, subtitles, labels, banners, slogan cards, price tags, app UI, watermarks, random letters, Chinese characters, English words, or pseudo-text. If text would appear, keep that area blank or as a non-readable graphic surface.")
    return " ".join(part.strip() for part in prompt_parts if isinstance(part, str) and part.strip())


def _download_file(url: str, destination: Path, timeout_seconds: int) -> Path:
    with requests.get(url, stream=True, timeout=timeout_seconds) as response:
        response.raise_for_status()
        with destination.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=1024 * 512):
                if chunk:
                    fh.write(chunk)
    return destination


def _validate_downloaded_clip(path: Path) -> None:
    if not path.exists():
        raise PipelineError(f"downloaded clip missing: {path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PipelineError(f"cannot stat downloaded clip: {path} ({exc})") from exc
    if size <= 0:
        raise PipelineError(f"downloaded clip is empty: {path}")


def _skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _candidate_tool_paths(tool_name: str) -> List[Path]:
    ext = ".exe" if sys.platform.startswith("win") else ""
    filename = f"{tool_name}{ext}"
    root = _skill_root()
    candidates = [
        root / "tools" / "ffmpeg" / filename,
        root / "tools" / "ffmpeg" / "windows" / filename,
        root / "tools" / "ffmpeg" / "bin" / filename,
    ]
    return candidates


def _resolve_tool_binary(tool_name: str, configured_path: str = "") -> str:
    explicit = (configured_path or "").strip()
    if explicit and explicit != tool_name:
        explicit_path = Path(explicit)
        if explicit_path.exists():
            return str(explicit_path)
        resolved = shutil.which(explicit)
        if resolved:
            return resolved
    for candidate in _candidate_tool_paths(tool_name):
        if candidate.exists():
            return str(candidate)
    resolved_default = shutil.which(tool_name)
    if resolved_default:
        return resolved_default
    return explicit or tool_name


def _probe_stream_types(media_path: str, ffmpeg_path: str) -> List[str]:
    ffprobe_binary = _resolve_tool_binary("ffprobe", "")
    if (not ffprobe_binary or ffprobe_binary == "ffprobe") and ffmpeg_path and ffmpeg_path != "ffmpeg":
        ffmpeg_candidate = Path(ffmpeg_path)
        sidecar_name = "ffprobe.exe" if ffmpeg_candidate.suffix.lower() == ".exe" else "ffprobe"
        candidate = ffmpeg_candidate.with_name(sidecar_name)
        if candidate.exists():
            ffprobe_binary = str(candidate)
    if not ffprobe_binary:
        return []
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
    streams = payload.get("streams", [])
    if not isinstance(streams, list):
        return []
    return [str(stream.get("codec_type", "")).strip().lower() for stream in streams if isinstance(stream, dict)]


def _ensure_ffmpeg_available(ffmpeg_binary: str) -> None:
    try:
        proc = subprocess.run(
            [ffmpeg_binary, "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise PipelineError(f"ffmpeg executable not found: {ffmpeg_binary}") from exc
    except OSError as exc:
        raise PipelineError(f"ffmpeg unavailable or missing runtime dependencies: {ffmpeg_binary} ({exc})") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        raise PipelineError(f"ffmpeg unavailable: {detail}")


def _merge_completed_shots(config: PipelineConfig, logger: RunLogger, shots: List[Dict[str, Any]]) -> Dict[str, Any]:
    ffmpeg_binary = _resolve_tool_binary("ffmpeg", config.ffmpeg_path)
    if not ffmpeg_binary:
        raise PipelineError(f"ffmpeg not found: {config.ffmpeg_path}")
    _ensure_ffmpeg_available(ffmpeg_binary)

    clips_dir = logger.run_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    downloaded: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for shot in sorted(shots, key=lambda item: int(item.get("index", 0))):
        index = int(shot.get("index", 0))
        clip_url = _first_text(shot, "mp4url")
        if not clip_url:
            payload = {"index": index, "error": f"Shot {index} missing mp4url for merge"}
            skipped.append(payload)
            logger.shot(index, "merge_download", "failed", error=payload["error"], payload=payload)
            continue
        clip_path = clips_dir / f"shot_{index:02d}.mp4"
        payload = {"index": index, "url": clip_url, "path": str(clip_path)}
        logger.shot(index, "merge_download", "running", payload=payload)
        try:
            downloaded_path, attempts = _retry(
                f"download_clip_{index:02d}",
                config.clip_download_retries,
                config.network_retry_delay_seconds,
                logger,
                lambda clip_url=clip_url, clip_path=clip_path: _download_file(clip_url, clip_path, config.clip_download_timeout_seconds),
            )
            _validate_downloaded_clip(Path(downloaded_path))
        except Exception as exc:
            payload = {"index": index, "url": clip_url, "path": str(clip_path), "error": str(exc)}
            skipped.append(payload)
            logger.shot(index, "merge_download", "failed", error=str(exc), payload=payload)
            continue
        logger.shot(index, "merge_download", "success", attempts=attempts, payload={"index": index, "path": str(downloaded_path), "url": clip_url})
        downloaded.append({"index": index, "url": clip_url, "path": str(downloaded_path)})

    if not downloaded:
        detail = "; ".join(f"shot {item.get('index')}: {item.get('error')}" for item in skipped[:3]) or "no clips available"
        raise PipelineError(f"no downloadable clips available for merge: {detail}")

    merged_path = logger.run_dir / "merged_output.mp4"
    output_arg = merged_path.resolve().as_posix()
    if len(downloaded) == 1:
        shutil.copyfile(downloaded[0]["path"], merged_path)
        stream_types = _probe_stream_types(downloaded[0]["path"], ffmpeg_binary)
        return {
            "status": "success",
            "merged_video_path": str(merged_path),
            "ffmpeg_command": None,
            "downloaded_clips": downloaded,
            "skipped_clips": skipped,
            "merge_mode": "single_clip_copy",
            "audio_preserved": "audio" in stream_types,
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

    if all_have_audio and len(audio_inputs) == len(downloaded):
        interleaved_inputs: List[str] = []
        for video_input, audio_input in zip(video_inputs, audio_inputs):
            interleaved_inputs.extend([video_input, audio_input])
        filter_complex = "".join(interleaved_inputs) + f"concat=n={len(downloaded)}:v=1:a=1[v][a]"
        cmd.extend(
            [
                "-filter_complex",
                filter_complex,
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                output_arg,
            ]
        )
        audio_preserved = True
    else:
        filter_complex = "".join(video_inputs) + f"concat=n={len(downloaded)}:v=1:a=0[v]"
        cmd.extend(["-filter_complex", filter_complex, "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", output_arg])
        audio_preserved = False

    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not merged_path.exists():
        last_error = proc.stderr.strip() or proc.stdout.strip() or f"ffmpeg exited with code {proc.returncode}"
        raise PipelineError(f"ffmpeg merge failed: {last_error}")

    return {
        "status": "success",
        "merged_video_path": str(merged_path),
        "ffmpeg_command": cmd,
        "downloaded_clips": downloaded,
        "skipped_clips": skipped,
        "merge_mode": "ffmpeg_concat_filter",
        "audio_preserved": audio_preserved,
    }


def _run_shot(client: ComflyClient, config: PipelineConfig, logger: RunLogger, storyboard: Dict[str, Any], product_image_url: str, character_image_url: str, character: Dict[str, Any], product_summary: Dict[str, Any], round_index: int = 1, deadline_monotonic: float | None = None) -> Dict[str, Any]:
    _ensure_before_deadline(deadline_monotonic, "shot generation")
    index = int(storyboard.get("index", 0))
    round_tag = f"round_{round_index}"
    image_prompt = _compose_image_prompt(storyboard, character, product_summary)
    locale = _locale_defaults(config.platform, config.country, config.language, config.target_market)
    video_prompt = _compose_video_prompt(storyboard, image_prompt, locale)
    logger.shot(
        index,
        f"plan_{round_tag}",
        "ready",
        payload={
            "round_index": round_index,
            "storyboard": storyboard,
            "storyboard_image_prompt": image_prompt,
            "submitted_video_prompt": video_prompt,
        },
    )
    _ensure_before_deadline(deadline_monotonic, "shot image generation")
    image_result, image_attempts = client.generate_image(config.image_model, image_prompt, config.aspect_ratio, [product_image_url, character_image_url], f"shot_{index:02d}_image_{round_tag}")
    logger.shot(index, f"image_{round_tag}", "success", attempts=image_attempts, payload=image_result)
    logger.record_usage(
        "image",
        config.image_model,
        f"shot_{index:02d}_storyboard_image_{round_tag}",
        payload={"index": index, "round_index": round_index, "url": image_result.get("url")},
    )
    last_error = ""
    for video_attempt in range(1, config.video_generation_retries + 1):
        _ensure_before_deadline(deadline_monotonic, "shot video generation")
        try:
            submit_result, submit_attempts = client.submit_video(video_prompt, config.video_model, [image_result["url"]], config.aspect_ratio, config.enhance_prompt, config.watermark, f"shot_{index:02d}_submit_{round_tag}_{video_attempt}")
            logger.shot(index, f"submit_{round_tag}_{video_attempt}", "success", attempts=submit_attempts, payload=submit_result)
            poll_result = client.poll_video(submit_result["task_id"], config.poll_interval_seconds, config.max_polls, deadline_monotonic=deadline_monotonic)
            logger.shot(index, f"poll_{round_tag}_{video_attempt}", "success", attempts=len(poll_result.get("history", [])), payload=poll_result)
            logger.record_usage(
                "video",
                config.video_model,
                f"shot_{index:02d}_video_{round_tag}",
                payload={"index": index, "round_index": round_index, "task_id": submit_result["task_id"], "mp4url": poll_result.get("mp4url")},
            )
            return {
                "index": index,
                "round_index": round_index,
                "title_cn": storyboard.get("title_cn"),
                "goal_cn": storyboard.get("goal_cn"),
                "scene_cn": storyboard.get("scene_cn"),
                "hook_line_cn": storyboard.get("hook_line_cn"),
                "selling_point_cn": storyboard.get("selling_point_cn"),
                "cta_cn": storyboard.get("cta_cn"),
                "storyboard_image_prompt_en": storyboard.get("storyboard_image_prompt_en"),
                "video_prompt_en": storyboard.get("video_prompt_en"),
                "submitted_video_prompt_en": video_prompt,
                "storyboard_image_url": image_result["url"],
                "storyboard_image_revised_prompt": image_result.get("revised_prompt"),
                "video_task_id": submit_result["task_id"],
                "video_status": poll_result["status"],
                "video_progress": poll_result["progress"],
                "mp4url": poll_result["mp4url"],
                "video_raw": poll_result["raw"],
                "video_generation_attempt": video_attempt,
            }
        except Exception as exc:
            last_error = str(exc)
            logger.shot(index, f"video_attempt_{round_tag}_{video_attempt}", "failed", attempts=video_attempt, error=last_error)
            if video_attempt < config.video_generation_retries:
                time.sleep(config.network_retry_delay_seconds * video_attempt)
    raise PipelineError(f"shot {index} video generation failed: {last_error}")


def run_pipeline(data: Input) -> Dict[str, Any]:
    config = _build_config(data)
    locale_profile = _locale_defaults(config.platform, config.country, config.language, config.target_market)
    output_dir = config.output_dir or str(Path(__file__).resolve().parent.parent / "runs")
    logger = RunLogger(output_dir, config, data)
    try:
        product_image = data.get("product_image")
        if not product_image:
            raise PipelineError("Missing product_image")
        client = ComflyClient(config, logger)
        product_image_url, upload_attempts = client.upload(product_image)
        logger.step("01_product_upload", "success", attempts=upload_attempts, payload={"product_image": product_image, "product_image_url": product_image_url})
        storyboard_plan, analysis_attempts = client.analyze(config.analysis_model, _storyboard_prompt(config), [product_image_url])
        _coerce_plan_character_and_product_summary(storyboard_plan)
        raw_storyboards = storyboard_plan.get("storyboards", [])
        storyboard_plan["storyboards"] = [_sanitize_storyboard_fields(sb) for sb in raw_storyboards if isinstance(sb, dict)]
        logger.step("02_storyboard_plan", "success", attempts=analysis_attempts, payload=storyboard_plan)
        logger.record_usage("analysis", config.analysis_model, "storyboard_plan_analysis", payload={"attempts": analysis_attempts})
        product_summary = storyboard_plan.get("product_summary", {})
        character = storyboard_plan.get("character", {})
        storyboards = storyboard_plan.get("storyboards", [])
        if not isinstance(character, dict) or not character:
            raise PipelineError(f"Invalid character plan: {storyboard_plan}")
        if not isinstance(storyboards, list) or not storyboards:
            raise PipelineError(f"Invalid storyboard plan: {storyboard_plan}")
        character_prompt = _build_character_image_prompt(
            character,
            [sb for sb in storyboards if isinstance(sb, dict)],
            locale_profile,
        )
        if not character_prompt.strip():
            raise PipelineError(f"Missing character image prompt after fallback synthesis: {storyboard_plan}")
        char_image_prompt = f"{character_prompt}. Keep the person suitable for repeated consistency across all storyboard scenes."
        char_result, char_attempts = client.generate_image(config.image_model, char_image_prompt, config.aspect_ratio, [product_image_url], "03_character_image")
        character_image_url = char_result["url"]
        logger.step("03_character_image", "success", attempts=char_attempts, payload={"character": character, "character_prompt": char_image_prompt, "character_image": char_result})
        logger.record_usage("image", config.image_model, "character_reference_image", payload={"url": character_image_url})
        results_map: Dict[int, Dict[str, Any]] = {}
        failure_map: Dict[int, Dict[str, Any]] = {}
        merge_result: Dict[str, Any] | None = None
        pending_storyboards = [sb for sb in storyboards[: config.storyboard_count] if isinstance(sb, dict)]
        max_rounds = max(1, config.shot_refill_retries + 1)
        generation_started_at = time.monotonic()
        generation_deadline = generation_started_at + config.generation_time_limit_seconds
        deadline_hit = False
        logger.step(
            "79_generation_deadline",
            "running",
            payload={
                "generation_time_limit_seconds": config.generation_time_limit_seconds,
                "deadline_started_at": datetime.now().isoformat(),
            },
        )
        for round_index in range(1, max_rounds + 1):
            if not pending_storyboards:
                break
            remaining_seconds = generation_deadline - time.monotonic()
            if remaining_seconds <= 0:
                deadline_hit = True
                for sb in pending_storyboards:
                    idx = int(sb.get("index", 0))
                    failure_map[idx] = {
                        "index": idx,
                        "title_cn": sb.get("title_cn"),
                        "round_index": round_index,
                        "error": f"generation deadline reached after {config.generation_time_limit_seconds}s",
                    }
                    logger.shot(idx, f"deadline_round_{round_index}", "failed", error=failure_map[idx]["error"], payload=failure_map[idx])
                break
            logger.step(
                f"80_generation_round_{round_index}",
                "running",
                attempts=round_index,
                payload={
                    "round_index": round_index,
                    "pending_storyboard_indexes": [int(sb.get("index", 0)) for sb in pending_storyboards],
                    "remaining_seconds": max(0, int(remaining_seconds)),
                },
            )
            round_failures: List[Dict[str, Any]] = []
            ex = concurrent.futures.ThreadPoolExecutor(max_workers=config.shot_concurrency)
            futures = {
                ex.submit(
                    _run_shot,
                    client,
                    config,
                    logger,
                    sb,
                    product_image_url,
                    character_image_url,
                    character,
                    product_summary if isinstance(product_summary, dict) else {},
                    round_index,
                    generation_deadline,
                ): sb
                for sb in pending_storyboards
            }
            try:
                unfinished_futures = set(futures.keys())
                try:
                    done_iter = concurrent.futures.as_completed(futures, timeout=max(1, remaining_seconds))
                    for future in done_iter:
                        unfinished_futures.discard(future)
                        sb = futures[future]
                        idx = int(sb.get("index", 0))
                        try:
                            result = future.result()
                            results_map[idx] = result
                            failure_map.pop(idx, None)
                            logger.shot(idx, f"final_round_{round_index}", "success", payload=result)
                        except Exception as exc:
                            payload = {"index": idx, "title_cn": sb.get("title_cn"), "round_index": round_index, "error": str(exc), "traceback": traceback.format_exc()}
                            failure_map[idx] = payload
                            round_failures.append(payload)
                            logger.shot(idx, f"final_round_{round_index}", "failed", error=str(exc), payload=payload)
                except concurrent.futures.TimeoutError:
                    deadline_hit = True
                if deadline_hit:
                    for future in unfinished_futures:
                        future.cancel()
                        sb = futures[future]
                        idx = int(sb.get("index", 0))
                        payload = {
                            "index": idx,
                            "title_cn": sb.get("title_cn"),
                            "round_index": round_index,
                            "error": f"generation deadline reached after {config.generation_time_limit_seconds}s",
                        }
                        failure_map[idx] = payload
                        round_failures.append(payload)
                        logger.shot(idx, f"deadline_round_{round_index}", "failed", error=payload["error"], payload=payload)
                    pending_storyboards = []
                    break
            finally:
                ex.shutdown(wait=not deadline_hit, cancel_futures=deadline_hit)
            round_failure_indexes = {item["index"] for item in round_failures}
            rerun_storyboards = [
                sb
                for sb in pending_storyboards
                if int(sb.get("index", 0)) in round_failure_indexes and _should_rerun_failed_shot(str(failure_map[int(sb.get("index", 0))]["error"]))
            ]
            logger.step(
                f"80_generation_round_{round_index}",
                "success" if not round_failures else ("partial_failure" if rerun_storyboards and round_index < max_rounds else "failed"),
                attempts=round_index,
                payload={
                    "round_index": round_index,
                    "succeeded_indexes": sorted(results_map.keys()),
                    "failed_indexes": sorted(round_failure_indexes),
                    "rerun_indexes": [int(sb.get("index", 0)) for sb in rerun_storyboards],
                },
            )
            pending_storyboards = rerun_storyboards
            if deadline_hit:
                break
        results = sorted(results_map.values(), key=lambda x: int(x.get("index", 0)))
        errors = [failure_map[idx] for idx in sorted(failure_map.keys())]
        generation_elapsed_seconds = int(time.monotonic() - generation_started_at)
        logger.step(
            "79_generation_deadline",
            "deadline_hit" if deadline_hit else "success",
            payload={
                "generation_time_limit_seconds": config.generation_time_limit_seconds,
                "generation_elapsed_seconds": generation_elapsed_seconds,
                "deadline_hit": deadline_hit,
                "completed_count": len(results),
                "failed_count": len(errors),
            },
        )
        if results and config.merge_clips:
            try:
                merge_result = _merge_completed_shots(config, logger, results)
                logger.step("90_merge_clips", "success", attempts=1, payload=merge_result)
            except Exception as exc:
                merge_result = {"status": "failed", "error": str(exc)}
                logger.step("90_merge_clips", "failed", attempts=1, error=str(exc), payload=merge_result)
        final_video = _final_video_deliverable(config.merge_clips, merge_result, results)
        output = {
            "run_dir": str(logger.run_dir),
            "final_video": final_video,
            "locale_profile": locale_profile,
            "product_image_url": product_image_url,
            "product_summary": product_summary,
            "character": character,
            "character_image_url": character_image_url,
            "storyboards": storyboards,
            "completed_shots": results,
            "failed_shots": errors,
            "deadline_hit": deadline_hit,
            "generation_elapsed_seconds": generation_elapsed_seconds,
            "merge_result": merge_result,
            "usage": logger.usage_snapshot(),
            "config": {
                "video_model": config.video_model,
                "image_model": config.image_model,
                "analysis_model": config.analysis_model,
                "aspect_ratio": config.aspect_ratio,
                "enhance_prompt": config.enhance_prompt,
                "watermark": config.watermark,
                "storyboard_count": config.storyboard_count,
                "shot_concurrency": config.shot_concurrency,
                "upload_retries": config.upload_retries,
                "analysis_retries": config.analysis_retries,
                "image_generation_retries": config.image_generation_retries,
                "video_submit_retries": config.video_submit_retries,
                "video_generation_retries": config.video_generation_retries,
                "merge_clips": config.merge_clips,
                "ffmpeg_path": config.ffmpeg_path,
                "clip_download_retries": config.clip_download_retries,
                "clip_download_timeout_seconds": config.clip_download_timeout_seconds,
                "shot_refill_retries": config.shot_refill_retries,
                "generation_time_limit_seconds": config.generation_time_limit_seconds,
            },
        }
        logger.finish("partial_failure" if errors else "success", output)
        return output
    except Exception:
        logger.finish("failed", {"error": traceback.format_exc()})
        raise


def handler(args: Args[Input]) -> Output:
    try:
        result = run_pipeline(args.input)
        return {"code": 206 if result.get("failed_shots") else 200, "msg": "Pipeline finished" if result.get("failed_shots") else "Pipeline completed successfully", "data": result}
    except Exception as exc:
        return {"code": -500, "msg": f"Pipeline failed: {exc}", "data": None}


if __name__ == "__main__":
    import sys

    raw = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    sys.stdout.write(json.dumps(handler(Args(raw)), ensure_ascii=False, indent=2))
