"""确定性、UTF-8 输出与引擎边界规则（截止相等、区间闭包、优先级）。"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

from _helpers import engine_reconcile, load_sample
from solution.engine import (
    COMPLIANT, OFFLINE_EXPIRED, REVOKED_ROSTER, verdict_at)
from solution.validation import parse_case

ROOT = Path(__file__).resolve().parent.parent


class TestDeterminismAndEncoding(unittest.TestCase):
    def test_repeated_runs_byte_identical(self):
        path = ROOT / "samples" / "sample_01_restore_hole.json"
        outs = set()
        for _ in range(3):
            proc = subprocess.run(
                [sys.executable, "-m", "solution", "reconcile", str(path)],
                cwd=str(ROOT), capture_output=True, check=False)
            outs.add(proc.stdout)
        self.assertEqual(len(outs), 1)

    def test_key_order_reordering_does_not_change_result(self):
        raw = load_sample("sample_01_restore_hole.json")
        first = engine_reconcile(raw)
        reordered = copy.deepcopy(raw)
        # 打乱中心事件外的键顺序（事件顺序本身是输入语义，不能动）
        reordered["center"], reordered["terminals"] = (
            reordered["terminals"], reordered["center"])
        reordered["center"], reordered["terminals"] = (
            reordered["terminals"], reordered["center"])
        second = engine_reconcile(reordered)
        self.assertEqual(
            json.dumps(first, sort_keys=True, ensure_ascii=False),
            json.dumps(second, sort_keys=True, ensure_ascii=False))

    def test_stdout_is_utf8_with_chinese_note(self):
        proc = subprocess.run(
            [sys.executable, "-m", "solution", "reconcile",
             str(ROOT / "samples" / "sample_01_restore_hole.json")],
            cwd=str(ROOT), capture_output=True, check=False)
        text = proc.stdout.decode("utf-8")
        self.assertIn("同一", text)
        json.loads(text)  # 仍是合法 JSON


class TestVerdictBoundaryRules(unittest.TestCase):
    def setUp(self):
        raw = load_sample("sample_01_restore_hole.json")
        self.timeline, self.terminals, _ = parse_case(raw)
        self.session = next(
            r for t in self.terminals for r in t.records
            if r.record_type == "session" and r.session_id == "S2")

    def test_deadline_equality_means_expired(self):
        # 离线截止秒 120 的起始槽位是 seq=30；相等即判时限失效
        expiry = self.timeline.expiry_seq(120)
        self.assertEqual(expiry, 30)
        self.assertEqual(
            verdict_at(self.timeline, self.session, expiry), OFFLINE_EXPIRED)

    def test_one_slot_before_deadline_not_expired(self):
        # seq=29 仍属逻辑秒 110，早于截止；S2 持有 v1（D1 禁用）-> 名册问题
        self.assertEqual(
            verdict_at(self.timeline, self.session, 29), REVOKED_ROSTER)

    def test_expired_takes_priority_over_revoked(self):
        # seq=30 时既到截止、名册也禁用，固定优先级先判时限失效
        self.assertEqual(
            verdict_at(self.timeline, self.session, 30), OFFLINE_EXPIRED)

    def test_interval_is_closed_on_both_ends(self):
        raw = load_sample("sample_02_same_second_order.json")
        timeline, terminals, _ = parse_case(raw)
        s1 = next(r for r in terminals[0].records
                  if r.record_type == "session" and r.session_id == "S1")
        # 区间 [10,11] 两个端点都可映射且裁定不同
        self.assertEqual(verdict_at(timeline, s1, 10), COMPLIANT)
        self.assertEqual(verdict_at(timeline, s1, 11), REVOKED_ROSTER)

    def test_allowed_segments_can_be_multiple(self):
        raw = load_sample("sample_01_restore_hole.json")
        timeline, _, _ = parse_case(raw)
        # D1 的中心侧允许状态随禁用/恢复断开：禁(10)-恢复(20)-禁(30)
        self.assertFalse(timeline.state_at("D1", 15).allowed)
        self.assertTrue(timeline.state_at("D1", 25).allowed)
        self.assertFalse(timeline.state_at("D1", 30).allowed)


if __name__ == "__main__":
    unittest.main()
