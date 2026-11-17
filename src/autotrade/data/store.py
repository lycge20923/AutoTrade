"""數據的資料庫讀寫。交易日期一律用台北時間的 YYYY-MM-DD 字串。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from autotrade.data.markets import Flow, Margin, Market, Revenue
from autotrade.data.prices import Quote
from autotrade.storage.repository import to_iso


# ---------- 抓取紀錄 ----------

def known_days(conn: sqlite3.Connection, dataset: str, market: Market) -> set[str]:
    rows = conn.execute(
        "SELECT date FROM data_days WHERE dataset = ? AND market = ?", (dataset, market)
    ).fetchall()
    return {r["date"] for r in rows}


def mark_day(
    conn: sqlite3.Connection, dataset: str, market: Market, day: date, status: str, now: datetime
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO data_days (dataset, market, date, status, fetched_at) VALUES (?, ?, ?, ?, ?)",
        (dataset, market, day.isoformat(), status, to_iso(now)),
    )
    conn.commit()


# ---------- 寫入 ----------

def save_flows(conn: sqlite3.Connection, day: date, market: Market, flows: list[Flow]) -> int:
    n = 0
    for f in flows:
        cur = conn.execute(
            """INSERT OR REPLACE INTO institutional_flows
               (date, code, market, foreign_net, trust_net, dealer_net, total_net)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (day.isoformat(), f.code, market, f.foreign_net, f.trust_net, f.dealer_net, f.total_net),
        )
        n += cur.rowcount
    conn.commit()
    return n


def save_margins(conn: sqlite3.Connection, day: date, market: Market, rows: list[Margin]) -> int:
    n = 0
    for m in rows:
        cur = conn.execute(
            """INSERT OR REPLACE INTO margin_balances
               (date, code, market, margin_prev, margin_balance, short_prev, short_balance)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (day.isoformat(), m.code, market, m.margin_prev, m.margin_balance, m.short_prev, m.short_balance),
        )
        n += cur.rowcount
    conn.commit()
    return n


def save_revenues(conn: sqlite3.Connection, rows: list[Revenue], now: datetime) -> list[Revenue]:
    """回傳新的（之前沒有的）月營收。"""
    new = []
    for r in rows:
        cur = conn.execute(
            """INSERT OR IGNORE INTO monthly_revenue
               (code, year_month, revenue, prev_month, last_year, mom_pct, yoy_pct, note, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (r.code, r.year_month, r.revenue, r.prev_month, r.last_year, r.mom_pct, r.yoy_pct,
             r.note, to_iso(now)),
        )
        if cur.rowcount:
            new.append(r)
    conn.commit()
    return new


def save_quotes(conn: sqlite3.Connection, quotes: list[Quote], now: datetime) -> int:
    n = 0
    for q in quotes:
        cur = conn.execute(
            """INSERT OR IGNORE INTO price_quotes
               (item, frequency, average, change_pct, high, low, quoted_at, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (q.item, q.frequency, q.average, q.change_pct, q.high, q.low, to_iso(q.quoted_at), to_iso(now)),
        )
        n += cur.rowcount
    conn.commit()
    return n


# ---------- 讀取 ----------

@dataclass(frozen=True)
class FlowRow:
    date: str
    market: str
    foreign_net: int
    trust_net: int
    dealer_net: int
    total_net: int


@dataclass(frozen=True)
class MarginRow:
    date: str
    code: str
    market: str
    margin_prev: int
    margin_balance: int
    short_prev: int
    short_balance: int


@dataclass(frozen=True)
class QuoteRow:
    item: str
    frequency: str
    average: float
    change_pct: float | None
    quoted_at: datetime


def recent_flows(conn: sqlite3.Connection, code: str, limit: int = 60) -> list[FlowRow]:
    """新的在前。"""
    rows = conn.execute(
        """SELECT date, market, foreign_net, trust_net, dealer_net, total_net
           FROM institutional_flows WHERE code = ? ORDER BY date DESC LIMIT ?""",
        (code, limit),
    ).fetchall()
    return [FlowRow(**dict(r)) for r in rows]


def recent_margins(conn: sqlite3.Connection, code: str, limit: int = 60) -> list[MarginRow]:
    rows = conn.execute(
        "SELECT * FROM margin_balances WHERE code = ? ORDER BY date DESC LIMIT ?", (code, limit)
    ).fetchall()
    return [MarginRow(**dict(r)) for r in rows]


def margins_since(conn: sqlite3.Connection, since: date) -> list[MarginRow]:
    rows = conn.execute(
        "SELECT * FROM margin_balances WHERE date >= ? ORDER BY date, code", (since.isoformat(),)
    ).fetchall()
    return [MarginRow(**dict(r)) for r in rows]


def latest_revenue(conn: sqlite3.Connection, code: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM monthly_revenue WHERE code = ? ORDER BY year_month DESC LIMIT 1", (code,)
    ).fetchone()


def _quote(row: sqlite3.Row | None) -> QuoteRow | None:
    if row is None:
        return None
    return QuoteRow(row["item"], row["frequency"], row["average"], row["change_pct"],
                    datetime.fromisoformat(row["quoted_at"]))


def latest_quote(conn: sqlite3.Connection, item: str) -> QuoteRow | None:
    return _quote(conn.execute(
        "SELECT * FROM price_quotes WHERE item = ? ORDER BY quoted_at DESC LIMIT 1", (item,)
    ).fetchone())


def quote_at_or_before(conn: sqlite3.Connection, item: str, when: datetime) -> QuoteRow | None:
    return _quote(conn.execute(
        "SELECT * FROM price_quotes WHERE item = ? AND quoted_at <= ? ORDER BY quoted_at DESC LIMIT 1",
        (item, to_iso(when)),
    ).fetchone())


def quote_items(conn: sqlite3.Connection) -> list[str]:
    return [r["item"] for r in conn.execute("SELECT DISTINCT item FROM price_quotes ORDER BY item")]
