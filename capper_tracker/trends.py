"""Cross-week trends from graded picks: who hits, which markets hit, does consensus help, week by week.

Input is results.grade() output for several weeks concatenated, with a `week` column.
Hit rate = won / (won + lost); pushes, pending and ungraded picks are left out. Every rate ships
with its sample size and a 95% Wilson interval, because 4-1 and 40-10 are both "80%".
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import results
from .normalize import STAT_LABEL
from .results import LOST, PUSH, WON

GAME_LINE_LABEL = {"moneyline": "Moneyline", "spread": "Spread", "total": "Game total",
                   "team_total": "Team total"}


def wilson(won: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if n == 0:
        return None, None
    p = won / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)


def _rec(res: pd.Series) -> dict:
    won, lost, push = (int((res == x).sum()) for x in (WON, LOST, PUSH))
    lo, hi = wilson(won, won + lost)
    return {"won": won, "lost": lost, "push": push, "graded": won + lost,
            "hit_rate": round(won / (won + lost), 4) if won + lost else None, "ci_low": lo, "ci_high": hi}


def _money(grp: pd.DataFrame) -> dict:
    """Flat-stake (1 unit per bet) profit on settled straight bets that posted odds.

    Parlay legs are excluded: the posted price is the whole parlay's, not the leg's.
    `breakeven` is the average hit rate those odds need to break even (the vig is ignored).
    """
    s = grp[grp["parlay_group"].isna() & grp["odds"].notna() & grp["result"].isin([WON, LOST, PUSH])] \
        if {"parlay_group", "odds"} <= set(grp.columns) else grp.iloc[0:0]
    s = s[s["odds"].astype(float).abs() >= 100]  # American odds only; drop junk like 0 or 1.9
    if s.empty:
        return {"priced": 0, "breakeven": None, "units": None, "roi": None}
    odds = s["odds"].astype(float).to_numpy()
    implied = np.where(odds > 0, 100 / (odds + 100), -odds / (-odds + 100))
    payout = np.where(odds > 0, odds / 100, 100 / -odds)
    res = s["result"].to_numpy()
    pl = np.where(res == WON, payout, np.where(res == LOST, -1.0, 0.0))
    return {"priced": len(s), "breakeven": round(float(implied.mean()), 4),
            "units": round(float(pl.sum()), 2), "roi": round(float(pl.sum() / len(s)), 4)}


def market_of(r: dict) -> str:
    bt = r.get("bet_type")
    if bt == "player_prop":
        stat = r.get("stat") or "other"
        name = STAT_LABEL.get(stat, stat.replace("_", " "))
        name = name[0].upper() + name[1:]
        return f"{name} (under/no)" if (r.get("direction") or 0) < 0 else name
    base = GAME_LINE_LABEL.get(bt, "Other")
    if bt in ("total", "team_total") and (r.get("direction") or 0) < 0:
        base += " (under)"
    period = (r.get("period") or "game").lower()
    return base if period == "game" else f"{base} ({period.upper()})"


def _with_market(g: pd.DataFrame) -> pd.DataFrame:
    return g.assign(market=[market_of(r) for r in results.clean_records(g)])


def bets_by_week(g: pd.DataFrame) -> pd.DataFrame:
    """Bets grouped within each week (the same bet in two weeks is two bets)."""
    frames = [results.bet_results(gw).assign(week=w) for w, gw in g.groupby("week")]
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def weekly(g: pd.DataFrame, bets: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for w, gw in g.groupby("week"):
        shared = bets[(bets["week"] == w) & (bets["accounts"] >= 2)] if not bets.empty else bets
        a, s = _rec(gw["result"]), _rec(shared["result"] if not shared.empty else pd.Series(dtype=str))
        rows.append({"week": int(w), "picks": len(gw), "accounts": gw["handle"].nunique(),
                     **{f"all_{k}": v for k, v in a.items()}, **{f"shared_{k}": v for k, v in s.items()},
                     "ungraded": int((gw["result"] == results.UNGRADED).sum()),
                     "pending": int((gw["result"] == results.PENDING).sum())})
    return pd.DataFrame(rows)


def cappers(g: pd.DataFrame, bets: pd.DataFrame, min_market: int = 3) -> pd.DataFrame:
    shared = set(zip(bets.loc[bets["accounts"] >= 2, "week"], bets.loc[bets["accounts"] >= 2, "bet_key"])) \
        if not bets.empty else set()
    g = _with_market(g)
    rows = []
    for handle, grp in g.groupby("handle"):
        rec = _rec(grp["result"])
        on_shared = grp[[(w, k) in shared for w, k in zip(grp["week"], grp["bet_key"])]]
        srec = _rec(on_shared["result"])
        best, worst = None, None
        mk = [(m, _rec(x["result"])) for m, x in grp.groupby("market")]
        mk = [(m, r) for m, r in mk if r["graded"] >= min_market]
        if mk:
            b = max(mk, key=lambda t: (t[1]["hit_rate"], t[1]["graded"]))
            best = f"{b[0]} {b[1]['won']}–{b[1]['lost']}"
            if len(mk) > 1:
                w_ = min(mk, key=lambda t: (t[1]["hit_rate"], -t[1]["graded"]))
                worst = f"{w_[0]} {w_[1]['won']}–{w_[1]['lost']}"
        weeks_rec = [_rec(x["result"]) for _, x in grp.groupby("week")]
        rows.append({"handle": handle, **rec,
                     "weeks_active": int(grp["week"].nunique()),
                     "winning_weeks": sum(1 for r in weeks_rec if r["graded"] and r["hit_rate"] >= 0.5),
                     "shared_won": srec["won"], "shared_lost": srec["lost"], "shared_hit_rate": srec["hit_rate"],
                     "best_market": best, "worst_market": worst,
                     **{f"straight_{k}": v for k, v in _money(grp).items()},
                     "ungraded": int((grp["result"] == results.UNGRADED).sum())})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["ci_low", "graded"], ascending=False, na_position="last").reset_index(drop=True)


def capper_weeks(g: pd.DataFrame) -> pd.DataFrame:
    rows = [{"handle": h, "week": int(w), **_rec(x["result"])} for (h, w), x in g.groupby(["handle", "week"])]
    return pd.DataFrame(rows)


def markets(g: pd.DataFrame) -> pd.DataFrame:
    g = _with_market(g)
    g = g.assign(league=g["league"].fillna("?"))
    rows = []
    for (league, market), grp in g.groupby(["league", "market"]):
        rec = _rec(grp["result"])
        if rec["graded"] == 0:
            continue
        rows.append({"league": league, "market": market, **rec, "accounts": grp["handle"].nunique(),
                     **_money(grp)})
    out = pd.DataFrame(rows)
    return out.sort_values("graded", ascending=False).reset_index(drop=True) if not out.empty else out


def styles(g: pd.DataFrame) -> pd.DataFrame:
    is_leg = g["parlay_group"].notna() if "parlay_group" in g else pd.Series(False, index=g.index)
    rows = [{"style": name, **_rec(g.loc[mask, "result"]), **_money(g.loc[mask])}
            for name, mask in (("Straight bets", ~is_leg), ("Parlay legs", is_leg))]
    return pd.DataFrame(rows)


def trends_report(g: pd.DataFrame) -> dict:
    if g.empty:
        return {"weeks": [], "frames": {}}
    bets = bets_by_week(g)
    return {
        "weeks": sorted(int(w) for w in g["week"].unique()),
        "frames": {
            "Weekly": weekly(g, bets),
            "Cappers": cappers(g, bets),
            "Capper Weeks": capper_weeks(g),
            "Markets": markets(g),
            "Agreement": results.by_agreement(bets) if not bets.empty else pd.DataFrame(),
            "Styles": styles(g),
        },
    }
