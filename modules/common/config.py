"""config：基础设施配置层（读 ~/.config/wechat-claw/config.yaml）。

- 文件不存在 → 全部用内置默认（= 改造前的硬编码常量，老用户零影响）
- 优先级：config.yaml > 内置默认（无环境变量覆盖层）
- 键访问用点号路径：get("push.port") → config["push"]["port"]

新用户/分发场景：由初始化向导或手工复制 config.yaml.example 生成。
"""
from __future__ import annotations

from pathlib import Path

import yaml

CONFIG_FILE = Path.home() / ".config" / "wechat-claw" / "config.yaml"

# 内置默认值（= 改造前各文件的硬编码常量，保持行为不变）
DEFAULTS: dict = {
    "push": {
        "host": "127.0.0.1",
        "port": 9898,
        "max_body_mb": 100,
        "retry_attempts": 3,
        "retry_interval": 3,
        "timeout": 15,
    },
    "session": {
        "ttl_seconds": 5 * 3600,          # 5h 会话窗
        "perm_timeout_seconds": 30,       # 权限确认超时（默认拒绝）
    },
    "scheduler": {
        "run_timeout_seconds": 300,       # 模块子进程超时保护
        "prune_days": 30,                 # 状态文件修剪保留天数
    },
    "log": {
        "rotate_mb": 1,
        "backup_count": 2,
    },
    "acp": {
        "command": "opencode",
        "port": 45678,
    },
    "file_send": {
        "default_dirs": [
            "~/文档", "~/下载", "~/桌面", "~/图片",
            "~/音乐", "~/视频", "~/公共", "~/wechat-claw/inbox",
        ],
        "reject_dirs": ["~/.config/wechat-claw", "~/.ssh", "~/.gnupg"],
        "reject_name_re": "token|secret|credential|private|anniversaries\\.json\\.enc",
        "reject_suffixes": [".key", ".pem", ".p12", ".pfx", ".p8"],
    },
    "crypto": {
        "key_file": "~/.config/wechat-claw/secret.key",
    },
}

_cached: dict | None = None


def _load() -> dict:
    """读取 config.yaml，深合并到默认值（部分配置也生效）。失败回退默认。"""
    global _cached
    if _cached is not None:
        return _cached
    cfg: dict = {}
    try:
        if CONFIG_FILE.is_file():
            data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                cfg = data
    except Exception:
        cfg = {}
    _cached = _merge(DEFAULTS, cfg)
    return _cached


def _merge(base: dict, override: dict) -> dict:
    """浅层逐键合并：override 的 dict 值递归合并，标量直接覆盖。"""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def get(key: str, default=None):
    """点号路径取值：get("push.port")；缺键回退 default 或内置默认。"""
    node: dict = _load()
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node


def reset_cache() -> None:
    """清缓存（测试用；运行期配置只读不重载）。"""
    global _cached
    _cached = None