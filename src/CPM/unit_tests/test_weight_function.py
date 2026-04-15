"""
Unit tests for _weight_function — priority weight based on activity slack.

Key properties under test:
- Monotonically decreasing: more slack → lower weight (less urgent)
- Inflection at threshold = max(5, 0.01 * project_duration)
- Output always in (0, 1)
- Legacy behaviour preserved for short networks (project_duration ≤ 500 h)
"""

import math
import pytest

from CPM.pert import _weight_function


TOL = 1e-4


# ---------------------------------------------------------------------------
# Output range
# ---------------------------------------------------------------------------

class TestOutputRange:

    def test_output_bounded_between_0_and_1(self):
        # The sigmoid maps R → (0,1) analytically, but at extreme slack values
        # floating-point underflow/overflow collapses it to exactly 0.0 or 1.0.
        # We therefore accept [0, 1] (closed) rather than (0, 1) (open).
        for slack in [-10, 0, 5, 20, 100, 1000]:
            for dur in [10, 100, 500, 720, 2000]:
                w = _weight_function(float(slack), float(dur))
                assert 0.0 <= w <= 1.0, f"Out of range: slack={slack}, dur={dur}, w={w}"

    def test_zero_slack_near_one(self):
        # An activity with zero slack should receive near-maximum priority
        for dur in [10, 500, 720, 2000]:
            w = _weight_function(0.0, float(dur))
            assert w > 0.98, f"Expected w≈1 for slack=0, got {w} (dur={dur})"

    def test_large_slack_near_zero(self):
        # An activity far past the threshold should have near-zero priority
        for dur in [10, 720]:
            threshold = max(5.0, 0.01 * dur)
            w = _weight_function(threshold * 5, dur)
            assert w < 0.02, f"Expected w≈0 for large slack, got {w} (dur={dur})"


# ---------------------------------------------------------------------------
# Monotonicity
# ---------------------------------------------------------------------------

class TestMonotonicity:

    def test_strictly_decreasing_in_slack(self):
        # The sigmoid is strictly decreasing, but floating-point underflow means
        # very large slack values both map to 0.0 (equal, not strictly less).
        # We test over a range where the values are still numerically distinct.
        dur = 720.0
        prev = _weight_function(0.0, dur)
        for slack in [1, 5, 10, 20, 30]:
            w = _weight_function(float(slack), dur)
            assert w <= prev, f"Not non-increasing at slack={slack}"
            # Verify strict decrease while values are still meaningfully distinct
            if prev > 1e-6:
                assert w < prev, f"Not strictly decreasing at slack={slack}"
            prev = w

    def test_monotone_across_project_durations(self):
        # For any fixed slack, a larger project_duration shifts the inflection
        # point right, so priority at a fixed small slack should increase
        # (the same 5-hour slack is more urgent relative to a longer project).
        slack = 5.0
        prev = _weight_function(slack, 10.0)
        for dur in [100, 500, 720, 2000]:
            w = _weight_function(slack, float(dur))
            # Larger project_duration → higher threshold → slack=5 is below threshold → higher weight
            assert w >= prev - TOL, f"Monotonicity broken at dur={dur}"
            prev = w


# ---------------------------------------------------------------------------
# Inflection point at threshold
# ---------------------------------------------------------------------------

class TestInflectionPoint:

    def test_inflection_at_5h_for_short_project(self):
        # For project_duration ≤ 500 h, threshold = 5 h (floor)
        w = _weight_function(5.0, project_duration=100.0)
        assert abs(w - 0.5) < TOL, f"Expected 0.5 at inflection, got {w}"

    def test_inflection_scales_with_project_duration(self):
        # For project_duration = 720 h: threshold = max(5, 7.2) = 7.2 h
        w = _weight_function(7.2, project_duration=720.0)
        assert abs(w - 0.5) < TOL, f"Expected 0.5 at threshold=7.2 h, got {w}"

    def test_inflection_at_20h_for_2000h_project(self):
        w = _weight_function(20.0, project_duration=2000.0)
        assert abs(w - 0.5) < TOL, f"Expected 0.5 at threshold=20 h, got {w}"

    def test_floor_prevents_sub_5h_threshold(self):
        # Even for a tiny 2-hour project, threshold is still 5 h
        w_tiny = _weight_function(5.0, project_duration=2.0)
        w_ref  = _weight_function(5.0, project_duration=500.0)
        assert abs(w_tiny - w_ref) < TOL


# ---------------------------------------------------------------------------
# Legacy behaviour (default project_duration=10)
# ---------------------------------------------------------------------------

class TestLegacyBehaviour:

    def test_default_arg_inflection_at_5(self):
        # Calling with default project_duration should give inflection at 5 h
        w = _weight_function(5.0)
        assert abs(w - 0.5) < TOL

    def test_explicit_10h_same_as_default(self):
        for slack in [0, 2, 5, 10]:
            assert abs(
                _weight_function(float(slack)) -
                _weight_function(float(slack), 10.0)
            ) < 1e-12
