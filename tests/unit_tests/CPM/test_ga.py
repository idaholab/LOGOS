"""
Unit tests for ga.py — RCPSPGeneticAlgorithm

Tests are organized in four tiers:

  1. Pure-function tests (no Pert required)
       _crossover_one_point, _crossover_two_point, _crossover_uniform_order

  2. Structural tests using a small Pert fixture (example_10.json, 12 tasks)
       _crossover_decuple, __init__ (default/custom operators, invalid inputs),
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
# 4. Instance crossover tests — _crossover_decuple
# =============================================================================

class TestCrossoverDecuple:

    def test_outputs_are_valid_permutations_without_evaluation(
        self,
        pert,
        monkeypatch,
    ):
        """DCS must select valid children without decoding candidates."""
        g = RCPSPGeneticAlgorithm(
            pert,
            pop_size=4,
            n_gen=1,
            crossover='decuple',
            mutation='adjacent_swap',
            fb_improvement=False,
            verbose=False,
        )
        parent1 = g._Ind(g._rule_to_chromosome('es'))
        parent2 = g._Ind(g._rule_to_chromosome('lf'))
        expected = set(range(g._n))

        def fail_evaluate(_):
            raise AssertionError("_crossover_decuple must not evaluate candidates")

        monkeypatch.setattr(g, "_evaluate", fail_evaluate)
        before = g._eval_count
        child1, child2 = g._crossover_decuple(parent1, parent2)

        assert set(child1) == expected
        assert set(child2) == expected
        assert len(child1) == g._n
        assert len(child2) == g._n
        assert not child1.fitness.valid
        assert not child2.fitness.valid
        assert g._eval_count == before

    def test_decuple_candidate_selection_maximizes_diversity(self, ga):
        candidates = [
            [0, 1, 2, 3],
            [0, 1, 3, 2],
            [3, 2, 1, 0],
            [0, 1, 2, 3],
        ]

        selected = ga._select_best_decuple_candidates(candidates)

        assert selected == [
            [0, 1, 2, 3],
            [3, 2, 1, 0],
        ]


# =============================================================================
# 5. Structural tests — constructor and chromosome helpers
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

    def test_default_n_random(self, ga):
        """Default consensus-library random reference count must be 8."""
        assert ga.n_random == 8

    def test_supported_initialization_modes(self, pert):
        assert RCPSPGeneticAlgorithm._INITIAL_POPULATION_MODES == {
            'priority_rules', 'random'
        }
        g = RCPSPGeneticAlgorithm(
            pert,
            initial_population_mode='random',
            verbose=False,
        )
        assert g.initial_population_mode == 'random'
        with pytest.raises(ValueError, match="initial_population_mode"):
            RCPSPGeneticAlgorithm(
                pert,
                initial_population_mode='mixed',
                verbose=False,
            )

    def test_crossover_method_map(self):
        """_CROSSOVER_METHODS must contain all documented choices."""
        assert set(RCPSPGeneticAlgorithm._CROSSOVER_METHODS) == {
            'one_point', 'two_point', 'uniform_order', 'decuple'
        }

    def test_mutation_method_map(self):
        """_MUTATION_METHODS must contain all documented choices."""
        assert set(RCPSPGeneticAlgorithm._MUTATION_METHODS) == {
            'swap', 'adjacent_swap', 'insertion_window', 'consensus_reorder'
        }

    def test_invalid_crossover_raises(self, pert):
        """An unknown crossover name must raise ValueError at construction time."""
        with pytest.raises(ValueError, match="crossover"):
            RCPSPGeneticAlgorithm(pert, crossover='bogus', mutation='swap')

    def test_invalid_mutation_raises(self, pert):
        """An unknown mutation name must raise ValueError at construction time."""
        with pytest.raises(ValueError, match="mutation"):
            RCPSPGeneticAlgorithm(pert, crossover='one_point', mutation='bogus')

    def test_default_fb_improvement(self, ga):
        assert ga.fb_improvement is True

    def test_default_fb_freq(self, ga):
        assert ga.fb_freq == 0

    def test_custom_fb_improvement_false(self, pert):
        g = RCPSPGeneticAlgorithm(
            pert, pop_size=3, n_gen=1, verbose=False, fb_improvement=False
        )
        assert g.fb_improvement is False

    def test_invalid_max_evals_raises(self, pert):
        with pytest.raises(ValueError, match="max_evals"):
            RCPSPGeneticAlgorithm(
                pert,
                pop_size=5,
                max_evals=4,
                verbose=False,
            )

    def test_invalid_stall_generations_raises(self, pert):
        with pytest.raises(ValueError, match="stall_generations"):
            RCPSPGeneticAlgorithm(
                pert,
                stall_generations=0,
                verbose=False,
            )

    def test_custom_fb_freq(self, pert):
        g = RCPSPGeneticAlgorithm(
            pert, pop_size=3, n_gen=1, verbose=False, fb_freq=5
        )
        assert g.fb_freq == 5

    def test_toolbox_mate_registered(self, ga):
        assert hasattr(ga.toolbox, 'mate')

    def test_toolbox_mutate_registered(self, ga):
        assert hasattr(ga.toolbox, 'mutate')

    @pytest.mark.parametrize("cx,mut", [
        ('one_point',    'swap'),
        ('two_point',    'adjacent_swap'),
        ('uniform_order', 'insertion_window'),
        ('decuple',      'adjacent_swap'),
        ('one_point',    'insertion_window'),
        ('uniform_order', 'swap'),
        ('two_point',    'consensus_reorder'),
    ])
    def test_operator_selection(self, pert, cx, mut):
        """All valid crossover/mutation combinations must construct without error."""
        g = RCPSPGeneticAlgorithm(
            pert, pop_size=3, n_gen=1, n_random=3, verbose=False,
            crossover=cx, mutation=mut,
        )
        assert g.crossover == cx
        assert g.mutation == mut
        assert g.n_random == 3


# =============================================================================
# 6. rule_to_chromosome tests
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
# 7. generate_initial_population tests
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

    def test_priority_rule_seed_candidates_not_limited_by_pop_size(self, ga):
        candidates, seed_info = ga._append_priority_rule_seeds()
        assert len(candidates) > ga.pop_size
        assert len(candidates) == sum(len(info) for info in seed_info.values())
        assert {candidate['source'] for candidate in candidates} == {
            'serial', 'parallel'
        }

    def test_priority_rule_mode_keeps_best_20_percent_then_random_fills(
        self,
        pert,
        monkeypatch,
    ):
        g = RCPSPGeneticAlgorithm(
            pert,
            pop_size=10,
            n_gen=1,
            fb_improvement=False,
            verbose=False,
        )
        best = g._rule_to_chromosome('lf')
        second = g._rule_to_chromosome('es')
        third = g._rule_to_chromosome('duration')
        random_fill = g._rule_to_chromosome('random')

        def fake_priority_seeds():
            return (
                [
                    {
                        'rule': 'duration',
                        'source': 'serial',
                        'chromosome': third,
                        'duration': 30.0,
                    },
                    {
                        'rule': 'lf',
                        'source': 'serial',
                        'chromosome': best,
                        'duration': 10.0,
                    },
                    {
                        'rule': 'es',
                        'source': 'parallel',
                        'chromosome': second,
                        'duration': 20.0,
                    },
                ],
                {
                    'duration': {'serial': 30.0},
                    'lf': {'serial': 10.0},
                    'es': {'parallel': 20.0},
                },
            )

        def fake_random_fill(population):
            n_added = 0
            while len(population) < g.pop_size:
                population.append(g._Ind(random_fill))
                n_added += 1
            return n_added

        monkeypatch.setattr(g, '_append_priority_rule_seeds', fake_priority_seeds)
        monkeypatch.setattr(g, '_fill_random_population', fake_random_fill)

        pop = g.generate_initial_population()
        assert len(pop) == 10
        assert list(pop[0]) == best
        assert list(pop[1]) == second
        assert all(list(ind) == random_fill for ind in pop[2:])

    def test_random_mode_skips_priority_rule_seeds(self, pert, monkeypatch):
        g = RCPSPGeneticAlgorithm(
            pert,
            pop_size=5,
            n_gen=1,
            initial_population_mode='random',
            fb_improvement=False,
            verbose=False,
        )
        random_fill = g._rule_to_chromosome('random')

        def fail_priority_seeds():
            raise AssertionError("random mode must not build priority-rule seeds")

        def fake_random_fill(population):
            n_added = 0
            while len(population) < g.pop_size:
                population.append(g._Ind(random_fill))
                n_added += 1
            return n_added

        monkeypatch.setattr(g, '_append_priority_rule_seeds', fail_priority_seeds)
        monkeypatch.setattr(g, '_fill_random_population', fake_random_fill)

        pop = g.generate_initial_population()
        assert len(pop) == g.pop_size
        assert all(list(ind) == random_fill for ind in pop)


# =============================================================================
# 8. Fitness evaluation
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
# 9. Mutation — _mutate_swap
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
# 10. Mutation — _mutate_adjacent_swap
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
# 11. Mutation — _mutate_insertion_window
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
# 12. Forward-Backward-Forward improvement
# =============================================================================

class TestFBImprovement:

    def test_backward_chromosome_is_valid_permutation(self, ga):
        """_compute_backward_chromosome must return a full permutation."""
        chrom = ga._rule_to_chromosome('lf')
        bwd_chrom, makespan_h = ga._compute_backward_chromosome(chrom)
        assert sorted(bwd_chrom) == list(range(ga._n))
        assert len(bwd_chrom) == ga._n

    def test_backward_chromosome_makespan_positive(self, ga):
        chrom = ga._rule_to_chromosome('lf')
        _, makespan_h = ga._compute_backward_chromosome(chrom)
        assert makespan_h > 0

    def test_backward_chromosome_precedence_feasible(self, ga):
        """The backward-ordered chromosome must respect all precedence constraints."""
        chrom = ga._rule_to_chromosome('mts')
        bwd_chrom, _ = ga._compute_backward_chromosome(chrom)
        acts = ga._chromosome_to_activities(bwd_chrom)
        pos = {a: i for i, a in enumerate(acts)}
        for act, preds in ga.pert.backwardDict.items():
            if act not in pos:
                continue
            for pred in preds:
                if pred in pos:
                    assert pos[pred] < pos[act], (
                        f"Precedence violated: {pred} before {act}"
                    )

    def test_fb_improvement_returns_valid_permutation(self, ga):
        """_fb_improvement must return a full permutation and a float fitness."""
        chrom = ga._rule_to_chromosome('lf')
        best_chrom, best_fit = ga._fb_improvement(chrom)
        assert sorted(best_chrom) == list(range(ga._n))
        assert isinstance(best_fit, (int, float))

    @pytest.mark.parametrize("rule", ['es', 'lf', 'mts', 'grpw', 'random'])
    def test_fb_improvement_never_worsens(self, ga, rule):
        """FBF must return fitness ≤ the original forward-pass fitness."""
        chrom = ga._rule_to_chromosome(rule)
        (orig_fit,) = ga._evaluate(chrom)
        _, fb_fit = ga._fb_improvement(chrom)
        assert fb_fit <= orig_fit + 1e-9, (
            f"FBF worsened '{rule}': {orig_fit:.2f} → {fb_fit:.2f}"
        )

    def test_fb_improvement_precedence_feasible(self, ga):
        """The chromosome returned by _fb_improvement must be precedence-feasible."""
        chrom = ga._rule_to_chromosome('mts')
        best_chrom, _ = ga._fb_improvement(chrom)
        acts = ga._chromosome_to_activities(best_chrom)
        pos = {a: i for i, a in enumerate(acts)}
        for act, preds in ga.pert.backwardDict.items():
            if act not in pos:
                continue
            for pred in preds:
                if pred in pos:
                    assert pos[pred] < pos[act]

    def test_apply_fb_improvement_returns_int(self, ga):
        """apply_fb_improvement must return an int count of improved individuals."""
        pop = ga.generate_initial_population()
        for ind in pop:
            ind.fitness.values = ga._evaluate(ind)
        result = ga.apply_fb_improvement(pop)
        assert isinstance(result, int)
        assert 0 <= result <= len(pop)

    def test_apply_fb_improvement_never_worsens(self, ga):
        """No individual's fitness may increase after apply_fb_improvement."""
        pop = ga.generate_initial_population()
        for ind in pop:
            ind.fitness.values = ga._evaluate(ind)
        before = [ind.fitness.values[0] for ind in pop]
        ga.apply_fb_improvement(pop)
        for i, (ind, orig) in enumerate(zip(pop, before)):
            assert ind.fitness.values[0] <= orig + 1e-9, (
                f"Individual {i} worsened: {orig:.2f} → {ind.fitness.values[0]:.2f}"
            )

    def test_apply_fb_improvement_updates_fitness_when_improved(self, ga):
        """Individuals that improve must have updated fitness values."""
        pop = ga.generate_initial_population()
        for ind in pop:
            ind.fitness.values = ga._evaluate(ind)
        before = [ind.fitness.values[0] for ind in pop]
        n_improved = ga.apply_fb_improvement(pop)
        actual_improved = sum(
            1 for ind, orig in zip(pop, before)
            if ind.fitness.values[0] < orig - 1e-9
        )
        assert n_improved == actual_improved


# =============================================================================
# 13. End-to-end run
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

    def test_log_has_stopping_fields(self, run_results):
        _, _, log = run_results
        expected = {'evals', 'best', 'stall', 'unique_schedules', 'stop_reason'}
        assert expected.issubset(log[-1].keys())

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

    def test_plot_convergence_returns_figure(self, run_results, tmp_path):
        """GA convergence log should render and optionally save to disk."""
        import matplotlib
        matplotlib.use('Agg', force=True)
        import matplotlib.pyplot as plt

        g, _, log = run_results
        output = tmp_path / 'ga_convergence.png'
        fig, ax = g.plot_convergence(log, filename=str(output), show=False)

        assert output.exists()
        assert ax.get_xlabel() == 'Generation'
        assert ax.get_ylabel() == 'Schedule duration (h)'
        assert len(ax.lines) >= 2
        plt.close(fig)

    def test_get_convergence_summary_keys(self, run_results):
        g, _, log = run_results
        summary = g.get_convergence_summary(log)
        expected = {'n_gen', 'best_duration', 'initial_best',
                    'improvement', 'final_avg', 'final_std',
                    'n_evals', 'n_unique_schedules', 'stop_reason'}
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

    def test_target_fitness_stops_after_initial_population(self, pert):
        g = RCPSPGeneticAlgorithm(
            pert,
            pop_size=5,
            n_gen=20,
            target_fitness=10_000.0,
            fb_improvement=False,
            verbose=False,
            seed=3,
        )
        _, log = g.run()
        assert len(log) == 1
        assert g.stop_reason == 'target_fitness'
        assert log[-1]['stop_reason'] == 'target_fitness'

    def test_max_evals_stops_before_extra_generation(self, pert):
        g = RCPSPGeneticAlgorithm(
            pert,
            pop_size=5,
            n_gen=20,
            max_evals=5,
            fb_improvement=False,
            verbose=False,
            seed=4,
        )
        _, log = g.run()
        assert len(log) == 1
        assert log[-1]['evals'] == 5
        assert g.stop_reason == 'max_evals'

    def test_stall_generations_stops_early(self, pert):
        g = RCPSPGeneticAlgorithm(
            pert,
            pop_size=5,
            n_gen=20,
            cxpb=0.0,
            mutpb=0.0,
            stall_generations=1,
            fb_improvement=False,
            verbose=False,
            seed=5,
        )
        _, log = g.run()
        assert len(log) <= 2
        assert g.stop_reason == 'stall_generations'

    def test_unique_schedule_budget_is_tracked(self, pert):
        g = RCPSPGeneticAlgorithm(
            pert,
            pop_size=5,
            n_gen=20,
            max_unique_schedules=1,
            fb_improvement=False,
            verbose=False,
            seed=6,
        )
        _, log = g.run()
        assert len(log) == 1
        assert log[-1]['unique_schedules'] >= 1
        assert g.stop_reason == 'max_unique_schedules'

    @pytest.mark.parametrize("cx,mut", [
        ('one_point',    'swap'),
        ('two_point',    'adjacent_swap'),
        ('uniform_order', 'insertion_window'),
        ('decuple',      'adjacent_swap'),
        ('two_point',    'consensus_reorder'),
    ])
    def test_all_operator_combinations_complete(self, pert, cx, mut):
        """Every valid crossover/mutation pair must complete a short run."""
        g = RCPSPGeneticAlgorithm(
            pert, pop_size=5, n_gen=2, verbose=False,
            crossover=cx, mutation=mut, seed=0, n_random=3,
        )
        hof, log = g.run()
        assert len(hof) > 0
        assert len(log) == g.n_gen + 1

    def test_run_fb_improvement_disabled(self, pert):
        """GA must complete when FBF improvement is disabled."""
        g = RCPSPGeneticAlgorithm(
            pert, pop_size=5, n_gen=2, verbose=False,
            fb_improvement=False, seed=1,
        )
        hof, log = g.run()
        assert len(hof) > 0
        assert len(log) == g.n_gen + 1

    def test_run_fb_freq_periodic(self, pert):
        """GA must complete with periodic FBF enabled and log length unchanged."""
        g = RCPSPGeneticAlgorithm(
            pert, pop_size=5, n_gen=4, verbose=False,
            fb_improvement=True, fb_freq=2, seed=2,
        )
        hof, log = g.run()
        assert len(hof) > 0
        assert len(log) == g.n_gen + 1  # FBF does not add log entries

    def test_run_fb_hof_fitness_not_worse_than_pre_fb(self, pert):
        """HoF best fitness with FBF must be ≤ HoF best fitness without FBF."""
        g_no_fb = RCPSPGeneticAlgorithm(
            pert, pop_size=8, n_gen=5, verbose=False,
            fb_improvement=False, seed=3,
        )
        hof_no_fb, _ = g_no_fb.run()
        best_no_fb = hof_no_fb[0].fitness.values[0]

        g_fb = RCPSPGeneticAlgorithm(
            pert, pop_size=8, n_gen=5, verbose=False,
            fb_improvement=True, seed=3,
        )
        hof_fb, _ = g_fb.run()
        best_fb = hof_fb[0].fitness.values[0]

        assert best_fb <= best_no_fb + 1e-9, (
            f"FBF worsened HoF best: {best_no_fb:.2f} → {best_fb:.2f}"
        )


# =============================================================================
# Run standalone
# =============================================================================

if __name__ == '__main__':
    import subprocess
    sys.exit(subprocess.call(['pytest', __file__, '-v']))
