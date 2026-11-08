"""Web API tests: no network, temp DB and accounts file."""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from capper_tracker import api, db


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    real_connect = db.connect
    monkeypatch.setattr(api.db, "connect", lambda: real_connect(path))
    accounts = tmp_path / "accounts.txt"
    accounts.write_text("# comment header\n\nalpha\nbeta  # note\n")
    monkeypatch.setattr(api, "ACCOUNTS_FILE", accounts)
    api._report_cache.clear()
    return TestClient(api.app), accounts


def test_accounts_roundtrip_keeps_header(client):
    c, accounts = client
    assert [a["handle"] for a in c.get("/api/accounts").json()["accounts"]] == ["alpha", "beta"]
    r = c.put("/api/accounts", json={"handles": ["@Gamma", "alpha", "ALPHA", " "]})
    assert r.status_code == 200
    assert accounts.read_text() == "# comment header\n\nGamma\nalpha\n"


@pytest.mark.parametrize("handles", [["bad handle"], ["x" * 16], []])
def test_accounts_rejects_bad_input(client, handles):
    c, accounts = client
    before = accounts.read_text()
    assert c.put("/api/accounts", json={"handles": handles}).status_code == 400
    assert accounts.read_text() == before


def test_week_serializes_frames(client, monkeypatch):
    c, _ = client
    frames = {
        "Top Consensus": pd.DataFrame({"bet": ["Chase over rec yds"], "accounts": [3],
                                       "game_time_et": [pd.Timestamp("2026-10-11T17:00Z")],
                                       "median_odds_straight": [float("nan")], "kalshi_now": [0.62]}),
        "Picks": pd.DataFrame({"handle": ["a", "b"], "kalshi_prob_post": [0.5, None]}),
        "Tweets": pd.DataFrame({"extracted": [1, -1, 0]}),
        "Conflicts": pd.DataFrame(),
    }
    res = {"summary": {"shared": {}, "all_picks": {}, "pending": 0, "ungraded": 0},
           "frames": {"Bet Results": pd.DataFrame({"bet": ["x"], "result": ["Won"]})}}
    monkeypatch.setattr(api.pipeline, "week_bundle", lambda conn, window: {"frames": frames, "results": res})
    body = c.get("/api/week", params={"week": 5}).json()
    assert body["window"]["name"] == "week05" and body["window"]["live"] is False
    assert body["summary"] == {"posts": 3, "picks": 2, "accounts_with_picks": 2, "shared_bets": 1,
                               "conflicts": 0, "failed_posts": 1, "pending_posts": 1, "priced": 1}
    row = body["frames"]["Top Consensus"]["rows"][0]
    assert row["median_odds_straight"] is None and row["game_time_et"].startswith("2026-10-11T17:00")
    assert row["kalshi_now"] == 0.62
    assert body["frames"]["Conflicts"] == {"columns": [], "rows": []}
    assert body["results"]["Bet Results"]["rows"][0]["result"] == "Won"


def test_exports_only_serves_report_files(client, monkeypatch, tmp_path):
    c, _ = client
    (tmp_path / "week05_consensus_20261011_1200.xlsx").write_bytes(b"x")
    (tmp_path / "week06_consensus_20261018_1200.xlsx").write_bytes(b"y")
    monkeypatch.setattr(api, "EXPORT_DIR", tmp_path)
    assert [f["name"] for f in c.get("/api/exports", params={"week": 5}).json()["exports"]] == \
        ["week05_consensus_20261011_1200.xlsx"]
    assert c.get("/api/exports/week05_consensus_20261011_1200.xlsx").content == b"x"
    assert c.get("/api/exports/..%2F.env").status_code == 404
    assert c.get("/api/exports/notes.txt").status_code == 404


def test_week_validates_window(client):
    c, _ = client
    assert c.get("/api/week", params={"week": 5, "days": 7}).status_code == 400
    assert c.get("/api/week", params={"days": 0}).status_code == 400
    assert c.post("/api/runs", json={"command": "rm -rf"}).status_code == 422
