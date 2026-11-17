from __future__ import annotations

import logging
from pathlib import Path

import pytest

from autotrade.config import Secrets, Settings, load_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_SETTINGS = PROJECT_ROOT / "config" / "settings.yaml"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _clean_secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """避免開發者電腦上的環境變數影響測試結果。"""
    for name in Secrets.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture
def real_settings_path() -> Path:
    return REAL_SETTINGS


@pytest.fixture
def sample_feed() -> bytes:
    return (FIXTURES / "google_news_sample.xml").read_bytes()


@pytest.fixture
def make_settings(tmp_path: Path, real_settings_path: Path):
    """以真實設定檔為基礎，但資料庫和 log 都放在暫存資料夾。"""

    def _make(env: str = "", extra_yaml: str = "", data_enabled: bool = False) -> Settings:
        text = real_settings_path.read_text(encoding="utf-8")
        if not data_enabled:  # 預設關閉數據線，避免測試連網
            text = text.replace("data:\n  enabled: true", "data:\n  enabled: false")
        text = text.replace("db_path: data/autotrade.db", f"db_path: {tmp_path / 'test.db'}")
        text = text.replace("file: logs/autotrade.log", "file: null")
        cfg = tmp_path / "settings.yaml"
        cfg.write_text(text + extra_yaml, encoding="utf-8")
        env_path = tmp_path / ".env"
        env_path.write_text(env, encoding="utf-8")
        return load_settings(cfg, env_path=env_path, project_root=tmp_path)

    return _make


@pytest.fixture(autouse=True)
def _reset_logging():
    """CLI 測試會設定 log 輸出到當下被攔截的終端機，測試結束後清掉，避免影響下一個測試。"""
    yield
    logger = logging.getLogger("autotrade")
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
