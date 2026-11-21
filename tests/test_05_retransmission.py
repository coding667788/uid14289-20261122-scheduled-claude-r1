"""样例 5：重传幂等 + SQLite 台账/缓存。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _helpers import engine_reconcile, load_sample, run_cli, sync_actions


class TestRetransmission(unittest.TestCase):
    def setUp(self):
        self.sample = "sample_05_retransmission.json"
        self.result = engine_reconcile(load_sample(self.sample))

    def test_duplicate_delivery_is_replay_action(self):
        actions = sync_actions(self.result, "T1")
        self.assertEqual(actions, ["applied", "replay", "applied", None])

    def test_replay_does_not_change_held_version(self):
        term = self.result["terminals"][0]
        sess = term["sessions"][0]
        self.assertEqual(sess["held_version"], 2)

    def test_session_verdict(self):
        sess = self.result["terminals"][0]["sessions"][0]
        self.assertEqual(
            [v["verdict"] for v in sess["reachable_verdicts"]],
            ["offline_expired"])

    def test_cli_db_ledger_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "ledger.sqlite")
            path = f"samples/{self.sample}"

            code, first = run_cli("reconcile", path, "--db", db)
            self.assertEqual(code, 0)
            self.assertEqual(first["persistence"]["cache"], "stored")
            self.assertEqual(first["persistence"]["terminals"][0]["replays"], 0)

            code, second = run_cli("reconcile", path, "--db", db)
            self.assertEqual(code, 0)
            self.assertEqual(second["persistence"]["cache"], "hit")
            # 第二次整案投递：4 条记录全部计为重传
            self.assertEqual(second["persistence"]["terminals"][0]["replays"], 4)

            code, ledger = run_cli("ledger", path, "--db", db)
            self.assertEqual(code, 0)
            self.assertEqual(len(ledger["records"]), 4)
            self.assertTrue(all(r["replay_count"] == 1 for r in ledger["records"]))

            # 缓存命中时业务结果与首跑完全一致（去掉 persistence 包装后比较）
            first_biz = {k: v for k, v in first.items() if k != "persistence"}
            second_biz = {k: v for k, v in second.items() if k != "persistence"}
            self.assertEqual(
                json.dumps(first_biz, sort_keys=True, ensure_ascii=False),
                json.dumps(second_biz, sort_keys=True, ensure_ascii=False))

    def test_ledger_requires_db_flag(self):
        code, payload = run_cli("ledger", f"samples/{self.sample}")
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "invalid_input")


if __name__ == "__main__":
    unittest.main()
