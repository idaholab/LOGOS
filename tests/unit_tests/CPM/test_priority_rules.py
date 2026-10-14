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


# ---------------------------------------------------------------------------
# Phase 2: static Table 4 metrics on the worked example of Nekoueian,
# Servranckx & Vanhoucke (2023), Fig. 1c / Table 3, alternatives ignored.
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta  # noqa: E402

from src.CPM.activity import Activity  # noqa: E402
from src.CPM.outage_data import (  # noqa: E402
    EquipmentPool, LocationPool, ResourceAvailability, ResourcePool,
)
from src.CPM.priority_rules import (  # noqa: E402
    EXTENDED_METRIC_KEYS, WACRU_ALPHA, WACRU_W, WRUP_W_SUCC,
)

FIG_DUR = {'1': 2, '2': 3, '3': 1, '4': 6, '5': 5, '6': 3, '7': 4, '8': 2,
           '9': 3, '10': 2, '11': 5, '12': 1}
FIG_RES = {'1': 7, '2': 2, '3': 3, '4': 5, '5': 4, '6': 5, '7': 6, '8': 3,
           '9': 4, '10': 6, '11': 1, '12': 3}
FIG_SUCC = {'1': ['2', '3'], '2': ['4'], '3': ['4'], '4': ['9'], '5': ['6', '7'],
            '6': ['8'], '7': ['8'], '8': ['9'], '9': ['10', '11'], '10': ['12'],
            '11': ['12'], '12': []}
FIG_CAP = 10


@pytest.fixture(scope='module')
def fig():
    t0 = datetime(2026, 1, 1)
    acts = {k: Activity(k, v, required_resources=[{'skill_type': 'R', 'crew_count': FIG_RES[k]}])
            for k, v in FIG_DUR.items()}
    p = Pert(graph={acts[k]: [acts[s] for s in v] for k, v in FIG_SUCC.items()})
    rp = ResourcePool()
    rp.resources['R'] = ResourceAvailability(
        'R', [{'start_date': t0, 'end_date': t0 + timedelta(days=30), 'available_count': FIG_CAP}])
    p.crew_pool, p.equipment_pool, p.location_pool = rp, EquipmentPool(), LocationPool()
    p.startTime = t0
    p.generateInfo()
    return p, {a.name: p.infoDict[a] for a in p.forwardDict}


def test_extended_keys_present_on_every_activity(fig):
    p, _ = fig
    for a in p.forwardDict:
        assert set(EXTENDED_METRIC_KEYS) <= set(p.infoDict[a])


def test_paper_worked_example_scrr(fig):
    """N23 §4.2: SCRR(1) = 23, SCRR(5) = 59 (immediate-successor work content)."""
    _, info = fig
    assert info['1']['gcrr'] == pytest.approx(23)
    assert info['5']['gcrr'] == pytest.approx(59)


def test_paper_worked_example_rpw(fig):
    """N23 §4.2 gives LRPW(1)=28, LRPW(5)=25; 28 includes the 3→6 link, absent here."""
    _, info = fig
    assert info['1']['rpw'] == pytest.approx(23)   # 28 − d6 − d8 (reached only via the link)
    assert info['5']['rpw'] == pytest.approx(25)


def test_exact_counts_vs_path_counts(fig):
    """mts counts paths (legacy); mts_exact counts unique successors."""
    _, info = fig
    assert info['1']['mts_exact'] == 7           # {2, 3, 4, 9, 10, 11, 12}
    assert info['1']['mts'] == 14                # two paths 1→4 double the tail
    assert info['12']['mtp_exact'] == 11         # everything except 12 itself
    assert info['9']['mtp_exact'] == 8


def test_network_metrics(fig):
    _, info = fig
    n = len(FIG_DUR)
    e1, e9, e12 = info['1'], info['9'], info['12']
    assert (e1['n_imm_succ'], e1['n_imm_pred']) == (2, 0)
    assert e1['level'] == 6                      # 1-2-4-9-10-12
    assert e12['level'] == 1
    assert e1['lnrj'] == n - 0 - 7
    assert e9['lnrj'] == n - 8 - 3
    assert e1['ms_l'] == pytest.approx(7 / 6)
    assert e12['ms_l'] == 0


def test_rank_positional_weights(fig):
    _, info = fig
    e5 = info['5']
    assert e5['grpw_imm'] == 5 + 3 + 4
    assert e5['gprpw'] == pytest.approx(12 / 5)
    assert e5['mtspt'] == pytest.approx(25 - 5)
    assert e5['sts'] == pytest.approx(20 / 7)
    assert e5['tts_alb'] == 5 + 7
    tf5 = e5['ls'] - e5['es']
    assert e5['tssk'] == pytest.approx(7 / (tf5 + 1))
    assert e5['lfs'] == pytest.approx(tf5 / 2)


def test_resource_metrics(fig):
    _, info = fig
    e1 = info['1']
    assert e1['grr'] == 7
    assert e1['work_content'] == 14
    assert e1['trs'] == pytest.approx(7 / FIG_CAP)
    assert e1['wrup'] == pytest.approx(WRUP_W_SUCC * 2 + (1 - WRUP_W_SUCC) * 0.7)
    crit = sum((1 + info[s]['ls'] - info[s]['es']) ** (-WACRU_ALPHA) for s in ('2', '3'))
    assert e1['wacru'] == pytest.approx(WACRU_W * crit + (1 - WACRU_W) * 0.7)


def test_zero_duration_gprpw_is_finite():
    a, b = Activity('A', 0.0), Activity('B', 4.0)
    p = Pert(graph={a: [b], b: []})
    p.crew_pool, p.equipment_pool, p.location_pool = ResourcePool(), EquipmentPool(), LocationPool()
    p.startTime = datetime(2026, 1, 1)
    p.generateInfo()
    assert p.infoDict[a]['gprpw'] == pytest.approx(1 + 4)


@pytest.mark.parametrize('rule', [k for k, s in PRIORITY_RULES.items()
                                  if s.kind == 'static' and s.key not in LEGACY_RULE_NAMES])
def test_new_static_rule_produces_valid_schedule(rule):
    """Every new rule schedules j301_1 completely (parallel and serial SGS)."""
    p = Pert.from_json_file(JSON_PATH, schema_path=SCHEMA)
    p.calculateScheduleWithResources('max_use_res_ranked', priority_rule=rule)
    assert len(p.completed) == len(p.infoDict)
    q = Pert.from_json_file(JSON_PATH, schema_path=SCHEMA)
    res = q.calculateSerialScheduleWithResources(priority_rule=rule)
    assert res['n_completed'] == res['n_activities']
