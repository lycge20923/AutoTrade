"""關鍵字與公司名稱比對。

- 中文詞直接比對子字串。
- 英數字詞會檢查前後不是英數字，避免 "LTA" 誤中 "DELTA"、"DDR5" 誤中 "LPDDR5X"。
- 比對前先做 NFKC 正規化，全形的「ＤＲＡＭ」也能比對到。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from autotrade.config import AppConfig


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _is_ascii_alnum(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def compile_term(term: str) -> re.Pattern[str]:
    t = _normalize(term.strip())
    if not t:
        raise ValueError("關鍵字不能是空字串")
    pattern = re.escape(t)
    if _is_ascii_alnum(t[0]):
        pattern = r"(?<![A-Za-z0-9])" + pattern
    if _is_ascii_alnum(t[-1]):
        pattern += r"(?![A-Za-z0-9])"
    return re.compile(pattern, re.IGNORECASE)


@dataclass(frozen=True)
class MatchResult:
    keywords: tuple[str, ...] = ()
    companies: tuple[str, ...] = ()  # 台股觀察標的
    peers: tuple[str, ...] = ()  # 國際風向球
    risk: tuple[str, ...] = ()  # 命中的風險關鍵字

    @property
    def relevant(self) -> bool:
        """有命中產業關鍵字或台股標的才算相關。

        國際公司單獨出現不算（例如三星的手機新聞），要搭配產業關鍵字。
        """
        return bool(self.keywords or self.companies)

    @property
    def tags(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.companies + self.peers + self.keywords))


class KeywordMatcher:
    def __init__(
        self,
        keywords: list[str],
        risk_keywords: list[str] | None = None,
        companies: list[str] | None = None,
        peers: dict[str, list[str]] | None = None,
    ) -> None:
        self._keywords = [(k, compile_term(k)) for k in dict.fromkeys(keywords)]
        self._risk = set(risk_keywords or [])
        self._companies = [(c, compile_term(c)) for c in dict.fromkeys(companies or [])]
        # peers：{正式名稱: [別名...]}，命中任何別名都回報正式名稱
        self._peers = [
            (name, [compile_term(t) for t in dict.fromkeys([name, *aliases])])
            for name, aliases in (peers or {}).items()
        ]

    @classmethod
    def from_config(cls, app: AppConfig) -> KeywordMatcher:
        return cls(
            keywords=app.keywords.all(),
            risk_keywords=app.keywords.risk,
            companies=app.watchlist.taiwan_names(),
            peers={p.name: p.aliases for p in app.watchlist.international},
        )

    def match(self, text: str) -> MatchResult:
        text = _normalize(text)
        keywords = tuple(k for k, pat in self._keywords if pat.search(text))
        return MatchResult(
            keywords=keywords,
            companies=tuple(c for c, pat in self._companies if pat.search(text)),
            peers=tuple(name for name, pats in self._peers if any(p.search(text) for p in pats)),
            risk=tuple(k for k in keywords if k in self._risk),
        )

    def is_risk_tags(self, tags: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        """從已存檔的 matched_keywords 找出風險關鍵字。"""
        return tuple(t for t in tags if t in self._risk)
