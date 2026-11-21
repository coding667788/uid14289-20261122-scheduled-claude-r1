"""样例 6：证据冲突（无一致映射 / 持有版本不符）优先于任何裁定。"""

from __future__ import annotations

import unittest

from _helpers import engine_reconcile, load_sample, run_cli


class TestEvidenceConflict(unittest.TestCase):
    def setUp(self):
        self.sample = "sample_06_evidence_conflict.json"
        self.result = engine_reconcile(load_sample(self.sample))

    def test_status_is_conflict(self):
        self.assertEqual(self.result["status"], "evidence_conflict")

    def test_both_conflict_codes_reported(self):
        codes = {c["code"] for c in self.result["conflicts"]}
        self.assertEqual(
            codes, {"unsatisfiable_interval", "held_version_mismatch"})

    def test_conflicts_carry_terminal_and_local_seq(self):
        for conflict in self.result["conflicts"]:
            self.assertIn("terminal_id", conflict)
            self.assertIn("local_seq", conflict)

    def test_monotone_violation_is_unsatisfiable(self):
        # T1：sync 只能映射到 20，session 区间上界 15，同站单调性无解
        t1 = [c for c in self.result["conflicts"] if c["terminal_id"] == "T1"]
        self.assertTrue(t1)
        self.assertTrue(all(c["code"] == "unsatisfiable_interval" for c in t1))

    def test_cli_returns_conflict_with_zero_exit(self):
        # 证据冲突是语义结果而非结构错误：退出码 0，状态字段区分
        code, payload = run_cli("reconcile", f"samples/{self.sample}")
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "evidence_conflict")


if __name__ == "__main__":
    unittest.main()
