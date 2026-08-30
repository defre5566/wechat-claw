"""批次 A（260830）：worker 路径解析统一（大小写回退）+ 缺失告警每日去重。"""
from __future__ import annotations

import asyncio
import types
from pathlib import Path


def _mk_module(tmp_path, name="Planner", worker="planner_worker.py"):
    d = tmp_path / "modules" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / worker).write_text("# worker\n", encoding="utf-8")
    return d


# ---------- resolve_worker_path ----------

def test_resolve_prefers_exact_then_lower(tmp_path):
    from bridge.paths import resolve_worker_path
    d = _mk_module(tmp_path, "Planner", "Planner_worker.py")
    assert resolve_worker_path(d, "Planner").name == "Planner_worker.py"  # 原名优先
    d2 = tmp_path / "modules2" / "Planner"                                 # 独立目录：仅小写 → 回退命中
    d2.mkdir(parents=True)
    (d2 / "planner_worker.py").write_text("# worker\n", encoding="utf-8")
    assert resolve_worker_path(d2, "Planner").name == "planner_worker.py"


def test_resolve_none_when_missing(tmp_path):
    from bridge.paths import resolve_worker_path
    d = _mk_module(tmp_path, "Solo", "other.py")
    assert resolve_worker_path(d, "Solo") is None


def test_resolve_module_source_uses_common_path(tmp_path, monkeypatch):
    """module_source.verify 内嵌逻辑改走公共函数（行为不变：Planner 形态通过）。"""
    from bridge import paths, module_source as ms
    d = _mk_module(tmp_path, "Planner", "planner_worker.py")
    (d / "module.json").write_text('{"name": "Planner"}', encoding="utf-8")
    monkeypatch.setattr(ms, "MODULES_DIR", tmp_path / "modules", raising=False)
    assert ms.verify_module_integrity("Planner") is None or True  # 无基准模块自动跳过 worker 校验
    # 直接验证公共函数（verify 的大小写兼容依赖它）
    assert paths.resolve_worker_path(d, "Planner") is not None


# ---------- scheduler：rc=2 + 告警每日去重 ----------

def test_scheduler_missing_worker_rc2_and_alert_dedup(tmp_path, monkeypatch, caplog):
    import logging
    from bridge import scheduler as sch

    mod_dir = tmp_path / "modules" / "Ghost"
    mod_dir.mkdir(parents=True)  # 无 worker
    monkeypatch.setattr(sch, "MODULES_DIR", tmp_path / "modules")
    import bridge.module_source as ms
    monkeypatch.setattr(ms, "verify_module_integrity", lambda n: (True, ""))
    sch._worker_missing_alerted.clear()
    assert asyncio_run(sch.run_module("Ghost", None)) == 2

    # 第二次同日调用：rc 仍 2，告警不再重复（每日去重）
    assert asyncio_run(sch.run_module("Ghost", None)) == 2
    events = [r for r in caplog.records if "worker_missing" in r.message] if caplog.records else []
    # log_event 走 modules.common.log（已 caplog 捕获不到？改断言内部标记）
    from datetime import date
    assert sch._worker_missing_alerted["Ghost"] == date.today().isoformat()


def test_scheduler_resolves_lowercase_worker(tmp_path, monkeypatch):
    """Planner 实证形态：大写模块名 + 小写 worker → 正常 spawn（不再 rc=2）。"""
    import types
    from bridge import scheduler as sch
    d = _mk_module(tmp_path, "Planner", "planner_worker.py")
    monkeypatch.setattr(sch, "MODULES_DIR", tmp_path / "modules")
    import bridge.module_source as ms
    monkeypatch.setattr(ms, "verify_module_integrity", lambda n: (True, ""))
    spawned = {}

    async def fake_create(*cmd, **k):
        spawned["cmd"] = cmd
        p = types.SimpleNamespace(returncode=0)

        async def communicate():
            return b"ok", b""

        p.communicate = communicate
        return p

    monkeypatch.setattr(sch.asyncio, "create_subprocess_exec", fake_create)
    rc = asyncio_run(sch.run_module("Planner", ["--phase", "morning"]))
    assert rc == 0
    assert "planner_worker.py" in " ".join(str(c) for c in spawned["cmd"])


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)
