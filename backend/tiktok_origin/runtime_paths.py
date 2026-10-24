import os
import shutil
import sys
from pathlib import Path

APP_RUNTIME_DIRNAME = "\u5fc5\u706bAI\u83b7\u5ba2"
LEGACY_RUNTIME_DIRNAMES = (
    "\u7ebf\u7d22\u96f7\u8fbe",
    "AI\u83b7\u5ba2",
    "AI\u83b7\u5ba2\u7cfb\u7edf",
)


def resolve_install_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resolve_runtime_root(install_dir: Path) -> Path:
    if not getattr(sys, "frozen", False):
        return install_dir

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data).resolve() / APP_RUNTIME_DIRNAME
    return install_dir


def merge_missing_tree(source_dir: Path, target_dir: Path):
    if not source_dir.exists() or not source_dir.is_dir():
        return

    for source_path in source_dir.rglob("*"):
        relative_path = source_path.relative_to(source_dir)
        target_path = target_dir / relative_path
        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if not target_path.exists():
            shutil.copy2(source_path, target_path)


def iter_legacy_runtime_dirs(runtime_dir: Path):
    parent_dir = runtime_dir.parent
    if not parent_dir.exists():
        return
    seen = set()
    for dirname in LEGACY_RUNTIME_DIRNAMES:
        candidate = parent_dir / dirname
        candidate_key = str(candidate).lower()
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        if candidate == runtime_dir:
            continue
        if candidate.exists() and candidate.is_dir():
            yield candidate


def migrate_legacy_runtime_dirs(install_dir: Path, runtime_dir: Path):
    if not getattr(sys, "frozen", False):
        return
    runtime_dir.mkdir(parents=True, exist_ok=True)

    source_dirs = []
    if install_dir != runtime_dir:
        source_dirs.append(install_dir)
    source_dirs.extend(iter_legacy_runtime_dirs(runtime_dir))

    for source_dir in source_dirs:
        merge_missing_tree(source_dir / "data", runtime_dir / "data")
        merge_missing_tree(source_dir / "logs", runtime_dir / "logs")
