"""AGENTS.md 生成器：读 .config/agent/ 字段 → 套锁定模板 → 覆盖数据根 AGENTS.md。

- 字段真源：.config/agent/identity.json（role/language/address/assistant_name）
  + .config/agent/rules.json（行为守则列表），均带 .prev 撤销（经 common._userdata）
- 未填字段使用中立默认值（= AGENTS.md 默认产物的人设）
- 锁定段（系统自述/安全红线/其余部分）在模板中写死，生成时原样输出
- 消费方：初始化向导第④步、管理后台「助理人设」保存
"""
from __future__ import annotations

from pathlib import Path

from bridge.config import DATA_ROOT, RESOURCE_ROOT
from modules.common import _userdata

TEMPLATE = RESOURCE_ROOT / "web" / "templates" / "AGENTS.tmpl"
OUTPUT = DATA_ROOT / "AGENTS.md"

# 中立默认人设（= 默认产物 AGENTS.md 的字段值，未填字段时使用）
DEFAULTS: dict = {
    "role": "你是用户部署的个人数字助理，服务对象是用户",
    "language": "态度中立、可靠、不过度亲昵也不生硬：像专业助理，如实汇报，不迎合不表演",
    "address": "用户",
    "assistant_name": "小助手",
}
DEFAULT_RULES: list[str] = [
    "先结论后细节，密度优先，不灌水",
    "口语自然、不端不客套；不确定就明说，不糊弄",
    "复杂任务分步确认；高危操作先说明再做",
    "不过度打扰、不刷屏；尊重选择，建议给到不强推",
    "拿不准意图先问，不猜",
]


# ---------- 字段读写（web 侧调用，含 prev 撤销） ----------

def get_identity() -> dict:
    """读身份字段（含默认值兜底）。"""
    data = _userdata.load("agent/identity", {}) or {}
    fields = {k: data.get(k, v) for k, v in DEFAULTS.items()}
    if not str(fields["assistant_name"]).strip():
        fields["assistant_name"] = DEFAULTS["assistant_name"]
    # Default name is not the same as a name the user explicitly chose.
    fields["assistant_name_customized"] = bool(
        data.get("assistant_name_customized", data.get("assistant_name", "") not in ("", DEFAULTS["assistant_name"]))
    )
    return fields


def set_identity(identity: dict) -> bool:
    """写身份字段（自动备份 prev）。"""
    current = _userdata.load("agent/identity", {}) or {}
    data = {k: identity.get(k, v) for k, v in DEFAULTS.items()}
    previous_customized = current.get("assistant_name_customized")
    if previous_customized is None:
        previous_customized = current.get("assistant_name", "") not in ("", DEFAULTS["assistant_name"])
    data["assistant_name_customized"] = bool(
        identity.get("assistant_name_customized", previous_customized)
    )
    return _userdata.save("agent/identity", data)


def undo_identity() -> dict:
    """撤销身份字段上次修改。"""
    prev = _userdata.undo("agent/identity", None)
    if prev is None:
        return get_identity()
    return prev if isinstance(prev, dict) else get_identity()


def get_rules() -> list[str]:
    """读行为守则（空则返回中立默认）。"""
    data = _userdata.load("agent/rules", [])
    return data if isinstance(data, list) and data else DEFAULT_RULES


def set_rules(rules: list[str]) -> bool:
    """写行为守则（自动备份 prev）。"""
    return _userdata.save("agent/rules", list(rules))


def undo_rules() -> list[str]:
    """撤销行为守则上次修改。"""
    prev = _userdata.undo("agent/rules", None)
    if prev is None:
        return get_rules()
    return prev if isinstance(prev, list) else get_rules()


# ---------- 生成 ----------

def render(fields: dict) -> str:
    """套模板：字段替换 {{...}}；rules 列表逐条渲染。"""
    tpl = TEMPLATE.read_text(encoding="utf-8")
    text = tpl
    for key, value in fields.items():
        if key == "rules":
            rendered = "\n".join(f"- {r}" for r in value)
        else:
            rendered = str(value)
        text = text.replace("{{" + key + "}}", rendered)
    return text


def write_agents() -> Path:
    """读字段 → 渲染 → 覆盖项目根 AGENTS.md；返回输出路径。"""
    fields = get_identity()
    fields["rules"] = get_rules()
    OUTPUT.write_text(render(fields), encoding="utf-8")
    return OUTPUT
