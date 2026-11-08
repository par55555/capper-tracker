const ET = "America/New_York";

const gameFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: ET, weekday: "short", month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit",
});
const postFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: ET, weekday: "short", month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit",
});

export function fmtGameTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(+d) ? "" : gameFmt.format(d) + " ET";
}

export function fmtPostTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(+d) ? "" : postFmt.format(d);
}

export function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export function fmtOdds(n: number | null | undefined): string {
  if (n == null || isNaN(n)) return "";
  const r = Math.round(n);
  return r > 0 ? `+${r}` : `${r}`;
}

export function fmtNum(n: number | null | undefined): string {
  if (n == null || isNaN(n)) return "";
  return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, "");
}

export const splitWho = (who: string | null | undefined): string[] =>
  who ? who.split(",").map((s) => s.trim()).filter(Boolean) : [];

export const signed = (n: number) => (n > 0 ? `+${n}` : `${n}`);
