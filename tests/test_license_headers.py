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

"""Tests for scripts/check-license-headers.sh, run in a scratch repository.

The check must pass on a tree whose governed files carry the MIT header, and fail, naming the
file, on each way a header can be missing or wrong; make-common's fetched files and non-source
files are never governed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check-license-headers.sh"
# The header exactly as this repository's own files carry it: the hash-comment block of the
# checker itself, and the block-comment form of a TypeScript source.
HASH_HEADER = "".join(SCRIPT.read_text().splitlines(keepends=True)[2:23])
BLOCK_HEADER = "".join((ROOT / "integrations/opencode-tui-plugin/src/tui.tsx").read_text().splitlines(keepends=True)[:23])


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=_clean_env())


def _clean_env() -> dict[str, str]:
    # A git hook exports GIT_DIR, GIT_INDEX_FILE and friends; the scratch repository must not
    # inherit them, or `git add` would write into the real index.
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None or shutil.which("bash") is None:
        pytest.fail("git and bash are required to test the licence header check")
    r = tmp_path / "repo"
    (r / "scripts").mkdir(parents=True)
    (r / ".mk").mkdir()
    (r / "src").mkdir()
    _git(tmp_path, "init", "-q", str(r))
    shutil.copy(SCRIPT, r / "scripts" / "check-license-headers.sh")
    (r / "Makefile").write_text(HASH_HEADER + "\nMK_COMMON_FILES := help.mk\n")
    (r / ".mk" / "help.mk").write_text("ifndef X\nendif\n")
    (r / ".mk" / "local.mk").write_text(HASH_HEADER.replace("2026", "2025-2026") + "\nifndef Y\nendif\n")
    (r / "scripts" / "bootstrap-mk-common.sh").write_text("#!/usr/bin/env bash\n")
    (r / "src" / "mod.py").write_text(HASH_HEADER + '\n"""Docstring."""\n')
    (r / "src" / "ui.tsx").write_text(BLOCK_HEADER + "\nexport {};\n")
    (r / "config.yaml").write_text("key: value\n")
    (r / "README.md").write_text("# Title\n")
    _git(r, "add", "-A")
    return r


def _run(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/check-license-headers.sh"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_clean_env(),
        check=False,
    )


def _add(repo: Path, path: str, content: str) -> None:
    (repo / path).write_text(content)
    _git(repo, "add", "--", path)


def test_a_tree_with_every_header_passes(repo: Path) -> None:
    result = _run(repo)
    # The checker itself, Makefile, .mk/local.mk, src/mod.py and src/ui.tsx; the make-common
    # files, YAML and Markdown are not governed.
    assert result.returncode == 0, result.stderr
    assert "5 files checked" in result.stdout


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("src/bare.py", '"""No header."""\n'),
        ("scripts/other.sh", "#!/usr/bin/env bash\n" + HASH_HEADER.replace("Roberto Leinardi", "Someone Else")),
        ("src/gpl.ts", "/*\n * Copyright (c) 2026 Roberto Leinardi\n *\n * This program is free software\n */\n"),
        ("src/late.py", "#\n" * 30 + HASH_HEADER),
        ("src/noyear.py", HASH_HEADER.replace("Copyright (c) 2026", "Copyright (c)")),
    ],
    ids=["missing", "other-holder", "other-licence", "below-the-window", "no-year"],
)
def test_a_wrong_header_fails_and_names_the_file(repo: Path, path: str, content: str) -> None:
    _add(repo, path, content)
    result = _run(repo)
    assert result.returncode == 1
    assert f"  {path}" in result.stderr.splitlines()


def test_a_shebang_may_precede_the_header(repo: Path) -> None:
    _add(repo, "scripts/tool.sh", "#!/usr/bin/env bash\n#\n" + HASH_HEADER + "#\n# What it does.\n")
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_a_fetched_snippet_stops_being_exempt_once_it_is_local(repo: Path) -> None:
    (repo / "Makefile").write_text(HASH_HEADER + "\nMK_COMMON_FILES := other.mk\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "  .mk/help.mk" in result.stderr.splitlines()


def test_an_untracked_file_is_not_checked(repo: Path) -> None:
    (repo / "src" / "untracked.py").write_text("x = 1\n")
    result = _run(repo)
    assert result.returncode == 0, result.stderr
