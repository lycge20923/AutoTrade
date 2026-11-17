from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from autotrade.cli import main
from autotrade.data import rules, store
from autotrade.data.collector import backfill, candidate_days, update_data
from autotrade.data.markets import Flow, Margin
from autotrade.data.prices import Quote
from autotrade.notify.email import EmailError
from autotrade.pipeline import run
from autotrade.storage import connect, init_db

FX = Path(__file__).parent / "fixtures"
# 台北 2026-11-18（週三）01:00
NOW = datetime(2026, 11, 17, 17, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 18)
# 台北 2026-11-10 01:00：DRAMeXchange 週報價（11/9 更新）剛出來不久
NOW_EARLY = datetime(2026, 11, 9, 17, 0, tzinfo=timezone.utc)
ENV = "GMAIL_ADDRESS=me@gmail.com\nGMAIL_APP_PASSWORD=x\n"


def load(name: str):
    return json.loads((FX / f"{name}.json").read_text(encoding="utf-8"))


class Router:
    """模擬各網站：只有 2026-11-17 有交易資料，其他日期都是休市。"""

    def __init__(self, fail_hosts: set[str] = frozenset()):
        self.calls: list[str] = []
        self.fail_hosts = fail_hosts

    def __call__(self, req: httpx.Request) -> httpx.Response:
        host, path, params = req.url.host, req.url.path, dict(req.url.params)
        self.calls.append(f"{host}{path}?{params.get('date', '')}")
        if host in self.fail_hosts:
            return httpx.Response(503)
        if "dramexchange" in host:
            return httpx.Response(200, text=(FX / "dramexchange_home.html").read_text(encoding="utf-8"))
        if host == "openapi.twse.com.tw":
            return httpx.Response(200, json=load("twse_revenue"))
        if path.endswith("mopsfin_t187ap05_O"):
            return httpx.Response(200, json=load("tpex_revenue"))
        d = params.get("date", "")
        if path.endswith("/T86"):
            return httpx.Response(200, json=load("twse_t86") if d == "20261117" else load("twse_holiday"))
        if path.endswith("/MI_MARGN"):
            return httpx.Response(200, json=load("twse_margin") if d == "20261117" else load("twse_holiday"))
        if path.endswith("/dailyTrade"):
            return httpx.Response(200, json=load("tpex_flows") if d == "2026/11/17" else load("tpex_holiday"))
        if path.endswith("/margin/balance"):
            return httpx.Response(200, json=load("tpex_margin") if d == "2026/11/17" else load("tpex_holiday"))
        return httpx.Response(404)


@pytest.fixture
def env(make_settings):
    settings = make_settings(ENV, data_enabled=True)
    conn = connect(settings.db_path)
    init_db(conn)
    yield settings, conn
    conn.close()


def _client(router: Router) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(router))


def test_candidate_days_skips_weekends() -> None:
    assert candidate_days(TODAY, 7) == [
        date(2026, 11, 12), date(2026, 11, 13), date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 18)
    ]


def test_first_update(env) -> None:
    settings, conn = env
    sleeps: list[float] = []
    results, alerts, warnings = update_data(conn, settings, NOW, client=_client(Router()), sleep=sleeps.append)

    assert [r.name for r in results] == ["prices", "flows", "margin", "revenue"]
    assert all(r.error is None for r in results) and warnings == []
    assert [f.foreign_net for f in store.recent_flows(conn, "2408")] == [1_103_915]
    assert store.recent_flows(conn, "3260")[0].market == "上櫃"
    assert store.recent_margins(conn, "2337")[0].margin_balance == 72_989

    # 過去沒資料的日子記為休市；今天（11/18）可能還沒公布，不記錄，下次再試
    days = {r["date"]: r["status"] for r in conn.execute(
        "SELECT date, status FROM data_days WHERE dataset='flows' AND market='上市'")}
    assert days == {"2026-11-12": "empty", "2026-11-13": "empty", "2026-11-16": "empty", "2026-11-17": "ok"}

    # 請求之間有間隔（避免被證交所封鎖）
    assert sleeps and set(sleeps) == {2.5}

    titles = [a.title for a in alerts]
    # 週報價最後更新是 11/9，已經超過 3 天的提醒期間，視為舊消息不提醒
    assert not any("現貨價" in t for t in titles)
    assert any(t.startswith("威剛（3260）10 月營收 170.6 億元，月增 +8.5%、年增 +282.3%") for t in titles)
    assert any(t.startswith("旺宏（2337）10 月營收") for t in titles)
    assert not any("DDR4" in t for t in titles)  # 每日報價沒有一週前的紀錄，不提醒


def test_fresh_weekly_quote_alerts(env) -> None:
    settings, conn = env
    _, alerts, _ = update_data(conn, settings, NOW_EARLY, client=_client(Router()), sleep=lambda s: None)
    titles = [a.title for a in alerts]
    assert "512Gb TLC 現貨價週跌 -11.7%（均價 14.267 美元）" in titles  # 沒有歷史時用網站提供的週漲跌
    assert not any("256Gb" in t for t in titles)  # -1.21% 不到門檻


def test_second_update_only_retries_today(env) -> None:
    settings, conn = env
    update_data(conn, settings, NOW, client=_client(Router()), sleep=lambda s: None)
    router = Router()
    results, alerts, _ = update_data(conn, settings, NOW, client=_client(router), sleep=lambda s: None)

    daily_calls = [c for c in router.calls if "dramexchange" not in c and "openapi" not in c
                   and "mopsfin" not in c]
    assert len(daily_calls) == 4  # 兩個資料集 × 兩個市場，只重試今天
    assert all("2026" in c and ("20261118" in c or "2026/11/18" in c) for c in daily_calls)
    assert {r.name: r.new for r in results}["revenue"] == 0
    assert not any("月營收" in a.title for a in alerts)  # 同一個月份不會重複提醒


def test_one_dataset_failing_does_not_stop_others(env) -> None:
    settings, conn = env
    results, alerts, _ = update_data(
        conn, settings, NOW, client=_client(Router(fail_hosts={"www.dramexchange.com"})), sleep=lambda s: None
    )
    by_name = {r.name: r for r in results}
    assert by_name["prices"].error and by_name["flows"].error is None
    assert by_name["flows"].new == 3


def _flows(conn, code: str, nets: list[int], end: date) -> None:
    day = end
    for net in nets:  # nets[0] 是最新的一天
        store.save_flows(conn, day, "上市", [Flow(code, net, 0, 0, net)])
        day -= timedelta(days=1)


def test_foreign_streak_alert(env) -> None:
    settings, conn = env
    r = settings.app.data.rules
    names = {"2408": "南亞科"}
    _flows(conn, "2408", [-1_000_000] * 5 + [2_000_000], TODAY)
    (alert,) = rules.flow_alerts(conn, names, r, TODAY - timedelta(days=2))
    assert alert.title == "南亞科（2408）外資連續 5 日賣超，累計 -5,000 張"
    assert alert.matched_keywords == ("南亞科", "外資連賣")
    assert "flow:2408:sell:2026-11-14" in alert.url

    # 第 6 天還在賣：同一段連賣用同一個 key，只會通知一次
    _flows(conn, "2408", [-500_000], TODAY + timedelta(days=1))
    (again,) = rules.flow_alerts(conn, names, r, TODAY - timedelta(days=2))
    assert again.url == alert.url and "6 日" in again.title


def test_short_or_broken_streak_no_alert(env) -> None:
    settings, conn = env
    _flows(conn, "2408", [-1, -1, -1, -1, 5, -1], TODAY)
    assert rules.flow_alerts(conn, {"2408": "南亞科"}, settings.app.data.rules, TODAY) == []


def test_old_streak_outside_window_no_alert(env) -> None:
    settings, conn = env
    _flows(conn, "2408", [-1] * 6, TODAY - timedelta(days=10))
    assert rules.flow_alerts(conn, {"2408": "南亞科"}, settings.app.data.rules, TODAY - timedelta(days=2)) == []


def test_margin_surge(env) -> None:
    settings, conn = env
    r = settings.app.data.rules
    store.save_margins(conn, TODAY, "上市", [
        Margin("2408", 10_000, 11_500, 0, 0),  # +15% → 提醒
        Margin("2344", 500, 900, 0, 0),  # 基數太小，不檢查
        Margin("2337", 70_000, 72_000, 0, 0),  # +2.9%，不到門檻
    ])
    store.save_margins(conn, TODAY - timedelta(days=10), "上市", [Margin("2337", 1000, 5000, 0, 0)])  # 太舊
    names = {"2408": "南亞科", "2344": "華邦電", "2337": "旺宏"}
    (alert,) = rules.margin_alerts(conn, names, r, TODAY - timedelta(days=2))
    assert alert.title == "南亞科（2408）融資餘額單日增加 15.0%（10,000 → 11,500 張）"


def test_price_alert_against_week_old_quote(env) -> None:
    settings, conn = env
    latest = datetime(2026, 11, 17, 10, 10, tzinfo=timezone.utc)
    store.save_quotes(conn, [
        Quote("DDR4 16Gb (2Gx8) 3200", "daily", 92.0, 0.0, None, None, latest - timedelta(days=8)),
        Quote("DDR4 16Gb (2Gx8) 3200", "daily", 85.0, 0.0, None, None, latest - timedelta(days=3)),
        Quote("DDR4 16Gb (2Gx8) 3200", "daily", 79.125, -0.32, None, None, latest),
    ], NOW)
    (alert,) = rules.price_alerts(conn, ["DDR4 16Gb (2Gx8) 3200"], settings.app.data.rules, TODAY - timedelta(days=2))
    assert alert.title == "DDR4 16Gb (2Gx8) 3200 現貨價週跌 -14.0%（92.000 → 79.125 美元）"
    assert alert.matched_keywords == ("現貨價", "跌價")


def test_small_price_move_no_alert(env) -> None:
    settings, conn = env
    latest = datetime(2026, 11, 17, 10, 10, tzinfo=timezone.utc)
    store.save_quotes(conn, [
        Quote("DDR5 16Gb (2Gx8) 4800/5600", "daily", 60.0, 0.0, None, None, latest - timedelta(days=7)),
        Quote("DDR5 16Gb (2Gx8) 4800/5600", "daily", 61.0, 0.0, None, None, latest),
    ], NOW)
    assert rules.price_alerts(conn, ["DDR5 16Gb (2Gx8) 4800/5600"], settings.app.data.rules, TODAY) == []


@dataclass
class FakeSender:
    sent: list = field(default_factory=list)

    def send(self, to, subject, text, html=None):
        self.sent.append((subject, text))


def test_pipeline_puts_data_alerts_in_email(env) -> None:
    settings, _ = env
    sender = FakeSender()
    result = run(settings, sources=[], sender=sender, now=NOW_EARLY,
                 data_client=_client(Router()), sleep=lambda s: None)
    assert result.emailed
    subject, text = sender.sent[0]
    assert "風險訊號 1 則" in subject
    risk = text[text.index("⚠️ 風險訊號"):text.index("📊 數據異動")]
    assert "512Gb TLC 現貨價週跌" in risk  # 跌價屬於風險關鍵字
    data = text[text.index("📊 數據異動"):]
    assert "威剛（3260）10 月營收" in data and "記憶體售價上漲" in data  # 附上公司說明

    # 再跑一次：同樣的提醒不會重複寄
    again = run(settings, sources=[], sender=sender, now=NOW_EARLY + timedelta(hours=1),
                data_client=_client(Router()), sleep=lambda s: None)
    assert again.digest is None and len(sender.sent) == 1


def test_backfill(env) -> None:
    settings, conn = env
    messages: list[str] = []
    result = backfill(conn, settings, 10, NOW, client=_client(Router()), sleep=lambda s: None,
                      progress=messages.append)
    assert result == {"flows": (3, 3), "margin": (2, 2)}
    assert "flows 上市 2026-11-17：2 筆" in messages


def test_cli_data_views(env, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, conn = env
    update_data(conn, settings, NOW, client=_client(Router()), sleep=lambda s: None)
    cfg, env_file = str(tmp_path / "settings.yaml"), str(tmp_path / ".env")

    assert main(["--config", cfg, "--env", env_file, "data", "南亞科"]) == 0
    out = capsys.readouterr().out
    assert "南亞科（2408）" in out and "2026-11-17" in out and "+1,104" in out

    assert main(["--config", cfg, "--env", env_file, "data", "3260"]) == 0
    out = capsys.readouterr().out
    assert "16,411" in out and "-467" in out  # 融資餘額與增減
    assert "170.6 億元" in out and "記憶體售價上漲" in out

    assert main(["--config", cfg, "--env", env_file, "data", "prices"]) == 0
    out = capsys.readouterr().out
    assert "★ 512Gb TLC" in out and "14.267" in out

    assert main(["--config", cfg, "--env", env_file, "data", "9999"]) == 1
