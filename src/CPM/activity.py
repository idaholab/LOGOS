import json
import copy
from datetime import timedelta


class Activity:
    """
    Base class for a single activity in nuclear outage planning.
    Represents a task with resources, equipment, location, and dependency information.
    Extended from the original development of Nofar Alfasi
    Source https://github.com/nofaralfasi/PERT-CPM-graph
    """

    def __init__(self, name, duration, res=None, childs=None,
                 location_id=None, required_resources=None, required_equipment=None,
                 is_hold_point=False, hold_point_type=None, blocks_tasks=None,
                 description=None):
        """
        Constructor for Activity.

        Args:
            name (str): Unique identifier for the task (backward compatible with task_id)
            duration (float): Planned activity duration in hours
            res (list, optional): DEPRECATED - Use required_resources instead.
                                  Legacy resource list for backward compatibility
            childs (list, optional): DEPRECATED - Use successors parameter.
                                     List of successor task names/IDs
            location_id (str, optional): Physical location where task occurs
            required_resources (list, optional): List of dicts with 'skill_type' and 'crew_count'
                                                 Format: [{'skill_type': 'MECHANIC', 'crew_count': 4}, ...]
            required_equipment (list, optional): List of dicts with 'equipment_id' and 'quantity_needed'
                                                 Format: [{'equipment_id': 'EQ_CRANE', 'quantity_needed': 1}, ...]
            is_hold_point (bool, optional): True if task is a regulatory/QA hold point
            hold_point_type (str, optional): Type of hold point ('NRC', 'QA', 'Engineering', 'Operations')
            blocks_tasks (list, optional): List of task IDs blocked by this hold point
            description (str, optional): Human-readable task description (defaults to name if not provided)
        """
        # Core identification
        self.name = str(name)  # Keep 'name' for backward compatibility
        self.description = description if description is not None else str(name)

        # Temporal properties
        self.duration = duration
        self.startTime = None
        self.endTime = None
        self.delay = 0
        # Lazy delay tracking: set when activity first enters the candidate set;
        # delay is computed once at start time rather than accumulated each step.
        self._candidate_since = None

        # Dependencies - support both old (childs) and new (successors) terminology
        if childs is None:
            self.childs = []  # Keep for backward compatibility
        else:
            self.childs = childs

        # Lag mapping: {successor_task_id: lag_hours}
        # Populated by from_json() when successors are given as dicts with a
        # 'lag_hours' field.  Plain-string successors receive zero lag.
        self.successor_lags: dict = {}

        # Location — primary single zone (backward-compatible).
        self.location_id = location_id
        # Multi-zone membership (Option C).  When non-empty, the activity must
        # simultaneously occupy all listed zones (e.g. a physical room and a
        # regulatory work-permit zone).  Populated by from_json() from the JSON
        # 'zone_ids' field; falls back to [location_id] via getZoneIds().
        self.zone_ids: list = []

        # Resources - support both old and new format
        # Old format: simple list (backward compatible)
        self.resources = res if res is not None else []

        # New format: structured resources with skill types and crew counts
        self.required_resources = required_resources if required_resources is not None else []
        # Expected format: [{'skill_type': 'MECHANIC', 'crew_count': 4}, ...]

        # Equipment requirements (new)
        self.required_equipment = required_equipment if required_equipment is not None else []
        # Expected format: [{'equipment_id': 'EQ_CRANE', 'quantity_needed': 1}, ...]

        # Consumable requirements — items permanently depleted when the activity starts.
        # Expected format: [{'item_id': 'AC_SUIT', 'quantity_needed': 4}, ...]
        # Structural data — not cleared by reset().
        self.required_consumables: list = []

        # Plant-system isolation state requirements.
        # Each entry declares a system ID and the state that system must be in
        # for this activity to run.  Activities holding the same state on a
        # system can coexist; activities requiring a different state are blocked
        # until all current holders complete.
        # Expected format: [{'system_id': 'VALVE_V1', 'required_state': 'CLOSED'}, ...]
        # Structural data — not cleared by reset().
        self.required_system_states: list = []

        # Hold point properties (new)
        self.is_hold_point = is_hold_point
        self.hold_point_type = hold_point_type
        self.blocks_tasks = blocks_tasks if blocks_tasks is not None else []

        # Mobilization lead time: hours of advance preparation required before
        # this activity can start.  When > 0, the scheduler cannot begin the
        # activity until (last_predecessor_EF + mobilization_lead_hours) has
        # elapsed, because a vendor specialist or specialist crew must be
        # called and travel to the site.
        # The lead time is baked into CPM ES during generateInfo() so every
        # downstream priority metric (slack, GRPW, etc.) already accounts for it.
        # It is NOT reset by reset() — it is structural data like duration.
        self.mobilization_lead_hours: float = 0.0

        # Radiation dose rate for consumable resource tracking.
        # Represents the dose each assigned worker accumulates per hour on this
        # task, in mRem/hour.  Zero means no dose exposure (default for most tasks).
        self.dose_rate_mrem_per_hour: float = 0.0

        # Regulatory time-window constraints (hours from outage start).
        # window_earliest_start_hours: activity cannot start before this offset.
        # window_latest_finish_hours:  activity must complete by this offset.
        # Both are None by default (no window constraint).  A negative window
        # (latest_finish < earliest_start + duration) is detected by generateInfo()
        # and flagged as an infeasible window.
        self.window_earliest_start_hours: float | None = None
        self.window_latest_finish_hours: float | None = None
        # Optional list of discrete allowed windows: [{'earliest': h, 'latest': h}, ...]
        # When non-empty, takes precedence over the single-window fields above.
        self.time_windows: list = []

        # Buffer type for CCPM proactive robustness buffering.
        # Set by insert_project_buffer() / insert_feeding_buffers() when this
        # activity is a scheduler-generated time buffer, not real work.
        # Values: 'project' (end-of-chain project buffer),
        #         'feeding' (merge-point feeding buffer), or None (real task).
        # Buffer activities consume no resources and are excluded from
        # compute_fitness() criticality metrics.
        # Structural — not cleared by reset().
        self.buffer_type: str | None = None

        # WBS group membership for aggregate priority roll-up.
        # When set, _compute_wbs_slack() uses the minimum slack across all
        # activities sharing the same wbs_group as the effective scheduling
        # priority.  This ensures that when any member of a package is on the
        # system critical path, every member is elevated simultaneously.
        # None = no WBS grouping; this activity is prioritised solely by its
        # individual CPM slack.  Structural data — not cleared by reset().
        self.wbs_group: str | None = None

        # Multi-mode support (MMRCPSP).
        # Each mode is a dict with keys:
        #   mode_id (str), duration (float),
        #   required_resources (list), required_equipment (list),
        #   and optionally dose_rate_mrem_per_hour (float),
        #                  mobilization_lead_hours (float).
        # When non-empty, set_mode(mode_id) writes the named mode's values into
        # self.duration / self.required_resources / self.required_equipment etc.
        # When empty the activity has a single implicit mode — all existing code
        # paths remain unchanged (backward compatible).
        self.modes: list = []
        self.selected_mode_id: str | None = None

        # Substitution-resolved skill breakdown set by the scheduler when an
        # activity is started.  Maps skill_type -> workers_actually_assigned.
        # None until _update_activity_sets commits the assignment.
        self._actual_resources: dict | None = None

        # Remaining duration (hours) for in-progress activities at replan time.
        # Set by _partial_reset(); used by _effective_duration() and
        # _generate_info_from() to anchor the activity's EF correctly.
        # None for activities that have never been replanned mid-execution.
        self._remaining_duration: float | None = None

        # Activity status for replanning.
        # Tracks where the activity sits in a live or simulated outage:
        #   'pending'     — not yet started
        #   'in_progress' — started but not finished
        #   'completed'   — finished
        # Set by the scheduler (_update_activity_sets / _update_ongoing_list)
        # and reset to 'pending' by reset().
        self.status: str = 'pending'

        # Critical path tracking
        self.belongsToCP = False

        # Hierarchical activity support
        self.subActivities = []

    @classmethod
    def from_json(cls, task_dict):
        """
        Factory method to create Activity from JSON task dictionary.

        Args:
            task_dict (dict): Dictionary containing task data from JSON format
                Expected keys: task_id, description, duration, successors, location_id,
                               required_resources, required_equipment, is_hold_point,
                               hold_point_type, blocks_tasks

        Returns:
            Activity: New Activity instance populated from JSON data

        Example:
            >>> task_data = {
            ...     "task_id": "T001",
            ...     "description": "Remove reactor vessel head",
            ...     "duration": 12.0,
            ...     "successors": ["T002"],
            ...     "location_id": "LOC_REACTOR_CAVITY",
            ...     "required_resources": [
            ...         {"skill_type": "MECHANIC", "crew_count": 6}
            ...     ],
            ...     "required_equipment": [
            ...         {"equipment_id": "EQ_POLAR_CRANE", "quantity_needed": 1}
            ...     ],
            ...     "is_hold_point": False
            ... }
            >>> activity = Activity.from_json(task_data)
        """
        # Parse successors: each entry is either a plain task-ID string or a dict
        # {"task_id": "T3", "lag_hours": 2.0}.  Both forms are normalised here so
        # the rest of the code only ever sees a plain list of task-ID strings in
        # self.childs, while lag information lives in self.successor_lags.
        raw_successors = task_dict.get('successors', [])
        childs_list: list = []
        lag_map: dict = {}
        for entry in raw_successors:
            if isinstance(entry, dict):
                tid = entry['task_id']
                childs_list.append(tid)
                lag_h = float(entry.get('lag_hours', 0.0))
                if lag_h != 0.0:
                    lag_map[tid] = lag_h
            else:
                childs_list.append(str(entry))

        instance = cls(
            name=task_dict['task_id'],
            duration=task_dict['duration'],
            description=task_dict.get('description'),
            childs=childs_list,
            location_id=task_dict.get('location_id'),
            required_resources=task_dict.get('required_resources', []),
            required_equipment=task_dict.get('required_equipment', []),
            is_hold_point=task_dict.get('is_hold_point', False),
            hold_point_type=task_dict.get('hold_point_type'),
            blocks_tasks=task_dict.get('blocks_tasks', [])
        )
        instance.successor_lags = lag_map
        instance.mobilization_lead_hours = float(
            task_dict.get('mobilization_lead_hours', 0.0)
        )
        instance.dose_rate_mrem_per_hour = float(
            task_dict.get('dose_rate_mrem_per_hour', 0.0)
        )
        raw_west = task_dict.get('window_earliest_start_hours')
        raw_wlf  = task_dict.get('window_latest_finish_hours')
        instance.window_earliest_start_hours = float(raw_west) if raw_west is not None else None
        instance.window_latest_finish_hours  = float(raw_wlf)  if raw_wlf  is not None else None
        raw_tw = task_dict.get('time_windows', [])
        instance.time_windows = [
            {'earliest': float(w['earliest']), 'latest': float(w['latest'])}
            for w in raw_tw
        ] if raw_tw else []
        raw_modes = task_dict.get('modes', [])
        instance.modes = list(raw_modes) if raw_modes else []
        instance.wbs_group = task_dict.get('wbs_group') or None
        instance.required_consumables    = list(task_dict.get('required_consumables', []))
        instance.required_system_states  = list(task_dict.get('required_system_states', []))
        instance.zone_ids = list(task_dict.get('zone_ids', []))
        return instance

    def to_json_dict(self):
        """
        Convert Activity to JSON-compatible dictionary format.

        Returns:
            dict: Dictionary in the standard JSON format for tasks
        """
        # Serialise successors: use plain strings when lag is 0, dicts otherwise.
        lags = getattr(self, 'successor_lags', {})
        successors_out = []
        for tid in self.childs:
            lag = lags.get(tid, 0.0)
            if lag:
                successors_out.append({'task_id': tid, 'lag_hours': lag})
            else:
                successors_out.append(tid)

        d = {
            'task_id': self.name,
            'description': self.description,
            'duration': self.duration,
            'successors': successors_out,
            'location_id': self.location_id,
            'required_resources': self.required_resources,
            'required_equipment': self.required_equipment,
            'is_hold_point': self.is_hold_point,
            'hold_point_type': self.hold_point_type,
            'blocks_tasks': self.blocks_tasks,
        }
        lead = getattr(self, 'mobilization_lead_hours', 0.0)
        if lead:
            d['mobilization_lead_hours'] = lead
        dose_rate = getattr(self, 'dose_rate_mrem_per_hour', 0.0)
        if dose_rate:
            d['dose_rate_mrem_per_hour'] = dose_rate
        west = getattr(self, 'window_earliest_start_hours', None)
        wlf  = getattr(self, 'window_latest_finish_hours',  None)
        if west is not None:
            d['window_earliest_start_hours'] = west
        if wlf is not None:
            d['window_latest_finish_hours'] = wlf
        tw = getattr(self, 'time_windows', [])
        if tw:
            d['time_windows'] = tw
        modes = getattr(self, 'modes', [])
        if modes:
            d['modes'] = modes
        wbs = getattr(self, 'wbs_group', None)
        if wbs is not None:
            d['wbs_group'] = wbs
        consumables = getattr(self, 'required_consumables', [])
        if consumables:
            d['required_consumables'] = consumables
        sys_states = getattr(self, 'required_system_states', [])
        if sys_states:
            d['required_system_states'] = sys_states
        zone_ids = getattr(self, 'zone_ids', [])
        if zone_ids:
            d['zone_ids'] = zone_ids
        return d

    def printToJson(self):
        """
        Method designed to print activity in JSON format.

        Returns:
            str: JSON string representation of the activity
        """
        return json.dumps(self.__dict__, sort_keys=True, default=str)

    def updateChilds(self, childs):
        """
        Method designed to assign the successors (children) of an activity.

        Args:
            childs (list): List of Activity objects or list of strings (task names)
        """
        self.childs = []
        for child in childs:
            if isinstance(child, str):
                self.childs.append(child)
            else:
                # Assume it's an Activity object
                self.childs.append(child.returnName())

    def addSuccessor(self, successor):
        """
        Add a single successor to this activity.

        Args:
            successor (str or Activity): Successor task ID or Activity object
        """
        if isinstance(successor, str):
            if successor not in self.childs:
                self.childs.append(successor)
        else:
            # Assume it's an Activity object
            succ_name = successor.returnName()
            if succ_name not in self.childs:
                self.childs.append(succ_name)

    def getSuccessors(self):
        """
        Get list of successor task IDs.

        Returns:
            list: List of successor task IDs
        """
        return self.childs

    def returnName(self):
        """
        Returns the name (ID) of the activity.

        Returns:
            str: Name/ID of the activity
        """
        return self.name

    def returnDescription(self):
        """
        Returns the description of the activity.

        Returns:
            str: Human-readable description of the activity
        """
        return self.description

    def returnDuration(self):
        """
        Returns the duration of the activity.

        Returns:
            float: Duration of the activity in hours
        """
        return self.duration

    def returnResources(self):
        """
        Returns the legacy resources of the activity.

        Returns:
            list: Legacy resource list (for backward compatibility)
        """
        return self.resources

    def getRequiredResources(self):
        """
        Returns the structured resource requirements.

        Returns:
            list: List of dicts with 'skill_type' and 'crew_count'
        """
        return self.required_resources

    def getRequiredEquipment(self):
        """
        Returns the equipment requirements.

        Returns:
            list: List of dicts with 'equipment_id' and 'quantity_needed'
        """
        return self.required_equipment

    def getRequiredConsumables(self):
        """
        Returns the consumable requirements.

        Returns:
            list: List of dicts with 'item_id' and 'quantity_needed'
        """
        return getattr(self, 'required_consumables', [])

    def getRequiredSystemStates(self):
        """
        Returns the plant-system isolation state requirements.

        Returns:
            list: List of dicts with 'system_id' and 'required_state'
        """
        return getattr(self, 'required_system_states', [])

    def getLocation(self):
        """
        Returns the location ID where this activity occurs.

        Returns:
            str or None: Location ID or None if no specific location
        """
        return self.location_id

    def getZoneIds(self) -> list:
        """
        Return all zone IDs this activity must occupy simultaneously.

        When ``zone_ids`` is explicitly set (Option C multi-zone), that list is
        returned as-is.  Otherwise falls back to ``[location_id]`` for full
        backward compatibility with single-location activities.

        Returns:
            list: Ordered list of zone ID strings, or ``[]`` if the activity
                  has no zone constraints at all.
        """
        if self.zone_ids:
            return list(self.zone_ids)
        if self.location_id:
            return [self.location_id]
        return []

    def isHoldPoint(self):
        """
        Check if this activity is a hold point.

        Returns:
            bool: True if this is a hold point, False otherwise
        """
        return self.is_hold_point

    def getHoldPointType(self):
        """
        Get the type of hold point.

        Returns:
            str or None: Hold point type ('NRC', 'QA', 'Engineering', 'Operations') or None
        """
        return self.hold_point_type

    def getBlockedTasks(self):
        """
        Get list of tasks blocked by this hold point.

        Returns:
            list: List of task IDs blocked by this hold point
        """
        return self.blocks_tasks

    def updateDuration(self, newDuration):
        """
        Changes the duration of the activity.

        Args:
            newDuration (float): Updated duration of the activity
        """
        self.duration = copy.deepcopy(newDuration)


    def set_mode(self, mode_id: str) -> None:
        """Apply one of the activity's pre-defined execution modes.

        Writes the named mode's ``duration``, ``required_resources``,
        ``required_equipment``, and (if present) ``dose_rate_mrem_per_hour``
        and ``mobilization_lead_hours`` into the activity's live fields.
        The caller is responsible for calling ``Pert.generateInfo()`` (or
        ``Pert.set_modes()``, which does it automatically) so that CPM values
        reflect the new duration.

        Args:
            mode_id: Identifier of the mode to activate.

        Raises:
            ValueError: If the activity has no modes defined or if mode_id is
                        not found among the defined modes.
        """
        if not self.modes:
            raise ValueError(
                f"set_mode: activity '{self.name}' has no modes defined. "
                "Add a 'modes' array to its JSON definition."
            )
        mode = next((m for m in self.modes if m['mode_id'] == mode_id), None)
        if mode is None:
            available = [m['mode_id'] for m in self.modes]
            raise ValueError(
                f"set_mode: mode '{mode_id}' not found for activity '{self.name}'. "
                f"Available modes: {available}"
            )
        self.duration             = float(mode['duration'])
        self.required_resources   = list(mode.get('required_resources', []))
        self.required_equipment   = list(mode.get('required_equipment', []))
        # Optional per-mode overrides for dose and mobilization lead
        if 'dose_rate_mrem_per_hour' in mode:
            self.dose_rate_mrem_per_hour = float(mode['dose_rate_mrem_per_hour'])
        if 'mobilization_lead_hours' in mode:
            self.mobilization_lead_hours = float(mode['mobilization_lead_hours'])
        if 'required_consumables' in mode:
            self.required_consumables = list(mode['required_consumables'])
        if 'required_system_states' in mode:
            self.required_system_states = list(mode['required_system_states'])
        self.selected_mode_id = mode_id

    def get_available_modes(self) -> list:
        """Return the list of mode IDs defined for this activity.

        Returns an empty list for single-mode (legacy) activities.
        """
        return [m['mode_id'] for m in self.modes]

    def addDelay(self, hours: float = 1.0):
        """
        Accumulate resource-wait time on this activity.

        In the event-driven scheduler the time between consecutive events is
        variable, so the caller passes the actual elapsed hours rather than a
        fixed 1-hour increment.  The default of 1.0 preserves backward
        compatibility with any code that still calls addDelay() without arguments.

        Args:
            hours (float): Elapsed hours to add to the delay accumulator.
                        Must be >= 0.
        """
        if hours < 0:
            raise ValueError(f"addDelay: hours must be >= 0, got {hours}")
        self.delay += hours

    def returnSubActivities(self):
        """
        Returns the list of sub-activities.

        Returns:
            list: List of Activity objects that are sub-activities
        """
        return self.subActivities

    def addSubActivities(self, subActivities):
        """
        Associates a list of sub-activities to this activity.
        Recalculates total duration based on sub-activities.

        Args:
            subActivities (list): List of Activity objects
        """

        self.subActivities = subActivities
        # Serial composition by default: sum planned durations
        self.duration = sum(max(0.0, act.returnDuration()) for act in subActivities)


    def setOnCP(self):
        """
        Marks this activity as being part of the critical path.
        """
        self.belongsToCP = True

    def returnCPstatus(self):
        """
        Returns whether this activity is part of the critical path.

        Returns:
            bool: True if activity is on critical path, False otherwise
        """
        return self.belongsToCP

    def setActualStartTime(self, Tin):
        """
        Set the actual start time of the activity based on scheduling calculations.

        Args:
            Tin (datetime): Start time of the activity
        """
        self.startTime = Tin
        self.endTime = Tin + timedelta(hours=self.duration)

    def returnAbsTimes(self):
        """
        Returns the start and end times of the activity.

        Returns:
            tuple: (start_time, end_time) as datetime objects
        """
        return (self.startTime, self.endTime)

    def __repr__(self):
        """
        String representation of the Activity.

        Returns:
            str: String representation showing task ID and description
        """
        return f"Activity('{self.name}': {self.description}, duration={self.duration}h)"

    def __str__(self):
        """
        Human-readable string representation.

        Returns:
            str: Readable string with key activity information
        """
        return f"{self.name} - {self.description} ({self.duration}h)"

    def reset(self):
        """
        Reset all scheduling state so the activity is ready for a new scheduling run.
        Must be called between successive RAVEN/RCPSP iterations to avoid stale state.

        Resets:
            - startTime / endTime: set during setActualStartTime()
            - delay: accumulated by addDelay() each time a candidate is postponed
            - belongsToCP: marked during critical path analysis

        Does NOT reset:
            - duration: may have been updated by set_durations() for this run
            - required_resources / required_equipment / childs: structural data
        """
        self.startTime = None
        self.endTime = None
        self.delay = 0.0
        self.belongsToCP = False
        self.status = 'pending'
        self._actual_resources = None
        self._remaining_duration = None
        self._candidate_since = None