"""Desktop notifications (macOS). Silently does nothing on other platforms."""
from __future__ import annotations

import logging
import platform
import subprocess

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


def run_succeeded(window_name: str, s: dict) -> None:
    headline = (f"{s['picks']} picks from {s['accounts_with_picks']} accounts · "
                f"{s['shared_bets']} shared bets")
    lines = [headline]
    if s["top"]:
        lines.append("Top: " + "; ".join(s["top"]))
    if s["themes"]:
        lines.append("Themes: " + "; ".join(s["themes"]))
    send(f"Capper report ready ({window_name})", "\n".join(lines))


def run_failed(window_name: str, error: BaseException) -> None:
    send(f"Capper run FAILED ({window_name})",
         f"{type(error).__name__}: {error}. See logs/run.log", sound="Basso")
