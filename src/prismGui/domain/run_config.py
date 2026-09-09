"""domain/run_config.py — how PRISM solves a (baseline + scenario).

Execution-mode SELECTION lives here and is applied by the adapter to the fresh
runtime — never to a working copy. Mirrors the skeleton and model-spec §4.
Pure: stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

Hours = float


class SGSVariant(str, Enum):
    MAX_USE_RES_RANKED = "max_use_res_ranked"      # default, production
    MAX_USE_RES_SHUFFLED = "max_use_res_shuffled"
    FIRST = "first"
    MD_KNAPSACK = "md_knapsack"
    LOOK_AHEAD = "look_ahead"


# The 22-rule priority-rule library (pert.py _list_priority_names). Descriptions
# and caveats live in prism-gui-priority-rules.md. An unrecognized key is a hard
# error in the engine (no silent default), so the GUI offers only these.
PRIORITY_RULES: tuple[str, ...] = (
    "lf", "ls", "ef", "es", "duration", "random",
    "mts", "mtp", "grpw", "grd", "rr", "avgrr", "maxrr", "minrr",
    "mehh_8000_b", "mehh_3375_b", "mehh_1000_b", "mehh_125_b",
    "gphh_b", "wcs", "acs", "irsm",
)
DEFAULT_PRIORITY_RULE = "lf"


@dataclass(frozen=True)
class EvaluationWeights:
    """Post-hoc fitness weights. They change how completed schedules are COMPARED;
    they do NOT change the GA/ALNS search objective."""
    alpha: float = 1.0
    beta: float = 0.5
    gamma: float = 0.3
    delta: float = 2.0


@dataclass(frozen=True)
class ModeSelection:
    """Tuple-backed record (not a dict) so RunConfig is deeply immutable."""
    task_id: str
    mode_name: str


@dataclass(frozen=True)
class RunConfig:
    run_config_id: str
    sgs: SGSVariant = SGSVariant.MAX_USE_RES_RANKED
    priority_rule: str = DEFAULT_PRIORITY_RULE          # one of the 22 library keys
    mode_selections: tuple[ModeSelection, ...] = ()
    seed: int = 42
    scheduling_horizon_hours: Optional[Hours] = None    # -> PRISM max_time_hours=; None -> engine default
    evaluation_weights: Optional[EvaluationWeights] = None
    # optimization: reserved for GA/ALNS (Phase 6); absent in Phase 1–2.
