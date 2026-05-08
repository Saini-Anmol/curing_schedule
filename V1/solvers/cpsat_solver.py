"""
CPSAT_Solver — Constraint Programming formulation solved via Google OR-Tools
CP-SAT. Native integer variables, parallel search, no rounding step.

Inputs / outputs match LP_Solver / MILP_Solver so the orchestrator can dispatch
on solver type without changing any other Phase.
"""

import math
from collections import defaultdict

import pandas as pd
from ortools.sat.python import cp_model

from V1.config.settings import Config
from V1.setups.mould_tracker import MouldTracker


# ══════════════════════════════════════════════════════════════════════════════
# CP-SAT SOLVER  (v1 — integer cycles + binary assignment + parallel search)
# ══════════════════════════════════════════════════════════════════════════════
class CPSAT_Solver:
    """
    Constraint Programming solver (Google OR-Tools CP-SAT). All variables are
    integer by construction — there is no rounding step and no floor-loss.

    Variables (only created for eligible (SKU, machine) pairs)
    ----------------------------------------------------------
    c[s,m]   : IntVar in [0, BigM]   — number of cure cycles
    y[s,m]   : BoolVar               — 1 iff the (SKU, machine) pair is active
    slack[s] : IntVar in [0, Demand] — unmet demand-units

    Constraints
    -----------
    Demand   :  CAVITIES * sum_m c[s,m] + slack[s] >= Demand[s]
    Capacity :  sum_s ct[s] * c[s,m] + co * sum_s y[s,m] <= avail[m] - locked[m] + co
                (for n_active SKUs on a press this deducts exactly
                 (n_active - 1) changeovers worth of downtime)
    Linking  :  y[s,m] == 1  iff  c[s,m] > 0     (reified via OnlyEnforceIf)

    Objective
    ---------
    minimise  W_SLACK * sum(slack[s])  +  sum(y[s,m])
              (W_SLACK makes unmet demand dominate; the sum(y) term
               concentrates each SKU on as few presses as possible)
    """

    def __init__(self):
        self.avail_mins = int(Config.avail_mins())
        self.co_mins    = int(Config.CHANGEOVER_DURATION_MIN)
        self.w_slack    = int(Config.CPSAT_W_SLACK)

    def solve(
        self,
        df_valid: pd.DataFrame,
        all_machines: list[str],
        mould_tracker: MouldTracker,
        locked_machine_mins: dict[str, float] = None,
    ) -> tuple[dict, dict]:
        """
        Parameters
        ----------
        df_valid            : SKU rows with Demand, Machines, CycleTime_min
        all_machines        : sorted list of all machine IDs
        mould_tracker       : for mould-level eligibility filtering
        locked_machine_mins : {machine: minutes} already consumed by
                              continuity blocks (currently running moulds)

        Returns
        -------
        solution : {'cycles': {(si,mi): int}, 'y': {(si,mi): 0|1},
                    'slack':  {si: int}}
        meta     : same shape as LP/MILP solvers — consumed by the extractor
        """
        locked = locked_machine_mins or {}
        S = len(df_valid)
        M = len(all_machines)
        midx = {m: i for i, m in enumerate(all_machines)}
        sku_rows = list(df_valid.itertuples(index=False))

        # ── effective per-machine capacity (minutes, integer) ────────────────
        eff_cap = {}
        for m in all_machines:
            eff_cap[m] = max(0, self.avail_mins - int(round(locked.get(str(m), 0.0))))

        # ── build eligible (s, m) pair list — drops the huge Cartesian grid ──
        pair_list: list[tuple[int, int]] = []
        pair_idx:  dict[tuple[int, int], int] = {}
        for si, row in enumerate(sku_rows):
            elig_machines = set(str(m) for m in row.Machines)
            mould_elig = set(
                str(m) for m in
                mould_tracker.get_eligible_machines_with_moulds(
                    row.SKUCode, list(row.Machines)
                )
            )
            elig = elig_machines & mould_elig
            ct = int(row.CycleTime_min) if row.CycleTime_min else 0
            if ct <= 0:
                continue
            for mi, mach in enumerate(all_machines):
                if str(mach) in elig and eff_cap[mach] >= ct:
                    pair_idx[(si, mi)] = len(pair_list)
                    pair_list.append((si, mi))

        P = len(pair_list)
        if P == 0:
            print("  [CP-SAT] No eligible (SKU, machine) pairs found.")
            solution = {"cycles": {}, "y": {}, "slack": {si: int(row.Demand)
                                                         for si, row in enumerate(sku_rows)}}
            meta = {"S": S, "M": M, "P": P, "midx": midx,
                    "all_machines": all_machines, "sku_rows": sku_rows,
                    "pair_list": pair_list, "pair_idx": pair_idx}
            return solution, meta

        # ── build CP-SAT model ───────────────────────────────────────────────
        model = cp_model.CpModel()

        # Per-pair upper bound on cycles
        bigM = {}
        c_var: dict[int, cp_model.IntVar] = {}
        y_var: dict[int, cp_model.IntVar] = {}
        for p, (si, mi) in enumerate(pair_list):
            ct = int(sku_rows[si].CycleTime_min)
            cap_m = eff_cap[all_machines[mi]]
            M_cap = max(1, cap_m // ct)
            # Also bound by demand — no point producing more than demand
            demand_cap = int(math.ceil(int(sku_rows[si].Demand)
                                       / Config.CAVITIES_PER_MOULD))
            bigM[p] = max(1, min(M_cap, demand_cap))
            c_var[p] = model.NewIntVar(0, bigM[p], f"c_{si}_{mi}")
            y_var[p] = model.NewBoolVar(f"y_{si}_{mi}")

            # Reified linking: c[p] > 0  iff  y[p] == 1
            # c[p] >= 1  when y[p] = 1   (cycles is 0 otherwise — enforced below)
            # c[p] == 0  when y[p] = 0
            model.Add(c_var[p] >= 1).OnlyEnforceIf(y_var[p])
            model.Add(c_var[p] == 0).OnlyEnforceIf(y_var[p].Not())

        # Slack per SKU
        slack_var: dict[int, cp_model.IntVar] = {}
        for si, row in enumerate(sku_rows):
            slack_var[si] = model.NewIntVar(0, int(row.Demand), f"slack_{si}")

        # ── Constraint 1: demand coverage ───────────────────────────────────
        # CAVITIES * sum_m c[s,m] + slack[s] >= Demand[s]
        cav = Config.CAVITIES_PER_MOULD
        sku_pair_idx: dict[int, list[int]] = defaultdict(list)
        for p, (si, mi) in enumerate(pair_list):
            sku_pair_idx[si].append(p)
        for si, row in enumerate(sku_rows):
            pairs_s = sku_pair_idx.get(si, [])
            model.Add(
                cav * sum(c_var[p] for p in pairs_s) + slack_var[si]
                >= int(row.Demand)
            )

        # ── Constraint 2: machine capacity + changeover deduction ───────────
        # sum_s ct[s] * c[s,m] + co * sum_s y[s,m] <= eff_cap[m] + co
        mach_pair_idx: dict[int, list[int]] = defaultdict(list)
        for p, (si, mi) in enumerate(pair_list):
            mach_pair_idx[mi].append(p)
        for mi, mach in enumerate(all_machines):
            pairs_m = mach_pair_idx.get(mi, [])
            if not pairs_m:
                continue
            prod_mins = sum(
                int(sku_rows[pair_list[p][0]].CycleTime_min) * c_var[p]
                for p in pairs_m
            )
            co_mins = self.co_mins * sum(y_var[p] for p in pairs_m)
            model.Add(prod_mins + co_mins <= eff_cap[mach] + self.co_mins)

        # ── Objective ───────────────────────────────────────────────────────
        # W_SLACK * sum(slack) + sum(y)
        # slack dominates (W_SLACK >> 1), sum(y) is a soft tie-breaker that
        # concentrates each SKU on as few presses as possible.
        model.Minimize(
            self.w_slack * sum(slack_var[si] for si in range(S))
            + sum(y_var[p] for p in range(P))
        )

        print(f"  [CP-SAT] {2 * P + S:,} vars ({P} cycle + {P} bool + {S} slack) | "
              f"SKUs: {S} | Machines: {M} | Pairs: {P}")
        print(f"  [CP-SAT] Eff capacity range: "
              f"{min(eff_cap.values()):,}-{max(eff_cap.values()):,} min/press | "
              f"W_SLACK={self.w_slack}")

        # ── Solve ───────────────────────────────────────────────────────────
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(Config.CPSAT_TIME_LIMIT_SEC)
        solver.parameters.num_search_workers  = int(Config.CPSAT_NUM_WORKERS)
        solver.parameters.log_search_progress = False

        status = solver.Solve(model)
        status_name = solver.StatusName(status)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise RuntimeError(f"CP-SAT did not find a feasible solution: {status_name}")

        # ── Extract solution into a dict keyed by pair index ────────────────
        solution_cycles = {p: solver.Value(c_var[p]) for p in range(P)}
        solution_y      = {p: solver.Value(y_var[p]) for p in range(P)}
        solution_slack  = {si: solver.Value(slack_var[si]) for si in range(S)}
        solution = {"cycles": solution_cycles, "y": solution_y, "slack": solution_slack}

        unmet_units  = sum(solution_slack.values())
        active_pairs = sum(solution_y.values())
        obj          = solver.ObjectiveValue()
        best_bound   = solver.BestObjectiveBound()
        walltime     = solver.WallTime()
        print(f"  [CP-SAT] status={status_name} | obj={obj:,.0f} | "
              f"bound={best_bound:,.0f} | wall={walltime:.2f}s")
        print(f"  [CP-SAT] Unmet units: {unmet_units:,} | Active pairs: {active_pairs}")

        meta = {
            "S": S, "M": M, "P": P, "midx": midx,
            "all_machines": all_machines, "sku_rows": sku_rows,
            "pair_list": pair_list, "pair_idx": pair_idx,
        }
        return solution, meta
