"""对账引擎。

输入已经通过结构校验。引擎解决三类问题：

1. 同步语义重放：终端只在收到连续版本后推进，乱序先进缓冲区，重传幂等；
   会话声明的 held_version 必须与重放结果一致，否则证据冲突。
2. 中央时间映射：每条终端记录在其中心序号闭区间内取一个整数时刻 t_i，
   要求同站 t_i 不随本地序号下降，且 sync 时刻不早于所收版本的发布时刻。
3. 裁定与汇总：按固定优先级判定每个会话在每个可行映射下的裁定；再在
   “同一个映射”下求最多违规会话数（不能把逐条可能性相加）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import CenterTimeline, Terminal, TerminalRecord

COMPLIANT = "compliant"
REVOKED_ROSTER = "revoked_roster"
OFFLINE_EXPIRED = "offline_expired"
VERDICT_PRIORITY = (OFFLINE_EXPIRED, REVOKED_ROSTER, COMPLIANT)

SYNC_APPLIED = "applied"
SYNC_BUFFERED = "buffered"
SYNC_REPLAY = "replay"


# ---------------------------------------------------------------------------
# 同步重放
# ---------------------------------------------------------------------------

@dataclass
class ReplayResult:
    actions: list[str | None]          # 每条记录的同步动作（session 为 None）
    held_after: list[int]              # 每条记录处理完后终端持有的版本
    held_conflicts: list[dict]         # 会话 held_version 与重放不符的冲突


def replay_sync(records: list[TerminalRecord]) -> ReplayResult:
    """按“只推进连续版本”的规则重放一个终端的审计记录。"""
    buffer: set[int] = set()
    next_version = 1
    applied = 0
    actions: list[str | None] = []
    held_after: list[int] = []
    conflicts: list[dict] = []

    for rec in records:
        if rec.record_type == "sync":
            v = rec.version
            if v < next_version or v in buffer:
                # 已应用版本的重传，或同一未应用版本的重复投递
                action = SYNC_REPLAY
            else:
                buffer.add(v)
                action = SYNC_BUFFERED
            # 尽量排空连续前缀
            advanced = False
            while next_version in buffer:
                buffer.remove(next_version)
                applied = next_version
                next_version += 1
                advanced = True
            if advanced and action == SYNC_BUFFERED:
                action = SYNC_APPLIED
            actions.append(action)
        else:
            actions.append(None)
            if rec.held_version != applied:
                conflicts.append({
                    "terminal_id": rec.terminal_id,
                    "local_seq": rec.local_seq,
                    "session_id": rec.session_id,
                    "code": "held_version_mismatch",
                    "message": (
                        f"会话声明持有版本 {rec.held_version}，"
                        f"但按连续同步重放此时只持有版本 {applied}"
                    ),
                    "declared_held_version": rec.held_version,
                    "replayed_held_version": applied,
                })
        held_after.append(applied)

    return ReplayResult(actions, held_after, conflicts)


# ---------------------------------------------------------------------------
# 中央时间映射的可行性区间
# ---------------------------------------------------------------------------

@dataclass
class ChainBounds:
    earliest: list[int]
    latest: list[int]
    lower: list[int]                    # 含因果下界的原始下界
    upper: list[int]                    # 原始上界


def compute_bounds(timeline: CenterTimeline,
                   records: list[TerminalRecord]) -> ChainBounds:
    """计算每条记录在可行单调链中的最早/最晚可取时刻。"""
    lower: list[int] = []
    upper: list[int] = []
    causal_floor = timeline.min_seq
    for rec in records:
        lb = rec.center_seq_low
        if rec.record_type == "sync":
            # 同步不可能发生在所收版本发布之前
            lb = max(lb, timeline.release_seq(rec.version))
        lb = max(lb, causal_floor)
        lower.append(lb)
        upper.append(rec.center_seq_high)
        causal_floor = lb

    earliest = lower[:]
    for i in range(1, len(earliest)):
        earliest[i] = max(earliest[i], earliest[i - 1])

    latest = upper[:]
    for i in range(len(latest) - 2, -1, -1):
        latest[i] = min(latest[i], latest[i + 1])

    return ChainBounds(earliest, latest, lower, upper)


def build_chain(lower: list[int], upper: list[int], pin_index: int,
                pin_value: int) -> list[int] | None:
    """构造 pin 处固定为 pin_value 时分量最小的可行单调链；不可行返回 None。"""
    n = len(lower)
    t = [0] * n
    t[pin_index] = pin_value
    cur = pin_value
    for i in range(pin_index + 1, n):
        cur = max(lower[i], cur)
        if cur > upper[i]:
            return None
        t[i] = cur
    cur = pin_value
    for i in range(pin_index - 1, -1, -1):
        v = lower[i]
        if v > cur or v > upper[i]:
            return None
        t[i] = v
        cur = v
    return t


# ---------------------------------------------------------------------------
# 裁定
# ---------------------------------------------------------------------------

def verdict_at(timeline: CenterTimeline, rec: TerminalRecord, t: int) -> str:
    """固定优先级：离线截止 -> 名册撤销 -> 合规。"""
    expiry = timeline.expiry_seq(rec.offline_deadline_second)
    if t >= expiry:
        return OFFLINE_EXPIRED
    center_state = timeline.state_at(rec.device_id, t)
    held_state = timeline.held_state(rec.device_id, rec.held_version)
    if not center_state.allowed or not held_state.allowed:
        return REVOKED_ROSTER
    return COMPLIANT


def _cell_boundaries(timeline: CenterTimeline,
                     records: list[TerminalRecord]) -> list[int]:
    """裁定只在“发布事件点”和“离线截止点”处改变，收集全部边界。"""
    boundaries = {timeline.min_seq, timeline.max_seq + 1}
    for ev in timeline.events:
        boundaries.add(ev.center_seq)
    for rec in records:
        if rec.record_type == "session":
            boundaries.add(timeline.expiry_seq(rec.offline_deadline_second))
    return sorted(boundaries)


def _eligible_cells(boundaries: list[int], lo: int, hi: int):
    """生成与整数区间 [lo, hi] 相交的单元 (left, right_inclusive)。"""
    for j in range(len(boundaries) - 1):
        left = boundaries[j]
        right = boundaries[j + 1] - 1
        cell_lo = max(left, lo)
        cell_hi = min(right, hi)
        if cell_lo <= cell_hi:
            yield j, cell_lo, cell_hi, left


def _mapping_entry(timeline: CenterTimeline, rec: TerminalRecord, t: int) -> dict:
    return {
        "terminal_id": rec.terminal_id,
        "local_seq": rec.local_seq,
        "mapped_center_seq": t,
        "mapped_logical_second": timeline.second_at(t),
    }


# ---------------------------------------------------------------------------
# 单终端分析
# ---------------------------------------------------------------------------

@dataclass
class TerminalAnalysis:
    terminal_id: str
    records: list[TerminalRecord]
    actions: list[str | None]
    bounds: ChainBounds
    session_results: list[dict]
    max_violations: int
    witness_chain: list[int]
    session_verdicts_in_witness: dict[str, str]


def analyze_terminal(timeline: CenterTimeline, term: Terminal) -> tuple[TerminalAnalysis | None, list[dict]]:
    """返回 (分析结果, 冲突列表)；存在冲突时分析结果为 None。"""
    records = sorted(term.records, key=lambda r: r.local_seq)
    replay = replay_sync(records)
    conflicts = list(replay.held_conflicts)

    bounds = compute_bounds(timeline, records)
    for i, rec in enumerate(records):
        if bounds.earliest[i] > bounds.latest[i]:
            conflicts.append({
                "terminal_id": term.terminal_id,
                "local_seq": rec.local_seq,
                "code": "unsatisfiable_interval",
                "message": (
                    f"记录的中心序号闭区间 [{rec.center_seq_low}, "
                    f"{rec.center_seq_high}] 与同站单调性/因果约束无一致映射，"
                    f"可行时刻要求 [{bounds.earliest[i]}, {bounds.latest[i]}]"
                ),
            })
    if conflicts:
        return None, conflicts

    boundaries = _cell_boundaries(timeline, records)
    session_results: list[dict] = []

    for k, rec in enumerate(records):
        if rec.record_type != "session":
            continue
        lo, hi = bounds.earliest[k], bounds.latest[k]
        first_q_by_verdict: dict[str, int] = {}
        for _, cell_lo, _cell_hi, _left in _eligible_cells(boundaries, lo, hi):
            q = cell_lo  # 单元内裁定恒定，取最小点作为最小映射证据
            verdict = verdict_at(timeline, rec, q)
            if verdict not in first_q_by_verdict:
                first_q_by_verdict[verdict] = q

        reachable: list[dict] = []
        for verdict in VERDICT_PRIORITY:
            if verdict not in first_q_by_verdict:
                continue
            q = first_q_by_verdict[verdict]
            chain = build_chain(bounds.lower, bounds.upper, k, q)
            if chain is None:
                continue
            witness_mapping = [
                _mapping_entry(timeline, records[i], chain[i])
                for i in range(len(records))
            ]
            reachable.append({
                "verdict": verdict,
                "witness_mapping": witness_mapping,
            })
        session_results.append({
            "session_id": rec.session_id,
            "local_seq": rec.local_seq,
            "held_version": rec.held_version,
            "device_id": rec.device_id,
            "offline_deadline_second": rec.offline_deadline_second,
            "feasible_center_seq_range": [lo, hi],
            "reachable_verdicts": reachable,
        })

    # ---- 同一个映射下的最多违规会话数（单元 DP） ----
    n = len(records)
    cell_count = len(boundaries) - 1
    # 每条记录可选单元
    options: list[list[tuple[int, int, int]]] = []
    for i in range(n):
        opts = []
        rec = records[i]
        for j, cell_lo, _cell_hi, _left in _eligible_cells(
                boundaries, bounds.earliest[i], bounds.latest[i]):
            if rec.record_type == "session":
                bad = 0 if verdict_at(timeline, rec, cell_lo) == COMPLIANT else 1
            else:
                bad = 0
            opts.append((j, cell_lo, bad))
        options.append(opts)

    neg = -10**9
    dp = [[neg] * cell_count for _ in range(n)]
    back: list[list[int]] = [[-1] * cell_count for _ in range(n)]
    for j, _lo, bad in options[0]:
        dp[0][j] = bad
    for i in range(1, n):
        best_prev = neg
        best_j = -1
        for j in range(cell_count):
            # 维护 max dp[i-1][j']，j' <= j
            if dp[i - 1][j] > best_prev:
                best_prev = dp[i - 1][j]
                best_j = j
            if best_prev == neg:
                continue  # 此前没有可行前驱，不产生状态
            for jj, cell_lo, bad in options[i]:
                if jj == j:
                    dp[i][jj] = best_prev + bad
                    back[i][jj] = best_j
    # 重建
    last_j = max(range(cell_count), key=lambda j: dp[n - 1][j])
    max_violations = dp[n - 1][last_j]
    assert max_violations != neg, "区间可行性检查通过后 DP 不应无解"
    chosen_cell = [-1] * n
    chosen_cell[n - 1] = last_j
    for i in range(n - 1, 0, -1):
        chosen_cell[i - 1] = back[i][chosen_cell[i]]
        assert chosen_cell[i - 1] >= 0
    witness_chain = [0] * n
    for i in range(n):
        j = chosen_cell[i]
        witness_chain[i] = max(boundaries[j], bounds.earliest[i])

    witness_verdicts: dict[str, str] = {}
    for i, rec in enumerate(records):
        if rec.record_type == "session":
            witness_verdicts[rec.session_id] = verdict_at(
                timeline, rec, witness_chain[i])

    return TerminalAnalysis(
        terminal_id=term.terminal_id,
        records=records,
        actions=replay.actions,
        bounds=bounds,
        session_results=session_results,
        max_violations=max_violations,
        witness_chain=witness_chain,
        session_verdicts_in_witness=witness_verdicts,
    ), []


# ---------------------------------------------------------------------------
# 整案入口
# ---------------------------------------------------------------------------

def reconcile(timeline: CenterTimeline,
              terminals: list[Terminal]) -> dict:
    all_conflicts: list[dict] = []
    analyses: list[TerminalAnalysis] = []
    for term in sorted(terminals, key=lambda x: x.terminal_id):
        analysis, conflicts = analyze_terminal(timeline, term)
        all_conflicts.extend(conflicts)
        if analysis is not None:
            analyses.append(analysis)

    if all_conflicts:
        return {
            "status": "evidence_conflict",
            "conflicts": sorted(
                all_conflicts, key=lambda c: (c["terminal_id"], c["local_seq"])),
        }

    terminal_reports = []
    global_witness: list[dict] = []
    violating_sessions: list[dict] = []
    total_max = 0
    for an in analyses:
        record_reports = []
        for i, rec in enumerate(an.records):
            entry = rec.to_dict()
            entry["sync_action"] = an.actions[i]
            entry["feasible_center_seq_range"] = [
                an.bounds.earliest[i], an.bounds.latest[i]]
            record_reports.append(entry)
        terminal_reports.append({
            "terminal_id": an.terminal_id,
            "records": record_reports,
            "sessions": an.session_results,
            "max_violations": an.max_violations,
        })
        for i, rec in enumerate(an.records):
            global_witness.append(
                _mapping_entry(timeline, rec, an.witness_chain[i]))
        for sid, verdict in sorted(an.session_verdicts_in_witness.items()):
            if verdict != COMPLIANT:
                violating_sessions.append({
                    "terminal_id": an.terminal_id,
                    "session_id": sid,
                    "verdict": verdict,
                })
        total_max += an.max_violations

    return {
        "status": "ok",
        "terminals": terminal_reports,
        "summary": {
            "max_violations_under_one_mapping": total_max,
            "violating_sessions": violating_sessions,
            "witness_mapping": global_witness,
            "note": "各会话 reachable_verdicts 仅证明逐条可能性；本汇总在同一个"
                    "中央时间映射下求最多违规会话数，不可逐条相加。",
        },
    }
