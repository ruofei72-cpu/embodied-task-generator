#!/usr/bin/env python3
"""Validate canonical task JSON and export a new UTF-8 CSV import template."""

import argparse
import csv
import io
import json
import sys
from pathlib import Path

try:
    from .validate_tasks import (CSV_FIELDS, LABEL_FIELDS, ROOT, load_config,
                                 load_source, load_tasks, validate_source, validate_tasks)
except ImportError:  # Direct execution: python scripts/export_tasks.py ...
    from validate_tasks import (CSV_FIELDS, LABEL_FIELDS, ROOT, load_config,
                                load_source, load_tasks, validate_source, validate_tasks)


def write_csv(tasks, output):
    """Serialize canonical fields; exclusive creation preserves existing files."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(CSV_FIELDS), lineterminator="\n")
    writer.writeheader()
    for task in tasks:
        row = {column: task[field] for column, field in CSV_FIELDS.items()
               if field is not None}
        row["Task Goal"] = json.dumps(task["task_goal"], ensure_ascii=False)
        row["Label"] = json.dumps({field: task[field] for field in sorted(LABEL_FIELDS)},
                                  ensure_ascii=False)
        writer.writerow(row)
    # BOM keeps Chinese text readable when opened directly in common Excel versions.
    # The validator accepts both plain UTF-8 and UTF-8 with BOM.
    with Path(output).open("x", encoding="utf-8-sig", newline="") as stream:
        stream.write(buffer.getvalue())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Canonical .json task array")
    parser.add_argument("output", type=Path, help="New .csv file (existing files are refused)")
    parser.add_argument("--config", type=Path, default=ROOT / "config.example.json")
    parser.add_argument("--source", type=Path, help="Original task-variant CSV to compare")
    args = parser.parse_args(argv)
    try:
        if args.input.suffix.lower() != ".json" or args.output.suffix.lower() != ".csv":
            raise ValueError("Use a .json input and a .csv output")
        config = load_config(args.config)
        tasks = load_tasks(args.input)
        errors = validate_tasks(tasks, config)
        if args.source is not None:
            variants = load_source(args.source)
            if not errors:
                errors.extend(validate_source(tasks, variants))
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        write_csv(tasks, args.output)
    except FileExistsError:
        print(f"Export failed: {args.output} already exists; choose a new output path.",
              file=sys.stderr)
        return 1
    except (OSError, UnicodeError, ValueError, csv.Error) as error:
        print(f"Export failed: {error}", file=sys.stderr)
        return 1
    print(f"Exported {len(tasks)} tasks to {args.output}. "
          "Semantic and platform review still required.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
