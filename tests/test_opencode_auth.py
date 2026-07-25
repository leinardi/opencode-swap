import json
import stat

import pytest

from opencode_swap.exceptions import AuthFileError
from opencode_swap.opencode_auth import atomic_write_auth, read_auth


def test_read_auth_missing_file_raises(tmp_path):
    with pytest.raises(AuthFileError):
        read_auth(tmp_path / "auth.json")


def test_read_auth_malformed_json_raises(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text("{not json")
    with pytest.raises(AuthFileError):
        read_auth(path)


def test_read_auth_non_dict_top_level_raises(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text("[1, 2, 3]")
    with pytest.raises(AuthFileError):
        read_auth(path)


def test_read_auth_valid(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"openai": {"type": "api", "key": "sk-abc"}}))
    data = read_auth(path)
    assert data["openai"]["key"] == "sk-abc"


def test_atomic_write_roundtrip_and_permissions(tmp_path):
    path = tmp_path / "sub" / "auth.json"
    atomic_write_auth(path, {"openai": {"type": "api", "key": "sk-abc"}})
    assert read_auth(path) == {"openai": {"type": "api", "key": "sk-abc"}}
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_atomic_write_no_leftover_temp_files(tmp_path):
    path = tmp_path / "auth.json"
    atomic_write_auth(path, {"a": 1})
    leftovers = list(tmp_path.glob(".auth.*.tmp"))
    assert leftovers == []


def test_atomic_write_failure_leaves_original_untouched(tmp_path, monkeypatch):
    path = tmp_path / "auth.json"
    original = {"openai": {"type": "api", "key": "original"}}
    atomic_write_auth(path, original)

    def boom(*a, **k):
        raise RuntimeError("simulated crash mid-write")

    monkeypatch.setattr("json.dumps", boom)
    with pytest.raises(RuntimeError):
        atomic_write_auth(path, {"openai": {"type": "api", "key": "new"}})

    # original file untouched, no leftover temp file
    assert read_auth(path) == original
    assert list(tmp_path.glob(".auth.*.tmp")) == []
