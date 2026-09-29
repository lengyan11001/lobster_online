from __future__ import annotations

import sys
from typing import Any


def safe_print(*values: Any, sep: str = " ", end: str = "\n") -> None:
    text = sep.join("" if value is None else str(value) for value in values) + end
    try:
        print(*values, sep=sep, end=end)
        return
    except UnicodeEncodeError:
        pass
    except Exception:
        return

    try:
        stream = getattr(sys, "stdout", None)
        if stream is None:
            return
        # 输出被重定向到文件时（客户端把 stdout 写进 backend.log）必须用 UTF-8：
        # 用 locale 编码（cp936/ascii）会把中文替换成 '?'、写出乱码，线上异常文案全丢。
        try:
            is_tty = bool(stream.isatty())
        except Exception:
            is_tty = False
        encoding = (getattr(stream, "encoding", None) or "utf-8") if is_tty else "utf-8"
        payload = text.encode(encoding, errors="replace")
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            buffer.write(payload)
            buffer.flush()
            return
        stream.write(payload.decode(encoding, errors="replace"))
        stream.flush()
    except Exception:
        return
