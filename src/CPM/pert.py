
"""
Updated Pert Class for Nuclear Outage Planning

This class manages project scheduling using Critical Path Method (CPM) and
Resource-Constrained Project Scheduling (RCPSP) with support for time-varying
availability of resources, equipment, and locations.

Extended from the original development of Nofar Alfasi
Source https://github.com/nofaralfasi/PERT-CPM-graph
"""

import json
import copy
import random
import math
import numpy as np
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import networkx as nx
import heapq


# Assuming these are imported from your modules
from .activity import Activity
from .outage_data import ResourcePool, EquipmentPool, LocationPool, OutageData, load_outage_data
from .validate_outage_data import OutageDataValidator
from .cpm_utils import CUSTOM_PRIORITY_FUNCS, sigmoid_bipolar, sigmoid_inv, normalize_tuples

logging.basicConfig(level=logging.WARNING)
logging.disable(logging.CRITICAL)
logger = logging.getLogger(__name__)

class Pert:
    """
    Base class for a schedule as a set of activities linked by a graph structure.

    A graph is a map with activities as keys and list of outgoing activities as
    values for every key. The graph starts with a 'START' node and ends with an
    'END' node.
    """

    def __init__(self, graph=None, outage_data=None, priorities=None, seed=2506178):
        """
        Constructor for Pert scheduling system.

        Args:
            graph (dict, optional): Dictionary containing child activities for each activity
                                    Format: {Activity: [Activity, Activity, ...]}
            outage_data (OutageData, optional): OutageData object containing all planning data
            priorities (dict, optional): Dictionary mapping activity names to priority values
            seed (int): Random seed for reproducibility

        Note:
            Either provide 'graph' for manual construction, or 'outage_data' to load
            from JSON. If outage_data is provided, the graph will be built automatically.
        """
        # Initialize core data structures
        self.forwardDict = graph if graph is not None else {}
        self.backwardDict = {}
        self.infoDict = {}
        self.nxgraph = None
        self._max_time_factor = 10000
        self._list_priority_names = [
            'lf', 'ls', 'ef', 'es', 'duration', 'random',
            'mts', 'mtp', 'grpw', 'grd', 'rr', 'avgrr',
            'maxrr', 'minrr','mehh_8000_b','mehh_3375_b',
            'mehh_1000_b','mehh_125_b','gphh_b',
            # 'wcs', 'acs', 'irsm',
            ]

        self.task_to_activity = {} # dictionary in the form: {act_ID: act_instance}

        # Lag table: {(predecessor_Activity, successor_Activity): lag_hours}
        # Populated by _build_graph_from_outage_data().  Zero-lag edges are
        # absent (treat missing key as 0.0).
        self.lag_dict: dict = {}

        # Store outage data components
        self.outage_data = outage_data
        if outage_data:
            self.crew_pool = outage_data.crew_pool
            self.equipment_pool = outage_data.equipment_pool
            self.location_pool = outage_data.location_pool
            self.startTime = outage_data.start_date
            self.working_hours_per_day = outage_data.working_hours_per_day
        else:
            self.crew_pool = None
            self.equipment_pool = None
            self.location_pool = None
            self.startTime = None
            self.working_hours_per_day = 24

        # Shift calendar: hour-of-day at which each work shift begins.
        # Default 0 keeps 24/7 behaviour unchanged when working_hours_per_day=24.
        # For partial-day operations (e.g. working_hours_per_day=12, shift_start_hour=6)
        # activities are prevented from starting during off-shift hours and the
        # event queue is seeded with shift-start boundaries.
        self.shift_start_hour: int = getattr(outage_data, 'shift_start_hour', 0) if outage_data else 0

        # Dose budget trackers: one DoseBudgetTracker per consumable skill type.
        # Empty dict when no resource pool is attached or when no resources are
        # typed as 'consumable' — zero cost to existing code paths.
        self.dose_trackers: dict = (
            self.crew_pool.build_dose_trackers()
            if self.crew_pool else {}
        )

        # ConsumablePool: scalar-inventory items permanently depleted on start
        # (AC suits, gaskets, nitrogen cylinders, etc.).  None when outage_data
        # carries no consumable pool — all `if self.consumable_pool:` guards
        # make this safe.
        self.consumable_pool = (
            getattr(outage_data, 'consumable_pool', None)
            if outage_data else None
        )

        # SystemStatePool: shared-state locks for plant systems.
        # Activities requiring the same state on a system can coexist;
        # activities requiring a different state are blocked until all
        # current holders complete.  None when outage_data carries no
        # system state pool — all `if self.system_state_pool:` guards make
        # this safe.
        self.system_state_pool = (
            getattr(outage_data, 'system_state_pool', None)
            if outage_data else None
        )

        # Priority values for activities
        self.priorities = priorities

        # Random seed
        self.seed = seed
        random.seed(self.seed)

        # Initialize activity tracking lists
        self.wait: set = set()              # pending activities (set for O(1) remove/member)
        self.ongoing = []
        self.completed = []
        self._completed_set: set = set()   # O(1) membership mirror of self.completed
        self._ready: set = set()            # wait activities whose predecessors are all done
        self._pending_preds: dict = {}      # act → # predecessors not yet in _completed_set
        # Universal no-alternative skill minimum: skills needed by ALL activities
        # with no alternatives, and the minimum crew count across those activities.
        # Computed once per scheduling run in _reset_scheduling_state / _partial_reset
        # and used for early-exit in _schedule_generation_scheme.
        self._univ_skill_min: dict = {}     # {skill: min_crew} or {} if no universal skill

        # Fix III: heap-based candidate selection (O(K log n) per step vs O(n)).
        # _priority_cache: precomputed static priority per activity for TF_based /
        # external priority modes.  Populated at start of each scheduling run.
        # _ready_heap: max-heap (stored as min-heap with negated values) of
        # (-priority, name_str, act) for activities in self._ready.  Maintained
        # incrementally alongside self._ready.  Empty list = heap not active.
        self._priority_cache: dict = {}     # {Activity: float}
        self._ready_heap: list = []         # [(-priority, name, seq, act), ...]
        self._heap_seq: dict = {}           # {Activity: int} push-count; used to invalidate stale entries

        # Activities
        self.startActivity = None
        self.endActivity = None

        # If outage_data provided, build graph from tasks
        if outage_data and not graph:
            self._build_graph_from_outage_data()

        # Initialize graph structure
        if self.forwardDict:
            self.resetInitialGraph()
            self.nxgraph = nx.DiGraph(self.forwardDict)
            self.generateInfo()
            self._update_activity_successors()


        self._availability_events: frozenset = frozenset()
        if self.crew_pool or self.equipment_pool or self.location_pool:
            self._precompute_availability_events()

        self.schedule_log = []
        self._last_schedule_result: dict = {}
        self._window_violations: list = []
        self._window_violations_baseline: int = 0  # index into _window_violations at start of current run

    @classmethod
    def from_json_file(cls, filepath: str, schema_path: str, priorities: Dict = None, seed: int = 2506178):
        """
        Create Pert object from JSON file.
        Args:
            filepath (str): Path to outage input JSON
            schema_path (str): Path to external JSON schema (required)
            priorities (dict, optional): Activity priorities
            seed (int): RNG seed
        """
        # 1) Load raw JSON
        import json
        with open(filepath, "r", encoding="utf-8") as f:
            raw = json.load(f)

        # 2) Validate against external schema
        validator = OutageDataValidator(schema_path=schema_path)  # schema_path is mandatory in your design
        is_valid, errors, warnings = validator.validate(raw)

        if not is_valid:
            msg = "Outage data failed schema/semantic validation:\n" + "\n".join(f"- {e}" for e in errors[:20])
            # raise a hard error; caller can catch
            raise ValueError(msg)

        # (Optional) log warnings but proceed
        if warnings:
            for w in warnings[:20]:
                logger.debug(f"[validation warning] {w}")

        # 3) Construct OutageData only after validation passes
        outage_data = load_outage_data(filepath)  # existing factory
        return cls(outage_data=outage_data, priorities=priorities, seed=seed)



    def _build_graph_from_outage_data(self):
        """
        Build the graph structure from OutageData tasks.

        Creates Activity objects from task dictionaries and establishes
        predecessor/successor relationships.
        """
        # Create Activity objects from tasks
        for task_dict in self.outage_data.tasks:
            activity = Activity.from_json(task_dict)
            self.task_to_activity[task_dict['task_id']] = activity

        # Build forward dictionary (activity -> successors) and populate lag_dict.
        # Each successor entry is normalised by Activity.from_json() to a plain
        # task-ID string; lag information is stored in activity.successor_lags.
        for task_dict in self.outage_data.tasks:
            activity = self.task_to_activity[task_dict['task_id']]
            successors = []
            for succ_id in activity.childs:   # already normalised by from_json
                if succ_id in self.task_to_activity:
                    succ_act = self.task_to_activity[succ_id]
                    successors.append(succ_act)
                    lag_h = activity.successor_lags.get(succ_id, 0.0)
                    if lag_h:
                        self.lag_dict[(activity, succ_act)] = lag_h
            self.forwardDict[activity] = successors

        # Handle hold points - add implicit dependencies (guard cycles)
        for task_dict in self.outage_data.tasks:
            if task_dict.get('is_hold_point', False):
                hold_activity = self.task_to_activity[task_dict['task_id']]

                # Ensure hold_activity has a list in forwardDict
                if hold_activity not in self.forwardDict:
                    self.forwardDict[hold_activity] = []

                for blocked_id in task_dict.get('blocks_tasks', []):
                    if blocked_id in self.task_to_activity:
                        blocked_activity = self.task_to_activity[blocked_id]

                        # Skip duplicates
                        already_present = blocked_activity in self.forwardDict[hold_activity]

                        # Cycle guard: do NOT add hold_activity -> blocked_activity
                        # if blocked_activity already reaches hold_activity through existing edges.
                        creates_cycle = self._is_reachable(blocked_activity, hold_activity)

                        if not already_present and not creates_cycle:
                            self.forwardDict[hold_activity].append(blocked_activity)
                        elif creates_cycle:
                            logger.warning(
                                "Skipping hold edge %s -> %s to avoid cycle.",
                                hold_activity.name, blocked_activity.name
                            )

    def _is_reachable(self, src, dst) -> bool:
        """
        Return True if `dst` is reachable from `src` via forwardDict.

        This guard is used to prevent introducing cycles when adding new edges.
        """
        stack = [src]
        seen = set()
        while stack:
            cur = stack.pop()
            if cur == dst:
                return True
            for nxt in self.forwardDict.get(cur, []):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    def _update_activity_successors(self):
        """Update each activity's internal successor list."""
        for activity in self.forwardDict.keys():
            activity.updateChilds(self.forwardDict[activity])

    def __str__(self):
        """Return basic information about the schedule graph."""
        iterator = iter(self)
        graphStr = 'Activities:\n'
        for activity in iterator:
            graphStr += str(activity) + '\n'

        duration = self.infoDict[self.endActivity]['ef'] if self.endActivity else 0

        return (graphStr + 'Connections:\n'
                + str(self.returnGraphSymbolic())
                + '\nProject Duration:\n'
                + str(duration))

    def __iter__(self):
        """Iterator for the Pert class."""
        return iter(self.forwardDict)

    def reseed(self, seedValue: int):
        """
        Reseed the random number generator.

        Args:
            seedValue (int): New seed value
        """
        self.seed = seedValue
        random.seed(self.seed)


    def _get_sources(self) -> List['Activity']:
        """
        Return activities with no predecessors (in-degree zero).

        When a START node is present it will be the sole result.
        When no START node exists, all activities that have no predecessors
        in the graph are returned, allowing the scheduler and CPM to work
        without a mandatory dummy start node.
        """
        return [a for a in self.forwardDict
                if not self.backwardDict.get(a)]

    def _get_sinks(self) -> List['Activity']:
        """
        Return activities with no successors (out-degree zero).

        When an END node is present it will be the sole result.
        When no END node exists, all activities that have no successors
        in the graph are returned, enabling multi-sink CPM and duration
        calculation.
        """
        return [a for a in self.forwardDict
                if not self.forwardDict.get(a)]


    def resetInitialGraph(self):
            """
            Reset the schedule graph structure.

            Sets startActivity and endActivity if nodes named START/END are found;
            otherwise leaves them as None and the scheduler/CPM will use
            _get_sources() / _get_sinks() instead.
            """
            for activity in self.forwardDict:
                self.backwardDict[activity] = []

            for activity in self.forwardDict:
                if activity.name.upper() == "START":
                    self.startActivity = activity
                if activity.name.upper() == "END":
                    self.endActivity = activity
                for node in self.forwardDict[activity]:
                    self.backwardDict[node].append(activity)

            self.resetInfo()

    def resetInfo(self):
        """
        Reset the numeric values of the schedule graph.

        Initializes:
        - duration: the duration of the activity
        - es: early start
        - ef: early finish
        - ls: late start
        - lf: late finish
        - slack: lf - ef or ls - es
        """
        for activity in self.forwardDict:
            self.infoDict[activity] = {
                "duration": activity.duration,
                "es": 0,
                "ef": 0,
                "ls": 0,
                "lf": math.inf,
                "slack": 0,
                "wbs_slack": 0,
                "mts":0,
                "mtp":0,
                "grpw":0,
                "grd":0,
                "rr":0,
                "avgrr":0,
                "maxrr":0,
                "minrr":0,
                "window_infeasible": False,
            }

    def returnGraph(self):
        """
        Return the graph info contained in forwardDict.

        Returns:
            dict: Graph structure (edges, nodes, and time values)
        """
        return self.forwardDict

    def returnGraphSymbolic(self):
        """
        Return the graph in symbolic form (using activity names).

        Returns:
            dict: Graph structure with activity names as keys and values
        """
        symbolicGraph = {}
        for key in self.forwardDict.keys():
            symbolicGraph[key.returnName()] = []
            for elem in self.forwardDict[key]:
                symbolicGraph[key.returnName()].append(elem.returnName())
        return symbolicGraph
    """
    def generateInfo_OLD(self):
        Calculate es, ef, ls, lf, and slack for all activities.

        Process:
        1. Forward pass: calculate early start (es) and early finish (ef)
        2. Backward pass: calculate late start (ls) and late finish (lf)
        3. Calculate slack for all activities
        4. Handle isolated activities

        if self.forwardDict == {}:
            return

        if not self.startActivity or not self.endActivity:
            raise ValueError("Start and End activities must be defined")

        # Forward pass
        self.infoDict[self.startActivity]["ef"] = self.infoDict[self.startActivity]["duration"]
        self.startToEndScan(self.startActivity)

        # Backward pass
        self.infoDict[self.endActivity]["lf"] = self.infoDict[self.endActivity]["ef"]
        self.infoDict[self.endActivity]["ls"] = (
            self.infoDict[self.endActivity]["lf"] -
            self.infoDict[self.endActivity]["duration"]
        )
        self.endToStartScan(self.endActivity)

        # Calculate slack
        self.calculateSlack()

        # Handle isolated activities
        self.generateInfoForIsolated()
    """

    def set_priorities(self, new_priorities: Dict[str, float], mode: str = "replace"):
        """
        Update the external priority map used by _select_candidate_activities('external').
        mode: 'replace' replaces the map; 'merge' updates keys and keeps existing ones.

        Example priority map: task_id -> priority score
        new_priorities = {
            "T01": 0.9,
            "T02": 0.8,
            "T03": 0.3
        }
        pert.set_priorities(new_priorities, mode="replace")

        """
        # Basic validation
        if not isinstance(new_priorities, dict):
            raise ValueError("Priorities must be a dict of {task_id: float}.")
        for k, v in new_priorities.items():
            if not isinstance(k, str) or not isinstance(v, (int, float)):
                raise ValueError(f"Invalid priority entry: {k} -> {v}")

        if mode == "replace":
            self.priorities = dict(new_priorities)
        elif mode == "merge":
            self.priorities = (self.priorities or {}).copy()
            self.priorities.update(new_priorities)
        else:
            raise ValueError("mode must be 'replace' or 'merge'")

    def _sync_infodict_durations(self):
        """
        Synchronise the 'duration' field in infoDict with the current value stored
        on each Activity object.

        Why this is needed:
            infoDict entries are populated by resetInfo(), which reads activity.duration
            at the time of the call. If activity.duration is subsequently changed by
            set_durations(), the infoDict 'duration' field becomes stale. generateInfo()
            uses infoDict['duration'] (not activity.duration) during the forward/backward
            pass, so all ES/EF/LS/LF values would be computed from the old durations
            unless this sync is performed first.
        """
        for act in self.forwardDict.keys():
            if act in self.infoDict:
                self.infoDict[act]['duration'] = act.duration
            else:
                # Defensive: activity somehow missing from infoDict (shouldn't happen)
                self.infoDict[act] = {
                    'duration': act.duration,
                    'es': 0.0, 'ef': 0.0,
                    'ls': 0.0, 'lf': 0.0,
                    'slack': 0.0
                }


    def set_durations(self, new_durations: Dict[str, float]):
        """
        Update activity durations and recompute all CPM values (ES, EF, LS, LF, slack).

        This must be called before calculateScheduleWithResources() whenever durations
        have changed (e.g. each RAVEN Monte-Carlo iteration), otherwise the scheduler
        operates on a stale CPM solution:
            - The safety time limit (max_time = cpm_duration * 2) is wrong
            - Candidate selection uses stale ES values to gate activity eligibility
            - getProjectDuration() returns the wrong reference duration

        Args:
            new_durations (dict): Mapping of {task_id: duration_in_hours}

        Raises:
            ValueError: If new_durations is not a dict or contains invalid entries
            KeyError: If a task_id is not found in task_to_activity
        """
        if not isinstance(new_durations, dict):
            raise ValueError("new_durations must be a dict of {task_id: float}.")
        for k, v in new_durations.items():
            if not isinstance(k, str) or not isinstance(v, (int, float)):
                raise ValueError(f"Invalid duration entry: {k!r} -> {v!r}")
            if v < 0:
                raise ValueError(f"Duration must be non-negative, got {k!r}: {v}")

        # Step 1: update duration on each Activity object.
        # Pre-sync task_to_activity for graph-built Pert objects that don't
        # populate it during construction (same pattern as set_modes).
        for existing in self.forwardDict:
            self.task_to_activity.setdefault(existing.name, existing)

        for task_id, duration in new_durations.items():
            if task_id not in self.task_to_activity:
                raise KeyError(f"set_durations: task_id '{task_id}' not found in schedule")
            self.task_to_activity[task_id].updateDuration(duration)

        # Step 2: push the new durations into infoDict so generateInfo() sees them
        self._sync_infodict_durations()

        # Step 3: recompute ES, EF, LS, LF, slack for the whole network
        self.generateInfo()

        logger.debug(
            "set_durations: updated %d activities and recomputed CPM. "
            "New project duration = %.1f h",
            len(new_durations),
            self.getProjectDuration()
        )


    def set_modes(self, mode_assignments: Dict[str, str]):
        """
        Apply execution modes to activities and recompute all CPM values.

        Each entry in *mode_assignments* maps a ``task_id`` to the ``mode_id``
        to activate for that activity.  Calling this is equivalent to calling
        ``activity.set_mode(mode_id)`` for each entry and then running
        ``_sync_infodict_durations()`` + ``generateInfo()`` — which is exactly
        what :meth:`set_durations` does after applying durations.

        This is the GP entry point for Multi-Mode RCPSP: the GP evolves a mode
        assignment vector, passes it here, then calls
        ``calculateScheduleWithResources()`` and ``compute_fitness()`` to
        evaluate the schedule.

        Args:
            mode_assignments (dict): ``{task_id: mode_id}`` pairs.

        Raises:
            ValueError: If *mode_assignments* is not a dict, or if
                ``activity.set_mode()`` raises (activity has no modes or
                mode_id is not found).
            KeyError: If a task_id is not found.
        """
        if not isinstance(mode_assignments, dict):
            raise ValueError("mode_assignments must be a dict of {task_id: mode_id}.")

        # Graph-built Pert objects don't populate task_to_activity; sync it first.
        for existing in self.forwardDict:
            self.task_to_activity.setdefault(existing.name, existing)

        for task_id, mode_id in mode_assignments.items():
            if task_id not in self.task_to_activity:
                raise KeyError(f"set_modes: task_id '{task_id}' not found in schedule")
            self.task_to_activity[task_id].set_mode(mode_id)

        # Push updated durations into infoDict and recompute CPM
        self._sync_infodict_durations()
        self.generateInfo()

        logger.debug(
            "set_modes: applied %d mode assignments and recomputed CPM. "
            "New project duration = %.1f h",
            len(mode_assignments),
            self.getProjectDuration()
        )


    def generateInfo(self):
        """
        Calculate es, ef, ls, lf, and slack for all activities using
        topological order.

        Works with or without explicit START/END nodes:
        - Sources (in-degree 0) are seeded with es=0 in the forward pass.
        - Project duration is the maximum ef across all sink nodes.
        - The backward pass propagates lf=project_duration from every sink.
        """
        if not self.forwardDict:
            return

        sources = self._get_sources() if not self.startActivity else [self.startActivity]
        sinks   = self._get_sinks()   if not self.endActivity   else [self.endActivity]

        # Guard: malformed graph
        if not sources:
            raise ValueError("generateInfo: no source activities found (cycle or empty graph)")
        if not sinks:
            raise ValueError("generateInfo: no sink activities found (cycle or empty graph)")

        # 1) Topological order (Kahn) — deque for O(1) popleft
        from collections import deque as _deque
        indeg = {a: 0 for a in self.forwardDict}
        for u, succs in self.forwardDict.items():
            for v in succs:
                indeg[v] = indeg.get(v, 0) + 1

        queue = _deque(a for a in sources if a in indeg)
        topo  = []
        while queue:
            u = queue.popleft()
            topo.append(u)
            for v in self.forwardDict.get(u, []):
                indeg[v] -= 1
                if indeg[v] == 0:
                    queue.append(v)

        # 2) Forward pass: ES / EF
        # All activities start at 0; sources keep es=0 and ef=duration.
        for a in self.forwardDict:
            self.infoDict[a]["es"] = 0.0
            self.infoDict[a]["ef"] = 0.0

        for src in sources:
            # Source activities may themselves carry a mobilization lead time.
            # They have no predecessors so the lead is counted from t=0.
            src_lead = getattr(src, 'mobilization_lead_hours', 0.0)
            self.infoDict[src]["es"] = src_lead
            self.infoDict[src]["ef"] = src_lead + self.infoDict[src]["duration"]

        for u in topo:
            u_ef = self.infoDict[u]["ef"]
            for v in self.forwardDict.get(u, []):
                # Apply finish-to-start lag (default 0) PLUS any mobilization
                # lead time on the successor.  The lead is the advance-notice
                # period required before v can start: once u finishes, v cannot
                # begin until lag + mobilization_lead_hours have elapsed.
                lag  = self.lag_dict.get((u, v), 0.0)
                lead = getattr(v, 'mobilization_lead_hours', 0.0)
                earliest = u_ef + lag + lead
                # Use >= so that when a zero-duration source (e.g. START) has
                # EF=0 and a successor initialised to ES=0, we still propagate
                # and set EF = ES + duration correctly.  A strict > would
                # silently skip this update, leaving EF=0 for those activities.
                if earliest >= self.infoDict[v]["es"]:
                    self.infoDict[v]["es"] = earliest
                    self.infoDict[v]["ef"] = self.infoDict[v]["es"] + self.infoDict[v]["duration"]

        # 3) Project duration = maximum EF across all sinks
        project_duration = max(self.infoDict[s]["ef"] for s in sinks)

        # 4) Backward pass: LS / LF
        # Initialise every activity with lf = project_duration.
        for a in self.forwardDict:
            self.infoDict[a]["lf"] = project_duration
            self.infoDict[a]["ls"] = self.infoDict[a]["lf"] - self.infoDict[a]["duration"]

        for u in reversed(topo):
            for v in self.forwardDict.get(u, []):
                # LF(u) must be no later than LS(v) − lag − mobilization_lead.
                # The mobilization lead shifts v's effective start to the right,
                # so u's latest finish must also shift left by the same amount.
                lag  = self.lag_dict.get((u, v), 0.0)
                lead = getattr(v, 'mobilization_lead_hours', 0.0)
                constrained_lf = self.infoDict[v]["ls"] - lag - lead
                # Strict > is correct here: lf is already initialised to
                # project_duration for every activity before this loop runs,
                # so we only need to tighten it when a successor imposes a
                # stricter (smaller) deadline.
                if self.infoDict[u]["lf"] > constrained_lf:
                    self.infoDict[u]["lf"] = constrained_lf
                    self.infoDict[u]["ls"] = self.infoDict[u]["lf"] - self.infoDict[u]["duration"]

        # 5) Slack
        self.calculateSlack()

        # 6) Isolated activities
        self.generateInfoForIsolated()

        # 5b) Time-window post-processing.
        # Applied after the standard CPM pass so that window constraints further
        # restrict the ES/LF derived from network topology.  Infeasible windows
        # (window width < duration) are logged as warnings; the infoDict value
        # 'window_infeasible' is set True so callers can inspect them.
        # topo is passed so _apply_time_windows can propagate tightened LF
        # values back to all predecessors in a single backward sweep.
        self._apply_time_windows(topo=topo)

        # 7-9) Priority metrics
        self.calculate_total_successors(topo=topo)
        self.calculate_total_predecessors(topo=topo)
        self.calculate_greatest_rank_position_weight(topo=topo)
        self.calculate_greatest_resource_demand()
        self.calculate_resource_requirement()
        self.calculate_gp_rules()

        # 10) WBS aggregate float roll-up
        self._compute_wbs_slack()
# ========================================
    def calculate_total_successors(self, topo: list | None = None):
        """Compute MTS (number of reachable successors) for every activity.

        When *topo* is supplied (the topological order already computed by
        ``generateInfo``), a single O(V+E) backward-DP pass is used instead
        of calling ``nx.descendants()`` once per activity (O(n²) total).
        Without *topo* the old O(n²) path is kept for backward compatibility
        with direct callers.

        Note: the DP counts successor paths rather than unique reachable nodes,
        so it over-counts when successors share common descendants.  This is
        acceptable for a scheduling priority heuristic.
        """
        if topo is not None:
            # Backward pass: mts[u] = Σ (1 + mts[v]) for each direct successor v
            mts: dict = {a: 0 for a in self.forwardDict}
            for u in reversed(topo):
                for v in self.forwardDict.get(u, []):
                    mts[u] += 1 + mts.get(v, 0)
            for a in self.forwardDict:
                self.infoDict[a]['mts'] = mts[a]
        else:
            for a in self.forwardDict.keys():
                self.infoDict[a]['mts'] = len(nx.descendants(self.nxgraph, a))

    def calculate_total_predecessors(self, topo: list | None = None):
        """Compute MTP (number of reachable predecessors) for every activity.

        When *topo* is supplied, a single O(V+E) forward-DP pass is used.
        Without *topo* the old O(n²) path is kept for backward compatibility.

        Note: same path-count approximation as ``calculate_total_successors``.
        """
        if topo is not None:
            # Forward pass: mtp[v] = Σ (1 + mtp[u]) for each direct predecessor u
            mtp: dict = {a: 0 for a in self.forwardDict}
            for u in topo:
                for v in self.forwardDict.get(u, []):
                    mtp[v] = mtp.get(v, 0) + 1 + mtp[u]
            for a in self.forwardDict:
                self.infoDict[a]['mtp'] = mtp[a]
        else:
            for a in self.backwardDict.keys():
                self.infoDict[a]['mtp'] = len(nx.ancestors(self.nxgraph, a))

    def calculate_greatest_rank_position_weight(self, topo: list | None = None):
        """Compute GRPW = duration(a) + Σ duration(all ancestors) for each activity.

        When *topo* is supplied, a single O(V+E) forward-DP pass accumulates
        the predecessor duration sum without calling ``nx.ancestors()`` per
        activity.  Without *topo* the old O(n²) path is kept for backward
        compatibility.
        """
        if topo is not None:
            # grpw_anc[v] = sum of durations of all predecessors of v (path-weight
            # approximation; over-counts shared ancestors in DAGs with merges).
            grpw_anc: dict = {a: 0.0 for a in self.forwardDict}
            for u in topo:
                u_dur = self.infoDict[u]['duration']
                for v in self.forwardDict.get(u, []):
                    # v inherits u's accumulated predecessor weight plus u's own duration
                    grpw_anc[v] = grpw_anc.get(v, 0.0) + u_dur + grpw_anc[u]
            for a in self.forwardDict:
                self.infoDict[a]['grpw'] = self.infoDict[a]['duration'] + grpw_anc[a]
        else:
            for a in self.forwardDict.keys():
                pred = nx.ancestors(self.nxgraph, a)
                self.infoDict[a]['grpw'] = self.infoDict[a]['duration'] + sum(self.infoDict[b]['duration'] for b in pred)

    def calculate_greatest_resource_demand(self):
        for a in self.forwardDict.keys():
            res = a.getRequiredResources() # list of dict
            equip = a.getRequiredEquipment() # list of dict
            zone_ids = a.getZoneIds()
            dur = self.infoDict[a]['duration']
            grd = 0
            # grd = dur
            if res:
                grd += sum(r['crew_count'] for r in res) * dur
            if equip:
                grd += sum(e['quantity_needed'] for e in equip) * dur
            if zone_ids:
                grd += len(zone_ids) * dur
            self.infoDict[a]['grd'] = grd

    def calculate_resource_requirement(self):
        for a in self.forwardDict.keys():
            res = a.getRequiredResources()
            eq  = a.getRequiredEquipment()
            zone_ids = a.getZoneIds()

            skills = self.crew_pool.get_all_skills() if self.crew_pool else []
            equips = self.equipment_pool.get_all_equipment_ids() if self.equipment_pool else []
            locs   = self.location_pool.get_all_location_ids() if self.location_pool else []
            rr = dict.fromkeys(skills + equips + locs, 0.0)  # default 0 not None

            if self.crew_pool:
                for r in res:
                    skill_type, crew_count = r['skill_type'], r['crew_count']
                    if skill_type in self.crew_pool.resources:
                        max_avail = self.crew_pool.resources[skill_type].get_max_availability()
                        rr[skill_type] = crew_count / max_avail if max_avail != 0 else 0.0

            if self.equipment_pool:
                for e in eq:
                    e_id, quant = e['equipment_id'], e['quantity_needed']
                    if e_id in self.equipment_pool.equipment:
                        max_avail = self.equipment_pool.equipment[e_id].get_max_availability()
                        rr[e_id] = quant / max_avail if max_avail != 0 else 0.0

            for zone_id in zone_ids:
                if zone_id in rr:
                    rr[zone_id] = 1.0

            rr_val      = np.asarray(list(rr.values()), dtype=float)
            num_res     = len(rr_val)
            num_req_res = int(np.count_nonzero(rr_val))

            self.infoDict[a]['rr']    = num_req_res / num_res if num_res != 0 else 0.0
            self.infoDict[a]['avgrr'] = float(np.sum(rr_val)) / num_res if num_res != 0 else 0.0
            self.infoDict[a]['maxrr'] = float(np.max(rr_val)) if num_res != 0 else 0.0
            self.infoDict[a]['minrr'] = float(np.min(rr_val)) if num_res != 0 else 0.0

# (ES, EF, LS, LF, TPC, TSC, RR, AvgRReq, MaxRReq, MinRReq)
    def calculate_gp_rules(self):
        max_es  = max([self.infoDict[a]['es']  for a in self.forwardDict.keys()])
        max_ef  = max([self.infoDict[a]['ef']  for a in self.forwardDict.keys()])
        max_ls  = max([self.infoDict[a]['ls']  for a in self.forwardDict.keys()])
        max_lf  = max([self.infoDict[a]['lf']  for a in self.forwardDict.keys()
                        if math.isfinite(self.infoDict[a]['lf'])], default=0.0)
        max_mtp = max([self.infoDict[a]['mtp'] for a in self.forwardDict.keys()])
        max_mts = max([self.infoDict[a]['mts'] for a in self.forwardDict.keys()])

        # Guard against zero denominators (trivial or single-activity networks)
        safe_max_es  = max_es  if max_es  != 0.0 else 1.0
        safe_max_ef  = max_ef  if max_ef  != 0.0 else 1.0
        safe_max_ls  = max_ls  if max_ls  != 0.0 else 1.0
        safe_max_lf  = max_lf  if max_lf  != 0.0 else 1.0
        safe_max_mtp = max_mtp if max_mtp != 0   else 1
        safe_max_mts = max_mts if max_mts != 0   else 1

        for a in self.forwardDict.keys():
            lf_val = self.infoDict[a]['lf']
            lf_norm = (lf_val / safe_max_lf) if math.isfinite(lf_val) else 1.0
            for key, func in CUSTOM_PRIORITY_FUNCS.items():
                self.infoDict[a][key] = func(
                    self.infoDict[a]['es']  / safe_max_es,
                    self.infoDict[a]['ef']  / safe_max_ef,
                    self.infoDict[a]['ls']  / safe_max_ls,
                    lf_norm,
                    self.infoDict[a]['mtp'] / safe_max_mtp,
                    self.infoDict[a]['mts'] / safe_max_mts,
                    self.infoDict[a]['rr'],
                    self.infoDict[a]['avgrr'],
                    self.infoDict[a]['maxrr'],
                    self.infoDict[a]['minrr'],
                )

#=========================================

    def startToEndScan(self, activity, visited=None):
        """
        Calculate early start (es) and early finish (ef) recursively.

        Args:
            activity (Activity): Current activity in forward scan
        """

        if visited is None:
            visited = set()
        if activity in visited:
            return
        visited.add(activity)

        for node in self.forwardDict.get(activity, []):
            # ES for node is max of EF of predecessors; here we update if current EF pushes ES forward
            if self.infoDict[activity]["ef"] > self.infoDict[node]["es"]:
                self.infoDict[node]["es"] = self.infoDict[activity]["ef"]
                self.infoDict[node]["ef"] = self.infoDict[node]["es"] + self.infoDict[node]["duration"]
            # Recurse with the same visited set
            self.startToEndScan(node, visited)

    def endToStartScan(self, activity, visited=None):
        """
        Calculate late start (ls) and late finish (lf) recursively.

        Args:
            activity (Activity): Current activity in backward scan
        """

        if visited is None:
            visited = set()
        if activity in visited:
            return
        visited.add(activity)

        for node in self.backwardDict.get(activity, []):
            # LF for predecessor is min of LS of its successors; here we update if current LS pulls LF earlier
            if self.infoDict[node]["lf"] > self.infoDict[activity]["ls"]:
                self.infoDict[node]["lf"] = self.infoDict[activity]["ls"]
                self.infoDict[node]["ls"] = self.infoDict[node]["lf"] - self.infoDict[node]["duration"]
            # Recurse with the same visited set
            self.endToStartScan(node, visited)

    def calculateSlack(self):
        """Calculate slack for all activities (slack = lf - ef)."""
        for activity in self.forwardDict:
            self.infoDict[activity]["slack"] = (
                self.infoDict[activity]["lf"] -
                self.infoDict[activity]["ef"]
            )


    def generateInfoForIsolated(self):
        """
        Calculate timing for isolated activities (no predecessors AND no successors).

        Uses the maximum LF across all sinks so the method works whether or not
        an explicit END node is present.
        """
        isolated = self.findIsolated()
        if not isolated:
            return

        sinks = [self.endActivity] if self.endActivity else self._get_sinks()
        project_lf = max(self.infoDict[s]["lf"] for s in sinks) if sinks else 0.0

        for activity in isolated:
            self.infoDict[activity]["ef"] = (
                self.infoDict[activity]["es"] + self.infoDict[activity]["duration"]
            )
            self.infoDict[activity]["lf"]    = project_lf
            self.infoDict[activity]["ls"]    = project_lf - self.infoDict[activity]["duration"]
            self.infoDict[activity]["slack"] = project_lf - self.infoDict[activity]["ef"]

    def findIsolated(self):
        """
        Find isolated activities (no predecessors or successors).

        Returns:
            list: List of isolated Activity objects
        """
        isolated = list(self.infoDict)
        for activity in self.forwardDict:
            if self.forwardDict[activity] != [] and activity in isolated:
                isolated.remove(activity)
        for activity in self.backwardDict:
            if self.backwardDict[activity] != [] and activity in isolated:
                isolated.remove(activity)
        return isolated

    def _resolve_windows(self, act) -> list:
        """Return a normalised list of (earliest_h, latest_h) float tuples.

        Checks ``act.time_windows`` (multi-window list) first; falls back to
        the legacy single-window fields
        (``window_earliest_start_hours`` / ``window_latest_finish_hours``).

        Returns an empty list when the activity has no window constraints.
        The returned tuples use ``float('inf')`` for an absent latest bound
        and ``0.0`` for an absent earliest bound.
        """
        tw = getattr(act, 'time_windows', [])
        if tw:
            return [(float(w['earliest']), float(w['latest'])) for w in tw]
        west = getattr(act, 'window_earliest_start_hours', None)
        wlf  = getattr(act, 'window_latest_finish_hours',  None)
        if west is not None or wlf is not None:
            return [(
                float(west) if west is not None else 0.0,
                float(wlf)  if wlf  is not None else float('inf'),
            )]
        return []

    def _apply_time_windows(self, topo: list | None = None) -> None:
        """
        Post-process CPM ES/EF/LS/LF/slack to incorporate regulatory
        time-window constraints stored on each Activity.

        Time windows are expressed as hours from outage start:
            window_earliest_start_hours : activity cannot start before this offset
            window_latest_finish_hours  : activity must complete by this offset

        Effect on infoDict
        ------------------
        * ES is tightened:  ES = max(CPM_ES, window_earliest_start_hours)
          EF is updated:    EF = ES + duration
        * LF is tightened:  LF = min(CPM_LF, window_latest_finish_hours)
          LS is updated:    LS = LF - duration
        * Slack is recomputed: slack = LS - ES
        * 'window_infeasible' key is set True when slack < 0 (window narrower
          than activity duration) and a WARNING is logged.

        Window-propagating backward sweep
        ----------------------------------
        When ``topo`` (topological order) is supplied and at least one window
        tightened an LF value, a second backward sweep is performed in reverse
        topological order.  This propagates the tightened LF upstream so that
        every predecessor's LF — and therefore slack — reflects the window
        constraint, not just the unconstrained CPM value.

        Formula (identical to the standard backward pass):
            LF(u) = min(LF(u), LS(v) − lag(u,v) − v.mobilization_lead_hours)
                  for each successor v

        Cost: O(V + E) — one additional linear pass.

        Activities without window fields (both None) are untouched.
        """
        any_lf_tightened = False

        for act in self.forwardDict:
            windows = self._resolve_windows(act)

            if not windows:
                self.infoDict[act]['window_infeasible'] = False
                continue

            info = self.infoDict[act]
            dur  = info['duration']

            # For CPM: use the broadest envelope across all windows.
            # ES is bounded by the earliest window's open time.
            # LF is bounded by the latest window's hard deadline.
            eff_west = min(w[0] for w in windows)
            finite_latests = [w[1] for w in windows if w[1] != float('inf')]
            eff_wlf = max(finite_latests) if finite_latests else None

            if eff_west > 0.0:
                new_es = max(info['es'], eff_west)
                info['es'] = new_es
                info['ef'] = new_es + dur

            if eff_wlf is not None:
                new_lf = min(info['lf'], eff_wlf)
                if new_lf < info['lf']:
                    any_lf_tightened = True
                info['lf'] = new_lf
                info['ls'] = new_lf - dur

            info['slack'] = info['ls'] - info['es']

            infeasible = info['slack'] < 0.0
            info['window_infeasible'] = infeasible
            if infeasible:
                win_str = ', '.join(
                    f'[{w[0]:.1f}, {"∞" if w[1]==float("inf") else f"{w[1]:.1f}"}]'
                    for w in windows
                )
                logger.warning(
                    "Time-window infeasibility on '%s': window(s) %s "
                    "cannot accommodate duration %.1f h (slack=%.2f h).",
                    act.name, win_str, dur, info['slack'],
                )

        # Backward sweep: propagate tightened LF values to all predecessors.
        # Only runs when a window actually reduced an LF and topo is available.
        # Uses the same formula as the standard CPM backward pass.
        if topo is not None and any_lf_tightened:
            for u in reversed(topo):
                info_u = self.infoDict[u]
                for v in self.forwardDict.get(u, []):
                    lag  = self.lag_dict.get((u, v), 0.0)
                    lead = getattr(v, 'mobilization_lead_hours', 0.0)
                    constrained_lf = self.infoDict[v]['ls'] - lag - lead
                    if info_u['lf'] > constrained_lf:
                        info_u['lf'] = constrained_lf
                        info_u['ls'] = constrained_lf - info_u['duration']
                        info_u['slack'] = info_u['ls'] - info_u['es']

    def _compute_wbs_slack(self) -> None:
        """Compute WBS-level aggregate float and write it into ``infoDict``.

        For each ``wbs_group``, the *group minimum slack* is the minimum
        ``slack`` value across all activities that share the same group label.
        Every member's ``infoDict['wbs_slack']`` is set to that minimum.

        Activities with no ``wbs_group`` (or ``wbs_group = None``) receive
        ``wbs_slack = slack`` (their own individual float), so the rest of the
        scheduling logic behaves identically to before for ungrouped tasks.

        **Effect on scheduling priority:**

        When ``_select_candidate_activities`` evaluates a task's weight it uses
        ``min(slack, wbs_slack)`` — whichever is tighter.  If even one task in
        a WBS package has zero float, every member of the package gets an
        effective slack of 0 and is elevated to maximum priority
        simultaneously, preventing the scheduler from idling cross-trained
        workers while a system's critical sub-tasks stall.

        Called at the end of both ``generateInfo()`` and
        ``_generate_info_from()`` so that replanning also respects WBS groups.
        """
        from collections import defaultdict

        # Collect group members
        groups: dict = defaultdict(list)
        for act in self.forwardDict:
            group = getattr(act, 'wbs_group', None)
            if group:
                groups[group].append(act)

        # Min slack per group
        group_min: dict = {
            group: min(self.infoDict[m]['slack'] for m in members)
            for group, members in groups.items()
        }

        # Write wbs_slack for every activity
        for act in self.forwardDict:
            group = getattr(act, 'wbs_group', None)
            if group and group in group_min:
                self.infoDict[act]['wbs_slack'] = group_min[group]
            else:
                self.infoDict[act]['wbs_slack'] = self.infoDict[act]['slack']

    def _is_zero(self, x: float, tol: float = 1e-6) -> bool:
        return abs(x) <= tol

    def _eq_with_tol(self, a: float, b: float, tol: float = 1e-6) -> bool:
        return abs(a - b) <= tol

    def getCriticalPath(self, return_all: bool = False):
        """
        Return the critical path(s) of the schedule.

        A critical path is a maximal path from a source to a sink along which
        every activity has zero slack and consecutive EF/ES values align.

        Works with or without explicit START/END nodes.  When START/END are
        absent the search is performed from every zero-indegree node to every
        zero-outdegree node.

        Args:
            return_all (bool): If False (default) return the single longest
                critical path as List[Activity].  If True return all critical
                paths as List[List[Activity]], sorted longest-first.

        Returns:
            List[Activity]           when return_all=False
            List[List[Activity]]     when return_all=True  (may be empty list
                                     if no strict critical path exists, in which
                                     case the fallback heuristic is used for
                                     the return_all=False case only)
        """
        sources = [self.startActivity] if self.startActivity else self._get_sources()
        sinks   = [self.endActivity]   if self.endActivity   else self._get_sinks()

        if not sources or not sinks:
            return [] if not return_all else [[]]

        # Build zero-slack, EF/ES-aligned subgraph
        crit_successors: Dict = {}
        for u in self.forwardDict:
            crit_successors[u] = []
            u_ef = self.infoDict[u]["ef"]
            for v in self.forwardDict[u]:
                if (self._is_zero(self.infoDict[v]["slack"]) and
                        self._eq_with_tol(u_ef, self.infoDict[v]["es"])):
                    crit_successors[u].append(v)

        # DFS from every source to every sink collecting all zero-slack paths
        all_paths: List[List[Activity]] = []

        def dfs(u: Activity, path: List[Activity], length: float):
            if u in sinks:
                all_paths.append((path[:], length))
                return
            for v in crit_successors.get(u, []):
                path.append(v)
                dfs(v, path, length + self.infoDict[v]["duration"])
                path.pop()

        for src in sources:
            dfs(src, [src], self.infoDict[src]["duration"])

        # Sort longest-first
        all_paths.sort(key=lambda t: t[1], reverse=True)
        paths_only = [p for p, _ in all_paths]

        if return_all:
            return paths_only  # may be empty if no strict critical path found

        if paths_only:
            return paths_only[0]  # single longest path

        # ── Fallback heuristic (return_all=False only) ───────────────────────
        # When the strict subgraph is disconnected, greedily walk from the
        # source with the highest EF, choosing successors by minimum slack and
        # closest EF/ES alignment.
        best_src = max(sources, key=lambda s: self.infoDict[s]["ef"])
        current  = best_src
        path     = [current]
        visited  = {current}

        while current not in sinks:
            succs = self.forwardDict.get(current, [])
            if not succs:
                break
            cur_ef = self.infoDict[current]["ef"]
            ranked = sorted(
                succs,
                key=lambda v: (
                    abs(cur_ef - self.infoDict[v]["es"]),
                    self.infoDict[v]["slack"]
                )
            )
            nxt = ranked[0]
            if nxt in visited:
                break
            path.append(nxt)
            visited.add(nxt)
            current = nxt

        return path


    def getCriticalPathSymbolic(self, return_all: bool = False):
        """
        Return critical path(s) as activity name strings.

        Args:
            return_all (bool): Mirrors getCriticalPath(return_all).

        Returns:
            List[str]            when return_all=False
            List[List[str]]      when return_all=True
        """
        if return_all:
            paths = self.getCriticalPath(return_all=True)
            return [[a.returnName() for a in path] for path in paths]
        path = self.getCriticalPath(return_all=False)
        return [a.returnName() for a in path] if path else []


    def getCriticalPathWithLength(self):
        """
        Get critical path as dictionary with durations.

        Returns:
            dict: {Activity: duration} for activities on critical path
        """
        return {activity: activity.duration for activity in self.getCriticalPath()}


    def getProjectDuration(self) -> float:
        """
        Return the project duration in hours.

        Uses endActivity.ef when an END node is present; otherwise returns
        the maximum ef across all sink nodes.
        """
        if self.endActivity:
            return self.infoDict[self.endActivity]["ef"]
        sinks = self._get_sinks()
        if not sinks:
            return 0.0
        return max(self.infoDict[s]["ef"] for s in sinks)

    def returnScheduleEndTime(self):
        """
        Get absolute end time of the schedule.

        Returns:
            datetime: Absolute end time
        """
        if not self.startTime:
            raise ValueError("Start time not set")

        duration_hours = self.getProjectDuration()
        endTime = self.startTime + timedelta(hours=duration_hours)
        return endTime

    def addActivity(self, activity, inConnections=None, outConnections=None):
        """
        Add a new activity to the existing schedule.

        Args:
            activity (Activity): Activity to be added
            inConnections (list): List of predecessor activities
            outConnections (list): List of successor activities
        """
        if inConnections is None:
            inConnections = []
        if outConnections is None:
            outConnections = []

        if activity in self.forwardDict:
            return

        self.forwardDict[activity] = outConnections
        self.backwardDict[activity] = inConnections

        # Update forward connections
        if inConnections:
            for node in inConnections:
                if self.forwardDict.get(node) is None:
                    self.forwardDict[node] = []
                self.forwardDict[node].append(activity)

        # Update backward connections
        if outConnections:
            for node in outConnections:
                if self.backwardDict.get(node) is None:
                    self.backwardDict[node] = []
                self.backwardDict[node].append(activity)

        # resetInfo() (called immediately below) will fully initialise all
        # infoDict keys for every activity in forwardDict — including the new
        # one just added above.  The explicit assignment here is therefore not
        # strictly necessary, but it is kept as a defensive guard so that any
        # code path that reads infoDict between addActivity() and resetInfo()
        # sees a complete entry rather than a KeyError.  Keys must match those
        # written by resetInfo() exactly.
        self.infoDict[activity] = {
            "duration": activity.duration,
            "es": 0, "ef": 0, "ls": 0, "lf": math.inf, "slack": 0,
            "wbs_slack": 0,
            "mts": 0, "mtp": 0,
            "grpw": 0, "grd": 0,
            "rr": 0, "avgrr": 0, "maxrr": 0, "minrr": 0,
        }

        # Rebuild the NetworkX graph so that calculateInfo()'s calls to
        # nx.descendants() / nx.ancestors() see the updated topology.
        self.nxgraph = nx.DiGraph(self.forwardDict)

        # Recalculate
        self.resetInfo()
        self.generateInfo()

    # -------------------------------------------------------------------------
    # Emergent-activity analysis support
    # The two methods below (insert_task, clone_for_analysis) were added to
    # support DACKAR's unexpected-activity workflow (outage Stage E).  They
    # provide a first-class API for topology mutation and clean what-if copies
    # so that external callers do not need to manipulate Pert internals directly.
    # -------------------------------------------------------------------------

    def insert_task(
        self,
        task_dict: dict,
        after_task_id: Optional[str] = None,
        before_task_id: Optional[str] = None,
    ) -> 'Activity':
        """Insert a new task into the schedule network between two existing tasks.

        Added to support DACKAR emergent-activity analysis (outage Stage E):
        allows an unexpected activity to be spliced into the live schedule
        graph without manually touching forwardDict / backwardDict / nxgraph.

        Compared with addActivity(), this method:
          - Accepts a task_dict (same format as outage JSON tasks) rather than
            an Activity object, so callers do not need to construct Activity.
          - Removes the direct edge after_task → before_task when it exists,
            preserving finish-to-start ordering (A → NEW → B, not A → B too).
          - Updates task_to_activity and nxgraph consistently.
          - Does NOT call resetInfo() / generateInfo() — the caller decides
            when to recompute CPM state (allows batching multiple insertions).

        Args:
            task_dict:      Task definition dict following the outage JSON schema
                            (keys: task_id, duration, successors, …).
            after_task_id:  ID of the predecessor task (new task starts after this).
                            None means the new task has no predecessor (new source).
            before_task_id: ID of the successor task (new task finishes before this).
                            None means the new task has no successor (new sink).

        Returns:
            The newly created Activity object.

        Raises:
            ValueError: If task_dict['task_id'] already exists in the network.
        """
        task_id: str = task_dict['task_id']
        if task_id in self.task_to_activity:
            raise ValueError(
                f"Task '{task_id}' already exists in the schedule network."
            )

        new_activity = Activity.from_json(task_dict)

        # Register so callers can look up the Activity by ID
        self.task_to_activity[task_id] = new_activity

        # Locate predecessor / successor Activity objects
        after_act = self.task_to_activity.get(after_task_id) if after_task_id else None
        before_act = self.task_to_activity.get(before_task_id) if before_task_id else None

        # Initialise new node in graph dicts
        self.forwardDict[new_activity] = [before_act] if before_act else []
        self.backwardDict[new_activity] = [after_act] if after_act else []

        # Wire predecessor: after_act → new_activity
        if after_act is not None:
            self.forwardDict.setdefault(after_act, []).append(new_activity)
            # Remove the now-bypassed direct edge after_act → before_act
            if before_act is not None:
                try:
                    self.forwardDict[after_act].remove(before_act)
                except ValueError:
                    pass  # edge did not exist; nothing to remove

        # Wire successor: new_activity → before_act (backward direction)
        if before_act is not None:
            self.backwardDict.setdefault(before_act, []).append(new_activity)
            # Remove the now-bypassed backward edge before_act ← after_act
            if after_act is not None:
                try:
                    self.backwardDict[before_act].remove(after_act)
                except ValueError:
                    pass

        # Keep NetworkX graph in sync
        if self.nxgraph is not None:
            self.nxgraph.add_node(new_activity)
            if after_act is not None:
                self.nxgraph.add_edge(after_act, new_activity)
            if before_act is not None:
                self.nxgraph.add_edge(new_activity, before_act)
            if after_act is not None and before_act is not None:
                if self.nxgraph.has_edge(after_act, before_act):
                    self.nxgraph.remove_edge(after_act, before_act)

        # Seed infoDict entry with zeros; actual values set by generateInfo()
        self.infoDict[new_activity] = {
            "duration": new_activity.duration,
            "es": 0, "ef": 0, "ls": 0, "lf": math.inf,
            "slack": 0, "mts": 0, "mtp": 0, "grpw": 0,
            "grd": 0, "rr": 0, "avgrr": 0, "maxrr": 0, "minrr": 0,
        }

        return new_activity

    def print_summary(self):
        """Print summary of the schedule."""
        logger.debug("=" * 70)
        logger.debug("PERT SCHEDULE SUMMARY")
        logger.debug("=" * 70)
        logger.debug(f"Total Activities: {len(self.forwardDict)}")
        logger.debug(f"Project Duration: {self.getProjectDuration():.2f} hours")

        if self.startTime:
            end_time = self.returnScheduleEndTime()
            logger.debug(f"Start Time: {self.startTime.strftime('%Y-%m-%d %H:%M')}")
            logger.debug(f"End Time: {end_time.strftime('%Y-%m-%d %H:%M')}")

        logger.debug(f"\nCritical Path ({len(self.getCriticalPath())} activities):")
        cp_symbolic = self.getCriticalPathSymbolic()
        logger.debug(" -> ".join(cp_symbolic))

        logger.debug("\nCritical Path Durations:")
        for activity in self.getCriticalPath():
            if activity.name not in ['START', 'END']:
                logger.debug(f"  {activity.name}: {activity.duration:.1f} hours - {activity.description}")

        logger.debug("=" * 70)

    def __repr__(self):
        """String representation of Pert object."""
        n_activities = len(self.forwardDict)
        duration = self.getProjectDuration()
        return f"Pert({n_activities} activities, duration={duration:.2f} hours)"


    def _compute_univ_skill_min(self) -> None:
        """Compute self._univ_skill_min once per scheduling run.

        Result: skills needed by EVERY activity in the graph that have NO
        declared alternative skill types, mapped to their minimum crew count.
        When any such skill's remaining capacity drops below its minimum, no
        candidate can start on that skill regardless of priority — safe to
        break the candidate loop in _schedule_generation_scheme.

        Empty dict means no universal no-alternative skill exists (mixed-skill
        graph), so the early-exit never fires and correctness is preserved.

        Complexity: O(n × |reqs|) — called once per scheduling run.
        """
        univ: dict = {}
        first = True
        for act in self.forwardDict:
            reqs = act.getRequiredResources()
            act_skills = {
                r['skill_type']: r['crew_count']
                for r in reqs
                if r['crew_count'] > 0 and not r.get('alternative_skill_types')
            }
            if not act_skills:
                # This activity needs no skill (zero-duration gate) or all
                # requirements have alternatives — skip it from the intersection.
                continue
            if first:
                univ = dict(act_skills)
                first = False
            else:
                for s in list(univ):
                    if s not in act_skills:
                        del univ[s]
                    else:
                        univ[s] = min(univ[s], act_skills[s])
            if not univ:
                break   # intersection is empty — no universal skill
        self._univ_skill_min = univ

    def _rebuild_ready_set(self) -> None:
        """Recompute _ready from self.wait and _completed_set.

        An activity is *ready* when every one of its predecessors (direct or
        transitive) has been placed in _completed_set.  This is evaluated
        cheaply using only the direct-predecessor list (backwardDict), since
        transitivity is handled by the fact that predecessors enter _completed_set
        only after their own predecessors completed.

        Call this after any bulk rebuild of self.wait and _completed_set — i.e.
        at the end of _reset_scheduling_state() and _partial_reset().  During
        the scheduling loop, _ready is maintained incrementally by
        _update_ongoing_list().

        Complexity: O(n × k) where k is average predecessor count.
        """
        # Build pending-predecessor counters for every activity in the graph,
        # counting only predecessors not yet in _completed_set.  O(n × k).
        self._pending_preds = {
            act: sum(1 for pred in self.backwardDict.get(act, [])
                     if pred not in self._completed_set)
            for act in self.forwardDict
        }
        # An activity is ready iff all its predecessors are done (counter == 0)
        # and it is still in self.wait.  O(n).
        self._ready = {
            act for act in self.wait
            if self._pending_preds[act] == 0
        }

    def _compute_priority_cache(self, value_mode: str) -> None:
        """Precompute static activity priorities for heap-based candidate selection.

        Populates ``self._priority_cache`` with a float per activity.  Only
        supported for static priority modes (TF_based, external) where the value
        does not change as activities complete during the run.  For dynamic or
        unknown modes, the cache is cleared so the heap falls back to the full
        O(n) candidate scan.

        Must be called *after* ``generateInfo()`` / ``_generate_info_from()``
        so that ``infoDict`` slack values are up to date.
        """
        self._priority_cache = {}
        if value_mode == 'TF_based':
            proj_dur = self.getProjectDuration()
            for act in self.forwardDict:
                info = self.infoDict.get(act, {})
                eff_slack = min(
                    info.get('slack', 0.0),
                    info.get('wbs_slack', info.get('slack', 0.0)),
                )
                self._priority_cache[act] = _weight_function(eff_slack, proj_dur)
        elif value_mode == 'external' and self.priorities:
            for act in self.forwardDict:
                act_name = act.returnName()
                self._priority_cache[act] = self.priorities.get(act_name, 0.5)
        # For priority-rule names (lf, ls, ef, es, …) and other dynamic modes:
        # leave _priority_cache empty → _select_candidate_activities falls back
        # to the O(n) full scan.

    def _build_ready_heap(self) -> None:
        """Build (or rebuild) the max-heap from ``_priority_cache`` and ``_ready``.

        Should be called once per scheduling run, after ``_compute_priority_cache``
        and after the initial ``_ready`` set has been constructed by
        ``_rebuild_ready_set()``.

        Complexity: O(n) via heapq.heapify.
        """
        self._heap_seq = {}
        if not self._priority_cache:
            self._ready_heap = []
            return
        self._ready_heap = []
        for act in self._ready:
            self._heap_push(act)

    def _heap_push(self, act: Activity) -> None:
        """Push *act* onto ``_ready_heap`` with a fresh version tag.

        Incrementing ``_heap_seq[act]`` before pushing ensures that any prior
        heap entry for this activity compares unequal on the seq field and is
        treated as stale during the next ``_collect_candidates_from_heap`` call.
        At most one valid entry per activity exists in the heap at any time,
        eliminating the duplicate accumulation that occurred when all collected
        candidates were re-pushed each scheduling step.
        """
        seq = self._heap_seq.get(act, 0) + 1
        self._heap_seq[act] = seq
        heapq.heappush(
            self._ready_heap,
            (-self._priority_cache.get(act, 0.0), str(act.name), seq, act),
        )

    def _reset_scheduling_state(self):
        """
        Reset all mutable scheduling state on activities and on the Pert instance
        so that calculateScheduleWithResources() produces a clean result each time
        it is called (e.g. across successive RAVEN Monte-Carlo iterations).

        What is reset:
            Activity level:
                - startTime, endTime  (set by setActualStartTime)
                - delay               (computed lazily at start time via _candidate_since)
                - _candidate_since    (lazy delay anchor; reset by Activity.reset())
                - belongsToCP         (flagged by getCriticalPath)

            Pert level:
                - wait / ongoing / completed  (scheduling queues)
                - schedule_log                (step-by-step log)
                - actual_tf                   (post-schedule analytics)
                - actual_zero_tf_set          (post-schedule analytics)
                - constrained_chain_list      (post-schedule analytics)
                - constrained_chain_set       (post-schedule analytics)

        What is NOT reset:
            - activity durations   (already updated by set_durations for this run)
            - infoDict (ES/EF/LS/LF)   (regenerated by generateInfo in Issue-2 fix)
            - graph structure      (forwardDict / backwardDict)
            - priorities           (set by set_priorities for this run)
        """
        # Reset every activity's scheduling state
        for act in self.forwardDict.keys():
            act.reset()  # calls the new Activity.reset() method

        # Reset Pert-level scheduling queues
        self.wait = set(self.forwardDict.keys())
        self.ongoing = []
        self.completed = []
        self._completed_set = set()
        self._rebuild_ready_set()
        self._compute_univ_skill_min()
        # Clear Fix III heap/cache — rebuilt after value_mode is known
        self._priority_cache = {}
        self._ready_heap = []
        self._heap_seq = {}

        # Reset consumable dose budgets so repeated scheduling runs start clean
        for tracker in self.dose_trackers.values():
            tracker.reset()

        # Reset consumable inventory so repeated scheduling runs start clean
        if self.consumable_pool:
            self.consumable_pool.reset()

        # Reset system state locks so repeated scheduling runs start clean
        if self.system_state_pool:
            self.system_state_pool.reset()

        # Reset time-window violation log; baseline resets to 0 for a fresh run
        self._window_violations: list = []
        self._window_violations_baseline: int = 0

        # Reset step-by-step log
        self.schedule_log = []

        # Reset post-schedule analytics (they will be recomputed at end of run)
        self.actual_tf = {}
        self.actual_zero_tf_set = set()
        self.constrained_chain_list = []
        self.constrained_chain_set = set()

    # ------------------------------------------------------------------
    # REAL-TIME REPLANNING (Challenge 4)
    # ------------------------------------------------------------------

    def _partial_reset(self, current_time_hours: float,
                       duration_overrides: dict = None) -> None:
        """Prepare scheduling state for a mid-outage replan.

        Classifies every activity by its actual timing relative to
        ``current_time_hours``:

        * **completed** (endTime ≤ current_abs): frozen — start/end/delay/
          belongsToCP preserved; dose re-consumed so tracker state is correct.
        * **in_progress** (startTime ≤ current_abs < endTime): frozen start;
          remaining duration stored in ``act._remaining_duration`` for the
          partial CPM pass; dose re-consumed.
        * **pending** (not yet started): full ``activity.reset()`` — start/end/
          delay/belongsToCP cleared; added to ``self.wait``.

        Dose trackers are reset at the top and then re-consumed for frozen
        activities, so consumable budgets reflect the pre-replan expenditure.

        Window violations accumulated before the replan are **not** cleared —
        they are historical facts.  New violations during the rescheduled
        portion are appended on top.  ``_window_violations_baseline`` is
        updated to the current list length so that ``compute_fitness()`` and
        the schedule-result snapshot only count violations from this run.

        Args:
            current_time_hours: Hours from outage start at which the replan
                                 is triggered.
        """
        current_abs = self.startTime + timedelta(hours=current_time_hours)

        # Snapshot the violation count so compute_fitness() and the result
        # dict only report violations produced by this replan run, not those
        # accumulated during earlier scheduling passes.
        self._window_violations_baseline = len(self._window_violations)

        # Rebuild Pert-level scheduling queues from scratch
        self.wait = set()
        self.ongoing = []
        self.completed = []
        self.schedule_log = []
        self.actual_tf = {}
        self.actual_zero_tf_set = set()
        self.constrained_chain_list = []
        self.constrained_chain_set = set()

        # Reset consumable dose trackers; replay for frozen activities below
        for tracker in self.dose_trackers.values():
            tracker.reset()

        # Reset consumable inventory; replay for frozen activities below.
        # Apply restocks up to current_time_hours first so deliveries that
        # arrived before the replan point are accounted for.
        if self.consumable_pool:
            self.consumable_pool.reset()
            self.consumable_pool.apply_restocks_up_to(current_time_hours)

        # Reset system-state locks; re-acquire below for in-progress activities.
        # Completed activities have already released their locks normally, so
        # only in-progress activities (still holding state at replan time) need
        # re-acquisition.
        if self.system_state_pool:
            self.system_state_pool.reset()

        for act in self.forwardDict.keys():
            st, et = act.returnAbsTimes()

            if et is not None and et <= current_abs:
                # ── Completed before replan time ─────────────────────────
                act.status = 'completed'
                self.completed.append(act)
                # Re-commit dose so budget reflects pre-replan consumption
                if self.dose_trackers:
                    dose_rate = getattr(act, 'dose_rate_mrem_per_hour', 0.0)
                    if dose_rate > 0.0:
                        eff = max(0.0, act.duration)
                        actual_res = getattr(act, '_actual_resources', None)
                        if actual_res is not None:
                            for skill, workers in actual_res.items():
                                tracker = self.dose_trackers.get(skill)
                                if tracker:
                                    tracker.consume(dose_rate, workers, eff)
                        else:
                            for req in act.getRequiredResources():
                                tracker = self.dose_trackers.get(req['skill_type'])
                                if tracker:
                                    tracker.consume(dose_rate, req['crew_count'], eff)
                # Re-consume completed activity's consumables
                if self.consumable_pool:
                    for req in act.getRequiredConsumables():
                        self.consumable_pool.consume(req['item_id'],
                                                     float(req['quantity_needed']))

            elif st is not None and st <= current_abs:
                # ── In progress at replan time ───────────────────────────
                act.status = 'in_progress'
                # Apply duration override if provided (new total duration).
                # The override is permanent — it updates act.duration so that
                # CPM analytics and future replans reflect the revised estimate.
                if duration_overrides and act.name in duration_overrides:
                    new_total = float(duration_overrides[act.name])
                    act.duration = new_total
                    elapsed = (
                        (current_abs - st).total_seconds() / 3600.0
                        if st is not None else 0.0
                    )
                    remaining = max(0.0, new_total - elapsed)
                else:
                    remaining = (
                        (et - current_abs).total_seconds() / 3600.0
                        if et is not None else act.duration
                    )
                act._remaining_duration = max(0.0, remaining)
                self.ongoing.append(act)
                # Re-commit dose (committed when task started; still consumed).
                # Use act.duration (full) — _effective_duration returns _remaining_duration
                # for in-progress activities, which would under-charge the dose budget.
                # B3: prefer _actual_resources over declared crew_count so the charged
                # worker count matches the assignment that was actually made.
                if self.dose_trackers:
                    dose_rate = getattr(act, 'dose_rate_mrem_per_hour', 0.0)
                    if dose_rate > 0.0:
                        eff = max(0.0, act.duration)
                        actual_res = getattr(act, '_actual_resources', None)
                        if actual_res is not None:
                            for skill, workers in actual_res.items():
                                tracker = self.dose_trackers.get(skill)
                                if tracker:
                                    tracker.consume(dose_rate, workers, eff)
                        else:
                            for req in act.getRequiredResources():
                                tracker = self.dose_trackers.get(req['skill_type'])
                                if tracker:
                                    tracker.consume(dose_rate, req['crew_count'], eff)
                # Re-consume in-progress activity's consumables (deducted at start)
                if self.consumable_pool:
                    for req in act.getRequiredConsumables():
                        self.consumable_pool.consume(req['item_id'],
                                                     float(req['quantity_needed']))
                # Re-acquire system state locks for in-progress activities;
                # they are still holding their locks at replan time.
                if self.system_state_pool:
                    for req in act.getRequiredSystemStates():
                        self.system_state_pool.acquire(
                            req['system_id'], req['required_state']
                        )

            else:
                # ── Pending: not yet started ─────────────────────────────
                act.reset()         # clears startTime/endTime/delay/belongsToCP/status
                self.wait.add(act)

        logger.info(
            "_partial_reset at t=%.1fh: completed=%d in_progress=%d pending=%d",
            current_time_hours,
            len(self.completed), len(self.ongoing), len(self.wait),
        )
        self._completed_set = set(self.completed)
        self._rebuild_ready_set()
        self._compute_univ_skill_min()
        # Clear heap/cache — will be rebuilt after _generate_info_from updates
        # infoDict slack values (called by replan() before scheduling resumes).
        self._priority_cache = {}
        self._ready_heap = []
        self._heap_seq = {}

    def _inject_activities(
        self,
        new_activities: list,
        predecessor_wiring: dict = None,
    ) -> None:
        """Insert new Activity objects into the live graph before replanning.

        Each new activity's ``childs`` attribute lists successor task IDs
        (existing or other newly injected activities).  To wire *existing*
        activities as predecessors of new ones, pass ``predecessor_wiring``.

        After injection:
        * All new activities are registered in ``task_to_activity``.
        * ``forwardDict`` is extended.
        * ``backwardDict``, ``nxgraph``, and ``infoDict`` are rebuilt from
          scratch via ``resetInitialGraph()``.  Frozen scheduling state is
          preserved because it lives on the Activity objects themselves, not
          in these structural dicts.

        Args:
            new_activities: List of Activity objects to inject.  Activities
                            whose names already exist in the graph are skipped
                            with a warning.
            predecessor_wiring: Optional ``{new_task_id: [existing_pred_id, ...]}``
                dict.  For each entry, the named existing activities are
                added as predecessors of the new activity (i.e. edges
                existing_pred → new_task are inserted into ``forwardDict``).
                Unknown task IDs on either side are skipped with a warning.
                Lags for these edges default to 0; set them via
                ``act.successor_lags`` before calling if non-zero lags are
                needed.
        """
        # Ensure task_to_activity is complete for graph-built Pert objects
        # (graph= constructor does not populate this dict; injection needs it
        # to resolve successor names that belong to existing activities).
        for existing in self.forwardDict:
            self.task_to_activity.setdefault(existing.name, existing)

        for act in new_activities:
            if act.name in self.task_to_activity:
                logger.warning(
                    "inject_activities: task ID '%s' already in graph — skipping.",
                    act.name,
                )
                continue

            self.task_to_activity[act.name] = act

            # Resolve successor names to Activity objects
            succs: list = []
            for succ_id in act.childs:
                succ_act = self.task_to_activity.get(succ_id)
                if succ_act is None:
                    logger.warning(
                        "inject_activities: successor '%s' not found for '%s' — skipping edge.",
                        succ_id, act.name,
                    )
                    continue
                succs.append(succ_act)
                lag_h = act.successor_lags.get(succ_id, 0.0)
                if lag_h:
                    self.lag_dict[(act, succ_act)] = lag_h

            self.forwardDict[act] = succs
            act.status = 'pending'

        # Wire existing activities as predecessors of new ones.
        if predecessor_wiring:
            for new_name, pred_names in predecessor_wiring.items():
                new_act = self.task_to_activity.get(new_name)
                if new_act is None:
                    logger.warning(
                        "inject_activities: predecessor_wiring references "
                        "unknown new activity '%s' — skipping.", new_name,
                    )
                    continue
                if isinstance(pred_names, str):
                    pred_names = [pred_names]
                for pred_name in pred_names:
                    pred_act = self.task_to_activity.get(pred_name)
                    if pred_act is None:
                        logger.warning(
                            "inject_activities: predecessor '%s' not found "
                            "for new activity '%s' — skipping edge.",
                            pred_name, new_name,
                        )
                        continue
                    succs = self.forwardDict.setdefault(pred_act, [])
                    if new_act not in succs:
                        succs.append(new_act)

        # Rebuild graph structures (backwardDict + infoDict initialised to 0)
        self.resetInitialGraph()
        self.nxgraph = nx.DiGraph(self.forwardDict)

        logger.info(
            "inject_activities: %d new activities added; graph now has %d nodes.",
            len(new_activities), len(self.forwardDict),
        )

    def _generate_info_from(self, current_time_hours: float) -> None:
        """Partial CPM pass for replanning.

        Runs the standard forward / backward CPM pass but respects frozen
        activity state:

        * **completed** activities: ES/EF fixed from their actual start/end
          times (as offsets from outage start).  The forward pass skips their
          ES/EF updates so they serve as fixed anchors for successors.
        * **in_progress** activities: ES fixed at actual start offset; EF set
          to ``current_time_hours + act._remaining_duration`` (when they will
          finish, from the outage-start perspective).  Also frozen in the
          forward pass.
        * **pending** activities: standard CPM — ES is bounded below by
          ``current_time_hours`` so no pending activity can be scheduled in
          the past.

        After the CPM pass, ``_apply_time_windows()`` is called to tighten
        ES/LF for windowed activities, same as in ``generateInfo()``.

        Args:
            current_time_hours: Hours from outage start at the replan point.
        """
        if not self.forwardDict:
            return

        # Ensure infoDict['duration'] reflects any duration changes that may have
        # occurred since the last resetInfo() / generateInfo() call (e.g. a mode
        # switch performed outside the set_durations() / set_modes() helpers).
        self._sync_infodict_durations()

        sources = self._get_sources() if not self.startActivity else [self.startActivity]
        sinks   = self._get_sinks()   if not self.endActivity   else [self.endActivity]

        # ── 1) Topological order (Kahn's algorithm) — deque for O(1) popleft ──
        from collections import deque as _deque
        indeg = {a: 0 for a in self.forwardDict}
        for u, succs in self.forwardDict.items():
            for v in succs:
                indeg[v] = indeg.get(v, 0) + 1

        queue = _deque(a for a in sources if a in indeg)
        topo: list = []
        while queue:
            u = queue.popleft()
            topo.append(u)
            for v in self.forwardDict.get(u, []):
                indeg[v] -= 1
                if indeg[v] == 0:
                    queue.append(v)

        # ── 2) Seed ES / EF ──────────────────────────────────────────────
        for a in self.forwardDict:
            status = getattr(a, 'status', 'pending')
            if status == 'completed':
                st, et = a.returnAbsTimes()
                if st and et:
                    es_h = (st - self.startTime).total_seconds() / 3600.0
                    ef_h = (et - self.startTime).total_seconds() / 3600.0
                else:
                    es_h = ef_h = 0.0
                self.infoDict[a]['es'] = es_h
                self.infoDict[a]['ef'] = ef_h
            elif status == 'in_progress':
                st, _ = a.returnAbsTimes()
                es_h = (
                    (st - self.startTime).total_seconds() / 3600.0
                    if st else current_time_hours
                )
                remaining = getattr(a, '_remaining_duration', a.duration)
                ef_h = current_time_hours + remaining
                self.infoDict[a]['es'] = es_h
                self.infoDict[a]['ef'] = ef_h
            else:   # pending
                # Floor ES at current_time_hours so no pending activity is
                # scheduled in the past.  For source activities (no predecessors
                # to push them forward), also apply mobilization_lead_hours
                # relative to outage start: if the mobilization window has not
                # yet closed, the activity cannot start before
                # max(current_time_hours, mobilization_lead_hours).
                lead = getattr(a, 'mobilization_lead_hours', 0.0)
                base = max(current_time_hours, lead)
                self.infoDict[a]['es'] = base
                # Set EF = base + duration so that when this activity is a
                # source (no incoming edge in the forward pass), its successors
                # still receive the correct EF as an anchor.
                self.infoDict[a]['ef'] = base + self.infoDict[a]['duration']

        # ── 3) Forward pass (skip frozen activities) ─────────────────────
        for u in topo:
            u_ef = self.infoDict[u]['ef']
            for v in self.forwardDict.get(u, []):
                if getattr(v, 'status', 'pending') in ('completed', 'in_progress'):
                    continue    # frozen anchor — do not override
                lag  = self.lag_dict.get((u, v), 0.0)
                lead = getattr(v, 'mobilization_lead_hours', 0.0)
                earliest = u_ef + lag + lead
                if earliest >= self.infoDict[v]['es']:
                    self.infoDict[v]['es'] = earliest
                    self.infoDict[v]['ef'] = earliest + self.infoDict[v]['duration']

        # ── 4) Project duration = max EF across all sinks ─────────────────
        project_duration = max(self.infoDict[s]['ef'] for s in sinks)

        # ── 5) Backward pass ──────────────────────────────────────────────
        for a in self.forwardDict:
            self.infoDict[a]['lf'] = project_duration
            self.infoDict[a]['ls'] = self.infoDict[a]['lf'] - self.infoDict[a]['duration']

        for u in reversed(topo):
            for v in self.forwardDict.get(u, []):
                lag  = self.lag_dict.get((u, v), 0.0)
                lead = getattr(v, 'mobilization_lead_hours', 0.0)
                constrained_lf = self.infoDict[v]['ls'] - lag - lead
                if self.infoDict[u]['lf'] > constrained_lf:
                    self.infoDict[u]['lf'] = constrained_lf
                    self.infoDict[u]['ls'] = self.infoDict[u]['lf'] - self.infoDict[u]['duration']

        # ── 6) Slack ──────────────────────────────────────────────────────
        for a in self.forwardDict:
            self.infoDict[a]['slack'] = self.infoDict[a]['ls'] - self.infoDict[a]['es']

        # ── 7) Time-window post-processing ───────────────────────────────
        # Pass topo so tightened LF values propagate back to predecessors.
        self._apply_time_windows(topo=topo)

        # ── 8) WBS aggregate float roll-up ───────────────────────────────
        self._compute_wbs_slack()

    def _build_event_queue_from(self, current_time_hours: float) -> list:
        """Build the initial event heap for the replanning scheduling loop.

        Like ``_build_event_queue`` but anchored at ``current_time_hours``
        instead of the project start:

        1. ``current_abs`` — replan anchor point.
        2. Completion times of in-progress activities.
        3. Availability boundaries ≥ ``current_abs``.
        4. CPM ES of pending activities (already ≥ ``current_abs`` after
           ``_generate_info_from``).
        5. Window-open times for pending activities.
        6. Shift-start boundaries for partial-day schedules.

        Returns:
            list: A valid heapq (min-heap) of datetime objects.
        """
        current_abs = self.startTime + timedelta(hours=current_time_hours)
        events: set = set()

        # 1) Replan anchor
        events.add(current_abs)

        # 2) In-progress completion times
        for act in self.ongoing:
            _, et = act.returnAbsTimes()
            if et and et >= current_abs:
                events.add(et)

        # 3) Availability boundaries at or after replan time
        for dt in self._availability_events:
            if dt >= current_abs:
                events.add(dt)

        # 4) CPM ES of pending activities
        for act in self.wait:
            abs_es = self.startTime + timedelta(hours=self.infoDict[act]['es'])
            if abs_es >= current_abs:
                events.add(abs_es)

        # 5) Window-open times for pending activities (all windows, including
        #    multi-window activities).
        for act in self.wait:
            for (west_h, _) in self._resolve_windows(act):
                if west_h > 0.0 and west_h < float('inf'):
                    window_open = self.startTime + timedelta(hours=west_h)
                    if window_open >= current_abs:
                        events.add(window_open)

        # 6) Shift-start boundaries for partial-day schedules
        if self.working_hours_per_day < 24:
            cpm_end = self.startTime + timedelta(
                hours=self.getProjectDuration() * self._max_time_factor
            )
            for shift_dt in self._shift_boundary_events(current_abs, cpm_end):
                events.add(shift_dt)

        # 7) Consumable restock delivery times at or after the replan anchor.
        if self.consumable_pool:
            for item_id in self.consumable_pool.get_all_item_ids():
                for delivery_hour, _ in self.consumable_pool.restocks.get(item_id, []):
                    restock_dt = self.startTime + timedelta(hours=delivery_hour)
                    if restock_dt >= current_abs:
                        events.add(restock_dt)

        heap = list(events)
        heapq.heapify(heap)
        return heap

    def calculateScheduleWithResources_from(
        self,
        current_time_hours: float,
        sgs: str = 'max_use_res_ranked',
        max_time_hours: float = None,
        priority_rule: str = '',
    ) -> dict:
        """Resume the resource-constrained scheduling loop from a mid-outage
        replan point.

        Unlike ``calculateScheduleWithResources()``, this method does **not**
        call ``_reset_scheduling_state()``; the caller (``replan()``) is
        expected to have already called ``_partial_reset()`` and
        ``_generate_info_from()`` to establish the correct frozen state and
        updated CPM values.

        The scheduling loop is identical to the baseline scheduler with three
        differences:

        1. The event heap is seeded from ``current_time_hours`` via
           ``_build_event_queue_from()``.
        2. ``self.completed`` and ``self.ongoing`` are pre-populated with
           frozen activities, so the completion predicate
           (``len(completed) == n_activities``) accounts for them from the start.
        3. A ``'replan_time_hours'`` key is added to the result dict.

        Args:
            current_time_hours: Offset from outage start in hours.
            sgs: Schedule Generation Scheme name.
            max_time_hours: Safety cutoff (defaults to CPM × _max_time_factor).
            priority_rule: Override priority rule (empty = TF-based).

        Returns:
            Same dict as ``calculateScheduleWithResources()``, plus
            ``'replan_time_hours'``.
        """
        if not self.crew_pool or not self.equipment_pool or not self.location_pool:
            raise ValueError("Resource, equipment, and location pools must be initialised")
        if not self.startTime:
            raise ValueError("startTime must be set before scheduling")

        current_abs  = self.startTime + timedelta(hours=current_time_hours)
        cpm_duration = self.getProjectDuration()
        n_activities = len(self.infoDict)

        if max_time_hours is None:
            max_time_hours = cpm_duration * self._max_time_factor
        max_time = self.startTime + timedelta(hours=max_time_hours)

        logger.info(
            "Replanning from t=%.1fh | total=%d (frozen=%d in_progress=%d pending=%d)"
            " | CPM=%.1fh | strategy=%s",
            current_time_hours, n_activities,
            len(self.completed), len(self.ongoing), len(self.wait),
            cpm_duration, sgs,
        )

        # Seed event heap from current time
        event_heap = self._build_event_queue_from(current_time_hours)

        # ── Determine value_mode once (constant for entire run) ──────────
        if self.priorities is not None:
            value_mode = 'external'
        elif priority_rule:
            value_mode = priority_rule
        else:
            value_mode = 'TF_based'

        # Fix III: precompute priority cache and ready heap
        self._compute_priority_cache(value_mode)
        self._build_ready_heap()

        # ── Main event-driven loop ────────────────────────────────────────
        iteration  = 0
        time_index = current_abs

        while len(self.completed) != n_activities:

            if not event_heap:
                if self.wait and not self.ongoing:
                    logger.warning(
                        "Replan event queue exhausted with %d activities waiting "
                        "and nothing ongoing. Completed %d/%d.",
                        len(self.wait), len(self.completed), n_activities,
                    )
                break

            time_index = heapq.heappop(event_heap)
            while event_heap and (event_heap[0] - time_index) <= self._EVENT_EPSILON:
                heapq.heappop(event_heap)

            if time_index > max_time:
                logger.warning(
                    "Replan reached safety cutoff at %s. Completed %d/%d.",
                    time_index.strftime('%Y-%m-%d %H:%M'),
                    len(self.completed), n_activities,
                )
                break

            iteration += 1
            self._update_ongoing_list(time_index)

            if len(self.completed) == n_activities:
                break

            candidates = self._select_candidate_activities(time_index, value_mode)

            next_event: datetime | None = event_heap[0] if event_heap else None
            elapsed_hours = (
                (next_event - time_index).total_seconds() / 3600.0
                if next_event and next_event > time_index else 0.0
            )

            selected = []
            if candidates:
                selected = self._schedule_generation_scheme(candidates, time_index, sgs)
                self._update_activity_sets(selected, candidates, time_index, elapsed_hours)
                for act in selected:
                    _, end_time = act.returnAbsTimes()
                    if end_time:
                        heapq.heappush(event_heap, end_time)

                self.schedule_log.append({
                    'time':        time_index,
                    'candidates':  [a.name for a in candidates],
                    'selected':    [a.name for a in selected],
                    'ongoing':     [a.name for a in self.ongoing],
                    'n_completed': len(self.completed),
                })

            logger.debug(
                "replan t=%s | iter=%d | completed=%d/%d | ongoing=%d | "
                "waiting=%d | candidates=%d | selected=%d",
                time_index.strftime('%Y-%m-%d %H:%M'), iteration,
                len(self.completed), n_activities,
                len(self.ongoing), len(self.wait),
                len(candidates), len(selected),
            )

        # ── Post-schedule analytics ───────────────────────────────────────
        self._compute_actual_tf_proxy()
        self._compute_resource_constrained_chain()

        actual_end      = self.get_project_finish_actual()
        actual_duration = (actual_end - self.startTime).total_seconds() / 3600.0
        total_delay     = sum(act.delay for act in self.forwardDict)

        results = {
            'scheduled_duration': float(actual_duration),
            'cpm_duration':       float(cpm_duration),
            'delay_hours':        float(total_delay),
            'n_activities':       n_activities,
            'n_completed':        len(self.completed),
            'iterations':         iteration,
            'window_violations':  list(self._window_violations[self._window_violations_baseline:]),
            'replan_time_hours':  float(current_time_hours),
        }

        logger.info(
            "Replanning complete | actual=%.1fh | completed=%d/%d | iterations=%d",
            actual_duration, len(self.completed), n_activities, iteration,
        )
        self._last_schedule_result = results
        return results

    def replan(
        self,
        current_time_hours: float,
        new_activities: list = None,
        predecessor_wiring: dict = None,
        resource_updates: list = None,
        equipment_updates: list = None,
        duration_overrides: dict = None,
        sgs: str = 'max_use_res_ranked',
        max_time_hours: float = None,
    ) -> dict:
        """Replan the remaining schedule from a mid-outage snapshot.

        Intended workflow::

            # 1) Build and run the initial schedule
            p = Pert.from_json_file(...)
            p.calculateScheduleWithResources(sgs='max_use_res_ranked')

            # 2) At t=48h: welder calls in sick, ISI crawler breaks, new task found
            new_task = Activity('T_EMERG', 6.0)
            new_task.childs = ['T_DOWNSTREAM']
            result = p.replan(
                current_time_hours=48.0,
                new_activities=[new_task],
                resource_updates=[
                    {'skill_type': 'WELDER', 'from_hour': 48, 'new_count': 2},
                ],
                equipment_updates=[
                    {'equipment_id': 'ISI_CRAWLER', 'from_hour': 48,
                     'until_hour': 60, 'new_quantity': 0},
                ],
                duration_overrides={'T_ISI_INSP': 32.0},
            )

        Steps performed internally:

        1. Apply ``resource_updates`` — mutate ResourcePool availability.
        2. Apply ``equipment_updates`` — mutate EquipmentPool availability.
        3. Rebuild ``_availability_events`` (pool boundaries have changed).
        4. ``_inject_activities(new_activities)`` — extend the graph (if any).
        5. ``_partial_reset(current_time_hours, duration_overrides)`` — classify
           activities; replay dose; apply duration overrides for in-progress.
        6. Rebuild nxgraph.
        7. ``_generate_info_from(current_time_hours)`` — partial CPM.
        8. ``calculateScheduleWithResources_from(...)`` — scheduling loop.

        Args:
            current_time_hours: Hours from outage start at the replan trigger.
            new_activities: Optional list of Activity objects to inject.
            predecessor_wiring: Optional ``{new_task_id: [existing_pred_id, ...]}``
                passed directly to ``_inject_activities``.  Ignored when
                ``new_activities`` is None or empty.
            resource_updates: Optional list of dicts, each with keys:
                ``skill_type`` (str), ``from_hour`` (float),
                ``new_count`` (int), ``until_hour`` (float, optional).
            equipment_updates: Optional list of dicts, each with keys:
                ``equipment_id`` (str), ``from_hour`` (float),
                ``new_quantity`` (int), ``until_hour`` (float, optional).
            duration_overrides: Optional {task_id: new_total_duration_hours}
                for in-progress activities whose remaining time has changed.
                Updates ``act.duration`` permanently.
            sgs: Schedule Generation Scheme strategy name.
            max_time_hours: Safety cutoff in hours (default: CPM × 10).

        Returns:
            Same dict as ``calculateScheduleWithResources()``, plus
            ``'replan_time_hours'``.

        Raises:
            ValueError: If pools or startTime are not set, or if a resource /
                        equipment update specifies from_hour < 0.
            RuntimeError: If called before any scheduling run has been
                          performed.
        """
        if not self.startTime:
            raise ValueError("startTime must be set before replanning")
        if not self.crew_pool or not self.equipment_pool or not self.location_pool:
            raise ValueError("Resource, equipment, and location pools must be initialised")

        # Guard: at least one scheduling run must have happened
        any_scheduled = any(
            act.returnAbsTimes()[0] is not None
            for act in self.forwardDict
        )
        if not any_scheduled:
            raise RuntimeError(
                "replan() called before any scheduling run. "
                "Execute calculateScheduleWithResources() first to establish "
                "the baseline schedule."
            )

        # ── Step 1: apply resource availability updates ───────────────────
        if resource_updates:
            for upd in resource_updates:
                skill     = upd['skill_type']
                from_h    = float(upd['from_hour'])
                new_count = int(upd['new_count'])
                until_h   = upd.get('until_hour')
                if from_h < 0:
                    raise ValueError(
                        f"resource_updates: from_hour={from_h} must be ≥ 0 "
                        f"(no retroactive changes allowed)"
                    )
                self.crew_pool.update_skill_from_hour(
                    skill, self.startTime, from_h, new_count,
                    until_hour=until_h,
                )
                logger.info(
                    "replan: resource '%s' → %d workers from h=%.1f%s",
                    skill, new_count, from_h,
                    f" until h={until_h}" if until_h is not None else " (permanent)",
                )

        # ── Step 2: apply equipment availability updates ──────────────────
        if equipment_updates:
            for upd in equipment_updates:
                eq_id     = upd['equipment_id']
                from_h    = float(upd['from_hour'])
                new_qty   = int(upd['new_quantity'])
                until_h   = upd.get('until_hour')
                if from_h < 0:
                    raise ValueError(
                        f"equipment_updates: from_hour={from_h} must be ≥ 0"
                    )
                self.equipment_pool.update_equipment_from_hour(
                    eq_id, self.startTime, from_h, new_qty,
                    until_hour=until_h,
                )
                logger.info(
                    "replan: equipment '%s' → %d units from h=%.1f%s",
                    eq_id, new_qty, from_h,
                    f" until h={until_h}" if until_h is not None else " (permanent)",
                )

        # ── Step 3: rebuild availability event boundaries ─────────────────
        # Pool mutations may add new period boundaries that the scheduler
        # must treat as event points (e.g. the hour when the broken crawler
        # returns to service).  Recompute before building the event heap.
        if resource_updates or equipment_updates:
            self._precompute_availability_events()

        # ── Step 4: inject new activities ────────────────────────────────
        if new_activities:
            self._inject_activities(new_activities,
                                    predecessor_wiring=predecessor_wiring)

        # ── Step 5: classify / reset activities (with duration overrides) ─
        self._partial_reset(current_time_hours,
                            duration_overrides=duration_overrides)

        # Rebuild nxgraph after possible injection (inject does this, but
        # belt-and-suspenders after _partial_reset may have mutated lists)
        self.nxgraph = nx.DiGraph(self.forwardDict)

        # ── Step 6: partial CPM ───────────────────────────────────────────
        self._generate_info_from(current_time_hours)

        # ── Step 7: reschedule pending activities ─────────────────────────
        return self.calculateScheduleWithResources_from(
            current_time_hours,
            sgs=sgs,
            max_time_hours=max_time_hours,
        )


    def clone_for_analysis(self) -> 'Pert':
        """Return a copy of this Pert suitable for what-if schedule analysis.

        Added to support DACKAR emergent-activity analysis (outage Stage E):
        provides a clean graph copy for deterministic or Monte-Carlo what-if
        runs without copying scheduling history or mutating the baseline Pert.

        Compared with copy.deepcopy(pert):
          - Scheduling state (wait / ongoing / completed / schedule_log /
            actual_tf / constrained_chain) is initialised fresh rather than
            copied.  The clone starts as if no run has been executed yet.
          - Resource, equipment, location, consumable, and system-state pools
            are deep-copied so that replan() calls on the clone cannot mutate
            the baseline's pool state.  This enables safe what-if comparisons.
          - The NetworkX graph is rebuilt from the copied forwardDict so that
            node identity is consistent across all topology structures.

        The caller is responsible for calling resetInfo() + generateInfo() (or
        only generateInfo() if infoDict is already initialised) after any
        topology mutations before reading CPM results.

        Returns:
            A new Pert instance with independent topology, deep-copied pools,
            and fresh scheduling state.
        """
        # Deep copy all topology structures in a single call so that Activity
        # object identity is preserved across forwardDict, backwardDict,
        # infoDict, and task_to_activity (they all key/reference the same
        # Activity instances in the copy).
        topology = copy.deepcopy({
            'forwardDict':    self.forwardDict,
            'backwardDict':   self.backwardDict,
            'infoDict':       self.infoDict,
            'task_to_activity': self.task_to_activity,
        })

        # Build clone without triggering __init__ graph construction
        clone = object.__new__(Pert)

        # Topology (deep copied — independent from baseline)
        clone.forwardDict      = topology['forwardDict']
        clone.backwardDict     = topology['backwardDict']
        clone.infoDict         = topology['infoDict']
        clone.task_to_activity = topology['task_to_activity']

        # Rebuild NetworkX graph from copied forwardDict so node identity
        # matches the copied Activity objects (not the originals)
        clone.nxgraph = nx.DiGraph(clone.forwardDict)

        # START / END sentinels (locate in copied topology)
        clone.startActivity = None
        clone.endActivity   = None
        for act in clone.forwardDict:
            if act.name.upper() == 'START':
                clone.startActivity = act
            elif act.name.upper() == 'END':
                clone.endActivity = act

        # Config — shallow copy (scalar / immutable values)
        clone.startTime             = self.startTime
        clone.working_hours_per_day = self.working_hours_per_day
        clone.shift_start_hour      = getattr(self, 'shift_start_hour', 0)
        clone.seed                  = self.seed
        clone._max_time_factor      = self._max_time_factor
        clone._list_priority_names  = self._list_priority_names
        clone.priorities = copy.copy(self.priorities) if self.priorities else None

        # Lag dict: re-map keys to point to copied Activity instances.
        # The topology deep-copy preserved Activity identity within the clone,
        # so we need to translate original Activity keys → cloned Activity keys.
        orig_to_clone: dict = {}
        for orig_act, clone_act in zip(self.forwardDict.keys(), clone.forwardDict.keys()):
            orig_to_clone[orig_act] = clone_act
        clone.lag_dict = {
            (orig_to_clone.get(u, u), orig_to_clone.get(v, v)): lag
            for (u, v), lag in self.lag_dict.items()
        }

        # Resource / equipment / location pools — deep-copied so that
        # replan() on the clone (which mutates pool periods) does not affect
        # the original Pert's pools.  This enables safe what-if comparisons:
        #   clone1 = baseline.clone_for_analysis(); clone1.replan(48, resource_updates=[...])
        #   clone2 = baseline.clone_for_analysis(); clone2.replan(48, resource_updates=[...])
        # Each clone has an independent copy of the pool state.
        clone.crew_pool   = copy.deepcopy(self.crew_pool)
        clone.equipment_pool  = copy.deepcopy(self.equipment_pool)
        clone.location_pool   = copy.deepcopy(self.location_pool)
        clone.outage_data     = self.outage_data  # read-only config reference
        # Consumable and system-state pools have mutable runtime state; deep-copy
        # so that replan() on the clone does not affect the baseline's pool state.
        clone.consumable_pool   = copy.deepcopy(self.consumable_pool) \
                                  if self.consumable_pool   else None
        clone.system_state_pool = copy.deepcopy(self.system_state_pool) \
                                  if self.system_state_pool else None
        # Dose trackers are also mutable; rebuild fresh from the copied resource pool
        # so the clone's trackers reference its own pool (not the original's).
        clone.dose_trackers = clone.crew_pool.build_dose_trackers() \
                              if clone.crew_pool else {}

        # Fresh scheduling state — not inherited from the baseline run
        clone.wait       = list(clone.forwardDict.keys())
        clone.ongoing    = []
        clone.completed  = []
        clone.schedule_log           = []
        clone.actual_tf              = {}
        clone.actual_zero_tf_set     = set()
        clone.constrained_chain_list = []
        clone.constrained_chain_set  = set()

        # Availability events will be recomputed on demand if scheduling runs
        clone._availability_events = frozenset()

        # Window violations and last schedule result start fresh in the clone
        # (they are historical records of the baseline run, not structural data)
        clone._window_violations    = []
        clone._last_schedule_result = {}

        return clone

    # ========================================================================
    # SHIFT CALENDAR HELPERS
    # ========================================================================

    def _is_work_time(self, t: datetime) -> bool:
        """Return True if *t* falls inside an active work shift.

        When ``working_hours_per_day >= 24`` every hour is valid (24/7 plant
        operations).  Otherwise a single recurring shift is assumed:

        * The shift starts daily at ``shift_start_hour`` (0-based hour).
        * It lasts exactly ``working_hours_per_day`` hours.
        * Off-shift hours (the remaining ``24 - working_hours_per_day`` h) are
          excluded from scheduling.

        Fractional-minute precision is preserved by comparing the total
        decimal hours within the day rather than the integer hour field.

        Args:
            t: Datetime to test.

        Returns:
            bool: True if activities may start at *t*.
        """
        if self.working_hours_per_day >= 24:
            return True
        hour_frac = t.hour + t.minute / 60.0 + t.second / 3600.0
        shift_end_hour = (self.shift_start_hour + self.working_hours_per_day) % 24
        if self.shift_start_hour < shift_end_hour:
            # Shift does not cross midnight
            return self.shift_start_hour <= hour_frac < shift_end_hour
        else:
            # Shift crosses midnight (e.g. starts 22:00, ends 06:00)
            return hour_frac >= self.shift_start_hour or hour_frac < shift_end_hour

    def _next_shift_start_after(self, t: datetime) -> datetime:
        """Return the earliest shift-start time that is >= *t*.

        Used to snap an activity's eligible start forward to the next open
        shift window when the current time falls in an off-shift period.

        Returns:
            datetime: Start of the next (or current, if already in shift) window.
        """
        if self._is_work_time(t):
            return t

        # Snap to today's shift start; advance by one day if it is in the past.
        candidate = t.replace(
            hour=self.shift_start_hour, minute=0, second=0, microsecond=0
        )
        if candidate <= t:
            candidate += timedelta(days=1)
        return candidate

    def _shift_boundary_events(self, start: datetime, end: datetime) -> list:
        """Return all shift-start datetimes in [start, end] for event seeding.

        Only meaningful when ``working_hours_per_day < 24``; returns an empty
        list for 24/7 schedules.

        Args:
            start: Window start (inclusive).
            end:   Window end (inclusive).
        """
        if self.working_hours_per_day >= 24:
            return []
        events: list = []
        # Walk day-by-day from the first shift-start on or after *start*.
        candidate = start.replace(
            hour=self.shift_start_hour, minute=0, second=0, microsecond=0
        )
        if candidate < start:
            candidate += timedelta(days=1)
        while candidate <= end:
            events.append(candidate)
            candidate += timedelta(days=1)
        return events

    # ========================================================================
    # RESOURCE-CONSTRAINED PROJECT SCHEDULING (RCPSP) METHODS
    # ========================================================================

    def _precompute_availability_events(self) -> None:
        """
        Collect every availability-period boundary datetime from the resource,
        equipment, and location pools and store them as a frozenset.

        Called once at construction time (__init__ / from_json_file).  Because
        the pools are read-only after loading, the result never becomes stale
        and does not need to be recomputed between RAVEN iterations.

        Storing boundaries as a frozenset gives O(1) membership tests and
        makes the set trivially hashable / loggable.

        Standalone usage note:
            Works identically whether the object is created via from_json_file()
            or constructed manually, because __init__ always runs.  If no pools
            are present (graph-only construction) the frozenset remains empty
            and the event-driven scheduler falls back to activity-based events.
        """
        events: set = set()

        if self.crew_pool:
            for skill in self.crew_pool.get_all_skills():
                for period in self.crew_pool.resources[skill].get_all_periods():
                    events.add(period['start_date'])
                    events.add(period['end_date'])

        if self.equipment_pool:
            for eq_id in self.equipment_pool.get_all_equipment_ids():
                for period in self.equipment_pool.equipment[eq_id].get_all_periods():
                    events.add(period['start_date'])
                    events.add(period['end_date'])

        if self.location_pool:
            for loc_id in self.location_pool.get_all_location_ids():
                for period in self.location_pool.locations[loc_id].get_all_periods():
                    events.add(period['start_date'])
                    events.add(period['end_date'])

        self._availability_events = frozenset(events)
        logger.debug(
            "_precompute_availability_events: collected %d boundary events",
            len(self._availability_events)
        )


    # ── New method 2 ─────────────────────────────────────────────────────────────

    def _build_event_queue(self) -> list:
        """
        Build the initial event min-heap for the event-driven scheduling loop.

        The heap is seeded with three categories of events:

        1. Project start time — guarantees the loop always has a first step.

        2. Pre-computed availability boundaries (>= startTime) — ensures the
           scheduler wakes up whenever resource/equipment/location capacity
           changes, even if no activity completes at that exact moment.
           These are free to add because _precompute_availability_events()
           already did the scanning work at construction time.

        3. Absolute ES of every waiting activity — ensures the scheduler wakes
           up exactly when each activity first becomes time-eligible, avoiding
           spinning through empty periods.

        Activity completion times are NOT seeded here; they are pushed onto
        the heap dynamically inside calculateScheduleWithResources() as each
        activity is started, because completion times depend on the actual
        start time (which is determined by resource availability, not just ES).

        Returns:
            list: A valid heapq (min-heap) of datetime objects.
        """
        events: set = set()

        # 1) Always include the project start
        events.add(self.startTime)

        # 2) Availability boundaries that lie at or after project start
        for dt in self._availability_events:
            if dt >= self.startTime:
                events.add(dt)

        # 3) Absolute ES of all activities currently in the wait list
        for act in self.wait:
            abs_es = self.startTime + timedelta(hours=self.infoDict[act]['es'])
            if abs_es >= self.startTime:
                events.add(abs_es)

        # 4) Shift-start boundaries for partial-day schedules.
        #    Without these events the loop would stall in off-shift periods
        #    because no activity completion or availability change would wake it
        #    up to re-check candidates at the next shift open.
        if self.working_hours_per_day < 24:
            cpm_end = self.startTime + timedelta(
                hours=self.getProjectDuration() * self._max_time_factor
            )
            for shift_dt in self._shift_boundary_events(self.startTime, cpm_end):
                events.add(shift_dt)

        # 5) Window-open times for activities with earliest-start constraints.
        #    Without these events the loop might skip over the window-open moment
        #    if no other event coincides with it, causing the activity to wait
        #    until the next unrelated event rather than starting at the earliest
        #    allowed time.  Seed an event for every window's open time (handles
        #    both single-window and multi-window activities uniformly).
        for act in self.wait:
            for (west_h, _) in self._resolve_windows(act):
                if west_h > 0.0 and west_h < float('inf'):
                    window_open = self.startTime + timedelta(hours=west_h)
                    if window_open >= self.startTime:
                        events.add(window_open)

        # 6) Consumable restock delivery times.
        #    Without these events an activity blocked on a depleted consumable
        #    pool would not be re-checked until the next unrelated event, causing
        #    it to start later than necessary.  Seeding each delivery time
        #    guarantees the loop wakes up exactly when new inventory arrives.
        if self.consumable_pool:
            for item_id in self.consumable_pool.get_all_item_ids():
                for delivery_hour, _ in self.consumable_pool.restocks.get(item_id, []):
                    restock_dt = self.startTime + timedelta(hours=delivery_hour)
                    if restock_dt >= self.startTime:
                        events.add(restock_dt)

        heap = list(events)
        heapq.heapify(heap)

        logger.debug(
            "_build_event_queue: heap seeded with %d events "
            "(%d availability boundaries + start + ES times)",
            len(heap),
            sum(1 for dt in self._availability_events if dt >= self.startTime)
        )
        return heap


    # Epsilon for merging near-simultaneous events into one scheduling step.
    # 1 minute is tight enough to catch genuine coincident events (e.g. two
    # activities ending at the same time) while ignoring floating-point jitter
    # in duration arithmetic.
    _EVENT_EPSILON = timedelta(minutes=1)

    def calculateScheduleWithResources(self, sgs: str = 'max_use_res_ranked',
                                       max_time_hours: float = None, priority_rule: str = '',
                                       tie_breaker: str = 'mehh_8000_b') -> dict:
        """
        Schedule activities considering resource, equipment, and location
        constraints using an event-driven scheduling loop.

        The scheduler advances time only to the next meaningful event rather
        than stepping hour-by-hour.  Events are:
          - Activity completions  (pushed dynamically as activities start)
          - Availability-period boundaries (pre-computed at construction)
          - Absolute early-start times of waiting activities (seeded at run start)

        Near-simultaneous events within _EVENT_EPSILON (1 minute) are merged
        into a single scheduling step to avoid redundant iterations caused by
        floating-point duration arithmetic.

        Works in both standalone and RAVEN (BaseCPMmodel) modes.

        Args:
            sgs (str): Schedule Generation Scheme strategy name.
            max_time_hours (float, optional): Safety cutoff in hours from
                startTime.  Defaults to self._max_time_factor x the CPM duration.
            tie_breaker (str, optional): infoDict key used to break ties in
                priority sorting.  Defaults to 'mehh_8000_b'.  Pass None to
                disable tie-breaking.

        Returns:
            dict: {
                'scheduled_duration': float,   # hours from startTime to last end
                'cpm_duration':        float,   # unconstrained CPM duration
                'delay_hours':         float,   # total accumulated delay
                'n_activities':        int,
                'n_completed':         int,
                'iterations':          int      # number of event-loop steps
            }
        """
        if not self.crew_pool or not self.equipment_pool or not self.location_pool:
            raise ValueError(
                "Resource, equipment, and location pools must be initialised"
            )
        if not self.startTime:
            raise ValueError("startTime must be set before scheduling")

        # ── Clean slate (Issue 3 fix) ────────────────────────────────────────
        self._reset_scheduling_state()   # resets activities + wait/ongoing/completed

        # ── Reference values ────────────────────────────────────────────────
        cpm_duration  = self.getProjectDuration()
        n_activities  = len(self.infoDict)
        if max_time_hours is None:
            max_time_hours = cpm_duration * self._max_time_factor   # generous safety margin
        max_time = self.startTime + timedelta(hours=max_time_hours)

        logger.info(
            "Starting event-driven RCPSP | activities=%d | CPM=%.1fh | "
            "strategy=%s | max_time=%.1fh",
            n_activities, cpm_duration, sgs, max_time_hours
        )

        # ── Determine value_mode once (constant for entire run) ──────────────
        if self.priorities is not None:
            value_mode = 'external'
        elif priority_rule:
            value_mode = priority_rule
        else:
            value_mode = 'TF_based'

        # Fix III: precompute static priority cache + ready heap so that
        # _select_candidate_activities can use O(K log n) extraction.
        self._compute_priority_cache(value_mode)
        self._build_ready_heap()

        # ── Bootstrap START activity ─────────────────────────────────────────
        if self.startActivity and self.startActivity in self.wait:
            self.startActivity.setActualStartTime(self.startTime)
            self.wait.discard(self.startActivity)
            self._ready.discard(self.startActivity)
            self.ongoing.append(self.startActivity)
            # Heap entry for startActivity is lazily discarded on first pop
            # (startActivity is no longer in _ready after discard above).
            _, start_end = self.startActivity.returnAbsTimes()
            event_heap = []
            heapq.heappush(event_heap, start_end)
        else:
            event_heap = []

        # ── Seed the event heap ──────────────────────────────────────────────
        for dt in self._build_event_queue():
            heapq.heappush(event_heap, dt)

        # ── Main event-driven loop ───────────────────────────────────────────
        iteration   = 0
        time_index  = self.startTime

        while len(self.completed) != n_activities:

            # ── Deadlock / exhaustion guard ──────────────────────────────────
            if not event_heap:
                if self.wait and not self.ongoing:
                    logger.warning(
                        "Event queue exhausted with %d activities still waiting "
                        "and nothing ongoing — possible deadlock. "
                        "Completed %d/%d.",
                        len(self.wait), len(self.completed), n_activities
                    )
                break

            # ── Pop next event, merge events within epsilon ──────────────────
            time_index = heapq.heappop(event_heap)
            while event_heap and (event_heap[0] - time_index) <= self._EVENT_EPSILON:
                heapq.heappop(event_heap)   # discard near-duplicate

            # ── Safety cutoff ────────────────────────────────────────────────
            if time_index > max_time:
                logger.warning(
                    "Scheduling reached safety cutoff at %s. "
                    "Completed %d/%d activities.",
                    time_index.strftime('%Y-%m-%d %H:%M'),
                    len(self.completed), n_activities
                )
                break

            iteration += 1

            # Update ongoing activities (move completed to completed list)
            # ── Move finished activities to completed ────────────────────────
            self._update_ongoing_list(time_index)

            if len(self.completed) == n_activities:
                break   # finished exactly on this event

            # ── Find candidates eligible at time_index ───────────────────────
            if self.priorities is not None:
                value_mode = 'external'
            else:
                if not priority_rule:
                    value_mode = 'TF_based'
                else:
                    value_mode = priority_rule
            candidates = self._select_candidate_activities(time_index, value_mode, tie_breaker=tie_breaker)

            # ── Determine elapsed time to next event (for delay accounting) ──
            # Peek at the heap without popping; used by _update_activity_sets
            # to record how long postponed activities will wait.
            next_event: datetime | None = event_heap[0] if event_heap else None
            elapsed_hours: float = (
                (next_event - time_index).total_seconds() / 3600.0
                if next_event and next_event > time_index
                else 0.0
            )

            selected = []
            if candidates:
                selected = self._schedule_generation_scheme(
                    candidates, time_index, sgs
                )
                self._update_activity_sets(
                    selected, candidates, time_index, elapsed_hours
                )

                # Push completion times of newly started activities
                for act in selected:
                    _, end_time = act.returnAbsTimes()
                    if end_time:
                        heapq.heappush(event_heap, end_time)

                self.schedule_log.append({
                    'time':        time_index,
                    'candidates':  [a.name for a in candidates],
                    'selected':    [a.name for a in selected],
                    'ongoing':     [a.name for a in self.ongoing],
                    'n_completed': len(self.completed),
                })

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('==============')
                logger.debug(f"t={time_index.strftime('%Y-%m-%d %H:%M')}")
                logger.debug(f"completed={[a.name for a in self.completed]}")
                logger.debug(f"ongoing={[a.name for a in self.ongoing]}")
                logger.debug(f"waiting={[a.name for a in self.wait]}")
                if candidates:
                    logger.debug(f"candidates={[a.name for a in candidates.keys()]}")
                if selected:
                    logger.debug(f"selected={[a.name for a in selected]}")

            logger.debug(
                "t=%s | iter=%d | completed=%d/%d | ongoing=%d | "
                "waiting=%d | candidates=%d | selected=%d | heap_size=%d",
                time_index.strftime('%Y-%m-%d %H:%M'),
                iteration,
                len(self.completed), n_activities,
                len(self.ongoing),
                len(self.wait),
                len(candidates),
                len(selected),
                len(event_heap),
            )

        # ── Post-schedule analytics ──────────────────────────────────────────
        self._compute_actual_tf_proxy()
        self._compute_resource_constrained_chain()

        actual_end      = self.get_project_finish_actual()
        actual_duration = (actual_end - self.startTime).total_seconds() / 3600.0
        total_delay     = sum(act.delay for act in self.forwardDict)

        results = {
            'scheduled_duration': actual_duration,
            'cpm_duration':       cpm_duration,
            'delay_hours':        total_delay,
            'n_activities':       n_activities,
            'n_completed':        len(self.completed),
            'iterations':         iteration,
            'window_violations':  list(self._window_violations),
        }

        logger.info(
            "Scheduling complete | CPM=%.1fh | actual=%.1fh | "
            "delay=%.1fh | completed=%d/%d | iterations=%d",
            cpm_duration, actual_duration,
            total_delay, len(self.completed), n_activities, iteration
        )
        # Cache so compute_fitness() can access scheduling metrics without
        # requiring callers to thread return values through their code.
        self._last_schedule_result = results
        return results


    def _update_activity_sets(self, selected: list, candidates: dict,
                               time_index: datetime,
                               elapsed_hours: float = 0.0) -> None:
        """
        Update activity tracking lists after the SGS has made its selection.

        - Selected activities move from wait → ongoing with their start time set.
        - Postponed candidates (candidates not selected) accumulate delay equal
          to elapsed_hours — the time until the next scheduling event — rather
          than a fixed 1-hour increment, matching the variable step size of the
          event-driven loop.

        Args:
            selected (list):      Activities chosen to start at time_index.
            candidates (dict):    Full candidate set considered this step.
            time_index (datetime): Current event time.
            elapsed_hours (float): Hours to the next event; used for delay
                                   accounting on postponed activities.
                                   Defaults to 0.0 if no next event exists.
        """
        if selected:
            for act in selected:
                act.setActualStartTime(time_index)
                # Fix I: lazy delay — compute once at start from first-candidate time
                # instead of accumulating addDelay(elapsed) each step (was O(n²/k)).
                if act._candidate_since is not None:
                    act.delay = max(
                        0.0,
                        (time_index - act._candidate_since).total_seconds() / 3600.0,
                    )
                act.status = 'in_progress'
                self.wait.discard(act)
                self._ready.discard(act)
                self.ongoing.append(act)
                # Commit the substitution-resolved skill breakdown so _get_consumed_resources
                # tracks the *actual* skills in use (not just the declared primary skill).
                actual = getattr(act, '_actual_resources_for_start', None)
                if actual is not None:
                    act._actual_resources = actual
                # Commit dose for consumable resources when the activity starts.
                # Dose is irrevocable: once a worker enters the radiation field it
                # cannot be returned to the budget.
                if self.dose_trackers:
                    dose_rate = getattr(act, 'dose_rate_mrem_per_hour', 0.0)
                    if dose_rate > 0.0:
                        eff = self._effective_duration(act)
                        actual_res = getattr(act, '_actual_resources', None)
                        if actual_res is not None:
                            # Charge dose against the skills that are *actually* working
                            for skill, workers in actual_res.items():
                                tracker = self.dose_trackers.get(skill)
                                if tracker and workers > 0:
                                    tracker.consume(dose_rate, workers, eff)
                        else:
                            for req in act.getRequiredResources():
                                skill = req['skill_type']
                                tracker = self.dose_trackers.get(skill)
                                if tracker:
                                    tracker.consume(dose_rate, req['crew_count'], eff)
                # Consumable inventory is deducted in _apply_tentative so that
                # later candidates in the same time-step see the reduced pool.
                # No second deduction here.

    def _collect_candidates_from_heap(
        self,
        time: datetime,
        current_hours: float,
        value_assignment: str,
    ) -> Dict[Activity, Dict]:
        """Heap-based candidate collection for TF_based / external priority modes.

        Fix III: extracts the top-K activities from ``_ready_heap`` using lazy
        deletion (stale entries are discarded on pop) and early-exit once K
        candidates have been collected.  K is estimated from ``_univ_skill_min``
        with an 8× overbook factor to handle feasibility check failures.

        Complexity per call: O(K log n + stale_entries × log n).  For fan/tight
        topologies with static priorities this is O(K log n) ≈ O(1) vs the
        O(n) full scan — converting O(n²/k) scheduling to O(n log n).
        """
        # Estimate K: how many activities can start this step.
        _univ_min = self._univ_skill_min
        if _univ_min and self.crew_pool:
            try:
                max_slots = min(
                    int(self.crew_pool.get_availability(s, time) // d)
                    for s, d in _univ_min.items()
                    if d > 0
                )
            except Exception:
                max_slots = len(self._ready)
        else:
            max_slots = len(self._ready)

        k_needed = max(1, max_slots) * 8  # 8× overbook for feasibility headroom
        candidates: Dict[Activity, Dict] = {}
        pushed_back: list = []

        while self._ready_heap and len(candidates) < k_needed:
            neg_val, _, seq, act = self._ready_heap[0]

            # Lazy deletion: stale if act left _ready (started/violated) or if
            # a newer push superseded this entry (seq mismatch).
            if act not in self._ready or seq != self._heap_seq.get(act, 0):
                heapq.heappop(self._ready_heap)
                continue

            heapq.heappop(self._ready_heap)

            # Safety: pending-preds guard (should always be 0 here)
            if self._pending_preds.get(act, 0) != 0:
                pushed_back.append(act)
                continue

            # ES check
            abs_es = self.startTime + timedelta(hours=self.infoDict[act]['es'])
            if abs_es > time:
                # Not eligible yet — push back for next iteration
                pushed_back.append(act)
                continue

            # Time-window enforcement
            windows = self._resolve_windows(act)
            if windows:
                eff = self._effective_duration(act)
                placed = any_future = False
                for west_h, wlf_h in sorted(windows, key=lambda w: w[0]):
                    if current_hours + eff <= wlf_h:
                        if current_hours >= west_h:
                            placed = True; break
                        else:
                            any_future = True
                if not placed:
                    if not any_future:
                        self._window_violations.append({
                            'activity':      act.name,
                            'reason':        'window_missed',
                            'windows':       [{'earliest': w[0], 'latest': w[1]}
                                              for w in windows],
                            'current_hours': current_hours,
                            'duration_hours': eff,
                        })
                        self.wait.discard(act)
                        self._ready.discard(act)
                        logger.warning(
                            "Window missed: '%s' cannot fit in any of %d "
                            "window(s) at h=%.1f (duration=%.1f h). "
                            "Marking as violation.",
                            act.name, len(windows), current_hours, eff,
                        )
                    else:
                        pushed_back.append(act)
                    continue

            # Valid candidate — record first-seen time and add
            if act._candidate_since is None:
                act._candidate_since = time
            candidates[act] = self.infoDict[act]
            candidates[act]['value'] = -neg_val   # precomputed from _priority_cache

        # Restore pushed-back items (ES not reached yet or window future).
        # _heap_push increments seq, invalidating the entry we just popped.
        for act in pushed_back:
            if act in self._ready:
                self._heap_push(act)

        # Re-push valid candidates so non-selected ones stay available next step.
        # _heap_push increments seq, so only the new entry is valid — prior
        # entries (from earlier steps) are automatically treated as stale.
        # Selected activities are removed from self._ready by _update_activity_sets;
        # their re-pushed entries are lazily discarded on the next pop.
        for act in candidates:
            self._heap_push(act)

        return candidates

    def _select_candidate_activities(self, time: datetime, value_assignment: str, tie_breaker: str = 'mehh_8000_b') -> Dict[Activity, Dict]:
        """
        Select activities that can potentially start at given time.

        An activity is a candidate if:
        1. It's in the wait list
        2. All its predecessors are complete
        3. Its early start (ES) <= current time

        Args:
            time (datetime): Current time in schedule
            value_assignment (str): Method to assign priority values:
                - 'TF_based': Use slack-based weight function
                - 'external': Use externally provided priorities

        Returns:
            dict: {Activity: {'duration', 'es', 'ef', 'ls', 'lf', 'slack', 'value'}}
        """

        candidates: Dict[Activity, Dict] = {}

        # Shift-calendar gate: if the current event time falls in an off-shift
        # window, no new activity may start here.  The scheduler will advance to
        # the next shift-start event automatically because _build_event_queue()
        # seeds those boundaries into the heap.
        if not self._is_work_time(time):
            return candidates

        current_hours = (time - self.startTime).total_seconds() / 3600.0

        # Fix III: if priority cache is populated, use heap-based O(K log n) scan
        # instead of O(n) full scan.  Only valid for TF_based / external modes
        # where priorities are static within a run.
        if self._priority_cache and (
            value_assignment in ('TF_based', 'external')
        ):
            return self._collect_candidates_from_heap(
                time, current_hours, value_assignment
            )

        for act in sorted(
            self._ready,
            key=lambda a: a.returnName() if hasattr(a, "returnName") else str(a),
        ):
            # _ready guarantees all predecessors are complete (_pending_preds==0);
            # the counter check is O(1) and acts as a safety net for drift.
            if self._pending_preds.get(act, 0) != 0:
                continue

            # ES reached?
            abs_es = self.startTime + timedelta(hours=self.infoDict[act]['es'])
            if abs_es > time:
                continue

            # ── Time-window enforcement ──────────────────────────────────────
            # Works for both single-window (legacy fields) and multi-window
            # (time_windows list) activities via _resolve_windows().
            windows = self._resolve_windows(act)
            if windows:
                eff = self._effective_duration(act)
                # Iterate windows in earliest-first order.
                placed       = False   # True when activity fits inside an open window
                any_future   = False   # True when at least one window hasn't opened yet

                for (west_h, wlf_h) in sorted(windows, key=lambda w: w[0]):
                    if current_hours + eff <= wlf_h:    # window not yet missed
                        if current_hours >= west_h:     # window is open now
                            placed = True
                            break
                        else:
                            any_future = True           # window will open later

                if not placed:
                    if not any_future:
                        # All windows are past — record violation and drop activity.
                        self._window_violations.append({
                            'activity':      act.name,
                            'reason':        'window_missed',
                            'windows':       [{'earliest': w[0], 'latest': w[1]}
                                              for w in windows],
                            'current_hours': current_hours,
                            'duration_hours': eff,
                        })
                        self.wait.discard(act)
                        self._ready.discard(act)
                        logger.warning(
                            "Window missed: '%s' cannot fit in any of %d "
                            "window(s) at h=%.1f (duration=%.1f h). "
                            "Marking as violation.",
                            act.name, len(windows), current_hours, eff,
                        )
                    # Whether future window exists or all missed, skip for now.
                    continue

            # Fix I: lazy delay — record the first time this activity is a candidate
            if act._candidate_since is None:
                act._candidate_since = time
            # Fix IIa: use infoDict directly (no copy); 'value' is written back
            # into infoDict[act] below — safe for single-threaded scheduling.
            candidates[act] = self.infoDict[act]

        # Assign priority
        if value_assignment == 'TF_based':
            proj_dur = self.getProjectDuration()
            for act in candidates.keys():
                # Use the tighter of individual slack and WBS-group aggregate
                # slack so that when any member of a package is critical, all
                # members receive elevated priority simultaneously.
                eff_slack = min(
                    candidates[act]['slack'],
                    candidates[act].get('wbs_slack', candidates[act]['slack']),
                )
                candidates[act]['value'] = _weight_function(eff_slack, proj_dur)
        elif value_assignment == 'external':
            for act in candidates.keys():
                act_name = act.returnName()
                candidates[act]['value'] = self.priorities.get(act_name, 0.5)
        # use priority rules to compute candidate
        elif value_assignment.lower() in self._list_priority_names:
            acts = list(candidates.keys())
            priority = self.priority_calculation(acts, value_assignment, current_time=time, tie_breaker=tie_breaker)
            for (a, _, val) in priority:
                candidates[a]['value'] = val
        return candidates

    # -----------------------------
    # Selection helpers
    # -----------------------------
    def _effective_duration(self, activity) -> float:
        """Clamped effective runtime used for scheduling.

        For in-progress activities during replanning, returns the
        *remaining* duration (``activity._remaining_duration``) so that
        capacity checks do not over-book resources for the already-elapsed
        portion of the task.  For all other activities the original full
        duration is returned.
        """
        remaining = getattr(activity, '_remaining_duration', None)
        status = getattr(activity, 'status', 'pending')
        if status == 'in_progress' and remaining is not None:
            return max(0.0, remaining)
        return max(0.0, activity.duration)

    def _iter_hours(self, start: datetime, end: datetime):
        """Yield each hour (inclusive) between start and end."""
        t = start
        while t < end:   # strict <
            yield t
            t += timedelta(hours=1)


    def _build_capacity_snapshots(
        self,
        start_time: datetime,
        end_time: datetime,
        extra_boundaries=None,
    ):
        """Build capacity snapshots on an event-boundary grid.

        Instead of populating one entry per clock hour (O(D × K) where D is
        the window width in hours and K is the number of ongoing activities),
        this method builds a **sparse boundary grid** keyed at the times where
        capacity actually changes:

        * ``start_time`` and ``end_time`` (always present)
        * Start and end times of every ongoing activity that overlaps the window
        * Any additional boundaries supplied by the caller (e.g. the end times
          of all candidate activities for this scheduling step)

        Between consecutive grid points capacity is constant, so checking or
        decrementing at each grid point correctly represents the whole interval.
        Complexity: O((K + extra) × (S + E + L)) instead of
        O(D × K × (S + E + L)).

        Args:
            start_time:        Window start (always included in the grid).
            end_time:          Window end (always included in the grid).
            extra_boundaries:  Optional iterable of additional datetime boundary
                               points to add to the grid (e.g. candidate end
                               times so that ``_apply_tentative`` stops at the
                               right boundary for each candidate).

        Returns:
            Tuple ``(res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid)``
            where ``grid`` is a sorted list of datetime boundary points.
            Pass ``grid`` to :meth:`_fits_with_tentative` and
            :meth:`_apply_tentative` to activate the O(K) fast path.
        """
        # ── 1. Build the boundary grid ────────────────────────────────────────
        boundaries: set = {start_time, end_time}
        for act in self.ongoing:
            s, e = act.returnAbsTimes()
            if s is not None and start_time <= s < end_time:
                boundaries.add(s)
            if e is not None and start_time < e <= end_time:
                boundaries.add(e)
        if extra_boundaries is not None:
            for t in extra_boundaries:
                if start_time <= t <= end_time:
                    boundaries.add(t)
        grid = sorted(boundaries)

        res_rem      = defaultdict(dict)
        eq_rem       = defaultdict(dict)
        loc_tasks_rem   = defaultdict(dict)
        loc_workers_rem = defaultdict(dict)

        # ── 2. Seed base pool availability at every grid point ────────────────
        for skill in self.crew_pool.get_all_skills():
            for h in grid:
                res_rem[skill][h] = self.crew_pool.get_availability(skill, h)

        for eq_id in self.equipment_pool.get_all_equipment_ids():
            for h in grid:
                eq_rem[eq_id][h] = self.equipment_pool.get_availability(eq_id, h)

        for loc_id in self.location_pool.get_all_location_ids():
            for h in grid:
                cap = self.location_pool.get_capacity(loc_id, h)
                loc_tasks_rem[loc_id][h]   = cap['max_tasks']
                loc_workers_rem[loc_id][h] = cap.get('max_workers')  # None = unlimited

        # ── 3. Subtract each ongoing activity's consumption at grid points ────
        # Single pass over self.ongoing (O(K × |grid|)) instead of calling
        # _get_consumed_resources / _get_consumed_equipment per grid point
        # (O(|grid| × K)).  Net work is the same but avoids Python-function
        # call overhead and the repeated inner loop over self.ongoing.
        for act in self.ongoing:
            s, e = act.returnAbsTimes()
            if s is None or e is None:
                continue

            # Resource consumption — use the substitution-resolved breakdown
            # when available (same as _get_consumed_resources does).
            actual = getattr(act, '_actual_resources', None)
            res_consumption = actual if actual is not None else {
                req['skill_type']: req['crew_count']
                for req in act.getRequiredResources()
            }
            for skill, workers in res_consumption.items():
                if workers > 0 and skill in res_rem:
                    for h in grid:
                        if s <= h < e:
                            res_rem[skill][h] = max(0, res_rem[skill][h] - workers)

            # Equipment consumption
            for eq_req in act.getRequiredEquipment():
                eq_id, qty = eq_req['equipment_id'], eq_req['quantity_needed']
                if eq_id in eq_rem:
                    for h in grid:
                        if s <= h < e:
                            eq_rem[eq_id][h] = max(0, eq_rem[eq_id][h] - qty)

            # Location / zone consumption.
            # An activity occupying multiple zones consumes a slot in each zone
            # simultaneously (permit zone + physical room, for example).
            workers_at_loc = sum(
                req['crew_count'] for req in act.getRequiredResources()
            )
            for zone_id in act.getZoneIds():
                if zone_id not in loc_tasks_rem:
                    continue
                for h in grid:
                    if s <= h < e:
                        loc_tasks_rem[zone_id][h] = max(
                            0, loc_tasks_rem[zone_id][h] - 1
                        )
                        if loc_workers_rem[zone_id][h] is not None:
                            loc_workers_rem[zone_id][h] = max(
                                0, loc_workers_rem[zone_id][h] - workers_at_loc
                            )

        return res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid

    def _fits_with_tentative(
        self,
        activity,
        start_time,
        res_rem,
        eq_rem,
        loc_tasks_rem,
        loc_workers_rem,
        grid=None,
    ) -> bool:
        """Check feasibility against remaining capacity snapshots.

        Args:
            grid: Optional sorted list of boundary datetimes returned by
                  :meth:`_build_capacity_snapshots`.  When supplied, only
                  the boundary points within ``[start_time, end_time)`` are
                  checked — O(K) instead of O(D).  When ``None``, falls back
                  to the original hour-by-hour iteration (backward compatible).
        """
        eff = self._effective_duration(activity)
        end_time = start_time + timedelta(hours=eff)

        # Select the time points to check: boundary grid or every clock hour.
        check_points = (
            [h for h in grid if start_time <= h < end_time]
            if grid is not None
            else list(self._iter_hours(start_time, end_time))
        )

        # Resources (with substitution fallback)
        for h in check_points:
            for req in activity.getRequiredResources():
                skill, need = req['skill_type'], req['crew_count']
                available = res_rem[skill].get(h, 0)
                if available < need:
                    still_needed = need - available
                    for alt_skill in req.get('alternative_skill_types', []):
                        still_needed -= res_rem[alt_skill].get(h, 0)
                        if still_needed <= 0:
                            break
                    if still_needed > 0:
                        return False

        # Equipment — count check
        for h in check_points:
            for eq in activity.getRequiredEquipment():
                eq_id, need = eq['equipment_id'], eq['quantity_needed']
                if eq_rem[eq_id].get(h, 0) < need:
                    return False

        # Equipment — zone-affinity check (static, time-independent).
        # If a piece of equipment is assigned to a specific zone (zone_id is set),
        # only activities that declare that zone in their zone list may use it.
        # Two backward-compat guards:
        #   - eq_zone is None  → equipment is unconstrained, skip
        #   - act_zones empty  → activity has no zone declaration, skip
        act_zones = set(activity.getZoneIds())
        for eq in activity.getRequiredEquipment():
            eq_zone = self.equipment_pool.get_zone_id(eq['equipment_id'])
            if eq_zone is not None and act_zones and eq_zone not in act_zones:
                return False

        # Location / zone constraints.
        # All zones must have a free task slot (and worker slot if bounded).
        workers_needed = sum(req['crew_count'] for req in activity.getRequiredResources())
        for zone_id in activity.getZoneIds():
            if zone_id not in loc_tasks_rem:
                continue
            for h in check_points:
                # Must have a task slot in this zone
                if loc_tasks_rem[zone_id].get(h, 0) < 1:
                    return False
                # Must have worker slot if bounded
                lw = loc_workers_rem[zone_id].get(h, None)
                if lw is not None and lw < workers_needed:
                    return False

        # Consumable dose budget check.
        # For each resource requirement, resolve the actual skill breakdown
        # (accounting for substitution) so dose is checked against the skills
        # that will *actually* perform the work.
        if self.dose_trackers:
            dose_rate = getattr(activity, 'dose_rate_mrem_per_hour', 0.0)
            if dose_rate > 0.0:
                eff = self._effective_duration(activity)
                # Compute resolved worker counts (same logic as _apply_tentative)
                dose_check_workers: dict = {}  # {skill: workers}
                for req in activity.getRequiredResources():
                    skill, need = req['skill_type'], req['crew_count']
                    primary_use = min(res_rem[skill].get(start_time, 0), need)
                    dose_check_workers[skill] = dose_check_workers.get(skill, 0) + primary_use
                    still_needed = need - primary_use
                    for alt_skill in req.get('alternative_skill_types', []):
                        if still_needed <= 0:
                            break
                        alt_use = min(res_rem[alt_skill].get(start_time, 0), still_needed)
                        if alt_use > 0:
                            dose_check_workers[alt_skill] = dose_check_workers.get(alt_skill, 0) + alt_use
                            still_needed -= alt_use
                for skill, workers in dose_check_workers.items():
                    if workers > 0:
                        tracker = self.dose_trackers.get(skill)
                        if tracker and not tracker.fits(dose_rate, workers, eff):
                            return False

        # Consumable feasibility: check each item has sufficient remaining inventory.
        # Uses a point-in-time check at start_time (deduct-on-start contract).
        if self.consumable_pool:
            at_hour = (start_time - self.startTime).total_seconds() / 3600.0
            for req in activity.getRequiredConsumables():
                if not self.consumable_pool.fits(req['item_id'],
                                                  float(req['quantity_needed']),
                                                  at_hour=at_hour):
                    return False

        # System-state feasibility: check that no conflicting state is currently
        # held for any required plant system.  _apply_tentative calls acquire()
        # immediately after this check so subsequent candidates in the same
        # time-step see the tentative lock.
        if self.system_state_pool:
            for req in activity.getRequiredSystemStates():
                if not self.system_state_pool.fits(
                    req['system_id'], req['required_state']
                ):
                    return False

        return True

    def _apply_tentative(
        self,
        activity,
        start_time,
        res_rem,
        eq_rem,
        loc_tasks_rem,
        loc_workers_rem,
        grid=None,
    ):
        """Decrement remaining capacity snapshots by activity's consumption.

        Args:
            grid: Optional sorted list of boundary datetimes returned by
                  :meth:`_build_capacity_snapshots`.  When supplied, only
                  the boundary points within ``[start_time, end_time)`` are
                  decremented — O(K) instead of O(D).  When ``None``, falls
                  back to the original hour-by-hour iteration.

        Note: When ``grid`` is used the caller must ensure that the activity's
        end time (``start_time + duration``) was included in ``extra_boundaries``
        when building the snapshot.  This guarantees the decrement stops at the
        correct boundary and does not bleed into subsequent intervals.
        """
        eff = self._effective_duration(activity)
        end_time = start_time + timedelta(hours=eff)

        # Select the time points to update: boundary grid or every clock hour.
        hours = (
            [h for h in grid if start_time <= h < end_time]
            if grid is not None
            else list(self._iter_hours(start_time, end_time))
        )

        # Resources — compute worker assignment at the first time point
        # (assignments persist for the full activity duration), then apply
        # the same breakdown to every time point in the window.
        actual_consumption: dict = {}   # {skill_type: workers_used}
        if hours:
            h0 = hours[0]
            for req in activity.getRequiredResources():
                skill, need = req['skill_type'], req['crew_count']
                primary_use = min(res_rem[skill].get(h0, 0), need)
                actual_consumption[skill] = actual_consumption.get(skill, 0) + primary_use
                still_needed = need - primary_use
                for alt_skill in req.get('alternative_skill_types', []):
                    if still_needed <= 0:
                        break
                    alt_use = min(res_rem[alt_skill].get(h0, 0), still_needed)
                    if alt_use > 0:
                        actual_consumption[alt_skill] = actual_consumption.get(alt_skill, 0) + alt_use
                        still_needed -= alt_use

        # Store resolved breakdown for _update_activity_sets to commit
        activity._actual_resources_for_start = actual_consumption

        for h in hours:
            for skill, workers in actual_consumption.items():
                if workers > 0:
                    res_rem[skill][h] = max(0, res_rem[skill].get(h, 0) - workers)

        # Equipment
        for h in hours:
            for eq in activity.getRequiredEquipment():
                eq_id, need = eq['equipment_id'], eq['quantity_needed']
                eq_rem[eq_id][h] = max(0, eq_rem[eq_id].get(h, 0) - need)

        # Location / zone consume — deduct from every zone the activity occupies.
        workers_needed = sum(req['crew_count'] for req in activity.getRequiredResources())
        for zone_id in activity.getZoneIds():
            if zone_id not in loc_tasks_rem:
                continue
            for h in hours:
                # consume 1 task slot per zone
                loc_tasks_rem[zone_id][h] = max(0, loc_tasks_rem[zone_id][h] - 1)
                # consume worker slots if bounded
                if loc_workers_rem[zone_id].get(h) is not None:
                    loc_workers_rem[zone_id][h] = max(0, loc_workers_rem[zone_id][h] - workers_needed)

        # System-state acquire: lock each required plant system in the declared
        # state.  Doing this here (during the greedy selection loop) ensures that
        # later candidates at the same time-step see the tentative lock and are
        # correctly blocked if they require a conflicting state.
        if self.system_state_pool:
            for req in activity.getRequiredSystemStates():
                self.system_state_pool.acquire(
                    req['system_id'], req['required_state']
                )

        # Consumable deduction: deduct inventory here (not in _update_activity_sets)
        # so that later candidates in the same time-step see the reduced pool and
        # are correctly blocked if inventory is insufficient.  Matches the
        # system-state acquire pattern above.
        if self.consumable_pool:
            at_hour = (start_time - self.startTime).total_seconds() / 3600.0
            self.consumable_pool.apply_restocks_up_to(at_hour)
            for req in activity.getRequiredConsumables():
                self.consumable_pool.consume(req['item_id'],
                                             float(req['quantity_needed']))

    def _schedule_generation_scheme(self, candidates: Dict,
                                    time_index: datetime,
                                    choice: str) -> List:
        """
        Select which candidate activities to start based on strategy.

        Returns:
            list: List of Activity objects selected to start
        """
        if choice == 'first':
            # Select first candidate
            # Need to rank by value since Dict is not ordered
            ordered = self._rank_by_value(candidates)
            return [next(iter(ordered))]

        if choice in ('max_use_res_ranked', 'max_use_res_shuffled'):
            # Order candidates
            # Fix IIb: for ranked mode, estimate max startable activities from
            # _univ_skill_min and use heapq.nlargest(K) instead of a full sort.
            # This is O(n + K log K) instead of O(n log n) — major win for tight
            # pools where K << n.  A generous overbook factor (×8) ensures we
            # never miss a valid candidate due to the cap.
            if choice == 'max_use_res_ranked':
                ordered = self._rank_by_value_top_k(candidates)
            else:
                ordered = self._shuffle_candidates(candidates)

            # Build capacity snapshots across needed window.
            # Pre-compute candidate end times and pass them as extra boundaries
            # so the event-boundary grid is split at each candidate's finish —
            # this guarantees _apply_tentative stops at the right point and
            # doesn't bleed capacity reduction into later intervals.
            max_end = time_index
            cand_ends: set = set()
            for act in ordered:
                eff = self._effective_duration(act)
                cand_end = time_index + timedelta(hours=eff)
                cand_ends.add(cand_end)
                if cand_end > max_end:
                    max_end = cand_end

            res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid = \
                self._build_capacity_snapshots(time_index, max_end, extra_boundaries=cand_ends)

            # Use the universal-skill cache precomputed once per scheduling run
            # (self._univ_skill_min).  When any universal no-alternative skill's
            # remaining capacity drops below its minimum crew demand, every
            # remaining candidate will fail the resource check — break early.
            # O(1) per iteration; O(0) extra precomputation here.
            _univ_min = self._univ_skill_min

            selected = []
            for act in ordered:
                # Early-exit: if any universal skill is fully consumed at
                # time_index, no remaining candidate can start → stop scanning.
                if _univ_min and any(
                    res_rem.get(s, {}).get(time_index, 0) < d
                    for s, d in _univ_min.items()
                ):
                    break
                if self._fits_with_tentative(
                    act, time_index, res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid
                ):
                    selected.append(act)
                    self._apply_tentative(
                        act, time_index, res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid
                    )
            return selected

        elif choice == 'md_knapsack':
            # Multi-dimensional knapsack optimization (tentative),
            # then re-validate with per-hour capacity snapshots to avoid overbooking.
            optimizer = MDKnapsackScheduler(
                candidates,
                self.crew_pool,
                self.equipment_pool,
                self.location_pool,
                time_index,
                value_mode='value_based' if self.priorities else 'uniform'
            )
            tentative = optimizer.solve()

            max_end = time_index
            cand_ends_knap: set = set()
            for act in tentative:
                eff = self._effective_duration(act)
                cand_end = time_index + timedelta(hours=eff)
                cand_ends_knap.add(cand_end)
                if cand_end > max_end:
                    max_end = cand_end

            res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid = \
                self._build_capacity_snapshots(time_index, max_end, extra_boundaries=cand_ends_knap)

            selected = []
            for act in tentative:
                if self._fits_with_tentative(
                    act, time_index, res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid
                ):
                    selected.append(act)
                    self._apply_tentative(
                        act, time_index, res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid
                    )
            return selected

        elif choice == 'look_ahead':
            # LookAheadScheduler.select_activities() is fully tentative-aware:
            # it builds shared capacity snapshots, calls _fits_with_tentative,
            # and decrements via _apply_tentative for each selected activity.
            # No second validation pass is needed here.
            scheduler = LookAheadScheduler(self, look_ahead_hours=48)
            return scheduler.select_activities(candidates, time_index)

        else:
            raise ValueError(f"Unknown scheduling strategy: {choice}")

    def _can_schedule_activity(self, activity, start_time: datetime) -> bool:
        """
        Check if a single activity can be scheduled at start_time, considering
        only currently ongoing activities (no tentative selections).

        This is a stateless convenience wrapper around _fits_with_tentative.
        It builds a fresh capacity snapshot from self.ongoing for the activity's
        time window and delegates all constraint logic to _fits_with_tentative,
        ensuring there is a single source of truth for feasibility checking.

        Use this method when evaluating one activity in isolation:
            - LookAheadScheduler.select_activities()
            - debug_candidates_and_capacity()

        For evaluating multiple candidates within the same scheduling step
        (where tentative selections must reduce available capacity for subsequent
        checks), use _fits_with_tentative() directly with shared snapshot dicts
        managed by _schedule_generation_scheme(), as is already the case for the
        'max_use_res_ranked', 'max_use_res_shuffled', 'md_knapsack', and
        'look_ahead' strategies.

        Args:
            activity (Activity): The activity to check.
            start_time (datetime): Proposed start time.

        Returns:
            bool: True if the activity can feasibly start at start_time given
                currently ongoing activities.
        """
        end_time = start_time + timedelta(hours=self._effective_duration(activity))

        # Build a capacity snapshot for this activity's window from self.ongoing.
        # The activity's end_time is the grid's end boundary, so it is always
        # a split point — _fits_with_tentative checks the full window correctly.
        res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid = \
            self._build_capacity_snapshots(start_time, end_time)

        return self._fits_with_tentative(
            activity, start_time,
            res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid
        )

    def _get_consumed_resources(self, skill_type: str, time_point: datetime) -> int:
        """
        Calculate how many workers of a skill type are in use at time_point.
        Uses the substitution-resolved actual breakdown when available.
        """
        in_use = 0
        for act in self.ongoing:
            start_time, end_time = act.returnAbsTimes()
            if start_time and end_time and start_time <= time_point < end_time:
                actual = getattr(act, '_actual_resources', None)
                if actual is not None:
                    in_use += actual.get(skill_type, 0)
                else:
                    for res_req in act.getRequiredResources():
                        if res_req['skill_type'] == skill_type:
                            in_use += res_req['crew_count']
        return in_use


    def get_project_finish_actual(self) -> datetime:
        """
        Return the actual project finish time (latest actual end across activities).
        If no activities have actual times, return the schedule start time.
        """
        if not self.startTime:
            raise ValueError("Start time not set")

        ends: List[datetime] = []
        for a in self.forwardDict.keys():
            st, et = a.returnAbsTimes()
            if et is not None:
                ends.append(et)

        return max(ends) if ends else self.startTime

    def returnActualScheduleEndTime(self) -> datetime:
        """Convenience alias: return the actual schedule end time."""
        return self.get_project_finish_actual()

    # ------------------------------------------------------------------
    # GP TRAINING INTERFACE
    # ------------------------------------------------------------------

    def compute_fitness(
        self,
        alpha: float = 1.0,
        beta:  float = 0.5,
        gamma: float = 0.3,
        delta: float = 2.0,
    ) -> dict:
        """Compute a composite fitness score for use as a GP training signal.

        Must be called **after** :meth:`calculateScheduleWithResources` has
        completed for the current priority assignment.  Lower is better.

        Components
        ----------
        makespan_ratio : float
            ``scheduled_duration / cpm_duration`` — ideal value is 1.0 (no
            resource-induced stretch).  Increases as contention worsens.
        delay_ratio : float
            ``total_delay_hours / cpm_duration`` — sum of all activity wait
            times divided by the unconstrained project length.  Penalises
            schedules that leave resources idle while tasks queue.
        criticality_ratio : float
            Fraction of non-dummy activities with zero *actual* total float
            (i.e. on the resource-constrained critical chain).  A higher
            fraction means more activities are tight, which reduces
            robustness.
        window_violation_ratio : float
            ``n_window_violations / n_real_activities`` — fraction of real
            activities that missed their regulatory time window.  A schedule
            with any violations is operationally invalid; the high default
            weight (delta=2.0) ensures GP strongly penalises them.

        Composite score
        ---------------
        ``composite = alpha * makespan_ratio
                     + beta  * delay_ratio
                     + gamma * criticality_ratio
                     + delta * window_violation_ratio``

        Default weights (alpha=1, beta=0.5, gamma=0.3, delta=2.0) give
        makespan the dominant influence and treat window violations as a
        severe penalty — consistent with nuclear outage priorities where
        missing a Technical Specification window is a regulatory failure.

        Args:
            alpha: Weight for makespan component (default 1.0).
            beta:  Weight for delay component (default 0.5).
            gamma: Weight for criticality / robustness component (default 0.3).
            delta: Weight for window-violation component (default 2.0).

        Returns:
            dict with keys ``composite``, ``makespan_ratio``, ``delay_ratio``,
            ``criticality_ratio``, ``window_violation_ratio``,
            ``scheduled_duration``, ``cpm_duration``, ``delay_hours``,
            ``n_window_violations``.
            All float values; ``composite`` is the scalar to minimise.

        Raises:
            RuntimeError: If called before any schedule has been computed.
        """
        if not self._last_schedule_result:
            raise RuntimeError(
                "compute_fitness() called before calculateScheduleWithResources(). "
                "Run the scheduler first."
            )

        res = self._last_schedule_result
        cpm_dur   = max(res.get('cpm_duration', 1.0), 1.0)
        sched_dur = res.get('scheduled_duration', cpm_dur)
        delay_h   = res.get('delay_hours', 0.0)

        makespan_ratio    = sched_dur / cpm_dur
        delay_ratio       = delay_h   / cpm_dur

        # Criticality: fraction of real (non-dummy, non-buffer) activities with zero actual TF.
        dummy_names = {'START', 'END'}
        real_acts = [
            a for a in self.forwardDict
            if a.name.upper() not in dummy_names
            and getattr(a, 'buffer_type', None) is None
        ]
        n_real = max(len(real_acts), 1)
        zero_tf_set = getattr(self, 'actual_zero_tf_set', set())
        criticality_ratio = len(
            [a for a in real_acts if a in zero_tf_set]
        ) / n_real

        n_violations = len(res.get('window_violations', []))
        window_violation_ratio = n_violations / n_real

        composite = (
            alpha * makespan_ratio
            + beta  * delay_ratio
            + gamma * criticality_ratio
            + delta * window_violation_ratio
        )

        return {
            'composite':               composite,
            'makespan_ratio':          makespan_ratio,
            'delay_ratio':             delay_ratio,
            'criticality_ratio':       criticality_ratio,
            'window_violation_ratio':  window_violation_ratio,
            'n_window_violations':     n_violations,
            'scheduled_duration':      sched_dur,
            'cpm_duration':            cpm_dur,
            'delay_hours':             delay_h,
        }

    # =========================================================================
    # PROACTIVE ROBUSTNESS BUFFERING (CCPM)
    # =========================================================================

    @staticmethod
    def _size_buffer(durations: list, method: str, fraction: float) -> float:
        """Compute a CCPM buffer size from a list of activity durations.

        Args:
            durations: Activity durations in hours.  May be empty.
            method:    ``'half'`` — ``fraction × Σ(d)``; standard cut-and-paste.
                       ``'ssq'``  — ``√(Σ((d×fraction)²))``; statistically grounded
                       (half-normal approximation of duration uncertainty).
            fraction:  Scaling factor.  Typical value: 0.5.

        Returns:
            Buffer size in hours (≥ 0.0).  Zero when *durations* is empty.

        Raises:
            ValueError: If *method* is not ``'half'`` or ``'ssq'``.
        """
        if not durations:
            return 0.0
        if method == 'half':
            return fraction * sum(durations)
        elif method == 'ssq':
            return math.sqrt(sum((d * fraction) ** 2 for d in durations))
        else:
            raise ValueError(
                f"_size_buffer: unknown method '{method}'. Use 'half' or 'ssq'."
            )

    def _splice_buffer_activity(
        self,
        buffer_act,
        predecessors: list,
        successors: list,
    ) -> None:
        """Insert *buffer_act* between *predecessors* and *successors*.

        For every (pred, succ) pair the direct edge pred → succ is removed and
        replaced by pred → buffer_act → succ.  Predecessors that already point
        only to the buffer are unaffected; successors whose only incoming edge
        from the predecessor set is already via the buffer are also safe.

        After splicing, ``nxgraph``, ``infoDict``, and CPM values are all
        rebuilt via ``resetInfo()`` + ``generateInfo()``.
        """
        # Register buffer in forwardDict / backwardDict
        self.forwardDict[buffer_act]  = list(successors)
        self.backwardDict[buffer_act] = list(predecessors)

        for pred in predecessors:
            # Remove direct pred → succ edges that are now routed via buffer
            for succ in successors:
                if succ in self.forwardDict.get(pred, []):
                    self.forwardDict[pred].remove(succ)
                if pred in self.backwardDict.get(succ, []):
                    self.backwardDict[succ].remove(pred)
            # Wire pred → buffer
            if buffer_act not in self.forwardDict.get(pred, []):
                self.forwardDict[pred].append(buffer_act)

        for succ in successors:
            # Wire buffer → succ (already in forwardDict[buffer_act])
            if buffer_act not in self.backwardDict.get(succ, []):
                if succ not in self.backwardDict:
                    self.backwardDict[succ] = []
                self.backwardDict[succ].append(buffer_act)

        # Register in task lookup
        self.task_to_activity[buffer_act.name] = buffer_act

        # Seed infoDict so resetInfo() + generateInfo() see the new activity
        self.infoDict[buffer_act] = {
            "duration": buffer_act.duration,
            "es": 0, "ef": 0, "ls": 0, "lf": math.inf,
            "slack": 0, "wbs_slack": 0,
            "mts": 0, "mtp": 0,
            "grpw": 0, "grd": 0,
            "rr": 0, "avgrr": 0, "maxrr": 0, "minrr": 0,
        }

        # Rebuild graph topology and CPM
        self.nxgraph = nx.DiGraph(self.forwardDict)
        self.resetInfo()
        self.generateInfo()

    def insert_project_buffer(self, method: str = 'ssq', fraction: float = 0.5):
        """Insert a CCPM Project Buffer at the end of the resource-constrained
        critical chain.

        The project buffer absorbs disruptions anywhere on the critical chain
        so that individual task delays do not immediately extend the project
        finish.  It is sized from the chain activities' durations using the
        chosen *method* and spliced between the chain's terminal activity and
        its successors.

        Must be called **after** :meth:`calculateScheduleWithResources` has
        run (requires ``constrained_chain_list`` to be populated).

        Calling twice is idempotent — the existing buffer is returned without
        modification if a project buffer already exists in the graph.

        Args:
            method:   ``'half'`` (50 % of chain sum) or ``'ssq'`` (default;
                      sum-of-squares root — statistically grounded).
            fraction: Scaling factor applied during sizing (default 0.5).

        Returns:
            The :class:`Activity` representing the project buffer.

        Raises:
            RuntimeError: If called before any scheduling run.
        """
        if not getattr(self, 'constrained_chain_list', None):
            raise RuntimeError(
                "insert_project_buffer: call calculateScheduleWithResources() first "
                "to populate the resource-constrained critical chain."
            )

        # Idempotent: return existing buffer if already inserted
        existing = [a for a in self.forwardDict
                    if getattr(a, 'buffer_type', None) == 'project']
        if existing:
            return existing[0]

        # Size from real (non-buffer) chain activities
        chain_real = [a for a in self.constrained_chain_list
                      if getattr(a, 'buffer_type', None) is None]
        pb_size = self._size_buffer([a.duration for a in chain_real], method, fraction)

        # Build buffer activity
        pb = Activity(name='PB', duration=pb_size, description='Project Buffer')
        pb.buffer_type = 'project'

        # Splice: terminal → PB → original successors of terminal
        terminal  = self.constrained_chain_list[-1]
        successors = list(self.forwardDict.get(terminal, []))
        self._splice_buffer_activity(pb, predecessors=[terminal], successors=successors)

        logger.info(
            "insert_project_buffer: inserted PB (%.1f h, method=%s, fraction=%.2f) "
            "after '%s'.",
            pb_size, method, fraction, terminal.name,
        )
        return pb

    def insert_feeding_buffers(
        self, method: str = 'ssq', fraction: float = 0.5
    ) -> list:
        """Insert CCPM Feeding Buffers at every merge point where a non-critical
        feeding chain joins the resource-constrained critical chain.

        A feeding buffer intercepts delays from the feeding chain before they
        can propagate onto the critical chain.  One buffer is inserted per
        merge point; it collects all non-chain predecessors of the merge point
        as its own predecessors.

        Must be called **after** :meth:`calculateScheduleWithResources` (and
        optionally after :meth:`insert_project_buffer`).

        Args:
            method:   ``'half'`` or ``'ssq'`` (default).
            fraction: Scaling factor (default 0.5).

        Returns:
            List of :class:`Activity` objects representing the inserted feeding
            buffers (may be empty if the critical chain has no feeding inputs).

        Raises:
            RuntimeError: If called before any scheduling run.
        """
        if not getattr(self, 'constrained_chain_list', None):
            raise RuntimeError(
                "insert_feeding_buffers: call calculateScheduleWithResources() first."
            )

        chain_set = self.constrained_chain_set
        inserted  = []

        for merge_act in list(self.constrained_chain_list):
            # Non-chain, non-buffer predecessors feeding into this chain activity
            non_chain_preds = [
                p for p in self.backwardDict.get(merge_act, [])
                if p not in chain_set
                and getattr(p, 'buffer_type', None) is None
            ]
            if not non_chain_preds:
                continue

            # Guard: don't insert a second FB at the same merge point
            already = [a for a in self.forwardDict
                       if getattr(a, 'buffer_type', None) == 'feeding'
                       and merge_act in self.forwardDict.get(a, [])]
            if already:
                continue

            # Collect the full feeding subnetwork (BFS backward from non-chain preds)
            feeding_acts: set = set()
            frontier = list(non_chain_preds)
            while frontier:
                curr = frontier.pop()
                if curr in feeding_acts or curr in chain_set:
                    continue
                if getattr(curr, 'buffer_type', None) is not None:
                    continue
                feeding_acts.add(curr)
                for pred in self.backwardDict.get(curr, []):
                    frontier.append(pred)

            if not feeding_acts:
                continue

            fb_size = self._size_buffer(
                [a.duration for a in feeding_acts], method, fraction
            )
            if fb_size < 1e-6:
                continue

            fb_name = f'FB_{merge_act.name}'
            fb = Activity(
                name=fb_name,
                duration=fb_size,
                description=f'Feeding Buffer \u2192 {merge_act.name}',
            )
            fb.buffer_type = 'feeding'

            # Splice: non_chain_preds → FB → merge_act
            self._splice_buffer_activity(
                fb,
                predecessors=non_chain_preds,
                successors=[merge_act],
            )

            # Refresh chain_set after graph change (chain activities unchanged,
            # but chain_set must stay valid for subsequent merge-point checks)
            chain_set = self.constrained_chain_set

            inserted.append(fb)
            logger.info(
                "insert_feeding_buffers: inserted %s (%.1f h) before '%s'.",
                fb_name, fb_size, merge_act.name,
            )

        return inserted

    def get_buffer_status(self) -> dict:
        """Report the consumption status of all buffer activities in the schedule.

        Buffer *consumption* measures how much of the protective time was
        actually absorbed by upstream delays:

        ``consumed_hours = max(0, actual_start_hours − CPM_ES_hours)``

        If the critical chain (or feeding chain) ran exactly on plan,
        ``consumed_hours = 0``.  If delays pushed the chain's terminal
        activity past its CPM finish time, the buffer's actual start is
        later than planned and ``consumed_hours > 0``.

        Returns:
            dict keyed by buffer activity name, each value a dict with:
            ``buffer_type``, ``size_hours``, ``cpm_start_hours``,
            ``actual_start_hours`` (None if not yet scheduled),
            ``consumed_hours``, ``utilization_pct``.
            Empty dict if no buffer activities exist.
        """
        if not self.startTime:
            return {}

        status = {}
        for act in self.forwardDict:
            bt = getattr(act, 'buffer_type', None)
            if bt is None:
                continue

            cpm_es = self.infoDict.get(act, {}).get('es', 0.0)
            start_abs, _ = act.returnAbsTimes()

            if start_abs is not None:
                actual_start_h = (start_abs - self.startTime).total_seconds() / 3600.0
                consumed = max(0.0, actual_start_h - cpm_es)
            else:
                actual_start_h = None
                consumed       = 0.0

            util_pct = (consumed / act.duration * 100.0) if act.duration > 0 else 0.0

            status[act.name] = {
                'buffer_type':        bt,
                'size_hours':         act.duration,
                'cpm_start_hours':    cpm_es,
                'actual_start_hours': actual_start_h,
                'consumed_hours':     consumed,
                'utilization_pct':    min(100.0, util_pct),
            }

        return status

    def _compute_actual_tf_proxy(self, tol: float = 1e-6):
        """
        Compute actual (constraint-aware) total float per activity using the augmented graph:

            TF_actual(a) = s_min(a) - EF_actual(a)

        where:
        - s_min(a) = min( actual start of each successor of 'a' in the augmented DAG ),
                    or ProjectFinish_actual if 'a' has no successors.
        - EF_actual(a) is the actual end time of 'a'.

        Stores:
        self.actual_tf          : Dict[Activity, float or None]  # hours
        self.actual_zero_tf_set : Set[Activity]                  # |TF| <= tol
        """
        # Initialize
        self.actual_tf = {}
        self.actual_zero_tf_set = set()

        # If nothing has actual times yet, exit
        any_times = any(a.returnAbsTimes()[1] is not None for a in self.forwardDict.keys())
        if not any_times:
            return

        # Build augmented graph (precedence + binding arcs)
        augmented = self._build_augmented_graph()

        # For convenience, compute a map of successors (augmented)
        succs_map = {a: list(augmented.get(a, [])) for a in augmented.keys()}

        # Precompute project finish actual
        project_finish_actual = self.get_project_finish_actual()

        tf_actual = {}
        zero_tf = set()

        for a in augmented.keys():
            st_a, et_a = a.returnAbsTimes()
            if not st_a or not et_a:
                tf_actual[a] = None  # unscheduled or incomplete
                continue

            succs = succs_map.get(a, [])
            if succs:
                # min actual start among successors (if they have starts)
                succ_starts = [s.returnAbsTimes()[0] for s in succs if s.returnAbsTimes()[0] is not None]
                s_min = min(succ_starts) if succ_starts else project_finish_actual
            else:
                s_min = project_finish_actual

            # TF_actual in hours
            tf_val_hours = (s_min - et_a).total_seconds() / 3600.0
            tf_actual[a] = tf_val_hours

            if abs(tf_val_hours) <= tol:
                zero_tf.add(a)

        self.actual_tf = tf_actual
        self.actual_zero_tf_set = zero_tf

    def explain_idle_on_chain_detailed(self, tol: float = 1e-6):
        """
        Detailed idle-gap explanation along the resource-constrained chain.

        For each pair of consecutive activities on self.constrained_chain_list:
        1) Report the idle window [prev_end, act_start).
        2) Show precedence gating (ES not reached) and which predecessors were incomplete.
        3) For each hour in the idle window, report deficits for:
            - Resources (remaining < need)
            - Equipment (remaining < need)
            - Location slots (task or worker capacity)
            and list the scheduled activities (not only 'ongoing') consuming capacity.

        Notes:
        - Uses half-open interval semantics everywhere: [prev_end, act_start).
        - Skips pairs with missing times (unscheduled tasks).
        """

        # Must have computed the constrained chain
        if not hasattr(self, 'constrained_chain_list') or not self.constrained_chain_list:
            logger.debug("Run RCPSP first (calculateScheduleWithResources) to compute constrained chain.")
            return

        # Helper: iterate hours in a half-open interval [start, end)
        def _hour_iter(start: datetime, end: datetime):
            t = start
            while t < end:
                yield t
                t += timedelta(hours=1)

        # Pre-compute the set of all scheduled activities (those with non-None times)
        scheduled_acts = [
            a for a in self.forwardDict.keys()
            if a.returnAbsTimes()[0] is not None and a.returnAbsTimes()[1] is not None
        ]

        chain = self.constrained_chain_list

        for i, act in enumerate(chain):
            # Skip first node (often START) and unscheduled activities
            st, et = act.returnAbsTimes()
            if i == 0 or st is None or et is None:
                continue

            prev = chain[i - 1]
            prev_st, prev_et = prev.returnAbsTimes()
            if prev_et is None:
                # Cannot compute idle if predecessor lacks end time
                logger.debug(f"\n⤺ Skipping detailed idle analysis for {act.returnName()} "
                    f"(missing prev_end for {prev.returnName()}).")
                continue

            # Idle gap in hours
            idle_h = (st - prev_et).total_seconds() / 3600.0
            if idle_h <= tol:
                # No meaningful idle
                continue

            logger.debug("\n========================================")
            logger.debug(f"{act.returnName()} waited {idle_h:.1f}h after {prev.returnName()}")
            logger.debug(f"Idle window: [{prev_et.strftime('%Y-%m-%d %H:%M')} -> {st.strftime('%Y-%m-%d %H:%M')}]")

            # (1) Precedence gate (CPM ES vs prev_end)
            abs_es = self.startTime + timedelta(hours=self.infoDict[act]['es'])
            if abs_es > prev_et + timedelta(seconds=tol):
                gap_h = (abs_es - prev_et).total_seconds() / 3600.0
                logger.debug(f"• Precedence gate: ES not reached until {abs_es.strftime('%Y-%m-%d %H:%M')} (+{gap_h:.1f}h)")
                # List predecessors of 'act' that were not completed by prev_et
                blocking_preds = []
                for pred in self.backwardDict.get(act, []):
                    p_st, p_et = pred.returnAbsTimes()
                    # If predecessor didn't end by the idle start, it gates
                    if p_et is None or p_et > prev_et + timedelta(seconds=tol):
                        blocking_preds.append(pred.returnName())
                if blocking_preds:
                    logger.debug(f"  Other predecessor(s) not complete by idle start: {blocking_preds}")

            # Prepare 'act' demands
            skill_demands  = {req['skill_type']: req['crew_count'] for req in act.getRequiredResources()}
            eq_demands     = {req['equipment_id']: req['quantity_needed'] for req in act.getRequiredEquipment()}
            act_zone_ids   = act.getZoneIds()
            need_workers   = sum(skill_demands.values())

            found_blockers = False

            # (2) Diagnostics per hour in the idle window [prev_et, st)
            for h in _hour_iter(prev_et, st):
                hour_str = h.strftime('%Y-%m-%d %H:%M')

                # (2a) Resources: remaining = availability - consumption
                for skill, need in skill_demands.items():
                    avail = self.crew_pool.get_availability(skill, h)
                    consumed = 0
                    consumers = []
                    # Scan all scheduled activities overlapping hour h
                    for og in scheduled_acts:
                        og_st, og_et = og.returnAbsTimes()
                        if og_st <= h < og_et:
                            qty = sum(r['crew_count'] for r in og.getRequiredResources()
                                    if r['skill_type'] == skill)
                            if qty > 0:
                                consumed += qty
                                consumers.append((og.returnName(), qty))
                    remaining = avail - consumed
                    if remaining < need:
                        found_blockers = True
                        logger.debug(f"• {hour_str} | RESOURCE {skill}: need {need}, avail {avail}, "
                            f"consumed {consumed}, remaining {remaining} -> BLOCKED")
                        logger.debug(f"  Consumers at {hour_str}: {consumers or 'None (calendar blackout?)'}")

                # (2b) Equipment: remaining = availability - consumption
                for eq_id, need in eq_demands.items():
                    avail = self.equipment_pool.get_availability(eq_id, h)
                    consumed = 0
                    consumers = []
                    for og in scheduled_acts:
                        og_st, og_et = og.returnAbsTimes()
                        if og_st <= h < og_et:
                            qty = sum(e['quantity_needed'] for e in og.getRequiredEquipment()
                                    if e['equipment_id'] == eq_id)
                            if qty > 0:
                                consumed += qty
                                consumers.append((og.returnName(), qty))
                    remaining = avail - consumed
                    if remaining < need:
                        found_blockers = True
                        logger.debug(f"• {hour_str} | EQUIPMENT {eq_id}: need {need}, avail {avail}, "
                            f"consumed {consumed}, remaining {remaining} -> BLOCKED")
                        logger.debug(f"  Equipment consumers at {hour_str}: {consumers or 'None (calendar blackout?)'}")

                # (2c) Zone capacity: task slots and worker slots for every zone.
                for zone_id in act_zone_ids:
                    cap = self.location_pool.get_capacity(zone_id, h)
                    tasks_now = 0
                    workers_now = 0
                    loc_tasks = []
                    for og in scheduled_acts:
                        if zone_id in og.getZoneIds():
                            og_st, og_et = og.returnAbsTimes()
                            if og_st <= h < og_et:
                                tasks_now += 1
                                loc_tasks.append(og.returnName())
                                # total workers at the zone (all skills)
                                workers_now += sum(r['crew_count'] for r in og.getRequiredResources())

                    # Task slot deficit
                    if cap['max_tasks'] - tasks_now < 1:
                        found_blockers = True
                        logger.debug(f"• {hour_str} | ZONE {zone_id} tasks: "
                            f"max_tasks {cap['max_tasks']}, in_use {tasks_now} -> BLOCKED")
                        logger.debug(f"  Zone tasks at {hour_str}: {loc_tasks}")

                    # Worker slot deficit
                    if cap.get('max_workers') is not None and (cap['max_workers'] - workers_now) < need_workers:
                        found_blockers = True
                        logger.debug(f"• {hour_str} | ZONE {zone_id} workers: "
                            f"max_workers {cap['max_workers']}, in_use {workers_now}, "
                            f"need {need_workers} -> BLOCKED")

            if not found_blockers:
                logger.debug("• No capacity deficits detected in idle window.")
                logger.debug("  If ES gate above isn't the cause, this likely indicates calendar unavailability, off-hours,")
                logger.debug("  or non-modeled constraints (e.g., shift rules).")



    def explain_idle_on_chain(self):
        """
        For each activity on the constrained chain, explain why it waited.
        Lists overlapping activities that share resources during its idle period.
        """

        if not hasattr(self, 'constrained_chain_list'):
            logger.debug("Run RCPSP first.")
            return

        chain = self.constrained_chain_list
        for i, act in enumerate(chain):
            if i == 0:
                continue  # skip first
            st, et = act.returnAbsTimes()
            prev = chain[i-1]
            prev_st, prev_end = prev.returnAbsTimes()

            if st is None or prev_end is None:
                logger.debug(f"\n⤺ Skipping idle analysis between {prev.returnName()} and {act.returnName()} "
                    f"(missing times: prev_end={prev_end}, act_start={st}).")
                continue

            idle = (st - prev_end).total_seconds() / 3600.0
            if idle > 0.01:
                logger.debug(f"\n{act.returnName()} waited {idle:.1f}h after {prev.returnName()}")
                blockers = []
                for other in self.forwardDict.keys():
                    if other == act:
                        continue
                    o_st, o_et = other.returnAbsTimes()
                    if o_st and o_et and o_st < st and o_et > prev_end:
                        shared = (set(r['skill_type'] for r in act.getRequiredResources()) &
                                set(r['skill_type'] for r in other.getRequiredResources()))
                        if shared:
                            blockers.append((other.returnName(), sorted(shared)))


    def print_chain_sets_summary(self):
        """Diagnostic: print CPM CP, constrained chain, and zero-TF sets and their overlaps."""
        cpm_cp = set(self.getCriticalPathSymbolic())
        constrained_chain = [a.returnName() for a in getattr(self, 'constrained_chain_list', [])]
        constrained_set_names = set(constrained_chain)
        zero_tf_set_names = set(a.returnName() for a in getattr(self, 'actual_zero_tf_set', set()))

        only_cpm = sorted(list(cpm_cp - constrained_set_names))
        only_constrained = sorted(list(constrained_set_names - cpm_cp))
        both = sorted(list(cpm_cp & constrained_set_names))

        logger.debug("=== Chain Sets Summary ===")
        logger.debug(f"CPM Critical Path count: {len(cpm_cp)}")
        logger.debug(f"Constrained Chain count: {len(constrained_set_names)}")
        logger.debug(f"Zero-TF Actual count:    {len(zero_tf_set_names)}")
        logger.debug(f"Overlap (both):          {len(both)} -> {both[:10]}{' ...' if len(both) > 10 else ''}")
        logger.debug(f"CPM-only:                {len(only_cpm)} -> {only_cpm[:10]}{' ...' if len(only_cpm) > 10 else ''}")
        logger.debug(f"Constrained-only:        {len(only_constrained)} -> {only_constrained[:10]}{' ...' if len(only_constrained) > 10 else ''}")

    def _get_consumed_equipment(self, equipment_id: str, time_point: datetime) -> int:
        """
        Calculate how many units of equipment are in use at time_point.
        """
        in_use = 0
        for act in self.ongoing:
            start_time, end_time = act.returnAbsTimes()
            if start_time and end_time and start_time <= time_point < end_time:
                for eq_req in act.getRequiredEquipment():
                    if eq_req['equipment_id'] == equipment_id:
                        in_use += eq_req['quantity_needed']
        return in_use

    def _get_tasks_at_location(self, location_id: str, time_point: datetime) -> int:
        """
        Count how many tasks are ongoing at a zone/location at time_point.
        """
        count = 0
        for act in self.ongoing:
            if location_id in act.getZoneIds():
                start_time, end_time = act.returnAbsTimes()
                if start_time and end_time and start_time <= time_point < end_time:
                    count += 1
        return count

    def _get_workers_at_location(self, location_id: str, time_point: datetime) -> int:
        """
        Count how many workers are at a zone/location at time_point.
        """
        total_workers = 0
        for act in self.ongoing:
            if location_id in act.getZoneIds():
                start_time, end_time = act.returnAbsTimes()
                if start_time and end_time and start_time <= time_point < end_time:
                    for res_req in act.getRequiredResources():
                        total_workers += res_req['crew_count']
        return total_workers

    def _rank_by_value(self, candidates: Dict) -> List:
        """
        Rank candidates by priority value (descending).
        """
        sorted_items = sorted(
            candidates.items(),
            key=lambda item: item[1]['value'],
            reverse=True
        )
        return [act for act, info in sorted_items]

    def _rank_by_value_top_k(self, candidates: Dict) -> List:
        """
        Rank candidates by priority value, returning at most K × overbook items.

        Fix IIb: estimates the maximum number of startable activities this step
        from ``self._univ_skill_min`` (precomputed once per run).  Uses
        ``heapq.nlargest(K)`` which is O(n + K log K) rather than the O(n log n)
        full sort.  Falls back to full sort when no slot estimate is available or
        when there are few candidates.

        The overbook factor (8×) is intentionally generous so that activities
        that fail the feasibility check (_fits_with_tentative) do not cause the
        SGS to miss valid high-priority alternatives.
        """
        _OVERBOOK = 8
        n = len(candidates)

        # Estimate max slots from universal-skill minimum capacity
        max_slots: int = 0
        _univ_min = self._univ_skill_min
        if _univ_min and self.crew_pool:
            max_slots = min(
                int(self.crew_pool.get_availability(s, self.startTime) // d)
                for s, d in _univ_min.items()
                if d > 0
            )

        k = max_slots * _OVERBOOK if max_slots > 0 else n
        if k >= n:
            # No benefit from partial sort — full sort is simpler
            return self._rank_by_value(candidates)

        top_k = heapq.nlargest(k, candidates.items(), key=lambda x: x[1]['value'])
        return [act for act, _ in top_k]

    def _shuffle_candidates(self, candidates: Dict) -> List:
        """
        Randomly shuffle candidates.
        """
        activities = list(candidates.keys())
        random.shuffle(activities)
        return activities

    def _update_ongoing_list(self, time_index: datetime):
        """
        Update ongoing activities list.

        Move activities from ongoing to completed if their end time has been reached.
        """
        completed_now = []

        for act in self.ongoing:
            start_time, end_time = act.returnAbsTimes()
            if time_index >= end_time:
                completed_now.append(act)
                #logger.info(
                #    f"Completed: {act.name} at {time_index.strftime('%Y-%m-%d %H:%M')} "
                #    f"(duration: {max(0.0, act.duration):.1f}h, delay: {act.delay:.1f}h)"
                #)

        # Fix C: bulk-filter ongoing instead of O(|ongoing|) remove per activity
        if completed_now:
            completed_set_now = set(completed_now)
            self.ongoing = [a for a in self.ongoing if a not in completed_set_now]

        # Move to completed and release any system-state locks.
        # Fix D: use _pending_preds counter (O(1)) instead of all() predecessor scan.
        for act in completed_now:
            act.status = 'completed'
            self.completed.append(act)
            self._completed_set.add(act)
            # Decrement pending-predecessor counter for each successor; promote to
            # _ready when the counter reaches zero and the successor is still waiting.
            for succ in self.forwardDict.get(act, []):
                cnt = self._pending_preds.get(succ, 0) - 1
                self._pending_preds[succ] = cnt
                if cnt == 0 and succ in self.wait:
                    self._ready.add(succ)
                    # Fix III: push newly ready activity onto the priority heap
                    if self._priority_cache:
                        self._heap_push(succ)
            if self.system_state_pool:
                for req in act.getRequiredSystemStates():
                    self.system_state_pool.release(
                        req['system_id'], req['required_state']
                    )

    def get_schedule_dataframe(self):
        """
        Get schedule as pandas DataFrame.

        Returns:
            pandas.DataFrame: Schedule with columns:
                - activity_id
                - description
                - start_time
                - end_time
                - duration
                - delay
                - on_critical_path
        """
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("pandas is required for get_schedule_dataframe()")

        if not self.completed:
            raise ValueError("No schedule calculated yet. Run calculateScheduleWithResources() first.")

        data = []
        tf_actual_map = getattr(self, 'actual_tf', {})
        constrained_set = getattr(self, 'constrained_chain_set', set())

        for act in self.forwardDict.keys():
            st, et = act.returnAbsTimes()
            if st is None or et is None:
                continue
            data.append({
                'activity_id': act.returnName(),
                'description': act.returnDescription(),
                'start_time': st,
                'end_time': et,
                'duration': max(0.0, act.duration),
                'delay': act.delay,
                # Flags
                'on_resource_constrained_chain': (act in constrained_set),
                # NEW: TF_actual hours
                'tf_actual_hours': tf_actual_map.get(act, None),
            })

        df = pd.DataFrame(data).sort_values('start_time')
        return df


    def check_dependency_violations(self):
        """
        Check whether the computed schedule violates any job-precedence
        constraints defined in forwardDict, including finish-to-start lags.

        A violation occurs when a successor activity starts before its
        predecessor has finished plus any declared lag, i.e.:

            successor.start_time < predecessor.end_time + lag

        Returns
        -------
        violations : list[dict]
            One entry per violated edge, each with keys:
                - 'predecessor'    : activity_id of the predecessor
                - 'successor'      : activity_id of the successor
                - 'pred_end_time'  : scheduled end time of the predecessor
                - 'succ_start_time': scheduled start time of the successor
                - 'overlap_hours'  : how many hours early the successor started
                  relative to pred_end + lag
                - 'lag_hours'      : the declared finish-to-start lag (0.0 if none)
        is_feasible : bool
            True when no violations were found.
        """
        if not self.completed:
            raise ValueError(
                "No schedule calculated yet. "
                "Run calculateScheduleWithResources() or "
                "calculateSerialScheduleWithResources() first."
            )

        violations = []

        for pred, successors in self.forwardDict.items():
            pred_start, pred_end = pred.returnAbsTimes()
            if pred_end is None:
                continue

            for succ in successors:
                succ_start, succ_end = succ.returnAbsTimes()
                if succ_start is None:
                    continue

                lag_h = self.lag_dict.get((pred, succ), 0.0)
                required_start = pred_end + timedelta(hours=lag_h)
                if succ_start < required_start:
                    overlap = (required_start - succ_start).total_seconds() / 3600.0
                    violations.append({
                        'predecessor':    pred.returnName(),
                        'successor':      succ.returnName(),
                        'pred_end_time':  pred_end,
                        'succ_start_time': succ_start,
                        'overlap_hours':  overlap,
                        'lag_hours':      lag_h,
                    })

        is_feasible = len(violations) == 0
        return violations, is_feasible

    def export_schedule_to_csv(self, filename: str = 'schedule.csv'):
        """
        Export schedule to CSV file.
        """
        df = self.get_schedule_dataframe()
        df.to_csv(filename, index=False)
        logger.info(f"Schedule exported to {filename}")

    def validate_schedule(self):
        """Run post-schedule feasibility checks and return a ValidationResult.

        Interrogates the actual startTime / endTime values on every completed
        activity against every constraint type: completeness, duration
        consistency, precedence (with lags), time windows, hold points, crew
        capacity, equipment capacity, location concurrency, dose budgets, and
        system-state compatibility.  Also emits soft quality warnings for
        resource-wait delay, float consumption, and makespan stretch vs CPM.

        Should be called after ``calculateScheduleWithResources()`` or
        ``calculateScheduleWithResources_from()``.

        Returns:
            ValidationResult with .is_feasible, .violations, .warnings,
            and .summary() method.

        Example::

            result = pert.validate_schedule()
            if not result.is_feasible:
                print(result.summary())
        """
        from .schedule_validator import validate_schedule as _validate
        return _validate(self)

    def print_schedule_summary(self):
        """Print a summary of the calculated schedule."""
        if not self.completed:
            logger.debug("No schedule calculated yet.")
            return

        logger.debug("\n" + "=" * 70)
        logger.debug("SCHEDULE SUMMARY")
        logger.debug("=" * 70)

        # Overall statistics
        cpm_duration = self.getProjectDuration()
        actual_ends = [act.returnAbsTimes()[1] for act in self.forwardDict.keys() if act.returnAbsTimes()[1] is not None]
        if not actual_ends:
            logger.debug("No scheduled activities found — run calculateScheduleWithResources first.")
            return
        actual_end = max(actual_ends)
        actual_duration = (actual_end - self.startTime).total_seconds() / 3600
        total_delay = sum(act.delay for act in self.forwardDict.keys())

        logger.debug(f"\nProject Duration:")
        logger.debug(f"  CPM (no constraints): {cpm_duration:.1f} hours")
        logger.debug(f"  Actual (with constraints): {actual_duration:.1f} hours")
        logger.debug(f"  Total delay: {total_delay:.1f} hours")
        if actual_duration > 0:
            logger.debug(f"  Schedule efficiency: {(cpm_duration/actual_duration)*100:.1f}%")
        else:
            logger.debug("  Schedule efficiency: N/A")

        logger.debug(f"\nSchedule Timeline:")
        logger.debug(f"  Start: {self.startTime.strftime('%Y-%m-%d %H:%M')}")
        logger.debug(f"  End:   {actual_end.strftime('%Y-%m-%d %H:%M')}")

        # Activity statistics
        activities_with_delay = sum(1 for act in self.forwardDict.keys() if act.delay > 0)
        logger.debug(f"\nActivities:")
        logger.debug(f"  Total: {len(self.forwardDict)}")
        logger.debug(f"  Delayed: {activities_with_delay}")
        logger.debug(f"  On critical path: {len(self.getCriticalPath())}")

        # Top delayed activities
        delayed_acts = [
            (act.returnName(), act.delay, act.returnDescription())
            for act in self.forwardDict.keys()
            if act.delay > 0
        ]

        if delayed_acts:
            delayed_acts.sort(key=lambda x: x[1], reverse=True)
            logger.debug(f"\nTop 5 Most Delayed Activities:")
            for name, delay, desc in delayed_acts[:5]:
                logger.debug(f"  {name}: {delay:.1f}h - {desc}")

        logger.debug("=" * 70 + "\n")


    def _build_augmented_graph(self):
        """
        Build an augmented precedence graph (DAG):
        - Start from precedence edges (self.forwardDict).
        - Add 'resource-flow' arcs induced by capacity constraints:
            * Location ordering when max_tasks == 1 AND two tasks overlap.
            * Resource/equipment binding when overlapping activities share a skill/eq
                and their combined demand >= availability in the overlap.
        Only adds arcs from earlier-start to later-start to preserve DAG property.

        Returns:
        augmented: Dict[Activity, List[Activity]]  (adjacency list)
        """
        # Copy precedence edges
        augmented = {a: list(self.forwardDict[a]) for a in self.forwardDict.keys()}

        # Collect scheduled activities
        scheduled = []
        for a in self.forwardDict.keys():
            st, et = a.returnAbsTimes()
            if st and et:
                scheduled.append((a, st, et))
        # Sort by start time (then end) to define time order
        scheduled.sort(key=lambda t: (t[1], t[2]))

        def overlaps(st1, et1, st2, et2):
            # Half-open interval overlap: [st1, et1) with [st2, et2)
            return st1 < et2 and st2 < et1

        # --- Location / zone binding arcs ---
        if not self.location_pool:
            loc_ids = []
        else:
            loc_ids = self.location_pool.get_all_location_ids()
        for loc_id in loc_ids:
            acts_loc = [(a, st, et) for (a, st, et) in scheduled if loc_id in a.getZoneIds()]
            acts_loc.sort(key=lambda t: (t[1], t[2]))
            # Check consecutive overlapping pairs
            for i in range(len(acts_loc) - 1):
                a1, st1, et1 = acts_loc[i]
                a2, st2, et2 = acts_loc[i + 1]
                if overlaps(st1, et1, st2, et2):
                    # Evaluate location capacity around overlap start (representative hour)
                    cap = self.location_pool.get_capacity(loc_id, max(st1, st2))
                    if cap['max_tasks'] == 1:
                        # Enforced serial order -> arc earlier -> later
                        earlier, later = (a1, a2) if st1 <= st2 else (a2, a1)
                        if later not in augmented[earlier]:
                            augmented[earlier].append(later)

        # --- Resource/equipment binding arcs ---
        def demand_skill(act, skill):
            return sum(req['crew_count'] for req in act.getRequiredResources()
                    if req['skill_type'] == skill)

        def demand_eq(act, eq_id):
            return sum(req['quantity_needed'] for req in act.getRequiredEquipment()
                    if req['equipment_id'] == eq_id)

        # ── Precompute per-skill and per-equipment max single-activity demand ──
        # If 2 × max_demand < pool_availability for every skill and equipment,
        # no overlapping pair can ever be binding → skip the O(n²) pair scan.
        # This turns the fan+unconstrained case from O(n²) → O(n).
        all_skills = list(self.crew_pool.get_all_skills()) if self.crew_pool else []
        all_eq_ids = list(self.equipment_pool.get_all_equipment_ids()) if self.equipment_pool else []

        # Representative availability (use startTime; pools are rarely time-varying
        # in the coarse granularity that matters here).
        _t0 = self.startTime

        can_bind_res = False
        for skill in all_skills:
            avail = self.crew_pool.get_availability(skill, _t0)
            if avail <= 0:
                continue
            max_demand = max(
                (demand_skill(a, skill) for a, _, _ in scheduled),
                default=0,
            )
            if 2 * max_demand >= avail:
                can_bind_res = True
                break

        can_bind_eq = False
        for eq_id in all_eq_ids:
            avail = self.equipment_pool.get_availability(eq_id, _t0)
            if avail <= 0:
                continue
            max_demand = max(
                (demand_eq(a, eq_id) for a, _, _ in scheduled),
                default=0,
            )
            if 2 * max_demand >= avail:
                can_bind_eq = True
                break

        if can_bind_res or can_bind_eq:
            # ── O(n²) pair scan with sweep-line break ─────────────────────────
            # scheduled is sorted by (st, et); once st2 >= et1 no later j overlaps.
            for i in range(len(scheduled)):
                a1, st1, et1 = scheduled[i]
                for j in range(i + 1, len(scheduled)):
                    a2, st2, et2 = scheduled[j]
                    if st2 >= et1:          # sweep-line: no later j overlaps a1
                        break
                    if not overlaps(st1, et1, st2, et2):
                        continue

                    overlap_start = max(st1, st2)
                    binding = False

                    # Shared skills binding?
                    if can_bind_res:
                        skills1 = set(req['skill_type'] for req in a1.getRequiredResources())
                        skills2 = set(req['skill_type'] for req in a2.getRequiredResources())
                        for skill in skills1.intersection(skills2):
                            avail = self.crew_pool.get_availability(skill, overlap_start)
                            combined = demand_skill(a1, skill) + demand_skill(a2, skill)
                            if avail > 0 and combined >= avail:
                                binding = True
                                break

                    # Shared equipment binding?
                    if not binding and can_bind_eq:
                        eq1 = set(req['equipment_id'] for req in a1.getRequiredEquipment())
                        eq2 = set(req['equipment_id'] for req in a2.getRequiredEquipment())
                        for eq_id in eq1.intersection(eq2):
                            avail = self.equipment_pool.get_availability(eq_id, overlap_start)
                            combined = demand_eq(a1, eq_id) + demand_eq(a2, eq_id)
                            if avail > 0 and combined >= avail:
                                binding = True
                                break

                    if binding:
                        earlier, later = (a1, a2) if st1 <= st2 else (a2, a1)
                        if later not in augmented[earlier]:
                            augmented[earlier].append(later)

        return augmented


    def _longest_path_in_augmented(self, augmented):
        """
        Compute a 'critical chain' as the longest path over the augmented DAG
        using planned durations (not reduced by delay).

        Returns:
        path: List[Activity] from START to END (if present), else best available.
        """
        # Kahn topological sort — use deque for O(1) popleft instead of O(n) pop(0)
        from collections import deque
        indeg = {a: 0 for a in augmented.keys()}
        for u, succs in augmented.items():
            for v in succs:
                indeg[v] += 1
        queue = deque(a for a, d in indeg.items() if d == 0)
        topo = []
        while queue:
            u = queue.popleft()
            topo.append(u)
            for v in augmented[u]:
                indeg[v] -= 1
                if indeg[v] == 0:
                    queue.append(v)

        # Cycle guard: Kahn's algorithm processes every node exactly once in
        # a DAG.  If the augmented graph somehow contains a cycle (should not
        # happen — binding arcs are always directed earlier-start → later-start
        # — but defensive check for corrupt input), the sort terminates early
        # and the DP would run on a partial order, producing a silently wrong
        # critical chain.  Detect and log so it does not go unnoticed.
        if len(topo) != len(augmented):
            n_stuck = len(augmented) - len(topo)
            logger.warning(
                "_longest_path_in_augmented: cycle detected in augmented graph "
                "(%d/%d nodes processed). Critical chain may be incomplete.",
                len(topo), len(augmented),
            )
            # Add the stuck nodes in undefined order so the DP at least runs
            # over all nodes rather than silently dropping them.
            stuck = set(augmented.keys()) - set(topo)
            topo.extend(stuck)

        # DP longest path
        dist = {a: 0.0 for a in augmented.keys()}
        parent = {a: None for a in augmented.keys()}

        start = self.startActivity or (topo[0] if topo else None)
        if start is None:
            return []

        dist[start] = self._effective_duration(start)
        for u in topo:
            for v in augmented[u]:
                cand = dist[u] + self._effective_duration(v)
                if cand > dist[v]:
                    dist[v] = cand
                    parent[v] = u

        end = self.endActivity or (max(dist, key=dist.get) if dist else None)
        if end is None:
            return []

        # Reconstruct path from end -> start
        path = []
        cur = end
        seen = set()
        while cur is not None and cur not in seen:
            path.append(cur)
            seen.add(cur)
            cur = parent[cur]
        path.reverse()
        return path


    def _compute_resource_constrained_chain(self):
        """
        Build augmented graph and compute the resource-constrained critical chain.
        Stores:
        self.constrained_chain_list: List[Activity]
        self.constrained_chain_set: Set[Activity]
        """
        augmented = self._build_augmented_graph()
        chain = self._longest_path_in_augmented(augmented)
        self.constrained_chain_list = chain
        self.constrained_chain_set = set(chain)

    # ============================================================================
    # Schedule Debugging
    # To be used as follows:
    #     pert = Pert.from_json_file("example_10.json")
    #     pert.debug_connectivity_and_es()
    #     pert.debug_candidates_and_capacity(hours_ahead=48)

    # ============================================================================

    def debug_connectivity_and_es(self):
        logger.debug("=== Connectivity & ES debug ===")
        if not self.startActivity or not self.endActivity:
            logger.debug("Missing START or END in graph.")
            return

        # Successors of START
        succ_names = [s.returnName() for s in self.forwardDict.get(self.startActivity, [])]
        logger.debug(f"START successors: {succ_names}")

        # List first-level successors with their ES
        for s in self.forwardDict.get(self.startActivity, []):
            es = self.infoDict[s]["es"]
            ef = self.infoDict[s]["ef"]
            logger.debug(f"  {s.returnName()} ES={es:.1f}h, EF={ef:.1f}h")

        # Connectivity check: who is not reachable from START or cannot reach END
        not_from_start, not_to_end = [], []
        reachable = set()
        stack = [self.startActivity]
        while stack:
            u = stack.pop()
            if u in reachable: continue
            reachable.add(u)
            for v in self.forwardDict.get(u, []):
                stack.append(v)
        for a in self.forwardDict.keys():
            if a not in reachable:
                not_from_start.append(a.returnName())

        can_reach_end = set()
        stack = [self.endActivity]
        while stack:
            u = stack.pop()
            if u in can_reach_end: continue
            can_reach_end.add(u)
            for v in self.backwardDict.get(u, []):
                stack.append(v)
        for a in self.forwardDict.keys():
            if a not in can_reach_end:
                not_to_end.append(a.returnName())

        if not_from_start:
            logger.debug("Not reachable from START:", sorted(not_from_start))
        if not_to_end:
            logger.debug("Cannot reach END:", sorted(not_to_end))
        logger.debug("=== End connectivity & ES ===")


    def debug_candidates_and_capacity(self, hours_ahead=24):
        logger.debug("=== Candidates & capacity debug ===")
        t = self.startTime
        for k in range(hours_ahead + 1):
            time_index = t + timedelta(hours=k)
            # Candidates at this hour
            candidates = self._select_candidate_activities(
                time_index,
                'TF_based' if self.priorities is None else 'external'
            )
            cand_names = [a.returnName() for a in candidates.keys()]
            logger.debug(f"[{time_index.strftime('%Y-%m-%d %H:%M')}] candidates: {cand_names}")

            if cand_names:
                # Try feasibility check one by one
                for a in candidates.keys():
                    can = self._can_schedule_activity(a, time_index)
                    logger.debug(f"  - {a.returnName()} feasible? {can}")
            else:
                # If no candidates, show a likely gate for a few key tasks
                # (first successors of START)
                for s in self.forwardDict.get(self.startActivity, [])[:3]:
                    abs_es = self.startTime + timedelta(hours=self.infoDict[s]['es'])
                    logger.debug(f"  Note: {s.returnName()} abs ES is {abs_es.strftime('%Y-%m-%d %H:%M')}")
        logger.debug("=== End candidates & capacity ===")

# ============================================================================
# PROJECT PRIORITY CALCULATION
# ============================================================================

    def _compute_pairwise_E(self, act_i, act_j, t_n: datetime) -> datetime:
        """
        Compute E(i, j): the earliest feasible absolute start time of act_i
        given that act_j starts at t_n (Kolisch 1996, eq. 12-13).

        Cases
        -----
        - Schedulable (SP): i and j can run simultaneously at t_n without
          violating any resource/equipment/location constraint → E(i,j) = t_n.
        - Temporarily forbidden (TFP): conflict now but will resolve as other
          activities complete → scan forward hour-by-hour.
        - Generally forbidden (GFP): combined demand always exceeds some
          resource capacity → E(i,j) = t_n + d_j (i must wait for j to finish).
        """
        d_j = timedelta(hours=self._effective_duration(act_j))
        d_i = timedelta(hours=self._effective_duration(act_i))
        j_end = t_n + d_j

        # Build capacity snapshots covering the full window we may need.
        # Include one extra hour past j_end + d_i to ensure _fits_with_tentative
        # can always find valid data for any candidate start in the window.
        scan_end = j_end + d_i + timedelta(hours=1)
        # _build_capacity_snapshots now returns a 5-tuple.  This method scans
        # hour-by-hour (t_n, t_n+1h, …) so we keep the hour-by-hour fallback
        # (grid=None) for _apply_tentative and _fits_with_tentative to preserve
        # correctness of the sequential overlap check.
        res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, _grid = \
            self._build_capacity_snapshots(t_n, scan_end)

        # Commit j into the capacity snapshots (hour-by-hour path)
        self._apply_tentative(act_j, t_n, res_rem, eq_rem, loc_tasks_rem, loc_workers_rem)

        # Check simultaneous start (SP case)
        if self._fits_with_tentative(act_i, t_n, res_rem, eq_rem, loc_tasks_rem, loc_workers_rem):
            return t_n

        # Scan hour-by-hour up through j's completion (TFP case)
        t = t_n + timedelta(hours=1)
        while t < j_end:
            if self._fits_with_tentative(act_i, t, res_rem, eq_rem, loc_tasks_rem, loc_workers_rem):
                return t
            t += timedelta(hours=1)

        # GFP: i must start no earlier than when j finishes
        return j_end

    def priority_calculation(self, eligible, priority_rule='LF', current_time: datetime = None, tie_breaker: str = 'mehh_8000_b'):
        """Calculate activity priorities based on a named rule.

        Args:
            eligible (list): list of activities
            priority_rule (str, optional): priority rule. Defaults to 'LF'.
                lf: latest finish
                ls: latest start
                ef: early finish
                es: early start
                duration: activity duration, or shortest processing time
                random: random shuffle
                mts: most total successors
                mtp: most total predecessors
                rr: resource required
                avgrr: average resource requirement
                maxrr: maximum resource requirement
                minrr: minimum resource requirement
                grpw: greatest rank position weight
                grd: greatest resource demand
                wcs: worst case slack — LS_j - t; lower = higher urgency
                acs: average case slack — (ES_j + LS_j) / 2 - t; lower = higher urgency
                irsm: improved resource scheduling method — rr_j / (LS_j - t + 1); higher = higher urgency
            current_time (datetime, optional): current scheduling time; required for wcs/acs/irsm.
            tie_breaker (str, optional): infoDict key used to break ties in primary sort. Defaults to 'mehh_8000_b'.
                Pass None to disable tie-breaking.

        Raises:
            IOError: Invalid priority rule

        Returns:
            list: [(Activity, raw_value, normalized_value), ...]
        """
        rule = priority_rule.lower()
        tb_func = (lambda x: self.infoDict[x[0]][tie_breaker]) if tie_breaker is not None else None
        if rule in ['lf', 'ls', 'ef', 'es', 'duration'] + list(CUSTOM_PRIORITY_FUNCS.keys()):
            # There are tie values in the list which will cause the difference in priority orders
            data = [(a, self.infoDict[a][rule]) for a in eligible]
            priority = self.sort_with_tie_rule(data, key_func=lambda x: x[1], tie_breaker=tb_func)

        elif rule == 'random':
            random.shuffle(eligible)
            data = [(a, i) for i, a in enumerate(eligible)]
            priority = data
        elif rule in ['mts', 'mtp', 'grpw', 'grd', 'rr', 'avgrr', 'maxrr', 'minrr']:
            data = [(a, self.infoDict[a][rule]) for a in eligible]
            priority = self.sort_with_tie_rule(data, key_func=lambda x: x[1], tie_breaker=tb_func, reverse=True)

        elif rule == 'wcs':
            # Worst Case Slack (Kolisch 1996, eq. 19):
            #   v(j) = LS_j - max{ E(i,j) | i ∈ D_n, i ≠ j }
            # Minimum v(j) → most urgent → sort ascending.
            # When |D_n|=1 or no resource conflicts, reduces to classic MSLK (LS_j - t_n).
            t_n = current_time if current_time else self.startTime
            data = []
            for j in eligible:
                ls_j = self.infoDict[j]['ls']
                others = [i for i in eligible if i is not j]
                if others:
                    worst = max(
                        (self._compute_pairwise_E(j, i, t_n) - self.startTime).total_seconds() / 3600.0
                        for i in others
                    )
                else:
                    worst = (t_n - self.startTime).total_seconds() / 3600.0
                data.append((j, ls_j - worst))
            priority = self.sort_with_tie_rule(data, key_func=lambda x: x[1],
                                               tie_breaker=lambda x: self.infoDict[x[0]]['lf'])
        elif rule == 'acs':
            # Average Case Slack (Kolisch 1996, eq. 23):
            #   v(j) = LS_j - (1/|D_n|) * Σ_{i≠j} E(i,j)
            # Minimum v(j) → most urgent → sort ascending.
            t_n = current_time if current_time else self.startTime
            data = []
            for j in eligible:
                ls_j = self.infoDict[j]['ls']
                others = [i for i in eligible if i is not j]
                if others:
                    avg_displacement = sum(
                        (self._compute_pairwise_E(j, i, t_n) - self.startTime).total_seconds() / 3600.0
                        for i in others
                    ) / len(eligible)   # divide by |D_n| per the paper
                else:
                    avg_displacement = (t_n - self.startTime).total_seconds() / 3600.0
                data.append((j, ls_j - avg_displacement))
            priority = self.sort_with_tie_rule(data, key_func=lambda x: x[1],
                                               tie_breaker=lambda x: self.infoDict[x[0]]['lf'])
        elif rule == 'irsm':
            # Improved Resource Scheduling Method (Kolisch 1996, eq. 14):
            #   v(j) = max{ 0, E(j,i) - LS_i | i ∈ D_n, i ≠ j }
            # Minimum v(j) → least disruptive to others → sort ascending.
            t_n = current_time if current_time else self.startTime
            data = []
            for j in eligible:
                others = [i for i in eligible if i is not j]
                if others:
                    v = max(
                        max(0.0,
                            (self._compute_pairwise_E(i, j, t_n) - self.startTime).total_seconds() / 3600.0
                            - self.infoDict[i]['ls'])
                        for i in others
                    )
                else:
                    v = 0.0
                data.append((j, v))
            priority = self.sort_with_tie_rule(data, key_func=lambda x: x[1],
                                               tie_breaker=lambda x: self.infoDict[x[0]]['lf'])
        else:
            raise IOError("Invalid priority rule")
        # normalize
        if rule in ['lf', 'ls', 'ef', 'es', 'duration', 'mts', 'mtp', 'grpw', 'grd', 'random',
                    'wcs', 'acs', 'irsm'] + list(CUSTOM_PRIORITY_FUNCS.keys()):
            priority = normalize_tuples(priority)
        # update priority based on dependencies
        new_priority = self.reorder_by_dependencies(priority, self.forwardDict)
        new_priority = [(a, v, 1./(1.+i)) for i, (a, v) in enumerate(new_priority)]
        return new_priority


    def sort_with_tie_rule(self, data, key_func, tie_breaker=None, reverse=False):
        """
        Sort a list with a primary key and an optional tie-breaker rule.

        Args:
            data (list): Input list
            key_func (callable): Function to extract primary sort key
            tie_breaker (callable, optional): Function for tie-breaking
            reverse (bool): Whether to reverse the sort

        Returns:
            list: Sorted list
        """
        if tie_breaker is None:
            return sorted(data, key=key_func, reverse=reverse)

        return sorted(data, key=lambda x: (key_func(x), tie_breaker(x)), reverse=reverse)

    def reorder_by_dependencies(self, ordered_vars, dependency_dict):
        """
        Reorder (key, value) tuples based on dependency constraints on keys,
        preserving order as much as possible (based on value order).

        Parameters
        ----------
        ordered_vars : list[tuple[str, float]]
            List of (key, value), sorted by value.
        dependency_dict : dict[str, list[str]]
            {predecessor_key: [successor_keys]}

        Returns
        -------
        list[tuple[str, float]]
            Reordered list of tuples.
        """
        # Extract keys and mapping
        keys = [k for k, _ in ordered_vars]
        allowed = set(keys)

        # Map key -> tuple
        key_to_tuple = {k: (k, v) for k, v in ordered_vars}

        # Use index as stable ordering (since already sorted by value)
        pos = {k: i for i, k in enumerate(keys)}

        G = nx.DiGraph()
        G.add_nodes_from(keys)

        # Add only valid dependencies
        for pred, succs in dependency_dict.items():
            if pred not in allowed:
                continue
            for succ in succs:
                if succ not in allowed:
                    continue
                G.add_edge(pred, succ)

        try:
            sorted_keys = list(
                nx.lexicographical_topological_sort(G, key=lambda x: pos[x])
            )
        except nx.NetworkXUnfeasible:
            raise ValueError("Dependency graph contains a cycle; no valid ordering exists.")

        # Map back to tuples
        return [key_to_tuple[k] for k in sorted_keys]

    @staticmethod
    def activity_name(activity):
        """Return the stable activity name used in schedule logs."""
        if hasattr(activity, "returnName"):
            return activity.returnName()
        return getattr(activity, "name", str(activity))

    def priority_activity_order(
        self,
        priority_rule: str = 'lf',
        activities: Optional[List['Activity']] = None,
        current_time: datetime = None,
        tie_breaker: str = 'mehh_8000_b',
    ) -> List['Activity']:
        """
        Return a precedence-feasible activity order for a named priority rule.

        This normalizes the tuple format returned by ``priority_calculation`` so
        GA/GANS callers do not need to duplicate the extraction logic.
        """
        eligible = list(activities) if activities is not None else list(self.forwardDict.keys())
        raw = self.priority_calculation(
            eligible,
            priority_rule=priority_rule,
            current_time=current_time,
            tie_breaker=tie_breaker,
        )
        if raw and isinstance(raw[0], tuple):
            return [item[0] for item in raw]
        return list(raw)

    def repair_activity_order(self, activities: List['Activity']) -> List['Activity']:
        """
        Repair precedence feasibility for an activity order.

        The relative order is preserved as much as possible using the same
        dependency-aware topological sort used by priority rules.
        """
        ranked = [(activity, pos) for pos, activity in enumerate(activities)]
        repaired = self.reorder_by_dependencies(ranked, self.forwardDict)
        return [activity for activity, _ in repaired]

    def current_schedule_activity_order(
        self,
        fallback_order: Optional[List['Activity']] = None,
        activities: Optional[List['Activity']] = None,
        repair: bool = True,
    ) -> List['Activity']:
        """
        Convert the current schedule left on this Pert object into an activity order.

        Parallel SGS records chronological decision order in
        ``schedule_log[*]['selected']``.  Serial SGS records one ``activity`` per
        log row.  If the log is incomplete, remaining scheduled activities are
        appended by actual start time and unscheduled activities by the fallback
        order.  The result is repaired for precedence feasibility by default.
        """
        activity_list = list(activities) if activities is not None else list(self.forwardDict.keys())
        activity_set = set(activity_list)
        fallback_pos = (
            {activity: pos for pos, activity in enumerate(fallback_order)}
            if fallback_order is not None
            else {}
        )
        default_pos = lambda activity: fallback_pos.get(activity, len(fallback_pos))
        name_to_activity = {
            self.activity_name(activity): activity
            for activity in activity_list
        }

        ordered: List['Activity'] = []
        seen = set()

        def _append_logged(value) -> None:
            activity = value if value in activity_set else name_to_activity.get(str(value))
            if activity is not None and activity not in seen:
                ordered.append(activity)
                seen.add(activity)

        for step in getattr(self, "schedule_log", []) or []:
            selected = step.get("selected")
            if selected:
                for value in selected:
                    _append_logged(value)
            elif "activity" in step:
                _append_logged(step["activity"])

        scheduled_remainder = []
        unscheduled_remainder = []
        for activity in activity_list:
            if activity in seen:
                continue
            if hasattr(activity, "returnAbsTimes"):
                start_time, end_time = activity.returnAbsTimes()
            else:
                start_time = getattr(activity, "startTime", None)
                end_time = getattr(activity, "endTime", None)
            if start_time is None:
                unscheduled_remainder.append(activity)
            else:
                scheduled_remainder.append(
                    (
                        start_time,
                        end_time or start_time,
                        default_pos(activity),
                        activity,
                    )
                )

        scheduled_remainder.sort(key=lambda item: (item[0], item[1], item[2]))
        ordered.extend(activity for _, _, _, activity in scheduled_remainder)
        ordered.extend(sorted(unscheduled_remainder, key=default_pos))

        return self.repair_activity_order(ordered) if repair else ordered



    # =========================================================================
    # SERIAL SGS
    # =========================================================================

    def _serial_check_feasibility(
        self,
        activity:          'Activity',
        start_time:        datetime,
        schedule_profile:  List[tuple],
    ) -> bool:
        """
        Point-in-time feasibility check for Serial SGS.

        Unlike _fits_with_tentative (which uses pre-built snapshot dicts),
        this method computes consumed capacity on-the-fly from the
        schedule_profile — the list of activities already committed in the
        current serial scheduling pass.  It is intentionally lightweight:
        no dicts are built or mutated, making it cheap to call repeatedly
        during the forward scan in _find_earliest_feasible_start_serial.

        Args:
            activity:         Candidate activity to evaluate.
            start_time:       Proposed absolute start time.
            schedule_profile: List of (act, abs_start, abs_end) tuples for
                              every activity already scheduled this pass.

        Returns:
            True if the activity can start at start_time without violating
            any resource, equipment, or location constraint.
        """
        duration = self._effective_duration(activity)
        end_time = start_time + timedelta(hours=duration)

        # Pre-compute which scheduled activities overlap [start_time, end_time)
        # to avoid re-scanning the full profile at every hour.
        overlapping = [
            (a, s, e) for (a, s, e) in schedule_profile
            if s < end_time and e > start_time
        ]

        for h in self._iter_hours(start_time, end_time):

            # ── Resources ────────────────────────────────────────────────────
            for req in activity.getRequiredResources():
                skill, need = req['skill_type'], req['crew_count']
                avail = self.crew_pool.get_availability(skill, h)
                consumed = sum(
                    r['crew_count']
                    for (a, s, e) in overlapping
                    if s <= h < e
                    for r in a.getRequiredResources()
                    if r['skill_type'] == skill
                )
                if avail - consumed < need:
                    return False

            # ── Equipment ────────────────────────────────────────────────────
            for req in activity.getRequiredEquipment():
                eq_id, need = req['equipment_id'], req['quantity_needed']
                avail = self.equipment_pool.get_availability(eq_id, h)
                consumed = sum(
                    e_req['quantity_needed']
                    for (a, s, e) in overlapping
                    if s <= h < e
                    for e_req in a.getRequiredEquipment()
                    if e_req['equipment_id'] == eq_id
                )
                if avail - consumed < need:
                    return False

            # ── Location / zone ──────────────────────────────────────────────
            # All zones must have a free task slot (and worker slot if bounded).
            for zone_id in activity.getZoneIds():
                cap = self.location_pool.get_capacity(zone_id, h)

                # Task slots
                tasks_in_use = sum(
                    1 for (a, s, e) in overlapping
                    if s <= h < e and zone_id in a.getZoneIds()
                )
                if cap['max_tasks'] - tasks_in_use < 1:
                    return False

                # Worker slots (optional)
                if cap.get('max_workers') is not None:
                    workers_needed = sum(
                        r['crew_count'] for r in activity.getRequiredResources()
                    )
                    workers_in_use = sum(
                        r['crew_count']
                        for (a, s, e) in overlapping
                        if s <= h < e and zone_id in a.getZoneIds()
                        for r in a.getRequiredResources()
                    )
                    if cap['max_workers'] - workers_in_use < workers_needed:
                        return False

        return True

    def _find_earliest_feasible_start_serial(
        self,
        activity:          'Activity',
        min_start:         datetime,
        schedule_profile:  List[tuple],
    ) -> datetime:
        """
        Event-driven forward scan to find the earliest feasible start time
        for an activity in the Serial SGS, at or after min_start.

        Candidate times are drawn from three event categories:
          1. min_start itself (precedence-derived lower bound).
          2. Pre-computed availability boundaries (pool period edges) that
             lie at or after min_start — captures moments where a previously
             unavailable resource/equipment/location becomes available.
          3. Completion times of already-scheduled activities that lie at or
             after min_start — captures moments where a competing activity
             finishes and releases capacity.

        The scan tries each candidate time in ascending order and returns
        the first one that passes _serial_check_feasibility.  Because every
        meaningful capacity-change event is represented in the candidate set,
        no feasible window can be missed.

        Args:
            activity:         Activity to schedule.
            min_start:        Earliest permissible start (from precedence).
            schedule_profile: Already-committed (act, start, end) tuples.

        Returns:
            datetime: Earliest feasible absolute start time >= min_start.
        """
        # Build the candidate event set
        candidates: set = {min_start}

        # Availability boundary events at or after min_start
        for dt in self._availability_events:
            if dt >= min_start:
                candidates.add(dt)

        # Completion times of scheduled activities at or after min_start
        for (_, _, end_t) in schedule_profile:
            if end_t >= min_start:
                candidates.add(end_t)

        # Scan in ascending order
        for t in sorted(candidates):
            if self._serial_check_feasibility(activity, t, schedule_profile):
                return t

        # Fallback: should not be reached for a feasible problem, but return
        # the last candidate to avoid an infinite loop in degenerate cases.
        logger.warning(
            "_find_earliest_feasible_start_serial: no feasible slot found "
            "for %s from %s — returning last candidate.",
            activity.name,
            min_start.strftime('%Y-%m-%d %H:%M')
        )
        return max(candidates)

    def calculateSerialScheduleWithResources(
        self,
        priority_rule: str = 'lf',
        max_time_hours: float = None,
        _ordered: List['Activity'] = None,
        tie_breaker: str = 'mehh_8000_b',
    ) -> dict:
        """
        Schedule activities using a Serial Schedule Generation Scheme (Serial SGS).

        In Serial SGS each activity is scheduled exactly once, in the order
        defined by the priority rule.  For each activity the scheduler:
          1. Computes the precedence-based earliest start (ES) from the actual
             finish times of all already-scheduled predecessors.
          2. Performs an event-driven forward scan from that ES to find the
             earliest time at which resource, equipment, and location constraints
             are simultaneously satisfied for the full activity duration.
          3. Commits the activity to that start time and adds it to the
             schedule profile so subsequent activities see its resource usage.

        Unlike the Parallel SGS (calculateScheduleWithResources), this method:
          - Processes exactly N activities in N decision points (one per
            activity in the priority list).
          - Never advances a global time clock; each activity finds its own
            earliest feasible window independently.
          - Uses _serial_check_feasibility (lightweight, profile-based) rather
            than the snapshot-dict machinery used by the parallel scheduler.

        Works in both standalone and RAVEN modes.  Compatible with the same
        post-schedule analytics (Gantt, constrained chain, TF, etc.).

        Args:
            priority_rule (str): Any rule accepted by priority_calculation().
                Common choices:
                  'lf'       — Latest Finish (default, most common in literature)
                  'ls'       — Latest Start
                  'mts'      — Most Total Successors
                  'grpw'     — Greatest Rank Position Weight
                  'grd'      — Greatest Resource Demand
                  'random'   — Random shuffle
                  (see priority_calculation docstring for full list)
            max_time_hours (float, optional): Safety cutoff in hours from
                startTime.  Defaults to 3× the CPM duration.  Any activity
                whose computed start exceeds this limit is left unscheduled
                and triggers a warning.
            tie_breaker (str, optional): infoDict key used to break ties in
                priority sorting.  Defaults to 'mehh_8000_b'.  Pass None to
                disable tie-breaking.

        Returns:
            dict: {
                'scheduled_duration': float,  # hours, startTime → last end
                'cpm_duration':        float,  # unconstrained CPM duration
                'delay_hours':         float,  # total accumulated wait time
                'n_activities':        int,
                'n_completed':         int,    # activities successfully placed
                'priority_rule':       str,
            }
        """
        if not self.crew_pool or not self.equipment_pool or not self.location_pool:
            raise ValueError(
                "Resource, equipment, and location pools must be initialized"
            )
        if not self.startTime:
            raise ValueError("startTime must be set before scheduling")

        # ── Clean slate ───────────────────────────────────────────────────────
        self._reset_scheduling_state()

        cpm_duration = self.getProjectDuration()
        n_activities = len(self.infoDict)
        if max_time_hours is None:
            max_time_hours = cpm_duration * self._max_time_factor
        max_time = self.startTime + timedelta(hours=max_time_hours)

        # ── Build priority-ordered list ───────────────────────────────────────
        # If a pre-computed ordering is supplied (e.g. from the GA), use it
        # directly.  Otherwise derive it from the named priority rule.
        if _ordered is not None:
            ordered: List['Activity'] = list(_ordered)
            priority_rule = 'custom'
        else:
            # priority_calculation returns either List[Activity] (random) or
            # List[(Activity, value)] for all other rules.  Normalise to List[Activity].
            all_acts = list(self.forwardDict.keys())
            raw_priority = self.priority_calculation(all_acts, priority_rule, tie_breaker=tie_breaker)

            if raw_priority and isinstance(raw_priority[0], tuple):
                ordered: List['Activity'] = [a for (a, _, _) in raw_priority]
            else:
                ordered: List['Activity'] = list(raw_priority)

        logger.info(
            "Starting Serial SGS | activities=%d | CPM=%.1fh | rule=%s",
            n_activities, cpm_duration, priority_rule
        )

        # ── Schedule profile: committed (activity, abs_start, abs_end) ────────
        schedule_profile: List[tuple] = []

        # Map activity → actual end time for fast precedence lookup
        actual_end: Dict['Activity', datetime] = {}

        n_scheduled = 0

        for act in ordered:
            # ── Step 1: precedence-based earliest start ───────────────────────
            preds = self.backwardDict.get(act, [])
            if preds:
                pred_end = max(
                    actual_end.get(p, self.startTime) for p in preds
                )
            else:
                pred_end = self.startTime

            # Also respect CPM early start (converts hours offset to absolute)
            cpm_es_abs = self.startTime + timedelta(hours=self.infoDict[act]['es'])
            min_start = max(pred_end, cpm_es_abs)

            # ── Step 2: event-driven feasibility scan ─────────────────────────
            if min_start > max_time:
                logger.warning(
                    "Serial SGS: activity %s min_start %s exceeds cutoff — skipped.",
                    act.name, min_start.strftime('%Y-%m-%d %H:%M')
                )
                continue

            feasible_start = self._find_earliest_feasible_start_serial(
                act, min_start, schedule_profile
            )

            if feasible_start > max_time:
                logger.warning(
                    "Serial SGS: activity %s feasible start %s exceeds cutoff — skipped.",
                    act.name, feasible_start.strftime('%Y-%m-%d %H:%M')
                )
                continue

            # ── Step 3: commit ────────────────────────────────────────────────
            act.setActualStartTime(feasible_start)
            abs_end = feasible_start + timedelta(hours=self._effective_duration(act))
            actual_end[act] = abs_end

            schedule_profile.append((act, feasible_start, abs_end))

            # Delay = gap between precedence-driven min_start and actual start
            wait_hours = (feasible_start - min_start).total_seconds() / 3600.0
            if wait_hours > 0:
                act.addDelay(wait_hours)

            # Move through queues so post-schedule analytics work correctly
            if act in self.wait:
                self.wait.discard(act)
                self._ready.discard(act)
            self.completed.append(act)
            self._completed_set.add(act)

            self.schedule_log.append({
                'activity':   act.name,
                'start':      feasible_start,
                'end':        abs_end,
                'min_start':  min_start,
                'delay_h':    wait_hours,
            })

            n_scheduled += 1
            logger.debug(
                "Serial SGS: scheduled %s | start=%s | end=%s | delay=%.1fh",
                act.name,
                feasible_start.strftime('%Y-%m-%d %H:%M'),
                abs_end.strftime('%Y-%m-%d %H:%M'),
                wait_hours,
            )

        # ── Post-schedule analytics ───────────────────────────────────────────
        self._compute_actual_tf_proxy()
        self._compute_resource_constrained_chain()

        actual_project_end = self.get_project_finish_actual()
        actual_duration    = (actual_project_end - self.startTime).total_seconds() / 3600.0
        total_delay        = sum(act.delay for act in self.forwardDict)

        results = {
            'scheduled_duration': actual_duration,
            'cpm_duration':       cpm_duration,
            'delay_hours':        total_delay,
            'n_activities':       n_activities,
            'n_completed':        n_scheduled,
            'priority_rule':      priority_rule,
        }

        logger.info(
            "Serial SGS complete | CPM=%.1fh | actual=%.1fh | "
            "delay=%.1fh | scheduled=%d/%d | rule=%s",
            cpm_duration, actual_duration,
            total_delay, n_scheduled, n_activities, priority_rule
        )
        return results

# ============================================================================
# PROJECT SCHEDULE VISUALIZATION
# ============================================================================

    def plot_activity_dag(
        self,
        filename: str = "dag.html",
        library: str = "pyvis",           # 'pyvis' or 'plotly'
        highlight: str = "both",          # 'cpm', 'constrained', 'both', 'none'
        show_unscheduled: bool = True,
        show_hold_points: bool = True,
        physics: bool = False,            # pyvis physics on/off
        layer_by: str = "es",             # 'es', 'ls', or 'topo'
        include_augmented_edges: bool = False,  # add resource-flow arcs (dashed red)
        max_nodes: int = 0,                # 0 = no limit; >0 to cap nodes in very large graphs
        show_edge_arrows: bool = True  # NEW: render arrowheads on edges
    ):
        """
        Render the activity network as a DAG with rich tooltips that expose key data.

        Nodes show:
            - id (task_id), description
            - planned duration, delay
            - CPM: ES/EF/LS/LF, slack
            - actual start/end time
            - location, required resources/equipment
            - chain flags: CPM / resource-constrained / zero TF actual

        Edges:
            - Precedence edges from forwardDict
            - Optional resource-flow edges (include_augmented_edges=True) from the augmented graph

        Args:
            filename: Output HTML file name.
            library:  'pyvis' for interactive HTML; 'plotly' for static HTML.
            highlight: Which chain to color:
                'cpm' -> CPM critical path only
                'constrained' -> resource-constrained chain only
                'both' -> both chains distinct (purple for overlap)
                'none' -> no special highlighting (only delay/normal coloring)
            show_unscheduled: Include nodes without actual start/end times.
            show_hold_points: Include activities flagged as hold points (if your Activity has such an attribute).
            physics: Enable/disable PyVis force-directed physics. Default False for stable DAG layering.
            layer_by: Vertical layering: 'es' (CPM ES), 'ls' (CPM LS), 'topo' (topological depth).
            include_augmented_edges: If True, overlay resource-flow edges from _build_augmented_graph().
            max_nodes: If >0, cap number of nodes added (useful for huge graphs); edges referencing hidden nodes are skipped.

        Returns:
            - PyVis Network object (if library='pyvis')
            - Plotly Figure object (if library='plotly')
        """

        # --- Analytics guards (for color highlighting) ---
        cpm_cp_names = set(self.getCriticalPathSymbolic())
        constrained_chain_names = set(a.returnName() for a in getattr(self, 'constrained_chain_list', []))
        zero_tf_set = getattr(self, 'actual_zero_tf_set', set())  # Set[Activity]

        # --- Helpers ---
        def _is_scheduled(act):
            st, et = act.returnAbsTimes()
            return (st is not None) and (et is not None)



        # --- PATCH 3a: augment payload with TF_actual and flags ---

        def _build_node_payload(act):
            info = self.infoDict.get(act, {})
            st, et = act.returnAbsTimes()
            name = act.returnName()

            # Sets from schedule analytics
            cpm_cp_names = set(self.getCriticalPathSymbolic())
            constrained_chain_names = set(a.returnName() for a in getattr(self, 'constrained_chain_list', []))
            zero_tf_set = getattr(self, 'actual_zero_tf_set', set())

            # NEW: fetch TF_actual for this activity (hours)
            tf_actual_map = getattr(self, 'actual_tf', {})
            tf_actual_val = tf_actual_map.get(act, None)

            payload = {
                "id": name, "label": name,
                "description": act.returnDescription(),
                "duration": info.get("duration", act.duration),
                "es": info.get("es", None), "ef": info.get("ef", None),
                "ls": info.get("ls", None), "lf": info.get("lf", None),
                "slack": info.get("slack", None),
                "delay": getattr(act, "delay", 0.0),
                "start_time": st, "end_time": et,
                "resources": act.getRequiredResources() or [],
                "equipment": act.getRequiredEquipment() or [],
                "location": act.getLocation(),
                "on_cpm_critical_path": (name in cpm_cp_names),
                "on_resource_constrained_chain": (name in constrained_chain_names),
                "on_actual_zero_tf": (act in zero_tf_set),
                # NEW
                "tf_actual_hours": tf_actual_val,
                "is_hold_point": getattr(act, 'is_hold_point', False),
            }
            return payload


        def _fmt_dt(dt):
            try:
                return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"
            except Exception:
                return str(dt) if dt else "—"


        # --- PATCH 3b: add Actual TF to tooltip ---

        def _html_tooltip(payload):
            res_lines = ", ".join(
                f"{r.get('skill_type','?')}: {r.get('crew_count','?')}"
                for r in payload["resources"]
            ) or "None"
            eq_lines = ", ".join(
                f"{e.get('equipment_id','?')}: {e.get('quantity_needed','?')}"
                for e in payload["equipment"]
            ) or "None"

            tf_actual_str = (
                f"{payload['tf_actual_hours']:.2f} h"
                if isinstance(payload.get("tf_actual_hours"), (float, int))
                else "—"
            )
            flags = []
            if payload["on_cpm_critical_path"]: flags.append("CPM")
            if payload["on_resource_constrained_chain"]: flags.append("Constrained")
            if payload["on_actual_zero_tf"]: flags.append("Zero TF actual")

            return (
                f"<b>{payload['id']}</b><br>"
                f"<b>Descr:</b> {payload['description']}<br>"
                f"<b>Duration:</b> {payload['duration']} h<br>"
                f"<b>Delay:</b> {payload['delay']} h<br>"
                f"<b>CPM:</b> ES={payload['es']}, EF={payload['ef']}, LS={payload['ls']}, LF={payload['lf']}<br>"
                f"<b>Slack:</b> {payload['slack']} h<br>"
                f"<b>Actual TF:</b> {tf_actual_str}<br>"            # <-- NEW
                f"<b>Start:</b> {_fmt_dt(payload['start_time'])}<br>"
                f"<b>End:</b> {_fmt_dt(payload['end_time'])}<br>"
                f"<b>Location:</b> {payload['location'] or '—'}<br>"
                f"<b>Chain flags:</b> {', '.join(flags) if flags else '—'}<br>"
                f"<b>Resources:</b> {res_lines}<br>"
                f"<b>Equipment:</b> {eq_lines}<br>"
            )

        def _node_color(payload, tol: float = 0.1):
            # Bucket 1: Red if on constrained chain
            if payload["on_resource_constrained_chain"]:
                return "#d62728"   # Red

            # Bucket 2: Orange if NOT on chain and TF_actual ≈ 0
            tf = payload.get("tf_actual_hours", None)
            if tf is not None and abs(tf) <= tol:
                return "#ff7f0e"   # Orange

            # Bucket 3: Blue otherwise
            return "#1f77b4"       # Blue


        def _node_shape(payload):
            nm = payload["id"].upper()
            if nm in ("START", "END"):
                return "box"
            return "ellipse"

        # --- Nodes selection with filtering ---
        all_nodes_payload = []
        for act in self.forwardDict.keys():
            if not show_hold_points and getattr(act, 'is_hold_point', False):
                continue
            if not show_unscheduled and not _is_scheduled(act):
                continue
            all_nodes_payload.append(_build_node_payload(act))

        # Optional limit on nodes for very large graphs
        if max_nodes and len(all_nodes_payload) > max_nodes:
            all_nodes_payload = all_nodes_payload[:max_nodes]

        node_ids_allowed = set(p["id"] for p in all_nodes_payload)

        # --- Edges: precedence (forwardDict) ---
        precedence_edges = []
        for src in self.forwardDict.keys():
            src_name = src.returnName()
            for dst in self.forwardDict[src]:
                dst_name = dst.returnName()
                if (src_name in node_ids_allowed) and (dst_name in node_ids_allowed):
                    precedence_edges.append((src_name, dst_name))

        # --- Augmented resource-flow edges (optional) ---
        augmented_edges = []
        if include_augmented_edges and hasattr(self, "_build_augmented_graph"):
            augmented = self._build_augmented_graph()
            for u, succs in augmented.items():
                u_name = u.returnName() if hasattr(u, "returnName") else getattr(u, "name", str(u))
                for v in succs:
                    v_name = v.returnName() if hasattr(v, "returnName") else getattr(v, "name", str(v))
                    if (u_name in node_ids_allowed) and (v_name in node_ids_allowed):
                        # Only add edges that are NOT already in precedence_edges
                        if (u_name, v_name) not in precedence_edges:
                            augmented_edges.append((u_name, v_name))

        # --- Layering values (y-axis) ---
        # Topological depth
        topo_depth = {}
        indeg = {p["id"]: 0 for p in all_nodes_payload}
        adj = {p["id"]: [] for p in all_nodes_payload}
        for u, v in precedence_edges:
            adj.setdefault(u, []).append(v)
            indeg[v] = indeg.get(v, 0) + 1

        # Kahn's algorithm for depths — deque for O(1) popleft
        from collections import deque as _deque
        queue = _deque(nid for nid, d in indeg.items() if d == 0)
        for nid in queue:
            topo_depth[nid] = 0
        while queue:
            u = queue.popleft()
            for v in adj.get(u, []):
                topo_depth[v] = max(topo_depth.get(v, 0), topo_depth[u] + 1)
                indeg[v] -= 1
                if indeg[v] == 0:
                    queue.append(v)

        def _layer_value(payload):
            if layer_by == "es" and (payload["es"] is not None):
                return float(payload["es"])
            if layer_by == "ls" and (payload["ls"] is not None):
                return float(payload["ls"])
            return float(topo_depth.get(payload["id"], 0))

        # --- Renderer: PyVis ---
        if library.lower() == "pyvis":
            try:
                from pyvis.network import Network
            except ImportError:
                raise ImportError("pyvis is required. Install with: pip install pyvis")

            net = Network(height="800px", width="100%", directed=True, notebook=False)
            if physics:
                net.barnes_hut()
            else:
                net.toggle_physics(False)

            # Add nodes
            for nd in all_nodes_payload:
                net.add_node(
                    nd["id"],
                    label=nd["label"],
                    title=_html_tooltip(nd),
                    color=_node_color(nd),
                    shape=_node_shape(nd),
                    level=_layer_value(nd),
                )

            # Add precedence edges (solid)
            for u, v in precedence_edges:
                net.add_edge(u, v, arrows="to", color="#95a5a6")

            # Add augmented edges (dashed red)
            if augmented_edges:
                for u, v in augmented_edges:
                    net.add_edge(u, v, arrows="to", color="#c0392b", dashes=True)

            net.show(filename)
            logger.debug(f"DAG graph saved to {filename}")
            return net

        # --- Renderer: Plotly ---
        elif library.lower() == "plotly":
            try:
                import plotly.graph_objects as go
                import textwrap
            except ImportError:
                raise ImportError("plotly is required. Install with: pip install plotly")

            # Bucket by layer value (y), spread across x
            layer_buckets = {}
            for nd in all_nodes_payload:
                lv = _layer_value(nd)
                layer_buckets.setdefault(lv, []).append(nd["id"])

            pos = {}
            for y_idx, (layer_val, bucket) in enumerate(sorted(layer_buckets.items(), key=lambda x: x[0])):
                n = len(bucket)
                for x_idx, nid in enumerate(bucket):
                    x = (x_idx - n / 2.0) * 1.2
                    y = -float(layer_val)  # invert so earlier is higher
                    pos[nid] = (x, y)

            # Precedence edge traces (solid gray)
            edge_x = []
            edge_y = []
            for u, v in precedence_edges:
                x0, y0 = pos[u]
                x1, y1 = pos[v]
                edge_x += [x0, x1, None]
                edge_y += [y0, y1, None]
            edge_trace = go.Scatter(
                x=edge_x, y=edge_y,
                line=dict(width=1, color="#95a5a6"),
                hoverinfo='none',
                mode='lines'
            )

            # Augmented edge traces (dashed red)
            aug_x, aug_y = [], []
            for u, v in augmented_edges:
                x0, y0 = pos[u]
                x1, y1 = pos[v]
                aug_x += [x0, x1, None]
                aug_y += [y0, y1, None]
            aug_trace = go.Scatter(
                x=aug_x, y=aug_y,
                line=dict(width=2, color="#c0392b", dash="dash"),  # width=2 & dash='dash'
                hoverinfo='none',
                mode='lines',
                name="Augmented (→)"
            ) if augmented_edges else None

            # Nodes with color & hover
            node_x, node_y, node_color, node_text, node_labels = [], [], [], [], []
            for nd in all_nodes_payload:
                x, y = pos[nd["id"]]
                node_x.append(x)
                node_y.append(y)
                node_color.append(_node_color(nd))
                node_text.append(_html_tooltip(nd))
                node_labels.append(nd["label"])

            node_trace = go.Scatter(
                x=node_x, y=node_y,
                mode='markers+text',
                text=node_labels,
                textposition="bottom center",
                hoverinfo='text',
                hovertext=node_text,
                marker=dict(
                    color=node_color,
                    size=16,
                    line=dict(width=1, color="#2c3e50")
                ),
                name="Activities"
            )

            # --- NEW: Arrowhead annotations for directionality ---
            annotations = []
            if show_edge_arrows:
                # Precedence arrows (solid gray)
                for u, v in precedence_edges:
                    x0, y0 = pos[u]
                    x1, y1 = pos[v]
                    annotations.append(dict(
                        x=x1, y=y1, ax=x0, ay=y0,
                        xref='x', yref='y', axref='x', ayref='y',
                        showarrow=True, arrowhead=2, arrowsize=1.2, arrowwidth=1.5,
                        arrowcolor='#95a5a6', opacity=0.9
                    ))
                """
                # Augmented arrows (red; line remains dashed)
                for u, v in augmented_edges:
                    x0, y0 = pos[u]
                    x1, y1 = pos[v]
                    annotations.append(dict(
                        x=x1, y=y1, ax=x0, ay=y0,
                        xref='x', yref='y', axref='x', ayref='y',
                        showarrow=True, arrowhead=2, arrowsize=1.2, arrowwidth=1.5,
                        arrowcolor='#c0392b', opacity=0.9
                    ))"""


            data = [edge_trace, node_trace]
            if aug_trace is not None:
                data = [edge_trace, aug_trace, node_trace]

            fig = go.Figure(data=data)
            fig.update_layout(
                title="Activity DAG",
                showlegend=False,
                xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                margin=dict(l=20, r=20, t=40, b=20),
                height=800,
                annotations=annotations     # ← REQUIRED: this displays arrowheads
            )
            fig.write_html(filename)
            logger.debug(f"DAG graph saved to {filename}")
            return fig

        else:
            raise ValueError("Unknown library. Use 'pyvis' or 'plotly'.")

# ============================================================================
# SERVICE METHODS
# ============================================================================

def _weight_function(total_float: float, project_duration: float = 10.0) -> float:
    """
    Calculate priority weight based on total float (slack).

    Activities with less slack get higher priority (closer to 1.0).

    The sigmoid inflection point scales with project duration so that the
    "urgency zone" spans roughly 1 % of the project horizon rather than
    a fixed 5 hours.  For a 500-hour outage the cliff sits at ~5 h (floor);
    for a 2 000-hour outage it shifts to ~20 h, preventing near-identical
    floats from receiving radically different priority weights.

    Args:
        total_float:      Float (slack) of the activity in hours.
        project_duration: CPM project duration in hours (default 10 h gives
                          the legacy behaviour for short toy networks).
    """
    threshold = max(5.0, 0.01 * project_duration)
    return 1.0 - 1.0 / (1.0 + math.exp(threshold - total_float))


# ============================================================================
# VISUALIZATION METHODS
# ============================================================================


# --- PATCH 4b: color Gantt with Red/Orange/Blue buckets ---

def plot_gantt_chart(pert, filename='gantt_chart.html', show_delays=True, tol: float = 0.01):
    import plotly.express as px
    df = pert.get_schedule_dataframe()

    def classify(row):
        in_chain = row['on_resource_constrained_chain']
        tf = row['tf_actual_hours']
        is_zero_tf = (tf is not None) and (abs(tf) <= tol)
        if in_chain:
            return 'Red'     # Bucket 1
        elif is_zero_tf:
            return 'Orange'  # Bucket 2
        else:
            return 'Blue'    # Bucket 3

    df['color'] = df.apply(classify, axis=1)

    fig = px.timeline(
        df,
        x_start='start_time', x_end='end_time',
        y='activity_id',
        color='color',
        hover_data=['description', 'duration', 'delay', 'tf_actual_hours', 'on_resource_constrained_chain'],
        title='Project Schedule – Gantt Chart',
        labels={'activity_id': 'Activity', 'color': 'Status'},
        color_discrete_map={
            'Red':    '#d62728',
            'Orange': '#ff7f0e',
            'Blue':   '#1f77b4'
        }
    )
    fig.update_yaxes(autorange="reversed", title="Activities")
    fig.update_xaxes(title="Timeline", tickangle=45, tickformat='%Y-%m-%d %H:%M')
    fig.update_layout(height=max(600, len(df) * 25), showlegend=True, hovermode='closest')
    fig.write_html(filename)
    logger.debug(f"Gantt chart saved to {filename}")
    return fig




def plot_resource_utilization(pert, resource_type, filename=None,
                              show_available=True):
    """
    Plot resource utilization over time.
    """
    try:
        import plotly.graph_objects as go
        import pandas as pd
    except ImportError:
        raise ImportError("plotly and pandas required for visualization")

    if not pert.completed:
        raise ValueError("No schedule calculated. Run calculateScheduleWithResources() first.")

    if not pert.crew_pool.has_skill(resource_type):
        raise ValueError(f"Resource type '{resource_type}' not found in resource pool")

    # Get time range
    start_time = pert.startTime
    end_times = [act.returnAbsTimes()[1] for act in pert.forwardDict.keys() if act.returnAbsTimes()[1] is not None]
    if not end_times:
        raise ValueError("No activities have actual end times — run calculateScheduleWithResources first.")
    end_time = max(end_times)

    # Create hourly time index
    time_range = pd.date_range(start=start_time, end=end_time, freq='h')

    # Calculate usage at each hour
    usage = []
    available = []

    for time_point in time_range:
        # Count workers in use across all scheduled activities
        workers_in_use = 0
        for act in pert.forwardDict.keys():
            act_start, act_end = act.returnAbsTimes()
            if act_start and act_end and act_start <= time_point < act_end:
                for res_req in act.getRequiredResources():
                    if res_req['skill_type'] == resource_type:
                        workers_in_use += res_req['crew_count']

        usage.append(workers_in_use)

        # Get available workers
        if show_available:
            avail = pert.crew_pool.get_availability(resource_type, time_point)
            available.append(avail)

    # Create plot
    fig = go.Figure()

    # Add usage trace
    fig.add_trace(go.Scatter(
        x=time_range,
        y=usage,
        name=f'{resource_type} In Use',
        fill='tozeroy',
        line=dict(color='#3498db', width=2)
    ))

    # Add available trace
    if show_available:
        fig.add_trace(go.Scatter(
            x=time_range,
            y=available,
            name=f'{resource_type} Available',
            line=dict(color='#2ecc71', width=2, dash='dash')
        ))

    # Layout
    fig.update_layout(
        title=f'{resource_type} Utilization Over Time',
        xaxis_title='Time',
        yaxis_title='Number of Workers',
        hovermode='x unified',
        showlegend=True
    )

    fig.update_xaxes(tickangle=45, tickformat='%Y-%m-%d %H:%M')

    if filename:
        fig.write_html(filename)
        logger.debug(f"Resource utilization chart saved to {filename}")
    else:
        fig.show()

    return fig


def plot_location_utilization(pert, location_id, filename=None):
    """
    Plot location utilization over time.
    """
    try:
        import plotly.graph_objects as go
        import pandas as pd
    except ImportError:
        raise ImportError("plotly and pandas required for visualization")

    if not pert.completed:
        raise ValueError("No schedule calculated")

    if not pert.location_pool.has_location(location_id):
        raise ValueError(f"Location '{location_id}' not found")

    # Get time range
    start_time = pert.startTime
    end_times = [act.returnAbsTimes()[1] for act in pert.forwardDict.keys() if act.returnAbsTimes()[1] is not None]
    if not end_times:
        raise ValueError("No activities have actual end times — run calculateScheduleWithResources first.")
    end_time = max(end_times)
    time_range = pd.date_range(start=start_time, end=end_time, freq='h')

    # Calculate usage and capacity
    tasks_in_use = []
    workers_in_use = []
    max_tasks = []
    max_workers = []

    for time_point in time_range:
        # Count tasks and workers at location
        n_tasks = 0
        n_workers = 0

        for act in pert.forwardDict.keys():
            if act.getLocation() == location_id:
                act_start, act_end = act.returnAbsTimes()
                if act_start and act_end and act_start <= time_point < act_end:
                    n_tasks += 1
                    # Count total workers
                    for res_req in act.getRequiredResources():
                        n_workers += res_req['crew_count']

        tasks_in_use.append(n_tasks)
        workers_in_use.append(n_workers)

        # Get capacity
        capacity = pert.location_pool.get_capacity(location_id, time_point)
        max_tasks.append(capacity['max_tasks'])
        max_workers.append(capacity['max_workers'] if capacity['max_workers'] else 0)

    # Create subplots
    fig = go.Figure()

    # Tasks
    fig.add_trace(go.Scatter(
        x=time_range, y=tasks_in_use,
        name='Tasks In Progress',
        fill='tozeroy',
        line=dict(color='#3498db')
    ))

    fig.add_trace(go.Scatter(
        x=time_range, y=max_tasks,
        name='Max Task Capacity',
        line=dict(color='#e74c3c', dash='dash')
    ))

    fig.update_layout(
        title=f'Location Utilization: {location_id}',
        xaxis_title='Time',
        yaxis_title='Number of Tasks',
        hovermode='x unified'
    )

    if filename:
        fig.write_html(filename)
        logger.debug(f"Location utilization chart saved to {filename}")
    else:
        fig.show()

    return fig


def plot_equipment_utilization(pert, equipment_id, filename=None, show_available=True):
    """
    Plot equipment utilization over time.

    Displays:
      - 'In Use' (area): total units of the specified equipment engaged per hour,
        computed from scheduled activities using half-open intervals [start, end).
      - 'Available' (dashed line, optional): per-hour availability from EquipmentPool.
      - 'Deficit' markers (optional): red points where demand exceeds availability.

    Args:
        pert: Pert instance with a calculated schedule (calculateScheduleWithResources must have run).
        equipment_id (str): Equipment identifier present in EquipmentPool.
        filename (str or None): If provided, writes HTML to this path. Otherwise shows the figure.
        show_available (bool): Whether to overlay the availability time series.

    Returns:
        A Plotly Figure object.

    Raises:
        ValueError: if no schedule has been calculated or equipment_id is not found.
        ImportError: if required plotting libraries are missing.
    """
    # --- Imports (aligned with other plotting funcs) ---
    try:
        import plotly.graph_objects as go
        import pandas as pd
    except ImportError:
        raise ImportError("plotly and pandas required for visualization")

    # --- Guardrails ---
    if not pert.completed:
        raise ValueError("No schedule calculated. Run calculateScheduleWithResources() first.")
    if not pert.equipment_pool.has_equipment(equipment_id):
        raise ValueError(f"Equipment '{equipment_id}' not found in equipment pool")

    # --- Time window: from schedule start to latest actual end across activities ---
    start_time = pert.startTime
    end_time_candidates = [
        a.returnAbsTimes()[1] for a in pert.forwardDict.keys()
        if a.returnAbsTimes()[0] is not None and a.returnAbsTimes()[1] is not None
    ]
    if not end_time_candidates:
        raise ValueError("No activities have actual start/end times to plot")
    end_time = max(end_time_candidates)

    # Hourly index (inclusive of end; consistent with other plots)
    time_range = pd.date_range(start=start_time, end=end_time, freq='h')

    # --- Pre-index scheduled activities that use this equipment for performance ---
    scheduled_acts_using_eq = []
    for act in pert.forwardDict.keys():
        st, et = act.returnAbsTimes()
        if st is None or et is None:
            continue
        qty = sum(
            e['quantity_needed'] for e in act.getRequiredEquipment()
            if e.get('equipment_id') == equipment_id
        )
        if qty > 0:
            scheduled_acts_using_eq.append((act, st, et, qty))

    # If nothing uses this equipment, still produce a valid plot
    if not scheduled_acts_using_eq:
        # Build a flat zero series; optional availability overlay
        in_use = [0] * len(time_range)
        available = []
        if show_available:
            available = [pert.equipment_pool.get_availability(equipment_id, t) for t in time_range]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=time_range, y=in_use,
            name=f"{equipment_id} In Use",
            fill='tozeroy',
            line=dict(color='#3498db', width=2),
            hoverinfo='x+y',
        ))
        if show_available:
            fig.add_trace(go.Scatter(
                x=time_range, y=available,
                name=f"{equipment_id} Available",
                line=dict(color='#2ecc71', width=2, dash='dash'),
                hoverinfo='x+y',
            ))
        fig.update_layout(
            title=f"{equipment_id} Utilization Over Time",
            xaxis_title='Time',
            yaxis_title='Units of Equipment',
            hovermode='x unified',
            showlegend=True
        )
        fig.update_xaxes(tickangle=45, tickformat='%Y-%m-%d %H:%M')
        if filename:
            fig.write_html(filename)
            logger.debug(f"Equipment utilization chart saved to {filename}")
        else:
            fig.show()
        return fig

    # --- Build per-hour series: in_use, available (optional), and deficit markers ---
    in_use = []
    available = [] if show_available else None
    # For richer hover: list of consumer activities at each hour
    consumers_text = []

    for t in time_range:
        # Sum quantities for activities overlapping this hour (half-open: st <= t < et)
        use_at_t = 0
        consumers_at_t = []
        for act, st, et, qty in scheduled_acts_using_eq:
            if st <= t < et:
                use_at_t += qty
                consumers_at_t.append(act.returnName())
        in_use.append(use_at_t)
        consumers_text.append(", ".join(consumers_at_t) if consumers_at_t else "None")

        if show_available:
            available.append(pert.equipment_pool.get_availability(equipment_id, t))

    # Identify deficit points (in_use > available)
    deficit_x = []
    deficit_y = []
    deficit_hover = []
    if show_available:
        for t, use_val, avail_val, consumers in zip(time_range, in_use, available, consumers_text):
            if use_val > avail_val:
                deficit_x.append(t)
                deficit_y.append(use_val)
                deficit_hover.append(
                    f"Deficit at {t.strftime('%Y-%m-%d %H:%M')}<br>"
                    f"In Use: {use_val} | Available: {avail_val}<br>"
                    f"Consumers: {consumers}"
                )

    # --- Peak usage summary (printed) ---
    peak_idx = int(max(range(len(in_use)), key=lambda i: in_use[i])) if in_use else 0
    peak_time = time_range[peak_idx]
    peak_val = in_use[peak_idx]
    if peak_val > 0:
        # Recompute consumers at peak for clarity
        peak_consumers = []
        for act, st, et, qty in scheduled_acts_using_eq:
            if st <= peak_time < et:
                peak_consumers.append(act.returnName())
        logger.debug(f"[{equipment_id}] Peak in-use: {peak_val} at {peak_time.strftime('%Y-%m-%d %H:%M')}")
        logger.debug(f"  Consumers at peak: {sorted(peak_consumers)}")
    else:
        logger.debug(f"[{equipment_id}] No scheduled usage detected.")

    # --- Build figure ---
    fig = go.Figure()

    # In Use (area)
    fig.add_trace(go.Scatter(
        x=time_range, y=in_use,
        name=f"{equipment_id} In Use",
        fill='tozeroy',
        line=dict(color='#3498db', width=2),
        hoverinfo='text',
        hovertext=[
            f"{t.strftime('%Y-%m-%d %H:%M')}<br>In Use: {val}<br>Consumers: {txt}"
            for t, val, txt in zip(time_range, in_use, consumers_text)
        ],
    ))

    # Available (dashed line)
    if show_available:
        fig.add_trace(go.Scatter(
            x=time_range, y=available,
            name=f"{equipment_id} Available",
            line=dict(color='#2ecc71', width=2, dash='dash'),
            hoverinfo='x+y',
        ))

    # Deficit markers (red points)
    if show_available and deficit_x:
        fig.add_trace(go.Scatter(
            x=deficit_x, y=deficit_y,
            name="Deficit",
            mode='markers',
            marker=dict(color='#d62728', size=8),
            hoverinfo='text',
            hovertext=deficit_hover,
        ))

    # Layout
    fig.update_layout(
        title=f"{equipment_id} Utilization Over Time",
        xaxis_title='Time',
        yaxis_title='Units of Equipment',
        hovermode='x unified',
        showlegend=True
    )
    fig.update_xaxes(tickangle=45, tickformat='%Y-%m-%d %H:%M')

    # Output
    if filename:
        fig.write_html(filename)
        logger.debug(f"Equipment utilization chart saved to {filename}")
    else:
        fig.show()

    return fig








# ============================================================================
# MD-KNAPSACK OPTIMIZATION
# ============================================================================

class MDKnapsackScheduler:
    """
    Multi-Dimensional Knapsack optimizer for activity selection.

    Solves the problem: Given candidate activities and resource constraints,
    select the set of activities that maximizes value while respecting
    all resource limits.
    """

    def __init__(self, candidates: Dict, crew_pool, equipment_pool,
                 location_pool, time_point: datetime, value_mode='uniform'):
        """
        Initialize MD-Knapsack optimizer.

        Args:
            candidates (dict): Candidate activities with info
            crew_pool (ResourcePool): Resource availability
            equipment_pool (EquipmentPool): Equipment availability
            location_pool (LocationPool): Location availability
            time_point (datetime): Current scheduling time
            value_mode (str): 'uniform' or 'value_based'
        """
        self.candidates = list(candidates.keys())
        self.candidate_info = candidates
        self.crew_pool = crew_pool
        self.equipment_pool = equipment_pool
        self.location_pool = location_pool
        self.time_point = time_point
        self.value_mode = value_mode

    def solve(self) -> List:
        """
        Solve the MD-Knapsack problem using greedy heuristic.

        Returns:
            list: Selected activities

        Note:
            This is a greedy approximation. For exact solution, would need
            integer programming solver (e.g., PuLP, CPLEX).
        """
        # Get resource capacities at current time
        capacities = self._get_capacities()

        # Calculate efficiency ratio for each activity
        efficiency = []
        for act in self.candidates:
            if self.value_mode == 'uniform':
                value = 1.0
            else:
                value = self.candidate_info[act]['value']

            # Resource consumption
            consumption = self._get_resource_consumption(act)

            # Efficiency = value / total resource consumption
            total_consumption = sum(consumption.values())
            eff = value / max(total_consumption, 1.0)

            efficiency.append((act, eff, consumption))

        # Sort by efficiency (descending)
        efficiency.sort(key=lambda x: x[1], reverse=True)

        # Greedy selection
        selected = []
        remaining_capacity = capacities.copy()

        for act, eff, consumption in efficiency:
            # Check if activity fits
            can_fit = True
            for resource, amount in consumption.items():
                if amount > remaining_capacity.get(resource, 0):
                    can_fit = False
                    break

            if can_fit:
                selected.append(act)
                # Update remaining capacity
                for resource, amount in consumption.items():
                    remaining_capacity[resource] -= amount

        return selected

    def _get_capacities(self) -> Dict:
        """Get available capacity at current time point (original availability).

        Returns a flat dict keyed by prefixed dimension strings:
        ``RESOURCE_<skill>``, ``EQUIPMENT_<eq_id>``,
        ``LOC_TASKS_<loc_id>``, ``LOC_WORKERS_<loc_id>``.

        Location task/worker caps are included so the greedy selector avoids
        assigning too many concurrent tasks or workers to the same physical
        zone — a common source of infeasibility in high-dose outage areas.
        ``None`` caps (unlimited workers) are stored as ``math.inf``.
        """
        capacities = {}

        # Resource capacities
        for skill in self.crew_pool.get_all_skills():
            avail = self.crew_pool.get_availability(skill, self.time_point)
            capacities[f'RESOURCE_{skill}'] = avail

        # Equipment capacities
        for eq_id in self.equipment_pool.get_all_equipment_ids():
            avail = self.equipment_pool.get_availability(eq_id, self.time_point)
            capacities[f'EQUIPMENT_{eq_id}'] = avail

        # Location capacities
        for loc_id in self.location_pool.get_all_location_ids():
            cap = self.location_pool.get_capacity(loc_id, self.time_point)
            capacities[f'LOC_TASKS_{loc_id}'] = cap.get('max_tasks', 0)
            max_w = cap.get('max_workers', None)
            capacities[f'LOC_WORKERS_{loc_id}'] = (
                float('inf') if max_w is None else max_w
            )

        return capacities

    def _get_resource_consumption(self, activity) -> Dict:
        """Get resource consumption for an activity.

        Returns the same dimension space as :meth:`_get_capacities` so the
        greedy knapsack loop can compare them directly.  Location dimensions
        use 1 task slot and the sum of crew counts as worker slots.
        """
        consumption = {}

        # Resources
        for res_req in activity.getRequiredResources():
            key = f'RESOURCE_{res_req["skill_type"]}'
            consumption[key] = res_req['crew_count']

        # Equipment
        for eq_req in activity.getRequiredEquipment():
            key = f'EQUIPMENT_{eq_req["equipment_id"]}'
            consumption[key] = eq_req['quantity_needed']

        # Location / zone: 1 concurrent task slot per zone + aggregate worker slots.
        workers = sum(r['crew_count'] for r in activity.getRequiredResources())
        for zone_id in activity.getZoneIds():
            consumption[f'LOC_TASKS_{zone_id}'] = 1
            if workers:
                consumption[f'LOC_WORKERS_{zone_id}'] = workers

        return consumption


# ============================================================================
# LOOK-AHEAD SCHEDULING HEURISTICS
# ============================================================================


class LookAheadScheduler:
    def __init__(self, pert, look_ahead_hours=24):
        self.pert = pert
        self.look_ahead_hours = look_ahead_hours

    def select_activities(self, candidates: Dict, time_point: datetime) -> List:
        """
        Rank candidates by immediate value + expected future opportunities after
        they finish, then greedily select those that are feasible at time_point.

        Feasibility is evaluated using shared capacity snapshots that are
        decremented as each activity is tentatively selected, preventing
        overbooking within a single scheduling step.  This mirrors the approach
        used by _schedule_generation_scheme for all other SGS strategies.

        Args:
            candidates (dict): {Activity: info_dict} from _select_candidate_activities.
            time_point (datetime): Current scheduling event time.

        Returns:
            list: Activities selected to start at time_point, in selection order.
        """
        # ── Step 1: score all candidates ────────────────────────────────────────
        scored = []
        for act, info in candidates.items():
            immediate_value = info.get('value', 1.0)
            future_value    = self._evaluate_future_opportunities(act, time_point)
            total_score     = immediate_value + 0.3 * future_value
            scored.append((act, total_score))

        # Sort by combined score descending so highest-value activities get
        # first pick of the shared capacity pool.
        scored.sort(key=lambda x: x[1], reverse=True)

        # ── Step 2: build ONE shared snapshot covering all candidates' windows ──
        # The snapshot must span the longest candidate duration so that
        # _fits_with_tentative has valid entries for every hour any candidate
        # might run.  _build_capacity_snapshots already subtracts self.ongoing,
        # so the snapshots reflect truly remaining capacity.
        if not scored:
            return []

        max_end = time_point
        cand_ends_la: set = set()
        for act, _ in scored:
            cand_end = time_point + timedelta(hours=self.pert._effective_duration(act))
            cand_ends_la.add(cand_end)
            if cand_end > max_end:
                max_end = cand_end

        res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid = \
            self.pert._build_capacity_snapshots(time_point, max_end, extra_boundaries=cand_ends_la)

        # ── Step 3: greedy selection with tentative capacity decrement ───────────
        selected = []
        for act, score in scored:
            if self.pert._fits_with_tentative(
                act, time_point,
                res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid
            ):
                selected.append(act)
                # Decrement shared snapshots so the next candidate in the loop
                # sees reduced capacity — preventing overbooking.
                self.pert._apply_tentative(
                    act, time_point,
                    res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid
                )

        return selected

    def _evaluate_future_opportunities(self, activity, time_point: datetime) -> float:
        """
        Estimate future opportunity value *after* this activity finishes.

        Components:
          - Successor enablement: count/value successors with minimal slack that become eligible at finish_time.
          - Resource pressure relief: starting now frees resources at finish_time in a high-pressure period.
        """
        # When does this activity finish? (planned duration only)
        finish_time = time_point + timedelta(hours=max(0.0, activity.duration))

        future_score = 0.0

        # 1) Successor enablement score: successors that could be started at/after finish_time
        for succ in self.pert.forwardDict.get(activity, []):
            # All other predecessors must be completed (by finish_time)
            other_preds = [p for p in self.pert.backwardDict.get(succ, []) if p is not activity]
            # If any other predecessor won't be completed by finish_time, skip
            other_preds_complete = all(
                (p in self.pert.completed) or
                (p in self.pert.ongoing and p.returnAbsTimes()[1] <= finish_time)
                for p in other_preds
            )
            if not other_preds_complete:
                continue

            # Slack-based value: successors with smaller slack are more valuable to enable
            slack = self.pert.infoDict[succ]['slack']
            future_score += _weight_function(slack, self.pert.getProjectDuration())

        # 2) Resource pressure relief at finish time
        #    If resources are scarce at finish_time, completing this activity earlier helps open capacity
        pressure_score = 0.0
        for req in activity.getRequiredResources():
            skill = req['skill_type']
            avail = self.pert.crew_pool.get_availability(skill, finish_time)
            demand = 0
            # Estimated demand at finish_time from waiting tasks
            for w in self.pert.wait:
                for r in w.getRequiredResources():
                    if r['skill_type'] == skill:
                        demand += r['crew_count']
            # Pressure = demand / max(avail, 1)
            if avail > 0:
                pressure_score += (demand / avail)
            else:
                pressure_score += 2.0  # arbitrary high penalty when zero availability

        # Combine opportunity and pressure; weight can be tuned
        future_score += 0.15 * pressure_score

        return future_score
