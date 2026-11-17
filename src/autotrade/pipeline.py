"""一次完整執行：抓取 → 比對關鍵字 → 存檔去重 → 寄出彙整信。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from autotrade.config import Settings
from autotrade.data.collector import update_data
from autotrade.matching import KeywordMatcher
from autotrade.models import SourceResult
from autotrade.notify.digest import Digest, build_digest
from autotrade.notify.email import EmailError, EmailSender, sender_from_settings
from autotrade.sources.base import Source
from autotrade.sources.google_news import GoogleNewsSource
from autotrade.sources.mops import MopsSource
from autotrade.sources.ptt import PttSearchSource
from autotrade.storage import connect, init_db
from autotrade.storage.repository import (
    consecutive_failures,
    finish_run,
    mark_notified,
    pending_items,
    save_items,
    start_run,
    utcnow,
)

log = logging.getLogger(__name__)

DIGEST_KIND = "digest"
EMAIL_CHANNEL = "email"


@dataclass
class RunResult:
    sources: list[SourceResult] = field(default_factory=list)
    pending: int = 0
    # 這次要通知的資料各來自哪個來源（可能包含先前試跑存下、還沒寄出的資料）
    pending_by_source: dict[str, int] = field(default_factory=dict)
    digest: Digest | None = None
    emailed: bool = False
    email_error: str | None = None
    health_warnings: list[str] = field(default_factory=list)

    @property
    def all_sources_failed(self) -> bool:
        return bool(self.sources) and all(s.error for s in self.sources)


def build_sources(settings: Settings) -> list[Source]:
    """依設定建立來源。順序有意義：同一篇報導被多個來源抓到時，只會記在第一個來源。"""
    cfg = settings.app.sources
    sources: list[Source] = []
    if cfg.mops.enabled:
        sources.append(MopsSource([s.code for s in settings.app.watchlist.taiwan]))
    if cfg.trendforce.enabled:
        sources.append(GoogleNewsSource(cfg.trendforce, name="trendforce"))
    if cfg.google_news.enabled:
        sources.append(GoogleNewsSource(cfg.google_news))
    if cfg.ptt.enabled:
        sources.append(PttSearchSource(cfg.ptt))
    return sources


def collect(
    conn,
    sources: list[Source],
    matcher: KeywordMatcher,
    now: datetime,
    failure_threshold: int = 3,
) -> tuple[list[SourceResult], list[str]]:
    """回傳 (各來源結果, 來源異常警示)。"""
    warnings: list[str] = []
    results: list[SourceResult] = []
    for src in sources:
        result = SourceResult(name=src.name)
        run_id = start_run(conn, src.name, now)
        try:
            items = src.fetch()
        except Exception as exc:  # 任何來源出錯都不能拖垮其他來源
            log.error("來源 %s 失敗：%s", src.name, exc)
            result.error = str(exc) or type(exc).__name__
            finish_run(conn, run_id, status="error", finished_at=utcnow(), error=result.error)
            result.consecutive_failures = consecutive_failures(conn, src.name)
            # 剛好達到門檻時提醒一次，避免每次執行都寄
            if result.consecutive_failures == failure_threshold:
                warnings.append(
                    f"{src.name} 已連續失敗 {failure_threshold} 次，最近一次錯誤：{result.error}"
                )
            results.append(result)
            continue

        relevant = []
        for it in items:
            m = matcher.match(f"{it.title}\n{it.content}")
            if m.relevant:
                relevant.append(replace(it, matched_keywords=m.tags))
        new_ids = save_items(conn, relevant, now)

        result.fetched, result.relevant, result.new = len(items), len(relevant), len(new_ids)
        finish_run(
            conn,
            run_id,
            status="ok",
            finished_at=utcnow(),
            items_fetched=len(items),
            items_new=len(new_ids),
        )
        log.info("來源 %s：抓到 %d 則，相關 %d 則，新增 %d 則", src.name, *(
            result.fetched, result.relevant, result.new))
        results.append(result)
    return results, warnings


def run(
    settings: Settings,
    *,
    dry_run: bool = False,
    sources: list[Source] | None = None,
    sender: EmailSender | None = None,
    now: datetime | None = None,
    data_client=None,
    sleep=time.sleep,
) -> RunResult:
    now = now or utcnow()
    email_cfg = settings.app.notify.email
    matcher = KeywordMatcher.from_config(settings.app)
    result = RunResult()

    conn = connect(settings.db_path)
    try:
        init_db(conn)
        result.sources, result.health_warnings = collect(
            conn,
            sources if sources is not None else build_sources(settings),
            matcher,
            now,
            settings.app.health.failure_alert_threshold,
        )

        if settings.app.data.enabled:
            data_results, alerts, data_warnings = update_data(
                conn, settings, now, client=data_client, sleep=sleep
            )
            result.sources += data_results
            result.health_warnings += data_warnings
            save_items(conn, alerts, now)

        pending = pending_items(
            conn,
            kind=DIGEST_KIND,
            channel=EMAIL_CHANNEL,
            since=now - timedelta(hours=email_cfg.pending_window_hours),
        )
        result.pending = len(pending)
        for it in pending:
            result.pending_by_source[it.source] = result.pending_by_source.get(it.source, 0) + 1
        if not pending and not result.health_warnings:
            log.info("沒有新的資料需要通知")
            return result

        result.digest = build_digest(
            pending,
            risk_of=lambda it: matcher.is_risk_tags(it.matched_keywords),
            tz=settings.tz,
            now=now,
            max_items=email_cfg.max_items,
            subject_prefix=email_cfg.subject_prefix,
            health_warnings=result.health_warnings,
        )
        if dry_run:
            return result  # 試跑：不寄信，也不標記為已通知

        try:
            sender = sender or sender_from_settings(settings)
            recipient = settings.email_recipient
            sender.send(recipient, result.digest.subject, result.digest.text, result.digest.html)
        except EmailError as exc:
            log.error("%s", exc)
            result.email_error = str(exc)
            return result  # 沒寄成功就不標記，下次執行會再寄

        mark_notified(
            conn, result.digest.item_ids, kind=DIGEST_KIND, channel=EMAIL_CHANNEL, sent_at=utcnow()
        )
        result.emailed = True
        log.info("已寄出 %d 則給 %s", len(result.digest.item_ids), recipient)
        return result
    finally:
        conn.close()
