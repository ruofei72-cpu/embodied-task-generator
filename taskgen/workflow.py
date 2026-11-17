"""Customer-sheet adaptation and explicit, source-bound review before delivery."""

from collections import Counter, defaultdict
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .engine import (TaskgenError, canonical, prepare_task, require,
                     validate_config, validate_variants)
from .io import read_table, spreadsheet_safe, write_table


REVIEW_FIELDS = ["场景", "原子任务编号", "变体编号", "原始指令", "变更维度", "任务指令",
                 "初始状态", "目标状态", "现场准备", "指纹", "批次标识"]
REVIEW_COLUMNS = REVIEW_FIELDS + ["审核结论", "修改意见"]
STATUSES = {"": "pending", "待审核": "pending", "pending": "pending",
            "通过": "approved", "approved": "approved", "退回": "rejected", "rejected": "rejected"}


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".taskgen-", dir=path.parent) as directory:
        temporary = Path(directory) / path.name
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                             encoding="utf-8")
        temporary.replace(path)


def validate_mapping(mapping):
    require(isinstance(mapping, dict) and mapping.get("mapping_version") == "1.0",
            "Mapping must be an object with mapping_version='1.0'")
    columns, parameters = mapping.get("columns"), mapping.get("parameters")
    require(isinstance(columns, dict) and {"task_id", "scene_id", "original_instruction"} <= set(columns),
            "Mapping columns must specify task_id, scene_id and original_instruction")
    require(set(columns) <= {"task_id", "task_name", "scene_id", "original_instruction", "template_id"},
            "Unknown mapped task field")
    require(isinstance(parameters, dict) and bool(parameters), "Parameter-column mapping is required")
    for table in (columns, parameters):
        require(all(isinstance(k, str) and isinstance(v, str) and bool(v.strip()) for k, v in table.items()),
                "Mappings must use nonempty column names")
    sources = list(columns.values()) + list(parameters.values())
    require(len(set(sources)) == len(sources), "Each source column must map to only one field")
    templates = mapping.get("templates")
    require(isinstance(templates, dict) and bool(templates) and
            all(isinstance(k, str) and isinstance(v, dict) for k, v in templates.items()),
            "Mapping templates must contain named task definitions")
    default = mapping.get("default_template")
    require("template_id" in columns or (isinstance(default, str) and default in templates),
            "Map a template_id column or explicitly configure default_template")
    aliases = mapping.get("scene_aliases", {})
    require(isinstance(aliases, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in aliases.items()),
            "scene_aliases must map client names to scene IDs")
    numbers = mapping.get("integer_parameters", [])
    require(isinstance(numbers, list) and all(isinstance(x, str) and x in parameters for x in numbers),
            "integer_parameters must refer to mapped parameters")
    return mapping


def import_customer_sheet(path, raw_mapping, raw_config):
    """Map visible flat columns; never infer missing task semantics from prose."""
    mapping, config = validate_mapping(raw_mapping), validate_config(raw_config)
    rows = read_table(path)
    require(bool(rows), "Customer sheet contains no rows")
    required_headers = set(mapping["columns"].values()) | set(mapping["parameters"].values())
    tasks, rejected, row_reports, seen_ids = [], [], [], set()
    for number, row in enumerate(rows, start=2):
        if not any(value not in (None, "") for value in row.values()):
            continue
        task_id = row.get(mapping["columns"]["task_id"])
        entry = {"source_row": number, "task_id": task_id, "status": "rejected", "errors": []}
        row_reports.append(entry)
        try:
            missing = sorted(required_headers - set(row))
            require(not missing, "Missing mapped columns: " + ", ".join(missing))
            values = {}
            for key, column in mapping["columns"].items():
                value = row[column]
                if key == "task_name" and value in (None, ""):
                    continue
                require(isinstance(value, (str, int)) and type(value) is not bool and bool(str(value).strip()),
                        f"Column {column} needs a value")
                values[key] = str(value).strip()
            template_id = values.pop("template_id", mapping.get("default_template"))
            require(template_id in mapping["templates"], f"Unknown task template: {template_id}")
            task = deepcopy(mapping["templates"][template_id])
            task.update(values)
            task["task_name"] = values.get("task_name", values["task_id"])
            task["scene_id"] = mapping.get("scene_aliases", {}).get(task["scene_id"], task["scene_id"])
            task["parameters"] = {}
            for field, column in mapping["parameters"].items():
                value = row[column]
                require(value not in (None, ""), f"Column {column} needs a value; free text is not auto-parsed")
                if field in mapping.get("integer_parameters", []):
                    if type(value) is float and value.is_integer():
                        value = int(value)
                    elif isinstance(value, str) and value.strip().isdigit():
                        value = int(value.strip())
                    require(type(value) is int, f"Column {column} must contain an integer")
                elif isinstance(value, str):
                    value = value.strip()
                task["parameters"][field] = value
            prepare_task(task, config)
            require(task["task_id"] not in seen_ids, "Duplicate task_id in customer sheet")
            seen_ids.add(task["task_id"])
            tasks.append(task)
            entry.update(status="accepted", task_id=task["task_id"], template_id=template_id)
        except TaskgenError as exc:
            entry["errors"].append(str(exc))
            rejected.append({**row, "导入错误": str(exc), "源行号": number})
    require(bool(row_reports), "Customer sheet contains no task rows")
    report = {"mapping_version": "1.0", "status": "complete" if not rejected else ("partial" if tasks else "failed"),
              "accepted_rows": len(tasks), "rejected_rows": len(rejected), "rows": row_reports,
              "mapping_sha256": sha256(canonical(mapping).encode()).hexdigest(),
              "source_sha256": sha256(Path(path).read_bytes()).hexdigest()}
    return tasks, rejected, report


def export_import_results(tasks, rejected, report, outdir):
    outdir = Path(outdir)
    write_json(outdir / "imported_tasks.json", tasks)
    write_json(outdir / "import_report.json", report)
    columns = list(dict.fromkeys(k for row in rejected for k in row)) if rejected else ["源行号", "导入错误"]
    write_table(rejected, columns, outdir / "incomplete_rows.csv")


def batch_id(records):
    # Bind review decisions to the full canonical batch, not only task parameters.
    return sha256(canonical(records).encode("utf-8")).hexdigest()


def review_rows(records):
    batch = batch_id(records)
    return [{"场景": r["scene_id"], "原子任务编号": r["parent_task_id"], "变体编号": r["variant_id"],
             "原始指令": r["original_instruction"], "变更维度": ";".join(r["changed_axes"]),
             "任务指令": r["description"], "初始状态": r["initial_state"],
             "目标状态": "; ".join(r["task_goal"]), "现场准备": r["reset_instruction"],
             "指纹": r["fingerprint"], "批次标识": batch, "审核结论": "待审核", "修改意见": ""}
            for r in records]


def export_review(records, config, path):
    issues = validate_variants(records, config)
    require(not issues, "Canonical batch is invalid: " + "; ".join(issues))
    write_table(review_rows(records), REVIEW_COLUMNS, path)


def finalize_review(records, config, review_path):
    issues = validate_variants(records, config)
    require(not issues, "Canonical batch is invalid: " + "; ".join(issues))
    expected = {row["变体编号"]: row for row in review_rows(records)}
    decisions = {}
    for number, row in enumerate(read_table(review_path), start=2):
        if not any(value not in (None, "") for value in row.values()):
            continue
        variant_id = row.get("变体编号")
        require(isinstance(variant_id, str) and variant_id in expected,
                f"Review row {number}: unknown variant ID")
        require(variant_id not in decisions, f"Review row {number}: duplicate variant ID")
        require(set(REVIEW_COLUMNS) <= set(row), f"Review row {number}: missing review columns")
        for field in REVIEW_FIELDS:
            require(str(row.get(field, "")) == spreadsheet_safe(expected[variant_id][field]),
                    f"Review row {number}: source field '{field}' changed or belongs to a stale batch; regenerate and review again")
        raw_status = row.get("审核结论") or ""
        require(isinstance(raw_status, str) and raw_status.strip() in STATUSES,
                f"Review row {number}: decision must be 待审核, 通过 or 退回")
        status = STATUSES[raw_status.strip()]
        notes = str(row.get("修改意见") or "").strip()
        require(status != "rejected" or bool(notes), f"Review row {number}: a returned task needs a reason")
        decisions[variant_id] = {"status": status, "notes": notes, "source_row": number}
    groups = defaultdict(list)
    for record in records:
        groups[record["parent_task_id"]].append(record)
        decisions.setdefault(record["variant_id"], {"status": "pending", "notes": "Review row missing", "source_row": None})
    delivery, withheld, delivered_groups = [], [], []
    for task_id, group in groups.items():
        statuses = Counter(decisions[r["variant_id"]]["status"] for r in group)
        if statuses["approved"] == len(group):
            delivered_groups.append(task_id)
            for record in group:
                row = dict(expected[record["variant_id"]])
                row.update(审核结论="通过", 修改意见=decisions[record["variant_id"]]["notes"])
                delivery.append(row)
        else:
            withheld.append({"parent_task_id": task_id, "total": len(group), **dict(statuses)})
    counts = Counter(item["status"] for item in decisions.values())
    report = {"batch_id": batch_id(records), "status": "complete" if not withheld else "withheld",
              "source_variants": len(records), "decisions": dict(counts),
              "delivered_groups": delivered_groups, "delivered_variants": len(delivery),
              "withheld_groups": withheld,
              "note": "Only complete, explicitly approved atomic-task groups are delivered. No actual platform upload is performed."}
    audit = {"batch_id": batch_id(records), "review_file_sha256": sha256(Path(review_path).read_bytes()).hexdigest(),
             "decisions": decisions}
    return delivery, report, audit


def write_delivery(delivery, report, audit, outdir, xlsx=False):
    outdir = Path(outdir)
    # Customer-facing delivery excludes technical binding columns; audit retains them.
    columns = [field for field in REVIEW_COLUMNS if field not in ("指纹", "批次标识")]
    write_table(delivery, columns, outdir / "delivery.csv")
    if xlsx:
        write_table(delivery, columns, outdir / "delivery.xlsx")
    write_json(outdir / "delivery_report.json", report)
    write_json(outdir / "review_audit.json", audit)
