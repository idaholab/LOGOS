"""
test_oracle_decoupling.py — prove the oracle's availability/window checks are
decoupled from the engine's own primitives (ORACLE_COMPLETENESS_2026-09-07.md
Gap 1 / §6.4).

Background
----------
The fuzz-and-freeze program (src/CPM/devLogs/RCPSP_ROBUSTNESS_2026-09-07.md)
rests on ``schedule_validator.validate_schedule`` being an *independent*
feasibility oracle.  Historically its resource and window checks answered
"how much capacity is available over [t, nxt)?" and "what are this activity's
time windows?" by calling the engine's own primitives
(``crew_pool.get_availability_in_range``,
``equipment_pool.get_availability_in_range``,
``location_pool.get_capacity_in_range``, ``Pert._resolve_windows``).  That is a
**correlated blind spot**: a bug *inside* one of those primitives is invisible
to the oracle, because the oracle asks the engine the same question and gets the
same wrong answer.

§6.4 reimplemented the min-over-overlap reduction and the window resolution
directly inside the oracle, reading the raw declared data (the availability
objects' ``.periods`` and the activity's window fields) via the module helpers
``_min_avail_over`` / ``_min_location_cap_over`` / ``_resolve_windows_indep``.

This module proves that decoupling two ways:

1. **Differential blind-spot tests (the headline).**  Build a schedule that is
   *genuinely infeasible in the declared data*, then monkeypatch the engine
   primitive to *lie* the way a buggy primitive would (report plenty of
   capacity / no window).  The oracle must **still** flag the infeasibility —
   which it can only do if it no longer trusts the primitive.  Each test also
   asserts the patched primitive really does return the masking value, so the
   test proves independence, not mere redundancy.

2. **Reduction-correctness (parity) tests.**  Fuzz period tables and query
   ranges and assert each helper matches a *dead-simple, independently-derived
   ground truth* (a brute-force min over a fine time-sampling of the range) —
   validating the helper's correctness without reference to the engine.  A
   companion equivalence check asserts ``helper == engine primitive`` on random
   *valid* pools, documenting that §6.4 shipped no behaviour change on
   feasible data.

Section 3 extends the same discipline to **touch-point 5** (§8b, still Gap 1):
the oracle's ``_crew_demand`` used to trust the engine-committed
``act._actual_resources`` verbatim as the crew sweep's per-skill demand, so a
buggy/lying substitution breakdown — one that records *fewer* workers than
declared, or charges a skill no requirement allows — fooled the sweep silently.
``_check_substitution_legality`` now independently certifies that the committed
breakdown is a **legal, demand-conserving** resolution of the *declared*
``required_resources`` (reading declared data only).  The differential tests here
show each lie is invisible to ``_crew_demand`` (the old blind spot) yet caught by
the new check; the parity tests pin the max-flow legality test
(``_bipartite_saturates`` / ``_substitution_is_feasible``) to an independent
brute-force (Hall-condition) ground truth.

The module self-skips where Hypothesis is not installed (mirrors
test_property_based.py / test_oracle_mutation.py).
"""
import os
import types
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
)
from CPM.schedule_validator import (
    validate_schedule,
    _min_avail_over,
    _min_location_cap_over,
    _resolve_windows_indep,
    _crew_demand,
    _allowed_skills,
    _bipartite_saturates,
    _substitution_is_feasible,
    _check_substitution_legality,
)


# ---------------------------------------------------------------------------
# Hypothesis profiles
# ---------------------------------------------------------------------------
# Registered here as well as in test_property_based.py so this file is runnable
# standalone (the per-file RAVEN registration runs `pytest
# test_oracle_decoupling.py` in isolation).  register_profile is idempotent, so
# duplicating the block is harmless.

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
# Shared constants and minimal builders (kept local so the file is standalone)
# ---------------------------------------------------------------------------

START_DT = datetime(2026, 1, 1, 0, 0)
HORIZON  = timedelta(days=30)
MAX_TIME = 500
SGS      = 'max_use_res_ranked'
MASK     = 9999          # the "plenty of capacity" value a buggy primitive lies with


def _rp(skill, count):
    rp = ResourcePool()
    rp.resources[skill] = ResourceAvailability(
        skill,
        [{'start_date': START_DT, 'end_date': START_DT + HORIZON,
          'available_count': count}])
    return rp


def _mech(crew):
    return [{'skill_type': 'MECH', 'crew_count': crew, 'alternative_skill_types': []}]


def _crane_pool(qty=1, zone=None):
    ep = EquipmentPool()
    ep.equipment['CRANE'] = EquipmentAvailability(
        'CRANE', 'polar crane',
        [{'start_date': START_DT, 'end_date': START_DT + HORIZON,
          'quantity_available': qty}],
        **({'zone_id': zone} if zone is not None else {}))
    return ep


def _loc_pool(max_tasks, max_workers):
    lp = LocationPool()
    lp.locations['LOC_1'] = LocationAvailability(
        'LOC_1', 'test location',
        [{'start_date': START_DT, 'end_date': START_DT + HORIZON,
          'max_concurrent_tasks': max_tasks,
          'max_concurrent_workers': max_workers}])
    return lp


def _diamond(crew, pool_size, *, decorate=None,
             equipment_pool=None, location_pool=None):
    """START → {A, B} → END (A, B parallel), each needs `crew` MECH."""
    a = Activity('A', 4.0, required_resources=_mech(crew))
    b = Activity('B', 4.0, required_resources=_mech(crew))
    if decorate:
        decorate(a, b)
    start, end = Activity('START', 0.0), Activity('END', 0.0)
    fwd = {start: [a, b], a: [end], b: [end], end: []}
    p = Pert(graph=fwd)
    p.crew_pool         = _rp('MECH', pool_size)
    p.equipment_pool    = equipment_pool or EquipmentPool()
    p.location_pool     = location_pool or LocationPool()
    p.consumable_pool   = None
    p.system_state_pool = None
    p.startTime = START_DT
    return p


def _chain(crew):
    """START → A → B → END, each needs `crew` MECH from a generous pool."""
    a = Activity('A', 4.0, required_resources=_mech(crew))
    b = Activity('B', 4.0, required_resources=_mech(crew))
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


def _finish(p):
    p.generateInfo()
    p.calculateScheduleWithResources(sgs=SGS, max_time_hours=MAX_TIME)
    return p


def _act(p, name):
    return next(a for a in p.completed if a.name == name)


def _force_overlap(p):
    """Slide B onto A's start so the two run concurrently (both durations > 0)."""
    a, b = _act(p, 'A'), _act(p, 'B')
    b.startTime = a.startTime
    b.endTime   = b.startTime + timedelta(hours=b.duration)


def _types(p):
    return [v.type for v in validate_schedule(p).violations]


# ===========================================================================
# 1. Differential blind-spot tests
#
# Each builds a genuinely-infeasible schedule, then patches the engine
# primitive to lie.  The oracle must STILL fire because §6.4 recomputes from
# raw data.  A "rationale" assertion confirms the patched primitive really does
# return the masking value (so the pre-§6.4 oracle would have been blind).
# ===========================================================================

def test_crew_blind_spot_lying_primitive(monkeypatch):
    """A crew over-allocation is caught even when the crew primitive lies."""
    # 2 activities each need 2 MECH from a pool of 2 → serialised is feasible;
    # forcing overlap makes demand 4 > 2 (genuinely infeasible in declared data).
    p = _finish(_diamond(crew=2, pool_size=2))
    assert validate_schedule(p).is_feasible          # feasible before corruption
    _force_overlap(p)

    # Make the engine's availability query lie: "plenty of crew, always".
    monkeypatch.setattr(p.crew_pool, 'get_availability_in_range',
                        lambda *a, **k: MASK)
    monkeypatch.setattr(p.crew_pool.resources['MECH'], 'get_availability_in_range',
                        lambda *a, **k: MASK)

    # Rationale: the patched primitive really would have blinded the old oracle.
    assert p.crew_pool.get_availability_in_range('MECH', START_DT,
                                                 START_DT + HORIZON) == MASK
    # The decoupled oracle still catches it (reads raw available_count == 2).
    assert 'crew' in _types(p)


def test_equipment_blind_spot_lying_primitive(monkeypatch):
    """An equipment over-allocation is caught even when the primitive lies."""
    def deco(a, b):
        a.required_equipment = [{'equipment_id': 'CRANE', 'quantity_needed': 1}]
        b.required_equipment = [{'equipment_id': 'CRANE', 'quantity_needed': 1}]
    # 1 CRANE, generous crew → serialised on the crane; overlap → 2 > 1.
    p = _finish(_diamond(crew=1, pool_size=10, decorate=deco,
                         equipment_pool=_crane_pool(qty=1)))
    assert validate_schedule(p).is_feasible
    _force_overlap(p)

    monkeypatch.setattr(p.equipment_pool, 'get_availability_in_range',
                        lambda *a, **k: MASK)
    monkeypatch.setattr(p.equipment_pool.equipment['CRANE'], 'get_availability_in_range',
                        lambda *a, **k: MASK)

    assert p.equipment_pool.get_availability_in_range('CRANE', START_DT,
                                                      START_DT + HORIZON) == MASK
    assert 'equipment' in _types(p)


def test_location_tasks_blind_spot_lying_primitive(monkeypatch):
    """A location task-concurrency breach is caught even when the primitive lies."""
    def deco(a, b):
        a.location_id = 'LOC_1'
        b.location_id = 'LOC_1'
    # max_tasks=1 → serialised; overlap → 2 tasks > 1.
    p = _finish(_diamond(crew=1, pool_size=10, decorate=deco,
                         location_pool=_loc_pool(max_tasks=1, max_workers=99)))
    assert validate_schedule(p).is_feasible
    _force_overlap(p)

    lie = {'max_tasks': MASK, 'max_workers': None}
    monkeypatch.setattr(p.location_pool, 'get_capacity_in_range',
                        lambda *a, **k: lie)
    monkeypatch.setattr(p.location_pool.locations['LOC_1'], 'get_capacity_in_range',
                        lambda *a, **k: lie)

    assert p.location_pool.get_capacity_in_range('LOC_1', START_DT,
                                                 START_DT + HORIZON) == lie
    assert 'location' in _types(p)


def test_location_workers_blind_spot_lying_primitive(monkeypatch):
    """A location worker-concurrency breach is caught even when the primitive lies."""
    def deco(a, b):
        a.location_id = 'LOC_1'
        b.location_id = 'LOC_1'
    # Each activity needs 2 workers; cap = 3 admits one activity but not two
    # overlapping (2 ≤ 3, 4 > 3).  max_tasks generous so only workers can breach.
    p = _finish(_diamond(crew=2, pool_size=10, decorate=deco,
                         location_pool=_loc_pool(max_tasks=99, max_workers=3)))
    assert validate_schedule(p).is_feasible
    _force_overlap(p)

    lie = {'max_tasks': MASK, 'max_workers': MASK}
    monkeypatch.setattr(p.location_pool, 'get_capacity_in_range',
                        lambda *a, **k: lie)
    monkeypatch.setattr(p.location_pool.locations['LOC_1'], 'get_capacity_in_range',
                        lambda *a, **k: lie)

    assert p.location_pool.get_capacity_in_range('LOC_1', START_DT,
                                                 START_DT + HORIZON) == lie
    assert 'location' in _types(p)


def test_time_window_blind_spot_lying_primitive(monkeypatch):
    """A time-window breach is caught even when _resolve_windows lies (returns [])."""
    p = _finish(_chain(crew=1))
    a = _act(p, 'A')                       # A runs [0 h, 4 h]
    a.window_earliest_start_hours = 100.0  # impose a window it cannot fit
    a.window_latest_finish_hours  = 120.0
    # The declared window makes it genuinely infeasible even before patching.
    assert 'time_window' in _types(p)

    # Make the engine resolver lie: "this activity has no window constraint".
    monkeypatch.setattr(p, '_resolve_windows', lambda act: [])

    assert p._resolve_windows(a) == []     # rationale: old oracle would skip it
    assert 'time_window' in _types(p)      # decoupled oracle still fires


# ===========================================================================
# 2. Reduction-correctness (parity) tests
# ===========================================================================

def _count_at(periods, key, hours):
    """Availability at an instant `hours` after START_DT (0 in any gap)."""
    t = START_DT + timedelta(hours=hours)
    for p in periods:
        if p['start_date'] <= t < p['end_date']:
            return p[key]
    return 0


def _seg_at(periods, hours):
    t = START_DT + timedelta(hours=hours)
    for p in periods:
        if p['start_date'] <= t < p['end_date']:
            return p
    return None


@st.composite
def _contiguous_segments(draw, count_key):
    """A gapless partition of [0, total) hours into integer-hour segments plus a
    fully-covered integer-hour query range [qs, qe).  Gapless coverage makes a
    midpoint-sampling ground truth exactly equal to min-over-overlapping-periods.
    """
    n     = draw(st.integers(min_value=1, max_value=6))
    durs  = [draw(st.integers(min_value=1, max_value=8)) for _ in range(n)]
    vals  = [draw(st.integers(min_value=0, max_value=20)) for _ in range(n)]
    periods, cursor = [], 0
    for d, v in zip(durs, vals):
        periods.append({
            'start_date': START_DT + timedelta(hours=cursor),
            'end_date':   START_DT + timedelta(hours=cursor + d),
            count_key:    v,
        })
        cursor += d
    total = cursor
    qs = draw(st.integers(min_value=0, max_value=total - 1))
    qe = draw(st.integers(min_value=qs + 1, max_value=total))
    return periods, qs, qe


@st.composite
def _contiguous_loc_segments(draw):
    """Gapless location segments (tasks + optional worker limit) + query range."""
    n      = draw(st.integers(min_value=1, max_value=6))
    durs   = [draw(st.integers(min_value=1, max_value=8)) for _ in range(n)]
    tasks  = [draw(st.integers(min_value=0, max_value=20)) for _ in range(n)]
    wkrs   = [draw(st.one_of(st.none(), st.integers(min_value=0, max_value=40)))
              for _ in range(n)]
    periods, cursor = [], 0
    for d, t, w in zip(durs, tasks, wkrs):
        periods.append({
            'start_date':             START_DT + timedelta(hours=cursor),
            'end_date':               START_DT + timedelta(hours=cursor + d),
            'max_concurrent_tasks':   t,
            'max_concurrent_workers': w,
        })
        cursor += d
    total = cursor
    qs = draw(st.integers(min_value=0, max_value=total - 1))
    qe = draw(st.integers(min_value=qs + 1, max_value=total))
    return periods, qs, qe


@st.composite
def _valid_periods(draw, count_key):
    """Sorted, non-overlapping, possibly-gapped periods (what the availability
    constructors accept) + an arbitrary integer-hour query range (qe may equal
    qs to exercise the zero-width boundary).
    """
    n = draw(st.integers(min_value=0, max_value=6))
    periods, cursor = [], 0
    for _ in range(n):
        cursor += draw(st.integers(min_value=0, max_value=4))   # gap
        dur = draw(st.integers(min_value=1, max_value=8))
        periods.append({
            'start_date': START_DT + timedelta(hours=cursor),
            'end_date':   START_DT + timedelta(hours=cursor + dur),
            count_key:    draw(st.integers(min_value=0, max_value=20)),
        })
        cursor += dur
    qs = draw(st.integers(min_value=0, max_value=cursor + 5))
    qe = draw(st.integers(min_value=qs, max_value=qs + cursor + 5))
    return periods, qs, qe


@given(data=_contiguous_segments('available_count'))
@settings()
def test_min_avail_over_matches_ground_truth_crew(data):
    """`_min_avail_over` (crew key) == brute-force min over sampled instants."""
    periods, qs, qe = data
    gt  = min(_count_at(periods, 'available_count', h + 0.5) for h in range(qs, qe))
    got = _min_avail_over(periods, 'available_count',
                          START_DT + timedelta(hours=qs),
                          START_DT + timedelta(hours=qe))
    assert got == gt


@given(data=_contiguous_segments('quantity_available'))
@settings()
def test_min_avail_over_matches_ground_truth_equipment(data):
    """`_min_avail_over` (equipment key) == brute-force min over sampled instants."""
    periods, qs, qe = data
    gt  = min(_count_at(periods, 'quantity_available', h + 0.5) for h in range(qs, qe))
    got = _min_avail_over(periods, 'quantity_available',
                          START_DT + timedelta(hours=qs),
                          START_DT + timedelta(hours=qe))
    assert got == gt


@given(data=_contiguous_loc_segments())
@settings()
def test_min_location_cap_over_matches_ground_truth(data):
    """`_min_location_cap_over` == brute-force min capacity over sampled instants,
    including the "None when no overlapping period constrains workers" rule."""
    periods, qs, qe = data
    segs, seen = [], set()
    for h in range(qs, qe):
        seg = _seg_at(periods, h + 0.5)
        if seg is not None and id(seg) not in seen:
            seen.add(id(seg))
            segs.append(seg)
    tasks_gt = min(s['max_concurrent_tasks'] for s in segs)
    wl = [s['max_concurrent_workers'] for s in segs
          if s['max_concurrent_workers'] is not None]
    workers_gt = min(wl) if wl else None

    got = _min_location_cap_over(periods,
                                 START_DT + timedelta(hours=qs),
                                 START_DT + timedelta(hours=qe))
    assert got == {'max_tasks': tasks_gt, 'max_workers': workers_gt}


@given(data=_valid_periods('available_count'))
@settings()
def test_min_avail_over_equals_engine_crew(data):
    """§6.4 shipped no behaviour change: crew helper == engine primitive on valid data."""
    periods, qs, qe = data
    start = START_DT + timedelta(hours=qs)
    end   = START_DT + timedelta(hours=qe)
    engine = ResourceAvailability('MECH', periods).get_availability_in_range(start, end)
    assert _min_avail_over(periods, 'available_count', start, end) == engine


@given(data=_valid_periods('quantity_available'))
@settings()
def test_min_avail_over_equals_engine_equipment(data):
    """Equipment helper == engine primitive on valid data."""
    periods, qs, qe = data
    start = START_DT + timedelta(hours=qs)
    end   = START_DT + timedelta(hours=qe)
    engine = EquipmentAvailability('EQ', 'desc', periods).get_availability_in_range(start, end)
    assert _min_avail_over(periods, 'quantity_available', start, end) == engine


@st.composite
def _valid_loc_periods(draw):
    """Sorted, non-overlapping, possibly-gapped location periods + query range."""
    n = draw(st.integers(min_value=0, max_value=6))
    periods, cursor = [], 0
    for _ in range(n):
        cursor += draw(st.integers(min_value=0, max_value=4))
        dur = draw(st.integers(min_value=1, max_value=8))
        periods.append({
            'start_date':             START_DT + timedelta(hours=cursor),
            'end_date':               START_DT + timedelta(hours=cursor + dur),
            'max_concurrent_tasks':   draw(st.integers(min_value=0, max_value=20)),
            'max_concurrent_workers': draw(st.one_of(st.none(),
                                                     st.integers(min_value=0, max_value=40))),
        })
        cursor += dur
    qs = draw(st.integers(min_value=0, max_value=cursor + 5))
    qe = draw(st.integers(min_value=qs, max_value=qs + cursor + 5))
    return periods, qs, qe


@given(data=_valid_loc_periods())
@settings()
def test_min_location_cap_over_equals_engine(data):
    """Location helper == engine primitive on valid data."""
    periods, qs, qe = data
    start = START_DT + timedelta(hours=qs)
    end   = START_DT + timedelta(hours=qe)
    engine = LocationAvailability('LOC', 'desc', periods).get_capacity_in_range(start, end)
    assert _min_location_cap_over(periods, start, end) == engine


# ---------------------------------------------------------------------------
# Window-resolution parity
# ---------------------------------------------------------------------------

# A throwaway Pert instance whose bound `_resolve_windows` reads only `act`
# attributes (never `self`), used as the engine reference for equivalence.
_A, _B = Activity('A', 1.0), Activity('B', 0.0)
_REF_PERT = Pert(graph={_A: [_B], _B: []})

_FIN = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)


@st.composite
def _window_act(draw):
    """A stub activity exercising each branch of window resolution."""
    ns = types.SimpleNamespace()
    mode = draw(st.integers(min_value=0, max_value=2))
    if mode == 0:                                   # multi-window list
        k = draw(st.integers(min_value=1, max_value=3))
        ns.time_windows = [{'earliest': draw(_FIN), 'latest': draw(_FIN)}
                           for _ in range(k)]
    elif mode == 1:                                 # legacy scalar fields
        ns.time_windows = []
        if draw(st.booleans()):
            ns.window_earliest_start_hours = draw(_FIN)
        if draw(st.booleans()):
            ns.window_latest_finish_hours = draw(_FIN)
    else:                                           # unconstrained
        ns.time_windows = []
    return ns


@given(act=_window_act())
@settings()
def test_resolve_windows_indep_matches_engine(act):
    """`_resolve_windows_indep` reproduces `Pert._resolve_windows` exactly."""
    assert _resolve_windows_indep(act) == _REF_PERT._resolve_windows(act)


# ===========================================================================
# 3. Substitution-legality decoupling (touch-point 5, Gap 1)
#
# `_crew_demand` trusts the engine-committed `act._actual_resources` verbatim as
# the crew sweep's per-skill demand.  A buggy/lying breakdown therefore fools the
# sweep two ways: recording FEWER workers than declared (non-conservation), or
# charging a skill no requirement allows (illegal substitution).  The new
# `_check_substitution_legality` closes both by certifying the committed
# breakdown is a legal, demand-conserving resolution of the DECLARED
# requirements, reading declared data only.
# ===========================================================================


def _req(skill, crew, alt=()):
    """One `required_resources` entry."""
    return {'skill_type': skill, 'crew_count': crew,
            'alternative_skill_types': list(alt)}


def _single(required_resources, pool_skill='MECH', pool_size=10):
    """START → A → END; A carries `required_resources`, staffed from a generous
    single-skill pool.  Scheduled and returned (A is in `.completed`)."""
    a = Activity('A', 4.0, required_resources=required_resources)
    start, end = Activity('START', 0.0), Activity('END', 0.0)
    fwd = {start: [a], a: [end], end: []}
    p = Pert(graph=fwd)
    p.crew_pool         = _rp(pool_skill, pool_size)
    p.equipment_pool    = EquipmentPool()
    p.location_pool     = LocationPool()
    p.consumable_pool   = None
    p.system_state_pool = None
    p.startTime = START_DT
    return _finish(p)


def _subst_types(p):
    """Violation types from `_check_substitution_legality` alone (isolates the
    new check from the rest of `validate_schedule`)."""
    v, w = [], []
    _check_substitution_legality(p, v, w)
    return [x.type for x in v]


# ---------------------------------------------------------------------------
# 3a. Differential blind-spot tests
# ---------------------------------------------------------------------------

def test_substitution_underrecord_blind_to_crew_demand():
    """A non-conserving under-count fools `_crew_demand` but not the new check."""
    p = _single([_req('MECH', 3)])            # declares 3 MECH
    a = _act(p, 'A')
    a._actual_resources = {'MECH': 1}         # engine "forgot" 2 workers

    # (a) the crew sweep's demand path swallows the lie verbatim …
    assert _crew_demand(a) == {'MECH': 1}
    # … so the sweep sees demand 1 ≤ pool and stays silent (the old blind spot).
    assert 'crew' not in _types(p)
    # (b) the independent legality check still fires.
    assert 'substitution' in _subst_types(p)
    assert 'substitution' in _types(p)


def test_substitution_illegal_skill_blind_to_crew_demand():
    """An illegal-skill charge fools `_crew_demand` but not the new check."""
    p = _single([_req('MECH', 2, alt=['WELDER'])])   # MECH or WELDER only
    a = _act(p, 'A')
    a._actual_resources = {'ELEC': 2}                # neither primary nor alt

    assert _crew_demand(a) == {'ELEC': 2}
    # ELEC is absent from the pool → 0 availability → the crew sweep's `avail > 0`
    # guard means it cannot fire, so the illegal charge is invisible to it …
    assert 'crew' not in _types(p)
    # … while conservation holds (2 == 2), so only the max-flow legality path can
    # catch it — and it does.
    assert 'substitution' in _subst_types(p)
    assert 'substitution' in _types(p)


def test_substitution_legal_substitution_is_silent():
    """A genuinely legal substitution ({WELDER:2} for a MECH/WELDER requirement)
    conserves demand and routes legally → the check stays silent (positive
    control: the decoupling is behaviour-preserving on correct engine output)."""
    p = _single([_req('MECH', 2, alt=['WELDER'])])
    a = _act(p, 'A')
    a._actual_resources = {'WELDER': 2}       # legal alternative, full demand
    assert _subst_types(p) == []
    assert 'substitution' not in _types(p)


def test_substitution_schedule_then_corrupt():
    """Feasible as scheduled; corrupting the committed breakdown to a conserving
    but ILLEGAL routing makes `validate_schedule` fire 'substitution' end-to-end
    (and, being conserving, only the max-flow legality path can catch it)."""
    p = _single([_req('MECH', 3)])
    assert validate_schedule(p).is_feasible          # engine's own record is legal
    # 1 legal MECH + 2 illegal WELDER: sums to the declared 3 (conservation holds)
    # yet no legal routing staffs a MECH-only requirement from WELDER workers.
    _act(p, 'A')._actual_resources = {'MECH': 1, 'WELDER': 2}
    result = validate_schedule(p)
    assert not result.is_feasible
    assert 'substitution' in [v.type for v in result.violations]


def test_overlap_trap_needs_maxflow_not_membership():
    """The membership-only shortcut is unsound; the max-flow check is not.

    Required [{MECH,1},{ELEC,1}] with recorded {MECH:2}: MECH is "allowed" (by the
    first requirement) and the recorded total matches the declared total, so a
    per-skill membership + conservation test would wrongly pass.  No legal routing
    fills the ELEC requirement, though — only the bipartite max-flow catches it."""
    # A needs 1 MECH + 1 ELEC; stock both so the engine schedules it feasibly
    # (with {MECH:1, ELEC:1}) before we corrupt the committed record.
    a = Activity('A', 4.0, required_resources=[_req('MECH', 1), _req('ELEC', 1)])
    start, end = Activity('START', 0.0), Activity('END', 0.0)
    fwd = {start: [a], a: [end], end: []}
    p = Pert(graph=fwd)
    rp = _rp('MECH', 10)
    rp.resources['ELEC'] = ResourceAvailability(
        'ELEC', [{'start_date': START_DT, 'end_date': START_DT + HORIZON,
                  'available_count': 10}])
    p.crew_pool         = rp
    p.equipment_pool    = EquipmentPool()
    p.location_pool     = LocationPool()
    p.consumable_pool   = None
    p.system_state_pool = None
    p.startTime = START_DT
    p = _finish(p)
    _act(p, 'A')._actual_resources = {'MECH': 2}     # every worker "allowed", total ok
    assert 'substitution' in _subst_types(p)


# ---------------------------------------------------------------------------
# 3b. Property / parity tests — pin the flow to an independent ground truth
# ---------------------------------------------------------------------------
# Ground truth is Hall's saturation (defect) condition for a transportation
# problem, enumerated over all requirement subsets — completely independent of
# the Edmonds–Karp max-flow in `_bipartite_saturates`.  For a subset S of
# requirements, the demands in S are jointly fillable iff the total supply of
# skills that may legally staff SOME requirement in S is at least the total
# demand of S; the whole instance saturates iff this holds for every S.

_SKILLS = ['MECH', 'ELEC', 'WELDER', 'IC']


def _saturates_bruteforce(supply: dict, demand: list, allowed: list) -> bool:
    """Hall/defect condition over every requirement subset (ground truth)."""
    n = len(demand)
    for mask in range(1 << n):
        subset = [j for j in range(n) if mask & (1 << j)]
        need = sum(demand[j] for j in subset)
        adj_supply = sum(w for s, w in supply.items()
                         if w > 0 and any(s in allowed[j] for j in subset))
        if need > adj_supply:
            return False
    return True


def _subst_feasible_bruteforce(required, actual) -> bool:
    """`_substitution_is_feasible` ground truth: conservation + Hall saturation."""
    demand = [int(r['crew_count']) for r in required]
    if sum(int(w) for w in actual.values()) != sum(demand):
        return False
    allowed = [_allowed_skills(r) for r in required]
    return _saturates_bruteforce(actual, demand, allowed)


@st.composite
def _requirements(draw):
    """1–4 requirements over a small skill alphabet (crew 0–4, optional alts)."""
    n = draw(st.integers(min_value=1, max_value=4))
    reqs = []
    for _ in range(n):
        primary = draw(st.sampled_from(_SKILLS))
        crew = draw(st.integers(min_value=0, max_value=4))
        alts = draw(st.lists(st.sampled_from(_SKILLS), max_size=3, unique=True))
        reqs.append(_req(primary, crew, [s for s in alts if s != primary]))
    return reqs


@st.composite
def _supply(draw):
    """An arbitrary recorded `{skill: workers}` breakdown over the alphabet."""
    skills = draw(st.lists(st.sampled_from(_SKILLS), max_size=4, unique=True))
    return {s: draw(st.integers(min_value=0, max_value=6)) for s in skills}


@given(reqs=_requirements(), supply=_supply())
@settings()
def test_bipartite_saturates_matches_bruteforce(reqs, supply):
    """`_bipartite_saturates` (max-flow) == Hall-condition brute force."""
    demand = [r['crew_count'] for r in reqs]
    allowed = [_allowed_skills(r) for r in reqs]
    assert (_bipartite_saturates(supply, demand, allowed)
            == _saturates_bruteforce(supply, demand, allowed))


@given(reqs=_requirements(), actual=_supply())
@settings()
def test_substitution_is_feasible_matches_bruteforce(reqs, actual):
    """`_substitution_is_feasible` == conservation + Hall brute force."""
    assert (_substitution_is_feasible(reqs, actual)
            == _subst_feasible_bruteforce(reqs, actual))


@st.composite
def _legal_routing(draw):
    """Requirements plus an `actual` built from an actually-legal, fully-staffing
    routing (each requirement's `crew_count` drawn only from its allowed skills)."""
    reqs = draw(_requirements())
    actual: dict = {}
    for r in reqs:
        allowed = sorted(_allowed_skills(r))       # always non-empty (has primary)
        for _ in range(r['crew_count']):
            s = draw(st.sampled_from(allowed))
            actual[s] = actual.get(s, 0) + 1
    return reqs, actual


@given(inst=_legal_routing())
@settings()
def test_legal_routing_is_feasible_and_conservation_is_load_bearing(inst):
    """A breakdown built from a legal routing certifies feasible; dropping any one
    recorded worker (breaking conservation) must certify infeasible."""
    reqs, actual = inst
    assert _substitution_is_feasible(reqs, actual)
    assert _subst_feasible_bruteforce(reqs, actual)

    if sum(actual.values()) > 0:
        broken = dict(actual)
        s = next(k for k, v in broken.items() if v > 0)
        broken[s] -= 1
        assert not _substitution_is_feasible(reqs, broken)   # non-conserving
