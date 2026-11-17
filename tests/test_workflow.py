from copy import deepcopy
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

from taskgen.cli import main
from taskgen.engine import TaskgenError, generate_tasks
from taskgen.io import read_table, write_outputs, write_table
from taskgen.workflow import (REVIEW_COLUMNS, export_import_results, export_review,
                             finalize_review, import_customer_sheet, review_rows, write_delivery)


ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.config = json.loads((ROOT / "config.example.json").read_text())
        self.tasks = json.loads((ROOT / "examples/atomic_tasks.json").read_text())
        self.mapping = json.loads((ROOT / "examples/customer_mapping.json").read_text())
        self.customer = ROOT / "examples/customer_tasks.csv"
        self.records, self.report = generate_tasks(self.tasks, self.config)
        self.review = self.folder / "review.csv"

    def write_decisions(self, status="通过"):
        rows = review_rows(self.records)
        for row in rows:
            row["审核结论"] = status
            if status == "退回":
                row["修改意见"] = "Synthetic test decision: review the setup"
        write_table(rows, REVIEW_COLUMNS, self.review)
        return rows

    def test_flat_customer_columns_match_structured_inputs(self):
        tasks, rejected, report = import_customer_sheet(self.customer, self.mapping, self.config)
        self.assertEqual(tasks, self.tasks)
        self.assertEqual(rejected, [])
        self.assertEqual(report["accepted_rows"], 2)
        self.assertEqual(generate_tasks(tasks, self.config), generate_tasks(self.tasks, self.config))

    def test_three_column_sheet_reports_missing_information_without_guessing(self):
        path = self.folder / "incomplete.csv"
        rows = [{"场景": "厨房", "原子任务编号": "kitchen-001", "原始指令": "把杯子放进托盘"}]
        write_table(rows, list(rows[0]), path)
        tasks, rejected, report = import_customer_sheet(path, self.mapping, self.config)
        self.assertEqual(tasks, [])
        self.assertEqual(report["status"], "failed")
        self.assertIn("Missing mapped columns", rejected[0]["导入错误"])
        export_import_results(tasks, rejected, report, self.folder / "import")
        self.assertTrue((self.folder / "import/incomplete_rows.csv").exists())

    def test_blank_parameter_is_not_filled_from_template(self):
        rows = read_table(self.customer)
        rows[0]["颜色"] = ""
        path = self.folder / "missing-value.csv"
        write_table(rows, list(rows[0]), path)
        tasks, rejected, report = import_customer_sheet(path, self.mapping, self.config)
        self.assertEqual(len(tasks), 1)
        self.assertIn("颜色", rejected[0]["导入错误"])
        self.assertEqual(report["status"], "partial")

    def test_unknown_template_and_duplicate_source_id_are_rejected(self):
        rows = read_table(self.customer)
        rows[0]["任务模板"] = "unknown"
        path = self.folder / "bad-template.csv"
        write_table(rows, list(rows[0]), path)
        _, rejected, _ = import_customer_sheet(path, self.mapping, self.config)
        self.assertIn("Unknown task template", rejected[0]["导入错误"])
        rows = read_table(self.customer)
        rows[1]["原子任务编号"] = rows[0]["原子任务编号"]
        write_table(rows, list(rows[0]), path)
        _, rejected, _ = import_customer_sheet(path, self.mapping, self.config)
        self.assertIn("Duplicate", rejected[0]["导入错误"])

    def test_review_defaults_pending_and_does_not_release_tasks(self):
        export_review(self.records, self.config, self.review)
        delivery, report, audit = finalize_review(self.records, self.config, self.review)
        self.assertEqual(delivery, [])
        self.assertEqual(report["decisions"], {"pending": 14})
        self.assertEqual(len(audit["decisions"]), 14)

    def test_complete_approval_releases_all_tasks(self):
        self.write_decisions()
        delivery, report, audit = finalize_review(self.records, self.config, self.review)
        self.assertEqual(len(delivery), 14)
        self.assertEqual(report["status"], "complete")
        self.assertTrue(all(d["status"] == "approved" for d in audit["decisions"].values()))
        write_delivery(delivery, report, audit, self.folder / "delivery")
        self.assertEqual(len(read_table(self.folder / "delivery/delivery.csv")), 14)

    def test_rejection_withholds_entire_atomic_group(self):
        rows = self.write_decisions()
        rows[0].update(审核结论="退回", 修改意见="Synthetic test: object not available")
        write_table(rows, REVIEW_COLUMNS, self.review)
        delivery, report, audit = finalize_review(self.records, self.config, self.review)
        self.assertEqual(len(delivery), 7)
        self.assertEqual(report["withheld_groups"][0]["rejected"], 1)
        self.assertTrue(all(r["原子任务编号"] == "workshop-001" for r in delivery))
        self.assertIn("object not available", audit["decisions"][rows[0]["变体编号"]]["notes"])

    def test_missing_review_row_is_pending_not_implicitly_approved(self):
        rows = self.write_decisions()
        write_table(rows[1:], REVIEW_COLUMNS, self.review)
        delivery, report, _ = finalize_review(self.records, self.config, self.review)
        self.assertEqual(len(delivery), 7)
        self.assertEqual(report["decisions"]["pending"], 1)

    def test_changed_instruction_or_stale_batch_cannot_reuse_approval(self):
        for field in ("任务指令", "批次标识", "指纹", "原始指令"):
            with self.subTest(field=field):
                rows = self.write_decisions()
                rows[0][field] += "changed"
                write_table(rows, REVIEW_COLUMNS, self.review)
                with self.assertRaisesRegex(TaskgenError, "changed or belongs"):
                    finalize_review(self.records, self.config, self.review)

    def test_duplicate_unknown_and_invalid_decisions_fail_clearly(self):
        rows = self.write_decisions()
        write_table(rows + rows[:1], REVIEW_COLUMNS, self.review)
        with self.assertRaisesRegex(TaskgenError, "duplicate variant"):
            finalize_review(self.records, self.config, self.review)
        rows[0]["变体编号"] = "unknown"
        write_table(rows, REVIEW_COLUMNS, self.review)
        with self.assertRaisesRegex(TaskgenError, "unknown variant"):
            finalize_review(self.records, self.config, self.review)
        rows = self.write_decisions()
        rows[0]["审核结论"] = "maybe"
        write_table(rows, REVIEW_COLUMNS, self.review)
        with self.assertRaisesRegex(TaskgenError, "decision must"):
            finalize_review(self.records, self.config, self.review)

    def test_returned_task_requires_an_actionable_reason(self):
        rows = self.write_decisions()
        rows[0]["审核结论"] = "退回"
        write_table(rows, REVIEW_COLUMNS, self.review)
        with self.assertRaisesRegex(TaskgenError, "needs a reason"):
            finalize_review(self.records, self.config, self.review)

    def test_review_rows_can_be_reordered(self):
        rows = self.write_decisions()
        write_table(list(reversed(rows)), REVIEW_COLUMNS, self.review)
        delivery, _, _ = finalize_review(self.records, self.config, self.review)
        self.assertEqual(len(delivery), 14)

    def test_observed_coverage_does_not_claim_all_configured_values(self):
        color = self.report["coverage"]["axes"]["color"]
        self.assertEqual(color["changed_values_observed"], ["蓝色"])
        self.assertIn("绿色", color["configured_values"])
        self.assertEqual(color["changed_variant_count"], 2)
        config = deepcopy(self.config)
        config["mode"] = "cross"
        _, report = generate_tasks(self.tasks, config)
        self.assertTrue(report["coverage"]["observed_axis_pairs"])

    def test_cli_customer_to_review_to_delivery(self):
        imported = self.folder / "imported"
        generated = self.folder / "generated"
        def call(*args):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return main(list(args))
        self.assertEqual(call("import-sheet", "--input", str(self.customer), "--mapping", str(ROOT / "examples/customer_mapping.json"),
                              "--config", str(ROOT / "config.example.json"), "--output", str(imported)), 0)
        self.assertEqual(call("generate", "--input", str(imported / "imported_tasks.json"), "--config", str(ROOT / "config.example.json"),
                              "--output", str(generated)), 0)
        source = generated / "tasks.jsonl"
        self.assertEqual(call("create-review", "--input", str(source), "--config", str(ROOT / "config.example.json"),
                              "--output", str(self.review)), 0)
        self.assertEqual(call("finalize", "--input", str(source), "--review", str(self.review), "--config", str(ROOT / "config.example.json"),
                              "--output", str(self.folder / "pending")), 1)
        self.write_decisions()
        self.assertEqual(call("finalize", "--input", str(source), "--review", str(self.review), "--config", str(ROOT / "config.example.json"),
                              "--output", str(self.folder / "approved")), 0)

    @unittest.skipUnless(importlib.util.find_spec("openpyxl"), "Optional Excel dependency not installed")
    def test_excel_flat_import_and_review_dropdown_roundtrip(self):
        import openpyxl
        source = self.folder / "customer.xlsx"
        rows = read_table(self.customer)
        write_table(rows, list(rows[0]), source)
        tasks, rejected, _ = import_customer_sheet(source, self.mapping, self.config)
        self.assertEqual(tasks, self.tasks)
        self.assertFalse(rejected)
        review = self.folder / "review.xlsx"
        export_review(self.records, self.config, review)
        book = openpyxl.load_workbook(review)
        sheet = book.active
        self.assertEqual(len(sheet.data_validations.dataValidation), 1)
        decision_column = REVIEW_COLUMNS.index("审核结论") + 1
        for row_number in range(2, 16):
            sheet.cell(row_number, decision_column, "通过")
        book.save(review)
        book.close()
        delivery, report, audit = finalize_review(self.records, self.config, review)
        self.assertEqual(len(delivery), 14)
        write_delivery(delivery, report, audit, self.folder / "xlsx-delivery", xlsx=True)
        self.assertEqual(len(read_table(self.folder / "xlsx-delivery/delivery.xlsx")), 14)


if __name__ == "__main__":
    unittest.main()
