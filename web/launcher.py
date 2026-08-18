#!/usr/bin/env python3
"""wechat-claw web 引导器（纯标准库，venv 未建时必须能跑）。

流程：检查 .venv 是否可用（python 存在 + yaml/wechat_agent_sdk 可导入）
  → 不可用则自动重建（venv + requirements + vendor -e）
  → exec 换 .venv 解释器进 wizard.py（此后可 import SDK / yaml）

用法：python3 web/launcher.py [--port 8650] [--selftest]
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
PIP = VENV / ("Scripts/pip.exe" if os.name == "nt" else "bin/pip")


def _venv_usable() -> bool:
    if not PY.is_file():
        return False
    try:
        r = subprocess.run(
            [str(PY), "-c", "import yaml, wechat_agent_sdk"],
            capture_output=True, timeout=60,
        )
        return r.returncode == 0
    except Exception:
        return False


def _build_venv() -> None:
    print("[launcher] 创建虚拟环境 .venv ...")
    r = subprocess.run([sys.executable, "-m", "venv", str(VENV)])
    if r.returncode != 0:
        sys.exit("[launcher] venv 创建失败")
    for cmd in (
        [str(PIP), "install", "-r", str(ROOT / "requirements.txt")],
        [str(PIP), "install", "-e", str(ROOT / "vendor" / "wechat_agent_sdk")],
    ):
        print(f"[launcher] 运行: {' '.join(cmd)}")
        r = subprocess.run(cmd)
        if r.returncode != 0:
            sys.exit(f"[launcher] 依赖安装失败: {cmd}")
    print("[launcher] 依赖就绪")


def main(argv: list[str]) -> int:
    if not _venv_usable():
        _build_venv()
    wizard = ROOT / "web" / "wizard.py"
    os.execv(str(PY), [str(PY), str(wizard), *argv])
    return 1  # 不可达


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
