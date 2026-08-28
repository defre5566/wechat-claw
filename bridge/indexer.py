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
import re
import time

from .config import WORK_ROOT

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


# ---------- 会话画像与冷启动装配（乙为主） ----------

_PROFILE_WINDOW = 20          # 画像取样：该 conv 最近 N 条观测
_PROFILE_DEFAULT_TIER = 2     # 无画像（新会话/无记录）默认档


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

    调用时机 = 会话冷启动前后（5h 归档 / 模块重载清 session）；
    装配失败吞异常保留旧内容（部署 jsonc 指向的文件永远存在）。
    返回实际写入的档位。
    """
    from web.agent_gen import CURRENT_TIER, INSTRUCTIONS_DIR

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


# ---------- 硬索引：位置表匹配与材料拼接（260827 议题1 / Ⅱ-4） ----------

_INDEX_MATERIAL_MAX = 2048      # 单条材料上限（字符）
_INDEX_MATERIAL_TOTAL = 4096    # 单次拼接总上限


def match_index(text: str) -> list[dict]:
    """扫 instructions/index/ 现役 json（.off 天然不在 glob 内），关键词子串命中。

    返回去重后的 [{"module", "file", "title"}]；目标文件缺失 → 跳过 + 告警
    （死链防线：索引有、文件无 = 不可拼）；解析失败文件跳过不阻塞。
    """
    from web.agent_gen import INDEX_DIR

    hits: list[dict] = []
    seen: set[tuple[str, str]] = set()
    if not (text or "").strip() or not INDEX_DIR.is_dir():
        return hits
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
            kws = e.get("kw") or []
            if not any(isinstance(k, str) and k and k in text for k in kws):
                continue
            file = str(e.get("file") or "")
            if not file:
                continue
            if not (WORK_ROOT / file).is_file():
                log.warning("[index] 命中但目标缺失（死链跳过）: %s", file)
                continue
            key = (module, file)
            if key in seen:
                continue
            seen.add(key)
            hits.append({"module": module, "file": file, "title": str(e.get("title") or "")})
    return hits


def build_material_block(text: str) -> str:
    """硬索引命中 → 拼接材料块（甲路径落地）；无命中返回空串。

    体积上限：单条 2KB / 总 4KB（防拼接本身制造上下文雪球）。
    拼接格式：[参考材料·标题] 分块前缀，指令一行要求按材料规范操作。
    """
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
