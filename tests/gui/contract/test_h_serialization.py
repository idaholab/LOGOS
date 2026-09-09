"""Contract group H — Serialization round-trip (lossless-semantic).

Pure now: export is schema-valid, ISO<->hour-offset conversion (the adapter never sees
ISO; half-open [start,end) preserved), normalized dependencies round-trip to per-task
successors, and two equivalent ISO instants canonicalize to the same hash.

Deferred to Phase 2 (editing lifecycle — open_draft/apply_patch/commit): the two
unknown-field survival tests, which need the raw-tree edit path to carry untyped fields
through, and the combined "edit-only-unknown-field re-stales binding" test. These are
marked @pytest.mark.phase2, so the default Phase-1 selection deselects them.
"""

from __future__ import annotations

import copy
import json

import jsonschema
import pytest

from prismGui.domain import serialization as ser
from prismGui.domain.hashing import hash_reference_plan
from prismGui.domain.versions import SCHEMA_VERSION

NOT_IMPL = "editing lifecycle (raw-tree patch path) is deferred to Phase 2"


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

    # --- Phase 2: editing lifecycle carries untyped fields through the raw tree ---

    @pytest.mark.phase2
    def test_unknown_fields_survive_edit_export_reload(self, raw_plan_with_extra_fields):
        pytest.skip(NOT_IMPL)

    @pytest.mark.phase2
    def test_lossless_is_semantic_not_bytewise(self, raw_plan_with_extra_fields):
        pytest.skip(NOT_IMPL)

    @pytest.mark.phase2
    def test_change_only_unknown_field_keeps_export_valid_and_restales_binding(self, baseline, scenario):
        pytest.skip(NOT_IMPL)
