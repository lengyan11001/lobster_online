from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pymysql


class RuntimeStateStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _backup_malformed_db(self):
        if not self.db_path.exists():
            return
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.db_path.with_name(f"{self.db_path.stem}.malformed_{timestamp}{self.db_path.suffix}")
        try:
            if backup_path.exists():
                backup_path.unlink()
        except Exception:
            pass
        self.db_path.replace(backup_path)

    def _reset_malformed_db(self):
        self._backup_malformed_db()
        wal_path = self.db_path.with_suffix(f"{self.db_path.suffix}-wal")
        shm_path = self.db_path.with_suffix(f"{self.db_path.suffix}-shm")
        for extra_path in (wal_path, shm_path):
            try:
                if extra_path.exists():
                    extra_path.unlink()
            except Exception:
                pass

    def _ensure_schema(self):
        with self._lock:
            try:
                self._ensure_schema_once()
            except sqlite3.DatabaseError as exc:
                message = str(exc).lower()
                if "malformed" not in message and "disk image is malformed" not in message:
                    raise
                self._reset_malformed_db()
                self._ensure_schema_once()

    def _ensure_schema_once(self):
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS state_blobs (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS search_history_items (
                    key TEXT PRIMARY KEY,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    seen_count INTEGER NOT NULL DEFAULT 0,
                    keywords_json TEXT NOT NULL DEFAULT '[]',
                    last_title TEXT NOT NULL DEFAULT '',
                    last_author TEXT NOT NULL DEFAULT '',
                    last_url TEXT NOT NULL DEFAULT '',
                    last_cover_image TEXT NOT NULL DEFAULT '',
                    last_author_avatar TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS douyin_tasks (
                    task_id INTEGER PRIMARY KEY,
                    url TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    author TEXT NOT NULL DEFAULT '',
                    cover_image TEXT NOT NULL DEFAULT '',
                    source_session_id TEXT NOT NULL DEFAULT '',
                    source_item_key TEXT NOT NULL DEFAULT '',
                    likes INTEGER NOT NULL DEFAULT 0,
                    publish_time TEXT NOT NULL DEFAULT '',
                    comment_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT NOT NULL DEFAULT '',
                    video_comment_status TEXT NOT NULL DEFAULT 'pending',
                    video_comment_error TEXT NOT NULL DEFAULT '',
                    video_comment_mode TEXT NOT NULL DEFAULT 'fixed',
                    video_comment_prompt TEXT NOT NULL DEFAULT '',
                    video_comment_seed_text TEXT NOT NULL DEFAULT '',
                    video_comment_summary TEXT NOT NULL DEFAULT '',
                    video_comment_text TEXT NOT NULL DEFAULT '',
                    video_comment_account_id TEXT NOT NULL DEFAULT '',
                    video_comment_started_at TEXT NOT NULL DEFAULT '',
                    video_comment_finished_at TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS douyin_task_comments (
                    task_id INTEGER NOT NULL,
                    user_key TEXT NOT NULL,
                    order_index INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, user_key),
                    FOREIGN KEY(task_id) REFERENCES douyin_tasks(task_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS douyin_task_high_intent_users (
                    task_id INTEGER NOT NULL,
                    user_key TEXT NOT NULL,
                    order_index INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, user_key),
                    FOREIGN KEY(task_id) REFERENCES douyin_tasks(task_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_douyin_task_comments_task_id ON douyin_task_comments(task_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_douyin_task_high_intent_task_id ON douyin_task_high_intent_users(task_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS douyin_customer_pool_all (
                    task_id INTEGER NOT NULL,
                    user_key TEXT NOT NULL,
                    order_index INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, user_key)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS douyin_customer_pool_precise (
                    task_id INTEGER NOT NULL,
                    user_key TEXT NOT NULL,
                    order_index INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, user_key)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_douyin_customer_pool_all_task_id ON douyin_customer_pool_all(task_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_douyin_customer_pool_precise_task_id ON douyin_customer_pool_precise(task_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS douyin_monitor_targets (
                    target_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_url TEXT NOT NULL DEFAULT '',
                    sec_user_id TEXT NOT NULL DEFAULT '',
                    username TEXT NOT NULL DEFAULT '',
                    douyin_id TEXT NOT NULL DEFAULT '',
                    avatar_url TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    auto_collect_new INTEGER NOT NULL DEFAULT 1,
                    last_video_sync_at TEXT NOT NULL DEFAULT '',
                    last_collect_at TEXT NOT NULL DEFAULT '',
                    next_run_at TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_douyin_monitor_targets_profile_url ON douyin_monitor_targets(profile_url)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_douyin_monitor_targets_sec_user_id ON douyin_monitor_targets(sec_user_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS douyin_monitor_videos (
                    target_id INTEGER NOT NULL,
                    aweme_id TEXT NOT NULL,
                    url TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    cover_image TEXT NOT NULL DEFAULT '',
                    publish_time TEXT NOT NULL DEFAULT '',
                    likes INTEGER NOT NULL DEFAULT 0,
                    comments INTEGER NOT NULL DEFAULT 0,
                    video_order INTEGER NOT NULL DEFAULT 0,
                    is_selected INTEGER NOT NULL DEFAULT 0,
                    is_new INTEGER NOT NULL DEFAULT 0,
                    first_seen_at TEXT NOT NULL DEFAULT '',
                    last_seen_at TEXT NOT NULL DEFAULT '',
                    last_collected_at TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (target_id, aweme_id),
                    FOREIGN KEY(target_id) REFERENCES douyin_monitor_targets(target_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_douyin_monitor_videos_target_id ON douyin_monitor_videos(target_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS douyin_monitor_video_snapshots (
                    target_id INTEGER NOT NULL,
                    aweme_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    likes INTEGER NOT NULL DEFAULT 0,
                    comments INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (target_id, aweme_id, captured_at),
                    FOREIGN KEY(target_id) REFERENCES douyin_monitor_targets(target_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_douyin_monitor_snapshots_target_id ON douyin_monitor_video_snapshots(target_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS douyin_monitor_comments (
                    target_id INTEGER NOT NULL,
                    aweme_id TEXT NOT NULL,
                    comment_key TEXT NOT NULL,
                    user_key TEXT NOT NULL DEFAULT '',
                    is_high_intent INTEGER NOT NULL DEFAULT 0,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    seen_count INTEGER NOT NULL DEFAULT 1,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (target_id, aweme_id, comment_key),
                    FOREIGN KEY(target_id) REFERENCES douyin_monitor_targets(target_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_douyin_monitor_comments_target_id ON douyin_monitor_comments(target_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_douyin_monitor_comments_user_key ON douyin_monitor_comments(user_key)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS douyin_monitor_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trigger_type TEXT NOT NULL DEFAULT 'manual',
                    slot_key TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    started_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_users (
                    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mobile TEXT NOT NULL UNIQUE,
                    nickname TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    last_login_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_sms_codes (
                    code_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mobile TEXT NOT NULL,
                    purpose TEXT NOT NULL DEFAULT 'auth',
                    code_hash TEXT NOT NULL,
                    expire_at TEXT NOT NULL,
                    is_used INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    used_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_sms_codes_mobile ON auth_sms_codes(mobile)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    session_token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES auth_users(user_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_id ON auth_sessions(user_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_user_platforms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    platform TEXT NOT NULL,
                    license_key TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'activated',
                    activated_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, platform),
                    FOREIGN KEY(user_id) REFERENCES auth_users(user_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_user_platforms_user_id ON auth_user_platforms(user_id)"
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(search_history_items)").fetchall()
            }
            if "last_cover_image" not in columns:
                conn.execute(
                    "ALTER TABLE search_history_items ADD COLUMN last_cover_image TEXT NOT NULL DEFAULT ''"
                )
            if "last_author_avatar" not in columns:
                conn.execute(
                    "ALTER TABLE search_history_items ADD COLUMN last_author_avatar TEXT NOT NULL DEFAULT ''"
                )
            conn.commit()
        finally:
            conn.close()

    def load_douyin_tasks(self) -> List[Dict]:
        with self._lock:
            conn = self._connect()
            try:
                task_rows = conn.execute(
                    """
                    SELECT task_id, url, title, author, cover_image, source_session_id, source_item_key,
                           likes, publish_time, comment_count, status, error,
                           video_comment_status, video_comment_error, video_comment_mode,
                           video_comment_prompt, video_comment_seed_text, video_comment_summary,
                           video_comment_text, video_comment_account_id,
                           video_comment_started_at, video_comment_finished_at,
                           payload_json
                    FROM douyin_tasks
                    ORDER BY task_id
                    """
                ).fetchall()
                comment_rows = conn.execute(
                    """
                    SELECT task_id, payload_json
                    FROM douyin_task_comments
                    ORDER BY task_id, order_index, user_key
                    """
                ).fetchall()
                intent_rows = conn.execute(
                    """
                    SELECT task_id, payload_json
                    FROM douyin_task_high_intent_users
                    ORDER BY task_id, order_index, user_key
                    """
                ).fetchall()
            finally:
                conn.close()

        comments_by_task: Dict[int, List[Dict]] = {}
        for row in comment_rows:
            payload = self._load_json_dict(row["payload_json"])
            comments_by_task.setdefault(int(row["task_id"] or 0), []).append(payload)

        intent_by_task: Dict[int, List[Dict]] = {}
        for row in intent_rows:
            payload = self._load_json_dict(row["payload_json"])
            intent_by_task.setdefault(int(row["task_id"] or 0), []).append(payload)

        tasks: List[Dict] = []
        for row in task_rows:
            payload = self._load_json_dict(row["payload_json"])
            task_id = int(row["task_id"] or 0)
            task = {
                **payload,
                "id": task_id,
                "platform": payload.get("platform", "douyin"),
                "url": row["url"] or "",
                "title": row["title"] or "",
                "author": row["author"] or "",
                "cover_image": row["cover_image"] or "",
                "source_session_id": row["source_session_id"] or "",
                "source_item_key": row["source_item_key"] or "",
                "likes": int(row["likes"] or 0),
                "publish_time": row["publish_time"] or "",
                "comment_count": int(row["comment_count"] or 0),
                "status": row["status"] or "pending",
                "error": row["error"] or "",
                "video_comment_status": row["video_comment_status"] or "pending",
                "video_comment_error": row["video_comment_error"] or "",
                "video_comment_mode": row["video_comment_mode"] or "fixed",
                "video_comment_prompt": row["video_comment_prompt"] or "",
                "video_comment_seed_text": row["video_comment_seed_text"] or "",
                "video_comment_summary": row["video_comment_summary"] or "",
                "video_comment_text": row["video_comment_text"] or "",
                "video_comment_account_id": row["video_comment_account_id"] or "",
                "video_comment_started_at": row["video_comment_started_at"] or "",
                "video_comment_finished_at": row["video_comment_finished_at"] or "",
                "all_comments": comments_by_task.get(task_id, []),
                "high_intent_users": intent_by_task.get(task_id, []),
            }
            tasks.append(task)
        return tasks

    def save_douyin_tasks(self, tasks: List[Dict]):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        task_rows = []
        comment_rows = []
        intent_rows = []

        for raw_task in tasks or []:
            if not isinstance(raw_task, dict):
                continue
            task = dict(raw_task)
            task_id = int(task.get("id", 0) or 0)
            if task_id <= 0:
                continue

            comments = task.pop("all_comments", []) or []
            high_intent_users = task.pop("high_intent_users", []) or []

            task_rows.append(
                (
                    task_id,
                    str(task.get("url", "") or ""),
                    str(task.get("title", "") or ""),
                    str(task.get("author", "") or ""),
                    str(task.get("cover_image", "") or ""),
                    str(task.get("source_session_id", "") or ""),
                    str(task.get("source_item_key", "") or ""),
                    int(task.get("likes", 0) or 0),
                    str(task.get("publish_time", "") or ""),
                    int(task.get("comment_count", 0) or 0),
                    str(task.get("status", "pending") or "pending"),
                    str(task.get("error", "") or ""),
                    str(task.get("video_comment_status", "pending") or "pending"),
                    str(task.get("video_comment_error", "") or ""),
                    str(task.get("video_comment_mode", "fixed") or "fixed"),
                    str(task.get("video_comment_prompt", "") or ""),
                    str(task.get("video_comment_seed_text", "") or ""),
                    str(task.get("video_comment_summary", "") or ""),
                    str(task.get("video_comment_text", "") or ""),
                    str(task.get("video_comment_account_id", "") or ""),
                    str(task.get("video_comment_started_at", "") or ""),
                    str(task.get("video_comment_finished_at", "") or ""),
                    json.dumps(task, ensure_ascii=False),
                    now,
                )
            )

            for index, row in enumerate(comments):
                if not isinstance(row, dict):
                    continue
                comment_rows.append(
                    (
                        task_id,
                        self._build_douyin_user_key(row),
                        index,
                        json.dumps(row, ensure_ascii=False),
                        now,
                    )
                )

            for index, row in enumerate(high_intent_users):
                if not isinstance(row, dict):
                    continue
                intent_rows.append(
                    (
                        task_id,
                        self._build_douyin_user_key(row),
                        index,
                        json.dumps(row, ensure_ascii=False),
                        now,
                    )
                )

        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM douyin_task_comments")
                conn.execute("DELETE FROM douyin_task_high_intent_users")
                conn.execute("DELETE FROM douyin_tasks")
                if task_rows:
                    conn.executemany(
                        """
                        INSERT INTO douyin_tasks (
                            task_id, url, title, author, cover_image, source_session_id, source_item_key,
                            likes, publish_time, comment_count, status, error,
                            video_comment_status, video_comment_error, video_comment_mode,
                            video_comment_prompt, video_comment_seed_text, video_comment_summary,
                            video_comment_text, video_comment_account_id,
                            video_comment_started_at, video_comment_finished_at,
                            payload_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        task_rows,
                    )
                if comment_rows:
                    conn.executemany(
                        """
                        INSERT INTO douyin_task_comments (
                            task_id, user_key, order_index, payload_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        comment_rows,
                    )
                if intent_rows:
                    conn.executemany(
                        """
                        INSERT INTO douyin_task_high_intent_users (
                            task_id, user_key, order_index, payload_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        intent_rows,
                    )
                conn.commit()
            finally:
                conn.close()

    def douyin_task_count(self) -> int:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT COUNT(*) AS count FROM douyin_tasks").fetchone()
                return int(row["count"] or 0) if row else 0
            finally:
                conn.close()

    def load_douyin_customer_pools(self) -> Tuple[List[Dict], List[Dict]]:
        with self._lock:
            conn = self._connect()
            try:
                all_rows = conn.execute(
                    """
                    SELECT payload_json
                    FROM douyin_customer_pool_all
                    ORDER BY order_index, task_id, user_key
                    """
                ).fetchall()
                precise_rows = conn.execute(
                    """
                    SELECT payload_json
                    FROM douyin_customer_pool_precise
                    ORDER BY order_index, task_id, user_key
                    """
                ).fetchall()
            finally:
                conn.close()

        all_customers = [self._load_json_dict(row["payload_json"]) for row in all_rows]
        precise_customers = [self._load_json_dict(row["payload_json"]) for row in precise_rows]
        return all_customers, precise_customers

    def save_douyin_customer_pools(self, all_customers: List[Dict], precise_customers: List[Dict]):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        all_rows = []
        for index, row in enumerate(all_customers or []):
            if not isinstance(row, dict):
                continue
            all_rows.append(
                (
                    int(row.get("task_id", 0) or 0),
                    self._build_douyin_user_key(row),
                    index,
                    json.dumps(row, ensure_ascii=False),
                    now,
                )
            )

        precise_rows = []
        for index, row in enumerate(precise_customers or []):
            if not isinstance(row, dict):
                continue
            precise_rows.append(
                (
                    int(row.get("task_id", 0) or 0),
                    self._build_douyin_user_key(row),
                    index,
                    json.dumps(row, ensure_ascii=False),
                    now,
                )
            )

        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM douyin_customer_pool_all")
                conn.execute("DELETE FROM douyin_customer_pool_precise")
                if all_rows:
                    conn.executemany(
                        """
                        INSERT INTO douyin_customer_pool_all (
                            task_id, user_key, order_index, payload_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        all_rows,
                    )
                if precise_rows:
                    conn.executemany(
                        """
                        INSERT INTO douyin_customer_pool_precise (
                            task_id, user_key, order_index, payload_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        precise_rows,
                    )
                conn.commit()
            finally:
                conn.close()

    def load_runtime_state(self) -> Tuple[List[Dict], Dict]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT key, value FROM state_blobs WHERE key IN ('tasks', 'task_source_meta')"
                ).fetchall()
            finally:
                conn.close()

        payload = {row["key"]: row["value"] for row in rows}
        tasks = self._load_json_list(payload.get("tasks"))
        source = self._load_json_dict(payload.get("task_source_meta"))

        for task in tasks:
            if task.get("status") == "processing":
                task["status"] = "pending"
                task["error"] = task.get("error") or "程序重启后恢复为待处理"

        if not source:
            source = {"type": "empty", "label": "尚未准备评论采集任务", "count": 0}

        return tasks, source

    def save_runtime_state(self, tasks: List[Dict], task_source_meta: Dict):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        task_payload = json.dumps(tasks or [], ensure_ascii=False)
        source_payload = json.dumps(task_source_meta or {}, ensure_ascii=False)

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "REPLACE INTO state_blobs (key, value, updated_at) VALUES (?, ?, ?)",
                    ("tasks", task_payload, now),
                )
                conn.execute(
                    "REPLACE INTO state_blobs (key, value, updated_at) VALUES (?, ?, ?)",
                    ("task_source_meta", source_payload, now),
                )
                conn.commit()
            finally:
                conn.close()

    def load_blob_json(self, key: str, default=None):
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT value FROM state_blobs WHERE key = ?",
                    (str(key),),
                ).fetchone()
            finally:
                conn.close()

        if not row:
            return default

        try:
            return json.loads(row["value"])
        except Exception:
            return default

    def save_blob_json(self, key: str, value):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = json.dumps(value, ensure_ascii=False)

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "REPLACE INTO state_blobs (key, value, updated_at) VALUES (?, ?, ?)",
                    (str(key), payload, now),
                )
                conn.commit()
            finally:
                conn.close()

    def load_search_history(self) -> Dict:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT key, first_seen_at, last_seen_at, seen_count, keywords_json,
                           last_title, last_author, last_url, last_cover_image, last_author_avatar
                    FROM search_history_items
                    ORDER BY key
                    """
                ).fetchall()
            finally:
                conn.close()

        items = {}
        for row in rows:
            try:
                keywords = json.loads(row["keywords_json"] or "[]")
                if not isinstance(keywords, list):
                    keywords = []
            except Exception:
                keywords = []
            items[row["key"]] = {
                "first_seen_at": row["first_seen_at"] or "",
                "last_seen_at": row["last_seen_at"] or "",
                "seen_count": int(row["seen_count"] or 0),
                "keywords": [str(keyword) for keyword in keywords if keyword],
                "last_title": row["last_title"] or "",
                "last_author": row["last_author"] or "",
                "last_url": row["last_url"] or "",
                "last_cover_image": row["last_cover_image"] or "",
                "last_author_avatar": row["last_author_avatar"] or "",
            }
        return {"version": 1, "items": items}

    def save_search_history(self, history: Dict):
        payload = history if isinstance(history, dict) else {"version": 1, "items": {}}
        items = payload.get("items", {})
        if not isinstance(items, dict):
            items = {}

        normalized_rows = []
        for key, item in items.items():
            if not key or not isinstance(item, dict):
                continue
            keywords = item.get("keywords", [])
            if not isinstance(keywords, list):
                keywords = []
            normalized_rows.append(
                (
                    str(key),
                    str(item.get("first_seen_at", "") or ""),
                    str(item.get("last_seen_at", "") or ""),
                    int(item.get("seen_count", 0) or 0),
                    json.dumps([str(keyword) for keyword in keywords if keyword], ensure_ascii=False),
                    str(item.get("last_title", "") or ""),
                    str(item.get("last_author", "") or ""),
                    str(item.get("last_url", "") or ""),
                    str(item.get("last_cover_image", "") or ""),
                    str(item.get("last_author_avatar", "") or ""),
                )
            )

        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM search_history_items")
                if normalized_rows:
                    conn.executemany(
                        """
                        INSERT INTO search_history_items (
                            key, first_seen_at, last_seen_at, seen_count,
                            keywords_json, last_title, last_author, last_url,
                            last_cover_image, last_author_avatar
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        normalized_rows,
                    )
                conn.commit()
            finally:
                conn.close()

    def search_history_count(self) -> int:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT COUNT(*) AS count FROM search_history_items").fetchone()
                return int(row["count"] or 0) if row else 0
            finally:
                conn.close()

    def find_douyin_monitor_target(self, profile_url: str = "", sec_user_id: str = "") -> Dict:
        normalized_profile_url = str(profile_url or "").strip()
        normalized_sec_user_id = str(sec_user_id or "").strip()
        with self._lock:
            conn = self._connect()
            try:
                row = None
                if normalized_sec_user_id:
                    row = conn.execute(
                        """
                        SELECT *
                        FROM douyin_monitor_targets
                        WHERE sec_user_id = ?
                        ORDER BY target_id DESC
                        LIMIT 1
                        """,
                        (normalized_sec_user_id,),
                    ).fetchone()
                if row is None and normalized_profile_url:
                    row = conn.execute(
                        """
                        SELECT *
                        FROM douyin_monitor_targets
                        WHERE profile_url = ?
                        ORDER BY target_id DESC
                        LIMIT 1
                        """,
                        (normalized_profile_url,),
                    ).fetchone()
            finally:
                conn.close()
        return self._hydrate_douyin_monitor_target(row)

    def load_douyin_monitor_targets(self) -> List[Dict]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM douyin_monitor_targets
                    ORDER BY target_id DESC
                    """
                ).fetchall()
            finally:
                conn.close()
        return [self._hydrate_douyin_monitor_target(row) for row in rows]

    def save_douyin_monitor_target(self, target: Dict) -> Dict:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = dict(target or {})
        target_id = int(payload.get("target_id", 0) or 0)
        profile_url = str(payload.get("profile_url", "") or "")
        sec_user_id = str(payload.get("sec_user_id", "") or "")
        row = (
            profile_url,
            sec_user_id,
            str(payload.get("username", "") or ""),
            str(payload.get("douyin_id", "") or ""),
            str(payload.get("avatar_url", "") or ""),
            str(payload.get("status", "active") or "active"),
            1 if bool(payload.get("auto_collect_new", True)) else 0,
            str(payload.get("last_video_sync_at", "") or ""),
            str(payload.get("last_collect_at", "") or ""),
            str(payload.get("next_run_at", "") or ""),
            str(payload.get("notes", "") or ""),
            json.dumps(payload, ensure_ascii=False),
            now,
        )

        with self._lock:
            conn = self._connect()
            try:
                if target_id <= 0:
                    existing = None
                    if sec_user_id:
                        existing = conn.execute(
                            """
                            SELECT target_id
                            FROM douyin_monitor_targets
                            WHERE sec_user_id = ?
                            ORDER BY target_id DESC
                            LIMIT 1
                            """,
                            (sec_user_id,),
                        ).fetchone()
                    if existing is None and profile_url:
                        existing = conn.execute(
                            """
                            SELECT target_id
                            FROM douyin_monitor_targets
                            WHERE profile_url = ?
                            ORDER BY target_id DESC
                            LIMIT 1
                            """,
                            (profile_url,),
                        ).fetchone()
                    if existing:
                        target_id = int(existing["target_id"] or 0)
                if target_id > 0:
                    conn.execute(
                        """
                        UPDATE douyin_monitor_targets
                        SET profile_url = ?, sec_user_id = ?, username = ?, douyin_id = ?, avatar_url = ?,
                            status = ?, auto_collect_new = ?, last_video_sync_at = ?, last_collect_at = ?,
                            next_run_at = ?, notes = ?, payload_json = ?, updated_at = ?
                        WHERE target_id = ?
                        """,
                        (*row, target_id),
                    )
                else:
                    cursor = conn.execute(
                        """
                        INSERT INTO douyin_monitor_targets (
                            profile_url, sec_user_id, username, douyin_id, avatar_url,
                            status, auto_collect_new, last_video_sync_at, last_collect_at,
                            next_run_at, notes, payload_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        row,
                    )
                    target_id = int(cursor.lastrowid or 0)
                conn.commit()
            finally:
                conn.close()

        saved = dict(payload)
        saved["target_id"] = target_id
        saved["updated_at"] = now
        return saved

    def stop_douyin_monitor_target(self, target_id: int) -> bool:
        target_id = int(target_id or 0)
        if target_id <= 0:
            return False
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    UPDATE douyin_monitor_targets
                    SET status = 'inactive', updated_at = ?
                    WHERE target_id = ?
                    """,
                    (now, target_id),
                )
                conn.commit()
                return bool(cursor.rowcount)
            finally:
                conn.close()

    def load_douyin_monitor_videos(self, target_id: int | None = None) -> List[Dict]:
        params: tuple = ()
        sql = """
            SELECT *
            FROM douyin_monitor_videos
        """
        if target_id:
            sql += " WHERE target_id = ?"
            params = (int(target_id),)
        sql += " ORDER BY target_id DESC, video_order ASC, last_seen_at DESC, aweme_id DESC"

        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        return [self._hydrate_douyin_monitor_video(row) for row in rows]

    def save_douyin_monitor_videos(self, target_id: int, videos: List[Dict]):
        if int(target_id or 0) <= 0:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        normalized_rows = []
        for index, raw_video in enumerate(videos or []):
            if not isinstance(raw_video, dict):
                continue
            video = dict(raw_video)
            aweme_id = str(video.get("aweme_id", "") or "").strip()
            if not aweme_id:
                continue
            normalized_rows.append(
                (
                    int(target_id),
                    aweme_id,
                    str(video.get("url", "") or ""),
                    str(video.get("title", "") or ""),
                    str(video.get("cover_image", "") or ""),
                    str(video.get("publish_time", "") or ""),
                    int(video.get("likes", 0) or 0),
                    int(video.get("comments", 0) or 0),
                    int(video.get("video_order", index + 1) or (index + 1)),
                    1 if bool(video.get("is_selected", False)) else 0,
                    1 if bool(video.get("is_new", False)) else 0,
                    str(video.get("first_seen_at", "") or ""),
                    str(video.get("last_seen_at", "") or ""),
                    str(video.get("last_collected_at", "") or ""),
                    json.dumps(video, ensure_ascii=False),
                    now,
                )
            )

        with self._lock:
            conn = self._connect()
            try:
                if normalized_rows:
                    conn.executemany(
                        """
                        INSERT INTO douyin_monitor_videos (
                            target_id, aweme_id, url, title, cover_image, publish_time,
                            likes, comments, video_order, is_selected, is_new,
                            first_seen_at, last_seen_at, last_collected_at, payload_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(target_id, aweme_id) DO UPDATE SET
                            url = excluded.url,
                            title = excluded.title,
                            cover_image = excluded.cover_image,
                            publish_time = excluded.publish_time,
                            likes = excluded.likes,
                            comments = excluded.comments,
                            video_order = excluded.video_order,
                            is_selected = excluded.is_selected,
                            is_new = excluded.is_new,
                            first_seen_at = excluded.first_seen_at,
                            last_seen_at = excluded.last_seen_at,
                            last_collected_at = excluded.last_collected_at,
                            payload_json = excluded.payload_json,
                            updated_at = excluded.updated_at
                        """,
                        normalized_rows,
                    )
                conn.commit()
            finally:
                conn.close()

    def set_douyin_monitor_video_selection(self, target_id: int, selected_aweme_ids: List[str]):
        normalized_selected = {str(item or "").strip() for item in (selected_aweme_ids or []) if str(item or "").strip()}
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE douyin_monitor_videos
                    SET is_selected = 0
                    WHERE target_id = ?
                    """,
                    (int(target_id),),
                )
                if normalized_selected:
                    conn.executemany(
                        """
                        UPDATE douyin_monitor_videos
                        SET is_selected = 1
                        WHERE target_id = ? AND aweme_id = ?
                        """,
                        [(int(target_id), aweme_id) for aweme_id in normalized_selected],
                    )
                conn.commit()
            finally:
                conn.close()

    def append_douyin_monitor_video_snapshots(self, rows: List[Dict]):
        normalized_rows = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            target_id = int(row.get("target_id", 0) or 0)
            aweme_id = str(row.get("aweme_id", "") or "").strip()
            captured_at = str(row.get("captured_at", "") or "").strip()
            if target_id <= 0 or not aweme_id or not captured_at:
                continue
            normalized_rows.append(
                (
                    target_id,
                    aweme_id,
                    captured_at,
                    int(row.get("likes", 0) or 0),
                    int(row.get("comments", 0) or 0),
                    json.dumps(row, ensure_ascii=False),
                )
            )
        if not normalized_rows:
            return

        with self._lock:
            conn = self._connect()
            try:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO douyin_monitor_video_snapshots (
                        target_id, aweme_id, captured_at, likes, comments, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    normalized_rows,
                )
                conn.commit()
            finally:
                conn.close()

    def load_douyin_monitor_comments(self, target_id: int | None = None) -> List[Dict]:
        params: tuple = ()
        sql = """
            SELECT target_id, aweme_id, comment_key, user_key, is_high_intent,
                   first_seen_at, last_seen_at, seen_count, payload_json
            FROM douyin_monitor_comments
        """
        if target_id:
            sql += " WHERE target_id = ?"
            params = (int(target_id),)
        sql += " ORDER BY last_seen_at DESC, first_seen_at DESC"

        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()

        hydrated = []
        for row in rows:
            payload = self._load_json_dict(row["payload_json"])
            payload.setdefault("target_id", int(row["target_id"] or 0))
            payload.setdefault("aweme_id", row["aweme_id"] or "")
            payload.setdefault("comment_key", row["comment_key"] or "")
            payload.setdefault("user_key", row["user_key"] or "")
            payload["is_high_intent"] = bool(row["is_high_intent"])
            payload["first_seen_at"] = row["first_seen_at"] or ""
            payload["last_seen_at"] = row["last_seen_at"] or ""
            payload["seen_count"] = int(row["seen_count"] or 0)
            hydrated.append(payload)
        return hydrated

    def save_douyin_monitor_comments(self, rows: List[Dict]):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._connect()
            try:
                for raw_row in rows or []:
                    if not isinstance(raw_row, dict):
                        continue
                    row = dict(raw_row)
                    target_id = int(row.get("target_id", 0) or 0)
                    aweme_id = str(row.get("aweme_id", "") or "").strip()
                    comment_key = str(row.get("comment_key", "") or "").strip()
                    user_key = str(row.get("user_key", "") or "").strip()
                    if target_id <= 0 or not aweme_id or not comment_key:
                        continue
                    preserve_seen_state = bool(row.pop("_preserve_monitor_seen_state", False))
                    existing = conn.execute(
                        """
                        SELECT first_seen_at, last_seen_at, seen_count, is_high_intent
                        FROM douyin_monitor_comments
                        WHERE target_id = ? AND aweme_id = ? AND comment_key = ?
                        """,
                        (target_id, aweme_id, comment_key),
                    ).fetchone()
                    first_seen_at = (
                        str(existing["first_seen_at"] or "").strip()
                        if existing and str(existing["first_seen_at"] or "").strip()
                        else str(row.get("first_seen_at", "") or now)
                    )
                    if preserve_seen_state and existing:
                        last_seen_at = str(existing["last_seen_at"] or "").strip() or str(row.get("last_seen_at", "") or now)
                        seen_count = int(existing["seen_count"] or 0)
                    else:
                        last_seen_at = now
                        seen_count = int(existing["seen_count"] or 0) + 1 if existing else int(row.get("seen_count", 1) or 1)
                    is_high_intent = 1 if bool(row.get("is_high_intent", False) or (existing and existing["is_high_intent"])) else 0
                    row["first_seen_at"] = first_seen_at
                    row["last_seen_at"] = last_seen_at
                    row["seen_count"] = seen_count
                    row["is_high_intent"] = bool(is_high_intent)
                    conn.execute(
                        """
                        INSERT INTO douyin_monitor_comments (
                            target_id, aweme_id, comment_key, user_key, is_high_intent,
                            first_seen_at, last_seen_at, seen_count, payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(target_id, aweme_id, comment_key) DO UPDATE SET
                            user_key = excluded.user_key,
                            is_high_intent = excluded.is_high_intent,
                            first_seen_at = excluded.first_seen_at,
                            last_seen_at = excluded.last_seen_at,
                            seen_count = excluded.seen_count,
                            payload_json = excluded.payload_json
                        """,
                        (
                            target_id,
                            aweme_id,
                            comment_key,
                            user_key,
                            is_high_intent,
                            first_seen_at,
                            last_seen_at,
                            seen_count,
                            json.dumps(row, ensure_ascii=False),
                        ),
                    )
                conn.commit()
            finally:
                conn.close()

    def save_douyin_monitor_run(self, run: Dict) -> Dict:
        payload = dict(run or {})
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO douyin_monitor_runs (
                        trigger_type, slot_key, status, started_at, finished_at, payload_json, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(payload.get("trigger_type", "manual") or "manual"),
                        str(payload.get("slot_key", "") or ""),
                        str(payload.get("status", "pending") or "pending"),
                        str(payload.get("started_at", "") or ""),
                        str(payload.get("finished_at", "") or ""),
                        json.dumps(payload, ensure_ascii=False),
                        str(payload.get("error", "") or ""),
                    ),
                )
                payload["run_id"] = int(cursor.lastrowid or 0)
                conn.commit()
            finally:
                conn.close()
        return payload

    def load_latest_douyin_monitor_run(self) -> Dict:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT *
                    FROM douyin_monitor_runs
                    ORDER BY run_id DESC
                    LIMIT 1
                    """
                ).fetchone()
            finally:
                conn.close()
        if not row:
            return {}
        payload = self._load_json_dict(row["payload_json"])
        payload["run_id"] = int(row["run_id"] or 0)
        payload["trigger_type"] = row["trigger_type"] or ""
        payload["slot_key"] = row["slot_key"] or ""
        payload["status"] = row["status"] or ""
        payload["started_at"] = row["started_at"] or ""
        payload["finished_at"] = row["finished_at"] or ""
        payload["error"] = row["error"] or ""
        return payload

    def create_auth_user(self, mobile: str, nickname: str = "") -> Dict:
        mobile = str(mobile or "").strip()
        nickname = str(nickname or "").strip()
        if not mobile:
            return {}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO auth_users (mobile, nickname, status, created_at, last_login_at)
                    VALUES (?, ?, 'active', ?, ?)
                    """,
                    (mobile, nickname, now, now),
                )
                user_id = int(cursor.lastrowid or 0)
                conn.commit()
            finally:
                conn.close()
        return self.get_auth_user_by_id(user_id)

    def get_auth_user_by_mobile(self, mobile: str) -> Dict:
        mobile = str(mobile or "").strip()
        if not mobile:
            return {}
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT user_id, mobile, nickname, status, created_at, last_login_at
                    FROM auth_users
                    WHERE mobile = ?
                    LIMIT 1
                    """,
                    (mobile,),
                ).fetchone()
            finally:
                conn.close()
        return self._hydrate_auth_user(row)

    def get_auth_user_by_id(self, user_id: int) -> Dict:
        if int(user_id or 0) <= 0:
            return {}
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT user_id, mobile, nickname, status, created_at, last_login_at
                    FROM auth_users
                    WHERE user_id = ?
                    LIMIT 1
                    """,
                    (int(user_id),),
                ).fetchone()
            finally:
                conn.close()
        return self._hydrate_auth_user(row)

    def touch_auth_user_login(self, user_id: int):
        if int(user_id or 0) <= 0:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE auth_users
                    SET last_login_at = ?
                    WHERE user_id = ?
                    """,
                    (now, int(user_id)),
                )
                conn.commit()
            finally:
                conn.close()

    def save_auth_sms_code(self, mobile: str, purpose: str, code_hash: str, expire_at: str):
        mobile = str(mobile or "").strip()
        if not mobile:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO auth_sms_codes (mobile, purpose, code_hash, expire_at, is_used, created_at, used_at)
                    VALUES (?, ?, ?, ?, 0, ?, '')
                    """,
                    (
                        mobile,
                        str(purpose or "auth").strip() or "auth",
                        str(code_hash or "").strip(),
                        str(expire_at or "").strip(),
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_latest_auth_sms_code(self, mobile: str) -> Dict:
        mobile = str(mobile or "").strip()
        if not mobile:
            return {}
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT code_id, mobile, purpose, code_hash, expire_at, is_used, created_at, used_at
                    FROM auth_sms_codes
                    WHERE mobile = ?
                    ORDER BY code_id DESC
                    LIMIT 1
                    """,
                    (mobile,),
                ).fetchone()
            finally:
                conn.close()
        return self._hydrate_auth_sms_code(row)

    def consume_auth_sms_code(self, mobile: str, code_hash: str) -> Dict:
        mobile = str(mobile or "").strip()
        code_hash = str(code_hash or "").strip()
        if not mobile or not code_hash:
            return {}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT code_id, mobile, purpose, code_hash, expire_at, is_used, created_at, used_at
                    FROM auth_sms_codes
                    WHERE mobile = ? AND code_hash = ? AND is_used = 0
                    ORDER BY code_id DESC
                    LIMIT 1
                    """,
                    (mobile, code_hash),
                ).fetchone()
                if not row:
                    return {}
                conn.execute(
                    """
                    UPDATE auth_sms_codes
                    SET is_used = 1, used_at = ?
                    WHERE code_id = ?
                    """,
                    (now, int(row["code_id"] or 0)),
                )
                conn.commit()
            finally:
                conn.close()
        hydrated = self._hydrate_auth_sms_code(row)
        hydrated["is_used"] = True
        hydrated["used_at"] = now
        return hydrated

    def create_auth_session(self, user_id: int, session_token: str, expires_at: str) -> Dict:
        if int(user_id or 0) <= 0 or not str(session_token or "").strip():
            return {}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO auth_sessions (
                        session_token, user_id, status, created_at, expires_at, last_seen_at
                    ) VALUES (?, ?, 'active', ?, ?, ?)
                    """,
                    (
                        str(session_token or "").strip(),
                        int(user_id),
                        now,
                        str(expires_at or "").strip(),
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return self.get_auth_session(str(session_token or "").strip())

    def get_auth_session(self, session_token: str) -> Dict:
        session_token = str(session_token or "").strip()
        if not session_token:
            return {}
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT s.session_token, s.user_id, s.status, s.created_at, s.expires_at, s.last_seen_at,
                           u.mobile, u.nickname, u.status AS user_status, u.created_at AS user_created_at,
                           u.last_login_at
                    FROM auth_sessions s
                    JOIN auth_users u ON u.user_id = s.user_id
                    WHERE s.session_token = ?
                    LIMIT 1
                    """,
                    (session_token,),
                ).fetchone()
            finally:
                conn.close()
        return self._hydrate_auth_session(row)

    def touch_auth_session(self, session_token: str):
        session_token = str(session_token or "").strip()
        if not session_token:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE auth_sessions
                    SET last_seen_at = ?
                    WHERE session_token = ?
                    """,
                    (now, session_token),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_auth_session(self, session_token: str):
        session_token = str(session_token or "").strip()
        if not session_token:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM auth_sessions WHERE session_token = ?",
                    (session_token,),
                )
                conn.commit()
            finally:
                conn.close()

    def save_user_platform_license(
        self,
        user_id: int,
        platform: str,
        license_key: str,
        status: str = "activated",
        activated_at: str = "",
    ) -> Dict:
        user_id = int(user_id or 0)
        platform = str(platform or "").strip().lower()
        license_key = str(license_key or "").strip()
        if user_id <= 0 or not platform or not license_key:
            return {}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        activated_text = str(activated_at or "").strip() or now
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO auth_user_platforms (
                        user_id, platform, license_key, status, activated_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, platform) DO UPDATE SET
                        license_key = excluded.license_key,
                        status = excluded.status,
                        activated_at = excluded.activated_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        user_id,
                        platform,
                        license_key,
                        str(status or "activated").strip() or "activated",
                        activated_text,
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        platform_map = self.load_user_platform_licenses(user_id)
        return dict(platform_map.get(platform, {}))

    def load_user_platform_licenses(self, user_id: int) -> Dict[str, Dict]:
        user_id = int(user_id or 0)
        if user_id <= 0:
            return {}
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT id, user_id, platform, license_key, status, activated_at, updated_at
                    FROM auth_user_platforms
                    WHERE user_id = ?
                    ORDER BY id
                    """,
                    (user_id,),
                ).fetchall()
            finally:
                conn.close()
        result: Dict[str, Dict] = {}
        for row in rows:
            platform = str(row["platform"] or "").strip().lower()
            if not platform:
                continue
            result[platform] = self._hydrate_auth_platform(row)
        return result

    @staticmethod
    def _hydrate_douyin_monitor_target(row) -> Dict:
        if not row:
            return {}
        payload = RuntimeStateStore._load_json_dict(row["payload_json"])
        payload["target_id"] = int(row["target_id"] or 0)
        payload["profile_url"] = row["profile_url"] or ""
        payload["sec_user_id"] = row["sec_user_id"] or ""
        payload["username"] = row["username"] or ""
        payload["douyin_id"] = row["douyin_id"] or ""
        payload["avatar_url"] = row["avatar_url"] or ""
        payload["status"] = row["status"] or "active"
        payload["auto_collect_new"] = bool(row["auto_collect_new"])
        payload["last_video_sync_at"] = row["last_video_sync_at"] or ""
        payload["last_collect_at"] = row["last_collect_at"] or ""
        payload["next_run_at"] = row["next_run_at"] or ""
        payload["notes"] = row["notes"] or ""
        payload["updated_at"] = row["updated_at"] or ""
        return payload

    @staticmethod
    def _hydrate_douyin_monitor_video(row) -> Dict:
        if not row:
            return {}
        payload = RuntimeStateStore._load_json_dict(row["payload_json"])
        payload["target_id"] = int(row["target_id"] or 0)
        payload["aweme_id"] = row["aweme_id"] or ""
        payload["url"] = row["url"] or ""
        payload["title"] = row["title"] or ""
        payload["cover_image"] = row["cover_image"] or ""
        payload["publish_time"] = row["publish_time"] or ""
        payload["likes"] = int(row["likes"] or 0)
        payload["comments"] = int(row["comments"] or 0)
        payload["video_order"] = int(row["video_order"] or 0)
        payload["is_selected"] = bool(row["is_selected"])
        payload["is_new"] = bool(row["is_new"])
        payload["first_seen_at"] = row["first_seen_at"] or ""
        payload["last_seen_at"] = row["last_seen_at"] or ""
        payload["last_collected_at"] = row["last_collected_at"] or ""
        payload["updated_at"] = row["updated_at"] or ""
        return payload

    @staticmethod
    def _load_json_list(value: str | None) -> List[Dict]:
        if not value:
            return []
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []

    @staticmethod
    def _load_json_dict(value: str | None) -> Dict:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _build_douyin_user_key(row: Dict) -> str:
        comment_id = " ".join(str(row.get("comment_id", "") or "").split())
        if comment_id:
            return f"id:{comment_id}"
        return "|".join(
            [
                " ".join(str(row.get("user_id", "") or "").split()),
                " ".join(str(row.get("username", "") or "").split()),
                " ".join(str(row.get("comment", row.get("content", "")) or "").split()),
                " ".join(str(row.get("comment_time", "") or "").split()),
            ]
        )

    @staticmethod
    def _hydrate_auth_user(row) -> Dict:
        if not row:
            return {}
        return {
            "user_id": int(row["user_id"] or 0),
            "mobile": RuntimeStateStore._text_value(row["mobile"]),
            "nickname": RuntimeStateStore._text_value(row["nickname"]),
            "status": RuntimeStateStore._text_value(row["status"]) or "active",
            "created_at": RuntimeStateStore._datetime_text(row["created_at"]),
            "last_login_at": RuntimeStateStore._datetime_text(row["last_login_at"]),
        }

    @staticmethod
    def _hydrate_auth_sms_code(row) -> Dict:
        if not row:
            return {}
        return {
            "code_id": int(row["code_id"] or 0),
            "mobile": RuntimeStateStore._text_value(row["mobile"]),
            "purpose": RuntimeStateStore._text_value(row["purpose"]) or "auth",
            "code_hash": RuntimeStateStore._text_value(row["code_hash"]),
            "expire_at": RuntimeStateStore._datetime_text(row["expire_at"]),
            "is_used": bool(row["is_used"]),
            "created_at": RuntimeStateStore._datetime_text(row["created_at"]),
            "used_at": RuntimeStateStore._datetime_text(row["used_at"]),
        }

    @staticmethod
    def _hydrate_auth_session(row) -> Dict:
        if not row:
            return {}
        return {
            "session_token": RuntimeStateStore._text_value(row["session_token"]),
            "user_id": int(row["user_id"] or 0),
            "status": RuntimeStateStore._text_value(row["status"]) or "active",
            "created_at": RuntimeStateStore._datetime_text(row["created_at"]),
            "expires_at": RuntimeStateStore._datetime_text(row["expires_at"]),
            "last_seen_at": RuntimeStateStore._datetime_text(row["last_seen_at"]),
            "user": {
                "user_id": int(row["user_id"] or 0),
                "mobile": RuntimeStateStore._text_value(row["mobile"]),
                "nickname": RuntimeStateStore._text_value(row["nickname"]),
                "status": RuntimeStateStore._text_value(row["user_status"]) or "active",
                "created_at": RuntimeStateStore._datetime_text(row["user_created_at"]),
                "last_login_at": RuntimeStateStore._datetime_text(row["last_login_at"]),
            },
        }

    @staticmethod
    def _hydrate_auth_platform(row) -> Dict:
        if not row:
            return {}
        return {
            "id": int(row["id"] or 0),
            "user_id": int(row["user_id"] or 0),
            "platform": RuntimeStateStore._text_value(row["platform"]),
            "license_key": RuntimeStateStore._text_value(row["license_key"]),
            "status": RuntimeStateStore._text_value(row["status"]) or "activated",
            "activated_at": RuntimeStateStore._datetime_text(row["activated_at"]),
            "updated_at": RuntimeStateStore._datetime_text(row["updated_at"]),
        }

    @staticmethod
    def _text_value(value: object) -> str:
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _datetime_text(value: object) -> str:
        if not value:
            return ""
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        return str(value)


def _read_simple_env_file(env_path: Path) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not env_path.exists():
        return result
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
    except Exception:
        return {}
    return result


class RemoteAuthStore:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ):
        self.host = str(host or "").strip()
        self.port = int(port or 3306)
        self.user = str(user or "").strip()
        self.password = str(password or "")
        self.database = str(database or "").strip()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.user and self.database)

    def _connect(self):
        conn = pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )
        return conn

    def create_auth_user(self, mobile: str, nickname: str = "") -> Dict:
        mobile = str(mobile or "").strip()
        nickname = str(nickname or "").strip()
        if not mobile:
            return {}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._connect()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO auth_users (mobile, nickname, status, created_at, last_login_at)
                        VALUES (%s, %s, 'active', %s, %s)
                        """,
                        (mobile, nickname, now, now),
                    )
                    user_id = int(cursor.lastrowid or 0)
                conn.commit()
            finally:
                conn.close()
        return self.get_auth_user_by_id(user_id)

    def get_auth_user_by_mobile(self, mobile: str) -> Dict:
        mobile = str(mobile or "").strip()
        if not mobile:
            return {}
        with self._lock:
            conn = self._connect()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT user_id, mobile, nickname, status, created_at, last_login_at
                        FROM auth_users
                        WHERE mobile = %s
                        LIMIT 1
                        """,
                        (mobile,),
                    )
                    row = cursor.fetchone()
            finally:
                conn.close()
        return RuntimeStateStore._hydrate_auth_user(row)

    def get_auth_user_by_id(self, user_id: int) -> Dict:
        user_id = int(user_id or 0)
        if user_id <= 0:
            return {}
        with self._lock:
            conn = self._connect()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT user_id, mobile, nickname, status, created_at, last_login_at
                        FROM auth_users
                        WHERE user_id = %s
                        LIMIT 1
                        """,
                        (user_id,),
                    )
                    row = cursor.fetchone()
            finally:
                conn.close()
        return RuntimeStateStore._hydrate_auth_user(row)

    def touch_auth_user_login(self, user_id: int):
        user_id = int(user_id or 0)
        if user_id <= 0:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._connect()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE auth_users
                        SET last_login_at = %s
                        WHERE user_id = %s
                        """,
                        (now, user_id),
                    )
                conn.commit()
            finally:
                conn.close()

    def save_auth_sms_code(self, mobile: str, purpose: str, code_hash: str, expire_at: str):
        mobile = str(mobile or "").strip()
        if not mobile:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._connect()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO auth_sms_codes (mobile, purpose, code_hash, expire_at, is_used, created_at, used_at)
                        VALUES (%s, %s, %s, %s, 0, %s, NULL)
                        """,
                        (
                            mobile,
                            str(purpose or "auth").strip() or "auth",
                            str(code_hash or "").strip(),
                            str(expire_at or "").strip(),
                            now,
                        ),
                    )
                conn.commit()
            finally:
                conn.close()

    def get_latest_auth_sms_code(self, mobile: str) -> Dict:
        mobile = str(mobile or "").strip()
        if not mobile:
            return {}
        with self._lock:
            conn = self._connect()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT code_id, mobile, purpose, code_hash, expire_at, is_used, created_at, used_at
                        FROM auth_sms_codes
                        WHERE mobile = %s
                        ORDER BY code_id DESC
                        LIMIT 1
                        """,
                        (mobile,),
                    )
                    row = cursor.fetchone()
            finally:
                conn.close()
        return RuntimeStateStore._hydrate_auth_sms_code(row)

    def consume_auth_sms_code(self, mobile: str, code_hash: str) -> Dict:
        mobile = str(mobile or "").strip()
        code_hash = str(code_hash or "").strip()
        if not mobile or not code_hash:
            return {}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._connect()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT code_id, mobile, purpose, code_hash, expire_at, is_used, created_at, used_at
                        FROM auth_sms_codes
                        WHERE mobile = %s AND code_hash = %s AND is_used = 0
                        ORDER BY code_id DESC
                        LIMIT 1
                        FOR UPDATE
                        """,
                        (mobile, code_hash),
                    )
                    row = cursor.fetchone()
                    if not row:
                        conn.rollback()
                        return {}
                    cursor.execute(
                        """
                        UPDATE auth_sms_codes
                        SET is_used = 1, used_at = %s
                        WHERE code_id = %s
                        """,
                        (now, int(row["code_id"] or 0)),
                    )
                conn.commit()
            finally:
                conn.close()
        hydrated = RuntimeStateStore._hydrate_auth_sms_code(row)
        hydrated["is_used"] = True
        hydrated["used_at"] = now
        return hydrated

    def create_auth_session(self, user_id: int, session_token: str, expires_at: str) -> Dict:
        user_id = int(user_id or 0)
        session_token = str(session_token or "").strip()
        if user_id <= 0 or not session_token:
            return {}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._connect()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO auth_sessions (
                            session_token, user_id, status, created_at, expires_at, last_seen_at
                        ) VALUES (%s, %s, 'active', %s, %s, %s)
                        """,
                        (session_token, user_id, now, str(expires_at or "").strip(), now),
                    )
                conn.commit()
            finally:
                conn.close()
        return self.get_auth_session(session_token)

    def get_auth_session(self, session_token: str) -> Dict:
        session_token = str(session_token or "").strip()
        if not session_token:
            return {}
        with self._lock:
            conn = self._connect()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT s.session_token, s.user_id, s.status, s.created_at, s.expires_at, s.last_seen_at,
                               u.mobile, u.nickname, u.status AS user_status, u.created_at AS user_created_at,
                               u.last_login_at
                        FROM auth_sessions s
                        JOIN auth_users u ON u.user_id = s.user_id
                        WHERE s.session_token = %s
                        LIMIT 1
                        """,
                        (session_token,),
                    )
                    row = cursor.fetchone()
            finally:
                conn.close()
        return RuntimeStateStore._hydrate_auth_session(row)

    def touch_auth_session(self, session_token: str):
        session_token = str(session_token or "").strip()
        if not session_token:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._connect()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE auth_sessions
                        SET last_seen_at = %s
                        WHERE session_token = %s
                        """,
                        (now, session_token),
                    )
                conn.commit()
            finally:
                conn.close()

    def delete_auth_session(self, session_token: str):
        session_token = str(session_token or "").strip()
        if not session_token:
            return
        with self._lock:
            conn = self._connect()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM auth_sessions WHERE session_token = %s",
                        (session_token,),
                    )
                conn.commit()
            finally:
                conn.close()

    def save_user_platform_license(
        self,
        user_id: int,
        platform: str,
        license_key: str,
        status: str = "activated",
        activated_at: str = "",
    ) -> Dict:
        user_id = int(user_id or 0)
        platform = str(platform or "").strip().lower()
        license_key = str(license_key or "").strip()
        if user_id <= 0 or not platform or not license_key:
            return {}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        activated_text = str(activated_at or "").strip() or now
        with self._lock:
            conn = self._connect()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO auth_user_platforms (
                            user_id, platform, license_key, status, activated_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            license_key = VALUES(license_key),
                            status = VALUES(status),
                            activated_at = VALUES(activated_at),
                            updated_at = VALUES(updated_at)
                        """,
                        (
                            user_id,
                            platform,
                            license_key,
                            str(status or "activated").strip() or "activated",
                            activated_text,
                            now,
                        ),
                    )
                conn.commit()
            finally:
                conn.close()
        platform_map = self.load_user_platform_licenses(user_id)
        return dict(platform_map.get(platform, {}))

    def load_user_platform_licenses(self, user_id: int) -> Dict[str, Dict]:
        user_id = int(user_id or 0)
        if user_id <= 0:
            return {}
        with self._lock:
            conn = self._connect()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT id, user_id, platform, license_key, status, activated_at, updated_at
                        FROM auth_user_platforms
                        WHERE user_id = %s
                        ORDER BY id
                        """,
                        (user_id,),
                    )
                    rows = cursor.fetchall()
            finally:
                conn.close()
        result: Dict[str, Dict] = {}
        for row in rows:
            platform = str(row["platform"] or "").strip().lower()
            if not platform:
                continue
            result[platform] = RuntimeStateStore._hydrate_auth_platform(row)
        return result


def build_auth_store(
    local_store: RuntimeStateStore,
    project_root: Optional[Path] = None,
    runtime_root: Optional[Path] = None,
):
    root_dir = Path(project_root) if project_root else Path(__file__).resolve().parent.parent
    env_values: Dict[str, str] = {}

    for candidate in (
        root_dir / ".env",
        root_dir / "backend" / ".env",
    ):
        env_values.update(_read_simple_env_file(candidate))

    if runtime_root:
        runtime_dir = Path(runtime_root)
        for candidate in (
            runtime_dir / "auth_db.env",
            runtime_dir / "data" / "auth_db.env",
        ):
            env_values.update(_read_simple_env_file(candidate))

    def pick(*keys: str, default: str = "") -> str:
        for key in keys:
            value = os.environ.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
            file_value = env_values.get(key, "")
            if str(file_value).strip():
                return str(file_value).strip()
        return default

    host = pick("AUTH_DB_HOST", default="")
    port_text = pick("AUTH_DB_PORT", default="3306")
    user = pick("AUTH_DB_USER", default="")
    password = pick("AUTH_DB_PASSWORD", default="")
    database = pick("AUTH_DB_NAME", default="")

    try:
        port = int(port_text or 3306)
    except Exception:
        port = 3306

    remote_store = RemoteAuthStore(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
    )
    if remote_store.enabled:
        return remote_store
    return local_store
