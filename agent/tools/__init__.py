"""工具系统公开入口。

这里把“工具长什么样”和“工具怎么执行”分开：
1. ToolRegistry 负责登记工具
2. ToolExecutor 负责调用工具

这样核心循环只依赖抽象接口，不必关心每个工具的细节。
"""

from agent.tools.executor import ToolExecutor
from agent.tools.registry import ToolRegistry, build_stage1_registry

__all__ = ["ToolExecutor", "ToolRegistry", "build_stage1_registry"]
