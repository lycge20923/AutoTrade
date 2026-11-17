from __future__ import annotations

from abc import ABC, abstractmethod
from autotrade.models import Item, SourceType


class SourceError(Exception):
    """來源抓取失敗（例如網路錯誤、網站改版導致解析不到資料）。"""


class Source(ABC):
    """資料來源的共同介面。

    fetch() 只負責抓取並轉成 Item；比對關鍵字、去除重複、存檔都在別處處理。
    某個來源失敗時應直接拋出例外，由呼叫端記錄並繼續處理其他來源。
    """

    # 來源名稱，會記錄在資料庫；同一個類別可以用不同名稱建立多個來源
    name: str
    source_type: SourceType

    @abstractmethod
    def fetch(self) -> list[Item]:
        raise NotImplementedError
