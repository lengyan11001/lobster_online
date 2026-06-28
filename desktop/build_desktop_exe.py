from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


APP_NAME = "必火智能AI"


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

    icon = root / "static" / "bihu_box.ico"

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
    print(f"[desktop] Built: {out}")
    print(f"[desktop] Copied lightweight launcher to: {root_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
