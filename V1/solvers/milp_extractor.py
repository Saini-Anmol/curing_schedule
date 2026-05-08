"""
MILP_Extractor — turns the integer MILP solution vector into the (df_sched,
machine_sku_order) tuple consumed by ScheduleBuilder.

No rounding logic. The solver already returns integer cycles, so this module
only formats the result.
"""

from collections import defaultdict

import numpy as np
import pandas as pd

from V1.config.settings import Config


# ══════════════════════════════════════════════════════════════════════════════
# MILP EXTRACTOR
# ══════════════════════════════════════════════════════════════════════════════
class MILP_Extractor:
    """
    Turns the integer MILP solution into the same dataframes the rest of
    the pipeline expects.

    Because the MILP already returns integer cycles, there is NO floor-rounding
    loss and no priority top-up pass. The only remaining decision is the run
    ORDER of SKUs on each press, which we keep consistent with the LP version:
    sort by priority descending (ScheduleBuilder uses this order to place
    changeovers).
    """

    def __init__(self):
        self.avail_mins = Config.avail_mins()
        self.co_mins    = Config.CHANGEOVER_DURATION_MIN

    def extract(
        self,
        x: np.ndarray,
        meta: dict,
        df_valid: pd.DataFrame,
        locked_machine_mins: dict[str, float],
    ) -> tuple[pd.DataFrame, dict[str, list[str]]]:
        """
        Returns
        -------
        df_sched         : (Machine, SKUCode, Priority, CycleTime_min,
                            Cycles, Units_Planned, Mins_Used, Days_Used)
        machine_sku_order: {machine: [sku1, sku2, ...]} in run order
        """
        P         = meta["P"]
        machines  = meta["all_machines"]
        sku_rows  = meta["sku_rows"]
        pair_list = meta["pair_list"]

        if P == 0:
            return pd.DataFrame(columns=[
                "Machine", "SKUCode", "Priority", "CycleTime_min",
                "Cycles", "Units_Planned", "Mins_Used", "Days_Used",
            ]), {}

        cycles_arr = x[0:P]
        # y_arr exists at x[P:2P] but is redundant given the linking constraint

        # Group active assignments by machine
        by_machine: dict[str, list] = defaultdict(list)
        for p, (si, mi) in enumerate(pair_list):
            cyc = int(round(float(cycles_arr[p])))
            if cyc <= 0:
                continue
            row = sku_rows[si]
            by_machine[machines[mi]].append({
                "sku":      row.SKUCode,
                "priority": row.Priority,
                "ct":       row.CycleTime_min,
                "cycles":   cyc,
            })

        final: list[dict] = []
        machine_sku_order: dict[str, list[str]] = {}
        shift_min = Config.SHIFTS_PER_DAY * Config.HOURS_PER_SHIFT * 60

        for mach, assigns in by_machine.items():
            # Keep run order consistent with LP variant (priority desc)
            assigns.sort(key=lambda a: -a["priority"])
            machine_sku_order[mach] = [a["sku"] for a in assigns]
            for a in assigns:
                actual_min = a["cycles"] * a["ct"]
                final.append({
                    "Machine":       mach,
                    "SKUCode":       a["sku"],
                    "Priority":      round(a["priority"], 4),
                    "CycleTime_min": a["ct"],
                    "Cycles":        a["cycles"],
                    "Units_Planned": a["cycles"] * Config.CAVITIES_PER_MOULD,
                    "Mins_Used":     round(actual_min, 1),
                    "Days_Used":     round(actual_min / shift_min, 2),
                })

        df_sched = pd.DataFrame(final)
        n_co = sum(max(0, len(v) - 1) for v in machine_sku_order.values())
        total_co_min = n_co * self.co_mins
        total_units = int(df_sched["Units_Planned"].sum()) if not df_sched.empty else 0
        print(f"  [Extract] Rows: {len(df_sched)} | Units: {total_units:,} | "
              f"Changeovers: {n_co} | CO time: {total_co_min/60:.1f} hrs")
        return df_sched, machine_sku_order
