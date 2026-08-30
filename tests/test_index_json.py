"""二期 Ⅱ-2/Ⅱ-3（260827）：index 位置表机制——校验 / place_index 四分支 / register 插拔。"""
from __future__ import annotations

import json
import types
from pathlib import Path


# ---------- validate_index ----------

def test_validate_index_ok():
    from web.agent_gen import validate_index
    data = {"module": "todo", "entries": [
        {"kw": ["提醒", "待办"], "file": "modules/todo/agents.md", "title": "todo"},
    ]}
    assert validate_index(data)


def test_validate_index_bad_cases():
    from web.agent_gen import validate_index
    assert not validate_index("no")                       # 非对象
    assert not validate_index({})                         # 缺 entries
    assert not validate_index({"entries": "x"})           # entries 非列表
    assert not validate_index({"entries": [{"kw": [], "file": "a"}]})          # kw 空
    assert not validate_index({"entries": [{"kw": ["a"], "file": ""}]})        # file 空
    assert not validate_index({"entries": [{"kw": ["a"]}]})                    # 缺 file
    assert not validate_index({"entries": [{"kw": ["", "a"], "file": "f"}]})   # 空白词


# ---------- place_index 四分支 ----------

def _mk_module(tmp_path, name, seed=None):
    """伪造模块目录（可选种子 index.json）。"""
    mdir = tmp_path / "modules" / name
    mdir.mkdir(parents=True, exist_ok=True)
    if seed is not None:
        (mdir / "index.json").write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")


def test_place_index_restores_off(tmp_path, monkeypatch):
    import web.agent_gen as ag
    monkeypatch.setattr(ag, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(ag, "DATA_ROOT", tmp_path)
    (tmp_path / "index").mkdir(parents=True)
    off = tmp_path / "index" / "todo.json.off"
    off.write_text('{"module":"todo","entries":[]}', encoding="utf-8")

    out = ag.place_index("todo")
    assert out == tmp_path / "index" / "todo.json"
    assert out.is_file() and not off.exists()  # .off 恢复为现役


def test_place_index_keeps_existing(tmp_path, monkeypatch):
    import web.agent_gen as ag
    monkeypatch.setattr(ag, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(ag, "DATA_ROOT", tmp_path)
    _mk_module(tmp_path, "todo", seed={"module": "todo", "entries": [{"kw": ["x"], "file": "y"}]})
    (tmp_path / "index").mkdir(parents=True)
    live = tmp_path / "index" / "todo.json"
    live.write_text('{"module":"todo","entries":[]}', encoding="utf-8")  # worker 已改写

    ag.place_index("todo")
    assert json.loads(live.read_text(encoding="utf-8"))["entries"] == []  # 重复启用不覆盖


def test_place_index_copies_valid_seed(tmp_path, monkeypatch):
    import web.agent_gen as ag
    monkeypatch.setattr(ag, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(ag, "DATA_ROOT", tmp_path)
    seed = {"module": "todo", "entries": [{"kw": ["提醒"], "file": "modules/todo/agents.md", "title": "t"}]}
    _mk_module(tmp_path, "todo", seed=seed)

    out = ag.place_index("todo")
    assert json.loads(out.read_text(encoding="utf-8")) == seed


def test_place_index_skeleton_on_no_seed(tmp_path, monkeypatch):
    import web.agent_gen as ag
    monkeypatch.setattr(ag, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(ag, "DATA_ROOT", tmp_path)
    _mk_module(tmp_path, "solo")  # 无种子

    out = ag.place_index("solo")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["module"] == "solo"
    assert data["entries"][0]["kw"] == ["solo"]
    assert data["entries"][0]["file"] == "modules/solo/agents.md"


def test_place_index_bad_seed_falls_to_skeleton(tmp_path, monkeypatch):
    import web.agent_gen as ag
    monkeypatch.setattr(ag, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(ag, "DATA_ROOT", tmp_path)
    _mk_module(tmp_path, "broken", seed={"module": "broken", "entries": [{"kw": [], "file": ""}]})

    out = ag.place_index("broken")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["entries"][0]["file"] == "modules/broken/agents.md"  # 骨架兜底


# ---------- Ⅱ-3 register 插拔钩子 ----------

def test_register_enable_disable_uninstall_index_lifecycle(tmp_path, monkeypatch):
    import modules.register as reg
    import web.agent_gen as ag

    monkeypatch.setattr(ag, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(ag, "DATA_ROOT", tmp_path)
    _mk_module(tmp_path, "todo")
    idx_dir = tmp_path / "index"
    live = idx_dir / "todo.json"

    reg.sync_index_on_enable("todo")
    assert live.is_file()
    reg.sync_index_on_disable("todo")
    assert not live.is_file() and (idx_dir / "todo.json.off").is_file()
    reg.sync_index_on_enable("todo")                       # 再启用 = 恢复
    assert live.is_file() and not (idx_dir / "todo.json.off").exists()
    reg.sync_index_on_uninstall("todo")
    assert not live.is_file() and not (idx_dir / "todo.json.off").exists()  # 彻底移除
    reg.sync_index_on_uninstall("todo")                    # 幂等：重复卸载不抛


# ---------- Ⅱ-4 硬索引：match_index / build_material_block / handle 接线 ----------

def _seed_index_dir(tmp_path, monkeypatch, files: dict[str, str], index: dict):
    import web.agent_gen as ag
    import bridge.indexer as idx
    idir = tmp_path / "index"
    idir.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    for name, data in index.items():
        (idir / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ag, "INDEX_DIR", idir)
    monkeypatch.setattr(idx, "WORK_ROOT", tmp_path)
    return idir


def test_match_index_hit_dedup_and_off(tmp_path, monkeypatch):
    from bridge.indexer import match_index
    _seed_index_dir(
        tmp_path, monkeypatch,
        files={"modules/todo/agents.md": "指引正文"},
        index={
            "todo.json": {"module": "todo", "entries": [
                {"kw": ["提醒", "待办"], "file": "modules/todo/agents.md", "title": "todo"},
                {"kw": ["提醒"], "file": "modules/todo/agents.md", "title": "dup"},  # 同文件去重
            ]},
            "todo.json.off": {"module": "todo", "entries": [{"kw": ["x"], "file": "y"}]},  # 失效不扫
        },
    )
    hits = match_index("提醒我取快递")
    assert len(hits) == 1 and hits[0]["module"] == "todo"
    assert match_index("无关消息") == []


def test_match_index_skips_dead_link_and_bad_json(tmp_path, monkeypatch, caplog):
    from bridge.indexer import match_index
    _seed_index_dir(
        tmp_path, monkeypatch,
        files={},
        index={
            "dead.json": {"module": "d", "entries": [{"kw": ["k"], "file": "modules/nope.md"}]},
        },
    )
    (tmp_path / "index" / "bad.json").write_text("{broken", encoding="utf-8")
    assert match_index("k 相关") == []
    assert any("死链" in r.message for r in caplog.records)


def test_build_material_block_format_and_limits(tmp_path, monkeypatch):
    from bridge.indexer import build_material_block
    _seed_index_dir(
        tmp_path, monkeypatch,
        files={"modules/todo/agents.md": "任务文件固定为 modules/modules_data/todo/tasks/YYYY-MM.json"},
        index={"todo.json": {"module": "todo", "entries": [
            {"kw": ["提醒"], "file": "modules/todo/agents.md", "title": "todo 指引"}]}},
    )
    block = build_material_block("提醒我取快递")
    assert "[参考材料·todo 指引]" in block
    assert "任务文件固定为" in block
    assert build_material_block("无关") == ""

    # 上限截断：材料超 2KB 被截
    _seed_index_dir(
        tmp_path, monkeypatch,
        files={"modules/big/agents.md": "x" * 5000},
        index={"big.json": {"module": "big", "entries": [
            {"kw": ["大"], "file": "modules/big/agents.md", "title": "big"}]}},
    )
    from bridge.indexer import _INDEX_MATERIAL_MAX
    block = build_material_block("大工程")
    assert len(block) < _INDEX_MATERIAL_MAX + 200  # 截断生效


def test_handle_injects_material_into_prompt(tmp_path, monkeypatch):
    import types
    import bridge.indexer as idx
    import bridge.main as m
    import bridge.state as st
    import modules.registry_index as ri

    _seed_index_dir(
        tmp_path, monkeypatch,
        files={"modules/todo/agents.md": "操作卡：往 tasks/月.json 追加"},
        index={"todo.json": {"module": "todo", "entries": [
            {"kw": ["提醒"], "file": "modules/todo/agents.md", "title": "todo"}]}},
    )
    monkeypatch.setattr(ri, "build_index", lambda: {})  # 无 inbound 候选

    core = m.BridgeCore.__new__(m.BridgeCore)
    core.sessions = types.SimpleNamespace(check=lambda _c: "continue")
    core._last_token = {}
    captured = {}

    async def fake_typing(*a, **k):
        pass

    async def fake_chat(req):
        captured["text"] = req.text
        return types.SimpleNamespace(text="好的")

    async def fake_send(_c, _t):
        pass

    core._transport = types.SimpleNamespace(send_typing=fake_typing)
    core._agent = types.SimpleNamespace(chat=fake_chat)
    core.send_text = fake_send

    import asyncio
    asyncio.run(core.handle("wx-1", "提醒我取快递"))
    assert "提醒我取快递" in captured["text"]
    assert "[参考材料·todo]" in captured["text"]  # 材料已随 prompt 注入


# ---------- 批次 Ⅲ：fuzzy_match / 档位阶梯 ----------

def _fuzzy_env(tmp_path, monkeypatch, files=None, index=None, stdout="NONE", cfg=None):
    import bridge.config as cfgmod
    import bridge.indexer as idx
    import web.agent_gen as ag
    _seed_index_dir(tmp_path, monkeypatch, files or {}, index or {})
    monkeypatch.setattr(cfgmod, "resolve_opencode", lambda: "/usr/bin/opencode")
    monkeypatch.setattr(cfgmod, "xdg_env", lambda: {})
    monkeypatch.setattr(cfgmod, "get",
                        lambda k, d=None: cfg if cfg is not None else "test/model")
    monkeypatch.setattr(idx.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout=stdout, stderr=""))
    return idx


def test_fuzzy_skips_short_and_disabled(tmp_path, monkeypatch):
    idx = _fuzzy_env(tmp_path, monkeypatch, cfg=True)
    assert idx.fuzzy_match("测试测试") == []            # 4 字 < 6：跳过（不碰进程）
    idx = _fuzzy_env(tmp_path, monkeypatch, cfg=False)  # 显式关闭
    assert idx.fuzzy_match("帮我查一下最近的快递取件安排") == []


def test_fuzzy_parses_ids_with_hallucination_guard(tmp_path, monkeypatch):
    from bridge.indexer import fuzzy_match
    _fuzzy_env(
        tmp_path, monkeypatch,
        files={"modules/todo/agents.md": "A", "modules/mem/x.md": "B"},
        index={
            "todo.json": {"module": "todo", "entries": [{"kw": ["提醒"], "file": "modules/todo/agents.md", "title": "t1"}]},
            "memory.json": {"module": "memory", "entries": [{"kw": ["快递"], "file": "modules/mem/x.md", "title": "t2"}]},
        },
        stdout="编造号 99 与有效 2\n2",  # 99 越界丢弃；2 命中
        cfg="test/model",
    )
    hits = fuzzy_match("我之前说过快递要放驿站还是前台呢")
    # glob 序：memory.json < todo.json → 编号 2 = todo（条目顺序以实际清单为准）
    assert len(hits) == 1 and hits[0]["file"] == "modules/todo/agents.md"


def test_fuzzy_none_and_timeout(tmp_path, monkeypatch):
    import subprocess as sp
    idx = _fuzzy_env(tmp_path, monkeypatch, stdout="NONE", cfg="test/model",
                     files={"modules/todo/agents.md": "A"},
                     index={"todo.json": {"module": "todo", "entries": [
                         {"kw": ["提醒"], "file": "modules/todo/agents.md", "title": "t"}]}})
    assert idx.fuzzy_match("今天天气怎么样啊朋友们") == []
    # 超时退化
    def boom(*a, **k):
        raise sp.TimeoutExpired(cmd="opencode", timeout=30)
    idx.subprocess.run = boom
    assert idx.fuzzy_match("今天天气怎么样啊朋友们") == []


def test_tier_delta_prefix_truncation(tmp_path, monkeypatch):
    from bridge.indexer import _tier_delta
    import web.agent_gen as ag
    ins = tmp_path / "instructions"
    ins.mkdir()
    (ins / "tier0.md").write_text("第一\n", encoding="utf-8")
    (ins / "tier1.md").write_text("第一\n第二\n", encoding="utf-8")
    (ins / "tier3.md").write_text("第一\n第二\n第三\n第四\n", encoding="utf-8")
    monkeypatch.setattr(ag, "INSTRUCTIONS_DIR", ins)
    assert _tier_delta(0, 1) == ["第二"]
    assert _tier_delta(2, 3) == ["第四"]
    assert _tier_delta(3, 3) == []


def test_tier_increment_ladder_and_reset(tmp_path, monkeypatch):
    from bridge.indexer import _DEPTH, tier_increment
    import bridge.indexer as idx
    import web.agent_gen as ag
    ins = tmp_path / "instructions"
    ins.mkdir()
    lines = ["一", "二", "三", "四", "五"]
    for i in range(5):
        (ins / f"tier{i}.md").write_text("\n".join(lines[:i + 1]) + "\n", encoding="utf-8")
    monkeypatch.setattr(ag, "INSTRUCTIONS_DIR", ins)
    monkeypatch.setattr(idx, "OBSERVE_FILE", tmp_path / "none.jsonl")  # 无画像 → base 0
    _DEPTH.clear()

    # 前 5 条：无升档
    for _ in range(5):
        assert tier_increment("wx-1", "短消息") == ""
    # 第 6 条：msgs=6//6=1 → 升 tier1，增量=第二行
    out = tier_increment("wx-1", "随便聊聊")
    assert out == "\n\n[人设补充]\n二\n"
    assert _DEPTH["wx-1"]["cur"] == 1
    # 字数驱动：累计 1200 字 → steps=2 → 升 tier2
    tier_increment("wx-1", "字" * 600)
    assert _DEPTH["wx-1"]["cur"] == 1  # 600//600=1，msgs 并列取大仍 1 → 未再升
    tier_increment("wx-1", "字" * 600)
    assert _DEPTH["wx-1"]["cur"] == 2
    # 封顶：巨量文本后不超 tier4
    for _ in range(40):
        tier_increment("wx-1", "字" * 600)
    assert _DEPTH["wx-1"]["cur"] == 4


def test_refresh_current_tier_resets_depth(tmp_path, monkeypatch):
    from bridge.indexer import _DEPTH, refresh_current_tier, tier_increment
    import bridge.indexer as idx
    import web.agent_gen as ag
    ins = tmp_path / "instructions"
    ins.mkdir()
    (ins / "tier0.md").write_text("一\n", encoding="utf-8")
    (ins / "tier1.md").write_text("一\n二\n", encoding="utf-8")
    monkeypatch.setattr(ag, "INSTRUCTIONS_DIR", ins)
    monkeypatch.setattr(idx, "OBSERVE_FILE", tmp_path / "none.jsonl")
    _DEPTH.clear()
    for _ in range(7):
        tier_increment("wx-9", "消息")
    assert _DEPTH["wx-9"]["cur"] == 1
    refresh_current_tier("wx-9")          # 归档/重载 → 阶梯归零
    assert "wx-9" not in _DEPTH


# ---------- 批次 Ⅳ：模型解析统一 / config_gen 接线 / 启动自查 ----------

def test_regenerate_tiers_without_model_omits_flag(tmp_path, monkeypatch):
    """config 无 acp.model → argv 不带 -m，并使用只读纯净 agent。"""
    import web.agent_gen as ag
    import bridge.config as cfg

    seen = {}
    def fake_run(argv, **k):
        seen["argv"] = argv
        assert "--pure" in argv and "--agent" in argv and "plan" in argv
        from web.agent_gen import TIER_BUDGET
        seq = [f"条目{j}" for j in range(TIER_BUDGET[-1])]
        return types.SimpleNamespace(
            stdout=json.dumps({"type": "text", "part": {"text": "\n\n".join(
                f"===TIER{i}===\n" + "\n".join(seq[:TIER_BUDGET[i]])
                + f"\n===END_TIER{i}===" for i in range(5)
            )}}) + "\n" + json.dumps({"type": "step_finish", "part": {"reason": "stop"}}),
            stderr="",
            returncode=0,
        )

    ins = tmp_path / "instructions"
    monkeypatch.setattr(ag, "INSTRUCTIONS_DIR", ins)
    monkeypatch.setattr(ag.subprocess, "run", fake_run)
    monkeypatch.setattr(cfg, "resolve_opencode", lambda: "/usr/bin/opencode")
    monkeypatch.setattr(cfg, "xdg_env", lambda: {})
    monkeypatch.setattr(cfg, "get", lambda k, d=None: None)  # acp.model 未配置

    assert ag.regenerate_tiers(
        identity={"address": "鑫", "assistant_name": "鱼", "role": "r", "language": "l"},
        rules=["守则"],
    )
    assert "-m" not in seen["argv"]                          # 不带 -m


def test_config_gen_wires_instructions(tmp_path, monkeypatch):
    """向导④生成：数据根 jsonc 含 instructions 真实字段 + tier-current 已落盘。"""
    import json as _json
    import web.handlers.config_gen as cg
    import web.agent_gen as ag
    import bridge.config as bc

    monkeypatch.setattr(cg, "OPCODE_CONFIG", tmp_path / "opencode.jsonc")
    monkeypatch.setattr(cg, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(ag, "INSTRUCTIONS_DIR", tmp_path / "instructions")
    monkeypatch.setattr(bc, "DATA_ROOT", tmp_path)

    class _App:
        steps = {}
        def _h(self, *a, **k):
            pass
    # 依赖的 _gen_config/_gen_key 等落 tmp：monkeypatch 掉非本测关注项
    monkeypatch.setattr(cg, "_gen_config", lambda: {"ok": True})
    monkeypatch.setattr(cg, "_gen_key", lambda: {"ok": True})
    monkeypatch.setattr(cg, "_gen_trust_notice", lambda: {"ok": True})
    monkeypatch.setattr(cg.auth, "password_exists", lambda: True)

    cg.handle(_App(), {})
    text = (tmp_path / "opencode.jsonc").read_text(encoding="utf-8")
    assert '"instructions"' in text and "tier-current.md" in text
    assert (tmp_path / "instructions" / "tier-current.md").is_file()


def test_bridge_startup_warns_on_missing_instructions(tmp_path, monkeypatch, caplog):
    """启动自查：数据根 jsonc 缺 instructions → warning；有 → 不告警；无 jsonc → 静默。"""
    import logging
    import bridge.main as m

    core = m.BridgeCore.__new__(m.BridgeCore)

    # 缺 instructions
    cfg_a = tmp_path / "a"
    cfg_a.mkdir()
    (cfg_a / "opencode.jsonc").write_text('{\n  "model": "x"\n}\n', encoding="utf-8")
    monkeypatch.setattr(m, "WORKDIR", cfg_a)
    with caplog.at_level(logging.WARNING, logger="wechat-bridge"):
        core._check_instructions_wiring()
    assert any("缺 instructions" in r.message for r in caplog.records)

    # 已接线
    caplog.clear()
    cfg_b = tmp_path / "b"
    cfg_b.mkdir()
    (cfg_b / "opencode.jsonc").write_text(
        '{\n  "instructions": ["instructions/tier-current.md"]\n}\n', encoding="utf-8")
    monkeypatch.setattr(m, "WORKDIR", cfg_b)
    core._check_instructions_wiring()
    assert not any("缺 instructions" in r.message for r in caplog.records)

    # 接线存在但 tier 文件缺失/行数错误：分别给出只读告警
    caplog.clear()
    (cfg_b / "instructions").mkdir()
    (cfg_b / "instructions" / "tier-current.md").write_text("当前", encoding="utf-8")
    (cfg_b / "instructions" / "tier0.md").write_text("一\n二\n", encoding="utf-8")
    core._check_instructions_wiring()
    assert any("tier1.md 缺失" in r.message for r in caplog.records)
    assert any("tier0.md 非空行数=2" in r.message for r in caplog.records)

    # 无 jsonc（未配置形态）→ 静默
    caplog.clear()
    monkeypatch.setattr(m, "WORKDIR", tmp_path / "empty")
    core._check_instructions_wiring()
    assert not any("缺 instructions" in r.message for r in caplog.records)
