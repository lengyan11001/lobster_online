#!/usr/bin/env python3
"""
Create the same archive as pack_full_project.sh (zip -r ... -x ...) without external zip.exe.
Paths inside the zip: <proj>/<relative path from proj root>.
"""
from __future__ import annotations

import fnmatch
import argparse
import os
import sys
import zipfile
from pathlib import Path


FACTORY_CONFIGURATOR_EXE = "OEM\u914d\u7f6e\u542f\u52a8\u5668.exe"


def should_exclude(proj: str, rel_posix: str, *, factory_oem: bool = False) -> bool:
    """rel_posix: relative path under project root, forward slashes, no leading slash."""
    if not rel_posix:
        return True
    parts = rel_posix.split("/")
    if ".git" in parts:
        return True
    if any("__pycache__" in p for p in parts):
        return True
    if rel_posix.endswith(".DS_Store"):
        return True

    root_name = parts[0] if len(parts) == 1 else None
    lower_rel = rel_posix.lower()

    if parts[-1].lower() == "machine_identity.json":
        return True

    if factory_oem:
        if rel_posix == ".env":
            return True
        if root_name and root_name.lower().endswith(".exe"):
            return root_name != FACTORY_CONFIGURATOR_EXE
        if root_name and (root_name.startswith("test_") or root_name.startswith("_tmp_")):
            return True
        if root_name in {
            ".gitattributes",
            ".gitignore",
            ".impeccable.md",
            ".installed",
            "@AutomationLog.txt",
            "_probe_apiz_sdk.zip",
            "build_bihuo_log.txt",
            "client_code_manifest_1.0.7.json",
            "custom_configs.json",
            "models_config.json",
            "oem_configurator.log",
            "run_mock_comfly.bat",
            "test_localsystem.txt",
            "twilio_whatsapp_config.json",
            "wecom_cloud_config.json",
        }:
            return True
        if root_name and root_name.lower().endswith(".spec"):
            return True
        if root_name and (
            fnmatch.fnmatch(root_name, "youtube_accounts_*.json")
            or fnmatch.fnmatch(root_name, "xskill_*captured_requests*.json*")
        ):
            return True
        if rel_posix.startswith((
            ".cursor/",
            ".pack_python_shim/",
            ".pytest_cache/",
            "_probe_apiz_sdk_src/",
            "data/",
            "temp_assets/",
            "wxauto_logs/",
            "wxautox\u6587\u4ef6\u4e0b\u8f7d/",
            "\u7d20\u6750\u5e93/",
        )):
            return True

    if lower_rel.endswith((".pyc", ".pyo", ".log", ".db", ".sqlite", ".sqlite3", ".tmp", ".temp")):
        return True
    if lower_rel.endswith(".bak") or ".bak." in lower_rel or ".db.bak" in lower_rel:
        return True
    if rel_posix == "openclaw/.env":
        return True
    if rel_posix == "openclaw/identity" or rel_posix.startswith("openclaw/identity/"):
        return True
    if rel_posix.startswith("openclaw/workspace/"):
        return True
    if rel_posix.startswith("openclaw/workspace-"):
        return True
    if rel_posix.startswith((
        "openclaw/.openclaw/",
        "openclaw/agents/",
        "openclaw/browser/",
        "openclaw/logs/",
        "openclaw/memory/",
        "openclaw/tasks/",
        "openclaw/user_memory/",
    )):
        return True
    if rel_posix in {
        "openclaw/.channel_fallback.json",
        "openclaw/.lobster_plugin_state_backup.json",
        "openclaw/.weixin_login_last.json",
        "openclaw/update-check.json",
    }:
        return True
    if rel_posix.startswith("browser_data/"):
        return True
    if rel_posix.startswith("browser_chromium/"):
        return True
    if rel_posix.startswith(("_pack_exe_test/", "_lobster_runtime/", "dist/", "build/", "tmp_responsive_check/", "tmp_templates/", ".updates/", "release_updates/")):
        return True
    if rel_posix.startswith("desktop/webview2/fixed-runtime/"):
        return True
    if rel_posix.startswith("assets/"):
        return True
    if rel_posix.startswith("static/uploads/"):
        return True
    if rel_posix.startswith("static/branding/cache/"):
        return True
    if rel_posix.startswith("chat_storage/"):
        return True
    if rel_posix == "sutui_config.json":
        return True
    if rel_posix == "pack_bundle.env":
        return True
    if rel_posix.startswith("logs/"):
        return True
    if rel_posix.startswith("docs/"):
        return True

    if rel_posix.startswith("openclaw/browser/"):
        return True
    _skill_runtime = {"runs", "job_runs", "output", "cache"}
    if len(parts) >= 3 and parts[0] == "skills" and parts[2] in _skill_runtime:
        return True
    if root_name and root_name in ("backend.log", "backend_err.log", "mcp.log"):
        return True

    if root_name:
        if root_name.endswith(".log"):
            return True
        if fnmatch.fnmatch(root_name, "backend*.log") or fnmatch.fnmatch(root_name, "mcp*.log"):
            return True
        if root_name in ("lobster.exe", "lobster_fixed.exe"):
            return True
        if fnmatch.fnmatch(root_name, "build_*.txt"):
            return True
        if fnmatch.fnmatch(root_name, f"{proj}_*.zip"):
            return True
        if fnmatch.fnmatch(root_name, "*.tar.gz"):
            return True
        if root_name == "explore_douyin.py":
            return True
        if fnmatch.fnmatch(root_name, "douyin_*.png") or fnmatch.fnmatch(root_name, "douyin_*.json"):
            return True
        if fnmatch.fnmatch(root_name, "media_edit_skill_bundle_*.zip"):
            return True
        if fnmatch.fnmatch(root_name, "lobster_online_code_*.zip"):
            return True
        if fnmatch.fnmatch(root_name, "lobster_code_*.zip"):
            return True
        if fnmatch.fnmatch(root_name, "xskill_*.json") or fnmatch.fnmatch(root_name, "xskill_*.jsonl"):
            return True
        if root_name == "openclaw.log":
            return True
        if root_name == "installed_packages.json":
            return True
        if root_name == "mcp_registry_cache.json":
            return True
        if root_name == "test_mcp.py":
            return True
        if fnmatch.fnmatch(root_name, "pack_*.sh"):
            return True
        if root_name == "pack_full_project.sh":
            return True
        if root_name == "build_package.sh":
            return True
        if fnmatch.fnmatch(root_name, "使用说明*.txt"):
            return True
        if root_name == "README-一键使用.txt":
            return True
        if root_name == "README.md":
            return True
        if fnmatch.fnmatch(root_name, "单机版启动脚本*.txt"):
            return True
        if root_name == "修复MCP服务未就绪.md":
            return True
        if root_name == "诊断MCP连接问题.md":
            return True

    if rel_posix == "static/桌面图标说明.txt":
        return True

    script_excludes = {
        "scripts/ensure_full_pack_deps.sh",
        "scripts/ensure_pack_deps.sh",
        "scripts/pack_media_edit_skill.sh",
        "scripts/build_result_package.sh",
        "scripts/sync_deps_for_pack.sh",
        "scripts/report_pack_gaps.py",
    }
    if rel_posix in script_excludes:
        return True

    if rel_posix.startswith("nodejs/node_modules/thread-stream/test/") and rel_posix.endswith(".zip"):
        return True

    return False


def main() -> int:
    if os.environ.get("LOBSTER_ALLOW_PLAIN_PACK") != "1":
        print(
            "[ERR] 拒绝生成明文完整版：正式包必须使用 "
            "_pack_exe_test/build_encrypted_dist.py；仅本机调试时设置 "
            "LOBSTER_ALLOW_PLAIN_PACK=1",
            file=sys.stderr,
        )
        return 2
    parser = argparse.ArgumentParser(
        description="Create a complete project ZIP without requiring an external zip executable."
    )
    parser.add_argument("parent_dir")
    parser.add_argument("proj_dirname")
    parser.add_argument("out_zip_path")
    parser.add_argument(
        "--factory-oem",
        action="store_true",
        help="Build a generic factory package without local brand configuration or brand launchers.",
    )
    args = parser.parse_args()
    parent = Path(args.parent_dir).resolve()
    proj = args.proj_dirname
    out_zip = Path(args.out_zip_path).resolve()
    proj_root = parent / proj
    if not proj_root.is_dir():
        print(f"[ERR] Not a directory: {proj_root}", file=sys.stderr)
        return 1

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    if out_zip.exists():
        out_zip.unlink()

    count = 0
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(proj_root, followlinks=False):
            # Prune .git early
            if ".git" in dirnames:
                dirnames.remove(".git")
            for name in filenames:
                full = Path(dirpath) / name
                try:
                    rel = full.relative_to(proj_root)
                except ValueError:
                    continue
                rel_posix = rel.as_posix()
                if should_exclude(proj, rel_posix, factory_oem=args.factory_oem):
                    continue
                arcname = f"{proj}/{rel_posix}"
                zf.write(full, arcname, compress_type=zipfile.ZIP_DEFLATED)
                count += 1

    print(f"pack_full_project_zip.py: added {count} files -> {out_zip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
