from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


APP_NAME = "必火智能AI"
DEFAULT_BRAND_MARK = "bihuo"


def _read_dotenv_brand(root: Path) -> str:
    env_path = root / ".env"
    if not env_path.is_file():
        return ""
    for raw in env_path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "LOBSTER_BRAND_MARK":
            return value.strip().strip('"').strip("'").lower()
    return ""


def resolve_build_brand(root: Path) -> tuple[str, Path]:
    registry_path = root / "static" / "branding" / "brands.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    marks = registry.get("marks") if isinstance(registry.get("marks"), dict) else {}
    default_mark = str(registry.get("default_mark") or DEFAULT_BRAND_MARK).strip().lower() or DEFAULT_BRAND_MARK
    requested = str(os.environ.get("LOBSTER_BRAND_MARK") or _read_dotenv_brand(root) or default_mark).strip().lower()
    mark = requested if requested in marks else default_mark
    config = marks.get(mark) if isinstance(marks.get(mark), dict) else {}
    install = config.get("install") if isinstance(config.get("install"), dict) else {}
    icon_rel = str(install.get("desktop_ico") or "static/bihu_box.ico").strip().replace("/", os.sep)
    return mark, root / icon_rel


def find_csc() -> str | None:
    csc = shutil.which("csc") or shutil.which("csc.exe")
    if csc:
        return csc
    candidates = [
        Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
        Path(r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"),
        Path(r"C:\Windows\Microsoft.NET\Framework64\v3.5\csc.exe"),
        Path(r"C:\Windows\Microsoft.NET\Framework\v3.5\csc.exe"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    brand_mark, icon = resolve_build_brand(root)
    if not icon.is_file():
        raise SystemExit(f"品牌 {brand_mark} 的桌面图标不存在：{icon}")

    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    out = dist / f"{APP_NAME}.exe"
    csc = find_csc()
    if not csc:
        raise SystemExit("找不到 csc.exe，无法构建轻量启动器。请确认已安装 .NET Framework SDK 或 Visual Studio Build Tools。")
    cmd = [
        csc,
        "/nologo",
        "/target:winexe",
        "/platform:x64",
        "/optimize+",
        f"/out:{out}",
        f"/win32icon:{icon}",
        "/reference:System.Windows.Forms.dll",
        str(root / "desktop" / "launcher_stub.cs"),
    ]
    subprocess.check_call(cmd, cwd=str(root))

    root_out = root / f"{APP_NAME}.exe"
    root_out.write_bytes(out.read_bytes())

    print()
    print(f"[desktop] Brand: {brand_mark}")
    print(f"[desktop] Icon: {icon}")
    print(f"[desktop] Built: {out}")
    print(f"[desktop] Copied lightweight launcher to: {root_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
