"""Pure scenario-overlay authoring (Streamlit-free Scenario transforms)."""
from __future__ import annotations

import json
import uuid
from dataclasses import replace
from typing import Optional

from prismGui.domain.materialize import materialize
from prismGui.domain.scenario import DurationOverride, ResourceChange, Scenario


# =============================================================================
# scenario authoring (streamlit-free): build/extend a Scenario overlay
# =============================================================================
# A Scenario is an immutable OVERLAY delta bound to a baseline revision by
# ``base_plan_hash``. These helpers fold a single what-if (a task duration override or a
# resource-availability change) into a new frozen Scenario, so the render panel stays a
# thin ``st.*`` shell over pure, unit-testable transforms — the same discipline the editor
# builders follow. The domain (materialize) is the arbiter of validity; these only shape
# the delta. An EMPTY scenario (no overrides, no changes) is treated as "no scenario".

# The resource-change intent radio: a resource-availability edit is canonically ambiguous,
# so the user tags it. A what-if becomes a Scenario overlay; a baseline correction is
# redirected to the editor (a baseline edit), never authored here.
_SCN_INTENTS = ("What-if (scenario)", "Baseline correction")

def _is_whatif(intent: str) -> bool:
    return intent == _SCN_INTENTS[0]

def _scenario_is_empty(scenario: Optional[Scenario]) -> bool:
    """True when the scenario touches nothing (no duration overrides, no resource changes),
    so the run should use the plain baseline (materialize's scenario=None mirror path)."""
    return scenario is None or (not scenario.duration_overrides and not scenario.resource_changes)

def _new_scenario_for(baseline) -> Scenario:
    """A fresh empty Scenario bound to the given baseline revision (by plan_hash), with a
    FIXED per-baseline id — the internal single-overlay rebuild target used by
    ``_scenario_base``. For a NEW named scenario the analyst keeps alongside others, use
    ``_mint_scenario`` (a unique id), never this."""
    return Scenario(
        scenario_id=f"scn-{baseline.plan_id}",
        base_plan_id=baseline.plan_id,
        base_plan_hash=baseline.plan_hash,
        name="session what-if",
    )

def _mint_scenario(baseline, existing_ids=(), name: Optional[str] = None) -> Scenario:
    """A fresh empty Scenario bound to ``baseline`` with a UNIQUE id, so several scenarios
    coexist (unlike ``_new_scenario_for``'s fixed per-baseline id, which would collide). When
    no ``name`` is given an auto label is chosen from how many already exist ("Scenario A",
    "Scenario B", …). ``existing_ids`` guards the (vanishingly unlikely) id clash."""
    existing = tuple(existing_ids)
    scenario_id = f"scn-{uuid.uuid4().hex[:8]}"
    while scenario_id in existing:
        scenario_id = f"scn-{uuid.uuid4().hex[:8]}"
    if not name:
        n = len(existing)
        name = f"Scenario {chr(ord('A') + n)}" if n < 26 else f"Scenario {n + 1}"
    return Scenario(
        scenario_id=scenario_id,
        base_plan_id=baseline.plan_id,
        base_plan_hash=baseline.plan_hash,
        name=name,
    )

def _scenario_base(scenario: Optional[Scenario], baseline) -> Scenario:
    """The Scenario to extend: the session's when it targets THIS baseline, else a fresh
    one — a scenario bound to a superseded revision is rebuilt so its overrides never leak
    across a baseline change (the session's resolve_for_new_baseline normally clears such a
    scenario first; this is the belt-and-suspenders guard)."""
    if scenario is None or scenario.base_plan_hash != baseline.plan_hash:
        return _new_scenario_for(baseline)
    return scenario

def _scenario_duration_rows(scenario: Optional[Scenario]) -> list[dict]:
    """Duration overrides as display/removal rows ``{index, task_id, duration_hours}``."""
    if scenario is None:
        return []
    return [{"index": i, "task_id": ov.task_id, "duration_hours": ov.duration_hours}
            for i, ov in enumerate(scenario.duration_overrides or ())]

def _scenario_resource_rows(scenario: Optional[Scenario]) -> list[dict]:
    """Resource changes as rows ``{index, skill_type, from_hour, new_count}``."""
    if scenario is None:
        return []
    return [{"index": i, "skill_type": rc.skill_type, "from_hour": rc.from_hour,
             "new_count": rc.new_count}
            for i, rc in enumerate(scenario.resource_changes or ())]

def _add_duration_override(scenario: Optional[Scenario], baseline, task_id: str,
                           duration_hours: float) -> Scenario:
    """New Scenario with a duration override for ``task_id`` (last-write-wins per task: an
    existing override for the same task is replaced, never duplicated)."""
    base = _scenario_base(scenario, baseline)
    kept = tuple(ov for ov in (base.duration_overrides or ()) if ov.task_id != task_id)
    return replace(base, duration_overrides=kept + (
        DurationOverride(task_id=task_id, duration_hours=float(duration_hours)),))

def _remove_duration_override(scenario: Optional[Scenario], baseline, task_id: str) -> Scenario:
    """New Scenario with the override for ``task_id`` dropped (empties to None)."""
    base = _scenario_base(scenario, baseline)
    kept = tuple(ov for ov in (base.duration_overrides or ()) if ov.task_id != task_id)
    return replace(base, duration_overrides=kept or None)

def _add_resource_change(scenario: Optional[Scenario], baseline, skill_type: str,
                         from_hour: float, new_count: int) -> Scenario:
    """New Scenario with a resource change (last-write-wins per (skill_type, from_hour), so
    the UI can never author the duplicate-hour case materialize would reject)."""
    base = _scenario_base(scenario, baseline)
    fh = float(from_hour)
    kept = tuple(rc for rc in (base.resource_changes or ())
                 if not (rc.skill_type == skill_type and rc.from_hour == fh))
    return replace(base, resource_changes=kept + (
        ResourceChange(skill_type=skill_type, from_hour=fh, new_count=int(new_count)),))

def _remove_resource_change(scenario: Optional[Scenario], baseline, index: int) -> Scenario:
    """New Scenario with the resource change at ``index`` dropped (empties to None)."""
    base = _scenario_base(scenario, baseline)
    changes = list(base.resource_changes or ())
    if 0 <= index < len(changes):
        del changes[index]
    return replace(base, resource_changes=tuple(changes) or None)

def _current_schedule_payload(baseline, scenario) -> tuple[dict, Optional[str]]:
    """The effective payload for the current schedule (streamlit-free): the plain baseline when
    ``scenario`` is None, else the baseline with the scenario overlay MATERIALIZED in (duration
    overrides, resource what-ifs, emergent tasks). Returns ``(payload, warning)`` — ``warning``
    is set when the scenario cannot be materialized (a stale binding or a conflict), in which
    case the plain baseline payload is returned so the caller still renders something."""
    if scenario is None:
        return json.loads(baseline.raw_snapshot)["payload"], None
    outcome = materialize(baseline, scenario)
    if not outcome.ok or outcome.effective_plan is None:
        return (json.loads(baseline.raw_snapshot)["payload"],
                "This scenario could not be applied to the baseline; showing the baseline.")
    return json.loads(outcome.effective_plan.raw_snapshot)["payload"], None

def _schedule_label(session, baseline) -> str:
    """Human label for the current schedule — 'Baseline' or the scenario's name/id."""
    scenario = session.get_scenario()
    if scenario is None:
        return "Baseline"
    return f"Scenario “{scenario.name or scenario.scenario_id}”"
