import contextlib
import csv
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

from taskgen.cli import main
from taskgen.engine import TaskgenError, generate_tasks
from taskgen.io import JSON_COLUMNS, load_tasks, load_variants, read_json, write_outputs


ROOT = Path(__file__).resolve().parents[1]


class IOAndCLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.config = json.loads((ROOT / "config.example.json").read_text())
        self.tasks = json.loads((ROOT / "examples/atomic_tasks.json").read_text())
        self.input = self.folder / "tasks.json"
        self.input.write_text(json.dumps(self.tasks, ensure_ascii=False), encoding="utf-8")
        self.config_file = self.folder / "config.json"
        self.config_file.write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")

    def call_cli(self, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_json_and_csv_atomic_inputs_generate_same_records(self):
        path = self.folder / "tasks.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(self.tasks[0]))
            writer.writeheader()
            for task in self.tasks:
                writer.writerow({k: json.dumps(v, ensure_ascii=False) if k in JSON_COLUMNS else v
                                 for k, v in task.items()})
        from_csv = load_tasks(path)
        self.assertEqual(from_csv, self.tasks)
        self.assertEqual(generate_tasks(from_csv, self.config), generate_tasks(load_tasks(self.input), self.config))

    def test_output_jsonl_and_platform_mapping(self):
        records, report = generate_tasks(self.tasks, self.config)
        write_outputs(records, report, self.folder / "output")
        self.assertEqual(load_variants(self.folder / "output/tasks.jsonl"), records)
        with (self.folder / "output/platform_tasks.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 14)
        self.assertEqual(json.loads(rows[0]["Label"])["tool_use"], False)
        self.assertEqual(rows[0]["Task ID"], records[0]["parent_task_id"])
        self.assertEqual(rows[0]["Reset Instruction"], records[0]["reset_instruction"])
        self.assertEqual(rows[0]["Requires Human Review"], "true")

    def test_spreadsheet_formula_protection_preserves_canonical_text(self):
        self.tasks[0]["task_name"] = '=HYPERLINK("https://example.invalid")'
        records, report = generate_tasks(self.tasks, self.config)
        write_outputs(records, report, self.folder / "output")
        with (self.folder / "output/platform_tasks.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertTrue(rows[0]["Task Name"].startswith("'="))
        self.assertTrue(load_variants(self.folder / "output/tasks.jsonl")[0]["task_name"].startswith("="))

    def test_output_writer_leaves_unrelated_files_alone(self):
        marker = self.folder / "keep.txt"
        marker.write_text("keep me")
        records, report = generate_tasks(self.tasks, self.config)
        write_outputs(records, report, self.folder)
        write_outputs(records, report, self.folder)
        self.assertEqual(marker.read_text(), "keep me")
        self.assertFalse(list(self.folder.glob(".taskgen-*")))

    def test_cli_generate_and_validate(self):
        output = self.folder / "output"
        code, text, error = self.call_cli([
            "generate", "--input", str(self.input), "--config", str(self.config_file), "--output", str(output)])
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(json.loads(text)["generated_variants"], 14)
        code, text, _ = self.call_cli([
            "validate", "--input", str(output / "tasks.jsonl"), "--config", str(self.config_file)])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(text)["valid"])

    def test_cli_cross_mode(self):
        code, text, error = self.call_cli([
            "generate", "--input", str(self.input), "--config", str(self.config_file),
            "--mode", "cross", "--output", str(self.folder / "cross")])
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(json.loads(text)["generated_variants"], 14)

    def test_partial_batch_exit_code_and_report(self):
        self.tasks[1]["scene_id"] = "unknown"
        self.input.write_text(json.dumps(self.tasks), encoding="utf-8")
        output = self.folder / "partial"
        code, _, error = self.call_cli([
            "generate", "--input", str(self.input), "--config", str(self.config_file), "--output", str(output)])
        self.assertEqual(code, 1)
        self.assertIn("rejected", error)
        self.assertEqual(read_json(output / "report.json")["summary"]["generated_variants"], 7)

    def test_invalid_config_exit_two_and_no_output(self):
        self.config_file.write_text('{"schema_version":"1.0"}', encoding="utf-8")
        output = self.folder / "must-not-exist"
        code, _, error = self.call_cli([
            "generate", "--input", str(self.input), "--config", str(self.config_file), "--output", str(output)])
        self.assertEqual(code, 2)
        self.assertIn("schema_version", error)
        self.assertFalse(output.exists())

    def test_malformed_json_and_duplicate_keys(self):
        for content in ('{"task":', '{"task":1,"task":2}', '{"number":NaN}'):
            with self.subTest(content=content):
                self.input.write_text(content)
                with self.assertRaises(TaskgenError):
                    read_json(self.input)

    def test_bad_csv_headers_and_json_cells(self):
        path = self.folder / "bad.csv"
        for text in ('task_id, task_id\na,b\n', 'task_id,parameters\na,not-json\n', 'task_id,\na,b\n'):
            with self.subTest(text=text):
                path.write_text(text)
                with self.assertRaises(TaskgenError):
                    load_tasks(path)

    def test_validator_nonzero_exit_for_truncated_output(self):
        records, report = generate_tasks(self.tasks, self.config)
        output = self.folder / "truncated"
        write_outputs(records[:-1], report, output)
        code, text, _ = self.call_cli([
            "validate", "--input", str(output / "tasks.jsonl"), "--config", str(self.config_file)])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(text)["valid"])

    @unittest.skipUnless(importlib.util.find_spec("openpyxl"), "Optional Excel dependency not installed")
    def test_excel_input_output_and_formula_rejection(self):
        import openpyxl
        path = self.folder / "tasks.xlsx"
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        keys = list(self.tasks[0])
        sheet.append(keys)
        for task in self.tasks:
            sheet.append([json.dumps(task[k], ensure_ascii=False) if k in JSON_COLUMNS else task[k] for k in keys])
        workbook.save(path)
        self.assertEqual(load_tasks(path), self.tasks)
        records, report = generate_tasks(self.tasks, self.config)
        write_outputs(records, report, self.folder / "xlsx", xlsx=True)
        exported = openpyxl.load_workbook(self.folder / "xlsx/platform_tasks.xlsx")
        self.assertEqual(exported.active.max_row, 15)
        self.assertEqual(exported.active.freeze_panes, "A2")
        exported.close()
        sheet.cell(2, 2, '=HYPERLINK("https://example.invalid")')
        workbook.save(path)
        workbook.close()
        with self.assertRaisesRegex(TaskgenError, "formulas"):
            load_tasks(path)


if __name__ == "__main__":
    unittest.main()
