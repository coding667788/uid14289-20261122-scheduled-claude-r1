"""领域模型：中心时间线（事件）与终端审计记录。

形式化约定（详见 README“形式模型”）：

中心时间线是事件的全序，按 (logical_second, center_seq) 排序；center_seq 为
全中心单调递增整数，因此也可单独作为时间线位置。事件之间的整数槽位代表
“上一事件之后、下一事件之前”的时刻，状态由最近一个已发布事件决定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class CenterEvent:
    """中心事件。本实现中事件均为名册版本发布。"""

    center_seq: int
    logical_second: int
    version: int
    changes: tuple[tuple[str, str], ...]  # (device_id, "disable" | "restore")


@dataclass
class DeviceState:
    """设备在某一中心时间点的名册状态。"""

    allowed: bool
    # 该状态由哪个名册版本决定（用于说明“中心持有版本”）
    version: int


@dataclass
class TerminalRecord:
    """终端审计记录（带单调本地序号 local_seq）。"""

    terminal_id: str
    local_seq: int
    record_type: str  # "sync" | "session"
    # sync 专用
    version: Optional[int] = None
    # session 专用
    session_id: Optional[str] = None
    held_version: Optional[int] = None
    device_id: Optional[str] = None
    offline_deadline_second: Optional[int] = None
    # 对时不确定性：中心序号闭区间
    center_seq_low: Optional[int] = None
    center_seq_high: Optional[int] = None

    def to_dict(self) -> dict:
        d: dict = {
            "terminal_id": self.terminal_id,
            "local_seq": self.local_seq,
            "type": self.record_type,
            "center_seq_interval": [self.center_seq_low, self.center_seq_high],
        }
        if self.record_type == "sync":
            d["version"] = self.version
        else:
            d["session_id"] = self.session_id
            d["held_version"] = self.held_version
            d["device_id"] = self.device_id
            d["offline_deadline_second"] = self.offline_deadline_second
        return d


@dataclass
class Terminal:
    terminal_id: str
    records: list[TerminalRecord] = field(default_factory=list)


class CenterTimeline:
    """中心全序时间线，提供版本发布位置、设备状态与离线截止换算。"""

    def __init__(self, events: list[CenterEvent]):
        self.events = sorted(events, key=lambda e: e.center_seq)
        self._release_seq: dict[int, int] = {}
        for e in self.events:
            self._release_seq[e.version] = e.center_seq
        # 设备初始状态为 allowed（样例与 README 约定），版本 0 即基线名册
        self._baseline_allowed = True

    # ---- 基本查询 ----

    @property
    def min_seq(self) -> int:
        return self.events[0].center_seq

    @property
    def max_seq(self) -> int:
        return self.events[-1].center_seq

    def release_seq(self, version: int) -> int:
        """版本 version 发布事件的中心序号。version 0 视为时间线起点之前的基线。"""
        if version == 0:
            return self.min_seq
        return self._release_seq[version]

    def second_at(self, seq: int) -> int:
        """槽位 seq 所在逻辑秒（取不晚于 seq 的最后一个事件的逻辑秒）。"""
        second = self.events[0].logical_second
        for e in self.events:
            if e.center_seq <= seq:
                second = e.logical_second
            else:
                break
        return second

    def expiry_seq(self, offline_deadline_second: int) -> int:
        """把“中心授权的离线截止逻辑秒”换算为中心序号槽位。

        规则：截止秒 s 表示逻辑秒 s 的起始时刻；槽位 t 满足
        second_at(t) >= s 即视为 t >= 截止时刻。返回满足该条件的最小槽位，
        即逻辑秒 s 内第一个事件的 center_seq（秒严格递增，事件按秒排列）。
        若截止秒晚于最后一个事件所在秒，则越界（结构校验阶段拒绝）。
        """
        for e in self.events:
            if e.logical_second >= offline_deadline_second:
                return e.center_seq
        raise KeyError(offline_deadline_second)

    # ---- 名册状态 ----

    def state_at(self, device_id: str, seq: int) -> DeviceState:
        """设备 device_id 在中心槽位 seq 的中心侧名册状态。"""
        allowed = self._baseline_allowed
        version = 0
        for e in self.events:
            if e.center_seq > seq:
                break
            version = e.version
            for dev, action in e.changes:
                if dev == device_id:
                    allowed = action == "restore"
        return DeviceState(allowed=allowed, version=version)

    def held_state(self, device_id: str, held_version: int) -> DeviceState:
        """设备在“终端持有的名册版本 held_version”下的状态。

        即把该版本发布事件应用完毕后的名册。
        """
        if held_version == 0:
            return DeviceState(allowed=self._baseline_allowed, version=0)
        release = self._release_seq[held_version]
        return self.state_at(device_id, release)
