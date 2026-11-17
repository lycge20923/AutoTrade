"""資料庫讀寫。時間一律以 UTC ISO 8601 字串儲存。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Literal

from autotrade.models import Item

NotifyKind = Literal["alert", "digest"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("時間必須帶時區")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass(frozen=True)
class StoredItem:
    id: int
    source: str
    source_type: str
    title: str
    url: str
    publisher: str
    content: str
    published_at: datetime | None
    fetched_at: datetime
    matched_keywords: tuple[str, ...]

    @property
    def sort_time(self) -> datetime:
        return self.published_at or self.fetched_at


def _row_to_item(row: sqlite3.Row) -> StoredItem:
    return StoredItem(
        id=row["id"],
        source=row["source"],
        source_type=row["source_type"],
        title=row["title"],
        url=row["url"],
        publisher=row["publisher"],
        content=row["content"],
        published_at=_from_iso(row["published_at"]),
        fetched_at=_from_iso(row["fetched_at"]),
        matched_keywords=tuple(json.loads(row["matched_keywords"])),
    )


def save_items(conn: sqlite3.Connection, items: Iterable[Item], fetched_at: datetime) -> list[int]:
    """存入資料，網址已存在的會略過。回傳新存入的 id。"""
    new_ids: list[int] = []
    fetched = to_iso(fetched_at)
    for it in items:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO items
                (source, source_type, url, title, content, publisher,
                 published_at, fetched_at, matched_keywords)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                it.source,
                it.source_type,
                it.url,
                it.title,
                it.content,
                it.publisher,
                to_iso(it.published_at) if it.published_at else None,
                fetched,
                json.dumps(list(it.matched_keywords), ensure_ascii=False),
            ),
        )
        if cur.rowcount:
            new_ids.append(cur.lastrowid)
    conn.commit()
    return new_ids


def start_run(conn: sqlite3.Connection, source: str, started_at: datetime) -> int:
    cur = conn.execute(
        "INSERT INTO source_runs (source, started_at, status) VALUES (?, ?, 'running')",
        (source, to_iso(started_at)),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: Literal["ok", "error"],
    finished_at: datetime,
    items_fetched: int = 0,
    items_new: int = 0,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE source_runs
        SET status = ?, finished_at = ?, items_fetched = ?, items_new = ?, error = ?
        WHERE id = ?
        """,
        (status, to_iso(finished_at), items_fetched, items_new, error, run_id),
    )
    conn.commit()


def pending_items(
    conn: sqlite3.Connection, *, kind: NotifyKind, channel: str, since: datetime
) -> list[StoredItem]:
    """在 since 之後抓到、還沒用這個管道通知過的資料，新的在前。"""
    rows = conn.execute(
        """
        SELECT i.* FROM items i
        WHERE i.fetched_at >= ?
          AND NOT EXISTS (
              SELECT 1 FROM notifications n
              WHERE n.item_id = i.id AND n.kind = ? AND n.channel = ?
          )
        ORDER BY COALESCE(i.published_at, i.fetched_at) DESC, i.id DESC
        """,
        (to_iso(since), kind, channel),
    ).fetchall()
    return [_row_to_item(r) for r in rows]


def mark_notified(
    conn: sqlite3.Connection,
    item_ids: Iterable[int],
    *,
    kind: NotifyKind,
    channel: str,
    sent_at: datetime,
) -> None:
    sent = to_iso(sent_at)
    conn.executemany(
        "INSERT OR IGNORE INTO notifications (item_id, kind, channel, sent_at) VALUES (?, ?, ?, ?)",
        [(i, kind, channel, sent) for i in item_ids],
    )
    conn.commit()


def consecutive_failures(conn: sqlite3.Connection, source: str) -> int:
    """這個來源從最近一次往回數，連續失敗了幾次。"""
    rows = conn.execute(
        """
        SELECT status FROM source_runs
        WHERE source = ? AND status != 'running'
        ORDER BY id DESC LIMIT 100
        """,
        (source,),
    ).fetchall()
    count = 0
    for row in rows:
        if row["status"] != "error":
            break
        count += 1
    return count
