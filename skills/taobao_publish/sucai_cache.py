"""淘宝素材库持久化缓存：本地文件 md5 → 远端 imgBox src 的稳定片段（OID）。

设计目标
--------
1) 同一账号 / 平台下，上传过到素材库的图，下次再要用就不再重复上传；直接在 iframe
   网格里按 OID 定位 imgBox 点击即可。
2) 保存的"远端 src"做裁剪：
   - 丢掉 `?t=...` 这些带时效性的 query
   - 抽取 OID（`O1CN01...` 一串）作为最稳的命中指纹；全 src 作为降级匹配

表结构
------
CREATE TABLE sucai_map (
    platform TEXT NOT NULL,
    account  TEXT NOT NULL,
    md5      TEXT NOT NULL,
    remote_src  TEXT NOT NULL,   -- 已剥离 ?query 的完整 URL
    remote_oid  TEXT,            -- 从 URL 抽出的 O1CN01.. 片段（最稳）
    filename    TEXT,
    file_size   INTEGER,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP,
    PRIMARY KEY (platform, account, md5)
);

公共函数
--------
- compute_md5(path) -> str
- bulk_lookup(platform, account, md5s) -> Dict[md5, Entry]
- put(platform, account, md5, remote_src, filename=None, file_size=None)
- mark_used(platform, account, md5s)
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = os.path.join(
    os.environ.get("LOBSTER_DATA_DIR", r"E:\lobster_online\data"),
    "sucai_cache.sqlite",
)

_DB_PATH = os.environ.get("TAOBAO_SUCAI_CACHE_DB", _DEFAULT_DB_PATH)
_lock = threading.Lock()

_OID_RE = re.compile(r"(O1CN[0-9A-Za-z]+)", re.I)


@dataclass
class SucaiEntry:
    md5: str
    remote_src: str
    remote_oid: Optional[str]
    filename: Optional[str]
    file_size: Optional[int]


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=10.0)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sucai_map (
            platform TEXT NOT NULL,
            account  TEXT NOT NULL,
            md5      TEXT NOT NULL,
            remote_src  TEXT NOT NULL,
            remote_oid  TEXT,
            filename    TEXT,
            file_size   INTEGER,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used_at TIMESTAMP,
            PRIMARY KEY (platform, account, md5)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sucai_oid ON sucai_map(platform, account, remote_oid)"
    )
    conn.commit()
    return conn


def strip_query(url: str) -> str:
    if not url:
        return url
    q = url.find("?")
    return url[:q] if q >= 0 else url


def extract_oid(url: str) -> Optional[str]:
    if not url:
        return None
    m = _OID_RE.search(url)
    return m.group(1) if m else None


def compute_md5(path: str, _buf_size: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_buf_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def bulk_lookup(platform: str, account: str, md5s: Iterable[str]) -> Dict[str, SucaiEntry]:
    md5_list = [m for m in md5s if m]
    if not md5_list:
        return {}
    with _lock:
        conn = _connect()
        try:
            out: Dict[str, SucaiEntry] = {}
            CHUNK = 400
            for i in range(0, len(md5_list), CHUNK):
                sub = md5_list[i : i + CHUNK]
                placeholder = ",".join(["?"] * len(sub))
                rows = conn.execute(
                    f"""
                    SELECT md5, remote_src, remote_oid, filename, file_size
                    FROM sucai_map
                    WHERE platform = ? AND account = ? AND md5 IN ({placeholder})
                    """,
                    [platform, account, *sub],
                ).fetchall()
                for md5, remote_src, remote_oid, filename, file_size in rows:
                    out[md5] = SucaiEntry(
                        md5=md5,
                        remote_src=remote_src,
                        remote_oid=remote_oid,
                        filename=filename,
                        file_size=file_size,
                    )
            return out
        finally:
            conn.close()


def put(
    platform: str,
    account: str,
    md5: str,
    remote_src: str,
    filename: Optional[str] = None,
    file_size: Optional[int] = None,
) -> None:
    if not (md5 and remote_src):
        return
    clean_src = strip_query(remote_src)
    oid = extract_oid(clean_src)
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO sucai_map (platform, account, md5, remote_src, remote_oid, filename, file_size, last_used_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(platform, account, md5) DO UPDATE SET
                    remote_src = excluded.remote_src,
                    remote_oid = excluded.remote_oid,
                    filename   = COALESCE(excluded.filename, sucai_map.filename),
                    file_size  = COALESCE(excluded.file_size, sucai_map.file_size),
                    last_used_at = CURRENT_TIMESTAMP
                """,
                [platform, account, md5, clean_src, oid, filename, file_size],
            )
            conn.commit()
        finally:
            conn.close()


def mark_used(platform: str, account: str, md5s: Iterable[str]) -> None:
    md5_list = [m for m in md5s if m]
    if not md5_list:
        return
    with _lock:
        conn = _connect()
        try:
            placeholder = ",".join(["?"] * len(md5_list))
            conn.execute(
                f"UPDATE sucai_map SET last_used_at = CURRENT_TIMESTAMP "
                f"WHERE platform = ? AND account = ? AND md5 IN ({placeholder})",
                [platform, account, *md5_list],
            )
            conn.commit()
        finally:
            conn.close()


def db_path() -> str:
    return _DB_PATH
