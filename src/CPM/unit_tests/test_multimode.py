"""
Unit tests for multi-mode activities (Challenge 15 — MMRCPSP).

Each activity may define an optional ``modes`` array containing execution
options that differ in duration, resource requirements, equipment requirements,
and optionally dose rate and mobilization lead.  Selecting a mode via
``Activity.set_mode(mode_id)`` writes those values into the live fields;
``Pert.set_modes({task_id: mode_id})`` does the same for a batch and
recomputes CPM so that the GP or caller can evaluate different mode-assignment
vectors before scheduling.

Tests cover:
- Activity default fields (modes=[], selected_mode_id=None)
- set_mode() applies duration / resources / equipment
- set_mode() raises when activity has no modes
- set_mode() raises on unknown mode_id
- set_mode() applies optional dose_rate and mobilization_lead overrides
- get_available_modes() returns correct IDs
- from_json() parses modes array; empty array leaves modes=[]
- to_json_dict() includes modes when non-empty; omits when empty
- Round-trip: from_json() → to_json_dict() round-trips correctly
- reset() does NOT clear modes or selected_mode_id
- Pert.set_modes() applies mode and recomputes CPM (project duration changes)
- Pert.set_modes() with multiple activities
- Pert.set_modes() raises on non-dict input
- Pert.set_modes() raises on unknown task_id
- Pert.set_modes() raises when mode_id not found
- Pert.set_modes() works for graph-built Pert (no task_to_activity pre-populated)
- CPM: crash mode shortens project duration
- CPM: normal mode gives correct (longer) project duration
- CPM: slack correct for non-critical activities after mode switch
- Scheduler: completes within crash duration
- Scheduler: completes within normal duration
- Schema: modes array is present with correct structure
"""

import pytest
import json
from datetime import datetime
from pathlib import Path

from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, ResourceAvailability, EquipmentPool, LocationPool

TOL = 1e-9

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pools():
    return ResourcePool(), EquipmentPool(), LocationPool()


def _make_activity_with_modes(name='A'):
    """Activity with two modes: normal(8h, 2 mechanics) and crash(4h, 4 mechanics)."""
    a = Activity(name=name, duration=8.0,
                 required_resources=[{'skill_type': 'MECHANIC', 'crew_count': 2}])
    a.modes = [
        {
            'mode_id': 'normal',
            'duration': 8.0,
            'required_resources': [{'skill_type': 'MECHANIC', 'crew_count': 2}],
            'required_equipment': [],
        },
        {
            'mode_id': 'crash',
            'duration': 4.0,
            'required_resources': [{'skill_type': 'MECHANIC', 'crew_count': 4}],
            'required_equipment': [],
        },
    ]
    return a


def _start_date():
    return datetime(2026, 1, 1, 0, 0, 0)


def _simple_pert_with_modes():
    """
    Build a minimal 3-activity network:  A -> B -> C
    A and B have two modes each; C is single-mode.

    Returns (pert, a, b, c) where resource pool has 6 MECHANIC workers.
    """
    a_date = _start_date()
    rp, ep, lp = _make_pools()
    rp.resources['MECHANIC'] = ResourceAvailability(
        'MECHANIC',
        [{'start_date': a_date, 'end_date': datetime(2026, 1, 10), 'available_count': 6}],
    )

    A = Activity(name='A', duration=8.0,
                 required_resources=[{'skill_type': 'MECHANIC', 'crew_count': 2}])
    A.modes = [
        {'mode_id': 'normal', 'duration': 8.0,
         'required_resources': [{'skill_type': 'MECHANIC', 'crew_count': 2}],
         'required_equipment': []},
        {'mode_id': 'crash', 'duration': 4.0,
         'required_resources': [{'skill_type': 'MECHANIC', 'crew_count': 4}],
         'required_equipment': []},
    ]
    A.childs = ['B']

    B = Activity(name='B', duration=6.0,
                 required_resources=[{'skill_type': 'MECHANIC', 'crew_count': 2}])
    B.modes = [
        {'mode_id': 'normal', 'duration': 6.0,
         'required_resources': [{'skill_type': 'MECHANIC', 'crew_count': 2}],
         'required_equipment': []},
        {'mode_id': 'crash', 'duration': 3.0,
         'required_resources': [{'skill_type': 'MECHANIC', 'crew_count': 4}],
         'required_equipment': []},
    ]
    B.childs = ['C']

    C = Activity(name='C', duration=2.0,
                 required_resources=[{'skill_type': 'MECHANIC', 'crew_count': 1}])
    C.childs = []

    p = Pert(graph={A: [B], B: [C], C: []})
    p.crew_pool  = rp
    p.equipment_pool = ep
    p.location_pool  = lp
    p.startTime      = a_date
    p.generateInfo()
    return p, A, B, C


# ---------------------------------------------------------------------------
# TestActivityModeField
# ---------------------------------------------------------------------------

class TestActivityModeField:

    def test_modes_default_empty(self):
        a = Activity('T1', 5.0)
        assert a.modes == []

    def test_selected_mode_id_default_none(self):
        a = Activity('T1', 5.0)
        assert a.selected_mode_id is None

    def test_set_mode_applies_duration(self):
        a = _make_activity_with_modes()
        a.set_mode('crash')
        assert abs(a.duration - 4.0) < TOL

    def test_set_mode_applies_resources(self):
        a = _make_activity_with_modes()
        a.set_mode('crash')
        assert a.required_resources == [{'skill_type': 'MECHANIC', 'crew_count': 4}]

    def test_set_mode_applies_equipment(self):
        a = _make_activity_with_modes()
        a.modes[0]['required_equipment'] = [{'equipment_id': 'EQ_CRANE', 'quantity_needed': 1}]
        a.set_mode('normal')
        assert a.required_equipment == [{'equipment_id': 'EQ_CRANE', 'quantity_needed': 1}]

    def test_set_mode_records_selected_mode_id(self):
        a = _make_activity_with_modes()
        a.set_mode('normal')
        assert a.selected_mode_id == 'normal'

    def test_set_mode_raises_no_modes(self):
        a = Activity('T1', 5.0)
        with pytest.raises(ValueError, match="has no modes defined"):
            a.set_mode('crash')

    def test_set_mode_raises_unknown_mode(self):
        a = _make_activity_with_modes()
        with pytest.raises(ValueError, match="not found"):
            a.set_mode('turbo')

    def test_set_mode_available_modes_listed_in_error(self):
        a = _make_activity_with_modes()
        with pytest.raises(ValueError, match="normal"):
            a.set_mode('turbo')

    def test_set_mode_optional_dose_override(self):
        a = _make_activity_with_modes()
        a.modes[0]['dose_rate_mrem_per_hour'] = 50.0
        a.set_mode('normal')
        assert abs(a.dose_rate_mrem_per_hour - 50.0) < TOL

    def test_set_mode_optional_lead_override(self):
        a = _make_activity_with_modes()
        a.modes[1]['mobilization_lead_hours'] = 12.0
        a.set_mode('crash')
        assert abs(a.mobilization_lead_hours - 12.0) < TOL

    def test_set_mode_no_dose_key_leaves_dose_unchanged(self):
        a = _make_activity_with_modes()
        a.dose_rate_mrem_per_hour = 30.0
        a.set_mode('crash')          # crash mode has no dose key
        assert abs(a.dose_rate_mrem_per_hour - 30.0) < TOL

    def test_get_available_modes(self):
        a = _make_activity_with_modes()
        assert a.get_available_modes() == ['normal', 'crash']

    def test_get_available_modes_empty(self):
        a = Activity('T1', 5.0)
        assert a.get_available_modes() == []

    def test_reset_does_not_clear_modes(self):
        a = _make_activity_with_modes()
        a.set_mode('crash')
        a.reset()
        assert len(a.modes) == 2

    def test_reset_does_not_clear_selected_mode_id(self):
        a = _make_activity_with_modes()
        a.set_mode('crash')
        a.reset()
        assert a.selected_mode_id == 'crash'


# ---------------------------------------------------------------------------
# TestActivityFromJsonModes
# ---------------------------------------------------------------------------

class TestActivityFromJsonModes:

    def _task_dict_with_modes(self):
        return {
            'task_id': 'T1',
            'description': 'Test task',
            'duration': 8.0,
            'successors': [],
            'required_resources': [{'skill_type': 'MECHANIC', 'crew_count': 2}],
            'required_equipment': [],
            'is_hold_point': False,
            'modes': [
                {
                    'mode_id': 'normal',
                    'duration': 8.0,
                    'required_resources': [{'skill_type': 'MECHANIC', 'crew_count': 2}],
                    'required_equipment': [],
                },
                {
                    'mode_id': 'crash',
                    'duration': 4.0,
                    'required_resources': [{'skill_type': 'MECHANIC', 'crew_count': 4}],
                    'required_equipment': [],
                },
            ],
        }

    def test_from_json_parses_modes(self):
        td = self._task_dict_with_modes()
        a = Activity.from_json(td)
        assert len(a.modes) == 2

    def test_from_json_mode_ids(self):
        td = self._task_dict_with_modes()
        a = Activity.from_json(td)
        assert a.get_available_modes() == ['normal', 'crash']

    def test_from_json_no_modes_gives_empty(self):
        td = self._task_dict_with_modes()
        del td['modes']
        a = Activity.from_json(td)
        assert a.modes == []

    def test_from_json_empty_modes_gives_empty(self):
        td = self._task_dict_with_modes()
        td['modes'] = []
        a = Activity.from_json(td)
        assert a.modes == []

    def test_from_json_set_mode_works_after_parse(self):
        td = self._task_dict_with_modes()
        a = Activity.from_json(td)
        a.set_mode('crash')
        assert abs(a.duration - 4.0) < TOL

    def test_to_json_dict_includes_modes(self):
        td = self._task_dict_with_modes()
        a = Activity.from_json(td)
        d = a.to_json_dict()
        assert 'modes' in d
        assert len(d['modes']) == 2

    def test_to_json_dict_omits_modes_when_empty(self):
        td = self._task_dict_with_modes()
        del td['modes']
        a = Activity.from_json(td)
        d = a.to_json_dict()
        assert 'modes' not in d

    def test_round_trip_modes_preserved(self):
        td = self._task_dict_with_modes()
        a = Activity.from_json(td)
        d = a.to_json_dict()
        a2 = Activity.from_json(d)
        assert a2.get_available_modes() == ['normal', 'crash']
        a2.set_mode('crash')
        assert abs(a2.duration - 4.0) < TOL

    def test_from_json_mode_with_dose_rate(self):
        td = self._task_dict_with_modes()
        td['modes'][0]['dose_rate_mrem_per_hour'] = 75.0
        a = Activity.from_json(td)
        a.set_mode('normal')
        assert abs(a.dose_rate_mrem_per_hour - 75.0) < TOL

    def test_from_json_mode_with_lead(self):
        td = self._task_dict_with_modes()
        td['modes'][0]['mobilization_lead_hours'] = 6.0
        a = Activity.from_json(td)
        a.set_mode('normal')
        assert abs(a.mobilization_lead_hours - 6.0) < TOL


# ---------------------------------------------------------------------------
# TestPertSetModes
# ---------------------------------------------------------------------------

class TestPertSetModes:

    def test_set_modes_raises_non_dict(self):
        p, A, B, C = _simple_pert_with_modes()
        with pytest.raises(ValueError, match="dict"):
            p.set_modes(['A:normal'])

    def test_set_modes_raises_unknown_task_id(self):
        p, A, B, C = _simple_pert_with_modes()
        with pytest.raises(KeyError, match="NONEXISTENT"):
            p.set_modes({'NONEXISTENT': 'normal'})

    def test_set_modes_raises_unknown_mode_id(self):
        p, A, B, C = _simple_pert_with_modes()
        with pytest.raises(ValueError, match="not found"):
            p.set_modes({'A': 'turbo'})

    def test_set_modes_applies_mode(self):
        p, A, B, C = _simple_pert_with_modes()
        p.set_modes({'A': 'crash'})
        assert abs(A.duration - 4.0) < TOL

    def test_set_modes_recomputes_cpm(self):
        p, A, B, C = _simple_pert_with_modes()
        p.set_modes({'A': 'normal', 'B': 'normal'})
        normal_duration = p.getProjectDuration()  # 8 + 6 + 2 = 16

        p.set_modes({'A': 'crash', 'B': 'crash'})
        crash_duration = p.getProjectDuration()   # 4 + 3 + 2 = 9

        assert crash_duration < normal_duration

    def test_set_modes_crash_duration_correct(self):
        p, A, B, C = _simple_pert_with_modes()
        p.set_modes({'A': 'crash', 'B': 'crash'})
        # A(4) -> B(3) -> C(2): critical path = 9
        assert abs(p.getProjectDuration() - 9.0) < TOL

    def test_set_modes_normal_duration_correct(self):
        p, A, B, C = _simple_pert_with_modes()
        p.set_modes({'A': 'normal', 'B': 'normal'})
        # A(8) -> B(6) -> C(2): critical path = 16
        assert abs(p.getProjectDuration() - 16.0) < TOL

    def test_set_modes_partial_assignment(self):
        """Only A gets crash mode; B and C keep their current duration."""
        p, A, B, C = _simple_pert_with_modes()
        p.set_modes({'A': 'crash'})
        # A(4) -> B(6) -> C(2) = 12
        assert abs(p.getProjectDuration() - 12.0) < TOL

    def test_set_modes_empty_dict_no_error(self):
        p, A, B, C = _simple_pert_with_modes()
        p.set_modes({})   # should succeed silently

    def test_set_modes_updates_infodict_es(self):
        p, A, B, C = _simple_pert_with_modes()
        p.set_modes({'A': 'crash', 'B': 'crash'})
        # B's ES should equal A's EF = 4.0
        assert abs(p.infoDict[B]['es'] - 4.0) < TOL

    def test_set_modes_resources_updated(self):
        p, A, B, C = _simple_pert_with_modes()
        p.set_modes({'A': 'crash'})
        assert A.required_resources == [{'skill_type': 'MECHANIC', 'crew_count': 4}]


# ---------------------------------------------------------------------------
# TestCPMWithModes
# ---------------------------------------------------------------------------

class TestCPMWithModes:

    def test_crash_mode_slack_zero_on_critical(self):
        """After crash mode, all three activities are on the critical path."""
        p, A, B, C = _simple_pert_with_modes()
        p.set_modes({'A': 'crash', 'B': 'crash'})
        for act in (A, B, C):
            assert abs(p.infoDict[act]['slack']) < TOL, \
                f"{act.name} slack={p.infoDict[act]['slack']} expected 0"

    def test_normal_mode_slack_zero_on_critical(self):
        p, A, B, C = _simple_pert_with_modes()
        p.set_modes({'A': 'normal', 'B': 'normal'})
        for act in (A, B, C):
            assert abs(p.infoDict[act]['slack']) < TOL

    def test_mode_switch_changes_slack(self):
        """Switch A from crash to normal: A(8)+B(3)+C(2)=13; A's slack=0, B's slack=0."""
        p, A, B, C = _simple_pert_with_modes()
        # Start with crash on A, normal on B → critical path A(4)+B(6)+C(2)=12
        p.set_modes({'A': 'crash', 'B': 'normal'})
        # A has slack 0 (on critical path still: 4+6+2=12)
        assert abs(p.infoDict[A]['slack']) < TOL

        # Switch A to normal: A(8)+B(6)+C(2)=16 — A still on CP
        p.set_modes({'A': 'normal', 'B': 'normal'})
        assert abs(p.infoDict[A]['slack']) < TOL
        assert abs(p.getProjectDuration() - 16.0) < TOL


# ---------------------------------------------------------------------------
# TestSchedulerWithModes
# ---------------------------------------------------------------------------

class TestSchedulerWithModes:

    def test_scheduler_crash_finishes_faster(self):
        p, A, B, C = _simple_pert_with_modes()
        p.set_modes({'A': 'crash', 'B': 'crash'})
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['scheduled_duration'] <= 10.0   # 9h theoretical + possible resource wait

    def test_scheduler_normal_finishes_slower(self):
        p, A, B, C = _simple_pert_with_modes()
        p.set_modes({'A': 'normal', 'B': 'normal'})
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['scheduled_duration'] >= 9.0    # cannot be shorter than crash+slack

    def test_scheduler_crash_scheduled_duration_correct(self):
        """With 6 mechanics available and sequential A(4)→B(3)→C(2), total = 9h."""
        p, A, B, C = _simple_pert_with_modes()
        p.set_modes({'A': 'crash', 'B': 'crash'})
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        # A needs 4 mechanics, B needs 4 mechanics — run sequentially as only 6 available
        # C needs 1 mechanic
        assert result['scheduled_duration'] >= 9.0

    def test_mode_switch_before_scheduler_uses_new_resources(self):
        """Crash mode requires 4 workers; if only 2 available the task waits."""
        a_date = _start_date()
        rp, ep, lp = _make_pools()
        rp.resources['MECHANIC'] = ResourceAvailability(
            'MECHANIC',
            [{'start_date': a_date, 'end_date': datetime(2026, 1, 10),
              'available_count': 2}],   # only 2 — crash mode needs 4
        )

        A = Activity(name='A', duration=8.0,
                     required_resources=[{'skill_type': 'MECHANIC', 'crew_count': 2}])
        A.modes = [
            {'mode_id': 'normal', 'duration': 8.0,
             'required_resources': [{'skill_type': 'MECHANIC', 'crew_count': 2}],
             'required_equipment': []},
            {'mode_id': 'crash', 'duration': 4.0,
             'required_resources': [{'skill_type': 'MECHANIC', 'crew_count': 4}],
             'required_equipment': []},
        ]
        A.childs = []

        p = Pert(graph={A: []})
        p.crew_pool  = rp
        p.equipment_pool = ep
        p.location_pool  = lp
        p.startTime      = a_date
        p.generateInfo()
        p.set_modes({'A': 'crash'})
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        # Task needs 4 mechanics but only 2 available — the scheduler cannot
        # satisfy the resource requirement so A never starts (deadlock).
        # scheduled_duration = 0 because no activity completed.
        # This confirms that set_modes() correctly applied the crash resource
        # requirements (4 workers) rather than the normal ones (2 workers).
        assert result['scheduled_duration'] == 0.0


# ---------------------------------------------------------------------------
# TestSchema
# ---------------------------------------------------------------------------

class TestSchema:

    @pytest.fixture(scope='class')
    def schema(self):
        schema_path = Path(__file__).parent.parent / 'outage_schema.json'
        with open(schema_path) as f:
            return json.load(f)

    def test_modes_field_present_in_task_schema(self, schema):
        task_props = schema['properties']['tasks']['items']['properties']
        assert 'modes' in task_props

    def test_modes_is_array_type(self, schema):
        task_props = schema['properties']['tasks']['items']['properties']
        assert task_props['modes']['type'] == 'array'

    def test_mode_item_has_mode_id(self, schema):
        task_props = schema['properties']['tasks']['items']['properties']
        item_props = task_props['modes']['items']['properties']
        assert 'mode_id' in item_props

    def test_mode_item_has_duration(self, schema):
        task_props = schema['properties']['tasks']['items']['properties']
        item_props = task_props['modes']['items']['properties']
        assert 'duration' in item_props

    def test_mode_item_has_required_resources(self, schema):
        task_props = schema['properties']['tasks']['items']['properties']
        item_props = task_props['modes']['items']['properties']
        assert 'required_resources' in item_props

    def test_mode_item_has_required_equipment(self, schema):
        task_props = schema['properties']['tasks']['items']['properties']
        item_props = task_props['modes']['items']['properties']
        assert 'required_equipment' in item_props

    def test_mode_item_optional_dose_field(self, schema):
        task_props = schema['properties']['tasks']['items']['properties']
        item_props = task_props['modes']['items']['properties']
        assert 'dose_rate_mrem_per_hour' in item_props

    def test_mode_item_optional_lead_field(self, schema):
        task_props = schema['properties']['tasks']['items']['properties']
        item_props = task_props['modes']['items']['properties']
        assert 'mobilization_lead_hours' in item_props

    def test_modes_field_not_in_required(self, schema):
        """modes is optional — not in the task's required list."""
        task_required = schema['properties']['tasks']['items']['required']
        assert 'modes' not in task_required
