#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从速推 / xskill 实时拉取全量模型清单（及可选的每个模型 params_schema）。

供「全量自动体检」第一步使用；与 lobster-server/scripts/audit_model_params.py 使用相同公开接口：
  GET  {BASE}/api/v3/mcp/models
  GET  {BASE}/api/v3/models/{urlencoded_model_id}/docs?lang=zh

环境变量：
  SUTUI_API_BASE   默认 https://api.xskill.ai
  XSKILL_API_KEY   或 SUTUI_SERVER_TOKEN（部分环境拉 /docs 可能需要 Bearer）

禁止兜底：任一步 HTTP 非 2xx 或 JSON 结构缺 data 时进程非零退出。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx


def _base() -> str:
    return (os.environ.get("SUTUI_API_BASE") or "https://api.xskill.ai").rstrip("/")


def _bearer() -> str:
    return (
        os.environ.get("XSKILL_API_KEY", "").strip()
        or os.environ.get("SUTUI_SERVER_TOKEN", "").strip()
    )


def _headers() -> Dict[str, str]:
    h = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    t = _bearer()
    if t:
        h["Authorization"] = f"Bearer {t}"
    return h


async def fetch_model_list(client: httpx.AsyncClient, base: str) -> List[Dict[str, Any]]:
    r = await client.get(f"{base}/api/v3/mcp/models", headers=_headers())
    r.raise_for_status()
    body = r.json()
    if not isinstance(body, dict):
        raise RuntimeError("mcp/models 响应不是 JSON 对象")
    data = body.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("mcp/models 缺少 data 对象")
    models = data.get("models")
    if not isinstance(models, list):
        raise RuntimeError("mcp/models data.models 不是数组")
    return models


async def fetch_one_schema(
    client: httpx.AsyncClient, base: str, model_id: str, lang: str
) -> Dict[str, Any]:
    from urllib.parse import quote

    enc = quote(model_id, safe="")
    r = await client.get(
        f"{base}/api/v3/models/{enc}/docs",
        params={"lang": lang},
        headers=_headers(),
    )
    r.raise_for_status()
    body = r.json()
    if not isinstance(body, dict):
        raise RuntimeError(f"{model_id} docs 响应不是对象")
    data = body.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"{model_id} docs 缺少 data")
    return data


async def fetch_all_schemas(
    base: str,
    model_ids: List[str],
    *,
    lang: str,
    concurrency: int,
) -> Dict[str, Any]:
    sem = asyncio.Semaphore(max(1, concurrency))
    out: Dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=60.0) as client:

        async def one(mid: str) -> None:
            async with sem:
                out[mid] = await fetch_one_schema(client, base, mid, lang)

        await asyncio.gather(*[one(mid) for mid in model_ids])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="拉取速推全量模型清单 / schema 快照")
    ap.add_argument(
        "--out",
        "-o",
        type=Path,
        default=None,
        help="写出 JSON 路径（默认：lobster_online/docs/_generated/xskill_models_<step>.json）",
    )
    ap.add_argument(
        "--schemas",
        action="store_true",
        help="额外拉取每个模型的 /docs 全文（较慢；模型多时请配 XSKILL_API_KEY 避免 401）",
    )
    ap.add_argument("--lang", default="zh", choices=["zh", "en"], help="/docs 语言")
    ap.add_argument(
        "--concurrency",
        type=int,
        default=12,
        help="拉 /docs 并发数",
    )
    ap.add_argument(
        "--category",
        default="",
        help="仅保留 category（如 image / video）；空表示全部",
    )
    args = ap.parse_args()

    base = _base()
    root = Path(__file__).resolve().parent.parent
    gen = root / "docs" / "_generated"
    gen.mkdir(parents=True, exist_ok=True)

    async def run() -> Path:
        async with httpx.AsyncClient(timeout=60.0) as client:
            models = await fetch_model_list(client, base)

        if args.category:
            c = args.category.strip().lower()
            models = [m for m in models if str(m.get("category", "")).lower() == c]

        snapshot: Dict[str, Any] = {
            "source": base,
            "count": len(models),
            "models": models,
        }

        if args.schemas:
            ids = [str(m["id"]) for m in models if m.get("id")]
            snapshot["schemas"] = await fetch_all_schemas(
                base, ids, lang=args.lang, concurrency=args.concurrency
            )

        out_path = args.out
        if out_path is None:
            suffix = "with_schemas" if args.schemas else "list"
            out_path = gen / f"xskill_models_{suffix}.json"

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return out_path

    try:
        path = asyncio.run(run())
    except httpx.HTTPStatusError as e:
        print(f"HTTP {e.response.status_code}: {e.request.url}", file=sys.stderr)
        print(e.response.text[:2000], file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    print(f"OK: {path}")
    print(f"models: {path}  (count 见 JSON 顶层 count)")


if __name__ == "__main__":
    main()
