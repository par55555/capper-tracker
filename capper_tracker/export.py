"""Write the weekly report to a formatted .xlsx workbook."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .config import TZ

_HEADER_FILL = PatternFill("solid", fgColor="1F3864")
_HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF")
_BODY_FONT = Font(name="Arial", size=10)
_WRAP_COLUMNS = {"who", "bets", "pass_players", "run_players", "td_players", "snippet", "text"}


def _excel_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Excel can't store tz-aware datetimes; show times in ET."""
    df = df.copy()
    for col in df.columns:
        if col in ("created_at", "first_posted"):
            ts = pd.to_datetime(df[col], utc=True, errors="coerce")
            df[col] = ts.dt.tz_convert(TZ).dt.strftime("%a %m/%d %I:%M %p")
    return df


def write_report(frames: dict[str, pd.DataFrame], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in frames.items():
            df = df if not df.empty else pd.DataFrame({"note": ["No data for this week yet."]})
            _excel_safe(df).to_excel(xw, sheet_name=name[:31], index=False)

    wb = load_workbook(path)
    for ws in wb.worksheets:
        headers = [c.value for c in ws[1]]
        for cell in ws[1]:
            cell.fill, cell.font = _HEADER_FILL, _HEADER_FONT
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.font = _BODY_FONT
                if headers[cell.column - 1] in _WRAP_COLUMNS:
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
        for idx, header in enumerate(headers, start=1):
            longest = max((len(str(c.value)) for c in ws[get_column_letter(idx)] if c.value is not None),
                          default=8)
            ws.column_dimensions[get_column_letter(idx)].width = min(max(longest + 2, 10), 60)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
    wb.save(path)
    return path
