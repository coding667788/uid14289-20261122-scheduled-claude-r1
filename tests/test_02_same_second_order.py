"""样例 2：同一逻辑秒内中心序号决定顺序，同秒映射改变裁定。"""

from __future__ import annotations

import unittest

from _helpers import engine_reconcile, load_sample, verdict_map
from solution.engine import COMPLIANT, REVOKED_ROSTER


class TestSameSecondOrder(unittest.TestCase):
    def setUp(self):
        self.result = engine_reconcile(load_sample("sample_02_same_second_order.json"))

    def test_s1_switches_within_same_logical_second(self):
        verdicts = verdict_map(self.result)["S1"]
        self.assertCountEqual(verdicts, [COMPLIANT, REVOKED_ROSTER])

    def test_witness_points_are_same_second_different_seq(self):
        sess = self.result["terminals"][0]["sessions"][0]
        points = []
        for v in sess["reachable_verdicts"]:
            entry = next(e for e in v["witness_mapping"] if e["local_seq"] == 2)
            points.append((entry["mapped_logical_second"], entry["mapped_center_seq"]))
        seconds = {p[0] for p in points}
        self.assertEqual(seconds, {50})          # 都在逻辑秒 50
        self.assertEqual(len({p[1] for p in points}), 2)  # 但中心序号不同

    def test_s2_forced_revoked(self):
        self.assertEqual(verdict_map(self.result)["S2"], [REVOKED_ROSTER])

    def test_same_second_center_events_are_both_ordered(self):
        from solution.validation import parse_case
        raw = load_sample("sample_02_same_second_order.json")
        timeline, _, errors = parse_case(raw)
        self.assertFalse(errors)
        same_second = [e for e in timeline.events if e.logical_second == 50]
        self.assertEqual([e.center_seq for e in same_second], [10, 11])


if __name__ == "__main__":
    unittest.main()
