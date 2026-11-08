"""Install the scheduled runs as macOS launchd jobs.

    python -m capper_tracker.schedule install     # game-window pulls + Sunday every-15-min quick pulls
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
QUICK_LABEL = "com.capper-tracker.sunday-quick"
QUICK_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{QUICK_LABEL}.plist"
LOG = ROOT / "logs" / "run.log"

# launchd weekdays: 0 = Sunday ... 6 = Saturday. Times are the Mac's local time (set for US Eastern).
#
# Chosen from when picks are actually posted (weeks 4-7): most go up 1-8 hours before their game,
# so a pull must land shortly before each slate. Replaying every stored pick against this schedule,
# 83% of all picks (91% of NFL) are fetched before their game starts, vs 37% (62% NFL) with the old
# Thursday-noon + Sunday-11am schedule. Extra pulls cost little: X bills per new post, and each run
# only asks for posts since the last one.
_NFL = [
    (4, 19, 30),   # Thu 7:30pm   before Thursday Night Football
    (0, 8, 45),    # Sun 8:45am   before London games; catches the overnight/early-morning posts
    (0, 12, 30),   # Sun 12:30pm  30 min before the 1:00 games (all their picks are up by ~11am)
    (0, 15, 30),   # Sun 3:30pm   before the 4:05 / 4:25 games
    (0, 19, 30),   # Sun 7:30pm   before Sunday Night Football
    (1, 19, 30),   # Mon 7:30pm   before Monday Night Football
]
_COLLEGE = [(6, 11, 15), (6, 15, 0)]                       # Sat: before noon and 3:30 kickoffs
_DAILY = [(d, 18, 30) for d in range(7)]                    # 6:30pm every day: evening NBA/NHL/MLB
_MIDDAY = [(d, 12, 30) for d in (1, 2, 3, 4, 5)]            # 12:30pm weekdays: afternoon slates
SCHEDULE = [{"Weekday": d, "Hour": h, "Minute": m} for d, h, m in _NFL + _COLLEGE + _DAILY + _MIDDAY]

# Sunday quick pulls: every 15 minutes, 7:00am-8:15pm, so new picks reach the Live page within
# ~15 minutes of being posted. Each asks every account for at most QUICK_MAX_POSTS new posts:
# in weeks 4-7 no account ever posted more than 5 times in one 15-minute window on a Sunday, so
# nothing is skipped (5 is also the smallest batch X returns). Times the main schedule already
# covers are left out, and a run lock stops two pulls overlapping. No Excel file per quick pull.
QUICK_MAX_POSTS = 5
_MAIN_SUNDAY = {(h, m) for d, h, m in _NFL + _DAILY if d == 0}
QUICK_SCHEDULE = [{"Weekday": 0, "Hour": h, "Minute": m}
                  for h in range(7, 21) for m in (0, 15, 30, 45)
                  if (h, m) <= (20, 15) and (h, m) not in _MAIN_SUNDAY]


def _plist(label: str, args: list[str], schedule: list[dict], python: str) -> dict:
    return {
        "Label": label,
        "ProgramArguments": [python, "-m", "capper_tracker", *args],
        "WorkingDirectory": str(ROOT),
        "StartCalendarInterval": schedule,
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
        "EnvironmentVariables": {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONUNBUFFERED": "1",
        },
        "ProcessType": "Background",
    }


def build_plist(python: str = sys.executable) -> dict:
    return _plist(LABEL, ["run", "--notify"], SCHEDULE, python)


def build_quick_plist(python: str = sys.executable) -> dict:
    return _plist(QUICK_LABEL, ["run", "--notify", "--no-excel", "--max-posts", str(QUICK_MAX_POSTS)],
                  QUICK_SCHEDULE, python)


JOBS = [(LABEL, PLIST, build_plist), (QUICK_LABEL, QUICK_PLIST, build_quick_plist)]


def _launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], check=check, capture_output=True, text=True)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    for label, path, build in JOBS:
        _launchctl("bootout", f"{_domain()}/{label}", check=False)  # replace any older version
        with path.open("wb") as f:
            plistlib.dump(build(), f)
        _launchctl("bootstrap", _domain(), str(path))
        print(f"Installed {path}")
    print(f"Main pulls: {len(SCHEDULE)} a week around game windows. "
          f"Sunday quick pulls: every 15 min, 7:00am-8:15pm ({len(QUICK_SCHEDULE)} runs, "
          f"max {QUICK_MAX_POSTS} new posts per account each).")
    print(f"Python: {sys.executable}")
    print("Notifications only appear when a new shared bet shows up, or when something fails.")
    print(f"Log: {LOG}   Latest report: {ROOT / 'exports' / 'latest_consensus.xlsx'}")


def uninstall() -> None:
    for label, path, _ in JOBS:
        _launchctl("bootout", f"{_domain()}/{label}", check=False)
        path.unlink(missing_ok=True)
    print("Removed the scheduled jobs.")


def run_now() -> None:
    _launchctl("kickstart", f"{_domain()}/{LABEL}")
    print(f"Started. Follow along with: tail -f {LOG}")


def status() -> None:
    for label, path, _ in JOBS:
        print(f"{label}:")
        if not path.exists():
            print("  Not installed.")
            continue
        out = _launchctl("print", f"{_domain()}/{label}", check=False)
        keep = ("state =", "last exit code", "runs =")
        lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip().startswith(keep)]
        print("  " + ("\n  ".join(lines) or out.stderr.strip() or "Installed (no runs yet)."))


def main() -> None:
    ap = argparse.ArgumentParser(prog="capper_tracker.schedule")
    ap.add_argument("command", choices=["install", "uninstall", "run-now", "status"])
    if sys.platform != "darwin":
        sys.exit("The scheduler uses macOS launchd; on Linux use the cron lines in the README.")
    {"install": install, "uninstall": uninstall, "run-now": run_now, "status": status}[
        ap.parse_args().command]()


if __name__ == "__main__":
    main()
