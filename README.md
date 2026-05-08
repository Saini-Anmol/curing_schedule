# JK Tyre BTP — PCR Curing Scheduler

> Monthly curing-press schedule optimizer for **JK Tyre & Industries Ltd. — Banmore Tyre Plant (BTP)**.
> Decides which SKU runs on which press, for how many cycles, in what order — for a 28- to 31-day horizon across ~90 presses and ~50 active SKUs.

**Optimises**: demand fulfilment (primary) + changeover count (secondary).
**Subject to**: mould availability, machine eligibility, shift caps, mould lifecycle, press continuity.

---

## Table of contents

1. [Project at a glance](#1-project-at-a-glance)
2. [Three algorithms](#2-three-algorithms)
3. [Quick start](#3-quick-start)
4. [Repo layout](#4-repo-layout)
5. [Pipeline](#5-pipeline-identical-across-all-three-approaches)
6. [Inputs](#6-inputs)
7. [Outputs](#7-outputs)
8. [Production constraints](#8-production-constraints)
9. [Configuration](#9-configuration)
10. [Branches](#10-branches)
11. [Documentation](#11-documentation)
12. [Dependencies](#12-dependencies)
13. [Troubleshooting](#13-troubleshooting)
14. [Authorship](#14-authorship)

---

## 1. Project at a glance

| | |
|---|---|
| **Plant** | Banmore Tyre Plant (BTP), JK Tyre & Industries Ltd. |
| **Scope** | PCR (Passenger Car Radial) curing department |
| **Horizon** | 28 days (Feb) / 30 days (Apr) / 31 days (Mar, May…) |
| **Capacity** | ~90 presses × 3 shifts × 8 h × 60 min = ~40,320 min/press |
| **Press model** | 2 moulds (LH+RH) per press, 2 cavities/cycle, 3,000-cycle mould life |
| **Status** | Production V1 — three solver back-ends maintained |

This repository is the **single source of truth** for the BTP PCR curing
schedule. It ingests demand, cycle times, machine eligibility, mould
inventory, GT inventory, and currently-running moulds; produces a
shift-level monthly plan as a 5-sheet Excel workbook.

---

## 2. Three algorithms

The same problem is solved with three different optimisation engines. **All three live in the `V1/` package on `main`** and produce **identical output formats** — only the math engine differs.

| Algorithm | Solver | CLI | Best for |
|---|---|---|---|
| **LP + post-rounding** | HiGHS via `scipy.optimize.linprog` | `python3 -m V1.main --algo lp` (default) | Original baseline |
| **MILP** | HiGHS via `scipy.optimize.milp` | `python3 -m V1.main --algo milp` | Provable-optimal medium |
| **CP-SAT** | Google OR-Tools | `python3 -m V1.main --algo cpsat` | **Combinatorial scheduling — fastest in practice** |

### Quick comparison

| Aspect | LP | MILP | CP-SAT |
|---|---|---|---|
| Allocation variable | continuous **minutes** | integer **cycles** | integer **cycles** |
| Assignment flag `y[s,m]` | implicit (post-hoc) | explicit binary | explicit `BoolVar` |
| Rounding step | yes (lossy floor + greedy top-up) | none | none |
| Changeover accounting | approximate (ε penalty) | exact (in capacity) | exact (in capacity) |
| Linking `c > 0 ⇒ y = 1` | n/a | BigM linearisation | reified `OnlyEnforceIf` (no BigM) |
| Parallelism | single-threaded | single-threaded | 8 workers |
| Extra dependency | — | — | `ortools` |

For the configured PCR instance (~30 SKUs after continuity × ~90 machines, ~900 eligible pairs), **CP-SAT is fastest with provable optimality**. MILP needs a top-K-presses filter (default `K=8`) to be tractable on HiGHS; without it, branch-and-bound times out on ~1000 binaries.

For deep-dives: see [`docs/LP_approach.md`](docs/LP_approach.md), [`docs/MILP_approach.md`](docs/MILP_approach.md), [`docs/CPSAT_approach.md`](docs/CPSAT_approach.md).

---

## 3. Quick start

### Install

```bash
# Common to all three algorithms
pip3 install pandas numpy openpyxl scipy sqlalchemy pymysql

# CP-SAT only
pip3 install ortools
```

### Run

From the repository root:

```bash
python3 -m V1.main                      # default: --algo lp --source db
python3 -m V1.main --algo lp            # LP + post-rounding
python3 -m V1.main --algo milp          # MILP (HiGHS)
python3 -m V1.main --algo cpsat         # CP-SAT (OR-Tools, parallel)
```

By default, the scheduler connects to MySQL using credentials in `Config`. To run **offline** against the snapshot files in `input/load_*.xlsx`:

```bash
python3 -m V1.main --algo cpsat --source excel
```

Override input/output locations via env vars:

```bash
INPUT_DIR=/data/in OUTPUT_DIR=/data/out python3 -m V1.main --algo cpsat
```

The output filename is derived per-algorithm and lands at:

```
output/CTP_PCR_Curing_<TAG>_PlanSchedule_<Month>_<YYYY-MM-DD>_<N>Days.xlsx
```

### TBR (legacy, single-file)

A separate Truck-Bus-Radial scheduler exists as a standalone script:

```bash
python3 V1/jk_curing_lp_TBR.py
```

It is LP-only, has not been ported into the V1 modular layout, and is not actively maintained.

---

## 4. Repo layout

```
.
├── V1/                          ← Source code (modular package, all 3 algos on main)
│   ├── __init__.py
│   ├── main.py                  ← CLI entry — dispatches --algo to the right solver
│   ├── config/
│   │   └── settings.py          ← Config class (all knobs + paths + env vars)
│   ├── setups/
│   │   ├── etl.py               ← ETL — DB + Excel loaders
│   │   └── mould_tracker.py     ← MouldTracker — mould compatibility/lifecycle
│   ├── solvers/
│   │   ├── lp_solver.py         ← LP_Solver + Rounder (LP-specific)
│   │   ├── milp_solver.py       ← MILP_Solver + MILP_Extractor
│   │   └── cpsat_solver.py      ← CPSAT_Solver + CPSAT_Extractor
│   ├── routes/
│   │   ├── run_from_db.py       ← run_from_database()
│   │   └── run_from_excel.py    ← run_from_excel()
│   ├── reports/
│   │   ├── schedule_builder.py  ← ScheduleBuilder (shift timeline)
│   │   └── excel_exporter.py    ← ExcelExporter (5-sheet writer)
│   ├── utilities/
│   │   └── shifts.py            ← Shift helpers
│   └── jk_curing_lp_TBR.py      ← TBR legacy monolith
│
├── input/                       ← All input data
│   ├── Feb_CTP_PCR_Requirement.csv     (demand)
│   ├── April_CTP_PCR_Requirement.csv
│   ├── daily running moulds_<month>.csv (continuity)
│   └── load_*.xlsx              (DB snapshots for offline runs)
│
├── output/                      ← All generated reports
│   └── CTP_PCR_Curing_<ALGO>_PlanSchedule_<Month>_<date>_<N>Days.xlsx
│
├── docs/                        ← Documentation
│   ├── README.md                (docs index)
│   ├── PRD.md                   (Product Requirements Document)
│   ├── TRD.md                   (Technical Requirements Document)
│   ├── LP_approach.md           (LP solver deep-dive)
│   ├── MILP_approach.md
│   └── CPSAT_approach.md
│
├── .claude/                     ← Claude Code agents (optional)
│   └── agents/
│       ├── optimization-engineer.md
│       ├── code-reviewer.md
│       ├── schedule-output-reviewer.md
│       └── codebase-architect.md
│
├── CLAUDE.md                    ← Project briefing for Claude Code sessions
└── README.md                    ← This file
```

Path resolution: `Config.INPUT_DIR` / `Config.OUTPUT_DIR` resolve to `<repo>/input` / `<repo>/output` by default; override via `INPUT_DIR` / `OUTPUT_DIR` env vars.

---

## 5. Pipeline (identical across all three approaches)

```
ETL ─→ _prepare_skus ─→ _build_continuity ─→ Solver ─→ Extractor / Rounder
                                                              │
                                          ScheduleBuilder ←───┘
                                                  │
                                        ExcelExporter (5 sheets)
```

Only the **Solver** and **Extractor** classes differ between algorithms:

| Phase | LP | MILP | CP-SAT |
|---|---|---|---|
| Solver | `LP_Solver` | `MILP_Solver` | `CPSAT_Solver` |
| Extractor | `Rounder` (~160 lines, 3 passes) | `MILP_Extractor` (~60 lines) | `CPSAT_Extractor` (~60 lines) |

Everything else (`Config`, `MouldTracker`, `ETL`, `ScheduleBuilder`, `ExcelExporter`, helper functions) is **shared infrastructure** and identical across all three algorithms.

---

## 6. Inputs

| Dataset | Source | Key columns |
|---|---|---|
| Demand | `<Month>_CTP_PCR_Requirement.csv` (DB or `input/`) | `SKUCode`, `Quantity`, `Priority` |
| Cycle times | DB table `Master_Curing_Design_CycleTime_pcr` | `SKUCode`, `CycleTime_min` |
| Machine allowable | DB table `Master_Curing_Allowable_Machines_*` | `SKUCode`, `Machines` (list) |
| GT inventory | DB table `gt_inventory_manual_pcr` | `SKUCode`, `GT_Inventory` |
| Running moulds | DB tables `Master_WC_Master` + `Daily_Running_Moulds_pcr` | `Machine`, `SKUCode`, `MouldNos`, life remaining |
| Mould master | DB table `Master_Mapping_Mould_SKU` | `MouldNo`, `Matl.Code` (SKU), Active flag |

The ETL writes Excel snapshots (`load_*.xlsx`) to `input/` after each DB load — they are reusable for offline runs.

**Cycle-time formula**:

```
CycleTime_min = round((Raw_CureTime + LOAD_UNLOAD_BUFFER_MIN) / PRESS_EFFICIENCY)
              = round((Raw + 2.3) / 0.90)
```

---

## 7. Outputs

A single 5-sheet Excel workbook per run:

| Sheet | Rows | Contents |
|---|---|---|
| **Demand Fulfillment** | ~50 SKUs | Status: FULLY MET / PARTIAL / UNMET / UNSCHEDULABLE |
| **Machine Schedule** | ~100–120 | One row per (machine, SKU) pair with cycles & minutes |
| **Shift Schedule** | ~7,000–8,000 | Full timeline incl. CHANGEOVER and MOULD_CLEAN rows |
| **Machine Utilization** | ~90 | Used / idle minutes, utilisation % |
| **Mould Tracker** | ~2,200 | Free vs assigned, life remaining |

Every sheet carries a banner with the **plan KPIs**: Demand, Planned, Gap, Fulfilment %, Avg Util %, Changeovers, Mould Cleans.

Each sheet has a 2-row title/subtitle banner before the header row — read with `pd.read_excel(..., skiprows=2)`.

---

## 8. Production constraints

All six are enforced in **every** algorithm. Where they're enforced:

| # | Constraint | Numeric | Where enforced |
|---|---|---|---|
| 1 | Changeover time per SKU switch | 360 min | LP: in `Rounder` post-solve. MILP/CP-SAT: in capacity constraint |
| 2 | Mould cleaning every `NEW_MOULD_LIFE × CAVITIES × MOULDS_PER_PRESS` units | 180 min / 6,000 units | `ScheduleBuilder._split_block` |
| 3 | Minimise # changeovers | secondary objective | LP: ε soft penalty. MILP/CP-SAT: explicit `Σ y[s,m]` term |
| 4 | Press continuity (currently running moulds) | first-class | `_build_continuity` (Phase 2, before solver) |
| 5 | Max changeovers per shift, plant-wide | 3 | `ScheduleBuilder._next_co_slot` |
| 6 | Mould-SKU compatibility, ≥ 2 free moulds per assignment | hard | `MouldTracker.get_eligible_machines_with_moulds` |

---

## 9. Configuration

All knobs live in [`V1/config/settings.py`](V1/config/settings.py) under a single `Config` class.

### Common (all algorithms)

| Setting | Default | Purpose |
|---|---|---|
| `PLANNING_DAYS` | 28 | Horizon length |
| `SHIFTS_PER_DAY` | 3 | 8-hour shifts |
| `CHANGEOVER_DURATION_MIN` | 360 | Per SKU switch |
| `CLEANING_DURATION_MIN` | 180 | Mould cleaning |
| `MAX_CHANGEOVERS_PER_SHIFT` | 3 | Plant-wide cap |
| `CHANGEOVER_PENALTY_WEIGHT` | 0.01 | ε in objective |
| `PLAN_DATE` | 2026-02-01 07:00 | Schedule start |
| `INPUT_DIR` / `OUTPUT_DIR` | `<repo>/input`, `<repo>/output` | Override via env vars |

### MILP-specific

| Setting | Default |
|---|---|
| `MILP_TIME_LIMIT_SEC` | 600 |
| `MILP_REL_GAP` | 0.05 |
| `MILP_TOP_K_PRESSES_PER_SKU` | 8 (the key speedup; 0 disables) |
| `MILP_SHOW_PROGRESS` | True |

### CP-SAT-specific

| Setting | Default |
|---|---|
| `CPSAT_TIME_LIMIT_SEC` | 300 |
| `CPSAT_NUM_WORKERS` | 8 (parallel search) |
| `CPSAT_W_SLACK` | 1_000 (slack-vs-changeover weight) |

To re-run for a different month, edit `Config.PLAN_DATE` and `Config.PLANNING_DAYS`, drop the new demand CSV into `input/`, and update the demand path in the entry point.

---

## 10. Branches

| Branch | Contains | Status |
|---|---|---|
| `main` | All three algorithms in V1 package + docs + agents | **Active** — all new work happens here |
| `MILP_approach` | Original single-file MILP monolith (`jk_curing_milp_PCR.py`) | Reference only — not maintained against V1 layout |
| `CP_SAT_approach` | Original single-file CP-SAT monolith (`jk_curing_cpsat_PCR.py`) | Reference only — not maintained against V1 layout |

The legacy branches preserve the original side-by-side structure for historical comparison. **All new development happens on `main`** against the V1 modular layout.

---

## 11. Documentation

Two layers — read them in this order:

### Layer 1 — Specification (start here)

| Document | Purpose |
|---|---|
| [`docs/PRD.md`](docs/PRD.md) | **Product Requirements** — objectives, scope, stakeholders, use cases, KPIs, constraints |
| [`docs/TRD.md`](docs/TRD.md) | **Technical Requirements** — architecture, data model, algorithm specs, module structure |

### Layer 2 — Per-algorithm deep-dives

| Document | Topic |
|---|---|
| [`docs/LP_approach.md`](docs/LP_approach.md) | LP formulation, Rounder, full pipeline |
| [`docs/MILP_approach.md`](docs/MILP_approach.md) | MILP formulation, BigM, top-K filter, HiGHS timeout handling |
| [`docs/CPSAT_approach.md`](docs/CPSAT_approach.md) | CP-SAT formulation, reified linking, parallel search |

For Claude Code / AI-agent sessions: see [`CLAUDE.md`](CLAUDE.md) for the concise project briefing that auto-loads on session start.

---

## 12. Dependencies

### Required

```
python>=3.9
pandas>=2.0
numpy>=1.24
scipy>=1.13           # provides scipy.optimize.milp (used by MILP)
openpyxl>=3.1
sqlalchemy>=2.0
pymysql>=1.4
```

### CP-SAT only

```
ortools>=9.10
```

### macOS note

The project assumes Python 3.9 from system / Command Line Tools (`/usr/bin/python3`). Use `pip3` (not `pip`) on macOS. User-installed binaries land at `~/Library/Python/3.9/bin/`, which isn't on `$PATH` by default — invoke them via `python3 -m <tool>`:

```bash
python3 -m streamlit ...        # not just `streamlit ...`
python3 -m pytest ...
```

---

## 13. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `RuntimeError: MILP did not converge` | scipy 1.9+ returns `success=False` on time-limit even with usable incumbent | Already fixed — solver accepts incumbent on timeout. If you see this, pull latest from `main`. |
| MILP hangs > 10 min in Phase 3 | 904 binaries is at HiGHS's MIP edge | Lower `MILP_TOP_K_PRESSES_PER_SKU` (try 5) or raise `MILP_REL_GAP` to 0.10 |
| `FileNotFoundError: 'Feb_CTP_PCR_Requirement.csv'` | Running scheduler from wrong CWD | Run from repo root: `python3 -m V1.main`, not from inside `V1/` |
| `command not found: streamlit` | `pip3 install --user` puts binaries off `$PATH` | Use `python3 -m streamlit ...` |
| Empty Excel output | All SKUs marked UNSCHEDULABLE | Check `input/load_running_moulds.xlsx` and `input/load_mould_master.xlsx` for compatibility gaps |
| Excel banner shows fulfilment < 90% | Capacity-constrained instance | Run `schedule-output-reviewer` agent to identify root cause (eligibility, mould compatibility, or genuine capacity gap) |

---

## 14. Authorship

| | |
|---|---|
| **Designed by** | Paranjay Dodiya |
| **Organisation** | Algo8 AI Pvt. Ltd. |
| **Client** | JK Tyre & Industries Ltd. — Banmore Tyre Plant (BTP) |
| **Tyre type** | PCR (Passenger Car Radial) |
| **Version** | V1 — Production |
| **Status** | Active maintenance |

This system replaces heuristic monthly planning with a solver-backed scheduler that meets demand more reliably and uses fewer changeovers — see [`docs/PRD.md`](docs/PRD.md) for the full business case.
