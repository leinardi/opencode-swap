import json
import traceback

import pytest

from opencode_swap import backup, transfer
from opencode_swap.exceptions import SchemaError
from opencode_swap.models import AccountMeta, Platform
from opencode_swap.switcher import Switcher
from tests.helpers import make_jwt


def openai_record(account_id: str) -> dict[str, object]:
    return {
        "type": "oauth",
        "refresh": f"refresh-{account_id}",
        "access": make_jwt({"chatgpt_account_id": account_id}),
        "expires": 4_000_000_000_000,
        "accountId": account_id,
    }


def write_auth(path, auth):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(auth))


def test_same_name_and_active_account_are_provider_scoped(tmp_path):
    switcher = Switcher(tmp_path / "auth.json", tmp_path / "data", platform=Platform.UNKNOWN)
    write_auth(
        switcher.opencode_auth_path,
        {"openai": openai_record("openai-work"), "anthropic": {"type": "api", "key": "anthropic-work"}},
    )
    switcher.add_account("work", provider_id="openai")
    switcher.add_account("work", provider_id="anthropic")

    write_auth(
        switcher.opencode_auth_path,
        {"openai": openai_record("openai-work"), "anthropic": {"type": "api", "key": "anthropic-personal"}},
    )
    switcher.add_account("personal", provider_id="anthropic")
    switcher.use_account("work", provider_id="anthropic")

    live = json.loads(switcher.opencode_auth_path.read_text())
    assert live["openai"] == openai_record("openai-work")
    assert live["anthropic"]["key"] == "anthropic-work"
    assert switcher.registry.get_active("openai") == "work"
    assert switcher.registry.get_active("anthropic") == "work"
    assert set(switcher.registry.scoped_accounts()) == {
        ("openai", "work"),
        ("anthropic", "work"),
        ("anthropic", "personal"),
    }


def test_restore_recomputes_each_provider_active_account(tmp_path):
    switcher = Switcher(tmp_path / "auth.json", tmp_path / "data", platform=Platform.UNKNOWN)
    original = {"openai": openai_record("openai-work"), "anthropic": {"type": "api", "key": "anthropic-work"}}
    write_auth(switcher.opencode_auth_path, original)
    switcher.add_account("work", provider_id="openai")
    switcher.add_account("work", provider_id="anthropic")
    backup.write_bak(switcher.data_root, original)
    write_auth(switcher.opencode_auth_path, {})

    restored = switcher.restore()

    assert {(meta.provider, meta.name) for meta in restored} == {("openai", "work"), ("anthropic", "work")}
    assert switcher.registry.get_active("openai") == "work"
    assert switcher.registry.get_active("anthropic") == "work"


def test_unclaimed_provider_id_cannot_escape_backup_directory(tmp_path):
    path = backup.write_unclaimed(tmp_path, "../../outside", {"type": "api", "key": "secret"})

    assert path.parent == tmp_path / "backups"
    assert "outside" not in path.name


def test_import_rejects_credential_embedded_in_provider_metadata(tmp_path):
    secret = "provider-shaped-secret"
    archive = tmp_path / "accounts.ocs"
    transfer.write_archive(
        archive,
        [
            transfer.TransferEntry(
                AccountMeta("work", secret, "api", None, None, "2026-01-01T00:00:00Z"),
                {"type": "api", "key": secret},
            )
        ],
        "password",
    )
    switcher = Switcher(tmp_path / "auth.json", tmp_path / "data", platform=Platform.UNKNOWN)

    with pytest.raises(SchemaError, match="credential data"):
        switcher.import_accounts(archive, "password")

    assert not (switcher.data_root / "registry.json").exists()


def test_import_validation_error_cannot_leak_another_entry_credential(tmp_path):
    secret = "cross-entry-secret"
    archive = tmp_path / "accounts.ocs"
    transfer.write_archive(
        archive,
        [
            transfer.TransferEntry(
                AccountMeta("first", "anthropic", "api", None, None, "2026-01-01T00:00:00Z"),
                {"type": "api", "key": secret},
            ),
            transfer.TransferEntry(
                AccountMeta("second", secret, "api", None, None, "2026-01-01T00:00:00Z"),
                {"type": "api"},
            ),
        ],
        "password",
    )
    switcher = Switcher(tmp_path / "auth.json", tmp_path / "data", platform=Platform.UNKNOWN)

    with pytest.raises(SchemaError) as exc_info:
        switcher.import_accounts(archive, "password")

    assert secret not in "".join(traceback.format_exception(exc_info.value))
