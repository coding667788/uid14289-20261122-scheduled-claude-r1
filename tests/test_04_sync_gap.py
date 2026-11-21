"""样例 4：同步版本缺口——终端不能跳过缺失版本。"""

from __future__ import annotations

import unittest

from _helpers import engine_reconcile, load_sample
from solution.engine import replay_sync
from solution.validation import parse_case


class TestSyncGap(unittest.TestCase):
    def test_gap_reports_evidence_conflict(self):
        result = engine_reconcile(load_sample("sample_04_sync_gap.json"))
        self.assertEqual(result["status"], "evidence_conflict")
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["code"], "held_version_mismatch")
        self.assertEqual(conflict["declared_held_version"], 3)
        self.assertEqual(conflict["replayed_held_version"], 1)

    def test_out_of_order_version_is_buffered_not_applied(self):
        raw = load_sample("sample_04_sync_gap.json")
        _, terminals, errors = parse_case(raw)
        self.assertFalse(errors)
        records = sorted(terminals[0].records, key=lambda r: r.local_seq)
        replay = replay_sync(records)
        self.assertEqual(replay.actions, ["applied", "buffered", None])
        # 缓冲期间持有版本仍停在 1
        self.assertEqual(replay.held_after[1], 1)

    def test_gap_fills_then_buffer_drains(self):
        """补传缺失版本后，缓冲区应按连续顺序排空。"""
        from solution.models import TerminalRecord
        records = [
            TerminalRecord("T", 1, "sync", version=1,
                           center_seq_low=1, center_seq_high=1),
            TerminalRecord("T", 2, "sync", version=3,
                           center_seq_low=1, center_seq_high=1),
            TerminalRecord("T", 3, "sync", version=2,
                           center_seq_low=1, center_seq_high=1),
        ]
        replay = replay_sync(records)
        self.assertEqual(replay.actions, ["applied", "buffered", "applied"])
        self.assertEqual(replay.held_after, [1, 1, 3])


if __name__ == "__main__":
    unittest.main()
