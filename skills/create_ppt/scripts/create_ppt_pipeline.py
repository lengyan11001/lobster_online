#!/usr/bin/env python3
"""Minimal JSON pipeline for the bundled create_ppt skill.

Input is read from stdin as JSON and output is written as JSON. This keeps the
skill easy to call from the local backend without depending on OpenClaw paths.
"""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


SKILL_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = SKILL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _safe_name(value: str, default: str = "presentation") -> str:
    text = (value or "").strip()
    if not text:
        text = default
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
    text = re.sub(r"\s+", "_", text).strip("._ ")
    return (text or default)[:80]


def _default_output_dir() -> Path:
    root = Path(os.environ.get("LOBSTER_CREATE_PPT_OUTPUT_DIR") or "").expanduser()
    if not str(root).strip():
        root = SKILL_ROOT / "runs"
    return root


def _write_outline_file(run_dir: Path, data: Dict[str, Any]) -> Path:
    outline_file = str(data.get("outline_file") or "").strip()
    if outline_file:
        path = Path(outline_file)
        if not path.exists():
            raise FileNotFoundError(f"outline_file not found: {outline_file}")
        return path

    content = str(data.get("outline_markdown") or data.get("markdown") or "").strip()
    if not content:
        topic = str(data.get("topic") or "PPT").strip()
        content = f"# {topic}\n\n## {topic}\n\n### Overview\n- {topic}\n"
    path = run_dir / "outline.md"
    path.write_text(content, encoding="utf-8")
    return path


def run_pipeline(data: Dict[str, Any]) -> Dict[str, Any]:
    from ppt_maker import create_from_ai, create_from_outline

    mode = str(data.get("mode") or "outline").strip().lower()
    topic = str(data.get("topic") or data.get("title") or "PPT").strip()
    theme = str(data.get("theme") or "business").strip() or "business"
    language = str(data.get("language") or "zh-CN").strip() or "zh-CN"
    slide_count = int(data.get("slide_count") or data.get("slides") or 10)
    output_dir = Path(str(data.get("output_dir") or _default_output_dir())).expanduser()
    if not output_dir.is_absolute():
        output_dir = SKILL_ROOT / output_dir
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_dir / f"run_{stamp}_{_safe_name(topic)}"
    n = 1
    while run_dir.exists():
        n += 1
        run_dir = output_dir / f"run_{stamp}_{_safe_name(topic)}_{n:02d}"
    run_dir.mkdir(parents=True, exist_ok=True)

    filename = str(data.get("filename") or "").strip()
    if not filename:
        filename = f"{_safe_name(topic)}.pptx"
    if not filename.lower().endswith(".pptx"):
        filename += ".pptx"
    output_path = run_dir / filename

    template_path = str(data.get("template_path") or "").strip() or None
    if template_path and not Path(template_path).exists():
        raise FileNotFoundError(f"template_path not found: {template_path}")

    if mode in {"outline", "markdown", "md"}:
        outline_path = _write_outline_file(run_dir, data)
        result_path = create_from_outline(
            input_path=str(outline_path),
            output_path=str(output_path),
            theme_name=theme,
            template_path=template_path,
        )
    elif mode in {"ai", "topic"}:
        api_key = str(data.get("api_key") or os.environ.get("OPENAI_API_KEY") or os.environ.get("HAODUOMI_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("AI mode requires api_key or OPENAI_API_KEY/HAODUOMI_API_KEY")
        model = str(data.get("model") or "gpt-5.4").strip()
        base_url = str(data.get("base_url") or os.environ.get("OPENAI_BASE_URL") or os.environ.get("HAODUOMI_OPENAI_BASE_URL") or "").strip() or None
        instructions = str(data.get("instructions") or "").strip()
        result_path = create_from_ai(
            topic=topic,
            output_path=str(output_path),
            slide_count=max(1, slide_count),
            theme_name=theme,
            language=language,
            instructions=instructions,
            ai_model=model,
            api_key=api_key,
            base_url=base_url,
            template_path=template_path,
        )
    else:
        raise ValueError(f"Unsupported mode: {mode}")

    pptx_path = Path(result_path)
    return {
        "ok": True,
        "mode": mode,
        "topic": topic,
        "theme": theme,
        "run_dir": str(run_dir),
        "pptx_path": str(pptx_path),
        "filename": pptx_path.name,
        "size_bytes": pptx_path.stat().st_size if pptx_path.exists() else 0,
    }


def handler(raw: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return {"code": 200, "msg": "PPT created", "data": run_pipeline(raw)}
    except Exception as exc:
        return {
            "code": -500,
            "msg": f"PPT creation failed: {exc}",
            "data": {"traceback": traceback.format_exc()},
        }


if __name__ == "__main__":
    payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    sys.stdout.write(json.dumps(handler(payload), ensure_ascii=False, indent=2))
