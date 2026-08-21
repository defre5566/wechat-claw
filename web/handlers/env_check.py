"""① 环境体检：python 版本 / opencode 存在 / 磁盘空间 / bridge 端口占用。

- opencode 缺失为引导项（advisory，下一步自动安装），不计入硬性通过条件
- 端口检查 bridge 实际所需：push 入口 + opencode ACP（config.yaml 已生成时取其值）；
  不检查向导自身端口（8650 恒被本进程占用，检查无意义）
"""
from __future__ import annotations

import shutil
import socket
import sys

import yaml

from bridge.config import CONFIG_FILE, DATA_ROOT, DEPLOY_ROOT, get as cfg_get
from web.handlers.opencode_setup import detect_installed


def _python_version() -> dict:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 11)
    return {"ok": ok, "value": f"{v.major}.{v.minor}.{v.micro}"}


def _opencode_version() -> dict:
    d = detect_installed()
    return {"ok": bool(d), "value": d["version"] if d else "未安装"}


def _disk_space() -> dict:
    try:
        # 数据根可能尚未创建（首次运行）→ 回退部署根
        probe = DATA_ROOT if DATA_ROOT.is_dir() else DEPLOY_ROOT
        usage = shutil.disk_usage(probe)
        free_gb = usage.free / (1024 ** 3)
        return {"ok": free_gb >= 1, "value": f"{free_gb:.1f} GB 剩余"}
    except Exception:
        return {"ok": True, "value": "未知"}


def _bridge_ports() -> list[int]:
    """bridge 运行所需端口：push 入口 + opencode ACP（config.yaml 已生成时取其值）。"""
    ports = [int(cfg_get("push.port", 9898)), int(cfg_get("acp.port", 45678))]
    # config.yaml 未生成时 get 返回内置默认；文件存在但解析失败则用默认
    try:
        if CONFIG_FILE.is_file():
            cfg = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
            ports = [int((cfg.get("push") or {}).get("port", 9898)),
                     int((cfg.get("acp") or {}).get("port", 45678))]
    except Exception:
        pass
    return sorted(set(ports))


def _port_free() -> dict:
    ports = _bridge_ports()
    busy = []
    for p in ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
        except OSError:
            busy.append(p)
        finally:
            s.close()
    label = "/".join(map(str, ports))
    if busy:
        return {"ok": False, "value": f"{label}：{'、'.join(map(str, busy))} 被占用"}
    return {"ok": True, "value": f"{label} 空闲"}


def handle(app, body: dict | None = None) -> dict:
    items = [
        {"key": "python", "name": "Python 版本", **_python_version()},
        {"key": "opencode", "name": "opencode", "advisory": True, **_opencode_version()},
        {"key": "disk", "name": "磁盘空间", **_disk_space()},
        {"key": "port", "name": "bridge 端口（push/ACP）", **_port_free()},
    ]
    # opencode 缺失属引导项（下一步自动安装），不计入硬性通过条件
    app.steps["env_check"] = all(i["ok"] for i in items if not i.get("advisory"))
    return {"ok": True, "items": items, "passed": app.steps["env_check"]}
