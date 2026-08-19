"""② opencode 检测与引导（不再自动执行远程脚本，S5 供应链风险收敛）。

- 检测 opencode --version：已安装 → 向导该步通过
- 未安装 → 返回官方安装命令 + 文档链接，由用户本机手动执行后点"重新检测"
- 不在 wizard 进程内 `curl|bash`：bridge 进程能读 crypto.key/微信 token，远程脚本管道
  执行存在供应链风险（opencode.ai 被攻陷即一锅端），与项目"高危先确认"文化不符
"""
from __future__ import annotations

import os
import subprocess

SELFTEST = os.environ.get("WEB_SELFTEST") == "1"

# 官方安装命令（按平台）；文档链接
_INSTALL_CMD = {
    "posix": "curl -fsSL https://opencode.ai/install | bash",
    "nt": "irm https://opencode.ai/install.ps1 | iex",
}
_DOC_URL = "https://opencode.ai/docs/install"


def _detect() -> dict:
    try:
        r = subprocess.run(
            ["opencode", "--version"], capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            out = (r.stdout or r.stderr or "").strip().splitlines()
            return {"ok": True, "value": out[0] if out else "已安装"}
    except Exception:
        pass
    return {"ok": False, "value": "未安装"}


def handle(app, body: dict | None = None) -> dict:
    # selftest：mock 已安装（CI 全新 runner 无 opencode，且不再走网络安装）
    if SELFTEST:
        app.steps["opencode"] = True
        return {"ok": True, "already": True, "version": "selftest-mock"}

    detect = _detect()
    if detect["ok"]:
        app.steps["opencode"] = True
        return {"ok": True, "already": True, "version": detect["value"]}

    # 未安装：返回官方命令 + 文档，由用户手动安装后重新检测（不在本进程执行远程脚本）
    cmd = _INSTALL_CMD["nt" if os.name == "nt" else "posix"]
    return {
        "ok": False,
        "missing": True,
        "cmd": cmd,
        "doc": _DOC_URL,
        "hint": "请在终端手动执行上述命令安装 opencode，完成后点「重新检测」",
    }


def status(app, body: dict | None = None) -> dict:
    # 不再有安装长任务；保留接口供前端兼容，恒为 done
    return {"ok": True, "done": True, "lines": []}
