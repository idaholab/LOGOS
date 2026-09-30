"""
Unit tests for gga.py - RCPSPGraphGeneticAlgorithm.

Run from repo root:
    pytest tests/unit_tests/CPM/test_gga.py -v
"""

import math
import random
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from src.CPM.gga import PRIORITY_RULES, RCPSPGraphGeneticAlgorithm  # noqa: E402
from src.CPM.pert import Pert  # noqa: E402


EXAMPLES_DIR = REPO_ROOT / 'doc' / 'demos' / 'rcpsp' / 'examples'
JSON_PATH = str(EXAMPLES_DIR / 'j301_1.json')
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


@pytest.mark.parametrize(
    ('parameter', 'value', 'message'),
    [
        ('ne', 1, 'ne must be at least 2'),
        ('n_gen', -1, 'n_gen must be nonnegative'),
        ('restart_threshold', 0, 'restart_threshold must be greater than 0'),
        ('restart_threshold', -1, 'restart_threshold must be greater than 0'),
        ('rho', -0.01, r'rho must be finite and in \[0, 1\]'),
        ('rho', 1.01, r'rho must be finite and in \[0, 1\]'),
        ('rho', math.inf, r'rho must be finite and in \[0, 1\]'),
        ('rho', -math.inf, r'rho must be finite and in \[0, 1\]'),
        ('rho', math.nan, r'rho must be finite and in \[0, 1\]'),
    ],
)
def test_constructor_rejects_invalid_parameters(pert, parameter, value, message):
    with pytest.raises(ValueError, match=message):
        RCPSPGraphGeneticAlgorithm(
            pert,
            **{parameter: value},
            verbose=False,
        )


@pytest.mark.parametrize(
    ('parameter', 'value'),
    [
        ('ne', 2),
        ('n_gen', 0),
        ('restart_threshold', 1),
        ('rho', 0.0),
        ('rho', 1.0),
    ],
)
def test_constructor_accepts_boundary_parameters(pert, parameter, value):
    gga = RCPSPGraphGeneticAlgorithm(
        pert,
        **{parameter: value},
        verbose=False,
    )

    assert getattr(gga, parameter) == value


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


def test_evaluation_failure_logs_warning_with_context(gga, monkeypatch, caplog):
    def fail_improvement(_lags):
        raise RuntimeError('synthetic decode failure')

    monkeypatch.setattr(gga, '_improve', fail_improvement)
    ind = gga._make_individual([0.0] * len(gga._arcs))

    with caplog.at_level('WARNING', logger='src.CPM.gga'):
        result = gga._evaluate(ind)

    assert result['fitness'] == math.inf
    assert f"lag chromosome with {len(gga._arcs)} arcs" in caplog.text
    assert 'synthetic decode failure' in caplog.text
    assert caplog.records[-1].exc_info is not None


def test_frozen_block_is_deterministic_across_constructions():
    blocks = []
    keep_alive = []
    for allocation_size in (0, 127):
        allocation_noise = [object() for _ in range(allocation_size)]
        p = Pert.from_json_file(JSON_PATH, schema_path=SCHEMA)
        graph_ga = RCPSPGraphGeneticAlgorithm(
            p,
            ne=2,
            n_gen=0,
            rho=0.5,
            seed=17,
            verbose=False,
        )
        winner = graph_ga._make_individual([0.0] * len(graph_ga._arcs))
        random.seed(23)
        frozen = graph_ga._select_frozen_block(winner, block_type='backward')
        blocks.append({activity.returnName() for activity in frozen})
        keep_alive.append((allocation_noise, p, graph_ga))

    assert blocks[0] == blocks[1]


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


def test_same_seed_reproduces_run():
    runs = []
    keep_alive = []
    for allocation_size in (0, 127):
        allocation_noise = [object() for _ in range(allocation_size)]
        p = Pert.from_json_file(JSON_PATH, schema_path=SCHEMA)
        graph_ga = RCPSPGraphGeneticAlgorithm(
            p,
            ne=5,
            n_gen=3,
            restart_threshold=2,
            seed=29,
            verbose=False,
        )
        winner, log = graph_ga.run()
        runs.append((winner['fitness'], winner['lags'], log))
        keep_alive.append((allocation_noise, p, graph_ga))

    assert runs[0] == runs[1]
