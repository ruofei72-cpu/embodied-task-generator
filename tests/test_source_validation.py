"""Check source-row identity and preservation across AI-generated task data."""

import copy
import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.validate_tasks import load_source, load_tasks, validate_source  # noqa: E402


class SourceValidationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "source.csv"

    def write_rows(self, rows):
        with self.path.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(["Task ID", "Task Name", "Variant Instruction"])
            writer.writerows(rows)
        return self.path

    def test_shipped_source_matches_both_output_formats(self):
        variants = load_source(ROOT / "input_example.csv")
        self.assertEqual(["t1", "t1"], [item["task_id"] for item in variants])
        for suffix in ("json", "csv"):
            tasks = load_tasks(ROOT / f"output_example.{suffix}")
            self.assertEqual([], validate_source(tasks, variants))

    def test_group_identity_is_forward_filled_without_cross_group_leak(self):
        source = self.write_rows([
            ["a", "Sort", "variant_01: Move two boxes"],
            ["", "", "VARIANT_02: Move three boxes"],
            ["b", "Store", "Move a bottle"],
            ["b", "", "Move two bottles"],
        ])
        variants = load_source(source)
        self.assertEqual(["a", "a", "b", "b"], [v["task_id"] for v in variants])
        self.assertEqual(["Sort", "Sort", "Store", "Store"],
                         [v["task_name"] for v in variants])
        self.assertEqual("Move two boxes", variants[0]["description"])
        self.assertEqual("Move three boxes", variants[1]["description"])

    def test_new_group_without_name_cannot_inherit_previous_name(self):
        source = self.write_rows([["a", "Sort", "Move a box"],
                                  ["b", "", "Move a bottle"]])
        with self.assertRaisesRegex(ValueError, "new Task ID requires Task Name"):
            load_source(source)

    def test_first_group_requires_identity(self):
        source = self.write_rows([["", "Sort", "Move a box"]])
        with self.assertRaisesRegex(ValueError, "first task group"):
            load_source(source)

    def test_conflicting_names_are_rejected(self):
        source = self.write_rows([["a", "Sort", "Move a box"],
                                  ["", "Different name", "Move a bottle"]])
        with self.assertRaisesRegex(ValueError, "conflicts"):
            load_source(source)

    def test_reappearing_ids_cannot_change_names(self):
        source = self.write_rows([["a", "Sort", "Move a box"],
                                  ["b", "Store", "Move a bottle"],
                                  ["a", "Different name", "Move a cup"]])
        with self.assertRaisesRegex(ValueError, "earlier Task ID"):
            load_source(source)

    def test_partially_filled_rows_are_not_silently_discarded(self):
        for instruction in ("", "variant_01: "):
            source = self.write_rows([["a", "Sort", instruction]])
            with self.subTest(instruction=instruction), self.assertRaisesRegex(
                    ValueError, "Variant Instruction must not be empty"):
                load_source(source)
        source = self.write_rows([["", "", ""], ["a", "Sort", "Move a box"]])
        self.assertEqual(1, len(load_source(source)))

    def test_bad_source_headers_column_counts_and_empty_input_are_rejected(self):
        cases = [
            ("Task ID,Task ID,Variant Instruction\n", "unique headers"),
            ("Task ID,Task Name\n", "unique headers"),
            ("Task ID,Task Name,Variant Instruction\na,Sort\n", "column count"),
            ("Task ID,Task Name,Variant Instruction\n", "at least one task"),
        ]
        for text, message in cases:
            with self.subTest(message=message):
                self.path.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    load_source(self.path)

    def test_missing_reordered_and_rewritten_tasks_are_detected(self):
        variants = load_source(ROOT / "input_example.csv")
        tasks = load_tasks(ROOT / "output_example.json")
        self.assertTrue(validate_source(tasks[:1], variants))
        self.assertTrue(validate_source(list(reversed(tasks)), variants))
        changed = copy.deepcopy(tasks)
        changed[0]["description"] = changed[0]["description"].replace("2 boxes", "20 boxes")
        self.assertTrue(validate_source(changed, variants))
        self.assertEqual([], validate_source(tasks, variants))

    def test_cli_source_check_works_with_json_and_csv(self):
        for suffix in ("json", "csv"):
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts/validate_tasks.py"),
                str(ROOT / f"output_example.{suffix}"),
                "--source", str(ROOT / "input_example.csv"),
            ], cwd=self.path.parent, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
