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

from prismGui.app.view_data import _buffer_burn, _replan_diff
from prismGui.domain import serialization as ser
from prismGui.domain.hashing import hash_bytes, run_config_snapshot, scenario_snapshot
from prismGui.domain.issues import IssueCategory, Severity
from prismGui.domain.materialize import materialize
from prismGui.domain.results import TF_ZERO_TOL, DispositionOverall, RunResultStatus, Tri
from prismGui.domain.run_config import RunConfig, SGSVariant
from prismGui.domain.scenario import ResourceChange, Scenario
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


def _prepare_replan(store, example_10_path: str, run_config: RunConfig, checkpoint_hour: float,
                    scenario: Scenario) -> RunRequest:
    """The replan counterpart of ``_prepare`` (exactly what ``prepare_replan`` produces): the
    persisted effective plan is the BASELINE MIRROR (``materialize(baseline, None)``) — the
    engine's initial ``calculateScheduleWithResources`` runs it to satisfy replan()'s
    already-scheduled precondition — while the scenario deltas ride the persisted scenario
    snapshot for the adapter to resolve + project onto ``replan()``. ``checkpoint_hour`` set
    (non-None) on the ProvenanceInputs is what routes the adapter down its ``_run_replan``
    branch."""
    raw = json.loads(Path(example_10_path).read_text())
    baseline = ser.build_reference_plan("ex10", raw, schema_version=SCHEMA_VERSION)
    eff = materialize(baseline, None).effective_plan          # baseline mirror (NOT the overlay)
    baseline_hash = store.put(baseline.raw_snapshot)
    effective_hash = store.put(eff.raw_snapshot)
    run_config_hash = store.put(run_config_snapshot(run_config))
    scenario_hash = store.put(scenario_snapshot(scenario))
    provenance = ProvenanceInputs(
        baseline_snapshot_hash=baseline_hash,
        effective_plan_hash=effective_hash,
        run_config_hash=run_config_hash,
        schema_version=SCHEMA_VERSION,
        canonicalization_version=CANON_VERSION,
        scenario_delta_hash=scenario_hash,
        checkpoint_hour=checkpoint_hour,
    )
    return RunRequest(
        effective_plan_hash=effective_hash, run_config_hash=run_config_hash,
        provenance_inputs=provenance, request_id="ex10-replan")


class TestPrismAdapterReplan:
    """The real-engine replan branch (``_run_replan``): the adapter runs an INITIAL schedule
    on the baseline mirror, then ``pert.replan()`` reschedules the remainder from the as-of
    hour T. example_10's initial makespan is 85.0h (see the end-to-end test), so a mid-run T
    freezes the work already underway and re-solves the rest."""

    def test_replan_reschedules_the_remainder_from_the_as_of_hour(self, example_10_path):
        """A checkpoint-only replan at T=40 reschedules the remaining work: the result COMPLETES,
        the as-of hour rides the provenance, the schedule is complete (15 activities) and sorted,
        and there is a clean frozen/rescheduled split — at least one activity stays frozen with
        start < T while the rest are re-solved at/after T. With no deltas the tail reproduces the
        original 85.0h makespan (a no-op replan is a faithful reschedule, not a perturbation)."""
        store = InMemorySnapshotStore()
        rc = RunConfig(run_config_id="rc", sgs=SGSVariant.MAX_USE_RES_RANKED,
                       priority_rule="lf", seed=42)
        T = 40.0
        scenario = Scenario(scenario_id="scn-cp", base_plan_id="ex10",
                            base_plan_hash="", checkpoint_hour=T)
        request = _prepare_replan(store, example_10_path, rc, T, scenario)
        ex = InProcessPrismExecutor(store)

        run_id = ex.submit(request)
        assert ex.get_status(run_id) is RunStatus.COMPLETED
        r = ex.get_result(run_id)
        assert r.status is RunResultStatus.COMPLETED

        assert r.provenance.checkpoint_hour == T                 # the as-of hour rode through
        s = r.schedule
        assert len(s.activities) == 15                           # complete schedule
        assert [a.start_hour for a in s.activities] == sorted(a.start_hour for a in s.activities)
        assert s.makespan_hours == 85.0                          # no-delta replan == original tail

        # frozen/rescheduled split: work underway at T stays put; the remainder re-solves at/after T
        assert any(a.start_hour < T for a in s.activities)       # ≥1 frozen (pre-T) activity
        assert any(a.start_hour >= T for a in s.activities)      # ≥1 rescheduled (at/after T)

    def test_replan_applies_a_supported_resource_delta(self, example_10_path):
        """The supported-delta path end-to-end: a resource_update (MECHANIC 6→12 from T onward)
        is projected onto ``replan()`` and actually applied — the run COMPLETES and the extra
        crews shorten the makespan below the 85.0h baseline (85→77 for this sample), proving the
        delta reached the engine rather than being silently dropped. Exercises the open-ended
        (``until_hour=None``) resource-availability update the tz-sentinel fix unblocked."""
        store = InMemorySnapshotStore()
        rc = RunConfig(run_config_id="rc", sgs=SGSVariant.MAX_USE_RES_RANKED,
                       priority_rule="lf", seed=42)
        T = 40.0
        scenario = Scenario(
            scenario_id="scn-res", base_plan_id="ex10", base_plan_hash="", checkpoint_hour=T,
            resource_changes=(ResourceChange("MECHANIC", T, 12),))
        request = _prepare_replan(store, example_10_path, rc, T, scenario)
        ex = InProcessPrismExecutor(store)

        r = ex.get_result(ex.submit(request))
        assert r.status is RunResultStatus.COMPLETED
        assert r.provenance.checkpoint_hour == T
        assert len(r.schedule.activities) == 15
        assert r.schedule.makespan_hours < 85.0                  # the added crews sped it up

    def test_replan_residual_freezes_the_prefix_and_moves_the_tail(self, example_10_path):
        """The GUI's ``_replan_diff`` residual, grounded against the real engine: run the from-hour-0
        baseline and a replan at T=40 with a MECHANIC 6→12 delta under the SAME RunConfig, then diff
        their schedules. Because the replan's initial schedule runs the same baseline-mirror plan +
        config, every FROZEN row (start < T) is byte-identical to the baseline (``start_delta`` ==
        ``end_delta`` == 0.0), while at least one RESCHEDULED row (start ≥ T) MOVED — the extra crews
        reached the tail and dropped the makespan below 85 h. Confirms ``start < T`` reproduces the
        engine's frozen set and that a matching baseline yields a true residual (not just any diff)."""
        store = InMemorySnapshotStore()
        rc = RunConfig(run_config_id="rc", sgs=SGSVariant.MAX_USE_RES_RANKED,
                       priority_rule="lf", seed=42)
        T = 40.0
        baseline_req = _prepare(store, example_10_path, rc)
        scenario = Scenario(
            scenario_id="scn-res", base_plan_id="ex10", base_plan_hash="", checkpoint_hour=T,
            resource_changes=(ResourceChange("MECHANIC", T, 12),))
        replan_req = _prepare_replan(store, example_10_path, rc, T, scenario)
        ex = InProcessPrismExecutor(store)

        baseline = ex.get_result(ex.submit(baseline_req))
        replan = ex.get_result(ex.submit(replan_req))
        assert baseline.status is RunResultStatus.COMPLETED
        assert replan.status is RunResultStatus.COMPLETED

        # the pairing is a clean counterfactual: same starting plan + run config (baseline-mirror contract)
        assert replan.provenance.effective_plan_hash == baseline.provenance.effective_plan_hash
        assert replan.provenance.run_config_hash == baseline.provenance.run_config_hash

        d = _replan_diff(baseline.schedule, replan.schedule, T)
        frozen = [r for r in d["rows"] if r["frozen"]]
        rescheduled = [r for r in d["rows"] if r["in_replan"] and not r["frozen"]]
        assert frozen and rescheduled                            # a real split straddling T
        # frozen prefix is byte-identical to the baseline (the residual isolates the tail)
        assert all(r["start_delta"] == 0.0 and r["end_delta"] == 0.0 for r in frozen)
        # ... and the delta actually reached the rescheduled tail
        assert any(r["moved"] for r in rescheduled)
        assert replan.schedule.makespan_hours < 85.0

    def test_buffer_burn_tracks_the_finish_trajectory_across_replans(self, example_10_path):
        """The GUI's ``_buffer_burn`` temporal series (Phase-5 Row 3), grounded on the real engine:
        run the from-hour-0 baseline (the plan of record, 85.0 h) and TWO replans at T=20 and T=40
        under the SAME RunConfig, each with a MECHANIC reduction from its as-of hour (fewer crews ⇒
        the projected finish slips). Because every run's initial schedule is the same baseline-mirror
        plan + config, all three share an ``effective_plan_hash`` and assemble into one burn family
        ordered by as-of hour; slippage is measured against the 85.0 h plan of record and more work
        is frozen as the as-of hour advances."""
        store = InMemorySnapshotStore()
        rc = RunConfig(run_config_id="rc", sgs=SGSVariant.MAX_USE_RES_RANKED,
                       priority_rule="lf", seed=42)
        baseline_req = _prepare(store, example_10_path, rc)
        scn20 = Scenario(scenario_id="scn20", base_plan_id="ex10", base_plan_hash="",
                         checkpoint_hour=20.0, resource_changes=(ResourceChange("MECHANIC", 20.0, 4),))
        scn40 = Scenario(scenario_id="scn40", base_plan_id="ex10", base_plan_hash="",
                         checkpoint_hour=40.0, resource_changes=(ResourceChange("MECHANIC", 40.0, 4),))
        req20 = _prepare_replan(store, example_10_path, rc, 20.0, scn20)
        req40 = _prepare_replan(store, example_10_path, rc, 40.0, scn40)
        ex = InProcessPrismExecutor(store)

        baseline = ex.get_result(ex.submit(baseline_req))
        replan20 = ex.get_result(ex.submit(req20))
        replan40 = ex.get_result(ex.submit(req40))
        for r in (baseline, replan20, replan40):
            assert r.status is RunResultStatus.COMPLETED

        d = _buffer_burn((baseline, replan20, replan40))

        # the plan of record is the from-0 baseline; all three assemble into the burn family
        assert d["baseline_run_id"] == baseline.run_id
        assert d["baseline_makespan"] == 85.0
        assert d["n_replans"] == 2
        rows = d["rows"]
        assert len(rows) == 3
        assert [r["as_of_hour"] for r in rows] == [0.0, 20.0, 40.0]        # ordered by as-of hour

        # slippage is measured against the 85.0 h plan of record
        assert rows[0]["slippage_hours"] == 0.0
        assert all(abs(r["slippage_hours"] - (r["makespan_hours"] - 85.0)) <= TF_ZERO_TOL
                   for r in rows)

        # more work is frozen as the as-of hour advances (the real-schedule frozen fraction)
        pcs = [r["pct_complete"] for r in rows]
        assert pcs[0] == 0.0
        assert 0.0 <= pcs[1] <= pcs[2]


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

        # --- CPM per-activity timing (project-start axis) rides the DTO ---
        work = [a for a in s.activities if a.task_id not in ("START", "END")]
        assert work and all(a.es_hours is not None and a.ls_hours is not None
                            and a.cpm_slack_hours is not None for a in work)
        for a in work:
            assert isinstance(a.es_hours, float) and isinstance(a.ls_hours, float)
            # slack = lf - ef = ls - es (CPM identity), within quantization tolerance
            assert abs(a.cpm_slack_hours - (a.ls_hours - a.es_hours)) <= TF_ZERO_TOL

        # --- resource-contention arcs: a deterministic sorted tuple of task-id pairs,
        #     disjoint from precedence (added arcs only) ---
        ce = s.contention_edges
        ids = {a.task_id for a in s.activities}
        assert isinstance(ce, tuple) and list(ce) == sorted(ce)
        assert all(isinstance(e, tuple) and len(e) == 2
                   and e[0] in ids and e[1] in ids for e in ce)

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
