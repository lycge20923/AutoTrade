"""Google News RSS 搜尋。

一次查詢就能涵蓋鉅亨、經濟日報、工商時報等多家媒體。
連結是 Google News 的轉址網址，在瀏覽器開啟會導向原始報導。
"""

from __future__ import annotations

import calendar
import html
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import feedparser
import httpx

from autotrade.config import GoogleNewsConfig
from autotrade.models import Item
from autotrade.sources.base import Source, SourceError

log = logging.getLogger(__name__)

BASE_URL = "https://news.google.com/rss/search"
USER_AGENT = "Mozilla/5.0 (compatible; AutoTrade/0.1; personal news monitor)"
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def build_url(query: str, cfg: GoogleNewsConfig) -> str:
    q = f"{query} when:{cfg.when}" if cfg.when else query
    return f"{BASE_URL}?{urlencode({'q': q, 'hl': cfg.hl, 'gl': cfg.gl, 'ceid': cfg.ceid})}"


def _strip_html(text: str) -> str:
    return _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", text))).strip()


def _clean_title(title: str, publisher: str) -> str:
    """Google News 的標題結尾會加上「 - 媒體名稱」，拿掉。"""
    title = _SPACE_RE.sub(" ", title).strip()
    suffix = f" - {publisher}"
    if publisher and title.endswith(suffix):
        title = title[: -len(suffix)].rstrip()
    return title


def _published_at(entry: feedparser.FeedParserDict) -> datetime | None:
    st = entry.get("published_parsed")
    if not st:
        return None
    return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)


def parse_feed(content: bytes, source_name: str = "google_news") -> list[Item]:
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise SourceError(f"RSS 解析失敗：{feed.get('bozo_exception')}")

    items: list[Item] = []
    for entry in feed.entries:
        link = (entry.get("link") or "").strip()
        raw_title = (entry.get("title") or "").strip()
        if not link or not raw_title:
            continue
        publisher = ((entry.get("source") or {}).get("title") or "").strip()
        title = _clean_title(raw_title, publisher)

        # Google News 的摘要大多只是「標題 + 媒體名稱」，跟標題重複就不存
        summary = _strip_html(entry.get("summary") or "")
        if summary.startswith(title) and len(summary) <= len(title) + len(publisher) + 5:
            summary = ""

        items.append(
            Item(
                source=source_name,
                source_type="news",
                title=title,
                url=link,
                published_at=_published_at(entry),
                content=summary,
                publisher=publisher,
            )
        )
    return items


class GoogleNewsSource(Source):
    name = "google_news"
    source_type = "news"

    def __init__(
        self,
        cfg: GoogleNewsConfig,
        client: httpx.Client | None = None,
        name: str = "google_news",
        delay_seconds: float = 1.0,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.cfg = cfg
        self.name = name
        self._client = client
        self._delay = delay_seconds
        self._timeout = timeout_seconds

    def fetch(self) -> list[Item]:
        if not self.cfg.queries:
            return []

        client = self._client or httpx.Client(
            timeout=self._timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        try:
            return self._fetch_all(client)
        finally:
            if self._client is None:
                client.close()

    def _fetch_all(self, client: httpx.Client) -> list[Item]:
        seen: set[str] = set()
        items: list[Item] = []
        errors: list[str] = []

        for i, query in enumerate(self.cfg.queries):
            if i > 0 and self._delay > 0:
                time.sleep(self._delay)  # 查詢之間稍微間隔，避免被當成攻擊
            try:
                resp = client.get(build_url(query, self.cfg))
                resp.raise_for_status()
                parsed = parse_feed(resp.content, self.name)
            except (httpx.HTTPError, SourceError) as exc:
                log.warning("%s 查詢失敗「%s」：%s", self.name, query, exc)
                errors.append(f"「{query}」{exc}")
                continue

            new = [it for it in parsed if it.url not in seen]
            seen.update(it.url for it in new)
            items.extend(new)
            log.info("%s「%s」：%d 則（新 %d 則）", self.name, query, len(parsed), len(new))

        if errors and len(errors) == len(self.cfg.queries):
            raise SourceError("所有查詢都失敗：" + "；".join(errors))
        return items
