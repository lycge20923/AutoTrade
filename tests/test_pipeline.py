from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from autotrade.models import Item
from autotrade.notify.email import EmailError
from autotrade.pipeline import run
from autotrade.sources.base import Source
from autotrade.storage import connect

NOW = datetime(2026, 11, 18, 0, 0, tzinfo=timezone.utc)
ENV = "GMAIL_ADDRESS=me@gmail.com\nGMAIL_APP_PASSWORD=x\n"


def news(n: int, title: str, minutes_ago: int = 0) -> Item:
    return Item(
        source="fake",
        source_type="news",
        title=title,
        url=f"https://example.com/{n}",
        published_at=NOW - timedelta(minutes=minutes_ago),
        publisher="測試媒體",
    )


class FakeSource(Source):
    name = "fake"
    source_type = "news"

    def __init__(self, items: list[Item]):
        self.items = items

    def fetch(self) -> list[Item]:
        return list(self.items)


class BrokenSource(Source):
    name = "broken"
    source_type = "news"

    def fetch(self) -> list[Item]:
        raise RuntimeError("網站改版了")


@dataclass
class FakeSender:
    fail: bool = False
    sent: list[tuple[str, str, str, str | None]] = field(default_factory=list)

    def send(self, to, subject, text, html=None):
        if self.fail:
            raise EmailError("寄信失敗：模擬錯誤")
        self.sent.append((to, subject, text, html))


ITEMS = [
    news(1, "南亞科營收創新高", 10),
    news(2, "DRAM 現貨價跌價", 20),
    news(3, "今日天氣晴朗", 30),  # 不相關
    news(4, "三星新手機開賣", 40),  # 只有國際公司、沒有產業關鍵字 → 不相關
]


def test_first_run_sends_relevant_items_risk_first(make_settings) -> None:
    settings = make_settings(ENV)
    sender = FakeSender()
    result = run(settings, sources=[FakeSource(ITEMS)], sender=sender, now=NOW)

    assert result.emailed and not result.email_error
    assert result.sources[0].fetched == 4
    assert result.sources[0].relevant == result.sources[0].new == 2
    to, subject, text, html = sender.sent[0]
    assert to == "me@gmail.com"
    assert "記憶體產業 2 則" in subject and "風險訊號 1 則" in subject
    assert text.index("DRAM 現貨價跌價") < text.index("南亞科營收創新高")  # 風險優先
    assert "今日天氣晴朗" not in text and "三星新手機" not in text
    assert "https://example.com/1" in html


def test_second_run_with_same_news_sends_nothing(make_settings) -> None:
    settings = make_settings(ENV)
    sender = FakeSender()
    run(settings, sources=[FakeSource(ITEMS)], sender=sender, now=NOW)
    result = run(settings, sources=[FakeSource(ITEMS)], sender=sender, now=NOW + timedelta(hours=1))

    assert result.sources[0].new == 0
    assert result.digest is None
    assert len(sender.sent) == 1


def test_only_new_items_are_sent_next_time(make_settings) -> None:
    settings = make_settings(ENV)
    sender = FakeSender()
    run(settings, sources=[FakeSource(ITEMS)], sender=sender, now=NOW)
    later = ITEMS + [news(5, "華邦電法說會", 0)]
    run(settings, sources=[FakeSource(later)], sender=sender, now=NOW + timedelta(hours=1))

    assert len(sender.sent) == 2
    assert "記憶體產業 1 則" in sender.sent[1][1]
    assert "華邦電法說會" in sender.sent[1][2]


def test_dry_run_does_not_mark_notified(make_settings) -> None:
    settings = make_settings(ENV)
    sender = FakeSender()
    dry = run(settings, dry_run=True, sources=[FakeSource(ITEMS)], sender=sender, now=NOW)
    assert dry.digest is not None and not dry.emailed and sender.sent == []

    real = run(settings, sources=[FakeSource(ITEMS)], sender=sender, now=NOW)
    assert real.emailed and "記憶體產業 2 則" in sender.sent[0][1]
    assert real.sources[0].new == 0  # 試跑時已經存過
    assert real.pending_by_source == {"fake": 2}  # 但仍會寄出試跑存下的資料


def test_failed_email_is_retried_next_run(make_settings) -> None:
    settings = make_settings(ENV)
    failed = run(settings, sources=[FakeSource(ITEMS)], sender=FakeSender(fail=True), now=NOW)
    assert failed.email_error and not failed.emailed

    sender = FakeSender()
    retry = run(settings, sources=[FakeSource(ITEMS)], sender=sender, now=NOW + timedelta(hours=1))
    assert retry.emailed and "記憶體產業 2 則" in sender.sent[0][1]


def test_missing_credentials_reports_error_but_keeps_data(make_settings) -> None:
    settings = make_settings("")
    result = run(settings, sources=[FakeSource(ITEMS)], now=NOW)
    assert "GMAIL_ADDRESS" in result.email_error
    conn = connect(settings.db_path)
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
    conn.close()


def test_broken_source_does_not_stop_others(make_settings) -> None:
    settings = make_settings(ENV)
    sender = FakeSender()
    result = run(settings, sources=[BrokenSource(), FakeSource(ITEMS)], sender=sender, now=NOW)

    assert result.sources[0].error == "網站改版了"
    assert result.sources[1].new == 2
    assert result.emailed and not result.all_sources_failed

    conn = connect(settings.db_path)
    rows = conn.execute("SELECT source, status, error FROM source_runs ORDER BY id").fetchall()
    conn.close()
    assert [tuple(r) for r in rows] == [("broken", "error", "網站改版了"), ("fake", "ok", None)]


def test_all_sources_failing_is_flagged(make_settings) -> None:
    result = run(make_settings(ENV), sources=[BrokenSource()], sender=FakeSender(), now=NOW)
    assert result.all_sources_failed


def test_items_older_than_window_are_not_sent(make_settings) -> None:
    settings = make_settings(ENV)
    run(settings, dry_run=True, sources=[FakeSource(ITEMS)], now=NOW)  # 存檔但沒寄
    sender = FakeSender()
    result = run(settings, sources=[FakeSource([])], sender=sender, now=NOW + timedelta(days=4))
    assert result.digest is None and sender.sent == []


def test_digest_respects_max_items(make_settings) -> None:
    settings = make_settings(ENV, extra_yaml="")
    settings.app.notify.email.max_items = 3
    many = [news(i, f"DRAM 新聞 {i}", i) for i in range(10)]
    sender = FakeSender()
    result = run(settings, sources=[FakeSource(many)], sender=sender, now=NOW)

    assert "記憶體產業 10 則" in sender.sent[0][1]
    assert "另有 7 則未列出" in sender.sent[0][2]
    assert len(result.digest.item_ids) == 10  # 全部標記為已通知，避免下次重複


def test_health_warning_sent_once_when_threshold_reached(make_settings) -> None:
    settings = make_settings(ENV)
    sender = FakeSender()
    results = [
        run(settings, sources=[BrokenSource()], sender=sender, now=NOW + timedelta(hours=h))
        for h in range(4)
    ]
    assert [r.health_warnings != [] for r in results] == [False, False, True, False]
    assert [s.consecutive_failures for r in results for s in r.sources] == [1, 2, 3, 4]
    assert len(sender.sent) == 1  # 沒有新資料，但仍寄出來源異常通知
    subject, text = sender.sent[0][1], sender.sent[0][2]
    assert "資料來源異常" in subject
    assert "broken 已連續失敗 3 次" in text and "網站改版了" in text


def test_success_resets_failure_count(make_settings) -> None:
    settings = make_settings(ENV)
    for h in range(2):
        run(settings, sources=[BrokenSource()], sender=FakeSender(), now=NOW + timedelta(hours=h))

    class Recovered(FakeSource):
        name = "broken"

    run(settings, sources=[Recovered([])], sender=FakeSender(), now=NOW + timedelta(hours=2))
    r = run(settings, sources=[BrokenSource()], sender=FakeSender(), now=NOW + timedelta(hours=3))
    assert r.sources[0].consecutive_failures == 1 and r.health_warnings == []


def test_digest_sections_order(make_settings) -> None:
    from dataclasses import replace

    settings = make_settings(ENV)
    official = replace(news(10, "群聯（8299）公告買回庫藏股", 5), source_type="official",
                       content="1.本次買回股份數量:130,000\n2.總金額:215,041,885")
    social = replace(news(11, "[標的] 2408 南亞科 多", 1), source_type="social")
    normal = news(12, "DRAM 報價續揚", 3)
    sender = FakeSender()
    run(settings, sources=[FakeSource([social, normal, official])], sender=sender, now=NOW)

    text = sender.sent[0][2]
    assert text.index("📢 公司公告") < text.index("📰 新聞") < text.index("💬 PTT 討論")
    assert "1.本次買回股份數量:130,000 2.總金額" in text  # 公告附上說明摘要


def test_default_sources_order(make_settings) -> None:
    from autotrade.pipeline import build_sources

    assert [s.name for s in build_sources(make_settings(ENV))] == ["mops", "trendforce", "google_news", "ptt"]
