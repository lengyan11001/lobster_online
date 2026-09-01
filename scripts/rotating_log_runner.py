#!/usr/bin/env python3
"""Run a child process while keeping its combined output size bounded.

This is used by the Windows batch launchers because shell ``>>`` redirection
has no size limit and bypasses Python's logging handlers.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import BinaryIO


DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 3


def _rotate(path: Path, max_bytes: int, backup_count: int) -> None:
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return
    except OSError:
        return
    try:
        path.with_name(f"{path.name}.{backup_count}").unlink(missing_ok=True)
    except OSError:
        pass
    for index in range(backup_count - 1, 0, -1):
        src = path.with_name(f"{path.name}.{index}")
        dst = path.with_name(f"{path.name}.{index + 1}")
        try:
            if src.exists():
                src.replace(dst)
        except OSError:
            pass
    try:
        path.replace(path.with_name(f"{path.name}.1"))
    except OSError:
        # A concurrent reader may briefly hold the file. Keep writing to the
        # current file rather than interrupting the service.
        pass


def _write_chunk(
    handle: BinaryIO,
    path: Path,
    chunk: bytes,
    max_bytes: int,
    backup_count: int,
) -> BinaryIO:
    if not chunk:
        return handle
    remaining = memoryview(chunk)
    while remaining:
        try:
            current_size = path.stat().st_size if path.exists() else 0
        except OSError:
            current_size = 0
        available = max_bytes - current_size
        if available <= 0:
            try:
                handle.close()
            except OSError:
                pass
            _rotate(path, max_bytes=max_bytes, backup_count=backup_count)
            handle = path.open("ab", buffering=0)
            continue
        part = remaining[:available]
        handle.write(part)
        remaining = remaining[len(part):]
    return handle


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a command with rotating combined output")
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--backup-count", type=int, default=DEFAULT_BACKUP_COUNT)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a command is required after --")
    max_bytes = max(1024, int(args.max_bytes or DEFAULT_MAX_BYTES))
    backup_count = max(1, min(20, int(args.backup_count or DEFAULT_BACKUP_COUNT)))
    path = args.log.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _rotate(path, max_bytes=max_bytes, backup_count=backup_count)
        child = subprocess.Popen(
            command,
            cwd=os.getcwd(),
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        with path.open("ab") as error_log:
            error_log.write(f"[ERR] unable to start child: {exc}\n".encode("utf-8", "replace"))
        return 1

    handle = path.open("ab", buffering=0)
    try:
        assert child.stdout is not None
        read_chunk = getattr(child.stdout, "read1", child.stdout.read)
        while True:
            chunk = read_chunk(64 * 1024)
            if chunk:
                handle = _write_chunk(handle, path, chunk, max_bytes, backup_count)
                continue
            if child.poll() is not None:
                break
        return int(child.wait())
    except KeyboardInterrupt:
        child.terminate()
        return 130
    finally:
        try:
            handle.close()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
