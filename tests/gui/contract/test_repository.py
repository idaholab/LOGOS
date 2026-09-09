"""RepositoryPort — in-memory entity store round-trip.

Not one of the lettered contract groups (the RepositoryPort is a persistence seam the
skeleton names but leaves impl-free until needed); the plan builds the in-memory impl in
Step 3, so this pins its round-trip and unknown-id contract. Save-then-load returns the
same immutable entity for each entity type; loading an unknown id raises KeyError (a
typed error, never a silent None).
"""

from __future__ import annotations

import pytest

from prismGui.infrastructure.memory_repository import InMemoryRepository
from prismGui.ports.repository import RepositoryPort


class TestRepositoryRoundTrip:

    def test_repo_satisfies_the_port(self):
        """The impl structurally conforms to the runtime-checkable RepositoryPort."""
        assert isinstance(InMemoryRepository(), RepositoryPort)

    def test_baseline_scenario_config_result_roundtrip(
        self, baseline, scenario, run_config, run_result):
        """Each entity type saves and loads back byte-identical (frozen dataclasses, so
        equality is structural)."""
        repo = InMemoryRepository()
        repo.save_baseline(baseline)
        repo.save_scenario(scenario)
        repo.save_run_config(run_config)
        repo.save_run_result(run_result)
        assert repo.load_baseline(baseline.plan_id) == baseline
        assert repo.load_scenario(scenario.scenario_id) == scenario
        assert repo.load_run_config(run_config.run_config_id) == run_config
        assert repo.load_run_result(run_result.run_id) == run_result

    def test_unknown_id_raises_keyerror(self):
        """load_* on an unknown id raises KeyError, not a silent None."""
        repo = InMemoryRepository()
        for load in (repo.load_baseline, repo.load_scenario,
                     repo.load_run_config, repo.load_run_result):
            with pytest.raises(KeyError):
                load("no-such-id")
