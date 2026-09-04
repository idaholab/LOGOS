"""
Unit tests for CPM calculations: ES, EF, LS, LF, slack, critical path,
lag relationships, and isolated activity handling.

All expected values are derived by hand from the micro-networks defined in
conftest.py so they can be verified without running the scheduler.
"""

import math
import pytest

from CPM.activity import Activity
from CPM.pert import Pert
from conftest import make_chain_pert, make_fork_join_pert, make_lag_pert


TOL = 1e-9


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def info(pert, act):
    return pert.infoDict[act]


# ---------------------------------------------------------------------------
# Linear chain — all activities on the critical path
# ---------------------------------------------------------------------------

class TestChainCPM:
    """
    START(0) -> A(4) -> B(3) -> C(2) -> END(0)
    Project duration = 9 h.  All slack = 0.
    """

    def test_project_duration(self, chain_pert):
        p, *_ = chain_pert
        assert abs(p.getProjectDuration() - 9.0) < TOL

    def test_early_start_finish(self, chain_pert):
        p, start, a, b, c, end = chain_pert
        assert abs(info(p, a)["es"] - 0.0) < TOL
        assert abs(info(p, a)["ef"] - 4.0) < TOL
        assert abs(info(p, b)["es"] - 4.0) < TOL
        assert abs(info(p, b)["ef"] - 7.0) < TOL
        assert abs(info(p, c)["es"] - 7.0) < TOL
        assert abs(info(p, c)["ef"] - 9.0) < TOL

    def test_late_start_finish(self, chain_pert):
        p, start, a, b, c, end = chain_pert
        assert abs(info(p, a)["ls"] - 0.0) < TOL
        assert abs(info(p, a)["lf"] - 4.0) < TOL
        assert abs(info(p, b)["ls"] - 4.0) < TOL
        assert abs(info(p, b)["lf"] - 7.0) < TOL
        assert abs(info(p, c)["ls"] - 7.0) < TOL
        assert abs(info(p, c)["lf"] - 9.0) < TOL

    def test_zero_slack(self, chain_pert):
        p, start, a, b, c, end = chain_pert
        for act in (a, b, c):
            assert abs(info(p, act)["slack"]) < TOL

    def test_critical_path_contains_all_real_activities(self, chain_pert):
        p, start, a, b, c, end = chain_pert
        cp = p.getCriticalPath()
        # Critical path list should include A, B, C (may or may not include START/END)
        real_cp_names = {act.name for act in cp}
        for name in ("A", "B", "C"):
            assert name in real_cp_names, f"{name} missing from critical path"


# ---------------------------------------------------------------------------
# Fork-join — one critical and one near-critical path
# ---------------------------------------------------------------------------

class TestForkJoinCPM:
    """
    START(0) -> A(4) -> C(2) -> END(0)
                B(6) ───────^
    Project duration = 8 h.
    B is critical (slack=0); A has slack=2.
    """

    def test_project_duration(self, fork_join_pert):
        p, *_ = fork_join_pert
        assert abs(p.getProjectDuration() - 8.0) < TOL

    def test_join_node_uses_maximum_predecessor_ef(self, fork_join_pert):
        p, start, a, b, c, end = fork_join_pert
        # C's ES must come from the longer predecessor (B.ef=6, not A.ef=4)
        assert abs(info(p, c)["es"] - 6.0) < TOL
        assert abs(info(p, c)["ef"] - 8.0) < TOL

    def test_near_critical_activity_has_positive_slack(self, fork_join_pert):
        p, start, a, b, c, end = fork_join_pert
        assert abs(info(p, a)["slack"] - 2.0) < TOL

    def test_critical_activity_has_zero_slack(self, fork_join_pert):
        p, start, a, b, c, end = fork_join_pert
        assert abs(info(p, b)["slack"]) < TOL
        assert abs(info(p, c)["slack"]) < TOL

    def test_critical_path_contains_critical_activities(self, fork_join_pert):
        p, start, a, b, c, end = fork_join_pert
        cp = p.getCriticalPath()
        cp_names = {act.name for act in cp}
        assert "B" in cp_names
        assert "C" in cp_names

    def test_near_critical_not_on_critical_path(self, fork_join_pert):
        p, start, a, b, c, end = fork_join_pert
        cp = p.getCriticalPath()
        cp_names = {act.name for act in cp}
        assert "A" not in cp_names


# ---------------------------------------------------------------------------
# Lag relationships
# ---------------------------------------------------------------------------

class TestLagCPM:
    """
    START(0) -> A(4) --[lag=2h]--> B(3) -> END(0)
    Without lag: project duration = 7 h (A=4, B=3).
    With lag=2:  project duration = 9 h (B.ES = A.EF + lag = 6).
    """

    def test_lag_extends_successor_es(self, lag_pert):
        p, start, a, b, end = lag_pert
        # B must not start before A finishes + lag
        assert abs(info(p, b)["es"] - 6.0) < TOL

    def test_lag_extends_project_duration(self, lag_pert):
        p, start, a, b, end = lag_pert
        assert abs(p.getProjectDuration() - 9.0) < TOL

    def test_predecessor_ef_unaffected_by_lag(self, lag_pert):
        p, start, a, b, end = lag_pert
        assert abs(info(p, a)["ef"] - 4.0) < TOL

    def test_zero_lag_identical_to_no_lag(self):
        # Build two identical A->B chains — one with lag_dict entry of 0, one empty.
        # Both must produce the same project duration and B.es.
        p_with_zero, start, a, b, end = make_lag_pert(lag_ab=0.0)

        a2 = Activity("A", 4.0)
        b2 = Activity("B", 3.0)
        start2 = Activity("START", 0.0)
        end2 = Activity("END", 0.0)
        fwd2 = {start2: [a2], a2: [b2], b2: [end2], end2: []}
        p_no_lag = Pert(graph=fwd2)
        # lag_dict is empty by default (no lag)

        assert abs(p_no_lag.getProjectDuration() - p_with_zero.getProjectDuration()) < TOL
        assert abs(p_no_lag.infoDict[b2]["es"] - p_with_zero.infoDict[b]["es"]) < TOL

    def test_lag_propagates_through_backward_pass(self, lag_pert):
        p, start, a, b, end = lag_pert
        # A.LF = B.LS - lag = 6 - 2 = 4
        assert abs(info(p, a)["lf"] - 4.0) < TOL

    def test_lag_preserves_zero_slack_on_chain(self, lag_pert):
        p, start, a, b, end = lag_pert
        assert abs(info(p, a)["slack"]) < TOL
        assert abs(info(p, b)["slack"]) < TOL


# ---------------------------------------------------------------------------
# Isolated activity
# ---------------------------------------------------------------------------

class TestIsolatedActivity:
    """An activity with no successors and no predecessors is 'isolated'."""

    def test_isolated_activity_gets_finite_lf(self):
        start = Activity("START", 0.0)
        end = Activity("END", 0.0)
        isolated = Activity("ISO", 5.0)   # no connections
        connected = Activity("C1", 3.0)

        fwd = {start: [connected], connected: [end], end: [], isolated: []}
        p = Pert(graph=fwd)

        assert math.isfinite(info(p, isolated)["lf"])
        assert info(p, isolated)["ef"] > 0.0


# ---------------------------------------------------------------------------
# Reset and re-run consistency
# ---------------------------------------------------------------------------

class TestResetConsistency:

    def test_generateinfo_idempotent(self):
        p, start, a, b, c, end = make_chain_pert()
        dur_before = p.getProjectDuration()
        p.generateInfo()    # second call
        assert abs(p.getProjectDuration() - dur_before) < TOL

    def test_set_durations_updates_project_duration(self):
        from conftest import SCHEMA_PATH, EXAMPLES_DIR
        p = Pert.from_json_file(
            str(EXAMPLES_DIR / "test_case_1.json"),
            SCHEMA_PATH,
        )
        orig_dur = p.getProjectDuration()
        # Find the first real task (not START/END) and double its duration
        real_id = next(
            t["task_id"] for t in p.outage_data.tasks
            if t["task_id"] not in ("START", "END")
        )
        orig_task_dur = next(
            t["duration"] for t in p.outage_data.tasks if t["task_id"] == real_id
        )
        p.set_durations({real_id: orig_task_dur * 2})
        # Project duration must be >= original (adding duration can only stretch CP)
        assert p.getProjectDuration() >= orig_dur - TOL

    def test_add_activity_nxgraph_updated(self):
        p, start, a, b, c, end = make_chain_pert()
        new_act = Activity("NEW", 5.0)
        p.addActivity(new_act, inConnections=[start], outConnections=[end])
        assert new_act in p.nxgraph.nodes

    def test_add_activity_infodict_has_all_keys(self):
        p, start, a, b, c, end = make_chain_pert()
        new_act = Activity("NEW", 5.0)
        p.addActivity(new_act, inConnections=[start], outConnections=[end])
        # New activity's infoDict keys must match an established activity's keys
        assert set(p.infoDict[new_act].keys()) == set(p.infoDict[a].keys())


# ---------------------------------------------------------------------------
# gp_rules zero-division guard
# ---------------------------------------------------------------------------

class TestGPRulesEdgeCases:

    def test_trivial_two_node_graph_no_zero_division(self):
        """Single real activity between START and END must not raise ZeroDivisionError."""
        start = Activity("START", 0.0)
        task = Activity("ONLY", 4.0)
        end = Activity("END", 0.0)
        fwd = {start: [task], task: [end], end: []}
        p = Pert(graph=fwd)   # construction calls generateInfo → calculate_gp_rules
        assert abs(p.getProjectDuration() - 4.0) < TOL
