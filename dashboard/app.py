"""
JK Tyre BTP — PCR Curing Schedule Dashboard
===========================================
Interactive Streamlit dashboard that visualises the 5-sheet Excel output
produced by any of the three schedulers (LP / MILP / CP-SAT). The schedulers
all emit the same workbook structure, so this app works for all of them.

Run:
    pip install streamlit pandas plotly openpyxl
    streamlit run dashboard/app.py

Opens automatically at http://localhost:8501
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ══════════════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="JK Tyre BTP — Curing Schedule Dashboard",
    page_icon="🛞",
    layout="wide",
    initial_sidebar_state="expanded",
)

PRIMARY = "#1F3864"
TEAL = "#1F6B75"
GREEN = "#2E8540"
AMBER = "#F2B705"
RED = "#C0392B"
GREY = "#7F8C8D"

STATUS_COLORS = {
    "FULLY MET": GREEN,
    "PARTIAL": AMBER,
    "UNMET": RED,
    "UNSCHEDULABLE": GREY,
}

SHIFT_COLORS = {
    "A": "#4C9AC4",
    "B": "#E8B339",
    "C": "#9BA4A8",
    "CHANGEOVER": "#E67E22",
    "MOULD_CLEAN": "#F4C24A",
}


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════
EXPECTED_SHEETS = [
    "Demand Fulfillment",
    "Machine Schedule",
    "Shift Schedule",
    "Machine Utilization",
    "Mould Tracker",
]


@st.cache_data(show_spinner="Reading Excel workbook…")
def load_workbook(path_or_buffer) -> dict[str, pd.DataFrame]:
    """Read all 5 sheets from the scheduler's Excel output.

    The first two rows are title/subtitle banners and the third is the header
    row, so we skip the first 2 rows and let pandas infer the header from
    row 3.
    """
    sheets: dict[str, pd.DataFrame] = {}
    for sheet in EXPECTED_SHEETS:
        try:
            df = pd.read_excel(path_or_buffer, sheet_name=sheet, skiprows=2)
            df = df.dropna(axis=0, how="all").reset_index(drop=True)
            # Drop total footer row if present
            if "SKUCode" in df.columns:
                df = df[df["SKUCode"].astype(str) != "TOTAL"].reset_index(drop=True)
            sheets[sheet] = df
        except Exception as exc:
            st.warning(f"Could not read sheet '{sheet}': {exc}")
            sheets[sheet] = pd.DataFrame()
    return sheets


def find_recent_outputs() -> list[Path]:
    """Look for scheduler output Excel files in common locations."""
    repo_root = Path(__file__).resolve().parent.parent
    patterns = ["CTP_*_PlanSchedule_*.xlsx", "*_Curing_*_Schedule*.xlsx"]
    found: list[Path] = []
    for base in [repo_root, repo_root / "btp", repo_root / "btp" / "Curing"]:
        if base.exists():
            for pat in patterns:
                found.extend(base.rglob(pat))
    # Keep deterministic order, newest first
    found = sorted(set(found), key=lambda p: p.stat().st_mtime, reverse=True)
    return found


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR — FILE PICKER
# ══════════════════════════════════════════════════════════════════════════════
st.sidebar.markdown(
    f"<h2 style='color:{PRIMARY};margin-bottom:0'>🛞 Curing Schedule</h2>"
    "<p style='color:#666;margin-top:4px'>JK Tyre BTP — PCR Dashboard</p>",
    unsafe_allow_html=True,
)
st.sidebar.divider()

source = st.sidebar.radio(
    "**Data source**",
    ["Pick from repo", "Upload file"],
    horizontal=True,
    label_visibility="visible",
)

workbook_bytes = None
selected_path: Path | None = None

if source == "Pick from repo":
    candidates = find_recent_outputs()
    if not candidates:
        st.sidebar.info(
            "No schedule Excel files found. Either upload one, or run a "
            "scheduler (`jk_curing_lp_PCR.py`, `_milp_`, or `_cpsat_`) "
            "and refresh this page."
        )
    else:
        display = {p.name: p for p in candidates}
        choice = st.sidebar.selectbox("Recent outputs", list(display.keys()))
        selected_path = display[choice]
        st.sidebar.caption(f"Path: `{selected_path}`")
else:
    uploaded = st.sidebar.file_uploader(
        "Upload scheduler Excel output (.xlsx)", type=["xlsx"]
    )
    if uploaded is not None:
        workbook_bytes = uploaded

if selected_path is None and workbook_bytes is None:
    st.markdown(
        f"<h1 style='color:{PRIMARY}'>JK Tyre BTP — Curing Schedule Dashboard</h1>",
        unsafe_allow_html=True,
    )
    st.info(
        "👈 Select a scheduler output file from the sidebar to begin. "
        "Compatible with LP, MILP, and CP-SAT outputs (all share the same "
        "5-sheet format)."
    )
    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# LOAD DATA
# ══════════════════════════════════════════════════════════════════════════════
sheets = load_workbook(selected_path if selected_path else workbook_bytes)
df_demand = sheets.get("Demand Fulfillment", pd.DataFrame())
df_mach = sheets.get("Machine Schedule", pd.DataFrame())
df_shift = sheets.get("Shift Schedule", pd.DataFrame())
df_util = sheets.get("Machine Utilization", pd.DataFrame())
df_mould = sheets.get("Mould Tracker", pd.DataFrame())


# Detect which algorithm produced the file (best-effort from filename)
algo_label = "Unknown"
if selected_path:
    name_up = selected_path.name.upper()
    if "CPSAT" in name_up:
        algo_label = "CP-SAT"
    elif "MILP" in name_up:
        algo_label = "MILP"
    elif "LP" in name_up:
        algo_label = "LP"


# ══════════════════════════════════════════════════════════════════════════════
# HEADER + KPIS
# ══════════════════════════════════════════════════════════════════════════════
st.markdown(
    f"<h1 style='color:{PRIMARY};margin-bottom:0'>JK Tyre BTP — PCR Curing Schedule</h1>"
    f"<p style='color:#555;font-size:0.95rem;margin-top:4px'>"
    f"<b>Algorithm:</b> {algo_label}"
    f" &nbsp;|&nbsp; <b>Source:</b> <code>{selected_path.name if selected_path else 'uploaded file'}</code></p>",
    unsafe_allow_html=True,
)

st.divider()

# Compute KPIs
total_demand = int(df_demand["Demand"].sum()) if "Demand" in df_demand else 0
total_planned = int(df_demand["Planned_Units"].sum()) if "Planned_Units" in df_demand else 0
gap = max(total_demand - total_planned, 0)
fulfillment = (total_planned / total_demand * 100) if total_demand > 0 else 0.0
avg_util = float(df_util["Utilization_Pct"].mean()) if "Utilization_Pct" in df_util else 0.0
n_changeovers = int((df_shift["SKUCode"] == "CHANGEOVER").sum()) if "SKUCode" in df_shift else 0
n_mould_cleans = int((df_shift["SKUCode"] == "MOULD_CLEAN").sum()) if "SKUCode" in df_shift else 0

kpi_cols = st.columns(7)
kpi_cols[0].metric("Total Demand", f"{total_demand:,}")
kpi_cols[1].metric("Planned", f"{total_planned:,}")
kpi_cols[2].metric("Gap", f"{gap:,}", delta=None if gap == 0 else f"-{gap:,}", delta_color="inverse")
kpi_cols[3].metric("Fulfillment", f"{fulfillment:.1f}%")
kpi_cols[4].metric("Avg Utilisation", f"{avg_util:.1f}%")
kpi_cols[5].metric("Changeovers", f"{n_changeovers}")
kpi_cols[6].metric("Mould Cleans", f"{n_mould_cleans}")


# ══════════════════════════════════════════════════════════════════════════════
# TABS — one per Excel sheet + an "Overview"
# ══════════════════════════════════════════════════════════════════════════════
tabs = st.tabs(
    [
        "📊 Overview",
        "🎯 Demand Fulfillment",
        "🏭 Machine Schedule",
        "🕐 Shift Schedule",
        "⚙️ Machine Utilisation",
        "🧩 Mould Tracker",
    ]
)


# ──────────────────────────────────────────────────────────────────────────────
# Tab 0 : OVERVIEW
# ──────────────────────────────────────────────────────────────────────────────
with tabs[0]:
    st.subheader("Plan-wide summary")

    c1, c2 = st.columns([1, 1])

    # Status pie
    if not df_demand.empty and "Status" in df_demand.columns:
        status_counts = df_demand["Status"].value_counts().reset_index()
        status_counts.columns = ["Status", "Count"]
        fig = px.pie(
            status_counts,
            names="Status",
            values="Count",
            color="Status",
            color_discrete_map=STATUS_COLORS,
            hole=0.5,
            title="Demand fulfilment status (SKU count)",
        )
        fig.update_traces(textposition="inside", textinfo="label+percent")
        fig.update_layout(height=380, showlegend=True)
        c1.plotly_chart(fig, use_container_width=True)

    # Util histogram
    if not df_util.empty and "Utilization_Pct" in df_util.columns:
        fig = px.histogram(
            df_util,
            x="Utilization_Pct",
            nbins=20,
            title="Machine utilisation distribution",
            color_discrete_sequence=[PRIMARY],
        )
        fig.update_layout(height=380, xaxis_title="Utilisation %", yaxis_title="# machines")
        fig.add_vline(
            x=avg_util,
            line_dash="dash",
            line_color=AMBER,
            annotation_text=f"Avg {avg_util:.1f}%",
        )
        c2.plotly_chart(fig, use_container_width=True)

    # Top unmet
    if not df_demand.empty and "Gap" in df_demand.columns:
        top_unmet = (
            df_demand[df_demand["Gap"] > 0]
            .sort_values("Gap", ascending=False)
            .head(10)
        )
        if not top_unmet.empty:
            st.subheader("Top 10 SKUs with unmet demand")
            fig = px.bar(
                top_unmet,
                x="Gap",
                y="SKUCode",
                orientation="h",
                color="Status",
                color_discrete_map=STATUS_COLORS,
                text="Gap",
                hover_data=["Demand", "Planned_Units", "Fulfillment_Pct"],
            )
            fig.update_traces(texttemplate="%{text:,}", textposition="outside")
            fig.update_layout(
                height=380,
                yaxis={"categoryorder": "total ascending"},
                xaxis_title="Units short",
                yaxis_title=None,
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.success("🎉 All schedulable SKU demand is met.")


# ──────────────────────────────────────────────────────────────────────────────
# Tab 1 : DEMAND FULFILLMENT
# ──────────────────────────────────────────────────────────────────────────────
with tabs[1]:
    st.subheader("Demand fulfilment detail")

    if df_demand.empty:
        st.info("Demand fulfilment sheet is empty.")
    else:
        f1, f2 = st.columns([1, 3])
        status_filter = f1.multiselect(
            "Filter by status",
            options=df_demand["Status"].dropna().unique().tolist(),
            default=df_demand["Status"].dropna().unique().tolist(),
        )
        search = f2.text_input("Search SKU code", "")
        view = df_demand[df_demand["Status"].isin(status_filter)]
        if search:
            view = view[view["SKUCode"].astype(str).str.contains(search, case=False, na=False)]

        st.dataframe(
            view,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Fulfillment_Pct": st.column_config.ProgressColumn(
                    "Fulfilment %", format="%.1f%%", min_value=0, max_value=100
                ),
                "Demand": st.column_config.NumberColumn(format="%d"),
                "Planned_Units": st.column_config.NumberColumn(format="%d"),
                "Gap": st.column_config.NumberColumn(format="%d"),
                "Priority": st.column_config.NumberColumn(format="%.3f"),
            },
            height=500,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Tab 2 : MACHINE SCHEDULE
# ──────────────────────────────────────────────────────────────────────────────
with tabs[2]:
    st.subheader("Machine-wise allocation (solver output)")

    if df_mach.empty:
        st.info("Machine schedule sheet is empty.")
    else:
        summary = (
            df_mach.groupby("Machine")
            .agg(
                SKUs=("SKUCode", "nunique"),
                Cycles=("Cycles", "sum"),
                Units=("Units_Planned", "sum"),
                MinsUsed=("Mins_Used", "sum"),
            )
            .reset_index()
            .sort_values("Units", ascending=False)
        )

        c1, c2 = st.columns([2, 3])
        with c1:
            st.markdown("**Per-machine rollup**")
            st.dataframe(summary, use_container_width=True, hide_index=True, height=420)

        with c2:
            fig = px.bar(
                summary.head(25),
                x="Machine",
                y="Units",
                color="SKUs",
                title="Top 25 presses by planned units (colour = # distinct SKUs)",
                color_continuous_scale="Viridis",
            )
            fig.update_layout(height=420, xaxis_title=None, yaxis_title="Units planned")
            fig.update_xaxes(type="category")
            st.plotly_chart(fig, use_container_width=True)

        st.markdown("**Full machine schedule**")
        sel = st.multiselect(
            "Filter by machine",
            options=sorted(df_mach["Machine"].unique().tolist()),
            default=[],
        )
        view = df_mach if not sel else df_mach[df_mach["Machine"].isin(sel)]
        st.dataframe(view, use_container_width=True, hide_index=True, height=440)


# ──────────────────────────────────────────────────────────────────────────────
# Tab 3 : SHIFT SCHEDULE (gantt)
# ──────────────────────────────────────────────────────────────────────────────
with tabs[3]:
    st.subheader("Shift-wise timeline")

    if df_shift.empty:
        st.info("Shift schedule sheet is empty.")
    else:
        # Coerce datetimes
        df_s = df_shift.copy()
        df_s["StartTime"] = pd.to_datetime(df_s["StartTime"], errors="coerce")
        df_s["EndTime"] = pd.to_datetime(df_s["EndTime"], errors="coerce")
        df_s = df_s.dropna(subset=["StartTime", "EndTime"])

        # Filters
        f1, f2, f3 = st.columns(3)
        date_range = f1.date_input(
            "Date range",
            value=(df_s["StartTime"].min().date(), df_s["StartTime"].max().date()),
        )
        machine_pick = f2.multiselect(
            "Machines (leave empty = all)",
            options=sorted(df_s["Machine"].astype(str).unique().tolist()),
            default=[],
        )
        shift_pick = f3.multiselect(
            "Shifts",
            options=["A", "B", "C"],
            default=["A", "B", "C"],
        )

        view = df_s.copy()
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start, end = date_range
            view = view[
                (view["StartTime"].dt.date >= start)
                & (view["StartTime"].dt.date <= end)
            ]
        if machine_pick:
            view = view[view["Machine"].astype(str).isin(machine_pick)]
        view = view[view["Shift"].isin(shift_pick) | view["SKUCode"].isin(["CHANGEOVER", "MOULD_CLEAN"])]

        # Gantt (limit to avoid browser slowdown)
        max_rows = 2000
        if len(view) > max_rows:
            st.caption(
                f"⚠️ Showing first {max_rows:,} of {len(view):,} rows in the gantt. "
                "Narrow the filters above for more detail."
            )
            gantt_view = view.head(max_rows)
        else:
            gantt_view = view

        if not gantt_view.empty:
            gantt_view = gantt_view.copy()
            gantt_view["Label"] = gantt_view.apply(
                lambda r: "CHANGEOVER" if r["SKUCode"] == "CHANGEOVER"
                else ("MOULD_CLEAN" if r["SKUCode"] == "MOULD_CLEAN" else r["Shift"]),
                axis=1,
            )
            fig = px.timeline(
                gantt_view,
                x_start="StartTime",
                x_end="EndTime",
                y=gantt_view["Machine"].astype(str),
                color="Label",
                color_discrete_map=SHIFT_COLORS,
                hover_data=["SKUCode", "Qty", "CycleTime_min", "Remarks"],
            )
            fig.update_yaxes(autorange="reversed", title=None)
            fig.update_layout(height=700, legend_title="Shift / Event")
            st.plotly_chart(fig, use_container_width=True)

        # Raw table
        with st.expander("Raw shift-level rows"):
            st.dataframe(view, use_container_width=True, hide_index=True, height=420)


# ──────────────────────────────────────────────────────────────────────────────
# Tab 4 : MACHINE UTILISATION
# ──────────────────────────────────────────────────────────────────────────────
with tabs[4]:
    st.subheader("Press utilisation")

    if df_util.empty:
        st.info("Machine utilisation sheet is empty.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total presses", f"{len(df_util)}")
        high = int((df_util["Utilization_Pct"] >= 90).sum())
        med = int(((df_util["Utilization_Pct"] >= 60) & (df_util["Utilization_Pct"] < 90)).sum())
        low = int((df_util["Utilization_Pct"] < 60).sum())
        c2.metric("High ≥ 90 %", f"{high}")
        c3.metric("Med 60–89 %", f"{med}")
        c4.metric("Low < 60 %", f"{low}")

        df_u = df_util.copy().sort_values("Utilization_Pct", ascending=False)
        df_u["Bucket"] = pd.cut(
            df_u["Utilization_Pct"],
            bins=[-0.01, 60, 90, 101],
            labels=["Low (<60)", "Medium (60–89)", "High (≥90)"],
        )
        color_map = {
            "Low (<60)": RED,
            "Medium (60–89)": AMBER,
            "High (≥90)": GREEN,
        }

        fig = px.bar(
            df_u,
            x="Machine",
            y="Utilization_Pct",
            color="Bucket",
            color_discrete_map=color_map,
            title="Utilisation % per press",
            hover_data=["Used_Mins", "Idle_Mins", "SKUs_Count", "Total_Units"],
        )
        fig.update_xaxes(type="category", title=None)
        fig.update_yaxes(title="Utilisation %")
        fig.update_layout(height=480, legend_title="Utilisation bucket")
        st.plotly_chart(fig, use_container_width=True)

        st.dataframe(
            df_u.drop(columns=["Bucket"]),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Utilization_Pct": st.column_config.ProgressColumn(
                    "Utilisation %", format="%.1f%%", min_value=0, max_value=100
                ),
                "Available_Mins": st.column_config.NumberColumn(format="%d"),
                "Used_Mins": st.column_config.NumberColumn(format="%d"),
                "Idle_Mins": st.column_config.NumberColumn(format="%d"),
                "Total_Units": st.column_config.NumberColumn(format="%d"),
            },
            height=500,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Tab 5 : MOULD TRACKER
# ──────────────────────────────────────────────────────────────────────────────
with tabs[5]:
    st.subheader("Mould availability")

    if df_mould.empty:
        st.info("Mould tracker sheet is empty.")
    else:
        total = len(df_mould)
        free = int((df_mould["Assigned_Machine"].astype(str).str.upper() == "FREE").sum())
        assigned = total - free
        c1, c2, c3 = st.columns(3)
        c1.metric("Total moulds", f"{total:,}")
        c2.metric("Free", f"{free:,}", delta=f"{free/total*100:.1f}%" if total else None)
        c3.metric("Assigned", f"{assigned:,}", delta=f"{assigned/total*100:.1f}%" if total else None)

        # Pie
        pie = pd.DataFrame({"State": ["Free", "Assigned"], "Count": [free, assigned]})
        fig = px.pie(
            pie,
            names="State",
            values="Count",
            color="State",
            color_discrete_map={"Free": GREEN, "Assigned": AMBER},
            hole=0.55,
            title="Free vs assigned",
        )
        fig.update_layout(height=360)
        st.plotly_chart(fig, use_container_width=True)

        search = st.text_input("Search mould / SKU")
        view = df_mould
        if search:
            mask = False
            for col in view.columns:
                mask = mask | view[col].astype(str).str.contains(search, case=False, na=False)
            view = view[mask]
        st.dataframe(view, use_container_width=True, hide_index=True, height=500)


# ══════════════════════════════════════════════════════════════════════════════
# FOOTER
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
st.caption(
    f"Dashboard rendered from **{algo_label}** scheduler output. "
    "Same 5-sheet format is produced by the LP, MILP, and CP-SAT schedulers — "
    "this app works for any of them. "
    f"Source file: `{selected_path if selected_path else 'uploaded'}`"
)
