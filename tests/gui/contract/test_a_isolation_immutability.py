"""Contract group A — Isolation & immutability (pure-domain slice).

Migrated from ``dev_docs/prism-gui-contract-tests.py`` group A. The two executor-
driven tests (running a scenario leaves the baseline byte-identical; A-B-A leaves no
residue) need the ExecutionPort + PRISM adapter and are written in Step 5's isolation
suite — they are deferred here (not collected) rather than stub-skipped, so the
negative-inference gate stays honest. The five tests below are pure: immutability of
the committed types, no-runtime-leak into RunResult, and the domain import boundary.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import textwrap
from datetime import datetime
from enum import Enum
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = str(_REPO_ROOT / "src")

# Scalars a neutral DTO tree may bottom out in.
_SCALAR = (str, int, float, bool, type(None), datetime)


def _assert_neutral(obj, path="run_result"):
    """Recursively assert `obj` is a neutral DTO: scalar / enum / tuple / or a frozen
    dataclass defined under ``prismGui.domain``. Any list/dict/set or any object from
    another module (e.g. a live PRISM ``Pert``) fails — that is the leak this guards."""
    if isinstance(obj, _SCALAR) or isinstance(obj, Enum):
        return
    if isinstance(obj, tuple):
        for i, x in enumerate(obj):
            _assert_neutral(x, f"{path}[{i}]")
        return
    assert dataclasses.is_dataclass(obj) and not isinstance(obj, type), (
        f"{path}: non-neutral value {obj!r} of type {type(obj)}"
    )
    mod = type(obj).__module__
    assert mod.startswith("prismGui.domain"), (
        f"{path}: dataclass {type(obj).__name__} comes from non-domain module {mod!r}"
    )
    for f in dataclasses.fields(obj):
        _assert_neutral(getattr(obj, f.name), f"{path}.{f.name}")


class TestIsolationAndImmutability:

    def test_no_runtime_object_stored_on_domain_types(self, run_result):
        """No PRISM runtime / live object leaks into RunResult: every field resolves,
        recursively, to a neutral DTO / scalar / enum / tuple (no list/dict/foreign
        object anywhere in the tree)."""
        _assert_neutral(run_result)

    def test_reference_plan_is_deeply_immutable(self, baseline):
        """A committed ReferencePlan cannot be mutated: attribute assignment raises,
        its collections are tuples, and the nested PlanContent/PlanMeta are frozen too."""
        with pytest.raises(dataclasses.FrozenInstanceError):
            baseline.plan_id = "mutated"          # type: ignore[misc]
        for coll in (baseline.content.tasks, baseline.content.dependencies,
                     baseline.content.resources, baseline.content.equipment,
                     baseline.content.locations):
            assert isinstance(coll, tuple)
        with pytest.raises(dataclasses.FrozenInstanceError):
            baseline.content.meta.outage_id = "x"  # type: ignore[misc]
        with pytest.raises(AttributeError):        # tuples have no append
            baseline.content.tasks.append(object())  # type: ignore[attr-defined]

    def test_run_result_is_deeply_immutable(self, run_result):
        """RunResult and its DTOs are frozen and tuple-backed; no in-place edit of the
        result, its activities, or a nested actual-resource assignment."""
        with pytest.raises(dataclasses.FrozenInstanceError):
            run_result.run_id = "other"           # type: ignore[misc]
        assert isinstance(run_result.issues, tuple)
        assert isinstance(run_result.schedule.activities, tuple)
        first = run_result.schedule.activities[0]
        with pytest.raises(dataclasses.FrozenInstanceError):
            first.start_hour = 99.0               # type: ignore[misc]
        assert isinstance(first.actual_resources, tuple)
        with pytest.raises(dataclasses.FrozenInstanceError):
            first.actual_resources[0].crew_count = 7  # type: ignore[misc]

    def test_frozen_objects_have_no_mutable_dict_fields(self, scenario, run_config):
        """Regression guard: Scenario and RunConfig expose no mutable list/dict/set
        field (override & mode-selection collections are tuple-backed), and attribute
        assignment on either fails."""
        for obj in (scenario, run_config):
            for f in dataclasses.fields(obj):
                value = getattr(obj, f.name)
                assert not isinstance(value, (list, dict, set)), (
                    f"{type(obj).__name__}.{f.name} is a mutable {type(value).__name__}"
                )
        assert isinstance(scenario.duration_overrides, tuple)
        assert isinstance(run_config.mode_selections, tuple)
        with pytest.raises(dataclasses.FrozenInstanceError):
            scenario.scenario_id = "x"            # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            run_config.seed = 0                   # type: ignore[misc]

    def test_domain_imports_neither_streamlit_nor_prism(self):
        """Static layering invariant: importing every domain module in a fresh
        interpreter pulls in neither streamlit, nor the PRISM (CPM) package, nor
        jsonschema. Run in a subprocess so no other test's imports pollute sys.modules."""
        code = textwrap.dedent(
            """
            import importlib, sys
            mods = ["issues", "results", "plan", "scenario", "run_config",
                    "hashing", "serialization", "materialize", "disposition", "freshness",
                    "resource_util"]
            for m in mods:
                importlib.import_module("prismGui.domain." + m)
            banned = ("streamlit", "CPM", "jsonschema")
            leaked = sorted(
                name for name in sys.modules
                if any(name == b or name.startswith(b + ".") for b in banned)
            )
            assert not leaked, "domain pulled in forbidden modules: " + repr(leaked)
            print("OK")
            """
        )
        env = {**os.environ, "PYTHONPATH": _SRC}
        proc = subprocess.run([sys.executable, "-c", code],
                              capture_output=True, text=True, env=env)
        assert proc.returncode == 0, (
            f"domain import boundary violated:\nstdout={proc.stdout}\nstderr={proc.stderr}"
        )
        assert "OK" in proc.stdout
