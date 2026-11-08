"""Trend math: intervals, flat-stake profit, market labels, cross-week rollups."""
from __future__ import annotations

import pandas as pd
import pytest

from capper_tracker import trends


def test_wilson_interval():
    assert trends.wilson(0, 0) == (None, None)
    lo, hi = trends.wilson(35, 55)
    assert lo < 35 / 55 < hi and hi - lo < 0.3
    small_lo, small_hi = trends.wilson(4, 5)   # same-ish rate, tiny sample -> much wider
    assert small_hi - small_lo > hi - lo


def test_money_uses_straight_priced_bets_only():
    g = pd.DataFrame({
        "result": ["Won", "Lost", "Won", "Push", "Won"],
        "odds": [300, -110, -200, 150, 500],
        "parlay_group": [None, None, None, None, "p1"],   # parlay leg excluded
    })
    m = trends._money(g)
    assert m["priced"] == 4
    assert m["units"] == pytest.approx(3.0 - 1.0 + 0.5 + 0.0, abs=0.01)
    assert m["breakeven"] == pytest.approx((0.25 + 110 / 210 + 200 / 300 + 0.4) / 4, abs=1e-3)


@pytest.mark.parametrize("row, label", [
    ({"bet_type": "player_prop", "stat": "receiving_yards", "direction": 1}, "Receiving yards"),
    ({"bet_type": "player_prop", "stat": "anytime_td", "direction": 1}, "Anytime touchdown"),
    ({"bet_type": "player_prop", "stat": "rushing_yards", "direction": -1}, "Rushing yards (under/no)"),
    ({"bet_type": "total", "direction": -1, "period": "1h"}, "Game total (under) (1H)"),
    ({"bet_type": "spread", "direction": 0}, "Spread"),
])
def test_market_labels(row, label):
    assert trends.market_of(row) == label


def test_trends_report_rollup():
    def p(handle, week, key, result, **kw):
        return {"handle": handle, "week": week, "bet_key": key, "result": result, "league": "NFL",
                "label": key, "bet_type": "player_prop", "stat": "anytime_td", "direction": 1,
                "line": None, "odds": None, "parlay_group": None, "actual_text": "", "game": "A @ B",
                "game_time_et": None, "category": "scoring", **kw}
    g = pd.DataFrame([
        p("a", 5, "x", "Won"), p("b", 5, "x", "Won"), p("a", 5, "y", "Lost"),
        p("a", 6, "x", "Lost"), p("c", 6, "z", "Ungraded"),
    ])
    rep = trends.trends_report(g)
    assert rep["weeks"] == [5, 6]
    weekly = rep["frames"]["Weekly"].set_index("week")
    assert (weekly.loc[5, "all_won"], weekly.loc[5, "all_lost"], weekly.loc[5, "shared_won"]) == (2, 1, 1)
    cappers = rep["frames"]["Cappers"].set_index("handle")
    assert (cappers.loc["a", "won"], cappers.loc["a", "lost"], cappers.loc["a", "weeks_active"]) == (1, 2, 2)
    assert cappers.loc["a", "shared_won"] == 1
    cw = rep["frames"]["Capper Weeks"]
    assert len(cw[cw["handle"] == "a"]) == 2
