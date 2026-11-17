"""Command-line entry points. Exit: 0 success, 1 task/record rejection, 2 input/IO error."""

import argparse
import json
import sys
from zipfile import BadZipFile

from . import __version__
from .engine import TaskgenError, generate_tasks, validate_variants
from .io import load_tasks, load_variants, read_json, write_outputs
from .workflow import (export_import_results, export_review, finalize_review,
                       import_customer_sheet, write_delivery)


def parser():
    root = argparse.ArgumentParser(description="Generate validated embodied task variations without an LLM API.")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="Generate atomic-task variants and a report")
    generate.add_argument("--input", required=True, help="Atomic task .json, .csv or .xlsx")
    generate.add_argument("--config", required=True, help="Rule configuration JSON")
    generate.add_argument("--output", required=True, help="Directory for named outputs; existing named outputs are replaced")
    generate.add_argument("--mode", choices=("single-axis", "cross"), help="Override the configured generation mode")
    generate.add_argument("--variants-per-task", type=int, help="Override default quota (explicit task quotas still take precedence)")
    generate.add_argument("--xlsx", action="store_true", help="Also export Excel; requires the excel extra")
    validate = commands.add_parser("validate", help="Validate canonical generated .jsonl/.json records")
    validate.add_argument("--input", required=True)
    validate.add_argument("--config", required=True)
    customer = commands.add_parser("import-sheet", help="Map ordinary customer CSV/XLSX columns to atomic task definitions")
    customer.add_argument("--input", required=True)
    customer.add_argument("--mapping", required=True, help="Column names, scene aliases and task templates")
    customer.add_argument("--config", required=True)
    customer.add_argument("--output", required=True)
    review = commands.add_parser("create-review", help="Create a pending-review CSV/XLSX with source binding")
    review.add_argument("--input", required=True, help="Canonical variants JSONL/JSON")
    review.add_argument("--config", required=True)
    review.add_argument("--output", required=True, help="Review .csv or .xlsx file")
    finalize = commands.add_parser("finalize", help="Export only complete, explicitly approved task groups")
    finalize.add_argument("--input", required=True, help="Unmodified canonical variants JSONL/JSON")
    finalize.add_argument("--review", required=True, help="Completed review CSV/XLSX")
    finalize.add_argument("--config", required=True)
    finalize.add_argument("--output", required=True)
    finalize.add_argument("--xlsx", action="store_true")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        config = read_json(args.config)
        if args.command == "import-sheet":
            tasks, rejected, report = import_customer_sheet(args.input, read_json(args.mapping), config)
            export_import_results(tasks, rejected, report, args.output)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1 if rejected else 0
        if args.command == "create-review":
            records = load_variants(args.input)
            export_review(records, config, args.output)
            print(json.dumps({"review_file": args.output, "pending_variants": len(records)}, ensure_ascii=False))
            return 0
        if args.command == "finalize":
            delivery, report, audit = finalize_review(load_variants(args.input), config, args.review)
            write_delivery(delivery, report, audit, args.output, xlsx=args.xlsx)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1 if report["withheld_groups"] else 0
        if args.command == "validate":
            issues = validate_variants(load_variants(args.input), config)
            print(json.dumps({"valid": not issues, "errors": issues}, ensure_ascii=False, indent=2))
            return 1 if issues else 0
        if not isinstance(config, dict):
            raise TaskgenError("Config must be a JSON object")
        if args.mode is not None:
            config["mode"] = args.mode
        if args.variants_per_task is not None:
            config["variants_per_task"] = args.variants_per_task
        records, report = generate_tasks(load_tasks(args.input), config)
        if records:
            issues = validate_variants(records, config)
            if issues:
                raise TaskgenError("Generated record validation failed: " + "; ".join(issues))
        paths = write_outputs(records, report, args.output, xlsx=args.xlsx)
        print(json.dumps({"status": report["status"], **report["summary"], "outputs": paths},
                         ensure_ascii=False, indent=2))
        if report["summary"]["rejected_tasks"]:
            print("Some atomic tasks were rejected. See report.json; incomplete groups were not exported.", file=sys.stderr)
            return 1
        return 0
    except (TaskgenError, OSError, UnicodeError, BadZipFile) as exc:
        print(f"taskgen: {exc}", file=sys.stderr)
        return 2
