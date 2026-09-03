"""
benchmark_psplib.py — PSPLIB calibration benchmark for Pert scheduler

Runs the Pert scheduler on standard PSPLIB instances (j30/j60/j90/j120)
that have been pre-converted to the LOGOS outage JSON format via
src/CPM/psplib_converter.ipynb.  Results are compared against the known
best (optimal or best-known) makespans from the PSPLIB database.

This provides an external calibration point: if a heuristic SGS recovers
the optimal makespan on these canonical instances, its performance on larger
synthetic and plant-scale graphs is more credible.

Usage
-----
    cd /Users/mandd/projects/LOGOS/src
    python -m CPM.tests.comp_perf.benchmark_psplib
    python -m CPM.tests.comp_perf.benchmark_psplib --reps 10
    python -m CPM.tests.comp_perf.benchmark_psplib --sgs first max_use_res_ranked

Adding instances
----------------
    1. Convert the .sm file using psplib_converter.ipynb.
    2. Add the instance name (matching the key in benchmarks/best_results.json)
       to INSTANCES_DEFAULT and place the .json file in src/CPM/.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as _stats
import sys
import time
import tracemalloc
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
_SRC = Path(__file__).resolve().parents[3]          # …/LOGOS/src
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import logging
logging.disable(logging.WARNING)

from CPM.pert import Pert
from CPM.tests.comp_perf.benchmark_scheduler import (
    SGS_ALL, SGS_DEFAULT, _instrument_ready,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_CPM_DIR          = Path(__file__).resolve().parents[2]   # src/CPM/
_BENCHMARKS_DIR   = _CPM_DIR / 'benchmarks'
_BEST_RESULTS     = _BENCHMARKS_DIR / 'best_results.json'
OUT_DEFAULT       = Path(__file__).parent / 'benchmark_psplib_results.json'

# ---------------------------------------------------------------------------
# Instance registry
# Pre-converted JSON files live in src/CPM/.
# Keys must match entries in benchmarks/best_results.json.
# ---------------------------------------------------------------------------
_INSTANCE_REGISTRY: dict[str, Path] = {
    'j301_1.sm': _CPM_DIR / 'j301_1.json',
    # Add more as you convert them via psplib_converter.ipynb:
    # 'j601_1.sm': _CPM_DIR / 'j601_1.json',
    # 'j901_1.sm': _CPM_DIR / 'j901_1.json',
    # 'j1201_1.sm': _CPM_DIR / 'j1201_1.json',
}

INSTANCES_DEFAULT = ['j301_1.sm']
REPS_DEFAULT      = 5


# ===========================================================================
# §1  Harness
# ===========================================================================

def run_psplib(
    sm_key: str,
    json_path: Path,
    optimal_makespan: float,
    sgs: str,
    reps: int = REPS_DEFAULT,
) -> dict:
    """
    Time ``calculateScheduleWithResources`` on a single PSPLIB instance for
    one SGS variant.  Each rep builds a fresh Pert from the JSON file so no
    internal scheduler state carries over.

    Returns a result dict with timing, memory, schedule quality, and
    optimality gap vs the known best makespan.
    """
    # ── Reference instance: CPM timing + cutoff bound ─────────────────────
    p_ref = Pert.from_json_file(str(json_path))
    p_ref.generateInfo()                           # warmup
    t0 = time.perf_counter()
    p_ref.generateInfo()
    t_cpm_ms     = (time.perf_counter() - t0) * 1_000
    cpm_duration = p_ref.getProjectDuration()
    max_time_h   = max(cpm_duration * 4, optimal_makespan * 4)

    # ── Rep loop ───────────────────────────────────────────────────────────
    times:       list[float] = []
    peak_mems:   list[float] = []
    all_samples: list[int]   = []
    result_dict: dict        = {}
    p_best:      Pert        = p_ref
    t_best:      float       = float('inf')

    tracemalloc.start()
    for _ in range(reps):
        p = Pert.from_json_file(str(json_path))
        p.generateInfo()                           # warmup per rep

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
            t_best = elapsed
            p_best = p
    tracemalloc.stop()

    # ── Timing statistics ──────────────────────────────────────────────────
    times_s  = sorted(times)
    t_min    = times_s[0]
    t_median = _stats.median(times)
    p95_idx  = min(len(times_s) - 1, math.ceil(0.95 * len(times_s)) - 1)
    t_p95    = times_s[p95_idx]

    peak_ready  = max(all_samples) if all_samples else 0
    avg_ready   = (sum(all_samples) / len(all_samples)) if all_samples else 0.0
    peak_mem_mb = round(max(peak_mems), 2)

    n_graph     = len(p_best.forwardDict)
    n_completed = result_dict.get('n_completed', 0)
    sched_dur   = result_dict.get('scheduled_duration', None)
    sched_ratio = round(sched_dur / cpm_duration, 3) if (sched_dur and cpm_duration > 0) else None

    # Optimality gap: how far above the known best makespan is our solution?
    # gap_pct = 0 means we matched or beat the best known solution.
    if sched_dur is not None and optimal_makespan > 0:
        gap_pct = round((sched_dur - optimal_makespan) / optimal_makespan * 100, 2)
    else:
        gap_pct = None

    # ── Validation (fastest rep) ───────────────────────────────────────────
    try:
        val          = p_best.validate_schedule()
        is_valid     = val.is_feasible
        n_violations = len(val.violations)
    except Exception:
        is_valid     = False
        n_violations = -1

    return {
        'instance':          sm_key,
        'sgs':               sgs,
        'optimal_makespan':  optimal_makespan,
        'cpm_duration':      round(cpm_duration, 1),
        'sched_duration':    round(sched_dur, 1) if sched_dur is not None else None,
        'gap_pct':           gap_pct,
        'sched_ratio':       sched_ratio,
        't_cpm_ms':          round(t_cpm_ms, 2),
        't_sched_ms':        round(t_min, 2),
        't_median_ms':       round(t_median, 2),
        't_p95_ms':          round(t_p95, 2),
        'peak_mem_mb':       peak_mem_mb,
        'peak_ready':        peak_ready,
        'avg_ready':         round(avg_ready, 1),
        'n_completed':       n_completed,
        'is_complete':       n_completed == n_graph,
        'is_valid':          is_valid,
        'n_violations':      n_violations,
    }


# ===========================================================================
# §2  Reporter
# ===========================================================================

_COL_WIDTHS = {
    'instance':         12,
    'sgs':              22,
    'optimal_makespan':  8,
    'cpm_duration':      8,
    'sched_duration':   10,
    'gap_pct':           8,
    'sched_ratio':       9,
    't_sched_ms':        8,
    't_median_ms':       9,
    't_p95_ms':          8,
    'peak_mem_mb':      11,
    'is_valid':          7,
}
_HEADERS = {
    'instance':         'Instance',
    'sgs':              'SGS',
    'optimal_makespan': 'Optimal',
    'cpm_duration':     'CPM dur',
    'sched_duration':   'Sched dur',
    'gap_pct':          'Gap (%)',
    'sched_ratio':      'Sched/CPM',
    't_sched_ms':       'Min (ms)',
    't_median_ms':      'Med (ms)',
    't_p95_ms':         'p95 (ms)',
    'peak_mem_mb':      'Peak mem MB',
    'is_valid':         'Valid',
}


def _row(rec: dict) -> str:
    cells = [str(rec.get(k, '')).ljust(w) for k, w in _COL_WIDTHS.items()]
    return '| ' + ' | '.join(cells) + ' |'


def _header_row() -> str:
    return '| ' + ' | '.join(v.ljust(w) for v, w in
                              zip(_HEADERS.values(), _COL_WIDTHS.values())) + ' |'


def _sep_row() -> str:
    return '|-' + '-|-'.join('-' * w for w in _COL_WIDTHS.values()) + '-|'


def print_table(results: list[dict]) -> None:
    print()
    print(_header_row())
    print(_sep_row())
    for r in results:
        print(_row(r))
    print()


# ===========================================================================
# §3  main
# ===========================================================================

def main() -> None:
    # Load best-known makespans
    if not _BEST_RESULTS.exists():
        print(f'ERROR: best_results.json not found at {_BEST_RESULTS}')
        sys.exit(1)
    with open(_BEST_RESULTS) as f:
        best_known: dict = json.load(f)

    parser = argparse.ArgumentParser(
        description='Benchmark Pert scheduler against PSPLIB instances.'
    )
    parser.add_argument(
        '--instances', nargs='+', default=INSTANCES_DEFAULT,
        choices=list(_INSTANCE_REGISTRY),
        help='PSPLIB instance keys to benchmark (must be pre-converted to JSON).',
    )
    parser.add_argument(
        '--sgs', nargs='+', choices=SGS_ALL, default=SGS_DEFAULT,
        metavar='SGS',
    )
    parser.add_argument(
        '--reps', type=int, default=REPS_DEFAULT,
    )
    parser.add_argument('--out', type=Path, default=OUT_DEFAULT)
    args = parser.parse_args()

    sgs_names = list(dict.fromkeys(args.sgs))

    results: list[dict] = []
    total = len(args.instances) * len(sgs_names)
    done  = 0

    print(f'Running {total} configurations '
          f'({len(args.instances)} instance(s) × {len(sgs_names)} SGS variants, '
          f'{args.reps} reps each) …')

    for sm_key in args.instances:
        json_path = _INSTANCE_REGISTRY.get(sm_key)
        if json_path is None or not json_path.exists():
            print(f'  SKIP {sm_key}: JSON not found at {json_path}')
            continue

        optimal = best_known.get(sm_key)
        if optimal is None:
            print(f'  SKIP {sm_key}: no entry in best_results.json')
            continue

        for sgs_name in sgs_names:
            done += 1
            label = f'[{done:>2}/{total}] {sm_key:<14} {sgs_name:<22}'
            print(f'  {label} … ', end='', flush=True)
            try:
                rec = run_psplib(
                    sm_key=sm_key,
                    json_path=json_path,
                    optimal_makespan=float(optimal),
                    sgs=sgs_name,
                    reps=args.reps,
                )
                results.append(rec)
                valid_tag = 'OK' if rec.get('is_valid', True) else f'INVALID({rec.get("n_violations","?")})'
                gap = rec.get('gap_pct')
                gap_str = f'{gap:+.1f}%' if gap is not None else 'n/a'
                print(f'gap={gap_str}  min={rec["t_sched_ms"]:.1f} '
                      f'med={rec["t_median_ms"]:.1f} p95={rec["t_p95_ms"]:.1f} ms  '
                      f'mem={rec["peak_mem_mb"]:.1f} MB  valid={valid_tag}')
            except Exception as exc:
                print(f'ERROR: {exc}')
                results.append({'instance': sm_key, 'sgs': sgs_name, 'error': str(exc)})

    print_table(results)

    meta = {
        'run_at':    datetime.now().isoformat(timespec='seconds'),
        'instances': args.instances,
        'sgs':       sgs_names,
        'reps':      args.reps,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({'meta': meta, 'results': results}, indent=2))
    print(f'Results written to {args.out}')


if __name__ == '__main__':
    main()
