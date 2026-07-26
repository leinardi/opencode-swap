import stat
import zipfile

import pytest

from opencode_swap import transfer
from opencode_swap.exceptions import TransferError
from opencode_swap.models import AccountMeta


def entry(secret="super-secret-refresh"):
    return transfer.TransferEntry(
        meta=AccountMeta(
            name="work",
            provider="openai",
            type="oauth",
            account_id="acct-1",
            email=None,
            added="2026-01-01T00:00:00Z",
        ),
        record={"type": "oauth", "refresh": secret, "access": "token", "expires": 1, "accountId": "acct-1"},
    )


def test_encrypted_archive_roundtrip_without_plaintext(tmp_path):
    path = tmp_path / "accounts.ocs"
    secret = "super-secret-refresh"

    transfer.write_archive(path, [entry(secret)], "correct horse battery staple")

    assert secret.encode() not in path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    restored = transfer.read_archive(path, "correct horse battery staple")
    assert restored == [entry(secret)]


def test_wrong_password_fails_without_leaking_password(tmp_path):
    path = tmp_path / "accounts.ocs"
    transfer.write_archive(path, [entry()], "right-password")

    with pytest.raises(TransferError) as exc_info:
        transfer.read_archive(path, "wrong-password")

    assert "wrong-password" not in str(exc_info.value)
    assert "super-secret-refresh" not in str(exc_info.value)


def test_plain_zip_is_rejected(tmp_path):
    path = tmp_path / "accounts.ocs"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(transfer.MEMBER_NAME, b"{}")

    with pytest.raises(TransferError, match="not AES-256 encrypted"):
        transfer.read_archive(path, "password")


def test_export_refuses_to_overwrite(tmp_path):
    path = tmp_path / "accounts.ocs"
    transfer.write_archive(path, [entry()], "password")
    original = path.read_bytes()

    with pytest.raises(TransferError, match="refusing to overwrite"):
        transfer.write_archive(path, [entry("new-secret")], "password")

    assert path.read_bytes() == original
