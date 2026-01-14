#!/usr/bin/env python3
"""
Outage Planning Data Validator

This script validates nuclear outage planning JSON data files against the defined schema.
It performs both schema validation and semantic/referential integrity checks.

Usage:
    python validate_outage_data.py <input_file.json> [--schema schema.json]

Requirements:
    pip install jsonschema
"""

import json
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Set, Tuple
from datetime import datetime

try:
    from jsonschema import validate, ValidationError, Draft7Validator
except ImportError:
    print("Error: jsonschema library not found. Install it with: pip install jsonschema")
    sys.exit(1)


class OutageDataValidator:
    """Validates outage planning data for schema compliance and referential integrity."""
    
    def __init__(self, schema_path: str = None):
        """
        Initialize the outage data validator.
        
        Args:
            schema_path (str, optional): Path to custom JSON schema file.
                If None, uses default embedded schema.
        
        Attributes:
            schema (dict): The JSON schema used for validation
            errors (list): Accumulated validation errors
            warnings (list): Accumulated validation warnings
        """
        if schema_path:
            with open(schema_path, 'r') as f:
                self.schema = json.load(f)
        else:
            # Use embedded schema if no path provided
            self.schema = self._get_default_schema()
        
        self.errors = []
        self.warnings = []
    
    def _get_default_schema(self) -> Dict:
        """
        Return the default JSON schema for outage data validation.
        
        Returns:
            dict: JSON schema definition
        
        Note:
            For production use, the full schema should be loaded from an external
            file or embedded here. This is a placeholder implementation.
        """
        # For production, load from external file or embed the full schema here
        # This is a placeholder - in practice, keep schema in separate file
        return {}
    
    def validate_schema(self, data: Dict) -> bool:
        """
        Validate data against the JSON schema.
        
        Performs structural validation to ensure the data conforms to the
        expected format, types, and constraints defined in the schema.
        
        Args:
            data (dict): The outage data to validate
        
        Returns:
            bool: True if schema validation passes, False otherwise
        
        Side Effects:
            Appends errors to self.errors if validation fails
            Prints validation status to stdout
        """
        try:
            validate(instance=data, schema=self.schema)
            print("✓ Schema validation passed")
            return True
        except ValidationError as e:
            self.errors.append(f"Schema validation error: {e.message}")
            print(f"✗ Schema validation failed: {e.message}")
            return False
    
    def validate_referential_integrity(self, data: Dict) -> bool:
        """
        Validate referential integrity and semantic constraints.
        
        Checks that all references between data elements are valid and that
        the data is semantically consistent. This includes:
        - Task ID references (successors, blocked tasks)
        - Location ID references
        - Equipment ID references
        - Skill type references
        - Hold point logic consistency
        - Absence of circular dependencies
        - Uniqueness of identifiers
        
        Args:
            data (dict): The outage data to validate
        
        Returns:
            bool: True if all referential integrity checks pass, False otherwise
        
        Side Effects:
            Appends errors to self.errors for any referential integrity violations
            Appends warnings to self.warnings for potential issues
            Prints validation status to stdout
        """
        all_valid = True
        
        # Extract reference sets
        task_ids = {task['task_id'] for task in data.get('tasks', [])}
        location_ids = {loc['location_id'] for loc in data.get('locations', [])}
        equipment_ids = {eq['equipment_id'] for eq in data.get('equipment', [])}
        skill_types = {res['skill_type'] for res in data.get('resources', [])}
        
        # Validate task references
        all_valid &= self._validate_task_references(data.get('tasks', []), task_ids)
        
        # Validate location references
        all_valid &= self._validate_location_references(data.get('tasks', []), location_ids)
        
        # Validate equipment references
        all_valid &= self._validate_equipment_references(data.get('tasks', []), equipment_ids)
        
        # Validate skill type references
        all_valid &= self._validate_skill_references(data.get('tasks', []), skill_types)
        
        # Validate hold point logic
        all_valid &= self._validate_hold_points(data.get('tasks', []), task_ids)
        
        # Validate no circular dependencies
        all_valid &= self._validate_no_cycles(data.get('tasks', []))
        
        # Validate unique task IDs
        all_valid &= self._validate_unique_ids(data.get('tasks', []))
        
        # Validate resource sufficiency (warnings only)
        self._check_resource_sufficiency(data)
        
        return all_valid
    
    def _validate_task_references(self, tasks: List[Dict], task_ids: Set[str]) -> bool:
        """
        Validate that all task references point to existing tasks.
        
        Checks that all task IDs referenced in successor lists and blocks_tasks
        lists actually exist in the task collection.
        
        Args:
            tasks (list): List of task dictionaries
            task_ids (set): Set of all valid task IDs
        
        Returns:
            bool: True if all task references are valid, False otherwise
        
        Side Effects:
            Appends errors to self.errors for invalid references
            Prints validation status to stdout
        """
        valid = True
        for task in tasks:
            task_id = task['task_id']
            
            # Check successors
            for succ_id in task.get('successors', []):
                if succ_id not in task_ids:
                    self.errors.append(
                        f"Task '{task_id}' references non-existent successor '{succ_id}'"
                    )
                    valid = False
            
            # Check blocks_tasks
            for blocked_id in task.get('blocks_tasks', []):
                if blocked_id not in task_ids:
                    self.errors.append(
                        f"Task '{task_id}' references non-existent blocked task '{blocked_id}'"
                    )
                    valid = False
        
        if valid:
            print("✓ Task references are valid")
        return valid
    
    def _validate_location_references(self, tasks: List[Dict], location_ids: Set[str]) -> bool:
        """
        Validate that all location references point to defined locations.
        
        Checks that any location_id specified in a task exists in the
        locations collection.
        
        Args:
            tasks (list): List of task dictionaries
            location_ids (set): Set of all valid location IDs
        
        Returns:
            bool: True if all location references are valid, False otherwise
        
        Side Effects:
            Appends errors to self.errors for invalid references
            Prints validation status to stdout
        """
        valid = True
        for task in tasks:
            loc_id = task.get('location_id')
            if loc_id and loc_id not in location_ids:
                self.errors.append(
                    f"Task '{task['task_id']}' references non-existent location '{loc_id}'"
                )
                valid = False
        
        if valid:
            print("✓ Location references are valid")
        return valid
    
    def _validate_equipment_references(self, tasks: List[Dict], equipment_ids: Set[str]) -> bool:
        """
        Validate that all equipment references point to defined equipment.
        
        Checks that any equipment_id specified in task equipment requirements
        exists in the equipment collection.
        
        Args:
            tasks (list): List of task dictionaries
            equipment_ids (set): Set of all valid equipment IDs
        
        Returns:
            bool: True if all equipment references are valid, False otherwise
        
        Side Effects:
            Appends errors to self.errors for invalid references
            Prints validation status to stdout
        """
        valid = True
        for task in tasks:
            for eq_req in task.get('required_equipment', []):
                eq_id = eq_req['equipment_id']
                if eq_id not in equipment_ids:
                    self.errors.append(
                        f"Task '{task['task_id']}' references non-existent equipment '{eq_id}'"
                    )
                    valid = False
        
        if valid:
            print("✓ Equipment references are valid")
        return valid
    
    def _validate_skill_references(self, tasks: List[Dict], skill_types: Set[str]) -> bool:
        """
        Validate that all skill type references are defined in the resource pool.
        
        Checks that any skill_type specified in task resource requirements
        exists in the resources collection.
        
        Args:
            tasks (list): List of task dictionaries
            skill_types (set): Set of all valid skill types
        
        Returns:
            bool: True if all skill references are valid, False otherwise
        
        Side Effects:
            Appends errors to self.errors for invalid references
            Prints validation status to stdout
        """
        valid = True
        for task in tasks:
            for res_req in task.get('required_resources', []):
                skill = res_req['skill_type']
                if skill not in skill_types:
                    self.errors.append(
                        f"Task '{task['task_id']}' requires skill '{skill}' "
                        f"which is not defined in resources"
                    )
                    valid = False
        
        if valid:
            print("✓ Skill type references are valid")
        return valid
    
    def _validate_hold_points(self, tasks: List[Dict], task_ids: Set[str]) -> bool:
        """
        Validate hold point configuration and consistency.
        
        Checks that hold point flags and attributes are consistently defined:
        - Hold points should have a hold_point_type
        - Non-hold points should not have a hold_point_type
        - Only hold points should have blocks_tasks defined
        - All blocked task IDs must exist
        
        Args:
            tasks (list): List of task dictionaries
            task_ids (set): Set of all valid task IDs
        
        Returns:
            bool: True if hold point logic is valid, False otherwise
        
        Side Effects:
            Appends errors to self.errors for logic violations
            Appends warnings to self.warnings for potential issues
            Prints validation status to stdout
        """
        valid = True
        for task in tasks:
            is_hold = task.get('is_hold_point', False)
            hold_type = task.get('hold_point_type')
            blocks = task.get('blocks_tasks', [])
            
            # If is_hold_point is True, hold_point_type should not be null
            if is_hold and not hold_type:
                self.warnings.append(
                    f"Task '{task['task_id']}' is marked as hold point but has no hold_point_type"
                )
            
            # If is_hold_point is False, hold_point_type should be null
            if not is_hold and hold_type:
                self.errors.append(
                    f"Task '{task['task_id']}' has hold_point_type '{hold_type}' "
                    f"but is_hold_point is False"
                )
                valid = False
            
            # If not a hold point, blocks_tasks should be empty
            if not is_hold and blocks:
                self.errors.append(
                    f"Task '{task['task_id']}' is not a hold point but has blocks_tasks defined"
                )
                valid = False
        
        if valid:
            print("✓ Hold point logic is valid")
        return valid
    
    def _validate_no_cycles(self, tasks: List[Dict]) -> bool:
        """
        Validate that the task dependency graph contains no cycles.
        
        Uses depth-first search to detect circular dependencies in the task
        network. Circular dependencies would make scheduling impossible as
        tasks would be waiting on each other indefinitely.
        
        Both successor relationships and hold point blocking relationships
        are considered when building the dependency graph.
        
        Args:
            tasks (list): List of task dictionaries
        
        Returns:
            bool: True if no cycles exist, False if circular dependencies found
        
        Side Effects:
            Appends errors to self.errors if cycles are detected
            Prints validation status to stdout
        
        Algorithm:
            Uses DFS with recursion stack to detect back edges (cycles)
        """
        # Build adjacency list (task -> successors)
        graph = {task['task_id']: task.get('successors', []) for task in tasks}
        
        # Also add hold point dependencies
        for task in tasks:
            if task.get('is_hold_point'):
                task_id = task['task_id']
                for blocked_id in task.get('blocks_tasks', []):
                    if task_id not in graph:
                        graph[task_id] = []
                    if blocked_id not in graph[task_id]:
                        graph[task_id].append(blocked_id)
        
        # DFS-based cycle detection
        visited = set()
        rec_stack = set()
        
        def has_cycle(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            
            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    if has_cycle(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True
            
            rec_stack.remove(node)
            return False
        
        for node in graph:
            if node not in visited:
                if has_cycle(node):
                    self.errors.append(
                        f"Circular dependency detected in task network involving '{node}'"
                    )
                    print("✗ Circular dependencies found in task network")
                    return False
        
        print("✓ No circular dependencies found")
        return True
    
    def _validate_unique_ids(self, tasks: List[Dict]) -> bool:
        """
        Validate that all task IDs are unique.
        
        Duplicate task IDs would create ambiguity in the dependency graph
        and make scheduling impossible.
        
        Args:
            tasks (list): List of task dictionaries
        
        Returns:
            bool: True if all task IDs are unique, False otherwise
        
        Side Effects:
            Appends errors to self.errors listing duplicate IDs
            Prints validation status to stdout
        """
        task_ids = [task['task_id'] for task in tasks]
        if len(task_ids) != len(set(task_ids)):
            duplicates = [tid for tid in task_ids if task_ids.count(tid) > 1]
            self.errors.append(f"Duplicate task IDs found: {set(duplicates)}")
            print("✗ Duplicate task IDs found")
            return False
        
        print("✓ All task IDs are unique")
        return True
    
    def _check_resource_sufficiency(self, data: Dict):
        """
        Check if available resources are sufficient for individual tasks.
        
        Performs a preliminary check to identify tasks that require more workers
        of a particular skill than are available in the total resource pool.
        Such tasks cannot be executed and represent data errors.
        
        Note: This check uses the MAXIMUM availability across all time periods.
        It doesn't account for parallel tasks competing for resources or 
        time-specific availability constraints.
        
        Args:
            data (dict): The complete outage data
        
        Side Effects:
            Appends warnings to self.warnings for insufficient resources
            Does not affect validation pass/fail status (warnings only)
        """
        # Calculate maximum simultaneous demand per skill
        skill_max_demand = {}
        
        for task in data.get('tasks', []):
            for res_req in task.get('required_resources', []):
                skill = res_req['skill_type']
                count = res_req['crew_count']
                skill_max_demand[skill] = max(skill_max_demand.get(skill, 0), count)
        
        # Build resource availability map - use MAXIMUM availability across all periods
        resource_map = {}
        for r in data.get('resources', []):
            skill = r['skill_type']
            # Find maximum available count across all availability periods
            max_available = 0
            for period in r.get('availability_periods', []):
                max_available = max(max_available, period.get('available_count', 0))
            resource_map[skill] = max_available
        
        # Compare demand with maximum available
        for skill, max_demand in skill_max_demand.items():
            available = resource_map.get(skill, 0)
            if max_demand > available:
                self.warnings.append(
                    f"Skill '{skill}': max single task demand ({max_demand}) exceeds "
                    f"maximum available across all periods ({available}). This task cannot be executed."
                )
    
    def validate(self, data: Dict) -> Tuple[bool, List[str], List[str]]:
        """
        Perform complete validation.
        
        Returns:
            Tuple of (is_valid, errors, warnings)
        """
        self.errors = []
        self.warnings = []
        
        print("\n" + "="*60)
        print("OUTAGE DATA VALIDATION")
        print("="*60 + "\n")
        
        # Schema validation
        schema_valid = self.validate_schema(data)
        
        # Referential integrity validation
        if schema_valid:
            integrity_valid = self.validate_referential_integrity(data)
        else:
            integrity_valid = False
            print("\n⚠ Skipping referential integrity checks due to schema errors")
        
        # Print summary
        print("\n" + "="*60)
        if self.errors:
            print(f"✗ VALIDATION FAILED - {len(self.errors)} error(s)")
            print("="*60)
            print("\nErrors:")
            for i, error in enumerate(self.errors, 1):
                print(f"  {i}. {error}")
        else:
            print("✓ VALIDATION PASSED")
            print("="*60)
        
        if self.warnings:
            print(f"\n⚠ {len(self.warnings)} warning(s):")
            for i, warning in enumerate(self.warnings, 1):
                print(f"  {i}. {warning}")
        
        print()
        
        return len(self.errors) == 0, self.errors, self.warnings


def main():
    """Main entry point for the validation script."""
    parser = argparse.ArgumentParser(
        description='Validate nuclear outage planning JSON data files'
    )
    parser.add_argument(
        'input_file',
        help='Path to the JSON data file to validate'
    )
    parser.add_argument(
        '--schema',
        help='Path to custom JSON schema file (optional)',
        default=None
    )
    
    args = parser.parse_args()
    
    # Check if input file exists
    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"Error: File '{args.input_file}' not found")
        sys.exit(1)
    
    # Load JSON data
    try:
        with open(input_path, 'r') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON file - {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file: {e}")
        sys.exit(1)
    
    # Validate
    validator = OutageDataValidator(schema_path=args.schema)
    is_valid, errors, warnings = validator.validate(data)
    
    # Exit with appropriate code
    sys.exit(0 if is_valid else 1)


if __name__ == '__main__':
    main()