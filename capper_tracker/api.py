"""JSON API for the web front end.

    python -m capper_tracker.server        # http://127.0.0.1:8000

Read endpoints serve the same tables as the Excel report (built by pipeline.week_bundle), so the
browser and the workbook never disagree. Write endpoints edit accounts.txt and start pipeline runs
as a subprocess of the normal CLI, exactly as the scheduler would.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, pipeline, results, trends
from .config import ACCOUNTS_FILE, EXPORT_DIR, ROOT, TZ, current_week, lookback, nfl_week_to_date
from concurrent.futures import ThreadPoolExecutor

WEB_DIST = ROOT / "web" / "dist"
RUN_LOG_DIR = ROOT / "logs" / "web_runs"
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
REPORT_TTL_SECONDS = 300  # game status (Upcoming/Live/Final) comes from ESPN, so don't cache forever

app = FastAPI(title="capper-tracker", docs_url="/api/docs", openapi_url="/api/openapi.json")


# --- helpers ----------------------------------------------------------------

def _window(week: int | None, days: int | None):
    if week is not None and days is not None:
        raise HTTPException(400, "Pass week or days, not both")
    if days is not None:
        if not 1 <= days <= 60:
            raise HTTPException(400, "days must be 1-60")
        return lookback(days)
    if week is not None and not 1 <= week <= 25:
        raise HTTPException(400, "week must be 1-25")
    return nfl_week_to_date(week)


def _records(df: pd.DataFrame) -> dict:
    """DataFrame -> {columns, rows} with NaN as null and timestamps as ISO strings."""
    if df is None or df.empty:
        return {"columns": [], "rows": []}
    payload = json.loads(df.to_json(orient="split", date_format="iso", default_handler=str, index=False))
    cols = payload["columns"]
    return {"columns": cols, "rows": [dict(zip(cols, r)) for r in payload["data"]]}


def _db_fingerprint(conn) -> tuple:
    row = conn.execute("SELECT COUNT(*), MAX(extracted_at) FROM picks").fetchone()
    t = conn.execute("SELECT COUNT(*), SUM(extracted) FROM tweets").fetchone()
    return tuple(row) + tuple(t)


_report_cache: dict = {}
_report_lock = threading.Lock()


_graded_weeks: dict[int, tuple] = {}   # week -> (fingerprint, built_at, graded DataFrame)
_trends_lock = threading.Lock()
_trends_body: dict = {}  # fingerprints of every week -> response


def _clear_caches() -> None:
    with _report_lock:
        _report_cache.clear()


# --- read endpoints ---------------------------------------------------------

@app.get("/api/weeks")
def weeks():
    """Every NFL week with stored posts, newest first, plus extraction health."""
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT t.nfl_week AS week, COUNT(*) AS posts,
                      SUM(t.extracted = 1) AS extracted, SUM(t.extracted = -1) AS failed,
                      SUM(t.extracted = 0) AS pending,
                      (SELECT COUNT(*) FROM picks p WHERE p.nfl_week = t.nfl_week) AS picks
               FROM tweets t WHERE t.nfl_week >= 1 GROUP BY t.nfl_week ORDER BY t.nfl_week DESC"""
        ).fetchall()
    finally:
        conn.close()
    return {"current_week": current_week(), "weeks": [dict(r) for r in rows]}


LIVE_TTL_SECONDS = 120   # the current week: Kalshi prices and game status move


def _report_summary(frames: dict) -> dict:
    picks, tweets, top = frames["Picks"], frames["Tweets"], frames["Top Consensus"]
    return {
        "posts": len(tweets),
        "picks": len(picks),
        "accounts_with_picks": int(picks["handle"].nunique()) if not picks.empty else 0,
        "shared_bets": len(top),
        "conflicts": len(frames["Conflicts"]),
        "failed_posts": int((tweets["extracted"] == -1).sum()) if not tweets.empty else 0,
        "pending_posts": int((tweets["extracted"] == 0).sum()) if not tweets.empty else 0,
        "priced": int(picks["kalshi_prob_post"].notna().sum()) if "kalshi_prob_post" in picks else 0,
    }


@app.get("/api/week")
def week_endpoint(week: int | None = Query(None), days: int | None = Query(None), refresh: bool = False):
    """Everything for one window (default: this NFL week so far): report tables with Kalshi prices
    joined on, and graded results. Live and History both use this."""
    window = _window(week, days)
    live = window.end >= datetime.now(window.end.tzinfo) - timedelta(minutes=5)
    conn = db.connect()
    try:
        key = (window.name, window.start_utc, _db_fingerprint(conn))
        ttl = LIVE_TTL_SECONDS if live else REPORT_TTL_SECONDS * 6
        with _report_lock:
            hit = _report_cache.get(key)
            if hit and not refresh and time.time() - hit[0] < ttl:
                return hit[1]
        b = pipeline.week_bundle(conn, window)
    finally:
        conn.close()
    body = {
        "window": {"name": window.name, "label": str(window), "live": live,
                   "start": window.start.isoformat(), "end": window.end.isoformat()},
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": _report_summary(b["frames"]),
        "results_summary": b["results"]["summary"],
        "frames": {name: _records(df) for name, df in b["frames"].items()},
        "results": {name: _records(df) for name, df in b["results"]["frames"].items()},
    }
    with _report_lock:
        if len(_report_cache) > 8:  # a handful of recently viewed weeks
            _report_cache.pop(min(_report_cache, key=lambda k: _report_cache[k][0]))
        _report_cache[key] = (time.time(), body)
    return body


EXPORT_NAME = re.compile(r"^[\w.-]+\.xlsx$")


@app.get("/api/exports")
def list_exports(week: int | None = Query(None)):
    """Excel reports saved by runs, newest first; optionally only one NFL week's."""
    files = sorted(Path(EXPORT_DIR).glob("*.xlsx"), key=lambda f: f.stat().st_mtime, reverse=True)
    if week is not None:
        files = [f for f in files if f.name.startswith(f"week{week:02d}_")]
    return {"exports": [{"name": f.name, "size": f.stat().st_size,
                         "modified": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")}
                        for f in files]}


@app.get("/api/exports/{name}")
def download_export(name: str):
    f = Path(EXPORT_DIR) / name
    if not EXPORT_NAME.match(name) or not f.is_file():
        raise HTTPException(404, "No such report")
    return FileResponse(f, filename=name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _week_fingerprint(conn, week: int) -> tuple:
    return tuple(conn.execute("SELECT COUNT(*), MAX(extracted_at) FROM picks WHERE nfl_week = ?", (week,)).fetchone())


def _graded_week(week: int, boxes: results.BoxScores) -> pd.DataFrame:
    """One week's graded picks, cached until that week's picks change (or 5 min, while games may be live)."""
    conn = db.connect()
    try:
        fp = _week_fingerprint(conn, week)
        hit = _graded_weeks.get(week)
        recent = week >= current_week() - 1
        if hit and hit[0] == fp and not (recent and time.time() - hit[1] > REPORT_TTL_SECONDS):
            return hit[2]
        g = pipeline.graded_picks(conn, nfl_week_to_date(week), boxes=boxes)
    finally:
        conn.close()
    g = g.assign(week=week) if not g.empty else g
    _graded_weeks[week] = (fp, time.time(), g)
    return g


@app.get("/api/trends")
def trends_endpoint(refresh: bool = False):
    """Every week with picks, graded and compared: who hits, which markets hit, week-by-week."""
    conn = db.connect()
    try:
        weeks = [r[0] for r in conn.execute(
            "SELECT DISTINCT nfl_week FROM picks WHERE nfl_week >= 1 ORDER BY nfl_week")]
    finally:
        conn.close()
    with _trends_lock:
        if refresh:
            _graded_weeks.clear()
            _trends_body.clear()
        boxes = results.BoxScores()
        with ThreadPoolExecutor(max_workers=4) as pool:
            frames = list(pool.map(lambda w: _graded_week(w, boxes), weeks))
        key = tuple((w, *_graded_weeks[w][:2]) for w in weeks if w in _graded_weeks)
        if key in _trends_body:
            return _trends_body[key]
        g = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(not f.empty for f in frames) \
            else pd.DataFrame()
        rep = trends.trends_report(g)
        body = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "weeks": rep["weeks"],
                "frames": {name: _records(df) for name, df in rep["frames"].items()}}
        _trends_body.clear()
        _trends_body[key] = body
    return body


# --- accounts ---------------------------------------------------------------

def _accounts_file_lines() -> list[str]:
    return Path(ACCOUNTS_FILE).read_text().splitlines() if Path(ACCOUNTS_FILE).exists() else []


@app.get("/api/accounts")
def get_accounts():
    listed = []
    for line in _accounts_file_lines():
        handle = line.split("#", 1)[0].split(",", 1)[0].strip().lstrip("@")
        if handle:
            listed.append(handle)
    conn = db.connect()
    try:
        info = {r["handle"]: dict(r) for r in conn.execute(
            """SELECT a.handle, a.display_name, a.user_id, a.active,
                      MAX(t.created_at) AS last_post, COUNT(t.tweet_id) AS posts,
                      (SELECT COUNT(*) FROM picks p WHERE p.handle = a.handle) AS picks
               FROM accounts a LEFT JOIN tweets t ON t.handle = a.handle
               GROUP BY a.handle""")}
    finally:
        conn.close()
    out = []
    for h in listed:
        r = info.get(h.lower(), {})
        out.append({"handle": h, "display_name": r.get("display_name"),
                    "resolved": bool(r.get("user_id")), "last_post": r.get("last_post"),
                    "posts": r.get("posts", 0), "picks": r.get("picks", 0)})
    return {"accounts": out}


class AccountsIn(BaseModel):
    handles: list[str] = Field(..., max_length=500)


@app.put("/api/accounts")
def put_accounts(body: AccountsIn):
    """Rewrite accounts.txt with these handles, keeping the file's leading comment block."""
    seen, handles = set(), []
    for raw in body.handles:
        h = raw.strip().lstrip("@")
        if not h:
            continue
        if not HANDLE_RE.match(h):
            raise HTTPException(400, f"Not a valid X handle: {raw!r}")
        if h.lower() not in seen:
            seen.add(h.lower())
            handles.append(h)
    if not handles:
        raise HTTPException(400, "Keep at least one account")
    lines = _accounts_file_lines()
    header = []
    for line in lines:
        if line.strip().startswith("#") or not line.strip():
            header.append(line)
        else:
            break
    Path(ACCOUNTS_FILE).write_text("\n".join(header + handles) + "\n")
    # The DB's active flags are synced from the file at the start of the next fetch.
    return get_accounts()


# --- runs -------------------------------------------------------------------

class RunIn(BaseModel):
    command: Literal["run", "fetch", "extract", "report"] = "run"
    week: int | None = None
    days: int | None = None
    redo_all: bool = False


class _Run:
    def __init__(self, args: list[str], log_path: Path):
        self.id = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
        self.args = args
        self.log_path = log_path
        self.started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.finished: str | None = None
        self.exit_code: int | None = None
        self.output: deque[str] = deque(maxlen=2000)
        self.proc: subprocess.Popen | None = None

    def as_dict(self, with_output: bool = False) -> dict:
        d = {"id": self.id, "command": " ".join(self.args), "started": self.started,
             "finished": self.finished, "exit_code": self.exit_code,
             "running": self.exit_code is None}
        if with_output:
            d["output"] = list(self.output)
        return d


_runs: deque[_Run] = deque(maxlen=20)
_runs_lock = threading.Lock()


def _pump(run: _Run) -> None:
    assert run.proc and run.proc.stdout
    with open(run.log_path, "w") as log:
        for line in run.proc.stdout:
            run.output.append(line.rstrip("\n"))
            log.write(line)
            log.flush()
    run.exit_code = run.proc.wait()
    run.finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _clear_caches()
    _graded_weeks.clear()
    _trends_body.clear()


@app.post("/api/runs")
def start_run(body: RunIn):
    _window(body.week, body.days)  # validate
    with _runs_lock:
        if _runs and _runs[-1].exit_code is None:
            raise HTTPException(409, "A run is already in progress")
        args = ["-m", "capper_tracker", body.command]
        if body.week is not None:
            args += ["--week", str(body.week)]
        if body.days is not None:
            args += ["--days", str(body.days)]
        if body.redo_all:
            args.append("--redo-all")
        RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
        run = _Run(["python"] + args, RUN_LOG_DIR / f"{datetime.now(TZ):%Y%m%d_%H%M%S}.log")
        run.proc = subprocess.Popen([sys.executable, "-u", *args], cwd=ROOT, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1)
        _runs.append(run)
        threading.Thread(target=_pump, args=(run,), daemon=True).start()
    return run.as_dict(with_output=True)


@app.get("/api/runs")
def list_runs():
    return {"runs": [r.as_dict() for r in reversed(_runs)]}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    for r in _runs:
        if r.id == run_id:
            return r.as_dict(with_output=True)
    raise HTTPException(404, "No such run")


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    for r in _runs:
        if r.id == run_id and r.exit_code is None and r.proc:
            r.proc.terminate()
            return r.as_dict()
    raise HTTPException(404, "No running run with that id")


# --- built front end --------------------------------------------------------

_NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Capper Tracker</title>
<body style="font:16px/1.5 -apple-system,sans-serif;max-width:620px;margin:60px auto;padding:0 16px">
<h1>The web page hasn't been built yet</h1>
<p>The server is running, but the page itself needs a one-time build. In a terminal, from the
project folder:</p>
<pre style="background:#f1f3f6;padding:12px;border-radius:8px">cd web
npm install
npm run build</pre>
<p>Then stop this server (Ctrl+C), start it again and reload this page.</p>
<p>The data API is already working: <a href="/api/docs">/api/docs</a></p></body>"""

if not WEB_DIST.exists():
    from fastapi.responses import HTMLResponse

    @app.get("/", include_in_schema=False)
    def not_built():
        return HTMLResponse(_NOT_BUILT)

if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Unknown API route")
        f = (WEB_DIST / path).resolve()
        if path and f.is_file() and WEB_DIST.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(WEB_DIST / "index.html")
