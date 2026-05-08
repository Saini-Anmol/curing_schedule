"""
JK Tyre BTP — PCR Curing LP Scheduler v2 (modular package)
==========================================================
Run from the repo root with:

    python -m V1.main

Public entry points (import directly to avoid sub-import side effects):

    from V1.routes.run_from_database import run_from_database
    from V1.routes.run_from_excel    import run_from_excel

Internal layout:
    config/      — Config (knobs + paths)
    setups/      — ETL, MouldTracker
    solvers/     — LP_Solver, Rounder
    reports/     — ScheduleBuilder, ExcelExporter
    routes/      — run_from_database, run_from_excel (entry points)
    utilities/   — shift helpers
    main.py      — orchestrator + CLI (`python -m V1.main`)
"""
