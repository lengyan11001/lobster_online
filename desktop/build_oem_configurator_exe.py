from __future__ import annotations

import subprocess
from pathlib import Path

try:
    from desktop.build_desktop_exe import find_csc
except ModuleNotFoundError:
    from build_desktop_exe import find_csc


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    csc = find_csc()
    if not csc:
        raise SystemExit("csc.exe not found")
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
