"""B 方案：本地 MCP 能力目录缓存（文件没变不再每次读盘解析）。

用户口径（2026-10-01）：工作模式提速，目录读取不要每次都重新解析 30KB JSON；
保持热加载（文件一变立刻生效）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
MCP_DIR = ROOT / "mcp"


def _load_module():
    # mcp 包内有相对导入，必须按包导入
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    module = importlib.import_module("mcp.http_server")
    return module


def test_catalog_is_parsed_once_until_files_change(monkeypatch):
    module = _load_module()
    module._CATALOG_FILE_CACHE["stamp"] = None
    module._CATALOG_FILE_CACHE["value"] = None

    calls = {"n": 0}
    original = module._load_catalog_from_file

    def counting(path):
        calls["n"] += 1
        return original(path)

    monkeypatch.setattr(module, "_load_catalog_from_file", counting)

    first = module._load_capability_catalog()
    after_first = calls["n"]
    second = module._load_capability_catalog()
    assert isinstance(first, dict) and isinstance(second, dict)
    # 一次冷加载会解析 local + base 两个文件；第二次调用必须命中缓存，不能再解析
    assert after_first >= 1, f"expected at least one parse, got {after_first}"
    assert calls["n"] == after_first, f"cache miss: parses went {after_first} -> {calls['n']}"
    assert first == second

    # 文件时间戳一变（模拟热加载）→ 必须重新解析
    stamps = iter([(1, 1), (2, 2), (2, 2), (2, 2)])
    monkeypatch.setattr(module, "_file_stamp", lambda path: next(stamps, (2, 2)))
    module._load_capability_catalog()
    assert calls["n"] > after_first, "hot reload broken: file change did not re-parse"
