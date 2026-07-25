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
