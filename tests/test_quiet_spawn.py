"""opencode 静默拉起 + build zip 解析加固测试。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge.session import _quiet_asyncio_exec
from scripts.build import _pick_exe_from_zip


def test_quiet_exec_injects_creationflags_windows(monkeypatch):
    """Windows：作用域内 create_subprocess_exec 被注入 creationflags，出作用域还原。"""
    import bridge.config as bc
    monkeypatch.setattr(bc, "_os", type("S", (), {"name": "nt"}))

    seen = {}

    async def fake_exec(*args, **kwargs):
        seen.update(kwargs)
        return "proc"

    orig = asyncio.create_subprocess_exec
    asyncio.create_subprocess_exec = fake_exec
    try:
        async def run():
            with _quiet_asyncio_exec():
                return await asyncio.create_subprocess_exec("opencode", "acp")
        assert asyncio.run(run()) == "proc"
        assert seen.get("creationflags") == 0x08000000
    finally:
        asyncio.create_subprocess_exec = orig

    # 出作用域后还原：不再注入
    captured = {}

    async def fake2(*a, **kw):
        captured.update(kw)
        return None

    asyncio.create_subprocess_exec = fake2
    try:
        asyncio.run(asyncio.create_subprocess_exec("x"))
        assert "creationflags" not in captured
    finally:
        asyncio.create_subprocess_exec = orig


def test_quiet_exec_noop_posix(monkeypatch):
    """POSIX：no_window_flags()=0 时注入为 no-op（不包装）。"""
    import bridge.config as bc
    monkeypatch.setattr(bc, "_os", type("S", (), {"name": "posix"}))
    orig = asyncio.create_subprocess_exec
    with _quiet_asyncio_exec():
        assert asyncio.create_subprocess_exec is orig  # 未包装
    assert asyncio.create_subprocess_exec is orig


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


PE_HEAD = b"MZ" + b"\x00" * (10 * 1024 * 1024)  # PE 头 + 补足 10MB


def test_pick_exe_exact_match_only():
    """精确 basename 匹配：opencode.exe.sha256 / README 不命中，只取真 exe。"""
    data = _zip_bytes({
        "opencode.exe.sha256": b"abc123",
        "README.md": b"hi",
        "dist/opencode.exe": PE_HEAD,
    })
    assert _pick_exe_from_zip(data) == PE_HEAD


def test_pick_exe_rejects_small_or_non_pe():
    """非 PE 头 / 小于 10MB 的条目被拒；全不合格返回 None。"""
    assert _pick_exe_from_zip(_zip_bytes({"opencode.exe": b"MZ" + b"x" * 1024})) is None
    assert _pick_exe_from_zip(_zip_bytes({"opencode.exe": b"PK" + b"\x00" * (11 * 1024 * 1024)})) is None
    assert _pick_exe_from_zip(_zip_bytes({"other.exe": PE_HEAD})) is None
