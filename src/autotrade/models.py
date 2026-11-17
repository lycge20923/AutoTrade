"""各模組之間傳遞的共用資料格式。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

# news: 新聞媒體　official: 官方公告（如公開資訊觀測站）
# data: 數據（報價、法人買賣超）　social: 社群論壇（如 PTT）
SourceType = Literal["news", "official", "data", "social"]


@dataclass(frozen=True)
class Item:
    """所有資料來源統一輸出的格式。url 用來去除重複。"""

    source: str
    source_type: SourceType
    title: str
    url: str
    published_at: datetime | None = None
    content: str = ""
    # 發布者，例如「經濟日報」；官方公告或數據來源可留空
    publisher: str = ""
    matched_keywords: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class SourceResult:
    """一個來源（或數據集）這次執行的結果。"""

    name: str
    fetched: int = 0
    relevant: int = 0  # 文字來源：相關筆數；數據：產生的提醒數
    new: int = 0
    error: str | None = None
    consecutive_failures: int = 0
    kind: str = "text"  # text：文字來源；data：數據
