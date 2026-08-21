"""④ 配置生成：config.yaml（DEFAULTS_USER 序列化）+ crypto.key + 管理密码 + opencode.jsonc。"""
from __future__ import annotations

import json
import os

import yaml

from bridge.config import CONFIG_FILE, DEFAULTS_USER, DATA_ROOT, RESOURCE_ROOT
from modules.common import crypto as crypto_mod
from web import auth
from web.handlers.opencode_setup import detect_installed

OPCODE_CONFIG = DATA_ROOT / "opencode.jsonc"


def _gen_config() -> dict:
    """config.yaml：已存在不覆盖（幂等，标注）；不存在按 DEFAULTS_USER 生成。"""
    if CONFIG_FILE.is_file():
        return {"ok": True, "file": str(CONFIG_FILE), "created": False}
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        cfg = json.loads(json.dumps(DEFAULTS_USER))
        # opencode 自动安装于用户目录（~/.opencode/bin）：acp.command 写绝对路径，
        # 避免 systemd/nssm 拉起 bridge 的环境 PATH 不含该目录
        d = detect_installed()
        if d and d.get("path") and os.path.isabs(d["path"]):
            cfg["acp"] = {**(cfg.get("acp") or {}), "command": d["path"]}
        CONFIG_FILE.write_text(
            yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
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


def _gen_opencode_config() -> dict:
    """数据根 opencode.jsonc：权限模板 + 默认免费模型（已存在不覆盖）。

    ACP 子进程 cwd=数据根，自动加载 ./opencode.jsonc；不写全局 opencode 配置，
    避免覆盖用户原有配置。
    """
    if OPCODE_CONFIG.is_file():
        return {"ok": True, "file": str(OPCODE_CONFIG), "created": False}
    tpl = RESOURCE_ROOT / "opencode.jsonc.example"
    if not tpl.is_file():
        return {"ok": False, "error": "模板缺失"}
    try:
        OPCODE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        OPCODE_CONFIG.write_text(tpl.read_text(encoding="utf-8"), encoding="utf-8")
        return {"ok": True, "file": str(OPCODE_CONFIG), "created": True}
    except OSError as e:
        return {"ok": False, "error": str(e)}


# 信任模型声明（部署机数据根存档，供用户查阅；不入仓库 docs/）
_TRUST_NOTICE = """# 模块源信任模型声明

本机通过「模块更新」功能每日自动拉取模块库并安装/更新模块。请知悉以下信任模型：

## 模块库性质
- 「官方模块库」为**作者个人维护**的 GitHub 仓库（wechat-claw_modules_official），
  并非机构运营的官方渠道。安装与自动更新即代表你信任作者及其账号安全。
- 「自定义源」为第三方地址，风险自担；管理后台安装时会提示「只装信任来源」。

## 校验机制
- 传输校验：模块包 sha256 与 manifest 比对（防传输损坏）。
- 签名校验：builtin 源启用了 Ed25519 签名（manifest.sig）后，manifest 变化必须
  通过内置公钥验证；验证失败将**拒绝更新**并告警（防源仓库被攻破后恶意分发）。
- 签名公钥（若已配置）：{pubkey}

## 建议
- 不需要自动更新可在配置中关闭「模块自动更新」。
- 本文件为部署存档副本，程序升级可覆盖；数据在 <数据根>/.config/trust/。
"""


def _gen_trust_notice() -> dict:
    """数据根 .config/trust/TRUST-NOTICE.md：信任模型存档（幂等覆盖，供用户查阅）。"""
    try:
        from bridge.config import SIGNING_PUBLIC_KEY
        pub = str(SIGNING_PUBLIC_KEY or "").strip() or "（未启用）"
        target_dir = DATA_ROOT / ".config" / "trust"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "TRUST-NOTICE.md").write_text(
            _TRUST_NOTICE.format(pubkey=pub), encoding="utf-8"
        )
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle(app, body: dict | None = None) -> dict:
    body = body or {}
    results = {"config": _gen_config(), "key": _gen_key(),
               "opencode": _gen_opencode_config(),
               "trust_notice": _gen_trust_notice()}

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
