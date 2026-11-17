from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from autotrade.config import GoogleNewsConfig
from autotrade.sources.base import SourceError
from autotrade.sources.google_news import GoogleNewsSource, build_url, parse_feed


def test_parse_feed(sample_feed: bytes) -> None:
    items = parse_feed(sample_feed)
    assert len(items) == 4
    first = items[0]
    assert first.title == "南亞科10月營收創新高 DRAM合約價續揚"  # 已去掉「 - 經濟日報」
    assert first.publisher == "經濟日報"
    assert first.url == "https://news.google.com/rss/articles/AAA111?oc=5"
    assert first.published_at == datetime(2026, 11, 17, 23, 30, tzinfo=timezone.utc)
    assert first.content == ""  # 摘要只是重複標題，不存
    assert first.source_type == "news"
    assert items[3].title == "Micron earnings beat as HBM demand stays strong"


def test_parse_feed_rejects_garbage() -> None:
    with pytest.raises(SourceError):
        parse_feed(b"<html>not a feed")


def test_build_url() -> None:
    url = build_url("記憶體 OR DRAM", GoogleNewsConfig(when="12h"))
    qs = parse_qs(urlparse(url).query)
    assert qs["q"] == ["記憶體 OR DRAM when:12h"]
    assert qs["hl"] == ["zh-TW"] and qs["ceid"] == ["TW:zh-Hant"]


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_dedupes_across_queries(sample_feed: bytes) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(200, content=sample_feed)

    src = GoogleNewsSource(GoogleNewsConfig(queries=["a", "b"]), client=_client(handler), delay_seconds=0)
    items = src.fetch()
    assert len(calls) == 2
    assert len(items) == 4  # 兩個查詢回傳相同新聞，只留一份


def test_partial_failure_keeps_successful_queries(sample_feed: bytes) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        q = parse_qs(urlparse(str(request.url)).query)["q"][0]
        return httpx.Response(500) if q.startswith("bad") else httpx.Response(200, content=sample_feed)

    src = GoogleNewsSource(GoogleNewsConfig(queries=["bad", "good"]), client=_client(handler), delay_seconds=0)
    assert len(src.fetch()) == 4


def test_all_queries_failing_raises() -> None:
    src = GoogleNewsSource(
        GoogleNewsConfig(queries=["a", "b"]),
        client=_client(lambda r: httpx.Response(503)),
        delay_seconds=0,
    )
    with pytest.raises(SourceError, match="所有查詢都失敗"):
        src.fetch()


def test_no_queries_makes_no_requests() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("不應該發出請求")

    assert GoogleNewsSource(GoogleNewsConfig(queries=[]), client=_client(handler)).fetch() == []


def test_invalid_when_is_rejected() -> None:
    with pytest.raises(ValueError):
        GoogleNewsConfig(when="yesterday")
