
#!/usr/bin/env python3
"""
Outage Planning Data Validator
Validates nuclear outage planning JSON data files against a JSON Schema and performs
additional referential/semantic integrity checks.

Usage:
  python validate_outage_data.py <input_file.json> [--schema outage_schema.json]
                                 [--json-output] [--fail-on-warning]
                                 [--strict-resource-overlaps]

Requirements:
  pip install jsonschema
"""

import json
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Set, Tuple, Any
from datetime import datetime

try:
    from jsonschema import Draft7Validator, ValidationError
except ImportError:
    print("Error: jsonschema library not found. Install it with: pip install jsonschema")
    sys.exit(1)


class OutageDataValidator:
    """Validates outage planning data for schema compliance and referential integrity."""

    def __init__(self, schema_path: str):
        """
        Initialize the outage data validator.

        Args:
            schema_path (str, optional): Path to custom JSON schema file.
                                         If None, attempt to load local 'outage_schema.json';
                                         if not found, use embedded DEFAULT_SCHEMA.
        """
        if not schema_path:
            print("Error: --schema argument is required.")
            sys.exit(1)
        self.schema = self._load_schema(schema_path)
        self.errors: List[str] = []
        self.warnings: List[str] = []

    # ------------------------- Schema Handling -------------------------

    def _load_schema(self, schema_path: str | None) -> Dict:
        """Load schema from provided path, local file, or embedded default."""
        if schema_path:
            p = Path(schema_path)
            if not p.exists():
                print(f"⚠ Schema file '{schema_path}' not found. Falling back to defaults.")
                return DEFAULT_SCHEMA
            with p.open("r", encoding="utf-8") as f:
                return json.load(f)

    def validate_schema(self, data: Dict) -> bool:
        """
        Validate data against the JSON schema using Draft7Validator.
        Collects ALL schema errors for comprehensive reporting.
        """
        validator = Draft7Validator(self.schema)
        errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
        if not errors:
            print("✓ Schema validation passed")
            return True

        for err in errors:
            path = "/" + "/".join(str(p) for p in err.path)
            self.errors.append(f"Schema error at {path or '/'}: {err.message}")
        print("✗ Schema validation failed")
        return False

    # ----------------------- Referential Integrity -----------------------

    def validate_referential_integrity(self, data: Dict,
                                       strict_resource_overlaps: bool = False) -> bool:
        """
        Validate referential integrity and semantic constraints.
        Returns True if all checks pass; otherwise False.
        """
        all_valid = True

        tasks = data.get("tasks", [])
        locations = data.get("locations", [])
        equipment = data.get("equipment", [])
        resources = data.get("resources", [])

        task_ids: Set[str] = {t["task_id"] for t in tasks if "task_id" in t}
        location_ids: Set[str] = {l["location_id"] for l in locations if "location_id" in l}
        equipment_ids: Set[str] = {e["equipment_id"] for e in equipment if "equipment_id" in e}
        skill_types: Set[str] = {r["skill_type"] for r in resources if "skill_type" in r}

        # Uniqueness checks by ID fields
        all_valid &= self._validate_unique_ids_tasks(tasks)
        all_valid &= self._validate_unique_ids_by_field(resources, "skill_type", "resource skill")
        all_valid &= self._validate_unique_ids_by_field(equipment, "equipment_id", "equipment")
        all_valid &= self._validate_unique_ids_by_field(locations, "location_id", "location")

        # Task references & self-references
        all_valid &= self._validate_task_references(tasks, task_ids)

        # Location references
        all_valid &= self._validate_location_references(tasks, location_ids)

        # Equipment references
        all_valid &= self._validate_equipment_references(tasks, equipment_ids)

        # Skill references
        all_valid &= self._validate_skill_references(tasks, skill_types)

        # Hold-point logic (runtime checks complementing schema)
        all_valid &= self._validate_hold_points(tasks, task_ids)

        # Cycle detection across successors + blocks_tasks
        all_valid &= self._validate_no_cycles(tasks)

        # Time-period consistency: start <= end and overlaps
        self._validate_availability_periods(resources, "resources", strict=strict_resource_overlaps)
        self._validate_availability_periods(equipment, "equipment", strict=True)
        self._validate_availability_periods(locations, "locations", strict=True)

        # Resource sufficiency (coarse, max-based) → warnings
        self._check_resource_sufficiency(data)

        return all_valid

    # -------------------------- Core Checks --------------------------

    def _validate_unique_ids_tasks(self, tasks: List[Dict]) -> bool:
        ids = [t.get("task_id") for t in tasks]
        dup = self._duplicates(ids)
        if dup:
            self.errors.append(f"Duplicate task IDs found: {sorted(list(dup))}")
            print("✗ Duplicate task IDs found")
            return False
        print("✓ All task IDs are unique")
        return True

    def _validate_unique_ids_by_field(self, items: List[Dict], field: str, label: str) -> bool:
        ids = [x.get(field) for x in items if field in x]
        dup = self._duplicates(ids)
        if dup:
            self.errors.append(f"Duplicate {label} IDs found: {sorted(list(dup))}")
            print(f"✗ Duplicate {label} IDs found")
            return False
        print(f"✓ All {label} IDs are unique")
        return True

    @staticmethod
    def _duplicates(values: List[str]) -> Set[str]:
        return {v for v in values if v is not None and values.count(v) > 1}

    def _validate_task_references(self, tasks: List[Dict], task_ids: Set[str]) -> bool:
        valid = True
        for t in tasks:
            tid = t.get("task_id")
            # successors exist and not self
            for succ in t.get("successors", []):
                if succ not in task_ids:
                    self.errors.append(
                        f"Task '{tid}' references non-existent successor '{succ}'"
                    )
                    valid = False
                if succ == tid:
                    self.errors.append(
                        f"Task '{tid}' lists itself as a successor (self-reference)"
                    )
                    valid = False
            # blocks_tasks exist and not self
            for blocked in t.get("blocks_tasks", []):
                if blocked not in task_ids:
                    self.errors.append(
                        f"Task '{tid}' references non-existent blocked task '{blocked}'"
                    )
                    valid = False
                if blocked == tid:
                    self.errors.append(
                        f"Task '{tid}' blocks itself (self-reference)"
                    )
                    valid = False
        print("✓ Task references checked")
        return valid

    def _validate_location_references(self, tasks: List[Dict], location_ids: Set[str]) -> bool:
        valid = True
        for t in tasks:
            loc = t.get("location_id")
            if loc and loc not in location_ids:
                self.errors.append(
                    f"Task '{t.get('task_id')}' references non-existent location '{loc}'"
                )
                valid = False
        if valid:
            print("✓ Location references are valid")
        return valid

    def _validate_equipment_references(self, tasks: List[Dict], equipment_ids: Set[str]) -> bool:
        valid = True
        for t in tasks:
            for eq in t.get("required_equipment", []):
                eq_id = eq.get("equipment_id")
                if eq_id not in equipment_ids:
                    self.errors.append(
                        f"Task '{t.get('task_id')}' references non-existent equipment '{eq_id}'"
                    )
                    valid = False
        if valid:
            print("✓ Equipment references are valid")
        return valid

    def _validate_skill_references(self, tasks: List[Dict], skill_types: Set[str]) -> bool:
        valid = True
        for t in tasks:
            for rr in t.get("required_resources", []):
                skill = rr.get("skill_type")
                if skill not in skill_types:
                    self.errors.append(
                        f"Task '{t.get('task_id')}' requires skill '{skill}' which is not defined"
                    )
                    valid = False
        if valid:
            print("✓ Skill type references are valid")
        return valid

    def _validate_hold_points(self, tasks: List[Dict], task_ids: Set[str]) -> bool:
        """
        Runtime hold-point checks to complement schema:
          - If is_hold_point true → hold_point_type present
          - If is_hold_point false → blocks_tasks must be empty; hold_point_type absent or null
          - blocks_tasks may not contain self; must reference existing tasks
        """
        valid = True
        for t in tasks:
            tid = t.get("task_id")
            is_hold = t.get("is_hold_point", False)
            hold_type = t.get("hold_point_type")
            blocks = t.get("blocks_tasks", [])

            if is_hold and not hold_type:
                # Schema already enforces via if/then; keep as warning for clarity
                self.warnings.append(
                    f"Task '{tid}' marked as hold point but 'hold_point_type' is missing/null"
                )

            if not is_hold and (hold_type or blocks):
                self.errors.append(
                    f"Task '{tid}' is not a hold point but has hold_point_type/blocks_tasks defined"
                )
                valid = False

            for b in blocks:
                if b == tid:
                    self.errors.append(f"Hold task '{tid}' cannot block itself")
                    valid = False
                if b not in task_ids:
                    self.errors.append(
                        f"Hold task '{tid}' blocks non-existent task '{b}'"
                    )
                    valid = False

        if valid:
            print("✓ Hold-point logic is valid")
        return valid

    def _validate_no_cycles(self, tasks: List[Dict]) -> bool:
        """
        Check for cycles over successors and blocks_tasks using DFS.
        """
        graph: Dict[str, List[str]] = {t["task_id"]: list(t.get("successors", [])) for t in tasks}
        # incorporate hold edges
        for t in tasks:
            if t.get("is_hold_point", False):
                src = t["task_id"]
                for b in t.get("blocks_tasks", []):
                    graph.setdefault(src, [])
                    if b not in graph[src]:
                        graph[src].append(b)

        visited: Set[str] = set()
        stack: Set[str] = set()

        def dfs(node: str) -> bool:
            visited.add(node)
            stack.add(node)
            for nbr in graph.get(node, []):
                if nbr not in visited:
                    if dfs(nbr):
                        return True
                elif nbr in stack:
                    return True
            stack.remove(node)
            return False

        for n in graph.keys():
            if n not in visited:
                if dfs(n):
                    self.errors.append(f"Circular dependency detected involving '{n}'")
                    print("✗ Circular dependencies found in task network")
                    return False

        print("✓ No circular dependencies found")
        return True

    # ----------------------- Availability Periods -----------------------

    def _validate_availability_periods(self, items: List[Dict], group: str, strict: bool) -> None:
        """
        Validate period ordering and overlap for resources/equipment/locations.
        - strict=True → overlaps produce ERRORS
        - strict=False → overlaps produce WARNINGS
        """
        for obj in items:
            # select periods per group
            if group == "resources":
                periods = obj.get("availability_periods", [])
                label = f"Resource '{obj.get('skill_type')}'"
            elif group == "equipment":
                periods = obj.get("availability_periods", [])
                label = f"Equipment '{obj.get('equipment_id')}'"
            else:  # locations
                periods = obj.get("availability_periods", [])
                label = f"Location '{obj.get('location_id')}'"

            # Validate ordering and overlaps
            # Convert to list of (start_dt, end_dt, idx)
            parsed: List[Tuple[datetime, datetime, int]] = []
            for i, p in enumerate(periods):
                try:
                    sd = datetime.fromisoformat(p["start_date"])
                    ed = datetime.fromisoformat(p["end_date"])
                except Exception as ex:
                    self.errors.append(f"{label}: invalid date-time format in period {i} ({ex})")
                    continue

                if sd >= ed:
                    self.errors.append(f"{label}: start_date >= end_date in period {i}")
                parsed.append((sd, ed, i))

            # Sort by start date
            parsed.sort(key=lambda x: x[0])

            # Check overlaps
            for i in range(len(parsed) - 1):
                _, e_cur, idx_cur = parsed[i]
                s_next, _, idx_next = parsed[i + 1]
                if e_cur > s_next:  # strict '>' overlap
                    msg = f"{label}: overlapping availability periods {idx_cur} and {idx_next}"
                    if strict:
                        self.errors.append(msg)
                    else:
                        self.warnings.append(msg)

    # ---------------------- Coarse Sufficiency Check ----------------------

    def _check_resource_sufficiency(self, data: Dict) -> None:
        """
        Coarse check: max demand per skill vs max available across all periods.
        Warns on impossible single-task demands.
        """
        skill_max_demand: Dict[str, int] = {}
        for t in data.get("tasks", []):
            for rr in t.get("required_resources", []):
                skill = rr.get("skill_type")
                cnt = rr.get("crew_count", 0)
                skill_max_demand[skill] = max(skill_max_demand.get(skill, 0), cnt)

        resource_map: Dict[str, int] = {}
        for r in data.get("resources", []):
            skill = r.get("skill_type")
            max_avail = 0
            for period in r.get("availability_periods", []):
                max_avail = max(max_avail, period.get("available_count", 0))
            resource_map[skill] = max_avail

        for skill, max_dem in skill_max_demand.items():
            available = resource_map.get(skill, 0)
            if max_dem > available:
                self.warnings.append(
                    f"Skill '{skill}': max single-task demand ({max_dem}) exceeds "
                    f"maximum available across all periods ({available})."
                )

    # ----------------------------- Driver -----------------------------

    def validate(self, data: Dict, *, strict_resource_overlaps: bool = False
                 ) -> Tuple[bool, List[str], List[str]]:
        """
        Perform complete validation and return (is_valid, errors, warnings).
        """
        self.errors = []
        self.warnings = []

        print("\n" + "=" * 60)
        print("OUTAGE DATA VALIDATION")
        print("=" * 60 + "\n")

        schema_ok = self.validate_schema(data)

        if schema_ok:
            integrity_ok = self.validate_referential_integrity(
                data, strict_resource_overlaps=strict_resource_overlaps
            )
        else:
            integrity_ok = False
            print("\n⚠ Skipping referential integrity checks due to schema errors")

        print("\n" + "=" * 60)
        if self.errors:
            print(f"✗ VALIDATION FAILED - {len(self.errors)} error(s)")
            print("=" * 60)
            print("\nErrors:")
            for i, err in enumerate(self.errors, 1):
                print(f" {i}. {err}")
        else:
            print("✓ VALIDATION PASSED")
            print("=" * 60)

        if self.warnings:
            print(f"\n⚠ {len(self.warnings)} warning(s):")
            for i, warn in enumerate(self.warnings, 1):
                print(f" {i}. {warn}")
            print()

        return len(self.errors) == 0, self.errors, self.warnings


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Validate nuclear outage planning JSON data files"
    )
    parser.add_argument("input_file", help="Path to the JSON data file to validate")
    parser.add_argument("--schema", help="Path to custom JSON schema file", default=None)
    parser.add_argument("--json-output", action="store_true",
                        help="Emit JSON report (useful for CI)")
    parser.add_argument("--fail-on-warning", action="store_true",
                        help="Exit with non-zero status if warnings are present")
    parser.add_argument("--strict-resource-overlaps", action="store_true",
                        help="Treat overlapping resource availability periods as errors "
                             "(default: warnings)")

    args = parser.parse_args()

    # Load data
    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"Error: File '{args.input_file}' not found")
        sys.exit(1)

    try:
        with input_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON file - {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file: {e}")
        sys.exit(1)

    # Validate
    validator = OutageDataValidator(schema_path=args.schema)
    is_valid, errors, warnings = validator.validate(
        data, strict_resource_overlaps=args.strict_resource_overlaps
    )

    if args.json_output:
        report = {
            "file": str(input_path),
            "is_valid": is_valid,
            "errors": errors,
            "warnings": warnings,
        }
        print(json.dumps(report, indent=2, default=str))

    # Exit code policy
    if not is_valid:
        sys.exit(1)
    if args.fail_on_warning and warnings:
        sys.exit(2)
    sys.exit(0)
