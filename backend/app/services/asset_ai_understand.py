"""Turn one local image or video into 2-3 visible tags."""
import os
import re
import subprocess
import tempfile
from pathlib import Path

import httpx

from .asset_ai_tags import parse_ai_tag_text

_PROMPT = (
    "\u53ea\u8fd4\u56de JSON\uff0c\u4e0d\u8981\u89e3\u91ca\uff1a"
    "{\"tags\":[\"\u6807\u7b7e1\",\"\u6807\u7b7e2\",\"\u6807\u7b7e3\"]}\u3002"
    "\u6839\u636e\u7d20\u6750\u5185\u5bb9\u751f\u6210 2 \u5230 3 \u4e2a\u77ed\u4e2d\u6587\u6807\u7b7e\u3002"
)


def understand_asset_tags(path: Path, media_type: str, *, base_url: str, headers: dict) -> str:
    kind = str(media_type or "").strip().lower()
    if kind not in {"image", "video"}:
        raise RuntimeError("\u53ea\u652f\u6301\u56fe\u7247\u6216\u89c6\u9891")
    source = Path(path)
    if not source.is_file():
        raise RuntimeError("\u627e\u4e0d\u5230\u7d20\u6750\u6587\u4ef6")
    data_urls = _image_data_urls(source) if kind == "image" else _video_frame_data_urls(source)
    if not data_urls:
        raise RuntimeError("\u6ca1\u6709\u62bd\u51fa\u53ef\u7528\u4e8e\u7406\u89e3\u7684\u753b\u9762")
    text = _complete_vision(base_url, headers, data_urls)
    return parse_ai_tag_text(text)


def _image_data_urls(path: Path):
    from ..api.ai_3d_model import _image_understand_data_url

    return [_image_understand_data_url(path, max_side=1024, max_bytes=1_500_000)]


def _video_frame_data_urls(path: Path):
    from ..api.ai_3d_model import _image_understand_data_url
    from .media_edit_exec import find_ffmpeg

    ffmpeg = find_ffmpeg()
    duration = _video_duration_seconds(ffmpeg, path)
    times = [0.0] if duration <= 0.4 else [duration * 0.1, duration * 0.5, max(0.0, duration - 0.2)]
    urls = []
    with tempfile.TemporaryDirectory(prefix="lobster_asset_ai_frames_") as temp_name:
        for index, timestamp in enumerate(times, start=1):
            frame = Path(temp_name) / f"frame_{index}.jpg"
            if not _extract_frame(ffmpeg, path, timestamp, frame):
                continue
            urls.append(_image_understand_data_url(frame, max_side=1024, max_bytes=1_500_000))
    return urls


def _video_duration_seconds(ffmpeg: str, path: Path) -> float:
    proc = _run([ffmpeg, "-i", str(path)], 60)
    text = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _extract_frame(ffmpeg: str, source: Path, timestamp: float, dest: Path) -> bool:
    _run(
        [ffmpeg, "-y", "-ss", f"{max(0.0, timestamp):.3f}", "-i", str(source), "-frames:v", "1", "-q:v", "3", str(dest)],
        120,
    )
    return dest.is_file() and dest.stat().st_size > 0


def _complete_vision(base_url: str, headers: dict, data_urls) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("\u6ca1\u6709\u53ef\u7528\u7684\u8ba4\u8bc1\u4e2d\u5fc3\u5730\u5740")
    request_headers = {
        str(key): str(value)
        for key, value in dict(headers or {}).items()
        if str(key).lower() not in {"content-type", "content-length", "host"}
    }
    request_headers["X-Lobster-Image-Understand"] = "1"
    payload = {
        "model": "gpt-5.6-sol",
        "stream": False,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": _PROMPT}]
                + [{"type": "image_url", "image_url": {"url": url}} for url in data_urls],
            }
        ],
    }
    try:
        with httpx.Client(timeout=120.0, follow_redirects=True, trust_env=False) as client:
            response = client.post(f"{base}/api/sutui-chat/completions", json=payload, headers=request_headers)
    except Exception as exc:
        raise RuntimeError(f"AI\u7406\u89e3\u8bf7\u6c42\u5931\u8d25\uff1a{exc}") from exc
    if response.status_code >= 400:
        raise RuntimeError(f"AI\u7406\u89e3\u5931\u8d25\uff1aHTTP {response.status_code} {(response.text or '')[:300]}")
    data = response.json() if response.content else {}
    return _message_text(data)


def _message_text(data) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices") if isinstance(data.get("choices"), list) else []
    message = choices[0].get("message") if choices and isinstance(choices[0], dict) else {}
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "\n".join(parts)
    return ""


def _run(command, timeout: int):
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=timeout,
        check=False,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    )
