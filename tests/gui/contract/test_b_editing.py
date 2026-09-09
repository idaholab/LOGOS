"""Contract group B — Editing contract (draft / commit / referential integrity).

The editing lifecycle is real (Phase 2, Increment 1). The pure ops — ``open_draft`` /
``apply_patch`` / ``discard_draft`` — live in ``prismGui.domain.plan``; the FULL commit
(``services.commit_draft``) is orchestrated in the application layer, where it runs the
schema + referential re-validation through the ValidationPort and, only when clean, mints a
NEW immutable ``ReferencePlan`` with a recomputed ``plan_hash``.

The load-bearing contract these tests pin is the **lightweight / full split**: ``apply_patch``
does a purely structural check (does the JSON-Pointer resolve; is the op mechanically valid)
and never runs schema/referential validation, so a referentially-fatal edit stages cleanly
and is caught only at commit — while a structurally-broken edit is rejected by ``apply_patch``
itself, mutating nothing.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from prismGui.application import services
from prismGui.domain import plan as dm
from prismGui.domain.hashing import hash_bytes
from prismGui.domain.issues import IssueCode, Severity


class TestEditingContract:

    def test_cancelled_edit_does_not_change_committed_baseline(self, baseline):
        """Open a draft, stage an edit, then discard: the committed baseline — its hash and
        its typed content — is untouched (a draft only ever holds a deep copy), and the
        draft's own working state is cleared."""
        original_hash = baseline.plan_hash
        original_duration = baseline.content.tasks[0].duration

        draft = dm.open_draft(baseline)
        applied = dm.apply_patch(
            draft, dm.PatchOp(dm.PatchAction.REPLACE, "/tasks/0/duration", 99))
        assert applied.ok
        assert dm.discard_draft(draft) is None

        assert baseline.plan_hash == original_hash
        assert baseline.content.tasks[0].duration == original_duration == 4.0
        assert draft.raw_working_tree == {}
        assert draft.pending_patches == []

    def test_invalid_edit_cannot_commit(self, baseline, validator_adapter):
        """A structurally-valid but schema-INVALID edit (a task duration set to a string)
        stages fine — ``apply_patch`` is lightweight — yet is rejected at commit: ``ok`` is
        False, an ERROR issue is surfaced, and no new plan is minted."""
        draft = dm.open_draft(baseline)
        applied = dm.apply_patch(
            draft, dm.PatchOp(dm.PatchAction.REPLACE, "/tasks/0/duration", "four"))
        assert applied.ok                                    # structural check passes

        commit = services.commit_draft(draft, validator_adapter)
        assert commit.ok is False
        assert commit.plan is None
        assert any(i.severity is Severity.ERROR for i in commit.issues)

    def test_valid_edit_commits_to_new_immutable_plan_with_new_hash(self, baseline,
                                                                     validator_adapter):
        """A valid edit commits to a NEW immutable ReferencePlan: a distinct object with a
        new hash; the baseline still carries the old value, the new plan the edited one, and
        the new plan is frozen (attribute assignment raises)."""
        draft = dm.open_draft(baseline)
        assert dm.apply_patch(
            draft, dm.PatchOp(dm.PatchAction.REPLACE, "/tasks/1/duration", 8)).ok

        commit = services.commit_draft(draft, validator_adapter)
        assert commit.ok
        new_plan = commit.plan
        assert new_plan is not None and new_plan is not baseline
        assert new_plan.plan_hash != baseline.plan_hash
        assert baseline.content.tasks[1].duration == 6.0     # baseline B unchanged
        assert new_plan.content.tasks[1].duration == 8.0     # new revision B edited
        with pytest.raises(dataclasses.FrozenInstanceError):
            new_plan.plan_id = "mutated"                     # type: ignore[misc]

    def test_delete_referenced_resource_is_blocked(self, baseline, validator_adapter):
        """Removing the only resource pool applies structurally, but commit blocks: both
        tasks still require skill 'MECH', now undefined -> REF_MISSING, and no new plan."""
        draft = dm.open_draft(baseline)
        assert dm.apply_patch(draft, dm.PatchOp(dm.PatchAction.REMOVE, "/resources/0")).ok

        commit = services.commit_draft(draft, validator_adapter)
        assert commit.ok is False
        assert commit.plan is None
        assert any(i.code is IssueCode.REF_MISSING and i.severity is Severity.ERROR
                   for i in commit.issues)

    def test_commit_goes_through_raw_then_rehydrate(self, baseline, validator_adapter):
        """Commit rebuilds from the RAW patched tree, then rehydrates the typed view: the new
        plan's raw payload AND its rehydrated ``content`` agree on the edited duration, and
        its ``plan_hash`` is exactly the SHA-256 of its canonical raw snapshot."""
        draft = dm.open_draft(baseline)
        assert dm.apply_patch(
            draft, dm.PatchOp(dm.PatchAction.REPLACE, "/tasks/0/duration", 5)).ok

        commit = services.commit_draft(draft, validator_adapter)
        assert commit.ok
        new_plan = commit.plan
        raw_payload = json.loads(new_plan.raw_snapshot)["payload"]
        assert raw_payload["tasks"][0]["duration"] == 5          # raw tree
        assert new_plan.content.tasks[0].duration == 5.0         # rehydrated typed view
        assert new_plan.plan_hash == hash_bytes(new_plan.raw_snapshot)

    def test_dependency_cycle_rejected_on_commit(self, baseline, validator_adapter):
        """Appending B -> A closes the A -> B -> A cycle. ``apply_patch`` (structural only)
        accepts it; commit runs the full referential check and blocks with DEP_CYCLE."""
        draft = dm.open_draft(baseline)
        assert dm.apply_patch(
            draft, dm.PatchOp(dm.PatchAction.ADD, "/tasks/1/successors/-", "A")).ok

        commit = services.commit_draft(draft, validator_adapter)
        assert commit.ok is False
        assert commit.plan is None
        assert any(i.code is IssueCode.DEP_CYCLE and i.severity is Severity.ERROR
                   for i in commit.issues)

    def test_apply_patch_is_lightweight_commit_is_full(self, baseline, validator_adapter):
        """The lightweight / full split, both directions:
          * a referentially-fatal but structurally-fine edit (remove the only resource pool)
            passes ``apply_patch`` (ok, no issues) yet blocks at commit;
          * a structurally-broken edit (a JSON-Pointer that does not resolve) is rejected by
            ``apply_patch`` itself — INVALID_PATCH — and leaves the working tree untouched.
        """
        draft = dm.open_draft(baseline)

        light = dm.apply_patch(draft, dm.PatchOp(dm.PatchAction.REMOVE, "/resources/0"))
        assert light.ok is True and light.issues == ()          # no referential check here
        commit = services.commit_draft(draft, validator_adapter)
        assert commit.ok is False
        assert any(i.severity is Severity.ERROR for i in commit.issues)

        before = json.loads(json.dumps(draft.raw_working_tree))  # snapshot the current tree
        bad = dm.apply_patch(
            draft, dm.PatchOp(dm.PatchAction.REPLACE, "/tasks/99/duration", 3))
        assert bad.ok is False
        assert any(i.code is IssueCode.INVALID_PATCH and i.severity is Severity.ERROR
                   for i in bad.issues)
        assert draft.raw_working_tree == before                  # no mutation on rejection
        assert len(draft.pending_patches) == 1                   # only the remove was staged
