#!/usr/bin/env python3
"""Patch OpenClaw MCP tools/call timeout above the SDK default 60s.

OpenClaw calls MCP tools through @modelcontextprotocol/sdk. When no timeout is
passed, that SDK waits only 60000ms. Long image/video generations can exceed
that while the upstream job still succeeds, causing the model to retry and
create duplicate jobs. This patch raises all OpenClaw MCP tool calls to a
default 600000ms, configurable by LOBSTER_OPENCLAW_MCP_TOOL_TIMEOUT_MS.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "nodejs" / "node_modules" / "openclaw" / "dist"
MARKER = "LOBSTER_OPENCLAW_MCP_TOOL_TIMEOUT_MS"

OLD = """\t\t\treturn await session.client.callTool({
\t\t\t\tname: toolName,
\t\t\t\targuments: isMcpConfigRecord(input) ? input : {}
\t\t\t});"""

NEW = """\t\t\tconst lobsterToolTimeoutMsRaw = Number(process.env.LOBSTER_OPENCLAW_MCP_TOOL_TIMEOUT_MS ?? 600000);
\t\t\tconst lobsterToolTimeoutMs = Number.isFinite(lobsterToolTimeoutMsRaw) && lobsterToolTimeoutMsRaw > 0 ? Math.floor(lobsterToolTimeoutMsRaw) : 600000;
\t\t\treturn await session.client.callTool({
\t\t\t\tname: toolName,
\t\t\t\targuments: isMcpConfigRecord(input) ? input : {}
\t\t\t}, void 0, { timeout: lobsterToolTimeoutMs });"""


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
        if OLD not in text:
            missed += 1
            print(f"[warn] expected snippet not found; manual merge needed: {target}", file=sys.stderr)
            continue
        target.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
        changed = True
        print(f"[ok] patched {target}")

    if not changed and not already_patched and missed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
