# jk-btp-lp-scheduler

JK TYRE & INDUSTRIES LTD. 

Banmore Tyre Plant  |  PCR Curing Department 

 

Technical & Architecture Documentation 

BTP PCR Curing LP Scheduler 

Version 1.0 

A Linear Programming-based Monthly Curing Schedule Optimizer 

 

Designed By 

Paranjay Dodiya 

Organisation 

Algo8 AI Pvt. Ltd. 

Client 

JK Tyre & Industries Ltd. — Banmore Tyre Plant (BTP) 

Document Type 

Technical Design Document + Architecture Reference 

Tyre Type 

PCR (Passenger Car Radial) 

Version 

v1.0 — Production Ready 

Date 

April 2026 

Status 

Final 

 

Table of Contents 

​​ 

​​ 

 

1. Executive Summary 

The BTP PCR Curing LP Scheduler (v2) is a production-grade, Python-based scheduling engine that computes an optimal monthly curing plan for Passenger Car Radial (PCR) tyres at JK Tyre's Banmore Tyre Plant. The system replaces manual planning with a mathematically optimal, constraint-aware allocation algorithm grounded in Linear Programming (LP). 

At its core, the scheduler solves a continuous LP to allocate press-minutes across machines and SKUs, then rounds the solution to integer production cycles, sequences changeovers and mould-cleaning events, and emits a fully formatted, shift-granular Excel schedule. 

 

Key Capabilities 

•  LP-optimal press-minute allocation across all machines and SKUs simultaneously 

•  Changeover penalty in LP objective to minimise mould switches and production disruptions 

•  Continuity scheduling: currently running moulds are locked and scheduled first 

•  Mould lifecycle tracking: cleaning events inserted every 6,000 units (3,000 cycles × 2 moulds) 

•  Shift-level schedule output with A/B/C shift breakdown (07:00–15:00, 15:00–23:00, 23:00–07:00) 

•  Plant-wide changeover cap: maximum 4 changeovers per shift across all presses 

•  Five-sheet Excel export: Demand Fulfillment, Machine Schedule, Shift Schedule, Utilization, Mould Tracker 

 

1.1 Problem Statement 

Planning curing press schedules manually for 30+ machines, 100+ SKUs, and a 30-day horizon is computationally intractable for human planners. Suboptimal scheduling leads to: 

Unmet customer demand due to poor press allocation 

Excessive mould changeovers increasing downtime 

Mould cleaning events missed, causing quality defects 

Inefficient press utilisation with some presses idle while demand goes unmet 

 

1.2 Solution Approach 

The scheduler solves this as a five-phase pipeline: ETL → LP Solve → Rounding → Schedule Build → Export. The LP minimises unmet demand-minutes subject to machine capacity and SKU eligibility constraints, producing a globally optimal allocation. Post-LP rounding converts continuous allocations to physically meaningful integer cycles, and the ScheduleBuilder sequences the resulting production blocks into a shift-granular timeline. 

 

2. System Architecture 

The scheduler follows a linear pipeline architecture. Each phase is encapsulated in a dedicated class with a single well-defined responsibility. Data flows forward through the pipeline; no phase depends on outputs of a later phase. 

 

2.1 Pipeline Overview 

Phase 

Name 

Module / Class 

Output 

0 

ETL 

ETL (class) 

Raw DataFrames from DB or Excel 

1 

SKU Preparation 

JK_LP_Curing_Scheduler_v2._prepare_skus() 

df_valid — filtered, enriched SKU table 

2 

Continuity Build 

JK_LP_Curing_Scheduler_v2._build_continuity() 

continuity_rows, locked_mins, demand_remainder 

3 

LP Solve 

LP_Solver.solve() 

x[] — continuous press-minute allocations 

4 

Rounding 

Rounder.round() 

df_mach — integer cycle allocation + run order 

5 

Schedule Build 

ScheduleBuilder.build() 

df_shift — shift-granular timeline 

6 

Summary & Export 

ExcelExporter.export() 

5-sheet XLSX file 

 

2.2 High-Level Architecture Diagram 

┌──────────────────────────────────────────────────────┐ 

│            DATA SOURCES                              │ 

│   MySQL DB ──┬── ETL ──── Excel Files (offline)      │ 

└─────────────┼────────────────────────────────────────┘ 

              │  5 DataFrames (demand, cycles, 

              │  allowable, GT inventory, moulds) 

              ▼ 

┌─────────────────────────────────────────────────────┐ 

│  PHASE 1: SKU Preparation  (_prepare_skus)          │ 

│  Filter schedulable SKUs, compute demand_mins       │ 

└──────────────────────┬──────────────────────────────┘ 

                       ▼ 

┌─────────────────────────────────────────────────────┐ 

│  PHASE 2: Continuity Build  (_build_continuity)     │ 

│  Lock running moulds → rows + locked_mins           │ 

└──────────────────────┬──────────────────────────────┘ 

                       ▼  (residual demand + reduced capacity) 

┌─────────────────────────────────────────────────────┐ 

│  PHASE 3: LP Solve  (LP_Solver)                     │ 

│  HiGHS solver → continuous x[s,m] allocations      │ 

└──────────────────────┬──────────────────────────────┘ 

                       ▼ 

┌─────────────────────────────────────────────────────┐ 

│  PHASE 4: Rounding  (Rounder)                       │ 

│  Floor to integer cycles + priority top-up          │ 

└──────────────────────┬──────────────────────────────┘ 

                       ▼ 

┌─────────────────────────────────────────────────────┐ 

│  PHASE 5: Schedule Build  (ScheduleBuilder)         │ 

│  Shift-rows + CHANGEOVER + MOULD_CLEAN events       │ 

└──────────────────────┬──────────────────────────────┘ 

                       ▼ 

┌─────────────────────────────────────────────────────┐ 

│  PHASE 6: Excel Export  (ExcelExporter)             │ 

└─────────────────────────────────────────────────────┘ 

 

2.3 Class Dependency Map 

The following classes are defined in the system. Arrows indicate usage/instantiation: 

Config — global constants consumed by all classes 

MouldTracker — instantiated once; passed into LP_Solver and used in _prepare_skus 

ETL — standalone loader; produces DataFrames consumed by orchestrator 

LP_Solver — instantiated per run inside JK_LP_Curing_Scheduler_v2.run() 

Rounder — instantiated per run; consumes LP_Solver output 

ScheduleBuilder — instantiated per run; consumes Rounder output 

JK_LP_Curing_Scheduler_v2 — top-level orchestrator; owns phases 1–5 

ExcelExporter — consumes results dict returned by orchestrator 

 

3. Configuration Reference 

All tuneable parameters are centralised in the Config class. Planners can adjust scheduling behaviour without touching algorithmic code. 

 

Parameter 

Default 

Unit 

Description 

PLANNING_DAYS 

30 

days 

Length of the scheduling horizon 

SHIFTS_PER_DAY 

3 

— 

Number of 8-hour shifts per calendar day 

HOURS_PER_SHIFT 

8 

hours 

Duration of each production shift 

SHIFT_START_HOUR 

7 

h (24h) 

Shift A begins at 07:00; B at 15:00; C at 23:00 

CAVITIES_PER_MOULD 

2 

tyres/cycle 

Number of tyres cured per press cycle 

MOULDS_PER_PRESS 

2 

moulds 

LH + RH mould pair per press 

NEW_MOULD_LIFE 

3000 

cycles 

Cycles before mould requires cleaning 

CHANGEOVER_DURATION_MIN 

300 

min 

240 min changeover + 60 min FTC check 

CLEANING_DURATION_MIN 

120 

min 

Duration of a mould cleaning event 

LOAD_UNLOAD_BUFFER_MIN 

2.3 

min 

Added to raw cure time for L/UL overhead 

PRESS_EFFICIENCY 

1.0 

ratio 

Divisor for warmup/transition losses (currently 100%) 

MAX_CHANGEOVERS_PER_SHIFT 

4 

events 

Plant-level cap on COs per 8-hour shift 

CHANGEOVER_PENALTY_WEIGHT 

0.01 

— 

LP objective weight for (machine, SKU) pair penalty 

TYRE_TYPE 

'pcr' 

— 

Tyre type selector (used in DB queries) 

 

📌  avail_mins() = PLANNING_DAYS × SHIFTS_PER_DAY × HOURS_PER_SHIFT × 60 = 30 × 3 × 8 × 60 = 43,200 minutes per press for the default 30-day horizon. 

 

📌  units_per_cleaning_cycle() = NEW_MOULD_LIFE × CAVITIES_PER_MOULD × MOULDS_PER_PRESS = 3,000 × 2 × 2 = 12,000 units. However, the code tracks cleaning at 6,000 units (per press, per pair) by using NEW_MOULD_LIFE × CAVITIES_PER_MOULD internally. 

 

4. Class & Method Reference 

4.1  Config 

Static configuration class. All values are class-level attributes. Two class methods compute derived constants. 

Method / Attribute 

Signature / Type 

Description 

DB_SERVER / DB_NAME / DB_USER / DB_PASSWORD 

str 

MySQL connection parameters 

PLANNING_DAYS 

int = 30 

Planning horizon length in calendar days 

SHIFTS_PER_DAY / HOURS_PER_SHIFT / SHIFT_START_HOUR 

int 

Shift structure definition 

CAVITIES_PER_MOULD / MOULDS_PER_PRESS / NEW_MOULD_LIFE 

int 

Press and mould physical parameters 

CHANGEOVER_DURATION_MIN / CLEANING_DURATION_MIN 

int (minutes) 

Downtime constants 

LOAD_UNLOAD_BUFFER_MIN / PRESS_EFFICIENCY 

float 

Cycle-time adjustment factors 

MAX_CHANGEOVERS_PER_SHIFT 

int = 4 

Plant-wide CO cap per shift 

CHANGEOVER_PENALTY_WEIGHT 

float = 0.01 

LP objective penalty coefficient 

avail_mins() → float 

class method 

Total available press-minutes per machine over horizon 

units_per_cleaning_cycle() → int 

class method 

Units before a full press cleaning is required 

 

4.2  MouldTracker 

Tracks mould availability, SKU compatibility, lifecycle remaining, and machine assignment. Acts as a validation layer between LP eligibility and physical mould inventory. 

Method / Attribute 

Signature / Type 

Description 

_ledger: dict[str, dict] 

Private 

mould_id → {compatible_skus, life_remaining, assigned_machine} 

load_from_df(df_mould, df_running) 

void 

Initialise ledger from Master_Mapping_Mould_SKU and Daily_Running_Moulds DataFrames 

load_from_excel(mould_path, running_path) 

void 

Convenience wrapper — reads Excel files, delegates to load_from_df 

available_moulds_for_sku(sku) → list[str] 

str list 

Returns mould IDs that are compatible with sku AND unassigned 

can_assign(sku) → bool 

bool 

True if ≥ MOULDS_PER_PRESS free moulds available for sku 

get_eligible_machines_with_moulds(sku, machines) → list 

list[str] 

Filters candidate machines by mould pool feasibility 

assign_moulds(sku, machine) → list[str] 

list[str] 

Locks MOULDS_PER_PRESS highest-life moulds to a machine; raises ValueError if insufficient 

release_moulds(mould_ids) 

void 

Frees moulds at run end so they can be reassigned 

mould_life(mould_id) → int 

int 

Returns remaining cycles for a given mould 

avg_life_remaining_for_sku(sku) → float 

float 

Average life across all free compatible moulds 

summary → pd.DataFrame 

property 

DataFrame of all moulds: MouldNo, Compatible_SKUs, Life_Remaining, Assigned_Machine 

 

4.3  ETL 

Handles all data loading — from MySQL database (production) or Excel files (testing/offline). Produces clean DataFrames consumed by the orchestrator. 

Method / Attribute 

Signature / Type 

Description 

load_demand(csv_path) → DataFrame 

DB method 

Reads demand CSV; aggregates by SKUCode summing Updated_Requirement and max ConsolidatedPriorityScore 

load_cycle_times() → DataFrame 

DB method 

Reads Master_Curing_Design_CycleTime; adds LOAD_UNLOAD_BUFFER_MIN and applies PRESS_EFFICIENCY 

load_machine_allowable() → DataFrame 

DB method 

Reads Master_Curing_Allowable_Machines_source; converts yes/no columns to list of eligible machine IDs 

load_gt_inventory() → DataFrame 

DB method 

Reads gt_inventory_manual; returns SKUCode → GT_Inventory 

load_running_moulds() → DataFrame 

DB method 

Reads Daily_Running_Moulds + WC Master; strips LH/RH suffix; groups by base machine; returns list of MouldNos per machine 

load_mould_master() → DataFrame 

DB method 

Reads Master_Mapping_Mould_SKU where Active Flag = True 

load_*_from_excel(...) → DataFrame 

Static methods 

Excel equivalents of all DB methods above; used for run_from_excel() 

 

4.4  LP_Solver 

Formulates and solves the Linear Programme using scipy.optimize.linprog with the HiGHS solver backend. Returns a continuous allocation vector and metadata for downstream rounding. 

Method / Attribute 

Signature / Type 

Description 

avail_mins 

float 

Total available press-minutes (from Config.avail_mins()) 

co_mins 

int 

Changeover duration in minutes 

penalty 

float 

CHANGEOVER_PENALTY_WEIGHT — scales LP objective soft term 

solve(df_valid, all_machines, mould_tracker, locked_machine_mins) → (x, meta) 

Main method 

Constructs and solves LP; returns solution vector x and metadata dict 

 

4.5  Rounder 

Converts the continuous LP solution into integer production cycles. Deducts actual changeover time for multi-SKU machines, then performs a priority-ordered top-up pass to recover demand fulfillment lost in rounding. 

Method / Attribute 

Signature / Type 

Description 

avail_mins / co_mins 

float / int 

Config-derived constants 

round(x, meta, df_valid, locked_machine_mins) → (df_sched, machine_sku_order) 

Main method 

First pass: floor; second pass: deduct CO; third pass: priority top-up 

 

4.6  ScheduleBuilder 

Builds a shift-granular row-level schedule by sequencing PRODUCTION, CHANGEOVER, and MOULD_CLEAN events for each machine. Respects the plant-wide changeover-per-shift cap via _next_co_slot(). 

Method / Attribute 

Signature / Type 

Description 

plan_start / plan_end 

datetime 

Planning window boundaries 

max_co_shift 

int 

MAX_CHANGEOVERS_PER_SHIFT from Config 

_co_shift_counter 

defaultdict[tuple, int] 

Tracks (date, shift) → CO count to enforce cap 

_get_shift(dt) → (shift_label, shift_end) 

Static 

Returns shift letter (A/B/C) and shift-end datetime for a given timestamp 

_next_co_slot(earliest) → datetime 

Internal 

Returns earliest datetime at which a CO can start without exceeding shift cap; defers to next shift if full 

_make_row(...) → dict 

Internal 

Creates a single schedule row dict 

_split_block(...) → (rows, units_so_far) 

Internal 

Splits a production block across shift boundaries; inserts MOULD_CLEAN events at lifecycle trigger 

build(df_sched, machine_sku_order, df_gt, continuity_rows) → DataFrame 

Main method 

Assembles the complete shift schedule including continuity rows, changeovers, cleanings, and production blocks 

 

4.7  JK_LP_Curing_Scheduler_v2 (Orchestrator) 

Top-level orchestrator that drives all five phases (preparation through schedule build) and assembles the results dictionary returned to the caller. 

Method / Attribute 

Signature / Type 

Description 

_prepare_skus(...) → (df_valid, df_all, all_machines) 

Phase 1 

Joins demand, cycle times, machine eligibility, GT inventory, and mould availability into a single enriched SKU table 

_build_continuity(...) → (rows, locked_mins, demand_remainder) 

Phase 2 

Groups running machines by SKU, distributes demand proportionally, generates continuity rows, returns locked capacity 

_build_summary(df_all, df_sched, df_shift) → DataFrame 

Result builder 

Demand fulfillment table: FULLY MET / PARTIAL / UNMET / UNSCHEDULABLE per SKU 

_build_util(df_sched, df_shift, all_machines) → DataFrame 

Result builder 

Press utilization table: Available_Mins, Used_Mins, Idle_Mins, Utilization_Pct per machine 

run(...) → dict 

Entry point 

Executes all phases; returns dict with keys: machine_schedule, shift_schedule, demand_fulfillment, machine_utilization, mould_tracker 

 

5. Mathematical Formulation 

5.1 Decision Variables 

The LP operates on two sets of variables: 

x[s, m] — press-minutes assigned to SKU s on machine m.  Continuous, non-negative. 

slack[s] — unmet demand-minutes for SKU s.  Continuous, non-negative. 

 

5.2 Objective Function 

The objective has two components: a hard penalty on unmet demand and a soft penalty discouraging spreading each SKU across many machines (which drives changeover reduction): 

Minimise: 

 

  Σ_s  slack[s] 

  +  CHANGEOVER_PENALTY_WEIGHT × Σ_s Σ_m ( x[s,m] / demand_mins[s] ) 

 

where the second term penalises each active (machine, SKU) pair 

proportionally to its share of that SKU's demand. 

 

5.3 Constraints 

Machine Capacity 

For each machine m: 

  Σ_s  x[s,m]  ≤  eff_cap[m] 

 

where: 

  eff_cap[m] = avail_mins - locked_mins[m] - CHANGEOVER_DURATION_MIN 

  (one CO conservatively reserved per machine) 

 

Demand Coverage (with slack) 

For each SKU s: 

  Σ_m  x[s,m]  +  slack[s]  ≥  demand_mins[s] 

 

Equivalently (standard form): 

  -Σ_m x[s,m]  -  slack[s]  ≤  -demand_mins[s] 

 

Variable Bounds 

x[s,m] = 0            if machine m not in eligible set for SKU s 

                       (enforced via upper bound = 0 in linprog bounds) 

 

x[s,m] ∈ [0, demand_mins[s]]   for eligible pairs 

slack[s] ∈ [0, ∞) 

 

5.4 Effective Capacity Derivation 

Each machine's theoretical capacity is avail_mins = 43,200 min (30-day horizon). Three deductions reduce this to effective scheduling capacity: 

Deduction 

Rationale 

locked_mins[m] 

Capacity pre-committed to continuity (currently running moulds) 

CHANGEOVER_DURATION_MIN (300 min) 

Conservative 1-CO buffer reserved per machine in LP; actual COs computed in Rounder 

CO surplus (Rounder pass) 

Additional COs for machines assigned > 1 SKU are deducted in the Rounder's first pass 

 

5.5 Rounding Strategy 

Because the LP returns continuous allocations (press-minutes), a rounding step converts these to physically realizable integer production cycles. The procedure is: 

Floor all LP allocations: cycles = floor(mins_lp / cycle_time_min) 

Sort SKUs on each machine by descending priority 

Deduct (n_skus − 1) × CHANGEOVER_DURATION_MIN from each machine's available capacity 

Priority top-up pass: iterate high-priority SKUs; assign residual capacity on eligible machines 

 

📌  Only complete cycles are scheduled — no partial cycles. This guarantees that every produced unit is a finished tyre. 

 

6. Production Constraints 

Six production constraints are explicitly modelled and enforced throughout the scheduling pipeline: 

 

# 

Constraint 

Implementation 

1 

Changeover Time 

300 minutes deducted from effective press capacity per SKU switch on a machine (240 min changeover + 60 min FTC check). 

2 

Mould Cleaning 

120-minute cleaning block inserted after every 6,000 units (3,000 cycles × 2 moulds per press). The min(LH life, RH life) drives the trigger. 

3 

Minimum Changeovers 

Small LP objective penalty per active (machine, SKU) pair concentrates each SKU on as few machines as possible, reducing total changeovers. 

4 

Press Continuity 

Currently running moulds are locked via _build_continuity(); the LP only handles residual demand. Running machines are never interrupted mid-run. 

5 

Max Changeovers / Shift 

Configurable plant-level cap (default: 4) on changeovers starting in any single 8-hour shift. ScheduleBuilder._next_co_slot() defers excess COs to the next shift. 

6 

Mould Tracking 

MouldTracker validates every assignment against mould-SKU compatibility and pool availability. Moulds are locked on assignment and released at run end. 

 

7. Data Model 

The system operates on six primary DataFrames that flow through the pipeline. All DataFrames are in-memory (pandas) and are not persisted to the database. 

7.1 Input DataFrames 

df_demand — SKU Demand 

Column 

Type 

Description 

SKUCode 

str 

SAP material code for the tyre SKU 

Quantity 

int 

Monthly demand quantity in units (tyres) 

Priority 

float 

Consolidated priority score (higher = higher priority) 

 

df_cycles — Cure Cycle Times 

Column 

Type 

Description 

SKUCode 

str 

SAP material code 

Raw 

float 

Raw cure time from Master_Curing_Design_CycleTime (minutes) 

CycleTime_min 

float 

Effective cycle time = (Raw + 2.3) / PRESS_EFFICIENCY 

 

df_allow — Machine Allowable 

Column 

Type 

Description 

SKUCode 

str 

SAP material code 

Machines 

list[int] 

List of machine IDs on which this SKU is approved to run 

 

df_gt — GT Inventory 

Column 

Type 

Description 

SKUCode 

str 

SAP material code 

GT_Inventory 

int 

Current Good-in-Transit inventory quantity 

 

df_running — Currently Running Moulds 

Column 

Type 

Description 

Machine 

str 

Base machine ID (LH/RH suffix stripped) 

SKUCode 

str 

SKU currently in production on this machine 

MouldNos 

list[str] 

List of mould numbers (LH + RH) loaded on the press 

MouldLife_remaining 

int 

Minimum of LH/RH mould cycles remaining before cleaning 

Num_Moulds 

int 

Number of moulds currently loaded (typically 2) 

 

7.2 Key Intermediate DataFrames 

df_valid — Schedulable SKU Table 

Column 

Type 

Description 

SKUCode 

str 

SAP material code 

Demand 

int 

Monthly demand (units) 

Priority 

float 

Priority score 

CycleTime_min 

float 

Effective cycle time 

Machines 

list[int] 

Eligible machine IDs 

Demand_Mins 

float 

Total press-minutes needed = ceil(Demand / 2) × CycleTime_min 

Presses_Needed 

float 

demand_mins / avail_mins — theoretical press count needed 

Schedulable 

bool 

True if cycle time + machine mapping + mould all available 

 

7.3 Output DataFrames 

df_mach — Machine Schedule (LP result, rounded) 

Column 

Type 

Description 

Machine 

str/int 

Press ID 

SKUCode 

str 

SKU assigned to this machine 

CycleTime_min 

float 

Cycle time in minutes 

Cycles 

int 

Integer number of production cycles planned 

Units_Planned 

int 

Cycles × CAVITIES_PER_MOULD 

Mins_Used 

float 

Cycles × CycleTime_min 

Days_Used 

float 

Mins_Used / (shifts_per_day × hours × 60) 

 

df_shift — Shift Schedule (final timeline) 

Column 

Type 

Description 

Date 

date 

Calendar date of the shift 

Shift 

str 

Shift label: A, B, or C 

Machine 

str/int 

Press ID 

SKUCode 

str 

SKU, CHANGEOVER, or MOULD_CLEAN 

StartTime 

datetime 

Block start timestamp 

EndTime 

datetime 

Block end timestamp 

Qty 

int 

Units produced (0 for CHANGEOVER / MOULD_CLEAN) 

CycleTime_min 

float 

Cycle time (0 for non-production rows) 

GT_Inventory 

int 

GT inventory for this SKU at plan time 

Remarks 

str 

Row type annotation (LP Scheduled, Continuity, C/O to X, etc.) 

 

 

8. Excel Output Reference 

The ExcelExporter produces a single workbook with five worksheets. Each sheet has a navy title bar, a teal subtitle/KPI bar, and styled data rows. 

 

Sheet Name 

Fill 

Content 

Demand Fulfillment 

 

Per-SKU summary: Demand, GT_Inventory, Planned_Units, Gap, Fulfillment_Pct, Status (FULLY MET / PARTIAL / UNMET / UNSCHEDULABLE). Colour-coded by status. Footer row shows plant totals. 

Machine Schedule 

 

LP + rounding result: one row per (Machine, SKU) pair showing Cycles, Units_Planned, Mins_Used, Days_Used. Sorted by Machine then SKU. 

Shift Schedule 

 

Full timeline: one row per production/CO/cleaning block. Colour-coded by row type: blue for Shift A rows, amber for B, grey for C, orange for CHANGEOVER, yellow for MOULD_CLEAN. 

Machine Utilization 

 

Per-machine summary: Available_Mins, Used_Mins, Idle_Mins, Utilization_Pct (green ≥ 90%, amber ≥ 60%, red < 60%), SKUs_Count, Total_Cycles, Total_Units. 

Mould Tracker 

 

All moulds from MouldTracker.summary: MouldNo, Compatible_SKUs, Life_Remaining, Assigned_Machine. Green = FREE, amber = assigned. 

 

A KPI banner is embedded in the subtitle bar of every sheet:  Demand | Planned | Gap | Fulfillment% | Avg Utilization% | Changeovers | Mould Cleans 

 

9. Deployment & Usage 

9.1 Prerequisites 

Python 3.10+ 

numpy, pandas, scipy (linprog / HiGHS backend) 

openpyxl (Excel export) 

sqlalchemy + pymysql (for database mode) 

 

9.2 Running from Database (Production) 

from datetime import datetime 

from scheduler import run_from_database 

 

results = run_from_database( 

    demand_csv = "BTP-April_requirement.csv", 

    plan_start = datetime(2026, 4, 1, 7, 0, 0) 

) 

 

9.3 Running from Excel (Testing / Offline) 

from datetime import datetime 

from scheduler import run_from_excel 

 

results = run_from_excel( 

    demand_path  = "Demand_for_Curing_Schedule3_pcr.xlsx", 

    cycles_path  = "Master_Curing_Design_CycleTime_pcr.xlsx", 

    allow_path   = "curing_pcr_machine_allowable.xlsx", 

    gt_path      = "GT_Inventory_pcr.xlsx", 

    mould_path   = "Master_Mapping_Mould_SKU.xlsx", 

    running_path = "Curing_Current_Running_moulds_pcr.xlsx", 

    plan_start   = datetime(2026, 4, 1, 7, 0, 0), 

    output_path  = "PCR_Schedule_April2026.xlsx", 

) 

 

9.4 Required Input Files 

File 

Source 

Key Columns 

BTP-April_requirement.csv 

BTP Planning 

SKUCode, Updated_Requirement, ConsolidatedPriorityScore 

Master_Curing_Design_CycleTime 

DB table 

Sapcode, Cure Time 

Master_Curing_Allowable_Machines 

DB table 

SKU Code, [machine cols] = yes/no 

gt_inventory_manual 

DB table 

sizeCode, gtInventory 

Daily_Running_Moulds 

DB table 

WCNAME, Side, Sapcode, Current MouldNo, Mould life 

Master_Mapping_Mould_SKU 

DB table 

MouldNo, Matl.Code, Active Flag 

 

9.5 Output File 

The scheduler produces a single Excel file (default: BTP_PCR_Curing_LP_v3_PlanSchedule.xlsx) containing all five sheets described in Section 8. The file path is configurable via Config.OUTPUT_FILE or the output_path parameter of either entry-point function. 

 

10. Design Decisions & Trade-offs 

Decision 

Rationale 

Known Limitation 

LP objective: minimise unmet demand-minutes 

Demand fulfillment is the primary KPI. Minimising unmet demand-minutes is equivalent to maximising throughput against constrained capacity. 

SKUs with very long cycle times consume disproportionate press-minutes; the LP may prefer many short-cycle SKUs over one long-cycle SKU with equal unit demand. 

Changeover penalty in LP (soft, not hard) 

A hard changeover constraint would require binary/integer variables (MIP), making the problem NP-hard at scale. The soft penalty steers the LP toward concentration while remaining polynomial-time. 

Very small penalty weights may not fully concentrate SKUs; very large weights can sacrifice demand fulfillment to avoid changeovers. 

Continuity-first, LP-second sequencing 

Interrupting a currently running press wastes the 300-min changeover already paid. Locking running machines respects sunk changeover cost and avoids disruption to in-progress production. 

Continuity scheduling reduces LP flexibility. If running machines are on low-priority SKUs, higher-priority unmet demand must wait for idle presses. 

Floor rounding (no partial cycles) 

A partial cure cycle produces a defective or non-conforming tyre. Floor rounding is physically mandatory. 

Rounding loss can accumulate to hundreds of units across many machines. The priority top-up pass partially recovers this loss. 

Shift-cap for changeovers (≤ 4/shift) 

Concentrating too many simultaneous changeovers overloads the maintenance crew and FTC check resources. 

Deferred changeovers push production start later, potentially reducing total output in edge cases with many SKU switches. 

MouldTracker as a separate layer 

Mould availability is a physical constraint orthogonal to the machine eligibility matrix. Combining them would require re-querying the mould ledger every time the allowable matrix is used. 

The tracker's can_assign() check is called in _prepare_skus() but not dynamically updated during the LP solve; a mould assigned to one SKU in continuity may block another SKU's LP eligibility even if it will be freed mid-horizon. 

 

11. Known Limitations & Future Enhancements 

11.1 Current Limitations 

Single tyre type per run: the scheduler processes PCR only. TBR/OTR would require separate runs with different allowable/cycle-time inputs. 

No real-time re-planning: the schedule is a static 30-day plan computed once at plan_start. Mid-horizon disruptions (machine breakdown, emergency demand) require a manual re-run. 

Mould tracker not updated intra-horizon: moulds assigned in continuity are not released for LP consideration even if their run completes mid-horizon. 

LP assumes homogeneous machine capacity: all machines are treated as having the same avail_mins. Planned maintenance windows, PMs, or shift-specific downtime are not natively modelled. 

Rounding top-up is greedy: the priority top-up pass is not globally optimal; a MIP would produce a tighter solution but at significantly higher computational cost. 

 

11.2 Recommended Enhancements 

MIP integration for small instances: for ≤ 50 SKUs × 20 machines, a full MIP (PuLP + CBC/Gurobi) would eliminate LP rounding loss entirely. 

Rolling horizon re-optimisation: re-run the LP every shift or every day, feeding actual production counts back as continuity inputs. 

Machine downtime modelling: add planned maintenance windows as hard capacity blocks, reducing avail_mins per machine by the PM duration. 

Multi-tyre-type scheduling: extend the allowable matrix and cycle-time table to cover TBR/OTR; run a joint LP with product-type constraints. 

Graphical Gantt export: use plotly or matplotlib to generate a machine × time Gantt chart from df_shift for visual plan review. 

Demand sensitivity analysis: after the LP solve, compute shadow prices (dual variables) on the demand constraints to identify which SKUs are most capacity-constrained. 

 

12. Glossary 

Term 

Definition 

BTP 

Banmore Tyre Plant — JK Tyre's manufacturing facility at Banmore, MP 

PCR 

Passenger Car Radial — the tyre category scheduled by this system 

SKU 

Stock Keeping Unit — a unique tyre product identified by its SAP material code 

Cycle Time 

Total press time per cure cycle including load/unload buffer (minutes) 

Changeover (CO) 

The process of switching a press from one SKU to another (300 min total) 

FTC Check 

First-Tyre Check — quality inspection after a changeover (included in 300 min) 

Mould Life 

Remaining production cycles before a mould requires cleaning (max 3,000) 

Continuity Block 

Production rows pre-scheduled for currently running moulds before LP executes 

demand_mins 

Required press-minutes for a SKU = ceil(demand / 2) × cycle_time 

avail_mins 

Total available press-minutes per machine = 30 × 3 × 8 × 60 = 43,200 

LP 

Linear Programme — a mathematical optimisation problem with a linear objective and linear constraints 

HiGHS 

High-performance LP solver used via scipy.optimize.linprog (method='highs') 

Slack variable 

LP variable representing unmet demand-minutes; minimised in the objective 

GT Inventory 

Good-in-Transit inventory — tyres already produced and in the supply chain 

Shift A / B / C 

07:00–15:00 / 15:00–23:00 / 23:00–07:00 production shifts 

LH / RH Mould 

Left-hand and right-hand mould halves that form a matched pair per press 

 

— End of Document — 

BTP PCR Curing LP Scheduler v2  |  Designed by Paranjay Dodiya  |  Algo8 AI Pvt. Ltd. 
