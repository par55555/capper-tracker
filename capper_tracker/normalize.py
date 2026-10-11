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

# stat -> category. Categories drive the NFL team "threat" analysis
# (passing + receiving -> pass game, rushing -> run game); other sports just get a readable group.
STAT_CATEGORY = {
    # NFL / college football
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
    # MLB
    "home_runs": "batting", "hits": "batting", "singles": "batting", "doubles": "batting",
    "total_bases": "batting", "rbis": "batting", "runs": "batting", "hits_runs_rbis": "batting",
    "stolen_bases": "batting", "walks": "batting",
    "pitcher_strikeouts": "pitching", "outs_recorded": "pitching", "earned_runs_allowed": "pitching",
    "hits_allowed": "pitching",
    # NBA / WNBA / college basketball
    "points": "scoring", "rebounds": "rebounding", "assists": "playmaking",
    "threes_made": "shooting", "pts_reb_ast": "combo", "pts_reb": "combo", "pts_ast": "combo",
    "reb_ast": "combo", "steals": "defense", "blocks": "defense", "double_double": "combo",
    # NHL
    "goals": "scoring", "hockey_assists": "playmaking", "hockey_points": "scoring",
    "shots_on_goal": "shooting", "saves": "goaltending", "anytime_goal": "scoring",
    "other": "other",
}

# How each stat reads in the report.
STAT_LABEL = {
    "passing_tds": "passing TDs", "interceptions_thrown": "interceptions thrown",
    "pass_rush_yards": "passing + rushing yards", "rushing_tds": "rushing TDs",
    "receiving_tds": "receiving TDs", "rush_rec_yards": "rushing + receiving yards",
    "anytime_td": "anytime touchdown", "first_td": "first touchdown", "two_plus_tds": "2+ touchdowns",
    "tackles_assists": "tackles + assists", "defensive_interceptions": "interceptions",
    "field_goals_made": "field goals made",
    "home_runs": "home runs", "rbis": "RBIs", "hits_runs_rbis": "hits + runs + RBIs",
    "total_bases": "total bases", "stolen_bases": "stolen bases",
    "pitcher_strikeouts": "strikeouts (pitcher)", "outs_recorded": "outs recorded",
    "earned_runs_allowed": "earned runs allowed", "hits_allowed": "hits allowed",
    "threes_made": "3-pointers made", "pts_reb_ast": "points + rebounds + assists",
    "pts_reb": "points + rebounds", "pts_ast": "points + assists", "reb_ast": "rebounds + assists",
    "double_double": "double-double",
    "hockey_assists": "assists", "hockey_points": "points (goals + assists)",
    "shots_on_goal": "shots on goal", "anytime_goal": "anytime goal",
}
# Yes/no events that have no count line.
EVENT_STATS = {"anytime_td", "first_td", "two_plus_tds", "anytime_goal", "double_double"}

STATS = list(STAT_CATEGORY)
BET_TYPES = ["spread", "moneyline", "total", "team_total", "player_prop", "future", "other"]
PERIODS = ["game", "1h", "2h", "1q"]
SIDES = ["over", "under", "yes", "no", "team"]

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


# Recognize the market from the post text when the extractor didn't name it (older picks stored
# as "other"). Ordered: more specific phrases first. Each rule: (leagues or None for any, regex, stat).
_STAT_RULES = [
    (None, r"hits?\s*\+\s*runs?\s*\+\s*rbis?|\bh\s*\+\s*r\s*\+\s*rbi", "hits_runs_rbis"),
    (None, r"total bases|\btb\b", "total_bases"),
    (None, r"home ?runs?|homers?|\bhrs?\b|to homer", "home_runs"),
    (None, r"stolen bases?|\bsbs?\b", "stolen_bases"),
    (None, r"\bdoubles?\b", "doubles"),
    (None, r"\bsingles?\b", "singles"),
    (None, r"\brbis?\b", "rbis"),
    ({"MLB"}, r"strike ?outs?|\bks\b", "pitcher_strikeouts"),
    ({"MLB"}, r"outs recorded", "outs_recorded"),
    ({"MLB"}, r"\bhits\b|\bto record (a|\d+\+?) hits?\b", "hits"),
    ({"MLB"}, r"\bruns? scored\b|\bto score a run\b", "runs"),
    (None, r"points?\s*\+\s*rebounds?\s*\+\s*assists?|\bpra\b", "pts_reb_ast"),
    (None, r"points?\s*\+\s*rebounds?|\bp\+r\b", "pts_reb"),
    (None, r"points?\s*\+\s*assists?|\bp\+a\b", "pts_ast"),
    (None, r"rebounds?\s*\+\s*assists?|\br\+a\b", "reb_ast"),
    (None, r"made threes|threes made|3-?pointers?|\b3pm\b|\bthrees\b", "threes_made"),
    (None, r"double[- ]double", "double_double"),
    (None, r"rebounds?|\breb\b|\brebs\b", "rebounds"),
    (None, r"shots on goal|\bsogs?\b", "shots_on_goal"),
    ({"NHL"}, r"goal ?scorer|anytime goal|to score a goal", "anytime_goal"),
    ({"NHL"}, r"\bgoals?\b", "goals"),
    ({"NHL"}, r"\bsaves?\b", "saves"),
    ({"NHL"}, r"assists?", "hockey_assists"),
    ({"NHL"}, r"\bpoints?\b|\bpts\b", "hockey_points"),
    (None, r"assists?|\bast\b", "assists"),
    (None, r"steals?", "steals"),
    (None, r"blocks?", "blocks"),
    ({"NFL", "NCAAF"}, r"anytime (td|touchdown)|\batd\b|to score a (td|touchdown)", "anytime_td"),
    (None, r"\bpoints?\b|\bpts\b", "points"),
]
_FILLER = (r"\b(player|players|to|record|score|hit|over|under|total|o|u|a|an|the|or more|and|more|yes|no|"
           r"straight|parlay|leg|lock|play|bet|units?)\b")


def infer_stat(league, snippet, player=None) -> tuple[str, str]:
    """(stat, stat_detail) from the post text. Falls back to stat 'other' with the market in words."""
    lg = (league or "NFL").upper() if isinstance(league, str) else "NFL"
    text = snippet.lower() if isinstance(snippet, str) else ""
    for leagues, pattern, stat in _STAT_RULES:
        if (leagues is None or lg in leagues) and re.search(pattern, text):
            return stat, STAT_LABEL.get(stat, stat.replace("_", " "))
    # Nothing recognized: keep the market wording from the post, minus the player's name and numbers.
    words = text
    if isinstance(player, str) and player:
        for part in re.split(r"\s*&\s*|\s+and\s+", player.lower()):
            words = words.replace(part, " ")
    words = re.sub(r"[-+]?\d+(\.\d+)?\+?|[^a-z ]", " ", words)
    words = re.sub(_FILLER, " ", words)
    words = re.sub(r"\s+", " ", words).strip()
    return "other", (words[:60] if len(words) >= 3 else "unrecognized market (see snippet)")


def _slug(text) -> str:
    text = text if isinstance(text, str) else ""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "unknown"


def stat_name(stat: str | None, stat_detail: str | None = None) -> str:
    """Readable stat. For stat 'other' the extractor's own description is used, so it's never just 'other'."""
    if not isinstance(stat, str) or not stat:  # NaN/None from pandas
        return ""
    if stat == "other":
        detail = (stat_detail if isinstance(stat_detail, str) and stat_detail.strip()
                  else "unrecognized market (see snippet)")
        return detail.strip().lower()
    return STAT_LABEL.get(stat, stat.replace("_", " "))


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
        return f"prop|{period}|{p.get('player_key') or '?'}|{_stat_key(p)}|{side}"
    if bt in ("spread", "moneyline"):
        return f"{bt}|{period}|{p.get('team') or '?'}"
    if bt == "team_total":
        return f"team_total|{period}|{p.get('team') or '?'}|{side}"
    if bt == "total":
        game = f"{p['away_team']}@{p['home_team']}" if p.get("away_team") and p.get("home_team") \
            else (p.get("team") or "?")
        return f"total|{period}|{game}|{side}"
    return f"{bt}|{period}|{p.get('player_key') or p.get('team') or '?'}|{_stat_key(p)}|{side}"


def _stat_key(p: dict) -> str:
    """Different 'other' stats (home runs vs stolen bases) must never group together."""
    stat = p.get("stat") or "?"
    return f"other:{_slug(p.get('stat_detail'))}" if stat == "other" else stat


def label(p: dict) -> str:
    """Short bet name without the line (lines are shown as a range). Non-NFL bets carry a league tag."""
    period = "" if (p.get("period") or "game") == "game" else f" ({p['period'].upper()})"
    league = (p.get("league") or "NFL").upper()
    tag = "" if league == "NFL" else f"[{league}] "
    bt = p.get("bet_type")
    side = p.get("side") or "?"
    if bt == "player_prop":
        stat = stat_name(p.get("stat"), p.get("stat_detail"))
        if p.get("stat") in EVENT_STATS:
            return f"{tag}{p.get('player') or '?'} {stat}{'' if side == 'yes' else ' ' + side.upper()}{period}"
        return f"{tag}{p.get('player') or '?'} {side} {stat}{period}"
    if bt == "spread":
        return f"{tag}{p.get('team') or '?'} spread{period}"
    if bt == "moneyline":
        return f"{tag}{p.get('team') or '?'} moneyline{period}"
    if bt == "team_total":
        return f"{tag}{p.get('team') or '?'} team total {side}{period}"
    if bt == "total":
        game = f"{p['away_team']} @ {p['home_team']}" if p.get("away_team") and p.get("home_team") \
            else (p.get("team") or "?")
        return f"{tag}{game} game total {side}{period}"
    what = stat_name(p.get("stat"), p.get("stat_detail")) or p.get("stat_detail") or ""
    return f"{tag}{bt}: {p.get('player') or p.get('team') or '?'} {what} {p.get('side') or ''}".strip()


def _num(v, cast=float):
    try:
        return cast(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _normalize_threshold(p: dict) -> None:
    """Express "N+" style props as over (N - 0.5) so they group with ordinary over/unders.

    "To record 2+ hits" may arrive as side 'yes' line 2, or 'over' line 2; both become over 1.5.
    "To hit a double" (yes, no line) on a countable stat becomes over 0.5.
    """
    if p["bet_type"] != "player_prop" or p["stat"] in EVENT_STATS:
        return
    line, side, snippet = p["line"], p["side"], p.get("snippet") or ""
    whole = line is not None and float(line).is_integer()
    if side == "yes":
        p["side"] = "over"
        p["line"] = (line - 0.5) if whole else (line if line is not None else 0.5)
    elif side == "over" and whole and re.search(rf"(?<![\d.]){int(line)}\s*\+", snippet):
        p["line"] = line - 0.5


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
        "stat_detail": (raw.get("stat_detail") or "").strip()[:80] or None,
        "side": raw.get("side") if raw.get("side") in SIDES else None,
        "line": _num(raw.get("line")),
        "odds": _num(raw.get("odds"), int),
        "units": _num(raw.get("units")),
        "parlay_group": raw.get("parlay_group") or None,
        "snippet": (raw.get("snippet") or "")[:300] or None,
    }
    if p["bet_type"] == "player_prop" and p["stat"] in (None, "other") and not p["stat_detail"]:
        # No stat named and no description (picks extracted before the multi-sport update):
        # recognize the market from the post text instead.
        p["stat"], p["stat_detail"] = infer_stat(league, p["snippet"], p["player"])
    _normalize_threshold(p)
    if p["bet_type"] in ("spread", "moneyline"):
        p["side"] = "team"
    p["player_key"] = player_key(p["player"])
    p["category"] = STAT_CATEGORY.get(p["stat"]) if p["stat"] else "game_line"
    p["direction"] = direction(p["side"])
    p["bet_key"] = bet_key(p)
    p["label"] = label(p)
    return p
