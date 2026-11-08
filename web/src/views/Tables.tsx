import { useMemo, useState } from "react";
import { DataTable, type Column } from "../components/DataTable";
import { Chips, Handles, KalshiPrice, League, LeanBar, Net, StatusPill, TweetLink } from "../components/bits";
import { fmtGameTime, fmtNum, fmtOdds, fmtPostTime, fmtProb } from "../format";
import type { Row, WeekPayload } from "../types";

const rowsOf = (r: WeekPayload, name: string) => r.frames[name]?.rows ?? [];
const leaguesOf = (rows: Row[]) => [...new Set(rows.map((r) => r.league).filter(Boolean))].sort() as string[];

// --- Team Themes ------------------------------------------------------------

export function Themes({ report }: { report: WeekPayload }) {
  const rows = rowsOf(report, "Team Themes");
  const cols: Column[] = [
    { key: "team", label: "Team", render: (r) => <strong>{r.team}</strong> },
    { key: "theme", label: "Theme", render: (r) => r.theme ? <span className={`theme theme-${themeClass(r.theme)}`}>{r.theme}</span> : <span className="muted">—</span> },
    { key: "pass_net", label: "Pass net", align: "right", render: (r) => <Net n={r.pass_net} />, title: "Accounts on pass-game overs minus unders (QB passing + receiving)" },
    { key: "run_net", label: "Run net", align: "right", render: (r) => <Net n={r.run_net} />, title: "Accounts on rushing overs minus unders" },
    { key: "side_backers", label: "Side backers", align: "right", title: "Accounts backing this team's spread or moneyline" },
    { key: "td_scorer_accounts", label: "TD scorer accts", align: "right" },
    { key: "pass_players", label: "Pass game players", render: (r) => <span className="wrap">{r.pass_players}</span> },
    { key: "run_players", label: "Run game players", render: (r) => <span className="wrap">{r.run_players}</span> },
    { key: "td_players", label: "TD scorers", render: (r) => <span className="wrap">{r.td_players}</span> },
  ];
  return (
    <>
      <p className="section-sub">NFL only, games not yet finished. Net = distinct accounts on overs minus distinct accounts on unders.</p>
      <DataTable rows={rows} columns={cols} searchKeys={["team", "theme", "pass_players", "run_players", "td_players"]}
                 empty="No NFL props on unfinished games in this window." />
    </>
  );
}

function themeClass(t: string) {
  if (t.startsWith("Pass +")) return "both";
  if (t.startsWith("Passing")) return "pass";
  if (t.startsWith("Rushing")) return "run";
  return "fade";
}

// --- Players ----------------------------------------------------------------

export function Players({ report }: { report: WeekPayload }) {
  const all = rowsOf(report, "Players");
  const [league, setLeague] = useState<string | null>(null);
  const [onlyConflicts, setOnlyConflicts] = useState(false);
  const rows = useMemo(() => all.filter((r) => (!league || r.league === league) && (!onlyConflicts || r.conflicting_sides)), [all, league, onlyConflicts]);
  const cols: Column[] = [
    { key: "player", label: "Player", render: (r) => <strong>{r.player}</strong> },
    { key: "team", label: "Team" },
    { key: "league", label: "League", render: (r) => <League league={r.league} /> },
    { key: "accounts", label: "Accounts", align: "right" },
    { key: "net_lean", label: "Over – under", render: (r) => <LeanBar over={r.accounts_over_or_yes} under={r.accounts_under_or_no} />, sortValue: (r) => r.net_lean },
    { key: "bets", label: "Bets", render: (r) => <span className="wrap">{r.bets}</span> },
    { key: "conflicting_sides", label: "Conflict", align: "center", render: (r) => r.conflicting_sides ? <span className="flag">⚠</span> : "" },
    { key: "who", label: "Who", render: (r) => <Handles who={r.who} max={4} /> },
  ];
  return (
    <DataTable rows={rows} columns={cols} searchKeys={["player", "team", "bets", "who"]}
      toolbar={<>
        <Chips options={leaguesOf(all)} value={league} onChange={setLeague} />
        <label className="check"><input type="checkbox" checked={onlyConflicts} onChange={(e) => setOnlyConflicts(e.target.checked)} /> Conflicts only</label>
      </>} />
  );
}

// --- All Bets ---------------------------------------------------------------

export function AllBets({ report }: { report: WeekPayload }) {
  const all = rowsOf(report, "All Bets");
  const [league, setLeague] = useState<string | null>(null);
  const [minAccts, setMinAccts] = useState(1);
  const rows = useMemo(() => all.filter((r) => (!league || r.league === league) && r.accounts >= minAccts), [all, league, minAccts]);
  const cols: Column[] = [
    { key: "accounts", label: "Accts", align: "right", render: (r) => <strong>{r.accounts}</strong> },
    { key: "bet", label: "Bet", render: (r) => <span className="wrap" title={r.what_it_means}>{r.bet}</span> },
    { key: "game", label: "Game", render: (r) => <>{r.game} <span className="muted small">{fmtGameTime(r.game_time_et)}</span></>, sortValue: (r) => r.game_time_et },
    { key: "game_status", label: "Status", render: (r) => <StatusPill status={r.game_status} /> },
    { key: "lines", label: "Lines", sortValue: (r) => r.median_line },
    { key: "kalshi_now", label: "Kalshi", render: (r) => <KalshiPrice row={r} live={report.window.live && r.game_status !== "Final"} />,
      sortValue: (r) => r.kalshi_now ?? r.kalshi_first },
    { key: "median_odds_straight", label: "Posted odds", align: "right", render: (r) => fmtOdds(r.median_odds_straight) },
    { key: "in_parlays", label: "In parlays", align: "right" },
    { key: "total_units", label: "Units", align: "right", render: (r) => fmtNum(r.total_units) || "" },
    { key: "category", label: "Category" },
    { key: "who", label: "Who", render: (r) => <Handles who={r.who} max={3} /> },
  ];
  return (
    <DataTable rows={rows} columns={cols} searchKeys={["bet", "what_it_means", "game", "who", "category"]}
      toolbar={<>
        <Chips options={leaguesOf(all)} value={league} onChange={setLeague} />
        <label className="check">Min accounts
          <select value={minAccts} onChange={(e) => setMinAccts(Number(e.target.value))}>
            {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}+</option>)}
          </select>
        </label>
      </>} />
  );
}

// --- Conflicts --------------------------------------------------------------

export function Conflicts({ report }: { report: WeekPayload }) {
  const rows = rowsOf(report, "Conflicts");
  if (!rows.length) return <div className="empty">No market has tracked accounts on opposite sides in this window.</div>;
  return (
    <div className="conflict-list">
      {rows.map((r, i) => (
        <article key={i} className="conflict">
          <h3>{r.market} <span className="muted small">{r.lines && `· ${r.lines}`}</span></h3>
          <div className="conflict-sides">
            <div><div className="side-label pos">Over / Yes</div><Handles who={r.over_or_yes} max={20} /></div>
            <div><div className="side-label neg">Under / No</div><Handles who={r.under_or_no} max={20} /></div>
          </div>
        </article>
      ))}
    </div>
  );
}

// --- Picks ------------------------------------------------------------------

export function Picks({ report }: { report: WeekPayload }) {
  const all = rowsOf(report, "Picks");
  const [league, setLeague] = useState<string | null>(null);
  const rows = useMemo(() => all.filter((r) => !league || r.league === league), [all, league]);
  const cols: Column[] = [
    { key: "created_at", label: "Posted", render: (r) => <span className="nowrap">{fmtPostTime(r.created_at)}</span> },
    { key: "handle", label: "Account", render: (r) => <a className="handle" href={`https://x.com/${r.handle}`} target="_blank" rel="noreferrer">@{r.handle}</a> },
    { key: "label", label: "Bet", render: (r) => <span className="wrap">{r.label}</span> },
    { key: "line", label: "Line", align: "right", render: (r) => fmtNum(r.line) },
    { key: "odds", label: "Odds", align: "right", render: (r) => fmtOdds(r.odds) },
    { key: "kalshi_prob_post", label: "Kalshi at post", align: "right",
      render: (r) => r.kalshi_prob_post != null
        ? <span title={`${r.kalshi_title}${r.kalshi_match === "interpolated" ? "\n" + r.kalshi_note : ""}`}>{r.kalshi_match === "interpolated" ? "≈" : ""}{fmtProb(r.kalshi_prob_post)}</span>
        : <span className="muted small" title={r.kalshi_note || "No Kalshi market"}>—</span> },
    { key: "units", label: "Units", align: "right", render: (r) => fmtNum(r.units) },
    { key: "game", label: "Game", render: (r) => <>{r.game} <span className="muted small">{fmtGameTime(r.game_time_et)}</span></>, sortValue: (r) => r.game_time_et },
    { key: "flags", label: "", render: (r) => <>
        {r.parlay_group && <span className="tag" title={`Parlay leg (${r.parlay_group})`}>parlay</span>}
        {r.times_posted > 1 && <span className="tag" title="Posted more than once; counted once">×{r.times_posted}</span>}
        {r.switched_side && <span className="tag warn" title="Account switched sides; only the latest side counts">switched</span>}
      </> },
    { key: "snippet", label: "Snippet", render: (r) => <span className="wrap snippet">{r.snippet}</span> },
    { key: "url", label: "", render: (r) => <TweetLink url={r.url} /> },
  ];
  return (
    <DataTable rows={rows} columns={cols} initialSort={{ key: "created_at", dir: "desc" }}
      searchKeys={["handle", "label", "player", "team", "snippet", "game"]}
      toolbar={<Chips options={leaguesOf(all)} value={league} onChange={setLeague} />} />
  );
}

// --- Posts (Tweets) ---------------------------------------------------------

export function Posts({ report }: { report: WeekPayload }) {
  const all = rowsOf(report, "Tweets");
  const types = useMemo(() => [...new Set(all.map((r) => statusOf(r)))].sort(), [all]);
  const [type, setType] = useState<string | null>(null);
  const rows = useMemo(() => all.filter((r) => !type || statusOf(r) === type), [all, type]);
  const cols: Column[] = [
    { key: "created_at", label: "Posted", render: (r) => <span className="nowrap">{fmtPostTime(r.created_at)}</span> },
    { key: "handle", label: "Account", render: (r) => <span className="handle">@{r.handle}</span> },
    { key: "post_type", label: "Type", render: (r) => <span className={`tag ${r.extracted === -1 ? "err" : r.tweet_type === "new_pick" ? "good" : ""}`}>{statusOf(r)}</span>, sortValue: statusOf },
    { key: "text", label: "Text", render: (r) => <span className="wrap post-text">{r.text}{r.extract_error && <span className="error-line">{shortError(r.extract_error)}</span>}</span> },
    { key: "url", label: "", render: (r) => <TweetLink url={r.url} /> },
  ];
  return (
    <DataTable rows={rows} columns={cols} initialSort={{ key: "created_at", dir: "desc" }}
      searchKeys={["handle", "text", "post_type", "extract_error"]}
      rowClass={(r) => (r.extracted === -1 ? "row-err" : undefined)}
      toolbar={<Chips options={types} value={type} onChange={setType} />} />
  );
}

const statusOf = (r: Row): string => (r.extracted === -1 ? "Failed" : r.post_type ?? "Not extracted yet");

function shortError(e: string) {
  if (/401|invalid x-api-key|authentication/i.test(e)) return "Failed: Anthropic API key rejected (401)";
  return "Failed: " + (e.length > 160 ? e.slice(0, 160) + "…" : e);
}

// --- Guide ------------------------------------------------------------------

export function Guide({ report }: { report: WeekPayload }) {
  const rows = rowsOf(report, "Guide");
  return (
    <dl className="guide">
      {rows.map((r, i) => (<div key={i}><dt>{r.item}</dt><dd>{r.meaning}</dd></div>))}
    </dl>
  );
}
