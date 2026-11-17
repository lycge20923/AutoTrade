"""用 macOS 的 launchd 定時執行 autotrade run。

launchd 是 macOS 內建的排程工具。和 cron 不同的是，排定時間如果電腦在睡眠，
醒來後會補跑一次；但如果電腦是關機狀態，那次就會跳過。

排程設定檔放在 ~/Library/LaunchAgents/，只在你登入時執行。
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

LABEL = "com.autotrade.run"
DEFAULT_TIMES = ["07:30", "21:30"]
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")

Runner = Callable[..., subprocess.CompletedProcess]


class ScheduleError(Exception):
    pass


def parse_time(value: str) -> tuple[int, int]:
    m = _TIME_RE.match(value.strip())
    if not m or not (0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2)) <= 59):
        raise ScheduleError(f"時間格式應為 HH:MM（24 小時制），例如 07:30：{value!r}")
    return int(m.group(1)), int(m.group(2))


def agent_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(times: list[str], python: str, project_root: Path, log_path: Path) -> dict:
    slots = sorted({parse_time(t) for t in times})
    if not slots:
        raise ScheduleError("至少要指定一個時間")
    return {
        "Label": LABEL,
        # 用目前虛擬環境的 Python 執行，確保套件版本與手動執行時相同
        "ProgramArguments": [python, "-m", "autotrade", "run"],
        "WorkingDirectory": str(project_root),
        "StartCalendarInterval": [{"Hour": h, "Minute": m} for h, m in slots],
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
            "PYTHONUNBUFFERED": "1",
        },
        "ProcessType": "Background",
    }


def _domain() -> str:
    return f"gui/{os.getuid()}"


def is_loaded(runner: Runner = subprocess.run) -> bool:
    r = runner(["launchctl", "print", f"{_domain()}/{LABEL}"], capture_output=True, text=True)
    return r.returncode == 0


def install(
    times: list[str],
    project_root: Path,
    log_path: Path,
    python: str = sys.executable,
    runner: Runner = subprocess.run,
    home: Path | None = None,
) -> Path:
    plist = build_plist(times, python, project_root, log_path)
    path = agent_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        plistlib.dump(plist, f)

    if is_loaded(runner):  # 重新安裝：先卸載舊的，再載入新的設定
        runner(["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=True, text=True)
    r = runner(["launchctl", "bootstrap", _domain(), str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        raise ScheduleError(f"launchctl 載入失敗：{(r.stderr or r.stdout).strip()}")
    return path


def uninstall(runner: Runner = subprocess.run, home: Path | None = None) -> bool:
    path = agent_path(home)
    if is_loaded(runner):
        runner(["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=True, text=True)
    if path.exists():
        path.unlink()
        return True
    return False


def run_now(runner: Runner = subprocess.run) -> None:
    r = runner(["launchctl", "kickstart", "-k", f"{_domain()}/{LABEL}"], capture_output=True, text=True)
    if r.returncode != 0:
        raise ScheduleError(f"無法立即執行：{(r.stderr or r.stdout).strip()}（排程安裝了嗎？）")


def installed_times(home: Path | None = None) -> list[str] | None:
    path = agent_path(home)
    if not path.exists():
        return None
    with path.open("rb") as f:
        data = plistlib.load(f)
    return [f"{s['Hour']:02d}:{s['Minute']:02d}" for s in data.get("StartCalendarInterval", [])]


def cron_line(times: list[str], python: str, project_root: Path, log_path: Path) -> str:
    """非 macOS 時改用 cron 的設定範例。"""
    slots = sorted({parse_time(t) for t in times})
    minutes = {m for _, m in slots}
    if len(minutes) == 1:
        spec = f"{minutes.pop()} {','.join(str(h) for h, _ in slots)} * * *"
        lines = [spec]
    else:
        lines = [f"{m} {h} * * *" for h, m in slots]
    cmd = f"cd {project_root} && {python} -m autotrade run >> {log_path} 2>&1"
    return "\n".join(f"{spec} {cmd}" for spec in lines)
