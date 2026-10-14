"""
Unit tests for priority_rules.py - the priority-rule registry.

Run from repo root:
    pytest tests/unit_tests/CPM/test_priority_rules.py -v
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from src.CPM.cpm_utils import CUSTOM_PRIORITY_FUNCS  # noqa: E402
from src.CPM.pert import Pert  # noqa: E402
from src.CPM.priority_rules import (  # noqa: E402
    PRIORITY_RULES, PRIORITY_RULE_NAMES, SEED_PRIORITY_RULES, get_rule,
)
from src.CPM import ga, gans, gga, rcpsp_alns  # noqa: E402

EXAMPLES_DIR = REPO_ROOT / 'doc' / 'demos' / 'rcpsp' / 'examples'
JSON_PATH = str(EXAMPLES_DIR / 'j301_1.json')
SCHEMA = str(REPO_ROOT / 'src' / 'CPM' / 'outage_schema.json')

# Rule names and order before the registry was introduced.
LEGACY_RULE_NAMES = [
    'lf', 'ls', 'ef', 'es', 'duration', 'random',
    'mts', 'mtp', 'grpw', 'grd', 'rr', 'avgrr',
    'maxrr', 'minrr', 'mehh_8000_b', 'mehh_3375_b',
    'mehh_1000_b', 'mehh_125_b', 'gphh_b',
    'wcs', 'acs', 'irsm',
]
LEGACY_SEED_RULES = [
    'es', 'ef', 'ls', 'lf', 'duration', 'random',
    'mts', 'mtp', 'grpw', 'grd', 'rr', 'avgrr',
    'maxrr', 'minrr', 'irsm', 'wcs', 'acs',
    'mehh_8000_b', 'mehh_3375_b', 'mehh_1000_b', 'mehh_125_b', 'gphh_b',
]


@pytest.fixture(scope='module')
def pert():
    p = Pert.from_json_file(JSON_PATH, schema_path=SCHEMA)
    p.generateInfo()
    return p


def test_registry_keeps_legacy_names_and_order():
    assert list(PRIORITY_RULE_NAMES)[:len(LEGACY_RULE_NAMES)] == LEGACY_RULE_NAMES
    assert Pert()._list_priority_names == list(PRIORITY_RULE_NAMES)


def test_seed_rules_unchanged_and_shared():
    assert list(SEED_PRIORITY_RULES) == LEGACY_SEED_RULES
    for module_rules in (ga.PRIORITY_RULES, gga.PRIORITY_RULES,
                         gans.PRIORITY_RULES, rcpsp_alns.SEED_PRIORITY_RULES):
        assert module_rules == LEGACY_SEED_RULES
    assert set(SEED_PRIORITY_RULES) <= set(PRIORITY_RULES)


def test_spec_fields_are_consistent():
    for key, spec in PRIORITY_RULES.items():
        assert key == spec.key == key.lower()
        assert spec.kind in ('static', 'dynamic', 'random')
        if spec.kind == 'random':
            assert spec.direction is None
        else:
            assert spec.direction in ('min', 'max')
        if spec.kind == 'static':
            assert spec.metric
    for key in CUSTOM_PRIORITY_FUNCS:
        assert PRIORITY_RULES[key].direction == 'min'


def test_get_rule_is_case_insensitive():
    assert get_rule('LF') is PRIORITY_RULES['lf']
    with pytest.raises(IOError):
        get_rule('not_a_rule')


def test_priority_calculation_rejects_unknown_rule(pert):
    with pytest.raises(IOError):
        pert.priority_calculation(list(pert.forwardDict), 'not_a_rule')


@pytest.mark.parametrize('rule', [k for k, s in PRIORITY_RULES.items() if s.kind == 'static'])
def test_static_rule_follows_registered_direction(pert, rule):
    """Before dependency repair, values are sorted in the registered direction."""
    spec = PRIORITY_RULES[rule]
    acts = sorted(pert.forwardDict, key=lambda a: a.returnName())
    result = pert.priority_calculation(acts, rule)
    assert len(result) == len(acts)
    first_raw = pert.infoDict[result[0][0]][spec.metric]
    sources = [a for a in acts if not pert.backwardDict.get(a)]
    best_source = (min if spec.direction == 'min' else max)(
        pert.infoDict[a][spec.metric] for a in sources)
    # The first activity must be a source (dependency repair), and the best
    # source under the rule's direction.
    assert result[0][0] in sources
    assert first_raw == best_source
