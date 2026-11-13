"""视频号获客可行性探测：只读，不登录、不发消息。

用法：
  python scripts/probe_channels_feasibility.py
输出：
  - 视频号助手 Web 是否可达
  - TikHub 关键词搜索 / 视频详情 / 一级评论是否可用
不打印 API Key。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "channels_origin"))

import tikhub_channels  # noqa: E402


def probe_web() -> dict:
    req = urllib.request.Request(
        "https://channels.weixin.qq.com/platform",
        headers={"User-Agent": "Mozilla/5.0 Chrome/124 Safari/537.36"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            title = re.search(r"<title>(.*?)</title>", html, re.S)
            return {"ok": resp.status == 200, "status": resp.status, "title": (title.group(1).strip() if title else "")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def main() -> int:
    summary = {"web": probe_web()}
    try:
        search = tikhub_channels.search_videos("装修", sort="hot", publish_time="week", max_results=3)
        items = search.get("items") or []
        summary["search"] = {"ok": bool(items), "count": len(items), "first_title": (items[0].get("title") if items else "")}
        if items:
            detail = tikhub_channels.video_detail(
                export_id=items[0]["export_id"],
                object_nonce_id=items[0].get("object_nonce_id", ""),
            )
            summary["detail"] = {
                "ok": bool(detail.get("id")),
                "object_id": detail.get("id"),
                "nickname": detail.get("nickname"),
                "username_prefix": str(detail.get("username") or "")[:18],
                "comment_count": detail.get("comment_count"),
            }
            if detail.get("id"):
                comments = tikhub_channels.video_comments(str(detail["id"]), max_comments=5)
                summary["comments"] = {"ok": bool(comments.get("comments")), "count": len(comments.get("comments") or [])}
    except Exception as exc:
        summary["error"] = str(exc)[:300]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("search", {}).get("ok") and summary.get("comments", {}).get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
