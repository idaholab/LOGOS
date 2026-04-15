"""
Unit tests for ga.py — RCPSPGeneticAlgorithm

Tests are organized in three tiers:

  1. Pure-function tests (no Pert required)
       _crossover_one_point, _chromosome_to_activities

  2. Structural tests using a small Pert fixture (example_10.json, 12 tasks)
       __init__, _rule_to_chromosome, generate_initial_population, _evaluate

  3. End-to-end smoke tests
       run, get_best_schedule, get_best_activity_list, get_convergence_summary

Usage (from repo root):
    pytest tests/unit_tests/CPM/test_ga.py -v
"""

import random
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ── path setup ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from src.CPM.pert import Pert  # noqa: E402
from src.CPM.ga import RCPSPGeneticAlgorithm, PRIORITY_RULES  # noqa: E402

# ── shared fixtures ───────────────────────────────────────────────────────────
CPM_DIR   = REPO_ROOT / 'src' / 'CPM'
JSON_PATH = str(CPM_DIR / 'example_10.json')
SCHEMA    = str(CPM_DIR / 'outage_schema.json')


@pytest.fixture(scope='module')
def pert():
    """Fully initialised Pert object for example_10.json (12 tasks)."""
    p = Pert.from_json_file(JSON_PATH, schema_path=SCHEMA)
    p.generateInfo()
    return p


@pytest.fixture(scope='module')
def ga(pert):
    """GA instance with a small population for fast tests."""
    return RCPSPGeneticAlgorithm(
        pert,
        pop_size=5,
        n_gen=3,
        cxpb=0.8,
        mutpb=0.1,
        seed=0,
        verbose=False,
    )


# =============================================================================
# 1. Pure-function tests — _crossover_one_point
# =============================================================================

class TestCrossoverOnePoint:

    def test_paper_example(self):
        """Reproduce Hartmann (1998) paper example with q=3."""
        mother = [1, 3, 2, 5, 4, 6]
        father = [2, 4, 6, 1, 3, 5]
        ind1 = list(mother)
        ind2 = list(father)

        with patch('src.CPM.ga.random.randint', return_value=3):
            c1, c2 = RCPSPGeneticAlgorithm._crossover_one_point(ind1, ind2)

        # Child 1: first 3 from mother, rest from father preserving father order
        assert c1 == [1, 3, 2, 4, 6, 5], f"Child 1 wrong: {c1}"

    def test_outputs_are_valid_permutations(self):
        """Both children must be permutations of the original gene set."""
        n = 8
        parent_set = set(range(n))
        ind1 = list(range(n))
        ind2 = list(range(n - 1, -1, -1))

        # run several cut points
        for q in range(1, n):
            a, b = list(ind1), list(ind2)
            with patch('src.CPM.ga.random.randint', return_value=q):
                RCPSPGeneticAlgorithm._crossover_one_point(a, b)
            assert set(a) == parent_set, f"Child1 not a permutation at q={q}: {a}"
            assert set(b) == parent_set, f"Child2 not a permutation at q={q}: {b}"
            assert len(a) == n
            assert len(b) == n

    def test_no_duplicate_genes(self):
        """Neither child should contain duplicate genes."""
        ind1 = [0, 2, 4, 6, 1, 3, 5, 7]
        ind2 = [7, 5, 3, 1, 6, 4, 2, 0]
        with patch('src.CPM.ga.random.randint', return_value=4):
            c1, c2 = RCPSPGeneticAlgorithm._crossover_one_point(
                list(ind1), list(ind2)
            )
        assert len(c1) == len(set(c1)), f"Duplicate in child1: {c1}"
        assert len(c2) == len(set(c2)), f"Duplicate in child2: {c2}"

    def test_prefix_preserved_in_child1(self):
        """First q genes of child1 must equal first q genes of original ind1."""
        ind1 = [3, 1, 4, 1, 5, 9, 2, 6]  # intentional dup to catch index errors
        # use a valid permutation instead
        ind1 = [3, 1, 4, 0, 5, 7, 2, 6]
        ind2 = [6, 2, 7, 5, 0, 4, 1, 3]
        q = 3
        orig_prefix = list(ind1[:q])
        with patch('src.CPM.ga.random.randint', return_value=q):
            c1, _ = RCPSPGeneticAlgorithm._crossover_one_point(
                list(ind1), list(ind2)
            )
        assert c1[:q] == orig_prefix

    def test_trivial_length_one(self):
        """A single-gene chromosome must be returned unchanged."""
        ind1, ind2 = [0], [0]
        c1, c2 = RCPSPGeneticAlgorithm._crossover_one_point(ind1, ind2)
        assert c1 == [0]
        assert c2 == [0]

    def test_returns_same_objects(self):
        """DEAP convention: crossover modifies in-place and returns same objects."""
        ind1 = [0, 1, 2, 3]
        ind2 = [3, 2, 1, 0]
        id1, id2 = id(ind1), id(ind2)
        with patch('src.CPM.ga.random.randint', return_value=2):
            r1, r2 = RCPSPGeneticAlgorithm._crossover_one_point(ind1, ind2)
        assert id(r1) == id1
        assert id(r2) == id2


# =============================================================================
# 2. Structural tests — constructor and chromosome helpers
# =============================================================================

class TestConstructor:

    def test_activity_count(self, pert, ga):
        assert ga._n == len(list(pert.forwardDict.keys()))

    def test_act_to_idx_inverse(self, ga):
        """_act_to_idx must be the inverse of _activities."""
        for i, act in enumerate(ga._activities):
            assert ga._act_to_idx[act] == i

    def test_chromosomes_to_activities_roundtrip(self, ga):
        identity = list(range(ga._n))
        acts = ga._chromosome_to_activities(identity)
        assert acts == ga._activities

    def test_chromosome_length(self, ga):
        acts = ga._chromosome_to_activities(list(range(ga._n)))
        assert len(acts) == ga._n


# =============================================================================
# 3. rule_to_chromosome tests
# =============================================================================

class TestRuleToChromosome:

    def test_returns_full_permutation(self, ga):
        chrom = ga._rule_to_chromosome('lf')
        assert sorted(chrom) == list(range(ga._n)), \
            f"Not a permutation: {chrom}"

    def test_random_rule_returns_permutation(self, ga):
        chrom = ga._rule_to_chromosome('random')
        assert sorted(chrom) == list(range(ga._n))

    @pytest.mark.parametrize("rule", ['es', 'ef', 'ls', 'lf', 'duration',
                                       'mts', 'mtp', 'grpw'])
    def test_named_rules_produce_permutations(self, ga, rule):
        chrom = ga._rule_to_chromosome(rule)
        assert sorted(chrom) == list(range(ga._n)), \
            f"Rule '{rule}' did not produce a permutation"


# =============================================================================
# 4. generate_initial_population tests
# =============================================================================

class TestInitialPopulation:

    def test_population_size(self, ga):
        pop = ga.generate_initial_population()
        assert len(pop) == ga.pop_size

    def test_all_individuals_are_valid_permutations(self, ga):
        pop = ga.generate_initial_population()
        expected = set(range(ga._n))
        for i, ind in enumerate(pop):
            assert set(ind) == expected, \
                f"Individual {i} is not a valid permutation: {ind}"
            assert len(ind) == ga._n

    def test_individual_type(self, ga):
        from deap import creator
        pop = ga.generate_initial_population()
        for ind in pop:
            assert isinstance(ind, list)


# =============================================================================
# 5. Fitness evaluation
# =============================================================================

class TestEvaluate:

    def test_returns_single_float_tuple(self, ga):
        chrom = list(range(ga._n))
        result = ga._evaluate(chrom)
        assert isinstance(result, tuple)
        assert len(result) == 1
        assert isinstance(result[0], (int, float))

    def test_fitness_is_positive(self, ga):
        chrom = ga._rule_to_chromosome('lf')
        (fitness,) = ga._evaluate(chrom)
        assert fitness > 0, f"Expected positive fitness, got {fitness}"

    def test_reverse_order_does_not_crash(self, ga):
        """Reversed index order may be infeasible but must not raise."""
        chrom = list(reversed(range(ga._n)))
        result = ga._evaluate(chrom)
        assert len(result) == 1


# =============================================================================
# 6. End-to-end run
# =============================================================================

class TestRun:

    @pytest.fixture(scope='class')
    def run_results(self, pert):
        """Run the GA once and reuse results across the class."""
        g = RCPSPGeneticAlgorithm(
            pert,
            pop_size=5,
            n_gen=3,
            cxpb=0.8,
            mutpb=0.2,
            seed=7,
            verbose=False,
        )
        hof, log = g.run()
        return g, hof, log

    def test_run_returns_hof_and_log(self, run_results):
        _, hof, log = run_results
        from deap import tools
        assert isinstance(hof, tools.HallOfFame)
        assert isinstance(log, tools.Logbook)

    def test_hof_not_empty(self, run_results):
        _, hof, _ = run_results
        assert len(hof) > 0

    def test_log_has_correct_generations(self, run_results):
        g, _, log = run_results
        # gen 0 + n_gen generations
        assert len(log) == g.n_gen + 1

    def test_best_fitness_is_finite(self, run_results):
        _, hof, _ = run_results
        best = hof[0].fitness.values[0]
        assert best > 0
        assert best < float('inf')

    def test_get_best_schedule_keys(self, run_results):
        g, hof, _ = run_results
        result = g.get_best_schedule(hof)
        expected_keys = {
            'scheduled_duration', 'cpm_duration', 'delay_hours',
            'n_activities', 'n_completed', 'priority_rule',
        }
        assert expected_keys.issubset(result.keys()), \
            f"Missing keys: {expected_keys - result.keys()}"

    def test_get_best_activity_list_length(self, run_results):
        g, hof, _ = run_results
        act_list = g.get_best_activity_list(hof)
        assert len(act_list) == g._n

    def test_get_best_activity_list_types(self, run_results):
        g, hof, _ = run_results
        act_list = g.get_best_activity_list(hof)
        assert all(isinstance(name, str) for name in act_list)

    def test_get_convergence_summary_keys(self, run_results):
        g, _, log = run_results
        summary = g.get_convergence_summary(log)
        expected = {'n_gen', 'best_duration', 'initial_best',
                    'improvement', 'final_avg', 'final_std'}
        assert expected == set(summary.keys())

    def test_convergence_summary_n_gen(self, run_results):
        g, _, log = run_results
        summary = g.get_convergence_summary(log)
        assert summary['n_gen'] == g.n_gen

    def test_improvement_is_non_negative(self, run_results):
        _, _, log = run_results
        from src.CPM.ga import RCPSPGeneticAlgorithm as GA
        g, _, _ = run_results
        summary = g.get_convergence_summary(log)
        # GA can only improve or stay the same; not guaranteed to improve in 3 gens
        assert summary['improvement'] >= -1e-9, \
            f"Unexpected regression: {summary['improvement']}"


# =============================================================================
# Run standalone (mirrors existing test_psplib.py style)
# =============================================================================

if __name__ == '__main__':
    import subprocess, sys
    sys.exit(subprocess.call(['pytest', __file__, '-v']))
