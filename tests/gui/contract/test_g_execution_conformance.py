"""Contract group G — Execution-port conformance.

Runs against the `executor` fixture, parametrized so every ExecutionPort impl satisfies
the SAME contract. Phase-1 pure-contract job: the FAKE synchronous in-process executor
(no PRISM); the real in-process PRISM executor joins the parametrization in Step 5 and
must pass this identical suite.

Verified here: submit returns a resolvable RunId; status reaches a terminal state;
get_result is valid at terminal and its provenance matches the submitted request; a
missing snapshot surfaces as a FAILED result with SNAPSHOT_MISSING (never a raw
exception); unknown run_ids raise a typed KeyError; cancel is accepted and idempotent.

Deferred (not written): ``test_get_result_before_terminal_has_defined_behavior`` — a
synchronous in-process executor completes inside submit(), so there is no pre-terminal
window to probe; it becomes meaningful only with the background/async executor.
"""

from __future__ import annotations

import pytest

from prismGui.domain.issues import IssueCode, Severity
from prismGui.domain.results import RunResult, RunResultStatus
from prismGui.ports.execution import ExecutionPort, RunStatus, TERMINAL_STATUSES


class TestExecutionPortConformance:

    def test_executor_satisfies_the_port(self, executor):
        """The impl structurally conforms to the runtime-checkable ExecutionPort."""
        assert isinstance(executor, ExecutionPort)

    def test_submit_returns_run_id(self, executor, run_request):
        """submit(request) returns a non-empty RunId that get_status can resolve."""
        run_id = executor.submit(run_request)
        assert isinstance(run_id, str) and run_id
        assert isinstance(executor.get_status(run_id), RunStatus)   # resolvable, no raise

    def test_status_reaches_terminal_state(self, executor, run_request):
        """After submit, status is terminal (the synchronous in-process impl is terminal
        immediately); a clean request completes rather than failing."""
        run_id = executor.submit(run_request)
        status = executor.get_status(run_id)
        assert status in TERMINAL_STATUSES
        assert status is RunStatus.COMPLETED

    def test_result_available_at_terminal_state(self, executor, run_request):
        """At a terminal status get_result returns a RunResult whose provenance echoes the
        submitted request's hashes."""
        run_id = executor.submit(run_request)
        assert executor.get_status(run_id) in TERMINAL_STATUSES
        result = executor.get_result(run_id)
        assert isinstance(result, RunResult)
        assert result.run_id == run_id
        assert result.provenance.effective_plan_hash == run_request.effective_plan_hash
        assert result.provenance.run_config_hash == run_request.run_config_hash
        assert (result.provenance.baseline_snapshot_hash
                == run_request.provenance_inputs.baseline_snapshot_hash)

    def test_missing_snapshot_surfaces_execution_failure(self, executor, run_request_dangling_hash):
        """A request whose snapshot hash is absent from the store: submit does NOT raise;
        the executor catches SnapshotNotFoundError and returns a FAILED RunResult carrying
        a SNAPSHOT_MISSING (ERROR) issue."""
        run_id = executor.submit(run_request_dangling_hash)
        assert executor.get_status(run_id) is RunStatus.FAILED
        result = executor.get_result(run_id)
        assert result.status is RunResultStatus.FAILED
        assert any(i.code is IssueCode.SNAPSHOT_MISSING and i.severity is Severity.ERROR
                   for i in result.issues)

    def test_unknown_run_id_has_defined_behavior(self, executor):
        """get_status / get_result / cancel on an unknown run_id raise a typed KeyError,
        not an unhandled/garbage error."""
        for call in (executor.get_status, executor.get_result, executor.cancel):
            with pytest.raises(KeyError):
                call("no-such-run")

    def test_cancel_is_accepted_and_idempotent(self, executor, run_request):
        """cancel on an immediate-complete run is a defined no-op and can be called
        repeatedly without error; the run stays terminal."""
        run_id = executor.submit(run_request)
        assert executor.get_status(run_id) in TERMINAL_STATUSES
        executor.cancel(run_id)
        executor.cancel(run_id)                                   # idempotent
        assert executor.get_status(run_id) in TERMINAL_STATUSES
