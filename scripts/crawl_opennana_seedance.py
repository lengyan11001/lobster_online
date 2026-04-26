#!/usr/bin/env python3
"""Fetch OpenNana Seedance 2.0 video prompt examples for the video studio."""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests


API_BASE = "https://api.opennana.com/api"
SOURCE_URL = "https://opennana.com/awesome-prompt-gallery?media_type=video&model=Seedance%202.0"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "static" / "data" / "comfly-seedance-tvc-examples.json"
LIST_LIMIT = 100
MAX_WORKERS = 12
REQUEST_TIMEOUT = 24


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0 Safari/537.36"
            )
        }
    )
    return session


def request_json(session: requests.Session, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError(f"Unexpected payload type from {url!r}")
            return payload
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < 2:
                time.sleep(0.45 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_error}") from last_error


def fetch_summaries(session: requests.Session) -> list[dict[str, Any]]:
    params = {
        "page": 1,
        "limit": LIST_LIMIT,
        "sort": "reviewed_at",
        "order": "DESC",
        "model": "Seedance 2.0",
        "media_type": "video",
    }
    first_payload = request_json(session, f"{API_BASE}/prompts", params=params)
    data = first_payload.get("data") or {}
    pagination = data.get("pagination") or {}
    total_pages = int(pagination.get("total_pages") or 1)
    items = list(data.get("items") or [])

    for page in range(2, total_pages + 1):
        params["page"] = page
        payload = request_json(session, f"{API_BASE}/prompts", params=params)
        items.extend((payload.get("data") or {}).get("items") or [])

    deduped: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or item.get("_is_sponsor"):
            continue
        slug = str(item.get("slug") or "").strip()
        if not slug or slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        deduped.append(item)
    return deduped


def extract_prompt_text(detail: dict[str, Any]) -> tuple[str, str]:
    prompt_en = ""
    prompt_zh = ""
    prompts = detail.get("prompts")
    if isinstance(prompts, list):
        for entry in prompts:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text") or "").strip()
            kind = str(entry.get("type") or "").strip().lower()
            if not text:
                continue
            if kind == "zh" and not prompt_zh:
                prompt_zh = text
            elif kind == "en" and not prompt_en:
                prompt_en = text
        if not prompt_zh:
            prompt_zh = next((str(entry.get("text") or "").strip() for entry in prompts if isinstance(entry, dict) and entry.get("text")), "")
        if not prompt_en:
            prompt_en = next((str(entry.get("text") or "").strip() for entry in prompts if isinstance(entry, dict) and entry.get("text")), "")
    return prompt_en, prompt_zh


def normalize_tags(detail: dict[str, Any]) -> list[str]:
    tags = detail.get("tags")
    if not isinstance(tags, list):
        return []
    return [str(tag or "").strip() for tag in tags if str(tag or "").strip()]


def first_string(values: Any) -> str:
    if isinstance(values, list):
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
    return ""


def fetch_detail_row(slug: str) -> dict[str, Any]:
    session = build_session()
    payload = request_json(session, f"{API_BASE}/prompts/{slug}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"Missing detail data for slug {slug!r}")

    prompt_en, prompt_zh = extract_prompt_text(data)
    images = data.get("images") if isinstance(data.get("images"), list) else []
    video_url = first_string(data.get("video_urls"))
    cover_image = str(data.get("cover_image") or first_string(images)).strip()

    return {
        "id": str(data.get("id") or slug),
        "slug": str(data.get("slug") or slug).strip(),
        "title": str(data.get("title") or "").strip() or "Seedance 2.0 案例",
        "prompt": prompt_zh or prompt_en,
        "prompt_zh": prompt_zh,
        "prompt_en": prompt_en,
        "cover_image": cover_image,
        "video_url": video_url,
        "model": str(data.get("model") or "Seedance 2.0").strip() or "Seedance 2.0",
        "tags": normalize_tags(data),
        "language": "zh" if prompt_zh else "en",
        "is_featured": True,
        "featured_order": int(data.get("featured_order") or 0),
        "author": str(data.get("source_name") or data.get("submitter_name") or "").strip(),
        "source_url": str(data.get("source_url") or "").strip(),
    }


def fetch_all_rows(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slugs = [str(item.get("slug") or "").strip() for item in summaries if str(item.get("slug") or "").strip()]
    rows_by_slug: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {executor.submit(fetch_detail_row, slug): slug for slug in slugs}
        for index, future in enumerate(as_completed(future_map), start=1):
            slug = future_map[future]
            row = future.result()
            rows_by_slug[slug] = row
            print(f"[{index}/{len(slugs)}] fetched {slug}")
    return [rows_by_slug[slug] for slug in slugs if slug in rows_by_slug and rows_by_slug[slug].get("prompt")]


def save_payload(rows: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda row: (not row.get("is_featured"), int(row.get("featured_order") or 999999), str(row.get("title") or "")))
    payload = {
        "source": "OpenNana",
        "source_url": SOURCE_URL,
        "api_source": f"{API_BASE}/prompts",
        "model": "Seedance 2.0",
        "media_type": "video",
        "total_count": len(rows),
        "featured_count": sum(1 for row in rows if row.get("is_featured")),
        "zh_count": sum(1 for row in rows if row.get("prompt_zh")),
        "en_count": sum(1 for row in rows if row.get("prompt_en") and not row.get("prompt_zh")),
        "last_updated": time.strftime("%Y-%m-%d"),
        "data_fields": [
            "id",
            "title",
            "slug",
            "prompt",
            "prompt_zh",
            "prompt_en",
            "cover_image",
            "video_url",
            "model",
            "tags",
            "language",
            "is_featured",
            "author",
            "source_url",
        ],
        "prompts": rows,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch OpenNana Seedance 2.0 video prompts.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = Path(args.output).resolve()
    session = build_session()
    summaries = fetch_summaries(session)
    print(f"Found {len(summaries)} OpenNana Seedance 2.0 video prompt summaries.")
    rows = fetch_all_rows(summaries)
    save_payload(rows, output_path)
    print(f"Saved {len(rows)} rows to {output_path}")


if __name__ == "__main__":
    main()
