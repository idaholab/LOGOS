"""
conftest.py — pytest configuration and shared fixtures for the CPM unit tests.

The source package lives at ``<repo>/src/CPM``.  ``pytest.ini`` puts ``src/`` on
sys.path (``pythonpath = ../../../src``), so tests import the engine directly as
``from CPM.pert import ...`` — a single import root, no sys.modules stubbing, and
no swallowing of ImportErrors (a missing optional dep surfaces as a real error,
which the affected test files convert to a skip via ``pytest.importorskip``).

``REPO_ROOT`` below is retained only to anchor the test-data paths.

Conventions
-----------
- "micro" fixtures are tiny hand-crafted graphs whose CPM values can be
  verified with pencil-and-paper.  They are the bedrock for correctness tests.
- "json_path" fixtures resolve absolute paths to the JSON data files shipped
  alongside the source code, so integration tests never depend on the current
  working directory.
"""

import pytest
from pathlib import Path

from CPM.activity import Activity
from CPM.pert import Pert
from CPM.schedule_validator import validate_schedule

# Repo root — anchors the test-data paths below (see SCHEMA_PATH / EXAMPLES_DIR).
REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Correctness assertion helper
# ---------------------------------------------------------------------------

def assert_valid_schedule(pert, msg: str = "") -> None:
    """Assert that validate_schedule reports no violations on a Pert output.

    Intended to be called at the end of any test that produces a complete
    schedule.  A validation failure prints the full human-readable summary
    so the cause is immediately visible in the pytest output.

    Args:
        pert: A Pert instance whose scheduler has already run.
        msg:  Optional extra context appended to the failure message.
    """
    result = validate_schedule(pert)
    if not result.is_feasible:
        detail = f": {msg}" if msg else ""
        pytest.fail(
            f"Scheduler output failed validation{detail}.\n\n"
            + result.summary()
        )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
#
# Test data lives in two canonical locations (see BRANCH_ASSESSMENT / H2):
#   - the JSON schema is a source artifact, kept next to the code so the tests
#     validate against the *shipping* schema and catch drift;
#   - the example / test-case networks were moved to the demo folder.
# Both are anchored to REPO_ROOT (defined above) so they are independent of the
# current working directory and of where the test tree itself lives.

SCHEMA_PATH  = str(REPO_ROOT / "src" / "CPM" / "outage_schema.json")
EXAMPLES_DIR = REPO_ROOT / "doc" / "demos" / "rcpsp" / "examples"


@pytest.fixture(scope="session")
def schema_path():
    return SCHEMA_PATH


@pytest.fixture(scope="session")
def json_example_10():
    return str(EXAMPLES_DIR / "example_10.json")


@pytest.fixture(scope="session")
def json_example_30():
    return str(EXAMPLES_DIR / "example_30.json")


@pytest.fixture(scope="session")
def json_test_case_1():
    return str(EXAMPLES_DIR / "test_case_1.json")


# ---------------------------------------------------------------------------
# Micro-network builders
# ---------------------------------------------------------------------------

def make_chain_pert(durations=None):
    """
    Build a linear chain: START(0) -> A -> B -> C -> END(0).

    Default durations: A=4, B=3, C=2  → project duration = 9 h.

    All activities are on the critical path (zero slack).
    """
    if durations is None:
        durations = {"A": 4.0, "B": 3.0, "C": 2.0}

    start = Activity("START", 0.0)
    a = Activity("A", durations["A"])
    b = Activity("B", durations["B"])
    c = Activity("C", durations["C"])
    end = Activity("END", 0.0)

    fwd = {start: [a], a: [b], b: [c], c: [end], end: []}
    return Pert(graph=fwd), start, a, b, c, end


def make_fork_join_pert():
    """
    Build a fork-join network:

        START(0)
           |
           A(4) ──> C(2) ──> END(0)
           B(6) ─────────────^

    CPM values:
        A: ES=0, EF=4, LS=2, LF=6, slack=2
        B: ES=0, EF=6, LS=0, LF=6, slack=0  ← critical
        C: ES=6, EF=8, LS=6, LF=8, slack=0  ← critical
        Project duration = 8 h
    Critical path: START → B → C → END
    """
    start = Activity("START", 0.0)
    a = Activity("A", 4.0)
    b = Activity("B", 6.0)
    c = Activity("C", 2.0)
    end = Activity("END", 0.0)

    fwd = {start: [a, b], a: [c], b: [c], c: [end], end: []}
    return Pert(graph=fwd), start, a, b, c, end


def make_lag_pert(lag_ab=2.0):
    """
    Build a chain with a FS lag on A→B:

        START(0) -> A(4) --[lag]--> B(3) -> END(0)

    Default lag = 2 h.
    CPM values (lag=2):
        A: ES=0, EF=4
        B: ES=6 (= EF_A + lag), EF=9
        Project duration = 9 h
    """
    start = Activity("START", 0.0)
    a = Activity("A", 4.0)
    a.successor_lags = {"B": lag_ab}
    b = Activity("B", 3.0)
    end = Activity("END", 0.0)

    fwd = {start: [a], a: [b], b: [end], end: []}
    p = Pert(graph=fwd)
    p.lag_dict = {(a, b): lag_ab}
    p.generateInfo()
    return p, start, a, b, end


@pytest.fixture
def chain_pert():
    """Minimal 3-activity chain.  Project duration = 9 h."""
    return make_chain_pert()


@pytest.fixture
def fork_join_pert():
    """Fork-join with one critical and one near-critical path."""
    return make_fork_join_pert()


@pytest.fixture
def lag_pert():
    """Chain with a 2-hour FS lag on A→B.  Project duration = 9 h."""
    return make_lag_pert()

