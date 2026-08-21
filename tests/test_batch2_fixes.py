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


def test_reject_dirs_cover_sdk_paths(tmp_path):
    """F6.2：agent-SDK 与 ~/.wechat-agent-sdk 目录级硬拒。"""
    from bridge import paths
    from bridge.config import DATA_ROOT
    sdk = DATA_ROOT / "agent-SDK"
    sdk.mkdir(parents=True, exist_ok=True)
    (sdk / "accounts.json").write_text("{}", encoding="utf-8")
    try:
        assert paths.classify(sdk / "accounts.json") == "reject"
    finally:
        import shutil
        shutil.rmtree(sdk, ignore_errors=True)
    home_sdk = paths.Path.home() / ".wechat-agent-sdk"
    if not home_sdk.exists():
        home_sdk.mkdir()
        try:
            assert paths.classify(home_sdk / "accounts.json") == "reject"
        finally:
            home_sdk.rmdir()
    else:
        assert paths.classify(home_sdk / "accounts.json") == "reject"