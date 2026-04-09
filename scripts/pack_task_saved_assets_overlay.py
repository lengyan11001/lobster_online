#!/usr/bin/env python3
"""一键打「task.get_result 终态 saved_assets 兜底」覆盖包 zip，供用户机器解压覆盖验证。

在**本机**工作区执行（需能读 lobster_online 源码）：
  python3 scripts/pack_task_saved_assets_overlay.py

生成：上级目录 lobster_online_overlay_task_saved_assets_<日期>/ 与同名 .zip
"""
from __future__ import annotations

import shutil
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOBSTER = ROOT / "lobster_online"
CHAT = LOBSTER / "backend" / "app" / "api" / "chat.py"
VERIFY_SRC = LOBSTER / "scripts" / "verify_task_result_saved_assets.py"
README_LINES = """在线版补丁包：task.get_result 终态 SSE 视频 saved_assets 兜底

【包含】
  backend/app/api/chat.py
  scripts/verify_task_result_saved_assets.py

【覆盖】
  解压到在线版根目录（与 backend、mcp 同级），全部替换同名文件。

【重启】
  释放 8000 后 ./start_online.sh

【离线验证】
  cd 在线版根目录 && python scripts/verify_task_result_saved_assets.py
  应打印「全部通过」。

【说明】
  仅本机在线版；不上传云端 lobster-server。
""".strip()


def main() -> None:
    if not CHAT.is_file():
        raise SystemExit(f"找不到 {CHAT}，请在含 lobster_online 的工作区运行")
    stamp = datetime.now().strftime("%Y%m%d")
    name = f"lobster_online_overlay_task_saved_assets_{stamp}"
    out_dir = ROOT / name
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "backend" / "app" / "api").mkdir(parents=True)
    (out_dir / "scripts").mkdir(parents=True)
    shutil.copy2(CHAT, out_dir / "backend" / "app" / "api" / "chat.py")
    if VERIFY_SRC.is_file():
        shutil.copy2(VERIFY_SRC, out_dir / "scripts" / "verify_task_result_saved_assets.py")
    else:
        # 最小内联：仅提示从仓库复制
        (out_dir / "scripts" / "verify_task_result_saved_assets.py").write_text(
            "# 请从本仓库 lobster_online/scripts/verify_task_result_saved_assets.py 复制\n",
            encoding="utf-8",
        )
    (out_dir / "README.txt").write_text(README_LINES + "\n", encoding="utf-8")

    zip_path = ROOT / f"{name}.zip"
    if zip_path.is_file():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in out_dir.rglob("*"):
            if p.is_file():
                arc = p.relative_to(out_dir.parent)
                zf.write(p, arc.as_posix())
    print("OK:", out_dir)
    print("OK:", zip_path)


if __name__ == "__main__":
    main()
