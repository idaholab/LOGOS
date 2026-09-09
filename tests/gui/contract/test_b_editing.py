"""Contract group B — Editing contract (draft / commit / referential integrity).

The whole editing lifecycle (open_draft / apply_patch / commit_draft / discard_draft)
is Phase 2 (plan "Out of scope"): the domain types exist but the operations raise
NotImplementedError. The class is marked @pytest.mark.phase2, so the default Phase-1
contract selection deselects it and the negative-inference gate does not require it.
Bodies are migrated verbatim as skips; each gains a real assertion when Phase 2 opens.
"""

from __future__ import annotations

import pytest

NOT_IMPL = "editing lifecycle is deferred to Phase 2"


@pytest.mark.phase2
class TestEditingContract:

    def test_cancelled_edit_does_not_change_committed_baseline(self, baseline):
        pytest.skip(NOT_IMPL)

    def test_invalid_edit_cannot_commit(self, baseline):
        pytest.skip(NOT_IMPL)

    def test_valid_edit_commits_to_new_immutable_plan_with_new_hash(self, baseline):
        pytest.skip(NOT_IMPL)

    def test_delete_referenced_resource_is_blocked(self, baseline):
        pytest.skip(NOT_IMPL)

    def test_commit_goes_through_raw_then_rehydrate(self, baseline):
        pytest.skip(NOT_IMPL)

    def test_dependency_cycle_rejected_on_commit(self, baseline):
        pytest.skip(NOT_IMPL)

    def test_apply_patch_is_lightweight_commit_is_full(self, baseline):
        pytest.skip(NOT_IMPL)
