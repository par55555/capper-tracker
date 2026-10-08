# capper-tracker

Weekly pull of betting picks from a list of X accounts, turned into structured bets and a consensus report. Built to run Thursday and/or Sunday.

```
accounts.txt ──► fetch (X API) ──► SQLite ──► extract (Claude, text + bet-slip images) ──► analyze ──► weekXX_consensus.xlsx
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # add X_BEARER_TOKEN and ANTHROPIC_API_KEY
# list handles in accounts.txt, one per line (optional ",weight")
python -m pytest -q tests   # offline test, no API calls
```

## Run

```bash
python -m capper_tracker run               # default: this NFL week so far (Tuesday -> now)
python -m capper_tracker run --days 7      # rolling last 7 days instead
python -m capper_tracker run --week 5      # a specific past NFL week
python -m capper_tracker extract --redo-all   # re-extract after changing the prompt
```

**Which window?** The default covers posts since the Tuesday that opens the NFL week, so a Thursday run sees Tue–Thu posts and a Sunday run sees Tue–Sun. That's every pick for this week's games and nothing for last week's, which are already settled. `--days 7` is a literal rolling week; on a Sunday it would pull in last Sunday's picks too.

Runs are incremental. A Sunday run after a Thursday run only buys the posts made since Thursday from X and only sends those to the LLM, but the Sunday report still covers the whole week.

### Schedule it (macOS, hands-off)

From the project folder, in the environment you run it with (e.g. `conda activate capper`):

```bash
python -m capper_tracker.schedule install    # Thu 12:00 + Sun 11:00, local time
python -m capper_tracker.schedule run-now    # fire it once now to test
python -m capper_tracker.schedule status     # last exit code / run count
python -m capper_tracker.schedule uninstall
```

Each run sends a macOS notification with the headline numbers, the top shared bets, and any team themes (or a failure alert pointing to `logs/run.log`). The newest report is always at `exports/latest_consensus.xlsx`, alongside the dated copies.

- If the Mac is **asleep** at the scheduled time, the run happens when it wakes. If it's **shut down**, that run is skipped.
- First notification not appearing? System Settings → Notifications → allow **Script Editor** (macOS attributes `osascript` notifications to it).
- On Linux, use cron instead: `0 12 * * 4` and `0 11 * * 0` running `python -m capper_tracker run`.

## What the report contains

| Sheet | What it answers |
|---|---|
| Top Consensus | Bets 2+ accounts share, ranked by number of accounts. Starts with league, game time (ET), matchup and status, then a plain-English `what_it_means` |
| Team Themes | Per team: net accounts on pass-game overs vs run-game overs, labeled *Passing threat* / *Rushing threat* / *Pass + run* / *Fade*, plus who's driving it and TD-scorer interest |
| Players | Every player any account touched: total accounts, over vs under lean, which props, conflict flag |
| All Bets | Full grouped list, including singletons |
| Conflicts | Markets where tracked accounts are on opposite sides |
| Picks / Tweets | Raw extracted picks (with snippet and link) and every post with its classification (`post_type`), for auditing |
| Guide | Plain-English definitions of every column and term, including what 'other' means |

Counting rules:

- **Every account counts equally, once per bet.** If the same person posts the same bet again (a reminder, a repost, the same leg in several parlays, or at a moved line), it's still one vote. `repeat_posts` on the bets sheets and `times_posted` on Picks show how often it was repeated.
- **If a person switches sides** on the same market (Burrow over 1.5 TDs, later under), only their latest side counts; `switched_side` flags it on Picks.
- Bets group **ignoring the exact line**: Chase o74.5 and o79.5 are the same bet; each account's latest line feeds the range shown.
- Pass game = passing + receiving props; run game = rushing props. Rush+rec yards props are tracked but kept out of the pass/run split.
- "J. Allen" and "Josh Allen" merge when first initial, last name and team agree.

Game matchups and start times come from ESPN's public scoreboard feeds (no key). If ESPN is unreachable the report still builds with those columns blank.

## Cost (X pay-per-use, Oct 2026)

About $0.005 per post returned and $0.01 per user lookup (IDs are cached after the first run). 20 accounts × ~50 posts/week ≈ $5/week. Each `fetch` prints an estimate. `MAX_TWEETS_PER_ACCOUNT` (default 15) keeps only each account's newest 15 posts per run and only requests that many from X; accounts that hit the cap are listed in the fetch output.

## Known limitations

- **Untested against the live X API** — validated with mocked responses. Run once with one or two accounts first and check the `Tweets` sheet.
- Player → team comes from the model's knowledge and the post text; mid-season trades can mislabel a team. Feeding a weekly roster/schedule into the prompt is the fix.
- This uses the user-timeline endpoint (the account's most recent ~3,200 posts), not search, so a missed week can usually be backfilled with `--week N`. Very high-volume accounts may push older posts out of reach.
- Posts that are videos only give the model a preview frame.
