"""Install the scheduled runs as macOS launchd jobs.

    python -m capper_tracker.schedule install     # Thu + Sun, 30 min before each NFL kickoff slot
    python -m capper_tracker.schedule run-now     # trigger the scheduled job immediately (test)
    python -m capper_tracker.schedule status
    python -m capper_tracker.schedule uninstall

Run `install` from the environment you want the job to use (e.g. after `conda activate capper`);
that Python interpreter is baked into the job. If the Mac is asleep at a scheduled time, launchd
runs the job once when it wakes. If it's shut down, that run is skipped.
"""
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from .config import ROOT

LABEL = "com.capper-tracker.weekly"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
# An earlier version also installed Sunday every-15-minute pulls; install/uninstall remove it.
OLD_QUICK_LABEL = "com.capper-tracker.sunday-quick"
OLD_QUICK_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{OLD_QUICK_LABEL}.plist"
LOG = ROOT / "logs" / "run.log"

# launchd weekdays: 0 = Sunday ... 4 = Thursday. Times are the Mac's local time (set for US Eastern).
#
# NFL only, Thursdays and Sundays, one pull ~30 minutes before each kickoff slot. Replaying weeks
# 4-7, this collects 85% of NFL picks before their game: Sunday 1pm and 4pm games 100%, London 95%,
# Sunday night 91%, Thursday night 88%. Monday night games get only the picks already up by
# Sunday's 7:50pm pull (13%); other sports aren't covered.
SCHEDULE = [
    {"Weekday": 4, "Hour": 19, "Minute": 45},   # Thu 7:45pm  before Thursday Night Football (8:15)
    {"Weekday": 0, "Hour": 9, "Minute": 0},     # Sun 9:00am  before the London game (9:30)
    {"Weekday": 0, "Hour": 12, "Minute": 30},   # Sun 12:30pm before the 1:00 games
    {"Weekday": 0, "Hour": 15, "Minute": 35},   # Sun 3:35pm  before the 4:05 / 4:25 games
    {"Weekday": 0, "Hour": 19, "Minute": 50},   # Sun 7:50pm  before Sunday Night Football (8:20)
]


def build_plist(python: str = sys.executable) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "capper_tracker", "run", "--notify"],
        "WorkingDirectory": str(ROOT),
        "StartCalendarInterval": SCHEDULE,
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
        "EnvironmentVariables": {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONUNBUFFERED": "1",
        },
        "ProcessType": "Background",
    }


def _launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], check=check, capture_output=True, text=True)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _remove_old_quick_job() -> None:
    _launchctl("bootout", f"{_domain()}/{OLD_QUICK_LABEL}", check=False)
    OLD_QUICK_PLIST.unlink(missing_ok=True)


def install() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    _remove_old_quick_job()
    _launchctl("bootout", f"{_domain()}/{LABEL}", check=False)  # replace any older version
    with PLIST.open("wb") as f:
        plistlib.dump(build_plist(), f)
    _launchctl("bootstrap", _domain(), str(PLIST))
    print(f"Installed {PLIST}")
    print("Runs 30 min before each NFL kickoff slot: Thu 7:45pm; Sun 9:00am, 12:30pm, 3:35pm, 7:50pm "
          f"(using {sys.executable})")
    print("Notifications only appear when a new shared bet shows up, or when something fails.")
    print(f"Log: {LOG}   Latest report: {ROOT / 'exports' / 'latest_consensus.xlsx'}")


def uninstall() -> None:
    _remove_old_quick_job()
    _launchctl("bootout", f"{_domain()}/{LABEL}", check=False)
    PLIST.unlink(missing_ok=True)
    print("Removed the scheduled job.")


def run_now() -> None:
    _launchctl("kickstart", f"{_domain()}/{LABEL}")
    print(f"Started. Follow along with: tail -f {LOG}")


def status() -> None:
    if not PLIST.exists():
        print("Not installed.")
        return
    out = _launchctl("print", f"{_domain()}/{LABEL}", check=False)
    keep = ("state =", "last exit code", "runs =")
    lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip().startswith(keep)]
    print("\n".join(lines) or out.stderr.strip() or "Installed (no runs yet).")


def main() -> None:
    ap = argparse.ArgumentParser(prog="capper_tracker.schedule")
    ap.add_argument("command", choices=["install", "uninstall", "run-now", "status"])
    if sys.platform != "darwin":
        sys.exit("The scheduler uses macOS launchd; on Linux use the cron lines in the README.")
    {"install": install, "uninstall": uninstall, "run-now": run_now, "status": status}[
        ap.parse_args().command]()


if __name__ == "__main__":
    main()
