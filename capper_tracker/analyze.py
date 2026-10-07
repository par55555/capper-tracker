"""Find what the tracked cappers agree on for a given week."""
from __future__ import annotations

import pandas as pd

from .config import THEME_MIN_ACCOUNTS
from .normalize import bet_key, label

PASS_GAME = {"passing", "receiving"}
RUN_GAME = {"rushing"}


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


def prepare(picks: pd.DataFrame) -> pd.DataFrame:
    """One row per (account, bet) for the window. All accounts count equally.

    1. Name variants are merged first, so "J. Allen o34.5" and "Josh Allen o30.5" are the same bet.
    2. If an account switched sides on a market (over -> under), only their latest side counts.
    3. Repeats of the same bet by the same account (re-posts, the same leg in several parlays,
       a moved line) collapse to one row; `times_posted` keeps the count for auditing.
    """
    if picks.empty:
        return picks
    df = canonicalize_players(picks).sort_values("created_at")
    df["market"] = _market(df)

    sides = df[df["direction"] != 0].groupby(["handle", "market"])["direction"].nunique()
    switched = set(sides[sides > 1].index)
    latest = df.drop_duplicates(["handle", "market"], keep="last")[["handle", "market", "bet_key"]]
    df = df.merge(latest, on=["handle", "market", "bet_key"])

    posts = df.groupby(["handle", "bet_key"])["tweet_id"].nunique().rename("times_posted").reset_index()
    df = df.drop_duplicates(["handle", "bet_key"], keep="last").merge(posts, on=["handle", "bet_key"])
    df["switched_side"] = [(h, m) in switched for h, m in zip(df["handle"], df["market"])]
    return df.sort_values("created_at").reset_index(drop=True)


def consensus_bets(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    g = df.groupby("bet_key")
    out = g.agg(
        bet=("label", "first"),
        bet_type=("bet_type", "first"),
        category=("category", "first"),
        accounts=("handle", "nunique"),
        repeat_posts=("times_posted", lambda x: int(x.sum() - len(x))),
        lines=("line", _line_range),
        median_line=("line", "median"),
        median_odds=("odds", "median"),
        total_units=("units", "sum"),
        in_parlays=("parlay_group", lambda s: int(s.notna().sum())),
        who=("handle", _join),
        first_posted=("created_at", "min"),
    ).reset_index()
    return out.sort_values(["accounts", "first_posted"], ascending=[False, True]).reset_index(drop=True)


def player_consensus(df: pd.DataFrame) -> pd.DataFrame:
    props = df[df["bet_type"].eq("player_prop") & df["player_key"].notna()]
    if props.empty:
        return pd.DataFrame()
    rows = []
    for key, grp in props.groupby("player_key"):
        bulls = set(grp.loc[grp["direction"] > 0, "handle"])
        bears = set(grp.loc[grp["direction"] < 0, "handle"])
        per_bet = grp.groupby(["stat", "side"])["handle"].nunique().sort_values(ascending=False)
        conflict = any(grp[grp["stat"] == st]["direction"].nunique() > 1 for st in grp["stat"].unique())
        rows.append({
            "player": grp["player"].iat[0],
            "team": grp["team"].dropna().iat[0] if grp["team"].notna().any() else None,
            "accounts": grp["handle"].nunique(),
            "accounts_over_or_yes": len(bulls),
            "accounts_under_or_no": len(bears),
            "net_lean": len(bulls) - len(bears),
            "bets": "; ".join(f"{st.replace('_', ' ')} {sd} x{n}" for (st, sd), n in per_bet.items()),
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
    props = df[df["bet_type"].eq("player_prop") & df["team"].notna()]
    sides = df[df["bet_type"].isin(["spread", "moneyline"]) & df["team"].notna()]
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


def build_report(picks: pd.DataFrame, tweets: pd.DataFrame) -> dict[str, pd.DataFrame]:
    df = prepare(picks)
    consensus = consensus_bets(df)
    players = player_consensus(df)
    themes = team_themes(df) if not df.empty else pd.DataFrame()
    summary = (consensus[consensus["accounts"] >= 2].drop(columns="bet_key").head(25)
               if not consensus.empty else consensus)
    pick_cols = ["handle", "created_at", "label", "line", "odds", "units", "times_posted",
                 "switched_side", "bet_type", "category", "player", "team", "stat", "side",
                 "parlay_group", "snippet", "url"]
    return {
        "Top Consensus": summary,
        "Team Themes": themes,
        "Players": players,
        "All Bets": consensus,
        "Conflicts": conflicts(df) if not df.empty else pd.DataFrame(),
        "Picks": df[pick_cols] if not df.empty else df,
        "Tweets": tweets,
    }
