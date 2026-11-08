"""Match each bet to its game (matchup, start time, status) using ESPN's public scoreboard feeds.

No API key needed. If ESPN can't be reached, the report still builds; the game columns are just blank.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

log = logging.getLogger(__name__)

URL = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
ESPN_PATHS = {
    "NFL": "football/nfl",
    "NCAAF": "football/college-football",
    "MLB": "baseball/mlb",
    "NBA": "basketball/nba",
    "WNBA": "basketball/wnba",
    "NCAAB": "basketball/mens-college-basketball",
    "NHL": "hockey/nhl",
}
# College scoreboards only return featured games unless asked for the whole division.
EXTRA_PARAMS = {"NCAAF": {"groups": "80", "limit": "500"}, "NCAAB": {"groups": "50", "limit": "500"}}
LEAGUE_ALIASES = {"CFB": "NCAAF", "NCAA FOOTBALL": "NCAAF", "COLLEGE FOOTBALL": "NCAAF",
                  "CBB": "NCAAB", "NCAAM": "NCAAB"}
# Our abbreviation -> ESPN's, where they differ.
ABBR_ALIASES = {"WAS": "WSH"}
STATUS = {"pre": "Upcoming", "in": "Live", "post": "Final"}


def league_key(league) -> str:
    lg = str(league or "NFL").upper().strip()
    return LEAGUE_ALIASES.get(lg, lg)


def _norm(text) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", "", str(text or "").upper())).strip()


@dataclass
class Game:
    league: str
    event_id: str
    start: datetime
    short: str               # "CIN @ BAL"
    venue: str
    status: str              # Upcoming / Live / Final
    home_abbrs: set = field(default_factory=set)
    away_abbrs: set = field(default_factory=set)
    home_names: set = field(default_factory=set)
    away_names: set = field(default_factory=set)


def _team_keys(team: dict) -> tuple[set, set]:
    abbr = _norm(team.get("abbreviation"))
    abbrs = {abbr} | {k for k, v in ABBR_ALIASES.items() if v == abbr}
    loc, nick = team.get("location") or "", team.get("name") or ""
    names = {_norm(x) for x in (team.get("displayName"), team.get("shortDisplayName"), loc, nick,
                                f"{loc} {nick}") if x}
    return abbrs - {""}, names - {""}


def parse_scoreboard(data: dict, league: str) -> list[Game]:
    games = []
    for ev in data.get("events", []):
        try:
            comp = ev["competitions"][0]
            sides = {c["homeAway"]: c["team"] for c in comp["competitors"]}
            ha, hn = _team_keys(sides["home"])
            aa, an = _team_keys(sides["away"])
            start = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            games.append(Game(
                league=league, event_id=str(ev["id"]), start=start,
                short=ev.get("shortName") or f"{sides['away'].get('abbreviation')} @ {sides['home'].get('abbreviation')}",
                venue=((comp.get("venue") or {}).get("fullName") or ""),
                status=STATUS.get(((ev.get("status") or {}).get("type") or {}).get("state", ""), ""),
                home_abbrs=ha, away_abbrs=aa, home_names=hn, away_names=an,
            ))
        except (KeyError, IndexError, ValueError) as e:
            log.debug("Skipping malformed ESPN event: %s", e)
    return games


def side_strength(team, abbrs: set, names: set) -> int:
    """How well a team string fits one side of a game: 3 exact abbreviation, 2 exact name, 1 loose, 0 no."""
    n = _norm(team)
    if not n:
        return 0
    if n in abbrs:
        return 3
    if n in names:
        return 2
    return 1 if _side_matches(team, abbrs, names) else 0


def pick_side(team, home: tuple[set, set], away: tuple[set, set]) -> str | None:
    """'home' / 'away' for a team string, preferring the stronger match ("LOUISIANA TECH" is the
    exact name of one side and only a loose fit for "Louisiana"). None if absent or a tie."""
    h, a = side_strength(team, *home), side_strength(team, *away)
    if h == a:
        return None
    return "home" if h > a else "away"


def _side_matches(team, abbrs: set, names: set) -> bool:
    """Our team string ('SD', 'NYL', 'New York Liberty', 'Ottawa Senators') vs one side of a game."""
    n = _norm(team)
    if not n:
        return False
    if n in abbrs or n in names:
        return True
    if len(n) <= 4:  # abbreviation that's one letter off ESPN's, e.g. NYL vs NY, LVA vs LV
        return any(abs(len(n) - len(a)) <= 1 and (n.startswith(a) or a.startswith(n)) and len(a) >= 2
                   for a in abbrs)
    return any(len(k) > 4 and (k in n or n in k) for k in names)


class Schedule:
    """Loads ESPN scoreboards for a date range and finds the game a bet belongs to."""

    def __init__(self, fetch=None, cache_dir: Path | None = None):
        self._fetch = fetch or self._http_get
        # Scoreboards for days that finished 3+ days ago never change; cache them on disk when asked.
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.games: dict[str, list[Game]] = {}
        self.failed: set[str] = set()

    def _get_day(self, league: str, day: date, params: dict) -> dict:
        settled = day <= date.today() - timedelta(days=3)
        path = self.cache_dir / f"{league}_{day:%Y%m%d}.json" if self.cache_dir and settled else None
        if path and path.exists():
            return json.loads(path.read_text())
        data = self._fetch(URL.format(path=ESPN_PATHS[league]), params)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
        return data

    @staticmethod
    def _http_get(url: str, params: dict) -> dict:
        r = requests.get(url, params=params, timeout=15, headers={"User-Agent": "capper-tracker"})
        r.raise_for_status()
        return r.json()

    def load(self, leagues, first_day: date, last_day: date) -> None:
        for league in {league_key(lg) for lg in leagues}:
            if league in self.games or league not in ESPN_PATHS:
                continue
            seen, found = set(), []
            day = first_day
            while day <= last_day:
                params = {"dates": day.strftime("%Y%m%d"), **EXTRA_PARAMS.get(league, {})}
                try:
                    data = self._get_day(league, day, params)
                except Exception as e:  # never let the schedule break a run
                    log.warning("ESPN %s schedule unavailable (%s); game columns left blank", league, e)
                    self.failed.add(league)
                    break
                for g in parse_scoreboard(data, league):
                    if g.event_id not in seen:
                        seen.add(g.event_id)
                        found.append(g)
                day += timedelta(days=1)
            self.games[league] = sorted(found, key=lambda g: g.start)

    def find(self, league, team, opponent=None, posted_at: datetime | None = None) -> Game | None:
        games = self.games.get(league_key(league), [])
        cands = [g for g in games if _side_matches(team, g.home_abbrs, g.home_names)
                 or _side_matches(team, g.away_abbrs, g.away_names)]
        if opponent:
            both = [g for g in cands if _side_matches(opponent, g.home_abbrs, g.home_names)
                    or _side_matches(opponent, g.away_abbrs, g.away_names)]
            cands = both or cands
        if posted_at is not None:
            # A pick is for the team's next game: allow posts up to 6h after first pitch/kickoff.
            later = [g for g in cands if g.start >= posted_at - timedelta(hours=6)]
            cands = later or cands
        return cands[0] if cands else None


GAME_COLUMNS = ["game", "game_time_et", "game_status", "venue", "event_id"]


def attach_games(df: pd.DataFrame, schedule: Schedule | None, first_day: date, last_day: date) -> pd.DataFrame:
    """Add game, game_time_et (UTC timestamp, shown in ET on export), game_status, venue and ESPN event_id."""
    df = df.copy()
    for col in GAME_COLUMNS:
        df[col] = None
    if df.empty or schedule is None:
        return df
    schedule.load(df["league"].dropna().unique(), first_day, last_day)
    rows = []
    for r in df.to_dict("records"):
        team = r.get("team") or r.get("away_team") or r.get("home_team")
        opponent = r.get("opponent") if r.get("team") else (r.get("home_team") if r.get("away_team") else None)
        posted = pd.to_datetime(r.get("created_at"), utc=True, errors="coerce")
        g = schedule.find(r.get("league"), team, opponent,
                          None if pd.isna(posted) else posted.to_pydatetime()) if team else None
        rows.append((g.short, g.start.isoformat(), g.status, g.venue, g.event_id) if g else (None,) * 5)
    df[GAME_COLUMNS] = pd.DataFrame(rows, index=df.index, columns=GAME_COLUMNS)
    return df
