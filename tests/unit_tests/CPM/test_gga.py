"""
Unit tests for gga.py - RCPSPGraphGeneticAlgorithm.

Run from repo root:
    pytest tests/unit_tests/CPM/test_gga.py -v
"""

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from src.CPM.gga import PRIORITY_RULES, RCPSPGraphGeneticAlgorithm  # noqa: E402
from src.CPM.pert import Pert  # noqa: E402


CPM_DIR = REPO_ROOT / 'tests' / 'unit_tests' / 'CPM'
JSON_PATH = str(CPM_DIR / 'j301_1.json')
SCHEMA = str(REPO_ROOT / 'src' / 'CPM' / 'outage_schema.json')


@pytest.fixture(scope='module')
def pert():
    p = Pert.from_json_file(JSON_PATH, schema_path=SCHEMA)
    p.generateInfo()
    return p


@pytest.fixture()
def gga(pert):
    return RCPSPGraphGeneticAlgorithm(
        pert,
        ne=5,
        n_gen=3,
        restart_threshold=2,
        seed=0,
        verbose=False,
    )


def test_zero_lag_matches_cpm_early_starts(gga, pert):
    starts = gga._lags_to_start_times([0.0] * len(gga._arcs))
    for act in gga._activities:
        assert math.isclose(starts[act], pert.infoDict[act]['es'], abs_tol=1e-9)


def test_corrected_lags_preserve_feasible_start_times(gga):
    lags = [float(i % 4) * 0.25 for i in range(len(gga._arcs))]
    starts = gga._lags_to_start_times(lags)
    corrected = gga._correct_aon_lag(starts)
    restored = gga._lags_to_start_times(corrected)

    for act in gga._activities:
        assert math.isclose(restored[act], starts[act], abs_tol=1e-9)


def test_priority_seed_population_uses_priority_orders(pert):
    gga = RCPSPGraphGeneticAlgorithm(
        pert,
        ne=5,
        n_gen=0,
        seed=0,
        verbose=False,
    )

    expected = []
    for rule in PRIORITY_RULES[:gga.ne]:
        pert.priorities = None
        out = pert.calculateSerialScheduleWithResources(priority_rule=rule)
        expected.append(out['scheduled_duration'] - 2)

    pool = gga._build_initial_population()
    fitnesses = [ind['fitness'] for ind in pool]

    assert sorted(fitnesses) == sorted(expected)
    assert len(set(fitnesses)) > 1


def test_run_returns_feasible_winner(gga):
    winner, log = gga.run()
    result = gga.get_best_schedule(winner)

    assert len(log) == gga.n_gen + 1
    assert math.isfinite(winner['fitness'])
    assert result['n_completed'] == result['n_activities']
    assert math.isclose(
        result['scheduled_duration'] - 2,
        winner['fitness'],
        abs_tol=1e-9,
    )
