"""
Offline / testing entry point: runs the full scheduler from Excel snapshots
(``input/load_*.xlsx``) instead of querying the MySQL database.

Algorithm dispatch
------------------
Pass ``algo="lp"`` (default), ``"milp"`` or ``"cpsat"`` to pick the solver.
"""

from datetime import datetime

from V1.config.settings import Config
from V1.reports.excel_exporter import ExcelExporter
from V1.setups.etl import ETL
from V1.setups.mould_tracker import MouldTracker


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINTS
# ══════════════════════════════════════════════════════════════════════════════
def run_from_excel(
    demand_path:  str = None,
    cycles_path:  str = None,
    allow_path:   str = None,
    gt_path:      str = None,
    mould_path:   str = None,
    running_path: str = None,
    plan_start:   datetime = None,
    output_path:  str = None,
    algo:         str = "lp",
) -> dict:
    """
    Default paths point at the Excel snapshots produced by an earlier
    ``run_from_database`` call (i.e. the ``input/load_*.xlsx`` files written
    by ETL). Pass overrides for ad-hoc data sets.
    """
    from V1.main import SCHEDULERS  # local import to avoid circularity

    if algo not in SCHEDULERS:
        raise ValueError(f"Unknown algo '{algo}'. Choose from {list(SCHEDULERS)}.")

    demand_path = demand_path or Config.LOAD_DEMAND_SNAPSHOT
    cycles_path = cycles_path or Config.LOAD_CYCLE_TIMES_SNAPSHOT
    allow_path  = allow_path  or Config.LOAD_MACHINE_ALLOWABLE_SNAPSHOT
    gt_path     = gt_path     or Config.LOAD_GT_INVENTORY_SNAPSHOT
    mould_path  = mould_path  or Config.LOAD_MOULD_MASTER_SNAPSHOT
    running_path = running_path or Config.LOAD_RUNNING_MOULDS_SNAPSHOT
    output_path = output_path or Config.output_file_for(algo, plan_start)
    algo_label = Config.ALGO_LABEL[algo]

    print("\n[Phase 0] ETL from Excel files...")
    df_demand  = ETL.load_demand_from_excel(demand_path)
    df_cycles  = ETL.load_cycle_times_from_excel(cycles_path)
    df_allow   = ETL.load_machine_allowable_from_excel(allow_path)
    df_gt      = ETL.load_gt_inventory_from_excel(gt_path)
    df_running = ETL.load_running_moulds_from_excel(running_path) if running_path else None

    tracker = MouldTracker()
    tracker.load_from_excel(mould_path, running_path)

    scheduler = SCHEDULERS[algo]()
    results   = scheduler.run(df_demand, df_cycles, df_allow, df_gt,
                              tracker, df_running, plan_start)
    ExcelExporter(output_path, algo_label=algo_label).export(results)
    return results
