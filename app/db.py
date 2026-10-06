"""SQLite 存储：档案清单、记录索引（含正文）、访问/缺档日志。"""
import os
import sqlite3
import threading

from . import config

_init_lock = threading.Lock()
_initialized = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS archives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'importing',   -- importing | ok | failed
    error TEXT,
    record_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_id INTEGER NOT NULL REFERENCES archives(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    ts TEXT NOT NULL,                -- WARC-Date (ISO 8601)
    ts_epoch REAL NOT NULL,
    record_type TEXT NOT NULL,       -- response | revisit | resource
    http_status INTEGER,
    http_headers TEXT,               -- JSON [[name, value], ...]
    content_type TEXT,
    digest TEXT,                     -- 归一化后的负载摘要 algo:hash
    refers_to TEXT,
    payload BLOB                     -- HTTP 正文；revisit 为 NULL
);
CREATE INDEX IF NOT EXISTS idx_records_url_ts ON records(archive_id, url, ts_epoch);
CREATE INDEX IF NOT EXISTS idx_records_digest ON records(archive_id, digest, ts_epoch);

CREATE TABLE IF NOT EXISTS access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_id INTEGER NOT NULL REFERENCES archives(id) ON DELETE CASCADE,
    logged_at TEXT NOT NULL,         -- 请求发生时间
    requested_url TEXT NOT NULL,     -- 原址（浏览器请求的原始 URL）
    cutoff_epoch REAL NOT NULL,
    hit INTEGER NOT NULL,            -- 1 命中 / 0 缺档
    http_status INTEGER,
    hit_ts TEXT,                     -- 命中记录的采集时间
    record_type TEXT,
    reason TEXT,                     -- 缺档原因
    via TEXT                         -- document / image / script / ...（Sec-Fetch-Dest）
);
CREATE INDEX IF NOT EXISTS idx_log_archive ON access_log(archive_id, id);
"""


def connect():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    global _initialized
    with _init_lock:
        if _initialized:
            return
        os.makedirs(config.UPLOAD_DIR, exist_ok=True)
        conn = connect()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()
        _initialized = True


def log_access(archive_id, requested_url, cutoff_epoch, hit, http_status=None,
               hit_ts=None, record_type=None, reason=None, via=None):
    from datetime import datetime, timezone
    conn = connect()
    try:
        conn.execute(
            """INSERT INTO access_log
               (archive_id, logged_at, requested_url, cutoff_epoch, hit,
                http_status, hit_ts, record_type, reason, via)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (archive_id, datetime.now(timezone.utc).isoformat(timespec="seconds"),
             requested_url, cutoff_epoch, 1 if hit else 0,
             http_status, hit_ts, record_type, reason, via),
        )
        conn.commit()
    finally:
        conn.close()
