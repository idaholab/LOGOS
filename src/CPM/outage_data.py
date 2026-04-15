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
                 location_pool: 'LocationPool',
                 consumable_pool: 'ConsumablePool' = None,
                 system_state_pool: 'SystemStatePool' = None):
        """
        Initialize outage data container.

        Args:
            outage_config (dict): Outage configuration (ID, dates, etc.)
            tasks (list): List of task dictionaries
            resource_pool (ResourcePool): Initialized resource pool
            equipment_pool (EquipmentPool): Initialized equipment pool
            location_pool (LocationPool): Initialized location pool
            consumable_pool (ConsumablePool, optional): Consumable inventory pool.
                Defaults to an empty ConsumablePool (no consumable constraints).
            system_state_pool (SystemStatePool, optional): Plant-system isolation
                state pool.  Defaults to an empty SystemStatePool (no state
                constraints).
        """
        self.outage_config = outage_config
        self.tasks = tasks
        self.resource_pool = resource_pool
        self.equipment_pool = equipment_pool
        self.location_pool = location_pool
        self.consumable_pool = consumable_pool if consumable_pool is not None else ConsumablePool()
        self.system_state_pool = (
            system_state_pool if system_state_pool is not None else SystemStatePool()
        )

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
        resource_pool  = ResourcePool.from_json(data['resources'])
        equipment_pool = EquipmentPool.from_json(data.get('equipment', []))
        location_pool  = LocationPool.from_json(data.get('locations', []))
        consumable_pool    = ConsumablePool.from_json(data.get('consumables', []))
        system_state_pool  = SystemStatePool.from_json(data.get('plant_systems', []))

        return cls(outage_config, tasks, resource_pool, equipment_pool,
                   location_pool, consumable_pool, system_state_pool)

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

        # Check consumable references
        all_consumables = self.consumable_pool.get_all_item_ids()
        for task in self.tasks:
            for req in task.get('required_consumables', []):
                item_id = req['item_id']
                if item_id not in all_consumables:
                    errors.append(
                        f"Task '{task['task_id']}' requires consumable '{item_id}' "
                        f"which is not in consumable pool"
                    )

        # Check equipment zone_id references (reuses all_locations defined above)
        for eq_id in self.equipment_pool.get_all_equipment_ids():
            eq_zone = self.equipment_pool.get_zone_id(eq_id)
            if eq_zone and eq_zone not in all_locations:
                errors.append(
                    f"Equipment '{eq_id}' has zone_id '{eq_zone}' "
                    f"which is not in location pool"
                )

        # Check system-state references
        for task in self.tasks:
            for req in task.get('required_system_states', []):
                sid   = req.get('system_id', '')
                state = req.get('required_state', '')
                if sid and not self.system_state_pool.has_system(sid):
                    errors.append(
                        f"Task '{task['task_id']}' references plant system '{sid}' "
                        f"which is not in plant_systems"
                    )
                elif sid and state:
                    valid = self.system_state_pool.systems[sid].get('valid_states', [])
                    if valid and state not in valid:
                        errors.append(
                            f"Task '{task['task_id']}' requires state '{state}' for "
                            f"system '{sid}' which is not in valid_states {valid}"
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
        print()
        consumable_ids = self.consumable_pool.get_all_item_ids()
        print(f"Consumables: {len(consumable_ids)} items")
        for item_id in sorted(consumable_ids):
            total = self.consumable_pool.items[item_id]
            desc  = self.consumable_pool.description[item_id]
            restocks = self.consumable_pool.restocks.get(item_id, [])
            restock_note = f" + {len(restocks)} restock(s)" if restocks else ""
            print(f"  - {item_id}: {total:.0f} units ({desc}){restock_note}")
        print()
        sys_ids = self.system_state_pool.get_all_system_ids()
        print(f"Plant Systems: {len(sys_ids)} systems")
        for sid in sorted(sys_ids):
            info  = self.system_state_pool.systems[sid]
            valid = info.get('valid_states', [])
            states_str = f" states={valid}" if valid else ""
            print(f"  - {sid}: {info['description']}{states_str}")
        print("=" * 70)

    def __repr__(self):
        return (f"OutageData('{self.outage_id}', "
                f"{len(self.tasks)} tasks, "
                f"{len(self.resource_pool.get_all_skills())} skills, "
                f"{len(self.equipment_pool.get_all_equipment_ids())} equipment, "
                f"{len(self.location_pool.get_all_location_ids())} locations, "
                f"{len(self.consumable_pool.get_all_item_ids())} consumables, "
                f"{len(self.system_state_pool.get_all_system_ids())} plant_systems)")


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


class DoseBudgetTracker:
    """
    Pool-level consumable dose budget tracker for a single skill group.

    In nuclear outages, radiation dose is a consumable resource governed by
    10 CFR 20 and plant ALARA goals: workers accumulate dose across the outage
    and may not exceed their limit regardless of available calendar time.

    This class implements pool-level tracking — the entire skill pool shares a
    single aggregate dose budget:

        total_budget_mrem = dose_budget_per_worker_mrem × peak_pool_size

    A task draws from the budget when it starts; the budget is permanent (dose
    cannot be "returned").  Per-worker identity is not tracked; that requires a
    full worker-roster model and is deferred to a future iteration.
    """

    def __init__(self, skill_type: str, total_budget_mrem: float):
        """
        Args:
            skill_type (str): The skill type this tracker covers.
            total_budget_mrem (float): Total mRem budget for the entire pool
                over the outage (= dose_budget_per_worker_mrem × max_workers).
        """
        self.skill_type = skill_type
        self.total_budget_mrem = total_budget_mrem
        self.consumed_mrem: float = 0.0

    @property
    def remaining_mrem(self) -> float:
        """Remaining dose budget in mRem."""
        return max(0.0, self.total_budget_mrem - self.consumed_mrem)

    def fits(self, dose_rate_mrem_per_hour: float,
             crew_count: int, duration_hours: float) -> bool:
        """
        Return True if this task fits within the remaining dose budget.

        A zero or negative dose rate is treated as no dose exposure (always fits).
        """
        if dose_rate_mrem_per_hour <= 0.0:
            return True
        required = dose_rate_mrem_per_hour * crew_count * duration_hours
        return self.consumed_mrem + required <= self.total_budget_mrem

    def consume(self, dose_rate_mrem_per_hour: float,
                crew_count: int, duration_hours: float) -> None:
        """Permanently record dose drawn by a starting task."""
        if dose_rate_mrem_per_hour > 0.0:
            self.consumed_mrem += dose_rate_mrem_per_hour * crew_count * duration_hours

    def reset(self) -> None:
        """Reset consumed dose to zero (called at the start of each scheduling run)."""
        self.consumed_mrem = 0.0

    def __repr__(self):
        return (
            f"DoseBudgetTracker('{self.skill_type}', "
            f"consumed={self.consumed_mrem:.1f}/{self.total_budget_mrem:.1f} mRem, "
            f"remaining={self.remaining_mrem:.1f} mRem)"
        )


class ResourceAvailability:
    """
    Represents availability periods for a single resource/skill type.

    Manages time-varying availability of a specific skill type throughout
    the outage, supporting queries for availability at any given time.
    """

    def __init__(self, skill_type: str, periods: List[Dict],
                 resource_type: str = 'renewable',
                 dose_budget_per_worker_mrem: float = 0.0):
        """
        Initialize resource availability.

        Args:
            skill_type (str): The skill/resource type (e.g., 'MECHANIC', 'I&C_TECH')
            periods (list): List of dicts with keys:
                - 'start_date' (datetime): Period start
                - 'end_date' (datetime): Period end
                - 'available_count' (int): Number of workers available
                - 'reason' (str, optional): Explanation for this period
            resource_type (str): 'renewable' (default) or 'consumable'.
                Consumable resources (e.g. radiation dose) are tracked with a
                pool-level DoseBudgetTracker; renewable resources are not.
            dose_budget_per_worker_mrem (float): Per-worker dose budget for the
                outage in mRem.  Only meaningful when resource_type='consumable'.
                The total pool budget is this value × peak available_count.
        """
        self.skill_type = skill_type
        self.resource_type = resource_type
        self.dose_budget_per_worker_mrem = dose_budget_per_worker_mrem
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

    def __init__(self, equipment_id: str, description: str, periods: List[Dict],
                 zone_id: Optional[str] = None):
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
            zone_id (str, optional): Location zone this equipment is permanently
                assigned to.  When set, only activities whose zone list includes
                this zone_id may use the equipment.  None means unconstrained
                (any activity may use it regardless of zone).
        """
        self.equipment_id = equipment_id
        self.description = description
        self.zone_id: Optional[str] = zone_id
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
                 periods: List[Dict], is_confined: bool = False,
                 zone_type: str = 'physical'):
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
            zone_type (str): Zone classification — 'physical' (default) or 'permit'.
                Permit zones enforce task/worker density limits like physical zones
                but also represent regulatory work permits that must be acquired
                before any activity in that zone can start.
        """
        self.location_id = location_id
        self.description = description
        self.is_confined_space = is_confined
        self.zone_type = zone_type
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
        return f"LocationAvailability('{self.location_id}', zone_type='{self.zone_type}', {len(self.periods)} periods)"


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
            resource_type = res_data.get('resource_type', 'renewable')
            dose_budget_per_worker = float(
                res_data.get('dose_budget_per_worker_mrem', 0.0)
            )
            periods = []
            for period in res_data['availability_periods']:
                periods.append({
                    'start_date': datetime.fromisoformat(period['start_date']),
                    'end_date': datetime.fromisoformat(period['end_date']),
                    'available_count': period['available_count'],
                    'reason': period.get('reason', '')
                })
            pool.resources[skill] = ResourceAvailability(
                skill, periods,
                resource_type=resource_type,
                dose_budget_per_worker_mrem=dose_budget_per_worker,
            )
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

    def get_consumable_skills(self) -> List[str]:
        """
        Return skill types whose resource_type is 'consumable'.

        Returns:
            list: Skill type strings for consumable resources.
        """
        return [
            skill for skill, ra in self.resources.items()
            if ra.resource_type == 'consumable'
        ]

    def build_dose_trackers(self) -> Dict[str, 'DoseBudgetTracker']:
        """
        Build a DoseBudgetTracker for each consumable resource.

        The total pool budget is:
            dose_budget_per_worker_mrem × peak available_count

        Returns:
            dict: {skill_type: DoseBudgetTracker} — empty if no consumable resources.
        """
        trackers = {}
        for skill in self.get_consumable_skills():
            ra = self.resources[skill]
            peak = ra.get_max_availability()
            total_budget = ra.dose_budget_per_worker_mrem * peak
            trackers[skill] = DoseBudgetTracker(skill, total_budget)
        return trackers

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
            zone_id = eq_data.get('zone_id')  # None when absent — unconstrained
            pool.equipment[eq_id] = EquipmentAvailability(eq_id, description, periods,
                                                          zone_id=zone_id)
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

    def get_zone_id(self, equipment_id: str) -> Optional[str]:
        """
        Return the zone this equipment is assigned to, or None if unconstrained.

        Args:
            equipment_id (str): The equipment ID

        Returns:
            str or None: zone_id if the equipment is zone-locked, else None.
        """
        if equipment_id not in self.equipment:
            return None
        return self.equipment[equipment_id].zone_id

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
            zone_type = loc_data.get('zone_type', 'physical')
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
                loc_id, description, periods, is_confined, zone_type
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

    def get_zone_type(self, location_id: str) -> str:
        """
        Return the zone_type for a location ('physical' or 'permit').

        Args:
            location_id (str): The location ID to query

        Returns:
            str: zone_type string, or 'physical' if not found
        """
        if location_id not in self.locations:
            return 'physical'
        return self.locations[location_id].zone_type

    def __repr__(self):
        return f"LocationPool({len(self.locations)} locations)"


class ConsumablePool:
    """
    Tracks named consumable items whose inventory is permanently depleted
    when an activity starts (deduct-on-start contract).

    Unlike ResourcePool / EquipmentPool, there is no time-varying capacity
    grid: a scalar ``remaining[item_id]`` is maintained.  Optional mid-outage
    restock deliveries are expressed as a sorted list of
    ``(delivery_hour, quantity)`` pairs per item and applied lazily when the
    scheduler advances past a delivery hour.

    This generalises the ``DoseBudgetTracker`` pattern to arbitrary named
    items (nitrogen cylinders, anti-contamination suits, specialty seals, etc.).
    Dose tracking remains as a dedicated ``DoseBudgetTracker`` — ConsumablePool
    covers non-radiological consumables.
    """

    def __init__(self):
        self.items: Dict[str, float] = {}               # item_id -> total_quantity
        self.remaining: Dict[str, float] = {}           # item_id -> current_remaining
        self.description: Dict[str, str] = {}           # item_id -> human description
        # restocks[item_id] = sorted list of (delivery_hour, qty)
        self.restocks: Dict[str, List[Tuple[float, float]]] = {}
        # cursor: highest delivery_hour already applied, per item
        self._restock_cursor: Dict[str, float] = {}

    @classmethod
    def from_json(cls, consumables_list: List[Dict]) -> 'ConsumablePool':
        """
        Create ConsumablePool from the ``"consumables"`` JSON array.

        Args:
            consumables_list: List of dicts with keys:
                - ``item_id`` (str)
                - ``description`` (str)
                - ``total_quantity`` (float)
                - ``restocks`` (list, optional): [{delivery_hour, quantity}, ...]

        Returns:
            ConsumablePool with all items loaded.
        """
        pool = cls()
        for entry in consumables_list:
            item_id = entry['item_id']
            qty = float(entry['total_quantity'])
            pool.items[item_id] = qty
            pool.remaining[item_id] = qty
            pool.description[item_id] = entry.get('description', item_id)
            pool._restock_cursor[item_id] = -1.0  # nothing applied yet
            raw_restocks = entry.get('restocks', [])
            pool.restocks[item_id] = sorted(
                [(float(r['delivery_hour']), float(r['quantity'])) for r in raw_restocks],
                key=lambda x: x[0],
            )
        return pool

    # ------------------------------------------------------------------
    # Query / consume
    # ------------------------------------------------------------------

    def has_item(self, item_id: str) -> bool:
        """Return True if item_id is registered in this pool."""
        return item_id in self.items

    def get_all_item_ids(self) -> List[str]:
        """Return all registered item IDs."""
        return list(self.items.keys())

    def get_remaining(self, item_id: str) -> float:
        """Return current remaining quantity for item_id (0.0 if unknown)."""
        return self.remaining.get(item_id, 0.0)

    def fits(self, item_id: str, qty: float, at_hour: float = None) -> bool:
        """
        Return True if ``qty`` units of ``item_id`` are available.

        Args:
            item_id: Consumable identifier.
            qty: Quantity needed.
            at_hour: Outage-offset hour at which the check is made.
                     If supplied, pending restock deliveries up to this
                     hour are applied before the comparison.

        Returns:
            True when the pool has sufficient remaining inventory, or when
            ``item_id`` is not registered (permissive default — unknown items
            are not constrained).
        """
        if item_id not in self.items:
            return True     # unknown item: not constrained
        if at_hour is not None:
            self.apply_restocks_up_to(at_hour)
        return self.remaining[item_id] >= qty

    def consume(self, item_id: str, qty: float) -> None:
        """
        Permanently deduct ``qty`` units of ``item_id``.

        Silently ignores unknown item IDs so that callers need not pre-check.
        Remaining is floored at 0 — it will never go negative.
        """
        if item_id in self.remaining:
            self.remaining[item_id] = max(0.0, self.remaining[item_id] - qty)

    # ------------------------------------------------------------------
    # Restock management
    # ------------------------------------------------------------------

    def apply_restocks_up_to(self, hour: float) -> None:
        """
        Apply all pending restock deliveries with ``delivery_hour <= hour``.

        Idempotent: calling twice with the same hour applies each delivery
        at most once (tracked via ``_restock_cursor``).
        """
        for item_id, deliveries in self.restocks.items():
            cursor = self._restock_cursor.get(item_id, -1.0)
            for delivery_hour, qty in deliveries:
                if delivery_hour <= hour and delivery_hour > cursor:
                    self.remaining[item_id] = self.remaining.get(item_id, 0.0) + qty
                    cursor = delivery_hour
            self._restock_cursor[item_id] = max(cursor,
                                                self._restock_cursor.get(item_id, -1.0))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """
        Restore all remaining quantities to their initial totals and reset
        the restock cursor.  Called at the start of each scheduling run and
        at the top of ``_partial_reset`` before replaying frozen activities.
        """
        for item_id, total in self.items.items():
            self.remaining[item_id] = total
            self._restock_cursor[item_id] = -1.0

    def __repr__(self):
        parts = [f"{k}: {self.remaining[k]:.1f}/{self.items[k]:.1f}"
                 for k in self.items]
        return f"ConsumablePool({{{', '.join(parts)}}})"


class SystemStatePool:
    """
    Tracks mutual-exclusion-by-state constraints for shared plant systems.

    A shared plant system (valve, pump, circuit breaker, temporary power drop,
    compressed air manifold, etc.) can be held by multiple concurrent activities
    **only if they all require the same state**.  Any activity requiring a
    different state is blocked until all holders of the current state complete.

    This is a shared-state lock (analogous to a read-write lock where all
    "readers" must be in the same mode):

    * ``fits('VALVE_V1', 'CLOSED')`` — True when V1 is free *or* already held
      in state ``'CLOSED'``; False when V1 is held in any other state.
    * Multiple concurrent activities requiring the same state all succeed
      (reference count > 1 for that state).
    * ``release`` decrements the count; the system becomes free when it
      reaches zero.

    Scheduling contract
    -------------------
    * ``_fits_with_tentative`` — check feasibility (read-only)
    * ``_apply_tentative``    — ``acquire()`` when a candidate is committed
      during the greedy selection loop; this ensures later candidates in the
      same time-step see the lock.
    * ``_update_ongoing_list`` — ``release()`` when an activity completes.
    * ``reset()``              — called at the start of each scheduling run
      and at the top of ``_partial_reset`` before re-acquiring for
      in-progress activities.

    Relationship to EquipmentPool / UtilityConnections
    ---------------------------------------------------
    For utility connections (power drops, compressed air manifolds), two
    orthogonal constraints apply:

    * **Count** — how many ports / units are physically available
      → modelled by ``EquipmentPool``
    * **Isolation state** — whether the connection must be ENERGIZED,
      DE-ENERGIZED, PRESSURIZED, DRAINED, etc.
      → modelled by ``SystemStatePool``

    A task that uses a power drop needs both a port (EquipmentPool) and
    the correct isolation state (SystemStatePool).
    """

    def __init__(self):
        # {system_id: {'description': str, 'valid_states': [str]}}
        self.systems: Dict[str, Dict] = {}
        # Reference counts: {system_id: {state: int}}
        # Absent key means the system is free (no holders).
        self._held: Dict[str, Dict[str, int]] = {}

    @classmethod
    def from_json(cls, plant_systems_list: List[Dict]) -> 'SystemStatePool':
        """
        Create SystemStatePool from the ``"plant_systems"`` JSON array.

        Args:
            plant_systems_list: List of dicts with keys:
                - ``system_id``   (str)
                - ``description`` (str)
                - ``valid_states`` (list of str, optional but recommended)

        Returns:
            SystemStatePool with all systems registered.
        """
        pool = cls()
        for entry in plant_systems_list:
            sid = entry['system_id']
            pool.systems[sid] = {
                'description':  entry.get('description', sid),
                'valid_states': list(entry.get('valid_states', [])),
            }
        return pool

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def has_system(self, system_id: str) -> bool:
        """Return True if system_id is registered in this pool."""
        return system_id in self.systems

    def get_all_system_ids(self) -> List[str]:
        """Return all registered system IDs."""
        return list(self.systems.keys())

    def get_held_state(self, system_id: str) -> Optional[str]:
        """
        Return the state currently held for system_id, or None if free.

        If multiple activities hold the same state the value is still that
        single state (all holders agree by invariant).
        """
        states = self._held.get(system_id, {})
        if not states:
            return None
        # By invariant only one state can be non-zero at a time.
        return next(iter(states))

    def fits(self, system_id: str, required_state: str) -> bool:
        """
        Return True if the candidate activity can start at the current time.

        Args:
            system_id:      Plant system identifier.
            required_state: State the activity needs.

        Returns:
            True when the system is free **or** already held in
            ``required_state`` (compatible shared lock).
            False when a different state is currently held.
            True (permissive) when ``system_id`` is not registered — unknown
            systems impose no constraint.
        """
        if system_id not in self.systems:
            return True     # unknown system: not constrained
        states = self._held.get(system_id, {})
        if not states:
            return True     # free — any state is allowed
        return required_state in states   # same state → compatible

    # ------------------------------------------------------------------
    # Acquire / release
    # ------------------------------------------------------------------

    def acquire(self, system_id: str, required_state: str) -> None:
        """
        Increment the reference count for ``(system_id, required_state)``.

        Called in ``_apply_tentative`` when a candidate activity is
        committed during the greedy selection loop.

        Args:
            system_id:      Plant system identifier.
            required_state: State being held by the starting activity.
        """
        if system_id not in self._held:
            self._held[system_id] = {}
        self._held[system_id][required_state] = (
            self._held[system_id].get(required_state, 0) + 1
        )

    def release(self, system_id: str, required_state: str) -> None:
        """
        Decrement the reference count for ``(system_id, required_state)``.

        Removes the entry when the count reaches zero so the system
        becomes free again.

        Called in ``_update_ongoing_list`` when an activity completes.

        Args:
            system_id:      Plant system identifier.
            required_state: State being released by the finishing activity.
        """
        if system_id not in self._held:
            return
        if required_state not in self._held[system_id]:
            return
        self._held[system_id][required_state] -= 1
        if self._held[system_id][required_state] <= 0:
            del self._held[system_id][required_state]
        if not self._held[system_id]:
            del self._held[system_id]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """
        Clear all held-state reference counts.

        Called at the start of each scheduling run and at the top of
        ``_partial_reset`` before re-acquiring for in-progress activities.
        """
        self._held.clear()

    def __repr__(self):
        held_str = ', '.join(
            f"{sid}={list(states.keys())}"
            for sid, states in self._held.items()
        ) or 'all free'
        return f"SystemStatePool({len(self.systems)} systems | {held_str})"


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