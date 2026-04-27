"""结构化日志 - 敏感数据脱敏"""

import logging
import re
from pathlib import Path


class SensitiveDataFilter(logging.Filter):
    """敏感数据过滤器 - 脱敏 API Key、Token 等"""

    PATTERNS = [
        # API Keys
        (r"(api[_-]?key['\"]?\s*[:=]\s*['\"]?)([A-Za-z0-9_-]{20,})", r"\1***REDACTED***"),
        (r"(sk-[A-Za-z0-9]{20,})", r"sk-***REDACTED***"),
        (r"(claude-[A-Za-z0-9]{20,})", r"claude-***REDACTED***"),
        (r"((?:access_key|ticket)=)([^&\s]+)", r"\1***REDACTED***"),
        # Bearer tokens
        (r"(Bearer\s+)([A-Za-z0-9_.-]+)", r"\1***REDACTED***"),
        # 密码
        (r"(password['\"]?\s*[:=]\s*['\"]?)([^'\"]+)", r"\1***REDACTED***"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        """过滤日志记录中的敏感数据"""
        if hasattr(record, "msg"):
            msg = str(record.msg)
            for pattern, replacement in self.PATTERNS:
                msg = re.sub(pattern, replacement, msg, flags=re.IGNORECASE)
            record.msg = msg

        return True


def setup_logging(
    log_file: Path,
    level: str = "INFO",
    enable_sensitive_filter: bool = True,
) -> None:
    """配置结构化日志
    
    Args:
        log_file: 日志文件路径
        level: 日志级别（DEBUG/INFO/WARNING/ERROR）
        enable_sensitive_filter: 是否启用敏感数据过滤
    """
    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # 配置格式
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 文件 Handler
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    # 控制台 Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # 添加敏感数据过滤器
    if enable_sensitive_filter:
        sensitive_filter = SensitiveDataFilter()
        file_handler.addFilter(sensitive_filter)
        console_handler.addFilter(sensitive_filter)

    # 配置 root logger
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    root_logger.setLevel(getattr(logging, level.upper()))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # 降低第三方库日志级别
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    logging.info(f"Logging initialized: level={level}, file={log_file}")
