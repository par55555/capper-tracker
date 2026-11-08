"""Run the web front end + API.

    python -m capper_tracker.server               # http://127.0.0.1:8000
    python -m capper_tracker.server --port 8080

Binds to localhost only: the API can start paid runs (X + Anthropic) and edit accounts.txt.
"""
from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(prog="capper_tracker.server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true", help="auto-reload on Python changes (dev)")
    args = ap.parse_args()
    from .api import WEB_DIST
    print(f"\n  Capper Tracker: open http://{args.host}:{args.port} in your browser  (Ctrl+C to stop)")
    if not WEB_DIST.exists():
        print("  Note: the web page isn't built yet. Run:  cd web && npm install && npm run build\n")
    uvicorn.run("capper_tracker.api:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
