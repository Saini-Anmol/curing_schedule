"""
LP solver — continuous press-minute allocation via HiGHS / scipy.linprog.
"""

import numpy as np
import pandas as pd
from scipy.optimize import linprog

from V1.config.settings import Config
from V1.setups.mould_tracker import MouldTracker


# ══════════════════════════════════════════════════════════════════════════════
# LP SOLVER  (v2 — with changeover penalty + effective capacity)
# ══════════════════════════════════════════════════════════════════════════════
class LP_Solver:
    """
    Variables
    ---------
    x[s,m]   : press-minutes assigned to SKU s on machine m
    slack[s] : unmet demand-minutes for SKU s

    Objective
    ---------
    minimise  Σ slack[s]
            + CHANGEOVER_PENALTY_WEIGHT * Σ (x[s,m] / demand_mins[s])

    The second term penalises spreading SKU s across many machines
    (each active pair gets a fractional penalty proportional to how much
    of the demand it handles), which drives the LP to concentrate each
    SKU on as few machines as possible — i.e. fewer changeovers.

    Effective capacity
    ------------------
    Each machine has theoretical capacity = AVAIL_MINS.
    We reserve CHANGEOVER_DURATION_MIN for each *additional* SKU assigned
    beyond the first.  Because the number of SKUs per machine is unknown
    before solving, we use a conservative estimate:

        effective_cap[m] = AVAIL_MINS
                         - CHANGEOVER_DURATION_MIN * (expected_skus_per_m - 1)

    expected_skus_per_m is initialised to 1 and iterated once if needed.
    In practice, with the penalty in the objective the LP naturally limits
    SKUs per machine, so one iteration suffices.
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
        df_valid            : SKU rows with Demand_Mins, Machines, CycleTime_min
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

        def xidx(s, mi): return s * M + mi

        # Effective capacity = avail - locked - conservatively reserve 1 CO
        # (LP penalty keeps actual SKUs/machine low, so 1 CO buffer is enough)
        eff_cap = {}
        for m in all_machines:
            cap = self.avail_mins - locked.get(str(m), 0.0) #- self.co_mins
            eff_cap[m] = max(cap, 0.0)

        n_vars = S * M + S
        # ── objective ────────────────────────────────────────────────────────
        c = np.zeros(n_vars)
        for s in range(S):
            c[S * M + s] = 1.0          # slack penalty (hard)
        for s, row in enumerate(sku_rows):
            if row.Demand_Mins > 0:
                for mi in range(M):
                    # soft penalty per active pair (encourages concentration)
                    c[xidx(s, mi)] += self.penalty / row.Demand_Mins

        # ── bounds ───────────────────────────────────────────────────────────
        bounds = [(0.0, None)] * n_vars
        for si, row in enumerate(sku_rows):
            sku = row.SKUCode
            eligible = set(row.Machines)
            # mould filter: remove machines if no compatible moulds available
            mould_eligible = set(
                mould_tracker.get_eligible_machines_with_moulds(sku, list(eligible))
            )
            for mi, mach in enumerate(all_machines):
                if mach not in mould_eligible:
                    bounds[xidx(si, mi)] = (0.0, 0.0)
                else:
                    bounds[xidx(si, mi)] = (0.0, float(row.Demand_Mins))

        # ── machine capacity  A_ub x ≤ b_ub ─────────────────────────────────
        A_cap = np.zeros((M, n_vars))
        b_cap = np.array([eff_cap[m] for m in all_machines])
        for mi in range(M):
            for si in range(S):
                A_cap[mi, xidx(si, mi)] = 1.0

        # ── demand coverage  -Σ x[s,m] - slack[s] ≤ -demand_mins[s] ─────────
        A_dem = np.zeros((S, n_vars))
        b_dem = np.zeros(S)
        for si, row in enumerate(sku_rows):
            for mi in range(M):
                A_dem[si, xidx(si, mi)] = -1.0
            A_dem[si, S * M + si] = -1.0
            b_dem[si] = -float(row.Demand_Mins)

        A_ub = np.vstack([A_cap, A_dem])
        b_ub = np.concatenate([b_cap, b_dem])

        print(f"  [LP] {n_vars:,} vars | {len(b_ub):,} constraints | "
              f"Penalty weight: {self.penalty}")
        print(f"  [LP] Eff capacity range: "
              f"{min(eff_cap.values()):,.0f}–{max(eff_cap.values()):,.0f} min/press")

        result = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
        if result.status != 0:
            raise RuntimeError(f"LP did not converge: {result.message}")

        unmet = sum(result.x[S * M + s] for s in range(S))
        print(f"  [LP] OPTIMAL | Unmet demand-mins: {unmet:,.0f} ({unmet/60:.1f} hrs)")

        meta = {"S":S,"M":M,"midx":midx,"all_machines":all_machines,
                "sku_rows":sku_rows,"xidx":xidx}
        return result.x, meta
