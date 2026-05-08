"""
Orchestrator for the curing schedulers. Ties Phase 1–5 together and exposes a
CLI that dispatches to LP, MILP or CP-SAT.

Run from the repo root with one of:

    python -m V1.main                  # default: --algo lp
    python -m V1.main --algo lp
    python -m V1.main --algo milp
    python -m V1.main --algo cpsat

This calls run_from_database with the default demand CSV
(`input/Feb_CTP_PCR_Requirement.csv`) and PLAN_DATE from Config. The
algorithm-specific output filename is derived from `Config.output_file_for`.

The orchestrator class (``JK_LP_Curing_Scheduler_v2``) is parametric — Phases
1, 2 and 5 are identical across algorithms; only Phases 3-4 (the solver and
extractor calls) differ. Subclasses ``JK_MILP_Curing_Scheduler_v1`` and
``JK_CPSAT_Curing_Scheduler_v1`` override those two phases.
"""

import math
import warnings
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd

from V1.config.settings import Config
from V1.reports.excel_exporter import ExcelExporter  # re-exported for callers
from V1.reports.schedule_builder import ScheduleBuilder
from V1.setups.etl import ETL  # re-exported for callers
from V1.setups.mould_tracker import MouldTracker
from V1.solvers.cpsat_extractor import CPSAT_Extractor
from V1.solvers.cpsat_solver import CPSAT_Solver
from V1.solvers.lp_solver import LP_Solver
from V1.solvers.milp_extractor import MILP_Extractor
from V1.solvers.milp_solver import MILP_Solver
from V1.solvers.rounder import Rounder
from V1.utilities.shifts import _get_shift_fn

warnings.filterwarnings("ignore")


# ══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════
class JK_LP_Curing_Scheduler_v2:
    # ── algorithm profile (subclasses override) ──────────────────────────────
    BANNER_TITLE   = "JK Tyre PCR Curing LP Scheduler v2"
    ALGO_LABEL     = "LP"
    INPUT_TAG      = "LP Input"
    PHASE3_HEADER  = "[Phase 3] Solving LP..."
    PHASE4_HEADER  = "[Phase 4] Rounding to integer cycles..."
    EXTRA_BANNER   = ()  # iterable of additional print lines for the banner

    def _build_solver(self):
        return LP_Solver()

    def _build_extractor(self):
        return Rounder()

    def _run_solver(self, solver, df_lp, all_machines, mould_tracker, locked_mins):
        return solver.solve(df_lp, all_machines, mould_tracker, locked_mins)

    def _run_extractor(self, extractor, solver_out, meta, df_lp, locked_mins):
        # LP variant: extractor is Rounder, method is .round
        return extractor.round(solver_out, meta, df_lp, locked_mins)

    def __init__(self):
        self.avail_mins = Config.avail_mins()

    # ── prep ──────────────────────────────────────────────────────────────────
    def _prepare_skus(self, df_demand, df_cycles, df_allow, df_gt,
                      mould_tracker: MouldTracker, df_running):
        cycle_map  = dict(zip(df_cycles["SKUCode"], df_cycles["CycleTime_min"]))
        mach_map   = dict(zip(df_allow["SKUCode"],  df_allow["Machines"]))
        gt_map     = dict(zip(df_gt["SKUCode"],     df_gt["GT_Inventory"]))

        rows = []
        for _, r in df_demand.iterrows():
            sku      = r["SKUCode"]
            qty      = int(r["Quantity"])
            priority = r["Priority"]
            ct       = cycle_map.get(sku, 15)
            machines = mach_map.get(sku, [])
            gt       = int(gt_map.get(sku, 0))
            dm       = math.ceil(qty / Config.CAVITIES_PER_MOULD) * ct if ct else 0
            if sku in df_running['SKUCode'].unique():
                has_mould = True
            else:
                has_mould = mould_tracker.can_assign(sku) if (ct and machines) else False

            #has_mould = mould_tracker.can_assign(sku) if (ct and machines) else False
            schedulable = bool(ct and machines and has_mould)
            skip = ("" if schedulable
                    else ("No cycle time" if not ct
                          else ("No machine mapping" if not machines
                                else "No compatible mould available")))
            rows.append({
                "SKUCode":          sku,
                "Demand":           qty,
                "Priority":         priority,
                "GT_Inventory":     gt,
                "CycleTime_min":    ct,
                "Machines":         machines,
                "Num_Machines":     len(machines),
                "Demand_Mins":      dm,
                "Presses_Needed":   round(dm / self.avail_mins, 2) if ct else 0,
                "Schedulable":      schedulable,
                "Skip_Reason":      skip,
            })

        df_all   = pd.DataFrame(rows)
        df_valid = df_all[df_all["Schedulable"]].copy().reset_index(drop=True)
        df_valid = df_valid.sort_values(["Priority","Num_Machines"],
                                        ascending=[False,True]).reset_index(drop=True)
        all_machines = sorted({m for ml in df_allow["Machines"] for m in ml})
        print(f"  [Prep] Schedulable: {len(df_valid)}/{len(df_all)} | "
              f"Machines: {len(all_machines)} | "
              f"Demand: {df_valid['Demand'].sum():,.0f}")
        return df_valid, df_all, all_machines

    def _build_continuity(self, df_running: pd.DataFrame,
                          df_valid: pd.DataFrame,
                          df_gt: pd.DataFrame,
                          plan_start: datetime) -> tuple[list[dict], dict[str, float], dict[str, int]]:
        """
        Schedule continuity blocks for all currently running machines.

        Key rules
        ---------
        1. Group all machines by SKU — every press running SKU X works together
           to satisfy SKU X demand before the LP handles anything else.
        2. Distribute demand proportionally across machines, weighted by each
           machine's horizon capacity (more moulds / faster cycle = larger share).
        3. If total group capacity < demand (even full horizon), run all machines
           for the full horizon and return the unmet remainder to the LP.
        4. Mould cleaning (120 min) is inserted mid-run whenever a machine hits
           NEW_MOULD_LIFE cycles (min of LH/RH life drives the trigger).
        5. locked_machine_mins tells the LP how much capacity each machine has
           already committed.

        Returns
        -------
        continuity_rows    : list of shift-level row dicts
        locked_machine_mins: {machine: minutes_committed}
        demand_remainder   : {sku: units_still_needed_from_LP}
        """
        if df_running is None or df_running.empty:
            return [], {}, {}

        cycle_map  = dict(zip(df_valid["SKUCode"], df_valid["CycleTime_min"]))
        demand_map = dict(zip(df_valid["SKUCode"], df_valid["Demand"]))
        gt_map     = dict(zip(df_gt["SKUCode"],    df_gt["GT_Inventory"]))
        plan_end      = plan_start + timedelta(days=Config.PLANNING_DAYS)
        horizon_mins  = Config.avail_mins()

        continuity_rows  = []
        locked_mins      = {}
        demand_remainder = {}

        # ── Step 1: group running machines by SKU ─────────────────────────────
        sku_groups: dict[str, list] = defaultdict(list)
        for _, row in df_running.iterrows():
            mach = str(row["Machine"])
            sku  = str(row["SKUCode"])
            ct   = cycle_map.get(sku, 15)
            if not ct:
                locked_mins[mach] = 0.0   # unknown cycle time — release immediately
                continue
            sku_groups[sku].append({
                "machine":  mach,
                "life":     int(row.get("MouldLife_remaining", Config.NEW_MOULD_LIFE)),
                "cavities": int(row.get("Num_Moulds", Config.MOULDS_PER_PRESS)),
                "ct":       ct,
            })

        # ── Step 2: process each SKU group ────────────────────────────────────
        for sku, group in sku_groups.items():
            demand = demand_map.get(sku, 0)
            gt     = int(gt_map.get(sku, 0))

            if demand <= 0:
                for m in group:
                    locked_mins[m["machine"]] = 0.0
                continue

            # Total units each machine can produce over the full horizon
            for m in group:
                m["max_units"] = int(horizon_mins / m["ct"]) * m["cavities"]
            group_total_cap = sum(m["max_units"] for m in group)

            can_meet = group_total_cap >= demand

            # ── Step 3: distribute demand across machines ──────────────────────
            # Proportional by each machine's share of group capacity.
            # Process machines so rounding shortfall goes to the highest-cap press.
            group_sorted = sorted(group, key=lambda m: -m["max_units"])
            remaining_demand = demand
            for i, m in enumerate(group_sorted):
                if not can_meet:
                    # Run every machine at full horizon — LP handles remainder
                    alloc = m["max_units"]
                elif remaining_demand <= 0:
                    alloc = 0
                elif i == len(group_sorted) - 1:
                    # Last machine: take exactly what's left (no rounding gap)
                    alloc = min(remaining_demand, m["max_units"])
                else:
                    share = m["max_units"] / group_total_cap if group_total_cap else 0
                    alloc = min(math.ceil(demand * share), m["max_units"], remaining_demand)

                m["alloc_units"] = max(alloc, 0)
                remaining_demand -= m["alloc_units"]

            # Pass unmet demand to LP
            if not can_meet:
                unmet = demand - group_total_cap
                demand_remainder[sku] = max(int(unmet), 0)
                if unmet > 0:
                    print(f"  [Cont] SKU {sku}: group cap {group_total_cap:,} < "
                          f"demand {demand:,} -> {unmet:,} units to LP")
            else:
                demand_remainder[sku] = 0

            # ── Step 4: build shift rows for each machine ──────────────────────
            for m in group:
                mach        = m["machine"]
                ct          = m["ct"]
                life        = m["life"]
                cavities    = m["cavities"]
                alloc_units = m["alloc_units"]

                if alloc_units <= 0:
                    locked_mins[mach] = 0.0
                    continue

                alloc_mins         = math.ceil(alloc_units / cavities) * ct
                units_before_clean = life * cavities   # min(LH,RH) drives trigger
                cursor             = plan_start
                remaining_mins     = alloc_mins

                while remaining_mins > 0 and cursor < plan_end:
                    # Run until mould clean threshold or allocation exhausted
                    mins_to_clean = (units_before_clean / cavities) * ct
                    block_mins    = min(mins_to_clean, remaining_mins)
                    block_end     = min(cursor + timedelta(minutes=block_mins), plan_end)
                    actual_mins   = (block_end - cursor).total_seconds() / 60
                    qty           = int(actual_mins / ct) * cavities

                    shift, _ = _get_shift_fn(cursor)
                    continuity_rows.append({
                        "Date":          cursor.date(),
                        "Shift":         shift,
                        "Machine":       mach,
                        "SKUCode":       sku,
                        "StartTime":     cursor,
                        "EndTime":       block_end,
                        "Qty":           qty,
                        "CycleTime_min": ct,
                        "GT_Inventory":  gt,
                        "Remarks":       f"Continuity ({cavities}-mould press)",
                    })
                    cursor        = block_end
                    remaining_mins -= block_mins

                    # Mould cleaning if more production follows
                    if remaining_mins > 0 and cursor < plan_end:
                        clean_end = min(
                            cursor + timedelta(minutes=Config.CLEANING_DURATION_MIN),
                            plan_end,
                        )
                        shift, _ = _get_shift_fn(cursor)
                        continuity_rows.append({
                            "Date":          cursor.date(),
                            "Shift":         shift,
                            "Machine":       mach,
                            "SKUCode":       "MOULD_CLEAN",
                            "StartTime":     cursor,
                            "EndTime":       clean_end,
                            "Qty":           0,
                            "CycleTime_min": 0.0,
                            "GT_Inventory":  0,
                            "Remarks":       "Mould cleaning (continuity)",
                        })
                        cursor             = clean_end
                        units_before_clean = Config.NEW_MOULD_LIFE * cavities

                locked_mins[mach] = (cursor - plan_start).total_seconds() / 60

        demand_remainder = {k: (0 if v < 50 else v) for k, v in demand_remainder.items()}

        total_locked = sum(locked_mins.values())
        print(f"  [Cont] {len(continuity_rows)} rows | "
              f"Machines committed: {len(locked_mins)} | "
              f"Locked mins: {total_locked:,.0f} | "
              f"SKUs with LP remainder: {sum(1 for v in demand_remainder.values() if v > 0)}")
        return continuity_rows, locked_mins, demand_remainder


    def _build_summary(self, df_all, df_sched, df_shift):
        """
        Build demand fulfillment summary.

        Source of truth: df_shift (the shift schedule).
        It contains ALL production — both continuity rows and LP-sourced rows.
        We never mix df_mach (LP allocation) with df_shift here because
        ScheduleBuilder converts df_mach rows into df_shift rows, meaning
        every LP unit already appears in df_shift. Adding df_mach on top
        would double-count every unit.
        """
        if not df_shift.empty:
            prod = df_shift[~df_shift["SKUCode"].isin(["CHANGEOVER", "MOULD_CLEAN"])]
            planned = prod.groupby("SKUCode")["Qty"].sum().to_dict()
        else:
            planned = {}

        rows = []
        for _, r in df_all.iterrows():
            sku  = r["SKUCode"]
            d    = r["Demand"]
            plan = int(planned.get(sku, 0))
            gap  = max(d - plan, 0)
            pct  = round(plan / d * 100, 1) if d > 0 else 100.0
            if not r["Schedulable"]:
                status = "UNSCHEDULABLE"
            elif gap <= 0:
                status = "FULLY MET"
            elif plan > 0:
                status = "PARTIAL"
            else:
                status = "UNMET"
            rows.append({"SKUCode": sku, "Priority": r["Priority"],
                         "Demand": d, "GT_Inventory": r["GT_Inventory"],
                         "Planned_Units": plan, "Gap": gap,
                         "Fulfillment_Pct": pct, "Status": status,
                         "CycleTime_min": r["CycleTime_min"],
                         "Eligible_Machines": r["Num_Machines"],
                         "Presses_Needed": r["Presses_Needed"],
                         "Skip_Reason": r["Skip_Reason"]})
        return pd.DataFrame(rows).sort_values("Priority", ascending=False)

    def _build_util(self, df_sched, df_shift, all_machines):
        # Use df_shift as single source of truth (contains both continuity and LP rows).
        # Compute elapsed minutes from actual timestamps to avoid double-counting.
        if not df_shift.empty:
            prod = df_shift[~df_shift["SKUCode"].isin(["CHANGEOVER","MOULD_CLEAN"])].copy()
            prod['Machine'] = prod['Machine'].astype('int64')
            prod["Elapsed"] = (pd.to_datetime(prod["EndTime"]) - pd.to_datetime(prod["StartTime"])).dt.total_seconds() / 60
            grp = prod.groupby("Machine").agg(
                Used_Mins=("Elapsed",  "sum"),
                Total_Units=("Qty",    "sum"),
                SKUs_Count=("SKUCode", "nunique"),
            ).reset_index()
            # Cycles from LP allocation only (continuity doesn't track cycles)
            if not df_sched.empty:
                cyc = df_sched.groupby("Machine")["Cycles"].sum().reset_index()
                grp = grp.merge(cyc, on="Machine", how="left").fillna(0)
                grp.rename(columns={"Cycles":"Total_Cycles"}, inplace=True)
            else:
                grp["Total_Cycles"] = 0
        else:
            grp = pd.DataFrame(columns=["Machine","Used_Mins","Total_Units","SKUs_Count","Total_Cycles"])
        df_u = pd.DataFrame({"Machine": all_machines}).merge(grp, on="Machine", how="left").fillna(0)
        df_u["Available_Mins"]  = self.avail_mins
        df_u["Idle_Mins"]       = self.avail_mins - df_u["Used_Mins"]
        df_u["Utilization_Pct"] = ((df_u["Used_Mins"] / df_u['Available_Mins']) * 100).round(2)
        return df_u[["Machine","Available_Mins","Used_Mins","Idle_Mins",
                     "Utilization_Pct","SKUs_Count","Total_Cycles","Total_Units"]]               .sort_values("Utilization_Pct", ascending=False)

    def _print_results(self, df_summary, df_util, df_shift):
        td  = df_summary["Demand"].sum()
        tp  = df_summary["Planned_Units"].sum()
        co  = (df_shift["SKUCode"]=="CHANGEOVER").sum() if not df_shift.empty else 0
        cl  = (df_shift["SKUCode"]=="MOULD_CLEAN").sum() if not df_shift.empty else 0
        print(f"\n{'='*64}")
        print(f"  Total demand    : {td:>10,.0f}")
        print(f"  Units planned   : {tp:>10,.0f}  ({tp/td*100:.1f}%)")
        print(f"  Gap             : {td-tp:>10,.0f}")
        print(f"  Avg press util  : {df_util['Utilization_Pct'].mean()}%")
        print(f"  Changeover rows : {co:>10}")
        print(f"  Mould clean rows: {cl:>10}")
        print(f"  Fully met SKUs  : {(df_summary['Status']=='FULLY MET').sum():>10}")
        print(f"  Partial SKUs    : {(df_summary['Status']=='PARTIAL').sum():>10}")
        print(f"  Unmet SKUs      : {(df_summary['Status']=='UNMET').sum():>10}")
        print(f"  Unschedulable   : {(df_summary['Status']=='UNSCHEDULABLE').sum():>10}")
        print(f"{'='*64}")

    # ── main entry point ──────────────────────────────────────────────────────
    def run(
        self,
        df_demand:    pd.DataFrame,
        df_cycles:    pd.DataFrame,
        df_allow:     pd.DataFrame,
        df_gt:        pd.DataFrame,
        mould_tracker: MouldTracker,
        df_running:   pd.DataFrame = None,
        plan_start:   datetime     = None,
    ) -> dict:

        if plan_start is None:
            plan_start = datetime.now().replace(
                hour=Config.SHIFT_START_HOUR, minute=0, second=0, microsecond=0
            )

        print("\n" + "="*64)
        print(f"  {self.BANNER_TITLE}")
        print(f"  Plan start  : {plan_start:%Y-%m-%d %H:%M}")
        print(f"  Horizon     : {Config.PLANNING_DAYS} days ({self.avail_mins:,.0f} min/press)")
        print(f"  Changeover  : {Config.CHANGEOVER_DURATION_MIN} min | "
              f"Max/shift: {Config.MAX_CHANGEOVERS_PER_SHIFT}")
        print(f"  Mould clean : {Config.CLEANING_DURATION_MIN} min every "
              f"{Config.units_per_cleaning_cycle():,} units")
        for line in self.EXTRA_BANNER:
            print(line)
        print("="*64)

        print("\n[Phase 1] Preparing SKU table...")
        df_valid, df_all, all_machines = self._prepare_skus(
            df_demand, df_cycles, df_allow, df_gt, mould_tracker, df_running
        )

        print("\n[Phase 2] Building continuity blocks...")
        continuity_rows, locked_mins, demand_remainder = self._build_continuity(
            df_running, df_valid, df_gt, plan_start
        )

        # Reduce LP demand by what continuity already covers
        # SKUs fully handled in continuity are excluded from LP entirely
        df_lp = df_valid.copy()
        for sku, remainder in demand_remainder.items():
            mask = df_lp["SKUCode"] == sku
            if remainder == 0:
                # Continuity covers all demand — remove from LP
                df_lp = df_lp[~mask]
            else:
                # Continuity covers part — LP only needs the remainder
                df_lp.loc[mask, "Demand"] = remainder
                df_lp.loc[mask, "Demand_Mins"] = (
                    math.ceil(remainder / Config.CAVITIES_PER_MOULD)
                    * df_lp.loc[mask, "CycleTime_min"].values[0]
                )
        print(f"  [{self.INPUT_TAG}] {len(df_lp)} SKUs | "
              f"Remaining demand: {df_lp['Demand'].sum():,.0f} units")

        print("\n" + self.PHASE3_HEADER)
        solver = self._build_solver()
        solver_out, meta = self._run_solver(
            solver, df_lp, all_machines, mould_tracker, locked_mins,
        )

        print("\n" + self.PHASE4_HEADER)
        extractor = self._build_extractor()
        df_mach, machine_sku_order = self._run_extractor(
            extractor, solver_out, meta, df_lp, locked_mins,
        )

        print("\n[Phase 5] Building shift-wise schedule...")
        builder  = ScheduleBuilder(plan_start, algo_label=self.ALGO_LABEL)
        df_shift = builder.build(df_mach, machine_sku_order, df_gt, continuity_rows)
        df_shift.to_excel(Config.DF_SHIFT_DEBUG_DUMP, index=False)

        df_summary = self._build_summary(df_all, df_mach, df_shift)
        df_util    = self._build_util(df_mach, df_shift, all_machines)

        self._print_results(df_summary, df_util, df_shift)


        return {
            "machine_schedule":   df_mach,
            "shift_schedule":     df_shift,
            "demand_fulfillment": df_summary,
            "machine_utilization":df_util,
            "mould_tracker":      mould_tracker.summary,
        }


# ══════════════════════════════════════════════════════════════════════════════
# ALGORITHM SUBCLASSES
# ══════════════════════════════════════════════════════════════════════════════
class JK_MILP_Curing_Scheduler_v1(JK_LP_Curing_Scheduler_v2):
    """MILP variant — integer cycles via scipy.optimize.milp (HiGHS)."""

    BANNER_TITLE  = "JK Tyre PCR Curing MILP Scheduler v1"
    ALGO_LABEL    = "MILP"
    INPUT_TAG     = "MILP Input"
    PHASE3_HEADER = "[Phase 3] Solving MILP..."
    PHASE4_HEADER = "[Phase 4] Extracting integer cycles (no rounding loss)..."

    @property
    def EXTRA_BANNER(self):
        return (
            f"  MILP limits : time={Config.MILP_TIME_LIMIT_SEC}s | "
            f"rel_gap={Config.MILP_REL_GAP}",
        )

    def _build_solver(self):
        return MILP_Solver()

    def _build_extractor(self):
        return MILP_Extractor()

    def _run_extractor(self, extractor, solver_out, meta, df_lp, locked_mins):
        # MILP variant: extractor is MILP_Extractor, method is .extract
        return extractor.extract(solver_out, meta, df_lp, locked_mins)


class JK_CPSAT_Curing_Scheduler_v1(JK_LP_Curing_Scheduler_v2):
    """CP-SAT variant — integer cycles via OR-Tools CP-SAT (parallel)."""

    BANNER_TITLE  = "JK Tyre PCR Curing CP-SAT Scheduler v1"
    ALGO_LABEL    = "CP-SAT"
    INPUT_TAG     = "CP-SAT Input"
    PHASE3_HEADER = "[Phase 3] Solving CP-SAT..."
    PHASE4_HEADER = "[Phase 4] Extracting integer cycles (no rounding loss)..."

    @property
    def EXTRA_BANNER(self):
        return (
            f"  CP-SAT      : time={Config.CPSAT_TIME_LIMIT_SEC}s | "
            f"workers={Config.CPSAT_NUM_WORKERS}",
        )

    def _build_solver(self):
        return CPSAT_Solver()

    def _build_extractor(self):
        return CPSAT_Extractor()

    def _run_extractor(self, extractor, solver_out, meta, df_lp, locked_mins):
        # CP-SAT variant: extractor is CPSAT_Extractor, method is .extract
        return extractor.extract(solver_out, meta, df_lp, locked_mins)


# Algorithm registry — used by CLI dispatch and the run_from_* routes.
SCHEDULERS = {
    "lp":    JK_LP_Curing_Scheduler_v2,
    "milp":  JK_MILP_Curing_Scheduler_v1,
    "cpsat": JK_CPSAT_Curing_Scheduler_v1,
}


# ------- CLI Execution ---------------------------------------------------------

def _parse_cli_args(argv=None):
    import argparse
    p = argparse.ArgumentParser(
        prog="V1.main",
        description="JK Tyre PCR curing scheduler — LP / MILP / CP-SAT dispatcher.",
    )
    p.add_argument(
        "--algo", choices=list(SCHEDULERS.keys()), default="lp",
        help="Optimisation algorithm (default: lp)",
    )
    p.add_argument(
        "--source", choices=["db", "excel"], default="db",
        help="Input source: live MySQL ('db', default) or input/load_*.xlsx ('excel')",
    )
    p.add_argument(
        "--demand-csv", default=str(Config.DEMAND_CSV),
        help="Path to demand CSV (default: input/Feb_CTP_PCR_Requirement.csv)",
    )
    p.add_argument(
        "--output", default=None,
        help="Output workbook path (default: derived from --algo via Config.output_file_for)",
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_cli_args()
    output_path = args.output or Config.output_file_for(args.algo, Config.PLAN_DATE)

    if args.source == "db":
        from V1.routes.run_from_database import run_from_database
        run_from_database(
            demand_csv = args.demand_csv,
            plan_start = Config.PLAN_DATE,
            algo       = args.algo,
            output_path= output_path,
        )
    else:
        from V1.routes.run_from_excel import run_from_excel
        run_from_excel(
            plan_start  = Config.PLAN_DATE,
            algo        = args.algo,
            output_path = output_path,
        )
