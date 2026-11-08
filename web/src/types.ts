export type Row = Record<string, any>;

export interface Frame {
  columns: string[];
  rows: Row[];
}

export interface Summary {
  posts: number;
  picks: number;
  accounts_with_picks: number;
  shared_bets: number;
  conflicts: number;
  failed_posts: number;
  pending_posts: number;
}

export interface Report {
  window: { name: string; label: string; start: string; end: string };
  built_at: string;
  summary: Summary;
  frames: Record<string, Frame>;
}

export interface WeekInfo {
  week: number;
  posts: number;
  extracted: number;
  failed: number;
  pending: number;
  picks: number;
}

export interface Account {
  handle: string;
  display_name: string | null;
  resolved: boolean;
  last_post: string | null;
  posts: number;
  picks: number;
}

export interface Run {
  id: string;
  command: string;
  started: string;
  finished: string | null;
  exit_code: number | null;
  running: boolean;
  output?: string[];
}

export type Scope = { kind: "week"; week: number } | { kind: "days"; days: number };

export interface Record3 {
  won: number;
  lost: number;
  push: number;
  hit_rate: number | null;
}

export interface ResultsReport {
  window: { name: string; label: string };
  built_at: string;
  summary: {
    shared: Record3 & { bets: number };
    all_picks: Record3 & { picks: number };
    pending: number;
    ungraded: number;
  };
  frames: Record<string, Frame>;
}

export interface TrendsReport {
  built_at: string;
  weeks: number[];
  frames: Record<string, Frame>;
}
