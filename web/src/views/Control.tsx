import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { fmtAgo, fmtPostTime } from "../format";
import type { Account, Run, WeekInfo } from "../types";

const COMMANDS = [
  { id: "run", label: "Full run", desc: "Fetch new posts from X, extract picks with Claude, rebuild the report.", paid: true },
  { id: "fetch", label: "Fetch only", desc: "Pull new posts from X into the database.", paid: true },
  { id: "extract", label: "Extract only", desc: "Send stored, unprocessed (or failed) posts to Claude.", paid: true },
  { id: "report", label: "Report only", desc: "Rebuild the Excel report from the database. Free.", paid: false },
] as const;

function Accounts() {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [draft, setDraft] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = () => api.accounts().then((r) => { setAccounts(r.accounts); setDraft(r.accounts.map((a) => a.handle)); }).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  const byHandle = new Map((accounts ?? []).map((a) => [a.handle.toLowerCase(), a]));
  const dirty = accounts != null && draft.join("\n") !== accounts.map((a) => a.handle).join("\n");

  const add = () => {
    const handles = input.split(/[\s,]+/).map((h) => h.replace(/^@/, "").trim()).filter(Boolean);
    const bad = handles.filter((h) => !/^[A-Za-z0-9_]{1,15}$/.test(h));
    if (bad.length) { setErr(`Not a valid X handle: ${bad.join(", ")}`); return; }
    const have = new Set(draft.map((h) => h.toLowerCase()));
    setDraft([...draft, ...handles.filter((h) => !have.has(h.toLowerCase()))]);
    setInput(""); setErr(null);
  };

  const save = async () => {
    setSaving(true); setErr(null);
    try { const r = await api.saveAccounts(draft); setAccounts(r.accounts); setDraft(r.accounts.map((a) => a.handle)); }
    catch (e: any) { setErr(e.message); }
    finally { setSaving(false); }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Tracked accounts <span className="muted">{draft.length}</span></h2>
        {dirty && <div className="row gap">
          <button className="btn ghost" onClick={() => accounts && setDraft(accounts.map((a) => a.handle))}>Discard</button>
          <button className="btn primary" onClick={save} disabled={saving}>{saving ? "Saving…" : "Save accounts.txt"}</button>
        </div>}
      </div>
      <p className="section-sub">Saved to <code>accounts.txt</code>. New handles are looked up on X (about $0.01 each) on the next fetch. Removed accounts stop being fetched; their history stays.</p>
      <form className="row gap" onSubmit={(e) => { e.preventDefault(); add(); }}>
        <input className="search grow" placeholder="Add handles, e.g. @sharpboyjon, linesoverminds" value={input} onChange={(e) => setInput(e.target.value)} />
        <button className="btn" type="submit" disabled={!input.trim()}>Add</button>
      </form>
      {err && <div className="banner err">{err}</div>}
      {accounts == null ? <div className="empty">Loading…</div> : (
        <ul className="account-list">
          {draft.map((h) => {
            const a = byHandle.get(h.toLowerCase());
            return (
              <li key={h} className={a ? "" : "new"}>
                <div className="acct-main">
                  <a className="handle" href={`https://x.com/${h}`} target="_blank" rel="noreferrer">@{h}</a>
                  {a?.display_name && <span className="muted small">{a.display_name}</span>}
                </div>
                <div className="acct-stats muted small">
                  {!a ? <span className="tag">unsaved</span> : !a.resolved ? <span className="tag warn">not looked up yet</span> :
                    <>{a.posts} posts · {a.picks} picks · last post {fmtAgo(a.last_post)}</>}
                </div>
                <button className="btn icon" title="Remove" onClick={() => setDraft(draft.filter((x) => x !== h))}>✕</button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function Runs({ currentWeek, weeks, onFinished }: { currentWeek: number; weeks: WeekInfo[]; onFinished: () => void }) {
  const [command, setCommand] = useState<(typeof COMMANDS)[number]["id"]>("run");
  const [scopeKind, setScopeKind] = useState<"current" | "week" | "days">("current");
  const [week, setWeek] = useState(currentWeek);
  const [days, setDays] = useState(7);
  const [redoAll, setRedoAll] = useState(false);
  const [active, setActive] = useState<Run | null>(null);
  const [history, setHistory] = useState<Run[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement>(null);
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;

  // Pick up a run that was already going (e.g. after a page reload).
  useEffect(() => {
    api.runs().then((r) => {
      setHistory(r.runs);
      if (r.runs[0]?.running) api.run(r.runs[0].id).then(setActive);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!active?.running) return;
    const t = setInterval(async () => {
      try {
        const r = await api.run(active.id);
        setActive(r);
        if (!r.running) {
          api.runs().then((h) => setHistory(h.runs));
          finishedRef.current();
        }
      } catch { /* server restarting; keep polling */ }
    }, 1500);
    return () => clearInterval(t);
  }, [active?.id, active?.running]);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [active?.output?.length]);

  const cmd = COMMANDS.find((c) => c.id === command)!;
  const start = async () => {
    if (cmd.paid && !confirm(`${cmd.label} calls paid APIs (X and/or Anthropic). Start it?`)) return;
    setErr(null);
    try {
      const body: any = { command, redo_all: command === "extract" && redoAll };
      if (scopeKind === "week") body.week = week;
      if (scopeKind === "days") body.days = days;
      const r = await api.startRun(body);
      setActive(r);
      api.runs().then((h) => setHistory(h.runs));
    } catch (e: any) { setErr(e.message); }
  };

  const view = async (id: string) => { try { setActive(await api.run(id)); } catch (e: any) { setErr(e.message); } };

  return (
    <section className="panel">
      <div className="panel-head"><h2>Run the pipeline</h2></div>
      <div className="run-form">
        <div className="segmented">
          {COMMANDS.map((c) => (
            <button key={c.id} className={command === c.id ? "on" : ""} onClick={() => setCommand(c.id)}>{c.label}</button>
          ))}
        </div>
        <p className="section-sub">{cmd.desc}{cmd.paid && <> <span className="tag warn">paid</span></>}</p>
        <div className="row gap wrap-row">
          <label className="check">Window
            <select value={scopeKind} onChange={(e) => setScopeKind(e.target.value as any)}>
              <option value="current">This NFL week so far (wk {currentWeek})</option>
              <option value="week">A specific NFL week</option>
              <option value="days">Last N days</option>
            </select>
          </label>
          {scopeKind === "week" && (
            <select value={week} onChange={(e) => setWeek(Number(e.target.value))}>
              {Array.from({ length: Math.max(currentWeek, ...weeks.map((w) => w.week), 1) }, (_, i) => i + 1).reverse()
                .map((w) => <option key={w} value={w}>Week {w}</option>)}
            </select>
          )}
          {scopeKind === "days" && (
            <input type="number" min={1} max={60} value={days} onChange={(e) => setDays(Number(e.target.value))} className="num" />
          )}
          {command === "extract" && (
            <label className="check"><input type="checkbox" checked={redoAll} onChange={(e) => setRedoAll(e.target.checked)} /> Re-extract everything (not just new/failed)</label>
          )}
          <button className="btn primary" onClick={start} disabled={!!active?.running}>
            {active?.running ? "Running…" : `Start ${cmd.label.toLowerCase()}`}
          </button>
          {active?.running && <button className="btn ghost" onClick={() => api.cancelRun(active.id).catch(() => {})}>Cancel</button>}
        </div>
        {err && <div className="banner err">{err}</div>}
      </div>

      {active && (
        <div className="run-log">
          <div className="run-log-head">
            <code>{active.command}</code>
            <span className={`tag ${active.running ? "" : active.exit_code === 0 ? "good" : "err"}`}>
              {active.running ? "running" : active.exit_code === 0 ? "finished" : `exit ${active.exit_code}`}
            </span>
          </div>
          <pre ref={logRef}>{(active.output ?? []).join("\n") || "Starting…"}</pre>
        </div>
      )}

      {history.length > 0 && (
        <div className="run-history">
          <h3 className="small muted">Runs started from this page (since the server started)</h3>
          <ul>
            {history.map((r) => (
              <li key={r.id}>
                <button className="linkish" onClick={() => view(r.id)}>{fmtPostTime(r.started)}</button>
                <code className="small">{r.command.replace("python -m capper_tracker ", "")}</code>
                <span className={`tag ${r.running ? "" : r.exit_code === 0 ? "good" : "err"}`}>
                  {r.running ? "running" : r.exit_code === 0 ? "ok" : `exit ${r.exit_code}`}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

export function Control(props: { currentWeek: number; weeks: WeekInfo[]; onFinished: () => void }) {
  return (
    <div className="control-grid">
      <Runs {...props} />
      <Accounts />
    </div>
  );
}
