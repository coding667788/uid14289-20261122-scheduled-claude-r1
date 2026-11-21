"""样例 1：禁用-恢复造成的中间允许区间空洞 + 持有版本已撤销。"""

from __future__ import annotations

import unittest

from _helpers import engine_reconcile, load_sample, sync_actions, verdict_map
from solution.engine import COMPLIANT, OFFLINE_EXPIRED, REVOKED_ROSTER


class TestRestoreHole(unittest.TestCase):
    def setUp(self):
        self.result = engine_reconcile(load_sample("sample_01_restore_hole.json"))

    def test_status_ok(self):
        self.assertEqual(self.result["status"], "ok")

    def test_s1_reaches_both_revoked_and_expired(self):
        verdicts = verdict_map(self.result)
        self.assertCountEqual(
            verdicts["S1"], [REVOKED_ROSTER, OFFLINE_EXPIRED])

    def test_s2_forced_expired_at_deadline(self):
        verdicts = verdict_map(self.result)
        self.assertEqual(verdicts["S2"], [OFFLINE_EXPIRED])

    def test_s3_revoked_by_held_version_even_while_center_restored(self):
        # S3 发生在版本 2（中心已恢复 D1）之后，但终端只持有版本 1（D1 被禁用）
        verdicts = verdict_map(self.result)
        self.assertEqual(verdicts["S3"], [REVOKED_ROSTER])

    def test_no_compliant_verdict_anywhere(self):
        for sid, vs in verdict_map(self.result).items():
            self.assertNotIn(COMPLIANT, vs, f"{sid} 不应可达合规")

    def test_sync_actions(self):
        self.assertEqual(sync_actions(self.result, "T1"),
                         ["applied", None, None])
        self.assertEqual(sync_actions(self.result, "T2"),
                         ["applied", None])

    def test_witness_mapping_is_complete_and_monotone(self):
        mapping = self.result["summary"]["witness_mapping"]
        # 覆盖全部 5 条记录
        self.assertEqual(len(mapping), 5)
        by_terminal = {}
        for entry in mapping:
            by_terminal.setdefault(entry["terminal_id"], []).append(
                entry["mapped_center_seq"])
        for seqs in by_terminal.values():
            self.assertEqual(seqs, sorted(seqs))

    def test_summary_violations_count(self):
        summary = self.result["summary"]
        self.assertEqual(summary["max_violations_under_one_mapping"], 3)
        self.assertEqual(len(summary["violating_sessions"]), 3)


if __name__ == "__main__":
    unittest.main()
