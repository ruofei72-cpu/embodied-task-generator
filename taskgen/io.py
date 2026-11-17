"""UTF-8 JSON/CSV and optional Excel adapters; explicit platform column mapping."""

import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import os

from .engine import TaskgenError, canonical


PLATFORM_COLUMNS = [
    "Task Name", "Description", "Task Goal", "Initial State", "Generalization Axes",
    "Task Type", "Environment", "Label", "Task ID", "Variant ID", "Scene ID",
    "Reset Instruction", "Parameters", "Fingerprint", "Requires Human Review",
]
JSON_COLUMNS = {"parameters", "goal", "labels", "axes"}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TaskgenError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def invalid_constant(value):
    raise TaskgenError(f"Non-finite JSON number is not allowed: {value}")


def json_options():
    return {"object_pairs_hook": unique_object, "parse_constant": invalid_constant}


def read_json(path):
    try:
        with Path(path).open(encoding="utf-8-sig") as stream:
            return json.load(stream, **json_options())
    except json.JSONDecodeError as exc:
        raise TaskgenError(f"{path}: invalid JSON at line {exc.lineno}: {exc.msg}") from exc


def excel_library():
    try:
        import openpyxl
    except ImportError as exc:
        raise TaskgenError("Excel support requires: python -m pip install '.[excel]'") from exc
    return openpyxl


def decode_rows(rows):
    result = []
    for index, row in enumerate(rows, start=2):
        if not row or not any(v not in (None, "") for v in row.values()):
            continue
        if None in row:
            raise TaskgenError(f"Row {index}: extra CSV cells; quote JSON columns correctly")
        record = {str(k).strip(): v for k, v in row.items() if v not in (None, "")}
        for field in JSON_COLUMNS & record.keys():
            if isinstance(record[field], str):
                try:
                    record[field] = json.loads(record[field], **json_options())
                except json.JSONDecodeError as exc:
                    raise TaskgenError(f"Row {index}: {field} must contain valid JSON") from exc
        if "variants_per_task" in record:
            try:
                raw = record["variants_per_task"]
                if type(raw) is bool or str(raw).strip() != str(int(raw)):
                    raise ValueError
                record["variants_per_task"] = int(raw)
            except (ValueError, TypeError) as exc:
                raise TaskgenError(f"Row {index}: variants_per_task must be an integer") from exc
        result.append(record)
    return result


def load_tasks(path):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = read_json(path)
        if isinstance(data, dict):
            data = data.get("tasks")
        if not isinstance(data, list):
            raise TaskgenError("Input JSON must be a list or an object with a tasks list")
        return data
    if suffix in (".csv", ".xlsx"):
        return decode_rows(read_table(path))
    raise TaskgenError("Atomic task input must be .json, .csv or .xlsx")


def read_table(path):
    """Read plain cells without interpreting JSON columns or client wording."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            names = [n.strip() for n in (reader.fieldnames or [])]
            if not names or not all(names) or len(names) != len(set(names)):
                raise TaskgenError("CSV needs unique column headers")
            reader.fieldnames = names
            rows = list(reader)
            if any(None in row for row in rows):
                raise TaskgenError("Extra CSV cells; quote cells containing commas")
            return rows
    if path.suffix.lower() == ".xlsx":
        library = excel_library()
        workbook = library.load_workbook(path, read_only=True, data_only=False)
        try:
            sheet = workbook.worksheets[0]
            rows = iter(sheet.iter_rows())
            header = next(rows, ())
            if not header or any(cell.data_type == "f" for cell in header):
                raise TaskgenError("Excel needs plain-text column headers")
            names = [cell.value.strip() if isinstance(cell.value, str) else cell.value for cell in header]
            if any(not isinstance(n, str) or not n.strip() for n in names) or len(set(names)) != len(names):
                raise TaskgenError("Excel headers must be nonempty, unique strings")
            values = []
            for row in rows:
                if any(cell.data_type == "f" for cell in row):
                    raise TaskgenError("Excel formulas are not evaluated; provide literal cell values")
                values.append(dict(zip(names, [cell.value for cell in row])))
            return values
        finally:
            workbook.close()
    raise TaskgenError("Table must be .csv or .xlsx")


def write_table(rows, columns, path):
    """Write a human-facing table; write to a temporary file before replacement."""
    path = Path(path)
    if path.suffix.lower() not in (".csv", ".xlsx"):
        raise TaskgenError("Table output must be .csv or .xlsx")
    library = excel_library() if path.suffix.lower() == ".xlsx" else None
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".taskgen-", dir=path.parent) as folder:
        temporary = Path(folder) / path.name
        if library is None:
            with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
                writer.writeheader()
                writer.writerows({c: spreadsheet_safe(row.get(c, "")) for c in columns} for row in rows)
        else:
            from openpyxl.worksheet.datavalidation import DataValidation
            workbook = library.Workbook()
            sheet = workbook.active
            sheet.title = "Tasks"
            sheet.append(columns)
            for row in rows:
                sheet.append([spreadsheet_safe(row.get(c, "")) for c in columns])
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            for index, column in enumerate(columns, start=1):
                letter = sheet.cell(1, index).column_letter
                sheet.column_dimensions[letter].width = 52 if column in ("任务指令", "初始状态", "目标状态", "现场准备", "修改意见") else 22
                if column in ("指纹", "批次标识"):
                    sheet.column_dimensions[letter].hidden = True
            if "审核结论" in columns and rows:
                column = sheet.cell(1, columns.index("审核结论") + 1).column_letter
                validation = DataValidation(type="list", formula1='"待审核,通过,退回"', allow_blank=False)
                validation.errorTitle = "请选择审核结论"
                validation.error = "待审核、通过或退回"
                validation.showErrorMessage = True
                validation.errorStyle = "stop"
                sheet.add_data_validation(validation)
                validation.add(f"{column}2:{column}{len(rows) + 1}")
            workbook.save(temporary)
            workbook.close()
        os.replace(temporary, path)


def load_variants(path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        value = read_json(path)
        if not isinstance(value, list):
            raise TaskgenError("Variant JSON must be a list")
        return value
    if path.suffix.lower() != ".jsonl":
        raise TaskgenError("Validate the canonical .jsonl/.json output, not the spreadsheet view")
    records = []
    with path.open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, start=1):
            if line.strip():
                try:
                    records.append(json.loads(line, **json_options()))
                except json.JSONDecodeError as exc:
                    raise TaskgenError(f"Invalid JSONL at line {number}: {exc.msg}") from exc
    return records


def platform_row(record):
    labels = dict(record["labels"], project=record["project"])
    return {
        "Task Name": record["task_name"], "Description": record["description"],
        "Task Goal": canonical(record["task_goal"]), "Initial State": record["initial_state"],
        "Generalization Axes": ";".join(record["changed_axes"]),
        "Task Type": labels["task_type"], "Environment": record["environment"],
        "Label": canonical(labels), "Task ID": record["parent_task_id"],
        "Variant ID": record["variant_id"], "Scene ID": record["scene_id"],
        "Reset Instruction": record["reset_instruction"],
        "Parameters": canonical(record["parameters"]), "Fingerprint": record["fingerprint"],
        "Requires Human Review": "true",
    }


def spreadsheet_safe(value):
    text = str(value)
    # Prevent an imported instruction/task name from becoming an Excel formula.
    if text and (text[0] in "\t\r\n" or text.lstrip().startswith(("=", "+", "-", "@"))):
        return "'" + text
    return text


def write_outputs(records, report, outdir, xlsx=False):
    library = excel_library() if xlsx else None
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    names = ["tasks.jsonl", "platform_tasks.csv", "report.json"]
    rows = [platform_row(record) for record in records]
    with TemporaryDirectory(prefix=".taskgen-", dir=outdir) as folder:
        temporary = Path(folder)
        with (temporary / "tasks.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for record in records:
                stream.write(canonical(record) + "\n")
        with (temporary / "platform_tasks.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=PLATFORM_COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows({key: spreadsheet_safe(value) for key, value in row.items()} for row in rows)
        (temporary / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        if xlsx:
            workbook = library.Workbook()
            sheet = workbook.active
            sheet.title = "Tasks"
            sheet.append(PLATFORM_COLUMNS)
            for row in rows:
                sheet.append([spreadsheet_safe(row[column]) for column in PLATFORM_COLUMNS])
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            workbook.save(temporary / "platform_tasks.xlsx")
            workbook.close()
            names.append("platform_tasks.xlsx")
        # Only replace this tool's named outputs, never clear the supplied folder.
        for name in names:
            os.replace(temporary / name, outdir / name)
    return [str(outdir / name) for name in names]
