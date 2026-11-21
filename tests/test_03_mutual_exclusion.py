"""样例 3：跨多会话不能同时犯规——汇总必须在同一个映射下求最大违规数。"""

from __future__ import annotations

import unittest

from _helpers import engine_reconcile, load_sample, verdict_map
from solution.engine import COMPLIANT, OFFLINE_EXPIRED, REVOKED_ROSTER
from solution.validation import parse_case
from solution.engine import verdict_at


class TestMutualExclusion(unittest.TestCase):
    def setUp(self):
        self.raw = load_sample("sample_03_mutual_exclusion.json")
        self.result = engine_reconcile(self.raw)

    def test_each_session_individually_can_violate(self):
        verdicts = verdict_map(self.result)
        self.assertIn(OFFLINE_EXPIRED, verdicts["S1"])
        self.assertIn(REVOKED_ROSTER, verdicts["S2"])
        self.assertIn(COMPLIANT, verdicts["S1"])
        self.assertIn(COMPLIANT, verdicts["S2"])

    def test_naive_sum_would_be_two_but_shared_mapping_max_is_one(self):
        # 逐条可能性相加会得到 2；正确答案是同一映射下最多 1
        self.assertEqual(
            self.result["summary"]["max_violations_under_one_mapping"], 1)
        self.assertEqual(len(self.result["summary"]["violating_sessions"]), 1)

    def test_witness_violating_session_is_s2_revoked(self):
        violating = self.result["summary"]["violating_sessions"]
        self.assertEqual(
            violating, [{"terminal_id": "T1", "session_id": "S2",
                         "verdict": REVOKED_ROSTER}])

    def test_exhaustive_enumeration_confirms_max_one(self):
        """暴力枚举全部可行整数链，独立验证 DP 的最大违规数。"""
        timeline, terminals, errors = parse_case(self.raw)
        self.assertFalse(errors)
        term = terminals[0]
        records = sorted(term.records, key=lambda r: r.local_seq)

        # 取引擎算出的每记录可行区间
        from solution.engine import compute_bounds
        bounds = compute_bounds(timeline, records)

        def chains(idx: int, floor: int):
            if idx == len(records):
                yield []
                return
            lo = max(bounds.earliest[idx], floor)
            for t in range(lo, bounds.latest[idx] + 1):
                for tail in chains(idx + 1, t):
                    yield [t] + tail

        worst = 0
        for chain in chains(0, timeline.min_seq):
            bad = 0
            for rec, t in zip(records, chain):
                if rec.record_type == "session" and \
                        verdict_at(timeline, rec, t) != COMPLIANT:
                    bad += 1
            worst = max(worst, bad)
        self.assertEqual(worst, 1)

    def test_every_reachable_verdict_witness_is_feasible(self):
        """每个可达裁定所附完整映射都必须满足区间、单调性且裁定自洽。"""
        timeline, terminals, _ = parse_case(self.raw)
        records = sorted(terminals[0].records, key=lambda r: r.local_seq)
        for sess in self.result["terminals"][0]["sessions"]:
            pinned_local_seq = sess["local_seq"]
            for rv in sess["reachable_verdicts"]:
                mapping = {e["local_seq"]: e["mapped_center_seq"]
                           for e in rv["witness_mapping"]}
                prev = None
                for rec in records:
                    t = mapping[rec.local_seq]
                    self.assertGreaterEqual(t, rec.center_seq_low)
                    self.assertLessEqual(t, rec.center_seq_high)
                    if prev is not None:
                        self.assertGreaterEqual(t, prev)
                    prev = t
                    # 只有被 pin 的会话必须恰好呈现所声明的可达裁定
                    if rec.local_seq == pinned_local_seq:
                        self.assertEqual(
                            verdict_at(timeline, rec, t), rv["verdict"])


if __name__ == "__main__":
    unittest.main()
