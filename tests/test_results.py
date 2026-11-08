"""Grading rules against a hand-built ESPN game summary (no network)."""
from __future__ import annotations

import pandas as pd
import pytest

from capper_tracker import results
from capper_tracker.results import BoxScores, grade, parse_summary


def team(abbr, loc, name):
    return {"abbreviation": abbr, "displayName": f"{loc} {name}", "shortDisplayName": name,
            "location": loc, "name": name}


def athlete(name, stats):
    return {"athlete": {"displayName": name}, "stats": stats}


SUMMARY = {
    "header": {"competitions": [{
        "status": {"type": {"completed": True}},
        "competitors": [
            {"homeAway": "home", "team": team("JAX", "Jacksonville", "Jaguars"), "score": "31",
             "linescores": [{"value": 0}, {"value": 14}, {"value": 7}, {"value": 10}]},
            {"homeAway": "away", "team": team("HOU", "Houston", "Texans"), "score": "34",
             "linescores": [{"value": 7}, {"value": 10}, {"value": 14}, {"value": 3}]},
        ]}]},
    "boxscore": {"players": [{"statistics": [
        {"name": "passing", "keys": ["completions/passingAttempts", "passingYards", "passingTouchdowns", "interceptions"],
         "athletes": [athlete("C.J. Stroud", ["24/34", "312", "3", "1"])]},
        {"name": "rushing", "keys": ["rushingAttempts", "rushingYards", "rushingTouchdowns", "longRushing"],
         "athletes": [athlete("Jawhar Jordan", ["6", "43", "1", "25"])]},
        {"name": "receiving", "keys": ["receptions", "receivingYards", "receivingTouchdowns", "longReception"],
         "athletes": [athlete("Nico Collins", ["3", "74", "0", "42"]),
                      athlete("Jaylin Noel", ["4", "107", "1", "50"])]},
    ]}]},
    "scoringPlays": [
        {"scoringType": {"name": "touchdown"}, "text": "Foster Moreau 6 Yd pass from C.J. Stroud (Kick)"},
        {"scoringType": {"name": "field-goal"}, "text": "Ka'imi Fairbairn 25 Yd Field Goal"},
        {"scoringType": {"name": "touchdown"}, "text": "Jaylin Noel 13 Yd pass from C.J. Stroud (Kick)"},
        {"scoringType": {"name": "touchdown"}, "text": "Jawhar Jordan 25 Yd Rush (Kick)"},
        {"scoringType": {"name": "touchdown"}, "text": "Jaylin Noel 1 Yd pass from C.J. Stroud (Kick)"},
    ],
}


def pick(**kw):
    base = {"handle": "a", "league": "NFL", "event_id": "1", "game_status": "Final", "period": "game",
            "bet_type": "player_prop", "player": None, "team": None, "stat": None, "side": "over",
            "direction": 1, "line": float("nan"), "label": "x", "bet_key": "k", "parlay_group": None}
    return {**base, **kw}


@pytest.fixture
def boxes():
    return BoxScores(fetch=lambda url, params: SUMMARY, cache_dir=None)


def graded(boxes, **kw):
    return grade(pd.DataFrame([pick(**kw)]), boxes).iloc[0]


@pytest.mark.parametrize("kw, result, text", [
    (dict(player="Nico Collins", stat="receiving_yards", line=69.5), "Won", "74 rec yds"),
    (dict(player="Nico Collins", stat="receiving_yards", line=79.5), "Lost", "74 rec yds"),
    (dict(player="Nico Collins", stat="receiving_yards", line=79.5, side="under", direction=-1), "Won", "74 rec yds"),
    (dict(player="Nico Collins", stat="receptions", line=3.0), "Push", "3 rec"),
    (dict(player="CJ Stroud", stat="passing_yards", line=299.5), "Won", "312 pass yds"),
    (dict(player="C.J. Stroud", stat="completions", line=22.5), "Won", "24 completions"),
    (dict(player="Jawhar Jordan", stat="receiving_yards", line=0.5), "Lost", "0 rec yds"),  # played, no catches
    (dict(player="Jaylin Noel", stat="anytime_td", side="yes"), "Won", "2 TDs"),
    (dict(player="Jaylin Noel", stat="two_plus_tds", side="yes"), "Won", "2 TDs"),
    (dict(player="Nico Collins", stat="anytime_td", side="yes"), "Lost", "No TD"),
    (dict(player="Foster Moreau", stat="first_td", side="yes"), "Won", "Scored first TD"),
    (dict(player="Jaylin Noel", stat="first_td", side="yes"), "Lost", "First TD: Foster Moreau"),
    (dict(player="Somebody Else", stat="receiving_yards", line=10.5), "Ungraded", None),
])
def test_football_props(boxes, kw, result, text):
    g = graded(boxes, **kw)
    assert g["result"] == result
    if text:
        assert g["actual_text"] == text


@pytest.mark.parametrize("kw, result", [
    (dict(bet_type="moneyline", team="HOU", side="team", direction=0), "Won"),
    (dict(bet_type="moneyline", team="Jacksonville Jaguars", side="team", direction=0), "Lost"),
    (dict(bet_type="spread", team="JAX", side="team", direction=0, line=3.5), "Won"),   # lost by 3
    (dict(bet_type="spread", team="JAX", side="team", direction=0, line=3.0), "Push"),
    (dict(bet_type="spread", team="HOU", side="team", direction=0, line=-3.5), "Lost"),
    (dict(bet_type="total", side="over", line=64.5), "Won"),                          # 65
    (dict(bet_type="total", side="under", direction=-1, line=20.5, period="1h"), "Lost"),  # 31 in 1H
    (dict(bet_type="team_total", team="HOU", side="over", line=6.5, period="1q"), "Won"),  # 7 in Q1
])
def test_game_lines(boxes, kw, result):
    assert graded(boxes, **kw)["result"] == result


def test_not_final_or_unsupported(boxes):
    assert graded(boxes, game_status="Upcoming", player="Nico Collins", stat="receptions", line=2.5)["result"] == "Pending"
    assert graded(boxes, league="NBA", player="X", stat="points", line=20.5)["result"] == "Ungraded"
    assert graded(boxes, event_id=None, bet_type="moneyline", team="HOU")["result"] == "Ungraded"


def test_bet_rollup_and_tiers(boxes):
    df = pd.DataFrame([
        pick(handle="a", player="Nico Collins", stat="receiving_yards", line=49.5, bet_key="nc"),
        pick(handle="b", player="Nico Collins", stat="receiving_yards", line=79.5, bet_key="nc"),
        pick(handle="a", player="Jaylin Noel", stat="anytime_td", side="yes", bet_key="jn"),
        pick(handle="b", player="Jaylin Noel", stat="anytime_td", side="yes", bet_key="jn"),
        pick(handle="c", player="Jaylin Noel", stat="anytime_td", side="yes", bet_key="jn"),
    ])
    rep = results.results_report(grade(df, boxes))
    bets = rep["frames"]["Bet Results"].set_index("bet_key")
    assert bets.loc["nc", "result"] == "Split" and bets.loc["nc", "who_won"] == "a"
    assert bets.loc["jn", "result"] == "Won" and bets.loc["jn", "accounts"] == 3
    assert rep["summary"]["shared"] == {"won": 1, "lost": 0, "push": 0, "hit_rate": 1.0, "bets": 2}
    cappers = rep["frames"]["Capper Records"].set_index("handle")
    assert (cappers.loc["a", "won"], cappers.loc["b", "lost"]) == (2, 1)


def test_scorer_parsing():
    box = parse_summary(SUMMARY, "NFL", "1")
    assert box.td_scorers == ["foster moreau", "jaylin noel", "jawhar jordan", "jaylin noel"]
