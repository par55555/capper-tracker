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
    return build_report(db.picks_frame(conn, WINDOW.start_utc, WINDOW.end_utc),
                        db.tweets_frame(conn, WINDOW.start_utc, WINDOW.end_utc))


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
    assert "Joe Burrow over passing tds" not in bets.index
    assert bets.loc["Joe Burrow under passing tds", "accounts"] == 1
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
    assert wb.sheetnames == ["Top Consensus", "Team Themes", "Players", "All Bets",
                             "Conflicts", "Picks", "Tweets"]


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
