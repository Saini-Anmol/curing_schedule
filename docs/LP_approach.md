# LP Approach — PCR Curing Schedule

**File**: [`jk_curing_lp_PCR.py`](../btp/Curing/V1%2011-37-56-875/jk_curing_lp_PCR.py)
**Branch**: `main`
**Solver**: HiGHS via `scipy.optimize.linprog`

This document describes the original scheduler that uses continuous Linear
Programming for allocation, with a post-solve rounding pass to obtain
integer production cycles.

---

## 1. Problem in one paragraph

JK Tyre's Banmore plant has ~90 curing presses that must produce a monthly
(28-day or 31-day) demand across ~50 active PCR SKUs. Each SKU has a fixed
cycle time per mould, each press has physical allowability constraints, the
mould pool is finite, and every SKU switch on a press costs 360 minutes of
changeover plus a mould cleaning every 6,000 units. The scheduler decides
**which SKU runs on which press, for how many cycles, in what order**,
minimising unmet demand and changeover count.

---

## 2. Data inputs

| Dataset | Loaded by | Rows (typical) | Key columns |
|---|---|---|---|
| Demand | `ETL.load_demand` | ~50 SKUs | `SKUCode`, `Quantity`, `Priority` |
| Cycle times | `ETL.load_cycle_times` | ~220 SKUs | `SKUCode`, `CycleTime_min` |
| Machine allowable | `ETL.load_machine_allowable` | ~250 SKUs | `SKUCode`, `Machines` (list of machine IDs) |
| GT inventory | `ETL.load_gt_inventory` | ~30 SKUs | `SKUCode`, `GT_Inventory` |
| Running moulds | `ETL.load_running_moulds` | ~90 machines | `Machine`, `SKUCode`, `MouldNos`, `MouldLife_remaining`, `Num_Moulds` |
| Mould master | `ETL.load_mould_master` | ~2,200 moulds | `MouldNo`, `Matl.Code` (SKU), `Active Flag` |

Cycle-time transformation:
```
CycleTime_min = round((Raw_CureTime + LOAD_UNLOAD_BUFFER_MIN) / PRESS_EFFICIENCY)
              = round((Raw + 2.3) / 0.90)
```

---

## 3. Production constraints modelled

| # | Constraint | How it's enforced |
|---|---|---|
| 1 | Changeover time = 360 min per SKU switch | Deducted in the `Rounder` after LP solve |
| 2 | Mould cleaning = 180 min every 6,000 units | Inserted by `ScheduleBuilder` during timeline build |
| 3 | Minimise changeovers | Small ε penalty per active (SKU, machine) pair in LP objective |
| 4 | Press continuity (currently running moulds) | Locked in Phase 2 (`_build_continuity`) before the LP runs |
| 5 | Max changeovers per shift (default 3) | Plant-level cap enforced in `ScheduleBuilder._next_co_slot` |
| 6 | Mould availability per SKU | `MouldTracker` filters out machines with no compatible mould |

---

## 4. Machines & press model

- Every press has two moulds (LH + RH).
- Each cycle produces `CAVITIES_PER_MOULD = 2` units.
- A new mould lasts 3,000 cycles before cleaning.
- Shifts: **A** 07–15 h, **B** 15–23 h, **C** 23–07 h (3 shifts × 8 h).
- Horizon: 28 days × 3 shifts × 8 h × 60 min = **40,320 min / press**.
- Effective capacity = `40,320 − locked_mins[m]` where locked mins are
  consumed by continuity blocks from currently running moulds.

---

## 5. LP formulation

### Variables

| Symbol | Type | Domain | Meaning |
|---|---|---|---|
| `x[s, m]` | continuous | ℝ₊ | press-minutes allocated to SKU *s* on machine *m* |
| `slack[s]` | continuous | ℝ₊ | unmet demand-minutes for SKU *s* |

Variable count: `|S| × |M| + |S|` ≈ 50 × 90 + 50 = **4,550**.

### Objective

Minimise:

```
Σ_s slack[s]
+ ε · Σ_{s,m} (x[s,m] / demand_mins[s])
```

with `ε = CHANGEOVER_PENALTY_WEIGHT = 0.01`.

- The first term is the primary objective — minimise total unmet demand.
- The second is a tiny per-pair penalty that *softly* concentrates each SKU
  on fewer machines. It does **not** count changeovers exactly — it just
  prefers fewer active pairs. Exact changeover counting is impossible in
  pure LP without binary variables.

### Constraints

**Machine capacity** (one per press):
```
Σ_s x[s, m]  ≤  eff_cap[m]
```
where `eff_cap[m] = AVAIL_MINS − locked_mins[m]`. Note: changeover time
is **not** subtracted here — `Rounder` handles it post-solve.

**Demand coverage** (one per SKU):
```
Σ_m x[s, m] + slack[s]  ≥  demand_mins[s]
```

**Bounds** (per variable):
```
0 ≤ x[s, m] ≤ demand_mins[s]         if (s, m) is eligible
0 ≤ x[s, m] ≤ 0                       if ineligible (mould / allowable)
0 ≤ slack[s]                           (no upper bound)
```

### Eligibility

`(s, m)` is eligible iff:
- *m* is in the SKU's allowable-machines list, **and**
- `MouldTracker.get_eligible_machines_with_moulds(s, …)` returns *m*
  (there are ≥ 2 free moulds compatible with SKU *s*).

---

## 6. Solution pipeline

```text
ETL ─→ _prepare_skus ─→ _build_continuity ─→ LP_Solver ─→ Rounder
                                                              │
                                    ScheduleBuilder ←─────────┘
                                            │
                                     ExcelExporter (5 sheets)
```

### Phase 1 — SKU preparation (`_prepare_skus`)
- Join demand, cycle times, allowable matrix, GT inventory, mould pool
- Compute `Demand_Mins = ceil(Demand / CAVITIES) × CycleTime_min`
- Classify each SKU as `Schedulable` or skip with reason
  (no cycle time / no machines / no compatible mould)

### Phase 2 — Continuity (`_build_continuity`)
- Group all *running* machines by the SKU they're currently on
- For each group, distribute the SKU's demand across its presses
  proportionally to press capacity
- If group capacity < demand, the shortfall becomes the LP's remainder
- Generate shift-level rows immediately (before LP runs) so those
  presses can start at *t = 0* without changeovers

### Phase 3 — LP solve (`LP_Solver.solve`)
- Build `c`, `A_ub`, `b_ub`, `bounds` as above
- Call `scipy.optimize.linprog(method="highs")`
- Read `result.x` into a flat vector indexed by `s × M + m`

### Phase 4 — Rounding (`Rounder.round`)
Three passes turn the continuous LP solution into integer cycles:
1. **Floor**: `cycles[s,m] = floor(x[s,m] / ct[s])` — **this loses fractional capacity**.
2. **Changeover deduction**: for machines with > 1 SKU, subtract
   `(n_skus − 1) × CHANGEOVER_DURATION_MIN` and trim assignments if
   capacity is now exceeded.
3. **Priority top-up**: iterate SKUs by priority descending and give
   residual capacity to under-fulfilled SKUs.

### Phase 5 — Timeline build (`ScheduleBuilder.build`)
- Iterate each machine in priority order from `machine_sku_order`
- Insert `CHANGEOVER` rows (300 min) before every SKU switch, respecting
  the plant-wide per-shift cap
- Insert `MOULD_CLEAN` rows (120 min) every `units_per_cleaning_cycle`
  units of production
- Split production blocks across shift boundaries

### Phase 6 — Excel export (`ExcelExporter.export`)
Writes the 5-sheet workbook: Demand Fulfillment, Machine Schedule, Shift
Schedule, Machine Utilization, Mould Tracker.

---

## 7. Outputs

The workbook is the single artefact consumed by the dashboard:

| Sheet | Rows | Contents |
|---|---|---|
| Demand Fulfillment | ~50 SKUs + 1 total | Status (FULLY MET / PARTIAL / UNMET / UNSCHEDULABLE) |
| Machine Schedule | ~100–120 | One row per (machine, SKU) pair from LP |
| Shift Schedule | ~7,000–8,000 | Full timeline incl. CHANGEOVER and MOULD_CLEAN |
| Machine Utilization | ~90 presses | Used / idle minutes, utilisation % |
| Mould Tracker | ~2,200 moulds | Free vs assigned with life remaining |

---

## 8. Configuration knobs

All in the `Config` class at the top of `jk_curing_lp_PCR.py`:

| Setting | Default | Purpose |
|---|---|---|
| `PLANNING_DAYS` | 28 | Horizon length |
| `SHIFTS_PER_DAY` | 3 | 8-hour shifts per day |
| `CAVITIES_PER_MOULD` | 2 | Tyres per cycle |
| `CHANGEOVER_DURATION_MIN` | 360 | Minutes per SKU switch |
| `CLEANING_DURATION_MIN` | 180 | Minutes per mould clean |
| `LOAD_UNLOAD_BUFFER_MIN` | 2.3 | Added to raw cure time |
| `PRESS_EFFICIENCY` | 0.90 | Efficiency divisor |
| `MAX_CHANGEOVERS_PER_SHIFT` | 3 | Plant cap |
| `CHANGEOVER_PENALTY_WEIGHT` | 0.01 | ε in LP objective |
| `PLAN_DATE` | 2026-02-01 07:00 | Schedule start |
| `TYRE_TYPE` | "pcr" | PCR or TBR |

---

## 9. Known limitations

1. **Rounding loss** — `Rounder` floors every allocation, so SKUs that would
   benefit from an extra partial cycle lose it permanently. The greedy
   top-up pass tries to reclaim that capacity for high-priority SKUs, but
   it's not globally optimal.

2. **Approximate changeover accounting** — the LP's ε penalty only
   discourages spreading; it does not *count* changeovers. The actual
   changeover time is deducted in `Rounder`, which is reactive rather than
   proactive — if the LP spread a SKU across 4 presses, the rounder now has
   to pay 3 changeovers it didn't ask for.

3. **Greedy run order** — `machine_sku_order` on each press is priority
   desc, not sequence-optimised. There's no TSP-style minimisation of
   changeover patterns.

4. **Priority only post-hoc** — priority influences the top-up pass, not
   the LP itself. All slack variables have weight 1 in the LP objective.

5. **No proven optimality for the *rounded* solution** — the LP is
   globally optimal in *continuous* minutes, but the rounded/adjusted
   schedule after Phases 4–5 is a heuristic.

---

## 10. Entry points

```python
# Database mode (production)
from jk_curing_lp_PCR import run_from_database
run_from_database(
    demand_csv = "Feb_CTP_PCR_Requirement.csv",
    plan_start = datetime(2026, 2, 1, 7, 0, 0),
)

# Excel mode (offline / testing)
from jk_curing_lp_PCR import run_from_excel
run_from_excel(
    demand_path  = "load_demand.xlsx",
    cycles_path  = "load_cycle_times.xlsx",
    allow_path   = "load_machine_allowable.xlsx",
    gt_path      = "load_gt_inventory.xlsx",
    mould_path   = "load_mould_master.xlsx",
    running_path = "load_running_moulds.xlsx",
    plan_start   = datetime(2026, 2, 1, 7, 0, 0),
    output_path  = "PCR_LP_Schedule.xlsx",
)
```

Or just `python3 jk_curing_lp_PCR.py` to use the defaults from `Config`.

---

## 11. See also

- [`MILP_approach.md`](MILP_approach.md) — Mixed-integer formulation that
  eliminates the rounding step by solving for integer cycles directly.
- [`CPSAT_approach.md`](CPSAT_approach.md) — Constraint Programming
  version with reified linking, parallel search, and native integer
  variables.
- [`../dashboard/README.md`](../dashboard/README.md) — how to visualise
  the output.
