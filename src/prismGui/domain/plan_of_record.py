"""domain/plan_of_record.py — a rolling plan-of-record: an ordered chain of adopted replans.

During outage EXECUTION an analyst may "adopt" a satisfactory replan as the current plan of
record so the NEXT replan chains from the adopted schedule (freeze against it, layer new
deltas) instead of re-branching off the original from-hour-0 baseline. This module is the
neutral, deeply-immutable representation of that chain — a tuple of ``AdoptedStep`` (each a
frozen ``Scenario`` delta + the run_id it was adopted from), kept sorted by the step's as-of
hour T (``scenario.checkpoint_hour``).

The chain holds INCREMENTAL deltas per step: each step's scenario carries only the changes
introduced at its own T. Replaying the steps in T-order on one Pert
(``initial → replan(step₁) → … → replan(stepₙ)``) accumulates them correctly and freezes each
step's rescheduled prefix before the next step's T. The adapter does that replay; this module
only models the chain and the pure operations over it.

Pure: stdlib + domain only (Scenario, ReplanStep). Imports neither PRISM nor Streamlit.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from prismGui.domain.results import ReplanStep
from prismGui.domain.scenario import Scenario

Hours = float
Hash = str


@dataclass(frozen=True)
class AdoptedStep:
    """One adopted replan in the chain: the scenario delta it rescheduled from (which carries
    the as-of hour T on ``scenario.checkpoint_hour``) and the run_id it was adopted from (for
    display / linking back to the stored RunResult). Scenarios are frozen, so holding the
    reference is a safe immutable snapshot of what was adopted."""
    scenario: Scenario
    adopted_run_id: str

    @property
    def checkpoint_hour(self) -> Optional[Hours]:
        return self.scenario.checkpoint_hour


@dataclass(frozen=True)
class PlanOfRecord:
    """An ordered chain of adopted replans over a fixed baseline revision.

    Bound to a baseline revision by ``base_plan_hash``: a chain is meaningless against a
    different revision, so ``resolve_for_new_baseline`` drops it when the revision changes.
    ``steps`` is kept sorted ascending by each step's ``checkpoint_hour`` — the order the
    adapter replays them in.
    """
    base_plan_id: str
    base_plan_hash: Hash
    steps: tuple[AdoptedStep, ...] = ()

    def is_empty(self) -> bool:
        return len(self.steps) == 0

    def last_checkpoint_hour(self) -> Optional[Hours]:
        """The as-of hour of the most-recently-adopted step, or None for an empty chain. The
        next adopted step (and any new replan) must be at T >= this."""
        if not self.steps:
            return None
        return self.steps[-1].checkpoint_hour

    def append(self, step: AdoptedStep) -> "PlanOfRecord":
        """Return a new PoR with ``step`` added, re-sorted by checkpoint_hour (stable). Pure —
        the receiver is unchanged. Ordering validity (T non-decreasing) is enforced upstream in
        services.prepare_replan; append only keeps the tuple canonically ordered."""
        merged = (*self.steps, step)
        ordered = tuple(sorted(merged, key=_step_sort_key))
        return replace(self, steps=ordered)

    def without_last(self) -> "PlanOfRecord":
        """Return a new PoR with the most-recently-adopted step removed (undo the last
        adoption). A no-op on an empty chain."""
        if not self.steps:
            return self
        return replace(self, steps=self.steps[:-1])

    def step_refs(self) -> tuple[ReplanStep, ...]:
        """The chain as content-addressed provenance refs — EXCEPT the hash, which services
        fills in when it persists each step's scenario snapshot. Returned here with an empty
        hash placeholder is NOT what callers want; services builds ReplanStep directly from
        each step's snapshot. Kept as a convenience for tests that only need the T-order."""
        return tuple(
            ReplanStep(scenario_delta_hash="", checkpoint_hour=float(s.checkpoint_hour or 0.0))
            for s in self.steps
        )


def _step_sort_key(step: AdoptedStep) -> Hours:
    """Sort key: as-of hour T, treating a (should-not-happen) None as 0 so the sort is total."""
    ch = step.checkpoint_hour
    return float(ch) if ch is not None else 0.0
