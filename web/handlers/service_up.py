"""⑥ 平台守护拉起 bridge：systemd / launchd / nssm（selftest 下 dry-run）。

- Linux：写 ~/.config/systemd/user/wechat-bridge.service → systemctl --user daemon-reload + enable --now
- macOS：写 ~/Library/LaunchAgents/wechat-bridge.plist → launchctl load -w
- Windows：vendor/nssm/win64.exe install + start（nssm 已随仓库 vendor）
- dry_run（selftest）：只生成文件/打印命令，不真执行
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from bridge.config import PROJECT_ROOT

SELFTEST = os.environ.get("WEB_SELFTEST") == "1"


def _write_and_run(path: Path, content: str, commands: list[list[str]]) -> list[dict]:
    steps = []
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        steps.append({"cmd": f"写入 {path}", "ok": True})
    else:
        steps.append({"cmd": f"已存在 {path}", "ok": True})
    for cmd in commands:
        if SELFTEST:
            steps.append({"cmd": " ".join(shlex.quote(c) for c in cmd), "ok": True, "dry": True})
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            steps.append({
                "cmd": " ".join(shlex.quote(c) for c in cmd),
                "ok": r.returncode == 0,
                "out": (r.stdout or r.stderr or "").strip()[-200:],
            })
        except Exception as e:
            steps.append({"cmd": " ".join(shlex.quote(c) for c in cmd), "ok": False, "out": str(e)})
    return steps


def _systemd() -> list[dict]:
    unit = Path.home() / ".config" / "systemd" / "user" / "wechat-bridge.service"
    content = f"""[Unit]
Description=WeChat to opencode bridge (iLink direct)
After=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=3

[Service]
Type=simple
ExecStart={PROJECT_ROOT / '.venv' / 'bin' / 'python'} -m bridge.main
WorkingDirectory={PROJECT_ROOT}
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
"""
    return _write_and_run(
        unit, content,
        [["systemctl", "--user", "daemon-reload"],
         ["systemctl", "--user", "enable", "--now", "wechat-bridge"]],
    )


def _launchd() -> list[dict]:
    plist = Path.home() / "Library" / "LaunchAgents" / "wechat-bridge.plist"
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>wechat-bridge</string>
    <key>ProgramArguments</key>
    <array>
        <string>{PROJECT_ROOT / '.venv' / 'bin' / 'python'}</string>
        <string>-m</string>
        <string>bridge.main</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{PROJECT_ROOT}</string>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>600</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/wechat-bridge.out.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/wechat-bridge.err.log</string>
</dict>
</plist>
"""
    return _write_and_run(
        plist, content,
        [["launchctl", "load", "-w", str(plist)]],
    )


def _nssm() -> list[dict]:
    if os.name != "nt":
        return [{"cmd": "Windows 平台才支持 nssm", "ok": False}]
    arch = "win64" if sys.maxsize > 2 ** 32 else "win32"
    nssm = PROJECT_ROOT / "vendor" / "nssm" / f"{arch}.exe"
    py = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if not nssm.is_file():
        return [{"cmd": f"nssm 缺失: {nssm}", "ok": False}]
    return _write_and_run(
        Path(f"nssm-{arch}.exe"), "",  # 占位（nssm 无需配置文件）
        [[str(nssm), "install", "wechat-bridge", str(py), "-m", "bridge.main"],
         [str(nssm), "set", "wechat-bridge", "AppDirectory", str(PROJECT_ROOT)],
         [str(nssm), "set", "wechat-bridge", "AppExit", "Default", "Restart"],
         [str(nssm), "set", "wechat-bridge", "AppRestartDelay", "10000"],
         [str(nssm), "start", "wechat-bridge"]],
    )


def handle(app, body: dict | None = None) -> dict:
    if os.name == "nt":
        steps = _nssm()
    elif sys.platform == "darwin":
        steps = _launchd()
    else:
        steps = _systemd()
    ok = all(s["ok"] for s in steps)
    if ok:
        app.steps["service_up"] = True
    return {"ok": ok, "steps": steps}
