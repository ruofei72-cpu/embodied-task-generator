#!/usr/bin/env python3
"""Validate task JSON or platform-template CSV without modifying either file."""

import argparse
import csv
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSV_FIELDS = {
    "Task Name": "task_name",
    "Description": "description",
    "Task Goal": "task_goal",
    "Initial State": "initial_state",
    "Generalization Axes": "generalization_axes",
    "Task Type": "task_type",
    "Environment": "environment",
    "Label": None,
    "SOP": "sop",
}
LABEL_FIELDS = {
    "tool_use", "estimated_duration", "dexterity_level", "fine_tool_use", "project"
}
STRING_FIELDS = {
    "task_name", "description", "initial_state", "generalization_axes",
    "task_type", "dexterity_level", "environment", "estimated_duration", "sop",
    "project",
}
BOOL_FIELDS = {"tool_use", "fine_tool_use"}
TASK_FIELDS = STRING_FIELDS | BOOL_FIELDS | {"task_goal"}
ENUM_FIELDS = {
    "environment": "supported_environments",
    "task_type": "task_types",
    "dexterity_level": "dexterity_levels",
    "estimated_duration": "duration_buckets",
}


def nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def reject_constant(value):
    raise ValueError(f"Non-standard JSON constant: {value}")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_json(text):
    return json.loads(text, object_pairs_hook=unique_object,
                      parse_constant=reject_constant)


def load_config(path):
    config = parse_json(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict) or config.get("schema_version") != "1.1":
        raise ValueError("Configuration must be an object with schema_version '1.1'")
    if not nonempty_string(config.get("project_tag")):
        raise ValueError("Configuration project_tag must be a nonempty string")
    for key in ENUM_FIELDS.values():
        values = config.get(key)
        if (not isinstance(values, list) or not values
                or not all(nonempty_string(value) for value in values)):
            raise ValueError(f"Configuration {key} must be a nonempty string array")
        if len(set(values)) != len(values):
            raise ValueError(f"Configuration {key} must not contain duplicates")
    return config


def load_tasks(path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        return parse_json(path.read_text(encoding="utf-8-sig"))
    if path.suffix.lower() != ".csv":
        raise ValueError("Input must be a .json or .csv file")
    tasks = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if reader.fieldnames != list(CSV_FIELDS):
            raise ValueError("CSV headers must be: " + ",".join(CSV_FIELDS))
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"CSV line {reader.line_num}: incorrect column count")
            try:
                label = parse_json(row["Label"])
                if not isinstance(label, dict) or set(label) != LABEL_FIELDS:
                    raise ValueError("Label must contain exactly: "
                                     + ", ".join(sorted(LABEL_FIELDS)))
                task = {field: row[column] for column, field in CSV_FIELDS.items()
                        if field is not None}
                task["task_goal"] = parse_json(row["Task Goal"])
                task.update(label)
                tasks.append(task)
            except ValueError as error:
                raise ValueError(f"CSV line {reader.line_num}: {error}") from error
    return tasks


def load_source(path):
    """Read source CSV rows in order, resolving task-group identity only."""
    path = Path(path)
    if path.suffix.lower() != ".csv":
        raise ValueError("Source must be a .csv file; save Excel input as CSV first")
    required = {"Task ID", "Task Name", "Variant Instruction"}
    variants = []
    group_id = None
    group_name = None
    group_names = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        headers = reader.fieldnames
        if (not headers or len(set(headers)) != len(headers)
                or not required.issubset(headers)):
            raise ValueError("Source needs unique headers including Task ID, "
                             "Task Name, Variant Instruction")
        for row in reader:
            prefix = f"Source CSV line {reader.line_num}"
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{prefix}: incorrect column count")
            if not any(value.strip() for value in row.values()):
                continue
            task_id = row["Task ID"].strip()
            task_name = row["Task Name"].strip()
            if task_id and task_id != group_id:
                if not task_name:
                    raise ValueError(f"{prefix}: a new Task ID requires Task Name")
                if task_id in group_names and group_names[task_id] != task_name:
                    raise ValueError(f"{prefix}: Task Name conflicts with an earlier Task ID")
                group_names[task_id] = task_name
                group_id, group_name = task_id, task_name
            elif group_id is None:
                raise ValueError(f"{prefix}: first task group requires Task ID and Task Name")
            elif task_name and task_name != group_name:
                raise ValueError(f"{prefix}: Task Name conflicts with the current Task ID")
            description = re.sub(r"^variant_\d+\s*:\s*", "",
                                 row["Variant Instruction"].strip(), flags=re.IGNORECASE)
            if not description:
                raise ValueError(f"{prefix}: Variant Instruction must not be empty")
            variants.append({"task_id": group_id, "task_name": group_name,
                             "description": description, "source_line": reader.line_num})
    if not variants:
        raise ValueError("Source CSV must contain at least one task variant")
    return variants


def validate_source(tasks, variants):
    """Compare validated tasks to source row order without rewriting content."""
    errors = []
    if len(tasks) != len(variants):
        errors.append(f"Source has {len(variants)} variants but output has {len(tasks)} tasks")
    for index, (task, variant) in enumerate(zip(tasks, variants), start=1):
        if task["description"] != variant["description"]:
            errors.append(f"Task {index}.description: does not match source line "
                          f"{variant['source_line']} after removing its variant prefix "
                          "and surrounding whitespace; preserve input order and text")
    return errors


def validate_tasks(tasks, config):
    """Return structural errors and exact duplicate candidates; never delete tasks."""
    if not isinstance(tasks, list) or not tasks:
        return ["Input must be a nonempty JSON task array (or CSV with data rows)"]
    errors = []
    seen_names = {}
    seen_descriptions = {}
    for index, task in enumerate(tasks, start=1):
        prefix = f"Task {index}"
        if not isinstance(task, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        missing = TASK_FIELDS - set(task)
        extra = set(task) - TASK_FIELDS
        if missing:
            errors.append(f"{prefix}: missing fields: {', '.join(sorted(missing))}")
        if extra:
            errors.append(f"{prefix}: unknown fields: {', '.join(sorted(extra))}")
        for field in sorted(STRING_FIELDS & set(task)):
            if not nonempty_string(task[field]):
                errors.append(f"{prefix}.{field}: must be a nonempty string")
        for field in sorted(BOOL_FIELDS & set(task)):
            if type(task[field]) is not bool:
                errors.append(f"{prefix}.{field}: must be a JSON boolean, not text/number")
        if "task_goal" in task:
            goals = task["task_goal"]
            if (not isinstance(goals, list) or not goals
                    or not all(nonempty_string(goal) for goal in goals)):
                errors.append(f"{prefix}.task_goal: must be a nonempty string array")
        for field, config_key in ENUM_FIELDS.items():
            if field in task and task[field] not in config[config_key]:
                errors.append(f"{prefix}.{field}: not allowed by {config_key}")
        if task.get("fine_tool_use") is True and task.get("tool_use") is not True:
            errors.append(f"{prefix}: fine_tool_use=true requires tool_use=true")
        for field, seen in (("task_name", seen_names),
                            ("description", seen_descriptions)):
            value = task.get(field)
            if nonempty_string(value):
                normalized = " ".join(value.casefold().split())
                if normalized in seen:
                    errors.append(f"{prefix}.{field}: exact duplicate candidate of "
                                  f"task {seen[normalized]}; review before export")
                else:
                    seen[normalized] = index
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Task JSON array or template CSV")
    parser.add_argument("--config", type=Path, default=ROOT / "config.example.json")
    parser.add_argument("--source", type=Path, help="Original task-variant CSV to compare")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        tasks = load_tasks(args.input)
        errors = validate_tasks(tasks, config)
        if args.source is not None:
            variants = load_source(args.source)
            if not errors:
                errors.extend(validate_source(tasks, variants))
    except (OSError, UnicodeError, ValueError, csv.Error) as error:
        print(f"Validation failed: {error}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Validated {len(tasks)} tasks. Semantic and platform review still required.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
