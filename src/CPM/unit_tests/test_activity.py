"""
Unit tests for Activity — construction, JSON I/O, and lag parsing.
"""

import json
import pytest

from CPM.activity import Activity


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestActivityConstruction:

    def test_basic_attributes(self):
        act = Activity("T1", 8.0)
        assert act.name == "T1"
        assert act.duration == 8.0
        assert act.description == "T1"          # defaults to name
        assert act.childs == []
        assert act.resources == []
        assert act.required_resources == []
        assert act.required_equipment == []
        assert act.is_hold_point is False
        assert act.hold_point_type is None
        assert act.blocks_tasks == []
        assert act.successor_lags == {}

    def test_description_override(self):
        act = Activity("T1", 4.0, description="Reactor head removal")
        assert act.description == "Reactor head removal"

    def test_hold_point_attributes(self):
        act = Activity("HP1", 0.5, is_hold_point=True,
                       hold_point_type="NRC", blocks_tasks=["T2", "T3"])
        assert act.is_hold_point is True
        assert act.hold_point_type == "NRC"
        assert act.blocks_tasks == ["T2", "T3"]

    def test_location_stored(self):
        act = Activity("T1", 6.0, location_id="LOC_CONTAINMENT")
        assert act.getLocation() == "LOC_CONTAINMENT"

    def test_required_resources_stored(self):
        res = [{"skill_type": "MECHANIC", "crew_count": 4}]
        act = Activity("T1", 8.0, required_resources=res)
        assert act.getRequiredResources() == res

    def test_required_equipment_stored(self):
        eq = [{"equipment_id": "EQ_CRANE", "quantity_needed": 1}]
        act = Activity("T1", 8.0, required_equipment=eq)
        assert act.getRequiredEquipment() == eq


# ---------------------------------------------------------------------------
# from_json — backward-compatible (plain string successors, no lag)
# ---------------------------------------------------------------------------

class TestFromJsonNoLag:

    def test_plain_successors_parsed(self):
        d = {"task_id": "T1", "duration": 8.0, "successors": ["T2", "T3"]}
        act = Activity.from_json(d)
        assert act.childs == ["T2", "T3"]
        assert act.successor_lags == {}

    def test_empty_successors(self):
        d = {"task_id": "T1", "duration": 4.0, "successors": []}
        act = Activity.from_json(d)
        assert act.childs == []

    def test_missing_successors_key(self):
        d = {"task_id": "T1", "duration": 4.0}
        act = Activity.from_json(d)
        assert act.childs == []

    def test_hold_point_parsed(self):
        d = {
            "task_id": "HP1",
            "duration": 0.5,
            "successors": [],
            "is_hold_point": True,
            "hold_point_type": "QA",
            "blocks_tasks": ["T5"],
        }
        act = Activity.from_json(d)
        assert act.is_hold_point is True
        assert act.hold_point_type == "QA"
        assert act.blocks_tasks == ["T5"]

    def test_resources_and_equipment_parsed(self):
        d = {
            "task_id": "T1",
            "duration": 6.0,
            "successors": [],
            "required_resources": [{"skill_type": "HP_TECH", "crew_count": 2}],
            "required_equipment": [{"equipment_id": "EQ_CRANE", "quantity_needed": 1}],
        }
        act = Activity.from_json(d)
        assert act.required_resources[0]["skill_type"] == "HP_TECH"
        assert act.required_equipment[0]["equipment_id"] == "EQ_CRANE"


# ---------------------------------------------------------------------------
# from_json — lag-aware successor format
# ---------------------------------------------------------------------------

class TestFromJsonWithLag:

    def test_single_lag_successor(self):
        d = {
            "task_id": "T1",
            "duration": 4.0,
            "successors": [{"task_id": "T2", "lag_hours": 2.0}],
        }
        act = Activity.from_json(d)
        assert act.childs == ["T2"]
        assert act.successor_lags == {"T2": 2.0}

    def test_mixed_plain_and_lag_successors(self):
        d = {
            "task_id": "T1",
            "duration": 4.0,
            "successors": ["T2", {"task_id": "T3", "lag_hours": 3.5}],
        }
        act = Activity.from_json(d)
        assert act.childs == ["T2", "T3"]
        assert "T2" not in act.successor_lags   # zero lag → not stored
        assert act.successor_lags["T3"] == 3.5

    def test_zero_lag_not_stored(self):
        d = {
            "task_id": "T1",
            "duration": 4.0,
            "successors": [{"task_id": "T2", "lag_hours": 0.0}],
        }
        act = Activity.from_json(d)
        assert act.childs == ["T2"]
        assert act.successor_lags == {}

    def test_missing_lag_hours_treated_as_zero(self):
        d = {
            "task_id": "T1",
            "duration": 4.0,
            "successors": [{"task_id": "T2"}],
        }
        act = Activity.from_json(d)
        assert act.childs == ["T2"]
        assert act.successor_lags == {}

    def test_multiple_lags(self):
        d = {
            "task_id": "T1",
            "duration": 4.0,
            "successors": [
                {"task_id": "T2", "lag_hours": 1.0},
                {"task_id": "T3", "lag_hours": 4.0},
            ],
        }
        act = Activity.from_json(d)
        assert act.childs == ["T2", "T3"]
        assert act.successor_lags == {"T2": 1.0, "T3": 4.0}


# ---------------------------------------------------------------------------
# to_json_dict — round-trip serialisation
# ---------------------------------------------------------------------------

class TestToJsonDictRoundTrip:

    def test_plain_successors_round_trip(self):
        d = {"task_id": "T1", "duration": 4.0, "successors": ["T2", "T3"]}
        act = Activity.from_json(d)
        out = act.to_json_dict()
        assert out["successors"] == ["T2", "T3"]

    def test_lag_successor_round_trip(self):
        d = {
            "task_id": "T1",
            "duration": 4.0,
            "successors": ["T2", {"task_id": "T3", "lag_hours": 2.0}],
        }
        act = Activity.from_json(d)
        out = act.to_json_dict()
        assert out["successors"][0] == "T2"
        assert out["successors"][1] == {"task_id": "T3", "lag_hours": 2.0}

    def test_round_trip_is_json_serialisable(self):
        d = {
            "task_id": "T1",
            "duration": 6.0,
            "successors": ["T2", {"task_id": "T3", "lag_hours": 1.5}],
            "location_id": "LOC_A",
            "required_resources": [{"skill_type": "MECHANIC", "crew_count": 2}],
            "required_equipment": [],
            "is_hold_point": False,
            "hold_point_type": None,
            "blocks_tasks": [],
        }
        act = Activity.from_json(d)
        # Must not raise
        serialised = json.dumps(act.to_json_dict())
        back = json.loads(serialised)
        assert back["task_id"] == "T1"
        assert back["duration"] == 6.0


# ---------------------------------------------------------------------------
# Mutable state helpers
# ---------------------------------------------------------------------------

class TestActivityMutableState:

    def test_add_delay_accumulates(self):
        act = Activity("T1", 4.0)
        act.addDelay(2.0)
        act.addDelay(1.5)
        assert abs(act.delay - 3.5) < 1e-9

    def test_update_duration(self):
        act = Activity("T1", 4.0)
        act.updateDuration(10.0)
        assert act.duration == 10.0

    def test_reset_clears_times(self):
        from datetime import datetime
        act = Activity("T1", 4.0)
        act.setActualStartTime(datetime(2025, 1, 1, 6, 0))
        act.reset()
        st, et = act.returnAbsTimes()
        assert st is None
        assert et is None
        assert act.delay == 0

    def test_set_on_cp(self):
        act = Activity("T1", 4.0)
        assert act.returnCPstatus() is False
        act.setOnCP()
        assert act.returnCPstatus() is True
