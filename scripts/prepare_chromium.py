"""Pre-download Playwright Chromium for offline deployment.

Run on a machine with internet access:
  python scripts/prepare_chromium.py

This downloads Chromium to lobster/browser_chromium/ which can be
copied to target devices for fully offline install.
"""
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BROWSER_DIR = BASE_DIR / "browser_chromium"


def main():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSER_DIR)
    BROWSER_DIR.mkdir(exist_ok=True)

    print(f"[1/2] Installing playwright package...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright>=1.49.0", "-q"])

    print(f"[2/2] Downloading Chromium to {BROWSER_DIR}...")
    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])

    size = sum(f.stat().st_size for f in BROWSER_DIR.rglob("*") if f.is_file())
    print(f"\nDone! Chromium downloaded to: {BROWSER_DIR}")
    print(f"Size: {size / (1024 * 1024):.1f} MB")
    print(f"\nCopy the entire 'browser_chromium' folder into the lobster package for offline use.")


if __name__ == "__main__":
    main()
