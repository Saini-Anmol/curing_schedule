# Curing Schedule — Documentation

Technical documentation for the three scheduler approaches in this repo.

| Document | Branch | Approach |
|---|---|---|
| [**LP_approach.md**](LP_approach.md) | `main` | Continuous LP + post-solve rounding (HiGHS via `scipy.optimize.linprog`) |
| [**MILP_approach.md**](MILP_approach.md) | `MILP_approach` | Mixed-Integer LP (HiGHS via `scipy.optimize.milp`) |
| [**CPSAT_approach.md**](CPSAT_approach.md) | `CP_SAT_approach` | Constraint Programming (Google OR-Tools CP-SAT) |

All three solvers produce the **same 5-sheet Excel output**, so you can
visualise any of them with the same dashboard —
see [`../dashboard/README.md`](../dashboard/README.md).

---

## Reading order

If you're new to the project, read in this order:

1. [`LP_approach.md`](LP_approach.md) — describes the complete pipeline
   (ETL, continuity, solver, rounding, schedule build, Excel export).
2. [`MILP_approach.md`](MILP_approach.md) — focuses on the differences
   vs LP, skipping the shared pipeline bits.
3. [`CPSAT_approach.md`](CPSAT_approach.md) — same, focused on the
   CP-SAT-specific changes.

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
