"""
通过 stdin JSON 调用 comfly_storyboard_pipeline.py（与 OpenClaw/本地脚本约定一致）。

示例中的 apikey、base_url、product_image 与联调日志一致；真密钥请勿提交公开仓库，用完可轮换。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "comfly_storyboard_pipeline.py"
# app.log / COMFLY_PIPE_HTTP_DEBUG 同款
APIKEY = "sk-FtluOvNh9Vu1w3DMpfquIpcKxK5jbkPI1YZmMm0Hci24oymp"
BASE_URL = "https://ai.comfly.chat"
PRODUCT_IMAGE = "https://cdn-video.51sux.com/mcp-images/20260401/f136eb57-b075-48bb-b9a7-ebaaa414c1b9.png"

payload = {
    "product_image": PRODUCT_IMAGE,
    "apikey": APIKEY,
    "base_url": BASE_URL,
    "storyboard_count": 1,
    "shot_concurrency": 1,
    "merge_clips": False,
    "aspect_ratio": "9:16",
}

if __name__ == "__main__":
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    sys.stdout.write(proc.stdout or "")
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    try:
        out = json.loads(proc.stdout or "{}")
        print("code:", out.get("code"), "msg:", out.get("msg"), file=sys.stderr)
    except json.JSONDecodeError:
        print("stdout 非 JSON，raw 长:", len(proc.stdout or ""), file=sys.stderr)
    raise SystemExit(proc.returncode)
