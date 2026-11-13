"""视频号获客运行路径与状态库。"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_BACKEND_DIR = _HERE.parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from douyin_origin.runtime_paths import resolve_install_dir, resolve_runtime_root  # noqa: E402

INSTALL_DIR = resolve_install_dir()
RUNTIME_ROOT = resolve_runtime_root(INSTALL_DIR)
DATA_DIR = RUNTIME_ROOT / "data"
LOG_DIR = RUNTIME_ROOT / "logs"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

STATE_DB_FILE = DATA_DIR / "channels_ui_state.db"
EXPORT_DIR = DATA_DIR / "channels_exports"
CHANNELS_UI_DUMP_DIR = DATA_DIR / "channels_ui_dumps"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
CHANNELS_UI_DUMP_DIR.mkdir(parents=True, exist_ok=True)
