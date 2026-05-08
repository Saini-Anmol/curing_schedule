"""
Rounder — converts continuous LP solution to integer cycles, deducts
changeover time, and tops up under-fulfilled high-priority SKUs.
"""

import math
from collections import defaultdict

import numpy as np
import pandas as pd

from V1.config.settings import Config


# ══════════════════════════════════════════════════════════════════════════════
# ROUNDER  (v2 — changeover-aware)
# ══════════════════════════════════════════════════════════════════════════════
class Rounder:
    """
    1. Floor all LP allocations to integer cycles.
    2. Deduct actual changeover time from each machine that runs > 1 SKU.
    3. Top up under-fulfilled high-priority SKUs with residual capacity.
    4. Record (machine → list[SKU]) assignment order for ScheduleBuilder.
    """

    def __init__(self):
        self.avail_mins = Config.avail_mins()
        self.co_mins    = Config.CHANGEOVER_DURATION_MIN

    def round(
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
        S        = meta["S"]
        M        = meta["M"]
        machines = meta["all_machines"]
        xidx     = meta["xidx"]
        sku_rows = meta["sku_rows"]

        # Remaining press capacity after locked continuity blocks
        machine_cap = {
            m: self.avail_mins - locked_machine_mins.get(m, 0.0)
            for m in machines
        }

        # First pass: floor all allocations
        raw = []
        for si, row in enumerate(sku_rows):
            ct = row.CycleTime_min
            for mi, mach in enumerate(machines):
                mins_lp = x[xidx(si, mi)]
                if mins_lp < ct:
                    continue
                cycles     = int(mins_lp / ct)
                actual_min = cycles * ct
                raw.append({"si": si, "mi": mi, "mach": mach,
                             "sku": row.SKUCode, "ct": ct,
                             "cycles": cycles, "mins": actual_min,
                             "priority": row.Priority})

        # Deduct changeover time for machines with > 1 SKU
        # Group raw assignments by machine
        by_machine: dict[str, list] = defaultdict(list)
        for a in raw:
            by_machine[a["mach"]].append(a)

        # For each machine, sort SKUs by priority, compute CO cost, trim if needed
        final = []
        machine_sku_order: dict[str, list[str]] = {}

        for mach, assignments in by_machine.items():
            assignments.sort(key=lambda a: -a["priority"])
            n_skus   = len(assignments)
            co_cost  = max(0, n_skus - 1) * self.co_mins
            avail    = machine_cap[mach] - co_cost
            used     = 0.0

            mach_skus = []
            for a in assignments:
                if avail - used < a["ct"]:
                    continue
                max_mins  = avail - used
                max_cyc   = int(max_mins / a["ct"])
                cycles    = min(a["cycles"], max_cyc)
                if cycles <= 0:
                    continue
                actual_min = cycles * a["ct"]
                used      += actual_min
                mach_skus.append(a["sku"])
                final.append({
                    "Machine":       mach,
                    "SKUCode":       a["sku"],
                    "Priority":      round(a["priority"], 4),
                    "CycleTime_min": a["ct"],
                    "Cycles":        cycles,
                    "Units_Planned": cycles * Config.CAVITIES_PER_MOULD,
                    "Mins_Used":     round(actual_min, 1),
                    "Days_Used":     round(actual_min / (Config.SHIFTS_PER_DAY
                                          * Config.HOURS_PER_SHIFT * 60), 2),
                })
            machine_sku_order[mach] = mach_skus
            machine_cap[mach] -= used + co_cost

        # Second pass: top-up under-fulfilled high-priority SKUs
        planned = defaultdict(int)
        for a in final:
            planned[a["SKUCode"]] += a["Units_Planned"]

        for si, row in enumerate(sorted(
            enumerate(sku_rows), key=lambda x: -x[1].Priority
        )):
            si, row = si, row[1]
            sku    = row.SKUCode
            ct     = row.CycleTime_min
            needed = int(row.Demand) - planned[sku]
            if needed <= 0:
                continue
            eligible = sorted(
                [mi for mi, m in enumerate(machines)
                 if machine_cap[machines[mi]] >= ct
                 and m in set(row.Machines)],
                key=lambda mi: -machine_cap[machines[mi]],
            )
            for mi in eligible:
                if needed <= 0:
                    break
                mach = machines[mi]
                cap  = machine_cap[mach]
                # Adding a new SKU to this machine costs one changeover
                existing_skus = len(machine_sku_order.get(mach, []))
                co  = self.co_mins if existing_skus > 0 else 0.0
                available = cap - co
                if available < ct:
                    continue
                extra_c = min(int(available / ct), math.ceil(needed / Config.CAVITIES_PER_MOULD))
                if extra_c <= 0:
                    continue
                actual_min = extra_c * ct
                machine_cap[mach] -= actual_min + co
                units = extra_c * Config.CAVITIES_PER_MOULD
                planned[sku] += units
                needed       -= units
                machine_sku_order.setdefault(mach, []).append(sku)
                final.append({
                    "Machine":       mach,
                    "SKUCode":       sku,
                    "Priority":      round(row.Priority, 4),
                    "CycleTime_min": ct,
                    "Cycles":        extra_c,
                    "Units_Planned": units,
                    "Mins_Used":     round(actual_min, 1),
                    "Days_Used":     round(actual_min / (Config.SHIFTS_PER_DAY
                                          * Config.HOURS_PER_SHIFT * 60), 2),
                })

        df_sched = pd.DataFrame(final)
        total_co = sum(
            max(0, len(v) - 1) * self.co_mins
            for v in machine_sku_order.values()
        )
        print(f"  [Round] Rows: {len(df_sched)} | "
              f"Units: {df_sched['Units_Planned'].sum():,.0f} | "
              f"Total CO time: {total_co/60:.1f} hrs | "
              f"Changeovers: {sum(max(0,len(v)-1) for v in machine_sku_order.values())}")
        return df_sched, machine_sku_order
