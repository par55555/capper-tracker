"""Install the weekly run as a macOS launchd job.

    python -m capper_tracker.schedule install     # Thu 12:00 + Sun 11:00 local time
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
LOG = ROOT / "logs" / "run.log"

# launchd weekdays: 0 = Sunday ... 4 = Thursday. Times are the Mac's local time.
SCHEDULE = [
    {"Weekday": 4, "Hour": 12, "Minute": 0},   # Thursday noon, before TNF
    {"Weekday": 0, "Hour": 11, "Minute": 0},   # Sunday 11am, before the early games
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


def install() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    _launchctl("bootout", f"{_domain()}/{LABEL}", check=False)  # replace any older version
    with PLIST.open("wb") as f:
        plistlib.dump(build_plist(), f)
    _launchctl("bootstrap", _domain(), str(PLIST))
    print(f"Installed {PLIST}")
    print(f"Runs Thursday 12:00 and Sunday 11:00 using {sys.executable}")
    print(f"Log: {LOG}   Latest report: {ROOT / 'exports' / 'latest_consensus.xlsx'}")


def uninstall() -> None:
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
