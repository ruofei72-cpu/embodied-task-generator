from copy import deepcopy
import json
from pathlib import Path
import unittest

from taskgen.engine import TaskgenError, generate_tasks, validate_config, validate_variants


ROOT = Path(__file__).resolve().parents[1]


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config.example.json").read_text())
        self.tasks = json.loads((ROOT / "examples/atomic_tasks.json").read_text())

    def test_quota_coverage_and_canonical_validation(self):
        records, report = generate_tasks(self.tasks, self.config)
        self.assertEqual(report["summary"]["generated_variants"], 14)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(validate_variants(records, self.config), [])
        for task in self.tasks:
            group = [r for r in records if r["parent_task_id"] == task["task_id"]]
            self.assertEqual(len(group), 7)
            self.assertEqual({a for r in group for a in r["changed_axes"]},
                             {a["name"] for a in self.config["axes"]})
            self.assertTrue(all(type(r["goal"]["quantity"]) is int for r in group))

    def test_single_axis_reset_and_input_not_mutated(self):
        original = deepcopy(self.tasks)
        original_config = deepcopy(self.config)
        records, _ = generate_tasks(self.tasks, self.config)
        self.assertEqual(self.tasks, original)
        self.assertEqual(self.config, original_config)
        for record in records:
            changed = [key for key, value in record["parameters"].items()
                       if value != record["base_parameters"][key]]
            self.assertEqual(len(changed), 1)
        records[0]["base_parameters"]["color"] = "not-shared"
        self.assertEqual(records[1]["base_parameters"]["color"], "红色")

    def test_cross_combines_two_axes_and_applies_constraints(self):
        self.config["mode"] = "cross"
        records, report = generate_tasks(self.tasks, self.config)
        self.assertEqual(report["status"], "complete")
        self.assertTrue(all(len(r["changed_axes"]) == 2 for r in records))
        self.assertTrue(all(not (r["parameters"]["material"] == "金属" and
                                r["parameters"]["orientation"] == "侧放") for r in records))
        self.assertTrue(any("constraint:demo_disallows_sideways_metal" in t["rejected_candidates"]
                            for t in report["tasks"]))
        self.assertEqual(validate_variants(records, self.config), [])

    def test_deterministic_reruns(self):
        self.assertEqual(generate_tasks(self.tasks, self.config), generate_tasks(self.tasks, self.config))

    def test_impossible_quota_does_not_export_incomplete_groups(self):
        self.config["variants_per_task"] = 1000
        records, report = generate_tasks(self.tasks, self.config)
        self.assertEqual(records, [])
        self.assertEqual(report["status"], "failed")
        self.assertIn("Requested 1000", report["tasks"][0]["errors"][0])

    def test_partial_batch_keeps_valid_task(self):
        self.tasks[1]["scene_id"] = "missing-scene"
        records, report = generate_tasks(self.tasks, self.config)
        self.assertEqual(len(records), 7)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["summary"]["rejected_tasks"], 1)

    def test_non_object_task_is_reported(self):
        records, report = generate_tasks(["invalid", self.tasks[0]], self.config)
        self.assertEqual(len(records), 7)
        self.assertEqual(report["tasks"][0]["errors"], ["Task must be an object"])

    def test_duplicate_task_ids(self):
        self.tasks[1]["task_id"] = self.tasks[0]["task_id"]
        records, report = generate_tasks(self.tasks, self.config)
        self.assertEqual(len(records), 7)
        self.assertIn("Duplicate task_id", report["tasks"][1]["errors"][0])

    def test_structured_dedup_ignores_textual_paraphrase(self):
        duplicate = deepcopy(self.tasks[0])
        duplicate["task_id"] = "paraphrased-task"
        duplicate["instruction_template"] = "请将{object}移动至{target}。"
        records, report = generate_tasks([self.tasks[0], duplicate], self.config)
        self.assertEqual(len(records), 7)
        self.assertGreater(report["tasks"][1]["rejected_candidates"]["duplicate_structured_task"], 0)

    def test_inventory_constraints_reject_excess_quantity(self):
        records, report = generate_tasks(self.tasks, self.config)
        self.assertTrue(all(r["parameters"]["quantity"] <= 2 for r in records))
        self.assertGreater(report["tasks"][0]["rejected_candidates"]["insufficient_inventory"], 0)

    def test_invalid_initial_state_rejected(self):
        self.tasks[0]["parameters"]["object"] = "不存在的物料"
        records, report = generate_tasks(self.tasks[:1], self.config)
        self.assertEqual(records, [])
        self.assertIn("catalog:object", report["tasks"][0]["errors"][0])

    def test_malformed_object_and_constraint_name_do_not_crash(self):
        self.tasks[0]["parameters"]["object"] = ["杯子"]
        records, report = generate_tasks(self.tasks[:1], self.config)
        self.assertFalse(records)
        self.assertIn("Invalid base parameters", report["tasks"][0]["errors"][0])
        self.config["constraints"][0]["name"] = ["invalid"]
        with self.assertRaisesRegex(TaskgenError, "Constraint name"):
            validate_config(self.config)

    def test_forbidden_initial_state_rejected(self):
        self.tasks[0]["parameters"].update(material="金属", orientation="侧放")
        records, report = generate_tasks(self.tasks[:1], self.config)
        self.assertFalse(records)
        self.assertIn("constraint:", report["tasks"][0]["errors"][0])

    def test_candidate_budget_reports_truncation(self):
        self.config["max_candidates"] = 2
        records, report = generate_tasks(self.tasks[:1], self.config)
        self.assertEqual(records, [])
        self.assertTrue(report["tasks"][0]["candidate_limit_reached"])
        self.assertEqual(report["tasks"][0]["candidates_checked"], 2)

    def test_missing_axis_coverage_rejects_even_with_enough_variants(self):
        self.config["axes"][0]["values"].append("黄色")
        self.config["scenes"]["kitchen_demo"]["allowed_values"]["color"].append("黄色")
        self.config["constraints"].append({"type": "forbid", "name": "no_sideways", "when": {"orientation": "侧放"}})
        records, report = generate_tasks(self.tasks[:1], self.config)
        self.assertEqual(records, [])
        self.assertGreaterEqual(report["tasks"][0]["valid_candidates"], 7)
        self.assertIn("orientation", report["tasks"][0]["missing_axes"])
        self.assertIn("coverage", report["tasks"][0]["errors"][0])

    def test_task_specific_axes_and_quota(self):
        self.tasks[0]["axes"] = ["color"]
        self.tasks[0]["variants_per_task"] = 2
        records, report = generate_tasks(self.tasks[:1], self.config)
        self.assertEqual(len(records), 2)
        self.assertEqual(report["status"], "complete")

    def test_unsafe_or_unknown_templates_are_rejected(self):
        for template in ("{object.__class__}", "{object[0]}", "{unknown}", "{object!r}", "{object:>20}"):
            with self.subTest(template=template):
                tasks = deepcopy(self.tasks[:1])
                tasks[0]["instruction_template"] = template
                records, report = generate_tasks(tasks, self.config)
                self.assertEqual(records, [])
                self.assertTrue(report["tasks"][0]["errors"])

    def test_label_booleans_and_consistency(self):
        for labels in ({"tool_use": "false"}, {"tool_use": False, "fine_tool_use": True}):
            self.tasks[0]["labels"] = labels
            records, _ = generate_tasks(self.tasks[:1], self.config)
            self.assertEqual(records, [])

    def test_bad_config_fails_clearly(self):
        for field, value in (("variants_per_task", True), ("max_candidates", 0),
                             ("constraints", None), ("mode", "unknown"), ("schema_version", "1.0")):
            with self.subTest(field=field):
                config = deepcopy(self.config)
                config[field] = value
                with self.assertRaises(TaskgenError):
                    validate_config(config)
        self.config["axes"].append(deepcopy(self.config["axes"][0]))
        with self.assertRaises(TaskgenError):
            validate_config(self.config)

    def test_nonfinite_or_nonjson_input_rejected_cleanly(self):
        for value in (float("nan"), float("inf"), {1, 2}):
            self.tasks[0]["parameters"]["quantity"] = value
            with self.assertRaises(TaskgenError):
                generate_tasks(self.tasks, self.config)

    def test_validator_detects_tampering_duplicates_and_missing_records(self):
        records, _ = generate_tasks(self.tasks, self.config)
        tampered = deepcopy(records)
        tampered[0]["parameters"]["color"] = "绿色"
        self.assertTrue(any("Fingerprint mismatch" in e for e in validate_variants(tampered, self.config)))
        self.assertTrue(any("incomplete" in e for e in validate_variants(records[:-1], self.config)))
        self.assertTrue(any("Duplicate variant_id" in e for e in validate_variants(records + records[:1], self.config)))
        records[0]["requires_human_review"] = False
        self.assertTrue(any("requires_human_review" in e for e in validate_variants(records, self.config)))

    def test_scaled_eight_scene_synthetic_batch(self):
        tasks = []
        scene = deepcopy(self.config["scenes"]["kitchen_demo"])
        base_task = self.tasks[0]
        for scene_number in range(8):
            scene_id = f"synthetic-{scene_number}"
            self.config["scenes"][scene_id] = deepcopy(scene)
            for task_number in range(21):
                task = deepcopy(base_task)
                task["task_id"] = f"scene-{scene_number}-task-{task_number}"
                task["scene_id"] = scene_id
                # Distinct synthetic actions avoid inventing duplicate atomic tasks.
                task["action"] = f"synthetic_place_{task_number}"
                tasks.append(task)
        records, report = generate_tasks(tasks, self.config)
        self.assertEqual(report["summary"]["accepted_tasks"], 168)
        self.assertEqual(len(records), 1176)
        self.assertEqual(validate_variants(records, self.config), [])


if __name__ == "__main__":
    unittest.main()
