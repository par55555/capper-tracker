"""SQLite storage. The DB is the source of truth; Excel files are exports of it."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    handle             TEXT PRIMARY KEY,          -- lowercase, no @
    user_id            TEXT,
    display_name       TEXT,
    weight             REAL NOT NULL DEFAULT 1.0, -- reserved; all accounts count equally today
    last_seen_tweet_id TEXT,
    active             INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tweets (
    tweet_id      TEXT PRIMARY KEY,
    handle        TEXT NOT NULL REFERENCES accounts(handle),
    created_at    TEXT NOT NULL,                  -- ISO8601 UTC
    nfl_week      INTEGER NOT NULL,
    text          TEXT,
    media_urls    TEXT NOT NULL DEFAULT '[]',     -- JSON list
    url           TEXT,
    fetched_at    TEXT NOT NULL,
    extracted     INTEGER NOT NULL DEFAULT 0,     -- 0 pending, 1 done, -1 failed
    tweet_type    TEXT,                           -- new_pick | result_recap | promo | other
    extract_error TEXT
);
CREATE INDEX IF NOT EXISTS ix_tweets_week ON tweets(nfl_week, extracted);

CREATE TABLE IF NOT EXISTS picks (
    pick_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    tweet_id     TEXT NOT NULL REFERENCES tweets(tweet_id) ON DELETE CASCADE,
    handle       TEXT NOT NULL,
    nfl_week     INTEGER NOT NULL,
    league       TEXT,
    bet_type     TEXT,
    period       TEXT,
    player       TEXT,
    player_key   TEXT,
    team         TEXT,
    opponent     TEXT,
    away_team    TEXT,
    home_team    TEXT,
    stat         TEXT,
    stat_detail  TEXT,                            -- the market in plain words ("Home runs")
    category     TEXT,
    side         TEXT,
    direction    INTEGER,
    line         REAL,
    odds         INTEGER,
    units        REAL,
    parlay_group TEXT,
    bet_key      TEXT NOT NULL,
    label        TEXT,
    snippet      TEXT,
    model        TEXT,
    extracted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_picks_week ON picks(nfl_week, bet_key);
"""

PICK_COLUMNS = [
    "league", "bet_type", "period", "player", "player_key", "team", "opponent", "away_team",
    "home_team", "stat", "stat_detail", "category", "side", "direction", "line", "odds", "units",
    "parlay_group", "bet_key", "label", "snippet",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a database was first created."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(picks)")}
    if "stat_detail" not in cols:
        with conn:
            conn.execute("ALTER TABLE picks ADD COLUMN stat_detail TEXT")


# --- accounts ---------------------------------------------------------------

def sync_account_list(conn: sqlite3.Connection, entries: dict[str, float]) -> None:
    """Make the accounts table match accounts.txt (handle -> weight). Removed handles go inactive."""
    with conn:
        conn.execute("UPDATE accounts SET active = 0")
        for handle, weight in entries.items():
            conn.execute(
                """INSERT INTO accounts(handle, weight, active) VALUES (?, ?, 1)
                   ON CONFLICT(handle) DO UPDATE SET weight = excluded.weight, active = 1""",
                (handle, weight),
            )


def accounts_missing_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT handle FROM accounts WHERE active = 1 AND user_id IS NULL")
    return [r["handle"] for r in rows]


def set_user_id(conn: sqlite3.Connection, handle: str, user_id: str, name: str) -> None:
    with conn:
        conn.execute(
            "UPDATE accounts SET user_id = ?, display_name = ? WHERE handle = ?",
            (user_id, name, handle),
        )


def active_accounts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM accounts WHERE active = 1 AND user_id IS NOT NULL ORDER BY handle"
    ).fetchall()


def set_last_seen(conn: sqlite3.Connection, handle: str, tweet_id: str) -> None:
    with conn:
        conn.execute(
            """UPDATE accounts SET last_seen_tweet_id = ?
               WHERE handle = ? AND (last_seen_tweet_id IS NULL
                                     OR CAST(last_seen_tweet_id AS INTEGER) < CAST(? AS INTEGER))""",
            (tweet_id, handle, tweet_id),
        )


# --- tweets -----------------------------------------------------------------

def insert_tweets(conn: sqlite3.Connection, handle: str, tweets: list[dict], week_of) -> int:
    """Insert new tweets; returns how many were new. `week_of` maps a datetime to an NFL week."""
    new = 0
    with conn:
        for t in tweets:
            created = datetime.fromisoformat(t["created_at"].replace("Z", "+00:00"))
            cur = conn.execute(
                """INSERT OR IGNORE INTO tweets
                   (tweet_id, handle, created_at, nfl_week, text, media_urls, url, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    t["id"], handle, created.isoformat(), week_of(created), t["text"],
                    json.dumps(t["media_urls"]), f"https://x.com/{handle}/status/{t['id']}", _now(),
                ),
            )
            new += cur.rowcount
    return new


def last_seen_created_at(conn: sqlite3.Connection, handle: str) -> str | None:
    """created_at (UTC ISO) of the newest stored post for an account."""
    row = conn.execute("SELECT MAX(created_at) AS c FROM tweets WHERE handle = ?", (handle,)).fetchone()
    return row["c"] if row else None


def tweets_to_extract(conn: sqlite3.Connection, start: str, end: str, retry_failed: bool, redo_all: bool):
    if redo_all:
        cond = "1 = 1"
    elif retry_failed:
        cond = "extracted IN (0, -1)"
    else:
        cond = "extracted = 0"
    return conn.execute(
        f"""SELECT * FROM tweets WHERE created_at >= ? AND created_at < ? AND {cond}
            ORDER BY created_at""", (start, end)
    ).fetchall()


def save_extraction(conn, tweet: sqlite3.Row, tweet_type: str, picks: list[dict], model: str) -> None:
    """Replace any earlier picks for this tweet with the new extraction, atomically."""
    now = _now()
    with conn:
        conn.execute("DELETE FROM picks WHERE tweet_id = ?", (tweet["tweet_id"],))
        for p in picks:
            conn.execute(
                f"""INSERT INTO picks (tweet_id, handle, nfl_week, {", ".join(PICK_COLUMNS)}, model, extracted_at)
                    VALUES (?, ?, ?, {", ".join("?" * len(PICK_COLUMNS))}, ?, ?)""",
                (tweet["tweet_id"], tweet["handle"], tweet["nfl_week"],
                 *(p.get(c) for c in PICK_COLUMNS), model, now),
            )
        conn.execute(
            "UPDATE tweets SET extracted = 1, tweet_type = ?, extract_error = NULL WHERE tweet_id = ?",
            (tweet_type, tweet["tweet_id"]),
        )


def mark_failed(conn: sqlite3.Connection, tweet_id: str, error: str) -> None:
    with conn:
        conn.execute(
            "UPDATE tweets SET extracted = -1, extract_error = ? WHERE tweet_id = ?",
            (error[:500], tweet_id),
        )


# --- reads for analysis -----------------------------------------------------

def picks_frame(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """All picks from new_pick posts made in [start, end), with post time and link."""
    return pd.read_sql_query(
        """SELECT p.*, t.created_at, t.url
           FROM picks p
           JOIN tweets t ON t.tweet_id = p.tweet_id
           WHERE t.created_at >= ? AND t.created_at < ? AND t.tweet_type = 'new_pick'
           ORDER BY t.created_at""",
        conn, params=(start, end),
    )


def tweets_frame(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """SELECT tweet_id, handle, created_at, tweet_type, extracted, extract_error, text, url
           FROM tweets WHERE created_at >= ? AND created_at < ? ORDER BY handle, created_at""",
        conn, params=(start, end),
    )
