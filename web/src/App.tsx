import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { Stat } from "./components/bits";
import { fmtAgo } from "./format";
import type { Report, ResultsReport, Scope, TrendsReport, WeekInfo } from "./types";
import { Consensus } from "./views/Consensus";
import { Control } from "./views/Control";
import { Results } from "./views/Results";
import { Trends } from "./views/Trends";
import { AllBets, Conflicts, Guide, Picks, Players, Posts, Themes } from "./views/Tables";

const TABS = [
  { id: "consensus", label: "Consensus" },
  { id: "results", label: "Results" },
  { id: "trends", label: "Trends" },
  { id: "themes", label: "Team themes" },
  { id: "players", label: "Players" },
  { id: "bets", label: "All bets" },
  { id: "conflicts", label: "Conflicts" },
  { id: "picks", label: "Picks" },
  { id: "posts", label: "Posts" },
  { id: "guide", label: "Guide" },
  { id: "control", label: "Run & accounts" },
] as const;
type TabId = (typeof TABS)[number]["id"];

const scopeKey = (s: Scope) => (s.kind === "week" ? `w${s.week}` : `d${s.days}`);
const parseScope = (k: string): Scope | null => {
  const m = /^([wd])(\d+)$/.exec(k);
  if (!m) return null;
  return m[1] === "w" ? { kind: "week", week: +m[2] } : { kind: "days", days: +m[2] };
};

function readHash(): { tab: TabId; scope: string | null } {
  const [tab, scope] = location.hash.replace(/^#\/?/, "").split("/");
  return { tab: (TABS.some((t) => t.id === tab) ? tab : "consensus") as TabId, scope: scope || null };
}

export default function App() {
  const initial = readHash();
  const [tab, setTab] = useState<TabId>(initial.tab);
  const [scope, setScope] = useState<Scope | null>(initial.scope ? parseScope(initial.scope) : null);
  const [weeks, setWeeks] = useState<WeekInfo[]>([]);
  const [currentWeek, setCurrentWeek] = useState<number | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<{ key: string; data: ResultsReport } | null>(null);
  const [resultsLoading, setResultsLoading] = useState(false);
  const resultsTried = useRef<string | null>(null); // don't auto-retry a window that just failed
  const [trends, setTrends] = useState<TrendsReport | null>(null);
  const [trendsLoading, setTrendsLoading] = useState(false);
  const trendsTried = useRef(false);

  const loadWeeks = useCallback(() =>
    api.weeks().then((w) => {
      setWeeks(w.weeks);
      setCurrentWeek(w.current_week);
      setScope((s) => s ?? { kind: "week", week: w.current_week });
    }).catch((e) => setError(e.message)), []);

  useEffect(() => { loadWeeks(); }, [loadWeeks]);

  const loadReport = useCallback(async (s: Scope, refresh = false) => {
    setLoading(true); setError(null);
    try { setReport(await api.report(s, refresh)); }
    catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { if (scope) loadReport(scope); }, [scope, loadReport]);

  // Results need box scores, so they load only when the tab is opened.
  const loadResults = useCallback(async (s: Scope, refresh = false) => {
    resultsTried.current = scopeKey(s);
    setResultsLoading(true); setError(null);
    try { setResults({ key: scopeKey(s), data: await api.results(s, refresh) }); }
    catch (e: any) { setError(e.message); }
    finally { setResultsLoading(false); }
  }, []);

  // Trends cover every week, independent of the week picker.
  const loadTrends = useCallback(async (refresh = false) => {
    trendsTried.current = true;
    setTrendsLoading(true); setError(null);
    try { setTrends(await api.trends(refresh)); }
    catch (e: any) { setError(e.message); }
    finally { setTrendsLoading(false); }
  }, []);

  useEffect(() => {
    if (tab === "trends" && !trends && !trendsLoading && !trendsTried.current) loadTrends();
  }, [tab, trends, trendsLoading, loadTrends]);

  useEffect(() => {
    if (tab === "results" && scope && results?.key !== scopeKey(scope) && !resultsLoading
        && resultsTried.current !== scopeKey(scope)) loadResults(scope);
  }, [tab, scope, results?.key, resultsLoading, loadResults]);

  useEffect(() => {
    if (scope) history.replaceState(null, "", `#/${tab}/${scopeKey(scope)}`);
  }, [tab, scope]);

  useEffect(() => {
    const onHash = () => {
      const h = readHash();
      setTab(h.tab);
      const s = h.scope && parseScope(h.scope);
      if (s) setScope((cur) => (cur && scopeKey(cur) === scopeKey(s) ? cur : s));
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const scopeOptions = useMemo(() => {
    const ws = new Set(weeks.map((w) => w.week));
    if (currentWeek) ws.add(currentWeek);
    const opts = [...ws].sort((a, b) => b - a).map((w) => ({
      key: `w${w}`, label: w === currentWeek ? `Week ${w} · this week` : `Week ${w}`,
    }));
    return [...opts, { key: "d3", label: "Last 3 days" }, { key: "d7", label: "Last 7 days" }, { key: "d14", label: "Last 14 days" }];
  }, [weeks, currentWeek]);

  const onRunFinished = useCallback(() => {
    loadWeeks();
    setResults(null);
    resultsTried.current = null;
    setTrends(null);
    trendsTried.current = false;
    if (scope) loadReport(scope, true);
  }, [scope, loadWeeks, loadReport]);

  const s = report?.summary;
  const counts: Partial<Record<TabId, number>> = report ? {
    consensus: report.frames["Top Consensus"]?.rows.length,
    themes: report.frames["Team Themes"]?.rows.length,
    players: report.frames["Players"]?.rows.length,
    bets: report.frames["All Bets"]?.rows.length,
    conflicts: report.frames["Conflicts"]?.rows.length,
    picks: report.frames["Picks"]?.rows.length,
    posts: report.frames["Tweets"]?.rows.length,
  } : {};

  const keyError = report?.frames["Tweets"]?.rows.some((r) => r.extracted === -1 && /401|x-api-key|authentication/i.test(r.extract_error ?? ""));

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">CT</span>
          <div>
            <div className="brand-name">Capper Tracker</div>
            <div className="brand-sub muted">{report?.window.label ?? "Loading…"}</div>
          </div>
        </div>
        <div className="row gap">
          <select className="scope" value={scope ? scopeKey(scope) : ""} onChange={(e) => setScope(parseScope(e.target.value))}>
            {scopeOptions.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
          </select>
          <button className="btn" onClick={() => { if (!scope) return; loadReport(scope, true); if (tab === "results") loadResults(scope, true); if (tab === "trends") loadTrends(true); }}
                  disabled={loading || resultsLoading || trendsLoading || !scope}
                  title="Rebuild from the database and re-check game status on ESPN">
            {loading || resultsLoading || trendsLoading ? "Building…" : "Refresh"}
          </button>
        </div>
      </header>

      {s && tab !== "trends" && (
        <div className="stats">
          <Stat label="Shared bets to play" value={s.shared_bets} tone={s.shared_bets ? "good" : undefined} />
          <Stat label="Picks" value={s.picks.toLocaleString()} />
          <Stat label="Accounts with picks" value={s.accounts_with_picks} />
          <Stat label="Posts" value={s.posts.toLocaleString()} />
          <Stat label="Conflicts" value={s.conflicts} />
          <Stat label="Failed / unprocessed" value={`${s.failed_posts} / ${s.pending_posts}`}
                tone={s.failed_posts + s.pending_posts ? "warn" : undefined}
                hint="Posts the model failed on, and posts not sent to the model yet. Neither counts toward picks." />
        </div>
      )}

      {s && tab !== "trends" && (s.failed_posts > 0 || s.pending_posts > 0) && (
        <div className="banner warn">
          <strong>{s.failed_posts + s.pending_posts} of {s.posts} posts in this window have no picks extracted.</strong>{" "}
          {keyError ? <>Anthropic rejected the API key (401). Update <code>ANTHROPIC_API_KEY</code> in <code>.env</code>, then </> : <>To include them, </>}
          run <button className="linkish" onClick={() => setTab("control")}>Extract</button> for this window.
        </div>
      )}
      {error && <div className="banner err">{error}</div>}

      <nav className="tabs">
        {TABS.map((t) => (
          <button key={t.id} className={`tab ${tab === t.id ? "on" : ""}`} onClick={() => setTab(t.id)}>
            {t.label}
            {counts[t.id] != null && <span className="tab-count">{counts[t.id]}</span>}
          </button>
        ))}
      </nav>

      <main className={loading && report ? "dim" : undefined}>
        {tab === "trends" ? (
          trends ? <div className={trendsLoading ? "dim" : undefined}><Trends data={trends} /></div> :
            <div className="empty big">{trendsLoading ? "Grading every week against ESPN box scores… the first load can take a minute; after that it's cached." : "No trends loaded."}</div>
        ) : tab === "results" ? (
          results && scope && results.key === scopeKey(scope) ? <div className={resultsLoading ? "dim" : undefined}><Results data={results.data} /></div> :
            <div className="empty big">{resultsLoading ? "Grading picks against ESPN box scores… the first load for a week fetches every game and can take ~20s." : "No results loaded."}</div>
        ) : tab === "control" ? (
          currentWeek != null && <Control currentWeek={currentWeek} weeks={weeks} onFinished={onRunFinished} />
        ) : !report ? (
          <div className="empty big">{loading ? "Building report… the first load checks game schedules on ESPN and can take ~10s." : "No report loaded."}</div>
        ) : (
          <>
            {tab === "consensus" && <Consensus report={report} />}
            {tab === "themes" && <Themes report={report} />}
            {tab === "players" && <Players report={report} />}
            {tab === "bets" && <AllBets report={report} />}
            {tab === "conflicts" && <Conflicts report={report} />}
            {tab === "picks" && <Picks report={report} />}
            {tab === "posts" && <Posts report={report} />}
            {tab === "guide" && <Guide report={report} />}
          </>
        )}
      </main>

      {report && <footer className="muted small">Report built {fmtAgo(report.built_at)} · game times in ET from ESPN</footer>}
    </div>
  );
}
