# Technical Requirements Document — JK Tyre BTP PCR Curing Scheduler

| | |
|---|---|
| **Document type** | TRD (Technical Requirements / Design) |
| **System** | PCR curing-press monthly schedule generator |
| **Audience** | Engineers (human + AI agents), code reviewers, on-call SRE |
| **Companion** | [`PRD.md`](PRD.md) — what the product must do, why, for whom |
| **Status** | Active, V1 |

---

## 1. Purpose & relation to the PRD

The PRD specifies *what* the scheduler must do. This TRD specifies *how*
it does it: architecture, data model, algorithms, module layout, runtime
behaviour, and the constraints / contracts each component honours.

Anywhere this TRD conflicts with the PRD, the PRD wins.

---

## 2. System architecture

### 2.1 Component diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                          INPUT LAYER (input/)                        │
│  CSV: demand, running-moulds  •  XLSX: load_*.xlsx snapshots         │
│  DB:  MySQL (jkplanning_CTP)                                         │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    SETUPS LAYER (V1/setups/)                         │
│  ETL ─────────► loads & cleans 6 datasets, writes XLSX snapshots    │
│  MouldTracker ─► tracks compatibility, life, assignments            │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│              ROUTES / ORCHESTRATION (V1/routes/, V1/main.py)         │
│  run_from_database ──► DB-backed full pipeline                       │
│  run_from_excel    ──► Offline / testing pipeline                    │
│  CLI: python -m V1.main --algo {lp,milp,cpsat} --source {db,excel}  │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
  ┌──────────┐          ┌──────────┐          ┌──────────┐
  │ LP_Solver│          │MILP_Solver│         │CPSAT_Slvr│   (V1/solvers/)
  │  + Round │          │ + MILP_Ex │         │ + CPSAT_X│
  │ HiGHS LP │          │ HiGHS MIP │         │ OR-Tools │
  └────┬─────┘          └────┬─────┘          └────┬─────┘
       └────────────────────┬┴─────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  REPORTS LAYER (V1/reports/)                         │
│  ScheduleBuilder ─► turns allocation into shift-level timeline       │
│  ExcelExporter   ─► writes 5-sheet XLSX with KPI banner              │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      OUTPUT LAYER (output/)                          │
│  CTP_PCR_Curing_<TAG>_PlanSchedule_<Month>_<date>_<N>Days.xlsx       │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Pipeline (6 phases, identical across all 3 algorithms)

```
Phase 0: ETL                 ─ load 6 datasets
Phase 1: SKU preparation     ─ enrich, compute Demand_Mins, classify
Phase 2: Continuity          ─ lock running moulds, derive remainder
Phase 3: Solve               ─ algorithm-specific (LP/MILP/CP-SAT)
Phase 4: Extract             ─ pull integer cycles, build run order
Phase 5: Schedule build      ─ shift-level timeline with CO + cleaning
Phase 6: Excel export        ─ 5-sheet workbook
```

The **only** algorithm-specific phases are 3 and 4. All other phases are
shared infrastructure.

---

## 3. Module structure (V1 package)

```
V1/
├── __init__.py
├── main.py                       # CLI entry, dispatches to solver
├── config/
│   ├── __init__.py
│   └── settings.py               # Config class (all knobs + paths)
├── setups/
│   ├── __init__.py
│   ├── etl.py                    # ETL class (DB + Excel loaders)
│   └── mould_tracker.py          # MouldTracker class
├── solvers/
│   ├── __init__.py
│   ├── lp_solver.py              # LP_Solver + Rounder
│   ├── milp_solver.py            # MILP_Solver + MILP_Extractor
│   └── cpsat_solver.py           # CPSAT_Solver + CPSAT_Extractor
├── routes/
│   ├── __init__.py
│   ├── run_from_db.py            # run_from_database()
│   └── run_from_excel.py         # run_from_excel()
├── reports/
│   ├── __init__.py
│   ├── schedule_builder.py       # ScheduleBuilder class
│   └── excel_exporter.py         # ExcelExporter class
└── utilities/
    ├── __init__.py
    └── shifts.py                 # _get_shift_fn, con_split_into_shifts
```

### 3.1 Module ownership rules

| Module | Stable across algorithms? | Who can modify |
|---|---|---|
| `config/settings.py` | Mostly — algo-specific knobs grouped by prefix | optimization-engineer (algo knobs); codebase-architect (paths) |
| `setups/etl.py` | YES — shared | only with explicit user approval |
| `setups/mould_tracker.py` | YES — shared | only with explicit user approval |
| `solvers/*.py` | NO — algorithm-specific | optimization-engineer |
| `routes/*.py` | YES — orchestration | optimization-engineer (rare) |
| `reports/*.py` | YES — output format | only with explicit user approval (changes break Excel contract) |
| `utilities/*.py` | YES | optimization-engineer or codebase-architect |

Touching shared modules silently affects all 3 algorithms; doing so
requires explicit awareness and ideally a smoke-run on each algorithm.

---

## 4. Data model

### 4.1 Inputs

| Dataset | DB table / file | Key columns | Transformations |
|---|---|---|---|
| Demand | `Feb_CTP_PCR_Requirement.csv` | `SKUCode`, `Updated_Requirement`, `ConsolidatedPriorityScore` | groupby SKU → sum, max priority |
| Cycle times | `Master_Curing_Design_CycleTime_pcr` | `Sapcode`, `Cure Time` | `round((Raw + 2.3) / 0.90)` → int min |
| Machine allowable | `Master_Curing_Allowable_Machines_*` | `SKUCode`, machine cols | yes/no matrix → list[machine_id] |
| GT inventory | `gt_inventory_manual_pcr` | `ItemCode`, `TotalQuantity` | rename SKUCode, GT_Inventory |
| Running moulds | `Daily_Running_Moulds_pcr` + `Master_WC_Master` | `WCNAME`, `Sapcode`, `Current MouldNo`, `Mould life` | split LH/RH, group by machine |
| Mould master | `Master_Mapping_Mould_SKU` | `MouldNo`, `Matl.Code`, `Active Flag` | filter active=True |

### 4.2 Internal data structures

| Structure | Type | Purpose |
|---|---|---|
| `df_valid` | DataFrame | Schedulable SKUs after eligibility filter |
| `df_all` | DataFrame | All SKUs incl. unschedulable (for reporting) |
| `all_machines` | sorted list[str] | Universe of presses |
| `mould_tracker` | MouldTracker | Mould compatibility & assignments |
| `continuity_rows` | list[dict] | Pre-built shift rows for running moulds |
| `locked_machine_mins` | dict[machine, float] | Capacity already consumed |
| `demand_remainder` | dict[sku, int] | Residual demand after continuity |
| `df_sched` | DataFrame | Solver allocation (machine, sku, cycles) |
| `machine_sku_order` | dict[machine, list[sku]] | Run order per press |
| `df_shift` | DataFrame | Shift-level timeline (~7-8K rows) |

### 4.3 Output schema (5 sheets)

| Sheet | Columns |
|---|---|
| Demand Fulfillment | SKUCode, Priority, Demand, GT_Inventory, Planned_Units, Gap, Fulfillment_Pct, Status, CycleTime_min, Eligible_Machines, Presses_Needed, Skip_Reason |
| Machine Schedule | Machine, SKUCode, Priority, CycleTime_min, Cycles, Units_Planned, Mins_Used, Days_Used |
| Shift Schedule | Date, Shift, Machine, SKUCode, StartTime, EndTime, Qty, CycleTime_min, GT_Inventory, Remarks |
| Machine Utilization | Machine, Available_Mins, Used_Mins, Idle_Mins, Utilization_Pct, SKUs_Count, Total_Cycles, Total_Units |
| Mould Tracker | MouldNo, Compatible_SKUs, Life_Remaining, Assigned_Machine |

Each sheet has a 2-row title/subtitle banner before the header row. Read
with `pd.read_excel(..., skiprows=2)`.

---

## 5. Algorithm specifications

The same problem is solved three ways. Each is a drop-in replacement for
the others — same I/O contract.

### 5.1 LP (continuous + post-rounding)

**File**: `V1/solvers/lp_solver.py`
**Solver**: HiGHS via `scipy.optimize.linprog`

```
Variables   : x[s,m] continuous (press-minutes), slack[s] continuous
Objective   : min  Σ slack[s] + ε · Σ (x[s,m] / demand_mins[s])
Constraints : Σ_s x[s,m] ≤ eff_cap[m]
              Σ_m x[s,m] + slack[s] ≥ demand_mins[s]
              0 ≤ x[s,m] ≤ demand_mins[s]   (eligible)
              x[s,m] = 0                     (ineligible)
```

Followed by `Rounder` (3 passes: floor → CO deduction → priority top-up).

**Trade-offs**: simplest, but loses fractional cycles and approximates
changeovers. See [`LP_approach.md`](LP_approach.md).

### 5.2 MILP (integer cycles, exact CO)

**File**: `V1/solvers/milp_solver.py`
**Solver**: HiGHS via `scipy.optimize.milp`

```
Variables   : c[s,m] integer ∈ [0, BigM]
              y[s,m] binary
              slack[s] continuous ∈ [0, Demand[s]]
Objective   : min  Σ slack[s] + ε · Σ y[s,m]
Constraints : CAVITIES · Σ_m c[s,m] + slack[s] ≥ Demand[s]
              Σ_s ct[s]·c[s,m] + co · Σ_s y[s,m] ≤ cap[m] + co
              c[s,m] ≤ BigM[s,m] · y[s,m]
              BigM[s,m] = min(floor(cap[m]/ct[s]), ceil(Demand[s]/CAVITIES))
```

Followed by `MILP_Extractor` (~60 lines, no rounding).

**Trade-offs**: exact, but HiGHS MIP is slow on >1000 binaries. Top-K
filter (default K=8) shrinks problem ~5×. See [`MILP_approach.md`](MILP_approach.md).

### 5.3 CP-SAT (integer + reified linking + parallel)

**File**: `V1/solvers/cpsat_solver.py`
**Solver**: Google OR-Tools `cp_model`

```
Variables   : c[s,m] IntVar  ∈ [0, BigM]
              y[s,m] BoolVar
              slack[s] IntVar ∈ [0, Demand[s]]
Objective   : min  W_SLACK · Σ slack[s] + Σ y[s,m]    (W_SLACK = 1000)
Constraints : CAVITIES · Σ_m c[s,m] + slack[s] ≥ Demand[s]
              Σ_s ct[s]·c[s,m] + co · Σ_s y[s,m] ≤ cap[m] + co
              c[s,m] ≥ 1   .OnlyEnforceIf(y[s,m])
              c[s,m] == 0  .OnlyEnforceIf(y[s,m].Not())
```

Solver config: `max_time_in_seconds=300`, `num_search_workers=8`.

**Trade-offs**: fastest in practice, parallel, native integer; requires
`ortools` dependency. See [`CPSAT_approach.md`](CPSAT_approach.md).

### 5.4 Why three?

| | LP | MILP | CP-SAT |
|---|:---:|:---:|:---:|
| Rounding loss | yes | no | no |
| Exact CO accounting | no | yes | yes |
| Linking strength | n/a | weak (BigM) | **strong (reified)** |
| Parallelism | none | none | **8 workers** |
| Best fit | baseline | medium / proven optimal | **combinatorial scheduling** |

CP-SAT is the recommended production default for this problem class.

---

## 6. Configuration

All knobs live in `V1/config/settings.py` as a single `Config` class.

### 6.1 Common (all algorithms)

| Key | Default | Purpose |
|---|---|---|
| `PLANNING_DAYS` | 28 | Horizon length |
| `SHIFTS_PER_DAY` | 3 | 8-hour shifts |
| `HOURS_PER_SHIFT` | 8 | |
| `SHIFT_START_HOUR` | 7 | Shift A starts 07:00 |
| `CAVITIES_PER_MOULD` | 2 | Tyres per cycle |
| `MOULDS_PER_PRESS` | 2 | LH + RH |
| `NEW_MOULD_LIFE` | 3000 | Cycles before cleaning |
| `CHANGEOVER_DURATION_MIN` | 360 | |
| `CLEANING_DURATION_MIN` | 180 | |
| `LOAD_UNLOAD_BUFFER_MIN` | 2.3 | |
| `PRESS_EFFICIENCY` | 0.90 | |
| `MAX_CHANGEOVERS_PER_SHIFT` | 3 | Plant cap |
| `CHANGEOVER_PENALTY_WEIGHT` | 0.01 | ε in LP/MILP |
| `PLAN_DATE` | 2026-02-01 07:00 | |
| `INPUT_DIR` | `<repo>/input` (env: `INPUT_DIR`) | |
| `OUTPUT_DIR` | `<repo>/output` (env: `OUTPUT_DIR`) | |

### 6.2 MILP-specific

| Key | Default |
|---|---|
| `MILP_TIME_LIMIT_SEC` | 600 |
| `MILP_REL_GAP` | 0.05 |
| `MILP_TOP_K_PRESSES_PER_SKU` | 8 (0 disables) |
| `MILP_SHOW_PROGRESS` | True |

### 6.3 CP-SAT-specific

| Key | Default |
|---|---|
| `CPSAT_TIME_LIMIT_SEC` | 300 |
| `CPSAT_NUM_WORKERS` | 8 |
| `CPSAT_W_SLACK` | 1_000 |

### 6.4 DB credentials

| Key | Default |
|---|---|
| `DB_SERVER` | (set in env / Config) |
| `DB_NAME` | `jkplanning_CTP` |
| `DB_USER`, `DB_PASSWORD` | (set in env / Config) |

Credentials SHOULD eventually be moved to `.env` / secret store; today
they live in `Config`.

---

## 7. Entry points

### 7.1 CLI

```bash
python -m V1.main                         # default --algo lp --source db
python -m V1.main --algo milp             # MILP
python -m V1.main --algo cpsat            # CP-SAT
python -m V1.main --algo lp --source excel  # offline mode
```

Exit codes:
- 0 — success
- 1 — input data error
- 2 — solver infeasible (no plan possible)
- 3 — runtime error

### 7.2 Python API

```python
from V1.routes.run_from_db    import run_from_database
from V1.routes.run_from_excel import run_from_excel

results = run_from_database(
    demand_csv = "Feb_CTP_PCR_Requirement.csv",
    plan_start = datetime(2026, 2, 1, 7, 0, 0),
    algo       = "cpsat",
)
```

`results` is a dict with keys: `machine_schedule`, `shift_schedule`,
`demand_fulfillment`, `machine_utilization`, `mould_tracker`.

---

## 8. Performance targets

| Metric | Target | Measured how |
|---|---|---|
| ETL time | < 30 s | wall-clock around `ETL.load_*` |
| LP solve | < 10 s | `linprog` wall-clock |
| MILP solve (top-K=8) | < 60 s | `milp` wall-clock |
| CP-SAT solve (8 workers) | < 30 s | `solver.WallTime()` |
| Excel write | < 5 s | wall-clock around `ExcelExporter.export` |
| Total end-to-end | < 5 min | total wall-clock |

If any target is missed, log a warning and proceed; the user / on-call
agent investigates after the fact.

---

## 9. Tech stack & dependencies

### 9.1 Runtime
- Python 3.9+
- macOS 12+ / Linux

### 9.2 Required packages
```
pandas>=2.0
numpy>=1.24
scipy>=1.13          # provides scipy.optimize.milp
openpyxl>=3.1
sqlalchemy>=2.0
pymysql>=1.4
```

### 9.3 CP-SAT only
```
ortools>=9.10
```

### 9.4 Optional / dev
```
pytest               # not yet wired
streamlit, plotly    # exploratory dashboard (currently disabled)
```

---

## 10. Testing strategy

### 10.1 Smoke test (always)
After any change, run with `Config.PLANNING_DAYS = 3` and verify:
- the run completes without exception
- the output Excel exists and has 5 sheets
- KPIs are non-zero (Demand > 0, Planned > 0)

### 10.2 KPI baseline (post-refactor)
Refactors must produce **bit-identical KPIs** to the pre-refactor run.
Compare:
- Total demand, planned units, gap
- Fulfilment %
- Changeover count
- Mould-clean count

Any drift → investigate before commit.

### 10.3 Unit tests (planned, not yet present)
Highest-value targets if/when added:
- `MouldTracker.get_eligible_machines_with_moulds`
- `Rounder` floor + top-up logic
- `ScheduleBuilder._next_co_slot` plant-wide CO cap
- Cycle-time transformation

### 10.4 Constraint validation (programmatic)
A future `validate_schedule.py` SHOULD assert each of the 6 hard
constraints on the produced Excel. Until then, the
`schedule-output-reviewer` agent does this via pandas aggregates.

---

## 11. Operations & runbook

### 11.1 Standard run
```bash
cd <repo-root>
python -m V1.main --algo cpsat
```
Output: `output/CTP_PCR_Curing_CPSAT_v1_PlanSchedule_<Month>_<date>_<N>Days.xlsx`

### 11.2 If the solver times out
- LP: should never time out
- MILP: bumps `MILP_REL_GAP` or `MILP_TOP_K_PRESSES_PER_SKU` lower
- CP-SAT: rerun (parallel search may hit different incumbents); raise
  `CPSAT_TIME_LIMIT_SEC` if needed

### 11.3 If the output looks wrong
1. Run `schedule-output-reviewer` agent on the workbook
2. Check the agent's report for constraint violations or anomalies
3. If algorithmic, hand off to `optimization-engineer`
4. If structural / path-related, hand off to `codebase-architect`

### 11.4 If the run crashes
Check exit code; consult logs printed during the run. Most common:
- Input file missing or wrong path → check `INPUT_DIR`
- DB connection failure → check `DB_SERVER` / network
- `pd.read_excel` failure → input file format drift

---

## 12. Open technical questions

1. Should `Rounder` live in `solvers/` (algorithm-specific) or `utilities/`
   (shared)? Currently slated for `solvers/` since it's LP-specific.
2. Should DB credentials move to `.env` for V2?
3. Should CP-SAT use full interval-variable scheduling (replacing
   `ScheduleBuilder`) for tighter optimisation? Larger refactor.
4. Should priority weighting move into the objective (currently all
   slacks equal-weight in LP; would need refactor in all 3 solvers)?
5. Should there be a programmatic `validate_schedule.py`, or do we
   rely on the `schedule-output-reviewer` agent?

These are flagged for product/engineering decision, not blocking V1.

---

## 13. Change history

| Version | Date | Change |
|---|---|---|
| V1 | 2026-Q1 | Initial release: LP on `main`, MILP/CP-SAT on branches |
| V1.1 | 2026-Q1 | MILP timeout-incumbent + top-K filter fix |
| V1.2 | 2026-Q1 | V1/ package restructure; `python -m V1.main` CLI |

---

## 14. References

- [`PRD.md`](PRD.md) — what & why
- [`CLAUDE.md`](../CLAUDE.md) — quick briefing for AI agents
- [`LP_approach.md`](LP_approach.md), [`MILP_approach.md`](MILP_approach.md), [`CPSAT_approach.md`](CPSAT_approach.md) — solver deep-dives
