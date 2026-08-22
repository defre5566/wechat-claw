"""本地完整性校验测试：verify_module_integrity / verify_all_modules + 安装/更新基准落盘。

隔离：OPENCODE_PERMS_ROOT 重定向 module_source 路径到临时根（import 后 reload）；
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


def _mk_installed(root: Path, with_baseline: bool = True):
    """已装模块（文件 + token + 数据区 settings/installed；可选完整性基准）。"""
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
    st = {"version": "0.1.0", "source_id": "local", "installed_at": "x"}
    if with_baseline:
        st["sha256"] = ms._module_sha256(mod_dir, SRC_FILES)
        st["files"] = SRC_FILES
    (data_dir / "installed.json").write_text(
        json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
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


# ---------- verify_module_integrity ----------

def test_verify_ok(iso_env):
    _mk_installed(iso_env, with_baseline=True)
    ok, why = ms.verify_module_integrity("Planner")
    assert ok and why == "ok"


def test_verify_tampered_rejects(iso_env):
    mod_dir, _ = _mk_installed(iso_env, with_baseline=True)
    (mod_dir / "planner_worker.py").write_text("v1-content-MODIFIED", encoding="utf-8")
    ok, why = ms.verify_module_integrity("Planner")
    assert not ok and "篡改" in why


def test_verify_no_baseline_skips(iso_env):
    _mk_installed(iso_env, with_baseline=False)
    ok, why = ms.verify_module_integrity("Planner")
    assert ok and why == "no-baseline"


def test_verify_missing_dir(iso_env):
    _mk_installed(iso_env, with_baseline=True)
    import shutil
    shutil.rmtree(iso_env / "modules" / "Planner")
    ok, why = ms.verify_module_integrity("Planner")
    assert not ok and "目录缺失" in why


def test_verify_all_modules_reports_only_tampered(iso_env):
    mod_dir, _ = _mk_installed(iso_env, with_baseline=True)
    (mod_dir / "module.json").write_text(json.dumps({"name": "Planner-Evil"}), encoding="utf-8")
    # 另一个无基准模块（本地手写）：应跳过不报
    hand = iso_env / "modules" / "Handmade"
    hand.mkdir(parents=True)
    (hand / "module.json").write_text(json.dumps({"name": "Handmade"}), encoding="utf-8")
    (hand / "handmade_worker.py").write_text("x", encoding="utf-8")
    problems = ms.verify_all_modules()
    assert [n for n, _ in problems] == ["Planner"]


# ---------- 基准落盘链路（安装/更新写入 installed.json） ----------

def test_update_refreshes_baseline(iso_env):
    root = iso_env
    src_root, entry = _mk_src(root.parent, version="0.2.0", worker_body="v2-content")
    _, data_dir = _mk_installed(root, with_baseline=False)
    sources = [{"id": "local", "name": "本地", "type": "local", "url": str(src_root), "modules": [entry]}]

    r = ms.update_module_from_source(sources, "local", "Planner")
    assert r["ok"] and r["updated"]
    st = json.loads((data_dir / "installed.json").read_text(encoding="utf-8"))
    assert st["version"] == "0.2.0"
    assert st.get("sha256") == entry["sha256"]          # 基准写入
    assert st.get("files") == SRC_FILES                  # 文件清单写入
    # 更新后校验通过（基准=新文件哈希）
    ok, why = ms.verify_module_integrity("Planner")
    assert ok and why == "ok"


def test_save_state_without_baseline(iso_env):
    """无哈希源（本地手写）更新不写基准 → 校验跳过。"""
    from modules import register
    register.save_module_state("Planner", version="0.3.0", source_id="local")
    st = json.loads((iso_env / "modules" / "modules_data" / "Planner" / "installed.json").read_text(encoding="utf-8"))
    assert "sha256" not in st and "files" not in st
    ok, why = ms.verify_module_integrity("Planner")
    assert ok and why == "no-baseline"
