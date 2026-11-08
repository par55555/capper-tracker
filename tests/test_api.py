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


def test_report_serializes_frames(client, monkeypatch):
    c, _ = client
    frames = {
        "Top Consensus": pd.DataFrame({"bet": ["Chase over rec yds"], "accounts": [3],
                                       "game_time_et": [pd.Timestamp("2026-10-11T17:00Z")],
                                       "median_odds_straight": [float("nan")]}),
        "Picks": pd.DataFrame({"handle": ["a", "b"]}),
        "Tweets": pd.DataFrame({"extracted": [1, -1, 0]}),
        "Conflicts": pd.DataFrame(),
    }
    monkeypatch.setattr(api.pipeline, "build_frames", lambda conn, window: frames)
    body = c.get("/api/report", params={"week": 5}).json()
    assert body["window"]["name"] == "week05"
    assert body["summary"] == {"posts": 3, "picks": 2, "accounts_with_picks": 2, "shared_bets": 1,
                               "conflicts": 0, "failed_posts": 1, "pending_posts": 1}
    row = body["frames"]["Top Consensus"]["rows"][0]
    assert row["median_odds_straight"] is None and row["game_time_et"].startswith("2026-10-11T17:00")
    assert body["frames"]["Conflicts"] == {"columns": [], "rows": []}


def test_report_validates_window(client):
    c, _ = client
    assert c.get("/api/report", params={"week": 5, "days": 7}).status_code == 400
    assert c.get("/api/report", params={"days": 0}).status_code == 400
    assert c.post("/api/runs", json={"command": "rm -rf"}).status_code == 422
