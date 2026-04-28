"""Web 搜索工具"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def web_search(query: str, num_results: int = 5) -> str:
    """Web 搜索（基于 DuckDuckGo）。
    
    Args:
        query: 搜索查询
        num_results: 返回结果数量（默认 5）
        
    Returns:
        搜索结果摘要
        
    注意:
    - 使用 DuckDuckGo 搜索（无需 API Key）
    - 返回标题、摘要和链接
    """
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return (
                "网页搜索不可用：未安装 ddgs。"
                "可执行 `pip install ddgs` 安装。"
            )

    try:
        logger.info(f"Web search: {query}")

        ddgs = DDGS()
        results = list(ddgs.text(query, max_results=num_results))

        if not results:
            return f"没有找到与以下查询相关的结果：{query}"

        # 格式化输出
        lines = [f"搜索结果：{query}\n"]
        for i, result in enumerate(results, 1):
            title = result.get("title", "无标题")
            snippet = result.get("body", "无摘要")
            url = result.get("href", "")

            lines.append(f"{i}. {title}")
            lines.append(f"   {snippet}")
            lines.append(f"   URL: {url}\n")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Web search error: {e}", exc_info=True)
        return f"搜索失败：{str(e)}"
