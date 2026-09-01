"""config.yaml 用户段 schema：acp / file_send。

字段类型：text / number / list（多行文本，每行一项）。
前端按此渲染高级设置表单；后端按此校验提交。

260830 收敛（鑫定案）：
- crypto 组移除——key_file 是自动生成的内部路径，不是用户配置（改错即隐私数据
  永久不可解密）；读取逻辑不受影响（bridge.config DEFAULTS_USER 仍供默认值），
  仅不再暴露为可编辑项
- update 组迁出——模块自动更新开关归模块页（存储仍是 config.yaml update 段，
  scheduler/module_source 读取不变，仅 UI 归位）
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
            {"key": "model", "label": "渲染模型", "type": "select",
             "default": "",
             "hint": "推送渲染/索引/人设优化使用的模型；留空用 opencode 部署默认。候选经 opencode models 动态注入（schema_get 时填充 field.options）"},
        ],
    },
    {
        "group": "file_send",
        "title": "文件发送规则",
        "fields": [
            {"key": "default_dirs", "label": "直发目录", "type": "list",
             "default": [],
             "hint": "每行一个目录；这些目录下的文件微信发送免确认。默认为空：未填写的路径发送时走微信确认（gate）"},
            {"key": "reject_dirs", "label": "拒绝目录", "type": "list",
             "default": [".config", "agent-SDK", "~/.wechat-agent-sdk",
                         "~/.ssh", "~/.gnupg"],
             "hint": "每行一个目录；硬拒，任何通道不放行（含微信 SDK 凭证目录）"},
            {"key": "reject_name_re", "label": "拒绝文件名模式", "type": "text",
             "default": "token|secret|credential|private|accounts\\.json|anniversaries\\.json\\.enc",
             "hint": "正则，命中文件名的文件硬拒"},
            {"key": "reject_suffixes", "label": "拒绝扩展名", "type": "list",
             "default": [".key", ".pem", ".p12", ".pfx", ".p8"],
             "hint": "每行一个扩展名；收紧后微信端将对命中文件要求确认或拒发"},
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
            elif ftype == "boolean":
                if isinstance(val, bool):
                    pass
                elif val in ("true", "True", 1):
                    val = True
                elif val in ("false", "False", 0):
                    val = False
                else:
                    errors.append(f"{gname}.{f['key']} 必须为布尔值")
                    continue
            elif ftype in ("text", "readonly"):
                val = str(val)
            elif ftype == "select":
                # 候选是动态的（opencode models），不在此强校验；空串表示"不指定"合法
                val = str(val)
            else:
                continue
            g_out[f["key"]] = val
        if g_out:
            clean[gname] = g_out
    return {"ok": not errors, "clean": clean, "errors": errors}
