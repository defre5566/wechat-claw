"""批 1 回归：config 坏文件回退+日志（F2.1）、配置面同根（F4.5）、任务监护+推送分发（F3.2）。"""
from __future__ import annotations

import asyncio
import logging

import pytest


# ---------- F2.1 config.yaml 解析失败 → 回退默认 + 明确日志 ----------

def test_config_bad_yaml_logs_and_falls_back(tmp_path, monkeypatch, caplog):
    import bridge.config as cfg
    bad = tmp_path / "config.yaml"
    bad.write_text("acp:\n  command: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_FILE", bad)
    monkeypatch.setattr(cfg, "_cached", None)
    with caplog.at_level(logging.WARNING, logger="wechat-config"):
        val = cfg.get("acp.command")
    assert val == "opencode"  # 回退默认
    assert any("config.yaml 解析失败" in r.message for r in caplog.records)


def test_config_missing_file_no_log(tmp_path, monkeypatch, caplog):
    import bridge.config as cfg
    monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "no.yaml")
    monkeypatch.setattr(cfg, "_cached", None)
    with caplog.at_level(logging.WARNING, logger="wechat-config"):
        val = cfg.get("acp.command")
    assert val == "opencode"
    assert not any("解析失败" in r.message for r in caplog.records)


# ---------- F4.5 全配置面同根（<数据根>/.config） ----------

def test_all_config_faces_same_root():
    from bridge.config import CONFIG_FILE, DATA_ROOT, WORK_ROOT
    from bridge.permissions import CONFIG_DIR as PERM_DIR
    from modules.common._userdata import CONFIG_DIR as UDATA_DIR
    assert WORK_ROOT == DATA_ROOT
    assert UDATA_DIR == CONFIG_FILE.parent
    assert PERM_DIR == CONFIG_FILE.parent


# ---------- F3.2 任务监护：异常死亡有日志；推送队列分发走对路 ----------

def test_spawn_task_logs_exception(caplog):
    from bridge.main import spawn_task

    async def boom():
        raise RuntimeError("spawn-test-boom")

    async def runner():
        t = spawn_task(boom(), "test_task")
        await asyncio.sleep(0.05)
        return t

    with caplog.at_level(logging.ERROR, logger="wechat-bridge"):
        asyncio.run(runner())
    assert any("test_task 异常退出" in r.message and "spawn-test-boom" in r.message
               for r in caplog.records)


def test_push_worker_dispatches():
    from bridge import main as m

    core = m.BridgeCore.__new__(m.BridgeCore)
    core.push_queue = asyncio.Queue()
    core.send_lock = asyncio.Lock()
    core.retry_queue = []
    calls = {"file": 0, "direct": 0, "agent": 0}

    async def fake_send_file(_p):
        calls["file"] += 1

    async def fake_agent(_t):
        calls["agent"] += 1

    async def fake_send_with_retry(_c, _t):
        calls["direct"] += 1

    core._send_file = fake_send_file
    core._agent_process = fake_agent
    core.send_with_retry = fake_send_with_retry

    import bridge.state as st
    st.targets_for_text = lambda text: ("wx-conv", text)

    for item in ({"type": "file", "path": "x"},
                 {"type": "text", "text": "hi"},
                 {"type": "alert", "text": "go"}):
        core.push_queue.put_nowait(item)

    async def runner():
        task = asyncio.create_task(core.push_worker())
        await core.push_queue.join()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(runner())
    assert calls == {"file": 1, "direct": 1, "agent": 1}

    async def runner_concurrent():
        # agent 推送不与 direct 串行锁互斥（锁仅 direct 使用：F7.1 语义）
        return core.send_lock.locked()

    assert asyncio.run(runner_concurrent()) is False