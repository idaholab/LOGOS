"""Contract group D — Freshness / lineage (derived, never stored).

All four are pure: they call ``assess_freshness`` against a hand-built RunResult whose
provenance references the current baseline. Freshness is a function of provenance vs.
present lineage, so a baseline change is STALE (result kept, not deleted), a config-only
change is DIFFERENT_CONFIG (not stale), an exact-lineage match is CURRENT, and no
freshness flag is ever stored on the result itself.
"""

from __future__ import annotations

import dataclasses

from prismGui.domain.freshness import assess_freshness, explain_freshness
from prismGui.domain.results import Freshness


class TestFreshness:

    def test_baseline_change_marks_displayed_result_stale_not_deleted(self, baseline, run_result):
        """After a baseline edit (its hash changes), the displayed result assesses STALE
        and remains fully present/viewable — nothing is deleted."""
        new_baseline_hash = "0123456789abcdef" * 4
        assert new_baseline_hash != baseline.plan_hash
        freshness = assess_freshness(run_result, new_baseline_hash, current_scenario_hash=None)
        assert freshness is Freshness.STALE
        # the result object is untouched by the assessment
        assert run_result.schedule is not None
        assert run_result.run_id == "run-1"

    def test_selecting_different_rule_is_not_stale(self, run_result):
        """Lineage still matches but the currently-selected run config differs ->
        DIFFERENT_CONFIG, explicitly NOT STALE (a config selection cannot invalidate a
        past result)."""
        prov = run_result.provenance
        freshness = assess_freshness(
            run_result,
            current_plan_hash=prov.baseline_snapshot_hash,
            current_scenario_hash=prov.scenario_delta_hash,
            current_run_config_hash="a-different-config-hash",
        )
        assert freshness is Freshness.DIFFERENT_CONFIG

    def test_unchanged_lineage_is_current(self, run_result):
        """Assessed against the exact hashes it was produced from -> CURRENT."""
        prov = run_result.provenance
        freshness = assess_freshness(
            run_result,
            current_plan_hash=prov.baseline_snapshot_hash,
            current_scenario_hash=prov.scenario_delta_hash,
            current_run_config_hash=prov.run_config_hash,
        )
        assert freshness is Freshness.CURRENT

    def test_freshness_is_derived_not_stored(self, run_result):
        """RunResult carries no freshness/stale field: freshness exists only as the
        return of assess_freshness, never as stored state that could itself go stale."""
        field_names = {f.name for f in dataclasses.fields(run_result)}
        assert not any(
            token in name.lower()
            for name in field_names
            for token in ("fresh", "stale")
        ), f"RunResult must not store freshness; found {field_names}"


class TestExplainFreshness:
    """``explain_freshness`` returns the ordered reason codes behind a freshness verdict,
    using the SAME comparisons + precedence as ``assess_freshness`` (lineage dominates
    config). The provenance/freshness panel renders these as the 'why-stale' detail."""

    def test_baseline_change_yields_baseline_changed(self, run_result):
        new_baseline_hash = "0123456789abcdef" * 4
        reasons = explain_freshness(run_result, new_baseline_hash,
                                    current_scenario_hash=None)
        assert reasons == ("baseline_changed",)

    def test_config_only_change_yields_config_differs(self, run_result):
        prov = run_result.provenance
        reasons = explain_freshness(
            run_result,
            current_plan_hash=prov.baseline_snapshot_hash,
            current_scenario_hash=prov.scenario_delta_hash,
            current_run_config_hash="a-different-config-hash",
        )
        assert reasons == ("config_differs",)

    def test_exact_lineage_yields_no_reasons(self, run_result):
        prov = run_result.provenance
        reasons = explain_freshness(
            run_result,
            current_plan_hash=prov.baseline_snapshot_hash,
            current_scenario_hash=prov.scenario_delta_hash,
            current_run_config_hash=prov.run_config_hash,
        )
        assert reasons == ()

    def test_reasons_empty_iff_freshness_is_current(self, run_result):
        """Consistency with ``assess_freshness``: the reasons are empty exactly when the
        verdict is CURRENT (a lineage change is never silent)."""
        prov = run_result.provenance
        # lineage mismatch -> STALE and non-empty
        stale = assess_freshness(run_result, "f" * 64, current_scenario_hash=None)
        stale_reasons = explain_freshness(run_result, "f" * 64, current_scenario_hash=None)
        assert stale is Freshness.STALE and stale_reasons == ("baseline_changed",)
        # exact match -> CURRENT and empty
        current = assess_freshness(
            run_result, prov.baseline_snapshot_hash, prov.scenario_delta_hash,
            prov.run_config_hash)
        current_reasons = explain_freshness(
            run_result, prov.baseline_snapshot_hash, prov.scenario_delta_hash,
            prov.run_config_hash)
        assert current is Freshness.CURRENT and current_reasons == ()
