"""Exercise task contract failures and the shipped JSON/CSV data boundary."""

import copy
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.validate_tasks import (  # noqa: E402
    CSV_FIELDS, load_config, load_tasks, parse_json, validate_tasks,
)


class TaskValidationTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(ROOT / "config.example.json")
        self.tasks = load_tasks(ROOT / "output_example.json")

    def assert_invalid(self, tasks, message):
        self.assertTrue(any(message in error
                            for error in validate_tasks(tasks, self.config)))

    def test_shipped_json_and_csv_are_valid_and_lossless(self):
        csv_tasks = load_tasks(ROOT / "output_example.csv")
        self.assertEqual(self.tasks, csv_tasks)
        self.assertEqual([], validate_tasks(self.tasks, self.config))
        self.assertEqual([], validate_tasks(csv_tasks, self.config))

    def test_examples_preserve_each_input_instruction(self):
        with (ROOT / "input_example.csv").open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), len(self.tasks))
        for row, task in zip(rows, self.tasks):
            self.assertEqual(row["Variant Instruction"].split(": ", 1)[1],
                             task["description"])

    def test_all_required_fields_are_checked(self):
        for field in self.tasks[0]:
            with self.subTest(field=field):
                tasks = copy.deepcopy(self.tasks)
                del tasks[0][field]
                self.assert_invalid(tasks, "missing fields: " + field)

    def test_string_fields_cannot_be_blank(self):
        self.tasks[0]["initial_state"] = "  "
        self.assert_invalid(self.tasks, "initial_state: must be a nonempty string")

    def test_boolean_strings_and_numbers_are_rejected(self):
        for field in ("tool_use", "fine_tool_use"):
            for value in ("false", "true", 0, 1, None):
                with self.subTest(field=field, value=value):
                    tasks = copy.deepcopy(self.tasks)
                    tasks[0][field] = value
                    self.assert_invalid(tasks, field + ": must be a JSON boolean")

    def test_all_enum_fields_use_the_selected_configuration(self):
        for field in ("environment", "task_type", "dexterity_level",
                      "estimated_duration"):
            with self.subTest(field=field):
                tasks = copy.deepcopy(self.tasks)
                tasks[0][field] = "unknown"
                self.assert_invalid(tasks, field + ": not allowed")
        self.tasks[0]["estimated_duration"] = "≤4min"
        self.assert_invalid(self.tasks, "estimated_duration: not allowed")
        self.config["duration_buckets"].append("≤4min")
        self.assertEqual([], validate_tasks(self.tasks, self.config))

    def test_goals_must_be_nonempty_string_arrays(self):
        for value in ([], "goal", [""], [1], None):
            with self.subTest(value=value):
                tasks = copy.deepcopy(self.tasks)
                tasks[0]["task_goal"] = value
                self.assert_invalid(tasks, "task_goal: must be a nonempty string array")

    def test_fine_tool_requires_a_tool(self):
        self.tasks[0]["fine_tool_use"] = True
        self.assert_invalid(self.tasks, "fine_tool_use=true requires tool_use=true")
        self.tasks[0]["tool_use"] = True
        self.assertEqual([], validate_tasks(self.tasks, self.config))

    def test_duplicate_description_is_reported_without_mutation(self):
        self.tasks[1]["description"] = "  " + self.tasks[0]["description"].upper() + "  "
        before = copy.deepcopy(self.tasks)
        self.assert_invalid(self.tasks, "description: exact duplicate candidate")
        self.assertEqual(before, self.tasks)

    def test_duplicate_name_is_reported(self):
        self.tasks[1]["task_name"] = self.tasks[0]["task_name"]
        self.assert_invalid(self.tasks, "task_name: exact duplicate candidate")

    def test_extra_fields_and_bad_root_shapes_are_rejected(self):
        self.tasks[0]["unexpected"] = "value"
        self.assert_invalid(self.tasks, "unknown fields: unexpected")
        for value in ([], {}, None):
            self.assert_invalid(value, "nonempty JSON task array")
        self.assert_invalid([1], "must be an object")

    def test_ambiguous_or_nonstandard_json_is_rejected(self):
        for text in ('{"tool_use":true,"tool_use":false}', '[NaN]', '[Infinity]'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_json(text)

    def test_invalid_csv_boundaries_are_rejected(self):
        header = ",".join(CSV_FIELDS) + "\n"
        cases = [
            ("bad header\n", "CSV headers"),
            (header + "short,row\n", "column count"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.csv"
            for text, message in cases:
                with self.subTest(message=message):
                    path.write_text(text, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, message):
                        load_tasks(path)

    def test_label_validation_applies_inside_csv(self):
        with (ROOT / "output_example.csv").open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        label = json.loads(rows[0]["Label"])
        label["tool_use"] = "false"
        rows[0]["Label"] = json.dumps(label)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.csv"
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(CSV_FIELDS))
                writer.writeheader()
                writer.writerows(rows)
            self.assert_invalid(load_tasks(path), "tool_use: must be a JSON boolean")
            label["unexpected"] = 1
            rows[0]["Label"] = json.dumps(label)
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(CSV_FIELDS))
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(ValueError, "Label must contain exactly"):
                load_tasks(path)

    def test_invalid_config_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            for value in ([], {"schema_version": "1.0"},
                          {**self.config, "duration_buckets": []},
                          {**self.config, "supported_environments": ["home", "home"]}):
                with self.subTest(config=value):
                    path.write_text(json.dumps(value), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_config(path)

    def test_cli_works_outside_repo_and_does_not_rewrite_input(self):
        script = ROOT / "scripts/validate_tasks.py"
        with tempfile.TemporaryDirectory() as tmp:
            good = subprocess.run([sys.executable, str(script),
                                   str(ROOT / "output_example.json")],
                                  cwd=tmp, capture_output=True, text=True)
            self.assertEqual(0, good.returncode, good.stderr)
            path = Path(tmp) / "bad.json"
            original = '[{"tool_use":"false"}]'
            path.write_text(original, encoding="utf-8")
            bad = subprocess.run([sys.executable, str(script), str(path)],
                                 cwd=tmp, capture_output=True, text=True)
            self.assertEqual(1, bad.returncode)
            self.assertIn("must be a JSON boolean", bad.stderr)
            self.assertNotIn("Traceback", bad.stderr)
            self.assertEqual(original, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
