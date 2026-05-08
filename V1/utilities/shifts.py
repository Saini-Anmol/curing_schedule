"""
Shift helpers: derive (shift_letter, shift_end) from a timestamp, and split a
continuity row spanning multiple shifts into per-shift rows.
"""

from datetime import datetime, timedelta

import pandas as pd

from V1.config.settings import Config


def con_split_into_shifts(df):
    rows = []

    for _, r in df.iterrows():
        start = r['StartTime']
        end   = r['EndTime']
        total_qty = r['Qty']
        total_minutes = (end - start).total_seconds() / 60

        current = start

        while current < end:
            shift, shift_end = _get_shift_fn(current)

            slice_end = min(shift_end, end)
            slice_minutes = (slice_end - current).total_seconds() / 60

            # proportional qty allocation
            qty = (slice_minutes / total_minutes) * total_qty if total_minutes > 0 else 0

            new_row = r.copy()
            new_row['StartTime'] = current
            new_row['EndTime']   = slice_end
            new_row['Shift']     = shift
            new_row['Qty']       = round(qty)

            rows.append(new_row)

            current = slice_end

    return pd.DataFrame(rows)
# ══════════════════════════════════════════════════════════════════════════════
# PATCH: make _get_shift a static-callable helper
# ══════════════════════════════════════════════════════════════════════════════
def _get_shift_fn(dt: datetime) -> tuple[str, datetime]:
    h    = dt.hour
    base = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    sh   = Config.SHIFT_START_HOUR
    if sh <= h < sh + 8:
        return "A", base + timedelta(hours=sh + 8)
    elif sh + 8 <= h < sh + 16:
        return "B", base + timedelta(hours=sh + 16)
    else:
        if h >= sh + 16:
            return "C", base + timedelta(days=1, hours=sh)
        else:
            return "C", base + timedelta(hours=sh)
