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
import re
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime
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
# 每档可承载条目预算（260829 P1：从 5×60 字放宽——部署实测朴素输入会丢失大量
# 语气细节；tier-current 装载有阶梯机制挡着，冷启动 token 仍远小于旧 AGENTS.md）
TIER_BUDGET = [1, 2, 4, 5, 8]
TIER_LINE_MAX = 80  # 单条字数上限
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


# ---------- tier 分档生成（web 两框保存 → opencode run 输出协议） ----------

TIER_PROMPT = """你是 wechat-claw 助理人设编辑器。任务：把用户的朴素输入整理为助理人设的分档规范内容。

【用户输入】
称呼要求：{address}
助理名字：{assistant_name}
角色定位：{role}
语言习惯：{language}
行为守则（逐条）：
{rules}

【输出方式】禁止使用任何工具，禁止读取、创建或修改任何文件。只在最终回答中输出五个分档内容。
严格使用以下分隔格式，不要输出解释、标题、Markdown 代码块或其他文字：

===TIER0===
第1条
===END_TIER0===

===TIER1===
第1条
第2条
===END_TIER1===

===TIER2===
第1条
第2条
第3条
第4条
===END_TIER2===

===TIER3===
第1条
第2条
第3条
第4条
第5条
===END_TIER3===

===TIER4===
第1条
第2条
第3条
第4条
第5条
第6条
第7条
第8条
===END_TIER4===

【分档逻辑】所有档共用同一条目序列的第 1~N 条（预算：1/2/4/5/8 条）：
先产出一条完整的优先级序列（共 8 条），从最不可少的排到最锦上添花的：
第 1 条 = 身份核心（谁是谁、如何称呼对方）；其后依次是基础语气、
常用交互风格、更细的表达偏好、附加加分项。
然后 tierN = 该序列前预算条数的原样拷贝，一行一条，不加序号。

【条目规范】
- 每条一句紧凑规范句，≤80 字，直接可作系统提示使用，不解释理由
- 文件内不得出现标题、空行、markdown 符号或 JSON 结构
- 逐条覆盖输入要点，不得丢弃原文语义要素（语气比喻、风格限定词必须保留）
- 允许把相近要点整合为一句，但整合后总信息量不得少于原文
- 只允许重组用户输入的信息，禁止虚构新的性格细节、经历或能力承诺
- 输入信息不足以撑满某档时，可用中性的通用措辞补位（如"回应务实简短"），不得编造

【自查】输出前逐一核对五个区块的条数是否为 1/2/4/5/8，并核对高档包含低档的全部前缀。
"""

TIER_RUN_TIMEOUT = 120  # 与 admin.optimize_persona 同口径


def _validate_tiers(d: Path) -> bool:
    """硬校验：tier0~4 存在且非空行数 = 预算条数（bridge 侧兜底，prompt 自查仅为软约束）。"""
    for i, fname in enumerate(TIER_FILES):
        f = d / fname
        if not f.is_file():
            return False
        try:
            lines = [ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            return False
        if len(lines) != TIER_BUDGET[i]:
            return False
    return True


def _parse_tier_output(raw: str) -> dict[str, list[str]] | None:
    """解析模型 stdout 的五档协议，拒绝缺档、脏文本、越长条目与非前缀分档。"""
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw or "")
    result: dict[str, list[str]] = {}
    cursor = 0
    for i in range(5):
        marker = re.match(
            rf"\s*===TIER{i}===\s*(.*?)\s*===END_TIER{i}===",
            clean[cursor:],
            flags=re.DOTALL,
        )
        if marker is None:
            return None
        lines = [line.strip() for line in marker.group(1).splitlines() if line.strip()]
        if len(lines) != TIER_BUDGET[i] or any(len(line) > TIER_LINE_MAX for line in lines):
            return None
        if any(line.startswith(("#", "-", "*", "```")) for line in lines):
            return None
        result[f"tier{i}"] = lines
        cursor += marker.end()
    if clean[cursor:].strip():
        return None
    for i in range(1, 5):
        if result[f"tier{i}"][:TIER_BUDGET[i - 1]] != result[f"tier{i - 1}"]:
            return None
    return result


def _extract_run_text(raw: str) -> str | None:
    """从 opencode --format json 的 JSONL 流提取完整回答。

    只收集 type=text 的 part.text，并要求出现 step_finish；CLI 状态行、工具输出和
    未完成流不进入 tier 协议解析。
    """
    parts: list[str] = []
    finished = False
    for line in (raw or "").splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "text":
            part = event.get("part")
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        elif event.get("type") == "step_finish":
            finished = True
    text = "".join(parts).strip()
    return text if finished and text else None


def _write_web_log(message: str) -> None:
    """把 tier 任务状态写入数据根 web.log，避免 stderr 被重定向后丢失。"""
    try:
        path = DATA_ROOT / "logs" / "web.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [tiers] {message}\n")
    except OSError:
        pass


def _snapshot_tiers() -> dict[str, bytes | None]:
    """记录正式 tier 六文件，防外部 opencode 进程越权改写真实目录。"""
    snapshot: dict[str, bytes | None] = {}
    for fname in TIER_FILES + [CURRENT_TIER]:
        path = INSTRUCTIONS_DIR / fname
        try:
            snapshot[fname] = path.read_bytes() if path.is_file() else None
        except OSError:
            snapshot[fname] = None
    return snapshot


def _restore_unexpected_tier_writes(snapshot: dict[str, bytes | None]) -> bool:
    """恢复 run 期间对正式 tier 的非预期修改；返回是否发现过越权写。"""
    changed = False
    INSTRUCTIONS_DIR.mkdir(parents=True, exist_ok=True)
    for fname, old in snapshot.items():
        path = INSTRUCTIONS_DIR / fname
        try:
            current = path.read_bytes() if path.is_file() else None
            if current == old:
                continue
            changed = True
            if old is None:
                path.unlink(missing_ok=True)
            else:
                tmp = path.with_suffix(path.suffix + ".restore.tmp")
                tmp.write_bytes(old)
                os.replace(tmp, path)
        except OSError as e:
            log.error("[tiers] 恢复越权写失败: %s (%s)", path, e)
    return changed


def _atomic_put(src: Path, target: Path) -> None:
    """copyfile 到目标同目录临时文件后同盘 os.replace——
    staging（tmpfs/%TEMP%）与数据根（磁盘）可能跨设备，直接 os.replace 必报 EXDEV
    （部署机实测 [Errno 18]）；同盘 tmp+replace 全平台安全。"""
    tmp = target.with_suffix(target.suffix + ".commit.tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, target)


def _commit_tiers(staging: Path, payload: dict[str, list[str]]) -> None:
    """事务提交 tier0~4 与 tier-current；中途失败恢复提交前的六个文件。

    提交与回滚均经 _atomic_put（同盘 tmp+replace），staging 与数据根跨设备不炸。
    """
    INSTRUCTIONS_DIR.mkdir(parents=True, exist_ok=True)
    staged = staging / "instructions"
    backup = staging / "backup"
    backup.mkdir()
    targets = TIER_FILES + [CURRENT_TIER]
    existed: set[str] = set()
    for fname in targets:
        target = INSTRUCTIONS_DIR / fname
        if target.is_file():
            shutil.copyfile(target, backup / fname)
            existed.add(fname)
    for fname in TIER_FILES:
        (staged / fname).write_text("\n".join(payload[fname[:-3]]) + "\n", encoding="utf-8")
    (staged / CURRENT_TIER).write_text(
        (staged / DEFAULT_TIER).read_text(encoding="utf-8"), encoding="utf-8"
    )
    try:
        for fname in targets:
            _atomic_put(staged / fname, INSTRUCTIONS_DIR / fname)
    except Exception:
        for fname in targets:
            target = INSTRUCTIONS_DIR / fname
            old = backup / fname
            try:
                if fname in existed:
                    _atomic_put(old, target)
                elif target.exists():
                    target.unlink()
            except OSError:
                log.error("[tiers] 回滚失败: %s", target)
        raise


def regenerate_tiers(identity: dict | None = None, rules: list[str] | None = None) -> bool:
    """同步执行：opencode run 输出协议 → Python 校验/写 staging → 事务提交。

    模型不拥有文件工具；正式文件只由本进程在协议校验通过后写入。
    """
    from bridge.config import WORK_ROOT, get as get_cfg, resolve_opencode, xdg_env, no_window_flags

    ident = identity if identity is not None else get_identity()
    rule_list = rules if rules is not None else get_rules()
    binary = resolve_opencode()
    if not binary:
        log.warning("[tiers] 未找到 opencode 可执行文件，跳过 tier 重建")
        _write_web_log("未找到 opencode，可执行文件未生成")
        return False
    _write_web_log("开始生成")
    prompt = TIER_PROMPT.format(
        address=ident.get("address", ""),
        assistant_name=ident.get("assistant_name", ""),
        role=ident.get("role", ""),
        language=ident.get("language", ""),
        rules="\n".join(f"- {r}" for r in rule_list),
    )
    staging = Path(tempfile.mkdtemp(prefix="wc-tiers-"))
    before_run = _snapshot_tiers()
    try:
        # 模型：config acp.model > 省略（让 opencode 使用部署配置默认模型）。模型只输出协议文本，
        # 不获得文件写入任务；正式文件只由 Python 在后续事务中写入。
        model = str(get_cfg("acp.model") or "").strip()
        env = {**os.environ, **xdg_env()}
        try:
            argv = [str(binary), "run", "--agent", "plan", "--pure", "--format", "json"]
            if model:
                argv += ["-m", model]
            argv.append(prompt)
            r = subprocess.run(
                argv,
                capture_output=True, text=True, timeout=TIER_RUN_TIMEOUT,
                # cwd 只用于让 opencode 读取现有部署配置；模型没有正式文件写入职责。
                cwd=str(WORK_ROOT), env=env, creationflags=no_window_flags(),
            )
        except subprocess.TimeoutExpired:
            log.warning("[tiers] tier 生成超时（%ds），保留原文件", TIER_RUN_TIMEOUT)
            _write_web_log(f"生成超时（{TIER_RUN_TIMEOUT}s），保留原文件")
            return False
        except OSError as e:
            log.warning("[tiers] tier 生成进程异常: %s", e)
            _write_web_log(f"生成进程异常：{e}，保留原文件")
            return False
        if _restore_unexpected_tier_writes(before_run):
            log.warning("[tiers] 检测到 opencode 越权改写正式 tier，已恢复；本次继续仅使用 stdout")
            _write_web_log("检测到 opencode 越权改写正式 tier，已恢复；仅使用 stdout 产物")
        if r.returncode != 0:
            message = f"opencode 返回码 {r.returncode}；run 输出尾: {(r.stdout or r.stderr or '')[-120:].strip()}"
            log.warning("[tiers] %s，保留原文件", message)
            _write_web_log(message)
            return False
        run_text = _extract_run_text(r.stdout or "")
        payload = _parse_tier_output(run_text or "")
        if payload is None:
            message = f"JSONL/tier 协议校验失败；run 输出尾: {(r.stdout or r.stderr or '')[-120:].strip()}"
            log.warning("[tiers] %s，保留原文件", message)
            _write_web_log(message)
            return False
        staged = staging / "instructions"
        staged.mkdir()
        for fname in TIER_FILES:
            (staged / fname).write_text("\n".join(payload[fname[:-3]]) + "\n", encoding="utf-8")
        if not _validate_tiers(staged):
            log.warning("[tiers] Python 产物校验失败，保留原文件")
            _write_web_log("Python 产物校验失败，保留原文件")
            return False
        _commit_tiers(staging, payload)
        log.info("[tiers] 五档 tier 已更新（model=%s）", model)
        _write_web_log(f"生成成功，model={model or 'opencode-default'}")
        return True
    except Exception as e:
        log.warning("[tiers] 提交失败，已尝试回滚：%s", e)
        _write_web_log(f"提交失败，已尝试回滚：{e}")
        return False
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
