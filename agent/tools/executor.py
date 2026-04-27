"""工具执行器。

一期这里故意写得很薄，但多做了一件重要的事：
把工具异常收束成字符串结果，避免工具失败直接把主循环打崩。
"""

from agent.tools.registry import ToolRegistry


class ToolExecutor:
    """从注册表里取工具并执行。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def execute(self, name: str, params: dict, *, context=None) -> str:
        """执行单个工具调用。

        这里统一兜底异常，是为了让上层把错误当成“工具结果”继续处理，
        而不是让整个 Agent 进程直接中断。
        """

        tool = self.registry.get(name)
        try:
            if tool.accepts_context:
                return tool.handler(context=context, **params)
            return tool.handler(**params)
        except Exception as exc:
            return f"Tool execution failed: {type(exc).__name__}: {exc}"
