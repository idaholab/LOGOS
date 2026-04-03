"""
Availability Management Classes for Outage Planning

This module provides classes to manage time-based availability of resources,
equipment, and locations throughout a nuclear outage.
"""

import json
from datetime import datetime
from typing import List, Dict, Optional, Tuple


class OutageData:
    """
    Container class for all outage planning data.

    Provides a single point of access to all outage information including
    tasks, resources, equipment, and locations.
    """

    def __init__(self, outage_config: Dict, tasks: List[Dict],
                 resource_pool: 'ResourcePool', equipment_pool: 'EquipmentPool',
                 location_pool: 'LocationPool'):
        """
        Initialize outage data container.

        Args:
            outage_config (dict): Outage configuration (ID, dates, etc.)
            tasks (list): List of task dictionaries
            resource_pool (ResourcePool): Initialized resource pool
            equipment_pool (EquipmentPool): Initialized equipment pool
            location_pool (LocationPool): Initialized location pool
        """
        self.outage_config = outage_config
        self.tasks = tasks
        self.resource_pool = resource_pool
        self.equipment_pool = equipment_pool
        self.location_pool = location_pool

        # Parse outage dates
        self.outage_id = outage_config['outage_id']
        self.start_date = datetime.fromisoformat(outage_config['start_date'] + 'T00:00:00')
        self.target_end_date = None
        if outage_config.get('target_end_date'):
            self.target_end_date = datetime.fromisoformat(
                outage_config['target_end_date'] + 'T23:59:59'
            )
        self.working_hours_per_day = outage_config['working_hours_per_day']

    @classmethod
    def from_json_file(cls, filepath: str) -> 'OutageData':
        """
        Load outage data from JSON file.

        Args:
            filepath (str): Path to JSON file containing outage data

        Returns:
            OutageData: Initialized outage data object with all pools

        Raises:
            FileNotFoundError: If file doesn't exist
            json.JSONDecodeError: If file is not valid JSON
            KeyError: If required fields are missing

        Example:
            >>> outage = OutageData.from_json_file('example_30.json')
            >>> print(outage.outage_id)
            'RFO_2025_SPRING'
            >>> print(outage.resource_pool.get_availability('MECHANIC', outage.start_date))
            20
        """
        with open(filepath, 'r') as f:
            data = json.load(f)

        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict) -> 'OutageData':
        """
        Load outage data from dictionary.

        Args:
            data (dict): Dictionary containing outage data (parsed JSON)

        Returns:
            OutageData: Initialized outage data object with all pools

        Raises:
            KeyError: If required fields are missing

        Example:
            >>> import json
            >>> with open('example_30.json', 'r') as f:
            ...     data = json.load(f)
            >>> outage = OutageData.from_dict(data)
        """
        # Extract main sections
        outage_config = data['outage']
        tasks = data['tasks']

        # Create pools from JSON data
        resource_pool = ResourcePool.from_json(data['resources'])
        equipment_pool = EquipmentPool.from_json(data.get('equipment', []))
        location_pool = LocationPool.from_json(data.get('locations', []))

        return cls(outage_config, tasks, resource_pool, equipment_pool, location_pool)

    def get_task_by_id(self, task_id: str) -> Optional[Dict]:
        """
        Get task dictionary by ID.

        Args:
            task_id (str): Task ID to find

        Returns:
            dict or None: Task dictionary if found, None otherwise
        """
        for task in self.tasks:
            if task['task_id'] == task_id:
                return task
        return None

    def get_all_task_ids(self) -> List[str]:
        """
        Get list of all task IDs.

        Returns:
            list: List of task ID strings
        """
        return [task['task_id'] for task in self.tasks]

    def get_tasks_by_location(self, location_id: str) -> List[Dict]:
        """
        Get all tasks that occur at a specific location.

        Args:
            location_id (str): Location ID to filter by

        Returns:
            list: List of task dictionaries at that location
        """
        return [task for task in self.tasks if task.get('location_id') == location_id]

    def get_hold_point_tasks(self) -> List[Dict]:
        """
        Get all tasks that are hold points.

        Returns:
            list: List of hold point task dictionaries
        """
        return [task for task in self.tasks if task.get('is_hold_point', False)]

    def validate_data_consistency(self) -> Tuple[bool, List[str]]:
        """
        Perform basic validation on loaded data.

        Checks:
        - All task resource requirements reference existing skills
        - All task equipment requirements reference existing equipment
        - All task locations reference existing locations

        Returns:
            tuple: (is_valid, list_of_errors)
        """
        errors = []

        # Check resource references
        all_skills = self.resource_pool.get_all_skills()
        for task in self.tasks:
            for res_req in task.get('required_resources', []):
                skill = res_req['skill_type']
                if skill not in all_skills:
                    errors.append(
                        f"Task '{task['task_id']}' requires skill '{skill}' "
                        f"which is not in resource pool"
                    )

        # Check equipment references
        all_equipment = self.equipment_pool.get_all_equipment_ids()
        for task in self.tasks:
            for eq_req in task.get('required_equipment', []):
                eq_id = eq_req['equipment_id']
                if eq_id not in all_equipment:
                    errors.append(
                        f"Task '{task['task_id']}' requires equipment '{eq_id}' "
                        f"which is not in equipment pool"
                    )

        # Check location references
        all_locations = self.location_pool.get_all_location_ids()
        for task in self.tasks:
            loc_id = task.get('location_id')
            if loc_id and loc_id not in all_locations:
                errors.append(
                    f"Task '{task['task_id']}' references location '{loc_id}' "
                    f"which is not in location pool"
                )

        return len(errors) == 0, errors

    def print_summary(self):
        """Print a summary of the loaded outage data."""
        print("=" * 70)
        print(f"OUTAGE DATA SUMMARY: {self.outage_id}")
        print("=" * 70)
        print(f"Start Date: {self.start_date.strftime('%Y-%m-%d')}")
        if self.target_end_date:
            print(f"Target End Date: {self.target_end_date.strftime('%Y-%m-%d')}")
        print(f"Working Hours/Day: {self.working_hours_per_day}")
        print()
        print(f"Tasks: {len(self.tasks)}")
        print(f"  - Hold Points: {len(self.get_hold_point_tasks())}")
        print()
        print(f"Resources: {len(self.resource_pool.get_all_skills())} skill types")
        for skill in sorted(self.resource_pool.get_all_skills()):
            max_avail = self.resource_pool.resources[skill].get_max_availability()
            print(f"  - {skill}: max {max_avail} workers")
        print()
        print(f"Equipment: {len(self.equipment_pool.get_all_equipment_ids())} types")
        for eq_id in sorted(self.equipment_pool.get_all_equipment_ids()):
            max_avail = self.equipment_pool.equipment[eq_id].get_max_availability()
            desc = self.equipment_pool.equipment[eq_id].description
            print(f"  - {eq_id}: max {max_avail} units ({desc})")
        print()
        print(f"Locations: {len(self.location_pool.get_all_location_ids())}")
        for loc_id in sorted(self.location_pool.get_all_location_ids()):
            loc = self.location_pool.locations[loc_id]
            confined = " [CONFINED]" if loc.is_confined_space else ""
            print(f"  - {loc_id}: {loc.description}{confined}")
        print("=" * 70)

    def __repr__(self):
        return (f"OutageData('{self.outage_id}', "
                f"{len(self.tasks)} tasks, "
                f"{len(self.resource_pool.get_all_skills())} skills, "
                f"{len(self.equipment_pool.get_all_equipment_ids())} equipment, "
                f"{len(self.location_pool.get_all_location_ids())} locations)")


def load_outage_data(filepath: str) -> OutageData:
    """
    Convenience function to load outage data from JSON file.

    This is the main entry point for loading outage planning data.
    It reads a JSON file and creates all necessary data structures
    including resource, equipment, and location pools.

    Args:
        filepath (str): Path to JSON file containing outage data

    Returns:
        OutageData: Complete outage data object with all pools initialized

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If file is not valid JSON
        KeyError: If required fields are missing
        ValueError: If data validation fails (overlapping periods, etc.)

    Example:
        >>> # Simple usage
        >>> outage = load_outage_data('example_30.json')
        >>>
        >>> # Access different components
        >>> print(f"Outage ID: {outage.outage_id}")
        >>> print(f"Number of tasks: {len(outage.tasks)}")
        >>>
        >>> # Query resource availability
        >>> from datetime import datetime
        >>> time = datetime(2025, 3, 22, 10, 0, 0)
        >>> mechanics = outage.resource_pool.get_availability('MECHANIC', time)
        >>> print(f"Mechanics available: {mechanics}")
        >>>
        >>> # Check location capacity
        >>> capacity = outage.location_pool.get_capacity('LOC_REACTOR_CAVITY', time)
        >>> print(f"Reactor cavity capacity: {capacity}")
        >>>
        >>> # Validate data consistency
        >>> is_valid, errors = outage.validate_data_consistency()
        >>> if not is_valid:
        ...     print("Errors found:")
        ...     for error in errors:
        ...         print(f"  - {error}")
    """
    return OutageData.from_json_file(filepath)


class ResourceAvailability:
    """
    Represents availability periods for a single resource/skill type.

    Manages time-varying availability of a specific skill type throughout
    the outage, supporting queries for availability at any given time.
    """

    def __init__(self, skill_type: str, periods: List[Dict]):
        """
        Initialize resource availability.

        Args:
            skill_type (str): The skill/resource type (e.g., 'MECHANIC', 'I&C_TECH')
            periods (list): List of dicts with keys:
                - 'start_date' (datetime): Period start
                - 'end_date' (datetime): Period end
                - 'available_count' (int): Number of workers available
                - 'reason' (str, optional): Explanation for this period
        """
        self.skill_type = skill_type
        # Store periods sorted by start time for efficient querying
        self.periods = sorted(periods, key=lambda p: p['start_date'])
        self._validate_periods()

    def _validate_periods(self):
        """
        Validate that periods don't have gaps or overlaps.

        Raises:
            ValueError: If periods have gaps or overlaps
        """

        for i in range(len(self.periods) - 1):
            current_end = self.periods[i]['end_date']
            next_start  = self.periods[i + 1]['start_date']

            # Check for gaps (optional - you may allow gaps)
            # Uncomment if you want to enforce continuous coverage
            # if current_end < next_start:
            #     raise ValueError(
            #         f"Gap detected in {self.skill_type} availability "
            #         f"between {current_end} and {next_start}"
            #     )

            # Check for overlaps
            if current_end > next_start:  # strict '>'
                raise ValueError(
                    f"Overlap detected in {self.skill_type} availability "
                    f"between {current_end} and {next_start}"
                )


    def get_availability_at(self, timestamp: datetime) -> int:
        """
        Get available count at a specific timestamp.

        Args:
            timestamp (datetime): The time to query

        Returns:
            int: Number of workers available at that time (0 if unavailable)
        """
        for period in self.periods:
            if period['start_date'] <= timestamp < period['end_date']:
                return period['available_count']
        return 0  # Not available at this time

    def get_availability_in_range(self, start: datetime, end: datetime) -> int:
        """
        Get minimum availability within a time range.

        This is useful for checking if a task can be scheduled - it needs
        the minimum availability throughout its duration.

        Args:
            start (datetime): Range start
            end (datetime): Range end

        Returns:
            int: Minimum availability during the range
        """
        min_availability = float('inf')

        for period in self.periods:
            # overlap if [ps, pe) intersects [start, end)
            ps, pe = period['start_date'], period['end_date']
            if ps < end and start < pe:
                min_availability = min(min_availability, period['available_count'])
        return min_availability if min_availability != float('inf') else 0

    def get_max_availability(self) -> int:
        """
        Get maximum availability across all periods.

        Returns:
            int: Maximum number of workers available at any time
        """
        return max((p['available_count'] for p in self.periods), default=0)

    def get_periods_in_range(self, start: datetime, end: datetime) -> List[Dict]:
        """
        Get all periods that overlap with given time range.

        Args:
            start (datetime): Range start
            end (datetime): Range end

        Returns:
            list: List of period dictionaries that overlap the range
        """
        return [p for p in self.periods
                if p['start_date'] < end and start < p['end_date']]


    def get_all_periods(self) -> List[Dict]:
        """
        Get all availability periods.

        Returns:
            list: List of all period dictionaries
        """
        return self.periods.copy()

    def __repr__(self):
        return f"ResourceAvailability('{self.skill_type}', {len(self.periods)} periods)"


class EquipmentAvailability:
    """
    Represents availability periods for a single equipment type.

    Similar to ResourceAvailability but for equipment/tools.
    """

    def __init__(self, equipment_id: str, description: str, periods: List[Dict]):
        """
        Initialize equipment availability.

        Args:
            equipment_id (str): Unique equipment identifier
            description (str): Human-readable description
            periods (list): List of dicts with keys:
                - 'start_date' (datetime): Period start
                - 'end_date' (datetime): Period end
                - 'quantity_available' (int): Number of units available
                - 'reason' (str, optional): Explanation for this period
        """
        self.equipment_id = equipment_id
        self.description = description
        # Store periods sorted by start time
        self.periods = sorted(periods, key=lambda p: p['start_date'])
        self._validate_periods()

    def _validate_periods(self):
        """Validate that periods don't overlap."""
        for i in range(len(self.periods) - 1):
            current_end = self.periods[i]['end_date']
            next_start  = self.periods[i + 1]['start_date']
            if current_end > next_start:  # allow adjacency
                raise ValueError(
                    f"Overlap detected in {self.equipment_id} availability "
                    f"between {current_end} and {next_start}"
                )


    def get_availability_at(self, timestamp: datetime) -> int:
        """
        Get available quantity at a specific timestamp.

        Args:
            timestamp (datetime): The time to query

        Returns:
            int: Number of units available at that time (0 if unavailable)
        """
        for period in self.periods:
            if period['start_date'] <= timestamp < period['end_date']:  # half-open
                return period['quantity_available']
        return 0


    def get_availability_in_range(self, start: datetime, end: datetime) -> int:
        """
        Get minimum availability within a time range.

        Args:
            start (datetime): Range start
            end (datetime): Range end

        Returns:
            int: Minimum quantity available during the range
        """

        min_availability = float('inf')
        for period in self.periods:
            ps, pe = period['start_date'], period['end_date']
            if ps < end and start < pe:  # half-open overlap
                min_availability = min(min_availability, period['quantity_available'])
        return min_availability if min_availability != float('inf') else 0


    def get_max_availability(self) -> int:
        """Get maximum availability across all periods."""
        return max((p['quantity_available'] for p in self.periods), default=0)

    def get_periods_in_range(self, start: datetime, end: datetime) -> List[Dict]:
        """Get all periods that overlap with given time range."""
        return [p for p in self.periods
                if p['start_date'] < end and start < p['end_date']]

    def get_all_periods(self) -> List[Dict]:
        """Get all availability periods."""
        return self.periods.copy()

    def __repr__(self):
        return f"EquipmentAvailability('{self.equipment_id}', {len(self.periods)} periods)"


class LocationAvailability:
    """
    Represents availability periods for a physical location.

    Manages time-varying capacity constraints for a specific location.
    """

    def __init__(self, location_id: str, description: str,
                 periods: List[Dict], is_confined: bool = False):
        """
        Initialize location availability.

        Args:
            location_id (str): Unique location identifier
            description (str): Human-readable description
            periods (list): List of dicts with keys:
                - 'start_date' (datetime): Period start
                - 'end_date' (datetime): Period end
                - 'max_concurrent_tasks' (int): Max simultaneous tasks
                - 'max_concurrent_workers' (int, optional): Max simultaneous workers
                - 'reason' (str, optional): Explanation for this period
            is_confined (bool): Whether this is a confined space
        """
        self.location_id = location_id
        self.description = description
        self.is_confined_space = is_confined
        # Store periods sorted by start time
        self.periods = sorted(periods, key=lambda p: p['start_date'])
        self._validate_periods()

    def _validate_periods(self):
        """Validate that periods don't overlap."""
        for i in range(len(self.periods) - 1):
            current_end = self.periods[i]['end_date']
            next_start  = self.periods[i + 1]['start_date']
            if current_end > next_start:  # allow adjacency
                raise ValueError(
                    f"Overlap detected in {self.location_id} availability "
                    f"between {current_end} and {next_start}"
                )


    def get_capacity_at(self, timestamp: datetime) -> Dict:
        """
        Get capacity constraints at a specific timestamp.

        Args:
            timestamp (datetime): The time to query

        Returns:
            dict: Dictionary with keys:
                - 'max_tasks' (int): Maximum concurrent tasks
                - 'max_workers' (int or None): Maximum concurrent workers
        """

        for period in self.periods:
            if period['start_date'] <= timestamp < period['end_date']:
                return {
                    'max_tasks': period['max_concurrent_tasks'],
                    'max_workers': period.get('max_concurrent_workers')
                }
        return {'max_tasks': 0, 'max_workers': 0}


    def get_capacity_in_range(self, start: datetime, end: datetime) -> Dict:
        """
        Get minimum capacity within a time range.

        Args:
            start (datetime): Range start
            end (datetime): Range end

        Returns:
            dict: Dictionary with minimum 'max_tasks' and 'max_workers' during range
        """

        min_tasks   = float('inf')
        min_workers = float('inf')
        seen_workers_limit = False

        for period in self.periods:
            ps, pe = period['start_date'], period['end_date']
            if ps < end and start < pe:  # half-open overlap
                min_tasks = min(min_tasks, period['max_concurrent_tasks'])
                worker_limit = period.get('max_concurrent_workers')
                if worker_limit is not None:
                    seen_workers_limit = True
                    min_workers = min(min_workers, worker_limit)

        return {
            'max_tasks': min_tasks if min_tasks != float('inf') else 0,
            'max_workers': (min_workers if (min_workers != float('inf') and seen_workers_limit) else None)
        }


    def is_accessible_at(self, timestamp: datetime) -> bool:
        """
        Check if location is accessible at a given time.

        Args:
            timestamp (datetime): The time to query

        Returns:
            bool: True if location allows at least one task at this time
        """
        capacity = self.get_capacity_at(timestamp)
        return capacity['max_tasks'] > 0

    def get_periods_in_range(self, start: datetime, end: datetime) -> List[Dict]:
        """Get all periods that overlap with given time range."""
        return [p for p in self.periods if p['start_date'] < end and start < p['end_date']]

    def get_all_periods(self) -> List[Dict]:
        """Get all availability periods."""
        return self.periods.copy()

    def __repr__(self):
        return f"LocationAvailability('{self.location_id}', {len(self.periods)} periods)"


class ResourcePool:
    """
    Manages all resource availability throughout the outage.

    Central repository for querying workforce availability by skill type and time.
    """

    def __init__(self):
        """Initialize empty resource pool."""
        # {skill_type: ResourceAvailability}
        self.resources: Dict[str, ResourceAvailability] = {}

    @classmethod
    def from_json(cls, resources_list: List[Dict]):
        """
        Create ResourcePool from JSON data.

        Args:
            resources_list (list): List of resource dictionaries from JSON

        Returns:
            ResourcePool: Initialized resource pool

        Example:
            >>> data = {
            ...     "resources": [
            ...         {
            ...             "skill_type": "MECHANIC",
            ...             "availability_periods": [
            ...                 {
            ...                     "start_date": "2025-03-15T00:00:00",
            ...                     "end_date": "2025-04-25T23:59:59",
            ...                     "available_count": 20
            ...                 }
            ...             ]
            ...         }
            ...     ]
            ... }
            >>> pool = ResourcePool.from_json(data["resources"])
        """
        pool = cls()
        for res_data in resources_list:
            skill = res_data['skill_type']
            periods = []
            for period in res_data['availability_periods']:
                periods.append({
                    'start_date': datetime.fromisoformat(period['start_date']),
                    'end_date': datetime.fromisoformat(period['end_date']),
                    'available_count': period['available_count'],
                    'reason': period.get('reason', '')
                })
            pool.resources[skill] = ResourceAvailability(skill, periods)
        return pool

    def get_availability(self, skill_type: str, timestamp: datetime) -> int:
        """
        Get availability for a skill at a specific time.

        Args:
            skill_type (str): The skill type to query
            timestamp (datetime): The time to query

        Returns:
            int: Number of workers available (0 if skill not found or unavailable)
        """
        if skill_type not in self.resources:
            return 0
        return self.resources[skill_type].get_availability_at(timestamp)

    def get_availability_in_range(self, skill_type: str,
                                   start: datetime, end: datetime) -> int:
        """
        Get minimum availability for a skill within a time range.

        Args:
            skill_type (str): The skill type to query
            start (datetime): Range start
            end (datetime): Range end

        Returns:
            int: Minimum availability during the range
        """
        if skill_type not in self.resources:
            return 0
        return self.resources[skill_type].get_availability_in_range(start, end)

    def get_all_skills(self) -> List[str]:
        """
        Get list of all skill types in the pool.

        Returns:
            list: List of skill type strings
        """
        return list(self.resources.keys())

    def has_skill(self, skill_type: str) -> bool:
        """
        Check if a skill type exists in the pool.

        Args:
            skill_type (str): The skill type to check

        Returns:
            bool: True if skill exists in pool
        """
        return skill_type in self.resources

    def __repr__(self):
        return f"ResourcePool({len(self.resources)} skill types)"


class EquipmentPool:
    """
    Manages all equipment availability throughout the outage.

    Central repository for querying equipment availability by ID and time.
    """

    def __init__(self):
        """Initialize empty equipment pool."""
        # {equipment_id: EquipmentAvailability}
        self.equipment: Dict[str, EquipmentAvailability] = {}

    @classmethod
    def from_json(cls, equipment_list: List[Dict]):
        """
        Create EquipmentPool from JSON data.

        Args:
            equipment_list (list): List of equipment dictionaries from JSON

        Returns:
            EquipmentPool: Initialized equipment pool
        """
        pool = cls()
        for eq_data in equipment_list:
            eq_id = eq_data['equipment_id']
            description = eq_data['description']
            periods = []
            for period in eq_data['availability_periods']:
                periods.append({
                    'start_date': datetime.fromisoformat(period['start_date']),
                    'end_date': datetime.fromisoformat(period['end_date']),
                    'quantity_available': period['quantity_available'],
                    'reason': period.get('reason', '')
                })
            pool.equipment[eq_id] = EquipmentAvailability(eq_id, description, periods)
        return pool

    def get_availability(self, equipment_id: str, timestamp: datetime) -> int:
        """
        Get availability for equipment at a specific time.

        Args:
            equipment_id (str): The equipment ID to query
            timestamp (datetime): The time to query

        Returns:
            int: Number of units available (0 if not found or unavailable)
        """
        if equipment_id not in self.equipment:
            return 0
        return self.equipment[equipment_id].get_availability_at(timestamp)

    def get_availability_in_range(self, equipment_id: str,
                                   start: datetime, end: datetime) -> int:
        """
        Get minimum availability for equipment within a time range.

        Args:
            equipment_id (str): The equipment ID to query
            start (datetime): Range start
            end (datetime): Range end

        Returns:
            int: Minimum availability during the range
        """
        if equipment_id not in self.equipment:
            return 0
        return self.equipment[equipment_id].get_availability_in_range(start, end)

    def get_all_equipment_ids(self) -> List[str]:
        """
        Get list of all equipment IDs in the pool.

        Returns:
            list: List of equipment ID strings
        """
        return list(self.equipment.keys())

    def has_equipment(self, equipment_id: str) -> bool:
        """
        Check if equipment exists in the pool.

        Args:
            equipment_id (str): The equipment ID to check

        Returns:
            bool: True if equipment exists in pool
        """
        return equipment_id in self.equipment

    def get_description(self, equipment_id: str) -> Optional[str]:
        """
        Get description for equipment.

        Args:
            equipment_id (str): The equipment ID

        Returns:
            str or None: Equipment description, or None if not found
        """
        if equipment_id not in self.equipment:
            return None
        return self.equipment[equipment_id].description

    def __repr__(self):
        return f"EquipmentPool({len(self.equipment)} equipment types)"


class LocationPool:
    """
    Manages all location availability throughout the outage.

    Central repository for querying location capacity by ID and time.
    """

    def __init__(self):
        """Initialize empty location pool."""
        # {location_id: LocationAvailability}
        self.locations: Dict[str, LocationAvailability] = {}

    @classmethod
    def from_json(cls, locations_list: List[Dict]):
        """
        Create LocationPool from JSON data.

        Args:
            locations_list (list): List of location dictionaries from JSON

        Returns:
            LocationPool: Initialized location pool
        """
        pool = cls()
        for loc_data in locations_list:
            loc_id = loc_data['location_id']
            description = loc_data['description']
            is_confined = loc_data.get('is_confined_space', False)
            periods = []
            for period in loc_data['availability_periods']:
                periods.append({
                    'start_date': datetime.fromisoformat(period['start_date']),
                    'end_date': datetime.fromisoformat(period['end_date']),
                    'max_concurrent_tasks': period['max_concurrent_tasks'],
                    'max_concurrent_workers': period.get('max_concurrent_workers'),
                    'reason': period.get('reason', '')
                })
            pool.locations[loc_id] = LocationAvailability(
                loc_id, description, periods, is_confined
            )
        return pool

    def get_capacity(self, location_id: str, timestamp: datetime) -> Dict:
        """
        Get capacity constraints for a location at a specific time.

        Args:
            location_id (str): The location ID to query
            timestamp (datetime): The time to query

        Returns:
            dict: Dictionary with 'max_tasks' and 'max_workers' keys
                  Returns {max_tasks: 0, max_workers: 0} if not found
        """
        if location_id not in self.locations:
            return {'max_tasks': 0, 'max_workers': 0}
        return self.locations[location_id].get_capacity_at(timestamp)

    def get_capacity_in_range(self, location_id: str,
                              start: datetime, end: datetime) -> Dict:
        """
        Get minimum capacity for a location within a time range.

        Args:
            location_id (str): The location ID to query
            start (datetime): Range start
            end (datetime): Range end

        Returns:
            dict: Dictionary with minimum 'max_tasks' and 'max_workers'
        """
        if location_id not in self.locations:
            return {'max_tasks': 0, 'max_workers': None}
        return self.locations[location_id].get_capacity_in_range(start, end)

    def is_accessible_at(self, location_id: str, timestamp: datetime) -> bool:
        """
        Check if location is accessible at a given time.

        Args:
            location_id (str): The location ID to query
            timestamp (datetime): The time to query

        Returns:
            bool: True if location allows at least one task at this time
        """
        if location_id not in self.locations:
            return False
        return self.locations[location_id].is_accessible_at(timestamp)

    def get_all_location_ids(self) -> List[str]:
        """
        Get list of all location IDs in the pool.

        Returns:
            list: List of location ID strings
        """
        return list(self.locations.keys())

    def has_location(self, location_id: str) -> bool:
        """
        Check if location exists in the pool.

        Args:
            location_id (str): The location ID to check

        Returns:
            bool: True if location exists in pool
        """
        return location_id in self.locations

    def is_confined_space(self, location_id: str) -> bool:
        """
        Check if location is a confined space.

        Args:
            location_id (str): The location ID to check

        Returns:
            bool: True if location is confined space, False otherwise
        """
        if location_id not in self.locations:
            return False
        return self.locations[location_id].is_confined_space

    def __repr__(self):
        return f"LocationPool({len(self.locations)} locations)"

"""
#!/usr/bin/env python3
from availability_classes import load_outage_data
from datetime import datetime, timedelta

# Load data
print("Loading outage data...")
outage = load_outage_data('example_30.json')

# Print summary
outage.print_summary()

# Validate
is_valid, errors = outage.validate_data_consistency()
if not is_valid:
    print("\n⚠ Data validation errors:")
    for error in errors:
        print(f"  - {error}")
    exit(1)

print("\n✓ Data validation passed\n")

# Example: Check if we can schedule task T003 on March 18
task = outage.get_task_by_id('T003')
start_time = datetime(2025, 3, 18, 8, 0, 0)
end_time = start_time + timedelta(hours=task['duration'])

print(f"Checking if task {task['task_id']} can be scheduled at {start_time}:")
print(f"  Description: {task['description']}")
print(f"  Duration: {task['duration']} hours")

# Check resources
print("\n  Resource availability:")
for res_req in task['required_resources']:
    skill = res_req['skill_type']
    needed = res_req['crew_count']
    available = outage.resource_pool.get_availability_in_range(
        skill, start_time, end_time
    )
    status = "✓" if available >= needed else "✗"
    print(f"    {status} {skill}: need {needed}, have {available}")

# Check equipment
print("\n  Equipment availability:")
for eq_req in task['required_equipment']:
    eq_id = eq_req['equipment_id']
    needed = eq_req['quantity_needed']
    available = outage.equipment_pool.get_availability_in_range(
        eq_id, start_time, end_time
    )
    status = "✓" if available >= needed else "✗"
    desc = outage.equipment_pool.get_description(eq_id)
    print(f"    {status} {eq_id}: need {needed}, have {available} ({desc})")

# Check location
if task['location_id']:
    print("\n  Location availability:")
    loc_id = task['location_id']
    capacity = outage.location_pool.get_capacity_in_range(
        loc_id, start_time, end_time
    )
    accessible = capacity['max_tasks'] > 0
    status = "✓" if accessible else "✗"
    print(f"    {status} {loc_id}: max {capacity['max_tasks']} tasks, "
          f"{capacity['max_workers']} workers")
"""