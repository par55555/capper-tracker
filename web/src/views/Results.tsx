import { useMemo, useState } from "react";
import { DataTable, type Column } from "../components/DataTable";
import { Chips, Handles, League, Stat, TweetLink } from "../components/bits";
import { fmtGameTime, fmtNum, fmtOdds, fmtPostTime } from "../format";
import type { Record3, ResultsReport, Row } from "../types";

const RESULTS = ["Won", "Lost", "Push", "Split", "Pending", "Ungraded"] as const;
const ICON: Record<string, string> = { Won: "✓", Lost: "✗", Push: "=", Split: "½", Pending: "…", Ungraded: "–" };

export function ResultBadge({ result }: { result?: string | null }) {
  if (!result) return null;
  return <span className={`result result-${result.toLowerCase()}`}>{ICON[result]} {result}</span>;
}

const pct = (r: number | null | undefined) => (r == null ? "—" : `${Math.round(r * 100)}%`);
const rec = (r: Record3) => `${r.won}–${r.lost}${r.push ? `–${r.push}` : ""}`;

/** Hit rate per agreement tier: thin horizontal bars against a 50% reference. */
export function AgreementChart({ rows }: { rows: Row[] }) {
  const [hover, setHover] = useState<number | null>(null);
  if (!rows.length) return null;
  return (
    <figure className="chart">
      <figcaption>
        <h3>Hit rate by how many accounts agreed</h3>
        <p className="section-sub">Each bet counts once. Bars show won ÷ (won + lost); pushes, splits and ungraded bets are left out.</p>
      </figcaption>
      <div className="hbars" role="table" aria-label="Hit rate by number of accounts">
        {rows.map((r, i) => {
          const graded = r.won + r.lost;
          return (
            <div key={r.tier} className={`hbar-row ${hover === i ? "hover" : ""}`} role="row"
                 onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <span className="hbar-label" role="rowheader">{r.tier}</span>
              <span className="hbar-track" role="cell">
                <span className="hbar-ref" style={{ left: "50%" }} />
                {graded > 0 && <span className="hbar-fill" style={{ width: `${(r.hit_rate ?? 0) * 100}%` }} />}
                {hover === i && (
                  <span className="tooltip" role="tooltip">
                    <strong>{r.tier}</strong>
                    <span>{r.bets} bets · {r.won} won · {r.lost} lost{r.push ? ` · ${r.push} push` : ""}</span>
                    {(r.split > 0 || r.pending > 0 || r.ungraded > 0) && (
                      <span className="muted">{[r.split && `${r.split} split`, r.pending && `${r.pending} pending`, r.ungraded && `${r.ungraded} ungraded`].filter(Boolean).join(" · ")}</span>
                    )}
                  </span>
                )}
              </span>
              <span className="hbar-value" role="cell">
                <strong>{graded ? pct(r.hit_rate) : "—"}</strong>
                <span className="muted small">{graded ? `${r.won}–${r.lost}` : "none graded"}</span>
              </span>
            </div>
          );
        })}
        <div className="hbar-axis"><span /><span className="hbar-axis-track"><span style={{ left: 0 }}>0%</span><span style={{ left: "50%" }}>50%</span><span style={{ left: "100%" }}>100%</span></span><span /></div>
      </div>
    </figure>
  );
}

function BetsTable({ rows }: { rows: Row[] }) {
  const [result, setResult] = useState<string | null>(null);
  const [league, setLeague] = useState<string | null>(null);
  const [minAccts, setMinAccts] = useState(2);
  const leagues = useMemo(() => [...new Set(rows.map((r) => r.league).filter(Boolean))].sort() as string[], [rows]);
  const present = useMemo(() => RESULTS.filter((x) => rows.some((r) => r.result === x)), [rows]);
  const filtered = useMemo(() => rows.filter((r) =>
    r.accounts >= minAccts && (!result || r.result === result) && (!league || r.league === league)), [rows, result, league, minAccts]);
  const cols: Column[] = [
    { key: "result", label: "Result", render: (r) => <ResultBadge result={r.result} />,
      sortValue: (r) => RESULTS.indexOf(r.result) },
    { key: "accounts", label: "Accts", align: "right", render: (r) => <strong>{r.accounts}</strong> },
    { key: "bet", label: "Bet", render: (r) => <span className="wrap">{r.bet}</span> },
    { key: "lines", label: "Lines" },
    { key: "actual", label: "What happened", render: (r) => <span className={`wrap ${r.result === "Ungraded" ? "muted" : ""}`}>{r.actual}</span> },
    { key: "game", label: "Game", render: (r) => <><League league={r.league} /> {r.game} <span className="muted small">{fmtGameTime(r.game_time_et)}</span></>,
      sortValue: (r) => r.game_time_et },
    { key: "who", label: "Who", render: (r) => r.result === "Split"
        ? <span className="split-who"><span><b className="pos">✓</b> <Handles who={r.who_won} max={4} /></span><span><b className="neg">✗</b> <Handles who={r.who_lost} max={4} /></span></span>
        : <Handles who={r.who} max={4} /> },
  ];
  return (
    <DataTable rows={filtered} columns={cols} searchKeys={["bet", "actual", "game", "who"]}
      rowClass={(r) => `row-${String(r.result).toLowerCase()}`}
      toolbar={<>
        <Chips options={present as unknown as string[]} value={result} onChange={setResult} all="Any result" />
        <Chips options={leagues} value={league} onChange={setLeague} />
        <label className="check">Min accounts
          <select value={minAccts} onChange={(e) => setMinAccts(Number(e.target.value))}>
            {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}+</option>)}
          </select>
        </label>
      </>} />
  );
}

function Cappers({ rows }: { rows: Row[] }) {
  const [minGraded, setMinGraded] = useState(5);
  const filtered = useMemo(() => rows.filter((r) => r.won + r.lost >= minGraded), [rows, minGraded]);
  const cols: Column[] = [
    { key: "handle", label: "Account", render: (r) => <a className="handle" href={`https://x.com/${r.handle}`} target="_blank" rel="noreferrer">@{r.handle}</a> },
    { key: "hit_rate", label: "Hit rate", render: (r) => (
        <span className="mini-bar"><span className="mini-track"><span className="mini-fill" style={{ width: `${(r.hit_rate ?? 0) * 100}%` }} /><span className="mini-ref" /></span><strong>{pct(r.hit_rate)}</strong></span>) },
    { key: "won", label: "Record (W–L–P)", render: (r) => rec(r as unknown as Record3), sortValue: (r) => r.won - r.lost },
    { key: "graded", label: "Graded", align: "right", render: (r) => r.won + r.lost + r.push, sortValue: (r) => r.won + r.lost + r.push },
    { key: "shared_won", label: "On shared bets", render: (r) => r.shared_won + r.shared_lost ? `${r.shared_won}–${r.shared_lost}` : <span className="muted">—</span>,
      title: "Record on bets that 2+ tracked accounts also posted", sortValue: (r) => r.shared_won - r.shared_lost },
    { key: "straight_record", label: "Straight bets", title: "Record on non-parlay picks only" },
    { key: "pending", label: "Pending", align: "right" },
    { key: "ungraded", label: "Ungraded", align: "right" },
  ];
  return (
    <DataTable rows={filtered} columns={cols} initialSort={{ key: "hit_rate", dir: "desc" }} searchKeys={["handle"]}
      empty="No account has enough graded picks in this window."
      toolbar={<label className="check">Min graded picks
        <select value={minGraded} onChange={(e) => setMinGraded(Number(e.target.value))}>
          {[1, 5, 10, 20].map((n) => <option key={n} value={n}>{n}+</option>)}
        </select>
      </label>} />
  );
}

function EveryPick({ rows }: { rows: Row[] }) {
  const [result, setResult] = useState<string | null>(null);
  const present = useMemo(() => RESULTS.filter((x) => rows.some((r) => r.result === x)), [rows]);
  const filtered = useMemo(() => rows.filter((r) => !result || r.result === result), [rows, result]);
  const cols: Column[] = [
    { key: "result", label: "Result", render: (r) => <ResultBadge result={r.result} />, sortValue: (r) => RESULTS.indexOf(r.result) },
    { key: "handle", label: "Account", render: (r) => <span className="handle">@{r.handle}</span> },
    { key: "label", label: "Bet", render: (r) => <span className="wrap">{r.label}</span> },
    { key: "line", label: "Line", align: "right", render: (r) => fmtNum(r.line) },
    { key: "odds", label: "Odds", align: "right", render: (r) => fmtOdds(r.odds) },
    { key: "actual_text", label: "What happened", render: (r) => <span className="wrap">{r.actual_text}</span> },
    { key: "parlay_group", label: "", render: (r) => r.parlay_group ? <span className="tag">parlay leg</span> : "" },
    { key: "created_at", label: "Posted", render: (r) => <span className="nowrap">{fmtPostTime(r.created_at)}</span> },
    { key: "url", label: "", render: (r) => <TweetLink url={r.url} /> },
  ];
  return (
    <DataTable rows={filtered} columns={cols} searchKeys={["handle", "label", "actual_text", "snippet"]}
      rowClass={(r) => `row-${String(r.result).toLowerCase()}`}
      toolbar={<Chips options={present as unknown as string[]} value={result} onChange={setResult} all="Any result" />} />
  );
}

const SUB = [
  { id: "bets", label: "Bets" },
  { id: "cappers", label: "Cappers" },
  { id: "picks", label: "Every pick" },
] as const;

export function Results({ data }: { data: ResultsReport }) {
  const [sub, setSub] = useState<(typeof SUB)[number]["id"]>("bets");
  const { shared, all_picks, pending, ungraded } = data.summary;
  const f = (n: string) => data.frames[n]?.rows ?? [];
  return (
    <div className="stack">
      <div>
        <div className="stats results-stats">
          <Stat label={`Shared bets (2+ accounts) · ${pct(shared.hit_rate)}`} value={rec(shared)}
                hint="Record of bets two or more tracked accounts posted. Each bet counts once." />
          <Stat label={`All picks · ${pct(all_picks.hit_rate)}`} value={rec(all_picks)}
                hint="Every account's picks, each account once per bet. Parlay legs are graded as single legs." />
          <Stat label="Pending" value={pending} hint="Games not final yet" />
          <Stat label="Not graded" value={ungraded}
                hint="Mostly non-football player props and custom markets. See 'What happened' for the reason." />
        </div>
        <p className="section-sub note">
          Graded from ESPN box scores. Hit rate isn't profit: most picks here are plus-money TD props, which can hit
          well under 50% and still make money.
        </p>
      </div>

      <AgreementChart rows={f("By Agreement")} />

      <div>
        <div className="segmented sub-tabs">
          {SUB.map((s) => <button key={s.id} className={sub === s.id ? "on" : ""} onClick={() => setSub(s.id)}>{s.label}</button>)}
        </div>
        {sub === "bets" && <BetsTable rows={f("Bet Results")} />}
        {sub === "cappers" && <Cappers rows={f("Capper Records")} />}
        {sub === "picks" && <EveryPick rows={f("Pick Results")} />}
      </div>
    </div>
  );
}
