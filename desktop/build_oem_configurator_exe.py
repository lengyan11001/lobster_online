from __future__ import annotations

import subprocess
from pathlib import Path

try:
    from desktop.build_desktop_exe import find_csc, resolve_build_brand
except ModuleNotFoundError:
    from build_desktop_exe import find_csc, resolve_build_brand


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    csc = find_csc()
    if not csc:
        raise SystemExit("csc.exe not found")
    # 配置器是用户最先看到的入口，图标必须跟当前品牌一致（之前没传 /win32icon，
    # 资源管理器里一直是 C# 默认图标）。
    _brand_mark, icon = resolve_build_brand(root)
    output = root / "OEM配置启动器.exe"
    subprocess.check_call(
        [
            csc,
            "/nologo",
            "/target:winexe",
            "/platform:x64",
            "/optimize+",
            "/codepage:65001",
            f"/out:{output}",
            f"/win32icon:{icon}",
            "/reference:System.Drawing.dll",
            "/reference:System.Windows.Forms.dll",
            str(root / "desktop" / "oem_configurator_stub.cs"),
        ],
        cwd=str(root),
    )
    print(f"[oem-configurator] Built: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
