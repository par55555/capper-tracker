"""Grade picks against what actually happened, using ESPN's public game summaries (box scores).

Graded today:
- Game lines in every league ESPN covers: moneyline, spread, game total, team total
  (full game, first half, first quarter/period, second half).
- Football player props (NFL and college): yards, receptions, attempts, completions, TDs, longest
  catch/run, anytime / 2+ / first TD scorer, sacks, tackles, kicking.

Everything else (other sports' player props, period props, "other" markets) is marked Ungraded with
the reason, so it's visible rather than silently dropped. Box scores of finished games never
change, so they're cached on disk under data/boxscores/.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import requests

from .config import ROOT
from .games import ESPN_PATHS, _side_matches, _team_keys, league_key

log = logging.getLogger(__name__)

SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/{path}/summary"
CACHE_DIR = ROOT / "data" / "boxscores"
FOOTBALL = {"NFL", "NCAAF"}
GAME_LINES = {"moneyline", "spread", "total", "team_total"}

WON, LOST, PUSH, PENDING, UNGRADED = "Won", "Lost", "Push", "Pending", "Ungraded"


# --- box scores -------------------------------------------------------------

@dataclass
class Side:
    abbrs: set
    names: set
    abbr: str
    score: float
    periods: list[float]


@dataclass
class BoxScore:
    league: str
    event_id: str
    final: bool
    home: Side
    away: Side
    players: dict[str, dict[str, float]] = field(default_factory=dict)  # norm name -> stats
    display: dict[str, str] = field(default_factory=dict)               # norm name -> "Ja'Marr Chase"
    td_scorers: list[str] = field(default_factory=list)                 # norm names, in order

    def side_of(self, team) -> tuple[Side, Side] | None:
        """(team's side, opponent's side) for a team string, or None if it isn't in this game."""
        if _side_matches(team, self.home.abbrs, self.home.names):
            return self.home, self.away
        if _side_matches(team, self.away.abbrs, self.away.names):
            return self.away, self.home
        return None


_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def norm_name(name) -> str:
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    toks = [t for t in re.sub(r"[^a-z ]", "", s.replace("-", " ").replace(".", " ")).split()
            if t not in _SUFFIXES]
    return " ".join(toks)


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


# ESPN box-score key -> our stat name. "a/b" keys are split into two stats.
_FOOTBALL_KEYS = {
    "passingYards": "passing_yards", "passingTouchdowns": "passing_tds",
    "interceptions@passing": "interceptions_thrown",
    "rushingAttempts": "rushing_attempts", "rushingYards": "rushing_yards",
    "rushingTouchdowns": "rushing_tds", "longRushing": "longest_rush",
    "receptions": "receptions", "receivingYards": "receiving_yards",
    "receivingTouchdowns": "receiving_tds", "longReception": "longest_reception",
    "totalTackles": "tackles_assists", "sacks@defensive": "sacks",
    "interceptions@interceptions": "defensive_interceptions",
    "totalKickingPoints": "kicking_points",
}
_FOOTBALL_SPLIT = {"completions/passingAttempts": ("completions", "passing_attempts"),
                   "fieldGoalsMade/fieldGoalAttempts": ("field_goals_made", None)}
_SCORER = re.compile(r"^(.+?)\s+\d+\s*(?:yd|yard)", re.I)


def _side(c: dict) -> Side:
    abbrs, names = _team_keys(c.get("team") or {})
    periods = [_num(ls.get("value", ls.get("displayValue"))) or 0.0 for ls in c.get("linescores") or []]
    return Side(abbrs, names, (c.get("team") or {}).get("abbreviation", ""), _num(c.get("score")) or 0.0, periods)


def parse_summary(data: dict, league: str, event_id: str) -> BoxScore:
    comp = data["header"]["competitions"][0]
    sides = {c["homeAway"]: _side(c) for c in comp["competitors"]}
    final = bool(((comp.get("status") or {}).get("type") or {}).get("completed"))
    box = BoxScore(league, event_id, final, sides["home"], sides["away"])
    if league not in FOOTBALL:
        return box

    for team in (data.get("boxscore") or {}).get("players") or []:
        for cat in team.get("statistics") or []:
            keys = cat.get("keys") or []
            for a in cat.get("athletes") or []:
                name = (a.get("athlete") or {}).get("displayName")
                key = norm_name(name)
                if not key:
                    continue
                box.display.setdefault(key, name)
                stats = box.players.setdefault(key, {})
                for k, v in zip(keys, a.get("stats") or []):
                    if k in _FOOTBALL_SPLIT:
                        parts = str(v).split("/")
                        for stat, part in zip(_FOOTBALL_SPLIT[k], parts):
                            if stat and _num(part) is not None:
                                stats[stat] = _num(part)
                        continue
                    stat = _FOOTBALL_KEYS.get(f"{k}@{cat.get('name')}") or _FOOTBALL_KEYS.get(k)
                    if stat and _num(v) is not None:
                        stats[stat] = _num(v)

    for play in data.get("scoringPlays") or []:
        kind = ((play.get("scoringType") or {}).get("name") or "").lower()
        if kind != "touchdown":
            continue
        m = _SCORER.match(play.get("text") or "")
        if m:
            key = norm_name(m.group(1))
            box.td_scorers.append(key)
            box.display.setdefault(key, m.group(1).strip())
    return box


class BoxScores:
    """Fetches and caches ESPN game summaries. Finished games are cached on disk forever."""

    def __init__(self, fetch=None, cache_dir: Path | None = CACHE_DIR):
        self._fetch = fetch or self._http_get
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._mem: dict[tuple, BoxScore | None] = {}

    @staticmethod
    def _http_get(url: str, params: dict) -> dict:
        r = requests.get(url, params=params, timeout=20, headers={"User-Agent": "capper-tracker"})
        r.raise_for_status()
        return r.json()

    def get(self, league: str, event_id: str) -> BoxScore | None:
        league = league_key(league)
        key = (league, str(event_id))
        if key in self._mem:
            return self._mem[key]
        box = None
        path = self.cache_dir / f"{league}_{event_id}.json" if self.cache_dir else None
        try:
            if path and path.exists():
                data = json.loads(path.read_text())
            else:
                data = self._fetch(SUMMARY_URL.format(path=ESPN_PATHS[league]), {"event": event_id})
            box = parse_summary(data, league, str(event_id))
            if path and box.final and not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data))
        except Exception as e:  # never let one game break the page
            log.warning("ESPN box score %s %s unavailable: %s", league, event_id, e)
        self._mem[key] = box
        return box

    def prefetch(self, pairs, workers: int = 8) -> None:
        todo = [p for p in set(pairs) if (league_key(p[0]), str(p[1])) not in self._mem]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda p: self.get(*p), todo))


# --- grading ----------------------------------------------------------------

def _half_periods(league: str) -> int | None:
    return {"NCAAB": 1, "MLB": 5, "NHL": None}.get(league, 2)  # MLB "1H" = first five innings


def _period_score(side: Side, period: str, league: str) -> float | None:
    p = (period or "game").lower()
    if p == "game":
        return side.score
    if p == "1q":
        return side.periods[0] if side.periods else None
    n = _half_periods(league)
    if n is None or len(side.periods) < n:
        return None
    first = sum(side.periods[:n])
    return first if p == "1h" else side.score - first if p == "2h" else None


def _over_under(value: float, line: float, direction: int) -> str:
    diff = (value - line) * (1 if direction >= 0 else -1)
    return WON if diff > 0 else LOST if diff < 0 else PUSH


def _match_player(box: BoxScore, player) -> str | None:
    key = norm_name(player)
    pool = set(box.players) | set(box.td_scorers)
    if key in pool:
        return key
    toks = key.split()
    if not toks:
        return None
    first, last = toks[0][0], toks[-1]
    cands = [k for k in pool if k.split() and k.split()[-1] == last and k[0] == first]
    return cands[0] if len(cands) == 1 else None


def _fmt(v: float) -> str:
    return f"{v:g}"


@dataclass
class Grade:
    result: str
    actual: float | None = None
    text: str = ""


def grade_game_line(r: dict, box: BoxScore) -> Grade:
    bt, period, league = r.get("bet_type"), r.get("period") or "game", box.league
    tag = "" if period == "game" else f" ({period.upper()})"
    home, away = _period_score(box.home, period, league), _period_score(box.away, period, league)
    if home is None or away is None:
        return Grade(UNGRADED, text=f"No {period.upper()} score for {league}")
    score_text = f"{box.away.abbr} {_fmt(away)}, {box.home.abbr} {_fmt(home)}{tag}"
    line, direction = r.get("line"), r.get("direction") or 0

    if bt == "total":
        if line is None or not direction:
            return Grade(UNGRADED, text="No total line")
        total = home + away
        return Grade(_over_under(total, line, direction), total, f"{_fmt(total)} total · {score_text}")

    team = r.get("team")
    sides = box.side_of(team) if team else None
    if not sides:
        return Grade(UNGRADED, text=f"Couldn't find {team or 'the team'} in {box.away.abbr} @ {box.home.abbr}")
    mine, theirs = sides
    ms = _period_score(mine, period, league)
    ts = _period_score(theirs, period, league)

    if bt == "moneyline":
        res = WON if ms > ts else LOST if ms < ts else PUSH
        return Grade(res, ms - ts, score_text)
    if bt == "spread":
        if line is None:
            return Grade(UNGRADED, text="No spread line")
        margin = ms - ts
        res = WON if margin + line > 0 else LOST if margin + line < 0 else PUSH
        return Grade(res, margin, f"{mine.abbr} by {_fmt(margin)}" if margin >= 0 else f"{mine.abbr} lost by {_fmt(-margin)}")
    if bt == "team_total":
        if (r.get("stat") or "points") not in ("points", "runs", "goals") or line is None or not direction:
            return Grade(UNGRADED, text="Team total on a stat other than points")
        return Grade(_over_under(ms, line, direction), ms, f"{mine.abbr} scored {_fmt(ms)}{tag}")
    return Grade(UNGRADED, text="Unsupported bet type")


_STAT_TEXT = {
    "passing_yards": "pass yds", "passing_tds": "pass TDs", "interceptions_thrown": "INTs thrown",
    "rushing_attempts": "rush att", "rushing_yards": "rush yds", "rushing_tds": "rush TDs",
    "longest_rush": "long rush", "receptions": "rec", "receiving_yards": "rec yds",
    "receiving_tds": "rec TDs", "longest_reception": "long rec", "tackles_assists": "tackles",
    "sacks": "sacks", "defensive_interceptions": "INTs", "kicking_points": "kicking pts",
    "completions": "completions", "passing_attempts": "pass att", "field_goals_made": "FGs",
    "rush_rec_yards": "rush+rec yds", "pass_rush_yards": "pass+rush yds",
}
_COMBOS = {"rush_rec_yards": ("rushing_yards", "receiving_yards"),
           "pass_rush_yards": ("passing_yards", "rushing_yards")}


def grade_football_prop(r: dict, box: BoxScore) -> Grade:
    stat, line, direction = r.get("stat"), r.get("line"), r.get("direction") or 0
    if (r.get("period") or "game") != "game":
        return Grade(UNGRADED, text="Period props aren't in the box score")
    key = _match_player(box, r.get("player"))
    if key is None:
        return Grade(UNGRADED, text=f"{r.get('player')}: no stats in this game (inactive or name mismatch)")
    tds = box.td_scorers.count(key)

    if stat in ("anytime_td", "two_plus_tds"):
        need = 2 if stat == "two_plus_tds" else 1
        threshold = line + 0.5 if line is not None else need  # "over 1.5 TDs" == 2+
        hit = tds >= threshold
        res = (WON if hit else LOST) if direction >= 0 else (LOST if hit else WON)
        return Grade(res, tds, f"{tds} TD{'s' if tds != 1 else ''}" if tds else "No TD")
    if stat == "first_td":
        if not box.td_scorers:
            return Grade(LOST if direction >= 0 else WON, 0, "No touchdowns scored")
        first = box.td_scorers[0]
        hit = first == key
        res = (WON if hit else LOST) if direction >= 0 else (LOST if hit else WON)
        return Grade(res, 1 if hit else 0, "Scored first TD" if hit else f"First TD: {box.display.get(first, first)}")

    if stat not in _STAT_TEXT:
        return Grade(UNGRADED, text=f"'{stat}' isn't graded yet")
    if line is None or not direction:
        return Grade(UNGRADED, text="No line to grade against")
    stats = box.players.get(key, {})
    if stat in _COMBOS:
        value = sum(stats.get(s, 0.0) for s in _COMBOS[stat])
    else:
        value = stats.get(stat, 0.0)  # played but no entry in that category = 0
    return Grade(_over_under(value, line, direction), value, f"{_fmt(value)} {_STAT_TEXT[stat]}")


def gradeable(r: dict) -> str | None:
    """Why a pick can't be graded (before fetching anything), or None if it can."""
    league = league_key(r.get("league"))
    bt = r.get("bet_type")
    if bt == "player_prop" and league not in FOOTBALL:
        return f"{league} player props aren't graded yet"
    if bt not in GAME_LINES and bt != "player_prop":
        return "Custom / 'other' market"
    if league not in ESPN_PATHS:
        return f"{league} isn't covered by ESPN feeds"
    if not r.get("event_id"):
        return "Game not matched to a schedule"
    return None


def grade_pick(r: dict, boxes: BoxScores) -> Grade:
    why = gradeable(r)
    if why:
        return Grade(UNGRADED, text=why)
    if r.get("game_status") != "Final":
        return Grade(PENDING, text=r.get("game_status") or "Not started")
    box = boxes.get(r["league"], r["event_id"])
    if box is None:
        return Grade(UNGRADED, text="Box score unavailable")
    if not box.final:
        return Grade(PENDING, text="Not final yet")
    if r.get("bet_type") == "player_prop":
        return grade_football_prop(r, box)
    return grade_game_line(r, box)


def clean_records(df: pd.DataFrame) -> list[dict]:
    """Rows as dicts with NaN -> None: pandas stores missing values as NaN, which slips past
    "is None" checks and fails every comparison."""
    return [{k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()}
            for r in df.to_dict("records")]


def grade(df: pd.DataFrame, boxes: BoxScores | None = None) -> pd.DataFrame:
    """Add result, actual, actual_text to each pick (one row per account per bet, from analyze.prepare)."""
    df = df.copy()
    if df.empty:
        for c in ("result", "actual", "actual_text"):
            df[c] = None
        return df
    boxes = boxes or BoxScores()
    records = clean_records(df)
    boxes.prefetch((r["league"], r["event_id"]) for r in records
                   if gradeable(r) is None and r.get("game_status") == "Final")
    grades = [grade_pick(r, boxes) for r in records]
    df["result"] = [g.result for g in grades]
    df["actual"] = [g.actual for g in grades]
    df["actual_text"] = [g.text for g in grades]
    return df


# --- summaries --------------------------------------------------------------

def _record(results: pd.Series) -> dict:
    won, lost, push = (int((results == x).sum()) for x in (WON, LOST, PUSH))
    return {"won": won, "lost": lost, "push": push,
            "hit_rate": round(won / (won + lost), 4) if won + lost else None}


def _bet_result(results: pd.Series) -> str:
    settled = set(results) & {WON, LOST, PUSH}
    if len(settled) > 1:
        return "Split"  # accounts had different lines and landed on both sides of the result
    if settled:
        return settled.pop()
    return PENDING if (results == PENDING).any() else UNGRADED


def _join(values) -> str:
    return ", ".join(sorted({str(v) for v in values if pd.notna(v)}))


def bet_results(g: pd.DataFrame) -> pd.DataFrame:
    """One row per bet (grouped like All Bets), with what happened."""
    if g.empty:
        return pd.DataFrame()
    rows = []
    for key, grp in g.groupby("bet_key"):
        first = grp.iloc[0]
        lo, hi = grp["line"].min(), grp["line"].max()
        settled = grp[grp["result"].isin([WON, LOST, PUSH])]
        rows.append({
            "league": first["league"], "game": first.get("game"), "game_time_et": first.get("game_time_et"),
            "bet": first["label"], "bet_type": first["bet_type"], "category": first.get("category"),
            "accounts": grp["handle"].nunique(), "who": _join(grp["handle"]),
            "lines": "" if pd.isna(lo) else f"{lo:g}" if lo == hi else f"{lo:g} to {hi:g}",
            "result": _bet_result(grp["result"]),
            "actual": (settled if not settled.empty else grp)["actual_text"].mode().iat[0]
                      if grp["actual_text"].notna().any() else "",
            "who_won": _join(grp.loc[grp["result"] == WON, "handle"]),
            "who_lost": _join(grp.loc[grp["result"] == LOST, "handle"]),
            "bet_key": key,
        })
    out = pd.DataFrame(rows)
    order = {WON: 0, LOST: 0, PUSH: 0, "Split": 0, PENDING: 1, UNGRADED: 2}
    out["_o"] = out["result"].map(order)
    return (out.sort_values(["_o", "accounts", "game_time_et"], ascending=[True, False, True], na_position="last")
               .drop(columns="_o").reset_index(drop=True))


def by_agreement(bets: pd.DataFrame) -> pd.DataFrame:
    """Hit rate of bets by how many accounts shared them: does consensus beat a lone pick?"""
    if bets.empty:
        return pd.DataFrame()
    tiers = bets.assign(tier=bets["accounts"].clip(upper=4).map({1: "1 account", 2: "2 accounts",
                                                                   3: "3 accounts", 4: "4+ accounts"}))
    rows = []
    for tier, grp in tiers.groupby("tier"):
        rec = _record(grp["result"])
        rows.append({"tier": tier, "min_accounts": int(grp["accounts"].min()), "bets": len(grp),
                     **rec, "split": int((grp["result"] == "Split").sum()),
                     "pending": int((grp["result"] == PENDING).sum()),
                     "ungraded": int((grp["result"] == UNGRADED).sum())})
    return pd.DataFrame(rows).sort_values("min_accounts").drop(columns="min_accounts").reset_index(drop=True)


def capper_records(g: pd.DataFrame, bets: pd.DataFrame) -> pd.DataFrame:
    """Each account's record on its graded picks (parlay legs graded as individual legs)."""
    if g.empty:
        return pd.DataFrame()
    shared = set(bets.loc[bets["accounts"] >= 2, "bet_key"])
    rows = []
    for handle, grp in g.groupby("handle"):
        rec = _record(grp["result"])
        srec = _record(grp.loc[grp["bet_key"].isin(shared), "result"])
        straight = grp[grp["parlay_group"].isna()] if "parlay_group" in grp else grp
        rows.append({"handle": handle, "picks": len(grp), **rec,
                     "pending": int((grp["result"] == PENDING).sum()),
                     "ungraded": int((grp["result"] == UNGRADED).sum()),
                     "straight_record": "{won}-{lost}-{push}".format(**_record(straight["result"])),
                     "shared_won": srec["won"], "shared_lost": srec["lost"]})
    out = pd.DataFrame(rows)
    out["_graded"] = out["won"] + out["lost"]
    return (out.sort_values(["hit_rate", "_graded"], ascending=False, na_position="last")
               .drop(columns="_graded").reset_index(drop=True))


def results_report(g: pd.DataFrame) -> dict:
    bets = bet_results(g)
    shared = bets[bets["accounts"] >= 2] if not bets.empty else bets
    pick_cols = ["handle", "created_at", "league", "game", "game_time_et", "label", "line", "odds",
                 "parlay_group", "result", "actual_text", "snippet", "url"]
    return {
        "summary": {
            "shared": {**_record(shared["result"] if not shared.empty else pd.Series(dtype=str)),
                       "bets": len(shared)},
            "all_picks": {**_record(g["result"] if not g.empty else pd.Series(dtype=str)), "picks": len(g)},
            "pending": int((g["result"] == PENDING).sum()) if not g.empty else 0,
            "ungraded": int((g["result"] == UNGRADED).sum()) if not g.empty else 0,
        },
        "frames": {
            "Bet Results": bets,
            "By Agreement": by_agreement(bets),
            "Capper Records": capper_records(g, bets),
            "Pick Results": g[[c for c in pick_cols if c in g.columns]] if not g.empty else g,
        },
    }
