"""终端名册撤回后的离线会话断点核对（仅标准库）。

模块划分：
- errors:     输入结构错误的数据类
- models:     中心时间线、终端记录等领域模型
- validation: 纯结构校验（错误码 + 字段位置）
- engine:     因果映射、可达裁定、最大违规汇总
- persistence: SQLite 持久化（重传台账 + 结果缓存）
- cli:        命令行入口
"""

__all__ = ["errors", "models", "validation", "engine", "persistence", "cli"]
