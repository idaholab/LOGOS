"""Contract group C — Provenance & snapshots.

Pure-hash slice: the identity hash excludes human-assigned ids/names, is deterministic
across key ordering & numeric formatting, and covers unmodeled raw fields (thin typing
does not blind revision id).

Snapshot-store slice (Step 3, with the in-memory store): put-key == provenance hash;
content-addressed idempotency + missing-hash raises; substituted bytes are detectable by
recompute. Still deferred to Step 6 (need prepare_run / a real executor):
``test_stored_run_reproduces_from_snapshot_and_seed``,
``test_every_provenance_hash_resolves_in_snapshot_store``,
``test_prepare_run_persists_snapshots_before_returning`` — not written yet, so the
negative-inference gate does not require them.
"""

from __future__ import annotations

import copy
import dataclasses

import pytest

from prismGui.domain import serialization as ser
from prismGui.domain.hashing import (
    hash_bytes,
    hash_reference_plan,
    hash_run_config,
    hash_scenario,
    reference_plan_snapshot,
)
from prismGui.domain.run_config import EvaluationWeights, SGSVariant
from prismGui.domain.scenario import (
    DependencySuppression, DurationOverride, EquipmentChange, LocationChange, ResourceChange,
    TaskSuppression,
)
from prismGui.domain.versions import SCHEMA_VERSION
from prismGui.ports.snapshot_store import SnapshotNotFoundError


class TestProvenanceAndSnapshots:

    def test_baseline_hash_excludes_self_and_id(self, baseline, raw_plan):
        """The identity hash is the content hash of the raw snapshot — not
        self-referential. Changing plan_id (or the stored plan_hash) does not change
        the recomputed hash, and building the same content under a different id yields
        the same plan_hash."""
        assert hash_bytes(baseline.raw_snapshot) == baseline.plan_hash
        relabelled = dataclasses.replace(baseline, plan_id="a-different-id",
                                         plan_hash="deadbeef")
        assert hash_bytes(relabelled.raw_snapshot) == baseline.plan_hash
        rebuilt = ser.build_reference_plan("yet-another-id", copy.deepcopy(raw_plan),
                                           schema_version=SCHEMA_VERSION)
        assert rebuilt.plan_hash == baseline.plan_hash

    def test_run_config_hash_excludes_config_id(self, run_config):
        """Two RunConfigs differing only in run_config_id hash equal; differing in any
        solver-affecting field (sgs / priority_rule / seed / modes) hash different."""
        h = hash_run_config(run_config)
        assert hash_run_config(dataclasses.replace(run_config, run_config_id="other")) == h
        assert hash_run_config(dataclasses.replace(run_config, priority_rule="es")) != h
        assert hash_run_config(dataclasses.replace(run_config, seed=run_config.seed + 1)) != h
        assert hash_run_config(dataclasses.replace(run_config, sgs=SGSVariant.FIRST)) != h

    def test_run_config_hash_covers_evaluation_weights(self, run_config):
        """The composite fitness weights are part of run-config identity: setting them (vs the
        None the fixture carries) and changing them each shift the hash — so the GUI weights
        control already registers as ``DIFFERENT_CONFIG`` freshness with no serialization change.
        (The fixture's ``evaluation_weights`` is None — the "defaults → None" GUI path — so this
        also pins that a None run and a custom-weights run are distinct configs.)"""
        h = hash_run_config(run_config)
        assert run_config.evaluation_weights is None
        custom = dataclasses.replace(
            run_config, evaluation_weights=EvaluationWeights(alpha=2.0, beta=0.5, gamma=0.3, delta=2.0))
        assert hash_run_config(custom) != h
        other = dataclasses.replace(
            run_config, evaluation_weights=EvaluationWeights(alpha=1.0, beta=1.0, gamma=1.0, delta=1.0))
        assert hash_run_config(other) != h
        assert hash_run_config(other) != hash_run_config(custom)

    def test_scenario_hash_excludes_nonsemantic_fields(self, scenario):
        """Scenario hash ignores scenario_id and display name; it depends on the delta
        content and on the base_plan_hash it binds to."""
        h = hash_scenario(scenario)
        renamed = dataclasses.replace(scenario, scenario_id="other-id", name="new name")
        assert hash_scenario(renamed) == h
        changed_delta = dataclasses.replace(
            scenario, duration_overrides=(DurationOverride(task_id="B", duration_hours=99.0),))
        assert hash_scenario(changed_delta) != h
        rebound = dataclasses.replace(scenario, base_plan_hash="f" * 64)
        assert hash_scenario(rebound) != h

    def test_scenario_hash_covers_increment_a_families(self, scenario):
        """Freshness gate for the Increment-A families: two scenarios differing only in
        equipment_changes or location_changes hash DIFFERENTLY, and a period change's bounded
        ``to_hour`` is part of its identity (open-ended vs bounded hash apart) — so
        ``scenario_payload`` includes each field (else two distinct scenarios would look
        identical and a re-run would wrongly read as CURRENT)."""
        base = dataclasses.replace(scenario, duration_overrides=None)   # empty-delta baseline
        h0 = hash_scenario(base)
        assert hash_scenario(dataclasses.replace(
            base, equipment_changes=(EquipmentChange("CRANE", 24.0, 0),))) != h0
        assert hash_scenario(dataclasses.replace(
            base, location_changes=(LocationChange("BAY1", 24.0, 1),))) != h0
        # a skill change's window bound distinguishes it (open-ended vs bounded)
        res_open = dataclasses.replace(base, resource_changes=(ResourceChange("MECH", 24.0, 1),))
        res_bounded = dataclasses.replace(
            base, resource_changes=(ResourceChange("MECH", 24.0, 1, to_hour=72.0),))
        assert hash_scenario(res_open) != hash_scenario(res_bounded)
        # …and likewise for an equipment change's window bound
        eqp_open = dataclasses.replace(base, equipment_changes=(EquipmentChange("CRANE", 24.0, 0),))
        eqp_bounded = dataclasses.replace(
            base, equipment_changes=(EquipmentChange("CRANE", 24.0, 0, to_hour=72.0),))
        assert hash_scenario(eqp_open) != hash_scenario(eqp_bounded)
        # a location change's worker-cap value is part of identity too
        loc_tasks = dataclasses.replace(base, location_changes=(LocationChange("BAY1", 24.0, 1),))
        loc_both = dataclasses.replace(
            base, location_changes=(LocationChange("BAY1", 24.0, 1, new_max_concurrent_workers=2),))
        assert hash_scenario(loc_tasks) != hash_scenario(loc_both)

    def test_scenario_hash_covers_increment_c_suppression_families(self, scenario):
        """Freshness gate for the Increment-C removal families: two scenarios differing only in
        task_suppressions or dependency_suppressions hash DIFFERENTLY — so ``scenario_payload``
        includes each (else suppressing a task/edge would look CURRENT and skip a re-run)."""
        base = dataclasses.replace(scenario, duration_overrides=None)   # empty-delta baseline
        h0 = hash_scenario(base)
        assert hash_scenario(dataclasses.replace(
            base, task_suppressions=(TaskSuppression("A"),))) != h0
        assert hash_scenario(dataclasses.replace(
            base, dependency_suppressions=(DependencySuppression("A", "B"),))) != h0
        # the two removal families are distinct: suppressing a task != suppressing that task's edge
        by_task = dataclasses.replace(base, task_suppressions=(TaskSuppression("B"),))
        by_edge = dataclasses.replace(base, dependency_suppressions=(DependencySuppression("A", "B"),))
        assert hash_scenario(by_task) != hash_scenario(by_edge)

    def test_canonicalization_is_deterministic(self, baseline, raw_plan):
        """Canonical serialization is stable across object-key ordering and numeric
        formatting of equivalent inputs: reordered keys and an int rewritten as an
        equal float produce identical bytes -> identical hash."""
        variant = copy.deepcopy(raw_plan)
        # reorder the top-level keys and the outage keys
        variant = {k: variant[k] for k in reversed(list(variant.keys()))}
        variant["outage"] = {k: variant["outage"][k]
                             for k in reversed(list(variant["outage"].keys()))}
        # 24 (int) and 24.0 (float) canonicalize to the same token "24"
        variant["outage"]["working_hours_per_day"] = 24.0
        assert (reference_plan_snapshot(variant, schema_version=SCHEMA_VERSION)
                == reference_plan_snapshot(raw_plan, schema_version=SCHEMA_VERSION))
        assert hash_reference_plan(variant, schema_version=SCHEMA_VERSION) == baseline.plan_hash

    def test_baseline_hash_covers_unmodeled_raw_fields(self, raw_plan_with_extra_fields):
        """Provenance-critical: changing ONLY an unmodeled/untyped raw field (a task
        dose rate the thin view never re-emits) still changes the baseline hash, because
        the hash is taken over the whole raw tree — thin typing cannot blind identity."""
        base = hash_reference_plan(raw_plan_with_extra_fields, schema_version=SCHEMA_VERSION)
        mutated = copy.deepcopy(raw_plan_with_extra_fields)
        mutated["tasks"][0]["dose_rate_mrem_per_hour"] = 999.0
        assert hash_reference_plan(mutated, schema_version=SCHEMA_VERSION) != base

    # --- snapshot-store slice (Step 3: the in-memory content-addressed store) ---

    def test_snapshot_store_is_content_addressed_and_idempotent(self, snapshot_store, baseline):
        """put(x) returns a hash; put(x) again returns the SAME hash and stores once
        (dedup); get(hash) returns the original bytes; get(missing) raises
        SnapshotNotFoundError (the SNAPSHOT_MISSING source)."""
        snapshot = baseline.raw_snapshot
        h1 = snapshot_store.put(snapshot)
        h2 = snapshot_store.put(snapshot)
        assert h1 == h2
        assert len(snapshot_store._blobs) == 1          # dedup: stored exactly once
        assert snapshot_store.contains(h1)
        assert snapshot_store.get(h1) == snapshot
        with pytest.raises(SnapshotNotFoundError) as excinfo:
            snapshot_store.get("0" * 64)
        assert excinfo.value.snapshot_hash == "0" * 64

    def test_stored_bytes_hash_to_the_put_key(self, snapshot_store, baseline):
        """The storage key and the provenance hash for the same bytes cannot diverge:
        put(raw_snapshot) returns exactly the plan's provenance hash, and recomputing the
        content hash of the retrieved bytes reproduces the key."""
        key = snapshot_store.put(baseline.raw_snapshot)
        assert key == baseline.plan_hash                       # put-key == provenance hash
        assert hash_bytes(snapshot_store.get(key)) == key      # retrieved bytes re-hash to the key

    def test_substituted_snapshot_bytes_are_detected(self, snapshot_store, baseline):
        """Content-addressing makes tampering detectable: if the bytes stored under a key
        are substituted, recomputing the hash of get(key) no longer matches the key. (The
        honest store cannot produce this, so the corruption is injected directly.)"""
        key = snapshot_store.put(baseline.raw_snapshot)
        assert hash_bytes(snapshot_store.get(key)) == key      # intact
        snapshot_store._blobs[key] = baseline.raw_snapshot + "  <tampered>"
        assert hash_bytes(snapshot_store.get(key)) != key      # recompute detects the mismatch
