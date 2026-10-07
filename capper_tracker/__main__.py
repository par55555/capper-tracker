"""CLI.

    python -m capper_tracker run               # posts since Tuesday (this NFL week) -> report
    python -m capper_tracker run --days 7      # rolling last 7 days instead
    python -m capper_tracker run --week 5      # a specific (past) NFL week
    python -m capper_tracker run --notify      # macOS notification when done (used by the scheduler)
    python -m capper_tracker extract --redo-all   # re-extract after a prompt change

Scheduling: python -m capper_tracker.schedule install   (see schedule.py)
"""
from __future__ import annotations

import argparse
import json
import logging

from . import db, notify, pipeline
from .config import X_BEARER_TOKEN, lookback, nfl_week_to_date


def main() -> None:
    ap = argparse.ArgumentParser(prog="capper_tracker")
    ap.add_argument("command", choices=["run", "fetch", "extract", "report"])
    scope = ap.add_mutually_exclusive_group()
    scope.add_argument("--week", type=int, help="NFL week number (default: current week to date)")
    scope.add_argument("--days", type=int, help="rolling window: the last N days")
    ap.add_argument("--redo-all", action="store_true", help="re-extract every post in the window")
    ap.add_argument("--notify", action="store_true", help="desktop notification on finish or failure")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    window = lookback(args.days) if args.days else nfl_week_to_date(args.week)
    print(window, flush=True)

    try:
        conn = db.connect()
        if args.command in ("run", "fetch"):
            from .x_client import XClient
            print("fetch:", json.dumps(pipeline.fetch(conn, XClient(X_BEARER_TOKEN), window)), flush=True)

        if args.command in ("run", "extract"):
            from .extractor import PickExtractor
            stats = pipeline.extract(conn, PickExtractor(), window, redo_all=args.redo_all)
            print("extract:", json.dumps(stats), flush=True)

        if args.command in ("run", "report"):
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
