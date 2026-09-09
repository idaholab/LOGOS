"""infrastructure/memory_snapshot_store.py — Phase-1 in-memory SnapshotStorePort.

A dict-backed, content-addressed blob store for the life of a session/process. The key
is ``hash_bytes(bytes)`` (domain.hashing), so ``put`` is idempotent (identical bytes
dedup to one entry) and the returned key equals the provenance hash for those bytes.
A persistent content-addressed store replaces this behind the same port later.

Infrastructure, but engine-free: it imports only ``domain.hashing`` and the port, never
PRISM. ``get`` on a missing hash raises the neutral ``SnapshotNotFoundError``.
"""

from __future__ import annotations

from prismGui.domain.hashing import hash_bytes
from prismGui.ports.snapshot_store import Canonical, Hash, SnapshotNotFoundError


class InMemorySnapshotStore:
    """SnapshotStorePort backed by a process-memory dict."""

    def __init__(self) -> None:
        # hash -> canonical snapshot string. Content-addressed, so re-putting identical
        # bytes is a no-op; exposed for integrity re-checks in tests, not for mutation.
        self._blobs: dict[Hash, Canonical] = {}

    def put(self, canonical_snapshot: Canonical) -> Hash:
        h = hash_bytes(canonical_snapshot)
        # Dedup: store the bytes once. A second put of identical bytes returns the same
        # key and does not overwrite (they are byte-identical anyway).
        self._blobs.setdefault(h, canonical_snapshot)
        return h

    def get(self, snapshot_hash: Hash) -> Canonical:
        try:
            return self._blobs[snapshot_hash]
        except KeyError:
            raise SnapshotNotFoundError(snapshot_hash) from None

    def contains(self, snapshot_hash: Hash) -> bool:
        return snapshot_hash in self._blobs
