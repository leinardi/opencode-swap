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

import pytest

from opencode_swap.models import normalize_account_name


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("work", "work"),
        ("Work", "work"),
        ("  personal  ", "personal"),
        ("acct.v2", "acct.v2"),
        ("my_account-1", "my_account-1"),
        ("Account+Label@Example.Test", "account+label@example.test"),
    ],
)
def test_normalize_valid_names(raw, expected):
    assert normalize_account_name(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "-work", ".hidden", "..", "work/etc", "work:etc", "wörk", "a b"],
)
def test_normalize_rejects_invalid_names(raw):
    with pytest.raises(ValueError):
        normalize_account_name(raw)
