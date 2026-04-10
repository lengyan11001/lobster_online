from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import os
import re
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Generic, List, Optional, TypedDict, TypeVar
from urllib.parse import unquote, urlparse

import requests
from PIL import Image, ImageDraw, ImageFont
from json_repair import repair_json

try:
    from runtime import Args  # type: ignore
except ImportError:
    T = TypeVar("T")

    class Args(Generic[T]):  # type: ignore[override]
        def __init__(self, input: T):
            self.input = input


class Input(TypedDict, total=False):
    product_image: str
    reference_images: List[str]
    apikey: str
    base_url: str
    platform: str
    country: str
    language: str
    target_market: str
    analysis_model: str
    image_model: str
    aspect_ratio: str
    page_count: int
    page_width: int
    page_height: int
    page_gap_px: int
    output_dir: str
    upload_retries: int
    analysis_retries: int
    image_generation_retries: int
    network_retry_delay_seconds: int
    image_concurrency: int


class Output(TypedDict):
    code: int
    msg: str
    data: Dict[str, Any] | None


@dataclass
class PipelineConfig:
    base_url: str
    api_key: str
    platform: str = "ecommerce"
    country: str = ""
    language: str = "zh-CN"
    target_market: str = ""
    analysis_model: str = "gemini-2.5-pro"
    image_model: str = "nano-banana-2"
    aspect_ratio: str = "9:16"
    page_count: int = 12
    page_width: int = 1242
    page_height: int = 1480
    page_gap_px: int = 0
    output_dir: str = ""
    upload_retries: int = 3
    analysis_retries: int = 2
    image_generation_retries: int = 3
    network_retry_delay_seconds: int = 3
    image_concurrency: int = 11


class PipelineError(RuntimeError):
    pass


MODEL_UNIT_COSTS: Dict[str, int] = {"analysis": 1, "image": 2}
DEFAULT_SELLING_POINTS = [
    "核心卖点突出",
    "适合移动端详情页展示",
    "强调场景价值与使用体验",
]
DEFAULT_TRUST_POINTS = ["细节清晰可见", "风格统一专业", "适合电商转化表达"]
DEFAULT_USAGE_SCENES = ["居家场景", "日常使用场景", "近景细节场景"]


class RunLogger:
    def __init__(self, base_dir: str, config: PipelineConfig, raw_input: Dict[str, Any]) -> None:
        root = Path(base_dir)
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = root / f"run_{stamp}"
        seq = 1
        while self.run_dir.exists():
            seq += 1
            self.run_dir = root / f"run_{stamp}_{seq:02d}"
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
    "aspect_ratio": config.aspect_ratio,
    "page_count": config.page_count,
    "page_width": config.page_width,
    "page_height": config.page_height,
    "page_gap_px": config.page_gap_px,
    "image_concurrency": config.image_concurrency,
            },
            "input": {k: v for k, v in raw_input.items() if k != "apikey"},
            "usage": {
                "summary": {"analysis_count": 0, "image_count": 0, "total_points": 0, "total_units": 0},
                "breakdown": {"analysis": {}, "image": {}},
                "records": [],
            },
            "steps": {},
            "pages": {},
            "errors": [],
        }
        self.write_json("00_input.json", self.manifest["input"])
        self._save()

    def write_json(self, filename: str, payload: Any) -> None:
        with (self.run_dir / filename).open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def step(self, name: str, status: str, attempts: int = 0, payload: Any = None, error: str | None = None) -> None:
        with self.lock:
            self.manifest["steps"][name] = {
                "status": status,
                "attempts": attempts,
                "error": error,
                "updated_at": datetime.now().isoformat(),
            }
            self._save()
        if payload is not None:
            self.write_json(f"{name}.json", payload)

    def page(self, index: int, stage: str, status: str, attempts: int = 0, payload: Any = None, error: str | None = None) -> None:
        key = str(index)
        with self.lock:
            self.manifest["pages"].setdefault(key, {})[stage] = {
                "status": status,
                "attempts": attempts,
                "error": error,
                "updated_at": datetime.now().isoformat(),
            }
            self._save()
        if payload is not None:
            self.write_json(f"page_{index:02d}_{stage}.json", payload)

    def error(self, where: str, message: str) -> None:
        with self.lock:
            self.manifest["errors"].append({"where": where, "message": message, "ts": datetime.now().isoformat()})
            self._save()

    def record_usage(self, kind: str, model: str, context: str, payload: Any = None) -> None:
        units = MODEL_UNIT_COSTS.get(kind, 0)
        with self.lock:
            usage = self.manifest["usage"]
            usage["summary"][f"{kind}_count"] += 1
            usage["summary"]["total_points"] += units
            usage["summary"]["total_units"] += units
            bucket = usage["breakdown"].setdefault(kind, {}).setdefault(
                model, {"count": 0, "points": 0, "units": 0}
            )
            bucket["count"] += 1
            bucket["points"] += units
            bucket["units"] += units
            usage["records"].append(
                {
                    "kind": kind,
                    "model": model,
                    "context": context,
                    "points": units,
                    "units": units,
                    "ts": datetime.now().isoformat(),
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


def _retry(action: str, attempts: int, delay: int, logger: RunLogger, fn):
    last: Optional[Exception] = None
    for idx in range(1, attempts + 1):
        try:
            return fn(), idx
        except Exception as exc:
            last = exc
            logger.error(action, f"attempt {idx} failed: {exc}")
            if idx >= attempts:
                break
            time.sleep(max(1, delay) * idx)
    raise PipelineError(f"{action} failed after {attempts} attempt(s): {last}")


class ComflyClient:
    def __init__(self, config: PipelineConfig, logger: RunLogger) -> None:
        self.config = config
        self.logger = logger
        self.base_url = config.base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {config.api_key}", "Accept": "application/json"})

    def _check(self, response: requests.Response) -> Dict[str, Any]:
        try:
            payload = response.json()
        except Exception:
            payload = {"raw_text": response.text}
        if response.status_code != 200 or not isinstance(payload, dict):
            raise PipelineError(f"HTTP {response.status_code}: {payload}")
        return payload

    def upload(self, src: str) -> tuple[str, int]:
        if src.startswith("http://") or src.startswith("https://"):
            return src, 0

        path = _resolve_local_path(src)
        if not path.exists():
            raise PipelineError(f"Image file not found: {src}")

        def call() -> str:
            with path.open("rb") as f:
                response = self.session.post(
                    f"{self.base_url}/v1/files",
                    files={"file": (path.name, f, "application/octet-stream")},
                    timeout=120,
                )
            payload = self._check(response)
            url = payload.get("url")
            if not isinstance(url, str) or not url.strip():
                raise PipelineError(f"Upload returned no url: {payload}")
            return url.strip()

        return _retry("upload", self.config.upload_retries, self.config.network_retry_delay_seconds, self.logger, call)

    def analyze_json(
        self,
        model: str,
        prompt: str,
        image_urls: List[str],
        action: str,
        max_tokens: int = 4000,
    ) -> tuple[Dict[str, Any], int]:
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
        body = {
            "model": model,
            "stream": False,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": int(max_tokens),
        }

        def call() -> Dict[str, Any]:
            response = self.session.post(
                f"{self.base_url}/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                json=body,
                timeout=180,
            )
            payload = self._check(response)
            text = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = _parse_json_block(text)
            parsed["_raw_text"] = text
            return parsed

        return _retry(action, self.config.analysis_retries, self.config.network_retry_delay_seconds, self.logger, call)

    def generate_image(self, model: str, prompt: str, aspect_ratio: str, refs: List[str], action: str) -> tuple[Dict[str, Any], int]:
        body: Dict[str, Any] = {"model": model, "prompt": prompt, "aspect_ratio": aspect_ratio}
        if refs:
            body["image"] = refs

        def call() -> Dict[str, Any]:
            response = self.session.post(
                f"{self.base_url}/v1/images/generations",
                headers={"Content-Type": "application/json"},
                json=body,
                timeout=180,
            )
            payload = self._check(response)
            data = payload.get("data", [])
            if not isinstance(data, list) or not data:
                raise PipelineError(f"Image generation returned no data: {payload}")
            first = data[0] if isinstance(data[0], dict) else {}
            url = first.get("url")
            if not isinstance(url, str) or not url.strip():
                raise PipelineError(f"Image generation returned no url: {payload}")
            return {"url": url.strip(), "raw": payload, "request": body}

        return _retry(
            action,
            self.config.image_generation_retries,
            self.config.network_retry_delay_seconds,
            self.logger,
            call,
        )


def _parse_json_block(text: str) -> Dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        raise PipelineError("Model returned empty response")
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    candidates = [stripped]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    for candidate in candidates:
        try:
            repaired = repair_json(candidate, ensure_ascii=False)
            parsed = json.loads(repaired)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    raise PipelineError(f"Unable to parse JSON from model output: {stripped[:500]}")


def _safe_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _sanitize_copy_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"</?[^>]+>", "", text)
    text = text.replace("**", "").replace("__", "").replace("##", "").replace("`", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -*\n\r\t")


def _sanitize_copy_list(value: Any, limit: int) -> List[str]:
    items = [_sanitize_copy_text(item) for item in _safe_string_list(value)]
    return [item for item in items if item][:limit]


def _short_overlay_phrase(value: Any, max_chars: int = 8) -> str:
    text = _sanitize_copy_text(value)
    if not text:
        return ""
    text = re.sub(r"[，。、“”‘’：；！!？?（）()\[\]{}<>《》·\-_/|]+", "", text)
    text = re.sub(r"\s+", "", text)
    return text[:max_chars]


def _resolve_local_path(src: str) -> Path:
    raw = str(src or "").strip()
    if not raw:
        return Path(raw)
    if raw.startswith("file://"):
        parsed = urlparse(raw)
        raw = unquote(parsed.path or "")
        if raw.startswith("/") and re.match(r"^/[A-Za-z]:", raw):
            raw = raw[1:]
    raw = os.path.expandvars(os.path.expanduser(raw))
    path = Path(raw)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def _locale_defaults(platform: str, country: str, language: str, target_market: str) -> Dict[str, str]:
    normalized_country = (country or "").strip()
    normalized_language = (language or "").strip()
    language_name = normalized_language or (
        "English" if normalized_country.lower() in {"united states", "usa", "uk", "united kingdom"} else "Simplified Chinese"
    )
    market = target_market or (f"{normalized_country} ecommerce shoppers" if normalized_country else "Chinese ecommerce shoppers")
    return {
        "platform": (platform or "ecommerce").strip().lower(),
        "country": normalized_country or "China",
        "language_name": language_name,
        "market": market,
    }


def _build_config(data: Input) -> PipelineConfig:
    api_key = str(data.get("apikey") or os.getenv("COMFLY_API_KEY", "")).strip()
    if not api_key:
        raise PipelineError("Missing apikey")
    page_count = max(10, min(16, int(data.get("page_count", 12))))
    return PipelineConfig(
        base_url=str(data.get("base_url", os.getenv("COMFLY_API_BASE", "https://ai.comfly.chat"))).strip(),
        api_key=api_key,
        platform=str(data.get("platform", "ecommerce")).strip(),
        country=str(data.get("country", "")).strip(),
        language=str(data.get("language", "zh-CN")).strip(),
        target_market=str(data.get("target_market", "")).strip(),
        analysis_model=str(data.get("analysis_model", "gemini-2.5-pro")).strip(),
        image_model=str(data.get("image_model", "nano-banana-2")).strip(),
        aspect_ratio=str(data.get("aspect_ratio", "9:16")).strip(),
        page_count=page_count,
        page_width=int(data.get("page_width", 1242)),
        page_height=int(data.get("page_height", 1480)),
        page_gap_px=int(data.get("page_gap_px", 0)),
        output_dir=str(data.get("output_dir", "")).strip(),
        upload_retries=int(data.get("upload_retries", 3)),
        analysis_retries=int(data.get("analysis_retries", 2)),
        image_generation_retries=int(data.get("image_generation_retries", 3)),
        network_retry_delay_seconds=int(data.get("network_retry_delay_seconds", 3)),
        image_concurrency=max(1, int(data.get("image_concurrency", 11))),
    )


def _analysis_prompt(config: PipelineConfig) -> str:
    locale = _locale_defaults(config.platform, config.country, config.language, config.target_market)
    return f"""
You are a senior ecommerce creative strategist.
The user gave product reference images and wants a mobile ecommerce detail-image sequence.
Return strict JSON only with:
product_name, category, audience, product_summary, hero_claim, visual_style,
selling_points (8-12 strings), trust_points (3-6), usage_scenes (3-6),
materials, colors, structure_features, care_points, certification_clues, visual_constraints.
Rules:
1. Stay faithful to what is visible.
2. If information is uncertain, put it in visual_constraints conservatively.
3. product_summary and hero_claim must use the target consumer language.
Platform: {locale["platform"]}
Country: {locale["country"]}
Target market: {locale["market"]}
Target language: {locale["language_name"]}
""".strip()


def _normalize_analysis(plan: Dict[str, Any], locale: Dict[str, str]) -> Dict[str, Any]:
    result = dict(plan)
    result["selling_points"] = _safe_string_list(plan.get("selling_points"))[:12] or DEFAULT_SELLING_POINTS[:]
    result["trust_points"] = _safe_string_list(plan.get("trust_points"))[:6] or DEFAULT_TRUST_POINTS[:]
    result["usage_scenes"] = _safe_string_list(plan.get("usage_scenes"))[:6] or DEFAULT_USAGE_SCENES[:]
    result["materials"] = _safe_string_list(plan.get("materials"))[:8]
    result["colors"] = _safe_string_list(plan.get("colors"))[:8]
    result["structure_features"] = _safe_string_list(plan.get("structure_features"))[:8]
    result["care_points"] = _safe_string_list(plan.get("care_points"))[:8]
    result["certification_clues"] = _safe_string_list(plan.get("certification_clues"))[:6]
    result["visual_constraints"] = _safe_string_list(plan.get("visual_constraints"))[:8]
    result["product_name"] = str(result.get("product_name") or "Product").strip()
    result["category"] = str(result.get("category") or "product").strip()
    result["audience"] = str(result.get("audience") or "ecommerce shoppers").strip()
    result["hero_claim"] = str(result.get("hero_claim") or result["selling_points"][0]).strip()
    result["product_summary"] = str(result.get("product_summary") or result["hero_claim"]).strip()
    result["visual_style"] = str(
        result.get("visual_style") or "clean ecommerce poster, warm lifestyle lighting, premium mobile detail page"
    ).strip()
    result["locale_profile"] = locale
    return result


def _pad_points(points: List[str], minimum: int, fallback: List[str]) -> List[str]:
    out = [item for item in points if item]
    seed = [item for item in fallback if item]
    while len(out) < minimum and seed:
        out.append(seed[len(out) % len(seed)])
    while len(out) < minimum:
        out.append(f"补充卖点 {len(out) + 1}")
    return out


def _build_page_slots(analysis: Dict[str, Any], page_count: int) -> List[Dict[str, Any]]:
    points = _pad_points(_safe_string_list(analysis.get("selling_points")), 6, DEFAULT_SELLING_POINTS)
    trust_points = _safe_string_list(analysis.get("trust_points")) or DEFAULT_TRUST_POINTS[:]
    scenes = _safe_string_list(analysis.get("usage_scenes")) or DEFAULT_USAGE_SCENES[:]
    materials = _safe_string_list(analysis.get("materials"))
    structure = _safe_string_list(analysis.get("structure_features"))
    care = _safe_string_list(analysis.get("care_points"))
    certs = _safe_string_list(analysis.get("certification_clues"))
    hero_claim = str(analysis.get("hero_claim") or points[0]).strip()
    summary = str(analysis.get("product_summary") or hero_claim).strip()
    detail_target = max(10, int(page_count) - 1)
    hero_slot = {
        "slot": "hero_cover",
        "goal": "Create a premium, non-numbered advertising cover that sells why this product is worth bringing home.",
        "focus": hero_claim,
        "points": points[:3],
        "show_number": False,
    }

    detail_slots: List[Dict[str, Any]] = [
        {"slot": "overview", "goal": "Summarize the top consumer benefits at a glance.", "focus": summary, "points": points[:4], "show_number": True},
        {"slot": "feature", "goal": "Explain the first key feature with a hero visual.", "focus": points[0], "points": [points[0], points[1]]},
        {"slot": "feature", "goal": "Explain the second key feature with a strong comparison feel.", "focus": points[1], "points": [points[1], points[2]]},
        {"slot": "feature", "goal": "Explain the third key feature with detail emphasis.", "focus": points[2], "points": [points[2], points[3]]},
        {"slot": "feature", "goal": "Show another value point that supports conversion.", "focus": points[3], "points": [points[3], points[4]]},
        {"slot": "scene", "goal": "Show the product in a believable daily scene.", "focus": scenes[0], "points": scenes[:3]},
        {
            "slot": "material",
            "goal": "Explain material and structure details.",
            "focus": (materials + structure + care + [hero_claim])[0],
            "points": (materials + structure + care)[:4] or points[2:6],
        },
        {
            "slot": "trust",
            "goal": "Build confidence with conservative proof points.",
            "focus": (trust_points + certs + [hero_claim])[0],
            "points": (trust_points + certs)[:4] or points[1:5],
        },
        {"slot": "closing", "goal": "Close with a summary and buying motivation.", "focus": hero_claim, "points": points[:4]},
    ]

    extra_pool = points[4:] + trust_points + scenes + materials + structure + care + certs
    extra_idx = 0
    while len(detail_slots) < detail_target:
        focus = extra_pool[extra_idx] if extra_idx < len(extra_pool) else f"补充卖点 {len(slots) + 1}"
        detail_slots.insert(
            -1,
            {
                "slot": "feature",
                "goal": "Expand one more selling point to make the sequence more complete.",
                "focus": focus,
                "points": [focus] + points[max(0, (extra_idx % len(points)) - 1) : (extra_idx % len(points)) + 1],
            },
        )
        extra_idx += 1
    slots = [hero_slot] + detail_slots[:detail_target]
    return slots[: max(11, int(page_count))]


def _page_copy_prompt(analysis: Dict[str, Any], slots: List[Dict[str, Any]], config: PipelineConfig) -> str:
    locale = analysis.get("locale_profile") or _locale_defaults(
        config.platform, config.country, config.language, config.target_market
    )
    slot_lines = [
        json.dumps(
            {
                "index": idx,
                "slot": slot["slot"],
                "goal": slot["goal"],
                "focus": slot["focus"],
                "points": slot.get("points", []),
            },
            ensure_ascii=False,
        )
        for idx, slot in enumerate(slots, 1)
    ]
    return f"""
You are designing a mobile ecommerce detail-image sequence.
Return strict JSON only:
{{"pages":[{{"index":1,"slot":"cover","title":"...","subtitle":"...","highlights":["..."],"badge":"...","footer":"...","image_prompt_en":"...","layout_hint":"..."}}]}}
Rules:
1. Generate exactly {len(slots)} pages in the same order.
2. title/subtitle/highlights/badge/footer must use {locale["language_name"]}.
3. title must be plain text only. Do NOT use markdown, asterisks, HTML tags, bullets, numbering symbols, or emoji.
4. The cover page must feel like a premium campaign poster with a strong reason-to-buy headline and elevated hero composition.
5. subtitle one short sentence. highlights 2-4 short bullets.
6. For the cover page only, every highlight must be no more than 8 Chinese characters.
7. image_prompt_en must be English and describe a clean ecommerce background with NO text, NO watermark, NO UI.
8. Keep product appearance consistent across pages.
9. Avoid unsafe hard claims.
Product analysis:
{json.dumps(analysis, ensure_ascii=False, indent=2)}
Page slots:
{chr(10).join(slot_lines)}
""".strip()


def _normalize_pages(plan: Dict[str, Any], slots: List[Dict[str, Any]], analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_pages = plan.get("pages")
    if not isinstance(raw_pages, list):
        raise PipelineError(f"Invalid page copy plan: {plan}")

    default_footer = str(analysis.get("hero_claim") or analysis.get("product_summary") or "").strip()
    pages: List[Dict[str, Any]] = []
    for idx, slot in enumerate(slots, 1):
        raw = raw_pages[idx - 1] if idx - 1 < len(raw_pages) and isinstance(raw_pages[idx - 1], dict) else {}
        is_cover = str(slot.get("slot") or "").strip().lower() in {"cover", "hero_cover"}
        highlights = _sanitize_copy_list(raw.get("highlights"), 4) or [
            _sanitize_copy_text(item) for item in slot.get("points", []) if _sanitize_copy_text(item)
        ][:4]
        if not highlights:
            highlights = DEFAULT_SELLING_POINTS[:3]
        if is_cover:
            highlights = [_short_overlay_phrase(item, 8) for item in highlights]
            highlights = [item for item in highlights if item][:3]
            if not highlights:
                highlights = [_short_overlay_phrase(item, 8) for item in DEFAULT_SELLING_POINTS[:3] if _short_overlay_phrase(item, 8)]
        pages.append(
            {
                "index": idx,
                "display_index": None if is_cover else max(1, idx - 1),
                "slot": slot["slot"],
                "goal": slot["goal"],
                "focus": slot["focus"],
                "title": _sanitize_copy_text(raw.get("title") or slot["focus"] or analysis.get("hero_claim") or ""),
                "subtitle": _sanitize_copy_text(raw.get("subtitle") or analysis.get("product_summary") or ""),
                "highlights": highlights,
                "badge": _sanitize_copy_text(raw.get("badge") or ""),
                "footer": _sanitize_copy_text(raw.get("footer") or default_footer),
                "image_prompt_en": str(raw.get("image_prompt_en") or "").strip(),
                "layout_hint": _sanitize_copy_text(raw.get("layout_hint") or ""),
            }
        )
    return pages


def _compose_page_background_prompt(page: Dict[str, Any], analysis: Dict[str, Any]) -> str:
    style = str(analysis.get("visual_style") or "premium ecommerce poster").strip()
    points = ", ".join(_safe_string_list(page.get("highlights")))
    constraints = ", ".join(_safe_string_list(analysis.get("visual_constraints"))[:4])
    prompt_parts = [
        str(page.get("image_prompt_en") or "").strip(),
        f"Product category: {analysis.get('category') or 'product'}",
        f"Hero focus: {page.get('focus') or analysis.get('hero_claim') or analysis.get('product_name')}",
        f"Support highlights: {points}",
        f"Visual style: {style}",
        "Create a clean mobile ecommerce detail page background with strong product visibility",
        "No text, no typography, no watermark, no UI, no sticker, no subtitles",
    ]
    if str(page.get("slot") or "").strip().lower() == "cover":
        prompt_parts.append(
            "Make it feel like a premium advertising poster cover, not a normal detail page. Prefer a lifestyle scene, aspirational usage scene, or campaign-style hero image with stronger storytelling and atmosphere. Avoid flat lay, avoid ghost mannequin, avoid plain isolated product-only composition unless absolutely necessary"
        )
    if constraints:
        prompt_parts.append(f"Stay conservative about uncertain claims: {constraints}")
    return ". ".join(part for part in prompt_parts if part)


def _compose_cover_background_prompt(page: Dict[str, Any], analysis: Dict[str, Any]) -> str:
    category = str(analysis.get("category") or "product").strip()
    product_name = str(analysis.get("product_name") or category or "product").strip()
    style = str(analysis.get("visual_style") or "premium campaign poster").strip()
    summary = str(page.get("focus") or analysis.get("hero_claim") or analysis.get("product_summary") or "").strip()
    scenes = ", ".join(_safe_string_list(analysis.get("usage_scenes"))[:2])
    points = ", ".join(_safe_string_list(page.get("highlights"))[:3])
    model_prompt = str(page.get("image_prompt_en") or "").strip()
    if model_prompt:
        model_prompt = re.sub(
            r"(?i)\b(flat lay|white studio background|clean white background|studio background|plain isolated product-only composition|ghost mannequin|mannequin)\b",
            "",
            model_prompt,
        )
        model_prompt = re.sub(r"\s{2,}", " ", model_prompt).strip(" ,.")
    constraints = ", ".join(_safe_string_list(analysis.get("visual_constraints"))[:2])

    prompt_parts = [
        f"Premium commercial advertising poster background for {product_name}",
        f"Product category: {category}",
        f"Poster message: {summary}",
        f"Key benefits to visually support: {points}",
        f"Visual style: {style}",
        "Create a cinematic campaign hero scene rather than a standard ecommerce product page",
        "Show the product in a believable aspirational usage scenario with storytelling, atmosphere, depth, and strong visual focus",
        "Prefer a fashion advertising composition, urban winter lifestyle scene, editorial campaign photography, premium magazine poster mood, natural environment context, confident hero framing",
        "Keep the product appearance consistent with the reference image while making the scene feel premium and purchase-driven",
        "No collage, no split panels, no infographic layout, no white seamless studio background, no empty product-only center composition, no flat lay, no mannequin, no ghost mannequin",
        "No text, no typography, no watermark, no logo, no UI, no sticker, no subtitles",
    ]
    if scenes:
        prompt_parts.append(f"Scene inspiration: {scenes}")
    if model_prompt:
        prompt_parts.append(f"Reference product cues only: {model_prompt}")
    if constraints:
        prompt_parts.append(f"Do not imply unverifiable technical claims: {constraints}")
    return ". ".join(part for part in prompt_parts if part)


def _download_image(url: str) -> Image.Image:
    last: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            response = requests.get(url, timeout=180)
            response.raise_for_status()
            image = Image.open(BytesIO(response.content))
            return image.convert("RGB") if image.mode != "RGB" else image
        except Exception as exc:
            last = exc
            if attempt >= 3:
                break
            time.sleep(attempt * 2)
    raise PipelineError(f"Failed to download image after retries: {last}")


def _fit_cover(image: Image.Image, width: int, height: int) -> Image.Image:
    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (int(math.ceil(image.width * scale)), int(math.ceil(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - width) // 2)
    top = max(0, (resized.height - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    return mask


def _font(size: int, bold: bool = False):
    candidates: List[Path] = []
    if os.name == "nt":
        win = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        if bold:
            candidates.extend([win / "msyhbd.ttc", win / "simhei.ttf"])
        candidates.extend([win / "msyh.ttc", win / "simhei.ttf", win / "simsun.ttc"])
    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except Exception:
                continue
    try:
        return ImageFont.truetype("arial.ttf", size=size)
    except Exception:
        return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Any, max_width: int, max_lines: int) -> List[str]:
    source = str(text or "").strip()
    if not source:
        return []

    lines: List[str] = []
    current = ""
    for ch in source:
        candidate = current + ch
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
            continue
        lines.append(current)
        current = ch
        if len(lines) >= max_lines - 1:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and "".join(lines) != source:
        lines[-1] = lines[-1].rstrip(" .") + "..."
    return [line for line in lines if line.strip()]


def _draw_multiline(
    draw: ImageDraw.ImageDraw,
    pos: tuple[int, int],
    text: str,
    font: Any,
    fill: str,
    max_width: int,
    max_lines: int,
    spacing: int,
) -> int:
    x, y = pos
    lines = _wrap(draw, text, font, max_width, max_lines)
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        bbox = draw.textbbox((x, y), line, font=font)
        y = bbox[3] + spacing
    return y


def _draw_multiline_with_shadow(
    draw: ImageDraw.ImageDraw,
    pos: tuple[int, int],
    text: str,
    font: Any,
    fill: str,
    shadow_fill: tuple[int, int, int, int] | str,
    max_width: int,
    max_lines: int,
    spacing: int,
    shadow_offset: tuple[int, int] = (0, 4),
) -> int:
    x, y = pos
    sx, sy = shadow_offset
    lines = _wrap(draw, text, font, max_width, max_lines)
    for line in lines:
        draw.text((x + sx, y + sy), line, font=font, fill=shadow_fill)
        draw.text((x, y), line, font=font, fill=fill)
        bbox = draw.textbbox((x, y), line, font=font)
        y = bbox[3] + spacing
    return y


def _draw_multiline_stroked(
    draw: ImageDraw.ImageDraw,
    pos: tuple[int, int],
    text: str,
    font: Any,
    fill: str,
    stroke_fill: str,
    stroke_width: int,
    max_width: int,
    max_lines: int,
    spacing: int,
) -> int:
    x, y = pos
    lines = _wrap(draw, text, font, max_width, max_lines)
    for line in lines:
        draw.text(
            (x, y),
            line,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        bbox = draw.textbbox((x, y), line, font=font, stroke_width=stroke_width)
        y = bbox[3] + spacing
    return y


def _wrapped_text_height(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: Any,
    max_width: int,
    max_lines: int,
    spacing: int,
) -> tuple[List[str], int]:
    lines = _wrap(draw, text, font, max_width, max_lines)
    if not lines:
        return [], 0
    total_height = 0
    for idx, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        total_height += bbox[3] - bbox[1]
        if idx < len(lines) - 1:
            total_height += spacing
    return lines, total_height


def _render_page(product_image: Image.Image, background: Image.Image, page: Dict[str, Any], config: PipelineConfig) -> Image.Image:
    width = config.page_width
    height = config.page_height
    margin = 64
    accent = "#ff6a1a"
    dark = "#1d1713"
    muted = "#5b5349"

    canvas = Image.new("RGB", (width, height), "#faf8f5")
    draw = ImageDraw.Draw(canvas)

    title_font = _font(78, bold=True)
    subtitle_font = _font(34)
    bullet_font = _font(32)
    badge_font = _font(28, bold=True)
    slot_font = _font(30, bold=True)
    number_font = _font(86, bold=True)
    footer_font = _font(30, bold=True)

    slot_text = str(page.get("slot") or "").upper()
    is_cover = slot_text == "COVER"
    display_index = page.get("display_index")
    if not is_cover and display_index is not None:
        draw.text((margin, 52), f"{int(display_index):02d}", font=number_font, fill=accent)
    slot_bbox = draw.textbbox((0, 0), slot_text, font=slot_font)
    if not is_cover:
        draw.text((width - margin - (slot_bbox[2] - slot_bbox[0]), 78), slot_text, font=slot_font, fill="#9b907f")

    if is_cover:
        title_font = _font(82, bold=True)
        subtitle_font = _font(36)
        highlight_font = _font(28, bold=True)

        hero = _fit_cover(background, width, height)
        canvas.paste(hero, (0, 0))

        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle((0, 0, width, height), fill=(12, 10, 8, 28))
        overlay_draw.rectangle((0, 0, width, height // 2), fill=(0, 0, 0, 10))
        overlay_draw.rectangle((0, height - 520, width, height), fill=(14, 10, 8, 108))
        canvas.paste(overlay, (0, 0), overlay)

        chip_text = str(page.get("badge") or "").strip()
        chip_y = 84
        if chip_text:
            chip_bbox = draw.textbbox((0, 0), chip_text, font=badge_font)
            chip_w = chip_bbox[2] - chip_bbox[0] + 52
            chip_h = chip_bbox[3] - chip_bbox[1] + 22
            chip_x = margin
            draw.rounded_rectangle((chip_x, chip_y, chip_x + chip_w, chip_y + chip_h), radius=26, fill=accent)
            draw.text((chip_x + 26, chip_y + 11), chip_text, font=badge_font, fill="#ffffff")
            title_y = chip_y + chip_h + 28
        else:
            title_y = 96

        title_bottom = _draw_multiline(
            draw,
            (margin, title_y),
            str(page.get("title") or ""),
            title_font,
            "#fffaf4",
            width - margin * 2 - 40,
            3,
            14,
        )
        subtitle_bottom = _draw_multiline(
            draw,
            (margin, title_bottom + 14),
            str(page.get("subtitle") or ""),
            subtitle_font,
            "#f2e8dd",
            width - margin * 2 - 40,
            2,
            10,
        )

        footer_text = str(page.get("footer") or "").strip()
        if footer_text:
            footer_bbox = draw.textbbox((0, 0), footer_text, font=footer_font)
            footer_w = min(width - margin * 2, footer_bbox[2] - footer_bbox[0] + 84)
            footer_y = max(subtitle_bottom + 30, 360)
            draw.rounded_rectangle((margin, footer_y, margin + footer_w, footer_y + 62), radius=30, fill=(255, 245, 235))
            draw.text((margin + 34, footer_y + 13), footer_text, font=footer_font, fill=accent)

        cols = _safe_string_list(page.get("highlights"))[:3]
        chip_x = margin
        chip_y = height - 300
        chip_gap = 18
        for idx2, text in enumerate(cols):
            if not text:
                continue
            lines, text_h = _wrapped_text_height(draw, text, highlight_font, 360, 3, 6)
            text_width = 0
            for line in lines:
                bbox = draw.textbbox((0, 0), line, font=highlight_font)
                text_width = max(text_width, bbox[2] - bbox[0])
            card_w = min(420, text_width + 70)
            card_h = max(60, text_h + 26)
            if chip_x + card_w > width - margin:
                chip_x = margin
                chip_y += card_h + chip_gap
            draw.rounded_rectangle((chip_x, chip_y, chip_x + card_w, chip_y + card_h), radius=26, fill=(255, 248, 240, 236))
            draw.ellipse((chip_x + 18, chip_y + 21, chip_x + 32, chip_y + 35), fill=accent)
            _draw_multiline(draw, (chip_x + 44, chip_y + 12), text, highlight_font, dark, card_w - 58, 3, 6)
            chip_x += card_w + chip_gap
        return canvas

    title_bottom = _draw_multiline(draw, (margin, 168), str(page.get("title") or ""), title_font, dark, width - margin * 2, 2, 10)
    subtitle_bottom = _draw_multiline(
        draw,
        (margin, title_bottom + 10),
        str(page.get("subtitle") or ""),
        subtitle_font,
        muted,
        width - margin * 2,
        2,
        8,
    )

    hero_top = subtitle_bottom + 22
    hero_w = width - margin * 2
    hero_h = max(780, height - hero_top - 160)
    hero = _fit_cover(background, hero_w, hero_h)
    hero_mask = _rounded_mask((hero_w, hero_h), 48)
    canvas.paste(hero, (margin, hero_top), hero_mask)

    chip_text = str(page.get("badge") or "").strip()
    if chip_text:
        chip_bbox = draw.textbbox((0, 0), chip_text, font=badge_font)
        chip_w = chip_bbox[2] - chip_bbox[0] + 44
        chip_h = chip_bbox[3] - chip_bbox[1] + 20
        chip_x = margin + 24
        chip_y = hero_top + 24
        draw.rounded_rectangle((chip_x, chip_y, chip_x + chip_w, chip_y + chip_h), radius=24, fill=accent)
        draw.text((chip_x + 22, chip_y + 10), chip_text, font=badge_font, fill="#ffffff")

    thumb = _fit_cover(product_image, 210, 210)
    thumb_mask = _rounded_mask((210, 210), 32)
    thumb_x = width - margin - 232
    thumb_y = hero_top + 24
    canvas.paste(thumb, (thumb_x, thumb_y), thumb_mask)

    bullet_items = _safe_string_list(page.get("highlights"))[:4]
    bullet_x = margin + 34
    bullet_text_x = bullet_x + 42
    bullet_text_width = min(hero_w - 110, 760)
    bullet_spacing = 10
    bullet_block_gap = 18
    bullet_y_start = hero_top + hero_h - 220
    y = bullet_y_start

    for item in bullet_items:
        lines, text_h = _wrapped_text_height(draw, item, bullet_font, bullet_text_width, 2, bullet_spacing)
        if not lines:
            continue
        dot_y = y + max(8, (text_h // 2) - 8)
        draw.ellipse((bullet_x, dot_y, bullet_x + 16, dot_y + 16), fill=accent)
        _draw_multiline_stroked(
            draw,
            (bullet_text_x, y),
            "".join(lines),
            bullet_font,
            "#fffdf8",
            "#231a14",
            4,
            bullet_text_width,
            2,
            bullet_spacing,
        )
        y += text_h + bullet_block_gap

    footer_text = str(page.get("footer") or "").strip()
    if footer_text:
        footer_y = height - 92
        footer_bbox = draw.textbbox((0, 0), footer_text, font=footer_font)
        footer_w = min(width - margin * 2, footer_bbox[2] - footer_bbox[0] + 80)
        draw.rounded_rectangle((margin, footer_y, margin + footer_w, footer_y + 62), radius=31, fill=accent)
        draw.text((margin + 34, footer_y + 14), footer_text, font=footer_font, fill="#ffffff")

    return canvas


def _compose_long_image(page_paths: List[str], output_path: str, page_gap_px: int) -> Dict[str, Any]:
    if not page_paths:
        raise PipelineError("No page images to compose")
    images = [Image.open(path).convert("RGB") for path in page_paths]
    width = max(img.width for img in images)
    effective_gap = max(0, int(page_gap_px))
    height = sum(img.height for img in images) + effective_gap * (len(images) - 1)
    bg_color = images[0].getpixel((0, 0))
    canvas = Image.new("RGB", (width, height), bg_color)
    y = 0
    for idx, image in enumerate(images):
        canvas.paste(image, (0, y))
        y += image.height
        if idx < len(images) - 1:
            y += effective_gap
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, format="PNG")
    return {"path": str(out), "width": width, "height": height, "page_count": len(images)}


DEFAULT_SELLING_POINTS = ["核心卖点突出", "适合移动端详情页展示", "强调场景价值与使用体验"]
DEFAULT_TRUST_POINTS = ["细节清晰可见", "风格统一专业", "适合电商转化表达"]
DEFAULT_USAGE_SCENES = ["居家场景", "日常使用场景", "近景细节场景"]


def _load_json_response(response: requests.Response) -> Dict[str, Any]:
    candidates: List[str] = []
    raw = response.content or b""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(encoding)
        except Exception:
            continue
        if text not in candidates:
            candidates.append(text)
    if response.text and response.text not in candidates:
        candidates.append(response.text)

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except Exception:
            stripped = candidate.lstrip()
            try:
                payload, _ = json.JSONDecoder().raw_decode(stripped)
            except Exception:
                continue
        if isinstance(payload, dict):
            return payload
    return {"raw_text": (response.text or raw.decode("utf-8", errors="replace"))}


def _comfly_check(self, response: requests.Response) -> Dict[str, Any]:
    payload = _load_json_response(response)
    if response.status_code != 200 or not isinstance(payload, dict):
        raise PipelineError(f"HTTP {response.status_code}: {payload}")
    return payload


ComflyClient._check = _comfly_check


def _pad_points(points: List[str], minimum: int, fallback: List[str]) -> List[str]:
    out = [item for item in points if item]
    seed = [item for item in fallback if item]
    while len(out) < minimum and seed:
        out.append(seed[len(out) % len(seed)])
    while len(out) < minimum:
        out.append(f"补充卖点 {len(out) + 1}")
    return out


def _build_page_slots(analysis: Dict[str, Any], page_count: int) -> List[Dict[str, Any]]:
    points = _pad_points(_safe_string_list(analysis.get("selling_points")), 6, DEFAULT_SELLING_POINTS)
    trust_points = _safe_string_list(analysis.get("trust_points")) or DEFAULT_TRUST_POINTS[:]
    scenes = _safe_string_list(analysis.get("usage_scenes")) or DEFAULT_USAGE_SCENES[:]
    materials = _safe_string_list(analysis.get("materials"))
    structure = _safe_string_list(analysis.get("structure_features"))
    care = _safe_string_list(analysis.get("care_points"))
    certs = _safe_string_list(analysis.get("certification_clues"))
    hero_claim = str(analysis.get("hero_claim") or points[0]).strip()
    summary = str(analysis.get("product_summary") or hero_claim).strip()
    target_page_count = max(1, int(page_count))
    detail_target = max(1, target_page_count - 1)

    hero_slot = {
        "slot": "cover",
        "goal": "Create a premium, non-numbered advertising cover that sells why this product is worth bringing home.",
        "focus": hero_claim,
        "points": points[:3],
        "show_number": False,
    }

    detail_slots: List[Dict[str, Any]] = [
        {"slot": "overview", "goal": "Summarize the top consumer benefits at a glance.", "focus": summary, "points": points[:4], "show_number": True},
        {"slot": "feature", "goal": "Explain the first key feature with a hero visual.", "focus": points[0], "points": [points[0], points[1]]},
        {"slot": "feature", "goal": "Explain the second key feature with a strong comparison feel.", "focus": points[1], "points": [points[1], points[2]]},
        {"slot": "feature", "goal": "Explain the third key feature with detail emphasis.", "focus": points[2], "points": [points[2], points[3]]},
        {"slot": "feature", "goal": "Show another value point that supports conversion.", "focus": points[3], "points": [points[3], points[4]]},
        {"slot": "scene", "goal": "Show the product in a believable daily scene.", "focus": scenes[0], "points": scenes[:3]},
        {
            "slot": "material",
            "goal": "Explain material and structure details.",
            "focus": (materials + structure + care + [hero_claim])[0],
            "points": (materials + structure + care)[:4] or points[2:6],
        },
        {
            "slot": "trust",
            "goal": "Build confidence with conservative proof points.",
            "focus": (trust_points + certs + [hero_claim])[0],
            "points": (trust_points + certs)[:4] or points[1:5],
        },
        {"slot": "closing", "goal": "Close with a summary and buying motivation.", "focus": hero_claim, "points": points[:4]},
    ]

    extra_pool = points[4:] + trust_points + scenes + materials + structure + care + certs
    extra_idx = 0
    while len(detail_slots) < detail_target:
        focus = extra_pool[extra_idx] if extra_idx < len(extra_pool) else f"补充卖点 {len(detail_slots) + 2}"
        point_idx = extra_idx % len(points)
        detail_slots.insert(
            -1,
            {
                "slot": "feature",
                "goal": "Expand one more selling point to make the sequence more complete.",
                "focus": focus,
                "points": [focus] + points[max(0, point_idx - 1) : point_idx + 1],
            },
        )
        extra_idx += 1

    slots = [hero_slot] + detail_slots[:detail_target]
    return slots[:target_page_count]


def _download_image(url: str, retries: int = 5, timeout: int = 180) -> Image.Image:
    last: Optional[Exception] = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            image = Image.open(BytesIO(response.content))
            return image.convert("RGB") if image.mode != "RGB" else image
        except Exception as exc:
            last = exc
            if attempt >= max(1, retries):
                break
            time.sleep(min(12, attempt * 2))
    raise PipelineError(f"Failed to download image after retries: {last}")


DEFAULT_SELLING_POINTS = ["核心卖点突出", "适合移动端详情页展示", "强调场景价值与使用体验"]
DEFAULT_TRUST_POINTS = ["细节清晰可见", "风格统一专业", "适合电商转化表达"]
DEFAULT_USAGE_SCENES = ["居家场景", "日常使用场景", "近景细节场景"]


def _pad_points(points: List[str], minimum: int, fallback: List[str]) -> List[str]:
    out = [item for item in points if item]
    seed = [item for item in fallback if item]
    while len(out) < minimum and seed:
        out.append(seed[len(out) % len(seed)])
    while len(out) < minimum:
        out.append(f"补充卖点 {len(out) + 1}")
    return out


def _build_page_slots(analysis: Dict[str, Any], page_count: int) -> List[Dict[str, Any]]:
    points = _pad_points(_safe_string_list(analysis.get("selling_points")), 6, DEFAULT_SELLING_POINTS)
    trust_points = _safe_string_list(analysis.get("trust_points")) or DEFAULT_TRUST_POINTS[:]
    scenes = _safe_string_list(analysis.get("usage_scenes")) or DEFAULT_USAGE_SCENES[:]
    materials = _safe_string_list(analysis.get("materials"))
    structure = _safe_string_list(analysis.get("structure_features"))
    care = _safe_string_list(analysis.get("care_points"))
    certs = _safe_string_list(analysis.get("certification_clues"))
    hero_claim = str(analysis.get("hero_claim") or points[0]).strip()
    summary = str(analysis.get("product_summary") or hero_claim).strip()
    detail_target = max(1, int(page_count))
    target_page_count = detail_target + 1

    hero_slot = {
        "slot": "cover",
        "goal": "Create a dedicated promotional poster cover before the numbered detail pages, with a strong ad feel and immediate desire to buy.",
        "focus": hero_claim,
        "points": points[:3],
        "show_number": False,
    }

    detail_slots: List[Dict[str, Any]] = [
        {"slot": "overview", "goal": "Summarize the top consumer benefits at a glance.", "focus": summary, "points": points[:4], "show_number": True},
        {"slot": "feature", "goal": "Explain the first key feature with a hero visual.", "focus": points[0], "points": [points[0], points[1]]},
        {"slot": "feature", "goal": "Explain the second key feature with a strong comparison feel.", "focus": points[1], "points": [points[1], points[2]]},
        {"slot": "feature", "goal": "Explain the third key feature with detail emphasis.", "focus": points[2], "points": [points[2], points[3]]},
        {"slot": "feature", "goal": "Show another value point that supports conversion.", "focus": points[3], "points": [points[3], points[4]]},
        {"slot": "scene", "goal": "Show the product in a believable daily scene.", "focus": scenes[0], "points": scenes[:3]},
        {
            "slot": "material",
            "goal": "Explain material and structure details.",
            "focus": (materials + structure + care + [hero_claim])[0],
            "points": (materials + structure + care)[:4] or points[2:6],
        },
        {
            "slot": "trust",
            "goal": "Build confidence with conservative proof points.",
            "focus": (trust_points + certs + [hero_claim])[0],
            "points": (trust_points + certs)[:4] or points[1:5],
        },
        {"slot": "closing", "goal": "Close with a summary and buying motivation.", "focus": hero_claim, "points": points[:4]},
    ]

    extra_pool = points[4:] + trust_points + scenes + materials + structure + care + certs
    extra_idx = 0
    while len(detail_slots) < detail_target:
        focus = extra_pool[extra_idx] if extra_idx < len(extra_pool) else f"补充卖点 {len(detail_slots) + 1}"
        point_idx = extra_idx % len(points)
        detail_slots.insert(
            -1,
            {
                "slot": "feature",
                "goal": "Expand one more selling point to make the sequence more complete.",
                "focus": focus,
                "points": [focus] + points[max(0, point_idx - 1) : point_idx + 1],
            },
        )
        extra_idx += 1

    slots = [hero_slot] + detail_slots[:detail_target]
    return slots[:target_page_count]


def run_pipeline(data: Input) -> Dict[str, Any]:
    config = _build_config(data)
    output_dir = config.output_dir or str(Path(__file__).resolve().parent.parent / "runs")
    logger = RunLogger(output_dir, config, dict(data))

    try:
        product_image = str(data.get("product_image") or "").strip()
        if not product_image:
            raise PipelineError("Missing product_image")

        refs_in = [str(x).strip() for x in data.get("reference_images", []) or [] if str(x).strip()]
        client = ComflyClient(config, logger)

        product_image_url, upload_attempts = client.upload(product_image)
        reference_urls = [product_image_url]
        for ref in refs_in[:5]:
            ref_url, _ = client.upload(ref)
            if ref_url not in reference_urls:
                reference_urls.append(ref_url)
        logger.step(
            "01_upload_inputs",
            "success",
            attempts=upload_attempts,
            payload={"product_image_url": product_image_url, "reference_urls": reference_urls},
        )

        analysis_raw, analysis_attempts = client.analyze_json(
            config.analysis_model,
            _analysis_prompt(config),
            reference_urls,
            "02_analysis",
            max_tokens=4000,
        )
        locale = _locale_defaults(config.platform, config.country, config.language, config.target_market)
        analysis = _normalize_analysis(analysis_raw, locale)
        logger.step("02_analysis", "success", attempts=analysis_attempts, payload=analysis)
        logger.record_usage("analysis", config.analysis_model, "product_analysis", payload={"attempts": analysis_attempts})

        slots = _build_page_slots(analysis, config.page_count)
        logger.step("03_page_slots", "success", attempts=1, payload={"slots": slots})

        copy_raw, copy_attempts = client.analyze_json(
            config.analysis_model,
            _page_copy_prompt(analysis, slots, config),
            reference_urls,
            "04_page_copy_plan",
            max_tokens=8000,
        )
        pages = _normalize_pages(copy_raw, slots, analysis)
        logger.step("04_page_copy_plan", "success", attempts=copy_attempts, payload={"pages": pages})
        logger.record_usage("analysis", config.analysis_model, "page_copy_plan", payload={"attempts": copy_attempts})

        product_local = _download_image(product_image_url)
        page_results: List[Dict[str, Any]] = []
        failed_pages: List[Dict[str, Any]] = []

        def _generate_single_page(page: Dict[str, Any]) -> Dict[str, Any]:
            idx = int(page["index"])
            if str(page.get("slot") or "").strip().lower() == "cover":
                image_prompt = _compose_cover_background_prompt(page, analysis)
            else:
                image_prompt = _compose_page_background_prompt(page, analysis)
            generated = None
            attempts = 0
            last_page_error: Optional[Exception] = None
            for page_attempt in range(1, config.image_generation_retries + 1):
                try:
                    generated, attempts = client.generate_image(
                        config.image_model,
                        image_prompt,
                        config.aspect_ratio,
                        reference_urls,
                        f"05_page_{idx:02d}",
                    )
                    logger.record_usage(
                        "image",
                        config.image_model,
                        f"page_{idx:02d}",
                        payload={"attempts": attempts, "page_attempt": page_attempt},
                    )
                    background_image = _download_image(generated["url"], retries=5, timeout=180)
                    rendered = _render_page(product_local.copy(), background_image, page, config)
                    local_path = logger.run_dir / f"page_{idx:02d}.png"
                    rendered.save(local_path, format="PNG")
                    return {
                        "index": idx,
                        "slot": page["slot"],
                        "title": page["title"],
                        "subtitle": page["subtitle"],
                        "highlights": page["highlights"],
                        "footer": page["footer"],
                        "generated_image_url": generated["url"],
                        "generated_image_prompt": image_prompt,
                        "local_path": str(local_path),
                        "attempts": attempts,
                    }
                except Exception as page_exc:
                    last_page_error = page_exc
                    if page_attempt >= config.image_generation_retries:
                        raise PipelineError(f"Page {idx} failed after retries: {last_page_error}") from page_exc
                    time.sleep(max(1, config.network_retry_delay_seconds) * page_attempt)
            raise PipelineError(f"Page {idx} did not produce an image: {last_page_error}")

        max_workers = max(1, min(config.image_concurrency, len(pages)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(_generate_single_page, page): page for page in pages}
            for future in as_completed(future_map):
                page = future_map[future]
                idx = int(page["index"])
                try:
                    result = future.result()
                    page_results.append(result)
                    logger.page(idx, "final", "success", attempts=int(result.get("attempts") or 0), payload=result)
                except Exception as exc:
                    failed_pages.append({"index": idx, "slot": page["slot"], "error": str(exc)})
                    logger.page(
                        idx,
                        "final",
                        "failed",
                        error=str(exc),
                        payload={"page": page, "traceback": traceback.format_exc()},
                    )

        if not page_results:
            raise PipelineError("All pages failed to generate")
        if failed_pages:
            failed_summary = ", ".join(f"page {item['index']}: {item['error']}" for item in failed_pages)
            raise PipelineError(f"Incomplete output is not allowed, failed pages: {failed_summary}")

        long_image = _compose_long_image(
            [item["local_path"] for item in sorted(page_results, key=lambda x: int(x["index"]))],
            str(logger.run_dir / "detail_long_image.png"),
            config.page_gap_px,
        )
        logger.step("06_compose_long_image", "success", attempts=1, payload=long_image)

        output = {
            "run_dir": str(logger.run_dir),
            "product_image_url": product_image_url,
            "reference_urls": reference_urls,
            "analysis": analysis,
            "pages": pages,
            "page_results": sorted(page_results, key=lambda x: int(x["index"])),
            "failed_pages": failed_pages,
            "final_long_image": long_image,
            "usage": logger.usage_snapshot(),
            "config": {
                "analysis_model": config.analysis_model,
                "image_model": config.image_model,
                "aspect_ratio": config.aspect_ratio,
                "page_count": config.page_count,
                "total_page_count": len(pages),
                "page_width": config.page_width,
                "page_height": config.page_height,
                "page_gap_px": config.page_gap_px,
                "image_concurrency": config.image_concurrency,
            },
        }
        logger.finish("success", output)
        return output
    except Exception:
        logger.finish("failed", {"error": traceback.format_exc()})
        raise


def handler(args: Args[Input]) -> Output:
    try:
        result = run_pipeline(args.input)
        return {"code": 200, "msg": "Pipeline completed successfully", "data": result}
    except Exception as exc:
        return {"code": -500, "msg": f"Pipeline failed: {exc}", "data": None}


def _decode_cli_json(data: bytes) -> Dict[str, Any]:
    last_error: Optional[Exception] = None
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "utf-16-le", "utf-16-be", "gb18030"):
        try:
            return json.loads(data.decode(encoding))
        except Exception as exc:
            last_error = exc
            continue
    raise PipelineError(f"Unable to decode JSON input: {last_error}")


def _load_cli_input() -> Dict[str, Any]:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--input-file", dest="input_file", help="Read JSON input from a file path. Recommended for local testing with Chinese paths.")
    parsed = parser.parse_args()

    if parsed.input_file:
        return _decode_cli_json(Path(parsed.input_file).read_bytes())

    if not os.sys.stdin.isatty():
        stdin_bytes = os.sys.stdin.buffer.read()
        if stdin_bytes.strip():
            return _decode_cli_json(stdin_bytes)
    return {}


if __name__ == "__main__":
    raw = _load_cli_input()
    os.sys.stdout.write(json.dumps(handler(Args(raw)), ensure_ascii=False, indent=2))
