"""
Unit tests for ga.py — RCPSPGeneticAlgorithm

Tests are organized in four tiers:

  1. Pure-function tests (no Pert required)
       _crossover_one_point, _crossover_two_point, _crossover_uniform_order

  2. Structural tests using a small Pert fixture (example_10.json, 12 tasks)
       __init__ (default/custom operators, invalid inputs),
       _rule_to_chromosome, generate_initial_population, _evaluate

  3. Mutation operator tests (require Pert)
       _mutate_swap, _mutate_adjacent_swap, _mutate_insertion_window

  4. End-to-end smoke tests
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
    """GA instance using default operators (two_point crossover, adjacent_swap mutation)."""
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
        with patch('src.CPM.ga.random.randint', return_value=3):
            c1, c2 = RCPSPGeneticAlgorithm._crossover_one_point(
                list(mother), list(father)
            )
        assert c1 == [1, 3, 2, 4, 6, 5], f"Child 1 wrong: {c1}"

    def test_outputs_are_valid_permutations(self):
        """Both children must be permutations of the original gene set."""
        n = 8
        parent_set = set(range(n))
        ind1 = list(range(n))
        ind2 = list(range(n - 1, -1, -1))
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
        ind1 = [3, 1, 4, 0, 5, 7, 2, 6]
        ind2 = [6, 2, 7, 5, 0, 4, 1, 3]
        q = 3
        with patch('src.CPM.ga.random.randint', return_value=q):
            c1, _ = RCPSPGeneticAlgorithm._crossover_one_point(
                list(ind1), list(ind2)
            )
        assert c1[:q] == ind1[:q]

    def test_trivial_length_one(self):
        """A single-gene chromosome must be returned unchanged."""
        c1, c2 = RCPSPGeneticAlgorithm._crossover_one_point([0], [0])
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
# 2. Pure-function tests — _crossover_two_point
# =============================================================================

class TestCrossoverTwoPoint:

    def test_outputs_are_valid_permutations(self):
        """Both children must be permutations of the original gene set."""
        n = 8
        parent_set = set(range(n))
        ind1 = list(range(n))
        ind2 = list(range(n - 1, -1, -1))
        random.seed(0)
        for _ in range(15):
            a, b = list(ind1), list(ind2)
            RCPSPGeneticAlgorithm._crossover_two_point(a, b)
            assert set(a) == parent_set, f"Child1 not a permutation: {a}"
            assert set(b) == parent_set, f"Child2 not a permutation: {b}"
            assert len(a) == n
            assert len(b) == n

    def test_no_duplicate_genes(self):
        """Neither child should contain duplicate genes."""
        ind1 = [0, 2, 4, 6, 1, 3, 5, 7]
        ind2 = [7, 5, 3, 1, 6, 4, 2, 0]
        with patch('src.CPM.ga.random.sample', return_value=[2, 5]):
            c1, c2 = RCPSPGeneticAlgorithm._crossover_two_point(
                list(ind1), list(ind2)
            )
        assert len(c1) == len(set(c1)), f"Duplicate in child1: {c1}"
        assert len(c2) == len(set(c2)), f"Duplicate in child2: {c2}"

    def test_fixed_ends_from_mother(self):
        """Positions [:q1] and [q2:] of child1 must match the mother's."""
        ind1 = [0, 1, 2, 3, 4, 5, 6, 7]
        ind2 = [7, 6, 5, 4, 3, 2, 1, 0]
        q1, q2 = 2, 5
        with patch('src.CPM.ga.random.sample', return_value=[q1, q2]):
            c1, _ = RCPSPGeneticAlgorithm._crossover_two_point(
                list(ind1), list(ind2)
            )
        assert c1[:q1] == ind1[:q1], f"Prefix mismatch: {c1[:q1]} != {ind1[:q1]}"
        assert c1[q2:] == ind1[q2:], f"Suffix mismatch: {c1[q2:]} != {ind1[q2:]}"

    def test_trivial_short_chromosome(self):
        """Chromosomes shorter than 3 must be returned unchanged."""
        for short in ([0], [0, 1]):
            a, b = list(short), list(short)
            ra, rb = RCPSPGeneticAlgorithm._crossover_two_point(a, b)
            assert ra == short
            assert rb == short

    def test_returns_same_objects(self):
        """DEAP convention: crossover modifies in-place and returns same objects."""
        ind1 = [0, 1, 2, 3, 4]
        ind2 = [4, 3, 2, 1, 0]
        id1, id2 = id(ind1), id(ind2)
        with patch('src.CPM.ga.random.sample', return_value=[1, 3]):
            r1, r2 = RCPSPGeneticAlgorithm._crossover_two_point(ind1, ind2)
        assert id(r1) == id1
        assert id(r2) == id2


# =============================================================================
# 3. Pure-function tests — _crossover_uniform_order
# =============================================================================

class TestCrossoverUniformOrder:

    def test_outputs_are_valid_permutations(self):
        """Both children must be permutations of the original gene set."""
        n = 8
        parent_set = set(range(n))
        ind1 = list(range(n))
        ind2 = list(range(n - 1, -1, -1))
        random.seed(1)
        for _ in range(15):
            a, b = list(ind1), list(ind2)
            RCPSPGeneticAlgorithm._crossover_uniform_order(a, b)
            assert set(a) == parent_set, f"Child1 not a permutation: {a}"
            assert set(b) == parent_set, f"Child2 not a permutation: {b}"
            assert len(a) == n
            assert len(b) == n

    def test_no_duplicate_genes(self):
        """Neither child should contain duplicate genes."""
        ind1 = [0, 1, 2, 3, 4, 5]
        ind2 = [5, 4, 3, 2, 1, 0]
        random.seed(2)
        for _ in range(10):
            c1, c2 = RCPSPGeneticAlgorithm._crossover_uniform_order(
                list(ind1), list(ind2)
            )
            assert len(c1) == len(set(c1)), f"Duplicate in child1: {c1}"
            assert len(c2) == len(set(c2)), f"Duplicate in child2: {c2}"

    def test_masked_positions_from_mother(self):
        """Where mask=1, child1 must carry the mother's gene at that position."""
        ind1 = [0, 1, 2, 3, 4, 5]
        ind2 = [5, 4, 3, 2, 1, 0]
        mask = [1, 0, 1, 0, 1, 0]
        with patch('src.CPM.ga.random.randint', side_effect=mask):
            c1, _ = RCPSPGeneticAlgorithm._crossover_uniform_order(
                list(ind1), list(ind2)
            )
        for i, m in enumerate(mask):
            if m:
                assert c1[i] == ind1[i], (
                    f"Masked pos {i}: expected mother's {ind1[i]}, got {c1[i]}"
                )

    def test_trivial_length_one(self):
        """A single-gene chromosome must be returned unchanged."""
        c1, c2 = RCPSPGeneticAlgorithm._crossover_uniform_order([0], [0])
        assert c1 == [0]
        assert c2 == [0]

    def test_returns_same_objects(self):
        """DEAP convention: crossover modifies in-place and returns same objects."""
        ind1 = [0, 1, 2, 3]
        ind2 = [3, 2, 1, 0]
        id1, id2 = id(ind1), id(ind2)
        with patch('src.CPM.ga.random.randint', side_effect=[1, 0, 0, 1]):
            r1, r2 = RCPSPGeneticAlgorithm._crossover_uniform_order(ind1, ind2)
        assert id(r1) == id1
        assert id(r2) == id2


# =============================================================================
# 4. Structural tests — constructor and chromosome helpers
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

    def test_default_crossover(self, ga):
        """Default crossover operator must be 'two_point'."""
        assert ga.crossover == 'two_point'

    def test_default_mutation(self, ga):
        """Default mutation operator must be 'adjacent_swap'."""
        assert ga.mutation == 'adjacent_swap'

    def test_crossover_method_map(self):
        """_CROSSOVER_METHODS must contain all documented choices."""
        assert set(RCPSPGeneticAlgorithm._CROSSOVER_METHODS) == {
            'one_point', 'two_point', 'uniform_order'
        }

    def test_mutation_method_map(self):
        """_MUTATION_METHODS must contain all documented choices."""
        assert set(RCPSPGeneticAlgorithm._MUTATION_METHODS) == {
            'swap', 'adjacent_swap', 'insertion_window'
        }

    def test_invalid_crossover_raises(self, pert):
        """An unknown crossover name must raise ValueError at construction time."""
        with pytest.raises(ValueError, match="crossover"):
            RCPSPGeneticAlgorithm(pert, crossover='bogus', mutation='swap')

    def test_invalid_mutation_raises(self, pert):
        """An unknown mutation name must raise ValueError at construction time."""
        with pytest.raises(ValueError, match="mutation"):
            RCPSPGeneticAlgorithm(pert, crossover='one_point', mutation='bogus')

    def test_toolbox_mate_registered(self, ga):
        assert hasattr(ga.toolbox, 'mate')

    def test_toolbox_mutate_registered(self, ga):
        assert hasattr(ga.toolbox, 'mutate')

    @pytest.mark.parametrize("cx,mut", [
        ('one_point',    'swap'),
        ('two_point',    'adjacent_swap'),
        ('uniform_order', 'insertion_window'),
        ('one_point',    'insertion_window'),
        ('uniform_order', 'swap'),
    ])
    def test_operator_selection(self, pert, cx, mut):
        """All valid crossover/mutation combinations must construct without error."""
        g = RCPSPGeneticAlgorithm(
            pert, pop_size=3, n_gen=1, verbose=False,
            crossover=cx, mutation=mut,
        )
        assert g.crossover == cx
        assert g.mutation == mut


# =============================================================================
# 5. rule_to_chromosome tests
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
# 6. generate_initial_population tests
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
        pop = ga.generate_initial_population()
        for ind in pop:
            assert isinstance(ind, list)


# =============================================================================
# 7. Fitness evaluation
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
# 8. Mutation — _mutate_swap
# =============================================================================

class TestMutateSwap:

    def test_returns_single_element_tuple(self, ga):
        """DEAP mutation convention: returns (individual,)."""
        chrom = list(ga._rule_to_chromosome('lf'))
        result = ga._mutate_swap(chrom)
        assert isinstance(result, tuple)
        assert len(result) == 1

    def test_modifies_in_place(self, ga):
        """DEAP convention: the returned individual is the same object."""
        chrom = list(ga._rule_to_chromosome('lf'))
        original_id = id(chrom)
        (mutated,) = ga._mutate_swap(chrom)
        assert id(mutated) == original_id

    def test_result_is_valid_permutation(self, ga):
        """After mutation the chromosome must still be a valid permutation."""
        random.seed(5)
        expected = set(range(ga._n))
        for _ in range(10):
            chrom = list(ga._rule_to_chromosome('random'))
            (mutated,) = ga._mutate_swap(chrom)
            assert set(mutated) == expected, f"Not a permutation: {mutated}"
            assert len(mutated) == ga._n

    def test_trivial_short_chromosome(self, ga):
        """Chromosomes shorter than 4 must be returned untouched.

        With range(1, n-1) we need at least 2 elements (n >= 4) to sample
        two distinct positions; shorter chromosomes are returned unchanged.
        """
        for short in ([0], [0, 1], [0, 1, 2]):
            (result,) = ga._mutate_swap(list(short))
            assert result == short, f"Short chromosome was modified: {result}"

    def test_dummy_positions_never_swapped(self, ga):
        """Positions 0 and n-1 (dummy START/END) must never be touched."""
        random.seed(10)
        for _ in range(30):
            chrom = list(ga._rule_to_chromosome('random'))
            first, last = chrom[0], chrom[-1]
            (mutated,) = ga._mutate_swap(chrom)
            assert mutated[0] == first, "Position 0 (dummy START) was swapped"
            assert mutated[-1] == last, "Position n-1 (dummy END) was swapped"

    def test_precedence_feasibility(self, ga):
        """Every predecessor must appear before its successor after mutation."""
        random.seed(99)
        for _ in range(20):
            chrom = list(ga._rule_to_chromosome('lf'))
            (mutated,) = ga._mutate_swap(chrom)
            acts = ga._chromosome_to_activities(mutated)
            pos = {a: i for i, a in enumerate(acts)}
            for act, preds in ga.pert.backwardDict.items():
                if act not in pos:
                    continue
                for pred in preds:
                    if pred in pos:
                        assert pos[pred] < pos[act], (
                            f"Precedence violated: {pred} (pos {pos[pred]}) "
                            f"must precede {act} (pos {pos[act]})"
                        )

    def test_mutpb_attribute(self, ga):
        assert hasattr(ga, 'mutpb')
        assert 0.0 <= ga.mutpb <= 1.0


# =============================================================================
# 9. Mutation — _mutate_adjacent_swap
# =============================================================================

class TestMutateAdjacentSwap:

    def test_returns_single_element_tuple(self, ga):
        """DEAP mutation convention: returns (individual,)."""
        chrom = list(ga._rule_to_chromosome('lf'))
        result = ga._mutate_adjacent_swap(chrom)
        assert isinstance(result, tuple)
        assert len(result) == 1

    def test_modifies_in_place(self, ga):
        """DEAP convention: the returned individual is the same object."""
        chrom = list(ga._rule_to_chromosome('lf'))
        original_id = id(chrom)
        (mutated,) = ga._mutate_adjacent_swap(chrom)
        assert id(mutated) == original_id

    def test_result_is_valid_permutation(self, ga):
        """After mutation the chromosome must still be a valid permutation."""
        random.seed(7)
        expected = set(range(ga._n))
        for _ in range(10):
            chrom = list(ga._rule_to_chromosome('random'))
            (mutated,) = ga._mutate_adjacent_swap(chrom, pmutation=1.0)
            assert set(mutated) == expected, f"Not a permutation: {mutated}"
            assert len(mutated) == ga._n

    def test_trivial_short_chromosome(self, ga):
        """Chromosomes shorter than 4 must be returned untouched."""
        for short in ([0], [0, 1], [0, 1, 2]):
            (result,) = ga._mutate_adjacent_swap(list(short))
            assert result == short, f"Short chromosome was modified: {result}"

    def test_dummy_positions_never_involved(self, ga):
        """Positions 0 and n-1 (dummy START/END) must never change."""
        random.seed(20)
        for _ in range(30):
            chrom = list(ga._rule_to_chromosome('random'))
            first, last = chrom[0], chrom[-1]
            (mutated,) = ga._mutate_adjacent_swap(chrom, pmutation=1.0)
            assert mutated[0] == first, "Position 0 (dummy START) was modified"
            assert mutated[-1] == last, "Position n-1 (dummy END) was modified"

    def test_precedence_feasibility(self, ga):
        """Every predecessor must appear before its successor after mutation."""
        random.seed(99)
        for _ in range(20):
            chrom = list(ga._rule_to_chromosome('lf'))
            (mutated,) = ga._mutate_adjacent_swap(chrom, pmutation=1.0)
            acts = ga._chromosome_to_activities(mutated)
            pos = {a: i for i, a in enumerate(acts)}
            for act, preds in ga.pert.backwardDict.items():
                if act not in pos:
                    continue
                for pred in preds:
                    if pred in pos:
                        assert pos[pred] < pos[act], (
                            f"Precedence violated: {pred} (pos {pos[pred]}) "
                            f"must precede {act} (pos {pos[act]})"
                        )


# =============================================================================
# 10. Mutation — _mutate_insertion_window
# =============================================================================

class TestMutateInsertionWindow:

    def test_returns_single_element_tuple(self, ga):
        """DEAP mutation convention: returns (individual,)."""
        chrom = list(ga._rule_to_chromosome('lf'))
        result = ga._mutate_insertion_window(chrom)
        assert isinstance(result, tuple)
        assert len(result) == 1

    def test_modifies_in_place(self, ga):
        """DEAP convention: the returned individual is the same object."""
        chrom = list(ga._rule_to_chromosome('lf'))
        original_id = id(chrom)
        (mutated,) = ga._mutate_insertion_window(chrom)
        assert id(mutated) == original_id

    def test_result_is_valid_permutation(self, ga):
        """After mutation the chromosome must still be a valid permutation."""
        random.seed(11)
        expected = set(range(ga._n))
        for _ in range(20):
            chrom = list(ga._rule_to_chromosome('random'))
            (mutated,) = ga._mutate_insertion_window(chrom)
            assert set(mutated) == expected, f"Not a permutation: {mutated}"
            assert len(mutated) == ga._n

    def test_trivial_short_chromosome(self, ga):
        """A chromosome of length < 2 must be returned untouched."""
        (result,) = ga._mutate_insertion_window([0])
        assert result == [0]

    def test_dummy_never_moved(self, ga):
        """Dummy START/END activities must never be relocated."""
        dummy_acts = {ga.pert.startActivity, ga.pert.endActivity} - {None}
        dummy_idx = {ga._act_to_idx[a] for a in dummy_acts if a in ga._act_to_idx}
        random.seed(33)
        for _ in range(20):
            chrom = list(ga._rule_to_chromosome('random'))
            before = {idx: chrom.index(idx) for idx in dummy_idx if idx in chrom}
            (mutated,) = ga._mutate_insertion_window(chrom)
            for idx, pos in before.items():
                assert mutated[pos] == idx, (
                    f"Dummy gene {idx} moved from position {pos}"
                )

    def test_precedence_feasibility(self, ga):
        """Every predecessor must appear before its successor after mutation."""
        random.seed(55)
        for _ in range(20):
            chrom = list(ga._rule_to_chromosome('lf'))
            (mutated,) = ga._mutate_insertion_window(chrom)
            acts = ga._chromosome_to_activities(mutated)
            pos = {a: i for i, a in enumerate(acts)}
            for act, preds in ga.pert.backwardDict.items():
                if act not in pos:
                    continue
                for pred in preds:
                    if pred in pos:
                        assert pos[pred] < pos[act], (
                            f"Precedence violated: {pred} (pos {pos[pred]}) "
                            f"must precede {act} (pos {pos[act]})"
                        )


# =============================================================================
# 11. End-to-end run
# =============================================================================

class TestRun:

    @pytest.fixture(scope='class')
    def run_results(self, pert):
        """Run the GA once with default operators and reuse across the class."""
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
        g, _, log = run_results
        summary = g.get_convergence_summary(log)
        assert summary['improvement'] >= -1e-9, \
            f"Unexpected regression: {summary['improvement']}"

    @pytest.mark.parametrize("cx,mut", [
        ('one_point',    'swap'),
        ('two_point',    'adjacent_swap'),
        ('uniform_order', 'insertion_window'),
    ])
    def test_all_operator_combinations_complete(self, pert, cx, mut):
        """Every valid crossover/mutation pair must complete a short run."""
        g = RCPSPGeneticAlgorithm(
            pert, pop_size=5, n_gen=2, verbose=False,
            crossover=cx, mutation=mut, seed=0,
        )
        hof, log = g.run()
        assert len(hof) > 0
        assert len(log) == g.n_gen + 1


# =============================================================================
# Run standalone
# =============================================================================

if __name__ == '__main__':
    import subprocess
    sys.exit(subprocess.call(['pytest', __file__, '-v']))
