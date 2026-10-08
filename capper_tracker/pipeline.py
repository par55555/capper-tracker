"""The three steps of a run. Each is a plain function so it can later be wrapped as an agent tool."""
from __future__ import annotations

import json
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

from . import db, games
from .analyze import build_report
from .config import (ACCOUNTS_FILE, EXPORT_DIR, EXTRACT_WORKERS, MAX_TWEETS_PER_ACCOUNT, TZ,
                     Window, nfl_week)
from .export import write_report

log = logging.getLogger(__name__)


def read_accounts_file(path: Path = ACCOUNTS_FILE) -> dict[str, float]:
    """accounts.txt: one handle per line. '#' starts a comment. (A ',weight' suffix is accepted but unused.)"""
    entries: dict[str, float] = {}
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        handle, _, weight = line.partition(",")
        entries[handle.strip().lstrip("@").lower()] = float(weight) if weight.strip() else 1.0
    if not entries:
        raise ValueError(f"No accounts listed in {path}")
    return entries


def fetch(conn, x, window: Window, accounts_file: Path = ACCOUNTS_FILE) -> dict:
    """Pull every active account's posts in the window into the DB."""
    db.sync_account_list(conn, read_accounts_file(accounts_file))
    missing = db.accounts_missing_ids(conn)
    if missing:
        found = x.lookup_users(missing)
        for handle in missing:
            if handle in found:
                db.set_user_id(conn, handle, found[handle]["id"], found[handle]["name"])
            else:
                log.warning("@%s not found on X; check the handle in accounts.txt", handle)

    live = window.end >= datetime.now(TZ).replace(microsecond=0)
    stats = {"window": str(window), "accounts": 0, "posts_returned": 0, "new_posts": 0}
    for acct in db.active_accounts(conn):
        # If we already hold this account's posts from inside the window (e.g. a Thursday run
        # before this Sunday run), only ask X for newer ones so they aren't paid for twice.
        last = db.last_seen_created_at(conn, acct["handle"])
        since_id = acct["last_seen_tweet_id"] if live and last and last >= window.start_utc else None
        tweets = x.user_tweets(
            acct["user_id"],
            since_id=since_id,
            start_time=window.start,
            end_time=None if live else window.end,
            max_tweets=MAX_TWEETS_PER_ACCOUNT,
        )
        if len(tweets) >= MAX_TWEETS_PER_ACCOUNT:
            log.warning("@%s hit the %d-post cap; older posts in the window were skipped",
                        acct["handle"], MAX_TWEETS_PER_ACCOUNT)
            stats.setdefault("capped_accounts", []).append(acct["handle"])
        new = db.insert_tweets(conn, acct["handle"], tweets, nfl_week)
        if tweets:
            db.set_last_seen(conn, acct["handle"], max(tweets, key=lambda t: int(t["id"]))["id"])
        stats["accounts"] += 1
        stats["posts_returned"] += len(tweets)
        stats["new_posts"] += new
        log.info("@%s: %d posts returned, %d new", acct["handle"], len(tweets), new)
    stats["est_x_cost_usd"] = round(x.estimated_cost, 2)
    return stats


def extract(conn, extractor, window: Window, retry_failed: bool = True, redo_all: bool = False) -> dict:
    """Run the LLM over unprocessed posts in the window. Each post is extracted once and cached."""
    todo = db.tweets_to_extract(conn, window.start_utc, window.end_utc,
                                retry_failed=retry_failed, redo_all=redo_all)
    stats = {"posts": len(todo), "picks": 0, "failed": 0, "by_type": {}}
    if not todo:
        return stats

    def work(t):
        return extractor.extract(handle=t["handle"], created_at=t["created_at"], week=t["nfl_week"],
                                 text=t["text"], media_urls=json.loads(t["media_urls"]))

    with ThreadPoolExecutor(max_workers=EXTRACT_WORKERS) as pool:
        futures = {pool.submit(work, t): t for t in todo}
        for fut in as_completed(futures):
            tweet = futures[fut]
            try:
                result = fut.result()
            except Exception as e:  # keep going; failed posts are retried next run
                log.error("Extraction failed for %s: %s", tweet["url"], e)
                db.mark_failed(conn, tweet["tweet_id"], repr(e))
                stats["failed"] += 1
                continue
            db.save_extraction(conn, tweet, result.tweet_type, result.picks, extractor.model)
            stats["picks"] += len(result.picks)
            stats["by_type"][result.tweet_type] = stats["by_type"].get(result.tweet_type, 0) + 1
    return stats


_FRAMES_CACHE: dict = {}


def _frames(conn, window: Window, schedule: "games.Schedule | None" = None):
    """Build the report tables once per run (report and notification share them)."""
    key = (id(conn), window.start_utc, window.end_utc)
    if key not in _FRAMES_CACHE:
        first = window.start.astimezone(TZ).date()
        # Cover every game a pick posted in the window could be for (NFL: through Monday night).
        last = max(window.end.astimezone(TZ).date(), first) + timedelta(days=8)
        _FRAMES_CACHE.clear()
        _FRAMES_CACHE[key] = build_report(
            db.picks_frame(conn, window.start_utc, window.end_utc),
            db.tweets_frame(conn, window.start_utc, window.end_utc),
            schedule=schedule or games.Schedule(), game_days=(first, last))
    return _FRAMES_CACHE[key]


def report(conn, window: Window, out_dir: Path = EXPORT_DIR) -> Path:
    _FRAMES_CACHE.clear()  # always rebuild for a fresh report
    """Write the dated workbook, plus a copy at exports/latest_consensus.xlsx for quick access."""
    stamp = datetime.now(TZ).strftime("%Y%m%d_%H%M")
    path = write_report(_frames(conn, window), Path(out_dir) / f"{window.name}_consensus_{stamp}.xlsx")
    shutil.copyfile(path, Path(out_dir) / "latest_consensus.xlsx")
    return path


def summary(conn, window: Window, top_n: int = 3) -> dict:
    """Headline numbers for the run notification."""
    frames = _frames(conn, window)
    picks, top = frames["Picks"], frames["Top Consensus"]
    return {
        "picks": len(picks),
        "accounts_with_picks": picks["handle"].nunique() if not picks.empty else 0,
        "shared_bets": len(top),
        "top": [f"{r.bet} ({r.accounts})" for r in top.head(top_n).itertuples()] if not top.empty else [],
        "themes": [f"{r.team} {r.theme.lower()}" for r in frames["Team Themes"].itertuples()
                   if getattr(r, "theme", "")][:top_n] if not frames["Team Themes"].empty else [],
    }
