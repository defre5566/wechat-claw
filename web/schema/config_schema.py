"""config.yaml 用户段 schema：acp / file_send / crypto。

字段类型：text / number / list（多行文本，每行一项）/ readonly（只读展示）。
前端按此渲染高级设置表单；后端按此校验提交。
"""
from __future__ import annotations

CONFIG_SCHEMA: list[dict] = [
    {
        "group": "acp",
        "title": "opencode 对话 agent",
        "fields": [
            {"key": "command", "label": "命令路径", "type": "text",
             "default": "opencode",
             "hint": "默认为 PATH 中的 opencode；自定义安装可填绝对路径"},
            {"key": "port", "label": "端口", "type": "number",
             "default": 45678, "min": 1, "max": 65535,
             "hint": "opencode ACP 子进程端口（避免与 4096/8650/9898 冲突）"},
        ],
    },
    {
        "group": "file_send",
        "title": "文件发送规则",
        "fields": [
            {"key": "default_dirs", "label": "直发目录", "type": "list",
             "default": ["~/文档", "~/下载", "~/桌面", "~/图片",
                         "~/音乐", "~/视频", "~/公共", "inbox"],
             "hint": "每行一个目录；这些目录下的文件微信发送免确认"},
            {"key": "reject_dirs", "label": "拒绝目录", "type": "list",
             "default": [".config", "~/.ssh", "~/.gnupg"],
             "hint": "每行一个目录；硬拒，任何通道不放行"},
            {"key": "reject_name_re", "label": "拒绝文件名模式", "type": "text",
             "default": "token|secret|credential|private|anniversaries\\.json\\.enc",
             "hint": "正则，命中文件名的文件硬拒"},
            {"key": "reject_suffixes", "label": "拒绝扩展名", "type": "list",
             "default": [".key", ".pem", ".p12", ".pfx", ".p8"],
             "hint": "每行一个扩展名"},
        ],
    },
    {
        "group": "crypto",
        "title": "隐私数据",
        "fields": [
            {"key": "key_file", "label": "加密密钥路径", "type": "readonly",
             "default": ".config/crypto.key",
             "hint": "只读：密钥自动生成，丢失将无法解密已加密的隐私数据"},
        ],
    },
    {
        "group": "update",
        "title": "模块自动更新",
        "fields": [
            {"key": "auto_enabled", "label": "自动更新", "type": "boolean",
             "default": True,
             "hint": "开启后每天定时检查模块源：源有变化即自动更新已装模块（静默，不推送）；模块级开关可单独关闭"},
            {"key": "check_time", "label": "检查时刻", "type": "text",
             "default": "04:00",
             "hint": "每日检查时刻（HH:MM）；源无变化时零开销跳过"},
        ],
    },
]


def get_schema() -> list[dict]:
    """返回 schema 副本（含当前默认值）。"""
    import copy
    return copy.deepcopy(CONFIG_SCHEMA)


def validate_settings(settings: dict) -> dict:
    """按 schema 校验并清洗提交值：只保留 schema 内键，类型/范围校验。非法返回 (ok=False, errors)。"""
    errors: list[str] = []
    clean: dict = {}
    for group in CONFIG_SCHEMA:
        gname = group["group"]
        raw_group = settings.get(gname)
        if not isinstance(raw_group, dict):
            continue
        g_out: dict = {}
        for f in group["fields"]:
            if f["key"] not in raw_group:
                continue
            val = raw_group[f["key"]]
            ftype = f["type"]
            if ftype == "number":
                try:
                    val = int(val)
                except (TypeError, ValueError):
                    errors.append(f"{gname}.{f['key']} 必须为数字")
                    continue
                if val < f.get("min", -(10 ** 9)) or val > f.get("max", 10 ** 9):
                    errors.append(f"{gname}.{f['key']} 超出范围")
                    continue
            elif ftype == "list":
                if not isinstance(val, list):
                    errors.append(f"{gname}.{f['key']} 必须为列表")
                    continue
                val = [str(x) for x in val]
            elif ftype in ("text", "readonly"):
                val = str(val)
            else:
                continue
            g_out[f["key"]] = val
        if g_out:
            clean[gname] = g_out
    return {"ok": not errors, "clean": clean, "errors": errors}
