"""Runtime configuration, read from the environment (or a .env file in the project root)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# override=True: .env wins over (possibly empty) variables already exported in the shell.
load_dotenv(ROOT / ".env", override=True)

DB_PATH = Path(os.getenv("CAPPER_DB", ROOT / "data" / "cappers.db"))
EXPORT_DIR = Path(os.getenv("CAPPER_EXPORT_DIR", ROOT / "exports"))
ACCOUNTS_FILE = Path(os.getenv("CAPPER_ACCOUNTS", ROOT / "accounts.txt"))

X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "")
EXTRACT_MODEL = os.getenv("EXTRACT_MODEL", "claude-sonnet-5-5")
EXTRACT_WORKERS = int(os.getenv("EXTRACT_WORKERS", "4"))

# Cost guards. X pay-per-use bills each post returned (~$0.005) and each user lookup (~$0.01).
MAX_TWEETS_PER_ACCOUNT = int(os.getenv("MAX_TWEETS_PER_ACCOUNT", "15"))
X_COST_PER_POST = 0.005
X_COST_PER_USER = 0.010

# A team needs this many more accounts leaning one way before it gets a theme label.
THEME_MIN_ACCOUNTS = int(os.getenv("THEME_MIN_ACCOUNTS", "2"))

TZ = ZoneInfo("America/New_York")
# NFL weeks run Tuesday -> Monday (ET). Set to the Tuesday before the season opener; verify each season.
NFL_WEEK1_TUESDAY = date.fromisoformat(os.getenv("NFL_WEEK1_TUESDAY", "2026-09-08"))


def nfl_week(ts: datetime) -> int:
    """NFL week number for a timestamp (<= 0 means preseason)."""
    return (ts.astimezone(TZ).date() - NFL_WEEK1_TUESDAY).days // 7 + 1


def week_window(week: int) -> tuple[datetime, datetime]:
    """[start, end) of an NFL week as tz-aware ET datetimes."""
    start = datetime.combine(NFL_WEEK1_TUESDAY + timedelta(weeks=week - 1), time(0), TZ)
    return start, start + timedelta(weeks=1)


def current_week() -> int:
    return nfl_week(datetime.now(TZ))


@dataclass(frozen=True)
class Window:
    """The span of posts a run aggregates. `end` is never later than the moment of the run."""
    start: datetime
    end: datetime
    name: str  # used in the report filename

    @property
    def start_utc(self) -> str:
        return self.start.astimezone(timezone.utc).replace(microsecond=0).isoformat()

    @property
    def end_utc(self) -> str:
        return self.end.astimezone(timezone.utc).replace(microsecond=0).isoformat()

    def __str__(self) -> str:
        fmt = "%a %m/%d %I:%M %p"
        return f"{self.name}: {self.start.astimezone(TZ):{fmt}} -> {self.end.astimezone(TZ):{fmt}} ET"


def nfl_week_to_date(week: int | None = None, now: datetime | None = None) -> Window:
    """Posts from the Tuesday that opens the NFL week up to now (or the week's end, if it's over).

    Run on Thursday -> Tue..Thu posts; run on Sunday -> Tue..Sun posts. Last week's Sunday/Monday
    picks (already settled) are excluded because they belong to the previous NFL week.
    """
    now = now or datetime.now(TZ)
    week = week or nfl_week(now)
    start, end = week_window(week)
    return Window(start, min(end, now), f"week{week:02d}")


def lookback(days: int, now: datetime | None = None) -> Window:
    """Rolling window: the last N days up to now."""
    now = now or datetime.now(TZ)
    return Window(now - timedelta(days=days), now, f"last{days}d")
