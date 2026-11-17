"""證交所（上市）與櫃買中心（上櫃）的三大法人、融資融券與月營收。

- 三大法人、融資融券用兩邊官網的 API，可指定日期，也能回補歷史資料。
  （證交所的 OpenAPI 沒有個股三大法人，融資融券也沒有日期且晚一天更新。）
- 月營收用 OpenAPI，只提供最新一個月份。

表格欄位是用位置取值，所以會先檢查欄位標題，格式改變時直接報錯，
避免默默存入錯位的數字。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import httpx

from autotrade.sources.base import SourceError

Market = Literal["上市", "上櫃"]

TWSE_FLOWS_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TWSE_MARGIN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
TPEX_FLOWS_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
TPEX_MARGIN_URL = "https://www.tpex.org.tw/www/zh-tw/margin/balance"
TWSE_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
TPEX_REVENUE_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"

# 信件裡的連結
FLOWS_PAGE = {
    "上市": "https://www.twse.com.tw/zh/trading/foreign/t86.html",
    "上櫃": "https://www.tpex.org.tw/zh-tw/mainboard/trading/major-institutional/detail/day.html",
}
MARGIN_PAGE = {
    "上市": "https://www.twse.com.tw/zh/trading/margin/mi-margn.html",
    "上櫃": "https://www.tpex.org.tw/zh-tw/mainboard/trading/margin-trading/transactions.html",
}
REVENUE_PAGE = "https://mops.twse.com.tw/mops/#/web/t05st10_ifrs"


@dataclass(frozen=True)
class Flow:
    code: str
    foreign_net: int  # 股，外資合計（含外資自營商）
    trust_net: int
    dealer_net: int
    total_net: int


@dataclass(frozen=True)
class Margin:
    code: str
    margin_prev: int  # 張
    margin_balance: int
    short_prev: int
    short_balance: int


@dataclass(frozen=True)
class Revenue:
    code: str
    year_month: str  # 2026-10
    revenue: int  # 千元
    prev_month: int | None
    last_year: int | None
    mom_pct: float | None
    yoy_pct: float | None
    note: str


def to_int(value: object) -> int:
    text = str(value).replace(",", "").strip()
    if text in ("", "-", "--", "---"):
        return 0
    try:
        return int(float(text))
    except ValueError as exc:
        raise SourceError(f"無法解析數字：{value!r}") from exc


def to_float(value: object) -> float | None:
    text = str(value).replace(",", "").strip()
    if text in ("", "-", "--", "---"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _check_fields(fields: list[str], expected_len: int, first: str, what: str) -> None:
    if len(fields) != expected_len or fields[0].strip() != first:
        raise SourceError(f"{what}的欄位格式改變了（{len(fields)} 欄：{fields[:3]}…）")


# ---------- 三大法人 ----------

def parse_twse_flows(payload: dict, codes: set[str]) -> list[Flow] | None:
    """回傳 None 代表當天沒有資料（休市或尚未公布）。"""
    if payload.get("stat") != "OK" or not payload.get("data"):
        return None
    fields = payload.get("fields") or []
    _check_fields(fields, 19, "證券代號", "上市三大法人")
    if "三大法人" not in fields[18]:
        raise SourceError("上市三大法人的欄位格式改變了（最後一欄不是三大法人合計）")
    out = []
    for r in payload["data"]:
        code = str(r[0]).strip()
        if code in codes:
            out.append(Flow(code, to_int(r[4]) + to_int(r[7]), to_int(r[10]), to_int(r[11]), to_int(r[18])))
    return out


def parse_tpex_flows(payload: dict, codes: set[str]) -> list[Flow] | None:
    tables = payload.get("tables") or []
    if not tables or not tables[0].get("data"):
        return None
    fields = tables[0].get("fields") or []
    _check_fields(fields, 24, "代號", "上櫃三大法人")
    out = []
    for r in tables[0]["data"]:
        code = str(r[0]).strip()
        if code in codes:
            out.append(Flow(code, to_int(r[10]), to_int(r[13]), to_int(r[22]), to_int(r[23])))
    return out


# ---------- 融資融券 ----------

def parse_twse_margin(payload: dict, codes: set[str]) -> list[Margin] | None:
    if payload.get("stat") != "OK":
        return None
    table = next(
        (t for t in payload.get("tables") or [] if (t.get("fields") or [""])[0].strip() == "代號"),
        None,
    )
    if table is None or not table.get("data"):
        return None
    _check_fields(table["fields"], 16, "代號", "上市融資融券")
    out = []
    for r in table["data"]:
        code = str(r[0]).strip()
        if code in codes:
            out.append(Margin(code, to_int(r[5]), to_int(r[6]), to_int(r[11]), to_int(r[12])))
    return out


def parse_tpex_margin(payload: dict, codes: set[str]) -> list[Margin] | None:
    tables = payload.get("tables") or []
    if not tables or not tables[0].get("data"):
        return None
    _check_fields(tables[0].get("fields") or [], 20, "代號", "上櫃融資融券")
    out = []
    for r in tables[0]["data"]:
        code = str(r[0]).strip()
        if code in codes:
            out.append(Margin(code, to_int(r[2]), to_int(r[6]), to_int(r[10]), to_int(r[14])))
    return out


# ---------- 月營收 ----------

def roc_year_month(value: str) -> str | None:
    """11510 → 2026-10"""
    v = value.strip()
    if len(v) != 5 or not v.isdigit() or not 1 <= int(v[3:]) <= 12:
        return None
    return f"{int(v[:3]) + 1911}-{v[3:]}"


def parse_revenue(records: object, codes: set[str], market: Market) -> list[Revenue]:
    if not isinstance(records, list):
        raise SourceError(f"{market}月營收格式不符：預期是 JSON 陣列")
    out = []
    for raw in records:
        if not isinstance(raw, dict):
            continue
        rec = {str(k).strip(): v for k, v in raw.items()}
        if "公司代號" not in rec or "營業收入-當月營收" not in rec:
            raise SourceError(f"{market}月營收的欄位名稱改變了：{sorted(rec)[:5]}…")
        code = str(rec["公司代號"]).strip()
        ym = roc_year_month(str(rec.get("資料年月", "")))
        if code not in codes or ym is None:
            continue
        out.append(
            Revenue(
                code=code,
                year_month=ym,
                revenue=to_int(rec["營業收入-當月營收"]),
                prev_month=to_int(rec.get("營業收入-上月營收", "")) or None,
                last_year=to_int(rec.get("營業收入-去年當月營收", "")) or None,
                mom_pct=to_float(rec.get("營業收入-上月比較增減(%)", "")),
                yoy_pct=to_float(rec.get("營業收入-去年同月增減(%)", "")),
                note=str(rec.get("備註", "") or "").strip(),
            )
        )
    return out


# ---------- 抓取 ----------

def _get_json(client: httpx.Client, url: str, params: dict[str, str] | None = None) -> object:
    resp = client.get(url, params=params)
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError as exc:
        raise SourceError(f"回應不是 JSON（可能被暫時封鎖）：{url}") from exc


def fetch_flows(client: httpx.Client, market: Market, day: date, codes: set[str]) -> list[Flow] | None:
    if market == "上市":
        payload = _get_json(client, TWSE_FLOWS_URL, {
            "date": day.strftime("%Y%m%d"), "selectType": "ALLBUT0999", "response": "json"})
        return parse_twse_flows(payload, codes)
    payload = _get_json(client, TPEX_FLOWS_URL, {
        "type": "Daily", "sect": "EW", "date": day.strftime("%Y/%m/%d"), "response": "json"})
    return parse_tpex_flows(payload, codes)


def fetch_margin(client: httpx.Client, market: Market, day: date, codes: set[str]) -> list[Margin] | None:
    if market == "上市":
        payload = _get_json(client, TWSE_MARGIN_URL, {
            "date": day.strftime("%Y%m%d"), "selectType": "ALL", "response": "json"})
        return parse_twse_margin(payload, codes)
    payload = _get_json(client, TPEX_MARGIN_URL, {"date": day.strftime("%Y/%m/%d"), "response": "json"})
    return parse_tpex_margin(payload, codes)


def fetch_revenue(client: httpx.Client, market: Market, codes: set[str]) -> list[Revenue]:
    url = TWSE_REVENUE_URL if market == "上市" else TPEX_REVENUE_URL
    return parse_revenue(_get_json(client, url), codes, market)
