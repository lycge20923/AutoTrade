from __future__ import annotations

from pathlib import Path

import pytest

from autotrade.config import ConfigError, load_settings


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


MINIMAL_YAML = """
watchlist:
  taiwan:
    - { code: "2408", name: 南亞科, segment: DRAM原廠 }
keywords:
  products: [DRAM, NAND]
  risk: [跌價, DRAM]
"""


def test_real_settings_file_is_valid(real_settings_path: Path, tmp_path: Path) -> None:
    settings = load_settings(real_settings_path, env_path=None, project_root=tmp_path)
    codes = [s.code for s in settings.app.watchlist.taiwan]
    assert len(codes) == 12
    assert "2408" in codes and "8299" in codes
    assert settings.app.timezone == "Asia/Taipei"
    assert len(settings.app.keywords.all()) > 20


def test_keywords_all_dedupes_and_keeps_order(tmp_path: Path) -> None:
    settings = load_settings(_write(tmp_path, "s.yaml", MINIMAL_YAML), env_path=None)
    assert settings.app.keywords.all() == ["DRAM", "NAND", "跌價"]


def test_relative_paths_resolve_against_project_root(tmp_path: Path) -> None:
    settings = load_settings(
        _write(tmp_path, "s.yaml", MINIMAL_YAML), env_path=None, project_root=tmp_path
    )
    assert settings.db_path == tmp_path / "data" / "autotrade.db"
    assert settings.log_path == tmp_path / "logs" / "autotrade.log"


def test_duplicate_stock_code_is_rejected(tmp_path: Path) -> None:
    yaml_text = """
watchlist:
  taiwan:
    - { code: "2408", name: 南亞科, segment: DRAM原廠 }
    - { code: "2408", name: 重複, segment: DRAM原廠 }
keywords: {}
"""
    with pytest.raises(ConfigError, match="2408"):
        load_settings(_write(tmp_path, "s.yaml", yaml_text), env_path=None)


def test_non_numeric_stock_code_is_rejected(tmp_path: Path) -> None:
    yaml_text = MINIMAL_YAML.replace('"2408"', '"TSMC"')
    with pytest.raises(ConfigError, match="股票代號"):
        load_settings(_write(tmp_path, "s.yaml", yaml_text), env_path=None)


def test_unknown_timezone_is_rejected(tmp_path: Path) -> None:
    yaml_text = "timezone: Mars/Olympus\n" + MINIMAL_YAML
    with pytest.raises(ConfigError, match="時區"):
        load_settings(_write(tmp_path, "s.yaml", yaml_text), env_path=None)


def test_missing_settings_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="找不到設定檔"):
        load_settings(tmp_path / "nope.yaml", env_path=None)


def test_broken_yaml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="YAML"):
        load_settings(_write(tmp_path, "s.yaml", "watchlist: [unclosed"), env_path=None)


def test_secrets_loaded_from_env_file_and_masked(tmp_path: Path) -> None:
    env = _write(
        tmp_path,
        ".env",
        "GMAIL_ADDRESS=me@example.com\nGMAIL_APP_PASSWORD=abcd efgh\nLLM_PROVIDER=anthropic\nOPENAI_API_KEY=\n",
    )
    settings = load_settings(_write(tmp_path, "s.yaml", MINIMAL_YAML), env_path=env)
    secrets = settings.secrets
    assert secrets.gmail_address == "me@example.com"
    assert secrets.gmail_app_password.get_secret_value() == "abcd efgh"
    assert "abcd" not in repr(secrets)  # 密碼不會出現在 log 或錯誤訊息裡
    assert secrets.openai_api_key is None  # 空值視為未設定
    assert secrets.status()["gmail_app_password"] is True
    assert secrets.status()["anthropic_api_key"] is False


def test_environment_variable_overrides_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _write(tmp_path, ".env", "GMAIL_ADDRESS=file@example.com\n")
    monkeypatch.setenv("GMAIL_ADDRESS", "shell@example.com")
    settings = load_settings(_write(tmp_path, "s.yaml", MINIMAL_YAML), env_path=env)
    assert settings.secrets.gmail_address == "shell@example.com"


def test_invalid_llm_provider_is_rejected(tmp_path: Path) -> None:
    env = _write(tmp_path, ".env", "LLM_PROVIDER=gemini\n")
    with pytest.raises(ConfigError, match="llm_provider"):
        load_settings(_write(tmp_path, "s.yaml", MINIMAL_YAML), env_path=env)
