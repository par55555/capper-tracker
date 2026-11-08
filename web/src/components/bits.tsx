import { ReactNode } from "react";
import { signed, splitWho } from "../format";

export function StatusPill({ status }: { status?: string | null }) {
  if (!status) return null;
  return <span className={`pill status-${status.toLowerCase()}`}>{status === "Live" && <i className="dot" />}{status}</span>;
}

export function League({ league }: { league?: string | null }) {
  if (!league) return null;
  return <span className="league">{league}</span>;
}

export function Handles({ who, max = 8 }: { who?: string | null; max?: number }) {
  const list = splitWho(who);
  const shown = list.slice(0, max);
  return (
    <span className="handles">
      {shown.map((h) => (
        <a key={h} className="handle" href={`https://x.com/${h}`} target="_blank" rel="noreferrer">@{h}</a>
      ))}
      {list.length > max && <span className="muted" title={list.slice(max).join(", ")}>+{list.length - max}</span>}
    </span>
  );
}

/** A signed net value: green for overs, red for unders. */
export function Net({ n }: { n: number | null | undefined }) {
  if (n == null) return null;
  const cls = n > 0 ? "pos" : n < 0 ? "neg" : "zero";
  return <span className={`net ${cls}`}>{n === 0 ? "0" : signed(n)}</span>;
}

/** Over vs under split as a small bar. */
export function LeanBar({ over, under }: { over: number; under: number }) {
  const total = over + under || 1;
  return (
    <span className="leanbar" title={`${over} over/yes · ${under} under/no`}>
      <span className="leanbar-track">
        <span className="leanbar-over" style={{ width: `${(over / total) * 100}%` }} />
        <span className="leanbar-under" style={{ width: `${(under / total) * 100}%` }} />
      </span>
      <span className="leanbar-label">{over}–{under}</span>
    </span>
  );
}

export function Stat({ label, value, tone, hint }: { label: string; value: ReactNode; tone?: "warn" | "good"; hint?: string }) {
  return (
    <div className={`stat ${tone ?? ""}`} title={hint}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

export function TweetLink({ url }: { url?: string | null }) {
  if (!url) return null;
  return <a className="ext" href={url} target="_blank" rel="noreferrer" title="Open post on X">↗</a>;
}

export function Chips<T extends string>({ options, value, onChange, all = "All" }:
  { options: T[]; value: T | null; onChange: (v: T | null) => void; all?: string }) {
  if (options.length < 2) return null;
  return (
    <div className="chips">
      <button className={`chip ${value === null ? "on" : ""}`} onClick={() => onChange(null)}>{all}</button>
      {options.map((o) => (
        <button key={o} className={`chip ${value === o ? "on" : ""}`} onClick={() => onChange(value === o ? null : o)}>{o}</button>
      ))}
    </div>
  );
}

/** Kalshi's probability for a bet. Live: price now, with movement since the first post.
 *  Settled: what the market gave it when posted. "≈" = estimated between two Kalshi lines. */
export function KalshiPrice({ row, live }: { row: Record<string, any>; live: boolean }) {
  const now = row.kalshi_now, first = row.kalshi_first, close = row.kalshi_close;
  if (now == null && first == null && close == null) {
    return <span className="kalshi none" title="No matching Kalshi market">Kalshi —</span>;
  }
  const approx = row.kalshi_match === "interpolated" ? "≈" : "";
  const main = live && now != null ? now : first ?? close;
  const ref = live && now != null ? first : null;
  const move = main != null && ref != null ? main - ref : null;
  const tip = [row.kalshi_title, approx && "Estimated between two Kalshi lines",
               first != null && `At first post: ${pct(first)}`, close != null && `Before kickoff: ${pct(close)}`,
               now != null && `Now: ${pct(now)}`].filter(Boolean).join("\n");
  return (
    <span className="kalshi" title={tip}>
      <span className="kalshi-label">Kalshi</span>
      <strong>{approx}{pct(main)}</strong>
      {move != null && Math.abs(move) >= 0.01 && (
        <span className={move > 0 ? "pos" : "neg"}>{move > 0 ? "▲" : "▼"}{Math.abs(Math.round(move * 100))}</span>
      )}
      {!live && <span className="muted small">at post</span>}
    </span>
  );
}

const pct = (p: number | null | undefined) => (p == null ? "" : `${Math.round(p * 100)}%`);
