"""Contract group H — Serialization round-trip (lossless-semantic).

Pure now: export is schema-valid, ISO<->hour-offset conversion (the adapter never sees
ISO; half-open [start,end) preserved), normalized dependencies round-trip to per-task
successors, and two equivalent ISO instants canonicalize to the same hash.

The three unknown-field / re-staling tests are real (Phase 2, Increment 1): they run the
committed-plan edit path — ``open_draft`` -> ``apply_patch`` -> ``services.commit_draft`` —
to prove that the raw-tree commit carries schema-defined-but-untyped fields through an edit
(which the lossy ``serialize_plan_content`` typed path would drop), that losslessness is
semantic rather than bytewise (equivalent instants normalize), and that editing even a
purely unmodeled field mints a new hash that correctly re-stales a bound scenario.
"""

from __future__ import annotations

import copy
import json

import jsonschema

from prismGui.application import services
from prismGui.domain import plan as dm
from prismGui.domain import serialization as ser
from prismGui.domain.hashing import hash_reference_plan
from prismGui.domain.issues import IssueCode
from prismGui.domain.materialize import materialize
from prismGui.domain.versions import SCHEMA_VERSION


class TestSerializationRoundTrip:

    def test_export_is_schema_valid(self, baseline, schema_path):
        """serialize_plan_content(baseline.content) validates against the input schema —
        including the root-required resources/equipment/locations arrays (present even
        when empty)."""
        exported = ser.serialize_plan_content(baseline.content)
        with open(schema_path) as fh:
            schema = json.load(fh)
        jsonschema.validate(exported, schema)   # raises on invalid
        for required_key in ("outage", "tasks", "resources", "equipment", "locations"):
            assert required_key in exported

    def test_iso_to_hour_offset_boundary(self, raw_plan_with_iso_dates):
        """Load converts ISO timestamps to float hour-offsets in the typed view (no ISO
        leaks to the adapter); export converts back; half-open [start,end) is preserved
        as offsets (0 h .. 96 h over a 4-day period)."""
        content = ser.load_plan_content(raw_plan_with_iso_dates)
        period = content.resources[0].availability_periods[0]
        assert isinstance(period.start, float) and isinstance(period.end, float)
        assert period.start == 0.0
        assert period.end == 96.0
        exported = ser.serialize_plan_content(content)
        exported_period = exported["resources"][0]["availability_periods"][0]
        assert exported_period["start_date"] == "2025-01-01T00:00:00"
        assert exported_period["end_date"] == "2025-01-05T00:00:00"

    def test_dependencies_roundtrip_to_per_task_successors(self, raw_plan):
        """Normalized top-level dependencies serialize back to the schema's per-task
        successors and reload to the same edge set."""
        content = ser.load_plan_content(raw_plan)
        edges = {(d.predecessor_id, d.successor_id) for d in content.dependencies}
        assert edges == {("A", "B")}
        exported = ser.serialize_plan_content(content)
        by_id = {t["task_id"]: t for t in exported["tasks"]}
        assert by_id["A"]["successors"] == ["B"]
        assert by_id["B"]["successors"] == []
        reloaded = ser.load_plan_content(exported)
        assert {(d.predecessor_id, d.successor_id) for d in reloaded.dependencies} == edges

    def test_equivalent_iso_offsets_canonicalize_consistently(self, raw_plan_with_iso_dates):
        """Two ISO timestamps denoting the same instant with different offsets normalize
        to the same UTC form -> identical canonical bytes -> identical hash."""
        naive = copy.deepcopy(raw_plan_with_iso_dates)     # "...T00:00:00" read as UTC
        offset = copy.deepcopy(raw_plan_with_iso_dates)
        # same instant as midnight UTC, expressed with a +02:00 offset
        offset["resources"][0]["availability_periods"][0]["start_date"] = "2025-01-01T02:00:00+02:00"
        assert (hash_reference_plan(naive, schema_version=SCHEMA_VERSION)
                == hash_reference_plan(offset, schema_version=SCHEMA_VERSION))

    # --- Phase 2: the editing lifecycle carries untyped fields through the raw tree ---

    def test_unknown_fields_survive_edit_export_reload(self, raw_plan_with_extra_fields,
                                                       validator_adapter):
        """Editing a typed field through the commit path preserves schema-defined-but-
        untyped siblings: the committed raw payload still carries the task's
        ``dose_rate_mrem_per_hour`` (12.5) and ``wbs_group`` ("WBS-1") alongside the edit —
        even though the lossy typed export (``serialize_plan_content``) drops dose rate.
        The raw-tree commit path is what makes the round-trip lossless."""
        plan = ser.build_reference_plan(
            "extra", raw_plan_with_extra_fields, schema_version=SCHEMA_VERSION)
        draft = dm.open_draft(plan)
        assert dm.apply_patch(
            draft, dm.PatchOp(dm.PatchAction.REPLACE, "/tasks/0/duration", 7)).ok

        commit = services.commit_draft(draft, validator_adapter)
        assert commit.ok
        edited_task = json.loads(commit.plan.raw_snapshot)["payload"]["tasks"][0]
        assert edited_task["duration"] == 7                     # the typed edit landed
        assert edited_task["dose_rate_mrem_per_hour"] == 12.5   # untyped sibling survived
        assert edited_task["wbs_group"] == "WBS-1"

        # Contrast: the typed export drops the field the raw path preserved.
        exported_task = ser.serialize_plan_content(commit.plan.content)["tasks"][0]
        assert "dose_rate_mrem_per_hour" not in exported_task

    def test_lossless_is_semantic_not_bytewise(self, raw_plan_with_extra_fields,
                                               validator_adapter):
        """Losslessness is SEMANTIC, not bytewise. Round-tripping through a commit keeps the
        unmodeled value (dose rate 12.5) intact, yet the canonical snapshot is not byte-equal
        to the input: an availability ``start_date`` given as ``"2025-01-01T00:00:00"``
        canonicalizes to the normalized UTC form ``"2025-01-01T00:00:00.000Z"`` — the same
        instant, different bytes."""
        assert (raw_plan_with_extra_fields["resources"][0]
                ["availability_periods"][0]["start_date"] == "2025-01-01T00:00:00")

        plan = ser.build_reference_plan(
            "extra", raw_plan_with_extra_fields, schema_version=SCHEMA_VERSION)
        draft = dm.open_draft(plan)
        assert dm.apply_patch(
            draft, dm.PatchOp(dm.PatchAction.REPLACE, "/tasks/0/duration", 7)).ok
        commit = services.commit_draft(draft, validator_adapter)
        assert commit.ok

        payload = json.loads(commit.plan.raw_snapshot)["payload"]
        assert payload["tasks"][0]["dose_rate_mrem_per_hour"] == 12.5   # meaning preserved
        # ... but the timestamp bytes were normalized (same instant, canonical UTC form).
        assert (payload["resources"][0]["availability_periods"][0]["start_date"]
                == "2025-01-01T00:00:00.000Z")

    def test_change_only_unknown_field_keeps_export_valid_and_restales_binding(
            self, baseline, scenario, validator_adapter, schema_path):
        """The provenance test: editing ONLY a schema-defined-but-untyped field still mints a
        new revision that re-stales a bound scenario. Add ``dose_rate_mrem_per_hour`` to a
        task, commit, and (1) the new raw payload validates against the shipping schema;
        (2) its ``plan_hash`` differs from the baseline's; (3) materializing the OLD scenario
        (bound to the baseline hash) against the new plan fails with PROV_HASH_MISMATCH.
        Thin typing never weakens provenance — the hash covers the whole raw tree."""
        draft = dm.open_draft(baseline)
        assert dm.apply_patch(
            draft, dm.PatchOp(dm.PatchAction.ADD, "/tasks/0/dose_rate_mrem_per_hour", 5.0)).ok

        commit = services.commit_draft(draft, validator_adapter)
        assert commit.ok
        new_plan = commit.plan

        # (1) the edited raw payload is still schema-valid
        new_payload = json.loads(new_plan.raw_snapshot)["payload"]
        assert new_payload["tasks"][0]["dose_rate_mrem_per_hour"] == 5.0
        with open(schema_path) as fh:
            schema = json.load(fh)
        jsonschema.validate(new_payload, schema)   # raises on invalid

        # (2) a purely-unmodeled edit still changed the revision identity
        assert new_plan.plan_hash != baseline.plan_hash

        # (3) the scenario, bound to the OLD baseline hash, no longer materializes here
        outcome = materialize(new_plan, scenario)
        assert outcome.ok is False
        assert outcome.effective_plan is None
        assert any(i.code is IssueCode.PROV_HASH_MISMATCH for i in outcome.issues)
