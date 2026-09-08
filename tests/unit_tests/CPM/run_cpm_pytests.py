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
(``test_ga.py`` needs ``deap``; ``test_rcpsp_alns.py`` needs ``alns``;
``test_raven_interface.py`` needs ``ravenframework``) using
``pytest.importorskip``.  When *some* tests still run, pytest reports the run as
a pass (exit code 0).  But when a file's *only* tests all self-skip, the module-
level ``importorskip`` skips at collection time, pytest collects **zero** items,
and it returns exit code 5 ("no tests collected") — which RavenPython would
otherwise treat as a failure.  ``main`` therefore normalizes exit code 5 to 0
(see the comment there); a dev/CI environment lacking an optional package no
longer reddens that file's registration.  The two standalone regression scripts
in this directory (``legacy_cpm_regression.py`` and ``psplib_regression.py``)
are *not* pytest files and are intentionally not run here — they are manual/dev
harnesses.

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
    @ Out, code, int, the pytest exit code with 5 ("no tests collected")
      normalized to 0 (0 == pass).
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
    code = subprocess.call([sys.executable, "-m", "pytest", *targets])
    # pytest exit code 5 == "no tests collected".  This is the *pass* case for a
    # file whose only tests all self-skip on a missing optional dependency
    # (e.g. test_raven_interface.py when ``ravenframework`` is absent): the
    # module-level ``pytest.importorskip`` skips at collection, leaving 0 items,
    # so pytest returns 5.  RavenPython treats any non-zero exit as a failure,
    # so without this remap a clean, expected skip would redden CI.  The remap
    # is safe because 5 is reserved for "nothing collected": a real
    # collection/import error is exit 2 and a genuine test failure is exit 1 —
    # neither is masked here.
    return 0 if code == 5 else code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
