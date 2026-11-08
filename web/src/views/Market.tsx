import { useMemo, useState } from "react";
import { DataTable, type Column } from "../components/DataTable";
import { Stat } from "../components/bits";
import { fmtProb, fmtPts } from "../format";
import type { Row, TrendsReport } from "../types";

const roi = (r: number | null | undefined) => (r == null ? "—" : `${r > 0 ? "+" : ""}${Math.round(r * 100)}%`);
const tone = (v: number | null | undefined) => (v == null ? "" : v > 0.005 ? "pos" : v < -0.005 ? "neg" : "");

/** Expected (Kalshi price, hollow ring) vs actual hit rate (filled dot) per row, on one 0–100% axis. */
function ExpectedVsActual({ rows, labelKey, title, sub }: { rows: Row[]; labelKey: string; title: string; sub: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const shown = rows.filter((r) => r.mkt_n > 0);
  if (!shown.length) return null;
  return (
    <figure className="chart">
      <figcaption className="chart-head">
        <div><h3>{title}</h3><p className="section-sub">{sub}</p></div>
        <div className="legend">
          <span><i className="dot-key ring" />Kalshi price at post (expected)</span>
          <span><i className="dot-key fill" />Actual hit rate</span>
        </div>
      </figcaption>
      <div className="dots">
        {shown.map((r, i) => {
          const e = r.mkt_expected * 100, a = r.mkt_actual * 100;
          return (
            <div key={i} className={`dot-row ${hover === i ? "hover" : ""}`} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <span className="hbar-label">{r[labelKey]}</span>
              <span className="dot-track">
                <span className={`dot-gap ${tone(r.mkt_edge)}`} style={{ left: `${Math.min(e, a)}%`, width: `${Math.abs(a - e)}%` }} />
                <span className="dot-mark ring" style={{ left: `${e}%` }} />
                <span className="dot-mark fill" style={{ left: `${a}%` }} />
                {hover === i && (
                  <span className="tooltip" role="tooltip">
                    <strong>{r[labelKey]}</strong>
                    <span>Hit {fmtProb(r.mkt_actual)} vs priced {fmtProb(r.mkt_expected)} ({fmtPts(r.mkt_edge)})</span>
                    <span>{r.mkt_n} priced picks · {r.won}–{r.lost} overall</span>
                    {r.mkt_roi_n > 0 && <span>Profit at Kalshi prices: {roi(r.mkt_roi)} on {r.mkt_roi_n} picks</span>}
                  </span>
                )}
              </span>
              <span className="hbar-value">
                <strong className={tone(r.mkt_edge)}>{fmtPts(r.mkt_edge)}</strong>
                <span className="muted small">{r.mkt_n} picks</span>
              </span>
            </div>
          );
        })}
        <div className="hbar-axis"><span /><span className="hbar-axis-track"><span style={{ left: 0 }}>0%</span><span style={{ left: "50%" }}>50%</span><span style={{ left: "100%" }}>100%</span></span><span /></div>
      </div>
    </figure>
  );
}

function CappersVsMarket({ rows }: { rows: Row[] }) {
  const [minN, setMinN] = useState(15);
  const shown = useMemo(() => rows.filter((r) => r.mkt_n >= minN), [rows, minN]);
  const cols: Column[] = [
    { key: "handle", label: "Account", render: (r) => <a className="handle" href={`https://x.com/${r.handle}`} target="_blank" rel="noreferrer">@{r.handle}</a> },
    { key: "mkt_edge", label: "Vs market", align: "right", render: (r) => <strong className={tone(r.mkt_edge)}>{fmtPts(r.mkt_edge)}</strong>,
      title: "Actual hit rate minus the average Kalshi price when they posted. Positive = they find bets the market underrates." },
    { key: "mkt_actual", label: "Hit", align: "right", render: (r) => fmtProb(r.mkt_actual) },
    { key: "mkt_expected", label: "Priced at", align: "right", render: (r) => fmtProb(r.mkt_expected) },
    { key: "mkt_roi", label: "Profit at Kalshi prices", align: "right",
      render: (r) => r.mkt_roi_n ? <span className={tone(r.mkt_roi)}>{roi(r.mkt_roi)} <span className="muted small">({r.mkt_roi_n})</span></span> : "—",
      title: "Return per $1 if you'd bought each pick on Kalshi at the moment it was posted (fees ignored)." },
    { key: "mkt_move", label: "Price moved", align: "right", render: (r) => <span className={tone(r.mkt_move)}>{fmtPts(r.mkt_move)}</span>,
      title: "Average change in Kalshi's price from the post to kickoff. Positive = the market moved toward their pick after they posted it (a sign of real skill)." },
    { key: "mkt_move_up", label: "Moved their way", align: "right", render: (r) => fmtProb(r.mkt_move_up) },
    { key: "mkt_n", label: "Priced picks", align: "right" },
  ];
  return (
    <section>
      <h2 className="section-title">Accounts vs. the market</h2>
      <p className="section-sub">Hit rate alone flatters favorites and punishes long shots. These compare each account against Kalshi's price at the moment they posted.</p>
      <DataTable rows={shown} columns={cols} initialSort={{ key: "mkt_edge", dir: "desc" }} searchKeys={["handle"]}
        toolbar={<label className="check">Min priced picks
          <select value={minN} onChange={(e) => setMinN(Number(e.target.value))}>{[5, 15, 30, 60].map((n) => <option key={n} value={n}>{n}+</option>)}</select>
        </label>} />
    </section>
  );
}

export function MarketSection({ data }: { data: TrendsReport }) {
  const f = (n: string) => data.frames[n]?.rows ?? [];
  const o = f("Market Overall")[0];
  if (!o || !o.mkt_n) return null;
  return (
    <div className="stack">
      <div>
        <h2 className="section-title">Versus the market (Kalshi)</h2>
        <p className="section-sub">Every pick priced at Kalshi's odds when it was posted. Beating these numbers, not a 50% hit rate, is what makes a pick worth following.</p>
        <div className="stats">
          <Stat label={`Picks hit · priced at ${fmtProb(o.mkt_expected)}`} value={fmtProb(o.mkt_actual)} hint={`${o.mkt_n} picks with a Kalshi price`} />
          <Stat label="Vs market" value={<span className={tone(o.mkt_edge)}>{fmtPts(o.mkt_edge)}</span>} hint="Actual hit rate minus the average price" />
          <Stat label={`Profit at Kalshi prices · ${o.mkt_roi_n} picks`} value={<span className={tone(o.mkt_roi)}>{roi(o.mkt_roi)}</span>}
                hint="Buying every exactly-matched pick at the posted-time Kalshi price, $1 per contract, fees ignored" />
          <Stat label="Price moved their way" value={fmtProb(o.mkt_move_up)} hint={`Average move from post to kickoff: ${fmtPts(o.mkt_move)}`} />
        </div>
      </div>
      <div className="two-col">
        <ExpectedVsActual rows={f("Agreement vs Market")} labelKey="tier" title="Does agreement beat the price?"
          sub="Picks grouped by how many accounts were on the same bet." />
        <ExpectedVsActual rows={f("Calibration")} labelKey="bucket" title="Favorites vs long shots"
          sub="Picks grouped by Kalshi's price when posted. Dots right of the rings = underpriced." />
      </div>
      <CappersVsMarket rows={f("Cappers")} />
    </div>
  );
}
