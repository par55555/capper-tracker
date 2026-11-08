"""End-to-end test with no network: fake X responses, fake Claude tool calls, real DB/analysis/export."""
from __future__ import annotations

from datetime import timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
from openpyxl import load_workbook

from capper_tracker import db, pipeline
from capper_tracker.analyze import build_report
from capper_tracker.config import nfl_week_to_date, week_window
from capper_tracker.extractor import PickExtractor, parse_response
from capper_tracker.x_client import XClient

WEEK = 5
START, END = week_window(WEEK)
WINDOW = nfl_week_to_date(WEEK, now=END)  # a completed week


def ts(hours: int) -> str:
    return (START + timedelta(hours=hours)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


USERS = {"sharp_sam": "1", "prop_pete": "2", "lock_lucy": "3"}
TIMELINES = {
    "1": [{"id": "101", "created_at": ts(30), "text": "Chase o74.5 rec yds 2u, Burrow o 1.5 pass TDs"},
          {"id": "102", "created_at": ts(31), "text": "Went 4-1 yesterday ✅"},
          {"id": "103", "created_at": ts(60), "text": "Reminder: Chase o77.5 still my fav"},
          {"id": "104", "created_at": ts(61), "text": "Changed my mind, Burrow under 1.5 TDs"}],
    "2": [{"id": "201", "created_at": ts(40), "text": "Ja'Marr Chase over 79.5 receiving, CIN -3"},
          {"id": "202", "created_at": ts(41), "text": "reply to a fan", "in_reply_to_user_id": "999"},
          {"id": "203", "created_at": ts(42), "text": "SGP: J. Allen o 34.5 rush yds + Cook o 59.5 rush"}],
    "3": [{"id": "301", "created_at": ts(50), "text": "Chase under 74.5 rec. Josh Allen o 30.5 rush yds"}],
}

# What Claude would return for each tweet.
PICKS = {
    "101": ("new_pick", [
        dict(league="NFL", bet_type="player_prop", period="game", player="Ja'Marr Chase", team="CIN",
             stat="receiving_yards", side="over", line=74.5, odds=None, units=2, parlay_group=None, snippet="Chase o74.5"),
        dict(league="NFL", bet_type="player_prop", period="game", player="Joe Burrow", team="CIN",
             stat="passing_tds", side="over", line=1.5, odds=None, units=None, parlay_group=None, snippet="Burrow o 1.5")]),
    "102": ("result_recap", []),
    "103": ("new_pick", [
        dict(league="NFL", bet_type="player_prop", period="game", player="Ja'Marr Chase", team="CIN",
             stat="receiving_yards", side="over", line=77.5, odds=None, units=None, parlay_group=None, snippet="Chase o77.5")]),
    "104": ("new_pick", [
        dict(league="NFL", bet_type="player_prop", period="game", player="Joe Burrow", team="CIN",
             stat="passing_tds", side="under", line=1.5, odds=None, units=None, parlay_group=None, snippet="Burrow under 1.5")]),
    "201": ("new_pick", [
        dict(league="NFL", bet_type="player_prop", period="game", player="Ja'Marr Chase", team="CIN",
             stat="receiving_yards", side="over", line=79.5, odds=-115, units=None, parlay_group=None, snippet="Chase over 79.5"),
        dict(league="NFL", bet_type="spread", period="game", player=None, team="CIN",
             stat=None, side=None, line=-3, odds=None, units=None, parlay_group=None, snippet="CIN -3")]),
    "203": ("new_pick", [
        dict(league="NFL", bet_type="player_prop", period="game", player="J. Allen", team="BUF",
             stat="rushing_yards", side="over", line=34.5, odds=None, units=None, parlay_group="p1", snippet="J. Allen o 34.5"),
        dict(league="NFL", bet_type="player_prop", period="game", player="James Cook", team="BUF",
             stat="rushing_yards", side="over", line=59.5, odds=None, units=None, parlay_group="p1", snippet="Cook o 59.5")]),
    "301": ("new_pick", [
        dict(league="NFL", bet_type="player_prop", period="game", player="Ja'Marr Chase", team="CIN",
             stat="receiving_yards", side="under", line=74.5, odds=None, units=None, parlay_group=None, snippet="Chase under"),
        dict(league="NFL", bet_type="player_prop", period="game", player="Josh Allen", team="BUF",
             stat="rushing_yards", side="over", line=30.5, odds=None, units=None, parlay_group=None, snippet="Josh Allen o 30.5")]),
}


class FakeResp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.headers = payload, status, {}
        self.ok = status < 400
        self.text = str(payload)

    def json(self):
        return self.payload


class FakeXSession:
    headers: dict = {}

    def get(self, url, params=None, timeout=None):
        if url.endswith("/users/by"):
            names = params["usernames"].split(",")
            return FakeResp({"data": [{"id": USERS[n], "username": n, "name": n.title()} for n in names]})
        uid = url.split("/users/")[1].split("/")[0]
        return FakeResp({"data": TIMELINES[uid], "meta": {}})


class FakeAnthropic:
    def __init__(self):
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        text = kw["messages"][0]["content"][-1]["text"]
        tid = next(t["id"] for tl in TIMELINES.values() for t in tl if t["text"] in text)
        ttype, picks = PICKS[tid]
        block = SimpleNamespace(type="tool_use", name="record_picks", input={"tweet_type": ttype, "picks": picks})
        return SimpleNamespace(content=[block], stop_reason="tool_use")


def espn_event(eid, when, away, home, state="pre", venue="Stadium"):
    def team(t):
        abbr, loc, nick = t
        return {"abbreviation": abbr, "displayName": f"{loc} {nick}", "shortDisplayName": nick,
                "location": loc, "name": nick}
    return {"id": eid, "date": when, "shortName": f"{away[0]} @ {home[0]}",
            "status": {"type": {"state": state}},
            "competitions": [{"venue": {"fullName": venue}, "competitors": [
                {"homeAway": "home", "team": team(home)}, {"homeAway": "away", "team": team(away)}]}]}


FAKE_ESPN = {
    ("football/nfl", "20261011"): [
        espn_event("n1", "2026-10-11T17:00Z", ("CIN", "Cincinnati", "Bengals"), ("PIT", "Pittsburgh", "Steelers")),
        espn_event("n2", "2026-10-11T20:25Z", ("BUF", "Buffalo", "Bills"), ("NE", "New England", "Patriots")),
    ],
}


def fake_espn(url, params):
    path = url.split("/sports/")[1].rsplit("/scoreboard", 1)[0]
    return {"events": FAKE_ESPN.get((path, params["dates"]), [])}


@pytest.fixture(autouse=True)
def offline_espn(monkeypatch):
    """Keep tests offline: every Schedule reads the fake ESPN feed above."""
    from capper_tracker import games
    monkeypatch.setattr(games.Schedule, "_http_get", staticmethod(fake_espn))
    monkeypatch.setattr(pipeline, "SCOREBOARD_CACHE", None)  # don't read real cached scoreboards


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "test.db")


def run_pipeline(conn, tmp_path):
    accounts = tmp_path / "accounts.txt"
    accounts.write_text("# test\n@Sharp_Sam\nprop_pete\nlock_lucy  # comment\n")
    x = XClient("fake", session=FakeXSession())
    fstats = pipeline.fetch(conn, x, WINDOW, accounts_file=accounts)
    estats = pipeline.extract(conn, PickExtractor(model="test", client=FakeAnthropic()), WINDOW)
    return fstats, estats, pipeline.report(conn, WINDOW, tmp_path / "out")


def report_frames(conn):
    pipeline._FRAMES_CACHE.clear()
    return pipeline._frames(conn, WINDOW)


def test_end_to_end(conn, tmp_path):
    fstats, estats, path = run_pipeline(conn, tmp_path)

    assert fstats["new_posts"] == 7                  # reply to a fan is dropped
    assert fstats["est_x_cost_usd"] == pytest.approx(8 * 0.005 + 3 * 0.01, abs=0.01)
    assert estats["by_type"] == {"new_pick": 6, "result_recap": 1}
    assert estats["picks"] == 10

    frames = report_frames(conn)
    bets = frames["All Bets"].set_index("bet")

    # sharp_sam posted Chase over twice (74.5 then 77.5): counted once, repeat recorded
    chase_over = bets.loc["Ja'Marr Chase over receiving yards"]
    assert chase_over["accounts"] == 2
    assert chase_over["repeat_posts"] == 1
    assert chase_over["lines"] == "77.5 to 79.5"     # latest line per account

    # sharp_sam flipped Burrow over -> under: only the under counts
    assert "Joe Burrow over passing TDs" not in bets.index
    assert bets.loc["Joe Burrow under passing TDs", "accounts"] == 1
    picks = frames["Picks"]
    assert picks.loc[picks["label"].str.startswith("Joe Burrow"), "switched_side"].all()

    # "J. Allen" and "Josh Allen" on BUF merge into one player
    assert bets.loc["Josh Allen over rushing yards", "accounts"] == 2

    themes = frames["Team Themes"].set_index("team")
    assert themes.loc["BUF", "theme"] == "Rushing threat"
    assert themes.loc["BUF", "run_net"] == 2
    assert themes.loc["CIN", "pass_net"] == 0        # over: sam, pete; under: lucy, sam (Burrow)

    conflicts = frames["Conflicts"]
    assert len(conflicts) == 1 and conflicts.iloc[0]["under_or_no"] == "lock_lucy"

    wb = load_workbook(path)
    assert path.name.startswith("week05_consensus_")
    assert wb.sheetnames == ["Top Consensus", "Team Themes", "Players", "All Bets", "Already Played",
                             "Conflicts", "Picks", "Tweets", "Guide"]
    top = frames["Top Consensus"].set_index("bet")
    assert top.loc["Ja'Marr Chase over receiving yards", "what_it_means"].startswith(
        "2 accounts like Ja'Marr Chase (CIN, NFL) to record over 77.5 to 79.5 receiving yards")
    assert top.loc["Ja'Marr Chase over receiving yards", "game"] == "CIN @ PIT"
    assert top.loc["Josh Allen over rushing yards", "game"] == "BUF @ NE"
    assert top.loc["Josh Allen over rushing yards", "game_status"] == "Upcoming"
    assert list(frames["Top Consensus"].columns[:5]) == ["league", "game_time_et", "game", "game_status", "bet"]
    tweets = frames["Tweets"].set_index("tweet_id")
    assert tweets.loc["102", "post_type"].startswith("Recap")


def test_posts_outside_window_are_ignored(conn, tmp_path):
    run_pipeline(conn, tmp_path)
    early = nfl_week_to_date(WEEK, now=START + timedelta(hours=35))   # e.g. a run on Wed 11am
    picks = db.picks_frame(conn, early.start_utc, early.end_utc)
    assert set(picks["tweet_id"]) == {"101"}


def test_rerun_is_idempotent(conn, tmp_path):
    run_pipeline(conn, tmp_path)
    fstats, estats, _ = run_pipeline(conn, tmp_path)
    assert fstats["new_posts"] == 0 and estats["posts"] == 0
    assert conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 10


def test_recap_never_yields_picks():
    block = SimpleNamespace(type="tool_use", name="record_picks",
                            input={"tweet_type": "result_recap", "picks": [{"bet_type": "spread"}]})
    assert parse_response(SimpleNamespace(content=[block])).picks == []


def test_summary_and_latest_copy(conn, tmp_path):
    _, _, path = run_pipeline(conn, tmp_path)
    assert (path.parent / "latest_consensus.xlsx").exists()
    s = pipeline.summary(conn, WINDOW)
    assert s["picks"] == 8 and s["accounts_with_picks"] == 3 and s["shared_bets"] == 2
    assert s["top"][0].endswith("(2)")
    assert "BUF rushing threat" in s["themes"]


def test_schedule_plist():
    from capper_tracker import schedule
    plist = schedule.build_plist("/opt/py/bin/python")
    assert plist["ProgramArguments"] == ["/opt/py/bin/python", "-m", "capper_tracker", "run", "--notify"]
    times = {(d["Weekday"], d["Hour"], d["Minute"]) for d in plist["StartCalendarInterval"]}
    # Thursdays and Sundays only, ~30 min before each NFL kickoff slot
    assert times == {(4, 19, 45), (0, 9, 0), (0, 12, 30), (0, 15, 35), (0, 19, 50)}


def test_notify_quoting_is_safe():
    from capper_tracker.notify import _osa_quote
    assert _osa_quote('Ja\'Marr "Chase" o74.5') == '"Ja\'Marr \\"Chase\\" o74.5"'


def _bad_request(msg):
    import anthropic
    try:
        import httpx2 as httpx
    except ImportError:
        import httpx
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError(msg, response=httpx.Response(400, request=req), body=None)


def test_falls_back_when_forced_tool_choice_unsupported():
    calls = []

    class Client:
        def __init__(self):
            self.messages = SimpleNamespace(create=self.create)

        def create(self, **kw):
            calls.append(kw["tool_choice"]["type"])
            if kw["tool_choice"]["type"] == "tool":
                raise _bad_request('tool_choice: type "tool" and "any" are not supported for this model.')
            if len(calls) == 2:  # first auto call: model answers in prose
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="no picks")])
            block = SimpleNamespace(type="tool_use", name="record_picks",
                                    input={"tweet_type": "promo", "picks": []})
            return SimpleNamespace(content=[block])

    ex = PickExtractor(model="m", client=Client())
    result = ex.extract(handle="a", created_at="x", week=5, text="use code SAVE20", media_urls=[])
    assert result.tweet_type == "promo"
    assert calls == ["tool", "auto", "auto"] and ex.forced_tool_choice is False



def test_other_sports_are_described_and_never_merged():
    from capper_tracker.analyze import build_report
    from capper_tracker.normalize import clean_pick
    raw = [
        ("a", dict(league="MLB", bet_type="player_prop", player="Fernando Tatis Jr.", team="SD",
                   stat="home_runs", stat_detail="Home runs", side="over", line=1, snippet="1+ home runs")),
        ("b", dict(league="MLB", bet_type="player_prop", player="Fernando Tatis Jr.", team="SD",
                   stat="stolen_bases", stat_detail="Stolen bases", side="over", line=0.5, snippet="1+ SB")),
        ("c", dict(league="MLB", bet_type="player_prop", player="Fernando Tatis Jr.", team="SD",
                   stat="home_runs", stat_detail="Home runs", side="yes", snippet="to hit a home run",
                   parlay_group="p1", odds=1285)),
        ("a", dict(league="WNBA", bet_type="player_prop", player="A'ja Wilson", team="LVA",
                   stat="other", stat_detail="Free throws made", side="over", line=5.5, snippet="o5.5 FTM")),
        ("b", dict(league="WNBA", bet_type="player_prop", player="A'ja Wilson", team="LVA",
                   stat="other", stat_detail="Steals + blocks", side="over", line=2.5, snippet="o2.5 stocks")),
        ("c", dict(league="NHL", bet_type="player_prop", player="Claude Giroux", team="OTT",
                   stat="anytime_goal", stat_detail="Anytime goal", side="yes", snippet="Anytime Goal Scorer")),
        ("d", dict(league="NHL", bet_type="player_prop", player="Claude Giroux", team="OTT",
                   stat="anytime_goal", stat_detail="Anytime goal", side="yes", snippet="AGS", odds=250)),
    ]
    rows = []
    for i, (handle, r) in enumerate(raw):
        p = clean_pick(r)
        p.update(handle=handle, tweet_id=str(i), created_at=f"2026-10-0{7 if i < 5 else 8}T12:00:00+00:00",
                 url="u", nfl_week=5)
        rows.append(p)
    frames = build_report(pd.DataFrame(rows), pd.DataFrame())
    bets = frames["All Bets"].set_index("bet")
    hr = bets.loc["[MLB] Fernando Tatis Jr. over home runs"]
    assert hr["accounts"] == 2 and hr["league"] == "MLB"
    assert hr["what_it_means"] == ("2 accounts like Fernando Tatis Jr. (SD, MLB) to record 1+ home runs; "
                                   "1 of 2 as parlay legs.")
    assert pd.isna(hr["median_odds_straight"])          # the 1285 parlay price is not used
    assert bets.loc["[MLB] Fernando Tatis Jr. over stolen bases", "accounts"] == 1
    assert "[WNBA] A'ja Wilson over free throws made" in bets.index
    assert "[WNBA] A'ja Wilson over steals + blocks" in bets.index   # different 'other' markets stay apart
    goal = bets.loc["[NHL] Claude Giroux anytime goal"]
    assert goal["what_it_means"] == ("2 accounts like Claude Giroux (OTT, NHL) to score a goal; "
                                     "typical straight-bet price +250.")
    assert frames["Team Themes"].empty                    # pass/run themes are NFL-only



def test_games_matched_across_sports_and_formats():
    from datetime import date, datetime, timezone
    from capper_tracker.games import Schedule, attach_games
    feed = {
        ("baseball/mlb", "20261006"): [espn_event("m0", "2026-10-06T23:10Z", ("SD", "San Diego", "Padres"),
                                                  ("MIL", "Milwaukee", "Brewers"), state="post")],
        ("baseball/mlb", "20261007"): [espn_event("m1", "2026-10-07T23:10Z", ("SD", "San Diego", "Padres"),
                                                  ("MIL", "Milwaukee", "Brewers"))],
        ("basketball/wnba", "20261007"): [espn_event("w1", "2026-10-08T00:00Z", ("NY", "New York", "Liberty"),
                                                     ("LV", "Las Vegas", "Aces"))],
        ("hockey/nhl", "20261008"): [espn_event("h1", "2026-10-08T23:00Z", ("OTT", "Ottawa", "Senators"),
                                                ("MTL", "Montreal", "Canadiens"))],
    }
    sched = Schedule(fetch=lambda url, params: {"events": feed.get(
        (url.split("/sports/")[1].rsplit("/scoreboard", 1)[0], params["dates"]), [])})
    picks = pd.DataFrame([
        # posted Wed afternoon -> Wednesday's game, not Tuesday's finished one
        dict(league="MLB", team="SD", created_at="2026-10-07T16:00:00+00:00"),
        dict(league="WNBA", team="LVA", created_at="2026-10-07T16:00:00+00:00"),        # ESPN uses LV
        dict(league="WNBA", team="NEW YORK LIBERTY", created_at="2026-10-07T16:00:00+00:00"),
        dict(league="NHL", team="Ottawa Senators", created_at="2026-10-08T12:00:00+00:00"),
        dict(league="NHL", team="ZZZ", created_at="2026-10-08T12:00:00+00:00"),         # unknown team
    ])
    out = attach_games(picks, sched, date(2026, 10, 6), date(2026, 10, 9))
    assert list(out["game"].fillna("")) == ["SD @ MIL", "NY @ LV", "NY @ LV", "OTT @ MTL", ""]
    assert out.loc[0, "game_status"] == "Upcoming"
    assert datetime.fromisoformat(out.loc[0, "game_time_et"]) == datetime(2026, 10, 7, 23, 10, tzinfo=timezone.utc)


def test_espn_outage_leaves_columns_blank():
    from datetime import date
    from capper_tracker.games import Schedule, attach_games
    def down(url, params):
        raise ConnectionError("no network")
    out = attach_games(pd.DataFrame([dict(league="NFL", team="KC", created_at="2026-10-07T16:00:00+00:00")]),
                       Schedule(fetch=down), date(2026, 10, 6), date(2026, 10, 9))
    assert out["game"].isna().all() and "game_time_et" in out.columns


def test_old_picks_get_stats_from_snippet_without_reextraction():
    """Picks stored as plain 'other' before the multi-sport update are fixed at report time, for free."""
    from capper_tracker.analyze import renormalize
    rows = [
        ("MLB", "Fernando Tatis Jr.", "Fernando Tatis Jr. 1+ home runs", "over", 0.5),
        ("MLB", "Fernando Tatis Jr.", "1+ Fernando Tatis Jr. Stolen Bases", "over", 0.5),
        ("MLB", "Jo Adell", "Jo Adell PLAYER TO RECORD 2+ HITS + RUNS + RBIS", "yes", 2.0),
        ("WNBA", "Sabrina Ionescu", "Sabrina Ionescu TO SCORE 15+ POINTS", "over", 15.0),
        ("NHL", "Quinn Hughes", "Quinn Hughes 1+ ASSISTS", "over", 0.5),
        ("NHL", "Claude Giroux", "Claude Giroux Anytime Goal Scorer", "yes", None),
        ("NHL", "Bryan Rust", "Bryan Rust 2+ Yes", "yes", 2.0),
    ]
    old = pd.DataFrame([dict(league=lg, bet_type="player_prop", player=pl, snippet=sn, side=sd, line=ln,
                             stat="other", stat_detail=None, period=None, team=None, opponent=None,
                             away_team=None, home_team=None, odds=None, units=None, parlay_group="p1")
                        for lg, pl, sn, sd, ln in rows])
    new = renormalize(old)
    assert list(new["label"]) == [
        "[MLB] Fernando Tatis Jr. over home runs",
        "[MLB] Fernando Tatis Jr. over stolen bases",
        "[MLB] Jo Adell over hits + runs + RBIs",
        "[WNBA] Sabrina Ionescu over points",
        "[NHL] Quinn Hughes over assists",
        "[NHL] Claude Giroux anytime goal",
        "[NHL] Bryan Rust over unrecognized market (see snippet)",
    ]
    assert list(new["line"].fillna(-1)) == [0.5, 0.5, 1.5, 14.5, 0.5, -1, 1.5]
    assert new.loc[0, "bet_key"] != new.loc[1, "bet_key"]      # home runs vs stolen bases stay separate



def test_finished_games_leave_top_consensus():
    """Settled bets (e.g. Thursday night's game on a Sunday run) must not crowd out upcoming ones."""
    from datetime import date
    from capper_tracker.analyze import build_report
    from capper_tracker.games import Schedule
    feed = {("football/nfl", "20261008"): [espn_event("t", "2026-10-09T00:15Z", ("TB", "Tampa Bay", "Buccaneers"),
                                                      ("DAL", "Dallas", "Cowboys"), state="post")],
            ("football/nfl", "20261011"): [espn_event("s", "2026-10-11T20:25Z", ("DET", "Detroit", "Lions"),
                                                      ("ARI", "Arizona", "Cardinals"))]}
    sched = Schedule(fetch=lambda url, params: {"events": feed.get(
        (url.split("/sports/")[1].rsplit("/scoreboard", 1)[0], params["dates"]), [])})
    def pick(handle, player, team, stat, side, line, posted):
        return dict(handle=handle, tweet_id=handle + player, created_at=posted, url="u", nfl_week=5,
                    league="NFL", bet_type="player_prop", period="game", player=player, team=team,
                    opponent=None, away_team=None, home_team=None, stat=stat, stat_detail=None, side=side,
                    line=line, odds=None, units=None, parlay_group=None, snippet="s")
    thu, sun = "2026-10-08T16:00:00+00:00", "2026-10-11T14:00:00+00:00"
    rows = [pick(h, "Chris Godwin", "TB", "receiving_yards", "over", 34.5, thu) for h in "abcde"]
    rows += [pick(h, "Jahmyr Gibbs", "DET", "anytime_td", "yes", None, sun) for h in "ab"]
    frames = build_report(pd.DataFrame(rows), pd.DataFrame(), schedule=sched,
                          game_days=(date(2026, 10, 6), date(2026, 10, 14)))
    assert list(frames["Top Consensus"]["bet"]) == ["Jahmyr Gibbs anytime touchdown"]
    assert list(frames["Already Played"]["bet"]) == ["Chris Godwin over receiving yards"]
    assert "TB" not in set(frames["Team Themes"].get("team", []))   # finished game left out of themes


def test_fetch_respects_max_posts(conn, tmp_path):
    accounts = tmp_path / "accounts.txt"
    accounts.write_text("sharp_sam\nprop_pete\n")
    asked = []

    class StubX:
        estimated_cost = 0.0

        def lookup_users(self, handles):
            return {h: {"id": str(i), "name": h} for i, h in enumerate(handles, 1)}

        def user_tweets(self, user_id, since_id, start_time, end_time, max_tweets):
            asked.append(max_tweets)
            n = 5 if user_id == "1" else 2           # sam is posting a lot, pete isn't
            return [{"id": f"{user_id}{i:03d}", "created_at": ts(30 + i), "text": "x", "media_urls": []}
                    for i in range(n)][:max_tweets]

    stats = pipeline.fetch(conn, StubX(), WINDOW, accounts_file=accounts, max_tweets=5)
    assert asked == [5, 5]
    assert stats["new_posts"] == 7 and stats["capped_accounts"] == ["sharp_sam"]


def test_cli_cap_bounds_and_run_lock(tmp_path):
    import fcntl
    import os
    import subprocess
    import sys
    env = {**os.environ, "CAPPER_DB": str(tmp_path / "cappers.db")}
    bad = subprocess.run([sys.executable, "-m", "capper_tracker", "report", "--max-posts", "3"],
                         capture_output=True, text=True, env=env)
    assert bad.returncode == 2 and "--max-posts" in bad.stderr
    with open(tmp_path / ".run.lock", "w") as fh:            # another run holds the lock
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = subprocess.run([sys.executable, "-m", "capper_tracker", "report", "--no-excel"],
                           capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "still in progress" in r.stdout
