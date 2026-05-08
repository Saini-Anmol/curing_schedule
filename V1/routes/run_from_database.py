"""
Default entry point: connects to MySQL using credentials in Config and runs
the full scheduler. Writes the 5-sheet Excel workbook to OUTPUT_DIR.

Algorithm dispatch
------------------
Pass ``algo="lp"`` (default), ``"milp"`` or ``"cpsat"`` to pick the solver.
The scheduler class, ExcelExporter title strings, and ScheduleBuilder
production-row Remarks all flow from the same algo label.
"""

from datetime import datetime

try:
    from sqlalchemy import create_engine
except ImportError:
    create_engine = None

from V1.config.settings import Config
from V1.reports.excel_exporter import ExcelExporter
from V1.setups.etl import ETL
from V1.setups.mould_tracker import MouldTracker


def run_from_database(
    demand_csv:   str,
    plan_start:   datetime = None,
    tyre_type:    str = Config.TYRE_TYPE,
    output_path:  str = None,
    algo:         str = "lp",
) -> dict:
    if create_engine is None:
        raise ImportError("sqlalchemy not installed. Use run_from_excel() instead.")
    # Imported here to avoid a circular import at module load
    # (V1.main imports from V1.routes via __main__ only).
    from V1.main import SCHEDULERS

    if algo not in SCHEDULERS:
        raise ValueError(f"Unknown algo '{algo}'. Choose from {list(SCHEDULERS)}.")

    output_path = output_path or Config.output_file_for(algo, plan_start)
    algo_label = Config.ALGO_LABEL[algo]

    engine = create_engine(
        f"mysql+pymysql://{Config.DB_USER}:{Config.DB_PASSWORD}"
        f"@{Config.DB_SERVER}/{Config.DB_NAME}"
    )
    etl = ETL(engine, tyre_type)
    print("\n[Phase 0] ETL from database...")
    df_demand  = etl.load_demand(demand_csv)
    df_cycles  = etl.load_cycle_times()
    df_allow   = etl.load_machine_allowable()
    df_gt      = etl.load_gt_inventory()
    df_running = etl.load_running_moulds()
    df_mould_m = etl.load_mould_master()

    tracker = MouldTracker()
    tracker.load_from_df(df_mould_m, df_running)

    scheduler = SCHEDULERS[algo]()
    results   = scheduler.run(df_demand, df_cycles, df_allow, df_gt,
                              tracker, df_running, plan_start)
    ExcelExporter(output_path, algo_label=algo_label).export(results)
    return results
