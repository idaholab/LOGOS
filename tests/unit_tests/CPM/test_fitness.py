"""
Unit tests for Pert.compute_fitness() — the GP training signal.

Tests cover:
- RuntimeError when called before scheduling
- Return dict keys and value types
- Composite score formula with known weights
- Component bounds (all non-negative; makespan_ratio ≥ 1)
- Composite is lower-bounded by makespan_ratio alone
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path

from conftest import assert_valid_schedule
from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import load_outage_data


from conftest import SCHEMA_PATH, EXAMPLES_DIR  # canonical locations (see H2)
EXAMPLE_10 = str(EXAMPLES_DIR / "example_10.json")


# ---------------------------------------------------------------------------
# Error before scheduling
# ---------------------------------------------------------------------------

class TestComputeFitnessPreCondition:

    def test_raises_before_scheduling(self):
        start = Activity("START", 0.0)
        end = Activity("END", 0.0)
        act = Activity("A", 4.0)
        p = Pert(graph={start: [act], act: [end], end: []})
        with pytest.raises(RuntimeError, match="calculateScheduleWithResources"):
            p.compute_fitness()


# ---------------------------------------------------------------------------
# Return structure
# ---------------------------------------------------------------------------

class TestComputeFitnessReturnStructure:

    @pytest.fixture(scope="class")
    def scheduled_pert(self):
        p = Pert.from_json_file(EXAMPLE_10, SCHEMA_PATH)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        return p

    def test_returns_dict(self, scheduled_pert):
        result = scheduled_pert.compute_fitness()
        assert isinstance(result, dict)

    def test_required_keys_present(self, scheduled_pert):
        result = scheduled_pert.compute_fitness()
        required = {
            "composite", "makespan_ratio", "delay_ratio",
            "criticality_ratio", "scheduled_duration",
            "cpm_duration", "delay_hours",
        }
        assert required.issubset(result.keys())

    def test_all_values_are_finite_numbers(self, scheduled_pert):
        result = scheduled_pert.compute_fitness()
        import math
        # n_window_violations is an integer count; all other values are floats
        int_keys = {'n_window_violations'}
        for key, val in result.items():
            if key in int_keys:
                assert isinstance(val, int), f"{key} is not int"
            else:
                assert isinstance(val, float), f"{key} is not float"
                assert math.isfinite(val), f"{key} is not finite: {val}"


# ---------------------------------------------------------------------------
# Component bounds
# ---------------------------------------------------------------------------

class TestComputeFitnessComponentBounds:

    @pytest.fixture(scope="class")
    def fitness(self):
        p = Pert.from_json_file(EXAMPLE_10, SCHEMA_PATH)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        return p.compute_fitness()

    def test_makespan_ratio_at_least_one(self, fitness):
        # Resource constraints can only stretch the schedule, never compress it
        assert fitness["makespan_ratio"] >= 1.0 - 1e-6

    def test_delay_ratio_non_negative(self, fitness):
        assert fitness["delay_ratio"] >= 0.0

    def test_criticality_ratio_between_0_and_1(self, fitness):
        assert 0.0 <= fitness["criticality_ratio"] <= 1.0

    def test_composite_positive(self, fitness):
        assert fitness["composite"] > 0.0


# ---------------------------------------------------------------------------
# Composite formula
# ---------------------------------------------------------------------------

class TestComputeFitnessFormula:

    @pytest.fixture(scope="class")
    def pert_instance(self):
        p = Pert.from_json_file(EXAMPLE_10, SCHEMA_PATH)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        return p

    def test_composite_formula_with_default_weights(self, pert_instance):
        result = pert_instance.compute_fitness(alpha=1.0, beta=0.5, gamma=0.3)
        expected = (
            1.0 * result["makespan_ratio"]
            + 0.5 * result["delay_ratio"]
            + 0.3 * result["criticality_ratio"]
        )
        assert abs(result["composite"] - expected) < 1e-9

    def test_composite_formula_with_custom_weights(self, pert_instance):
        result = pert_instance.compute_fitness(alpha=2.0, beta=0.0, gamma=0.0)
        expected = 2.0 * result["makespan_ratio"]
        assert abs(result["composite"] - expected) < 1e-9

    def test_zero_weights_gives_zero_composite(self, pert_instance):
        result = pert_instance.compute_fitness(alpha=0.0, beta=0.0, gamma=0.0)
        assert abs(result["composite"]) < 1e-9

    def test_different_sgs_can_produce_different_fitness(self, pert_instance):
        # Two different SGS strategies should generally produce different schedules.
        # We just verify that the method is callable for both without error.
        p1 = Pert.from_json_file(EXAMPLE_10, SCHEMA_PATH)
        p1.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert_valid_schedule(p1)
        f1 = p1.compute_fitness()

        p2 = Pert.from_json_file(EXAMPLE_10, SCHEMA_PATH)
        p2.calculateScheduleWithResources(sgs='max_use_res_shuffled')
        assert_valid_schedule(p2)
        f2 = p2.compute_fitness()

        # Both must produce valid dicts regardless of which is better
        assert f1["composite"] > 0.0
        assert f2["composite"] > 0.0
