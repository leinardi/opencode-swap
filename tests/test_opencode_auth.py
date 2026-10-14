# MIT License
#
# Copyright (c) 2026 Roberto Leinardi
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

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
