"""
Priority-rule registry for the CPM/RCPSP scheduler.

Single source of truth for every priority rule accepted by
``Pert.priority_calculation()``: its sort direction, whether it is static or
dynamic, which ``infoDict`` metric it reads, and whether the raw values are
normalized.  The metaheuristics (``ga.py``, ``gga.py``, ``gans.py``,
``rcpsp_alns.py``) take their seeding rules from :data:`SEED_PRIORITY_RULES`.
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
    description : str
        Short human-readable name.
    components : tuple of str
        For ``kind == 'hybrid'`` only: the component rule keys.
    """
    key: str
    direction: Optional[str]
    kind: str
    metric: Optional[str] = None
    normalize: bool = True
    description: str = ''
    components: Tuple[str, ...] = ()


# Weights of the composite rules.
WRUP_W_SUCC: float = 0.7
"""WRUP weight on the immediate-successor count (Liu et al. 2025, Table A1)."""
WACRU_W: float = 0.3
"""WACRU weight on successor criticality (Nekoueian et al. 2023, Table 8 footnote)."""
WACRU_ALPHA: float = 0.5
"""WACRU exponent on successor slack (Wang et al. 2017)."""


def _static(key, direction, normalize=True, metric=None, description=''):
    return RuleSpec(key, direction, 'static', metric or key, normalize, description)


def _dynamic(key, direction, description='', normalize=True):
    return RuleSpec(key, direction, 'dynamic', None, normalize, description)


# Insertion order is the historical order of ``Pert._list_priority_names``.
_RULES: List[RuleSpec] = [
    _static('lf', 'min', description='Latest finish time'),
    _static('ls', 'min', description='Latest start time'),
    _static('ef', 'min', description='Earliest finish time'),
    _static('es', 'min', description='Earliest start time'),
    _static('duration', 'min', description='Shortest processing time'),
    RuleSpec('random', None, 'random', None, True, 'Random'),
    _static('mts', 'max', description='Most total successors (path count)'),
    _static('mtp', 'max', description='Most total predecessors (path count)'),
    _static('grpw', 'max', description='Rank positional weight over predecessors (LOGOS variant)'),
    _static('grd', 'max', description='Greatest resource demand'),
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

# Static extended rules.  Appended after the legacy rules so
# the legacy order of PRIORITY_RULE_NAMES is unchanged.
_RULES += [
    # Activity information
    _static('lpt', 'max', metric='duration', description='Longest processing time'),
    _static('act_index', 'min', description='Minimum task number (input order)'),
    # Network information
    _static('mis', 'max', metric='n_imm_succ', description='Most immediate successors'),
    _static('lis', 'min', metric='n_imm_succ', description='Least immediate successors'),
    _static('mts_exact', 'max', description='Most total successors (exact count)'),
    _static('mtp_exact', 'max', description='Most total predecessors (exact count)'),
    _static('lts', 'min', metric='mts_exact', description='Least total successors'),
    _static('np', 'min', metric='n_imm_pred',
            description='Least immediate predecessors (activity-level adaptation)'),
    _static('lfs', 'min', description='Least float per immediate successor'),
    _static('lnrj', 'min', description='Least non-related jobs'),
    _static('lpf', 'max', metric='level', description='Longest path following (activity count)'),
    _static('ms_l', 'max', description='Most total successors per level'),
    _static('grpw_imm', 'max',
            description='Greatest rank positional weight (immediate successors)'),
    _static('rpw', 'max', description='Rank positional weight (all successors)'),
    _static('gprpw', 'max', description='Greatest proportional rank positional weight'),
    _static('lrpw', 'min', metric='rpw',
            description='Least rank positional weight (all successors)'),
    _static('lrpw_imm', 'min', metric='grpw_imm',
            description='Least rank positional weight (immediate successors)'),
    _static('mtspt', 'max', description='Most total successors processing time'),
    _static('tts_alb', 'max', description='Task time plus total successors'),
    _static('sts', 'max', description='Maximum average time of total successors'),
    _static('tssk', 'max', description='Total successors divided by (slack + 1)'),
    # Resource information
    _static('grr', 'max', description='Greatest resource requirement'),
    _static('srr', 'min', metric='grr', description='Smallest resource requirement'),
    _static('srd', 'min', metric='grd', description='Smallest resource demand'),
    _static('gcrr', 'max',
            description='Greatest cumulative resource work content (immediate successors)'),
    _static('scrr', 'min', metric='gcrr',
            description='Smallest cumulative resource work content (immediate successors)'),
    _static('trd', 'min', metric='grr', description='Total resource demand'),
    _static('trs', 'min', description='Total resource scarcity'),
    _static('wacru', 'max', description='Weighted activity criticality and resource utilization'),
    _static('wrup', 'max', description='Weighted resource utilization and precedence'),
    # Scheduling information
    _static('mslk', 'min', metric='slack', description='Minimum slack'),
    _static('max_lst', 'max', metric='ls', description='Maximum latest start time'),
    _static('max_lft', 'max', metric='lf', description='Maximum latest finish time'),
]

# Dynamic extended rules, evaluated by Pert._dynamic_rule_values.
# Their values can be negative (msld, drc, maxsp) or infinite (drs), which
# max-normalization would turn into flipped signs or NaN, so raw values are kept.
_RULES += [
    _dynamic('rsm', 'min', description='Resource scheduling method', normalize=False),
    _dynamic('estd', 'min', description='Earliest start time (dynamic)', normalize=False),
    _dynamic('eftd', 'min', description='Earliest finish time (dynamic)', normalize=False),
    _dynamic('msld', 'min', description='Minimum slack (dynamic)', normalize=False),
    _dynamic('drd', 'min', description='Dynamic relative resource demand', normalize=False),
    _dynamic('drc', 'min', description='Dynamic remaining capacity', normalize=False),
    _dynamic('drs', 'min', description='Dynamic resource scarcity', normalize=False),
    _dynamic('nondep', 'min',
             description='Least non-dependent eligible jobs (LOGOS interpretation)', normalize=False),
    _dynamic('maxsp', 'max', description='Maximum schedule pressure', normalize=False),
]
DYNAMIC_RULES = frozenset(
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


HYBRID_PREFIX = 'hybrid:'
"""Prefix of hybrid rule names, e.g. ``'hybrid:lrpw+scrr+ms_l'``."""


def is_hybrid_rule(name: str) -> bool:
    """Return True if ``name`` uses the hybrid-rule syntax."""
    return isinstance(name, str) and name.lower().startswith(HYBRID_PREFIX)


def _hybrid_spec(name: str) -> RuleSpec:
    """
    Build the :class:`RuleSpec` of a hybrid rule ``'hybrid:<a>+<b>[+<c>...]'``.

    Following Nekoueian et al. (2023, §4.2), the component values are summed
    when all components share a direction; when directions are mixed, the sum
    of the min-rule values is divided by the sum of the max-rule values and
    the minimum is selected.
    """
    body = name.lower()[len(HYBRID_PREFIX):]
    keys = tuple(k.strip() for k in body.split('+'))
    if len(keys) < 2 or any(not k for k in keys):
        raise IOError(f"Invalid hybrid rule '{name}': expected 'hybrid:<rule>+<rule>[+...]'")
    for k in keys:
        spec = PRIORITY_RULES.get(k)
        if spec is None:
            raise IOError(f"Invalid hybrid rule '{name}': unknown component '{k}'")
        if spec.kind == 'random':
            raise IOError(f"Invalid hybrid rule '{name}': 'random' cannot be combined")
    directions = {PRIORITY_RULES[k].direction for k in keys}
    kinds = {PRIORITY_RULES[k].kind for k in keys}
    return RuleSpec(
        key=HYBRID_PREFIX + '+'.join(keys),
        direction='max' if directions == {'max'} else 'min',
        kind='hybrid',
        metric=None,
        normalize=False,
        description=('dynamic ' if 'dynamic' in kinds else '') + 'hybrid of ' + ', '.join(keys),
        components=keys,
    )


def get_rule(name: str) -> RuleSpec:
    """
    Return the :class:`RuleSpec` for ``name`` (case-insensitive).

    ``name`` is a registered rule key or a hybrid rule
    ``'hybrid:<a>+<b>[+<c>...]'`` built from registered keys.

    Raises
    ------
    IOError
        If ``name`` is not a registered priority rule or a valid hybrid.
    """
    if is_hybrid_rule(name):
        return _hybrid_spec(name)
    spec = PRIORITY_RULES.get(name.lower())
    if spec is None:
        raise IOError(f"Invalid priority rule: '{name}'")
    return spec
