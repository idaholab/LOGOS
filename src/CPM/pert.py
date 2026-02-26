
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
import math
import random
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import heapq

# Assuming these are imported from your modules
from activity import Activity
from outage_data import ResourcePool, EquipmentPool, LocationPool, OutageData, load_outage_data
from validate_outage_data import OutageDataValidator

logging.basicConfig(level=logging.DEBUG)

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

        self.task_to_activity = {} # dictionary in the form: {act_ID: act_instance}

        # Store outage data components
        self.outage_data = outage_data
        if outage_data:
            self.resource_pool = outage_data.resource_pool
            self.equipment_pool = outage_data.equipment_pool
            self.location_pool = outage_data.location_pool
            self.startTime = outage_data.start_date
            self.working_hours_per_day = outage_data.working_hours_per_day
        else:
            self.resource_pool = None
            self.equipment_pool = None
            self.location_pool = None
            self.startTime = None
            self.working_hours_per_day = 24

        # Priority values for activities
        self.priorities = priorities

        # Random seed
        self.seed = seed
        random.seed(self.seed)

        # Initialize activity tracking lists
        self.wait = []
        self.ongoing = []
        self.completed = []

        # Activities
        self.startActivity = None
        self.endActivity = None

        # If outage_data provided, build graph from tasks
        if outage_data and not graph:
            self._build_graph_from_outage_data()

        # Initialize graph structure
        if self.forwardDict:
            self.resetInitialGraph()
            self.generateInfo()
            self._update_activity_successors()

        self._availability_events: frozenset = frozenset()
        if self.resource_pool or self.equipment_pool or self.location_pool:
            self._precompute_availability_events()

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
                print(f"[validation warning] {w}")

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

        # Build forward dictionary (activity -> successors)
        for task_dict in self.outage_data.tasks:
            activity = self.task_to_activity[task_dict['task_id']]
            successors = []
            for succ_id in task_dict.get('successors', []):
                if succ_id in self.task_to_activity:
                    successors.append(self.task_to_activity[succ_id])
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
                            logging.warning(
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

    def resetInitialGraph(self):
        """
        Reset the schedule graph structure.

        - Resets backwardDict for every activity
        - Sets startActivity and endActivity
        - Resets info dictionary
        """
        # Initialize backward dictionary
        for activity in self.forwardDict:
            self.backwardDict[activity] = []

        # Build backward relationships and identify start/end
        for activity in self.forwardDict:
            # Identify start and end activities
            if activity.name.upper() == "START":
                self.startActivity = activity
            if activity.name.upper() == "END":
                self.endActivity = activity

            # Build backward links
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
                "slack": 0
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

        # Step 1: update duration on each Activity object
        for task_id, duration in new_durations.items():
            if task_id not in self.task_to_activity:
                raise KeyError(f"set_durations: task_id '{task_id}' not found in schedule")
            self.task_to_activity[task_id].updateDuration(duration)

        # Step 2: push the new durations into infoDict so generateInfo() sees them
        self._sync_infodict_durations()

        # Step 3: recompute ES, EF, LS, LF, slack for the whole network
        self.generateInfo()

        logging.debug(
            "set_durations: updated %d activities and recomputed CPM. "
            "New project duration = %.1f h",
            len(new_durations),
            self.getProjectDuration()
        )

    def generateInfo(self):
        """
        Calculate es, ef, ls, lf, and slack for all activities using topological order.
        """
        if not self.forwardDict:
            return
        if not self.startActivity or not self.endActivity:
            raise ValueError("Start and End activities must be defined")

        # 1) Topological order (Kahn)
        indeg = {a: 0 for a in self.forwardDict.keys()}
        for u, succs in self.forwardDict.items():
            for v in succs:
                indeg[v] = indeg.get(v, 0) + 1
        queue = [self.startActivity] if self.startActivity in indeg else [a for a, d in indeg.items() if d == 0]
        topo = []
        while queue:
            u = queue.pop(0)
            topo.append(u)
            for v in self.forwardDict.get(u, []):
                indeg[v] -= 1
                if indeg[v] == 0:
                    queue.append(v)

        # 2) Forward pass: ES/EF
        for a in self.forwardDict.keys():
            self.infoDict[a]["es"] = 0.0
            self.infoDict[a]["ef"] = 0.0
        # Start ES/EF
        self.infoDict[self.startActivity]["es"] = 0.0
        self.infoDict[self.startActivity]["ef"] = self.infoDict[self.startActivity]["duration"]

        for u in topo:
            u_ef = self.infoDict[u]["ef"]
            for v in self.forwardDict.get(u, []):
                if u_ef > self.infoDict[v]["es"]:
                    self.infoDict[v]["es"] = u_ef
                    self.infoDict[v]["ef"] = self.infoDict[v]["es"] + self.infoDict[v]["duration"]

        # 3) Backward pass: LS/LF (reverse topo)
        project_duration = self.infoDict[self.endActivity]["ef"]
        for a in self.forwardDict.keys():
            self.infoDict[a]["lf"] = project_duration
            self.infoDict[a]["ls"] = self.infoDict[a]["lf"] - self.infoDict[a]["duration"]

        for u in reversed(topo):
            for v in self.forwardDict.get(u, []):
                # LF(u) = min over successors' LS(v)
                if self.infoDict[u]["lf"] > self.infoDict[v]["ls"]:
                    self.infoDict[u]["lf"] = self.infoDict[v]["ls"]
                    self.infoDict[u]["ls"] = self.infoDict[u]["lf"] - self.infoDict[u]["duration"]

        # 4) Slack
        self.calculateSlack()

        # 5) Isolated activities (keep your existing treatment)
        self.generateInfoForIsolated()


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
        Calculate timing for isolated activities.

        Assumption: activity duration shorter than project duration
        """
        isolated = self.findIsolated()
        for activity in isolated:
            self.infoDict[activity]["ef"] = (
                self.infoDict[activity]["es"] +
                self.infoDict[activity]["duration"]
            )
            self.infoDict[activity]["lf"] = self.infoDict[self.endActivity]["lf"]
            self.infoDict[activity]["ls"] = (
                self.infoDict[activity]["lf"] -
                self.infoDict[activity]["duration"]
            )
            self.infoDict[activity]["slack"] = (
                self.infoDict[activity]["lf"] -
                self.infoDict[activity]["ef"]
            )

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

    def _is_zero(self, x: float, tol: float = 1e-6) -> bool:
        return abs(x) <= tol

    def _eq_with_tol(self, a: float, b: float, tol: float = 1e-6) -> bool:
        return abs(a - b) <= tol

    def getCriticalPath(self) -> List[Activity]:
        """
        Return a list of Activity objects forming the critical path.

        Method:
        - Build a subgraph of edges (u -> v) that satisfy:
            slack(v) ~ 0  AND  ef(u) == es(v) (within tolerance)
        - DFS from START to END to find the path with maximum total planned duration.
        - Fallback: if no path reaches END, walk by repeatedly choosing the successor
        with minimum slack that also best aligns EF/ES.
        """
        if not self.startActivity or not self.endActivity:
            # Defensive: return empty instead of None
            return []

        # Build zero-slack, EF/ES-aligned subgraph
        crit_successors = {}
        for u in self.forwardDict.keys():
            crit_successors[u] = []
            u_ef = self.infoDict[u]["ef"]
            for v in self.forwardDict[u]:
                v_es = self.infoDict[v]["es"]
                v_slack = self.infoDict[v]["slack"]
                if self._is_zero(v_slack) and self._eq_with_tol(u_ef, v_es):
                    crit_successors[u].append(v)

        # DFS to find the longest (by planned duration) route to END
        best_path: List[Activity] = []
        best_len: float = -float('inf')

        def dfs(u: Activity, path: List[Activity], length: float):
            nonlocal best_path, best_len
            if u == self.endActivity:
                if length > best_len:
                    best_len = length
                    best_path = path[:]
                return
            for v in crit_successors.get(u, []):
                # Add planned duration of v (do not subtract delay; work content is planned duration)
                dfs(v, path + [v], length + self.infoDict[v]['duration'])

        # Start with START (include its planned duration)
        dfs(self.startActivity, [self.startActivity], self.infoDict[self.startActivity]['duration'])

        if best_path:
            return best_path

        # Fallback heuristic:
        # If the strict critical subgraph doesn't connect START -> END,
        # greedily walk by picking the successor with minimum slack AND closest EF/ES alignment.
        current = self.startActivity
        path = [current]
        visited = set([current])  # avoid cycles defensively

        while current != self.endActivity:
            succs = self.forwardDict.get(current, [])
            if not succs:
                # Dead end; break defensively (no infinite loop)
                break

            # Rank successors: first by abs(ef(current) - es(successor)), then by slack
            cur_ef = self.infoDict[current]['ef']
            ranked = sorted(
                succs,
                key=lambda v: (
                    abs(cur_ef - self.infoDict[v]['es']),
                    self.infoDict[v]['slack']
                )
            )
            next_act = ranked[0]
            if next_act in visited:
                # Cycle detected defensively
                break
            path.append(next_act)
            visited.add(next_act)
            current = next_act

        return path

    def getCriticalPathSymbolic(self):
        """
        Get critical path as a list of activity names.

        Returns:
            list: List of activity names on critical path
        """

        path = self.getCriticalPath()
        return [a.returnName() for a in path] if path else []


    def getCriticalPathWithLength(self):
        """
        Get critical path as dictionary with durations.

        Returns:
            dict: {Activity: duration} for activities on critical path
        """
        return {activity: activity.duration for activity in self.getCriticalPath()}

    def getProjectDuration(self):
        """
        Get total project duration from critical path.

        Returns:
            float: Project duration in hours
        """
        if self.endActivity:
            return self.infoDict[self.endActivity]['ef']
        return 0

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

        # Initialize info
        self.infoDict[activity] = {
            "duration": activity.duration,
            "es": 0,
            "ef": 0,
            "ls": 0,
            "lf": math.inf,
            "slack": 0
        }

        # Recalculate
        self.resetInfo()
        self.generateInfo()

    def print_summary(self):
        """Print summary of the schedule."""
        print("=" * 70)
        print("PERT SCHEDULE SUMMARY")
        print("=" * 70)
        print(f"Total Activities: {len(self.forwardDict)}")
        print(f"Project Duration: {self.getProjectDuration():.2f} hours")

        if self.startTime:
            end_time = self.returnScheduleEndTime()
            print(f"Start Time: {self.startTime.strftime('%Y-%m-%d %H:%M')}")
            print(f"End Time: {end_time.strftime('%Y-%m-%d %H:%M')}")

        print(f"\nCritical Path ({len(self.getCriticalPath())} activities):")
        cp_symbolic = self.getCriticalPathSymbolic()
        print(" -> ".join(cp_symbolic))

        print("\nCritical Path Durations:")
        for activity in self.getCriticalPath():
            if activity.name not in ['START', 'END']:
                print(f"  {activity.name}: {activity.duration:.1f} hours - {activity.description}")

        print("=" * 70)

    def __repr__(self):
        """String representation of Pert object."""
        n_activities = len(self.forwardDict)
        duration = self.getProjectDuration()
        return f"Pert({n_activities} activities, duration={duration:.2f} hours)"


    def _reset_scheduling_state(self):
        """
        Reset all mutable scheduling state on activities and on the Pert instance
        so that calculateScheduleWithResources() produces a clean result each time
        it is called (e.g. across successive RAVEN Monte-Carlo iterations).

        What is reset:
            Activity level:
                - startTime, endTime  (set by setActualStartTime)
                - delay               (incremented by addDelay)
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
        self.wait = list(self.forwardDict.keys())
        self.ongoing = []
        self.completed = []

        # Reset step-by-step log
        self.schedule_log = []

        # Reset post-schedule analytics (they will be recomputed at end of run)
        self.actual_tf = {}
        self.actual_zero_tf_set = set()
        self.constrained_chain_list = []
        self.constrained_chain_set = set()


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

        if self.resource_pool:
            for skill in self.resource_pool.get_all_skills():
                for period in self.resource_pool.resources[skill].get_all_periods():
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
        logging.debug(
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

        heap = list(events)
        heapq.heapify(heap)

        logging.debug(
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
                                       max_time_hours: float = None) -> dict:
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
                startTime.  Defaults to 3× the CPM duration.

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
        if not self.resource_pool or not self.equipment_pool or not self.location_pool:
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
            max_time_hours = cpm_duration * 3   # generous safety margin
        max_time = self.startTime + timedelta(hours=max_time_hours)

        logging.info(
            "Starting event-driven RCPSP | activities=%d | CPM=%.1fh | "
            "strategy=%s | max_time=%.1fh",
            n_activities, cpm_duration, sgs, max_time_hours
        )

        # ── Bootstrap START activity ─────────────────────────────────────────
        if self.startActivity and self.startActivity in self.wait:
            self.startActivity.setActualStartTime(self.startTime)
            self.wait.remove(self.startActivity)
            self.ongoing.append(self.startActivity)
            # Push START's completion time so the loop wakes up when it finishes
            _, start_end = self.startActivity.returnAbsTimes()
            heapq.heappush(event_heap := [], start_end)
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
                    logging.warning(
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
                logging.warning(
                    "Scheduling reached safety cutoff at %s. "
                    "Completed %d/%d activities.",
                    time_index.strftime('%Y-%m-%d %H:%M'),
                    len(self.completed), n_activities
                )
                break

            iteration += 1

            # ── Move finished activities to completed ────────────────────────
            self._update_ongoing_list(time_index)

            if len(self.completed) == n_activities:
                break   # finished exactly on this event

            # ── Find candidates eligible at time_index ───────────────────────
            value_mode = 'TF_based' if self.priorities is None else 'external'
            candidates = self._select_candidate_activities(time_index, value_mode)

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
                    'time':       time_index,
                    'candidates': [a.name for a in candidates],
                    'selected':   [a.name for a in selected],
                    'ongoing':    [a.name for a in self.ongoing],
                    'completed':  [a.name for a in self.completed],
                })

            print('==============')
            print(f"t={time_index.strftime('%Y-%m-%d %H:%M')}")
            print(f"completed={[a.name for a in self.completed]}")
            print(f"ongoing={[a.name for a in self.ongoing]}")
            print(f"waiting={[a.name for a in self.wait]}")
            if candidates:
                print(f"candidates={[a.name for a in candidates.keys()]}")
            if selected:
                print(f"selected={[a.name for a in selected]}")

            logging.debug(
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
        }

        logging.info(
            "Scheduling complete | iterations=%d | CPM=%.1fh | "
            "actual=%.1fh | delay=%.1fh | completed=%d/%d",
            iteration, cpm_duration, actual_duration,
            total_delay, len(self.completed), n_activities
        )
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
                self.wait.remove(act)
                self.ongoing.append(act)

            postponed = set(candidates.keys()) - set(selected)
            for act in postponed:
                act.addDelay(elapsed_hours)
        else:
            # No activity could be started this step
            for act in candidates.keys():
                act.addDelay(elapsed_hours)
    
    def _select_candidate_activities(self, time: datetime, value_assignment: str) -> Dict[Activity, Dict]:
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

        for act in list(self.wait):
            # Check predecessors complete
            predecessors_complete = all(
                pred in self.completed
                for pred in self.backwardDict.get(act, [])
            )
            if not predecessors_complete:
                continue

            # ES reached?
            abs_es = self.startTime + timedelta(hours=self.infoDict[act]['es'])
            if abs_es <= time:
                candidates[act] = self.infoDict[act].copy()

        # Assign priority
        if value_assignment == 'TF_based':
            for act in candidates.keys():
                candidates[act]['value'] = _weight_function(candidates[act]['slack'])
        elif value_assignment == 'external':
            for act in candidates.keys():
                act_name = act.returnName()
                candidates[act]['value'] = self.priorities.get(act_name, 0.5)
        
        return candidates

    # -----------------------------
    # Selection helpers 
    # -----------------------------
    def _effective_duration(self, activity) -> float:
        """Clamped effective runtime used for scheduling."""
        #return max(0.0, activity.duration - activity.delay)
        return max(0.0, activity.duration)

    def _iter_hours(self, start: datetime, end: datetime):
        """Yield each hour (inclusive) between start and end."""
        t = start
        while t < end:   # strict <
            yield t
            t += timedelta(hours=1)


    def _build_capacity_snapshots(self, start_time: datetime, end_time: datetime):
        """
        Build per-hour snapshots of remaining capacity for resources, equipment,
        and location task/worker capacity, after subtracting consumption of ongoing tasks.
        """
        res_rem = defaultdict(dict)        # res_rem[skill][hour] = remaining workers
        eq_rem = defaultdict(dict)         # eq_rem[eq_id][hour] = remaining units
        loc_tasks_rem = defaultdict(dict)  # loc_tasks_rem[loc_id][hour] = remaining task slots
        loc_workers_rem = defaultdict(dict)# loc_workers_rem[loc_id][hour] = remaining worker slots (None = unlimited)

        hours = list(self._iter_hours(start_time, end_time))

        # Remaining resources/equipment after ongoing consumption
        for skill in self.resource_pool.get_all_skills():
            for h in hours:
                orig = self.resource_pool.get_availability(skill, h)
                consumed = self._get_consumed_resources(skill, h)
                res_rem[skill][h] = max(0, orig - consumed)

        for eq in self.equipment_pool.get_all_equipment_ids():
            for h in hours:
                orig = self.equipment_pool.get_availability(eq, h)
                consumed = self._get_consumed_equipment(eq, h)
                eq_rem[eq][h] = max(0, orig - consumed)

        # Remaining location task/worker capacity after ongoing usage
        for loc in self.location_pool.get_all_location_ids():
            for h in hours:
                capacity = self.location_pool.get_capacity(loc, h)
                ongoing_tasks = self._get_tasks_at_location(loc, h)
                loc_tasks_rem[loc][h] = max(0, capacity['max_tasks'] - ongoing_tasks)

                max_workers = capacity.get('max_workers', None)
                if max_workers is None:
                    loc_workers_rem[loc][h] = None
                else:
                    ongoing_workers = self._get_workers_at_location(loc, h)
                    loc_workers_rem[loc][h] = max(0, max_workers - ongoing_workers)

        return res_rem, eq_rem, loc_tasks_rem, loc_workers_rem

    def _fits_with_tentative(self, activity, start_time, res_rem, eq_rem, loc_tasks_rem, loc_workers_rem) -> bool:
        """
        Check feasibility against remaining capacity snapshots, including tentative picks.
        """
        eff = self._effective_duration(activity)
        end_time = start_time + timedelta(hours=eff)

        # Resources
        for h in self._iter_hours(start_time, end_time):
            for req in activity.getRequiredResources():
                skill, need = req['skill_type'], req['crew_count']
                if res_rem[skill].get(h, 0) < need:
                    return False

        # Equipment
        for h in self._iter_hours(start_time, end_time):
            for eq in activity.getRequiredEquipment():
                eq_id, need = eq['equipment_id'], eq['quantity_needed']
                if eq_rem[eq_id].get(h, 0) < need:
                    return False

        # Location constraints
        loc_id = activity.getLocation()
        if loc_id:
            workers_needed = sum(req['crew_count'] for req in activity.getRequiredResources())
            for h in self._iter_hours(start_time, end_time):
                # Must have a task slot
                if loc_tasks_rem[loc_id].get(h, 0) < 1:
                    return False
                # Must have worker slot if bounded
                lw = loc_workers_rem[loc_id].get(h, None)
                if lw is not None and lw < workers_needed:
                    return False

        return True

    def _apply_tentative(self, activity, start_time, res_rem, eq_rem, loc_tasks_rem, loc_workers_rem):
        """
        Decrement remaining capacity snapshots by activity’s consumption,
        so subsequent candidates see reduced capacity.
        """
        eff = self._effective_duration(activity)
        end_time = start_time + timedelta(hours=eff)

        # Resources
        for h in self._iter_hours(start_time, end_time):
            for req in activity.getRequiredResources():
                skill, need = req['skill_type'], req['crew_count']
                res_rem[skill][h] = max(0, res_rem[skill][h] - need)

        # Equipment
        for h in self._iter_hours(start_time, end_time):
            for eq in activity.getRequiredEquipment():
                eq_id, need = eq['equipment_id'], eq['quantity_needed']
                eq_rem[eq_id][h] = max(0, eq_rem[eq_id][h] - need)

        # Location
        loc_id = activity.getLocation()
        if loc_id:
            workers_needed = sum(req['crew_count'] for req in activity.getRequiredResources())
            for h in self._iter_hours(start_time, end_time):
                # consume 1 task slot
                loc_tasks_rem[loc_id][h] = max(0, loc_tasks_rem[loc_id][h] - 1)
                # consume worker slots if bounded
                if loc_workers_rem[loc_id][h] is not None:
                    loc_workers_rem[loc_id][h] = max(0, loc_workers_rem[loc_id][h] - workers_needed)

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
            return [next(iter(candidates))]

        if choice in ('max_use_res_ranked', 'max_use_res_shuffled'):
            # Order candidates
            ordered = (self._rank_by_value(candidates)
                       if choice == 'max_use_res_ranked'
                       else self._shuffle_candidates(candidates))

            # Build capacity snapshots across needed window
            max_end = time_index
            for act in ordered:
                eff = self._effective_duration(act)
                cand_end = time_index + timedelta(hours=eff)
                if cand_end > max_end:
                    max_end = cand_end

            res_rem, eq_rem, loc_tasks_rem, loc_workers_rem = self._build_capacity_snapshots(time_index, max_end)

            selected = []
            for act in ordered:
                if self._fits_with_tentative(act, time_index, res_rem, eq_rem, loc_tasks_rem, loc_workers_rem):
                    selected.append(act)
                    self._apply_tentative(act, time_index, res_rem, eq_rem, loc_tasks_rem, loc_workers_rem)
            return selected

        elif choice == 'md_knapsack':
            # Multi-dimensional knapsack optimization (tentative),
            # then re-validate with per-hour capacity snapshots to avoid overbooking.
            optimizer = MDKnapsackScheduler(
                candidates,
                self.resource_pool,
                self.equipment_pool,
                self.location_pool,
                time_index,
                value_mode='value_based' if self.priorities else 'uniform'
            )
            tentative = optimizer.solve()

            max_end = time_index
            for act in tentative:
                eff = self._effective_duration(act)
                cand_end = time_index + timedelta(hours=eff)
                if cand_end > max_end:
                    max_end = cand_end

            res_rem, eq_rem, loc_tasks_rem, loc_workers_rem = self._build_capacity_snapshots(time_index, max_end)

            selected = []
            for act in tentative:
                if self._fits_with_tentative(act, time_index, res_rem, eq_rem, loc_tasks_rem, loc_workers_rem):
                    selected.append(act)
                    self._apply_tentative(act, time_index, res_rem, eq_rem, loc_tasks_rem, loc_workers_rem)
            return selected

        elif choice == 'look_ahead':
            # Look-ahead heuristic (tentative), then re-validate to avoid overbooking
            scheduler = LookAheadScheduler(self, look_ahead_hours=48)
            tentative = scheduler.select_activities(candidates, time_index)

            max_end = time_index
            for act in tentative:
                eff = self._effective_duration(act)
                cand_end = time_index + timedelta(hours=eff)
                if cand_end > max_end:
                    max_end = cand_end

            res_rem, eq_rem, loc_tasks_rem, loc_workers_rem = self._build_capacity_snapshots(time_index, max_end)

            selected = []
            for act in tentative:
                if self._fits_with_tentative(act, time_index, res_rem, eq_rem, loc_tasks_rem, loc_workers_rem):
                    selected.append(act)
                    self._apply_tentative(act, time_index, res_rem, eq_rem, loc_tasks_rem, loc_workers_rem)
            return selected

        else:
            raise ValueError(f"Unknown scheduling strategy: {choice}")

    def _can_schedule_activity(self, activity, start_time: datetime) -> bool:
        """
        Check if activity can be scheduled at given time.

        Checks REMAINING availability (after accounting for ongoing tasks):
        1. Resource availability for entire duration
        2. Equipment availability for entire duration
        3. Location capacity for entire duration
        """
        # Calculate activity duration and end time (clamped)
        duration_hours = self._effective_duration(activity)
        end_time = start_time + timedelta(hours=duration_hours)

        # Check resource availability for entire duration
        for res_req in activity.getRequiredResources():
            skill = res_req['skill_type']
            needed = res_req['crew_count']

            # Check minimum remaining availability over the entire duration
            min_remaining = self._get_min_remaining_resources(
                skill, start_time, end_time
            )

            if min_remaining < needed:
                logging.debug(
                    f"Activity {activity.name} blocked: "
                    f"need {needed} {skill}, only {min_remaining} remaining "
                    f"(some already allocated to ongoing tasks)"
                )
                return False

        # Check equipment availability for entire duration
        for eq_req in activity.getRequiredEquipment():
            eq_id = eq_req['equipment_id']
            needed = eq_req['quantity_needed']

            # Check minimum remaining availability over the entire duration
            min_remaining = self._get_min_remaining_equipment(
                eq_id, start_time, end_time
            )

            if min_remaining < needed:
                logging.debug(
                    f"Activity {activity.name} blocked: "
                    f"need {needed} {eq_id}, only {min_remaining} remaining "
                    f"(some already allocated to ongoing tasks)"
                )
                return False

        # Check location capacity
        location_id = activity.getLocation()
        if location_id:
            # Check if location is accessible for entire duration
            min_capacity = self._get_min_remaining_location_capacity(
                location_id, start_time, end_time
            )

            if min_capacity['max_tasks'] == 0:
                logging.debug(
                    f"Activity {activity.name} blocked: "
                    f"location {location_id} not accessible during required period"
                )
                return False

            # Check if we would exceed task capacity (based on ongoing)
            max_concurrent = 0
            current_time = start_time
            while current_time < end_time:
                concurrent = self._get_tasks_at_location(location_id, current_time)
                max_concurrent = max(max_concurrent, concurrent)
                current_time += timedelta(hours=1)

            if max_concurrent >= min_capacity['max_tasks']:
                logging.debug(
                    f"Activity {activity.name} blocked: "
                    f"location {location_id} at capacity "
                    f"({max_concurrent}/{min_capacity['max_tasks']})"
                )
                return False

            # NEW: enforce location worker capacity across duration
            if min_capacity['max_workers'] is not None:
                workers_needed = sum(req['crew_count'] for req in activity.getRequiredResources())
                current_time = start_time
                while current_time < end_time:
                    workers_now = self._get_workers_at_location(location_id, current_time)
                    if workers_now + workers_needed > min_capacity['max_workers']:
                        logging.debug(
                            f"Activity {activity.name} blocked: "
                            f"location {location_id} would exceed worker capacity at "
                            f"{current_time.strftime('%Y-%m-%d %H:%M')} "
                            f"({workers_now + workers_needed}/{min_capacity['max_workers']})"
                        )
                        return False
                    current_time += timedelta(hours=1)

        return True

    def _get_consumed_resources(self, skill_type: str, time_point: datetime) -> int:
        """
        Calculate how many workers of a skill type are in use at time_point.
        """
        in_use = 0
        for act in self.ongoing:
            start_time, end_time = act.returnAbsTimes()
            if start_time and end_time and start_time <= time_point < end_time:
                for res_req in act.getRequiredResources():
                    if res_req['skill_type'] == skill_type:
                        in_use += res_req['crew_count']
        return in_use

    def _get_remaining_resources(self, skill_type: str, time_point: datetime) -> int:
        """
        Get remaining (available) resources at time_point after ongoing consumption.
        """
        original = self.resource_pool.get_availability(skill_type, time_point)
        consumed = self._get_consumed_resources(skill_type, time_point)
        remaining = original - consumed

        logging.debug(
            f"Resources at {time_point.strftime('%Y-%m-%d %H:%M')} - "
            f"{skill_type}: original={original}, consumed={consumed}, remaining={remaining}"
        )

        return remaining


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
            print("Run RCPSP first (calculateScheduleWithResources) to compute constrained chain.")
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
                print(f"\n⤺ Skipping detailed idle analysis for {act.returnName()} "
                    f"(missing prev_end for {prev.returnName()}).")
                continue

            # Idle gap in hours
            idle_h = (st - prev_et).total_seconds() / 3600.0
            if idle_h <= tol:
                # No meaningful idle
                continue

            print("\n========================================")
            print(f"{act.returnName()} waited {idle_h:.1f}h after {prev.returnName()}")
            print(f"Idle window: [{prev_et.strftime('%Y-%m-%d %H:%M')} -> {st.strftime('%Y-%m-%d %H:%M')}]")

            # (1) Precedence gate (CPM ES vs prev_end)
            abs_es = self.startTime + timedelta(hours=self.infoDict[act]['es'])
            if abs_es > prev_et + timedelta(seconds=tol):
                gap_h = (abs_es - prev_et).total_seconds() / 3600.0
                print(f"• Precedence gate: ES not reached until {abs_es.strftime('%Y-%m-%d %H:%M')} (+{gap_h:.1f}h)")
                # List predecessors of 'act' that were not completed by prev_et
                blocking_preds = []
                for pred in self.backwardDict.get(act, []):
                    p_st, p_et = pred.returnAbsTimes()
                    # If predecessor didn’t end by the idle start, it gates
                    if p_et is None or p_et > prev_et + timedelta(seconds=tol):
                        blocking_preds.append(pred.returnName())
                if blocking_preds:
                    print(f"  Other predecessor(s) not complete by idle start: {blocking_preds}")

            # Prepare 'act' demands
            skill_demands = {req['skill_type']: req['crew_count'] for req in act.getRequiredResources()}
            eq_demands    = {req['equipment_id']: req['quantity_needed'] for req in act.getRequiredEquipment()}
            loc_id        = act.getLocation()
            need_workers  = sum(skill_demands.values())

            found_blockers = False

            # (2) Diagnostics per hour in the idle window [prev_et, st)
            for h in _hour_iter(prev_et, st):
                hour_str = h.strftime('%Y-%m-%d %H:%M')

                # (2a) Resources: remaining = availability - consumption
                for skill, need in skill_demands.items():
                    avail = self.resource_pool.get_availability(skill, h)
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
                        print(f"• {hour_str} | RESOURCE {skill}: need {need}, avail {avail}, "
                            f"consumed {consumed}, remaining {remaining} -> BLOCKED")
                        print(f"  Consumers at {hour_str}: {consumers or 'None (calendar blackout?)'}")

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
                        print(f"• {hour_str} | EQUIPMENT {eq_id}: need {need}, avail {avail}, "
                            f"consumed {consumed}, remaining {remaining} -> BLOCKED")
                        print(f"  Equipment consumers at {hour_str}: {consumers or 'None (calendar blackout?)'}")

                # (2c) Location capacity: task slots and worker slots
                if loc_id:
                    cap = self.location_pool.get_capacity(loc_id, h)
                    tasks_now = 0
                    workers_now = 0
                    loc_tasks = []
                    for og in scheduled_acts:
                        if og.getLocation() == loc_id:
                            og_st, og_et = og.returnAbsTimes()
                            if og_st <= h < og_et:
                                tasks_now += 1
                                loc_tasks.append(og.returnName())
                                # total workers at the location (all skills)
                                workers_now += sum(r['crew_count'] for r in og.getRequiredResources())

                    # Task slot deficit
                    if cap['max_tasks'] - tasks_now < 1:
                        found_blockers = True
                        print(f"• {hour_str} | LOCATION {loc_id} tasks: "
                            f"max_tasks {cap['max_tasks']}, in_use {tasks_now} -> BLOCKED")
                        print(f"  Location tasks at {hour_str}: {loc_tasks}")

                    # Worker slot deficit
                    if cap.get('max_workers') is not None and (cap['max_workers'] - workers_now) < need_workers:
                        found_blockers = True
                        print(f"• {hour_str} | LOCATION {loc_id} workers: "
                            f"max_workers {cap['max_workers']}, in_use {workers_now}, "
                            f"need {need_workers} -> BLOCKED")

            if not found_blockers:
                print("• No capacity deficits detected in idle window.")
                print("  If ES gate above isn’t the cause, this likely indicates calendar unavailability, off-hours,")
                print("  or non-modeled constraints (e.g., shift rules).")



    def explain_idle_on_chain(self):
        """
        For each activity on the constrained chain, explain why it waited.
        Lists overlapping activities that share resources during its idle period.
        """

        if not hasattr(self, 'constrained_chain_list'):
            print("Run RCPSP first.")
            return

        chain = self.constrained_chain_list
        for i, act in enumerate(chain):
            if i == 0:
                continue  # skip first
            st, et = act.returnAbsTimes()
            prev = chain[i-1]
            prev_st, prev_end = prev.returnAbsTimes()

            if st is None or prev_end is None:
                print(f"\n⤺ Skipping idle analysis between {prev.returnName()} and {act.returnName()} "
                    f"(missing times: prev_end={prev_end}, act_start={st}).")
                continue

            idle = (st - prev_end).total_seconds() / 3600.0
            if idle > 0.01:
                print(f"\n{act.returnName()} waited {idle:.1f}h after {prev.returnName()}")
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

        print("=== Chain Sets Summary ===")
        print(f"CPM Critical Path count: {len(cpm_cp)}")
        print(f"Constrained Chain count: {len(constrained_set_names)}")
        print(f"Zero-TF Actual count:    {len(zero_tf_set_names)}")
        print(f"Overlap (both):          {len(both)} -> {both[:10]}{' ...' if len(both) > 10 else ''}")
        print(f"CPM-only:                {len(only_cpm)} -> {only_cpm[:10]}{' ...' if len(only_cpm) > 10 else ''}")
        print(f"Constrained-only:        {len(only_constrained)} -> {only_constrained[:10]}{' ...' if len(only_constrained) > 10 else ''}")

    def _get_min_remaining_resources(self, skill_type: str,
                                     start_time: datetime,
                                     end_time: datetime) -> int:
        """
        Get minimum remaining resources over a time range.
        """
        min_remaining = float('inf')

        # Check each hour in the range
        current_time = start_time
        while current_time < end_time:
            remaining = self._get_remaining_resources(skill_type, current_time)
            min_remaining = min(min_remaining, remaining)
            current_time += timedelta(hours=1)

        return min_remaining if min_remaining != float('inf') else 0

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

    def _get_remaining_equipment(self, equipment_id: str, time_point: datetime) -> int:
        """
        Get remaining (available) equipment at time_point.
        """
        original = self.equipment_pool.get_availability(equipment_id, time_point)
        consumed = self._get_consumed_equipment(equipment_id, time_point)
        return original - consumed

    def _get_min_remaining_equipment(self, equipment_id: str,
                                     start_time: datetime,
                                     end_time: datetime) -> int:
        """
        Get minimum remaining equipment over a time range.
        """
        min_remaining = float('inf')

        current_time = start_time
        while current_time < end_time:
            remaining = self._get_remaining_equipment(equipment_id, current_time)
            min_remaining = min(min_remaining, remaining)
            current_time += timedelta(hours=1)

        return min_remaining if min_remaining != float('inf') else 0

    def _get_tasks_at_location(self, location_id: str, time_point: datetime) -> int:
        """
        Count how many tasks are ongoing at a location at time_point.
        """
        count = 0
        for act in self.ongoing:
            if act.getLocation() == location_id:
                start_time, end_time = act.returnAbsTimes()
                if start_time and end_time and start_time <= time_point < end_time:
                    count += 1
        return count

    def _get_workers_at_location(self, location_id: str, time_point: datetime) -> int:
        """
        Count how many workers are at a location at time_point.
        """
        total_workers = 0
        for act in self.ongoing:
            if act.getLocation() == location_id:
                start_time, end_time = act.returnAbsTimes()
                if start_time and end_time and start_time <= time_point < end_time:
                    for res_req in act.getRequiredResources():
                        total_workers += res_req['crew_count']
        return total_workers

    def _get_min_remaining_location_capacity(self, location_id: str,
                                             start_time: datetime,
                                             end_time: datetime) -> Dict:
        """
        Get minimum location capacity over a time range.
        """
        min_tasks = float('inf')
        min_workers = float('inf')

        current_time = start_time
        while current_time < end_time:
            capacity = self.location_pool.get_capacity(location_id, current_time)
            min_tasks = min(min_tasks, capacity['max_tasks'])
            if capacity['max_workers'] is not None:
                min_workers = min(min_workers, capacity['max_workers'])
            current_time += timedelta(hours=1)

        return {
            'max_tasks': min_tasks if min_tasks != float('inf') else 0,
            'max_workers': min_workers if min_workers != float('inf') else None
        }

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
                #logging.info(
                #    f"Completed: {act.name} at {time_index.strftime('%Y-%m-%d %H:%M')} "
                #    f"(duration: {max(0.0, act.duration):.1f}h, delay: {act.delay:.1f}h)"
                #)

        # Move to completed
        for act in completed_now:
            self.ongoing.remove(act)
            self.completed.append(act)

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


    def export_schedule_to_csv(self, filename: str = 'schedule.csv'):
        """
        Export schedule to CSV file.
        """
        df = self.get_schedule_dataframe()
        df.to_csv(filename, index=False)
        logging.info(f"Schedule exported to {filename}")

    def print_schedule_summary(self):
        """Print a summary of the calculated schedule."""
        if not self.completed:
            print("No schedule calculated yet.")
            return

        print("\n" + "=" * 70)
        print("SCHEDULE SUMMARY")
        print("=" * 70)

        # Overall statistics
        cpm_duration = self.getProjectDuration()
        actual_end = max(act.returnAbsTimes()[1] for act in self.forwardDict.keys() if act.returnAbsTimes()[1] is not None)
        actual_duration = (actual_end - self.startTime).total_seconds() / 3600
        total_delay = sum(act.delay for act in self.forwardDict.keys())

        print(f"\nProject Duration:")
        print(f"  CPM (no constraints): {cpm_duration:.1f} hours")
        print(f"  Actual (with constraints): {actual_duration:.1f} hours")
        print(f"  Total delay: {total_delay:.1f} hours")
        print(f"  Schedule efficiency: {(cpm_duration/actual_duration)*100:.1f}%")

        print(f"\nSchedule Timeline:")
        print(f"  Start: {self.startTime.strftime('%Y-%m-%d %H:%M')}")
        print(f"  End:   {actual_end.strftime('%Y-%m-%d %H:%M')}")

        # Activity statistics
        activities_with_delay = sum(1 for act in self.forwardDict.keys() if act.delay > 0)
        print(f"\nActivities:")
        print(f"  Total: {len(self.forwardDict)}")
        print(f"  Delayed: {activities_with_delay}")
        print(f"  On critical path: {len(self.getCriticalPath())}")

        # Top delayed activities
        delayed_acts = [
            (act.returnName(), act.delay, act.returnDescription())
            for act in self.forwardDict.keys()
            if act.delay > 0
        ]

        if delayed_acts:
            delayed_acts.sort(key=lambda x: x[1], reverse=True)
            print(f"\nTop 5 Most Delayed Activities:")
            for name, delay, desc in delayed_acts[:5]:
                print(f"  {name}: {delay:.1f}h - {desc}")

        print("=" * 70 + "\n")


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

        # --- Location binding arcs ---
        for loc_id in self.location_pool.get_all_location_ids():
            acts_loc = [(a, st, et) for (a, st, et) in scheduled if a.getLocation() == loc_id]
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

        for i in range(len(scheduled)):
            a1, st1, et1 = scheduled[i]
            for j in range(i + 1, len(scheduled)):
                a2, st2, et2 = scheduled[j]
                if not overlaps(st1, et1, st2, et2):
                    continue

                overlap_start = max(st1, st2)
                binding = False

                # Shared skills binding?
                skills1 = set(req['skill_type'] for req in a1.getRequiredResources())
                skills2 = set(req['skill_type'] for req in a2.getRequiredResources())
                for skill in skills1.intersection(skills2):
                    avail = self.resource_pool.get_availability(skill, overlap_start)
                    combined = demand_skill(a1, skill) + demand_skill(a2, skill)
                    if avail > 0 and combined >= avail:
                        binding = True
                        break

                # Shared equipment binding?
                if not binding:
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
        # Kahn topological sort
        indeg = {a: 0 for a in augmented.keys()}
        for u, succs in augmented.items():
            for v in succs:
                indeg[v] += 1
        queue = [a for a, d in indeg.items() if d == 0]
        topo = []
        while queue:
            u = queue.pop(0)
            topo.append(u)
            for v in augmented[u]:
                indeg[v] -= 1
                if indeg[v] == 0:
                    queue.append(v)

        # DP longest path
        dist = {a: 0.0 for a in augmented.keys()}
        parent = {a: None for a in augmented.keys()}

        start = self.startActivity or (topo[0] if topo else None)
        if start is None:
            return []

        dist[start] = self.infoDict[start]['duration']
        for u in topo:
            for v in augmented[u]:
                cand = dist[u] + self.infoDict[v]['duration']
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
        print("=== Connectivity & ES debug ===")
        if not self.startActivity or not self.endActivity:
            print("Missing START or END in graph.")
            return

        # Successors of START
        succ_names = [s.returnName() for s in self.forwardDict.get(self.startActivity, [])]
        print(f"START successors: {succ_names}")

        # List first-level successors with their ES
        for s in self.forwardDict.get(self.startActivity, []):
            es = self.infoDict[s]["es"]
            ef = self.infoDict[s]["ef"]
            print(f"  {s.returnName()} ES={es:.1f}h, EF={ef:.1f}h")

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
            print("Not reachable from START:", sorted(not_from_start))
        if not_to_end:
            print("Cannot reach END:", sorted(not_to_end))
        print("=== End connectivity & ES ===")

    
    def debug_candidates_and_capacity(self, hours_ahead=24):
        print("=== Candidates & capacity debug ===")
        t = self.startTime
        for k in range(hours_ahead + 1):
            time_index = t + timedelta(hours=k)
            # Candidates at this hour
            candidates = self._select_candidate_activities(
                time_index,
                'TF_based' if self.priorities is None else 'external'
            )
            cand_names = [a.returnName() for a in candidates.keys()]
            print(f"[{time_index.strftime('%Y-%m-%d %H:%M')}] candidates: {cand_names}")

            if cand_names:
                # Try feasibility check one by one
                for a in candidates.keys():
                    can = self._can_schedule_activity(a, time_index)
                    print(f"  - {a.returnName()} feasible? {can}")
            else:
                # If no candidates, show a likely gate for a few key tasks
                # (first successors of START)
                for s in self.forwardDict.get(self.startActivity, [])[:3]:
                    abs_es = self.startTime + timedelta(hours=self.infoDict[s]['es'])
                    print(f"  Note: {s.returnName()} abs ES is {abs_es.strftime('%Y-%m-%d %H:%M')}")
        print("=== End candidates & capacity ===")

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

        # Kahn's algorithm for depths
        queue = [nid for nid, d in indeg.items() if d == 0]
        for nid in queue:
            topo_depth[nid] = 0
        while queue:
            u = queue.pop(0)
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
            print(f"DAG graph saved to {filename}")
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
            print(f"DAG graph saved to {filename}")
            return fig

        else:
            raise ValueError("Unknown library. Use 'pyvis' or 'plotly'.")

# ============================================================================
# SERVICE METHODS
# ============================================================================

def _weight_function(total_float: float) -> float:
    """
    Calculate priority weight based on total float (slack).

    Activities with less slack get higher priority (closer to 1.0).
    """
    return 1.0 - 1.0 / (1.0 + math.exp(5.0 - total_float))


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
    print(f"Gantt chart saved to {filename}")
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

    if not pert.resource_pool.has_skill(resource_type):
        raise ValueError(f"Resource type '{resource_type}' not found in resource pool")

    # Get time range
    start_time = pert.startTime
    end_time = max(act.returnAbsTimes()[1] for act in pert.forwardDict.keys() if act.returnAbsTimes()[1] is not None)

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
            avail = pert.resource_pool.get_availability(resource_type, time_point)
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
        print(f"Resource utilization chart saved to {filename}")
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
    end_time = max(act.returnAbsTimes()[1] for act in pert.forwardDict.keys() if act.returnAbsTimes()[1] is not None)
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
        print(f"Location utilization chart saved to {filename}")
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
            print(f"Equipment utilization chart saved to {filename}")
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
        print(f"[{equipment_id}] Peak in-use: {peak_val} at {peak_time.strftime('%Y-%m-%d %H:%M')}")
        print(f"  Consumers at peak: {sorted(peak_consumers)}")
    else:
        print(f"[{equipment_id}] No scheduled usage detected.")

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
        print(f"Equipment utilization chart saved to {filename}")
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

    def __init__(self, candidates: Dict, resource_pool, equipment_pool,
                 location_pool, time_point: datetime, value_mode='uniform'):
        """
        Initialize MD-Knapsack optimizer.

        Args:
            candidates (dict): Candidate activities with info
            resource_pool (ResourcePool): Resource availability
            equipment_pool (EquipmentPool): Equipment availability
            location_pool (LocationPool): Location availability
            time_point (datetime): Current scheduling time
            value_mode (str): 'uniform' or 'value_based'
        """
        self.candidates = list(candidates.keys())
        self.candidate_info = candidates
        self.resource_pool = resource_pool
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
        """Get available capacity at current time point (original availability)."""
        capacities = {}

        # Resource capacities
        for skill in self.resource_pool.get_all_skills():
            avail = self.resource_pool.get_availability(skill, self.time_point)
            capacities[f'RESOURCE_{skill}'] = avail

        # Equipment capacities
        for eq_id in self.equipment_pool.get_all_equipment_ids():
            avail = self.equipment_pool.get_availability(eq_id, self.time_point)
            capacities[f'EQUIPMENT_{eq_id}'] = avail

        return capacities

    def _get_resource_consumption(self, activity) -> Dict:
        """Get resource consumption for an activity."""
        consumption = {}

        # Resources
        for res_req in activity.getRequiredResources():
            key = f'RESOURCE_{res_req["skill_type"]}'
            consumption[key] = res_req['crew_count']

        # Equipment
        for eq_req in activity.getRequiredEquipment():
            key = f'EQUIPMENT_{eq_req["equipment_id"]}'
            consumption[key] = eq_req['quantity_needed']

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
        Rank candidates by immediate value + expected future opportunities after they finish,
        then greedily select those that pass feasibility at time_point.
        """
        # Compute scores
        scored = []
        for act, info in candidates.items():
            immediate_value = info.get('value', 1.0)
            future_value = self._evaluate_future_opportunities(act, time_point)
            # You can tune the weight; 0.3 is a reasonable start
            total_score = immediate_value + 0.3 * future_value
            scored.append((act, total_score))

        # Sort by combined score descending
        scored.sort(key=lambda x: x[1], reverse=True)

        # Greedy selection with basic feasibility at current time
        selected = []
        for act, score in scored:
            if self.pert._can_schedule_activity(act, time_point):
                selected.append(act)

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
            future_score += _weight_function(slack)

        # 2) Resource pressure relief at finish time
        #    If resources are scarce at finish_time, completing this activity earlier helps open capacity
        pressure_score = 0.0
        for req in activity.getRequiredResources():
            skill = req['skill_type']
            avail = self.pert.resource_pool.get_availability(skill, finish_time)
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
