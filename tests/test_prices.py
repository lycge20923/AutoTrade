from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from autotrade.data.prices import parse_home, parse_last_update
from autotrade.sources.base import SourceError

HTML = (Path(__file__).parent / "fixtures" / "dramexchange_home.html").read_text(encoding="utf-8")


def test_parse_home() -> None:
    quotes = {q.item: q for q in parse_home(HTML)}
    assert set(quotes) == {
        "DDR5 16Gb (2Gx8) 4800/5600", "DDR4 16Gb (2Gx8) 3200",
        "DDR5 RDIMM 32GB 4800/5600", "512Gb TLC", "256Gb TLC",
    }
    ddr5 = quotes["DDR5 16Gb (2Gx8) 4800/5600"]
    assert (ddr5.frequency, ddr5.average, ddr5.change_pct, ddr5.high, ddr5.low) == ("daily", 60.333, 0.0, 72.0, 45.3)
    # 台北 11/17 18:10 = UTC 10:10
    assert ddr5.quoted_at == datetime(2026, 11, 17, 10, 10, tzinfo=timezone.utc)
    tlc = quotes["512Gb TLC"]
    assert (tlc.frequency, tlc.average, tlc.change_pct) == ("weekly", 14.267, -11.67)
    assert tlc.quoted_at == datetime(2026, 11, 9, 6, 40, tzinfo=timezone.utc)


def test_missing_daily_table_means_layout_changed() -> None:
    with pytest.raises(SourceError, match="結構"):
        parse_home("<html><body>維護中</body></html>")


@pytest.mark.parametrize("text,expected", [
    ("Last Update: Nov.17 2026    18:10 (GMT+8)", datetime(2026, 11, 17, 10, 10, tzinfo=timezone.utc)),
    ("Last Update:Nov.9 2026 14:40 (GMT+8)", datetime(2026, 11, 9, 6, 40, tzinfo=timezone.utc)),
    ("LastUpdate:Aug.5 2026, 11:00 (GMT+8)", datetime(2026, 8, 5, 3, 0, tzinfo=timezone.utc)),
])
def test_parse_last_update(text: str, expected: datetime) -> None:
    assert parse_last_update(text) == expected


def test_parse_last_update_garbage() -> None:
    assert parse_last_update("Updated recently") is None
