"""數據線的執行流程：抓取 → 存檔 → 檢查提醒規則。

每個數據集（prices、flows、margin、revenue）各自記錄執行結果，
與文字來源共用「連續失敗提醒」的機制；其中一個失敗不影響其他。
"""

from __future__ import annotations

import logging
import sqlite3
import time as _time
from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

import httpx

from autotrade.config import Settings
from autotrade.data import markets, prices, rules, store
from autotrade.data.markets import Market
from autotrade.models import Item, SourceResult
from autotrade.storage.repository import consecutive_failures, finish_run, start_run, utcnow

log = logging.getLogger(__name__)

TAIPEI = ZoneInfo("Asia/Taipei")
MARKETS: tuple[Market, ...] = ("上市", "上櫃")
USER_AGENT = "Mozilla/5.0 (compatible; AutoTrade/0.1; personal news monitor)"

Sleep = Callable[[float], None]


def make_client() -> httpx.Client:
    return httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def candidate_days(today: date, lookback_days: int) -> list[date]:
    """最近 lookback_days 天內的週一到週五，舊的在前。"""
    days = [today - timedelta(days=i) for i in range(lookback_days)]
    return sorted(d for d in days if d.weekday() < 5)


class _Throttle:
    """控制請求間隔；第一次請求不等待。"""

    def __init__(self, delay: float, sleep: Sleep) -> None:
        self.delay, self.sleep, self.first = delay, sleep, True

    def wait(self) -> None:
        if not self.first and self.delay > 0:
            self.sleep(self.delay)
        self.first = False


def update_daily(
    conn: sqlite3.Connection,
    client: httpx.Client,
    dataset: str,
    codes: set[str],
    today: date,
    lookback_days: int,
    throttle: _Throttle,
    now: datetime,
    progress: Callable[[str], None] | None = None,
) -> tuple[int, int]:
    """補抓缺少的交易日資料，回傳 (抓到筆數, 新增筆數)。"""
    fetch = markets.fetch_flows if dataset == "flows" else markets.fetch_margin
    save = store.save_flows if dataset == "flows" else store.save_margins
    fetched = new = 0
    for market in MARKETS:
        known = store.known_days(conn, dataset, market)
        for day in candidate_days(today, lookback_days):
            if day.isoformat() in known:
                continue
            throttle.wait()
            rows = fetch(client, market, day, codes)
            if rows is None:
                # 今天的資料可能還沒公布，下次再試；過去的日期沒資料就是休市
                if day < today:
                    store.mark_day(conn, dataset, market, day, "empty", now)
                continue
            new += save(conn, day, market, rows)
            fetched += len(rows)
            store.mark_day(conn, dataset, market, day, "ok", now)
            if progress:
                progress(f"{dataset} {market} {day}：{len(rows)} 筆")
    return fetched, new


def _run_dataset(
    conn: sqlite3.Connection,
    name: str,
    fn: Callable[[], tuple[int, int, list[Item]]],
    failure_threshold: int,
) -> tuple[SourceResult, list[Item], str | None]:
    result = SourceResult(name=name, kind="data")
    run_id = start_run(conn, name, utcnow())
    try:
        result.fetched, result.new, alerts = fn()
    except Exception as exc:  # 數據集之間互不影響
        log.error("數據 %s 失敗：%s", name, exc)
        result.error = str(exc) or type(exc).__name__
        finish_run(conn, run_id, status="error", finished_at=utcnow(), error=result.error)
        result.consecutive_failures = consecutive_failures(conn, name)
        warning = None
        if result.consecutive_failures == failure_threshold:
            warning = f"{name} 已連續失敗 {failure_threshold} 次，最近一次錯誤：{result.error}"
        return result, [], warning
    result.relevant = len(alerts)
    finish_run(conn, run_id, status="ok", finished_at=utcnow(),
               items_fetched=result.fetched, items_new=result.new)
    log.info("數據 %s：抓到 %d 筆，新增 %d 筆，提醒 %d 則", name, result.fetched, result.new, len(alerts))
    return result, alerts, None


def update_data(
    conn: sqlite3.Connection,
    settings: Settings,
    now: datetime,
    client: httpx.Client | None = None,
    sleep: Sleep = _time.sleep,
) -> tuple[list[SourceResult], list[Item], list[str]]:
    """回傳 (各數據集結果, 提醒, 來源異常警示)。"""
    cfg = settings.app.data
    names = {s.code: s.name for s in settings.app.watchlist.taiwan}
    codes = set(names)
    today = now.astimezone(TAIPEI).date()
    window_start = today - timedelta(days=cfg.rules.alert_window_days - 1)
    threshold = settings.app.health.failure_alert_threshold
    own_client = client is None
    client = client or make_client()
    throttle = _Throttle(cfg.request_delay_seconds, sleep)

    def do_prices():
        quotes = prices.fetch_quotes(client)
        new = store.save_quotes(conn, quotes, now)
        return len(quotes), new, rules.price_alerts(conn, cfg.prices.track, cfg.rules, window_start)

    def do_flows():
        f, n = update_daily(conn, client, "flows", codes, today, cfg.lookback_days, throttle, now)
        return f, n, rules.flow_alerts(conn, names, cfg.rules, window_start)

    def do_margin():
        f, n = update_daily(conn, client, "margin", codes, today, cfg.lookback_days, throttle, now)
        return f, n, rules.margin_alerts(conn, names, cfg.rules, window_start)

    def do_revenue():
        fetched, new_rows = 0, []
        for market in MARKETS:
            rows = markets.fetch_revenue(client, market, codes)
            fetched += len(rows)
            new_rows += store.save_revenues(conn, rows, now)
        alerts = rules.revenue_alerts(new_rows, names, now) if cfg.rules.revenue_release else []
        return fetched, len(new_rows), alerts

    datasets = [("flows", do_flows), ("margin", do_margin), ("revenue", do_revenue)]
    if cfg.prices.enabled:
        datasets.insert(0, ("prices", do_prices))

    results, alerts, warnings = [], [], []
    try:
        for name, fn in datasets:
            result, items, warning = _run_dataset(conn, name, fn, threshold)
            results.append(result)
            alerts += items
            if warning:
                warnings.append(warning)
    finally:
        if own_client:
            client.close()
    return results, alerts, warnings


def backfill(
    conn: sqlite3.Connection,
    settings: Settings,
    days: int,
    now: datetime,
    client: httpx.Client | None = None,
    sleep: Sleep = _time.sleep,
    progress: Callable[[str], None] | None = None,
) -> dict[str, tuple[int, int]]:
    """回補過去 days 天的三大法人與融資融券（不產生提醒）。"""
    codes = {s.code for s in settings.app.watchlist.taiwan}
    today = now.astimezone(TAIPEI).date()
    own_client = client is None
    client = client or make_client()
    throttle = _Throttle(settings.app.data.request_delay_seconds, sleep)
    try:
        return {
            ds: update_daily(conn, client, ds, codes, today, days, throttle, now, progress)
            for ds in ("flows", "margin")
        }
    finally:
        if own_client:
            client.close()
