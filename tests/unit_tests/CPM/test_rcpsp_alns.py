"""
test_rcpsp_alns.py — Unit tests for rcpsp_alns.py

Tests are organized in six tiers:

  1. RCPSPState tests
       __init__, objective, invalidate_cache, copy, __repr__

  2. Constructor tests
       __init__ validation, operator maps, slack loading, dummy detection

  3. Internal helper tests
       _ordering_from_rule, _non_dummy, _topo_sort, _insert_feasible

  4. Destroy operator tests
       _destroy_most_mobile, _destroy_segment, _destroy_random

  5. Repair operator tests
       _repair_random_insert, _repair_greedy_insert

  6. End-to-end run and result helper tests
       run, get_best_schedule, get_best_activity_list

Usage (from repo root):
    pytest tests/unit_tests/CPM/test_rcpsp_alns.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pytest

# ── optional dependency guard ─────────────────────────────────────────────────
# rcpsp_alns.py requires the optional 'alns' package; skip the module if absent.
pytest.importorskip("alns", reason="rcpsp_alns.py requires the optional 'alns' package")

from CPM.pert import Pert                                          # noqa: E402
from CPM.rcpsp_alns import (                                       # noqa: E402
    RCPSPAdaptiveLNS, RCPSPState, SEED_PRIORITY_RULES,
)

# ── shared fixtures ───────────────────────────────────────────────────────────
# Data paths are centralized in conftest.py (see BRANCH_ASSESSMENT / H2).
from conftest import SCHEMA_PATH, EXAMPLES_DIR  # noqa: E402
JSON_PATH = str(EXAMPLES_DIR / 'example_10.json')
SCHEMA    = SCHEMA_PATH


@pytest.fixture(scope='module')
def pert():
    """Fully initialised Pert object for example_10.json (12 activities)."""
    p = Pert.from_json_file(JSON_PATH, schema_path=SCHEMA)
    p.generateInfo()
    return p


@pytest.fixture(scope='module')
def alns(pert):
    """ALNS instance with minimal settings for fast tests."""
    return RCPSPAdaptiveLNS(
        pert,
        n_iter=10,
        destroy_fraction=0.3,
        seed=0,
        verbose=False,
    )


@pytest.fixture(scope='module')
def init_state(alns):
    """A complete initial state built from the best seed rule."""
    state, _ = alns._create_initial_solution()
    return state


def _is_precedence_feasible(ordering, backward_dict):
    """Return True if every predecessor appears before its successor."""
    pos = {a: i for i, a in enumerate(ordering)}
    for act, preds in backward_dict.items():
        if act not in pos:
            continue
        for pred in preds:
            if pred in pos and pos[pred] >= pos[act]:
                return False
    return True


# =============================================================================
# 1. RCPSPState
# =============================================================================

class TestRCPSPState:

    def test_init_complete_state(self, pert):
        """A state with no unscheduled list defaults to empty."""
        ordering = list(pert.forwardDict.keys())
        state = RCPSPState(pert, ordering)
        assert state.ordering == ordering
        assert state.unscheduled == []
        assert state._obj_cache is None

    def test_init_with_unscheduled(self, pert):
        """Unscheduled activities are stored separately."""
        acts = list(pert.forwardDict.keys())
        scheduled, unscheduled = acts[:-2], acts[-2:]
        state = RCPSPState(pert, scheduled, unscheduled)
        assert state.ordering == scheduled
        assert state.unscheduled == unscheduled

    def test_objective_raises_for_incomplete_state(self, pert):
        """Calling objective() on a destroyed state must raise ValueError."""
        acts = list(pert.forwardDict.keys())
        state = RCPSPState(pert, acts[:-2], acts[-2:])
        with pytest.raises(ValueError, match="incomplete"):
            state.objective()

    def test_objective_returns_positive_float(self, init_state):
        """Objective must return a positive numeric value."""
        dur = init_state.objective()
        assert isinstance(dur, float)
        assert dur > 0

    def test_objective_is_cached(self, init_state):
        """Second call must return the same object without re-evaluating."""
        _ = init_state.objective()           # populate cache
        cached = init_state._obj_cache
        assert cached is not None
        assert init_state.objective() == cached

    def test_invalidate_cache_clears_value(self, pert, alns):
        """After invalidate_cache() the next objective() call must recompute."""
        state, _ = alns._create_initial_solution()
        _ = state.objective()                # populate cache
        assert state._obj_cache is not None
        state.invalidate_cache()
        assert state._obj_cache is None

    def test_copy_is_different_object(self, init_state):
        """copy() must return a new RCPSPState instance."""
        copy = init_state.copy()
        assert copy is not init_state

    def test_copy_ordering_is_independent(self, init_state):
        """Mutating the copy's ordering must not affect the original."""
        copy = init_state.copy()
        original_first = init_state.ordering[0]
        copy.ordering[0] = None          # corrupt the copy
        assert init_state.ordering[0] is original_first

    def test_copy_unscheduled_is_independent(self, pert):
        """Mutating copy's unscheduled must not affect the original."""
        acts = list(pert.forwardDict.keys())
        state = RCPSPState(pert, acts[:-1], acts[-1:])
        copy = state.copy()
        copy.unscheduled.clear()
        assert len(state.unscheduled) == 1

    def test_copy_preserves_cached_objective(self, init_state):
        """copy() should carry over any already-computed objective cache."""
        _ = init_state.objective()
        copy = init_state.copy()
        assert copy._obj_cache == init_state._obj_cache

    def test_repr_shows_unknown_obj_before_evaluation(self, pert):
        """Before objective() is called, repr must show '?'."""
        state = RCPSPState(pert, list(pert.forwardDict.keys()))
        assert "?" in repr(state)

    def test_repr_shows_numeric_obj_after_evaluation(self, init_state):
        """After objective() is called, repr must show a numeric value."""
        _ = init_state.objective()
        r = repr(init_state)
        assert "?" not in r
        assert "obj=" in r


# =============================================================================
# 2. Constructor and initialisation
# =============================================================================

class TestConstructor:

    def test_seed_priority_rules_match_ga(self):
        """SEED_PRIORITY_RULES must cover the full set from ga.py."""
        from CPM.ga import PRIORITY_RULES as GA_RULES
        assert set(SEED_PRIORITY_RULES) == set(GA_RULES)

    def test_slack_loaded_from_infodict(self, pert, alns):
        """_slack values must equal pert.infoDict[act]['slack'] exactly."""
        for act, slack_val in alns._slack.items():
            expected = pert.infoDict[act].get('slack', 0.0)
            assert slack_val == expected, (
                f"{act.returnName()}: stored {slack_val} != infoDict {expected}"
            )

    def test_slack_not_recomputed(self, pert, alns):
        """Slack must be lf-ef as computed by pert.calculateSlack(), not lf-ef recomputed here."""
        for act, slack_val in alns._slack.items():
            info = pert.infoDict[act]
            lf_ef = info.get('lf', 0.0) - info.get('ef', 0.0)
            assert abs(slack_val - lf_ef) < 1e-9

    def test_dummies_identified(self, pert, alns):
        """_dummies must contain startActivity and endActivity (when present)."""
        if pert.startActivity:
            assert pert.startActivity in alns._dummies
        if pert.endActivity:
            assert pert.endActivity in alns._dummies

    def test_destroy_method_map_complete(self):
        """All documented destroy operators must be registered."""
        assert set(RCPSPAdaptiveLNS._DESTROY_METHODS) == {
            'most_mobile', 'segment', 'random'
        }

    def test_repair_method_map_complete(self):
        """All documented repair operators must be registered."""
        assert set(RCPSPAdaptiveLNS._REPAIR_METHODS) == {
            'random_insert', 'greedy_insert'
        }

    def test_invalid_destroy_op_raises(self, pert):
        """Unknown destroy operator name must raise ValueError at construction."""
        with pytest.raises(ValueError, match="destroy"):
            RCPSPAdaptiveLNS(pert, destroy_ops=['nonexistent'], verbose=False)

    def test_invalid_repair_op_raises(self, pert):
        """Unknown repair operator name must raise ValueError at construction."""
        with pytest.raises(ValueError, match="repair"):
            RCPSPAdaptiveLNS(pert, repair_ops=['nonexistent'], verbose=False)

    def test_invalid_accept_raises(self, pert):
        """Unknown acceptance criterion name must raise ValueError."""
        with pytest.raises(ValueError, match="accept"):
            RCPSPAdaptiveLNS(pert, accept='bogus', verbose=False)

    def test_default_destroy_ops_all_three(self, alns):
        """Default destroy_ops must include all three operators."""
        assert set(alns.destroy_ops) == {'most_mobile', 'segment', 'random'}

    def test_default_repair_ops_both(self, alns):
        """Default repair_ops must include both repair operators."""
        assert set(alns.repair_ops) == {'random_insert', 'greedy_insert'}

    @pytest.mark.parametrize("accept", [
        'hill_climbing', 'record_to_record', 'simulated_annealing'
    ])
    def test_all_accept_criteria_construct(self, pert, accept):
        """All documented acceptance criteria must construct without error."""
        a = RCPSPAdaptiveLNS(pert, n_iter=5, accept=accept, verbose=False)
        assert a.accept_name == accept

    def test_subset_destroy_ops(self, pert):
        """Constructing with a subset of destroy operators must work."""
        a = RCPSPAdaptiveLNS(
            pert, destroy_ops=['random'], repair_ops=['greedy_insert'],
            n_iter=5, verbose=False
        )
        assert a.destroy_ops == ['random']

    def test_rng_is_numpy_generator(self, alns):
        """_rng must be a numpy.random.Generator."""
        assert isinstance(alns._rng, np.random.Generator)


# =============================================================================
# 3. Internal helpers
# =============================================================================

class TestOrderingFromRule:

    @pytest.mark.parametrize("rule", ['es', 'lf', 'duration', 'mts', 'grpw'])
    def test_named_rules_return_all_activities(self, alns, pert, rule):
        """Every named rule must return all activities."""
        ordering = alns._ordering_from_rule(rule)
        assert set(ordering) == set(pert.forwardDict.keys())

    def test_returns_activity_objects(self, alns, pert):
        ordering = alns._ordering_from_rule('es')
        assert len(ordering) == len(pert.forwardDict)
        for act in ordering:
            assert act in pert.forwardDict

    def test_random_rule_returns_all_activities(self, alns, pert):
        ordering = alns._ordering_from_rule('random')
        assert set(ordering) == set(pert.forwardDict.keys())


class TestNonDummy:

    def test_excludes_start_activity(self, alns, pert):
        ordering = list(pert.forwardDict.keys())
        non_dummy = alns._non_dummy(ordering)
        if pert.startActivity:
            assert pert.startActivity not in non_dummy

    def test_excludes_end_activity(self, alns, pert):
        ordering = list(pert.forwardDict.keys())
        non_dummy = alns._non_dummy(ordering)
        if pert.endActivity:
            assert pert.endActivity not in non_dummy

    def test_non_dummy_plus_dummies_covers_all(self, alns, pert):
        """Non-dummy + dummies must reconstruct the original ordering."""
        ordering = list(pert.forwardDict.keys())
        non_dummy = alns._non_dummy(ordering)
        assert set(non_dummy) | alns._dummies == set(ordering)

    def test_empty_ordering_returns_empty(self, alns):
        assert alns._non_dummy([]) == []

    def test_count_is_total_minus_dummies(self, alns, pert):
        ordering = list(pert.forwardDict.keys())
        non_dummy = alns._non_dummy(ordering)
        n_dummies_in_ordering = sum(
            1 for a in ordering if a in alns._dummies
        )
        assert len(non_dummy) == len(ordering) - n_dummies_in_ordering


class TestTopoSort:

    def test_empty_input_returns_empty(self, alns):
        assert alns._topo_sort([]) == []

    def test_single_activity_returned(self, alns, pert):
        acts = list(pert.forwardDict.keys())
        result = alns._topo_sort([acts[1]])
        assert result == [acts[1]]

    def test_same_activities_returned(self, alns, pert):
        """Output set must equal input set."""
        acts = list(pert.forwardDict.keys())
        result = alns._topo_sort(acts)
        assert set(result) == set(acts)

    def test_predecessors_before_successors(self, alns, pert):
        """For every pair (pred, succ) in backwardDict, pred must appear first."""
        acts = list(pert.forwardDict.keys())
        result = alns._topo_sort(acts)
        assert _is_precedence_feasible(result, pert.backwardDict)

    def test_subset_is_topologically_ordered(self, alns, pert):
        """Works correctly on a subset of activities."""
        acts = list(pert.forwardDict.keys())
        subset = acts[::2]  # every other activity
        result = alns._topo_sort(subset)
        assert set(result) == set(subset)
        # Check feasibility within the subset
        subset_set = set(subset)
        pos = {a: i for i, a in enumerate(result)}
        for act in subset:
            for pred in pert.backwardDict.get(act, []):
                if pred in subset_set:
                    assert pos[pred] < pos[act]


class TestInsertFeasible:

    def test_length_increases_by_one(self, alns, init_state):
        """After insertion the ordering must have exactly one more activity."""
        # Destroy one activity to get something to reinsert
        rng = np.random.default_rng(1)
        destroyed = alns._destroy_random(init_state, rng)
        act = destroyed.unscheduled[0]
        ordering = list(destroyed.ordering)
        original_len = len(ordering)
        alns._insert_feasible(act, ordering, rng, greedy=True)
        assert len(ordering) == original_len + 1

    def test_inserted_activity_present(self, alns, init_state):
        """The target activity must appear in the ordering after insertion."""
        rng = np.random.default_rng(2)
        destroyed = alns._destroy_random(init_state, rng)
        act = destroyed.unscheduled[0]
        ordering = list(destroyed.ordering)
        alns._insert_feasible(act, ordering, rng, greedy=True)
        assert act in ordering

    def test_greedy_inserts_at_lo(self, alns, init_state):
        """Greedy insertion must place the activity as early as possible (at lo)."""
        rng = np.random.default_rng(3)
        destroyed = alns._destroy_random(init_state, rng)
        for act in destroyed.unscheduled:
            ordering = list(destroyed.ordering)
            pos_before = {a: p for p, a in enumerate(ordering)}
            preds = [pr for pr in alns.pert.backwardDict.get(act, []) if pr in pos_before]
            lo = (max(pos_before[pr] for pr in preds) + 1) if preds else 0
            alns._insert_feasible(act, ordering, rng, greedy=True)
            inserted_pos = ordering.index(act)
            assert inserted_pos == lo

    def test_result_is_precedence_feasible(self, alns, init_state):
        """After greedy insertion the complete ordering must remain feasible."""
        rng = np.random.default_rng(4)
        destroyed = alns._destroy_random(init_state, rng)
        ordering = list(destroyed.ordering)
        for act in alns._topo_sort(destroyed.unscheduled):
            alns._insert_feasible(act, ordering, rng, greedy=True)
        assert _is_precedence_feasible(ordering, alns.pert.backwardDict)


# =============================================================================
# 4. Destroy operators
# =============================================================================

def _check_destroy_invariants(destroyed, original_state, alns):
    """
    Common post-conditions for all destroy operators:
      - Result is a different object.
      - Original state is unchanged.
      - Union of ordering + unscheduled equals the original ordering.
      - Cache is invalidated.
      - Dummy activities are not in unscheduled.
      - At least one activity was removed.
    """
    assert destroyed is not original_state
    assert original_state.ordering == list(alns._ordering_from_rule('ls'))  # unchanged (not reliable — skip)
    assert (set(destroyed.ordering) | set(destroyed.unscheduled)
            == set(original_state.ordering))
    assert destroyed._obj_cache is None
    assert len(destroyed.unscheduled) > 0
    for act in destroyed.unscheduled:
        assert act not in alns._dummies, (
            f"Dummy activity {act} appeared in unscheduled"
        )


class TestDestroyMostMobile:

    def _make_state(self, alns):
        """Fresh complete state from 'ls' priority rule."""
        ordering = alns._ordering_from_rule('ls')
        return RCPSPState(alns.pert, ordering)

    def test_returns_new_object(self, alns):
        state = self._make_state(alns)
        rng = np.random.default_rng(0)
        destroyed = alns._destroy_most_mobile(state, rng)
        assert destroyed is not state

    def test_original_ordering_unchanged(self, alns):
        state = self._make_state(alns)
        original_ordering = list(state.ordering)
        rng = np.random.default_rng(0)
        alns._destroy_most_mobile(state, rng)
        assert state.ordering == original_ordering

    def test_cache_invalidated(self, alns):
        state = self._make_state(alns)
        rng = np.random.default_rng(0)
        destroyed = alns._destroy_most_mobile(state, rng)
        assert destroyed._obj_cache is None

    def test_dummies_not_removed(self, alns):
        state = self._make_state(alns)
        rng = np.random.default_rng(0)
        destroyed = alns._destroy_most_mobile(state, rng)
        for act in destroyed.unscheduled:
            assert act not in alns._dummies

    def test_union_covers_original(self, alns):
        state = self._make_state(alns)
        rng = np.random.default_rng(0)
        destroyed = alns._destroy_most_mobile(state, rng)
        assert set(destroyed.ordering) | set(destroyed.unscheduled) == set(state.ordering)

    def test_k_activities_removed(self, alns):
        """Number removed must be max(1, floor(n_non_dummy * fraction))."""
        state = self._make_state(alns)
        n_nd = len(alns._non_dummy(state.ordering))
        expected_k = max(1, int(n_nd * alns.destroy_fraction))
        rng = np.random.default_rng(0)
        destroyed = alns._destroy_most_mobile(state, rng)
        assert len(destroyed.unscheduled) == expected_k

    def test_highest_slack_activities_removed(self, alns):
        """Removed activities must be the k highest-slack ones."""
        state = self._make_state(alns)
        non_dummy = alns._non_dummy(state.ordering)
        n_nd = len(non_dummy)
        k = max(1, int(n_nd * alns.destroy_fraction))
        expected_removed = set(
            sorted(non_dummy, key=lambda a: alns._slack.get(a, 0.0), reverse=True)[:k]
        )
        rng = np.random.default_rng(0)
        destroyed = alns._destroy_most_mobile(state, rng)
        assert set(destroyed.unscheduled) == expected_removed


class TestDestroySegment:

    def _make_state(self, alns):
        ordering = alns._ordering_from_rule('es')
        return RCPSPState(alns.pert, ordering)

    def test_returns_new_object(self, alns):
        state = self._make_state(alns)
        rng = np.random.default_rng(5)
        assert alns._destroy_segment(state, rng) is not state

    def test_original_ordering_unchanged(self, alns):
        state = self._make_state(alns)
        original = list(state.ordering)
        alns._destroy_segment(state, np.random.default_rng(5))
        assert state.ordering == original

    def test_cache_invalidated(self, alns):
        state = self._make_state(alns)
        destroyed = alns._destroy_segment(state, np.random.default_rng(5))
        assert destroyed._obj_cache is None

    def test_dummies_not_removed(self, alns):
        state = self._make_state(alns)
        for seed in range(10):
            destroyed = alns._destroy_segment(state, np.random.default_rng(seed))
            for act in destroyed.unscheduled:
                assert act not in alns._dummies

    def test_union_covers_original(self, alns):
        state = self._make_state(alns)
        destroyed = alns._destroy_segment(state, np.random.default_rng(5))
        assert set(destroyed.ordering) | set(destroyed.unscheduled) == set(state.ordering)

    def test_k_activities_removed(self, alns):
        state = self._make_state(alns)
        n_nd = len(alns._non_dummy(state.ordering))
        expected_k = max(1, min(int(n_nd * alns.destroy_fraction), n_nd))
        destroyed = alns._destroy_segment(state, np.random.default_rng(5))
        assert len(destroyed.unscheduled) == expected_k

    def test_removed_segment_is_contiguous(self, alns):
        """Removed activities must have been contiguous in the original non-dummy ordering."""
        state = self._make_state(alns)
        non_dummy = alns._non_dummy(state.ordering)
        nd_positions = {a: i for i, a in enumerate(non_dummy)}
        for seed in range(8):
            destroyed = alns._destroy_segment(state, np.random.default_rng(seed))
            positions = sorted(nd_positions[a] for a in destroyed.unscheduled
                               if a in nd_positions)
            if len(positions) > 1:
                assert positions == list(range(positions[0], positions[0] + len(positions))), (
                    f"Removed segment is not contiguous: {positions}"
                )


class TestDestroyRandom:

    def _make_state(self, alns):
        ordering = alns._ordering_from_rule('lf')
        return RCPSPState(alns.pert, ordering)

    def test_returns_new_object(self, alns):
        state = self._make_state(alns)
        assert alns._destroy_random(state, np.random.default_rng(7)) is not state

    def test_original_ordering_unchanged(self, alns):
        state = self._make_state(alns)
        original = list(state.ordering)
        alns._destroy_random(state, np.random.default_rng(7))
        assert state.ordering == original

    def test_cache_invalidated(self, alns):
        state = self._make_state(alns)
        destroyed = alns._destroy_random(state, np.random.default_rng(7))
        assert destroyed._obj_cache is None

    def test_dummies_not_removed(self, alns):
        state = self._make_state(alns)
        for seed in range(10):
            destroyed = alns._destroy_random(state, np.random.default_rng(seed))
            for act in destroyed.unscheduled:
                assert act not in alns._dummies

    def test_union_covers_original(self, alns):
        state = self._make_state(alns)
        destroyed = alns._destroy_random(state, np.random.default_rng(7))
        assert set(destroyed.ordering) | set(destroyed.unscheduled) == set(state.ordering)

    def test_k_activities_removed(self, alns):
        state = self._make_state(alns)
        n_nd = len(alns._non_dummy(state.ordering))
        expected_k = max(1, min(int(n_nd * alns.destroy_fraction), n_nd))
        destroyed = alns._destroy_random(state, np.random.default_rng(7))
        assert len(destroyed.unscheduled) == expected_k

    def test_different_seeds_produce_variety(self, alns):
        """Different RNG seeds should (almost always) produce different removals."""
        state = self._make_state(alns)
        results = [
            frozenset(alns._destroy_random(state, np.random.default_rng(s)).unscheduled)
            for s in range(5)
        ]
        # At least two distinct removal sets expected with 5 seeds
        assert len(set(results)) > 1, "All seeds produced identical removals"


# =============================================================================
# 5. Repair operators
# =============================================================================

def _make_destroyed_state(alns, rule='ls', destroy='segment', seed=0):
    """Helper: build a destroyed state for repair tests."""
    ordering = alns._ordering_from_rule(rule)
    state = RCPSPState(alns.pert, ordering)
    rng = np.random.default_rng(seed)
    if destroy == 'segment':
        return alns._destroy_segment(state, rng)
    if destroy == 'random':
        return alns._destroy_random(state, rng)
    return alns._destroy_most_mobile(state, rng)


class TestRepairRandomInsert:

    def test_returns_complete_state(self, alns):
        """Repaired state must have no unscheduled activities."""
        destroyed = _make_destroyed_state(alns, seed=1)
        rng = np.random.default_rng(1)
        repaired = alns._repair_random_insert(destroyed, rng)
        assert repaired.unscheduled == []

    def test_all_activities_present(self, alns, pert):
        """All activities must be in the repaired ordering."""
        destroyed = _make_destroyed_state(alns, seed=2)
        rng = np.random.default_rng(2)
        repaired = alns._repair_random_insert(destroyed, rng)
        assert set(repaired.ordering) == set(pert.forwardDict.keys())

    def test_ordering_correct_length(self, alns, pert):
        destroyed = _make_destroyed_state(alns, seed=3)
        rng = np.random.default_rng(3)
        repaired = alns._repair_random_insert(destroyed, rng)
        assert len(repaired.ordering) == len(pert.forwardDict)

    def test_ordering_is_precedence_feasible(self, alns):
        """Repaired ordering must satisfy all precedence constraints."""
        for seed in range(5):
            destroyed = _make_destroyed_state(alns, seed=seed)
            rng = np.random.default_rng(seed)
            repaired = alns._repair_random_insert(destroyed, rng)
            assert _is_precedence_feasible(repaired.ordering, alns.pert.backwardDict), (
                f"Precedence violated after random_insert (seed={seed})"
            )

    def test_cache_invalidated(self, alns):
        destroyed = _make_destroyed_state(alns, seed=4)
        rng = np.random.default_rng(4)
        repaired = alns._repair_random_insert(destroyed, rng)
        assert repaired._obj_cache is None

    def test_returns_new_object(self, alns):
        destroyed = _make_destroyed_state(alns, seed=5)
        rng = np.random.default_rng(5)
        repaired = alns._repair_random_insert(destroyed, rng)
        assert repaired is not destroyed

    def test_destroyed_state_unmodified(self, alns):
        """The destroyed state passed in must not be mutated."""
        destroyed = _make_destroyed_state(alns, seed=6)
        original_unscheduled = list(destroyed.unscheduled)
        rng = np.random.default_rng(6)
        alns._repair_random_insert(destroyed, rng)
        assert destroyed.unscheduled == original_unscheduled

    def test_different_seeds_produce_different_orderings(self, alns):
        """Random insertion with different seeds should usually differ."""
        destroyed = _make_destroyed_state(alns, seed=0)
        orderings = [
            alns._repair_random_insert(destroyed.copy(), np.random.default_rng(s)).ordering
            for s in range(6)
        ]
        unique_orderings = {tuple(o) for o in orderings}
        assert len(unique_orderings) > 1, "All seeds produced identical orderings"


class TestRepairGreedyInsert:

    def test_returns_complete_state(self, alns):
        destroyed = _make_destroyed_state(alns, seed=10)
        rng = np.random.default_rng(10)
        repaired = alns._repair_greedy_insert(destroyed, rng)
        assert repaired.unscheduled == []

    def test_all_activities_present(self, alns, pert):
        destroyed = _make_destroyed_state(alns, seed=11)
        rng = np.random.default_rng(11)
        repaired = alns._repair_greedy_insert(destroyed, rng)
        assert set(repaired.ordering) == set(pert.forwardDict.keys())

    def test_ordering_correct_length(self, alns, pert):
        destroyed = _make_destroyed_state(alns, seed=12)
        rng = np.random.default_rng(12)
        repaired = alns._repair_greedy_insert(destroyed, rng)
        assert len(repaired.ordering) == len(pert.forwardDict)

    def test_ordering_is_precedence_feasible(self, alns):
        """Greedy repair must always produce a precedence-feasible ordering."""
        for seed in range(5):
            destroyed = _make_destroyed_state(alns, seed=seed)
            rng = np.random.default_rng(seed)
            repaired = alns._repair_greedy_insert(destroyed, rng)
            assert _is_precedence_feasible(repaired.ordering, alns.pert.backwardDict), (
                f"Precedence violated after greedy_insert (seed={seed})"
            )

    def test_cache_invalidated(self, alns):
        destroyed = _make_destroyed_state(alns, seed=13)
        rng = np.random.default_rng(13)
        repaired = alns._repair_greedy_insert(destroyed, rng)
        assert repaired._obj_cache is None

    def test_returns_new_object(self, alns):
        destroyed = _make_destroyed_state(alns, seed=14)
        rng = np.random.default_rng(14)
        repaired = alns._repair_greedy_insert(destroyed, rng)
        assert repaired is not destroyed

    def test_destroyed_state_unmodified(self, alns):
        destroyed = _make_destroyed_state(alns, seed=15)
        original_unscheduled = list(destroyed.unscheduled)
        rng = np.random.default_rng(15)
        alns._repair_greedy_insert(destroyed, rng)
        assert destroyed.unscheduled == original_unscheduled

    def test_greedy_gives_consistent_result(self, alns):
        """Greedy repair is deterministic (RNG not used), so two calls must agree."""
        destroyed = _make_destroyed_state(alns, seed=0)
        r1 = alns._repair_greedy_insert(destroyed.copy(), np.random.default_rng(0))
        r2 = alns._repair_greedy_insert(destroyed.copy(), np.random.default_rng(99))
        assert r1.ordering == r2.ordering


# =============================================================================
# 6. Full run and result helpers
# =============================================================================

class TestRun:

    @pytest.fixture(scope='class')
    def run_results(self, pert):
        """Run ALNS once with tiny settings and cache the outputs."""
        a = RCPSPAdaptiveLNS(
            pert,
            n_iter=20,
            destroy_fraction=0.3,
            seg_length=10,
            seed=42,
            accept='hill_climbing',
            verbose=False,
        )
        best_state, log = a.run()
        return a, best_state, log

    def test_run_returns_state_and_dict(self, run_results):
        _, best_state, log = run_results
        assert isinstance(best_state, RCPSPState)
        assert isinstance(log, dict)

    def test_best_state_is_complete(self, run_results):
        """Best state must be a complete schedule (no unscheduled activities)."""
        _, best_state, _ = run_results
        assert best_state.unscheduled == []

    def test_best_duration_positive_finite(self, run_results):
        _, best_state, _ = run_results
        dur = best_state.objective()
        assert dur > 0
        assert dur < float('inf')

    def test_log_has_required_keys(self, run_results):
        _, _, log = run_results
        required = {'initial_duration', 'best_duration', 'improvement',
                    'destroy_counts', 'repair_counts'}
        assert required.issubset(log.keys())

    def test_log_improvement_non_negative(self, run_results):
        """Hill-climbing can only improve or stay the same."""
        _, _, log = run_results
        assert log['improvement'] >= -1e-9, (
            f"Unexpected regression: {log['improvement']:.4f}"
        )

    def test_best_duration_equals_log(self, run_results):
        _, best_state, log = run_results
        assert abs(best_state.objective() - log['best_duration']) < 1e-9

    def test_destroy_counts_cover_all_ops(self, run_results):
        a, _, log = run_results
        for op in a.destroy_ops:
            assert op in log['destroy_counts'], f"Missing destroy op '{op}' in counts"

    def test_repair_counts_cover_all_ops(self, run_results):
        a, _, log = run_results
        for op in a.repair_ops:
            assert op in log['repair_counts'], f"Missing repair op '{op}' in counts"

    def test_get_best_schedule_returns_dict(self, run_results):
        a, best_state, _ = run_results
        result = a.get_best_schedule(best_state)
        assert isinstance(result, dict)

    def test_get_best_schedule_has_duration_key(self, run_results):
        a, best_state, _ = run_results
        result = a.get_best_schedule(best_state)
        assert 'scheduled_duration' in result

    def test_get_best_activity_list_correct_length(self, run_results, pert):
        a, best_state, _ = run_results
        act_list = a.get_best_activity_list(best_state)
        assert len(act_list) == len(pert.forwardDict)

    def test_get_best_activity_list_all_strings(self, run_results):
        a, best_state, _ = run_results
        act_list = a.get_best_activity_list(best_state)
        assert all(isinstance(name, str) for name in act_list)

    def test_get_best_activity_list_no_duplicates(self, run_results):
        a, best_state, _ = run_results
        act_list = a.get_best_activity_list(best_state)
        assert len(act_list) == len(set(act_list)), "Duplicate names in best activity list"

    @pytest.mark.parametrize("accept", [
        'hill_climbing', 'record_to_record', 'simulated_annealing'
    ])
    def test_all_accept_criteria_complete(self, pert, accept):
        """All acceptance criteria must complete a short run without error."""
        a = RCPSPAdaptiveLNS(
            pert, n_iter=15, seg_length=5, seed=1,
            accept=accept, verbose=False,
        )
        best_state, log = a.run()
        assert isinstance(best_state, RCPSPState)
        assert best_state.unscheduled == []
        assert log['best_duration'] > 0

    @pytest.mark.parametrize("destroy,repair", [
        (['most_mobile'], ['random_insert']),
        (['segment'],     ['greedy_insert']),
        (['random'],      ['random_insert']),
        (['most_mobile', 'random'], ['random_insert', 'greedy_insert']),
    ])
    def test_operator_combinations_complete(self, pert, destroy, repair):
        """Various operator subsets must complete a short run without error."""
        a = RCPSPAdaptiveLNS(
            pert, n_iter=10, seg_length=5, seed=0,
            destroy_ops=destroy, repair_ops=repair,
            verbose=False,
        )
        best_state, log = a.run()
        assert best_state.unscheduled == []
        assert log['best_duration'] > 0


# =============================================================================
# Standalone runner
# =============================================================================

if __name__ == '__main__':
    import subprocess
    sys.exit(subprocess.call(['pytest', __file__, '-v']))
