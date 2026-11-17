"""把數據的明顯變化轉成「提醒」，以 Item 的形式存進 items 表。

每則提醒都有唯一的 key（放在網址裡），同一件事只會通知一次，
所以每次執行都可以放心重新檢查所有規則。
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from autotrade.config import DataRulesConfig
from autotrade.data import store
from autotrade.data.markets import FLOWS_PAGE, MARGIN_PAGE, REVENUE_PAGE, Revenue
from autotrade.data.prices import PRICE_PAGE
from autotrade.models import Item

TAIPEI = ZoneInfo("Asia/Taipei")


def with_ref(base: str, key: str) -> str:
    """在連結後面加上提醒的唯一 key，同時用來去除重複。"""
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}autotrade={quote(key, safe=':')}"


def _day_start(day: str) -> datetime:
    return datetime.combine(date.fromisoformat(day), time(0), tzinfo=TAIPEI).astimezone(timezone.utc)


def _lots(shares: int) -> str:
    return f"{shares / 1000:+,.0f} 張"


def price_alerts(
    conn: sqlite3.Connection, items: list[str], rules: DataRulesConfig, window_start: date
) -> list[Item]:
    out = []
    for item in items:
        latest = store.latest_quote(conn, item)
        if latest is None or latest.quoted_at.astimezone(TAIPEI).date() < window_start:
            continue
        # 與約 7 天前比較；沒有那麼舊的紀錄時，週報價直接用網站提供的週漲跌
        base = store.quote_at_or_before(conn, item, latest.quoted_at - timedelta(days=6, hours=12))
        if base is not None and base.average > 0:
            pct = (latest.average / base.average - 1) * 100
            detail = f"（{base.average:.3f} → {latest.average:.3f} 美元）"
        elif latest.frequency == "weekly" and latest.change_pct is not None:
            pct = latest.change_pct
            detail = f"（均價 {latest.average:.3f} 美元）"
        else:
            continue
        if abs(pct) < rules.price_change_pct:
            continue
        direction = "漲價" if pct > 0 else "跌價"
        key = f"price:{item}:{latest.quoted_at.isoformat()}"
        out.append(Item(
            source="prices",
            source_type="data",
            title=f"{item} 現貨價週{'漲' if pct > 0 else '跌'} {pct:+.1f}%{detail}",
            url=with_ref(PRICE_PAGE, key),
            published_at=latest.quoted_at,
            publisher="DRAMeXchange",
            matched_keywords=("現貨價", direction),
        ))
    return out


def flow_alerts(
    conn: sqlite3.Connection, names: dict[str, str], rules: DataRulesConfig, window_start: date
) -> list[Item]:
    out = []
    for code, name in names.items():
        rows = store.recent_flows(conn, code, limit=60)
        if not rows or date.fromisoformat(rows[0].date) < window_start or rows[0].foreign_net == 0:
            continue
        buying = rows[0].foreign_net > 0
        streak = 0
        for r in rows:
            if r.foreign_net == 0 or (r.foreign_net > 0) != buying:
                break
            streak += 1
        if streak < rules.foreign_streak_days:
            continue
        start = rows[streak - 1].date
        total = sum(r.foreign_net for r in rows[:streak])
        word = "買超" if buying else "賣超"
        # key 用連續期間的起始日：同一段連買／連賣只提醒一次
        key = f"flow:{code}:{'buy' if buying else 'sell'}:{start}"
        out.append(Item(
            source="flows",
            source_type="data",
            title=f"{name}（{code}）外資連續 {streak} 日{word}，累計 {_lots(total)}",
            url=with_ref(FLOWS_PAGE.get(rows[0].market, FLOWS_PAGE["上市"]), key),
            published_at=_day_start(rows[0].date),
            publisher=f"{rows[0].market}三大法人",
            matched_keywords=(name, f"外資連{'買' if buying else '賣'}"),
        ))
    return out


def margin_alerts(
    conn: sqlite3.Connection, names: dict[str, str], rules: DataRulesConfig, window_start: date
) -> list[Item]:
    out = []
    for m in store.margins_since(conn, window_start):
        if m.code not in names or m.margin_prev < rules.margin_min_balance or m.margin_prev <= 0:
            continue
        pct = (m.margin_balance / m.margin_prev - 1) * 100
        if pct < rules.margin_surge_pct:
            continue
        name = names[m.code]
        key = f"margin:{m.code}:{m.date}"
        out.append(Item(
            source="margin",
            source_type="data",
            title=(f"{name}（{m.code}）融資餘額單日增加 {pct:.1f}%"
                   f"（{m.margin_prev:,} → {m.margin_balance:,} 張）"),
            url=with_ref(MARGIN_PAGE.get(m.market, MARGIN_PAGE["上市"]), key),
            published_at=_day_start(m.date),
            publisher=f"{m.market}融資融券",
            matched_keywords=(name, "融資暴增"),
        ))
    return out


def revenue_alerts(new_rows: list[Revenue], names: dict[str, str], now: datetime) -> list[Item]:
    out = []
    for r in new_rows:
        name = names.get(r.code, r.code)
        parts = [f"{name}（{r.code}）{int(r.year_month[5:])} 月營收 {r.revenue / 100_000:,.1f} 億元"]
        changes = []
        if r.mom_pct is not None:
            changes.append(f"月增 {r.mom_pct:+.1f}%")
        if r.yoy_pct is not None:
            changes.append(f"年增 {r.yoy_pct:+.1f}%")
        title = parts[0] + ("，" + "、".join(changes) if changes else "")
        key = f"revenue:{r.code}:{r.year_month}"
        out.append(Item(
            source="revenue",
            source_type="data",
            title=title,
            url=with_ref(REVENUE_PAGE, key),
            published_at=now,
            content=r.note,
            publisher="月營收",
            matched_keywords=(name, "月營收"),
        ))
    return out
