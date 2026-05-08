"""
MILP_Solver — integer-cycles MILP formulation solved via scipy.optimize.milp
(HiGHS backend). Drop-in replacement for the LP + post-hoc rounding pipeline.

Inputs / outputs match LP_Solver.solve so the rest of the orchestrator can
treat both interchangeably.
"""

import math

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp

from V1.config.settings import Config
from V1.setups.mould_tracker import MouldTracker


# ══════════════════════════════════════════════════════════════════════════════
# MILP SOLVER  (v1 — integer cycles + binary assignment + exact changeover count)
# ══════════════════════════════════════════════════════════════════════════════
class MILP_Solver:
    """
    Mixed Integer Linear Programme — solves the production allocation
    directly in integer cycles, so no post-hoc floor rounding is needed.

    Variables (only created for eligible (SKU, machine) pairs)
    ----------------------------------------------------------
    c[s,m]   : integer >= 0  — number of cure cycles for SKU s on machine m
    y[s,m]   : binary {0,1}  — 1 iff SKU s is assigned to machine m
    slack[s] : continuous    — unmet demand-units for SKU s

    Objective
    ---------
    minimise  sum(slack[s])  +  eps * sum(y[s,m])

    The second term (with eps = CHANGEOVER_PENALTY_WEIGHT) counts active
    (SKU, machine) pairs — every extra pair costs a changeover, so the
    solver concentrates each SKU on as few presses as possible.

    Constraints
    -----------
    Demand   :  CAVITIES * sum_m c[s,m] + slack[s] >= Demand[s]
    Capacity :  sum_s ct[s]*c[s,m] + co * sum_s y[s,m] <= avail[m] - locked[m] + co
                (for n_active SKUs this allows exactly (n_active - 1)
                 changeovers worth of downtime on each press)
    Linking  :  c[s,m] <= BigM[s,m] * y[s,m]
                (where BigM[s,m] = floor(avail[m] / ct[s]))
    """

    def __init__(self):
        self.avail_mins = Config.avail_mins()
        self.co_mins    = Config.CHANGEOVER_DURATION_MIN
        self.penalty    = Config.CHANGEOVER_PENALTY_WEIGHT

    def solve(
        self,
        df_valid: pd.DataFrame,
        all_machines: list[str],
        mould_tracker: MouldTracker,
        locked_machine_mins: dict[str, float] = None,
    ) -> tuple[np.ndarray, dict]:
        """
        Parameters
        ----------
        df_valid            : SKU rows with Demand, Machines, CycleTime_min
        all_machines        : sorted list of all machine IDs
        mould_tracker       : for mould-level eligibility filtering
        locked_machine_mins : {machine: minutes} already consumed by
                              continuity blocks (currently running moulds)
        """
        locked = locked_machine_mins or {}
        S = len(df_valid)
        M = len(all_machines)
        midx = {m: i for i, m in enumerate(all_machines)}
        sku_rows = list(df_valid.itertuples(index=False))

        # ── effective per-machine capacity (minutes) ─────────────────────────
        eff_cap = {}
        for m in all_machines:
            eff_cap[m] = max(0.0, self.avail_mins - locked.get(str(m), 0.0))

        # ── build eligible (s, m) pair list — drops the huge Cartesian grid ──
        # Only pairs that pass three filters become variables:
        #   (a) machine-allowable matrix,
        #   (b) mould availability (MouldTracker),
        #   (c) top-K per SKU by fit score (eff_cap[m] / ct[s]).
        # Filter (c) is the biggest speedup — it keeps each SKU to its
        # ~K most productive presses instead of 20-30, shrinking the binary
        # variable count by roughly 4-6x and making HiGHS branch-and-bound
        # tractable within the time limit.
        top_k = int(Config.MILP_TOP_K_PRESSES_PER_SKU)
        pair_list: list[tuple[int, int]] = []
        pair_idx:  dict[tuple[int, int], int] = {}
        total_elig_before_k = 0
        for si, row in enumerate(sku_rows):
            elig_machines = set(str(m) for m in row.Machines)
            mould_elig = set(
                str(m) for m in
                mould_tracker.get_eligible_machines_with_moulds(
                    row.SKUCode, list(row.Machines)
                )
            )
            elig = elig_machines & mould_elig
            ct = row.CycleTime_min
            if ct <= 0:
                continue

            # (mi, fit) pairs for machines that pass (a) AND (b) AND have capacity
            scored: list[tuple[int, float]] = []
            for mi, mach in enumerate(all_machines):
                if str(mach) in elig and eff_cap[mach] >= ct:
                    fit = eff_cap[mach] / ct           # max producible cycles
                    scored.append((mi, fit))
            total_elig_before_k += len(scored)

            # Filter (c): keep only the top-K machines for this SKU
            if top_k > 0 and len(scored) > top_k:
                scored.sort(key=lambda t: -t[1])
                scored = scored[:top_k]

            for mi, _ in scored:
                pair_idx[(si, mi)] = len(pair_list)
                pair_list.append((si, mi))

        P = len(pair_list)                 # number of eligible pairs
        if P == 0:
            print("  [MILP] No eligible (SKU, machine) pairs found.")
            empty_x = np.zeros(2 * P + S)
            meta = {"S": S, "M": M, "P": P, "midx": midx,
                    "all_machines": all_machines, "sku_rows": sku_rows,
                    "pair_list": pair_list, "pair_idx": pair_idx}
            return empty_x, meta

        n_vars = 2 * P + S                 # c[p], y[p], slack[s]

        # ── variable bounds & integrality ───────────────────────────────────
        lb = np.zeros(n_vars)
        ub = np.full(n_vars, np.inf)
        integrality = np.zeros(n_vars, dtype=int)

        # per-pair upper bound on cycles — tightening BigM improves LP bound
        #   capacity-derived : floor(eff_cap[m] / ct[s])
        #   demand-derived   : ceil(Demand[s] / CAVITIES)   (no point producing
        #                      more than demand of this SKU)
        # The min of these gives the tightest valid BigM, which in turn gives
        # a tighter LP relaxation and much faster branch-and-bound.
        bigM = np.zeros(P)
        cav_int = Config.CAVITIES_PER_MOULD
        for p, (si, mi) in enumerate(pair_list):
            row = sku_rows[si]
            ct = row.CycleTime_min
            cap_m = eff_cap[all_machines[mi]]
            cap_cycles = int(cap_m / ct) if ct > 0 else 0
            dem_cycles = int(math.ceil(float(row.Demand) / cav_int))
            bigM[p] = max(1, min(cap_cycles, dem_cycles))
            ub[p] = bigM[p]                    # c[p] in [0, BigM]
            ub[P + p] = 1.0                    # y[p] in {0, 1}
            integrality[p] = 1                 # cycles integer
            integrality[P + p] = 1             # y binary (ub=1 enforces)

        # slack upper bound = Demand[s] (can't miss more than full demand)
        for si, row in enumerate(sku_rows):
            ub[2 * P + si] = float(row.Demand)

        # slack[s] is continuous >= 0 (integrality already 0)

        # ── objective ────────────────────────────────────────────────────────
        c_obj = np.zeros(n_vars)
        # slack cost: 1 per unmet unit (dominant term)
        c_obj[2 * P : 2 * P + S] = 1.0
        # changeover penalty: epsilon per active (s, m) pair
        c_obj[P : 2 * P] = self.penalty

        # ── Constraint 1 : demand coverage ──────────────────────────────────
        # CAVITIES * sum_m c[s,m] + slack[s] >= Demand[s]
        # rewritten as  -CAVITIES * sum_m c[s,m] - slack[s] <= -Demand[s]
        A_dem = np.zeros((S, n_vars))
        b_dem = np.zeros(S)
        cav = Config.CAVITIES_PER_MOULD
        for p, (si, mi) in enumerate(pair_list):
            A_dem[si, p] = -cav
        for si, row in enumerate(sku_rows):
            A_dem[si, 2 * P + si] = -1.0
            b_dem[si] = -float(row.Demand)

        # ── Constraint 2 : machine capacity with changeover accounting ──────
        # sum_s ct[s] * c[s,m] + co * sum_s y[s,m] <= eff_cap[m] + co
        # (for n_active=0 -> trivially true; n_active>=1 -> (n_active-1) COs deducted)
        A_cap = np.zeros((M, n_vars))
        b_cap = np.zeros(M)
        for p, (si, mi) in enumerate(pair_list):
            A_cap[mi, p]      = sku_rows[si].CycleTime_min
            A_cap[mi, P + p]  = self.co_mins
        for mi, mach in enumerate(all_machines):
            b_cap[mi] = eff_cap[mach] + self.co_mins

        # ── Constraint 3 : linking c[s,m] <= BigM[s,m] * y[s,m] ─────────────
        # Forces y[p] = 1 whenever c[p] > 0, so the CO count above is tight.
        A_link = np.zeros((P, n_vars))
        for p in range(P):
            A_link[p, p]     = 1.0
            A_link[p, P + p] = -bigM[p]
        b_link = np.zeros(P)

        A_ub = np.vstack([A_dem, A_cap, A_link])
        b_ub = np.concatenate([b_dem, b_cap, b_link])

        print(f"  [MILP] {n_vars:,} vars ({P} cycle + {P} binary + {S} slack) | "
              f"{len(b_ub):,} constraints | eps: {self.penalty}")
        print(f"  [MILP] Pairs after top-{top_k} filter: {P} "
              f"(was {total_elig_before_k} before filter)")
        print(f"  [MILP] Eff capacity range: "
              f"{min(eff_cap.values()):,.0f}-{max(eff_cap.values()):,.0f} min/press")
        print(f"  [MILP] Solving (time limit {Config.MILP_TIME_LIMIT_SEC}s, "
              f"gap tol {Config.MILP_REL_GAP*100:.1f}%) — streaming HiGHS log:")

        options = {
            "time_limit": Config.MILP_TIME_LIMIT_SEC,
            "mip_rel_gap": Config.MILP_REL_GAP,
            "disp": bool(Config.MILP_SHOW_PROGRESS),
        }
        result = milp(
            c=c_obj,
            constraints=[LinearConstraint(A_ub, -np.inf, b_ub)],
            integrality=integrality,
            bounds=Bounds(lb, ub),
            options=options,
        )

        # HiGHS returns success=False on time-limit even when it has a good
        # incumbent. We only fail if there is no feasible solution at all
        # (result.x is None). A time-limited incumbent is still usable — it
        # just may not be provably optimal.
        if result.x is None:
            raise RuntimeError(f"MILP found no feasible solution: {result.message}")
        if not result.success:
            first_line = result.message.strip().splitlines()[0]
            print(f"  [MILP] WARNING: {first_line} - using best incumbent found")

        unmet_units = float(np.sum(result.x[2 * P : 2 * P + S]))
        active_pairs = int(round(float(np.sum(result.x[P : 2 * P]))))
        status_line  = result.message.strip().splitlines()[0]
        print(f"  [MILP] status={status_line} | "
              f"Unmet units: {unmet_units:,.0f} | Active pairs: {active_pairs}")

        meta = {
            "S": S, "M": M, "P": P, "midx": midx,
            "all_machines": all_machines, "sku_rows": sku_rows,
            "pair_list": pair_list, "pair_idx": pair_idx,
        }
        return result.x, meta
