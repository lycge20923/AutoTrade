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


# ======================================================================
# Linux：systemd 使用者層級的 timer
# ======================================================================
#
# 設定檔放在 ~/.config/systemd/user/：
# - autotrade.service：要執行的指令
# - autotrade.timer：什麼時間執行；Persistent=true 讓關機錯過的排程在開機後補跑
#
# 使用者層級的 systemd 預設只在你登入時運作。主機上沒人登入也要執行的話，
# 需要開啟 linger：sudo loginctl enable-linger <帳號>

SYSTEMD_UNIT = "autotrade"


def systemd_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".config" / "systemd" / "user"


def _quote(path: str) -> str:
    return f'"{path}"' if " " in path else path


def build_systemd_units(
    times: list[str], python: str, project_root: Path, log_path: Path
) -> tuple[str, str]:
    slots = sorted({parse_time(t) for t in times})
    if not slots:
        raise ScheduleError("至少要指定一個時間")
    service = f"""[Unit]
Description=AutoTrade 記憶體產業監測

[Service]
Type=oneshot
WorkingDirectory={project_root}
ExecStart={_quote(python)} -m autotrade run
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:{log_path}
StandardError=append:{log_path}
"""
    calendars = "\n".join(f"OnCalendar=*-*-* {h:02d}:{m:02d}:00" for h, m in slots)
    timer = f"""[Unit]
Description=AutoTrade 每日排程

[Timer]
{calendars}
Persistent=true

[Install]
WantedBy=timers.target
"""
    return service, timer


def _systemctl(runner: Runner, *args: str) -> subprocess.CompletedProcess:
    return runner(["systemctl", "--user", *args], capture_output=True, text=True)


def systemd_install(
    times: list[str],
    project_root: Path,
    log_path: Path,
    python: str = sys.executable,
    runner: Runner = subprocess.run,
    home: Path | None = None,
) -> Path:
    service, timer = build_systemd_units(times, python, project_root, log_path)
    unit_dir = systemd_dir(home)
    unit_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    (unit_dir / f"{SYSTEMD_UNIT}.service").write_text(service, encoding="utf-8")
    (unit_dir / f"{SYSTEMD_UNIT}.timer").write_text(timer, encoding="utf-8")

    for args in (("daemon-reload",), ("enable", "--now", f"{SYSTEMD_UNIT}.timer"),
                 ("restart", f"{SYSTEMD_UNIT}.timer")):
        r = _systemctl(runner, *args)
        if r.returncode != 0:
            raise ScheduleError(f"systemctl {' '.join(args)} 失敗：{(r.stderr or r.stdout).strip()}")
    return unit_dir / f"{SYSTEMD_UNIT}.timer"


def systemd_uninstall(runner: Runner = subprocess.run, home: Path | None = None) -> bool:
    unit_dir = systemd_dir(home)
    files = [unit_dir / f"{SYSTEMD_UNIT}.timer", unit_dir / f"{SYSTEMD_UNIT}.service"]
    if not any(f.exists() for f in files):
        return False
    _systemctl(runner, "disable", "--now", f"{SYSTEMD_UNIT}.timer")
    for f in files:
        if f.exists():
            f.unlink()
    _systemctl(runner, "daemon-reload")
    return True


def systemd_run_now(runner: Runner = subprocess.run) -> None:
    # --no-block：在背景執行，指令立刻返回
    r = _systemctl(runner, "start", "--no-block", f"{SYSTEMD_UNIT}.service")
    if r.returncode != 0:
        raise ScheduleError(f"無法立即執行：{(r.stderr or r.stdout).strip()}（排程安裝了嗎？）")


def systemd_installed_times(home: Path | None = None) -> list[str] | None:
    timer = systemd_dir(home) / f"{SYSTEMD_UNIT}.timer"
    if not timer.exists():
        return None
    times = []
    for line in timer.read_text(encoding="utf-8").splitlines():
        m = re.match(r"OnCalendar=\*-\*-\* (\d{2}):(\d{2})", line.strip())
        if m:
            times.append(f"{m.group(1)}:{m.group(2)}")
    return times


def systemd_is_active(runner: Runner = subprocess.run) -> bool:
    return _systemctl(runner, "is-active", f"{SYSTEMD_UNIT}.timer").stdout.strip() == "active"


def systemd_next_run(runner: Runner = subprocess.run) -> str:
    r = _systemctl(runner, "list-timers", f"{SYSTEMD_UNIT}.timer", "--no-pager")
    return r.stdout.strip()


def linger_enabled(user: str, runner: Runner = subprocess.run) -> bool | None:
    """沒人登入時是否仍會執行。無法判斷時回傳 None。"""
    try:
        r = runner(["loginctl", "show-user", user, "--property=Linger"], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() == "Linger=yes"


def local_timezone_matches(tz_name: str) -> bool:
    """機器的本地時區是否與設定檔的時區相同（目前的 UTC 時差）。排程時間是看機器的時區。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now().astimezone()
    return now.utcoffset() == now.astimezone(ZoneInfo(tz_name)).utcoffset()
