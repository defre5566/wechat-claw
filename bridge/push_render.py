"""push_render：reminder/alert 推送的单轮文本渲染器。

- 独立一次性 subprocess（opencode run），无会话/无历史——切断
  "推送加工写入用户会话历史"的上下文雪球正反馈（260827 批1）
- 人设读 tier-current 当前档（完整语气条目进渲染；缺失回退内置基线）
- 职责边界（260830 P3 定案）：主体只给通道和人设，**不限定排版/字数**——
  素材长短由模块侧内容决定；唯一约束是防模型发疯的兜底线
- 任何失败/超时返回 None，调用方回退原文直发（提醒绝不因渲染丢失）
"""
from __future__ import annotations

import logging
import os
import subprocess

from .config import WORK_ROOT, get as get_cfg, no_window_flags

log = logging.getLogger("wechat-bridge")

RENDER_TIMEOUT = 120          # 与 admin.optimize_persona 同口径
FUSE_LEN = 5000               # 防发疯兜底线（正常内容不可触达；与素材体量无关）

# 出厂默认人设（tier-current 缺失时的兜底，与 instructions/tier0.md 语义一致）
FALLBACK_TIER = "你是用户部署的个人数字助理小助手，服务对象是用户，称呼对方为用户。"

RENDER_PROMPT = """你是微信助手的播报渲染器。把下面的推送素材按人设语气整理成微信消息。

人设：
{tier}

要求：
- 素材已包含全部信息，按素材条目如实播报，不要增加素材之外的信息，也不要遗漏条目
- 保持素材的自然段结构，段落之间用空行分隔；不要把多条内容挤成一段，也不要自行拆散条目
- 不要使用任何工具或文件操作；不要解释；不要引号
- 直接输出最终文本
素材：
{text}
"""


def _load_tier() -> str:
    """读 tier-current 当前档（完整人设进渲染）；缺失/为空用内置兜底。"""
    p = WORK_ROOT / "instructions" / "tier-current.md"
    try:
        text = p.read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    return FALLBACK_TIER


def _clean_output(raw: str) -> str:
    """清洗 run 输出：去 CLI 前缀行（'>' 开头）与空行，余下拼接；
    仅 FUSE_LEN 兜底（防模型复读机式跑飞，正常输出不可触达）。"""
    lines = [ln.strip() for ln in (raw or "").splitlines()]
    keep = [ln for ln in lines if ln and not ln.startswith(">")]
    text = "\n".join(keep).strip()
    if len(text) > FUSE_LEN:
        text = text[: FUSE_LEN - 1] + "…"
    return text


def render_push_text(text: str, model: str | None = None) -> str | None:
    """渲染推送素材为播报文本；失败返回 None（调用方回退原文直发）。

    单轮无工具：prompt 明令禁用工具 + 权限默认未放行写操作，双保险；
    subprocess 一次性进程，不产生任何持久会话。
    """
    from .config import resolve_opencode, xdg_env

    if not (text or "").strip():
        return None
    binary = resolve_opencode()
    if not binary:
        log.warning("[push-render] 未找到 opencode 可执行文件，回退原文")
        return None
    prompt = RENDER_PROMPT.format(tier=_load_tier(), text=text.strip())
    mdl = model or str(get_cfg("acp.model") or "")
    argv = [str(binary), "run"]
    if mdl:
        argv += ["-m", mdl]
    argv.append(prompt)
    env = {**os.environ, **xdg_env()}
    try:
        r = subprocess.run(
            argv, capture_output=True, text=True, timeout=RENDER_TIMEOUT,
            cwd=str(WORK_ROOT), env=env, creationflags=no_window_flags(),
        )
    except subprocess.TimeoutExpired:
        log.warning("[push-render] 渲染超时（%ds），回退原文", RENDER_TIMEOUT)
        return None
    except OSError as e:
        log.warning("[push-render] 渲染进程异常: %s", e)
        return None
    out = _clean_output(r.stdout or r.stderr or "")
    if not out:
        log.warning("[push-render] 渲染输出为空，回退原文")
        return None
    return out
