import json
import stat
import time

import pytest

from opencode_swap import cli, process_detection
from opencode_swap.models import Platform
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
    assert cli.main(["add", "work"]) == 0
    out = capsys.readouterr().out
    assert "Added 'work'" in out
    assert "acct-1@example.com" in out


def test_add_no_live_account_fails_cleanly(capsys):
    assert cli.main(["add", "work"]) == 1
    assert "opencode-swap:" in capsys.readouterr().err


def test_add_never_prints_secrets(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1", refresh="super-secret-refresh-token")
    cli.main(["add", "work"])
    combined = "".join(capsys.readouterr())
    assert "super-secret-refresh-token" not in combined


def test_list_empty(capsys):
    assert cli.main(["list"]) == 0
    assert "No accounts saved" in capsys.readouterr().out


def test_list_shows_active_marker(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "work"])
    capsys.readouterr()
    cli.main(["list"])
    out = capsys.readouterr().out
    assert "*" in out
    assert "work" in out


def test_list_without_usage_flag_never_touches_network(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "work"])
    capsys.readouterr()

    def boom(*a, **k):
        raise AssertionError("network should not be touched without --usage")

    monkeypatch.setattr("opencode_swap.usage.urllib.request.urlopen", boom)
    assert cli.main(["list"]) == 0


def test_list_usage_flag_shows_usage_line(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "work"])
    capsys.readouterr()

    monkeypatch.setattr(
        "opencode_swap.switcher.usage.fetch_openai_oauth_usage",
        lambda *a, **k: UsageSnapshot(available=True, used_percent=55, plan_name="ChatGPT Plus"),
    )
    cli.main(["list", "--usage"])
    out = capsys.readouterr().out
    assert "55% used" in out
    assert "ChatGPT Plus" in out


def test_list_usage_flag_never_prints_secrets(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1", refresh="super-secret-refresh-token")
    cli.main(["add", "work"])
    capsys.readouterr()

    monkeypatch.setattr(
        "opencode_swap.switcher.usage.fetch_openai_oauth_usage",
        lambda *a, **k: UsageSnapshot(available=True, used_percent=10),
    )
    cli.main(["list", "--usage"])
    assert "super-secret-refresh-token" not in capsys.readouterr().out


def test_list_never_prints_secrets(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1", refresh="super-secret-refresh-token")
    cli.main(["add", "work"])
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
    cli.main(["add", "work"])
    capsys.readouterr()
    cli.main(["current"])
    assert "work" in capsys.readouterr().out


def test_use_switches_account(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "work"])
    write_live_account(tmp_path, account_id="acct-2")
    cli.main(["add", "personal"])
    capsys.readouterr()

    assert cli.main(["use", "work"]) == 0
    assert "Switched to 'work'" in capsys.readouterr().out
    assert json.loads(auth_path(tmp_path).read_text())["openai"]["accountId"] == "acct-1"


def test_use_unknown_account_fails_cleanly(capsys):
    assert cli.main(["use", "ghost"]) == 1
    assert "opencode-swap:" in capsys.readouterr().err


def test_use_prompts_when_opencode_running_and_aborts_non_tty(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "work"])
    capsys.readouterr()

    monkeypatch.setattr(process_detection, "is_opencode_running", lambda: True)
    assert cli.main(["use", "work"]) == 1  # non-tty, no --yes -> refuses
    assert "Aborted" in capsys.readouterr().err


def test_use_yes_flag_skips_confirmation_even_when_running(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "work"])
    capsys.readouterr()

    monkeypatch.setattr(process_detection, "is_opencode_running", lambda: True)
    assert cli.main(["use", "work", "--yes"]) == 0
    assert "Switched to 'work'" in capsys.readouterr().out


def test_remove_requires_confirmation_non_tty(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "work"])
    capsys.readouterr()

    assert cli.main(["remove", "work"]) == 1
    assert "Aborted" in capsys.readouterr().err
    assert "work" in cli.Switcher.default().registry.accounts()


def test_remove_yes_flag(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "work"])
    capsys.readouterr()

    assert cli.main(["remove", "work", "--yes"]) == 0
    assert "Removed 'work'" in capsys.readouterr().out
    assert cli.Switcher.default().registry.accounts() == {}


def test_rename(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "work"])
    capsys.readouterr()

    assert cli.main(["rename", "work", "personal"]) == 0
    assert "Renamed 'work' to 'personal'" in capsys.readouterr().out
    assert "personal" in cli.Switcher.default().registry.accounts()


def test_rename_unknown_fails_cleanly(capsys):
    assert cli.main(["rename", "ghost", "new"]) == 1
    assert "opencode-swap:" in capsys.readouterr().err


def test_doctor_runs_clean_with_no_state(capsys):
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "OpenCode auth file:" in out
    assert "secret backend:" in out
    assert ".bak present: no" in out
    assert ".pristine present: no" in out


def test_doctor_reports_backups_present_after_a_switch(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "work"])
    write_live_account(tmp_path, account_id="acct-2")
    cli.main(["add", "personal"])
    cli.main(["use", "work", "--yes"])
    capsys.readouterr()

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert ".bak present: yes" in out
    assert ".pristine present: yes" in out


def test_restore_requires_confirmation_non_tty(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "work"])
    write_live_account(tmp_path, account_id="acct-2")
    cli.main(["add", "personal"])
    cli.main(["use", "work", "--yes"])
    capsys.readouterr()

    assert cli.main(["restore"]) == 1
    assert "Aborted" in capsys.readouterr().err


def test_restore_bak_with_yes(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "work"])
    write_live_account(tmp_path, account_id="acct-2")
    cli.main(["add", "personal"])
    cli.main(["use", "work", "--yes"])
    cli.main(["use", "personal", "--yes"])
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
    cli.main(["add", "work"])
    data_root = tmp_path / "opencode-swap"
    assert stat.S_IMODE(data_root.stat().st_mode) == 0o700
    assert stat.S_IMODE((data_root / "registry.json").stat().st_mode) == 0o600
