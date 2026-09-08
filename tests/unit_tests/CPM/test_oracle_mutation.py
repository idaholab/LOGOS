"""
Fuzzed oracle-sensitivity ("mutation") tests for schedule_validator.

The whole fuzz-and-freeze program (see
src/CPM/devLogs/RCPSP_ROBUSTNESS_2026-09-07.md) assumes the independent oracle
``schedule_validator.validate_schedule`` flags *every* infeasible schedule the
engine can emit.  ``test_schedule_validator.py`` proves each of the 14 hard
checks catches *one* hand-crafted corruption — but a single example per check is
not the same as "the oracle is sensitive to this class of defect."  See
src/CPM/devLogs/ORACLE_COMPLETENESS_2026-09-07.md §6.1.

This module generalises those hand-injected fault tests into a Hypothesis-driven
loop, one *scenario* per check:

    1. build a **feasible-by-construction** schedule that exercises the check,
       fuzzing the numeric parameters (durations, crew counts);
    2. assert the oracle calls it feasible **pre-mutation** — a false positive
       here is an oracle-soundness bug (or an engine bug that produced a bad
       schedule);
    3. apply one feasibility-breaking mutation whose effect is **guaranteed
       regardless of the fuzzed numerics**;
    4. assert the expected ``Violation.type`` appears among the *hard*
       violations post-mutation — a miss here is an oracle-sensitivity bug.

A red in step 2 or 4 is a genuine finding: fix the oracle (or engine) and freeze
the shrunk counterexample into test_bugfix_regressions.py.

The mutation mechanics mirror the proven ones in test_schedule_validator.py
verbatim (tamper endTime → duration; force overlap → crew/equipment/location/
system_state; reassign zone → equipment_zone; reduce stock → consumable; bump
tracker → dose; …).  Templates are 2–4 activities so ~15 scenarios × 150 "ci"
examples stays fast.

The module self-skips where Hypothesis is not installed (mirrors
test_property_based.py), so the suite still collects cleanly without it.
"""
import os
from collections import namedtuple
from datetime import datetime, timedelta

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest

hypothesis = pytest.importorskip("hypothesis")  # skip cleanly if not installed
from hypothesis import given, settings, strategies as st, HealthCheck

from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import (
    ResourcePool, ResourceAvailability,
    EquipmentPool, EquipmentAvailability,
    LocationPool, LocationAvailability,
    ConsumablePool, SystemStatePool,
)
from CPM.schedule_validator import validate_schedule


# ---------------------------------------------------------------------------
# Hypothesis profiles
# ---------------------------------------------------------------------------
# Registered here as well as in test_property_based.py so this file is runnable
# standalone (the per-file RAVEN registration runs `pytest test_oracle_mutation.py`
# in isolation, without importing test_property_based).  register_profile is
# idempotent, so duplicating the block is harmless.

settings.register_profile(
    "ci",
    max_examples=150,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
settings.register_profile(
    "thorough",
    max_examples=2000,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))


# ---------------------------------------------------------------------------
# Shared constants and helpers
# ---------------------------------------------------------------------------

# Midnight so the shift-calendar scenario's activity naturally lands at hour 0,
# outside a daytime shift.
START_DT  = datetime(2026, 1, 1, 0, 0)
HORIZON   = timedelta(days=30)
MAX_TIME  = 500                 # scheduling horizon (h); our templates need < 100
BIG_HOURS = 999.0               # a corruption offset far outside any fuzzed range

SGS = 'max_use_res_ranked'

# The 14 hard checks run by validate_schedule (see its dispatch block).
# The scenario table below must cover every one; the meta-test enforces it.
ALL_CHECK_TYPES = frozenset({
    'completeness', 'duration', 'precedence', 'time_window', 'hold_point',
    'crew', 'substitution', 'equipment', 'equipment_zone', 'location',
    'consumable', 'shift_calendar', 'dose', 'system_state',
})


def _rp(skill, count, *, consumable=False, dose_budget=0.0):
    """A renewable (or dose-tracked consumable) crew pool with one skill."""
    rp = ResourcePool()
    period = [{'start_date': START_DT, 'end_date': START_DT + HORIZON,
               'available_count': count}]
    if consumable:
        rp.resources[skill] = ResourceAvailability(
            skill, period, resource_type='consumable',
            dose_budget_per_worker_mrem=dose_budget)
    else:
        rp.resources[skill] = ResourceAvailability(skill, period)
    return rp


def _mech(crew):
    return [{'skill_type': 'MECH', 'crew_count': crew, 'alternative_skill_types': []}]


def _finish(p):
    """generateInfo + schedule; returns the scheduled Pert (populated .completed)."""
    p.generateInfo()
    p.calculateScheduleWithResources(sgs=SGS, max_time_hours=MAX_TIME)
    return p


def _act(p, name):
    return next(a for a in p.completed if a.name == name)


def _hard_types(p):
    return [v.type for v in validate_schedule(p).violations]


def _force_overlap(p):
    """Slide B onto A's start so the two run concurrently (both durations > 0 →
    they always overlap).  Used by every "capacity exceeded under overlap"
    scenario; B keeps its own duration so no spurious 'duration' violation."""
    a, b = _act(p, 'A'), _act(p, 'B')
    b.startTime = a.startTime
    b.endTime   = b.startTime + timedelta(hours=b.duration)


# ---------------------------------------------------------------------------
# Feasible-by-construction builders (one per topology the scenarios need)
# ---------------------------------------------------------------------------

def _chain(dur_a, dur_b, crew, *, decorate=None):
    """START → A → B → END, each activity needs `crew` MECH from a generous pool.

    `decorate(a, b)` may attach extra per-activity requirements before build.
    """
    a = Activity('A', dur_a, required_resources=_mech(crew))
    b = Activity('B', dur_b, required_resources=_mech(crew))
    if decorate:
        decorate(a, b)
    start, end = Activity('START', 0.0), Activity('END', 0.0)
    fwd = {start: [a], a: [b], b: [end], end: []}
    p = Pert(graph=fwd)
    p.crew_pool         = _rp('MECH', 10)
    p.equipment_pool    = EquipmentPool()
    p.location_pool     = LocationPool()
    p.consumable_pool   = None
    p.system_state_pool = None
    p.startTime = START_DT
    return p


def _diamond(dur_a, dur_b, crew, pool_size, *, decorate=None,
             equipment_pool=None, location_pool=None, system_state_pool=None):
    """START → {A, B} → END (A and B parallel), each needs `crew` MECH.

    With `pool_size` == one activity's demand the scheduler serialises A and B
    (feasible); forcing them to overlap then exceeds capacity.
    """
    a = Activity('A', dur_a, required_resources=_mech(crew))
    b = Activity('B', dur_b, required_resources=_mech(crew))
    if decorate:
        decorate(a, b)
    start, end = Activity('START', 0.0), Activity('END', 0.0)
    fwd = {start: [a, b], a: [end], b: [end], end: []}
    p = Pert(graph=fwd)
    p.crew_pool         = _rp('MECH', pool_size)
    p.equipment_pool    = equipment_pool or EquipmentPool()
    p.location_pool     = location_pool or LocationPool()
    p.consumable_pool   = None
    p.system_state_pool = system_state_pool
    p.startTime = START_DT
    return p


# ---------------------------------------------------------------------------
# Scenario builders + mutations (fuzzed over dur_a, dur_b, crew)
# ---------------------------------------------------------------------------
# Each build_* returns a scheduled, feasible Pert; each mut_* corrupts it so the
# named violation type must fire.  Guarantees noted inline.

def build_completeness(d):   return _finish(_chain(d.dur_a, d.dur_b, d.crew))
def mut_completeness(p):
    # Drop A from the completed collection → len(completed) < len(graph) fires
    # the count-based completeness violation (schedule_validator.py:222).
    a = _act(p, 'A')
    p.completed = type(p.completed)(x for x in p.completed if x is not a)
    cs = getattr(p, '_completed_set', None)
    if cs is not None:
        cs.discard(a)


def build_duration(d):       return _finish(_chain(d.dur_a, d.dur_b, d.crew))
def mut_duration(p):
    # endTime − startTime = 999 h ≫ duration (≤ 20 h) + _DUR_TOL (60 s).
    a = _act(p, 'A')
    a.endTime = a.startTime + timedelta(hours=BIG_HOURS)


def build_precedence(d):     return _finish(_chain(d.dur_a, d.dur_b, d.crew))
def mut_precedence(p):
    # B starts when A starts; A has duration ≥ 1 h > 0, so B.start < A.end.
    a, b = _act(p, 'A'), _act(p, 'B')
    b.startTime = a.startTime
    b.endTime   = b.startTime + timedelta(hours=b.duration)


def build_precedence_lag(d): return _finish(_chain(d.dur_a, d.dur_b, d.crew))
def mut_precedence_lag(p):
    # Inject a lag larger than the actual A→B gap (chain → B starts at A.end,
    # gap ≈ 0), so B.start < A.end + lag.
    a = _act(p, 'A')
    a.successor_lags = {'B': BIG_HOURS}


def build_time_window(d):    return _finish(_chain(d.dur_a, d.dur_b, d.crew))
def mut_time_window(p):
    # A runs [0 h, dur_a ≤ 20 h]; a window at [100 h, 120 h] cannot contain it.
    a = _act(p, 'A')
    a.window_earliest_start_hours = 100.0
    a.window_latest_finish_hours  = 120.0


def build_hold_point(d):
    hp = Activity('HP', 0.0, is_hold_point=True, blocks_tasks=['B'])
    b  = Activity('B', d.dur_b, required_resources=_mech(d.crew))
    start, end = Activity('START', 0.0), Activity('END', 0.0)
    fwd = {start: [hp, b], hp: [b], b: [end], end: []}
    p = Pert(graph=fwd)
    p.crew_pool         = _rp('MECH', 10)
    p.equipment_pool    = EquipmentPool()
    p.location_pool     = LocationPool()
    p.consumable_pool   = None
    p.system_state_pool = None
    p.startTime = START_DT
    return _finish(p)
def mut_hold_point(p):
    # Push the hold point to h=8 and pull the blocked task back to h=0, so B
    # starts before its hold point completes.
    hp, b = _act(p, 'HP'), _act(p, 'B')
    hp.startTime = START_DT + timedelta(hours=8)
    hp.endTime   = hp.startTime
    b.startTime  = START_DT
    b.endTime    = b.startTime + timedelta(hours=b.duration)


def build_crew(d):
    # pool == one activity's demand → serialised (feasible); overlap → 2× > cap.
    return _finish(_diamond(d.dur_a, d.dur_b, d.crew, pool_size=d.crew))
def mut_crew(p):  _force_overlap(p)


def build_substitution(d):
    # A single MECH activity from a generous pool → always feasible, and the
    # engine commits a legal, conserving _actual_resources ({'MECH': crew}).  The
    # mutation replaces it with an illegal breakdown so the substitution-legality
    # check must fire.  (Single activity keeps every OTHER check trivially clean,
    # so only 'substitution' can appear post-mutation.)
    a = Activity('A', d.dur_a, required_resources=_mech(d.crew))
    start, end = Activity('START', 0.0), Activity('END', 0.0)
    fwd = {start: [a], a: [end], end: []}
    p = Pert(graph=fwd)
    p.crew_pool         = _rp('MECH', 10)
    p.equipment_pool    = EquipmentPool()
    p.location_pool     = LocationPool()
    p.consumable_pool   = None
    p.system_state_pool = None
    p.startTime = START_DT
    return _finish(p)
def mut_substitution(p):
    # Charge WELDER — not the requirement's primary skill and not among its
    # (empty) alternatives.  The recorded total still equals the declared demand
    # (conservation holds), so this exercises the max-flow legality path, not the
    # cheap conservation short-circuit: no legal routing staffs a MECH-only
    # requirement from WELDER workers → 'substitution' fires.
    a = _act(p, 'A')
    crew = sum(int(r['crew_count']) for r in a.getRequiredResources())
    a._actual_resources = {'WELDER': crew}


def _crane_pool(qty=1, zone=None):
    ep = EquipmentPool()
    ep.equipment['CRANE'] = EquipmentAvailability(
        'CRANE', 'polar crane',
        [{'start_date': START_DT, 'end_date': START_DT + HORIZON,
          'quantity_available': qty}],
        **({'zone_id': zone} if zone is not None else {}))
    return ep

def build_equipment(d):
    def deco(a, b):
        a.required_equipment = [{'equipment_id': 'CRANE', 'quantity_needed': 1}]
        b.required_equipment = [{'equipment_id': 'CRANE', 'quantity_needed': 1}]
    # 1 CRANE, generous crew → serialised on the crane (feasible); overlap → 2>1.
    return _finish(_diamond(d.dur_a, d.dur_b, crew=1, pool_size=10,
                            decorate=deco, equipment_pool=_crane_pool(qty=1)))
def mut_equipment(p):  _force_overlap(p)


def _loc_pool(max_tasks, max_workers):
    lp = LocationPool()
    lp.locations['LOC_1'] = LocationAvailability(
        'LOC_1', 'test location',
        [{'start_date': START_DT, 'end_date': START_DT + HORIZON,
          'max_concurrent_tasks': max_tasks,
          'max_concurrent_workers': max_workers}])
    return lp

def build_location_tasks(d):
    def deco(a, b):
        a.location_id = 'LOC_1'
        b.location_id = 'LOC_1'
    # max_tasks=1 → serialised (feasible); overlap → 2 tasks > 1.
    return _finish(_diamond(d.dur_a, d.dur_b, crew=1, pool_size=10, decorate=deco,
                            location_pool=_loc_pool(max_tasks=1, max_workers=99)))
def mut_location_tasks(p):  _force_overlap(p)


def build_location_workers(d):
    def deco(a, b):
        a.location_id = 'LOC_1'
        b.location_id = 'LOC_1'
    # Each activity needs `crew` workers; cap = 2·crew − 1 admits one activity
    # (crew ≤ 2·crew−1 for crew ≥ 1) but not two overlapping (2·crew > 2·crew−1).
    cap = 2 * d.crew - 1
    return _finish(_diamond(d.dur_a, d.dur_b, crew=d.crew, pool_size=10, decorate=deco,
                            location_pool=_loc_pool(max_tasks=99, max_workers=cap)))
def mut_location_workers(p):  _force_overlap(p)


def build_equipment_zone(d):
    a = Activity('A', d.dur_a, required_resources=_mech(d.crew),
                 required_equipment=[{'equipment_id': 'CRANE', 'quantity_needed': 1}])
    a.zone_ids = ['CONTAINMENT']
    start, end = Activity('START', 0.0), Activity('END', 0.0)
    fwd = {start: [a], a: [end], end: []}
    p = Pert(graph=fwd)
    p.crew_pool         = _rp('MECH', 10)
    p.equipment_pool    = _crane_pool(qty=1, zone='CONTAINMENT')
    p.location_pool     = LocationPool()
    p.consumable_pool   = None
    p.system_state_pool = None
    p.startTime = START_DT
    return _finish(p)
def mut_equipment_zone(p):
    # CRANE is locked to CONTAINMENT; move A to a foreign zone.
    _act(p, 'A').zone_ids = ['AUX_BLDG']


def build_consumable(d):
    def deco(a, b):
        a.required_consumables = [{'item_id': 'SEAL', 'quantity_needed': 1}]
        b.required_consumables = [{'item_id': 'SEAL', 'quantity_needed': 1}]
    p = _chain(d.dur_a, d.dur_b, d.crew, decorate=deco)
    cp = ConsumablePool()
    cp.items['SEAL']           = 2.0
    cp.remaining['SEAL']       = 2.0
    cp.description['SEAL']     = 'valve seals'
    cp.restocks['SEAL']        = []
    cp._restock_cursor['SEAL'] = -1.0
    p.consumable_pool = cp
    return _finish(p)
def mut_consumable(p):
    # A and B each consume 1 SEAL; the validator replay resets to pool.items, so
    # cutting declared stock to 1 makes the replay run short.
    p.consumable_pool.items['SEAL'] = 1.0


def build_system_state(d):
    # Build with COMPATIBLE states (both CLOSED) so A and B may run concurrently
    # in the diamond without conflict — feasible pre-mutation.
    def deco(a, b):
        a.required_system_states = [{'system_id': 'V1', 'required_state': 'CLOSED'}]
        b.required_system_states = [{'system_id': 'V1', 'required_state': 'CLOSED'}]
    return _finish(_diamond(d.dur_a, d.dur_b, crew=1, pool_size=10, decorate=deco,
                            system_state_pool=SystemStatePool()))
def mut_system_state(p):
    # Flip B to the incompatible state and force the overlap → conflicting states
    # concurrent on system V1.
    _act(p, 'B').required_system_states = [{'system_id': 'V1', 'required_state': 'OPEN'}]
    _force_overlap(p)


def build_shift(d):
    a = Activity('A', d.dur_a, required_resources=_mech(d.crew))
    start, end = Activity('START', 0.0), Activity('END', 0.0)
    fwd = {start: [a], a: [end], end: []}
    p = Pert(graph=fwd)
    p.crew_pool         = _rp('MECH', 10)
    p.equipment_pool    = EquipmentPool()
    p.location_pool     = LocationPool()
    p.consumable_pool   = None
    p.system_state_pool = None
    p.startTime = START_DT
    p.working_hours_per_day = 24    # 24-h shift → feasible pre-mutation
    p.shift_start_hour      = 0
    return _finish(p)
def mut_shift(p):
    # Tighten to a daytime shift [08:00, 20:00]; A ran at hour 0 (midnight).
    p.working_hours_per_day = 12
    p.shift_start_hour      = 8


def build_dose(d):
    # A *single* activity: a dose-tracked skill is resource_type='consumable', so
    # its count DEPLETES per activity — a chain would starve later tasks when
    # peak < crew·n.  One activity needs `crew` from a peak-`crew` pool: always
    # feasible, and dose accumulates while scheduling.
    a = Activity('A', d.dur_a, required_resources=_mech(d.crew))
    # Rate kept low enough that even the longest fuzzed run (20 h → 200 mrem)
    # stays under the 500-mrem/worker budget: the ENGINE gates placement on the
    # dose budget via .fits(), so an over-budget build would deadlock (not just
    # fail validation).  The mutation drives the *validator* over budget instead.
    a.dose_rate_mrem_per_hour = 10.0
    start, end = Activity('START', 0.0), Activity('END', 0.0)
    fwd = {start: [a], a: [end], end: []}
    p = Pert(graph=fwd)
    # MECH must be a *consumable* skill for dose tracking; populate dose_trackers
    # BEFORE scheduling (the graph= path leaves them {}, and the validator's
    # `if not dose_trackers: return` guard would otherwise no-op).
    p.crew_pool         = _rp('MECH', d.crew, consumable=True, dose_budget=500.0)
    p.equipment_pool    = EquipmentPool()
    p.location_pool     = LocationPool()
    p.consumable_pool   = None
    p.system_state_pool = None
    p.startTime = START_DT
    p.dose_trackers = p.crew_pool.build_dose_trackers()
    return _finish(p)
def mut_dose(p):
    t = p.dose_trackers['MECH']
    t.consumed_mrem = t.total_budget_mrem + 1000.0   # > budget + 1e-6


# ---------------------------------------------------------------------------
# Scenario table
# ---------------------------------------------------------------------------

Scenario = namedtuple('Scenario', 'name build mutate expected')

SCENARIOS = [
    Scenario('completeness',       build_completeness,      mut_completeness,      'completeness'),
    Scenario('duration',           build_duration,          mut_duration,          'duration'),
    Scenario('precedence',         build_precedence,        mut_precedence,        'precedence'),
    Scenario('precedence_lag',     build_precedence_lag,    mut_precedence_lag,    'precedence'),
    Scenario('time_window',        build_time_window,       mut_time_window,       'time_window'),
    Scenario('hold_point',         build_hold_point,        mut_hold_point,        'hold_point'),
    Scenario('crew',               build_crew,              mut_crew,              'crew'),
    Scenario('substitution',       build_substitution,      mut_substitution,      'substitution'),
    Scenario('equipment',          build_equipment,         mut_equipment,         'equipment'),
    Scenario('location_tasks',     build_location_tasks,    mut_location_tasks,    'location'),
    Scenario('location_workers',   build_location_workers,  mut_location_workers,  'location'),
    Scenario('equipment_zone',     build_equipment_zone,    mut_equipment_zone,    'equipment_zone'),
    Scenario('consumable',         build_consumable,        mut_consumable,        'consumable'),
    Scenario('system_state',       build_system_state,      mut_system_state,      'system_state'),
    Scenario('shift_calendar',     build_shift,             mut_shift,             'shift_calendar'),
    Scenario('dose',               build_dose,              mut_dose,              'dose'),
]

# Fuzz only what cannot break the mutation's guarantee: activity durations
# (kept > 0 so intervals are non-degenerate and overlaps really overlap) and
# crew demand (kept ≥ 1).
_Fuzz = namedtuple('_Fuzz', 'dur_a dur_b crew')


@st.composite
def fuzz_params(draw):
    return _Fuzz(
        dur_a=draw(st.floats(min_value=1.0, max_value=20.0,
                             allow_nan=False, allow_infinity=False)),
        dur_b=draw(st.floats(min_value=1.0, max_value=20.0,
                             allow_nan=False, allow_infinity=False)),
        crew=draw(st.integers(min_value=1, max_value=4)),
    )


# ---------------------------------------------------------------------------
# The fuzzed sensitivity test
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
@given(params=fuzz_params())
def test_oracle_detects_mutation(scenario, params):
    """Feasible build must validate; the mutation must make the oracle fire."""
    p = scenario.build(params)

    # (1) Soundness: the oracle must accept the un-mutated, feasible schedule.
    pre = validate_schedule(p)
    assert pre.is_feasible, (
        f"[{scenario.name}] oracle rejected a feasible schedule "
        f"(params={params}):\n{pre.summary()}")

    # (2) Sensitivity: after the corruption the expected hard violation fires.
    scenario.mutate(p)
    types = _hard_types(p)
    assert scenario.expected in types, (
        f"[{scenario.name}] mutation did not raise '{scenario.expected}' "
        f"(params={params}); violations={types}")


# ---------------------------------------------------------------------------
# Meta-guard: the scenario table covers every hard check
# ---------------------------------------------------------------------------

def test_scenarios_cover_every_hard_check():
    """Every one of the 13 validate_schedule hard checks has a mutation scenario.

    If a new check is added to schedule_validator without a scenario here, this
    fails loudly rather than letting the new check go un-fuzzed.
    """
    covered = {s.expected for s in SCENARIOS}
    missing = ALL_CHECK_TYPES - covered
    assert not missing, f"hard checks with no mutation scenario: {sorted(missing)}"
    extra = covered - ALL_CHECK_TYPES
    assert not extra, f"scenarios target unknown violation types: {sorted(extra)}"
