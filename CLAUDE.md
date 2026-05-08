# CLAUDE.md

Project briefing for Claude Code working in this repository. Keep this concise — deep technical details live in [`docs/`](docs/).

---

## 1. What this project is

Monthly curing-press scheduler for **JK Tyre's Banmore plant (BTP)**. Decides which SKU runs on which press, for how many cycles, in what order — for a 28- or 31-day horizon across ~90 presses and ~50 active SKUs. Optimises **demand fulfilment** (primary) and **changeover count** (secondary), subject to mould availability and shift constraints.

Currently scopes **PCR** (Passenger Car Radial). Companion TBR file exists on `main` only (`jk_curing_lp_TBR.py`).

---

## 2. Three algorithms (consolidated on `main`)

The same problem is solved with three different optimisation approaches. As of
the 2026 V1 restructure, **all three live on `main` inside the `V1/` package**;
the legacy branches `MILP_approach` and `CP_SAT_approach` retain the original
single-file monoliths for reference but have not been ported.

| Algorithm | Solver | Entry point | Status |
|---|---|---|---|
| LP + post-rounding | HiGHS via `scipy.optimize.linprog` | `python -m V1.main --algo lp` (default) | Original baseline |
| MILP | HiGHS via `scipy.optimize.milp` | `python -m V1.main --algo milp` | Drop-in replacement, no rounding loss |
| CP-SAT | Google OR-Tools | `python -m V1.main --algo cpsat` | Fastest in practice, parallel search |

**All three produce the same 5-sheet Excel output** — only the algorithm differs.

The legacy single-file versions (`jk_curing_milp_PCR.py`, `jk_curing_cpsat_PCR.py`) still exist on the `MILP_approach` and `CP_SAT_approach` branches respectively; they are not maintained against the V1 layout.

For deep-dive technical documentation:
- [`docs/LP_approach.md`](docs/LP_approach.md)
- [`docs/MILP_approach.md`](docs/MILP_approach.md)
- [`docs/CPSAT_approach.md`](docs/CPSAT_approach.md)

---

## 3. Quick comparison

| Aspect | LP | MILP | CP-SAT |
|---|---|---|---|
| Allocation variable | continuous **minutes** | integer **cycles** | integer **cycles** |
| Assignment flag `y[s,m]` | implicit (post-hoc) | explicit binary | explicit `BoolVar` |
| Rounding step | yes (lossy floor + greedy top-up) | none | none |
| Changeover accounting | approximate (ε penalty) | exact (in capacity constraint) | exact (in capacity constraint) |
| Linking `c > 0 ⇒ y = 1` | n/a | BigM linearisation | reified `OnlyEnforceIf` (no BigM) |
| Parallelism | single-threaded | single-threaded | 8 workers |
| Extra dependency | — | — | `ortools` |
| Best for | small / quick baseline | provable-optimal medium | **combinatorial scheduling** |

For the configured PCR instance (~30 SKUs after continuity × ~90 machines, ~900 eligible pairs), CP-SAT is fastest with provable optimality. MILP needs a top-K-presses filter to be tractable on HiGHS; without it, branch-and-bound times out on ~1000 binaries.

---

## 4. Pipeline (identical across all three approaches)

```
ETL ─→ _prepare_skus ─→ _build_continuity ─→ Solver ─→ Extractor/Rounder
                                                              │
                                          ScheduleBuilder ←───┘
                                                  │
                                        ExcelExporter (5 sheets)
```

Only the **Solver** and **Extractor** classes differ between branches:

| Phase | LP version | MILP version | CP-SAT version |
|---|---|---|---|
| Solver | `LP_Solver` | `MILP_Solver` | `CPSAT_Solver` |
| Extractor | `Rounder` (~160 lines, 3 passes) | `MILP_Extractor` (~60 lines) | `CPSAT_Extractor` (~60 lines) |

Everything else (`Config`, `MouldTracker`, `ETL`, `ScheduleBuilder`, `ExcelExporter`, helper functions) is **identical and unchanged** across branches.

---

## 5. Inputs (loaded by `ETL`)

| Dataset | Source | Key columns |
|---|---|---|
| Demand | `Feb_CTP_PCR_Requirement.csv` (DB or local) | `SKUCode`, `Quantity`, `Priority` |
| Cycle times | DB table `Master_Curing_Design_CycleTime_pcr` | `SKUCode`, `CycleTime_min` |
| Machine allowable | DB table `Master_Curing_Allowable_Machines_*` | `SKUCode`, `Machines` (list) |
| GT inventory | DB table `gt_inventory_manual_pcr` | `SKUCode`, `GT_Inventory` |
| Running moulds | DB tables `Master_WC_Master` + `Daily_Running_Moulds_pcr` | `Machine`, `SKUCode`, `MouldNos`, life |
| Mould master | DB table `Master_Mapping_Mould_SKU` | `MouldNo`, `Matl.Code` (SKU), Active |

`ETL` writes Excel snapshots (`load_*.xlsx`) of each loader for offline/testing use.

Cycle-time formula:
```
CycleTime_min = round((Raw_CureTime + LOAD_UNLOAD_BUFFER_MIN) / PRESS_EFFICIENCY)
              = round((Raw + 2.3) / 0.90)
```

---

## 6. Production constraints (all 6 enforced in every branch)

| # | Constraint | Where enforced |
|---|---|---|
| 1 | Changeover = 360 min per SKU switch | LP: in `Rounder` post-solve. MILP/CP-SAT: in capacity constraint. |
| 2 | Mould cleaning = 180 min every 6,000 units (`NEW_MOULD_LIFE × CAVITIES × MOULDS_PER_PRESS`) | `ScheduleBuilder._split_block` |
| 3 | Minimise # changeovers | LP: ε soft penalty per active pair. MILP/CP-SAT: explicit `Σ y[s,m]` term in objective |
| 4 | Press continuity (currently running moulds) | `_build_continuity` (Phase 2, before solver) |
| 5 | Max changeovers per shift = 3 plant-wide | `ScheduleBuilder._next_co_slot` |
| 6 | Mould-SKU compatibility, ≥ 2 free moulds per assignment | `MouldTracker.get_eligible_machines_with_moulds` |

---

## 7. Press model

- 90 presses, each with 2 moulds (LH + RH)
- 2 cavities/cycle → each cycle produces 2 tyres
- Mould life: 3,000 cycles before cleaning
- Shifts: A (07–15), B (15–23), C (23–07)
- Horizon: 28 days × 3 shifts × 8 h × 60 min = **40,320 min/press**
- Effective capacity = 40,320 − `locked_mins[m]` (locked by continuity)

---

## 8. Outputs

A single 5-sheet Excel workbook (`CTP_PCR_Curing_<ALGO>_v*_PlanSchedule_*.xlsx`):

| Sheet | Rows | Contents |
|---|---|---|
| Demand Fulfillment | ~50 SKUs | Status: FULLY MET / PARTIAL / UNMET / UNSCHEDULABLE |
| Machine Schedule | ~100–120 | One row per (machine, SKU) pair with cycles & minutes |
| Shift Schedule | ~7,000–8,000 | Full timeline incl. CHANGEOVER and MOULD_CLEAN rows |
| Machine Utilization | ~90 | Used / idle minutes, utilisation % |
| Mould Tracker | ~2,200 | Free vs assigned, life remaining |

KPI banner on every sheet: Demand, Planned, Gap, Fulfilment %, Avg Util %, Changeovers, Mould Cleans.

---

## 9. How to run

All three PCR algorithms dispatch through a single CLI in the V1 package. Run from the repo root:

```bash
python3 -m V1.main                              # default: --algo lp
python3 -m V1.main --algo lp                    # LP + post-rounding
python3 -m V1.main --algo milp                  # MILP (HiGHS)
python3 -m V1.main --algo cpsat                 # CP-SAT (OR-Tools, parallel)
python3 V1/jk_curing_lp_TBR.py                  # TBR (sibling monolith — LP only, deferred)
```

By default each algorithm uses `run_from_database` (connects to MySQL using credentials in `Config`). To run offline against the input/load_*.xlsx snapshots:

```bash
python3 -m V1.main --algo milp --source excel
```

Override input/output locations via env vars (paths default to `<repo>/input` and `<repo>/output`):

```bash
INPUT_DIR=/data/in OUTPUT_DIR=/data/out python3 -m V1.main --algo cpsat
```

The output filename is derived per-algorithm from `Config.output_file_for(algo)` and lands under `OUTPUT_DIR/CTP_PCR_Curing_<TAG>_PlanSchedule_<Month>_<YYYY-MM-DD>_<N>Days.xlsx`.

The legacy single-file MILP/CP-SAT scripts still exist on the `MILP_approach` and `CP_SAT_approach` branches but have not been ported to V1.

---

## 10. Dependencies

Common (all branches):
```
pip3 install pandas numpy openpyxl scipy sqlalchemy pymysql
```

CP-SAT only:
```
pip3 install ortools
```

On macOS the user runs `pip3` (not `pip`); user-installed binaries land in `~/Library/Python/3.9/bin` which is not on default PATH — use `python3 -m <tool>` to invoke them.

---

## 11. Configuration

All knobs live in the single `Config` class in [`V1/config/settings.py`](V1/config/settings.py). Common ones:

| Setting | Default | Purpose |
|---|---|---|
| `PLANNING_DAYS` | 28 | Horizon |
| `CHANGEOVER_DURATION_MIN` | 360 | Per SKU switch |
| `CLEANING_DURATION_MIN` | 180 | Mould cleaning |
| `MAX_CHANGEOVERS_PER_SHIFT` | 3 | Plant-wide cap |
| `CHANGEOVER_PENALTY_WEIGHT` | 0.01 | ε in objective |
| `PLAN_DATE` | 2026-02-01 07:00 | Schedule start |

MILP-only:
| Setting | Default |
|---|---|
| `MILP_TIME_LIMIT_SEC` | 600 |
| `MILP_REL_GAP` | 0.05 |
| `MILP_TOP_K_PRESSES_PER_SKU` | 8 (the key speedup; set 0 to disable) |
| `MILP_SHOW_PROGRESS` | True (streams HiGHS log) |

CP-SAT-only:
| Setting | Default |
|---|---|
| `CPSAT_TIME_LIMIT_SEC` | 300 |
| `CPSAT_NUM_WORKERS` | 8 (parallel search) |
| `CPSAT_W_SLACK` | 1_000 (slack-vs-changeover weight) |

---

## 12. Repo structure

**Main branch** — single modular package hosting all three PCR algorithms:

```
.
├── V1/                            ← consolidated source (LP + MILP + CP-SAT)
│   ├── __init__.py
│   ├── main.py                    (CLI dispatcher: `python -m V1.main --algo {lp,milp,cpsat}`
│   │                               + JK_LP_Curing_Scheduler_v2 base
│   │                               + JK_MILP_Curing_Scheduler_v1
│   │                               + JK_CPSAT_Curing_Scheduler_v1)
│   ├── config/
│   │   └── settings.py            (Config: knobs incl. MILP_/CPSAT_ options,
│   │                               paths, env-var overrides, ALGO_LABEL,
│   │                               output_file_for(algo) helper)
│   ├── setups/
│   │   ├── etl.py                 (DB + Excel loaders — shared by all algos)
│   │   └── mould_tracker.py       (mould availability ledger — shared)
│   ├── solvers/
│   │   ├── lp_solver.py           (LP_Solver — scipy.linprog)
│   │   ├── rounder.py             (Rounder — LP-only float→int + top-up)
│   │   ├── milp_solver.py         (MILP_Solver — scipy.optimize.milp / HiGHS)
│   │   ├── milp_extractor.py      (MILP_Extractor — no rounding)
│   │   ├── cpsat_solver.py        (CPSAT_Solver — OR-Tools CP-SAT)
│   │   └── cpsat_extractor.py     (CPSAT_Extractor — no rounding)
│   ├── reports/
│   │   ├── schedule_builder.py    (ScheduleBuilder — shift timeline; algo_label
│   │   │                            param drives Remarks string)
│   │   └── excel_exporter.py      (ExcelExporter — 5-sheet workbook; algo_label
│   │                                drives sheet title strings)
│   ├── routes/
│   │   ├── run_from_database.py   (DB-backed entry; takes algo= arg)
│   │   └── run_from_excel.py      (offline / Excel entry; takes algo= arg)
│   ├── utilities/
│   │   └── shifts.py              (_get_shift_fn, con_split_into_shifts)
│   └── jk_curing_lp_TBR.py        (TBR sibling monolith — same algorithm,
│                                    different SQL tables / Config constants)
├── input/                         ← all input data
│   ├── Feb_*.csv  Mar_*.csv  May_*.csv     (demand inputs)
│   ├── Requirement(*).csv                  (legacy demand inputs)
│   ├── daily running moulds_feb.csv        (continuity reference)
│   ├── load_*.xlsx                         (DB snapshots written by ETL)
│   └── work.ipynb                          (scratch notebook)
├── output/                        ← all generated reports
│   ├── CTP_*_PlanSchedule_*.xlsx           (final 5-sheet workbook per algo)
│   └── df_shiftv1.xlsx                     (debug dump from orchestrator)
├── btp/Curing/V1/                 ← legacy directory; PCR sources now live in V1/
│                                    (MILP/CP-SAT still on their own branches as
│                                    legacy single-file experiments)
├── docs/
│   ├── README.md                  (index + comparison)
│   ├── LP_approach.md             (full LP doc)
│   ├── MILP_approach.md
│   └── CPSAT_approach.md
├── README.md                      (top-level project README)
└── CLAUDE.md                      (this file)
```

**`MILP_approach` and `CP_SAT_approach` branches** still hold the original
single-file monoliths (`jk_curing_milp_PCR.py`, `jk_curing_cpsat_PCR.py`)
under `btp/Curing/V1 11-37-56-875/` — they are kept for reference and have
*not* been restructured. New work should happen on `main` against the V1
layout.

Path resolution: `Config.INPUT_DIR` and `Config.OUTPUT_DIR` resolve to
`<repo-root>/input` and `<repo-root>/output` by default; override via the
`INPUT_DIR` / `OUTPUT_DIR` env vars.

---

## 13. Working conventions

- **Main now hosts all three PCR algorithms** in a single `V1/` package. The legacy `MILP_approach` and `CP_SAT_approach` branches are kept as monolith experiments for historical reference but should not receive new work.
- The LP `Rounder` is heuristic (3 passes). MILP/CP-SAT replace it with a tiny `Extractor` that just reads integer values out of the solution. Don't reintroduce rounding logic to the latter two.
- Algorithm dispatch is via `python -m V1.main --algo {lp,milp,cpsat}` from the repo root. Default is `lp`. Inputs live in `input/`, outputs in `output/` (override via `INPUT_DIR` / `OUTPUT_DIR` env vars).
- The `JK_LP_Curing_Scheduler_v2` class is the parametric base; `JK_MILP_Curing_Scheduler_v1` and `JK_CPSAT_Curing_Scheduler_v1` subclass it and override only the solver/extractor build hooks plus banner strings. Phases 1, 2, 5 are shared across all three.
- `ScheduleBuilder(plan_start, algo_label=...)` and `ExcelExporter(path, algo_label=...)` accept an algo label; defaults to `"LP"` for back-compat. The label drives the production-row Remarks string and the four sheet title strings, matching the legacy monolith outputs byte-for-byte.
- The `df_shiftv1.xlsx` debug dump is written by the orchestrator (`scheduler.run()` in each variant) into `output/`; harmless, just a side-effect.

---

## 14. Known issues / quirks

- HiGHS MIP is the slowest of the three solvers; without the top-K filter (or `MILP_TOP_K_PRESSES_PER_SKU = 0`) it can hang on ~900 binaries for 10+ min.
- `scipy.optimize.milp` returns `success=False` on time-limit even when it found a usable incumbent — handle by checking `result.x is not None` first.
- LP's `Rounder` priority top-up is greedy and may not be globally optimal even relative to the LP's own solution.
- `Config.PLAN_DATE` must align with the demand CSV's plan month (e.g. Feb 2026 demand → Feb start date).

---

## 15. Pointers

- Full per-approach docs: [`docs/`](docs/)
- Top-level README: [`README.md`](README.md)
- Existing scheduler outputs: `output/CTP_*_PlanSchedule_*.xlsx`
