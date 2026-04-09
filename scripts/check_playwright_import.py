"""install.bat Step 2c: verify playwright.async_api imports; write traceback on failure."""
from __future__ import annotations

import sys
import traceback
from pathlib import Path


def main() -> int:
    base = Path(__file__).resolve().parent.parent
    err_path = base / "playwright_import_error.txt"
    try:
        from playwright.async_api import async_playwright  # noqa: F401

        print("[OK] playwright.async_api import OK")
        return 0
    except Exception:
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        try:
            err_path.write_text(tb, encoding="utf-8")
            print(f"(Traceback also saved to {err_path})", file=sys.stderr)
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
