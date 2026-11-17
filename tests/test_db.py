from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from autotrade.storage import SCHEMA_VERSION, connect, init_db, list_tables


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "nested" / "test.db")
    init_db(c)
    yield c
    c.close()


def _insert_item(conn: sqlite3.Connection, url: str = "https://example.com/a") -> int:
    cur = conn.execute(
        "INSERT INTO items (source, source_type, url, title, fetched_at) VALUES (?, ?, ?, ?, ?)",
        ("test", "news", url, "標題", "2026-11-18T00:00:00+00:00"),
    )
    conn.commit()
    return cur.lastrowid


def test_creates_parent_dir_and_tables(conn: sqlite3.Connection) -> None:
    assert list_tables(conn) == [
        "data_days", "institutional_flows", "items", "margin_balances",
        "monthly_revenue", "notifications", "price_quotes", "source_runs",
    ]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_init_is_idempotent(conn: sqlite3.Connection) -> None:
    _insert_item(conn)
    assert init_db(conn) == SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_duplicate_url_is_rejected(conn: sqlite3.Connection) -> None:
    _insert_item(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_item(conn)


def test_invalid_source_type_is_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO items (source, source_type, url, title, fetched_at) VALUES ('x', 'tv', 'u', 't', 'now')"
        )


def test_notification_requires_existing_item(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO notifications (item_id, kind, channel, sent_at) VALUES (999, 'alert', 'email', 'now')"
        )


def test_same_item_not_notified_twice(conn: sqlite3.Connection) -> None:
    item_id = _insert_item(conn)
    sql = "INSERT INTO notifications (item_id, kind, channel, sent_at) VALUES (?, 'alert', 'email', 'now')"
    conn.execute(sql, (item_id,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, (item_id,))


def test_refuses_newer_database(tmp_path: Path) -> None:
    c = connect(tmp_path / "future.db")
    c.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError, match="更新程式"):
        init_db(c)
    c.close()


def test_upgrades_v1_database_without_losing_data(tmp_path: Path) -> None:
    from autotrade.storage.db import MIGRATIONS

    path = tmp_path / "old.db"
    c = connect(path)
    c.executescript(f"BEGIN;{MIGRATIONS[0]}PRAGMA user_version = 1;COMMIT;")
    c.execute(
        "INSERT INTO items (source, source_type, url, title, fetched_at) "
        "VALUES ('x', 'news', 'https://old', '舊資料', 'now')"
    )
    c.commit()

    assert init_db(c) == SCHEMA_VERSION
    row = c.execute("SELECT title, publisher FROM items").fetchone()
    assert (row["title"], row["publisher"]) == ("舊資料", "")
    c.close()
