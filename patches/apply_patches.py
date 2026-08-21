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

# Windows CI 控制台默认 GBK，输出中文/emoji 会 UnicodeEncodeError → 强制 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
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
    (
        STORAGE,
        "from pathlib import Path",
        (
            "import os\n"
            "from pathlib import Path"
        ),
        "import os",
        "⑥ storage.py import os（⑦ 依赖）",
    ),
    (
        STORAGE,
        'DEFAULT_STATE_DIR = Path.home() / ".wechat-agent-sdk"',
        (
            "# wechat-claw 补丁：支持 WECHAT_AGENT_SDK_STATE_DIR 环境变量重定向存储目录\n"
            "# （默认 ~/.wechat-agent-sdk，部署时由 bridge.config 收敛到数据根）\n"
            'DEFAULT_STATE_DIR = Path(os.environ.get("WECHAT_AGENT_SDK_STATE_DIR")\n'
            '                            or (Path.home() / ".wechat-agent-sdk"))'
        ),
        'os.environ.get("WECHAT_AGENT_SDK_STATE_DIR")',
        "⑦ storage.py 存储目录可经环境变量重定向",
    ),
    (
        TRANSPORT,
        '    async def activate_token(self, token: str) -> None:\n        """\n        Inject a new token (e.g. after platform handles re-login).\n\n        Persists the token to storage and updates the HTTP client.\n        """\n        self._client.token = token\n        await self._storage.save_token(self._account_id, token)',
        '    async def activate_token(self, token: str) -> None:\n        """\n        Inject a new token (e.g. after platform handles re-login).\n\n        Persists the token to storage and updates the HTTP client.\n        """\n        self._client.token = token\n        await self._storage.save_token(self._account_id, token)\n\n    async def restore_token(self, account_id: Optional[str] = None) -> Optional[str]:\n        """Load a previously saved token into the client (wechat-claw patch).\n\n        Returns the token, or None when nothing is saved. Callers may use it\n        to reuse a persisted login without touching private members.\n        """\n        aid = account_id or self._account_id\n        stored = await self._storage.load_token(aid)\n        if stored:\n            self._client.token = stored\n        return stored',
        "async def restore_token(self",
        "⑧ transport.py 公共 restore_token（bridge 不再摸私有成员）",
    ),
    (
        STORAGE,
        '"""Pluggable account state persistence."""\n\nfrom __future__ import annotations\n\nimport json\nimport logging\nfrom abc import ABC, abstractmethod\nimport os\nfrom pathlib import Path\nfrom typing import Optional\n\nlogger = logging.getLogger(__name__)\n\n# wechat-claw 补丁：支持 WECHAT_AGENT_SDK_STATE_DIR 环境变量重定向存储目录\n# （默认 ~/.wechat-agent-sdk，部署时由 bridge.config 收敛到数据根）\nDEFAULT_STATE_DIR = Path(os.environ.get("WECHAT_AGENT_SDK_STATE_DIR")\n                            or (Path.home() / ".wechat-agent-sdk"))',
        '"""Pluggable account state persistence."""\n\nfrom __future__ import annotations\n\nimport base64\nimport json\nimport logging\nimport os\nimport secrets\nfrom abc import ABC, abstractmethod\nfrom pathlib import Path\nfrom typing import Any, Optional\n\nlogger = logging.getLogger(__name__)\n\n# wechat-claw 补丁：支持 WECHAT_AGENT_SDK_STATE_DIR 环境变量重定向存储目录\n# （默认 ~/.wechat-agent-sdk，部署时由 bridge.config 收敛到数据根）\nDEFAULT_STATE_DIR = Path(os.environ.get("WECHAT_AGENT_SDK_STATE_DIR")\n                            or (Path.home() / ".wechat-agent-sdk"))\n\n# wechat-claw 补丁：accounts.json 静态加密（AES-GCM，密钥 = WECHAT_AGENT_SDK_KEY_FILE\n# 指向的 16/24/32B 文件，与 wechat-claw crypto.key 同源；密文带 enc:v1: 前缀，\n# 旧明文文件兼容读取、下次保存自动转密；SDK 无 cryptography/密钥时明文运行并告警）\n_ENCRYPT_PREFIX = "enc:v1:"\n\n\ndef _make_cipher() -> Optional[Any]:\n    try:\n        from cryptography.hazmat.primitives.ciphers.aead import AESGCM\n    except ImportError:\n        logger.warning("[storage] 无 cryptography，accounts.json 将明文保存")\n        return None\n    key_file = os.environ.get("WECHAT_AGENT_SDK_KEY_FILE")\n    if not key_file:\n        logger.warning("[storage] 未设置 WECHAT_AGENT_SDK_KEY_FILE，accounts.json 将明文保存")\n        return None\n    try:\n        raw = Path(key_file).read_bytes()\n        if len(raw) not in (16, 24, 32):\n            logger.warning("[storage] 密钥长度异常（%d B），accounts.json 将明文保存", len(raw))\n            return None\n        return AESGCM(raw)\n    except OSError as e:\n        logger.warning("[storage] 读取密钥失败: %s，accounts.json 将明文保存", e)\n        return None\n\n\ndef _encrypt_text(plain: str, cipher: Any) -> str:\n    nonce = secrets.token_bytes(12)\n    ct = cipher.encrypt(nonce, plain.encode("utf-8"), None)\n    return _ENCRYPT_PREFIX + base64.b64encode(nonce + ct).decode("ascii")\n\n\ndef _decrypt_text(raw: str, cipher: Any) -> Optional[str]:\n    if not raw.startswith(_ENCRYPT_PREFIX):\n        return None  # 旧明文格式（兼容首读）\n    try:\n        blob = base64.b64decode(raw[len(_ENCRYPT_PREFIX):])\n        nonce, ct = blob[:12], blob[12:]\n        return cipher.decrypt(nonce, ct, None).decode("utf-8")\n    except Exception:\n        logger.warning("[storage] accounts.json 解密失败（密钥变更？），按未登录处理")\n        return None',
        "_ENCRYPT_PREFIX",
        "⑨ storage.py accounts.json 静态加密（AES-GCM，env 密钥）",
    ),
    (
        STORAGE,
        '''    def _load(self) -> dict:
        if self._data is not None:
            return self._data

        if self._file.exists():
            try:
                self._data = json.loads(self._file.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

        return self._data

    def _save(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(self._data or {}, indent=2))
        # 敏感凭据：收紧到 0600（wechat-claw 补丁；默认 umask 644 可被本机其他用户读取）
        try:
            self._file.chmod(0o600)
        except OSError:
            pass''',
        '''    def _load(self) -> dict:
        if self._data is not None:
            return self._data

        if self._file.exists():
            try:
                raw = self._file.read_text()
                cipher = _make_cipher()
                if raw.startswith(_ENCRYPT_PREFIX):
                    if cipher is None:
                        logger.warning("[storage] 无法解密 accounts.json（无密钥/cryptography），按未登录处理")
                        self._data = {}
                        return self._data
                    plain = _decrypt_text(raw, cipher)
                    self._data = json.loads(plain) if plain is not None else {}
                else:
                    self._data = json.loads(raw)  # 兼容：旧明文文件
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("[storage] accounts.json 读取失败: %s", e)
                self._data = {}
        else:
            self._data = {}

        return self._data

    def _save(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._data or {}, indent=2)
        cipher = _make_cipher()
        if cipher is not None:
            payload = _encrypt_text(payload, cipher)  # 密钥可用 → 密文落盘
        else:
            logger.warning("[storage] accounts.json 将以明文保存（无可用密钥）")
        self._file.write_text(payload)
        # 敏感凭据：收紧到 0600（wechat-claw 补丁；默认 umask 644 可被本机其他用户读取）
        try:
            self._file.chmod(0o600)
        except OSError:
            pass''',
        "_decrypt_text",
        "⑩ storage.py _load/_save 加密读写 + 旧明文兼容",
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