"""统一日志：JSON Lines 落盘 logs/system.log，轮转参数可配（config.yaml log.*）。体系唯一日志出口。

用法: from common import log_event; log_event("WARN", "todo", "push_fail", "detail...")
"""
from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from bridge.config import get

WORKDIR = Path(__file__).resolve().parent.parent.parent
LOG_DIR = WORKDIR / "logs"
LOG_FILE = LOG_DIR / "system.log"

_logger: logging.Logger | None = None


def _get() -> logging.Logger:
    global _logger
    if _logger is None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _logger = logging.getLogger("wechat-system")
        _logger.setLevel(logging.INFO)
        if not _logger.handlers:
            handler = RotatingFileHandler(
                LOG_FILE,
                maxBytes=get("log.rotate_mb") * 1024 * 1024,
                backupCount=get("log.backup_count"),
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            _logger.addHandler(handler)
    return _logger


def log_event(level: str, module: str, event: str, detail: str = "", **extra) -> None:
    """记录一条结构化事件。level: DEBUG/INFO/WARN/ERROR。"""
    rec = {"level": level.upper(), "module": module, "event": event, "detail": detail}
    rec.update(extra)
    text = json.dumps(rec, ensure_ascii=False)
    getattr(_get(), level.lower(), _get().info)(text)