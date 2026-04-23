"""工具定义的数据模型。"""

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(slots=True)
class ToolDefinition:
    """描述一个可供模型调用的工具。

    - `name` / `description` 面向模型
    - `schema` 提供参数约束
    - `handler` 是真正执行动作的 Python 函数
    """

    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[..., str]
