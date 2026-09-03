"""
Integration tests for the full scheduling pipeline using the JSON fixtures
shipped with the CPM module.

Tests verify:
- Successful load + validation + scheduling (no crash)
- All activities completed
- No dependency violations (every task starts after all its predecessors end)
- No resource over-allocation at any hour
- Scheduled duration ≥ CPM duration (resource constraints can only stretch)
- Priority rules all produce valid schedules
- compute_fitness() returns a consistent value after scheduling
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path

from conftest import assert_valid_schedule
from CPM.pert import Pert


from conftest import SCHEMA_PATH as SCHEMA, EXAMPLES_DIR as DATA_DIR  # canonical locations (see H2)


# ---------------------------------------------------------------------------
# Fixtures: pre-loaded + pre-scheduled Pert instances
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sched_example_10():
    p = Pert.from_json_file(str(DATA_DIR / "example_10.json"), SCHEMA)
    p.calculateScheduleWithResources(sgs='max_use_res_ranked')
    return p


@pytest.fixture(scope="module")
def sched_test_case_1():
    p = Pert.from_json_file(str(DATA_DIR / "test_case_1.json"), SCHEMA)
    p.calculateScheduleWithResources(sgs='max_use_res_ranked')
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _real_activities(pert):
    """Return activities excluding dummy START and END."""
    return [a for a in pert.forwardDict
            if a.name.upper() not in ("START", "END")]


def _resource_usage_at(pert, t):
    """Return {skill: count} actually running at time t."""
    usage = {}
    for act in pert.ongoing + pert.completed:
        st, et = act.returnAbsTimes()
        if st is None or et is None:
            continue
        if st <= t < et:
            for req in act.getRequiredResources():
                skill = req["skill_type"]
                usage[skill] = usage.get(skill, 0) + req["crew_count"]
    return usage


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

class TestSchedulingCompletion:

    def test_all_activities_completed_example_10(self, sched_example_10):
        n_total = len(sched_example_10.infoDict)
        assert len(sched_example_10.completed) == n_total

    def test_all_activities_completed_test_case_1(self, sched_test_case_1):
        n_total = len(sched_test_case_1.infoDict)
        assert len(sched_test_case_1.completed) == n_total

    def test_every_activity_has_actual_times(self, sched_example_10):
        for act in _real_activities(sched_example_10):
            st, et = act.returnAbsTimes()
            assert st is not None, f"{act.name} has no start time"
            assert et is not None, f"{act.name} has no end time"

    def test_end_time_equals_start_plus_duration(self, sched_example_10):
        tol = timedelta(seconds=1)
        for act in _real_activities(sched_example_10):
            st, et = act.returnAbsTimes()
            if st is None:
                continue
            expected_et = st + timedelta(hours=act.duration)
            assert abs((et - expected_et).total_seconds()) < 1, \
                f"{act.name}: end-start={et-st} != duration={act.duration}h"


# ---------------------------------------------------------------------------
# Dependency constraints
# ---------------------------------------------------------------------------

class TestDependencyConstraints:

    def _check_no_violations(self, pert):
        violations = []
        for act, succs in pert.forwardDict.items():
            _, act_et = act.returnAbsTimes()
            if act_et is None:
                continue
            for succ in succs:
                succ_st, _ = succ.returnAbsTimes()
                if succ_st is None:
                    continue
                lag = pert.lag_dict.get((act, succ), 0.0)
                earliest_succ_start = act_et + timedelta(hours=lag)
                if succ_st < earliest_succ_start - timedelta(seconds=1):
                    violations.append(
                        f"{succ.name} starts {succ_st} before "
                        f"{act.name} ends+lag {earliest_succ_start}"
                    )
        return violations

    def test_no_dependency_violations_example_10(self, sched_example_10):
        violations = self._check_no_violations(sched_example_10)
        assert violations == [], "\n".join(violations)

    def test_no_dependency_violations_test_case_1(self, sched_test_case_1):
        violations = self._check_no_violations(sched_test_case_1)
        assert violations == [], "\n".join(violations)


# ---------------------------------------------------------------------------
# Resource constraints
# ---------------------------------------------------------------------------

class TestResourceConstraints:

    def _check_no_overallocation(self, pert):
        """
        Walk hour by hour and verify resource usage never exceeds pool capacity.
        Returns list of violation strings (empty = pass).
        """
        violations = []
        start = pert.startTime
        end = pert.get_project_finish_actual()
        t = start
        while t < end:
            for skill in pert.crew_pool.get_all_skills():
                available = pert.crew_pool.get_availability(skill, t)
                used = sum(
                    req["crew_count"]
                    for act in pert.completed
                    for req in act.getRequiredResources()
                    if req["skill_type"] == skill
                    and act.returnAbsTimes()[0] is not None
                    and act.returnAbsTimes()[0] <= t < act.returnAbsTimes()[1]
                )
                if used > available + 1e-6:
                    violations.append(
                        f"t={t}: {skill} used={used} > available={available}"
                    )
            t += timedelta(hours=1)
        return violations

    def test_no_resource_overallocation_example_10(self, sched_example_10):
        violations = self._check_no_overallocation(sched_example_10)
        assert violations == [], f"{len(violations)} overallocation(s):\n" + "\n".join(violations[:5])

    def test_no_resource_overallocation_test_case_1(self, sched_test_case_1):
        violations = self._check_no_overallocation(sched_test_case_1)
        assert violations == [], f"{len(violations)} overallocation(s):\n" + "\n".join(violations[:5])


# ---------------------------------------------------------------------------
# Makespan bound
# ---------------------------------------------------------------------------

class TestMakespanBound:

    def test_scheduled_duration_geq_cpm_duration_example_10(self, sched_example_10):
        cpm = sched_example_10.getProjectDuration()
        end = sched_example_10.get_project_finish_actual()
        actual = (end - sched_example_10.startTime).total_seconds() / 3600.0
        assert actual >= cpm - 1e-3, \
            f"Scheduled duration {actual:.1f}h < CPM {cpm:.1f}h — impossible"

    def test_scheduled_duration_geq_cpm_duration_test_case_1(self, sched_test_case_1):
        cpm = sched_test_case_1.getProjectDuration()
        end = sched_test_case_1.get_project_finish_actual()
        actual = (end - sched_test_case_1.startTime).total_seconds() / 3600.0
        assert actual >= cpm - 1e-3


# ---------------------------------------------------------------------------
# Priority rules — smoke test: each rule runs without error
# ---------------------------------------------------------------------------

PRIORITY_RULES = [
    "lf", "ls", "ef", "es", "duration",
    "mts", "mtp", "grpw", "grd", "rr",
    "mehh_8000_b", "gphh_b",
]


@pytest.mark.parametrize("rule", PRIORITY_RULES)
def test_priority_rule_completes(rule):
    """Each priority rule must produce a complete, valid schedule."""
    p = Pert.from_json_file(str(DATA_DIR / "test_case_1.json"), SCHEMA)
    p.calculateScheduleWithResources(sgs='max_use_res_ranked', priority_rule=rule)
    assert_valid_schedule(p)
    n_total = len(p.infoDict)
    assert len(p.completed) == n_total, \
        f"Rule '{rule}' left {n_total - len(p.completed)} activities incomplete"


# ---------------------------------------------------------------------------
# compute_fitness consistency after scheduling
# ---------------------------------------------------------------------------

class TestFitnessAfterScheduling:

    def test_fitness_consistent_with_schedule_result(self, sched_example_10):
        f = sched_example_10.compute_fitness()
        # makespan_ratio must equal last_result values
        res = sched_example_10._last_schedule_result
        expected_mr = res["scheduled_duration"] / max(res["cpm_duration"], 1.0)
        assert abs(f["makespan_ratio"] - expected_mr) < 1e-9

    def test_fitness_makespan_ratio_geq_1(self, sched_example_10):
        f = sched_example_10.compute_fitness()
        assert f["makespan_ratio"] >= 1.0 - 1e-6


# ---------------------------------------------------------------------------
# Validator consistency
# ---------------------------------------------------------------------------

class TestValidatorConsistency:
    def test_example_10_output_is_valid(self, sched_example_10):
        assert_valid_schedule(sched_example_10)

    def test_test_case_1_output_is_valid(self, sched_test_case_1):
        assert_valid_schedule(sched_test_case_1)
