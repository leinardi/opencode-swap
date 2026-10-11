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

from pathlib import Path

from opencode_swap import paths


def test_xdg_data_home_default(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("OPENCODE_TEST_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert paths.xdg_data_home() == tmp_path / ".local" / "share"


def test_xdg_data_home_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(custom))
    assert paths.xdg_data_home() == custom


def test_xdg_data_home_ignores_relative_env(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", "relative/path")
    monkeypatch.delenv("OPENCODE_TEST_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert paths.xdg_data_home() == tmp_path / ".local" / "share"


def test_opencode_test_home_overrides_home(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    fake_home = tmp_path / "fake-home"
    monkeypatch.setenv("OPENCODE_TEST_HOME", str(fake_home))
    assert paths.xdg_data_home() == fake_home / ".local" / "share"


def test_opencode_auth_path(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert paths.get_opencode_auth_path() == tmp_path / "opencode" / "auth.json"


def test_data_root_is_sibling_of_opencode_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert paths.get_data_root() == tmp_path / "opencode-swap"
    assert paths.get_opencode_data_dir() == tmp_path / "opencode"


def test_auth_content_override_detection(monkeypatch):
    monkeypatch.delenv("OPENCODE_AUTH_CONTENT", raising=False)
    assert paths.opencode_auth_content_override_active() is False
    monkeypatch.setenv("OPENCODE_AUTH_CONTENT", "{}")
    assert paths.opencode_auth_content_override_active() is True
