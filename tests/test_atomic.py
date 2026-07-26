import errno
import json
import os
import stat

import pytest

from opencode_swap.atomic import (
    atomic_write_bytes,
    atomic_write_bytes_exclusive,
    atomic_write_json,
    atomic_write_json_exclusive,
)

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


def test_atomic_write_json_rejects_nonfinite_values(tmp_path):
    with pytest.raises(ValueError):
        atomic_write_json(tmp_path / "state.json", {"value": float("nan")})


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


def test_exclusive_write_keeps_published_backup_when_temp_cleanup_fails(tmp_path, monkeypatch):
    path = tmp_path / "backup.json"
    original_unlink = type(path).unlink

    def fail_temp_cleanup(candidate, *args, **kwargs):
        if candidate.name.startswith(".backup.json."):
            raise OSError("cleanup failed")
        return original_unlink(candidate, *args, **kwargs)

    monkeypatch.setattr(type(path), "unlink", fail_temp_cleanup)
    atomic_write_json_exclusive(path, {"credential": "preserved"})

    assert json.loads(path.read_text()) == {"credential": "preserved"}


def test_atomic_write_fsyncs_data_before_rename_and_dir_after(tmp_path, monkeypatch):
    path = tmp_path / "file.bin"
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def tracked_fsync(fd):
        events.append("fsync")
        return real_fsync(fd)

    def tracked_replace(src, dst):
        events.append("replace")
        return real_replace(src, dst)

    monkeypatch.setattr("os.fsync", tracked_fsync)
    monkeypatch.setattr("os.replace", tracked_replace)
    atomic_write_bytes(path, HELLO)

    # data fsync before the rename, directory fsync after it.
    assert events == ["fsync", "replace", "fsync"]


def test_atomic_write_data_fsync_failure_propagates(tmp_path, monkeypatch):
    path = tmp_path / "file.bin"
    calls = 0

    def boom(fd):
        nonlocal calls
        calls += 1
        raise OSError("disk error")

    monkeypatch.setattr("os.fsync", boom)
    with pytest.raises(OSError, match="disk error"):
        atomic_write_bytes(path, HELLO)
    assert calls == 1
    assert not path.exists()


def test_atomic_write_dir_fsync_failure_does_not_fail_write(tmp_path, monkeypatch):
    path = tmp_path / "file.bin"
    real_fsync = os.fsync
    calls = 0

    def flaky(fd):
        nonlocal calls
        calls += 1
        if calls == 1:  # the data fsync, inside the writer, must still succeed
            return real_fsync(fd)
        raise OSError("directory fsync unsupported")

    monkeypatch.setattr("os.fsync", flaky)
    atomic_write_bytes(path, HELLO)  # must not raise
    assert path.read_bytes() == HELLO
    assert calls == 2


def test_exclusive_write_falls_back_when_hardlinks_unsupported(tmp_path, monkeypatch):
    path = tmp_path / "export.bin"
    real_link = os.link

    def no_hardlinks(src, dst):
        raise OSError(errno.EPERM, "operation not permitted")

    monkeypatch.setattr("os.link", no_hardlinks)
    atomic_write_bytes_exclusive(path, HELLO, mode=0o600)
    assert path.read_bytes() == HELLO
    monkeypatch.setattr("os.link", real_link)


def test_exclusive_write_still_refuses_to_overwrite_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "export.bin"
    path.write_bytes(ORIGINAL)

    def unsupported(src, dst):
        raise OSError(errno.EPERM, "operation not permitted")

    monkeypatch.setattr("os.link", unsupported)
    with pytest.raises(FileExistsError):
        atomic_write_bytes_exclusive(path, HELLO, mode=0o600)
    assert path.read_bytes() == ORIGINAL


def test_exclusive_write_fallback_leaves_no_stub_when_publish_fails(tmp_path, monkeypatch):
    """A failure between reserving the destination name and publishing the
    already-fsynced content must not leave a truncated/empty file at `path`
    -- that would permanently block every retry with a false
    "already exists" error."""
    path = tmp_path / "export.bin"
    real_replace = os.replace

    def no_hardlinks(src, dst):
        raise OSError(errno.EPERM, "operation not permitted")

    def boom_replace(src, dst):
        raise OSError("simulated I/O error mid-publish")

    monkeypatch.setattr("os.link", no_hardlinks)
    monkeypatch.setattr("os.replace", boom_replace)
    with pytest.raises(OSError, match="simulated I/O error"):
        atomic_write_bytes_exclusive(path, HELLO, mode=0o600)

    assert not path.exists()
    assert list(tmp_path.glob(".*.tmp")) == []

    monkeypatch.setattr("os.replace", real_replace)
    atomic_write_bytes_exclusive(path, HELLO, mode=0o600)  # retry must succeed, not "already exists"
    assert path.read_bytes() == HELLO
