"""Version constants stamped into hashes and provenance.

Split three ways on purpose (model-spec §6): the canonicalization scheme, the
outage-schema contract, and the GUI application move independently, so a stored
hash / provenance record says exactly which of each produced it. The PRISM engine
version is NOT here — it is resolved by the executor (infrastructure), since only
the adapter may import the engine.
"""

from __future__ import annotations

# Envelope `canon_version` (prism-gui-canonicalization.md §2). Bump when the
# canonical-serialization rules change; old hashes then never silently collide.
CANON_VERSION = "1"

# The outage_schema.json contract this GUI targets. Participates INSIDE the hash
# envelope, so a schema-version bump changes every hash even with identical field
# values (correct: it is a different contract). Plan meta may override per-plan.
SCHEMA_VERSION = "1"

# The PRISM GUI application version (provenance app_version).
APP_VERSION = "0.1.0"
