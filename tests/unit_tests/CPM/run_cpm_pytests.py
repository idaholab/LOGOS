#!/usr/bin/env python
"""
RAVEN entry point for the CPM pytest suite.

RAVEN's ``RavenPython`` test type runs ``python <input>`` and treats a zero exit
code as a pass.  This shim invokes pytest over this directory so the full CPM
pytest suite (~840 tests) is exercised through the RAVEN CI path via a single
registration (see tests/unit_tests/CPM/tests).

Optional-dependency test files self-skip when their dep is absent
(``test_ga.py`` needs ``deap``; ``test_rcpsp_alns.py`` needs ``alns``) using
``pytest.importorskip``.  pytest reports a run with skips-but-no-failures as a
pass (exit code 0), so a dev environment lacking the optional packages does not
break CI.

The two standalone regression scripts in this directory
(``legacy_cpm_regression.py`` and ``psplib_regression.py``) are *not* pytest
files and are intentionally not run here — they are manual/dev harnesses, not
CI gates.

To run manually:  LOGOS/tests/unit_tests/CPM$ python run_cpm_pytests.py
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", str(HERE)]))
