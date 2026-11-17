"""DRAMeXchange 首頁的現貨價表格。

首頁有多張價格表，各自標示最後更新時間：
- DRAM 現貨價（每日更新，欄位 Daily High / Daily Low）
- NAND 晶圓、模組、GDDR 等（每週更新，欄位 Weekly High / Weekly Low）
只解析表頭含有 Item 與 Session Average 的表格。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from autotrade.data.markets import to_float
from autotrade.sources.base import SourceError

HOME_URL = "https://www.dramexchange.com/"
PRICE_PAGE = "https://www.dramexchange.com/Price/Dram_Spot"
TAIPEI = ZoneInfo("Asia/Taipei")
USER_AGENT = "Mozilla/5.0 (compatible; AutoTrade/0.1; personal news monitor)"

_LAST_UPDATE_RE = re.compile(
    r"Last\s*Update\s*:\s*([A-Za-z]{3})\.?\s*(\d{1,2}),?\s+(\d{4}),?\s+(\d{1,2}):(\d{2})"
)
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


@dataclass(frozen=True)
class Quote:
    item: str
    frequency: Literal["daily", "weekly"]
    average: float
    change_pct: float | None
    high: float | None
    low: float | None
    quoted_at: datetime  # UTC


def parse_last_update(text: str) -> datetime | None:
    m = _LAST_UPDATE_RE.search(text)
    if not m or m.group(1).title() not in _MONTHS:
        return None
    mon, day, year, hh, mm = _MONTHS[m.group(1).title()], *map(int, m.group(2, 3, 4, 5))
    try:
        return datetime(year, mon, day, hh, mm, tzinfo=TAIPEI).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_home(html: str) -> list[Quote]:
    soup = BeautifulSoup(html, "html.parser")
    quotes: list[Quote] = []
    found_daily = False
    for tbody in soup.select("tbody[id^=tb_]"):
        rows = tbody.find_all("tr")
        if not rows:
            continue
        header = [c.get_text(strip=True) for c in rows[0].find_all("td")]
        if not header or header[0] != "Item" or "Session Average" not in header:
            continue
        frequency: Literal["daily", "weekly"] = "daily" if "Daily High" in header else "weekly"
        avg_i, chg_i = header.index("Session Average"), header.index("Session Average") + 1
        stamp = tbody.find_previous(string=_LAST_UPDATE_RE)
        quoted_at = parse_last_update(str(stamp)) if stamp else None
        if quoted_at is None:
            continue
        found_daily |= frequency == "daily"
        for row in rows[1:]:
            cells = [" ".join(c.get_text(" ", strip=True).split()) for c in row.find_all("td")]
            if len(cells) <= chg_i or not cells[0]:
                continue
            average = to_float(cells[avg_i])
            if average is None:
                continue
            quotes.append(
                Quote(
                    item=cells[0],
                    frequency=frequency,
                    average=average,
                    change_pct=to_float(cells[chg_i].replace("%", "")),
                    high=to_float(cells[1]),
                    low=to_float(cells[2]),
                    quoted_at=quoted_at,
                )
            )
    if not found_daily:
        raise SourceError("DRAMeXchange 找不到 DRAM 每日現貨價表格，網頁結構可能改變了")
    return quotes


def fetch_quotes(client: httpx.Client) -> list[Quote]:
    resp = client.get(HOME_URL, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return parse_home(resp.text)
