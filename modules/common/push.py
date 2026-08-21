"""推送客户端（模块 → bridge /push）：token 读取 + POST 重试。

三模块共用一套；重试策略可配置（config.yaml push.*，默认 3 次 × 3s × 15s）。
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from bridge.config import get

PUSH_URL = f"http://{get('push.host')}:{get('push.port')}/push"
RETRY = get("push.retry_attempts")
RETRY_INTERVAL = get("push.retry_interval")
TIMEOUT = get("push.timeout")


def load_token(module_dir: Path) -> str:
    """读模块 token（register.py 生成，0600）。"""
    return (module_dir / "token").read_text().strip()


def post_push(payload: dict, token: str) -> bool:
    """POST /push；200 返回 True；失败重试 RETRY 次，全败 False（落 WARN，不静默）。"""
    body = json.dumps(payload).encode()
    for attempt in range(RETRY):
        try:
            req = urllib.request.Request(
                PUSH_URL, data=body,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        if attempt < RETRY - 1:
            time.sleep(RETRY_INTERVAL)
    from modules.common.log import log_event
    log_event("WARN", str(payload.get("module") or "unknown"), "push_fail",
              f"POST /push 重试 {RETRY} 次全败（{payload.get('type', '?')}）")
    return False