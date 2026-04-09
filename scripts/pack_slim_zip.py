#!/usr/bin/env python3
"""
精简包 zip：解决 Windows 解压后中文文件名乱码、.bat 需 GBK 问题。
- 产物名：lobster_online_slim_<品牌>_<时间戳>.zip（品牌来自 LOBSTER_BRAND_MARK，默认 yingshi；仅 ASCII）
- zip 内路径：关键中文文件改为 ASCII 别名（内容不变，仅改名）
- 所有 .bat：UTF-8 源码 → GBK + CRLF 写入 zip（cmd 兼容）
其余文件：原样二进制写入。
"""
from __future__ import annotations

import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

PROJ = "lobster_online"
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
PARENT = ROOT.parent

# 与 pack_slim.sh 一致；另排除制包垃圾与重复说明
SKIP_DIR_PREFIXES: tuple[tuple[str, ...], ...] = (
    (PROJ, ".git"),
    (PROJ, "python"),
    (PROJ, "nodejs"),
    (PROJ, "deps"),
    (PROJ, "browser_chromium"),
    (PROJ, "browser_data"),
    (PROJ, "assets"),
    (PROJ, "node_modules"),
    (PROJ, "docs"),  # 与完整项目包一致：交付包不含 docs（源码仓维护）
    (PROJ, "openclaw", "workspace"),
    (PROJ, "scripts", "_probe_three_out"),
)

SKIP_NAME_EXACT = frozenset(
    {
        ".DS_Store",
        "使用说明.txt",  # 旧制包临时副本
        "使用说明-完整包.txt",
        "单机版启动脚本_完整包.txt",
        "mcp.log",
        "openclaw.log",
        "pack_bundle.env",
        "修复MCP服务未就绪.md",
        "诊断MCP连接问题.md",
    }
)


def parts_under_proj(rel: Path) -> tuple[str, ...]:
    p = rel.as_posix()
    if not p.startswith(PROJ + "/"):
        raise ValueError(p)
    return tuple(Path(p).parts)


def is_excluded(rel: Path) -> bool:
    """rel 相对于 PARENT，如 lobster_online/backend/..."""
    parts = parts_under_proj(rel)
    name = parts[-1] if parts else ""

    if name in SKIP_NAME_EXACT:
        return True
    if name.endswith(".pyc") or name.endswith(".db"):
        return True
    if "__pycache__" in parts:
        return True

    for prefix in SKIP_DIR_PREFIXES:
        if len(parts) >= len(prefix) and parts[: len(prefix)] == prefix:
            return True

    tail = parts[1:]  # 去掉 lobster_online
    if tail:
        if tail[0].startswith("lobster_online") and name.endswith(".zip"):
            return True
        if tail[0].startswith("lobster_code") and name.endswith(".zip"):
            return True
        if name == "explore_douyin.py":
            return True
        if name.startswith("douyin_") and name.endswith(".png"):
            return True
        if name.startswith("douyin_") and name.endswith(".json"):
            return True
    if name.endswith(".tar.gz"):
        return True
    return False


# zip 内路径：中文名 → ASCII（避免解压工具/资源管理器乱码）
ARCNAME_REPLACE: dict[str, str] = {
    "一键启动.bat": "quick_start.bat",
    "使用说明-精简包.txt": "README_SLIM.txt",
    "README-一键使用.txt": "README_QUICKSTART.txt",
    "桌面图标说明.txt": "README_ICON_DESKTOP.txt",
    "必火AI_BOX.icns": "BihuAI_BOX.icns",
}


def map_arcname(posix_path: str) -> str:
    for old, new in ARCNAME_REPLACE.items():
        if posix_path.endswith("/" + old):
            return posix_path[: -len(old)] + new
    return posix_path


def bat_payload_gbk(path: Path) -> bytes:
    raw = path.read_text(encoding="utf-8")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = raw.replace("\n", "\r\n")
    return raw.encode("gbk", errors="replace")


def _brand_slug() -> str:
    """与 pack_slim.sh 一致：LOBSTER_BRAND_MARK → 文件名安全片段（默认 yingshi）。"""
    raw = (os.environ.get("LOBSTER_BRAND_MARK") or "yingshi").strip().lower()
    s = re.sub(r"[^a-z0-9_-]+", "", raw)
    return s or "yingshi"


def main() -> int:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    brand = _brand_slug()
    out_zip = PARENT / f"lobster_online_slim_{brand}_{ts}.zip"

    count = 0
    with zipfile.ZipFile(
        out_zip,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as zf:
        for dirpath, dirnames, filenames in os.walk(ROOT):
            # prune excluded dirs in-place
            dpath = Path(dirpath)
            rel_dir = dpath.relative_to(PARENT)
            keep_dirs: list[str] = []
            for d in list(dirnames):
                trial = rel_dir / d
                if is_excluded(trial):
                    dirnames.remove(d)
                else:
                    keep_dirs.append(d)
            for fn in filenames:
                fp = Path(dirpath) / fn
                rel = fp.relative_to(PARENT)
                if is_excluded(rel):
                    continue
                arc = map_arcname(rel.as_posix())
                if fp.suffix.lower() == ".bat":
                    data = bat_payload_gbk(fp)
                    zi = zipfile.ZipInfo(arc)
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    zf.writestr(zi, data)
                else:
                    zf.write(fp, arcname=arc)
                count += 1

    size_mb = out_zip.stat().st_size / (1024 * 1024)
    print(f"[OK] 已生成: {out_zip.name} ({size_mb:.1f} MB, {count} 个文件)")
    print(f"  路径: {out_zip}")
    print("  解压后请运行 install_slim.bat，说明见 README_SLIM.txt；一键安装并启动用 quick_start.bat（原「一键启动」）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
