# Curing Schedule Dashboard

Interactive Streamlit dashboard that visualises the Excel output produced by
any of the three schedulers in this repo — **LP**, **MILP**, or **CP-SAT**.
They all produce the same 5-sheet workbook, so this one dashboard works for
any of them.

---

## 1. Install

```bash
pip install streamlit pandas plotly openpyxl
```

That's it — no other services or databases required. The dashboard reads
the Excel file the scheduler already writes.

---

## 2. Generate a schedule first

Pick any of the three algorithm branches and run:

```bash
# LP (main branch)
git checkout main
python3 "btp/Curing/V1 11-37-56-875/jk_curing_lp_PCR.py"

# MILP
git checkout MILP_approach
python3 "btp/Curing/V1 11-37-56-875/jk_curing_milp_PCR.py"

# CP-SAT
git checkout CP_SAT_approach
python3 "btp/Curing/V1 11-37-56-875/jk_curing_cpsat_PCR.py"
```

Each run writes an Excel file like `CTP_PCR_Curing_<ALGO>_v*_PlanSchedule_*.xlsx`.

---

## 3. Launch the dashboard

From the repo root:

```bash
streamlit run dashboard/app.py
```

Streamlit opens automatically at **http://localhost:8501**.

In the sidebar:

- **Pick from repo** — lists every `CTP_*_PlanSchedule_*.xlsx` it can find.
  Most recent first.
- **Upload file** — drag-and-drop any scheduler output.

The dashboard auto-detects which algorithm produced the file (from the
filename) and labels the banner accordingly.

---

## 4. What you'll see

| Tab | Source sheet | Visuals |
|---|---|---|
| **📊 Overview** | all | Status donut, utilisation histogram, top-10 unmet bars |
| **🎯 Demand Fulfillment** | *Demand Fulfillment* | Filterable table + progress-bar fulfilment % per SKU |
| **🏭 Machine Schedule** | *Machine Schedule* | Per-machine rollup + top-25 presses by units bar chart |
| **🕐 Shift Schedule** | *Shift Schedule* | **Interactive Gantt** with date / machine / shift filters |
| **⚙️ Machine Utilisation** | *Machine Utilization* | Utilisation-% bar chart with high/medium/low buckets |
| **🧩 Mould Tracker** | *Mould Tracker* | Free-vs-assigned donut + searchable mould table |

The header always shows live KPIs pulled from the workbook:

- Total demand, planned units, gap
- Fulfilment %, average utilisation %
- Total changeover and mould-cleaning events

---

## 5. File expectations

The dashboard reads sheets by their exact names:

- `Demand Fulfillment`
- `Machine Schedule`
- `Shift Schedule`
- `Machine Utilization`
- `Mould Tracker`

Each sheet has a 2-row banner (title + subtitle) followed by the column
headers, which matches how `ExcelExporter` writes them. If you rename
sheets manually, the dashboard will flag which ones it couldn't find.

---

## 6. Tips

- **Big shift schedules**: the Gantt chart caps at 2,000 rows per render to
  keep the browser snappy. Narrow the date / machine filters and the full
  detail returns.
- **Comparing algorithms**: run LP, MILP and CP-SAT, then switch between
  the three Excel files in the sidebar — the dashboard re-renders with
  KPIs side-by-side in your browser history.
- **Remote access**: Streamlit binds to `localhost` by default. To expose
  it on your LAN, run `streamlit run dashboard/app.py --server.address 0.0.0.0`.

---

## 7. Stopping

Hit `Ctrl+C` in the terminal running `streamlit run`. Reload the browser
to reconnect after a fresh start.
