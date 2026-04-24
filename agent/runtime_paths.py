"""入口层使用的运行时路径解析。"""

from pathlib import Path

from agent.config import load_environment_files, load_settings


def resolve_base_workspace(config_path: Path) -> Path:
    """根据配置解析入口层应使用的基础 workspace。"""

    resolved_config = Path(config_path).resolve()
    load_environment_files(resolved_config)
    return load_settings(resolved_config).agent.workspace_dir
