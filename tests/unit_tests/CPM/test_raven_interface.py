"""
Regression tests for the RAVEN <-> CPM interface (``BaseCPMmodel.run()``).

These guard the variable-unpacking path that translates RAVEN-sampled values
into ``Pert`` duration/priority updates. That path previously crashed for the
common duration-only case (empty ``itemgetter(*[])``, ndarray values, and
RAVEN-variable names used as activity IDs); see
``src/CPM/devLogs/RAVEN_PERT_INTERFACE_2026-09-07.md``.

``BaseCPMmodel`` imports ``ravenframework`` at module load, so the whole module
is skipped when RAVEN is not installed (e.g. the stand-alone CPM dev env). The
tests instantiate the model via ``__new__`` to bypass the RAVEN base-class
``__init__`` and drive the *real* ``run()`` method directly.
"""

import numpy as np
import pytest

from conftest import SCHEMA_PATH, EXAMPLES_DIR
from CPM.pert import Pert

# Skip the entire module if RAVEN is unavailable (import happens below).
pytest.importorskip("ravenframework")
from CPM.BaseCPMmodel import BaseCPMmodel  # noqa: E402


EXAMPLE = str(EXAMPLES_DIR / "test_case_1.json")


class _Container:
    """Minimal stand-in for the RAVEN container object run() writes into."""
    pass


def _real_task_ids(n=2):
    """Return the first ``n`` non-artificial task IDs from the example."""
    p = Pert.from_json_file(EXAMPLE, SCHEMA_PATH)
    ids = [t["task_id"] for t in p.outage_data.tasks
           if t["task_id"] not in ("START", "END")]
    return ids[:n]


def _make_model(mapping, sgs="max_use_res_ranked", cptime="end_time",
                scheduled=None, example=EXAMPLE):
    """Build a BaseCPMmodel wired for run() without the RAVEN base __init__."""
    m = BaseCPMmodel.__new__(BaseCPMmodel)   # bypass ExternalModelPluginBase.__init__
    m.pert = Pert.from_json_file(example, SCHEMA_PATH)
    m.pert.generateInfo()                    # mirrors BaseCPMmodel.initialize()
    m.mapping = mapping
    m.sgs = sgs
    m.CPtime = cptime
    m.scheduled_time = scheduled             # None => run() reports only <CPtime>
    return m


def test_run_duration_only_sets_finite_cptime():
    """Duration-only model (the primary case) must produce a finite CPtime."""
    id1, id2 = _real_task_ids(2)
    m = _make_model({"R_1": (id1, "duration"), "R_2": (id2, "duration")})
    c = _Container()
    # RAVEN delivers each realization as a 1-element ndarray.
    m.run(c, {"R_1": np.array([8.0]), "R_2": np.array([12.0])})
    val = float(c.__dict__["end_time"])
    assert np.isfinite(val) and val > 0.0


def test_run_single_duration_var():
    """A single duration map must not trip the old scalar/iterable bug."""
    (id1,) = _real_task_ids(1)
    m = _make_model({"R_1": (id1, "duration")})
    c = _Container()
    m.run(c, {"R_1": np.array([10.0])})
    assert np.isfinite(float(c.__dict__["end_time"]))


def test_run_translates_raven_name_to_activity_id():
    """RAVEN variable names differ from activity IDs; mapping must translate."""
    id1, id2 = _real_task_ids(2)
    # deliberately non-matching RAVEN names
    m = _make_model({"R_dur_a": (id1, "duration"), "R_dur_b": (id2, "duration")})
    c = _Container()
    m.run(c, {"R_dur_a": np.array([8.0]), "R_dur_b": np.array([12.0])})
    assert np.isfinite(float(c.__dict__["end_time"]))


def test_run_missing_variable_raises_ioerror():
    """A mapped variable absent from inputDict must raise a clear IOError."""
    (id1,) = _real_task_ids(1)
    m = _make_model({"R_missing": (id1, "duration")})
    with pytest.raises(IOError):
        m.run(_Container(), {"R_other": np.array([1.0])})


def test_run_exposes_resource_constrained_scheduled_time():
    """<scheduled_time> reports the resource-constrained makespan, which is
    distinct from the CPM length in <CPtime>. On example_10 the CPM length is
    71 h while the resource-constrained schedule is 85 h, so with resources
    binding the two outputs must differ (sched > cpm)."""
    example = str(EXAMPLES_DIR / "example_10.json")
    m = _make_model({}, scheduled="sched_time", example=example)
    c = _Container()
    m.run(c, {})                       # no sampled vars: exercise the plain path
    cpm   = float(c.__dict__["end_time"])
    sched = float(c.__dict__["sched_time"])
    assert np.isfinite(cpm) and np.isfinite(sched)
    assert sched > cpm                 # resource contention stretches the makespan


def test_run_omits_scheduled_time_when_not_declared():
    """Without a <scheduled_time> node, run() writes only <CPtime> -- no extra
    output leaks into the container (backward-compatible with existing decks)."""
    id1, id2 = _real_task_ids(2)
    m = _make_model({"R_1": (id1, "duration"), "R_2": (id2, "duration")})
    c = _Container()
    m.run(c, {"R_1": np.array([8.0]), "R_2": np.array([12.0])})
    assert "end_time" in c.__dict__
    assert "sched_time" not in c.__dict__
