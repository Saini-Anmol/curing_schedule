# Product Requirements Document — JK Tyre BTP PCR Curing Scheduler

| | |
|---|---|
| **Document type** | PRD (Product Requirements) |
| **Product** | Monthly curing-press schedule generator for JK Tyre Banmore plant |
| **Audience** | Production planning team, plant engineers, on-call operators, downstream agents (Claude Code) |
| **Companion** | [`TRD.md`](TRD.md) covers the technical design that satisfies these requirements |
| **Status** | Active, V1 |

---

## 1. Background & problem statement

### The plant
JK Tyre's Banmore Tyre Plant (BTP) operates ~90 curing presses producing
Passenger Car Radial (PCR) tyres. Each press has two moulds (LH + RH), and
each cure cycle produces 2 tyres. The plant runs 3 shifts × 8 hours × 7
days a week — 24/7 operation.

### The problem
Every month, planners must decide:

1. **Which SKU runs on which press** for the upcoming 28- or 31-day horizon
2. **How many cure cycles** of each SKU on each press
3. **In what order** SKUs run on each press (changeover sequencing)
4. **When mould cleanings** must be inserted (every 3,000 cycles per mould)
5. **How the schedule unfolds across 3-shift days** with a plant-wide
   changeover cap

Doing this by hand for ~50 active SKUs × ~90 presses × ~84 shifts is
combinatorially infeasible. Existing planning is heuristic and leaves
demand on the table.

### Why now
Mature open-source optimisation libraries (HiGHS, OR-Tools CP-SAT) now
solve this problem class to provable optimality on commodity hardware in
seconds. The opportunity is to replace heuristic planning with a
solver-backed scheduler that meets demand more reliably and uses fewer
changeovers.

---

## 2. Objectives

### 2.1 Primary objective
**Maximise demand fulfilment** for the plan period. Concretely:
minimise total **unmet demand units** (`slack`) across all SKUs.

### 2.2 Secondary objective
**Minimise the number of changeovers**, since each changeover costs 360
minutes of production capacity. Equivalently: concentrate each SKU on
as few presses as possible.

### 2.3 Tertiary (implicit)
- Maximise press utilisation (idle time has cost)
- Honour SKU priority — high-priority SKUs should be filled first when
  capacity is constrained

### 2.4 Hierarchy
Primary > Secondary > Tertiary. The solver must never sacrifice 1 unit
of demand to save 1 changeover; the scale chosen
(`W_SLACK = 1000` in CP-SAT, `slack` weight = 1 vs ε = 0.01 in LP/MILP)
encodes this lexicographic preference numerically.

---

## 3. Stakeholders

| Stakeholder | Interest |
|---|---|
| **Production planning team** | Fast, reliable monthly schedules; ability to re-run after changes |
| **Plant operators (shift leads)** | Clear shift-level instructions; fewer surprises |
| **Mould room** | Predictable mould-cleaning windows; never forced into compatibility-violations |
| **Logistics / dispatch** | Daily/weekly throughput visibility for outbound planning |
| **Plant management** | KPIs on fulfilment %, utilisation, changeover count |
| **Algo engineering team (Algo8 AI)** | Maintain/extend the scheduler |
| **Future agents (Claude Code, etc.)** | Modify, audit, and review schedules autonomously |

---

## 4. Scope

### 4.1 In scope (V1)
- PCR (Passenger Car Radial) curing schedule
- 28-day or 31-day monthly horizon
- All 90 presses, ~50 active SKUs, ~2,200 moulds
- Three solver back-ends: LP, MILP, CP-SAT
- 5-sheet Excel deliverable
- DB ingestion (MySQL) and Excel ingestion (offline)
- Continuity from currently-running moulds
- Changeover and mould-cleaning scheduling

### 4.2 Out of scope (V1)
- TBR (Truck Bus Radial) — companion file exists but is not actively
  maintained against the V1 layout
- Multi-month rolling re-plans (single horizon at a time)
- Real-time or sub-shift re-optimisation (plan is batch, monthly)
- Automated upstream data cleaning (input data is treated as canonical)
- Web UI / dashboard (Excel is the v1 deliverable)
- Alerting / notification systems

### 4.3 Future scope (post-V1)
- TBR consolidation under the V1 package
- Sequence-optimised changeover ordering inside the solver (TSP-style)
- Lexicographic two-stage objective (strict slack-first, then changeovers)
- Priority-weighted slack
- Streaming dashboard (the dashboard idea was explored and reverted; may
  return)
- Multi-plant scheduling

---

## 5. Use cases

### UC-1 — Monthly plan generation (most common)
A planner uploads/connects to the demand data for the next month, runs
the scheduler, reviews the 5-sheet Excel, and circulates it.

```
1. Planner verifies input snapshots (load_*.xlsx) are current
2. Planner runs:  python -m V1.main --algo cpsat
3. Scheduler completes in seconds–minutes
4. Excel lands at output/CTP_PCR_Curing_CPSAT_v1_PlanSchedule_<Month>.xlsx
5. Planner reviews KPIs in the workbook header
6. Planner shares with shift leads
```

### UC-2 — Algorithm comparison
The OR/algo team wants to compare LP vs MILP vs CP-SAT on the same input
to pick the best for production.

```
1. Run all three: --algo lp, --algo milp, --algo cpsat
2. Compare output Excel files (KPI banners)
3. Use schedule-output-reviewer agent to audit each
4. Choose the algorithm with best fulfilment % at acceptable solve time
```

### UC-3 — Re-plan after data correction
A cycle-time error is found in the master data; planner fixes it and
re-runs.

```
1. Update master data in DB (or load_cycle_times.xlsx)
2. Re-run: python -m V1.main --algo cpsat
3. Diff vs prior schedule — only affected SKUs change
```

### UC-4 — Constraint experimentation
Algo team wants to test "what if changeover were 240 min instead of 360?"

```
1. Edit Config.CHANGEOVER_DURATION_MIN
2. Run scheduler
3. Compare KPIs to baseline
```

### UC-5 — Audit by AI agent
Claude's `schedule-output-reviewer` agent reads the produced Excel and
returns a report on objective gaps and constraint violations.

---

## 6. Functional requirements

### FR-1 — Inputs

| ID | Requirement | Source |
|---|---|---|
| FR-1.1 | System SHALL load demand from CSV: `Feb_CTP_PCR_Requirement.csv` | DB or local CSV |
| FR-1.2 | System SHALL load cycle times keyed by SKU | DB table or `load_cycle_times.xlsx` |
| FR-1.3 | System SHALL load machine-allowable matrix | DB table or `load_machine_allowable.xlsx` |
| FR-1.4 | System SHALL load GT inventory per SKU | DB table or `load_gt_inventory.xlsx` |
| FR-1.5 | System SHALL load currently-running moulds | DB tables or `load_running_moulds.xlsx` |
| FR-1.6 | System SHALL load the mould-SKU compatibility master | DB table or `load_mould_master.xlsx` |
| FR-1.7 | All inputs SHALL be readable from the `input/` directory by default, overridable via `INPUT_DIR` env var |

### FR-2 — Solver

| ID | Requirement |
|---|---|
| FR-2.1 | System SHALL support LP, MILP, and CP-SAT solvers, selectable via CLI |
| FR-2.2 | All three solvers SHALL produce the same Excel output format |
| FR-2.3 | System SHALL respect the 6 production constraints (see §7) |
| FR-2.4 | System SHALL minimise unmet demand as the primary objective |
| FR-2.5 | System SHALL minimise changeovers as the secondary objective |
| FR-2.6 | System SHALL accept a feasible incumbent on time-limit (do not crash) |

### FR-3 — Outputs

| ID | Requirement |
|---|---|
| FR-3.1 | System SHALL produce a 5-sheet Excel workbook in `output/` |
| FR-3.2 | Sheets SHALL be named: Demand Fulfillment, Machine Schedule, Shift Schedule, Machine Utilization, Mould Tracker |
| FR-3.3 | Each sheet SHALL include a 2-row title/subtitle banner before headers |
| FR-3.4 | The Demand Fulfillment sheet SHALL include a KPI banner (Demand, Planned, Gap, Fulfilment %, Avg Util %, Changeovers, Mould Cleans) |
| FR-3.5 | Output SHALL be readable by pandas with `skiprows=2` |
| FR-3.6 | Output filename SHALL follow `CTP_PCR_Curing_<TAG>_PlanSchedule_<Month>_<YYYY-MM-DD>_<N>Days.xlsx` |
| FR-3.7 | Output directory SHALL default to `<repo>/output/`, overridable via `OUTPUT_DIR` env var |

### FR-4 — Continuity
| FR-4.1 | Currently-running moulds SHALL not be interrupted; their continuation SHALL appear at t=0 of the schedule |
| FR-4.2 | Continuity blocks SHALL include mould-cleaning insertions per the standard rule (every 6,000 units) |
| FR-4.3 | Solver SHALL only allocate the residual demand after continuity covers what it can |

---

## 7. Production constraints (hard, non-negotiable)

| # | Constraint | Numeric value |
|---|---|---|
| 1 | Changeover time per SKU switch on a press | 360 min |
| 2 | Mould cleaning duration | 180 min |
| 3 | Mould cleaning trigger | Every 6,000 units (3,000 cycles × 2 cavities) |
| 4 | Cavities per cycle | 2 tyres |
| 5 | Moulds per press | 2 (LH + RH) |
| 6 | Maximum changeovers per shift, plant-wide | 3 |
| 7 | Mould-SKU compatibility | ≥ 2 free moulds compatible with the SKU |
| 8 | Press eligibility | Per machine-allowable matrix |
| 9 | Continuity | Currently running moulds must continue |

---

## 8. Non-functional requirements

### NFR-1 — Performance
| ID | Requirement | Target |
|---|---|---|
| NFR-1.1 | LP solve | < 10 s for 50 SKUs × 90 presses |
| NFR-1.2 | MILP solve (with top-K=8) | < 60 s |
| NFR-1.3 | CP-SAT solve (8 workers) | < 30 s, often optimal in seconds |
| NFR-1.4 | Excel write | < 5 s |
| NFR-1.5 | End-to-end (ETL → Excel) | < 5 min |

### NFR-2 — Reliability
- System SHALL not crash on time-limit; feasible incumbent SHALL be used.
- System SHALL fail loudly with a clear message if a sheet is missing or
  data is malformed (rather than silently producing wrong output).
- System SHALL respect the 6 constraints exactly — no soft violations.

### NFR-3 — Reproducibility
- Same input + same `Config` SHALL produce bit-identical output.
- Solver determinism is best-effort (CP-SAT parallel search may differ
  slightly between runs).

### NFR-4 — Maintainability
- All algorithm-specific code SHALL be isolated in dedicated modules
  (`solvers/`).
- Shared infrastructure (`MouldTracker`, `ETL`, `ScheduleBuilder`,
  `ExcelExporter`) SHALL not be duplicated across algorithms.
- Configuration SHALL be centralised in a single `Config` class.

### NFR-5 — Auditability
- Every run SHALL log:
  - solver status (OPTIMAL / FEASIBLE / TIME_LIMIT)
  - objective value and optimality gap
  - solve wall-clock time
  - input row counts per dataset
- The Excel banner SHALL surface KPIs needed to verify the plan.

---

## 9. Success metrics / KPIs

### 9.1 Operational KPIs (per schedule)
| KPI | Target |
|---|---|
| Demand fulfilment % | ≥ 95% |
| High-priority (top 10) fulfilment % | 100% |
| Plant changeover count | ≤ 1 CO per 4,000 units produced |
| Avg press utilisation % | ≥ 85% |
| Idle presses (< 60% util) | ≤ 5 of 90 |

### 9.2 System-level KPIs
| KPI | Target |
|---|---|
| Schedule generation time | < 5 min total |
| Schedule generation success rate | 100% (no crashes on production input) |
| Constraint violations in output | 0 |

---

## 10. Constraints & assumptions

### 10.1 Operational constraints
- Plant runs 3 shifts × 8 h, 24/7
- No weekends off, no maintenance windows in V1 model
- Currently-running moulds cannot be interrupted

### 10.2 Data assumptions
- Input data is canonical — solver does not validate cycle times,
  compatibility, or demand for sanity
- A SKU with cycle time 0 or no eligible machines is skipped
- A SKU with no compatible moulds is marked UNSCHEDULABLE

### 10.3 Technology constraints
- Open-source solvers only (HiGHS, OR-Tools)
- Python 3.9+
- macOS / Linux production target

### 10.4 Business constraints
- Plant operates on monthly horizons; no real-time re-plan
- Single plant (Banmore); no multi-plant logic

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| HiGHS MILP timeout on hard instances | Top-K filter, time limits, accept incumbent |
| Bad input data presented as algo failure | Validation script (planned); explicit error messages |
| Constraint drift between LP / MILP / CP-SAT branches | Per-approach docs, code-reviewer agent |
| Solver dependency churn (`scipy`, `ortools` API changes) | Pinned versions in environment notes |
| Folder rename breaks relative paths | Refactoring discipline, env-var overrides |

---

## 12. Glossary

| Term | Meaning |
|---|---|
| SKU | Stock Keeping Unit — a tyre type / size / pattern combination |
| Cure cycle | One full press cycle producing 2 tyres |
| Changeover (CO) | Switching a press from one SKU to another (~360 min) |
| Mould cleaning | Mandatory maintenance every 3,000 cycles per mould (~180 min) |
| Continuity | Currently running moulds that must not be interrupted |
| GT inventory | Goods-in-Transit — inventory en route, reduces effective demand |
| LP | Linear Programming (continuous) |
| MILP | Mixed-Integer Linear Programming |
| CP-SAT | Constraint Programming with SAT — Google OR-Tools' flagship solver |
| BigM | Linearisation constant in MILP for indicator-style constraints |
| Slack | Unmet demand variable; quantity the solver couldn't fulfil |

---

## 13. References

- [`CLAUDE.md`](../CLAUDE.md) — quick briefing for Claude Code sessions
- [`TRD.md`](TRD.md) — technical design satisfying these requirements
- [`LP_approach.md`](LP_approach.md) — LP solver deep-dive
- [`MILP_approach.md`](MILP_approach.md) — MILP solver deep-dive
- [`CPSAT_approach.md`](CPSAT_approach.md) — CP-SAT solver deep-dive
