from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from autotrade.data import markets
from autotrade.data.markets import (
    Flow,
    Margin,
    parse_revenue,
    parse_tpex_flows,
    parse_tpex_margin,
    parse_twse_flows,
    parse_twse_margin,
    roc_year_month,
    to_int,
)
from autotrade.sources.base import SourceError

FX = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FX / f"{name}.json").read_text(encoding="utf-8"))


def test_twse_flows() -> None:
    flows = parse_twse_flows(load("twse_t86"), {"2337", "2408"})
    assert flows == [
        Flow("2337", 1_234_168, 70_000, 747_456, 2_051_624),
        Flow("2408", 1_103_915, -47_374, 186_461, 1_243_002),
    ]
    for f in flows:  # 外資＋投信＋自營商＝三大法人合計
        assert f.foreign_net + f.trust_net + f.dealer_net == f.total_net


def test_tpex_flows() -> None:
    (f,) = parse_tpex_flows(load("tpex_flows"), {"3260"})
    assert f == Flow("3260", 148_291, 534_048, -6_815, 675_524)
    assert f.foreign_net + f.trust_net + f.dealer_net == f.total_net


def test_holidays_return_none() -> None:
    assert parse_twse_flows(load("twse_holiday"), {"2408"}) is None
    assert parse_tpex_flows(load("tpex_holiday"), {"3260"}) is None
    assert parse_twse_margin(load("twse_holiday"), {"2408"}) is None


def test_watchlist_without_rows_returns_empty_list() -> None:
    assert parse_twse_flows(load("twse_t86"), {"8299"}) == []  # 有資料，但不是上市公司


def test_changed_columns_are_detected() -> None:
    payload = load("twse_t86")
    payload["fields"] = payload["fields"][:-1]
    with pytest.raises(SourceError, match="欄位格式改變"):
        parse_twse_flows(payload, {"2408"})


def test_twse_margin() -> None:
    assert parse_twse_margin(load("twse_margin"), {"2337"}) == [Margin("2337", 73_404, 72_989, 3_207, 2_237)]


def test_tpex_margin() -> None:
    assert parse_tpex_margin(load("tpex_margin"), {"3260"}) == [Margin("3260", 16_878, 16_411, 565, 482)]


def test_revenue() -> None:
    (r,) = parse_revenue(load("tpex_revenue"), {"3260"}, "上櫃")
    assert (r.year_month, r.revenue, r.prev_month, r.last_year) == ("2026-10", 17_063_103, 15_729_789, 4_462_788)
    assert r.mom_pct == pytest.approx(8.4764, abs=1e-3)
    assert r.yoy_pct == pytest.approx(282.3418, abs=1e-3)
    assert r.note == "記憶體售價上漲"


def test_revenue_field_change_detected() -> None:
    with pytest.raises(SourceError, match="欄位名稱改變"):
        parse_revenue([{"code": "3260"}], {"3260"}, "上櫃")


@pytest.mark.parametrize("value,expected", [("-47,374", -47374), ("", 0), ("--", 0), (" 1,234 ", 1234)])
def test_to_int(value: str, expected: int) -> None:
    assert to_int(value) == expected


def test_roc_year_month() -> None:
    assert roc_year_month("11510") == "2026-10"
    assert roc_year_month("11513") is None and roc_year_month("1151") is None


def test_request_parameters() -> None:
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.host, dict(req.url.params)))
        return httpx.Response(200, json=load("twse_holiday") if "twse" in req.url.host else load("tpex_holiday"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    markets.fetch_flows(client, "上市", date(2026, 11, 17), {"2408"})
    markets.fetch_flows(client, "上櫃", date(2026, 11, 17), {"3260"})
    assert seen[0] == ("www.twse.com.tw", {"date": "20261117", "selectType": "ALLBUT0999", "response": "json"})
    assert seen[1][1]["date"] == "2026/11/17"


def test_non_json_response_is_an_error() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>請稍後再試")))
    with pytest.raises(SourceError, match="不是 JSON"):
        markets.fetch_flows(client, "上市", date(2026, 11, 17), {"2408"})
