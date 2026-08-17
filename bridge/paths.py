"""paths：文件发送路径分级（_send_file 单点执行）。

三级规则（鑫 2026-08-17 定案）：
- "default"：个人目录直发（免确认）
- "gate"：其余路径 → 微信确认后发送
- "reject"：token/密钥相关，无论哪个通道、gate 是否批准都拒绝

清单可配置：config.yaml file_send.*（default_dirs / reject_dirs / reject_name_re / reject_suffixes）。
防绕过：resolve() 规范化（../ 收敛）+ 符号链接目标重新分级。
"""
from __future__ import annotations

import re
from pathlib import Path

from modules.common.config import get

HOME = Path.home()

# 默认允许直发的个人目录（含子目录，可配置）
DEFAULT_ALLOW_DIRS = [Path(d).expanduser() for d in get("file_send.default_dirs")]

# 硬拒目录（密钥类整体敏感；token 文件由 REJECT_NAME_RE 文件名模式覆盖，不整目录拒）
REJECT_DIRS = [Path(d).expanduser() for d in get("file_send.reject_dirs")]

# 硬拒文件名模式（兜底，不依赖路径位置）
REJECT_NAME_RE = re.compile(get("file_send.reject_name_re"), re.IGNORECASE)
REJECT_SUFFIXES = set(get("file_send.reject_suffixes"))


def _resolve(p: Path) -> Path:
    """规范化：绝对化 + 收敛 .. + 解析符号链接（不存在时用父链解析）。"""
    p = p.expanduser()
    if not p.is_absolute():
        p = HOME / p
    try:
        return p.resolve(strict=False)
    except OSError:
        return p.absolute()


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def classify(path: str | Path) -> str:
    """返回 "default" / "gate" / "reject"。"""
    target = _resolve(Path(path))
    name = target.name

    if REJECT_NAME_RE.search(name) or target.suffix.lower() in REJECT_SUFFIXES:
        return "reject"
    for d in REJECT_DIRS:
        if _is_within(target, d.resolve()):
            return "reject"
    for d in DEFAULT_ALLOW_DIRS:
        if _is_within(target, d.resolve()):
            return "default"
    return "gate"