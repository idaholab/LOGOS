"""
Unit tests for gans.py — RCPSPHybridGANS

Tests are organized in five tiers:

  1. Constructor / structural tests (default attributes, activity-index round-trips)
  2. Helper / pure-logic tests
       _chromosome_to_activities, _rule_to_order, _repair,
       _weighted_residual, _rank_resources / _randomize_weights
  3. Schedule-decode and ordering tests
       _decode_fitness, _get_schedule_times, _order_from_schedule, _fbi
  4. Operator tests (require Pert)
       _dense_activities, _crossover_A, _crossover_B, _mutate,
       _build_initial_population, _select_parents,
       _na_neighbor, _nb_neighbor,
       _update_block_size, _assign_subset_params
  5. End-to-end smoke tests
       run, get_best_schedule, get_best_activity_list, get_convergence_summary

Usage (from repo root):
    pytest tests/unit_tests/CPM/test_gans.py -v
"""

import math
import random
import sys
from pathlib import Path

import pytest

# ── path setup ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from src.CPM.activity import Activity                      # noqa: E402
from src.CPM.gans import RCPSPHybridGANS, PRIORITY_RULES  # noqa: E402
from src.CPM.outage_data import ResourcePool               # noqa: E402
from src.CPM.pert import Pert                              # noqa: E402
from conftest import assert_valid_schedule                 # noqa: E402

# ── fixture paths ─────────────────────────────────────────────────────────────
EXAMPLES_DIR = REPO_ROOT / 'doc' / 'demos' / 'rcpsp' / 'examples'
JSON_PATH = str(EXAMPLES_DIR / 'j301_1.json')
SCHEMA = str(REPO_ROOT / 'src' / 'CPM' / 'outage_schema.json')


@pytest.fixture(scope='module')
def pert():
    """Fully initialised Pert object for j301_1.json (PSPLIB j30 instance 1)."""
    p = Pert.from_json_file(JSON_PATH, schema_path=SCHEMA)
    p.generateInfo()
    return p


@pytest.fixture(scope='module')
def gans(pert):
    """GANS instance with a small budget for fast testing."""
    return RCPSPHybridGANS(
        pert,
        pop_size=5,
        lambda_max=500,
        ga_stall_limit=10,
        ns_steps=5,
        block_size=3,
        seed=0,
        verbose=False,
    )


# =============================================================================
# 1. Constructor / structural tests
# =============================================================================

class TestConstructor:

    def test_activity_count(self, pert, gans):
        assert gans._n == len(list(pert.forwardDict.keys()))

    def test_act_to_idx_inverse(self, gans):
        """_act_to_idx must be the inverse of _activities."""
        for i, act in enumerate(gans._activities):
            assert gans._act_to_idx[act] == i

    def test_activities_match_forward_dict(self, pert, gans):
        assert set(gans._activities) == set(pert.forwardDict.keys())

    @pytest.mark.parametrize(
        ('kwargs', 'message'),
        [
            ({'pop_size': 3}, 'pop_size'),
            ({'pop_size': 5, 'lambda_max': 4}, 'lambda_max'),
            ({'ga_stall_limit': 0}, 'ga_stall_limit'),
            ({'ns_steps': -1}, 'ns_steps'),
            ({'block_size': 0}, 'block_size'),
            ({'resource_threshold': -0.1}, 'resource_threshold'),
            ({'resource_threshold': math.nan}, 'resource_threshold'),
            ({'sigma1': -0.1}, 'sigma'),
            ({'sigma1': 0.7, 'sigma2': 0.6}, 'sigma'),
            ({'parents_size': 1}, 'parents_size'),
            ({'pop_size': 5, 'parents_size': 6}, 'parents_size'),
        ],
    )
    def test_invalid_parameters_raise(self, pert, kwargs, message):
        params = {'pop_size': 5, 'lambda_max': 50, **kwargs}
        with pytest.raises(ValueError, match=message):
            RCPSPHybridGANS(pert, verbose=False, **params)

    def test_default_parents_size_uses_population(self, pert):
        g = RCPSPHybridGANS(pert, pop_size=5, lambda_max=50, verbose=False)
        assert g.parents_size == 5

    def test_default_block_size_stored(self, gans):
        assert gans.block_size == 3
        assert gans._block_size == 3

    def test_topo_idx_covers_all_activities(self, gans):
        """Every activity in _activities must have a topological index."""
        for a in gans._activities:
            assert a in gans._topo_idx

    def test_topo_idx_is_unique(self, gans):
        """Topological indices must be unique."""
        vals = list(gans._topo_idx.values())
        assert len(vals) == len(set(vals))

    def test_dummy_acts_subset_of_activities(self, gans):
        for a in gans._dummy_acts:
            assert a in gans._act_to_idx

    def test_resource_weights_dict_exists(self, gans):
        assert isinstance(gans._resource_weights, dict)

    def test_resource_info_failure_logs_warning_with_context(
        self, pert, monkeypatch, caplog
    ):
        class BrokenCrewPool:
            def get_all_skills(self):
                raise RuntimeError('synthetic resource failure')

        monkeypatch.setattr(pert, 'crew_pool', BrokenCrewPool())
        with caplog.at_level('WARNING', logger='src.CPM.gans'):
            resource_gans = RCPSPHybridGANS(
                pert, pop_size=4, lambda_max=4, seed=0, verbose=False
            )

        assert resource_gans._skill_ids == []
        assert resource_gans._skill_capacity == {}
        assert 'resource-aware ranking and dense-gene scoring' in caplog.text
        assert 'synthetic resource failure' in caplog.text
        assert caplog.records[-1].exc_info is not None

    def test_resource_info_uses_pert_crew_pool(self):
        start = Activity('START', 0.0)
        mechanic = Activity(
            'MECHANIC_TASK',
            4.0,
            required_resources=[{'skill_type': 'MECHANIC', 'crew_count': 2}],
        )
        electrician = Activity(
            'ELECTRICIAN_TASK',
            2.0,
            required_resources=[{'skill_type': 'ELECTRICIAN', 'crew_count': 1}],
        )
        end = Activity('END', 0.0)
        graph = {
            start: [mechanic, electrician],
            mechanic: [end],
            electrician: [end],
            end: [],
        }
        p = Pert(graph=graph)
        p.crew_pool = ResourcePool.from_json([
            {
                'skill_type': 'MECHANIC',
                'availability_periods': [{
                    'start_date': '2026-01-01T00:00:00',
                    'end_date': '2026-01-02T00:00:00',
                    'available_count': 4,
                }],
            },
            {
                'skill_type': 'ELECTRICIAN',
                'availability_periods': [{
                    'start_date': '2026-01-01T00:00:00',
                    'end_date': '2026-01-02T00:00:00',
                    'available_count': 3,
                }],
            },
        ])

        resource_gans = RCPSPHybridGANS(
            p, pop_size=4, lambda_max=4, seed=0, verbose=False
        )

        assert resource_gans._skill_ids == ['MECHANIC', 'ELECTRICIAN']
        assert resource_gans._skill_capacity == {
            'MECHANIC': 4.0,
            'ELECTRICIAN': 3.0,
        }
        assert resource_gans._activity_demand[mechanic] == {'MECHANIC': 2.0}
        assert resource_gans._activity_demand[electrician] == {'ELECTRICIAN': 1.0}
        assert resource_gans._rank_resources() == ['MECHANIC', 'ELECTRICIAN']
        assert set(resource_gans._resource_weights) == {
            'MECHANIC', 'ELECTRICIAN'
        }

    def test_make_individual_defaults(self, gans):
        ind = gans._make_individual([0, 1, 2])
        assert ind['fitness'] == math.inf
        assert ind['order'] == [0, 1, 2]


# =============================================================================
# 2. Helper / pure-logic tests
# =============================================================================

class TestChromosomeHelpers:

    def test_chromosome_to_activities_identity(self, gans):
        identity = list(range(gans._n))
        acts = gans._chromosome_to_activities(identity)
        assert acts == gans._activities

    def test_chromosome_to_activities_length(self, gans):
        order = list(range(gans._n))
        assert len(gans._chromosome_to_activities(order)) == gans._n

    def test_rule_to_order_is_permutation(self, gans):
        order = gans._rule_to_order('lf')
        assert sorted(order) == list(range(gans._n))

    def test_rule_to_order_random_is_permutation(self, gans):
        order = gans._rule_to_order('random')
        assert sorted(order) == list(range(gans._n))

    @pytest.mark.parametrize("rule", ['es', 'ef', 'ls', 'lf', 'duration',
                                       'mts', 'mtp', 'grpw'])
    def test_named_rules_produce_permutations(self, gans, rule):
        order = gans._rule_to_order(rule)
        assert sorted(order) == list(range(gans._n)), \
            f"Rule '{rule}' did not produce a permutation"

    def test_repair_returns_permutation(self, gans):
        """_repair must return a list covering all activity indices."""
        order = list(reversed(range(gans._n)))
        repaired = gans._repair(order)
        assert sorted(repaired) == list(range(gans._n))

    def test_repair_respects_precedence(self, gans):
        """After repair, every predecessor must appear before its successor."""
        order = list(reversed(range(gans._n)))
        repaired = gans._repair(order)
        acts = gans._chromosome_to_activities(repaired)
        pos = {a: i for i, a in enumerate(acts)}
        for act, preds in gans.pert.backwardDict.items():
            if act not in pos:
                continue
            for pred in preds:
                if pred in pos:
                    assert pos[pred] < pos[act], (
                        f"Precedence violated: {pred} (pos {pos[pred]}) "
                        f"must precede {act} (pos {pos[act]})"
                    )

    def test_repair_failure_logs_warning_with_context(
        self, gans, monkeypatch, caplog
    ):
        def fail_repair(_ranked, _graph):
            raise RuntimeError('synthetic repair failure')

        monkeypatch.setattr(gans.pert, 'reorder_by_dependencies', fail_repair)
        order = list(reversed(range(gans._n)))

        with caplog.at_level('WARNING', logger='src.CPM.gans'):
            repaired = gans._repair(order)

        assert repaired == order
        assert f"order with {len(order)} genes" in caplog.text
        assert 'synthetic repair failure' in caplog.text
        assert caplog.records[-1].exc_info is not None


class TestResourceHelpers:

    def test_rank_resources_returns_list(self, gans):
        ranked = gans._rank_resources()
        assert isinstance(ranked, list)

    def test_resource_weights_after_randomize(self, gans):
        """All skill IDs must appear in _resource_weights after randomization."""
        gans._randomize_weights()
        for sk in gans._skill_ids:
            assert sk in gans._resource_weights

    def test_weighted_residual_no_resources(self, gans):
        """With no resources, fallback path returns a value in [0, 1]."""
        original = gans._skill_ids
        gans._skill_ids = []
        v = gans._weighted_residual([])
        gans._skill_ids = original
        assert 0.0 <= v <= 1.0

    def test_weighted_residual_all_idle(self, gans):
        """When no activities are running, residual should be >= 0."""
        v = gans._weighted_residual([])
        assert v >= 0.0

    def test_weighted_residual_range(self, gans):
        """Residual with some active activities must be non-negative."""
        acts = gans._activities[:3]
        v = gans._weighted_residual(acts)
        assert v >= 0.0


# =============================================================================
# 3. Schedule-decode and ordering tests
# =============================================================================

class TestDecodeAndOrdering:

    def test_decode_returns_dict(self, gans):
        order = gans._rule_to_order('lf')
        result = gans._decode(order)
        assert isinstance(result, dict)
        assert 'scheduled_duration' in result

    def test_decode_fitness_positive(self, gans):
        order = gans._rule_to_order('lf')
        f = gans._decode_fitness(order)
        assert f > 0

    def test_get_schedule_times_after_decode(self, gans):
        order = gans._rule_to_order('lf')
        gans._decode(order)
        times = gans._get_schedule_times()
        assert isinstance(times, dict)
        assert len(times) == gans._n
        for a, (s, e) in times.items():
            assert e >= s, f"End time {e} < start time {s} for {a}"

    def test_order_from_schedule_is_permutation(self, gans):
        order = gans._rule_to_order('lf')
        gans._decode(order)
        times = gans._get_schedule_times()
        derived = gans._order_from_schedule(times)
        assert sorted(derived) == list(range(gans._n))

    def test_fbi_returns_valid_fitness_and_order(self, gans):
        order = gans._rule_to_order('lf')
        fitness, best_order = gans._fbi(order)
        assert isinstance(fitness, (int, float))
        assert fitness > 0
        assert sorted(best_order) == list(range(gans._n))
        decoded = gans._decode(best_order)
        assert decoded['scheduled_duration'] - 2 == pytest.approx(fitness)
        assert_valid_schedule(gans.pert, 'GANS FBI result')

    def test_fbi_fitness_le_plain_decode(self, gans):
        """FBI should not worsen fitness compared to a plain forward decode."""
        order = gans._rule_to_order('lf')
        plain_fitness = gans._decode_fitness(order)
        fbi_fitness, _ = gans._fbi(order)
        assert fbi_fitness <= plain_fitness + 1e-6, (
            f"FBI worsened fitness: {fbi_fitness} > {plain_fitness}"
        )

    def test_evaluate_with_fbi_structure(self, gans):
        order = gans._rule_to_order('lf')
        ind = gans._evaluate_with_fbi(order)
        assert 'order' in ind and 'fitness' in ind
        assert sorted(ind['order']) == list(range(gans._n))
        assert ind['fitness'] < math.inf


# =============================================================================
# 4a. Dense genes
# =============================================================================

class TestDenseActivities:

    def test_returns_list(self, gans):
        order = gans._rule_to_order('lf')
        gans._decode(order)
        times = gans._get_schedule_times()
        genes = gans._dense_activities(order, times)
        assert isinstance(genes, list)

    def test_genes_are_frozensets(self, gans):
        order = gans._rule_to_order('lf')
        gans._decode(order)
        times = gans._get_schedule_times()
        genes = gans._dense_activities(order, times)
        for g in genes:
            assert isinstance(g, frozenset)

    def test_genes_activities_in_activity_set(self, gans):
        """Every activity in every dense gene must be a known activity."""
        act_set = set(gans._activities)
        order = gans._rule_to_order('lf')
        gans._decode(order)
        times = gans._get_schedule_times()
        genes = gans._dense_activities(order, times)
        for gene in genes:
            for a in gene:
                assert a in act_set

    def test_genes_non_overlapping(self, gans):
        """Dense genes must be non-overlapping (each activity in at most one gene)."""
        order = gans._rule_to_order('lf')
        gans._decode(order)
        times = gans._get_schedule_times()
        genes = gans._dense_activities(order, times)
        seen: set = set()
        for gene in genes:
            assert gene.isdisjoint(seen), "Dense gene activities overlap"
            seen |= gene

    def test_zero_threshold_returns_no_genes(self, gans):
        """A threshold of 0 means v_t < 0 is never satisfied → no dense genes."""
        original_threshold = gans.resource_threshold
        gans.resource_threshold = 0.0
        order = gans._rule_to_order('lf')
        gans._decode(order)
        times = gans._get_schedule_times()
        genes = gans._dense_activities(order, times)
        gans.resource_threshold = original_threshold
        assert genes == []


# =============================================================================
# 4b. Crossover operators
# =============================================================================

class TestCrossoverA:

    def _make_pair(self, gans):
        o1 = gans._rule_to_order('lf')
        o2 = gans._rule_to_order('es')
        p1 = gans._evaluate_no_fbi(o1)
        p2 = gans._evaluate_no_fbi(o2)
        return p1, p2

    def test_returns_individual(self, gans):
        p1, p2 = self._make_pair(gans)
        child = gans._crossover_A(p1, p2)
        assert 'order' in child and 'fitness' in child

    def test_child_is_valid_permutation(self, gans):
        p1, p2 = self._make_pair(gans)
        child = gans._crossover_A(p1, p2)
        assert sorted(child['order']) == list(range(gans._n))

    def test_child_fitness_is_positive(self, gans):
        p1, p2 = self._make_pair(gans)
        child = gans._crossover_A(p1, p2)
        assert child['fitness'] > 0
        assert child['fitness'] < math.inf

    def test_child_decodes_without_error(self, gans):
        """Crossover A child order must decode to a finite-duration schedule."""
        p1, p2 = self._make_pair(gans)
        child = gans._crossover_A(p1, p2)
        result = gans._decode(child['order'])
        assert result['scheduled_duration'] > 0
        assert_valid_schedule(gans.pert, 'GANS crossover A child')


class TestCrossoverB:

    def _make_pair(self, gans):
        o1 = gans._rule_to_order('lf')
        o2 = gans._rule_to_order('grpw')
        p1 = gans._evaluate_no_fbi(o1)
        p2 = gans._evaluate_no_fbi(o2)
        return p1, p2

    def test_returns_individual(self, gans):
        p1, p2 = self._make_pair(gans)
        child = gans._crossover_B(p1, p2)
        assert 'order' in child and 'fitness' in child

    def test_child_is_valid_permutation(self, gans):
        p1, p2 = self._make_pair(gans)
        child = gans._crossover_B(p1, p2)
        assert sorted(child['order']) == list(range(gans._n))

    def test_child_fitness_is_positive(self, gans):
        p1, p2 = self._make_pair(gans)
        child = gans._crossover_B(p1, p2)
        assert child['fitness'] > 0
        assert child['fitness'] < math.inf

    def test_child_decodes_without_error(self, gans):
        """Crossover B child order must decode to a finite-duration schedule."""
        p1, p2 = self._make_pair(gans)
        child = gans._crossover_B(p1, p2)
        result = gans._decode(child['order'])
        assert result['scheduled_duration'] > 0
        assert_valid_schedule(gans.pert, 'GANS crossover B child')

    def test_fallback_to_crossover_a_no_genes(self, gans):
        """Without dense genes, crossover B uses its decoded-data fallback."""
        original = gans.resource_threshold
        gans.resource_threshold = 0.0
        o1 = gans._rule_to_order('lf')
        o2 = gans._rule_to_order('es')
        p1 = gans._evaluate_no_fbi(o1)
        p2 = gans._evaluate_no_fbi(o2)
        child = gans._crossover_B(p1, p2)
        gans.resource_threshold = original
        assert sorted(child['order']) == list(range(gans._n))


# =============================================================================
# 4c. Mutation
# =============================================================================

class TestMutate:

    def test_returns_individual(self, gans):
        order = gans._rule_to_order('lf')
        ind = gans._evaluate_no_fbi(order)
        mutant = gans._mutate(ind)
        assert 'order' in mutant and 'fitness' in mutant

    def test_mutant_is_valid_permutation(self, gans):
        random.seed(42)
        expected = set(range(gans._n))
        for _ in range(10):
            order = gans._rule_to_order('random')
            ind = gans._evaluate_no_fbi(order)
            mutant = gans._mutate(ind)
            assert set(mutant['order']) == expected

    def test_mutant_fitness_is_finite(self, gans):
        order = gans._rule_to_order('lf')
        ind = gans._evaluate_no_fbi(order)
        mutant = gans._mutate(ind)
        assert mutant['fitness'] < math.inf

    def test_mutant_decodes_without_error(self, gans):
        """Mutant order must decode to a finite-duration schedule."""
        random.seed(7)
        for _ in range(5):
            order = gans._rule_to_order('lf')
            ind = gans._evaluate_no_fbi(order)
            mutant = gans._mutate(ind)
            result = gans._decode(mutant['order'])
            assert result['scheduled_duration'] > 0
            assert_valid_schedule(gans.pert, 'GANS mutation result')


# =============================================================================
# 4d. Initial population and parent selection
# =============================================================================

class TestInitialPopulation:

    def test_population_size(self, gans):
        pop = gans._build_initial_population()
        assert len(pop) == gans.pop_size

    def test_all_individuals_valid_permutations(self, gans):
        pop = gans._build_initial_population()
        expected = set(range(gans._n))
        for i, ind in enumerate(pop):
            assert set(ind['order']) == expected, \
                f"Individual {i} not a valid permutation"
            assert len(ind['order']) == gans._n

    def test_all_individuals_have_finite_fitness(self, gans):
        pop = gans._build_initial_population()
        for ind in pop:
            assert ind['fitness'] < math.inf
            assert ind['fitness'] > 0

    def test_population_elements_are_dicts(self, gans):
        pop = gans._build_initial_population()
        for ind in pop:
            assert isinstance(ind, dict)
            assert 'order' in ind
            assert 'fitness' in ind


class TestSelectParents:

    def test_always_includes_best(self, gans):
        pop = gans._build_initial_population()
        best_fitness = min(ind['fitness'] for ind in pop)
        random.seed(1)
        parents = gans._select_parents(pop)
        assert any(p['fitness'] <= best_fitness + 1e-9 for p in parents)

    def test_parents_size_bounded(self, gans):
        pop = gans._build_initial_population()
        parents = gans._select_parents(pop)
        assert len(parents) <= gans.parents_size
        assert len(parents) >= 1

    def test_parents_are_subset_of_population(self, gans):
        pop = gans._build_initial_population()
        parents = gans._select_parents(pop)
        pop_fitness_set = {ind['fitness'] for ind in pop}
        for p in parents:
            assert p['fitness'] in pop_fitness_set


# =============================================================================
# 4e. Neighbourhood operators
# =============================================================================

class TestNeighborhoodNA:

    def test_returns_tuple(self, gans):
        order = gans._rule_to_order('lf')
        ind = gans._evaluate_no_fbi(order)
        result = gans._na_neighbor(ind)
        assert result is None or isinstance(result, dict)

    def test_neighbor_or_none(self, gans):
        """_na_neighbor returns either a valid Individual or None."""
        order = gans._rule_to_order('lf')
        ind = gans._evaluate_no_fbi(order)
        neighbor = gans._na_neighbor(ind)
        if neighbor is not None:
            assert 'order' in neighbor and 'fitness' in neighbor
            assert sorted(neighbor['order']) == list(range(gans._n))

    def test_neighbor_fitness_finite_when_not_none(self, gans):
        order = gans._rule_to_order('lf')
        ind = gans._evaluate_no_fbi(order)
        neighbor = gans._na_neighbor(ind)
        if neighbor is not None:
            assert neighbor['fitness'] < math.inf

    def test_neighbor_decodes_without_error_when_not_none(self, gans):
        """NA neighbor order must decode to a finite-duration schedule."""
        random.seed(3)
        order = gans._rule_to_order('lf')
        ind = gans._evaluate_no_fbi(order)
        for _ in range(5):
            neighbor = gans._na_neighbor(ind)
            if neighbor is None:
                continue
            result = gans._decode(neighbor['order'])
            assert result['scheduled_duration'] > 0


class TestNeighborhoodNB:

    def test_returns_tuple(self, gans):
        order = gans._rule_to_order('lf')
        ind = gans._evaluate_no_fbi(order)
        result = gans._nb_neighbor(ind)
        assert result is None or isinstance(result, dict)

    def test_neighbor_or_none(self, gans):
        order = gans._rule_to_order('lf')
        ind = gans._evaluate_no_fbi(order)
        neighbor = gans._nb_neighbor(ind)
        if neighbor is not None:
            assert 'order' in neighbor and 'fitness' in neighbor
            assert sorted(neighbor['order']) == list(range(gans._n))

    def test_neighbor_fitness_finite_when_not_none(self, gans):
        order = gans._rule_to_order('lf')
        ind = gans._evaluate_no_fbi(order)
        neighbor = gans._nb_neighbor(ind)
        if neighbor is not None:
            assert neighbor['fitness'] < math.inf


# =============================================================================
# 4f. Adaptive block size and instance classification
# =============================================================================

class TestUpdateBlockSize:

    def test_size_decreases_on_high_empty_rate(self, gans):
        """Block size must shrink if > 50% of recent neighbors were empty."""
        gans._block_size = 5
        gans._empty_block_history = [True] * 20
        gans._update_block_size()
        assert gans._block_size < 5

    def test_size_increases_on_low_empty_rate(self, gans):
        """Block size must grow if < 20% of recent neighbors were empty."""
        gans._block_size = 3
        gans._empty_block_history = [False] * 20
        gans._update_block_size()
        assert gans._block_size >= 3

    def test_no_change_when_history_short(self, gans):
        gans._block_size = 4
        gans._empty_block_history = [True] * 5  # less than window=20
        original = gans._block_size
        gans._update_block_size()
        assert gans._block_size == original


class TestAssignSubsetParams:

    @pytest.fixture
    def adaptive(self, pert):
        return RCPSPHybridGANS(
            pert, pop_size=5, lambda_max=50, seed=0, verbose=False
        )

    def test_subset1_low_sigma(self, adaptive):
        adaptive._assign_subset_params(adaptive._cpm_duration * 1.05)
        assert adaptive.ga_stall_limit == 80
        assert adaptive.ns_steps == 50

    def test_subset3_high_sigma(self, adaptive):
        adaptive._assign_subset_params(adaptive._cpm_duration * 2.0)
        assert adaptive.ga_stall_limit == 20
        assert adaptive.ns_steps == 300

    def test_subset2_medium_sigma(self, adaptive):
        adaptive._assign_subset_params(adaptive._cpm_duration * 1.4)
        assert adaptive.ga_stall_limit == 50
        assert adaptive.ns_steps == 150

    def test_explicit_values_are_preserved(self, gans):
        gans._assign_subset_params(gans._cpm_duration * 2.0)
        assert gans.ga_stall_limit == 10
        assert gans.ns_steps == 5

    def test_partial_override_is_preserved(self, pert):
        instance = RCPSPHybridGANS(
            pert, pop_size=5, lambda_max=50, ga_stall_limit=7,
            ns_steps=None, verbose=False
        )
        instance._assign_subset_params(instance._cpm_duration * 1.05)
        assert instance.ga_stall_limit == 7
        assert instance.ns_steps == 50


# =============================================================================
# 5. End-to-end smoke tests
# =============================================================================

class TestRun:

    @pytest.fixture(scope='class')
    def run_results(self, pert):
        """Run GANS once with a tiny budget; reuse across the class."""
        g = RCPSPHybridGANS(
            pert,
            pop_size=5,
            lambda_max=200,
            ga_stall_limit=8,
            ns_steps=5,
            block_size=3,
            seed=42,
            verbose=False,
        )
        best, log = g.run()
        return g, best, log

    def test_run_returns_best_and_log(self, run_results):
        _, best, log = run_results
        assert isinstance(best, dict)
        assert isinstance(log, list)

    def test_best_has_required_keys(self, run_results):
        _, best, _ = run_results
        assert 'order' in best
        assert 'fitness' in best

    def test_best_order_is_valid_permutation(self, run_results):
        g, best, _ = run_results
        assert sorted(best['order']) == list(range(g._n))

    def test_best_fitness_is_finite_and_positive(self, run_results):
        _, best, _ = run_results
        assert best['fitness'] > 0
        assert best['fitness'] < math.inf

    def test_log_is_non_empty(self, run_results):
        _, _, log = run_results
        assert len(log) > 0

    def test_log_entry_keys(self, run_results):
        _, _, log = run_results
        expected_keys = {
            'n_evals', 'best', 'event', 'ga_stall', 'n_ns_activations',
            'ga_stall_limit', 'ns_steps',
        }
        for entry in log:
            assert expected_keys.issubset(entry.keys())

    def test_log_evals_monotonic(self, run_results):
        _, _, log = run_results
        evals = [e['n_evals'] for e in log]
        for i in range(1, len(evals)):
            assert evals[i] >= evals[i - 1], \
                f"n_evals decreased at entry {i}: {evals[i-1]} → {evals[i]}"

    def test_log_best_monotonic(self, run_results):
        """Best fitness in log must be non-increasing over time."""
        _, _, log = run_results
        bests = [e['best'] for e in log]
        for i in range(1, len(bests)):
            assert bests[i] <= bests[i - 1] + 1e-9, \
                f"Best fitness increased at entry {i}: {bests[i-1]} → {bests[i]}"

    def test_get_best_schedule_returns_dict(self, run_results):
        g, best, _ = run_results
        result = g.get_best_schedule(best)
        assert isinstance(result, dict)
        assert 'scheduled_duration' in result

    def test_get_best_schedule_duration_positive(self, run_results):
        """Re-decoded schedule must have a positive finite duration."""
        g, best, _ = run_results
        result = g.get_best_schedule(best)
        assert result['scheduled_duration'] > 0
        assert result['scheduled_duration'] < math.inf

    def test_get_best_activity_list_length(self, run_results):
        g, best, _ = run_results
        act_list = g.get_best_activity_list(best)
        assert len(act_list) == g._n

    def test_get_best_activity_list_types(self, run_results):
        g, best, _ = run_results
        act_list = g.get_best_activity_list(best)
        assert all(isinstance(name, str) for name in act_list)

    def test_get_convergence_summary_keys(self, run_results):
        g, _, log = run_results
        summary = g.get_convergence_summary(log)
        expected = {
            'n_evals', 'best_duration', 'initial_best',
            'improvement', 'n_ns_activations', 'final_stall',
        }
        assert expected == set(summary.keys())

    def test_convergence_improvement_non_negative(self, run_results):
        g, _, log = run_results
        summary = g.get_convergence_summary(log)
        assert summary['improvement'] >= -1e-9, \
            f"Unexpected regression: {summary['improvement']}"

    def test_convergence_summary_empty_log(self, run_results):
        g, _, _ = run_results
        assert g.get_convergence_summary([]) == {}

    def test_get_best_schedule_schedules_all_activities(self, run_results):
        """Re-decoded schedule must report all activities as completed."""
        g, best, _ = run_results
        result = g.get_best_schedule(best)
        assert result.get('n_completed', 0) == g._n
        assert_valid_schedule(g.pert, 'GANS final result')

    def test_reported_evals_match_actual_sgs_calls(self, pert, monkeypatch):
        calls = 0
        original = pert.calculateSerialScheduleWithResources

        def counted(*args, **kwargs):
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(pert, 'calculateSerialScheduleWithResources', counted)
        instance = RCPSPHybridGANS(
            pert, pop_size=5, lambda_max=6, ga_stall_limit=8,
            ns_steps=5, seed=42, verbose=False
        )
        _, log = instance.run()
        assert calls == log[-1]['n_evals'] == 5
        assert calls <= instance.lambda_max

    @pytest.mark.parametrize('budget', [5, 9, 10])
    def test_budget_never_exceeded(self, pert, monkeypatch, budget):
        calls = 0
        original = pert.calculateSerialScheduleWithResources

        def counted(*args, **kwargs):
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(pert, 'calculateSerialScheduleWithResources', counted)
        instance = RCPSPHybridGANS(
            pert, pop_size=5, lambda_max=budget, ga_stall_limit=8,
            ns_steps=5, seed=42, verbose=False
        )
        _, log = instance.run()
        assert calls == log[-1]['n_evals']
        assert calls <= budget


# =============================================================================
# Run standalone
# =============================================================================

if __name__ == '__main__':
    import subprocess
    sys.exit(subprocess.call(['pytest', __file__, '-v']))
