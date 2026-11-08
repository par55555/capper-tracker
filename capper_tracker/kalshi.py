"""Kalshi prices for picks: what the market thought each bet's chance was, when it was posted,
just before the game, and right now.

Kalshi's market data is public (no account or key). Every game has one key shared by all its
series, e.g. 26OCT18HOUJAC for HOU at JAC on Oct 18:
    KXNFLGAME-26OCT18HOUJAC       moneyline        "Jacksonville"            (one market per team)
    KXNFLSPREAD-26OCT18HOUJAC     spread ladder    "Jacksonville wins by over 3.5 points"
    KXNFLTOTAL-26OCT18HOUJAC      total ladder     "Over 44.5 points"
    KXNFLTD-26OCT18HOUJAC         TD scorers       "Jaylin Noel: 1+ touchdowns"
    KXNFLRECYDS-26OCT18HOUJAC     yards ladders    "Nico Collins: 75+ receiving yards"
A contract pays $1 if it happens, so its price in dollars is the market's probability.

Matching a pick (see match_pick):
  1. ESPN game -> Kalshi game key: same league, game date within a day, both teams match
     (Kalshi codes, with aliases like JAX<->JAC, or team names).
  2. Bet type / stat -> series (SERIES table), period-aware for game lines.
  3. Market within the event: player name (+ team) for props, team for sides, and the exact
     strike: "over 74.5" == "75+" (floor_strike 74.5). Under / No / underdog spreads use the No side.
  If the pick's line falls between two rungs (over 19.5 yds; Kalshi has 15+ and 25+), the
  probability is interpolated between them and flagged match="interpolated" (no buy price: there's
  no single contract at that line). Lines outside the ladder are reported as not offered.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from .config import ROOT, TZ
from .games import _norm, league_key
from .results import clean_records, norm_name

log = logging.getLogger(__name__)

BASE = "https://api.elections.kalshi.com/trade-api/v2"
LIVE_TTL = 90  # seconds to reuse prices of markets that are still trading
CACHE_DIR = ROOT / "data" / "kalshi"

# Kalshi game-series prefix per league (the GAME series lists every game with team codes).
LEAGUE_PREFIX = {"NFL": "KXNFL", "NCAAF": "KXNCAAF", "NBA": "KXNBA", "WNBA": "KXWNBA",
                 "NHL": "KXNHL", "MLB": "KXMLB"}

# (league, stat) -> Kalshi series suffixes to try, in order. Player props only.
PROP_SERIES = {
    "NFL": {
        "anytime_td": ["TD"], "two_plus_tds": ["TD"], "first_td": ["FIRSTTD"],
        "receiving_yards": ["RECYDS"], "rushing_yards": ["RSHYDS"], "passing_yards": ["PASSYDS"],
        "receptions": ["REC"], "rush_rec_yards": ["RRYDS"], "passing_tds": ["PASSTDS"],
        "longest_reception": ["LONGREC", "LONGESTREC"], "completions": ["PASSCOMP"],
        "passing_attempts": ["PASSATT"], "interceptions_thrown": ["PASSINT"],
    },
    "NBA": {"points": ["PTS"], "rebounds": ["REB"], "assists": ["AST"], "threes_made": ["3PT"],
            "pts_reb_ast": ["PRA"], "steals": ["STL"], "blocks": ["BLK"],
            "first_td": ["FIRSTBASKET"], "first_basket": ["FIRSTBASKET"]},  # posts say "First Basket"
    "WNBA": {"points": ["PTS"], "rebounds": ["REB"], "assists": ["AST"], "threes_made": ["3PT"]},
    "NHL": {"anytime_goal": ["GOAL", "ANYGOAL"], "anytime_td": ["GOAL", "ANYGOAL"], "goals": ["GOAL"],
            "hockey_assists": ["AST"], "hockey_points": ["PTS"], "shots_on_goal": ["SOG"],
            "saves": ["SAVES", "SAVE"]},
    "MLB": {"home_runs": ["HR"], "hits": ["HIT"], "total_bases": ["TB"], "rbis": ["RBI"],
            "pitcher_strikeouts": ["KS"]},
}
# Event stats that are "N+" with an implied line.
EVENT_LINE = {"anytime_td": 0.5, "two_plus_tds": 1.5, "anytime_goal": 0.5}
FIRST_SCORER = {"first_td", "first_basket"}

# (bet_type, period) -> series suffix. MLB "1H" = first five innings; NHL "1Q" = 1st period.
LINE_SERIES = {
    ("moneyline", "game"): "GAME", ("spread", "game"): "SPREAD", ("total", "game"): "TOTAL",
    ("team_total", "game"): "TEAMTOTAL",
    ("moneyline", "1h"): "1HWINNER", ("spread", "1h"): "1HSPREAD", ("total", "1h"): "1HTOTAL",
    ("team_total", "1h"): "1HTEAMTOTAL", ("total", "2h"): "2HTOTAL", ("spread", "2h"): "2HSPREAD",
    ("moneyline", "1q"): "1QWINNER", ("spread", "1q"): "1QSPREAD", ("total", "1q"): "1QTOTAL",
    ("team_total", "1q"): "1QTEAMTOTAL",
}
LEAGUE_PERIOD_SERIES = {("MLB", "1h"): {"spread": "F5SPREAD", "total": "F5TOTAL"},
                        ("NHL", "1q"): {"spread": "1PSPREAD", "total": "1PTOTAL"}}

# ESPN abbreviation -> Kalshi code where they differ, verified against this season's listings.
# Anything not listed is matched on team names ("Los Angeles C" ~ "Los Angeles Chargers").
CODE_ALIASES = {"NFL": {"JAX": "JAC"}, "MLB": {"CHW": "CWS"}}


# --- HTTP client with disk cache ----------------------------------------------

class Kalshi:
    """Public market-data client. Settled data is cached on disk forever; live data briefly."""

    def __init__(self, cache_dir: Path | None = CACHE_DIR, fetch=None, min_interval: float = 0.1):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._fetch = fetch or self._http_get
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0
        self._mem: dict[str, object] = {}
        self.requests = 0

    def _http_get(self, path: str, params: dict) -> dict:
        for attempt in range(8):
            with self._lock:  # stay well under the public rate limit
                wait = self._min_interval - (time.time() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.time()
            r = requests.get(BASE + path, params=params, timeout=20, headers={"User-Agent": "capper-tracker"})
            self.requests += 1
            if r.status_code == 429:
                time.sleep(2.0 * (attempt + 1))
                continue
            if r.status_code == 404:
                return {}
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"Kalshi rate limit: {path}")

    def _cached(self, key: str, loader, keep_forever) -> object:
        if key in self._mem:
            return self._mem[key]
        path = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if path and path.exists():
            blob = json.loads(path.read_text())
            if blob.get("final") or time.time() - blob.get("at", 0) < LIVE_TTL:
                self._mem[key] = blob["data"]
                return blob["data"]
        data = loader()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"at": time.time(), "final": bool(keep_forever(data)), "data": data}))
        self._mem[key] = data
        return data

    def quotes(self, tickers: list[str]) -> dict[str, dict]:
        """Current bid/ask/last for many markets at once (one request per 50 tickers, ~50 ms each).
        Never cached on disk: this is what the Live page polls every few seconds."""
        out = {}
        tickers = sorted({t for t in tickers if t})
        for i in range(0, len(tickers), 50):
            d = self._fetch("/markets", {"tickers": ",".join(tickers[i:i + 50]), "limit": 50})
            for m in d.get("markets") or []:
                s = _slim_market(m)
                out[s["ticker"]] = {k: s[k] for k in ("yes_bid", "yes_ask", "last", "status", "result", "volume")}
        return out

    def game_events(self, league: str, since: datetime) -> list[dict]:
        """Every game event in the league's GAME series from `since` on (newest first in the API)."""
        series = LEAGUE_PREFIX[league] + "GAME"

        def load():
            out, cursor = [], None
            for _ in range(30):
                params = {"series_ticker": series, "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                d = self._fetch("/events", params)
                evs = d.get("events") or []
                out += [{"event_ticker": e["event_ticker"], "title": e.get("title"),
                         "sub_title": e.get("sub_title")} for e in evs]
                cursor = d.get("cursor")
                dates = [_ticker_date(e["event_ticker"]) for e in evs]
                if not cursor or not evs or (all(dates) and min(dates) < since.date() - timedelta(days=3)):
                    break
            return out
        key = f"games_{league}_{since:%Y%m%d}"
        return self._cached(key, load, keep_forever=lambda d: False)

    def event(self, ticker: str) -> dict | None:
        def load():
            d = self._fetch(f"/events/{ticker}", {"with_nested_markets": "true"})
            ev = d.get("event")
            if not ev:
                return None
            ms = ev.get("markets") or d.get("markets") or []
            return {"ticker": ticker, "title": ev.get("title"), "markets": [_slim_market(m) for m in ms]}
        return self._cached(f"event_{ticker}", load,
                            keep_forever=lambda d: d is None or (d["markets"] and
                                                                 all(m["status"] in ("finalized", "settled")
                                                                     for m in d["markets"])))

    def candles(self, ticker: str, start: datetime, end: datetime, final: bool) -> list[dict]:
        series = ticker.split("-")[0]
        s, e = int(start.timestamp()), int(end.timestamp())

        def load():
            d = self._fetch(f"/series/{series}/markets/{ticker}/candlesticks",
                            {"start_ts": s, "end_ts": e, "period_interval": 60})
            return [_slim_candle(c) for c in d.get("candlesticks") or []]
        return self._cached(f"candles_{ticker}_{s}_{e}", load, keep_forever=lambda d: final)


def _ticker_date(event_ticker: str):
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", event_ticker)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)}{m.group(2)}{m.group(3)}", "%y%b%d").date()
    except ValueError:
        return None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _slim_market(m: dict) -> dict:
    return {"ticker": m["ticker"], "title": m.get("title") or "", "yes_sub_title": m.get("yes_sub_title") or "",
            "floor_strike": _f(m.get("floor_strike")), "strike_type": m.get("strike_type"),
            "status": m.get("status"), "result": m.get("result"),
            "yes_bid": _f(m.get("yes_bid_dollars")), "yes_ask": _f(m.get("yes_ask_dollars")),
            "last": _f(m.get("last_price_dollars")), "open_time": m.get("open_time"),
            "close_time": m.get("close_time"), "volume": _f(m.get("volume_fp")),
            "starts": m.get("occurrence_datetime")}


def _slim_candle(c: dict) -> dict:
    g = lambda k, f="close_dollars": _f((c.get(k) or {}).get(f))  # noqa: E731
    return {"t": c.get("end_period_ts"), "bid": g("yes_bid"), "ask": g("yes_ask"), "last": g("price"),
            "vol": _f(c.get("volume_fp"))}


# --- matching -----------------------------------------------------------------

@dataclass
class GameKey:
    key: str            # 26OCT18HOUJAC
    away: str           # Kalshi codes
    home: str
    away_name: str
    home_name: str


def _codes(sub_title: str) -> tuple[str, str] | None:
    m = re.match(r"\s*([A-Z0-9&.]+)\s+(?:vs|at|@)\s+([A-Z0-9&.]+)", sub_title or "")
    return (m.group(1), m.group(2)) if m else None


def _names(title: str) -> tuple[str, str] | None:
    t = re.sub(r"^.*?:\s*", "", title or "")          # "Game 5: Milwaukee vs Tampa Bay"
    t = re.sub(r"\s*\([A-Z][a-z]{2} \d{1,2}\)\s*$", "", t)  # "... (Oct 7)"
    parts = re.split(r"\s+(?:vs\.?|at|@)\s+", t)
    return (parts[0], parts[1]) if len(parts) == 2 else None


def _event_teams(ev: dict) -> tuple[tuple[str, str], tuple[str, str]]:
    """((away_code, home_code), (away_name, home_name)) of a GAME event. Playoff events put names,
    not codes, in the subtitle ("Las Vegas vs Golden State (Oct 7)"); codes are then "" and get
    filled from the event's market tickers by Matcher._fill_codes."""
    codes = _codes(ev.get("sub_title") or "")
    if codes and not any(c.isalpha() and c != c.upper() for c in "".join(codes)):
        sub = ev["sub_title"]
        if re.match(r"\s*[A-Z0-9&.]+\s+(?:vs|at|@)\s+[A-Z0-9&.]+\s*(\(|$)", sub):
            return codes, _names(ev.get("title")) or ("", "")
    return ("", ""), _names(ev.get("title")) or _names(ev.get("sub_title")) or ("", "")


COLLEGE = {"NCAAF", "NCAAB"}


def _canon(name) -> str:
    """Uppercase, punctuation-free; trailing "St"/"St." -> "STATE" ("Texas St." == "Texas State")."""
    n = _norm(name)
    return re.sub(r"\bST$", "STATE", n)


def _name_ok(league: str, kalshi_name: str, other: str) -> bool:
    """College: names must be equal ("Louisiana" is not "Louisiana Tech").
    Pro: Kalshi's short form may prefix the full name ("Los Angeles C" ~ "Los Angeles Chargers")."""
    k, o = _canon(kalshi_name), _canon(other)
    if not k or not o:
        return False
    if k == o:
        return True
    if league in COLLEGE:
        return False
    return len(k) > 3 and o.startswith(k)


def _team_ok(league: str, kalshi_code: str, kalshi_name: str, espn_abbr: str, espn_names: set) -> bool:
    k = (kalshi_code or "").upper()
    e = (espn_abbr or "").upper()
    if k and (k == e or CODE_ALIASES.get(league, {}).get(e) == k):
        return True
    return any(_name_ok(league, kalshi_name, n) for n in espn_names)


class Matcher:
    """Finds the Kalshi market (and side) for each pick."""

    def __init__(self, client: Kalshi, schedule, since: datetime):
        self.client = client
        self.schedule = schedule
        self.since = since
        self._keys: dict[tuple, GameKey | None] = {}
        self._by_id = {g.event_id: g for gs in schedule.games.values() for g in gs}

    def game_key(self, league: str, event_id: str) -> GameKey | None:
        ck = (league, event_id)
        if ck in self._keys:
            return self._keys[ck]
        g = self._by_id.get(str(event_id))
        found = None
        if g is not None and league in LEAGUE_PREFIX:
            espn_date = g.start.astimezone(TZ).date()
            away_abbr, home_abbr = (min(g.away_abbrs, key=len) if g.away_abbrs else ""), \
                (min(g.home_abbrs, key=len) if g.home_abbrs else "")
            prefix = LEAGUE_PREFIX[league] + "GAME-"
            cands = []  # (days apart, GameKey): playoff series have the same teams on back-to-back days
            for ev in self.client.game_events(league, self.since):
                d = _ticker_date(ev["event_ticker"])
                if d is None or abs((d - espn_date).days) > 1:
                    continue
                codes, names = _event_teams(ev)
                key = ev["event_ticker"][len(prefix):]
                if _team_ok(league, codes[0], names[0], away_abbr, g.away_names) and \
                        _team_ok(league, codes[1], names[1], home_abbr, g.home_names):
                    cands.append((abs((d - espn_date).days), GameKey(key, codes[0], codes[1], names[0], names[1])))
                elif _team_ok(league, codes[0], names[0], home_abbr, g.home_names) and \
                        _team_ok(league, codes[1], names[1], away_abbr, g.away_names):  # listed home-first
                    cands.append((abs((d - espn_date).days), GameKey(key, codes[1], codes[0], names[1], names[0])))
            if cands:
                found = min(cands, key=lambda c: c[0])[1]
        self._keys[ck] = self._fill_codes(league, found)
        return self._keys[ck]

    def _fill_codes(self, league: str, gk: GameKey | None) -> GameKey | None:
        """Read team codes off the GAME event's market tickers (…-LV, …-GS) when the listing lacked them."""
        if gk is None or (gk.away and gk.home):
            return gk
        ev = self.client.event(f"{LEAGUE_PREFIX[league]}GAME-{gk.key}") or {"markets": []}
        for m in ev["markets"]:
            code, who = m["ticker"].rsplit("-", 1)[-1], m.get("yes_sub_title") or ""
            if not gk.away and _loose_team(league, "", gk.away_name, who):
                gk.away = code
            elif not gk.home and _loose_team(league, "", gk.home_name, who):
                gk.home = code
        return gk

    def game_key_by_teams(self, league: str, teams: list, posted) -> GameKey | None:
        """Fallback when ESPN couldn't place the game: the first Kalshi game on or after the post
        (within 8 days) whose two teams include every team named on the pick."""
        teams = [t for t in teams if t]
        posted = _iso(posted)
        if not teams or posted is None or league not in LEAGUE_PREFIX:
            return None
        ck = (league, tuple(sorted(map(str, teams))), posted.date())
        if ck in self._keys:
            return self._keys[ck]
        prefix = LEAGUE_PREFIX[league] + "GAME-"
        best = None
        for ev in self.client.game_events(league, self.since):
            d = _ticker_date(ev["event_ticker"])
            if d is None or not (posted.date() - timedelta(days=1) <= d <= posted.date() + timedelta(days=8)):
                continue
            codes, names = _event_teams(ev)
            sides = [(codes[0], names[0]), (codes[1], names[1])]
            if all(any(_loose_team(league, c, n, t) for c, n in sides) for t in teams):
                if best is None or d < best[0]:
                    best = (d, GameKey(ev["event_ticker"][len(prefix):], codes[0], codes[1], names[0], names[1]))
        self._keys[ck] = self._fill_codes(league, best[1]) if best else None
        return self._keys[ck]

    def _team_side(self, league: str, gk: GameKey, event_id, team) -> tuple[str, str] | None:
        """(this team's Kalshi code, opponent's code) for a pick's team string."""
        if not team:
            return None
        g = self._by_id.get(str(event_id)) if event_id else None
        if g is not None:
            from .games import pick_side
            side = pick_side(team, (g.home_abbrs, g.home_names), (g.away_abbrs, g.away_names))
            if side:
                return (gk.home, gk.away) if side == "home" else (gk.away, gk.home)
        a, h = _loose_team(league, gk.away, gk.away_name, team), _loose_team(league, gk.home, gk.home_name, team)
        if a and not h:
            return gk.away, gk.home
        if h and not a:
            return gk.home, gk.away
        return None

    def match(self, r: dict) -> dict:
        """Kalshi market for one pick: {ticker, side ('yes'|'no'), match, note, ...} or a reason."""
        league = league_key(r.get("league"))
        if league not in LEAGUE_PREFIX:
            return _miss(f"Kalshi has no {league} markets")
        gk = self.game_key(league, r["event_id"]) if r.get("event_id") else None
        if gk is None:
            gk = self.game_key_by_teams(league, [r.get("team"), r.get("opponent"), r.get("away_team"),
                                                  r.get("home_team")], r.get("created_at"))
        if gk is None:
            return _miss("No Kalshi game for this matchup")
        bt = r.get("bet_type")
        if bt == "player_prop":
            return self._match_prop(league, gk, r)
        if bt in ("moneyline", "spread", "total", "team_total"):
            return self._match_line(league, gk, r)
        return _miss("Custom / 'other' market")

    # --- props
    def _match_prop(self, league: str, gk: GameKey, r: dict) -> dict:
        stat = r.get("stat")
        suffixes = PROP_SERIES.get(league, {}).get(stat)
        if not suffixes:
            return _miss(f"No Kalshi market for {league} '{stat}'")
        if (r.get("period") or "game") != "game":
            return _miss("Kalshi has no period player props")
        direction = r.get("direction") or 0
        line = r.get("line")
        if stat in EVENT_LINE:
            line = EVENT_LINE[stat] if line is None else line
        if stat not in FIRST_SCORER and line is None:
            return _miss("No line on the pick")
        want = norm_name(r.get("player"))
        if not want:
            return _miss("No player name")
        if " & " in str(r.get("player")) or " and " in str(r.get("player")).lower():
            return _miss("Multi-player bet")
        event_seen = False
        for suf in suffixes:
            ev = self.client.event(f"{LEAGUE_PREFIX[league]}{suf}-{gk.key}")
            if not ev or not ev["markets"]:
                continue
            event_seen = True
            mine = _player_markets(want, ev["markets"])
            if not mine:
                continue
            if stat in FIRST_SCORER:
                return _hit(mine[0], "yes" if direction >= 0 else "no", "exact")
            return _pick_rung(mine, line, direction)
        if not event_seen:
            return _miss(f"Kalshi has no {stat.replace('_', ' ')} market for this game")
        return _miss(f"{r.get('player')} not offered on Kalshi for this game")

    # --- game lines
    def _match_line(self, league: str, gk: GameKey, r: dict) -> dict:
        bt, period = r["bet_type"], (r.get("period") or "game").lower()
        suf = LEAGUE_PERIOD_SERIES.get((league, period), {}).get(bt) or LINE_SERIES.get((bt, period))
        if not suf:
            return _miss(f"No Kalshi {period.upper()} {bt} market")
        ev = self.client.event(f"{LEAGUE_PREFIX[league]}{suf}-{gk.key}")
        if not ev or not ev["markets"]:
            return _miss(f"Kalshi has no {bt} market for this game")
        ms, line, direction = ev["markets"], r.get("line"), r.get("direction") or 0

        if bt == "total":
            if line is None or not direction:
                return _miss("No line on the pick")
            return _pick_rung(ms, line, direction)

        sides = self._team_side(league, gk, r["event_id"], r.get("team"))
        if not sides:
            return _miss(f"Couldn't tell which team '{r.get('team')}' is")
        mine, theirs = sides
        if bt == "moneyline":
            m = next((m for m in ms if m["ticker"].endswith(f"-{mine}")), None)
            if m:
                return _hit(m, "yes", "exact")
            m = next((m for m in ms if m["ticker"].endswith(f"-{theirs}")), None)  # e.g. a 1H winner w/o tie
            return _hit(m, "no", "exact") if m else _miss("Team not found in Kalshi game market")
        if bt == "team_total":
            if line is None or not direction:
                return _miss("No line on the pick")
            team_ms = [m for m in ms if re.search(rf"-{re.escape(mine)}\d", m["ticker"])]
            return _pick_rung(team_ms, line, direction) if team_ms \
                else _miss("No team total for this team")
        if bt == "spread":
            if line is None:
                return _miss("No line on the pick")
            # favorite -3.5  -> "mine wins by over 3.5" YES; underdog +3.5 -> "theirs wins by over 3.5" NO
            code, side = (mine, 1) if line < 0 else (theirs, -1)
            team_ms = [m for m in ms if re.search(rf"-{re.escape(code)}\d", m["ticker"])]
            if not team_ms:
                return _miss("No spread markets for this side")
            return _pick_rung(team_ms, abs(line), side)
        return _miss("Unsupported bet type")


def _loose_team(league: str, code: str, name: str, team) -> bool:
    """A pick's free-text team ("SYRACUSE", "Milwaukee Brewers", "TB") vs a Kalshi code/name."""
    t = _norm(team)
    if not t:
        return False
    if code and (t == code.upper() or CODE_ALIASES.get(league, {}).get(t) == code.upper()):
        return True
    return _name_ok(league, name, t)


def _market_player(m: dict) -> str:
    return norm_name((m.get("title") or "").split(":")[0])


def _player_markets(want: str, markets: list[dict]) -> list[dict]:
    """Markets for one player. Exact name first; otherwise first initial + last name, but only if
    exactly one player in the game fits ("Kenneth Gainwell" -> "Kenny Gainwell", "J. Allen" -> "Josh Allen")."""
    exact = [m for m in markets if _market_player(m) == want]
    if exact:
        return exact
    w = want.split()
    if len(w) < 2:
        return []
    fits = {}
    for m in markets:
        h = _market_player(m).split()
        if len(h) >= 2 and h[-1] == w[-1] and h[0][0] == w[0][0]:
            fits.setdefault(" ".join(h), []).append(m)
    return next(iter(fits.values())) if len(fits) == 1 else []


def _pick_rung(markets: list[dict], line: float, direction: int) -> dict:
    """Over `line` == Kalshi 'greater than floor_strike'. Unders take the No side of the same rung.
    Between two rungs: interpolate. Outside the ladder: not offered."""
    rungs = sorted((m for m in markets if m.get("floor_strike") is not None), key=lambda m: m["floor_strike"])
    if not rungs:
        return _miss("Kalshi market has no lines")
    side = "yes" if direction >= 0 else "no"
    exact = [m for m in rungs if abs(m["floor_strike"] - line) < 1e-6]
    if exact:
        return _hit(exact[0], side, "exact")
    lo = [m for m in rungs if m["floor_strike"] < line]
    hi = [m for m in rungs if m["floor_strike"] > line]
    if lo and hi:
        a, b = lo[-1], hi[0]
        w = (line - a["floor_strike"]) / (b["floor_strike"] - a["floor_strike"])
        hit = _hit(a, side, "interpolated",
                   f"Estimated between Kalshi lines {a['floor_strike']:g} and {b['floor_strike']:g}")
        hit.update({"_market2": b, "_weight": w})
        return hit
    have = sorted({m["floor_strike"] for m in rungs})
    return _miss(f"Line {line:g} is outside Kalshi's ladder ({have[0]:g} to {have[-1]:g})")


def _hit(m: dict, side: str, quality: str, note: str = "") -> dict:
    return {"kalshi_ticker": m["ticker"], "kalshi_title": m["title"], "kalshi_side": side,
            "kalshi_match": quality, "kalshi_note": note, "_market": m}


def _miss(reason: str) -> dict:
    return {"kalshi_ticker": None, "kalshi_title": None, "kalshi_side": None,
            "kalshi_match": "none", "kalshi_note": reason, "_market": None}


# --- prices -------------------------------------------------------------------

def _side_prob(bid, ask, last, side: str):
    """Market probability for the pick's side: mid of bid/ask when both are real, else last trade."""
    if bid is not None and ask is not None and 0 < ask <= 1 and ask - bid <= 0.25 and ask > 0:
        p = (bid + ask) / 2
    elif last is not None:
        p = last
    else:
        return None
    return round(p if side == "yes" else 1 - p, 4)


def _side_cost(bid, ask, side: str):
    """What one contract on the pick's side cost (buy Yes at the ask; buy No at 1 - Yes bid)."""
    if side == "yes":
        return round(ask, 4) if ask is not None and 0 < ask < 1 else None
    return round(1 - bid, 4) if bid is not None and 0 < bid < 1 else None


def _iso(v) -> datetime | None:
    try:
        t = pd.to_datetime(v, utc=True)
        return None if pd.isna(t) else t.to_pydatetime()
    except (TypeError, ValueError):
        return None


def price_pick(client: Kalshi, hit: dict, posted_at, game_start) -> dict:
    """Probability / cost at post time, just before the game, and now (if still open).
    Interpolated matches blend the two neighbouring rungs' probabilities and have no cost."""
    if hit.get("_market2") is not None:
        w = hit["_weight"]
        a = _price_one(client, hit["_market"], hit["kalshi_side"], posted_at, game_start)
        b = _price_one(client, hit["_market2"], hit["kalshi_side"], posted_at, game_start)
        mix = lambda x, y: round(x * (1 - w) + y * w, 4) if x is not None and y is not None else None  # noqa: E731
        return {"kalshi_prob_post": mix(a["kalshi_prob_post"], b["kalshi_prob_post"]), "kalshi_cost_post": None,
                "kalshi_prob_close": mix(a["kalshi_prob_close"], b["kalshi_prob_close"]),
                "kalshi_prob_now": mix(a["kalshi_prob_now"], b["kalshi_prob_now"]), "kalshi_cost_now": None,
                "kalshi_opened_late": a["kalshi_opened_late"] or b["kalshi_opened_late"]}
    return _price_one(client, hit.get("_market"), hit.get("kalshi_side"), posted_at, game_start)


def _price_one(client: Kalshi, m: dict | None, side: str | None, posted_at, game_start) -> dict:
    out = {"kalshi_prob_post": None, "kalshi_cost_post": None, "kalshi_prob_close": None,
           "kalshi_prob_now": None, "kalshi_cost_now": None, "kalshi_opened_late": False}
    if not m:
        return out
    posted, start = _iso(posted_at), _iso(game_start) or _iso(m.get("starts"))
    final = m["status"] in ("finalized", "settled", "closed")
    if m["status"] in ("active", "open", "initialized"):
        out["kalshi_prob_now"] = _side_prob(m["yes_bid"], m["yes_ask"], m["last"], side)
        out["kalshi_cost_now"] = _side_cost(m["yes_bid"], m["yes_ask"], side)
    if posted is None:
        return out
    end = start or (posted + timedelta(days=2))
    now = datetime.now(timezone.utc)
    end = min(end, now)
    if end <= posted - timedelta(hours=1):
        return out
    # Round the window to whole hours so repeated live refreshes hit the cache.
    c_start = (posted - timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
    c_end = (end + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    candles = client.candles(m["ticker"], c_start, c_end, final=final)
    if not candles:
        return out
    pt = posted.timestamp()
    before = [c for c in candles if c["t"] and c["t"] <= pt + 60]
    c0 = before[-1] if before else candles[0]
    out["kalshi_opened_late"] = not before
    out["kalshi_prob_post"] = _side_prob(c0["bid"], c0["ask"], c0["last"], side)
    out["kalshi_cost_post"] = _side_cost(c0["bid"], c0["ask"], side)
    if start is not None and start <= now:
        pre = [c for c in candles if c["t"] and c["t"] <= start.timestamp()]
        if pre:
            out["kalshi_prob_close"] = _side_prob(pre[-1]["bid"], pre[-1]["ask"], pre[-1]["last"], side)
    return out


KALSHI_COLUMNS = ["kalshi_ticker", "kalshi_title", "kalshi_side", "kalshi_match", "kalshi_note",
                  "kalshi_prob_post", "kalshi_cost_post", "kalshi_prob_close", "kalshi_prob_now",
                  "kalshi_cost_now", "kalshi_opened_late", "kalshi_result", "kalshi_ticker2", "kalshi_weight"]


def _settled(hit: dict) -> str | None:
    """Won/Lost for the pick's side from Kalshi's settlement (exact matches only)."""
    m = hit.get("_market")
    if not m or hit.get("kalshi_match") != "exact" or m.get("result") not in ("yes", "no"):
        return None
    return "Won" if (m["result"] == "yes") == (hit["kalshi_side"] == "yes") else "Lost"


def attach_prices(df: pd.DataFrame, schedule, client: Kalshi | None = None, since: datetime | None = None,
                  workers: int = 6) -> pd.DataFrame:
    """Add Kalshi match + price columns to picks (output of analyze.prepare + games.attach_games)."""
    df = df.copy()
    if df.empty:
        for c in KALSHI_COLUMNS:
            df[c] = None
        return df
    client = client or Kalshi()
    if since is None:
        ts = pd.to_datetime(df["created_at"], utc=True, errors="coerce").min()
        since = (ts.to_pydatetime() if pd.notna(ts) else datetime.now(timezone.utc)) - timedelta(days=3)
    matcher = Matcher(client, schedule, since)
    recs = clean_records(df)
    # Resolve game keys first (one listing per league), then fetch events/candles in parallel.
    for r in recs:
        lg = league_key(r.get("league"))
        if r.get("event_id") and lg in LEAGUE_PREFIX:
            matcher.game_key(lg, r["event_id"])

    def work(r):
        try:
            hit = matcher.match(r)
            return {**{k: v for k, v in hit.items() if not k.startswith("_")},
                    **price_pick(client, hit, r.get("created_at"), r.get("game_time_et")),
                    "kalshi_result": _settled(hit),
                    "kalshi_ticker2": (hit.get("_market2") or {}).get("ticker"),
                    "kalshi_weight": hit.get("_weight")}
        except Exception as e:  # one bad lookup never breaks the page
            log.warning("Kalshi lookup failed for %s: %s", r.get("label"), e)
            return {**_miss(f"Kalshi lookup failed: {e}"), "_market": None}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(work, recs))
    for c in KALSHI_COLUMNS:
        df[c] = [row.get(c) for row in rows]
    df["kalshi_move"] = [(round(c - p, 4) if c is not None and p is not None else None)
                         for p, c in zip(df["kalshi_prob_post"], df["kalshi_prob_close"])]
    return df


def bet_summary(g: pd.DataFrame) -> pd.DataFrame:
    """One row per bet_key: the Kalshi market most of its accounts' lines map to, the price at the
    first post, now, and just before the game, plus the average price accounts got."""
    cols = ["bet_key", "kalshi_title", "kalshi_match", "kalshi_first", "kalshi_now", "kalshi_close",
            "kalshi_avg_post", "kalshi_priced", "kalshi_ticker", "kalshi_side", "kalshi_ticker2", "kalshi_weight",
            "kalshi_cost_now"]
    if g.empty or "kalshi_ticker" not in g:
        return pd.DataFrame(columns=cols)
    p = g[g["kalshi_ticker"].notna()]
    rows = []
    for key, grp in p.groupby("bet_key"):
        top = grp["kalshi_ticker"].mode().iat[0]
        main = grp[grp["kalshi_ticker"] == top].sort_values("created_at")
        first = grp.sort_values("created_at")
        first = first[first["kalshi_prob_post"].notna()]
        rows.append({
            "bet_key": key,
            "kalshi_title": main["kalshi_title"].iat[0],
            "kalshi_match": "exact" if main["kalshi_match"].eq("exact").any() else main["kalshi_match"].iat[0],
            "kalshi_first": first["kalshi_prob_post"].iat[0] if not first.empty else None,
            "kalshi_now": main["kalshi_prob_now"].dropna().iat[-1] if main["kalshi_prob_now"].notna().any() else None,
            "kalshi_close": main["kalshi_prob_close"].dropna().iat[-1] if main["kalshi_prob_close"].notna().any() else None,
            "kalshi_avg_post": round(float(grp["kalshi_prob_post"].mean()), 4) if grp["kalshi_prob_post"].notna().any() else None,
            "kalshi_priced": int(grp["kalshi_prob_post"].notna().sum()),
            # the market to stream live prices from (and its pair, for lines estimated between two)
            "kalshi_ticker": top, "kalshi_side": main["kalshi_side"].iat[0],
            "kalshi_ticker2": main["kalshi_ticker2"].iat[0] if "kalshi_ticker2" in main else None,
            "kalshi_weight": main["kalshi_weight"].iat[0] if "kalshi_weight" in main else None,
            "kalshi_cost_now": main["kalshi_cost_now"].dropna().iat[-1] if main["kalshi_cost_now"].notna().any() else None,
        })
    return pd.DataFrame(rows, columns=cols)
