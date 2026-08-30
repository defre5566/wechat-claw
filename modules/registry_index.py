"""模块注册索引：扫描 modules/*/module.json 动态汇总。

真源 = 各模块自己的 module.json；本模块只是生成物（每次调用实时扫描，
模块目录增删自动反映，基础设施不随模块增长而膨胀）。供 scheduler / push_server 读取。

短 TTL 缓存（默认 2s）避免 /push 鉴权每请求全量扫盘；register.set_enabled / uninstall
调用 invalidate() 主动清缓存，保证后台启停后下个请求即时生效（守住"实时扫描"语义）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

from bridge.config import MODULES_ROOT

MODULES_DIR = MODULES_ROOT

log = logging.getLogger("wechat-bridge")  # 与 bridge 同 logger（common/log.py 配置）

_CACHE_TTL = 2.0
_cache: dict = {"ts": 0.0, "index": {}}


def invalidate() -> None:
    """清缓存（register 启停/卸载后调用，使下个 build_index() 重新扫描）。"""
    _cache["ts"] = 0.0


def _token_hash(name: str) -> str | None:
    tok = MODULES_DIR / name / "token"
    if tok.is_file():
        try:
            return hashlib.sha256(tok.read_text().strip().encode()).hexdigest()
        except OSError:
            return None
    return None


def build_index() -> dict:
    """返回 {模块名: {name, purpose, spec, schedule, retry, token_hash, enabled}}。

    - 仅含 enabled=true 的模块（register.py 管理启停；缺失或 false = 关闭，不进 index）
    - args 内嵌于各 schedule 规则，无顶层 args（H6：every/window/cron 三态统一传规则 args）
    - H8：token 文件缺失的模块不进 index（异常状态，避免无限 401 补发循环）
    """
    now = time.monotonic()
    if now - _cache["ts"] < _CACHE_TTL:
        return _cache["index"]
    index: dict[str, dict] = {}
    if not MODULES_DIR.is_dir():
        _cache["ts"] = time.monotonic()
        _cache["index"] = index
        return index
    for mod_dir in sorted(MODULES_DIR.iterdir()):
        if not mod_dir.is_dir():
            continue
        mj = mod_dir / "module.json"
        if not mj.is_file():
            continue
        try:
            data = json.loads(mj.read_text(encoding="utf-8"))
        except Exception:
            continue
        name = data.get("name") or mod_dir.name
        # 部署状态（enabled）在数据区 settings.json（module.json 纯声明化后不再承载）
        enabled = False
        try:
            sf = MODULES_DIR / "modules_data" / name / "settings.json"
            if sf.is_file():
                sv = json.loads(sf.read_text(encoding="utf-8"))
                if isinstance(sv, dict):
                    enabled = bool(sv.get("enabled", False))
        except Exception:
            enabled = False
        if not enabled:
            continue  # 关闭的模块不调度、不认 token
        # 兼容门禁兜底（issue #4）：防绕过 register 直改 settings.json 硬启用；
        # 已启用模块在主程序跨基线更新后变不兼容 → 也在此静止（级别与 token 缺失同款）
        from bridge.compat import compat_ok
        ok_c, why_c = compat_ok(data)
        if not ok_c:
            log.error(f"[index] 模块 {name} 兼容性校验失败（未加入索引，不调度/不推送）：{why_c}")
            continue
        th = _token_hash(name)
        if th is None:
            log.error(f"[index] 模块 {name} token 文件缺失（未加入索引，无法调度/推送；可用 register.py --reissue-token {name} 补发）")
            continue  # H8：token 缺失不进 index
        index[name] = {
            "name": name,
            "purpose": data.get("purpose", ""),
            "spec": data.get("spec", "规范.md"),
            "schedule": data.get("schedule", []),
            "retry": data.get("retry"),
            "inbound": data.get("inbound"),  # B：入站订阅声明（intents/scope/priority）
            "token_hash": th,
            "enabled": True,
        }
    _cache["ts"] = time.monotonic()
    _cache["index"] = index
    return index


def load(name: str) -> dict | None:
    """按名取单个模块配置；不存在返回 None。"""
    return build_index().get(name)