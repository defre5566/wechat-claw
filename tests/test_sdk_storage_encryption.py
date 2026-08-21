"""SDK accounts.json 静态加密（F6.2 第三层）：密文落盘、回读解密、旧明文兼容、换钥/无钥兜底。"""
from __future__ import annotations

import asyncio
import os

import pytest

from wechat_agent_sdk.account.storage import JsonFileStorage


@pytest.fixture
def enc_env(tmp_path, monkeypatch):
    key = tmp_path / "crypto.key"
    key.write_bytes(os.urandom(32))
    monkeypatch.setenv("WECHAT_AGENT_SDK_KEY_FILE", str(key))
    sdk_dir = tmp_path / "sdk"
    sdk_dir.mkdir(parents=True, exist_ok=True)
    return {"key": key, "sdk_dir": sdk_dir}


def test_save_writes_ciphertext(enc_env):
    async def go():
        s = JsonFileStorage(state_dir=enc_env["sdk_dir"])
        await s.save_token("default", "tok-123")
        return (enc_env["sdk_dir"] / "accounts.json").read_text()

    raw = asyncio.run(go())
    assert raw.startswith("enc:v1:")
    assert "tok-123" not in raw


def test_roundtrip_decrypt(enc_env):
    async def go():
        await JsonFileStorage(state_dir=enc_env["sdk_dir"]).save_token("default", "tok-123")
        return await JsonFileStorage(state_dir=enc_env["sdk_dir"]).load_token("default")

    assert asyncio.run(go()) == "tok-123"


def test_old_plaintext_compat_and_auto_convert(enc_env):
    st = enc_env["sdk_dir"]
    async def go():
        (st / "accounts.json").write_text('{"default": {"token": "old-plain"}}')
        s = JsonFileStorage(state_dir=st)
        assert await s.load_token("default") == "old-plain"
        await s.save_cursor("default", "c1")
        return (st / "accounts.json").read_text()

    raw = asyncio.run(go())
    assert raw.startswith("enc:v1:")


def test_wrong_key_means_not_logged_in(enc_env):
    st = enc_env["sdk_dir"]
    async def go():
        await JsonFileStorage(state_dir=st).save_token("default", "tok-123")
        enc_env["key"].write_bytes(os.urandom(32))  # 换钥
        return await JsonFileStorage(state_dir=st).load_token("default")

    assert asyncio.run(go()) is None


def test_no_key_falls_back_to_plaintext(tmp_path, monkeypatch):
    monkeypatch.delenv("WECHAT_AGENT_SDK_KEY_FILE", raising=False)
    st = tmp_path / "sdk"
    async def go():
        s = JsonFileStorage(state_dir=st)
        await s.save_token("default", "tok-plain")
        return (st / "accounts.json").read_text()

    assert "tok-plain" in asyncio.run(go())