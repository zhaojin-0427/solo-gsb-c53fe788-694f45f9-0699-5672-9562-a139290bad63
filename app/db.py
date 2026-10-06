"""SQLite 存储层：档案、记录、缺档事件。"""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
DB_PATH = os.path.join(DATA_DIR, "replay.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS archives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    stored_path TEXT,
    status TEXT NOT NULL DEFAULT 'importing',   -- importing | ready | failed
    error TEXT,
    record_count INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT
);

CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_id INTEGER NOT NULL REFERENCES archives(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    ts TEXT NOT NULL,          -- WARC-Date (ISO 8601)
    epoch REAL NOT NULL,       -- 采集时间（秒，UTC）
    type TEXT NOT NULL,        -- response | revisit | resource
    status INTEGER,            -- HTTP 状态码
    mime TEXT,                 -- Content-Type（含字符集）
    digest TEXT,               -- 规范化后的 payload 摘要 algo:VALUE
    headers TEXT,              -- JSON [[k, v], ...]
    body BLOB,                 -- 响应正文（revisit 为空）
    refers_to TEXT
);
CREATE INDEX IF NOT EXISTS idx_records_url_epoch ON records(url, epoch);
CREATE INDEX IF NOT EXISTS idx_records_digest ON records(digest);

CREATE TABLE IF NOT EXISTS missing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    cutoff TEXT,
    reason TEXT NOT NULL,
    referer TEXT,
    epoch REAL NOT NULL        -- 缺档发生时间
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
