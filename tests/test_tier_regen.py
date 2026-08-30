"""批 3（260827）：tier 分档生成——基线内置 / 硬校验 / staging 隔离提交。"""
from __future__ import annotations

import types
import json
from pathlib import Path


# ---------- ensure_builtins ----------

def test_ensure_builtins_copies_baseline_and_current(tmp_path, monkeypatch):
    import web.agent_gen as ag
    monkeypatch.setattr(ag, "INSTRUCTIONS_DIR", tmp_path)
    ag.ensure_builtins()
    from web.agent_gen import TIER_BUDGET
    for i in range(5):
        f = tmp_path / f"tier{i}.md"
        assert f.is_file()
        assert len([ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]) == TIER_BUDGET[i]
    assert (tmp_path / "tier-current.md").is_file()
    assert (tmp_path / "tier-current.md").read_text(encoding="utf-8") == (
        tmp_path / "tier0.md"
    ).read_text(encoding="utf-8")


def test_ensure_builtins_never_overwrites_custom(tmp_path, monkeypatch):
    import web.agent_gen as ag
    monkeypatch.setattr(ag, "INSTRUCTIONS_DIR", tmp_path)
    (tmp_path / "tier0.md").write_text("用户定制内容", encoding="utf-8")
    ag.ensure_builtins()
    assert (tmp_path / "tier0.md").read_text(encoding="utf-8") == "用户定制内容"


# ---------- _validate_tiers ----------

def test_validate_tiers_ok_and_bad(tmp_path):
    import web.agent_gen as ag
    d = tmp_path / "instructions"
    d.mkdir()
    assert not ag._validate_tiers(d)  # 全缺
    budget = ag.TIER_BUDGET
    for i in range(5):
        (d / f"tier{i}.md").write_text("\n".join(f"条目{j}" for j in range(budget[i])), encoding="utf-8")
    assert ag._validate_tiers(d)
    (d / "tier3.md").write_text("只有一行", encoding="utf-8")  # 行数不符
    assert not ag._validate_tiers(d)


def test_parse_tier_output_strict_protocol():
    import web.agent_gen as ag
    good = _protocol_output(_good_files())
    assert ag._parse_tier_output(good) is not None
    assert ag._parse_tier_output("解释文字\n" + good) is None
    assert ag._parse_tier_output(good + "\n额外文字") is None
    broken = good.replace("条目0\n条目1\n===END_TIER1===", "别的第一条\n条目1\n===END_TIER1===")
    assert ag._parse_tier_output(broken) is None


def test_extract_run_text_reads_jsonl_and_requires_finish():
    import web.agent_gen as ag
    stream = "\n".join([
        '{"type":"step_start"}',
        '{"type":"text","part":{"text":"甲"}}',
        '{"type":"text","part":{"text":"乙"}}',
        '{"type":"step_finish","part":{"reason":"stop"}}',
    ])
    assert ag._extract_run_text(stream) == "甲乙"
    assert ag._extract_run_text(stream.replace(
        '{"type":"step_finish","part":{"reason":"stop"}}', ""
    )) is None


def test_write_web_log_records_failure(tmp_path, monkeypatch):
    import web.agent_gen as ag
    monkeypatch.setattr(ag, "DATA_ROOT", tmp_path)
    ag._write_web_log("协议校验失败")
    assert "协议校验失败" in (tmp_path / "logs" / "web.log").read_text(encoding="utf-8")


# ---------- regenerate_tiers（stdout 协议 + 原子提交） ----------

def _protocol_output(files: dict[str, str]) -> str:
    """把 tier 文件内容转换为生成器 stdout 协议。"""
    blocks = []
    for i in range(5):
        name = f"tier{i}.md"
        if name not in files:
            continue
        blocks.append(f"===TIER{i}===\n{files[name]}\n===END_TIER{i}===")
    return "\n\n".join(blocks)


def _good_files(budget=None):
    """按 TIER_BUDGET 生成合格五档（前缀截断关系自动成立）。"""
    from web.agent_gen import TIER_BUDGET
    budget = budget or TIER_BUDGET
    seq = [f"条目{j}" for j in range(budget[-1])]
    return {f"tier{i}.md": "\n".join(seq[:budget[i]]) for i in range(5)}


def _fake_run_output(files: dict[str, str], returncode: int = 0):
    """返回 fake subprocess.run：模型只返回 stdout，不接触任何文件。"""

    def fake_run(_argv, **kwargs):
        return types.SimpleNamespace(
            stdout=(
                json.dumps({"type": "text", "part": {"text": _protocol_output(files)}})
                + "\n"
                + json.dumps({"type": "step_finish", "part": {"reason": "stop"}})
            ),
            stderr="", returncode=returncode
        )

    return fake_run


def _good_files(budget=None):
    """按 TIER_BUDGET 生成合格五档（前缀截断关系自动成立）。"""
    from web.agent_gen import TIER_BUDGET
    budget = budget or TIER_BUDGET
    seq = [f"条目{j}" for j in range(budget[-1])]
    return {f"tier{i}.md": "\n".join(seq[:budget[i]]) for i in range(5)}


def test_regenerate_tiers_commits_on_valid(tmp_path, monkeypatch):
    import web.agent_gen as ag
    monkeypatch.setattr(ag, "INSTRUCTIONS_DIR", tmp_path / "instructions")
    monkeypatch.setattr(ag.subprocess, "run", _fake_run_output(_good_files()))
    monkeypatch.setattr(ag, "resolve_opencode", lambda: "/usr/bin/opencode", raising=False)
    import bridge.config as cfg
    monkeypatch.setattr(cfg, "resolve_opencode", lambda: "/usr/bin/opencode")
    monkeypatch.setattr(cfg, "xdg_env", lambda: {})
    monkeypatch.setattr(cfg, "get", lambda _k, d=None: "test/model", raising=False)

    assert ag.regenerate_tiers(
        identity={"address": "用户", "assistant_name": "小助手", "role": "r", "language": "l"},
        rules=["守则一"],
    )
    for i in range(5):
        assert (tmp_path / "instructions" / f"tier{i}.md").is_file()
    assert (tmp_path / "instructions" / "tier-current.md").read_text(encoding="utf-8") == (
        tmp_path / "instructions" / "tier0.md"
    ).read_text(encoding="utf-8")


def test_regenerate_tiers_keeps_old_on_invalid(tmp_path, monkeypatch):
    import web.agent_gen as ag
    ins = tmp_path / "instructions"
    ins.mkdir(parents=True)
    (ins / "tier0.md").write_text("旧基线", encoding="utf-8")
    monkeypatch.setattr(ag, "INSTRUCTIONS_DIR", ins)
    bad = _good_files()
    del bad["tier4.md"]  # 残品：缺 tier4
    monkeypatch.setattr(ag.subprocess, "run", _fake_run_output(bad))
    monkeypatch.setattr(cfg := __import__("bridge.config", fromlist=["x"]), "resolve_opencode",
                        lambda: "/usr/bin/opencode")
    monkeypatch.setattr(cfg, "xdg_env", lambda: {})
    monkeypatch.setattr(cfg, "get", lambda _k, d=None: "test/model", raising=False)

    assert ag.regenerate_tiers(
        identity={"address": "用户", "assistant_name": "小助手", "role": "r", "language": "l"},
        rules=["守则一"],
    ) is False
    assert (ins / "tier0.md").read_text(encoding="utf-8") == "旧基线"  # 旧文件原样保留
    assert not (ins / "tier1.md").exists()  # 残品未提交


def test_regenerate_restores_model_file_writes(tmp_path, monkeypatch):
    """即使 opencode 错认目录直接改真实 tier，也恢复六文件且不刷新 current。"""
    import web.agent_gen as ag
    import bridge.config as cfg
    ins = tmp_path / "instructions"
    ins.mkdir()
    old = {}
    for i in range(5):
        old[f"tier{i}.md"] = f"旧{i}\n"
        (ins / f"tier{i}.md").write_text(old[f"tier{i}.md"], encoding="utf-8")
    (ins / "tier-current.md").write_text("旧current\n", encoding="utf-8")

    def fake_run(_argv, **_kwargs):
        # 复刻部署事故：模型错误地写真实目录，但 stdout 协议无效。
        (ins / "tier0.md").write_text("越权新内容\n", encoding="utf-8")
        return types.SimpleNamespace(stdout="OK", stderr="", returncode=0)

    monkeypatch.setattr(ag, "INSTRUCTIONS_DIR", ins)
    monkeypatch.setattr(ag.subprocess, "run", fake_run)
    monkeypatch.setattr(cfg, "resolve_opencode", lambda: "/usr/bin/opencode")
    monkeypatch.setattr(cfg, "xdg_env", lambda: {})
    monkeypatch.setattr(cfg, "get", lambda *_: "test/model")
    assert not ag.regenerate_tiers(
        identity={"address": "鑫", "assistant_name": "鱼", "role": "r", "language": "l"},
        rules=["守则"],
    )
    assert (ins / "tier0.md").read_text(encoding="utf-8") == old["tier0.md"]
    assert (ins / "tier-current.md").read_text(encoding="utf-8") == "旧current\n"


def test_commit_tiers_rolls_back_all_files(tmp_path, monkeypatch):
    import os
    import web.agent_gen as ag
    ins = tmp_path / "instructions"
    staging = tmp_path / "staging"
    (staging / "instructions").mkdir(parents=True)
    monkeypatch.setattr(ag, "INSTRUCTIONS_DIR", ins)
    ins.mkdir()
    old = {}
    for fname in ag.TIER_FILES + [ag.CURRENT_TIER]:
        old[fname] = f"旧-{fname}\n"
        (ins / fname).write_text(old[fname], encoding="utf-8")
    payload = {f"tier{i}": [f"新{j}" for j in range(i + 1)] for i in range(5)}
    real_replace = os.replace
    calls = {"n": 0}

    def fail_midway(src, dst):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("commit fail")
        return real_replace(src, dst)

    monkeypatch.setattr(ag.os, "replace", fail_midway)
    try:
        ag._commit_tiers(staging, payload)
    except OSError:
        pass
    else:
        raise AssertionError("expected commit failure")
    for fname, content in old.items():
        assert (ins / fname).read_text(encoding="utf-8") == content


def test_commit_survives_exdev_environment(tmp_path, monkeypatch):
    """复刻部署机 EXDEV 场景：staging 在 tmpfs、数据根在磁盘。
    修复后所有 replace 均为同目录（.commit.tmp），模拟的跨设备 rename 不应触发。"""
    import os as os_mod
    import shutil as sh
    import bridge.config as cfg
    from web import agent_gen as ag

    real_replace = os_mod.replace
    exdev_hits = []

    def fake_replace(src, dst):
        src_s, dst_s = str(src), str(dst)
        # 模拟跨设备：staging(/tmp) → 数据根 的旧式 rename 会命中；同盘 tmp 不命中
        if "staging" in str(src).replace("/staging/", "/") and ".commit.tmp" not in src_s and ".restore.tmp" not in src_s and ".off" not in src_s and "backup" not in src_s and "wc-tiers" in src_s:
            exdev_hits.append(src_s)
            raise OSError(18, "Invalid cross-device link")
        return real_replace(src, dst)

    ins = tmp_path / "instructions"
    monkeypatch.setattr(ag, "INSTRUCTIONS_DIR", ins)
    staging = tmp_path / "staging"
    (staging / "instructions").mkdir(parents=True)
    payload = {f"tier{i}": [f"新{j}" for j in range(i + 1)] for i in range(5)}
    monkeypatch.setattr(cfg, "get", lambda *_: "test/model")
    ag._commit_tiers(staging, payload)
    assert exdev_hits == [], "提交路径出现跨设备 os.replace（应经 _atomic_put 同盘 tmp+replace）"
    assert (ins / "tier-current.md").read_text(encoding="utf-8") == (
        ins / "tier0.md").read_text(encoding="utf-8")
    assert (ins / "tier4.md").read_text(encoding="utf-8") == "新0\n新1\n新2\n新3\n新4\n"
