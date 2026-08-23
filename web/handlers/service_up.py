"""⑥ 平台守护拉起 bridge：systemd / launchd / nssm（selftest 下 dry-run）。

- Linux：写 ~/.config/systemd/user/wechat-bridge.service → systemctl --user daemon-reload + enable --now
- macOS：写 ~/Library/LaunchAgents/wechat-bridge.plist → launchctl load -w
- Windows：vendor/nssm/win64.exe install + start（nssm 已随仓库 vendor）
- dry_run（selftest）：只生成文件/打印命令，不真执行

应用列表入口（部署完成后三端都有）：
- Windows：开始菜单「wechat-claw Web 管理」.lnk（wscript → 无窗口 VBS 启动器）+ HKCU
  卸载注册项（设置→应用可卸载：停/删服务 + 删注册项 + 删快捷方式 + 删数据根）
- Linux：~/.local/share/applications/wechat-claw-web.desktop → 启动器脚本
- macOS：~/Applications/wechat-claw Web 管理.app（最小 bundle，launchpad 可见）
启动器逻辑：8650 已在监听 → 直接开浏览器；否则后台起服务、等端口就绪、开浏览器。
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from bridge.config import DATA_ROOT, DEPLOY_ROOT, RESOURCE_ROOT

SELFTEST = os.environ.get("WEB_SELFTEST") == "1"
WEB_PORT = 8650
VERSION = "0.1.2"

# Windows 用户级自启：HKCU Run 键（登录后自动运行，无需管理员）
_RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "wechat-claw-bridge"


def _log_web(msg: str) -> None:
    """写数据根 logs/web.log（与向导同文件同格式；服务步骤记录，selftest 跳过）。"""
    if SELFTEST:
        return
    try:
        f = DATA_ROOT / "logs" / "web.log"
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [service_up] {msg}\n")
    except Exception:
        pass


def _is_admin() -> bool:
    """Windows 管理员检测（UAC 提权判断）；非 Windows 视为已授权（平台原逻辑）。"""
    if os.name != "nt":
        return True
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _write_and_run(path: Path, content: str, commands: list[list[str]]) -> list[dict]:
    steps = []
    if SELFTEST:
        # 服务隔离：不写真实文件、不执行命令，仅输出将执行的内容
        steps.append({"cmd": f"（selftest）将写入 {path}", "ok": True, "dry": True})
        for cmd in commands:
            steps.append({"cmd": " ".join(shlex.quote(c) for c in cmd), "ok": True, "dry": True})
        return steps
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        steps.append({"cmd": f"写入 {path}", "ok": True})
    else:
        steps.append({"cmd": f"已存在 {path}", "ok": True})
    for cmd in commands:
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


def _write_file(path: Path, content: str, mode: int | None = None) -> list[dict]:
    if SELFTEST:
        return [{"cmd": f"（selftest）将写入 {path}", "ok": True, "dry": True}]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if mode is not None:
            path.chmod(mode)
        return [{"cmd": f"写入 {path}", "ok": True}]
    except OSError as e:
        return [{"cmd": f"写入失败 {path}: {e}", "ok": False}]


def _copy_file(src: Path, dst: Path) -> list[dict]:
    if SELFTEST:
        return [{"cmd": f"（selftest）将复制 {src} → {dst}", "ok": True, "dry": True}]
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copy2(src, dst)
        return [{"cmd": f"复制 {src} → {dst}", "ok": True}]
    except OSError as e:
        return [{"cmd": f"复制失败 {src}: {e}", "ok": False}]


def _exec_commands(commands: list[list[str]], timeout: int = 180) -> list[dict]:
    if SELFTEST:
        return [{"cmd": " ".join(shlex.quote(c) for c in cmd), "ok": True, "dry": True}
                for cmd in commands]
    steps = []
    for cmd in commands:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            steps.append({
                "cmd": " ".join(shlex.quote(c) for c in cmd),
                "ok": r.returncode == 0,
                "out": (r.stdout or r.stderr or "").strip()[-200:],
            })
        except Exception as e:
            steps.append({"cmd": " ".join(shlex.quote(c) for c in cmd), "ok": False, "out": str(e)})
    return steps


def _bridge_command() -> str:
    """bridge 启动命令：打包形态 = 可执行文件自身；源码形态 = venv python -m bridge.main。"""
    if getattr(sys, "frozen", False):
        return sys.executable
    py = DEPLOY_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return f"{py} -m bridge.main"


def _systemd() -> list[dict]:
    unit = Path.home() / ".config" / "systemd" / "user" / "wechat-bridge.service"
    content = f"""[Unit]
Description=WeChat to opencode bridge (iLink direct)
After=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=3

[Service]
Type=simple
ExecStart={_bridge_command()}
WorkingDirectory={DEPLOY_ROOT}
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
        <string>{_bridge_command()}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{DEPLOY_ROOT}</string>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>600</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{DATA_ROOT / "logs" / "bridge.out.log"}</string>
    <key>StandardErrorPath</key>
    <string>{DATA_ROOT / "logs" / "bridge.err.log"}</string>
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
    nssm = RESOURCE_ROOT / "vendor" / "nssm" / f"{arch}.exe"
    py = sys.executable if getattr(sys, "frozen", False) \
        else str(DEPLOY_ROOT / ".venv" / "Scripts" / "python.exe")
    if not nssm.is_file():
        return [{"cmd": f"nssm 缺失: {nssm}", "ok": False}]
    return _write_and_run(
        Path(f"nssm-{arch}.exe"), "",  # 占位（nssm 无需配置文件）
        [[str(nssm), "install", "wechat-bridge", str(py), "-m", "bridge.main"],
         [str(nssm), "set", "wechat-bridge", "AppDirectory", str(DEPLOY_ROOT)],
         [str(nssm), "set", "wechat-bridge", "AppExit", "Default", "Restart"],
         [str(nssm), "set", "wechat-bridge", "AppRestartDelay", "10000"],
         [str(nssm), "start", "wechat-bridge"]],
    )


def _nssm_remove() -> list[dict]:
    """移除 wechat-bridge 系统服务（需管理员；调用前确认提权）。"""
    if os.name != "nt":
        return [{"cmd": "Windows 平台才支持 nssm", "ok": False}]
    arch = "win64" if sys.maxsize > 2 ** 32 else "win32"
    nssm = RESOURCE_ROOT / "vendor" / "nssm" / f"{arch}.exe"
    if not nssm.is_file():
        return [{"cmd": f"nssm 缺失: {nssm}", "ok": False}]
    return _exec_commands([[str(nssm), "stop", "wechat-bridge"],
                           [str(nssm), "remove", "wechat-bridge", "confirm"]])


def _user_autostart_reg() -> list[dict]:
    """用户级自启：HKCU Run 键 → 无窗口 VBS → 后台跑 bridge（登录后自动运行，无需管理员）。"""
    if os.name != "nt":
        return [{"cmd": "仅 Windows 支持用户级自启", "ok": False}]
    vbs = DATA_ROOT / "wechat-claw-bridge.vbs"
    py = sys.executable if getattr(sys, "frozen", False) \
        else str(DEPLOY_ROOT / ".venv" / "Scripts" / "python.exe")
    vbs_content = (
        "' wechat-claw bridge 用户级自启（向导/管理后台生成）\n"
        "Dim sh\n"
        "Set sh = CreateObject(\"WScript.Shell\")\n"
        "sh.Run \"\"\"%s\"\" -m bridge.main\", 0, False\n"
    ) % py
    steps = _write_file(vbs, vbs_content)
    cmd = ["reg", "add", _RUN_KEY, "/v", _RUN_VALUE, "/t", "REG_SZ",
           "/d", f'wscript.exe "{vbs}"', "/f"]
    steps += _exec_commands([cmd])
    return steps


def _user_autostart_unreg() -> list[dict]:
    """移除用户级自启（HKCU Run 键；无需管理员）。"""
    if os.name != "nt":
        return []
    return _exec_commands([["reg", "delete", _RUN_KEY, "/v", _RUN_VALUE, "/f"]])


def _service_up_windows() -> list[dict]:
    """Windows 拉起 bridge：管理员 → nssm 系统服务；非管理员 → 用户级自启 + UAC 引导说明。"""
    if _is_admin():
        steps = _nssm()
        steps.append({"cmd": "说明：系统服务 wechat-bridge 已注册（开机自启、免登录）", "ok": True})
    else:
        steps = _user_autostart_reg()
        steps.append({"cmd": "说明：当前为普通权限，已注册用户级自启（登录后自动运行）。"
                             "如需开机免登录的系统服务，请到管理后台开启『开机自动启动』"
                             "——届时会弹出 UAC 授权窗口，请点『允许』", "ok": True})
    return steps


# ---------- 应用列表入口 / 卸载注册（数据根随平台规范，见 bridge.config.DATA_ROOT） ----------

def _launcher_posix() -> str:
    """POSIX 启动器脚本：已监听 → 开浏览器；否则后台起服务并等端口就绪。"""
    frozen = getattr(sys, "frozen", False)
    if frozen:
        exe_path = DEPLOY_ROOT / "wechat-claw"
        start_cmd = f'nohup "{exe_path}" >/dev/null 2>&1 &'
    else:
        start_sh = DEPLOY_ROOT / "web" / "start.sh"
        start_cmd = f'nohup bash "{start_sh}" >/dev/null 2>&1 &'
    opener = 'open "http://127.0.0.1:${PORT}"' if sys.platform == "darwin" \
        else 'xdg-open "http://127.0.0.1:${PORT}" >/dev/null 2>&1 &'
    return f"""#!/usr/bin/env bash
# wechat-claw Web 管理入口（初始化向导生成）
PORT={WEB_PORT}
open_url() {{ {opener} }}
probe() {{ (exec 3<>"/dev/tcp/127.0.0.1/${{PORT}}") 2>/dev/null && {{ exec 3>&- 3<&-; return 0; }}; return 1; }}
if probe; then open_url; exit 0; fi
{start_cmd}
for i in $(seq 1 80); do
  sleep 0.5
  if probe; then open_url; exit 0; fi
done
echo "wechat-claw web 启动超时（127.0.0.1:${{PORT}}）" >&2
exit 1
"""


def _linux_desktop_entry() -> list[dict]:
    launcher = DATA_ROOT / "wechat-claw-web.sh"
    desktop = Path.home() / ".local" / "share" / "applications" / "wechat-claw-web.desktop"
    steps = _write_file(launcher, _launcher_posix(), mode=0o755)
    desktop_content = f"""[Desktop Entry]
Type=Application
Name=wechat-claw Web 管理
Comment=wechat-claw 初始化向导与管理后台
Exec="{launcher}"
Terminal=false
Categories=Network;Utility;
StartupNotify=false
"""
    steps += _write_file(desktop, desktop_content)
    return steps


def _macos_app_entry() -> list[dict]:
    app_dir = Path.home() / "Applications" / "wechat-claw Web 管理.app"
    script = app_dir / "Contents" / "MacOS" / "wechat-claw-web.sh"
    plist = app_dir / "Contents" / "Info.plist"
    steps = _write_file(script, _launcher_posix(), mode=0o755)
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>wechat-claw Web 管理</string>
    <key>CFBundleIdentifier</key>
    <string>com.wechat-claw.web</string>
    <key>CFBundleExecutable</key>
    <string>wechat-claw-web.sh</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>{VERSION}</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.13</string>
</dict>
</plist>
"""
    steps += _write_file(plist, plist_content)
    return steps


def _win_app_entries() -> list[dict]:
    """Windows：开始菜单快捷方式 + HKCU 卸载注册项（设置→应用可见可卸载）。"""
    if os.name != "nt":
        return []
    steps: list[dict] = []
    appdata = os.environ.get("LOCALAPPDATA")
    if not appdata:
        return [{"cmd": "LOCALAPPDATA 未定义，无法注册卸载项", "ok": False}]
    base = Path(appdata) / "wechat-claw"                     # 数据根
    uninstall_bat = Path(appdata) / "wechat-claw-uninstall.bat"
    vbs = base / "wechat-claw-web.vbs"
    start_menu = (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows"
                  / "Start Menu" / "Programs" / "wechat-claw")
    lnk = start_menu / "wechat-claw Web 管理.lnk"
    frozen = getattr(sys, "frozen", False)
    exe = sys.executable if frozen else None

    # 1) 复制 nssm 到数据根（卸载脚本自包含，不依赖解包目录）
    arch = "win64" if sys.maxsize > 2 ** 32 else "win32"
    nssm_src = RESOURCE_ROOT / "vendor" / "nssm" / f"{arch}.exe"
    if nssm_src.is_file():
        steps += _copy_file(nssm_src, base / "nssm.exe")

    # 2) 无窗口 VBS 启动器（已监听 → 开浏览器；否则起服务等就绪）
    if frozen:
        start_cmd = 'sh.Run """%s""", 0, False' % exe
    else:
        start_cmd = 'sh.Run """%s\\web\\start.bat""", 0, False' % DEPLOY_ROOT
    # VBS 用 "" 转义引号，不能放 Python 三引号里（"" 后跟 " 会触发三引号结束）；
    # 用普通双引号字符串 + 转义，% 占位（VBS 无 % 冲突）
    vbs_content = (
        "' wechat-claw Web 管理启动器（初始化向导生成）\n"
        "Dim sh, i, probe\n"
        "Set sh = CreateObject(\"WScript.Shell\")\n"
        "For i = 1 To 80\n"
        "    Set probe = sh.Exec(\"cmd /c netstat -ano | findstr \"\"LISTENING\"\" | findstr \"\"127.0.0.1:%s\"\"\")\n"
        "    If Not probe.StdOut.AtEndOfStream Then Exit For\n"
        "    If i = 1 Then %s\n"
        "    WScript.Sleep 500\n"
        "Next\n"
        "sh.Run \"cmd /c start http://127.0.0.1:%s\", 0, False\n"
    ) % (WEB_PORT, start_cmd, WEB_PORT)
    steps += _write_file(vbs, vbs_content)

    # 3) 开始菜单快捷方式（wscript → vbs）——目标目录须先创建（否则 Save 抛 DirectoryNotFound）
    lnk_cmd = (
        f"$dir='{start_menu}'; if (!(Test-Path $dir)) {{ New-Item -ItemType Directory -Force -Path $dir | Out-Null }}; "
        f"$ws=New-Object -ComObject WScript.Shell; "
        f"$s=$ws.CreateShortcut('{lnk}'); "
        f"$s.TargetPath='wscript.exe'; "
        f"$s.Arguments='\"{vbs}\"'; "
        + (f"$s.IconLocation='{exe},0'; " if exe else "")
        + "$s.Save()"
    )
    steps += _exec_commands([["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                              "-Command", lnk_cmd]])

    # 4) HKCU 卸载注册项 + 数据根内卸载脚本（收敛：脚本与数据同目录，自删式清理）
    # 注意：不用 f-string 三引号——bat 里的 ""%~f0"" 会产生 """ 序列提前结束字符串
    uninstall_bat = base / "uninstall.bat"
    uninstall_content = (
        """@echo off
chcp 65001 >nul
rem wechat-claw 卸载脚本（初始化向导生成）
setlocal
set NSSM=%~dp0nssm.exe
"%NSSM%" stop wechat-bridge >nul 2>&1
"%NSSM%" remove wechat-bridge confirm >nul 2>&1
reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" /v "wechat-claw-bridge" /f >nul 2>&1
reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\wechat-claw" /f >nul 2>&1
del "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\wechat-claw\\wechat-claw Web 管理.lnk" /f >nul 2>&1
rmdir "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\wechat-claw" >nul 2>&1
if exist "%~dp0.config\\opencode-installed.json" (
  echo [1/4] opencode 由 wechat-claw 安装：正在一并删除（~\\.opencode\\bin）...
  rmdir /s /q "%USERPROFILE%\\.opencode" >nul 2>&1
) else (
  echo [1/4] 未删除 opencode：它可能由其他程序安装，如需删除请手动处理
)
echo [2/4] 服务 wechat-bridge 已移除
echo [3/4] 卸载注册项与快捷方式已移除
echo [4/4] 数据目录将在 2 秒后自动删除...
echo.
echo wechat-claw 已卸载。
echo 程序本体请手动删除：__DEPLOY_ROOT__
echo 微信登录凭证（如需彻底清除）：%USERPROFILE%\\.wechat-agent-sdk\\accounts.json
"""
        # bat 里的 ""%~dp0"" 转义会形成 """ 序列，不能用三引号字符串承载 → 单引号行拼接
        'start "" /b cmd /c "timeout /t 2 /nobreak >nul & rmdir /s /q ""%~dp0"" & del ""%~f0"""\n'
        "exit /b 0\n"
    ).replace("__DEPLOY_ROOT__", str(DEPLOY_ROOT))
    steps += _write_file(uninstall_bat, uninstall_content)

    key = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\wechat-claw"
    reg_cmds = [
        ["reg", "add", key, "/v", "DisplayName", "/t", "REG_SZ", "/d", "wechat-claw", "/f"],
        ["reg", "add", key, "/v", "DisplayVersion", "/t", "REG_SZ", "/d", VERSION, "/f"],
        ["reg", "add", key, "/v", "Publisher", "/t", "REG_SZ", "/d", "wechat-claw", "/f"],
        ["reg", "add", key, "/v", "InstallLocation", "/t", "REG_SZ", "/d", str(DEPLOY_ROOT), "/f"],
        ["reg", "add", key, "/v", "UninstallString", "/t", "REG_SZ",
         "/d", f'"{uninstall_bat}"', "/f"],
        ["reg", "add", key, "/v", "NoModify", "/t", "REG_DWORD", "/d", "1", "/f"],
        ["reg", "add", key, "/v", "NoRepair", "/t", "REG_DWORD", "/d", "1", "/f"],
    ]
    if exe:
        reg_cmds.insert(4, ["reg", "add", key, "/v", "DisplayIcon", "/t", "REG_SZ",
                            "/d", str(exe), "/f"])
    steps += _exec_commands(reg_cmds)
    return steps


def handle(app, body: dict | None = None) -> dict:
    if os.name == "nt":
        steps = _service_up_windows() + _win_app_entries()
    elif sys.platform == "darwin":
        steps = _launchd() + _macos_app_entry()
    else:
        steps = _systemd() + _linux_desktop_entry()
    ok = all(s["ok"] for s in steps)
    if ok:
        app.steps["service_up"] = True
    # 步骤结果落 web.log（无论成败，可追溯）
    failed = [s for s in steps if not s["ok"]]
    if ok:
        _log_web(f"service_up 完成（{len(steps)} 步全成功）")
    else:
        _log_web(f"service_up 部分失败（{len(failed)}/{len(steps)} 步失败）: "
                 f"{failed[0].get('out', '') if failed else ''}")
    return {"ok": ok, "steps": steps}


def ensure_win_shortcuts() -> None:
    """web 启动即注册 Windows 入口（幂等）：开始菜单快捷方式 + VBS 启动器 + 卸载注册。

    与 service_up 解耦：无论 bridge 是否服务化、向导是否走到「启动服务」步骤，
    用户都有入口（VBS 探测 8650 → 未监听则启动 web → 等就绪 → 开浏览器）。
    失败仅记录日志，不阻塞 web 启动。
    """
    if os.name != "nt" or SELFTEST:
        return
    try:
        steps = _win_app_entries()
        failed = [s for s in steps if not s["ok"]]
        _log_web(f"ensure_win_shortcuts: {'完成' if not failed else f'部分失败 {failed[0].get("out", "")}'}")
    except Exception as e:  # noqa: BLE001
        _log_web(f"ensure_win_shortcuts 异常: {e}")


# ---------- 管理后台「开机自动启动」开关（Windows UAC 提权；Linux/macOS 免提权） ----------

def _bridge_running() -> bool:
    """探测 bridge 是否在运行：push 入口端口（默认 9898）TCP 可连。"""
    try:
        from bridge.config import get as get_cfg
        port = int(get_cfg("push.port", 9898))
        import socket
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except Exception:
        return False


def _platform_service_exists() -> bool:
    """非 Windows 平台守护存在性（systemd/launchd；Linux 实机测试另行讨论，只留接口）。"""
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10)
            return "wechat-bridge" in (r.stdout or "")
        r = subprocess.run(["systemctl", "--user", "list-unit-files", "wechat-bridge.service"],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0 and "wechat-bridge.service" in (r.stdout or "")
    except Exception:
        return False


def autostart_status() -> dict:
    """自启动真实状态：{mode: system|user|none, bridge_running}。"""
    mode = "none"
    if os.name == "nt":
        try:
            r = subprocess.run(["sc", "query", "wechat-bridge"],
                               capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                mode = "system"
        except Exception:
            pass
        if mode == "none":
            try:
                r = subprocess.run(["reg", "query", _RUN_KEY, "/v", _RUN_VALUE],
                                   capture_output=True, text=True, timeout=15)
                if r.returncode == 0:
                    mode = "user"
            except Exception:
                pass
    elif _platform_service_exists():
        mode = "system"
    return {"ok": True, "mode": mode, "bridge_running": _bridge_running()}


def _uac_elevate(on: bool) -> list[dict]:
    """UAC 提权执行 nssm 装/卸服务：runas 跑辅助 bat（弹窗需用户在电脑前点『允许』）。"""
    if os.name != "nt":
        return []
    if SELFTEST:
        return [{"cmd": f"（selftest）将发起 UAC 提权执行 nssm {'install' if on else 'remove'}",
                 "ok": True, "dry": True}]
    bat = DATA_ROOT / f"wechat-claw-autostart-{'on' if on else 'off'}.bat"
    arch = "win64" if sys.maxsize > 2 ** 32 else "win32"
    nssm = RESOURCE_ROOT / "vendor" / "nssm" / f"{arch}.exe"
    py = sys.executable if getattr(sys, "frozen", False) \
        else str(DEPLOY_ROOT / ".venv" / "Scripts" / "python.exe")
    if on:
        lines = [
            f'"{nssm}" install wechat-bridge "{py}" -m bridge.main',
            f'"{nssm}" set wechat-bridge AppDirectory "{DEPLOY_ROOT}"',
            f'"{nssm}" set wechat-bridge AppExit Default Restart',
            f'"{nssm}" set wechat-bridge AppRestartDelay 10000',
            f'"{nssm}" start wechat-bridge',
        ]
    else:
        lines = [
            f'"{nssm}" stop wechat-bridge >nul 2>&1',
            f'"{nssm}" remove wechat-bridge confirm',
        ]
    content = "@echo off\nchcp 65001 >nul\n" + "\n".join(lines) + f"\nexit /b %errorlevel%\n"
    steps = _write_file(bat, content)
    try:
        import ctypes
        h = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "cmd.exe", f'/c ""{bat}""', None, 1)
        if h > 32:
            steps.append({"cmd": "已发起 UAC 提权（请在弹窗点『允许』），稍后刷新状态确认",
                          "ok": True})
        else:
            steps.append({"cmd": f"UAC 未获授权（代码 {h}）：用户取消或拒绝了提权", "ok": False})
    except Exception as e:
        steps.append({"cmd": f"UAC 提权发起失败: {e}", "ok": False})
    return steps


def autostart_set(on: bool) -> dict:
    """开关执行：开启 → 系统服务（非管理员 UAC 提权）；关闭 → 移除服务 + 清用户级自启。

    返回 {ok, steps, mode, uac_required}——uac_required=True 时前端提示用户在弹窗点允许。
    """
    uac_required = False
    if os.name != "nt":
        # Linux/macOS：免提权（systemd/launchd 用户级即可），实机测试另行讨论
        steps = _systemd() if on else _exec_commands(
            [["systemctl", "--user", "disable", "--now", "wechat-bridge"]])
    else:
        if on:
            if _is_admin():
                steps = _nssm()
            else:
                steps = _user_autostart_unreg()  # 升级为服务时先清用户级（互斥）
                steps += _uac_elevate(True)
                uac_required = True
        else:
            steps = _user_autostart_unreg()
            if _is_admin():
                steps += _nssm_remove()
            else:
                steps += _uac_elevate(False)
                uac_required = True
    ok = all(s["ok"] for s in steps)
    failed = [s for s in steps if not s["ok"]]
    _log_web(f"autostart_set({'开启' if on else '关闭'}): {'成功' if ok else '失败'}"
             f"（{'需 UAC' if uac_required else '已提权/免提权'}）"
             + (f"；失败步骤: {failed[0].get('cmd')} -> {failed[0].get('out', '')}" if failed else ""))
    return {"ok": ok, "steps": steps, "mode": autostart_status().get("mode", "none"),
            "uac_required": uac_required}
