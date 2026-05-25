#!/usr/bin/env python3
"""Patch OpenClaw MCP tools/call timeout above the SDK default 60s.

OpenClaw calls MCP tools through @modelcontextprotocol/sdk. When no timeout is
passed, that SDK waits only 60000ms. Long image/video generations can exceed
that while the upstream job still succeeds, causing the model to retry and
create duplicate jobs. This patch raises all OpenClaw MCP tool calls to a
default 600000ms, configurable by LOBSTER_OPENCLAW_MCP_TOOL_TIMEOUT_MS.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "nodejs" / "node_modules" / "openclaw" / "dist"
MARKER = "LOBSTER_OPENCLAW_MCP_TOOL_TIMEOUT_MS"

CALL_RE = re.compile(
    r"(?P<indent>[ \t]*)return\s+await\s+session\.client\.callTool\(\{\s*"
    r"name:\s*toolName\s*,\s*"
    r"arguments:\s*isMcpConfigRecord\(input\)\s*\?\s*input\s*:\s*\{\}\s*"
    r"\}\s*\)\s*;",
    re.DOTALL,
)


def apply_patch(text: str) -> tuple[str, bool]:
    def repl(match: re.Match[str]) -> str:
        indent = match.group("indent") or ""
        inner = indent + "\t"
        return (
            f"{indent}const lobsterToolTimeoutMsRaw = Number(process.env.LOBSTER_OPENCLAW_MCP_TOOL_TIMEOUT_MS ?? 600000);\n"
            f"{indent}const lobsterToolTimeoutMs = Number.isFinite(lobsterToolTimeoutMsRaw) && lobsterToolTimeoutMsRaw > 0 ? Math.floor(lobsterToolTimeoutMsRaw) : 600000;\n"
            f"{indent}return await session.client.callTool({{\n"
            f"{inner}name: toolName,\n"
            f"{inner}arguments: isMcpConfigRecord(input) ? input : {{}}\n"
            f"{indent}}}, void 0, {{ timeout: lobsterToolTimeoutMs }});"
        )

    new_text, count = CALL_RE.subn(repl, text or "", count=1)
    return new_text, bool(count)


def main() -> int:
    if not DIST.is_dir():
        print(f"[skip] openclaw dist not found: {DIST}", file=sys.stderr)
        return 0

    candidates = sorted(DIST.glob("content-blocks-*.js"))
    if not candidates:
        print(f"[skip] content-blocks bundle not found under: {DIST}", file=sys.stderr)
        return 0

    changed = False
    already_patched = False
    missed = 0
    for target in candidates:
        text = target.read_text(encoding="utf-8")
        if MARKER in text:
            already_patched = True
            print(f"[ok] already patched {target}")
            continue
        new_text, patched = apply_patch(text)
        if not patched:
            missed += 1
            print(f"[warn] compatible callTool snippet not found; skipped: {target}", file=sys.stderr)
            continue
        target.write_text(new_text, encoding="utf-8")
        changed = True
        print(f"[ok] patched {target}")

    if not changed and not already_patched and missed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
