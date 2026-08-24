# -*- mode: python ; coding: utf-8 -*-
"""wechat-claw PyInstaller spec：单文件可执行（web 向导 + 管理后台）。

用法（见 scripts/build.py）：
    pyinstaller --noconfirm wechat-claw.spec

打包内容：
- 入口 web/wizard.py（可执行文件直接启动 web 服务）
- 数据文件：web/static（含 cities.json）、web/templates、config.yaml.example、
  AGENTS.md、opencode.jsonc.example、vendor/nssm（Windows）、vendor/opencode
- 全量收集：bridge/ + modules/ + patches/（datas 强制打包，排除 __pycache__）
- 动态导入模块：web.handlers.* + wechat_agent_sdk + bridge + modules
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

_SPEC_DIR = Path(SPECPATH)  # PyInstaller 内置：spec 文件所在目录
_ROOT = _SPEC_DIR.parent


def _walk(src: str, dst: str) -> list[tuple[str, str]]:
    """遍历目录，排除 __pycache__ 和 .pyc 文件，返回 (源路径, 目标路径) 列表。"""
    out: list[tuple[str, str]] = []
    src_p = Path(src)
    if not src_p.exists():
        return out
    for f in src_p.rglob("*"):
        if f.is_dir() or "__pycache__" in f.parts or f.suffix == ".pyc":
            continue
        rel = f.relative_to(src_p)
        # 统一正斜杠（PyInstaller 内部用 POSIX 路径，Windows 反斜杠可能触发转义问题）
        out.append((str(f), str(Path(dst) / rel).replace("\\", "/")))
    return out


# 动态导入的 handlers（importlib 加载，静态分析看不到）
hiddenimports = collect_submodules("web.handlers")
hiddenimports += [
    "web.auth",
    "web.agent_gen",
    "web.schema.config_schema",
    "web.schema.module_schema",
]
# vendor SDK 为 editable 安装，PyInstaller 静态分析收集不到，显式全量收集
hiddenimports += ["wechat_agent_sdk"] + collect_submodules("wechat_agent_sdk")
# bridge + modules 用 datas 全量 + hiddenimports 兜底
hiddenimports += ["bridge"] + collect_submodules("bridge")
hiddenimports += ["modules"] + collect_submodules("modules")

# 数据文件（打包后位于 _MEIPASS 根，RESOURCE_ROOT 逻辑读取）
datas = [
    (str(_ROOT / "web" / "static"), "web/static"),
    (str(_ROOT / "web" / "templates"), "web/templates"),
    (str(_ROOT / "config.yaml.example"), "."),
    (str(_ROOT / "AGENTS.md"), "."),
    (str(_ROOT / "opencode.jsonc.example"), "."),
    (str(_ROOT / "patches"), "patches"),
]
# bridge + modules：datas 全量打包（排除 __pycache__/pyc，确保运行时完整可用）
datas += _walk(str(_ROOT / "bridge"), "bridge")
datas += _walk(str(_ROOT / "modules"), "modules")

if sys.platform == "win32":
    datas.append((str(_ROOT / "vendor" / "nssm"), "vendor/nssm"))
    oc = _ROOT / "vendor" / "opencode" / "opencode.exe"
    if oc.is_file():
        datas.append((str(oc), "vendor/opencode/opencode.exe"))

a = Analysis(
    [str(_ROOT / "web" / "wizard.py")],
    pathex=[str(_ROOT), str(_ROOT / "vendor")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="wechat-claw",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="wechat-claw",
)