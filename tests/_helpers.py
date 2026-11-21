"""测试公共工具：加载样例并调用引擎/CLI。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# discover -s tests 时仓库根不一定在 sys.path 上，显式加入以便 import solution
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAMPLES = ROOT / "samples"


def load_sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def run_cli(*args: str) -> tuple[int, dict]:
    """以子进程方式运行 python -m solution，返回 (退出码, JSON 输出)。"""
    proc = subprocess.run(
        [sys.executable, "-m", "solution", *args],
        cwd=str(ROOT), capture_output=True, check=False)
    payload = json.loads(proc.stdout.decode("utf-8"))
    return proc.returncode, payload


def engine_reconcile(raw: dict) -> dict:
    """进程内直接调用引擎，便于细粒度断言。"""
    from solution.validation import parse_case
    from solution.engine import reconcile
    timeline, terminals, errors = parse_case(raw)
    assert not errors, [e.to_dict() for e in errors]
    return reconcile(timeline, terminals)


def verdict_map(result: dict) -> dict[str, list[str]]:
    out = {}
    for term in result["terminals"]:
        for sess in term["sessions"]:
            out[sess["session_id"]] = [
                v["verdict"] for v in sess["reachable_verdicts"]]
    return out


def sync_actions(result: dict, terminal_id: str) -> list[str | None]:
    term = next(t for t in result["terminals"] if t["terminal_id"] == terminal_id)
    return [r["sync_action"] for r in term["records"]]
