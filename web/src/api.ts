import type { Account, ExportFile, Run, Scope, TrendsReport, WeekInfo, WeekPayload } from "./types";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(msg);
  }
  return res.json();
}

const scopeQuery = (s: Scope) => (s.kind === "week" ? `week=${s.week}` : `days=${s.days}`);

export const api = {
  weeks: () => call<{ current_week: number; weeks: WeekInfo[] }>("/api/weeks"),
  /** No scope = this NFL week so far (the Live page). */
  week: (s: Scope | null, refresh = false) => {
    const q = [s ? scopeQuery(s) : "", refresh ? "refresh=true" : ""].filter(Boolean).join("&");
    return call<WeekPayload>(`/api/week${q ? `?${q}` : ""}`);
  },
  exports: (week?: number) => call<{ exports: ExportFile[] }>(`/api/exports${week != null ? `?week=${week}` : ""}`),
  trends: (refresh = false) => call<TrendsReport>(`/api/trends${refresh ? "?refresh=true" : ""}`),
  accounts: () => call<{ accounts: Account[] }>("/api/accounts"),
  saveAccounts: (handles: string[]) =>
    call<{ accounts: Account[] }>("/api/accounts", { method: "PUT", body: JSON.stringify({ handles }) }),
  runs: () => call<{ runs: Run[] }>("/api/runs"),
  run: (id: string) => call<Run>(`/api/runs/${id}`),
  startRun: (body: { command: string; week?: number; days?: number; redo_all?: boolean }) =>
    call<Run>("/api/runs", { method: "POST", body: JSON.stringify(body) }),
  cancelRun: (id: string) => call<Run>(`/api/runs/${id}/cancel`, { method: "POST" }),
};
