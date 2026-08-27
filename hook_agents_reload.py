#!/usr/bin/env python3
"""wechat-claw-dist 模块启停 → AGENTS.md 重载闭环测试。

隔离运行：所有数据在 /tmp/wc-test/ 下，不碰实际环境。
用法：cd <dist根目录> && .venv/bin/python hook_agents_reload.py
"""
import json
import os
import sys
import shutil
from pathlib import Path

# ---- 隔离环境 ----
TEST_DIR = Path("/tmp/wc-test")
if TEST_DIR.exists():
    shutil.rmtree(TEST_DIR)
TEST_DIR.mkdir(parents=True)
os.environ["WC_DATA_ROOT"] = str(TEST_DIR)

DIST = Path(__file__).resolve().parent
sys.path.insert(0, str(DIST / "vendor"))
sys.path.insert(0, str(DIST))

PASS = 0
FAIL = 0


def check(label, ok):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ {label}")


def install_test_module(name, agents_md_content, enabled=True):
    """模拟安装一个模块（agents.md + module.json + settings + token）。"""
    mdir = TEST_DIR / "modules" / name
    ddir = TEST_DIR / "modules" / "modules_data" / name
    mdir.mkdir(parents=True, exist_ok=True)
    ddir.mkdir(parents=True, exist_ok=True)
    (mdir / "agents.md").write_text(agents_md_content)
    (mdir / "module.json").write_text(json.dumps({"name": name, "purpose": f"测试模块{name}"}))
    (ddir / "settings.json").write_text(json.dumps({"enabled": enabled}))
    (mdir / "token").write_text("x" * 64)


def write_signal(entries):
    """模拟 web admin / register CLI 写累积列表信号文件。"""
    sig = TEST_DIR / ".config" / ".agents-reload-requested"
    sig.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if sig.is_file():
        try:
            data = json.loads(sig.read_text())
            if isinstance(data, list):
                existing = data
        except Exception:
            pass
    existing.extend(entries)
    sig.write_text(json.dumps(existing, ensure_ascii=False) + "\n")
    return sig


def simulate_bridge_reload():
    """模拟 bridge _check_agents_reload 的核心逻辑（不跑 bridge，只验证逻辑）。"""
    sig = TEST_DIR / ".config" / ".agents-reload-requested"
    if not sig.is_file():
        return None, None
    entries = json.loads(sig.read_text())
    if not isinstance(entries, list):
        entries = [entries]
    sig.unlink()

    # 清 registry_index 缓存（2s TTL），确保 build_index 重新扫 settings.json
    from modules.registry_index import invalidate
    invalidate()

    from web.agent_gen import write_agents
    path = write_agents()
    text = path.read_text()

    parts = []
    for e in entries:
        emoji = "✅" if e["enabled"] else "🔴"
        action = "已启用" if e["enabled"] else "已关闭"
        parts.append(f"{emoji} {e['module']} 模块{action}")
    tip = "；".join(parts) + "，新功能将在新对话中生效"
    return text, tip


print("=" * 60)
print("测试 1：安装单个模块 → write_agents 生成 AGENTS.md")
print("=" * 60)
install_test_module("todo", "# todo 模块 · agent 维护指引\n\n记任务时写 tasks/月.json\n")
write_signal([{"module": "todo", "enabled": True, "at": "2026-08-25T19:00:00"}])
text, tip = simulate_bridge_reload()
check("AGENTS.md 已生成", text is not None)
check("含模块操作指引段", "## 模块操作指引" in text)
check("todo agents.md 全文已插入", "todo 模块 · agent 维护指引" in text)
check("无未替换占位", "{{" not in text)
check("提示内容正确", tip is not None and "todo" in tip and "已启用" in tip)

print()
print("=" * 60)
print("测试 2：累积列表——一次开两个模块 → 合并成一条提示")
print("=" * 60)
sig = TEST_DIR / ".config" / ".agents-reload-requested"
if sig.exists():
    sig.unlink()
write_signal([{"module": "todo", "enabled": True, "at": "2026-08-25T19:00:00"}])
write_signal([{"module": "Planner", "enabled": True, "at": "2026-08-25T19:00:05"}])
install_test_module("Planner", "# Planner 模块 · agent 维护指引\n\n记纪念日写 countdown.json\n")
text, tip = simulate_bridge_reload()
check("信号含两条记录", "todo" in tip and "Planner" in tip)
check("合并成一条提示", tip.count("新功能将在新对话中生效") == 1)
check("AGENTS.md 含两个模块", "todo 模块" in text and "Planner 模块" in text)
check("信号文件已删", not sig.exists())

print()
print("=" * 60)
print("测试 3：停用模块 → AGENTS.md 不再含该模块")
print("=" * 60)
write_signal([{"module": "todo", "enabled": False, "at": "2026-08-25T19:01:00"}])
install_test_module("todo", "# todo 模块 · agent 维护指引\n", enabled=False)
text, tip = simulate_bridge_reload()
check("提示含停用", "todo" in tip and "已关闭" in tip)
check("AGENTS.md 不再含 todo", "todo 模块 · agent 维护指引" not in text)
check("AGENTS.md 仍含 Planner", "Planner 模块" in text)

print()
print("=" * 60)
print("测试 4：clear_sessions 方法存在于 ConfirmAcpAgent")
print("=" * 60)
from bridge.session import ConfirmAcpAgent
check("clear_sessions 方法存在", hasattr(ConfirmAcpAgent, "clear_sessions"))

print()
print("=" * 60)
print(f"结果：{PASS} 通过，{FAIL} 失败")
print("=" * 60)

shutil.rmtree(TEST_DIR)
sys.exit(1 if FAIL else 0)
