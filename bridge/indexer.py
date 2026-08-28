"""indexer：全索引化架构的调度中枢（v0 观测期）。

三步流水线（260827 决策 DESIGN-DECISION-INDEXER）：
  ① 量级判档 —— tier0~4 深度分档，判据「几句话能说清」（句数为主、长度为辅）
  ② 模块功能路由 —— 观测现有 inbound 订阅命中（触发词接口为挂起议题，不自造匹配）
  ③ 条数选取 + 合并装配 —— 装配单（v0 仅落观测文件）

v0 过渡期（b 方案）：全走主链路，不改任何执行路径；每次入站消息落一行
jsonl 观测（logs/indexer.jsonl），作为二期分流接线与回锅接口设计的实证数据。
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time

from .config import WORK_ROOT, no_window_flags

log = logging.getLogger("wechat-bridge")

OBSERVE_FILE = WORK_ROOT / "logs" / "indexer.jsonl"
# 单条观测记录的文本截取上限（分析够用，控体积）
_TEXT_PREVIEW = 120

_SENT_SPLIT = re.compile(r"[。？！?!；;\n]")


def judge_tier(text: str) -> int:
    """① 量级判档：返回 tier 0~4。

    判据（鑫定案 A 档）：一句话能办成 → 薄；几句话说不清 → 厚。
    句数为主档、长度微调：tier0=短句(≤12字) / tier1=单句或双短句 /
    tier2=≤3句 / tier3=≤6句 / tier4=更多。
    """
    t = (text or "").strip()
    if not t:
        return 0
    sents = max(1, len([s for s in _SENT_SPLIT.split(t) if s.strip()]))
    n = len(t)
    if sents <= 1:
        return 0 if n <= 12 else 1
    if sents <= 3:
        return 2
    if sents <= 6:
        return 3
    return 4


def build_assembly(text: str, modules_hit: list[str] | None = None,
                   routed: bool = False,
                   conversation_id: str | None = None) -> dict:
    """②③ 生成装配单（v0：tier 选取 + 模块命中 + 主链路标记，不实际装载）。"""
    return {
        "ts": round(time.time(), 3),
        "conv": conversation_id or "",
        "tier": judge_tier(text),
        "routed": bool(routed),
        "modules": list(modules_hit or []),
        "mode": "main",  # v0 固定主链路；二期此处出现轻量档分流
        "text_preview": (text or "").strip()[:_TEXT_PREVIEW],
    }


def observe(text: str, modules_hit: list[str] | None = None,
            routed: bool = False, conversation_id: str | None = None) -> None:
    """落一行观测 jsonl；任何异常吞掉并告警，绝不影响消息主流程。"""
    try:
        record = build_assembly(text, modules_hit, routed, conversation_id)
        OBSERVE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with OBSERVE_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        log.info("[indexer] tier=%s routed=%s modules=%s",
                 record["tier"], record["routed"], record["modules"])
    except Exception as e:  # noqa: BLE001 观测失败不阻塞消息
        log.warning("[indexer] 观测落盘失败: %s", e)


# ---------- 会话画像与冷启动装配（乙为主）+ 档位阶梯（甲为辅，Ⅲ-2） ----------

_PROFILE_WINDOW = 20          # 画像取样：该 conv 最近 N 条观测
_PROFILE_DEFAULT_TIER = 0     # 无画像（全新会话）L0 起步（260827 第七章定案 a 方案）

_DEPTH_STEP_MSGS = 6          # 升档量尺：会话内每 N 条消息……
_DEPTH_STEP_CHARS = 600       # ……或每 N 字（任一达标升一档），tier4 封顶
_DEPTH: dict[str, dict] = {}  # conv → {"msgs", "chars", "cur"}（内存态，归档/重载清零）


def _profile_tier(conversation_id: str | None) -> int:
    """从观测 jsonl 现算会话画像档位（最近 N 条 tier 均值四舍五入）。

    单一真源 = indexer.jsonl，不另设画像文件；无记录回默认档。
    """
    if not conversation_id or not OBSERVE_FILE.is_file():
        return _PROFILE_DEFAULT_TIER
    tiers: list[int] = []
    try:
        lines = OBSERVE_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return _PROFILE_DEFAULT_TIER
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("conv") != conversation_id:
            continue
        try:
            tiers.append(int(rec.get("tier", _PROFILE_DEFAULT_TIER)))
        except (TypeError, ValueError):
            continue
        if len(tiers) >= _PROFILE_WINDOW:
            break
    if not tiers:
        return _PROFILE_DEFAULT_TIER
    return min(4, max(0, round(sum(tiers) / len(tiers))))


def refresh_current_tier(conversation_id: str | None = None) -> int:
    """冷启动装配：按会话画像把对应 tierN 内容写入 tier-current.md。

    调用时机 = 会话冷启动前后（5h 归档 / 模块重载清 session）；同时清该会话的
    深度计数（阶梯随新会话重新起步）。装配失败吞异常保留旧内容
    （部署 jsonc 指向的文件永远存在）。返回实际写入的档位。
    """
    from web.agent_gen import CURRENT_TIER, INSTRUCTIONS_DIR

    _DEPTH.pop(conversation_id, None)  # 会话重开 → 阶梯归零
    tier = _profile_tier(conversation_id)
    try:
        src = INSTRUCTIONS_DIR / f"tier{tier}.md"
        dst = INSTRUCTIONS_DIR / CURRENT_TIER
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".md.tmp")
        tmp.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        tmp.replace(dst)
        log.info("[indexer] tier-current 已装配（conv=%s tier=%s）", conversation_id, tier)
    except Exception as e:  # noqa: BLE001 装配失败不阻塞会话
        log.warning("[indexer] tier-current 装配失败（保留旧内容）: %s", e)
    return tier


def _tier_delta(a: int, b: int) -> list[str]:
    """tier b 相对 tier a 的增量行（前缀截断关系保证 = rb[a+1:b+1]）。"""
    from web.agent_gen import INSTRUCTIONS_DIR

    try:
        rb = [ln for ln in (INSTRUCTIONS_DIR / f"tier{b}.md").read_text(
            encoding="utf-8").splitlines() if ln.strip()]
        if b <= a:
            return []
        return rb[(a + 1):(b + 1)]
    except OSError:
        return []


def tier_increment(conversation_id: str, text: str) -> str:
    """深度计数更新 + 跨阈值时返回人设增量块（甲路径：user message 前缀注入）。

    量尺：会话内每 6 条消息或每 600 字升一档（任一达标，取大者），tier4 封顶；
    起点 = 冷启动档（首次进入时定格 base，画像后变不漂移）。无升档返回空串。
    """
    t = text or ""
    d = _DEPTH.get(conversation_id)
    if d is None:
        base = _profile_tier(conversation_id)
        d = {"msgs": 0, "chars": 0, "base": base, "cur": base}
        _DEPTH[conversation_id] = d
    d["msgs"] += 1
    d["chars"] += len(t)
    steps = min(4, max(d["msgs"] // _DEPTH_STEP_MSGS, d["chars"] // _DEPTH_STEP_CHARS))
    target = min(4, d["base"] + steps)
    if target <= d["cur"]:
        return ""
    delta: list[str] = []
    for ti in range(d["cur"] + 1, target + 1):
        delta.extend(_tier_delta(ti - 1, ti))
    d["cur"] = target
    if not delta:
        return ""
    log.info("[indexer] 档位阶梯: conv=%s 升至 tier%s", conversation_id, target)
    return "\n\n[人设补充]\n" + "\n".join(delta) + "\n"


# ---------- 硬索引：位置表匹配与材料拼接（260827 议题1 / Ⅱ-4） ----------

_INDEX_MATERIAL_MAX = 2048      # 单条材料上限（字符）
_INDEX_MATERIAL_TOTAL = 4096    # 单次拼接总上限


def load_index_entries() -> list[dict]:
    """读全部现役索引条目（.off 天然不在 glob 内；坏文件跳过、死链过滤+告警）。

    返回 [{"module", "file", "title", "kw"}]；match_index 与 fuzzy_match 共用。
    """
    from web.agent_gen import INDEX_DIR

    entries: list[dict] = []
    if not INDEX_DIR.is_dir():
        return entries
    for f in sorted(INDEX_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("[index] 索引文件解析失败，跳过: %s", f.name)
            continue
        module = str(data.get("module") or f.stem)
        for e in data.get("entries", []):
            if not isinstance(e, dict):
                continue
            file = str(e.get("file") or "")
            if not file:
                continue
            if not (WORK_ROOT / file).is_file():
                log.warning("[index] 死链跳过: %s", file)
                continue
            entries.append({
                "module": module,
                "file": file,
                "title": str(e.get("title") or ""),
                "kw": [k for k in (e.get("kw") or []) if isinstance(k, str) and k],
            })
    return entries


def match_index(text: str) -> list[dict]:
    """硬索引：关键词子串匹配（确定性，路由不经模型）。

    返回去重后的 [{"module", "file", "title"}]。
    """
    hits: list[dict] = []
    seen: set[tuple[str, str]] = set()
    if not (text or "").strip():
        return hits
    for e in load_index_entries():
        if not any(k in text for k in e["kw"]):
            continue
        key = (e["module"], e["file"])
        if key in seen:
            continue
        seen.add(key)
        hits.append({"module": e["module"], "file": e["file"], "title": e["title"]})
    return hits


def build_material_block(text: str, hits: list[dict] | None = None) -> str:
    """命中条目 → 拼接材料块（甲路径落地）；无命中返回空串。

    hits 缺省时走硬索引匹配；fuzzy_match 的结果可直接注入复用拼接与体积上限。
    体积上限：单条 2KB / 总 4KB（防拼接本身制造上下文雪球）。
    拼接格式：[参考材料·标题] 分块前缀，指令一行要求按材料规范操作。
    """
    if hits is None:
        hits = match_index(text)
    if not hits:
        return ""
    parts: list[tuple[str, str]] = []
    total = 0
    for h in hits:
        try:
            content = (WORK_ROOT / h["file"]).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not content:
            continue
        if len(content) > _INDEX_MATERIAL_MAX:
            content = content[: _INDEX_MATERIAL_MAX - 1] + "…"
        if total + len(content) > _INDEX_MATERIAL_TOTAL:
            log.warning("[index] 拼接材料超总上限，截断: %s", [t for t, _ in parts])
            break
        total += len(content)
        parts.append((h["title"] or h["file"], content))
        log.info("[index] 拼接材料: module=%s file=%s", h["module"], h["file"])
    if not parts:
        return ""
    blocks = "\n\n".join(f"[参考材料·{t}]\n{c}" for t, c in parts)
    return "\n\n以下参考材料与本次消息相关，按材料规范操作：\n\n" + blocks + "\n"


# ---------- 模糊索引：小模型判定的兜底层（260827 第七章定案 / Ⅲ-1） ----------

_FUZZY_MIN_LEN = 6     # 极短消息跳过判定（闲聊直通主链路）
_FUZZY_TIMEOUT = 30    # 收紧版超时：最坏多等 30s 后退化裸跑，永不拖死消息

_FUZZY_PROMPT = """你是检索助手。判断用户消息与下列条目中哪些相关。

相关 = 消息涉及该条目的话题、对象或动作即可，不必逐字命中关键词。
例：消息"快递一般放哪" 与条目"快递习惯"相关；消息"今天好累" 与"快递习惯"不相关。

【条目清单】
{listing}

【用户消息】
{text}

【要求】只输出相关条目的编号（每行一个数字），无相关则只输出 NONE；
只能从上述编号中选择，不得编造；不解释。"""


def fuzzy_match(text: str, model: str | None = None) -> list[dict]:
    """硬索引未命中时的模糊兜底：小模型窄任务从索引摘要清单中选相关条目。

    - ≤6 字跳过（闲聊直通主链路）
    - opencode run 单轮（push_render 同构：无状态/无工具/不写会话，构不成雪球回路）
    - 模型：显式传入 > config acp.model > 均无则不带 -m（用 opencode 部署默认模型）
    - 超时/失败/输出无有效编号 → 空列表（退化为裸跑，永不阻塞消息）
    - config `indexer.fuzzy` 显式 False 时整层关闭（观测期保险）
    - 防幻觉：输出编号经范围校验映射回清单，清单外编号丢弃
    """
    from .config import get as get_cfg, resolve_opencode, xdg_env

    if get_cfg("indexer.fuzzy") is False:
        return []
    t = (text or "").strip()
    if len(t) < _FUZZY_MIN_LEN:
        return []
    entries = load_index_entries()
    if not entries:
        return []
    binary = resolve_opencode()
    if not binary:
        log.warning("[index] 未找到 opencode 可执行文件，模糊判定跳过")
        return []
    listing = "\n".join(
        f"{i}. [{e['module']}] {e['title'] or e['file']}（关键词：{'、'.join(e['kw'][:4]) or '-'}）"
        for i, e in enumerate(entries, 1)
    )
    prompt = _FUZZY_PROMPT.format(listing=listing, text=t)
    mdl = model or str(get_cfg("acp.model") or "")
    argv = [str(binary), "run"]
    if mdl:
        argv += ["-m", mdl]
    argv.append(prompt)
    env = {**os.environ, **xdg_env()}
    try:
        r = subprocess.run(
            argv, capture_output=True, text=True, timeout=_FUZZY_TIMEOUT,
            cwd=str(WORK_ROOT), env=env, creationflags=no_window_flags(),
        )
    except subprocess.TimeoutExpired:
        log.warning("[index] 模糊判定超时（%ds），退化裸跑", _FUZZY_TIMEOUT)
        return []
    except OSError as e:
        log.warning("[index] 模糊判定进程异常: %s", e)
        return []
    # 防幻觉解析：提取数字 → 范围校验 → 映射清单
    out = r.stdout or ""
    ids = sorted({int(n) for n in re.findall(r"\d+", out) if 1 <= int(n) <= len(entries)})
    if not ids:
        log.info("[index] 模糊判定: 无命中")
        return []
    hits: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for n in ids:
        e = entries[n - 1]
        key = (e["module"], e["file"])
        if key in seen:
            continue
        seen.add(key)
        hits.append({"module": e["module"], "file": e["file"], "title": e["title"]})
    log.info("[index] 模糊判定: 命中 %s", [(h["module"], h["file"]) for h in hits])
    return hits
