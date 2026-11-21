"""样例 7 与补充用例：结构校验错误码与字段位置。"""

from __future__ import annotations

import unittest

from _helpers import load_sample, run_cli
from solution.errors import InputError
from solution.validation import parse_case


def validate_raw(raw):
    _, _, errors = parse_case(raw)
    return errors


class TestStructuralValidation(unittest.TestCase):
    def test_sample_07_collects_multiple_errors_with_paths(self):
        code, payload = run_cli(
            "validate", "samples/sample_07_invalid_structure.json")
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "invalid_input")
        paths_codes = {(e["path"], e["code"]) for e in payload["errors"]}
        self.assertIn(("/center/events", "ordering_violation"), paths_codes)
        self.assertIn(
            ("/center/events/1/changes/0/action", "invalid_value"), paths_codes)
        self.assertIn(
            ("/terminals/0/records/1/local_seq", "duplicate_value"), paths_codes)
        self.assertIn(
            ("/terminals/0/records/1/device_id", "unknown_reference"), paths_codes)
        self.assertIn(
            ("/terminals/0/records/1/offline_deadline_second",
             "range_violation"), paths_codes)

    def test_root_must_be_object(self):
        errors = validate_raw([1, 2, 3])
        self.assertTrue(any(e.code == "type_error" and e.path == "" for e in errors))

    def test_interval_lower_above_upper(self):
        raw = load_sample("sample_01_restore_hole.json")
        raw["terminals"][0]["records"][0]["center_seq_interval"] = [20, 10]
        errors = validate_raw(raw)
        self.assertTrue(any(e.code == "range_violation" for e in errors))

    def test_bool_is_not_integer(self):
        raw = load_sample("sample_01_restore_hole.json")
        raw["center"]["events"][0]["center_seq"] = True
        errors = validate_raw(raw)
        self.assertTrue(any(
            e.path == "/center/events/0/center_seq" and e.code == "invalid_value"
            for e in errors))

    def test_missing_file_exit_code(self):
        code, payload = run_cli("validate", "samples/does_not_exist.json")
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "io_error")

    def test_malformed_json(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.json"
            p.write_text("{not json", encoding="utf-8")
            code, payload = run_cli("validate", str(p))
            self.assertEqual(code, 2)
            self.assertEqual(payload["errors"][0]["code"], "json_parse_error")

    def test_error_is_dataclass_dict_shape(self):
        err = InputError("/x", "type_error", "坏")
        self.assertEqual(set(err.to_dict()), {"path", "code", "message"})


if __name__ == "__main__":
    unittest.main()
