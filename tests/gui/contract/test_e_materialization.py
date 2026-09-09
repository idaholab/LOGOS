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

from prismGui.domain.issues import IssueCode, Severity
from prismGui.domain.materialize import materialize, validate_run_config


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
