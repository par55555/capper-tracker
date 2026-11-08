import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { Stat } from "./components/bits";
import { fmtAgo } from "./format";
import type { ExportFile, Scope, TrendsReport, WeekInfo, WeekPayload } from "./types";
import { Consensus } from "./views/Consensus";
import { Control } from "./views/Control";
import { Results } from "./views/Results";
import { AllBets, Conflicts, Guide, Picks, Players, Posts, Themes } from "./views/Tables";
import { Trends } from "./views/Trends";
import { LiveBoard } from "./views/LiveBoard";

type Section = "live" | "history" | "analysis" | "manage";
const SECTIONS: { id: Section; label: string }[] = [
  { id: "live", label: "Live" },
  { id: "history", label: "History" },
  { id: "analysis", label: "Analysis" },
];

const WEEK_TABS = {
  results: "Results", consensus: "Consensus", bets: "All bets", players: "Players", themes: "Team themes",
  conflicts: "Conflicts", picks: "Picks", posts: "Posts", downloads: "Excel files",
} as const;
type WeekTab = keyof typeof WEEK_TABS;
const HISTORY_TABS: WeekTab[] = ["results", "consensus", "bets", "players", "themes", "conflicts", "picks", "posts", "downloads"];
const LIVE_REFRESH_MS = 120_000;

const scopeKey = (s: Scope) => (s.kind === "week" ? `w${s.week}` : `d${s.days}`);
const parseScope = (k: string | undefined): Scope | null => {
  const m = /^([wd])(\d+)$/.exec(k || "");
  return !m ? null : m[1] === "w" ? { kind: "week", week: +m[2] } : { kind: "days", days: +m[2] };
};

interface Route { section: Section; scope: string | null; tab: string | null }
function readRoute(): Route {
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  const section = (["live", "history", "analysis", "manage"].includes(parts[0]) ? parts[0] : "live") as Section;
  if (section === "history") return { section, scope: parts[1] || null, tab: parts[2] || null };
  if (section === "live") return { section, scope: parts[1] || null, tab: null };  // #/live/w6 = preview a past week
  return { section, scope: null, tab: parts[1] || null };
}

// --- one week's tabs (shared by Live and History) ------------------------------

function WeekTabs({ data, tabs, tab, setTab, live, exportsFor, goManage }: {
  data: WeekPayload; tabs: WeekTab[]; tab: WeekTab; setTab: (t: WeekTab) => void; live: boolean;
  exportsFor: number | null; goManage: () => void;
}) {
  const s = data.summary;
  const counts: Partial<Record<WeekTab, number>> = {
    consensus: data.frames["Top Consensus"]?.rows.length, bets: data.frames["All Bets"]?.rows.length,
    players: data.frames["Players"]?.rows.length, themes: data.frames["Team Themes"]?.rows.length,
    conflicts: data.frames["Conflicts"]?.rows.length, picks: data.frames["Picks"]?.rows.length,
    posts: data.frames["Tweets"]?.rows.length,
  };
  const keyError = data.frames["Tweets"]?.rows.some((r) => r.extracted === -1 && /401|x-api-key|authentication/i.test(r.extract_error ?? ""));
  const rs = data.results_summary;
  return (
    <>
      <div className="stats">
        {live ? <Stat label="Shared bets to play" value={s.shared_bets} tone={s.shared_bets ? "good" : undefined} />
              : <Stat label={`Shared bets · ${rs.shared.hit_rate != null ? Math.round(rs.shared.hit_rate * 100) + "%" : "—"}`}
                      value={`${rs.shared.won}–${rs.shared.lost}`} />}
        <Stat label="Picks" value={s.picks.toLocaleString()} />
        <Stat label="Accounts with picks" value={s.accounts_with_picks} />
        <Stat label="Priced on Kalshi" value={s.picks ? `${Math.round((s.priced / s.picks) * 100)}%` : "—"}
              hint={`${s.priced} of ${s.picks} picks matched to a Kalshi market`} />
        <Stat label="Posts" value={s.posts.toLocaleString()} />
        <Stat label="Failed / unprocessed" value={`${s.failed_posts} / ${s.pending_posts}`}
              tone={s.failed_posts + s.pending_posts ? "warn" : undefined}
              hint="Posts the model failed on, and posts not sent to the model yet. Neither counts toward picks." />
      </div>
      {(s.failed_posts > 0 || s.pending_posts > 0) && (
        <div className="banner warn">
          <strong>{s.failed_posts + s.pending_posts} of {s.posts} posts in this window have no picks extracted.</strong>{" "}
          {keyError ? <>Anthropic rejected the API key (401). Update <code>ANTHROPIC_API_KEY</code> in <code>.env</code>, then </> : <>To include them, </>}
          run <button className="linkish" onClick={goManage}>Extract</button> for this window.
        </div>
      )}
      <nav className="tabs">
        {tabs.map((t) => (
          <button key={t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>
            {WEEK_TABS[t]}{counts[t] != null && <span className="tab-count">{counts[t]}</span>}
          </button>
        ))}
      </nav>
      {tab === "results" && <Results data={{ window: data.window, built_at: data.built_at, summary: rs, frames: data.results }} />}
      {tab === "consensus" && <Consensus report={data} live={live} />}
      {tab === "bets" && <AllBets report={data} />}
      {tab === "players" && <Players report={data} />}
      {tab === "themes" && <Themes report={data} />}
      {tab === "conflicts" && <Conflicts report={data} />}
      {tab === "picks" && <Picks report={data} />}
      {tab === "posts" && <Posts report={data} />}
      {tab === "downloads" && <Downloads week={exportsFor} />}
    </>
  );
}

function Downloads({ week }: { week: number | null }) {
  const [files, setFiles] = useState<ExportFile[] | null>(null);
  useEffect(() => { api.exports(week ?? undefined).then((r) => setFiles(r.exports)).catch(() => setFiles([])); }, [week]);
  if (!files) return <div className="empty">Loading…</div>;
  if (!files.length) return <div className="empty">No Excel reports saved for this window. Runs save one to <code>exports/</code>.</div>;
  return (
    <div className="panel">
      <p className="section-sub">Each run saves a spreadsheet of that moment's report. Newest first.</p>
      <ul className="file-list">
        {files.map((f) => (
          <li key={f.name}>
            <a href={`/api/exports/${encodeURIComponent(f.name)}`} download>{f.name}</a>
            <span className="muted small">{new Date(f.modified).toLocaleString()} · {Math.round(f.size / 1024)} KB</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// --- app -----------------------------------------------------------------------

export default function App() {
  const initial = readRoute();
  const [section, setSection] = useState<Section>(initial.section);
  const [preview] = useState<Scope | null>(initial.section === "live" ? parseScope(initial.scope ?? undefined) : null);
  const [nextPull, setNextPull] = useState<string | null>(null);
  const [histTab, setHistTab] = useState<WeekTab>(initial.section === "history" && HISTORY_TABS.includes(initial.tab as WeekTab) ? initial.tab as WeekTab : "results");
  const [histScope, setHistScope] = useState<Scope | null>(parseScope(initial.scope ?? undefined));
  const [weeks, setWeeks] = useState<WeekInfo[]>([]);
  const [currentWeek, setCurrentWeek] = useState<number | null>(null);
  const [live, setLive] = useState<WeekPayload | null>(null);
  const [hist, setHist] = useState<Record<string, WeekPayload>>({});
  const [trends, setTrends] = useState<TrendsReport | null>(null);
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const tried = useRef<Set<string>>(new Set());

  const guard = useCallback(async (k: string, fn: () => Promise<void>) => {
    tried.current.add(k);
    setLoading((l) => ({ ...l, [k]: true })); setError(null);
    try { await fn(); } catch (e: any) { setError(e.message); } finally { setLoading((l) => ({ ...l, [k]: false })); }
  }, []);

  const loadWeeks = useCallback(() => api.weeks().then((w) => {
    setWeeks(w.weeks); setCurrentWeek(w.current_week); setNextPull(w.next_pull);
  }).catch((e) => setError(e.message)), []);
  const loadLive = useCallback((refresh = false) => guard("live", async () => setLive(await api.week(preview, refresh))), [guard, preview]);
  const loadHist = useCallback((s: Scope, refresh = false) =>
    guard(`h:${scopeKey(s)}`, async () => { const d = await api.week(s, refresh); setHist((h) => ({ ...h, [scopeKey(s)]: d })); }), [guard]);
  const loadTrends = useCallback((refresh = false) => guard("trends", async () => setTrends(await api.trends(refresh))), [guard]);

  useEffect(() => { loadWeeks(); }, [loadWeeks]);

  // History covers every week, this one included; it opens on the latest week that has picks.
  const histWeeks = useMemo(() => {
    const ws = [...weeks];
    if (currentWeek != null && !ws.some((w) => w.week === currentWeek))
      ws.unshift({ week: currentWeek, posts: 0, extracted: 0, failed: 0, pending: 0, picks: 0 });
    return ws.sort((a, b) => b.week - a.week);
  }, [weeks, currentWeek]);
  useEffect(() => {
    if (!histScope && histWeeks.length) {
      const w = histWeeks.find((x) => x.picks > 0) ?? histWeeks[0];
      if (w) setHistScope({ kind: "week", week: w.week });
    }
  }, [histScope, histWeeks]);

  // Load what the visible section needs (once; Refresh reloads).
  useEffect(() => {
    if ((section === "live" || section === "manage") && !live && !tried.current.has("live")) loadLive();
    if (section === "history" && histScope && !hist[scopeKey(histScope)] && !tried.current.has(`h:${scopeKey(histScope)}`)) loadHist(histScope);
    if (section === "analysis" && !trends && !tried.current.has("trends")) loadTrends();
  }, [section, histScope, live, hist, trends, loadLive, loadHist, loadTrends]);

  // Live prices: refresh every two minutes while the Live page is open.
  useEffect(() => {
    if (section !== "live") return;
    const t = setInterval(() => loadLive(), LIVE_REFRESH_MS);
    return () => clearInterval(t);
  }, [section, loadLive]);

  // URL mirrors where you are, so reloads and links keep your place.
  useEffect(() => {
    const h = section === "history" ? `#/history/${histScope ? scopeKey(histScope) : ""}/${histTab}`
      : section === "live" ? `#/live${preview ? `/${scopeKey(preview)}` : ""}` : `#/${section}`;
    history.replaceState(null, "", h);
  }, [section, histScope, histTab, preview]);

  const refresh = () => {
    if (section === "live") loadLive(true);
    if (section === "history" && histScope) loadHist(histScope, true);
    if (section === "analysis") loadTrends(true);
  };
  const onRunFinished = () => {
    loadWeeks(); setHist({}); setTrends(null); tried.current.clear(); loadLive(true);
  };

  const histKey = histScope ? scopeKey(histScope) : "";
  const histData = histScope ? hist[histKey] : undefined;
  const activeLoading = section === "live" ? loading.live : section === "history" ? loading[`h:${histKey}`] : section === "analysis" ? loading.trends : false;
  const builtAt = section === "live" ? live?.built_at : section === "history" ? histData?.built_at : section === "analysis" ? trends?.built_at : undefined;

  const scopeOptions = [
    ...histWeeks.map((w) => ({ key: `w${w.week}`,
      label: `Week ${w.week}${w.week === currentWeek ? " · this week" : ""}${w.picks ? "" : " (no picks)"}` })),
    { key: "d7", label: "Last 7 days" }, { key: "d14", label: "Last 14 days" }, { key: "d30", label: "Last 30 days" },
  ];
  const liveWeek = live ? live.window.name.replace(/^week0?/, "") : null;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">CT</span>
          <div>
            <div className="brand-name">Capper Tracker</div>
            <div className="brand-sub muted">
              {section === "live" && (live ? `Week ${liveWeek} · upcoming games` : "Loading…")}
              {section === "history" && (histData?.window.label ?? "Pick a week")}
              {section === "analysis" && (trends ? `All weeks with picks (${trends.weeks[0]}–${trends.weeks[trends.weeks.length - 1]})` : "Loading…")}
              {section === "manage" && "Runs, tracked accounts and definitions"}
            </div>
          </div>
        </div>
        <nav className="sections">
          {SECTIONS.map((s) => (
            <button key={s.id} className={section === s.id ? "on" : ""} onClick={() => setSection(s.id)}>
              {s.id === "live" && <i className="live-dot" />}{s.label}
            </button>
          ))}
        </nav>
        <div className="row gap">
          {section === "history" && (
            <select className="scope" value={histKey} onChange={(e) => setHistScope(parseScope(e.target.value))}>
              {scopeOptions.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
            </select>
          )}
          {section !== "manage" && (
            <button className="btn" onClick={refresh} disabled={!!activeLoading} title="Rebuild now: re-check game status and Kalshi prices">
              {activeLoading ? "Updating…" : "Refresh"}
            </button>
          )}
          <button className={`btn icon-btn ${section === "manage" ? "on" : ""}`} onClick={() => setSection("manage")} title="Runs, accounts and guide">⚙</button>
        </div>
      </header>

      {error && <div className="banner err">{error}</div>}

      <main className={activeLoading && builtAt ? "dim" : undefined}>
        {section === "live" && (live
          ? <LiveBoard data={live} nextPull={nextPull} preview={!!preview} />
          : <Loading what="this week's bets and Kalshi prices" busy={!!loading.live} />)}
        {section === "history" && (histData
          ? <WeekTabs data={histData} tabs={HISTORY_TABS} tab={histTab} setTab={setHistTab} live={histData.window.live}
                      exportsFor={histScope?.kind === "week" ? histScope.week : null} goManage={() => setSection("manage")} />
          : <Loading what="that week, its results and Kalshi prices" busy={!!loading[`h:${histKey}`]} />)}
        {section === "analysis" && (trends ? <Trends data={trends} /> : <Loading what="every week's results and Kalshi prices" busy={!!loading.trends} slow />)}
        {section === "manage" && (
          <div className="stack">
            {currentWeek != null && <Control currentWeek={currentWeek} weeks={weeks} onFinished={onRunFinished} />}
            {live && <section><h2 className="section-title">Guide</h2><Guide report={live} /></section>}
          </div>
        )}
      </main>

      {builtAt && <footer className="muted small">Updated {fmtAgo(builtAt)} · game data from ESPN · prices from Kalshi</footer>}
    </div>
  );
}

function Loading({ what, busy, slow }: { what: string; busy: boolean; slow?: boolean }) {
  return (
    <div className="empty big">
      {busy ? <>Loading {what}…{slow ? " The first load fetches every game and price and can take a minute or two; after that it's cached." : " The first load can take up to a minute."}</> : "Nothing loaded."}
    </div>
  );
}
