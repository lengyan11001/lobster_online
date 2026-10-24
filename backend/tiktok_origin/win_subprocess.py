from __future__ import annotations

import os
import subprocess
from typing import Any, Dict


def hidden_subprocess_kwargs() -> Dict[str, Any]:
    if os.name != "nt":
        return {}

    kwargs: Dict[str, Any] = {}
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
    if creationflags:
        kwargs["creationflags"] = creationflags

    startupinfo_factory = getattr(subprocess, "STARTUPINFO", None)
    startf_use_showwindow = getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    sw_hide = getattr(subprocess, "SW_HIDE", 0)
    if startupinfo_factory and startf_use_showwindow:
        startupinfo = startupinfo_factory()
        startupinfo.dwFlags |= startf_use_showwindow
        startupinfo.wShowWindow = sw_hide
        kwargs["startupinfo"] = startupinfo

    return kwargs


def run_hidden(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    merged = {**hidden_subprocess_kwargs(), **kwargs}
    return subprocess.run(*args, **merged)


def popen_hidden(*args: Any, **kwargs: Any) -> subprocess.Popen:
    merged = {**hidden_subprocess_kwargs(), **kwargs}
    return subprocess.Popen(*args, **merged)
