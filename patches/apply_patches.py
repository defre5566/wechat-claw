#!/usr/bin/env python3
"""wechat-agent-sdk 本地补丁：检测 / 打补丁 / 校验（幂等）。

用法（用项目 venv 的 python 运行）:
    .venv/bin/python patches/apply_patches.py                    # 检测并打补丁（site-packages）
    .venv/bin/python patches/apply_patches.py --check-only       # 只检测报告
    .venv/bin/python patches/apply_patches.py --vendor [--check-only]  # 目标为 vendor/wechat_agent_sdk 快照（分发/CI 校验用）

原理: 每个补丁 = (原版锚点 → 补丁文本)。文件含补丁特征 → 已打跳过；
含原版锚点(且唯一) → 替换打补丁；两者皆无 → 报异常（SDK 版本/改动未知）。
"""
from __future__ import annotations

import pathlib
import py_compile
import sys
import sysconfig

VENDOR = pathlib.Path(__file__).resolve().parent.parent / "vendor" / "wechat_agent_sdk"
SITE = pathlib.Path(sysconfig.get_paths()["purelib"])
SDK = VENDOR if "--vendor" in sys.argv else SITE / "wechat_agent_sdk"
CDN = SDK / "media" / "cdn.py"
TRANSPORT = SDK / "transport.py"
STORAGE = SDK / "account" / "storage.py"

PATCHES = [
    (
        CDN,
        "    aes_key_b64 = base64.b64encode(key).decode()",
        "    # 格式 B：base64(hex字符串)，与官方 wong2 参考实现一致；格式 A（base64(原始字节)）微信端解密失败\n    aes_key_b64 = base64.b64encode(key.hex().encode()).decode()",
        "aes_key_b64 = base64.b64encode(key.hex().encode()).decode()",
        "① cdn.py aes_key_b64 格式 B",
    ),
    (
        TRANSPORT,
        'def _build_media_item(media_type: str, cdn_info: dict, file_name: str = "") -> dict:',
        'def _build_media_item(media_type: str, cdn_info: dict, file_name: str = "", file_size: int = 0) -> dict:',
        "file_size: int = 0) -> dict:",
        "② transport.py _build_media_item 加 file_size",
    ),
    (
        TRANSPORT,
        '        if file_name:\n            item["file_item"]["file_name"] = file_name\n        return item',
        '        if file_name:\n            item["file_item"]["file_name"] = file_name\n        if file_size:\n            item["file_item"]["len"] = str(file_size)\n        return item',
        'if file_size:\n            item["file_item"]["len"] = str(file_size)',
        "②b transport.py file_item 加 len",
    ),
    (
        CDN,
        'url = f"{CDN_BASE}?encrypted_query_param={encrypt_query_param}"',
        "url = f\"{CDN_BASE}/download?encrypted_query_param={quote(encrypt_query_param, safe='')}\"",
        "{quote(encrypt_query_param, safe='')}",
        "③ cdn.py download URL quote",
    ),
    (
        CDN,
        "import httpx",
        "import httpx\nfrom urllib.parse import quote",
        "from urllib.parse import quote",
        "④ cdn.py import quote（③ 依赖的符号）",
    ),
    (
        STORAGE,
        "        self._file.write_text(json.dumps(self._data or {}, indent=2))",
        (
            "        self._file.write_text(json.dumps(self._data or {}, indent=2))\n"
            "        # 敏感凭据：收紧到 0600（wechat-claw 补丁；默认 umask 644 可被本机其他用户读取）\n"
            "        try:\n"
            "            self._file.chmod(0o600)\n"
            "        except OSError:\n"
            "            pass"
        ),
        "self._file.chmod(0o600)",
        "⑤ storage.py accounts.json chmod 0600",
    ),
]


def status_of(file: pathlib.Path, original: str, feature: str) -> str:
    """返回: 'patched' / 'need' / 'unknown'。"""
    text = file.read_text(encoding="utf-8")
    if feature in text:
        return "patched"
    if original in text:
        n = text.count(original)
        if n == 1:
            return "need"
        return f"ambiguous({n})"
    return "unknown"


def apply(file: pathlib.Path, original: str, patched: str) -> bool:
    text = file.read_text(encoding="utf-8")
    text = text.replace(original, patched, 1)
    file.write_text(text, encoding="utf-8")
    return True


def main() -> int:
    check_only = "--check-only" in sys.argv
    mode = "vendor 快照" if "--vendor" in sys.argv else "site-packages"
    all_ok = True
    print(f"SDK 目录: {SDK}（{mode}）")

    for file, original, patched, feature, desc in PATCHES:
        if not file.is_file():
            print(f"[FAIL] {desc}: 文件不存在 {file}")
            all_ok = False
            continue
        st = status_of(file, original, feature)
        if st == "patched":
            print(f"[SKIP] {desc}: 已打")
        elif st == "need":
            if check_only:
                print(f"[TODO] {desc}: 未打，需补丁")
                all_ok = False
            else:
                apply(file, original, patched)
                after = status_of(file, original, feature)
                if after == "patched":
                    print(f"[DONE] {desc}: 已打")
                else:
                    print(f"[FAIL] {desc}: 打补丁后状态异常 {after}")
                    all_ok = False
        else:
            print(f"[FAIL] {desc}: 未知状态 {st}（原版锚点缺失或重复，SDK 版本可能不对）")
            all_ok = False

    # 语法校验
    for file in (CDN, TRANSPORT, STORAGE):
        try:
            py_compile.compile(str(file), doraise=True)
        except py_compile.PyCompileError as e:
            print(f"[FAIL] py_compile {file.name}: {e}")
            all_ok = False
    if all_ok:
        print("OK: 全部补丁在位，语法通过")
    else:
        print("NG: 存在未处理项")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())