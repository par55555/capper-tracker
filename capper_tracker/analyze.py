"""Find what the tracked cappers agree on for a given week."""
from __future__ import annotations

from datetime import date

import pandas as pd

from .config import THEME_MIN_ACCOUNTS
from .games import GAME_COLUMNS, Schedule, attach_games
from .normalize import EVENT_STATS, bet_key, clean_pick, label, stat_name

PASS_GAME = {"passing", "receiving"}
RUN_GAME = {"rushing"}


def _first(values):
    """Most common non-empty value (all picks in a group should point at the same game)."""
    v = values.dropna()
    return v.mode().iat[0] if not v.empty else None


def _join(values) -> str:
    return ", ".join(sorted({str(v) for v in values if pd.notna(v)}))


def _line_range(s: pd.Series) -> str:
    s = s.dropna()
    if s.empty:
        return ""
    lo, hi = s.min(), s.max()
    return f"{lo:g}" if lo == hi else f"{lo:g} to {hi:g}"


def canonicalize_players(df: pd.DataFrame) -> pd.DataFrame:
    """Merge name variants ("J. Allen", "Josh Allen") when first initial, last name and team agree.

    Different teams stay separate, so Josh Allen (BUF QB) and Josh Hines-Allen style collisions
    don't merge as long as the extractor got the team right.
    """
    df = df.copy()
    has = df["player_key"].notna()
    if not has.any():
        return df
    stats = (
        df[has].groupby("player_key")
        .agg(n=("player", "size"),
             name=("player", lambda s: max(s.mode(), key=len)),
             team=("team", lambda s: s.dropna().mode().iat[0] if s.notna().any() else None))
    )
    # Most-mentioned spelling wins; ties go to the fuller name ("josh allen" over "j allen").
    stats = stats.assign(_len=stats.index.str.len()).sort_values(["n", "_len"], ascending=False)
    reps: list[dict] = []
    canon: dict[str, str] = {}
    for key, row in stats.iterrows():
        toks = key.split()
        first, last = (toks[0][0] if len(toks) > 1 else None), toks[-1]
        match = next(
            (r for r in reps
             if r["last"] == last
             and (first is None or r["first"] is None or r["first"] == first)
             and (row.team is None or r["team"] is None or r["team"] == row.team)),
            None,
        )
        if match:
            canon[key] = match["key"]
            match["team"] = match["team"] or row.team
            if len(row["name"]) > len(match["name"]):  # prefer the fuller spelling
                match["name"] = row["name"]
        else:
            reps.append({"key": key, "first": first, "last": last, "team": row.team, "name": row["name"]})
            canon[key] = key
    names = {r["key"]: r["name"] for r in reps}
    teams = {r["key"]: r["team"] for r in reps}
    df.loc[has, "player_key"] = df.loc[has, "player_key"].map(canon)
    df.loc[has, "player"] = df.loc[has, "player_key"].map(names)
    df.loc[has, "team"] = df.loc[has, "team"].fillna(df.loc[has, "player_key"].map(teams))
    records = df.to_dict("records")
    df["bet_key"] = [bet_key(r) for r in records]
    df["label"] = [label(r) for r in records]
    return df


def _market(df: pd.DataFrame) -> pd.Series:
    """bet_key without the side for directional bets: 'Chase receiving yards' rather than '... over'."""
    no_side = df["bet_key"].str.rsplit("|", n=1).str[0]
    return no_side.where(df["direction"] != 0, df["bet_key"])


_RAW_FIELDS = ["league", "bet_type", "period", "player", "team", "opponent", "away_team", "home_team",
               "stat", "stat_detail", "side", "line", "odds", "units", "parlay_group", "snippet"]


def renormalize(picks: pd.DataFrame) -> pd.DataFrame:
    """Re-run today's normalization on stored picks (free; no API calls).

    Picks extracted by older versions get the current rules: stats recognized from the snippet,
    "N+" thresholds, readable labels and correct grouping keys.
    """
    if picks.empty:
        return picks
    df = picks.copy()
    if "stat_detail" not in df:
        df["stat_detail"] = None
    rows = []
    for r in df[_RAW_FIELDS].to_dict("records"):
        raw = {k: (None if (not isinstance(v, str) and pd.isna(v)) else v) for k, v in r.items()}
        rows.append(clean_pick(raw))
    fresh = pd.DataFrame(rows, index=df.index)
    for col in fresh.columns:
        df[col] = fresh[col]
    return df


def prepare(picks: pd.DataFrame) -> pd.DataFrame:
    """One row per (account, bet) for the window. All accounts count equally.

    1. Name variants are merged first, so "J. Allen o34.5" and "Josh Allen o30.5" are the same bet.
    2. If an account switched sides on a market (over -> under), only their latest side counts.
    3. Repeats of the same bet by the same account (re-posts, the same leg in several parlays,
       a moved line) collapse to one row; `times_posted` keeps the count for auditing.
    """
    if picks.empty:
        return picks
    df = canonicalize_players(renormalize(picks)).sort_values("created_at")
    df["market"] = _market(df)

    sides = df[df["direction"] != 0].groupby(["handle", "market"])["direction"].nunique()
    switched = set(sides[sides > 1].index)
    latest = df.drop_duplicates(["handle", "market"], keep="last")[["handle", "market", "bet_key"]]
    df = df.merge(latest, on=["handle", "market", "bet_key"])

    posts = df.groupby(["handle", "bet_key"])["tweet_id"].nunique().rename("times_posted").reset_index()
    df = df.drop_duplicates(["handle", "bet_key"], keep="last").merge(posts, on=["handle", "bet_key"])
    df["switched_side"] = [(h, m) in switched for h, m in zip(df["handle"], df["market"])]
    return df.sort_values("created_at").reset_index(drop=True)


POST_TYPES = {
    "new_pick": "Pick: recommends a bet on an unsettled game",
    "result_recap": "Recap: reports results of settled bets (not counted)",
    "promo": "Promo: ad, code or giveaway with no bet (not counted)",
    "other": "No bet: commentary, analysis, memes or replies (not counted)",
}


def _fmt_lines(lo, hi) -> str:
    if pd.isna(lo):
        return ""
    return f"{lo:g}" if lo == hi else f"{lo:g} to {hi:g}"


def _threshold_phrase(side: str, lo, hi, stat: str) -> str:
    """'over 0.5 home runs' reads better as '1+ home runs'."""
    if pd.isna(lo):
        return f"{side} {stat}"
    if side == "over" and lo == hi and lo % 1 == 0.5 and lo < 10:
        return f"{int(lo + 0.5)}+ {stat}"
    return f"{side} {_fmt_lines(lo, hi)} {stat}"


def explain(row: dict) -> str:
    """One plain-English sentence describing what a grouped bet means."""
    n, league = row["accounts"], row.get("league") or "NFL"
    who = f"{n} account{'s' if n != 1 else ''}"
    subject = row.get("player") or row.get("team") or "?"
    team = row.get("team")
    tag = f" ({team}, {league})" if team and row.get("player") else f" ({league})"
    lo, hi, side, bt = row.get("line_min"), row.get("line_max"), row.get("side") or "", row["bet_type"]
    stat = stat_name(row.get("stat"), row.get("stat_detail"))

    if bt == "player_prop":
        if row.get("stat") in EVENT_STATS:
            event = {"anytime_td": "score a touchdown", "first_td": "score the first touchdown",
                     "two_plus_tds": "score 2+ touchdowns", "anytime_goal": "score a goal",
                     "double_double": "record a double-double"}.get(row["stat"], stat)
            text = f"{who} like {subject}{tag} to {event}" if side == "yes" else \
                f"{who} like {subject}{tag} NOT to {event}"
        else:
            text = f"{who} like {subject}{tag} to record {_threshold_phrase(side, lo, hi, stat)}"
    elif bt == "spread":
        text = f"{who} back {subject} ({league}) against the spread at {_fmt_lines(lo, hi)}"
    elif bt == "moneyline":
        text = f"{who} back {subject} ({league}) to win outright"
    elif bt == "team_total":
        text = f"{who} like the {side.upper()} on {subject}'s team total ({_fmt_lines(lo, hi)} points, {league})"
    elif bt == "total":
        game = (f"{row['away_team']} @ {row['home_team']}" if row.get("away_team") and row.get("home_team")
                else subject)
        text = f"{who} like the {side.upper()} in {game} ({_fmt_lines(lo, hi)} total, {league})"
    else:
        text = f"{who} on {row['bet']}"

    legs = row.get("in_parlays", 0)
    if legs and legs == n:
        text += "; all as parlay legs"
    elif legs:
        text += f"; {legs} of {n} as parlay legs"
    odds = row.get("median_odds_straight")
    if odds is not None and not pd.isna(odds):
        text += f"; typical straight-bet price {int(odds):+d}"
    return text + "."


def consensus_bets(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    d = df.copy()
    d["odds_straight"] = d["odds"].where(d["parlay_group"].isna())  # parlay prices aren't a leg's price
    if "stat_detail" not in d:
        d["stat_detail"] = None
    g = d.groupby("bet_key")
    out = g.agg(
        league=("league", "first"),
        bet=("label", "first"),
        bet_type=("bet_type", "first"),
        category=("category", "first"),
        player=("player", "first"),
        team=("team", "first"),
        away_team=("away_team", "first"),
        home_team=("home_team", "first"),
        stat=("stat", "first"),
        stat_detail=("stat_detail", "first"),
        side=("side", "first"),
        accounts=("handle", "nunique"),
        repeat_posts=("times_posted", lambda x: int(x.sum() - len(x))),
        line_min=("line", "min"),
        line_max=("line", "max"),
        median_line=("line", "median"),
        median_odds_straight=("odds_straight", "median"),
        total_units=("units", "sum"),
        in_parlays=("parlay_group", lambda s: int(s.notna().sum())),
        who=("handle", _join),
        first_posted=("created_at", "min"),
        game=("game", _first),
        game_time_et=("game_time_et", _first),
        game_status=("game_status", _first),
        venue=("venue", _first),
    ).reset_index()
    out["what_it_means"] = [explain(r) for r in out.to_dict("records")]
    out["lines"] = [_fmt_lines(a, b) for a, b in zip(out["line_min"], out["line_max"])]
    cols = ["league", "game_time_et", "game", "game_status", "bet", "what_it_means", "accounts",
            "who", "lines", "median_line", "median_odds_straight", "in_parlays", "repeat_posts",
            "total_units", "venue", "bet_type", "category", "first_posted", "bet_key"]
    out = out[cols].sort_values(["accounts", "game_time_et", "first_posted"],
                                ascending=[False, True, True], na_position="last")
    return out.reset_index(drop=True)


def player_consensus(df: pd.DataFrame) -> pd.DataFrame:
    props = df[df["bet_type"].eq("player_prop") & df["player_key"].notna()]
    if props.empty:
        return pd.DataFrame()
    rows = []
    for key, grp in props.groupby("player_key"):
        bulls = set(grp.loc[grp["direction"] > 0, "handle"])
        bears = set(grp.loc[grp["direction"] < 0, "handle"])
        g2 = grp.assign(stat_text=[stat_name(a, b) for a, b in
                                   zip(grp["stat"], grp.get("stat_detail", pd.Series(index=grp.index)))])
        per_bet = g2.groupby(["stat_text", "side"])["handle"].nunique().sort_values(ascending=False)
        conflict = any(grp[grp["stat"] == st]["direction"].nunique() > 1 for st in grp["stat"].unique())
        rows.append({
            "league": grp["league"].iat[0],
            "player": grp["player"].iat[0],
            "team": grp["team"].dropna().iat[0] if grp["team"].notna().any() else None,
            "accounts": grp["handle"].nunique(),
            "accounts_over_or_yes": len(bulls),
            "accounts_under_or_no": len(bears),
            "net_lean": len(bulls) - len(bears),
            "bets": "; ".join(f"{sd} {st} x{n}" for (st, sd), n in per_bet.items()),
            "conflicting_sides": conflict,
            "who": _join(grp["handle"]),
        })
    return (pd.DataFrame(rows)
            .sort_values(["accounts", "net_lean"], ascending=False).reset_index(drop=True))


def _players_summary(grp: pd.DataFrame) -> str:
    counts = grp.groupby("player")["handle"].nunique().sort_values(ascending=False)
    return ", ".join(f"{p} ({n})" for p, n in counts.items())


def team_themes(df: pd.DataFrame, min_accounts: int = THEME_MIN_ACCOUNTS) -> pd.DataFrame:
    """Per team: how many accounts lean on its pass game vs run game, and who's driving it.

    pass game = QB passing props + pass-catcher receiving props; run game = rushing props.
    Net = distinct accounts betting overs minus distinct accounts betting unders.
    """
    nfl = df[df["league"].fillna("NFL").str.upper().eq("NFL")]
    props = nfl[nfl["bet_type"].eq("player_prop") & nfl["team"].notna()]
    sides = nfl[nfl["bet_type"].isin(["spread", "moneyline"]) & nfl["team"].notna()]
    teams = sorted(set(props["team"]) | set(sides["team"]))
    rows = []
    for team in teams:
        tp = props[props["team"] == team]
        row: dict = {"team": team}
        for name, cats in (("pass", PASS_GAME), ("run", RUN_GAME)):
            area = tp[tp["category"].isin(cats)]
            bulls = set(area.loc[area["direction"] > 0, "handle"])
            bears = set(area.loc[area["direction"] < 0, "handle"])
            row[f"{name}_accounts_over"] = len(bulls)
            row[f"{name}_accounts_under"] = len(bears)
            row[f"{name}_net"] = len(bulls) - len(bears)
            row[f"{name}_players"] = _players_summary(area)
        td = tp[tp["category"].eq("scoring") & (tp["direction"] > 0)]
        row["td_scorer_accounts"] = td["handle"].nunique()
        row["td_players"] = _players_summary(td)
        row["side_backers"] = sides.loc[sides["team"] == team, "handle"].nunique()

        p, r = row["pass_net"], row["run_net"]
        if p >= min_accounts and r >= min_accounts:
            theme = "Pass + run threat"
        elif p >= min_accounts and p > r:
            theme = "Passing threat"
        elif r >= min_accounts and r > p:
            theme = "Rushing threat"
        elif p <= -min_accounts or r <= -min_accounts:
            theme = "Fade (unders)"
        else:
            theme = ""
        row["theme"] = theme
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out["_strength"] = out[["pass_net", "run_net"]].abs().max(axis=1) + out["side_backers"]
    out = out.sort_values(["_strength", "td_scorer_accounts"], ascending=False).drop(columns="_strength")
    first = ["team", "theme", "pass_net", "run_net", "side_backers", "td_scorer_accounts"]
    return out[first + [c for c in out.columns if c not in first]].reset_index(drop=True)


def conflicts(df: pd.DataFrame) -> pd.DataFrame:
    """Bets where tracked accounts are on opposite sides of the same player stat or game total."""
    d = df[df["direction"] != 0].copy()
    if d.empty:
        return pd.DataFrame()
    d["market"] = d["bet_key"].str.rsplit("|", n=1).str[0]  # bet_key minus the side
    rows = []
    for market, grp in d.groupby("market"):
        if grp["direction"].nunique() < 2:
            continue
        first = grp.iloc[0]
        rows.append({
            "market": first["label"].replace(f" {first['side']}", "", 1),
            "over_or_yes": _join(grp.loc[grp["direction"] > 0, "handle"]),
            "under_or_no": _join(grp.loc[grp["direction"] < 0, "handle"]),
            "lines": _line_range(grp["line"]),
        })
    return pd.DataFrame(rows)


def build_report(picks: pd.DataFrame | None, tweets: pd.DataFrame, schedule: Schedule | None = None,
                 game_days: tuple[date, date] | None = None,
                 prepared: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    """Report tables. Pass `prepared` (prepare() + attach_games() output, possibly with extra
    columns such as Kalshi prices) to skip those steps."""
    if prepared is not None:
        df = prepared
    else:
        df = prepare(picks)
        if not df.empty:
            first, last = game_days or (date.today(), date.today())
            df = attach_games(df, schedule, first, last)
    consensus = consensus_bets(df)
    players = player_consensus(df)
    # Games already played are settled; they shouldn't crowd out bets you can still make.
    open_picks = df[df["game_status"].ne("Final")] if not df.empty else df
    themes = team_themes(open_picks) if not open_picks.empty else pd.DataFrame()
    if consensus.empty:
        summary = settled = consensus
    else:
        shared = consensus[consensus["accounts"] >= 2]  # bet_key kept for joins; dropped on export
        is_final = shared["game_status"].eq("Final")
        summary = shared[~is_final].reset_index(drop=True)   # every shared bet still to be played
        settled = shared[is_final].reset_index(drop=True)
    pick_cols = ["handle", "created_at", "league", "game_time_et", "game", "label", "line", "odds", "units", "times_posted",
                 "switched_side", "bet_type", "category", "player", "team", "stat", "stat_detail",
                 "side", "parlay_group", "snippet", "url", "bet_key", "event_id", "period", "direction",
                 "kalshi_ticker", "kalshi_title", "kalshi_side", "kalshi_match", "kalshi_note",
                 "kalshi_prob_post", "kalshi_cost_post", "kalshi_prob_close", "kalshi_prob_now", "kalshi_move"]
    pick_cols = [c for c in pick_cols if c in df.columns]
    if not tweets.empty:
        tweets = tweets.copy()
        tweets.insert(3, "post_type", tweets["tweet_type"].map(POST_TYPES).fillna("Not extracted yet"))
    return {
        "Top Consensus": summary,
        "Team Themes": themes,
        "Players": players,
        "All Bets": consensus,
        "Already Played": settled,
        "Conflicts": conflicts(df) if not df.empty else pd.DataFrame(),
        "Picks": df[pick_cols] if not df.empty else df,
        "Tweets": tweets,
        "Guide": GUIDE,
    }


GUIDE = pd.DataFrame([
    ("Top Consensus", "Bets that 2+ tracked accounts posted this week on games NOT yet finished, most-shared first, then "
                      "soonest game. Start with 'what_it_means'. Bets on finished games move to 'Already Played'."),
    ("Already Played", "Shared bets whose game was Final when the report ran. Kept for reviewing how consensus did."),
    ("league", "Sport/league of the bet (NFL, NCAAF, MLB, WNBA, NHL, ...). Non-NFL bets are also tagged in 'bet', e.g. [MLB]."),
    ("game / game_time_et", "The matchup (away @ home) and start time in Eastern, looked up from ESPN's schedule using the "
                            "bet's team and the time it was posted. Blank if the team couldn't be matched or ESPN was unreachable."),
    ("game_status", "Upcoming, Live or Final when the report ran. Final means the bet is already settled."),
    ("lines", "The line(s) the accounts posted. Lines are grouped: o74.5 and o79.5 count as the same bet. '1+ home runs' = over 0.5."),
    ("median_odds_straight", "Typical price from straight bets only. Parlay legs are excluded because a parlay's price isn't the leg's price."),
    ("in_parlays", "How many of the accounts posted this bet as a parlay leg rather than a straight bet."),
    ("repeat_posts", "Extra times the same accounts re-posted this bet. Repeats never add votes: one account = one vote."),
    ("category", "Stat group. Football: passing, receiving, rushing, scoring, defense. Other sports: batting, pitching, "
                 "rebounding, playmaking, shooting, goaltending, combo. 'other' = a market outside these lists; the bet name "
                 "then shows the exact market in words (e.g. 'free throws made') and different 'other' markets are never merged. "
                 "'unrecognized market (see snippet)' means neither the model nor the post text named the market; "
                 "check the Picks sheet snippet."),
    ("Team Themes", "NFL only, games not yet finished. Net accounts on a team's pass-game overs (QB passing + receiving) "
                    "vs run-game overs (rushing)."),
    ("Players", "Every player any account touched, with over/under lean and conflict flag."),
    ("All Bets", "Every grouped bet, including ones only one account posted."),
    ("Conflicts", "Markets where tracked accounts are on opposite sides."),
    ("Picks", "One row per account per bet after dedup, with the original snippet and link for checking."),
    ("Tweets: post_type", "How each post was classified. Pick = counted. Recap, Promo and No bet "
                          "(commentary, analysis, memes, replies; stored as 'other') are not counted."),
], columns=["item", "meaning"])
