import json
import stat
import time

import pytest

from opencode_swap import cli, process_detection, transfer
from opencode_swap.exceptions import RegistryError
from opencode_swap.models import AccountMeta, Platform
from opencode_swap.usage import UsageSnapshot
from tests.helpers import make_jwt


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Route paths.py at a throwaway dir and force the file secret backend
    so CLI tests never touch the real OpenCode auth.json or the real OS
    keychain/keyring."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("OPENCODE_AUTH_CONTENT", raising=False)
    monkeypatch.setattr(Platform, "detect", classmethod(lambda cls: Platform.UNKNOWN))
    monkeypatch.setattr(process_detection, "is_opencode_running", lambda: False)
    return tmp_path


def auth_path(tmp_path):
    return tmp_path / "opencode" / "auth.json"


def write_live_account(tmp_path, account_id="acct-1", refresh="r1", extra=None):
    entry = {
        "type": "oauth",
        "refresh": refresh,
        "access": make_jwt({"chatgpt_account_id": account_id, "email": f"{account_id}@example.com"}),
        "expires": int((time.time() + 3600) * 1000),
        "accountId": account_id,
    }
    data = dict(extra or {})
    data["openai"] = entry
    path = auth_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    assert "opencode-swap" in capsys.readouterr().out


def test_no_args_prints_help_and_exits_zero(capsys):
    assert cli.main([]) == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0


def test_add_success(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    assert cli.main(["add", "openai", "work"]) == 0
    out = capsys.readouterr().out
    assert "Added 'openai:work'" in out
    assert "acct-1@example.com" in out


def test_add_no_live_account_fails_cleanly(capsys):
    assert cli.main(["add", "openai", "work"]) == 1
    assert "opencode-swap:" in capsys.readouterr().err


def test_add_never_prints_secrets(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1", refresh="super-secret-refresh-token")
    cli.main(["add", "openai", "work"])
    combined = "".join(capsys.readouterr())
    assert "super-secret-refresh-token" not in combined


def test_add_does_not_persist_or_print_secret_jwt_email(tmp_path, capsys):
    secret = "super-secret-refresh-token"
    write_live_account(tmp_path, account_id="acct-1", refresh=secret)
    auth = json.loads(auth_path(tmp_path).read_text())
    auth["openai"]["access"] = make_jwt({"chatgpt_account_id": "acct-1", "email": secret})
    auth_path(tmp_path).write_text(json.dumps(auth))

    assert cli.main(["add", "openai", "work"]) == 0

    assert secret not in "".join(capsys.readouterr())
    registry = (tmp_path / "opencode-swap" / "registry.json").read_text()
    assert secret not in registry


def test_list_empty(capsys):
    assert cli.main(["list"]) == 0
    assert "No accounts saved" in capsys.readouterr().out


def test_list_shows_active_marker(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()
    cli.main(["list"])
    out = capsys.readouterr().out
    assert "*" in out
    assert "work" in out


def test_list_does_not_mark_stale_registry_active_account(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    write_live_account(tmp_path, account_id="external")
    capsys.readouterr()

    assert cli.main(["list"]) == 0
    assert "*" not in capsys.readouterr().out


def test_list_without_usage_flag_never_touches_network(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    def boom(*a, **k):
        raise AssertionError("network should not be touched without --usage")

    monkeypatch.setattr("opencode_swap.usage.urllib.request.urlopen", boom)
    assert cli.main(["list"]) == 0


def test_list_usage_flag_shows_usage_line(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    monkeypatch.setattr(
        "opencode_swap.switcher.usage.fetch_openai_oauth_usage",
        lambda *a, **k: UsageSnapshot(available=True, used_percent=55, plan_name="ChatGPT Plus"),
    )
    cli.main(["list", "--usage"])
    out = capsys.readouterr().out
    assert "55%" in out
    assert "ChatGPT Plus" in out


def test_format_usage_shows_days_and_reset_time(monkeypatch):
    monkeypatch.setattr(cli.time, "time", lambda: 1_751_310_000)
    output = cli._format_usage(
        UsageSnapshot(
            available=True,
            used_percent=5,
            reset_at=(1_751_310_000 + 7 * 86_400) * 1000,
        )
    )

    assert output.startswith("  usage: 7d 5% @")
    assert ", " in output


def test_list_usage_flag_never_prints_secrets(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1", refresh="super-secret-refresh-token")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    monkeypatch.setattr(
        "opencode_swap.switcher.usage.fetch_openai_oauth_usage",
        lambda *a, **k: UsageSnapshot(available=True, used_percent=10),
    )
    cli.main(["list", "--usage"])
    assert "super-secret-refresh-token" not in capsys.readouterr().out


def test_list_never_prints_secrets(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1", refresh="super-secret-refresh-token")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()
    cli.main(["list"])
    assert "super-secret-refresh-token" not in capsys.readouterr().out


def test_current_none(capsys):
    assert cli.main(["current"]) == 0
    assert "No active" in capsys.readouterr().out


def test_current_unmanaged(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    assert cli.main(["current"]) == 0
    assert "doesn't manage" in capsys.readouterr().out


def test_current_managed(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()
    cli.main(["current"])
    assert "work" in capsys.readouterr().out


def test_current_explicit_incompatible_provider_fails(tmp_path, capsys):
    path = auth_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"openai": {"type": "oauth"}}))

    assert cli.main(["current", "openai"]) == 1
    assert "missing/invalid" in capsys.readouterr().err


def test_switcher_initialization_failure_is_reported_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(cli.Switcher, "default", lambda: (_ for _ in ()).throw(RegistryError("migration failed")))

    assert cli.main(["list"]) == 1
    captured = capsys.readouterr()
    assert "opencode-swap: migration failed" in captured.err
    assert "Traceback" not in captured.err


def test_use_switches_account(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    write_live_account(tmp_path, account_id="acct-2")
    cli.main(["add", "openai", "personal"])
    capsys.readouterr()

    assert cli.main(["use", "openai", "work", "--yes"]) == 0
    assert "Switched to 'openai:work'" in capsys.readouterr().out
    assert json.loads(auth_path(tmp_path).read_text())["openai"]["accountId"] == "acct-1"


def test_switch_cycles_accounts_and_wraps(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    write_live_account(tmp_path, account_id="acct-2")
    cli.main(["add", "openai", "personal"])
    capsys.readouterr()

    assert cli.main(["switch", "openai", "--yes"]) == 0
    assert "Switched to 'openai:work'" in capsys.readouterr().out
    assert json.loads(auth_path(tmp_path).read_text())["openai"]["accountId"] == "acct-1"

    assert cli.main(["switch", "openai", "--yes"]) == 0
    assert "Switched to 'openai:personal'" in capsys.readouterr().out
    assert json.loads(auth_path(tmp_path).read_text())["openai"]["accountId"] == "acct-2"


def test_switch_without_managed_active_account_fails_cleanly(capsys):
    assert cli.main(["switch", "openai"]) == 1
    assert "opencode-swap:" in capsys.readouterr().err


def test_use_unknown_account_fails_cleanly(capsys):
    assert cli.main(["use", "openai", "ghost", "--yes"]) == 1
    assert "opencode-swap:" in capsys.readouterr().err


def test_use_prompts_when_opencode_running_and_aborts_non_tty(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    monkeypatch.setattr(process_detection, "is_opencode_running", lambda: True)
    assert cli.main(["use", "openai", "work"]) == 1  # non-tty, no --yes -> refuses
    assert "Aborted" in capsys.readouterr().err


def test_use_requires_confirmation_when_opencode_is_not_running(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    assert cli.main(["use", "openai", "work"]) == 1
    assert "Aborted" in capsys.readouterr().err


def test_use_yes_flag_skips_confirmation_even_when_running(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    monkeypatch.setattr(process_detection, "is_opencode_running", lambda: True)
    assert cli.main(["use", "openai", "work", "--yes"]) == 0
    assert "Switched to 'openai:work'" in capsys.readouterr().out


def test_remove_requires_confirmation_non_tty(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    assert cli.main(["remove", "openai", "work"]) == 1
    assert "Aborted" in capsys.readouterr().err
    assert "work" in cli.Switcher.default().registry.accounts()


def test_remove_yes_flag(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    assert cli.main(["remove", "openai", "work", "--yes"]) == 0
    assert "Removed 'openai:work'" in capsys.readouterr().out
    assert cli.Switcher.default().registry.accounts() == {}


def test_rename(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    assert cli.main(["rename", "openai", "work", "personal"]) == 0
    assert "Renamed 'openai:work' to 'openai:personal'" in capsys.readouterr().out
    assert "personal" in cli.Switcher.default().registry.accounts()


def test_rename_unknown_fails_cleanly(capsys):
    assert cli.main(["rename", "openai", "ghost", "new"]) == 1
    assert "opencode-swap:" in capsys.readouterr().err


def test_export_import_roundtrip_with_hidden_password(tmp_path, monkeypatch, capsys):
    secret = "super-secret-refresh-token"
    write_live_account(tmp_path, account_id="acct-1", refresh=secret)
    assert cli.main(["add", "openai", "work"]) == 0
    archive = tmp_path / "accounts.ocs"
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    passwords = iter(["password", "password", "password"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(passwords))

    assert cli.main(["export", str(archive)]) == 0
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "mac"))
    assert cli.main(["import", str(archive)]) == 0

    combined = "".join(capsys.readouterr())
    assert secret not in combined
    assert secret.encode() not in archive.read_bytes()
    assert "work" in cli.Switcher.default().registry.accounts()


def test_export_offers_ocs_extension_for_extensionless_path(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    assert cli.main(["add", "openai", "work"]) == 0
    archive = tmp_path / "accounts"
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "password")
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    capsys.readouterr()

    assert cli.main(["export", str(archive)]) == 0

    assert archive.with_suffix(".ocs").exists()
    assert not archive.exists()
    assert str(archive.with_suffix(".ocs")) in capsys.readouterr().out


def test_export_keeps_extensionless_path_when_ocs_extension_declined(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    assert cli.main(["add", "openai", "work"]) == 0
    archive = tmp_path / "accounts"
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "password")
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    capsys.readouterr()

    assert cli.main(["export", str(archive)]) == 0

    assert archive.exists()
    assert not archive.with_suffix(".ocs").exists()


@pytest.mark.parametrize(
    ("choice", "expected_count", "expected_ids"),
    [
        ("sa", 0, {"a": "old-a", "b": "old-b"}),
        ("oa", 2, {"a": "new-a", "b": "new-b"}),
    ],
)
def test_import_conflict_apply_to_all(  # noqa: PLR0913
    choice, expected_count, expected_ids, tmp_path, monkeypatch, capsys
):
    for name in ("a", "b"):
        write_live_account(tmp_path, account_id=f"old-{name}")
        assert cli.main(["add", "openai", name]) == 0
    entries = [
        transfer.TransferEntry(
            AccountMeta(name, "openai", "oauth", f"new-{name}", None, "2026-01-01T00:00:00Z"),
            {
                "type": "oauth",
                "refresh": f"refresh-{name}",
                "access": make_jwt({"chatgpt_account_id": f"new-{name}"}),
                "expires": int((time.time() + 3600) * 1000),
                "accountId": f"new-{name}",
            },
        )
        for name in ("a", "b")
    ]
    archive = tmp_path / "accounts.ocs"
    transfer.write_archive(archive, entries, "password")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "password")
    prompts = []

    def choose(prompt):
        prompts.append(prompt)
        return choice

    monkeypatch.setattr("builtins.input", choose)
    capsys.readouterr()

    assert cli.main(["import", str(archive)]) == 0

    assert len(prompts) == 1
    assert "[s/sa/o/oa/a]" in prompts[0]
    assert f"Imported {expected_count} account" in capsys.readouterr().out
    accounts = cli.Switcher.default().registry.accounts()
    assert {name: accounts[name].account_id for name in ("a", "b")} == expected_ids


def test_export_refuses_noninteractive_password_prompt(tmp_path, capsys):
    write_live_account(tmp_path)
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    assert cli.main(["export", str(tmp_path / "accounts.ocs")]) == 1
    assert "interactive terminal" in capsys.readouterr().err


def test_import_list_never_prints_another_account_credential(tmp_path, monkeypatch, capsys):
    secret = "account-a-super-secret-refresh"
    record_a = {
        "type": "oauth",
        "refresh": secret,
        "access": make_jwt({"chatgpt_account_id": "acct-a"}),
        "expires": int((time.time() + 3600) * 1000),
        "accountId": "acct-a",
    }
    record_b = {
        "type": "oauth",
        "refresh": "refresh-b",
        "access": make_jwt({"chatgpt_account_id": "acct-b", "email": secret}),
        "expires": int((time.time() + 3600) * 1000),
        "accountId": "acct-b",
    }
    entries = [
        transfer.TransferEntry(AccountMeta("a", "openai", "oauth", "acct-a", None, "2026-01-01T00:00:00Z"), record_a),
        transfer.TransferEntry(AccountMeta("b", "openai", "oauth", "acct-b", None, "2026-01-01T00:00:00Z"), record_b),
    ]
    archive = tmp_path / "accounts.ocs"
    transfer.write_archive(archive, entries, "password")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "password")

    assert cli.main(["import", str(archive)]) == 0
    assert cli.main(["list"]) == 0

    assert secret not in "".join(capsys.readouterr())
    assert secret not in (tmp_path / "opencode-swap" / "registry.json").read_text()


def test_doctor_runs_clean_with_no_state(capsys):
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "OpenCode auth file:" in out
    assert "secret backend:" in out
    assert ".bak present: no" in out
    assert ".pristine present: no" in out


def test_doctor_reports_backups_present_after_a_switch(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    write_live_account(tmp_path, account_id="acct-2")
    cli.main(["add", "openai", "personal"])
    cli.main(["use", "openai", "work", "--yes"])
    capsys.readouterr()

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert ".bak present: yes" in out
    assert ".pristine present: yes" in out


def test_restore_requires_confirmation_non_tty(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    write_live_account(tmp_path, account_id="acct-2")
    cli.main(["add", "openai", "personal"])
    cli.main(["use", "openai", "work", "--yes"])
    capsys.readouterr()

    assert cli.main(["restore"]) == 1
    assert "Aborted" in capsys.readouterr().err


def test_restore_bak_with_yes(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    write_live_account(tmp_path, account_id="acct-2")
    cli.main(["add", "openai", "personal"])
    cli.main(["use", "openai", "work", "--yes"])
    cli.main(["use", "openai", "personal", "--yes"])
    capsys.readouterr()

    assert cli.main(["restore", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Restored" in out
    assert "work" in out
    assert json.loads(auth_path(tmp_path).read_text())["openai"]["accountId"] == "acct-1"


def test_restore_no_backup_fails_cleanly(capsys):
    assert cli.main(["restore", "--yes"]) == 1
    assert "opencode-swap:" in capsys.readouterr().err


def test_doctor_reports_auth_content_override(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENCODE_AUTH_CONTENT", "{}")
    cli.main(["doctor"])
    assert "OPENCODE_AUTH_CONTENT" in capsys.readouterr().out


def test_doctor_reports_incompatible_schema(tmp_path, capsys):
    path = auth_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"openai": {"type": "oauth"}}))  # missing required fields
    cli.main(["doctor"])
    assert "INCOMPATIBLE" in capsys.readouterr().out


def test_data_dir_created_with_safe_permissions(tmp_path):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    data_root = tmp_path / "opencode-swap"
    assert stat.S_IMODE(data_root.stat().st_mode) == 0o700
    assert stat.S_IMODE((data_root / "registry.json").stat().st_mode) == 0o600
