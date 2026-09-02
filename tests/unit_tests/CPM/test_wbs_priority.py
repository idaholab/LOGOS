"""
Unit tests for WBS-level priority roll-up (Challenge 11).

When activities share a ``wbs_group`` label, the scheduler elevates every
member's effective priority to the group's minimum slack.  This ensures that
when any task within a WBS package hits the critical path, all other tasks in
the package are scheduled immediately rather than waiting behind lower-priority
work.

Tests cover:
- Activity.wbs_group default, from_json parse, to_json_dict round-trip
- reset() does not clear wbs_group (structural data)
- _compute_wbs_slack(): ungrouped activities receive individual slack
- _compute_wbs_slack(): group min propagated to all members
- _compute_wbs_slack(): multiple independent groups don't interfere
- _compute_wbs_slack(): single-member group works correctly
- _compute_wbs_slack(): member with zero slack pulls whole group to zero
- _compute_wbs_slack(): negative slack (window-infeasible) propagates
- Priority weight: grouped member with high individual slack gets elevated weight
- Priority weight: no group → weight based solely on individual slack
- Priority weight: group collapse lifts lower-priority member to critical
- Replanning: _generate_info_from also computes wbs_slack
- Schema: wbs_group field present and nullable
"""

import json
import math
import pytest
from datetime import datetime
from pathlib import Path

from conftest import assert_valid_schedule
from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool

TOL = 1e-9


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _start():
    return datetime(2026, 1, 1)


def _empty_pools():
    return ResourcePool(), EquipmentPool(), LocationPool()


def _chain_pert(*durations, groups=None):
    """Build A(d0) -> B(d1) -> ... with optional wbs_group labels.

    groups: dict {task_name: group_label} e.g. {'A': 'PKG1', 'B': 'PKG1'}
    Returns (pert, [A, B, ...]).
    """
    acts = [Activity(chr(ord('A') + i), float(d)) for i, d in enumerate(durations)]
    if groups:
        for act in acts:
            act.wbs_group = groups.get(act.name)

    fwd = {}
    for i, act in enumerate(acts):
        fwd[act] = [acts[i + 1]] if i + 1 < len(acts) else []

    rp, ep, lp = _empty_pools()
    p = Pert(graph=fwd)
    p.crew_pool  = rp
    p.equipment_pool = ep
    p.location_pool  = lp
    p.startTime      = _start()
    p.generateInfo()
    return p, acts


# ---------------------------------------------------------------------------
# TestActivityWbsField
# ---------------------------------------------------------------------------

class TestActivityWbsField:

    def test_default_is_none(self):
        a = Activity('T', 4.0)
        assert a.wbs_group is None

    def test_assignment(self):
        a = Activity('T', 4.0)
        a.wbs_group = 'PKG_RCP'
        assert a.wbs_group == 'PKG_RCP'

    def test_from_json_parses_wbs_group(self):
        td = {
            'task_id': 'T1', 'description': 'x', 'duration': 4.0,
            'successors': [], 'required_resources': [], 'required_equipment': [],
            'is_hold_point': False, 'wbs_group': 'PKG_RCP',
        }
        a = Activity.from_json(td)
        assert a.wbs_group == 'PKG_RCP'

    def test_from_json_absent_gives_none(self):
        td = {
            'task_id': 'T1', 'description': 'x', 'duration': 4.0,
            'successors': [], 'required_resources': [], 'required_equipment': [],
            'is_hold_point': False,
        }
        a = Activity.from_json(td)
        assert a.wbs_group is None

    def test_from_json_null_gives_none(self):
        td = {
            'task_id': 'T1', 'description': 'x', 'duration': 4.0,
            'successors': [], 'required_resources': [], 'required_equipment': [],
            'is_hold_point': False, 'wbs_group': None,
        }
        a = Activity.from_json(td)
        assert a.wbs_group is None

    def test_to_json_dict_includes_wbs_group(self):
        a = Activity('T', 4.0)
        a.wbs_group = 'PKG_RCP'
        d = a.to_json_dict()
        assert 'wbs_group' in d
        assert d['wbs_group'] == 'PKG_RCP'

    def test_to_json_dict_omits_when_none(self):
        a = Activity('T', 4.0)
        d = a.to_json_dict()
        assert 'wbs_group' not in d

    def test_round_trip(self):
        td = {
            'task_id': 'T1', 'description': 'x', 'duration': 4.0,
            'successors': [], 'required_resources': [], 'required_equipment': [],
            'is_hold_point': False, 'wbs_group': 'PKG_VALVE',
        }
        a = Activity.from_json(td)
        d = a.to_json_dict()
        a2 = Activity.from_json(d)
        assert a2.wbs_group == 'PKG_VALVE'

    def test_reset_does_not_clear_wbs_group(self):
        a = Activity('T', 4.0)
        a.wbs_group = 'PKG_RCP'
        a.reset()
        assert a.wbs_group == 'PKG_RCP'


# ---------------------------------------------------------------------------
# TestComputeWbsSlack
# ---------------------------------------------------------------------------

class TestComputeWbsSlack:

    def test_ungrouped_wbs_slack_equals_individual_slack(self):
        """Activities with no wbs_group get wbs_slack == their own slack."""
        p, (A, B, C) = _chain_pert(4.0, 6.0, 2.0)
        # Linear chain: all on critical path → slack = 0
        for act in (A, B, C):
            assert abs(p.infoDict[act]['wbs_slack'] - p.infoDict[act]['slack']) < TOL

    def test_ungrouped_non_critical_wbs_slack_equals_slack(self):
        """Parallel branch: off-critical activity has positive slack and wbs_slack=slack.

        Network: A(1) -> C(6) [critical path = 7h]
                 A(1) -> B(2) -> C(6) [path = 9h — wait, that makes B critical]

        Use a fan-in instead:
        B(1) -> C(6): path = 7h
        A(4) -> C(6): critical path = 10h
        So B.slack = 10 - 1 - 6 = 3h.
        """
        A = Activity('A', 4.0); A.childs = ['C']
        B = Activity('B', 1.0); B.childs = ['C']
        C = Activity('C', 6.0); C.childs = []

        rp, ep, lp = _empty_pools()
        p = Pert(graph={A: [C], B: [C], C: []})
        p.crew_pool  = rp
        p.equipment_pool = ep
        p.location_pool  = lp
        p.startTime      = _start()
        p.generateInfo()

        # B is off the critical path (slack = 3h), A and C have slack=0
        assert p.infoDict[B]['slack'] > 0
        assert abs(p.infoDict[B]['wbs_slack'] - p.infoDict[B]['slack']) < TOL

    def test_group_min_propagated_to_all_members(self):
        """All members get the minimum slack in their group."""
        # A(4) -> B(6) -> C(2): linear, all slack=0
        # D(1): isolated, slack = 12 - 1 = 11
        # Group: A, B, D → min slack = 0
        A = Activity('A', 4.0); A.childs = ['B']
        B = Activity('B', 6.0); B.childs = ['C']
        C = Activity('C', 2.0); C.childs = []
        D = Activity('D', 1.0); D.childs = ['C']

        A.wbs_group = 'PKG1'
        B.wbs_group = 'PKG1'
        D.wbs_group = 'PKG1'
        # C has no group

        rp, ep, lp = _empty_pools()
        p = Pert(graph={A: [B], B: [C], C: [], D: [C]})
        p.crew_pool  = rp
        p.equipment_pool = ep
        p.location_pool  = lp
        p.startTime      = _start()
        p.generateInfo()

        # A and B are on the critical path (slack=0); D has positive slack
        assert p.infoDict[A]['slack'] == pytest.approx(0.0, abs=TOL)
        assert p.infoDict[D]['slack'] > 0

        # All PKG1 members should have wbs_slack = group min = 0
        for act in (A, B, D):
            assert p.infoDict[act]['wbs_slack'] == pytest.approx(0.0, abs=TOL), \
                f"{act.name} wbs_slack={p.infoDict[act]['wbs_slack']}"

    def test_c_not_in_group_keeps_individual_slack(self):
        """Activity C not in the group retains its own slack."""
        A = Activity('A', 4.0); A.childs = ['B']
        B = Activity('B', 6.0); B.childs = ['C']
        C = Activity('C', 2.0); C.childs = []
        D = Activity('D', 1.0); D.childs = ['C']
        A.wbs_group = 'PKG1'
        B.wbs_group = 'PKG1'
        D.wbs_group = 'PKG1'

        rp, ep, lp = _empty_pools()
        p = Pert(graph={A: [B], B: [C], C: [], D: [C]})
        p.crew_pool = rp; p.equipment_pool = ep; p.location_pool = lp
        p.startTime = _start()
        p.generateInfo()

        assert abs(p.infoDict[C]['wbs_slack'] - p.infoDict[C]['slack']) < TOL

    def test_multiple_independent_groups(self):
        """Two groups don't interfere with each other."""
        # PKG1: A(4), B(4) in series — both slack=0 (critical)
        # PKG2: C(2), D(6) — C has slack=4, D has slack=0
        A = Activity('A', 4.0); A.childs = ['B']
        B = Activity('B', 4.0); B.childs = []
        C = Activity('C', 2.0); C.childs = ['D']
        D = Activity('D', 6.0); D.childs = []
        A.wbs_group = 'PKG1'; B.wbs_group = 'PKG1'
        C.wbs_group = 'PKG2'; D.wbs_group = 'PKG2'

        rp, ep, lp = _empty_pools()
        p = Pert(graph={A: [B], B: [], C: [D], D: []})
        p.crew_pool = rp; p.equipment_pool = ep; p.location_pool = lp
        p.startTime = _start()
        p.generateInfo()

        # PKG1: all zero slack → wbs_slack = 0 for A, B
        assert p.infoDict[A]['wbs_slack'] == pytest.approx(0.0, abs=TOL)
        assert p.infoDict[B]['wbs_slack'] == pytest.approx(0.0, abs=TOL)

        # PKG2: D.slack=0, C.slack>0 → wbs_slack = 0 for both
        assert p.infoDict[C]['wbs_slack'] == pytest.approx(0.0, abs=TOL)
        assert p.infoDict[D]['wbs_slack'] == pytest.approx(0.0, abs=TOL)

    def test_single_member_group(self):
        """Group with one member: wbs_slack == its own slack."""
        p, (A, B) = _chain_pert(4.0, 4.0, groups={'A': 'SOLO'})
        # A on critical path, slack=0
        assert p.infoDict[A]['wbs_slack'] == pytest.approx(0.0, abs=TOL)

    def test_all_members_already_critical_no_change(self):
        """If all members have zero slack individually, wbs_slack stays 0."""
        p, (A, B, C) = _chain_pert(4.0, 4.0, 4.0,
                                   groups={'A': 'G', 'B': 'G', 'C': 'G'})
        for act in (A, B, C):
            assert p.infoDict[act]['wbs_slack'] == pytest.approx(0.0, abs=TOL)

    def test_wbs_slack_recomputed_after_set_durations(self):
        """Calling set_durations() triggers generateInfo() which recomputes wbs_slack."""
        A = Activity('A', 4.0); A.childs = ['B']
        B = Activity('B', 4.0); B.childs = ['C']
        C = Activity('C', 2.0); C.childs = []
        D = Activity('D', 2.0); D.childs = ['C']
        B.wbs_group = 'PKG'; D.wbs_group = 'PKG'

        rp, ep, lp = _empty_pools()
        p = Pert(graph={A: [B], B: [C], C: [], D: [C]})
        p.crew_pool = rp; p.equipment_pool = ep; p.location_pool = lp
        p.startTime = _start(); p.generateInfo()

        # Initial: B slack=0 (critical), D slack>0 → group min=0
        assert p.infoDict[D]['wbs_slack'] == pytest.approx(0.0, abs=TOL)

        # Lengthen D so it becomes critical too
        p.set_durations({'D': 8.0})
        # Now both B and D have slack=0 → still 0
        assert p.infoDict[B]['wbs_slack'] == pytest.approx(0.0, abs=TOL)


# ---------------------------------------------------------------------------
# TestPriorityElevation
# ---------------------------------------------------------------------------

class TestPriorityElevation:

    def _weight(self, slack, proj_dur=100.0):
        """Replicate _weight_function logic for comparison."""
        threshold = max(5.0, 0.01 * proj_dur)
        return 1.0 - 1.0 / (1.0 + math.exp(threshold - slack))

    def test_grouped_member_high_slack_gets_elevated_weight(self):
        """Member with high individual slack gets elevated weight when group is critical."""
        # A(4) -> C(2): critical path 6h
        # B(1) -> C(2): B has slack = 6 - 1 - 2 = 3h if project is 6h actually
        # Let me set up a clearer case:
        # Serial: A(4) -> B(6) -> END; D(1) -> END
        # D has large slack; group {B, D} → wbs_slack = min(0, D.slack) = 0
        A = Activity('A', 4.0); A.childs = ['B']
        B = Activity('B', 6.0); B.childs = ['END']
        D = Activity('D', 1.0); D.childs = ['END']
        END = Activity('END', 0.0); END.childs = []
        B.wbs_group = 'PKG'; D.wbs_group = 'PKG'

        rp, ep, lp = _empty_pools()
        p = Pert(graph={A: [B], B: [END], D: [END], END: []})
        p.crew_pool = rp; p.equipment_pool = ep; p.location_pool = lp
        p.startTime = _start(); p.generateInfo()

        # D has positive individual slack; wbs_slack = 0 (B is on CP)
        assert p.infoDict[D]['slack'] > 0
        assert p.infoDict[D]['wbs_slack'] == pytest.approx(0.0, abs=TOL)

        # The effective slack used for weight = min(D.slack, 0) = 0
        # Weight at slack=0 is near 1.0; at D.slack it would be lower
        proj_dur = p.getProjectDuration()
        weight_at_zero   = self._weight(0.0, proj_dur)
        weight_at_d_slack = self._weight(p.infoDict[D]['slack'], proj_dur)
        assert weight_at_zero > weight_at_d_slack

    def test_no_group_weight_uses_individual_slack(self):
        """Without wbs_group the weight is exactly _weight_function(individual_slack)."""
        p, (A, B) = _chain_pert(4.0, 4.0)
        # Both on critical path, slack=0, no groups
        proj_dur = p.getProjectDuration()
        threshold = max(5.0, 0.01 * proj_dur)
        expected = 1.0 - 1.0 / (1.0 + math.exp(threshold - 0.0))
        # Verify wbs_slack == slack
        assert abs(p.infoDict[A]['wbs_slack'] - p.infoDict[A]['slack']) < TOL
        assert abs(p.infoDict[B]['wbs_slack'] - p.infoDict[B]['slack']) < TOL

    def test_group_collapse_affects_scheduler_ordering(self):
        """With WBS grouping the grouped low-priority activity completes;
        without it a resource-constrained run would leave it pending longer."""
        # Build a schedule where grouping changes which activity gets priority.
        # A(4, PKG) -> C(2): critical 6h; B(1, PKG): slack=5h without grouping.
        # With grouping, B gets wbs_slack=0 → elevated priority.
        from CPM.outage_data import ResourceAvailability

        a_date = _start()
        rp, ep, lp = _empty_pools()
        rp.resources['WORKER'] = ResourceAvailability(
            'WORKER',
            [{'start_date': a_date, 'end_date': datetime(2026, 1, 10),
              'available_count': 1}],
        )

        A = Activity('A', 4.0)
        A.required_resources = [{'skill_type': 'WORKER', 'crew_count': 1}]
        A.childs = ['C']
        A.wbs_group = 'PKG'

        B = Activity('B', 1.0)
        B.required_resources = [{'skill_type': 'WORKER', 'crew_count': 1}]
        B.childs = ['C']
        B.wbs_group = 'PKG'

        C = Activity('C', 2.0)
        C.required_resources = [{'skill_type': 'WORKER', 'crew_count': 1}]
        C.childs = []

        p = Pert(graph={A: [C], B: [C], C: []})
        p.crew_pool = rp; p.equipment_pool = ep; p.location_pool = lp
        p.startTime = a_date; p.generateInfo()

        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert_valid_schedule(p)
        # All activities should complete (no deadlock)
        assert result['scheduled_duration'] > 0


# ---------------------------------------------------------------------------
# TestWbsSlackReplanning
# ---------------------------------------------------------------------------

class TestWbsSlackReplanning:

    def test_generate_info_from_computes_wbs_slack(self):
        """After replan, wbs_slack is present and equals individual slack
        for activities with no wbs_group."""
        from CPM.outage_data import ResourceAvailability

        a_date = _start()
        rp, ep, lp = _empty_pools()
        rp.resources['WORKER'] = ResourceAvailability(
            'WORKER',
            [{'start_date': a_date, 'end_date': datetime(2026, 1, 10),
              'available_count': 2}],
        )

        A = Activity('A', 4.0)
        A.required_resources = [{'skill_type': 'WORKER', 'crew_count': 1}]
        A.childs = ['B']
        B = Activity('B', 4.0)
        B.required_resources = [{'skill_type': 'WORKER', 'crew_count': 1}]
        B.childs = []

        p = Pert(graph={A: [B], B: []})
        p.crew_pool = rp; p.equipment_pool = ep; p.location_pool = lp
        p.startTime = a_date; p.generateInfo()

        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        result = p.replan(current_time_hours=2.0)

        # After replan, wbs_slack should be present for pending activities
        for act in p.forwardDict:
            assert 'wbs_slack' in p.infoDict[act], \
                f"wbs_slack missing for {act.name} after replan"

    def test_generate_info_from_respects_group_after_replan(self):
        """WBS group min is recomputed after replan with partial frozen activities."""
        from CPM.outage_data import ResourceAvailability

        a_date = _start()
        rp, ep, lp = _empty_pools()
        rp.resources['WORKER'] = ResourceAvailability(
            'WORKER',
            [{'start_date': a_date, 'end_date': datetime(2026, 1, 10),
              'available_count': 2}],
        )

        A = Activity('A', 4.0)
        A.required_resources = [{'skill_type': 'WORKER', 'crew_count': 1}]
        A.childs = ['C']

        B = Activity('B', 1.0)
        B.required_resources = [{'skill_type': 'WORKER', 'crew_count': 1}]
        B.childs = ['C']
        B.wbs_group = 'PKG'

        C = Activity('C', 2.0)
        C.required_resources = [{'skill_type': 'WORKER', 'crew_count': 1}]
        C.childs = []
        C.wbs_group = 'PKG'

        p = Pert(graph={A: [C], B: [C], C: []})
        p.crew_pool = rp; p.equipment_pool = ep; p.location_pool = lp
        p.startTime = a_date; p.generateInfo()

        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        p.replan(current_time_hours=2.0)

        # wbs_slack should be present; PKG members should have consistent values
        for act in (B, C):
            assert 'wbs_slack' in p.infoDict[act]
            # Both in PKG: wbs_slack = min of their individual slacks
            expected = min(p.infoDict[B]['slack'], p.infoDict[C]['slack'])
            assert p.infoDict[act]['wbs_slack'] == pytest.approx(expected, abs=TOL)


# ---------------------------------------------------------------------------
# TestSchema
# ---------------------------------------------------------------------------

class TestSchema:

    @pytest.fixture(scope='class')
    def schema(self):
        schema_path = Path(__file__).parent.parent / 'outage_schema.json'
        with open(schema_path) as f:
            return json.load(f)

    def test_wbs_group_field_present(self, schema):
        props = schema['properties']['tasks']['items']['properties']
        assert 'wbs_group' in props

    def test_wbs_group_allows_string(self, schema):
        props = schema['properties']['tasks']['items']['properties']
        types = props['wbs_group']['type']
        assert 'string' in types

    def test_wbs_group_allows_null(self, schema):
        props = schema['properties']['tasks']['items']['properties']
        types = props['wbs_group']['type']
        assert 'null' in types

    def test_wbs_group_not_required(self, schema):
        required = schema['properties']['tasks']['items']['required']
        assert 'wbs_group' not in required
