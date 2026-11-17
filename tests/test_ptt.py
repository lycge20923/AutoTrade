from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from autotrade.config import PttConfig
from autotrade.sources.base import SourceError
from autotrade.sources.ptt import PttSearchSource, parse_search_page, published_from_url, search_url

HTML = (Path(__file__).parent / "fixtures" / "ptt_search_dram.html").read_text(encoding="utf-8")
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def test_parse_search_page_skips_deleted_and_old() -> None:
    items = parse_search_page(HTML, "Stock", now=NOW, max_age=timedelta(days=3))
    assert [it.title for it in items] == [
        "Re: [新聞] 傳輝達找台積電生產3D DRAMAI記憶體供應",
        "[新聞] 傳特斯拉 Terafab 將自製 DRAM 與台積重",
    ]
    first = items[0]
    assert first.url == "https://www.ptt.cc/bbs/Stock/M.1794286945.A.4CC.html"
    assert first.published_at == datetime(2026, 11, 10, 5, 2, 25, tzinfo=timezone.utc)
    assert first.publisher == "PTT Stock · uodam64402 · 推 14"
    assert first.source_type == "social"


def test_published_from_url() -> None:
    assert published_from_url("/bbs/Stock/M.1794269653.A.011.html") == datetime(
        2026, 11, 10, 0, 14, 13, tzinfo=timezone.utc
    )
    assert published_from_url("/bbs/Stock/index.html") is None


def test_missing_list_means_layout_changed() -> None:
    with pytest.raises(SourceError, match="結構"):
        parse_search_page("<html><body>維護中</body></html>", "Stock", now=NOW, max_age=timedelta(days=3))


def test_empty_result_page() -> None:
    html = '<div class="r-list-container action-bar-margin bbs-screen"></div>'
    assert parse_search_page(html, "Stock", now=NOW, max_age=timedelta(days=3)) == []


def test_search_url_encodes_query() -> None:
    assert parse_qs(urlparse(search_url("Stock", "南亞科")).query) == {"q": ["南亞科"]}


def _source(handler, queries: list[str]) -> PttSearchSource:
    return PttSearchSource(
        PttConfig(queries=queries, max_age_days=3),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        delay_seconds=0,
        now_fn=lambda: NOW,
    )


def test_fetch_dedupes_across_queries() -> None:
    src = _source(lambda r: httpx.Response(200, text=HTML), ["DRAM", "記憶體"])
    assert len(src.fetch()) == 2


def test_404_is_treated_as_no_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        q = parse_qs(urlparse(str(request.url)).query)["q"][0]
        return httpx.Response(404) if q == "沒有這個詞" else httpx.Response(200, text=HTML)

    assert len(_source(handler, ["沒有這個詞", "DRAM"]).fetch()) == 2


def test_all_queries_failing_raises() -> None:
    with pytest.raises(SourceError, match="所有查詢都失敗"):
        _source(lambda r: httpx.Response(500), ["DRAM", "NAND"]).fetch()
