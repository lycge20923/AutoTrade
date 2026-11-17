from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from autotrade.config import load_settings
from autotrade.matching import KeywordMatcher
from autotrade.sources.base import SourceError
from autotrade.sources.mops import (
    TPEX_URL,
    TWSE_URL,
    MopsSource,
    parse_announcements,
    parse_roc_datetime,
)

FIXTURES = Path(__file__).parent / "fixtures"
TWSE = json.loads((FIXTURES / "twse_announcements.json").read_text(encoding="utf-8"))
TPEX = json.loads((FIXTURES / "tpex_announcements.json").read_text(encoding="utf-8"))


def test_roc_datetime_converts_to_utc() -> None:
    # 民國 115/11/16 07:00:03 台北 = 2026-11-15 23:00:03 UTC（發言時間沒有補零）
    assert parse_roc_datetime("1151116", "70003") == datetime(2026, 11, 15, 23, 0, 3, tzinfo=timezone.utc)
    assert parse_roc_datetime("1151116", "151835") == datetime(2026, 11, 16, 7, 18, 35, tzinfo=timezone.utc)


@pytest.mark.parametrize("date,time", [("", "70003"), ("115111", "1"), ("1151332", "0"), ("abcdefg", "0")])
def test_roc_datetime_invalid(date: str, time: str) -> None:
    assert parse_roc_datetime(date, time) is None


def test_tpex_record_for_watchlist_company() -> None:
    items = parse_announcements(TPEX, {"8299"}, "上櫃")
    assert len(items) == 1
    it = items[0]
    assert it.title == "群聯（8299）公告本公司本次買回庫藏股金額達新台幣三億元以上"
    assert it.source_type == "official"
    assert it.publisher == "公開資訊觀測站・上櫃"
    assert it.content.startswith("1.本次買回股份數量累積")
    assert "\r" not in it.content
    assert "companyId=8299" in it.url
    assert it.published_at == datetime(2026, 11, 16, 7, 18, 35, tzinfo=timezone.utc)


def test_twse_subject_key_with_trailing_space() -> None:
    items = parse_announcements(TWSE, {"6415"}, "上市")
    assert [it.title for it in items] == ["矽力*-KY（6415）說明2026年11月14日媒體報導"]


def test_multiline_subject_is_collapsed() -> None:
    (it,) = parse_announcements(TWSE, {"2072"}, "上市")
    assert "\n" not in it.title and "更名 為" in it.title


def test_non_watchlist_companies_are_skipped() -> None:
    assert parse_announcements(TWSE, {"2408"}, "上市") == []


def test_url_is_stable_and_unique() -> None:
    a = parse_announcements(TPEX, {"8299", "3489"}, "上櫃")
    b = parse_announcements(TPEX, {"8299", "3489"}, "上櫃")
    assert [x.url for x in a] == [x.url for x in b]
    assert len({x.url for x in a}) == 2


def test_changed_field_names_are_detected() -> None:
    with pytest.raises(SourceError, match="欄位名稱改變"):
        parse_announcements([{"code": "8299", "title": "x"}], {"8299"}, "上櫃")


def test_non_list_response_is_rejected() -> None:
    with pytest.raises(SourceError):
        parse_announcements({"error": "busy"}, {"8299"}, "上櫃")


def test_watchlist_announcement_is_relevant(real_settings_path: Path, tmp_path: Path) -> None:
    app = load_settings(real_settings_path, env_path=None, project_root=tmp_path).app
    (it,) = parse_announcements(TPEX, {"8299"}, "上櫃")
    assert "群聯" in KeywordMatcher.from_config(app).match(it.title).companies


def _client(routes: dict[str, httpx.Response]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes[str(request.url)]

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_source_combines_both_markets() -> None:
    client = _client({TWSE_URL: httpx.Response(200, json=TWSE), TPEX_URL: httpx.Response(200, json=TPEX)})
    items = MopsSource(["6415", "8299"], client=client).fetch()
    assert sorted(it.title[:2] for it in items) == ["矽力", "群聯"]


def test_one_market_failing_keeps_the_other() -> None:
    client = _client({TWSE_URL: httpx.Response(503), TPEX_URL: httpx.Response(200, json=TPEX)})
    assert len(MopsSource(["8299"], client=client).fetch()) == 1


def test_both_markets_failing_raises() -> None:
    client = _client({TWSE_URL: httpx.Response(503), TPEX_URL: httpx.Response(200, text="<html>")})
    with pytest.raises(SourceError, match="都抓取失敗"):
        MopsSource(["8299"], client=client).fetch()
