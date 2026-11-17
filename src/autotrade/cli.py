"""命令列入口：python -m autotrade <指令>"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from autotrade import __version__
from autotrade.config import DEFAULT_ENV_PATH, ConfigError, Settings, load_settings
from autotrade.logging_setup import setup_logging
from autotrade.notify.email import EmailError, sender_from_settings
from autotrade.storage import connect, init_db, list_tables


def _width(text: str) -> int:
    """終端機顯示寬度：中文等全形字佔 2 格。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _rjust(text: str, width: int) -> str:
    return " " * max(0, width - _width(text)) + text


def _ljust(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _cmd_check_config(settings: Settings, args: argparse.Namespace) -> int:
    app = settings.app
    print(f"設定檔讀取成功（時區：{app.timezone}）\n")

    print(f"台股觀察標的：{len(app.watchlist.taiwan)} 檔")
    by_segment = Counter(s.segment for s in app.watchlist.taiwan)
    for segment, count in by_segment.items():
        names = "、".join(f"{s.name}({s.code})" for s in app.watchlist.taiwan if s.segment == segment)
        print(f"  {segment}（{count}）：{names}")
    peers = "、".join(p.name for p in app.watchlist.international)
    print(f"國際風向球：{peers or '（無）'}\n")

    kw = app.keywords
    print(f"關鍵字：共 {len(kw.all())} 個")
    print(f"  產品 {len(kw.products)}、價格 {len(kw.price)}、供需 {len(kw.supply_demand)}、風險 {len(kw.risk)}\n")

    print(f"資料庫：{settings.db_path}")
    print(f"Log 檔：{settings.log_path or '（不寫入檔案）'}\n")

    print(".env 設定狀態（只顯示是否已填，不顯示內容）：")
    for name, is_set in settings.secrets.status().items():
        print(f"  {'✅' if is_set else '⬜'} {name.upper()}")
    return 0


def _cmd_init_db(settings: Settings, args: argparse.Namespace) -> int:
    db_path: Path = args.db or settings.db_path
    conn = connect(db_path)
    try:
        version = init_db(conn)
        tables = list_tables(conn)
    finally:
        conn.close()
    print(f"資料庫已就緒：{db_path}")
    print(f"資料表版本：v{version}")
    print(f"資料表：{', '.join(tables)}")
    return 0


def _cmd_run(settings: Settings, args: argparse.Namespace) -> int:
    from autotrade.pipeline import run  # 延後載入，讓 --help 更快

    result = run(settings, dry_run=args.dry_run)

    print("\n來源執行結果：")
    for s in result.sources:
        if s.error:
            print(f"  ❌ {s.name}：{s.error}（連續失敗 {s.consecutive_failures} 次）")
        elif s.kind == "data":
            print(f"  ✅ {s.name}：抓到 {s.fetched} 筆，新增 {s.new} 筆，提醒 {s.relevant} 則")
        else:
            print(f"  ✅ {s.name}：抓到 {s.fetched} 則，相關 {s.relevant} 則，新增 {s.new} 則")
    if not result.sources:
        print("  （沒有啟用任何來源，請檢查 config/settings.yaml 的 sources）")

    for w in result.health_warnings:
        print(f"\n🔧 {w}")

    if result.pending_by_source:
        breakdown = "、".join(f"{k} {v}" for k, v in result.pending_by_source.items())
        print(f"\n本次通知內容：共 {result.pending} 則（{breakdown}）")
        print("  註：包含先前試跑存下、還沒寄出的資料，所以可能比上面的「新增」多。")

    if result.digest is None:
        print("\n沒有新的資料需要通知。")
    elif args.dry_run:
        print(f"\n[試跑] 不會寄信。信件預覽：\n主旨：{result.digest.subject}\n")
        print(result.digest.text)
    elif result.emailed:
        print(f"\n📧 已寄出：{result.digest.subject}")
    else:
        print(f"\n❌ {result.email_error}")
        print("   資料已存檔，下次執行會再嘗試寄出。")

    if result.all_sources_failed or result.email_error:
        return 1
    return 0


def _cmd_check_sources(settings: Settings, args: argparse.Namespace) -> int:
    """逐一實際抓取每個來源，顯示筆數與前幾則標題，不存檔也不寄信。"""
    from autotrade.matching import KeywordMatcher
    from autotrade.pipeline import build_sources

    matcher = KeywordMatcher.from_config(settings.app)
    sources = [s for s in build_sources(settings) if not args.only or s.name in args.only]
    if not sources:
        print("沒有符合的來源。可用名稱：mops、trendforce、google_news、ptt")
        return 1

    failed = 0
    for src in sources:
        print(f"\n▶ {src.name}")
        try:
            items = src.fetch()
        except Exception as exc:
            failed += 1
            print(f"  ❌ 失敗：{exc}")
            continue
        relevant = [it for it in items if matcher.match(f"{it.title}\n{it.content}").relevant]
        print(f"  ✅ 抓到 {len(items)} 則，其中相關 {len(relevant)} 則")
        for it in relevant[: args.limit]:
            when = it.published_at.astimezone(settings.tz).strftime("%m/%d %H:%M") if it.published_at else "--"
            print(f"     {when}  {it.title[:60]}  ({it.publisher})")
    return 1 if failed == len(sources) else 0


def _cmd_data(settings: Settings, args: argparse.Namespace) -> int:
    """查詢已累積的數據：個股的法人與融資動向，或現貨價。"""
    from autotrade.data import store

    conn = connect(settings.db_path)
    try:
        init_db(conn)
        if args.target == "prices":
            return _show_prices(conn, settings)
        return _show_stock(conn, settings, args.target, args.days)
    finally:
        conn.close()


def _show_prices(conn, settings: Settings) -> int:
    from datetime import timedelta

    from autotrade.data import store

    items = store.quote_items(conn)
    if not items:
        print("還沒有現貨價資料，請先執行 autotrade run。")
        return 0
    tracked = settings.app.data.prices.track
    print(_ljust("  品項", 32) + "".join(_rjust(h, w) for h, w in
          (("均價(美元)", 12), ("報價時間", 14), ("約 7 天前", 12), ("變化", 9))))
    for item in sorted(items, key=lambda i: (i not in tracked, i)):
        q = store.latest_quote(conn, item)
        base = store.quote_at_or_before(conn, item, q.quoted_at - timedelta(days=6, hours=12))
        old = f"{base.average:.3f}" if base else "--"
        pct = f"{(q.average / base.average - 1) * 100:+.1f}%" if base and base.average else "--"
        star = "★" if item in tracked else " "
        when = q.quoted_at.astimezone(settings.tz).strftime("%m/%d %H:%M")
        print(_ljust(f"{star} {item}", 32) + _rjust(f"{q.average:.3f}", 12) + _rjust(when, 14)
              + _rjust(old, 12) + _rjust(pct, 9))
    print("\n★ 為設定檔中追蹤提醒的品項。「約 7 天前」需要累積一週以上的資料才會出現。")
    return 0


def _show_stock(conn, settings: Settings, code: str, days: int) -> int:
    from autotrade.data import store

    names = {s.code: s.name for s in settings.app.watchlist.taiwan}
    code = next((c for c, n in names.items() if n == code), code)  # 也接受公司名稱
    flows = {r.date: r for r in store.recent_flows(conn, code, days)}
    margins = {r.date: r for r in store.recent_margins(conn, code, days)}
    if not flows and not margins:
        print(f"沒有 {code} 的資料。請確認代號，或先執行 autotrade run / autotrade backfill。")
        return 1

    print(f"{names.get(code, code)}（{code}）最近 {days} 個交易日（單位：張）\n")
    print(_ljust("日期", 12) + "".join(_rjust(h, 10) for h in ("外資", "投信", "自營商", "三大法人", "融資餘額", "融資增減")))
    for d in sorted(set(flows) | set(margins), reverse=True)[:days]:
        f, m = flows.get(d), margins.get(d)
        cols = [f"{x / 1000:+,.0f}" if f else "--" for x in
                ((f.foreign_net, f.trust_net, f.dealer_net, f.total_net) if f else (0, 0, 0, 0))]
        mb = f"{m.margin_balance:,}" if m else "--"
        md = f"{m.margin_balance - m.margin_prev:+,}" if m else "--"
        print(_ljust(d, 12) + "".join(_rjust(c, 10) for c in (*cols, mb, md)))

    rev = store.latest_revenue(conn, code)
    if rev:
        mom = f"{rev['mom_pct']:+.1f}%" if rev["mom_pct"] is not None else "--"
        yoy = f"{rev['yoy_pct']:+.1f}%" if rev["yoy_pct"] is not None else "--"
        print(f"\n最新月營收（{rev['year_month']}）：{rev['revenue'] / 100_000:,.1f} 億元，月增 {mom}、年增 {yoy}")
        if rev["note"]:
            print(f"  公司說明：{rev['note']}")
    return 0


def _cmd_backfill(settings: Settings, args: argparse.Namespace) -> int:
    from autotrade.data.collector import backfill
    from autotrade.storage.repository import utcnow

    delay = settings.app.data.request_delay_seconds
    est = args.days * 5 / 7 * 4 * delay / 60
    print(f"回補最近 {args.days} 天的三大法人與融資融券（上市＋上櫃），預計約 {est:.0f} 分鐘…")
    conn = connect(settings.db_path)
    try:
        init_db(conn)
        result = backfill(conn, settings, args.days, utcnow(), progress=lambda m: print(f"  {m}"))
    except KeyboardInterrupt:
        print("\n已中斷。已抓到的資料都已存檔，再執行一次會從缺少的日期繼續。")
        return 1
    finally:
        conn.close()
    for ds, (fetched, new) in result.items():
        print(f"✅ {ds}：抓到 {fetched} 筆，新增 {new} 筆")
    return 0


def _cmd_schedule(settings: Settings, args: argparse.Namespace) -> int:
    from autotrade import schedule as sch

    log_path = settings.resolve_path(Path("logs/launchd.log"))
    if sys.platform != "darwin":
        print("自動排程指令目前只支援 macOS。其他系統可以用 cron，例如在 crontab -e 加入：\n")
        print(sch.cron_line(args.times or sch.DEFAULT_TIMES, sys.executable, settings.project_root, log_path))
        return 1
    try:
        if args.action == "install":
            times = args.times or sch.DEFAULT_TIMES
            path = sch.install(times, settings.project_root, log_path)
            print(f"✅ 已設定每天 {'、'.join(sorted(times))} 自動執行。")
            print(f"   排程設定檔：{path}")
            print(f"   執行紀錄：{log_path}")
            print("\n建議接著執行 `uv run autotrade schedule run-now` 立刻測試一次。")
            print("如果 macOS 跳出「是否允許存取『文件』檔案夾」的提示，請按允許。")
        elif args.action == "uninstall":
            removed = sch.uninstall()
            print("✅ 已移除自動排程。" if removed else "目前沒有安裝自動排程。")
        elif args.action == "run-now":
            sch.run_now()
            print(f"▶ 已在背景開始執行，約 1～2 分鐘後完成。進度請看：{log_path}")
            print(f"   例如：tail -f {log_path}")
        else:  # status
            times = sch.installed_times()
            if times is None:
                print("目前沒有安裝自動排程。執行 `uv run autotrade schedule install` 安裝。")
            else:
                state = "已載入" if sch.is_loaded() else "設定檔存在但未載入（請重新執行 install）"
                print(f"自動排程：每天 {'、'.join(times)}（{state}）")
                print(f"執行紀錄：{log_path}")
    except sch.ScheduleError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_test_email(settings: Settings, args: argparse.Namespace) -> int:
    try:
        sender = sender_from_settings(settings)
        to = settings.email_recipient
        sender.send(
            to,
            f"{settings.app.notify.email.subject_prefix} 測試信",
            "如果你收到這封信，代表 AutoTrade 的 Gmail 設定正確。",
        )
    except EmailError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    print(f"📧 測試信已寄到 {to}，請檢查收件匣（也看一下垃圾郵件匣）。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autotrade",
        description="台股記憶體產業資訊監測與 AI 摘要系統",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config", type=Path, default=None, help="設定檔路徑（預設 config/settings.yaml）"
    )
    parser.add_argument(
        "--env", type=Path, default=DEFAULT_ENV_PATH, help="密碼設定檔路徑（預設專案根目錄的 .env）"
    )
    sub = parser.add_subparsers(dest="command", metavar="<指令>")

    p_check = sub.add_parser("check-config", help="檢查設定檔與 .env 是否正確")
    p_check.set_defaults(func=_cmd_check_config)

    p_init = sub.add_parser("init-db", help="建立或升級資料庫")
    p_init.add_argument("--db", type=Path, default=None, help="資料庫路徑（預設取自設定檔）")
    p_init.set_defaults(func=_cmd_init_db)

    p_run = sub.add_parser("run", help="抓取新聞、比對關鍵字，並寄出彙整信")
    p_run.add_argument(
        "--dry-run", action="store_true", help="試跑：抓取並存檔，但不寄信，只在畫面顯示信件內容"
    )
    p_run.set_defaults(func=_cmd_run)

    p_cs = sub.add_parser("check-sources", help="實際抓取每個來源並顯示結果（不存檔、不寄信）")
    p_cs.add_argument("--only", nargs="+", metavar="名稱", help="只檢查指定來源，例如 --only mops ptt")
    p_cs.add_argument("--limit", type=int, default=5, help="每個來源顯示幾則（預設 5）")
    p_cs.set_defaults(func=_cmd_check_sources)

    p_data = sub.add_parser("data", help="查詢累積的數據：個股代號（或名稱）或 prices")
    p_data.add_argument("target", help="股票代號或名稱（例如 2408、南亞科），或 prices 查現貨價")
    p_data.add_argument("--days", type=int, default=20, help="顯示幾個交易日（預設 20）")
    p_data.set_defaults(func=_cmd_data)

    p_bf = sub.add_parser("backfill", help="回補過去的三大法人與融資融券資料")
    p_bf.add_argument("--days", type=int, default=30, help="回補幾天（預設 30）")
    p_bf.set_defaults(func=_cmd_backfill)

    p_sch = sub.add_parser("schedule", help="設定每天自動執行（macOS）")
    p_sch.add_argument("action", choices=["install", "uninstall", "status", "run-now"],
                       help="install 安裝／uninstall 移除／status 查看／run-now 立即執行一次")
    p_sch.add_argument("--times", nargs="+", metavar="HH:MM",
                       help="每天執行的時間（預設 07:30 21:30），例如 --times 08:00 12:30 21:30")
    p_sch.set_defaults(func=_cmd_schedule)

    p_test = sub.add_parser("test-email", help="寄一封測試信，確認 Gmail 設定")
    p_test.set_defaults(func=_cmd_test_email)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    try:
        settings = load_settings(args.config, env_path=args.env)
    except ConfigError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    setup_logging(settings.app.logging.level, settings.log_path)
    return args.func(settings, args)


if __name__ == "__main__":
    raise SystemExit(main())
