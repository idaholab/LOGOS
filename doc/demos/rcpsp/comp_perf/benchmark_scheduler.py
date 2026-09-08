"""
benchmark_scheduler.py — Wall-clock timing for Pert.calculateScheduleWithResources()

Usage
-----
    cd /Users/mandd/projects/LOGOS/src
    python -m CPM.tests.comp_perf.benchmark_scheduler                   # defaults
    python -m CPM.tests.comp_perf.benchmark_scheduler --sizes 100 500 1000
    python -m CPM.tests.comp_perf.benchmark_scheduler --topologies pipeline fan
    python -m CPM.tests.comp_perf.benchmark_scheduler --pools tight
    python -m CPM.tests.comp_perf.benchmark_scheduler --sgs first max_use_res_ranked
    python -m CPM.tests.comp_perf.benchmark_scheduler --reps 5 --out results.json

Topologies
----------
    serial   : START → T001 → T002 → … → T_n → END
               _ready is always 1 — measures pure CPM + event-loop overhead
    fan      : START → [T001 … T_n] → END  (all parallel)
               _ready spikes to n at event 0 — stresses candidate-selection loop
    pipeline : n // CLUSTER_SIZE clusters, each cluster_size work activities
               running in parallel between gate-in and gate-out nodes, clusters
               chained sequentially — closest model to a real outage work-package
               structure

Pool modes
----------
    unconstrained : 9 999 MECH workers — activities never wait for resources;
                    isolates topology / graph overhead
    tight         : pool = ceil(cluster_size × crew_per_act × TIGHT_FRACTION)
                    workers; forces partial serialization within each cluster;
                    exercises the full scheduling loop

SGS variants
------------
    first                 : pick the first feasible candidate (ranked by value)
    max_use_res_ranked    : greedy max-resource-use, deterministic ranking
    max_use_res_shuffled  : greedy max-resource-use, shuffled tiebreaking
    md_knapsack           : multi-dimensional knapsack selection
    look_ahead            : one-step look-ahead selection

Instrumentation
---------------
    _select_candidate_activities is monkey-patched (no changes to pert.py) to
    record len(self._ready) at every call.  Peak and mean are reported so the
    O(n) reduction of the _ready set can be verified empirically.

Output
------
    Markdown table  → stdout
    JSON results    → --out path  (default: tests/benchmark_results.json)
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as _stats
import sys
import time
import tracemalloc
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# sys.path: allow `python -m CPM.tests.comp_perf.benchmark_scheduler` from src/
# ---------------------------------------------------------------------------
_SRC = Path(__file__).resolve().parents[3]          # …/LOGOS/src
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import logging
logging.disable(logging.WARNING)   # suppress INFO chatter from pert.py

from CPM.activity import Activity                    # noqa: E402
from CPM.outage_data import (                        # noqa: E402
    ResourcePool, ResourceAvailability,
    EquipmentPool, LocationPool,
)
from CPM.pert import Pert                            # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
START_DT       = datetime(2026, 1, 1, 0, 0)
ACT_DURATION   = 4.0          # hours — nominal duration (overridden by randomisation)
CREW_PER_ACT   = 2            # MECH workers per activity
CLUSTER_SIZE   = 10           # pipeline topology: activities per cluster
TIGHT_FRACTION = 0.5          # tight pool = fraction × peak-demand in cluster
POOL_HORIZON   = timedelta(days=365)

# Plant-realistic duration distribution — shared with benchmark_plant.py
DURATION_CHOICES = [1.0,  2.0,  4.0,  8.0, 16.0, 24.0]
DURATION_WEIGHTS = [ 15,   20,   30,   20,   10,    5]

SIZES_DEFAULT      = [100, 300, 500, 1_000, 1_500]
REPS_DEFAULT       = 3
TOPOLOGIES_DEFAULT = ['serial', 'fan', 'pipeline']
POOLS_DEFAULT      = ['unconstrained', 'tight']
SGS_ALL            = ['first', 'max_use_res_ranked', 'max_use_res_shuffled',
                       'md_knapsack', 'look_ahead']
SGS_DEFAULT        = SGS_ALL
OUT_DEFAULT        = Path(__file__).parent / 'benchmark_results.json'


# ===========================================================================
# §1  Graph generators
# ===========================================================================

def _make_pert(fwd: dict) -> Pert:
    """Wrap a forward-dict into a bare Pert (no pools attached yet)."""
    return Pert(graph=fwd)


def make_serial_chain(n: int) -> Pert:
    """START → T001 → T002 → … → T_{n} → END."""
    acts = [Activity(f'T{i:05d}', ACT_DURATION) for i in range(1, n + 1)]
    start = Activity('START', 0.0)
    end   = Activity('END',   0.0)
    fwd: dict = {start: [acts[0]]}
    for i in range(len(acts) - 1):
        fwd[acts[i]] = [acts[i + 1]]
    fwd[acts[-1]] = [end]
    fwd[end]      = []
    return _make_pert(fwd)


def make_wide_fan(n: int) -> Pert:
    """START → [T001 … T_{n}] → END (all parallel)."""
    acts  = [Activity(f'T{i:05d}', ACT_DURATION) for i in range(1, n + 1)]
    start = Activity('START', 0.0)
    end   = Activity('END',   0.0)
    fwd: dict = {start: list(acts), end: []}
    for act in acts:
        fwd[act] = [end]
    return _make_pert(fwd)


def make_pipeline(n: int, cluster_size: int = CLUSTER_SIZE) -> Pert:
    """
    n_clusters clusters of cluster_size work activities, chained sequentially,
    bracketed by a global START and END node (consistent with serial/fan).

    Each cluster:
        gate_in(0h) → [work_k_1 … work_k_cs](4h) → gate_out(0h)

    Clusters linked:
        START → gate_in[0] → … → gate_out[k] → gate_in[k+1] → … → END

    n is rounded down to the nearest multiple of cluster_size.
    """
    n_clusters = max(1, n // cluster_size)
    start      = Activity('START', 0.0)
    end        = Activity('END',   0.0)
    fwd: dict  = {end: []}

    prev = start
    for k in range(n_clusters):
        gate_in  = Activity(f'GI_{k:04d}', 0.0)
        gate_out = Activity(f'GO_{k:04d}', 0.0)
        works    = [Activity(f'W_{k:04d}_{j:03d}', ACT_DURATION)
                    for j in range(cluster_size)]

        fwd[prev]     = [gate_in]
        fwd[gate_in]  = list(works)
        fwd[gate_out] = []
        for w in works:
            fwd[w] = [gate_out]

        prev = gate_out

    fwd[prev] = [end]
    return _make_pert(fwd)


# ===========================================================================
# §2  Pool builders
# ===========================================================================

def _crew_pool(n_workers: int) -> ResourcePool:
    rp = ResourcePool()
    rp.resources['MECH'] = ResourceAvailability(
        'MECH',
        [{'start_date': START_DT,
          'end_date':   START_DT + POOL_HORIZON,
          'available_count': n_workers}],
    )
    return rp


def make_pools_unconstrained() -> tuple:
    """9 999 workers — activities never block on resources."""
    return _crew_pool(9_999), EquipmentPool(), LocationPool()


def make_pools_tight() -> tuple:
    """
    Pool sized to TIGHT_FRACTION of the per-cluster peak demand.

    This is topology-independent — the pool size is always based on a single
    cluster's peak demand regardless of graph size.  The caller (run_one) passes
    a generous max_time_hours so the safety cutoff is never hit even when the
    pool forces heavy serialisation.

    Peak demand in one cluster = CLUSTER_SIZE × CREW_PER_ACT.
    Tight pool = ceil(peak × TIGHT_FRACTION).

    For fan topology peak is n × CREW_PER_ACT, but we deliberately keep the
    pool small (cluster-sized) to force serialisation and stress the scheduler.
    """
    peak    = CLUSTER_SIZE * CREW_PER_ACT
    workers = max(CREW_PER_ACT, math.ceil(peak * TIGHT_FRACTION))
    return _crew_pool(workers), EquipmentPool(), LocationPool()


# ===========================================================================
# §3  Instrumentation
# ===========================================================================

def _instrument_ready(p: Pert) -> list:
    """
    Monkey-patch p._select_candidate_activities to record len(p._ready)
    at each call.  Returns the shared list that accumulates samples.

    No changes to pert.py — closure captures the original bound method
    and the sample list.
    """
    original = p._select_candidate_activities
    samples: list[int] = []

    def patched(time_arg, value_assignment):
        samples.append(len(p._ready))
        return original(time_arg, value_assignment)

    p._select_candidate_activities = patched
    return samples


# ===========================================================================
# §4  Measurement harness
# ===========================================================================

def _attach_pools(p: Pert, pools: tuple) -> None:
    rp, ep, lp = pools
    p.crew_pool     = rp
    p.equipment_pool    = ep
    p.location_pool     = lp
    p.consumable_pool   = None
    p.system_state_pool = None
    p.startTime         = START_DT


def _add_resource_requirements(p: Pert) -> None:
    """
    Assign CREW_PER_ACT MECH workers to every non-zero-duration activity
    so that the pool constraint is actually exercised.
    """
    for act in p.forwardDict:
        if act.duration > 0.0:
            act.required_resources = [
                {'skill_type': 'MECH',
                 'crew_count': CREW_PER_ACT,
                 'alternative_skill_types': []}
            ]


def _randomize_durations(p: Pert, seed: int = 42) -> None:
    """Replace uniform ACT_DURATION with the plant-realistic distribution.

    Zero-duration activities (START, END, gate nodes) are left unchanged.
    Using a fixed seed per rep ensures each rep measures the same problem;
    the seed argument lets callers vary the distribution across benchmark runs.
    """
    import random as _random
    rng = _random.Random(seed)
    for act in p.forwardDict:
        if act.duration > 0.0:
            act.duration = rng.choices(DURATION_CHOICES, weights=DURATION_WEIGHTS)[0]


def run_one(
    topology_name: str,
    pool_name: str,
    sgs: str,
    n: int,
    topo_fn: Callable,
    pools_fn: Callable,
    reps: int = REPS_DEFAULT,
) -> dict:
    """
    Build a *fresh* Pert object for every repetition so no internal state
    (completed flag, schedule_log, _ready set) carries over between runs.

    CPM timing and the safety-cutoff bound are derived from a single reference
    instance built before the rep loop.  Each rep: build → attach → warmup
    generateInfo → instrument → time calculateScheduleWithResources.

    Reports min, median, and p95 over *reps* measurements.
    """
    # ── Reference instance: CPM time + safety-cutoff parameters ───────────
    p_ref = topo_fn(n)
    _attach_pools(p_ref, pools_fn(n))
    _add_resource_requirements(p_ref)
    _randomize_durations(p_ref, seed=42)
    p_ref.generateInfo()                       # warmup
    t0 = time.perf_counter()
    p_ref.generateInfo()
    t_cpm_ms    = (time.perf_counter() - t0) * 1_000
    cpm_duration = p_ref.getProjectDuration()  # unconstrained makespan (hours)

    total_work = sum(a.duration for a in p_ref.forwardDict if a.duration > 0.0)
    try:
        pool_workers = p_ref.crew_pool.get_availability('MECH', START_DT)
    except Exception:
        pool_workers = CREW_PER_ACT
    pool_workers = max(CREW_PER_ACT, pool_workers)
    max_time_h = max(
        cpm_duration * 4,
        math.ceil(total_work * CREW_PER_ACT / pool_workers) * 2,
    )

    # ── Rep loop: fresh Pert per rep ───────────────────────────────────────
    times:        list[float] = []
    peak_mems:    list[float] = []
    all_samples:  list[int]   = []
    result_dict:  dict        = {}
    p_best:       Pert        = p_ref   # holds the fastest-rep Pert for validation
    t_best:       float       = float('inf')

    tracemalloc.start()
    for _ in range(reps):
        p = topo_fn(n)
        _attach_pools(p, pools_fn(n))
        _add_resource_requirements(p)
        _randomize_durations(p, seed=42)       # same seed → identical problem each rep
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
# §5  Reporter
# ===========================================================================

_COL_WIDTHS = {
    'topology':     10,
    'pool':         15,
    'sgs':          22,
    'n_work':        6,
    'n_total':       7,
    't_cpm_ms':      9,
    't_sched_ms':    8,
    't_median_ms':   9,
    't_p95_ms':      8,
    'peak_mem_mb':  11,
    'sched_ratio':   8,
    'iterations':   10,
    'peak_ready':   11,
    'avg_ready':    10,
    'n_completed':  11,
    'is_valid':      7,
    'n_violations': 10,
}
_HEADERS = {
    'topology':     'Topology',
    'pool':         'Pool',
    'sgs':          'SGS',
    'n_work':       'n (work)',
    'n_total':      'n (total)',
    't_cpm_ms':     'CPM (ms)',
    't_sched_ms':   'Min (ms)',
    't_median_ms':  'Med (ms)',
    't_p95_ms':     'p95 (ms)',
    'peak_mem_mb':  'Peak mem MB',
    'sched_ratio':  'Sched/CPM',
    'iterations':   'Iterations',
    'peak_ready':   'Peak _ready',
    'avg_ready':    'Avg _ready',
    'n_completed':  'Completed',
    'is_valid':     'Valid',
    'n_violations': 'Violations',
}


def _row(rec: dict) -> str:
    cells = []
    for k, w in _COL_WIDTHS.items():
        val = rec.get(k, '')
        cells.append(str(val).ljust(w))
    return '| ' + ' | '.join(cells) + ' |'


def _header_row() -> str:
    cells = [_HEADERS[k].ljust(w) for k, w in _COL_WIDTHS.items()]
    return '| ' + ' | '.join(cells) + ' |'


def _separator_row() -> str:
    cells = ['-' * w for w in _COL_WIDTHS.values()]
    return '|-' + '-|-'.join(cells) + '-|'


def print_markdown_table(results: list[dict]) -> None:
    print()
    print(_header_row())
    print(_separator_row())
    for rec in results:
        print(_row(rec))
    print()


def print_scaling_summary(results: list[dict]) -> None:
    """
    For each (topology, pool, sgs) triple, compute the sched-time ratio between
    consecutive size steps.  A ratio ≈ n_new/n_old indicates O(n);
    ratio ≈ (n_new/n_old)² indicates O(n²).

    Incomplete runs (is_complete=False) are excluded and flagged; they would
    produce artificially low scheduling times that distort the exponent.
    Non-monotone timing steps (t_ratio < 1, e.g. JIT warmup at small n) are
    included but annotated with (*).
    """
    from itertools import groupby

    print('Scaling exponent estimate (Sched time ratio vs size ratio)')
    print('  ratio ≈ 1 × (n_new/n_old)  →  O(n)')
    print('  ratio ≈ 2 × (n_new/n_old)  →  O(n²)')
    print('  (*)  non-monotone step — possible JIT/cache effect; treat with caution')
    print('  [!]  incomplete run excluded from analysis')
    print('  [X]  schedule validation failed (constraint violation detected)')
    print()

    keyfn = lambda r: (r['topology'], r['pool'], r.get('sgs', ''))
    for key, group in groupby(sorted(results, key=keyfn), key=keyfn):
        topo, pool, sgs = key
        all_rows = sorted(list(group), key=lambda r: r['n_work'])
        print(f'  {topo} / {pool} / {sgs}:')

        # Report and exclude incomplete rows.
        incomplete = [r for r in all_rows if not r.get('is_complete', True)]
        rows       = [r for r in all_rows if r.get('is_complete', True)]
        for r in incomplete:
            print(f'    n={r["n_work"]:>5}  [!] incomplete '
                  f'({r.get("n_completed", "?")} / {r.get("n_total", "?")} completed) — excluded')

        # Report invalid schedules (do not exclude from scaling — timing is still valid).
        for r in rows:
            if not r.get('is_valid', True):
                nv = r.get('n_violations', '?')
                print(f'    n={r["n_work"]:>5}  [X] INVALID schedule '
                      f'({nv} violation(s))')

        for i in range(1, len(rows)):
            prev, curr = rows[i - 1], rows[i]
            if prev['t_sched_ms'] <= 0:
                continue
            t_ratio = curr['t_sched_ms'] / prev['t_sched_ms']
            n_ratio = curr['n_work']     / prev['n_work']
            exp     = math.log(t_ratio) / math.log(n_ratio) if n_ratio > 1 else float('nan')
            flag    = ' (*)' if t_ratio < 1.0 else ''
            print(f'    n={prev["n_work"]:>5} → {curr["n_work"]:>5}: '
                  f't_ratio={t_ratio:.2f}  n_ratio={n_ratio:.2f}  '
                  f'empirical exponent≈{exp:.2f}{flag}')
        print()


# ===========================================================================
# §6  main
# ===========================================================================

_TOPO_MAP: dict[str, Callable] = {
    'serial':   make_serial_chain,
    'fan':      make_wide_fan,
    'pipeline': make_pipeline,
}

_POOL_MAP: dict[str, Callable] = {
    'unconstrained': lambda n: make_pools_unconstrained(),
    'tight':         lambda n: make_pools_tight(),
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Benchmark Pert scheduler at varying graph sizes.'
    )
    parser.add_argument(
        '--sizes', nargs='+', type=int, default=SIZES_DEFAULT,
        metavar='N',
        help='Space-separated list of work-activity counts to benchmark.',
    )
    parser.add_argument(
        '--topologies', nargs='+', choices=list(_TOPO_MAP), default=TOPOLOGIES_DEFAULT,
        help='Topologies to benchmark.',
    )
    parser.add_argument(
        '--pools', nargs='+', choices=['unconstrained', 'tight', 'both'],
        default=POOLS_DEFAULT,
        help='"unconstrained", "tight", or "both".',
    )
    parser.add_argument(
        '--sgs', nargs='+', choices=SGS_ALL, default=SGS_DEFAULT,
        metavar='SGS',
        help='SGS variants to benchmark.  Each variant produces its own result row.',
    )
    parser.add_argument(
        '--reps', type=int, default=REPS_DEFAULT,
        help='Repetitions per configuration (minimum time is reported).',
    )
    parser.add_argument(
        '--out', type=Path, default=OUT_DEFAULT,
        help='Path for JSON output.',
    )
    args = parser.parse_args()

    # Normalise pool list
    pool_names: list[str] = []
    for p in args.pools:
        if p == 'both':
            pool_names += ['unconstrained', 'tight']
        else:
            pool_names.append(p)
    pool_names = list(dict.fromkeys(pool_names))   # deduplicate, preserve order

    sgs_names = list(dict.fromkeys(args.sgs))      # deduplicate, preserve order

    results: list[dict] = []
    total = len(args.topologies) * len(pool_names) * len(sgs_names) * len(args.sizes)
    done  = 0

    print(f'Running {total} configurations '
          f'({len(args.topologies)} topologies × '
          f'{len(pool_names)} pool modes × '
          f'{len(sgs_names)} SGS variants × '
          f'{len(args.sizes)} sizes, '
          f'{args.reps} reps each) …')

    for topo_name in args.topologies:
        for pool_name in pool_names:
            for sgs_name in sgs_names:
                for n in sorted(args.sizes):
                    done += 1
                    label = (f'[{done:>3}/{total}] {topo_name:<8} {pool_name:<15} '
                             f'{sgs_name:<22} n={n:>5}')
                    print(f'  {label} … ', end='', flush=True)
                    try:
                        rec = run_one(
                            topology_name=topo_name,
                            pool_name=pool_name,
                            sgs=sgs_name,
                            n=n,
                            topo_fn=_TOPO_MAP[topo_name],
                            pools_fn=_POOL_MAP[pool_name],
                            reps=args.reps,
                        )
                        results.append(rec)
                        valid_tag = 'OK' if rec.get('is_valid', True) else f'INVALID({rec.get("n_violations","?")})'
                        print(f'min={rec["t_sched_ms"]:.1f} med={rec["t_median_ms"]:.1f} '
                              f'p95={rec["t_p95_ms"]:.1f} ms  '
                              f'(iters={rec["iterations"]}, '
                              f'peak_ready={rec["peak_ready"]}, '
                              f'valid={valid_tag})')
                    except Exception as exc:
                        print(f'ERROR: {exc}')
                        results.append({
                            'topology': topo_name, 'pool': pool_name,
                            'sgs': sgs_name, 'n_work': n, 'error': str(exc),
                        })

    print_markdown_table(results)
    print_scaling_summary(results)

    # ── JSON output ─────────────────────────────────────────────────────────
    meta = {
        'run_at':         datetime.now().isoformat(timespec='seconds'),
        'sizes':          args.sizes,
        'topologies':     args.topologies,
        'pools':          pool_names,
        'sgs':            sgs_names,
        'reps':           args.reps,
        'cluster_size':   CLUSTER_SIZE,
        'crew_per_act':   CREW_PER_ACT,
        'tight_fraction': TIGHT_FRACTION,
    }
    output = {'meta': meta, 'results': results}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2))
    print(f'Results written to {args.out}')


if __name__ == '__main__':
    main()
