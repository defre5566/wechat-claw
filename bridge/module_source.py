"""模块源管理（web 安装模块功能）：源 CRUD + 拉取校验 + 安装。

数据：modules/modules_data/sources.json（用户数据区，随备份）。
源：builtin 官方源（defre5566/wechat-claw_modules_official，不可删）+ 自定义（github / local）。
拉取：GitHub 浅 clone（--depth 1）到临时目录 / 本地源直接读 → 读 manifest.json →
      模块列表缓存 + 源指纹（manifest 内容 sha256，变更检测：指纹变 → 刷新缓存）。
校验（安装时）：清单一致（manifest.files vs 实际文件）+ 结构规范（module.json/worker 对齐 docs/04）
                + 哈希（模块包 sha256 = files 内容排序拼接；不符自动重拉一次再校验）。
安装：复制到 modules/<name>/ → register 发 token + 建数据目录 + enabled:false → 刷新缓存/豁免。
安全：所有源都校验哈希（防劫持）；信任自担，web 提示"只装信任来源"。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from bridge.config import MODULES_ROOT

MODULES_DIR = MODULES_ROOT
DATA_ROOT = MODULES_DIR / "modules_data"
SOURCES_FILE = DATA_ROOT / "sources.json"

OFFICIAL_SOURCE = {
    "id": "official",
    "name": "官方模块库",
    "type": "github",
    "url": "https://github.com/defre5566/wechat-claw_modules_official.git",
    "builtin": True,
    "added_at": "",
    "fingerprint": "",
    "modules": [],
}

# 测试隔离点
if os.environ.get("OPENCODE_PERMS_ROOT"):  # 复用同一隔离根
    _root = Path(os.environ["OPENCODE_PERMS_ROOT"])
    MODULES_DIR = _root / "modules"
    DATA_ROOT = MODULES_DIR / "modules_data"
    SOURCES_FILE = DATA_ROOT / "sources.json"


# ---------- sources CRUD ----------

def load_sources() -> list[dict]:
    """读 sources.json；文件缺失/损坏返回 [official]。"""
    if SOURCES_FILE.is_file():
        try:
            data = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("sources"), list):
                return data["sources"]
        except Exception:
            pass
    return [dict(OFFICIAL_SOURCE)]


def save_sources(sources: list[dict]) -> bool:
    """原子写 sources.json（official 恒在首位）。"""
    ordered = sorted(sources, key=lambda s: (0 if s.get("builtin") else 1, s.get("added_at", "")))
    try:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        tmp = SOURCES_FILE.with_name(SOURCES_FILE.name + ".tmp")
        tmp.write_text(json.dumps({"sources": ordered}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(SOURCES_FILE)
        return True
    except OSError:
        return False


def _find_source(sources: list[dict], sid: str) -> dict | None:
    return next((s for s in sources if s.get("id") == sid), None)


# ---------- 拉取（GitHub 浅 clone / 本地目录） ----------

def _git_clone(url: str, dest: Path) -> bool:
    """浅 clone 到目标目录；成功返回 True。"""
    try:
        r = subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", url, str(dest)],
            capture_output=True, text=True, timeout=120,
        )
        return r.returncode == 0
    except Exception:
        return False


def _source_local_dir(src: dict) -> Path | None:
    """本地源：返回目录（url 即路径）；不存在返回 None。"""
    p = Path(os.path.expanduser(src.get("url", "")))
    return p if p.is_dir() else None


def _read_manifest(mod_root: Path) -> dict | None:
    mj = mod_root / "manifest.json"
    if not mj.is_file():
        return None
    try:
        data = json.loads(mj.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _list_modules_from_manifest(manifest: dict) -> list[dict]:
    """manifest.modules → 列表缓存条目（name/version/purpose/sha256）。"""
    out = []
    for m in manifest.get("modules", []) or []:
        if not isinstance(m, dict) or not m.get("name"):
            continue
        out.append({
            "name": m["name"],
            "version": m.get("version", ""),
            "purpose": m.get("purpose", ""),
            "sha256": m.get("sha256", ""),
        })
    return out


def _fingerprint(manifest: dict) -> str:
    """源指纹 = manifest 内容 sha256（变更检测）。"""
    return hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def fetch_source(src: dict) -> dict:
    """拉取源的最新模块列表 + 指纹；返回 {ok, modules, fingerprint, error}。"""
    if src.get("type") == "local":
        root = _source_local_dir(src)
        if root is None:
            return {"ok": False, "modules": [], "fingerprint": "", "error": f"本地目录不存在: {src.get('url')}"}
        manifest = _read_manifest(root)
        if manifest is None:
            return {"ok": False, "modules": [], "fingerprint": "", "error": "源缺少 manifest.json"}
        fp = _fingerprint(manifest)
        return {"ok": True, "modules": _list_modules_from_manifest(manifest), "fingerprint": fp}

    # github：浅 clone 到临时目录
    tmp = Path(tempfile.mkdtemp(prefix="wc-source-"))
    try:
        if not _git_clone(src.get("url", ""), tmp):
            return {"ok": False, "modules": [], "fingerprint": "", "error": f"clone 失败: {src.get('url')}"}
        manifest = _read_manifest(tmp)
        if manifest is None:
            return {"ok": False, "modules": [], "fingerprint": "", "error": "源缺少 manifest.json"}
        fp = _fingerprint(manifest)
        return {"ok": True, "modules": _list_modules_from_manifest(manifest), "fingerprint": fp}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def refresh_source(sources: list[dict], sid: str) -> dict:
    """拉取并更新缓存；指纹未变也刷新（列表可能变）；返回 {ok, updated, error}。"""
    src = _find_source(sources, sid)
    if src is None:
        return {"ok": False, "error": f"源不存在: {sid}"}
    res = fetch_source(src)
    if not res["ok"]:
        return {"ok": False, "error": res["error"]}
    old_fp = src.get("fingerprint", "")
    src["modules"] = res["modules"]
    src["fingerprint"] = res["fingerprint"]
    save_sources(sources)
    return {"ok": True, "updated": old_fp != res["fingerprint"], "modules": res["modules"]}


def list_catalog(sources: list[dict]) -> list[dict]:
    """合并所有源模块（按源在列表中的顺序），标注 installed。"""
    installed = set()
    from modules.registry_index import MODULES_DIR as _md
    if _md.is_dir():
        for d in _md.iterdir():
            if (d / "module.json").is_file():
                installed.add(d.name)
    out = []
    for src in sources:
        for m in src.get("modules", []) or []:
            item = dict(m)
            item["source"] = src.get("name", src.get("id", ""))
            item["source_id"] = src.get("id", "")
            item["installed"] = m.get("name") in installed
            out.append(item)
    return out


# ---------- 校验与安装 ----------

def _expand_files(mod_dir: Path, files: list) -> list[str]:
    """manifest.files → 相对路径文件清单（目录展开递归）。

    排除运行时垃圾（__pycache__/*.pyc），保证哈希只覆盖源仓库受版本控制的文件。
    """
    out: list[str] = []
    for f in files or []:
        p = mod_dir / str(f)
        if p.is_dir():
            for sub in sorted(p.rglob("*")):
                if not sub.is_file():
                    continue
                rel = str(sub.relative_to(mod_dir))
                if "__pycache__" in rel or rel.endswith(".pyc"):
                    continue
                out.append(rel)
        elif p.is_file():
            out.append(str(f))
    return sorted(out)


def _module_sha256(mod_dir: Path, files: list) -> str:
    """模块包哈希：files 内容按相对路径排序拼接 → sha256。"""
    h = hashlib.sha256()
    for rel in _expand_files(mod_dir, files):
        try:
            h.update(rel.encode("utf-8"))
            h.update(b"\x00")
            h.update((mod_dir / rel).read_bytes())
            h.update(b"\x00")
        except OSError:
            continue
    return h.hexdigest()


def _validate_structure(mod_dir: Path, name: str, manifest_entry: dict) -> str | None:
    """结构规范校验（对齐 docs/04）：module.json 合法 + worker 存在；错误返回描述。"""
    mj = mod_dir / "module.json"
    if not mj.is_file():
        return "缺少 module.json"
    try:
        data = json.loads(mj.read_text(encoding="utf-8"))
    except Exception:
        return "module.json 非法 JSON"
    if not isinstance(data, dict):
        return "module.json 结构非法"
    if data.get("name") != name:
        return f"module.json 模块名({data.get('name')!r})与清单({name!r})不一致"
    worker = mod_dir / f"{name}_worker.py"
    if not worker.is_file():  # 大小写兼容（如 Planner → planner_worker.py）
        worker = mod_dir / f"{name.lower()}_worker.py"
        if not worker.is_file():
            return f"缺少 {name}_worker.py"
    return None


def install_module(sources: list[dict], sid: str, name: str) -> dict:
    """从源安装模块：定位 → 校验（清单/结构/哈希，哈希不符重拉一次）→ 复制 → 注册。

    返回 {ok, error?}。
    """
    src = _find_source(sources, sid)
    if src is None:
        return {"ok": False, "error": f"源不存在: {sid}"}

    # 同名冲突：已存在 → 拒绝（先卸载再装）
    if (MODULES_DIR / name / "module.json").is_file():
        return {"ok": False, "error": f"模块 {name} 已安装（如需重装请先卸载）"}

    entry = next((m for m in src.get("modules", []) if m.get("name") == name), None)
    if entry is None:
        return {"ok": False, "error": f"源 {src.get('name')} 中没有模块 {name}"}

    tmp_root = None
    try:
        if src.get("type") == "local":
            root = _source_local_dir(src)
            if root is None:
                return {"ok": False, "error": f"本地目录不存在: {src.get('url')}"}
            mod_root = root / name
        else:
            tmp_root = Path(tempfile.mkdtemp(prefix="wc-install-"))
            if not _git_clone(src.get("url", ""), tmp_root):
                return {"ok": False, "error": f"clone 失败: {src.get('url')}"}
            mod_root = tmp_root / name

        if not mod_root.is_dir():
            return {"ok": False, "error": f"源中缺少模块目录: {name}"}

        manifest = _read_manifest(tmp_root if tmp_root else _source_local_dir(src))
        files = []
        if manifest:
            mentry = next((m for m in manifest.get("modules", []) if m.get("name") == name), None)
            files = (mentry or {}).get("files", [])

        # 校验①清单一致：声明的 files 实际都存在
        if files:
            missing = [f for f in files if not (mod_root / str(f)).exists()]
            if missing:
                return {"ok": False, "error": f"清单声明文件缺失: {missing}"}
        # 校验②结构规范
        err = _validate_structure(mod_root, name, entry)
        if err:
            return {"ok": False, "error": f"结构不规范: {err}"}
        # 校验③哈希（不符 → 重新拉取一次再校验）
        expected = entry.get("sha256", "")
        if expected:
            actual = _module_sha256(mod_root, files)
            if actual != expected:
                # 重拉一次（可能缓存/下载损坏）
                if tmp_root is None:
                    tmp_root = Path(tempfile.mkdtemp(prefix="wc-install-"))
                    if not _git_clone(src.get("url", ""), tmp_root):
                        return {"ok": False, "error": "哈希不符且重拉失败"}
                    mod_root = tmp_root / name
                    actual = _module_sha256(mod_root, files)
                if actual != expected:
                    return {"ok": False, "error": f"模块包哈希校验失败（可能被篡改或源已更新，请刷新源后重试）"}

        # 复制到 modules/<name>/
        dest = MODULES_DIR / name
        dest.mkdir(parents=True, exist_ok=True)
        for rel in _expand_files(mod_root, files):
            src_f = mod_root / rel
            dst_f = dest / rel
            dst_f.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_f, dst_f)

        # 注册：token + 数据目录 + enabled:false
        from modules.register import register_module
        purpose = entry.get("purpose", "")
        spec = "规范.md"
        result = register_module(name, purpose=purpose, spec=spec)
        if not result["ok"]:
            shutil.rmtree(dest, ignore_errors=True)  # 注册失败回滚
            return {"ok": False, "error": "注册失败，已回滚"}

        # 刷新缓存 installed 标记 + 豁免
        for m in src.get("modules", []):
            if m.get("name") == name:
                m["installed"] = True
        save_sources(sources)
        from bridge.permissions import refresh_permissions
        refresh_permissions()
        # 安装记录版本（installed.json，部署状态数据区）
        from modules.register import save_module_state
        save_module_state(name, version=str(entry.get("version") or ""), source_id=sid)
        return {"ok": True, "name": name, "enabled": False}
    finally:
        if tmp_root is not None:
            shutil.rmtree(tmp_root, ignore_errors=True)


# ---------- 更新（哈希 + .bak 双保险，不换 token / 不碰数据区） ----------

def update_module_from_source(sources: list[dict], sid: str, name: str) -> dict:
    """从源更新已装模块（手动按钮 / 每日自动检查共用）。

    流程：拉新包 → 校验①清单 ②结构 ③sha256（不符拒收，保持旧版）
    → .bak 备份现目录 → 复制（跳过 token，G1 不轮换）→ 失败恢复 .bak
    → 联动（schedule 重算 + job 重登记 + 豁免）→ 记录新版本。

    与安装的区别：不重新注册（不换 token）、settings.json 全程不碰（部署状态保留）。
    """
    src = _find_source(sources, sid)
    if src is None:
        return {"ok": False, "error": f"源不存在: {sid}"}
    if not (MODULES_DIR / name / "module.json").is_file():
        return {"ok": False, "error": f"模块 {name} 未安装（先安装再更新）"}
    entry = next((m for m in src.get("modules", []) if m.get("name") == name), None)
    if entry is None:
        return {"ok": False, "error": f"源 {src.get('name')} 中没有模块 {name}"}

    tmp_root = None
    try:
        if src.get("type") == "local":
            root = _source_local_dir(src)
            if root is None:
                return {"ok": False, "error": f"本地目录不存在: {src.get('url')}"}
            mod_root = root / name
        else:
            tmp_root = Path(tempfile.mkdtemp(prefix="wc-update-"))
            if not _git_clone(src.get("url", ""), tmp_root):
                return {"ok": False, "error": f"clone 失败: {src.get('url')}"}
            mod_root = tmp_root / name

        manifest = _read_manifest(tmp_root if tmp_root else _source_local_dir(src))
        files = []
        if manifest:
            mentry = next((m for m in manifest.get("modules", []) if m.get("name") == name), None)
            files = (mentry or {}).get("files", [])

        # 校验①清单一致
        if files:
            missing = [f for f in files if not (mod_root / str(f)).exists()]
            if missing:
                return {"ok": False, "error": f"清单声明文件缺失: {missing}"}
        # 校验②结构规范
        err = _validate_structure(mod_root, name, entry)
        if err:
            return {"ok": False, "error": f"结构不规范: {err}"}
        # 校验③哈希（不符 → 拒收，保持旧版运行）
        expected = entry.get("sha256", "")
        if expected:
            actual = _module_sha256(mod_root, files)
            if actual != expected:
                return {"ok": False, "error": "模块包哈希校验失败（可能被篡改或源已更新，请刷新源后重试）"}

        # .bak 备份现目录（含 token，复制失败时恢复）
        dest = MODULES_DIR / name
        bak = MODULES_DIR / f"{name}.bak"
        if bak.exists():
            shutil.rmtree(bak, ignore_errors=True)
        try:
            shutil.copytree(dest, bak)
        except OSError as e:
            return {"ok": False, "error": f"备份现有模块失败: {e}"}

        # 复制新文件（跳过 token）
        try:
            for rel in _expand_files(mod_root, files):
                if rel == "token":
                    continue  # G1：模块存在期间不轮换 token
                dst_f = dest / rel
                dst_f.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(mod_root / rel, dst_f)
        except OSError as e:
            shutil.rmtree(dest, ignore_errors=True)
            try:
                shutil.move(bak, dest)  # 恢复旧版
            except OSError:
                return {"ok": False, "error": f"复制失败且恢复失败（模块目录可能不完整，请重装）: {e}"}
            return {"ok": False, "error": f"复制失败，已恢复旧版: {e}"}
        shutil.rmtree(bak, ignore_errors=True)

        # 更新后联动（schedule 重算 + job 重登记 + 豁免 + 索引刷新）
        from modules.register import refresh_module_config, save_module_state
        refresh_module_config(name)
        save_module_state(name, version=str(entry.get("version") or ""), source_id=sid)
        for m in src.get("modules", []):
            if m.get("name") == name:
                m["installed"] = True
        save_sources(sources)
        return {"ok": True, "updated": True, "name": name,
                "version": str(entry.get("version") or "")}
    finally:
        if tmp_root is not None:
            shutil.rmtree(tmp_root, ignore_errors=True)


# ---------- 每日自动更新检查（指纹驱动，静默） ----------

def check_updates(sources: list[dict] | None = None, force: bool = False) -> dict:
    """自动更新检查：指纹没变跳过（零开销）；变了 → 逐模块更新。

    开关：全局 update.auto_enabled（config 用户段）&& 模块级 auto_update（settings.json）。
    静默：只写日志与结果，不推送。force=True 绕过全局开关（web 手动检查/更新用）。
    """
    from bridge.config import get
    if not force and not get("update.auto_enabled", True):
        return {"checked": False, "reason": "全局自动更新已关闭"}
    sources = sources if sources is not None else load_sources()
    updated: list[str] = []
    skipped: list[str] = []
    errors: list[tuple[str, str]] = []
    for src in sources:
        if src.get("builtin") and src.get("type") != "local":
            pass  # 官方源同样检查（模块都在官方源）
        res = refresh_source(sources, src["id"])
        if not res["ok"]:
            continue  # 源不可达：跳过（静默，下次再查）
        if not res["updated"]:
            continue  # 指纹没变：源没动，跳过
        for m in res["modules"] or []:
            name = m.get("name")
            if not name or not (MODULES_DIR / name / "module.json").is_file():
                continue  # 未安装
            from modules.register import get_auto_update
            if not get_auto_update(name):
                skipped.append(name)
                continue
            r = update_module_from_source(sources, src["id"], name)
            if r.get("updated"):
                updated.append(name)
            elif not r.get("ok"):
                errors.append((name, r.get("error", "未知错误")))
    return {"checked": True, "updated": updated, "skipped": skipped, "errors": errors}
