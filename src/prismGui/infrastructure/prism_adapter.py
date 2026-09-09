"""infrastructure/prism_adapter.py — ExecutionPort over the PRISM engine.

The **only** module in the system that imports the PRISM package (``CPM.pert`` /
``CPM.outage_data`` / ``CPM.activity`` — the schedule audit is reached through
``Pert.validate_schedule``). It implements ``ExecutionPort`` (ports/execution.py) by
building a **fresh** ``Pert`` runtime per run from the effective-plan snapshot + the
RunConfig snapshot, running one schedule, and translating the result into the neutral
DTOs of ``prismGui.domain.results``. The domain and application layers never import
PRISM; they hand this adapter two snapshot hashes and receive a ``RunResult``.

The full contract this implements is ``dev_docs/prism-gui-prism-adapter.md``. The load-
bearing points:

* **Fresh runtime per run (§2, §10).** PRISM mutates activities, pools, and timing
  fields in place across a run, so a runtime is never stored or reused. Every ``submit``
  builds ``Pert(outage_data=OutageData.from_dict(payload), seed=...)`` from a snapshot
  resolved out of the SnapshotStore — never ``Pert.from_json_file`` (which re-reads disk
  and re-runs the validator). Because each run starts clean, a failed run cannot corrupt
  a later one and the A→B→A isolation invariant holds.
* **Time model (§1, §5).** The snapshot carries ISO timestamps (what ``from_dict``
  ingests and what canonicalization hashes); the ISO→hour-offset conversion happens here,
  on the OUTPUT side, quantized to the same 1 ms grid (``q``) the hashing uses.
* **Hard failure boundary (§9).** Any PRISM exception is caught and converted to a FAILED
  ``RunResult`` bearing an ``EXECUTION_FAILURE`` issue with a *sanitized* message — a raw
  traceback is logged, never surfaced. A missing snapshot is caught separately as
  ``SNAPSHOT_MISSING`` (storage mechanics, not an engine failure).
* **Synchronous in Phase 1 (§0).** ``submit`` runs to a terminal state before returning,
  so ``get_status`` reports ``completed`` / ``failed`` immediately. The job-shaped port is
  unchanged, so a later background executor satisfies the same interface.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from CPM.outage_data import OutageData
from CPM.pert import Pert

from prismGui.domain import serialization as ser
from prismGui.domain.disposition import ScheduleSummary, compute_disposition
from prismGui.domain.hashing import q
from prismGui.domain.resource_util import build_resource_utilization
from prismGui.domain.issues import Issue, IssueCategory, IssueCode, Severity
from prismGui.domain.results import (
    ActualResource,
    DiagnosticsDTO,
    FitnessDTO,
    Provenance,
    RunResult,
    RunResultStatus,
    ScheduleDTO,
    ScheduledActivityDTO,
    classify_float,
)
from prismGui.domain.versions import APP_VERSION
from prismGui.ports.execution import ProvenanceInputs, RunRequest, RunStatus
from prismGui.ports.snapshot_store import SnapshotNotFoundError, SnapshotStorePort

logger = logging.getLogger(__name__)

# CPM ships no ``__version__``; this constant is the engine identity stamped into
# provenance and should be bumped when the engine gains a real version marker. It is not
# asserted by any test and is excluded from the isolation comparison (it is constant).
_PRISM_VERSION = "cpm-unversioned"

# Audit ``Violation.type`` -> (reused IssueCode | None, IssueCategory) per adapter §6.
# ``None`` means synthesize ``AUDIT_<TYPE>`` from the validator's own type string (one
# source of truth; no hand-maintained list of 16 names).
_AUDIT_MAP: dict[str, tuple[object, IssueCategory]] = {
    "completeness": (IssueCode.UNSCHEDULED_TASK, IssueCategory.FEASIBILITY),
    "precedence": (IssueCode.DEP_VIOLATION, IssueCategory.FEASIBILITY),
    "mode": (IssueCode.INVALID_MODE, IssueCategory.EXECUTION),
    "duration": (None, IssueCategory.EXECUTION),
    "time_window": (None, IssueCategory.TIME_WINDOW),
    "hold_point": (None, IssueCategory.FEASIBILITY),
    "crew": (None, IssueCategory.FEASIBILITY),
    "substitution": (None, IssueCategory.FEASIBILITY),
    "equipment": (None, IssueCategory.FEASIBILITY),
    "location": (None, IssueCategory.FEASIBILITY),
    "consumable": (None, IssueCategory.FEASIBILITY),
    "equipment_zone": (None, IssueCategory.FEASIBILITY),
    "shift_calendar": (None, IssueCategory.FEASIBILITY),
    "dose": (None, IssueCategory.DOSE),
    "system_state": (None, IssueCategory.SYSTEM_STATE),
    "quality": (None, IssueCategory.FEASIBILITY),
}


def _severity(raw: object) -> Severity:
    """Map a ``Violation.severity`` string to a domain ``Severity``. Only 'error' blocks;
    anything else (the validator emits 'warning') is a non-blocking WARNING."""
    return Severity.ERROR if str(raw).lower() == "error" else Severity.WARNING


class InProcessPrismExecutor:
    """Synchronous ``ExecutionPort`` backed by a fresh in-process PRISM runtime per run.

    Construct once with a ``SnapshotStorePort``; reuse across many ``submit`` calls (each
    builds its own ``Pert``, so there is no shared mutable engine state between runs).
    Out-of-band run ids raise ``KeyError``; ``cancel`` is a defined no-op on an already-
    terminal (immediate-complete) run.
    """

    prism_version = _PRISM_VERSION

    def __init__(self, snapshot_store: SnapshotStorePort) -> None:
        self._store = snapshot_store
        self._status: dict[str, RunStatus] = {}
        self._results: dict[str, RunResult] = {}
        self._counter = 0

    # ------------------------------------------------------------------ port API
    def submit(self, request: RunRequest) -> str:
        self._counter += 1
        run_id = f"prism-run-{self._counter}"
        prov = self._provenance(run_id, request.provenance_inputs)

        # Resolve snapshots first: a missing hash is a storage fact (SNAPSHOT_MISSING),
        # kept distinct from an engine failure so the UI can tell them apart.
        try:
            plan_payload = self._resolve_payload(request.effective_plan_hash)
            rc_payload = self._resolve_payload(request.run_config_hash)
        except SnapshotNotFoundError as exc:
            self._record_failed(
                run_id, prov,
                Issue(code=IssueCode.SNAPSHOT_MISSING, severity=Severity.ERROR,
                      category=IssueCategory.PROVENANCE,
                      message=f"snapshot not found: {exc.snapshot_hash}"))
            return run_id

        # Run PRISM behind the hard boundary: no exception escapes to the caller (§9).
        try:
            result = self._run(plan_payload, rc_payload, run_id, prov)
        except Exception as exc:  # noqa: BLE001 - deliberate hard boundary
            logger.exception("PRISM execution failed for run %s", run_id)
            self._record_failed(
                run_id, prov,
                Issue(code=IssueCode.EXECUTION_FAILURE, severity=Severity.ERROR,
                      category=IssueCategory.EXECUTION,
                      message=f"{type(exc).__name__}: {exc}"))
            return run_id

        self._results[run_id] = result
        self._status[run_id] = RunStatus.COMPLETED
        return run_id

    def get_status(self, run_id: str) -> RunStatus:
        if run_id not in self._status:
            raise KeyError(run_id)
        return self._status[run_id]

    def get_result(self, run_id: str) -> RunResult:
        if run_id not in self._results:
            raise KeyError(run_id)
        return self._results[run_id]

    def cancel(self, run_id: str) -> None:
        if run_id not in self._status:
            raise KeyError(run_id)
        # Immediate-complete runs are already terminal; cancel is a defined no-op.

    # ---------------------------------------------------------------- internals
    def _resolve_payload(self, snapshot_hash: str) -> dict:
        """Fetch a canonical JCS envelope string from the store and return its schema-
        shaped ``payload`` (a fresh dict each call — never shared into a Pert twice)."""
        raw = self._store.get(snapshot_hash)
        return json.loads(raw)["payload"]

    def _provenance(self, run_id: str, pin: ProvenanceInputs) -> Provenance:
        return Provenance(
            baseline_snapshot_hash=pin.baseline_snapshot_hash,
            effective_plan_hash=pin.effective_plan_hash,
            run_config_hash=pin.run_config_hash,
            schema_version=pin.schema_version,
            canonicalization_version=pin.canonicalization_version,
            app_version=APP_VERSION,
            prism_version=self.prism_version,
            run_id=run_id,
            timestamp=datetime.now(timezone.utc),
            scenario_delta_hash=pin.scenario_delta_hash,
        )

    def _record_failed(self, run_id: str, prov: Provenance, issue: Issue) -> None:
        self._results[run_id] = RunResult(
            run_id=run_id, status=RunResultStatus.FAILED, provenance=prov,
            issues=(issue,), disposition=None, schedule=None, diagnostics=None)
        self._status[run_id] = RunStatus.FAILED

    def _run(self, plan_payload: dict, rc_payload: dict, run_id: str,
             prov: Provenance) -> RunResult:
        """Build a fresh runtime, run one schedule, and assemble a COMPLETED RunResult.
        Any exception raised here is caught by ``submit`` and turned into EXECUTION_FAILURE."""
        # 1-3. fresh runtime <- snapshot; apply RunConfig; run (order: modes before run).
        outage = OutageData.from_dict(plan_payload)
        pert = Pert(outage_data=outage, seed=int(rc_payload.get("seed", 42)))

        modes = rc_payload.get("mode_selections") or {}
        if modes:
            pert.set_modes(dict(modes))

        sgs = rc_payload.get("sgs", "max_use_res_ranked")
        priority_rule = rc_payload.get("priority_rule", "lf")
        horizon = rc_payload.get("scheduling_horizon_hours")  # None -> engine default
        result = pert.calculateScheduleWithResources(
            sgs=sgs, max_time_hours=horizon, priority_rule=priority_rule)

        # 4. output DTOs.
        schedule = self._build_schedule(pert, result)

        # 5-6. audit (validate_schedule ONCE) + dependency check -> Issues.
        validation = pert.validate_schedule()
        audit_issues = self._map_audit(validation)
        dep_violations, _dep_feasible = pert.check_dependency_violations()
        dep_issues = self._map_dependency(dep_violations)
        issues = tuple(audit_issues) + tuple(dep_issues)

        # diagnostics: fitness (post-hoc weights) + the dependency issues + the demand-
        # vs-capacity timeline. The pools come from the effective-plan payload (hour-offset,
        # same project-start anchor as the schedule hours); the pure builder is the only
        # invoker path, so plot_resource_utilization is never called.
        weights = rc_payload.get("evaluation_weights")
        pools = ser.load_plan_content(plan_payload).resources
        diagnostics = DiagnosticsDTO(
            fitness=self._build_fitness(pert, weights),
            dependency_violations=tuple(dep_issues),
            resource_utilization=build_resource_utilization(schedule, pools))

        # 7. disposition via the single pure domain policy (not re-derived here).
        n_unscheduled = int(result["n_activities"]) - int(result["n_completed"])
        summary = ScheduleSummary(produced=True, n_unscheduled=n_unscheduled, audit_ran=True)
        disposition = compute_disposition(summary, issues)

        return RunResult(
            run_id=run_id, status=RunResultStatus.COMPLETED, provenance=prov,
            issues=issues, disposition=disposition, schedule=schedule,
            diagnostics=diagnostics)

    # --- output mapping -------------------------------------------------------
    def _build_schedule(self, pert: Pert, result: dict) -> ScheduleDTO:
        makespan = q(float(result["scheduled_duration"]))
        cpm = q(float(result["cpm_duration"]))
        return ScheduleDTO(
            makespan_hours=makespan,
            cpm_lower_bound_hours=cpm,
            optimism_gap_hours=q(makespan - cpm),
            activities=self._build_activities(pert),
            constrained_chain=tuple(a.returnName() for a in pert.constrained_chain_list),
            cpm_critical_path=tuple(a.returnName() for a in pert.getCriticalPath()),
        )

    def _build_activities(self, pert: Pert) -> tuple[ScheduledActivityDTO, ...]:
        """One DTO per scheduled node, mirroring ``get_schedule_dataframe`` node selection
        (iterate ``forwardDict``; skip un-timed nodes). Sorted by (start_hour, task_id) so
        two identical runs emit a byte-identical order (the A→B→A invariant)."""
        start_time = pert.startTime
        constrained_set = pert.constrained_chain_set
        tf_map = pert.actual_tf
        records: list[ScheduledActivityDTO] = []
        for act in pert.forwardDict.keys():
            st, et = act.returnAbsTimes()
            if st is None or et is None:
                continue
            on_chain = act in constrained_set
            tf_actual = tf_map.get(act)
            records.append(ScheduledActivityDTO(
                task_id=act.returnName(),
                start_hour=q((st - start_time).total_seconds() / 3600.0),
                end_hour=q((et - start_time).total_seconds() / 3600.0),
                duration=q(max(0.0, float(act.duration))),
                delay_hours=q(float(act.delay)) if act.delay is not None else 0.0,
                on_constrained_chain=on_chain,
                float_class=classify_float(tf_actual, on_chain),
                description=act.returnDescription(),
                tf_actual_hours=(None if tf_actual is None else q(float(tf_actual))),
                actual_resources=self._actual_resources(act),
                wbs_group=getattr(act, "wbs_group", None),
            ))
        records.sort(key=lambda r: (r.start_hour, r.task_id))
        return tuple(records)

    @staticmethod
    def _actual_resources(act) -> tuple[ActualResource, ...]:
        """``activity._actual_resources`` (``{skill: workers}`` post-substitution) -> a
        deterministic tuple. Missing/None/empty all map to ``()``."""
        raw = getattr(act, "_actual_resources", None) or {}
        return tuple(
            ActualResource(skill_type=str(skill), crew_count=int(count))
            for skill, count in sorted(raw.items())
        )

    @staticmethod
    def _build_fitness(pert: Pert, weights) -> FitnessDTO:
        """``compute_fitness`` -> the 6-field FitnessDTO (1:1 with the model-spec fitness
        keys). Post-hoc only: weights change comparison, never the search objective."""
        if weights is None:
            f = pert.compute_fitness()
        else:
            f = pert.compute_fitness(
                weights["alpha"], weights["beta"], weights["gamma"], weights["delta"])
        return FitnessDTO(
            composite=float(f["composite"]),
            makespan_ratio=float(f["makespan_ratio"]),
            delay_ratio=float(f["delay_ratio"]),
            criticality_ratio=float(f["criticality_ratio"]),
            window_violation_ratio=float(f["window_violation_ratio"]),
            n_window_violations=int(f["n_window_violations"]),
        )

    # --- audit / dependency -> Issues ----------------------------------------
    def _map_audit(self, validation) -> list[Issue]:
        """Map every ``Violation`` in both ``violations`` and ``warnings`` to an Issue
        (adapter §6): severity passed through from the Violation; code reused where the
        meaning is identical to a catalogue code, else ``AUDIT_<TYPE>``."""
        issues: list[Issue] = []
        for v in list(validation.violations) + list(validation.warnings):
            code, category = self._audit_code_category(v.type)
            issues.append(Issue(
                code=code, severity=_severity(v.severity), category=category,
                message=v.detail, entity_type="task", entity_id=v.activity))
        return issues

    @staticmethod
    def _audit_code_category(vtype: str):
        code, category = _AUDIT_MAP.get(vtype, (None, IssueCategory.FEASIBILITY))
        if code is None:
            code = "AUDIT_" + str(vtype).upper()
        return code, category

    @staticmethod
    def _map_dependency(dep_violations) -> list[Issue]:
        """Map each precedence-violation dict (§7) to a DEP_VIOLATION Issue keyed on the
        successor; unifies with the audit's ``precedence`` producer on the same code."""
        issues: list[Issue] = []
        for v in dep_violations:
            msg = (
                f"{v['successor']} starts at {v['succ_start_time']} before predecessor "
                f"{v['predecessor']} ends at {v['pred_end_time']} "
                f"(overlap {v['overlap_hours']}h, lag {v['lag_hours']}h)"
            )
            issues.append(Issue(
                code=IssueCode.DEP_VIOLATION, severity=Severity.ERROR,
                category=IssueCategory.FEASIBILITY, message=msg,
                entity_type="dependency", entity_id=str(v["successor"])))
        return issues
