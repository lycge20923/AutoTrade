"""把待通知的資料整理成一封信（純文字 + HTML）。

分區順序：來源異常 → 風險訊號 → 數據異動 → 公司公告 → 新聞 → PTT 討論。
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Callable

from autotrade.storage.repository import StoredItem

SNIPPET_CHARS = 150

# (分區標題, 屬於這區的 source_type)
_SECTIONS: list[tuple[str, set[str]]] = [
    ("📊 數據異動", {"data"}),
    ("📢 公司公告", {"official"}),
    ("📰 新聞", {"news"}),
    ("💬 PTT 討論", {"social"}),
]
_RISK_TITLE = "⚠️ 風險訊號"
_HEALTH_TITLE = "🔧 資料來源異常"


@dataclass(frozen=True)
class Digest:
    subject: str
    text: str
    html: str
    item_ids: list[int]  # 這封信涵蓋的所有資料（包含超過上限沒列出的）


def _fmt_time(dt: datetime, tz: tzinfo) -> str:
    return dt.astimezone(tz).strftime("%m/%d %H:%M")


def _item_time(it: StoredItem, tz: tzinfo) -> str:
    """數據提醒只顯示日期：交易資料是「某一天」的數字，沒有確切的發布時刻。"""
    if it.source_type == "data":
        return it.sort_time.astimezone(tz).strftime("%m/%d")
    return _fmt_time(it.sort_time, tz)


def _snippet(it: StoredItem) -> str:
    text = " ".join(it.content.split())
    return text[:SNIPPET_CHARS] + ("…" if len(text) > SNIPPET_CHARS else "")


Entry = tuple[StoredItem, tuple[str, ...]]


def build_digest(
    items: list[StoredItem],
    *,
    risk_of: Callable[[StoredItem], tuple[str, ...]],
    tz: tzinfo,
    now: datetime,
    max_items: int,
    subject_prefix: str = "[AutoTrade]",
    health_warnings: list[str] | None = None,
) -> Digest:
    warnings = health_warnings or []
    entries: list[Entry] = [(it, risk_of(it)) for it in items]

    # 先決定每則落在哪一區，再依分區順序套用上限
    sections: list[tuple[str, list[Entry]]] = [(_RISK_TITLE, [e for e in entries if e[1]])]
    for title, types in _SECTIONS:
        sections.append((title, [e for e in entries if not e[1] and e[0].source_type in types]))
    leftovers = [
        e for e in entries
        if not e[1] and not any(e[0].source_type in t for _, t in _SECTIONS)
    ]
    if leftovers:
        sections.append(("其他", leftovers))

    remaining = max_items
    shown_sections: list[tuple[str, list[Entry]]] = []
    for title, sec in sections:
        shown = sec[:remaining]
        remaining -= len(shown)
        shown_sections.append((title, shown))
    hidden = len(entries) - sum(len(s) for _, s in shown_sections)
    risk_count = len(sections[0][1])

    if items:
        subject = f"{subject_prefix} 記憶體產業 {len(items)} 則"
        if risk_count:
            subject += f"｜⚠️ 風險訊號 {risk_count} 則"
        if warnings:
            subject += "｜🔧 來源異常"
    else:
        subject = f"{subject_prefix} {_HEALTH_TITLE}"
    subject += f"｜{_fmt_time(now, tz)}"

    # --- 純文字版 ---
    lines: list[str] = []
    if warnings:
        lines += [_HEALTH_TITLE, "=" * 30, *[f"- {w}" for w in warnings], ""]
    for title, shown in shown_sections:
        if not shown:
            continue
        lines += [f"{title}（{len(shown)}）", "=" * 30]
        for n, (it, risk) in enumerate(shown, 1):
            meta = " · ".join(
                x for x in [it.publisher, _item_time(it, tz), "、".join(it.matched_keywords)] if x
            )
            lines += [f"{n}. {it.title}", f"   {meta}"]
            if it.source_type in ("official", "data") and it.content:
                lines.append(f"   {_snippet(it)}")
            if risk:
                lines.append(f"   ⚠️ 風險關鍵字：{'、'.join(risk)}")
            lines += [f"   {it.url}", ""]
    if hidden:
        lines.append(f"另有 {hidden} 則未列出（超過每封信 {max_items} 則的上限）。")
    text = "\n".join(lines).rstrip() + "\n"

    # --- HTML 版 ---
    def tag(t: str, risky: bool) -> str:
        bg, fg = ("#fde8e8", "#b42318") if risky else ("#eef2f7", "#344054")
        return (
            f'<span style="display:inline-block;background:{bg};color:{fg};border-radius:4px;'
            f'padding:1px 6px;margin:2px 4px 0 0;font-size:12px">{html.escape(t)}</span>'
        )

    def html_section(title: str, shown: list[Entry]) -> str:
        if not shown:
            return ""
        rows = []
        for it, risk in shown:
            meta = " · ".join(html.escape(x) for x in [it.publisher, _item_time(it, tz)] if x)
            snippet = (
                f'<div style="color:#475467;font-size:13px;margin-top:2px">{html.escape(_snippet(it))}</div>'
                if it.source_type in ("official", "data") and it.content
                else ""
            )
            rows.append(
                '<li style="margin:0 0 14px 0">'
                f'<a href="{html.escape(it.url, quote=True)}" style="color:#1d4ed8;font-size:15px;'
                f'text-decoration:none">{html.escape(it.title)}</a>'
                f'<div style="color:#667085;font-size:12px;margin-top:2px">{meta}</div>'
                f"{snippet}<div>{''.join(tag(t, t in risk) for t in it.matched_keywords)}</div></li>"
            )
        return (
            f'<h3 style="margin:20px 0 8px 0;font-size:16px">{html.escape(title)}（{len(shown)}）</h3>'
            f'<ol style="padding-left:20px;margin:0">{"".join(rows)}</ol>'
        )

    health_html = (
        f'<div style="background:#fffaeb;border:1px solid #fedf89;border-radius:6px;padding:8px 12px;'
        f'margin-top:12px"><strong>{_HEALTH_TITLE}</strong><ul style="margin:6px 0 0 0;padding-left:18px">'
        + "".join(f"<li>{html.escape(w)}</li>" for w in warnings)
        + "</ul></div>"
        if warnings
        else ""
    )
    footer = (
        f'<p style="color:#667085;font-size:12px">另有 {hidden} 則未列出（超過每封信 {max_items} 則的上限）。</p>'
        if hidden
        else ""
    )
    html_body = (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'PingFang TC\',\'Microsoft JhengHei\','
        'sans-serif;max-width:720px;color:#101828">'
        + health_html
        + "".join(html_section(t, s) for t, s in shown_sections)
        + footer
        + '<p style="color:#98a2b3;font-size:11px;margin-top:24px">由 AutoTrade 自動產生</p></div>'
    )

    return Digest(subject=subject, text=text, html=html_body, item_ids=[it.id for it in items])
