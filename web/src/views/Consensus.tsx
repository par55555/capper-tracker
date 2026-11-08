import { useMemo, useState } from "react";
import { Chips, Handles, KalshiPrice, League, StatusPill } from "../components/bits";
import { fmtGameTime, fmtNum, fmtOdds } from "../format";
import type { Row, WeekPayload } from "../types";

function BetCard({ r, live }: { r: Row; live: boolean }) {
  const details = [
    r.lines && `Line ${r.lines}`,
    r.median_odds_straight != null && `Typical price ${fmtOdds(r.median_odds_straight)}`,
    r.in_parlays > 0 && `${r.in_parlays} in parlays`,
    r.total_units > 0 && `${fmtNum(r.total_units)}u total`,
    r.repeat_posts > 0 && `${r.repeat_posts} repeat post${r.repeat_posts > 1 ? "s" : ""}`,
  ].filter(Boolean);
  return (
    <article className={`bet-card ${r.game_status === "Final" ? "final" : ""}`}>
      <div className="bet-count" title={`${r.accounts} tracked accounts posted this bet`}>
        <span className="n">{r.accounts}</span>
        <span className="lbl">accounts</span>
      </div>
      <div className="bet-body">
        <div className="bet-meta">
          <League league={r.league} />
          {r.game && <span className="game">{r.game}</span>}
          {r.game_time_et && <span className="muted">{fmtGameTime(r.game_time_et)}</span>}
          <StatusPill status={r.game_status} />
        </div>
        <div className="bet-title-row">
          <h3 className="bet-title">{r.bet}</h3>
          <KalshiPrice row={r} live={live && r.game_status !== "Final"} />
        </div>
        <p className="bet-means">{r.what_it_means}</p>
        {details.length > 0 && <div className="bet-details">{details.map((d, i) => <span key={i}>{d}</span>)}</div>}
        <Handles who={r.who} max={10} />
      </div>
    </article>
  );
}

function useFiltered(rows: Row[], league: string | null, q: string) {
  return useMemo(() => {
    const needle = q.trim().toLowerCase();
    return rows.filter((r) =>
      (!league || r.league === league) &&
      (!needle || [r.bet, r.what_it_means, r.who, r.game].some((v) => String(v ?? "").toLowerCase().includes(needle))));
  }, [rows, league, q]);
}

export function Consensus({ report, live = false }: { report: WeekPayload; live?: boolean }) {
  const top = report.frames["Top Consensus"]?.rows ?? [];
  const played = report.frames["Already Played"]?.rows ?? [];
  const [league, setLeague] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [showPlayed, setShowPlayed] = useState(false);
  const leagues = useMemo(
    () => [...new Set([...top, ...played].map((r) => r.league).filter(Boolean))].sort() as string[],
    [top, played]);
  const topF = useFiltered(top, league, q);
  const playedF = useFiltered(played, league, q);
  const playedOpen = showPlayed || top.length === 0;

  return (
    <div className="stack">
      <div className="filters">
        <input className="search" placeholder="Search bets, players, accounts…" value={q} onChange={(e) => setQ(e.target.value)} />
        <Chips options={leagues} value={league} onChange={setLeague} />
      </div>

      <section>
        <h2 className="section-title">Still to play <span className="muted">{topF.length}</span></h2>
        <p className="section-sub">Bets two or more tracked accounts share on games that haven't finished. Most shared first, then soonest game.</p>
        {topF.length ? (
          <div className="bet-list">{topF.map((r, i) => <BetCard key={i} r={r} live={live} />)}</div>
        ) : (
          <div className="empty">
            {top.length ? "No bets match your filters." :
              played.length ? "Every shared bet in this window is on a game that's already final." :
                "No bet is shared by two or more accounts in this window yet."}
          </div>
        )}
      </section>

      {played.length > 0 && (
        <section>
          <button className="section-toggle" onClick={() => setShowPlayed((s) => !s)} disabled={top.length === 0}>
            <h2 className="section-title">{top.length > 0 && <span className="caret">{playedOpen ? "▾" : "▸"}</span>} Already played <span className="muted">{playedF.length}</span></h2>
          </button>
          <p className="section-sub">Shared bets whose game was final when the report was built. Useful for checking how consensus did.</p>
          {playedOpen && <div className="bet-list">{playedF.map((r, i) => <BetCard key={i} r={r} live={false} />)}</div>}
        </section>
      )}
    </div>
  );
}
