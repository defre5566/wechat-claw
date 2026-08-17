"""模块注册：生成/重置模块 token（0600）并写各模块自描述 module.json。

用法:
  python3 modules/register.py <模块名> [--purpose 用途] [--spec 规范文件] \\
      [--schedule-json 'json 数组'] [--retry-json 'json 或 null']

说明:
  - token 每次注册重置（index 的 token_hash 由 build_index 实时从 token 计算，自动跟随）
  - module.json 已存在的字段保留，只更新指定字段
  - 模块注册后由 scheduler 的 build_index() 自动发现，bridge 零改动
"""
from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

MODULES_DIR = Path(__file__).resolve().parent


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    name = sys.argv[1]
    args = sys.argv[2:]

    opts: dict[str, str] = {}
    i = 0
    while i < len(args):
        if args[i] in ("--purpose", "--spec", "--schedule-json", "--retry-json") and i + 1 < len(args):
            opts[args[i][2:]] = args[i + 1]  # 去掉 -- 前缀
            i += 2
        else:
            i += 1

    mod_dir = MODULES_DIR / name
    mod_dir.mkdir(parents=True, exist_ok=True)

    # token：重置 + 0600
    token_file = mod_dir / "token"
    token = secrets.token_hex(32)
    token_file.write_text(token)
    token_file.chmod(0o600)

    # module.json：保留已有字段，更新指定字段
    mj = mod_dir / "module.json"
    data = {}
    if mj.is_file():
        try:
            data = json.loads(mj.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["name"] = name
    if "purpose" in opts:
        data["purpose"] = opts["purpose"]
    if "spec" in opts:
        data["spec"] = opts["spec"]
    data.setdefault("purpose", "")
    data.setdefault("spec", "规范.md")
    if "schedule-json" in opts:
        data["schedule"] = json.loads(opts["schedule-json"])
    if "retry-json" in opts:
        data["retry"] = json.loads(opts["retry-json"])  # 支持 null
    mj.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    print(f"[register] {name}: token -> {token_file} (0600 已重置), module.json 已更新")
    print(f"[register] schedule={data.get('schedule')!r} retry={data.get('retry')!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())