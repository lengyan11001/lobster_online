from __future__ import annotations

import json
import sys

import comfly_seedance_storyboard_pipeline as pipeline


def main() -> int:
    raw = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    result = pipeline.run_pipeline(raw)
    pipeline._write_json_stdout(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
