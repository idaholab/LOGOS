"""
Unit tests for mobilization lead time (Challenge 13).

``mobilization_lead_hours`` on an Activity represents the advance-preparation
window required between a predecessor finishing and this activity starting
(e.g. a vendor specialist must be called and travel to site).  The value is
baked into CPM ES during generateInfo() so all downstream metrics
(slack, GRPW, priority weights) automatically account for the delay.

Tests cover:
- Activity field default, from_json parse, to_json_dict round-trip
- generateInfo forward pass: source ES shifted by lead
- generateInfo forward pass: successor ES = predecessor EF + lag + lead
- generateInfo backward pass: predecessor LF tightened by successor lead
- Slack correctly reflects lead time (critical path test)
- Zero lead: backward-compatible, no change to CPM
- Scheduler: activity cannot start before predecessor EF + lead
- Scheduler: activity completion time correct with lead
- _generate_info_from: pending source respects lead
- _generate_info_from: pending non-source predecessor EF + lead propagated
- replan: rescheduled activity respects lead after replan
- Schema: outage_schema.json contains mobilization_lead_hours field
"""

import pytest
import math
import json
from datetime import datetime, timedelta
from pathlib import Path

from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool


TOL = 1e-9

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pools():
    return ResourcePool(), EquipmentPool(), LocationPool()


def _simple_pert(a_duration=4.0, b_duration=4.0, b_lead=0.0):
    """
    START(0) -> A(a_duration) -> B(b_duration, lead=b_lead) -> END(0)
    Returns (p, a, b).
    """
    start = Activity('START', 0.0)
    a     = Activity('A', a_duration)
    b     = Activity('B', b_duration)
    b.mobilization_lead_hours = b_lead
    end   = Activity('END', 0.0)
    fwd   = {start: [a], a: [b], b: [end], end: []}
    p     = Pert(graph=fwd)
    return p, a, b


def _pooled_pert(a_duration=4.0, b_duration=4.0, b_lead=0.0):
    """Same as _simple_pert but with empty pools attached for scheduling."""
    p, a, b = _simple_pert(a_duration, b_duration, b_lead)
    rp, ep, lp = _make_pools()
    p.resource_pool  = rp
    p.equipment_pool = ep
    p.location_pool  = lp
    p.generateInfo()
    p.startTime = datetime(2025, 6, 1, 0, 0)
    return p, a, b


# ---------------------------------------------------------------------------
# Activity field
# ---------------------------------------------------------------------------

class TestActivityMobilizationField:

    def test_default_is_zero(self):
        act = Activity('T', 4.0)
        assert act.mobilization_lead_hours == 0.0

    def test_assignment_stored(self):
        act = Activity('T', 4.0)
        act.mobilization_lead_hours = 6.0
        assert act.mobilization_lead_hours == 6.0

    def test_from_json_parses_field(self):
        task = {
            'task_id': 'T1',
            'description': 'Vendor task',
            'duration': 8.0,
            'successors': [],
            'required_resources': [],
            'required_equipment': [],
            'is_hold_point': False,
            'mobilization_lead_hours': 24.0,
        }
        act = Activity.from_json(task)
        assert abs(act.mobilization_lead_hours - 24.0) < TOL

    def test_from_json_defaults_to_zero(self):
        task = {
            'task_id': 'T2',
            'description': 'Normal task',
            'duration': 4.0,
            'successors': [],
            'required_resources': [],
            'required_equipment': [],
            'is_hold_point': False,
        }
        act = Activity.from_json(task)
        assert act.mobilization_lead_hours == 0.0

    def test_to_json_dict_includes_field_when_nonzero(self):
        act = Activity('T', 4.0)
        act.mobilization_lead_hours = 12.0
        d = act.to_json_dict()
        assert 'mobilization_lead_hours' in d
        assert abs(d['mobilization_lead_hours'] - 12.0) < TOL

    def test_to_json_dict_omits_field_when_zero(self):
        act = Activity('T', 4.0)
        d = act.to_json_dict()
        assert 'mobilization_lead_hours' not in d

    def test_round_trip_via_json(self):
        task = {
            'task_id': 'T_RT',
            'description': 'Round-trip',
            'duration': 6.0,
            'successors': [],
            'required_resources': [],
            'required_equipment': [],
            'is_hold_point': False,
            'mobilization_lead_hours': 8.0,
        }
        act = Activity.from_json(task)
        d   = act.to_json_dict()
        assert abs(d['mobilization_lead_hours'] - 8.0) < TOL

    def test_reset_does_not_clear_lead(self):
        """mobilization_lead_hours is structural, reset() must not clear it."""
        act = Activity('T', 4.0)
        act.mobilization_lead_hours = 5.0
        act.reset()
        assert abs(act.mobilization_lead_hours - 5.0) < TOL


# ---------------------------------------------------------------------------
# CPM — generateInfo forward pass
# ---------------------------------------------------------------------------

class TestCPMForwardPass:

    def test_source_es_shifted_by_lead(self):
        """
        A source activity with mobilization_lead_hours=6 must have ES=6,
        even though its CPM position (no predecessors) would normally give ES=0.
        """
        start = Activity('START', 0.0)
        a     = Activity('A', 4.0)
        a.mobilization_lead_hours = 6.0
        end   = Activity('END', 0.0)
        fwd   = {start: [a], a: [end], end: []}
        p     = Pert(graph=fwd)
        assert abs(p.infoDict[a]['es'] - 6.0) < TOL
        assert abs(p.infoDict[a]['ef'] - 10.0) < TOL  # 6 + 4

    def test_successor_es_predecessor_ef_plus_lead(self):
        """
        B.ES must equal A.EF + B.mobilization_lead_hours.
        Network: A(4h) -> B(4h, lead=5h).
        Expected B.ES = 4 + 5 = 9.
        """
        p, a, b = _simple_pert(b_lead=5.0)
        assert abs(p.infoDict[b]['es'] - 9.0) < TOL
        assert abs(p.infoDict[b]['ef'] - 13.0) < TOL  # 9 + 4

    def test_zero_lead_unchanged(self):
        """Zero lead must produce identical CPM to the no-lead case."""
        p_lead, a_lead, b_lead = _simple_pert(b_lead=0.0)
        p_none, a_none, b_none = _simple_pert()
        assert abs(p_lead.infoDict[b_lead]['es'] - p_none.infoDict[b_none]['es']) < TOL
        assert abs(p_lead.infoDict[b_lead]['ef'] - p_none.infoDict[b_none]['ef']) < TOL

    def test_lead_plus_lag_both_applied(self):
        """When both a FS lag and a mobilization lead are present, both add to ES."""
        start = Activity('START', 0.0)
        a     = Activity('A', 4.0)
        b     = Activity('B', 4.0)
        b.mobilization_lead_hours = 3.0
        end   = Activity('END', 0.0)
        fwd   = {start: [a], a: [b], b: [end], end: []}
        p     = Pert(graph=fwd)
        # Manually inject a 2h lag on the A->B edge
        p.lag_dict[(a, b)] = 2.0
        p.generateInfo()
        # B.ES = A.EF + lag + lead = 4 + 2 + 3 = 9
        assert abs(p.infoDict[b]['es'] - 9.0) < TOL

    def test_project_duration_extended_by_lead(self):
        """Project duration must increase by the lead time."""
        p_no_lead, _, _ = _simple_pert(b_lead=0.0)
        p_lead,    _, _ = _simple_pert(b_lead=5.0)
        dur_no = p_no_lead.getProjectDuration()
        dur_ld = p_lead.getProjectDuration()
        assert abs(dur_ld - dur_no - 5.0) < TOL


# ---------------------------------------------------------------------------
# CPM — backward pass and slack
# ---------------------------------------------------------------------------

class TestCPMBackwardPass:

    def test_predecessor_lf_tightened_by_successor_lead(self):
        """
        A's LF must account for B's mobilization lead.
        Network: A(4) -> B(4, lead=5).  Project duration = 13.
        B.LS = 13 - 4 = 9.
        A.LF = B.LS - lag - lead = 9 - 0 - 5 = 4.
        A.LS = 4 - 4 = 0.  A.slack = 0 (on critical path).
        """
        p, a, b = _simple_pert(b_lead=5.0)
        assert abs(p.infoDict[a]['lf'] - 4.0) < TOL
        assert abs(p.infoDict[a]['ls'] - 0.0) < TOL

    def test_critical_path_activity_has_zero_slack_with_lead(self):
        """A in a chain with lead on B must have zero slack (CP member)."""
        p, a, b = _simple_pert(b_lead=5.0)
        assert abs(p.infoDict[a]['slack']) < TOL

    def test_b_slack_zero_on_critical_path(self):
        """B itself must also have zero slack."""
        p, a, b = _simple_pert(b_lead=5.0)
        assert abs(p.infoDict[b]['slack']) < TOL

    def test_non_critical_predecessor_has_positive_slack(self):
        """
        Parallel path: START -> A(2) -> END and START -> B(4, lead=5) -> END.
        A is off the critical path (CP = B path, duration 9).
        A.LF = project_duration - B.lead - ... actually A doesn't constrain B.
        A.LF = 9 (project duration), A.LS = 9-2=7, A.slack = 7-0 = 7 > 0.
        """
        start = Activity('START', 0.0)
        a     = Activity('A', 2.0)          # short parallel branch
        b     = Activity('B', 4.0)
        b.mobilization_lead_hours = 5.0     # B.ES = 5, B.EF = 9
        end   = Activity('END', 0.0)
        fwd   = {start: [a, b], a: [end], b: [end], end: []}
        p     = Pert(graph=fwd)
        # CP = START -> B -> END, duration = 9
        assert abs(p.getProjectDuration() - 9.0) < TOL
        assert p.infoDict[a]['slack'] > 0.0


# ---------------------------------------------------------------------------
# Scheduler enforcement
# ---------------------------------------------------------------------------

class TestSchedulerEnforcement:

    def test_activity_starts_after_lead_plus_predecessor_ef(self):
        """
        Scheduler must not start B before predecessor A finishes + B's lead.
        A finishes at t=4h; B.lead=5h → B cannot start before t=9h.
        """
        p, a, b = _pooled_pert(b_lead=5.0)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        b_st, _ = b.returnAbsTimes()
        b_start_hours = (b_st - p.startTime).total_seconds() / 3600.0
        assert b_start_hours >= 9.0 - TOL

    def test_schedule_completes_with_lead(self):
        """Schedule must complete all activities even with a long lead time."""
        p, a, b = _pooled_pert(b_lead=10.0)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == result['n_activities']

    def test_zero_lead_schedule_unchanged(self):
        """Zero lead must not change when activities start."""
        p, a, b = _pooled_pert(b_lead=0.0)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        a_st, _ = a.returnAbsTimes()
        b_st, _ = b.returnAbsTimes()
        a_start = (a_st - p.startTime).total_seconds() / 3600.0
        b_start = (b_st - p.startTime).total_seconds() / 3600.0
        # A starts at 0, B starts at 4 (immediately after A, no lead)
        assert a_start < TOL
        assert abs(b_start - 4.0) < TOL

    def test_lead_time_adds_to_scheduled_duration(self):
        """Scheduled duration with lead must be greater than without."""
        p_no, _, _ = _pooled_pert(b_lead=0.0)
        r_no = p_no.calculateScheduleWithResources(sgs='max_use_res_ranked')

        p_ld, _, _ = _pooled_pert(b_lead=6.0)
        r_ld = p_ld.calculateScheduleWithResources(sgs='max_use_res_ranked')

        assert r_ld['scheduled_duration'] > r_no['scheduled_duration']


# ---------------------------------------------------------------------------
# _generate_info_from with lead time
# ---------------------------------------------------------------------------

class TestGenerateInfoFromWithLead:

    def _run_and_replan(self, b_lead, replan_at):
        """Run a full schedule then replan from replan_at hours."""
        p, a, b = _pooled_pert(b_lead=b_lead)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        p._partial_reset(replan_at)
        p._generate_info_from(replan_at)
        return p, a, b

    def test_pending_source_es_max_current_and_lead(self):
        """
        If a source activity's lead has NOT yet elapsed (current < lead),
        its ES in the partial CPM must be the lead value, not current time.
        Network: chain A(4,lead=8)->B(4).  Replan at t=0.
        At t=0 everything is pending (A started at t=8 in original run, so
        if we replan at t=0 A.st > t=0 → pending).
        Wait — in the chain A starts at startTime (t=0h from outage start)
        in the original run since lead is baked into CPM ES, not actual start.

        Actually let's use a simpler test: build a fresh Pert (no prior run),
        just check _generate_info_from directly after seeding status.
        """
        start = Activity('START', 0.0)
        a     = Activity('A', 4.0)
        a.mobilization_lead_hours = 8.0   # A can't start before t=8h
        end   = Activity('END', 0.0)
        fwd   = {start: [a], a: [end], end: []}

        rp, ep, lp = _make_pools()
        p = Pert(graph=fwd)
        p.resource_pool  = rp
        p.equipment_pool = ep
        p.location_pool  = lp
        p.generateInfo()
        p.startTime = datetime(2025, 6, 1, 0, 0)

        # Run full schedule so A gets actual times
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        # A starts at t=8h (lead), finishes at t=12h.

        # Replan at t=5h: A has startTime = startTime+8h > current_abs (startTime+5h)
        # → A is pending.  Its ES must be max(5, 8) = 8.
        p._partial_reset(5.0)
        p._generate_info_from(5.0)
        assert abs(p.infoDict[a]['es'] - 8.0) < TOL

    def test_pending_non_source_es_includes_lead(self):
        """
        If B is pending and its predecessor A finishes at t=10h,
        and B has lead=3h, then B.ES must be >= 10+3=13.
        """
        p, a, b = _pooled_pert(a_duration=6.0, b_duration=4.0, b_lead=3.0)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        # A.ES=0, A.EF=6; B.ES=9(6+3), B.EF=13.
        # Replan at t=2h: A started at t=0, ends at t=6 → in_progress.
        # B is pending.
        p._partial_reset(2.0)
        p._generate_info_from(2.0)
        # A.EF in partial CPM = 2 + (6-2) = 6.  B.ES = 6 + 3 = 9.
        assert p.infoDict[b]['es'] >= 9.0 - TOL

    def test_replan_activity_starts_after_lead(self):
        """After replan(), B must not start before its predecessor's EF + lead."""
        p, a, b = _pooled_pert(a_duration=4.0, b_duration=4.0, b_lead=5.0)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        # Replan at t=2h (A in_progress, B pending)
        result = p.replan(2.0)
        assert result['n_completed'] == result['n_activities']
        b_st, _ = b.returnAbsTimes()
        b_hours = (b_st - p.startTime).total_seconds() / 3600.0
        # A finishes at 4h; B can't start until 4 + 5 = 9h.
        assert b_hours >= 9.0 - TOL


# ---------------------------------------------------------------------------
# Schema contains mobilization_lead_hours
# ---------------------------------------------------------------------------

class TestSchema:

    def test_schema_contains_mobilization_field(self):
        schema_path = Path(__file__).parent.parent / 'outage_schema.json'
        with open(schema_path) as f:
            schema = json.load(f)
        task_props = schema['properties']['tasks']['items']['properties']
        assert 'mobilization_lead_hours' in task_props

    def test_schema_field_is_nonnegative_number(self):
        schema_path = Path(__file__).parent.parent / 'outage_schema.json'
        with open(schema_path) as f:
            schema = json.load(f)
        field = schema['properties']['tasks']['items']['properties']['mobilization_lead_hours']
        assert field['type'] == 'number'
        assert field.get('minimum', -1) >= 0
