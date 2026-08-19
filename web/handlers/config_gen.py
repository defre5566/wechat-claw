"""④ 配置生成：config.yaml（DEFAULTS_USER 序列化）+ crypto.key + 管理密码。"""
from __future__ import annotations

import yaml

from bridge.config import CONFIG_FILE, DEFAULTS_USER
from modules.common import crypto as crypto_mod
from web import auth


def _gen_config() -> dict:
    """config.yaml：已存在不覆盖（幂等，标注）；不存在按 DEFAULTS_USER 生成。"""
    if CONFIG_FILE.is_file():
        return {"ok": True, "file": str(CONFIG_FILE), "created": False}
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            yaml.safe_dump(DEFAULTS_USER, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return {"ok": True, "file": str(CONFIG_FILE), "created": True}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def _gen_key() -> dict:
    try:
        crypto_mod._ensure_key()  # 自动生成 + chmod 600（不存在时）
        return {"ok": True, "file": str(crypto_mod.KEY_FILE)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle(app, body: dict | None = None) -> dict:
    body = body or {}
    results = {"config": _gen_config(), "key": _gen_key()}

    # 管理密码：首次必填（开放期仅限向导完成前；已存在则可跳过）
    password = body.get("password", "")
    if password:
        if len(password) < auth.MIN_PASSWORD_LEN:
            return {"ok": False, "error": f"密码至少 {auth.MIN_PASSWORD_LEN} 位",
                    "results": results}, 400
        if not auth.set_password(password):
            return {"ok": False, "error": "密码写入失败", "results": results}, 500
        results["password"] = {"ok": True, "set": True}
    elif not auth.password_exists():
        # 首次配置必须设密码（杜绝"未设密码=永久开放期"被 CSRF 接管）
        return {"ok": False,
                "error": "首次配置必须设置管理密码（至少 6 位）",
                "results": results}, 400
    else:
        results["password"] = {"ok": True, "set": False, "exists": True}

    app.steps["config_gen"] = all(r.get("ok") for r in results.values())
    return {"ok": True, "results": results}
