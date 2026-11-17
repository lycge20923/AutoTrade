"""設定讀取。

- 一般設定（觀察標的、關鍵字、路徑）放在 config/settings.yaml，可以進版控。
- 密碼與 API key 放在 .env，不進版控。系統環境變數會覆蓋 .env 的同名值。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"


class ConfigError(Exception):
    """設定檔不存在、格式錯誤或內容不合法。"""


class Stock(BaseModel):
    code: str
    name: str
    segment: str

    @field_validator("code")
    @classmethod
    def _code_must_be_digits(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit():
            raise ValueError(f"股票代號必須是數字：{v!r}")
        return v


class InternationalPeer(BaseModel):
    name: str
    ticker: str | None = None
    aliases: list[str] = Field(default_factory=list)


class Watchlist(BaseModel):
    taiwan: list[Stock]
    international: list[InternationalPeer] = Field(default_factory=list)

    @model_validator(mode="after")
    def _codes_must_be_unique(self) -> Watchlist:
        codes = [s.code for s in self.taiwan]
        dupes = sorted({c for c in codes if codes.count(c) > 1})
        if dupes:
            raise ValueError(f"觀察標的代號重複：{', '.join(dupes)}")
        return self

    def taiwan_names(self) -> list[str]:
        return [s.name for s in self.taiwan]

    def international_names(self) -> list[str]:
        names: list[str] = []
        for peer in self.international:
            names.append(peer.name)
            names.extend(peer.aliases)
        return names


class Keywords(BaseModel):
    products: list[str] = Field(default_factory=list)
    price: list[str] = Field(default_factory=list)
    supply_demand: list[str] = Field(default_factory=list)
    risk: list[str] = Field(default_factory=list)

    def all(self) -> list[str]:
        """所有關鍵字，去除重複並保留順序。"""
        seen: dict[str, None] = {}
        for group in (self.products, self.price, self.supply_demand, self.risk):
            for kw in group:
                kw = kw.strip()
                if kw:
                    seen.setdefault(kw, None)
        return list(seen)


class GoogleNewsConfig(BaseModel):
    enabled: bool = True
    # Google News 搜尋語法：空白代表 AND，OR 要大寫
    queries: list[str] = Field(default_factory=list)
    # 只抓這段時間內的新聞，例如 1h、12h、1d、7d
    when: str = "1d"
    hl: str = "zh-TW"
    gl: str = "TW"
    ceid: str = "TW:zh-Hant"

    @field_validator("when")
    @classmethod
    def _when_format(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2 or not v[:-1].isdigit() or v[-1] not in "hd":
            raise ValueError(f"when 格式應為數字加 h 或 d，例如 12h、1d：{v!r}")
        return v


class MopsConfig(BaseModel):
    """公開資訊觀測站重大訊息（證交所與櫃買中心 OpenAPI），只保留觀察標的。"""

    enabled: bool = True


class PttConfig(BaseModel):
    enabled: bool = True
    board: str = "Stock"
    # 每個查詞搜尋一次看板，PTT 搜尋一次只能查一個詞
    queries: list[str] = Field(default_factory=list)
    # 只保留幾天內的文章（搜尋結果會包含很久以前的文章）
    max_age_days: int = Field(default=3, ge=1)


class SourcesConfig(BaseModel):
    google_news: GoogleNewsConfig = Field(default_factory=GoogleNewsConfig)
    # TrendForce 官方 RSS 自 2026/07 起停止更新，改用 Google News 搜尋它與轉載它的報導
    trendforce: GoogleNewsConfig = Field(default_factory=lambda: GoogleNewsConfig(when="3d"))
    mops: MopsConfig = Field(default_factory=MopsConfig)
    ptt: PttConfig = Field(default_factory=PttConfig)


class PricesConfig(BaseModel):
    enabled: bool = True
    # 要計算漲跌並提醒的品項（名稱需與 DRAMeXchange 網頁上完全相同）；其他品項也會存檔
    track: list[str] = Field(
        default_factory=lambda: [
            "DDR5 16Gb (2Gx8) 4800/5600",
            "DDR4 16Gb (2Gx8) 3200",
            "DDR4 8Gb (1Gx8) 3200",
            "512Gb TLC",
            "256Gb TLC",
        ]
    )


class DataRulesConfig(BaseModel):
    # 現貨價：與約 7 天前相比漲跌超過幾 %
    price_change_pct: float = Field(default=5.0, gt=0)
    # 外資：連續幾個交易日買超或賣超
    foreign_streak_days: int = Field(default=5, ge=2)
    # 融資：單日餘額增加超過幾 %
    margin_surge_pct: float = Field(default=10.0, gt=0)
    # 融資：前一日餘額至少幾張才檢查，避免基數太小造成誤報
    margin_min_balance: int = Field(default=1000, ge=0)
    # 月營收：一公布就通知
    revenue_release: bool = True
    # 只對最近幾天內的數據發出提醒（回補歷史資料時不會一次冒出大量舊提醒）
    alert_window_days: int = Field(default=3, ge=1)


class DataConfig(BaseModel):
    enabled: bool = True
    # 每次執行時，檢查最近幾天有沒有缺少的交易日資料並補抓
    lookback_days: int = Field(default=7, ge=1)
    # 證交所對短時間大量請求會暫時封鎖，每次請求之間間隔幾秒
    request_delay_seconds: float = Field(default=2.5, ge=0)
    prices: PricesConfig = Field(default_factory=PricesConfig)
    rules: DataRulesConfig = Field(default_factory=DataRulesConfig)


class HealthConfig(BaseModel):
    # 某個來源連續失敗幾次就寄信提醒（只在剛達到時提醒一次）
    failure_alert_threshold: int = Field(default=3, ge=1)


class EmailConfig(BaseModel):
    subject_prefix: str = "[AutoTrade]"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    # 一封信最多列幾則，避免第一次執行時信件過長
    max_items: int = Field(default=80, ge=1)
    # 只通知最近幾小時內抓到、還沒寄過的資料
    pending_window_hours: int = Field(default=72, ge=1)


class NotifyConfig(BaseModel):
    email: EmailConfig = Field(default_factory=EmailConfig)


class StorageConfig(BaseModel):
    db_path: Path = Path("data/autotrade.db")


class LoggingConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    file: Path | None = Path("logs/autotrade.log")


class AppConfig(BaseModel):
    timezone: str = "Asia/Taipei"
    watchlist: Watchlist
    keywords: Keywords
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @field_validator("timezone")
    @classmethod
    def _timezone_must_exist(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"未知的時區：{v!r}") from exc
        return v


class Secrets(BaseModel):
    gmail_address: str | None = None
    gmail_app_password: SecretStr | None = None
    notify_to: str | None = None
    llm_provider: Literal["anthropic", "openai"] | None = None
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    @classmethod
    def from_env(cls, env_path: Path | None) -> Secrets:
        values: dict[str, str | None] = {}
        if env_path is not None and env_path.exists():
            values.update(dotenv_values(env_path))
        # 系統環境變數優先於 .env
        for name in cls.model_fields:
            env_value = os.environ.get(name.upper())
            if env_value is not None:
                values[name.upper()] = env_value
        data = {}
        for name in cls.model_fields:
            raw = values.get(name.upper())
            if raw is not None and raw.strip():
                data[name] = raw.strip()
        return cls(**data)

    def status(self) -> dict[str, bool]:
        """每個欄位是否已設定（不顯示值本身）。"""
        return {name: getattr(self, name) is not None for name in type(self).model_fields}


class Settings(BaseModel):
    app: AppConfig
    secrets: Secrets
    project_root: Path

    def resolve_path(self, path: Path) -> Path:
        return path if path.is_absolute() else self.project_root / path

    @property
    def db_path(self) -> Path:
        return self.resolve_path(self.app.storage.db_path)

    @property
    def log_path(self) -> Path | None:
        f = self.app.logging.file
        return None if f is None else self.resolve_path(f)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.app.timezone)

    @property
    def email_recipient(self) -> str | None:
        return self.secrets.notify_to or self.secrets.gmail_address


def _format_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


def load_settings(
    settings_path: Path | None = None,
    env_path: Path | None = DEFAULT_ENV_PATH,
    project_root: Path = PROJECT_ROOT,
) -> Settings:
    settings_path = settings_path or DEFAULT_SETTINGS_PATH
    if not settings_path.exists():
        raise ConfigError(f"找不到設定檔：{settings_path}")
    try:
        raw = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"設定檔 YAML 格式錯誤：{settings_path}\n{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"設定檔最外層必須是鍵值對：{settings_path}")

    try:
        app = AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"設定檔內容不合法：{settings_path}\n{_format_validation_error(exc)}") from exc
    try:
        secrets = Secrets.from_env(env_path)
    except ValidationError as exc:
        raise ConfigError(f".env 內容不合法：\n{_format_validation_error(exc)}") from exc

    return Settings(app=app, secrets=secrets, project_root=project_root)
