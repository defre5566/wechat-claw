"""config：基础设施配置层（读 <项目根>/.config/config.yaml）。

- 运行参数（push/session/scheduler/log）= bridge 内置默认，**配置文件不可覆盖**
- 用户参数（acp/file_send/crypto）可经 config.yaml 覆盖（深合并生效）
- 优先级：config.yaml（用户段）> DEFAULTS_USER > DEFAULTS_RUNTIME（恒内置）
- 键访问用点号路径：get("push.port") → config["push"]["port"]
- 路径类配置（crypto.key_file / file_send 目录）支持相对项目根写法，经 resolve_path 解析
- 归属：bridge 基础设施层（bridge 必须能不依赖模块运行）；modules/common 经
  `from bridge.config import get` 读取——模块依赖基础设施为设计方向

新用户/分发场景：由初始化向导生成（DEFAULTS_USER 序列化），或手工参考 config.yaml.example。
"""
from __future__ import annotations

from pathlib import Path

import yaml

# ---- 部署根 / 资源根（打包形态适配）----
# PyInstaller onefile 打包后：__file__ 在临时解包目录（_MEIPASS），
# 用户数据（.config/ 等）必须落在可执行文件所在目录，否则重启即丢；
# 只读资源（static/templates）从解包目录读取。
import sys as _sys

_FROZEN = getattr(_sys, "frozen", False)
PROJECT_ROOT = Path(__file__).resolve().parent.parent          # 源码形态项目根
DEPLOY_ROOT = (Path(_sys.executable).resolve().parent if _FROZEN else PROJECT_ROOT)  # 用户数据根
RESOURCE_ROOT = (Path(getattr(_sys, "_MEIPASS", PROJECT_ROOT)) if _FROZEN else PROJECT_ROOT)  # 资源根
CONFIG_FILE = DEPLOY_ROOT / ".config" / "config.yaml"

# ---- 运行参数（bridge 内置，不可被配置文件覆盖）----
DEFAULTS_RUNTIME: dict = {
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
}

# ---- 用户参数（config.yaml 合并基准，GUI 可配置）----
DEFAULTS_USER: dict = {
    "acp": {
        "command": "opencode",
        "port": 45678,
    },
    "file_send": {
        "default_dirs": [
            "~/文档", "~/下载", "~/桌面", "~/图片",
            "~/音乐", "~/视频", "~/公共", "inbox",   # inbox = <项目根>/inbox
        ],
        "reject_dirs": [".config", "~/.ssh", "~/.gnupg"],  # .config = <项目根>/.config
        "reject_name_re": "token|secret|credential|private|anniversaries\\.json\\.enc",
        "reject_suffixes": [".key", ".pem", ".p12", ".pfx", ".p8"],
    },
    "crypto": {
        "key_file": ".config/crypto.key",   # 相对项目根
    },
}

# 完整默认表（get() 的合并基准 = 运行参数 + 用户默认）
DEFAULTS: dict = {**DEFAULTS_RUNTIME, **DEFAULTS_USER}

_cached: dict | None = None


def resolve_path(p: str | Path) -> Path:
    """解析配置里的路径：绝对路径/含 ~ 直接展开；相对路径基于项目根拼接。"""
    s = str(p)
    if s.startswith("~"):
        return Path(s).expanduser().resolve()
    path = Path(s)
    if path.is_absolute():
        return path.resolve()
    return (DEPLOY_ROOT / path).resolve()


def _load() -> dict:
    """读 config.yaml 用户段，深合并到 DEFAULTS_USER；运行参数恒为内置。"""
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
    # 只合并用户段键（文件里写运行参数段 = 无效，防止覆盖内置）
    user_cfg = {k: v for k, v in cfg.items() if k in DEFAULTS_USER}
    user = _merge(DEFAULTS_USER, user_cfg)
    _cached = {**DEFAULTS_RUNTIME, **user}
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
