import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { PlacedBet, Quote, Row, WeekPayload } from "../types";

const QUOTE_POLL_MS = 5_000;
const ET = "America/New_York";
const kickFmt = new Intl.DateTimeFormat("en-US", { timeZone: ET, weekday: "short", hour: "numeric", minute: "2-digit" });

// --- prices ---------------------------------------------------------------------

interface Price { prob: number | null; cost: number | null; live: boolean }

function sideProb(q: Quote | undefined, side: string): number | null {
  if (!q) return null;
  const { yes_bid: b, yes_ask: a, last } = q;
  let p: number | null = null;
  if (b != null && a != null && a > 0 && a - b <= 0.25) p = (a + b) / 2;
  else if (last != null) p = last;
  if (p == null) return null;
  return side === "no" ? 1 - p : p;
}

function sideCost(q: Quote | undefined, side: string): number | null {
  if (!q) return null;
  if (side === "no") return q.yes_bid != null && q.yes_bid > 0 && q.yes_bid < 1 ? 1 - q.yes_bid : null;
  return q.yes_ask != null && q.yes_ask > 0 && q.yes_ask < 1 ? q.yes_ask : null;
}

/** Live price for a bet from streamed quotes; falls back to the price in the last page load. */
function priceOf(r: Row, quotes: Record<string, Quote>): Price {
  const side = r.kalshi_side || "yes";
  if (r.kalshi_ticker && quotes[r.kalshi_ticker]) {
    const q = quotes[r.kalshi_ticker];
    if (r.kalshi_ticker2 && r.kalshi_weight != null) {
      const p1 = sideProb(q, side), p2 = sideProb(quotes[r.kalshi_ticker2], side);
      const prob = p1 != null && p2 != null ? p1 * (1 - r.kalshi_weight) + p2 * r.kalshi_weight : null;
      return { prob, cost: null, live: true };
    }
    return { prob: sideProb(q, side), cost: sideCost(q, side), live: true };
  }
  return { prob: r.kalshi_now ?? r.kalshi_first ?? null, cost: r.kalshi_cost_now ?? null, live: false };
}

/** Kalshi price (cost of one contract) as American odds, the way sportsbooks show it. */
function american(cost: number | null): string {
  if (cost == null || cost <= 0 || cost >= 1) return "";
  return cost >= 0.5 ? `${-Math.round((100 * cost) / (1 - cost))}` : `+${Math.round((100 * (1 - cost)) / cost)}`;
}

const pct = (p: number | null | undefined) => (p == null ? "—" : `${Math.round(p * 100)}%`);
const cents = (c: number | null | undefined) => (c == null ? "" : `${Math.round(c * 100)}¢`);

// --- bits -------------------------------------------------------------------------

function countdown(iso: string | null, now: number): { text: string; live: boolean } {
  if (!iso) return { text: "", live: false };
  const ms = new Date(iso).getTime() - now;
  if (ms <= 0) return { text: "LIVE", live: true };
  const m = Math.round(ms / 60000);
  if (m < 60) return { text: `in ${m}m`, live: false };
  const h = Math.floor(m / 60);
  return { text: h < 24 ? `in ${h}h ${m % 60}m` : `in ${Math.round(h / 24)}d`, live: false };
}

function Avatars({ who }: { who: string }) {
  const list = (who || "").split(",").map((s) => s.trim()).filter(Boolean);
  return (
    <span className="avatars" title={list.map((h) => "@" + h).join("\n")}>
      {list.slice(0, 5).map((h) => <i key={h}>{h[0]?.toUpperCase()}</i>)}
      {list.length > 5 && <i className="more">+{list.length - 5}</i>}
    </span>
  );
}

function betName(r: Row) {
  return String(r.bet || "").replace(/^\[[A-Z]+\]\s*/, "");
}

// --- tile -------------------------------------------------------------------------

function Tile({ r, price, flash, placed, onToggle, started }: {
  r: Row; price: Price; flash: "up" | "down" | null; placed: PlacedBet | undefined;
  onToggle: () => void; started: boolean;
}) {
  const priced = price.prob != null;
  const move = priced && r.kalshi_first != null ? price.prob! - r.kalshi_first : null;
  const sinceYou = placed && placed.prob != null && priced ? price.prob! - placed.prob : null;
  const side = (r.kalshi_side || "yes") === "no" ? "No" : "Yes";
  const approx = r.kalshi_match === "interpolated";
  return (
    <article className={`tile ${placed ? "placed" : ""} ${started ? "started" : ""}`}>
      <header className="tile-top">
        <span className="tile-accounts"><Avatars who={r.who} /><b>{r.accounts}</b> {r.accounts === 1 ? "account" : "accounts"}</span>
        {move != null && Math.abs(move) >= 0.01 && (
          <span className={`tile-move ${move > 0 ? "up" : "down"}`} title="Kalshi price change since the first account posted it">
            {move > 0 ? "▲" : "▼"} {Math.abs(Math.round(move * 100))}
          </span>
        )}
      </header>
      <h3 className="tile-title">{betName(r)}</h3>
      <p className="tile-market" title={r.what_it_means}>
        {r.kalshi_title ? <>{approx && "≈ "}{r.kalshi_title}{r.kalshi_side === "no" && " · No"}</> : r.lines ? `Line ${r.lines}` : r.what_it_means}
      </p>

      <div className="tile-actions">
        {priced ? (
          <div className={`odds-btn ${flash ? `flash-${flash}` : ""}`} title={approx ? "Estimated between two Kalshi lines" : `Buy ${side} on Kalshi at ${cents(price.cost)}`}>
            <span className="odds-side">{side}{price.cost != null && <> {cents(price.cost)}</>}{!price.live && price.cost == null && " · at post"}</span>
            <span className="odds-main">{approx && "≈"}{pct(price.prob)}</span>
            <span className="odds-us">{american(price.cost)}</span>
          </div>
        ) : (
          <div className="odds-btn off" title={r.kalshi_note || "No matching Kalshi market"}>
            <span className="odds-side">Kalshi</span><span className="odds-main">—</span><span className="odds-us">not listed</span>
          </div>
        )}
        <button className={`placed-btn ${placed ? "on" : ""}`} onClick={onToggle} aria-pressed={!!placed}
                title={placed ? "Unmark this bet" : "Mark this bet as placed"}>
          {placed ? <>✓ Placed{placed.cost != null && <span> at {cents(placed.cost)}</span>}</> : "Placed"}
        </button>
      </div>
      {placed && sinceYou != null && Math.abs(sinceYou) >= 0.01 && (
        <p className={`tile-since ${sinceYou > 0 ? "up" : "down"}`}>
          {sinceYou > 0 ? "▲" : "▼"} {Math.abs(Math.round(sinceYou * 100))} pts since you placed it
        </p>
      )}
    </article>
  );
}

// --- board ------------------------------------------------------------------------

export function LiveBoard({ data, nextPull, preview }: { data: WeekPayload; nextPull: string | null; preview: boolean }) {
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [quotedAt, setQuotedAt] = useState<number | null>(null);
  const [quoteErr, setQuoteErr] = useState(false);
  const [placed, setPlaced] = useState<PlacedBet[]>([]);
  const [now, setNow] = useState(Date.now());
  const [league, setLeague] = useState<string | null>(null);
  const [scope, setScope] = useState<"shared" | "all" | "placed">("shared");
  const [flash, setFlash] = useState<Record<string, "up" | "down">>({});
  const prev = useRef<Record<string, number>>({});
  const win = data.window.name;

  const all = data.frames["All Bets"]?.rows ?? [];
  const open = useMemo(() => all.filter((r) => preview || r.game_status !== "Final"), [all, preview]);
  const placedBy = useMemo(() => new Map(placed.map((p) => [p.bet_key, p])), [placed]);
  const leagues = useMemo(() => [...new Set(open.map((r) => r.league).filter(Boolean))].sort() as string[], [open]);
  const shown = useMemo(() => open.filter((r) =>
    (!league || r.league === league) &&
    (scope === "all" || (scope === "shared" ? r.accounts >= 2 : placedBy.has(r.bet_key)))), [open, league, scope, placedBy]);

  // Group by game, soonest kickoff first; most-shared bets first within a game.
  const groups = useMemo(() => {
    const m = new Map<string, Row[]>();
    for (const r of shown) {
      const k = `${r.game_time_et ?? "9999"}|${r.league}|${r.game ?? "Other"}`;
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(r);
    }
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b))
      .map(([k, rows]) => ({ key: k, rows: rows.sort((a, b) => b.accounts - a.accounts) }));
  }, [shown]);

  const tickers = useMemo(() => [...new Set(shown.flatMap((r) => [r.kalshi_ticker, r.kalshi_ticker2]).filter(Boolean))] as string[], [shown]);

  // Prices: poll Kalshi (through the server) every 5 s while the page is visible.
  const poll = useCallback(async () => {
    if (!tickers.length || preview || document.hidden) return;
    try {
      const r = await api.quotes(tickers);
      setQuotes((q) => ({ ...q, ...r.quotes }));
      setQuotedAt(Date.now()); setQuoteErr(false);
    } catch { setQuoteErr(true); }
  }, [tickers, preview]);
  useEffect(() => { poll(); const t = setInterval(poll, QUOTE_POLL_MS); return () => clearInterval(t); }, [poll]);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 15_000); return () => clearInterval(t); }, []);
  useEffect(() => { api.placed(win).then((r) => setPlaced(r.placed)).catch(() => {}); }, [win]);

  // Flash a price green/red when it moves.
  useEffect(() => {
    const f: Record<string, "up" | "down"> = {};
    for (const r of shown) {
      const p = priceOf(r, quotes).prob;
      if (p == null) continue;
      const before = prev.current[r.bet_key];
      if (before != null && Math.abs(p - before) >= 0.005) f[r.bet_key] = p > before ? "up" : "down";
      prev.current[r.bet_key] = p;
    }
    if (Object.keys(f).length) {
      setFlash(f);
      const t = setTimeout(() => setFlash({}), 1200);
      return () => clearTimeout(t);
    }
  }, [quotes, shown]);

  const toggle = async (r: Row) => {
    const on = !placedBy.has(r.bet_key);
    const p = priceOf(r, quotes);
    // optimistic: highlight immediately, then sync with the server
    setPlaced((list) => on ? [...list, { window: win, bet_key: r.bet_key, label: r.bet, placed_at: new Date().toISOString(), prob: p.prob, cost: p.cost }]
                           : list.filter((x) => x.bet_key !== r.bet_key));
    try {
      const res = await api.setPlaced({ window: win, bet_key: r.bet_key, placed: on, label: r.bet, prob: p.prob, cost: p.cost });
      setPlaced(res.placed);
    } catch { api.placed(win).then((x) => setPlaced(x.placed)).catch(() => {}); }
  };

  const age = quotedAt ? Math.round((now - quotedAt) / 1000) : null;
  const failed = data.summary.failed_posts + data.summary.pending_posts;

  return (
    <div className="live">
      <div className="live-bar">
        <div className="seg">
          {(["shared", "all", "placed"] as const).map((s) => (
            <button key={s} className={scope === s ? "on" : ""} onClick={() => setScope(s)}>
              {s === "shared" ? "2+ accounts" : s === "all" ? "All picks" : `Placed${placed.length ? ` (${placed.length})` : ""}`}
            </button>
          ))}
        </div>
        {leagues.length > 1 && (
          <div className="seg">
            <button className={!league ? "on" : ""} onClick={() => setLeague(null)}>All</button>
            {leagues.map((l) => <button key={l} className={league === l ? "on" : ""} onClick={() => setLeague(l)}>{l}</button>)}
          </div>
        )}
        <span className={`live-status ${quoteErr ? "err" : ""}`}>
          {preview ? `Preview of ${win.replace("week", "week ")} (games finished; prices as posted)`
            : quoteErr ? "Kalshi prices paused, retrying…"
            : <><i className="live-dot" /> Prices live{age != null ? ` · ${age < 6 ? "just now" : `${age}s ago`}` : ""}</>}
        </span>
      </div>

      {failed > 0 && !preview && (
        <p className="live-note">{failed} of this week's posts couldn't be read yet, so some bets may be missing. Check ⚙ Manage.</p>
      )}

      {!groups.length ? (
        <div className="live-empty">
          <h2>{scope === "placed" ? "Nothing placed yet" : "No bets on upcoming games yet"}</h2>
          <p className="muted">
            {scope === "placed" ? "Press Placed on a tile to keep track of it here."
              : scope === "shared" && open.length ? `${open.length} single-account pick${open.length > 1 ? "s" : ""} are up. Switch to "All picks" to see them.`
              : nextPull ? `Next pull: ${kickFmt.format(new Date(nextPull))} ET.` : "Run a pull from ⚙ Manage, or install the schedule."}
          </p>
        </div>
      ) : <div className="games">{groups.map((g) => {
        const r0 = g.rows[0];
        const cd = countdown(r0.game_time_et, now);
        return (
          <section key={g.key} className="game">
            <header className="game-head">
              <span className="league">{r0.league}</span>
              <h2>{r0.game || "Game not matched"}</h2>
              {r0.game_time_et && <span className="muted">{kickFmt.format(new Date(r0.game_time_et))}</span>}
              {cd.text && <span className={`game-cd ${cd.live || r0.game_status === "Live" ? "live" : ""}`}>{r0.game_status === "Final" ? "FINAL" : cd.text}</span>}
            </header>
            <div className="tiles">
              {g.rows.map((r) => (
                <Tile key={r.bet_key} r={r} price={priceOf(r, quotes)} flash={flash[r.bet_key] ?? null}
                      placed={placedBy.get(r.bet_key)} onToggle={() => toggle(r)}
                      started={r.game_status !== "Upcoming" && r.game_status != null} />
              ))}
            </div>
          </section>
        );
      })}</div>}
    </div>
  );
}
