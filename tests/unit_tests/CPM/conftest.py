"""
conftest.py — pytest configuration for CPM unit tests.

Patches sys.modules so that the LOGOS root __init__.py (which requires a
RAVEN installation via .ravenconfig.xml) is never executed during test
collection or test runs.  Only the CPM sub-package is needed here.
"""

import sys
import types
from pathlib import Path

# ── path setup ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ── prevent LOGOS root __init__.py from running ───────────────────────────────
# The root __init__.py calls getPluginLoc() which requires .ravenconfig.xml
# (a RAVEN-plugin configuration file not present in standalone CPM tests).
# Pre-populate sys.modules with lightweight stubs so CPM imports still work.
if 'LOGOS' not in sys.modules:
    _logos = types.ModuleType('LOGOS')
    sys.modules['LOGOS'] = _logos

# Ensure src.CPM is importable as both 'src.CPM.*' and 'LOGOS.src.CPM.*'
import importlib as _il

def _ensure(name):
    if name not in sys.modules:
        try:
            sys.modules[name] = _il.import_module(name)
        except ImportError:
            sys.modules[name] = types.ModuleType(name)

_ensure('src')
_ensure('src.CPM')
_ensure('src.CPM.pert')
_ensure('src.CPM.ga')
