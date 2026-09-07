#!/usr/bin/env python
"""
RAVEN entry point for the CPM pytest suite.

RAVEN's ``RavenPython`` test type runs ``python <input>`` and treats a zero exit
code as a pass.  This shim invokes pytest so the CPM pytest suite is exercised
through the RAVEN CI path.

Targeting a single file (per-file registrations)
------------------------------------------------
``RavenPython`` has no dedicated arguments parameter, but rook builds the
command as ``<python> <input>`` and runs it through the shell, so the ``input``
value may carry arguments.  The ``tests`` file registers one ``RavenPython``
block *per test file*::

    input = 'run_cpm_pytests.py test_replan.py'

which runs ``python run_cpm_pytests.py test_replan.py`` → pytest on that one
file.  Per-file registrations give per-file red/green localization on the CI
dashboard (a single aggregate registration reports only "the CPM suite failed",
and on a *timeout* rook kills pytest before its summary prints, so the aggregate
form cannot say which file hung).  With no positional argument this shim falls
back to running the whole directory (handy for a local ``python
run_cpm_pytests.py``).

The deep property profile
-------------------------
``--thorough`` selects the randomized, high-example-count Hypothesis profile
(see test_property_based.py) by exporting ``HYPOTHESIS_PROFILE=thorough`` before
pytest starts.  It is used only by the ``heavy`` nightly registration
(``cpm_property_deep``); the per-PR property registration passes no flag and so
runs the deterministic, modestly-sized "ci" profile.

Optional-dependency test files self-skip when their dep is absent
(``test_ga.py`` needs ``deap``; ``test_rcpsp_alns.py`` needs ``alns``) using
``pytest.importorskip``.  pytest reports a run with skips-but-no-failures as a
pass (exit code 0), so a dev environment lacking the optional packages does not
break CI.  The two standalone regression scripts in this directory
(``legacy_cpm_regression.py`` and ``psplib_regression.py``) are *not* pytest
files and are intentionally not run here — they are manual/dev harnesses.

Manual use::

    LOGOS/tests/unit_tests/CPM$ python run_cpm_pytests.py                 # whole dir
    LOGOS/tests/unit_tests/CPM$ python run_cpm_pytests.py test_replan.py  # one file
    LOGOS/tests/unit_tests/CPM$ python run_cpm_pytests.py --thorough test_property_based.py
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(argv):
    """
    Run pytest over the requested targets (or the whole directory).

    @ In, argv, list[str], arguments after the script name; an optional
      ``--thorough`` flag plus zero or more test-file names (relative to this
      directory).
    @ Out, code, int, the pytest process exit code (0 == pass).
    """
    args = list(argv)
    if "--thorough" in args:
        args.remove("--thorough")
        # Deep, randomized exploration for the nightly `heavy` run.  Set here
        # (rather than in the command line) because RavenPython appends `input`
        # after the interpreter, leaving no room to prefix an env assignment.
        os.environ["HYPOTHESIS_PROFILE"] = "thorough"

    # Remaining args are test files relative to this directory; resolve them so
    # the run is independent of the caller's working directory.  With none,
    # run the whole directory (auto-discovers every test_*.py).
    targets = [str(HERE / a) for a in args] if args else [str(HERE)]
    return subprocess.call([sys.executable, "-m", "pytest", *targets])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
