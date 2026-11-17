from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest

from autotrade import schedule as sch


class FakeLaunchctl:
    def __init__(self, loaded: bool = False, fail: str | None = None):
        self.loaded, self.fail = loaded, fail
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        sub = cmd[1]
        if sub == self.fail:
            return subprocess.CompletedProcess(cmd, 5, "", "Bootstrap failed: 5: Input/output error")
        if sub == "print":
            return subprocess.CompletedProcess(cmd, 0 if self.loaded else 113, "", "")
        if sub == "bootstrap":
            self.loaded = True
        if sub == "bootout":
            self.loaded = False
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.mark.parametrize("value,expected", [("07:30", (7, 30)), ("7:05", (7, 5)), ("23:59", (23, 59))])
def test_parse_time(value, expected) -> None:
    assert sch.parse_time(value) == expected


@pytest.mark.parametrize("value", ["24:00", "7:5", "0730", "7:60", "早上"])
def test_parse_time_invalid(value) -> None:
    with pytest.raises(sch.ScheduleError):
        sch.parse_time(value)


def test_build_plist(tmp_path: Path) -> None:
    p = sch.build_plist(["21:30", "07:30", "07:30"], "/proj/.venv/bin/python", tmp_path, tmp_path / "l.log")
    assert p["Label"] == "com.autotrade.run"
    assert p["ProgramArguments"] == ["/proj/.venv/bin/python", "-m", "autotrade", "run"]
    assert p["WorkingDirectory"] == str(tmp_path)
    assert p["StartCalendarInterval"] == [{"Hour": 7, "Minute": 30}, {"Hour": 21, "Minute": 30}]
    assert p["StandardOutPath"] == p["StandardErrorPath"] == str(tmp_path / "l.log")


def test_install_writes_plist_and_loads(tmp_path: Path) -> None:
    fake = FakeLaunchctl()
    path = sch.install(["07:30"], tmp_path / "proj", tmp_path / "proj/logs/launchd.log",
                       python="/py", runner=fake, home=tmp_path)
    assert path == tmp_path / "Library/LaunchAgents/com.autotrade.run.plist"
    with path.open("rb") as f:
        assert plistlib.load(f)["StartCalendarInterval"] == [{"Hour": 7, "Minute": 30}]
    assert [c[1] for c in fake.calls] == ["print", "bootstrap"]
    assert (tmp_path / "proj/logs").is_dir()
    assert sch.installed_times(tmp_path) == ["07:30"]


def test_reinstall_unloads_first(tmp_path: Path) -> None:
    fake = FakeLaunchctl(loaded=True)
    sch.install(["08:00", "21:30"], tmp_path, tmp_path / "l.log", python="/py", runner=fake, home=tmp_path)
    assert [c[1] for c in fake.calls] == ["print", "bootout", "bootstrap"]
    assert sch.installed_times(tmp_path) == ["08:00", "21:30"]


def test_install_failure_is_reported(tmp_path: Path) -> None:
    with pytest.raises(sch.ScheduleError, match="載入失敗"):
        sch.install(["07:30"], tmp_path, tmp_path / "l.log", python="/py",
                    runner=FakeLaunchctl(fail="bootstrap"), home=tmp_path)


def test_uninstall(tmp_path: Path) -> None:
    fake = FakeLaunchctl()
    sch.install(["07:30"], tmp_path, tmp_path / "l.log", python="/py", runner=fake, home=tmp_path)
    assert sch.uninstall(runner=fake, home=tmp_path) is True
    assert not sch.agent_path(tmp_path).exists() and fake.loaded is False
    assert sch.uninstall(runner=fake, home=tmp_path) is False
    assert sch.installed_times(tmp_path) is None


def test_run_now_requires_install() -> None:
    with pytest.raises(sch.ScheduleError, match="安裝了嗎"):
        sch.run_now(runner=FakeLaunchctl(fail="kickstart"))


def test_cron_line(tmp_path: Path) -> None:
    line = sch.cron_line(["07:30", "21:30"], "/py", Path("/proj"), Path("/proj/logs/l.log"))
    assert line == "30 7,21 * * * cd /proj && /py -m autotrade run >> /proj/logs/l.log 2>&1"
