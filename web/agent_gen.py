"""人设与索引生成器：identity/rules 字段读写 + instructions 目录维护。

- 字段真源：.config/agent/identity.json（role/language/address/assistant_name）
  + .config/agent/rules.json（行为守则列表），均带 .prev 撤销（经 common._userdata）
- 未填字段使用中立默认值（= 出厂 tier 基线的人设）
- 消费方：初始化向导第④步、管理后台「助理人设」保存、模块启停 reload

260827 全索引化改造（N1 决策 + 议题1 定案）：
- 部署态不再生成 AGENTS.md（agent 全部指引经 opencode.jsonc instructions 装载）
- ensure_builtins()：首启把出厂 tier0~4 基线复制到数据根（不覆盖已有定制），
  兜底创建 tier-current.md（当前档位文件，索引器按画像刷新），初始化 index 目录
- index 位置表：instructions/index/<module>.json（关键词→内容文件位置），
  生命周期归 register（启用放置/关闭 .off/卸载移除），worker 可写本模块条目；
  bridge 硬索引消费（路由不经模型）
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from bridge.config import DATA_ROOT
from modules.common import _userdata

log = logging.getLogger("wechat-agent-gen")

TEMPLATES = Path(__file__).resolve().parent / "templates"
BASELINE_DIR = TEMPLATES / "instructions"
INSTRUCTIONS_DIR = DATA_ROOT / "instructions"
# 指令索引位置表（260827 议题1 定案）：条目 = 关键词 → 内容文件位置；
# register 管生命周期（启用放置/关闭 .off/卸载移除），worker 可写本模块文件
INDEX_DIR = INSTRUCTIONS_DIR / "index"

# 出厂 tier 基线文件（tier0~4，装载条数 = 级别 + 1）
TIER_FILES = [f"tier{i}.md" for i in range(5)]
# 当前档位兜底：无画像时 L0 起步（260827 第七章定案）
CURRENT_TIER = "tier-current.md"
DEFAULT_TIER = "tier0.md"

# 中立默认人设（= 出厂 tier 基线的字段值，未填字段时使用）
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


# ---------- instructions 目录维护 ----------

def ensure_builtins() -> Path:
    """首启/向导/重载兜底调用：出厂 tier 基线复制到数据根（已存在不覆盖，护住用户定制）；
    tier-current.md 缺失时按默认档兜底创建；初始化 index 位置表目录。
    返回 instructions 目录。"""
    INSTRUCTIONS_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    for fname in TIER_FILES:
        dst = INSTRUCTIONS_DIR / fname
        if dst.is_file():
            continue
        src = BASELINE_DIR / fname
        if src.is_file():
            shutil.copyfile(src, dst)
    cur = INSTRUCTIONS_DIR / CURRENT_TIER
    if not cur.is_file():
        default = INSTRUCTIONS_DIR / DEFAULT_TIER
        if default.is_file():
            shutil.copyfile(default, cur)
    return INSTRUCTIONS_DIR


# ---------- tier 分档生成（web 两框保存 → opencode run 写层级文件） ----------

TIER_PROMPT = """你是 wechat-claw 助理人设编辑器。任务：把用户的朴素输入整理为助理人设的分档规范文件。

【用户输入】
称呼要求：{address}
助理名字：{assistant_name}
角色定位：{role}
语言习惯：{language}
行为守则（逐条）：
{rules}

【写入位置】当前工作目录下的 instructions/ 目录，覆盖以下五个文件：
tier0.md（1 条）、tier1.md（2 条）、tier2.md（3 条）、tier3.md（4 条）、tier4.md（5 条）

【分档逻辑】所有档共用同一条目序列的第 1~N 条：
先产出一条完整的优先级序列（共 5 条），从最不可少的排到最锦上添花的：
第 1 条 = 身份核心（谁是谁、如何称呼对方）；其后依次是基础语气、
常用交互风格、更细的表达偏好、附加加分项。
然后 tierN.md = 该序列前 N+1 条的原样拷贝，一行一条，不加序号。

【条目规范】
- 每条一句紧凑规范句，≤60 字，直接可作系统提示使用，不解释理由
- 文件内不得出现标题、空行、markdown 符号或 JSON 结构
- 只允许重组用户输入的信息，禁止虚构新的性格细节、经历或能力承诺
- 输入信息不足以撑满某档时，可用中性的通用措辞补位（如"回应务实简短"），不得编造

【自查】写完五个文件后逐一核对行数是否等于 1/2/3/4/5，不符则修正后收工，
最后仅回复 "OK"。
"""

TIER_RUN_TIMEOUT = 120  # 与 admin.optimize_persona 同口径
STAGING_MODEL_MIN_JSONC = (
    '{{\n  "model": "{model}",\n'
    '  "permission": {{ "read": {{ "**": "allow" }}, "edit": {{ "**": "allow" }} }}\n}}\n'
)


def _validate_tiers(d: Path) -> bool:
    """硬校验：tier0~4 存在且非空行数 = 级别 + 1（bridge 侧兜底，prompt 自查仅为软约束）。"""
    for i, fname in enumerate(TIER_FILES):
        f = d / fname
        if not f.is_file():
            return False
        try:
            lines = [ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            return False
        if len(lines) != i + 1:
            return False
    return True


def regenerate_tiers(identity: dict | None = None, rules: list[str] | None = None) -> bool:
    """同步执行：opencode run 在 staging 目录生成五档 → 硬校验 → 原子替换正式文件。

    staging 隔离：模型写临时目录，校验不过则用户现有 tier 文件原样保留；
    通过后逐文件 os.replace 提交，并把 tier-current 刷新为默认档内容。
    """
    from bridge.config import WORK_ROOT, get as get_cfg, resolve_opencode, xdg_env, no_window_flags

    ident = identity if identity is not None else get_identity()
    rule_list = rules if rules is not None else get_rules()
    binary = resolve_opencode()
    if not binary:
        log.warning("[tiers] 未找到 opencode 可执行文件，跳过 tier 重建")
        return False
    prompt = TIER_PROMPT.format(
        address=ident.get("address", ""),
        assistant_name=ident.get("assistant_name", ""),
        role=ident.get("role", ""),
        language=ident.get("language", ""),
        rules="\n".join(f"- {r}" for r in rule_list),
    )
    staging = Path(tempfile.mkdtemp(prefix="wc-tiers-"))
    try:
        (staging / "instructions").mkdir()
        # 模型：config acp.model > 省略（staging jsonc 不写 model 键，继承 opencode
        # 默认解析链）；不落任何 fallback 常量（deepseek 教训：无凭据环境静默失败）
        model = str(get_cfg("acp.model") or "").strip()
        if model:
            (staging / "opencode.jsonc").write_text(
                STAGING_MODEL_MIN_JSONC.format(model=model), encoding="utf-8"
            )
        else:
            (staging / "opencode.jsonc").write_text(
                '{\n  "permission": { "read": { "**": "allow" }, "edit": { "**": "allow" } }\n}\n',
                encoding="utf-8",
            )
        env = {**os.environ, **xdg_env()}
        try:
            argv = [str(binary), "run"]
            if model:
                argv += ["-m", model]
            argv.append(prompt)
            r = subprocess.run(
                argv,
                capture_output=True, text=True, timeout=TIER_RUN_TIMEOUT,
                cwd=str(staging), env=env, creationflags=no_window_flags(),
            )
        except subprocess.TimeoutExpired:
            log.warning("[tiers] tier 生成超时（%ds），保留原文件", TIER_RUN_TIMEOUT)
            return False
        except OSError as e:
            log.warning("[tiers] tier 生成进程异常: %s", e)
            return False
        out_dir = staging / "instructions"
        if not _validate_tiers(out_dir):
            log.warning(
                "[tiers] 产物校验未过（%s），保留原文件；run 输出尾: %s",
                [f.name for f in out_dir.iterdir()] if out_dir.is_dir() else "无产物",
                (r.stdout or r.stderr or "")[-120:].strip(),
            )
            return False
        INSTRUCTIONS_DIR.mkdir(parents=True, exist_ok=True)
        for fname in TIER_FILES:
            os.replace(out_dir / fname, INSTRUCTIONS_DIR / fname)
        # 当前档位文件随新基线刷新（一期无画像，恒为默认档）
        cur = INSTRUCTIONS_DIR / CURRENT_TIER
        tmp = cur.with_suffix(".md.tmp")
        shutil.copyfile(INSTRUCTIONS_DIR / DEFAULT_TIER, tmp)
        os.replace(tmp, cur)
        log.info("[tiers] 五档 tier 已更新（model=%s）", model)
        return True
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def regenerate_tiers_async() -> None:
    """后台线程触发 tier 重建（web 保存人设后调用，不阻塞请求）。"""
    threading.Thread(target=regenerate_tiers, daemon=True, name="tier-regen").start()


# ---------- index 位置表机制（260827 议题1：关键词 → 内容文件位置） ----------

def validate_index(data) -> bool:
    """index json 校验：{module: str, entries: [{kw 非空字符串列表, file 非空串, title}]}。"""
    if not isinstance(data, dict):
        return False
    entries = data.get("entries")
    if not isinstance(entries, list):
        return False
    for e in entries:
        if not isinstance(e, dict):
            return False
        kw = e.get("kw")
        if not isinstance(kw, list) or not kw:
            return False
        if not all(isinstance(k, str) and k.strip() for k in kw):
            return False
        if not isinstance(e.get("file"), str) or not e["file"].strip():
            return False
    return True


def place_index(name: str) -> Path:
    """启用模块时放置其索引文件（register 钩子，Ⅱ-3 调用）。

    优先级：恢复 <name>.json.off（保留的用户/worker 态）> 已存在即保留 > 复制
    模块种子 index.json > 生成最小骨架（单条目指向 agents.md）。
    返回现役文件路径。
    """
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    dst = INDEX_DIR / f"{name}.json"
    off = INDEX_DIR / f"{name}.json.off"
    if off.is_file():
        off.replace(dst)
        return dst
    if dst.is_file():
        return dst
    seed = DATA_ROOT / "modules" / name / "index.json"
    if seed.is_file():
        try:
            data = json.loads(seed.read_text(encoding="utf-8"))
            if validate_index(data):
                shutil.copyfile(seed, dst)
                return dst
            log.warning("[index] 模块 %s 种子 index.json 格式不合格，改用骨架", name)
        except (OSError, ValueError) as e:
            log.warning("[index] 模块 %s 种子读取失败（%s），改用骨架", name, e)
    skeleton = {
        "module": name,
        "entries": [{"kw": [name], "file": f"modules/{name}/agents.md", "title": name}],
    }
    dst.write_text(json.dumps(skeleton, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dst
