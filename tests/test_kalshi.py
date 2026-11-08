"""Kalshi matching and pricing against a fake Kalshi feed (no network).

The cases mirror real ones found when matching every stored pick: Jacksonville is JAC on Kalshi,
playoff events list team names instead of codes, nicknames ("Kenny" Gainwell), back-to-back
playoff games, "Louisiana" vs "Louisiana Tech", and lines between two ladder rungs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from capper_tracker import kalshi
from capper_tracker.games import Game

T0 = datetime(2026, 10, 18, 17, 0, tzinfo=timezone.utc)   # 1:00pm ET kickoff
POST = datetime(2026, 10, 18, 13, 30, tzinfo=timezone.utc)


def mk(ticker, title, strike=None, result="", status="finalized", bid=None, ask=None):
    return {"ticker": ticker, "title": title, "yes_sub_title": title.split(":")[0] if ":" in title else title,
            "floor_strike": strike, "strike_type": "greater", "status": status, "result": result,
            "yes_bid_dollars": bid, "yes_ask_dollars": ask, "last_price_dollars": None, "volume_fp": "10",
            "occurrence_datetime": T0.isoformat()}


EVENTS = {
    "KXNFLGAME": [
        {"event_ticker": "KXNFLGAME-26OCT18HOUJAC", "title": "Houston vs Jacksonville", "sub_title": "HOU vs JAC (Oct 18)"},
        {"event_ticker": "KXNFLGAME-26OCT18LACKC", "title": "Los Angeles C vs Kansas City", "sub_title": "LAC vs KC (Oct 18)"},
    ],
    "KXWNBAGAME": [  # playoffs: names, not codes, and the same teams on back-to-back days
        {"event_ticker": "KXWNBAGAME-26OCT08GSLV", "title": "Game 3: Golden State vs Las Vegas", "sub_title": "Golden State vs Las Vegas (Oct 8)"},
        {"event_ticker": "KXWNBAGAME-26OCT07LVGS", "title": "Game 2: Las Vegas vs Golden State", "sub_title": "Las Vegas vs Golden State (Oct 7)"},
    ],
    "KXNCAAFGAME": [
        {"event_ticker": "KXNCAAFGAME-26OCT10ULLLT", "title": "Louisiana vs Louisiana Tech", "sub_title": "ULL vs LT (Oct 10)"},
    ],
}
MARKETS = {
    "KXNFLGAME-26OCT18HOUJAC": [mk("KXNFLGAME-26OCT18HOUJAC-HOU", "Houston", result="yes"),
                                mk("KXNFLGAME-26OCT18HOUJAC-JAC", "Jacksonville", result="no")],
    "KXNFLSPREAD-26OCT18HOUJAC": [mk("KXNFLSPREAD-26OCT18HOUJAC-JAC4", "Jacksonville wins by over 3.5 points?", 3.5, "no"),
                                  mk("KXNFLSPREAD-26OCT18HOUJAC-HOU4", "Houston wins by over 3.5 points?", 3.5, "no")],
    "KXNFLTOTAL-26OCT18HOUJAC": [mk("KXNFLTOTAL-26OCT18HOUJAC-45", "Over 44.5 points", 44.5, "yes")],
    "KXNFLTD-26OCT18HOUJAC": [mk("KXNFLTD-26OCT18HOUJAC-HOUJNOEL13-1", "Jaylin Noel: 1+ touchdowns", 0.5, "yes"),
                              mk("KXNFLTD-26OCT18HOUJAC-HOUJNOEL13-2", "Jaylin Noel: 2+ touchdowns", 1.5, "yes"),
                              mk("KXNFLTD-26OCT18HOUJAC-JACKGAINWELL1-1", "Kenny Gainwell: 1+ touchdowns", 0.5, "no")],
    "KXNFLRECYDS-26OCT18HOUJAC": [mk("KXNFLRECYDS-26OCT18HOUJAC-HOUNCOLLINS12-50", "Nico Collins: 50+ receiving yards", 49.5, "yes"),
                                  mk("KXNFLRECYDS-26OCT18HOUJAC-HOUNCOLLINS12-75", "Nico Collins: 75+ receiving yards", 74.5, "no"),
                                  mk("KXNFLRECYDS-26OCT18HOUJAC-HOUNCOLLINS12-100", "Nico Collins: 100+ receiving yards", 99.5, "no")],
    "KXWNBAGAME-26OCT07LVGS": [mk("KXWNBAGAME-26OCT07LVGS-LV", "Las Vegas", result="yes"),
                               mk("KXWNBAGAME-26OCT07LVGS-GS", "Golden State", result="no")],
    "KXNCAAFGAME-26OCT10ULLLT": [mk("KXNCAAFGAME-26OCT10ULLLT-ULL", "Louisiana", result="no"),
                                 mk("KXNCAAFGAME-26OCT10ULLLT-LT", "Louisiana Tech", result="yes")],
}
# Hourly candles: 25c at 13:00 UTC, 31c at 16:00 UTC (just before kickoff).
CANDLES = [{"end_period_ts": int(datetime(2026, 10, 18, h, 0, tzinfo=timezone.utc).timestamp()),
            "yes_bid": {"close_dollars": str(b)}, "yes_ask": {"close_dollars": str(b + 0.02)},
            "price": {"close_dollars": str(b + 0.01)}} for h, b in ((12, 0.22), (13, 0.24), (14, 0.26), (16, 0.30))]


def fake_fetch(path, params):
    if path == "/events":
        return {"events": EVENTS.get(params["series_ticker"], []), "cursor": None}
    if path.startswith("/events/"):
        t = path.split("/")[2]
        return {"event": {"title": t, "markets": MARKETS[t]}} if t in MARKETS else {}
    if path.endswith("/candlesticks"):
        return {"candlesticks": CANDLES}
    raise AssertionError(path)


def game(league, eid, start, away, home):
    return Game(league=league, event_id=eid, start=start, short=f"{away[0]} @ {home[0]}", venue="", status="Final",
                away_abbrs={away[0]}, home_abbrs={home[0]}, away_names=set(away[1:]), home_names=set(home[1:]))


SCHEDULE = SimpleNamespace(games={
    "NFL": [game("NFL", "1", T0, ("HOU", "HOUSTON", "HOUSTON TEXANS"), ("JAX", "JACKSONVILLE", "JACKSONVILLE JAGUARS"))],
    "WNBA": [game("WNBA", "2", datetime(2026, 10, 8, 1, 30, tzinfo=timezone.utc),
                  ("LV", "LAS VEGAS", "LAS VEGAS ACES"), ("GS", "GOLDEN STATE", "GOLDEN STATE VALKYRIES"))],
    "NCAAF": [game("NCAAF", "3", datetime(2026, 10, 10, 23, 30, tzinfo=timezone.utc),
                   ("UL", "LOUISIANA", "LOUISIANA RAGIN CAJUNS"), ("LT", "LOUISIANA TECH", "LOUISIANA TECH BULLDOGS"))],
})


@pytest.fixture
def matcher():
    client = kalshi.Kalshi(cache_dir=None, fetch=fake_fetch, min_interval=0)
    return kalshi.Matcher(client, SCHEDULE, datetime(2026, 9, 1, tzinfo=timezone.utc))


def pick(**kw):
    base = {"league": "NFL", "event_id": "1", "bet_type": "player_prop", "period": "game", "direction": 1,
            "line": None, "player": None, "team": None, "stat": None, "created_at": POST.isoformat()}
    return {**base, **kw}


@pytest.mark.parametrize("kw, ticker, side, quality", [
    (dict(player="Jaylin Noel", stat="anytime_td"), "KXNFLTD-26OCT18HOUJAC-HOUJNOEL13-1", "yes", "exact"),
    (dict(player="Jaylin Noel", stat="two_plus_tds"), "KXNFLTD-26OCT18HOUJAC-HOUJNOEL13-2", "yes", "exact"),
    (dict(player="Kenneth Gainwell", stat="anytime_td"), "KXNFLTD-26OCT18HOUJAC-JACKGAINWELL1-1", "yes", "exact"),
    (dict(player="Nico Collins", stat="receiving_yards", line=74.5), "KXNFLRECYDS-26OCT18HOUJAC-HOUNCOLLINS12-75", "yes", "exact"),
    (dict(player="Nico Collins", stat="receiving_yards", line=74.5, direction=-1), "KXNFLRECYDS-26OCT18HOUJAC-HOUNCOLLINS12-75", "no", "exact"),
    (dict(player="Nico Collins", stat="receiving_yards", line=59.5), "KXNFLRECYDS-26OCT18HOUJAC-HOUNCOLLINS12-50", "yes", "interpolated"),
    (dict(bet_type="moneyline", team="JAX"), "KXNFLGAME-26OCT18HOUJAC-JAC", "yes", "exact"),
    (dict(bet_type="spread", team="HOU", line=-3.5), "KXNFLSPREAD-26OCT18HOUJAC-HOU4", "yes", "exact"),
    (dict(bet_type="spread", team="JAX", line=3.5), "KXNFLSPREAD-26OCT18HOUJAC-HOU4", "no", "exact"),  # underdog = No on the favorite
    (dict(bet_type="total", line=44.5, direction=-1), "KXNFLTOTAL-26OCT18HOUJAC-45", "no", "exact"),
    (dict(league="WNBA", event_id="2", bet_type="moneyline", team="LV"), "KXWNBAGAME-26OCT07LVGS-LV", "yes", "exact"),
    (dict(league="NCAAF", event_id="3", bet_type="moneyline", team="LOUISIANA TECH"), "KXNCAAFGAME-26OCT10ULLLT-LT", "yes", "exact"),
])
def test_matches(matcher, kw, ticker, side, quality):
    hit = matcher.match(pick(**kw))
    assert (hit["kalshi_ticker"], hit["kalshi_side"], hit["kalshi_match"]) == (ticker, side, quality)


@pytest.mark.parametrize("kw, reason", [
    (dict(player="Nico Collins", stat="receiving_yards", line=120.5), "outside Kalshi's ladder"),
    (dict(player="Kameron Johnson", stat="anytime_td"), "not offered on Kalshi"),
    (dict(player="Nico Collins", stat="rushing_yards", line=5.5), "no rushing yards market"),
    (dict(league="SOCCER"), "no SOCCER markets"),
    (dict(event_id=None, team=None), "No Kalshi game"),
])
def test_misses_say_why(matcher, kw, reason):
    hit = matcher.match(pick(**kw))
    assert hit["kalshi_match"] == "none" and reason in hit["kalshi_note"]


def test_fallback_finds_game_by_team_names(matcher):
    hit = matcher.match(pick(event_id=None, bet_type="moneyline", team="Houston Texans", opponent="JAX"))
    assert hit["kalshi_ticker"] == "KXNFLGAME-26OCT18HOUJAC-HOU"


def test_prices_at_post_and_kickoff(matcher):
    hit = matcher.match(pick(player="Jaylin Noel", stat="anytime_td"))
    p = kalshi.price_pick(matcher.client, hit, POST.isoformat(), T0.isoformat())
    assert p["kalshi_prob_post"] == pytest.approx(0.25)    # 13:00 candle: bid .24 / ask .26
    assert p["kalshi_cost_post"] == pytest.approx(0.26)    # buying Yes at the ask
    assert p["kalshi_prob_close"] == pytest.approx(0.31)   # last candle before kickoff
    under = matcher.match(pick(player="Nico Collins", stat="receiving_yards", line=74.5, direction=-1))
    q = kalshi.price_pick(matcher.client, under, POST.isoformat(), T0.isoformat())
    assert q["kalshi_prob_post"] == pytest.approx(0.75) and q["kalshi_cost_post"] == pytest.approx(0.76)


def test_attach_prices_and_settlement():
    client = kalshi.Kalshi(cache_dir=None, fetch=fake_fetch, min_interval=0)
    df = pd.DataFrame([pick(player="Jaylin Noel", stat="anytime_td", label="a", game_time_et=T0.isoformat()),
                       pick(player="Nico Collins", stat="receiving_yards", line=74.5, label="b", game_time_et=T0.isoformat()),
                       pick(player="Nico Collins", stat="receiving_yards", line=59.5, label="c", game_time_et=T0.isoformat())])
    out = kalshi.attach_prices(df, SCHEDULE, client, since=datetime(2026, 9, 1, tzinfo=timezone.utc), workers=1)
    assert list(out["kalshi_result"][:2]) == ["Won", "Lost"]
    assert pd.isna(out["kalshi_result"].iloc[2])                  # interpolated matches don't settle
    assert pd.isna(out["kalshi_cost_post"].iloc[2])               # no single contract at 59.5
    assert out["kalshi_move"].iloc[0] == pytest.approx(0.06)
