"""命令行入口：python -m solution <命令> CASE.json [--db DB.sqlite]

命令：
- validate:  只做结构校验
- reconcile: 完整对账（默认）
- ledger:    查看 --db 中该 CASE 的投递/重传台账
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import persistence
from .engine import reconcile
from .validation import parse_case

EXIT_OK = 0
EXIT_INPUT_ERROR = 2
EXIT_IO_ERROR = 1


def _emit(payload: dict) -> None:
    """stdout 始终输出 UTF-8 JSON（sort_keys 保证逻辑输入不变时字节稳定）。"""
    sys.stdout.buffer.write(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))


def _load_case(path: str) -> tuple[bytes, dict]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(path)
    raw_bytes = p.read_bytes()
    return raw_bytes, json.loads(raw_bytes.decode("utf-8"))


def _print_errors(errors) -> None:
    _emit({
        "status": "invalid_input",
        "errors": [e.to_dict() for e in sorted(errors, key=lambda e: (e.path, e.code))],
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m solution",
        description="终端名册撤回后的离线会话断点核对")
    parser.add_argument("command", choices=["reconcile", "validate", "ledger"])
    parser.add_argument("case_file")
    parser.add_argument("--db", default=None, help="SQLite 持久化文件（可选）")
    argv = list(sys.argv[1:] if argv is None else argv)
    # 省略子命令时默认 reconcile：python -m solution CASE.json
    if argv and argv[0] not in ("reconcile", "validate", "ledger", "-h", "--help"):
        argv.insert(0, "reconcile")
    args = parser.parse_args(argv)

    try:
        raw_bytes, raw = _load_case(args.case_file)
    except FileNotFoundError:
        _emit({"status": "io_error", "code": "file_not_found",
               "message": f"找不到 CASE 文件: {args.case_file}"})
        return EXIT_IO_ERROR
    except json.JSONDecodeError as exc:
        _emit({"status": "invalid_input",
               "errors": [{"path": "", "code": "json_parse_error",
                           "message": f"JSON 解析失败: {exc.msg} (行 {exc.lineno} 列 {exc.colno})"}]})
        return EXIT_INPUT_ERROR

    timeline, terminals, errors = parse_case(raw)
    if errors:
        _print_errors(errors)
        return EXIT_INPUT_ERROR

    if args.command == "validate":
        _emit({"status": "valid",
               "center_events": len(timeline.events),
               "terminals": len(terminals)})
        return EXIT_OK

    chash = persistence.case_hash(raw_bytes)

    if args.command == "ledger":
        if not args.db:
            _emit({"status": "invalid_input",
                   "errors": [{"path": "--db", "code": "missing_field",
                               "message": "ledger 命令需要 --db DB.sqlite"}]})
            return EXIT_INPUT_ERROR
        conn = persistence.open_db(args.db)
        try:
            _emit(persistence.ledger_snapshot(conn, chash))
        finally:
            conn.close()
        return EXIT_OK

    # reconcile
    conn = persistence.open_db(args.db) if args.db else None
    try:
        if conn is not None:
            ledger = persistence.record_ingestion(conn, chash, raw)
            cached = persistence.get_cached_result(conn, chash)
            if cached is not None:
                result = cached
                result["persistence"] = {"cache": "hit", **ledger}
            else:
                result = reconcile(timeline, terminals)
                persistence.store_result(conn, chash, result)
                result["persistence"] = {"cache": "stored", **ledger}
        else:
            result = reconcile(timeline, terminals)
        _emit(result)
        return EXIT_OK
    finally:
        if conn is not None:
            conn.close()
