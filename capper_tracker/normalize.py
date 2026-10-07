"""Turn raw LLM output into consistent, groupable picks."""
from __future__ import annotations

import re
import unicodedata

NFL_TEAMS = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU",
    "IND", "JAX", "KC", "LV", "LAC", "LAR", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI",
    "PIT", "SF", "SEA", "TB", "TEN", "WAS",
}
TEAM_ALIASES = {
    "WSH": "WAS", "JAC": "JAX", "LA": "LAR", "OAK": "LV", "LVR": "LV", "SD": "LAC", "GNB": "GB",
    "KAN": "KC", "NWE": "NE", "NOR": "NO", "SFO": "SF", "TAM": "TB", "ARZ": "ARI", "CLV": "CLE",
}

# stat -> category. Categories drive the team "threat" analysis:
#   passing + receiving -> pass game, rushing -> run game.
STAT_CATEGORY = {
    "passing_yards": "passing", "passing_tds": "passing", "passing_attempts": "passing",
    "completions": "passing", "interceptions_thrown": "passing", "longest_completion": "passing",
    "pass_rush_yards": "passing",
    "rushing_yards": "rushing", "rushing_attempts": "rushing", "longest_rush": "rushing",
    "rushing_tds": "rushing",
    "receiving_yards": "receiving", "receptions": "receiving", "longest_reception": "receiving",
    "receiving_tds": "receiving",
    "rush_rec_yards": "scrimmage",
    "anytime_td": "scoring", "first_td": "scoring", "two_plus_tds": "scoring",
    "sacks": "defense", "tackles_assists": "defense", "defensive_interceptions": "defense",
    "kicking_points": "kicking", "field_goals_made": "kicking",
    "other": "other",
}
STATS = list(STAT_CATEGORY)
BET_TYPES = ["spread", "moneyline", "total", "team_total", "player_prop", "future", "other"]
PERIODS = ["game", "1h", "2h", "1q"]
SIDES = ["over", "under", "yes", "no", "team"]

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def norm_team(value, league: str | None) -> str | None:
    if not value:
        return None
    t = str(value).strip().upper()
    if (league or "NFL").upper() != "NFL":
        return t
    t = TEAM_ALIASES.get(t, t)
    return t if t in NFL_TEAMS else None


def player_key(name: str | None) -> str | None:
    """'Ja'Marr Chase' -> 'jamarr chase'; 'Kenneth Walker III' -> 'kenneth walker'."""
    if not name:
        return None
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"['’`.]", "", s)
    s = re.sub(r"[^a-z ]+", " ", s)
    tokens = [t for t in s.split() if t not in _SUFFIXES]
    return " ".join(tokens) or None


def direction(side: str | None) -> int:
    """+1 = player/team does more (over, yes), -1 = less (under, no), 0 = not directional."""
    return {"over": 1, "yes": 1, "under": -1, "no": -1}.get(side or "", 0)


def bet_key(p: dict) -> str:
    """Identity of a bet *ignoring the exact line*, so -3 and -3.5 on the same side group together."""
    bt, period, side = p.get("bet_type"), p.get("period") or "game", p.get("side") or "?"
    if bt == "player_prop":
        return f"prop|{period}|{p.get('player_key') or '?'}|{p.get('stat') or '?'}|{side}"
    if bt in ("spread", "moneyline"):
        return f"{bt}|{period}|{p.get('team') or '?'}"
    if bt == "team_total":
        return f"team_total|{period}|{p.get('team') or '?'}|{side}"
    if bt == "total":
        game = f"{p['away_team']}@{p['home_team']}" if p.get("away_team") and p.get("home_team") \
            else (p.get("team") or "?")
        return f"total|{period}|{game}|{side}"
    return f"{bt}|{period}|{p.get('player_key') or p.get('team') or '?'}|{p.get('stat') or '?'}|{side}"


def label(p: dict) -> str:
    """Human-readable bet description without the line (lines are shown as a range in reports)."""
    period = "" if (p.get("period") or "game") == "game" else f" ({p['period'].upper()})"
    bt = p.get("bet_type")
    if bt == "player_prop":
        stat = (p.get("stat") or "?").replace("_", " ")
        return f"{p.get('player') or '?'} {p.get('side') or '?'} {stat}{period}"
    if bt == "spread":
        return f"{p.get('team') or '?'} spread{period}"
    if bt == "moneyline":
        return f"{p.get('team') or '?'} ML{period}"
    if bt == "team_total":
        return f"{p.get('team') or '?'} team total {p.get('side') or '?'}{period}"
    if bt == "total":
        game = f"{p['away_team']} @ {p['home_team']}" if p.get("away_team") and p.get("home_team") \
            else (p.get("team") or "?")
        return f"{game} total {p.get('side') or '?'}{period}"
    return f"{bt}: {p.get('player') or p.get('team') or '?'} {p.get('stat') or ''} {p.get('side') or ''}".strip()


def _num(v, cast=float):
    try:
        return cast(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def clean_pick(raw: dict) -> dict:
    """Validate/normalize one pick from the extractor and attach derived fields."""
    league = (raw.get("league") or "NFL").upper()
    p = {
        "league": league,
        "bet_type": raw.get("bet_type") if raw.get("bet_type") in BET_TYPES else "other",
        "period": raw.get("period") if raw.get("period") in PERIODS else "game",
        "player": (raw.get("player") or "").strip() or None,
        "team": norm_team(raw.get("team"), league),
        "opponent": norm_team(raw.get("opponent"), league),
        "away_team": norm_team(raw.get("away_team"), league),
        "home_team": norm_team(raw.get("home_team"), league),
        "stat": raw.get("stat") if raw.get("stat") in STAT_CATEGORY else None,
        "side": raw.get("side") if raw.get("side") in SIDES else None,
        "line": _num(raw.get("line")),
        "odds": _num(raw.get("odds"), int),
        "units": _num(raw.get("units")),
        "parlay_group": raw.get("parlay_group") or None,
        "snippet": (raw.get("snippet") or "")[:300] or None,
    }
    if p["bet_type"] == "player_prop" and p["stat"] is None:
        p["stat"] = "other"
    if p["bet_type"] in ("spread", "moneyline"):
        p["side"] = "team"
    p["player_key"] = player_key(p["player"])
    p["category"] = STAT_CATEGORY.get(p["stat"]) if p["stat"] else "game_line"
    p["direction"] = direction(p["side"])
    p["bet_key"] = bet_key(p)
    p["label"] = label(p)
    return p
