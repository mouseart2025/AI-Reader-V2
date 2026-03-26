"""统一日志 — 控制台 + 文件输出，按天轮转"""

from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_initialized = False


def setup_logger(
    name: str = "marketing",
    log_dir: str | Path = "./logs",
    level: int = logging.INFO,
) -> logging.Logger:
    """初始化日志系统，首次调用创建 handler，后续调用返回已有 logger"""
    global _initialized

    logger = logging.getLogger(name)

    if _initialized:
        return logger

    logger.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # 文件 — 按天轮转，保留 30 天
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        log_path / "marketing.log",
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    _initialized = True
    return logger


def get_logger(module: str = "marketing") -> logging.Logger:
    """获取子 logger（自动继承根 logger 配置）"""
    root = setup_logger()
    return root.getChild(module) if module != "marketing" else root
