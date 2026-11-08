"""CLI.

    python -m capper_tracker run               # posts since Tuesday (this NFL week) -> report
    python -m capper_tracker run --days 7      # rolling last 7 days instead
    python -m capper_tracker run --week 5      # a specific (past) NFL week
    python -m capper_tracker run --notify      # macOS notification when done (used by the scheduler)
    python -m capper_tracker run --max-posts 5 --no-excel   # quick pull (Sunday every 15 min)
    python -m capper_tracker extract --redo-all   # re-extract after a prompt change

Scheduling: python -m capper_tracker.schedule install   (see schedule.py)
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import sys

from . import db, notify, pipeline
from .config import DB_PATH, MAX_TWEETS_PER_ACCOUNT, X_BEARER_TOKEN, lookback, nfl_week_to_date


def _single_run_lock():
    """Hold an exclusive lock for the whole run so scheduled pulls never overlap (a slow run and
    the next 15-minute pull would otherwise fetch and pay for the same posts twice)."""
    path = DB_PATH.parent / ".run.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another run is still in progress; skipping this one.", flush=True)
        sys.exit(0)
    return fh  # keep the handle alive; the lock is released when the process exits


def main() -> None:
    ap = argparse.ArgumentParser(prog="capper_tracker")
    ap.add_argument("command", choices=["run", "fetch", "extract", "report"])
    scope = ap.add_mutually_exclusive_group()
    scope.add_argument("--week", type=int, help="NFL week number (default: current week to date)")
    scope.add_argument("--days", type=int, help="rolling window: the last N days")
    ap.add_argument("--redo-all", action="store_true", help="re-extract every post in the window")
    ap.add_argument("--notify", action="store_true", help="desktop notification on finish or failure")
    ap.add_argument("--max-posts", type=int, default=MAX_TWEETS_PER_ACCOUNT, metavar="N",
                    help=f"newest posts to fetch per account this run (default {MAX_TWEETS_PER_ACCOUNT})")
    ap.add_argument("--no-excel", action="store_true",
                    help="skip writing the Excel report (quick pulls; the web app reads the database)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if not 5 <= args.max_posts <= 100:
        ap.error("--max-posts must be 5-100 (X returns at least 5 per request)")
    lock = _single_run_lock()  # noqa: F841

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    window = lookback(args.days) if args.days else nfl_week_to_date(args.week)
    print(window, flush=True)

    try:
        conn = db.connect()
        if args.command in ("run", "fetch"):
            from .x_client import XClient
            print("fetch:", json.dumps(pipeline.fetch(conn, XClient(X_BEARER_TOKEN), window,
                                                      max_tweets=args.max_posts)), flush=True)

        if args.command in ("run", "extract"):
            from .extractor import PickExtractor
            stats = pipeline.extract(conn, PickExtractor(), window, redo_all=args.redo_all)
            print("extract:", json.dumps(stats), flush=True)
            if args.notify and stats.get("failed"):
                notify.extraction_failed(window.name, stats["failed"], stats["posts"], stats.get("first_error", ""))

        if args.command in ("run", "report"):
            if not args.no_excel:
                print("report:", pipeline.report(conn, window), flush=True)
            if args.notify:
                notify.run_succeeded(window.name, pipeline.summary(conn, window))
    except Exception as e:
        logging.exception("Run failed")
        if args.notify:
            notify.run_failed(window.name, e)
        raise


if __name__ == "__main__":
    main()
