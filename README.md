# 终端名册撤回后的离线会话断点核对

核对**外场终端**与**中心**两级认证结果。中心按顺序版本发布可用设备名册，
每个变更可禁用（`disable`）或恢复（`restore`）任一设备，设备的允许区间可随
“禁用→恢复”断成多段。本工具在所有符合因果顺序和对时区间的**中央时间映射**上，
判断每个终端会话可能落入的裁定，并在**同一个映射**下汇总最多会违规的会话数。

仅使用 Python 3.12 标准库与 CPU，无第三方依赖。

---

## 1. 目录结构

```
.
├── solution/                  # 可运行的对账程序（python -m solution）
│   ├── __init__.py
│   ├── __main__.py            # 入口
│   ├── cli.py                 # 命令行解析、UTF-8 JSON 输出、退出码
│   ├── errors.py              # InputError（错误码 + 字段位置）
│   ├── models.py              # 中心时间线、设备状态、终端记录
│   ├── validation.py          # 纯结构校验
│   ├── engine.py              # 同步重放、因果映射、裁定、最大违规汇总
│   └── persistence.py         # SQLite 台账与结果缓存（--db 时启用）
├── samples/                   # 7 个可运行的本地虚构消息样例
│   ├── sample_01_restore_hole.json
│   ├── sample_02_same_second_order.json
│   ├── sample_03_mutual_exclusion.json
│   ├── sample_04_sync_gap.json
│   ├── sample_05_retransmission.json
│   ├── sample_06_evidence_conflict.json
│   └── sample_07_invalid_structure.json
├── tests/                     # unittest 断言测试（45 个用例）
└── README.md
```

---

## 2. 环境与复现命令

- Windows 原生 Python 3.12（开发验证版本：3.12.8），仅标准库。
- 在仓库根目录执行：

```bash
# 对账（默认命令，也可显式写 reconcile）
python -m solution reconcile samples/sample_01_restore_hole.json

# 只做结构校验
python -m solution validate  samples/sample_01_restore_hole.json

# 涉及持久状态（重传台账 + 结果缓存）
python -m solution reconcile samples/sample_05_retransmission.json --db state.sqlite
python -m solution ledger    samples/sample_05_retransmission.json --db state.sqlite

# 运行全部断言测试
python -m unittest discover -s tests -v
```

stdout 始终返回 **UTF-8 编码的 JSON**（`ensure_ascii=False`，键排序固定），
逻辑输入不变时输出字节一致。

---

## 3. 形式模型

### 3.1 中心时间线

中心事件按 `(logical_second, center_seq)` 排序，`center_seq` 为全中心单调递增
整数，因此它单独就给出事件的全序位置。两个相邻事件之间的整数槽位代表
“上一事件发布之后、下一事件发布之前”的时刻，该槽位上的名册状态由最近一个
已发布事件决定。

设备初始状态为**允许**（基线名册，版本号 0）。名册版本从 1 开始、**连续**
发布；事件的 `changes` 描述本版本相对上一版本的设备变更。

### 3.2 终端同步语义

终端同步接口**只在收到连续版本后推进**，不允许跳过缺失版本：

- 收到下一个期望版本 `next_version` → `applied`，持有版本前进，并尝试排空
  缓冲区中的连续前缀；
- 收到更大的非连续版本 → 先进入缓冲区，动作记为 `buffered`；
- 收到已应用版本（重传）或缓冲区中已有的重复投递 → `replay`，幂等、不改变
  持有版本。

终端开会话时必须记录**已持有版本** `held_version` 与**中心授权的离线时限**
`offline_deadline_second`。会话声明的 `held_version` 若与按上述规则重放得到的
持有版本不一致，属于证据冲突（`held_version_mismatch`）。

### 3.3 对时不确定性与中央时间映射

每条终端审计记录带单调本地序号 `local_seq`，同逻辑秒时各终端记录**只相对
本地序号有序**。输入为每条记录给出中心序号**闭区间**
`center_seq_interval: [low, high]`，表示对时不确定性。

一个**中央时间映射**为每条记录在其闭区间内选择一个整数中心时刻 `t_i`，满足：

1. **同站单调性**：同一终端 `local_seq` 越大，映射时刻不得下降
   （`t_i ≤ t_{i+1}`，相等允许，表示同刻或同秒先后不明）；
2. **同步因果**：`sync` 记录的映射时刻不得早于所收版本的发布时刻
   （`t ≥ release_seq(version)`）。

同站记录映射出的中央时点不得随本地序号下降。不存在满足全部约束的映射时，
先报证据冲突（`unsatisfiable_interval`），不输出任何裁定。

### 3.4 离线截止换算

离线截止以逻辑秒给出：截止秒 `s` 表示逻辑秒 `s` 的**起始**时刻。
`expiry_seq(s)` 是满足 `second_at(t) ≥ s` 的最小槽位，即逻辑秒 `s` 内第一个
中心事件的 `center_seq`。因此截止秒晚于最后一个事件所在秒的会话无法对时，
在结构校验阶段拒绝。

### 3.5 裁定规则（固定优先级）

对会话记录映射到的时刻 `t`，按以下顺序判定，先命中先生效：

1. **离线时限失效 `offline_expired`**：`t ≥ expiry_seq(offline_deadline_second)`
   （会话时刻晚于或等于离线截止即判失效，相等也算）；
2. **用已撤销名册 `revoked_roster`**：未超时的前提下，只要**中心侧当前名册**
   或**终端持有版本名册**中任一禁用了该设备即判名册问题
   （`not center_allowed OR not held_allowed`）；
3. **合规 `compliant`**：其余情况。

### 3.6 可达裁定与最小映射见证

裁定只可能在“名册发布点”和“离线截止点”处改变。引擎把时间线切成裁定恒定的
单元，对每个会话枚举其可行区间相交到的单元：

- 会话在单元内任一点的裁定相同，因此每个**可达裁定**取该单元内最小整数点
  作为最小映射证据，并构造一条覆盖该终端**全部记录**的完整可行单调链
  （`witness_mapping`）；
- 不可行（被单调性/因果排除）的裁定不出现在结果中。

### 3.7 同一映射下的最大违规汇总

每个会话的 `reachable_verdicts` 只证明**逐条可能性**。汇总另求：在**同一个
中央时间映射**下，最多有多少会话同时违规。引擎以单元为状态做单调链动态规划
（O(记录数 × 单元数)），输出最大违规数
`max_violations_under_one_mapping`、达到该数的见证映射
`witness_mapping` 以及其中违规的会话清单 `violating_sessions`。
**不能把逐条可能违规数直接相加**——样例 3 专门演示这一点。

---

## 4. 输入格式（CASE.json）

```json
{
  "case_id": "可选标识字符串",
  "description": "可选说明",
  "center": {
    "events": [
      {
        "center_seq": 10,
        "logical_second": 100,
        "type": "roster_publication",
        "version": 1,
        "changes": [
          {"device_id": "D1", "action": "disable"}
        ]
      }
    ]
  },
  "terminals": [
    {
      "terminal_id": "T1",
      "records": [
        {"terminal_id": "T1", "local_seq": 1, "type": "sync",
         "version": 1, "center_seq_interval": [10, 12]},
        {"terminal_id": "T1", "local_seq": 2, "type": "session",
         "session_id": "S1", "held_version": 1, "device_id": "D1",
         "offline_deadline_second": 120, "center_seq_interval": [11, 30]}
      ]
    }
  ]
}
```

### 字段约束

| 字段 | 约束 |
|---|---|
| `center.events[].center_seq` | ≥0 整数，全中心唯一且严格递增 |
| `center.events[].logical_second` | ≥0 整数；`(logical_second, center_seq)` 严格升序（同秒靠 `center_seq` 排序） |
| `center.events[].type` | 固定 `roster_publication` |
| `center.events[].version` | ≥1 整数，从 1 开始连续发布，不允许缺号 |
| `center.events[].changes[]` | 非空数组；`action ∈ {disable, restore}`；同事件内设备不重复 |
| `terminals[].terminal_id` | 非空字符串，全站唯一 |
| `records[].local_seq` | ≥0 整数，同一终端内唯一（终端侧单调序号） |
| `records[].type` | `sync` 或 `session` |
| `records[].center_seq_interval` | 两个整数组成的闭区间 `[low, high]`，`low ≤ high`，且整体落在时间线 `[min_center_seq, max_center_seq]` 内 |
| `sync.version` | ≥1，且必须是中心已发布的版本 |
| `session.session_id` | 非空字符串，同一终端内唯一 |
| `session.held_version` | ≥0（0 表示仅基线名册）；>0 时必须是中心已发布版本 |
| `session.device_id` | 非空字符串，必须出现在某个中心名册变更中 |
| `session.offline_deadline_second` | ≥0，且不晚于中心最后事件所在逻辑秒 |

结构校验只判断“形状对不对”；同步不可行、映射无解等**语义**问题在引擎层产生
`evidence_conflict`，退出码仍为 0。

---

## 5. 输出格式

### 5.1 正常结果 `status: "ok"`

```json
{
  "status": "ok",
  "terminals": [
    {
      "terminal_id": "T1",
      "records": [
        {"terminal_id": "T1", "local_seq": 1, "type": "sync",
         "center_seq_interval": [10, 12], "version": 1,
         "sync_action": "applied",
         "feasible_center_seq_range": [10, 12]}
      ],
      "sessions": [
        {
          "session_id": "S1",
          "local_seq": 2,
          "held_version": 1,
          "device_id": "D1",
          "offline_deadline_second": 120,
          "feasible_center_seq_range": [11, 30],
          "reachable_verdicts": [
            {
              "verdict": "revoked_roster",
              "witness_mapping": [
                {"terminal_id": "T1", "local_seq": 1,
                 "mapped_center_seq": 10, "mapped_logical_second": 100}
              ]
            }
          ]
        }
      ],
      "max_violations": 2
    }
  ],
  "summary": {
    "max_violations_under_one_mapping": 3,
    "violating_sessions": [
      {"terminal_id": "T1", "session_id": "S2", "verdict": "offline_expired"}
    ],
    "witness_mapping": [ "覆盖所有终端全部记录的完整映射" ],
    "note": "各会话 reachable_verdicts 仅证明逐条可能性；本汇总在同一个中央时间映射下求最多违规会话数，不可逐条相加。"
  }
}
```

`sync_action ∈ {applied, buffered, replay}`，会话记录该字段为 `null`。
`feasible_center_seq_range` 是该记录在所有可行单调链中可取时刻的整体范围。

### 5.2 证据冲突 `status: "evidence_conflict"`（退出码 0）

```json
{
  "status": "evidence_conflict",
  "conflicts": [
    {"terminal_id": "T1", "local_seq": 2,
     "code": "unsatisfiable_interval", "message": "..."},
    {"terminal_id": "T2", "local_seq": 2, "session_id": "S2",
     "code": "held_version_mismatch",
     "declared_held_version": 2, "replayed_held_version": 1, "message": "..."}
  ]
}
```

### 5.3 结构错误 `status: "invalid_input"`（退出码 2）

```json
{
  "status": "invalid_input",
  "errors": [
    {"path": "/terminals/0/records/1/device_id",
     "code": "unknown_reference", "message": "设备 D9 未出现在任何中心名册变更中"}
  ]
}
```

`path` 为 JSON Pointer 风格字段位置（数组用下标），根节点错误路径为 `""`。

---

## 6. 错误码与退出码

### 结构错误码

| code | 含义 |
|---|---|
| `type_error` | 字段类型错误（含根节点不是对象、`bool` 不被当作整数） |
| `missing_field` | 必填字段缺失或为空数组 |
| `invalid_value` | 取值非法（负数、非法枚举、空字符串、JSON 语法错误 `json_parse_error`） |
| `duplicate_value` | 唯一键重复（`center_seq`、`local_seq`、`session_id`、同事件设备等） |
| `range_violation` | 区间下界大于上界、区间超出时间线、离线截止秒超出可对时范围 |
| `unknown_reference` | 引用了不存在的版本或设备 |
| `ordering_violation` | 事件未按 `(logical_second, center_seq)` 升序、`center_seq` 未递增、名册版本缺号 |

### 语义冲突码（`evidence_conflict`）

| code | 含义 |
|---|---|
| `unsatisfiable_interval` | 闭区间与同站单调性/同步因果无一致映射 |
| `held_version_mismatch` | 会话声明的持有版本与连续同步重放结果不符 |

### 进程退出码

| 退出码 | 情形 |
|---|---|
| `0` | 对账完成（含 `ok` 与 `evidence_conflict`）、结构校验通过 |
| `2` | 输入结构错误或 JSON 解析失败、`ledger` 缺少 `--db` |
| `1` | 文件不存在等 I/O 错误（`io_error`） |

---

## 7. 持久化（`--db DB.sqlite`）

仅当传入 `--db` 时启用，SQLite 文件自动创建，含两张表：

- `record_ledger`：以 `(case_hash, terminal_id, local_seq)` 为幂等键的投递台账。
  同一 CASE（按输入字节 sha256 分区）再次投递时，已存在记录的 `replay_count`
  累加，用于核对重传；
- `case_cache`：按 `case_hash` 缓存对账结果（`sort_keys` 序列化）。第二次对账
  同一 CASE 时缓存命中，输出中 `persistence.cache` 为 `hit`（首次为 `stored`），
  业务结果与首跑完全一致。

`ledger` 命令输出该 CASE 的台账快照；不传 `--db` 时拒绝执行（退出码 2）。

---

## 8. 样例一览

| 样例 | 覆盖点 | 预期要点 |
|---|---|---|
| `sample_01_restore_hole.json` | 恢复造成的中间空洞 | D1 禁-恢复-禁；S1 同时可达 `revoked_roster` 与 `offline_expired`；S3 中心已恢复但终端仍持撤销版本 → 名册问题；同一映射最多 3 个违规 |
| `sample_02_same_second_order.json` | 同秒顺序改变 | S1 映射到同逻辑秒 50 的 seq 10/11 时在 `compliant`/`revoked_roster` 间切换 |
| `sample_03_mutual_exclusion.json` | 跨多会话不能同时犯规 | S1 仅可能晚段时限失效、S2 仅可能早段名册撤销；逐条相加得 2，同一映射最多 **1**（测试含暴力枚举独立验证） |
| `sample_04_sync_gap.json` | 同步版本缺口 | v3 先 `buffered`，会话仍只持 v1；声明 held=3 → `held_version_mismatch` |
| `sample_05_retransmission.json` | 重传 | 重复投递记 `replay` 且不改持有版本；`--db` 台账跨运行累计、缓存命中结果一致 |
| `sample_06_evidence_conflict.json` | 证据冲突 | 同站区间无一致映射（`unsatisfiable_interval`）+ 持有版本不符，同时报出 |
| `sample_07_invalid_structure.json` | 结构错误 | 版本缺号、非法 `action`、未知设备/版本、区间越界、重复本地序号、截止秒越界，一次报齐（退出码 2） |

---

## 9. 确定性保证

- 所有集合迭代前显式排序（终端按 `terminal_id`、记录按 `local_seq`、
  冲突按 `(terminal_id, local_seq)`、JSON 键 `sort_keys`）；
- 引擎不读取时钟、不使用随机数；SQLite 时间戳只进台账字段，不参与对账结果；
- 逻辑输入不变时，任意次运行的业务输出字节一致（测试
  `test_repeated_runs_byte_identical` 守护）。

## 10. 测试

```bash
python -m unittest discover -s tests -v
```

共 45 个用例，分布在 8 个模块中，覆盖题目要求的全部场景：恢复造成的中间空洞、
同秒顺序改变、跨多会话不能同时犯规（含独立暴力枚举对照）、同步版本缺口与
缓冲排空、重传幂等与持久化、两类证据冲突、结构错误码与字段位置、截止相等
边界、闭区间端点、裁定优先级、UTF-8 输出与字节级确定性。
