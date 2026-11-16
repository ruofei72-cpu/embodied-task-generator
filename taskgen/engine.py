"""Rule-based generation, state isolation, constraint checks, and provenance.

This module does not execute robot actions or prove physical feasibility. Each
candidate is generated from a fresh copy of the atomic task's parameter state.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from hashlib import sha256
from itertools import combinations, product
import json
import math
import re
from string import Formatter


class TaskgenError(ValueError):
    """An actionable configuration or input-data error."""


ENVIRONMENTS = {
    "home", "retail", "logistics", "factory", "office", "healthcare",
    "education", "hospitality", "outdoor", "construction", "transit",
    "sports", "service", "other",
}
IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
LABEL_DEFAULTS = {
    "task_type": "Gripper,Dexterous Hand",
    "dexterity_level": "non-dexterous",
    "estimated_duration": "0.5–2min",
    "tool_use": False,
    "fine_tool_use": False,
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def require(condition, message):
    if not condition:
        raise TaskgenError(message)


def ensure_json(value, name):
    try:
        return canonical(value)
    except (TypeError, ValueError) as exc:
        raise TaskgenError(f"{name} must contain JSON-compatible values and finite numbers") from exc


def scalar(value):
    return (isinstance(value, str) and bool(value.strip())) or (
        type(value) in (int, float) and math.isfinite(value)
    )


def positive_int(value, name, maximum=1000):
    require(type(value) is int and 1 <= value <= maximum,
            f"{name} must be an integer between 1 and {maximum}")


def validate_rule(rule, fields):
    require(isinstance(rule, dict), "Each constraint must be an object")
    require(isinstance(rule.get("name", "constraint"), str) and bool(rule.get("name", "constraint").strip()),
            "Constraint name must be a nonempty string")
    kind = rule.get("type")
    require(kind in ("different", "forbid"),
            "Constraint type must be 'different' or 'forbid'")
    if kind == "different":
        names = rule.get("fields")
        require(isinstance(names, list) and len(names) >= 2 and
                all(isinstance(x, str) and x in fields for x in names) and
                len(set(names)) == len(names),
                "A 'different' constraint needs at least two distinct known fields")
    else:
        when = rule.get("when")
        require(isinstance(when, dict) and bool(when) and
                all(k in fields and scalar(v) for k, v in when.items()),
                "A 'forbid' constraint needs a nonempty 'when' using known fields")


def validate_config(config):
    require(isinstance(config, dict), "Config must be a JSON object")
    ensure_json(config, "Config")
    require(config.get("schema_version") == "2.0", "Config schema_version must be '2.0'")
    positive_int(config.get("variants_per_task", 7), "variants_per_task")
    positive_int(config.get("max_candidates", 10000), "max_candidates", 100000)
    require(config.get("mode", "single-axis") in ("single-axis", "cross"),
            "mode must be 'single-axis' or 'cross'")
    for field in ("require_axis_coverage", "deduplicate_across_tasks"):
        require(type(config.get(field, True)) is bool, f"{field} must be boolean")
    require(isinstance(config.get("project_tag", "embodied_demo"), str) and
            bool(config.get("project_tag", "embodied_demo").strip()),
            "project_tag must be a nonempty string")
    axes = config.get("axes")
    require(isinstance(axes, list) and 1 <= len(axes) <= 12,
            "axes must contain 1–12 configurable dimensions")
    names, fields = set(), set()
    for axis in axes:
        require(isinstance(axis, dict), "Each axis must be an object")
        name, field = axis.get("name"), axis.get("field")
        require(isinstance(name, str) and IDENTIFIER.fullmatch(name), "Invalid axis name")
        require(isinstance(field, str) and IDENTIFIER.fullmatch(field), "Invalid axis field")
        require(name not in names and field not in fields, "Axis names and fields must be unique")
        values = axis.get("values")
        require(isinstance(values, list) and bool(values) and all(scalar(v) for v in values),
                f"Axis {name}: values must be nonempty strings or finite numbers")
        require(len({canonical(v) for v in values}) == len(values),
                f"Axis {name}: duplicate values")
        names.add(name)
        fields.add(field)
    require(isinstance(config.get("constraints", []), list), "constraints must be a list")
    scenes = config.get("scenes")
    require(isinstance(scenes, dict) and bool(scenes), "scenes must be a nonempty object")
    for scene_id, scene in scenes.items():
        require(isinstance(scene_id, str) and TASK_ID.fullmatch(scene_id), "Invalid scene_id")
        require(isinstance(scene, dict), f"Scene {scene_id} must be an object")
        require(isinstance(scene.get("environment"), str) and scene["environment"] in ENVIRONMENTS,
                f"Scene {scene_id}: invalid environment")
        require(isinstance(scene.get("constraints", []), list), "Scene constraints must be a list")
        allowed = scene.get("allowed_values")
        require(isinstance(allowed, dict) and bool(allowed),
                f"Scene {scene_id}: allowed_values is required")
        for key, values in allowed.items():
            require(isinstance(key, str) and IDENTIFIER.fullmatch(key), "Invalid catalog field")
            require(isinstance(values, list) and bool(values) and all(scalar(v) for v in values),
                    f"Scene {scene_id}: invalid catalog for {key}")
        require(fields <= set(allowed), f"Scene {scene_id}: catalog must list every axis field")
        inventory = scene.get("inventory", {})
        require(isinstance(inventory, dict) and all(
            isinstance(k, str) and type(v) is int and v >= 0 for k, v in inventory.items()
        ), f"Scene {scene_id}: inventory must map object names to nonnegative counts")
        for rule in config.get("constraints", []) + scene.get("constraints", []):
            validate_rule(rule, set(allowed))
    return deepcopy(config)


def template_fields(template):
    require(isinstance(template, str) and bool(template.strip()), "Templates must be nonempty strings")
    try:
        parts = list(Formatter().parse(template))
    except ValueError as exc:
        raise TaskgenError(f"Invalid template: {exc}") from exc
    fields = set()
    for _, name, spec, conversion in parts:
        if name is None:
            continue
        require(bool(IDENTIFIER.fullmatch(name)) and not spec and not conversion,
                "Templates only support simple {parameter} placeholders; no attributes or formatting")
        fields.add(name)
    return fields


def render(template, parameters):
    fields = template_fields(template)
    require(fields <= set(parameters), f"Template references unknown fields: {sorted(fields - set(parameters))}")
    return template.format_map(parameters)


def render_goal(goal, parameters):
    require(isinstance(goal, dict) and bool(goal), "goal must be a nonempty structured object")
    result = {}
    for key, value in goal.items():
        require(isinstance(key, str) and bool(key), "Goal keys must be nonempty strings")
        if isinstance(value, str):
            names = template_fields(value)
            require(names <= set(parameters), "Goal references an unknown parameter")
            if len(names) == 1 and value == "{" + next(iter(names)) + "}":
                result[key] = parameters[next(iter(names))]
            else:
                result[key] = render(value, parameters)
        else:
            require(scalar(value), "Goal values must be strings or finite numbers")
            result[key] = value
    return result


def parameter_issues(parameters, scene, config):
    issues = []
    if not isinstance(parameters, dict) or not parameters:
        return ["parameters must be a nonempty object"]
    for key, value in parameters.items():
        if not isinstance(key, str) or not IDENTIFIER.fullmatch(key) or not scalar(value):
            issues.append("invalid_parameter")
        elif key not in scene["allowed_values"] or canonical(value) not in {
                canonical(v) for v in scene["allowed_values"][key]}:
            issues.append(f"catalog:{key}")
    quantity = parameters.get("quantity")
    if quantity is not None:
        if type(quantity) is not int or quantity <= 0:
            issues.append("quantity_must_be_positive_integer")
        elif scene.get("inventory") and (not isinstance(parameters.get("object"), str) or
                quantity > scene["inventory"].get(parameters["object"], 0)):
            issues.append("insufficient_inventory")
    for rule in config.get("constraints", []) + scene.get("constraints", []):
        kind = rule["type"]
        if kind == "different":
            values = [parameters.get(k) for k in rule["fields"]]
            if any(v is None for v in values) or len({canonical(v) for v in values}) != len(values):
                issues.append("constraint:" + rule.get("name", "different"))
        elif all(canonical(parameters.get(k)) == canonical(v) for k, v in rule["when"].items()):
            issues.append("constraint:" + rule.get("name", "forbid"))
    return issues


def labels_for(task):
    labels = dict(LABEL_DEFAULTS)
    custom = task.get("labels", {})
    require(isinstance(custom, dict) and set(custom) <= set(LABEL_DEFAULTS), "Unknown label fields")
    labels.update(custom)
    require(labels["task_type"] in ("Gripper,Dexterous Hand", "Dexterous Hand"), "Invalid task_type")
    require(labels["dexterity_level"] in ("non-dexterous", "dexterous"), "Invalid dexterity_level")
    require(labels["estimated_duration"] in ("≤0.5min", "0.5–2min", "2–5min", "5–30min", ">30min"),
            "Invalid estimated_duration")
    require(type(labels["tool_use"]) is bool and type(labels["fine_tool_use"]) is bool,
            "Tool-use flags must be JSON booleans")
    require(not labels["fine_tool_use"] or labels["tool_use"], "fine_tool_use requires tool_use")
    return labels


def prepare_task(task, config):
    require(isinstance(task, dict), "Task must be an object")
    task_id = task.get("task_id")
    require(isinstance(task_id, str) and TASK_ID.fullmatch(task_id), "Invalid task_id")
    for key in ("task_name", "original_instruction", "action"):
        require(isinstance(task.get(key), str) and bool(task[key].strip()), f"{key} is required")
    scene_id = task.get("scene_id")
    require(isinstance(scene_id, str) and scene_id in config["scenes"], "Unknown scene_id")
    scene = config["scenes"][scene_id]
    issues = parameter_issues(task.get("parameters"), scene, config)
    require(not issues, "Invalid base parameters: " + ", ".join(issues))
    all_axes = {a["name"]: a for a in config["axes"]}
    active_names = task.get("axes", list(all_axes))
    require(isinstance(active_names, list) and bool(active_names) and
            all(isinstance(n, str) and n in all_axes for n in active_names) and
            len(set(active_names)) == len(active_names), "Task axes must be distinct configured names")
    axes = [all_axes[name] for name in active_names]
    for axis in axes:
        require(axis["field"] in task["parameters"], f"Missing parameter: {axis['field']}")
    for field in ("instruction_template", "initial_state_template", "goal_template", "reset_template"):
        render(task.get(field), task["parameters"])
    render_goal(task.get("goal"), task["parameters"])
    labels_for(task)
    positive_int(task.get("variants_per_task", config.get("variants_per_task", 7)), "variants_per_task")
    return scene, axes


def candidate_parameters(base, axes, scene, mode):
    """Yield fresh parameter states, never mutating the base or a prior variant."""
    pools = []
    for axis in axes:
        field = axis["field"]
        catalog = {canonical(v) for v in scene["allowed_values"][field]}
        pools.append([v for v in axis["values"]
                      if canonical(v) != canonical(base[field]) and canonical(v) in catalog])
    if mode == "single-axis":
        # Round-robin over axes, not seven color changes before the next dimension.
        for index in range(max((len(p) for p in pools), default=0)):
            for axis, pool in zip(axes, pools):
                if index < len(pool):
                    parameters = deepcopy(base)
                    parameters[axis["field"]] = pool[index]
                    yield parameters, [axis["name"]]
    else:
        # Two-axis combinations, bounded by max_candidates in the caller.
        # This is NOT a covering-array or exhaustive all-pairs guarantee.
        for i, j in combinations(range(len(axes)), 2):
            for left, right in product(pools[i], pools[j]):
                parameters = deepcopy(base)
                parameters[axes[i]["field"]] = left
                parameters[axes[j]["field"]] = right
                yield parameters, [axes[i]["name"], axes[j]["name"]]


def fingerprint(scene_id, action, parameters, goal):
    data = {"scene_id": scene_id, "action": action, "parameters": parameters, "goal": goal}
    return sha256(canonical(data).encode("utf-8")).hexdigest()


def select_candidates(pool, quota, required_axes):
    selected, uncovered = [], set(required_axes)
    remaining = list(pool)
    while remaining and len(selected) < quota:
        best = max(range(len(remaining)), key=lambda i: len(uncovered & set(remaining[i][1])))
        chosen = remaining.pop(best)
        selected.append(chosen)
        uncovered.difference_update(chosen[1])
    return selected, sorted(uncovered)


def generate_tasks(tasks, raw_config):
    config = validate_config(raw_config)
    require(isinstance(tasks, list), "Input must be a list of atomic tasks")
    require(bool(tasks), "Input contains no tasks")
    require(len(tasks) <= 10000, "At most 10000 atomic tasks may be processed per run")
    input_json = ensure_json(tasks, "Atomic task input")
    records, task_reports, seen_ids, committed_fingerprints = [], [], set(), set()
    mode = config.get("mode", "single-axis")
    limit = config.get("max_candidates", 10000)
    for row_index, task in enumerate(tasks, start=1):
        report = {"row": row_index, "task_id": task.get("task_id") if isinstance(task, dict) else None,
                  "status": "rejected", "generated_variants": 0, "errors": []}
        task_reports.append(report)
        try:
            scene, axes = prepare_task(task, config)
            task_id = task["task_id"]
            require(task_id not in seen_ids, "Duplicate task_id in input")
            seen_ids.add(task_id)
            quota = task.get("variants_per_task", config.get("variants_per_task", 7))
            report.update(scene_id=task["scene_id"], requested_variants=quota)
            pool, seen_candidates, rejections = [], set(), Counter()
            checked, truncated = 0, False
            for parameters, changed_axes in candidate_parameters(task["parameters"], axes, scene, mode):
                if checked >= limit:
                    truncated = True
                    break
                checked += 1
                issues = parameter_issues(parameters, scene, config)
                if issues:
                    rejections.update(issues)
                    continue
                goal = render_goal(task["goal"], parameters)
                key = fingerprint(task["scene_id"], task["action"], parameters, goal)
                if key in seen_candidates or (config.get("deduplicate_across_tasks", True)
                                              and key in committed_fingerprints):
                    rejections["duplicate_structured_task"] += 1
                    continue
                seen_candidates.add(key)
                pool.append((parameters, changed_axes, goal, key))
            required_axes = [a["name"] for a in axes] if config.get("require_axis_coverage", True) else []
            chosen, missing = select_candidates(pool, quota, required_axes)
            covered = sorted({name for c in chosen for name in c[1]})
            report.update(candidates_checked=checked, candidate_limit_reached=truncated,
                          valid_candidates=len(pool), rejected_candidates=dict(rejections),
                          covered_axes=covered, missing_axes=missing)
            require(len(chosen) == quota,
                    f"Requested {quota} valid variants; found {len(chosen)}. Check catalogs, constraints or candidate limit.")
            require(not missing, "Required axes lack valid coverage: " + ", ".join(missing))
            group = []
            for index, (parameters, changed_axes, goal, key) in enumerate(chosen, start=1):
                variant_id = f"{task_id}-v{index:02d}"
                group.append({
                    "schema_version": "2.0", "variant_id": variant_id,
                    "task_name": f"{task['task_name']}-v{index:02d}",
                    "parent_task_id": task_id, "scene_id": task["scene_id"],
                    "action": task["action"], "original_instruction": task["original_instruction"],
                    "description": render(task["instruction_template"], parameters),
                    "initial_state": render(task["initial_state_template"], parameters),
                    "task_goal": [render(task["goal_template"], parameters)], "goal": goal,
                    "reset_instruction": render(task["reset_template"], parameters),
                    "base_parameters": deepcopy(task["parameters"]), "parameters": parameters,
                    "changed_axes": changed_axes, "active_axes": [a["name"] for a in axes],
                    "requested_variants": quota, "generation_mode": mode,
                    "environment": scene["environment"], "labels": labels_for(task),
                    "project": config.get("project_tag", "embodied_demo"),
                    "fingerprint": key, "requires_human_review": True,
                })
            # All-or-nothing per atomic task: don't silently export five of seven.
            records.extend(group)
            committed_fingerprints.update(c[3] for c in chosen)
            report.update(status="accepted", generated_variants=len(group))
        except TaskgenError as exc:
            report["errors"].append(str(exc))
    accepted = sum(t["status"] == "accepted" for t in task_reports)
    report = {
        "schema_version": "2.0", "generator_version": "0.2.0",
        "status": "complete" if accepted == len(tasks) else ("partial" if accepted else "failed"),
        "summary": {"input_tasks": len(tasks), "accepted_tasks": accepted,
                    "rejected_tasks": len(tasks) - accepted, "generated_variants": len(records)},
        "config_sha256": sha256(canonical(config).encode("utf-8")).hexdigest(),
        "input_sha256": sha256(input_json.encode("utf-8")).hexdigest(),
        "tasks": task_reports,
        "limitations": ["No physical or simulator feasibility validation.",
                        "Reset instructions describe intended setup; no hardware or simulator is controlled.",
                        "Structured duplicates are checked; free-text semantic equivalence is not guaranteed."],
    }
    return records, report


def validate_variants(records, raw_config):
    """Validate exported records, including group completeness and fingerprints."""
    config = validate_config(raw_config)
    require(isinstance(records, list), "Variants must be a list")
    errors, groups, seen_ids, seen_fingerprints = [], defaultdict(list), set(), set()
    if not records:
        return ["No variants to validate"]
    for index, record in enumerate(records, start=1):
        try:
            require(isinstance(record, dict), "Variant must be an object")
            require(record.get("schema_version") == "2.0", "Invalid record schema_version")
            for key in ("variant_id", "parent_task_id", "scene_id", "action", "task_name",
                        "original_instruction", "description", "initial_state", "reset_instruction", "project"):
                require(isinstance(record.get(key), str) and bool(record[key].strip()), f"Missing {key}")
            require(TASK_ID.fullmatch(record["parent_task_id"]), "Invalid parent_task_id")
            require(record["variant_id"] not in seen_ids, "Duplicate variant_id")
            seen_ids.add(record["variant_id"])
            require(record["scene_id"] in config["scenes"], "Unknown scene_id")
            scene = config["scenes"][record["scene_id"]]
            require(record.get("environment") == scene["environment"], "Environment differs from scene catalog")
            require(not parameter_issues(record.get("parameters"), scene, config), "Invalid variant parameters")
            require(not parameter_issues(record.get("base_parameters"), scene, config), "Invalid base parameters")
            require(set(record["parameters"]) == set(record["base_parameters"]), "Parameter fields changed")
            goal = record.get("goal")
            require(isinstance(goal, dict) and bool(goal) and all(
                isinstance(k, str) and scalar(v) for k, v in goal.items()), "Invalid structured goal")
            require(isinstance(record.get("task_goal"), list) and bool(record["task_goal"]) and
                    all(isinstance(x, str) and bool(x.strip()) for x in record["task_goal"]), "Invalid task_goal")
            require(type(record.get("requires_human_review")) is bool and record["requires_human_review"],
                    "Generated tasks must retain requires_human_review=true")
            require(isinstance(record.get("labels"), dict) and set(record["labels"]) == set(LABEL_DEFAULTS),
                    "Missing or unexpected label fields")
            labels_for({"labels": record["labels"]})
            key = fingerprint(record["scene_id"], record["action"], record["parameters"], goal)
            require(record.get("fingerprint") == key, "Fingerprint mismatch")
            require(not config.get("deduplicate_across_tasks", True) or key not in seen_fingerprints,
                    "Duplicate structured task")
            seen_fingerprints.add(key)
            axes = {a["name"]: a["field"] for a in config["axes"]}
            active = record.get("active_axes")
            changed = record.get("changed_axes")
            require(isinstance(active, list) and bool(active) and all(isinstance(a, str) and a in axes for a in active)
                    and len(set(active)) == len(active), "Invalid active_axes")
            require(isinstance(changed, list) and bool(changed) and all(isinstance(a, str) and a in active for a in changed)
                    and len(set(changed)) == len(changed), "Invalid changed_axes")
            actual = {field for field in record["parameters"]
                      if canonical(record["parameters"][field]) != canonical(record["base_parameters"][field])}
            require(actual == {axes[a] for a in changed}, "Changed axes do not match parameter changes")
            mode = record.get("generation_mode")
            require(mode in ("single-axis", "cross"), "Invalid generation_mode")
            require(len(changed) == (1 if mode == "single-axis" else 2), "Incorrect number of changed axes")
            positive_int(record.get("requested_variants"), "requested_variants")
            groups[record["parent_task_id"]].append(record)
        except (TaskgenError, TypeError, KeyError, ValueError) as exc:
            errors.append(f"Record {index}: {exc}")
    for task_id, group in groups.items():
        first = group[0]
        if len(group) != first["requested_variants"]:
            errors.append(f"{task_id}: incomplete group ({len(group)}/{first['requested_variants']})")
        for item in group[1:]:
            for field in ("scene_id", "action", "original_instruction", "base_parameters", "active_axes",
                          "requested_variants", "generation_mode", "project"):
                if item[field] != first[field]:
                    errors.append(f"{task_id}: inconsistent {field}")
        covered = {name for item in group for name in item["changed_axes"]}
        if config.get("require_axis_coverage", True) and not set(first["active_axes"]) <= covered:
            errors.append(f"{task_id}: missing axis coverage")
    return errors
