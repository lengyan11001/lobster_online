#!/usr/bin/env python3
"""本地联调 Comfly 风格 API：与 backend comfly_veo_exec 默认路径一致。

监听 127.0.0.1:8765：
  POST /v1/files                 — multipart field「file」；返回 id/object/bytes/created_at/filename/url（与 Comfly 文档一致）
  POST /v1/chat/completions      — OpenAI 兼容；可选 body.tools（Gemini 预设如 googleSearch）；返回含 prompts 的正文
  POST /v2/videos/generations    — Comfly 文档风格：body 含 prompt/model/images，返回 task_id
  POST /v1/video/jobs           — 旧版相对路径（仍支持）
  GET  /v2/videos/generations/{task_id} — Comfly 文档风格轮询（默认）
  GET  /v1/video/jobs/{id}      — 旧版相对路径（仍支持）

.env 示例：
  COMFLY_API_BASE=http://127.0.0.1:8765/v1
  COMFLY_API_KEY=mock
"""
from __future__ import annotations

import json
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse


def _send_json(handler: BaseHTTPRequestHandler, status: int, obj: dict) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print("[mock-comfly]", self.address_string(), fmt % args)

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""

        if path == "/v1/files":
            _send_json(
                self,
                200,
                {
                    "id": "file-mock-" + str(uuid.uuid4())[:8],
                    "object": "file",
                    "bytes": max(0, n),
                    "created_at": 0,
                    "filename": "upload.bin",
                    "url": "https://example.com/mock-comfly-files-upload.png",
                },
            )
            return

        try:
            _ = json.loads(raw.decode("utf-8", errors="replace") if raw else "{}")
        except json.JSONDecodeError:
            _send_json(self, 400, {"error": "invalid json"})
            return

        if path == "/v1/chat/completions":
            content = json.dumps(
                {
                    "prompts": [
                        "Slow dolly-in on product, soft studio light, 9:16.",
                        "Hand lifts product slightly, macro detail, clean background.",
                        "360 subtle rotation, premium packaging highlight.",
                        "Overhead flat lay with gentle shadow, minimal style.",
                        "Close-up texture sweep, shallow depth of field.",
                    ]
                },
                ensure_ascii=False,
            )
            out = {
                "id": "mock-chatcmpl",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            }
            _send_json(self, 200, out)
            return

        if path == "/v1/video/jobs":
            tid = str(uuid.uuid4())
            _send_json(self, 200, {"task_id": tid, "data": {"id": tid, "status": "queued"}})
            return

        if path == "/v2/videos/generations":
            b = _ if isinstance(_, dict) else {}
            if not (b.get("prompt") and isinstance(b.get("images"), list) and b.get("images")):
                _send_json(self, 400, {"error": "need prompt and non-empty images[]"})
                return
            tid = str(uuid.uuid4())
            _send_json(self, 200, {"task_id": tid, "data": {"id": tid, "status": "queued"}})
            return

        _send_json(self, 404, {"error": "not found", "path": path})

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        tid = ""
        p_v2 = "/v2/videos/generations/"
        if path.startswith(p_v2):
            tid = path[len(p_v2) :].strip("/")
        prefix = "/v1/video/jobs/"
        if not tid and path.startswith(prefix) and path.rstrip("/") != prefix.rstrip("/"):
            tid = path[len(prefix) :].strip("/")
        if not tid:
            _send_json(self, 404, {"error": "not found", "path": path})
            return
        # 与 Comfly 文档 GET /v2/videos/generations/{task_id} 一致：status=SUCCESS，视频在 data.output
        _send_json(
            self,
            200,
            {
                "task_id": tid,
                "platform": "google",
                "action": "google-videos",
                "status": "SUCCESS",
                "fail_reason": "",
                "submit_time": 0,
                "start_time": 0,
                "finish_time": 0,
                "progress": "100%",
                "data": {"output": "https://example.com/mock-veo-output.mp4"},
                "search_item": "",
            },
        )


def main() -> None:
    host, port = "127.0.0.1", 8765
    httpd = HTTPServer((host, port), Handler)
    print(f"Mock Comfly listening on http://{host}:{port}")
    print("Use COMFLY_API_BASE=http://127.0.0.1:8765/v1  COMFLY_API_KEY=mock")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
