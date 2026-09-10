"""Contract group E — Materialization & preparation (pure slice).

``prepare_run`` (persisting snapshots + stamping provenance into a RunRequest) needs the
SnapshotStore port and lives in the application layer (Step 6), so its two tests are
deferred here rather than stub-skipped. The five pure tests below pin materialize's
behavior and signature and validate_run_config's mode check:
  * scenario=None mirrors the baseline exactly (distinct effective-plan hash kind);
  * a base_plan_hash mismatch is flagged PROV_HASH_MISMATCH;
  * valid-alone-but-invalid-combined surfaces MATERIALIZE_CONFLICT;
  * materialize takes only (reference_plan, scenario) — mode validity is not its job;
  * validate_run_config catches a mode selection for a task absent from the plan.
"""

from __future__ import annotations

import inspect
import json

from prismGui.domain.issues import IssueCode, Severity
from prismGui.domain.materialize import materialize, validate_run_config
from prismGui.domain.scenario import ResourceChange, Scenario


class TestMaterialization:

    def test_materialize_with_none_scenario_mirrors_baseline(self, baseline):
        """materialize(baseline, None) -> ok, an effective plan whose content mirrors the
        baseline; the effective hash is derived under the distinct ``effective_plan``
        envelope kind, so it never collides with the reference hash of identical bytes."""
        outcome = materialize(baseline, None)
        assert outcome.ok
        assert outcome.issues == ()
        effective = outcome.effective_plan
        assert effective is not None
        assert effective.base_plan_id == baseline.plan_id
        assert effective.base_plan_hash == baseline.plan_hash
        assert effective.content == baseline.content          # exact mirror
        assert effective.effective_plan_hash != baseline.plan_hash

    def test_base_hash_mismatch_is_flagged(self, baseline, scenario_with_stale_hash):
        """A scenario whose base_plan_hash != baseline.plan_hash -> PROV_HASH_MISMATCH
        and no effective plan."""
        outcome = materialize(baseline, scenario_with_stale_hash)
        assert not outcome.ok
        assert outcome.effective_plan is None
        assert any(i.code is IssueCode.PROV_HASH_MISMATCH and i.severity is Severity.ERROR
                   for i in outcome.issues)

    def test_valid_alone_invalid_combined_is_caught(self, baseline, scenario_emergent_bad_ref):
        """Baseline valid; scenario valid alone; but the emergent task references a skill
        absent from the baseline -> MATERIALIZE_CONFLICT (caught only on combination)."""
        outcome = materialize(baseline, scenario_emergent_bad_ref)
        assert not outcome.ok
        assert outcome.effective_plan is None
        assert any(i.code is IssueCode.MATERIALIZE_CONFLICT for i in outcome.issues)

    def test_materialize_does_not_receive_run_config(self):
        """Signature contract: materialize takes exactly (reference_plan, scenario).
        Mode validity is validate_run_config's job, not materialize's."""
        params = list(inspect.signature(materialize).parameters)
        assert params == ["reference_plan", "scenario"]

    def test_validate_run_config_catches_mode_for_missing_task(self, effective_plan, run_config_bad_mode):
        """A mode_selection naming a task absent from the effective plan -> INVALID_MODE."""
        issues = validate_run_config(effective_plan, run_config_bad_mode)
        assert any(i.code is IssueCode.INVALID_MODE and i.severity is Severity.ERROR
                   for i in issues)


def _res_scenario(baseline, *changes: ResourceChange) -> Scenario:
    """A Scenario carrying only resource_changes, bound to `baseline`."""
    return Scenario(
        scenario_id="scn-res", base_plan_id=baseline.plan_id,
        base_plan_hash=baseline.plan_hash, resource_changes=tuple(changes))


def _periods(effective, skill: str = "MECH") -> list[dict]:
    """The (canonicalized) availability periods of `skill`'s pool in the effective payload."""
    payload = json.loads(effective.raw_snapshot)["payload"]
    pool = next(r for r in payload["resources"] if r["skill_type"] == skill)
    return pool["availability_periods"]


class TestMaterializeResourceChanges:
    """The Phase-2 resource-availability applier folded into materialize (Increment 8). The
    `baseline` fixture ships one MECH pool with a single period spanning hours 0..96
    (2025-01-01 → 2025-01-05) at available_count 3, so a change's ``from_hour`` in [0, 96)
    is in range. Clip-and-split rewrites only the named pool; the three error paths trip the
    ERROR gate (ok=False, no effective plan)."""

    def test_interior_split_produces_two_periods(self, baseline):
        """A change at an interior hour splits the one period in two: the segment before
        ``from_hour`` keeps the old count, the segment at/after it takes the new count. No
        zero-length period is emitted (start_date < end_date holds)."""
        outcome = materialize(baseline, _res_scenario(baseline, ResourceChange("MECH", 48.0, 1)))
        assert outcome.ok and outcome.effective_plan is not None
        periods = _periods(outcome.effective_plan)
        assert len(periods) == 2
        assert [p["available_count"] for p in periods] == [3, 1]
        assert all(p["start_date"] < p["end_date"] for p in periods)

    def test_whole_period_lowering_keeps_one_period(self, baseline):
        """A change at ``from_hour <= start`` rewrites the whole period in place — the count
        drops with no split (one period remains)."""
        outcome = materialize(baseline, _res_scenario(baseline, ResourceChange("MECH", 0.0, 1)))
        assert outcome.ok and outcome.effective_plan is not None
        periods = _periods(outcome.effective_plan)
        assert len(periods) == 1
        assert periods[0]["available_count"] == 1

    def test_temporary_outage_as_two_changes(self, baseline):
        """A temporary outage is authored as two changes — drop to 0 at hour 24, restore to 3
        at hour 72 — yielding three contiguous periods with counts [3, 0, 3]."""
        outcome = materialize(baseline, _res_scenario(
            baseline, ResourceChange("MECH", 24.0, 0), ResourceChange("MECH", 72.0, 3)))
        assert outcome.ok and outcome.effective_plan is not None
        periods = _periods(outcome.effective_plan)
        assert [p["available_count"] for p in periods] == [3, 0, 3]
        # contiguous / non-overlapping: each period's end abuts the next period's start
        assert all(periods[i]["end_date"] == periods[i + 1]["start_date"]
                   for i in range(len(periods) - 1))

    def test_ghost_skill_is_blocking_conflict(self, baseline):
        """A change targeting a skill absent from the baseline resources ->
        MATERIALIZE_CONFLICT + ok=False, no effective plan."""
        outcome = materialize(baseline, _res_scenario(baseline, ResourceChange("GHOST", 24.0, 0)))
        assert not outcome.ok
        assert outcome.effective_plan is None
        assert any(i.code is IssueCode.MATERIALIZE_CONFLICT and i.severity is Severity.ERROR
                   for i in outcome.issues)

    def test_duplicate_hour_for_same_skill_is_rejected(self, baseline):
        """Two changes sharing a (skill_type, from_hour) is the scenario contract's
        invalid case -> INVALID_AVAILABILITY_INTERVAL + ok=False."""
        outcome = materialize(baseline, _res_scenario(
            baseline, ResourceChange("MECH", 24.0, 1), ResourceChange("MECH", 24.0, 2)))
        assert not outcome.ok
        assert outcome.effective_plan is None
        assert any(i.code is IssueCode.INVALID_AVAILABILITY_INTERVAL and i.severity is Severity.ERROR
                   for i in outcome.issues)

    def test_out_of_range_from_hour_is_rejected(self, baseline):
        """A ``from_hour`` at or beyond the pool's last period end (here 96) would change
        nothing, so it is rejected up front -> INVALID_AVAILABILITY_INTERVAL."""
        outcome = materialize(baseline, _res_scenario(baseline, ResourceChange("MECH", 200.0, 1)))
        assert not outcome.ok
        assert outcome.effective_plan is None
        assert any(i.code is IssueCode.INVALID_AVAILABILITY_INTERVAL
                   for i in outcome.issues)

    def test_negative_from_hour_is_rejected(self, baseline):
        """A negative ``from_hour`` is out of range -> INVALID_AVAILABILITY_INTERVAL."""
        outcome = materialize(baseline, _res_scenario(baseline, ResourceChange("MECH", -1.0, 1)))
        assert not outcome.ok
        assert outcome.effective_plan is None
        assert any(i.code is IssueCode.INVALID_AVAILABILITY_INTERVAL
                   for i in outcome.issues)

    def test_effective_payload_revalidates_after_rewrite(self, baseline, validator_adapter):
        """The rewritten effective payload is a schema-valid plan: fed back through the real
        ValidationPort, the split periods raise no ERROR (valid ISO dates, start < end)."""
        outcome = materialize(baseline, _res_scenario(baseline, ResourceChange("MECH", 48.0, 1)))
        assert outcome.ok and outcome.effective_plan is not None
        effective_payload = json.loads(outcome.effective_plan.raw_snapshot)["payload"]
        issues = validator_adapter.validate_plan(effective_payload)
        assert not any(i.severity is Severity.ERROR for i in issues), \
            [(i.code.name, i.message) for i in issues if i.severity is Severity.ERROR]
