"""批 4（260827）：indexer v0——量级判档 / 装配单 / 观测落盘 / 路由接入不改行为。"""
from __future__ import annotations

import json
import types

from bridge.indexer import OBSERVE_FILE, build_assembly, judge_tier, observe


# ---------- judge_tier（A 档启发式） ----------

def test_judge_tier_boundaries():
    assert judge_tier("") == 0
    assert judge_tier("测试") == 0                       # 短句
    assert judge_tier("今天下午提醒我取快递然后顺便买水果") == 1   # 单句较长
    assert judge_tier("帮我记一下周五要取快递。另外周日有个会。") == 2  # 两句
    long3 = "第一句。第二句。第三句。"
    assert judge_tier(long3) == 2                        # 三句
    assert judge_tier("一。二。三。四。五。六。七。") == 4   # 七句


# ---------- build_assembly / observe ----------

def test_build_assembly_shape():
    rec = build_assembly("提醒我取快递", modules_hit=["todo"], routed=True)
    assert rec["tier"] in range(5)
    assert rec["routed"] is True and rec["modules"] == ["todo"]
    assert rec["mode"] == "main"  # v0 固定主链路
    assert "text_preview" in rec and "ts" in rec


def test_observe_writes_jsonl_and_never_raises(tmp_path, monkeypatch, caplog):
    import bridge.indexer as idx
    monkeypatch.setattr(idx, "OBSERVE_FILE", tmp_path / "logs" / "indexer.jsonl")
    idx.observe("测试文本", modules_hit=["todo"], routed=False)
    lines = (tmp_path / "logs" / "indexer.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["modules"] == ["todo"] and rec["routed"] is False

    # 落盘失败（目录变只读文件）不抛异常，仅告警
    bad = tmp_path / "blocker"
    bad.write_text("not a dir", encoding="utf-8")
    monkeypatch.setattr(idx, "OBSERVE_FILE", bad / "logs" / "indexer.jsonl")
    idx.observe("再次观测")  # 不应 raise
    assert any("观测落盘失败" in r.message for r in caplog.records)


# ---------- _route_inbound 接入观测（不改行为） ----------

def test_route_inbound_observes_miss_and_hit(tmp_path, monkeypatch):
    import bridge.indexer as idx
    import bridge.main as m

    monkeypatch.setattr(idx, "OBSERVE_FILE", tmp_path / "indexer.jsonl")

    core = m.BridgeCore.__new__(m.BridgeCore)

    # 无候选：未接管
    import modules.registry_index as ri
    monkeypatch.setattr(ri, "build_index", lambda: {})
    monkeypatch.setattr(m, "build_index", lambda: {}, raising=False)
    hit = asyncio_run(core._route_inbound("conv", "普通消息"))
    assert hit is False

    # 有候选且 rc=0：接管
    fake_index = {"todo": {"inbound": {"intents": ["提醒"], "priority": 1}}}
    monkeypatch.setattr(ri, "build_index", lambda: fake_index)
    monkeypatch.setattr(m, "build_index", lambda: fake_index, raising=False)
    core._run_inbound = fake_run_inbound(0, "已记录")
    core.send_text = fake_send_text()
    hit = asyncio_run(core._route_inbound("conv", "提醒我取快递"))
    assert hit is True

    records = [json.loads(ln) for ln in
               (tmp_path / "indexer.jsonl").read_text(encoding="utf-8").strip().splitlines()]
    assert records[0]["routed"] is False and records[0]["modules"] == []
    assert records[1]["routed"] is True and records[1]["modules"] == ["todo"]


def test_route_inbound_rc3_falls_through(tmp_path, monkeypatch):
    import bridge.indexer as idx
    import bridge.main as m
    import modules.registry_index as ri

    monkeypatch.setattr(idx, "OBSERVE_FILE", tmp_path / "indexer2.jsonl")
    core = m.BridgeCore.__new__(m.BridgeCore)
    fake_index = {"todo": {"inbound": {"intents": ["提醒"], "priority": 1}}}
    monkeypatch.setattr(ri, "build_index", lambda: fake_index)
    monkeypatch.setattr(m, "build_index", lambda: fake_index, raising=False)
    core._run_inbound = fake_run_inbound(3, "")
    hit = asyncio_run(core._route_inbound("conv", "提醒我取快递"))
    assert hit is False  # rc=3 转 agent，消息继续走主链路
    rec = json.loads((tmp_path / "indexer2.jsonl").read_text(encoding="utf-8").strip())
    assert rec["routed"] is False and rec["modules"] == ["todo"]  # 命中观测在案


# ---------- helpers ----------

def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


def fake_run_inbound(rc, out):
    async def _f(_name, _conv, _text):
        return rc, out
    return _f


def fake_send_text():
    async def _f(_c, _t):
        pass
    return _f


# ---------- 批 5：会话画像与冷启动装配 ----------

def test_observe_records_conv(tmp_path, monkeypatch):
    import bridge.indexer as idx
    monkeypatch.setattr(idx, "OBSERVE_FILE", tmp_path / "indexer.jsonl")
    idx.observe("测试", conversation_id="wx-9")
    rec = json.loads((tmp_path / "indexer.jsonl").read_text(encoding="utf-8").strip())
    assert rec["conv"] == "wx-9"


def test_profile_tier_from_jsonl(tmp_path, monkeypatch):
    import bridge.indexer as idx
    f = tmp_path / "indexer.jsonl"
    recs = [{"conv": "wx-1", "tier": t} for t in (4, 4, 4, 3, 3, 3)]  # 均值 3.5 → 4
    recs += [{"conv": "wx-2", "tier": 0}] * 5  # 别的会话，不计入
    f.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    monkeypatch.setattr(idx, "OBSERVE_FILE", f)
    assert idx._profile_tier("wx-1") == 4
    assert idx._profile_tier("wx-2") == 0
    assert idx._profile_tier("wx-none") == 2  # 无记录默认档
    assert idx._profile_tier(None) == 2


def test_refresh_current_tier_writes_profile_tier(tmp_path, monkeypatch):
    import bridge.indexer as idx
    import web.agent_gen as ag
    ins = tmp_path / "instructions"
    ins.mkdir()
    (ins / "tier3.md").write_text("T3 内容", encoding="utf-8")
    (ins / "tier2.md").write_text("T2 内容", encoding="utf-8")
    monkeypatch.setattr(ag, "INSTRUCTIONS_DIR", ins)
    f = tmp_path / "indexer.jsonl"
    f.write_text(json.dumps({"conv": "wx-1", "tier": 3}) + "\n", encoding="utf-8")
    monkeypatch.setattr(idx, "OBSERVE_FILE", f)

    assert idx.refresh_current_tier("wx-1") == 3
    assert (ins / "tier-current.md").read_text(encoding="utf-8") == "T3 内容"


def test_refresh_current_tier_keeps_old_on_failure(tmp_path, monkeypatch, caplog):
    import bridge.indexer as idx
    import web.agent_gen as ag
    ins = tmp_path / "instructions"
    ins.mkdir()
    (ins / "tier-current.md").write_text("旧装配", encoding="utf-8")  # 只有 current，无 tierN 源
    monkeypatch.setattr(ag, "INSTRUCTIONS_DIR", ins)
    monkeypatch.setattr(idx, "OBSERVE_FILE", tmp_path / "missing.jsonl")  # 无画像→默认档2→源缺失

    assert idx.refresh_current_tier("wx-1") == 2
    assert (ins / "tier-current.md").read_text(encoding="utf-8") == "旧装配"
    assert any("装配失败" in r.message for r in caplog.records)


def test_session_expiry_triggers_assembly(tmp_path, monkeypatch):
    from bridge.session import SessionManager
    import bridge.indexer as idx
    import bridge.state as st

    called = []
    monkeypatch.setattr(idx, "refresh_current_tier", lambda conv: called.append(conv))
    # 归档落盘路径与状态文件隔离到 tmp，防污染真实 WORK_ROOT
    monkeypatch.setattr(st, "ARCHIVE_DIR", tmp_path / "_archive")
    monkeypatch.setattr(st, "SESSION_STATE_FILE", tmp_path / ".session_state.json")
    monkeypatch.setattr("bridge.session.ARCHIVE_DIR", tmp_path / "_archive")
    monkeypatch.setattr("bridge.session.SESSION_STATE_FILE", tmp_path / ".session_state.json")

    sm = SessionManager()
    sm._last_active = {"wx-1": 0.0}  # 远古活跃点
    result = sm.check("wx-1")
    assert result == "expired"
    assert called == ["wx-1"]
