"""模块 settings 通用校验器（settings_schema → 清洗/校验提交值）。

settings_schema 结构（module.json）：
[{ "section": "小节标题", "desc": "标题下说明文字(可选)",
   "fields": [{"key","type","options?","default","desc","show_when?","required_when?"}] }]

- type：string / path / select（options 支持 {value,label}）/ boolean / tags / number / choice
- choice：多选词条（候选列表 + 最多可选数）。值 = list[str]；候选由调用方注入
  （field["candidates"]，如模块 directions.json + 自定义 prompt 目录）；非法候选静默过滤
  （候选可能因删除自定义 prompt 而变化，不阻塞保存）；超过 max → 报错
- show_when：条件不满足 → 丢弃提交值（前端隐藏字段，后端兜底）
- show_when_service：运行时服务条件（如 pollen/typhoon 按 location 可用性）；调用方传入
  services 列表，字段声明该条件且服务不可用 → 丢弃提交值（传 None 不校验，向后兼容）
- required_when：条件满足但未提交 → 报错（如 vault_path 在 data_source=vault 时必填）
- 只保留 schema 内键（清洗），防注入未知字段
"""
from __future__ import annotations


def _cond(cond: dict | None, settings: dict) -> bool:
    """条件判定：{"data_source": "vault"} → settings.data_source == "vault"。"""
    if not cond:
        return True
    return all(settings.get(k) == v for k, v in cond.items())


def _blank(value) -> bool:
    """空白判定：None / 空字符串（去空格）/ 空列表。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _coerce(typ: str, value, field: dict) -> tuple[bool, object]:
    """类型转换/校验；返回 (ok, 清洗后的值)。"""
    if typ in ("string", "path"):
        if value is None:
            return True, ""
        return True, str(value)
    if typ == "number":
        try:
            return True, int(value)
        except (TypeError, ValueError):
            return False, None
    if typ == "boolean":
        if isinstance(value, bool):
            return True, value
        if value in ("true", "True"):
            return True, True
        if value in ("false", "False"):
            return True, False
        return False, None
    if typ == "tags":
        if isinstance(value, list) and all(isinstance(x, str) for x in value):
            return True, value
        return False, None
    if typ == "choice":
        if isinstance(value, list) and all(isinstance(x, str) for x in value):
            return True, value
        return False, None
    if typ == "select":
        return True, str(value)
    return False, None


def validate_module_settings(
    schema: list | None,
    submitted: dict | None,
    services: list | None = None,
) -> tuple[bool, dict, list[str]]:
    """校验并清洗提交的 settings。

    services（可选）：当前可用的运行时服务名列表（如 localdata.available 结果）。
    字段声明 show_when_service 且服务不可用 → 丢弃提交值（传 None 不校验，向后兼容）。

    返回 (ok, clean_settings, errors)。errors 非空即失败（提交方应拒绝保存）。
    """
    if not schema:
        return True, {}, []  # 无 schema 的模块：settings 不可改
    if not isinstance(submitted, dict):
        return False, {}, ["settings 必须是对象"]
    if not isinstance(schema, list):
        return False, {}, ["settings_schema 格式错误"]

    by_key: dict[str, dict] = {}
    for section in schema:
        for f in section.get("fields") or []:
            if isinstance(f, dict) and f.get("key"):
                by_key[f["key"]] = f

    clean: dict = {}
    errors: list[str] = []
    for key, field in by_key.items():
        if not _cond(field.get("show_when"), clean):
            continue  # 条件不满足 → 丢弃（含隐藏字段的提交）
        svc = field.get("show_when_service")
        if svc and services is not None and svc not in services:
            continue  # 运行时服务不可用（如花粉在非内蒙古）→ 丢弃提交值
        rw = field.get("required_when")
        rw_active = rw is not None and _cond(rw, clean)
        has = key in submitted
        if not has:
            if rw_active:
                errors.append(f"{field.get('desc') or key} 为必填")
            continue
        if rw_active and _blank(submitted.get(key)):
            errors.append(f"{field.get('desc') or key} 为必填")  # 空白不能保存
            continue
        ok_v, cv = _coerce(field.get("type", "string"), submitted.get(key), field)
        if not ok_v:
            errors.append(f"{field.get('desc') or key} 格式不正确")
            continue
        if field.get("type") == "select":
            opts = field.get("options") or []
            vals = [o.get("value") if isinstance(o, dict) else o for o in opts]
            if cv not in vals:
                errors.append(f"{field.get('desc') or key} 选项无效")
                continue
        if field.get("type") == "choice":
            cands = field.get("candidates") or []
            if cands:
                cv = [x for x in cv if x in cands]  # 非法候选静默过滤（候选可变）
            mx = field.get("max")
            if mx and len(cv) > int(mx):
                errors.append(f"{field.get('desc') or key} 最多选择 {mx} 项")
                continue
        clean[key] = cv

    return (not errors), clean, errors
