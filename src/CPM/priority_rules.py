"""
Priority-rule registry for the CPM/RCPSP scheduler.

Single source of truth for every priority rule accepted by
``Pert.priority_calculation()``: its sort direction, whether it is static or
dynamic, which ``infoDict`` metric it reads, and whether the raw values are
normalized.  The metaheuristics (``ga.py``, ``gga.py``, ``gans.py``,
``rcpsp_alns.py``) take their seeding rules from :data:`SEED_PRIORITY_RULES`.

See ``doc/priority_rules_doc.md`` for the definition of every rule and
``doc/priority_rules_dev.md`` for the rules still to be implemented.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .cpm_utils import CUSTOM_PRIORITY_FUNCS


@dataclass(frozen=True)
class RuleSpec:
    """
    Description of one priority rule.

    Attributes
    ----------
    key : str
        Rule name accepted by ``Pert.priority_calculation()`` (lower case).
    direction : str or None
        ``'min'`` — the activity with the smallest value is scheduled first;
        ``'max'`` — the largest value first; ``None`` for ``'random'``.
    kind : str
        ``'static'`` — value read from ``infoDict`` (computed once by
        ``generateInfo``); ``'dynamic'`` — recomputed at each decision time;
        ``'random'`` — random order.
    metric : str or None
        ``infoDict`` key holding the value of a static rule.  Lets several
        rules share one metric with different directions.
    normalize : bool
        Whether the raw values are scaled by their maximum
        (``normalize_tuples``) before being returned.
    table4_id : int or None
        Rule ID in Table 4 of Nekoueian, Servranckx & Vanhoucke (2023), when
        the rule appears there.
    description : str
        Short human-readable name.
    """
    key: str
    direction: Optional[str]
    kind: str
    metric: Optional[str] = None
    normalize: bool = True
    table4_id: Optional[int] = None
    description: str = ''


def _static(key, direction, normalize=True, metric=None, table4_id=None, description=''):
    return RuleSpec(key, direction, 'static', metric or key, normalize, table4_id, description)


def _dynamic(key, direction, table4_id=None, description=''):
    return RuleSpec(key, direction, 'dynamic', None, True, table4_id, description)


# Insertion order is the historical order of ``Pert._list_priority_names``.
_RULES: List[RuleSpec] = [
    _static('lf', 'min', table4_id=17, description='Latest finish time'),
    _static('ls', 'min', table4_id=16, description='Latest start time'),
    _static('ef', 'min', table4_id=15, description='Earliest finish time'),
    _static('es', 'min', table4_id=14, description='Earliest start time'),
    _static('duration', 'min', table4_id=2, description='Shortest processing time'),
    RuleSpec('random', None, 'random', None, True, 37, 'Random'),
    _static('mts', 'max', table4_id=5, description='Most total successors (path count)'),
    _static('mtp', 'max', description='Most total predecessors (path count)'),
    _static('grpw', 'max', table4_id=11,
            description='Rank positional weight over predecessors (LOGOS variant)'),
    _static('grd', 'max', table4_id=20, description='Greatest resource demand'),
    _static('rr', 'max', normalize=False, description='Fraction of resource types required'),
    _static('avgrr', 'max', normalize=False, description='Average normalized resource requirement'),
    _static('maxrr', 'max', normalize=False, description='Maximum normalized resource requirement'),
    _static('minrr', 'max', normalize=False, description='Minimum normalized resource requirement'),
]
_RULES += [
    _static(key, 'min', description='Evolved (GP) priority rule')
    for key in CUSTOM_PRIORITY_FUNCS
]
_RULES += [
    _dynamic('wcs', 'min', description='Worst case slack (Kolisch 1996)'),
    _dynamic('acs', 'min', description='Average case slack (Kolisch 1996)'),
    _dynamic('irsm', 'min', description='Improved resource scheduling method (Kolisch 1996)'),
]

PRIORITY_RULES: Dict[str, RuleSpec] = {spec.key: spec for spec in _RULES}
"""All rules accepted by ``Pert.priority_calculation()``, keyed by name."""

PRIORITY_RULE_NAMES: Tuple[str, ...] = tuple(PRIORITY_RULES)
"""Rule names in registry order."""

# Order matters: the metaheuristics seed their populations in this order, so
# changing it changes their results for a fixed random seed.
SEED_PRIORITY_RULES: Tuple[str, ...] = (
    'es', 'ef', 'ls', 'lf', 'duration', 'random',
    'mts', 'mtp', 'grpw', 'grd', 'rr', 'avgrr',
    'maxrr', 'minrr', 'irsm', 'wcs', 'acs',
    'mehh_8000_b', 'mehh_3375_b', 'mehh_1000_b', 'mehh_125_b', 'gphh_b',
)
"""Rules the metaheuristics use to seed their initial populations/solutions."""


def get_rule(name: str) -> RuleSpec:
    """
    Return the :class:`RuleSpec` for ``name`` (case-insensitive).

    Raises
    ------
    IOError
        If ``name`` is not a registered priority rule.
    """
    spec = PRIORITY_RULES.get(name.lower())
    if spec is None:
        raise IOError(f"Invalid priority rule: '{name}'")
    return spec
