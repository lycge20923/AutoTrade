from __future__ import annotations

from pathlib import Path

import pytest

from autotrade.cli import main


def _cfg(tmp_path: Path, real_settings_path: Path, sources_enabled: bool = True) -> Path:
    text = real_settings_path.read_text(encoding="utf-8")
    # 把資料庫和 log 導到暫存資料夾，測試不會在專案裡留下檔案
    text = text.replace("db_path: data/autotrade.db", f"db_path: {tmp_path / 'cli.db'}")
    text = text.replace("file: logs/autotrade.log", f"file: {tmp_path / 'test.log'}")
    if not sources_enabled:
        text = text.replace("    enabled: true", "    enabled: false")
    text = text.replace("data:\n  enabled: true", "data:\n  enabled: false")  # 測試不連網
    cfg = tmp_path / "s.yaml"
    cfg.write_text(text, encoding="utf-8")
    return cfg


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "check-config" in out and "run" in out and "test-email" in out


def test_init_db_with_custom_path(
    tmp_path: Path, real_settings_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "x.db"
    assert main(["--config", str(_cfg(tmp_path, real_settings_path)), "init-db", "--db", str(db)]) == 0
    assert db.exists()
    assert "items" in capsys.readouterr().out


def test_bad_config_returns_error_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", str(tmp_path / "missing.yaml"), "check-config"]) == 1
    assert "找不到設定檔" in capsys.readouterr().err


def test_run_dry_run_without_sources(
    tmp_path: Path, real_settings_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _cfg(tmp_path, real_settings_path, sources_enabled=False)
    assert main(["--config", str(cfg), "run", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "沒有啟用任何來源" in out and "沒有新的資料" in out


def test_test_email_without_credentials(
    tmp_path: Path, real_settings_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _cfg(tmp_path, real_settings_path)
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    assert main(["--config", str(cfg), "--env", str(empty_env), "test-email"]) == 1
    assert "GMAIL_ADDRESS" in capsys.readouterr().err


def test_check_sources_unknown_name(
    tmp_path: Path, real_settings_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _cfg(tmp_path, real_settings_path)
    assert main(["--config", str(cfg), "check-sources", "--only", "nope"]) == 1
    assert "可用名稱" in capsys.readouterr().out
