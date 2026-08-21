# -*- mode: python ; coding: utf-8 -*-
"""wechat-claw PyInstaller spec：单文件可执行（web 向导 + 管理后台）。

用法（见 scripts/build.py）：
    pyinstaller --noconfirm wechat-claw.spec

打包内容：
- 入口 web/wizard.py（可执行文件直接启动 web 服务）
- 数据文件（--add-data 语义）：web/static（含 cities.json）、web/templates、
  config.yaml.example、AGENTS.md、opencode.jsonc.example、vendor/nssm（Windows）
- 动态导入模块：web.handlers.* 显式收集
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

_SPEC_DIR = Path(SPECPATH)  # PyInstaller 内置：spec 文件所在目录
_ROOT = _SPEC_DIR.parent

# 动态导入的 handlers（importlib 加载，静态分析看不到）
hiddenimports = collect_submodules("web.handlers")
hiddenimports += [
    "web.auth",
    "web.agent_gen",
    "web.schema.config_schema",
    "web.schema.module_schema",  # admin 函数内延迟 import，显式收集
]
# vendor SDK 为 editable 安装（PEP 660），PyInstaller 静态分析收集不到，显式全量收集
hiddenimports += ["wechat_agent_sdk"] + collect_submodules("wechat_agent_sdk")

# 数据文件（打包后位于 _MEIPASS 根，RESOURCE_ROOT 逻辑读取）
datas = [
    (str(_ROOT / "web" / "static"), "web/static"),
    (str(_ROOT / "web" / "templates"), "web/templates"),
    (str(_ROOT / "config.yaml.example"), "."),
    (str(_ROOT / "AGENTS.md"), "."),
    (str(_ROOT / "opencode.jsonc.example"), "."),
    (str(_ROOT / "vendor" / "opencode-scheduler"), "vendor/opencode-scheduler"),
]
if sys.platform == "win32":
    # Windows 服务化依赖 nssm（service_up 从 RESOURCE_ROOT 读取）
    datas.append((str(_ROOT / "vendor" / "nssm"), "vendor/nssm"))

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
    console=False,  # windowed：无控制台弹窗（日志落数据根 logs/）
)
