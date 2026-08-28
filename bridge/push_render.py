"""push_render：reminder/alert 推送的单轮文本渲染器。

- 独立一次性 subprocess（opencode run），无会话/无历史——切断
  "推送加工写入用户会话历史"的上下文雪球正反馈（260827 批1）
- tier0 基线人设优先读 <数据根>/instructions/tier0.md，缺失用内置出厂基线
- 任何失败/超时返回 None，调用方回退原文直发（提醒绝不因渲染丢失）
"""
from __future__ import annotations

import logging
import os
import subprocess

from .config import WORK_ROOT, get as get_cfg, no_window_flags

log = logging.getLogger("wechat-bridge")

RENDER_TIMEOUT = 120          # 与 admin.optimize_persona 同口径
MAX_RENDER_LEN = 200          # 渲染产物上限（防模型跑飞），超出截断

# 出厂默认 tier0（与 instructions/tier0.md 内置基线一致；文件存在则优先文件）
FALLBACK_TIER0 = "你是用户部署的个人数字助理小助手，服务对象是用户，称呼对方为用户。"

RENDER_PROMPT = """你是微信助手的播报渲染器。把下面的推送素材改写成一条自然的微信提醒文本。

人设基调：
{tier0}

要求：直接输出最终文本，仅一句，不超过40字；不要使用任何工具或文件操作；不要解释；不要引号。
素材：{text}
"""


def _load_tier0() -> str:
    """读数据根 tier0 基线；缺失/为空用内置默认。"""
    p = WORK_ROOT / "instructions" / "tier0.md"
    try:
        text = p.read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    return FALLBACK_TIER0


def _clean_output(raw: str) -> str:
    """清洗 run 输出：去 CLI 前缀行（'>' 开头）与空行，余下拼接并限长。"""
    lines = [ln.strip() for ln in (raw or "").splitlines()]
    keep = [ln for ln in lines if ln and not ln.startswith(">")]
    text = " ".join(keep).strip()
    if len(text) > MAX_RENDER_LEN:
        text = text[: MAX_RENDER_LEN - 1] + "…"
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
    mdl = model or str(get_cfg("acp.model") or "deepseek/deepseek-chat")
    prompt = RENDER_PROMPT.format(tier0=_load_tier0(), text=text.strip())
    env = {**os.environ, **xdg_env()}
    try:
        r = subprocess.run(
            [str(binary), "run", "-m", mdl, prompt],
            capture_output=True, text=True, timeout=RENDER_TIMEOUT,
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
