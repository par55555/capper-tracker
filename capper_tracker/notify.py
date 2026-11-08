"""Desktop notifications (macOS). Silently does nothing on other platforms."""
from __future__ import annotations

import json
import logging
import platform
import subprocess

from .config import ROOT

STATE = ROOT / "data" / "notify_state.json"

log = logging.getLogger(__name__)


def _osa_quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def send(title: str, message: str, sound: str = "Glass") -> None:
    if platform.system() != "Darwin":
        log.info("Notification (not shown, non-macOS): %s | %s", title, message)
        return
    script = (f"display notification {_osa_quote(message[:240])} "
              f"with title {_osa_quote(title)} sound name {_osa_quote(sound)}")
    try:
        subprocess.run(["osascript", "-e", script], check=True, timeout=10, capture_output=True)
    except (subprocess.SubprocessError, OSError) as e:  # never let a notification break a run
        log.warning("Could not show notification: %s", e)


def _seen(window_name: str) -> set:
    try:
        return set(json.loads(STATE.read_text()).get(window_name, []))
    except (OSError, ValueError):
        return set()


def _remember(window_name: str, bets: list) -> None:
    try:
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
    except (OSError, ValueError):
        state = {}
    state = {window_name: sorted(set(state.get(window_name, [])) | set(bets))}  # keep only this week
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))


def run_succeeded(window_name: str, s: dict, only_new: bool = True) -> None:
    """Notify about shared bets. With only_new (the scheduler's ~21 runs a week), stay quiet unless
    a shared bet appeared that hasn't been announced this week."""
    new = [b for b in s.get("shared", []) if b not in _seen(window_name)]
    _remember(window_name, s.get("shared", []))
    if only_new and not new:
        log.info("No new shared bets; no notification")
        return
    lines = [f"New shared bets: {'; '.join(new[:4])}" if new else
             f"{s['picks']} picks from {s['accounts_with_picks']} accounts · {s['shared_bets']} shared bets"]
    if s["top"]:
        lines.append("Top: " + "; ".join(s["top"]))
    send(f"Capper Tracker ({window_name})", "\n".join(lines))


def extraction_failed(window_name: str, failed: int, total: int, error: str = "") -> None:
    hint = "Check ANTHROPIC_API_KEY in .env" if "401" in error or "api-key" in error.lower() else "See logs/run.log"
    send(f"Capper run: {failed} of {total} posts couldn't be read ({window_name})", hint, sound="Basso")


def run_failed(window_name: str, error: BaseException) -> None:
    send(f"Capper run FAILED ({window_name})",
         f"{type(error).__name__}: {error}. See logs/run.log", sound="Basso")
