"""公開資訊觀測站重大訊息。

資料來自證交所（上市）與櫃買中心（上櫃）的 OpenAPI，內容是最近一個交易日
所有公司的重大訊息，每天更新。只保留觀察標的的公告。

兩個 API 的欄位名稱不一樣，證交所的「主旨」後面還多了一個空白，
所以讀取前會先把欄位名稱前後的空白去掉，再用對照表取值。
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from autotrade.models import Item
from autotrade.sources.base import Source, SourceError

log = logging.getLogger(__name__)

TWSE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O"
MOPS_LINK = "https://mops.twse.com.tw/mops/#/web/t05st01"
TAIPEI = ZoneInfo("Asia/Taipei")

_FIELDS: dict[str, tuple[str, ...]] = {
    "code": ("公司代號", "SecuritiesCompanyCode"),
    "name": ("公司名稱", "CompanyName"),
    "subject": ("主旨",),
    "date": ("發言日期",),
    "time": ("發言時間",),
    "detail": ("說明",),
}
_SPACE_RE = re.compile(r"\s+")
MAX_DETAIL_CHARS = 2000


def _get(record: dict[str, object], field: str) -> str:
    for key in _FIELDS[field]:
        if key in record and record[key] is not None:
            return str(record[key]).strip()
    return ""


def parse_roc_datetime(date: str, time: str) -> datetime | None:
    """民國日期 1151116 + 時間 70003 → 2026-11-16 07:00:03（台北時間），回傳 UTC。"""
    date, time = date.strip(), time.strip()
    if len(date) != 7 or not date.isdigit():
        return None
    time = time.zfill(6) if time.isdigit() and len(time) <= 6 else "000000"
    try:
        local = datetime(
            int(date[:3]) + 1911, int(date[3:5]), int(date[5:7]),
            int(time[:2]), int(time[2:4]), int(time[4:6]),
            tzinfo=TAIPEI,
        )
    except ValueError:
        return None
    return local.astimezone(timezone.utc)


def parse_announcements(records: object, codes: set[str], market: str) -> list[Item]:
    if not isinstance(records, list):
        raise SourceError(f"{market}重大訊息格式不符：預期是 JSON 陣列")
    if records and isinstance(records[0], dict):
        first = {str(k).strip() for k in records[0]}
        if not first & set(_FIELDS["code"]) or not first & set(_FIELDS["subject"]):
            raise SourceError(f"{market}重大訊息的欄位名稱改變了：{sorted(first)}")

    items: list[Item] = []
    for raw in records:
        if not isinstance(raw, dict):
            continue
        rec = {str(k).strip(): v for k, v in raw.items()}
        code = _get(rec, "code")
        if code not in codes:
            continue
        subject = _SPACE_RE.sub(" ", _get(rec, "subject")).strip()
        if not subject:
            continue
        name, date, time = _get(rec, "name"), _get(rec, "date"), _get(rec, "time")

        # API 沒有每則公告的網址，用內容雜湊做出唯一連結，同時用來去除重複
        digest = hashlib.sha1(f"{code}|{date}|{time}|{subject}".encode()).hexdigest()[:10]
        url = f"{MOPS_LINK}?companyId={code}&ref={date}{time.zfill(6)}-{digest}"
        detail = _get(rec, "detail").replace("\r\n", "\n").strip()

        items.append(
            Item(
                source="mops",
                source_type="official",
                title=f"{name}（{code}）{subject}",
                url=url,
                published_at=parse_roc_datetime(date, time),
                content=detail[:MAX_DETAIL_CHARS],
                publisher=f"公開資訊觀測站・{market}",
            )
        )
    return items


class MopsSource(Source):
    name = "mops"
    source_type = "official"

    def __init__(
        self,
        codes: list[str],
        client: httpx.Client | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.codes = set(codes)
        self._client = client
        self._timeout = timeout_seconds

    def fetch(self) -> list[Item]:
        client = self._client or httpx.Client(timeout=self._timeout, follow_redirects=True)
        try:
            items: list[Item] = []
            errors: list[str] = []
            for market, url in (("上市", TWSE_URL), ("上櫃", TPEX_URL)):
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    parsed = parse_announcements(resp.json(), self.codes, market)
                except (httpx.HTTPError, ValueError, SourceError) as exc:
                    log.warning("%s重大訊息抓取失敗：%s", market, exc)
                    errors.append(f"{market}：{exc}")
                    continue
                log.info("%s重大訊息：觀察標的 %d 則", market, len(parsed))
                items.extend(parsed)
            if len(errors) == 2:
                raise SourceError("上市與上櫃重大訊息都抓取失敗：" + "；".join(errors))
            return items
        finally:
            if self._client is None:
                client.close()
