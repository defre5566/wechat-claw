"""批 2 回归：签名验签（F8.4）、拒发清单（F6.2）、fixed 话术（F6.3 已在批1单测外）。"""
from __future__ import annotations

import json

import pytest

from bridge.module_source import verify_manifest_signature


def _sig_pair() -> tuple[bytes, bytes]:
    """生成测试密钥对：返回 (私钥hex, 公钥hex)。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    priv = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return priv.hex(), pub.hex()


@pytest.fixture
def signed_source(tmp_path, monkeypatch):
    root = tmp_path / "src"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({"modules": []}), encoding="utf-8")
    priv, pub = _sig_pair()
    monkeypatch.setattr("bridge.config.SIGNING_PUBLIC_KEY", pub)
    return {"root": root, "priv": priv, "pub": pub}


def test_signature_disabled_without_pubkey(signed_source):
    import bridge.config as cfg
    monkeypatch = signed_source["root"].parent  # noqa: F841
    cfg.SIGNING_PUBLIC_KEY = ""
    assert verify_manifest_signature(signed_source["root"]) is None


def test_signature_ok_when_signed(signed_source):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(signed_source["priv"]))
    sig = key.sign((signed_source["root"] / "manifest.json").read_bytes())
    (signed_source["root"] / "manifest.sig").write_text(sig.hex() + "\n", encoding="utf-8")
    assert verify_manifest_signature(signed_source["root"]) is None


def test_signature_rejects_tampered(signed_source):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(signed_source["priv"]))
    sig = key.sign((signed_source["root"] / "manifest.json").read_bytes())
    (signed_source["root"] / "manifest.sig").write_text(sig.hex() + "\n", encoding="utf-8")
    # 篡改 manifest 内容（含末尾换行）→ 验签必须失败
    p = signed_source["root"] / "manifest.json"
    p.write_text(p.read_text() + "\n", encoding="utf-8")
    err = verify_manifest_signature(signed_source["root"])
    assert err is not None and "签名" in err


def test_signature_missing_sig_file(signed_source):
    err = verify_manifest_signature(signed_source["root"])
    assert err is not None and "manifest.sig" in err


def test_reject_name_re_blocks_accounts():
    """F6.2：accounts.json 文件名级硬拒。"""
    from bridge.paths import classify
    assert classify("/tmp/x/accounts.json") == "reject"
    assert classify("/tmp/x/ACCOUNTS.JSON") == "reject"


def test_reject_dirs_cover_sdk_paths(tmp_path, monkeypatch):
    """F6.2：agent-SDK 与 ~/.wechat-agent-sdk 目录级硬拒。

    历史教训：旧版用真实 DATA_ROOT 建 agent-SDK 且 finally rmtree——部署机数据根
    里该目录就是微信登录态真身，跑一次全量 pytest 即清掉登录态（260829 鑫证实）。
    自此 DATA_ROOT/HOME 全部隔离到 tmp_path，永不触碰真实目录。"""
    from bridge import paths
    from bridge.config import DATA_ROOT

    fake_data_root = tmp_path / "data"
    fake_home = tmp_path / "home"
    monkeypatch.setattr("bridge.config.DATA_ROOT", fake_data_root)
    monkeypatch.setattr(paths, "HOME", fake_home)
    # classify→resolve_path 依赖 bridge.config.DATA_ROOT（模块属性隔离后自动生效）
    monkeypatch.setattr("bridge.paths.REJECT_DIRS", [fake_data_root / "agent-SDK"])
    monkeypatch.setattr("bridge.paths.DEFAULT_ALLOW_DIRS", [])

    sdk = fake_data_root / "agent-SDK"
    sdk.mkdir(parents=True, exist_ok=True)
    (sdk / "accounts.json").write_text("{}", encoding="utf-8")
    assert paths.classify(sdk / "accounts.json") == "reject"

    home_sdk = fake_home / ".wechat-agent-sdk"
    home_sdk.mkdir(parents=True, exist_ok=True)
    (home_sdk / "accounts.json").write_text("{}", encoding="utf-8")
    assert paths.classify(home_sdk / "accounts.json") == "reject"
    # 全程只摸 tmp——真实 HOME 与数据根从不被触碰，无需 try/finally 清理