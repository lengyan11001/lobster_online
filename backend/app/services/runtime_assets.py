"""运行期按需资源：不再打进 OTA 主包，首次运行从 TOS 拉取。

为什么：
- OTA 每次都是全量包，字体 5 个 OTF 压缩后仍占 ~20.9MB，
  把包从 ~33MB 撑到 ~54MB；字体属于内容资源而不是代码。
- 改成「启动时后台补拉 + sha256 校验」，OTA 只带几 KB 的清单逻辑。

行为：
- 文件已存在且 sha256 匹配 -> 跳过（不产生任何网络请求）。
- 缺失或损坏 -> 下载到 `.part` 再校验后原子替换。
- 全部在 daemon 线程里跑，不阻塞后端启动；失败只记日志，不影响主流程。
- 手动补拉：`python scripts/fetch_cutcli_fonts.py`。
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

TOS_ROOT = "https://lobster-online-assets-2103871705.tos-cn-guangzhou.volces.com"
_FONT_BASE = f"{TOS_ROOT}/assets/cutcli_templates/fonts"

# rel = 安装根目录下的相对路径；sha256 必须与 TOS 上的对象一致。
RUNTIME_ASSETS: tuple[dict[str, str], ...] = (
    {
        "rel": "static/fonts/AlibabaPuHuiTi-3-105-Heavy.otf",
        "url": f"{_FONT_BASE}/AlibabaPuHuiTi-3-105-Heavy.otf",
        "sha256": "f2e848f94ac746f5a51ef1d71caea5cc268c920a9a63740423ee61abeac89bd6",
    },
    {
        "rel": "static/fonts/AlibabaPuHuiTi-3-95-ExtraBold.otf",
        "url": f"{_FONT_BASE}/AlibabaPuHuiTi-3-95-ExtraBold.otf",
        "sha256": "a8fb15c8685836d5041a4c47f2917fd57117182aff01350ea0335b216cc7993e",
    },
    {
        "rel": "static/fonts/AlibabaPuHuiTi-3-75-SemiBold.otf",
        "url": f"{_FONT_BASE}/AlibabaPuHuiTi-3-75-SemiBold.otf",
        "sha256": "fd789c70fa4f1a265fe11a64d29bb48926aef7e14da338cb5ca5e6c359ae7e72",
    },
    {
        "rel": "static/fonts/AlibabaPuHuiTi-3-115-Black.otf",
        "url": f"{_FONT_BASE}/AlibabaPuHuiTi-3-115-Black.otf",
        "sha256": "3832ce0df4aa0d5493c759e6b16e128efc884950ceeb8e047a6443479816d921",
    },
    {
        "rel": "static/fonts/AlibabaPuHuiTi-3-65-Medium.otf",
        "url": f"{_FONT_BASE}/AlibabaPuHuiTi-3-65-Medium.otf",
        "sha256": "00cd19ccd391bba61515361eea87163ba079c4bf691fa63c59e280fda690ada4",
    },
)

DEFAULT_ROOT = Path(__file__).resolve().parents[3]
_FETCH_LOCK = threading.Lock()
_FETCH_STARTED = False


def repo_root() -> Path:
    return DEFAULT_ROOT


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def is_valid(path: Path, asset: dict[str, str]) -> bool:
    """文件存在、大小合理、sha256 与清单一致才算有效。"""
    try:
        if not path.is_file() or path.stat().st_size < 1024:
            return False
        return _sha256(path) == str(asset.get("sha256") or "").lower()
    except OSError:
        return False


def missing_assets(root: Path | None = None, assets: tuple[dict[str, str], ...] | None = None) -> list[dict[str, str]]:
    base = Path(root) if root is not None else DEFAULT_ROOT
    items = assets if assets is not None else RUNTIME_ASSETS
    out: list[dict[str, str]] = []
    for asset in items:
        rel = str(asset.get("rel") or "")
        if not rel:
            continue
        if not is_valid(base / rel.replace("/", os.sep), asset):
            out.append(asset)
    return out


def ensure_assets(
    root: Path | None = None,
    assets: tuple[dict[str, str], ...] | None = None,
    *,
    timeout: float = 180.0,
    log=logger.info,
) -> list[str]:
    """补齐缺失资源，返回本次真正下载的相对路径列表。失败不改动旧文件。"""
    base = Path(root) if root is not None else DEFAULT_ROOT
    downloaded: list[str] = []
    for asset in missing_assets(base, assets):
        rel = str(asset["rel"])
        target = base / rel.replace("/", os.sep)
        url = str(asset.get("url") or "")
        sha = str(asset.get("sha256") or "").lower()
        if not url or not sha:
            log("[assets] skip %s: manifest incomplete", rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LobsterClientRuntimeAssets/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp, tmp.open("wb") as fh:
                for chunk in iter(lambda: resp.read(1024 * 256), b""):
                    fh.write(chunk)
            got = _sha256(tmp)
            if got != sha:
                tmp.unlink(missing_ok=True)
                log("[assets] sha256 mismatch %s: %s != %s", rel, got, sha)
                continue
            tmp.replace(target)
            downloaded.append(rel)
            log("[assets] fetched %s (%s bytes)", rel, target.stat().st_size)
        except Exception as exc:  # 网络/磁盘异常都不该影响主流程
            tmp.unlink(missing_ok=True)
            log("[assets] fetch failed %s: %s", rel, exc)
    return downloaded


def start_background_fetch(root: Path | None = None) -> threading.Thread | None:
    """幂等启动一次后台补拉；已启动过则返回 None。"""
    global _FETCH_STARTED
    with _FETCH_LOCK:
        if _FETCH_STARTED:
            return None
        _FETCH_STARTED = True

    def _runner() -> None:
        try:
            pending = missing_assets(root)
            if not pending:
                return
            logger.info("[assets] runtime assets missing=%s, fetching in background", len(pending))
            ensure_assets(root)
        except Exception as exc:
            logger.warning("[assets] background fetch failed: %s", exc)

    thread = threading.Thread(target=_runner, name="runtime-assets-fetch", daemon=True)
    thread.start()
    return thread
