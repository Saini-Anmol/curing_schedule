"""
ScheduleBuilder — translates the rounded LP allocation into a shift-level
timeline, inserting CHANGEOVER and MOULD_CLEAN events.
"""

import math
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from V1.config.settings import Config
from V1.utilities.shifts import _get_shift_fn, con_split_into_shifts


# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULE BUILDER  (v2 — changeover + cleaning blocks + CO shift cap)
# ══════════════════════════════════════════════════════════════════════════════
class ScheduleBuilder:
    """
    Builds a shift-level timeline for each machine:

      PRODUCTION   : normal tyre production rows
      CHANGEOVER   : 300-min block between SKU runs
      MOULD_CLEAN  : 120-min block inserted after every
                     units_per_cleaning_cycle (default 6000) units

    Changeover cap: at most MAX_CHANGEOVERS_PER_SHIFT changeovers
    start in any single 8-hour shift across the whole plant.
    Excess changeovers are deferred to the next shift.
    """

    def __init__(self, plan_start: datetime, algo_label: str = "LP"):
        self.plan_start = plan_start
        self.plan_end   = plan_start + timedelta(days=Config.PLANNING_DAYS)
        self.max_co_shift = Config.MAX_CHANGEOVERS_PER_SHIFT
        # {(date, shift): count_of_COs_started}
        self._co_shift_counter: dict[tuple, int] = defaultdict(int)
        # Remarks string used for production rows; defaults to "LP" for
        # backwards compatibility with the LP-only contract.
        self._remarks = f"{algo_label} Scheduled"

    # ── shift helpers ─────────────────────────────────────────────────────────
    @staticmethod
    def _get_shift(dt: datetime) -> tuple[str, datetime]:
        return _get_shift_fn(dt)

    def _shift_key(self, dt: datetime) -> tuple:
        shift, _ = self._get_shift(dt)
        return (dt.date(), shift)

    def _next_co_slot(self, earliest: datetime) -> datetime:
        """
        Return the earliest datetime >= earliest at which a new changeover
        can start without exceeding MAX_CHANGEOVERS_PER_SHIFT.
        Defers to next shift if current shift is full.
        """
        dt = earliest
        for _ in range(Config.PLANNING_DAYS * Config.SHIFTS_PER_DAY + 1):
            key = self._shift_key(dt)
            if self._co_shift_counter[key] < self.max_co_shift:
                self._co_shift_counter[key] += 1
                return dt
            # Move to start of next shift
            _, shift_end = self._get_shift(dt)
            dt = shift_end
        return dt  # fallback — should not reach here

    # ── row maker ─────────────────────────────────────────────────────────────
    def _make_row(self, start: datetime, end: datetime, machine: str,
                  sku: str, qty: int, ct: float, remarks: str, gt_inv: int = 0):
        shift, _ = self._get_shift(start)
        return {
            "Date":          start.date(),
            "Shift":         shift,
            "Machine":       machine,
            "SKUCode":       sku,
            "StartTime":     start,
            "EndTime":       end,
            "Qty":           qty,
            "CycleTime_min": round(ct, 2),
            "GT_Inventory":  gt_inv,
            "Remarks":       remarks,
        }

    # ── production block splitter ─────────────────────────────────────────────
    def _split_block(
        self, start: datetime, end: datetime,
        machine: str, sku: str, ct: float,
        gt_inv: int, remarks: str, units_so_far: int,
    ) -> tuple[list[dict], int]:
        """
        Split a production block (start→end) into shift rows.
        Insert MOULD_CLEAN events every units_per_cleaning_cycle units.
        Returns (rows, new_units_so_far).
        """
        rows = []
        cleaning_cycle = Config.units_per_cleaning_cycle()
        total_mins = (end - start).total_seconds() / 60
        total_units = int(total_mins / ct) * Config.CAVITIES_PER_MOULD
        produced   = 0
        curr       = start

        while curr < end and produced < total_units:
            # How many units until next cleaning?
            units_to_clean = cleaning_cycle - (units_so_far % cleaning_cycle)
            units_this_run = min(total_units - produced, units_to_clean)
            mins_this_run  = math.ceil(units_this_run / Config.CAVITIES_PER_MOULD) * ct
            run_end        = min(curr + timedelta(minutes=mins_this_run), end)

            # Split run_end across shifts
            inner = curr
            run_produced = 0
            run_total = int((run_end - curr).total_seconds() / 60 / ct) * Config.CAVITIES_PER_MOULD

            while inner < run_end:
                _, shift_end = self._get_shift(inner)
                slice_end    = min(shift_end, run_end)
                dur          = (slice_end - inner).total_seconds() / 60
                if dur <= 0:
                    inner = slice_end
                    continue
                if slice_end == run_end:
                    qty = run_total - run_produced
                else:
                    qty = int(dur / ct) * Config.CAVITIES_PER_MOULD
                rows.append(self._make_row(inner, slice_end, machine, sku,
                                           qty, ct, remarks, gt_inv))
                run_produced += qty
                inner = slice_end

            produced      += run_produced
            units_so_far  += run_produced
            curr           = run_end

            # Insert cleaning if hit cycle boundary and more production follows
            if (units_so_far % cleaning_cycle == 0
                    and produced < total_units and curr < end):
                clean_end = curr + timedelta(minutes=Config.CLEANING_DURATION_MIN)
                rows.append(self._make_row(curr, clean_end, machine,
                                           "MOULD_CLEAN", 0, 0.0,
                                           f"Mould cleaning after {units_so_far} units"))
                curr = clean_end

        return rows, units_so_far

    # ── main builder ──────────────────────────────────────────────────────────
    def build(
        self,
        df_sched: pd.DataFrame,
        machine_sku_order: dict[str, list[str]],
        df_gt: pd.DataFrame,
        continuity_rows: list[dict],
    ) -> pd.DataFrame:
        """
        Parameters
        ----------
        df_sched          : rounded allocation (Machine, SKUCode, Cycles, ...)
        machine_sku_order : {machine: [sku1, sku2...]} run order
        df_gt             : GT inventory for each SKU
        continuity_rows   : pre-built rows for currently running moulds
        """
        gt_map      = dict(zip(df_gt["SKUCode"], df_gt["GT_Inventory"]))
        # Machine cursors: start at plan_start unless occupied by continuity
        machine_free: dict[str, datetime] = {}
        for r in continuity_rows:
            m = r["Machine"]
            if m not in machine_free or r["EndTime"] > machine_free[m]:
                machine_free[m] = r["EndTime"]

        # Build lookup: (machine, sku) -> Cycles
        alloc: dict[tuple, dict] = {}
        for _, row in df_sched.iterrows():
            alloc[(row["Machine"], row["SKUCode"])] = row.to_dict()

        #all_rows = list(continuity_rows)
        all_rows = []
        con_dataframe = con_split_into_shifts(pd.DataFrame(continuity_rows))

        con_dataframe['Date'] = con_dataframe['StartTime'].dt.date
        con_dataframe['Date'] = con_dataframe['Date'].astype('datetime64[ns]')
        con_dataframe['Date'] = np.where(con_dataframe['StartTime'].dt.hour.isin([0,1,2,3,4,5,6]), con_dataframe['Date'] - pd.Timedelta(1, inut='d'), con_dataframe['Date'])

        # Process each machine in order
        for mach, sku_order in machine_sku_order.items():
            cursor = machine_free.get(str(mach), self.plan_start)
            units_so_far = 0   # cumulative units for cleaning tracker

            for idx, sku in enumerate(sku_order):
                key = (mach, sku)
                if key not in alloc:
                    continue
                a  = alloc[key]
                ct = a["CycleTime_min"]
                gt = int(gt_map.get(sku, 0))

                # Changeover block (not before first SKU on this machine)
                if idx > 0:
                    co_start = self._next_co_slot(cursor)
                    co_end   = co_start + timedelta(minutes=Config.CHANGEOVER_DURATION_MIN)
                    all_rows.append(self._make_row(
                        co_start, co_end, mach, "CHANGEOVER", 0, 0.0,
                        f"C/O to {sku}"
                    ))
                    cursor = co_end

                # Production block
                total_mins = a["Mins_Used"]
                block_end  = min(
                    cursor + timedelta(minutes=total_mins),
                    self.plan_end,
                )
                if block_end <= cursor:
                    continue

                prod_rows, units_so_far = self._split_block(
                    cursor, block_end, mach, sku, ct,
                    gt, self._remarks, units_so_far,
                )
                all_rows.extend(prod_rows)
                cursor = block_end
            machine_free[mach] = cursor

        df_out = pd.DataFrame(all_rows)
        df_out = pd.concat([df_out, con_dataframe], axis = 0)
        if not df_out.empty:
            df_out = df_out.sort_values(["Machine","StartTime"]).reset_index(drop=True)
        co_count = (df_out["SKUCode"] == "CHANGEOVER").sum()
        cl_count = (df_out["SKUCode"] == "MOULD_CLEAN").sum()
        prod_qty = df_out.loc[~df_out["SKUCode"].isin(["CHANGEOVER","MOULD_CLEAN"]),"Qty"].sum()
        print(f"  [Build] Total rows: {len(df_out)} | "
              f"Prod qty: {prod_qty:,.0f} | "
              f"Changeovers: {co_count} | Cleanings: {cl_count}")
        return df_out
