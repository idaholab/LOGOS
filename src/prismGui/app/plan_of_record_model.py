"""Pure rolling-plan-of-record authoring (Streamlit-free chain transforms).

The Replan page lets an analyst ADOPT a satisfactory replan as the current plan of record so
the next replan chains from the adopted schedule. These helpers fold that intent into an
immutable ``PlanOfRecord`` (domain) and derive the small display / control values the page
needs, so the render panel stays a thin ``st.*`` shell over pure, unit-testable transforms —
the same discipline ``scenario_model`` follows. The domain type owns the chain invariants
(steps kept T-sorted); these shape adoption / undo / the timeline rows.
"""
from __future__ import annotations

from typing import Optional

from prismGui.app.scenario_model import _new_scenario_for
from prismGui.domain.hashing import hash_scenario
from prismGui.domain.plan_of_record import AdoptedStep, PlanOfRecord
from prismGui.domain.results import RunResultStatus
from prismGui.domain.scenario import Scenario


def _por_is_empty(por: Optional[PlanOfRecord]) -> bool:
    """True when there is no rolling plan of record (None or a chain with no adopted steps)."""
    return por is None or por.is_empty()


def _por_next_asof_floor(por: Optional[PlanOfRecord]) -> Optional[float]:
    """The minimum as-of hour the NEXT replan may use: the last adopted step's T — a chained
    replan must reschedule AT OR AFTER the adopted hour (each step freezes the previous step's
    rescheduled prefix before its own T). ``None`` when there is no PoR (no floor)."""
    if _por_is_empty(por):
        return None
    return por.last_checkpoint_hour()


def _run_is_adoptable(result, scenario: Optional[Scenario]) -> bool:
    """True when ``result`` is THIS working scenario's own completed replan — the guard on the
    Adopt-as-plan-of-record button. Adoption must reference a COMPLETED run that took the replan
    path (its provenance carries an as-of hour) whose as-of hour AND scenario-delta hash both
    equal the current working scenario's, so a stale, mismatched, or from-hour-0 run can never be
    adopted (Risk #3). Pure: reads DTO fields + the content hash of the scenario."""
    if result is None or scenario is None or scenario.checkpoint_hour is None:
        return False
    if result.status != RunResultStatus.COMPLETED:
        return False
    prov = result.provenance
    if prov.checkpoint_hour is None:
        return False
    if float(prov.checkpoint_hour) != float(scenario.checkpoint_hour):
        return False
    return prov.scenario_delta_hash == hash_scenario(scenario)


def _adopt_step(por: Optional[PlanOfRecord], baseline, scenario: Scenario,
                run_id: str) -> PlanOfRecord:
    """Append the just-run replan (its scenario delta + the run_id it was adopted from) to the
    rolling plan of record, starting a fresh chain bound to ``baseline`` when there is none yet
    (or the existing one is bound to a superseded revision). Pure — returns a new PlanOfRecord;
    the caller stores it on the session. T-ordering is enforced upstream (the UI floors the
    as-of input and ``prepare_replan`` gates the chain); ``append`` only keeps the chain
    T-sorted."""
    if por is None or por.base_plan_hash != baseline.plan_hash:
        por = PlanOfRecord(base_plan_id=baseline.plan_id, base_plan_hash=baseline.plan_hash)
    return por.append(AdoptedStep(scenario=scenario, adopted_run_id=run_id))


def _revert_last(por: Optional[PlanOfRecord]) -> Optional[PlanOfRecord]:
    """Undo the most-recently-adopted step. Returns ``None`` when the chain becomes empty (the
    session then clears the PoR entirely — a plain hub-and-spoke replan again), else the shorter
    chain. A no-op-shaped ``None`` in → ``None`` out."""
    if por is None:
        return None
    shorter = por.without_last()
    return None if shorter.is_empty() else shorter


def _por_chain_run_ids(por: Optional[PlanOfRecord]) -> Optional[list[str]]:
    """The adopted run-ids in T-order for the PoR-aware buffer-burn family, or ``None`` when there
    is no PoR (so ``_buffer_burn`` keeps its implicit anchor+replans family — backward compatible).
    Under a rolling PoR the committed family is exactly the from-hour-0 anchor plus these adopted
    runs, NOT the rejected counterfactual replans that share the baseline-mirror effective hash."""
    if _por_is_empty(por):
        return None
    return [step.adopted_run_id for step in por.steps]


def _next_scenario_after_adopt(baseline) -> Scenario:
    """A fresh EMPTY overlay (no deltas, no as-of hour) bound to ``baseline`` — the working
    scenario the Replan page resets to right after an adoption, ready for the next incremental
    step. Reuses the scenario-authoring helper so the id / binding match a normal session
    what-if."""
    return _new_scenario_for(baseline)


def _por_timeline_rows(por: Optional[PlanOfRecord], results_by_id) -> list[dict]:
    """The adopted chain as display rows for the Replan-page PoR panel, in adopted (T-sorted)
    order. Each row is ``{index, as_of_hour, run_id, makespan_hours, slip_from_prev_hours}``:
    ``makespan_hours`` is read from the adopted run's stored schedule when available (else
    ``None`` — the run may have been evicted), and ``slip_from_prev_hours`` is this step's
    makespan minus the previous step's (``None`` for the first step or when either makespan is
    absent) — the incremental margin each adoption consumed or recovered. Pure: reads only DTO
    fields, no ``st``."""
    if _por_is_empty(por):
        return []
    rows: list[dict] = []
    prev_makespan: Optional[float] = None
    for i, step in enumerate(por.steps):
        result = results_by_id.get(step.adopted_run_id) if results_by_id else None
        makespan = None
        if result is not None and getattr(result, "schedule", None) is not None:
            makespan = result.schedule.makespan_hours
        slip_from_prev = (makespan - prev_makespan
                          if makespan is not None and prev_makespan is not None else None)
        rows.append({
            "index": i,
            "as_of_hour": step.checkpoint_hour,
            "run_id": step.adopted_run_id,
            "makespan_hours": makespan,
            "slip_from_prev_hours": slip_from_prev,
        })
        if makespan is not None:
            prev_makespan = makespan
    return rows
