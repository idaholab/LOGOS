"""
benchmark_plant.py — Plant-scale wall-clock timing for Pert.calculateScheduleWithResources()

Extends benchmark_scheduler.py to model realistic nuclear-outage conditions:
  - 15 K-scale activity counts matching real plant outages
  - Multiple parallel work-package streams  (plant_outage topology)
  - Multi-skill resource pools              (MECH / ELEC / IC)
  - Location concurrency constraints        (containment, RX-head, aux building)
  - Variable activity durations             (1 h – 24 h, plant-realistic weights)

All timing infrastructure, topology builders for serial/fan/pipeline, pool
builders for unconstrained/tight, and reporter functions are imported directly
from benchmark_scheduler so no code is duplicated.

Usage
-----
    cd /Users/mandd/projects/LOGOS/src
    python -m CPM.tests.comp_perf.benchmark_plant                                  # defaults
    python -m CPM.tests.comp_perf.benchmark_plant --sizes 1000 5000 15000
    python -m CPM.tests.comp_perf.benchmark_plant --topologies plant_outage pipeline
    python -m CPM.tests.comp_perf.benchmark_plant --pools plant tight
    python -m CPM.tests.comp_perf.benchmark_plant --sgs first max_use_res_ranked
    python -m CPM.tests.comp_perf.benchmark_plant --reps 1 --out plant_results.json

Topologies
----------
    plant_outage : N_STREAMS parallel work-package pipelines connected via
                   a global START / END pair.  Each stream is a standard
                   cluster pipeline internally.  CROSS_STREAM_FRAC of streams
                   are rewired to depend on the preceding stream's completion
                   (system-isolation prerequisites).  Durations are randomised
                   from a plant-realistic distribution; activities carry mixed
                   skill and location requirements.
    pipeline     : benchmark_scheduler pipeline (uniform durations, MECH only)
    fan          : benchmark_scheduler wide fan  (uniform durations, MECH only)
    serial       : benchmark_scheduler serial chain

Pool modes
----------
    plant        : fixed staffing (MECH=40 / ELEC=20 / IC=10) + 3 location
                   zones.  Pool size is independent of n so constraint pressure
                   grows with n, matching real outage conditions.
    tight        : benchmark_scheduler tight pool (single MECH skill)
    unconstrained: benchmark_scheduler unconstrained pool (9 999 MECH)
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics as _stats
import sys
import time
import tracemalloc
from datetime import datetime
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# sys.path: allow `python -m CPM.tests.benchmark_plant` from src/
# ---------------------------------------------------------------------------
_SRC = Path(__file__).resolve().parents[3]          # …/LOGOS/src
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import logging
logging.disable(logging.WARNING)

# ---------------------------------------------------------------------------
# Re-use everything available from benchmark_scheduler — no duplication
# ---------------------------------------------------------------------------
from CPM.tests.comp_perf.benchmark_scheduler import (
    # topology generators
    make_serial_chain,
    make_wide_fan,
    make_pipeline,
    # pool builders
    make_pools_unconstrained,
    make_pools_tight,
    # harness helpers
    _instrument_ready,
    _attach_pools,
    _add_resource_requirements,
    _randomize_durations,
    # reporters (use their own _COL_WIDTHS / _HEADERS internally)
    print_markdown_table,
    print_scaling_summary,
    # shared constants
    START_DT,
    ACT_DURATION,
    CLUSTER_SIZE,
    POOL_HORIZON,
    DURATION_CHOICES,
    DURATION_WEIGHTS,
    SGS_ALL,
    SGS_DEFAULT,
)

from CPM.activity import Activity
from CPM.outage_data import (
    ResourcePool, ResourceAvailability,
    EquipmentPool,
    LocationPool, LocationAvailability,
)
from CPM.pert import Pert

# ---------------------------------------------------------------------------
# Plant-scale defaults
# ---------------------------------------------------------------------------
SIZES_DEFAULT      = [1_000, 3_000, 5_000, 10_000, 15_000]
REPS_DEFAULT       = 3
TOPOLOGIES_DEFAULT = ['plant_outage', 'pipeline', 'fan']
POOLS_DEFAULT      = ['plant', 'tight']
OUT_DEFAULT        = Path(__file__).parent / 'benchmark_plant_results.json'

# Plant topology parameters
N_STREAMS          = 16     # parallel work-package pipelines
CROSS_STREAM_FRAC  = 0.15   # fraction of streams gated on prior stream completion

# Fixed plant staffing  (pool size is independent of n)
MECH_COUNT         = 40
ELEC_COUNT         = 20
IC_COUNT           = 10

# Skill-profile weights and resource lists
# (weight, required_resources)
_SKILL_PROFILES: list[tuple[int, list]] = [
    (60, [{'skill_type': 'MECH', 'crew_count': 2, 'alternative_skill_types': []}]),
    (20, [{'skill_type': 'ELEC', 'crew_count': 1, 'alternative_skill_types': []}]),
    (10, [{'skill_type': 'IC',   'crew_count': 1, 'alternative_skill_types': []}]),
    (10, [
        {'skill_type': 'MECH', 'crew_count': 2, 'alternative_skill_types': []},
        {'skill_type': 'ELEC', 'crew_count': 1, 'alternative_skill_types': []},
    ]),
]

# Location definitions: (id, description, max_concurrent_tasks, fraction of work activities)
_LOCATION_DEFS: list[tuple[str, str, int, float]] = [
    ('LOC_CONTAINMENT', 'Reactor containment',  4, 0.25),
    ('LOC_RX_HEAD',     'Reactor head area',    6, 0.20),
    ('LOC_AUX_BLDG',    'Auxiliary building',   8, 0.25),
    # remaining 30 % of work activities have no location constraint
]


# ===========================================================================
# §1  Plant topology generator
# ===========================================================================

def make_plant_outage(
    n: int,
    n_streams: int = N_STREAMS,
    cluster_size: int = CLUSTER_SIZE,
    seed: int = 42,
) -> Pert:
    """
    ``n_streams`` independent work-package pipelines connected to a global
    START / END node pair.

    Each stream structure::

        START → SGI_s → GI_s_0 → [W_s_0_0 … W_s_0_cs] → GO_s_0
                                → GI_s_1 → … → GO_s_k → SGO_s → END

    Cross-stream prerequisites (``CROSS_STREAM_FRAC`` of streams):
        SGI_s is rewired from START to SGO_{s-1}, modelling system-isolation
        sequencing (stream s cannot begin until stream s-1 is fully complete).

    Activity durations are set to the module-level ``ACT_DURATION`` constant;
    call ``_randomize_durations()`` after construction to apply the
    plant-realistic distribution.
    """
    rng = random.Random(seed)

    start = Activity('START', 0.0)
    end   = Activity('END',   0.0)
    fwd: dict = {start: [], end: []}

    # Activities per stream — round up to a whole number of clusters
    raw             = max(cluster_size, n // n_streams)
    acts_per_stream = max(cluster_size, (raw // cluster_size) * cluster_size)
    n_clusters      = acts_per_stream // cluster_size

    stream_gis: list[Activity] = []
    stream_gos: list[Activity] = []

    for s in range(n_streams):
        sgi = Activity(f'SGI_{s:03d}', 0.0)
        sgo = Activity(f'SGO_{s:03d}', 0.0)
        fwd[sgi] = []
        fwd[sgo] = [end]
        fwd[start].append(sgi)          # may be rewired for cross-stream deps
        stream_gis.append(sgi)
        stream_gos.append(sgo)

        # Internal cluster pipeline for this stream
        prev = sgi
        for k in range(n_clusters):
            gi    = Activity(f'GI_{s:03d}_{k:04d}', 0.0)
            go    = Activity(f'GO_{s:03d}_{k:04d}', 0.0)
            works = [
                Activity(f'W_{s:03d}_{k:04d}_{j:03d}', ACT_DURATION)
                for j in range(cluster_size)
            ]
            fwd[prev].append(gi)
            fwd[gi] = list(works)
            for w in works:
                fwd[w] = [go]
            fwd[go] = []
            prev   = go

        fwd[prev].append(sgo)

    # Cross-stream dependencies: rewire selected SGI_s from START → SGO_{s-1}
    n_cross       = max(0, int(n_streams * CROSS_STREAM_FRAC))
    cross_indices = rng.sample(range(1, n_streams), min(n_cross, n_streams - 1))
    for s in cross_indices:
        fwd[start].remove(stream_gis[s])
        fwd[stream_gos[s - 1]].append(stream_gis[s])

    return Pert(graph=fwd)


# ===========================================================================
# §2  Plant-specific setup helpers
# ===========================================================================


def _add_multi_skill_requirements(p: Pert, seed: int = 42) -> None:
    """Assign mixed skill requirements and location IDs to work activities.

    Skill profiles and location fractions are drawn from the module-level
    constants ``_SKILL_PROFILES`` and ``_LOCATION_DEFS``; the fixed seed
    ensures fully reproducible benchmark runs.
    """
    rng = random.Random(seed)

    skill_weights   = [sp[0]  for sp in _SKILL_PROFILES]
    skill_resources = [sp[1]  for sp in _SKILL_PROFILES]

    loc_ids     = [ld[0]  for ld in _LOCATION_DEFS]
    loc_fracs   = [ld[3]  for ld in _LOCATION_DEFS]
    no_loc_frac = max(0.0, 1.0 - sum(loc_fracs))
    loc_choices = loc_ids + [None]
    loc_weights = [f * 100 for f in loc_fracs] + [no_loc_frac * 100]

    for act in p.forwardDict:
        if act.duration > 0.0:
            # Copy the profile so each activity owns its own list; sharing a
            # reference to the module-level _SKILL_PROFILES list would corrupt
            # the global constant if the scheduler mutates required_resources.
            act.required_resources = [
                dict(r) for r in rng.choices(skill_resources, weights=skill_weights)[0]
            ]
            act.location_id = rng.choices(loc_choices, weights=loc_weights)[0]


def make_pools_plant(n_activities: int = 0) -> tuple:
    """Multi-skill resource pool with realistic plant staffing + 3 location zones.

    Pool sizes (MECH=40, ELEC=20, IC=10) are independent of ``n_activities``
    so that constraint pressure scales naturally with n — matching real outage
    conditions where crew count is fixed and the work backlog grows.
    """
    rp = ResourcePool()
    for skill, count in [('MECH', MECH_COUNT), ('ELEC', ELEC_COUNT), ('IC', IC_COUNT)]:
        rp.resources[skill] = ResourceAvailability(
            skill,
            [{'start_date':      START_DT,
              'end_date':        START_DT + POOL_HORIZON,
              'available_count': count}],
        )

    lp = LocationPool()
    for loc_id, desc, max_tasks, _ in _LOCATION_DEFS:
        lp.locations[loc_id] = LocationAvailability(
            loc_id, desc,
            [{'start_date':              START_DT,
              'end_date':                START_DT + POOL_HORIZON,
              'max_concurrent_tasks':    max_tasks,
              'max_concurrent_workers':  max_tasks * 4}],
        )

    return rp, EquipmentPool(), lp


# ===========================================================================
# §3  Plant measurement harness
# ===========================================================================

def run_one_plant(
    topology_name: str,
    pool_name: str,
    sgs: str,
    n: int,
    topo_fn: Callable,
    pools_fn: Callable,
    reps: int = REPS_DEFAULT,
    is_plant_topo: bool = False,
) -> dict:
    """Plant-scale version of ``run_one`` from benchmark_scheduler.

    Builds a *fresh* Pert object for every repetition so no internal state
    carries over between runs.  CPM timing and the safety-cutoff bound are
    derived from a single reference instance built before the rep loop.

    Reports min, median, and p95 over *reps* measurements.
    """
    def _build(seed_offset: int = 0) -> Pert:
        """Return a fully configured, ready-to-schedule Pert instance."""
        p = topo_fn(n)
        _attach_pools(p, pools_fn(n))
        if is_plant_topo:
            _randomize_durations(p, seed=42 + seed_offset)
        if pool_name == 'plant':
            _add_multi_skill_requirements(p, seed=42 + seed_offset)
        else:
            _add_resource_requirements(p)
        return p

    # ── Reference instance: CPM time + safety-cutoff parameters ───────────
    p_ref = _build()
    p_ref.generateInfo()                       # warmup
    t0 = time.perf_counter()
    p_ref.generateInfo()
    t_cpm_ms     = (time.perf_counter() - t0) * 1_000
    cpm_duration = p_ref.getProjectDuration()  # unconstrained makespan (hours)

    total_work = sum(a.duration for a in p_ref.forwardDict if a.duration > 0.0)
    max_time_h = cpm_duration * 4
    for skill in list(p_ref.crew_pool.resources):
        try:
            avail = p_ref.crew_pool.get_availability(skill, START_DT)
        except Exception:
            avail = 1
        avail = max(1, avail)
        max_crew_for_skill = max(
            (r['crew_count']
             for a in p_ref.forwardDict
             for r in (getattr(a, 'required_resources', None) or [])
             if r.get('skill_type') == skill),
            default=1,
        )
        max_time_h = max(max_time_h,
                         math.ceil(total_work * max_crew_for_skill / avail) * 2)

    # ── Rep loop: fresh Pert per rep ───────────────────────────────────────
    times:        list[float] = []
    peak_mems:    list[float] = []
    all_samples:  list[int]   = []
    result_dict:  dict        = {}
    p_best:       Pert        = p_ref
    t_best:       float       = float('inf')

    tracemalloc.start()
    for rep_i in range(reps):
        p = _build(seed_offset=rep_i)
        p.generateInfo()                       # warmup per rep

        rep_samples = _instrument_ready(p)

        tracemalloc.reset_peak()
        t0 = time.perf_counter()
        result_dict = p.calculateScheduleWithResources(
            sgs=sgs,
            max_time_hours=max_time_h,
        )
        elapsed = (time.perf_counter() - t0) * 1_000
        _, rep_peak = tracemalloc.get_traced_memory()

        times.append(elapsed)
        peak_mems.append(rep_peak / 1024 / 1024)
        all_samples.extend(rep_samples)

        if elapsed < t_best:
            t_best  = elapsed
            p_best  = p            # keep fastest rep's object for validation
    tracemalloc.stop()

    # ── Timing statistics ──────────────────────────────────────────────────
    times_s  = sorted(times)
    t_min    = times_s[0]
    t_median = _stats.median(times)
    p95_idx  = min(len(times_s) - 1, math.ceil(0.95 * len(times_s)) - 1)
    t_p95    = times_s[p95_idx]

    peak_ready   = max(all_samples) if all_samples else 0
    avg_ready    = (sum(all_samples) / len(all_samples)) if all_samples else 0.0
    n_graph      = len(p_best.forwardDict)
    n_completed  = result_dict.get('n_completed', 0)
    sched_dur    = result_dict.get('scheduled_duration', None)
    sched_ratio  = round(sched_dur / cpm_duration, 3) if (sched_dur and cpm_duration > 0) else None
    peak_mem_mb  = round(max(peak_mems), 2)

    # ── Schedule validation — fastest rep's Pert object ────────────────────
    try:
        val          = p_best.validate_schedule()
        is_valid     = val.is_feasible
        n_violations = len(val.violations)
    except Exception:
        is_valid     = False
        n_violations = -1   # -1 = validation itself raised an exception

    return {
        'topology':      topology_name,
        'pool':          pool_name,
        'sgs':           sgs,
        'n_work':        n,
        'n_total':       n_graph,
        't_cpm_ms':      round(t_cpm_ms, 2),
        't_sched_ms':    round(t_min, 2),
        't_median_ms':   round(t_median, 2),
        't_p95_ms':      round(t_p95, 2),
        'peak_mem_mb':   peak_mem_mb,
        'cpm_duration':  round(cpm_duration, 1),
        'sched_ratio':   sched_ratio,
        'iterations':    result_dict.get('iterations', 0),
        'n_completed':   n_completed,
        'is_complete':   n_completed == n_graph,
        'is_valid':      is_valid,
        'n_violations':  n_violations,
        'peak_ready':    peak_ready,
        'avg_ready':     round(avg_ready, 1),
    }


# ===========================================================================
# §4  Topology / pool maps
# ===========================================================================

# Tuple value: (generator_fn, is_plant_topo)
# is_plant_topo=True → _randomize_durations() is applied before scheduling
_TOPO_MAP: dict[str, tuple[Callable, bool]] = {
    'plant_outage': (make_plant_outage, True),
    'pipeline':     (make_pipeline,     False),
    'fan':          (make_wide_fan,     False),
    'serial':       (make_serial_chain, False),
}

_POOL_MAP: dict[str, Callable] = {
    'plant':         make_pools_plant,
    'tight':         lambda n: make_pools_tight(),
    'unconstrained': lambda n: make_pools_unconstrained(),
}


# ===========================================================================
# §5  main
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Plant-scale Pert benchmark (1 K – 15 K activities).'
    )
    parser.add_argument(
        '--sizes', nargs='+', type=int, default=SIZES_DEFAULT, metavar='N',
        help='Work-activity counts to benchmark.',
    )
    parser.add_argument(
        '--topologies', nargs='+', choices=list(_TOPO_MAP),
        default=TOPOLOGIES_DEFAULT,
    )
    parser.add_argument(
        '--pools', nargs='+', choices=list(_POOL_MAP), default=POOLS_DEFAULT,
    )
    parser.add_argument(
        '--sgs', nargs='+', choices=SGS_ALL, default=SGS_DEFAULT,
        metavar='SGS',
        help='SGS variants to benchmark.  Each variant produces its own result row.',
    )
    parser.add_argument(
        '--reps', type=int, default=REPS_DEFAULT,
        help='Repetitions per configuration (minimum time reported).',
    )
    parser.add_argument('--out', type=Path, default=OUT_DEFAULT)
    args = parser.parse_args()

    sgs_names = list(dict.fromkeys(args.sgs))   # deduplicate, preserve order

    results: list[dict] = []
    total = len(args.topologies) * len(args.pools) * len(sgs_names) * len(args.sizes)
    done  = 0

    print(f'Running {total} configurations '
          f'({len(args.topologies)} topologies × '
          f'{len(args.pools)} pool modes × '
          f'{len(sgs_names)} SGS variants × '
          f'{len(args.sizes)} sizes, '
          f'{args.reps} rep(s) each) …')

    for topo_name in args.topologies:
        topo_fn, is_plant = _TOPO_MAP[topo_name]
        for pool_name in args.pools:
            pools_fn = _POOL_MAP[pool_name]
            for sgs_name in sgs_names:
                for n in sorted(args.sizes):
                    done += 1
                    label = (f'[{done:>3}/{total}] {topo_name:<14} '
                             f'{pool_name:<14} {sgs_name:<22} n={n:>6}')
                    print(f'  {label} … ', end='', flush=True)
                    try:
                        rec = run_one_plant(
                            topology_name=topo_name,
                            pool_name=pool_name,
                            sgs=sgs_name,
                            n=n,
                            topo_fn=topo_fn,
                            pools_fn=pools_fn,
                            reps=args.reps,
                            is_plant_topo=is_plant,
                        )
                        results.append(rec)
                        valid_tag = 'OK' if rec.get('is_valid', True) else f'INVALID({rec.get("n_violations","?")})'
                        print(
                            f'min={rec["t_sched_ms"]:.1f} med={rec["t_median_ms"]:.1f} '
                            f'p95={rec["t_p95_ms"]:.1f} ms  '
                            f'(iters={rec["iterations"]}, '
                            f'peak_ready={rec["peak_ready"]}, '
                            f'completed={rec["n_completed"]}/{rec["n_total"]}, '
                            f'valid={valid_tag})'
                        )
                    except Exception as exc:
                        print(f'ERROR: {exc}')
                        results.append({
                            'topology': topo_name, 'pool': pool_name,
                            'sgs': sgs_name, 'n_work': n, 'error': str(exc),
                        })

    print_markdown_table(results)
    print_scaling_summary(results)

    meta = {
        'run_at':            datetime.now().isoformat(timespec='seconds'),
        'sizes':             args.sizes,
        'topologies':        args.topologies,
        'pools':             args.pools,
        'sgs':               sgs_names,
        'reps':              args.reps,
        'n_streams':         N_STREAMS,
        'cross_stream_frac': CROSS_STREAM_FRAC,
        'cluster_size':      CLUSTER_SIZE,
        'mech_count':        MECH_COUNT,
        'elec_count':        ELEC_COUNT,
        'ic_count':          IC_COUNT,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({'meta': meta, 'results': results}, indent=2))
    print(f'Results written to {args.out}')


if __name__ == '__main__':
    main()
