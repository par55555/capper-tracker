# Capper Tracker

**Follow a group of sports bettors ("cappers") on X, see which bets they agree on, and find out who actually wins.**

Every week, Capper Tracker:

1. **Collects** the latest posts from a list of X (Twitter) accounts you choose.
2. **Reads** each post, including screenshots of bet slips, using Claude (an AI model), and turns it into a clean list of bets: "Ja'Marr Chase over 74.5 receiving yards", "Chiefs -3.5", and so on.
3. **Finds the consensus**: bets that several accounts posted independently, ranked by how many agree.
4. **Prices** every bet with [Kalshi](https://kalshi.com)'s market odds: what the market thought when the capper posted it, at kickoff, and right now.
5. **Grades** every bet once the game is over, using official box scores, so you can see who hits, who beats the market, and whether following the crowd pays off.

You use it through a web page on your own computer, or as a spreadsheet report that's generated automatically.

---

## Contents

- [What you'll see](#what-youll-see)
- [What you need](#what-you-need)
- [Setup (one time)](#setup-one-time)
- [Everyday use](#everyday-use)
- [How to read the numbers](#how-to-read-the-numbers)
- [Glossary](#glossary)
- [What it costs](#what-it-costs)
- [Troubleshooting](#troubleshooting)
- [How it works under the hood](#how-it-works-under-the-hood)
- [Known limitations](#known-limitations)

---

## What you'll see

The web app has three pages, switched at the top:

| Page | What it's for |
|---|---|
| **Live** (front page) | **Bets for games that haven't started, as betting tiles.** Grouped by game with a kickoff countdown; each tile shows how many accounts are on it, the matching Kalshi market, its **live price** (as a % chance, the cost in cents, and the equivalent sportsbook odds like +150), and how far it has moved since the first account posted it. Prices update every 5 seconds and flash green/red when they move. Press **Placed** on a tile to mark a bet you've made: it's outlined in blue, remembers the price you got, and shows how the price has moved since. Filter by 2+ accounts / all picks / placed, and by league. |
| **History** | **Any week (this one included), broken out like the Excel reports.** Results (every bet marked Won/Lost with what actually happened and what the market gave it), plus consensus, all bets, players, team themes, conflicts, picks, posts, and the saved Excel files to download. |
| **Analysis** | **What's working, across every week.** Who beats the market (not just who hits), whether agreement between accounts beats the price, favorites vs. long shots, which markets hit, week-by-week trends, and hot/cold streaks. |

The ⚙ button opens **Manage**: run the update with a button, add or remove tracked accounts, and a guide to every term.

![History: a past week's shared bets, each with the Kalshi price when it was posted](docs/images/consensus.png)

![Results: each shared bet graded, with what happened and what the market gave it](docs/images/results.png)

![Analysis: accounts and agreement measured against Kalshi's odds](docs/images/trends.png)

---

## What you need

| Thing | Why | Where to get it |
|---|---|---|
| A Mac (or Linux) computer | Runs everything locally. Automatic scheduling is set up for macOS. | |
| **Python 3.10 or newer** | Runs the tracker. | [python.org](https://www.python.org/downloads/) or [Anaconda](https://www.anaconda.com/download) |
| **Node.js 18 or newer** | Only needed once, to build the web page. | [nodejs.org](https://nodejs.org/), or `brew install node`, or `conda install -c conda-forge nodejs` |
| **An X API bearer token**, with pay-per-use credits | To read posts from X. | [developer.x.com](https://developer.x.com/) → create a project and app → copy the *Bearer Token* |
| **An Anthropic API key** | To read the posts and bet slips. | [console.anthropic.com](https://console.anthropic.com/) → API Keys |

Game schedules, scores and box scores come from ESPN's public feeds, and market prices from Kalshi's public market data. Neither needs an account or key.

---

## Setup (one time)

Open Terminal and run these steps one at a time.

**1. Download the project**

```bash
git clone https://github.com/par55555/capper-tracker.git
cd capper-tracker
```

**2. Create a Python environment and install the requirements**

Using Anaconda (recommended):

```bash
conda create -n capper python=3.12 -y
conda activate capper
pip install -r requirements.txt
```

Or with plain Python: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`

> Each time you open a new Terminal window to use the tracker, run `conda activate capper` (or `source .venv/bin/activate`) first.

**3. Add your API keys**

```bash
cp .env.example .env
open -e .env        # opens it in TextEdit
```

Paste your keys after the `=` signs, with no quotes and no spaces:

```
X_BEARER_TOKEN=AAAAAAAAAAAAAAAAAAAAA...
ANTHROPIC_API_KEY=sk-ant-...
```

The other settings in that file have sensible defaults (see [Settings](#settings)). The `.env` file stays on your computer; it's excluded from git, so your keys are never uploaded.

**4. Choose the accounts to track**

Edit `accounts.txt`: one X handle per line, without the `@`. You can also do this later on the web page's **Run & accounts** tab.

**5. Build the web page**

```bash
cd web
npm install
npm run build
cd ..
```

**6. Check that everything works**

```bash
python -m pytest -q tests
```

This runs the built-in tests with fake data. It costs nothing and needs no internet. You should see `passed` at the end.

---

## Everyday use

### Open the web app

```bash
python -m capper_tracker.server
```

Then open **http://127.0.0.1:8000** in your browser. Leave the Terminal window open while you use it; press **Ctrl+C** in it to stop.

Use the dropdown at the top right to switch between NFL weeks, or to look at the last 3, 7 or 14 days.

### Get this week's picks

On the **Run & accounts** tab, choose **Full run** and press **Start**. You'll see its progress live. This:

1. fetches new posts from X (costs money; see [What it costs](#what-it-costs)),
2. sends new posts to Claude to pull out the bets (costs money),
3. rebuilds the report (free).

![Run & accounts tab: start a run and manage tracked accounts](docs/images/run-accounts.png)

Runs only pay for posts they haven't seen before, so running again later in the week is cheap. The other buttons do one step at a time, for example **Report only** to refresh the report for free.

The same steps are available from Terminal:

```bash
python -m capper_tracker run               # this NFL week so far (since Tuesday)
python -m capper_tracker run --week 5      # a specific past NFL week
python -m capper_tracker run --days 7      # the last 7 days, regardless of NFL weeks
python -m capper_tracker report            # just rebuild the report (free)
python -m capper_tracker extract           # retry posts that failed to be read
```

Each run also saves a spreadsheet. The newest one is always at `exports/latest_consensus.xlsx`, with dated copies next to it.

### Run it automatically (Mac)

```bash
python -m capper_tracker.schedule install    # game-window pulls + Sunday every-15-minute quick pulls
python -m capper_tracker.schedule run-now    # try it once right now
python -m capper_tracker.schedule status     # did the last one work?
python -m capper_tracker.schedule uninstall  # turn it off
```

Run `install` with your `capper` environment active; the schedule uses that environment.

**When it runs (Eastern time), and why.** Most picks go up 1–8 hours before their game. NFL picks cluster on Sunday morning; NBA and NHL picks around 9–10am and 5–6pm. So the pulls are timed just before each slate:

| When | Why |
|---|---|
| Thu 7:30pm | Thursday Night Football |
| Sun 8:45am, 12:30pm, 3:30pm, 7:30pm | London game, 1pm, 4pm and Sunday night games |
| Mon 7:30pm | Monday Night Football |
| Sat 11:15am, 3:00pm | College football noon and afternoon kickoffs |
| Weekdays 12:30pm, every day 6:30pm | NBA / NHL / MLB slates |
| **Sundays every 15 min, 7:00am–8:15pm** | **Quick pulls** so new NFL picks reach the Live page within ~15 minutes |

**Sunday quick pulls** are a second, lighter job. Each one asks every account for at most **5 new posts** (only posts since the last pull are ever requested) and skips the Excel file, since the Live page reads the database directly. In weeks 4–7 no account posted more than 5 times in any 15-minute window on a Sunday, so the cap skips nothing. A lock makes sure two pulls never overlap; if one runs long, the next simply skips. You can run one by hand with `python -m capper_tracker run --max-posts 5 --no-excel`.

Replaying every stored pick against this schedule, **83% of picks (91% of NFL picks) are collected before their game starts**, compared with 37% (62% NFL) for a twice-a-week schedule. More pulls barely change the cost: X bills per new post, and each run only asks for posts since the last one.

- **Notifications** only appear when a *new* shared bet shows up, or when something fails (including posts that couldn't be read, e.g. an expired API key).
- If the Mac is **asleep** at a scheduled time, that run happens when it wakes. If it's **shut down**, the run is skipped. For game days, keep it plugged in and awake.
- Not seeing notifications? System Settings → Notifications → allow **Script Editor**.
- On Linux, use cron with the same times, running `python -m capper_tracker run`.

### Which posts count for "this week"?

NFL weeks run **Tuesday to Monday** (Eastern time). A Thursday run sees posts since Tuesday; a Sunday run sees everything since Tuesday. That covers every pick for this week's games without mixing in last week's, which are already settled.

---

## How to read the numbers

**Each account gets one vote per bet.**
- If someone posts the same bet several times (a reminder, a repost, the same leg in several parlays, or at an updated line), it still counts once.
- If someone switches sides (over, then later under), only their latest side counts.

**Bets are grouped regardless of the exact line.** "Chase over 74.5 yards" and "Chase over 79.5 yards" are the same bet; the card shows the range of lines posted.

**Grading** (History → Results, and Analysis):
- A bet is graded once ESPN marks its game **Final**.
- **Graded:** NFL and college football player props (yards, receptions, touchdowns, anytime / 2+ / first TD scorer, and more), plus moneylines, spreads, game totals and team totals in every league.
- **Ungraded, with the reason shown:** player props in other sports and unusual custom bets.
- A player who didn't play is shown as ungraded, matching how sportsbooks usually void those bets.

**Kalshi prices.** Kalshi is a regulated exchange where every outcome trades as a contract that pays $1 if it happens. A price of 62¢ means the market thinks there's a 62% chance. For each pick the app finds the matching Kalshi market and records:
- **at post**: the price in the hour the capper posted it (what you could have bought it for),
- **before kickoff**: the last price before the game,
- **now**: the live price, for games that haven't started.

How picks are matched to Kalshi markets:
- **Exact line:** "Over 74.5 receiving yards" is Kalshi's "75+ receiving yards"; unders use the "No" side of the same market, and an underdog +3.5 is "No" on the favorite winning by over 3.5.
- **"≈" (interpolated):** when Kalshi doesn't offer the exact line (over 19.5 yards, but Kalshi has 15+ and 25+), the price is estimated between the two neighboring lines and marked with "≈".
- **No match:** the reason is shown on the Picks tab, e.g. the player isn't listed or Kalshi has no market for that stat.
- **Accuracy:** checked against all of weeks 4–7, about 78% of picks match a Kalshi market, and on every exact match Kalshi's settled result agreed with the ESPN box score (523 of 523).

**Hit rate** = wins ÷ (wins + losses). Pushes and ungraded bets are left out. When ESPN can't grade a pick (e.g. NBA and NHL player props), Kalshi's settled result is used instead.

**Beating the market** is what matters, not a 50% hit rate. On the Analysis page:
- **Vs market**: actual hit rate minus the average Kalshi price when posted. +9 pts means the account's picks hit 9 points more often than the market expected, i.e. they find underpriced bets.
- **Profit at Kalshi prices**: what you'd have made per $1 buying each pick at the moment it was posted (Kalshi's small trading fees are ignored).
- **Price moved**: how Kalshi's price changed between the post and kickoff. If prices keep moving toward an account's picks after they post, the market is catching up to them, which is a strong sign of skill that shows up faster than win-loss records.

**Hit rate is not profit.** An anytime-TD bet at +400 only needs to win about 20% of the time to break even, and a 60% favorite that hits 58% of the time loses money. That's why the market comparison above exists.

**The 95% range** (the thin line on the "Who hits" bars) shows how much to trust a hit rate. 4–1 and 40–10 are both 80%, but the second is far more convincing. "Most reliable" ranks accounts by the low end of that range, so a lucky small sample can't top the list.

---

## Glossary

| Term | Meaning |
|---|---|
| **Capper** | Someone who posts betting picks publicly. |
| **Consensus / shared bet** | A bet that 2 or more tracked accounts posted. |
| **Prop (player prop)** | A bet on one player's stats, e.g. over 74.5 receiving yards. |
| **Anytime TD** | The player scores a touchdown at any point in the game. |
| **Spread** | A team must win by more than (or lose by less than) a number of points, e.g. -3.5. |
| **Moneyline** | A team simply has to win. |
| **Total (over/under)** | Combined points scored by both teams, over or under a number. |
| **Parlay / parlay leg** | Several bets combined into one; each bet inside is a *leg*. Legs are graded individually here. |
| **Push** | Exactly on the line: the bet is refunded. |
| **Split** | Accounts posted the same bet at different lines, and some won while others lost. |
| **Odds (+150 / -110)** | +150 wins $150 on a $100 bet; -110 risks $110 to win $100. |
| **Unit (u)** | A standard bet size, so results compare fairly across people. |
| **Kalshi price** | The market's probability, in cents: 62¢ = 62% chance. |
| **Vs market (pts)** | How much more (or less) often picks hit than Kalshi's price said they would. |
| **1H / 1Q / 2H** | First half, first quarter, second half. |

---

## What it costs

| Service | Price | Typical week |
|---|---|---|
| X API (pay-per-use) | ~$0.005 per post, ~$0.01 the first time each account is looked up | 20 accounts × ~50 posts ≈ **$5** |
| Anthropic (Claude) | Per post read; posts with bet-slip images cost more | A few dollars |
| ESPN data, Kalshi prices | Free | — |

- **Post cap:** by default each run keeps only each account's newest **15** posts (`MAX_TWEETS_PER_ACCOUNT`). Each fetch prints a cost estimate and lists accounts that hit the cap.
- **Free buttons:** the web page asks you to confirm before any paid run. **Report only** and all three pages (Live, History, Analysis) are always free.

### Settings

All optional, in `.env`:

| Setting | Default | What it does |
|---|---|---|
| `EXTRACT_MODEL` | `claude-sonnet-5-5` | Which Claude model reads the posts. |
| `MAX_TWEETS_PER_ACCOUNT` | `15` | Newest posts kept per account per run (cost guard). |
| `NFL_WEEK1_TUESDAY` | `2026-09-08` | The Tuesday before the season opener. **Update this each season.** |
| `THEME_MIN_ACCOUNTS` | `2` | How many more accounts must lean one way before a team gets a "passing/rushing threat" label. |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Yellow banner: *"posts have no picks extracted"*, mentioning the API key | Your `ANTHROPIC_API_KEY` in `.env` is missing or expired. Replace it, then run **Extract only** for that week. |
| The browser says *"The web page hasn't been built yet"* | Run `cd web && npm install && npm run build`, then restart the server. |
| `address already in use` when starting the server | It's already running in another window. Use that one, or start on another port: `python -m capper_tracker.server --port 8080`. |
| `ModuleNotFoundError` | Your environment isn't active. Run `conda activate capper` (or `source .venv/bin/activate`). |
| `@handle not found on X` | Check the spelling in `accounts.txt`; the account may have been renamed or suspended. |
| Game times or results are blank | ESPN couldn't be reached, or the team couldn't be matched. Everything else still works; press **Refresh** later. |
| A bet shows "Kalshi —" | No matching Kalshi market. Hover it, or check the Picks tab, for the reason. |
| Analysis takes a minute or two the first time | It's fetching every game and price history once; after that it's cached in `data/`. |
| A bet looks wrong | Open the **Picks** tab, find it, and click ↗ to see the original post. |
| Something else failed during a scheduled run | See `logs/run.log`. Runs started from the web page are logged in `logs/web_runs/`. |

---

## How it works under the hood

```
accounts.txt ─► X API ─► SQLite database ─► Claude reads posts & bet slips ─► analysis ─┬─► web app (FastAPI + React)
                                                                                        └─► Excel report
                         ESPN schedules & box scores ─► game times, results, grading ───┤
                         Kalshi markets & price history ─► odds at post / kickoff / now ─┘
```

| Path | What's in it |
|---|---|
| `capper_tracker/` | The Python code: `x_client.py` (X API), `extractor.py` (Claude), `normalize.py` + `analyze.py` (grouping and consensus), `games.py` (ESPN schedules), `results.py` (grading), `kalshi.py` (matching picks to Kalshi markets and pricing them), `trends.py` (cross-week stats), `api.py` + `server.py` (web server), `pipeline.py` (ties the steps together), `schedule.py` (Mac scheduling) |
| `web/` | The web page (React + TypeScript). `src/views/` has one file per tab. |
| `tests/` | Automated tests. They use fake data and need no API keys. |
| `data/` | Your local database (`cappers.db`) and cached ESPN and Kalshi data. Not uploaded to git. |
| `exports/` | Generated Excel reports. Not uploaded to git. |

Things to know:
- **One source of truth:** the database is the record of everything. The web app and the Excel report are built by the same code, so they always agree.
- **Caching:** box scores, schedules and Kalshi prices for finished games are cached in `data/`, so past weeks are fast after the first load. Live prices are re-checked every couple of minutes.
- **Local only:** the web server only accepts connections from your own computer, because it can start paid runs and has no login.

### For developers

```bash
python -m capper_tracker.server --reload     # restart automatically when Python files change
cd web && npm run dev                        # live-reloading UI at http://localhost:5173
```

The dev UI forwards `/api` requests to the Python server on port 8000. Interactive API docs are at http://127.0.0.1:8000/api/docs.

---

## Known limitations

- **X API:** built and tested against simulated responses. When first setting up, try one or two accounts and check the **Posts** tab.
- **Player teams:** a player's team comes from the post and the AI's knowledge, so a mid-season trade can mislabel a team.
- **Grading coverage:** football props and game lines are graded from box scores; other sports' props are graded from Kalshi's settlement when Kalshi lists them, and otherwise show as ungraded.
- **Kalshi coverage:** Kalshi doesn't list every player, every line, college player props, NHL shots on goal, or NBA preseason props. Those picks show the reason instead of a price.
- **Video posts:** Claude only sees a preview frame.
- **Very busy accounts:** X only returns an account's most recent ~3,200 posts, so a very busy account's older weeks may be out of reach.
