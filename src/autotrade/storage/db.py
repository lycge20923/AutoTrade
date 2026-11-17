"""SQLite 資料庫。

用 PRAGMA user_version 記錄資料表版本；之後需要新增資料表時，
在 MIGRATIONS 後面加一段，舊資料庫會自動升級，不會遺失資料。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# 時間一律以 ISO 8601 UTC 字串儲存，例如 2026-11-18T01:00:00+00:00
MIGRATIONS: list[str] = [
    # v1：第 0 階段的基礎資料表
    """
    CREATE TABLE items (
        id               INTEGER PRIMARY KEY,
        source           TEXT NOT NULL,
        source_type      TEXT NOT NULL CHECK (source_type IN ('news', 'official', 'data', 'social')),
        url              TEXT NOT NULL UNIQUE,
        title            TEXT NOT NULL,
        content          TEXT NOT NULL DEFAULT '',
        published_at     TEXT,
        fetched_at       TEXT NOT NULL,
        matched_keywords TEXT NOT NULL DEFAULT '[]'  -- JSON 陣列
    );
    CREATE INDEX idx_items_published_at ON items (published_at);
    CREATE INDEX idx_items_source ON items (source);

    -- 每次抓取的執行紀錄，用來偵測某個來源是否壞掉
    CREATE TABLE source_runs (
        id            INTEGER PRIMARY KEY,
        source        TEXT NOT NULL,
        started_at    TEXT NOT NULL,
        finished_at   TEXT,
        status        TEXT NOT NULL CHECK (status IN ('running', 'ok', 'error')),
        items_fetched INTEGER NOT NULL DEFAULT 0,
        items_new     INTEGER NOT NULL DEFAULT 0,
        error         TEXT
    );
    CREATE INDEX idx_source_runs_source ON source_runs (source, started_at);

    -- 已寄出的通知，避免同一則內容重複通知
    CREATE TABLE notifications (
        id      INTEGER PRIMARY KEY,
        item_id INTEGER NOT NULL REFERENCES items (id) ON DELETE CASCADE,
        kind    TEXT NOT NULL CHECK (kind IN ('alert', 'digest')),
        channel TEXT NOT NULL,
        sent_at TEXT NOT NULL,
        UNIQUE (item_id, kind, channel)
    );
    """,
    # v2：第 1 階段，記錄新聞的發布媒體
    """
    ALTER TABLE items ADD COLUMN publisher TEXT NOT NULL DEFAULT '';
    """,
    # v3：第 3 階段，數據線。交易日期以台北時間 YYYY-MM-DD 儲存
    """
    -- DRAMeXchange 現貨價（美元）
    CREATE TABLE price_quotes (
        id         INTEGER PRIMARY KEY,
        item       TEXT NOT NULL,
        frequency  TEXT NOT NULL CHECK (frequency IN ('daily', 'weekly')),
        average    REAL NOT NULL,
        change_pct REAL,
        high       REAL,
        low        REAL,
        quoted_at  TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        UNIQUE (item, quoted_at)
    );

    -- 三大法人買賣超（股）
    CREATE TABLE institutional_flows (
        date        TEXT NOT NULL,
        code        TEXT NOT NULL,
        market      TEXT NOT NULL,
        foreign_net INTEGER NOT NULL,
        trust_net   INTEGER NOT NULL,
        dealer_net  INTEGER NOT NULL,
        total_net   INTEGER NOT NULL,
        PRIMARY KEY (date, code)
    );

    -- 融資融券餘額（張）
    CREATE TABLE margin_balances (
        date           TEXT NOT NULL,
        code           TEXT NOT NULL,
        market         TEXT NOT NULL,
        margin_prev    INTEGER NOT NULL,
        margin_balance INTEGER NOT NULL,
        short_prev     INTEGER NOT NULL,
        short_balance  INTEGER NOT NULL,
        PRIMARY KEY (date, code)
    );

    -- 月營收（千元）
    CREATE TABLE monthly_revenue (
        code       TEXT NOT NULL,
        year_month TEXT NOT NULL,  -- 例如 2026-10
        revenue    INTEGER NOT NULL,
        prev_month INTEGER,
        last_year  INTEGER,
        mom_pct    REAL,
        yoy_pct    REAL,
        note       TEXT NOT NULL DEFAULT '',
        fetched_at TEXT NOT NULL,
        PRIMARY KEY (code, year_month)
    );

    -- 每個資料集、每個市場、每一天是否已抓過；empty 代表休市
    CREATE TABLE data_days (
        dataset    TEXT NOT NULL,
        market     TEXT NOT NULL,
        date       TEXT NOT NULL,
        status     TEXT NOT NULL CHECK (status IN ('ok', 'empty')),
        fetched_at TEXT NOT NULL,
        PRIMARY KEY (dataset, market, date)
    );
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def init_db(conn: sqlite3.Connection) -> int:
    """建立或升級資料表，回傳目前版本。重複執行是安全的。"""
    current = get_version(conn)
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"資料庫版本 {current} 比程式支援的 {SCHEMA_VERSION} 還新，請更新程式。"
        )
    for version in range(current + 1, SCHEMA_VERSION + 1):
        script = MIGRATIONS[version - 1]
        # executescript 會自動 commit，所以把版本號寫在同一段 script 裡，確保一起生效
        conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
    return get_version(conn)


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]
