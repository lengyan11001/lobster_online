#!/usr/bin/env python3
"""
与仓库根目录 pack_code_only.sh 同清单、同排除（见 docs/生产打包流程.md ②′ 纯代码包）。
供 Windows 等未安装 Info-Zip「zip」命令的环境使用；产物名与 sh 一致：lobster_online_code_only_<时间戳>.zip
"""
from __future__ import annotations

import datetime as _dt
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _is_excluded(rel_posix: str) -> bool:
    if not rel_posix:
        return True
    if "/__pycache__/" in f"/{rel_posix}/" or rel_posix.startswith("__pycache__/") or "/__pycache__" in rel_posix:
        return True
    if rel_posix.endswith(".pyc"):
        return True
    if rel_posix.endswith(".db"):
        return True
    if "probe_three_out" in rel_posix:
        return True
    if rel_posix.endswith(".DS_Store") or "/.DS_Store" in rel_posix:
        return True
    if rel_posix == "sutui_config.json":
        return True
    if rel_posix == "openclaw/.env":
        return True
    if rel_posix.startswith("openclaw/workspace/"):
        return True
    if rel_posix.startswith("browser_data/"):
        return True
    if rel_posix.startswith("assets/"):
        return True
    return False


def _add_file(zf: zipfile.ZipFile, root: Path, rel: str) -> None:
    p = root / rel
    if not p.is_file():
        return
    rp = rel.replace("\\", "/")
    if _is_excluded(rp):
        return
    zf.write(p, rp, compress_type=zipfile.ZIP_DEFLATED)


def _add_tree(zf: zipfile.ZipFile, root: Path, dir_rel: str) -> None:
    d = root / dir_rel
    if not d.is_dir():
        return
    for p in d.rglob("*"):
        if p.is_dir():
            continue
        rel = p.relative_to(root).as_posix()
        if _is_excluded(rel):
            continue
        zf.write(p, rel, compress_type=zipfile.ZIP_DEFLATED)


def _bat_payload(path: Path) -> bytes:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")
    nl = text.replace("\r\n", "\n").replace("\r", "\n")
    crlf = "\r\n".join(nl.split("\n")) + ("\r\n" if nl.endswith("\n") else "")
    try:
        return crlf.encode("gbk")
    except UnicodeEncodeError:
        return crlf.encode("utf-8")


def main() -> int:
    root = ROOT
    build_id = _dt.datetime.now().strftime("%Y%m%d%H%M%S")
    out_name = f"lobster_online_code_only_{build_id}.zip"
    out_path = root / out_name

    for old in root.glob("lobster_online_code_only_*.zip"):
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass

    dirs = [
        "backend",
        "mcp",
        "static",
        "scripts",
        "publisher",
        "skills",
        "docs",
    ]
    files: list[str] = [
        "deps/ffmpeg/README.txt",
        "skill_registry.json",
        "upstream_urls.json",
        "openclaw/openclaw.json",
        "requirements.txt",
        "requirements.runtime.txt",
        ".env.example",
        "README-一键使用.txt",
        "README.md",
        "使用说明-完整包.txt",
        "使用说明-精简包.txt",
        "单机版启动脚本_完整包.txt",
        "nodejs/package.json",
        "nodejs/package-lock.json",
    ]

    pols = [
        "openclaw/workspace/LOBSTER_CHAT_POLICY_INTRO.md",
        "openclaw/workspace/LOBSTER_CHAT_POLICY_TOOLS.md",
    ]

    count = 0
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for dr in dirs:
            before = len(zf.namelist())
            _add_tree(zf, root, dr)
            count += len(zf.namelist()) - before
        for fr in files:
            before = len(zf.namelist())
            _add_file(zf, root, fr)
            count += len(zf.namelist()) - before
        # 与 sh 中 for bat in *.bat：根目录全部 .bat 以 GBK+CRLF 写入
        for bat in sorted(root.glob("*.bat")):
            zf.writestr(bat.name, _bat_payload(bat), compress_type=zipfile.ZIP_DEFLATED)
            count += 1
        for pol in pols:
            before = len(zf.namelist())
            _add_file(zf, root, pol)
            count += len(zf.namelist()) - before

        env = root / ".env"
        if env.is_file():
            zf.write(env, ".env", compress_type=zipfile.ZIP_DEFLATED)
            count += 1
            print("  (packed root .env)")

    mb = out_path.stat().st_size / (1024 * 1024)
    print(f"[OK] code-only zip: {out_name} ({mb:.2f} MB, ~{count} entries)")
    print("  Same manifest as pack_code_only.sh; excludes deps/wheels, ffmpeg.exe, pack_bundle.env")
    print(f"  路径: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
