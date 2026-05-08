# CLAUDE.md

Project briefing for Claude Code working in this repository. Keep this concise — deep technical details live in [`docs/`](docs/).

---

## 1. What this project is

Monthly curing-press scheduler for **JK Tyre's Banmore plant (BTP)**. Decides which SKU runs on which press, for how many cycles, in what order — for a 28- or 31-day horizon across ~90 presses and ~50 active SKUs. Optimises **demand fulfilment** (primary) and **changeover count** (secondary), subject to mould availability and shift constraints.

Currently scopes **PCR** (Passenger Car Radial). Companion TBR file exists on `main` only (`jk_curing_lp_TBR.py`).

---

## 2. Three algorithm branches

The same problem is solved with three different optimisation approaches, each on its own branch. **Main branch is LP**; the other two are isolated experiments.

| Branch | Solver | Main file (PCR) | Status |
|---|---|---|---|
| `main` | LP + post-rounding (HiGHS via `scipy.optimize.linprog`) | [`btp/Curing/V1 11-37-56-875/jk_curing_lp_PCR.py`](btp/Curing/V1%2011-37-56-875/jk_curing_lp_PCR.py) | Original baseline |
| `MILP_approach` | MILP (HiGHS via `scipy.optimize.milp`) | `btp/Curing/V1 11-37-56-875/jk_curing_milp_PCR.py` | Drop-in replacement, no rounding loss |
| `CP_SAT_approach` | CP-SAT (Google OR-Tools) | `btp/Curing/V1 11-37-56-875/jk_curing_cpsat_PCR.py` | Fastest in practice, parallel search |

**All three produce the same 5-sheet Excel output** — only the algorithm differs.

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

**Critical:** scripts use *relative paths* for input CSVs/XLSX, so always `cd` into the script's folder first:

```bash
cd "btp/Curing/V1 11-37-56-875"
python3 jk_curing_lp_PCR.py        # LP (main)
# or
python3 jk_curing_milp_PCR.py      # MILP (after git checkout MILP_approach)
# or
python3 jk_curing_cpsat_PCR.py     # CP-SAT (after git checkout CP_SAT_approach)
```

Each script's `if __name__ == "__main__"` calls `run_from_database(...)` by default — connects to MySQL using credentials in `Config`. To run offline, swap to the commented-out `run_from_excel(...)` block and point at `load_*.xlsx`.

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

All knobs are in the `Config` class at the top of each scheduler file. Common ones:

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

```
.
├── btp/Curing/V1 11-37-56-875/   ← code lives here (all branches)
│   ├── jk_curing_lp_PCR.py        (main branch only — but file structure same on all)
│   ├── jk_curing_lp_TBR.py        (main only — TBR variant)
│   ├── jk_curing_milp_PCR.py      (MILP_approach only)
│   ├── jk_curing_cpsat_PCR.py     (CP_SAT_approach only)
│   ├── load_*.xlsx                (snapshots for offline runs)
│   ├── Feb_/Mar_*.csv             (demand inputs)
│   ├── daily running moulds_*.csv (continuity input)
│   └── CTP_*_PlanSchedule_*.xlsx  (generated outputs)
├── docs/
│   ├── README.md                  (index + comparison)
│   ├── LP_approach.md             (full LP doc)
│   ├── MILP_approach.md
│   └── CPSAT_approach.md
├── README.md                      (top-level project README)
└── CLAUDE.md                      (this file)
```

---

## 13. Working conventions

- **Don't put MILP/CP-SAT code on `main`** — keep them isolated on their own branches.
- The LP `Rounder` is heuristic (3 passes). MILP/CP-SAT replace it with a tiny `Extractor` that just reads integer values out of the solution. Don't reintroduce rounding logic to the latter two.
- **Folder name has a space**: `V1 11-37-56-875`. Always quote it in shell: `cd "V1 11-37-56-875"`. Tab-completion handles this automatically.
- The `df_shiftv1.xlsx` debug dump is written by the orchestrator (`scheduler.run()` line ~1470 in each variant); harmless, just a side-effect.

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
- Existing scheduler outputs: `btp/Curing/V1 11-37-56-875/CTP_*_PlanSchedule_*.xlsx`
