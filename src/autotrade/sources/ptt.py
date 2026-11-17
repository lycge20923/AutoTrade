"""PTT 看板搜尋（預設 Stock 板）。

PTT 的搜尋一次只能查一個詞，所以每個查詞各搜尋一次，再合併去重。
文章網址裡有發文時間（M.1794286945.A.4CC 的數字是 Unix 時間），
用它來判斷文章新舊；搜尋結果會包含很久以前的文章，超過 max_age_days 的略過。
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlencode, urljoin

import httpx
from bs4 import BeautifulSoup

from autotrade.config import PttConfig
from autotrade.models import Item
from autotrade.sources.base import Source, SourceError

log = logging.getLogger(__name__)

BASE_URL = "https://www.ptt.cc"
USER_AGENT = "Mozilla/5.0 (compatible; AutoTrade/0.1; personal news monitor)"
_EPOCH_RE = re.compile(r"/M\.(\d{9,11})\.A\.")


def search_url(board: str, query: str) -> str:
    return f"{BASE_URL}/bbs/{board}/search?{urlencode({'q': query})}"


def published_from_url(href: str) -> datetime | None:
    m = _EPOCH_RE.search(href)
    return datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc) if m else None


def parse_search_page(
    html: str, board: str, *, now: datetime, max_age: timedelta
) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("div.r-list-container")
    if container is None:
        raise SourceError("找不到文章列表，PTT 頁面結構可能改變了")

    items: list[Item] = []
    for ent in container.select("div.r-ent"):
        link = ent.select_one("div.title a")
        if link is None or not link.get("href"):
            continue  # 已刪除的文章沒有連結
        href = link["href"]
        published = published_from_url(href)
        if published is not None and now - published > max_age:
            continue

        author = ent.select_one("div.author")
        nrec = ent.select_one("div.nrec")
        author_text = author.get_text(strip=True) if author else ""
        nrec_text = nrec.get_text(strip=True) if nrec else ""
        publisher = " · ".join(
            x for x in (f"PTT {board}", author_text, f"推 {nrec_text}" if nrec_text else "") if x
        )
        items.append(
            Item(
                source="ptt",
                source_type="social",
                title=link.get_text(strip=True),
                url=urljoin(BASE_URL, href),
                published_at=published,
                publisher=publisher,
            )
        )
    return items


class PttSearchSource(Source):
    name = "ptt"
    source_type = "social"

    def __init__(
        self,
        cfg: PttConfig,
        client: httpx.Client | None = None,
        delay_seconds: float = 1.0,
        timeout_seconds: float = 15.0,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.cfg = cfg
        self._client = client
        self._delay = delay_seconds
        self._timeout = timeout_seconds
        self._now = now_fn

    def fetch(self) -> list[Item]:
        if not self.cfg.queries:
            return []
        client = self._client or httpx.Client(
            timeout=self._timeout,
            headers={"User-Agent": USER_AGENT},
            cookies={"over18": "1"},
            follow_redirects=True,
        )
        try:
            return self._fetch_all(client)
        finally:
            if self._client is None:
                client.close()

    def _fetch_all(self, client: httpx.Client) -> list[Item]:
        now = self._now()
        max_age = timedelta(days=self.cfg.max_age_days)
        seen: set[str] = set()
        items: list[Item] = []
        errors: list[str] = []

        for i, query in enumerate(self.cfg.queries):
            if i > 0 and self._delay > 0:
                time.sleep(self._delay)
            try:
                resp = client.get(search_url(self.cfg.board, query))
                if resp.status_code == 404:  # 查無結果時 PTT 可能回 404
                    continue
                resp.raise_for_status()
                parsed = parse_search_page(resp.text, self.cfg.board, now=now, max_age=max_age)
            except (httpx.HTTPError, SourceError) as exc:
                log.warning("PTT 搜尋失敗「%s」：%s", query, exc)
                errors.append(f"「{query}」{exc}")
                continue
            new = [it for it in parsed if it.url not in seen]
            seen.update(it.url for it in new)
            items.extend(new)
            log.info("PTT %s「%s」：%d 則（新 %d 則）", self.cfg.board, query, len(parsed), len(new))

        if errors and len(errors) == len(self.cfg.queries):
            raise SourceError("所有查詢都失敗：" + "；".join(errors))
        return items
