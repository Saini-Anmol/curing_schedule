# MILP Approach — PCR Curing Schedule

**File**: [`jk_curing_milp_PCR.py`](../btp/Curing/V1%2011-37-56-875/jk_curing_milp_PCR.py)
**Branch**: `MILP_approach`
**Solver**: HiGHS via `scipy.optimize.milp`

This document describes the Mixed-Integer Linear Programming variant. It
is a **drop-in replacement** for the LP scheduler — same inputs, same
5-sheet Excel output, only the allocation algorithm changes. The MILP
eliminates the floor-rounding and greedy top-up steps by solving for
integer cycles directly.

For the unchanged parts (data inputs, constraints modelled,
`MouldTracker`, `ScheduleBuilder`, `ExcelExporter`), see
[`LP_approach.md`](LP_approach.md). This document focuses on *what's
different*.

---

## 1. Why MILP over LP

Three issues with the LP + Rounder pipeline the MILP fixes directly:

| LP issue | MILP resolution |
|---|---|
| Floor rounding loses fractional cycles | Integer variables — no rounding step exists |
| Changeover time deducted *after* solving | Capacity constraint bakes in exact `(n_active − 1)` changeovers |
| Per-pair penalty (ε) only *softly* discourages spreading | Explicit binary `y[s,m]` — objective counts changeovers directly |

---

## 2. MILP formulation

### Decision variables (eligible pairs only)

| Symbol | Type | Domain | Meaning |
|---|---|---|---|
| `c[s, m]` | integer | ℤ₊, `[0, BigM]` | Number of cure cycles for SKU *s* on machine *m* |
| `y[s, m]` | binary | {0, 1} | 1 iff SKU *s* runs on machine *m* |
| `slack[s]` | continuous | ℝ₊, `[0, Demand]` | Unmet demand-units for SKU *s* |

Variables are created **only for eligible (s, m) pairs** (passing the
machine-allowable matrix *and* the mould filter *and* the top-K
filter — see §5). For the typical PCR instance this drops the variable
count from ~9,000 (LP's full grid) to ~500–1,000.

### Objective

```
minimise  Σ_s slack[s]                      (primary — unmet units)
        + ε · Σ_{s,m} y[s, m]                (secondary — # changeovers)
```

where `ε = CHANGEOVER_PENALTY_WEIGHT = 0.01`. The first term is in
**units** (not demand-mins like the LP), so its scale is natural and
comparable across instances.

### Constraints

**Demand coverage** (one per SKU):
```
CAVITIES · Σ_m c[s, m] + slack[s]  ≥  Demand[s]
```
Note this is in units — integer arithmetic throughout.

**Machine capacity** (one per press) — the clean formula:
```
Σ_s ct[s] · c[s, m]  +  co · Σ_s y[s, m]  ≤  avail[m] − locked[m] + co
```

Case analysis of what this encodes:

| `n_active = Σ y[s,m]` | LHS | Constraint | Interpretation |
|---|---|---|---|
| 0 | `0` | `0 ≤ cap + co` | Trivially satisfied |
| 1 | `ct·c + co` | `ct·c ≤ cap` | No changeover needed |
| 2 | `ct·(c₁+c₂) + 2·co` | `ct·(c₁+c₂) ≤ cap − co` | Exactly 1 changeover deducted |
| k | `ct·Σc + k·co` | `ct·Σc ≤ cap − (k−1)·co` | `(k − 1)` changeovers deducted |

**This is exact** — the solver sees the real capacity cost of every
changeover while choosing `y[s,m]`, not after.

**Linking** (one per eligible pair) — BigM linearization:
```
c[s, m]  ≤  BigM[s, m] · y[s, m]
```

where:
```
BigM[s, m] = min(
    floor(eff_cap[m] / ct[s]),      # capacity-derived ceiling
    ceil(Demand[s]   / CAVITIES)    # demand-derived ceiling
)
```

Using the tighter of the two caps dramatically improves the LP
relaxation bound. The demand cap alone typically shrinks BigM 5–10×.

### Variable bounds

```
c[s, m]   ∈  [0, BigM[s, m]]
y[s, m]   ∈  {0, 1}             (via ub=1 + integrality)
slack[s]  ∈  [0, Demand[s]]
```

---

## 3. Pipeline

The pipeline is **identical** to the LP version except Phase 3
(solve) and Phase 4 (extract) are swapped:

```text
ETL ─→ _prepare_skus ─→ _build_continuity ─→ MILP_Solver ─→ MILP_Extractor
                                                                   │
                                         ScheduleBuilder ←─────────┘
                                                 │
                                          ExcelExporter (5 sheets)
```

### Phase 3 — MILP solve (`MILP_Solver.solve`)
- Build decision-variable arrays, bounds, integrality vector
- Assemble constraint matrix for demand, capacity, linking
- Call `scipy.optimize.milp` with HiGHS backend
- Accept the best **feasible incumbent** on time-limit (don't require
  provable optimality — see §6)

### Phase 4 — Extraction (`MILP_Extractor.extract`)
Replaces the old 161-line `Rounder` with a 62-line pass:
- Read integer `c[s, m]` from `result.x`
- Group active pairs by machine
- Sort each machine's SKUs by priority desc (for `ScheduleBuilder` order)
- Emit `df_sched` directly — **no floor rounding, no top-up pass**

---

## 4. Key advantages over LP

| # | Benefit | Why |
|---|---|---|
| 1 | Zero rounding loss | Cycles are integer by construction |
| 2 | Exact changeover count | Binary `y[s,m]` in objective counts the real thing |
| 3 | Exact capacity accounting | Capacity constraint ties CO time to chosen `y` |
| 4 | Priority can move to objective | Simple to weight `slack[s]` by priority (future enhancement) |
| 5 | Simpler extractor | 100 fewer lines than `Rounder`, no heuristic top-up |

---

## 5. Making HiGHS fast enough — the top-K filter

HiGHS's MIP solver is solid but slower than commercial solvers (Gurobi,
CPLEX) on combinatorial problems. For a naive formulation on your
instance (30 SKUs × 90 machines × ~30 eligible each → 904 binaries),
HiGHS can take 10+ minutes to close the optimality gap.

Solution: the **top-K presses per SKU filter**.

```
Config.MILP_TOP_K_PRESSES_PER_SKU = 8
```

For each SKU, we score each eligible machine by
`fit = eff_cap[m] / ct[s]` (= max producible cycles) and keep only the
top-K. A SKU that *could* run on 30 presses gets restricted to its 8 most
productive ones.

**Effect measured on a 30 × 90 synthetic instance:**
- Before filter: >600 s, time-out before finding optimum
- After filter (K=8): 240 binaries, **2.73 s to OPTIMAL**

Set `MILP_TOP_K_PRESSES_PER_SKU = 0` to disable the filter (full
eligibility, slower).

---

## 6. Handling HiGHS time-limits gracefully

`scipy.optimize.milp` returns `success=False` whenever the solver hits
the time limit — **even when `result.x` contains a perfectly usable
incumbent**. The solver never needs a provably-optimal answer; a
feasible plan that's within 5 % of the LP bound is fine for production.

```python
if result.x is None:
    raise RuntimeError(f"MILP found no feasible solution: {result.message}")
if not result.success:
    print(f"  [MILP] WARNING: {first_line} - using best incumbent found")
```

The two knobs controlling this:

| Setting | Default | Purpose |
|---|---|---|
| `MILP_TIME_LIMIT_SEC` | 600 | Wall-clock cap |
| `MILP_REL_GAP` | 0.05 | Stop once incumbent is within 5 % of LP bound |
| `MILP_SHOW_PROGRESS` | True | Stream HiGHS solver log so you can see it working |

---

## 7. Configuration knobs specific to MILP

```python
class Config:
    # … (all LP settings unchanged) …

    # MILP-specific
    MILP_TIME_LIMIT_SEC        = 600
    MILP_REL_GAP               = 0.05
    MILP_SHOW_PROGRESS         = True
    MILP_TOP_K_PRESSES_PER_SKU = 8       # 0 = use all eligible machines

    OUTPUT_FILE = f"CTP_PCR_Curing_MILP_v1_PlanSchedule_Feb_{PLAN_DATE.date()}_28Days.xlsx"
```

---

## 8. Running it

Same interface as the LP version:

```bash
git checkout MILP_approach
cd "btp/Curing/V1 11-37-56-875"
python3 jk_curing_milp_PCR.py
```

Expected console output:
```
[Phase 3] Solving MILP...
  [MILP] 510 vars (240 cycle + 240 binary + 30 slack) | 360 constraints | eps: 0.01
  [MILP] Pairs after top-8 filter: 240 (was 900 before filter)
  [MILP] Solving (time limit 600s, gap tol 5.0%) — streaming HiGHS log:
  Running HiGHS 1.X.X …
  [MILP] status=Optimal | Unmet units: 0 | Active pairs: 34

[Phase 4] Extracting integer cycles (no rounding loss)...
  [Extract] Rows: 34 | Units: 71,188 | Changeovers: 7 | CO time: 42.0 hrs
```

---

## 9. Limitations

1. **HiGHS isn't the fastest MIP solver**. Even with the top-K filter
   and 5% gap tolerance, instances beyond ~1,500 binaries may still be
   slow. The CP-SAT version handles the same problem in seconds with
   parallel search.
2. **Top-K is heuristic**. A SKU's 9th-best press by fit score might
   occasionally be the right answer for a niche capacity pocket — the
   filter rules that out. If needed, raise K or set to 0.
3. **Priority is not yet in the objective**. Weighting slack by priority
   is a trivial change but not enabled by default (keeps parity with LP).
4. **No sequence optimisation**. Run order on each machine is still
   priority-desc, not TSP-minimised.

---

## 10. See also

- [`LP_approach.md`](LP_approach.md) — baseline approach, describes the
  unchanged parts of the pipeline.
- [`CPSAT_approach.md`](CPSAT_approach.md) — CP-SAT variant, faster on
  this class of instance due to native integer handling and parallel
  search.
- [`../dashboard/README.md`](../dashboard/README.md) — how to visualise
  the MILP output in the dashboard.
