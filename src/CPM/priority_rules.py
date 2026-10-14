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


# Weights of the composite rules (see doc/priority_rules_doc.md §5.7–5.8).
WRUP_W_SUCC: float = 0.7
"""WRUP weight on the immediate-successor count (Liu et al. 2025, Table A1)."""
WACRU_W: float = 0.3
"""WACRU weight on successor criticality (Nekoueian et al. 2023, Table 8 footnote)."""
WACRU_ALPHA: float = 0.5
"""WACRU exponent on successor slack (Wang et al. 2017)."""


def _static(key, direction, normalize=True, metric=None, table4_id=None, description=''):
    return RuleSpec(key, direction, 'static', metric or key, normalize, table4_id, description)


def _dynamic(key, direction, table4_id=None, description='', normalize=True):
    return RuleSpec(key, direction, 'dynamic', None, normalize, table4_id, description)


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

# Table 4 rules added in Phase 2 (static).  Appended after the legacy rules so
# the legacy order of PRIORITY_RULE_NAMES is unchanged.
_RULES += [
    # Activity information
    _static('lpt', 'max', metric='duration', table4_id=1, description='Longest processing time'),
    _static('act_index', 'min', table4_id=43, description='Minimum task number (input order)'),
    # Network information
    _static('mis', 'max', metric='n_imm_succ', table4_id=3, description='Most immediate successors'),
    _static('lis', 'min', metric='n_imm_succ', table4_id=4, description='Least immediate successors'),
    _static('mts_exact', 'max', table4_id=5, description='Most total successors (exact count)'),
    _static('mtp_exact', 'max', description='Most total predecessors (exact count)'),
    _static('lts', 'min', metric='mts_exact', table4_id=6, description='Least total successors'),
    _static('np', 'min', metric='n_imm_pred', table4_id=38,
            description='Least immediate predecessors (activity-level adaptation)'),
    _static('lfs', 'min', table4_id=7, description='Least float per immediate successor'),
    _static('lnrj', 'min', table4_id=8, description='Least non-related jobs'),
    _static('lpf', 'max', metric='level', table4_id=9,
            description='Longest path following (activity count)'),
    _static('ms_l', 'max', table4_id=10, description='Most total successors per level'),
    _static('grpw_imm', 'max', table4_id=11,
            description='Greatest rank positional weight (immediate successors)'),
    _static('rpw', 'max', table4_id=11, description='Rank positional weight (all successors)'),
    _static('gprpw', 'max', table4_id=12,
            description='Greatest proportional rank positional weight'),
    _static('lrpw', 'min', metric='rpw', table4_id=13,
            description='Least rank positional weight (all successors)'),
    _static('lrpw_imm', 'min', metric='grpw_imm', table4_id=13,
            description='Least rank positional weight (immediate successors)'),
    _static('mtspt', 'max', table4_id=19, description='Most total successors processing time'),
    _static('tts_alb', 'max', table4_id=45, description='Task time plus total successors'),
    _static('sts', 'max', table4_id=46, description='Maximum average time of total successors'),
    _static('tssk', 'max', table4_id=47, description='Total successors divided by (slack + 1)'),
    # Resource information
    _static('grr', 'max', table4_id=20, description='Greatest resource requirement'),
    _static('srr', 'min', metric='grr', table4_id=21, description='Smallest resource requirement'),
    _static('srd', 'min', metric='grd', table4_id=21, description='Smallest resource demand'),
    _static('gcrr', 'max', table4_id=22,
            description='Greatest cumulative resource work content (immediate successors)'),
    _static('scrr', 'min', metric='gcrr', table4_id=23,
            description='Smallest cumulative resource work content (immediate successors)'),
    _static('trd', 'min', metric='grr', table4_id=24, description='Total resource demand'),
    _static('trs', 'min', table4_id=25, description='Total resource scarcity'),
    _static('wacru', 'max', table4_id=29,
            description='Weighted activity criticality and resource utilization'),
    _static('wrup', 'max', table4_id=30,
            description='Weighted resource utilization and precedence'),
    # Scheduling information
    _static('mslk', 'min', metric='slack', table4_id=18, description='Minimum slack'),
    _static('max_lst', 'max', metric='ls', table4_id=54, description='Maximum latest start time'),
    _static('max_lft', 'max', metric='lf', table4_id=55, description='Maximum latest finish time'),
]

# Table 4 dynamic rules added in Phase 3, evaluated by Pert._dynamic_rule_values.
# Their values can be negative (msld, drc, maxsp) or infinite (drs), which
# max-normalization would turn into flipped signs or NaN, so raw values are kept.
_RULES += [
    _dynamic('rsm', 'min', table4_id=36, description='Resource scheduling method', normalize=False),
    _dynamic('estd', 'min', table4_id=31, description='Earliest start time (dynamic)', normalize=False),
    _dynamic('eftd', 'min', table4_id=32, description='Earliest finish time (dynamic)', normalize=False),
    _dynamic('msld', 'min', table4_id=33, description='Minimum slack (dynamic)', normalize=False),
    _dynamic('drd', 'min', table4_id=26, description='Dynamic relative resource demand', normalize=False),
    _dynamic('drc', 'min', table4_id=27, description='Dynamic remaining capacity', normalize=False),
    _dynamic('drs', 'min', table4_id=28, description='Dynamic resource scarcity', normalize=False),
    _dynamic('nondep', 'min', table4_id=34,
             description='Least non-dependent eligible jobs (LOGOS interpretation)', normalize=False),
    _dynamic('maxsp', 'max', table4_id=35, description='Maximum schedule pressure', normalize=False),
]
PHASE3_DYNAMIC_RULES = frozenset(
    ('rsm', 'estd', 'eftd', 'msld', 'drd', 'drc', 'drs', 'nondep', 'maxsp'))
"""Dynamic rules evaluated by ``Pert._dynamic_rule_values``."""

PRIORITY_RULES: Dict[str, RuleSpec] = {spec.key: spec for spec in _RULES}
"""All rules accepted by ``Pert.priority_calculation()``, keyed by name."""

EXTENDED_METRIC_KEYS: Tuple[str, ...] = (
    'act_index', 'n_imm_succ', 'n_imm_pred', 'mts_exact', 'mtp_exact', 'level',
    'lnrj', 'ms_l', 'lfs', 'grpw_imm', 'rpw', 'gprpw', 'mtspt', 'sts', 'tssk',
    'tts_alb', 'grr', 'work_content', 'trs', 'gcrr', 'wrup', 'wacru',
)
"""infoDict keys written by ``Pert.calculate_extended_priority_metrics``."""

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
