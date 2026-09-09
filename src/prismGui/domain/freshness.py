"""domain/freshness.py — is a displayed result still current? (derived, never stored).

A ``RunResult`` carries no freshness flag: a cached flag would itself go stale. Instead
freshness is ALWAYS recomputed by comparing the result's provenance hashes against the
present lineage (model-spec §6). The distinction that matters to the UI:

  * STALE            — the baseline or the scenario the result was computed against has
                       since changed. The result is shown against inputs that no longer
                       hold; it is not deleted, just flagged.
  * DIFFERENT_CONFIG — baseline & scenario still match, but this result was produced with
                       a different run config than the one now selected. Not "stale" — a
                       config selection does not invalidate a past result.
  * CURRENT          — every lineage hash matches the present state.

Precedence: a lineage (baseline / scenario) change dominates a config change. Pure: stdlib only.
"""

from __future__ import annotations

from typing import Optional

from prismGui.domain.results import Freshness, Hash, RunResult


def assess_freshness(
    result: RunResult,
    current_plan_hash: Hash,
    current_scenario_hash: Optional[Hash],
    current_run_config_hash: Optional[Hash] = None,
) -> Freshness:
    """Derive freshness from provenance vs. the current lineage. STALE iff the baseline
    or scenario changed; DIFFERENT_CONFIG iff only the selected config changed; else
    CURRENT. ``current_run_config_hash=None`` means "config not being compared" —
    lineage-match then yields CURRENT."""
    prov = result.provenance
    if prov.baseline_snapshot_hash != current_plan_hash:
        return Freshness.STALE
    if prov.scenario_delta_hash != current_scenario_hash:
        return Freshness.STALE
    if current_run_config_hash is not None and prov.run_config_hash != current_run_config_hash:
        return Freshness.DIFFERENT_CONFIG
    return Freshness.CURRENT


def explain_freshness(
    result: RunResult,
    current_plan_hash: Hash,
    current_scenario_hash: Optional[Hash],
    current_run_config_hash: Optional[Hash] = None,
) -> tuple[str, ...]:
    """The ordered reason codes behind a result's freshness — the "why" a UI panel shows
    beside the STALE / DIFFERENT_CONFIG badge. Uses the SAME hash comparisons and the SAME
    precedence as ``assess_freshness`` (a lineage change dominates a config change):

      * a baseline and/or scenario change yields ``("baseline_changed",)`` /
        ``("scenario_changed",)`` (both when both changed) — the STALE reasons;
      * otherwise a config-only change yields ``("config_differs",)`` — the DIFFERENT_CONFIG
        reason (checked only when ``current_run_config_hash`` is not None);
      * an exact lineage+config match yields ``()`` (== CURRENT).
    """
    prov = result.provenance
    lineage: list[str] = []
    if prov.baseline_snapshot_hash != current_plan_hash:
        lineage.append("baseline_changed")
    if prov.scenario_delta_hash != current_scenario_hash:
        lineage.append("scenario_changed")
    if lineage:
        return tuple(lineage)
    if current_run_config_hash is not None and prov.run_config_hash != current_run_config_hash:
        return ("config_differs",)
    return ()
