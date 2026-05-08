"""
ETL — load all input data from DB or Excel snapshots.

The DB-backed loaders also write `load_*.xlsx` snapshots (under
`Config.INPUT_DIR`) so the same run can be replayed offline via the
*_from_excel staticmethods.
"""

import ast

import numpy as np
import pandas as pd

from V1.config.settings import Config


# ══════════════════════════════════════════════════════════════════════════════
# ETL
# ══════════════════════════════════════════════════════════════════════════════
class ETL:
    def __init__(self, engine=None, tyre_type: str = "pcr"):
        self.engine = engine
        self.t = tyre_type

    def _sql(self, q): return pd.read_sql(q, self.engine)

    # ── from DB ───────────────────────────────────────────────────────────────
    def load_demand(self, csv_path: str) -> pd.DataFrame:
        df = pd.read_csv(csv_path)
        df = (df.groupby("SKUCode")
                .agg(Quantity=("Updated_Requirement","sum"),
                     Priority=("ConsolidatedPriorityScore","max"))
                .reset_index())

        df = df[df["Quantity"] > 0].copy()
        df.to_excel(Config.LOAD_DEMAND_SNAPSHOT, index=False)
        return df

    def load_cycle_times(self) -> pd.DataFrame:
        df = self._sql(
            f"SELECT Sapcode AS SKUCode, `Cure Time` AS Raw "
            f"FROM {Config.DB_NAME}.Master_Curing_Design_CycleTime_pcr"
        )
        df["CycleTime_min"] = np.round(
            (df["Raw"] + Config.LOAD_UNLOAD_BUFFER_MIN) / Config.PRESS_EFFICIENCY
        )
        df = df[["SKUCode","CycleTime_min"]].drop_duplicates("SKUCode")
        df['SKUCode'] = df['SKUCode'].str.strip()

        df.to_excel(Config.LOAD_CYCLE_TIMES_SNAPSHOT, index=False)
        return df

    def load_machine_allowable(self) -> pd.DataFrame:
        df = self._sql(
            f"SELECT * FROM {Config.DB_NAME}"
            f".Master_Curing_Allowable_Machines_source_pcr"
        )
        df = df.rename(columns={"SKUCode":"SKUCode"})
        mcols = [c for c in df.columns if str(c).isdigit()]
        df["Machines"] = df.apply(
            lambda r: [str(c) for c in mcols if str(r[c]).strip().lower()=="yes"], axis=1
        )
        df = df[["SKUCode","Machines"]]
        #print(df.values)
        #df['Machines'] = df['Machines'].apply(lambda x: [int(i) for i in ast.literal_eval(x)])
        df['Machines'] = df['Machines'].apply(lambda lst: list(map(int, lst)))

        df.to_excel(Config.LOAD_MACHINE_ALLOWABLE_SNAPSHOT, index=False)
        return df

    def load_gt_inventory(self) -> pd.DataFrame:
        df = self._sql(
            f"SELECT ItemCode AS SKUCode, TotalQuantity AS GT_Inventory"
            f" FROM {Config.DB_NAME}.gt_inventory_manual_pcr"
        )

        df.to_excel(Config.LOAD_GT_INVENTORY_SNAPSHOT, index=False)
        return df

    # def load_running_moulds(self) -> pd.DataFrame:
    #     """
    #     Returns one row per machine with columns:
    #         Machine | SKUCode | MouldNos (list) | MouldLife_remaining | Num_Moulds
    #
    #     Each physical press has a LH and RH mould (WCNAME has LH/RH suffix).
    #     We strip the suffix to get the base machine ID, then group BOTH moulds
    #     together into a list.  Machines with only one mould (one side only) are
    #     handled naturally — MouldNos will be a single-element list.
    #
    #     MouldLife_remaining = min(LH_life, RH_life): the press needs cleaning
    #     as soon as EITHER mould expires, so we track the worst-case life.
    #     """
    #
    #     wc_master = self._sql(f"SELECT * FROM {Config.DB_NAME}.Master_WC_Master")
    #
    #     wc_master = wc_master[['wcID', 'WCNAME']]
    #
    #     df = self._sql(f"SELECT * FROM {Config.DB_NAME}.Daily_Running_Moulds_pcr")
    #     # df = df.drop(columns=["updatedAt"])
    #
    #     dff = df[['WCNAME', 'Side','Sapcode', 'Current MouldNo', 'Mould life']]
    #     # dff['Mould life'] = 3000 - dff['Mould life']
    #     dff['Mould life'] = np.where(dff['Mould life']<0, 0, dff['Mould life'])
    #
    #     dff = dff.merge(wc_master, on=['WCNAME'], how='left')
    #     dff['WCNAME'] = dff['WCNAME'].str.replace(r'(LH|RH)$', '', regex=True).str.strip()
    #     dff['curing_machine'] = dff['WCNAME'] + dff['Side']
    #
    #     Running_number_molds_sku = dff.groupby(['Sapcode']).agg({'Current MouldNo':'count'}).reset_index()
    #     Running_number_molds_sku.rename(columns={'Current MouldNo':'Noof_moulds'}, inplace=True)
    #
    #     Running_Moulds = dff[['curing_machine', 'Current MouldNo', 'Sapcode', 'Mould life']]
    #     Running_Moulds.columns = ['WCNAME', 'Current MouldNo', 'Sapcode', 'Mould life']
    #
    #     Running_Moulds['WCNAME'] = Running_Moulds['WCNAME'].str.strip('LH|RH')
    #
    #     Running_Moulds['No'] = 1
    #
    #     grouped = (
    #                 Running_Moulds.groupby("WCNAME")
    #                     .agg(
    #                         SKUCode=("Sapcode", "first"),
    #                         MouldNos=("Current MouldNo", list),
    #                         MouldLife_remaining=("Mould life", "min"),
    #                         Num_Moulds=("No", "count"),
    #                     )
    #                     .reset_index()
    #             )
    #
    #     grouped.columns = ["Machine", "SKUCode", "MouldNos", "MouldLife_remaining", "Num_Moulds"]
    #     #grouped['MouldNos'] = grouped['MouldNos'].apply(ast.literal_eval)
    #
    #     grouped.to_excel("load_running_moulds.xlsx", index=False)
    #     return grouped[["Machine", "SKUCode", "MouldNos", "MouldLife_remaining", "Num_Moulds"]]

    def load_running_moulds(self) -> pd.DataFrame:
        """
        Returns one row per machine with columns:
            Machine | SKUCode | MouldNos (list) | MouldLife_remaining | Num_Moulds

        Updated:
        - Handles new DB format (no Side column)
        - Splits moulds using '#'
        - Recreates Side column (LH/RH) internally
        """

        wc_master = self._sql(f"SELECT * FROM {Config.DB_NAME}.Master_WC_Master")
        wc_master = wc_master[['wcID', 'WCNAME']]

        df = self._sql(f"SELECT * FROM {Config.DB_NAME}.Daily_Running_Moulds_pcr")

        # OLD LOGIC (Side-based) — NOT USED NOW
        # dff = df[['WCNAME', 'Side','Sapcode', 'Current MouldNo', 'Mould life']]

        # NEW LOGIC
        dff = df[['WCNAME', 'Sapcode', 'Current MouldNo', 'Mould life']].copy()

        dff['Mould life'] = np.where(dff['Mould life'] < 0, 0, dff['Mould life'])

        # datatype fix
        dff['WCNAME'] = dff['WCNAME'].astype(str)
        wc_master['WCNAME'] = wc_master['WCNAME'].astype(str)

        dff = dff.merge(wc_master, on=['WCNAME'], how='left')

        dff['WCNAME'] = dff['WCNAME'].str.replace(r'(LH|RH)$', '', regex=True).str.strip()

        # ❌ OLD LOGIC
        # dff['curing_machine'] = dff['WCNAME'] + dff['Side']

        # ✅ NEW LOGIC
        dff['curing_machine'] = dff['WCNAME']

        Running_number_molds_sku = dff.groupby(['Sapcode']).agg({'Current MouldNo': 'count'}).reset_index()
        Running_number_molds_sku.rename(columns={'Current MouldNo': 'Noof_moulds'}, inplace=True)

        Running_Moulds = dff[['curing_machine', 'Current MouldNo', 'Sapcode', 'Mould life']]
        Running_Moulds.columns = ['WCNAME', 'Current MouldNo', 'Sapcode', 'Mould life']

        # =============================================================================
        # ✅ NEW LOGIC: Split moulds + create Side column
        # =============================================================================
        split_df = Running_Moulds.copy()

        split_cols = split_df['Current MouldNo'].str.split('#', n=1, expand=True)

        split_df['Mould1'] = split_cols[0]
        split_df['Mould2'] = split_cols[1] if split_cols.shape[1] > 1 else None

        # LH rows
        df1 = split_df.copy()
        df1['Current MouldNo'] = df1['Mould1']
        df1['Side'] = 'LH'

        # RH rows
        df2 = split_df.copy()
        df2['Current MouldNo'] = df2['Mould2']
        df2['Side'] = 'RH'

        # Combine
        Running_Moulds = pd.concat([df1, df2], ignore_index=True)

        # Remove null moulds (important)
        Running_Moulds = Running_Moulds[Running_Moulds['Current MouldNo'].notna()]

        # Keep columns (Side is kept internally if needed later)
        Running_Moulds = Running_Moulds[['WCNAME', 'Current MouldNo', 'Side', 'Sapcode', 'Mould life']]

        # =============================================================================
        # SAME OLD AGGREGATION (UNCHANGED)
        # =============================================================================
        Running_Moulds['No'] = 1

        grouped = (
            Running_Moulds.groupby("WCNAME")
            .agg(
                SKUCode=("Sapcode", "first"),
                MouldNos=("Current MouldNo", list),
                MouldLife_remaining=("Mould life", "min"),
                Num_Moulds=("No", "count"),
            )
            .reset_index()
        )

        grouped.columns = ["Machine", "SKUCode", "MouldNos", "MouldLife_remaining", "Num_Moulds"]

        grouped.to_excel(Config.LOAD_RUNNING_MOULDS_SNAPSHOT, index=False)

        return grouped[["Machine", "SKUCode", "MouldNos", "MouldLife_remaining", "Num_Moulds"]]

    def load_mould_master(self) -> pd.DataFrame:
        df = self._sql(
            f"SELECT * FROM {Config.DB_NAME}.Master_Mapping_Mould_SKU "
            "WHERE `Active Flag`=True"
        )

        df.to_excel(Config.LOAD_MOULD_MASTER_SNAPSHOT, index=False)
        return df

    # ── from Excel (offline / testing) ────────────────────────────────────────
    @staticmethod
    def load_demand_from_excel(path: str) -> pd.DataFrame:
        df = pd.read_excel(path)
        df = df.rename(columns={"Penetration%":"Priority"})
        return df[df["Quantity"] > 0].copy()

    @staticmethod
    def load_cycle_times_from_excel(path: str) -> pd.DataFrame:
        df = pd.read_excel(path)
        df = df.rename(columns={"Sapcode":"SKUCode","Cure Time":"Raw"})
        df["CycleTime_min"] = np.round(
            (df["Raw"] + Config.LOAD_UNLOAD_BUFFER_MIN) / Config.PRESS_EFFICIENCY
        )
        return df[["SKUCode","CycleTime_min"]].drop_duplicates("SKUCode")

    @staticmethod
    def load_machine_allowable_from_excel(path: str) -> pd.DataFrame:
        df = pd.read_excel(path)
        df["Machines"] = df["Machines"].apply(
            lambda x: ast.literal_eval(x) if isinstance(x, str) else []
        )
        return df[["SKUCode","Machines"]]

    @staticmethod
    def load_gt_inventory_from_excel(path: str) -> pd.DataFrame:
        df = pd.read_excel(path)
        return df.rename(columns={"Sapcode":"SKUCode","ctb_qty":"GT_Inventory"})[
            ["SKUCode","GT_Inventory"]
        ]

    @staticmethod
    def load_mould_master_from_excel(path: str) -> pd.DataFrame:
        df = pd.read_excel(path)
        if "Active Flag" in df.columns:
            df = df[df["Active Flag"].astype(bool)]
        return df

    @staticmethod
    def load_running_moulds_from_excel(path: str) -> pd.DataFrame:
        """
        Reads running moulds from Excel.  The source Excel may have one row
        per WCNAME (i.e. 4501LH and 4501RH as separate rows) matching the DB
        format, or may already be pre-aggregated.  We normalise to one row
        per Machine with MouldNos as a list, same as load_running_moulds().
        """
        df = pd.read_excel(path)
        if "MouldLife_remaining" not in df.columns:
            df["MouldLife_remaining"] = Config.NEW_MOULD_LIFE

        # Detect if WCNAME-style (LH/RH suffixed) or already machine-level
        wcname_col = next((c for c in ["WCNAME", "Machine"] if c in df.columns), None)
        mould_col  = next((c for c in ["Current MouldNo", "MouldNo", "MouldNos"] if c in df.columns), None)
        sku_col    = next((c for c in ["Sapcode", "SKUCode"] if c in df.columns), None)

        if wcname_col is None or mould_col is None or sku_col is None:
            raise ValueError(
                f"Running moulds Excel missing required columns. "
                f"Found: {list(df.columns)}"
            )

        df["Machine"] = (df[wcname_col].astype(str)
                         .str.replace(r"(LH|RH)$", "", regex=True).str.strip())
        df = df.rename(columns={sku_col: "SKUCode", mould_col: "MouldNo"})

        # If MouldNos is already a list column, return as-is
        if df["MouldNo"].dtype == object and isinstance(df["MouldNo"].iloc[0], list):
            return df[["Machine", "SKUCode", "MouldNos", "MouldLife_remaining"]]

        grouped = (
            df.groupby("Machine")
              .agg(
                  SKUCode=("SKUCode", "first"),
                  MouldNos=("MouldNo", list),
                  MouldLife_remaining=("MouldLife_remaining", "min"),
                  Num_Moulds=("MouldNo", "count"),
              )
              .reset_index()
        )
        single = (grouped["Num_Moulds"] == 1).sum()
        double = (grouped["Num_Moulds"] == 2).sum()
        print(f"  [ETL] Running moulds (Excel): {len(grouped)} machines | "
              f"2-mould: {double} | 1-mould: {single}")
        return grouped[["Machine", "SKUCode", "MouldNos", "MouldLife_remaining", "Num_Moulds"]]
