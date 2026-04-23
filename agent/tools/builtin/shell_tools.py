"""Shell 执行工具 - 需要审批"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def shell_exec(workspace_dir: Path, command: str, timeout: int = 30) -> str:
    """执行 Shell 命令（需要审批）。
    
    Args:
        workspace_dir: 工作区目录（命令执行的工作目录）
        command: 要执行的命令
        timeout: 超时秒数（默认 30）
        
    Returns:
        命令输出（stdout + stderr）
        
    注意:
    - 此工具需要人工审批才能执行
    - 命令在 workspace 目录下执行
    - 自动设置超时限制
    """
    logger.info(f"Executing shell command: {command}")

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        output = []
        if result.stdout:
            output.append(f"stdout:\n{result.stdout}")
        if result.stderr:
            output.append(f"stderr:\n{result.stderr}")

        output.append(f"exit_code: {result.returncode}")

        return "\n\n".join(output)

    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout} seconds"
    except Exception as e:
        logger.error(f"Shell execution error: {e}", exc_info=True)
        return f"Error: {str(e)}"
