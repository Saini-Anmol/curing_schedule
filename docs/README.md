# Curing Schedule — Documentation

Project documentation for the JK Tyre BTP PCR curing scheduler. Two layers:

### Layer 1 — Product & technical specification
Read these first to understand *what* the system does and *how* it's
designed.

| Document | Purpose |
|---|---|
| [**PRD.md**](PRD.md) | Product Requirements — objectives, scope, stakeholders, use cases, KPIs, constraints |
| [**TRD.md**](TRD.md) | Technical Requirements — architecture, data model, algorithm specs, module structure, performance targets |

### Layer 2 — Per-algorithm deep-dives

| Document | Branch | Approach |
|---|---|---|
| [**LP_approach.md**](LP_approach.md) | `main` | Continuous LP + post-solve rounding (HiGHS via `scipy.optimize.linprog`) |
| [**MILP_approach.md**](MILP_approach.md) | `MILP_approach` | Mixed-Integer LP (HiGHS via `scipy.optimize.milp`) |
| [**CPSAT_approach.md**](CPSAT_approach.md) | `CP_SAT_approach` | Constraint Programming (Google OR-Tools CP-SAT) |

All three solvers produce the **same 5-sheet Excel output** — only the
algorithm differs.

---

## Reading order

If you're new to the project:

1. [`PRD.md`](PRD.md) — what the system does and why
2. [`TRD.md`](TRD.md) — how it's built end-to-end
3. [`LP_approach.md`](LP_approach.md) — full LP pipeline (the baseline)
4. [`MILP_approach.md`](MILP_approach.md) — MILP, focused on the diff vs LP
5. [`CPSAT_approach.md`](CPSAT_approach.md) — CP-SAT, focused on the diff vs MILP

If you're an AI agent (Claude Code) onboarding to this repo, also read
[`../CLAUDE.md`](../CLAUDE.md) — the concise project briefing.

---

## Quick comparison

| | LP | MILP | CP-SAT |
|---|---|---|---|
| Allocation variable | continuous minutes | integer cycles | integer cycles |
| Assignment flag | (implicit) | binary `y` | `BoolVar` `y` |
| Linking | — | `c ≤ BigM·y` | `c > 0 iff y = 1` (reified) |
| Rounding loss | yes (post-solve) | none | none |
| Changeover accounting | approximate (ε penalty) | exact | exact |
| Solver backend | HiGHS LP | HiGHS MIP | Google CP-SAT |
| Parallelism | none | none | 8 workers |
| Extra dependency | — | — | `ortools` |
| Best on | small / linear | medium / proven optimal | **combinatorial / scheduling** |

For this problem (∼30 SKUs × 90 machines, exact changeover count
critical), **CP-SAT is the sharpest tool**. MILP is a solid middle
ground — same mathematical improvements as CP-SAT but without parallel
search. LP remains valuable as the baseline that everything else is
compared against.
