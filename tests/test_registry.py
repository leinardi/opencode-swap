import json
import stat

import pytest

from opencode_swap.exceptions import RegistryError
from opencode_swap.models import AccountMeta
from opencode_swap.store import Registry


def make_meta(name, **overrides):
    fields = {"name": name, "provider": "openai", "type": "oauth", "account_id": "acct-1", "email": None, "added": "2026-01-01T00:00:00Z"}
    fields.update(overrides)
    return AccountMeta(**fields)


def test_empty_registry_when_missing(tmp_path):
    reg = Registry(tmp_path / "registry.json")
    assert reg.accounts() == {}
    assert reg.get_active() is None


def test_upsert_and_read_back(tmp_path):
    reg = Registry(tmp_path / "registry.json")
    reg.upsert_account(make_meta("work"))
    accounts = reg.accounts()
    assert accounts["work"].account_id == "acct-1"
    assert stat.S_IMODE((tmp_path / "registry.json").stat().st_mode) == 0o600


def test_upsert_overwrites_existing(tmp_path):
    reg = Registry(tmp_path / "registry.json")
    reg.upsert_account(make_meta("work", account_id="acct-1"))
    reg.upsert_account(make_meta("work", account_id="acct-2"))
    assert reg.accounts()["work"].account_id == "acct-2"
    assert len(reg.accounts()) == 1


def test_remove_account_clears_active(tmp_path):
    reg = Registry(tmp_path / "registry.json")
    reg.upsert_account(make_meta("work"))
    reg.set_active("work")
    reg.remove_account("work")
    assert reg.accounts() == {}
    assert reg.get_active() is None


def test_rename_account_preserves_active(tmp_path):
    reg = Registry(tmp_path / "registry.json")
    reg.upsert_account(make_meta("work"))
    reg.set_active("work")
    reg.rename_account("work", "personal")
    assert "personal" in reg.accounts()
    assert "work" not in reg.accounts()
    assert reg.get_active() == "personal"


def test_rename_unknown_account_raises(tmp_path):
    reg = Registry(tmp_path / "registry.json")
    with pytest.raises(RegistryError):
        reg.rename_account("ghost", "new")


def test_rename_to_existing_name_raises(tmp_path):
    reg = Registry(tmp_path / "registry.json")
    reg.upsert_account(make_meta("work"))
    reg.upsert_account(make_meta("personal"))
    with pytest.raises(RegistryError):
        reg.rename_account("work", "personal")


def test_set_active_unknown_account_raises(tmp_path):
    reg = Registry(tmp_path / "registry.json")
    with pytest.raises(RegistryError):
        reg.set_active("ghost")


def test_set_active_none_clears(tmp_path):
    reg = Registry(tmp_path / "registry.json")
    reg.upsert_account(make_meta("work"))
    reg.set_active("work")
    reg.set_active(None)
    assert reg.get_active() is None


def test_corrupt_registry_raises(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text("{not json")
    reg = Registry(path)
    with pytest.raises(RegistryError):
        reg.accounts()


def test_registry_missing_accounts_key_raises(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text('{"version": 1}')
    reg = Registry(path)
    with pytest.raises(RegistryError):
        reg.accounts()


@pytest.mark.parametrize(
    "accounts",
    [
        {"work": {}},
        {"Work": {"provider": "openai"}},
        {"work": {"provider": "openai", "type": 1}},
        {"work": {"provider": "openai", "refresh": "secret"}},
        {"work": []},
    ],
)
def test_malformed_account_metadata_raises_registry_error(tmp_path, accounts):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"version": 1, "active": None, "accounts": accounts}))

    with pytest.raises(RegistryError):
        Registry(path).accounts()


@pytest.mark.parametrize(
    "registry",
    [
        {"version": 2, "active": None, "accounts": {}},
        {"version": True, "active": None, "accounts": {}},
        {"version": 1, "active": "missing", "accounts": {}},
        {"version": 1, "active": "Work", "accounts": {"Work": {"provider": "openai"}}},
        {"version": 1, "active": None, "accounts": {}, "refresh": "secret"},
    ],
)
def test_registry_rejects_unknown_top_level_schema(tmp_path, registry):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry))

    with pytest.raises(RegistryError):
        Registry(path).accounts()


def test_registry_metadata_allows_registered_future_providers(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"version": 1, "active": None, "accounts": {"work": {"provider": "future", "type": "oauth"}}}))

    assert Registry(path).accounts()["work"].provider == "future"


def test_registry_never_contains_secret_fields(tmp_path):
    reg = Registry(tmp_path / "registry.json")
    reg.upsert_account(make_meta("work", email="a@example.com"))
    raw_text = (tmp_path / "registry.json").read_text()
    assert "refresh" not in raw_text
    assert "access" not in raw_text
