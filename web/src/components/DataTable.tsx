import { ReactNode, useMemo, useState } from "react";
import type { Row } from "../types";

export interface Column {
  key: string;
  label: string;
  render?: (row: Row) => ReactNode;
  /** Value used for sorting; defaults to row[key]. */
  sortValue?: (row: Row) => unknown;
  align?: "left" | "right" | "center";
  width?: string;
  title?: string;
}

interface Props {
  rows: Row[];
  columns: Column[];
  /** Row fields included in the text search. */
  searchKeys?: string[];
  initialSort?: { key: string; dir: "asc" | "desc" };
  toolbar?: ReactNode;
  empty?: ReactNode;
  rowClass?: (row: Row) => string | undefined;
  pageSize?: number;
}

function compare(a: unknown, b: unknown): number {
  const an = a == null || a === "", bn = b == null || b === "";
  if (an || bn) return an && bn ? 0 : an ? 1 : -1; // blanks last either way
  if (typeof a === "number" && typeof b === "number") return a - b;
  if (typeof a === "boolean" && typeof b === "boolean") return Number(a) - Number(b);
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

export function DataTable({ rows, columns, searchKeys, initialSort, toolbar, empty, rowClass, pageSize = 100 }: Props) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState(initialSort);
  const [limit, setLimit] = useState(pageSize);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    let out = rows;
    if (needle) {
      const keys = searchKeys ?? columns.map((c) => c.key);
      out = rows.filter((r) => keys.some((k) => String(r[k] ?? "").toLowerCase().includes(needle)));
    }
    if (sort) {
      const col = columns.find((c) => c.key === sort.key);
      const get = col?.sortValue ?? ((r: Row) => r[sort.key]);
      const sign = sort.dir === "asc" ? 1 : -1;
      out = [...out].sort((a, b) => {
        const va = get(a), vb = get(b);
        const blank = (v: unknown) => v == null || v === "";
        if (blank(va) || blank(vb)) return compare(va, vb); // keep blanks at the bottom
        return sign * compare(va, vb);
      });
    }
    return out;
  }, [rows, columns, q, sort, searchKeys]);

  const toggle = (key: string) =>
    setSort((s) => (s?.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "desc" }));

  return (
    <div className="table-card">
      <div className="table-toolbar">
        <input className="search" placeholder="Search…" value={q} onChange={(e) => { setQ(e.target.value); setLimit(pageSize); }} />
        {toolbar}
        <span className="muted count">{filtered.length.toLocaleString()} of {rows.length.toLocaleString()}</span>
      </div>
      {filtered.length === 0 ? (
        <div className="empty">{rows.length === 0 ? empty ?? "Nothing here for this window." : "No rows match your search."}</div>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c.key} style={{ width: c.width, textAlign: c.align }} title={c.title}
                      onClick={() => toggle(c.key)} className={sort?.key === c.key ? "sorted" : undefined}>
                    {c.label}
                    <span className="sort-ind">{sort?.key === c.key ? (sort.dir === "asc" ? "▲" : "▼") : ""}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, limit).map((r, i) => (
                <tr key={i} className={rowClass?.(r)}>
                  {columns.map((c) => (
                    <td key={c.key} style={{ textAlign: c.align }}>
                      {c.render ? c.render(r) : r[c.key] == null ? "" : String(r[c.key])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {filtered.length > limit && (
        <button className="btn ghost more" onClick={() => setLimit((l) => l + pageSize)}>
          Show {Math.min(pageSize, filtered.length - limit)} more
        </button>
      )}
    </div>
  );
}
