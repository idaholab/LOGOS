"""Integration — the real PRISM adapter end-to-end (Step 5's risky-component gate).

Every test here builds a real ``Pert`` and runs a schedule, so the whole module is marked
``adapter_integration``: it runs in the integration job, never the pure-contract job. It
covers the two invariants the plan gates Step 5 on —

  * the **fresh-runtime isolation invariant**: run A, then a *different* run B, then A
    again — the two A results are byte-identical in all PRISM-derived content, proving no
    shared mutable runtime leaks between runs (a fresh ``Pert`` is built per ``submit``);
  * a **full end-to-end run on ``example_10.json``** translated into neutral DTOs, with
    the metrics/disposition pinned to the engine's verified current behavior —

plus the corollary that a run leaves its stored input snapshot byte-identical.

The group-G execution-port CONTRACT (submit/status/result/cancel semantics, missing
snapshot, unknown id) is exercised against this same adapter via the ``executor`` fixture's
``in_process`` param in ``tests/gui/contract/test_g_execution_conformance.py`` — those
instances also carry ``adapter_integration`` and run in this job.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismGui.domain import serialization as ser
from prismGui.domain.hashing import hash_bytes, run_config_snapshot
from prismGui.domain.issues import IssueCategory, Severity
from prismGui.domain.materialize import materialize
from prismGui.domain.results import TF_ZERO_TOL, DispositionOverall, RunResultStatus, Tri
from prismGui.domain.run_config import RunConfig, SGSVariant
from prismGui.domain.versions import CANON_VERSION, SCHEMA_VERSION
from prismGui.infrastructure.memory_snapshot_store import InMemorySnapshotStore
from prismGui.infrastructure.prism_adapter import InProcessPrismExecutor
from prismGui.ports.execution import ProvenanceInputs, RunRequest, RunStatus

pytestmark = pytest.mark.adapter_integration


def _prepare(store, example_10_path: str, run_config: RunConfig) -> RunRequest:
    """Materialize example_10 and persist its snapshots, returning a RunRequest whose
    hashes are the store keys (exactly what prepare_run produces in Step 6)."""
    raw = json.loads(Path(example_10_path).read_text())
    baseline = ser.build_reference_plan("ex10", raw, schema_version=SCHEMA_VERSION)
    eff = materialize(baseline, None).effective_plan
    baseline_hash = store.put(baseline.raw_snapshot)
    effective_hash = store.put(eff.raw_snapshot)
    run_config_hash = store.put(run_config_snapshot(run_config))
    provenance = ProvenanceInputs(
        baseline_snapshot_hash=baseline_hash,
        effective_plan_hash=effective_hash,
        run_config_hash=run_config_hash,
        schema_version=SCHEMA_VERSION,
        canonicalization_version=CANON_VERSION,
    )
    return RunRequest(
        effective_plan_hash=effective_hash, run_config_hash=run_config_hash,
        provenance_inputs=provenance, request_id="ex10")


class TestPrismAdapterEndToEnd:

    def test_example_10_runs_end_to_end_into_neutral_dtos(self, example_10_path):
        """A clean run on example_10 completes and translates into neutral DTOs whose
        metrics, activity set, fitness, audit issues, and disposition match the engine's
        verified behavior. The 3 `quality` warnings are the case the disposition fix
        guards: audit warnings do NOT flip audit_passed, and do NOT block."""
        store = InMemorySnapshotStore()
        rc = RunConfig(run_config_id="rc", sgs=SGSVariant.MAX_USE_RES_RANKED,
                       priority_rule="lf", seed=42)
        request = _prepare(store, example_10_path, rc)
        ex = InProcessPrismExecutor(store)

        run_id = ex.submit(request)
        assert ex.get_status(run_id) is RunStatus.COMPLETED
        r = ex.get_result(run_id)
        assert r.status is RunResultStatus.COMPLETED

        # --- schedule metrics (direct-engine-probe verified) ---
        s = r.schedule
        assert s.makespan_hours == 85.0
        assert s.cpm_lower_bound_hours == 71.0
        assert s.optimism_gap_hours == 14.0
        assert len(s.activities) == 15                       # 13 work tasks + START + END
        assert [a.start_hour for a in s.activities] == sorted(a.start_hour for a in s.activities)
        assert all(a.float_class is not None for a in s.activities)
        assert "START" in s.constrained_chain and "END" in s.constrained_chain

        # --- provenance echoes the request; the real engine version is stamped ---
        assert r.provenance.effective_plan_hash == request.effective_plan_hash
        assert r.provenance.run_config_hash == request.run_config_hash
        assert r.provenance.prism_version == InProcessPrismExecutor.prism_version

        # --- diagnostics: the 6-field fitness DTO is populated ---
        assert r.diagnostics is not None and r.diagnostics.fitness is not None
        assert r.diagnostics.fitness.composite > 0

        # --- audit: 3 `quality` warnings -> AUDIT_QUALITY / WARNING / feasibility ---
        quality = [i for i in r.issues if i.code_value == "AUDIT_QUALITY"]
        assert len(quality) == 3
        assert all(i.severity is Severity.WARNING and i.category is IssueCategory.FEASIBILITY
                   for i in quality)

        # --- disposition: warnings but no blocker -> READY_WITH_WARNINGS ---
        assert not any(i.severity is Severity.ERROR for i in r.issues)
        assert r.disposition.overall is DispositionOverall.READY_WITH_WARNINGS
        assert r.disposition.indicators.audit_passed is Tri.TRUE
        assert r.disposition.indicators.schedule_complete is Tri.TRUE

    def test_example_10_resource_utilization_timeline(self, example_10_path):
        """The demand-vs-capacity timeline rides ``diagnostics.resource_utilization``: one
        series per pool in plan order, each tiling [0, makespan], with capacity anchored to
        the SAME project-start instant as the schedule hours. MECHANIC's capacity steps
        6 → 10 across the run — pinned at hour 10 (first period) and hour 80 (second),
        which also dodges the sub-second 23:59:59 capacity gap the sliver-drop discards."""
        store = InMemorySnapshotStore()
        rc = RunConfig(run_config_id="rc", sgs=SGSVariant.MAX_USE_RES_RANKED,
                       priority_rule="lf", seed=42)
        request = _prepare(store, example_10_path, rc)
        ex = InProcessPrismExecutor(store)

        r = ex.get_result(ex.submit(request))

        util = r.diagnostics.resource_utilization
        assert util is not None
        assert util.horizon_hours == 85.0                      # == makespan
        assert [s.skill_type for s in util.series] == ["MECHANIC", "HP_TECH"]

        for series in util.series:
            ivs = series.intervals
            assert ivs, f"{series.skill_type} has no intervals"
            assert ivs[0].start_hour == 0.0
            assert ivs[-1].end_hour == 85.0                    # tiles [0, makespan]
            for prev, nxt in zip(ivs, ivs[1:]):
                # Contiguous up to a dropped sub-tolerance sliver (the shipping samples'
                # ...23:59:59 -> ...00:00:00 one-second capacity gap): never overlapping,
                # and any gap left behind is smaller than TF_ZERO_TOL.
                gap = nxt.start_hour - prev.end_hour
                assert 0.0 <= gap <= TF_ZERO_TOL
            assert all(iv.demand >= 0 and iv.available >= 0 for iv in ivs)

        def _available_at(series, hour):
            return next(iv.available for iv in series.intervals
                        if iv.start_hour <= hour < iv.end_hour)

        mech = util.series[0]
        assert _available_at(mech, 10.0) == 6                  # 09-01 10:00, first period
        assert _available_at(mech, 80.0) == 10                 # 09-04 08:00, second period
        assert max(iv.demand for iv in mech.intervals) > 0     # MECHANIC is actually used

    def test_run_a_then_b_then_a_leaves_no_residue(self, example_10_path):
        """The fresh-runtime isolation invariant: A → B → A yields two byte-identical A
        results even though the intervening B is a genuinely different run (a different
        priority rule, hence a different makespan). Equality is over the frozen DTO trees
        (schedule, diagnostics, disposition, issues) — the PRISM-derived content."""
        store = InMemorySnapshotStore()
        req_a = _prepare(store, example_10_path,
                         RunConfig(run_config_id="a", priority_rule="lf", seed=42))
        req_b = _prepare(store, example_10_path,
                         RunConfig(run_config_id="b", priority_rule="duration", seed=42))
        ex = InProcessPrismExecutor(store)

        a1 = ex.get_result(ex.submit(req_a))
        b = ex.get_result(ex.submit(req_b))
        a2 = ex.get_result(ex.submit(req_a))

        # B genuinely differs (otherwise the "no residue" claim would be vacuous).
        assert b.schedule.makespan_hours == 91.0
        assert b.schedule.makespan_hours != a1.schedule.makespan_hours

        # No residue from B: the two A runs are identical in all engine-derived content.
        assert a1.schedule == a2.schedule
        assert a1.diagnostics == a2.diagnostics
        assert a1.disposition == a2.disposition
        assert a1.issues == a2.issues

    def test_run_leaves_stored_input_snapshot_byte_identical(self, example_10_path):
        """A run mutates PRISM state in place but must never write back through the
        adapter into the stored effective-plan snapshot: the bytes are unchanged and
        still resolve to the hash they are keyed by."""
        store = InMemorySnapshotStore()
        request = _prepare(store, example_10_path,
                           RunConfig(run_config_id="rc", priority_rule="lf", seed=42))
        before = store.get(request.effective_plan_hash)

        InProcessPrismExecutor(store).submit(request)

        after = store.get(request.effective_plan_hash)
        assert after == before
        assert hash_bytes(after) == request.effective_plan_hash
