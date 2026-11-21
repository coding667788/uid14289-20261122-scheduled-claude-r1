"""输入结构错误模型。错误码与字段位置（JSON Pointer 风格路径）用于说明输入结构问题。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InputError:
    """单条结构校验错误。

    path:    出错字段位置，例如 "/center/events/2/center_seq"
    code:    稳定错误码（见 README“错误码”一节）
    message: 中文说明
    """

    path: str
    code: str
    message: str

    def to_dict(self) -> dict:
        return {"path": self.path, "code": self.code, "message": self.message}
