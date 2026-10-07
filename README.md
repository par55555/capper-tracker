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

### Schedule (cron, machine on Eastern time)

```cron
0 12 * * 4  cd /path/to/capper-tracker && .venv/bin/python -m capper_tracker run >> run.log 2>&1   # Thu noon, before TNF
0 11 * * 0  cd /path/to/capper-tracker && .venv/bin/python -m capper_tracker run >> run.log 2>&1   # Sun 11am, before early games
```

A laptop has to be awake for cron to fire; Cloud Run Jobs + Cloud Scheduler is the always-on option (the SQLite file would then live in a mounted bucket or move to a hosted DB).

## What the report contains

| Sheet | What it answers |
|---|---|
| Top Consensus | Bets 2+ accounts share, ranked by number of accounts |
| Team Themes | Per team: net accounts on pass-game overs vs run-game overs, labeled *Passing threat* / *Rushing threat* / *Pass + run* / *Fade*, plus who's driving it and TD-scorer interest |
| Players | Every player any account touched: total accounts, over vs under lean, which props, conflict flag |
| All Bets | Full grouped list, including singletons |
| Conflicts | Markets where tracked accounts are on opposite sides |
| Picks / Tweets | Raw extracted picks (with snippet and link) and every post with its classification, for auditing |

Counting rules:

- **Every account counts equally, once per bet.** If the same person posts the same bet again (a reminder, a repost, the same leg in several parlays, or at a moved line), it's still one vote. `repeat_posts` on the bets sheets and `times_posted` on Picks show how often it was repeated.
- **If a person switches sides** on the same market (Burrow over 1.5 TDs, later under), only their latest side counts; `switched_side` flags it on Picks.
- Bets group **ignoring the exact line**: Chase o74.5 and o79.5 are the same bet; each account's latest line feeds the range shown.
- Pass game = passing + receiving props; run game = rushing props. Rush+rec yards props are tracked but kept out of the pass/run split.
- "J. Allen" and "Josh Allen" merge when first initial, last name and team agree.

## Cost (X pay-per-use, Oct 2026)

About $0.005 per post returned and $0.01 per user lookup (IDs are cached after the first run). 20 accounts × ~50 posts/week ≈ $5/week. Each `fetch` prints an estimate. `MAX_TWEETS_PER_ACCOUNT` (default 15) keeps only each account's newest 15 posts per run and only requests that many from X; accounts that hit the cap are listed in the fetch output.

## Known limitations

- **Untested against the live X API** — validated with mocked responses. Run once with one or two accounts first and check the `Tweets` sheet.
- Player → team comes from the model's knowledge and the post text; mid-season trades can mislabel a team. Feeding a weekly roster/schedule into the prompt is the fix.
- This uses the user-timeline endpoint (the account's most recent ~3,200 posts), not search, so a missed week can usually be backfilled with `--week N`. Very high-volume accounts may push older posts out of reach.
- Posts that are videos only give the model a preview frame.
