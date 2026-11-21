"""SQLite 持久化（仅当命令行给出 --db 时使用）。

两张表：
- record_ledger：以 (case_hash, terminal_id, local_seq) 为幂等键的投递台账，
  重传只累加 replay_count，不产生新记录；用于 ledger 命令查看重传情况。
- case_cache：按输入字节的稳定哈希缓存 reconcile 结果，保证同输入同输出。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS record_ledger (
    case_hash     TEXT NOT NULL,
    terminal_id   TEXT NOT NULL,
    local_seq     INTEGER NOT NULL,
    record_type   TEXT NOT NULL,
    replay_count  INTEGER NOT NULL DEFAULT 0,
    first_seen    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (case_hash, terminal_id, local_seq)
);
CREATE TABLE IF NOT EXISTS case_cache (
    case_hash   TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""


def case_hash(raw_bytes: bytes) -> str:
    """对输入字节做稳定哈希（sha256），作为台账与缓存的分区键。"""
    return hashlib.sha256(raw_bytes).hexdigest()


def open_db(path: str) -> sqlite3.Connection:
    db_path = Path(path)
    if db_path.parent and not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def record_ingestion(conn: sqlite3.Connection, chash: str, case: dict) -> dict:
    """把 CASE 中的终端记录登记到台账，返回每站重传统计。"""
    stats: dict[str, dict[str, int]] = {}
    for term in case.get("terminals", []):
        tid = term["terminal_id"]
        stats.setdefault(tid, {"records": 0, "replays": 0})
        for rec in term.get("records", []):
            stats[tid]["records"] += 1
            cur = conn.execute(
                "SELECT replay_count FROM record_ledger "
                "WHERE case_hash=? AND terminal_id=? AND local_seq=?",
                (chash, tid, rec["local_seq"]))
            row = cur.fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO record_ledger "
                    "(case_hash, terminal_id, local_seq, record_type, replay_count) "
                    "VALUES (?, ?, ?, ?, 0)",
                    (chash, tid, rec["local_seq"], rec["type"]))
            else:
                conn.execute(
                    "UPDATE record_ledger SET replay_count=replay_count+1 "
                    "WHERE case_hash=? AND terminal_id=? AND local_seq=?",
                    (chash, tid, rec["local_seq"]))
                stats[tid]["replays"] += 1
    conn.commit()
    return {
        "case_hash": chash,
        "terminals": [
            {"terminal_id": tid, "ledger_records": s["records"], "replays": s["replays"]}
            for tid, s in sorted(stats.items())
        ],
    }


def get_cached_result(conn: sqlite3.Connection, chash: str) -> dict | None:
    row = conn.execute(
        "SELECT result_json FROM case_cache WHERE case_hash=?", (chash,)).fetchone()
    if row is None:
        return None
    return json.loads(row["result_json"])


def store_result(conn: sqlite3.Connection, chash: str, result: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO case_cache (case_hash, result_json) VALUES (?, ?)",
        (chash, json.dumps(result, ensure_ascii=False, sort_keys=True)))
    conn.commit()


def ledger_snapshot(conn: sqlite3.Connection, chash: str) -> dict:
    rows = conn.execute(
        "SELECT terminal_id, local_seq, record_type, replay_count, first_seen "
        "FROM record_ledger WHERE case_hash=? "
        "ORDER BY terminal_id, local_seq", (chash,)).fetchall()
    return {
        "case_hash": chash,
        "records": [
            {
                "terminal_id": r["terminal_id"],
                "local_seq": r["local_seq"],
                "record_type": r["record_type"],
                "replay_count": r["replay_count"],
                "first_seen": r["first_seen"],
            }
            for r in rows
        ],
    }
