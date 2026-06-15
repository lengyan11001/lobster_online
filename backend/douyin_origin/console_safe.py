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
        encoding = getattr(stream, "encoding", None) or "utf-8"
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
