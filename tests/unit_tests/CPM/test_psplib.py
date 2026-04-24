"""
  Unit tests for pert.py using PSPLIB benchmark instances.
  Based on src/CPM/test_pert_psplib.ipynb.

  Tests cover:
    - CPM analysis (forward/backward pass, project duration)
    - Serial Schedule Generation Scheme (SGS) with 19 priority rules
    - Parallel SGS with 5 scheduling strategies
    - Return dict keys (API contract)
    - Schedule dataframe columns

  To run: LOGOS/tests/unit_tests/CPM$ python test_psplib.py
"""

import sys
import warnings
from datetime import timedelta
from pathlib import Path

warnings.simplefilter('default', DeprecationWarning)

# Resolve repo root relative to this file and add to path
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from src.CPM.pert import Pert  # noqa: E402

# JSON input and schema paths (PSPLIB j30 instance 1)
CPM_DIR = REPO_ROOT / 'tests' / 'unit_tests'/ 'CPM'
JSON_PATH   = str(CPM_DIR / 'j301_1.json')
SCHEMA_PATH = str(REPO_ROOT / 'src' / 'CPM' / 'outage_schema.json')

results = {"pass": 0, "fail": 0}


# ---------------------------------------------------------------------------
# Assertion helpers (mirroring tests/unit_tests/CPM/CPM.py style)
# ---------------------------------------------------------------------------

def checkAnswer(comment, value, expected, tol=1e-10, updateResults=True):
    """Compare two floats within tolerance."""
    if abs(value - expected) > tol:
        print(f"FAIL  {comment}: {value} != {expected}")
        if updateResults:
            results["fail"] += 1
        return False
    else:
        if updateResults:
            results["pass"] += 1
        return True


def checkAnswerString(comment, value, expected, updateResults=True):
    """Compare two strings."""
    if value != expected:
        print(f"FAIL  {comment}: {value!r} != {expected!r}")
        if updateResults:
            results["fail"] += 1
        return False
    else:
        if updateResults:
            results["pass"] += 1
        return True


def checkList(comment, check, expected, updateResults=True):
    """Compare two lists element-by-element."""
    if check == expected:
        if updateResults:
            results["pass"] += 1
        return True
    else:
        print(f"FAIL  {comment}: {check} != {expected}")
        if updateResults:
            results["fail"] += 1
        return False


def checkSubset(comment, subset, container, updateResults=True):
    """Check that every item in subset is in container."""
    missing = [k for k in subset if k not in container]
    if missing:
        print(f"FAIL  {comment}: missing keys {missing}")
        if updateResults:
            results["fail"] += 1
        return False
    else:
        if updateResults:
            results["pass"] += 1
        return True


# ---------------------------------------------------------------------------
# Helper: load a fresh Pert instance (avoids state bleed between tests)
# ---------------------------------------------------------------------------

def load_pert():
    return Pert.from_json_file(JSON_PATH, schema_path=SCHEMA_PATH)


# ---------------------------------------------------------------------------
# 1. CPM Analysis Tests
# ---------------------------------------------------------------------------

print("\n=== CPM Analysis (j30 instance 1) ===")

pert_cpm = load_pert()
pert_cpm.generateInfo()

# Project duration from unconstrained CPM forward pass
checkAnswer(
    "CPM: project duration",
    pert_cpm.getProjectDuration(),
    expected=40.0
)

# Critical path exists and is non-empty
cp = pert_cpm.getCriticalPath()
checkAnswer(
    "CPM: critical path non-empty",
    float(len(cp) > 0),
    expected=1.0
)

# First node of critical path is the START dummy (J1)
cp_sym = pert_cpm.getCriticalPathSymbolic()
checkAnswerString(
    "CPM: critical path starts at J1",
    cp_sym[0],
    expected="J1"
)

# Last node of critical path is the END dummy (J32)
checkAnswerString(
    "CPM: critical path ends at J32",
    cp_sym[-1],
    expected="J32"
)


# ---------------------------------------------------------------------------
# 2. Serial SGS – API Contract
# ---------------------------------------------------------------------------

print("\n=== Serial SGS API Contract ===")

pert_api = load_pert()
out_serial = pert_api.calculateSerialScheduleWithResources(priority_rule='lf')

EXPECTED_SERIAL_KEYS = {
    'scheduled_duration', 'cpm_duration', 'delay_hours',
    'n_activities', 'n_completed', 'priority_rule'
}
checkSubset(
    "Serial SGS: return dict has required keys",
    EXPECTED_SERIAL_KEYS,
    out_serial
)

# n_activities == 32 (30 real + 2 dummy nodes J1/J32)
checkAnswer(
    "Serial SGS: n_activities",
    float(out_serial['n_activities']),
    expected=32.0
)

# All activities completed
checkAnswer(
    "Serial SGS: n_completed == n_activities",
    float(out_serial['n_completed']),
    expected=float(out_serial['n_activities'])
)

# cpm_duration matches CPM analysis
checkAnswer(
    "Serial SGS: cpm_duration matches CPM",
    out_serial['cpm_duration'],
    expected=40.0
)

# priority_rule echoed in output
checkAnswerString(
    "Serial SGS: priority_rule echoed",
    out_serial['priority_rule'],
    expected='lf'
)


# ---------------------------------------------------------------------------
# 3. Serial SGS – All Priority Rules (scheduled_duration golden values)
#    Golden values produced by running the notebook on j301_1.json.
#    Reported durations subtract the 2-hour dummy START+END contribution.
# ---------------------------------------------------------------------------

print("\n=== Serial SGS Priority Rules (j30) ===")

SERIAL_GOLDEN = {
    'lf':          49.0,
    'ls':          46.0,
    'ef':          60.0,
    'es':          53.0,
    'duration':    57.0,
    'random':      47.0,
    'mts':         49.0,
    'mtp':         74.0,
    'grpw':        63.0,
    'grd':         53.0,
    'rr':          73.0,
    'avgrr':       55.0,
    'maxrr':       55.0,
    'minrr':       73.0,
    'mehh_8000_b': 52.0,
    'mehh_3375_b': 53.0,
    'mehh_1000_b': 52.0,
    'mehh_125_b':  46.0,
    'gphh_b':      74.0,
}

for rule, expected_duration in SERIAL_GOLDEN.items():
    pert_s = load_pert()
    out = pert_s.calculateSerialScheduleWithResources(priority_rule=rule)
    # Subtract 2 h for dummy START (1 h) + END (1 h) activities
    actual = out['scheduled_duration'] - 2.0
    checkAnswer(
        f"Serial SGS [{rule}]: scheduled_duration",
        actual,
        expected=expected_duration
    )


# ---------------------------------------------------------------------------
# 4. Parallel SGS – API Contract
# ---------------------------------------------------------------------------

print("\n=== Parallel SGS API Contract ===")

pert_p_api = load_pert()
out_parallel = pert_p_api.calculateScheduleWithResources(sgs='max_use_res_ranked')

EXPECTED_PARALLEL_KEYS = {
    'scheduled_duration', 'cpm_duration', 'delay_hours',
    'n_activities', 'n_completed', 'iterations'
}
checkSubset(
    "Parallel SGS: return dict has required keys",
    EXPECTED_PARALLEL_KEYS,
    out_parallel
)

checkAnswer(
    "Parallel SGS: n_activities",
    float(out_parallel['n_activities']),
    expected=32.0
)

checkAnswer(
    "Parallel SGS: cpm_duration matches CPM",
    out_parallel['cpm_duration'],
    expected=40.0
)


# ---------------------------------------------------------------------------
# 5. Parallel SGS – All Strategies (scheduled_duration golden values)
# ---------------------------------------------------------------------------

print("\n=== Parallel SGS Strategies (j30) ===")

PARALLEL_GOLDEN = {
    'first':               47.0,
    'max_use_res_ranked':  43.0,
    # max_use_res_shuffled uses a random shuffle; result is deterministic only
    # with a fixed seed.  The default seed (2506178) produces 48 h on j30/inst-1.
    'max_use_res_shuffled': 48.0,
    'md_knapsack':         61.0,
    'look_ahead':          43.0,
}

for sgs, expected_duration in PARALLEL_GOLDEN.items():
    pert_p = load_pert()
    out = pert_p.calculateScheduleWithResources(sgs=sgs)
    actual = out['scheduled_duration'] - 2.0
    checkAnswer(
        f"Parallel SGS [{sgs}]: scheduled_duration",
        actual,
        expected=expected_duration
    )


# ---------------------------------------------------------------------------
# 6. Schedule DataFrame – Column Contract
# ---------------------------------------------------------------------------

print("\n=== Schedule DataFrame Columns ===")

pert_df = load_pert()
pert_df.calculateSerialScheduleWithResources(priority_rule='lf')
df = pert_df.get_schedule_dataframe()

EXPECTED_COLUMNS = {
    'activity_id', 'description', 'start_time', 'end_time',
    'duration', 'delay', 'on_resource_constrained_chain', 'tf_actual_hours'
}
checkSubset(
    "Schedule DataFrame: required columns present",
    EXPECTED_COLUMNS,
    set(df.columns)
)

# Row count equals number of scheduled activities
checkAnswer(
    "Schedule DataFrame: row count == n_activities",
    float(len(df)),
    expected=32.0
)

# All durations are positive
checkAnswer(
    "Schedule DataFrame: all durations > 0",
    float((df['duration'] > 0).all()),
    expected=1.0
)

# All start times precede end times
checkAnswer(
    "Schedule DataFrame: start_time < end_time for all rows",
    float((df['start_time'] < df['end_time']).all()),
    expected=1.0
)


# ---------------------------------------------------------------------------
# 7. Parallel SGS + Priority Rule Combinations (spot-check)
#    Tests a representative subset: max_use_res_ranked with lf/ls/mts/grpw
# ---------------------------------------------------------------------------

print("\n=== Parallel SGS + Priority Rule (spot-check, max_use_res_ranked) ===")

# These golden values are taken directly from the notebook output.
PARALLEL_PR_GOLDEN = {
    ('max_use_res_ranked', 'lf'):   43.0,
    ('max_use_res_ranked', 'ls'):   46.0,
    ('max_use_res_ranked', 'mts'):  43.0,
    ('max_use_res_ranked', 'grpw'): 61.0,
    ('look_ahead',         'lf'):   43.0,
    ('look_ahead',         'grpw'): 61.0,
    ('first',              'lf'):   49.0,
    ('first',              'ls'):   49.0,
}

for (sgs, rule), expected_duration in PARALLEL_PR_GOLDEN.items():
    pert_combo = load_pert()
    out = pert_combo.calculateScheduleWithResources(sgs=sgs, priority_rule=rule)
    actual = out['scheduled_duration'] - 2.0
    checkAnswer(
        f"Parallel SGS [{sgs}] + PR [{rule}]: scheduled_duration",
        actual,
        expected=expected_duration
    )


# ===========================================================================
# PSPLIB j60 instance 1  (62 activities, CPM = 79 h)
# ===========================================================================

JSON_PATH_J60 = str(CPM_DIR / 'j601_1.json')

def load_pert_j60():
    return Pert.from_json_file(JSON_PATH_J60, schema_path=SCHEMA_PATH)


# ---------------------------------------------------------------------------
# 8. j60 – CPM Analysis
# ---------------------------------------------------------------------------

print("\n=== CPM Analysis (j60 instance 1) ===")

pert_j60_cpm = load_pert_j60()
pert_j60_cpm.generateInfo()

checkAnswer("j60 CPM: project duration", pert_j60_cpm.getProjectDuration(), expected=79.0)
cp_j60 = pert_j60_cpm.getCriticalPathSymbolic()
checkAnswerString("j60 CPM: critical path starts at J1",  cp_j60[0],  expected="J1")
checkAnswerString("j60 CPM: critical path ends at J62",   cp_j60[-1], expected="J62")


# ---------------------------------------------------------------------------
# 9. j60 – Serial SGS, all 19 priority rules
# ---------------------------------------------------------------------------

print("\n=== Serial SGS Priority Rules (j60) ===")

SERIAL_GOLDEN_J60 = {
    'lf':          77.0,
    'ls':          77.0,
    'ef':          88.0,
    'es':          86.0,
    'duration':   121.0,
    'random':      98.0,
    'mts':         77.0,
    'mtp':        102.0,
    'grpw':        88.0,
    'grd':         98.0,
    'rr':         102.0,
    'avgrr':      100.0,
    'maxrr':      100.0,
    'minrr':      102.0,
    'mehh_8000_b': 77.0,
    'mehh_3375_b': 85.0,
    'mehh_1000_b': 77.0,
    'mehh_125_b': 109.0,
    'gphh_b':     121.0,
}

for rule, expected_duration in SERIAL_GOLDEN_J60.items():
    pert_s = load_pert_j60()
    out = pert_s.calculateSerialScheduleWithResources(priority_rule=rule)
    checkAnswer(
        f"j60 Serial SGS [{rule}]: scheduled_duration",
        out['scheduled_duration'] - 2.0,
        expected=expected_duration
    )


# ---------------------------------------------------------------------------
# 10. j60 – Parallel SGS, all 5 strategies
# ---------------------------------------------------------------------------

print("\n=== Parallel SGS Strategies (j60) ===")

PARALLEL_GOLDEN_J60 = {
    'first':                102.0,
    'max_use_res_ranked':    86.0,
    'max_use_res_shuffled':  86.0,
    'md_knapsack':           92.0,
    'look_ahead':            86.0,
}

for sgs, expected_duration in PARALLEL_GOLDEN_J60.items():
    pert_p = load_pert_j60()
    out = pert_p.calculateScheduleWithResources(sgs=sgs)
    checkAnswer(
        f"j60 Parallel SGS [{sgs}]: scheduled_duration",
        out['scheduled_duration'] - 2.0,
        expected=expected_duration
    )


# ---------------------------------------------------------------------------
# 11. j60 – Schedule DataFrame basic integrity
# ---------------------------------------------------------------------------

print("\n=== Schedule DataFrame Integrity (j60) ===")

pert_j60_df = load_pert_j60()
pert_j60_df.calculateSerialScheduleWithResources(priority_rule='lf')
df_j60 = pert_j60_df.get_schedule_dataframe()

checkAnswer("j60 DataFrame: row count", float(len(df_j60)), expected=62.0)
checkAnswer("j60 DataFrame: all durations > 0",
            float((df_j60['duration'] > 0).all()), expected=1.0)
checkAnswer("j60 DataFrame: start_time < end_time",
            float((df_j60['start_time'] < df_j60['end_time']).all()), expected=1.0)


# ===========================================================================
# PSPLIB j90 instance 1  (92 activities, CPM = 69 h)
# ===========================================================================

JSON_PATH_J90 = str(CPM_DIR / 'j901_1.json')

def load_pert_j90():
    return Pert.from_json_file(JSON_PATH_J90, schema_path=SCHEMA_PATH)


# ---------------------------------------------------------------------------
# 12. j90 – CPM Analysis
# ---------------------------------------------------------------------------

print("\n=== CPM Analysis (j90 instance 1) ===")

pert_j90_cpm = load_pert_j90()
pert_j90_cpm.generateInfo()

checkAnswer("j90 CPM: project duration", pert_j90_cpm.getProjectDuration(), expected=69.0)
cp_j90 = pert_j90_cpm.getCriticalPathSymbolic()
checkAnswerString("j90 CPM: critical path starts at J1",  cp_j90[0],  expected="J1")
checkAnswerString("j90 CPM: critical path ends at J92",   cp_j90[-1], expected="J92")


# ---------------------------------------------------------------------------
# 13. j90 – Serial SGS, all 19 priority rules
# ---------------------------------------------------------------------------

print("\n=== Serial SGS Priority Rules (j90) ===")

SERIAL_GOLDEN_J90 = {
    'lf':           82.0,
    'ls':           83.0,
    'ef':           98.0,
    'es':           88.0,
    'duration':    111.0,
    'random':      105.0,
    'mts':          91.0,
    'mtp':         143.0,
    'grpw':         92.0,
    'grd':         111.0,
    'rr':          148.0,
    'avgrr':        97.0,
    'maxrr':        97.0,
    'minrr':       148.0,
    'mehh_8000_b':  84.0,
    'mehh_3375_b': 101.0,
    'mehh_1000_b':  84.0,
    'mehh_125_b':  102.0,
    'gphh_b':      148.0,
}

for rule, expected_duration in SERIAL_GOLDEN_J90.items():
    pert_s = load_pert_j90()
    out = pert_s.calculateSerialScheduleWithResources(priority_rule=rule)
    checkAnswer(
        f"j90 Serial SGS [{rule}]: scheduled_duration",
        out['scheduled_duration'] - 2.0,
        expected=expected_duration
    )


# ---------------------------------------------------------------------------
# 14. j90 – Parallel SGS, all 5 strategies
# ---------------------------------------------------------------------------

print("\n=== Parallel SGS Strategies (j90) ===")

PARALLEL_GOLDEN_J90 = {
    'first':                262.0,
    'max_use_res_ranked':    86.0,
    'max_use_res_shuffled':  87.0,
    'md_knapsack':           97.0,
    'look_ahead':            94.0,
}

for sgs, expected_duration in PARALLEL_GOLDEN_J90.items():
    pert_p = load_pert_j90()
    out = pert_p.calculateScheduleWithResources(sgs=sgs)
    checkAnswer(
        f"j90 Parallel SGS [{sgs}]: scheduled_duration",
        out['scheduled_duration'] - 2.0,
        expected=expected_duration
    )


# ---------------------------------------------------------------------------
# 15. j90 – Schedule DataFrame basic integrity
# ---------------------------------------------------------------------------

print("\n=== Schedule DataFrame Integrity (j90) ===")

pert_j90_df = load_pert_j90()
pert_j90_df.calculateSerialScheduleWithResources(priority_rule='lf')
df_j90 = pert_j90_df.get_schedule_dataframe()

checkAnswer("j90 DataFrame: row count", float(len(df_j90)), expected=92.0)
checkAnswer("j90 DataFrame: all durations > 0",
            float((df_j90['duration'] > 0).all()), expected=1.0)
checkAnswer("j90 DataFrame: start_time < end_time",
            float((df_j90['start_time'] < df_j90['end_time']).all()), expected=1.0)


# ===========================================================================
# PSPLIB j120 instance 1  (122 activities, CPM = 101 h)
# ===========================================================================

JSON_PATH_J120 = str(CPM_DIR / 'j1201_1.json')

def load_pert_j120():
    return Pert.from_json_file(JSON_PATH_J120, schema_path=SCHEMA_PATH)


# ---------------------------------------------------------------------------
# 16. j120 – CPM Analysis
# ---------------------------------------------------------------------------

print("\n=== CPM Analysis (j120 instance 1) ===")

pert_j120_cpm = load_pert_j120()
pert_j120_cpm.generateInfo()

checkAnswer("j120 CPM: project duration", pert_j120_cpm.getProjectDuration(), expected=101.0)
cp_j120 = pert_j120_cpm.getCriticalPathSymbolic()
checkAnswerString("j120 CPM: critical path starts at J1",   cp_j120[0],  expected="J1")
checkAnswerString("j120 CPM: critical path ends at J122",   cp_j120[-1], expected="J122")


# ---------------------------------------------------------------------------
# 17. j120 – Serial SGS, all 19 priority rules
# ---------------------------------------------------------------------------

print("\n=== Serial SGS Priority Rules (j120) ===")

SERIAL_GOLDEN_J120 = {
    'lf':          123.0,
    'ls':          119.0,
    'ef':          143.0,
    'es':          137.0,
    'duration':    136.0,
    'random':      165.0,
    'mts':         126.0,
    'mtp':         178.0,
    'grpw':        148.0,
    'grd':         154.0,
    'rr':          195.0,
    'avgrr':       148.0,
    'maxrr':       148.0,
    'minrr':       195.0,
    'mehh_8000_b': 124.0,
    'mehh_3375_b': 138.0,
    'mehh_1000_b': 114.0,
    'mehh_125_b':  157.0,
    'gphh_b':      193.0,
}

for rule, expected_duration in SERIAL_GOLDEN_J120.items():
    pert_s = load_pert_j120()
    out = pert_s.calculateSerialScheduleWithResources(priority_rule=rule)
    checkAnswer(
        f"j120 Serial SGS [{rule}]: scheduled_duration",
        out['scheduled_duration'] - 2.0,
        expected=expected_duration
    )


# ---------------------------------------------------------------------------
# 18. j120 – Parallel SGS, all 5 strategies
# ---------------------------------------------------------------------------

print("\n=== Parallel SGS Strategies (j120) ===")

PARALLEL_GOLDEN_J120 = {
    'first':                209.0,
    'max_use_res_ranked':   120.0,
    'max_use_res_shuffled': 139.0,
    'md_knapsack':          148.0,
    'look_ahead':           120.0,
}

for sgs, expected_duration in PARALLEL_GOLDEN_J120.items():
    pert_p = load_pert_j120()
    out = pert_p.calculateScheduleWithResources(sgs=sgs)
    checkAnswer(
        f"j120 Parallel SGS [{sgs}]: scheduled_duration",
        out['scheduled_duration'] - 2.0,
        expected=expected_duration
    )


# ---------------------------------------------------------------------------
# 19. j120 – Schedule DataFrame basic integrity
# ---------------------------------------------------------------------------

print("\n=== Schedule DataFrame Integrity (j120) ===")

pert_j120_df = load_pert_j120()
pert_j120_df.calculateSerialScheduleWithResources(priority_rule='lf')
df_j120 = pert_j120_df.get_schedule_dataframe()

checkAnswer("j120 DataFrame: row count", float(len(df_j120)), expected=122.0)
checkAnswer("j120 DataFrame: all durations > 0",
            float((df_j120['duration'] > 0).all()), expected=1.0)
checkAnswer("j120 DataFrame: start_time < end_time",
            float((df_j120['start_time'] < df_j120['end_time']).all()), expected=1.0)


# ===========================================================================
# Dependency Violation Checks  (check_dependency_violations)
# ===========================================================================

# ---------------------------------------------------------------------------
# 20. Unscheduled instance raises ValueError
# ---------------------------------------------------------------------------

print("\n=== Dependency Violations: unscheduled guard ===")

pert_unscheduled = load_pert()
try:
    pert_unscheduled.check_dependency_violations()
    # Should never reach here
    print("FAIL  check_dependency_violations: expected ValueError before scheduling")
    results["fail"] += 1
except ValueError:
    results["pass"] += 1


# ---------------------------------------------------------------------------
# 21. Feasible schedules – no violations expected
#     Covers both SGS modes and all four PSPLIB instances.
# ---------------------------------------------------------------------------

print("\n=== Dependency Violations: feasible schedules ===")

FEASIBLE_CASES = [
    # (loader,         sgs/rule,                     label)
    (load_pert,        ('serial',   'lf'),            "j30  serial  lf"),
    (load_pert,        ('serial',   'mts'),           "j30  serial  mts"),
    (load_pert,        ('parallel', 'max_use_res_ranked'), "j30  parallel max_use_res_ranked"),
    (load_pert,        ('parallel', 'first'),         "j30  parallel first"),
    (load_pert_j60,    ('serial',   'lf'),            "j60  serial  lf"),
    (load_pert_j60,    ('parallel', 'max_use_res_ranked'), "j60  parallel max_use_res_ranked"),
    (load_pert_j90,    ('serial',   'lf'),            "j90  serial  lf"),
    (load_pert_j90,    ('parallel', 'look_ahead'),    "j90  parallel look_ahead"),
    (load_pert_j120,   ('serial',   'lf'),            "j120 serial  lf"),
    (load_pert_j120,   ('parallel', 'max_use_res_ranked'), "j120 parallel max_use_res_ranked"),
]

for loader, (mode, rule), label in FEASIBLE_CASES:
    p = loader()
    if mode == 'serial':
        p.calculateSerialScheduleWithResources(priority_rule=rule)
    else:
        p.calculateScheduleWithResources(sgs=rule)
    violations, is_feasible = p.check_dependency_violations()
    checkAnswer(
        f"Feasible [{label}]: no violations",
        float(len(violations)),
        expected=0.0
    )
    checkAnswer(
        f"Feasible [{label}]: is_feasible=True",
        float(is_feasible),
        expected=1.0
    )


# ---------------------------------------------------------------------------
# 22. Artificially injected violation – single edge
#     Schedule j30 normally, then shift J2's start time back so it overlaps
#     with predecessor J1 (which ends at t+1 h).  Expect exactly one
#     violation entry with correct predecessor/successor names and overlap.
# ---------------------------------------------------------------------------

print("\n=== Dependency Violations: single injected violation ===")

pert_viol = load_pert()
pert_viol.calculateSerialScheduleWithResources(priority_rule='lf')

# Locate J1 (the START dummy) and J2 in the activity graph
act_j1 = pert_viol.task_to_activity['J1']
act_j2 = pert_viol.task_to_activity['J2']

_, j1_end = act_j1.returnAbsTimes()
# Move J2 to start 2 h *before* J1 ends  →  2 h overlap
injected_start = j1_end - timedelta(hours=2)
act_j2.startTime = injected_start
act_j2.endTime   = injected_start + timedelta(hours=act_j2.duration)

violations_viol, is_feasible_viol = pert_viol.check_dependency_violations()

checkAnswer(
    "Injected violation: is_feasible=False",
    float(is_feasible_viol),
    expected=0.0
)
checkAnswer(
    "Injected violation: exactly 1 violation found",
    float(len(violations_viol)),
    expected=1.0
)

if violations_viol:
    v = violations_viol[0]
    checkAnswerString(
        "Injected violation: predecessor is J1",
        v['predecessor'],
        expected='J1'
    )
    checkAnswerString(
        "Injected violation: successor is J2",
        v['successor'],
        expected='J2'
    )
    checkAnswer(
        "Injected violation: overlap_hours == 2.0",
        v['overlap_hours'],
        expected=2.0
    )
    checkAnswer(
        "Injected violation: pred_end_time correct",
        float((v['pred_end_time'] - j1_end).total_seconds()),
        expected=0.0
    )
    checkAnswer(
        "Injected violation: succ_start_time correct",
        float((v['succ_start_time'] - injected_start).total_seconds()),
        expected=0.0
    )


# ---------------------------------------------------------------------------
# 23. Multiple injected violations
#     Break two independent edges simultaneously and verify both are reported.
# ---------------------------------------------------------------------------

print("\n=== Dependency Violations: multiple injected violations ===")

pert_multi = load_pert()
pert_multi.calculateSerialScheduleWithResources(priority_rule='lf')

# Break J1→J2 (overlap 1 h) and J1→J3 (overlap 0.5 h)
act_j1m  = pert_multi.task_to_activity['J1']
act_j2m  = pert_multi.task_to_activity['J2']
act_j3m  = pert_multi.task_to_activity['J3']
_, j1m_end = act_j1m.returnAbsTimes()

act_j2m.startTime = j1m_end - timedelta(hours=1)
act_j2m.endTime   = act_j2m.startTime + timedelta(hours=act_j2m.duration)

act_j3m.startTime = j1m_end - timedelta(hours=0.5)
act_j3m.endTime   = act_j3m.startTime + timedelta(hours=act_j3m.duration)

violations_multi, is_feasible_multi = pert_multi.check_dependency_violations()

checkAnswer(
    "Multi-violation: is_feasible=False",
    float(is_feasible_multi),
    expected=0.0
)
checkAnswer(
    "Multi-violation: exactly 2 violations found",
    float(len(violations_multi)),
    expected=2.0
)

if len(violations_multi) == 2:
    violated_pairs = {(v['predecessor'], v['successor']) for v in violations_multi}
    checkAnswer(
        "Multi-violation: J1→J2 reported",
        float(('J1', 'J2') in violated_pairs),
        expected=1.0
    )
    checkAnswer(
        "Multi-violation: J1→J3 reported",
        float(('J1', 'J3') in violated_pairs),
        expected=1.0
    )

    by_succ = {v['successor']: v for v in violations_multi}
    checkAnswer(
        "Multi-violation: J1→J2 overlap_hours == 1.0",
        by_succ['J2']['overlap_hours'],
        expected=1.0
    )
    checkAnswer(
        "Multi-violation: J1→J3 overlap_hours == 0.5",
        by_succ['J3']['overlap_hours'],
        expected=0.5
    )


# ===========================================================================
# WCS / ACS / IRSM Dynamic Priority Rules  (Kolisch 1996)
# ===========================================================================

print("\n=== Serial SGS: WCS / ACS / IRSM (j30) ===")

SERIAL_GOLDEN_DYNAMIC_J30 = {
    'wcs':  47.0,
    'acs':  46.0,
    'irsm': 51.0,
}
for rule, expected_duration in SERIAL_GOLDEN_DYNAMIC_J30.items():
    pert_s = load_pert()
    out = pert_s.calculateSerialScheduleWithResources(priority_rule=rule)
    checkAnswer(
        f"j30 Serial SGS [{rule}]: scheduled_duration",
        out['scheduled_duration'] - 2.0,
        expected=expected_duration
    )

print("\n=== Serial SGS: WCS / ACS / IRSM (j60) ===")

SERIAL_GOLDEN_DYNAMIC_J60 = {
    'wcs':  77.0,
    'acs':  77.0,
    'irsm': 81.0,
}
for rule, expected_duration in SERIAL_GOLDEN_DYNAMIC_J60.items():
    pert_s = load_pert_j60()
    out = pert_s.calculateSerialScheduleWithResources(priority_rule=rule)
    checkAnswer(
        f"j60 Serial SGS [{rule}]: scheduled_duration",
        out['scheduled_duration'] - 2.0,
        expected=expected_duration
    )

print("\n=== Serial SGS: WCS / ACS / IRSM (j90) ===")

SERIAL_GOLDEN_DYNAMIC_J90 = {
    'wcs':  81.0,
    'acs':  83.0,
    'irsm': 82.0,
}
for rule, expected_duration in SERIAL_GOLDEN_DYNAMIC_J90.items():
    pert_s = load_pert_j90()
    out = pert_s.calculateSerialScheduleWithResources(priority_rule=rule)
    checkAnswer(
        f"j90 Serial SGS [{rule}]: scheduled_duration",
        out['scheduled_duration'] - 2.0,
        expected=expected_duration
    )

print("\n=== Serial SGS: WCS / ACS / IRSM (j120) ===")

SERIAL_GOLDEN_DYNAMIC_J120 = {
    'wcs':  124.0,
    'acs':  119.0,
    'irsm': 154.0,
}
for rule, expected_duration in SERIAL_GOLDEN_DYNAMIC_J120.items():
    pert_s = load_pert_j120()
    out = pert_s.calculateSerialScheduleWithResources(priority_rule=rule)
    checkAnswer(
        f"j120 Serial SGS [{rule}]: scheduled_duration",
        out['scheduled_duration'] - 2.0,
        expected=expected_duration
    )

print("\n=== Parallel SGS (max_use_res_ranked): WCS / ACS / IRSM (all instances) ===")

PARALLEL_DYNAMIC_GOLDEN = {
    # (loader_func, instance_label): {rule: expected_duration}
    ('j30',  load_pert):     {'wcs': 43.0, 'acs': 46.0, 'irsm': 43.0},
    ('j60',  load_pert_j60): {'wcs': 86.0, 'acs': 86.0, 'irsm': 86.0},
    ('j90',  load_pert_j90): {'wcs': 81.0, 'acs': 81.0, 'irsm': 89.0},
    ('j120', load_pert_j120):{'wcs': 124.0,'acs': 125.0,'irsm': 124.0},
}
for (label, loader), rule_map in PARALLEL_DYNAMIC_GOLDEN.items():
    for rule, expected_duration in rule_map.items():
        pert_p = loader()
        out = pert_p.calculateScheduleWithResources(sgs='max_use_res_ranked', priority_rule=rule)
        checkAnswer(
            f"{label} Parallel SGS (max_use_res_ranked) [{rule}]: scheduled_duration",
            out['scheduled_duration'] - 2.0,
            expected=expected_duration
        )

print("\n=== WCS/ACS/IRSM: return dict keys and completion ===")

pert_wcs = load_pert()
out_wcs = pert_wcs.calculateScheduleWithResources(sgs='max_use_res_ranked', priority_rule='wcs')
checkSubset(
    "WCS parallel: return dict has required keys",
    {'scheduled_duration', 'cpm_duration', 'delay_hours', 'n_activities', 'n_completed', 'iterations'},
    out_wcs
)
checkAnswer(
    "WCS parallel: all activities completed",
    float(out_wcs['n_completed']),
    expected=float(out_wcs['n_activities'])
)

pert_acs = load_pert()
out_acs = pert_acs.calculateScheduleWithResources(sgs='max_use_res_ranked', priority_rule='acs')
checkAnswer(
    "ACS parallel: all activities completed",
    float(out_acs['n_completed']),
    expected=float(out_acs['n_activities'])
)

pert_irsm = load_pert()
out_irsm = pert_irsm.calculateScheduleWithResources(sgs='max_use_res_ranked', priority_rule='irsm')
checkAnswer(
    "IRSM parallel: all activities completed",
    float(out_irsm['n_completed']),
    expected=float(out_irsm['n_activities'])
)

print("\n=== WCS/ACS/IRSM: no dependency violations ===")

for rule in ['wcs', 'acs', 'irsm']:
    p = load_pert()
    p.calculateScheduleWithResources(sgs='max_use_res_ranked', priority_rule=rule)
    violations, is_feasible = p.check_dependency_violations()
    checkAnswer(f"WCS/ACS/IRSM [{rule}]: no violations", float(len(violations)), expected=0.0)
    checkAnswer(f"WCS/ACS/IRSM [{rule}]: is_feasible", float(is_feasible), expected=1.0)


# ---------------------------------------------------------------------------
# 15. Backward Serial SGS – API Contract and Basic Properties (j30)
# ---------------------------------------------------------------------------

print("\n=== Backward Serial SGS API Contract (j30) ===")

# Derive a makespan from a forward serial run to use as the ALAP horizon.
_p_fwd = load_pert()
_out_fwd = _p_fwd.calculateSerialScheduleWithResources(priority_rule='lf')
_makespan = _out_fwd['scheduled_duration']  # 51.0 h

pert_bwd_s = load_pert()
out_bwd_s = pert_bwd_s.calculateBackwardSerialScheduleWithResources(
    makespan_hours=_makespan,
    priority_rule='lf',
)

# Return dict keys
EXPECTED_BWD_SERIAL_KEYS = {
    'scheduled_duration', 'cpm_duration', 'delay_hours',
    'n_activities', 'n_completed', 'priority_rule'
}
checkSubset(
    "Backward Serial SGS: return dict has required keys",
    EXPECTED_BWD_SERIAL_KEYS,
    out_bwd_s
)

# cpm_duration matches CPM analysis
checkAnswer(
    "Backward Serial SGS: cpm_duration matches CPM",
    out_bwd_s['cpm_duration'],
    expected=40.0
)

# n_activities count is correct
checkAnswer(
    "Backward Serial SGS: n_activities",
    float(out_bwd_s['n_activities']),
    expected=32.0
)

# n_completed ≤ n_activities
checkAnswer(
    "Backward Serial SGS: n_completed ≤ n_activities",
    float(out_bwd_s['n_completed'] <= out_bwd_s['n_activities']),
    expected=1.0
)

# priority_rule echoed
checkAnswerString(
    "Backward Serial SGS: priority_rule echoed",
    out_bwd_s['priority_rule'],
    expected='lf'
)

# All placed activities start at or after project start
_bad_start = [
    act.name for act in pert_bwd_s.forwardDict
    if act.returnAbsTimes()[0] is not None
    and act.returnAbsTimes()[0] < pert_bwd_s.startTime
]
checkAnswer(
    "Backward Serial SGS: no placed activity starts before project start",
    float(len(_bad_start) == 0),
    expected=1.0
)

# All placed activities end at or before the horizon
from datetime import timedelta as _td
_horizon = pert_bwd_s.startTime + _td(hours=_makespan)
_bad_end = [
    act.name for act in pert_bwd_s.forwardDict
    if act.returnAbsTimes()[1] is not None
    and act.returnAbsTimes()[1] > _horizon
]
checkAnswer(
    "Backward Serial SGS: no placed activity exceeds horizon",
    float(len(_bad_end) == 0),
    expected=1.0
)

# No precedence violations among placed activities
_bwd_s_viols, _bwd_s_feas = pert_bwd_s.check_dependency_violations()
# Violations only for placed activities (skip None-start pairs already filtered
# by check_dependency_violations which skips None times)
checkAnswer(
    "Backward Serial SGS: no precedence violations among placed activities",
    float(len(_bwd_s_viols) == 0),
    expected=1.0
)


# ---------------------------------------------------------------------------
# 16. Backward Parallel SGS – API Contract and Properties (j30)
# ---------------------------------------------------------------------------

print("\n=== Backward Parallel SGS API Contract (j30) ===")

pert_bwd_p = load_pert()
# Use default priority_rule='' (TF_based) which achieves full completion on j30.
out_bwd_p = pert_bwd_p.calculateBackwardScheduleWithResources(
    makespan_hours=_makespan,
)

# Return dict keys
EXPECTED_BWD_PARALLEL_KEYS = {
    'scheduled_duration', 'cpm_duration', 'delay_hours',
    'n_activities', 'n_completed', 'iterations'
}
checkSubset(
    "Backward Parallel SGS: return dict has required keys",
    EXPECTED_BWD_PARALLEL_KEYS,
    out_bwd_p
)

# cpm_duration matches CPM analysis
checkAnswer(
    "Backward Parallel SGS: cpm_duration matches CPM",
    out_bwd_p['cpm_duration'],
    expected=40.0
)

# All activities completed (backward parallel places all activities)
checkAnswer(
    "Backward Parallel SGS: n_completed == n_activities",
    float(out_bwd_p['n_completed']),
    expected=float(out_bwd_p['n_activities'])
)

# scheduled_duration matches horizon (ALAP does not extend beyond horizon)
checkAnswer(
    "Backward Parallel SGS: scheduled_duration == makespan_hours",
    out_bwd_p['scheduled_duration'],
    expected=_makespan,
    tol=1e-6
)

# All activities start at or after project start
_bad_start_p = [
    act.name for act in pert_bwd_p.forwardDict
    if act.returnAbsTimes()[0] is not None
    and act.returnAbsTimes()[0] < pert_bwd_p.startTime
]
checkAnswer(
    "Backward Parallel SGS: no activity starts before project start",
    float(len(_bad_start_p) == 0),
    expected=1.0
)

# All activities end at or before the horizon
_bad_end_p = [
    act.name for act in pert_bwd_p.forwardDict
    if act.returnAbsTimes()[1] is not None
    and act.returnAbsTimes()[1] > _horizon
]
checkAnswer(
    "Backward Parallel SGS: no activity exceeds horizon",
    float(len(_bad_end_p) == 0),
    expected=1.0
)

# No precedence violations
_bwd_p_viols, _bwd_p_feas = pert_bwd_p.check_dependency_violations()
checkAnswer(
    "Backward Parallel SGS: no precedence violations",
    float(len(_bwd_p_viols) == 0),
    expected=1.0
)

checkAnswer(
    "Backward Parallel SGS: is_feasible == True",
    float(_bwd_p_feas),
    expected=1.0
)


# ---------------------------------------------------------------------------
# 17. Backward SGS via _ordered: ALAP respects F1 topological order (j30)
#     Tests that passing _ordered (from a forward run) produces an ALAP
#     schedule consistent with precedence constraints.
# ---------------------------------------------------------------------------

print("\n=== Backward SGS with _ordered (j30) ===")

_p_ord = load_pert()
_raw_prio = _p_ord.priority_calculation(list(_p_ord.forwardDict.keys()), 'lf')
_f1_ordered = [a for (a, _, _) in _raw_prio]

out_bwd_ord = _p_ord.calculateBackwardSerialScheduleWithResources(
    makespan_hours=_makespan,
    _ordered=_f1_ordered,
)
checkAnswerString(
    "Backward Serial SGS with _ordered: priority_rule echoed as 'custom'",
    out_bwd_ord['priority_rule'],
    expected='custom'
)
checkAnswer(
    "Backward Serial SGS with _ordered: n_completed ≤ n_activities",
    float(out_bwd_ord['n_completed'] <= out_bwd_ord['n_activities']),
    expected=1.0
)
_viols_ord, _ = _p_ord.check_dependency_violations()
checkAnswer(
    "Backward Serial SGS with _ordered: no precedence violations",
    float(len(_viols_ord) == 0),
    expected=1.0
)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print(f"\n{'='*60}")
print(f"Results: {results['pass']} passed, {results['fail']} failed")
print(f"{'='*60}")

sys.exit(results["fail"])
