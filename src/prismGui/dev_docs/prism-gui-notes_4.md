# PRISM GUI — Layout / Structure Notes

## definitions

Baseline — a committed reference plan. Edited via the draft→commit lifecycle in _render_editor. This is the "original imported plan" and any corrected version of it.

Scenario — an overlay delta on a baseline (duration overrides + resource changes), bound by base_plan_hash. This is the "what-if," and it is deliberately not a full edited copy of the plan — it's a thin diff.

RunResult — the output of running (baseline + scenario + config). StreamlitSessionState already stores these keyed by run_id (prism_results + prism_selected_id), so it already supports "a list the analyst selects from."

## Data loader
1) I like the data loading bar on the left side of the GUI where we import the original plan.
Are there missing configuration elements? I would add scheduling_horizon_hours.
The activity "mode_selections" should occur in bullet 7 I think

2) I think validation and run schedule bottom should be located within the same loading bar. put the Run button and a compact status badge (Ready / Warnings / Blocked) in the bar, but keep the expandable issue detail in the main column. That keeps the bar scannable.

3) We might want to think to add a here also a list of scenarios including the baseline. The analyst can select from here what needs to be edited, visualized, etc.
   
4) Tracking the history of edits between baseline and scenarios should be important, including a graph-based map the shows edit and development of the scenarios: this could be a tab ion the GUI


## Tab lists
These are tabs that will show data/plots element of the current schedule (either the baseline or the scenarios, see bullet 3)

5- Data viewer tab: there should be a tab that list in tabular form all activities and all the data elements for each activity for the selected scenario/baseline

6- visual plots associated to the selected scenario/baseline: gantt chart plus all Resource utilization plots. All these plots should have their x-axis perfectly aligned. Here also the activity graph plot should be explorable graphically 

7- what-if scenario tab: here the analyst can edit/modify the current scenario: edit/remove/add tasks, edit resources data, edit constraints data 

## Reply to claude
### In-scope-now (render-layer, safe polish):
(2) Validation + Run button in the left bar. Fine as a page-flow move — with one caveat: validation output can be a long list of Issue objects, and the sidebar is narrow. I'd put the Run button and a compact status badge (Ready / Warnings / Blocked) in the bar, but keep the expandable issue detail in the main column. That keeps the bar scannable.
Reply: I agree

(4-) Data viewer tab — tabular activities + fields for the selected schedule. New read-only view, but the data's all in the snapshot; modest.
Reply: I agree

(5-) Gantt + resource-utilization with aligned x-axes — this is a genuine improvement and squarely in scope; we already render both, just not on a shared time axis.
Reply: for schedule spanning long time horizons we should add the possibility to add an horizontal scroll tab

(6-) What-if as a tab — moving the existing _render_scenario_panel into a tab is trivially in-scope. But "edit/remove/add tasks, edit constraints" in that tab risks collapsing the baseline-vs-scenario distinction. Scenarios are overlays, not full edits. I'd keep the tab to what a scenario is (durations + resources + the few things the overlay model supports) and route structural edits (add/remove tasks, constraints) to the editor, which edits the baseline. Blurring them would be an architectural regression, not a layout change.
Reply: Here i intended the main tabs in  bullets 5 6 and 7 as the main one, when selecting the what-if scenario tab I would see the subtabs that are in place now. does it make sense?

### Needs a decision (doable but more than a polish):

(3) List of edited schedules + selector — in-scope if it means "selected run" (substrate exists). Bigger if it means edited baseline revisions.
Reply: it could be but it would improve usability

(5-) Explorable activity graph — new capability. Streamlit-native graph rendering is limited; it likely means a graph lib and interaction wiring. Worth doing, but it's a feature, not a polish — I'd tag it "next," not "now."
Reply: let's investigate options

### Later (real roadmap phases, not this pass):

(4) Edit-history graph between two schedules — this is provenance lineage + baseline versioning, i.e. Phase 6 in notes_3.md. It presupposes multiple persisted baseline revisions, which we don't have yet.
Reply: here i intended a graph that capture the relation between the baseline and the set of developed scenarios
Claude: that's much cheaper than baseline versioning. Every scenario already carries base_plan_hash,
so the baseline -> scenarios relation is a derivable star/tree — no new persistence model needed once
we store more than one scenario. Drops from "later" to Stage B below.


## Target structure (shared skeleton)

Agreed skeleton to build against. Nothing here touches domain / application / infrastructure / ports;
Stage A is render-layer only (main() + _render_*). Stage B adds one session-state field.

### Left bar ("data loader")
- Import original plan (source picker) — as today.
- scheduling_horizon_hours — NEW config knob (RunConfig already supports it -> PRISM max_time_hours).
- Validation status badge (Ready / Warnings / Blocked) + Run button. The expandable issue DETAIL
  stays in the main column (the bar is too narrow for a long Issue list).
- [Stage B] Selector: a list of { baseline, scenario A, scenario B, ... }. Picking one sets the
  "current schedule" that every tab below operates on. Stage A operates on the single scenario we
  already store (no selector yet).

### Main area — top-level tabs (operate on the SELECTED schedule)
1. Data viewer — read-only tabular activities + all data fields. Reads INPUT, so always available.
2. Plots — Gantt + resource-utilization with x-axes perfectly aligned; horizontal scroll for long
   horizons; explorable activity graph [Stage B / investigate lib]. These are properties of a RUN
   (output), see the input/output note below.
3. What-if / Edit — contains the subtabs that exist today (the 9 editor tabs + the scenario controls
   + mode_selections). Edits the selected schedule.

### Input vs. output (the one wrinkle)
A baseline/scenario is an INPUT; it has no Gantt until it is RUN. So:
- Data viewer (tab 1) reads input -> always works.
- Plots (tab 2) is a property of a RunResult:
    - selected schedule HAS a run  -> show its latest run;
    - selected schedule NOT yet run -> "Run to see the schedule" state, not an empty chart.
- Persistent results HEADER above the tabs whenever the selected schedule has a run: status badge +
  makespan / CPM / gap + disposition + provenance/freshness, so those stay visible on every tab.
  The rest of today's _render_result relocates: Gantt + utilization + schedule table -> Plots;
  audit findings + fitness -> header (or a small Results area).

### Three decisions this structure implies
D1. Edit target follows selection. Baseline selected -> the 9 subtabs edit the baseline DRAFT
    (add/remove tasks OK). Scenario selected -> a scenario is a THIN overlay, so only duration /
    resource / mode changes apply (no add/remove tasks). The edit subtabs adapt to the selection.
D2. Store more than one scenario. Today StreamlitSessionState holds exactly one prism_scenario.
    Bullets 3 + 4 need many -> one new session-state field (scenario -> dict of scenarios). This is
    the thing that lifts the work past pure render-layer.
D3. mode_selections relocates from the sidebar RunConfig into the What-if/Edit tab (per bullet 1).
    The run wiring then reads modes from the edit context instead of the run bar. Small but real.

### Staging
- Stage A (render-layer only, no session-state change): top-level tabs (Data viewer / Plots /
  What-if) over the SINGLE scenario we store today; aligned x-axes + horizontal scroll; persistent
  results header. == the agreed "result tabs + page flow". Touches only main() + _render_*.
- Stage B (small feature): multi-scenario storage (D2) + left-bar selector + edit-target-follows-
  selection (D1) + baseline->scenarios relation graph (bullet 4) + mode_selections relocation (D3).

### Rendering library
Aligned x-axes + horizontal scroll rules out st.bar_chart / st.line_chart (can't share an axis or
scroll). Plotly (built-in range slider + pan) or Altair — both CDN-loadable and Streamlit-native.
Stage-A choice, since Gantt + utilization both depend on it.



