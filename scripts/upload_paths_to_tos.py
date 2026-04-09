#!/usr/bin/env python3
"""Upload local files to Volcengine TOS using custom_configs.json TOS_CONFIG. Streams large files."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import tos

_ROOT = Path(__file__).resolve().parent.parent
_CFG = _ROOT / "custom_configs.json"


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: upload_paths_to_tos.py <object_key_prefix/> <file1> [file2 ...]", file=sys.stderr)
        sys.exit(2)
    prefix = sys.argv[1].strip().rstrip("/") + "/"
    paths = [Path(p) for p in sys.argv[2:]]
    data = json.loads(_CFG.read_text(encoding="utf-8"))
    tc = (data.get("configs") or {}).get("TOS_CONFIG")
    if not isinstance(tc, dict):
        print("TOS_CONFIG missing in custom_configs.json", file=sys.stderr)
        sys.exit(1)
    ak = str(tc.get("access_key", "")).strip()
    sk = str(tc.get("secret_key", "")).strip()
    endpoint = str(tc.get("endpoint", "")).strip()
    region = str(tc.get("region", "")).strip()
    bucket = str(tc.get("bucket_name", "")).strip()
    public_domain = str(tc.get("public_domain", "")).strip().rstrip("/")
    client = tos.TosClientV2(ak, sk, endpoint, region)

    for p in paths:
        if not p.is_file():
            print(f"skip (not a file): {p}", file=sys.stderr)
            continue
        key = prefix + p.name
        size = p.stat().st_size
        ctype = "application/zip" if p.suffix.lower() == ".zip" else "application/octet-stream"
        print(f"uploading {p.name} ({size} bytes) -> {key} ...", flush=True)
        with open(p, "rb") as f:
            client.put_object(
                bucket,
                key,
                content=f,
                content_length=size,
                content_type=ctype,
            )
        url = f"{public_domain}/{key}"
        print(url, flush=True)


if __name__ == "__main__":
    main()
