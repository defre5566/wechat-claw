"""批 3（260827）：tier 分档生成——基线内置 / 硬校验 / staging 隔离提交。"""
from __future__ import annotations

import types
from pathlib import Path


# ---------- ensure_builtins ----------

def test_ensure_builtins_copies_baseline_and_current(tmp_path, monkeypatch):
    import web.agent_gen as ag
    monkeypatch.setattr(ag, "INSTRUCTIONS_DIR", tmp_path)
    ag.ensure_builtins()
    for i in range(5):
        f = tmp_path / f"tier{i}.md"
        assert f.is_file()
        assert len([ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]) == i + 1
    assert (tmp_path / "tier-current.md").is_file()
    assert (tmp_path / "tier-current.md").read_text(encoding="utf-8") == (
        tmp_path / "tier2.md"
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
    for i in range(5):
        (d / f"tier{i}.md").write_text("\n".join(f"条目{j}" for j in range(i + 1)), encoding="utf-8")
    assert ag._validate_tiers(d)
    (d / "tier3.md").write_text("只有一行", encoding="utf-8")  # 行数不符
    assert not ag._validate_tiers(d)


# ---------- regenerate_tiers（staging 隔离 + 原子提交） ----------

def _fake_run_writes(files: dict[str, str]):
    """返回 fake subprocess.run：在 cwd/instructions 下写 files（缺文件模拟残品）。"""

    def fake_run(_argv, **kwargs):
        out = Path(kwargs["cwd"]) / "instructions"
        out.mkdir(exist_ok=True)
        for name, content in files.items():
            (out / name).write_text(content, encoding="utf-8")
        return types.SimpleNamespace(stdout="OK", stderr="")

    return fake_run


def _good_files() -> dict[str, str]:
    return {
        f"tier{i}.md": "\n".join(f"条目{j}" for j in range(i + 1)) for i in range(5)
    }


def test_regenerate_tiers_commits_on_valid(tmp_path, monkeypatch):
    import web.agent_gen as ag
    monkeypatch.setattr(ag, "INSTRUCTIONS_DIR", tmp_path / "instructions")
    monkeypatch.setattr(ag.subprocess, "run", _fake_run_writes(_good_files()))
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
        tmp_path / "instructions" / "tier2.md"
    ).read_text(encoding="utf-8")


def test_regenerate_tiers_keeps_old_on_invalid(tmp_path, monkeypatch):
    import web.agent_gen as ag
    ins = tmp_path / "instructions"
    ins.mkdir(parents=True)
    (ins / "tier0.md").write_text("旧基线", encoding="utf-8")
    monkeypatch.setattr(ag, "INSTRUCTIONS_DIR", ins)
    bad = _good_files()
    del bad["tier4.md"]  # 残品：缺 tier4
    monkeypatch.setattr(ag.subprocess, "run", _fake_run_writes(bad))
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
