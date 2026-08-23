"""config：基础设施配置层（读 <数据根>/.config/config.yaml）。

- 运行参数（push/session/scheduler/log）= bridge 内置默认，**配置文件不可覆盖**
- 用户参数（acp/file_send/crypto）可经 config.yaml 覆盖（深合并生效）
- 优先级：config.yaml（用户段）> DEFAULTS_USER > DEFAULTS_RUNTIME（恒内置）
- 键访问用点号路径：get("push.port") → config["push"]["port"]
- 路径类配置（crypto.key_file / file_send 目录）支持相对数据根写法，经 resolve_path 解析
- 归属：bridge 基础设施层（bridge 必须能不依赖模块运行）；modules/common 经
  `from bridge.config import get` 读取——模块依赖基础设施为设计方向

新用户/分发场景：由初始化向导生成（DEFAULTS_USER 序列化），或手工参考 config.yaml.example。
"""
from __future__ import annotations

import logging
import os as _os
import shutil as _shutil
import sys as _sys
from pathlib import Path

import yaml

log = logging.getLogger("wechat-config")

# ---- 部署根 / 数据根 / 资源根（打包形态适配）----
# PyInstaller onefile 打包后：__file__ 在临时解包目录（_MEIPASS），
# 程序本体（exe）所在目录与解包目录都不可作为持久数据位置；
# 用户数据按平台规范落用户目录（exe 是部署包，不是数据包）。
_FROZEN = getattr(_sys, "frozen", False)
PROJECT_ROOT = Path(__file__).resolve().parent.parent          # 源码形态项目根
VERSION = "0.1.2"  # 发版版本号（exe 形态用此值；源码形态优先 git describe --tags）
DEPLOY_ROOT = (Path(_sys.executable).resolve().parent if _FROZEN else PROJECT_ROOT)  # 程序根（exe/项目）
RESOURCE_ROOT = (Path(getattr(_sys, "_MEIPASS", PROJECT_ROOT)) if _FROZEN else PROJECT_ROOT)  # 资源根


def _default_data_root() -> Path:
    """平台规范数据根：
    - Windows：%LOCALAPPDATA%\\wechat-claw（用户目录新建文件夹）
    - macOS：~/Library/Application Support/wechat-claw（平台惯例）
    - Linux/其他：~/.local/share/wechat-claw（XDG 单目录）
    """
    if _os.name == "nt":
        base = _os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "wechat-claw"
    if _sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "wechat-claw"
    return Path.home() / ".local" / "share" / "wechat-claw"


# 用户数据根：源码形态保持项目根（开发自洽、无需迁移）；打包形态落平台规范用户目录
# （exe 是部署包不是数据包，exe 目录/解包目录都不可作为持久数据位置）。
# WC_DATA_ROOT 环境变量可覆盖（测试隔离 / 自定义部署）。
DATA_ROOT = Path(_os.environ.get("WC_DATA_ROOT")
                  or (_default_data_root() if _FROZEN else PROJECT_ROOT))

# 模块源签名公钥（Ed25519，作者私钥签名 manifest.sig；空 = 未启用签名校验，
# 发布前保持；发布时经 scripts/gen_signing_key.py 生成并填入）——防源仓库账号被盗
# 时恶意模块自动进入部署机（账号被盗者无私钥签不出合法包）
SIGNING_PUBLIC_KEY: str = "f2c2236c4a06c9140b0de3a9b9017cd0b4326506cd9aaee19ee03a5d1d8c2df3"

# 运行时根 = 数据根：模块系统（registry/register/module_source/scheduler/jobs/permissions）
# 与 bridge 工作区（logs/inbox/_archive/agent-SDK/状态文件）统一以 WORK_ROOT 定位——
# 打包形态下 __file__ 指向只读临时解包目录，落盘必须走数据根，否则重启即丢。
WORK_ROOT = DATA_ROOT
MODULES_ROOT = WORK_ROOT / "modules"
CONFIG_FILE = WORK_ROOT / ".config" / "config.yaml"

# 微信 SDK 存储（accounts.json 等）收敛到数据根：SDK 已打补丁支持该环境变量重定向
# （vendor 快照 + site-packages 双形态都经 apply_patches 打上；未打时回落 ~/.wechat-agent-sdk）
_os.environ.setdefault("WECHAT_AGENT_SDK_STATE_DIR", str(WORK_ROOT / "agent-SDK"))
# SDK 存储加密密钥（modules/common/crypto.py 同名 32B 主密钥；SDK 已打补丁支持该 env）
_os.environ.setdefault("WECHAT_AGENT_SDK_KEY_FILE", str(WORK_ROOT / ".config" / "crypto.key"))

# ---- 运行参数（bridge 内置，不可被配置文件覆盖）----
DEFAULTS_RUNTIME: dict = {
    "push": {
        "host": "127.0.0.1",
        "port": 9898,
        "max_body_mb": 100,
        "retry_attempts": 3,
        "retry_interval": 3,
        "retry_worker_interval": 300,   # bridge→微信 发送失败兜底重试间隔（与模块推送 retry_* 语义区分）
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
            "~/音乐", "~/视频", "~/公共", "inbox",   # inbox = <数据根>/inbox
        ],
        "reject_dirs": [".config", "agent-SDK", "~/.wechat-agent-sdk",
                        "~/.ssh", "~/.gnupg"],  # .config = <数据根>/.config；agent-SDK = <数据根> 下微信 SDK 凭证目录
        "reject_name_re": "token|secret|credential|private|accounts\\.json|anniversaries\\.json\\.enc",
        "reject_suffixes": [".key", ".pem", ".p12", ".pfx", ".p8"],
    },
    "crypto": {
        "key_file": ".config/crypto.key",   # 相对数据根
    },
}

# 完整默认表（get() 的合并基准 = 运行参数 + 用户默认）
DEFAULTS: dict = {**DEFAULTS_RUNTIME, **DEFAULTS_USER}

_cached: dict | None = None


def resolve_path(p: str | Path) -> Path:
    """解析配置里的路径：绝对路径/含 ~ 直接展开；相对路径基于数据根拼接。"""
    s = str(p)
    if s.startswith("~"):
        return Path(s).expanduser().resolve()
    path = Path(s)
    if path.is_absolute():
        return path.resolve()
    return (DATA_ROOT / path).resolve()


def _load() -> dict:
    """读 config.yaml 用户段，深合并到 DEFAULTS_USER；运行参数恒为内置。"""
    global _cached
    if _cached is not None:
        return _cached
    cfg: dict = {}
    fail_msg = None
    try:
        if CONFIG_FILE.is_file():
            data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                cfg = data
            else:
                fail_msg = f"内容不是映射（{type(data).__name__}），请检查格式"
    except Exception as e:
        fail_msg = str(e)
    if fail_msg:
        log.warning(
            "config.yaml 解析失败，用户配置（acp.command/file_send/crypto）回退默认值: %s",
            fail_msg,
        )
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


def opencode_converged() -> Path | None:
    """opencode 收敛判定：向导安装（标记存在）→ 配置收敛根，否则 None。

    收敛根 = DATA_ROOT/opencode/config（会话/任务子进程经 XDG_CONFIG_HOME 注入，
    opencode 配置、scheduler 数据与主链路同域）。
    """
    marker = DATA_ROOT / ".config" / "opencode-installed.json"
    if marker.is_file():
        return DATA_ROOT / "opencode" / "config"
    return None


def xdg_env() -> dict:
    """收敛形态的 XDG 重定向环境（空 dict = 不收敛，保持 opencode 默认位置）。"""
    root = opencode_converged()
    if root is None:
        return {}
    base = root.parent  # DATA_ROOT/opencode
    return {
        "XDG_DATA_HOME": str(base / "data"),
        "XDG_CONFIG_HOME": str(root),
        "XDG_CACHE_HOME": str(base / "cache"),
    }


def resolve_opencode() -> str | None:
    """opencode 可执行文件解析（job 登记/单元生成与主链路 acp.command 同源寻址）。

    优先级：acp.command 显式配置（绝对路径校验，无效即失败）→ PATH → 官方默认
    安装目录 ~/.opencode/bin。找不到返回 None（调用方明确报错并回滚）。
    """
    cmd = str(get("acp.command") or "").strip()
    if cmd and ("/" in cmd or "\\" in cmd):
        p = Path(cmd).expanduser()
        if p.is_file() and _os.access(p, _os.X_OK):
            return str(p)
        return None
    found = _shutil.which(cmd or "opencode")
    if found:
        return found
    fallback = Path.home() / ".opencode" / "bin" / "opencode"
    if fallback.is_file() and _os.access(fallback, _os.X_OK):
        return str(fallback)
    return None
