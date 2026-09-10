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

    def test_dependency_lag_roundtrips(self, raw_plan):
        """A successor in the schema's object form ``{"task_id", "lag_hours"}`` loads to a
        Dependency carrying that lag and serializes back to the same object form, while a
        lag-0 edge stays a bare task-id string (the shape lag-free plans use). This is the
        loader support the dependencies/lags editor form needs — without it an object-form
        successor makes ``(pred, {dict})`` unhashable and crashes the commit rehydrate."""
        lagged = copy.deepcopy(raw_plan)
        lagged["tasks"][0]["successors"] = [{"task_id": "B", "lag_hours": 5}]

        content = ser.load_plan_content(lagged)
        edge = next(d for d in content.dependencies
                    if (d.predecessor_id, d.successor_id) == ("A", "B"))
        assert edge.lag_hours == 5.0

        exported = ser.serialize_plan_content(content)
        by_id = {t["task_id"]: t for t in exported["tasks"]}
        assert by_id["A"]["successors"] == [{"task_id": "B", "lag_hours": 5.0}]
        assert by_id["B"]["successors"] == []       # lag-0 (empty) stays bare

        # A lag-0 edge still serializes to a bare string (not an object).
        plain = ser.load_plan_content(raw_plan)
        exported_plain = ser.serialize_plan_content(plain)
        plain_by_id = {t["task_id"]: t for t in exported_plain["tasks"]}
        assert plain_by_id["A"]["successors"] == ["B"]

    def test_location_worker_cap_optional_roundtrips(self, raw_plan, schema_path):
        """A location's ``max_concurrent_workers`` is optional/nullable in the schema
        (``type: ["integer","null"]``, not required), so a zone with no worker cap OMITS the key —
        exactly what a locations form emits. The loader must tolerate an absent OR null value
        without an ``int(None)`` crash (this is the fix that lets a no-worker-cap location commit),
        loading each as ``None`` while still reading a present integer, and round-trip through the
        schema-valid typed export. Nothing exercises this today — the fixtures have empty
        locations — so this test locks the loader fix."""
        plan = copy.deepcopy(raw_plan)
        plan["locations"] = [{
            "location_id": "ZONE-A",
            "description": "reactor bay",
            # is_confined_space / reason are populated only to keep the whole-document schema
            # validation focused: they are non-nullable in the schema, and serialize_plan_content
            # emits None for any absent optional — a separate, pre-existing gap. Leaving them set
            # isolates max_concurrent_workers as the sole nullable-by-design field under test.
            "is_confined_space": False,
            "availability_periods": [
                # (0) key omitted entirely -> no worker cap
                {"start_date": "2025-01-01T00:00:00", "end_date": "2025-01-05T00:00:00",
                 "max_concurrent_tasks": 2, "reason": "day shift"},
                # (1) explicit null -> no worker cap
                {"start_date": "2025-01-01T00:00:00", "end_date": "2025-01-05T00:00:00",
                 "max_concurrent_tasks": 2, "max_concurrent_workers": None, "reason": "night"},
                # (2) a present integer still loads as that int
                {"start_date": "2025-01-01T00:00:00", "end_date": "2025-01-05T00:00:00",
                 "max_concurrent_tasks": 2, "max_concurrent_workers": 3, "reason": "peak"},
            ],
        }]

        content = ser.load_plan_content(plan)           # must NOT crash on absent/null
        periods = content.locations[0].availability_periods
        assert periods[0].max_concurrent_workers is None       # omitted
        assert periods[1].max_concurrent_workers is None       # explicit null
        assert periods[2].max_concurrent_workers == 3          # present int survives

        # The typed export emits null for the uncapped periods (schema-valid) and reloads to None.
        exported = ser.serialize_plan_content(content)
        exported_periods = exported["locations"][0]["availability_periods"]
        assert exported_periods[0]["max_concurrent_workers"] is None
        assert exported_periods[2]["max_concurrent_workers"] == 3
        with open(schema_path) as fh:
            schema = json.load(fh)
        jsonschema.validate(exported, schema)                  # raises on invalid
        reloaded = ser.load_plan_content(exported)
        assert reloaded.locations[0].availability_periods[0].max_concurrent_workers is None
        assert reloaded.locations[0].availability_periods[2].max_concurrent_workers == 3

    def test_consumables_and_systems_load_to_typed_values(self, raw_plan):
        """Loader coverage for the two entity types the Increment-5 consumables/systems forms
        author. A plan with one consumable (incl. a restock) and one plant system (incl.
        ``valid_states``) loads via ``load_plan_content`` to the expected typed values — the shapes
        those forms emit through the commit path (create-or-append into keys every shipping sample
        omits, so nothing else exercises these loaders). Assert **load only**:
        ``serialize_plan_content`` intentionally does NOT emit consumables/systems — losslessness is
        *semantic*, so they live on the authoritative raw snapshot the commit rides, not the lossy
        typed export. A round-trip through export drops them BY DESIGN, not by a miss (asserted
        below so the omission reads as intentional)."""
        plan = copy.deepcopy(raw_plan)
        plan["consumables"] = [{
            "item_id": "N2-CYL", "description": "nitrogen cylinder", "total_quantity": 10.0,
            "restocks": [{"delivery_hour": 24.0, "quantity": 5.0}],
        }]
        plan["plant_systems"] = [{
            "system_id": "RCS", "description": "reactor coolant system",
            "valid_states": ["ISOLATED", "DRAINED"],
        }]

        content = ser.load_plan_content(plan)             # must NOT crash on either key
        assert len(content.consumables) == 1
        cons = content.consumables[0]
        assert cons.material_id == "N2-CYL"
        assert cons.initial_stock == 10.0
        assert len(cons.restock_deliveries) == 1
        assert cons.restock_deliveries[0].hour == 24.0    # delivery_hour is already an hour offset
        assert cons.restock_deliveries[0].quantity == 5.0
        assert len(content.systems) == 1
        assert content.systems[0].system_id == "RCS"
        assert content.systems[0].valid_states == ("ISOLATED", "DRAINED")

        # By design the lossy typed export drops both (they ride the raw snapshot the commit uses,
        # not this path) — a deliberate omission, not a gap.
        exported = ser.serialize_plan_content(content)
        assert "consumables" not in exported
        assert "plant_systems" not in exported and "systems" not in exported

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
