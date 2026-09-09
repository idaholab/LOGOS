"""ports/execution.py — the ExecutionPort seam and its request/status types.

A job-oriented contract (submit -> poll status -> read result -> cancel) so a Phase-1
in-process executor and a later background/queue executor satisfy the SAME interface.
The Phase-1 in-process impl may complete synchronously inside ``submit()`` — ``get_status``
then returns a terminal state immediately — keeping the UX synchronous without changing
the contract.

Snapshots are passed by HASH, never by value: ``prepare_run`` (Step 6) persists the
effective-plan and run-config bytes in the SnapshotStore, and the executor resolves the
hashes back to the schema-shaped snapshots it feeds to the engine. A ``RunRequest`` is
therefore a small, serializable execution package. Pure: depends only on the domain
(for the ``RunResult`` return type); imports neither PRISM nor Streamlit.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, runtime_checkable

from prismGui.domain.results import RunResult

Hash = str


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Terminal states: get_result is valid once status is one of these.
TERMINAL_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
)


@dataclass(frozen=True)
class ProvenanceInputs:
    """The hashes the executor needs to stamp ``RunResult.provenance``. The bytes each
    hash addresses are already in the SnapshotStore by the time a request is submitted
    (prepare_run guarantees it), so provenance is total and every hash resolves."""
    baseline_snapshot_hash: Hash
    effective_plan_hash: Hash
    run_config_hash: Hash
    schema_version: str
    canonicalization_version: str
    scenario_delta_hash: Optional[Hash] = None


@dataclass(frozen=True)
class RunRequest:
    """Serializable execution package produced by ``prepare_run()``. Snapshots are
    referenced by hash (bytes live in the SnapshotStore); the adapter resolves the
    hashes to schema-shaped ISO snapshots and feeds them to ``OutageData.from_dict``.
    ISO->hour conversion happens on the adapter's OUTPUT side (adapter spec §1)."""
    effective_plan_hash: Hash          # resolve via SnapshotStore
    run_config_hash: Hash              # resolve via SnapshotStore
    provenance_inputs: ProvenanceInputs
    request_id: Optional[str] = None


@runtime_checkable
class ExecutionPort(Protocol):
    """Job-oriented execution contract. Every implementation — the Phase-1 in-process
    executor, a fake test double, a future background executor — satisfies this same
    interface, so the conformance suite (group G) runs against each interchangeably.

    Behavior on out-of-band ids is part of the contract: ``get_status`` / ``get_result``
    / ``cancel`` on an unknown run_id raise ``KeyError`` (a typed error, never an
    unhandled crash). A missing snapshot never escapes ``submit`` as an exception — the
    executor catches ``SnapshotNotFoundError`` and returns a FAILED ``RunResult`` bearing
    a ``SNAPSHOT_MISSING`` issue."""

    def submit(self, request: RunRequest) -> str:
        """Enqueue (or run) the request; return a non-empty RunId resolvable by
        ``get_status``. A Phase-1 in-process impl may reach a terminal state before
        returning."""
        ...

    def get_status(self, run_id: str) -> RunStatus:
        """Current status. Raises ``KeyError`` for an unknown run_id."""
        ...

    def get_result(self, run_id: str) -> RunResult:
        """The result, valid once status is terminal. Raises ``KeyError`` for an unknown
        run_id."""
        ...

    def cancel(self, run_id: str) -> None:
        """Request cancellation. Idempotent and safe on an already-terminal run (a
        synchronous immediate-complete run stays terminal). Raises ``KeyError`` for an
        unknown run_id."""
        ...
