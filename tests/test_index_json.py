"""二期 Ⅱ-2/Ⅱ-3（260827）：index 位置表机制——校验 / place_index 四分支 / register 插拔。"""
from __future__ import annotations

import json


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
