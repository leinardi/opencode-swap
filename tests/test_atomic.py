import json
import stat

import pytest

from opencode_swap.atomic import atomic_write_bytes, atomic_write_json

HELLO = b"hello"
ORIGINAL = b"original"


def test_atomic_write_bytes_roundtrip_and_perms(tmp_path):
    path = tmp_path / "nested" / "file.bin"
    atomic_write_bytes(path, HELLO, mode=0o600)
    assert path.read_bytes() == HELLO
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_atomic_write_json_roundtrip(tmp_path):
    path = tmp_path / "data.json"
    atomic_write_json(path, {"a": 1, "b": [1, 2, 3]})
    assert json.loads(path.read_text()) == {"a": 1, "b": [1, 2, 3]}


def test_atomic_write_failure_leaves_no_temp_file(tmp_path, monkeypatch):
    path = tmp_path / "file.bin"

    def boom(fd, *a, **k):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr("os.fdopen", boom)
    with pytest.raises(RuntimeError):
        atomic_write_bytes(path, b"data")
    assert not path.exists()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_atomic_write_failure_leaves_original_untouched(tmp_path, monkeypatch):
    path = tmp_path / "file.bin"
    atomic_write_bytes(path, ORIGINAL)

    monkeypatch.setattr("os.replace", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        atomic_write_bytes(path, b"new")

    assert path.read_bytes() == ORIGINAL
    assert list(tmp_path.glob(".*.tmp")) == []
