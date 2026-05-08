"""
MouldTracker — mould availability, compatibility and life ledger.
"""

import pandas as pd

from V1.config.settings import Config


# ══════════════════════════════════════════════════════════════════════════════
# MOULD TRACKER
# ══════════════════════════════════════════════════════════════════════════════
class MouldTracker:
    """
    Tracks mould availability, compatibility and life.

    Each mould has:
        compatible_skus  : set of SKU codes this mould can produce
        life_remaining   : cycles remaining before cleaning (default 3000)
        assigned_machine : machine currently using this mould (None = free)

    Rules enforced:
        - A (machine, SKU) assignment is only valid if ≥ MOULDS_PER_PRESS
          compatible, unassigned moulds exist for that SKU.
        - When a mould is assigned, it is locked to that machine.
        - After 3000 cycles the mould needs cleaning (120 min) — tracked in
          ScheduleBuilder.
    """

    def __init__(self):
        # mould_id -> dict
        self._ledger: dict[str, dict] = {}

    def load_from_df(self, df_mould: pd.DataFrame, df_running: pd.DataFrame):
        """
        Initialise ledger from:
          df_mould   : Master_Mapping_Mould_SKU (MouldNo, Matl.Code, Active Flag)
          df_running : currently running moulds (Machine, SKUCode, MouldNo, MouldLife_remaining)
        """
        # Build base ledger from mould master
        mould_col = "MouldNo" if "MouldNo" in df_mould.columns else "Mould"
        for _, row in df_mould.iterrows():
            mid = str(row[mould_col])
            sku = str(row["Matl.Code"])
            if mid not in self._ledger:
                self._ledger[mid] = {
                    "compatible_skus":   set(),
                    "life_remaining":    Config.NEW_MOULD_LIFE,
                    "assigned_machine":  None,
                }
            self._ledger[mid]["compatible_skus"].add(sku)

        # Update life and assignment from running moulds
        # df_running now has MouldNos as a list (both LH and RH moulds per machine)
        for _, row in df_running.iterrows():
            machine = str(row["Machine"])
            life    = int(row.get("MouldLife_remaining", Config.NEW_MOULD_LIFE))

            # Support both old single-mould (MouldNo) and new list (MouldNos) format
            raw = row.get("MouldNos", row.get("MouldNo", None))
            if raw is None:
                continue
            moulds = raw if isinstance(raw, list) else [str(raw)]

            for mould in moulds:
                mould = str(mould).strip()
                if mould in self._ledger:
                    self._ledger[mould]["life_remaining"]   = life
                    self._ledger[mould]["assigned_machine"] = machine

    def load_from_excel(self, mould_path: str, running_path: str = None):
        df_mould = pd.read_excel(mould_path)
        df_mould = df_mould[df_mould.get("Active Flag", pd.Series([True]*len(df_mould))).astype(bool)]
        df_mould = df_mould.rename(columns={"Matl.Code": "Matl.Code"})
        # Build simplified running df if path given
        if running_path:
            df_running = pd.read_excel(running_path)
            if "MouldLife_remaining" not in df_running.columns:
                df_running["MouldLife_remaining"] = Config.NEW_MOULD_LIFE
        else:
            df_running = pd.DataFrame(columns=["Machine", "SKUCode", "MouldNo", "MouldLife_remaining"])
        self.load_from_df(df_mould, df_running)

    def available_moulds_for_sku(self, sku: str) -> list[str]:
        """Return list of free mould IDs compatible with this SKU."""
        return [
            mid for mid, data in self._ledger.items()
            if sku in data["compatible_skus"]
            and data["assigned_machine"] is None
        ]

    def can_assign(self, sku: str) -> bool:
        """True if ≥ MOULDS_PER_PRESS free compatible moulds exist."""
        return len(self.available_moulds_for_sku(sku)) >= Config.MOULDS_PER_PRESS

    def get_eligible_machines_with_moulds(
        self, sku: str, candidate_machines: list[str]
    ) -> list[str]:
        """
        Filter candidate machines to those where a valid mould assignment
        is possible — i.e. free moulds exist for the SKU.
        (Machine-level physical compatibility is already in allowable matrix;
        this layer adds mould pool feasibility.)
        """
        if not self.can_assign(sku):
            return []
        return candidate_machines

    def assign_moulds(self, sku: str, machine: str) -> list[str]:
        """
        Assign MOULDS_PER_PRESS moulds to a machine for a SKU.
        Returns the list of assigned mould IDs.
        Raises ValueError if insufficient moulds available.
        """
        avail = self.available_moulds_for_sku(sku)
        if len(avail) < Config.MOULDS_PER_PRESS:
            raise ValueError(
                f"Cannot assign {Config.MOULDS_PER_PRESS} moulds for "
                f"SKU={sku} on Machine={machine}: only {len(avail)} free"
            )
        chosen = sorted(avail, key=lambda m: -self._ledger[m]["life_remaining"])[: Config.MOULDS_PER_PRESS]
        for mid in chosen:
            self._ledger[mid]["assigned_machine"] = machine
        return chosen

    def release_moulds(self, mould_ids: list[str]):
        """Free moulds at end of run so they can be re-assigned."""
        for mid in mould_ids:
            if mid in self._ledger:
                self._ledger[mid]["assigned_machine"] = None

    def mould_life(self, mould_id: str) -> int:
        return self._ledger.get(mould_id, {}).get("life_remaining", Config.NEW_MOULD_LIFE)

    def avg_life_remaining_for_sku(self, sku: str) -> float:
        moulds = self.available_moulds_for_sku(sku)
        if not moulds:
            return 0.0
        return sum(self._ledger[m]["life_remaining"] for m in moulds) / len(moulds)

    @property
    def summary(self) -> pd.DataFrame:
        rows = []
        for mid, d in self._ledger.items():
            rows.append({
                "MouldNo":          mid,
                "Compatible_SKUs":  ", ".join(sorted(d["compatible_skus"])),
                "Life_Remaining":   d["life_remaining"],
                "Assigned_Machine": d["assigned_machine"] or "FREE",
            })
        return pd.DataFrame(rows)
