"""ports/snapshot_store.py — content-addressed blob storage seam.

Deliberately SEPARATE from the RepositoryPort: content-addressed immutable blobs
(``put`` returns the hash, dedup on identical bytes) are a different contract from
entity save/load-by-id. Every provenance hash in a ``RunResult`` must resolve to bytes
here — that is the invariant the executor and prepare_run uphold.

``put(x)`` returns ``hash_bytes(x)`` (domain.hashing) by construction, so the storage
key and the provenance hash for the same bytes can never diverge. Pure seam: this
module is a Protocol + a neutral exception, no I/O and no engine imports.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

Hash = str
Canonical = str


class SnapshotNotFoundError(Exception):
    """Raised by ``SnapshotStorePort.get()`` when a hash is absent. A DOMAIN-NEUTRAL
    storage exception: the executor catches it and converts it to a ``SNAPSHOT_MISSING``
    issue on a FAILED ``RunResult``, keeping storage mechanics separate from diagnostics
    and never letting a raw exception reach the UI."""

    def __init__(self, snapshot_hash: Hash) -> None:
        super().__init__(f"snapshot not found: {snapshot_hash}")
        self.snapshot_hash = snapshot_hash


@runtime_checkable
class SnapshotStorePort(Protocol):

    def put(self, canonical_snapshot: Canonical) -> Hash:
        """Store bytes; return their content hash. Idempotent (dedup by hash). The
        returned hash MUST equal the provenance hash computed for the same bytes."""
        ...

    def get(self, snapshot_hash: Hash) -> Canonical:
        """Retrieve bytes by hash. Raises ``SnapshotNotFoundError`` if absent."""
        ...

    def contains(self, snapshot_hash: Hash) -> bool:
        """True iff bytes for ``snapshot_hash`` are stored."""
        ...
