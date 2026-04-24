"""配置相关的环境变量加载。"""

from pathlib import Path

from dotenv import load_dotenv


def load_environment_files(config_path: Path) -> None:
    """加载与当前配置文件相关的 `.env` 文件。

    优先读取配置目录下的 `.env`，再补充当前工作目录下的 `.env`。
    已存在于进程环境中的变量保持不变。
    """

    candidates = [config_path.parent / ".env", Path.cwd() / ".env"]
    loaded: set[Path] = set()

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in loaded or not resolved.exists():
            continue
        load_dotenv(resolved, override=False)
        loaded.add(resolved)
