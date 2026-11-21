"""纯结构校验：错误码 + 字段位置（JSON Pointer 风格路径）。

校验只关心输入“长得对不对”，不做任何因果可行性判断；后者由 engine 负责，
不可行时报告 evidence_conflict（语义冲突），而不是结构错误。
"""

from __future__ import annotations

from typing import Any

from .errors import InputError
from .models import CenterEvent, CenterTimeline, Terminal, TerminalRecord

VALID_ACTIONS = ("disable", "restore")
VALID_EVENT_TYPES = ("roster_publication",)
VALID_RECORD_TYPES = ("sync", "session")


def _is_int(x: Any) -> bool:
    # bool 是 int 的子类，显式排除
    return isinstance(x, int) and not isinstance(x, bool)


def _is_nonempty_str(x: Any) -> bool:
    return isinstance(x, str) and len(x) > 0


def parse_case(raw: Any) -> tuple[CenterTimeline | None, list[Terminal] | None, list[InputError]]:
    """解析并校验一个 CASE。返回 (时间线, 终端列表, 错误列表)；有错误时前两者为 None。"""
    errors: list[InputError] = []

    if not isinstance(raw, dict):
        errors.append(InputError("", "type_error", "根节点必须是对象"))
        return None, None, errors

    events = _parse_center(raw.get("center"), errors)
    terminals_raw = _parse_terminals_container(raw, errors)

    # 收集中心侧已知设备与版本，供终端记录交叉引用校验
    known_devices: set[str] = set()
    known_versions: set[int] = set()
    for ev in events or []:
        known_versions.add(ev.version)
        for dev, _ in ev.changes:
            known_devices.add(dev)

    terminals: list[Terminal] = []
    if isinstance(terminals_raw, list):
        seen_terminal_ids: set[str] = set()
        for ti, traw in enumerate(terminals_raw):
            term = _parse_terminal(traw, ti, known_devices, known_versions,
                                   events, seen_terminal_ids, errors)
            if term is not None:
                terminals.append(term)

    if errors:
        return None, None, errors

    timeline = CenterTimeline(events)  # type: ignore[arg-type]
    return timeline, terminals, errors


def _parse_center(center: Any, errors: list[InputError]) -> list[CenterEvent] | None:
    if not isinstance(center, dict):
        errors.append(InputError("/center", "type_error", "center 必须是对象"))
        return None
    events_raw = center.get("events")
    if not isinstance(events_raw, list) or len(events_raw) == 0:
        errors.append(InputError("/center/events", "missing_field",
                                 "center.events 必须是非空数组"))
        return None

    events: list[CenterEvent] = []
    seen_seqs: set[int] = set()
    err_count_before = len(errors)
    raw_keys: list[tuple[Any, Any, Any]] = []
    for i, ev in enumerate(events_raw):
        base = f"/center/events/{i}"
        if not isinstance(ev, dict):
            errors.append(InputError(base, "type_error", "事件必须是对象"))
            raw_keys.append((None, None, None))
            continue

        seq = ev.get("center_seq")
        if not _is_int(seq) or seq < 0:
            errors.append(InputError(f"{base}/center_seq", "invalid_value",
                                     "center_seq 必须是 >=0 的整数"))
        elif seq in seen_seqs:
            errors.append(InputError(f"{base}/center_seq", "duplicate_value",
                                     f"center_seq {seq} 重复"))
        else:
            seen_seqs.add(seq)

        second = ev.get("logical_second")
        if not _is_int(second) or second < 0:
            errors.append(InputError(f"{base}/logical_second", "invalid_value",
                                     "logical_second 必须是 >=0 的整数"))

        etype = ev.get("type")
        if etype not in VALID_EVENT_TYPES:
            errors.append(InputError(f"{base}/type", "invalid_value",
                                     f"事件类型必须是 {VALID_EVENT_TYPES[0]}"))

        version = ev.get("version")
        if not _is_int(version) or version < 1:
            errors.append(InputError(f"{base}/version", "invalid_value",
                                     "version 必须是 >=1 的整数"))

        changes = _parse_changes(ev.get("changes"), base, errors)
        raw_keys.append((seq, second, version))
        if (_is_int(seq) and _is_int(second) and _is_int(version) and version >= 1
                and etype in VALID_EVENT_TYPES and changes is not None):
            events.append(CenterEvent(seq, second, version, tuple(changes)))

    # 全序与版本规则：即使个别事件字段有问题，也尽量在原始字段上给出结构错误
    valid_keys = [(s, c, v) for s, c, v in raw_keys
                  if _is_int(s) and _is_int(c) and _is_int(v)]
    if len(valid_keys) >= 2:
        order_key = [(c, s) for s, c, v in valid_keys]
        if any(order_key[i] >= order_key[i + 1] for i in range(len(order_key) - 1)):
            errors.append(InputError("/center/events", "ordering_violation",
                                     "事件必须按 (logical_second, center_seq) 严格升序排列"))
        seqs = [s for s, c, v in valid_keys]
        if any(seqs[i] >= seqs[i + 1] for i in range(len(seqs) - 1)):
            errors.append(InputError("/center/events", "ordering_violation",
                                     "center_seq 必须严格递增"))
    versions = [v for s, c, v in valid_keys]
    if versions and versions != list(range(1, len(versions) + 1)):
        errors.append(InputError("/center/events", "ordering_violation",
                                 "名册版本必须从 1 开始连续发布，不允许缺号"))

    if len(errors) > err_count_before:
        return None
    return events


def _parse_changes(changes: Any, base: str, errors: list[InputError]):
    if not isinstance(changes, list) or len(changes) == 0:
        errors.append(InputError(f"{base}/changes", "missing_field",
                                 "changes 必须是非空数组"))
        return None
    parsed: list[tuple[str, str]] = []
    seen_devices: set[str] = set()
    err_count_before = len(errors)
    for j, ch in enumerate(changes):
        cbase = f"{base}/changes/{j}"
        if not isinstance(ch, dict):
            errors.append(InputError(cbase, "type_error", "变更项必须是对象"))
            continue
        dev = ch.get("device_id")
        if not _is_nonempty_str(dev):
            errors.append(InputError(f"{cbase}/device_id", "invalid_value",
                                     "device_id 必须是非空字符串"))
        elif dev in seen_devices:
            errors.append(InputError(f"{cbase}/device_id", "duplicate_value",
                                     f"同一事件中设备 {dev} 出现多次"))
        else:
            seen_devices.add(dev)
        action = ch.get("action")
        if action not in VALID_ACTIONS:
            errors.append(InputError(f"{cbase}/action", "invalid_value",
                                     f"action 必须是 {VALID_ACTIONS} 之一"))
        if _is_nonempty_str(dev) and action in VALID_ACTIONS:
            parsed.append((dev, action))
    return parsed if len(errors) == err_count_before else None


def _parse_terminals_container(raw: dict, errors: list[InputError]) -> Any:
    terminals = raw.get("terminals")
    if not isinstance(terminals, list) or len(terminals) == 0:
        errors.append(InputError("/terminals", "missing_field",
                                 "terminals 必须是非空数组"))
        return None
    return terminals


def _parse_terminal(traw: Any, ti: int, known_devices: set[str],
                    known_versions: set[int], events: list[CenterEvent] | None,
                    seen_terminal_ids: set[str],
                    errors: list[InputError]) -> Terminal | None:
    base = f"/terminals/{ti}"
    if not isinstance(traw, dict):
        errors.append(InputError(base, "type_error", "终端必须是对象"))
        return None

    tid = traw.get("terminal_id")
    if not _is_nonempty_str(tid):
        errors.append(InputError(f"{base}/terminal_id", "invalid_value",
                                 "terminal_id 必须是非空字符串"))
    elif tid in seen_terminal_ids:
        errors.append(InputError(f"{base}/terminal_id", "duplicate_value",
                                 f"终端 {tid} 重复"))
    else:
        seen_terminal_ids.add(tid)

    records_raw = traw.get("records")
    if not isinstance(records_raw, list) or len(records_raw) == 0:
        errors.append(InputError(f"{base}/records", "missing_field",
                                 "records 必须是非空数组"))
        return None

    max_second = max(e.logical_second for e in events) if events else 0
    min_seq = min(e.center_seq for e in events) if events else 0
    max_seq = max(e.center_seq for e in events) if events else 0

    term = Terminal(tid if _is_nonempty_str(tid) else f"__invalid_{ti}")
    seen_local: set[int] = set()
    seen_sessions: set[str] = set()
    for ri, rraw in enumerate(records_raw):
        rec = _parse_record(rraw, f"{base}/records/{ri}", seen_local, seen_sessions,
                            known_devices, known_versions, max_second, min_seq,
                            max_seq, errors)
        if rec is not None:
            term.records.append(rec)
    return term


def _parse_record(rraw: Any, base: str, seen_local: set[int], seen_sessions: set[str],
                  known_devices: set[str], known_versions: set[int], max_second: int,
                  min_seq: int, max_seq: int, errors: list[InputError]) -> TerminalRecord | None:
    if not isinstance(rraw, dict):
        errors.append(InputError(base, "type_error", "记录必须是对象"))
        return None

    local_seq = rraw.get("local_seq")
    if not _is_int(local_seq) or local_seq < 0:
        errors.append(InputError(f"{base}/local_seq", "invalid_value",
                                 "local_seq 必须是 >=0 的整数"))
    elif local_seq in seen_local:
        errors.append(InputError(f"{base}/local_seq", "duplicate_value",
                                 f"本地序号 {local_seq} 在同一终端内重复"))
    else:
        seen_local.add(local_seq)

    interval = rraw.get("center_seq_interval")
    low = high = None
    if not isinstance(interval, list) or len(interval) != 2 or \
            not all(_is_int(x) for x in interval):
        errors.append(InputError(f"{base}/center_seq_interval", "invalid_value",
                                 "center_seq_interval 必须是两个整数组成的数组 [low, high]"))
    else:
        low, high = interval
        if low > high:
            errors.append(InputError(f"{base}/center_seq_interval", "range_violation",
                                     "闭区间下界不得大于上界"))
        elif not (min_seq <= low and high <= max_seq):
            errors.append(InputError(f"{base}/center_seq_interval", "range_violation",
                                     f"区间必须落在中心时间线 [{min_seq}, {max_seq}] 内"))

    rtype = rraw.get("type")
    if rtype not in VALID_RECORD_TYPES:
        errors.append(InputError(f"{base}/type", "invalid_value",
                                 f"记录类型必须是 {VALID_RECORD_TYPES} 之一"))
        return None

    tid_field = rraw.get("terminal_id")
    if not _is_nonempty_str(tid_field):
        errors.append(InputError(f"{base}/terminal_id", "invalid_value",
                                 "记录缺少非空字符串 terminal_id"))

    if rtype == "sync":
        version = rraw.get("version")
        if not _is_int(version) or version < 1:
            errors.append(InputError(f"{base}/version", "invalid_value",
                                     "sync 记录的 version 必须是 >=1 的整数"))
        elif version not in known_versions:
            errors.append(InputError(f"{base}/version", "unknown_reference",
                                     f"版本 {version} 在中心名册中不存在"))
        if not _is_int(local_seq) or low is None or not _is_nonempty_str(tid_field):
            return None
        return TerminalRecord(tid_field, local_seq, "sync", version=version,
                              center_seq_low=low, center_seq_high=high)

    # session
    session_id = rraw.get("session_id")
    if not _is_nonempty_str(session_id):
        errors.append(InputError(f"{base}/session_id", "invalid_value",
                                 "session 记录缺少非空字符串 session_id"))
    elif session_id in seen_sessions:
        errors.append(InputError(f"{base}/session_id", "duplicate_value",
                                 f"会话 {session_id} 在多条记录中出现"))
    else:
        seen_sessions.add(session_id)

    held = rraw.get("held_version")
    if not _is_int(held) or held < 0:
        errors.append(InputError(f"{base}/held_version", "invalid_value",
                                 "held_version 必须是 >=0 的整数"))
    elif held > 0 and held not in known_versions:
        errors.append(InputError(f"{base}/held_version", "unknown_reference",
                                 f"持有版本 {held} 在中心名册中不存在"))

    device_id = rraw.get("device_id")
    if not _is_nonempty_str(device_id):
        errors.append(InputError(f"{base}/device_id", "invalid_value",
                                 "session 记录缺少非空字符串 device_id"))
    elif device_id not in known_devices:
        errors.append(InputError(f"{base}/device_id", "unknown_reference",
                                 f"设备 {device_id} 未出现在任何中心名册变更中"))

    deadline = rraw.get("offline_deadline_second")
    if not _is_int(deadline) or deadline < 0:
        errors.append(InputError(f"{base}/offline_deadline_second", "invalid_value",
                                 "offline_deadline_second 必须是 >=0 的整数"))
    elif deadline > max_second:
        errors.append(InputError(f"{base}/offline_deadline_second", "range_violation",
                                 f"离线截止秒 {deadline} 晚于中心最后事件所在秒 {max_second}，无法对时"))

    if not _is_int(local_seq) or low is None or not _is_nonempty_str(tid_field) \
            or not _is_nonempty_str(session_id) or not _is_int(held) \
            or not _is_nonempty_str(device_id) or not _is_int(deadline):
        return None
    return TerminalRecord(tid_field, local_seq, "session", session_id=session_id,
                          held_version=held, device_id=device_id,
                          offline_deadline_second=deadline,
                          center_seq_low=low, center_seq_high=high)
