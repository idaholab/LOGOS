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

        # Dependencies - support both old (childs) and new (successors) terminology
        if childs is None:
            self.childs = []  # Keep for backward compatibility
        else:
            self.childs = childs

        # Location
        self.location_id = location_id

        # Resources - support both old and new format
        # Old format: simple list (backward compatible)
        self.resources = res if res is not None else []

        # New format: structured resources with skill types and crew counts
        self.required_resources = required_resources if required_resources is not None else []
        # Expected format: [{'skill_type': 'MECHANIC', 'crew_count': 4}, ...]

        # Equipment requirements (new)
        self.required_equipment = required_equipment if required_equipment is not None else []
        # Expected format: [{'equipment_id': 'EQ_CRANE', 'quantity_needed': 1}, ...]

        # Hold point properties (new)
        self.is_hold_point = is_hold_point
        self.hold_point_type = hold_point_type
        self.blocks_tasks = blocks_tasks if blocks_tasks is not None else []

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
        return cls(
            name=task_dict['task_id'],
            duration=task_dict['duration'],
            description=task_dict.get('description'),
            childs=task_dict.get('successors', []),
            location_id=task_dict.get('location_id'),
            required_resources=task_dict.get('required_resources', []),
            required_equipment=task_dict.get('required_equipment', []),
            is_hold_point=task_dict.get('is_hold_point', False),
            hold_point_type=task_dict.get('hold_point_type'),
            blocks_tasks=task_dict.get('blocks_tasks', [])
        )

    def to_json_dict(self):
        """
        Convert Activity to JSON-compatible dictionary format.

        Returns:
            dict: Dictionary in the standard JSON format for tasks
        """
        return {
            'task_id': self.name,
            'description': self.description,
            'duration': self.duration,
            'successors': self.childs,
            'location_id': self.location_id,
            'required_resources': self.required_resources,
            'required_equipment': self.required_equipment,
            'is_hold_point': self.is_hold_point,
            'hold_point_type': self.hold_point_type,
            'blocks_tasks': self.blocks_tasks
        }

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

    def getLocation(self):
        """
        Returns the location ID where this activity occurs.

        Returns:
            str or None: Location ID or None if no specific location
        """
        return self.location_id

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