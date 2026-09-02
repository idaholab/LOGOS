"""
Unit tests for shift-calendar enforcement.

Tests cover:
- _is_work_time()  — point-in-time queries for various shift configs
- _next_shift_start_after() — snap-forward logic
- _shift_boundary_events() — event seeding
- Integration: candidate selection blocked during off-shift hours
"""

import pytest
from datetime import datetime, timedelta

from CPM.activity import Activity
from CPM.pert import Pert


# ---------------------------------------------------------------------------
# Helper: bare Pert instance with only shift attributes set
# ---------------------------------------------------------------------------

def shift_pert(hours_per_day: float, start_hour: int = 6) -> Pert:
    """Return a minimal Pert configured for shift-calendar tests."""
    p = Pert.__new__(Pert)
    p.forwardDict = {}
    p.working_hours_per_day = hours_per_day
    p.shift_start_hour = start_hour
    return p


# ---------------------------------------------------------------------------
# _is_work_time
# ---------------------------------------------------------------------------

class TestIsWorkTime:

    def test_24_7_always_open(self):
        p = shift_pert(24, start_hour=0)
        for h in range(24):
            t = datetime(2025, 1, 1, h, 0)
            assert p._is_work_time(t), f"Hour {h} should be open for 24/7 schedule"

    def test_12h_shift_start_6_inside(self):
        p = shift_pert(12, start_hour=6)
        for h in range(6, 18):
            assert p._is_work_time(datetime(2025, 1, 1, h, 30))

    def test_12h_shift_start_6_outside(self):
        p = shift_pert(12, start_hour=6)
        for h in list(range(0, 6)) + list(range(18, 24)):
            assert not p._is_work_time(datetime(2025, 1, 1, h, 0))

    def test_8h_shift_start_8(self):
        p = shift_pert(8, start_hour=8)
        assert p._is_work_time(datetime(2025, 1, 1, 8, 0))
        assert p._is_work_time(datetime(2025, 1, 1, 15, 59))
        assert not p._is_work_time(datetime(2025, 1, 1, 16, 0))
        assert not p._is_work_time(datetime(2025, 1, 1, 7, 59))

    def test_shift_crossing_midnight(self):
        # Shift 22:00 – 06:00 (8 h crossing midnight)
        p = shift_pert(8, start_hour=22)
        assert p._is_work_time(datetime(2025, 1, 1, 23, 0))
        assert p._is_work_time(datetime(2025, 1, 2, 3, 0))
        assert not p._is_work_time(datetime(2025, 1, 1, 10, 0))

    def test_boundary_at_shift_start_is_open(self):
        p = shift_pert(12, start_hour=6)
        assert p._is_work_time(datetime(2025, 1, 1, 6, 0, 0))

    def test_boundary_at_shift_end_is_closed(self):
        p = shift_pert(12, start_hour=6)
        # 06:00 + 12 h = 18:00 should be closed (strict <)
        assert not p._is_work_time(datetime(2025, 1, 1, 18, 0, 0))


# ---------------------------------------------------------------------------
# _next_shift_start_after
# ---------------------------------------------------------------------------

class TestNextShiftStartAfter:

    def test_returns_same_time_when_inside_shift(self):
        p = shift_pert(12, start_hour=6)
        t = datetime(2025, 1, 1, 10, 0)
        assert p._next_shift_start_after(t) == t

    def test_snaps_to_next_day_when_outside_shift(self):
        p = shift_pert(12, start_hour=6)
        t = datetime(2025, 1, 1, 20, 0)
        expected = datetime(2025, 1, 2, 6, 0)
        assert p._next_shift_start_after(t) == expected

    def test_snaps_from_before_shift_to_todays_start(self):
        p = shift_pert(12, start_hour=6)
        t = datetime(2025, 1, 1, 3, 0)
        expected = datetime(2025, 1, 1, 6, 0)
        assert p._next_shift_start_after(t) == expected

    def test_24_7_returns_original_time(self):
        p = shift_pert(24, start_hour=0)
        t = datetime(2025, 1, 1, 3, 0)
        assert p._next_shift_start_after(t) == t


# ---------------------------------------------------------------------------
# _shift_boundary_events
# ---------------------------------------------------------------------------

class TestShiftBoundaryEvents:

    def test_24_7_returns_empty(self):
        p = shift_pert(24, start_hour=0)
        events = p._shift_boundary_events(
            datetime(2025, 1, 1), datetime(2025, 1, 5)
        )
        assert events == []

    def test_returns_daily_shift_starts(self):
        p = shift_pert(12, start_hour=6)
        start = datetime(2025, 1, 1, 0, 0)
        end = datetime(2025, 1, 3, 23, 59)
        events = p._shift_boundary_events(start, end)
        expected = [
            datetime(2025, 1, 1, 6, 0),
            datetime(2025, 1, 2, 6, 0),
            datetime(2025, 1, 3, 6, 0),
        ]
        assert events == expected

    def test_window_of_one_day(self):
        p = shift_pert(8, start_hour=8)
        events = p._shift_boundary_events(
            datetime(2025, 6, 1, 0, 0),
            datetime(2025, 6, 1, 23, 59),
        )
        assert events == [datetime(2025, 6, 1, 8, 0)]

    def test_start_after_shift_start_still_captured(self):
        p = shift_pert(12, start_hour=6)
        # Window starts after today's shift open but before tomorrow's
        events = p._shift_boundary_events(
            datetime(2025, 1, 1, 10, 0),
            datetime(2025, 1, 2, 12, 0),
        )
        assert datetime(2025, 1, 2, 6, 0) in events
        assert datetime(2025, 1, 1, 6, 0) not in events  # before window start


# ---------------------------------------------------------------------------
# Integration: candidate selection blocked during off-shift
# ---------------------------------------------------------------------------

class TestShiftGateInScheduler:
    """
    Verify that _select_candidate_activities() returns an empty dict when the
    current time is outside the shift window.

    We build the minimal Pert that has all pools/state needed to reach the
    gate check without crashing, then call the method directly.
    """

    def _minimal_scheduled_pert(self, working_hours=12, shift_start=6):
        start = Activity("START", 0.0)
        a = Activity("A", 4.0)
        end = Activity("END", 0.0)

        fwd = {start: [a], a: [end], end: []}
        p = Pert(graph=fwd)
        p.working_hours_per_day = working_hours
        p.shift_start_hour = shift_start

        # Minimal pool state so the method doesn't crash before reaching gate
        p.priorities = None
        p.wait = list(p.forwardDict.keys())
        p.ongoing = []
        p.completed = []
        p._completed_set = set()
        p._rebuild_ready_set()
        return p

    def test_off_shift_returns_empty_candidates(self):
        p = self._minimal_scheduled_pert(working_hours=12, shift_start=6)
        from datetime import datetime
        off_shift_time = datetime(2025, 1, 1, 20, 0)  # 20:00 — outside 06-18
        candidates = p._select_candidate_activities(off_shift_time, 'TF_based')
        assert candidates == {}

    def test_on_shift_can_return_candidates(self):
        p = self._minimal_scheduled_pert(working_hours=12, shift_start=6)
        from datetime import datetime
        on_shift_time = datetime(2025, 1, 1, 8, 0)   # 08:00 — inside 06-18
        p.startTime = datetime(2025, 1, 1, 6, 0)
        p.wait = list(p.forwardDict.keys())
        p.ongoing = []
        p.completed = []
        p._completed_set = set()
        p._rebuild_ready_set()
        # All predecessors complete = none (for START, which has no predecessors)
        candidates = p._select_candidate_activities(on_shift_time, 'TF_based')
        # START has ES=0 ≤ 8h → should be a candidate
        candidate_names = {act.name for act in candidates}
        assert "START" in candidate_names

    def test_24_7_always_allows_candidates(self):
        p = self._minimal_scheduled_pert(working_hours=24, shift_start=0)
        from datetime import datetime
        midnight = datetime(2025, 1, 1, 0, 0)
        p.startTime = datetime(2025, 1, 1, 0, 0)
        p.wait = list(p.forwardDict.keys())
        p.ongoing = []
        p.completed = []
        p._completed_set = set()
        p._rebuild_ready_set()
        candidates = p._select_candidate_activities(midnight, 'TF_based')
        # Should not be empty
        assert len(candidates) > 0
