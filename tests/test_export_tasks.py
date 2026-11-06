"""Exercise exports through the public CLI and re-read the resulting data."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.validate_tasks import load_config, load_tasks  # noqa: E402


class ExportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.input = self.directory / "tasks.json"
        self.output = self.directory / "tasks.csv"
        self.tasks = load_tasks(ROOT / "output_example.json")
        self.save_tasks()

    def save_tasks(self):
        self.input.write_text(json.dumps(self.tasks, ensure_ascii=False), encoding="utf-8")

    def export(self, *arguments):
        return subprocess.run([sys.executable, str(ROOT / "scripts/export_tasks.py"),
                               str(self.input), str(self.output), *map(str, arguments)],
                              cwd=self.directory, capture_output=True, text=True)

    def test_export_round_trip_preserves_every_field_and_input(self):
        original = self.input.read_bytes()
        result = self.export("--source", ROOT / "input_example.csv")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(self.tasks, load_tasks(self.output))
        self.assertEqual(original, self.input.read_bytes())
        self.assertTrue(self.output.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_chinese_quotes_commas_and_newlines_round_trip(self):
        self.tasks[0]["description"] = '将两盒“感冒药”放到 A 区, 标记为 "已整理"\n保留换行'
        self.tasks[0]["task_goal"] = ['两盒药物在 A 区', '状态为 "已整理", 数量正确']
        self.tasks[0]["sop"] = '1. 取出药盒;\n2. 放置到 A 区。'
        self.save_tasks()
        result = self.export()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(self.tasks, load_tasks(self.output))

    def test_existing_output_is_never_overwritten(self):
        self.output.write_text("existing data", encoding="utf-8")
        result = self.export()
        self.assertEqual(1, result.returncode)
        self.assertIn("already exists", result.stderr)
        self.assertEqual("existing data", self.output.read_text(encoding="utf-8"))

    def test_invalid_tasks_do_not_create_output(self):
        self.tasks[0]["tool_use"] = "false"
        self.save_tasks()
        result = self.export()
        self.assertEqual(1, result.returncode)
        self.assertIn("JSON boolean", result.stderr)
        self.assertFalse(self.output.exists())

    def test_source_mismatch_prevents_export(self):
        self.tasks[0]["description"] = "Move twenty boxes"
        self.save_tasks()
        result = self.export("--source", ROOT / "input_example.csv")
        self.assertEqual(1, result.returncode)
        self.assertIn("does not match source line", result.stderr)
        self.assertFalse(self.output.exists())

    def test_custom_config_is_used(self):
        config = load_config(ROOT / "config.example.json")
        config["supported_environments"].append("custom_scene")
        self.tasks[0]["environment"] = "custom_scene"
        self.save_tasks()
        path = self.directory / "custom-config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        result = self.export("--config", path)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(self.tasks, load_tasks(self.output))

    def test_wrong_extensions_and_missing_input_fail_cleanly(self):
        self.output = self.directory / "tasks.xlsx"
        result = self.export()
        self.assertEqual(1, result.returncode)
        self.assertIn(".csv output", result.stderr)
        self.assertFalse(self.output.exists())
        self.output = self.directory / "tasks.csv"
        self.input.unlink()
        result = self.export()
        self.assertEqual(1, result.returncode)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
