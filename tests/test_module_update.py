"""模块更新流程测试：update_module_from_source（校验/.bak/token 保留/版本记录）+ check_updates。

隔离：OPENCODE_PERMS_ROOT 重定向 module_source 路径到临时目录（import 后 reload）。
联动（refresh_module_config）打桩——已被其他测试覆盖。
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bridge.module_source as ms

SRC_FILES = ["module.json", "planner_worker.py", "规范.md", "job.template.json", "directions.json"]


def _mk_src(tmp_path: Path, version: str = "0.2.0", worker_body: str = "v2-content"):
    """本地源：模块目录 + manifest（sha256 按当前内容算）。"""
    src_root = tmp_path / "src"
    mod_src = src_root / "Planner"
    mod_src.mkdir(parents=True)
    (mod_src / "module.json").write_text(json.dumps({
        "name": "Planner", "job_template": "job.template.json",
        "schedule_from_settings": [{"phase": "morning", "time_field": "morning_time"}],
    }), encoding="utf-8")
    (mod_src / "planner_worker.py").write_text(worker_body, encoding="utf-8")
    (mod_src / "规范.md").write_text("spec", encoding="utf-8")
    (mod_src / "job.template.json").write_text(json.dumps(
        {"name": "x", "slug": "s", "phase": "morning", "offset_min": 5, "output_dir": "briefing"}),
        encoding="utf-8")
    (mod_src / "directions.json").write_text(json.dumps({"时政": {}}), encoding="utf-8")
    h = ms._module_sha256(mod_src, SRC_FILES)
    manifest = {"schema_version": 1, "modules": [{
        "name": "Planner", "version": version, "files": SRC_FILES, "sha256": h,
    }]}
    (src_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return src_root, manifest["modules"][0]


def _mk_installed(root: Path):
    """已装模块（v0.1.0 文件 + module.json + token + 数据区 settings/installed）。"""
    mod_dir = root / "modules" / "Planner"
    data_dir = root / "modules" / "modules_data" / "Planner"
    mod_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    (mod_dir / "module.json").write_text(json.dumps({"name": "Planner"}), encoding="utf-8")
    (mod_dir / "planner_worker.py").write_text("v1-content", encoding="utf-8")
    (mod_dir / "token").write_text("tok-123", encoding="utf-8")
    (data_dir / "settings.json").write_text(json.dumps(
        {"enabled": True, "retry": None, "auto_update": True, "morning_time": "08:30"}),
        encoding="utf-8")
    (data_dir / "installed.json").write_text(json.dumps(
        {"version": "0.1.0", "source_id": "local", "installed_at": "x"}), encoding="utf-8")
    return mod_dir, data_dir


@pytest.fixture
def iso_env(tmp_path, monkeypatch):
    """隔离 module_source + register 路径到临时根 + 打桩联动。"""
    monkeypatch.setenv("OPENCODE_PERMS_ROOT", str(tmp_path / "root"))
    importlib.reload(ms)
    from modules import register
    register.MODULES_DIR = tmp_path / "root" / "modules"
    register.DATA_ROOT = tmp_path / "root" / "modules" / "modules_data"
    # 联动打桩（schedule/job/豁免刷新已被其他测试覆盖）
    register.refresh_module_config = lambda name: None
    yield tmp_path / "root"
    importlib.reload(ms)   # 还原真实路径（防污染其他测试）
    importlib.reload(register)


# ---------- update_module_from_source ----------

def test_update_from_local_source(iso_env):
    tmp, src_root, entry = None, None, None
    root = iso_env
    src_root, entry = _mk_src(root.parent, version="0.2.0", worker_body="v2-content")
    mod_dir, data_dir = _mk_installed(root)
    sources = [{"id": "local", "name": "本地", "type": "local", "url": str(src_root), "modules": [entry]}]

    r = ms.update_module_from_source(sources, "local", "Planner")
    assert r["ok"] and r["updated"] and r["version"] == "0.2.0"
    assert (mod_dir / "planner_worker.py").read_text(encoding="utf-8") == "v2-content"  # 新文件
    assert (mod_dir / "token").read_text() == "tok-123"                                  # token 保留（G1）
    assert not (mod_dir.parent / "Planner.bak").exists()                                 # .bak 清理
    st = json.loads((data_dir / "installed.json").read_text(encoding="utf-8"))
    assert st["version"] == "0.2.0"                                                      # 版本记录
    sv = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
    assert sv["enabled"] is True and sv["morning_time"] == "08:30"                       # 数据区不碰


def test_update_hash_mismatch_rejects(iso_env):
    root = iso_env
    src_root, entry = _mk_src(root.parent, version="0.2.0")
    entry["sha256"] = "0" * 64  # 伪造哈希 → 校验拒收
    mod_dir, _ = _mk_installed(root)
    sources = [{"id": "local", "name": "本地", "type": "local", "url": str(src_root), "modules": [entry]}]

    r = ms.update_module_from_source(sources, "local", "Planner")
    assert not r["ok"] and "哈希" in r["error"]
    assert (mod_dir / "planner_worker.py").read_text(encoding="utf-8") == "v1-content"  # 旧版未动


def test_update_copy_failure_restores_bak(iso_env, monkeypatch):
    root = iso_env
    src_root, entry = _mk_src(root.parent, version="0.2.0")
    mod_dir, _ = _mk_installed(root)
    sources = [{"id": "local", "name": "本地", "type": "local", "url": str(src_root), "modules": [entry]}]

    real_copy2 = ms.shutil.copy2

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ms.shutil, "copy2", boom)
    r = ms.update_module_from_source(sources, "local", "Planner")
    assert not r["ok"] and "恢复" in r["error"]
    assert (mod_dir / "planner_worker.py").read_text(encoding="utf-8") == "v1-content"  # 已恢复旧版
    monkeypatch.setattr(ms.shutil, "copy2", real_copy2)


def test_update_not_installed(iso_env):
    root = iso_env
    src_root, entry = _mk_src(root.parent)
    sources = [{"id": "local", "name": "本地", "type": "local", "url": str(src_root), "modules": [entry]}]
    r = ms.update_module_from_source(sources, "local", "Planner")
    assert not r["ok"] and "未安装" in r["error"]


# ---------- check_updates ----------

def test_check_updates_global_off(monkeypatch):
    from bridge import config
    monkeypatch.setattr(config, "get", lambda k, d=None: False if k == "update.auto_enabled" else d)
    r = ms.check_updates()
    assert r == {"checked": False, "reason": "全局自动更新已关闭"}


def test_check_updates_fingerprint_unchanged(monkeypatch):
    from bridge import config
    monkeypatch.setattr(config, "get", lambda k, d=None: True if k == "update.auto_enabled" else d)
    monkeypatch.setattr(ms, "refresh_source", lambda sources, sid: {"ok": True, "updated": False, "modules": []})
    r = ms.check_updates()
    assert r["checked"] is True and r["updated"] == [] and r["errors"] == []


def test_check_updates_module_switch_off(tmp_path, monkeypatch):
    from bridge import config
    from modules import register
    monkeypatch.setattr(config, "get", lambda k, d=None: True if k == "update.auto_enabled" else d)
    # 假已装目录（两个模块都有 module.json）
    mod_root = tmp_path / "mods"
    for name in ("Planner", "todo"):
        (mod_root / name).mkdir(parents=True)
        (mod_root / name / "module.json").write_text(json.dumps({"name": name}), encoding="utf-8")
    monkeypatch.setattr(ms, "MODULES_DIR", mod_root)
    monkeypatch.setattr(ms, "refresh_source", lambda sources, sid: {
        "ok": True, "updated": True,
        "modules": [{"name": "Planner"}, {"name": "todo"}],
    })
    monkeypatch.setattr(register, "get_auto_update", lambda name: name != "Planner")
    monkeypatch.setattr(ms, "update_module_from_source", lambda *a, **k: {"ok": True, "updated": True})
    r = ms.check_updates(sources=[{"id": "s1", "name": "源", "type": "local", "url": "/tmp"}])
    assert r["skipped"] == ["Planner"]       # 模块级开关关 → 跳过
    assert r["updated"] == ["todo"]
