import { useEffect, useMemo, useRef, useState } from "react";
import { DataTable, type Column } from "../components/DataTable";
import { Chips, League, Stat } from "../components/bits";
import type { Row, TrendsReport } from "../types";
import { AgreementChart } from "./Results";

const pct = (r: number | null | undefined) => (r == null ? "—" : `${Math.round(r * 100)}%`);
const wl = (r: Row) => `${r.won}–${r.lost}${r.push ? `–${r.push}` : ""}`;
const units = (u: number | null | undefined) => (u == null ? "" : `${u > 0 ? "+" : ""}${u.toFixed(1)}u`);

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(600);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

/** Hit-rate bar on a 0–100% track with a 95% interval whisker and a 50% reference. */
function RateBar({ rate, lo, hi }: { rate: number | null; lo: number | null; hi: number | null }) {
  return (
    <span className="ci-track">
      <span className="hbar-ref" style={{ left: "50%" }} />
      {rate != null && <span className="ci-fill" style={{ width: `${rate * 100}%` }} />}
      {lo != null && hi != null && (
        <span className="ci-whisker" style={{ left: `${lo * 100}%`, width: `${(hi - lo) * 100}%` }} />
      )}
    </span>
  );
}

// --- who hits ---------------------------------------------------------------

function WhoHits({ rows }: { rows: Row[] }) {
  const [minGraded, setMinGraded] = useState(20);
  const [order, setOrder] = useState<"reliable" | "rate">("reliable");
  const [hover, setHover] = useState<string | null>(null);
  const shown = useMemo(() => rows.filter((r) => r.graded >= minGraded)
    .sort((a, b) => order === "rate" ? (b.hit_rate ?? 0) - (a.hit_rate ?? 0) : (b.ci_low ?? 0) - (a.ci_low ?? 0)), [rows, minGraded, order]);
  const hidden = rows.length - shown.length;
  return (
    <figure className="chart">
      <figcaption className="chart-head">
        <div>
          <h3>Who hits</h3>
          <p className="section-sub">Hit rate on graded picks, all weeks. The thin line is the 95% range: the shorter it is, the more the number can be trusted.</p>
        </div>
        <div className="row gap wrap-row">
          <div className="segmented small-seg">
            <button className={order === "reliable" ? "on" : ""} onClick={() => setOrder("reliable")} title="Rank by the low end of the 95% range, so small samples don't top the list">Most reliable</button>
            <button className={order === "rate" ? "on" : ""} onClick={() => setOrder("rate")}>Raw hit rate</button>
          </div>
          <label className="check">Min graded
            <select value={minGraded} onChange={(e) => setMinGraded(Number(e.target.value))}>
              {[5, 10, 20, 40].map((n) => <option key={n} value={n}>{n}+</option>)}
            </select>
          </label>
        </div>
      </figcaption>
      <div className="hbars wide">
        {shown.map((r) => (
          <div key={r.handle} className={`hbar-row ci-row ${hover === r.handle ? "hover" : ""}`}
               onMouseEnter={() => setHover(r.handle)} onMouseLeave={() => setHover(null)}>
            <a className="hbar-label handle-plain" href={`https://x.com/${r.handle}`} target="_blank" rel="noreferrer">@{r.handle}</a>
            <span className="hbar-track">
              <RateBar rate={r.hit_rate} lo={r.ci_low} hi={r.ci_high} />
              {hover === r.handle && (
                <span className="tooltip" role="tooltip">
                  <strong>@{r.handle} · {pct(r.hit_rate)} ({wl(r)})</strong>
                  <span>95% range {pct(r.ci_low)}–{pct(r.ci_high)} · {r.graded} graded</span>
                  <span>{r.winning_weeks} of {r.weeks_active} weeks at 50%+</span>
                  {r.shared_won + r.shared_lost > 0 && <span>On shared bets: {r.shared_won}–{r.shared_lost} ({pct(r.shared_hit_rate)})</span>}
                  {r.best_market && <span>Best: {r.best_market}</span>}
                  {r.worst_market && <span>Worst: {r.worst_market}</span>}
                </span>
              )}
            </span>
            <span className="hbar-value"><strong>{pct(r.hit_rate)}</strong><span className="muted small">{wl(r)}</span></span>
          </div>
        ))}
        <div className="hbar-axis"><span /><span className="hbar-axis-track"><span style={{ left: 0 }}>0%</span><span style={{ left: "50%" }}>50%</span><span style={{ left: "100%" }}>100%</span></span><span /></div>
      </div>
      {hidden > 0 && <p className="section-sub">{hidden} account{hidden > 1 ? "s" : ""} with fewer than {minGraded} graded picks hidden.</p>}
    </figure>
  );
}

// --- week by week -----------------------------------------------------------

const SERIES = [
  { key: "shared", label: "Shared bets (2+ accounts)", cls: "s1" },
  { key: "all", label: "All picks", cls: "s2" },
] as const;

function WeeklyChart({ rows }: { rows: Row[] }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const H = 220, M = { t: 16, r: 90, b: 28, l: 40 };
  const w = Math.max(width, 320), iw = w - M.l - M.r, ih = H - M.t - M.b;
  const x = (i: number) => M.l + (rows.length === 1 ? iw / 2 : (i / (rows.length - 1)) * iw);
  const y = (v: number) => M.t + (1 - v) * ih;
  const path = (k: string) => rows.map((r, i) => r[`${k}_hit_rate`] == null ? null : `${x(i)},${y(r[`${k}_hit_rate`])}`)
    .filter(Boolean).map((p, i) => `${i ? "L" : "M"}${p}`).join("");
  const last = rows.length - 1;
  if (!rows.length) return null;
  return (
    <figure className="chart">
      <figcaption className="chart-head">
        <div>
          <h3>Week by week</h3>
          <p className="section-sub">Hit rate of graded picks each NFL week.</p>
        </div>
        <div className="legend">
          {SERIES.map((s) => <span key={s.key}><i className={`swatch ${s.cls}`} />{s.label}</span>)}
        </div>
      </figcaption>
      <div ref={ref} className="line-wrap" onMouseLeave={() => setHover(null)}>
        <svg width={w} height={H} role="img" aria-label="Hit rate by week">
          {[0, 0.25, 0.5, 0.75, 1].map((v) => (
            <g key={v}>
              <line x1={M.l} x2={M.l + iw} y1={y(v)} y2={y(v)} className={v === 0.5 ? "grid ref" : "grid"} />
              <text x={M.l - 8} y={y(v)} className="axis" textAnchor="end" dominantBaseline="middle">{v * 100}%</text>
            </g>
          ))}
          {rows.map((r, i) => <text key={r.week} x={x(i)} y={H - 8} className="axis" textAnchor="middle">Wk {r.week}</text>)}
          {hover != null && <line x1={x(hover)} x2={x(hover)} y1={M.t} y2={M.t + ih} className="crosshair" />}
          {SERIES.map((s) => (
            <g key={s.key} className={s.cls}>
              <path d={path(s.key)} className="line" />
              {rows.map((r, i) => r[`${s.key}_hit_rate`] != null && (
                <circle key={i} cx={x(i)} cy={y(r[`${s.key}_hit_rate`])} r={hover === i ? 5 : 4} className="dot" />
              ))}
            </g>
          ))}
          {SERIES.map((s, si) => {
            const v = rows[last][`${s.key}_hit_rate`];
            if (v == null) return null;
            const other = rows[last][`${SERIES[1 - si].key}_hit_rate`];
            const nudge = other != null && Math.abs(y(v) - y(other)) < 14 ? (v >= other ? -8 : 8) : 0;
            return <text key={s.key} x={x(last) + 10} y={y(v) + nudge} className="end-label" dominantBaseline="middle">{pct(v)} {s.key === "shared" ? "shared" : "all"}</text>;
          })}
          {rows.map((_, i) => (
            <rect key={i} x={x(i) - (iw / Math.max(rows.length - 1, 1)) / 2} y={M.t} width={iw / Math.max(rows.length - 1, 1)} height={ih}
                  fill="transparent" onMouseEnter={() => setHover(i)} />
          ))}
        </svg>
        {hover != null && (
          <div className="tooltip line-tip" style={{ left: Math.min(x(hover) + 12, w - 230), top: 8 }}>
            <strong>Week {rows[hover].week}</strong>
            {SERIES.map((s) => (
              <span key={s.key}><i className={`swatch ${s.cls}`} /> {s.label}: {pct(rows[hover][`${s.key}_hit_rate`])} ({rows[hover][`${s.key}_won`]}–{rows[hover][`${s.key}_lost`]})</span>
            ))}
            <span className="muted">{rows[hover].ungraded} picks not graded</span>
          </div>
        )}
      </div>
    </figure>
  );
}

// --- account x week heatmap -------------------------------------------------

function Heatmap({ cappers, cells, weeks }: { cappers: Row[]; cells: Row[]; weeks: number[] }) {
  const [minGraded, setMinGraded] = useState(10);
  const byKey = useMemo(() => new Map(cells.map((c) => [`${c.handle}|${c.week}`, c])), [cells]);
  const rows = cappers.filter((r) => r.graded >= minGraded);
  return (
    <figure className="chart">
      <figcaption className="chart-head">
        <div>
          <h3>Accounts by week</h3>
          <p className="section-sub">Darker = higher hit rate that week. Use it to spot hot and cold streaks, and who's consistent.</p>
        </div>
        <label className="check">Min graded (all weeks)
          <select value={minGraded} onChange={(e) => setMinGraded(Number(e.target.value))}>
            {[1, 10, 20, 40].map((n) => <option key={n} value={n}>{n}+</option>)}
          </select>
        </label>
      </figcaption>
      <div className="heat-scroll">
        <table className="heat">
          <thead><tr><th>Account</th>{weeks.map((w) => <th key={w}>Wk {w}</th>)}<th>All</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.handle}>
                <td className="heat-name">@{r.handle}</td>
                {weeks.map((w) => {
                  const c = byKey.get(`${r.handle}|${w}`);
                  if (!c || !c.graded) return <td key={w} className="heat-cell empty-cell">—</td>;
                  const v = c.hit_rate as number;
                  return (
                    <td key={w} className={`heat-cell ${v >= 0.55 ? "ink-light" : ""}`}
                        style={{ background: `color-mix(in srgb, var(--accent) ${Math.round(12 + v * 80)}%, var(--surface))` }}
                        title={`@${r.handle}, week ${w}: ${wl(c)} (${pct(v)})`}>
                      <strong>{pct(v)}</strong><span>{c.won}–{c.lost}</span>
                    </td>
                  );
                })}
                <td className="heat-cell total"><strong>{pct(r.hit_rate)}</strong><span>{wl(r)}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

// --- markets ----------------------------------------------------------------

function Markets({ rows, styles }: { rows: Row[]; styles: Row[] }) {
  const [league, setLeague] = useState<string | null>("NFL");
  const [minGraded, setMinGraded] = useState(5);
  const leagues = useMemo(() => [...new Set(rows.map((r) => r.league))].sort() as string[], [rows]);
  const filtered = useMemo(() => rows.filter((r) => (!league || r.league === league) && r.graded >= minGraded), [rows, league, minGraded]);
  const cols: Column[] = [
    { key: "market", label: "Market", render: (r) => <><strong>{r.market}</strong> {!league && <League league={r.league} />}</> },
    { key: "hit_rate", label: "Hit rate (95% range)", render: (r) => (
        <span className="mini-bar" title={`95% range ${pct(r.ci_low)}–${pct(r.ci_high)}`}>
          <RateBar rate={r.hit_rate} lo={r.ci_low} hi={r.ci_high} /><strong>{pct(r.hit_rate)}</strong>
        </span>) },
    { key: "won", label: "Record", render: wl, sortValue: (r) => r.graded },
    { key: "accounts", label: "Accounts", align: "right" },
    { key: "breakeven", label: "Break-even", align: "right", render: (r) => r.priced ? pct(r.breakeven) : "",
      title: "Average hit rate the posted odds need to break even (straight bets with odds only)" },
    { key: "units", label: "Flat 1u P/L", align: "right", render: (r) => r.priced ? <span className={r.units > 0 ? "pos" : r.units < 0 ? "neg" : ""}>{units(r.units)} <span className="muted small">({r.priced})</span></span> : "",
      title: "Profit staking 1 unit on each straight bet with posted odds. (n) = how many bets had odds." },
  ];
  return (
    <section>
      <h2 className="section-title">Which markets hit</h2>
      <p className="section-sub">Hit rate by bet market across all accounts and weeks. Break-even and profit use straight bets with posted odds only; parlay prices aren't a leg's price.</p>
      <DataTable rows={filtered} columns={cols} initialSort={{ key: "won", dir: "desc" }} searchKeys={["market", "league"]}
        toolbar={<>
          <Chips options={leagues} value={league} onChange={setLeague} />
          <label className="check">Min graded
            <select value={minGraded} onChange={(e) => setMinGraded(Number(e.target.value))}>
              {[1, 5, 10, 25].map((n) => <option key={n} value={n}>{n}+</option>)}
            </select>
          </label>
        </>} />
      {styles.length > 0 && (
        <div className="style-row">
          {styles.map((s) => (
            <div key={s.style} className="style-card">
              <div className="muted small">{s.style}</div>
              <div><strong>{pct(s.hit_rate)}</strong> <span className="muted">({wl(s)})</span></div>
              {s.priced > 0 && <div className="small muted">needs ~{pct(s.breakeven)} at posted odds · <span className={s.units > 0 ? "pos" : "neg"}>{units(s.units)}</span> on {s.priced}</div>}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// --- page -------------------------------------------------------------------

export function Trends({ data }: { data: TrendsReport }) {
  const f = (n: string) => data.frames[n]?.rows ?? [];
  const weekly = f("Weekly"), cappers = f("Cappers"), markets = f("Markets");
  if (!weekly.length) return <div className="empty big">No graded picks yet. Trends appear once games with extracted picks are final.</div>;

  const sum = (k: string) => weekly.reduce((a, r) => a + (r[k] ?? 0), 0);
  const all = { won: sum("all_won"), lost: sum("all_lost") }, shared = { won: sum("shared_won"), lost: sum("shared_lost") };
  const rate = (r: { won: number; lost: number }) => (r.won + r.lost ? r.won / (r.won + r.lost) : null);
  const topCapper = [...cappers].filter((r) => r.graded >= 20).sort((a, b) => (b.ci_low ?? 0) - (a.ci_low ?? 0))[0];
  const topMarket = [...markets].filter((r) => r.graded >= 20).sort((a, b) => (b.hit_rate ?? 0) - (a.hit_rate ?? 0))[0];
  const weeks = data.weeks;

  return (
    <div className="stack">
      <div>
        <p className="section-sub note">
          Weeks {weeks[0]}–{weeks[weeks.length - 1]}, every graded pick. Not affected by the week picker.
          Hit rate isn't profit: long-odds picks can hit rarely and still win money.
        </p>
        <div className="stats">
          <Stat label={`Shared bets · ${shared.won}–${shared.lost}`} value={pct(rate(shared))} hint="Bets 2+ tracked accounts posted, each bet counted once per week" />
          <Stat label={`All picks · ${all.won}–${all.lost}`} value={pct(rate(all))} />
          {topCapper && <Stat label={`Most reliable account · ${wl(topCapper)}`} value={<span className="stat-handle">@{topCapper.handle}</span>}
                              hint={`${pct(topCapper.hit_rate)} hit rate; 95% range ${pct(topCapper.ci_low)}–${pct(topCapper.ci_high)} (min 20 graded)`} />}
          {topMarket && <Stat label={`Best market · ${pct(topMarket.hit_rate)} (${wl(topMarket)})`} value={<span className="stat-handle">{topMarket.market}</span>}
                              hint="Highest hit rate among markets with 20+ graded picks" />}
        </div>
      </div>

      <WhoHits rows={cappers} />
      <div className="two-col">
        <WeeklyChart rows={weekly} />
        <AgreementChart rows={f("Agreement")} />
      </div>
      <Heatmap cappers={cappers} cells={f("Capper Weeks")} weeks={weeks} />
      <Markets rows={markets} styles={f("Styles")} />
    </div>
  );
}
