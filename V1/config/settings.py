"""
Configuration constants for the LP scheduler.

Path resolution
---------------
INPUT_DIR  / OUTPUT_DIR resolve to <repo-root>/input and <repo-root>/output by
default. Override via the INPUT_DIR / OUTPUT_DIR environment variables (useful
for tests or alternative deployments).

All input/output path constants in this module derive from these two so an
override is honoured everywhere.
"""

import os
from datetime import datetime
from pathlib import Path

# Repo root = parents[2] from this file:
#   V1/config/settings.py  ->  V1/config -> V1 -> <repo-root>
_REPO_ROOT = Path(__file__).resolve().parents[2]

INPUT_DIR  = Path(os.environ.get("INPUT_DIR",  str(_REPO_ROOT / "input")))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(_REPO_ROOT / "output")))


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════
class Config:
    # ── database ──────────────────────────────────────────────────────────────
    DB_SERVER   = "35.208.174.2"
    DB_NAME     = "jkplanning_CTP"
    DB_USER     = "root"
    DB_PASSWORD = "Dev112233"

    # ── planning horizon ──────────────────────────────────────────────────────
    PLANNING_DAYS   = 30
    SHIFTS_PER_DAY  = 3
    HOURS_PER_SHIFT = 8
    SHIFT_START_HOUR = 7          # Shift A starts at 07:00

    # ── press & mould ─────────────────────────────────────────────────────────
    CAVITIES_PER_MOULD  = 2       # tyres produced per cure cycle
    MOULDS_PER_PRESS    = 2       # LH + RH mould per press
    NEW_MOULD_LIFE      = 3000    # cycles before mould needs cleaning

    # ── downtime constants (minutes) ──────────────────────────────────────────
    CHANGEOVER_DURATION_MIN = 360  # 420 min changeover + 60 min FTC check
    CLEANING_DURATION_MIN   = 180  # mould cleaning duration
    LOAD_UNLOAD_BUFFER_MIN  = 2.3  # added to raw cure time
    PRESS_EFFICIENCY        = 0.90  # divisor for warmup/transition losses

    # ── changeover scheduling ─────────────────────────────────────────────────
    MAX_CHANGEOVERS_PER_SHIFT = 3    # plant-level cap — configurable
    CHANGEOVER_PENALTY_WEIGHT = 0.01 # LP objective penalty per active (m, s) pair
                                      # relative to demand-minutes scale
    PLAN_DATE = datetime(2026, 4, 1, 7, 0, 0)

    # ── MILP solver options ───────────────────────────────────────────────────
    # HiGHS (open-source) MIP is slower than Gurobi/CPLEX on ~1000 binaries.
    # In practice HiGHS finds a strong incumbent within seconds but then
    # spends minutes trying to *prove* optimality. We tune for "good
    # incumbent fast" rather than "provably optimal".
    MILP_TIME_LIMIT_SEC = 600        # hard wall-clock cap passed to HiGHS
    MILP_REL_GAP        = 0.05       # accept solutions within 5% of LP bound
    MILP_SHOW_PROGRESS  = True       # stream HiGHS MIP progress to stdout

    # Biggest lever: cap how many presses each SKU can go on in the MILP.
    # The SKU eligibility list from the allowable matrix is often 20-30+
    # machines, which blows the binary count up to ~1000 and makes HiGHS
    # branch-and-bound extremely slow. In practice each SKU only needs a
    # handful of presses, so we keep only the top-K by fit score
    # (score = eff_cap[m] / ct[s] = max producible cycles).
    # Set to 0 to disable the filter (use every eligible machine).
    MILP_TOP_K_PRESSES_PER_SKU = 8

    # ── CP-SAT solver options ─────────────────────────────────────────────────
    CPSAT_TIME_LIMIT_SEC = 300         # hard wall-clock cap on solve
    CPSAT_NUM_WORKERS    = 8           # parallel search workers (0 = auto)
    CPSAT_W_SLACK        = 1_000       # weight on slack in objective
                                        # (slack dominates y count by 1000x)

    # ── output ────────────────────────────────────────────────────────────────
    TYRE_TYPE   = "pcr"

    # ── paths (resolved relative to INPUT_DIR / OUTPUT_DIR) ──────────────────
    INPUT_DIR  = INPUT_DIR
    OUTPUT_DIR = OUTPUT_DIR

    # Default demand CSV path used by the CLI / __main__ block. Other input
    # filenames are passed in by the caller (see ETL).
    DEMAND_CSV = INPUT_DIR / "Apr_CTP_PCR_Requirement.csv"

    OUTPUT_FILE = str(
        OUTPUT_DIR / f"CTP_PCR_Curing_LP_v4_PlanSchedule_April_{PLAN_DATE.date()}_30Days.xlsx"
    )

    # Per-algorithm output filename tags (used by main.py / routes when --algo
    # is supplied). LP keeps its v4 tag for backwards compatibility; MILP and
    # CP-SAT match the legacy monolith filenames so existing artefacts stay
    # bit-equivalent.
    ALGO_OUTPUT_TAG = {
        "lp":    "LP_v4",
        "milp":  "MILP_v1",
        "cpsat": "CPSAT_v1",
    }
    # Algorithm label used in workbook title strings + ScheduleBuilder Remarks.
    ALGO_LABEL = {
        "lp":    "LP",
        "milp":  "MILP",
        "cpsat": "CP-SAT",
    }

    @classmethod
    def output_file_for(cls, algo: str, plan_start: datetime = None,
                        month_label: str = "April") -> str:
        """
        Build the output workbook path for a given algorithm, mirroring the
        legacy monolith filename convention:
            CTP_PCR_Curing_<TAG>_PlanSchedule_<Month>_<YYYY-MM-DD>_<N>Days.xlsx
        """
        plan_start = plan_start or cls.PLAN_DATE
        tag = cls.ALGO_OUTPUT_TAG[algo]
        days = cls.PLANNING_DAYS
        return str(
            cls.OUTPUT_DIR
            / f"CTP_PCR_Curing_{tag}_PlanSchedule_{month_label}_"
              f"{plan_start.date()}_{days}Days.xlsx"
        )

    # Snapshot files written by ETL during DB-backed runs (offline replay).
    LOAD_DEMAND_SNAPSHOT            = str(INPUT_DIR / "load_demand.xlsx")
    LOAD_CYCLE_TIMES_SNAPSHOT       = str(INPUT_DIR / "load_cycle_times.xlsx")
    LOAD_MACHINE_ALLOWABLE_SNAPSHOT = str(INPUT_DIR / "load_machine_allowable.xlsx")
    LOAD_GT_INVENTORY_SNAPSHOT      = str(INPUT_DIR / "load_gt_inventory.xlsx")
    LOAD_RUNNING_MOULDS_SNAPSHOT    = str(INPUT_DIR / "load_running_moulds.xlsx")
    LOAD_MOULD_MASTER_SNAPSHOT      = str(INPUT_DIR / "load_mould_master.xlsx")

    # Debug dump from the orchestrator (preserved side-effect).
    DF_SHIFT_DEBUG_DUMP = str(OUTPUT_DIR / "df_shiftv1.xlsx")

    @classmethod
    def avail_mins(cls) -> float:
        return cls.PLANNING_DAYS * cls.SHIFTS_PER_DAY * cls.HOURS_PER_SHIFT * 60

    @classmethod
    def units_per_cleaning_cycle(cls) -> int:
        """Units produced before a full press cleaning is needed."""
        return cls.NEW_MOULD_LIFE * cls.CAVITIES_PER_MOULD * cls.MOULDS_PER_PRESS
