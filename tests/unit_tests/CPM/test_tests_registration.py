"""
Guard: every CPM ``test_*.py`` file has a RAVEN registration.

The CPM suite is registered *per file* in ``tests`` (one ``RavenPython`` block
per test file) so a failure localizes to a single file on the CI dashboard.  The
cost of per-file registration is that a newly-added ``test_*.py`` will silently
never run in CI unless someone remembers to add its block — the opposite of the
old single-aggregate shim, which auto-discovered new files.

This test closes that gap: it parses ``tests``, collects every ``.py`` file
named in an ``input = '...'`` value, and asserts that every sibling
``test_*.py`` (this file included) is registered at least once.  A forgotten
registration then fails loudly here instead of hiding.
"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS_FILE = HERE / "tests"

# The generic runner shim is what `input` points at; the *test file* is its
# argument.  Exclude the shim itself from the "registered test files" set.
RUNNER = "run_cpm_pytests.py"

_INPUT_RE = re.compile(r"""input\s*=\s*['"]([^'"]*)['"]""")


def _registered_test_files():
    """Return the set of test_*.py filenames referenced by an input= value."""
    text = TESTS_FILE.read_text()
    registered = set()
    for value in _INPUT_RE.findall(text):
        for token in value.split():
            if token.endswith(".py") and token != RUNNER:
                registered.add(token)
    return registered


def _sibling_test_files():
    """Return the set of test_*.py filenames present in this directory."""
    return {p.name for p in HERE.glob("test_*.py")}


def test_tests_file_exists():
    """The RAVEN registration file must be present and non-trivial."""
    assert TESTS_FILE.is_file(), f"missing RAVEN registration file: {TESTS_FILE}"
    assert TESTS_FILE.read_text().strip(), "tests file is empty"


def test_every_test_file_is_registered():
    """Every sibling test_*.py appears in at least one input= value."""
    present = _sibling_test_files()
    registered = _registered_test_files()
    missing = sorted(present - registered)
    assert not missing, (
        "these CPM test files have no RAVEN registration in `tests` (add a "
        "per-file [./cpm_<name>] block, else they never run in CI): "
        + ", ".join(missing))


def test_no_registration_points_at_a_missing_file():
    """Every registered test file actually exists (catches renames/typos)."""
    present = _sibling_test_files()
    registered = _registered_test_files()
    stale = sorted(f for f in registered if f not in present)
    assert not stale, (
        "these files are registered in `tests` but do not exist (stale after a "
        "rename/delete?): " + ", ".join(stale))
