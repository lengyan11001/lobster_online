#!/usr/bin/env python3
"""
Fetch all ChatGPT image prompts from OpenNana and save them as project-local JSON.

Default output matches the existing image studio example file path so the frontend
can use the refreshed data without any extra manual steps.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests


API_BASE = "https://api.opennana.com/api"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "static" / "data" / "comfly-image-studio-examples.json"
LIST_LIMIT = 100
MAX_WORKERS = 12
REQUEST_TIMEOUT = 20


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
                time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_error}") from last_error


def fetch_summaries(session: requests.Session) -> list[dict[str, Any]]:
    first_payload = request_json(
        session,
        f"{API_BASE}/prompts",
        params={
            "page": 1,
            "limit": LIST_LIMIT,
            "sort": "reviewed_at",
            "order": "DESC",
            "model": "ChatGPT",
            "media_type": "image",
        },
    )
    data = first_payload.get("data") or {}
    pagination = data.get("pagination") or {}
    total_pages = int(pagination.get("total_pages") or 1)
    items = list(data.get("items") or [])

    for page in range(2, total_pages + 1):
        payload = request_json(
            session,
            f"{API_BASE}/prompts",
            params={
                "page": page,
                "limit": LIST_LIMIT,
                "sort": "reviewed_at",
                "order": "DESC",
                "model": "ChatGPT",
                "media_type": "image",
            },
        )
        page_items = (payload.get("data") or {}).get("items") or []
        items.extend(page_items)

    deduped: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
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
            if kind == "en" and not prompt_en:
                prompt_en = text
            elif kind == "zh" and not prompt_zh:
                prompt_zh = text
        if not prompt_en:
            for entry in prompts:
                if isinstance(entry, dict):
                    text = str(entry.get("text") or "").strip()
                    if text:
                        prompt_en = text
                        break
        if not prompt_zh:
            for entry in prompts:
                if isinstance(entry, dict):
                    text = str(entry.get("text") or "").strip()
                    kind = str(entry.get("type") or "").strip().lower()
                    if text and kind != "en":
                        prompt_zh = text
                        break
    return prompt_en, prompt_zh


def normalize_tags(detail: dict[str, Any]) -> list[str]:
    tags = detail.get("tags")
    if not isinstance(tags, list):
        return []
    normalized: list[str] = []
    for tag in tags:
        value = str(tag or "").strip()
        if value:
            normalized.append(value)
    return normalized


def fetch_detail_row(slug: str) -> dict[str, Any]:
    session = build_session()
    payload = request_json(session, f"{API_BASE}/prompts/{slug}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"Missing detail data for slug {slug!r}")
    prompt_en, prompt_zh = extract_prompt_text(data)
    images = data.get("images") if isinstance(data.get("images"), list) else []
    return {
        "id": data.get("id"),
        "slug": str(data.get("slug") or slug).strip(),
        "title": str(data.get("title") or "").strip(),
        "media_type": str(data.get("media_type") or "image").strip() or "image",
        "cover_image": str(data.get("cover_image") or (images[0] if images else "")).strip(),
        "prompt_en": prompt_en,
        "prompt_zh": prompt_zh,
        "tags": normalize_tags(data),
        "model": str(data.get("model") or "ChatGPT").strip() or "ChatGPT",
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
    return [rows_by_slug[slug] for slug in slugs if slug in rows_by_slug]


def save_rows(rows: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch all OpenNana ChatGPT image prompts.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output JSON path. Defaults to the image studio example dataset.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = Path(args.output).resolve()
    session = build_session()
    summaries = fetch_summaries(session)
    print(f"Found {len(summaries)} prompt summaries.")
    rows = fetch_all_rows(summaries)
    save_rows(rows, output_path)
    with_prompt_en = sum(1 for row in rows if row.get("prompt_en"))
    with_prompt_zh = sum(1 for row in rows if row.get("prompt_zh"))
    print(f"Saved {len(rows)} rows to {output_path}")
    print(f"Rows with English prompt: {with_prompt_en}")
    print(f"Rows with Chinese prompt: {with_prompt_zh}")


if __name__ == "__main__":
    main()
