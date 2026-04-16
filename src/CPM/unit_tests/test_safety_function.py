"""
Unit tests for Safety Function Mutual Exclusion — Option A.

Coverage:
- validate_data_consistency: intra-activity state conflict detection
- Intra-activity duplicate system_id with same state (no error — valid)
- Intra-activity duplicate system_id with different states (error)
- Multiple conflicting entries for the same system in one task
- Conflict in one task does not suppress unrelated errors in another task
- No required_system_states field — no errors
- Empty required_system_states — no errors
- outage_schema.json: safety_functions array is present and well-formed
- Encoding demonstration: ECCS Train A / Train B via SystemStatePool enforces
  mutual exclusion with zero new scheduling code
"""

import json
import os
import pytest
from datetime import datetime

from CPM.outage_data import (
    OutageData,
    ResourcePool, ResourceAvailability,
    EquipmentPool,
    LocationPool,
    ConsumablePool,
    SystemStatePool,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_START = datetime(2026, 1, 1)
_END   = datetime(2026, 12, 31)


def _minimal_outage(**kwargs):
    """Return an OutageData with one dummy task and minimal pools."""
    tasks = kwargs.pop('tasks', [
        {
            'task_id': 'T1',
            'duration': 4.0,
            'required_system_states': [],
        }
    ])
    ssp = kwargs.pop('system_state_pool', SystemStatePool())

    rp = ResourcePool()
    ep = EquipmentPool()
    lp = LocationPool()

    cfg = {
        'outage_id': 'TEST',
        'start_date': '2026-01-01',
        'working_hours_per_day': 24,
    }
    return OutageData(cfg, tasks, rp, ep, lp, system_state_pool=ssp)


def _eccs_pool():
    """SystemStatePool encoding ECCS Train A / Train B mutual exclusion."""
    return SystemStatePool.from_json([
        {
            'system_id': 'ECCS_SAFETY_FUNCTION',
            'description': 'Emergency Core Cooling System',
            'valid_states': ['TRAIN_A_OOS', 'TRAIN_B_OOS'],
        }
    ])


# ===========================================================================
# Intra-activity system state conflict — validate_data_consistency
# ===========================================================================

class TestIntraActivityStateConflict:

    def test_no_system_states_no_errors(self):
        """Task with no required_system_states field passes cleanly."""
        od = _minimal_outage(tasks=[{'task_id': 'T1', 'duration': 4.0}])
        valid, errors = od.validate_data_consistency()
        assert valid
        assert errors == []

    def test_empty_system_states_no_errors(self):
        """Task with empty required_system_states list passes cleanly."""
        od = _minimal_outage(tasks=[
            {'task_id': 'T1', 'duration': 4.0, 'required_system_states': []}
        ])
        valid, errors = od.validate_data_consistency()
        assert valid
        assert errors == []

    def test_single_state_no_errors(self):
        """Task declaring one system state is valid."""
        ssp = _eccs_pool()
        od = _minimal_outage(
            tasks=[{
                'task_id': 'T1',
                'duration': 4.0,
                'required_system_states': [
                    {'system_id': 'ECCS_SAFETY_FUNCTION', 'required_state': 'TRAIN_A_OOS'}
                ],
            }],
            system_state_pool=ssp,
        )
        valid, errors = od.validate_data_consistency()
        assert valid, errors

    def test_same_system_same_state_twice_no_error(self):
        """Duplicate entry for same system + same state is redundant but valid."""
        ssp = _eccs_pool()
        od = _minimal_outage(
            tasks=[{
                'task_id': 'T1',
                'required_system_states': [
                    {'system_id': 'ECCS_SAFETY_FUNCTION', 'required_state': 'TRAIN_A_OOS'},
                    {'system_id': 'ECCS_SAFETY_FUNCTION', 'required_state': 'TRAIN_A_OOS'},
                ],
            }],
            system_state_pool=ssp,
        )
        valid, errors = od.validate_data_consistency()
        conflict_errors = [e for e in errors if 'conflicting states' in e]
        assert conflict_errors == []

    def test_same_system_different_states_raises_error(self):
        """Task claiming TRAIN_A_OOS and TRAIN_B_OOS simultaneously is impossible."""
        ssp = _eccs_pool()
        od = _minimal_outage(
            tasks=[{
                'task_id': 'T_BAD',
                'required_system_states': [
                    {'system_id': 'ECCS_SAFETY_FUNCTION', 'required_state': 'TRAIN_A_OOS'},
                    {'system_id': 'ECCS_SAFETY_FUNCTION', 'required_state': 'TRAIN_B_OOS'},
                ],
            }],
            system_state_pool=ssp,
        )
        valid, errors = od.validate_data_consistency()
        assert not valid
        assert any('T_BAD' in e and 'conflicting states' in e for e in errors)

    def test_error_message_names_both_states(self):
        """Error message must mention both conflicting state names."""
        ssp = _eccs_pool()
        od = _minimal_outage(
            tasks=[{
                'task_id': 'BAD',
                'required_system_states': [
                    {'system_id': 'ECCS_SAFETY_FUNCTION', 'required_state': 'TRAIN_A_OOS'},
                    {'system_id': 'ECCS_SAFETY_FUNCTION', 'required_state': 'TRAIN_B_OOS'},
                ],
            }],
            system_state_pool=ssp,
        )
        _, errors = od.validate_data_consistency()
        conflict = next(e for e in errors if 'conflicting states' in e)
        assert 'TRAIN_A_OOS' in conflict
        assert 'TRAIN_B_OOS' in conflict
        assert 'ECCS_SAFETY_FUNCTION' in conflict

    def test_conflict_in_one_task_does_not_suppress_other_tasks(self):
        """A conflict in T1 does not hide an error in T2 (full scan)."""
        ssp = SystemStatePool.from_json([
            {
                'system_id': 'SYS_A',
                'description': 'System A',
                'valid_states': ['ON', 'OFF'],
            }
        ])
        od = _minimal_outage(
            tasks=[
                {
                    'task_id': 'T1',
                    'required_system_states': [
                        {'system_id': 'SYS_A', 'required_state': 'ON'},
                        {'system_id': 'SYS_A', 'required_state': 'OFF'},
                    ],
                },
                {
                    'task_id': 'T2',
                    'required_system_states': [
                        {'system_id': 'SYS_A', 'required_state': 'ON'},
                        {'system_id': 'SYS_A', 'required_state': 'OFF'},
                    ],
                },
            ],
            system_state_pool=ssp,
        )
        _, errors = od.validate_data_consistency()
        t1_conflict = [e for e in errors if 'T1' in e and 'conflicting' in e]
        t2_conflict = [e for e in errors if 'T2' in e and 'conflicting' in e]
        assert t1_conflict
        assert t2_conflict

    def test_three_entries_first_two_conflict(self):
        """With three entries, A/B conflict is caught on the second entry."""
        ssp = _eccs_pool()
        od = _minimal_outage(
            tasks=[{
                'task_id': 'T3',
                'required_system_states': [
                    {'system_id': 'ECCS_SAFETY_FUNCTION', 'required_state': 'TRAIN_A_OOS'},
                    {'system_id': 'ECCS_SAFETY_FUNCTION', 'required_state': 'TRAIN_B_OOS'},
                    # third entry — same conflict already recorded
                    {'system_id': 'ECCS_SAFETY_FUNCTION', 'required_state': 'TRAIN_A_OOS'},
                ],
            }],
            system_state_pool=ssp,
        )
        _, errors = od.validate_data_consistency()
        assert any('conflicting states' in e and 'T3' in e for e in errors)

    def test_different_systems_no_conflict(self):
        """Different system_ids in same task do not conflict."""
        ssp = SystemStatePool.from_json([
            {'system_id': 'SYS_X', 'description': 'X', 'valid_states': ['OPEN', 'CLOSED']},
            {'system_id': 'SYS_Y', 'description': 'Y', 'valid_states': ['OPEN', 'CLOSED']},
        ])
        od = _minimal_outage(
            tasks=[{
                'task_id': 'T_OK',
                'required_system_states': [
                    {'system_id': 'SYS_X', 'required_state': 'OPEN'},
                    {'system_id': 'SYS_Y', 'required_state': 'CLOSED'},
                ],
            }],
            system_state_pool=ssp,
        )
        valid, errors = od.validate_data_consistency()
        assert not any('conflicting states' in e for e in errors)


# ===========================================================================
# Safety function encoding via SystemStatePool — scheduling behaviour
# ===========================================================================

class TestSafetyFunctionEncoding:
    """
    Demonstrate that ECCS Train A / Train B mutual exclusion works with the
    Option A encoding using SystemStatePool and zero new scheduling code.
    """

    def test_same_train_activities_can_coexist(self):
        """Two activities both requiring TRAIN_A_OOS can hold the lock together."""
        ssp = _eccs_pool()
        # Both activities want TRAIN_A_OOS — fits() should return True for both
        assert ssp.fits('ECCS_SAFETY_FUNCTION', 'TRAIN_A_OOS')
        ssp.acquire('ECCS_SAFETY_FUNCTION', 'TRAIN_A_OOS')
        assert ssp.fits('ECCS_SAFETY_FUNCTION', 'TRAIN_A_OOS')

    def test_different_trains_are_mutually_exclusive(self):
        """Train A locked → Train B is blocked."""
        ssp = _eccs_pool()
        ssp.acquire('ECCS_SAFETY_FUNCTION', 'TRAIN_A_OOS')
        assert not ssp.fits('ECCS_SAFETY_FUNCTION', 'TRAIN_B_OOS')

    def test_train_released_allows_other_train(self):
        """Releasing Train A allows Train B to proceed."""
        ssp = _eccs_pool()
        ssp.acquire('ECCS_SAFETY_FUNCTION', 'TRAIN_A_OOS')
        ssp.release('ECCS_SAFETY_FUNCTION', 'TRAIN_A_OOS')
        assert ssp.fits('ECCS_SAFETY_FUNCTION', 'TRAIN_B_OOS')

    def test_multiple_activities_same_train_reference_counted(self):
        """Two activities on Train A — must release twice before Train B can run."""
        ssp = _eccs_pool()
        ssp.acquire('ECCS_SAFETY_FUNCTION', 'TRAIN_A_OOS')
        ssp.acquire('ECCS_SAFETY_FUNCTION', 'TRAIN_A_OOS')
        ssp.release('ECCS_SAFETY_FUNCTION', 'TRAIN_A_OOS')
        # Still one holder left — Train B still blocked
        assert not ssp.fits('ECCS_SAFETY_FUNCTION', 'TRAIN_B_OOS')
        ssp.release('ECCS_SAFETY_FUNCTION', 'TRAIN_A_OOS')
        # Now free
        assert ssp.fits('ECCS_SAFETY_FUNCTION', 'TRAIN_B_OOS')


# ===========================================================================
# outage_schema.json — safety_functions array presence
# ===========================================================================

class TestSafetyFunctionsSchema:

    @pytest.fixture(scope='class')
    def schema(self):
        schema_path = os.path.join(
            os.path.dirname(__file__), '..', 'outage_schema.json'
        )
        with open(schema_path) as f:
            return json.load(f)

    def test_safety_functions_key_present(self, schema):
        assert 'safety_functions' in schema['properties']

    def test_safety_functions_is_array(self, schema):
        sf = schema['properties']['safety_functions']
        assert sf['type'] == 'array'

    def test_safety_functions_item_required_fields(self, schema):
        item = schema['properties']['safety_functions']['items']
        required = set(item['required'])
        assert {'safety_function_id', 'description', 'train_ids', 'plant_system_id'} <= required

    def test_safety_functions_train_ids_min_items(self, schema):
        item = schema['properties']['safety_functions']['items']
        assert item['properties']['train_ids']['minItems'] == 2

    def test_safety_functions_max_trains_oos_optional(self, schema):
        item = schema['properties']['safety_functions']['items']
        assert 'max_trains_oos_simultaneously' in item['properties']
        assert 'max_trains_oos_simultaneously' not in item['required']

    def test_safety_functions_not_in_top_level_required(self, schema):
        """safety_functions is optional (many outages won't declare it)."""
        assert 'safety_functions' not in schema.get('required', [])
